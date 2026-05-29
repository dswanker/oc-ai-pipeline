# OC4 Calendaring → oc-ai-pipeline Integration Scope

**Status:** DRAFT — scoping / pre-implementation
**Date:** 2026-05-26
**Context:** oc-ai-pipeline (protocol → EDC build / DVS / pricing automation)
**Source docs:** `Internal_Calendaring_Documentation.md`, `study-service.adoc`, `How_and_When_to_Use_APIs`; skills `protocol-analysis`, `edc-builder`, `dvs-specification`
**Worked example throughout:** PrTK05 (Candel Therapeutics, aglatimagene besadenovec shedding study; 2 arms)

---

## 1. Purpose

The pipeline today reads a protocol and produces the **data-collection** layer (XLSForms, study spec, DVS, pricing). It understands the visit schedule only as *description*. OpenClinica 4 **calendaring** is the **execution** layer that automates event scheduling, visit-window compliance, conditional form behavior, dynamic events, and participant routing. This document scopes deriving calendaring **rule artifacts** from the same protocol read, as a new downstream output of the pipeline.

---

## 2. OC4 calendaring primer (what we integrate with)

**Module / modes.** Calendaring is an internal module (hidden on the modules page), enabled by default for studies created after R19; older studies are enabled by OC staff via API. Two modes:

- **Simple** (default) — simple rules built through the study-designer UI (reminder notifications, auto-close). *Not* exportable as importable artifacts.
- **Advanced** — JSON rules authored in the Rules Management UI (`.../#/studies/{study-UUID}/calendar`), added via **Add Rule** / **Update Rule**. This is the only programmatically generatable form.

**Rule anatomy (advanced JSON).** `metadata` + a **when** + a **condition** + **actions**, optionally scoped to `epoch` + `studyCalendar` (`null/null` ⇒ global rule, every participant, whole study).

- **The "when":** *trigger-based* (`triggerType` = one or more participant/event/form data-changes — e.g. `PARTICIPANT_CREATED`, `EVENT_STATUS_CHANGE`, `FORM_STATUS_CHANGE`, `USER_CLOSES_FORM` — optionally narrowed by `triggerOID` to a specific `SE_`/`F_` OID; `null` = wildcard), or *run-on-schedule* (`RUN_ON_SCHEDULE`, `DAILY`, `time` HH:MM:SS respected on the hour, participant-filter `criteria`, non-null `offset`). Pure async-on-any-change exists but is explicitly discouraged.
- **The condition:** an XPath expression over the participant's ODM, evaluating true/false. Tokens include `${EVENT_TRIGGER_REPEAT_KEY}` (repeat ordinal, available on event/form triggers) and `$TRUE`.
- **The actions** (fire when condition matches `ruleResultToTriggerOn`):
  - **Event** — schedule / move / lock / change-status of an event. Helper style: `relativeEventOid` + `startDateRelativeDays`. XPath style: `startDateExpression`. `targetEventOid` accepts ordinals (`SE_TREATMENT[2]`, `SE_TREATMENT[${EVENT_TRIGGER_REPEAT_KEY}]`). Also `targetEventStatus`, `lockedExpression`, `eventStatusesToTriggerOn`.
  - **Form** — set `requiredExpression` / `visibleExpression` / `editableExpression` / `targetFormStatus`. Requires `targetEventOid` + `targetFormOid`.
  - **Notification** — email/SMS via `toEmailAddress` / `toPhoneNumber` (accept `$participant`), `emailSubject`, `emailMessage`, `textMessage`; light HTML formatting.
  - **Participant** — `setEpoch`, `setStudyCalendar`.

**Reference: minimal Event Action rule (from the internal doc).**

