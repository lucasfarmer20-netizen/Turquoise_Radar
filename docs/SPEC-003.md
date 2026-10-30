# CODEX TASK 003 — TURQUOISE RADAR DISCOVERY ENGINE

Repository: `lucasfarmer20-netizen/Turquoise_Radar`

Base branch: latest `main` after Task 002.

Create branch: `task/003-ebay-discovery`.

## Mission

Build Turquoise Radar's first marketplace discovery engine using the official eBay Browse API architecture.

The owner's eBay developer application is awaiting final approval. Implement and test the integration without requiring production access. Keep production calls disabled until explicitly authorized.

The goal is to discover underpriced Southwestern-style and sterling jewelry using misspellings, vocabulary gaps, poorly described items, category mistakes, and search anomalies.

Priority inventory:
- $15–$25 landed sterling rings.
- Rings with realistic resale potential of $35–$60.
- Sterling jewelry lots priced near metal value.
- Underpriced turquoise and coral jewelry.
- Mixed estate lots containing overlooked sterling pieces.
- Signed or potentially collectible Southwestern jewelry requiring research.

## 1. Preserve Existing Architecture

Read AGENTS.md, README.md, CALIBRATION.md, SOURCES.md, and existing tests.

Build on Phase 001 and 002.

Keep FastAPI, HTMX, SQLite, SQLAlchemy, Decimal financial calculations, and current review workflows.

Do not redesign the application or introduce unrelated infrastructure.

## 2. Official eBay Source Adapter

Implement an eBay Browse API adapter using its documented endpoints and OAuth client-credentials flow.

Support:
- Sandbox configuration.
- Production configuration gated by explicit approval and configuration.
- Secure environment-based credentials.
- HTTP timeout, rate-limit handling and bounded retry/backoff.
- Pagination within configured limits.
- Search by keyword.
- Fixed-price and auction search modes where documented.
- Price range filtering.
- Newly listed sorting if supported.
- A configurable maximum number of queries/results per run.

Use the existing SourceAdapter interface or extend it minimally.

Never store credentials in GitHub, SQLite, logs, or browser-visible responses.

Use official API endpoints only. No HTML scraping, undocumented endpoints, browser impersonation or anti-bot bypass.

If credentials or authorization are unavailable, the application must still run with fixture-based discovery.

Do not execute production requests during implementation.

## 3. Search Anomaly Execution — Highest Priority

Connect the existing curated anomaly engine to the new adapter.

The engine must preserve exact query strings including:

- nature america
- turqoise
- turquiose
- turquise
- sterlng
- Navaho
- Navjo
- old blue stone ring
- silver ring blue rock
- green stone silver ring
- silver jewelry lot
- old stamped jewelry
- 925 blue stone
- western silver ring

Add a bounded set of useful variants if justified.

For each search execution, record:
- source
- exact original query
- exact transmitted query
- source-reported correction, if available
- search parameters
- results returned
- unique listings identified
- execution timestamp
- errors
- search mode

**Important eBay detail:** The documented `auto_correct` parameter accepts `KEYWORD` to enable keyword correction. Do not invent an `OFF` value. Support experiments with the parameter omitted versus explicitly enabled.

If eBay returns an automatic correction, record it. Never replace the transmitted query in our records.

Distinguish:
- Exact anomalous query returned results.
- Source corrected the query.
- Source correction behavior unknown.

Do not assume omitting the option guarantees literal exact-match behavior.

Preserve search attribution when the same listing appears under multiple queries.

A listing discovered under several patterns must be deduplicated without losing the independent discovery history.

## 4. Search Strategy

Implement three configurable search groups:

**A. Cheap Ring Hunter**

Focus on generic sterling, blue/green-stone rings, misspellings and affordable listings.

**B. Silver Lot Hunter**

Focus on sterling lots, jewelry jars, heavy silver pieces, bulk estate jewelry and miscellaneous silver lots.

**C. Southwestern Anomaly Hunter**

Focus on Navajo/Zuni/Hopi-associated vocabulary variants, Southwestern designs, generic descriptions, and seller spelling mistakes.

These are discovery categories, not authentication claims.

Provide editable query groups and a small default query budget. Do not expand into thousands of generated phrases.

## 5. Listing Normalization

Map documented API result fields into existing ListingInput records.

