# Task 001 validation

Validated in the supplied Python 3.12 cloud environment.

- `uv sync --frozen`: installed 26 locked dependencies; repeat offline sync succeeds.
- `python -m radar.cli seed`: 26 observations, 25 new listings, 1 duplicate update.
  Repeat seed: 0 new, 26 updates, still 25 distinct listings.
- `pytest -q`: **55 passed**. One upstream Starlette TestClient / AnyIO deprecation
  warning; no skipped tests, failures, live marketplace or paid API calls.
- `ruff check .` and `ruff format --check .`: pass.
- Uvicorn startup and local `/health`, dashboard and listing detail requests: pass.
- Chromium desktop 1440×1000 and mobile 390×844: dashboard and detail rendered,
  25 demo cards, no horizontal document overflow, no JavaScript/console errors.
- Real browser HTMX anomaly filter narrowed to one `nature america` listing.
  Feedback saved and survived reload. Normal manual-entry form and JSON paste
  import saved records in a separate temporary browser-test SQLite database.
- Browser testing caught and fixed a normal form Origin issue: `Referrer-Policy:
  same-origin` preserves local form Origin while withholding cross-site referrers.
- Demo database retains zero feedback events; browser mutation checks used a
  separate temporary database, not the prepared demo data.

Socket restrictions stall TestClient's event loop in the command sandbox here;
full tests, server startup and Chromium validation used the platform's approved
escalation mechanism. No assertion, TLS verification, or network policy was disabled.
Chromium's container launch used its standard `--no-sandbox` option; this does not
change the environment's network access policy.

Screenshots were inspected as transient `/tmp/turquoise-radar-*.png` artifacts.
The reusable environment draft has `install_script` and `start_skill` saved;
publication and restoration in a new task have not been tested or claimed.

## Publication audit

The complete Phase 001 implementation was published to GitHub `main` as
`535e508d8e2596b440447b5810bd8f0b77b553b5`. The subsequent publication audit
expanded `.gitignore` to cover local environment variants, common credential files,
virtual environments, SQLite files/sidecars outside `data/`, caches, build outputs,
and temporary/artifact directories. The non-secret `.env.example`, synthetic
fixtures, and vendored runtime assets remain versioned intentionally.

The hygiene follow-up uses a separate branch because `main` already has history.
No existing remote commits are overwritten. Ignore rules are a preventive measure;
review staged contents before committing, because arbitrary source files can also
contain secrets. The audit checks tracked paths and common credential signatures
without printing potential secret values. It is not a guarantee that a general
secret scanner would detect every possible credential format.


## Task 002 validation

- Branch `task/002-owner-calibration` starts at updated main
  `a7541b1e42f31d5f022d9325a30e249aab6cadfb` (merged publication-hygiene PR).
- **91 tests pass**: all 55 Phase 001 cases retained, with expected monetary values
  and explicit synthetic assumptions updated for the corrected resale model, plus
  36 calibration/capture/workflow/comparable/migration cases. No tests are skipped.
- Ruff lint and formatting pass. Existing upstream AnyIO/Starlette deprecation
  warning remains; no test assertion was disabled.
- Fee regression: a $50 sale with buyer shipping $8 and tax $4 gives $4 item-only
  commission and $2.10 processing on $62, including one $0.30 fee. With $1 packaging
  and $20 landed cost, net resale is $42.90, profit $22.90, ROI 114.5%, and maximum
  item price $18.81 when inbound shipping is $8 and targets are $15 / 60%.
- Fresh, populated Phase 001 fixture, repeat migration, future-version refusal,
  migration rollback and latest-feedback state preservation are tested.
- An additional check migrated a copy of the actual prepared Phase 001 SQLite DB:
  25 listings, 25 patterns, one preferences row and zero feedback rows; all prior
  column values and raw/original JSON were unchanged. No real data was re-seeded.
- Desktop 1440×1000 and mobile 390×844 Chromium checks exercised copied-text
  extraction, owner edits/confirmation, financial recalculation, queue state,
  purchase feedback, comparable capture/confirmation/linking, source effectiveness,
  HTMX filtering and nine mobile views, with no page overflow or browser errors.
  Browser mutations used a separate synthetic temporary SQLite DB.
