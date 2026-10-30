# Source capability and authorization register

| Source | Phase 001 retrieval | Automated access status | Next evaluation |
|---|---|---|---|
| Manual | Working form / advanced JSON | Local owner input only | Evidence quality |
| CSV / JSON | Working local file / paste / CLI | No source network access | Approved export formats |
| Poshmark | Interface / demo fixtures only | Disabled; no public API assumed | Approved API, feeds, notifications, exports and terms |
| Mercari | Interface / demo fixtures only | Disabled; no public API assumed | Approved API, feeds, notifications, exports and terms |
| EstateSales.net | Interface / demo fixtures only | Disabled; no public API assumed | Authorized notifications/feeds and auction details |
| EstateSales.org | Interface / demo fixtures only | Disabled; no public API assumed | Authorized notifications/feeds and auction details |
| CTBids | Interface / demo fixtures only | Disabled; no public API assumed | Approved access, buyer fees and Alaska fulfillment |
| Other estate / secondhand | Extensible SourceAdapter | Authorization required | Independent access evaluation |

`radar.sources.SourceAdapter.retrieve(exact_query)` yields validated ListingInput
records. Capability metadata includes enabled state, exact-match support,
effective-query visibility, polling interval, request limit, retry cap and access
method. Current remote adapters are **not implemented**; defaults are evaluation
placeholders, not permission or claims about any platform. No scheduler runs.

Future adapters must retain untouched source data at ingestion, record the exact
query actually sent, the original phrase and any source-revealed effective query.
Never silently accept source autocorrection as the original query, assume quotation
marks enforce exact matching, or synthesize supported capabilities. Misclassified
categories are retained in the listing contract. Confirm Alaska shipping and the
applicable fee/tax base independently. Respect source terms, rate limits and scopes;
no circumvention or purchasing automation.

Network needs today: PyPI/files.pythonhosted.org for installation; GitHub proxy for
Git operations. HTMX is vendored from pinned jsDelivr npm content. The running app
makes no marketplace, AI or CDN requests. External listing links are opened only
by the owner. Image URLs are stored without remote fetching in this foundation.
