"""
prompts.py — Claude prompts for oc-ai-pipeline

JSON extraction prompts (used with call_claude — no skills, no code execution):
  EDC_STRUCTURE_PROMPT     — protocol PDF → Study Spec JSON
  PRICING_SUMMARY_PROMPT   — Study Spec JSON → Protocol Summary JSON
  DVS_TRANSLATE_PROMPT     — DVS changes + XLSForms → updated XLSForms JSON
  SPEC_FROM_BUILD_PROMPT   — built XLSForms → Study Spec JSON

File generation prompts (used with run_skill — Skills API + code execution):
  GENERATE_STUDY_SPEC_PROMPT       — Study Spec JSON → PDF + XLSX
  GENERATE_PROTOCOL_SUMMARY_PROMPT — Protocol Summary JSON → PDF
  PRICING_QUOTE_PROMPT             — Protocol Summary JSON → Quote PDFs + XLSXs
  EDC_BUILD_PROMPT                 — Study Spec JSON → EDC Build ZIP
  DVS_PROMPT                       — build info → DVS XLSX
"""

# ── JSON extraction prompts (call_claude only, no skill) ──────────────────────

EDC_STRUCTURE_PROMPT = """\
You are running the protocol-analysis skill.

Read the attached clinical trial protocol PDF and produce a complete Study
Specification following your skill instructions (Steps 1-8).

OUTPUT FORMAT — READ CAREFULLY:
  ✓ Your ENTIRE response must be a single valid JSON object.
  ✓ Start the response with `{` and end it with `}`.
  ✓ No explanation before or after the JSON.
  ✓ No markdown code fences (no ```json or ```).
  ✓ No reasoning or commentary anywhere in the output — not even inside
    the JSON as string values. Keep all string values concise and factual.
  ✓ The object's top-level keys MUST include: study_meta, timepoint_csv,
    labranges_csv, forms, review_flags.
  ✗ Do NOT output multiple JSON fragments.
  ✗ Do NOT output an example/stub object first and then the real one.
  ✗ Do NOT truncate — if you approach the token limit, shorten string
    values (especially survey row labels and flag_reason text) rather
    than omitting required structure keys.

════════════════════════════════════════════════════════════════════════════
OPENCLINICA OID NAMING CONVENTIONS  (CRITICAL)
════════════════════════════════════════════════════════════════════════════

Every identifier in the JSON you produce MUST follow the OpenClinica OID
naming conventions documented in "Locating Object Identifiers in a Study":

  Object          Prefix   Example
  ─────────────────────────────────────────────────────────
  Study           S_       S_PrTK05
  Site            S_       S_SITENAME(TEST)
  Event           SE_      SE_SCREENING, SE_BASELINE_INJECTION_1
  Form            F_       F_DEMO, F_VS, F_LB, F_ICF
  Form Version    F_*_N    F_DEMO_1
  Item Group      IG_      IG_DEMO_DM   (pattern: IG_<FORM>_<GROUP>)
  Item            I_       I_DEMO_SUBJID (pattern: I_<FORM>_<FIELD>)

DOTTED NOTATION for cross-form references in XLSForms:
  The `bind::oc:itemgroup` column and cross_form_dependencies use DOTTED
  notation: `<FORM_OID>.<GROUP>` for item groups and `<FORM_OID>.<FIELD>`
  for items. Example: `F_DEMO.DM` (item group), `F_DEMO.SUBJID` (item).

APPLY THIS TO ALL IDENTIFIERS:

  • timepoint_csv.rows[].event      → "SE_SCREENING", NOT "SCREENING"
  • forms[].form_id                 → "F_DEMO", NOT "F02_DEMO" or "DEMO"
    (no numeric prefix like F##_; just F_<UPPERCASE_NAME>)
  • forms[].settings.form_id        → same as forms[].form_id
  • forms[].visits_assigned         → ["SE_SCREENING","SE_WEEK_1", ...]
  • forms[].survey[].bind__oc_itemgroup  → "F_DEMO.DM" (dotted)
  • forms[].survey[].name           → use the BARE field name here
    (e.g. "SUBJID", "AETERM") — the xlsform tool constructs the full
    Item OID `I_<FORM>_<NAME>` at build time.

CROSS-FORM DEPENDENCIES — full XPath expressions:
  For each cross_form_dependencies entry you MUST also provide an
  `xpath_expression` field with the full OpenClinica XPath. Two patterns:

  Cross-event (data from a different event):
    instance('clinicaldata')/ODM/ClinicalData/SubjectData/
      StudyEventData[@StudyEventOID='SE_X']/
      FormData[@FormOID='F_Y']/
      ItemGroupData[@ItemGroupOID='F_Y.Z']/
      ItemData[@ItemOID='F_Y.FIELD']/@Value

  Same-event (from current event):
    instance('clinicaldata')/ODM/ClinicalData/SubjectData/
      StudyEventData[@OpenClinica:CurrentStudyEvent='true']/
      FormData[@FormOID='F_Y']/
      ItemGroupData/ItemData[@ItemOID='F_Y.FIELD']/@Value

  The xpath_expression may be a compact single-line string. Whitespace in
  the template above is for readability only.

FORM NAMING RULES for form_id:
  CDASH forms — use the CDASH domain code: F_DM, F_VS, F_LB, F_AE, F_EX,
  F_IE, F_MH, F_CM, F_DS, F_PE, F_PC.
  When you need multiple forms in the same domain, add a short suffix:
  F_EX (study drug), F_EXVAL (valacyclovir) — not F_EX_1/F_EX_2.
  Non-CDASH forms — use a descriptive uppercase short name: F_ICF, F_DIS,
  F_BIOSP, F_RT, F_PREG, F_ECOG, F_EN, F_PSA.

════════════════════════════════════════════════════════════════════════════
OPENCLINICA 4 AUTHORITATIVE RULES  (must follow to pass XLSForm upload)
════════════════════════════════════════════════════════════════════════════

The rules below are distilled from the OpenClinica 4 user documentation
(https://docs.openclinica.com/oc4/) and the official blank form template.
Following them is not optional — violations cause silent upload
failures in the Form Designer.

RULE OC-1 — VALIDATED FUNCTIONS ONLY
  Expressions in `relevant`, `constraint`, `calculation`, and `default`
  must use functions from the OpenClinica Validated Functions Index
  (OC4 docs §2.4.6.1). Common safe functions:
    . (self)              today()             now()
    selected()            count-selected()    string-length()
    regex()               coalesce()          substr()
    date()                decimal-date-time() format-date()
    int()                 number()            round()
    if()                  once()              position(..)
    concat()              upper-case()        lower-case()
  Avoid non-validated XPath functions (e.g. fn:… namespace, custom
  Enketo-only functions). When unsure, prefer a simpler expression.

RULE OC-2 — ITEMGROUP IS MANDATORY ON EVERY DATA ROW
  Every survey row whose `type` is a data type (text, integer, decimal,
  date, time, dateTime, select_one, select_multiple, note, calculate)
  MUST have `bind__oc_itemgroup` populated with a short group code —
  letters, digits, and underscores only, must not start with a digit,
  MUST NOT contain a period/dot.
  Correct:   "IC", "DM", "AE", "CM", "MH", "VS", "LB_CLIN"
  Incorrect: "F_ICF.IC" (contains dot — OC rejects),
             "F_DM" (F_ prefix belongs on form_id, not itemgroup)
  Rows with type `begin group` / `end group` / `begin repeat` /
  `end repeat` do NOT need this field.
  EXCEPTION (per RULE OC-5a below): calculate rows with
  `bind__oc_external: "clinicaldata"` (external lookups) MUST NOT have
  an itemgroup. They read from elsewhere and do not persist locally.
  Forms uploaded with invalid itemgroups are silently rejected.

RULE OC-3 — SETTINGS FIELDS REQUIRED
  The settings sheet needs these six cells populated (per OC4 docs
  §2.4.4 Using the Form Template):
    form_title       — human-readable name
    form_id          — form OID starting with F_
    version          — integer, start at 1
    style            — always "theme-grid"
    crossform_references — blank, comma-separated Event OIDs, or "current_event"
    namespaces       — EXACTLY:
                       oc="http://openclinica.org/xforms" , OpenClinica="http://openclinica.com/odm"
  These are produced in `settings` (object with those six keys) per form.

RULE OC-4 — CROSS-FORM/CROSS-EVENT XPATH PATTERNS
  When a field needs a value from another form or event, use these
  exact patterns in `calculation` (not made-up XPath):
    Same event, same form:
      instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/FormData[@FormOID='F_X']/ItemGroupData/ItemData[@ItemOID='F_X.FIELD']/@Value
    Different event (by OID):
      instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_X']/FormData[@FormOID='F_X']/ItemGroupData[@ItemGroupOID='F_X.GROUP']/ItemData[@ItemOID='F_X.FIELD']/@Value
    Current event OID:
      instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']/@StudyEventOID
    Timepoint lookup from the study_id_tpt.csv:
      pulldata('<study_id>_tpt','timepoint','event',${EVENT_CF})
  Fields using these also need `bind__oc_external: "clinicaldata"` (or
  the CSV name for `pulldata`). Put referenced event OIDs into
  `settings.crossform_references` (comma-separated) for performance.

RULE OC-5 — REPEATING GROUPS USE once() FOR THE KEY
  Forms with repeating groups (AE, CM, MH, DV, PR, and any custom
  repeating form) must include a `calculate` field at the top of the
  repeating group whose calculation uses `once(... @ItemGroupRepeatKey)`.
  This prevents the repeat key from being overwritten on edit. Example:
    once(instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@StudyEventOID='SE_X']/FormData[@FormOID='F_X']/ItemGroupData[@ItemGroupOID='F_X.AE']/@ItemGroupRepeatKey)
  Pair with a separate display-only field using
    if(${ID}!='', ${ID}, 'Scheduled')
  to show the repeat number during data entry.

RULE OC-6 — HARD EDIT CHECKS (when required)
  By default, constraint failures are soft (warning only). To make a
  constraint a hard rejection, add the column `bind::oc:constraint-type`
  with value "hard" on that row. Same for hard-required fields:
  `bind::oc:required-type` = "hard". In JSON output use the underscore
  forms: `bind__oc_constraint_type` and `bind__oc_required_type`.

RULE OC-7 — UNIVERSAL CLINICAL DATA PATTERNS (always apply when applicable)
  These patterns are standard in clinical EDC design. Apply them whenever
  the form contains the relevant fields, regardless of whether the
  protocol explicitly calls for them.

  7A. PAIRED START/END DATES — end date must be on or after start date.
      When a form has a paired *STDAT / *ENDAT pair (MHSTDAT+MHENDAT,
      AESTDAT+AEENDAT, CMSTDAT+CMENDAT, etc.):
        End date `constraint`:  `. >= ${XXSTDAT} and . <= today()`
        End date `constraint_message`:
            "End date must be on or after start date and cannot be in
             the future."

  7B. START DATES NOT IN FUTURE.
      Every *STDAT / event-date field should have:
        `constraint`: `. <= today()`
        `constraint_message`: "Date cannot be in the future."
      Exceptions: scheduled/planned dates (e.g. next-visit date).

  7C. END DATE CONDITIONAL ON "ONGOING" STATUS.
      When a form has both *ENDAT and *ONGO fields for the same item:
        End date `relevant`: `${XXONGO}='N'`
      Reason: no end date if the condition/med/AE is ongoing.

  7D. SEQUENTIAL DATES CASCADE.
      When a form has three or more dates that must occur in order
      (e.g. event date → awareness date → report date → IRB date):
      Each subsequent date's `constraint`: `. >= ${PREVIOUS_DATE}`
      Chain them so each date >= the one before it.

  7E. BMI CALCULATION WHEN WEIGHT + HEIGHT PRESENT.
      If a form captures both weight (kg) and height (cm), add a
      calculate row immediately after:
        type:        calculate
        name:        BMI
        label:       "BMI (kg/m²)"
        calculation: `${WEIGHT_FIELD} div (${HEIGHT_FIELD} * ${HEIGHT_FIELD} div 10000)`
        readonly:    yes

  7F. AE SEVERITY/SERIOUS LOGIC.
      On AE forms, if fields AESEV (grade) and AESER (serious?) exist:
        AESER `constraint`: `(${AESEV}!='5') or (.='Y')`
        AESER `constraint_message`: "Grade 5 AE must be classified as
                                     serious."
      If a grouped block exists for SAE details, its `relevant` should be
      `${AESER}='Y'` so the SAE fields only show for serious AEs.

  7G. ELIGIBILITY CRITERIA — fixed-value constraints.
      On the IE (Inclusion/Exclusion) form, each inclusion criterion
      field should have:
        `constraint`: `. = 'Y'`
        `constraint_message`: "Subject is ineligible if this criterion
                              is not met."
      Each exclusion criterion field should have:
        `constraint`: `. = 'N'`
        `constraint_message`: "Subject is ineligible if this exclusion
                              criterion is present."

  7H. PHYSIOLOGICAL RANGE CONSTRAINTS (vital signs, labs, ECG).
      Apply standard sanity-check ranges when fields exist. These catch
      data-entry errors. Values outside these ranges almost always
      indicate a typo. Use hard constraints only if the protocol
      demands it; otherwise soft is fine.
        SYSBP:  `. >= 60 and . <= 250`   (mmHg)
        DIABP:  `. >= 30 and . <= 150 and . < ${SYSBP}`   (mmHg)
        PULSE:  `. >= 30 and . <= 200`   (bpm)
        RESP:   `. >= 5 and . <= 60`     (breaths/min)
        TEMP:   `. >= 34 and . <= 42`    (°C)
        HEIGHT: `. >= 100 and . <= 250`  (cm)
        WEIGHT: `. >= 20 and . <= 300`   (kg)
        SpO2:   `. >= 0 and . <= 100`    (%)
        ECG HR: `. >= 30 and . <= 200`   (bpm)
      Adjust based on protocol-specific inclusion criteria (e.g. if
      protocol says age 18-65, add those bounds to AGE).

  7I. RACE MULTI-SELECT EXCLUSION.
      When RACE is a select_multiple with Unknown (UNK) and Not Reported
      (NOT_REP) options:
        `constraint`:
          `not(selected(.,'UNK') and count-selected(.) > 1) and
           not(selected(.,'NOT_REP') and count-selected(.) > 1)`
        `constraint_message`: "Cannot select Unknown or Not Reported
                              with other options."

  7J. OPTIONAL — DOSE CALCULATION.
      When dose-per-weight and weight are both captured on the same
      form:
        type:        calculate
        calculation: `${DOSE_MG_KG} * ${WEIGHT}`
      Label it "Calculated dose (mg)" or similar.

  7K. OPTIONAL — DURATION IN DAYS.
      When start AND end date are captured and the data consumer may
      need duration, add a calculated duration field (days):
        type:        calculate
        calculation: `(decimal-date-time(${ENDDAT}) -
                      decimal-date-time(${STDAT})) div 86400`
        relevant:    `${ONGO}='N'`   (skip when ongoing)


  7L. CROSS-FORM VALUE FETCH (CF pattern).
      To read a value from another form into the current form, add a
      `calculate` row at the TOP of the current form's survey, named
      `<FIELD>_CF` (the "_CF" suffix is required convention).
        type:               calculate
        name:               SEX_CF (or AGE_CF, WEIGHT_CF, etc.)
        calculation:        instance('clinicaldata')/ODM/ClinicalData/
                            SubjectData/StudyEventData[@StudyEventOID='SE_<X>']/
                            FormData[@FormOID='F_<SOURCE_FORM>']/
                            ItemGroupData[@ItemGroupOID='F_<SOURCE_FORM>.<GROUP>']/
                            ItemData[@ItemOID='F_<SOURCE_FORM>.<FIELD>']/@Value
        bind::oc:external:  clinicaldata
      This row MUST NOT have bind::oc:itemgroup (per RULE OC-2).
      Use the fetched value via ${SEX_CF} in relevant/constraint/
      calculation of downstream fields.

  7M. SEX-DEPENDENT FIELDS REQUIRE SEX_CF CROSS-FETCH.
      Whenever a form has fields that only apply to one sex (pregnancy
      tests, menstrual history, prostate exams, breast exams, PSA, etc.):
        1. Add a SEX_CF fetch from F_DM at the top of the form
           (per pattern 7L).
        2. Each sex-specific field gets:
           `relevant`: `${SEX_CF}='F'`  (or `='M'`)

  7N. CONSENT DATE FLOOR FOR EVENT DATES.
      An event cannot predate informed consent. For every clinical event
      date on the form (*STDAT, VSDAT, LBDAT, etc. — NOT including dates
      ON the F_ICF form itself):
        1. Add an ICFDAT_CF fetch from F_ICF at the top of the form
           (per pattern 7L).
        2. Extend the date's existing constraint with `. >= ${ICFDAT_CF}`
           AND-joined with any other date rules.
           Example: `. <= today() and . >= ${ICFDAT_CF}`
        3. Update constraint_message:
           "Date must be on or after informed consent date and cannot
            be in the future."

  7O. UNIVERSAL RELEVANCE PATTERNS — apply whenever the structural
      condition is present:

      (a) YES-BRANCH: When a Yes/No gate field is 'Y', show follow-up
          detail fields.
          `relevant`: `${GATE}='Y'`
          Examples: AE detail block when ${AEYN}='Y'; SAE block when
          ${AESER}='Y'.

      (b) NO-BRANCH: Common with ongoing flags — when ongoing is 'N',
          show end-date.
          `relevant`: `${XXONGO}='N'`
          Also used for dose-not-given reasons, deviation notes, etc.

      (c) "OTHER" FOLLOW-UP: When a select_one has an 'OTHER' option,
          add a free-text field immediately after:
          `relevant`: `${FIELD}='OTHER'`
          For select_multiple: `relevant`: `selected(${FIELD},'OTHER')`

      (d) MULTI-SELECT CONDITIONAL: To show a field when a specific
          multi-select value is chosen (or not):
          `relevant`: `selected(${FIELD},'VALUE')`
          `relevant`: `not(selected(${FIELD},'NONE'))`

      (e) TIMEPOINT-SPECIFIC: Fields that only apply at certain visits
          require a TPTCALC row in the form (see RULE OC-4 for the
          pulldata pattern). Then:
          `relevant`: `${TPTCALC}='Screening'`
          `relevant`: `${TPTCALC}!='Screening'`

      (f) FIRST-ENTRY-ONLY IN REPEATING FORMS: In AE/CM/MH/DV-style
          repeating forms, the "any <X> observed?" YN gate only applies
          to the first repeat instance:
          `relevant`: `${REPKEY_ID}=1`
          (Where REPKEY_ID is the calculated display form of the
           repeat key, e.g. AEID, CMID, MHID.)

      (g) OUT-OF-RANGE WARNING NOTES: To show a note when a value is
          outside a normal range:
          `relevant`: `${FIELD} < LOW or ${FIELD} > HIGH`
          Example: BMI out-of-range note: `${BMI} < 18 or ${BMI} > 40`

  7P. UNIVERSAL CONDITIONAL BRANCHING PATTERNS — use these exact building
      blocks for compound logic. Do not invent novel XPath constructs.

      (a) AND-CHAINED (multi-level gate): `${A}='Y' and ${B}='Y'`
          Example: three-level gate
            `${SEX_CF}='F' and ${TPTCALC}='Screening' and ${FSH_REQD}='Y'`

      (b) OR-BRANCHED (alternative triggers): `${A}='Y' or ${B}='Y'`
          Example: AE block visible when current visit has AEs OR prior
          visit flagged AEs carried forward:
            `${AEYN}='Y' or ${AEYN_CF}='Y'`

      (c) CROSS-FORM (via _CF field): `${FIELD_CF}='value'`

      (d) DERIVED-FLAG (gate on a calculated value): `${CALC_FIELD}=value`
          Example: `${BMI} < 18 or ${BMI} > 40`

      (e) NEGATED: `not(selected(${FIELD},'NONE'))` or `${FLAG}!='value'`

      (f) END-STATE / TERMINATION (disposition branching): On the DS form,
          different follow-up fields appear based on termination reason:
            `relevant`: `${DSDECOD}='ADVERSE_EVENT'` → AE reference fields
            `relevant`: `${DSDECOD}='WITHDREW_CONSENT'` → withdrawal date
            `relevant`: `${DSDECOD}='OTHER'` → specify free-text field

When in doubt about any rule above, the OpenClinica 4 user documentation
at https://docs.openclinica.com/oc4/ is the source of truth —
especially §2.4.4 (Using the Form Template), §2.4.5 (Form Logic),
§2.4.6 (Functions), and §2.4.9 (Locating Object Identifiers).

────────────────────────────────────────────────────────────────────────────
REQUIRED TOP-LEVEL KEYS
────────────────────────────────────────────────────────────────────────────

study_meta:
  protocol_number          (str, e.g. "PrTK05")
  study_id                 (str — use protocol_number if no other identifier)
  study_title              (str, full title from protocol cover page)
  sponsor                  (str)
  study_phase              (str, e.g. "Phase 2a")
  indication               (str)
  therapeutic_area         (str, e.g. "Oncology")
  total_study_duration_months (int)
  type                     ("INTERVENTIONAL" | "OBSERVATIONAL")
  total_enrollment         (int)
  number_of_arms           (int)
  number_of_sites          (int or null)
  regions                  (str or null, e.g. "United States")
  start_date / end_date    (str or "—")
  arms                     (list of {arm_name, arm_code, planned_enrollment, description})
  customer_segment         ("COMMERCIAL" | "ACADEMIC" | "LOW_MARKET")
  input_mode               ("PROTOCOL_ONLY" | "PROTOCOL_PLUS_CRF")
  library_files_provided   (list of str, may be empty)

timepoint_csv:
  filename : "{protocol}_tpt.csv"
  rows     : list of {event, timepoint, visit_number, arm} — one row per
             scheduled visit per arm. `event` MUST use SE_ prefix
             (SE_SCREENING, SE_BASELINE_INJECTION_1, SE_WEEK_1, etc.)
             Cover SCREENING, BASELINE, every numbered visit, UNSCHEDULED,
             END_OF_TREATMENT, SAFETY_FOLLOWUP as applicable.

  CRITICAL — EVENT UNIQUENESS:
  Each `event` OID MUST appear in EXACTLY ONE row across the entire
  `rows` list (with one exception: per-arm rows, see below). When
  protocols contain multiple overlapping SOE tables — e.g. a detailed
  injection-by-injection schedule AND a summary weekly schedule that
  reference the same visit — emit the event ONCE using the most
  specific / most complete timepoint description. NEVER emit two rows
  with the same `event` value just because the protocol shows them in
  two different tables.

  Example of what NOT to do:
    {"event":"SE_SCREENING",    "timepoint":"Day -28 to Day 0"}
    {"event":"SE_SCREENING",    "timepoint":"Day -28 to Day 0"}  ← duplicate!
    {"event":"SE_WEEK_8_10",    "timepoint":"Week 8-10 (1 month post inj 3)"}
    {"event":"SE_WEEK_8_10",    "timepoint":"Week 8-10"}         ← duplicate!

  Example of what TO do (pick the more descriptive timepoint):
    {"event":"SE_SCREENING",    "timepoint":"Day -28 to Day 0"}
    {"event":"SE_WEEK_8_10",    "timepoint":"Week 8-10 (1 month post inj 3)"}

  PER-ARM EXCEPTION: if the protocol has multiple arms with DIFFERENT
  visit schedules for the same timepoint, you MAY emit one row per
  arm — but only when the `arm` field actually distinguishes them.
  Most single-arm studies should have exactly one row per event OID.

labranges_csv:  (REQUIRED — populate every lab test from the protocol)
  filename : "{protocol}_labranges.csv"
  columns  : ["test_code","test_name","lower","upper","unit","lab_name"]
  rows     : list of {test_code, test_name, lower, upper, unit, lab_name}
             - test_code: CDASH LBTESTCD (e.g. "HGB","WBC","ALT","CREAT")
             - test_name: full name (e.g. "Hemoglobin","Alanine Aminotransferase")
             - lower/upper/unit/lab_name: "[PLACEHOLDER]" until site values known
             Include EVERY lab test mentioned in the protocol's laboratory
             safety assessments section. Do not leave rows empty.

forms: list of CRF form objects. For EACH form include:

  form_id                  (str with F_ prefix, e.g. "F_DEMO","F_VS")
  form_title               (str, human-readable)
  form_category            ("ADMINISTRATIVE"|"CDASH_CLINICAL"|"CDASH_SAFETY"|"INFRASTRUCTURE"|"CUSTOM")
  cdash_domain             (str or null, e.g. "DM","VS","LB","AE")
  visits_assigned          (list of SE_-prefixed event names from timepoint_csv, or ["ALL_EVENTS"])
  has_repeating_group      (bool)
  is_epro                  (bool)
  arm_applicability        ("ALL" or specific arm_code)
  reuse_count              (int — number of events this form is used at)
  complexity               ("simple"|"average"|"complex")
  library_match            ({status, source_type, fields_from_library,
                             fields_extended_from_protocol, fields_from_cdash_default})
  settings                 ({form_title, form_id, version, style, namespaces})
  choices                  (list of {list_name, label, name, source})
  survey                   (list of survey rows — see below)
  cross_form_dependencies  (list — see below)

════════════════════════════════════════════════════════════════════════════
FORM → VISIT ASSIGNMENT RULES (critical — read carefully)
════════════════════════════════════════════════════════════════════════════

When populating `visits_assigned`, follow these rules. Getting these wrong
causes form cards to appear on the wrong events in the OpenClinica Study
Designer, which is a top source of post-build manual corrections.

RULE 1 — ENROLLMENT vs SCREENING
    F_EN (Enrollment) belongs at the BASELINE or FIRST DOSING visit, NOT at
    SCREENING. Screening is for evaluating eligibility (IC/EC criteria
    checks, baseline assessments). Enrollment is the moment the patient is
    formally registered into the study and begins study interventions —
    this is typically the same day as Dose 1 / Injection 1.

    Correct:
      F_EN → SE_BASELINE_INJECTION_1 (for treatment arm)
      F_EN → SE_BASELINE              (for control arm)

    Wrong (very common mistake):
      F_EN → SE_SCREENING

RULE 2 — PER-ARM VISIT COVERAGE
    When the protocol has separate SOA tables for different arms
    (e.g. Table 1 for Treatment, Table 2 for Control), forms that appear
    in BOTH tables MUST list visits from BOTH tables in `visits_assigned`.

    Example: if F_PSA is marked "X" at Screening/W2-3/W8-10/W16-18 in
    Table 2 AND at Screening/Inj 2/W8-10/EOS in Table 1, then:
      visits_assigned: ["SE_SCREENING", "SE_INJECTION_2", "SE_WEEK_8_10",
                         "SE_END_OF_STUDY", "SE_BASELINE", "SE_WEEK_2_3"]

    Do NOT emit a form with TRT-arm visits only when the SOA clearly
    mandates it on the CTRL arm too. Read BOTH tables before finalizing
    `visits_assigned` for each form.

RULE 3 — TIMING-DEFINED EVENTS (EBRT, infusions, continuous meds)
    Forms for procedures that begin at a specific point in the schedule
    (e.g., "Begins X days after Injection N") should be assigned to the
    FIRST event where the procedure actually begins, NOT to a late
    follow-up visit like End of Study.

    Example: EBRT "Begins 0-3 days after Injection #2"
      → F_RT should be assigned to SE_INJECTION_2 onward (e.g., INJ_2,
        TA_2, INJ_3, TA_3, any concurrent CTRL weeks), NOT just to
        SE_END_OF_STUDY.

RULE 4 — SCREENING-ONLY FORMS
    These forms belong ONLY at SE_SCREENING (or equivalent first visit):
      - F_ICF (Informed Consent)
      - F_DM  (Demographics — collected once)
      - F_MH  (Medical History — collected once)
      - F_IE  (Inclusion/Exclusion — eligibility is only assessed once)
      - F_DIS (Disease Assessment / Staging at baseline)
      - F_ECOG (if assessed only at baseline per protocol)

RULE 5 — CONTINUOUS / ONGOING FORMS
    Forms that capture data throughout the study should be at EVERY visit
    where the SOA shows an "X" — do NOT compress to a subset:
      - F_AE  (Adverse Events): every clinical visit PLUS SE_UNSCHEDULED
      - F_CM  (Concomitant Meds): every visit the SOA shows "X"
      - F_VS  (Vital Signs): every visit with an "X" in SOA
      - F_BIOSP (Biosamples): every visit in biosample rows of BOTH tables

RULE 6 — UNSCHEDULED AND END-OF-STUDY
    - SE_UNSCHEDULED should host: F_AE, F_AESAE (if applicable), F_CM,
      F_PREG (if applicable) — anything that might arise ad hoc.
    - SE_END_OF_STUDY should host: F_DS (Disposition) AT MINIMUM, plus
      any final-visit assessments marked in the SOA.

RULE 7 — ARM-SPECIFIC SAFETY FORMS
    If the protocol specifies different safety reporting for different
    arms (e.g. "For the control group, only SAEs related to biomarker
    collection procedure should be recorded"), use `arm_applicability`
    to mark the form's scope. Consider a separate form (e.g., F_AESAE)
    if the control-arm safety form has materially different fields.

════════════════════════════════════════════════════════════════════════════
SURVEY ROWS — REQUIRED FIELDS AND AGGRESSIVE POPULATION
════════════════════════════════════════════════════════════════════════════

Each survey row MUST include these keys (never omit, may be empty):
    type                   (e.g. "text","integer","date","select_one X","calculate","begin group","end group")
    name                   (bare field name, no prefix — e.g. "SUBJID", "AETERM")
    label                  (question text visible to the data entry user)
    completion_status      ("COMPLETE" | "FLAGGED" | "PLACEHOLDER")
    library_source         ("CDASH_DEFAULT" | "CDASH_STANDARD" | "PROTOCOL_SPECIFIC" | "CUSTOM")
    flag_reason            (str — empty "" if COMPLETE; explain why if FLAGGED/PLACEHOLDER)

POPULATE THESE OPTIONAL FIELDS AGGRESSIVELY — err toward inclusion:

    bind__oc_itemgroup   — REQUIRED on every data row (not group rows).
                           Use dotted form "F_<FORM>.<GROUP>" for example
                           "F_DEMO.DM", "F_VS.VIT", "F_AE.AE_GROUP".
                           When the form has only one group, reuse the
                           form's CDASH domain code as the group name:
                           F_LB.LB, F_DM.DM, F_VS.VS.

    appearance           — Use OpenClinica/XLSForm values:
                           w1, w2, w3, w4, w5, w6, w9 — column widths (of 6)
                           horizontal, horizontal-compact — inline choices
                           minimal — dropdown instead of radio
                           multiline — multi-line text
                           field-list — single screen group layout
                           columns — choices in columns
                           Example inferences:
                             short text fields (SUBJID)          → "w2"
                             date (VSDAT)                        → "w2"
                             numeric with unit (TEMP, WEIGHT)    → "w2"
                             Yes/No select_one                   → "w2 horizontal"
                             long free-text (AE term, comments)  → "w6"
                             choice list from YN                 → "w2 horizontal"
                             severity/grade select with many items → "w3 minimal"

    relevant             — XPath/XForms expression gating when this field
                           appears. Populate whenever the protocol implies
                           conditionality, e.g.:
                             ${AEONGO}='N'         (show end date only if not ongoing)
                             ${PREG_REPORTED}='Y'  (show preg details only if reported)
                             ${TSTAGE}='OTHER'     (show TSTAGE_OTH if Other chosen)

    required             — Use "yes", "true()", or an XPath expression.
                           Populate whenever a field is clearly mandatory:
                             SUBJID on every form → "yes"
                             primary dates (VSDAT, AESTDAT, etc.) → "yes"
                             required efficacy/safety endpoints → "yes"

    constraint           — XPath validation. Populate whenever a protocol
                           rule implies a constraint:
                             date-not-future      → ". <= today()"
                             date-after-start     → ". >= ${START_DATE}"
                             integer-range        → ". >= 18 and . <= 100"
                             positive-decimal     → ". > 0"
                             blood-volume         → ". <= 42"
                             gleason-sum          → ". = ${GLEASON_PRIMARY} + ${GLEASON_SECONDARY}"
                             enum-restricted      → constraint on select_one limited choices

    constraint_message   — Plain-text error message when constraint fails.
                           Populate alongside every constraint.

    calculation          — XPath expression for auto-computed fields.
                           Populate whenever a value is derivable:
                             total score          → "${PRIMARY} + ${SECONDARY}"
                             age-from-DOB         → "floor((today() - ${BRTHDAT}) div 365.25)"
                             cross-form pulldata  → "pulldata('prtk05_tpt','timepoint','event',${EVENT_CF})"
                             cross-form instance  → full XPath as shown in
                                                     CROSS-FORM DEPENDENCIES above

    dependencies         — List of cross-form field references in dotted
                           notation: ["F_DEMO.SUBJID", "F_EX.EXSTDAT"].
                           Populate on every row that pulls data from
                           another form.

    readonly             — "yes" for calculated display fields.

    bind__oc_external    — "clinicaldata" for cross-form XPath calculations;
                           "labranges" for lab-range lookups; "{study}_tpt"
                           for timepoint lookups.

    bind__oc_briefdescription / bind__oc_description — Short/long descriptions
                           for sponsor reporting. Populate when the protocol
                           provides a definition or context beyond the label.

completion_status rules:
    COMPLETE     — field is fully specified and can be built as-is
    FLAGGED      — field is specified but needs reviewer confirmation
                   (e.g. ambiguous protocol language, uncertain constraint)
    PLACEHOLDER  — field has [PLACEHOLDER] values that MUST be filled in
                   (e.g. site-specific lab values, unit strings, unknown codes)

Be generous with FLAGGED/PLACEHOLDER — aim to flag any field where a
human reviewer should confirm the mapping. Typical flag rate: 10-30%.

RATIONALE: Humans add rules and refine — it is far better to overfill
these columns and let humans strip out what doesn't apply than to leave
them blank. If the protocol suggests ANY reasonable rule, populate the
column and mark the row FLAGGED with a flag_reason explaining your
inference.

════════════════════════════════════════════════════════════════════════════
CROSS_FORM_DEPENDENCIES — full XPaths required
════════════════════════════════════════════════════════════════════════════

Each dependency records one field on this form that references another form:
    source_form            (str, F_-prefixed form_id of the OTHER form)
    source_field           (str, bare field name on source_form, e.g. "SUBJID")
    source_item_oid        (str, dotted form "F_<FORM>.<FIELD>", e.g. "F_DEMO.SUBJID")
    source_itemgroup_oid   (str, dotted form "F_<FORM>.<GROUP>", e.g. "F_DEMO.DM")
    source_event_oid       (str, SE_<EVENT> or "CURRENT" for same-event reference)
    target_field           (str, bare name of the field ON THIS FORM that
                            will receive the pulled value — must match the
                            `name` of one of this form's survey rows. If
                            the naming matches the source (e.g. SUBJID →
                            SUBJID), use the same value. Required so that
                            downstream tooling can wire the XPath into the
                            correct survey row's `calculation` column.)
    purpose                (str, why — e.g. "Randomization number from EN form")
    visit_context          (str, when — e.g. "All visits after Baseline")
    status                 ("FLAGGED — OID CONFIRMATION REQUIRED" typically)
    xpath_expression       (str, full XPath as specified in the OID CONVENTIONS
                            section above. Pick the cross-event or same-event
                            template as appropriate.)

ALSO: Duplicate the xpath_expression into the corresponding survey row's
`calculation` column (where target_field matches the row's `name`), and
set `bind__oc_external: clinicaldata` on that row. This is critical —
the survey row is what drives the actual XLSForm build; the
cross_form_dependencies array is the structured catalog for review.

Typical cross-form deps: F_DEMO.SUBJID pulled into every form;
F_EN.RANDNUM pulled into treatment forms; F_VS.WEIGHT pulled into F_LB
for creatinine clearance calc. Populate these wherever the protocol
implies cross-form data lookups.

────────────────────────────────────────────────────────────────────────────
review_flags: (ALL eight categories must be present, even if empty list)
  site_specific           : values that must be set per site (lab ranges, units, site codes)
  oid_confirmation        : fields whose OID path needs runtime confirmation
  protocol_ambiguous      : protocol language unclear / multiple interpretations
  constraint_review       : constraints inferred from protocol — need review
  choice_list_review      : choice lists built from protocol — need review
  custom_domain           : non-CDASH domains / custom forms
  pdf_mapping_uncertain   : fields where PDF CRF mapping was uncertain
  name_deviation          : field names that deviate from CDASH standard

────────────────────────────────────────────────────────────────────────────
QUALITY CHECKLIST (verify before returning)
────────────────────────────────────────────────────────────────────────────
  ✓ study_meta.total_enrollment > 0 and number_of_arms >= 1
  ✓ All timepoint_csv.rows[].event values use SE_ prefix
  ✓ timepoint_csv.rows[].event values are UNIQUE across rows
    (unless row per arm and `arm` field distinguishes them)
  ✓ All forms[].form_id values use F_ prefix (no F## numeric prefix)
  ✓ All forms[].visits_assigned use SE_ prefix
  ✓ F_EN (Enrollment) visits_assigned includes a BASELINE event — NOT just
    SE_SCREENING (see FORM → VISIT ASSIGNMENT RULES, Rule 1)
  ✓ For multi-arm studies with separate SOA tables, forms present in both
    tables have visits from BOTH tables in visits_assigned (Rule 2)
  ✓ Procedure forms (EBRT, infusions, continuous meds) are assigned to the
    FIRST event where the procedure begins, not a late follow-up (Rule 3)
  ✓ F_AE assigned to every clinical visit + SE_UNSCHEDULED (Rule 5)
  ✓ F_DS (Disposition) assigned to SE_END_OF_STUDY (Rule 6)
  ✓ All survey rows with non-group type have bind__oc_itemgroup populated
    with dotted F_<FORM>.<GROUP> form
  ✓ labranges_csv.rows has at least one entry per lab test in the protocol
  ✓ Every survey row has completion_status, library_source, flag_reason
  ✓ Optional survey columns (appearance, relevant, required, constraint,
    calculation, dependencies) are populated wherever the protocol
    provides reasonable grounds — err toward inclusion
  ✓ Every form has cross_form_dependencies list (may be empty [])
  ✓ Every cross_form_dependencies entry has xpath_expression populated
  ✓ review_flags has all 8 categories as lists (may be empty)
"""

