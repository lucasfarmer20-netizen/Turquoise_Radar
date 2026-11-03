# JRI-014 — proposed simplified internal-tool closeout

**Decision pending.** This is a reviewed proposal for the owner-operated sourcing
app, not a record of accepted risk or authorized IAM mutation. The owner asked to
prepare it; explicit acceptance of the revised gate and authorization of the IAM
removal are still required. No production resources, keys, backups or permissions
were changed in this review.

## Minimal closeout

1. Record the owner's acceptance of the recovery limitation below instead of
   requiring the two-USB export or a timed recovery drill to close JRI-014.
2. Have the authorized Google administrator remove the reviewed temporary deployer
   access, independently of the deferred offline export.
3. Verify the existing receiver, private backup path and preserved identities after
   reduction. Then recommend PR #26 ready for review/merge. Keep it draft meanwhile.

The enabled Production keyset, registered deletion endpoint, signature checks,
durable deletion handling, Caddy, receiver, private runner, encrypted cloud backups
and narrow operational permissions remain in place. No activation or provisioning
is repeated. No actual duplicate eBay delivery or wire-level 204 receipt is required
for this closeout; their absence remains explicitly recorded.

## Proposed recovery limitation — owner acceptance required

> I accept deferring independent recovery-key custody for this internal tool.
> Existing cloud backups remain encrypted and verified at upload, but they are
> **not guaranteed recoverable after loss of the required on-host keys**. Neither
> independent key custody nor a four-hour recovery target has been demonstrated.
> This limitation replaces the mandatory two-USB export/timed-drill closeout gate;
> it does not waive eBay deletion handling or authorize an empty replacement ledger.

Record acceptance only after an explicit owner reply. No key or backup is deleted,
rotated, exported into Codex or treated as independently recoverable. Existing
backup timers, age checks, HMAC binding and per-operation fresh nonce proof remain
mandatory. A successful encrypted upload proves an upload, not survival of lost
recovery keys. The previous local quarantine timing does not demonstrate a full RTO.

If required keys or deletion-ledger continuity are lost, pause affected eBay-data
intake/import/export/replay/restore/reopening and receiver restart/redeployment
until complete valid state has been recovered and verified, or affected-data
removal/sanitized reconciliation has been completed and verified under an explicit
decision. Never use a new HMAC or empty ledger to bypass prior deletion obligations.
Unrelated isolated owner workflows need not be blocked. This is an operational
requirement: the current receiver constructor can create a missing queue file;
this proposal does **not** claim an automatic fail-closed receiver-startup guard.
No runtime change is made here.

## Optional future custody — one existing owner-controlled vault

When convenient, use an existing encrypted vault independent of the production
hosts/project, rather than requiring two USB devices or another service:

1. On an owner-controlled computer, use the existing approved export helper with
   its two required **new local encrypted staging files**. Those staging files are
   verification copies, not independent physical custody. Check the existing HMAC
   and public-key identifiers in the backup runbook.
2. Store one verified encrypted bundle in that existing owner-controlled vault.
   Retrieve the vault copy and compare its ciphertext SHA-256 with the original
   that the helper authenticated and decrypted successfully in memory.
3. Keep the bundle passphrase and vault/account recovery independently recoverable
   from the production machines. Retain only a non-secret receipt of verification.
   Remove temporary staging files when safely retained; do not claim secure erasure.

