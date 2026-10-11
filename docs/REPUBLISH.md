# Republishing an existing study with updated forms

How to bring new form versions (new checks, new lookups) into a study the pipeline already created and published
to Test, without creating a second study, duplicate board cards or touching Production. Everything here is the
existing "Send to AI" run on the study's own monday item; nothing is a separate tool.

## What the run does

| Step | What happens | Where |
|---|---|---|
| Specification | The item's saved Study Specification JSON is reused. No protocol analysis. | `pipeline.py`, Path R |
| Logic | Required forms, standards matching, conventions engine, AI edit-check proposals (proposed only), logic coverage, lookup CSV names and stable rules all run on the reused specification. | the shared steps before the chains |
| Build | Forms, CSVs, DVS and the build ZIP are rebuilt. The build fails a form whose lookup CSV is missing. | `run_edc_build`, `lookup_csv.validate_build` |
| Study | The study on the item (Study UUID) is found, not created. | `create_oc_study` |
| Board | Not imported again: events, cards and schedule stay as they are. | `fast_rerun` |
| Forms | For each existing board card, the form with the same OID is uploaded as a **new version** of that card's form, with the CSV files it reads, and made the default version. | `oc_form_publisher.py` |
| Publish | To the **Test** environment only. No code path publishes to Production. | `publish_to_test` |
| UAT | Fresh `UAT-` participants, data import, browser cases, results and reports. | `uat_loader.py` |

## monday settings

Leave **Study UUID**, **Study OID**, **OC Subdomain**, **Protocol Number** and the **Study Specification (JSON)**
file exactly as they are.

| Column | Value | Why |
|---|---|---|
| Pipeline Mode | blank (Full build) | "Logic + UAT" and "UAT only" never upload or publish. |
| Output Requested | **Study build ZIP** and **DVS** (at least these two) | A blank selection means "everything" and runs a new protocol analysis instead of reusing the specification. Without "Study build ZIP" no new ZIP is put on the item and the old forms would be uploaded again. |
| Create Study | ticked | The form upload is part of this step; the study itself is found by its UUID and not created. |
| Publish to Test | ticked | Publishes the new versions to Test. Also required with Load DVS UAT Data: that box alone is the UAT-only rerun. |
| Load DVS UAT Data | ticked | Runs UAT after the publish. |
| Production | unticked | Has no effect on the target, kept unticked for clarity. |
| Edited Study Specification / Edited Study Build Forms ZIP / Edited DVS | empty | Any of these takes precedence over reusing the saved specification. |

Then set the trigger to **Send to AI**. A valid browser login is needed (upload, publish and browser UAT use it):
if the run stops at "Paused for Authentication", use the item's auth link and trigger again. The saved login is
good for about an hour, so start the run right after authenticating.

## What to read in the run log

- `[fast-rerun] Re-using existing Study Spec JSON from monday`: the specification was reused. If this line is
  missing the run is analysing the protocol again: stop it and check Output Requested.
- `Logic coverage: ...` and `Lookup CSVs: ...`: the new logic and names were applied.
- `Study already exists ... skipping creation`.
- No `Study Spec unchanged since last upload`: that line means nothing was uploaded. The decision now covers the
  build code too (`UPLOAD_HASH_BUILD_CODE`), so a run after a deploy uploads even when the specification is the same.
- `Manual edit conflicts detected — pipeline did not overwrite`: a form was changed by hand in the designer since
  the pipeline's last upload. That form keeps its current version; decide by hand.
- `Skipping ... no xlsx in EDC zip`: a board card has no form of that OID in the build. It keeps its old version.
- `Publish to Test FAILED`: the published study is unchanged. `boardTransformError` means a duplicate form object
  on the board (the log line says what to do); do not rerun the upload before the board is fixed.

## What this run cannot do

- Add a form or an event that is not on the board yet, or remove one: the board is left alone. New forms need a
  card in the Study Designer first.
- Change an item's data type on an existing form. OpenClinica rejects that as a new version.
- Roll back. A new form version stays on the board; the previous version can be made the default again by hand.

Every run adds a new version to every form on the board whose OID is in the build, changed or not.

## After the run, in OpenClinica (Test)

1. Open the visit-date form of a participant at a screening visit: the timepoint text is shown and there is no
   "Can't find ... .csv" message. At the first dosing visit "Was the visit done?" is not asked and the visit date is.
2. On a form with a date, enter a date before the consent date: the cross-form check fires.
3. On a form with a "performed?" question, answer No: the detail questions are hidden.
4. The study's Test environment shows the new version name (`UAT-<timestamp>`); Production is untouched.

## Switches

| Variable | Effect |
|---|---|
| `LOOKUP_CSV_CANONICAL=0` | lookup file names and references are left as written |
| `LOOKUP_CSV_ATTACH=0` | only CSV files beside a form are uploaded with it (no lookups from `csv/`) |
| `LOOKUP_CSV_VALIDATE=0` | no build error for a lookup without a file |
| `LOOKUP_STABLE_CODES=0` | visit-label rules and the timepoint file stay as before |
| `UPLOAD_HASH_BUILD_CODE=0` | "nothing to upload" is decided on the specification alone |
| `LOGIC_COVERAGE=0`, `AI_EDIT_CHECKS=0` | skip those steps on the reused specification |
