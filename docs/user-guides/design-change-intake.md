# Design Change Intake — User Guide

**Version:** 1.0
**Last Updated:** 2026-06-03
**Skill:** `design-change-intake`
**Audience:** OpenClinica Professional Services team members

---

## What This Does

The Design Change Intake skill lets you submit study design change requests
in plain English — from meeting notes, a forwarded email, or a voice
transcript — and have the system automatically:

1. Apply the changes to the Study Specification XLSX on the project's
   Monday.com row
2. Save your original source text as a transcript for audit purposes
3. Notify you (the assigned team member) via Monday.com bell notification
   and email
4. Route any changes flagged as conventions to the Convention Rulebook
   board for OC team review

You do not need to open Excel, find the right row in the spec, or manually
upload anything. The system handles all of that. Your job is to review
what was changed after the fact and correct anything that needs adjusting.

---

## Who This Is For

- **PS team members** managing active study builds who receive change
  requests from customers via calls, emails, or meetings
- **PS team leads** reviewing build quality and convention patterns

---

## How to Trigger It

Go to the AI Hub board in Monday.com and find the row for the study you
want to update. Post a new **item update (comment)** on that row.

The update body must start with `[DESIGN_CHANGE]`. Everything after that
tag is your source text — paste in your meeting notes, email body, or
transcript as-is.

### Basic format

```
[DESIGN_CHANGE]
Your meeting notes, email, or transcript text goes here.
Claude will read this and extract the changes automatically.
```

### With optional metadata tags

You can add two optional tags immediately after `[DESIGN_CHANGE]` to
help the system identify the source type and the study:

```
[DESIGN_CHANGE] [SOURCE_TYPE:meeting_notes] [PROTOCOL:CRS-136]
Your text here...
```

**SOURCE_TYPE options:**
| Tag | When to use |
|-----|-------------|
| `[SOURCE_TYPE:meeting_notes]` | Notes from a customer call or review session |
| `[SOURCE_TYPE:email]` | Forwarded or pasted email body |
| `[SOURCE_TYPE:transcript]` | Voice memo or recorded session transcript |

If you omit `SOURCE_TYPE`, it defaults to `meeting_notes`.

**PROTOCOL tag:**
If the protocol number isn't mentioned anywhere in your text, add
`[PROTOCOL:CRS-136]` (replace with your actual protocol number) so
the system knows which board row to update. If the protocol number
appears naturally in your text, you can skip this tag.

---

## Step-by-Step Walkthrough

### Step 1 — Prepare your text

Copy your meeting notes, email, or transcript. You do not need to
clean it up or reformat it. The system reads natural language.

**Good examples of text that works well:**

From meeting notes:
```
Call with Pacira team 2026-06-03. They want to remove the AESEV
dropdown from the Adverse Events form and replace it with a free-text
field for severity description. Also, the demographics DOB field
should have a constraint that the subject must be at least 18 years
old. Finally, they said they always want the DOB age constraint on
all their studies — add this as a convention.
```

From an email:
```
Hi Dan, following up from our call — a few changes for CRS-136:
1. Add a new field to the VS form for oxygen saturation (SpO2),
   integer type, range 0-100.
2. Rename "Adverse Event Start Date" to "AE Onset Date" on the AE form.
3. Remove the "AE resolved" field — we track this in disposition.
Thanks, Sarah
```

### Step 2 — Open the Monday.com AI Hub board

Navigate to the AI Hub board and find the row for your study.

### Step 3 — Post the update

Click the speech bubble icon on the row to open the updates panel.
Paste your text with the `[DESIGN_CHANGE]` prefix at the top.
Click the send button.

**Example:**
```
[DESIGN_CHANGE] [SOURCE_TYPE:meeting_notes] [PROTOCOL:CRS-136]
Call with Pacira team 2026-06-03. They want to remove the AESEV
dropdown from the Adverse Events form and replace it with a free-text
field for severity description...
```

### Step 4 — Wait for processing (typically 2-5 minutes)

You will see the pipeline status column on the row change to
**"Change Intake Running"**. When complete it will show
**"Change Intake Complete"**.

You will also receive:
- A **Monday.com bell notification** summarising how many changes
  were applied
- An **email** (via item update) listing each change with its status

### Step 5 — Review the updated spec

Open the **Study Spec XLS** column on the board row and download
the updated file. It will have a timestamped filename, e.g.:
`CRS-136_Study_Spec_Updated_20260603_1430.xlsx`

Open the **CHANGE_LOG** sheet (last tab in the workbook). This lists
every change the system made, whether it was resolved or flagged for
human review, and any notes.

**Resolved = true:** The change was applied directly. Verify it looks
correct.

**Resolved = false:** The system couldn't apply it automatically —
it added a note in the spec explaining why (e.g. the field wasn't
found, or the change requires XPath logic that needs human authoring).
These need your attention.

### Step 6 — Check the transcript

The **Change Request Transcripts** column on the board row will have
a new .txt file, e.g.:
`CRS-136_ChangeRequest_20260603_1430.txt`

This is your original source text plus a structured summary of every
change that was extracted. Download it if you want to compare what
you submitted to what was applied.

