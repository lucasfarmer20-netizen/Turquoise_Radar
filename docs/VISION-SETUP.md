# Jewelry vision setup — Task 005

This is an owner-operated visual **research assistant**. A visual score is not an
appraisal, material verification, authentication, buying instruction or profit
estimate. Existing financial analysis, Alaska shipping gates and purchase evidence
remain independent. No vision operation automatically edits those facts.

## Default, free workflow

1. Open an existing workbench listing or discovery/inbox candidate and choose
   **Review photographs**. The **Vision Review** navigation lists existing cases.
2. Upload one to four owner-provided JPEG, PNG or WebP photographs. Label front,
   back/hallmark, stone and side/detail roles. Mobile file/camera selection and
   desktop drag-and-drop use the same bounded upload path.
3. Click an image for a zoomable processing-copy preview. Replace a blurry image
   or remove it from the active selection before adding another close-up.
4. Click **Analyze Jewelry · free synthetic fixture**. This makes no external
   request and needs no key. Fixture results are deterministic UI demonstrations,
   explicitly labelled synthetic; they **do not read real uploaded image contents**.
5. Inspect photo-linked findings, confidence, DIRECT vs INFERRED evidence,
   uncertainty, legibility and next research actions. Compare the original seller
   description with model observations and independent owner facts.
6. Append correct/incorrect/uncertain feedback or owner notes. A visual correction
   such as “sterling verified separately” is only a note; independently document
   verified facts in the existing evidence editor. A visual Purchased note is not
   financial purchase feedback.
7. Optionally confirm a **separate synthetic deeper comparison**, or later use
   explicitly authorized Sonnet investigation. Previous observations are retained.
8. Return to the original buying/financial workflow. Candidate analysis does not
   promote a candidate, create a purchase or create individual marketplace lots.

Mixed-lot sub-items identify distinguishable research locations within the original
photo/listing. Mark a sub-item for review and upload a better close-up in the same
case. No exact overlapping counts, weights or cost allocations are inferred.

## Separate synthetic demonstration

Use an explicit separate database, never owner inventory:

```bash
UV_CACHE_DIR=/tmp/turquoise-uv-cache uv sync --frozen
.venv/bin/python -m radar.jewelry.demo
RADAR_DATABASE_URL=sqlite:///./data/vision-demo.db .venv/bin/uvicorn radar.main:app --host 127.0.0.1 --port 8000
```

This generates two clearly synthetic listings, eight illustrated role photographs,
and three fixture runs: quick/deep ring reviews and a quick mixed-lot review. No
purchases or financial estimates are seeded. Repeating the demo reuses the photo
identities and cached results. All generated files stay in ignored private storage.

## Anthropic account and secure configuration

Anthropic is the only live provider. An Anthropic API account with usable API
credits and model permissions is required; a consumer chat subscription is not
proof of API eligibility. Existing credits are not a reason to spend automatically.

Defaults (also documented in `.env.example`):

```text
VISION_PROVIDER=fixture
VISION_LIVE_ENABLED=false
VISION_MODEL=claude-haiku-5-5
VISION_DEEP_MODEL=claude-sonnet-5-5
ANTHROPIC_API_KEY=
VISION_DAILY_LIMIT=10
VISION_MONTHLY_BUDGET_USD=5.00
```

Supply the actual key through secure server/environment secrets. Never paste it
into chat, a browser form, a pricing profile, SQLite, source files or startup scripts.
The app does not load `.env` automatically. No secret requirement/binding was added
or changed during this task. Models remain configurable without source changes.

For later, separately authorized live use:

1. Verify current model availability and access in the intended Anthropic API
   account. The pinned SDK recognizes both requested identifiers, but that is not
   proof of account access, vision capability, context limits or pricing.
2. Supply a secure `ANTHROPIC_API_KEY`; configure `VISION_PROVIDER=anthropic` and
   `VISION_LIVE_ENABLED=true`. Restart the private app to pick up environment changes.
   A key by itself never enables a request.
3. Authorize HTTPS access to **api.anthropic.com** through the managed environment's
   supported network settings, preserving other destinations. The current instance
   does not allow it. This task did not authorize a paid test or add that grant.
4. Open **Vision settings & spending** and append a dated price/access profile for
   each desired model after checking official documentation and account conditions.
   No actual rates are prefilled or invented. A profile requires an official source
   URL, verification date, conditions, input/output rates, the **full documented
   input-context ceiling**, output limit and explicit account/model verification.
5. Review the displayed model and maximum allowance. Select photos and explicitly
   confirm transmission/payment for each live run. Sonnet additionally requires
   a separate deeper-investigation confirmation. There is no automatic escalation.

