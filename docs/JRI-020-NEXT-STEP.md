# JRI-020: one existing-location preflight before a live image beta

> Superseded proposal: use [the separate local launch plan](JRI-020-LOCAL-LAUNCH.md).
> Later documentation established no runner Browse credential route or external
> egress. Do not run the co-location preflight or request another owner session
> based on this historical proposal.

## Established evidence

The owner-approved probe of listing 297931689884 completed one OAuth POST and one
getItemByLegacyId GET with ADDITIONAL_SELLER_DETAILS: both HTTP 200, requested item
matched, and a nonblank seller.userId was present. Token and response stayed in
process memory; no seller identifier, response body or reusable mapping was kept.
This resolves **field availability for that listing/keyset**, not a cross-API
worked example or coverage of a new retained dataset.

The owner's pinned IAP checks established working receiver/Caddy, ledger integrity
and the existing fresh-pull/encrypted-backup cycle. At different times the counts
were 8,926 and 8,944; this is not event-set equality evidence. Zero receipts does
not demonstrate application-asset processing. A saved receipt with
current_receiver_proof=false is expected. Restore-reader authentication remains
inconclusive and is not a prerequisite unless this operation needs restoration.

## Chosen candidate location and the next action

Prefer a **single isolated pilot on the existing ledger runner**, with deletion
credentials staying where they already work. Do not add a broker, service, VM,
receiver, IAM role or polling system. Do not move an unqualified cohort between
machines. This is a proposed use of that runner, not verified permission or
readiness to host the application.

One owner action is needed now: in a fresh approved, strictly pinned IAP session,
run the prepared read-only preflight as the existing tr-ledger-backup account and
return only its aggregate JSON. The key registration from the previous session
was removed; do not assume that session remains usable or reuse the retired
deployer. Follow the existing owner IAP/key/pin procedure. Do not paste keys.

Supply the public source of `scripts/check-jri020-location.py` to Python's standard
input; no remote script installation is needed. From the owner host shell:

```bash
sudo -u tr-ledger-backup python3 -I - <<'PY'
# Paste the exact reviewed scripts/check-jri020-location.py source here.
PY
```

The script only reads the existing non-secret configuration and checks file
metadata, process permissions, interpreter/dependency presence and whether the
probe process already has eBay credential variables. It does not open HMAC key
contents, read ledger records, query the receiver, import app code, run maintenance,
start services, write files, inspect cloud metadata credentials or contact eBay.
Its output omits configuration values and paths. The wrong user fails closed.

Missing eBay variables mean only that they are absent from this probe process,
not that no approved credential mechanism exists elsewhere. Module presence is
not locked-version compatibility. No flag is a live clearance. A later authorized
one-shot package must match the tested dependency lock and reviewed code.

If this location lacks a safe existing credential route or sufficient permissions,
stop and report that concrete gap. Do not export deletion keys to the executor,
reuse the removed deployer, broaden the reader or silently provision anything.
If ordinary temporary code/dependency staging is needed, present its exact paths,
contents, cleanup and resource bounds for approval rather than treating it as an
existing capability. The runner currently proves backup execution, not beta hosting.

## Bounded follow-on job once location and identity gates pass

Reuse existing components:

- The receiver, pre-bound local ledger, approved pinned SSH alias and per-operation
  nonce/HMAC maintenance verification. No replacement or empty ledger bootstrap.
- Existing replay/sanitization, tombstones and strict identity/provenance holds.
- JRI-020's exact digest-bound query preview, twelve-observation bound, consumed
  authorization marker and fail-closed orchestration. Its current command is
  intentionally mock-only; do not change a fixture switch into live permission.
- Typed getItem hydration before durable retention, with exact item/seller binding
  and independent query history. Keep production-validation status explicit.
- Existing exact-byte JPEG/PNG/WebP validation, inventory-before-storage rules,
  private image serving, six-hour listing/24-hour image limits, and open-tab expiry.
  Reuse these primitives, not the old JRI-019 frozen-cohort executor or its data.

A real job requires an explicitly scoped **live launcher/serving gate**, not just a
success receipt. Use one fresh private directory, no exports or application-data
backups, and an inventory covering the DB, originals, photos and every retained
copy. Keep logs/receipts aggregate-only. Take fresh deletion proof/replay before
collection, after hydration and before image continuation/display. Register photos
before writing them and replay again before serving. Any proof/identity/inventory
hold stops requests and keeps the entire cohort unavailable. Coordinate against
concurrent backup/maintenance writers; do not assume an old backup receipt proves
current coverage. This preflight does not establish processing time at the current ledger size.

For the first beta, keep the app local to that host and use the existing approved
owner tunnel, with a bounded viewing session. New receiver notifications during a
session are not detected by browser expiry; the serving gate must withhold content
when current maintenance coverage cannot be established. Do not claim that one
startup replay or six-hour listing freshness solves later deletion obligations.
No public endpoint or ongoing unattended beta is part of this assignment.

The original four-query/twelve-observation proposal remains unexecuted. The
hydration variant needs fresh approval for at most 3 OAuth attempts + 12 search
attempts + 12 detail GETs (no detail retries) = 27 maximum. A later exact primary
image continuation adds at most eight single GET attempts, with no retries or
redirects, for a combined maximum of 35 if explicitly approved together. Images
are not currently authorized. The successful two-request field probe is a separate
consumed authorization, not permission for any of these requests.

## Remaining identity proof and decision boundary

Official Browse and deletion documentation support a common stable userId meaning,
and the probe confirms field availability. The cross-API identity contract has
not been independently validated with a worked example or authoritative explicit
mapping confirmation. Do not trigger a real account deletion as a test. Resolve
this with supported documentation/vendor clarification or appropriately authorized
existing evidence; never infer identity from a similar username. No IDs from the
probe were retained, so it cannot be retroactively correlated with a ledger event.
Incompatible notification identifiers or any ambiguous mapping still require a hold.

There is no need to decide on new infrastructure or repeat receiver setup. The
only immediate owner action is the existing-location preflight. Then present one
concrete placement/credential/serving plan and exact request preview; obtain any
required action-time approval before staging, granting access or dispatching.
If those conditions cannot be met narrowly, report that blocker rather than
building infrastructure around it.
