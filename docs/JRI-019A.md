# JRI-019A — recovered pilot and qualified image-only preparation

The original pilot was recovered **in place**, with no transfer or replacement
records, on November 4, 2026, at 8:10:47 a.m. AKST
(`2026-11-04T17:10:47.266208Z`). Its database hash matches the frozen plan, all six
preserved table hashes match, and its original eleven manifest/receipt hashes
remain intact. The requested PR #30 head `e5e8406e1104af1fb7e42dd33daa63ce2b71fa69`
was already checked out and was fetched from GitHub. No obsolete JRI-013 code or
replacement data was used.

The saved JRI-019 cohort contains **eight distinct primary image references**, all
on `https://i.ebayimg.com`. Each matches the candidate's original Browse response
and a retained query observation. Read-only inspection at
`2026-11-04T04:05:39Z` (November 3, 2026, 7:05:39 p.m. AKST) confirmed eight
candidates, twelve observations, one discovery run, zero stored photos and zero
feedback. Database integrity/foreign-key checks passed and its bytes were unchanged.

## Published policy and execution environment

The owner reports that **`i.ebayimg.com`** has been published in Additional allowed
domains and independently verified in the newer environment. That exact host over
HTTPS/443 remains the complete requirement. The older executor holding the original
pilot still reports an enforced policy without this host. This mismatch does not
require another owner allowlist edit: downloads must use the published environment
when its current policy is available to the executor doing the work.

No environment, receiver or infrastructure change was made, and the image host was
not probed. Publication and image-download authorization remain separate. The
download path requires explicit current runtime readiness and also checks the
executor's allowed-host snapshot before making any request.

## Proposed bounded continuation

After policy readiness, present this exact image-only plan for separate owner
authorization before dispatch:

- Use the existing isolated pilot database and eight saved primary references;
  validate frozen candidate, source-response and observation hashes first.
- At most **eight GET attempts**, one per retained listing. A failure consumes
  that listing's attempt. No retries, HEAD probes, alternate URLs, redirects,
  additional images, OAuth, Browse, listing-page requests or recurring jobs.
- Require HTTPS, exact `i.ebayimg.com`, port 443, no URL credentials/fragments,
  and exact equality with the saved approved reference. Preserve proxy routing
  and TLS verification; send no API authorization headers or cookies.
- Validate HTTP 200, Content-Type and matching decoded JPEG/PNG/WebP bytes;
  reject markup, animation, malformed images and unsupported formats. Bound each
  file to 10,000,000 bytes, the whole batch to 40,000,000 bytes, dimensions to
  16–10,000 pixels and total pixels to 24 million. Use 15-second HTTP timeouts,
  a 30-second total deadline per image and one-second request spacing.
  Reject non-identity Content-Encoding and enforce byte caps before extending
  retained buffers; failed downloads also consume the transfer budget. These
  bound accepted bytes: streaming can receive one extra chunk (at most 64 KiB)
  before aborting, and that excess is counted but not retained.
- Record each attempt durably before dispatch. Preserve the previous failed
  image attempt and Browse authorization receipts. A new image-only receipt must
  not reset or reuse those consumed markers.
- Retain exact validated bytes and SHA-256 in private storage (directory 0700,
  files 0600), associated with the existing candidate and saved source reference.
  Register seller-copy provenance before exposure; storage/registration failure
  must not leave an untracked image. No crop, retouch, invented pixels or upscale.
- Preserve listing, observation, query, financial and feedback records. Keep
  NEEDS INFO. Missing photos belong in Needs Photos only while the listing
  observations remain fresh; stale listings are withheld in every feed view.
  Verify source-linked local image reads, deletion suppression, hashes and
  desktop/mobile counts after download.

The previous private downloader remains historical and must not be rerun. The
implementation now lives in `radar.discovery.image_pilot`: `prepare_preview`
reads the existing database without migration/seeding; `execute_preview` requires
the exact preview digest, approval and policy readiness. A fixed exclusive marker
beside the database prevents replay even if a different receipt directory is
provided. Attempts are fsynced before dispatch. Each attempt uses a new client to
prevent cookie propagation, with no API credentials or OAuth/Browse capability.

Private storage now supports validated `.jpg`, `.png` and `.webp` original bytes,
the correct MIME, equal original/stored hashes and unchanged dimensions. It does
not strip metadata, re-encode, crop or generate pixels. Descriptor-anchored file
operations reject symlink paths. Full-photo and compact-photo routes recheck
source ID/reference binding, surviving candidate membership, bytes and freshness.
The source images are display-only; inference rejects them before provider use.

