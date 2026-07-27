# Migration Capability — Status & Pickup Notes
**Last updated:** 2026-06-05
**Session:** Full audit of migration/ module, migration_pipeline.py, Syndeo UI,
             test suite, SKILL.md, vendor conventions, and TODO folder.

---

## What Is Built (complete and deployed)

### Core pipeline stack

| File | Status | What it does |
|------|--------|--------------|
| `migration/odm_reader.py` | ✅ Complete | Parses CDISC ODM 1.3.x XML → normalized OdmStudy dict. 14 vendors detected via `Originator` attr + namespace. |
| `migration/odm_validator.py` | ✅ Complete | 3-layer validation: XML well-formedness → ODM structural conformance → OID referential integrity. `can_proceed` flag gates pipeline. |
| `migration/odm_to_spec.py` | ✅ Complete | Two-mode transformer: deterministic (`transform()`) and AI-assisted (`transform_with_ai()`). Produces identical Study Spec JSON schema as `protocol-analysis`. |
| `migration/gap_analysis.py` | ✅ Complete | Field-level comparison engine. Per-field confidence (High/Medium/Low/Unmappable) and risk (Clean/Warning/Data Loss Risk/Blocking). 7 classification rules. |
| `migration/oc4_reader.py` | 🟡 STUB | Import paths ready. Currently delegates to odm_reader. Pass 2 (post-build OC4→source comparison) not implemented. See TODO below. |
| `migration_pipeline.py` | ✅ Complete | Orchestrator. 10-step `run_migration()`: download → unzip → validate → parse → vendor dropdown → transform → upload spec JSON → gap analysis → hub upsert → trainer row. |
| `skills/user/migration-analysis/SKILL.md` | ✅ Complete | Injected into AI-assist prompt. Preserve-first design, OC-9 rules, form title sanitization, `_source_oid` stamping contract. |
| `migration/vendor_conventions/` | ✅ Complete | 11 vendor .md files: Medidata Rave, Oracle InForm, Viedoc, Castor, REDCap, Veeva, Zelta, iMedNet, Medrio, generic_odm, OC3/OC4. 12-section structure enforced by tests. |

### Migrations AI Hub board (18414959764)
- 5 groups: awaiting_build, in_flight, testing, production, complete
- 9 columns: study_oid, source_edc_system, source_odm_xml, target_oc4_xml,
  gap_report, syndeo_url, pipeline_status, last_pipeline_run, notes
- Column IDs in `migration_pipeline.MIGRATIONS_HUB_COLUMNS`
- Dedup key: `study_oid` — re-runs update in place, never create duplicates

### Syndeo UI (`mapping-ui/`)
- `MappingWorkbench.jsx` is live at `mapping-ui-production.up.railway.app`
- Reads `?item_id=` from URL, fetches gap report from pipeline backend
- Renders per-field mappings sorted by risk (Blocking → Data Loss Risk → Warning → Clean)
- Approve / Override controls with reviewer notes
- Risk filter chips + OID/label search
- **State is in-memory only** — reviewer decisions lost on tab close

### Test suite
- `tests/migration/test_migration.py` — 2082 lines, 16 test classes
- 10 vendor synthetic fixtures (Medidata, Veeva, Viedoc, iMedNet, Oracle, REDCap, Castor, Zelta + prtk05 real + synthetic)
- Zero external dependencies — safe to run anytime: `PYTHONPATH=migration python3 tests/migration/test_migration.py -v`
- Coverage: reader, transformer, validator, vendor registry, vendor conventions (12-section structure), enrichment dispatch, trainer wiring, AI assist prompt hierarchy

### Supported source EDC vendors (14)
Medidata Rave, Oracle InForm, Viedoc, Castor EDC, REDCap, OpenClinica 3,
OpenClinica 4, Zelta (Merative), Medrio, Veeva Vault CDMS, iMedNet,
+ generic_odm fallback

### Trigger on AI Study Hub board
- Trigger: `label__1 = "Migration"` on board 18409146946
- Monday columns in COL dict: `source_edc_export` (file), `source_edc_system` (dropdown)
- Setup script: `scripts/create_migration_columns.py`

---

## What Is NOT Done — Open Items (priority order)

### P1 — Verify `/api/gap-report/{item_id}` endpoint exists in main.py
Syndeo's `MappingWorkbench.jsx` calls this on load. If it's missing, the UI
shows an error on every open. Quick `grep -n "gap.report" main.py` to confirm.
**Effort:** 15 min to verify, 1-2 hours to add if missing.

