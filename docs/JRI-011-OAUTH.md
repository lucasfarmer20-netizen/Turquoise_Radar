# JRI-011 OAuth compatibility correction

Based on merged main `5a4834f6449778a319a362c6f7f8eeee86b08f30`.
The adapter accepts eBay's documented `Application Access Token` response type,
retains Bearer/omitted-type compatibility, and continues sending Browse
`Authorization: Bearer`. Token and positive integer lifetime validation remain;
whitespace-only tokens are rejected. OAuth 4xx diagnostics retain only the stage,
HTTP status and a finite allowlisted error code (otherwise `UNKNOWN`). 401/403
remain `PERMISSION` failures with no retry or later query execution. Arbitrary
error descriptions, response bodies and authorization values are discarded.
429 and transient-service bounded retry handling is unchanged.

Official application-token documentation was read on 2026-11-01:
https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html
The current page shows `expires_in: 7200` and
`token_type: "Application Access Token"`. OAuth success alone never establishes
Browse access. No production defaults or approval gates changed.

## Validation

- Full suite: **829 passed**, with one upstream Starlette/AnyIO deprecation warning.
- Discovery suite: **53 passed**; all HTTP transport is mocked.
- Ruff, repository formatting and whitespace checks passed.
- Chromium displayed the mocked `OAuth HTTP 401: invalid_client` error on the
  existing Discovery page without the arbitrary upstream secret description.
- Mocked coverage includes documented token response/reuse across four searches,
  malformed responses, unknown and non-string error codes, secret-safe persisted
  diagnostics, and 401/403 stopping before Browse or later queries.

## Authorized one-time execution

The attached published runtime reported current observations, enforced access to
`api.ebay.com`, and ready direct `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` bindings.
Readiness does not verify credential validity. Managed proxy and TLS verification
were retained; supported per-command network permission enabled connectivity.
No separate setup OAuth probe was made.

A uniquely named private database was initialized through `radar.cli init`, with
no demo seeding. Listings, runs, observations, feedback and maker decisions were
all zero before constructing the plan. Its prepared backup was restored to a
separate directory, checked for integrity/foreign keys, and compared by complete
SQL dump before execution. No prior task's private history, preferences, maker
references or evaluation photos were restored or claimed.

The actual planner matched the authorized order exactly: `blue stone ring`,
`mixed jewelry lot`, `silver ring with blue stone`, `sterling jewelry lot`.
Each had one page, limit 3, offset 0, `newlyListed`, both buying formats and
omitted `auto_correct`; maximum 12 returned observations including duplicates and
invalid records. No additional narrowing filters were present. Existing US/99669
context, timeout and retry controls remained. Owner confirmation was supplied in
the task for this identical preview, and all existing production gates were used.

| Actual result | Count/status |
|---|---|
| OAuth attempts / retries | 1 / 0 |
| OAuth result | HTTP 401, `invalid_client`, `PERMISSION` |
| Browse attempts / retries | 0 / 0 |
| Queries actually sent to Browse | None |
| Returned / duplicate / invalid observations | 0 / 0 / 0 |
| Unique listings / workbench promotions | 0 / 0 |

The first planned query has an execution bookkeeping row, but its phrase was
never transmitted to Browse. No token was obtained and no real candidate review
was possible. The run stopped immediately and was not replaced. An exclusive
private execution marker records consumption of this task's authorization.

Remaining blocker: eBay rejected the configured client credentials. The response
does not prove the specific cause; the matching active Production keyset/account
configuration needs owner verification. Any subsequent execution needs new
explicit authorization. Vision remains fixture/live-disabled. No inference,
scraping, scheduling, bidding, purchasing or merge occurred.

Private database, exact preview, restore and request-count receipts remain in
ignored `data/`; none are part of this change.
