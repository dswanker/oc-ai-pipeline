---
name: design-change-intake
description: >
  Accepts unstructured text (meeting notes, email body, or voice transcript)
  describing study design changes for an OpenClinica 4 study build. Extracts
  structured change requests, applies them to the Study Specification XLSX on
  the AI Hub Monday.com board, saves the source transcript, notifies the
  assigned PS team member via Monday.com bell notification and email, and
  routes any changes flagged as conventions to the Convention Rulebook board
  for OC team review and explicit approval before the conventions engine picks
  them up. Triggered automatically — no human action required to start the
  intake. The human reviews and approves or corrects after the fact.
---

# Design Change Intake Skill

## Purpose

Convert unstructured design change input (meeting notes, email, transcript)
into four automatic outcomes:
1. Updated Study Specification XLSX posted to the AI Hub Monday.com board
2. Source transcript saved to the same board row for human audit
3. Monday.com bell notification + email to the assigned PS team member
4. Proposed convention rows on the Convention Rulebook board (OC team approval
   required before any convention becomes active in the engine)

This skill modifies ONLY the Study Specification XLSX (column file_mm2n3x71).
It never touches edc-builder output files, XLSForms, DVS files, or any
other pipeline artifact. The edc-builder re-runs from the updated spec —
that is the correct downstream flow. A change not reflected in the spec
will not survive the next edc-builder run.

---

## Before You Begin

Always read `references/spec-xls-format.md` before modifying any spec XLSX.
It documents the exact tab structure, column order, and safe modification
rules as produced by generate_study_spec_xlsx.py.

---

## Board & Column Reference

### AI Hub Board — 18409146946

| Purpose | Column ID |
|---------|-----------|
| Protocol Specification (xlsx) — working spec | file_mm2gjqgx |
| Change Request Transcripts | file_mm3tntz9 |
| Assigned PS team member (People) | dup__of_requester__1 |
| Protocol Number | text_mm2hcfre |
| Client / Customer | text7__1 |

### Convention Rulebook Board — 18411236453

| Purpose | Column ID |
|---------|-----------|
| Protocol Number | text_mm2yzxg3 |
| Review Status | color_mm2yp992 |
| Submit Trigger | color_mm2y41kb |
| Source Type | color_mm3xbjjv |
| Scope | color_mm3xx99e |
| Proposed Convention (plain text) | long_text_mm3x37ty |
| Source Project Link | link_mm3xyxk3 |

Convention group for customer-proposed items: group_mm3xt80m
("Customer-Proposed Conventions")

---

## Inputs

JSON payload:
```json
{
  "source_type": "meeting_notes | email | transcript",
  "source_text": "...full pasted text...",
  "protocol_hint": "CRS-136"
}
```

When triggered from a Monday.com webhook update, the payload is extracted
from the update body following the [DESIGN_CHANGE] token.
When called from email-change-intake, it is passed directly.

---

## Step 1 — Parse the Source Text

Call Claude (claude-sonnet-4-20250514, max_tokens=2000, temperature=0)
with this system prompt:

```
You are an expert OpenClinica EDC study build analyst. Extract all design
change requests from the provided text.

For each change identify:
- form: CRF form name or CDASH domain (e.g. "Demographics", "AE", "VS")
- field: specific field or element name
- change_type: one of add_field | remove_field | rename | change_validation |
  change_visit | change_choices | change_logic | other
- description: clear unambiguous description of what changes
- rationale: why this change is needed (null if not stated)
- is_convention: true if the speaker explicitly flags this as a convention
  using phrases: "this is a convention", "always do this", "add as convention",
  "for all our studies", "for this study always"
- convention_scope: "study" | "customer" | null

Also extract:
- protocol_id: protocol or study identifier (e.g. CRS-136, PrTK05)
- study_name: human-readable study name if mentioned
- summary: one-sentence summary of all changes

Return ONLY valid JSON, no markdown, no preamble:
{
  "protocol_id": "string or null",
  "study_name": "string or null",
  "summary": "string",
  "changes": [
    {
      "form": "string",
      "field": "string",
      "change_type": "string",
      "description": "string",
      "rationale": "string or null",
      "is_convention": false,
      "convention_scope": null
    }
  ]
}
```

If parsing fails or returns invalid JSON: abort, log error as item update,
return {"status": "error", "message": "Parse failed: {raw_response[:200]}"}.

