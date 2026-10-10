"""
UAT Traceability Matrix (XLSX) and UAT Validation Report (PDF): both are built from the results workbook alone, so
the counts must match it, a check with no executed case must show as not covered, and study text with HTML or a bare
"<" / "&" must never stop the PDF from being produced.
"""
import io
import os
import re
import sys

import openpyxl

_HERE = os.path.dirname(__file__)
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _REPO)

import uat_reports  # noqa: E402

ODM, BROWSER, MANUAL = "Data import (ODM)", "Browser (Playwright)", "Manual"
SPAN = 'Weight <span style="color:white"> </span>(kg)'
BARE = "Value < 5 & value > 1"

CASE_HDR = ["UAT Case ID", "Status", "Related Check ID", "Scenario", "Preconditions", "Test Steps", "Input Data",
            "Expected Result", "Actual Result", "Test Result", "Execution Date", "Notes", "Site_OID",
            "Participant_Key", "Study_Event_OID", "Form_OID", "Item_Group_OID", "Item_OID", "Participant_ID",
            "Load_Value", "Item_Name", "Setup_Steps", "Test_Value", "Test Method", "Evidence"]
DVS_HDR = ["Action", "Check ID", "Status", "Plain-English Description", "Protocol Reference", "Check Type",
           "Severity", "Target Form OID", "Target Item Name", "Expression / Calculation",
           "Constraint / Required / Relevant Message"]

# (case, check, form, item, event, scenario, expected, actual, result, method, participant key, participant id)
CASES = [
    ("UAT-001", "DVS-001", "F_AA", "ITEM1", "SE_ONE", "Happy path", "Value stored", "12", "Pass", ODM, "RUN-P001", "UAT-P001"),
    ("UAT-002", "DVS-001", "F_AA", "ITEM1", "SE_ONE", SPAN, "Error shown", "No error shown", "Fail", BROWSER, "RUN-P001", "UAT-P001"),
    ("UAT-003", "DVS-002", "F_AA", "ITEM2", "SE_ONE", BARE, "Error shown", "Setup failed: value not stored", "Blocked", BROWSER, "", "UAT-P002"),
    ("UAT-004", "DVS-003", "F_BB", "ITEM3", "SE_TWO", "Calc path", "Computed value", "Not verifiable via import", "Not Run", MANUAL, "RUN-P001", "UAT-P001"),
    ("UAT-005", "DVS-003", "F_BB", "ITEM3", "SE_TWO", "Select field", "Error shown", "Skipped: not testable", "Skip", BROWSER, "RUN-P001", "UAT-P001"),
    ("UAT-006", "DVS-004", "F_BB", "ITEM4", "SE_TWO", "Happy path", "Value stored", "Y", "Pass", ODM, "RUN-P001", "UAT-P001"),
    ("UAT-007", "DVS-009", "F_BB", "ITEM9", "SE_TWO", "Blank result", "Value stored", "", "", MANUAL, "RUN-P001", "UAT-P001"),
]
# (check, status, description, type, form, item, expression, message)
CHECKS = [
    ("DVS-001", "Draft", "ITEM1 is required.", "Required", "AA", "ITEM1", "yes", "This field is required."),
    ("DVS-002", "Draft", "", "Constraint", "AA", "ITEM2", ". < 5", BARE),
    ("DVS-003", "Draft", "", "Calculation", "BB", "ITEM3", "${ITEM1} + 1", ""),
    ("DVS-004", "Approved", "ITEM4 is required.", "Required", "BB", "ITEM4", "yes", "This field is required."),
    ("DVS-005", "Draft", "ITEM5 has no case.", "Required", "BB", "ITEM5", "yes", "This field is required."),
    ("DVS-006", "Proposed", "ITEM6 is suggested.", "Constraint", "BB", "ITEM6", ". > 0", "Must be positive."),
]
META = {"protocol_number": "PROT-001", "study_name": "Generic Study", "study_uuid": "S_UUID", "study_oid": "S_OID",
        "environment": "TEST", "environment_url": "https://example.invalid/study", "site_oid": "S_SITE(TEST)",
        "executed_by": "svc-uat", "executed_at": "2026-01-01 10:00 UTC", "participants": {"UAT-P001": "RUN-P001"},
        "browser_status": "ran"}


