# Next task — one bounded eBay sourcing pilot

**Prepared, not authorized or executed.** Use the existing Discovery planner,
adapter and workbench. No new recovery infrastructure or sourcing subsystem is
needed. JRI-014's revised policy/IAM decision is separate from the fresh one-run
sourcing authorization. The old authorization was consumed by an OAuth 401
`invalid_client` attempt with zero Browse requests; it cannot be reused.

## Concrete run preview

| Exact seller-language query, in planner order | Page size | Pages | Offset |
|---|---:|---:|---:|
| `blue stone ring` | 3 | 1 | 0 |
| `mixed jewelry lot` | 3 | 1 | 0 |
| `silver ring with blue stone` | 3 | 1 | 0 |
| `sterling jewelry lot` | 3 | 1 | 0 |

Use `newlyListed`, both auction and fixed-price formats, `EBAY_US`, existing US/99669
context and omitted `auto_correct`. Do not append material, maker, cultural,
vintage, category or price filters. Retain source corrections separately from the
transmitted phrase. Maximum four queries and **12 returned observations**, counting
duplicates and invalid records; explicitly set `max_pages_per_query=1` because the
existing default is 2. Use the existing 15-second timeout, two-retry bound,
one-second request interval and ten-second maximum retry delay. No extra pages.

## Preparation and independent authorization

1. Inspect credential **readiness**, enforced `api.ebay.com` access and the current
   intended Production keyset status without printing credentials. The owner has
   reported the keyset enabled and deletion notifications work, but Browse access
   is still unproven. Do not make a separate setup OAuth probe or repeat activation.
2. Use a protected isolated copy of the existing private application, retaining
   history and settings. Verify its pre-run backup/restore and deletion-ledger
   safeguards using the existing workflow. If prior private data is absent, report
   that and obtain it through the approved owner path; do not invent restored data.
3. Enable only the existing four-query group in that copy, preserving other settings.
   Render `/discovery/plan` with the exact table above; this sends no live request.
4. Present that actual preview and obtain new explicit per-run owner confirmation.
   The run requires `EBAY_MODE=production`, `EBAY_SOURCE_ENABLED=true`, both
   `EBAY_PRODUCTION_ENABLED=true` and `EBAY_PRODUCTION_APPROVED=true`, and
   `approve_production=yes` through `/discovery/run`. Keep vision fixture/live-disabled.
5. Execute once. A permission error stops immediately; exhausted bounded network/rate
   failures stop later queries. Report the real outcome without a replacement run,
   scraped substitutes, additional API probes or fixtures presented as listings.

## Useful product result

Record OAuth/Browse attempts and retries, exact queries transmitted, returned,
duplicate/invalid and unique-item counts, and any safe error status. Preserve each
query/item observation during deduplication. Promote up to three promising actual
candidates to the existing workbench, retaining listing URL/time and seller wording.

For each candidate, produce a short **BUY / PASS / RESEARCH** decision with evidence:

- Seller-language description and source claims, separated from verified silver,
  stone, maker, age or cultural attribution. Preserve permitted images and specific
  missing-evidence requests; no paid vision inference.
- Current asking price versus current auction bid, currency and quote timestamp.
  An auction bid is not a guaranteed acquisition price; show an evidence-backed
  maximum delivered-cost ceiling where the existing model supports one.
- Item-specific Alaska delivery eligibility and shipping quote. A US/99669 request
  or generic US shipping amount is not confirmation of Alaska delivery.
- Delivered acquisition cost: supported item price plus Alaska shipping, tax,
  buyer fees and handling. Use Decimal/conservative cent rounding and explicit
  unknowns; never turn a missing cost into zero. Keep multiple-item allocation
  unresolved without evidence.
- Existing financial/evidence thresholds and relevant realized-sale evidence where
  required. Asking prices and dealer sold labels are not verified realized proceeds.

Use **BUY** only when existing cost, eligibility and evidence gates pass; it is an
owner decision recommendation, never a purchase instruction. Use **PASS** for a
supported disqualifier and **RESEARCH** for material missing evidence. Deliver the
small candidate table and the next evidence needed, not a general market survey.

## Authorization text for the next task

> Prepare the exact four-query preview above and ask for confirmation of that
> actual preview. After my fresh per-run approval, run it once through the existing
> Production Discovery controls: four queries, one page each, three observations
> each, twelve maximum including duplicates/invalid records. Review up to three
> real candidates for Alaska delivered cost and BUY/PASS/RESEARCH decisions.
> No purchasing, automatic bidding, paid inference, recurring search or
> unrestricted scraping is authorized.

This task brief is not that approval. No sourcing call was made while preparing it.
