# Task 004 — Multi-source jewelry discovery and transaction email addendum

Owner scope: add working owner-operated ShopGoodwill, HiBid and generic estate
alert intake to the existing FastAPI/HTMX/SQLite system, without eBay production
approval. Preserve the existing financial safeguards, review workflows, raw
observations, exact queries and source-specific deduplication. Branch
`task/004-multi-source-discovery`, based on main after merged Task 003; no auto-merge.

## Required foundation

- Source definitions for ShopGoodwill, HiBid, EstateSales.org, EstateSales.net,
  CTBids, Poshmark, Mercari and other authorized sources; capability/authorization,
  notification knowledge, fee profiles, last import and unresolved restrictions.
- Private .eml/pasted notification intake, multiple messages and multiple item
  references, independent observations, CSV/JSON and copied-description entry.
- No website fetching, scraping, mailbox OAuth, automatic purchases, paid vision,
  recurring monitoring, public deployment, account management or new framework.
- Unified inbox with source/opportunity filters, provisional evidence, review
  handoff, irrelevant marking, timestamps and unknown Alaska costs.
- Exact curated saved-search suggestions, clipboard, owner selection/configuration
  records and verified real-match accounting; no automatic account interactions.
- Seller/auction grouping without assumed combined-shipping discounts or a bidder
  optimizer. Existing owner financial/purchase decisions must survive re-import.

## Addendum: message and receipt requirements

Classify SEARCH_MATCH, PROMOTIONAL_NEWSLETTER, AUCTION_CLOSING_REMINDER,
BID_CONFIRMED, OUTBID, AUCTION_WON, PAYMENT_RECEIVED and ORDER_SHIPPED; unknown
formats need human review. ShopGoodwill item/order IDs correlate messages. Bid
confirmations do not establish ownership or paid landed cost. Extract explicit
final item subtotals, tax, shipping/handling and order total; reconcile using
Decimal and never distribute multi-item order charges equally by default.

HiBid catalog URLs must remain distinct from individual lots. Preserve supplied
closing text and its original timezone without inventing item-specific prices or
deadlines. Only messages explicitly naming a saved search can support attribution;
ordinary promotional content cannot grant a typo credit. Personal Shopper and
HiBid daily digest formats remain provisional, despite the owner having reviewed
other transaction/promotion examples outside this coding task.

## Privacy, persistence and acceptance

No original owner messages, addresses, tracking links, personal details or real
purchases belong in Git. Store private original evidence in SQLite, render escaped
text, ignore external resources/attachments and impose upload/count limits. Use
v4 ordered migrations with preservation and rollback checks. Retain all 128 old
tests; add parsing, multi-email correlation, reconciliation, shipping confidence,
query attribution, safe HTML/links, grouping and workflow tests. Run desktop/mobile
browser checks and Ruff. Demonstrate at least 5 ShopGoodwill, 5 HiBid and 5 generic
synthetic candidates, duplicates, missing Alaska costs and explicit review handoff.

See [ALERT-SETUP.md](ALERT-SETUP.md) for implemented conventions, limitations and
operational paths; [VALIDATION.md](VALIDATION.md) records verification evidence.
