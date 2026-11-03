# JRI-014 — accepted internal-tool closeout

**Owner approved the revised policy and IAM plan.** On 2026-11-02 Alaska time,
the owner explicitly selected **"Approve revised policy and IAM plan"** in response
to the reviewed recovery-limitation and scoped-removal question. The two-USB export
gate is replaced by the accepted limitation below; no additional approval is needed
for the reviewed removal scope. The owner reports executing the first removal
block in Cloud Shell; no mutation timestamp was supplied. **PHASE 1 IAM REDUCTION
VERIFIED:** independent permission/reader checks and the owner's administrator
policy readback establish the first-block reduction. The readback's observation
label is **2026-11-03T01:33:57Z**; it is not a mutation or exact read-completion
timestamp. The owner has since supplied the dedicated federation-binding removal
receipt, etag `BwZfVWtiID0=`. The fresh deployer credential exchange currently fails
at the managed broker; reader access and external HTTPS pass. Dedicated-provider
retirement and private service/backup checks remain pending. This verification
made no IAM, resource, key or backup changes.

## Minimal closeout

1. **Complete:** owner accepted the recovery limitation below instead of requiring
   the two-USB export or a timed recovery drill to close JRI-014.
2. **Phase 1 verified; dedicated federation-binding removal recorded.** The runtime
   manifest now resolves the separate deployment provider exactly. The authorized
   administrator verifies its live state and disables only that provider below.
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

Phase 1 verification passed. The owner subsequently supplied the successful
policy-update result for the single-member removal below. Retain this as the
completed scope; do not repeat it. The assistant did not execute the mutation.

```bash
gcloud iam service-accounts remove-iam-policy-binding \
  codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com \
  --project=turquoiseradar \
  --member='principal://iam.googleapis.com/projects/100948930799/locations/global/workloadIdentityPools/codex/subject/oai:v1:oic:oic_6ae8ccf36cec819487de78d9ed901222' \
  --role=roles/iam.workloadIdentityUser --condition=None --quiet
```

Also disable/retire the dedicated deployment provider. Earlier managed provider
reads returned `identity_unsupported_request`; that did not establish its state.
The runtime manifest now supplies the exact separate provider mapping recorded
below. Do not delete the shared pool or alter the reader provider. Provider
disablement does not revoke previously issued credentials; the project/actAs
removals and their effective-permission checks remain separate evidence.

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

## Phase 1 IAM reduction verified

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

The owner supplied the requested administrator Cloud Shell readback in command
order, resolving the literal-binding evidence gap:

```text
Observation UTC: 2026-11-03T01:33:57Z
[]
WARNING: The following filter keys were not present in any resource : bindings.members
[]
```

The first `[]` is the project-policy result; the second is the runner-SA-policy
result. No matching direct deployer membership remains in either returned policy,
including all nine named project roles and the runner service-account-user grant.
The warning is consistent with an empty binding list and does not invalidate the
empty result. This is **owner-supplied administrator evidence**, distinct from the
independent permission tests and working reader checks above. The printed UTC
timestamp precedes the reads; exact read completion and IAM mutation times were
not supplied and are not inferred. This filtered readback does not establish the
state of unrelated bindings or replace final operational verification.

**PHASE 1 IAM REDUCTION VERIFIED.** No additional read grant or repeated removal is
required. The commands below are retained to identify the completed readback.
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

The subsequent federation-binding receipt and current remaining checks follow.
The existing real notification and encrypted post-event backup evidence is
preserved; no activation, provisioning or sourcing was repeated.

## Dedicated federation binding removed; final checks in progress

The owner supplied this result after the exact reviewed removal command:

```text
Updated IAM policy for serviceAccount [codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com].
etag: BwZfVWtiID0=
version: 1
```

The returned policy has no bindings, supporting removal of the dedicated
`roles/iam.workloadIdentityUser` membership. This is owner-supplied administrator
evidence. No mutation/completion timestamp was supplied; none is inferred.

Independent observations on **2026-11-03 UTC**:

| UTC observation | Result |
|---|---|
| 01:48:34.214 | New-process deployer credential acquisition failed at the managed broker with HTTP 503 |
| 01:50:35.581 | Repeat deployer acquisition failed with HTTP 503 `identity_exchange_failed`; no upstream Google denial was exposed |
| 01:48:35.132 / 01:48:35.373 | Reader authenticated and read both original VMs: RUNNING; receiver has no SA; runner retains its existing SA |
| 01:48:35.637 | Reader read the same backup bucket; uniform bucket-level access and public access prevention remain enabled |
| 01:48:43.597 | Public root GET: CONNECT 200, actual HTTP 404, TLS verification 0, curl exit 0, no redirects |
| 01:48:43.876 | Exact deletion-route GET without challenge: CONNECT 200, receiver HTTP 400, expected missing-challenge JSON, TLS verification 0, curl exit 0, no redirects |