- Direct official eBay/Whatnot documentation requests failed with proxy 403;
  numerical current quotas/production terms and current fee tiers are not claimed
  verified. Additions for developer.ebay.com and help.whatnot.com are saved to
  the environment draft, preserving api.github.com. They require user settings
  review/save and publication; they have not been applied to the runtime.

The implementation makes no live marketplace, paid vision, purchase or public
hosting calls. API-assisted PR creation remains blocked until api.github.com access is applied.
The task branch is prepared for publication without merging main. Saved Task 002
startup checks also passed against the migrated default database: health and all
six owner workflow pages responded with expected content.


## Task 003 validation

- Base: updated main `7f437e9503823b539a90cf6eb3f62bdcaf8e4351` (merged Task 002 PR).
  Work is on `task/003-ebay-discovery`; no merge to main is authorized.
- **128 tests pass**, including all 91 prior cases and mocked OAuth/Browse,
  permissions, production/source gates, retries/Retry-After, pagination, exact
  query/correction preservation, multi-query deduplication, unknown Alaska costs,
  explicit review handoff and populated Phase 002 migration/rollback tests.
- HTTP transport in discovery tests is mocked; unmocked async HTTP is explicitly
  rejected. No real production/sandbox calls or credentials were used.
- Ruff lint, Ruff format checks and git whitespace checks pass. One existing
  Starlette/AnyIO deprecation warning remains; no assertions or tests are disabled.
- SQLite v3 migration tested on a populated Phase 002 fixture containing owner
  evidence, feedback, preferences, comparables and edit history. Every prior value
  was unchanged; new tables were empty. Failure rolled back without losing data.
- A copy of the actual Phase 002 database was also migrated: 25 listings, 25
  patterns, one settings row and zero feedback/comparable/edit rows; all prior
  column values and original JSON were identical after migration.
- Desktop 1440×1100 and mobile 390×844 Chromium passed: empty-state/plan preview,
  fixture run (4 queries, 11 observations, 6 identities, 5 duplicates), repeat run
  (zero new), KEYWORD correction, independent item history, review handoff/evidence
  editor, group disable and smaller result budgets. Seven mobile views had no
  horizontal overflow. Browser errors and external network requests were both zero.
- Fixture image references are preserved but represented by explicit non-live
  placeholders. Browser/API fixtures do not imply current marketplace inventory.
- Official documentation access returned proxy 403. Current account quotas,
  production approval and live API responses remain unverified; fixture discovery
  is fully validated independently of those prerequisites.
- The restarted private app passed /health, /discovery, /discovery/plan, workbench
  and queue readiness checks against the migrated default database. It retained
  all 25 owner/default listings and had zero automatic discovery runs. Startup
  added only the two bounded jar/bulk vocabulary patterns and three missing query
  groups; no existing pattern or owner preference was overwritten.
- Task 003 startup/migration instructions and optional api.sandbox.ebay.com access
  were saved to the environment draft, preserving prior documentation/GitHub API
  entries. No production API host was added. Saving is not runtime application or
  publication; documentation, credentials and permitted sandbox access remain
  prerequisites for real API validation.


## Task 004 validation

- Branch task/004-multi-source-discovery is based on merged Task 003 main
  f37f63e175190e38a8b87f99a0404d254e09bfbc. eBay adapter/configuration/gates are unchanged.
- **165 tests pass**, including every retained Task 003 case and new classification,
  .eml/text/MIME, duplicates, source identities, query provenance, grouping, receipts,
  real-vs-estimated costs, HTML/URL safety, attachment, size and v4 migration tests.
- Ruff lint/format and whitespace checks pass; one existing Starlette/AnyIO
  deprecation warning remains, with no skipped/disabled tests.
- The source fixture demo yields 15 identities: 5 ShopGoodwill, 5 HiBid, 3 CTBids,
  1 EstateSales.org and 1 EstateSales.net. Thirteen unique messages retain 23
  independent observations; a repeated alert creates duplicate accounting, not
  another message/item. No purchase feedback is seeded.
