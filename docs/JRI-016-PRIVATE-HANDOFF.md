# JRI-016 — recover the existing private application

**PRIVATE_HANDOFF_NOT_ACCESSIBLE.** The owner confirmed the paths below are in a
previous Codex workspace and that no accessible transfer reference is currently
verified. The current workspace has no historical owner application directory.
Its JRI-015 synthetic preview is not a replacement. No full-history restoration,
real-data `/feed` qualification or current inventory count is claimed.

## Exact missing artifacts

From the original `/workspace/Turquoise_Radar/` workspace:

- Complete historical handoff: `data/backups/jri011-execution-final-20261101/`.
- Its transfer instructions: `data/backups/JRI-011-HANDOFF.md`.
- Last isolated working database, for verification against its existing snapshot:
  `data/jri011-execution-20261101/application/pilot.db`.

The handoff includes the original manifest and all referenced application,
history, photo/reference and evidence volumes. A database alone does not restore
the complete application. Do not replace this missing handoff with a new empty DB,
synthetic inventory, recreated decisions or the deletion runner's ledger backup.

## Smallest supported transfer

1. Reopen the original Codex task/workspace, if still accessible. Read its existing
   `JRI-011-HANDOFF.md`; locate the named handoff and original manifests. The current
   task has no tool for attaching or executing inside that historical workspace.
2. In that original workspace, verify every existing payload/volume against the
   original manifest and recorded hashes. Confirm the application snapshot retains
   all historical rows/decisions and that the documented separate restore still
   verifies. Report actual results; do not regenerate a manifest to hide missing
   files or treat a fresh checksum alone as historical verification.
3. Use the original task's supported private artifact download/attachment transfer
   for the verified existing volumes, manifests and handoff instructions. Preserve
   their names and volume relationships. No public GitHub artifact, repository
   commit, public bucket or new transfer service is needed. Do not include `.env`,
   credentials, live HMAC material, service-account keys or recovery secrets.
   If the existing archive contains such material, stop and arrange an approved
   data-only handoff; never expose it through chat or this executor.
4. Provide the resulting private attachment/reference to the destination task.
   Validate all original hashes again in a **new protected quarantine location**.
   Keep the source tree and original database unchanged. Preserve the receipt
   describing the source task, artifact names, manifest verification and UTC times.
5. Before opening or accepting any restored workbench, satisfy the existing
   [JRI-012 restoration gate](JRI-012.md): reviewed copy inventory, existing key-bound
   ledger, fresh authenticated nonce-bound receiver pull, integrity/foreign-key
   checks and completed replay with no unresolved coverage hold. Run this only in
   an approved owner-controlled context with its existing authorized access.

No new key export or infrastructure is part of this transfer. If the old workspace
cannot be reopened, the exact missing artifact remains the complete directory
above; software work and separately classified new notification analysis can
continue, but historical restoration and real-data feed qualification remain open.

## Existing ledger path is separate

The owner identified the existing receiver `tr-ebay-notify-01` (`10.84.13.2`), with
`/var/lib/ebay-receiver/queue.db`, and the approved runner `tr-ledger-runner-01`
(`10.84.13.3`), with protected `/etc/tr-ledger-backup/config.json` and
`/var/lib/tr-ledger-backup/ledger.db`. Its pinned, authenticated forced-command pull
and encrypted hourly backup path are existing operational components.

Their private SSH/HMAC credentials are **not transferable authorization for this
Codex workspace**. Do not copy those secrets here. A saved GCS backup is not fresh
receiver proof. The owner-accepted limitation remains: encrypted cloud backups are
not guaranteed independently recoverable if required on-host keys are lost; neither
independent recovery custody nor a four-hour RTO has been demonstrated.

No receiver/IAM/network changes, provisioning or eBay activation are required by
this handoff. The four-query Production pilot still requires its own actual preview
and fresh per-run approval after the private application and safeguards are ready.
