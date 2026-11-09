# Email leads: recovered evidence and remaining enrichment

The actual November 8 ShopGoodwill message supplies 25 titles, quoted prices,
bid counts, start timestamps and direct links. It supplies no photos, lot closing
timestamps, fees or shipping quotes. Personal Shopper Ends is subscription expiry,
not a lot deadline. Existing combined projections already contain all 25 prices;
the missing bid/start evidence is now recovered from retained messages in memory.

The HiBid message supplies six titles, direct links and date-only Ends 11/09 hints.
Its HTML references six unique remote CDN thumbnail URLs (duplicated in markup),
not embedded image attachments. The retained plaintext excerpt contains no image
bytes. No price, exact close time/timezone, fees or per-lot shipping quote is given.
No shipping is saved-search metadata, not evidence that each lot cannot ship.

Changes: preserve bid-count and date-only hint evidence in the existing parser;
reparse retained messages for display only without database writes/reimport;
show email-specific evidence with incomplete-date warnings; retain source
observation timestamps even when a price is absent. No hints are converted into
deadlines or current availability. Photos remain explicitly unavailable locally.
This correction does not refresh snapshots, establish sterling purity or value,
or qualify a purchase. The new view must not be sold as a complete sourcing feed.

On the genuine retained samples, all 25 bid/start fields and six close hints are
recoverable. All six price-less HiBid cards now have the November 8 observation
date. Original messages/databases stay unchanged. Tests contain synthetic data
only, including date-only deadlines and price-less observation timestamps.

## Enrichment boundary checked November 9, 2026

Official pages reviewed (documentation only, no marketplace API/image requests):

- https://shopgoodwill.com/about/terms-of-use : Acceptable Use requires prior
  consent for third-party applications interacting with the service and restricts
  unauthorized automated extraction. Shipping terms vary by seller.
- https://hibid.com/home/termsofuse : section 7 restricts automated extraction,
  scraping and other uses of auction information. A remote image URL inside an
  email is not itself confirmation of permission to retrieve/cache it in Radar.

No approved current-data API/feed or permission to automate these listing pages
has been established in this workspace. No hidden endpoints, logged-in session,
new account or access workaround was attempted. Loading the six remote thumbnails
would make additional external requests; it has not happened. Fetching them would
still leave current prices, fees and shipping unresolved.

Practical direction: treat these messages as discovery hints until a publisher-
authorized detail/image feed or permitted current-data export is available. The
existing eBay API/image workflow is the established richer source, but any new
collection still needs its bounded run approval and intact deletion handling.
Do not spend another installation cycle presenting bid counts as if they solve
missing photos and valuation. The local correction is ready for the next bundled
release; no owner-machine deployment or new acquisition was performed here.
