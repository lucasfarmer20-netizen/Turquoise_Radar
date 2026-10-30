"""Allowlisted failure metadata only; never stringify SDK errors or retain bodies."""

import asyncio
import re
from contextlib import aclosing

import anthropic
import httpx2
from pydantic import ValidationError

from radar.jewelry.validation import validation_diagnostics

# Far above the bounded VisionResult schema and current output-token limits.
# Enforce on decoded bytes, not an untrusted/compressed Content-Length alone.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    def __init__(self):
        super().__init__("Provider response exceeds the local size limit")


async def read_bounded_response(response):
    try:
        advertised = int(response.headers.get("content-length", ""))
    except ValueError:
        advertised = None
    if advertised is not None and advertised > MAX_RESPONSE_BYTES:
        await response.aclose()
        raise ResponseTooLargeError()
    try:
        buffered = response.content
    except httpx2.ResponseNotRead:
        buffered = None
    if buffered is not None:
        if len(buffered) > MAX_RESPONSE_BYTES:
            await response.aclose()
            raise ResponseTooLargeError()
        return
    chunks = bytearray()
    try:
        async with aclosing(response.aiter_bytes(chunk_size=64 * 1024)) as parts:
            async for part in parts:
                if len(chunks) + len(part) > MAX_RESPONSE_BYTES:
                    raise ResponseTooLargeError()
                chunks.extend(part)
    finally:
        await response.aclose()
    # The pinned HTTPX2 aread() uses this cache. Preserve the same decoded response
    # for the SDK without resetting its consumed stream or decoding gzip twice.
    # Assignment occurs only after the complete body passes the byte limit.
    response._content = bytes(chunks)


ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "not_found_error",
        "rate_limit_error",
        "timeout_error",
        "overloaded_error",
        "api_error",
        "billing_error",
        "spend_limit_error",
        "credit_balance_error",
    }
)
STAGES = frozenset(
    {
        "LOCAL_CONFIGURATION",
        "REQUEST_PREPARATION",
        "TRANSPORT",
        "HTTP_RESPONSE",
        "RESPONSE_PARSING",
        "POST_RESPONSE_VALIDATION",
        "RESULT_PERSISTENCE",
        "CLIENT_CLEANUP",
        "PROVIDER_EXECUTION",
    }
)
EXCEPTION_CLASSES = frozenset(
    {
        "AnthropicError",
        "APIError",
        "APIStatusError",
        "APIResponseValidationError",
        "APIConnectionError",
        "APITimeoutError",
        "CredentialsError",
        "IdentityTokenFileError",
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "ConflictError",
        "RequestTooLargeError",
        "UnprocessableEntityError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "OverloadedError",
        "DeadlineExceededError",
        "RetryableError",
        "ValidationError",
        "PydanticCustomError",
        "ResponseTooLargeError",
        "JSONDecodeError",
        "ValueError",
        "TypeError",
        "AttributeError",
        "RuntimeError",
        "ImportError",
        "ModuleNotFoundError",
        "OSError",
        "TimeoutError",
        "CancelledError",
        "KeyboardInterrupt",
        "SystemExit",
        "ConnectError",
        "ReadError",
        "WriteError",
        "ProxyError",
        "RemoteProtocolError",
        "LocalProtocolError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "UnsupportedProtocol",
    }
)


def safe_request_id(value, secret=""):
    if (
        isinstance(value, str)
        and re.fullmatch(r"req_[A-Za-z0-9_-]{1,120}", value)
        and not (secret and secret in value)
    ):
        return value
    return None


