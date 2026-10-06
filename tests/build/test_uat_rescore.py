"""
Offline re-score of the 2026-10-06 BioIVT UAT runs (Detroit, Precision).

Runs the loader's own _evaluate_uat_cases over the saved result workbooks with
the per-participant data OpenClinica actually stored
(docs/uat_analysis/participant_data.json). See
docs/UAT_FAILURE_ANALYSIS_2026-10-06.md. No network, nothing is created.
"""
import collections
import io
import json
import os
import sys

import openpyxl
import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO)

import uat_loader  # noqa: E402

_ANALYSIS = os.path.join(_REPO, "docs", "uat_analysis")
_RUNS = {
    "Detroit":   "detroit_bioivt_uat_results.xlsx",
    "Precision": "precision_bioivt_uat_results.xlsx",
}
_RESULT_COLS = ("Status", "Actual Result", "Test Result", "Execution Date", "Notes")


def _header(ws):
    for row in ws.iter_rows():
        if row[0].value == "UAT Case ID":
            return row[0].row, {str(c.value).strip(): c.column for c in row if c.value}
    raise AssertionError("UAT_Cases header row not found")


def _cases(wb_bytes):
    ws = openpyxl.load_workbook(io.BytesIO(wb_bytes), data_only=True)["UAT_Cases"]
    hdr, col = _header(ws)
    return [{k: r[c - 1].value for k, c in col.items()}
            for r in ws.iter_rows(min_row=hdr + 1) if r[0].value]


def _load_run(label):
    """(workbook bytes with result columns cleared, clinical_data, job_failures)."""
    with open(os.path.join(_ANALYSIS, "participant_data.json")) as f:
        stored = json.load(f)[label]
    clinical_data = {}
    for pkey, rows in stored.items():
        uat_loader._add_participant_data(
            clinical_data, pkey, {tuple(r[:4]): r[4] for r in rows})

    wb = openpyxl.load_workbook(os.path.join(_ANALYSIS, _RUNS[label]))
    ws = wb["UAT_Cases"]
    hdr, col = _header(ws)
    job_failures = {}
    for row in ws.iter_rows(min_row=hdr + 1):
        if not row[0].value:
            continue
        actual = str(row[col["Actual Result"] - 1].value or "")
        if actual.startswith("Import failed: "):
            pkey = str(row[col["Participant_Key"] - 1].value or "").strip()
            item = str(row[col["Item_OID"] - 1].value or "").strip().upper()
            job_failures[(pkey, item)] = actual[len("Import failed: "):]
        for name in _RESULT_COLS:      # back to a freshly stamped workbook
            row[col[name] - 1].value = None
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), clinical_data, job_failures


def _score(label):
    wb_bytes, clinical_data, job_failures = _load_run(label)
    scored = uat_loader._evaluate_uat_cases(wb_bytes, {}, clinical_data, job_failures)
    counts = collections.Counter()
    for c in _cases(scored):
        actual = str(c["Actual Result"] or "")
        if c["Test Result"] == "Fail" and actual.startswith("Import failed"):
            counts["Fail (import)"] += 1
        else:
            counts[str(c["Test Result"])] += 1
    return counts


@pytest.mark.parametrize("label,expected_pass", [("Detroit", 196), ("Precision", 178)])
def test_participant_aware_rescore_matches_analysis(label, expected_pass):
    """Each case is compared with its own participant's stored value."""
    assert _score(label)["Pass"] == expected_pass


def test_cases_for_one_item_are_scored_per_participant():
    """Detroit CM.CMSTDTC: P001 and P002 each stored what was loaded."""
    wb_bytes, clinical_data, job_failures = _load_run("Detroit")
    scored = uat_loader._evaluate_uat_cases(wb_bytes, {}, clinical_data, job_failures)
    by_id = {c["UAT Case ID"]: c for c in _cases(scored)}
    assert by_id["UAT-052"]["Test Result"] == "Pass"
    assert by_id["UAT-053"]["Test Result"] == "Pass"
    assert by_id["UAT-052"]["Actual Result"] != by_id["UAT-053"]["Actual Result"]


