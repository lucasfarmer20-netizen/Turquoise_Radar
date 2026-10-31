# JRI-004C: private cross-source hallmark research

The directory can now propose and review relationships across factual sources
without merging people or changing the frozen visual index. Run it locally; the
HTML report and all source evidence belong in ignored private storage. No network
client or inference provider is used by either new command.

## Evidence and decisions

`radar.references.research` proposes leads from names, explicit aliases, reviewed
literal lettering, description words, reported shop identities and reported periods.
Suffixes, initials and uncertain names remain distinct. Generic purity inscriptions
do not supply maker matches. Description overlap is a search aid, not pictorial
recognition; reported dates are context, not verified activity dates.

Every proposal starts UNKNOWN. A reviewed pair needs a dated reviewer, rationale,
uncertainty and exact quotations tied to both existing observations. Mark comparisons
also require distinctive mark evidence on both sides. The review distinguishes:

| Decision | Required interpretation |
| --- | --- |
| SAME_SOURCE_REPETITION | Repeated reported identity within one attribution family |
| INDEPENDENT_SOURCE_AGREEMENT | Compatible marks and identity, plus reviewed attribution provenance on both sides |
| CONFLICTING_ATTRIBUTIONS | Reviewed evidence of the same stamp attributed to different identities |
| SHARED_REUSED_MARK | Explicit source-reported shared, reused or co-stamped relationship |
| SIMILAR_UNVERIFIED | Compatible or similar mark evidence without established independent attribution |
| UNKNOWN | Insufficient or unresolved evidence, including name-only overlap |

Different domains are insufficient proof of independence. Art-Amerindien's library
and web records share a source family; Perry Null and Turquoise Traders are another
single family. The independent category requires quotations from an explicit
`attribution_provenance` field in each record, plus a reviewer assessment. This is an
auditable manual judgment, not automatic verification that a source tells the truth.
The older `relate(..., independently_assessed=True)` shortcut now rejects new uses;
existing events remain intact. None of these decisions authenticates jewelry.

The overlay saves immutable, content-addressed review packets and an append-only
directory event. It pins record observations and source reviews. Backup verification
detects missing packets, changed quotations, missing observations and inconsistent
decisions. Repeating the identical review is idempotent; a changed review creates a
new historical packet. Counts across all packets are historical review counts, not
necessarily unique pairs. Reports show one selected packet at a time and suppress
evidence whose current source review has expired or been revoked.

## Offline commands

```bash
.venv/bin/python -m radar.references.research data/jri-directory propose --query 'Synthetic Maker' --limit 25
.venv/bin/python -m radar.references.research data/jri-directory review data/private-assessment.json
.venv/bin/python -m radar.references.research data/jri-directory verify
.venv/bin/python -m radar.references.research data/jri-directory report data/jri-directory/research/reviews/REVIEW_SHA256.json data/jri-directory/research/report.html
```

An assessment JSON is a list of at most 25 unique pairs. Each supplies `left` and
`right` record IDs; `identity` (SAME_REPORTED, DIFFERENT_REPORTED, UNRESOLVED);
`mark` (UNASSESSED, COMPATIBLE, SAME_STAMP, SIMILAR, DIFFERENT); `independence`
(UNESTABLISHED, DEPENDENT, DOCUMENTED_INDEPENDENT); `reviewer`, timezone-aware
`reviewed_at`, `rationale`, `uncertainty`, and `support`. Evidence entries contain
`record`, `field`, and an exact `quote` from that observation. Optional
`mark_evidence`, `conflicting_evidence`, `shared_evidence`, and
`independent_provenance` support the stronger decisions. See synthetic regression
fixtures in `tests/test_cross_source_research.py` for complete examples.

The private report displays each candidate, transcription or symbol description,
all participating source citations, Hougart fifth-edition printed pages, source
families, dates, restrictions, supporting/conflicting evidence and uncertainty.
Unpaired imported factual records remain visible as UNKNOWN. External image links
require a deliberate click; no images, scripts or remote resources load in the report.
Regenerate reports after source permissions change; old exported reports are dated
historical artifacts, not current permission decisions.

## Narrow factual selections and rights

`radar.references.research_facts` imports a manually reviewed, hash-pinned packet:

```bash
.venv/bin/python -m radar.references.research_facts data/jri-directory data/private-facts-packet --sha256 PACKET_SHA256
```

