# XLSForm Patterns Reference — OpenClinica Build Standards

**Purpose:** Standard column definitions, field types, constraint patterns,
and OpenClinica-specific conventions observed in PrTK05 study builds.
Claude reads this before writing any survey rows.

---

## OpenClinica OID Naming Conventions

Per OpenClinica's "Locating Object Identifiers in a Study" reference, every
identifier in a study follows a strict prefix convention:

| Object     | Prefix | Example                              |
|------------|--------|--------------------------------------|
| Study      | `S_`   | `S_PrTK05`                           |
| Site       | `S_`   | `S_SITENAME(TEST)`                   |
| Event      | `SE_`  | `SE_SCREENING`, `SE_WEEK_1`          |
| Form       | (none) | `DEMO`, `VS`, `ICF` (plain short name — OC adds internal prefix) |
| Form Ver.  | `…_N`  | `DEMO_1` (OC adds internal prefix)    |
| Item Group | `IG_`  | `IG_DEMO_DM` (pattern `IG_<FORM>_<GRP>`) |
| Item       | `I_`   | `I_DEMO_SUBJID` (pattern `I_<FORM>_<FIELD>`) |
### Short-code itemgroup values inside the XLSForm

**IMPORTANT** — the `bind::oc:itemgroup` column takes a SHORT GROUP CODE
only. OpenClinica's XLSForm validator rejects any value that is not
composed of letters, digits, and underscores (and must not start with a
digit). Periods/dots are NOT allowed. NO F_ prefix anywhere —
neither in the itemgroup column nor in form_id.

- `bind::oc:itemgroup` column value: short group code only
    - Correct: `IC`, `DM`, `AE`, `CM`, `MH`, `VS`, `LB_CLIN`
    - Incorrect: `ICF.IC` (contains dot — OC rejects)
    - Incorrect: `F_ICF` or `F_DM` (NO F_ prefix in this column, and form_id
      itself should also not have F_ — OC adds any internal prefix itself)
- XPath references inside `calculation` / `relevant` expressions DO use
  OpenClinica's dotted OID form (e.g. `@FormOID='DM'`,
  `@ItemGroupOID='DM.DM'`, `@ItemOID='DM.SUBJID'`). The dotted form is
  only used inside XPath expressions — the column value itself stays as
  just the short group code.

**Calculate rows with external lookup:** rows where `type=calculate` AND
`bind::oc:external=clinicaldata` MUST leave `bind::oc:itemgroup` empty.
They pull from the ODM tree at runtime and do not persist locally.

The form_id used in the settings sheet is the PLAIN short name — e.g.
`DEMO`, `VS`, `ICF`. NO `F_` prefix. OpenClinica adds any internal prefix
itself during upload. We confirmed this by upload testing: forms with
`form_id='F_VS'` fail with the 'update the form failed' error, while
forms with `form_id='VS'` succeed.

### Form naming rules

- CDASH forms — use the CDASH domain code as-is (NO F_ prefix):
  `DM`, `VS`, `LB`, `AE`, `EX`, `IE`, `MH`, `CM`, `DS`,
  `PE`, `PC`.
- When multiple forms share a domain, use a short suffix: `EX` for
  study drug, `EXVAL` for valacyclovir. Do not use numeric suffixes.
- Non-CDASH forms — short descriptive uppercase names (NO F_ prefix):
  `ICF`, `DIS`, `BIOSP`, `RT`, `PREG`, `ECOG`, `EN`, `PSA`.

---

## XLSForm Sheet Structure

Every OpenClinica XLSForm has exactly 3 sheets:

| Sheet | Required Columns | Purpose |
|-------|-----------------|---------|
| settings | form_title, form_id, version, style, namespaces | Form metadata |
| choices | list_name, label, name | All code lists |
| survey | type, name, label + many optional columns | Form logic |

---

## Settings Sheet — Standard Values

```
form_title:  [Human readable name]
form_id:     <short uppercase name>  (no F_ prefix — e.g. DEMO, VS, ICF)
version:     1  (increment on updates)
style:       theme-grid
namespaces:  oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"
crossform_references: [blank, or "current_event" if referencing same-event data]
```

---

## Survey Sheet — Column Reference

