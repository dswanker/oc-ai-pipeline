# XLSForm Build Rules — OpenClinica EDC Builder

**Source:** Official OpenClinica blank form template (form_template.xls)
**Purpose:** Exact column definitions, ordering, and OpenClinica-specific
rules that every generated XLSForm must follow.

---

## Exact Sheet Structure

Every output XLSForm must have exactly these 3 sheets in this order:
1. `settings`
2. `choices`
3. `survey`

Do NOT include the reference sheets (Cross-Form Examples etc.) in output files.
Those are template documentation only.

---

## settings sheet — Exact Column Order

| Column | Required | Notes |
|--------|----------|-------|
| form_title | Yes | Human-readable name |
| form_id | Yes | OpenClinica Form OID: `F_<n>` (e.g. `F_DEMO`, `F_VS`). No spaces. Must start with `F_`. |
| version | Yes | Integer, start at 1 |
| style | Yes | Always `theme-grid` for OpenClinica |
| crossform_references | No | Blank, or comma-separated Event OIDs, or `current_event` |
| namespaces | Yes | Always exactly: `oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"` |

**Important:** The namespaces value must be on row 2, column 6. The settings
sheet has exactly 2 data rows — row 1 is headers, row 2 is values.

---

## choices sheet — Exact Column Order

| Column | Required | Notes |
|--------|----------|-------|
| list_name | Yes | Unique list identifier. No spaces. |
| label | Yes | User-visible option text |
| name | Yes | Machine-readable option value. No spaces. |
| image | No | Leave blank unless image is needed |

**Additional columns** added after `image` when needed:
- `site_filter` — for site-based choice filtering (e.g., lab names)
- `timepoint` — for timepoint-based choice filtering (e.g., DSDECOD)
- Any other custom filter column referenced in `choice_filter`

**Group choices by list_name.** All choices for the same list must be
consecutive rows. Choice list order matters — maintain spec order.

---

## survey sheet — Exact Column Order (20 standard columns)

| # | Column | Required | Notes |
|---|--------|----------|-------|
| 1 | type | Yes | Field type (see types below) |
| 2 | name | Yes | Machine-readable ID. No spaces. Start with letter. |
| 3 | label | Yes | User-visible question text. Supports HTML and ${ref}. |
| 4 | bind::oc:itemgroup | No | Item group reference in dotted form `F_<FORM>.<GROUP>` (e.g. `F_DEMO.DM`, `F_AE.AE_GROUP`) |
| 5 | hint | No | Helper text below label |
| 6 | appearance | No | Layout hint (w1-w9, horizontal, minimal, multiline, field-list, columns) |
| 7 | bind::oc:briefdescription | No | Short description for reporting |
| 8 | bind::oc:description | No | Full description for reporting |
| 9 | relevant | No | Show/hide XPath expression |
| 10 | required | No | yes / true() / expression |
| 11 | required_message | No | Message when required field is empty |
| 12 | constraint | No | Validation rule XPath expression |
| 13 | constraint_message | No | Error message when constraint fails |
| 14 | default | No | Default value or expression |
| 15 | calculation | No | Auto-calculated value XPath expression |
| 16 | trigger | No | Trigger field for calculations |
| 17 | readonly | No | yes or blank |
| 18 | image | No | Image filename (rarely used) |
| 19 | repeat_count | No | Integer or expression for repeating groups |
| 20 | bind::oc:external | No | External data source: `clinicaldata` / `labranges` / `{study_id}_tpt` / `contactdata` / `signature` / `identifier` |

**Additional columns** that may be added after the 20 standard columns:
- `choice_filter` — XPath expression to filter choices
- `bind::oc:constraint-type` — `hard` for hard edit checks (default is soft)
- `bind::oc:required-type` — `hard` for hard required checks
- `bind::oc:oc_annotation_LABEL` — custom annotations for annotated PDF
- `instance::oc:contactdata` — contact info field type
- `instance::oc:identifier` — offline participant ID field

**Column order is mandatory.** OpenClinica reads columns by name but
maintaining the standard order prevents import errors.

---

## Valid Field Types

| Type | Description |
|------|-------------|
| text | Free text string |
| integer | Whole number |
| decimal | Decimal number |
| date | Full date (YYYY-MM-DD) |
| time | Time |
| dateTime | Date and time |
| select_one [list] | Single choice |
| select_multiple [list] | Multiple choices |
| note | Display-only text |
| calculate | Hidden calculated field |
| begin group | Start a group |
| end group | End a group |
| begin repeat | Start a repeating group |
| end repeat | End a repeating group |

