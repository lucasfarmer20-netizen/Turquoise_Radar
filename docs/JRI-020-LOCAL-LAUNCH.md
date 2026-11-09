# Separate local pilot: offline launch and bounded approval proposal

The ephemeral-preview experiment is stopped. Use the owner's established Ubuntu/
Windows loopback-and-Chrome model. Never change these existing locations:
`$HOME/TurquoiseRadar/new-only-research/app`,
`$HOME/TurquoiseRadar/new-only-research/application/research.db`, or port 8000.
The 27-item application is not part of this package or its maintenance inventory.

## Offline package

The source archive contains the existing app, synthetic fixtures, locked dependency
specifications, offline hydration controller, and `scripts/launch-jri020-synthetic.py`.
It contains no credentials, prior databases, live probe responses, virtualenv or
private evidence. This is a synthetic launch package, NOT a live-enabled beta.

After approval to stage the package locally, extract into a new root only:
`$HOME/TurquoiseRadar/jri020-pilot/app`. Use a dedicated dependency environment under
that app, matching uv.lock. Installation is not performed by the launcher and is
not performed in this task. If dependencies are unavailable, stop; do not modify
the old application's environment. With that environment already available:

```bash
cd "$HOME/TurquoiseRadar/jri020-pilot/app"
.venv/bin/python scripts/launch-jri020-synthetic.py \
  --workspace "$HOME/TurquoiseRadar/jri020-pilot/application"
```

Open `http://127.0.0.1:8001/feed?dataset=fixture` in Chrome. The launcher refuses
an existing workspace, symlink traversal, a path containing new-only-research,
or an occupied port 8001. It creates an invented photo and a new private SQLite
DB. eBay and live vision are disabled in this process. It never installs software,
starts a public listener, or changes the original app. Ctrl-C stops the server.
The pilot workspace remains for review; after closing the pilot browser session,
remove ONLY that newly created synthetic application directory if desired. Do not
reuse the synthetic DB for a live run; live collection requires another empty root.
No automated recursive deletion of an owner-supplied path is included.

## Smallest proposed authenticated deletion path

A [runnable synthetic matching/enforcement slice](JRI-020-MATCHING-SLICE.md) now
exercises this boundary offline. The real SSH adapter and production feed wiring
remain unimplemented and unauthorized; the launch package stays synthetic-only.

Keep API credentials and the live DB on the owner's local machine; leave the
receiver/runner HMAC key, ledger and receiver connection where they already work.
Use a fresh owner-authenticated, strictly pinned IAP/SSH channel to the existing
runner for a **fixed, bounded matching command**, not a new HTTP service, VM, NAT,
public endpoint or grant to the existing read-only identity.

This command is NOT implemented or currently authorized. The existing backup job
cannot be repurposed silently. Proposed protocol, for review before implementation:

1. Local gate freezes the new pilot for collection/display, creates a random
   one-use nonce, hashes its complete inventory and current cohort, and sends only
   the <=12 typed seller identity records plus these bindings over authenticated
   SSH. No raw listings, image bytes, API token or eBay secret leaves the laptop.
2. A reviewed helper under the existing runner maintenance account uses its
   pre-bound ledger and existing fresh nonce/HMAC receiver pull. It evaluates every
   supplied identity against the complete current event set with existing strict
   identity_state rules. Missing/incompatible IDs or ledger uncertainty yield HOLD.
   The helper retains no seller inputs and prints no raw exception/log payloads.
3. It returns bounded outcomes CLEAR / MATCH / HOLD, echoing the nonce and cohort/
   inventory digest and operation completion time. Local code accepts the response
   only from that live authenticated channel for the outstanding operation and
   unchanged cohort. This is NOT a portable signed certificate, cached receipt or
   replacement for receiver proof. No key or reusable HMAC oracle is exported.
4. Missing/stale/wrong-bound replies deny collection/display. MATCH or identity HOLD
   suppresses the whole pilot; any purge is confined to the newly inventoried pilot
   and separately approved. Do not silently change old records, aliases or money
   history. Whole-cohort suppression avoids pretending a remote decision is a local
   keyed replay. Actual purge and verification must cover DB originals/history,
   images, exports, SQLite sidecars and all inventoried copies before claiming deletion.

