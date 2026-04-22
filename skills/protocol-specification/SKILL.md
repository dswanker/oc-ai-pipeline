---
name: protocol-to-edc-structure
description: >
  Reads a clinical trial protocol and produces a structured EDC extraction
  in JSON + human-readable summary format, ready to feed the edc-builder skill
  which generates OpenClinica-conformant XLSForm .xlsx files. Use this skill
  whenever a user uploads a protocol and asks for an EDC build, study build,
  XLSForm extraction, CRF structure, or OpenClinica configuration. Also trigger
  when the user mentions form OIDs, XLSForm, survey sheets, choices sheets,
  CDASH domains for building, or EDC structure. Always use this skill BEFORE
  the edc-builder skill — this skill produces what the edc-builder consumes.
---

# Protocol-to-EDC-Structure Skill

## Purpose

Extract the full structural definition of every CRF needed to build a study
in OpenClinica using the XLSForm standard. Output feeds the `edc-builder`
skill which generates the actual `.xlsx` files.

The output has two parts:
- **Output A**: Human-readable EDC Structure Summary (for review and sign-off)
- **Output B**: Structured JSON (machine-readable, consumed by edc-builder)

---

## Before You Begin — Read Reference Files

**Always read both reference files before processing any protocol:**

1. `references/xlsform-patterns.md` — Standard XLSForm column definitions,
   OpenClinica-specific columns, field type reference, and standard constraint
   patterns used across all forms. Read this before writing any survey rows.

2. `references/cdash-domain-library.md` — Standard field definitions for every
   CDASH domain. Use these as the baseline for all clinical form fields.
   Protocol-specific variations override these defaults.

---

## Input Detection — Three Modes

**Before doing anything else, determine what inputs were provided:**

### Mode 1 — Protocol Only
No customer library provided. Proceed using CDASH defaults from
`references/cdash-domain-library.md` for all forms. Tag every survey row
with `"library_source": "CDASH_DEFAULT"`.

### Mode 2 — Protocol + Customer XLSForm Library (.xlsx files)
One or more .xlsx files provided alongside the protocol. These are
OpenClinica-format XLSForm files with survey/choices/settings sheets.
Follow **CL-XLSX Processing** steps below before Step 1.

### Mode 3 — Protocol + Customer PDF Library
One or more PDF files provided as customer CRF reference forms.
These are paper/visual form layouts, not machine-readable XLSForms.
Follow **CL-PDF Processing** steps below before Step 1.

### Mode 4 — Protocol + Mixed Library (PDF + .xlsx)
Both PDF and .xlsx files provided. Process each file type using its
respective method. Produce the same JSON output structure for all forms
regardless of source. Mark confidence differently per source type.

---

## CL-XLSX Processing (Modes 2 and 4)

When .xlsx customer library files are provided:

### XLSX-1: Read Each Form File
For each .xlsx file, extract all three sheets:
- `settings` → capture form_title, form_id, version, namespaces
- `choices` → capture all list_name / label / name rows + any filter columns
- `survey` → capture every row with all populated columns

### XLSX-2: Build Customer Form Index
Create an internal index entry for each form:
```
{
  "customer_form_id": "[from settings.form_id]",
  "customer_form_title": "[from settings.form_title]",
  "source_file": "[filename]",
  "cdash_domain_match": "[inferred CDASH domain]",
  "total_survey_rows": n,
  "choice_lists": [...],
  "has_repeating_group": true/false,
  "field_names": [...],  // all name column values from survey
  "source_type": "XLSX"
}
```

### XLSX-3: Match to Protocol Domains
For each domain identified in the protocol SoA, search the customer index:

**EXACT MATCH** — form_id matches CDASH domain code AND field coverage
aligns with protocol requirements:
- Use customer form as the base
- Carry over all survey rows, choices, constraints, calculations exactly
- Add any protocol-required fields not in the customer form (mark as EXTENDED)
- Tag: `"library_source": "CUSTOMER_XLSX_EXACT"`
- Confidence: HIGH

**PARTIAL MATCH** — form_id or title matches but field coverage is incomplete:
- Use customer form as base
- Extend with missing protocol-required fields
- Use customer field names where present
- Flag deviations from CDASH naming: `"cdash_name_deviation": true`
- Tag: `"library_source": "CUSTOMER_XLSX_PARTIAL"`
- Confidence: MEDIUM

**NO MATCH** — no customer form found for this domain:
- Fall back to CDASH defaults
- Tag: `"library_source": "CDASH_DEFAULT_NO_LIBRARY_MATCH"`
- Confidence: MEDIUM

### XLSX-4: Handle Field Name Deviations
When a customer field name differs from CDASH standard:
- Use the customer's field name in the output
- Add `"cdash_standard_name": "[CDASH name]"` to the survey row
- Add `"flag_reason": "Field name deviates from CDASH standard: customer uses [X], CDASH standard is [Y]"`
- Mark `"completion_status": "FLAGGED"`

