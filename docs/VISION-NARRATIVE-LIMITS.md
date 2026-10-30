# Vision narrative field audit — October 30, 2026

The latest real-photo response reached HTTP 200 and `end_turn`, but its summary
exceeded the former 800-character cap. This repair removes presentation-driven
character caps from all 12 free-form narrative fields. It does not recover or
revalidate that historical report: its raw response was not retained.

| Model | Field | Former character cap | Current cap |
| --- | --- | ---: | --- |
| VisionResult | summary | 800 | No field-specific cap |
| VisionResult | visible_southwestern_style | 500 | No field-specific cap |
| VisionResult | hallmark_candidate | 500 | No field-specific cap |
| Finding | description | 350 | No field-specific cap |
| Finding | visible_evidence | 800 | No field-specific cap |
| Finding | uncertainty | 600 | No field-specific cap |
| Finding | next_action | 400 | No field-specific cap |
| MakerCandidate | research_name | 100 | No field-specific cap |
| MakerCandidate | uncertainty | 500 | No field-specific cap |
| SubItem | label | 150 | No field-specific cap |
| SubItem | location_hint | 250 | No field-specific cap |
| SubItem | uncertainty | 400 | No field-specific cap |

Required/optional status, defaults and existing minimum lengths are unchanged.
The existing 2 MiB decoded HTTP response limit and 32,768-token Haiku QUICK output
allowance remain in force. The response limit bounds buffering, not total process
memory or every possible non-provider caller of the Pydantic model.

## Safeguards retained

Literal hallmark transcription is still limited to 40 characters. Partial marks
retain the five-fragment limit and their 1–10-character fragment pattern. Keys,
photo IDs, collection sizes, classifications, confidence/evidence combinations,
photo and finding references, hallmark legibility and maker evidence restrictions
remain enforced. Neither cultural attribution nor material verification can be
promoted from UNVERIFIED by generated observations. Invented weights, valuations,
authentication and unsupported material or attribution claims remain rejected.

The cautious-wording guard now scans ordered match/span sequences instead of
repeatedly searching every span. Its rules and accepted wording are unchanged;
this prevents quadratic containment work on longer explanations. A long valid
prefix does not hide an unsafe claim at the end of a report.

The output schema version advances from 2 to 3 so cache identity distinguishes
the changed contract. The prompt remains `jewelry-3`. The provider request code,
system prompt, implicit thinking and effort, max_tokens, standard service tier,
timeouts, response cap and zero-retry policy are unchanged. The generated output
schema reflects the removed narrative caps; no thinking or speed parameter is added.

## Complete private display

All 12 narrative fields retain their exact validated strings in storage and HTML.
Text longer than 800 characters uses native, keyboard-accessible disclosure
controls. This is only a display threshold: no text is truncated, rewritten or
discarded. Whitespace is preserved and long words wrap on narrow screens. Maker
uncertainty, previously stored but omitted from the report view, is now displayed.
Jinja autoescaping remains active; model-supplied HTML cannot create scripts,
images or external resource loads. Validation diagnostics still exclude generated
text and credentials, and photographs remain behind the existing private routes.

## Offline verification and preservation

- All 489 tests pass, including 40 new narrative cases. Ruff lint, formatting and
  Git whitespace checks pass. The existing Starlette/AnyIO deprecation warning remains.
- Synthetic long output passes the actual SDK parse, production post-validation,
  database persistence and report rendering path with a mocked HTTP transport.
  Each of the 12 fields is preserved verbatim. Tests also cover a 480,000-character
  cautious summary, late unsafe claims, evidence/reference failures and secret
  reflection. Invalid output still leaves no accepted findings and retains its hold.
- Chromium checks at desktop and Android-sized widths verify all 12 disclosures,
  keyboard toggling, full visible text after expansion, escaped markup, no overflow,
  no external requests and disabled paid analysis. Only a temporary synthetic DB
  and a loopback server were used.
- All 26 actual private database tables and 28 pre-existing private files match
  the pre-change snapshot. All four failed real-photo attempts, consumed
  authorizations, photographs and holds remain intact: three $0.505120 holds and
  one $0.581920 hold. No billing reconciliation was performed.
- Sonnet restrictions, fixture/live-disabled defaults, the $5 monthly budget and
  daily limits are unchanged. No Anthropic request, new authorization, retry or
  pilot preparation was performed. Live acceptance of a future report is untested.
