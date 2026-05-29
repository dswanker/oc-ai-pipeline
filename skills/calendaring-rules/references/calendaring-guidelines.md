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

## Production-Verified Rule Patterns (from live OC4 study)

**Index event (schedule on participant creation):**
- triggerType: ["PARTICIPANT_CREATED"], triggerOID: null
- type/schedule/time/criteria: null
- action targetEventStatus: "SCHEDULED", eventStatusesToTriggerOn: null

**Relative event (schedule N days after anchor):**
- triggerType: ["EVENT_START_DATE_CHANGED"], triggerOID: {anchor_event_oid}
- type: "RUN_ON_SCHEDULE", schedule: "DAILY", time: "00:00:00"
- criteria: {type: "EVENT_CRITERIA", eventOid: anchor, eventStatuses: ["SCHEDULED","DATA_ENTRY_STARTED","COMPLETED"], offset: N, when: "after", range: 0}
- action targetEventStatus: "SCHEDULED", eventStatusesToTriggerOn: ["NOT_SCHEDULED"]

**Auto-close (close event after window upper bound):**
- triggerType: null, triggerOID: null
- type: "RUN_ON_SCHEDULE", schedule: "DAILY", time: "23:00:00"
- criteria: {type: "EVENT_CRITERIA", eventOid: target_event, eventStatuses: ["SCHEDULED","DATA_ENTRY_STARTED"], offset: window_upper_days, when: "after", range: -1}
- action eventStatusesToTriggerOn: ["SCHEDULED","DATA_ENTRY_STARTED"], closeEvent: true

**`criteria` field semantics:**
- offset: number of days after the reference event's start date
- range: 0 = exactly on that day; -1 = rolling (any day after offset)
- eventStatuses: which statuses of the reference event qualify the participant

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
14. (OC-24343) RUN_ON_SCHEDULE rules must have a non-null criteria block with a non-null offset value.

## Valid eventStatusesToTriggerOn values
NOT_SCHEDULED, SCHEDULED, DATA_ENTRY_STARTED, COMPLETED, STOPPED, SKIPPED

## Valid targetFormStatus values
NOT_STARTED, INITIAL_DATA_ENTRY, COMPLETED
