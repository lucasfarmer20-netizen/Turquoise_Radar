# JRI-014 private ledger backup operation

The owner approved `tr-ledger-runner-01`, a private non-Spot e2-micro in
us-west1-b with a retained 10 GiB pd-standard disk and the dedicated
`tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com` identity. Conservative
incremental planning cost is $6.51/month; receiver/cloud-backup plus runner is
$17.58/month before tax and the existing exclusions. No NAT, load balancer or
managed backup service is included.

Actual deployment and blockers are recorded in [JRI-014](JRI-014.md). These
instructions do not establish a working schedule or a successful cloud recovery.

## Package and runtime

`deploy/ebay-deletion/package-backup.sh NEW_ARCHIVE REVIEWED_COMMIT` builds an
explicit allowlist from an immutable Git commit. It includes no owner data,
configuration, keys, workbench, credential files or working-tree artifacts.
Extract only that reviewed archive into a new root-owned `/opt/tr-ledger-backup`.
The runner uses Ubuntu's Python 3.12 and `python3-cryptography` (observed 41.0.7),
OpenSSH and trusted system CAs; no gcloud, cloud key file or ordinary internet
connection is needed. New tests also run against cryptography 50.0.2. The added
cryptography dependency is development-only; it does not enlarge the receiver's
locked runtime closure.

Use a distinct unprivileged `tr-ledger-backup` OS account. Protect
`/etc/tr-ledger-backup` as root:tr-ledger-backup 0750, the dedicated SSH private
key and HMAC assignment as 0640, and `/var/lib/tr-ledger-backup` as 0700. The
root-owned `config.json` names `state_directory`, `hmac_file`, `ssh_alias` and
`encryption_public_key`; it contains paths rather than key values. The fixed SSH
alias uses receiver private IP 10.84.13.2, the reviewed forced ledger account,
strict checking and the Ed25519 key obtained via authenticated Compute serial
output. The runner's source IPv4 is 10.84.13.3/32. Preserve the forced command,
no PTY/forwarding and no direct receiver-config access.

Generate a separate RSA-4096 recovery key. The writer receives only its public
key. The private key is root-only under `/root/ledger-recovery/decryption.pem`;
that on-host copy alone is **not durable independent recovery custody**. An
owner-controlled secure export or existing approved vault must preserve it and
the HMAC assignment independently before recovery is qualified. Never place keys
in a backup, ordinary manifest, Git, logs, chat or task deliverable. Secure export
is an owner/admin operation through IAP, not a request to paste secrets into chat.

Explicitly create and bind an empty runner ledger under the existing receiver
HMAC assignment during approved setup. Scheduled cycles refuse missing or
incorrectly bound ledgers; they never auto-bootstrap a missing source.

## Cycle, encryption and storage

`cloud-backup.py cycle --config /etc/tr-ledger-backup/config.json` first obtains a
fresh nonce-bound authenticated receiver snapshot via the existing live barrier.
Every run repeats that barrier, even following a recent success. It then checks
key binding and SQLite integrity and makes the existing consistent backup.

Only the fixed `ledger.db` and `manifest.json` enter the plaintext tar. A new
random AES-256-GCM key/nonce encrypts every cycle. RSA-OAEP-SHA256 wraps the AES
key; the versioned envelope header is authenticated as additional data. The
RSA-4096 private key and receiver HMAC key are never included. Plaintext tar size
is bounded to 64 MiB before reading/encrypting it; the existing fresh-pull bundle
limit remains 16 MiB. A size failure requires review, not silent limit expansion.
Temporary plaintext files reside only in a private directory on the encrypted
runner disk and are removed on normal completion/failure. Abrupt termination can
leave a private temporary directory; do not claim forensic secure erasure.

The runner verifies its expected metadata identity and obtains its short-lived
cloud token through GCE metadata. It uploads only to
`tr-ebay-ledger-100948930799` using a timestamp plus 128-bit random object suffix
and `ifGenerationMatch=0`. It verifies the upload response's bucket, exact name,
generation, byte size and server MD5 checksum under HTTPS. It performs no object
GET, list or delete, and requires only bucket-level `roles/storage.objectCreator`.
The GCS response supplies upload verification without expanding the writer.

