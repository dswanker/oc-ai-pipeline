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

### TODO-edc-builder-choices-validation.md
**Status:** Not started.
**Summary:** Add a pre-validate step in `build_xlsforms.py` that scans every
form's survey sheet for `select_one`/`select_multiple` references and
cross-checks against the choices sheet. Known boilerplate lists (`yn`,
`saecrit`, `prepost`, etc.) are auto-injected if missing (soft recovery).
Unknown study-specific lists hard-fail with a clear message. Eliminates
build failures caused by Claude intermittently omitting known choices lists,
without requiring a full re-run to fix. Generalizes the existing `yn`
auto-inject pattern.
**Estimated effort:** 2-4 hours.
**Priority:** Medium. Current workaround (add list to OC-10 in prompts.py)
works but wastes a full Claude run each time a new list is discovered.

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
- **XLSForm upload via Playwright.** IN FLIGHT (May 2026 session). Forms
  upload individually. OID-driven panel walk implemented in oc_form_publisher.py
  (commit ffd97b2). Auth + URL fixes also landed. Pending: confirm upload
  succeeds end-to-end on a real pipeline run; set-default-version step is
  best-effort in v1 (goes to warnings, not errors).
- **Instructions page: show study-specific URL.** ✅ DONE (commit b48ceca,
  May 2026). Now points to {subdomain}.build.openclinica.io/#/account-study.
- **Trigger column reset on auth pause.** ✅ DONE (commit b48ceca, May 2026).
  Uses COL["ai_trigger"] + set_status("Do not Send To AI Yet").
- **Session lifetime and single-session-slot.** OC/Keycloak issues one session
  per user. The headless publisher session and the user's own Chrome session
  compete — a new login invalidates the captured one. Pre-flight check (shipped
  May 2026) detects stale sessions fast. Long-term: ask OC engineering to
  enable service-account or multi-session support so sessions don't compete.
- **Board card accumulation on full runs.** Each full pipeline run calls
  _import_board which appends to existing board content (CLONE-INTO-EMPTY
  only works on a truly empty board). After several full runs: 69 → 131 →
  194 cards. Fast-reruns skip the import so this only affects full runs.
  Fix: discover the OC designer delete API by inspecting Chrome DevTools
  Network tab while manually deleting a card/list on the designer board —
  look for DELETE or PUT /1/lists/{id} or /1/cards/{id} calls. Then add
  a _clear_board() helper in pipeline.py that runs before _import_board.
  Investigation is safe to do anytime via the designer UI. Low priority
  until full runs become frequent (e.g. new customer protocols).
- **Cleanup after Playwright debugging.** ✅ DONE (commit b48ceca, May 2026).
  /debug/dom removed, session delete re-enabled, DEBUG_KEY should still be
  rotated in Railway dashboard (low priority since endpoint is gone).
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
