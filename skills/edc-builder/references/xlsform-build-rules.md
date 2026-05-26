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
| form_id | Yes | Short uppercase name (e.g. `DEMO`, `VS`, `ICF`, `AE`). No spaces. NO F_ prefix — OpenClinica adds any internal prefix itself during upload. |
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
| 4 | bind::oc:itemgroup | Yes for data rows | Short group code (letters/digits/underscores only, no dots, no F_ prefix). E.g. `DM`, `AE`, `LB_CLIN`. Calculate rows with bind::oc:external=clinicaldata leave this blank. |
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
7. **bind::oc:itemgroup** is a SHORT GROUP CODE only (letters, digits,
   underscores, no dots, no F_ prefix). E.g. `DM`, `AE`, `LB_CLIN`.
   Calculate rows with `bind::oc:external=clinicaldata` MUST leave this
   field blank.
8. **Choice filter column** in choices sheet must match the column name
   referenced in the survey's `choice_filter` cell

---

## File Naming Convention

Output files must be named to match the form_id for easy identification:
`{form_id}.xlsx` (e.g., `AE.xlsx`, `VS.xlsx`, `DOV.xlsx`).
form_id is a plain short uppercase name (NO F_ prefix). OpenClinica
adds any internal prefix itself when the form is uploaded.

Exception: forms sharing the same form_id but different designs
(e.g., IE_TRT and IE_CTL both have form_id=IE) — use the variant
identifier as the filename: `IE_TRT.xlsx`, `IE_CTL.xlsx`


---

## Universal Clinical Data Rules (OC-7)

These patterns apply across nearly every clinical form. Always include
them when the relevant fields are present, even if the protocol doesn't
explicitly request them.

### Paired start/end dates (1A, 1B, 1C)

When a form has a paired `*STDAT` / `*ENDAT` pair for the same
logical item (medical condition, AE, medication, etc.):

**End date field:**
- `constraint`: `. >= ${XXSTDAT} and . <= today()`
- `constraint_message`: "End date must be on or after start date and
  cannot be in the future."
- `relevant` (when the form has a matching `*ONGO` field): `${XXONGO}='N'`

