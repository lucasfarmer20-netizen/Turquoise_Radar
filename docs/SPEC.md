# Master specification — Task 001

The following is the owner-supplied Phase 001 scope. Implementation decisions,
financial conventions and known limitations are documented in README.md.

CODEX TASK 001 — TURQUOISE RADAR FOUNDATION

Mission

Build the first working version of Turquoise Radar, a private, modular jewelry sourcing intelligence application designed to discover undervalued vintage Southwestern-style jewelry, Native American-made pieces with supported attribution, Taxco sterling, silver jewelry lots, and inexpensive rings suitable for profitable Whatnot resale.

The business objective is to acquire inventory at the lowest defensible total cost, emphasizing $15–$25 landed rings, undervalued sterling close to melt, multiple pieces from one seller, and high-margin overlooked listings.

Critical competitive insight: The owner previously acquired 11 profitable pieces because a marketplace search autocorrected "Native American" to "nature america", causing valuable listings to be overlooked by other buyers.

Search anomalies, misspellings, autocorrect mistakes, obscure seller descriptions, and category misclassification are first-class product features.

This is not an authentication system, autonomous buyer, or generic shopping search engine.

Implement a working foundation now. Do not stop after generating a design or roadmap.

1. Technical Architecture

Use:

Python 3.12+

FastAPI backend

Jinja2 templates + HTMX

Clean custom dark-mode CSS

SQLite database initially

SQLAlchemy 2.x

Pydantic models for validated input/output

httpx for approved future source integrations

pytest for testing

Ruff for linting

Avoid unnecessary microservices, Redis, Celery, React, external databases, or paid services in Phase 001.

Structure the project with clear separation between:

source ingestion

search anomaly generation

listing normalization

financial analysis

silver/melt calculation

ranking and classification

dashboard

user feedback

future AI vision and alerts

Create AGENTS.md, README.md, .env.example, docs/SPEC.md, and docs/ROADMAP.md.

AGENTS.md should tell future Codex tasks to preserve financial correctness, run tests, document changes, and avoid adding features outside the current task.

2. Search Anomaly Engine — Core Competitive Feature

Create a configurable, testable Python search generation engine.

Include these anomaly classes:

A. Misspellings
Examples:

turquoise → turqoise, turquiose, turquise

sterling → sterlng, stering

Navajo → Navaho, Navjo

Zuni → Zunni

Native American → nature america

B. Autocorrect and semantic substitutions
Maintain exact known mistakes, especially nature america. Support a curated mapping of substitutions discovered through real marketplace use.

C. Generic seller vocabulary
Examples:

old blue stone ring

silver ring blue rock

green stone silver ring

grandma's silver jewelry

old Indian jewelry

heavy silver bracelet

silver jewelry lot

western silver ring

925 blue stone

stamped silver cuff

mystery silver jewelry

D. Missing or incorrect material terminology
Examples:

metal bracelet turquoise

vintage blue stone cuff

silver colored jewelry

old stamped jewelry

E. Category mismatches
Future search integrations must be able to record and evaluate listings appearing in unusual categories, including costume jewelry and miscellaneous estate lots.

Maintain a search_patterns dataset with:

pattern ID

exact search phrase

canonical concept

anomaly category

source applicability

active/disabled state

priority weight

appearances

useful matches

successful purchases

realized profit

Mandatory behavior:

Never silently correct the exact phrase sent to a source.

Preserve both original and transformed search phrases.

Record the actual/effective query when a source reveals it.

Do not assume a source supports exact-match syntax.

Support manual addition of successful anomalous queries.

Prevent uncontrolled combinatorial query generation.

Keep business-priority searches above random generated typos.

Record feedback so profitable searches can rank higher over time.

Build unit tests explicitly verifying that nature america is retained as a high-priority standalone search pattern.

Do not fabricate the owner's historical 11 purchase records.

3. Listing Acquisition Architecture

Create a SourceAdapter interface with capability metadata and a normalized output contract.

The eventual source roster should support evaluation of:

Poshmark

Mercari

EstateSales.net

EstateSales.org

CTBids

Additional supported estate and secondhand sources

Do not assume these platforms provide public APIs or unrestricted automated access.

For Phase 001, implement:

A working CSV/JSON fixture importer.

Manual listing entry.

A modular adapter interface for future permitted automated retrieval.

Source capability documentation.