Every run writes an hourly copy. The first successful run of each UTC day/week
also writes a daily/weekly copy. Proposed lifecycle rules delete `hourly/` after
1 day, `daily/` after 7 days, `weekly/` after 28 days and `synthetic/` after 1 day.
Retain Standard class, us-west1, uniform bucket access, enforced public-access
prevention, 1-day unlocked retention and explicit 7-day soft deletion. Lifecycle
processing is asynchronous, not an exact object-count guarantee. Include retained
and soft-deleted bytes in the planning allowance. No retention lock is requested.
The owner subsequently created the bucket. Current readback confirms its base
security/retention settings, but lifecycle and labels are absent; see the latest
qualification handoff below. No lifecycle success is inferred from owner commands.

Only after **all** intended uploads pass does an atomic fsynced aggregate receipt
advance. It contains completion time, duration, counts, encrypted size and tier
markers, with no account identifiers, signatures, keys or object names. Partial
upload failure leaves the prior success receipt unchanged; retries use new names.
Receipts and manifests explicitly are not current receiver proof.

## Schedule and failure visibility

Install the four supplied systemd templates as `tr-ledger-backup.service`,
`tr-ledger-backup.timer`, `tr-ledger-backup-age.service` and
`tr-ledger-backup-age.timer`. The backup timer is hourly and persistent; the age
timer checks every five minutes and fails for a missing receipt, clock moving
behind the receipt, or age greater than 90 minutes. Errors emit a fixed aggregate
failure code, not exception text or SSH/cloud response bodies. Systemd records a
failed service and journal entry. This is local failure visibility; no external
alert delivery or paid monitoring service is configured.

Keep timers disabled until private SSH/HMAC, the approved bucket configuration
and writer permissions, and an initial explicit upload pass. Verify `success.json`
and the real age check, enable both timers, and observe scheduled success and
failure/age behavior. The latest owner instruction permits this before completion
of the separately gated cloud recovery/offline custody work. The backup timer now
includes a one-minute start-up trigger as well as the hourly persistent calendar;
a start-up execution alone does not prove an elapsed hourly RPO. One-hour RPO is a target until scheduled operation is
demonstrated; four-hour RTO requires the full timed cloud recovery drill.

## Separately controlled recovery

Proposed steady-state recovery principal:
`tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com`, with
`roles/storage.objectViewer` on this bucket only and separately approved access
to use that identity. The account now exists with bucket-only Object Viewer. Its operator/impersonation
path remains unconfigured for the attached deployer. Do not grant the writer object read/delete,
attach it to the public receiver, repurpose the unchanged reader, or issue a
long-lived service-account key. The administrator must create/control the recovery
principal or provide an existing explicitly approved equivalent.

Download the exact backup using that reader path, then on a protected recovery
host run:

```sh
python3 -I /opt/tr-ledger-backup/deploy/ebay-deletion/cloud-backup.py quarantine \
  --encrypted-file /PRIVATE/downloaded.trlb \
  --destination /PRIVATE/NEW_QUARANTINE \
  --private-key /PRIVATE/decryption.pem \
  --hmac-file /PRIVATE/receiver-hmac
```

The destination must be new. Authenticated decryption precedes archive handling;
only two regular fixed filenames are accepted, with no links or path extraction.
The recovered SQLite checksum, integrity, event shape and HMAC-key binding must
pass. The command cannot import into a workbench, start a receiver or mark a copy
current. A failed check may leave a private incomplete quarantine for inspection.
Existing per-operation live nonce barriers remain mandatory after recovery;
a saved bundle or backup is never accepted as current receiver proof.

Measure upload completion and cloud download-through-quarantine duration using
clearly synthetic data. Verify incorrect keys, corruption, stale/saved proof and
unavailable live receiver remain blocked. A local crypto round trip is a partial
check, not evidence of successful cloud recovery or a measured cloud RTO.