PRICING_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill — Protocol Summary step.

The Study Specification JSON is provided below.

OUTPUT FORMAT — READ CAREFULLY:
  ✓ Your ENTIRE response must be a single valid JSON object.
  ✓ Start the response with `{` and end it with `}`.
  ✓ No explanation before or after the JSON.
  ✓ No markdown code fences (no ```json or ```).
  ✓ No reasoning or commentary anywhere in the output.

────────────────────────────────────────────────────────────────────────────
REQUIRED TOP-LEVEL KEYS  (all must be present)
────────────────────────────────────────────────────────────────────────────

study_meta:
  protocol_number          (str)
  study_id                 (str — use protocol_number if no other identifier)
  study_title              (str)
  sponsor                  (str)
  study_phase              (str)
  indication               (str)
  therapeutic_area         (str, e.g. "Oncology")
  total_study_duration_months (int)
  type                     ("INTERVENTIONAL" | "OBSERVATIONAL")
  total_enrollment         (int)
  number_of_arms           (int)
  number_of_sites          (int or null)
  regions                  (str or null)
  start_date / end_date    (str or null)
  customer_segment         ("COMMERCIAL" | "ACADEMIC" | "LOW_MARKET")
  input_mode               (str)

patient_population:
  indication               (str)
  sex                      (str — "MALE" | "FEMALE" | "BOTH")
  age_range                (str)
  key_inclusion            (list of str)
  key_exclusion            (list of str)
  total_enrollment         (int)    ← REQUIRED at this level too
  number_of_arms           (int)    ← REQUIRED at this level too
  arms: list of {
    name               : str
    arm_code           : str
    n                  : int  (planned enrollment — use key 'n', not 'planned_enrollment')
    description        : str
  }