### XLSX-5: Handle Protocol Extensions
When the protocol requires fields beyond what the customer form contains:
- Append new rows to the end of the relevant group in the survey
- Mark extended rows: `"library_source": "EXTENDED_FROM_PROTOCOL"`
- Mark `"completion_status": "FLAGGED"` with reason: "New field required by protocol — not in customer library"
- Do NOT create a separate form version — extend the existing form

---

## CL-PDF Processing (Modes 3 and 4)

When PDF customer library files are provided:

### PDF-1: Identify Each Form
For each PDF file:
- Read the document title, header, and any form ID / version markings
- Identify the CDASH domain this form represents
- Note the form's section structure and grouping

### PDF-2: Extract Field Information
For each visible field on the PDF form, extract:
- **Field label** — the question text as written
- **Field type** — infer from visual layout:
  - Checkbox(es) → `select_one` (single) or `select_multiple` (multi)
  - Text box (single line) → `text` or `integer` or `decimal`
  - Text box (multi-line) → `text` with `appearance: multiline`
  - Date field → `date` or partial date pattern
  - Dropdown → `select_one [list]` with `appearance: minimal`
  - Radio buttons → `select_one [list]`
  - Calculated/pre-filled → `calculate` or `text` with `readonly: yes`
- **Choice options** — if listed on the form, capture all option labels
- **Required indicator** — asterisk, bold, or "required" marking
- **Conditional indicator** — "if yes, complete section X", arrows, indentation
- **Section/group membership** — which section or box the field belongs to
- **Instructions/hints** — any helper text near the field

### PDF-3: Map Labels to CDASH Field Names
For each extracted field, attempt to map the label to a CDASH standard name:

**High-confidence mapping** (map automatically, no flag needed):
- "Age" → `AGE`
- "Sex" / "Gender" → `SEX`
- "Adverse Event Term" / "Event Term" / "AE Term" → `AETERM`
- "Start Date" in AE context → `AESTDAT`
- "Severity" with grade options → `AESEV`
- "Outcome" in AE context → `AEOUT`
- "Medication Name" / "Drug Name" → `CMTRT`
- "Collection Date" in lab context → `LBDAT`
- "Result" in lab context → `LBORRES`
- "Date of Visit" → `VISDT`
- (Apply knowledge of all CDASH field labels from domain library)

**Uncertain mapping** (map with flag):
- When label is ambiguous or non-standard
- Add `"pdf_original_label": "[exact label from PDF]"`
- Add `"cdash_name_confidence": "UNCERTAIN"`
- Mark `"completion_status": "FLAGGED"`
- Flag reason: "PDF label '[X]' mapped to CDASH '[Y]' — verify mapping is correct"

**No mapping possible** (leave as custom):
- When label has no CDASH equivalent
- Set name to a sanitized version of the label (no spaces, start with letter)
- Mark `"completion_status": "PLACEHOLDER"`
- Flag reason: "No CDASH mapping found for PDF label '[X]' — assign field name manually"

### PDF-4: Reconstruct XLSForm Structure
Using the extracted fields, build the full form definition:
- Populate all survey rows with best-estimate values
- For constraint/relevant/calculation columns:
  - Apply standard patterns from `references/xlsform-patterns.md` where applicable
  - Mark as `FLAGGED` with note "Inferred from PDF layout — verify logic"
  - Apply known CDASH domain logic (e.g., AE severity triggers SAE)
- For choice lists:
  - Use customer's options if listed on PDF
  - Fall back to standard CDASH choice lists where options not visible
  - Flag: "Choice list inferred from PDF — verify options are complete"

### PDF-5: Set Confidence Levels
All PDF-derived forms use these confidence rules:
- Survey rows from clearly visible, standard fields → MEDIUM confidence
- Survey rows requiring inferred logic → FLAGGED
- Survey rows with no clear PDF basis → PLACEHOLDER
- Tag all rows: `"library_source": "CUSTOMER_PDF"`

### PDF-6: Handle Protocol Extensions
Same as XLSX-5 — extend the form with missing protocol-required fields.
Mark extended rows: `"library_source": "EXTENDED_FROM_PROTOCOL"`

---

## Confidence Level Summary

| Source | Row Confidence | library_source tag |
|--------|---------------|-------------------|
| CDASH default, no library | MEDIUM | CDASH_DEFAULT |
| Customer XLSX, exact match | HIGH | CUSTOMER_XLSX_EXACT |
| Customer XLSX, partial match | MEDIUM | CUSTOMER_XLSX_PARTIAL |
| Customer XLSX, extended field | FLAGGED | EXTENDED_FROM_PROTOCOL |
| Customer XLSX, name deviation | FLAGGED | CUSTOMER_XLSX_EXACT/PARTIAL |
| Customer PDF, clear field | MEDIUM | CUSTOMER_PDF |
| Customer PDF, inferred logic | FLAGGED | CUSTOMER_PDF |
| Customer PDF, no mapping | PLACEHOLDER | CUSTOMER_PDF |
| No library match, CDASH fallback | MEDIUM | CDASH_DEFAULT_NO_LIBRARY_MATCH |

---

---

## Step 1: Extract the Study Visit Schedule

Before defining any forms, map the complete visit schedule. This drives
the timepoint CSV and all relevant/branching logic.

### 1a: Map Event OIDs to Timepoint Labels