---

## Cross-Form Reference Patterns

**Reference item from another event (by OID — preferred):**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='EVENT_OID']/FormData[@FormOID='FORM_OID']/ItemGroupData[@ItemGroupOID='FORM_OID.DOMAIN']/ItemData[@ItemOID='FORM_OID.FIELD_NAME']/@Value
```
→ `bind::oc:external: clinicaldata`
→ `crossform_references: EVENT_OID` (in settings sheet, improves performance)

**Reference item from same event:**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/FormData[@FormOID='FORM_OID']/ItemGroupData/ItemData[@ItemOID='FORM_OID.FIELD_NAME']/@Value
```
→ `bind::oc:external: clinicaldata`
→ `crossform_references: current_event`

**Reference current event OID:**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@StudyEventOID
```

**Reference current event start date:**
```
substr(instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@OpenClinica:StartDate,1,10)
```

**Reference study/site OID:**
```
instance('clinicaldata')/ODM/ClinicalData/@StudyOID
```

**Reference participant ID:**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/@OpenClinica:StudySubjectID
```

**Reference user role:**
```
instance('clinicaldata')/ODM/ClinicalData/UserInfo/@OpenClinica:UserRole
```

**pulldata from CSV:**
```
pulldata('{study_id}_tpt','timepoint','event',${EVENT_CF})
pulldata('labranges','lower','test_code','WBC')
```
→ `bind::oc:external: {study_id}_tpt` or `labranges`

---

## Hard Edit Checks

To make a **hard constraint** (value rejected if violated):
- Fill in `constraint` and `constraint_message` as normal
- Add column `bind::oc:constraint-type` with value `hard`

To make a **hard required** field (cannot skip):
- Fill in `required` as normal
- Add column `bind::oc:required-type` with value `hard`

Default (blank) = soft check (warning shown but user can proceed).

---

## Once() Pattern for Repeating Groups

For forms with repeating groups (AE, CM, MH, DV, PR), the counter
field uses the `once()` function to prevent overwriting on edit:

```
once(instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='EVENT_OID']/FormData[@FormOID='FORM_OID']/ItemGroupData[@ItemGroupOID='FORM_OID.DOMAIN']/@ItemGroupRepeatKey)
```

This calculates the repeat key once when the form opens and locks it.
The `_CALC` version (`if(${ID}!='',${ID},'Scheduled')`) is used to
display the current record number or 'Scheduled' for new records.

---

## Timepoint Lookup Pattern (Every Form)

Every form that appears in multiple visits must include these two
calculate fields at the top of the survey:

```
Row 1: calculate | EVENT_CF | (blank) | (blank) | (blank) | (blank) | ... | 
       calculation: instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@StudyEventOID
       bind::oc:external: clinicaldata

Row 2: calculate | TPTCALC | (blank) | [FORM_ID] | ... |
       calculation: pulldata('{study_id}_tpt','timepoint','event',${EVENT_CF})
       (no bind::oc:external needed — references CSV)
```

---

## Group Structure Rules

- Every `begin group` must have a matching `end group`
- Every `begin repeat` must have a matching `end repeat`
- Groups can be nested but must be properly closed
- `begin group` and `end group` rows have blank name on `end group`
- Groups with `field-list` appearance display all fields on one screen
- Groups with `w4`, `w6` etc. set the width for all children

---

## Important Build Notes

1. **No blank rows** between data rows in survey or choices sheets
2. **No merged cells** in any sheet
3. **Column headers exactly as specified** — case sensitive
4. **form_id must be unique** across all forms in the study
5. **Field names must be unique** within a form
6. **list_name values** in choices must match `select_one [list]` references
7. **bind::oc:itemgroup** uses dotted form `F_<FORM>.<GROUP>` consistent
   with the form_id (also `F_`-prefixed). The group portion typically
   matches the CDASH domain code or a descriptive group name. The full
   value must be consistent for data mapping in OpenClinica.
8. **Choice filter column** in choices sheet must match the column name
   referenced in the survey's `choice_filter` cell

---

## File Naming Convention

Output files must be named to match the form_id for easy identification:
`{form_id}.xlsx` (e.g., `F_AE.xlsx`, `F_VS.xlsx`, `F_DOV.xlsx`).
form_id always carries the `F_` prefix per the OpenClinica OID convention.

Exception: forms sharing the same form_id but different designs
(e.g., F_IE_TRT and F_IE_CTL both have form_id=F_IE) — use the variant
identifier as the filename: `F_IE_TRT.xlsx`, `F_IE_CTL.xlsx`