def _workbook(cases=CASES, case_hdr=CASE_HDR, dvs=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "UAT_Cases"
    ws.append(["UAT Cases"])
    ws.append(["Fill in Actual Result and Test Result after each run."])
    ws.append(case_hdr)
    for (cid, chk, form, item, event, scen, exp, act, res, method, pkey, pid) in cases:
        full = {"UAT Case ID": cid, "Status": res, "Related Check ID": chk, "Scenario": scen, "Input Data": "5",
                "Expected Result": exp, "Actual Result": act, "Test Result": res,
                "Execution Date": "2026-01-01 10:00 UTC", "Participant_Key": pkey, "Study_Event_OID": event,
                "Form_OID": form, "Item_OID": f"I_X_{item}", "Participant_ID": pid, "Load_Value": "5",
                "Item_Name": item, "Test Method": method, "Evidence": act}
        ws.append([full.get(h, "") for h in case_hdr])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"      # text that starts with "=" is stored as text, as the loader writes it
    if dvs:
        ws = wb.create_sheet("DVS_OC4")
        ws.append(["DVS checks"])
        ws.append([])
        ws.append(DVS_HDR)
        for (cid, status, desc, ctype, form, item, expr, msg) in CHECKS:
            full = {"Check ID": cid, "Status": status, "Plain-English Description": desc, "Check Type": ctype,
                    "Severity": "Hard", "Target Form OID": form, "Target Item Name": item,
                    "Expression / Calculation": expr, "Constraint / Required / Relevant Message": msg,
                    "Protocol Reference": "Section 1"}
            ws.append([full.get(h, "") for h in DVS_HDR])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _rows(xlsx_bytes, sheet):
    ws = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))[sheet]
    hdr = [c.value for c in ws[1]]
    return [dict(zip(hdr, [c if c is not None else "" for c in r])) for r in ws.iter_rows(min_row=2, values_only=True)]


def _pages(pdf_bytes):
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))


def test_read_results_finds_padded_header_and_normalises_blank_result():
    data = uat_reports.read_results(_workbook())
    assert [c["UAT Case ID"] for c in data["cases"]] == [c[0] for c in CASES]
    assert data["cases"][-1]["Test Result"] == "Not Run"
    assert list(data["checks"]) == [c[0] for c in CHECKS]
    assert data["checks"]["DVS-006"]["Status"] == "Proposed"


def test_read_results_bad_bytes_is_empty():
    assert uat_reports.read_results(b"not a workbook") == {"cases": [], "checks": {}}


def test_summarize_counts_and_pass_rate():
    s = uat_reports.summarize(uat_reports.read_results(_workbook())["cases"])
    assert s["total"] == 7
    assert s["by_result"] == {"Pass": 2, "Fail": 1, "Blocked": 1, "Not Run": 2, "Skip": 1}
    assert s["executed"] == 3
    assert abs(s["pass_rate"] - 2 / 3) < 1e-9
    assert s["by_method"][ODM] == {"Pass": 2}
    assert s["by_method"][BROWSER] == {"Fail": 1, "Blocked": 1, "Skip": 1}
    assert s["by_method"][MANUAL] == {"Not Run": 2}
    assert s["by_form"]["F_AA"] == {"Pass": 1, "Fail": 1, "Blocked": 1}
    assert s["by_form"]["F_BB"] == {"Not Run": 2, "Skip": 1, "Pass": 1}


def test_summarize_nothing_executed_has_no_pass_rate():
    s = uat_reports.summarize([{"Test Result": ""}, {"Test Result": "Skip"}])
    assert s["executed"] == 0 and s["pass_rate"] is None
    assert s["by_result"]["Not Run"] == 1 and s["by_result"]["Skip"] == 1
    assert uat_reports.summarize([])["total"] == 0


