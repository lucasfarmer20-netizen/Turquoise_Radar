# MVP source audit — 2026-11-09

Scope: inspected repository code at base 056e9b34269960c143da456c5f3d522229e036ba and the narrow card-label patch. This is a capability audit, not verification of current marketplace permissions, local owner data, current listings, closing times, or a successful multi-source live run.

## Current product direction

The owner requires unattended discovery and qualification. The manual comparison
below is historical validation, not the product end state. See
[AUTOMATED-BUYER-MVP.md](AUTOMATED-BUYER-MVP.md) for the current build path.

## Finding

Radar has one live search adapter (official eBay Browse/OAuth) and nine named alternative-marketplace intake definitions plus a custom-source placeholder. That is enough structure for a two-source owner-reviewed comparison, but not evidence of nine working search integrations or sufficient buying opportunities. Source count alone cannot establish useful coverage.

| Source | Implemented access | Qualification still needed for a fresh test |
| --- | --- | --- |
| eBay | Bounded official API adapter, BIN/auction filters, exact query history, identity hydration, local photos and deletion maintenance | Actual current run result, current shipping/availability, applicable fees and conservative resale evidence. Existing tiny cohort cannot be expanded/reset. |
| ShopGoodwill | Owner email/text/structured intake, Personal Shopper parser | Current permitted sample, verified format/query attribution, live lot close, shipping/handling to99669 |
| HiBid | Owner email/text/structured intake, saved-search parser | Current permitted sample; auction-house-specific premium/tax/shipping and soft-close evidence |
| Invaluable | Owner email parsers for saved searches, outbid and spotlight structures | Current verified sample and lot/house terms; promotions are not exact search matches |
| LiveAuctioneers | Owner email parsers for outbid/ended/recommendation structures | Search-alert capability is not established by these formats; current active lot/house terms required |
| EstateSales.org / EstateSales.net / CTBids | Registry plus generic owner-supplied intake | Permitted current sample, source-specific fields, item-level purchase/closing/shipping terms |
| Poshmark / Mercari | Owner text/CSV/JSON/manual intake | Authorized discovery route, current offer, shipping eligibility and cost; no live search adapter |

Source evidence: radar/sources.py (disabled alternative adapter roster), radar/discovery/ebay.py, radar/intake/registry.py, search_email.py, auction_email.py and parsers.py. No account creation, scraping, mailbox connection or extra marketplace requests occurred in this audit.

## Existing tools worth using now

- /sources records domains, authorized methods, unresolved restrictions, sample evidence, reviewed formats and fee profile. Custom source rules require an explicitly verified referenced sample. Adding a row does not activate access.
- /saved-searches organizes exact phrases and counts real, verified search matches; synthetic, unverified and recommendation-only results do not earn matching credit (radar/intake/views.py:search_status).
- /search-catalog stages disabled exact-query drafts; it does not discover new platforms or launch searches (radar/sourcing_catalog.py).
- /feed/evaluation already compares real retained source inventory, attributed source/query observations, missing-cost and unknown/unavailable-shipping counts, and latest owner feedback; it also proposes bounded vocabulary. It explicitly does not establish complete search yield, HTTP dispatch statistics or profitability (radar/search_evaluation.py). This is the existing qualification aid to use before building another tool.
- Existing financial analysis uses low resale estimates, landed costs, buyer fees/tax/shipping, resale fees, minimum profit/ROI and unknown-cost warnings. Alaska delivery is not inferred from a US location hint (radar/analysis.py, radar/finance.py).

## Smallest useful next test

Use eBay plus ONE alternative with a current, permitted owner-supplied search alert or item sample, preferably a source already having a tested parser. Import the second source through its existing intake workflow into a separate evaluation workspace, not the immutable three-seller eBay pilot inventory. Compare at most two candidates per source using the same exact phrase where that source can support it. Do not claim current search results from an old email or recommendation.

For each candidate record source observation time, listing URL, sale format, verified deadline/timezone and soft-close terms, observed price/next bid, buyer premium, tax, Alaska shipping/handling, conservative resale evidence, maximum acquisition price and unresolved costs. Mark incomplete candidates NEEDS INFO rather than inventing a bargain. Manual checking is research; it is not a new automated Radar adapter. No bids or purchases.

Concrete blockers in this executor: no verified current alternative-source input or permitted search integration, no current personal-machine pilot database/runtime connection, and no current source-by-source candidate/fee/shipping evidence from the owner machine. Therefore no fresh multi-source buying recommendation or live test result can honestly be reported here. Existing consumed markers, receiver registry and cohort must remain unchanged.

## Source qualification improvement without a new framework

Use the current /sources definition and a referenced sample as the qualification record. Assess: allowed acquisition method and evidence/date; BIN/auction coverage; observed field quality and observation age; closing/soft-close behavior; buyer fees and99669 shipping; permitted photo handling; sample verification; unique useful candidates versus duplicates; and quota/monetary cost. The UI already tracks format verification, observed source/query coverage, missing costs/shipping and owner feedback. It does not establish overall source approval, complete economic yield or discover new marketplaces. Those are distinct gaps. A future small readiness summary should expose these gaps without turning a verified parser into a claim of automated access.

## Card patch and update boundary

Only non-ended FIXED_PRICE listings without AUCTION and without an aware deadline display Buy It Now. Tooltip: current availability requires confirmation. Deadlines, ended rendering, financial projections, shipping warnings and photo/listing expiry remain unchanged. This label is not an availability refresh.

The owner updater is source-only and requires the supervisor stopped (exclusive existing job lock plus free8001). It verifies the original complete source manifest, then replaces compact_feed.py and that manifest's one hash, with code-only rollback copies outside data. It does not read credentials, change data/photos/markers or restart collection. Existing acquisition ZIPs intentionally reject the changed manifest; do not bypass their checks or rerun them. The original installer/archive remain immutable. Resume only existing-cohort deletion maintenance after the patch, not acquisition. Preparing this updater is not evidence it has been installed on the owner machine.
