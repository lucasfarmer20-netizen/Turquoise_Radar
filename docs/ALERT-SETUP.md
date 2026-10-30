# Task 004: owner-provided multi-source intake

## Working paths and evidence status

All intake is local and owner-operated. No mailbox password, mailbox OAuth token,
Gmail/IMAP connection, marketplace login, website fetch or recurring poll is used.
The existing eBay adapter and its production gates are unchanged.

| Source | Operational now | Real-format validation status |
|---|---|---|
| ShopGoodwill | .eml/pasted text, direct item-ID correlation, labelled amounts, bidding/purchase/shipping event classification, paid-order reconciliation, CSV/JSON/manual entry | The addendum describes observed bidding/receipt categories. No actual owner email was supplied here; synthetic tests only. Personal Shopper search-match structure remains provisional. |
| HiBid | .eml/paste, direct lot vs catalog links, labelled auction/house/price/premium evidence, original closing time/zone, CSV/JSON/manual entry | Observed promotional/closing categories are described by the owner. Actual promotional templates and daily saved-search digests have not been validated here; extraction is provisional. |
| CTBids, EstateSales.org, EstateSales.net | Generic explicit-label and direct-link intake, CSV/JSON, copied descriptions, optional verified sample rules | Unknown/unlabelled layouts stay for human review; no claim of a confirmed live notification schema. |
| Poshmark, Mercari | Owner-provided structured records and direct-link/copied-text entry; generic email evidence storage | No source-specific email format or automatic access permission claimed. |
| Other authorized estate sources | Configure a separate source entry/domain, then use generic labels, structured data or manual entry | Rules require permitted sample verification; no undocumented endpoint or scraping is implemented. |

The source register records methods, authorization restrictions, notification
knowledge, available data, shipping uncertainty, fee-profile name, exact-query
limitations, implementation state, sample reference/kind and last successful owner
import. This is not an API connection-health register or evidence of crawling rights.
Source names become stable after an import to preserve source/item identities.
Use **Add another authorized estate source** for an independent marketplace, not
an unrelated vendor domain added to a previously imported source.

## Import alerts

1. Open **Multi-Source Inbox → Import alerts** and choose the source.
2. Upload up to **10 .eml files**, maximum **512,000 bytes each / 3,000,000 bytes
   total**, or paste text. Separate independent pasted messages using a line:
   `===NEXT MESSAGE===`. Listing separators within one message do not create emails.
3. Optional actual received time must be ISO 8601 with timezone. Otherwise only a
   valid Received header supplies received time. The Date header supplies sent
   time; it is never substituted for received or listing-creation time. Import
   time is separate. Original header/timezone strings remain in the private evidence.
4. Review the report, provisional suggestions, raw message and independent candidate
   observations. A catalog-only or unsupported message remains available for human
   review with zero invented lots. Use direct manual entry if its actual format
   cannot be interpreted safely.
5. Send selected candidates to the existing purchase-review queue and evidence editor.
   Imports never silently set ownership or overwrite existing financial/purchase
   history. Marking irrelevant affects the inbox only, not purchase decisions.

ShopGoodwill direct `/item/<numeric-ID>` references and HiBid `/lot/<numeric-ID>`
vs `/catalog/<numeric-ID>` references are recognized conservatively. Generic records
need an explicitly supplied Item ID/Source listing ID/Lot ID and safe direct URL,
or an owner-verified simple item-path prefix. A local lot number without its
marketplace identity is not guessed into a global item ID. No tracking redirect is followed. The bounded offline HiBid exception below recovers
only an explicit embedded target; it never guesses a destination from an opaque token.

Explicit label suggestions include Title, Current bid/price, Seller/Auction House,
Auction ID/Estate ID, Saved search/Search phrase, Lot closes, Shipping/Handling and
Buyer premium. Amounts need explicit dollar/USD notation; bare numbers, percentage
premiums and absent values do not become cash fees. A quoted percentage is retained
as rate evidence with unknown basis until a dated fee profile is confirmed.
Generic title/keyword matching never establishes source-query attribution.

