# Rerunning UAT only

Reruns the UAT load, the browser tests, the read-back and the UAT reports for a study that already exists and is
published to Test. Nothing is built, no form is uploaded, nothing is published.

## monday settings

On the item of the study:

| Column | Column id | Value |
|---|---|---|
| Study UUID | `text_mm3ggzga` | filled in (written by the run that created the study): leave it |
| Study OID | `text_mm3gxekw` | filled in (the Test environment OID, written at publish): leave it |
| OC Subdomain, OC email | `text_mm3aa7cx`, `emailothn6i3m` | as for the build |
| Load DVS UAT Data | `boolean_mm3gxe49` | **checked** |
| Publish to Test | `boolean_mm3g2vzf` | **unchecked** |
| AI trigger | | set to **Send to AI** |

The three conditions that select the rerun are: Study UUID present, Load DVS UAT Data checked, Publish to Test
unchecked (`pipeline._uat_only_requested`). With Publish to Test checked, or without a Study UUID, "Send to AI" is a
full run. The other columns (outputs requested, Create OC Study, CQ answers) are not read by a rerun.

The Study Specification JSON and the EDC Build ZIP of the last build must still be on the item: the UAT cases are
regenerated from them.

## The browser login

The browser tests use the saved OpenClinica login of the OC email. A saved login is usable for about an hour:
OpenClinica stores an idle deadline with it, and the first page opened after that deadline signs the login out.

- If the login is stale when the rerun starts, the item goes to **Paused for Authentication** with an auth link.
  Authenticate, then set **Send to AI** again.
- If the login expires before the browser step, the load still completes; the log says
  `browser tests were not run — the saved browser login has expired`, the browser cases are reported under Manual /
  Not Run with that reason, and the rerun can be repeated after authenticating.

So: authenticate, then start the rerun straight away.

## What a rerun does

1. Regenerates the DVS from the Study Specification JSON and the EDC Build ZIP on the item and uploads it to the DVS
   column. The cases then carry today's dates (a "future date" case generated yesterday is not in the future today)
   and the current case generator. The forms in OpenClinica are not touched. `UAT_ONLY_REGEN_DVS=0` skips this and
   uses the DVS already on the item.
2. Reuses the UAT site of the Test environment and creates new participants (`UAT-<date>-<time>-P001` …). Earlier
   UAT participants are left as they are.
3. Loads the data-import cases, reads every value back and scores them.
4. Loads the setup values of multi-step cases into a participant of their own and reads them back. A case whose setup
   is not in place is **Blocked**.
5. Runs the browser cases (required, constraint, show/hide) in the form, in the visit each case names.
6. Uploads the UAT DVS Results workbook (`file_mm3h5s3h`), the UAT Traceability Matrix (`file_mm3h7r4`) and the UAT
   Validation Report (`file_mm3hvbpb`), and logs each.

Status goes to **Loading UAT Data**, then **All Complete** or **UAT Load Failed**.

## Results

| Result | Meaning |
|---|---|
| Pass / Fail | The case was run. Data import: the value was stored and read back unchanged (the import does not run form logic). Browser: the field's own message or visibility in the form. |
| Blocked | The case could not be run; Actual Result gives the reason (setup not in place, form or visit could not be opened, field or gate not in the published form, calculated gate, value not an option). |
| Not Run | Not attempted on this run (for example the browser step did not run), or it needs a manual check. |
| Skip | Deliberately not run (the eligibility form, only when `PW_TEST_F_IE=0`). |

## Switches

All default to the behaviour described above.

| Variable | Effect |
|---|---|
| `UAT_ONLY_REGEN_DVS=0` | rerun with the DVS already on the item |
| `UAT_REPORTS=0` | no Traceability Matrix / Validation Report |
| `UAT_SETUP_OWN_PARTICIPANT=0` | multi-step setup on UAT-P001, as before |
| `UAT_TYPED_SAMPLES=0` | name-derived sample values without the data-type check |
| `PW_UAT_ENGINE=legacy` | the previous browser step |
| `PW_FORMS=F_A,F_B` | browser cases of these forms only |
| `PW_CONCURRENCY=n` | forms open at once in the browser step (default 6) |
| `PW_TEST_F_IE=0` | skip the eligibility form's browser cases (they run by default) |

## Without monday

`POST /admin/regen-dvs` with `{"item_id": "<id>"}` and the admin secret header regenerates the DVS and runs the
UAT load. It does not check the saved login first and does not pause for authentication.