The existing deletion inventory is registered before image bytes or photo rows
become visible. Failed storage rolls back database changes and removes bytes;
any retained unused inventory entry is conservative. Exact source copies cannot
be classified as independent photos. Their correct suffix/hash is required for
deletion, and expired images remain purgeable. Fresh authenticated ledger and
identity-review requirements remain unchanged; no real deletion replay was run.

## Official use and retention constraints

The [API License Agreement](https://developer.ebay.com/join/api-license-agreement)
§3.1(b) allows necessary intermediate copies, deleted when no longer needed.
§8.1(b) requires removal of content no longer publicly available and separation
from non-eBay listings. §8.1(c) requires listing-display freshness within six hours,
other displayed content within 24 hours, and age disclosure. Downloading a photo
does not refresh a price or availability observation. These are display limits,
not automatic raw-evidence deletion deadlines; no historical-caption exemption
was established. No recurring refresh or additional Browse request is authorized.
Apply freshness per actual listing observation. This batch reaches six hours
around November 3, 11:24 p.m. AKST. Afterward, withhold stale listing content;
do not promise eight Open cards without an authorized freshness resolution.

§3.1(e)'s user-authorized modification language is qualified by the agreement,
and §9.11 restricts alteration. No clear image-specific permission for blanket
metadata stripping/re-encoding was found. Use exact-byte preservation and browser
sizing for this continuation rather than claiming the existing PNG normalizer is
qualified. Purpose-bound retention and applicable eBay/user deletion requirements
still apply; private storage does not establish indefinite archival permission.

The [official Browse schema](https://developer.ebay.com/develop/api/spec/browse_api.json)
identifies item-summary `image` as the primary image; additional images are
separate. Returned width/height fields are reserved, so validate downloaded bytes.

## Recovery and qualification results

All eight primary references match the saved raw responses and all twelve query
observations. SQLite integrity and foreign keys pass. Original history remains
one OAuth request, four Browse requests, and one failed image CONNECT attempt;
there are still zero photos, zero feedback and no replacement records. Both email
workspaces remain unchanged across their 215 and 211 inventoried files.

At recovery, every listing observation was about 14 hours 45 minutes old. A real
read-only browser check of the original database at 1440 and 390 pixels verified
that Open, Needs Photos and All show a count-only stale-source notice, withholding
all eight listing cards/titles. No database bytes changed and no external browser
requests occurred. The prior **0 Open / 8 Needs Photos** screenshot is historical;
current results are **0 displayed cards / 8 stale records retained**, not deletion
of those records. Image retrieval cannot restore listing freshness.

Synthetic qualification covers exact bytes/MIME/metadata, rejected formats and
symlinks, source reassignment, hash failures, tombstones, listing/image expiry,
all three formats' deletion, fixed execution limits, approval/policy gates,
cookie/header isolation and storage failure cleanup. No synthetic items were
inserted into the real pilot or represented as actual eBay opportunities.

The full repository suite passes: **1,373 tests in 177.12 seconds**, with one
existing upstream Starlette/AnyIO deprecation warning. Ruff and whitespace checks
pass; independent code review found no blocking defect. Legacy maker-photo reads
remain compatible. Final readback at `2026-11-04T17:28:43Z` confirms the database,
exact preview and eleven original receipts are unchanged. At
`2026-11-04T17:29:41Z`, both email workspaces still match all 215 and 211 baseline
file hashes. The actual read-only browser checks occurred at
`2026-11-04T17:18:07.797974Z`; these are stale-content suppression checks, not
successful real-image rendering.

A private exact eight-reference preview and a prepared deletion inventory are
available outside Git. The prepared inventory extends the original inventory
with this preview export; original manifests and receipts were not overwritten.
Approval of this image-only preview would permit byte download/validation only;
it would not authorize refresh of expired listing information or make eight
current Open cards possible from stale observations.

Remaining gates: separate owner image-only approval and an execution environment
that observes the already-published image permission. Actual downloads and real
photo rendering remain unperformed. No OAuth, Browse, image GET/HEAD, paid
inference or recurring request was made during this qualification. PR #30 stays draft.

## Follow-up: expiry while the feed stays open

A synthetic Chromium reproduction at checkpoint
`6e2429b84966484602288bef8f56be07613c6d80` loaded one production-shaped eBay
photo card shortly before its six-hour boundary. Advancing the browser clock
past that boundary left one card and one image visible; no external requests
occurred. Server checks worked on subsequent requests but could not withdraw
already-rendered content.

The feed now supplies a display deadline only for production eBay cards and
Needs Photos rows. It uses the six-hour listing limit and, for original source
photos, the earlier of the photo's own listing-observation limit and its 24-hour
fetch limit. Fetching a photo never extends a listing's lifetime. Existing
server eligibility, private-photo authorization and deletion checks remain
independent and authoritative.

The browser removes expired content, including links and decision controls,
without fetching or modifying anything. It checks on a deadline timer, page
restoration, focus, visibility changes and before interaction. Missing or invalid
deadline metadata on a marked card causes withdrawal. A monotonic elapsed-time
floor prevents backwards wall-clock adjustment from prolonging display. The
server keeps its inclusive expiry boundary. Browser withdrawal may be earlier
when delivery time or clock state cannot establish the remaining lifetime.

A count-only notice reports items withdrawn from this page and offers a manual
reload. Potentially outdated tab/pagination totals are hidden rather than
inventing counts for uninspected pages. An image-only expiry withdraws the
whole card conservatively; a manual reload can place the still-fresh listing
in Needs Photos. Original evidence and feedback remain stored, and non-eBay,
fixture and sandbox content retain their existing behavior.

This is a browser usability safeguard, not revocation of bytes already delivered.
It requires JavaScript execution; suspended pages recheck when the browser resumes
events. It does not remotely detect later deletion events, replace maintenance,
refresh marketplace observations, or change server-side review semantics.

Qualification uses synthetic records only. Run
`scripts/check-feed-expiry-browser.py` with the repository Python environment,
optional Playwright installed, and `/usr/bin/chromium`. All browser requests are
fulfilled locally through TestClient or blocked. Desktop/mobile scenarios cover
listing/image boundaries, photo-less rows, resume events, pre-interaction expiry,
invalid metadata, backwards clocks and unaffected fixture/non-eBay views. Each
scenario requires zero external requests, zero mutations, unchanged database and
photo-file bytes, no browser errors and no horizontal overflow. No production
key, dataset, receiver connection or paid service is required.

Final validation on November 4, 2026: **1,380 pytest tests passed in 58.85 seconds**,
with the existing Starlette/AnyIO deprecation warning; Ruff, JavaScript syntax and
whitespace checks passed. **24 Chromium scenarios** passed across 1440px and 390px
widths, with zero external requests or mutations and unchanged synthetic stored
bytes. The 57-test focused regression run also passed. No live pilot was executed.


### Review follow-up: delivery time, sleep and alternate link gestures

The monotonic floor now starts at navigation, so response delivery and delayed
script execution consume the available display lifetime. A missing navigation
entry, cache-only navigation (zero transfer size), history navigation or a client
wall clock behind the server check causes conservative withdrawal. The feed HTML
also returns `Cache-Control: private, no-store`; private image responses already
had that policy. These controls do not grant access or refresh observations.

A persisted page restoration, or return after visibility loss/window blur,
withdraws the remaining marked eBay content even if both local clocks froze.
This deliberately trades convenience for a verifiable display state: switching
away and back may require a manual reload despite still-fresh server data.
Ordinary first load remains usable, including native Chromium navigation without
clock mocking. No automatic reload, polling or network revalidation is added.

Capture-phase guards now include auxiliary clicks and context-menu events, as
well as ordinary clicks and form submission. An expired event is cancelled before
its link/default action. The browser cannot revoke a URL already copied, a native
menu opened before expiry, another already-open tab, or bytes already delivered.
If the OS freezes both clocks without any lifecycle/focus event, local code cannot
measure that missing elapsed time; server checks remain authoritative on the next
request. This patch does not claim a security boundary against disabled JavaScript,
modified browser clocks/code, or stale content retained outside this page.

The browser harness explicitly supplies navigation metadata when Playwright's
clock mock omits it, and separately tests native navigation without mocking.
Delivery, cache and frozen-clock cases are controlled simulations, not claims
about actual OS sleep or a production cache. All requests remain locally
fulfilled or blocked and synthetic stored bytes must remain unchanged.

Timed cards/rows are initially hidden in HTML, with a scoped CSS rule preserving
that state until JavaScript validates the deadline. Delayed or absent script
execution cannot leave the card visible; a no-script notice explains the hidden
eBay content. This does not claim that browser-prefetched image bytes are revoked.

Review-follow-up validation: **46 final Chromium scenarios** passed at 1440px
and 390px, including native navigation and blocked-script concealment. All had
zero external requests/mutations, unchanged synthetic stored bytes and no browser
errors/overflow. Serving the prior commit's JavaScript failed all eight targeted
new delivery/sleep/alternate-gesture checks, confirming those checks detect the
reported gaps. The final full suite passed **1,380 tests** (existing upstream
warning); Ruff, JavaScript syntax and whitespace checks passed.
