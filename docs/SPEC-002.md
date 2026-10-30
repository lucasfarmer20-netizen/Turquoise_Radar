# CODEX TASK 002 — REAL-WORLD SOURCING CALIBRATION

Repository: `lucasfarmer20-netizen/Turquoise_Radar`

## Mission

Upgrade Turquoise Radar from a working synthetic prototype into a practical owner-operated jewelry buying workbench.

Phase 001 is complete with 55 passing tests. Build on the existing code. Do not rewrite the architecture.

The immediate business objective is to help the owner evaluate actual Southwestern jewelry listings quickly and determine:

1. Is the item potentially underpriced?
2. What is the realistic landed cost to Soldotna, Alaska 99669?
3. What could it conservatively net when sold on Whatnot?
4. What is the maximum safe purchase price?
5. Which exact search anomaly revealed it?

Prioritize $15–$25 landed sterling rings, profitable mixed lots, and metal-value opportunities.

Work on a new `task/002-owner-calibration` branch based on updated `main`.

## 1. Financial Engine Corrections — Highest Priority

Inspect the existing `radar/finance.py`, `schemas.py`, and all related tests.

Correct the Whatnot resale financial model.

Whatnot's current U.S. standard commission begins at 8% of item selling price, with category-dependent and sales-volume-dependent tiers.

Payment processing is 2.9% of the total buyer-paid transaction (including item price, buyer-paid shipping and applicable buyer taxes), plus $0.30 per transaction.

These rates must be configurable, date-stamped assumptions rather than permanent hard-coded facts.

Implement:

- Separate commission and payment-processing fee bases.
- Explicit buyer-paid shipping and buyer tax inputs or named scenario assumptions.
- Correct fixed processing-fee treatment.
- Configurable seller-paid outbound shipping and packaging.
- Support for different selling categories and commission tiers.
- An explanation of how each calculated fee was determined.
- Distinction between confirmed amounts, estimates, and unknown amounts.

Never silently assume unknown buyer-paid shipping or tax is zero.

Preserve the existing Decimal calculations and maximum-buy algorithm. Update the maximum-buy solver to respect the revised fee structure.

Calculate conservative profit using the lower supported resale estimate.

Maintain the ability to solve for the highest allowable item price under both profit and ROI constraints.

Add comprehensive regression tests.

## 2. Acquisition Fee and Alaska Shipping Calibration

Extend marketplace buyer profiles to support configurable fee bases, including fees charged on item price plus shipping.

Profiles must represent:

- buyer premium percentage
- service fee percentage and calculation base
- fixed marketplace fees
- sales tax where applicable
- shipping
- handling
- per-lot fees
- per-invoice fees where independently confirmed

Do not assume that any current marketplace fee applies universally or permanently.

Record source, date, and verification status for fee profiles.

Maintain three shipping states:
- CONFIRMED_TO_ALASKA
- CONFIRMED_UNAVAILABLE
- UNKNOWN

Unknown shipping must never produce a high-confidence BUY CANDIDATE.

Do not impose a universal $12 shipping rejection.

Do not assume that different lots can combine shipping without evidence.

For the first version, support manually entered shipping estimates and actual shipping charges.

## 3. Quick Capture — Essential New Feature

The owner currently needs to create structured JSON or complete a detailed form to enter listings. This is too slow for sourcing.

Implement a mobile-friendly Quick Capture interface.

Allow the owner to paste copied listing text, enter a listing URL, and provide a small number of essential fields.

The system should attempt to extract and suggest:

- title
- item price
- shipping cost
- weight in grams or dwt
- piece count
- ring size if available
- material terms such as sterling, 925, plated
- seller and auction identifiers if supplied
- source search phrase
- listing description

Extraction must be deterministic for now.

All extracted values must be editable before saving. Preserve the original pasted text as evidence. Do not treat extracted material terms or weights as independently verified.

Unresolved values remain UNKNOWN.

Do not automatically fetch arbitrary listing URLs or scrape third-party websites.

Do not send pasted content to an AI service.

Quick Capture should work with manually copied content from an estate auction or jewelry marketplace, with minimal typing.

## 4. Real Sourcing Review Workflow

Add a focused work queue that distinguishes:

**NEW:** Recently imported and not reviewed.

**RESEARCH:** Potential bargain with missing material, shipping, condition, or resale evidence.

**WATCH:** Owner wants to follow the listing.

**BUY CANDIDATE:** Sufficient confirmed inputs and favorable projected economics.

