# UAT result analysis: why most "failures" are not failures (BioIVT V4 runs, 2026-10-06)

Runs analysed (tenant `bioivt`, test environment, both published to test with "Load DVS UAT Data" ticked):

| Study | OC study OID | Loader finished | Cases | Loader reported | Re-scored correctly |
|---|---|---|---|---|---|
| Detroit | `S_DETROITD_6724(TEST)` | 17:05 UTC | 731 | Pass 58, Fail 173, Not Run 492 | **Pass 196**, real mismatch 34, item-ID mismatch 1, Not Run 492 |
| Precision | `S_PRECISIO_1118(TEST)` | 17:35 UTC | 1004 | Pass 78, Fail 332, Not Run 588 | **Pass 178**, item-ID mismatch 228, real mismatch 4, Not Run 588 |

"Re-scored correctly" = `uat_analysis/uat_rescore.py`: each case compared with the value stored for **its own participant**, using OpenClinica's real per-participant data (`participant_data.json`).
Nothing in the study build is implicated by the large groups of failures. They come from `uat_loader.py` and the DVS/UAT case generator.

## Root cause 1: the read-back merges all participants into one lookup (Detroit 149 of 173 failures, Precision 100 of 332)

`uat_loader.py` `run_uat_loader` (~line 1393-1406) does `clinical_data.update(participant_data)` for each participant. The key is
`(event_oid, form_oid, item_group_oid, item_oid)`, with **no participant in it**, so later participants overwrite earlier ones.
`_evaluate_uat_cases` (line ~899) then compares every case for that item with the single surviving value.
`job_failures` (~line 1470-1490) is keyed by item OID only, with the same flaw.

The DVS assigns each case for an item to a different participant (`Participant_ID`, `Load_Order`), because a participant can hold one value per item. So the design intends one value per participant per item. Only the lookup is wrong.

Worked example (Detroit, `CM.CMSTDTC`):

| Case | Participant | Loaded | Recorded |
|---|---|---|---|
| UAT-052 | P001 | 2026-10-06 | Fail, "actual 2026-10-07" |
| UAT-053 | P002 | 2026-10-05 | Fail, "actual 2026-10-07" |
| UAT-054 | P003 | 2026-10-07 (future date, "constraint fires") | Not Run |

P001 and P002 each stored the correct value (see `participant_data.json`). P003's value won the merge. The same pattern explains `BIOMK.PDL1TPS` (0, 50, 100 all reported against 101), `COVID19.LENGTH_STAY` (366), and `IE1009.IEEXC12..14` ("no" reported against "yes").

**Fix:** key the lookup by participant. `stamp_map[logical_pid]["participant_key"]` already exists and each case row carries `Participant_Key`. Look up `(participant_key, event, form, group, item)`. Key `job_failures` by `(participant_key, item_oid)` too.

## Root cause 2: OpenClinica assigns item OIDs the generator cannot predict (Precision 228 failures)

Error: `Import failed: errorCode.itemGroupDoesNotContainItemData`. Concentrated in 8 of the 9 `IE####` forms and in `F_DMONC`.

OpenClinica builds item OIDs as `I_<first 5 letters of the form TITLE>_<item name>`. When a later form collides on prefix and name, it appends a **random 4-digit suffix**. Verified live against the Precision study metadata (`probe_metadata_oids_remote.py`, GET `.../clinicaldata/{study}/*/*/*?clinicalData=n&includeMetadata=y`):

| Form | Item `DIN` | Item group |
|---|---|---|
| `F_DM` (first) | `I_DEMOG_DIN` | `IG_DEMOG_DM` |
| `F_DMONC` (later) | `I_DEMOG_DIN_2803` | `IG_DEMOG_DMONC` |
| `F_IE8200` (first IE form) | `I_ELIGI_IEINC01` | `IG_ELIGI_IE8200` |
| `F_IE1009` | `I_ELIGI_IEINC01_4052` | `IG_ELIGI_IE1009` |
| `F_IE6504` | `I_ELIGI_IEINC01_6062` | `IG_ELIGI_IE6504` |

Nine forms share `IEINC01`; two share `VISIT` and `DIN`. The UAT cases carry the predicted OID (`I_DEMOG_DIN`), which belongs to `F_DM`, so the ODM puts DM's item inside DMONC's group and OpenClinica rejects it. Group OIDs are unique per form and correct. The first form to use a name gets the plain OID, which is why `F_DM` and `F_IE8200` pass and the rest fail.

