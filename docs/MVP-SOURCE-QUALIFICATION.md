# Source qualification MVP

Use `/sources` to register a candidate by name and public domains. Registration
adds local intake metadata; it does not search, create accounts or grant access.
Open its Qualification form and record permitted access/sample references, an
actual test result, a timezone-bearing review time, freshness/deadline limitations,
fees, shipping to 99669 and the next bounded step. Status is an owner assessment,
not a purchase recommendation. Non-UNTESTED assessments require dated evidence;
USEFUL additionally requires access evidence. Unknown amounts remain unknown.

Import a permitted sample through `/inbox/import`. Parser-format approval remains
its own existing explicit confirmation; qualification never changes those rules.
The registry shows surviving real inventory and observation counts from the same
evaluator as `/feed/evaluation`. Follow that link for query attribution, missing
costs, Alaska shipping gaps and owner feedback. Counts exclude synthetic records
and are not complete search yield. A review timestamp does not refresh a listing.
No crawler, external request, recurring collection or database migration is added.

Synthetic route tests demonstrate registration, qualification, persistence without
changing access/parser configuration, evaluation navigation, invalid evidence
rejection, unknown-source rejection and HTML escaping. Real mailbox samples are
not fixtures and are not included in this change.

## Feed-label maintenance-only deployment

The previously prepared feed-label ZIP changes only compact_feed.py and its
manifest entry. It does NOT include this source qualification feature. Keep the
original source archive and acquisition ZIPs immutable: they intentionally reject
a changed manifest. Never reset/rename acquisition markers or use acquisition to
restart a feed.

Existing `radar.ebay_deletion.pilot_job.run` is the reusable supervisor. It stops
the child before each check, sanitizes definite matches, retains HOLD/outage denial
and couples the Linux child lifetime to the supervisor. However, `cycle` can enroll
when job.json is absent or unenrolled. Therefore invoking the generic CLI alone
does not guarantee maintenance-only operation.

Safe sequence for the existing personal pilot:

1. Establish that its existing job.json is valid and enrolled, with its immutable
   cohort and seller slots intact; inventory must pass the existing inventory and
   seller checks. Do not emit seller identifiers. If absent/unenrolled, stop and
   inspect the prior run result; do not infer remote enrollment state.
2. Stop the foreground supervisor normally. Apply the already checksum-gated
   feed-label update, which refuses a held job lock or listener on port 8001.
3. Resume through a narrow maintenance-only entrypoint that verifies the patched
   source manifest and existing enrolled state, reads the existing private token
   locally, and calls the existing supervisor with a transport wrapper that rejects
   any action other than `check`. This guard must remain active on every cycle,
   including if job.json is subsequently missing. Do not print the token or put it
   in command arguments. Remove marketplace credentials from the child environment.
4. The supervisor's first successful deletion check qualifies the feed; errors or
   HOLD keep it stopped. No OAuth, Browse, images or enrollment are required.

The integrated owner-run `radar-maintenance-update.zip` now provides that entrypoint.
It installs the feed label, qualification UI, and explicit maintenance-only mode.
It accepts only the reviewed baseline or the prior feed-label-only state; all
manifest entries are verified. Changed source files and the manifest are backed up
in `~/TurquoiseRadar/jri020-pilot/mvp-update-rollback`. Caught update failures restore
those files. An interrupted/partial update fails closed; inspect the backup and
source hashes rather than retrying acquisition. No data backup is made (which
would add an unmanaged retained copy), and no dependency install is performed.

Owner steps: stop the existing foreground supervisor with Ctrl-C; download the
integrated ZIP into personal Ubuntu home; verify its supplied SHA256; run it with
`~/TurquoiseRadar/jri020-pilot/.venv/bin/python ~/radar-maintenance-update.zip`.
The default operation updates then supervises in the foreground. Subsequent
maintenance starts use the same command with `run`; they do not acquire or enroll.
A fully applied update is recognized on rerun, but an uncertain partial update
stops for inspection. Ctrl-C stops the feed and maintenance together.

The new mode requires a valid enrolled job under the supervisor lock and pins its
cohort for the process lifetime. Every cycle checks that state again before any
receiver request. Missing/unenrolled/changed state keeps the feed stopped; only
`check` can be sent. The existing token is read locally with regular-file,
owner/permissions and symlink checks, never printed or placed in command arguments.
Marketplace/paid-AI credentials are removed from the child environment. Existing
MATCH sanitation, HOLD/outage handling and Linux parent-death protection remain.

No direct personal-machine access is available here. Installation and the first
real maintenance check remain unexecuted. Local synthetic tests do not establish
its current enrolled-job state or receiver availability. Qualification notes and
source registration can be used in this pilot, but importing additional listings
must use a separate evaluation workspace to preserve the immutable photo cohort.
