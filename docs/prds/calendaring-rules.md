# Calendaring Rules — Product Requirements Document

**Status:** Shipped  
**Last updated:** June 2026  
**Owner:** Dan Swanker, VP Service Delivery  
**Commits:** 62c67ba (Phase 0), 6c71602 (Phase 1), a095cdf (Phase 2+3), 2c6d60a (Phase 4), 92e0835 (docs)

---

## Problem Statement

Clinical trial studies built in OpenClinica 4 require a set of Event Action rules to automate visit scheduling, window management, and participant routing. These rules are currently authored manually in the OC4 Study Designer — a time-consuming, error-prone process that requires deep familiarity with OC4's rule JSON format, XPath syntax, and documented upload constraints. For a study with 15–20 scheduled events, manual rule authoring can take several hours and is typically deferred or skipped in the initial build.

The pipeline already extracts a structured Schedule of Events from every protocol PDF. This structured data contains everything needed to generate the rules mechanically — anchors, offsets, window bounds, arm assignments, safety form categories — but it was not previously used to produce rules.

---

## Goals

1. Generate production-accurate OC4 calendaring rules from protocol analysis output without manual authoring
2. Surface rule artifacts in a reviewable, auditable format before deployment
3. Publish validated rules directly to OC4 studies via the pipeline, reducing manual steps to zero for standard scheduling patterns
4. Flag rules that require human review (XPath validation, conditional logic) clearly and consistently

---

## Non-Goals

- Generating rules for studies not processed through the oc-ai-pipeline
- Full XPath generation from arbitrary clinical logic (deferred — Tier 3a/3c marked NEEDS_REVIEW pending evaluator validation)
- Integration with OC4 Test or Production publish workflow (calendaring rules are designer-environment artifacts)

---

## Solution Overview

A new `calendaring-rules` skill generates, validates, and publishes OC4 Event Action rules from the study spec JSON produced by protocol analysis. The pipeline is extended with a dedicated chain step, a Monday.com output column, and an optional auto-publish checkbox.