A message with multiple identifiable items creates independent candidate
observations, never one assumed multi-piece jewelry lot. Ambiguous card-to-price
associations remain unknown. HTML semantic item blocks or explicit `---` text
separators permit scoped suggestions; unknown real layouts may yield links and
titles without safe price binding. Each candidate starts with Alaska shipping
UNKNOWN and missing handling/other costs unfilled. “Shipping Available” is not
99669 eligibility, even when a source charges shipping or a receipt was paid.

## Classification and purchase reconciliation

The importer distinguishes:

- SEARCH_MATCH
- PROMOTIONAL_NEWSLETTER
- AUCTION_CLOSING_REMINDER
- BID_CONFIRMED
- OUTBID
- AUCTION_WON
- PAYMENT_RECEIVED
- ORDER_SHIPPED
- UNKNOWN (human review)

Classification uses explicit subject/heading vocabulary; it is a provisional
suggestion, not sender authentication or validation against every real template.
No bid, win, payment or shipment email automatically creates Purchased feedback.

ShopGoodwill item IDs correlate independent events. Explicit order IDs connect
receipts/wins/shipment messages. Order-level financial reconciliation uses only
PAYMENT_RECEIVED evidence, never a bid price. Explicit final paid line-item subtotals
can be summed when all identified items are covered; this derivation is marked.
Conflicting final line items vs a stated subtotal block automatic confirmation.

The reconciliation layer uses Decimal and keeps these states distinct:

- RECONCILED: stated/explicit final item subtotal + tax + either explicit combined
  shipping/handling or both separate charges + any separately stated fees equals
  the paid order total exactly.
- INCOMPLETE: a required component is unknown.
- MISMATCH: the known component sum differs from the stated total.
- CONFLICT: paid messages disagree, or line-item/combined-charge evidence conflicts.
- UNSUPPORTED_CURRENCY: non-USD payment evidence; no conversion is invented.

Unlisted premiums/service fees are not copied as zero into listing finances.
Order reconciliation describes the sum of supplied components, not a universal
fee schedule. Inspect the raw receipt and each unlisted component before confirming.

For a single identified item with a reconciled receipt, the owner must explicitly
confirm ownership and **enter all six actual cost amounts**, including independently
confirmed zeros and any shipping/handling split. They must match receipt totals
and stated components. This creates an audited before/after edit and one Purchased
feedback milestone. Repeated confirmation is idempotent. Shipping availability is
still UNKNOWN unless the owner explicitly confirms the Alaska status separately.

For multiple items, order-level tax, shipping and handling remain on the invoice.
There is **no equal split**, inferred discount or automatic per-lot landed cost.
Use the existing evidence editor only after obtaining defensible allocations.
Receipt import alone never changes an already reviewed/purchased listing.

HiBid catalog-only closing reminders retain catalog URL, supplied closing text and
original timezone. An ISO time with explicit offset can also be parsed, while
ambiguous/unzoned text stays raw. No catalog deadline, current price or bid increment
is invented for individual lots. Seller/auction group views link those catalog
notices without labelling them item-specific closing evidence.

## Deduplication, queries and saved searches

Identical source/message bytes import once; re-import attempts increment the
message counter and batch duplicate count. A distinct message referring to the
same source item adds an independent observation without another item identity.
Source + stable item ID is primary. Manual/CSV/JSON inbox records require a stable
ID or unambiguous direct URL; description/image similarity is never used to merge
new inbox candidates. Query-addressed or ambiguous URLs require an explicit ID.
Fixture identities use a separate `FIXTURE` namespace from real owner imports.

The existing `discovered_items.ebay_item_id` column name is retained for backward
compatibility; new paths use it as a provider item-key slot. The normalized
`source_listing_id` remains authoritative, and source namespaces never merge.
Existing eBay histories/gates are unaffected. Original API and email observations
remain separate and visible after handoff to review.

