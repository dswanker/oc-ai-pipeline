"""
extract_calendar_rules.py — Mechanical OC4 calendaring-rule extractor (Tier 1)

Walks a study's scheduling structure and builds OC4 Event Action rule dicts
ready for upload. The rules are a MIRROR of the resolved scheduling block, not
an invention of new timing from protocol prose.

Tier 1 covers the common case: every event is pre-scheduled at enrollment via a
PARTICIPANT_CREATED trigger, anchored either to an index date (now()) or to a
relative event offset. Windows and conditional triggers are NOT auto-built in
Tier 1 — they are surfaced as recommendations for the study designer.

Pure stdlib, no I/O inside functions. The __main__ block is a JSON-in/JSON-out
CLI for testing.
"""

import json
import sys


# ── Valid enum sets (from the 13 documented upload errors) ───────────────────

VALID_EVENT_STATUSES = {
    "NOT_SCHEDULED", "SCHEDULED", "DATA_ENTRY_STARTED",
    "COMPLETED", "STOPPED", "SKIPPED",
}
VALID_FORM_STATUSES = {"NOT_STARTED", "INITIAL_DATA_ENTRY", "COMPLETED"}

INDEX_DATE_EXPRESSION = "format-date(now(), '%Y-%m-%d')"


# ── Entry normalisation ──────────────────────────────────────────────────────

def _normalize_scheduling_entry(entry):
    """Normalise a struct_json['scheduling'] entry into the fields we consume."""
    e = entry or {}
    return {
        "event_oid":          e.get("event_oid"),
        "anchor_event_oid":   e.get("anchor_event_oid"),
        "offset_target_days": e.get("offset_target_days"),
        "window_lower_days":  e.get("window_lower_days"),
        "window_upper_days":  e.get("window_upper_days"),
        "repeating":          bool(e.get("repeating")),
        "arm":                e.get("arm"),
        "conditional_trigger": e.get("conditional_trigger"),
    }


def _normalize_timepoint_row(row):
    """Normalise a timepoint_csv row into a scheduling-shaped entry (fallback)."""
    r = row or {}
    return {
        "event_oid":          r.get("event"),
        "anchor_event_oid":   None,
        "offset_target_days": None,
        "window_lower_days":  None,
        "window_upper_days":  None,
        "repeating":          False,
        "arm":                r.get("arm"),
        "conditional_trigger": None,
    }


# ── Rule builder ─────────────────────────────────────────────────────────────

