---
name: edc-builder
description: >
  Reads the EDC structure specification (XLSX or PDF output from the
  protocol-to-edc-structure skill) and generates production-ready
  OpenClinica XLSForm .xlsx files — one per CRF form — plus supporting
  CSV files, a study build checklist PDF and XLSX, and a zip package
  of all deliverables. Use this skill whenever a user uploads an EDC
  structure XLSX or PDF and asks to build the study, generate the forms,
  create the XLSForms, or produce the OpenClinica build files. Always
  run protocol-to-edc-structure BEFORE this skill. This skill consumes
  what that skill produces.
---

# EDC Builder Skill

## Purpose

Convert an approved EDC structure specification into production-ready
OpenClinica XLSForm `.xlsx` files ready to upload to OpenClinica.

**Preferred input: XLSX** — machine-readable, structured, contains
ACTION column edits from human reviewer. Use PDF only as fallback.

---

## Before You Begin — Read Reference File

**Always read `references/xlsform-build-rules.md` before generating
any form.** It contains:
- Exact column names and order for all 3 sheets
- Valid field types
- Cross-form XPath patterns
- Hard check syntax
- Once() pattern for repeating groups
- File naming conventions
- Critical build rules

Do not rely on memory for column order — always verify against the
reference file.

---

## Step 1: Read the Specification

### Input A — EDC Structure XLSX (preferred)

Open the XLSX using openpyxl. Read in this order:

1. **INDEX sheet** — extract study_id, protocol_number, form list
2. **TIMEPOINTS sheet** — rows 3+ (skip headers), columns A and B
3. **LAB_RANGES sheet** — rows 3+ (skip headers), all columns
4. **REVIEW_FLAGS sheet** — note any unresolved items
5. **Per form** — for each form listed in INDEX:
   - `[FORMID]_survey` tab: rows 4+ (rows 1-3 are banner/legend/headers)
     - Column A = ACTION (blank / DELETE / ADD)
     - Column B = NOTES_FOR_AI — reviewer's optional explanation of changes.
       **Read this column first for every sheet.** Use the notes as context
       when deciding how to process ACTION=DELETE/ADD rows and modified cells.
       A note on a row with blank ACTION means the reviewer modified that row
       in place — apply their edit and use the note to understand intent.
     - Columns C onwards = XLSForm fields
     - Skip rows where ACTION = DELETE
     - Include rows where ACTION = ADD or blank
   - `[FORMID]_choices` tab: rows 3+ (row 1 = title banner, row 2 = headers)
     - Skip rows where ACTION = DELETE
   - `[FORMID]_settings` tab: rows 3-8, column 2 (yellow editable cells)

**Column name mapping** — XLSX uses underscores, XLSForm uses colons:
- `bind__oc_itemgroup` → `bind::oc:itemgroup`
- `bind__oc_external` → `bind::oc:external`
- `bind__oc_briefdescription` → `bind::oc:briefdescription`
- `bind__oc_description` → `bind::oc:description`

**Ignore these XLSX-only columns** (do not include in output XLSForms):
`ACTION`, `REVIEW_NOTES`, `completion_status`, `library_source`,
`pdf_original_label`, `cdash_standard_name`, `cdash_name_deviation`,
`cdash_name_confidence`, `flag_reason`

### Input B — EDC Structure PDF (fallback)

If only the PDF is available:
1. Read Section 3 (CRF Master Table) for the form list
2. Read Section 4 (Form Definitions) for each form's survey and choices
3. Read Section 2 (Timepoint CSV) for timepoint data
4. Read Section 6 (Lab Ranges) for lab ranges
5. Note: all forms will be MEDIUM confidence — flag in build report

---

## Step 2: Process Each Form

For each form in the specification, build three data structures:
settings dict, choices list, and survey rows list.

### 2a: Process Settings

Map from XLSX settings tab or JSON:
```python
settings = {
    'form_title': '...',
    'form_id': '...',
    'version': '1',
    'style': 'theme-grid',
    'crossform_references': '',  # or event OIDs
    'namespaces': 'oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"'
}
```

### 2b: Process Choices

For each choice row (excluding ACTION=DELETE):
```python
choice = {
    'list_name': '...',
    'label': '...',
    'name': '...',
    'image': '',
    # any filter columns if present
}
```

### 2c: Process Survey Rows

For each survey row (excluding ACTION=DELETE):
- Map all 20 standard columns
- Apply best-guess logic for PLACEHOLDER fields (Step 3)
- Remap XLSX column names to XLSForm column names
- Strip XLSX-only metadata columns

