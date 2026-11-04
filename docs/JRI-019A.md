# JRI-019A — image-only continuation preflight

The saved JRI-019 cohort contains **eight distinct primary image references**, all
on `https://i.ebayimg.com`. Each matches the candidate's original Browse response
and a retained query observation. Read-only inspection at
`2026-11-04T04:05:39Z` (November 3, 2026, 7:05:39 p.m. AKST) confirmed eight
candidates, twelve observations, one discovery run, zero stored photos and zero
feedback. Database integrity/foreign-key checks passed and its bytes were unchanged.

## Minimum owner-operated policy change

Add **`i.ebayimg.com`** to the Turquoise Radar environment's **Additional allowed
domains**, then publish the updated environment using the supported settings.
Only HTTPS/443 is needed for this continuation. No wildcard, additional image
host, OAuth/Browse access change, direct connection, TLS exception, receiver or
infrastructure change is required. Both the current runtime readiness response
and executor policy omit this host; policy is enforced.

The assistant has not changed the environment or contacted the image host again.
After publication, verify current enforced policy in the environment actually
used for execution. Publication and image-download authorization are separate.
No connectivity HEAD/GET probe is part of this preflight.

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
  retained buffers; failed downloads also consume the transfer budget.
- Record each attempt durably before dispatch. Preserve the previous failed
  image attempt and Browse authorization receipts. A new image-only receipt must
  not reset or reuse those consumed markers.
- Retain exact validated bytes and SHA-256 in private storage (directory 0700,
  files 0600), associated with the existing candidate and saved source reference.
  Register seller-copy provenance before exposure; storage/registration failure
  must not leave an untracked image. No crop, retouch, invented pixels or upscale.
- Preserve listing, observation, query, financial and feedback records. Keep
  NEEDS INFO; failures remain in Needs Photos. Verify source-linked local image
  reads, deletion suppression, hashes and desktop/mobile counts after download.

The previous private downloader is **not qualified for unchanged reuse**: it
lacked strict MIME checking, exact-eight cohort binding, an aggregate byte cap,
a total download deadline and failure-atomic provenance registration. Existing
application photo serving assumes normalized PNGs; an exact-byte serving path
must be qualified before downloading JPEG/WebP. No new downloader was executed.

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

## Pending gates

Owner publication, observed policy readiness, the separate image-only approval,
qualified byte-preserving storage/display, and actual image/feed verification
remain pending. The current result remains **0 photos, 0 Open, 8 Needs Photos**.
This task sent no OAuth, Browse or image request. The original pilot and the
separate 27-item email application remain unchanged. PR #30 stays draft.