## Supported-access handoff for bucket and recovery principal

The attached managed connection rejects bucket creation with
`403 identity_unsupported_request` through both the Google Auth SDK and Google
CLI. This is distinct from a demonstrated GCP IAM permission denial:
`storage.buckets.create` and `storage.buckets.setIamPolicy` are returned by the
project permission test. Do not relay the deployer's token through a VM or bypass
the managed connection. Use a supported connection, or have the owner/admin run
the following from their own authenticated environment after checking that the
exact bucket is still absent. Inspect any pre-existing bucket rather than taking
it over. That historical create failed. The owner has since created the bucket; do not
repeat the create or identity-create commands for resources that now exist.
Current corrective work is only the supported lifecycle/labels update below.

```sh
gcloud storage buckets create gs://tr-ebay-ledger-100948930799 \
  --project=turquoiseradar --location=us-west1 \
  --default-storage-class=STANDARD --uniform-bucket-level-access \
  --public-access-prevention --soft-delete-duration=7d --retention-period=1d

gcloud storage buckets update gs://tr-ebay-ledger-100948930799 \
  --lifecycle-file=deploy/ebay-deletion/backup-lifecycle.json \
  --update-labels=managed-by=turquoise-radar,task=jri-014,purpose=ledger-backup

gcloud storage buckets add-iam-policy-binding gs://tr-ebay-ledger-100948930799 \
  --member=serviceAccount:tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com \
  --role=roles/storage.objectCreator

gcloud iam service-accounts create tr-ledger-restore-reader \
  --project=turquoiseradar --display-name='Turquoise Radar isolated ledger recovery reader'

gcloud storage buckets add-iam-policy-binding gs://tr-ebay-ledger-100948930799 \
  --member=serviceAccount:tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com \
  --role=roles/storage.objectViewer
```

The IAM administrator must separately designate the owner-controlled recovery
operator and allow short-lived impersonation on **that recovery service account
only**. The operator identity is not guessed, and no such grant was applied.
Do not grant the writer/deployer recovery impersonation or change
`codex-radar-reader`. A bucket readback and actual writer/restore-reader tests must
follow; successful execution of these commands alone does not complete recovery
qualification. The public receiver must still have no service account.

## Approved owner-operated offline export

The owner selected secure offline IAP export: two owner-controlled encrypted copies
in separate physical locations, with HMAC and decryption-key records logically
separate inside the bundle. The only recoverable copy must not depend on the
receiver, runner, bucket or these same recovery keys. A named vault is not required.

Run `export-recovery.py` **on the owner-controlled machine**, with its own approved
IAP/OS administrator connection and an existing SSH key. It requires local Python
cryptography >=41 and gcloud. Do not run it in this temporary task environment.
Mount the two offline destinations and choose a separate strong recovery
passphrase through the hidden terminal prompts, keeping passphrase recovery
independent of these hosts and the encrypted copies.

```sh
python3 deploy/ebay-deletion/export-recovery.py --owner-machine \
  --ssh-key-file /OWNER/EXISTING_IAP_SSH_KEY \
  --copy-one /OFFLINE_MEDIA_ONE/turquoise-recovery.encrypted \
  --copy-two /OFFLINE_MEDIA_TWO/turquoise-recovery.encrypted
```

The helper checks instance numeric IDs against this deployment receipt, obtains
Ed25519 keys through authenticated Compute serial reads, and enforces pinned IAP
SSH. It captures remote key output directly into process memory, never the
terminal, logs or a plaintext local file. Separate JSON records hold the HMAC and
RSA private key. Non-secret production project/receiver/bucket identifiers,
generation date/time and fingerprints accompany them. Scrypt (N=262144, r=8, p=1)
derives an AES-256-GCM key from the passphrase, with independent random salt/nonce.
Only encrypted bytes are written, exclusively as new 0600 files. Both copies are
read back and authenticated/decrypted in memory and compared before reporting
success. CLI errors are fixed codes and never display captured secret output.
The helper does not upload recovery material to GCS.

