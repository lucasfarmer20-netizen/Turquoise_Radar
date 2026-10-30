# Haiku QUICK output allowance — October 30, 2026

The latest authorized real-photo attempt returned HTTP 200 with `max_tokens` as
its stop reason and invalid JSON during response parsing. Reported output usage
was 2,048 tokens, including 1,872 thinking tokens. These are diagnostic counters,
not a confirmed bill or valid jewelry findings.

The owner directed that thinking remain enabled and unchanged. This repair raises
only Haiku QUICK's application output allowance from **2,048 to 32,768**. The SDK
request retains the same implicit thinking/adaptive behavior and effort; no
`thinking`, effort, speed, tool or caching parameter is added. No token-usage
optimization is included. `radar/vision.py` is unchanged.

The dated Haiku price profile and its provenance now specify the larger output
cap. `PriceProfile` accepts up to 32,768; its old smaller values remain valid so
historical configuration can still be read. The Haiku settings example and the
QUICK fallback allowance match the new limit. Actual execution continues to take
its allowance from the current verified database profile.

The existing Decimal reservation formula needs no algorithm change:

`(1,000,000 × $0.50 + 32,768 × $2.50) / 1,000,000 = $0.581920`

Rates still use Haiku's conservative higher tier across the full 1M input ceiling.
The original owner-supplied reference date, cache rates and source attribution are
unchanged. No pricing or model endpoint was queried. Thinking usage is a subset
of total output and is not counted twice. Usage above the profile limit or under
unsupported conditions remains unknown rather than releasing the reservation.

VisionResult's schema, field lengths, safety/evidence validators and production
system prompt remain unchanged, as do prompt/schema versions. The output limit is
already part of the cache identity, so no old result is silently reused under the
new allowance. The **2 MiB decoded-response safeguard**, 45-second provider timeout,
outer timeout and zero-retry behavior remain unchanged. More output capacity is
not a guarantee of a valid result or successful completion within those limits.

## Applying after merge

This PR updates reviewable code and configuration files. The owner's actual
database, saved photos, all historical attempts, consumed authorizations and
three failed $0.505120 holds remain untouched during development.

After merge and before any separately authorized attempt, append
`docs/anthropic/2026-10-30/claude-haiku-5-5.price-profile.json` through the existing
`/vision/settings/pricing` workflow in the actual application database. Verify
that the newest Haiku profile is current, QUICK quotes 32,768 output tokens and
$0.581920, and the shared remaining budget/daily allowance still covers the run.
If the existing 30-day verification window has expired, obtain fresh owner pricing
verification rather than silently changing the reference date.

Appending a profile does not enable calls. Keep provider `fixture`, general live
disabled, Sonnet unverified/disabled, the $5 monthly budget and 10/day limit.
Existing private executors and consumed authorization records must not be patched
or replayed. A new one-request authorization will be prepared separately after
merge, bound to the new profile and amount. This PR prepares no such authorization.

## Offline verification

Eight new regression cases cover historical and enlarged profile bounds, exact
reservation math, usage above the old cap, thinking accounting, append-only
profile installation in a temporary database, unchanged historical holds,
consumed authorization gates, changed cache identity, explicit consent at the new
amount, insufficient remaining budget and full reservation retention on failure.

Mocked SDK requests before/after differ only in `max_tokens`; both use the production
VisionResult parser and the same synthetic image and prompt. Tests use blocked
external transports and temporary databases. No Anthropic call or paid retry is
part of this change.