```json
{
  "name": "Event 4 Scheduler",
  "description": "Schedules the Event 4 event",
  "condition": "$TRUE",
  "epoch": null,
  "studyCalendar": null,
  "actions": [{
    "type": "EVENT_ACTION",
    "ruleResultToTriggerOn": true,
    "condition": null,
    "targetEventOid": "SE_EVENT4",
    "startDateExpression": "format-date(now(), '%Y-%m-%d')",
    "eventStatusesToTriggerOn": ["NOT_SCHEDULED","SCHEDULED","DATA_ENTRY_STARTED","COMPLETED","STOPPED","SKIPPED"]
  }],
  "triggerType": null,
  "triggerOID": null
}
```

**Operational gotchas to design around:**

- **Update Rule overwrites** the entire rule it targets. → namespace rule names so re-runs never silently clobber.
- **Circuit breaker:** the same rule action firing ~10–15×/min for a participant disables that participant's calendaring (infinite-loop protection). → emit idempotency guards.
- **XPath cost** is 220–500 ms/eval; best practice is to push conditions as high up the chain as possible (rule condition → `ruleResultToTriggerOn` → action condition → field expression).
- **`USER_CLOSES_FORM` race:** form data may not have reached ODM service yet; prefer `FORM_STATUS_CHANGE = COMPLETED` when the condition reads item values.
- 13 documented rule-upload error messages → reusable as a pre-flight validation spec.
- **XPath evaluator endpoint** exists for validating expressions against a participant.

---

## 3. Current pipeline state & the gap

`protocol-analysis` already extracts a visit map: `event_oid`, `arm`, free-text `visit_window`, `forms_assigned`, plus `{study_id}_tpt.csv`. `edc-builder` mints the `SE_*` and `F_*` OIDs. **But the timing relationships never become executable, and the free-text windows are not machine-resolvable** (see §6.1). There is currently **no scheduling-rules output**.

**PrTK05 finding (revised after B0 audit).** Earlier wording said "the control arm isn't represented at all" — that was too strong. The build *does* recognize both arms: the Enrollment form carries an `ARMCD` field (`TRT`/`CTRL`), eligibility items are arm-flagged, and there is a control-specific SAE form scoped to the control cadence (`SE_SCREENING, SE_WEEK_2_3, SE_WEEK_4_6, SE_WEEK_8_10, SE_END_OF_STUDY, SE_UNSCHEDULED`). The actual limitation is the **event model**: arm differences are represented at the **form level** (arm applicability + `relevant` logic) on a **single shared, treatment-shaped event timeline**. No `SE_CTL*` events exist; a control participant would be placed on the full 18-event treatment timeline with ~13 events inapplicable. Separately, the SOE's **Arm column is unreliable** — it is computed at render time (string test `"CTL" in event_oid`, with hardcoded `SE_BASELINE`/`SE_UNSCH` constants that don't match the emitted `SE_SCREENING`/`SE_UNSCHEDULED`), and `{study_id}_tpt.csv` carries no `arm` column, so extracted arm is never persisted. Result: every event prints `TREATMENT`.

**Two independent root causes.** Distinct problems, distinct fixes; neither resolves the other:

1. **Timing isn't machine-readable** — windows are prose ("Week 4-6 (2-3 weeks post Injection #2)") a rule generator cannot act on. Fixed by the structured scheduling block (§6.1). Needed regardless of arms. → Track A / Phase 0.
2. **Arms aren't modeled as schedules, and arm isn't propagated to the SOE** — arm lives only as form-level applicability on a shared timeline, and the SOE arm label is guessed rather than read. Two sub-fixes at two layers: **B-report** (generator: persist `arm` to the CSV / render extracted arm, drop the OID heuristic) and **B-model** (skill: represent arms as distinct event sets / per-arm calendars). See §6.2 / §10.

The scheduling-block fix decides *how* each event's timing is encoded; B-model decides *whether each arm has its own schedule at all*. Both must land before calendaring can generate correct rules for a multi-arm study — tracked separately (§10–§12).

---

## 4. Integration vision (full) — capability → pipeline mapping