def classify_failure(error, *, stage="PROVIDER_EXECUTION", status=None, request_id=None, secret=""):
    """HTTP status identifies a response, not whether Anthropic or a proxy generated it.

    Unknown provider types and request IDs are discarded. Billing classification requires
    status 402 or a typed billing/spend signal, never guesses from arbitrary message text.
    """
    stage = stage if stage in STAGES else "PROVIDER_EXECUTION"
    # The SDK wraps exceptions raised by HTTP response hooks as connection errors.
    # Unwrap only our fixed local size error; do not inspect arbitrary error text.
    if isinstance(error, anthropic.APIConnectionError) and isinstance(
        error.__cause__, ResponseTooLargeError
    ):
        error = error.__cause__
    provider_type = None
    if isinstance(error, (anthropic.APIStatusError, anthropic.APIResponseValidationError)):
        status = error.status_code
        request_id = error.response.headers.get("request-id")
        candidate = getattr(error, "type", None)
        provider_type = (
            candidate if isinstance(candidate, str) and candidate in ERROR_TYPES else None
        )
        stage = (
            "RESPONSE_PARSING"
            if isinstance(error, anthropic.APIResponseValidationError)
            else "HTTP_RESPONSE"
        )
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    if isinstance(error, ResponseTooLargeError):
        category = "RESPONSE_TOO_LARGE"
    elif isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        category = "INTERRUPTED"
    elif isinstance(error, (anthropic.APITimeoutError, httpx2.TimeoutException, TimeoutError)) or (
        status in {408, 504}
    ):
        category = "TIMEOUT"
    elif isinstance(error, anthropic.AuthenticationError) or status == 401:
        category = "AUTHENTICATION_ERROR"
    elif isinstance(error, anthropic.PermissionDeniedError) or status == 403:
        category = "PERMISSION_ERROR"
    elif status == 402 or provider_type in {
        "billing_error",
        "spend_limit_error",
        "credit_balance_error",
    }:
        category = "BILLING_OR_SPEND_LIMIT"
    elif isinstance(error, anthropic.RateLimitError) or status == 429:
        category = "RATE_LIMIT"
    elif isinstance(error, anthropic.BadRequestError) or status in {400, 413, 422}:
        category = "INVALID_REQUEST"
    elif isinstance(error, (anthropic.APIConnectionError, httpx2.TransportError)):
        category = "TRANSPORT_FAILURE"
    elif isinstance(error, (anthropic.APIResponseValidationError, ValidationError)) or (
        isinstance(error, (ValueError, TypeError, AttributeError))
        and stage in {"RESPONSE_PARSING", "POST_RESPONSE_VALIDATION"}
    ):
        category = "INVALID_RESULT"
    elif (
        stage == "LOCAL_CONFIGURATION"
        or isinstance(error, anthropic.CredentialsError)
        or (
            isinstance(error, (TypeError, ValueError, ImportError))
            and stage == "REQUEST_PREPARATION"
        )
    ):
        category = "LOCAL_CONFIGURATION_ERROR"
    else:
        category = "PROVIDER_FAILURE"
    if isinstance(error, (anthropic.APIConnectionError, httpx2.TransportError)):
        stage = "TRANSPORT"
    name = type(error).__name__
    diagnostics = {
        "exception_class": name if name in EXCEPTION_CLASSES else "UnknownException",
        "http_status": status,
        "provider_error_type": provider_type,
        "provider_request_id": safe_request_id(request_id, secret),
        "failure_stage": stage,
        "category": category,
    }
    validation_error = error
    if isinstance(error, anthropic.APIResponseValidationError) and isinstance(
        error.__cause__, ValidationError
    ):
        validation_error = error.__cause__
    details = validation_diagnostics(validation_error)
    if details:
        diagnostics["validation_errors"] = details
    return diagnostics


STOP_REASONS = frozenset(
    {
        "end_turn",
        "max_tokens",
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
        "model_context_window_exceeded",
    }
)


def numeric_usage(raw):
    """Usage is untrusted metadata, not permission to release a failed reservation."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 2**53 - 1:
            result[key] = value
    for key, fields in {
        "cache_creation": ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"),
        "output_tokens_details": ("thinking_tokens",),
        "server_tool_use": ("web_search_requests", "web_fetch_requests"),
    }.items():
        nested = raw.get(key)
        if isinstance(nested, dict):
            result[key] = {
                name: nested[name]
                for name in fields
                if type(nested.get(name)) is int and 0 <= nested[name] <= 2**53 - 1
            }
    return result


class FailureTrace:
    """Hooks retain only boundary metadata; successful envelopes are read in memory."""

    def __init__(self, secret):
        self.secret = secret
        self.stage = "LOCAL_CONFIGURATION"
        self.status = None
        self.request_id = None
        self.response_metadata = {}

    async def request(self, request):
        # Handoff to transport is not proof of remote receipt or billing.
        self.stage = "TRANSPORT"

    async def response(self, response):
        self.status = response.status_code
        self.request_id = safe_request_id(response.headers.get("request-id"), self.secret)
        self.stage = "RESPONSE_PARSING" if response.is_success else "HTTP_RESPONSE"
        self.response_metadata = {
            "http_status": self.status,
            "request_id": self.request_id,
            "usage": {},
            "stop_reason": None,
        }
        # Limit error bodies too: otherwise the SDK would buffer them without a cap.
        # HTTPX hooks run before SDK post-parsers; preserve the SAME response.
        # The body is inspected in memory only; no body/text/content is retained here.
        await read_bounded_response(response)
        if not response.is_success:
            return
        try:
            envelope = response.json()
        except (ValueError, UnicodeError):
            return  # Let the SDK reject malformed JSON through its normal path.
        if isinstance(envelope, dict):
            self.response_metadata["usage"] = numeric_usage(envelope.get("usage"))
            reason = envelope.get("stop_reason")
            if isinstance(reason, str) and reason in STOP_REASONS:
                self.response_metadata["stop_reason"] = reason

    def classify(self, error):
        return classify_failure(
            error,
            stage=self.stage,
            status=self.status,
            request_id=self.request_id,
            secret=self.secret,
        )