For each visit in the Schedule of Assessments, assign:
- `event_oid` — short machine-readable ID (e.g., `SE_BASELINE`, `SE_C1`)
- `timepoint_label` — human-readable label (e.g., `Baseline`, `Course 1`)
- `arm` — `TREATMENT`, `CONTROL`, or `BOTH`
- `visit_window` — timing relative to key study events (e.g., "2-3 weeks post injection #1")
- `forms_assigned` — list of form_ids assigned to this visit

Use this naming convention for event OIDs:
- `SE_BASELINE` — screening/baseline
- `SE_C{n}` — treatment course n
- `SE_C{n}POST{timing}` — post-course timepoints (e.g., `SE_C1POST2H4H`)
- `SE_EOS` — end of study
- `SE_EOT` — end of treatment
- `SE_CTL{label}` — control group specific visits
- `SE_UNSCH` — unscheduled visit

### 1b: Generate Timepoint CSV Content

Output the full content of `{study_id}_tpt.csv` with columns:
`event,timepoint`

One row per event OID. This CSV is referenced by every form via:
`pulldata('{study_id}_tpt','timepoint','event',${EVENT_CF})`

---

## Step 2: Build the Complete CRF Inventory

**This step is critical. Do not proceed to form definitions until you have
a complete and verified CRF list. Every unique CRF must be identified here
before any form definition work begins.**

---

### Step 2a: Determine Input Mode

#### MODE A — Pricing Summary PDF Provided

If a protocol-to-pricing-summary PDF has been uploaded:

1. Read Section 4 (CRF Summary) — specifically the CRF Detail table
2. Extract for each unique CRF: domain name, CDASH code, visits used,
   complexity, re-use count, confidence, and notes
3. Use this as the authoritative CRF inventory — do not re-derive
4. If a CRF appears in the protocol but not the pricing summary,
   add it and flag with pricing_summary_gap: true
5. Proceed to Step 2d to add infrastructure forms, then Step 3+

#### MODE B — Protocol Only (Standalone)

Derive the complete CRF list using Steps 2b-2d below.
This must be as complete as the pricing summary would produce.

---

### Step 2b: Derive CRF List From Protocol (Mode B)

Work through ALL sources systematically:

**Source 1 — Every Schedule of Assessments table:**
For each assessment row × visit column combination:
- Map to CDASH domain
- Note which visits it appears at and which arms
- Check whether field set changes across visits (= new unique CRF)
- Check whether arm-specific (= arm-specific form versions)

**Source 2 — Protocol body sections:**
- Eligibility criteria → IE form fields; arm-specific criteria → 2 IE forms
- Study procedures → additional form requirements not in SoA
- Safety monitoring → AE/SAE/pregnancy reporting forms
- Treatment section → EX/EC form field requirements
- Dose adjustment criteria → EC repeating structure

**Source 3 — Apply these standing rules always:**
- DM and MH are ALWAYS separate forms
- I/E criteria with arm-specific items = 2 unique IE forms
- Full PE at screening + symptom-directed at follow-up = 2 unique PE forms
- Patient diaries / PRO = unique ePRO CRFs (never paper)
- PSA separate from full LB panel when at different visits
- Treatment arm biospecimen ≠ control arm biospecimen (different fields)
- Study drug EX and prodrug EC are always separate forms

**Source 4 — Infrastructure forms (always add):**
- DOV — Date of Visit — every visit
- DV — Protocol Deviation Log — ongoing
- SPELIG — Sponsor Eligibility Review — screening only

---

### Step 2c: Build the CRF Master Table

Produce the complete CRF Master Table before writing any form definitions.
Show this table in Output A Section 3.

For each unique CRF record:
- form_id, form_title, form_category, cdash_domain
- arm_applicability (TREATMENT / CONTROL / BOTH)
- visits_assigned (complete list of event OIDs)
- reuse_count
- complexity (Simple / Average / Complex)
- has_repeating_group (Yes / No)
- is_epro (Yes / No)
- pricing_summary_source (Yes if from PDF / No if derived)
- pricing_summary_gap (true if not in pricing summary)
- notes

**Completeness check:** Every assessment row in every SoA table must
map to at least one CRF. Flag any unmapped assessment.

---

### Step 2d: Assign Visits to Each Form

For every form in the CRF Master Table, list every event OID where
this form appears. This drives the timepoint CSV, relevant expressions,
and visit window constraints throughout Steps 3–8.

---

### Step 2e: Define Each Form

Only after the complete CRF Master Table is built, define each form.
Process in this order:
1. Infrastructure forms (DOV, SPELIG, DV)
2. Screening/baseline CDASH forms (DM, IE, MH)
3. Clinical assessment forms (VS, PE, LB, PSA, AE, CM, EX, EC, PR)
4. Biospecimen forms (BE, BE_CTL, BES)
5. Disposition and safety forms (DS, PREGPART)

For each form, produce the three-sheet XLSForm definition below.

---

### Sheet 1: Settings