visit_summary:
  arms: list of {
    name                : str   (matches the arm name from patient_population)
    visits_per_patient  : int
    patients            : int   (same as arm.n)
    total_visits        : int   (visits_per_patient × patients)
  }
  total_patient_visits_all_arms : int   (sum across all arms)
  unscheduled_included          : bool
  screening_window              : str
  treatment_duration            : str
  follow_up_duration            : str
  key_timepoints                : list of str (sample events across arms)

crf_summary:
  total_unique_crfs  : int
  simple_crfs        : int
  average_crfs       : int
  complex_crfs       : int
  total_reuse_crfs   : int   (how many forms are reused across multiple visits)
  crf_detail: list of {
    domain_name      : str (e.g. "Demographics", "Vital Signs")
    cdash_code       : str (e.g. "DM", "VS", or "" for custom)
    source           : "CDASH_STANDARD" | "PROTOCOL_SPECIFIC" | "CUSTOM"
    visits_used      : list of str (event names)
    complexity       : "simple" | "average" | "complex"
    reuse_count      : int
    confidence       : "HIGH" | "MEDIUM" | "LOW"
    notes            : str
  }

review_flags:
  site_specific_count, oid_confirmation_count, protocol_ambiguous_count,
  constraint_review_count, choice_list_review_count, custom_domain_count,
  pdf_mapping_uncertain_count, name_deviation_count, total_flags : int each
  critical_items : list of str (the most important items to address)