### P2 — Syndeo save/persist endpoint
Reviewer decisions (Approve/Override + reviewer_note) live in React state only.
No POST endpoint exists to write them back to the gap report JSON on the
Migrations Hub file column. When reviewer closes tab, work is lost.
**What's needed:**
- POST `/api/gap-report/{item_id}` accepting the full updated mappings array
- Overwrites the gap report JSON file on the Migrations Hub file column
- Syndeo adds a "Save" button that calls it
**Effort:** ~3-4 hours (backend endpoint + Syndeo button + optimistic UI).

### P3 — Gap Appendix in Study Spec PDF/XLSX
SKILL.md documents the Gap Appendix should be embedded in:
- Study Spec PDF (last appendix section): summary counts + per-form gap table
- Study Spec XLSX: `GAP_ANALYSIS` sheet
Currently the gap report only exists as a JSON file in the Migrations Hub
file column — invisible to anyone who doesn't open Syndeo.
**What's needed:** `run_study_spec_files` reads `gap_analysis_report` key
from spec JSON and renders appendix. Migration pipeline passes the report
into spec_json before calling `run_study_spec_files`.
**Effort:** ~4-6 hours.

### P4 — `oc4_reader.py` Pass 2 (OC4 multi-select fix)
After migration + publish, the customer can export the resulting OC4 ODM
and compare it back against the source (Pass 2 gap analysis).
Current blocker: OC4's multi-select fields use
`<OpenClinica:MultiSelectListRef CodeListOID="..."/>` (vendor namespace),
which `odm_reader` misses — those items appear as unbound text, causing
false Unmappable/Warning flags.
**What's needed:** Walk the XML after `parse_odm_metadata()`, find all
`<OpenClinica:MultiSelectListRef>` elements, set `item.codelist_ref` and
`item.multi_select = True`, update odm_to_spec/gap_analysis to honour the flag.
The TODO comment in `oc4_reader.py` has the full spec.
**Effort:** ~2-3 hours.

### P5 — Migrations Hub pipeline_status label verification
`migration_pipeline.py` sets `pipeline_status` to `{"label": "Gap Analysis Complete"}`.
Unconfirmed whether that label exists on the actual board. If it doesn't,
the Monday API call silently fails or creates an orphaned status.
**What's needed:** Check the board's status column settings; add the label
if missing.
**Effort:** 15 min.

### P6 — XLS/CSV → ODM XML converter (new capability, discussed 2026-06-05)
See "New Capability Discussion" section below.

---

## New Capability Discussion — XLS/CSV → ODM XML Converter
**Discussed:** 2026-06-05. **Status:** Pre-planning. No coding started.

### The problem it solves
Many customers cannot or will not export ODM XML from their source EDC
(system is locked, IT won't help, vendor charges for exports, study is
managed in spreadsheets from the start). They CAN provide data dictionaries
as Excel/CSV — the format most CRAs and data managers already work in.

### The idea
Accept one or more XLS/CSV files representing the study's data dictionary
(forms, fields, codelists, visits), convert them to a valid CDISC ODM 1.3.x
XML, then feed that XML into the existing migration pipeline unchanged.
The rest of the stack (odm_validator → odm_reader → odm_to_spec → gap_analysis
→ Migrations Hub → Syndeo) runs as-is.

### Key design questions to resolve before coding

1. **What is the expected XLS format?**
   - Is there a standard the customers already use, or do we define one?
   - Likely options: (a) one sheet per form, rows = fields; (b) flat data
     dictionary (all forms in one sheet with a Form column); (c) a
     schedule-of-events matrix (rows = visits, cols = forms) plus a
     separate field sheet. Real customers probably have all three variants.
   - Do we publish a template they fill in, or do we infer structure from
     whatever they send?

2. **How much structure inference do we need?**
   - Column headers will vary wildly ("Field Name" vs "Variable" vs "OID",
     "Data Type" vs "Type" vs "Format", "Code List" vs "Response Options").
   - Minimum viable: AI maps their column headers to our canonical schema
     (this is a natural Claude task — one short prompt per sheet).
   - Richer: detect visit/event structure from sheet names or a separate
     SoE tab.