---

## Step 2 — Identify the Board Row

Use parsed.protocol_id first, fall back to protocol_hint.
If neither: abort with "Cannot identify study row: no protocol ID found."

Query AI Hub board for matching row:
```graphql
query {
  boards(ids: [18409146946]) {
    items_page(limit: 200) {
      items {
        id name updated_at
        column_values(ids: ["text_mm2hcfre", "dup__of_requester__1", "text7__1"]) {
          id text value
        }
      }
    }
  }
}
```

Match text_mm2hcfre (Protocol Number) case-insensitively against
protocol_id. If multiple match, use most recently updated_at.
If none match: abort with "No AI Hub board row found matching: {protocol_id}"

Store: item_id, assignee_value (raw JSON), customer_name.

---

## Step 3 — Download the Current Spec XLSX

Query Monday assets for the item, find the most recently uploaded .xlsx
asset associated with column file_mm2n3x71, and download via public_url.

```python
query = """
query($i: [ID!]) {
  items(ids: $i) {
    assets { id name url public_url created_at }
  }
}
"""
```

Download using the same pattern as other pipeline file downloads
(S3 URLs: no auth header; Monday URLs: Authorization header).

If no spec XLSX found: abort with
"No spec XLSX on board row for {protocol_id}. Cannot apply changes."

---

## Step 4 — Apply Changes to the Spec XLSX

> Boundary rule: This step modifies ONLY the study spec XLSX.
> Never open, read, or write any edc-builder output file.

Use openpyxl to open the downloaded bytes (BytesIO).

Read the spec-xls-format reference before writing any cell.
Locate all columns by reading the header row (row 3 on survey tabs,
row 2 on choices tabs) — never assume fixed column positions.

For each change in parsed.changes:

### Locating the form

Try match strategy from spec-xls-format.md in order:
1. Exact case-insensitive tab prefix match
2. form_title match in {FORMID}_settings row 3 col B
3. CDASH domain abbreviation lookup
4. Partial substring match
5. Unresolved: log to CHANGE_LOG, continue to next change

### add_field
- Open {FORMID}_survey tab
- Find last data row (first empty row after row 3)
- Append new row with:
  - ACTION = ADD
  - type = infer from description (text/integer/date/select_one/etc.)
  - name = snake_case version of field name
  - label = field label as stated in description
  - NOTES_FOR_AI = "Added by design-change-intake: {description}"
  - All other columns = blank
- Log: "Added field '{field}' to {form}_survey row {n} — verify type and validation"

### remove_field
- Open {FORMID}_survey tab
- Find row where name column matches field (case-insensitive)
- Set ACTION = DELETE on that row
- Set NOTES_FOR_AI = "Marked for deletion by design-change-intake: {description}"
- Do NOT delete the row
- Log: "Marked '{field}' ACTION=DELETE in {form}_survey row {n}"

### rename
- Open {FORMID}_survey tab
- Find row where name or label matches field
- Update label column value
- Set NOTES_FOR_AI = "Label renamed by design-change-intake: {description}"
- Log: "Renamed label for '{field}' in {form}_survey"

### change_validation
- Open {FORMID}_survey tab
- Find field row
- Update constraint and/or constraint_message columns
- Set NOTES_FOR_AI = "Validation updated by design-change-intake: {description}"
- Log: "Updated validation for '{field}' in {form}_survey"
- If constraint is described in natural language (not XPath): leave constraint
  blank, put full description in NOTES_FOR_AI, log as needing human XPath authoring

### change_choices
- Open {FORMID}_choices tab
- Find rows where list_name matches the field's choice list name
- New choices: append row with ACTION=ADD, list_name, label, name
- Removed choices: set ACTION=DELETE on matching row
- Log: "Updated choices for '{field}' list in {form}_choices"

### change_visit
- Open TIMEPOINTS sheet
- Add a NOTES_FOR_AI-style note as a new row at end of TIMEPOINTS data
  with the requested change described in plain text, prefixed "DESIGN_CHANGE_REQUEST:"
- Log: "Visit change for '{field}' noted in TIMEPOINTS — requires manual schedule update"

### change_logic
- Open {FORMID}_survey tab
- Find field row
- If description contains explicit XPath: update relevant column
- If natural language only: update NOTES_FOR_AI with full description,
  leave relevant column as-is