def _build_rule(entry, protocol_number, force_review=False):
    """Build a single OC4 Event Action rule dict (with a _meta side-channel)."""
    event_oid = entry["event_oid"]
    anchor    = entry["anchor_event_oid"]
    offset    = entry["offset_target_days"]

    confidence = "HIGH"

    if anchor is None:
        # Index event — schedule at the participant's enrollment date via now().
        relative_event_oid     = None
        start_date_relative    = None
        start_date_expression  = INDEX_DATE_EXPRESSION
        description = (
            f"Schedules the {event_oid} index event at the enrollment date "
            f"on participant creation"
        )
    elif offset is not None:
        # Relative event — helper style: anchor + offset days.
        relative_event_oid     = anchor
        start_date_relative    = offset
        start_date_expression  = None
        description = (
            f"Schedules {event_oid} {offset} day(s) relative to {anchor} "
            f"on participant creation"
        )
    else:
        # Anchored but no resolved offset — placeholder, needs human review.
        relative_event_oid     = anchor
        start_date_relative    = 0
        start_date_expression  = None
        confidence = "NEEDS_REVIEW"
        description = (
            f"Schedules {event_oid} relative to {anchor} (offset unresolved — "
            f"placeholder 0 days) on participant creation"
        )

    if force_review:
        confidence = "NEEDS_REVIEW"

    # Production-verified rule patterns (matched against live OC4 study rules):
    # Index event:    PARTICIPANT_CREATED trigger, targetEventStatus="SCHEDULED",
    #                 eventStatusesToTriggerOn=null, type/schedule/time/criteria=null
    # Relative event: EVENT_START_DATE_CHANGED trigger on anchor OID,
    #                 RUN_ON_SCHEDULE daily at 00:00:00,
    #                 criteria={EVENT_CRITERIA, anchor, offset, range=0}
    # Unresolved:     PARTICIPANT_CREATED (no offset → can't build criteria), NEEDS_REVIEW
    if anchor is None:
        trigger_type          = ["PARTICIPANT_CREATED"]
        trigger_oid           = None
        rule_type             = None
        rule_schedule         = None
        rule_time             = None
        rule_criteria         = None
        action_target_status  = "SCHEDULED"
        action_statuses       = None
    elif offset is not None:
        trigger_type          = ["EVENT_START_DATE_CHANGED"]
        trigger_oid           = anchor
        rule_type             = "RUN_ON_SCHEDULE"
        rule_schedule         = "DAILY"
        rule_time             = "00:00:00"
        rule_criteria         = {
            "type":         "EVENT_CRITERIA",
            "eventOid":     anchor,
            "eventStatuses": ["SCHEDULED", "DATA_ENTRY_STARTED", "COMPLETED"],
            "offset":       offset,
            "when":         "after",
            "range":        0,
        }
        action_target_status  = "SCHEDULED"
        action_statuses       = ["NOT_SCHEDULED"]
    else:
        # Anchor present but offset unknown — can't build criteria; flag NEEDS_REVIEW
        trigger_type          = ["PARTICIPANT_CREATED"]
        trigger_oid           = None
        rule_type             = None
        rule_schedule         = None
        rule_time             = None
        rule_criteria         = None
        action_target_status  = None
        action_statuses       = ["NOT_SCHEDULED"]

    action = {
        "type":                     "EVENT_ACTION",
        "ruleResultToTriggerOn":    True,
        "condition":                None,
        "targetEventOid":           event_oid,
        "relativeEventOid":         relative_event_oid,
        "startDateRelativeDays":    start_date_relative,
        "startDateExpression":      start_date_expression,
        "targetEventStatus":        action_target_status,
        "lockedExpression":         None,
        "eventStatusesToTriggerOn": action_statuses,
        "closeEvent":               None,
    }

    rule = {
        "name":          f"{protocol_number}_sched_{event_oid}",
        "description":   description,
        "condition":     "$TRUE",
        "epoch":         None,
        "studyCalendar": None,
        "actions":       [action],
        "triggerType":   trigger_type,
        "triggerOID":    trigger_oid,
        "type":          rule_type,
        "schedule":      rule_schedule,
        "time":          rule_time,
        "criteria":      rule_criteria,
        "_meta": {
            "confidence":          confidence,
            "arm":                 entry["arm"],
            "window_lower_days":   entry["window_lower_days"],
            "window_upper_days":   entry["window_upper_days"],
            "conditional_trigger": entry["conditional_trigger"],
            "repeating":           entry["repeating"],
            "validation_errors":   [],
            "source_event":        entry,
        },
    }
    return rule


# ── Phase 3 helpers ───────────────────────────────────────────────────────────

def _find_armcd_source(struct_json):
    """Scan struct_json['forms'] for a form containing an ARMCD item.

    Returns a dict {event_oid, form_oid, item_oid} using the first visit in
    that form's visits_assigned, or None if ARMCD cannot be located.
    """
    for form in struct_json.get("forms", []):
        form_oid = form.get("form_id", "")
        for row in form.get("survey", []):
            if str(row.get("name", "")).upper() == "ARMCD":
                visits = form.get("visits_assigned", [])
                event_oid = visits[0] if visits else None
                # Derive item OID: I_{form_id_without_F_prefix}_{item_name}
                bare = form_oid.replace("F_", "") if form_oid.startswith("F_") else form_oid
                item_oid = f"I_{bare}_ARMCD"
                return {"event_oid": event_oid, "form_oid": form_oid, "item_oid": item_oid}
    return None


