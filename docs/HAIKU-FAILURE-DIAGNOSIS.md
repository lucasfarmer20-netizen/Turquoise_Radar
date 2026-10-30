# Task 005 Haiku failure diagnosis — October 30, 2026

The first owner-authorized attempt failed with the old `PROVIDER_FAILURE` code.
**A definite root cause was not established. No additional live Anthropic request
was made during this diagnosis, including Models, Messages or token counting.**
The original run, private photo, approval record and financial evidence are retained.
The $0.505120 reservation remains `HELD_UNKNOWN`; the one-request guard is consumed.
General settings remain fixture/live-disabled, the budget remains $5/month and
10 attempts/day, and Sonnet remains disabled. This is a Task 005 follow-up only.

## What the original evidence establishes

The private outcome JSON agrees with the database: one failed Anthropic QUICK run,
model `claude-haiku-5-5`, no usage, result, request ID, HTTP status or exception class.
It started at **10:38:21 AKDT** and completed at **10:38:22 AKDT** on October 30
(database times 18:38:21.138070–18:38:22.790607 UTC). Elapsed time is not evidence
of a particular error or billing outcome. Current cloud diagnostics have no retained
network failure record for that request.

| Boundary | Conclusion from original code and saved record |
| --- | --- |
| Consent, model/price/budget gates, image ownership/read/hash checks | Completed before the reservation and provider try-block; these gates did not cause this recorded failure. |
| Local SDK construction and request preparation | Still possible: e.g. a TypeError, SDK configuration exception or other non-ValueError. The `SENT` marker precedes SDK construction, so it does not prove network transmission. |
| Transport/proxy/credential substitution | Still possible. No retained HTTP event or origin evidence. |
| HTTP response/rejection | Still possible, including 400, 401, 403, billing or server errors. A successful prior Models GET does not prove Messages authorization or available credits. |
| Standard SDK timeout, RateLimitError, cancellation | Inconsistent with the saved generic code under the original handler, which assigned separate TIMEOUT, RATE_LIMIT and INTERRUPTED codes. This does not rule out differently surfaced proxy errors. |
| Normal JSON/Pydantic/domain validation ValueError | Inconsistent with the old code, which assigned INVALID_RESULT. Other response parsing exceptions (including SDK APIResponseValidationError, AttributeError/TypeError), cleanup and persistence errors remain possible. |

Nothing in this evidence establishes a charge or a zero charge. The new diagnostics
apply to future separately authorized attempts; they are not backfilled into the
original run with guessed values.

## Offline investigation

The unchanged pre-repair provider was loaded from Git and run with a MockTransport,
the existing private processing image, and an explicitly synthetic response. No
network transport or image analysis was used. It constructed and parsed successfully:

- One PNG block, 2,232,249 decoded bytes; base64 validation round-tripped exactly.
- Original complete serialized request: 2,983,978 bytes. No reproducible encoding
  or local size-handling defect; remote acceptance cannot be proved by mocks.
- Endpoint `/v1/messages` on `https://api.anthropic.com`; exact model
  `claude-haiku-5-5`, output cap 2048, service tier `standard_only`.
- Pinned Anthropic SDK 2.3.0 accepts `messages.parse(output_format=VisionResult)`
  and emits `output_config.format` with an object JSON schema (4,967 serialized
  schema bytes in this replay). A synthetic conforming response passes SDK parsing,
  Pydantic validation and photo-ID validation. Remote schema acceptance remains unproven.
- With a synthetic test credential only, the SDK emits Bearer authentication and
  API version 2026-10-21. Production still uses the configured ANTHROPIC_API_KEY
  binding; no auth header or credential value was printed or saved.
- Offline inspection of the default HTTP client's transport selection confirms
  api.anthropic.com selects the approved cloud proxy. Inherited proxy settings,
  CA trust and TLS verification are preserved. The runtime reports the binding
  ready and policy enforced, but cannot prove substitution on the historical call.

The original request included `speed="standard"`. The pinned SDK's own parameter
documentation warns that some models require a fast-mode beta for **any** explicit
speed value. Removing this optional parameter is a conservative compatibility
repair: default standard inference and `standard_only` capacity remain. It is a
possible 400 trigger, not an established explanation for this failure. No beta,
fast mode, priority capacity, caching, tools or alternate model was added.

## Safe failure diagnostics

`radar/jewelry/errors.py` classifies SDK exception types and HTTP status without
stringifying exceptions. Only these fields are persisted in new failed runs:

- `exception_class`: bounded allowlist; unfamiliar classes become UnknownException.
- `http_status`: integer 100–599 or null.
- `provider_error_type`: allowlisted typed error or null; never arbitrary text.
- `provider_request_id`: bounded `req_…` identifier or null; configured secret rejected.
- `failure_stage`: configuration, preparation, transport, HTTP response, response
  parsing, post-response validation, result persistence, cleanup, or unknown provider execution.
- `category`: authentication, permission, invalid request, billing/spend limit,
  rate limit, transport, timeout, invalid result, local configuration, interruption
  or other provider failure.

HTTP-secret/proxy HTML errors still retain status with null provider type/request ID.
Status and even a request ID are diagnostic evidence, not cryptographic proof of origin.
Billing/spend classification requires HTTP 402 or a recognized typed billing/spend
signal. A generic 400 or 429 whose billing explanation appears only in message
text remains INVALID_REQUEST or RATE_LIMIT; message text is deliberately not mined
or retained. Billing reconciliation always requires independent evidence.

HTTP hooks record only status/request ID and stage, never bodies or auth headers.
Transport handoff does not prove remote receipt. Response metadata survives malformed
JSON and Pydantic errors. Outer asyncio deadlines stay TIMEOUT rather than being
mistaken for cancellation; a secondary cleanup failure cannot mask the first error.
SQLite JSON accommodates the optional diagnostic fields without migration. Prompt,
schema, model, limits, reservation rules, retries and pilot guard are unchanged.

## Validation, preservation and billing

- Baseline: 313 passing tests (Task 005's 301 plus 12 existing pilot safeguards).
- Repaired full suite: **342 passing tests**, including 29 new mocked diagnostic
  cases; Ruff and whitespace checks pass. Existing Starlette/AnyIO warning only.
- Browser: desktop fixture flow and nine Android-sized views pass, with no overflow,
  console errors or external requests. Private owner case checked with GET-only
  browser routing: failed run visible, Haiku and Sonnet buttons disabled.
- Private database backup and before/after row comparison preserve all owner data;
  private image hashes are unchanged. Tests use temporary databases. The original
  outcome/consent records are not edited, and no billing reconciliation was applied.

No original provider request ID or message ID was retained, so this run cannot be
uniquely identified in Anthropic billing from local evidence. The timestamp/model
are lookup clues for the owner/provider, not a verified match. Do not release the
reservation without independent final billing evidence, including for a zero charge.

Mocks establish local behavior only. Confirming end-to-end Messages acceptance
would require a new, separately authorized real request; it would not reconstruct
the historical failure. This work neither authorizes that request nor resets the
consumed pilot. No automatic retry, Sonnet escalation or Task 006 work is included.
