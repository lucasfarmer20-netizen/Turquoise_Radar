# JRI-014 private ledger backup operation

The owner approved `tr-ledger-runner-01`, a private non-Spot e2-micro in
us-west1-b with a retained 10 GiB pd-standard disk and the dedicated
`tr-ledger-runner@turquoiseradar.iam.gserviceaccount.com` identity. Conservative
incremental planning cost is $6.51/month; receiver/cloud-backup plus runner is
$17.58/month before tax and the existing exclusions. No NAT, load balancer or
managed backup service is included.

Current evidence is recorded in [JRI-014](JRI-014.md) and the qualification section
below: bucket controls passed independent readback, both timers are enabled, a
timer-triggered backup succeeded, and the owner-downloaded synthetic object passed
protected quarantine. One elapsed hourly RPO, full download-through-quarantine RTO
and durable owner offline custody remain unqualified. Earlier blocked handoffs are
explicitly historical.

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
owner-controlled secure export must preserve it and the HMAC assignment
independently before durable recovery custody is complete. The protected drill
does not complete that custody requirement. Never place keys
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
also writes a daily/weekly copy. Verified lifecycle rules delete `hourly/` after
1 day, `daily/` after 7 days, `weekly/` after 28 days and `synthetic/` after 1 day.
Retain Standard class, us-west1, uniform bucket access, enforced public-access
prevention, 1-day unlocked retention and explicit 7-day soft deletion. Lifecycle
processing is asynchronous, not an exact object-count guarantee. Include retained
and soft-deleted bytes in the planning allowance. No retention lock is requested.
The owner created and configured the bucket. Independent metadata version 4
readback confirms these controls, lifecycle rules and required labels; no lifecycle
success is inferred from owner commands alone.

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
a start-up execution alone does not prove an elapsed hourly RPO. One-hour RPO
remains a target despite the observed start-up cycle; four-hour RTO requires the
full timed cloud recovery drill.

## Separately controlled recovery

Approved recovery principal:
`tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com`, with
`roles/storage.objectViewer` on this bucket only and separately approved access
to use that identity. The account exists with bucket-only Object Viewer. Its
service-account policy grants Token Creator only to approved owner-controlled
operator `lucas.farmer20@gmail.com`; the grant is not at project scope. The owner
reported a successful short-lived impersonated download. The attached deployer
has no recovery-account access-token or actAs permission. Do not grant the writer object read/delete,
attach it to the public receiver, repurpose the unchanged reader, or issue a
long-lived service-account key. Preserve the separately controlled operator path.

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

## Historical supported-access handoff for bucket and recovery principal

The following records the original owner handoff. Bucket creation, configuration
and the approved recovery-operator grant are now complete and independently read
back; do not repeat these mutations. Current qualification follows the export
instructions below.

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
The lifecycle/labels update was the subsequent owner correction and has also passed
current readback.

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

At this historical stage, the IAM administrator still needed to designate the
owner-controlled recovery operator and allow short-lived impersonation on **that
recovery service account only**. The owner has since supplied the exact operator
and completed the narrow grant described above; no grant was applied by this task.
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


## Current cloud qualification and remaining handoffs

Continued from `39a851fecbb39363ed51d6dba17c7b1c4eb8419f`; times below are
2026-11-02 UTC. Independent bucket metadata version **4** readback confirms
`US-WEST1`/`STANDARD`, uniform access, enforced public-access prevention, unlocked
86,400-second retention and 604,800-second soft delete. The exact lifecycle is
Delete at 1/7/28/1 days for `hourly/`, `daily/`, `weekly/`, `synthetic/`
respectively. Labels exactly match `managed-by=turquoise-radar`, `task=jri-014`,
`purpose=ledger-backup`. Bucket-only Creator/Viewer grants are preserved; both
dedicated identities have no project role bindings. The approved owner's Token
Creator grant is only on the recovery service account, with no project Token
Creator binding. The owner's existing project Owner role and legacy bucket
bindings were not changed.

The prior explicit cycle verified hourly/daily/weekly uploads in **2.359 seconds**,
and its real partial-upload failure check left the success receipt unchanged.
After configuration readback passed, both timers were enabled at **20:29:18Z**.
The start-up timer fired at **20:30:18Z** and the service succeeded at
**20:30:20Z**, taking **1.605 seconds**. A new hourly object of **41,508 bytes**,
generation `1793651420229174`, was independently confirmed. Daily/weekly copies
were appropriately skipped because their current UTC day/week was already recorded.
The receipt advanced from **19:21:49.341Z** to **20:30:20.583Z**, reporting one
verified upload, zero live events/receipts and `current_receiver_proof=false`.