def test_participant_key_falls_back_to_stamp_map():
    """A workbook without a stamped Participant_Key still resolves the participant."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "UAT_Cases"
    cols = ["UAT Case ID", "Status", "Actual Result", "Test Result", "Execution Date",
            "Notes", "Expected Result", "Scenario", "Participant_Key", "Study_Event_OID",
            "Form_OID", "Item_Group_OID", "Item_OID", "Participant_ID", "Load_Value"]
    ws.append(cols)
    base = ["", "", "", "", "", "", "No constraint error. Form saves.", "Happy path",
            "", "SE_A", "F_X", "IG_X_G", "I_X_AGE"]
    ws.append(["UAT-001"] + base[1:] + ["UAT-P001", "40"])
    ws.append(["UAT-002"] + base[1:] + ["UAT-P002", "50"])
    buf = io.BytesIO()
    wb.save(buf)

    clinical_data = {}
    coords = ("SE_A", "F_X", "IG_X_G", "I_X_AGE")
    uat_loader._add_participant_data(clinical_data, "RUN-P001", {coords: "40"})
    uat_loader._add_participant_data(clinical_data, "RUN-P002", {coords: "50"})
    stamp_map = {"UAT-P001": {"participant_key": "RUN-P001"},
                 "UAT-P002": {"participant_key": "RUN-P002"}}
    scored = uat_loader._evaluate_uat_cases(buf.getvalue(), stamp_map, clinical_data, {})
    assert [c["Test Result"] for c in _cases(scored)] == ["Pass", "Pass"]


def test_job_failures_are_keyed_by_participant():
    log = "ParticipantID,StudyEventOID,FormOID,ItemOID,Status,Message\n" \
          "x,SE_A,F_X,I_X_AGE,Failed,errorCode.bad\n" \
          "x,SE_A,F_X,I_X_SEX,Inserted,\n"
    failures = uat_loader._job_failures_from_imports([
        {"participant_key": "RUN-P001", "result": {"log": log}},
        {"participant_key": "RUN-P002", "result": {"log": ""}},
    ])
    assert failures == {("RUN-P001", "I_X_AGE"): "errorCode.bad"}


# ── Real item OIDs from study metadata ───────────────────────────────────────
# Shape and OIDs as read from the live Precision study metadata on 2026-10-06
# (docs/UAT_FAILURE_ANALYSIS_2026-10-06.md, root cause 2).

_METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3">
 <Study OID="S_PRECISIO_1118(TEST)"><MetaDataVersion OID="v1.0.0" Name="v1">
  <FormDef OID="F_DM" Name="Demographics"><ItemGroupRef ItemGroupOID="IG_DEMOG_DM"/></FormDef>
  <FormDef OID="F_DMONC" Name="Demographics Oncology"><ItemGroupRef ItemGroupOID="IG_DEMOG_DMONC"/></FormDef>
  <FormDef OID="F_IE8200" Name="Eligibility 8200"><ItemGroupRef ItemGroupOID="IG_ELIGI_IE8200"/></FormDef>
  <FormDef OID="F_IE1009" Name="Eligibility 1009"><ItemGroupRef ItemGroupOID="IG_ELIGI_IE1009"/></FormDef>
  <ItemGroupDef OID="IG_DEMOG_DM" Name="DM"><ItemRef ItemOID="I_DEMOG_DIN"/><ItemRef ItemOID="I_DEMOG_VISIT"/></ItemGroupDef>
  <ItemGroupDef OID="IG_DEMOG_DMONC" Name="DMONC"><ItemRef ItemOID="I_DEMOG_DIN_2803"/><ItemRef ItemOID="I_DEMOG_VISIT_7711"/></ItemGroupDef>
  <ItemGroupDef OID="IG_ELIGI_IE8200" Name="IE8200"><ItemRef ItemOID="I_ELIGI_IEINC01"/></ItemGroupDef>
  <ItemGroupDef OID="IG_ELIGI_IE1009" Name="IE1009"><ItemRef ItemOID="I_ELIGI_IEINC01_4052"/></ItemGroupDef>
  <ItemDef OID="I_DEMOG_DIN" Name="DIN" DataType="text"/>
  <ItemDef OID="I_DEMOG_VISIT" Name="VISIT" DataType="text"/>
  <ItemDef OID="I_DEMOG_DIN_2803" Name="DIN" DataType="text"/>
  <ItemDef OID="I_DEMOG_VISIT_7711" Name="VISIT" DataType="text"/>
  <ItemDef OID="I_ELIGI_IEINC01" Name="IEINC01" DataType="text"/>
  <ItemDef OID="I_ELIGI_IEINC01_4052" Name="IEINC01" DataType="text"/>
 </MetaDataVersion></Study>
</ODM>"""


def _row(form, group, item, steps, value="x", **extra):
    row = {"Study_Event_OID": "SE_A", "Event_Repeat_Key": "1", "Form_OID": form,
           "Item_Group_OID": group, "Item_OID": item, "Test Steps": steps,
           "Load_Value": value, "Expected Result": "No constraint error. Form saves.",
           "Scenario": "Happy path", "Participant_ID": "UAT-P001"}
    row.update(extra)
    return row


