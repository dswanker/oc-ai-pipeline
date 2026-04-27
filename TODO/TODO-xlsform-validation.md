# TODO — XLSForm Validation in edc-builder

**Status:** Approved approach, not yet implemented.
**Created:** 27 April 2026
**Origin:** Dan noticed errors in generated XLSForm files. Wanted to add a
validation step using ODK's XLSForm Online tool (https://getodk.org/xlsform/).
Research confirmed the tool is a thin wrapper around the open-source `pyxform`
Python library, so we'll use the library directly rather than HTTP-calling
the website.

---

## Why not the website?

Originally Dan suggested calling https://getodk.org/xlsform/ via HTTP.
Investigation revealed:

- The website is `xlsform-online`, a Django app at
  https://github.com/getodk/xlsform-online
- It uses `pyxform` (the open-source validator) plus an Enketo preview render
- We only need the validation half, not the preview
- Running `pyxform` locally is strictly better than HTTP-calling the website:
  no network failures, no rate limits, faster, no third-party dependency,
  pinned version in requirements.txt

**Decision:** Use `pyxform` as a local Python dependency. No URL config, no
env var, no external service.

---

## Two-phase plan

### Phase 1 — Basic validation with warnings (small scope, ~1 hour)

Add `pyxform` to `requirements.txt`. Create a new helper that validates each
generated XLSForm immediately after build. Capture errors and warnings.
Surface them in the build report. Pipeline proceeds even if errors found
(Behavior B — warn but don't halt).

**Scope:**
- Add `pyxform>=2.1.0` to `requirements.txt`
- New file: `skills/edc-builder/scripts/validate_form.py`
  - `validate_xlsform(xlsx_path) → (is_valid, errors, warnings)`
  - Uses `pyxform.xls2xform.xls2xform_convert(validate=False, pretty_print=False)`
  - Catches `PyXFormError`, `PyXFormReadError`, generic `Exception`
- Modify `skills/edc-builder/scripts/build_xlsforms.py` to call the validator
  after each form is built, accumulate per-form results
- Modify build report generation to include a "Validation Results" section
  per form (under the existing checklist or as a new section)
- Add validation summary to `BUILD_README.txt`
- If any form has errors, log to Railway and surface in Monday status
  message (but don't fail the chain)

**Out of scope for Phase 1:**
- Auto-fixing errors
- Java / ODK Validate JAR (we use `validate=False`)
- Learning from errors over time (deferred to Phase 2)

**Acceptance criteria:**
- Pipeline runs on CRS-138 successfully
- BUILD_README.txt includes a per-form validation status
- Forms with errors are clearly flagged
- Pipeline completes (does not fail) even when errors are found
- Total pipeline runtime increases by < 30 seconds

**Estimated effort:** 1-1.5 hours implementation, plus testing on 1-2 real
protocols.

---

### Phase 2 — Auto-resolve + learning loop (larger, deferred)

**This phase has significant overlap with the RAG/Trainer plan in
`FUTURE-PROJECT-rag-and-trainer.md` — see that doc for related work.**

After Phase 1 ships, accumulate enough real-world error data to design this
feature properly. The vision:

1. Pipeline generates a form
2. `pyxform` finds errors
3. **Auto-fix where possible** — for known error patterns (e.g. "missing
   `name` column", "calculate field needs `calculation` column"), apply
   programmatic fixes and re-validate
4. **Log unfixable errors** to a structured file
   (e.g. `learnings/xlsform_error_patterns.yaml`)
5. **Track over time:** "if Claude generates X pattern, it produces error
   Y N% of the time"
6. **Inject anti-patterns into `EDC_STRUCTURE_PROMPT`** so Claude stops
   making the same errors

**Scope (rough):**
- Error categorization taxonomy
- Pattern matching engine for known fixable errors
- Auto-fix module with N specific fixers (one per pattern)
- Structured learning log format
- Prompt injection mechanism (similar to RAG retrieval flow)
- Regression test suite to ensure auto-fixes don't break valid forms
- Versioned learnings — track when each pattern was added

**Why deferred:**
- We don't have enough real error data yet to know which patterns matter most
- Needs more design — error categorization, pattern matching, prompt injection
  format
- Phase 1 will inform what Phase 2 needs to handle
- Closely related to the RAG/Trainer work; might be merged into that
  effort rather than a separate phase

**Estimated effort:** Several days (1-2 weeks), assuming RAG/Trainer
infrastructure is in place.

**Trigger to start:**
- Phase 1 has been running in production for 2-4 weeks
- Have collected real error data showing which patterns are most common
- Either (a) starting RAG/Trainer Phase 2, or (b) error frequency is high
  enough that auto-resolve alone is worth the effort
- User on Anthropic Enterprise account

---

## Files involved

**Phase 1:**
- `requirements.txt` — add `pyxform>=2.1.0`
- `skills/edc-builder/scripts/validate_form.py` — NEW
- `skills/edc-builder/scripts/build_xlsforms.py` — MODIFIED (call validator)
- `skills/edc-builder/scripts/generate_build_checklist.py` (or similar) —
  MODIFIED (include validation section)
- `skills/edc-builder/scripts/build_readme.py` (or similar) — MODIFIED
  (validation summary)

**Phase 2:**
- `learnings/xlsform_error_patterns.yaml` — NEW
- `skills/edc-builder/scripts/auto_resolve.py` — NEW
- `prompts.py` — MODIFIED (anti-pattern injection in `EDC_STRUCTURE_PROMPT`)

---

## Related docs

- `FUTURE-PROJECT-rag-and-trainer.md` — the larger vision for learning from
  pipeline output. Phase 2 of this TODO is essentially a narrow-scope subset
  of that vision (error patterns vs. style preferences).
- `FUTURE-PROJECT-uat-runner.md` — UAT Runner is a different quality gate
  (catches form-design errors at the data-validation layer, vs. this TODO
  which catches errors at the form-structure layer). Both are needed.

---

## Open questions for Phase 1 implementation

1. **Where exactly does the validation section appear?** Options:
   - New section in `BUILD_README.txt`
   - New page or table in the Build Checklist PDF
   - Both — summary in PDF, detail in README
   - Recommendation: both, with PDF showing pass/fail counts and README
     showing per-form error detail

2. **What format for error display?** Options:
   - Plain text with form name + error list
   - Markdown table with form, error type, message
   - JSON file alongside other build artifacts
   - Recommendation: plain text in README + summary table in PDF

3. **Should validation results be uploaded to Monday?** Options:
   - Yes — new column for "Validation Status" with pass/fail/N errors
   - No — already in the build report, no need to duplicate
   - Recommendation: defer until we see how often errors actually occur

4. **What about the `validate=True` case (with Java)?** Options:
   - Add Java to Railway container, enable it, keep both modes available
   - Skip indefinitely
   - Recommendation: skip until Phase 1 data shows pyxform-only is
     missing important errors