`facts.json` uses `schema: jri-reviewed-facts-1`, a source and current `review_id`, a
`files` mapping of relative filenames to SHA-256, and up to 25 entries. Each entry
has a directory `record`, `text_file`, exact `quotes`, `review_note` and
`observed_at`. Retain the normal-access page, extracted text and response metadata
in the pinned files. This is an offline reviewer-authored selection, not an HTML
parser claiming to verify all factual paraphrases automatically. All supplied
payload hashes and quotes must pass. Records remain source attributed; no images
are accepted and transcriptions cannot be upgraded to visually LEGIBLE by this path.

The source review must explicitly allow factual use, bound the source record count
and collection paths, and preferably pin `selected_urls` and `selected_variants`.
An exact URL selection now takes precedence over path-prefix permission. The active
KayBen review pins four URLs and four variants. There is no KayBen or T.Skies live
directory adapter; these reviews do not authorize recursive collection. Imports are
transactional in SQLite and resumable with the original packet. A failed filesystem
copy can leave an incomplete private evidence directory; verification fails closed
until the same reviewed import is resumed successfully.

Normal HTTPS review on **2026-10-31** found:

| Source | Scope and result |
| --- | --- |
| T.Skies | Public terms and robots returned HTTP 200. Terms limit access to personal, noncommercial use. Retained for external manual research/citation; no factual ingestion approved here. |
| KayBen | Public policy, robots, index and four selected mark pages returned HTTP 200. No contrary extraction restriction found in the inspected linked policy. Bounded private facts and short evidence quotations only; copyright remains with source. Images remain external. Excluded admin/private-vault paths were not accessed. |
| Medicine Man, Horsekeeping | Earlier preserved restrictive reviews retained. Manual research/citation and possible permission requests; no new crawling. |
| Perry Null / Turquoise Traders | Preserved restrictive review and canonical-site relationship retained. No new crawling or duplicate independence credit. |
| Hougart / Art-Amerindien | Existing reviewed evidence reused locally. No additional book pages, images, training permission or expanded Art-Amerindien collection. |

No network-proxy block occurred in the selected requests. Restricted collection is
a permission decision, not a connectivity failure. No permission requests were sent.

## Bounded findings, 2026-10-31

Restored and verified the JRI-004B handoff before mutation: **503 payloads across
seven volumes, 637 active directory records, 120 book records, all 38 book evidence
files, 16 evaluation originals and the frozen 22-image index**. The already complete
active tree was retained. All 637 prior active records (639 total including two
excluded records), observations, corrections and book relationships remain intact.
Only the current KayBen/T.Skies review pointers advanced; previous reviews remain.

The new factual selection added **four records and zero images**, reaching 641
searchable records. Two new entries retain unresolved identities. No maker identity
was invented for an unresolved mark, and no material claim became verified evidence.

The pilot reviewed **18 relationships**: 12 cross-source pairs, including four
disambiguation controls, plus six within-source controls. It found:

| Decision | Count |
| --- | ---: |
| UNKNOWN | 11 |
| Similar/compatible but unverified | 1 |
| Source-reported shared/reused/co-stamped | 5 |
| Same-source repetition | 1 |
| Established independent source agreement | 0 |
| Established conflicting attribution of the same stamp | 0 |

Six exact source-name overlaps and one qualified compatible lettering overlap are
research leads. An additional uncertain name overlap remains unresolved. The
compatible case preserves the discrepancy between a fuller book inscription and a
shorter existing gallery crop. Shared initials or family surnames do not establish
conflicting attribution. The unpaired unresolved factual mark was not forced into
a relationship to meet a count.

Next useful evidence is a traced attribution history or maker/workshop documentation
for the strongest leads, plus legally permitted clear mark views where transcription
is missing. Another website repeating a name is insufficient. This pilot does not
measure recognition accuracy on the owner photographs, change the visual index or
justify training.

Use the existing `radar.references.handoff` backup/restore commands with all three
private components and `--volume-mib 30`. New research evidence, review events,
source restrictions and reports travel with the directory. Restore to a **new**
directory and verify every volume/payload; do not overlay or initialize a replacement
library when a handoff is missing. Keep `backup.json` and every part together outside
public GitHub. Model weights remain separately pinned public files as before.

Final verification passed **672 offline tests**, Ruff lint and formatting, and
whitespace checks. The final private handoff contains **571 verified payloads in
seven volumes**. A separate-directory restore reproduced 641 searchable records,
all 18 relationship reviews, the four factual imports, book evidence, original
evaluation photographs and the unchanged frozen index. The private backup index
contains volume digests and the exact restore command; no private artifacts are
included in this PR. The suite reports one existing Starlette/AnyIO deprecation warning.
