# Source register — Task 004

Local owner-provided alert, CSV/JSON and copied-text paths now operate independently
of eBay approval. See [operational matrix, official references and limitations](ALERT-SETUP.md).
ShopGoodwill Personal Shopper / HiBid daily digest formats are still provisional;
the addendum describes other observed transaction/promotion categories, but no real
messages were supplied to this task. Synthetic fixture validation is not real-format
verification. Current public help checks returned proxy 403. No public website is
assumed to grant API/crawling rights.

| Source | Working owner path | Remaining restriction |
|---|---|---|
| ShopGoodwill | .eml/paste item links, Item/item # and bounded notice IDs, item/order events, receipts, structured/manual | Personal Shopper digest unverified; no website automation authorization |
| HiBid | .eml/paste direct or explicit offline embedded lot/catalog targets, auction/price/fee evidence, structured/manual | Daily digest unverified; Shipping Available does not prove 99669 eligibility |
| EstateSales.org | Generic labelled/link intake, structured/manual | Optional source rules need permitted samples; automatic retrieval not authorized |
| EstateSales.net | Generic labelled/link intake, structured/manual | Same; source fees and shipping remain per-sale evidence |
| CTBids | Generic labelled/link intake, structured/manual | Same; no universal premium or Alaska quote assumed |
| Poshmark | Owner CSV/JSON, direct links/copied text, generic alert evidence | No validated email template or API/crawling access claimed |
| Mercari | Owner CSV/JSON, direct links/copied text, generic alert evidence | Same |
| Other authorized estates | Separate configurable source entry/domains, generic/structured/manual | One entry per marketplace; verified samples required for optional custom extraction |