def _extract_arm_visibility_rules(struct_json, armcd_source, protocol_number):
    """Tier 3a: For each arm-specific form emit a FORM_ACTION visibility rule.

    XPath for ARMCD check is a production-derived template — mark NEEDS_REVIEW
    for validation against the XPath evaluator endpoint before deploying.
    """
    if not armcd_source:
        return [], ["Tier 3a skipped — ARMCD item not found in any form. Arm visibility rules require an ARMCD select_one item."]

    rules = []
    warnings = []
    arm_vocab = {
        "trt": "TRT", "treatment": "TRT",
        "ctrl": "CTRL", "control": "CTRL",
    }
    neutral = {"both", "all", ""}

    armcd_event = armcd_source["event_oid"]
    armcd_form  = armcd_source["form_oid"]
    armcd_item  = armcd_source["item_oid"]

    for form in struct_json.get("forms", []):
        form_oid = form.get("form_id", "")
        raw_arm  = str(form.get("arm_applicability", "BOTH")).lower().strip()
        if raw_arm in neutral:
            continue

        arm_code = arm_vocab.get(raw_arm)
        if arm_code is None:
            warnings.append(f"Tier 3a: unrecognised arm_applicability '{raw_arm}' on {form_oid} — skipped.")
            continue

        # Build one FORM_ACTION rule per (event, form) placement
        for event_oid in form.get("visits_assigned", []):
            # XPath: visible only if participant's ARMCD equals this arm code.
            # Uses ${EVENT}/${FORM}/ITEM_OID syntax — validate against XPath evaluator.
            if armcd_event:
                xpath = (
                    f"${{SE_SCREENING or {armcd_event}}}/{armcd_form}/{armcd_item} = '{arm_code}'"
                    if armcd_event != "SE_SCREENING"
                    else f"${{{armcd_event}}}/{armcd_form}/{armcd_item} = '{arm_code}'"
                )
            else:
                xpath = f"{armcd_form}/{armcd_item} = '{arm_code}'"

            rule = {
                "name": f"{protocol_number}_vis_{form_oid}_{event_oid}",
                "description": (
                    f"Hides {form_oid} in {event_oid} for participants not on arm {arm_code}. "
                    f"visibleExpression references {armcd_item} in {armcd_form}."
                ),
                "condition": "$TRUE",
                "epoch": None,
                "studyCalendar": None,
                "actions": [{
                    "type": "FORM_ACTION",
                    "ruleResultToTriggerOn": True,
                    "condition": None,
                    "targetEventOid": event_oid,
                    "targetFormOid": form_oid,
                    "requiredExpression": None,
                    "visibleExpression": xpath,
                    "editableExpression": None,
                    "targetFormStatus": None,
                }],
                "triggerType": ["FORM_STATUS_CHANGE"],
                "triggerOID": armcd_form,
                "type": None,
                "schedule": None,
                "time": None,
                "criteria": None,
                "_meta": {
                    "confidence": "NEEDS_REVIEW",
                    "arm": arm_code,
                    "window_lower_days": None,
                    "window_upper_days": None,
                    "conditional_trigger": f"ARMCD={arm_code}",
                    "repeating": False,
                    "validation_errors": [],
                    "source_event": {"form_oid": form_oid, "event_oid": event_oid, "arm_applicability": raw_arm},
                    "rule_subtype": "ARM_VISIBILITY",
                    "xpath_needs_evaluator": True,
                },
            }
            rules.append(rule)

    if not rules:
        warnings.append("Tier 3a: no arm-specific forms found — no arm visibility rules generated.")
    return rules, warnings