- Tests cover bid/outbid/win/paid/shipped correlation; a paid single-item receipt
  reconciles 18 subtotal + 0 tax + 9 combined shipping/handling = 27 order total.
  A two-item receipt keeps its 12 shipping/handling at order level. Nonzero order
  tax, premiums, service fees and separate shipping/handling never become first-lot
  costs merely because their labels precede the first lot. Conflicts,
  incomplete amounts, non-USD and mismatches cannot apply paid landed costs.
- v4 migration/rollback tests use populated v3 tables, including eBay query histories,
  original observations, financial edits, feedback, comparables and settings.
  A copy of the actual default v3 DB also retained every old row/column unchanged:
  25 listings, 27 patterns, one preferences row, three query groups and all prior
  observation/review tables. New email data is not seeded into owner inventory.
- Desktop 1440×1000 / mobile 390×844 Chromium passed uploads from five sources,
  duplicate handling, 15-candidate/source filtering, review handoff, 27 paid-cost
  confirmation, multi-item non-allocation, purchase preservation after repeat import,
  irrelevant status, explicit query confirmation, exact clipboard copy and setup
  flags, and catalog-only reminder evidence. Ten mobile views had no horizontal
  overflow. Browser errors and external requests were both zero.
- Original bodies are escaped text only. No actual owner email, address, tracking
  link or purchase record was used in checked-in fixtures. Original .eml files are
  ignored except explicitly named synthetic fixtures; private data/backups stay ignored.
- Official ShopGoodwill/HiBid help requests returned proxy 403; current terms and
  actual search-digest formats remain unverified. No scraping or account/API access
  is inferred from website availability or owner-reported features.

- Restarted the private default app on Task 004 and verified ten readiness routes
  returned 200, including health destination 99669. Startup retained 25 existing
  listings, zero feedback, zero alert messages/observations/invoices; only eight
  reference sources and 168 recommended saved searches were initialized.
- Reusable install/start instructions were saved in the cloud configuration draft
  for v4/local alert intake. Reference-only ShopGoodwill/HiBid domains were added
  while preserving prior draft domains. Saving is confirmed; publication/application
  requires environment-settings review/save and publication and has not occurred.

## PR #4 compatibility follow-up

- Reviewed the pre-merge compatibility comment in PR #4 through GitHub's permitted
  HTML page. API access still returned Forbidden. Continued on the existing
  task/004-multi-source-discovery branch based on 583131576b407b8c34263fedf6169f3007ecca8f;
  no merge and no Task 005 work.
- **203 tests pass**: all 165 prior cases remain, with 38 new regression cases for
  reported item markers, bounded notice-context IDs, receipt correlation, repeated
  alerts, conservative multi-item charges, explicit embedded targets, malformed/
  unsafe/ambiguous/opaque URLs and text-reference limits. Prior test function bodies
  are unchanged. Ruff lint, formatting and Git whitespace checks pass. The existing
  Starlette/AnyIO deprecation warning remains.
- Synthetic ShopGoodwill Item/item #/standalone notice fixtures correlate one
  marketplace item without a usable direct link. A paid receipt reconciles 18 item
  subtotal + 0 tax + 9 shipping/handling = 27 total. Bid/win messages do not record
  purchases. Explicit paid confirmation and later re-import preserve confirmed
  costs/history. A two-item 36 + 3 + 12 = 51 invoice cannot apply per-item shipping,
  tax or handling automatically.
- Synthetic HiBid newsletters recover two distinct lot targets plus one catalog
  from explicit literal/one-pass-percent-encoded HTTPS targets inside URL paths on
  clicks.auctions.hibid.com. Only canonical provider identity URLs survive into
  normalized candidate/catalog links. A later direct link deduplicates to the same
  source item, preserving another observation. Catalog-only closing notices remain
  without candidates; closing text and CST are retained without inventing an offset.
- Opaque /ls/click?upn=... values and query-only redirects remain NEEDS_REVIEW.
  Spoofed hosts, non-HTTPS, credentials, unsafe ports, ambiguous/nested targets,
  double encoding and traversal paths are rejected. No tracking or marketplace
  requests are permitted by the regression tests.
