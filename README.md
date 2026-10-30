# Turquoise Radar

A private, local-first jewelry sourcing workbench for Soldotna, Alaska **99669**.
Phase 001 connects local CSV/JSON and manual entry to exact search anomalies,
Decimal financial analysis, evidence-gated sterling melt analysis, deterministic
ranking, a responsive dark dashboard, and owner feedback. No live marketplaces,
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
preserve listings, preferences, and feedback. `create_all` initializes this first
schema; a migration tool is deferred until the schema changes.

## Demo walkthrough

1. `seed` imports **26 synthetic observations → 25 distinct listings**, including
   one duplicate. Demo records say `DEMO` and `SYNTHETIC`; links use the reserved
   `example.invalid` domain. These are not live inventory or the owner's history.
2. Find **nature america** at the top of Search anomalies and on the first ring's
   detail page. Its canonical/original phrase is `Native American`; the exact
   sent phrase remains `nature america`. No cultural attribution is inferred.
3. The first ring costs $16 + $5 shipping = **$21 landed**. Its manually supplied
   synthetic conservative resale of $45, with demo planning fees, yields
   **$16.89 projected profit** and roughly **80.4% ROI**. Maximum item price is
   **$17.89** under both $15 profit and 60% ROI requirements.
4. Use Affordable rings, Melt opportunities, Highest projected margin, or Needs
   review; filter by source, opportunity, cost, confidence, anomaly, and shipping.
5. Open a detail page for each cost component, lower/higher resale scenarios,
   maximum bid, melt evidence, exact/effective query, risks, seller group, raw
   first observation, latest normalized data, and first/last-seen times.
6. Add a manual listing or import `fixtures/example.csv` / `fixtures/demo.json`.
   Record Interested, Purchased, Passed, False positive, or Sold. Realized profit
   is entered manually with Sold; projected profit is never treated as realized.

Re-seeding is idempotent by identity, but **updates existing demo listing values**.
It preserves first observations, feedback, and existing preferences. Keep demo and
real inventory in separate databases. The seed initializes synthetic spot/auction
settings only when no saved preferences exist.

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
  and seller-paid outbound shipping. Profit = net resale − landed; ROI = profit /
  landed. Zero landed cost has undefined ROI.
- **Preferences** edits validated JSON for resale-channel fee profiles,
  marketplace buyer profiles, targets, spot price, refiner payout and evidence
  requirements. Defaults are configurable planning assumptions, **not actual
  current Whatnot or source rates**. Conservative estimates must be supplied.
- For percentage auctions, select a `buyer_profile`, leave calculated fee fields
  null, and confirm the profile. Explicit absolute fee values override profiles
  and are treated as fixed at every proposed bid. Do not enter a percentage
  auction's current premium as a fixed fee when calculating a maximum bid.
  Profiles compute premium on price, service rate on price plus a per-lot fee,
  and tax on price + premium + service. Shipping/handling taxability varies;
  enter confirmed absolute tax when this simple base is unsuitable. More complex
  tiered/tax structures are a future extension, not silently approximated.
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

## Validate

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

55 tests cover financial breakdowns, highest feasible auction bids, fees and
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

See [master specification](docs/SPEC.md), [source capabilities](docs/SOURCES.md),
[roadmap](docs/ROADMAP.md), and [development rules](AGENTS.md).
The recommended next task is to validate fee/tax structures and manually observed
resale/evidence inputs against real owner examples before selecting one authorized
source integration. No production scrapers or third-party APIs are implemented.