complexity_flags: dict with these keys
  overall_complexity   : "LOW" | "MEDIUM" | "HIGH"
  drivers              : list of str
  mitigating_factors   : list of str
  edc_build_estimate   : str (brief narrative of build effort)

modules_detected: dict mapping module category → list of form_ids
  Categories: safety, efficacy_disease, exposure_treatment, biomarker_pkpd,
  standard_safety_labs, enrollment_eligibility, concomitant, disposition,
  ecoa_epro, imaging, ecg, randomization, ivrs_irt, central_lab,
  drug_accountability

conditional_branching: list of {
  description       : str (what the conditional logic does)
  type              : "RELEVANT" | "REQUIRED" | "CONSTRAINT" | "CALCULATION" | "SKIP_LOGIC"
  affected_domains  : list of str (CDASH codes or form_ids)
  confidence        : "HIGH" | "MEDIUM" | "LOW"
  note              : str (additional context)
}
Infer these from the Study Spec forms[].survey rows where `relevant`,
`constraint`, or `calculation` columns are populated. Typical examples:
conditional items in IE based on arm, lab panels gated by eligibility
criteria, cross-form pulls for subject data.

data_cleaning_estimate:
  domains: list of {
    domain            : str (form title or domain name)
    cdash_code        : str (or "" for custom)
    complexity_rating : "LOW" | "MEDIUM" | "HIGH"
    implied_checks    : list of str (discrete DVS check descriptions)
  }
  disclaimer: str (default provided if omitted)