```json
{
  "settings": {
    "form_title": "[Human-readable form name]",
    "form_id": "[CDASH domain code or custom code]",
    "version": "1",
    "style": "theme-grid",
    "namespaces": "oc=\"http://openclinica.org/xforms\" , OpenClinica=\"http://openclinica.com/odm\"",
    "crossform_references": ""
  }
}
```

Set `crossform_references` to `"current_event"` when the form references
data from other events. Leave empty if not needed.

---

### Sheet 2: Choices

For each choice list used in the form, output:
```json
{
  "choices": [
    {
      "list_name": "NY",
      "label": "No",
      "name": "N"
    },
    {
      "list_name": "NY",
      "label": "Yes",
      "name": "Y"
    }
  ]
}
```

**Standard choice lists to always include where applicable:**

| list_name | Contents |
|-----------|----------|
| NY | No/Yes (N/Y) |
| YN | Yes/No (Y/N) |
| NYU | No/Yes/Unknown |
| YNA | Yes/No/Not Applicable |
| SEX | Female/Male/Unknown/Undifferentiated |
| ETHNIC | Hispanic or Latino / Not Hispanic or Latino / Not Reported / Unknown |
| RACE | American Indian or Alaska Native / Asian / Black or African American / Native Hawaiian or Other Pacific Islander / White / Other / Unknown / Not Reported |
| DAY | 01–31 + UNK |
| MONTH | JAN–DEC + UNK |
| UNK | Unknown (UNK) |
| ND | Not Done |
| AESEV | Grade 1–5 |
| REL | No Not Related / No Unlikely Related / Yes Possibly / Yes Probably / Yes Definitely |
| OUT | Recovered/Resolved / Recovering/Resolving / Recovered with Sequelae / Not Recovered / Fatal / Unknown |
| AEACN | Dose Increased / Not Changed / Reduced / Drug Interrupted / Drug Withdrawn / Not Applicable / Unknown |
| DSDECOD | Completed / Adverse Event / Death / Lost To Follow-Up / Never Dosed / Physician Decision / Withdrawal by Subject / Consent Withdrawn / Screen Failure / Other |

Add study-specific choice lists from the protocol (e.g., injection routes,
lab names, radiation types) as additional entries. Flag protocol-specific
lists with `"source": "PROTOCOL_SPECIFIC"`.

---

### Sheet 3: Survey

For each field in the form, output a survey row object. Always include
these columns:

```json
{
  "type": "",
  "name": "",
  "label": "",
  "bind__oc_itemgroup": "",
  "hint": "",
  "appearance": "",
  "bind__oc_briefdescription": "",
  "bind__oc_description": "",
  "relevant": "",
  "required": "",
  "constraint": "",
  "constraint_message": "",
  "calculation": "",
  "readonly": "",
  "repeat_count": "",
  "bind__oc_external": "",
  "choice_filter": "",
  "completion_status": "COMPLETE | FLAGGED | PLACEHOLDER"
}
```

`completion_status` values:
- `COMPLETE` — Claude has fully populated this row from CDASH + protocol
- `FLAGGED` — Claude has made a best estimate but human review is needed
- `PLACEHOLDER` — Claude cannot determine the value; human must complete

---

## Step 3: Standard Survey Row Patterns

**Always include these standard rows at the top of every form's survey:**

```
Row 1: calculate | EVENT_CF | (blank label) |
  calculation: instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@StudyEventOID
  bind__oc_external: clinicaldata
  completion_status: COMPLETE

Row 2: calculate | TPTCALC | (blank label) | bind__oc_itemgroup: [FORM_ID] |
  calculation: pulldata('[STUDY_ID]_tpt','timepoint','event',${EVENT_CF})
  completion_status: COMPLETE
```

**For forms with repeating groups** — add counter row using full XPath:
```
Row 3: calculate | [DOMAIN]ID | 
  calculation: once(instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/FormData[@FormOID='[FORM_ID]']/ItemGroupData[@ItemGroupOID='[FORM_ID].[DOMAIN]']/@ItemGroupRepeatKey)
  bind__oc_external: clinicaldata
  completion_status: COMPLETE

Row 4: calculate | [DOMAIN]ID_CALC | bind__oc_itemgroup: [DOMAIN] |
  calculation: if(${[DOMAIN]ID}!='',${[DOMAIN]ID},'Scheduled')
  completion_status: COMPLETE
```

**NEVER abbreviate XPath strings with `...` or placeholder tokens.**
Always write the complete expression. Use the OID naming convention:
form_id → Form OID, `{form_id}.{domain}` → ItemGroup OID,
`{form_id}.{field_name}` → Item OID.

**Timepoint display field** (present in most forms):
```
type: text | name: [DOMAIN]TPT | label: ** Timepoint: ** | bind__oc_itemgroup: [DOMAIN] | calculation: ${TPTCALC} | readonly: yes
```

---

## Step 4: CDASH Domain Field Rules

Read `references/cdash-domain-library.md` for the complete field list per domain.

**Key rules when extracting from protocol:**

**Date fields** — always use partial date pattern (3 separate fields):
- `[prefix]DAT_YEAR` (integer) + `[prefix]DAT_MON` (select_one MONTH) +
  `[prefix]DAT_DAY` (select_one DAY) + `[prefix]DAT_UNK` (select_multiple UNK)