- scripts/check-intake-compat.cjs runs against an empty disposable database, covering
  desktop 1440x1000 and mobile 390x844, the 27 paid confirmation, re-import history,
  multi-item non-allocation, embedded/catalog/opaque cases and twelve mobile pages.
  No overflow, browser errors or external requests. Owner data is not used.
- Seven additional .eml fixtures use only synthetic IDs, order references, tokens
  and descriptions. No original email, address, tracking URL or customer data was
  supplied or committed. Existing demo manifest/counts remain unchanged. Database
  schema, financial engine, review workflow and eBay adapter/gates are unchanged.
- The PR supplies the tracking host but no concrete real wrapper URL or encoding.
  Explicit-target recovery is tested synthetically, not claimed as validation of a
  particular real tracking scheme. Opaque links and unknown layouts remain review
  evidence; actual Personal Shopper/HiBid daily digests remain provisional.

## Owner-confirmed HiBid CL0 structure

- The owner supplied sanitized examples and confirmed `/CL0/`, one-pass percent
  encoding and `/lot/` versus `/catalog/` destination paths. Both pasted URLs
  themselves contained `/lot/`; classification follows the actual decoded path,
  not the descriptive label. No original email or customer information was supplied.
- Extraction now requires the observed `/CL0/<destination>/1/<message>/<token>`
  shape on HTTPS clicks.auctions.hibid.com. It separates the unique raw tracking
  boundary before decoding only the destination. Encoded `%2F`, `%3F` and `%26`
  are supported. Opaque suffixes never supply IDs, even when they contain encoded
  URLs or plausible item numbers. Unknown layouts/ambiguous boundaries need review.
- Canonical lot/catalog links omit UTM parameters and opaque data. The observed
  email convention does not establish a HiBid API contract or validate every
  newsletter/saved-search layout. Existing actual-format limitations remain scoped
  to the full templates, rather than to this now-confirmed URL structure.
- **220 tests pass**, retaining all 165 original Task 004 cases. Seventeen additional
  CL0 cases cover lot/catalog destinations, encoded query/boundary isolation,
  opaque lookalike targets, malformed/missing/ambiguous suffixes, bad escapes,
  unsupported prefixes and path-vs-label identity. Host/scheme/security cases use
  complete CL0 envelopes so they continue to test destination validation.
- Ruff lint/format and whitespace checks pass. Desktop and twelve mobile views
  pass with zero browser errors, overflow or external requests. The synthetic
  receipt still reconciles 27; multi-item allocation remains blocked. Only the
  two synthetic HiBid fixture shapes were updated; original owner data, financial
  logic, SQLite v4 schema and eBay gates are unchanged. PR #4 remains unmerged.

## Corrected HiBid catalog tracking host

- Added the owner's corrected catalog host `clicks.alerts.hibid.com` to the exact
  parser allowlist alongside `clicks.auctions.hibid.com`. No network permissions
  were added. Both hosts remain blocked as clickable originals and as nested
  destinations; only the isolated allowlisted provider destination is returned.
- Updated synthetic catalog fixtures to the corrected `/CL0/` structure targeting
  `synthetic-auction-house.hibid.com/catalog/910000000100`. Catalog-only reminders
  retain their closing evidence, count one offline recovery and create no lots.
  Browser checks assert the canonical house URL and no active tracking-host links.
- **227 tests pass**, preserving all 165 original Task 004 tests. Seven additional
  regressions cover spoofed alerts hosts, off-domain destinations, nested tracking
  destinations and opaque alerts evidence. Ruff lint/format, JavaScript syntax and
  whitespace checks pass; only the existing Starlette/AnyIO warning remains.
- Desktop and twelve mobile views pass with zero errors, overflow or external
  requests. Paid receipt and multi-item allocation checks still pass. Only synthetic
  fixtures are committed; owner data, schema, financial logic and eBay gates are
  unchanged. Changes remain on PR #4's branch; no merge or Task 005 work.

## Task 005 — Anthropic jewelry vision

- Branch task/005-jewelry-vision starts from merged main
  4648ee01a196ecb303fe4bbdb127e7d3184d284e, including all Task 004 compatibility
  fixes. No force-push or automatic merge. Financial/maximum-buy/Alaska analysis,
  eBay adapter/gates and multi-source parser/service implementations are unchanged.