Populate a row per form (or per domain) with at minimum LOW/MEDIUM/HIGH
rating and 2-6 implied check descriptions. The DVS skill downstream will
use this to scope data validation work.
"""

DVS_TRANSLATE_PROMPT = """\
You are updating XLSForm files based on changes in a DVS XLSX.

The current XLSForm JSON and DVS changes are provided below.

Read the DVS changes and translate each into XLSForm field-level updates
(constraint, constraint_message, calculation, relevant columns).

Return a single, complete, valid JSON object — no text before or after it.

Rules:
- Keep all existing fields intact — only modify fields with DVS changes
- Preserve all original field names exactly
- Return ALL forms, not just modified ones
- Structure: {"forms": {"<filename>.xlsx": {"survey": [...], "choices": [...], "settings": {...}}}}
"""

SPEC_FROM_BUILD_PROMPT = """\
You are reverse-engineering a Study Specification from built XLSForm files.

The XLSForm JSON is provided below.

Produce an updated Study Specification JSON reflecting the actual built forms.
Return a single, complete, valid JSON object — no text before or after it.

Include: study_meta (preserve exactly), forms (from survey sheets),
codelists (from choices sheets), constraints (from constraint/relevant columns).
Do NOT include review_flags — those will be injected separately.

Preserve all field names and OIDs exactly. Do not invent or rename anything.
"""


# ── File generation prompts (used with run_skill) ─────────────────────────────

GENERATE_STUDY_SPEC_PROMPT = """\
You are running the protocol-analysis skill in file generation mode.

