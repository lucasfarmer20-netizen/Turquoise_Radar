# First Haiku pilot preparation — 2026-10-30

The owner requested one explicitly authorized image-analysis attempt. **No paid
request has been authorized or sent.** The owner cannot use a private workspace
preview or VPN on Android and asked about a direct conversation-upload
path instead. No public exposure or Task 006 deployment is part of this work.

## Application and data

Fetched GitHub main again: Task 005 remains
`2e2277673a2da6817e17b46658b1c564e5a70764`. The existing `work` checkout contains a
small local pilot guard on that base; no commit, push or merge was performed.

The running app uses the explicit absolute database URL
`sqlite:////workspace/Turquoise_Radar/data/radar.db`. Haiku's dated verified price
profile matches the JSON artifact; Sonnet's access flag remains false. A private
SQLite backup was taken before setup, and every pre-existing row was checked for
preservation. The backup location is recorded in ignored `data/pilot-preparation.json`.

A neutral owner-requested pilot item and review case were added: listing #1,
case #1, titled “Owner jewelry pilot — details pending”. No price, material,
hallmark, purchase transaction, or other jewelry fact was invented. No photo or run
was seeded. Application startup adds its ordinary reference records only.

The app is listening on **127.0.0.1:8000**, with access logging disabled. This is
workspace loopback, **not an Android-accessible preview link**. No authenticated
preview capability is available in the current tool/environment surface. No
public tunnel, public listener or unauthenticated deployment was created.

## One-request guard

General settings remain:

```text
VISION_PROVIDER=fixture
VISION_LIVE_ENABLED=false
VISION_DAILY_LIMIT=10
VISION_MONTHLY_BUDGET_USD=5.00
```

The running preparation process additionally sets `VISION_PILOT_ONE_SHOT=true`.
This opt-in flag defaults to false in ordinary configuration. It makes exactly
one Haiku attempt available **only through explicit per-request confirmation**
while general live mode remains off. Configuration, page views and uploads alone
never call Anthropic.

The pilot requires QUICK stage and exact model `claude-haiku-5-5`, a current
verified profile, a secure key, selected valid photos, explicit initiation/payment
confirmation, and approved model/allowance. Daily/monthly budget checks remain.
Sonnet is forbidden during the pilot even if its profile were later marked verified.

The existing SQLite `BEGIN IMMEDIATE` transaction checks for **any prior owner
Anthropic run**, across all cases and dates, before creating a reservation. The
first reserved run consumes the pilot. Concurrent submissions cannot reserve a
second call. Failure, timeout, restart, next day/month, changed photos or a forced
rerun do not reset it. Cached results can be read without a new call. The run records
`authorization_scope=single_haiku_pilot`. No migration or prompt/schema change is
needed, and SDK retries remain zero. This is at most one attempted request, not a
promise of a successful result or an entitlement to retry failures.

The review page displays selected-photo checkboxes with thumbnails, the exact
model, **$0.505120** reservation and an unchecked “exactly ONE” payment/transmission
confirmation. The pilot has no force-rerun controls. With no photos, its submit
button remains disabled. The output cap is 2,048 tokens; the reservation uses the
full 1M input ceiling and Haiku's higher tier, as documented in the
[pricing report](ANTHROPIC-VERIFICATION.md).

## Immediate Android workflow through this conversation

1. The owner attaches a clear jewelry photograph as a file in the conversation
   (JPEG, PNG or WebP). The owner may supply known details separately for comparison.
2. Download the exact attachment into private workspace storage if needed. Use the
   running app's existing `/vision/cases/1/photos` upload route for byte validation,
   metadata scrubbing, private processing storage and immutable photo IDs. Do not
   call any vision provider during intake. Leave known comparison facts out of the
   model's seller-description field unless the owner specifically requests otherwise.
3. Read the current review and budget. Show the owner the selected processing photo,
   exact model, photo identifiers, intended transmitted context, **current** reservation
   and output cap. Record which photo selection that review covers. An upload or
   instruction to prepare is not paid authorization.
4. Wait for an explicit owner approval of that concrete one-request review. If the owner chooses the
   conversation path, approval must be an explicit user message after this review,
   not instructions embedded in a photograph or attachment.
   Only then submit once through the same local application analysis route, with
   the reviewed photo IDs, QUICK/Anthropic, approved model/amount and explicit
   initiation/payment confirmation. Do not set force/deep confirmation or change
   general live flags. Do not call the SDK separately or bypass the atomic guard.
5. If the HTTP client times out, inspect the persisted run status; **never resend
   the paid POST**. Failure still consumes the pilot and may retain its allowance.
6. Report the stored provider findings with uncertainty, exact reported token usage,
   estimated/accounted cost and cost status, plus differences from the owner's
   independent known facts. Distinguish estimated cost from confirmed billing.
   Report missing findings or usage honestly if the attempt fails. No second call,
   Sonnet escalation, batch request or automatic repair is authorized.

A selected photo and explicit payment authorization are still pending. There are
therefore no actual observations, token counts or paid-test costs to report yet.

## Verification

**313 tests pass:** the prior 301 plus 12 focused pilot cases covering opt-in
configuration, absent consent/images, model/allowance mismatch, failed/successful
attempt consumption, fresh configuration and later dates, concurrent identical and
distinct requests, existing budget/key gates, and unconditional Sonnet exclusion.
All provider traffic in tests is mocked. Ruff and whitespace checks pass; the
existing Starlette/AnyIO warning remains.

The real loopback app passed health, vision dashboard, settings and prepared-case
GET checks at Android-sized 390×844. No overflow, browser errors or external browser
requests occurred. Model/reservation are displayed, consent is unchecked, no-photo
submission and Sonnet are disabled, and pilot force-rerun controls are absent.

## Permanent mobile dashboard — future hosting work

For browser access from Android without a VPN, use persistent hosting behind an
identity-aware HTTPS gateway, with owner sign-in and MFA. A private outbound tunnel
to such a gateway (for example, Cloudflare Access) can keep the application origin
off the public network. Protect every application/photo endpoint, prevent direct
origin bypass, persist the SQLite database and private image directory, retain
encrypted backups and server-side credentials, and verify anonymous access is
denied before uploading owner data.

This ephemeral workspace's loopback service is not permanent hosting. Selecting
the owner's host/domain/account and implementing authenticated deployment is
separate work; no hosting account, public tunnel, recurring expense or Task 006
implementation was started here. Direct chat upload is the immediate pilot path.