- **301 tests pass**, retaining all 227 previous Task 004 cases and adding 74 image,
  provider, evidence, score, correction, concurrency, cost, cache and v5 migration
  cases. Existing migration-target assertions now use CURRENT_VERSION rather than
  hard-code v4; no cases were removed. Ruff lint, formatting, JavaScript syntax
  and Git whitespace checks pass. One existing Starlette/AnyIO deprecation remains.
- Generated synthetic images cover actual JPEG/PNG/WebP detection despite deceptive
  filenames, corruption, unsupported/animated formats, file/body limits, excessive
  dimensions/decompression bombs, EXIF orientation/GPS removal, useful processing
  resolution, trailing-content removal, private paths/integrity and replacement.
- Tests exercise deterministic free results, independent quick/deep histories,
  strict Pydantic fields/photo references, directly legible vs partial marks,
  direct/inferred confidence, maker leads vs generic purity stamps, weight/valuation/
  cultural/material claim rejection, mixed-lot research candidates and append-only
  corrections. No listing facts, financial costs, resale estimates, purchases or
  verified sterling/cultural attribution are automatically changed.
- Official anthropic==2.3.0 / pillow==13.0.0 are locked with hashes. Official tagged
  SDK README/client/Message/model/migration source was successfully read. Mocked SDK
  wire tests verify current Bearer authentication, /v1/messages, separate base64
  image blocks, Pydantic helper translation to output_config.format and no tools.
  No deprecated HTTP output_format or beta flags are sent. SDK retries, redirects
  and content telemetry are disabled; debug payload logging is suppressed.
- SDK response tests cover malformed/truncated/unsafe structured results, wrong
  photo references, timeout and 400/429/500 errors without retry chains. Every
  automated external transport is mocked/forbidden, including the SDK's httpx2
  transport. No real Anthropic API request or API credit consumption occurred.
- Spending tests verify key-alone/live/model/pricing/confirmation gates; explicit
  Sonnet authorization; global atomic daily/monthly reservations; concurrent
  identical-call coalescing and distinct-call budget exclusion; cached views/reruns;
  relevant-context invalidation; missing/additional usage; estimated vs confirmed
  cost; reported-model mismatch; interrupted/failed allowance retention; final
  provider reconciliation and cross-period unresolved reservations.
- Known alert-derived private descriptions are withheld from paid context. Only
  the title and owner-selected processing copies are supplied in those cases;
  raw receipt/address evidence stays local. Reflected credential content is rejected
  before it can enter a saved result/usage record. The API key has no browser field
  and is absent from database/run metadata, fixtures, logs and installation scripts.
- v5 tests populate every v4 table, including query histories, feedback, invoice
  components/observations, settings and comparables. All prior rows survive;
  repeated migration is a no-op, future versions are refused and failed v5 DDL
  rolls back. A copy of the actual private v4 database also preserved every prior
  row, followed by the same verified default-database migration with a pre-v5 backup.
  Startup/GET readiness does not seed vision photos, paid runs, prices or purchases.
- Synthetic demo was run twice in /tmp/turquoise-v5-demo.db: two illustrated
  listings, eight independent photos and three fixture runs including separate
  quick/deep comparisons and a mixed-lot lead. The second invocation reused cache
  entries. No genuine inventory evidence or transaction history was seeded.
- scripts/check-vision-browser.cjs passed desktop 1440x1000 and nine Android-sized
  390x844 views: four-photo upload, zoom, analysis, corrections, cached repeats,
  separate deeper comparison, replacement/removal, mixed sub-item review, paid
  controls disabled and financial-return links. No overflow, browser errors or
  external requests. Retained scripts/check-intake-compat.cjs also passed desktop
  and twelve mobile views on a separate v5 database, including the 27 paid receipt,
  catalog identity and multi-item non-allocation safeguards.
- Existing frozen installation was rerun successfully. No Cloud Environment draft,
  credential or network changes were made for Task 005; no republication is needed
  for fixture use. The private default app runs on loopback with vision/eBay fixture
  modes and paid/production flags false. Future live use genuinely needs secure
  key/flags, dated model/rate/context verification and permitted api.anthropic.com.