def test_matrix_sheets_and_one_row_per_case():
    out = uat_reports.build_traceability_matrix(_workbook(), META)
    wb = openpyxl.load_workbook(io.BytesIO(out))
    assert wb.sheetnames == ["Traceability", "Coverage", "Run Info"]
    ws = wb["Traceability"]
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:")
    rows = _rows(out, "Traceability")
    assert [r["UAT Case ID"] for r in rows] == [c[0] for c in CASES]


def test_matrix_row_joins_the_check():
    rows = {r["UAT Case ID"]: r for r in _rows(uat_reports.build_traceability_matrix(_workbook(), META), "Traceability")}
    r = rows["UAT-002"]
    assert r["Rule Description"] == "ITEM1 is required." and r["Rule Type"] == "Required"
    assert r["Test Result"] == "Fail" and r["Test Method"] == BROWSER
    assert r["Form"] == "F_AA" and r["Item Name"] == "ITEM1" and r["Study Event"] == "SE_ONE"
    assert r["Test Scenario"] == SPAN                      # the matrix keeps the text as written
    assert r["Participant"] == "RUN-P001" and r["Executed By"] == "svc-uat"
    assert r["Environment"] == "TEST https://example.invalid/study"
    assert rows["UAT-003"]["Rule Description"] == BARE     # no description: falls back to the message
    assert rows["UAT-003"]["Participant"] == "UAT-P002"    # no Participant_Key: falls back to Participant_ID
    assert rows["UAT-004"]["Rule Description"] == "${ITEM1} + 1"   # no description or message: the expression
    assert rows["UAT-007"]["Test Result"] == "Not Run" and rows["UAT-007"]["Rule Description"] == ""


def test_coverage_values():
    rows = _rows(uat_reports.build_traceability_matrix(_workbook(), META), "Coverage")
    cov = {r["DVS Check ID"]: r for r in rows}
    assert [r["DVS Check ID"] for r in rows] == ["DVS-001", "DVS-002", "DVS-003", "DVS-004", "DVS-005", "DVS-009",
                                                 "DVS-006"]
    assert (cov["DVS-001"]["Covered"], cov["DVS-001"]["Outcome"]) == ("Yes", "Fail")
    assert (cov["DVS-001"]["Cases"], cov["DVS-001"]["Pass"], cov["DVS-001"]["Fail"]) == (2, 1, 1)
    assert (cov["DVS-004"]["Covered"], cov["DVS-004"]["Outcome"]) == ("Yes", "Pass")
    assert (cov["DVS-002"]["Covered"], cov["DVS-002"]["Outcome"]) == ("No — not executed", "")
    assert (cov["DVS-003"]["Not Run"], cov["DVS-003"]["Skip"]) == (1, 1)
    assert (cov["DVS-005"]["Covered"], cov["DVS-005"]["Cases"]) == ("No — no test case", 0)
    assert cov["DVS-005"]["Form"] == "F_BB"                # DVS form name gets the F_ prefix the cases use
    assert cov["DVS-006"]["Covered"] == "Not in build" and rows[-1]["DVS Check ID"] == "DVS-006"
    assert cov["DVS-009"]["Covered"] == "No — not executed"   # known only from UAT_Cases


def test_run_info_has_meta_counts_and_limits():
    info = {r["Item"]: r["Value"] for r in _rows(uat_reports.build_traceability_matrix(_workbook(), META), "Run Info")}
    assert info["Protocol number"] == "PROT-001" and info["Browser step"] == "ran"
    assert info["Total UAT cases"] == 7 and info["Fail"] == 1 and info["Executed (Pass + Fail)"] == 3
    assert info["Pass rate (Pass / executed)"] == "66.7%"
    assert info["Checks in the build"] == 6 and info["Checks covered (at least one Pass or Fail)"] == 2
    assert "UAT-P001 = RUN-P001" in info["Participants (logical ID = OpenClinica ID)"]
    topic, text = uat_reports._limits()
    assert info[topic] == text and "read back unchanged" in text
    assert "stored and read back unchanged" in info["Test methods"]


