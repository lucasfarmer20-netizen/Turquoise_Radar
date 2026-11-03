# JRI-014 — accepted internal-tool closeout

**Owner approved the revised policy and IAM plan.** On 2026-11-02 Alaska time,
the owner explicitly selected **"Approve revised policy and IAM plan"** in response
to the reviewed recovery-limitation and scoped-removal question. The two-USB export
gate is replaced by the accepted limitation below; no additional approval is needed
for the reviewed removal scope. The owner reports executing the first removal
block in Cloud Shell; no mutation timestamp was supplied. Effective project/bucket
mutation and runner actAs checks now return no tested grants. Fresh literal-policy
readback remains blocked by the attached identities' reduced read permissions, so
Phase 1 is not yet fully verified. This verification made no IAM, resource, key or
backup changes. Federation remains unchanged.

## Minimal closeout

1. **Complete:** owner accepted the recovery limitation below instead of requiring
   the two-USB export or a timed recovery drill to close JRI-014.
2. Complete the fresh administrator policy readback for the reported first-block
   removal, then retire only the dedicated deployment federation binding. Provider
   retirement still requires its exact verified mapping.
3. Verify the existing receiver, private backup path and preserved identities after
   reduction. Then recommend PR #26 ready for review/merge. Keep it draft meanwhile.

The enabled Production keyset, registered deletion endpoint, signature checks,
durable deletion handling, Caddy, receiver, private runner, encrypted cloud backups
and narrow operational permissions remain in place. No activation or provisioning
is repeated. No actual duplicate eBay delivery or wire-level 204 receipt is required
for this closeout; their absence remains explicitly recorded.

## Accepted recovery limitation

> I accept deferring independent recovery-key custody for this internal tool.
> Existing cloud backups remain encrypted and verified at upload, but they are
> **not guaranteed recoverable after loss of the required on-host keys**. Neither
> independent key custody nor a four-hour recovery target has been demonstrated.
> This limitation replaces the mandatory two-USB export/timed-drill closeout gate;
> it does not waive eBay deletion handling or authorize an empty replacement ledger.

Acceptance is recorded from the explicit owner reply. No key or backup is deleted,
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
this policy does **not** claim an automatic fail-closed receiver-startup guard.
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
required product milestone under the accepted policy.

## Reviewed IAM removal scope

Read-only observation: **2026-11-02 15:52:51 AKST
(2026-11-03 00:52:51 UTC)**. Project `turquoiseradar` is active, project number
`100948930799`, with no parent returned. These are observed direct bindings; final
permission denial and operational health are checked after authorized removal.

| Resource | Remove only | Pre-removal policy etag |
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
whole policies from an old snapshot. The first block below is **owner-reported
complete** and retained as the exact removal scope, not an instruction to repeat
it. No mutation timestamp was supplied. Do not request policy/IAM approval again:

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

After fresh policy readback and effective-permission verification pass, the
authorized administrator's exact next step is the single-member removal below.
It is **not executed in this verification task**. Reread the deployer service-account
policy first and confirm this unconditioned member/role pair still matches; do not
change the reader service account or shared pool:

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

## Historical administrator execution handoff

After approval, read-only Google `testIamPermissions` calls at
**2026-11-02 16:09:52.554 AKST (2026-11-03 01:09:52.554 UTC)** all returned HTTP 200:

| Attached deployer permission check | Result |
|---|---|
| Project `resourcemanager.projects.setIamPolicy` | Not granted |
| Runner `iam.serviceAccounts.setIamPolicy` | Not granted |
| Deployer service account `iam.serviceAccounts.setIamPolicy` | Not granted |
| Sample Compute instance/network/firewall delete and Storage object delete permissions | Still granted; reduction has not occurred |
| Runner `iam.serviceAccounts.actAs` | Still granted |

The checks returned read-policy permissions but no required IAM setter. This is
an administrator-capability blocker; owner authorization is already recorded.
No IAM write was attempted, no role was added, and the read-only identity was not
repurposed. The owner was asked to have
the authorized administrator apply the first command block and report completion
and time. Check deployer mutation/actAs denial before applying the second block
that removes its dedicated federation subject. Provider retirement still requires
the exact administrator-verified mapping. The subsequent owner report and fresh
verification observations below supersede this earlier execution-pending status.

## Phase 1 verification — effective checks pass; policy readback pending

