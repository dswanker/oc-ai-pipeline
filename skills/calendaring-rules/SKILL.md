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

## Execution Script
python3 skills/calendaring-rules/scripts/extract_calendar_rules.py <study_spec.json>