The managed exchange failures mean this task could not obtain fresh deployer
credentials; they are **not** a direct Google permission-denial receipt or proof
of provider disablement. No cached deployer token or old SSH session was used for
operational checks. Phase 1's successful permission tests remain the evidence for
the tested mutation/actAs denials. No network-policy workaround was attempted.

At **01:52:57.852 UTC**, the non-secret runtime identity manifest mapped:

| Identity | Connection | Exact provider under `projects/100948930799/locations/global/workloadIdentityPools/codex/providers/` |
|---|---|---|
| Deployer `codex-radar-deployer` / `GCP_2` | `oic_6ae8ccf36cec819487de78d9ed901222` | `turquoise-radar-deploy` |
| Reader `codex-radar-reader` / `GCP` | `oic_6ae7e0f9fa4881a185e188c44c7c304f` | `turquoise-radar` — preserve unchanged |

This resolves the configured mapping, not live provider state. The administrator
still must read live state: the exact provider read with the unchanged reader at
**02:00:48.608 UTC** returned managed `identity_unsupported_request`. The administrator
can now finish the already-authorized scoped retirement. First read the deployment
provider, confirm its exact full name and OIDC type, and inspect `disabled`:

```bash
gcloud iam workload-identity-pools providers describe turquoise-radar-deploy \
  --project=turquoiseradar --location=global \
  --workload-identity-pool=codex \
  --format='json(name,state,disabled,oidc.issuerUri)'
```

If that exact OIDC provider is active and not disabled, apply only this update,
then repeat the describe above. If already disabled, retain the readback without
repeating the mutation. An unexpected resource/type or failed read requires
inspection; do not guess or alter the reader provider/shared pool.

```bash
gcloud iam workload-identity-pools providers update-oidc turquoise-radar-deploy \
  --project=turquoiseradar --location=global \
  --workload-identity-pool=codex --disabled --quiet \
  --format='json(name,state,disabled)'
```

These commands passed offline SDK syntax/flag review and were not executed by the
assistant. Return only the provider name/state/disabled result and actual UTC
observation time. No issuer, audience or attribute-mapping change is requested.

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

### Owner/operator commands for the remaining private checks

The owner confirms there is **no currently verified complete owner/operator IAP
setup**. Previously created WSL2 Ubuntu 24.04 keys are
`/home/lucas_admin/tr-recovery-private/owner-iap` and `owner-iap.pub`; existence is
unrechecked, the earlier two-hour OS Login TTL must not be presumed active, and
prior connection tests failed for both VMs. No persistent pin file or Cloud Shell
key/pinning path is confirmed. Private checks remain pending fresh owner access.
Do not expand reader permissions or reuse deployer host access.

Expected instance IDs: receiver `8560186650804972514`, runner
`5443443387925882803`. The unchanged reader independently refreshed both public
host-key pins through authenticated Compute serial reads at **01:51:57.948 UTC**.
The prepared `jri014-public-known-hosts` contains only public host keys; SHA-256:
`43947b3b58b0de43fa31131eb0d0422367c22896a8513a2397a46e9cf13e9c84`.
No recovery key, new infrastructure or custody export is needed for these checks.

The local SDK review found that normal `gcloud compute ssh` puts its default
`~/.ssh/google_compute_known_hosts` before additional `--ssh-flag` options.
OpenSSH uses the first value, so an extra custom `UserKnownHostsFile` flag alone
does not reliably select the pins. The fresh-owner setup must install verified
pins into the file actually used, preserve unrelated entries and stop on a
conflicting host key. The earlier helper is not invoked for these checks.

#### Minimal fresh WSL2 owner session

Use the existing Ubuntu 24.04 WSL session as `lucas_admin`. Download the prepared
public-only `jri014-public-known-hosts` file from the task and place it at
`/home/lucas_admin/tr-recovery-private/jri014-public-known-hosts`. Do not upload or
paste either owner SSH key. Use the owner's existing Google login; if it is not
available, authenticate locally with `gcloud auth login lucas.farmer20@gmail.com`.
Use existing owner permissions; this procedure creates no service-account
credentials and grants no IAM roles.