def _extract_dynamic_event_rules(struct_json, protocol_number):
    """Tier 3b: For each CDASH_SAFETY form emit a trigger-based Event Action
    that schedules the unscheduled event when the safety form changes status.

    HIGH confidence — no synthesized XPath in condition or action.
    """
    # Find the unscheduled event OID from timepoint_csv or scheduling
    unsch_oid = None
    for row in struct_json.get("timepoint_csv", {}).get("rows", []):
        ev = str(row.get("event", "")).upper()
        if "UNSCH" in ev:
            unsch_oid = row["event"]
            break
    if unsch_oid is None:
        for entry in struct_json.get("scheduling", []):
            ev = str(entry.get("event_oid", "")).upper()
            if "UNSCH" in ev:
                unsch_oid = entry["event_oid"]
                break

    if not unsch_oid:
        return [], ["Tier 3b skipped — no unscheduled event OID found (expected an OID containing 'UNSCH' in timepoint_csv or scheduling)."]

    rules = []
    warnings = []
    safety_categories = {"cdash_safety", "cdash safety", "safety"}

    for form in struct_json.get("forms", []):
        form_oid = form.get("form_id", "")
        category = str(form.get("form_category", "")).lower().strip()
        if category not in safety_categories:
            continue

        rule = {
            "name": f"{protocol_number}_unsch_{form_oid}",
            "description": (
                f"Schedules {unsch_oid} when {form_oid} changes status. "
                f"Only fires if {unsch_oid} is not yet scheduled (idempotency guard)."
            ),
            "condition": "$TRUE",
            "epoch": None,
            "studyCalendar": None,
            "actions": [{
                "type": "EVENT_ACTION",
                "ruleResultToTriggerOn": True,
                "condition": None,
                "targetEventOid": unsch_oid,
                "relativeEventOid": None,
                "startDateRelativeDays": None,
                "startDateExpression": "format-date(now(), '%Y-%m-%d')",
                "targetEventStatus": "SCHEDULED",
                "lockedExpression": None,
                "eventStatusesToTriggerOn": ["NOT_SCHEDULED"],
                "closeEvent": None,
            }],
            "triggerType": ["FORM_STATUS_CHANGE"],
            "triggerOID": form_oid,
            "type": None,
            "schedule": None,
            "time": None,
            "criteria": None,
            "_meta": {
                "confidence": "HIGH",
                "arm": form.get("arm_applicability", "BOTH"),
                "window_lower_days": None,
                "window_upper_days": None,
                "conditional_trigger": f"FORM_STATUS_CHANGE:{form_oid}",
                "repeating": False,
                "validation_errors": [],
                "source_event": {"form_oid": form_oid, "unsch_oid": unsch_oid},
                "rule_subtype": "DYNAMIC_EVENT",
            },
        }
        rules.append(rule)

    if not rules:
        warnings.append("Tier 3b: no CDASH_SAFETY forms found — no dynamic event rules generated.")
    return rules, warnings


