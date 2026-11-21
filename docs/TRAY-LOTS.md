# Tray lots: the describe-vs-depict gap

## Why this exists

Auction houses audit the claims they make, not the ones they omit. A catalogue
entry that calls a piece "Native American" invites a complaint, so it gets
checked. Items the catalogue never mentions invite nothing, so nobody looks at
them — including, frequently, the consignor.

That asymmetry is the whole opportunity in mixed estate trays. Specialists bid
their specialty and ignore the rest of the tray, so the tray clears cheap, and
the pieces that were never described draw no attention at any price.

`radar/tray.py` measures how much of a depicted lot the listing never names, so
those lots sort to the top of the owner's review queue.

## What it measures

Both sides of the comparison are deterministic.

**Depicted** uses only `VisionResult.sub_items` — the distinguishable research
leads the vision model was permitted to isolate. It is deliberately *not* a
count of everything in the photo. The vision prompt forbids estimating
"overlapping piece counts", and that restriction is preserved here: a tray
holding thirty pieces may yield four sub-items, and four is what this module
uses.

**Described** is a literal scan of the listing title and seller description for
item nouns (`ring`, `cuff`, `bolo`, `concho`, `ingot`, `heishi`, …). Matching is
plural-tolerant but has no stemming, so an unrecognised word simply fails to
match and its sub-item is treated as unnamed. That is the cautious direction for
a review queue: it over-flags rather than dismisses.

A sub-item counts as **unnamed** when its label mentions no item noun that the
listing also mentions — including the case where the label mentions no
recognised noun at all, since the listing plainly did not name it either.

## Scoring

| Component | Points |
|---|---|
| Base, when any gap exists | 10 |
| Each unnamed sub-item | 12, capped at 48 |
| Undescribed gold tone present | 30 |

Capped at 100. Categories are `TRAY LOT REVIEW`, `UNDESCRIBED GOLD TONE`,
`LOT FULLY ENUMERATED` and `NO TRAY SIGNAL`.

The gold-tone component is weighted highest because an unremarked gold-coloured
item in an otherwise silver tray is the clearest case of value the catalogue
never priced.

## Apparent tone is not a material claim

`SubItem.tone` (`GOLD_TONE`, `SILVER_TONE`, `OTHER_TONE`, `UNKNOWN`) records the
colour visible in the photograph and nothing more. A `GOLD_TONE` sub-item may be
brass, gilt, plated or gold-filled. Tone never establishes material, purity,
karat or value, never modifies financial or attribution evidence, and exists
only to route an item to the owner for physical testing.

The field defaults to `UNKNOWN` so findings cached under schema 3 remain valid
and are never silently reinterpreted as carrying a tone observation.

## Limitations

- Not a valuation. No weight, purity, melt or resale figure is produced or
  implied, and a high score says only that a lot is worth a closer look.
- Not a bidding instruction. The owner's discipline — bid no more than the
  *described* contents justify, so the undescribed remainder costs nothing — is
  a human decision this module does not encode.
- Sub-item counts understate dense trays by design.
- A listing that enumerates thoroughly scores zero even when the pieces are
  individually valuable; other signals cover that case.
- An alert-derived description is blanked upstream to keep private receipt
  evidence out of provider context. The gap then reads the title alone, which
  will understate the described side for email-sourced lots.

## Contract versions

Adding `SubItem.tone` and the tone instruction changed the analysis contract:
`PROMPT_VERSION` moved to `jewelry-4` and `SCHEMA_VERSION` to `4`.

## Not included

Query seeding already covers lot language through the `Lots and mixed jewelry`
and `Sterling Lots` groups in `radar/intake/registry.py`; no new sources,
scraping, alerts or paid APIs were added. Feed surfacing and an outcome log
(what each won tray cost against what it actually contained, which is what would
eventually let scoring learn which houses undercatalogue) are deliberately left
to a later task.
