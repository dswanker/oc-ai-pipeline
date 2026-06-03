# Calendaring Rules — User Guide

**Last updated:** June 2026
**Pipeline version:** commit 2c6d60a (Phase 4 complete)

---

## What This Feature Does

The Calendaring Rules feature automatically generates and publishes OC4 event scheduling rules from a clinical trial protocol. Given a protocol PDF, the pipeline produces rules that automate:

- **Event scheduling** — scheduling each visit at the correct number of days relative to the prior visit or enrollment
- **Auto-close** — automatically closing visit windows when the upper window bound is exceeded
- **Form visibility** — showing/hiding arm-specific forms based on a participant's assigned treatment arm
- **Unscheduled events** — triggering unscheduled visits when a safety form changes status
- **Participant routing** — assigning participants to the correct study calendar at enrollment (multi-arm studies)

---

## How to Use It

### Step 1 — Run Protocol Analysis

Make sure the study has been run through the full pipeline at least once with Protocol Specification selected. The calendaring rules depend on the scheduling block in the study spec JSON.

> **Note:** If all rules show NEEDS_REVIEW, clear the Protocol Specification JSON column (file_mm2gefht) on the Monday board row before triggering a run. This forces a fresh LLM extraction rather than reusing a cached spec.

### Step 2 — Select "Calendaring Rules" in the Output Dropdown

On the Services AI Hub board, select **Calendaring Rules** from the output dropdown. You can select it alongside other outputs (Study Build ZIP, DVS, etc.).

### Step 3 — Trigger the Pipeline

Set the AI Trigger column to **Send to AI**. The pipeline will run protocol analysis, extract the scheduling block, generate rule artifacts, and upload the calendaring zip to the Calendaring Output column (file_mm3te0de).

### Step 4 — Review the Output Zip

Download the calendaring zip. It contains:

- rules/*.json — individual rule files ready for OC4 Rules Management UI
- reports/validation_report.md — pre-flight validation, check before deploying
- reports/simple_rule_recommendations.md — manual Study Designer setup items
- review/calendaring_spec.xlsx — tabular rule review with confidence flags
- rationale/calendaring_rationale.pdf — per-rule rationale with SoA source references

Check the validation report first. Rules marked NEEDS_REVIEW require manual verification before deployment.

### Step 5 — Publish to OC4 (Optional)

1. Confirm the study UUID is present on the board row
2. Check the **Publish Calendaring Rules** checkbox on the board row
3. Trigger a run with Calendaring Rules selected

The pipeline GETs existing rules first, then POSTs any not already present. Re-running is safe — rules matched by name are skipped.

---

## Rule Types Generated

### Tier 1 — Event Scheduling

| Event type | OC4 trigger | Notes |
|-----------|-------------|-------|
| Index event (first visit) | PARTICIPANT_CREATED | Schedules at enrollment date |
| Relative event (Day 30, Week 4, etc.) | EVENT_START_DATE_CHANGED on anchor | RUN_ON_SCHEDULE daily 00:00, EVENT_CRITERIA with offset |
| Unresolved offset | PARTICIPANT_CREATED | Marked NEEDS_REVIEW |

### Tier 2 — Auto-Close

For each event with a visit window upper bound: RUN_ON_SCHEDULE daily at 23:00, criteria offset = window_upper_days, range = -1, closeEvent: true.

### Tier 3 — Conditional Rules

| Tier | What it generates | Confidence |
|------|------------------|-----------|
| 3a — Arm visibility | FORM_ACTION visibleExpression for arm-specific forms | NEEDS_REVIEW |
| 3b — Dynamic events | FORM_STATUS_CHANGE on CDASH_SAFETY forms to schedule SE_UNSCHEDULED | HIGH |
| 3c — Participant routing | PARTICIPANT_ACTION to setStudyCalendar based on ARMCD | NEEDS_REVIEW |

Tier 3a and 3c XPath expressions must be validated against the OC4 XPath evaluator before deploying to a live study.

---

## Confidence Levels

| Level | Meaning | Action required |
|-------|---------|----------------|
| HIGH | Scheduling block present, validations passed | Safe to deploy |
| NEEDS_REVIEW | Scheduling absent, offset missing, XPath unvalidated, or any validation error | Review manually before deploying |

---

## Troubleshooting

### All rules show NEEDS_REVIEW / "No scheduling block found"

1. Clear file_mm2gefht (Protocol Specification JSON) on the Monday board row
2. Trigger a fresh run — the _extract_scheduling_block second-pass will extract scheduling from the timepoint rows
3. Check Railway logs for: [scheduling-pass] Extracted N scheduling entries for {protocol}

If the second pass also fails, look for [scheduling-pass] Failed in logs — this indicates an API error. The model used is claude-sonnet-4-6.

### Calendaring publish fails with "Failed to fetch existing rules"

- Confirm the study UUID is present on the board row
- Confirm the OC4 study exists at the correct subdomain
- Re-authenticate via the OC Auth Link column if the session has expired

### Rules upload but some fail with 400 errors

Check validation_report.md in the zip. Common causes:
- Event OID does not exist on the study
- criteria.offset is null (violates OC-24343)
- Invalid eventStatusesToTriggerOn value

---

## Monday Board Column Reference

| Column | ID | Purpose |
|--------|----|---------|
| Output Requested (Calendaring Rules) | dropdown_mm2nc7d4 label 7 | Triggers rule generation |
| Calendaring Output | file_mm3te0de | Receives the calendaring zip |
| Calendaring Rules Update Input | file_mm3tgqeg | Upload an edited zip to override |
| Publish Calendaring Rules | boolean_mm3z1xy8 | Auto-publish to OC4 on next run |

---

## Technical Reference

Rule-service API endpoints:

  GET  https://{subdomain}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules
  POST https://{subdomain}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules?newEpochOrCalendar=false

Skill location: skills/calendaring-rules/

Key pipeline functions:
- run_calendaring_rules(struct_json, forms_json) — generates the zip
- _extract_scheduling_block(struct_json) — second-pass scheduling extraction
- publish_calendaring_rules(subdomain, study_uuid, cal_zip_bytes) — POSTs rules to OC4, idempotent by name