def _extract_participant_routing_rules(struct_json, armcd_source, protocol_number):
    """Tier 3c: For each arm in study_calendars emit a Participant Action rule
    that routes the participant to the correct study calendar at enrollment.

    NEEDS_REVIEW — ARMCD XPath requires evaluator validation before deploying.
    Skips silently (with warning) when study_calendars is absent.
    """
    calendars = struct_json.get("study_calendars", [])
    if not calendars:
        return [], ["Tier 3c skipped — study_calendars absent. Run protocol-analysis with B-model support to generate per-arm calendars."]

    if not armcd_source:
        return [], ["Tier 3c skipped — ARMCD item not found. Participant routing rules require ARMCD to determine arm at enrollment."]

    rules = []
    warnings = []
    armcd_event = armcd_source["event_oid"]
    armcd_form  = armcd_source["form_oid"]
    armcd_item  = armcd_source["item_oid"]

    for cal in calendars:
        arm_code  = cal.get("arm_code", "")
        arm_name  = cal.get("arm_name", "")
        if not arm_code or not arm_name:
            continue

        xpath_cond = (
            f"${{{armcd_event}}}/{armcd_form}/{armcd_item} = '{arm_code}'"
            if armcd_event
            else f"{armcd_form}/{armcd_item} = '{arm_code}'"
        )

        rule = {
            "name": f"{protocol_number}_route_{arm_code}",
            "description": (
                f"Routes participants with ARMCD='{arm_code}' to study calendar '{arm_name}' at enrollment."
            ),
            "condition": xpath_cond,
            "epoch": None,
            "studyCalendar": None,
            "actions": [{
                "type": "PARTICIPANT_ACTION",
                "ruleResultToTriggerOn": True,
                "condition": None,
                "setStudyCalendar": arm_name,
                "setEpoch": None,
            }],
            "triggerType": ["PARTICIPANT_CREATED"],
            "triggerOID": None,
            "type": None,
            "schedule": None,
            "time": None,
            "criteria": None,
            "_meta": {
                "confidence": "NEEDS_REVIEW",
                "arm": arm_code,
                "window_lower_days": None,
                "window_upper_days": None,
                "conditional_trigger": f"ARMCD={arm_code}",
                "repeating": False,
                "validation_errors": [],
                "source_event": {"arm_code": arm_code, "arm_name": arm_name},
                "rule_subtype": "PARTICIPANT_ROUTING",
                "xpath_needs_evaluator": True,
            },
        }
        rules.append(rule)

    return rules, warnings


# ── Simple-rule recommendation builders ──────────────────────────────────────

def _reminder_rec(entry):
    event_oid = entry["event_oid"]
    lo = entry["window_lower_days"]
    hi = entry["window_upper_days"]
    return {
        "event_oid":   event_oid,
        "type":        "REMINDER",
        "description": (
            f"Event {event_oid} has a visit window (lower={lo}, upper={hi} days). "
            f"Set up the window manually in the study designer — Tier 1 does not "
            f"auto-build windows."
        ),
    }


def _conditional_rec(entry):
    event_oid = entry["event_oid"]
    trig = entry["conditional_trigger"]
    return {
        "event_oid":   event_oid,
        "type":        "CONDITIONAL",
        "description": (
            f"Event {event_oid} is conditionally triggered ({trig}). This requires "
            f"Tier 3 dynamic-scheduling handling — flagged for manual setup."
        ),
    }


# ── Main extraction function ─────────────────────────────────────────────────

