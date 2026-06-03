# Study Spec XLS Format Reference

Describes the exact structure of the Study Specification XLSX produced by
`skills/protocol-analysis/scripts/generate_study_spec_xlsx.py` and stored
in column `file_mm2n3x71` on the AI Hub Monday.com board (18409146946).

The `design-change-intake` skill reads and modifies this file ONLY.
It never touches edc-builder outputs, XLSForms, DVS files, or any other
pipeline artifact. The correct flow is always:
  source text → spec XLSX updated → edc-builder re-runs → new form files

---

## Workbook Tab Order

```
INDEX              ← study metadata + form inventory table
AI_INSTRUCTIONS    ← human-to-Claude guidance (do not modify programmatically)
TIMEPOINTS         ← visit/event schedule
LAB_RANGES         ← lab normal ranges placeholder
REVIEW_FLAGS       ← outstanding items requiring human resolution
{FORMID}_survey    ← one per form: editable survey rows
{FORMID}_choices   ← one per form: editable choice lists
{FORMID}_settings  ← one per form: settings + metadata + cross-form deps
CONVENTION_CONFLICTS ← present only when engine mutations were recorded
CONVENTIONS        ← last tab: conventions applied to this build
CHANGE_LOG         ← added/appended by design-change-intake skill only
```

To enumerate all forms: read all sheet names, collect every prefix that
appears before `_survey`. Example: `AE_survey` → form_id = `AE`.

---

## {FORMID}_survey Sheet

Row 1: Title banner — merged across all columns. Never modify.
Row 2: Colour legend. Never modify.
Row 3: Column headers.
Rows 4+: Field data rows.

### Column headers — exact order from SURVEY_EDITABLE_COLS

| # | Header | Description |
|---|--------|-------------|
| 1 | ACTION | blank=keep / ADD=new row / DELETE=remove |
| 2 | NOTES_FOR_AI | Pre-populated with flag_reason for FLAGGED rows |
| 3 | type | XLSForm field type |
| 4 | name | Machine-readable field ID |
| 5 | label | User-visible question text |
| 6 | bind::oc:itemgroup | CDASH domain group OID |
| 7 | appearance | Layout hint |
| 8 | relevant | XPath show/hide expression |
| 9 | required | yes / true() / XPath |
| 10 | constraint | XPath validation rule |
| 11 | constraint_message | Error message for constraint |
| 12 | calculation | Auto-calculated value XPath |
| 13 | readonly | yes or blank |
| 14 | hint | Helper text |
| 15 | repeat_count | Integer or expression |
| 16 | bind::oc:external | clinicaldata / labranges / {study_id}_tpt |
| 17 | choice_filter | Filter expression for choice lists |
| 18 | DEPENDENCIES | Auto-derived cross-form refs (read-only, grey) |

Column headers use colon format (bind::oc:itemgroup). Always locate
columns by reading the header row — do not assume fixed positions.

---

## {FORMID}_choices Sheet

Row 1: Title banner. Never modify.
Row 2: Column headers.
Rows 3+: Choice definitions.

Headers: ACTION, NOTES_FOR_AI, list_name, label, name, source,
filter_column, filter_value

---

## {FORMID}_settings Sheet

Row 1: Title banner. Never modify.
Row 2: Column headers. Never modify.
Rows 3+: key (col A) / value (col B, yellow=editable) / hint (cols C-F)

Editable keys: form_title, form_id, version, style, namespaces,
crossform_references

Below editable settings: CROSS-FORM DEPENDENCIES (read-only),
then READ-ONLY METADATA section.

---

## Form Name Matching Strategy

When matching change.form from parsed text to a workbook tab:

1. Exact case-insensitive match against tab prefix before _survey
2. Read {FORMID}_settings row 3 col B (form_title) and compare
3. Match against CDASH domain abbreviation table below
4. Partial substring match against any tab name
5. No match: write to CHANGE_LOG as unresolved, note in INDEX

Common abbreviations: AE=Adverse Events, CM=Concomitant Meds,
DM=Demographics, DS=Disposition, DOV=Date of Visit, DV=Deviations,
EC=Exposure Dosing, EX=Exposure Drug, IE=Inclusion/Exclusion,
LB=Laboratory, MH=Medical History, PE=Physical Exam, PR=Procedures,
VS=Vital Signs, SPELIG=Sponsor Eligibility, SLEEP=Sleep Diary

---

## Safe Modification Rules

1. Never modify rows 1-2 on any tab
2. Never modify row 3 on survey tabs (headers)
3. Never delete rows — use ACTION=DELETE
4. Append new ADD rows at end of data range only
5. Locate columns by header name, not position
6. Set NOTES_FOR_AI="Added by design-change-intake" on new ADD rows
7. Write every change to CHANGE_LOG

---

## CHANGE_LOG Sheet

Created if absent; appended to on every run.
Columns: timestamp, run_id, change_type, form_id, field_name,
description, action_taken, resolved, log_note, source_type, source_filename
