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
| Form       | `F_`   | `F_DEMO`, `F_VS`, `F_ICF`            |
| Form Ver.  | `F_…_N`| `F_DEMO_1`                           |
| Item Group | `IG_`  | `IG_DEMO_DM` (pattern `IG_<FORM>_<GRP>`) |
| Item       | `I_`   | `I_DEMO_SUBJID` (pattern `I_<FORM>_<FIELD>`) |

### Dotted notation for XLSForm content

Inside XLSForm files the item group reference uses dotted notation:

- `bind::oc:itemgroup` column value: `F_<FORM>.<GROUP>` — e.g. `F_DEMO.DM`
- Cross-form ItemOID reference: `F_<FORM>.<FIELD>` — e.g. `F_DEMO.SUBJID`

The form_id used in the settings sheet is the `F_<NAME>` form — e.g.
`F_DEMO`, not `F02_DEMO` and not `DEMO` bare.

### Form naming rules

- CDASH forms — use the CDASH domain code with `F_` prefix:
  `F_DM`, `F_VS`, `F_LB`, `F_AE`, `F_EX`, `F_IE`, `F_MH`, `F_CM`, `F_DS`,
  `F_PE`, `F_PC`.
- When multiple forms share a domain, use a short suffix: `F_EX` for
  study drug, `F_EXVAL` for valacyclovir. Do not use numeric suffixes.
- Non-CDASH forms — short descriptive uppercase names with `F_` prefix:
  `F_ICF`, `F_DIS`, `F_BIOSP`, `F_RT`, `F_PREG`, `F_ECOG`, `F_EN`, `F_PSA`.

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
form_id:     F_<NAME>  (with F_ prefix — e.g. F_DEMO, F_VS)
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
| bind::oc:itemgroup | Item group reference in dotted notation `F_<FORM>.<GROUP>` (e.g. `F_DEMO.DM`, `F_AE.AE_GROUP`) | OpenClinica-specific |
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

- `bind::oc:itemgroup` uses dotted notation `F_<FORM>.<GROUP>` — e.g.
  `F_DEMO.DM`, `F_AE.AE_GROUP`. The group portion typically matches the
  CDASH domain code or a descriptive group name. This must be consistent
  with the form_id (also `F_`-prefixed) for data to map correctly in the
  study database.
- `once()` function used on ID calculations to prevent overwriting on edit
- Site-filtering on choice lists uses `choice_filter` with `site_filter`
  column containing site OID patterns like `S_SITENAME(TEST)`
- `trigger` column used with `SUBMTSAF` pattern for safety reporting timestamps
- Forms with `crossform_references: current_event` load faster in OpenClinica
  for same-event cross-checks