- Plus calculate fields: `[prefix]DAT`, `[prefix]DAT_CALC`, `[prefix]DAT_FDC`,
  `[prefix]DAT_BDC`, `[prefix]DAT_LEAP`, `[prefix]DAT_M`
- Exception: use `date` type for fields where partial dates are not expected
  (e.g., injection date, lab collection date, visit date)

**Repeating groups** — wrap in `begin group` / `end group` with:
- First occurrence: `[DOMAIN]YN` (select_one NY) — "Did participant report any X?"
- Note field for when answer is No from prior visit cross-form check
- Group: `begin group [DOMAIN]1` with `relevant: ${[DOMAIN]YN}='Y' or ${[DOMAIN]YN_CF}='Y'`

**Cross-form references** — when a field must reference data from another form:
- Add `calculate` row with `bind__oc_external: clinicaldata`
- **Always write the full XPath with real OIDs** — do not abbreviate or use placeholders
- Use this OID naming convention (self-consistent within the spec):
  - Event OID: from the timepoint CSV (e.g., `SE_BASELINE`, `SE_C1`)
  - Form OID: the form's `form_id` value (e.g., `DM`, `EX`, `VS`)
  - ItemGroup OID: `{form_id}.{cdash_domain}` (e.g., `DM.DM`, `VS.VS`)
  - Item OID: `{form_id}.{field_name}` (e.g., `DM.AGE`, `EX.EXDAT`)
- Full XPath pattern:
  `instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='{EVENT_OID}']/FormData[@FormOID='{FORM_ID}']/ItemGroupData[@ItemGroupOID='{FORM_ID}.{CDASH_DOMAIN}']/ItemData[@ItemOID='{FORM_ID}.{FIELD_NAME}']/@Value`
- Mark as `COMPLETE` when both the source form_id and field_name are defined
  in the spec — the OIDs are self-consistent and do not require human review
- Mark as `FLAGGED` ONLY when the source form or field is itself a PLACEHOLDER
  (i.e., when the referenced form has not yet been fully defined)
- Add the event OID to `crossform_references` in the settings sheet for
  performance optimisation (e.g., `crossform_references: SE_BASELINE`)

**Common cross-form calculate patterns (write these in full):**

AGE from DM (at baseline):
`instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_BASELINE']/FormData[@FormOID='DM']/ItemGroupData[@ItemGroupOID='DM.DM']/ItemData[@ItemOID='DM.AGE']/@Value`

Injection date from EX (Course 1):
`instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_C1']/FormData[@FormOID='EX']/ItemGroupData[@ItemGroupOID='EX.EX']/ItemData[@ItemOID='EX.EXDAT']/@Value`

ARM from IE (at baseline):
`instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_BASELINE']/FormData[@FormOID='IE']/ItemGroupData[@ItemGroupOID='IE.IE']/ItemData[@ItemOID='IE.ARM']/@Value`

Weight from VS (at baseline):
`instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_BASELINE']/FormData[@FormOID='VS']/ItemGroupData[@ItemGroupOID='VS.VS']/ItemData[@ItemOID='VS.WEIGHT_VSORRES']/@Value`

Current event OID (any form):
`instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@StudyEventOID`

Site OID:
`instance('clinicaldata')/ODM/ClinicalData/@StudyOID`

Baseline date from IE visit date:
`substr(instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_BASELINE']/@OpenClinica:StartDate,1,10)`

---

## Step 5: Infrastructure Form Definitions

### DOV — Date of Visit
Always present at every study visit. Standard fields:
- `VISYN` (select_one NY) — "Was the visit done?" — relevant when not baseline
- `VISDT` (date) — "Date of visit" — required, constraint: `. <= today()`
- `VISNDRSN` (text) — reason not done — relevant when `${VISYN}='N'`

### DV — Protocol Deviation Log
Repeating group. Standard fields:
- `DVYN`, `DVSEQ`, `DVDESC`, `DVSTDAT`, `DVAWDAT`, `DVREPDAT`
- `DVCLAS` (select_one DVCLASCD), `DVCOD` (select_one DVCAT)
- `DVIRB`, `DVIRBDAT`, `DVAESAE`, `DVACT`, `DVRES`, `DVCOVAL`

### SPELIG — Sponsor Eligibility Review
Simple form, screening only:
- `IEDTC` (date) — date of review
- `IEORRES` (select_one YN) — "Is patient eligible?"

---

## Step 6: Generate Lab Ranges CSV Placeholder

Output the structure of `labranges.csv` with headers and placeholder rows.

Standard columns:
`lab_name, test_code, test_name, lower, upper, unit, sex_filter, age_lower, age_upper`

Populate `test_code` and `test_name` for every lab test identified in the
protocol's lab assessment section. Leave `lower`, `upper`, `unit` as
`[PLACEHOLDER — SITE SPECIFIC]` for each site-specific lab.

---

## Step 7: Identify Cross-Form Dependencies

Map all cross-form data references needed across the study.

