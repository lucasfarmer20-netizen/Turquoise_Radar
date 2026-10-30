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