### Step 7 — Correct anything as needed

If a change was misapplied or misunderstood, edit the spec XLSX
directly and re-upload it to the Study Spec XLS column. The next
edc-builder run will pick up your corrections.

---

## Flagging a Convention

If a customer says something like:

- *"We always want this on all our studies"*
- *"This is a convention for us"*
- *"Add this as a convention"*
- *"For this study, always do X"*

Include that language naturally in your text. The system will detect it
and automatically create a **proposed convention row** on the Convention
Rulebook board for OC team review.

The convention is **not active** until an OC team member explicitly
approves it on the Convention Rulebook board. You will see a bell
notification when a convention proposal is created.

### Convention scope

The system will try to detect scope from your language:

| Phrase | Scope assigned |
|--------|---------------|
| "for this study" / "on CRS-136" | Study-level |
| "for all our studies" / "always for this customer" | Customer-level |

If scope is ambiguous, it defaults to Study-level. The OC team reviewer
can adjust scope before approving.

---

## What Gets Changed (and What Doesn't)

The system only ever modifies the **Study Specification XLSX**
(`file_mm2n3x71` on the board row). It never touches:

- edc-builder output files
- XLSForm .xlsx files
- DVS files
- Any other pipeline output

The edc-builder re-runs from the updated spec on the next build cycle.
This means a change that isn't reflected in the spec will not survive
the next build run — always verify in the spec, not the build output.

### Change types the system handles

| Change type | What happens |
|-------------|-------------|
| Add field | New row appended to `{FORM}_survey` tab with ACTION=ADD |
| Remove field | Existing row marked ACTION=DELETE (row preserved) |
| Rename field | Label column updated on the existing row |
| Change validation | `constraint` + `constraint_message` updated (or flagged for XPath authoring if natural language) |
| Change choices | New choice appended to `{FORM}_choices` with ACTION=ADD, or existing marked ACTION=DELETE |
| Change visit | Note added to TIMEPOINTS sheet — requires manual schedule update |
| Change logic | `relevant` column updated if XPath provided; otherwise flagged for human authoring |
| Other / unstructured | Note added to REVIEW_NOTES on the relevant form tab |

---

## What to Do When Something Goes Wrong

### Status shows "Change Intake Failed"

Check the **AI Run Log** column on the board row — it will show the
error. Common causes:

| Error message | Fix |
|---------------|-----|
| "No protocol ID found in text" | Add `[PROTOCOL:CRS-136]` tag to your update |
| "No AI Hub board row found matching protocol" | Check the Protocol Number column on the row matches what's in your text |
| "No spec XLSX on board row" | Upload a spec XLSX to the Study Spec XLS column first |
| "Parse failed" | Your text may be very short or contain no actionable change requests — add more detail |

### The wrong form was updated

The system matched your form name to the closest tab in the spec.
If it picked the wrong form, the CHANGE_LOG sheet will show which tab
was targeted. Correct the spec manually and re-upload.

### A field wasn't found

If the field name in your text doesn't closely match the field name in
the spec (e.g. you said "Date of Birth" but the spec has "BRTHDTC"),
the system will log it as unresolved and add a note. Find the correct
field name in the spec and either edit manually or re-submit with the
exact field name.

### The change needs XPath logic

For validation or skip logic changes described in natural language
(e.g. "only show this field if the patient is female"), the system
flags it as unresolved and leaves a note. You'll need to write the
XPath expression manually in the spec. Ask the OC team if you need
help with XPath syntax.

---

## Quick Reference

### Trigger format
```
[DESIGN_CHANGE] [SOURCE_TYPE:meeting_notes|email|transcript] [PROTOCOL:XXX-NNN]
Your change request text here.
```

### Where to find outputs after processing

| What | Where on board row |
|------|-------------------|
| Updated spec XLSX | Study Spec XLS column |
| Source transcript | Change Request Transcripts column |
| Processing log | AI Run Log column |
| Pipeline status | Pipeline Status column |
| Convention proposals | Convention Rulebook board → Customer-Proposed Conventions group |

### Pipeline status values

| Status | Meaning |
|--------|---------|
| Change Intake Running | Processing your request |
| Change Intake Complete | Done — review updated spec |
| Change Intake Failed | Error — check AI Run Log |

---

## Tips for Best Results

**Be specific about form names.** "AE form" or "Adverse Events" both
work. Avoid vague references like "that form we discussed" — the system
has no memory of prior conversations.

**Include the field name as it appears in the spec when possible.**
If you know the CDASH variable name (e.g. AESEV, BRTHDTC), use it.
If you only know the label, that works too — the system tries both.

**One update per study per batch.** If you have ten changes for a study,
put them all in one update rather than ten separate updates. This keeps
the change log clean and avoids race conditions on the spec file.

**Don't worry about formatting.** You don't need numbered lists or
structured headers. Paragraph prose, bullet points, and stream-of-
consciousness notes all work equally well.

**For convention flags, use explicit language.** The system looks for
specific phrases. "This is a convention" and "always do this for our
studies" are reliably detected. Ambiguous phrases like "we prefer this"
may not be flagged.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-03 | Initial release — Phase 1 complete |