For each dependency, record:
- Source form + field (in `FormOID.ItemOID` format)
- Target form + field where reference is used
- Purpose (e.g., "AE start date must be after first injection date")
- Full XPath string (written out completely — no placeholders)
- Visit context (which event the source data comes from)
- Status: `COMPLETE` — OIDs are self-consistent within the spec

**Cross-form dependencies are COMPLETE by default.** They do not require
human review or OID confirmation as long as:
1. The source form is defined in the CRF Master Table (Step 2c)
2. The source field is defined in that form's survey
3. The event OID is defined in the timepoint CSV (Step 1b)

The only scenario where a cross-form reference is FLAGGED is when
the source form or field is itself unresolved (PLACEHOLDER).

---

## Step 8: Flag Summary

Produce a consolidated list of all items requiring human review before
the edc-builder can generate final files:

Categories:
- `SITE_SPECIFIC` — lab ranges, site filter lists, local lab names
- `PROTOCOL_AMBIGUOUS` — fields where protocol detail was insufficient
- `CONSTRAINT_REVIEW` — visit window constraints needing timing verification
- `CHOICE_LIST_REVIEW` — study-specific code lists needing confirmation
- `CUSTOM_DOMAIN` — non-standard forms or fields with no CDASH equivalent
- `UNRESOLVED_DEPENDENCY` — cross-form reference where source form/field
  is a PLACEHOLDER and the full XPath cannot be written yet

---

## Output Format

Produce ALL THREE outputs below. Do not omit any of them.

---

### OUTPUT 0: Generate the PDF

Before producing any text output, generate the PDF using the script at
`scripts/generate_pdf.py`. Follow these steps:

1. Extract all data from the protocol following Steps 1–8 above
2. Assemble the complete data dictionary matching the JSON structure in Output B
3. Import and call the generator:

```python
import sys
sys.path.insert(0, "/path/to/protocol-to-edc-structure/scripts")
from generate_pdf import build_edc_pdf
output_path = "/mnt/user-data/outputs/{PROTOCOL_NUMBER}_EDC_Structure.pdf"
build_edc_pdf(data, output_path)
```

4. Name the file: `{PROTOCOL_NUMBER}_EDC_Structure.pdf`
5. Use `present_files` to share the PDF with the user
6. If the script fails, report the error and still produce Outputs A and B

---

### OUTPUT 0b: Generate the XLSX Specification Document

Immediately after generating the PDF, generate the XLSX using the script
at `scripts/generate_xlsx.py`:

```python
import sys
sys.path.insert(0, "/path/to/protocol-to-edc-structure/scripts")
from generate_xlsx import build_edc_xlsx

output_path = "/mnt/user-data/outputs/{PROTOCOL_NUMBER}_EDC_Structure.xlsx"
build_edc_xlsx(data, output_path)
```

Name the file: `{PROTOCOL_NUMBER}_EDC_Structure.xlsx`
Use `present_files` to share the XLSX alongside the PDF.

**XLSX workbook structure:**
- `INDEX` — workbook summary, form inventory, instructions
- `TIMEPOINTS` — editable timepoint CSV content
- `LAB_RANGES` — lab ranges placeholder with highlighted empty cells
- `REVIEW_FLAGS` — all items requiring human review grouped by category
- Per form (one set of 3 tabs each):
  - `[FORMID]_survey` — editable survey rows with ACTION column and
    colour-coded status (green=complete, amber=flagged, red=placeholder)
  - `[FORMID]_choices` — editable choice lists with REVIEW_NOTES column
  - `[FORMID]_settings` — editable XLSForm settings + read-only metadata

**Colour coding in survey tabs:**
- Green rows = COMPLETE (no action needed)
- Amber rows = FLAGGED (review needed)
- Red rows = PLACEHOLDER (must be completed before building)
- Yellow cells = editable settings fields

**How the XLSX feeds back into the skill:**
When a human edits the XLSX and uploads it back to Claude, the skill
reads the ACTION column and REVIEW_NOTES to apply changes:
- ACTION = blank → keep row as-is
- ACTION = DELETE → remove this row from the form definition
- ACTION = ADD → new row to be incorporated
- Edited cell values → update the corresponding field in the JSON
The skill then regenerates all three outputs (PDF, JSON, XLSX) with
the changes applied.

---

---

### OUTPUT A: Human-Readable EDC Structure Summary

