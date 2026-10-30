# eBay discovery setup — Task 003

The default is **fixture mode**, with production disabled. The owner's developer
application is awaiting final approval. Task 003 made **no real sandbox or
production API calls**. Tests use handcrafted synthetic Browse-style structures
and httpx MockTransport; they do not represent current eBay inventory.

## Fixture walkthrough (no credentials)

Start the existing app as described in README, then open **Discovery**.

1. Choose **Preview search plan**. The default budget selects four curated
   query/parameter combinations, led by the standalone `nature america` pattern.
   The original canonical phrase before transformation remains `Native American`;
   the transmitted query and recorded `q` remain exactly `nature america`.
2. Return to Discovery, keep **Fixture · NON-LIVE** selected and correction omitted,
   then click **Run discovery**. On a fresh discovery database, the checked-in
   fixtures yield **11 observations → 6 unique items + 5 duplicate observations**.
   No search-run statistics or discovered inventory are seeded before that action.
3. Inspect a ring's **Full discovery history**. One identity can retain independent
   `nature america`, `old blue stone ring` and `silver ring blue rock` observations.
   Returned queries with no source correction field remain UNKNOWN correction
   behavior, even when the exact transmitted query returned results.
4. Run again with **KEYWORD** enabled. The nature-america fixture explicitly reports
   `autoCorrections.q = Native American`. The original/transmitted query remains
   untouched; the correction is stored separately on each observation/execution.
   There are zero new identities on a repeat, with all observations retained.
5. **Send to review queue** creates or links one owner listing, keeps raw source
   evidence, and opens Phase 002's detail/evidence editor. This action is
   idempotent; discovery alone does not silently fill the owner queue. No resale,
   weight, material verification, fees, taxes or Alaska eligibility are invented.
6. Expand **Run budgets & query groups** to edit exact phrases, enable/disable groups,
   choose fixed-price/auction/both formats and set item-price filters/sorting.
   Budgets are capped and apply to returned observations, including duplicates and
   malformed items. Disabling a curated pattern also removes it from future plans.

Fixture ordering is deterministic, not a claim about marketplace relevance or
current prices. Fixture image URLs are retained as synthetic evidence; the UI uses
an explicitly non-live placeholder instead of fetching nonexistent remote photos.
Sandbox/production images, when actually returned, render as seller-provided
images. No backend fetch of arbitrary item or image URLs occurs.

## Environment variables and credentials

| Variable | Default / requirement |
|---|---|
| `EBAY_MODE` | `fixture`; choose `sandbox` only for permitted API testing |
| `EBAY_SOURCE_ENABLED` | `true`; `false` disables all discovery modes |
| `EBAY_CLIENT_ID` | Empty; environment-specific eBay application keyset ID |
| `EBAY_CLIENT_SECRET` | Empty; environment-specific application secret |
| `EBAY_PRODUCTION_ENABLED` | `false`; explicit enablement required later |
| `EBAY_PRODUCTION_APPROVED` | `false`; owner assertion of verified eBay approval/authorization |

The app does not auto-load `.env`. Use secure environment settings, not committed
files, copied browser forms, chat, shell tracing or logs. Use the matching sandbox
or production keyset; simply finding credentials never activates production.
OAuth tokens are held in memory for one adapter/run and refreshed before expiry;
credentials, Basic headers and tokens are not written to SQLite or browser output.
Errors are fixed/sanitized messages, without upstream response bodies or request
headers. Raw search observations redact credential-like keys and configured
credential/token values. Review platform log handling independently before live use.

Platform proxy-secret placeholders are not necessarily native credential values.
The official client-credentials exchange uses HTTP Basic authentication. If the
platform exposes only placeholders that cannot be used with that exchange, a
compatible secure credential binding/platform OAuth injection is still required;
do not extract proxy-held secrets or replace TLS verification to work around it.
Fixture discovery remains available while credentials/authorization are absent.

## Sandbox testing after permission is available

1. Verify current official Browse and OAuth requirements and the sandbox keyset's
   access. Sandbox coverage is test data and may differ from production coverage.
2. Configure `EBAY_MODE=sandbox` and secure credentials; allow HTTPS to
   **api.sandbox.ebay.com** through supported environment settings. Documentation
   needs **developer.ebay.com**. No production host is necessary for sandbox calls.
3. Restart the app. Connection status says credentials are present but access is
   **untested** until a request actually succeeds. Select **Sandbox · NON-LIVE** and
   run a small manual plan. Inspect recorded permission/rate errors before retrying.
4. Confirm unknown shipping, currencies, buying formats, correction fields and
   pagination against actual authorized responses before expanding the budget.

Both endpoints are fixed by mode, never accepted from a browser form:

| Operation | Sandbox | Production (disabled until authorized) |
|---|---|---|
| OAuth | `https://api.sandbox.ebay.com/identity/v1/oauth2/token` | `https://api.ebay.com/identity/v1/oauth2/token` |
| Search | `https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search` | `https://api.ebay.com/buy/browse/v1/item_summary/search` |

The OAuth exchange requests `grant_type=client_credentials` and the general
`https://api.ebay.com/oauth/api_scope` application scope. No user password/session
or purchasing scope is used. Verify the approved keyset's currently required
scope and use-case eligibility against official documentation before live testing.

## Production activation checklist — future owner action only

- Obtain final eBay production approval and confirm permitted business model,
  data retention/use, keyset, scopes, marketplace coverage and assigned quotas.
- Explicitly authorize live **read-only** discovery, review the exact search plan
  and set a small budget. Do not treat this task as production authorization.