First recheck the existing key pair and downloaded pins. Stop if either key file
is missing or the comparison fails; do not silently generate a replacement key.
Any key passphrase prompt stays in the owner's local terminal.

```bash
set -euo pipefail
umask 077
test "$HOME" = /home/lucas_admin
test "$(gcloud config get-value auth/impersonate_service_account 2>/dev/null)" = '(unset)'
owner_key=/home/lucas_admin/tr-recovery-private/owner-iap
owner_pins=/home/lucas_admin/tr-recovery-private/jri014-public-known-hosts
test -s "$owner_key"
test -s "$owner_key.pub"
chmod 600 "$owner_key" "$owner_key.pub"
owner_pub_fingerprint=$(ssh-keygen -lf "$owner_key.pub" -E sha256 | awk '{print $2}')
owner_derived_fingerprint=$(ssh-keygen -y -f "$owner_key" | ssh-keygen -lf - -E sha256 | awk '{print $2}')
test "$owner_pub_fingerprint" = "$owner_derived_fingerprint"
printf '%s  %s\n' \
  43947b3b58b0de43fa31131eb0d0422367c22896a8513a2397a46e9cf13e9c84 \
  "$owner_pins" | sha256sum --check
```

The impersonation preflight must pass: `--account` alone does not override a
configured service-account impersonation. Stop and inspect the owner configuration
if it fails; do not fall back to the removed deployer or reader.

Install only the authenticated missing pins into gcloud's actual default file.
This locally tested step preserves unrelated/hashed entries and stops on a
conflicting pin before appending anything:

```bash
python3 - "$owner_pins" "$HOME/.ssh/google_compute_known_hosts" <<'PY'
import pathlib, subprocess, sys
source, destination = map(pathlib.Path, sys.argv[1:3])
destination.parent.mkdir(mode=0o700, exist_ok=True)
if destination.is_symlink():
    raise SystemExit('Known-hosts path is a symlink; stop')
destination.touch(mode=0o600, exist_ok=True)
pending = []
for line in source.read_text().splitlines():
    fields = line.split()
    result = subprocess.run(['ssh-keygen', '-F', fields[0], '-f', str(destination)],
                            capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise SystemExit('Known-hosts lookup failed; stop')
    matches = [r.split()[1:3] for r in result.stdout.splitlines()
               if r and not r.startswith('#')]
    if any(key != fields[1:3] for key in matches):
        raise SystemExit('Existing host pin differs; stop')
    if not matches:
        pending.append(line)
if pending:
    with destination.open('a') as stream:
        stream.write('\n' + '\n'.join(pending) + '\n')
print('Verified host pins present; unrelated entries preserved')
PY
```

Define the guarded connection command in that same WSL shell. Each invocation
rechecks the existing VM identity, uses the existing owner key, and imports or
refreshes only its OS Login registration with a **30-minute TTL**. The prior
two-hour registration is not relied upon. No separate key-add command is needed.

```bash
owner_iap() {
  local vm="$1" expected_id
  case "$vm" in
    tr-ebay-notify-01) expected_id=8560186650804972514 ;;
    tr-ledger-runner-01) expected_id=5443443387925882803 ;;
    *) return 2 ;;
  esac
  test "$(gcloud compute instances describe "$vm" \
    --account=lucas.farmer20@gmail.com --project=turquoiseradar \
    --zone=us-west1-b --format='value(id)')" = "$expected_id" || return 1
  test -s "$owner_key" && test -s "$owner_key.pub" || return 1
  gcloud compute ssh "$vm" \
    --account=lucas.farmer20@gmail.com --project=turquoiseradar \
    --zone=us-west1-b --tunnel-through-iap \
    --ssh-key-file="$owner_key" --ssh-key-expire-after=30m \
    --strict-host-key-checking=yes \
    --ssh-flag=-oGlobalKnownHostsFile=/dev/null \
    --ssh-flag=-oHostKeyAlgorithms=ssh-ed25519 \
    --ssh-flag=-oIdentitiesOnly=yes
}
```

Connect with `owner_iap tr-ebay-notify-01`, run the receiver block below, then
`exit` back to WSL. Connect with `owner_iap tr-ledger-runner-01`, run the runner
block, and exit. Reconnect to the receiver for the final aggregate read. Return
only those safe results. A failed login or host-key check leaves private
verification pending; do not change IAM/firewalls or bypass strict checking.

On the receiver, run the following before and after the runner cycle; return only
the aggregate JSON and selected service fields:

```bash
sudo systemctl show ebay-receiver.service caddy.service \
  --property=Id,ActiveState,SubState,UnitFileState,ExecMainStatus,Result,NRestarts
sudo python3 - <<'PY'
import datetime, json, sqlite3
c = sqlite3.connect('file:/var/lib/ebay-receiver/queue.db?mode=ro', uri=True)
c.execute('BEGIN')
print(json.dumps({
    'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'events': c.execute('SELECT count(*) FROM events').fetchone()[0],
    'receipts': c.execute('SELECT count(*) FROM receipts').fetchone()[0],
    'integrity_ok': c.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
    'foreign_key_failures': len(c.execute('PRAGMA foreign_key_check').fetchall())
}))
PY
```

On the runner, start the existing fresh-nonce backup cycle and age check, recording
receipt advancement and unit status without printing subprocess errors or logs:

```bash
sudo python3 - <<'PY'
import datetime, json, pathlib, subprocess, time
def utc(stamp):
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()
p = pathlib.Path('/var/lib/tr-ledger-backup/success.json')
fields = ['completed_at', 'duration_seconds', 'event_count', 'receipt_count',
          'uploads_verified', 'encrypted_bytes', 'current_receiver_proof']
before = json.loads(p.read_text())['completed_at']
requested = time.time()
backup = subprocess.run(['systemctl', 'start', 'tr-ledger-backup.service'],
                        capture_output=True, timeout=310)
age = subprocess.run(['systemctl', 'start', 'tr-ledger-backup-age.service'],
                     capture_output=True, timeout=30)
d = json.loads(p.read_text())
now = time.time()
print(json.dumps({
    'observed_utc': utc(now), 'backup_requested_utc': utc(requested),
    'before_completed_utc': utc(before), 'after_completed_utc': utc(d['completed_at']),
    'backup_exit': backup.returncode, 'age_exit': age.returncode,
    'receipt_advanced': d['completed_at'] > before,
    'receipt_age_seconds': round(now - d['completed_at'], 3),
    'receipt': {k: d[k] for k in fields}
}))
PY
sudo systemctl show tr-ledger-backup.service tr-ledger-backup.timer \
  tr-ledger-backup-age.service tr-ledger-backup-age.timer \
  --property=Id,ActiveState,SubState,UnitFileState,ExecMainStatus,Result
```

Require both command exits 0, an advanced receipt, at least one verified encrypted
upload, matching current aggregate counts, and healthy enabled timers. Receipt
`current_receiver_proof=false` is expected: the existing cycle obtains fresh
nonce-bound proof internally; the saved receipt is not reusable proof. Stable
bracketing counts support the comparison but do not prove event-set equality.
No new observation is claimed until the operator returns results.

From the approved owner's authenticated Cloud Shell/computer, check the separate
restore-reader object-read path using the existing synthetic ciphertext. Discard
bytes, record exit status, and do not decrypt or export production keys:

```bash
date -u '+Restore-read observation UTC: %Y-%m-%dT%H:%M:%SZ'
if gcloud storage cat \
  gs://tr-ebay-ledger-100948930799/synthetic/recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb \
  --account=lucas.farmer20@gmail.com \
  --impersonate-service-account=tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com \
  --project=turquoiseradar >/dev/null; then
  restore_read_exit=0
else
  restore_read_exit=$?
fi
printf 'restore_read_exit=%s\n' "$restore_read_exit"
```

A successful read proves this access path, not decryption/recoverability or an RTO.
If the old synthetic object has expired, distinguish object absence from lost
access and select an existing ciphertext through the same approved reader.

After exiting all owner SSH sessions, remove only the just-used OS Login public
key registration from WSL. Retain the owner's local key files; do not remove
unrelated registrations. SDK 591.0.0 supports this exact-key selector:

```bash
gcloud compute os-login ssh-keys remove \
  --key-file="$owner_key.pub" \
  --account=lucas.farmer20@gmail.com --project=turquoiseradar --quiet
```

These are prepared owner-operated steps, not a successful live WSL session receipt.
The pin merge passed seven synthetic local cases; all command blocks were syntax
checked. No production private key was accessed during that validation.

## Next product task

Pending private-host JRI-014 checks do not block independently scoped
sourcing-feed development. Keep JRI-014 draft until its own final gates pass.
Use the existing bounded [four-query sourcing pilot](JRI-011-NEXT-PILOT.md) as the
next separately authorized task. This IAM approval authorizes no OAuth/Browse request,
purchase, bid, paid vision call, recurring search or unrestricted scraping.
