# JRI-004B — Reviewed offline book references

The private maker directory now accepts bounded, owner-reviewed published book
references without a live adapter or fabricated web URL. The first authorized
pilot added **120 source-attributed entries from nine printed pages**, bringing
the companion directory from **517 to 637 active records**. All 519 prior rows
(517 active, two quality-excluded), their observations, corrections, source
reviews and research evidence remain intact. The historical library and frozen
22-image/crop visual index are unchanged.

## What the pilot established

The reconstructed original archive, ten original photographs and provisional CSV
passed their previously recorded hashes. The owner reconciled the reissued pilot
ZIP as a packaging-version difference. Both the legacy expected wrapper digest
and the reissue's actual digest are retained: the legacy mismatch is not relabeled
as a successful check. Every supplied payload hash is still enforced. The older
wrapper itself was unavailable for independent byte comparison.

The private reviewed report is the correction authority. Three clear transcription
corrections and eleven refinements were accepted; original observations remain
separate. The import preserves 66 illustrated entries, two source-unknown marks,
one pictorial-only description, five shared/reused/co-stamped pairs, 20 lossy-text
collision groups and seven research identity links (five exact name overlaps,
one qualified overlap and one uncertain candidate). There are 113 entries without
a found name counterpart, not 113 authenticated new people. Two photographs of
one page support a single page identity. Repeated illustrations do not count as
independent evidence.

Search demonstrations return new names and alternate mark text with author, full
title, fifth edition, printed page, entry identifier and original-photo hashes.
Distinctive complete variants can supply research candidates. Shared-stamp text,
material-only wording, uncertain letters and insufficient initials retain UNKNOWN.
No direct cross-source attribution contradiction was established; the existing
directory's many unreviewed literal transcriptions limit that conclusion. Source
specificity gaps and homonyms remain explicit, with no destructive deduplication.

No accuracy improvement on the 16 owner photographs is claimed. Their maker-name
coverage differs from this alphabetic pilot. No owner-photo benchmark was rerun,
no photographs entered the visual index, and no inference or training occurred.

## Reviewed packet contract

`radar.references.book_source` validates `jri-reviewed-book-1` packets. A private
`book-review.json` pins every input's path, SHA-256 and byte count, the stable
`book:` source ID, title/author/edition, exact printed-page scope, exact entry IDs,
owner authorization, correction authority, source-dependence uncertainty and
permitted use. Bibliographic metadata can remain explicitly owner-supplied when
no title/copyright page was provided; a cropped page numeral is not presented as
visually confirmed.

The five required documents are the unchanged provisional CSV, reviewed JSON,
reviewed CSV, relationship file and collision file. The importer verifies their
agreement and retains all original fields. Each reviewed entry cites every
photograph of its page. Literal variants remain separate from pictorial prose,
spatial arrangement notes and material claims. Unknown marks have no invented
literal text. Illustrated status is not automatically a legible transcription.
Book description text is REPORTED evidence, not authentication.

The packet format is demonstrated entirely with synthetic data in
`tests/test_book_references.py`. Real packets, transcripts, corrections, scans and
crops belong only in ignored private storage. Optional ordered transport parts
retain a reconstructible original archive; their combined size and hash are
verified without extracting or executing attached instructions. Evidence files
are bounded at 32 MiB each and 512 MiB per reviewed packet. Unsafe paths, symlinks,
unlisted entry IDs, altered hashes and expanded-use flags are rejected.

## Import and repeatability

```bash
# Read-only validation and proposed record/relationship counts; no implicit init.
.venv/bin/python -m radar.references.books --root data/jri-directory import \
  data/private-book-packet/book-review.json --manifest-sha256 REVIEWED_SHA256

# Explicit bounded application; creates a verified pre-write directory backup.
.venv/bin/python -m radar.references.books --root data/jri-directory import \
  data/private-book-packet/book-review.json --manifest-sha256 REVIEWED_SHA256 \
  --apply --backup data/backups/new-book-before --max-records 120

.venv/bin/python -m radar.references.books --root data/jri-directory verify
.venv/bin/python -m radar.references.directory search 'REVIEWED MAKER OR VARIANT'
```

The importer requires the existing companion database and applies its ordered
schema-1-to-2 migration in the same transaction as the first imported prefix.
It never creates a replacement library. Stable edition/source, page and entry IDs
are independent of normalized names or initials. Every entry has its own unresolved
source identity; matching names and shared marks become research relationships,
not merged people. Foreign-key-backed relationship groups retain their evidence.

A repeated packet is a byte-preserving database no-op. A bounded committed prefix
resumes with the same packet and a new pre-write backup destination. The complete
review packet is retained under `book_sources/<manifest-sha256>/`, so resume also
works after restoring into a different directory. A failed transaction rolls back
the schema and records; verified orphan evidence can be reused on retry. Conflicting
replacements for an existing book entry are refused pending a separate correction
workflow. Nonoverlapping future entries need a new explicitly reviewed packet;
this pilot authorizes no additional pages.

Pinned offline editions do not inherit the web adapter's 30-day content-review
expiry. Explicit source-use revocation still hides their records and blocks resume.
Web host/path/robots policies, source limits and expiration rules remain in force.

## Private persistence and validation

```bash
.venv/bin/python -m radar.references.handoff backup data/backups/new-book-final \
  --volume-mib 30
.venv/bin/python -m radar.references.handoff restore data/backups/new-book-final \
  data/new-book-restored --manifest-sha256 PRINTED_BACKUP_MANIFEST_SHA256
.venv/bin/python -m radar.references.books --root data/new-book-restored/directory verify
```

The combined handoff includes historical references, companion records, the
self-contained reviewed book packet and the separate owner evaluation collection.
It verifies book inputs and relationships as well as SQLite integrity, foreign
keys, media hashes and owner-original hashes. Restore requires a new destination.
The 30 MiB payload-volume bound accommodates original transfer parts while staying
below the 32 MiB attachment limit; individual large files are not silently split.

The final pilot handoff verified **503 payloads in seven ZIP volumes**, all below
32 MiB. A separate-directory restore checked both databases and all book evidence,
retained all 16 evaluation originals, reconstructed the original camera archive to
its supplied hash and reproduced a zero-addition import. The original archive and
book photographs remain byte-identical.

The pilot passed the full **654-test offline suite**, Ruff lint/format and whitespace
checks. Synthetic regressions cover hash failures, page/entry bounds, correction
agreement, privacy restrictions, homonyms, shared-stamp abstention during partial
import, separate variants, unknown marks, transactional rollback, idempotency,
revocation and restoration/resume without the original workspace. One existing
Starlette/AnyIO deprecation warning remains.

Published attribution does not become independent corroboration, material assay,
authentication or training eligibility. Copyright and existing source restrictions
remain attached. No scans, private database or actual source records are committed;
no external crawling, third-party image inference, paid hosting or model training
is part of this workflow.