Use approved source APIs, feeds, notifications, and other authorized access methods where available. Do not implement CAPTCHA bypass, proxy rotation, browser fingerprint evasion, session theft, or anti-bot circumvention. Do not automate purchases.

Future adapters should support independent polling schedules, rate limits, retry behavior, and disable switches.

Normalize these fields:

source

source listing ID

listing URL

seller ID

sale/auction ID

raw title

raw description

item price

currency

buyer premium

buyer service fee

applicable tax

shipping cost

handling fee

destination shipping eligibility

carrier information

condition

image URLs

stated weight

weight unit

weight evidence/source

hallmark text if available

piece count

listing time

search pattern ID

raw search phrase

first-seen time

last-seen time

Deduplicate on source plus source listing ID, with a documented fallback for missing identifiers.

Preserve original data alongside normalized values.

4. Alaska Acquisition Constraints

Shipping destination: 99669, Soldotna, Alaska.

Do not assume lower-48 shipping prices apply.

Each item must have a shipping status:

CONFIRMED_TO_ALASKA

CONFIRMED_UNAVAILABLE

UNKNOWN

Unknown shipping is not an automatic rejection, but prevents a high-confidence BUY NOW recommendation.

Do not universally reject shipping above $12.

For example, $15 shipping on a 100g sterling lot may be worthwhile, while $8 shipping on a $24 ring may destroy the margin.

Support combined shipping and seller-level grouping in the data model, but postpone advanced lot optimization to a later task.

5. Financial Engine

Financial calculations must be deterministic, inspectable, and independent from AI-generated opinions.

Use Python Decimal for currency calculations.

Implement configurable fee profiles by marketplace and resale channel. Do not hard-code current platform rates as permanent truths.

Calculate:

landed_cost = item_price + buyer_premium + buyer_service_fee + sales_tax + inbound_shipping + handling

net_resale = expected_sale_price - selling_commission - payment_processing - packaging - seller_paid_outbound_shipping

expected_profit = net_resale - landed_cost

ROI = expected_profit / landed_cost

Support lower and higher resale scenarios.

Missing costs must be explicitly labeled UNKNOWN, not treated as zero.

Support configurable acquisition preferences:

Ring target landed cost: $15–$25

Target resale band: $35–$60

Minimum projected profit: $15

Minimum projected ROI: 60%

These are editable starting preferences, not universal hard rejection rules. High-value collector or melt opportunities may qualify outside them.

Never invent a resale estimate merely because a piece looks valuable. Phase 001 may use manually supplied resale ranges or clearly labeled configurable category assumptions.

Maximum Buy Calculation

Implement a max_buy_price function.

Given conservative resale value, seller fees, shipping, buyer premium, handling, target profit, and required ROI, calculate the highest item price or auction bid satisfying BOTH minimum profit and ROI.

Use numerical solving if fee structures require it.

Return:

maximum item price

expected landed cost at maximum price

projected profit

projected ROI

explanation of limiting constraint

Include tests for auction buyer premiums, per-lot fees, and missing shipping estimates.

6. Silver Melt Engine

Do not use a permanently fixed silver price.

Support manually configured silver spot price in USD per troy ounce, with timestamp and source. Allow a future live-price provider without changing the calculation layer.

Use:

sterling_metal_value = net_silver_weight_g × 0.925 × spot_usd_per_troy_oz / 31.1034768

estimated_liquidation_value = sterling_metal_value × configured_refiner_payout_rate

Recognize seller-provided weights in grams and pennyweights; convert dwt to grams using approximately 1.55517.

Critical safeguards:

Total weight of turquoise-set jewelry is not automatically silver weight.

Gemstones, adhesives and other non-silver components must not be valued as sterling.

If only gross weight is known, show theoretical upper-bound melt with a clear warning.

If weight is unknown, never create a numeric melt-arbitrage recommendation from AI-estimated weight.

If sterling purity is uncertain, distinguish seller claims from verified evidence.

No definitive priority melt recommendation unless weight and material evidence meet configured confidence requirements.

Provide separate tracking for:

theoretical metal value

conservative liquidation value

landed-to-melt ratio

melt confidence

7. Deal Classification

Support five independent opportunities:

RING RESTOCK: Affordable wearable rings, especially $15–$25 landed.

STERLING / MELT: Jewelry with strong precious-metal downside protection.

WHATNOT FLIP: Pieces with favorable resale economics.

COLLECTOR UPSIDE: Potential maker, hallmark, construction or provenance premium.

