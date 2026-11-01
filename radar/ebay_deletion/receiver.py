"""Run ONLY this app on the public receiver; never radar.main:app."""

import asyncio
import inspect
import os
import re
import sqlite3
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from radar.ebay_deletion.protocol import (
    MAX_BODY,
    InvalidNotification,
    VerificationUnavailable,
    challenge,
    event,
    signature_header,
    verify,
)
from radar.ebay_deletion.store import Conflict, Queue


class PublicKeys:
    """Fixed official endpoint, one-hour bounded cache; token supplied by operator."""

    def __init__(self, token_provider):
        self.token_provider = token_provider
        self.cache = {}
        self.lock = asyncio.Lock()

    async def __call__(self, kid):
        async with self.lock:
            now = time.monotonic()
            if kid in self.cache and self.cache[kid][0] > now:
                return self.cache[kid][1]
            token = self.token_provider()
            if inspect.isawaitable(token):
                token = await token
            if not token:
                raise VerificationUnavailable("Notification API token unavailable")
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    async with client.stream(
                        "GET",
                        "https://api.ebay.com/commerce/notification/v1/public_key/" + kid,
                        headers={"Authorization": "Bearer " + token},
                    ) as response:
                        if response.status_code != 200:
                            raise VerificationUnavailable("Notification key retrieval unavailable")
                        body = b""
                        async for part in response.aiter_bytes():
                            body += part
                            if len(body) > 16384:
                                raise VerificationUnavailable("Notification key response invalid")
                        from radar.ebay_deletion.protocol import object_json

                        key = object_json(body)
            except (httpx.HTTPError, InvalidNotification):
                raise VerificationUnavailable("Notification key retrieval unavailable") from None
            if len(self.cache) >= 32:
                self.cache.clear()
            self.cache[kid] = (now + 3600, key)
            return key


def create_receiver(*, endpoint, token, secret, queue, keys):
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path != "/ebay/account-deletion"
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", token)
        or len(secret) < 32
    ):
        raise ValueError("Invalid deletion receiver configuration")
    queue.bind(secret)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ebay/account-deletion")
    async def endpoint_challenge(request: Request):
        codes = request.query_params.getlist("challenge_code")
        try:
            if len(codes) != 1 or len(request.query_params) != 1:
                raise InvalidNotification()
            return {"challengeResponse": challenge(codes[0], token, endpoint)}
        except InvalidNotification:
            return JSONResponse({"error": "Invalid challenge"}, status_code=400)

    @app.post("/ebay/account-deletion")
    async def receive(request: Request):
        body = b""
        try:
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return Response(status_code=415)
            if request.headers.get("content-encoding", "identity") != "identity":
                return Response(status_code=415)
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    body += chunk
                    if len(body) > MAX_BODY:
                        return Response(status_code=413)
            kid, signature = signature_header(request.headers.get("x-ebay-signature"))
            # Validate bounded schema before spending a key lookup; trust follows signature only.
            event_id, identifiers = event(body, secret)
            key = await keys(kid)
            await asyncio.to_thread(verify, body, signature, key)
            await asyncio.to_thread(queue.accept, event_id, identifiers)
            return Response(status_code=204)
        except (InvalidNotification, Conflict):
            return JSONResponse({"error": "Invalid notification"}, status_code=412)
        except (VerificationUnavailable, sqlite3.Error, OSError, TimeoutError):
            return JSONResponse({"error": "Temporarily unavailable"}, status_code=503)

    return app


class ApplicationToken:
    """Memory-only Notification API application token; no Browse or purchasing calls."""

    def __init__(self, client_id, client_secret):
        self.client_id, self.client_secret = client_id, client_secret
        self.token, self.expiry = "", 0

    async def __call__(self):
        if self.token and time.monotonic() < self.expiry:
            return self.token
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                async with client.stream(
                    "POST",
                    "https://api.ebay.com/identity/v1/oauth2/token",
                    auth=httpx.BasicAuth(self.client_id, self.client_secret),
                    data={
                        "grant_type": "client_credentials",
                        "scope": "https://api.ebay.com/oauth/api_scope",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise VerificationUnavailable("Notification OAuth unavailable")
                    body = b""
                    async for part in response.aiter_bytes():
                        body += part
                        if len(body) > 16384:
                            raise VerificationUnavailable("Notification OAuth invalid")
                    from radar.ebay_deletion.protocol import object_json

                    data = object_json(body)
            token, expires = data.get("access_token"), data.get("expires_in")
            if (
                not isinstance(token, str)
                or not token.strip()
                or type(expires) is not int
                or expires <= 0
                or data.get("token_type", "Bearer") not in ("Bearer", "Application Access Token")
            ):
                raise VerificationUnavailable("Notification OAuth invalid")
            self.token, self.expiry = token, time.monotonic() + max(0, expires - 30)
            return self.token
        except (httpx.HTTPError, InvalidNotification):
            raise VerificationUnavailable("Notification OAuth unavailable") from None


def from_env():
    # Loaded only by this explicit factory, never by workbench startup.
    return create_receiver(
        endpoint=os.environ["EBAY_DELETION_ENDPOINT"],
        token=os.environ["EBAY_DELETION_VERIFICATION_TOKEN"],
        secret=os.environ["EBAY_DELETION_HMAC_KEY"].encode(),
        queue=Queue(os.environ["EBAY_DELETION_QUEUE"]),
        keys=PublicKeys(
            ApplicationToken(
                os.environ["EBAY_NOTIFICATION_CLIENT_ID"],
                os.environ["EBAY_NOTIFICATION_CLIENT_SECRET"],
            )
        ),
    )