PR #4 compatibility follow-up adds reported ShopGoodwill ID forms and bounded
offline recovery of explicit targets on the reported HiBid tracking host. Real
wrapper encoding is not supplied/verified; opaque tokens remain human-review
evidence. See [exact compatibility limits](ALERT-SETUP.md#pr-4-compatibility-follow-up-task-0041).

All unknown costs/attribution remain unknown. Owner-configured metadata is not an
access grant. No direct mailbox integration or additional automated source adapter
is implemented. Original owner content stays in SQLite, not GitHub.

---

# Previous eBay architecture evaluation

Assessment date: **2026-10-29**. Task 003 implements an official eBay adapter with
fixture-first operation; **no real sandbox or production API calls were executed**.
Production remains disabled pending final approval and explicit authorization.
See [eBay setup](EBAY-SETUP.md) for exact implemented endpoints, filters, OAuth,
retry/pagination limits, correction handling and the activation checklist.

## Evidence status and access limitation

Direct checks of the official eBay Browse search reference and Whatnot fee help
page failed with proxy `403 Forbidden`. Their documentation domains are absent
from the running environment's enforced allowlist. Required additions
`developer.ebay.com` and `help.whatnot.com` were saved to the environment draft,
preserving the pending `api.github.com` entry. Saving does not apply or publish it.
Official current terms, numerical quotas, pricing and production eligibility
remain **unverified in this environment**. The URLs below are authoritative places
to verify, not a claim that their current page contents were read successfully.

## Selected architecture: official eBay Browse API, conditional on approval

Use the implemented **read-only eBay Browse integration** for authorized testing after
current documentation and the owner's application eligibility are verified.
It is a stronger first candidate than an undocumented marketplace scraping path:
its published REST API design offers a defined acquisition contract, OAuth and
rate-limit controls. This recommendation does not imply production access is
already approved or that all listings ship to Alaska.

| Area | Planning evaluation / required verification before implementation |
|---|---|
| Credentials | eBay Developers Program account and application keyset; client ID/secret for application OAuth. Use environment credentials securely; no credentials are needed for Task 002. |
| Production authorization | Verify current Buy API requirements, use-case/business-model approval, license agreements, permitted storage/use and any production access review. Do not assume a developer account grants unrestricted production Browse access. |
| Authentication | Evaluate the documented client-credentials application token flow and required scope(s); verify production/sandbox endpoints and token lifetime before coding. Never use harvested user sessions. |
| Keyword queries | Evaluate `/buy/browse/v1/item_summary/search` and `q` for exact curated phrases such as `nature america`. Preserve sent, original and source-revealed effective queries. Do not assume literal matching or quote syntax prevents eBay rewriting. |
| Listing data | Evaluate documented item IDs/URLs, title, price/currency, seller, buying options, categories, image references, item location and shipping options. Get item detail only where needed and authorized. Listing coverage and fields may vary by buying option. |
| Alaska shipping | Supply documented US destination/postal context for **99669**. Verify delivery filters/context headers and shipping cost/eligibility semantics against current docs and actual authorized responses. US delivery alone does not establish Alaska eligibility; null/missing costs and exclusions remain UNKNOWN. |
| Sorting | Evaluate documented price direction, newly listed, ending-soon and relevance/default capabilities. Confirm accepted sort values and compatibility with filters before implementation; do not infer auction closing times from search order. |
| Polling / limits | No polling now. Before Phase 003, read the app's assigned Browse quota, endpoint limits and current call-limit documentation. No numerical quota is claimed verified here. Budget calls per curated query, cache/dedup and stagger polling; use bounded retries/backoff, respect 429/Retry-After and an independent disable switch. |
| Costs | No paid service was added. Confirm current developer/API/affiliate/license charges and quota-increase terms in the approved account; do not promise universally free API access. Listing purchase, tax and Alaska shipping costs remain separate. |
| Source evidence | Official references below; current policy/contract review remains blocked pending environment access. |

Before production: verify credentials/scopes and approval, validate search/detail
response fixtures, explicitly test unknown/unavailable/confirmed 99669 shipping,
record field provenance and query behavior, and obtain approval for the specific
polling schedule. Only then implement the adapter. No live polling belongs in 002.

### Official eBay references to verify

- [Browse API overview](https://developer.ebay.com/api-docs/buy/browse/overview.html)
- [Search method and query/filter/sort fields](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search)
- [Get item fields and shipping](https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem)
- [OAuth client credentials](https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html)
- [API call limits](https://developer.ebay.com/develop/get-started/api-call-limits)
- [Buy API requirements](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html)

## Previously named marketplaces

| Source | Allowed workflow implemented now | Automated access evaluation / limitation |
|---|---|---|
| Poshmark | Owner-copied text, manual fields and authorized CSV/JSON exports | No public API or scraping permission assumed. Verify an official approved API/feed/export and current terms before any connector. |
| Mercari | Owner-copied text, manual fields and authorized CSV/JSON exports | Same authorization requirement; do not repurpose private web endpoints or sessions. |
| EstateSales.net | Manual listing/auction capture and owner-permitted exports | Evaluate written API/feed/notification access, shipping exclusions and auction-house-specific terms/fees. No general polling right established. |
| EstateSales.org | Manual listing/auction capture and owner-permitted exports | Verify approved partner/feed/export access and each sale's fee/shipping terms. No automation approval established. |
| CTBids | Manual capture and explicit buyer profiles | Verify operator permission/API/export access, item-plus-shipping service bases, handling, invoice charges and Alaska fulfillment per auction. No universal fee schedule assumed. |
| Other estate/secondhand | Local owner input through SourceAdapter normalization | Independent authorization evaluation required. |

These are not freshly verified claims of platform API absence: authorization and
current terms remain unresolved. A website visible in a browser is not permission
for automated retrieval. No CAPTCHA bypass, fingerprint evasion, proxy rotation,
unauthorized session reuse, scraping or purchasing automation is implemented.

## Whatnot calibration reference

Task 002's owner supplied 8% standard US commission on item price and 2.9% processing
on item + buyer shipping + buyer tax, plus $0.30/transaction. These are dated,
configurable assumptions until current category/volume terms are verified:
[Whatnot selling fees](https://help.whatnot.com/hc/en-us/articles/360061197472-Whatnot-s-Selling-Fees).
See [calculation conventions](CALIBRATION.md) for exact implementation.

## Adapter boundary

Manual, CSV/JSON, Quick Capture and fixture discovery work now. The official eBay
adapter is ready for permitted API testing, not proof of production approval. `SourceAdapter` retains capability
metadata for enabled state, exact-match support, effective-query visibility,
polling interval, request limit, retry cap and access method. Remote capabilities
remain disabled placeholders. Store untouched source observations at ingestion;
never fabricate effective queries, Alaska eligibility or source authorization.


## Task 003 implementation status

- Implemented: client-credentials OAuth, Browse keyword search, fixed/auction/both
  filters, USD price bounds, newlyListed/price sorting, bounded pagination,
  timeouts/pacing and safe bounded retry handling, explicit production gates.
- Query experiments omit auto_correct or send KEYWORD; OFF is never sent. A source
  autoCorrections.q is recorded independently. Omission does not prove literal matching.
- Fixture data is synthetic, credentials-free and local. Statistics are generated
  only by actual owner-triggered fixture runs, with conspicuous NON-LIVE labels.
- Summary shipping/context is not accepted as Alaska confirmation; no resale,
  purity, net weight, maker/tribal attribution or purchasing action is inferred.
- Current documentation remains inaccessible under the running proxy policy.
  Final approval, credentials, live schema validation and numerical assigned quotas
  remain unverified. No claim of sandbox connectivity or production readiness is made.
- Sandbox testing needs api.sandbox.ebay.com access and secure environment keyset
  bindings. Production additionally needs api.ebay.com access after explicit
  authorization; that production host is not added by Task 003.