Preserve:
- eBay item ID
- original title
- item URL
- price and currency
- seller identifier where returned
- buying options
- condition
- category
- image URL
- listing time when supplied
- end time when supplied
- source search phrase
- source correction
- shipping evidence
- original API observation

Do not fabricate missing fields.

For shipping to 99669, retain UNKNOWN unless an authorized response explicitly establishes eligibility and the amount.

Do not assume shipping shown for a generic U.S. destination applies to Alaska.

Do not assign resale prices automatically.

All discovery data must pass through the existing financial and review pipeline.

## 6. Discovery Dashboard

Add a focused Discovery page to the current application.

It should show:
- Source connection status.
- Fixture / Sandbox / Production mode.
- Latest completed search.
- Number of new and duplicate listings.
- Most productive anomaly queries.
- Affordable ring candidates.
- Silver lot candidates.
- Listings with missing shipping or resale evidence.

For each listing show:
- image when provided
- original title
- source price
- exact discovery query
- correction indicator
- buying format
- Alaska shipping status
- available calculated margin
- reason it was surfaced
- direct listing link

Allow the owner to move a listing into the existing review queue and use the Phase 002 evidence editor.

Clearly label sandbox and fixture results as non-live.

No fake live prices, synthetic marketplace success statistics, or unsupported BUY CANDIDATE labels.

## 7. Search Execution Controls

Implement:
- Manual Run Discovery action for approved modes.
- Preview Search Plan action.
- Enable/disable individual search groups.
- Maximum result count per run.
- A clearly visible production-disabled state.
- Last run time and errors.
- Duplicate listing counts.

Fixture mode must work without credentials.

Sandbox mode may be used only with configured credentials and permitted access.

Production mode must require explicit approval/configuration and must never automatically activate merely because credentials are present.

Do not implement recurring autonomous monitoring yet.

## 8. API Testing

Use documented response structures and checked-in synthetic test fixtures.

Test:
- OAuth token handling with mocked responses.
- Rate limiting and Retry-After.
- 401/403 permission failures.
- Malformed responses.
- Pagination.
- Auction versus fixed-price filtering.
- Search query preservation.
- `nature america` exact query attribution.
- Auto-correction response handling.
- Multi-query listing deduplication.
- Unknown Alaska shipping.
- Unknown resale prices.
- Existing cost and maximum-buy calculations.
- Source-disabled behavior.
- Import into the existing review queue.

Do not make production API requests in automated tests.

Do not claim sandbox fixtures reflect current eBay inventory.

## 9. Documentation

Update:
- README.md
- AGENTS.md if needed
- docs/SOURCES.md
- docs/ROADMAP.md
- docs/VALIDATION.md

Add `docs/EBAY-SETUP.md` covering:

- Developer account and production approval prerequisites.
- Environment variables and secure credential configuration.
- Sandbox testing.
- How to run discovery in fixture mode.
- How to activate production after authorization.
- Expected API limitations.
- Query auto-correction behavior.
- Alaska shipping caveats.
- Supported search formats and sorting.
- Verified quotas versus unknown quotas.

Verify official documentation when accessible. Do not invent quotas or approval status.

## 10. Scope Guardrails

Do not build:
- Automated purchasing.
- Live production polling.
- Continuous 24/7 workers.
- Paid AI vision.
- Telegram notifications.
- Proxies or fingerprint evasion.
- Additional marketplace adapters.
- User accounts.
- A new frontend framework.
- Unrelated UI redesigns.

Keep this task focused on a fully testable eBay discovery pipeline.

## 11. Completion Requirements

Run the complete existing test suite plus new tests.

Run Ruff lint and formatting checks.

Test desktop and mobile discovery workflows.

Verify SQLite migrations preserve existing Phase 002 data.

Commit changes to `task/003-ebay-discovery` and prepare a PR targeting main.

Do not merge the PR automatically.

Report:
- Implemented functionality.
- Tests passed.
- Fixture discovery walkthrough.
- Exact source permissions needed.
- Any unavailable API features.
- Production activation checklist.
- Remaining blockers.

**Definition of done:** The owner can open Turquoise Radar, run a fixture-based discovery session, see candidate jewelry listings collected from multiple exact typo queries, inspect how each item was discovered, deduplicate them, and send promising items into the existing buying review workflow.

The adapter must be ready for authorized eBay API testing when access becomes available, without rewriting the financial or discovery system.

Implement Task 003 now.