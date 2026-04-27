# TODO Folder Index

This folder holds in-progress and future planning documents for the
oc-ai-pipeline and related products. Updated as items are added, completed,
or moved.

**Last updated:** 27 April 2026

---

## Active future projects (not started, awaiting trigger)

### FUTURE-PROJECT-rag-and-trainer.md
**Status:** Planning. Awaiting Anthropic Enterprise account move.
**Summary:** Two related capabilities — (1) RAG retrieval layer that injects
similar past protocol-form pairs into oc-ai-pipeline's EDC_STRUCTURE_PROMPT
to improve build accuracy, and (2) OC4 EDC Build Trainer, a standalone tool
customers run on their historical data to produce a personalized skill
encoding their style preferences.
**Timeline:** RAG 1-3 months. Internal trainer ~6 months. Productized 12 months.
**Trigger:** User on Enterprise account + engineering team aligned on
embedding model and vector DB choices + initial corpus identified.

### FUTURE-PROJECT-uat-runner.md
**Status:** Planning. Awaiting Anthropic Enterprise account move + engineering
input on publish-to-Test API.
**Summary:** Standalone microservice that takes a DVS XLSX (formatted to our
spec) and executes its UAT cases against a published OpenClinica study,
captures actual results, and updates the DVS with pass/fail outcomes.
Sellable bundled with oc-ai-pipeline (Chain E) OR standalone for any
customer with a properly-formatted DVS. v1 assumes human publishes to Test.
**Timeline:** PoC 3-4 weeks. Internal tool 6-8 weeks. Productized 6-12 months.
**Trigger:** User on Enterprise account + engineering confirms publish-to-Test
path forward.

---

## Active operational TODOs (smaller fixes, can be picked up anytime)

### TODO-concurrency-queueing.md
**Status:** Documented, not implemented.
**Summary:** oc-ai-pipeline currently processes Monday webhooks synchronously.
Two simultaneous "Send to AI" triggers could cause shared workspace
collisions, rate limit hits, and OC board-import races. Recommended fix is
Option 2: in-process queue + 202 response, ~30 lines of code.
**Estimated effort:** 1-2 hours.
**Priority:** Low until a second user/sales-rep starts using the pipeline
concurrently.

### TODO-iterate-uat-input-data.md
**Status:** Identified, deferred.
**Summary:** After commit `c72d307` shipped the placeholder replacement and
cross-form date continuity, real-world re-runs confirmed the DVS UAT_Cases
Input Data column is significantly better but still not perfect. Needs a
focused 2-4 hour cleanup pass to capture remaining bad cells, identify
pattern-level fixes (constraint expressions the gate evaluator can't parse,
field names not covered by `_build_sample_context()`, edge cases in cross-
form continuity), and add unit tests.
**Estimated effort:** 2-4 hours per iteration.
**Priority:** Medium. Pick up when customer feedback flags specific bad
cells, when UAT Runner Phase 1 needs cleaner input to test against, or when
a new protocol with a different TA exposes context-coverage gaps.

### TODO-xlsform-validation.md
**Status:** Approved approach, not yet implemented. Two-phase plan documented.
**Summary:** Add XLSForm validation to edc-builder using `pyxform` library
locally (not HTTP-calling getodk.org). Phase 1 (~1 hour): basic per-form
validation with `validate=False`; errors and warnings surfaced in build
report; pipeline proceeds even on errors. Phase 2 (deferred, days of work):
auto-resolve known error patterns + learning loop that feeds anti-patterns
into `EDC_STRUCTURE_PROMPT`. Phase 2 is closely related to and may be merged
with the RAG/Trainer project.
**Estimated effort:** Phase 1: 1-1.5 hours. Phase 2: 1-2 weeks.
**Priority:** Phase 1 is high (catches errors before forms ship to OC).
Phase 2 deferred until real error data accumulated.

---

## Smaller items not yet documented (reminder list)

These are tracked here pending dedicated docs:

- **F6: per-chain timing metrics.** Add timestamps to each chain's start/end
  and log to Monday item or a metrics column. Useful for performance tuning.
- **F7: concurrency guard against duplicate webhooks.** Sometimes Monday
  fires the same status-change webhook twice. Add idempotency check using
  item_id + version number.
- **SOE CSV update path.** Currently blocked by Copy Study Design's clone-into-
  empty limitation. Need to revisit when OC team addresses OC-18941.
- **XLSForm upload via Playwright.** Blocked pending confirmation that forms
  load individually (rather than batch). Would automate the manual step
  customers do today.
- **Pre-existing duplicate "REQUIRED TOP-LEVEL KEYS" sections in prompts.py.**
  Pre-dates current session. Cosmetic cleanup. Low priority.
- **CRF library XLSX format support.** Pipeline expects PDF on `crf_library`
  column, ZIP on `oc_standard` column. XLSX not currently supported. User
  workaround: convert XLSX→PDF before upload. Could be added if requested.

---

## Completed items (recent)

For historical reference. These are NOT pending — they're already shipped.

- ✅ OC-1 through OC-9 rules in EDC_STRUCTURE_PROMPT (commits 3a03de1, e04fd62)
- ✅ Discount display restoration in pricing quotes (commit 2d0cad6)
- ✅ DVS mechanical extractor with 5-case UAT range expansion (commit 29d6839)
- ✅ Protocol Title row in Study Spec PDF + SoE sub-table in Protocol Summary
  PDF + Chain D nameNotUnique fix (commit f82098e)

---

## How to use this folder

1. **Adding a new TODO:** Create a new `TODO-<topic>.md` or
   `FUTURE-PROJECT-<topic>.md` file. Add an entry to this README under the
   appropriate section.

2. **Picking up a TODO:** Read the doc, confirm assumptions still hold, then
   work it. Update the entry to reflect status changes.

3. **Completing a TODO:** Move the entry from "Active" to "Completed items"
   with the commit reference. Optionally archive the doc itself by prefixing
   with `DONE-` or moving to a `done/` subfolder.

4. **Stale TODOs:** Items that haven't moved in 6+ months should be reviewed.
   Either revive them or document why they're being abandoned.
