# DVS Column Mapping Reference

**Template:** `references/DVS_Template.xlsx`
**Purpose:** Exact column definitions, valid values, XLSForm→DVS mapping
rules, and write-back rules. Claude reads this before processing any input.

---

## DVS_OC4 — All 30 Columns

| # | Column | Valid Values / Notes |
|---|--------|----------------------|
| 1 | Check ID | `DVS-001`, `DVS-002`, ... Sequential, stable, unique |
| 2 | Status | `Draft` / `In Review` / `Approved` / `Built` / `Retired` |
| 3 | Check Name | Short plain-English name. Max ~60 chars. No technical notation. |
| 4 | Business Purpose | Why this check exists; what data quality issue it prevents |
| 5 | Protocol Reference | Section/table from protocol (e.g. `Sec 5.1`, `Table 2`) |
| 6 | Source Section | Protocol section category (e.g. `Inclusion Criteria`, `Schedule of Assessments`) |
| 7 | Check Type | See Check_Type values below |
| 8 | Severity | `Hard` / `Soft` / `Informational` / `Manual Review` / `N/A` |
| 9 | Trigger Point | `Real-time on form entry` / `On item completion` / `On form review` / `Post-entry review` |
| 10 | Event Scope | `Single event` / `Cross-event` / `Study-level` |
| 11 | Source Event OID(s) | Comma-separated event OIDs from which data is read |
| 12 | Current Event Needed? | `Yes` / `No` — whether @OpenClinica:Current='Yes' is used |
| 13 | crossform_references | Value to put in XLSForm settings crossform_references (event OID or `current_event`) |
| 14 | Target Form OID | The form_id of the form containing the target item |
| 15 | Target Item Name | The human-readable label of the item being validated |
| 16 | Target Item OID | The `name` field value of the target item in the XLSForm survey |
| 17 | Source Form OID(s) | Form(s) providing cross-form data (blank for same-form checks) |
| 18 | Source Item Name(s) | Human-readable labels of source items |
| 19 | Source Item OID(s) | `name` field values of source items |
| 20 | Helper Calculate Item Needed? | `Yes` / `No` |
| 21 | Helper Item OID | `name` of the helper calculate field if needed |
| 22 | OC4 Logic Pattern | Free text — see patterns below |
| 23 | Expression / Calculation | The exact XLSForm expression: constraint / relevant / calculation / required expression |
| 24 | Constraint / Required / Relevant Message | Plain-language message shown to user. Must match XLSForm constraint_message. |
| 25 | Query Text ID | `QT-001`, `QT-002`, ... Links to Query_Text_Library |
| 26 | Expected Site Action | Plain English: what the site user should do when this fires |
| 27 | Build Owner | Person responsible for building this check |
| 28 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 29 | UAT Case ID(s) | Comma-separated UAT-IDs that test this check |
| 30 | Notes | Any additional build or review notes |

---

## Check_Type Valid Values

| Value | When to use |
|-------|-------------|
| `Constraint` | Field-level validation expression in the `constraint` column |
| `Required` | Field-level required expression (`required = yes` or conditional) |
| `Relevant` | Show/hide logic in the `relevant` column |
| `Calculate + Constraint` | Derived value (calculate) plus a constraint on the result |
| `Cross-Form Helper` | Helper calculate field reading from `instance('clinicaldata')` |
| `Derivation / Review Listing` | Calculate field that derives a value (no constraint) |
| `Manual Review Only` | No automated check; human review only |

---

## OC4 Logic Pattern Values

| Pattern | Description |
|---------|-------------|
| `Same-form constraint` | constraint on a field using only same-form references |
| `Same-form required` | required expression using same-form references |
| `Conditional display` | relevant expression to show/hide a field or group |
| `Cross-form helper + constraint` | helper calculate pulls cross-form data; constraint uses helper |
| `Cross-form helper calculate` | helper calculate only, no constraint |
| `Calculated derivation` | calculate field deriving a value from same-form items |
| `Required field` | simple `required = yes` |

---

## Protocol_Extraction — All 15 Columns

| # | Column | Notes |
|---|--------|-------|
| 1 | Source Section | Protocol section category |
| 2 | Protocol Reference | Section/table number |
| 3 | Category | `Eligibility` / `Visit Timing` / `Data Completeness` / `Adverse Events` / `Conditional Logic` / `Cross-Form Validation` / `Lab Values` / `Safety` |
| 4 | Structured Requirement / Fact | Plain-English statement of what the protocol requires |
| 5 | Raw Protocol Text Summary | Brief paraphrase of the original protocol wording |
| 6 | Downstream Build Object | `Item` / `Group / Section` / `Form` / `Event` / `Query text` / `UAT test` |
| 7 | Potential Check Needed? | `Yes` / `No` / `Maybe` |
| 8 | Candidate Check ID | DVS Check ID |
| 9 | Related Event OID | Event OID where the check applies |
| 10 | Related Form OID | Form OID |
| 11 | Related Item Name / OID | `{label} / {name}` |
| 12 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 13 | Owner | Person responsible |
| 14 | Status | `Draft` / `In Review` / `Approved` |
| 15 | Notes | Build notes or open questions |

---

## Query_Text_Library — All 10 Columns

