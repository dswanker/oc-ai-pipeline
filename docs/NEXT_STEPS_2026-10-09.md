# Next steps, 2026-10-09

Status at end of 2026-10-08: everything below the "Built" line is on main and deployed (last commit 30c0902).
ADMIN_SECRET rotated and verified (old value 403, new value 200); repo secret scan clean (gitleaks over 949 commits
plus a targeted search for hard-coded secret defaults). Note: the GitHub repo answers unauthenticated requests, so it
is PUBLIC. Decide whether it should be private.

## Tomorrow, first: live validation run (BioIVT Detroit, existing test study S_DETROITD_6724(TEST))

Nothing new is created in OpenClinica except a UAT test site and participants.

1. Optional, read-only: `/admin/dry-run-board-json` on the Detroit item (needs the new ADMIN_SECRET:
   `cd ~/oc-ai-pipeline && railway variables | grep ADMIN_SECRET`).
2. Monday, Detroit item: outputs = Study build ZIP, DVS, SDTM Mapping Specification, Annotated CRF;
   Publish to Test ticked; Load DVS UAT Data ticked; do NOT clear study UUID/OID; trigger Send to AI.
3. Stop and report if publish fails with `boardTransformError` (known duplicate form-object issue), or if the browser
   login (saved ~35 h before 2026-10-08 23:59, BioIVT tenant) has expired ("Browser test error" on the UI cases).
4. Check: monday log "UAT results by method" (expect roughly 197 data import + 500+ browser, vs 2026-10-06 baseline of
   58 Pass / 173 Fail / 492 Not Run); "multi-step setup values: N of N stored and confirmed"; no browser result
   overwritten; new CDISC / cross-form checks firing in the forms; Annotated CRF vs SDTM Mapping Spec (3 forms);
   "CDASH concepts", "Questionnaires (CDISC QRS)" lines if present.

What only this run can prove: the Playwright browser step itself (login, opening forms, constraints read after save,
radio buttons on IE forms), setup values importing and confirming on a real study.

## Then: CDISC work, in this order

### 1. Benchmark the pipeline's schedule extraction (highest value)
- What: run our protocol analysis on the protocols behind CDISC's reference studies (CDISC_Pilot,
  EliLilly_NCT03421379, Alexion_NCT04573309) and score the extracted visits, windows and form placements against their
  USDM files (the answer key). The existing USDM benchmark only tested reading USDM, not producing a schedule.
- Why not done: needs the matching protocol PDFs and costs 3 real protocol-analysis runs; features were finished first.
- Involves: fetch PDFs; run analysis offline (nothing created in OC); score with the tests/usdm harness. About a day.
- Gain: an objective accuracy number for the schedule (the part that went wrong on Karius); the yardstick for every
  prompt change, including item 2; an external claim ("X% against CDISC's reference studies").

### 2. Stop Claude writing choice lists for CDISC-governed fields (prompt change)
- What: today Claude writes full lists for Yes/No, Sex, Race, severity, etc., and the deterministic CT layer replaces
  them. Claude would emit a codelist reference instead.
- Why not done: it changes the main protocol-analysis prompt (most sensitive part; trainer variance +/-1.1 pp, 3+
  runs needed), and Claude's lists are the fallback until the CT layer has run cleanly in production.
- Involves: prompt edit (prompts.py), fallback when a reference cannot be resolved, 3+ measured runs (use item 1).
  About a day plus runs. See TODO/TODO-cdisc-prompt-list-removal.md.
- Gain: fewer output tokens (cost, speed, less truncation risk on big protocols), no list conflicts or run-to-run
  drift on these fields.
- Gate: 2-3 clean production builds and the item 1 benchmark.

### 3. Full Biomedical Concepts (BC) integration
- What: CDISC BCs define a measurement completely (e.g. systolic BP: result, unit mmHg, position, location, method,
  VSTESTCD=SYSBP). Today fields are tagged with the concept, but the rest of the definition is unused (BC codes are
  only used in USDM activity matching).
- Why not done: goes beyond tagging into form design (companion fields, value-level metadata), must respect customer
  tiers (BioIVT: fields never deleted); free coverage limited to a few hundred common measurements.
- Involves: load BCs from CDISC's public COSMoS repo, match findings fields via concept tags, add units / companion
  fields on CDASH-default forms only, value-level metadata into the SDTM Mapping Spec and aCRF, unit checks into the
  DVS. Several days.
- Gain: complete findings forms; much richer SDTM mapping for vitals / labs / ECG (where most submission rework is);
  better aCRFs; foundation for CDISC 360i automation.

### 4. CORE engine on Railway (defer)
- What: CDISC's official rules engine; core_validation.py is built and tested, inert until CORE_ENGINE_CMD is set.
- Why not done: needs Python 3.12 (image is 3.11) and a 462 MB cache; only USDM rules apply and no sponsor sends USDM
  yet; most CORE rules check SDTM datasets, which the pipeline does not produce.
- Involves: either upgrade the image to 3.12 and re-test everything, or (cleaner) run CORE as a separate small Railway
  service; cache on the volume; set CORE_ENGINE_CMD.
- Gain: low today; useful once sponsors send USDM files.
- Trigger: the first real USDM file.

## Known limits after the UAT fixes (expected Manual cases)
About 21 Detroit / 10 Precision cases (calculated and cross-form autofill values) stay Manual with the reason in
Evidence; hidden-path cases that depend on which visit a form is opened at are tested by opening the form at another
visit. Possible phase 2: the ordered-setup mechanism can also verify autofill and calculations in the browser.

## Testing summary (for slides)
Over 1,000 automated tests across the full OC AI Pipeline, all passing apart from 3 documented known gaps; every
change regression-tested on 3 real study builds. (main: tests/build 128, other tests 724 + 2 skipped + 3 xfailed,
trainer 176.) Note: run `pytest tests/build` and `pytest tests --ignore=tests/build` separately (or fix pytest.ini
testpaths); neither `pytest tests` nor plain `pytest` collects everything today.
