# oc-ai-pipeline — TODO / Backlog

Items listed roughly in priority order. Move to DONE when complete.

---

## 🔴 High Priority

### Add Item Level Security to EDC Build
**What:** Wire Item Level Security (ILS) configuration into the study build flow.
**Where:** `pipeline.py` / `oc_form_publisher.py` — after forms are published to TEST/PROD.
**Notes:** ILS controls which roles/users can see individual items on a form. Needs to be part of the automated build so it doesn't have to be configured manually post-build.
**Added:** 2026-06-08

### AE Form: no fields visible in OC4 TEST environment
**What:** Opening the AE form for a UAT participant shows only the group header "Adverse Event / Adverse Device Effect" with no fields and no way to add an entry. Close/Complete buttons only.
**Observed:** 2026-06-08 on participant UAT-20260608-124957-P001 in cust1 TEST env.
**Possible causes:**
- Repeat group not configured with min-count=0 / appearance=minimal — form starts collapsed with no add button
- SE_COMMON event not properly scheduled/opened for the participant
- Form version not published to the TEST site being used
**Investigate:** Compare AE XLSForm repeat group settings against a working form. Check if SE_COMMON has been properly activated for UAT participants.
**Added:** 2026-06-08

### Playwright UAT — Repeating group handling
**What:** Forms with repeat groups (AE, AESAE, CM, MH, DV) start empty — Playwright needs to click "Add" to create a new repeat instance before fields appear.
**Where:** `playwright_uat.py` — add `_open_repeat_group()` helper called before field interactions for repeat-group forms.
**Added:** 2026-06-08

### Playwright UAT — Form navigation (open Enketo form)
**What:** ParticipantDetailsPage loads participant summary, not an open form. Playwright needs to click the specific form link to open it in Enketo before reading UI state.
**Timing impact:** Without fix — 10s timeout per form × 24 forms = ~4 min overhead. With fix — ~3s per form click = ~72s total. Full Playwright run target: ~3 min.
**Current total run time:** ~10-11 min (ODM ~51s + Playwright ~8-10 min). Target: ~4 min.
**Options:**
- (A) Playwright clicks form link in hub.html iframe after landing on ParticipantDetailsPage
- (B) Find direct Enketo form URL — bypasses participant details page
**Where:** `playwright_uat.py` — `_form_entry_url()`, nav block, form_frame detection
**Added:** 2026-06-08

### Calc Fields returning 0 (ODISUM, PCSTOT, PHQTOT, AGE)
**What:** Calculated fields compute to 0 despite correct input values being loaded via ODM.
**Root cause:** ODISUM formula sums ODI1-10 but DVS test only loads ODI1-4. Missing ODI5-10 default to 0.
**Fix needed:** DVS generator (`extract_dvs_from_forms.py`) should detect calc formulas and include ALL referenced input fields in the Load_Value, not just the first few. Alternatively, load a valid value for all ODI/PCS/PHQ items when generating the calc test case.
**Added:** 2026-06-08

---

## 🟡 Medium Priority

### Human-editable DVS test cases
**What:** Allow testers to add, remove, or modify UAT test cases in the DVS XLSX directly, with the pipeline able to ingest those changes and execute them.
**Use cases:**
- Tester adds a new test case the generator missed
- Tester modifies a Load_Value or Expected Result to be more precise
- Tester removes a test case that doesn't apply
- Tester adds plain-language field/form names (not OID format)

**Design decisions needed:**
1. **Merge strategy on regen-dvs**: currently regenerates from scratch, wiping human edits.
   Options: (a) merge generated rows with human-edited DVS — preserve human changes,
   add new generated rows, fill in missing OIDs from XLSForm; (b) lock generated rows
   and only add human rows to a separate "Custom Tests" section.
2. **OID derivation**: human writes "AEYN" → pipeline maps to I_AE_AEYN using XLSForm.
   Human writes "AE form" → pipeline maps to F_AE.
3. **Human-deleted rows**: if a row exists in generated set but not in human file,
   treat as intentional deletion — don't recreate on regen.
4. **Custom UAT Case ID prefix**: human-added rows get "UAT-CUSTOM-xxx" prefix so
   pipeline can distinguish them from generated rows.