| Column | Description | Notes |
|--------|-------------|-------|
| type | Field type (see types table below) | Required |
| name | Machine-readable field ID | Required. No spaces. Start with letter. |
| label | User-visible question text | Required. Supports HTML and ${field_ref} |
| bind::oc:itemgroup | Short group code only: letters, digits, underscores (no dots, no F_ prefix). E.g. `DM`, `AE`, `LB_CLIN` | OpenClinica-specific |
| hint | Helper text shown below label | Optional |
| appearance | Layout hint (w1–w9, horizontal, minimal, multiline, field-list) | Optional |
| bind::oc:briefdescription | Short description for reporting | Optional |
| bind::oc:description | Full description for reporting | Optional |
| relevant | Show/hide expression | XPath/XForms expression |
| required | Whether field is mandatory | yes / true() / expression |
| constraint | Validation rule | XPath/XForms expression |
| constraint_message | Error message shown when constraint fails | Plain text |
| calculation | Auto-calculated value | XPath/XForms expression |
| readonly | Prevent user editing | yes / true() |
| repeat_count | Number of repeat instances | Integer or expression |
| bind::oc:external | External data source declaration | clinicaldata / labranges / [study_id]_tpt |
| choice_filter | Filter choices based on expression | XPath expression |
| default | Default value | Value or expression |
| trigger | Trigger field for calculations | Field name |

---

## Field Types

| Type | Description | Example Use |
|------|-------------|-------------|
| text | Free text string | AE term, medication name |
| integer | Whole number | Age, counts, years |
| decimal | Decimal number | Weight, dose, lab results |
| date | Full date (YYYY-MM-DD) | Collection date, injection date |
| select_one [list] | Single choice from list | Yes/No, severity grade |
| select_multiple [list] | Multiple choices | Race, seriousness criteria |
| note | Display-only text | Headers, instructions |
| calculate | Hidden calculated field | Cross-form refs, derived values |
| begin group | Start a group section | Logical grouping |
| end group | End a group section | |
| begin repeat | Start a repeating group | AE table, medication table |
| end repeat | End a repeating group | |

---

## Standard Appearance Values

| Value | Effect |
|-------|--------|
| w1 | 1-column width (of 6) |
| w2 | 2-column width |
| w3 | 3-column width (half) |
| w4 | 4-column width |
| w5 | 5-column width |
| w6 | Full width (6 columns) |
| w9 | Extended width |
| horizontal | Display choices horizontally |
| horizontal-compact | Compact horizontal choices |
| minimal | Dropdown instead of radio buttons |
| multiline | Multi-line text input |
| field-list | Display group fields on one screen |
| columns | Display choices in columns |

---

## Standard Calculate Rows — Every Form

These rows appear at the top of every form's survey sheet:

```
1. EVENT_CF
   type: calculate
   name: EVENT_CF
   calculation: instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:CurrentStudyEvent='true']/@StudyEventOID
   bind::oc:external: clinicaldata

2. TPTCALC
   type: calculate
   name: TPTCALC
   bind::oc:itemgroup: [FORM_ID]
   calculation: pulldata('[STUDY_ID]_tpt','timepoint','event',${EVENT_CF})
```

---

## Repeating Group Pattern

For forms with repeating groups (AE, CM, MH, PR, DV):

```
1. calculate | [DOM]ID | once(instance('clinicaldata')/.../ItemData[@ItemOID='[FORM].[DOM]ID']/@Value) | external: clinicaldata
2. calculate | [DOM]ID_CALC | if(${[DOM]ID}!='',${[DOM]ID},'Scheduled')
3. calculate | [DOM]YN_CF | instance('clinicaldata')/.../ItemData[@ItemOID='[FORM].[DOM]YN']/@Value | external: clinicaldata
4. select_one NY | [DOM]YN | "Did the participant report any [domain]?" | relevant: ${[DOM]ID}=1 | required: yes
5. note | NO[DOM]NOTE | "** No [domain] reported..." | relevant: ${[DOM]ID}!=1 and ${[DOM]YN_CF}='N'
6. begin group | [DOM]1 | appearance: w6 | relevant: ${[DOM]YN}='Y' or ${[DOM]YN_CF}='Y'
   ... domain fields ...
7. end group
```

---

## Standard Constraint Patterns

