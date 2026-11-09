# Ending-soon auction option: offline qualified, not activated

The existing Discovery group form now accepts `buying_format=AUCTION` with
`sort=endingSoonest`. Other buying formats are rejected for that narrow option.
The exact query, existing page/result/request budgets, production confirmation
gates and US ZIP 99669 context are preserved. No saved group is changed or enabled
by installing the source. Fixture sorting places known aware end times first;
missing or untrusted timestamps sort last. It is synthetic, not a market test.

Browse normalization retains reported current bid, image URL, end time and shipping
quote. Auctions that also offer Buy It Now use currentBidPrice; absent bids stay
unknown rather than becoming the BIN price. The original raw observation retains
the separate source fields. The combined card labels the last observed bid,
shows its observation timestamp, recorded closing time and reported shipping
amount with unverified 99669 eligibility. Photos use the existing local validated
photo route; retaining an image URL does not fetch an image or qualify its display.
Existing listing/photo freshness and deletion protections remain authoritative.

## Buyer sourcing and the unresolved price-modeling boundary

Radar is a buyer sourcing tool, not a seller repricing or listing tool. Section
9.5 addresses using eBay Content to suggest or model prices for items listed on
eBay; its wording does not expressly distinguish buyer from seller recommendations.
Section 9.6 separately addresses seller arbitrage. Neither justifies claiming
that all buyer opportunity qualification is categorically forbidden.

The narrow unresolved question is whether an automated maximum-bid, fair-value
or undervaluation estimate derived from eBay Content falls under 9.5. Buyer intent
alone does not settle that question, and no applicable written clarification is
established here. Displaying observed bids, shipping quotes, freshness, closing
times and missing information is distinct from generating a price recommendation;
this increment continues that practical buyer workflow.

The unpublished blanket FACTS ONLY branch has been removed following the owner's
buyer-only clarification. Existing buyer analysis and conservative evidence gates
continue unchanged. No seller repricing, listing creation, invented resale values,
or automatic bids are introduced. This does not claim an explicit contractual
exception for all possible future price models.

Sources reviewed November 9, 2026:
- https://developer.ebay.com/join/api-license-agreement (section 9, item 5)
- https://www.developer.ebay.com/develop/api/buy (Browse search: AUCTION and endingSoonest)
- https://developer.ebay.com/api-docs/buy/browse/static/release-notes-archive.html

## Validation and next live dependency

Synthetic tests cover exact wire parameters, one mocked OAuth plus one search,
result/page caps, auction/BIN price separation, unknown bid handling, retained end
and image fields, unknown destination shipping, fixture end ordering, and proof
that buyer analysis preserves the existing evidence rules. An actual-schema route
test displays the factual auction card and serves its synthetic local photo.
No external marketplace/image requests or scheduled refreshes were executed.

The existing one-search BIN launcher and consumed authorization markers are
unchanged. This option is not a ready replacement live launcher: the existing
pilot's seller cohort is immutable, so new auction sellers cannot be appended or
silently reenrolled. A live test needs its exact query and request preview plus a
separately authorized deletion-covered cohort arrangement. Until that is resolved,
preserve the installed working feed and do not deploy another partial increment.

Bonanza and eBid remain candidate integrations only. No credentials, accounts,
production access, data rights or working adapters have been established here;
no registration or key request was made. They are not counted as connected sources.

## Smallest next run and the actual remaining dependency

Proposed single run: exact `blue stone ring`, AUCTION/endingSoonest, US marketplace,
ZIP 99669, one page, at most three listings and primary photos. Upper bound:
one OAuth + one search + three seller-detail hydrations + three image fetches =
eight marketplace/image attempts, no retry/redirect/recurring discovery. Receiver
checks are separate. A new exclusive authorization marker must preserve every old
marker. Hidden existing Production credentials remain one-run-only. No paid AI,
bids, purchases or new server are needed; account-specific API/hosting charges
are not established.

Confirmed implementation dependency: PilotMatcher.operation permits one immutable
cohort; pilot_job.cycle rejects a retained seller outside it. Existing tests prove
changed enrollment fails even after receiver restart. This is our bounded access
model, not a requirement that eBay imposes immutable three-seller cohorts.

The smallest useful extension is one additional capped three-seller enrollment
in the existing receiver, using the existing authenticated connection and ledger,
with old enrollment/checks unchanged and local maintenance checking both retained
sets before serving the same feed. No new identity, remote host, generalized cohort
service, per-page proof or manual notice watching is required. This still needs a
small protocol/local-job update, synthetic deletion tests across both sets, and a
reviewed receiver deployment; it does not exist yet. Do not fetch new seller data
and discover this incompatibility after retention. Resetting the old registry or
restricting a supposedly broad search silently to old sellers is not a substitute.

The live executable is therefore pending that concrete integration, not blocked
by a blanket valuation restriction. One combined local update should carry it,
the improved email evidence and auction cards; no interim installer is warranted.