def test_matrix_keeps_formula_like_text_as_text():
    cases = [("UAT-001", "DVS-001", "F_AA", "ITEM1", "SE_ONE", "=1+1", "x", "=SUM(A1)", "Pass", ODM, "", "UAT-P001")]
    out = uat_reports.build_traceability_matrix(_workbook(cases), META)
    ws = openpyxl.load_workbook(io.BytesIO(out))["Traceability"]
    hdr = [c.value for c in ws[1]]
    cell = ws.cell(row=2, column=hdr.index("Actual Result") + 1)
    assert cell.value == "=SUM(A1)" and cell.data_type == "s"


def test_pdf_is_a_pdf():
    out = uat_reports.build_validation_report(_workbook(), META)
    assert out.startswith(b"%PDF") and _pages(out) >= 1


def test_pdf_survives_300_failed_cases_with_markup():
    texts = [SPAN, BARE, 'cut <span style="color:wh', "a & b < c", "<b>unclosed", "x" * 400, "tab\tand\nnewline"]
    cases = [(f"UAT-{i:03d}", f"DVS-{i % 40:03d}", "F_AA" if i % 2 else "F_BB", f"ITEM{i % 9}", "SE_ONE",
              texts[i % len(texts)], texts[(i + 1) % len(texts)], texts[(i + 2) % len(texts)], "Fail", BROWSER,
              "RUN-P001", "UAT-P001") for i in range(300)]
    cases += [(f"UAT-N{i:03d}", "DVS-100", "F_AA", "ITEM1", "SE_ONE", "s", "e", SPAN, "Not Run", MANUAL, "", "UAT-P001")
              for i in range(200)]
    wb_bytes = _workbook(cases)
    out = uat_reports.build_validation_report(wb_bytes, META)
    assert out.startswith(b"%PDF") and _pages(out) > 3
    assert uat_reports.build_traceability_matrix(wb_bytes, META)[:2] == b"PK"


def test_plain_text_strips_tags_but_keeps_comparisons():
    assert uat_reports._plain(SPAN) == "Weight (kg)"
    assert uat_reports._plain(BARE) == BARE
    assert uat_reports._plain('cut <span style="color:wh') == "cut"
    assert uat_reports._plain("a &amp; b &lt; c") == "a & b < c"
    assert len(uat_reports._plain("y" * 500, 100)) == 100


def test_missing_dvs_sheet():
    wb_bytes = _workbook(dvs=False)
    assert uat_reports.read_results(wb_bytes)["checks"] == {}
    out = uat_reports.build_traceability_matrix(wb_bytes, META)
    rows = _rows(out, "Traceability")
    assert len(rows) == len(CASES) and rows[0]["Rule Description"] == "" and rows[0]["DVS Check ID"] == "DVS-001"
    cov = {r["DVS Check ID"]: r["Covered"] for r in _rows(out, "Coverage")}
    assert cov == {"DVS-001": "Yes", "DVS-002": "No — not executed", "DVS-003": "No — not executed",
                   "DVS-004": "Yes", "DVS-009": "No — not executed"}
    assert uat_reports.build_validation_report(wb_bytes, META).startswith(b"%PDF")


def test_missing_optional_columns():
    hdr = ["UAT Case ID", "Related Check ID", "Scenario", "Expected Result", "Actual Result", "Test Result"]
    wb_bytes = _workbook(case_hdr=hdr)
    data = uat_reports.read_results(wb_bytes)
    assert data["cases"][0]["Test Method"] == "" and data["cases"][0]["Form_OID"] == ""
    s = uat_reports.summarize(data["cases"])
    assert s["by_method"] == {"Not recorded": {"Pass": 2, "Fail": 1, "Blocked": 1, "Not Run": 2, "Skip": 1}}
    rows = _rows(uat_reports.build_traceability_matrix(wb_bytes, META), "Traceability")
    assert rows[0]["Form"] == "F_AA" and rows[0]["Item Name"] == "ITEM1"   # taken from the check instead
    assert rows[0]["Evidence"] == "" and rows[0]["Execution Date"] == META["executed_at"]
    assert uat_reports.build_validation_report(wb_bytes, META).startswith(b"%PDF")


