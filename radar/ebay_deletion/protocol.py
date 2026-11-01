"""Bounded verification equivalent to eBay's official Node notification SDK."""

import base64
import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path

TOPIC = "MARKETPLACE_ACCOUNT_DELETION"
MAX_BODY = 32768


class InvalidNotification(ValueError):
    pass


class VerificationUnavailable(RuntimeError):
    pass


def digest(secret, kind, value):
    return hmac.new(secret, (kind + "\0" + value).encode(), hashlib.sha256).hexdigest()


def challenge(code, token, endpoint):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", code):
        raise InvalidNotification("Invalid challenge")
    return hashlib.sha256((code + token + endpoint).encode()).hexdigest()


def object_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("Duplicate JSON key")
            result[k] = v
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: None)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidNotification("Invalid notification") from None


def signature_header(value):
    try:
        if not value or len(value) > 4096:
            raise ValueError()
        header = object_json(base64.b64decode(value, validate=True))
        kid, sig = header["kid"], header["signature"]
        if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", kid):
            raise ValueError()
        if not isinstance(sig, str) or not 8 <= len(base64.b64decode(sig, validate=True)) <= 256:
            raise ValueError()
        return kid, sig
    except (ValueError, KeyError, TypeError):
        raise InvalidNotification("Invalid signature") from None


def verify(body, signature, public_key):
    if public_key.get("algorithm") != "ECDSA" or public_key.get("digest") != "SHA1":
        raise InvalidNotification("Unsupported signature algorithm")
    key = public_key.get("key")
    if not isinstance(key, str) or len(key) > 8192:
        raise InvalidNotification("Invalid public key")
    # The official SDK accepts PEM with or without line breaks after the labels.
    key = (
        "-----BEGIN PUBLIC KEY-----\n"
        + "".join(
            key.replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .split()
        )
        + "\n-----END PUBLIC KEY-----\n"
    )
    try:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("verify.cjs"))],
            input=json.dumps({"body": body.decode("utf-8"), "signature": signature, "key": key}),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationUnavailable("Signature verifier unavailable") from None
    if result.returncode:
        raise InvalidNotification("Invalid signature")


def event(body, secret):
    data = object_json(body)
    try:
        meta, notice = data["metadata"], data["notification"]
        if meta["topic"] != TOPIC or meta["schemaVersion"] != "1.0":
            raise ValueError()
        identity = notice["notificationId"]
        if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
            raise ValueError()
        account = notice["data"]
        identifiers = {}
        for field in ("userId", "eiasToken", "username"):
            value = account.get(field)
            if value is not None:
                if not isinstance(value, str) or not 1 <= len(value) <= 2048:
                    raise ValueError()
                identifiers[field] = digest(secret, field, value)
        if not identifiers:
            raise ValueError()
        return digest(secret, "notification", identity), identifiers
    except (KeyError, TypeError, ValueError):
        raise InvalidNotification("Unsupported deletion event") from None
