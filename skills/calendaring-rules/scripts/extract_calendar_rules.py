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

    action = {
        "type":                    "EVENT_ACTION",
        "ruleResultToTriggerOn":   True,
        "condition":               None,
        "targetEventOid":          event_oid,
        "relativeEventOid":        relative_event_oid,
        "startDateRelativeDays":   start_date_relative,
        "startDateExpression":     start_date_expression,
        "targetEventStatus":       None,
        "lockedExpression":        None,
        "eventStatusesToTriggerOn": ["NOT_SCHEDULED"],
        "closeEvent":              None,
    }

    rule = {
        "name":          f"{protocol_number}_sched_{event_oid}",
        "description":   description,
        "condition":     "$TRUE",
        "epoch":         None,
        "studyCalendar": None,
        "actions":       [action],
        "triggerType":   ["PARTICIPANT_CREATED"],
        "triggerOID":    None,
        "_meta": {
            "confidence":         confidence,
            "arm":                entry["arm"],
            "window_lower_days":  entry["window_lower_days"],
            "window_upper_days":  entry["window_upper_days"],
            "conditional_trigger": entry["conditional_trigger"],
            "repeating":          entry["repeating"],
            "validation_errors":  [],
            "source_event":       entry,
        },
    }
    return rule


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

    rules = [_build_rule(e, protocol_number, force_review=force_review)
             for e in valid_entries]

    simple_rule_recommendations = []
    for entry in valid_entries:
        if entry["window_lower_days"] is not None or entry["window_upper_days"] is not None:
            simple_rule_recommendations.append(_reminder_rec(entry))
        if entry["conditional_trigger"]:
            simple_rule_recommendations.append(_conditional_rec(entry))

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