The initial live blocker list explains missing key, disabled mode, unverified
pricing/model eligibility or insufficient budget. There is no “test key” request
button that might accidentally consume credits.

## Current SDK and structured outputs

Dependencies are pinned with hashes in `uv.lock`: official `anthropic==2.3.0` and
`pillow==13.0.0`. The existing `uv sync --frozen` installation handles them; no new
service, installer, database system or cloud startup command is required.

The provider uses server-side `AsyncAnthropic.messages.parse`, with
`output_format=VisionResult` as the SDK's Pydantic helper argument. The verified SDK
translates that into the current Messages API's **`output_config.format` JSON
schema**; `output_format` is not sent as a legacy HTTP body parameter. There are no
beta structured-output flags or tool calls. Returned results are validated again,
including photo IDs, strict field sets and evidence restrictions, before saving.

The endpoint is fixed to `https://api.anthropic.com/v1/messages`. Four photos remain
up to four separate base64 `image/png` blocks, each paired with its photo ID/role;
no composite image, remote URL source or marketplace fetch is used. Standard speed
and `standard_only` service tier are requested. Explicit HTTP timeout is 45 seconds,
with an outer bounded request lifetime. SDK retries and redirects are disabled.
SDK content telemetry is explicitly disabled; SDK/HTTP debug payload logging is
suppressed. Provider errors are stored/displayed as fixed codes, not response
bodies, exception text, credentials or image bytes.

SDK 2.3.0 uses `Authorization: Bearer <API key>` internally and defaults to API
version `2026-10-21`; this was checked in the pinned official source and mocked wire
requests. Do not assume an older SDK's `x-api-key`, `httpx` types or beta helper.
This SDK uses `httpx2`; its mocked transports are separately covered. Application
HTTPX/eBay behavior is unchanged.

## Cost, limits, cache and concurrency

- Paid requests are off by default. Ten paid starts per UTC day and $5 per UTC
  month are defaults, not an account quota or an Anthropic credit-balance readout.
- Fixture runs never count toward paid usage. Page views/uploads/corrections never
  run a model. No workers, polling, automatic retries or escalation exist.
- The model price profile must cover standard synchronous input/output over the
  model's full documented input ceiling, including any long-context rate changes.
  Profiles expire after 30 days; future dates and missing model-access confirmation
  are rejected. Unknown current prices/model eligibility keep live calls blocked.
- Before sending, SQLite `BEGIN IMMEDIATE` serializes budget and daily-limit checks
  with run creation. The reservation is rounded **up** to a micro-dollar:
  `(full input ceiling × input rate + max output tokens × output rate) / 1,000,000`.
  This intentionally reserves much more than a typical short image request.
- The selected model/allowance must still match the owner's confirmation. An
  identical successful cache result or in-flight identical key prevents another
  call. Different requests share the same atomic owner budget.
- Cache keys include case/owner, image hashes and immutable IDs/roles, provider,
  model, stage, prompt/schema version, output limit and the actual supplied seller
  context. Email-derived descriptions (which may contain private receipt/address
  evidence) are withheld; these cases send the title and selected photos only.
  Changing a photo, role, relevant text or model changes the key. A forced
  rerun needs its own explicit confirmation; failed attempts are never retried
  merely because the page is refreshed.
- Successful input/output token usage produces an **estimated**, not confirmed,
  API cost. Cache-read and 5-minute/1-hour creation usage need their corresponding
  rates. Output detail counts are subdivisions of total output, not added twice.
  An unexpected reported model, tool, fast/priority/batch or regional usage, absent token counts, or
  usage exceeding a verified ceiling leave cost unknown and hold the allowance.
- Rate limits, provider failures, invalid structured results, timeouts and canceled
  requests retain the conservative allowance because a missing result is not proof
  of no charge. Unresolved older reservations carry into the current budget.
- A hard interruption leaves a durable reservation. It is never automatically
  refunded. After the bounded request lifetime plus a guard interval has expired,
  the owner may independently verify final billing and explicitly reconcile it.
  An in-flight request cannot be released. Reconciliation is separately audited;
  zeros require independently confirmed no-charge evidence.
- Runs preserve provider/model, owner/case, date, hashes, prompt/schema versions,
  token/additional usage, request ID when present, original findings, failure state,
  price snapshot, reservation, estimated cost and separately confirmed billing.
  The API response does not itself provide a confirmed invoice charge here.

Incorrect owner-supplied pricing/context ceilings can undermine any estimate.
Unexpected usage is held conservatively, never silently called free. This local
budget protects this app's calls, not spending by another application using the key.