Reuse the existing matching/proof primitives; do not weaken them to implement this
interface. Test forged/replayed replies, wrong host/nonce/digest, new events,
concurrent writes, incomplete inventory and all failure cases synthetically first.
The present package does NOT implement or qualify this transport or a live gate.

For bounded local viewing, a startup check alone is insufficient. The proposed gate
must check before acquisition, before any image fetch and before release, then bind
a short display lease to successful current checks during a finite owner session.
Failure/expiry hides content and stops further requests. The exact check interval,
maximum maintenance calls and viewing duration must be part of the later execution
preview. Browser expiry is not deletion notification processing and cannot revoke
bytes already delivered or screenshots. Do not claim all browser/OS/platform copies
are erasable: inventory the chosen Chrome profile and prohibit screenshots/exports
for this initial pilot; unresolved retained copies keep the release blocked.

## Local eBay credentials

Use the owner's existing Production keyset, entered locally through a non-echoing
prompt into the one-shot process, or an already approved local credential store.
Never retrieve/export injected Codex secrets, transmit eBay credentials to the
runner, create a keyset, put secrets in shell arguments/history, or write a .env
file. This secure local prompt/launcher must be implemented and reviewed before
live use; it is not supplied by the synthetic launcher. Credential validity and
availability on the owner's machine are unknown.

## Requirements versus project safeguards

The official eBay agreement requires appropriate deletion, permitted use/display
and current content; the notification guide requires processing received deletion
notifications. Relevant references:
https://developer.ebay.com/join/api-license-agreement
https://developer.ebay.com/develop/guides/sell/marketplace-user-account-deletion

These sources do not prescribe our exact synchronous SSH matching protocol,
nonce scheme or whole-cohort suppression. Those are conservative project controls
chosen to preserve existing safeguards, not claims about eBay-mandated architecture.
Our six-hour listing and 24-hour image display limits remain independent of deletion.
Field availability was verified by the approved two-request probe; cross-API
identity compatibility remains an explicit production-validation gate. No probe
identifier was retained for retrospective correlation. Do not manufacture a test
deletion or infer stable IDs from usernames.

## One bundled approval, with a separately bounded live run

Proposed approval wording for the owner:

“Approve staging the separate local pilot under ~/TurquoiseRadar/jri020-pilot,
its own locked dependency environment and loopback port 8001; preserve the old
27-item app and port 8000. Approve implementing and synthetically validating the
fixed owner-IAP matching helper and local fail-closed gate, then review the exact
helper hash/command and temporary access/cleanup before any host execution. Use
existing owner permissions, pinned hosts and existing keys only; do not grant
roles, export secrets, add services or change network infrastructure. Keep live
eBay calls disabled until the identity and deletion-integration gates pass and I
approve the final exact request preview.”

The eventual original search proposal remains four queries, one page each, three
observations each, twelve total. Hydration adds at most twelve detail GETs with no
retries; OAuth/search retain existing bounded retries: <=27 attempts combined.
An exact image-only continuation may add <=8 single primary-image GETs with no
retries/redirects, <=35 combined only if explicitly approved. The two-request field
probe is already consumed and authorizes none of this. A smaller one-listing image
canary could be separately previewed, but is not a substituted authorization here.

No NAT or new hosted service is proposed. API quota, account-specific charges,
existing compute/IAP/network usage and dependency-download costs are not priced or
assumed free. Any newly required paid resource, credential creation, role grant or
network change stops the plan for a concrete separate decision. The actual missing
action is owner approval for the bounded local staging and fixed-command access
workflow; there is currently no established machine-to-runner matching channel.

## Offline evidence

The separate launcher was exercised against a temporary synthetic root: feed 200,
photo 200, removed-photo 404, server exited cleanly and workspace removed. Existing
synthetic deletion tests cover exact source-image removal and suppression; fresh
ledger tests reject stale/replayed proofs. The new remote matching protocol and
local live lease remain proposals, not tested capabilities. No local Windows app,
private host, credential or hosted setting was accessed or changed in this task.
