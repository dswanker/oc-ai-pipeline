# Calendaring Rules Skill

## Purpose
Convert a protocol-analysis study spec JSON + EDC build output into OC4 calendaring rule artifacts ready to paste into the Rules Management UI.

Tier 1 (this skill): mechanical Event Action rules from the structured scheduling block. Pure Python, no LLM call.

## Before You Begin
Always read references/calendaring-guidelines.md before processing any rules.

## Inputs
- struct_json — study spec JSON. Key fields: study_meta.protocol_number, scheduling[] (Phase B+), timepoint_csv.rows (fallback), study_calendars[].
- forms_json — EDC build survey rows. Accepted for interface parity; not consumed in Tier 1.

## Step 1: Extract
Call extract_calendar_rules(struct_json, forms_json).
Returns rule_data with: rules[], simple_rule_recommendations[], warnings[], has_scheduling (bool), study_calendars[].
If has_scheduling is False: all rules marked NEEDS_REVIEW. Prominently note this in all outputs.

## Step 2: Validate
Call validate_rules(rule_data).
Checks 7 statically-verifiable errors from the 13 documented upload errors.
Errors 5-10 depend on the live OC instance — not checkable here.
Adds validation_errors to each rule._meta and a top-level validation_summary.

## Step 3: Generate Artifacts
Call generate_rule_artifacts(rule_data, output_dir, build_log).
Produces a zip containing:
- rules/*.json — one validated rule per file, stripped of _meta, ready to paste into Add Rule
- reports/validation_report.md — pre-flight results + warnings
- reports/simple_rule_recommendations.md — manual Study Designer setup items
- review/calendaring_spec.xlsx — tabular rule review
- rationale/calendaring_rationale.pdf — per-rule rationale with SoA source

## Output Confidence Tiers
- HIGH: scheduling block present, anchor and offset populated, validation passed
- NEEDS_REVIEW: scheduling absent, or offset missing, or any validation error

## Phase 3 — Tier 3a, 3b, 3c

Phase 3 rules are generated automatically alongside Tier 1/2 rules. No separate invocation needed.

### Tier 3a: Arm-Based Form Visibility
For each form with `arm_applicability` set to TRT or CTRL (not BOTH/ALL), generates a FORM_ACTION rule that sets `visibleExpression` to show the form only to the correct arm. Requires an ARMCD `select_one` item in any form — the extractor locates it automatically. All Tier 3a rules are marked **NEEDS_REVIEW** and flagged `xpath_needs_evaluator: true` because the XPath item-value syntax must be validated against the OC4 XPath evaluator endpoint before deploying to a live study.

### Tier 3b: Dynamic Unscheduled Events
For each form with `form_category: CDASH_SAFETY`, generates a trigger-based EVENT_ACTION rule: when the safety form changes status, the unscheduled event OID (any event containing "UNSCH" in its OID) is scheduled if not already scheduled. No synthesized XPath — HIGH confidence, safe to deploy without evaluator validation.

### Tier 3c: Participant Routing
If `study_calendars` is populated (B-model runs only), generates one PARTICIPANT_ACTION rule per arm that routes participants to their study calendar at enrollment based on ARMCD value. Marked **NEEDS_REVIEW** — XPath condition requires evaluator validation. Skips silently when `study_calendars` is absent (single-arm study or pre-B-model spec).

### NEEDS_REVIEW rules
Rules marked `confidence: NEEDS_REVIEW` should not be deployed to a live study until the `visibleExpression` or `condition` XPath has been validated against the OC4 XPath evaluator endpoint. The `validation_report.md` in the output zip lists all NEEDS_REVIEW rules and flags `xpath_needs_evaluator: true` rules explicitly.

## Execution Script
python3 skills/calendaring-rules/scripts/extract_calendar_rules.py <study_spec.json>