| Use Case | Constraint Expression | Message |
|----------|-----------------------|---------|
| Not future date | `. <= today()` | Cannot be a future date. |
| After start date | `. >= ${START_DATE}` | Cannot be prior to start date. |
| Date range (visit window) | `. >= ${EXDAT_CALC} + 7 and . <= ${EXDAT_CALC} + 14 and . <= today()` | Must be 1–2 weeks post injection. |
| Age range | `. >= 18 and . <= 100` | Age must be within 18 to 100 years. |
| Time format (HH:MM) | `regex(., '([01][0-9]\|2[0-3]):[0-5][0-9]') and string-length(.) = 5` | Time must be in format HH:MM |
| Positive decimal | `. > 0` | Value must be positive. |
| Integer range | `. >= [min] and . <= [max]` | Value is outside expected range. |
| Required if not unknown | `${[FIELD]_UNK} = ''` | (use as required expression) |
| All items selected | `selected(${FIELD},'1') and selected(${FIELD},'2') and ...` | All criteria must be selected. |
| Cannot select NA with others | `not(selected(${FIELD},'NA') and count-selected(${FIELD}) > 1)` | Cannot select N/A with other options. |
| Inclusion criterion | `. = 'Y'` (constraint) | "No" selected — please confirm. |
| Exclusion criterion | `. = 'N'` (constraint) | "Yes" selected — please confirm. |
| Grade 5 = death | `(${AESEV} != '5') or selected(${AESERCRIT},'AESDTH')` | Select "Death" if severity is Grade 5. |

---

## Partial Date Pattern

Used for dates where day/month may be unknown (MH, CM start/end dates):

```
integer   | [prefix]_YEAR    | Year:  | appearance: w1
select_one MONTH | [prefix]_MON | Month: | appearance: w1 minimal
select_one DAY   | [prefix]_DAY | Day:   | appearance: w1 minimal  
select_multiple UNK | [prefix]_UNK | [blank/whitespace label] | appearance: w1

calculate | [prefix]     | concat(${[prefix]_YEAR},"-",if(...)) 
calculate | [prefix]_CALC| date(concat(...))
calculate | [prefix]_FDC | ${[prefix]_CALC} <= today()
calculate | [prefix]_BDC | ${[prefix]_CALC} >= date(today() - (${AGE_CF} * 365.25))
calculate | [prefix]_LEAP| if((${[prefix]_YEAR} mod 4 = 0 and ...), 1, 0)
calculate | [prefix]_M   | if(FDC='true' and BDC='true' and ..., '', 'error message')
```

---

## Cross-Form Reference Patterns

**Reference a field value from another event:**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='[EVENT_OID]']/FormData[@FormOID='[FORM_ID]']/ItemGroupData[@ItemGroupOID='[FORM_ID].[DOMAIN]']/ItemData[@ItemOID='[FORM_ID].[FIELD_NAME]']/@Value
```

**Reference a field from current event:**
```
instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:CurrentStudyEvent='true']/FormData[@FormOID='[FORM_ID]']/ItemGroupData/ItemData[@ItemOID='[FORM_ID].[FIELD_NAME]']/@Value
```

**pulldata from CSV:**
```
pulldata('[study_id]_tpt','timepoint','event',${EVENT_CF})
pulldata('labranges','lower','test_code','WBC')
```

Always declare external sources in `bind::oc:external`:
- `clinicaldata` — for cross-form clinical data references
- `labranges` — for lab reference ranges CSV
- `[study_id]_tpt` — for timepoint CSV

---

## Layout Conventions

- Most forms open with a `begin group` containing the timepoint display and
  a "Was [assessment] performed?" Yes/No question
- Detail fields go in a second group with `relevant: ${[PERF_FIELD]}='Y'`
- Use `field-list` appearance on the first group to display on one screen
- Spacer notes use `<span style="color:white">  </span>` as label
- Bold text in labels uses `**text**` markdown syntax
- Red warning text: `<span style="color:red; font-weight:bold;">text</span>`

---

## OpenClinica-Specific Notes

- `bind::oc:itemgroup` uses the short group code only (letters,
  digits, underscores — no dots, no F_ prefix). E.g. `DM`, `AE`, `LB_CLIN`.
  CDASH domain code or a descriptive group name. This must be consistent
  with the form_id (also `F_`-prefixed) for data to map correctly in the
  study database.
- `once()` function used on ID calculations to prevent overwriting on edit
- Site-filtering on choice lists uses `choice_filter` with `site_filter`
  column containing site OID patterns like `S_SITENAME(TEST)`
- `trigger` column used with `SUBMTSAF` pattern for safety reporting timestamps
- Forms with `crossform_references: current_event` load faster in OpenClinica
  for same-event cross-checks


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