| Tier | OC4 capability | Pipeline source | Generated rule |
|---|---|---|---|
| 1 | Visit scheduling (Event Action) | structured SoA anchors/offsets | `relativeEventOid` + `startDateRelativeDays`; ordinals for repeating cycles |
| 2 | Visit-window compliance (run-on-schedule Notification + auto-close) | window ± bounds | daily reminder + out-of-window auto-close |
| 3a | Conditional forms (Form Action) | arm-specific / dependency detection | `requiredExpression` / `visibleExpression` keyed on data |
| 3b | Dynamic / triggered events (trigger-based Event Action) | safety/early-term logic | unscheduled visit on form/status change |
| 3c | Participant routing (Participant Action) | arm assignment (`ARMCD`) | `setStudyCalendar` / `setEpoch` at enrollment |

---

## 5. Architecture

- **New downstream skill `calendaring-rules`**, positioned like `dvs-specification`: it reads the **EDC build output** (not the protocol independently). Reading the build is the only way to guarantee rules reference the **exact OIDs** edc-builder minted; parallel generation would drift OIDs (cf. `form_specs` naming drift).
- **Inputs:** study-spec JSON (visit map + minted OIDs + new structured `scheduling` block, §6.1) + EDC build (form/item OID inventory for XPath references).
- **Pipeline wiring:** new `output_requested` value ("Calendaring Rules") on `dropdown_mm2nc7d4` (label ID 7), gated by `_want()`; output zip posts to file column `file_mm3te0de` ("Calendaring Output"); human write-back uploads to `file_mm3tgqeg` ("Calendaring Rules Update Input") — same pattern as DVS Spec Update Input. Handler is a surgical addition to the EDC chain reusing the `build_zip_holder` shared-state pattern — no wholesale rewrite. All three board changes deployed to AI Hub board 18409146946.
- **Publish:** see Decision #4 — no confirmed customer-facing rules API; default deliverable is *reviewable artifacts for a human to paste into Rules Management*.

---

## 6. Decision detail

### 6.1 Decision #1 — structured scheduling block (Phase 0)

**Problem.** `visit_window` is free text. Example from PrTK05:

> `SE_WEEK_4_6 | "Week 4-6 (2-3 weeks post Injection #2)" | TREATMENT`

The **label implies week 4–6 from baseline**, but the **true anchor is Injection #2** (a protocol-footnote fact). A generator parsing the prose anchors to Day 0 and is wrong by weeks. PrTK05's visits form a **dependency chain** — post-inj-1 timepoints anchor to Injection #1; Injection #2 → Injection #1; TA #2 / Week 4-6 → Injection #2; everything past Week 6 → Injection #3 — resolvable only from explicit anchors.

**Proposal.** `protocol-analysis` emits a per-event structured block:

```json
{
  "event_oid": "SE_WEEK_4_6",
  "anchor_event_oid": "SE_INJECTION_2",
  "offset_target_days": 17,
  "window_lower_days": 14,
  "window_upper_days": 21,
  "repeating": false,
  "arm": "TREATMENT",
  "conditional_trigger": null
}
```

Single-purpose enrichment (one block), streaming API if it raises token count — same discipline as the `protocol_data_points` work. This is the true starting point; without it the calendaring skill re-parses prose and inherits XPath errors at the worst layer.

### 6.2 Decision #2 — epochs & study calendars

**Model.** A **study calendar** = a named schedule a participant follows (events + timing); an **epoch** = a phase grouping within it (Screening / Treatment / Follow-up). Rules carry `epoch` + `studyCalendar` scope (`null/null` = global); a Participant Action sets them; the engine rejects references to an epoch/calendar that "does not exist," so both are named study-design entities.

**Why it matters (PrTK05).** Treatment arm ≈ 18 events; concurrent control arm = 5 (Screening, W2-3, W4-6, W8-10, W16-18; blood/urine/PSA only). Clean design = **two calendars** (TREATMENT, CONTROL) + one enrollment Participant Action: `ARMCD = TRT → setStudyCalendar TREATMENT`, `CTRL → CONTROL`. Arm rules then scope to their calendar instead of every rule carrying an `if ARMCD=TRT` XPath guard (fewer/cheaper expressions, less fragile).

