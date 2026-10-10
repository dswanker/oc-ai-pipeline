"""
UAT-only rerun: "Send to AI" on an item that already has its study, with Load DVS UAT Data checked and Publish
to Test unchecked, runs the UAT load, browser tests, read-back and reports and nothing else. It first rebuilds
the DVS from the Study Specification and the EDC Build already on the item, so the cases carry today's dates and
the current case generator; no form is built, uploaded or published. See docs/UAT_RERUN.md.
"""
import ast
import io
import os
import sys
import types
import zipfile

import openpyxl

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "skills", "dvs-specification", "scripts"))

from monday_client import COL  # noqa: E402

_SRC = open(os.path.join(_REPO, "pipeline.py")).read()


def _fns():
    """The pure helpers compiled out of pipeline.py (as tests/build/conftest.py does)."""
    want = {"_forms_json_from_edc_zip", "_uat_only_requested", "_uat_only_regen_dvs_enabled"}
    parts = [ast.get_source_segment(_SRC, n) for n in ast.parse(_SRC).body
             if isinstance(n, ast.FunctionDef) and n.name in want]
    ns = {"os": os, "io": io, "zipfile": zipfile, "COL": COL}
    exec(compile("\n\n".join(parts), "<pipeline_uat_only>", "exec"), ns)  # noqa: S102
    return types.SimpleNamespace(**{k: ns[k] for k in want})


P = _fns()


def _cols(uuid="u-1", load="v", publish=""):
    return {COL["study_uuid"]: {"text": uuid}, COL["load_dvs_uat_data"]: {"text": load},
            "boolean_mm3g2vzf": {"text": publish}}


def test_uat_only_needs_the_study_the_load_box_and_no_publish():
    assert P._uat_only_requested(_cols()) is True
    assert P._uat_only_requested(_cols(uuid="")) is False          # no study yet: the full run creates it
    assert P._uat_only_requested(_cols(load="")) is False
    assert P._uat_only_requested(_cols(publish="v")) is False      # publish checked: the full run
    assert P._uat_only_requested({}) is False


def test_regeneration_kill_switch(monkeypatch):
    assert P._uat_only_regen_dvs_enabled() is True
    monkeypatch.setenv("UAT_ONLY_REGEN_DVS", "0")
    assert P._uat_only_regen_dvs_enabled() is False


def _edc_zip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "survey"
    ws.append(["type", "name", "label", "bind::oc:itemgroup", "required", "constraint"])
    ws.append(["integer", "ITEM1", "Count", "AA", "yes", ". >= 0 and . <= 10"])
    ws.append(["select_one yn", "ITEM2", "Done?", "AA", None, None])
    ws.append([None, None, None, None, None, None])
    ch = wb.create_sheet("choices")
    ch.append(["list_name", "name", "label"])
    ch.append(["yn", "Y", "Yes"])
    ch.append(["yn", "N", "No"])
    buf = io.BytesIO()
    wb.save(buf)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("T_EDC_Build/forms/AA.xlsx", buf.getvalue())
        f.writestr("T_EDC_Build/checklist/T_Build_Checklist.xlsx", buf.getvalue())   # not a form
        f.writestr("T_EDC_Build/BUILD_README.txt", "x")
    return z.getvalue()


def test_forms_are_read_from_the_edc_build_as_they_were_built():
    forms = P._forms_json_from_edc_zip(_edc_zip())["forms"]
    assert list(forms) == ["AA.xlsx"]
    assert forms["AA.xlsx"]["survey"] == [
        {"type": "integer", "name": "ITEM1", "label": "Count", "bind::oc:itemgroup": "AA", "required": "yes",
         "constraint": ". >= 0 and . <= 10"},
        {"type": "select_one yn", "name": "ITEM2", "label": "Done?", "bind::oc:itemgroup": "AA"}]
    assert [c["name"] for c in forms["AA.xlsx"]["choices"]] == ["Y", "N"]


def test_the_cases_are_generated_from_those_forms():
    import contextlib
    from extract_dvs_from_forms import extract_dvs_data
    struct = {"study_meta": {"protocol_number": "T"},
              "forms": [{"form_id": "AA", "form_title": "Form A", "visits_assigned": ["SE_ONE"]}]}
    with contextlib.redirect_stdout(io.StringIO()):
        cases = extract_dvs_data(struct, P._forms_json_from_edc_zip(_edc_zip()))["uat_cases"]
    assert {c["Item_Name"] for c in cases} == {"ITEM1"} and len(cases) == 7       # 5 range cases + blank + happy
    assert all(c["Study_Event_OID"] == "SE_ONE" and c["Form_OID"] == "F_AA" for c in cases)


def _uat_only_branch():
    start = _SRC.index("        if _uat_only_requested(cols):")
    return _SRC[start:_SRC.index("            return  # ← exit run_pipeline; nothing else to do", start)]


def test_the_rerun_regenerates_the_dvs_then_loads_and_does_nothing_else():
    branch = _uat_only_branch()
    assert branch.index("regenerate_dvs_for_item(item_id)") < branch.index("await run_uat_loader(item_id")
    assert "_uat_only_regen_dvs_enabled()" in branch
    for forbidden in ("create_oc_study", "publish_to_test", "_import_board", "run_edc_build", "call_claude",
                      "oc_form_publisher"):
        assert forbidden not in branch, forbidden


def test_regeneration_reads_only_what_is_on_the_item_and_writes_only_the_dvs_column():
    fn = next(ast.get_source_segment(_SRC, n) for n in ast.parse(_SRC).body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "regenerate_dvs_for_item")
    assert 'COL["spec_json"]' in fn and 'COL["edc_build"]' in fn
    assert fn.count("upload_file(") == 1 and 'COL["dvs_output"]' in fn
    assert "call_claude" not in fn and "httpx" not in fn


def test_admin_route_and_rerun_read_the_build_the_same_way():
    main_src = open(os.path.join(_REPO, "main.py")).read()
    assert "_forms_json_from_edc_zip(edc_bytes)" in main_src
