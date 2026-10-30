# Task 002 owner calibration

## First use

The existing FastAPI/HTMX/SQLite architecture is retained. Run the commands in
README, then use **Quick Capture**. The server never fetches the pasted URL or
sends copied content to an AI service.

1. Paste listing text and optionally its URL. Review suggestions for title, price,
   shipping, grams/dwt, gross weight, count, ring size, material vocabulary, seller,
   auction/listing IDs and exact query. Every suggestion is editable. The first
   pasted text is retained separately as evidence. Extracted material is at most
   a seller claim; inferred weight is unverified gross weight, never net silver.
2. Confirm the extraction, then use **Edit evidence & recalculate** on the detail
   page. Enter actual or estimated costs, Alaska availability, condition/material
   evidence, and a supported lower/higher resale range. Set status explicitly;
   confirming the capture alone does not authenticate material or confirm charges.
3. Once per resale channel, use **Preferences → Calibrate resale fees with a form**.
   Enter commission, processing rate and fixed fee, packaging, seller-paid outbound
   shipping, source, date and verification status. Advanced Preferences JSON
   retains multiple resale/buyer profiles, named scenarios and category/tier rates.
4. Enter buyer-paid resale shipping and tax explicitly, or define a **named**
   profile scenario. Unknown values are never silently zero. Lower and higher
   sale prices share those explicitly supplied shipping/tax dollar scenarios;
   enter conservative amounts that cover your expected range. Tax is not inferred
   from a buyer location or a listing photograph.
5. Confirm the resale evidence and scenario before using it for a buying decision.
   Numerical projections and maximum bids can be inspected with estimated inputs,
   but estimates and missing material/condition evidence block high-confidence
   BUY CANDIDATE. A resale range is always a projection, never a guaranteed sale.

## Corrected Whatnot fees

Task 002 supplies the initial US standard planning assumption: **8% commission**
on item selling price, and **2.9% processing on item + buyer-paid shipping + buyer
sales tax**, plus **$0.30 once per resale transaction**. Commission does not charge
on buyer shipping/tax. Buyer shipping and taxes are not added to seller revenue.
Packaging and seller-paid outbound shipping are subtracted separately. This models
one completed resale transaction; selling a lot as multiple separate transactions
requires separate scenarios rather than sharing one fixed fee.

Rates are stored with source, as-of date and CONFIRMED / ESTIMATED / UNKNOWN status.
The starting reference is **owner-provided Task 002, 2026-10-29**, not an assertion
that official documentation was freshly verified. The official Whatnot help page
was blocked by the cloud egress policy during this task. Verify category, location,
volume eligibility and current terms before marking a profile confirmed.

`commission_tiers` maps explicit `category:tier` keys to rates, e.g.
`{"jewelry:volume": "0.06"}` is a **hypothetical configuration example**, not a
claim that Whatnot offers this exact tier. A listing selects category and tier;
standard falls back to the profile's standard commission, while an unknown named
tier yields UNKNOWN. The app never guesses seller volume or eligibility.

`scenario()` exposes commission/processing bases, applied rates, component fees,
source/date, missing inputs and explanations. The maximum-buy solver uses that
same conservative net resale and recalculates purchase fees at every integer-cent
candidate. It returns the greatest cent satisfying both minimum profit and ROI.

## Acquisition profiles and Alaska

`BuyerProfile` supports these fields in Preferences:

| Field | Meaning |
|---|---|
| `premium_rate`, `premium_base` | Item or item-plus-inbound-shipping percentage |
| `service_rate`, `service_base` | Item or item-plus-inbound-shipping percentage |
| `fixed_marketplace_fee` | Fixed charge included in buyer service fees |
| `per_lot_fee` | Fixed fee per listing/lot, not per piece |
| `tax_rate`, `tax_base` | `item`, `item_shipping`, `item_premium_service`, or full `invoice` |
| `shipping`, `handling` | Explicit profile amounts, optional; no universal shipping default |
| `per_invoice_fee` | Independently confirmed invoice fee; requires confirmed profile metadata |
| `source`, `as_of`, `verification_status` | Evidence and calibration status |

Full invoice tax base is item + shipping + handling + premium + service fees.
Premium/service bases exclude other fees to avoid circular fee calculation.
Profile-derived values remain estimated unless the profile is confirmed. Explicit
listing amounts override profile calculations, including at every proposed bid;
use rate profiles, rather than a current absolute premium/tax, for a percentage
auction maximum-bid calculation. The detail page explains all bases.

