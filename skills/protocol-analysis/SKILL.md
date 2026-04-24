---
name: protocol-analysis
description: >
  Reads a clinical trial protocol once and produces all downstream outputs:
  the Study Specification (PDF, XLSX, JSON) and the Protocol Summary
  (PDF, JSON). The Study Specification feeds the edc-builder skill. The Protocol
  Summary feeds the pricing-quote skill. Use this skill whenever a user
  uploads a protocol and asks for an EDC build, study build, XLSForm
  extraction, CRF structure, OpenClinica configuration, pricing summary,
  quote, or protocol analysis. Always run this skill before edc-builder
  and before pricing-quote.
---

# Protocol Analysis Skill

## Purpose

Read a clinical trial protocol once and produce all protocol-derived outputs
in a single pass:

- **Study Specification** — full XLSForm-level specification for every CRF
  (PDF + XLSX + JSON). Feeds the `edc-builder` skill.
- **Protocol Summary** — structured protocol overview with CRF list,
  complexity classification, and pricing flags
  (PDF + JSON). Feeds the `pricing-quote` skill.

**Outputs — 5 files per run:**
- `{PROTOCOL}_Study_Specification.pdf`
- `{PROTOCOL}_Study_Specification.xlsx`
- `{PROTOCOL}_Study_Specification.json`
- `{PROTOCOL}_Protocol_Summary.pdf`
- `{PROTOCOL}_Protocol_Summary.json`

---

## Before You Begin — Read All Reference Files

**Always read all five reference files before processing any protocol:**

1. `references/xlsform-patterns.md` — Standard XLSForm column definitions,
   OpenClinica-specific columns, field type reference, and standard constraint
   patterns used across all forms. Read this before writing any survey rows.

2. `references/cdash-domain-library.md` — Standard field definitions for every
   CDASH domain. Use these as the baseline for all clinical form fields.
   Protocol-specific variations override these defaults.

3. `references/crf-complexity-rules.md` — Complexity classification tiers
   (Simple / Average / Complex). This definition changes over time.
   Always read the file; never rely on memory.

4. `references/crf-categorization-examples.md` — Human-corrected examples
   from previous runs. Entries here override general rules when a similar
   situation is encountered. Read all entries before classifying.

