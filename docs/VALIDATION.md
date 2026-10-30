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