MULTI-LOT: Opportunities that benefit from buying multiple pieces from one seller or auction.

Use deterministic scoring initially.

Each scored listing should include:

overall priority score 0–100

opportunity categories

projected landed cost

estimated resale range when supported

projected profit and ROI

maximum buy price when calculable

confidence level

evidence supporting the recommendation

unresolved risks

Recommendation tiers:

BUY CANDIDATE

STRONG REVIEW

RESEARCH NEEDED

PASS

Never give BUY CANDIDATE status where critical shipping or financial information is unknown.

Do not label jewelry as authentic Native American, Navajo, Zuni or Hopi without sufficient maker or provenance evidence. Descriptive Southwestern style and verified cultural attribution are separate fields.

8. Dark Dashboard

Create a functional desktop-friendly and mobile-responsive interface.

The main dashboard should display:

Recent candidate listings

Highest-priority anomalies

Affordable ring candidates

Melt opportunities

Highest projected margin

Listings needing review

Every listing card should show:

title

source

observed price

shipping status

landed cost if known

net profit projection if supported

ROI if supported

maximum buy price if supported

matched anomaly/search phrase

recommendation tier

listing link

Use clear labels for missing or estimated information.

Add filtering by:

source

opportunity type

cost range

confidence

anomaly class

shipping status

Create listing detail pages exposing the full financial breakdown and the exact original search phrase.

Add user feedback actions:

Interested

Purchased

Passed

False positive

Sold

Create simple manual entry/import and feedback interfaces.

Do not build user accounts or subscription billing. This is initially a single-owner application.

9. Future Vision Integration

Define a stub/interface only. Do not call paid AI APIs during this phase.

Future capabilities:

analyze individual photographs

detect visible hallmark text

recognize construction and stone appearance

identify visual factory-production warning signs

evaluate possible maker attribution with evidence

assign uncertainty

The future vision model may identify candidates but must not claim tribal authenticity, stone mine origin, or precise weight from appearance alone.

Keep images at useful resolution for hallmarks, rather than permanently reducing four photographs into a low-resolution composite.

10. Testing and Demo Data

Provide at least 25 clearly synthetic sample listings spanning:

typo bargains including nature america

cheap sterling ring opportunities

generic blue-stone listings

potentially profitable silver lots

signed jewelry requiring review

overpriced pieces

silver-plated false positives

unknown shipping

expensive handling fees

auctions with buyer premiums

stone-heavy pieces where gross weight overstates metal

combined-shipping candidates

duplicate listings

Demonstrate the entire pipeline:

import → normalize → anomaly matching → financial calculation → scoring → dashboard

Required tests include:

exact anomaly preservation

query deduplication

source listing deduplication

pennyweight conversion

melt math

shipping-to-Alaska unknown handling

maximum bid calculation

buyer premium application

no invented resale range

no melt arbitrage without reliable metal weight

no BUY CANDIDATE if essential costs are unknown

mixed jewelry lots not discarded solely because one negative keyword appears

dashboard and import routes

Use test fixtures, not live marketplace requests.

11. Explicitly Out of Scope for Task 001

Do not build:

production scrapers

anti-bot bypass

paid AI vision calls

automated checkout

Telegram/Discord alerts

live spot-price retrieval

background 24/7 monitoring

user authentication

subscription billing

complicated deployment infrastructure

These belong to future phases after the local/demo pipeline passes testing.

12. Execution Requirements

Inspect the repository.

Write AGENTS.md, the master specification, and the phased roadmap.

Implement the working application and the defined Phase 001 scope.

Install project dependencies using an appropriate reproducible configuration.

Seed clearly synthetic demo listings.

Run all tests and lint checks.

Fix failures before delivering.

Include clear run commands in the README.

Summarize implemented features, test results, known limitations, and recommended next task.

Make changes available for review in Codex Cloud; offer a pull request where supported.

If repository setup or a dependency presents a problem, select the simplest workable solution and document it. Do not halt for cosmetic decisions.

Definition of done: The owner can run the FastAPI app, open the dashboard, see 25 sample listings, identify the nature america anomaly, inspect real calculated costs and profits, view maximum buy prices for supported cases, import new manual listings, and record purchasing feedback.

Build Phase 001 now.

## Acceptance evidence

See tests/ for executable acceptance checks and fixtures/ for synthetic inputs.
Remote sources and vision remain interfaces only. No historical purchases are seeded.