**New Monday column needed:** Add a file column to the AI Study Hub board (e.g. `file_mm_dvs_override`) for "DVS with Custom Tests". Tester downloads the generated DVS, edits it, uploads back here.

**Pipeline logic on regen-dvs:**
- If `file_mm_dvs_override` is populated → download it → use as base for merge
- If empty → generate fresh DVS (current behavior)
- Merge: keep all `Custom` rows from override; regenerate `Generated` rows fresh; fill in any blank OIDs on custom rows using XLSForm lookup

**Monday board change:** Add column `file_mm_dvs_override` (File type) with label "DVS with Custom Tests" to board `18409146946`. Update `monday_client.py` COL dict with new column ID.

**Files affected:** `main.py` (regen-dvs endpoint),
  `skills/dvs-specification/scripts/generate_dvs.py` (merge logic),
  `uat_loader.py` (no change needed — already handles any valid row),
  `monday_client.py` (new COL entry)

**Added:** 2026-06-08



### UAT — Results file naming for UAT Results
**What:** `CRS-135_DVS_UAT_Results.xlsx` accumulates versions in `file_mm3h5s3h`. Clear before each run or use a single named file.
**Added:** 2026-06-08

### Build Preview: pyxform fails for DV and PE forms
**What:** `[row:N] List name not in choices sheet: yn` — cross-form `yn` choice list missing from per-form choices sheets.
**Where:** EDC builder / XLSForm generation.
**Notes:** Fix before customer demo.
**Added:** 2026-05-xx

### Trainer Board Duplicate Rows
**What:** Running same protocol multiple times creates duplicate trainer board rows instead of updating existing one.
**Where:** `pipeline.py` — trainer row creation / `create_pending_row`.
**Added:** 2026-05-20

### Trainer Spec Passthrough + Patch 14 Decomposition
**What:** Implement spec passthrough before re-baselining trainer accuracy. Then decompose Patch 14 (14a/14b/14c).
**Where:** `docs/TRAINER_SPEC_PASSTHROUGH.md`
**Notes:** Do NOT start until spec passthrough is done and new baseline established.
**Added:** 2026-05-xx

---

## 🟢 Lower Priority / Deferred

### Enterprise Migration
**What:** Move repo to company GitHub org, rotate Railway `ANTHROPIC_API_KEY`, move Railway project + volume, decommission personal accounts.
**Where:** `docs/` — see memory for phases.
**Notes:** Billing in flight; resume when resolved.
**Added:** 2026-05-xx

### Syndeo Rename
**What:** Update titles, README, `package.json`, Railway to "Syndeo by OpenClinica". Replace CSS logo with OC swoosh SVG in `Header.jsx` and `LoadScreen.jsx`.
**Added:** 2026-05-xx

### Form-Definition Upload API
**What:** When OC engineering releases the upload API, integrate with `study-service.adoc` to enable full end-to-end automated publishing. Currently XLSForm package is built but upload is manual.
**Added:** 2026-05-xx

### CQ_* Convention Questions Expansion
**What:** 46 full questions across 9 categories saved to `EDC_Build_Questions_For_Team_Review.xlsx`. After team review, add approved questions as `CQ_`-prefixed columns.
**Added:** 2026-05-xx

### XLS-to-ODM Converter
**What:** Accept customer freeform XLS relational DB dumps as migration input, convert to ODM XML, feed existing pipeline.
**Where:** `docs/XLS_TO_ODM_PREPLAN_2026-06-07.md` — build order: `odm_serializer.py` → `xls_reader.py` → `soe_parser.py` → orchestrator → pipeline routing.
**Notes:** SOE file and additional codelist examples pending before starting.
**Added:** 2026-06-05

---

## ✅ Done (recent)

- ODM UAT loader working end-to-end (Pass=10 Fail=0 on AE form) — 2026-06-07
- OpenClinica namespace `.com` → `.org` fix — 2026-06-07
- `runFormLogic: y` parameter added to ODM import — 2026-06-08
- Choice values loading correctly from XLSForm choices sheet — 2026-06-08
- DVS filename standardised to `{protocol}_DVS_V{timestamp}.xlsx` — 2026-06-08
- Playwright UAT module created (`playwright_uat.py`) — 2026-06-08
