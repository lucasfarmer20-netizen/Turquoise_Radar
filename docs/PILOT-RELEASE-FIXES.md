# Pilot release regressions — offline repair

The independent promotion, legacy-display and combined-page findings were
reproduced with synthetic actual-schema records. Five regression cases initially
failed; the expanded regression file covers eleven cases.

## Changes

- sellers_in recognizes only the exact eBay Browse production promotion envelope
  before applying the existing typed hydration binding and stable-identity checks.
  Unsupported wrappers still stop maintenance; invalid typed evidence has no
  fallback. Both GO and NO GO retain maintenance coverage and subsequent deletion
  removes linked originals, observations and photos. No receiver change is needed.
- Production eBay rows are excluded from legacy dashboard/discovery cards; their
  detail routes redirect to the qualified feed. Legacy promotion refuses them.
  The actual feed POST checks source freshness before saving a decision. Retained
  data is not deleted merely because it is too old to display.
- The supervised pilot sets RADAR_PILOT_VIEW_ONLY. Only feed/opportunities, local
  photos, feed decisions/evaluation, source qualification, health and static assets
  are exposed; other workbench/edit/export/capture routes are explicitly gated.
  Ordinary separate manual/email applications do not inherit this flag. Production
  source cards no longer advertise unsupported research/photo-attachment links.
- All dynamic responses receive private,no-store, and the combined feed has the
  same local-image CSP as the compact feed.
- The combined template now has the withdrawal status/reload link, card/end markers
  and countdown hooks required by compact-feed.js. Conservative blur/hidden/resume
  withdrawal is preserved; the notice states that saved decisions are preserved.
  Reload rechecks local evidence, not marketplace availability. No auto-refresh,
  marketplace requests or browser-revocation framework was added.

## Verification

Focused actual-schema regressions exercise GO/NO GO with direct and hydrated
identities, deletion after promotion, malformed envelope rejection, fresh versus
72-hour-old records, legacy route suppression, stale decision rejection, and the
supervised pilot route allowlist. Existing application regressions remain included.

Browser reproduction (synthetic only; no downloaded browser/package needed here):

    PYTHONPATH=. .venv/bin/python tests/browser/render_pilot_release.py /tmp/radar-release.html
    python3 tests/browser/check_pilot_release.py /tmp/radar-release.html

The second command needs Playwright and Chromium in the testing runtime. It uses
intercepted local responses from the real rendered template, not an external site.
Desktop 1280px and mobile 390px check focus withdrawal with visible notice, reload,
recorded-end countdown/assessment, uncertain resume, and zero external requests.
HTTP/DB tests separately verify actual photo route freshness and removal.

## Delivery boundary

Local source repair only; no owner data, credentials, enrollment, receiver, live
acquisition or deployment changed. The review ZIP is source/test evidence, not an
installer. Installation must update the current source manifest together with the
files and restart maintenance only, preserving all attempt markers and enrollment.
Do not rerun acquisition ZIPs or overwrite an owner checkout with this review ZIP.
The automatic discovery worker remains separate unfinished work; these repairs
remove release blockers before it is enabled.