**Start date field:**
- `constraint`: `. <= today()` (unless it's a scheduled/planned date)
- `constraint_message`: "Date cannot be in the future."

**Examples from reference builds:**
- `AEENDAT` constraint: `. <= today() and . >= ${AESTDAT}`
- `AEENDAT` relevant: `${AEONGO}='N'`
- `CMENDAT` constraint: `. >= ${CMSTDAT}`

### Sequential dates cascade (1D)

When a form has three or more dates that must occur in chronological
order (e.g. event date → awareness date → report date → IRB date):
each successive date's `constraint` should be `. >= ${PREVIOUS_DATE}`.

### BMI calculation (2A)

When a form captures both weight (kg) and height (cm), add:

    type:        calculate
    name:        BMI
    label:       BMI (kg/m²)
    calculation: ${WEIGHT_FIELD} div (${HEIGHT_FIELD} * ${HEIGHT_FIELD} div 10000)
    readonly:    yes

### AE severity / serious logic (2F)

On AE forms, if fields `AESEV` (grade) and `AESER` (serious?) both exist:
- `AESER` constraint: `(${AESEV}!='5') or (.='Y')`
- `AESER` constraint_message: "Grade 5 AE must be classified as serious."
- Any SAE-detail group should have `relevant`: `${AESER}='Y'`

### Eligibility criteria fixed-value constraints (2G)

On the IE (Inclusion/Exclusion) form:
- Each **inclusion** criterion: `constraint` = `. = 'Y'`
- Each **exclusion** criterion: `constraint` = `. = 'N'`
- `constraint_message`: "Subject is ineligible if this criterion is
  not met." / "...if this exclusion criterion is present."

### Physiological range sanity checks (2H)

Apply when the form captures these fields. Use soft constraints unless
the protocol demands hard (e.g. for eligibility).

| Field   | Constraint                                 | Units        |
|---------|--------------------------------------------|--------------|
| SYSBP   | `. >= 60 and . <= 250`                     | mmHg         |
| DIABP   | `. >= 30 and . <= 150 and . < ${SYSBP}`    | mmHg         |
| PULSE   | `. >= 30 and . <= 200`                     | bpm          |
| RESP    | `. >= 5 and . <= 60`                       | breaths/min  |
| TEMP    | `. >= 34 and . <= 42`                      | °C           |
| HEIGHT  | `. >= 100 and . <= 250`                    | cm           |
| WEIGHT  | `. >= 20 and . <= 300`                     | kg           |
| SpO2    | `. >= 0 and . <= 100`                      | %            |
| ECG HR  | `. >= 30 and . <= 200`                     | bpm          |

Narrow these bounds when protocol inclusion criteria are stricter
(e.g. protocol requires age 18-65 → `AGE` constraint
`. >= 18 and . <= 65`).

### RACE multi-select exclusion (2I)

When RACE is a `select_multiple` with Unknown (UNK) and Not Reported
(NOT_REP) options:

- `constraint`: `not(selected(.,'UNK') and count-selected(.) > 1) and
  not(selected(.,'NOT_REP') and count-selected(.) > 1)`
- `constraint_message`: "Cannot select Unknown or Not Reported with
  other options."

### Optional — dose and duration calculations (2J, 2K)

**Dose calculation** (when dose/kg and weight are both captured):

    type:        calculate
    calculation: ${DOSE_MG_KG} * ${WEIGHT}
    label:       Calculated dose (mg)
    readonly:    yes

**Duration in days** (when start and end dates are captured):

    type:        calculate
    calculation: (decimal-date-time(${ENDDAT}) - decimal-date-time(${STDAT})) div 86400
    relevant:    ${ONGO}='N'
    readonly:    yes


---

## Cross-form, Relevance, and Branching Patterns (OC-7 L–P)

### Cross-form value fetch (CF pattern) — 7L

To read a value from another form into the current form, add a
`calculate` row at the TOP of the current form's survey, named
with the `_CF` suffix:

    type:               calculate
    name:               SEX_CF   (or AGE_CF, WEIGHT_CF, ICFDAT_CF, etc.)
    calculation:        instance('clinicaldata')/ODM/ClinicalData/SubjectData/
                        StudyEventData[@StudyEventOID='SE_<X>']/
                        FormData[@FormOID='<SOURCE_FORM>']/
                        ItemGroupData[@ItemGroupOID='<SOURCE_FORM>.<GROUP>']/
                        ItemData[@ItemOID='<SOURCE_FORM>.<FIELD>']/@Value
    bind::oc:external:  clinicaldata
    bind::oc:itemgroup: (BLANK — external lookup rows must not have itemgroup)

### Sex-dependent fields via SEX_CF — 7M

Any form with sex-specific fields (pregnancy tests, menstrual history,
prostate/breast exams, PSA) must:

1. Add `SEX_CF` fetch from the DM form at top of form (per 7L)
2. Each sex-specific field: `relevant: ${SEX_CF}='F'` (or `='M'`)

### Consent-date floor for event dates — 7N

An event cannot predate informed consent. On every form OTHER THAN
the ICF form itself:

1. Add `ICFDAT_CF` fetch from the ICF form at top of form (per 7L)
2. Every event date field's constraint: AND in `. >= ${ICFDAT_CF}`
   Example: `. <= today() and . >= ${ICFDAT_CF}`
3. Update `constraint_message`: "Date must be on or after informed
   consent date and cannot be in the future."

### Universal relevance patterns — 7O

Apply whenever the structural condition is present.

| Pattern | Relevant expression | Use case |
|---------|---------------------|----------|
| Yes-branch | `${GATE}='Y'` | AE detail block when AEYN='Y' |
| No-branch | `${XXONGO}='N'` | End date visible when not ongoing |
| Other follow-up (single) | `${FIELD}='OTHER'` | Free-text specify field |
| Other follow-up (multi) | `selected(${FIELD},'OTHER')` | RACEOTH when RACE has OTHER |
| Multi-select value | `selected(${FIELD},'VALUE')` | Show when value chosen |
| Multi-select NOT | `not(selected(${FIELD},'NONE'))` | Show when anything but NONE |
| Timepoint-specific | `${TPTCALC}='Screening'` | Field only at screening |
| Timepoint NOT | `${TPTCALC}!='Screening'` | Field at all visits except screening |
| First-repeat-only | `${REPKEY_ID}=1` | YN gate only on first AE/CM/MH entry |
| Out-of-range warning | `${FIELD} < LOW or ${FIELD} > HIGH` | Flag abnormal values |

### Universal conditional branching patterns — 7P

Use these building blocks; do not invent new XPath constructs.

| Pattern | Expression | Example |
|---------|------------|---------|
| AND-chained | `${A}='Y' and ${B}='Y'` | `${SEX_CF}='F' and ${TPTCALC}='Screening' and ${FSH_REQD}='Y'` |
| OR-branched | `${A}='Y' or ${B}='Y'` | `${AEYN}='Y' or ${AEYN_CF}='Y'` (current OR propagated) |
| Cross-form | `${FIELD_CF}='value'` | `${SEX_CF}='F'` |
| Derived-flag | `${CALC}=value` | `${BMI} < 18 or ${BMI} > 40` |
| Negated | `not(selected(${FIELD},'NONE'))` or `${FLAG}!='value'` | Show when not a specific choice |
| End-state | `${DSDECOD}='<reason>'` | Different follow-up per DS termination reason |

### DS form end-state branching example

The disposition (DS) form typically has different follow-up depending
on termination reason. The `DSDECOD` field drives visibility:

| DSDECOD value       | Follow-up field visible                     |
|---------------------|---------------------------------------------|
| `ADVERSE_EVENT`     | AE reference (link to AE repeat key)        |
| `WITHDREW_CONSENT`  | Withdrawal date                             |
| `LOST_TO_FOLLOWUP`  | Last contact date, attempts made            |
| `DEATH`             | Death date, cause of death                  |
| `OTHER`             | Specify free-text field                     |

Each follow-up field uses `relevant: ${DSDECOD}='<value>'`.


---

## Element-Type Column Restrictions (OC-5a)

OpenClinica rejects or silently hides elements that have columns
incompatible with their type. The table below is exhaustive.

| Element type | Allowed columns | Forbidden columns |
|---|---|---|
| `text`, `integer`, `decimal`, `date`, `time`, `dateTime`, `select_one`, `select_multiple` | all columns | — |
| `calculate` (local) | `name`, `label`, `appearance`, `relevant`, `calculation`, `bind::oc:itemgroup` | `readonly`, `constraint`, `required` |
| `calculate` + `bind::oc:external=clinicaldata` | `name`, `calculation`, `bind::oc:external` | `bind::oc:itemgroup`, `readonly`, `constraint`, `required`, `label` (usually) |
| `note` | `name`, `label`, `appearance`, `relevant` | `bind::oc:itemgroup`, `required`, `constraint` |
| `begin group` / `end group` / `begin repeat` / `end repeat` | `type`, `name`, `appearance`, `relevant` (on begin only), `bind::oc:itemgroup` (optional) | — |

### Common errors and fixes

| Error message | Cause | Fix |
|---|---|---|
| "cannot be defined as type = calculate and readonly" | `readonly=yes` on `type=calculate` | Remove `readonly` column |
| "cannot be defined as type = calculate and have a constraint" | `constraint` on `type=calculate` | Remove constraint; add a `note` with `relevant=${CALC}<min or ${CALC}>max` instead (OC-7 7O-g pattern) |
| "Read-only note element X cannot have a value in column bind::oc:itemgroup" | `bind::oc:itemgroup` on `type=note` | Blank the itemgroup cell on the note row |

## Display-Only Calculated Fields (OC-5b)

When a computed value needs to be **visible** to the data-entry user (not
just used internally by other expressions), use a `text` element with
`calculation` + `readonly=yes` rather than `type=calculate`. OpenClinica
treats `type=calculate` as internal-only and does not display it to users.

**Correct** — visible display of a calculated ID:

```
type:        text
name:        CMSPID
label:       CM ID:
calculation: ${CMID_CALC}
readonly:    yes
appearance:  w1
bind::oc:itemgroup: CM
```

**Incorrect** — would be invisible:

```
type:        calculate
name:        CMSPID
calculation: ${CMID_CALC}
```

Rule of thumb: `type=calculate` for values consumed by other expressions;
`type=text` + `calculation` + `readonly=yes` for values the user must see.

## Repeating-Form Structural Pattern (OC-8)

OpenClinica uses a NON-STANDARD XLSForm structure for repeating forms.

The structural shape of every repeating form:

1. External calcs at top (EVENT_CF, repeat-key, ICFDAT_CF, etc.)
2. Local display/helper calcs
3. First-entry YN gate: `relevant: ${REPKEY_ID}=1`
4. `begin group` wrapping all data fields: `relevant: ${YN}='Y'`
5. Data fields inside the group
6. `end group` closing the data group
7. Three closing rows:

```
type=begin repeat   name=<form_id>   bind::oc:itemgroup=<group>
type=end group                       bind::oc:itemgroup=<group>   ← REQUIRED
type=end repeat                      bind::oc:itemgroup=<group>
```

The inner `end group` between `begin repeat` and `end repeat` is REQUIRED
even though there's no matching `begin group` inside the repeat block.
Without it, the XLSForm uploads successfully but OC fails to activate the
version — the form stays at "Please select default version for data entry"
and no data entry is possible.

### Additional rules for repeating forms

- **Do NOT include a top-level SUBJID text row.** OC uses its built-in
  subject context for repeating forms.
- **First-entry YN gate** uses `relevant: ${REPKEY_ID}=1` (OC-7 7O-f).
- **Data group is gated** by `relevant: ${YN}='Y'` so data fields only
  appear once the user has confirmed there is something to capture.


---

## Common Visit for Cross-Visit Forms (OC-9)

Every study includes a single repeating, non-scheduled event called
**Common Visit** with OID `SE_COMMON`. This event is available *after*
the enrollment/randomization event and allows coordinators to log
cross-visit events at any time during the trial.

### Forms that live ONLY on SE_COMMON

| Form | Purpose |
|---|---|
| `AE` | Adverse Events |
| `CM` | Concomitant Medications |
| `DV` | Protocol Deviations |
| `AESAE` | Serious Adverse Event Report |

These forms are never attached to a scheduled visit. Coordinators add
new entries to SE_COMMON as events occur.

### Study Spec JSON shape

Events list must include:

```json
{
  "event_oid":       "SE_COMMON",
  "event_title":     "Common Visit",
  "event_type":      "common",
  "is_repeating":    true,
  "available_after": "<enrollment event oid>"
}
```

Each affected form in the forms list:

```json
{
  "form_id":         "AE",
  "visits_assigned": ["SE_COMMON"]
}
```

### Why

AEs, CMs, deviations, and SAEs can occur at any time during a trial.
Attaching them to every scheduled visit creates duplication and confuses
the data model. A single Common Visit gives a single place to log these
cross-visit events and matches OpenClinica's native "common event"
pattern.

### Skipping SE_COMMON

If the protocol does not mention adverse event collection, concomitant
medications, deviations, or serious adverse events, skip the
corresponding form entirely — do NOT emit `AE` etc. with empty content.
SE_COMMON itself only exists when at least one of the four forms is in
scope.

---

## Begin/End Tag Pairing Rules (CRITICAL)

- `begin_repeat` MUST always be closed by `end_repeat`. NEVER by `end_group`.
- `begin_group` MUST always be closed by `end_group`. NEVER by `end_repeat`.
- The build script maintains a tag stack and asserts it is balanced at
  form completion. Any mismatch is a hard error caught at build time.
- All generated XLSForms are validated with ODK Validate before ZIP.
- Self-correction loop: up to 3 re-generation attempts on validation failure.

## What OC's form-service rejects (empirical — CRS-135, May 2026)

- Mismatched begin/end tags: OC returns HTTP 200 on upload but never
  creates a version object in minimongo. Symptom looks like propagation
  lag but the version never appears. Root cause: form rejected server-side.
- These errors are NOT caught by pyxform `validate=False`. Use ODK Validate.