Call this verified vault custody only after owner verification. It is optional,
adds no infrastructure, and proves neither two-media custody nor a recovery-time
objective. Never put key values/passphrases in chat, Git or task output. The earlier
[WSL2 two-media instructions](https://github.com/lucasfarmer20-netizen/Turquoise_Radar/blob/803ee28cb92d559403268d3cab31f70fffd9632e/docs/JRI-014-CLOSEOUT.md)
and existing helpers remain available as optional historical tooling, not a new
required product milestone under the proposed policy.

## Reviewed IAM removal scope

Read-only observation: **2026-11-02 15:52:51 AKST
(2026-11-03 00:52:51 UTC)**. Project `turquoiseradar` is active, project number
`100948930799`, with no parent returned. These are observed direct bindings; final
permission denial and operational health are checked after authorized removal.

| Resource | Remove only | Current policy etag |
|---|---|---|
| Project `turquoiseradar` | The deployer member from the nine roles below | `BwZfTtWb3Gs=` |
| `tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com` | Deployer `roles/iam.serviceAccountUser` | `BwZfT2YuBis=` |
| `codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com` | The single dedicated subject's `roles/iam.workloadIdentityUser` binding below | `BwZfTtPff4w=` |

The exact deployer member is
`serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com`.
All listed bindings were unconditioned. The dedicated observed federation subject is:

```text
principal://iam.googleapis.com/projects/100948930799/locations/global/workloadIdentityPools/codex/subject/oai:v1:oic:oic_6ae8ccf36cec819487de78d9ed901222
```

The administrator must reread these policies immediately before applying, confirm
this exact member/role/condition scope, and compare afterward. Do not overwrite
whole policies from an old snapshot. The following commands are **prepared only;
execute after the owner's explicit revised-gate and IAM authorization**, under the
administrator's own approved identity:

```bash
set -euo pipefail
for role in \
  roles/compute.instanceAdmin.v1 \
  roles/compute.networkAdmin \
  roles/compute.osAdminLogin \
  roles/compute.securityAdmin \
  roles/iam.securityReviewer \
  roles/iap.tunnelResourceAccessor \
  roles/orgpolicy.policyViewer \
  roles/serviceusage.serviceUsageViewer \
  roles/storage.admin
do
  gcloud projects remove-iam-policy-binding turquoiseradar \
    --member=serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com \
    --role="$role" --condition=None --quiet
done
gcloud iam service-accounts remove-iam-policy-binding \
  tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com \
  --project=turquoiseradar \
  --member=serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountUser --condition=None --quiet
```

At this point, use non-mutating permission tests with the deployer to check that
Compute mutation, Storage mutation and runner actAs grants are absent. Do not issue
a destructive request as a test. Then remove only its observed federation subject:

```bash
gcloud iam service-accounts remove-iam-policy-binding \
  codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com \
  --project=turquoiseradar \
  --member='principal://iam.googleapis.com/projects/100948930799/locations/global/workloadIdentityPools/codex/subject/oai:v1:oic:oic_6ae8ccf36cec819487de78d9ed901222' \
  --role=roles/iam.workloadIdentityUser --condition=None --quiet
```

Also disable/retire the dedicated `turquoise-radar-deploy` provider/connection once
its exact mapping is checked by the administrator. Managed pool-list and targeted
provider-read requests returned `identity_unsupported_request`; this is not proof
that the Google provider is missing or that its state is disabled. The known pool
is `codex`, derived from the observed subject; do not delete this shared pool or
alter the read-only provider. Provider mutation remains conditional on the exact
administrator readback; no guessed provider command is supplied. Removing the
observed deployer impersonation binding already addresses that grant. Provider
revocation alone would not remove permissions from previously issued credentials.

Preserve the independently read-back protections:

- `codex-radar-reader`: project Compute Viewer and Storage Bucket Viewer, plus its
  distinct existing read-only federation subject; no new OS/admin grants.
- Runner: bucket-only Object Creator. Restore reader: bucket-only Object Viewer.
- Owner: Token Creator only on the separate restore-reader service account.
- Receiver: no attached service account. Runner: its existing dedicated identity.
- All production VMs, network, firewall, address, disks, bucket, encryption keys,
  backups, unrelated IAM members and organization policy.

At **15:54:17 AKST (00:54:17 UTC on November 3)**, the unchanged reader successfully
read both running VMs, confirmed the receiver's empty service-account attachment,
and the runner's expected attached identity. This is pre-reduction preservation
evidence, not a substitute for the post-reduction checks.

## Small post-reduction receipt

After authorized reduction, record only non-secret results:

- Deployer mutation/actAs permissions absent; dedicated federation no longer usable
  or retired. Distinguish a permission denial from an authentication/managed-policy
  failure and account for propagation before claiming completion.
- Existing reader authentication and read-only VM inspection still work.
- Public TLS succeeds; exact missing-challenge route returns receiver 400 and an
  unrelated route returns 404, without unintended redirects.
- Through approved owner/operator access, receiver and Caddy are active/enabled,
  backup and age timers healthy, and one fresh nonce-bound pull plus encrypted GCS
  upload advances the receipt with the current aggregate ledger counts.
- The owner's separate restore-reader access remains usable. This need not include
  another key-export ceremony or claim a measured RTO.

Use an authorized owner/operator for private-host checks, not a lingering deployer
SSH session or expanded reader permissions. Label independently observed versus
owner-provided evidence. Keep the PR draft until the revised policy is accepted,
removal is applied and these checks pass; then recommend ready/merge.

## Next product task

Use the existing bounded [four-query sourcing pilot](JRI-011-NEXT-PILOT.md) as the
next separately authorized task. This proposal authorizes no OAuth/Browse request,
purchase, bid, paid vision call, recurring search or unrestricted scraping.