**Gap (revised after B0; SOE generator confirmed against repo).** The build recognizes both arms but models arm differences at the **form level** on a **single shared event timeline** — no `SE_CTL*` events, no per-arm calendar. Per-arm calendars therefore require a **B-model** change: `protocol-analysis` representing arms as distinct event sets / per-arm schedules, and `edc-builder` minting the corresponding calendars. **Confirmed in repo:** `scripts/generate_study_spec_pdf.py` recomputes the SOE Arm column from a `"CTL"`-string heuristic with hardcoded `SE_BASELINE`/`SE_UNSCH` constants (which don't match the emitted `SE_SCREENING`/`SE_UNSCHEDULED`), and `event_map` carries no arm — so B-report must thread arm through (CSV column or per-event arm from the JSON). *Open: authoring path (study designer vs API) for calendars/epochs.*

---

## 7. Output contract — the "calendaring pack" (zip)

- `rules/*.json` — one validated rule per file, ready to paste into Add Rule; names namespaced (`PrTK05_sched_SE_WEEK_4_6`) so Update Rule never silently clobbers.
- `calendaring_rationale.pdf` — each rule: what it does, the SoA row/criterion it derives from, OIDs touched, confidence tier.
- `calendaring_spec.xlsx` — tabular review (rule / trigger / condition / action / target OID / offset / window), DVS-workbook style.
- `validation_report.md` — pre-flight results vs the engine's documented constraints + list of XPath flagged for the evaluator endpoint.
- `simple_rule_recommendations.md` — simple-mode rules that can't be exported, documented for manual study-designer setup (per Decision #3).

---

## 8. Guardrails & validation

- **Static validator** encodes the 13 documented upload errors (non-empty name/condition, valid action type, no mixing `startDateExpression` with the relative pair, valid status enums, valid JSON).
- **No async-on-any-change**; always trigger-based with specific `triggerOID`, or run-on-schedule with criteria.
- **Idempotency guards** on any action that could re-fire its own trigger (`eventStatusesToTriggerOn`, "only if not already scheduled") to stay clear of the circuit breaker.
- Prefer `FORM_STATUS_CHANGE=COMPLETED` over `USER_CLOSES_FORM` when conditions read item values.
- Structure conditions high in the chain for XPath cost.
- **Live XPath evaluation** at generation time via the evaluator endpoint (Decision #5) — dependency: a TEST study + `OC_API_*` creds available to the skill.

---

## 9. Confidence tiers (what is safe to automate)

- **Tier 1 — mechanical (auto):** relative-day visit scheduling from a clean structured SoA. Helper-style Event Actions. Low XPath surface.
- **Tier 2 — templated (auto, light review):** window reminders + auto-close, parameterized from window bounds.
- **Tier 3 — semantic (draft + flag):** conditional forms, dynamic events, participant routing. Synthesized XPath = `form_specs`-class accuracy risk; gate behind the evaluator endpoint before trusting.

---

## 10. Phased roadmap

**Two parallel root-cause tracks** (see §3). Calendaring Tiers 1–2 depend only on Track A; only Tier 3c depends on Track B.

**Track A — scheduling structure (calendaring Phase 0):**
- **Phase 0:** structured `scheduling` block in `protocol-analysis` (single-purpose patch). Timing-encoding fix only — *not* coupled to the arm work.
- **Phase 1:** `calendaring-rules` skill — Tier 1 + validator + rationale + board output. Hand-paste deliverable.
- **Phase 2:** Tier 2 (run-on-schedule reminders + auto-close).
- **Phase 3:** Tier 3a/3b, gated by evaluator-endpoint validation.
- **Phase 4:** if a rules API exists, auto-publish to a TEST study (parallels the blocked form-definition publishing).

**Track B — arm modeling & SOE correctness (build-correctness fix, runs independently):**
- **B0 — DONE (audit).** Not an extraction miss: the build recognizes both arms (`ARMCD` field, arm-flagged eligibility, control-specific SAE form) but models arm at the form level on one shared timeline. Two distinct defects identified below.
- **B-report (generator layer, small/deterministic) — CONFIRMED in repo:** `skills/protocol-analysis/scripts/generate_study_spec_pdf.py` recomputes the Arm column via `"CTL" in ev` with hardcoded `SE_BASELINE`/`SE_UNSCH`; `event_map` (built from `visits_assigned`) carries no arm; `{study_id}_tpt.csv` has no arm column. Fix: thread `arm` through (CSV column or per-event arm from JSON), render it, delete the heuristic/constants. (Also: file is a renamed copy of the old EDC-structure generator — docstring + `build_edc_pdf` name — minor cleanup.)
- **B-model (skill layer) — DECIDED: per-arm event sets / per-arm calendars.** Represent arms as distinct event sets (`SE_CTL*` events, per-arm schedules) + enrollment Participant Action routing — *not* the single-timeline + form-relevance model. Touches `protocol-analysis` (extraction + guidance) and `edc-builder` (calendar minting). Remaining work is the event-model design, not the decision.
- Prioritized on its own build-quality merits, not on the calendaring timeline.

**Cross-dependency:** calendaring **Tier 3c** (participant routing — `setStudyCalendar` by `ARMCD`) is blocked until **B-model** lands (there is no control calendar to route to otherwise). B-report is independent and worth doing on its own. Tiers 1, 2, 3a, 3b do not depend on Track B.

---

## 11. Decisions log

| # | Decision | Status |
|---|---|---|
| 1 | Add structured `scheduling` block to `protocol-analysis` (timing-encoding fix) as calendaring Phase 0 | **Settled — yes.** Track A, Phase 0. Single-purpose patch, independent of the arm work. |
| 2 | Mint per-arm study calendars + enrollment Participant Action routing | **Settled — yes (per-arm calendars).** Arms modeled as distinct event sets / per-arm calendars; routing via enrollment Participant Action. Remaining work = event-model design. Gates only Tier 3c. |
| 3 | Advanced JSON is the sole *generated* output; simple-mode rules documented as recommendations | **Settled — yes.** |
| 4 | Customer-facing API to publish calendaring rules | **Settled — yes.** Engineering confirmed same API as form-definition publish. Auto-publish to OC study instance is in scope; confirm endpoint format and required study status before implementing the pipeline handler. |
| 5 | Call the XPath evaluator endpoint as a generation-time gate | **Settled — yes (ideal).** Adds TEST-study + `OC_API_*` dependency. |
| 6 | Track the arm gap as its own build-correctness fix (Track B), not bundled into calendaring | **Settled — yes.** Two parallel tracks (§3, §10). |
| 7 | B0 audit outcome: arm is recognized but modeled at form level on a shared timeline; SOE arm column is computed, not read | **DONE.** Split into B-report (generator) + B-model (skill); see §10. |

---

## 12. Open questions / dependencies

1. **B-model event-model design:** how to represent per-arm event sets / per-arm calendars in `protocol-analysis` output + `edc-builder` minting. (Direction decided — per-arm calendars; design is the remaining work.)
2. **SOE generator (B-report) — VERIFIED against repo.** `generate_study_spec_pdf.py` uses the `"CTL"` heuristic; `event_map` has no arm; CSV has no arm column. Resolved into the B-report fix above.
3. **Confirm extracted arm in JSON:** inspect a real analysis JSON (`study_visit_schedule[].arm` or equivalent) to confirm whether per-event arm is assigned correctly upstream of rendering — and to choose the B-report threading approach.
4. **Calendar/epoch authoring path:** does `edc-builder` emit study calendars/epochs at all today? Study designer vs API.
5. **Rules API (Decision #4):** does an internal/customer rules-publishing API exist, or is hand-paste the ceiling?
6. **Evaluator access:** standing TEST study + credentials path for generation-time XPath validation.
7. **Repeating events:** run-on-schedule repeating-event support and `${EVENT_TRIGGER_REPEAT_KEY}` bugs noted in the internal doc — confirm status before relying on them for cyclic injections.