**Standard survey columns to output** (always in this exact order):
`type`, `name`, `label`, `bind::oc:itemgroup`, `hint`, `appearance`,
`bind::oc:briefdescription`, `bind::oc:description`, `relevant`,
`required`, `required_message`, `constraint`, `constraint_message`,
`default`, `calculation`, `trigger`, `readonly`, `image`,
`repeat_count`, `bind::oc:external`

**Additional columns** — include only if any row in this form has a
value for them (add after the 20 standard columns):
`choice_filter`, `bind::oc:constraint-type`, `bind::oc:required-type`,
`bind::oc:oc_annotation_*`, `instance::oc:contactdata`,
`instance::oc:identifier`

---

## Step 3: Handle PLACEHOLDER Fields

When a field has completion_status=PLACEHOLDER or still contains
`[PLACEHOLDER...]` text, apply best-guess logic and record in build log:

| Field | Best-guess | Build log note |
|-------|-----------|----------------|
| LBNAM choices | Generate `LAB_1`, `LAB_2`, etc. | "Lab names require site-specific values" |
| labranges lower/upper | Use `0` / `999` | "Normal ranges require site-specific values" |
| labranges unit | Use `[UNIT]` | "Units require site-specific values" |
| labranges lab_name | Use `[LAB_NAME]` | "Lab name requires site-specific value" |
| XPath OID paths | Leave `[EVENT_OID]`/`[FORM_OID]` placeholders | "OID requires study configuration" |
| type (missing) | Use `text` | "Field type defaulted to text — verify" |
| constraint (missing) | Leave blank | "Constraint not specified in spec" |
| calculation (missing) | Leave blank | "Calculation not specified in spec" |

Record every best-guess in the build log with form_id, field name,
original value, and best-guess value applied.

---

## Step 4: Generate Each XLSForm File

Use the script at `scripts/build_xlsforms.py`.

For each form, call `build_single_xlsform(settings, choices, survey, output_path)`.

The script:
1. Creates a workbook with 3 sheets in order: settings, choices, survey
2. Writes headers in row 1 of each sheet
3. Writes data starting row 2
4. Applies professional formatting (Arial font, column widths, header styling)
5. Saves as `{form_id}.xlsx` (or variant name for forms sharing form_id)

**File naming:**
- Standard: `{form_id}.xlsx` → e.g., `AE.xlsx`, `VS.xlsx`
- Variants (same form_id, different designs):
  `{FORMID}_{VARIANT}.xlsx` → e.g., `IE_TRT.xlsx`, `IE_CTL.xlsx`
- The filename uses the XLSX tab form_id prefix, not the settings form_id

---

## Step 5: Generate Supporting CSV Files

### 5a: Timepoint CSV

```python
# Write {study_id}_tpt.csv
# Columns: event, timepoint
# One row per event from TIMEPOINTS sheet
```

### 5b: Lab Ranges CSV

```python
# Write labranges.csv
# Columns from LAB_RANGES sheet headers
# Apply best-guess for any remaining PLACEHOLDERs
```

---

## Step 6: Generate Study Build Checklist

Use `scripts/build_checklist.py` to generate both PDF and XLSX.

### Checklist content

**Sheet/Section 1 — SIGN-OFF SUMMARY**
- Study metadata (protocol, study_id, date built)
- Form count summary (total, CDASH, infrastructure, ePRO)
- Overall build status (ready / needs attention)
- Placeholder items remaining (count with form names)
- Sign-off table: Builder name, Date, Signature, QC reviewer, Date

**Sheet/Section 2 — DETAILED QA CHECKLIST**
One row per form per check. Checks to perform:

| Check | Pass criteria |
|-------|---------------|
| Settings complete | form_title, form_id, version, style, namespaces all populated |
| Survey has rows | At least 1 non-calculate survey row |
| No orphaned groups | Every begin group has matching end group |
| No orphaned repeats | Every begin repeat has matching end repeat |
| Choice lists complete | Every select_one/select_multiple references a defined list |
| Required fields present | type, name, label populated for all non-calculate rows |
| No PLACEHOLDER values remaining | No `[PLACEHOLDER` text in any cell |
| Cross-form refs flagged | Any OID placeholder noted for post-config completion |
| Timepoint CSV present | prtk05_tpt.csv included in package |
| Lab ranges CSV present | labranges.csv included in package |
| ePRO forms identified | is_epro=true forms flagged for ePRO module config |

**Status values:** ✓ PASS / ⚠ NEEDS ATTENTION / ✗ FAIL / — N/A

---

## Step 7: Package All Outputs

Use `scripts/build_package.py` to create the zip file.