IMPORTANT: The Study Specification data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate TWO output files and save both to /mnt/user-data/outputs/:
  1. {protocol}_Study_Specification.pdf
  2. {protocol}_Study_Specification.xlsx
  (where {protocol} is the study_meta.protocol_number from the JSON)

Use these scripts from your scripts/ folder:
  from generate_study_spec_pdf  import build_study_spec_pdf
  from generate_study_spec_xlsx import build_study_spec_xlsx

Call each with (data_dict, output_path).

Study Specification JSON follows this line:
"""

GENERATE_PROTOCOL_SUMMARY_PROMPT = """\
You are running the protocol-analysis skill in Protocol Summary PDF mode.

IMPORTANT: The Protocol Summary data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate ONE output file and save to /mnt/user-data/outputs/:
  1. {protocol}_Protocol_Summary.pdf
  (where {protocol} is the study_meta.protocol_number from the JSON)

Use this script from your scripts/ folder:
  from generate_protocol_summary_pdf import build_protocol_summary_pdf

Call with (data_dict, output_path).

Protocol Summary JSON follows this line:
"""

PRICING_QUOTE_PROMPT = """\
You are running the pricing-quote skill.

IMPORTANT: The Protocol Summary data is provided as JSON at the end of
this message. DO NOT attempt to read it from any file. Parse the JSON
directly from the message content.