## Photo privacy and storage

Actual image bytes are decoded and checked; filenames/MIME headers are not trusted.
JPEG/PNG/WebP only, non-animated, one to four active photos, maximum 10MB per input,
40MB per upload body plus bounded multipart overhead, 24MP / 10,000px input limits,
16px minimum dimensions. Corrupt files and decompression bombs are rejected before
storage. Pillow applies EXIF orientation and re-encodes new pixels into metadata-free
PNG processing copies at up to 2560px on the long side. Upload a tight hallmark crop
when a full-piece photograph does not retain legible lettering. No four-image
composite is made and no photo is fetched from a URL or email.

The original upload is not retained; original/processing hashes and dimensions are
recorded. Processing copies use generated IDs, 0700 directories and 0600 files,
outside static storage. Default location is a private sibling directory named
`<database-stem>-vision-images` (for example `data/radar-vision-images`). An optional
server-side `VISION_IMAGE_DIR` may select a private location; never use public
storage. SQLite stores references/metadata, never image binaries or keys.

Preview endpoints resolve opaque IDs through an owner-linked case, validate file
integrity and reject unsafe paths/symlinks. Responses are non-cacheable private PNGs.
Removing a photo deactivates it for future selection; processing copies referenced
by prior findings remain private for history. Back up both SQLite and its image
directory. Raw originals should be preserved separately by the owner if needed.
This is a single-owner unauthenticated app: keep Uvicorn bound to loopback/private
access. These endpoints are not suitable for public hosting. Data at rest is local,
not encrypted by the application. No mailbox or cloud-image integration is added.

## Evidence and failure limitations

Model text may be wrong. Strict results keep cultural attribution and material
verification UNVERIFIED and independent owner evidence NOT_ASSESSED. A maker name
is only an unverified research lead linked to a directly legible mark. Partial marks
cannot be completed/transcribed as legible text. Numeric weight, valuation and
unsupported authentication claims/fields are rejected conservatively; filters are
not a substitute for independent inspection. A stamp is not proof of metal purity.
Do not infer turquoise mine/treatment, age from patina, cultural maker identity,
exact obscured counts, Alaska shipping or resale value from these photos.

The deterministic 0–100 score adds each eligible signal once, requires direct
medium/high-confidence support for positive points and applies image/damage/plating/
conflict penalties. It ranks research only; it never changes BUY CANDIDATE, melt,
maximum buy or resale calculations. Original model output, owner corrections and
distinct quick/deep investigations remain separate. Comparison highlights evidence
differences without asserting which model is correct. Close-ups stay attached to
the original case; no fake individual lots, costs or weights are generated.

## Official references and verification status

Successfully read the pinned official SDK source and package metadata:

- [Official SDK README](https://github.com/anthropics/anthropic-sdk-python/tree/v2.3.0)
- [Messages parsing implementation](https://github.com/anthropics/anthropic-sdk-python/blob/v2.3.0/src/anthropic/resources/messages/messages.py)
- [SDK authentication/client implementation](https://github.com/anthropics/anthropic-sdk-python/blob/v2.3.0/src/anthropic/_client.py)
- [Recognized model identifiers](https://github.com/anthropics/anthropic-sdk-python/blob/v2.3.0/src/anthropic/types/model_param.py)
- [SDK migration guide](https://github.com/anthropics/anthropic-sdk-python/blob/v2.3.0/MIGRATION.md)

Official pages attempted but blocked by the current environment proxy:

- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Model overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
Additional unverified reference: [Vision documentation](https://platform.claude.com/docs/en/build-with-claude/vision).

No actual price schedule, account quota, credit balance or model permission was
verified. No live Anthropic request was made; all API tests use SDK mocks and spend
no credits. Tests and demo artwork are generated synthetic examples, not customer
photographs. New owner email/AI sample collection is not part of this task.

## Cloud environment impact

No Cloud Environment draft update or republication is required for fixture use:
the existing frozen install/start commands already support the new locked SDK and
Pillow, and the package-manager network preset supports dependency installation.
The existing local Node/Chromium/Playwright tooling supports browser checks. No
credential was requested or changed. Code/lockfile updates alone do not justify
changing the environment snapshot/configuration.

Later live activation genuinely requires secure key configuration, explicit live
flags, verified pricing/model eligibility and permitted `api.anthropic.com` access.
Only then use the onboarding integration for any actual missing network/secret
requirement and report whether its settings need publication. Do not add a network
grant, republish or make paid test calls merely to exercise this implementation.