The backup timer remains active/enabled with `OnActiveSec=1min`, hourly calendar,
`Persistent=true`, and next calendar run **21:00:00Z**. The age timer remains
active/enabled with `OnBootSec=5min`, `OnUnitActiveSec=5min`. Its service passed
against the new receipt at **20:32:06Z**, moving its next run to **20:37:06Z**.
The actual receipt's 90-minute boundary passed and 90 minutes plus one second was
rejected without changing the clock/receipt. Writer data/metadata/list requests
still returned **403**; effective create/get/list/delete permission testing returned
only `storage.objects.create`. No DELETE was issued. SHA-256 checks matched the
installed cloud-backup/sync code and both timer files to current repository bytes.
This is observed scheduled start-up operation, not an elapsed hourly RPO or
observed hourly calendar run.

### Actual synthetic cloud recovery

The owner reports a successful short-lived restore-reader download of this exact
synthetic object and its matching ciphertext checksum:

```text
gs://tr-ebay-ledger-100948930799/synthetic/recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb
```

Ciphertext SHA-256:
`721b3c7b5356093034f8260c88d8e511e385f5d5bd2a5a9842e2ab1bb9c71170`.
Encrypted size: 41,508 bytes. This object is a fixture, not live receiver proof.
The private non-secret reference receipt is
`/var/lib/tr-ledger-backup/recovery-drill-upload.json` on the runner.

The download provenance is owner-reported. The following is the approved scoped
download command for future drills, executed only in that owner's protected
authenticated environment; the runner must never receive reader credentials:

```sh
gcloud storage cp \
  gs://tr-ebay-ledger-100948930799/synthetic/recovery-qualification-20261102T192436Z-e5a0d02620faa2943ce04f98032c8909.trlb \
  /PRIVATE/synthetic-recovery.trlb \
  --impersonate-service-account=tr-ledger-restore-reader@turquoiseradar.iam.gserviceaccount.com
```

The owner-staged `/root/ledger-recovery/synthetic-recovery.trlb` on the approved
runner was independently checked as root:root 0600 beneath root-only directories,
with the expected SHA-256 above. The existing quarantine command passed
authenticated decryption, exactly two regular archive files (`ledger.db` and
`manifest.json`), database checksum, SQLite integrity/foreign keys and HMAC binding.
Protected follow-up checks verified the explicit synthetic markers and exact
expected HMAC-derived **one event and zero receipts**. The successful new
quarantine is `/root/ledger-recovery/quarantine-20261102T203045Z` and remains private
for inspection. No product code was changed.

Unavailable live proof, a correctly signed saved bundle with the wrong nonce and
a correctly signed stale bundle with the requested nonce were all rejected in an
isolated process. The recovered database checksum did not change. Output remains
`offline_copy_only=true`, `current_receiver_proof=false`. No recovery was imported
into the live receiver or workbench, and no locally generated proof was presented
as live receiver evidence. Protected checks confirmed the HMAC/public-key
identifiers listed above without revealing either private key or the HMAC value.

Local quarantine and verification ran from **20:30:45.417Z** to **20:30:46.353Z**,
taking **0.936 seconds**. No trustworthy original reader-download start timestamp
was available. The full download-through-quarantine duration remains unqualified;
do not call this timing a measured cloud RTO. Future timed drills must capture the
download start and include all transfer/quarantine verification time. Owner offline
export/custody also remains pending: require two encrypted verified copies,
matching identifiers and confirmed separate physical locations.

### DNS and activation handoff

Prepare this exact Spaceship record in the `turquoiseradar.com` zone:

| Type | Host | Value | TTL |
|---|---|---|---|
| A | `ebay-notify` | `35.212.153.53` | `300` |

At 20:30–20:32Z, recursive A/AAAA queries and A queries to both authoritative
Spaceship nameservers returned NXDOMAIN. Google Public DNS confirmed authenticated
A NXDOMAIN; Google/Cloudflare DS responses agreed and had authenticated-data flags.
The owner DNS handoff remains pending. The receiver is active on loopback with
boot enablement pending; Caddy is inactive/disabled.

After DNS resolves correctly, qualify public Caddy/TLS and the exact
`https://ebay-notify.turquoiseradar.com/ebay/account-deletion` boundary. Complete the
Developers portal handoff, real eBay GET challenge and eBay-signed test delivery;
verify durable acceptance and a subsequent fresh backup, and record actual
Production keyset status. Then reduce temporary-deployer privileges and prove
receiver/runner operation afterward. Keep PR #26 draft until this evidence and
owner offline custody are complete. No Browse sourcing pilot is authorized.
