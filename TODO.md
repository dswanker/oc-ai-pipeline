# oc-ai-pipeline — TODO / Backlog

Items listed roughly in priority order. Move to DONE when complete.

---

## 🔴 High Priority

### Add Item Level Security to EDC Build
**What:** Wire Item Level Security (ILS) configuration into the study build flow.
**Where:** `pipeline.py` / `oc_form_publisher.py` — after forms are published to TEST/PROD.
**Notes:** ILS controls which roles/users can see individual items on a form. Needs to be part of the automated build so it doesn't have to be configured manually post-build.
**Added:** 2026-06-08

### Playwright UAT — Selector Tuning
**What:** First Playwright run will reveal whether the OC data entry URL format and field selectors in `playwright_uat.py` are correct.
**Where:** `playwright_uat.py` — `_form_entry_url()`, `_fill_and_save()`, `_is_field_visible()`, `_read_field_errors()`
**Notes:** OC legacy form URL pattern needs verification against actual rendered HTML. Field selectors may need adjustment based on OC's actual DOM structure.
**Added:** 2026-06-08

### Calc Fields returning 0 (ODISUM, PCSTOT, PHQTOT, AGE)
**What:** Calculated fields compute to 0 despite correct input values being loaded via ODM.
**Where:** `uat_loader.py` `_build_odm_xml` — calc input items may be landing in wrong ItemGroup structure.
**Notes:** OC formula may expect inputs in a repeating ItemGroup; need to verify ItemGroupOID used for calc inputs matches what the formula references.
**Added:** 2026-06-08

---

## 🟡 Medium Priority

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