- Configure production credentials securely, `EBAY_MODE=production`,
  `EBAY_PRODUCTION_ENABLED=true` **and** `EBAY_PRODUCTION_APPROVED=true`.
- Separately permit **api.ebay.com** via environment settings after authorization.
  Task 003 does not add that production domain or issue production requests.
- Restart and review status, then select Production and check the per-run explicit
  authorization box. Both environment flags and the individual run confirmation
  are required by server-side checks. A browser form cannot set environment approval.
- Review a bounded result set, Alaska uncertainty and source correction behavior.
  No run starts on app startup, credential detection, group edit or plan preview.
- Turn either flag off, use fixture mode or disable the source to stop live runs.

There is no scheduler, background polling, checkout, alerts, paid AI, extra
marketplace adapter, account system or public deployment in this task.

## API controls and query semantics

Search sends `q` unchanged using standard URL parameter encoding. Filters include
`buyingOptions:{FIXED_PRICE}`, `{AUCTION}` or `{FIXED_PRICE|AUCTION}`. Optional USD
item-price bounds use `price:[min..max]` and `priceCurrency:USD`. Supported selected
sort values are `newlyListed`, `price` and `-price`; no undocumented sort or exact
match syntax is generated. Auction summaries use `currentBidPrice` when the
returned buying option is auction-only; other summaries use their observed
`price`. Raw observations preserve all supplied fields for inspection. The app
does not invent missing closing times or bid increments.

The documented correction experiment accepts **`auto_correct=KEYWORD`** or omits
that parameter. It never sends `OFF`. Omission is not a guarantee of literal
matching. `autoCorrections.q`, when supplied, is recorded separately:

- `CORRECTED`: source-reported query differs from what was sent.
- `REPORTED_UNCHANGED`: supplied source query equals what was sent; still not a
  guarantee about hidden expansion/ranking behavior.
- `UNKNOWN`: the source did not provide a correction query.

Per-execution records include source/mode, exact original and transmitted queries,
parameters (including requested page offsets and limits), returned/unique/new/
duplicate counts, timestamps and sanitized errors. One listing's independent
query observations are retained across groups, pages and runs. Same query plus
same options across groups executes once with all matching group IDs retained;
different parameters represent separate experiments.

Default limits: **4 queries, 12 returned observations, 2 pages/query, page size 3**.
Hard owner-configurable ceilings are 20 queries, 200 observations, 5 pages/query
and page size 50. Remaining results are shared across the selected plan; the runner
may use less than its cap if queries return fewer items. Returned duplicates and
invalid items consume the budget. Pagination uses bounded local offsets and never
follows arbitrary response-provided next URLs. No external redirects are followed.

Default API timeout is 15 seconds, retry limit 2, minimum request interval 1 second.
These are local settings, **not verified eBay quotas**. 429/5xx and transport errors
receive bounded backoff; numeric or HTTP-date Retry-After is honored within the
local maximum wait. A longer wait stops the run rather than retrying too early.
Permission/rate/network failures stop remaining queries. Defer further manual
runs until the indicated limit/access issue is resolved; no automatic reschedule
or persisted per-account quota/cooldown service exists. Only one manual run per
application process is allowed; run a single application worker for this local app.

## Normalization and Alaska safety

Summaries preserve item ID, raw title, link, price/currency, seller username,
buying options, condition/category, image reference and supplied creation/end
dates. Missing prices remain unknown. An amount without item currency is not
silently treated as USD; raw shipping evidence remains inspectable.

Requests include the US/99669 contextual location header. **A requested destination
is not response proof of delivery eligibility or a final Alaska shipping quote.**
Summary shipping amounts are estimated and all discovery shipping status remains
UNKNOWN. A displayed generic US shipping rate or free shipping never confirms
Soldotna eligibility. Use the existing evidence editor for independently confirmed
Alaska charges/availability. The adapter makes no detail/checkout requests to
resolve shipping during this task. Other fees, taxes, resale estimates, purity,
net weight and cultural attribution remain absent until supported owner evidence.

Fixture, sandbox and production item identities use separate source namespaces;
their observations cannot overwrite each other's records. Promoting an identity
links the owner's listing and every query observation. Subsequent discovery keeps
new observations separately and does not overwrite an owner-calibrated acquisition
or a purchased item's financial history. Inspect current observations before buying.
Source-corrected and non-live matches stay separate in pattern effectiveness;
non-live successes do not boost real query rankings. Attribution of one purchase
to several queries is non-additive: never sum those groups as inventory/profit totals.

## Documentation verification and remaining limitations

Official documentation access was attempted on **2026-10-29 (Alaska)** and rejected
by the running cloud proxy with 403. The API structure above is the documented
Browse/OAuth contract targeted by the implementation and mocked fixtures; live
schema/permission validation is still pending. The owner supplied the explicit
`auto_correct=KEYWORD` requirement in Task 003. No approval or assigned numerical
quota has been inferred from API architecture or credentials.

Official references to verify after supported access is applied:

- [Browse search](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [Application OAuth](https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html)
- [Buy API production requirements](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html)
- [API call limits](https://developer.ebay.com/develop/get-started/api-call-limits)

Current verified account quota: **UNKNOWN**. Current production approval: **pending
owner confirmation; not enabled**. APIs/operations not implemented here: detail
hydration, shipping quote/eligibility confirmation, user/purchase OAuth, live
resale/spot prices, recurring discovery and automatic purchases. Credentials and
permitted access are the remaining prerequisites for authorized sandbox testing,
not prerequisites for using the complete fixture pipeline.
