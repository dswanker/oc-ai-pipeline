# Pipeline modes: Full build, Logic + UAT, UAT only

Two entry points for a customer's **existing** study, next to the full build. Code: `pipeline_modes.py`,
`skills/logic-coverage`, `uat_loader.py`. Rerunning UAT on a study the pipeline built itself is a different thing:
see `docs/UAT_RERUN.md`.

## The monday column to create

The column does not exist yet. Nothing in the code creates it.

| Property | Value |
|---|---|
| Title | `Pipeline Mode` (exact) |
| Type | Status (single choice) |
| Labels | `Full build`, `Logic + UAT`, `UAT only` (exact text) |
| Default | none (blank) |

Blank, or any other text, is a full build: an item without the column behaves exactly as before.
Until the column id is registered the column is found by its title. After creating it, add the id to
`monday_client.COL` as `"pipeline_mode": "<column id>"`; the id then wins and the title may be changed.

The mode is read when the AI trigger is set to **Send to AI**.

## What the two modes never do

No protocol analysis, no standards matching, no study or form build from a protocol, no board import, no form
upload, no publish. The only OpenClinica writes are the UAT load itself (test participants and their data in the
Test environment), made by the existing UAT loader. No AI call is made.

## Inputs (both modes)

| Input | Where | Notes |
|---|---|---|
| The forms | **Customer OC4 Standard(s)** column: XLSForm files, a ZIP of them, or an ODM export | used when present |
| | or the study itself: **Study UUID** + **OC Subdomain** | when no file is uploaded, the forms (original XLSForms with their logic) are read from the study's design board, read-only |
| The study | **Study UUID**, **OC Subdomain**, **OC email** | needed for UAT; the Study OID column is filled in from the Test environment when blank |
| A protocol | Protocol column | optional; it is not analysed and changes no form. Linking UAT cases to protocol sections is not built yet |

## Preconditions

Checked before anything is produced. Each failure is one line in the monday log saying what to do.

| Check | When it fails |
|---|---|
| Forms are uploaded, or Study UUID and OC Subdomain are filled in | the run stops |
| The OC user can reach the subdomain | the run stops (Logic + UAT with uploaded forms still produces its files) |
| The study is published to Test and the Test environment is AVAILABLE | UAT only stops; Logic + UAT produces its files and does not run UAT |
| The Test environment has a site | as above. The run does not create a site unless `UAT_ALLOW_SITE_CREATE=1` |
| The saved browser login is valid | status **Paused for Authentication**; authenticate, then Send to AI again |

## UAT only

1. Reads the forms.
2. Generates the DVS from the forms' **own** logic (every constraint, required flag, show-when rule and calculation
   in the forms is a Draft row) and uploads it to the DVS column.
3. Runs the logic-coverage audit in report mode: what the catalog would add is listed as **Proposed** rows and in
   the `LOGIC_COVERAGE` sheet. No form is changed.
4. Generates the UAT cases, places each on the visit the form sits on in the published study, and runs the UAT
   loader: load, browser tests, read-back, **UAT DVS Results**, **UAT Traceability Matrix**,
   **UAT Validation Report**.

Status: Loading UAT Data, then All Complete or UAT Load Failed.

## Logic + UAT

1. Reads the forms.
2. Runs the logic-coverage skill in apply mode: on a form that carries **no logic of its own** the missing checks
   are built; on a form that carries its own logic they are proposed. Derived helper items of logic-free forms are
   made calculated or read-only.
3. Uploads the **Updated Forms ZIP** (EDC Build column) and the DVS (DVS column).
4. If anything was added, the study does not carry the updated forms yet: the run stops at **Build Complete** and
   the log says to upload and publish the forms, then run the mode again.
5. On a run where nothing is left to add, it goes on to UAT as in UAT only.

Proposed checks are approved or rejected in the DVS (Action column) as for any build.

## Switches

| Variable | Effect |
|---|---|
| `UAT_ALLOW_SITE_CREATE=1` | the UAT load of these modes may create a test site when the Test environment has none |
| `STANDARD_LOGIC_FREE_APPLY=0` | Logic + UAT proposes everything; nothing is built on logic-free forms |
| `LOGIC_COVERAGE=0` | no audit in the full build (the modes always run it) |
| `LOGIC_COVERAGE_HELPERS=0` | derived helper items are reported and none is changed |

The UAT switches are in `docs/UAT_RERUN.md`.
