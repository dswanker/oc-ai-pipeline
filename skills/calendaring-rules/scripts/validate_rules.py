"""
validate_rules.py — Static pre-flight validator for OC4 calendaring rules

Encodes the statically-checkable subset of the 13 documented rule-upload error
messages. Runtime-only errors (epoch/calendar existence, permissions, study
UUID match, update-mode lookups) depend on the live OC instance and are skipped
here — they are noted in validation_summary.runtime_only_checks.

Pure stdlib, no I/O inside functions.
"""

import json
import sys


VALID_EVENT_STATUSES = {
    "NOT_SCHEDULED", "SCHEDULED", "DATA_ENTRY_STARTED",
    "COMPLETED", "STOPPED", "SKIPPED",
}
VALID_FORM_STATUSES = {"NOT_STARTED", "INITIAL_DATA_ENTRY", "COMPLETED"}

# Error messages, verbatim from the 13 documented upload errors.
ERR_NAME_EMPTY      = "Rule name cannot be empty (null or empty rule name)"
ERR_CONDITION_EMPTY = "Rule condition cannot be empty (null or empty rule condition)"
ERR_ACTION_TYPE     = "Rule action type cannot be null or empty"
ERR_MIX_DATE_STYLES = (
    "Event Action rules can either use a startDateExpression or use a "
    "combination of relativeEventOid and startDateRelativeDays, but you can't "
    "use all three within an Event Action rule"
)
ERR_EVENT_STATUS = (
    "Rule event status has invalid value, accepted values are NOT_SCHEDULED, "
    "SCHEDULED, DATA_ENTRY_STARTED, COMPLETED, STOPPED, SKIPPED"
)
ERR_FORM_STATUS = (
    "Rule form status has invalid value, accepted values are NOT_STARTED, "
    "INITIAL_DATA_ENTRY, COMPLETED"
)
ERR_INVALID_JSON = "Rule JSON is invalid (any JSON error not covered above)"

# Runtime-only errors (5–10) — cannot be checked without a live OC instance.
RUNTIME_ONLY_CHECKS = [
    "The epoch specified in the rule does not exist",
    "The study calendar specified in the rule does not exist",
    "Rule doesn't exist with given uuid (update mode only)",
    "No matching rules found for this given study (update mode only)",
    "The requested action cannot be performed as the user does not have the required permission",
    "Study UUID in the url doesn't match the one in the request body",
]


def _is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _validate_one_rule(rule):
    """Return a list of error-message strings for a single rule dict."""
    errors = []

    # Check 1 (error 2): name must be a non-empty string.
    if not _is_nonempty_str(rule.get("name")):
        errors.append(ERR_NAME_EMPTY)

    # Check 2 (error 1): condition must be a non-empty string.
    if not _is_nonempty_str(rule.get("condition")):
        errors.append(ERR_CONDITION_EMPTY)

    actions = rule.get("actions") or []
    for action in actions:
        if not isinstance(action, dict):
            errors.append(ERR_ACTION_TYPE)
            continue

        # Check 3 (error 3): each action must have a non-empty "type".
        if not _is_nonempty_str(action.get("type")):
            errors.append(ERR_ACTION_TYPE)

        # Check 4 (error 4): EVENT_ACTION must not set all three date fields.
        if action.get("type") == "EVENT_ACTION":
            if (action.get("relativeEventOid") is not None
                    and action.get("startDateRelativeDays") is not None
                    and action.get("startDateExpression") is not None):
                errors.append(ERR_MIX_DATE_STYLES)

        # Check 5 (error 11): eventStatusesToTriggerOn values must be valid.
        for status in (action.get("eventStatusesToTriggerOn") or []):
            if status not in VALID_EVENT_STATUSES:
                errors.append(ERR_EVENT_STATUS)
                break

        # Check 6 (error 12): targetFormStatus (if present) must be valid.
        form_status = action.get("targetFormStatus")
        if form_status is not None and form_status not in VALID_FORM_STATUSES:
            errors.append(ERR_FORM_STATUS)

    # Check 7 (error 13): the uploadable rule must serialize to valid JSON.
    uploadable = {k: v for k, v in rule.items() if k != "_meta"}
    try:
        json.dumps(uploadable)
    except (ValueError, TypeError):
        errors.append(ERR_INVALID_JSON)

    # Check 8 (OC-24343): RUN_ON_SCHEDULE rules must have non-null criteria with non-null offset
    if rule.get("type") == "RUN_ON_SCHEDULE":
        criteria = rule.get("criteria")
        if criteria is None:
            errors.append(
                "RUN_ON_SCHEDULE rule has null criteria — offset cannot be null "
                "(OC-24343). Add an EVENT_CRITERIA block."
            )
        elif criteria.get("offset") is None:
            errors.append(
                "RUN_ON_SCHEDULE criteria.offset is null (OC-24343)."
            )

    return errors


def validate_rules(rule_data):
    """Statically validate every rule; populate _meta.validation_errors and a
    top-level validation_summary. Mutates and returns rule_data."""
    rules = rule_data.get("rules", []) if isinstance(rule_data, dict) else []

    summary_errors = []
    failed = 0

    for rule in rules:
        errors = _validate_one_rule(rule)
        meta = rule.setdefault("_meta", {})
        meta["validation_errors"] = errors
        if errors:
            failed += 1
            summary_errors.append({
                "rule_name": rule.get("name"),
                "errors":    errors,
            })

    total = len(rules)
    rule_data["validation_summary"] = {
        "total":               total,
        "passed":              total - failed,
        "failed":              failed,
        "errors":              summary_errors,
        "runtime_only_checks": RUNTIME_ONLY_CHECKS,
    }
    return rule_data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_rules.py <rule_data.json>")
        sys.exit(1)
    rule_data = json.load(open(sys.argv[1]))
    validated = validate_rules(rule_data)
    print(json.dumps(validated, indent=2))