- Log: "Logic change for '{field}' in {form}_survey — {'XPath updated' or 'natural language — needs human XPath authoring'}"

### other
- Open {FORMID}_survey tab if form was identified, else INDEX sheet
- Find or create a NOTES_FOR_AI cell on the last occupied row
- Append description prefixed "DESIGN_CHANGE_REQUEST:"
- Log: "Unstructured change noted on {form or INDEX}: {description}"

### After all changes — write CHANGE_LOG

Create CHANGE_LOG sheet if absent (append after last existing sheet).
Add one row per change with columns:
timestamp, run_id, change_type, form_id, field_name, description,
action_taken, resolved (true/false), log_note, source_type, source_filename

---

## Step 5 — Upload Updated Spec XLSX

```python
import io
output = io.BytesIO()
wb.save(output)
updated_bytes = output.getvalue()

timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
filename = f"{protocol_id}_Study_Spec_Updated_{timestamp}.xlsx"
await upload_file(item_id, "file_mm2n3x71", filename, updated_bytes)
```

---

## Step 6 — Save Source Transcript

```python
transcript_content = f"""Design Change Request Transcript
=================================
Source Type : {source_type}
Protocol    : {protocol_id}
Received    : {timestamp_utc}
Summary     : {parsed_summary}
Changes     : {len(changes)}

--- SOURCE TEXT ---
{source_text}

--- CHANGES EXTRACTED ---
{json.dumps(changes, indent=2)}
"""
filename = f"{protocol_id}_ChangeRequest_{timestamp}.txt"
await upload_file(item_id, "file_mm3tntz9", filename,
                  transcript_content.encode("utf-8"))
```

---

## Step 7 — Notify the Assigned PS Team Member

### Extract assignee user ID
Parse dup__of_requester__1 column value:
```python
col_value = json.loads(assignee_raw_value)
assignee_id = col_value["personsAndTeams"][0]["id"]
```
Then query: `query { users(ids: [assignee_id]) { email name } }`

### Monday.com bell notification
```graphql
mutation {
  create_notification(
    user_id: {assignee_id},
    target_id: {item_id},
    text: "Updated spec posted for {protocol_id} — {n} change(s) applied from {source_type_label}. Source transcript saved. Review CHANGE_LOG sheet in updated spec.",
    target_type: Project
  ) { text }
}
```

### Email (via item update — Monday emails assignee)
```graphql
mutation {
  create_update(
    item_id: {item_id},
    body: "Design Change Update — {protocol_id}\n\n{n} change(s) applied to Study Spec XLSX from {source_type_label}.\n\nChanges:\n{bulleted_list}\n\nSource transcript saved to Change Request Transcripts column.\nReview CHANGE_LOG sheet in updated spec and correct any misapplied changes."
  ) { id }
}
```

source_type_label map: meeting_notes → "meeting notes",
email → "email", transcript → "voice transcript"

---

## Step 8 — Route Convention Proposals

For each change where is_convention == true:

Scope label: convention_scope "study" → "Study", "customer" → "Customer",
null → default "Study" and note in convention text.

```python
convention_name = f"[PROPOSED] {change.form} / {change.field} — {protocol_id}"
source_url = f"https://openclinica-customerfirst.monday.com/boards/18409146946/pulses/{item_id}"

# Create row in Convention Rulebook board, group group_mm3xt80m
mutation = """
mutation ($col: JSON!) {
  create_item(
    board_id: 18411236453,
    group_id: "group_mm3xt80m",
    item_name: "{convention_name}",
    column_values: $col
  ) { id }
}
"""
col_values = {
  "text_mm2yzxg3":    protocol_id,
  "color_mm2yp992":   {"label": "Submitted"},
  "color_mm3xbjjv":   {"label": "Customer Proposed"},
  "color_mm3xx99e":   {"label": scope_label},
  "long_text_mm3x37ty": {"text": f"{change.description}\n\nRationale: {change.rationale or 'Not stated'}\n\nSource: {source_type_label} for {protocol_id}"},
  "link_mm3xyxk3":    {"url": source_url, "text": f"AI Hub — {protocol_id}"}
}
```

Then post an item update on the new convention row notifying OC team.

Convention rows are always created with Review Status = "Submitted".
Nothing is written to conventions.json until explicit OC team approval
via the Submit Trigger (color_mm2y41kb) workflow.