The owner reports removing all nine project roles and the runner
`roles/iam.serviceAccountUser` binding through the approved first block in Cloud
Shell. **The actual IAM mutation timestamp is unknown.** The following UTC times
are fresh observation times on **2026-11-03**, not reconstructed mutation times.
The existing manifest aliases were selected explicitly for each subprocess;
configured accounts were confirmed as deployer (`GCP_2`) and reader (`GCP`).

| Observation completed (UTC) | Check | Actual result |
|---|---|---|
| 01:25:02.952 | Deployer project `testIamPermissions`, 32 requested permissions | HTTP 200; zero granted |
| 01:25:02.932 | Runner service-account `testIamPermissions`: actAs/getIamPolicy/setIamPolicy | HTTP 200; zero granted, including `iam.serviceAccounts.actAs` |
| 01:25:03.203 | Backup bucket `testPermissions`, seven requested permissions | HTTP 200; zero granted |
| 01:25:03.766 | Reader GET of receiver VM | HTTP 200; existing instance `8560186650804972514`, RUNNING, no service account |
| 01:25:03.808 | Reader GET of runner VM | HTTP 200; existing instance `5443443387925882803`, RUNNING, original runner identity |
| 01:25:03.778 | Reader GET of backup bucket metadata | HTTP 200; same bucket, uniform bucket-level access enabled, public access prevention enforced |
| 01:25:02.944 / 01:25:02.941 | Project IAM read, deployer / reader | Google HTTP 403 `PERMISSION_DENIED`; policy unavailable, not empty |
| 01:25:38.967 / 01:25:40.540 | Runner IAM read through gcloud, deployer / reader | `PERMISSION_DENIED`; binding list unavailable |

The 32 project permissions cover Compute instance create/delete/start/stop/reset,
metadata/service-account/IAM changes and OS admin login; common instance metadata;
network and firewall create/update/delete; disk create/delete/resize/snapshot;
address create/delete; Storage bucket create/delete/update/setIamPolicy and object
create/delete/update; IAP tunnel access; and project IAM get/set. The bucket test
separately checks bucket IAM get/set, bucket delete/update and object
create/delete/update. These are successful permission-test responses with empty
grant sets, not failed authentication and not destructive mutation attempts.

Instance-specific permission-test attempts returned HTTP 403 at
**01:25:54.649 / 01:25:54.956** and do not establish an empty resource-level grant
set. A diagnostic retry identified missing `compute.instances.list` in the error;
no additional access was requested. The positive denial evidence above is scoped
to the successful project, bucket and runner-SA permission tests.

The normal CLI also could not reread reader/deployer/restore-reader SA policies;
bucket-policy reads returned HTTP 403. An initial alternate SA API resource form
returned managed `identity_unsupported_request`; this is kept separate from the
normal CLI's Google `PERMISSION_DENIED`. No reader role was expanded, no network
policy was changed and no existing resource or identity was modified. Narrow
bucket/owner recovery grants remain required and were not changed by this task;
their current binding lists were not independently reread here.

**Do not mark Phase 1 fully verified yet.** Permission loss supports the owner's
report but is not a substitute for the requested fresh literal-binding readback.
The administrator has been asked to run these read-only commands and return their
non-secret output. Both successful reads must show no matched deployer bindings
(`[]`); any remaining binding or command failure requires inspection. No new IAM
grant is needed, and the first removal block should not be repeated blindly.
The installed SDK's filter/flatten/projection was checked locally against synthetic
present, absent, empty and lookalike members. Only the literal member matched;
this is command validation, not production policy evidence.

```bash
date -u '+Observation UTC: %Y-%m-%dT%H:%M:%SZ'
gcloud projects get-iam-policy turquoiseradar \
  --flatten='bindings[].members' \
  --filter='bindings.members=serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com' \
  --format='json(etag,bindings.role,bindings.members,bindings.condition)'
gcloud iam service-accounts get-iam-policy \
  tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com \
  --project=turquoiseradar --flatten='bindings[].members' \
  --filter='bindings.members=serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com' \
  --format='json(etag,bindings.role,bindings.members,bindings.condition)'
```

Federation retirement and final service/backup/restore-reader checks remain
pending. The existing real notification and encrypted post-event backup evidence
is preserved; no activation, provisioning or sourcing was repeated.

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
owner-provided evidence. Keep the PR draft until the authorized removal is applied
and these checks pass; then recommend ready/merge.

## Next product task

Use the existing bounded [four-query sourcing pilot](JRI-011-NEXT-PILOT.md) as the
next separately authorized task. This IAM approval authorizes no OAuth/Browse request,
purchase, bid, paid vision call, recurring search or unrestricted scraping.
