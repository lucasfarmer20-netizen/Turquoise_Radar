# Turquoise Radar

A private, local-first jewelry sourcing workbench for Soldotna, Alaska **99669**.
Phase 004 connects local CSV/JSON and manual entry to exact search anomalies,
Decimal financial analysis, evidence-gated sterling melt analysis, deterministic
ranking, a responsive dark dashboard, and owner feedback. Quick Capture, evidence
editing, a review queue, comparable sales and source-separated anomaly metrics
support real owner-operated sourcing. Official eBay discovery is implemented with
fixture-first operation, gated sandbox/production configuration and manual-only
execution. No live API requests were made during implementation. No
paid AI, purchasing automation, accounts, or background monitoring.

## Run

Python **3.12+** and [uv](https://docs.astral.sh/uv/) are required. Dependencies and
transitive versions are pinned with hashes in `uv.lock`.

```bash
cd /workspace/Turquoise_Radar  # or your local checkout
UV_CACHE_DIR=/tmp/turquoise-uv-cache uv sync --frozen
.venv/bin/python -m radar.cli seed
.venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8000
```

On your own machine, open the dashboard on port 8000. In Codex Cloud, use the
product's supported private access mechanism if available. Do not expose this
unauthenticated application on the public Internet. The app binds to loopback by
default; network access restrictions in a cloud command sandbox may require the
platform's approval flow for running the server and TestClient tests.

The current cloud checkout already has its environment installed and demo seeded.
Processes need restarting after snapshot restoration. No secrets are required.

### Fresh private database without demo data

```bash
export RADAR_DATABASE_URL=sqlite:///./data/private.db
.venv/bin/python -m radar.cli init
.venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8000
```

`.env.example` documents shell configuration; the app does not automatically load
`.env`. SQLite files live in ignored `data/`; retain/back up that directory to
preserve listings, preferences, and feedback. Ordered SQLite migrations initialize
or upgrade the schema; existing populated databases are backed up before v4.
See [migration and calibration details](docs/CALIBRATION.md).

## Demo walkthrough

1. `seed` imports **26 synthetic observations → 25 distinct listings**, including
   one duplicate. Demo records say `DEMO` and `SYNTHETIC`; links use the reserved
   `example.invalid` domain. These are not live inventory or the owner's history.
2. Find **nature america** at the top of Search anomalies and on the first ring's
   detail page. Its canonical/original phrase is `Native American`; the exact
   sent phrase remains `nature america`. No cultural attribution is inferred.
3. The first ring costs $16 + $5 shipping = **$21 landed**. Its manually supplied
   synthetic conservative resale of $45, with demo planning fees, yields
   **$17.79 projected profit** and roughly **84.7% ROI** under the corrected 8%
   synthetic fee scenario with explicitly supplied zero buyer shipping/tax. Maximum item price is
   **$18.79** under both $15 profit and 60% ROI requirements.
4. Use Affordable rings, Melt opportunities, Highest projected margin, or Needs
   review; filter by source, opportunity, cost, confidence, anomaly, and shipping.
5. Open a detail page for each cost component, lower/higher resale scenarios,
   maximum bid, melt evidence, exact/effective query, risks, seller group, raw
   first observation, latest normalized data, and first/last-seen times.
6. Add a manual listing or import `fixtures/example.csv` / `fixtures/demo.json`.
   Record Interested, Purchased, Passed, False positive, or Sold. Realized profit
   is entered manually with Sold; projected profit is never treated as realized.

Restored Phase 001 records keep their original rates and lack the new confirmed
transaction inputs; they appropriately show unknown/unconfirmed economics until
reviewed. Use a separate new demo database for the updated synthetic examples.

Re-seeding is idempotent by identity, but **updates existing demo listing values**.
It preserves first observations, feedback, and existing preferences. Keep demo and
real inventory in separate databases. The seed initializes synthetic spot/auction
settings only when no saved preferences exist.

## Multi-source inbox (Task 004)

Open **Multi-Source Inbox → Import alerts** to upload owner-provided .eml files,
paste notifications or enter structured/copied listings. ShopGoodwill and HiBid
parsers separate search matches, promotions, closing reminders, bidding, wins,
payments and shipment notices. Generic estate sources share safe labelled/link
intake. No marketplace page or mailbox is fetched.

Review original evidence, exact query provenance, duplicates and missing costs;
then send candidates to the existing review queue. **Purchase receipts** reconcile
explicit paid components without inferring ownership from bids or splitting
multi-item shipping equally. **Saved search assistant** copies exact phrases and
records owner-managed setup, while **Seller / auction groups** show known costs
and independently confirmed combined-shipping evidence.

The actual Personal Shopper / HiBid daily digest formats remain provisional.
No real owner emails were supplied for validation; clearly synthetic fixtures
exercise the supported structures. See [alert setup and limitations](docs/ALERT-SETUP.md).
A separate synthetic demo is available with `.venv/bin/python -m radar.intake.demo`;
never seed fixtures into owner inventory.

## Discovery engine (Task 003)

Open **Discovery → Preview search plan → Run discovery**, keeping Fixture mode.
No credentials are required. A fresh default run records 11 synthetic observations
from four exact queries, deduplicating to six identities with five duplicate
observations. Open **Full discovery history**, then **Send to review queue** to use
the existing owner evidence editor. Repeat with KEYWORD to inspect source-reported
correction without replacing the original `nature america` query.

Fixture, sandbox and production identities are separate. Fixture statistics are
non-live test counts, not marketplace results. Production is disabled by default
and requires explicit environment approval/enablement plus individual run consent;
credentials alone never activate it. No recurring monitoring is implemented.
Read [eBay setup, permissions and limitations](docs/EBAY-SETUP.md) before any API use.

## Owner workflow (Task 002)

Use **Quick Capture** to paste copied listing text and review editable suggestions.
Only title, price, shipping and exact query need immediate attention; optional
source/weight/fee details expand on demand. Extraction never fetches a URL or
confirms material/weight independently. Use **Edit evidence & recalculate** to
supply missing acquisition costs, supported resale bounds and buyer transaction
inputs. **Preferences → Calibrate resale fees with a form** configures the dated
resale assumptions without JSON. Review queue states distinguish NEW, RESEARCH,
WATCH, calculated BUY CANDIDATE, PURCHASED and PASSED.

Use **Comparable sales** to record actual manually observed resale evidence and
review category ranges with sample counts and uncertainty. Applying selected
comparables to a listing requires explicit owner confirmation. **Anomaly
intelligence** compares actual source queries and inferred matches separately.
See [the detailed guide](docs/CALIBRATION.md) for confirmation gates and examples.

## Input and import contract

Use **Add / import** for simple forms, advanced listing JSON, file upload, or paste.
The CLI supports `.venv/bin/python -m radar.cli import path/to/listings.json` (or
CSV). JSON is a list of objects. CSV headers are the same field names; nested
`image_urls` use a JSON array in a quoted cell. UTF-8/UTF-8-BOM is supported.

`radar/schemas.py::ListingInput` defines every field. `fixtures/demo.json` has
complete examples. Minimum input is `source` and `raw_title`; unknown optional
fields stay null. In particular, **blank/missing costs are UNKNOWN**, not zero.
Enter zero only when confirmed. Provide both resale bounds and `resale_basis`, or
neither. The resale target band in Preferences never creates a listing estimate.
Manual advanced JSON and CSV/JSON support weights, material evidence, images,
auctions, carrier, category mismatches, seller grouping and attribution evidence.
HTTP(S) links are validated and templates escape seller content.

Deduplication uses case-normalized source + source listing ID. Without an ID,
use the URL without its fragment (query retained because it may identify an
item). Without either, use source + seller + auction + case-folded title and
description. That fallback is conservative but can merge indistinguishable items
or split an edited description; supply a stable source ID whenever possible.
Re-observation retains the first raw payload and updates normalized values and
last-seen. Import is atomic after validation; limited to 1,000 rows / 2 MB.

## Financial and evidence rules

- All financial math uses `Decimal`; SQLite JSON stores decimal strings to avoid
  binary floating-point conversion. Money uses half-up cents; maximum bids are
  solved on integer cents and must satisfy **both** profit and ROI limits.
- Landed = item + premium + service fee + tax + inbound shipping + handling.
  Net resale subtracts selling commission, processing (rate + fixed fee), packaging,
  and seller-paid outbound shipping. Processing applies to item + buyer-paid shipping
  + buyer tax, with the fixed fee charged once per transaction. Profit = net resale − landed; ROI = profit /
  landed. Zero landed cost has undefined ROI.
- **Preferences** edits validated JSON for resale-channel fee profiles,
  marketplace buyer profiles, targets, spot price, refiner payout and evidence
  requirements. Defaults are configurable planning assumptions, **not actual
  current Whatnot or source rates**. Conservative estimates must be supplied.
- For percentage auctions, select a `buyer_profile`, leave calculated fee fields
  null, and confirm the profile. Explicit absolute fee values override profiles
  and are treated as fixed at every proposed bid. Do not enter a percentage
  auction's current premium as a fixed fee when calculating a maximum bid.
  Buyer profiles support item or item-plus-shipping premium/service bases, fixed
  marketplace fees, per-lot fees, and configurable tax bases including invoice.
  Per-invoice fees require independently confirmed allocation. Configure dated
  source evidence and verification status; unknown fee structures are not guessed.
- Only USD comparisons are supported. Non-USD records remain visible but cannot
  receive calculated USD profit, maximum bid, or BUY CANDIDATE status.
- Unknown Alaska availability blocks BUY CANDIDATE; unavailable shipping is PASS.
  Shipping over $12 is never rejected on price alone. Combined shipping is grouped
  by source/seller, but no discount is assumed and no advanced optimization runs.
- Sterling value = net grams × 0.925 × timestamped spot / 31.1034768. Dwt × 1.55517
  gives grams. Conservative liquidation applies configurable refiner payout.
  Gross weight produces only a theoretical upper bound; unverified net weight,
  plated/unknown material or missing weight cannot support melt arbitrage.
- Fresh databases have **no spot price**. Demo seeding uses **$30/troy oz with a
  2026-01-01 timestamp labeled SYNTHETIC SCENARIO, NOT CURRENT SPOT**. Replace it
  with a current manually sourced value before real analysis. No freshness or
  live-price service exists in this phase; inspect its timestamp yourself.
- A user-entered material verification or attribution is a recorded assertion,
  not verification performed by this app. Style, hallmark transcription and
  cultural attribution are separate. Attribution requires documented evidence;
  the app never infers a tribe, maker, mine origin or weight from appearance.

Scoring starts at 10, adds 20 for affordable rings, 40 for conservative resale
meeting both thresholds, 40 for reliable melt protection, 10 for collector review,
and 10 for multi-piece/combined-shipping potential; capped at 100. Critical gaps
cap score at 49. Evidence-qualified resale or melt with complete costs and confirmed
Alaska shipping earns BUY CANDIDATE; other opportunities receive STRONG REVIEW,
RESEARCH NEEDED, or PASS. This is a review aid, not authentication or buying advice.

## Search feedback

Queries are curated, not combinatorially generated. A bounded plan contains at
most 100 exact distinct phrases, preserves the original/transformed phrases,
respects active state and source applicability, and adds no exact-match syntax.
`nature america` is a standalone base-priority 100 autocorrect pattern.
Title/description matching is a fallback only; it never fabricates a query sent.

Appearances count unique imported listings, not source impressions/poll attempts.
Interested counts useful matches once per listing; Purchased counts purchases once.
Sold records user-supplied realized profit; repeat Sold entries adjust totals by
its delta. The feedback audit trail retains all actions. Profit and useful/purchase
feedback add bounded priority adjustments. Passed/False positive are retained for
review; a learned suppression model is deferred. No historical 11-piece purchase
records are fabricated. Disabled queries remain attached to historical listings.
The new effectiveness view separates actual source-query attribution from inferred
title matches, with unique feedback/outcome counts per source. Legacy counters are
preserved on disk but do not boost live query ranking.

## Validate

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

165 tests (including all 128 retained Task 003 cases) cover financial breakdowns, highest feasible auction bids, fees and
missing costs, exact query preservation, deduplication, dwt/melt safeguards,
Alaska gates, mixed lots, atomic CSV/JSON import, settings, filters, manual entry,
feedback counters, escaped output and same-origin mutation protection. All tests
use local fixtures and temporary databases; no live marketplace requests.

HTMX 2.0.8 is vendored with its license in `radar/static/vendor/`; runtime needs
no CDN or paid APIs. Its SHA-256 is
`22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313`.

## Modules and next work

`schemas` / `db`: validation and SQLite persistence; `sources`: adapter contracts,
normalization/import/dedup; `anomalies`: curated patterns and query plans;
`finance` / `melt`: independent Decimal engines; `analysis`: classification;
`main` / templates / static: dashboard and feedback; `vision`: interface only.
`capture` / `owner_routes` / `workflow`: capture, evidence editing and queue;
`comparables`: manual resale evidence; `migrations`: ordered transactional upgrades;
`discovery`: eBay/OAuth, fixture adapter, bounded plans, observations and UI routes;
`intake`: safe owner-message parsing, source registry, reconciliation and multi-source inbox.

See [master specification](docs/SPEC.md), [source capabilities](docs/SOURCES.md),
[roadmap](docs/ROADMAP.md), and [development rules](AGENTS.md).
See [Task 002 usage and financial conventions](docs/CALIBRATION.md),
[Task 003 specification](docs/SPEC-003.md), and [eBay setup](docs/EBAY-SETUP.md).
Local multi-source intake works without API approval. Next, validate the provisional
formats with permitted owner samples and improve auction cost allocations; eBay
sandbox validation still requires secure credentials, access and current docs. Production approval is pending;
no live request, public deployment or automated purchasing is authorized by 003.