---

## Step 9 — Return Summary

```json
{
  "status": "success",
  "protocol_id": "CRS-136",
  "item_id": "12345678",
  "changes_applied": 4,
  "changes_unresolved": 1,
  "spec_uploaded": true,
  "transcript_saved": true,
  "assignee_notified": true,
  "conventions_proposed": 1,
  "change_log": [...]
}
```

Post this summary as an item update on the AI Hub board row.

---

## Error Handling

| Condition | Action |
|-----------|--------|
| No protocol ID | Abort before any Monday API calls |
| No matching board row | Abort |
| No spec XLSX on row | Abort |
| Parse fails (non-JSON) | Abort, log raw response first 500 chars |
| Form not found in spec | Log unresolved, continue remaining changes |
| Field not found in form | Log unresolved, continue |
| Upload fails | Retry once (5s), then log and continue |
| Notification fails | Log warning, do not abort — spec upload is the priority |
| Convention row creation fails | Log warning, do not abort |

Never abort after spec changes have been applied without uploading the
updated file. A missed notification is recoverable; a lost spec update is not.

---

## Trigger

This skill is triggered automatically — no human action starts it.

Path 1 — Monday.com webhook:
A new item update on an AI Hub board row whose body starts with [DESIGN_CHANGE].
The webhook fires immediately; pipeline.py routes to run_design_change_intake().

Path 2 — email-change-intake skill:
email-change-intake detects a qualifying email and calls this skill directly.

Human role is review-only after the fact:
- Spec is already updated when notification arrives
- Assignee reviews CHANGE_LOG in the updated spec
- Accepts as-is, or corrects manually, or posts a comment
- Convention proposals: OC team reviews Convention Rulebook board and approves or rejects

---

## pipeline.py Integration

Add to monday_client.py COL dict:
```python
"change_transcripts": "file_mm3tntz9",
"assignee":           "dup__of_requester__1",
```

Add to pipeline.py STATUS dict:
```python
"change_intake_running":  "Change Intake Running",
"change_intake_complete": "Change Intake Complete",
"change_intake_failed":   "Change Intake Failed",
```

Add to prompts.py:
```python
DESIGN_CHANGE_PROMPT = """You are running the design-change-intake skill.
Source type: {source_type}
Protocol hint: {protocol_hint}
Read references/spec-xls-format.md before modifying any spec XLSX.
Source text:
{source_text}
Return result summary:
===JSON_START===
[base64 encoded JSON summary bytes]
===JSON_END===
"""
```

New handler in pipeline.py:
```python
async def run_design_change_intake(item_id, source_type, source_text,
                                    protocol_hint=None):
    await set_status(item_id, COL["pipeline_status"],
                     STATUS["change_intake_running"])
    await append_log(item_id, "Design change intake started.")
    response = await run_skill(
        DESIGN_CHANGE_PROMPT.format(
            source_type=source_type,
            protocol_hint=protocol_hint or "",
            source_text=source_text
        )
    )
    summary_bytes = extract_b64(response, "JSON")
    if summary_bytes:
        summary = json.loads(summary_bytes.decode())
        await append_log(item_id,
            f"Design change intake complete. "
            f"{summary.get('changes_applied',0)} changes applied, "
            f"{summary.get('conventions_proposed',0)} conventions proposed.")
        await set_status(item_id, COL["pipeline_status"],
                         STATUS["change_intake_complete"])
    else:
        await append_log(item_id, "Design change intake: no summary returned.")
        await set_status(item_id, COL["pipeline_status"],
                         STATUS["change_intake_failed"])
```

---

## Implementation

Skill logic lives in scripts/design_change_intake.py.

```
skills/design-change-intake/
├── SKILL.md
├── references/
│   └── spec-xls-format.md
└── scripts/
    └── design_change_intake.py
```

---

## Key Constraints

- Never auto-commit conventions — always Submitted status, never write to
  conventions.json without explicit OC team approval
- Spec-only boundary — never modify any file except file_mm2n3x71
- Scope guardrail — customers may only propose Study or Customer conventions;
  Global scope is OC team only and must not be created by this skill
- No PII in convention rows — convention text describes the structural pattern,
  not patient data
- One spec version per run — single timestamped XLSX upload per intake run