| # | Column | Notes |
|---|--------|-------|
| 1 | Query Text ID | `QT-001`, `QT-002`, ... |
| 2 | Status | `Draft` / `In Review` / `Approved` |
| 3 | Standard Message | Plain-language message shown to the user |
| 4 | Audience | `Site` / `Review` / `Monitor` |
| 5 | When to Use | Context in which this message is appropriate |
| 6 | Avoid / Notes | Anti-patterns or wording to avoid |
| 7 | Related Check ID(s) | Comma-separated DVS Check IDs |
| 8 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 9 | Owner | Person responsible |
| 10 | Version Notes | Change history or generation note |

---

## UAT_Cases — All 16 Columns

| # | Column | Notes |
|---|--------|-------|
| 1 | UAT Case ID | `UAT-001`, `UAT-002`, ... |
| 2 | Status | `Not Run` / `Pass` / `Fail` / `Blocked` / `In Progress` |
| 3 | Related Check ID | DVS Check ID |
| 4 | Scenario | Short description of the test scenario |
| 5 | Preconditions | What must be true before the test runs |
| 6 | Test Steps | Numbered steps to execute |
| 7 | Input Data | Specific data values to enter |
| 8 | Expected Result | What should happen |
| 9 | Actual Result | Filled in during testing |
| 10 | Test Result | `Not Run` / `Pass` / `Fail` / `Blocked` |
| 11 | Tester | Person who ran the test |
| 12 | Execution Date | Date test was run |
| 13 | Defect / Ticket | Bug/ticket reference if failed |
| 14 | Retest Needed? | `Yes` / `No` |
| 15 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 16 | Notes | Any additional context |

---

## XLSForm → DVS Check Derivation Map

This table defines which XLSForm survey row conditions produce which DVS check.

| XLSForm condition | DVS Check Type | Severity rule | Priority |
|---|---|---|---|
| `constraint` is populated | `Constraint` | Hard if `bind::oc:constraint-type = hard`; else Soft | Critical (Hard) / High (Soft) |
| `required = yes` without conditional | `Required` | Soft (informational to builder) | Medium |
| `required = <expression>` | `Required` | Hard if `bind::oc:required-type = hard`; else Soft | High |
| `relevant` is populated on data field | `Relevant` | Informational | Low |
| `calculate` with `bind::oc:external = clinicaldata` | `Cross-Form Helper` | N/A | Medium |
| `calculate` without clinicaldata external | `Derivation / Review Listing` | N/A | Low |

**Rows to skip (do not generate a DVS check):**
- `begin group`, `end group`, `begin repeat`, `end repeat` rows
- `pulldata()` calculate rows (TPTCALC, lab range lookups)
- `note` rows
- `EVENT_CF` calculate row
- Any row where type, name, and label are all empty

---

## DVS → XLSForm Write-Back Map

When an updated DVS is provided, apply these changes to XLSForms:

| DVS_OC4 column changed | XLSForm column updated | Conditions |
|---|---|---|
| Expression / Calculation | `constraint` | Check Type = Constraint |
| Expression / Calculation | `required` | Check Type = Required |
| Expression / Calculation | `relevant` | Check Type = Relevant |
| Expression / Calculation | `calculation` | Check Type = Derivation or Cross-Form Helper |
| Constraint / Required / Relevant Message | `constraint_message` | Check Type = Constraint |
| Constraint / Required / Relevant Message | `required_message` | Check Type = Required |
| Severity changed to Hard | `bind::oc:constraint-type` = `hard` | Check Type = Constraint |
| Severity changed from Hard to Soft | clear `bind::oc:constraint-type` | Check Type = Constraint |
| Status = Retired | clear Expression column; clear Message | Any type |

**Columns write-back must NEVER modify:**
`type`, `name`, `label`, `bind::oc:itemgroup`, `bind::oc:external`,
`appearance`, `readonly`, `hint`, `repeat_count`, `image`

---

## ID Numbering and Cross-Linking

**Check IDs (`DVS-NNN`):**
- Sequential across the entire study (not per form)
- Ordered: form order from INDEX sheet, then row order within each form's survey
- Stable: once assigned, never renumbered

**Query Text IDs (`QT-NNN`):**
- Sequential across all checks
- De-duplicated: identical message strings share one QT-ID
- Multiple Check IDs can reference the same QT-ID

**UAT Case IDs (`UAT-NNN`):**
- Sequential
- Hard checks get 2 UAT cases (pass + fail)
- Soft/Informational checks get 1 UAT case
- Cross-Form Helper checks get 1 UAT case (verify helper populates)

---

## Priority Assignment

| Condition | Priority |
|-----------|----------|
| Hard constraint | `Critical` |
| Soft constraint | `High` |
| Required field | `High` |
| Conditional required (expression) | `High` |
| Cross-form helper | `Medium` |
| Relevant / show-hide | `Low` |
| Derivation / calculate | `Low` |
| Manual review only | `Medium` |

---

## Detected DVS vs. Fresh XLSForm: How to Tell Them Apart

| Indicator | DVS xlsx | XLSForm xlsx |
|-----------|----------|--------------|
| Sheet named `DVS_OC4` | ✓ Present | ✗ Absent |
| Sheet named `survey` | ✗ Absent | ✓ Present |
| Sheet named `settings` with form_id | ✗ Absent | ✓ Present |
| Sheet named `README` | ✓ Present | ✗ Absent |

Always check sheet names first to determine input mode before any processing.