Item OIDs are assigned at upload and are random, so they cannot be predicted in the build. **Fix:** at UAT-load time, after publish, fetch the study metadata (`includeMetadata=y`), map `(Form_OID, item NAME)` to the real item OID (`ItemGroupDef/ItemRef` gives membership, `ItemDef/@Name` gives the name), and rewrite `Item_OID` (and `Item_Group_OID` if needed) in the UAT rows before `_build_odm_xml` and `_evaluate_uat_cases`. The `_stamp_dvs` step ("stamping DVS with runtime OIDs") already rewrites runtime columns and is the natural place. The case rows need the item **name**: derive it from the check ("DM.DIN"), or better, add an `Item_Name` column in the generator.

The same wrong OIDs are in the DVS `DVS_OC4` sheet, so any rule authored from it for those forms would also point at the wrong item.

## Root cause 3: "constraint fires" rows are written to OpenClinica even though they are labelled Not Run

`_evaluate_uat_cases` classifies rows whose expected text contains "Constraint fires" etc. as `Not Testable via ODM` (492 Detroit, 588 Precision), and a code comment says they "must NOT be loaded". But `_build_odm_xml` still loads them. Evidence in `participant_data.json`: Detroit P003 stores `CM CMSTDTC = 2026-10-07` (the future-date negative case) and P001 stores `MONDISFREE = -1`.
OpenClinica's ODM import does not enforce XLSForm constraints, so these save. Harmless once root cause 1 is fixed, but it uses participants for nothing and pollutes UAT data. **Fix:** apply the same `not_testable` classification in the ODM builder and skip those rows.

## Root cause 4: test-data generator problems (Detroit ~34, Precision 4 remaining mismatches)

- Integer fields given decimal mid-range values: `COVID19.LENGTH_STAY` loads `182.5`, `DM.VISITNUM` loads `50.5`; nothing is stored.
- "Calc path" rows that load `EVENT_DATE_CF=Test value` into date fields (stored nothing): `DM.COLLDT`, `PC.PCDAT`, `VS.VSDAT`.
- "Sad path" rows with a blank `Load_Value` compared against the baseline value (ECONSENT, FOLLOWUP).
- Boundary values written directly into calculated fields (`MONDISFREE`/`MONSURV` hold -1, 0, 300, 600, 601, 1200, 1201): that exercises the data import, not the calculation.
- Detroit participants P006-P008 and Precision P006-P007 hold no data: each had one row, all dropped by the builder's filters (`0 items passed filter`), and the import returned `subjectDoesNotContainStudyEventData`. Harmless, wasteful.

## What UAT does NOT tell us (do not over-read the passes)

The ODM import bypasses form logic, so this harness **cannot confirm** the two newest BioIVT behaviours. Do not treat the "Calc path" passes or failures as evidence either way:
1. **Cross-event autofill** (Follow-up and Outcome pull DOB, gender, race, ethnicity, DIN from the Short Form via `pulldata`). The `*_SF` mirrors show `1974-03-22` only because a case loads the helper field directly.
2. **Calculated months** (disease-free and survived). Stored values are generator boundary values.

Needs a hands-on check in OC4 (cust1/bioivt test): enter Short Form DOB, open Follow-up and confirm the mirror shows it and the original question hides; enter remission and recurrence dates and confirm months-disease-free calculates, is read-only, and is blank when a date is missing.

## Possible real issue to look at: "Unexpected constraint" on autofill originals (Detroit, 9 cases)

`FOLLOWUP.DOB/GENDER/RACE/ETH` and `OUTCOME.DIN/DOB/GENDER/RACE/ETH` "Cross-form" cases report `Unexpected constraint: Follow-up Data` / `Oncology Outcome Data`. The wording is not produced by `_evaluate_uat_cases`, so it comes from the Playwright UI step. These are exactly the questions the autofill hides when a Short Form value exists, so it may be by design (the test tries to use a hidden question). Needs a person to confirm.

## Reproduce

All from the repo root. Nothing here creates anything in OpenClinica (read-only GETs).
- `docs/uat_analysis/uat_rescore.py`: re-scores both studies from the saved workbooks and `participant_data.json` (offline, no network).
- `docs/uat_analysis/probe_participant_data.py`: re-fetches per-participant data (run inside the Railway container: `python3 docs/uat_analysis/remote_run.py docs/uat_analysis/probe_participant_data.py`).
- `docs/uat_analysis/probe_metadata_oids_remote.py`: prints the real item/group OIDs from the live metadata (same way).
- Result workbooks as produced by the pipeline: `detroit_bioivt_uat_results.xlsx`, `precision_bioivt_uat_results.xlsx` (sheets `UAT_Cases`, `DVS_OC4`).

## Acceptance for a fix

Re-run `uat_rescore.py` logic inside the loader: Detroit should report about 196 Pass and Precision about 178 plus most of the 228 item-ID cases, with remaining failures limited to the generator problems above.
Do not create throwaway studies in OpenClinica to test (studies cannot be deleted); verify offline against the saved data, then on the next real run.
