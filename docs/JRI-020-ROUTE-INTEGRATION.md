# Actual routes with a mocked matching transport

This change adds an opt-in `pilot_gate` argument to `create_app`. The default is
None: the existing owner app, its 27 items, database, port and routes are unchanged.
The gate owns an exclusively NEW directory, requires exactly its `pilot.db` and
`photos` paths, and refuses the old new-only-research path. No live startup command
or environment-variable switch enables it. An explicit Python caller must supply
the gate and matching transport, prepare fixtures, and freeze the inventory.

Run locally with existing dependencies:

```sh
.venv/bin/pytest -q tests/test_pilot_route_gate.py
```

These tests construct the actual FastAPI app, seed its actual database schema,
create Listing/VisionCase/VisionPhoto records with existing photo functions, and
exercise `/feed?dataset=fixture`, `/feed/photos/{id}` and `/vision/photos/{id}`.
They do not substitute a toy HTTP server or toy SQLite schema. Only the matching
transport/receiver are simulated using the earlier matching slice.

## Enforced pilot behavior

The ASGI middleware permits read-only feed and both photo paths, plus public static
assets. Other data routes, docs, mutations, HEAD requests and WebSockets are denied
for this pilot instance. This prevents reaching the same retained data through an
alternate listing, discovery, vision or export route. It does not change routes in
an application constructed without the gate.

Every content request gets a fresh decision before rendering and another before
release. Both bind the session, fresh nonce, exact method/path/query bytes, phase,
identity cohort and inventory hash. The entire response is buffered (16 MiB cap)
until checks pass; a denial discards headers/body rather than leaking an initial
chunk. Successful responses use private,no-store. Both matches and route execution
have actual asyncio timeouts (five seconds by default). A timed-out transport
thread cannot be forcibly killed, but its result cannot release content, no retry
is made and the instance is latched off. Real SSH needs its own process timeout.
Two decisions per content GET mean the simulator's 128-challenge cap allows at
most 64 complete requests, fewer on failures. This is not an approved live quota.

The gate hashes all regular files recursively inside its private root, rejecting
symlinks and hard links. Any changed or added database/image/export/sidecar blocks
release. Checks run before matching, after matching and before buffered release.
Mutations through the app are disabled; an application-level async lock serializes
pilot requests. Tests verify changes during matching and during actual feed
rendering, deletion during rendering, outages, real timeout, cross-route replay,
photo-directory reconfiguration, bypass paths and unchanged default app behavior.

## Deliberate limits

Freeze currently accepts only 1–12 synthetic Listing records with typed synthetic
seller IDs in original_data. Production DiscoveredItem/DiscoveryObservation cohorts
are explicitly rejected. This preserves the unresolved production identity gate;
it is not a claim that synthetic flags or usernames qualify real listings. The
adapter does not yet derive/validate all retained production identities through
hydration provenance. No real eBay data can be enabled by this test adapter.

Copies must be inventoried before freezing; nothing proves absence of files outside
the root. Browser memory, downloads, OS snapshots and external backups remain
unverified. Already delivered pixels are not revoked on a later outage. Cleanup
of an actual application cohort is not added here. Database/file snapshots and an
in-process lock are not cross-process transactional isolation: hostile writers
could change and restore data between checks. Live use still requires a controlled
single writer and reviewed copy lifecycle. No remote access is established.

## Exact remaining owner-run route and dependencies

There is still NO executable production matching helper or approved local-to-runner
command. Do not invoke the simulator on the private host or install its dependencies
there. The command shape to finalize offline is:

```text
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes \
  <owner-controlled pinned IAP runner alias> \
  sudo -n -u tr-ledger-backup -- <verified-python> -I <reviewed-temporary-helper>
```

This is a specification with unresolved placeholders, NOT a command ready to run.
The owner alias must use the previously approved IAP route and authenticated host
pins; no retired deployer or expanded read-only identity. Fixed helper path, SHA,
interpreter, noninteractive sudo permission, stdin JSON framing, deadline/output
bounds, temporary staging and removal must be reviewed before owner execution.
No identifiers belong in argv or shell history. The helper must use fixed existing
ledger/key/config locations, never request-selected paths or commands. Run under
the existing maintenance account without new permanent roles/keys/services. If that
is not already permitted, stop and identify the exact grant; do not substitute an
unrestricted shell or expand access silently.

Existing runner-to-receiver behavior remains `sync._pull_receiver`: `ssh -T -o
BatchMode=yes -o StrictHostKeyChecking=yes <operator-configured receiver alias>`,
with nonce on stdin and a 30-second timeout. This is not the missing first hop.

Local test dependencies are the existing Python >=3.12 project environment/uv.lock:
FastAPI/Starlette, SQLAlchemy, Pydantic, Pillow, HTTPX and pytest; no install or lock
change occurred. Directly importing the current matching slice on a runner would
also import Pillow/Pydantic and application modules, so it is NOT a minimal
production helper package. The actual `sync`, `store` and `protocol.digest`
primitives are standard-library based; `processing.identity_state` currently shares
a module that imports image handling/Pillow. A bounded next implementation step is
to isolate that unchanged identity function into a standard-library-only module,
retain regression tests, and package a fixed stdin/stdout helper with only those
primitives. No runner dependency installation should be assumed or requested yet.

Live collection, owner IAP execution, credential entry, staging/install and remote
changes need their respective reviewed authorization. No new persistent access or
paid infrastructure was necessary for this route integration. Existing IAP/network
and account usage charges remain unknown. No live requests, hosts, credentials,
owner data, deployment or push were used in this work.
