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
  ✓ All forms[].form_id values use F_ prefix (no F## numeric prefix)
  ✓ All forms[].visits_assigned use SE_ prefix
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