**PURCHASED:** Owner confirms an acquisition.

**PASSED:** Owner rejects the opportunity.

Preserve existing feedback and historical data.

Allow the owner to supply or update missing evidence without having to re-enter an entire listing.

For an auction or fixed-price item, prominently show:

- observed asking price/current bid
- estimated full landed cost
- conservative net resale
- expected net profit
- ROI
- maximum safe bid or offer
- missing information
- matched search anomaly

Do not invent auction closing times or bid increments.

## 5. Search Anomaly Intelligence

Preserve the exact `nature america` pattern and all existing typo patterns.

Add a practical comparison view showing search pattern effectiveness:

- original exact phrase
- source
- number of unique imported matches
- owner-reviewed useful matches
- purchased items
- purchase conversion rate where computable
- realized profits where recorded
- false positives
- priority ranking

Keep counts separated by source.

Never treat a search pattern as successful just because it matched generic text in a listing. Distinguish actual source-query attribution from inferred title/description matches.

Add manual entry for newly discovered autocorrect mistakes and misspellings.

Preserve exact spelling and capitalization for display and retrieval.

Do not generate thousands of random typos. Prioritize curated, owner-validated anomalies.

## 6. Whatnot Comparable Sales

Build a lightweight way to record actual resale evidence.

Support importing manually recorded comparable sales with:

- item category
- ring size if applicable
- materials
- weight if known
- condition
- sale price
- selling fees when known
- sale date
- evidence/source
- notes

Separate comparable sales from the owner's purchased inventory.

Add a small summary of comparable price ranges by category, but do not automatically assign a price to every visually similar listing.

Any suggested resale range must show its evidence, sample count and uncertainty.

Require human confirmation before using a comparable-derived value in a BUY CANDIDATE decision.

Historical purchased listings should be linked to realized resale outcomes when available, without inventing missing values.

## 7. Database Safety

Before changing relational database structure, add a minimal migration approach suitable for SQLite.

Do not depend on `create_all()` to modify existing tables.

Test migrations against a populated Phase 001 database copy and a fresh database.

Preserve search patterns, imported listings, original observations, preferences, and feedback.

Do not replace real data with synthetic fixtures.

## 8. Source Evaluation for Phase 003

Update `docs/SOURCES.md` with a grounded evaluation of authorized live acquisition options.

Investigate an official eBay Browse API integration as a candidate for Phase 003.

Record:

- credentials and approval requirements
- keyword query support
- available listing and shipping fields
- polling and rate limits
- search sorting capabilities
- Alaska shipping uncertainty
- costs
- current platform authorization requirements

Evaluate the previously named marketplaces, but do not implement prohibited or unauthorized automated access.

Do not build a live connector in Task 002.

Prepare a precise recommendation for one first live source.

## 9. Testing and Acceptance

Preserve all 55 existing Phase 001 tests.

Add tests covering:

- Whatnot processing on total buyer-paid transaction.
- Commission on item price only.
- Configurable tier rates.
- Unknown buyer-paid shipping/tax handling.
- Fee-base calculations.
- Maximum bid recalculation.
- Real vs estimated shipping.
- Missing evidence.
- Manual listing capture.
- Text extraction with owner confirmation.
- Search attribution vs inferred matches.
- Pattern conversion calculations.
- Database migration and persistence.

Test desktop and mobile layouts.

Use synthetic fixtures for repeatable automated tests; do not claim they are real purchases.

## 10. Out of Scope

Do not implement:

- live marketplace scraping
- browser fingerprint evasion
- CAPTCHA bypass
- paid vision AI
- automated checkout
- Telegram alerts
- public deployment
- subscription features
- unrelated UI redesigns

## 11. Execution and Delivery

Read `AGENTS.md` and existing documentation first.

Implement changes in the existing FastAPI/HTMX application.

Run pytest, Ruff lint, Ruff formatting and applicable browser tests.

Document financial assumptions, limitations, source references, and usage instructions.

Update the roadmap and validation documentation.

Commit the completed code to the task branch and prepare a pull request to `main`.

Do not merge without review.

Report:
- changed files
- financial corrections
- new workflows
- test results
- migration results
- known limitations
- recommended Phase 003 source

**Definition of done:** The owner can paste a real jewelry listing, confirm extracted information, enter known purchase/shipping fees and a supported resale estimate, and immediately see a trustworthy projected profit, ROI and maximum safe buy price, with unknown values clearly identified.

Implement Task 002 now. Do not stop at a plan.