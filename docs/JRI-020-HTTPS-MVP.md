# One pilot: optional HTTPS deletion matching and local job

Implemented offline; disabled by default. No eBay calls, new real credentials,
private-host access, installs or deployment were performed. The original 27-item
app and port 8000 are not used. This replaces the proposed unattended IAP channel;
there is no per-page matching or new viewing application.

## What runs

`receiver.create_receiver` installs POST `/pilot-deletion` only with an explicit
matcher. `from_env` enables it only with `PILOT_MATCH_ENABLED=true` and a separately
provisioned `PILOT_MATCH_TOKEN_SHA256`. The receiver HMAC/eBay verification tokens
must NOT be reused as the pilot credential. Caddy still rejects this route by default.

First authenticated `enroll` accepts one list of 1–3 distinct stable seller IDs and
usernames. The job derives this list automatically from collected actual-schema
records, including hydration evidence and observations. No manual enrollment step
is needed after the search. The registry commits one immutable cohort; identical
retry is safe, replacement/expansion is rejected, including after restart. All
subsequent `check` requests name only its cohort digest, not arbitrary identities.
The registry stores keyed identity digests, not plaintext seller records. Missing
registration storage fails closed; startup never creates a replacement registry.
A stolen token before first enrollment could seize that single enrollment: protect
and revoke it as a credential; it is not a general lookup API.

The matcher uses the same identity logic as existing deletion processing, now
extracted into dependency-light `identity.py`. It reads accepted receiver events
locally. Stable matches return MATCH; ambiguous or incompatible identifier evidence
returns HOLD unless another accepted event definitively matches that seller. Known
MATCH removals are committed before HOLD is applied to still-retained seller slots.
Already sanitized slots do not block the remaining pilot. It neither exports the ledger nor its key. The operation bounds bodies
at 4 KiB, body-read time at five seconds, and authenticated calls at 12/minute per
process. TLS comes from existing Caddy; no HTTP fallback or redirects are supported.

The local job automatically enrolls/checks and uses existing `sanitize_database`,
which redacts source records and observations, removes registered seller-image bytes
and rows, and preserves supported owner financial facts. A public local selector
constant adapts the authenticated matched ID to the sanitizer's keyed comparison;
it is NOT an authentication key or a substitute receiver proof. Matching plaintext
identity entries are also removed from the local job state after sanitation.

`pilot_job.run` owns the pilot's existing uvicorn feed process on loopback port 8001.
Every five minutes it stops/waits for that process, performs maintenance, then
restarts only on success. This intentionally trades a brief periodic interruption
for reusing the proven stopped-database sanitizer. On outage, HOLD, unknown copies
or failed cleanup it stays stopped and retries on the next interval. It does not
kill arbitrary processes or renew owner SSH sessions. Only one supervisor may hold
the pilot lock. On Ubuntu/Linux the child arms PR_SET_PDEATHSIG with SIGKILL before
exec, checks that its expected parent is still alive, and therefore dies even if
the supervisor is killed without running cleanup. The old app remains available
on port 8000.

## Runnable synthetic evidence

```sh
.venv/bin/pytest -q tests/test_pilot_https_job.py
```

Tests exercise the actual receiver POST handler (notification crypto stubbed in
that test, independently tested elsewhere), actual database schema, existing source
image removal, and existing feed after maintenance. They cover auth/default-off,
immutable enrollment/restart, size/rate limits, username ambiguity, outage, unknown
copies, an unaffected seller, interrupted-removal retry and supervisor ordering.
Only dummy credentials, local fixtures and HTTPX MockTransport are used.

## Exact deployment preview — NOT executed or approved

Receiver code files: `receiver.py`, `identity.py`, `pilot_match.py`; keep their
existing `protocol.py` and `store.py` dependencies. No new Python packages are needed
for this receiver operation. The local job uses the existing locked app environment.

One owner-controlled deployment would:

1. Provision one separate random pilot credential locally, with only its SHA-256
   digest in the receiver's protected environment; place the actual credential in
   the local job environment using an approved secret-storage procedure. No secret
   belongs in command arguments, source, terminal history or logs.
2. Explicitly initialize `<private-registry-path>` once as
   `ebay-receiver`, mode 0600, using `pilot_match.initialize_registration`. Do not
   overwrite an existing registry or restore/reset it while its token remains valid.
3. Set `PILOT_MATCH_ENABLED=true` and `PILOT_MATCH_TOKEN_SHA256=<digest>` in the
   protected receiver environment, after reviewing the exact deployed code hash.
4. Add ONLY this Caddy handler before its existing fallback, preserving the current
   notification handler and no-access-log policy:

   ```caddyfile
   @pilot path /pilot-deletion
   handle @pilot {
       request_body {
           max_size 4KB
       }
       reverse_proxy 127.0.0.1:8091
   }
   ```

5. Validate/reload Caddy and restart the receiver under the existing owner access
   procedure. Verify existing notification handling and unauthorized pilot rejection.
   Rollback: remove this handler and disable the feature; revoke the pilot token.
   Preserve enrollment state during ordinary restarts/credential rotation.

This adds one public authenticated operation and one persistent pilot credential;
it is NOT covered by prior permission to inspect the host. It needs no new IAP
identity, IAM grant, VM, NAT or recurring owner login. Existing cloud usage is not
claimed free; no account billing inspection was performed.

On Ubuntu, after separate staging approval, prepare a new empty root with:

```sh
.venv/bin/python -m radar.ebay_deletion.pilot_job prepare --root NEW_PILOT_ROOT
```

The separately approved one-search collector must write its <=3 seller cohort to
that root's `pilot.db`, use its `photos` directory and maintain its existing
`inventory.json`. This change does not implement that collector or authorize calls.
Then the job automatically enrolls the collected cohort on first start:

```sh
.venv/bin/python -m radar.ebay_deletion.pilot_job run --root NEW_PILOT_ROOT \
  --endpoint https://receiver.example.invalid/pilot-deletion --enable
```

`PILOT_MATCH_TOKEN` must already be supplied securely. No service/scheduler was
installed. The foreground supervisor is itself the periodic job; autostart is a
separate local setup action, not required for the offline test.

## Honest limits

Only the new root's DB, state and registered photos are covered. Unknown files,
backups, symlinks and hard links are rejected; external/OS backups, browser downloads
and already delivered pixels are not claimed erased. Do not run another app or
writer against the pilot DB. When Ubuntu is off, notices remain in the receiver and
are processed before this supervisor next starts the feed. Sustained outage leaves
the feed stopped; that is not a claim of instantaneous deletion or a legal exemption.
Existing freshness behavior stays unchanged. Existing strict identity logic may
HOLD a cohort when historical events lack comparable stable IDs; that limitation
is preserved and must not be silently bypassed. Live seller-ID interoperability
and the actual deployed Caddy/configuration still need their bounded validation.