- Official web pricing/model/structured-output pages were blocked by the proxy.
  SDK model-name recognition is not account eligibility or price verification;
  no current rates, assigned quotas, credit balance or live model availability is
  claimed. Live provider behavior is tested with SDK mocks, not a paid end-to-end
  request. All committed test artwork is generated synthetically from code.

## Anthropic verification follow-up — 2026-10-30

- Corrected the stale checkout from Task 004 `4648ee0` to fetched main
  `2e2277673a2da6817e17b46658b1c564e5a70764` by fast-forwarding the existing `work`
  branch. All prior local changes are preserved in stash
  `0eef0614d6eeac12b98ccc2f9b262b0faf9e7c0a`; previous evidence was restored.
- After `uv sync --frozen`, the complete suite passed **301 tests**, including 71
  vision and three vision-migration cases added since the old 227-test checkout.
  The count difference was stale code, not a demonstrated regression. The existing
  Starlette/AnyIO deprecation warning remains. Ruff and whitespace checks pass.
- Haiku and Sonnet individual Models GETs now return 200 through both the original
  legacy method and the pinned SDK's current authentication/version. No original
  401 JSON error type/provenance headers were retained, and managed diagnostics
  returned no records. Current access is verified; historical error origin is not.
- Both public documentation URLs still fail with proxy tunnel 403; the web tool
  also fails without content. Owner-supplied rates are clearly distinguished from
  independent verification. No cache or modifier prices were invented.
- Dated inactive application-schema candidates preserve Haiku's 100K tier by using
  its higher supplied rates across the full 1M context ceiling. With 2048 output,
  existing reservation code yields 0.505120 USD for Haiku and 2.020480 for Sonnet.
  Offline checks cover schema, tier boundaries, conservative costs, unknown cache/
  regional usage, and rejection of inactive profiles using in-memory SQLite only.
- No owner database, application code, runtime flags, network configuration,
  credentials, API retries or activation gates were changed. No Messages API call,
  paid request or real-photo analysis occurred. See the
  [current report and readiness limitations](ANTHROPIC-VERIFICATION.md).

## Owner-supplied official pricing configuration — 2026-10-30

- The owner supplied complete official pricing/cache/modifier references and
  authorized their use. Source URLs/date/provenance are stored in
  `docs/anthropic/2026-10-30/owner-supplied-pricing.json`; no independent runtime
  access to those documents is claimed. This supersedes the preceding pricing
  blocker, without resolving or reclassifying the historical Sonnet 401.
- Haiku is configured at the higher full-context rates: input/output 0.50/2.50,
  5-minute/1-hour cache creation 0.625/1.00, cache read 0.05 USD per million.
  Sonnet has 2.00/10.00, 2.50/4.00, and 0.10 respectively, but its access flag
  remains false per owner instruction. Both profiles are dated 2026-10-30.
- The absent default `data/radar.db` was initialized using existing migrations;
  one transaction appended the profiles. No application code changed. No inventory,
  photos, cases or runs were seeded. The private database stays ignored by Git.
- Offline checks exercised 100K/100,001 prompt boundaries including cached input,
  both cache-write durations, reads, microdollar ceiling rounding, full-input
  reservation and unsupported regional/service/tool usage. Haiku's upper-bound
  profile never underestimates the owner's supplied tier schedule. Cache prices
  do not enable caching; full-context cache writes exceed the uncached reservation
  and would need separate reservation review before enabling that request mode.
- The real local preflight accepts Haiku's profile with only the deliberately
  disabled-live blocker; Sonnet remains access-blocked. Reservations are 0.505120
  USD for Haiku and, if separately eligible later, 2.020480 for Sonnet at 2048 output.
  Budget remains 5.00 USD/month with zero accounted spend, and 0/10 daily paid runs.
- **301 tests passed**; Ruff and whitespace checks passed. Only the existing
  Starlette/AnyIO deprecation warning remains. Effective configuration is fixture /
  live disabled. No network request, Messages call, paid request or real-photo
  analysis was made during this configuration. Haiku is ready for one future
  explicitly authorized pilot; no pilot was started.

