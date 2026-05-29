# OC4 Calendaring Rule Guidelines

## Rule JSON Structure

Every rule has: metadata (name, description), a "when" (triggerType + triggerOID), a condition (XPath), and actions.

```json
{
  "name": "STUDY_sched_SE_BASELINE",
  "description": "Schedules the SE_BASELINE event on participant creation",
  "condition": "$TRUE",
  "epoch": null,
  "studyCalendar": null,
  "actions": [{
    "type": "EVENT_ACTION",
    "ruleResultToTriggerOn": true,
    "condition": null,
    "targetEventOid": "SE_BASELINE",
    "relativeEventOid": "SE_SCREENING",
    "startDateRelativeDays": 1,
    "startDateExpression": null,
    "targetEventStatus": null,
    "lockedExpression": null,
    "eventStatusesToTriggerOn": ["NOT_SCHEDULED"],
    "closeEvent": null
  }],
  "triggerType": ["PARTICIPANT_CREATED"],
  "triggerOID": null
}
```

## Two Event Action Styles (mutually exclusive — never mix):

**Helper style (preferred for Tier 1):** relativeEventOid + startDateRelativeDays
**XPath style:** startDateExpression only (e.g. format-date(now(), '%Y-%m-%d') for index events)

Error 4 fires if you supply all three.

## Trigger Types (use specific triggers, never null/async)

- PARTICIPANT_CREATED — for pre-scheduling all events at enrollment (Tier 1)
- EVENT_STATUS_CHANGE — for dynamic/conditional events
- FORM_STATUS_CHANGE=COMPLETED — preferred over USER_CLOSES_FORM when conditions read item values

## Rule Naming Convention (idempotency)

Name format: {protocol_number}_sched_{event_oid}
This namespacing ensures Update Rule never silently clobbers a different study's rules.

## XPath Performance

Structure conditions high in the chain: rule condition → ruleResultToTriggerOn → action condition → field expression.
XPath evaluation costs 220–500ms per participant. Minimize expression count.

## Circuit Breaker

Same rule action firing ~10–15×/min for one participant disables that participant's calendaring.
Always include idempotency guards: use eventStatusesToTriggerOn: ["NOT_SCHEDULED"] for scheduling actions so they only fire once.

## 13 Rule Upload Error Messages

1. Rule condition cannot be empty (null or empty rule condition)
2. Rule name cannot be empty (null or empty rule name)
3. Rule action type cannot be null or empty
4. Event Action rules can either use a startDateExpression or use a combination of relativeEventOid and startDateRelativeDays, but you can't use all three within an Event Action rule
5. The epoch specified in the rule does not exist
6. The study calendar specified in the rule does not exist
7. Rule doesn't exist with given uuid (update mode only)
8. No matching rules found for this given study (update mode only)
9. The requested action cannot be performed as the user does not have the required permission
10. Study UUID in the url doesn't match the one in the request body
11. Rule event status has invalid value, accepted values are NOT_SCHEDULED, SCHEDULED, DATA_ENTRY_STARTED, COMPLETED, STOPPED, SKIPPED
12. Rule form status has invalid value, accepted values are NOT_STARTED, INITIAL_DATA_ENTRY, COMPLETED
13. Rule JSON is invalid (any JSON error not covered above)

## Valid eventStatusesToTriggerOn values
NOT_SCHEDULED, SCHEDULED, DATA_ENTRY_STARTED, COMPLETED, STOPPED, SKIPPED

## Valid targetFormStatus values
NOT_STARTED, INITIAL_DATA_ENTRY, COMPLETED
