---
name: email-change-intake
description: >
  Monitors each PS team member's Gmail inbox for inbound customer emails
  containing study design change requests. Classifies each email as
  change_request, needs_review, or not_a_change_request. In Automated
  mode posts [DESIGN_CHANGE] update directly to AI Hub and triggers
  design-change-intake. In Gatekeeper mode creates a review item on the
  Change Requests New board for the team member to approve or dismiss.
  Always routes needs_review emails to the review board regardless of mode.
  Triggered on a schedule (every 15-30 min) via POST /admin/run-email-intake.
---

# Email Change Intake Skill

## Purpose

Watch PS team member Gmail inboxes for customer study change request
emails and route them into the design-change-intake pipeline — either
directly (Automated) or via a human gatekeeper step (Gatekeeper).

No Gmail labels or manual tagging required. The team member never needs
to move or tag emails manually.

Handles OOO: when a team member is on leave, emails still surface as
review items on the Change Requests board visible to the whole PS team.
A covering colleague can action requests without inbox access.

Source content (email body + transcript) always lands on the AI Hub
board row — never on the Change Requests review board. The review board
is a decision gate only.

---

## Board & Column Reference

### OC Staff Board — 7663638790

| Purpose | Column ID |
|---------|-----------|
| Email Change Mode (status) | color_mm3zazg |
| Email Last Checked (date) | date_mm3zvx6q |

Mode labels: Automated / Gatekeeper / Off

### Change Requests New Board — 18395557554

| Purpose | Column ID |
|---------|-----------|
| Assigned (People) | project_owner |
| Status | project_status |
| Priority | project_priority |
| Company | text_mkzkeygb |
| Email Body | long_text_mm3zvw2q |
| From | text_mm3zej1p |
| AI Summary | long_text_mm3z80v1 |
| Proposed Update | long_text_mm3z9m21 |
| Review Decision | color_mm3zkh2y |
| Matched Study | text_mm3zkmkw |

Review group: group_mm3zj7yj ("Email Change Requests (AI)")
Review Decision labels: Awaiting Review / Approve / Dismiss

### AI Hub Board — 18409146946

| Purpose | Column ID |
|---------|-----------|
| Protocol Number | text_mm2hcfre |
| Change Request Transcripts | file_mm3tntz9 |
| Assigned PS member | dup__of_requester__1 |

---

## Email Classification

| Classification | Meaning | Action |
|---------------|---------|--------|
| change_request | Customer explicitly requesting specific study build changes | Route per mode |
| needs_review | Mentions study but intent ambiguous | Always → review board |
| not_a_change_request | Routine communication | Silently skip |

When in doubt, classify as needs_review.

---

## Routing Logic

| Mode | change_request + hub row found | needs_review |
|------|-------------------------------|-------------|
| Automated | Post [DESIGN_CHANGE] to AI Hub directly | Create review item |
| Gatekeeper | Create review item | Create review item |

If Automated + hub row NOT found: fall back to review item.

---

## Step 1 — Load Active PS Team Members

Query OC Staff board for rows where Email Change Mode != Off and
Employee Status = Active. Extract monday_user_id, mode, last_checked,
email address, staff_item_id.

## Step 2 — Update last_checked

Immediately write today's date to date_mm3zvx6q on the OC Staff row
before fetching emails so subsequent runs don't reprocess.

## Step 3 — Fetch Unread Emails

Use Gmail MCP connector per team member. Search: is:unread -from:me
[after:{last_checked_date}]. Requires OAuth token at
/data/gmail_sessions/{monday_user_id}.json. If token missing: skip
member and send bell notification with activation link.

## Step 4 — Classify Each Email

Call claude-sonnet-4-20250514 (max_tokens=500, temperature=0).
Return JSON: classification, protocol_id, customer_name, summary,
changes_mentioned. If classification fails: treat as needs_review.

## Step 5 — Find AI Hub Row

Search board 18409146946 for item matching protocol_id in
text_mm2hcfre column. Case-insensitive, strip hyphens and spaces.

## Step 6 — Post to AI Hub (Automated + change_request)

Build update body:
[DESIGN_CHANGE] [SOURCE_TYPE:email] [PROTOCOL:{protocol_id}]

From: {from_name} <{from_email}>
Subject: {subject}

{email_body}

Post as item update on AI Hub row → triggers /webhook/design-change.
Also upload email body as .txt transcript to file_mm3tntz9.

## Step 7 — Create Review Item (Gatekeeper or needs_review)

Create item in group_mm3zj7yj on board 18395557554.
Populate: Assigned=member, Status=Ready To Start,
Priority=High(needs_review)/Normal(change_request),
Email Body, From, AI Summary, Proposed Update, Review Decision=Awaiting Review,
Matched Study=protocol_id.
Send bell notification to member. Post item update for email notification.

## Step 8 — Handle Review Decision

Called from /webhook/email-change-decision when Review Decision changes.
Approve: fetch Proposed Update from item, find AI Hub row, post update,
upload transcript, set item Status=Done.
Dismiss: set item Status=Done, no AI Hub action.

---

## pipeline.py Integration

Add to pipeline.py:
async def run_email_change_intake(member_id=None):
    from scripts.email_change_intake import run_email_change_intake as _run
    return await _run(member_id)

Add to main.py:

@app.post("/admin/run-email-intake")
async def run_email_intake_route(request, background_tasks):
    # Auth check X-Admin-Secret
    # Extract optional member_id from body
    # background_tasks.add_task(run_email_change_intake, member_id)
    pass

@app.post("/webhook/email-change-decision")
async def email_change_decision(request, background_tasks):
    # Extract item_id and decision_label from Monday webhook payload
    # Call handle_review_decision(item_id, decision_label)
    pass

---

## Schedule

The skill runs on an **hourly schedule** via a Monday.com automation
rather than a Railway cron job. This keeps scheduling inside the
existing Monday infrastructure with built-in retry and per-member
pause control (set Email Change Mode to Off to stop polling for any
team member without touching infrastructure).

### Monday.com automation setup

On the OC Staff board (7663638790), create one automation:
  Trigger: Every hour
  Action:  Send a webhook to
           https://{RAILWAY_PUBLIC_DOMAIN}/admin/run-email-intake
           with headers: X-Admin-Secret: {ADMIN_SECRET}
           and body: {}

This fires the endpoint once per hour for all active members.
To run for a single member only, pass {"member_id": "{monday_user_id}"}
in the body.

Hourly cadence is appropriate for study build change requests — sub-hour
response times are not required and hourly polling keeps Gmail API
usage well within quota (24 calls per member per day).

---

## Implementation

skills/email-change-intake/
├── SKILL.md
├── references/
│   └── gmail-auth-setup.md
└── scripts/
    └── email_change_intake.py

---

## Key Constraints

- Never auto-post needs_review emails — always route to review board
- Never read email attachments or sent mail
- Source content always lands on AI Hub — review board is gate only
- Team member never needs to manually move transcripts
- Gmail OAuth tokens stored at /data/gmail_sessions/{monday_user_id}.json
- PS team member does NOT interact with review board for manual triggers
  (meeting notes, pasted emails) — only for emails detected automatically
