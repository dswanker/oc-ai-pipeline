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

## UAT_Cases — All 25 Columns

Columns 1–16 are human-authored test specification. Columns 17–25 are
**ODM load coordinates** — machine-readable fields used by `uat_loader.py`
to build and POST the ODM XML that seeds participant data into OC4 Test.
Columns 17–18 are stamped at runtime by the loader (blank in the DVS);
columns 19–25 are populated by the DVS skill from XLSForm metadata.

| # | Column | Notes |
|---|--------|-------|
| 1 | UAT Case ID | `UAT-001`, `UAT-002`, ... |
| 2 | Status | `Not Run` / `Pass` / `Fail` / `Blocked` / `In Progress` |
| 3 | Related Check ID | DVS Check ID |
| 4 | Scenario | Short description of the test scenario |
| 5 | Preconditions | What must be true before the test runs |
| 6 | Test Steps | Numbered steps to execute |
| 7 | Input Data | Specific data values to enter (human-readable; pass-scenario value is also in Load_Value col 25) |
| 8 | Expected Result | What should happen |
| 9 | Actual Result | Filled in during testing |
| 10 | Test Result | `Not Run` / `Pass` / `Fail` / `Blocked` |
| 11 | Tester | Person who ran the test |
| 12 | Execution Date | Date test was run |
| 13 | Defect / Ticket | Bug/ticket reference if failed |
| 14 | Retest Needed? | `Yes` / `No` |
| 15 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 16 | Notes | Any additional context |
| 17 | Site_OID | **RUNTIME POPULATED — blank in DVS.** Stamped by `uat_loader.py` with the OID of the dated UAT site created for this run (e.g. `SS_UAT-20260520-124530`). |
| 18 | Participant_Key | **RUNTIME POPULATED — blank in DVS.** Stamped by `uat_loader.py` with the run-scoped participant key (e.g. `UAT-20260520-124530-P001`). Derived from Participant_ID (col 23) at run time. |
| 19 | Study_Event_OID | Event OID where this item's data loads. Populated by DVS skill from `forms[].visits_assigned[0]` in Study Spec JSON, or from `crossform_references` in XLSForm settings. If a form is assigned to multiple events, use the first; flag as PLACEHOLDER if ambiguous. |
| 20 | Event_Repeat_Key | Ordinal of the event instance. `1` for non-repeating events. `2`, `3`, ... for subsequent instances of repeating events. Populated by DVS skill; defaults to `1`. |
| 21 | Form_OID | Form OID where the item lives. Populated from `form_id` in XLSForm settings sheet. |
| 22 | Item_Group_OID | ItemGroup OID of the row. Populated from `bind::oc:itemgroup` on the survey row. |
| 23 | Participant_ID | Logical participant identifier for this test case, e.g. `UAT-P001`. All rows sharing the same Participant_ID load into the same OC4 participant. Assigned by the DVS skill based on dependency chain grouping: rows testing cross-form checks that depend on the same baseline data share one Participant_ID. A second participant (UAT-P002) is only used when a different baseline state is required (e.g. a female participant for sex-conditional logic). |
| 24 | Load_Order | Integer sequence controlling the order ODM data is posted. Lower numbers load first. DM and ICF baseline forms always load first (order 1–10). Clinical forms load in visit order after that. Within a single form, rows load in survey row order. Cross-form dependencies are enforced by ensuring the source form's rows carry a lower Load_Order than dependent rows. |
| 25 | Load_Value | Machine-readable value to load for this item's pass scenario. Derived from the `Input Data` column (col 7) pass-scenario value, normalised to the item's data type. Dates in ISO format (`2025-01-15`). Select_one values as the choice `name` (not label). Numbers as plain numeric strings. Leave blank for calculate and readonly fields. |

### UAT_Cases ODM Column Population Rules

**Populating col 19 (Study_Event_OID):**
- Primary source: `forms[].visits_assigned` array in Study Spec JSON, index 0
- Fallback: `crossform_references` field in XLSForm settings
- If form appears in multiple events and row is an infrastructure field
  (EVENT_CF, TPTCALC, etc.), leave blank — these fields do not load as data
- Flag as `[PLACEHOLDER — multi-event form, confirm target visit]` if
  the form appears in more than one event and the row is a data field

**Populating col 22 (Item_Group_OID):**
- Use the `bind::oc:itemgroup` value from the survey row verbatim
- For begin group / end group rows, leave blank (not loadable data)
- For calculate rows, leave blank (not loadable data)

**Populating col 23 (Participant_ID):**
- Default: `UAT-P001` for all rows
- Assign `UAT-P002` only when the test case explicitly requires a different
  baseline participant state (documented in the Preconditions column)
- Cross-form dependency chains: all rows in a chain that tests one logical
  check must share the same Participant_ID
- Hard checks with a fail scenario: fail scenario rows use the same
  Participant_ID as the pass scenario rows for that check