5. `references/openclinica-oc4-docs.md` — Curated index into the live
   OpenClinica 4 user documentation (https://docs.openclinica.com/oc4/).
   Consult this when a question isn't answered by the four reference files
   above — in particular for XPath function support (OC4 docs §2.4.6
   Validated Functions Index), Form Logic syntax (§2.4.5), CDASH form
   library content (§2.3), and OID naming conventions (§2.4.9). When the
   distilled references (#1–#4) and the OC4 docs disagree on something,
   the distilled references win because they reflect what OpenClinica
   actually accepts in practice.

---

## Input Detection

**Before doing anything else, determine what inputs were provided.**

### Required Input
- **Protocol PDF** — always required. If not provided, stop and ask for it.

### Optional Inputs (check for each)
- **Customer CRF Library** — one or more PDF documents (monday.com column:
  `fileb5c8dt0c`). These are human-authored paper or visual form layouts.
- **Customer OC4 XLSForm Standards** — a zip file containing one xlsx per
  form/domain (monday.com column: `file_mm2mafjc`). These are
  OpenClinica-format XLSForms with survey/choices/settings sheets,
  in the same format produced by the edc-builder skill.

Determine input mode:
- **Mode 1** — Protocol only. No customer library or XLSForm standards provided.
- **Mode 2** — Protocol + Customer CRF Library (PDFs) only.
- **Mode 3** — Protocol + Customer OC4 XLSForm Standards (zip) only.
- **Mode 4** — Protocol + both Customer CRF Library and XLSForm Standards.

---

## Form Source Priority (Apply Per Form, Every Form)

For every form the protocol requires, work down this priority list and stop
at the first match found:

**Priority 1 — Customer CRF Library (PDF)**
- If a matching PDF form exists for this domain, use it as the base.
- Extend with any missing protocol-required fields; flag each extension.
- Never drop to Priority 2 once a PDF match is found, even for a partial match.

**Priority 2 — Customer OC4 XLSForm Standards (zip of xlsx)**
- Only consulted if no PDF match was found at Priority 1.
- If a matching xlsx exists in the zip, use it as the base.
- Extend with any missing protocol-required fields; flag each extension.

**Priority 3 — CDASH Defaults**
- Only used if no match was found at Priority 1 or Priority 2.
- Apply standard field definitions from `references/cdash-domain-library.md`.
- Tag all rows: `"library_source": "CDASH_DEFAULT"`.

---

## Processing Priority 1 — Customer CRF Library (PDF)
*(Modes 2 and 4 only — skip if no PDFs provided)*

### PDF-1: Identify Each Form
For each PDF file provided:
- Read the document title, header, and any form ID / version markings.
- Identify the CDASH domain this form represents.
- Note the form's section structure and grouping.

### PDF-2: Extract Field Information
For each visible field on the PDF form, extract:
- **Field label** — the question text as written
- **Field type** — infer from visual layout:
  - Checkbox(es) → `select_one` (single) or `select_multiple` (multi)
  - Text box (single line) → `text`, `integer`, or `decimal`
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
For each extracted field, attempt to map the label to a CDASH standard name.

**High-confidence mapping** (map automatically, no flag needed):
Common examples — "Age" → `AGE`, "Sex/Gender" → `SEX`,
"Adverse Event Term" → `AETERM`, "Start Date" (AE context) → `AESTDAT`,
"Severity" with grade options → `AESEV`, "Outcome" (AE context) → `AEOUT`,
"Medication Name/Drug Name" → `CMTRT`, "Collection Date" (lab) → `LBDAT`,
"Result" (lab) → `LBORRES`, "Date of Visit" → `VISDT`.
Apply full knowledge of CDASH field labels from domain library.

**Uncertain mapping** (map with flag):
- When label is ambiguous or non-standard
- Add `"pdf_original_label": "[exact label from PDF]"`
- Add `"cdash_name_confidence": "UNCERTAIN"`
- Mark `"completion_status": "FLAGGED"`
- Flag reason: "PDF label '[X]' mapped to CDASH '[Y]' — verify mapping is correct"

**No mapping possible** (leave as custom):
- Set name to sanitized version of label (no spaces, starts with letter)
- Mark `"completion_status": "PLACEHOLDER"`
- Flag reason: "No CDASH mapping found for PDF label '[X]' — assign field name manually"

### PDF-4: Reconstruct XLSForm Structure
Using the extracted fields, build the full form definition:
- Populate all survey rows with best-estimate values.
- For constraint/relevant/calculation columns, apply standard patterns from
  `references/xlsform-patterns.md` where applicable. Mark as `FLAGGED`
  with note "Inferred from PDF layout — verify logic".
- Apply known CDASH domain logic (e.g., AE severity triggers SAE fields).
- For choice lists, use customer's options if listed; fall back to standard
  CDASH choice lists where options not visible. Flag inferred choice lists.
- Tag all rows: `"library_source": "CUSTOMER_PDF"`

### PDF-5: Handle Protocol Extensions
When the protocol requires fields beyond what the PDF form contains:
- Append new rows to the end of the relevant group in the survey.
- Mark extended rows: `"library_source": "EXTENDED_FROM_PROTOCOL"`
- Mark `"completion_status": "FLAGGED"`
- Flag reason: "New field required by protocol — not in customer PDF library"

---

## Processing Priority 2 — Customer OC4 XLSForm Standards (zip)
*(Modes 3 and 4 only, and only for forms with no Priority 1 PDF match)*

### XLSX-1: Unzip and Read Each Form File
Unzip the provided zip file. For each xlsx file (one per form/domain),
extract all three sheets:
- `settings` → capture form_title, form_id, version, namespaces
- `choices` → capture all list_name / label / name rows + any filter columns
- `survey` → capture every row with all populated columns

### XLSX-2: Build XLSForm Standards Index
Create an internal index entry for each form:
```
{
  "form_id": "[from settings.form_id]",
  "form_title": "[from settings.form_title]",
  "source_file": "[filename]",
  "cdash_domain_match": "[inferred CDASH domain]",
  "total_survey_rows": n,
  "choice_lists": [...],
  "has_repeating_group": true/false,
  "field_names": [...],
  "source_type": "XLSX"
}
```

### XLSX-3: Match to Protocol Domains
For each domain that has no Priority 1 PDF match, search the XLSForm index:

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

**NO MATCH** — no customer xlsx found for this domain:
- Fall through to Priority 3 (CDASH defaults)
- Tag: `"library_source": "CDASH_DEFAULT_NO_LIBRARY_MATCH"`
- Confidence: MEDIUM

### XLSX-4: Handle Field Name Deviations
When a customer field name differs from CDASH standard:
- Use the customer's field name in the output
- Add `"cdash_standard_name": "[CDASH name]"`
- Add `"flag_reason": "Field name deviates from CDASH standard: customer uses [X], CDASH standard is [Y]"`
- Mark `"completion_status": "FLAGGED"`

### XLSX-5: Handle Protocol Extensions
When the protocol requires fields beyond what the xlsx form contains:
- Append new rows to the end of the relevant group in the survey.
- Mark extended rows: `"library_source": "EXTENDED_FROM_PROTOCOL"`
- Mark `"completion_status": "FLAGGED"`
- Flag reason: "New field required by protocol — not in customer XLSForm standard"

---

## Confidence Level Summary

| Source | Row Confidence | library_source tag |
|--------|---------------|-------------------|
| CDASH default, no library | MEDIUM | CDASH_DEFAULT |
| Customer PDF, clear field | MEDIUM | CUSTOMER_PDF |
| Customer PDF, inferred logic | FLAGGED | CUSTOMER_PDF |
| Customer PDF, no mapping | PLACEHOLDER | CUSTOMER_PDF |
| Customer XLSX, exact match | HIGH | CUSTOMER_XLSX_EXACT |
| Customer XLSX, partial match | MEDIUM | CUSTOMER_XLSX_PARTIAL |
| Customer XLSX, extended field | FLAGGED | EXTENDED_FROM_PROTOCOL |
| Customer XLSX, name deviation | FLAGGED | CUSTOMER_XLSX_EXACT/PARTIAL |
| No library match, CDASH fallback | MEDIUM | CDASH_DEFAULT_NO_LIBRARY_MATCH |

---

## Step 1: Extract the Study Visit Schedule

Before defining any forms, map the complete visit schedule.

### 1a: Map Event OIDs to Timepoint Labels
For each visit in the Schedule of Assessments, assign:
- `event_oid` — short machine-readable ID (e.g., `SE_BASELINE`, `SE_C1`)
- `timepoint_label` — human-readable label (e.g., `Baseline`, `Course 1`)
- `arm` — `TREATMENT`, `CONTROL`, or `BOTH`
- `visit_window` — timing relative to key study events
- `forms_assigned` — list of form_ids assigned to this visit

Use this naming convention for event OIDs:
- `SE_BASELINE` — screening/baseline
- `SE_C{n}` — treatment course n
- `SE_C{n}POST{timing}` — post-course timepoints
- `SE_EOS` — end of study
- `SE_EOT` — end of treatment
- `SE_CTL{label}` — control group specific visits
- `SE_UNSCH` — unscheduled visit

### 1b: Generate Timepoint CSV Content
Output the full content of `{study_id}_tpt.csv` with columns: `event,timepoint`
One row per event OID. This CSV is referenced by every form via:
`pulldata('{study_id}_tpt','timepoint','event',${EVENT_CF})`

---

## Step 2: Build the Complete CRF Inventory

**Do not proceed to form definitions until you have a complete and verified
CRF list. Every unique CRF must be identified here first.**

### Step 2a: Derive CRF List From Protocol

Work through ALL four sources:

**Source 1 — Every Schedule of Assessments table (row by row):**
For each assessment row × visit column:
- Map to CDASH domain
- Note which visits and arms it appears at
- Check whether field set changes across visits (= new unique CRF)
- Check whether arm differences create distinct form designs

Common CDASH domain mappings:
- Demographics → DM (always separate from MH)
- Medical History → MH (always separate from DM)
- Informed Consent + Eligibility → IE (split by arm if criteria differ)
- Disease Assessment / Characteristics → DC (always separate from IE)
- Vital Signs → VS (split if field set changes between visits)
- Physical Examination → PE (split if full vs. symptom-directed)
- Laboratory Assessments → LB; PSA separate if at different visits
- Adverse Events → AE; Concomitant Medications → CM
- Concomitant Procedures → PR_CONCOM (always separate from study drug PR)
- Study Drug Administration → EX; Prodrug/Companion Drug → EC
- Patient Diary / ePRO → separate ePRO CRF per diary instrument
- Biospecimen → BS/BE (split by arm if field sets differ)
- Disposition → DS; Pregnancy Reporting → PREGPART
- External Beam Radiation / Procedure → PR_EBRT

**Source 2 — Protocol body sections:**
- Eligibility criteria → arm-specific criteria = 2 IE forms
- Treatment section → patient diary = ePRO CRF; prodrug dose table = EC fields
- Procedures section → full PE at screening vs. symptom-directed at follow-up = 2 PE forms
- Safety section → SAE reporting, pregnancy reporting = PREGPART form
- Study operations → protocol deviation tracking = DV form

**Source 3 — Apply ALL standing rules from the learning log:**
Before finalising the CRF list, apply every rule in
`references/crf-categorization-examples.md`. Key rules:
- DM and MH → always 2 separate CRFs
- I/E with arm-specific criteria → 2 unique IE CRFs (one per arm)
- Full assessment at screening + modified at follow-up → 2 unique CRFs
- Patient diary / ePRO → always a separate unique CRF
- Disease assessment data → always a separate DC form; never part of IE
- Same CDASH domain code ≠ same CRF (different field sets = different forms)
- Biospecimen forms that differ by arm → 2 separate unique CRFs

**Source 4 — Infrastructure forms (always include):**
- DOV — Date of Visit — every visit
- DV — Protocol Deviation Log — ongoing
- SPELIG — Sponsor Eligibility Review — screening only

### Step 2b: Build the CRF Master Table

Produce the complete CRF Master Table before writing any form definitions.

For each unique CRF record:
- form_id, form_title, form_category, cdash_domain
- arm_applicability (TREATMENT / CONTROL / BOTH)
- visits_assigned (complete list of event OIDs)
- reuse_count
- complexity (Simple / Average / Complex — from Step 5 of Protocol Summary)
- has_repeating_group (Yes / No)
- is_epro (Yes / No)
- priority_source (PDF_LIBRARY / XLSX_STANDARD / CDASH_DEFAULT)
- library_match_status (EXACT / PARTIAL / NO_MATCH)
- notes

**Completeness check:** Every assessment row in every SoA table must
map to at least one CRF. Flag any unmapped assessment.

### Step 2c: Assign Visits to Each Form
For every form, list every event OID where it appears. This drives
the timepoint CSV, relevant expressions, and visit window constraints.

### Step 2d: Define Each Form
Only after the complete CRF Master Table is built, define each form.
Process in this order:
1. Infrastructure forms (DOV, SPELIG, DV)
2. Screening/baseline CDASH forms (DM, IE, MH)
3. Clinical assessment forms (VS, PE, LB, PSA, AE, CM, EX, EC, PR)
4. Biospecimen forms (BE, BE_CTL, BES)
5. Disposition and safety forms (DS, PREGPART)

For each form, apply the Form Source Priority rules above to determine
which source (PDF library, XLSForm standard, or CDASH) to use as base,
then produce the full three-sheet XLSForm definition.

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

**For forms with repeating groups:**
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
Always write the complete expression.

**Timepoint display field:**
```
type: text | name: [DOMAIN]TPT | label: ** Timepoint: ** |
bind__oc_itemgroup: [DOMAIN] | calculation: ${TPTCALC} | readonly: yes
```

---

## Step 4: CDASH Domain Field Rules

Read `references/cdash-domain-library.md` for the complete field list per domain.

**Date fields** — always use partial date pattern (3 separate fields):
- `[prefix]DAT_YEAR` (integer) + `[prefix]DAT_MON` (select_one MONTH) +
  `[prefix]DAT_DAY` (select_one DAY) + `[prefix]DAT_UNK` (select_multiple UNK)
- Plus calculate fields: `[prefix]DAT`, `[prefix]DAT_CALC`, `[prefix]DAT_FDC`,
  `[prefix]DAT_BDC`, `[prefix]DAT_LEAP`, `[prefix]DAT_M`
- Exception: use `date` type when partial dates are not expected

**Repeating groups:**
- First occurrence: `[DOMAIN]YN` (select_one NY) — "Did participant report any X?"
- Group: `begin group [DOMAIN]1` with `relevant: ${[DOMAIN]YN}='Y' or ${[DOMAIN]YN_CF}='Y'`

**Cross-form references:**
- Add `calculate` row with `bind__oc_external: clinicaldata`
- Always write full XPath with real OIDs — no abbreviations
- OID convention: Event OID from timepoint CSV; Form OID = form_id;
  ItemGroup OID = `{form_id}.{cdash_domain}`; Item OID = `{form_id}.{field_name}`
- Full XPath pattern:
  `instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='{EVENT_OID}']/FormData[@FormOID='{FORM_ID}']/ItemGroupData[@ItemGroupOID='{FORM_ID}.{CDASH_DOMAIN}']/ItemData[@ItemOID='{FORM_ID}.{FIELD_NAME}']/@Value`
- Mark COMPLETE when source form and field are defined in this spec
- Mark FLAGGED only when source form/field is itself a PLACEHOLDER

---

## Step 5: Infrastructure Form Definitions

### DOV — Date of Visit
Standard fields at every visit:
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

Output the structure of `labranges.csv` with columns:
`lab_name, test_code, test_name, lower, upper, unit, sex_filter, age_lower, age_upper`

Populate `test_code` and `test_name` for every lab test identified in the
protocol. Leave `lower`, `upper`, `unit` as `[PLACEHOLDER — SITE SPECIFIC]`.

---

## Step 7: Cross-Form Dependencies

For each dependency, record:
- Source form + field (FormOID.ItemOID format)
- Target form + field
- Purpose
- Full XPath string (written completely — no placeholders)
- Visit context
- Status: COMPLETE unless source form/field is a PLACEHOLDER

---

## Step 8: EDC Flag Summary

Produce a consolidated list of all items requiring human review:
- `SITE_SPECIFIC` — lab ranges, site filter lists, local lab names
- `PROTOCOL_AMBIGUOUS` — insufficient protocol detail
- `CONSTRAINT_REVIEW` — visit window constraints needing verification
- `CHOICE_LIST_REVIEW` — study-specific code lists needing confirmation
- `CUSTOM_DOMAIN` — non-standard forms or fields with no CDASH equivalent
- `PDF_MAPPING_UNCERTAIN` — PDF label → CDASH name mappings to verify
- `NAME_DEVIATION` — customer field names differing from CDASH standard
- `UNRESOLVED_DEPENDENCY` — cross-form reference where source is PLACEHOLDER

---

## Step 9: Protocol Summary — CRF Count and Unique/Re-use Split

Using the CRF Master Table from Step 2b:

**Unique CRF** = a distinct buildable XLSForm (distinct combination of
form design + field set + arm applicability).

**Re-use CRF** = every additional visit where an already-designed form
is deployed without field changes.

For each unique CRF:
- List every visit where it is used
- Re-use count = (total visits for that form) − 1

Sum all unique CRFs and re-use CRFs across all forms.

**Completeness check:** The unique CRF count from this step drives the
Protocol Summary output and must be consistent with the CRF Master Table.

---

## Step 10: Protocol Summary — Complexity Classification

For each unique CRF, apply `references/crf-complexity-rules.md` and
`references/crf-categorization-examples.md` to assign:
**Simple**, **Average**, or **Complex**

Record:
- Classification and primary reason
- Confidence: High (explicit protocol detail), Medium (CDASH estimate),
  or Low (insufficient information)

---

## Step 11: Protocol Summary — Visit Totals

- Visits per patient for each arm
- Total patient visits = (visits per patient × patients) summed across all arms
- Note any visits that may vary per patient

---

## Step 12: Protocol Summary — Complexity Flags

Identify study-level factors that add pricing complexity:
- Biological, gene therapy, or cell therapy investigational products
- Home-based sample or data collection
- Patient replacement rules
- SAE reporting timelines (24-hour requirements)
- Multi-arm differential scheduling
- Optional or conditional visits
- Regulatory complexity (IND, multiple IRBs, international sites)
- Non-standard CDASH domains or custom assessments

---

## Step 13: Protocol Summary — Conditional Branching

For each branching point:
- Description, Type (arm / visit / condition / optional)
- Affected CRF domain(s)
- Confidence: High / Medium / Low
- Note `[FIELD-LEVEL DETAIL REQUIRES CRF SPEC CONFIRMATION]` where applicable

Standard CDASH branching to always flag:
- AE severity ≥ Grade 3 → SAE assessment fields
- Abnormal lab values → follow-up fields
- Positive pregnancy test → pregnancy reporting workflow

---

## Step 14: Protocol Summary — Data Cleaning Complexity

For each CDASH domain, estimate:
- Per-domain complexity rating: Low / Medium / High
- Implied logical checks by category (range, required field, cross-form,
  domain-specific, arm-specific, derived field, date logic)

Always include: *"Precise check counts require downstream CRF specification
and data management plan review. This estimate is directional only."*

---

## Output Format

Produce ALL FIVE outputs below. Do not omit any of them.

---

### OUTPUT 1: Study Specification PDF

Generate using `scripts/generate_study_spec_pdf.py`:

```python
import sys
sys.path.insert(0, "/path/to/protocol-analysis/scripts")
from generate_study_spec_pdf import build_study_spec_pdf
output_path = "/mnt/user-data/outputs/{PROTOCOL_NUMBER}_Study_Specification.pdf"
build_study_spec_pdf(data, output_path)
```

Name: `{PROTOCOL_NUMBER}_Study_Specification.pdf`

---

### OUTPUT 2: Study Specification XLSX

Generate using `scripts/generate_study_spec_xlsx.py`:

```python
from generate_study_spec_xlsx import build_study_spec_xlsx
output_path = "/mnt/user-data/outputs/{PROTOCOL_NUMBER}_Study_Specification.xlsx"
build_study_spec_xlsx(data, output_path)
```

Name: `{PROTOCOL_NUMBER}_Study_Specification.xlsx`

**Workbook structure:**
- `INDEX` — workbook summary, form inventory, instructions
- `TIMEPOINTS` — editable timepoint CSV content
- `LAB_RANGES` — lab ranges placeholder with highlighted empty cells
- `REVIEW_FLAGS` — all items requiring human review grouped by category
- Per form: `[FORMID]_survey`, `[FORMID]_choices`, `[FORMID]_settings`

**Colour coding in survey tabs:**
- Green rows = COMPLETE
- Amber rows = FLAGGED (review needed)
- Red rows = PLACEHOLDER (must be completed before building)
- Yellow cells = editable settings fields

---

### OUTPUT 3: Study Specification JSON

The full structured data dict produced by Steps 1–8. This is consumed
directly by the `edc-builder` skill.

```json
{
  "study_meta": {
    "protocol_number": "",
    "study_id": "",
    "generated_date": "",
    "review_status": "PENDING_HUMAN_REVIEW",
    "input_mode": "PROTOCOL_ONLY | PROTOCOL_WITH_PDF_LIBRARY | PROTOCOL_WITH_XLSX_STANDARD | PROTOCOL_WITH_BOTH_LIBRARIES",
    "library_files_provided": [],
    "library_file_types": []
  },
  "timepoint_csv": {
    "filename": "{study_id}_tpt.csv",
    "rows": [ { "event": "SE_BASELINE", "timepoint": "Baseline" } ]
  },
  "labranges_csv": {
    "filename": "labranges.csv",
    "columns": ["lab_name","test_code","test_name","lower","upper","unit","sex_filter","age_lower","age_upper"],
    "rows": [ ... ]
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
      "priority_source": "PDF_LIBRARY | XLSX_STANDARD | CDASH_DEFAULT",
      "library_match": {
        "status": "EXACT | PARTIAL | NO_MATCH | PROTOCOL_ONLY",
        "source_type": "PDF | XLSX | NONE",
        "source_file": "",
        "fields_from_library": 0,
        "fields_extended_from_protocol": 0,
        "fields_from_cdash_default": 0,
        "name_deviations": []
      },
      "settings": { "form_title": "", "form_id": "", "version": "1",
        "style": "theme-grid",
        "namespaces": "oc=\"http://openclinica.org/xforms\" , OpenClinica=\"http://openclinica.com/odm\"",
        "crossform_references": "" },
      "choices": [ { "list_name": "", "label": "", "name": "",
        "source": "STANDARD | PROTOCOL_SPECIFIC", "filter_column": "", "filter_value": "" } ],
      "survey": [
        { "type": "", "name": "", "label": "", "bind__oc_itemgroup": "",
          "hint": "", "appearance": "", "bind__oc_briefdescription": "",
          "bind__oc_description": "", "relevant": "", "required": "",
          "constraint": "", "constraint_message": "", "calculation": "",
          "readonly": "", "repeat_count": "", "bind__oc_external": "",
          "choice_filter": "",
          "completion_status": "COMPLETE | FLAGGED | PLACEHOLDER",
          "library_source": "CDASH_DEFAULT | CUSTOMER_XLSX_EXACT | CUSTOMER_XLSX_PARTIAL | CUSTOMER_PDF | EXTENDED_FROM_PROTOCOL | CDASH_DEFAULT_NO_LIBRARY_MATCH",
          "pdf_original_label": "", "cdash_standard_name": "",
          "cdash_name_deviation": false,
          "cdash_name_confidence": "HIGH | MEDIUM | UNCERTAIN",
          "flag_reason": "", "dependencies": [] }
      ],
      "cross_form_dependencies": [
        { "source_form": "", "source_field": "", "purpose": "",
          "xpath_pattern": "", "visit_context": "",
          "status": "COMPLETE | FLAGGED" }
      ]
    }
  ],
  "review_flags": {
    "site_specific": [],
    "oid_confirmation": [],
    "protocol_ambiguous": [],
    "constraint_review": [],
    "choice_list_review": [],
    "custom_domain": [],
    "pdf_mapping_uncertain": [],
    "name_deviation": []
  }
}
```

---

### OUTPUT 4: Protocol Summary PDF

Generate using `scripts/generate_protocol_summary_pdf.py`:

```python
from generate_protocol_summary_pdf import build_protocol_summary_pdf
output_path = "/mnt/user-data/outputs/{PROTOCOL_NUMBER}_Protocol_Summary.pdf"
build_protocol_summary_pdf(summary_data, output_path)
```

Name: `{PROTOCOL_NUMBER}_Protocol_Summary.pdf`

The PDF uses this structure:

```
PROTOCOL SUMMARY
================
Generated by: Claude (protocol-analysis skill)
Review status: PENDING HUMAN REVIEW
Date: [date]

SECTION 1 — STUDY OVERVIEW
Protocol Number:
Sponsor:
Study Title:
Therapeutic Area:
Study Phase:
Study Type:
Number of Sites: [value or NOT SPECIFIED — PLEASE COMPLETE]
Region(s): [value or NOT SPECIFIED — PLEASE COMPLETE]
Estimated Start Date:
Estimated End Date:
Study Duration (months):

SECTION 2 — PATIENT POPULATION
Total Planned Enrollment:
Number of Arms / Groups:
  [Arm 1 Name]: n= | [description]
  [Arm 2 Name]: n= | [description]

SECTION 3 — VISIT SUMMARY
  [Arm 1]: X visits × N patients = X total patient visits
  [Arm 2]: X visits × N patients = X total patient visits
  TOTAL PATIENT VISITS (all arms): X

SECTION 4 — CRF SUMMARY
Total Unique CRFs: X  |  Simple: X  |  Average: X  |  Complex: X
Total Re-use CRFs: X

CRF DETAIL:
| Domain | CDASH | Source | Visits Used | Complexity | Re-uses | Confidence | Notes |

SECTION 5 — COMPLEXITY FLAGS

SECTION 6 — CONFIDENCE & REVIEW NOTES

SECTION 7 — CONDITIONAL BRANCHING INDICATORS

SECTION 8 — DATA CLEANING COMPLEXITY ESTIMATE
```

---

### OUTPUT 5: Protocol Summary JSON

The structured data dict produced by Steps 9–14. This is consumed
directly by the `pricing-quote` skill.

```json
{
  "skill_meta": {
    "mode": "PROTOCOL_ONLY | PROTOCOL_WITH_PDF_LIBRARY | PROTOCOL_WITH_XLSX_STANDARD | PROTOCOL_WITH_BOTH_LIBRARIES",
    "library_files_provided": [],
    "library_format_detected": ""
  },
  "study_meta": {
    "protocol_number": "",
    "sponsor": "",
    "study_title": "",
    "therapeutic_area": "",
    "study_phase": "",
    "study_type": "",
    "number_of_sites": null,
    "regions": null,
    "start_date": "",
    "end_date": "",
    "total_study_duration_months": null,
    "customer_segment": "COMMERCIAL | ACADEMIC | LOW_MARKET",
    "volume_studies": 1
  },
  "patient_population": {
    "total_enrollment": null,
    "number_of_arms": null,
    "arms": [ { "name": "", "n": null, "description": "" } ]
  },
  "visit_summary": {
    "arms": [ { "name": "", "visits_per_patient": null, "patients": null, "total_visits": null } ],
    "total_patient_visits_all_arms": null
  },
  "crf_summary": {
    "total_unique_crfs": null,
    "simple_crfs": null,
    "average_crfs": null,
    "complex_crfs": null,
    "total_reuse_crfs": null,
    "crf_detail": [
      { "domain_name": "", "cdash_code": "",
        "source": "PDF_LIBRARY | XLSX_STANDARD | CDASH_DEFAULT",
        "visits_used": [], "complexity": "", "reuse_count": null,
        "confidence": "", "notes": "" }
    ]
  },
  "review_flags": {
    "site_specific": [],
    "oid_confirmation": [],
    "protocol_ambiguous": [],
    "constraint_review": [],
    "custom_domain": [],
    "pdf_mapping_uncertain": [],
    "name_deviation": []
  },
  "complexity_flags": [],
  "confidence_review_notes": [],
  "conditional_branching": [
    { "description": "", "type": "", "affected_domains": [], "confidence": "", "note": "" }
  ],
  "data_cleaning_estimate": {
    "disclaimer": "Precise check counts require downstream CRF specification and data management plan review.",
    "domains": [ { "domain": "", "cdash_code": "", "complexity_rating": "", "implied_checks": [] } ]
  }
}
```

---

## Step 15: Present All Outputs

Use `present_files` to share all five files with the user.

Report in chat:
- Input mode detected (which library files were provided, if any)
- Total unique CRFs and re-use CRFs
- CRF complexity breakdown (Simple / Average / Complex counts)
- Total number of EDC flag items requiring human review (by category)
- Study duration in months
- Whether any forms fell back to CDASH (no library match found)

---

## Applying Changes From an Edited XLSX

When a user uploads a previously-generated Study Specification XLSX that has
been edited by a human reviewer:

1. Detect that the input is an edited XLSX (has INDEX, TIMEPOINTS, and
   [FORMID]_survey tab structure)
2. Read all changes:
   - ACTION = DELETE → remove row from form definition
   - ACTION = ADD → add as new survey row
   - Edited cell values → update the corresponding field
   - REVIEW_NOTES → include in change_log section of output JSON
3. Validate changes (no blank required columns, no orphaned group pairs)
4. Regenerate all five outputs with changes applied
5. Add `change_log` to the JSON documenting what was changed

---

## Human Review Instructions

At the end of every run include both blocks:

```
─────────────────────────────────────────────────────
EDC STRUCTURE REVIEW REQUIRED — DO NOT BUILD UNTIL COMPLETE
─────────────────────────────────────────────────────
1. REVIEW all items in Section 8 (EDC flag summary)
2. COMPLETE all PLACEHOLDER fields (lab ranges, cross-form OID paths,
   PDF fields with no CDASH mapping)
3. VERIFY all FLAGGED survey rows (PDF-derived fields, extended fields,
   name deviations, inferred constraints)
4. CONFIRM visit window constraints match protocol timing exactly
5. CONFIRM study_id matches your OpenClinica study OID
6. VERIFY choice lists — especially PDF-derived ones
7. REVIEW name deviation list — keep customer names or align to CDASH?
8. ADD any custom business rules not derivable from protocol or library

Once review is complete, pass the Study Specification JSON to the
edc-builder skill to generate the XLSForm .xlsx files.
─────────────────────────────────────────────────────

─────────────────────────────────────────────────────
PROTOCOL SUMMARY REVIEW REQUIRED
─────────────────────────────────────────────────────
1. COMPLETE any fields marked [NOT SPECIFIED — PLEASE COMPLETE]
   (Number of Sites is most critical for pricing)
2. VERIFY all CRF complexity classifications (especially Medium/Low confidence)
3. REVIEW any forms where library match was PARTIAL
4. CONFIRM conditional branching points are complete
5. ADD corrections to references/crf-categorization-examples.md
   so future runs improve automatically

Once review is complete, pass the Protocol Summary JSON to the
pricing-quote skill to generate the quote.
─────────────────────────────────────────────────────
```
