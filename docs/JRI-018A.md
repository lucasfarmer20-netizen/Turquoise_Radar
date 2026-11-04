# JRI-018A — item photographs and reversible review decisions

This focused follow-up continues JRI-018 draft PR #30. It keeps the compact feed,
financial evidence gates and existing research views while improving the use of
actual photographs and adding an explicit return to neutral review. PR #30 stays
draft and retains the #27 → #28 → #29 → #30 dependency order.

## Item photographs on the card face

An optional private framing manifest binds each reviewed screenshot to its existing
photo identity, source listing, original/content hashes and image dimensions. A
full-item frame uses a bounded viewport over those same pixels; it does not
generate, retouch or replace an image. Original screenshots and the full research
viewer remain available separately. Unknown, mismatched or unreviewed framing
cannot provide a qualified item thumbnail.

The reviewed private cohort contains two usable full-item photographs: one for
an open opportunity and one for the ended auction. The scrap-lot screenshot
shows a hallmark detail rather than the full lot, so that record belongs in
Needs Photos. Shipping calculators and description screenshots are excluded from
the photo-first view. A separately uploaded FRONT item photo can still qualify
without screenshot-framing metadata. All seven original screenshots and all
research records remain stored.

Before applying local owner-decision filters, this gives one open photographed
opportunity, one ended photographed opportunity and 25 items needing photos.
These are counts from the isolated reviewed cohort, not a claim about the owner's
current local view. The owner reports saved NO GO decisions in a local
application that is unavailable to this task; those decisions must be preserved.

## Clear a sourcing decision

**GO** still records Interested and **NO GO** still records Passed. **Clear decision** is
an explicit owner action that appends a neutral **Cleared** event and returns the
record to RESEARCH. Earlier decisions remain in the audit history. The current
card has neither GO nor NO GO selected after a successful clear. A cleared
decision is not an invented Interested action, a purchase or a profitable lead.

Repeat submissions remain idempotent and conflicting stale submissions require
a reload. Purchased records remain in their existing workbench workflow. No
decision is cleared during an upgrade or merely by opening the feed.

Needs Photos uses compact rows with title, source, dated price when supported,
the original listing link, an Add / attach photo action and a Research link.
Unpictured records stay rows in Reviewed as well; their full Research view
provides the guarded Clear decision / Return to review action.

## Preserve the owner's current application

The delivery is a private **code-only upgrade**, not another application snapshot.
It contains changed application code/assets, verified framing metadata, a hash
manifest, an installer and an owner guide. It contains no database, original
emails, photographs or replacement feedback records.

The owner stops the current application and points the installer at that same
workspace. The installer reads its NEW-ONLY scope and SQLite integrity without
opening the database for writing. Existing Feedback rows are accepted at any
count; zero feedback is never an installation requirement. SQLite sidecars or
unknown target-code modifications stop the upgrade for review.

Before applying framing, the installer checks the existing owner/photo/source
bindings, file hashes, dimensions and crop bounds. A conflicting framing file
is not silently overwritten. Changed code targets must match the known JRI-018
base or the exact already-installed upgrade. Files are written atomically and
replaced code is backed up for a code-only rollback.

Before/after checks require unchanged database bytes and unchanged pre-existing
private evidence files. A rollback restores code only and preserves the current
database, including later owner actions. The original snapshot verifier must
not be used to reset a working database to its initial zero-feedback state.

The missing historical application remains separate. This update does not merge
research into historical records, relax deletion safeguards, activate Discovery
or paid inference, fetch marketplace data, or change receiver infrastructure.
