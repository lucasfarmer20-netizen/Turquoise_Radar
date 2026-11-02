# JRI-014 owner recovery custody and final IAM closeout

The real eBay acceptance and post-event backup at `8b5e2f3` remain valid. This
procedure does not repeat activation or provisioning. PR #26 stays draft until
owner custody, administrator IAM reduction and post-reduction verification pass.
The unobserved actual retry and wire-level HTTP 204 receipt remain unverified.

## Windows / WSL2 Ubuntu owner procedure

Run these steps on the owner's Windows computer, never in Codex or Cloud Shell.
The downloadable `jri014-owner-recovery.zip` contains only reviewed source,
instructions and a source/hash manifest; it contains no production credentials.
Keep the Linux working directory and authenticated CLI configuration on WSL's
Linux filesystem, outside Windows/cloud-synchronized folders. The two output
files go directly to two different removable physical devices. Use a trusted
owner-controlled computer with protected local storage, and no shell tracing or
terminal transcript during secret handling.

These commands target **x86_64 WSL2 Ubuntu 24.04**. The Ubuntu package/runtime and
synthetic helper tests were exercised in an Ubuntu 24.04 container. Windows WSL
installation, DrvFS mounts, owner authentication and actual physical media must
be checked on the owner's computer; the container does not certify those steps.

### 1. Install or select Ubuntu 24.04

In Windows PowerShell as Administrator, if this distribution is not installed:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if requested, create the Linux user when prompted, then in PowerShell:

```powershell
wsl --list --verbose
```

Require `Ubuntu-24.04` with VERSION `2`. If that distribution exists as version 1,
convert only it with `wsl --set-version Ubuntu-24.04 2`. Open it with:

```powershell
wsl -d Ubuntu-24.04
```

The commands below run in that interactive Ubuntu terminal. Stop on an error;
do not continue into the export after a failed prerequisite.

### 2. Prepare the Linux tools and reviewed source bundle

```bash
set -euo pipefail
set +x
umask 077
ulimit -c 0
test -t 0
test -t 2
test "$(uname -m)" = x86_64
sudo apt-get update
sudo apt-get install -y python3 python3-cryptography openssh-client curl ca-certificates
python3 -c 'import sys,cryptography; assert sys.version_info >= (3,12); assert int(cryptography.__version__.split(".")[0]) >= 41; print("Python/cryptography ready")'

recovery_work="$HOME/tr-recovery-owner"
test ! -e "$recovery_work"
mkdir -m 700 "$recovery_work"
read -r -p 'WSL path to downloaded jri014-owner-recovery.zip: ' recovery_archive
python3 -m zipfile -e "$recovery_archive" "$recovery_work"
cd "$recovery_work"
python3 - <<'PY'
import hashlib,json,pathlib
d=json.loads(pathlib.Path('SOURCE.json').read_text())
for name,want in d['files_sha256'].items():
    assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest()==want, name
print('Source files verified; reviewed commit:', d['source_commit'])
PY
```

A Windows download such as `C:\Users\OWNER\Downloads\jri014-owner-recovery.zip`
is `/mnt/c/Users/OWNER/Downloads/jri014-owner-recovery.zip` in WSL. Enter the real
path at the prompt; spaces are supported. Only the source archive is read from
that Windows path. The helper needs about 256 MiB for each Scrypt derivation;
leave adequate free WSL memory.

Install the tested Google Linux CLI archive. The fixed version and checksum
avoid silently changing the tested CLI. This uses TLS verification throughout.

```bash
curl --fail --show-error --location \
  --output google-cloud-cli.tar.gz \
  https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-591.0.0-linux-x86_64.tar.gz
printf '%s\n' '8b8c1be16284f7072359de05d31fe5ce52b6052fb295404a62a54b7d03ec4326  google-cloud-cli.tar.gz' | sha256sum --check
tar -xzf google-cloud-cli.tar.gz
export PATH="$recovery_work/google-cloud-sdk/bin:$PATH"
gcloud --version
```

If using a Windows ARM computer, stop before the x86_64 package step; a matching
ARM package requires separate validation. Do not run Windows `gcloud.exe` from
these Linux helper commands.

### 3. Authenticate as the approved owner and prepare pinned IAP access

Use a new dedicated CLI configuration. Do not copy the managed deployer's
federation files, use Cloud Shell, or create a service-account key. The approved
owner recovery operator is `lucas.farmer20@gmail.com`. This procedure assumes its
existing approved IAP/OS administrator access; it grants no project/IAM roles.

```bash
export CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true
export CLOUDSDK_CORE_LOG_HTTP=false
gcloud config configurations create jri014-owner-recovery
export CLOUDSDK_ACTIVE_CONFIG_NAME=jri014-owner-recovery
gcloud auth login lucas.farmer20@gmail.com --no-launch-browser
gcloud config set account lucas.farmer20@gmail.com
gcloud config set project turquoiseradar
```

