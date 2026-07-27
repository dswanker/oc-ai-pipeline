# XLS → ODM Converter — Pre-Planning Notes
**Status:** Pre-coding. Waiting on real customer examples.
**Expected start:** Week of 2026-06-09 (Dan has examples Monday)
**Decision log:** All key design questions answered 2026-06-05.

---

## What This Capability Does

Accepts one or more XLS/CSV files from a customer (CRF data dictionary,
Schedule of Events, and/or a protocol PDF) and converts them into a valid
CDISC ODM 1.3.x XML that feeds the existing migration pipeline unchanged.

Entry point: new Monday column "CRF Data Dictionary" (file upload).
Exit point: `migration/odm_reader.parse_odm_metadata()` — same as today.
Everything downstream (validator → reader → odm_to_spec → gap_analysis →
Migrations Hub → Syndeo) is untouched.

---

## Design Decisions Locked (2026-06-05)

| Question | Decision |
|----------|----------|
| Template or freeform? | **Freeform.** Customer brings whatever they have. AI figures out the mapping. |
| Input format | **XLS/CSV of any structure.** Claude infers column roles (field name, data type, codelist, required, visit, etc.) from headers + content. |
| Visit/event source | **Protocol PDF and/or a Schedule of Events XLS sheet.** Either or both. If neither is present, all forms → SE_UNSCHEDULED and reviewer fixes in Syndeo. |
| Gap report fidelity | **Accepted as-is.** XLS-derived migrations will have more Low/Warning/Blocking rows than native EDC exports. PS team sets expectations. May revisit after seeing first real outputs. |
| Monday column placement | **New column: "CRF Data Dictionary" (file upload).** Pipeline routing: `source_edc_export` populated → migration path A (ODM XML). `crf_data_dictionary` populated → XLS conversion path → migration path A. Both can be present (XLS supplements ODM). |
| Where conversion lives | **New module: `migration/xls_to_odm.py`** Produces OdmStudy dict (same shape as odm_reader output), then deterministic serializer writes ODM XML. AI stays out of XML generation. |

---

## What We Need Monday (2026-06-09)

Dan is pulling 1-2 real customer data dictionary spreadsheets (anonymized).
Before any design or coding work, we need to see:

1. **What column headers do customers actually use?**
   (e.g. "Field Name" vs "Variable" vs "OID", "Data Type" vs "Type", etc.)

2. **How is the codelist represented?**
   (Inline in a "Response Options" column? Separate sheet? Coded value + decode pairs?)

3. **How is the Schedule of Events structured?**
   (Rows = visits, columns = forms with X marks? Or a separate "Visit" column on each row?)

4. **How many sheets are typical?**
   (One flat sheet? One per form? Plus a SoE sheet?)

5. **Is there a protocol PDF alongside it, or XLS only?**

These answers determine the AI prompt design, the column-mapping inference
strategy, and whether we need a multi-sheet parser.

---

## Proposed Architecture (draft — subject to revision after examples)

```
Customer uploads XLS (+ optionally protocol PDF) to Monday
        ↓
pipeline.py detects crf_data_dictionary column populated
        ↓
migration/xls_to_odm.py
  Step 1: Read all sheets from XLS (openpyxl)
  Step 2: Claude call — infer column roles from headers + sample rows
          Returns structured JSON: {field_name_col, type_col, label_col,
          codelist_col, required_col, form_col, visit_col, ...}
  Step 3: Deterministic builder — walk rows, build OdmStudy dict
          (same shape as odm_reader.parse_odm_metadata output)
  Step 4: If protocol PDF present → enrich study_meta via Claude
          (same protocol enrichment path as transform_with_ai)
  Step 5: Serialize OdmStudy → ODM XML (stdlib xml.etree)
        ↓
Hand ODM XML bytes to existing migration_pipeline.run_migration()
        ↓
Rest of migration pipeline runs unchanged
```

### Key design principle
**AI infers structure; deterministic code builds the artifact.**
Claude's role is column-header → canonical-field mapping (a short, bounded
task it's very good at). The OdmStudy dict construction and ODM XML
serialization are pure Python — no hallucination risk on the output format.

### Syndeo implication
Add a banner/badge when the gap report was derived from an XLS source:
"Source: CRF Data Dictionary (low-fidelity — expect more gaps than a
native EDC export)". Flag lives in `migration_meta.source_type = "xls_converted"`.

---

## Files to Create (when ready to code)

| File | Purpose |
|------|---------|
| `migration/xls_to_odm.py` | Main converter module |
| `migration/xls_reader.py` | Sheet parsing + Claude column-inference |
| `migration/odm_serializer.py` | OdmStudy dict → ODM XML string (deterministic) |
| `tests/migration/fixtures/sample_crf_dict.xlsx` | Anonymized real customer example |
| `tests/migration/test_xls_to_odm.py` | Test harness (no API calls) |
| `scripts/create_crf_dict_column.py` | One-shot Monday column setup |

`odm_serializer.py` may be worth building first (no AI, pure deterministic)
since it's the output end and can be tested against known-good OdmStudy
dicts produced by the existing reader.

---

## Open Questions (resolve with examples Monday)

- [ ] Do any customers provide one sheet per CRF form, or is it always flat?
- [ ] How do customers represent multi-select vs. single-select fields?
- [ ] Is "required" ever implicit (no column, assumed from context)?
- [ ] Do they ever include validation rules / range checks in the XLS?
- [ ] What's the typical item count? (10 forms × 20 fields = 200 rows, or larger?)
- [ ] Do we need to handle merged cells? (common in Excel data dictionaries)

---

## Related Work Not Yet Started

- PRD for migration capability: `docs/prds/migration.md` (write soon)
- User guide for migration: `docs/user-guides/migration.md` (write soon)
  Both are blocked on XLS converter design being settled (so the PRD covers
  the full capability including XLS input).