### Architecture
Protocol PDF
↓
protocol-analysis skill (SKILL.md Step 1a–1d)
→ scheduling[] array in study spec JSON
↓
_extract_scheduling_block (second-pass fallback if scheduling absent)
↓
calendaring-rules skill
→ extract_calendar_rules()  — builds rule dicts from scheduling data
→ validate_rules()          — static pre-flight against 14 documented upload errors
→ generate_rule_artifacts() — emits rules/*.json + reports + review xlsx + rationale PDF
↓
Monday board: file_mm3te0de (Calendaring Output)
↓ (if Publish Calendaring Rules checkbox checked)
publish_calendaring_rules()
→ GET existing rules (idempotency)
→ POST new / PUT existing via rule-service API

---

## Requirements

### Functional

| ID | Requirement | Status |
|----|-------------|--------|
| F-01 | Protocol analysis must emit a `scheduling` array with one entry per event OID | ✅ Phase 0 |
| F-02 | Index events (no anchor) generate PARTICIPANT_CREATED rules matching production pattern | ✅ Phase 1 |
| F-03 | Relative events generate EVENT_START_DATE_CHANGED + RUN_ON_SCHEDULE + EVENT_CRITERIA rules | ✅ Phase 2 |
| F-04 | Events with `window_upper_days` generate auto-close RUN_ON_SCHEDULE rules | ✅ Phase 2 |
| F-05 | Arm-specific forms generate FORM_ACTION visibility rules (NEEDS_REVIEW) | ✅ Phase 3 |
| F-06 | CDASH_SAFETY forms generate FORM_STATUS_CHANGE → SE_UNSCHEDULED rules | ✅ Phase 3 |
| F-07 | Multi-arm studies generate PARTICIPANT_ACTION routing rules (NEEDS_REVIEW) | ✅ Phase 3 |
| F-08 | All rules validated against 14 documented upload error conditions before output | ✅ Phase 1 |
| F-09 | Output zip contains rules/*.json + validation report + review xlsx + rationale PDF | ✅ Phase 1 |
| F-10 | Pipeline publishes rules to OC4 rule-service API when checkbox is checked | ✅ Phase 4 |
| F-11 | Publish is idempotent: POST new rules, PUT existing rules (matched by name) | ✅ Phase 4 |
| F-12 | Second-pass scheduling extraction fires when main protocol analysis omits scheduling block | ✅ Phase 0 |

### Non-Functional

| ID | Requirement | Status |
|----|-------------|--------|
| N-01 | Rules generation adds < 5s to pipeline runtime (pure Python, no LLM) | ✅ |
| N-02 | Second-pass scheduling extraction uses claude-sonnet-4-6, max_tokens=4000 | ✅ |
| N-03 | Publish is additive — calendaring errors do not abort the main build chain | ✅ |
| N-04 | NEEDS_REVIEW rules are clearly flagged in all output artifacts | ✅ |

---

## Rule Tiers

### Tier 1 — Event Scheduling (all studies)
Mechanical extraction from `scheduling[]` array. High confidence. No LLM required.
- Index event → `PARTICIPANT_CREATED`, `targetEventStatus: SCHEDULED`
- Relative event → `EVENT_START_DATE_CHANGED` + `RUN_ON_SCHEDULE` + `EVENT_CRITERIA`

### Tier 2 — Auto-Close (events with window upper bounds)
Generated automatically for any event with `window_upper_days` set.
- `RUN_ON_SCHEDULE` daily 23:00, `closeEvent: true`, criteria `range: -1`

### Tier 3 — Conditional Rules (arm-specific and event-triggered)
- **3a — Arm visibility:** FORM_ACTION per arm-specific form/event placement. XPath unvalidated — NEEDS_REVIEW.
- **3b — Dynamic events:** FORM_STATUS_CHANGE on CDASH_SAFETY forms → schedule SE_UNSCHEDULED. HIGH confidence.
- **3c — Participant routing:** PARTICIPANT_ACTION per study_calendars arm. XPath unvalidated — NEEDS_REVIEW.

---

## API Reference

**Rule-service endpoints (OC4 designer environment):**
GET  https://{subdomain}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules
POST https://{subdomain}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules?newEpochOrCalendar=false
PUT  https://{subdomain}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules/{rule_uuid}

Authentication: Bearer token from `_get_oc_token(subdomain)` — same as form upload and study publish.

---

## Monday Board Integration

| Column | ID | Purpose |
|--------|----|---------|
| Output Requested (label 7: Calendaring Rules) | `dropdown_mm2nc7d4` | Triggers generation |
| Calendaring Output | `file_mm3te0de` | Receives the zip |
| Calendaring Rules Update Input | `file_mm3tgqeg` | Override: upload edited rules zip |
| Publish Calendaring Rules | `boolean_mm3z1xy8` | Auto-publish to OC4 on next run |

---

## Known Gaps / Open Items

| # | Item | Priority |
|---|------|----------|
| 1 | Tier 3a/3c XPath not validated against OC4 XPath evaluator endpoint | Medium |
| 2 | Tier 3a/3c untested on a real multi-arm study | Medium |
| 3 | No pre-flight check that calendaring module is enabled on the study | Low |
| 4 | ~~PUT update not implemented~~ | ~~Done~~ |
| 5 | fixtures/study_spec.json lacks scheduling block — local tests fall back to NEEDS_REVIEW | Low |

---

## Key Files

| File | Purpose |
|------|---------|
| `skills/calendaring-rules/SKILL.md` | Skill documentation |
| `skills/calendaring-rules/scripts/extract_calendar_rules.py` | Rule extraction (Tiers 1–3) |
| `skills/calendaring-rules/scripts/validate_rules.py` | Static validation |
| `skills/calendaring-rules/scripts/generate_rule_artifacts.py` | Artifact generation |
| `skills/calendaring-rules/references/calendaring-guidelines.md` | Rule format reference |
| `skills/protocol-analysis/SKILL.md` | Step 1a–1d scheduling block instructions |
| `pipeline.py` | `run_calendaring_rules`, `_extract_scheduling_block`, `publish_calendaring_rules` |
| `docs/user-guides/calendaring-rules.md` | End-user guide |
| `docs/calendaring-integration-scope.md` | Original scoping document |