Complete Google's login in your own browser; do not send login codes or credentials
to this task. If the named configuration already exists, inspect/reuse your own
known configuration deliberately instead of overwriting it. A service-account
impersonation/credential override must not be configured for the export. The
separate restore-reader impersonation is explicit only in the timing step.

The existing helper requires an SSH key. The following creates a new dedicated
owner-local key, uploads only its public half through OS Login for two hours, and
loads the private key into the local SSH agent. Choose its passphrase through the
hidden `ssh-keygen` prompts. This is an SSH access key, not a recovery-key export.

```bash
recovery_ssh="$recovery_work/owner-iap"
test ! -e "$recovery_ssh"
ssh-keygen -t ed25519 -a 100 -f "$recovery_ssh" -C jri014-owner-recovery
eval "$(ssh-agent -s)"
ssh-add "$recovery_ssh"
gcloud compute os-login ssh-keys add \
  --key-file="$recovery_ssh.pub" --ttl=2h --project=turquoiseradar \
  --format=json > owner-login-receipt.json
recovery_fingerprint=$(python3 - <<'PY'
import json,pathlib
d=json.loads(pathlib.Path('owner-login-receipt.json').read_text())
p=d.get('loginProfile',d)
key=pathlib.Path('owner-iap.pub').read_text().split()[:2]
matches=[k for k,v in p['sshPublicKeys'].items() if v['key'].split()[:2]==key]
assert len(matches)==1
print(matches[0])
PY
)
```

If owner login/import fails, stop and resolve the approved owner access with the
administrator. Do not grant broad roles or reuse the Codex deployer identity.
The export helper checks both fixed VM IDs, obtains host keys from authenticated
Compute serial output and requires strict pinned Ed25519 IAP SSH. Do not replace
that with `ssh-keyscan`, trust-on-first-use or disabled host checking.

### 4. Identify and mount two different physical devices

Connect two existing suitable removable devices. Do not format disks. In Windows
PowerShell, substitute their actual drive letters for the example `E` and `F`:

```powershell
Get-Partition -DriveLetter E | Get-Disk | Select-Object Number,FriendlyName,BusType
Get-Partition -DriveLetter F | Get-Disk | Select-Object Number,FriendlyName,BusType
```

Require different physical disk Numbers/devices, not two partitions on one disk.
Use existing NTFS media supporting DrvFS metadata. Linux mode bits do not replace
Windows ACLs or encryption; the recovery files themselves are encrypted. Back in
the same Ubuntu terminal:

```bash
read -r -p 'First removable drive letter, without colon: ' recovery_drive_one
read -r -p 'Second removable drive letter, without colon: ' recovery_drive_two
[[ "$recovery_drive_one" =~ ^[D-Zd-z]$ ]]
[[ "$recovery_drive_two" =~ ^[D-Zd-z]$ ]]
test "${recovery_drive_one^^}" != "${recovery_drive_two^^}"
sudo mkdir -p /mnt/tr-recovery-one /mnt/tr-recovery-two
if mountpoint -q /mnt/tr-recovery-one || mountpoint -q /mnt/tr-recovery-two; then
  printf '%s\n' 'Mount point already in use; stop and inspect.'
  exit 1
fi
sudo mount -t drvfs "${recovery_drive_one^^}:" /mnt/tr-recovery-one \
  -o "metadata,uid=$(id -u),gid=$(id -g),umask=077"
sudo mount -t drvfs "${recovery_drive_two^^}:" /mnt/tr-recovery-two \
  -o "metadata,uid=$(id -u),gid=$(id -g),umask=077"
mountpoint -q /mnt/tr-recovery-one
mountpoint -q /mnt/tr-recovery-two
recovery_stamp=$(date -u +%Y%m%dT%H%M%SZ)
recovery_one="/mnt/tr-recovery-one/TurquoiseRadar-$recovery_stamp"
recovery_two="/mnt/tr-recovery-two/TurquoiseRadar-$recovery_stamp"
mkdir -m 700 "$recovery_one" "$recovery_two"
python3 - "$recovery_one" "$recovery_two" <<'PY'
import os,pathlib,stat,sys,tempfile
for folder in sys.argv[1:]:
    fd,name=tempfile.mkstemp(prefix='nonsecret-permission-probe-',dir=folder)
    p=pathlib.Path(name)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(b'nonsecret removable-media readback probe\n')
            f.flush(); os.fsync(f.fileno())
        assert stat.S_IMODE(p.stat().st_mode)==0o600
        assert p.stat().st_uid==os.getuid()
        assert p.read_bytes()==b'nonsecret removable-media readback probe\n'
    finally:
        p.unlink()
print('Both media passed private-file creation and readback probes')
PY
```