## Single-request pilot preparation — 2026-10-30

- Re-fetched main; latest Task 005 is still `2e2277673a2da6817e17b46658b1c564e5a70764`.
  Added a local, opt-in `VISION_PILOT_ONE_SHOT` guard within the existing workflow.
  No Task 006 work, deployment, commit, push or merge was performed.
- Pilot mode keeps provider fixture/general live false, requires explicit existing
  model/photo/allowance consent, restricts the model to Haiku QUICK, and checks all
  prior owner Anthropic runs under the atomic reservation transaction. The first
  reservation consumes the pilot across cases/dates/restarts, including failures.
  Sonnet, forced additional attempts and concurrent second calls are blocked.
- **313 tests pass**: all prior 301 plus 12 pilot tests. Ruff and whitespace checks
  pass; only the existing Starlette/AnyIO warning remains. A template closing-tag
  error found by the full suite was fixed before the final successful run.
- Started the actual app on 127.0.0.1:8000, explicitly using the absolute default
  database with the verified Haiku profile and unverified Sonnet profile. A private
  SQLite backup precedes setup; all original rows remain unchanged after startup.
  One neutral owner-requested pilot listing/case was added; no facts, photos or
  runs were invented or seeded. Existing spending limits remain 5 USD/month and
  10/day, with the additional one-attempt guard.
- Four real loopback routes pass Android-sized browser checks: no overflow,
  browser errors or external requests. The model and 0.505120 allowance are visible,
  consent is unchecked, empty-photo submission and Sonnet are disabled, and pilot
  force-rerun controls are absent. Database profiles exactly match dated files;
  zero photos and zero paid runs remain.
- No authenticated phone-preview capability is available. The owner has no private
  workspace preview/VPN and asked about direct conversation upload instead.
  The [pilot procedure](HAIKU-PILOT.md) requires a concrete selected-image review
  and new explicit approval before submitting the local application confirmation.
  No public exposure, real-photo upload/analysis or paid API request occurred.
# Task 005 targeted Haiku failure follow-up — October 30, 2026

- Branch base remains Task 005 merged main `2e2277673a2da6817e17b46658b1c564e5a70764`.
  The prior uncommitted owner-supplied pricing and one-shot pilot work is preserved
  as a separate prerequisite commit; error diagnostics are a separate repair commit.
- Baseline full suite: 313 passed. Repair full suite: **342 passed** (301 Task 005,
  12 pilot safeguards, 29 diagnostic regressions). Ruff and `git diff --check` pass.
  Only the existing Starlette/AnyIO deprecation warning remains.
- Mocked SDK responses cover HTTP 400/401/402/403/413/429/500/504/529, typed billing
  and spend errors, HTML proxy rejection, unknown provider types, credential/schema
  failures before transport, connection/proxy/timeouts, malformed JSON/envelopes,
  Pydantic/photo-ID/truncation rejection, cleanup and outer cancellation deadlines.
  Assertions cover retained metadata, excluded sensitive text, no retries, held
  reservations and consumed one-shot guards.
- Original pre-repair provider replayed entirely offline with MockTransport and
  the existing processing image. Encoding/schema/request construction and synthetic
  response parsing succeeded; this is not remote acceptance or photo analysis.
- Desktop browser fixture workflow and nine Android-sized routes passed: upload,
  zoom, fixture caching, separate deeper fixture, corrections, replacement/removal,
  mixed sub-item review, paid disabled, zero errors/external browser requests.
  A separate GET-only check of the real private owner case confirmed FAILED,
  consumed Haiku guard, Sonnet disabled and no Android overflow.
- Backup plus complete before/after table comparison confirmed all owner rows
  unchanged; private photo hashes unchanged. Still one failed run, no billing
  reconciliation, $0.505120 HELD_UNKNOWN. Original outcome and approval not rewritten.
- App remains bound to 127.0.0.1 with fixture mode/general live disabled; no public
  deployment, additional Anthropic call, credential change, retry or Task 006 work.
- See [diagnosis and evidence limits](HAIKU-FAILURE-DIAGNOSIS.md). No definite root
  cause or billing match is established; end-to-end confirmation remains untested.

