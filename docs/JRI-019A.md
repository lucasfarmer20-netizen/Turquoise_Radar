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
