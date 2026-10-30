# Task 001 validation

Validated in the supplied Python 3.12 cloud environment.

- `uv sync --frozen`: installed 26 locked dependencies; repeat offline sync succeeds.
- `python -m radar.cli seed`: 26 observations, 25 new listings, 1 duplicate update.
  Repeat seed: 0 new, 26 updates, still 25 distinct listings.
- `pytest -q`: **55 passed**. One upstream Starlette TestClient / AnyIO deprecation
  warning; no skipped tests, failures, live marketplace or paid API calls.
- `ruff check .` and `ruff format --check .`: pass.
- Uvicorn startup and local `/health`, dashboard and listing detail requests: pass.
- Chromium desktop 1440×1000 and mobile 390×844: dashboard and detail rendered,
  25 demo cards, no horizontal document overflow, no JavaScript/console errors.
- Real browser HTMX anomaly filter narrowed to one `nature america` listing.
  Feedback saved and survived reload. Normal manual-entry form and JSON paste
  import saved records in a separate temporary browser-test SQLite database.
- Browser testing caught and fixed a normal form Origin issue: `Referrer-Policy:
  same-origin` preserves local form Origin while withholding cross-site referrers.
- Demo database retains zero feedback events; browser mutation checks used a
  separate temporary database, not the prepared demo data.

Socket restrictions stall TestClient's event loop in the command sandbox here;
full tests, server startup and Chromium validation used the platform's approved
escalation mechanism. No assertion, TLS verification, or network policy was disabled.
Chromium's container launch used its standard `--no-sandbox` option; this does not
change the environment's network access policy.

Screenshots were inspected as transient `/tmp/turquoise-radar-*.png` artifacts.
The reusable environment draft has `install_script` and `start_skill` saved;
publication and restoration in a new task have not been tested or claimed.

## Publication audit

The complete Phase 001 implementation was published to GitHub `main` as
`535e508d8e2596b440447b5810bd8f0b77b553b5`. The subsequent publication audit
expanded `.gitignore` to cover local environment variants, common credential files,
virtual environments, SQLite files/sidecars outside `data/`, caches, build outputs,
and temporary/artifact directories. The non-secret `.env.example`, synthetic
fixtures, and vendored runtime assets remain versioned intentionally.

The hygiene follow-up uses a separate branch because `main` already has history.
No existing remote commits are overwritten. Ignore rules are a preventive measure;
review staged contents before committing, because arbitrary source files can also
contain secrets. The audit checks tracked paths and common credential signatures
without printing potential secret values. It is not a guarantee that a general
secret scanner would detect every possible credential format.


## Task 002 validation

- Branch `task/002-owner-calibration` starts at updated main
  `a7541b1e42f31d5f022d9325a30e249aab6cadfb` (merged publication-hygiene PR).
- **91 tests pass**: all 55 Phase 001 cases retained, with expected monetary values
  and explicit synthetic assumptions updated for the corrected resale model, plus
  36 calibration/capture/workflow/comparable/migration cases. No tests are skipped.
- Ruff lint and formatting pass. Existing upstream AnyIO/Starlette deprecation
  warning remains; no test assertion was disabled.
- Fee regression: a $50 sale with buyer shipping $8 and tax $4 gives $4 item-only
  commission and $2.10 processing on $62, including one $0.30 fee. With $1 packaging
  and $20 landed cost, net resale is $42.90, profit $22.90, ROI 114.5%, and maximum
  item price $18.81 when inbound shipping is $8 and targets are $15 / 60%.
- Fresh, populated Phase 001 fixture, repeat migration, future-version refusal,
  migration rollback and latest-feedback state preservation are tested.
- An additional check migrated a copy of the actual prepared Phase 001 SQLite DB:
  25 listings, 25 patterns, one preferences row and zero feedback rows; all prior
  column values and raw/original JSON were unchanged. No real data was re-seeded.
- Desktop 1440×1000 and mobile 390×844 Chromium checks exercised copied-text
  extraction, owner edits/confirmation, financial recalculation, queue state,
  purchase feedback, comparable capture/confirmation/linking, source effectiveness,
  HTMX filtering and nine mobile views, with no page overflow or browser errors.
  Browser mutations used a separate synthetic temporary SQLite DB.
- Direct official eBay/Whatnot documentation requests failed with proxy 403;
  numerical current quotas/production terms and current fee tiers are not claimed
  verified. Additions for developer.ebay.com and help.whatnot.com are saved to
  the environment draft, preserving api.github.com. They require user settings
  review/save and publication; they have not been applied to the runtime.

The implementation makes no live marketplace, paid vision, purchase or public
hosting calls. API-assisted PR creation remains blocked until api.github.com access is applied.
The task branch is prepared for publication without merging main. Saved Task 002
startup checks also passed against the migrated default database: health and all
six owner workflow pages responded with expected content.