```
EDC STRUCTURE SUMMARY
=====================
Generated by: Claude (protocol-to-edc-structure skill)
Review status: PENDING HUMAN REVIEW — DO NOT BUILD UNTIL REVIEWED
Date: [date]
Protocol: [protocol number]
Study ID (for CSV naming): [suggested study_id]
Input Mode: [PROTOCOL_ONLY | PROTOCOL_WITH_XLSX_LIBRARY | PROTOCOL_WITH_PDF_LIBRARY | PROTOCOL_WITH_MIXED_LIBRARY]
Library Files: [list of files provided, or "None"]

SECTION 1 — STUDY EVENT SCHEDULE
[Table: Event OID | Timepoint Label | Arm | Visit Window | Forms Assigned]

SECTION 2 — TIMEPOINT CSV
[Full content of {study_id}_tpt.csv]

SECTION 3 — CRF MASTER TABLE (complete list of all unique CRFs)
[Table: # | Form ID | Form Title | Category | CDASH | Arm | Visits Assigned |
 Re-use Count | Complexity | Repeating | ePRO | Pricing Summary Source |
 Library Match | Total Fields | Flagged | Placeholder]

NOTE: Every assessment in every SoA table must map to a row in this table.
Any unmapped assessment is flagged as a completeness gap.

SECTION 4 — FORM DEFINITIONS (one subsection per form)
  4.1 [Form Title] ([form_id])
    Library Match: [EXACT/PARTIAL/NO_MATCH] from [source_file] ([XLSX/PDF])
    Settings: ...
    Choice Lists: [count] lists — [count] from library, [count] standard, [count] protocol-specific
    Survey: [count] rows — [count] from library, [count] extended, [count] CDASH default
    Name Deviations: [list of customer name → CDASH standard mappings]
    Repeating Groups: Yes/No
    Key Fields: [list of primary data fields]
    Flagged Items: [list of flagged rows with reason]
    Placeholder Items: [list of placeholder rows]

SECTION 5 — CROSS-FORM DEPENDENCY MAP
[Table: Source Form | Source Field | Target Form | Target Field | Purpose]

SECTION 6 — LAB RANGES CSV PLACEHOLDER
[Column headers + test_code rows with placeholder values]

SECTION 7 — ITEMS REQUIRING HUMAN REVIEW
[Grouped by category: SITE_SPECIFIC | OID_CONFIRMATION |
 PROTOCOL_AMBIGUOUS | CONSTRAINT_REVIEW | CHOICE_LIST_REVIEW |
 CUSTOM_DOMAIN | PDF_MAPPING_UNCERTAIN | NAME_DEVIATION]
```

---

### OUTPUT B: Structured JSON

```json
{
  "study_meta": {
    "protocol_number": "",
    "study_id": "",
    "generated_date": "",
    "review_status": "PENDING_HUMAN_REVIEW",
    "input_mode": "PROTOCOL_ONLY | PROTOCOL_WITH_XLSX_LIBRARY | PROTOCOL_WITH_PDF_LIBRARY | PROTOCOL_WITH_MIXED_LIBRARY",
    "pricing_summary_provided": false,
    "pricing_summary_crf_count": null,
    "derived_crf_count": null,
    "library_files_provided": [],
    "library_file_types": []
  },
  "timepoint_csv": {
    "filename": "{study_id}_tpt.csv",
    "rows": [
      { "event": "SE_BASELINE", "timepoint": "Baseline" }
    ]
  },
  "labranges_csv": {
    "filename": "labranges.csv",
    "columns": ["lab_name","test_code","test_name","lower","upper","unit","sex_filter","age_lower","age_upper"],
    "rows": [
      {
        "lab_name": "[PLACEHOLDER]",
        "test_code": "WBC",
        "test_name": "White Blood Cells",
        "lower": "[PLACEHOLDER — SITE SPECIFIC]",
        "upper": "[PLACEHOLDER — SITE SPECIFIC]",
        "unit": "[PLACEHOLDER — SITE SPECIFIC]",
        "sex_filter": "",
        "age_lower": "",
        "age_upper": ""
      }
    ]
  },
  "forms": [
    {
      "form_id": "",
      "form_title": "",
      "form_category": "CDASH_CLINICAL | INFRASTRUCTURE",
      "cdash_domain": "",
      "visits_assigned": [],
      "has_repeating_group": false,
      "is_epro": false,
      "arm_applicability": "TREATMENT | CONTROL | BOTH",
      "reuse_count": null,
      "complexity": "Simple | Average | Complex",
      "pricing_summary_source": false,
      "pricing_summary_gap": false,
      "library_match": {
        "status": "EXACT | PARTIAL | NO_MATCH | PROTOCOL_ONLY",
        "source_type": "XLSX | PDF | NONE",
        "source_file": "",
        "customer_form_id": "",
        "customer_form_title": "",
        "fields_from_library": 0,
        "fields_extended_from_protocol": 0,
        "fields_from_cdash_default": 0,
        "name_deviations": []
      },
      "settings": {
        "form_title": "",
        "form_id": "",
        "version": "1",
        "style": "theme-grid",
        "namespaces": "oc=\"http://openclinica.org/xforms\" , OpenClinica=\"http://openclinica.com/odm\"",
        "crossform_references": ""
      },
      "choices": [
        {
          "list_name": "",
          "label": "",
          "name": "",
          "source": "STANDARD | PROTOCOL_SPECIFIC",
          "filter_column": "",
          "filter_value": ""
        }
      ],
      "survey": [
        {
          "type": "",
          "name": "",
          "label": "",
          "bind__oc_itemgroup": "",
          "hint": "",
          "appearance": "",
          "bind__oc_briefdescription": "",
          "bind__oc_description": "",
          "relevant": "",
          "required": "",
          "constraint": "",
          "constraint_message": "",
          "calculation": "",
          "readonly": "",
          "repeat_count": "",
          "bind__oc_external": "",
          "choice_filter": "",
          "completion_status": "COMPLETE | FLAGGED | PLACEHOLDER",
          "library_source": "CDASH_DEFAULT | CUSTOMER_XLSX_EXACT | CUSTOMER_XLSX_PARTIAL | CUSTOMER_PDF | EXTENDED_FROM_PROTOCOL | CDASH_DEFAULT_NO_LIBRARY_MATCH",
          "pdf_original_label": "",
          "cdash_standard_name": "",
          "cdash_name_deviation": false,
          "cdash_name_confidence": "HIGH | MEDIUM | UNCERTAIN",
          "flag_reason": "",
          "dependencies": []
        }
      ],
      "cross_form_dependencies": [
        {
          "source_form": "",
          "source_field": "",
          "purpose": "",
          "xpath_pattern": "",
          "visit_context": "",
          "status": "FLAGGED — OID CONFIRMATION REQUIRED"
        }
      ]
    }
  ],
  "review_flags": {
    "site_specific": [],
    "oid_confirmation": [],
    "protocol_ambiguous": [],
    "constraint_review": [],
    "choice_list_review": [],
    "custom_domain": []
  }
}
```