Task: Generate FOUR output files and save all to /mnt/user-data/outputs/:
  1. {protocol}_Quote_Internal.pdf
  2. {protocol}_Quote_Client.pdf
  3. {protocol}_Quote_Internal.xlsx
  4. {protocol}_Quote_Client.xlsx
  (where {protocol} is the study_meta.protocol_number from the JSON)

Steps:
  1. from pricing_engine      import calculate_quote
  2. from generate_quote_pdf  import build_quote_pdfs
  3. from generate_quote_xlsx import build_quote_xlsx
  4. quote = calculate_quote(protocol_summary_dict)
  5. build_quote_pdfs(quote, internal_pdf_path, client_pdf_path)
  6. build_quote_xlsx(quote, internal_xlsx_path, client_xlsx_path)

Protocol Summary JSON follows this line:
"""

EDC_BUILD_PROMPT = """\
You are running the edc-builder skill.

IMPORTANT: The Study Specification data is provided as JSON at the end of
this message. DO NOT attempt to read any XLSX or PDF file. Skip SKILL.md
Step 1 entirely. Parse the JSON directly from the message content into a
dict called spec_data.

Task: Build all XLSForms, generate CSVs and checklists, and package into
ONE output ZIP saved to /mnt/user-data/outputs/:
  {protocol}_EDC_Build.zip
  (where {protocol} is spec_data['study_meta']['protocol_number'])