3. **What ODM fidelity do we target?**
   - ODM requires: Study, MetaDataVersion, StudyEventDef, FormDef,
     ItemGroupDef, ItemDef, CodeList. All must have valid OIDs.
   - A spreadsheet may have none of this structure. We need to invent
     OIDs, infer event-to-form assignments, and synthesize ItemGroupDefs
     that don't exist in the source.
   - This is a lossy, inference-heavy process — the resulting ODM will
     be lower fidelity than a real EDC export. Gap analysis will flag
     more items as Warning/Low because the source metadata is thin.
   - That's OK — the point is to get SOMETHING into the pipeline rather
     than nothing.

4. **Where does AI fit vs. deterministic code?**
   - Deterministic: header normalization table, OID generation, ODM XML
     serialization (lxml or stdlib xml).
   - AI: column header → canonical field mapping, codelist value inference
     from free-text response options, visit structure detection,
     DataType inference from field name + example values.
   - Proposed split: Claude reads the sheet(s), returns a structured JSON
     (same OdmStudy intermediate dict shape), then deterministic code
     serializes it to ODM XML. This keeps the AI out of XML generation
     (error-prone) and reuses the existing odm_to_spec pipeline.

5. **Where does this live in the pipeline?**
   - Option A: New Monday column "CRF Data Dictionary" (XLS file upload)
     alongside the existing "Source EDC Export" column. Pipeline detects
     XLS → runs converter → produces ODM XML → hands off to existing
     migration path. Clean separation, zero change to existing columns.
   - Option B: Accept XLS IN the existing "Source EDC Export" column
     (the current column already accepts ZIP). Detect file type and route.
     Less board clutter but more magic.
   - Option A preferred — explicit column makes the path visible to the
     PS team and makes it easier to show both inputs when a customer
     provides both.

6. **Syndeo implications**
   - The gap report for XLS-derived migrations will have more Low/Unmappable
     rows (thin source metadata = more gaps). Syndeo may need a banner
     or source-type indicator: "Source: CRF Data Dictionary (low-fidelity
     ODM — expect more gaps than a native EDC export)."

7. **Template vs. freeform**
   - Publishing a template (downloadable from the AI Hub board or a docs
     page) dramatically reduces the AI inference burden and improves
     output quality. A template with defined column headers (Form, Field,
     Label, DataType, Codelist, Required, Visit) is a ~1 hour side task
     that pays forward in every subsequent run.
   - Should probably ship alongside the converter, not after.

### Suggested pre-coding decisions
Before writing a line of code, align on:
- [ ] Template vs. freeform (recommendation: template, with freeform fallback via AI)
- [ ] Flat vs. multi-sheet format (recommendation: flat with Form column, plus optional SoE sheet)
- [ ] Option A vs. B for Monday column placement (recommendation: Option A)
- [ ] Whether gap analysis should be annotated differently for XLS-derived migrations
- [ ] What the converter's output quality guarantee is (ODM fidelity level)

---

## PRD and User Guide Status

### PRD
**Status:** NOT YET WRITTEN. Planned soon.
**Location when created:** `docs/prds/migration.md`
**What to cover:** full capability scope, vendor support matrix, Path M
trigger conditions, gap analysis schema, Syndeo reviewer workflow,
Migrations Hub board lifecycle, XLS converter (once designed).

### User Guide
**Status:** NOT YET WRITTEN. Planned soon.
**Location when created:** `docs/user-guides/migration.md`
**Audience:** PS team operators running migrations day-to-day.
**What to cover:** how to trigger a migration run, what to do with the
gap report in Syndeo, Migrations Hub board lifecycle, when to use
AI-assist vs. deterministic mode, XLS input instructions (once built).

---

## Key Architecture Reminders

- Migration spec JSON is **schema-identical** to protocol-analysis output.
  Downstream (edc-builder, OC study create, calendaring, DVS) is unmodified.
- `migration_meta` block at top level of spec JSON carries migration-specific
  metadata. Downstream code ignores it.
- `_source_oid` field on every survey row is the gap analysis link.
  ODM-derived rows: `_source_oid = item.oid`. Injections: `_source_oid = ""`.
- Preserve-first design: customer naming is preserved UNLESS OC4 technical
  constraints force a change (OID syntax, OC-9 SE_COMMON pin, title
  sanitization of `+`, `&`, `%`, `#`, `@`).
- Gap analysis is non-blocking: failures in `run_gap_analysis_and_hub_upsert`
  never fail the build. The spec JSON upload is the load-bearing step.
- Syndeo URL pattern: `https://mapping-ui-production.up.railway.app?item_id={hub_row_id}`
  (hub row ID, not AI Study Hub item ID).
