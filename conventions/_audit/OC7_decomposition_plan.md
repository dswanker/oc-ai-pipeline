# OC-7 Decomposition Plan

**Status:** Plan only. Decomposition deferred to Phase B.1c.
**Source:** `prompts.py` lines 266–474 (RULE OC-7 — UNIVERSAL CLINICAL DATA PATTERNS).
**Captured as umbrella:** `conventions/global/clinical_patterns.universal_clinical_data_patterns.json` (Phase B.1a, commit pending).
**Decision context:** B.1a Option 3 (umbrella now, decomposition tracked).

## Purpose

OC-7 in `prompts.py` is an umbrella rule covering 16 sub-patterns (7A–7P). B.1a translates it as a single hybrid convention to keep B.1a scoped. This document captures the 16 sub-rules with their proposed kinds, applies_when shapes, effect shapes, and decomposition risks so Phase B.1c has a complete spec to work from.

## Sub-rule catalog

Each entry lists: sub-letter, proposed natural_key, proposed kind, proposed target, brief description, decomposition notes.

### 7A. Paired *STDAT / *ENDAT cross-date constraints

- **natural_key:** `clinical_patterns.paired_start_end_date_constraint`
- **kind:** structured
- **target:** field
- **description:** Any field whose name ends in `STDAT` paired with a sibling ending in `ENDAT` (in the same form, same itemgroup) gets a constraint that the start date is on or before the end date. Constraint expression: `. <= ${<base>ENDAT}` on STDAT; `. >= ${<base>STDAT}` on ENDAT.
- **applies_when:** `field.name matches "^.*STDAT$"` AND sibling exists `field.form.survey[?(@.name=~/^.*ENDAT$/ && @.bind__oc_itemgroup == $.field.bind__oc_itemgroup)]`
- **effect:** `ensure: field.constraint = ". <= ${<sibling>}"`, paired constraint on sibling.
- **decomposition_risk:** Sibling-lookup operator needs to be added to the DSL (the JSONPath shape above isn't in the current `applies_when` operator set). Phase B.1c work item: extend DSL with `has_sibling` or `field_pair` operator.

### 7B. Future-date block on start dates

- **natural_key:** `clinical_patterns.future_date_block_on_start_dates`
- **kind:** structured
- **target:** field
- **description:** Fields whose name ends in `STDAT` or matches a clinical-event start-date pattern get a constraint `. <= today()` and constraint_message "Future dates are not allowed for start dates."
- **applies_when:** `field.type == 'date'` AND `field.name matches "^.*STDAT$|^(VISITDAT|TRTDAT|...)"`
- **effect:** `ensure: field.constraint = ". <= today()"`, `ensure: field.constraint_message`
- **decomposition_risk:** Overlaps with §2 (`future_date_constraint_on_dates`) from B.0 — §2 applies to ALL dates, 7B applies to start dates specifically. Resolution at decomposition: §2 is the general rule, 7B is a stricter-message override for start dates. May merge into a single rule with a soft "use start-date-specific message when name ends in STDAT" clause.

### 7C. *ENDAT relevance gated on *ONGO status

- **natural_key:** `clinical_patterns.endat_relevance_on_ongo_status`
- **kind:** structured
- **target:** field
- **description:** Fields ending in `ENDAT` get `relevant: ${<base>ONGO}='N'` — end-date only visible when the corresponding "ongoing" flag is "No". Pairs with the *STDAT / *ENDAT pattern (7A).
- **applies_when:** `field.name matches "^.*ENDAT$"` AND sibling `${<base>ONGO}` exists.
- **effect:** `ensure: field.relevant = "${<base>ONGO}='N'"`
- **decomposition_risk:** Same sibling-lookup operator dependency as 7A.

### 7D. Sequential dates cascade

- **natural_key:** `clinical_patterns.sequential_dates_cascade`
- **kind:** hybrid
- **target:** form
- **description:** When a form has multiple sequential date fields (screening → consent → enrollment → randomization), each date constraint references the previous date in the sequence: `. >= ${<prev_date>}`. The sequence order comes from the protocol's visit schedule.
- **applies_when (soft):** "form has multiple date fields representing sequential clinical events"
- **effect (soft):** "emit constraints linking each date to the previous in protocol order"
- **decomposition_risk:** "Sequential" requires AI judgment — kind stays hybrid. May be partially mechanized if the orchestrator passes the visit-schedule ordering as context.

### 7E. BMI calculation

- **natural_key:** `clinical_patterns.bmi_calculation`
- **kind:** structured
- **target:** field
- **description:** When a form contains both HEIGHT and WEIGHT fields, emit a BMI calculate field with `type=text` (per OC-5b), `calculation = round(${WEIGHT} div (${HEIGHT} div 100) div (${HEIGHT} div 100), 1)`, `readonly=yes`. Unit assumption: HEIGHT in cm, WEIGHT in kg.
- **applies_when:** `form.survey` contains a field named HEIGHT AND a field named WEIGHT.
- **effect:** `ensure: form contains a BMI calculate row with the canonical formula`.
- **decomposition_risk:** "Form contains field X" predicate needs DSL support. Workaround: orchestrator-level check before convention runs.

### 7F. AE severity/serious logic

- **natural_key:** `clinical_patterns.ae_severity_serious_logic`
- **kind:** hybrid
- **target:** form
- **description:** AE form's AESEV (severity: Mild/Moderate/Severe) drives a default for AESER (serious flag): when AESEV='Severe', AESER defaults to 'Y' but remains editable. Pairs with AE termination logic.
- **applies_when:** `form.form_id == 'AE'` (or repeating AE log-line variant)
- **effect (soft):** "emit AESEV → AESER default-Y cascade with override allowed"
- **decomposition_risk:** "Default but editable" semantics aren't in the current effect operators. Phase B.1c may extend DSL with a `default_value` directive distinct from `set`.

### 7G. Eligibility fixed-value constraints

- **natural_key:** `clinical_patterns.eligibility_fixed_value_constraints`
- **kind:** structured
- **target:** field
- **description:** Inclusion/exclusion criteria fields are select_one yn with a hard constraint that the value matches the protocol-required answer (e.g. IE01: "Subject is 18+" must be Y). Failure to meet causes ineligibility.
- **applies_when:** `field.form.form_id matches "^I[EI]"` AND `field.type == 'select_one yn'`
- **effect:** `ensure: field.constraint = ". = '<expected>'"`, with `<expected>` driven by protocol parsing.
- **decomposition_risk:** The `<expected>` value comes from protocol analysis, not from the convention itself. The convention captures the structural pattern; the value is per-criterion.

### 7H. Physiological range sanity checks

- **natural_key:** `clinical_patterns.physiological_range_sanity_check`
- **kind:** structured
- **target:** field
- **description:** Numeric fields measuring physiological quantities get range-check constraints with warning-level (soft) defaults. HEIGHT 50–250 cm. WEIGHT 2–300 kg. TEMP 30–45 °C. Systolic BP 50–250 mmHg. Etc.
- **applies_when:** `field.name in ['HEIGHT', 'WEIGHT', 'TEMP', 'SBP', 'DBP', 'HR', ...]` AND `field.type in ['integer', 'decimal']`
- **effect:** `ensure: field.constraint = ". >= <min> and . <= <max>"`, ranges per-name
- **decomposition_risk:** Requires a per-name range table (HEIGHT=50–250, WEIGHT=2–300, etc.) — same DSL gap as §28's PRECISION_TABLE per B.0 Finding F5. Phase B.1c work item: extend DSL with a `match` operator that picks a value from a table.

### 7I. RACE multi-select exclusivity

- **natural_key:** `clinical_patterns.race_multiselect_exclusivity`
- **kind:** structured
- **target:** field
- **description:** RACE field is select_multiple with exclusive sentinels: when "Other" or "Unknown" or "Not reported" is selected, no other choice may be selected. Constraint expression: `not(selected(., 'OTHER') and count-selected(.) > 1)` and same for UNKNOWN, NOTREPORTED.
- **applies_when:** `field.name == 'RACE'` AND `field.type == 'select_multiple race'`
- **effect:** `ensure: field.constraint = "..."`
- **decomposition_risk:** Overlaps with §27 (`sentinel_exclusivity_constraint`) from B.0. Resolution at decomposition: §27 is the general pattern, 7I is the RACE-specific application. May merge or keep §27 as the cascade-resolved general rule and have 7I be a "use this for RACE" reference.

### 7J. Optional dose/duration calculations

- **natural_key:** `clinical_patterns.dose_duration_calculations`
- **kind:** hybrid
- **target:** form
- **description:** When a form has DOSE and ROUTE fields, optionally emit derived TOTAL_DOSE or PER_DAY_DOSE calculations based on frequency. Specifically optional — many protocols don't use these.
- **applies_when (soft):** "Form has dosing fields and protocol specifies cumulative or per-period dose tracking"
- **effect (soft):** "emit derived dose calculations per protocol spec"
- **decomposition_risk:** Inherently hybrid. May stay hybrid even after full decomposition.

### 7K. Cross-form value fetch via CF helpers

- **natural_key:** `clinical_patterns.cross_form_value_fetch_cf`
- **kind:** structured
- **target:** field
- **description:** Fields ending in `_CF` (e.g. ICFDAT_CF, SEX_CF, BRTHDAT_CF, AGE_CF, ARMCD_CF, ENRLDAT_CF) are pre-blessed cross-form helpers using external XPath. Each has a canonical calculation shape pulling from the source form (DM, IE, EN).
- **applies_when:** `field.name matches "^.*_CF$"`
- **effect:** `ensure: field.calculation` matches one of the pre-blessed helper patterns; `require: field.bind__oc_external = 'clinicaldata'`
- **decomposition_risk:** Per-CF-helper calculation template needs to live somewhere addressable. May warrant a sibling document `conventions/_audit/CF_helper_catalog.md` listing the seven blessed helpers and their canonical calculations.

### 7L. SEX_CF sex-dependent fields

- **natural_key:** `clinical_patterns.sex_dependent_fields_via_sex_cf`
- **kind:** structured
- **target:** field
- **description:** Fields that only apply to one sex (pregnancy test, menstrual cycle, prostate exam) gate themselves with `relevant: ${SEX_CF}='F'` (or 'M'). SEX_CF is the cross-form helper from 7K.
- **applies_when:** Domain-specific field-name list (PREG*, MENS*, PROS*, etc.)
- **effect:** `ensure: field.relevant references ${SEX_CF}`
- **decomposition_risk:** Field-name allowlist needs to live somewhere addressable. Similar to 7K's CF helper catalog — may share a registry doc.

### 7M. Repeat-key calculate + display pair

- **natural_key:** `clinical_patterns.repeat_key_calculate_display_pair`
- **kind:** structured
- **target:** form
- **description:** Repeating forms emit a two-row pair at the top of the repeat block: (1) a `calculate` row anchoring the repeat key with `once(...@ItemGroupRepeatKey)`, (2) a display-only `text` row showing `if(${ID}!='', ${ID}, 'Scheduled')`.
- **applies_when:** `form.has_repeating_group == true`
- **effect:** `ensure: form.survey first-rows-in-repeat-block match the canonical two-row pattern`
- **decomposition_risk:** Overlaps with OC-5 (`repeating_group_repeat_key_anchor`). Resolution at decomposition: 7M and OC-5 are the same rule. The B.0 audit captured this overlap. May merge during decomposition rather than emit two conventions.

### 7N. (Placeholder — see prompts.py lines 266–474 for actual text)

To be filled in during decomposition. The catalog is intentionally incomplete; B.1c will read the OC-7 prose carefully and produce the complete 16-entry list.

### 7O. (Placeholder — see prompts.py lines 266–474 for actual text)

Same. See 7N.

### 7P. (Placeholder — see prompts.py lines 266–474 for actual text)

Same. See 7N.

## Decomposition risks & DSL gaps

The catalog above surfaced four DSL gaps that B.1c will need to either extend or work around:

1. **Sibling-field lookup operator** — 7A, 7C need to find a paired field in the same form/itemgroup. Workaround: orchestrator-level pre-pass; long-term: `has_sibling` or `field_pair` operator.
2. **Form-level "contains field X" predicate** — 7E, 7J need to gate convention firing on whether the form contains specific named fields. Workaround: orchestrator-level pre-pass; long-term: extend `applies_when` operators to include `form.survey[?(@.name=='X')]`-style queries.
3. **Per-name lookup table operator** — 7H needs HEIGHT→[50,250], WEIGHT→[2,300], etc. Same DSL gap as §28's `PRECISION_TABLE` per B.0 F5. Long-term: `match` operator that picks a value from a table.
4. **`default_value` directive distinct from `set`** — 7F needs "default to Y but allow override," which is semantically different from "force to Y."

Phase B.1c kickoff should start with these four DSL extensions, then decompose the 16 sub-rules with the extended DSL available.

## Sub-rule overlaps to resolve at decomposition

- **7B ↔ §2** (general future-date constraint): merge or refine.
- **7I ↔ §27** (sentinel exclusivity): keep general rule, 7I becomes specific application.
- **7M ↔ OC-5** (repeat-key anchor): merge.
- **§25 / §26 / §27 / §28 ↔ OC-7** (per B.0 finding F4, several §-rules look like specific OC-7 sub-rules expressed standalone): resolution at decomposition.

## Stale duplicates to delete

Per B.0 inventory, OC-7's sub-rules 7A–7P are also written out in:

- `skills/edc-builder/references/xlsform-build-rules.md`
- `skills/protocol-analysis/references/xlsform-patterns.md`

These get deleted in Phase C (per the architecture doc §11 Phase B plan, with deletion of duplicate markdown deferred until the JSON-based conventions are proven in production). B.1c should not touch these files; deletion is a separate Phase C commit.

## Out of scope for B.1c

- Translating §20–§28 conventions from System 2. Those are their own decomposition discussion (B.0 finding F4 candidates), tracked separately.
- Translating customer-specific overrides. Customer scope is empty today.
- Translating vendor-specific rules. Vendor scope work is Phase B.1b per F2_resolution.md.

---

*End of plan. Awaiting Phase B.1c kickoff.*