Stop if either probe fails. Do not weaken permissions or silently redirect a copy
to the internal disk. These checks cannot prove physical placement; that remains
the owner's responsibility.

### 5. Export and verify the two encrypted copies

Run the existing reviewed helper from the owner computer only:

```bash
test -t 0
test -t 2
python3 deploy/ebay-deletion/export-recovery.py --owner-machine \
  --ssh-key-file "$recovery_ssh" \
  --copy-one "$recovery_one/turquoise-recovery.encrypted" \
  --copy-two "$recovery_two/turquoise-recovery.encrypted"
```

Enter a distinct recovery passphrase of at least 20 characters through the hidden
prompts. Arrange independent passphrase recovery; do not make its only recoverable
copy depend on these encrypted files, the receiver, runner or this task. Never
paste a passphrase or recovery key into chat, command arguments or environment
variables. The helper writes only encrypted files and verifies each by authenticated
decryption in memory. It refuses existing output files. A failure can leave one
encrypted copy; a failure is not a successful two-copy custody receipt.

Require the success JSON to report **encrypted_copies_verified: 2** and exactly:

- HMAC identifier: `0eb6b203e7fe4ffd`
- Encryption public-key SHA-256:
  `042d38760c4e132e24a072539d8abfc52f90cf37d6e1ef019fd1a85459524e40`

If either identifier differs, stop and report only the mismatch. Do not reveal
the underlying keys. Keep the non-secret success receipt. The timing drill below
can run while the first device is still mounted; it does not substitute for
final physical custody.

### 6. Optional new measured restore-reader drill

This uses the approved separate restore reader and the previously qualified
**synthetic** cloud object, not the writer or deployer. The source has a one-day
lifecycle; if it is unavailable, the helper fails closed. Do not substitute an
unreviewed source or create another object silently.

```bash
mkdir -p -m 700 "$recovery_work/drills"
test -t 0
test -t 2
python3 deploy/ebay-deletion/time-recovery.py --owner-machine \
  --recovery-copy "$recovery_one/turquoise-recovery.encrypted" \
  --destination "$recovery_work/drills/download-quarantine-$(date -u +%Y%m%dT%H%M%SZ)"
```

The helper unlocks the offline bundle only in owner-process memory and recomputes
both required key identifiers. It uses the approved owner login to impersonate
`tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com` for the download,
checks the known ciphertext checksum, and calls the existing protected-quarantine
validator. Only encrypted download bytes and the private quarantined ledger/manifest
are written; keys are not written as plaintext files. The destination must be new
with a private 0700 Linux-filesystem parent, not a Windows drive.

Its receipt records UTC start immediately before the cloud download, UTC completion
after checksum/decryption/quarantine validation, and monotonic elapsed time. Unlock,
setup and media retrieval are explicitly outside that interval. Success must retain
`offline_copy_only=true`, `current_receiver_proof=false` and
`operational_restore_performed=false`. Saved proofs never become current; future
operations still require the existing fresh nonce-bound barrier. Report only this
aggregate receipt. An untimed/failed run or the synthetic local tests do not
demonstrate a four-hour RTO. Even a timed success measures download-through-quarantine,
not restoration of live service or total disaster-response time.

### 7. Remove temporary owner SSH access and place the media

After export (and the optional timing drill), remove only the key created above:

```bash
gcloud compute os-login ssh-keys remove \
  --key="$recovery_fingerprint" --project=turquoiseradar --quiet
ssh-add -d "$recovery_ssh"
rm -- "$recovery_ssh" "$recovery_ssh.pub"
ssh-agent -k
sync
sudo umount /mnt/tr-recovery-one
sudo umount /mnt/tr-recovery-two
```

Safely eject both devices in Windows and place them in separate physical locations.
Do not erase the verified encrypted copies. Retain the private quarantine only
under owner-controlled protection; it is not a live or current ledger.

Send only this custody confirmation, after physical placement is complete:

> Two encrypted copies were written and independently verified. HMAC identifier:
> `0eb6b203e7fe4ffd`. Encryption public-key SHA-256:
> `042d38760c4e132e24a072539d8abfc52f90cf37d6e1ef019fd1a85459524e40`.
> The copies are on separate physical media in separate physical locations.
> The passphrase is independently recoverable.

Do not include locations, key values, passphrases, private keys or plaintext exports.
Custody remains **pending** until the owner supplies this verification.

## Administrator IAM handoff — gated on verified owner custody

No IAM reduction is authorized to run ahead of the custody gate. After the receipt
arrives, refresh effective grants and prepare the final exact removal diff using
current policy etags. Have the authorized Google administrator apply it under
their own administrator identity. The following is the removal scope, not a
claim that fresh policies have already been inspected or reduced:

| Resource | Exact removal scope |
|---|---|
| Project `turquoiseradar` | Only `serviceAccount:codex-radar-deployer@turquoiseradar.iam.gserviceaccount.com` from the nine temporary roles below |
| `tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com` IAM | Only that deployer's `roles/iam.serviceAccountUser` binding |
| Deployer service-account IAM | Exact observed temporary WIF principal memberships granting deployer impersonation; inspect conditions and preserve unrelated members |
| Dedicated `turquoise-radar-deploy` provider/connection | Disable/retire only its freshly resolved exact provider resource and dedicated connection; preserve the read-only provider and pool |

The nine previously observed unconditioned project roles are:

```text
roles/compute.instanceAdmin.v1
roles/compute.networkAdmin
roles/compute.osAdminLogin
roles/compute.securityAdmin
roles/iam.securityReviewer
roles/iap.tunnelResourceAccessor
roles/orgpolicy.policyViewer
roles/serviceusage.serviceUsageViewer
roles/storage.admin
```

Fresh inspection must cover project ancestry/inherited grants, project policy,
deployer/runner/restore-reader service-account policies, bucket policy, relevant
resource-level policies, the exact WIF pool/provider, and tested effective
permissions. Resolve conditions, custom roles or group-based grants before claiming
the effective audit complete. Do not guess a pool ID or remove a shared principal
at another resource. If the managed identity cannot read a policy, obtain that
non-secret readback from the authorized administrator rather than widening access.

The review must verify unchanged reader project roles/federation, runner bucket-only
Object Creator, restore-reader bucket-only Object Viewer, the owner's narrowly
scoped Token Creator, and the receiver's empty service-account attachment. Retain
production VMs, disks, network, address, firewall and bucket resources. Do not change
unrelated IAM, org policies or APIs. Provider disablement alone does not remove
already issued credentials' privileges; resource-role removal and effective
permission verification are required too.

Use member-specific removals with the observed condition (or `--condition=None`
only when confirmed unconditioned), then compare all policies to the reviewed
diff. Do not apply a stale whole-policy replacement. Remove the runner actAs and
project mutation grants, verify denial with non-mutating permission tests, then
remove dedicated federation access and disable the exact provider. No destructive
deployment request is needed to prove denial. Do not carry a deployer SSH session
across reduction as a substitute for an authorized post-reduction operator path.

The exact current WIF principal/provider resource names and reviewed administrator
commands will be recorded after custody confirmation and fresh readback. They are
not fabricated from earlier display names.

## Required post-reduction evidence

Independently inspect the reduced policy and effective deployer permissions:
Compute instance/network/firewall/disk mutations, Storage writes/deletes and runner
actAs must be absent; fresh deployer federation must fail or be retired. Confirm
the unchanged `GCP` reader can still authenticate and read receiver/runner resource
state. Record propagation/timing limits and distinguish authentication failure
from a tested permission denial.

Verify public HTTPS with normal TLS and exact missing-challenge route 400 plus
unrelated-path 404. Through the approved owner/operator authenticated path, inspect
receiver/Caddy active+enabled state, backup/age timers, and run a fresh nonce-bound
private pull plus encrypted upload with advancing receipt and aggregate ledger
counts. Use no deployer credentials. The existing reader is read-only cloud access,
not an OS administrator; do not add SSH rights to it. If only owner-operated
receipts are available for private-host checks, label their provenance and keep
any unmet independent-verification gate open rather than silently upgrading them.

Verify the owner's restore-reader download path still works. Inspect logging
configuration and aggregate checks without exposing private journal content; scan
Git and the final diff for secrets/payloads. Run the full repository suite and
review the complete PR diff. Retain the real-event receipt and qualification limits.
Only after custody, reduction and all post-reduction checks pass may the PR be
recommended ready for review and merge. No Browse pilot or paid inference call is
authorized here.

## Reference and test scope

The platform steps follow Microsoft's [WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install)
and [DrvFS permission](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)
documentation. The pinned CLI follows Google's [Linux installation guide](https://cloud.google.com/sdk/docs/install-sdk).
Actual WSL/media authentication and the production custody/timed recovery results
are owner operations. Local synthetic tests cannot complete those gates.

Validation of this preparation: 975 repository tests passed, including 21 new
synthetic custody/timing cases; those 21 also passed in an Ubuntu 24.04 container
with networking disabled (Python 3.12.3, cryptography 41.0.7). Google CLI 591.0.0
loaded successfully there, the source-only bundle extracted and verified, and
all eight Bash blocks passed syntax checks. Ruff, formatting, whitespace and final
PR-diff review passed. Windows/DrvFS and real owner execution remain untested here.