def test_empty_meta_and_empty_workbook():
    wb_bytes = _workbook()
    for meta in ({}, None):
        out = uat_reports.build_traceability_matrix(wb_bytes, meta)
        info = {r["Item"]: r["Value"] for r in _rows(out, "Run Info")}
        assert info["Protocol number"] == "not recorded" and info["Executed by"] == "not recorded"
        assert _rows(out, "Traceability")[0]["Executed By"] == ""
        assert uat_reports.build_validation_report(wb_bytes, meta).startswith(b"%PDF")
    empty = io.BytesIO()
    openpyxl.Workbook().save(empty)
    assert uat_reports.build_validation_report(empty.getvalue(), {}).startswith(b"%PDF")
    assert openpyxl.load_workbook(io.BytesIO(uat_reports.build_traceability_matrix(empty.getvalue(), {}))).sheetnames \
        == ["Traceability", "Coverage", "Run Info"]


def test_report_wording_makes_no_outcome_claim():
    src = open(os.path.join(_REPO, "uat_reports.py"), encoding="utf-8").read().split('"""', 2)[2]
    assert "validated" not in src.lower() and "study passed" not in src.lower()


# ── Delivery by the UAT loader ────────────────────────────────────────────────
# Run of 2026-10-10: the two monday columns stayed empty and no log line mentioned them. Nothing generated or
# uploaded the files: the column ids were constants marked "future".

def _deliver(monkeypatch, results, fail=None):
    import asyncio
    import uat_loader
    uploads, log = [], []

    async def upload_file(item_id, col, name, data):
        if fail and fail in name:
            raise RuntimeError("upload refused")
        uploads.append((col, name, data[:5]))

    async def append_log(item_id, msg):
        log.append(msg)

    monkeypatch.setattr(uat_loader, "upload_file", upload_file)
    monkeypatch.setattr(uat_loader, "append_log", append_log)
    done = asyncio.run(uat_loader._deliver_uat_reports("1", results, {"protocol_number": "T-1"}, "T-1"))
    return done, uploads, log


def test_loader_uploads_both_documents_to_their_columns_and_logs_each(monkeypatch):
    done, uploads, log = _deliver(monkeypatch, _workbook())
    assert done == {"matrix": True, "report": True}
    assert [(c, n) for c, n, _ in uploads] == [("file_mm3h7r4", "T-1_UAT_Traceability_Matrix.xlsx"),
                                              ("file_mm3hvbpb", "T-1_UAT_Validation_Report.pdf")]
    assert uploads[0][2][:2] == b"PK" and uploads[1][2][:4] == b"%PDF"
    assert any("UAT Traceability Matrix uploaded" in m for m in log)
    assert any("UAT Validation Report uploaded" in m for m in log)


def test_one_document_failing_does_not_stop_the_other_and_is_logged(monkeypatch):
    done, uploads, log = _deliver(monkeypatch, _workbook(), fail="Traceability")
    assert done == {"matrix": False, "report": True} and len(uploads) == 1
    assert any("UAT Traceability Matrix was not delivered: upload refused" in m for m in log)


def test_reports_kill_switch_says_so_in_the_log(monkeypatch):
    monkeypatch.setenv("UAT_REPORTS", "0")
    done, uploads, log = _deliver(monkeypatch, _workbook())
    assert done == {"matrix": False, "report": False} and not uploads
    assert any("UAT_REPORTS=0" in m for m in log)


def test_the_uat_load_delivers_the_reports_after_the_results_workbook():
    import uat_loader
    src = open(uat_loader.__file__).read()
    body = src[src.index("async def run_uat_loader("):]
    assert body.index("UAT_DVS_RESULTS_COL") < body.index("await _deliver_uat_reports(")
    assert uat_loader.UAT_MATRIX_COL == "file_mm3h7r4" and uat_loader.UAT_REPORT_COL == "file_mm3hvbpb"