## Task 005 real-photo structured-validation repair — October 30, 2026

- Based on merged PR #6 (`8dceffb3beec051e9dcc8143df19c93f419c6951`). Baseline:
  342 tests. Complete repaired suite: **425 passed**, including 83 new synthetic
  regressions. Ruff, formatting and whitespace checks pass; only the existing
  Starlette/AnyIO deprecation warning remains.
- Reproduced cautious-wording false positives, partial-mark punctuation mismatch,
  prompt/enum conflict and insufficient filtering of generic maker-support marks.
  Narrow repairs preserve strict evidence, reference, field and length checks.
  Prompt/schema versions advance to `jewelry-3` / `2` without rewriting old rows.
- Validation errors now retain bounded, allowlisted paths/types and fixed rule
  identifiers, never model text. Successful HTTP envelopes are inspected in memory
  before parsing to capture only safe numeric usage, stop reason and request
  metadata. Mocked HTTP-200 failures remain rejected with the full reservation held.
- Desktop fixture workflows and nine Android-sized routes passed with no errors
  or external browser requests. All browser mutations used a new temporary database
  and synthetic photos on a loopback-only, paid-disabled server.
- All 26 original tables and hashes of 14 private evidence/image files match the
  pre-repair snapshot. Four historical runs remain; both failed $0.505120 holds,
  original authorization histories and photographs are untouched. Budget $5/month,
  daily limit 10, Sonnet restrictions and fixture/live-disabled defaults remain.
- No Anthropic request, billing reconciliation, paid retry, authorization reset,
  new pilot preparation, public deployment or Task 006 work occurred. The historical
  rejected field and exact root cause remain unknown because those details were
  not retained. See [repair and evidence limits](VISION-VALIDATION-REPAIR.md).

### PR #7 final response-buffer safety check — October 30, 2026

- Found that response buffering had no size cap. Added a 2 MiB decoded-body limit
  for successful and error responses, with streamed counting independent of length
  headers, early rejection of advertised oversize and immediate stream closure.
  The SDK's fixed local size failure is classified as `RESPONSE_TOO_LARGE`, not a
  transport failure. No retry or reservation release follows this failure.
- **441 tests pass**, including 16 additional buffer regressions: exact boundary,
  overflow, prebuffered bodies, absent/invalid/lying length headers, gzip expansion,
  error responses, SDK wrapping and retained spending controls. Ruff, formatting
  and whitespace checks pass; the existing Starlette/AnyIO warning remains.
- Original database rows and private-file hashes still match the preservation
  snapshot. No Anthropic API request, jewelry retry, billing modification or new
  development phase was performed. No merge was performed.

## Haiku QUICK 32,768-token allowance — October 30, 2026

- Based on merged PR #7 main `86ffa01956e6beea644466df4524d021bf93b1dd`.
  Raised the reviewed Haiku profile, price-profile schema ceiling and QUICK
  defaults to 32,768 output tokens. The existing Decimal formula now yields
  **$0.581920** with that profile; no budgeting algorithm or rate change was needed.
- **449 tests pass**, including all 441 previous cases and eight new allowance
  regressions. Ruff, formatting and whitespace checks pass. The existing
  Starlette/AnyIO deprecation warning remains.
- Mocked SDK requests differ only in `max_tokens`. Provider source, production
  VisionResult validators, prompt, thinking/effort behavior, response-size safeguard
  and retry policy remain unchanged. The tests cover exact reservation/usage math,
  no double-counting thinking, append-only profile installation, immutable old
  holds, consumed guards, cache separation, stale amount rejection, insufficient
  budget and full reservation retention on failure.
- Compared all 26 actual database tables and hashes of 22 private files with the
  pre-change snapshot: unchanged. No actual database profile was appended during
  development. After merge, use the existing pricing workflow to append the reviewed
  profile before preparing any separately approved request.
- No Anthropic request, retry, new authorization, billing reconciliation, automatic
  merge, thinking optimization or Task 006 work occurred. See
  [output allowance and activation steps](HAIKU-OUTPUT-ALLOWANCE.md).