**Populating col 24 (Load_Order):**
- Groups: DM/ICF = 1–10, IE/MH = 11–20, VS/PE/LB = 21–50,
  AE/CM/EX = 51–80, DS/safety = 81–99
- Within a form, use the survey row order (first data row = group_base + 1)
- Cross-form dependencies: source form rows must have Load_Order < dependent rows
- If the same item appears in multiple UAT cases for the same participant,
  use the same Load_Order (data is loaded once, tested by multiple cases)

**Rows to leave blank in all ODM columns (cols 17–25):**
- `begin group`, `end group`, `begin repeat`, `end repeat` rows
- `calculate` rows without `bind::oc:external = clinicaldata`
- `pulldata()` calculate rows (TPTCALC, lab ranges)
- `note` rows
- `EVENT_CF` calculate row
- `readonly` rows (calculated/derived fields populated by OC4 automatically)

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

---

## UAT_Setup — Information Tab (read-only reference, no data entry)

This tab is a human-readable reference that explains the automated UAT
data loading system. It contains no data rows — only explanatory text
formatted as a two-column table (Topic / Description). The DVS skill
writes this tab on every Mode A run. The content is fixed per the
template below.

| Topic | Description |
|-------|-------------|
| **Purpose** | This tab documents how the automated UAT data loader works. It is a reference for anyone running or troubleshooting UAT. |
| **Site Naming** | A new site is created in the OC4 Test environment on every UAT run. Site Name: `UAT Automation Site - YYYY-MM-DD HH:MM`. Site ID (OID): `UAT-YYYYMMDD-HHMMSS`. This timestamp is derived from the moment the pipeline triggers. Creating a new site per run prevents data contamination between test runs and creates a visible audit trail in OC4's site list. |
| **Finding a Test Run** | In OC4, navigate to the study in the Test environment and open the Sites list. Each UAT run appears as a dated site. The most recent run is the site with the latest timestamp. |
| **Participant Naming** | Logical participant IDs in this DVS are `UAT-P001`, `UAT-P002`, etc. At runtime the loader maps these to run-scoped keys: `UAT-YYYYMMDD-HHMMSS-P001`. This ensures participants from different runs never collide. |
| **Cross-Form Test Cases** | All data for a single test case — including baseline data from prerequisite forms — must load into the same participant. For example, if an AE form checks consent date from DM, the DM data and AE data for that test both load for UAT-P001. A second participant (UAT-P002) is only used when the test scenario requires a genuinely different baseline state (e.g. different sex). |
| **Load Order** | Data loads in the order defined by the Load_Order column. DM and ICF always load first to establish the baseline participant record. Clinical forms follow in visit order. The loader respects cross-form dependencies: source form data always loads before forms that reference it. |
| **Runtime Columns** | Two columns in UAT_Cases are blank in this DVS and are stamped by the loader at runtime: `Site_OID` (the OID of the dated site created for this run) and `Participant_Key` (the run-scoped full participant key). These are written back to the DVS Results file uploaded to monday.com after the run. |
| **If a Run Fails** | If the loader fails partway through, a partial site and participants may exist in OC4 Test. These can be left in place — they are harmless, clearly dated, and do not affect subsequent runs. The next run creates a new dated site. Do not attempt to delete partial sites manually unless storage is a concern. |
| **Calendaring Tests** | Calendaring rule tests (on the Calendaring_UAT tab) are manual steps only. The loader does not execute calendaring UAT. See the Calendaring_UAT tab for step-by-step instructions to verify each rule. |

---

## Calendaring_Rules — All 20 Columns

Documents the OC4 calendaring rules derived from the protocol. The JSON
Output column contains the complete rule JSON ready to paste into the OC4
Rules Management UI.

