# Task 005 structured-validation repair — October 30, 2026

This is an offline repair based on merged PR #6, commit
`8dceffb3beec051e9dcc8143df19c93f419c6951`. No Anthropic request, billing
reconciliation, authorization reset or new pilot preparation is part of this work.

## Historical evidence and limits

The authorized real-photo attempt retained HTTP 200, a sanitized provider request
ID and a Pydantic `ValidationError` at `RESPONSE_PARSING`. This establishes that
the request reached an HTTP response and failed during SDK structured parsing.
It does not identify the rejected field, its text, whether the response was
truncated, its token usage or its billed cost. No raw historical output was saved;
the new diagnostics cannot reconstruct missing evidence retrospectively.

The offline reproductions below demonstrate real validator risks. They do **not**
establish the exact cause of that historical failure. Parsing/format, wording,
length, enum and evidence-rule failures remain possible. The production
post-response photo-reference checks were not reached in that attempt.

Both failed attempts, consumed authorizations and their $0.505120 reservations
remain unchanged and held pending independent billing reconciliation. Successful
earlier diagnostics, the original photo and processing copy are also preserved.
HTTP 200 and validation failure are not proof of a charge or absence of a charge.

## Reproduced risks and repairs

| Rule | Offline finding | Repair or retained restriction |
| --- | --- | --- |
| Negation | `This piece is not authenticated` and `Cannot determine whether this is sterling from the photograph` were rejected by blanket word matches. | Recognize narrowly specified, complete caution clauses; preserve accepted text verbatim. Scan the original full text so conjunctions, appended positive claims, decimals and newlines do not bypass other bans. |
| Attribution caution | Generic denials such as `No tribal attribution can be established from this photo` and `Cannot determine who this was made by` were rejected. | Accept these bounded generic uncertainty forms. Named cultural speculation and prose maker attribution remain rejected. Maker names still belong only in the unverified, evidence-linked research field. |
| Classification | The prompt requested `INCONCLUSIVE`, absent from the classification enum. | Prompt now requests `UNKNOWN` with an explanation in the summary. Invalid enum values are still rejected, never coerced. |
| Partial hallmark | Partial fragments excluded `&`, apostrophe, slash and plus, despite full transcriptions allowing them. | Allow the same bounded ASCII punctuation in fragments plus `?`; retain five-fragment and ten-character limits. Partial/unclear marks still cannot have a full transcription. |
| Maker evidence | Generic marks such as `14K`, `EPNS` and `1/20 12K GF` could pass as distinctive maker support. | Reject generic purity/material/coating tokens as maker evidence; retain directly legible, METAL, DIRECT support and unverified lead status. A distinctive mark is a research prerequisite, not proof of identity. |
| Confidence/references | INFERRED/HIGH, missing or duplicate finding keys, unknown images and cross-photo sub-item support are invalid. | Keep rejection and attach fixed internal evidence/reference rule identifiers. |
| Shape/length | Missing fields, extra fields, long prose, invalid enums/IDs and incomplete JSON remain invalid. | Keep all required fields, length/count bounds and enum constraints. Record sanitized format diagnostics without truncating, rewriting or accepting invalid output. |

Unsupported affirmative material, weight, value, authentication, cultural, stone
treatment and mine claims retain their existing restrictions. `The cuff is
sterling-plated` remains rejected: it asserts a composition; cautious visible
wear observations and `plating cannot be ruled out` remain acceptable.

These are conservative lexical/evidence guards, not a general natural-language
proof system. Some differently phrased cautious sentences still require review;
no blanket negation exemption or automatic text repair is introduced.

Prompt version is now `jewelry-3`, schema contract version `2`. Cache identity
already includes both versions. Existing results are retained, not silently
reinterpreted or used as a newly validated response.

## Safe diagnostics and response boundary

Pydantic structured errors are requested with `include_input=False`,
`include_context=False`, `include_url=False`. Raw messages are never copied.
Diagnostics retain only schema-allowlisted field paths, bounded list indices,
allowlisted validation types, fixed internal rule identifiers and fixed categories
(`formatting`, `safety`, `evidence`, `references`, `unknown`). Model-level rules may
identify the containing object/root rather than a single field. Arbitrary field
names and unknown error types are replaced with constants. At most 32 entries
are retained. No seller or model-generated text is stored through this channel.

The HTTP response hook buffers the same response before SDK post-parsing, capped
at **2 MiB of decoded bytes** for both success and error responses. It reads in
64 KiB pieces and rejects oversized Content-Length before reading, while counting
actual decoded bytes regardless of missing/invalid/understated length headers.
Gzip expansion is bounded too. Oversized streams close immediately and retain only
safe metadata with `RESPONSE_TOO_LARGE`; the reservation remains held and no retry
is permitted. The cap bounds retained body bytes, not all process memory: bounded
decoding chunks, copies and JSON objects also consume memory.

After the bounded read succeeds, the helper populates the pinned HTTPX2 response
content cache so the SDK consumes the same decoded body without another request or
second decompression. This small dependency integration is tested with chunked and
gzip responses and should be rechecked on HTTPX2 upgrades.

For successful responses, it inspects the envelope in memory only and retains bounded integer
usage counters, an allowlisted stop reason, status and sanitized request ID.
Content, response ID, stop sequence, arbitrary envelope fields and bodies are not
retained. The SDK still performs `messages.parse()` with `VisionResult`, followed
by production validation and photo-reference checks. This makes future parsing
failures diagnosable without a raw-response archive or a second API call.

Truncation, refusal and other non-`end_turn` results remain rejected, even if their
JSON otherwise validates. Safe token metadata is evidence only: failed runs keep
the entire reservation, with no inferred actual cost or automatic release.

## Verification

The baseline was 342 tests. The complete repaired suite passes **441 tests**,
including 99 new synthetic regressions (16 added in the final buffer-safety check).
Tests use mock transports and temporary
databases, with external transports blocked in the new regression module.
They cover accepted caution verbatim, adversarial negations, unchanged bans,
partial marks, maker evidence, mixed-lot references, bounds/enums, wrapped errors,
diagnostic redaction, malformed/truncated output and HTTP-200 parsing failures.
A chunked mocked response confirms that metadata extraction and SDK parsing use
the same response exactly once. Service tests verify retained reservations,
no persisted invalid findings and rejection of another one-shot attempt.
Buffer tests cover the exact limit, overflow, prebuffered responses, missing/invalid/
misleading length headers, gzip expansion, early stream closure, error bodies and
SDK-wrapped size failures without retries or reservation release.

Ruff and formatting checks pass. The existing Starlette/AnyIO deprecation warning
is unchanged. Desktop fixture workflows and nine Android-sized routes also pass
with no browser errors or external requests. They use a new temporary database,
synthetic photos and a loopback-only server with paid calls disabled.

A before/after comparison verified all 26 existing database tables and hashes of
14 private evidence/image files unchanged. There are still four historical runs,
including both failed $0.505120 holds. Fixture/live-disabled defaults, $5 monthly
budget, 10/day limit, Sonnet restrictions and consumed authorizations are unchanged.

No new request is authorized by these results; remote end-to-end
confirmation would require a separately reviewed and approved future test.