def extract_calendar_rules(struct_json, forms_json):
    """Walk the scheduling structure and emit calendaring rule data.

    forms_json is accepted for interface parity with run_dvs_xlsx but is not
    consumed in Tier 1.
    """
    struct = struct_json if isinstance(struct_json, dict) else {}
    study_meta = struct.get("study_meta", {}) or {}
    protocol_number = (
        study_meta.get("protocol_number")
        or study_meta.get("study_id")
        or "STUDY"
    )

    warnings = []

    scheduling = struct.get("scheduling")
    has_scheduling = bool(scheduling)

    if has_scheduling:
        entries = [_normalize_scheduling_entry(e) for e in scheduling]
        force_review = False
    else:
        rows = (struct.get("timepoint_csv") or {}).get("rows") or []
        entries = [_normalize_timepoint_row(r) for r in rows]
        force_review = True
        warnings.append(
            "No 'scheduling' block found in struct_json — fell back to "
            "timepoint_csv.rows. All rules treated as index events and marked "
            "NEEDS_REVIEW (anchors and offsets are unknown from timepoints alone)."
        )

    # Drop entries with no event_oid (cannot build a rule without a target).
    valid_entries = []
    for entry in entries:
        if entry["event_oid"]:
            valid_entries.append(entry)
        else:
            warnings.append("Skipped a scheduling entry with no event_oid.")

    rules = []
    for entry in valid_entries:
        event_oid = entry["event_oid"]
        rule = _build_rule(entry, protocol_number, force_review=force_review)
        rules.append(rule)

        # Phase 2 — auto-close rule: generated when window_upper_days is set and
        # the scheduling block is present. Pattern matches production Auto Close rules:
        # RUN_ON_SCHEDULE daily at 23:00:00, criteria on the target event with
        # offset=window_upper_days, range=-1 (rolling), closeEvent=True.
        if has_scheduling and entry.get("window_upper_days") is not None:
            wud = entry["window_upper_days"]
            ac_rule = {
                "name": f"{protocol_number}_autoclose_{event_oid}",
                "description": (
                    f"Auto-closes {event_oid} at {wud} days after its start date "
                    f"(visit window upper bound)"
                ),
                "condition":     "$TRUE",
                "epoch":         None,
                "studyCalendar": None,
                "actions": [{
                    "type":                     "EVENT_ACTION",
                    "ruleResultToTriggerOn":    True,
                    "condition":                None,
                    "targetEventOid":           event_oid,
                    "relativeEventOid":         None,
                    "startDateRelativeDays":    None,
                    "startDateExpression":      None,
                    "targetEventStatus":        None,
                    "lockedExpression":         None,
                    "eventStatusesToTriggerOn": ["SCHEDULED", "DATA_ENTRY_STARTED"],
                    "closeEvent":               True,
                }],
                "triggerType":  None,
                "triggerOID":   None,
                "type":         "RUN_ON_SCHEDULE",
                "schedule":     "DAILY",
                "time":         "23:00:00",
                "criteria": {
                    "type":         "EVENT_CRITERIA",
                    "eventOid":     event_oid,
                    "eventStatuses": ["SCHEDULED", "DATA_ENTRY_STARTED"],
                    "offset":       wud,
                    "when":         "after",
                    "range":        -1,
                },
                "_meta": {
                    "confidence":          "HIGH",
                    "arm":                 entry.get("arm", "BOTH"),
                    "window_lower_days":   entry.get("window_lower_days"),
                    "window_upper_days":   wud,
                    "conditional_trigger": None,
                    "repeating":           False,
                    "validation_errors":   [],
                    "source_event":        entry,
                    "rule_subtype":        "AUTO_CLOSE",
                },
            }
            rules.append(ac_rule)

    simple_rule_recommendations = []
    for entry in valid_entries:
        if entry["window_lower_days"] is not None or entry["window_upper_days"] is not None:
            simple_rule_recommendations.append(_reminder_rec(entry))
        if entry["conditional_trigger"]:
            simple_rule_recommendations.append(_conditional_rec(entry))

    # ── Phase 3: Tier 3a / 3b / 3c ───────────────────────────────────────────
    armcd_source = _find_armcd_source(struct_json)
    if not armcd_source:
        warnings.append("ARMCD item not found — Tier 3a (arm visibility) and Tier 3c (participant routing) skipped.")

    tier3a_rules, tier3a_warnings = _extract_arm_visibility_rules(struct_json, armcd_source, protocol_number)
    tier3b_rules, tier3b_warnings = _extract_dynamic_event_rules(struct_json, protocol_number)
    tier3c_rules, tier3c_warnings = _extract_participant_routing_rules(struct_json, armcd_source, protocol_number)

    rules.extend(tier3a_rules)
    rules.extend(tier3b_rules)
    rules.extend(tier3c_rules)
    warnings.extend(tier3a_warnings)
    warnings.extend(tier3b_warnings)
    warnings.extend(tier3c_warnings)

    return {
        "study_meta":                  study_meta,
        "rules":                       rules,
        "simple_rule_recommendations": simple_rule_recommendations,
        "warnings":                    warnings,
        "has_scheduling":              has_scheduling,
        "study_calendars":             struct.get("study_calendars", []) or [],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_calendar_rules.py <struct.json> [forms.json]")
        sys.exit(1)
    struct_json = json.load(open(sys.argv[1]))
    forms_json  = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {}
    rule_data   = extract_calendar_rules(struct_json, forms_json)
    print(json.dumps(rule_data, indent=2))