Saved-search attribution requires explicit search text on SEARCH_MATCH messages.
Promotions, lifecycle notices, generic text matches and catalog reminders do not
receive typo-query credit. Unverified extraction remains PROVISIONAL until owner
confirmation or an appropriately owner-verified source sample. A synthetic sample
profile does not automatically verify real query provenance. Exact spelling,
capitalization and meaningful spaces are retained; CRLF line endings are normalized
for parsing while original bytes remain unchanged.

**Saved search assistant** offers the requested Cheap Rings, Sterling Lots and
Southwestern Anomalies phrases for every registry source. Copy uses the exact
input value, with a select-and-copy fallback. Selected/configured flags are owner
records, not marketplace account actions or source acknowledgements. Real matches
count unique non-synthetic candidates only after exact source attribution is
verified. Synthetic, promotional and inferred matches cannot inflate that metric.
No platform's literal matching or query syntax is presumed supported.

## Seller/estate preparation

Groups use source plus exact seller/auction IDs, with counts of ring/lot vocabulary
leads, observed item amounts, known premiums/handling and remaining costs. Where a
candidate has been reviewed, owner-confirmed amounts/evidence are shown without
rewriting the original alert. Combined-shipping eligibility is shown as confirmed
only when the owner records both the flag and evidence; no savings amount is
invented. Overlapping seller and auction groups are not additive inventory totals.
Lead labels describe vocabulary, not verified sterling, authenticity or profit.
The existing financial/melt/scoring engine still decides financial confidence.

## Privacy and limits

Raw MIME bytes are base64-preserved inside the private SQLite database. Decoded
text and normalized observations are also private. The raw view is escaped text,
never active HTML. Script, form, style, embedded content and image resources are
not rendered; only validated HTTPS direct source links become clickable. Link
userinfo, unsafe schemes, off-source hosts and tracking/redirect paths are rejected.
Original tracking URLs are never clickable; only a validated canonical target from
the offline exception below can be used as a source link.
No tracking pixels, attachments, arbitrary URLs or external resources are fetched.
Attached messages are not traversed as new body content; attachments never execute.
MIME nesting/parts, decoded text, item links, upload counts and body sizes are bounded.

Validation responses are fixed summaries rather than echoed email bodies. No
complete message bodies are logged by intake. Keep the unauthenticated app bound
to loopback/private access: owner emails are sensitive. SQLite and pre-v4 backups
are plaintext private files, not an encrypted mailbox vault; back them up securely.
Never commit original messages or private database copies. `.gitignore` excludes
.eml/.msg/.mbox, allowing only explicitly named synthetic fixture .eml files.
No mailbox password or OAuth token is collected or stored by this feature.

## Synthetic demo

Use a **separate database**, not your owner inventory:

```bash
.venv/bin/python -m radar.intake.demo
RADAR_DATABASE_URL=sqlite:///./data/alerts-demo.db .venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8000
```

The demo script defaults to `data/alerts-demo.db` and imports only checked-in,
clearly labelled synthetic messages. Fresh results: 13 distinct messages from five
source paths, 15 candidates (5 ShopGoodwill, 5 HiBid, 5 generic estate), 23 independent
observations and one repeated-message attempt. It includes all eight message types,
a catalog-only reminder and single-/multi-item payment receipts. No purchases are
seeded. All fixture-only imported results remain non-live and never count as real
saved-search success. Actual owner messages, addresses and purchases are not used.

## Official references and authorization status

The owner reports ShopGoodwill Personal Shopper and HiBid saved-search emails.
Direct official help checks returned proxy **403 Forbidden** during this task.
The following are official starting references, not claims that current article
contents, paths, terms or digest formats were successfully verified:

- [ShopGoodwill help / Personal Shopper reference entry point](https://shopgoodwill.com/help)
- [ShopGoodwill official site](https://shopgoodwill.com/)
- [HiBid help / saved-search reference entry point](https://hibid.com/info/faq)
- [HiBid official site](https://hibid.com/)
- [EstateSales.org official site](https://estatesales.org/)
- [EstateSales.net official site](https://www.estatesales.net/)
- [CTBids official site](https://ctbids.com/)

Personal Shopper/daily digest structures remain provisional because no real owner
samples were provided to this coding task. The transaction categories in the
addendum guided privacy-safe synthetic examples; that does not establish full
real-email compatibility. Configure optional literal label aliases/item prefixes
only after permitted sample review and record the sample kind/reference. A network
domain allowance alone is never permission to scrape, crawl or automate accounts.
Local owner-provided intake requires no marketplace API approval. Any later live
connector/mailbox access needs separate documented authorization and scope.

## PR #4 compatibility follow-up (Task 004.1)

The [pre-merge compatibility comment](https://github.com/lucasfarmer20-netizen/Turquoise_Radar/pull/4)
reports `Item: <digits>`, `item #<digits>` and standalone bid-notice IDs, plus the
HiBid tracking host `clicks.auctions.hibid.com`. No original email or concrete real
tracking URL/encoding was attached to the comment. Regression messages represent
these reported structures using synthetic item/order IDs and synthetic tokens;
they do not establish full compatibility with an actual private email template.

ShopGoodwill now recognizes explicit Item/Item ID/Item Number labels and item #
references, including several items without direct links. For bid/win/outbid notices,
a number on its own line requires an immediately preceding Item heading or the
explicit sentence `Your bid has been accepted/submitted for the following item:`
(the `was` variant is also supported). Unlabelled numbers, order numbers, postal
codes and contact numbers never become inferred item IDs. Winning Bid Notice and
Bid Notification headings are classified as lifecycle events, not purchases.
`Item Subtotal`, `Tax`, `Shipping and Handling` and `Order Total` reconcile a paid
single-item receipt; its subtotal can supply the final item amount. Multiple item
references retain all order charges at invoice level and cannot use the single-item
confirmation action. Existing explicit ownership and six-component cost checks apply.

HiBid recovery is **offline text extraction**, not a redirect request or general
tracking-token decoder. It accepts only the exact reported tracking host over HTTPS
and a single absolute HTTPS target visible in its URL path, either literally or
after one percent-decoding pass. The target must be a provider-domain
`/lot/<numeric-ID>` or `/catalog/<numeric-ID>` path. The stored/clickable target is
canonicalized to that identity; wrapper prefixes/suffixes, query parameters and
fragments are discarded. No tracking token is copied into candidate or catalog URLs.
This is generic explicit-target recovery on the reported host; a particular real
wrapper encoding has not been verified by the PR comment or supplied samples.

Opaque paths (including opaque `/ls/click?upn=...` values), targets available only in
query parameters, arbitrary hosts, credentials, unsafe ports/schemes, repeated
encoding, nested or multiple targets and ambiguous paths remain unsupported review
evidence. There is no base64-token guessing, cryptographic decoding or link following.
Catalog-only reminders remain catalog-only, and original closing text/timezone is
retained without creating a deadline or price for an individual lot. Newsletter
links never receive saved-search attribution merely because a title contains a typo.
The original private alert remains available as escaped evidence, including any
unresolved links. Personal Shopper and daily digest formats remain provisional.

A reproducible browser regression checks only an **empty disposable** database.
With Playwright available to Node and Chromium installed, start a separate server:

```bash
RADAR_DATABASE_URL=sqlite:////tmp/radar-compat-check.db .venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8004
# In another shell (set NODE_PATH if Playwright is installed globally):
node scripts/check-intake-compat.cjs
```

Use a fresh temporary database filename each run. The check aborts external requests,
uses synthetic fixtures only and covers paid ownership confirmation, non-allocation,
repeat imports, catalog-only/opaque notices and twelve mobile pages. It must never
be pointed at owner inventory. `CHROMIUM_PATH` can select an installed Chromium binary.