| # | Column | Notes |
|---|--------|-------|
| 1 | Rule ID | `CAL-001`, `CAL-002`, ... Sequential. |
| 2 | Status | `Draft` / `In Review` / `Approved` / `Built` / `Retired` |
| 3 | Rule Name | Short plain-English name (≤60 chars). Becomes the `name` field in the JSON. |
| 4 | Business Purpose | Why this rule exists; what clinical workflow it automates. |
| 5 | Protocol Reference | Section/table from protocol (e.g. `Sec 6.2`). |
| 6 | Trigger Type | One of: `FORM_CHANGE` / `USER_CLOSES_FORM` / `EVENT_STATUS_CHANGE` / `EVENT_START_DATE_CHANGED` / `PARTICIPANT_CREATED` / `PARTICIPANT_STATUS_CHANGED` / `PARTICIPANT_EPOCH_CHANGED` / `RUN_ON_SCHEDULE`. Use `USER_CLOSES_FORM` for rules that react to a data entry completion. Avoid null/wildcard triggers. |
| 7 | Trigger OID | Event OID or Form OID that scopes the trigger. Blank = wildcard (applies to all events/forms of that type). Example: `F_VITALS` limits a FORM_CHANGE trigger to the Vitals form only. |
| 8 | Schedule | For `RUN_ON_SCHEDULE` only: `DAILY`. Leave blank for all trigger-based rules. |
| 9 | Schedule Time | For `RUN_ON_SCHEDULE` only: 24-hour time the rule runs, e.g. `09:00:00`. Respected on the hour. Leave blank for trigger-based rules. |
| 10 | Condition (XPath) | Full XPath condition expression that evaluates to true/false. Use `$TRUE` for unconditional rules. Use OC4 helper tokens: `${SE_OID}`, `${F_OID}`, `${IG_OID}`, `${I_OID}`. Never abbreviate. |
| 11 | Condition (Plain English) | Human-readable description of what the condition checks (e.g. "Screening event is completed"). |
| 12 | Action Type | `EVENT_ACTION` / `FORM_ACTION` / `NOTIFICATION_ACTION` / `PARTICIPANT_ACTION`. One row per action; if a rule has multiple actions, use one row per action and repeat the Rule ID. |
| 13 | Target Event OID | For EVENT_ACTION and FORM_ACTION: the event where the action applies. Accepts ordinal notation: `SE_TREATMENT[2]` or `SE_TREATMENT[${EVENT_TRIGGER_REPEAT_KEY}]`. |
| 14 | Target Form OID | For FORM_ACTION only: the form to act on. |
| 15 | Action Parameters | JSON blob of action-specific fields. See OC4_Syntax_Guide sheet for full schema. Examples: `{"startDateRelativeDays": 28, "relativeEventOid": "SE_BASELINE"}` for scheduling; `{"requiredExpression": "$TRUE"}` for form required; `{"toEmailAddress": "$participant", "emailSubject": "Visit reminder"}` for notification. |
| 16 | Rule Result To Trigger On | `true` / `false`. Almost always `true`. Set to `false` only when the action should fire when the condition is NOT met. |
| 17 | JSON Output | **Complete rule JSON** ready to paste into OC4 Rules Management. Populated by DVS skill. Validated against the error conditions in `Internal_Calendaring_Documentation.md`. |
| 18 | Build Owner | Person responsible for entering this rule into OC4. |
| 19 | Priority | `Critical` / `High` / `Medium` / `Low` |
| 20 | UAT Case ID(s) | Comma-separated CUAT-IDs from the Calendaring_UAT sheet that test this rule. |

---

## Calendaring_UAT — All 15 Columns

Manual test cases for OC4 calendaring rules. These cases are executed by
a human in the OC4 Test environment after UAT data has been loaded. The
automated loader does NOT execute these — they require human interaction
to trigger the rule (e.g. closing a form, changing a participant status)
and then observation of the outcome.

| # | Column | Notes |
|---|--------|-------|
| 1 | UAT Case ID | `CUAT-001`, `CUAT-002`, ... Sequential, separate series from `UAT-NNN`. |
| 2 | Status | `Not Run` / `Pass` / `Fail` / `Blocked` |
| 3 | Related Rule ID | CAL-NNN from Calendaring_Rules sheet |
| 4 | Scenario | Short description (e.g. "Baseline completed — Visit 2 auto-schedules") |
| 5 | Preconditions | Participant state required before triggering the rule (e.g. "UAT participant with DM and DOV data loaded. Baseline event in Data Entry Started status.") |
| 6 | Setup Steps | Manual steps to reach the precondition state in OC4 Test. Numbered. References the dated UAT site from the most recent data load run. |
| 7 | Trigger Action | The specific user action that fires the rule (e.g. "Click Close on the DOV form for the Baseline event"). |
| 8 | Expected Outcome | What OC4 should do after the rule fires (e.g. "Visit 2 event appears in participant timeline scheduled 28 days after Baseline start date"). |
| 9 | Verification Steps | How to confirm the expected outcome in the OC4 UI. Numbered. |
| 10 | Actual Result | Filled in during testing — describe what actually happened. |
| 11 | Test Result | `Not Run` / `Pass` / `Fail` |
| 12 | Tester | Person who executed the test |
| 13 | Execution Date | Date test was run |
| 14 | Defect / Ticket | Bug/ticket reference if failed |
| 15 | Notes | Any additional context, timing observations, or re-test notes |

---

## Detected DVS vs. Fresh XLSForm: How to Tell Them Apart

| Indicator | DVS xlsx | XLSForm xlsx |
|-----------|----------|--------------|
| Sheet named `DVS_OC4` | ✓ Present | ✗ Absent |
| Sheet named `survey` | ✗ Absent | ✓ Present |
| Sheet named `settings` with form_id | ✗ Absent | ✓ Present |
| Sheet named `README` | ✓ Present | ✗ Absent |

Always check sheet names first to determine input mode before any processing.
