# Synthetic matching and local enforcement: implemented slice

Subsequent work adds [actual-route qualification with a mocked transport](JRI-020-ROUTE-INTEGRATION.md).
The original simulator described below remains separate; real SSH and live cohorts
are still unsupported.

Run with existing dependencies (no installation, host or network access):

```sh
.venv/bin/python -m radar.discovery.local_deletion_slice
.venv/bin/pytest -q tests/test_local_deletion_slice.py
```

The demo exclusively creates temporary runner and pilot roots. Its invented PNG
passes a current match check, a new synthetic deletion notification denies the
next read, explicit cleanup removes five inventoried files, and the temporary
roots disappear. No owner directory can be attached to this pilot object.

## Implemented boundary

`SyntheticRunner.exchange` is a single bounded match operation, not a shell or
file-access interface. A strict request holds a session, one-use nonce, inventory
hash, plan hash and 1–12 typed synthetic identities. Extra fields and username-only
records fail. The response binds CLEAR/MATCH/HOLD to the hash of the entire request.
There are at most 128 challenges per simulated runner instance.

The runner alone owns the randomly generated synthetic ledger key. It invokes the
existing `maintenance_ledger` verifier, with only the receiver transport replaced
by a local fixture. This exercises real nonce/HMAC, binding and rollback checking.
Every current event passes through the existing strict `identity_state` logic.
Username ambiguity holds the cohort even if a stable ID was supplied.

Disposable **channel** MAC material models authenticated SSH integrity in both
directions. It is separate from the deletion ledger key. This is a test double,
not a new production credential, signing service or authenticated real connection.
The classes share a test process: Python private attributes are not a security
boundary against code executing in that process. Real isolation must come from
owner-authenticated, pinned SSH and the remote account, not this simulator.

`SyntheticPilot.read` is the enforced release path. Every call creates a fresh
challenge; no cached lease or durable CLEAR exists. It checks complete file hashes
before and after matching, exact response binding, channel integrity, elapsed
monotonic and wall time (five seconds each), and the returned bytes' hash. A lock
serializes release and cleanup in this object. Any failure permanently suppresses
this instance and writes/fsyncs a denial marker. Restart cannot reattach to its
existing directory. A matching event suppresses immediately; cleanup is a separate
explicit action, not a hidden deletion side effect of a read.

The synthetic retained inventory is: SQLite originals and observation history,
a SQLite backup, a JSON export, an image and an image copy. Changes to any copy,
unexpected exports/SQLite sidecars, symlinks and hard links deny release. Cleanup
removes the known whole cohort only when all directory entries are accounted for;
an unknown copy prevents a false cleanup success claim. The marker remains until
the demo's temporary root is removed. This is logical file removal, not forensic
secure erasure. Caller-held bytes, browser memory, external backups, OS snapshots,
screenshots and copies outside the closed root are NOT covered or claimed deleted.

This is an actual match-to-release-to-suppression-to-cleanup implementation, but it
is deliberately NOT connected to the production feed, local owner Chrome profile,
live controller or SSH. Existing application routes could bypass this object if
it were merely imported; do not launch real data with it. Thread serialization
also does not protect against a separate process modifying the directory. Live
use needs a single controlled writer and complete route/copy inventory. A deletion
arriving after a successful check is observed at the next check; previously
returned pixels cannot be revoked by this API.

## Smallest real-world bundle still required

1. Review a production adapter that executes exactly one fixed matching helper
   through a fresh owner-controlled, host-key-pinned IAP/SSH session. Run reviewed
   code for that session under the existing maintenance account, reading its
   existing key/ledger in place; no new daemon, public endpoint, IAM grant, NAT,
   persistent key registration or credential export. Existing receiver pull stays
   unchanged. Confirm this exact command is permitted; access is not established.
2. Before host execution, review exact helper content/hash, command, temporary
   staging paths, session limit and cleanup. The temporary helper's imports and
   dependency compatibility on the runner remain unverified. If unavailable,
   stop rather than installing or broadening access implicitly.
3. Locally wire the gate to every retained-data/image route and collection step
   in a separate owner pilot, inventory all browser and file copies, and test
   bypasses/concurrent writers and shutdown while disconnected. Keep the original
   27-item app unchanged. The synthetic slice is not permission to weaken its
   production-identity-validation gate or deploy untested route wiring.
4. Use the owner's existing eBay key locally through a reviewed non-echoing prompt;
   do not export Codex/runner credentials. Availability is unverified. Only after
   deletion/identity/retention gates pass, obtain separate exact live-request
   approval (original proposal <=27 API attempts and <=8 image GETs if approved).

The bundled approval is for separate local staging and the exact temporary owner
matching command/access/cleanup, with live calls separately approved. No new paid
infrastructure is necessary to test this design. Existing compute/IAP/network and
eBay quota/account charges are unverified, not promised free. If the existing
account cannot run the fixed helper without a new persistent grant, report that
permission and cost tradeoff before proceeding. No host or credential changes,
software installation, live calls, publication or deployment occurred in this task.