Per-invoice fees require the owner to enter the listing's
`invoice_fee_allocation`, `invoice_allocation_confirmed=true` and allocation
source/evidence. A missing allocation prevents a complete landed-cost estimate;
the app does not assign an invoice fee to every lot or invent combined-shipping
savings. This is manual allocation, not a multi-lot optimizer.

All amount values are Decimal strings; absent/null is UNKNOWN. Provided numeric
amounts without status are conservatively ESTIMATED. Explicit UNKNOWN acquisition
status suppresses use of that amount. Existing Alaska shipping states remain:
CONFIRMED_TO_ALASKA, CONFIRMED_UNAVAILABLE, UNKNOWN. Only confirmed availability and
complete confirmed acquisition costs can support BUY CANDIDATE. No universal $12
shipping cutoff exists. USD is the only supported comparison currency.

## Review queue and evidence history

- NEW: not reviewed, including existing records without a prior decision.
- RESEARCH: owner reviewed but missing evidence or confirmed economics.
- WATCH: Interested feedback or explicitly chosen watch state.
- BUY CANDIDATE: calculated from reviewed RESEARCH records meeting the gates.
- PURCHASED: recorded Purchased feedback; later Sold retains acquisition history.
- PASSED: Passed / False positive feedback.

The owner cannot directly force BUY CANDIDATE through a state endpoint. Evidence
edits retain before/after snapshots in `listing_edits`; the first original payload
and first pasted text are retained. Re-imports update supplied observations while
preserving omitted owner evidence and stored workflow state. No auction closing
times or bid increments are fabricated.

## Search effectiveness

**Anomaly intelligence** groups unique listings by pattern, source and attribution.
SOURCE QUERY requires the raw exact query to equal the curated phrase byte for
byte. A supplied pattern ID or a matching title alone is INFERRED. Useful matches,
purchases, false positives and latest user-recorded realized profits derive from
feedback; repeated milestones count once per listing. Conversion is purchased /
unique imported matches, never a claim about all source impressions. Unrecorded
profit stays UNKNOWN; source groups and inferred observations remain separate.

New inferred observations/feedback do not increment a query's success counters.
Legacy aggregate fields remain preserved; the source-separated comparison view
reconstructs accurate attribution from available historical listing/feedback data.
The comparison priority uses bounded useful/purchase/profit adjustments only for
SOURCE QUERY. Dashboard, pattern management and query plans reconstruct their priority from
actual source-query histories as well; legacy stored counters cannot inflate them.
Manual pattern entry preserves exact case/spelling, including `nature america`.

## Comparable sales and realized outcomes

**Comparable sales** accepts a manual form or pasted CSV/JSON with category, size,
material, optional weight, condition, actual sale price, optional known fees,
sale date, evidence and notes. A `listing_id` links an outcome only after that item
has Purchased feedback. A separate comparable record never implies ownership.
Missing fees and realized profits are not invented or replaced by projected profit.

Category summaries show observed min/max, n, evidence and uncertainty; synthetic
records are summarized separately. No listing automatically receives those prices.
Select comparables in the evidence editor, inspect size/condition/category, then
explicitly confirm before applying their range. Every selected ID and evidence
basis is retained. Synthetic comparable evidence cannot price a real listing.
No fuzzy visual matching or statistical appraisal is performed. Re-imported
comparables are separate observations; avoid importing the same sale twice.

## SQLite migration safety

Startup calls ordered transactional migrations using SQLite `PRAGMA user_version`:
v1 initializes the Phase 001 schema, and v2 adds workflow state, comparable sales
and evidence-edit history. `create_all()` is not used to alter existing tables.
A populated existing database receives a SQLite backup beside it as
`<database>.pre-v2.bak` before migration. Backup failure prevents migration. Keep
that backup, and separately back up `data/` before upgrades. Future schema versions
are refused; failed migration statements roll back. Stop all app writers and run
one migration/startup process at a time.

Existing listings, source IDs, raw/original observations, dates, patterns, feedback
and preferences are preserved. Prior decisions initialize review state without
inventing new feedback. Stored Phase 001 rates are not silently changed to 8%;
they appear as legacy unverified assumptions until the owner recalibrates. Existing
numeric costs without status are estimates. New resale transaction amounts are
unknown. Do not seed synthetic fixtures into a real database.