Use the scripts from your scripts/ folder:

  import os, tempfile, shutil
  from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
  from build_checklist import build_checklist_pdf, build_checklist_xlsx
  from build_package   import build_package

  # build_log is a dict of list buckets — NOT an empty list
  build_log = {
      'forms_built':        [],
      'forms_skipped':      [],
      'build_errors':       [],
      'build_warnings':     [],
      'placeholder_applied': [],
      'oid_placeholders':   [],
  }

  with tempfile.TemporaryDirectory() as tmp:
      forms_dir     = os.path.join(tmp, 'forms')
      csv_dir       = os.path.join(tmp, 'csv')
      checklist_dir = os.path.join(tmp, 'checklist')
      package_dir   = os.path.join(tmp, 'package')
      for d in (forms_dir, csv_dir, checklist_dir, package_dir):
          os.makedirs(d, exist_ok=True)

      build_all_xlsforms(spec_data, forms_dir, build_log)
      write_timepoint_csv(spec_data.get('timepoint_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_tpt.csv'),
                          build_log)
      write_labranges_csv(spec_data.get('labranges_csv', {}),
                          os.path.join(csv_dir, f'{protocol}_labranges.csv'),
                          build_log)
      build_checklist_pdf(spec_data, build_log,
                          os.path.join(checklist_dir,
                                       f'{protocol}_Build_Checklist.pdf'))
      build_checklist_xlsx(spec_data, build_log,
                           os.path.join(checklist_dir,
                                        f'{protocol}_Build_Checklist.xlsx'))

      # build_package writes a date-stamped zip into package_dir and
      # returns its path. Copy it to the required outputs path.
      produced_zip = build_package(spec_data, build_log,
                                   forms_dir, csv_dir,
                                   checklist_dir, package_dir)
      shutil.copy(produced_zip,
                  f'/mnt/user-data/outputs/{protocol}_EDC_Build.zip')

Follow SKILL.md Steps 2 onwards for the logic details (Step 2: process forms,
Step 3: handle PLACEHOLDER fields, etc.).

Study Specification JSON follows this line:
"""

DVS_PROMPT = """\
You are running the dvs-specification skill in Mode A (generate DVS from
XLSForm data).

IMPORTANT: The input data is provided as JSON at the end of this message.
DO NOT attempt to read any ZIP or XLSForm file. Skip SKILL.md Step 1
entirely. Parse the JSON directly from the message content.

Input structure:
  {
    "study_meta": { protocol_number, ... },
    "forms": { "<filename>": { "survey": [ {constraint, calculation, ...} ] } }
  }

Task: Follow SKILL.md Steps 2-6 to build the dvs_data dict with keys:
  study_meta, protocol_extraction, dvs_oc4, query_text_library, uat_cases

Then call:
  from generate_dvs import build_dvs
  build_dvs(dvs_data, f'/mnt/user-data/outputs/{protocol}_DVS.xlsx')
  (where {protocol} is study_meta.protocol_number)

Input JSON follows this line:
"""

QUOTE_PDF_FROM_XLSX_PROMPT = """\
You are running the pricing-quote skill in PDF regeneration mode.

An edited Quote XLSX is attached as base64 data above.

Follow the pricing-quote SKILL.md instructions to read the XLSX and
regenerate the PDFs. Save both files to /mnt/user-data/outputs/:
  {protocol}_Quote_Internal.pdf
  {protocol}_Quote_Client.pdf
"""