**Zip contents:**
```
{PROTOCOL_NUMBER}_EDC_Build_{DATE}/
├── forms/
│   ├── AE.xlsx
│   ├── BE.xlsx
│   ├── BE_CTL.xlsx
│   ├── BES.xlsx
│   ├── CM.xlsx
│   ├── DC.xlsx
│   ├── DOV.xlsx
│   ├── DS.xlsx
│   ├── DV.xlsx
│   ├── EC.xlsx
│   ├── EC_DIARY.xlsx
│   ├── EX.xlsx
│   ├── IE_CTL.xlsx
│   ├── IE_TRT.xlsx
│   ├── LB.xlsx
│   ├── MH.xlsx
│   ├── PE_FULL.xlsx
│   ├── PE_FOLLOWUP.xlsx
│   ├── PR_CONCOM.xlsx
│   ├── PR_EBRT.xlsx
│   ├── PREGPART.xlsx
│   ├── PSA.xlsx
│   ├── SPELIG.xlsx
│   ├── VS.xlsx
│   └── VS_FOLLOWUP.xlsx
├── csv/
│   ├── {study_id}_tpt.csv
│   └── labranges.csv
├── checklist/
│   ├── {PROTOCOL_NUMBER}_Build_Checklist.pdf
│   └── {PROTOCOL_NUMBER}_Build_Checklist.xlsx
└── BUILD_README.txt
```

**BUILD_README.txt** must include:
- Protocol number and study ID
- Build date
- Total forms built
- List of any PLACEHOLDER values remaining (requires post-config)
- List of any OID paths requiring confirmation
- Upload instructions for OpenClinica

---

## Step 8: Present Outputs

After generating all files:
1. Use `present_files` to share the zip package
2. Report build summary in chat:
   - How many forms built successfully
   - How many had PLACEHOLDER best-guesses applied
   - How many OID paths need post-configuration
   - Any QA checks that failed

---

## Build Log Format

Maintain a build log throughout Steps 2-6. Record:
```python
build_log = {
    'protocol': '',
    'study_id': '',
    'build_date': '',
    'forms_built': [],           # list of form_ids built
    'forms_skipped': [],         # form_ids skipped with reason
    'placeholder_applied': [],   # {form_id, field, original, applied}
    'oid_placeholders': [],      # {form_id, field, note}
    'qa_results': [],            # {form_id, check, status, note}
    'build_warnings': [],        # non-blocking issues
    'build_errors': [],          # blocking issues
}
```

Pass this to the checklist generator so it reflects actual build results.

---

## Execution Script

When the user provides a specification XLSX or PDF, execute this workflow:

```python
import sys, os
sys.path.insert(0, '/path/to/edc-builder/scripts')
from build_xlsforms import read_spec_xlsx, build_all_xlsforms, write_timepoint_csv, write_labranges_csv
from build_checklist import build_checklist_pdf, build_checklist_xlsx
from build_package import build_package

# Paths
spec_path    = "/mnt/user-data/uploads/{spec_file}"
output_base  = "/home/claude/edc_build"
forms_dir    = f"{output_base}/forms"
csv_dir      = f"{output_base}/csv"
checklist_dir= f"{output_base}/checklist"
protocol     = "PrTK05"  # from spec

build_log = {
    'forms_built': [], 'forms_skipped': [],
    'placeholder_applied': [], 'oid_placeholders': [],
    'qa_results': [], 'build_warnings': [], 'build_errors': []
}

# Step 1: Read spec
spec_data = read_spec_xlsx(spec_path)
study_id  = spec_data['study_meta'].get('study_id', 'study')

# Step 2-4: Build XLSForms + CSVs
build_all_xlsforms(spec_data, forms_dir, build_log)
write_timepoint_csv(spec_data['timepoint_csv'],
    os.path.join(csv_dir, f"{study_id}_tpt.csv"), build_log)
write_labranges_csv(spec_data['labranges_csv'],
    os.path.join(csv_dir, 'labranges.csv'), build_log)

# Step 5-6: Generate checklists
build_checklist_pdf(spec_data, build_log,
    os.path.join(checklist_dir, f"{protocol}_Build_Checklist.pdf"))
build_checklist_xlsx(spec_data, build_log,
    os.path.join(checklist_dir, f"{protocol}_Build_Checklist.xlsx"))

# Step 7: Package
zip_path = build_package(spec_data, build_log, forms_dir, csv_dir,
                         checklist_dir, "/mnt/user-data/outputs")

# Step 8: Present
# Use present_files tool with zip_path
```

After running, report to the user:
- How many forms built successfully
- Any placeholder best-guesses applied
- Any OID paths needing post-configuration
- Any QA check failures
