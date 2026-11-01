# JRI-011 — Execution follow-up, 2026-11-01

Started from merged main `68e181284f7469c7dd4073613bfe3705a23172ca`, containing
PRs #20 and #21, on `feat/jri-011-live-pilot-review`. This is a partial delivery:
the mobile defect is corrected and one existing ancillary case advanced, while
Production OAuth and Browse remain unverified. No production run occurred.

## Current environment and prepared run

Fresh environment-status and local-policy checks agree: desired and observed
runtime revision **2**, observations current, restricted network policy enforced.
Neither direct `EBAY_CLIENT_ID` nor `EBAY_CLIENT_SECRET` is present or ready, so
local HTTP Basic construction is not ready. `api.ebay.com` is absent from the
effective policy. This is current evidence, not an assumption from the earlier
report. The supported status metadata does not expose the environment's display
name. Exact environment/configuration/version IDs are in the private receipt.

The owner must publish/select the intended updated environment. Recheck its
identity, current revision, both direct bindings, effective API-host permission
and restored private application. Do not print credentials or authorization
headers. No separate OAuth probe, HTTP-secret workaround or proxy bypass is
authorized. Keep `VISION_PROVIDER=fixture` and `VISION_LIVE_ENABLED=false`.

The isolated application's actual preview still has exactly:

1. `blue stone ring`
2. `mixed jewelry lot`
3. `silver ring with blue stone`
4. `sterling jewelry lot`

Four queries, one page each, three returned observations/page, twelve total
including duplicates/invalid records; BOTH buying formats; `newlyListed`;
`auto_correct` omitted. Existing marketplace/destination context, timeouts and
bounded retries are unchanged. Once ready, present this exact preview for the
required per-run owner confirmation before sending any production request.

| Actual production outcome | Count/status |
|---|---|
| OAuth attempts / retries | 0 / 0 |
| Browse attempts / retries | 0 / 0 |
| Queries transmitted | None |
| Returned observations / unique items | 0 / 0 |
| eBay candidate handoffs | 0 |
| Completed buying evaluations | 0 |

No fixture results or public-web search results count as an eBay pilot.

## Ancillary research

One existing resale case was revisited through normal public-web document access.
Its asking price and generic shipping quote were re-observed; Alaska eligibility,
tax and buyer-fee evidence remain incomplete. A published hallmark attribution
provides a useful maker research lead. A separate initials collision remains a
caution, and the actual mark has not been visually inspected. A dealer's sold
label and displayed price were retained as research evidence, not entered as
verified realized proceeds or a financial comparable.

The existing structured intake retained the same candidate/workbench identity,
adding one observation/message with public-web research provenance and no exact
saved-search credit. An audited evidence-editor note records the research and
next evidence request. Historical observations and owner decisions are intact.
No prices, financial inputs, maker decisions, reference records or index entries
were changed. The case remains **RESEARCH**; no buying conclusion is claimed.
The smallest next evidence is a permitted sharp photograph of the complete inner
hallmark, followed by an item-specific Alaska checkout quote. Case IDs, URLs,
citations, timestamps and original evidence are private.

## Narrow mobile correction

The three reported workbench pages overflowed a 390px viewport to 404, 441 and
421px. The heading's flex child retained its minimum content width around long
source identifiers and descriptions. The fix allows that child to shrink and
wrap long tokens. A newly added citation also reproduced a 324px document at
320px; the resale-evidence paragraph now uses the existing wrapping prose class.

Tables keep their existing component scroll area. There is no blanket overflow
hiding. Prices, currencies, unknown costs/Alaska status and review links remain
visible. No calculations, source logic, production gates or schemas changed.

Committed browser coverage uses three clearly labelled synthetic cases containing
long source IDs, evidence URLs, shipping notes, citations and raw data, with known
and unknown amounts and a non-USD currency. The generator accepts only a new
`/tmp` database. The browser script allows loopback GETs only and exercises 390,
320 and 1440px, expanded evidence, component scrolling and `/identify` handoffs.

```bash
PYTHONPATH=. .venv/bin/python scripts/prepare-workbench-browser.py \
  --database /tmp/jri011-workbench-new.db
RADAR_DATABASE_URL=sqlite:////tmp/jri011-workbench-new.db \
  VISION_PROVIDER=fixture VISION_LIVE_ENABLED=false EBAY_MODE=fixture \
  EBAY_PRODUCTION_ENABLED=false EBAY_PRODUCTION_APPROVED=false \
  .venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8827
# In another terminal, with Playwright installed:
node scripts/check-workbench-browser.cjs
```

`CHROMIUM_PATH`, `WORKBENCH_BASE`, `WORKBENCH_IDS` and `WORKBENCH_REPORT` can select
an installed browser, private loopback instance, case IDs and private JSON output.
The default cases are the synthetic IDs. Private checks use the isolated copy.

## Verification and private continuity

- Full suite: **812 passed**; Ruff, formatting and whitespace checks passed.
- Synthetic browser: **9** case/viewport checks passed.
- Private browser: **12** checks passed, covering the three original pages and
  the advanced ancillary case. Document widths exactly match 320/390/1440px;
  tables scroll within their containers where needed. No browser errors or
  external requests. These checks establish software behavior, not purchases.
- Fresh protection baseline: **9,601 pre-existing private files**. The prior
  verified application archive was checked byte-for-byte. Its SQLite snapshot
  has different physical bytes from the active database but identical schema
  and every table row; both pass integrity and foreign-key checks. Active data
  remained authoritative and was never replaced with the snapshot.
- A fresh complete reference backup and separate restore verified **571 payloads
  across seven volumes**, **641 directory records**, **120 Hougart records**,
  the frozen **22-image index** and **16 evaluation originals**. A separate
  application/evidence archive protects **205 payloads and ten databases**;
  restored database contents were compared with their sources.
- Work proceeds only in `data/jri011-execution-20261101/application/pilot.db`.
  The previous application and historical trees remain intact. The final private
  handoff combines those reference volumes, historical applications and the
  current pilot/evidence, with pinned hashes and a fresh separate restore.

Private receipts, reports and backup instructions are under
`data/jri011-execution-20261101/` and
`data/backups/jri011-execution-final-20261101/`. Keep the entire final handoff when
starting a task in another environment; never restore over a newer private tree.
Reverify every payload before opening the restored application. The handoff is
excluded from Git. No reference images, original messages, databases, credentials
or real-case browser evidence belong in this PR.

Next: attach the intended published environment and reverify readiness, then
obtain explicit confirmation of the prepared run. Independently, obtain the
ancillary case's complete hallmark photograph. No paid inference, model training,
scheduled production run, bidding, purchase or automatic merge occurred.
