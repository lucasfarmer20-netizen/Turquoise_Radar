# Anthropic pilot readiness — 2026-10-30 UTC

**Haiku is configured and ready for one future, explicitly owner-authorized
real-photo pilot. No paid run is authorized or started by this configuration.**
Sonnet remains access-unverified and ineligible, as the owner requested while the
historical 401 origin remains unresolved. Sonnet does not block Haiku readiness.

Subsequent [pilot preparation](HAIKU-PILOT.md) started the app on private loopback,
preserved its database, added a one-request consent guard and passed 313 tests.
The owner asked about direct conversation upload because no private phone preview
is available. The pricing-installation checks below remain the evidence for the rates;
the pilot preparation document records the newer application/record counts.

Effective settings remain `VISION_PROVIDER=fixture`, `VISION_LIVE_ENABLED=false`,
$5.00 per UTC month, and 10 paid analyses per UTC day. No Messages API call, paid
request, image upload/analysis, credential change or new development phase occurred.

## Evidence and provenance

The owner supplied the following official references and pricing terms, dated
**October 30, 2026**, obtained through the owner's ChatGPT web research:

- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Haiku 5.5 overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview)
- [Sonnet 5.5 overview](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)
- [Service tiers](https://platform.claude.com/docs/en/api/service-tiers)

**This runtime did not independently access these documents.** Earlier document
retrieval attempts were blocked; that limitation is resolved for configuration by
the owner's explicit supplied evidence and authorization to use it. The complete
[owner-supplied reference record](anthropic/2026-10-30/owner-supplied-pricing.json)
retains the source URLs, date, tiers, caching prices and modifiers.

Prior successful Haiku Models API lookups provide account/model metadata evidence.
[Saved SDK lookup evidence](anthropic/2026-10-30/model-access-recheck.json) reports
image input, structured outputs, 1,000,000 input tokens and 128,000 maximum output
for both models. No inference behavior or account credit balance was tested.

## Official rates supplied by owner

All amounts are USD per million tokens.

| Model / prompt length | Input | Output | 5-minute cache write | 1-hour cache write | Cache read |
| --- | ---: | ---: | ---: | ---: | ---: |
| Haiku 5.5, up to 100,000 input tokens | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |
| Haiku 5.5, over 100,000 input tokens | 0.50 | 2.50 | 0.625 | 1.00 | 0.05 |
| Sonnet 5.5 | 2.00 | 10.00 | 2.50 | 4.00 | 0.10 |

Haiku's threshold counts **all prompt input, including cached tokens**. Above 100K,
the higher prices apply to the **entire request**, including output and caching;
these are not marginal rates on only the excess tokens.

Global inference is the standard default without a regional surcharge. US-only
`inference_geo="us"` has a 1.1 multiplier across input, output and caching.
`service_tier="standard_only"` restricts requests to standard capacity. Batch has
a 50% discount but is outside this synchronous workflow. Fast/priority modes are
also excluded. These terms are recorded as supplied by the owner, not runtime
observations of provider billing.

## Configured profiles and local installation

- [Haiku application profile](anthropic/2026-10-30/claude-haiku-5-5.price-profile.json)
  and [provenance/status](anthropic/2026-10-30/claude-haiku-5-5.pricing.json).
- [Sonnet application profile](anthropic/2026-10-30/claude-sonnet-5-5.price-profile.json)
  and [provenance/status](anthropic/2026-10-30/claude-sonnet-5-5.pricing.json).

Both profiles validate against the existing strict `PriceProfile` schema and are
dated 2026-10-30. `verified_on` records acceptance of the owner-supplied official
references. Haiku has `model_access_verified=true`; Sonnet has that flag **false**.
Profiles retain the existing 30-day freshness gate; access flags do not enable
live requests by themselves.

The configured default database `data/radar.db` was absent in this workspace.
The existing ordered migrations initialized an empty v5 schema, then a transaction
stored the two profiles. No example inventory, photos, cases or runs were seeded.
No pre-existing database was replaced. The database is ignored by Git; the dated
JSON files preserve portable configuration if another deployment uses a different
private database. No running deployment or cloud environment was republished.

The budget engine accepts one rate pair across the full input ceiling. Therefore
Haiku uses **0.50 input / 2.50 output**, with **0.625 / 1.00 / 0.05** cache write
5-minute / write 1-hour / read prices, at every prompt length up to 1M. This is a
conservative bound: short Haiku requests will be overestimated. The profile's
full-limit assertion describes coverage by this bound, not a claim that official
prices are uniform. Sonnet uses **2.00 / 10.00**, with **2.50 / 4.00 / 0.10** caching.

The application output cap remains **2,048 tokens**; the provider capability maximum
is **128,000**. The full 1M input ceiling was not reduced to the cheaper 100K tier.

| Model | Reservation for full input ceiling + 2,048 output | Access gate |
| --- | ---: | --- |
| Haiku 5.5 | $0.505120 | verified |
| Sonnet 5.5 | $2.020480, if later separately eligible | unverified; blocked |

Existing Decimal arithmetic rounds upward to microdollars:
`(1,000,000 × input rate + 2,048 × output rate) / 1,000,000`.
Confirmed invoice billing remains separate from estimated usage costs.

The current adapter requests **uncached, synchronous, global standard inference**:
it sends no cache control, batch, regional selection or tools, and requests
standard-only capacity and standard speed. Configuring cache prices does not enable
prompt caching. A full-context cache write can cost more than the current uncached
reservation; enabling cache writes would require a separate reservation review.
If unexpected usage exceeds the reservation, existing behavior retains unknown
cost and the allowance rather than automatically releasing it. Unsupported regional,
fast/priority or tool usage also stays unknown; no modifier is silently ignored.

## Preflight and safeguards

The local database preflight reports:

- Haiku has a current eligible profile and secure credential binding; its **only**
  current planning blocker is intentionally disabled paid/live mode.
- Sonnet has both the disabled-live blocker and the unverified-access blocker.
- Monthly budget: **$5.00**; accounted spend: **$0**; remaining: **$5.00**.
- Daily paid analyses: **0 / 10**. Listings, photos and runs remain empty.

[Offline validation evidence](anthropic/2026-10-30/profile-validation.json) covers
schema validation, exact configured rates, full-input reservations, the 100,000 /
100,001 boundary with cached input, both cache-write durations, cache reads,
conservative low-tier estimates, and unsupported-modifier handling. No external
request or real-photo processing was used for these checks.

Atomic reservations, daily/monthly limits, no retries, no automatic Sonnet escalation,
per-run payment/transmission confirmation, unknown-cost retention, separate model
histories and independent financial/evidence safeguards were preserved. Pricing
installation changed no application code or budget algorithm and passed **301
tests**. The subsequent pilot guard adds 12 tests, for 313 passing tests; the
existing Starlette/AnyIO warning remains. Ruff and whitespace checks pass.

For the future one-image pilot, the owner must explicitly authorize the selected
photo and displayed allowance, then use the existing live and per-run gates. A
photo has not been selected or uploaded here. This is readiness for that separately
authorized experiment, not proof of successful paid inference. Sonnet is not needed.

## Checkout and Sonnet diagnostic history

The current `work` branch is at GitHub main
`2e2277673a2da6817e17b46658b1c564e5a70764` (Task 005 merged). The earlier 227-test
report came from Task 004 `4648ee01a196ecb303fe4bbdb127e7d3184d284e`. Fast-forwarding
added 71 vision and three migration cases: 301 total, not a demonstrated regression.
Prior local work remains preserved in stash
`0eef0614d6eeac12b98ccc2f9b262b0faf9e7c0a`. No push or merge commit was created.

The original Sonnet GET to `/v1/models/claude-sonnet-5-5` used legacy API-key
authentication and version `2023-06-01`; only HTTP 401 / Python `HTTPError` was
retained. Its provider JSON error type and provenance headers are unknown. The
pinned SDK maps 401 to authentication and 403 to permission errors, but status alone
cannot distinguish an Anthropic response from a proxy-generated response. Retained
managed diagnostics had no records. No confirmed historical cause is claimed.

Later read-only rechecks succeeded for both models using the original method and
the pinned SDK's current Bearer authentication/API version `2026-10-21`. Those
observations remain in the evidence files, but **do not override the owner's
instruction to keep Sonnet unverified** until the historical 401 is resolved.
No credential, authorization-header value, or sensitive response body is recorded.