---

## Applying Changes From an Edited XLSX

When a user uploads a previously-generated EDC Structure XLSX that has been
edited by a human reviewer, follow these steps to apply the changes and
regenerate all outputs.

### XLSX-APPLY-1: Detect Edited XLSX Input

Identify that the input is an edited XLSX (not a protocol or library PDF) by:
- Checking whether the file has the INDEX, TIMEPOINTS, and [FORMID]_survey
  tab structure of a previously-generated EDC structure workbook
- Looking for populated ACTION column values (DELETE, ADD) or edited cells
- Checking REVIEW_NOTES columns for reviewer comments

### XLSX-APPLY-2: Read All Changes

For each form's survey tab:
- Rows where ACTION = DELETE → remove from form definition
- Rows where ACTION = ADD → add as new survey row
- Rows where any editable column value differs from the original →
  update that field in the JSON

For each form's choices tab:
- Same ACTION logic — DELETE removes the choice, ADD adds it
- Edited label/name/filter values → update the choice

For each form's settings tab:
- Yellow-highlighted cells are editable — read any changed values
- Update the form's settings JSON accordingly

For the TIMEPOINTS tab:
- Read any edited event/timepoint rows and update the timepoint CSV
- Respect ACTION = DELETE to remove a timepoint

For the LAB_RANGES tab:
- Read any cells where PLACEHOLDER has been replaced with real values
- Update the labranges_csv in the JSON

For REVIEW_NOTES columns:
- Extract reviewer notes and include them in a change_log section
  of the output JSON so the changes are documented

### XLSX-APPLY-3: Validate Changes

Before regenerating outputs, validate the changes:
- Ensure no required survey columns (type, name, label) are blank
  after edits
- Ensure deleted rows do not leave orphaned begin/end group pairs
- Ensure new rows have valid XLSForm field types
- Flag any validation issues in Section 7 of the new output

### XLSX-APPLY-4: Regenerate All Three Outputs

With changes applied to the data dict, regenerate:
1. The PDF (generate_pdf.py) — now reflects reviewed/approved state
2. The JSON — updated with all human corrections
3. The XLSX — fresh copy with all ACTION columns cleared and
   status updated to reflect the applied changes

Add a `change_log` section to the JSON:
```json
{
  "change_log": {
    "applied_from_xlsx": true,
    "review_date": "[date]",
    "forms_modified": ["AE", "LB"],
    "rows_deleted": 3,
    "rows_added": 1,
    "fields_updated": 7,
    "reviewer_notes": ["Note from reviewer 1", "Note from reviewer 2"]
  }
}
```

If no changes are detected (ACTION columns all blank, no cell edits),
inform the user and confirm the specification is unchanged.

---

## Human Review Instructions

At the end of every output include this block:

```
─────────────────────────────────────────────────────
EDC STRUCTURE REVIEW REQUIRED — DO NOT BUILD UNTIL COMPLETE
─────────────────────────────────────────────────────
Before passing this output to the edc-builder skill:

1. REVIEW all items in Section 7 / review_flags

2. COMPLETE all PLACEHOLDER fields — especially:
   - Lab ranges CSV (site-specific values required)
   - Cross-form OID paths (require actual study configuration)
   - Fields with no CDASH mapping from PDF forms

3. VERIFY all FLAGGED survey rows:
   - PDF-derived fields: confirm label→CDASH name mappings are correct
   - Extended fields: confirm new fields fit naturally in the existing form
   - Name deviations: confirm customer names should be kept or standardized
   - Inferred constraints: confirm logic matches protocol requirements

4. CONFIRM visit window constraints match protocol timing exactly

5. CONFIRM study_id matches your OpenClinica study OID

6. VERIFY choice lists — especially PDF-derived ones where options
   may have been inferred rather than read directly

7. REVIEW name deviation list — decide whether to keep customer
   field names or align to CDASH standard before building

8. ADD any custom business rules not derivable from protocol or library

Once review is complete, pass the JSON output to the
edc-builder skill to generate the XLSForm .xlsx files.
─────────────────────────────────────────────────────
```
