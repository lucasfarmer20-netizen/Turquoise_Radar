# Source integration status — November 9, 2026

Current code has one official marketplace search adapter: eBay. ShopGoodwill and
HiBid have tested owner-email intake, not live marketplace search. Poshmark and
Mercari appear as manual-intake names, not connected adapters. Bonanza, eBid and
Depop have no implemented adapter or verified credential here.

Priority after the bounded auction increment:

1. Bonanza: official findItemsByKeywords is a plausible next buyer search adapter.
   Read-only search uses a developer credential. Validate terms/image retention,
   exact field coverage and account/quota access before a mocked adapter followed
   by an approved tiny live test. No account or key request has been made.
2. eBid: official JSON docs include SearchEbid and GetListingSingle. Verify usable
   developer access, secure HTTPS endpoint and current service/retention terms;
   old official examples use HTTP and disabled TLS verification, which must not
   be copied. Then test normalized price/currency/end/shipping/photo fields before
   any small authorized live run. Documentation is not proof of activated access.
3. Poshmark: existing owner text/CSV/JSON intake only. No verified public buyer API.
   Obtain an authorized buyer feed/API or supported export/sample; do not build a
   crawler. Official terms restrict scraping and unauthorized reuse of content.
4. Depop: documented partner API is seller-oriented. Partner credentials do not
   establish marketplace-wide buyer search rights; its terms require written
   authorization for automated access/analysis and prohibit internal/legacy API
   access. Establish a suitable approved buyer feed before a connector.
5. Other marketplaces: use /sources qualification to record actual access method,
   sample evidence, freshness, fees/shipping completeness and tested yield. Keep
   untested names separate from connected sources. Prefer improving the already
   working email evidence over adding empty source labels.

All adapters should feed the existing cards/evaluation, keeping observation time,
unknown costs and shipping uncertainty. No new subscriptions, registration,
third-party scraping service or recurring collection has been initiated.

Official references:
- https://api.bonanza.com/docs/reference
- https://ebid.3scale.net/doc-json
- https://www.ebid.net/us/help/selling-on-ebid/
- https://www.poshmark.com/terms
- https://partnerapi.depop.com/api-docs/terms/