After a successful run, retain only the aggregate verification receipt and
fingerprints as evidence, and confirm the two media have reached separate physical
locations. Do not paste key material or passphrases into chat. Script verification
alone cannot establish physical placement. No export has been executed from this
task, and no owner export/placement verification has yet been received.

Non-secret identifiers generated on the approved runner:

- Receiver HMAC identifier: `0eb6b203e7fe4ffd`; generated 2026-11-02 (date precision).
- Encryption public-key SHA-256:
  `042d38760c4e132e24a072539d8abfc52f90cf37d6e1ef019fd1a85459524e40`.
- Encryption key generation: 2026-11-02 09:47:54 Alaska time (18:47:54 UTC).

The owner export receipt must match these identifiers. They distinguish keys;
they do not themselves prove export or custody.


## Latest cloud qualification and exact owner/operator handoff

2026-11-02 readback confirms the exact us-west1 Standard bucket, uniform access,
public-access prevention, 1-day unlocked retention, 7-day soft deletion and the
bucket-only Creator/Viewer grants. Both dedicated identities have no project role.
The bucket still has **no lifecycle or labels** (metadata version 3), confirmed
by both SDK and CLI. A conditional PATCH returned `identity_unsupported_request`.
The supported owner/admin environment must apply just this correction:

```sh
gcloud storage buckets update gs://tr-ebay-ledger-100948930799 \
  --lifecycle-file=deploy/ebay-deletion/backup-lifecycle.json \
  --update-labels=managed-by=turquoise-radar,task=jri-014,purpose=ledger-backup
```

Read back the exact lifecycle/labels and unchanged access/retention controls before
enabling timers. The explicit real cycle already verified all three tiers in
2.359 seconds; writer data/metadata/list requests returned 403, permission tests
excluded delete, a real partial-upload failure did not advance either receipt,
and the real age check and its 90-minute boundary passed. No DELETE was attempted.

A one-event explicitly synthetic encrypted fixture is ready for the approved
restore operator. Exact reference:

```text
gs://tr-ebay-ledger-100948930799/synthetic/recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb
```

Ciphertext SHA-256:
`721b3c7b5356093034f8260c88d8e511e385f5d5bd2a5a9842e2ab1bb9c71170`.
Encrypted size: 41,508 bytes. This object is a fixture, not live receiver proof.
The private non-secret reference receipt is
`/var/lib/tr-ledger-backup/recovery-drill-upload.json` on the runner.

The recovery service account exists, but the attached deployer has no
`iam.serviceAccounts.getAccessToken` or actAs permission on it. Its service-account
IAM policy has no bindings. The owner must identify the approved recovery operator
and provide a supported keyless path; do not silently add a Token Creator grant,
use the writer/elevated deployer for the download, attach the reader to the runner,
or issue a service-account key.

Once an approved owner-controlled operator can use short-lived reader impersonation,
the exact scoped download from that protected operator environment is:

```sh
gcloud storage cp \
  gs://tr-ebay-ledger-100948930799/synthetic/recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb \
  /PRIVATE/synthetic-recovery.trlb \
  --impersonate-service-account=tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com
```

Verify the ciphertext checksum, then use the existing `cloud-backup.py quarantine`
command on an approved protected recovery system with separately controlled private
key/HMAC access. The approved runner's root-only recovery environment can perform
the quarantine on ciphertext streamed through pinned IAP; the writer must not
receive the reader's credentials. Measure from reader download start through all
quarantine checks, keep fixture data outside the live receiver/workbench, and test
that unavailable live proof and saved nonces remain rejected. Record aggregate
outcomes, timing and non-secret key identifiers only. These steps have **not** run
through the reader yet; no cloud RTO or custody success is claimed.

After both timers are correctly enabled, observe a real timer-triggered backup and
receipt advance. Keep the public endpoint gated until cloud recovery qualifies,
and keep PR #26 draft until all owner custody/deployment/activation/IAM evidence is
complete. The pending Spaceship record remains A / ebay-notify / 35.212.153.53 /
TTL 300; no DNS/TLS or eBay portal success is inferred.