def test_metadata_maps_form_and_item_name_to_the_real_oid():
    oid_map = uat_loader._parse_item_oid_map(_METADATA_XML)
    assert oid_map[("F_DM", "DIN")]["item_oid"] == "I_DEMOG_DIN"
    assert oid_map[("F_DMONC", "DIN")] == {
        "item_oid": "I_DEMOG_DIN_2803", "item_group_oid": "IG_DEMOG_DMONC"}
    assert oid_map[("F_IE1009", "IEINC01")]["item_oid"] == "I_ELIGI_IEINC01_4052"
    assert uat_loader._parse_item_oid_map("") == {}
    assert uat_loader._parse_item_oid_map("<not xml") == {}


def test_item_name_comes_from_the_column_then_test_steps_then_the_oid():
    name = uat_loader._item_name_for_row
    assert name({"Item_Name": "DIN", "Item_OID": "I_DEMOG_OTHER"}) == "DIN"
    assert name({"Test Steps": "1. Navigate to DMONC.AGE_DISP\n2. Apply",
                 "Item_OID": "I_DEMOG_AGE_DISP"}) == "AGE_DISP"
    assert name({"Test Steps": "1. Enter 'Aspirin 500mg' for CMTRT\n2. Save",
                 "Item_OID": "I_CONCO_CMTRT"}) == "CMTRT"
    assert name({"Item_OID": "I_DEMOG_AGE_DISP"}) == "AGE_DISP"


def test_predicted_oids_are_rewritten_before_the_odm_is_built():
    oid_map = uat_loader._parse_item_oid_map(_METADATA_XML)
    rows = [
        _row("F_DM", "IG_DEMOG_DM", "I_DEMOG_DIN", "1. Navigate to DM.DIN\n"),
        _row("F_DMONC", "IG_DEMOG_DMONC", "I_DEMOG_DIN", "1. Navigate to DMONC.DIN\n"),
        _row("F_IE1009", "IG_ELIGI_IE1009", "I_ELIGI_IEINC01", "1. Navigate to IE1009.IEINC01\n"),
        _row("F_OTHER", "IG_OTHER_G", "I_OTHER_X", "1. Navigate to OTHER.X\n"),
    ]
    counts = uat_loader._apply_item_oid_map(rows, oid_map)
    assert counts == {"corrected": 2, "unchanged": 1, "unresolved": 1}
    assert [r["Item_OID"] for r in rows] == [
        "I_DEMOG_DIN", "I_DEMOG_DIN_2803", "I_ELIGI_IEINC01_4052", "I_OTHER_X"]
    assert [r["Item_Name"] for r in rows] == ["DIN", "DIN", "IEINC01", "X"]

    xml = uat_loader._build_odm_xml("S_X", "SITE", "SS_1", "P1", rows, item_oid_map=oid_map)
    dmonc = xml.split('FormOID="F_DMONC"')[1].split("</FormData>")[0]
    assert 'ItemOID="I_DEMOG_DIN_2803"' in dmonc and 'ItemOID="I_DEMOG_DIN"' not in dmonc
    assert uat_loader._validate_odm_xml(xml) == []


def test_stamp_rewrites_oids_and_adds_item_name_to_an_older_workbook():
    """The saved Precision workbook has no Item_Name column and predicted OIDs."""
    oid_map = uat_loader._parse_item_oid_map(_METADATA_XML)
    with open(os.path.join(_ANALYSIS, _RUNS["Precision"]), "rb") as f:
        before = _cases(f.read())
    with open(os.path.join(_ANALYSIS, _RUNS["Precision"]), "rb") as f:
        after = _cases(uat_loader._stamp_dvs(f.read(), {}, oid_map))
    assert len(after) == len(before)
    changed = 0
    for b, a in zip(before, after):
        assert a["Item_Name"], a["UAT Case ID"]
        key = (str(b["Form_OID"]).upper(), str(a["Item_Name"]).upper())
        if key in oid_map:
            assert a["Item_OID"] == oid_map[key]["item_oid"]
            changed += a["Item_OID"] != b["Item_OID"]
        else:
            assert a["Item_OID"] == b["Item_OID"]
    assert changed > 0   # F_DMONC.DIN, F_DMONC.VISIT, F_IE1009.IEINC01 rows
    # the derived name is always the tail of the predicted OID
    for b, a in zip(before, after):
        assert str(b["Item_OID"]).upper().endswith("_" + str(a["Item_Name"]).upper()), a["UAT Case ID"]
