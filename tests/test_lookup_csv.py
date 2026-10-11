"""
lookup_csv: the study's lookup CSVs work in every study, and no rule compares a looked-up display label.

Build of 2026-10-10: a form said pulldata('<lower-cased protocol>_tpt', ...) while the build's file kept the
protocol number as written, the lookups were never attached to the form upload, and the visit-date rules compared
the looked-up label to a literal that the schedule's own labels no longer contained.
"""
import ast
import io
import os
import sys
import tempfile

import openpyxl
import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "skills", "edc-builder", "scripts"))

import lookup_csv as lc  # noqa: E402
import vocab_attach  # noqa: E402

EVENT_CF = ("instance('clinicaldata')/ODM/ClinicalData/SubjectData/StudyEventData[@OpenClinica:Current='Yes']"
            "/@StudyEventOID")


def _spec(protocol="AbC-12_X", label_two="Day 1 dosing"):
    return {
        "study_meta": {"protocol_number": protocol},
        "timepoint_csv": {"filename": f"{protocol}_tpt.csv", "rows": [
            {"event": "SE_ONE", "timepoint": "Screening", "visit_number": 1},
            {"event": "SE_TWO", "timepoint": label_two, "visit_number": 2},
            {"event": "SE_THREE", "timepoint": "Week 4", "visit_number": 3},
            {"event": "SE_ANY", "timepoint": "Ongoing", "type": "common"}]},
        "labranges_csv": {"filename": f"{protocol}_labranges.csv", "columns": ["test_code", "lower"], "rows": []},
        "events": [{"event_oid": "SE_ANY", "event_type": "common", "is_repeating": True}],
        "forms": [
            {"form_id": "VD", "visits_assigned": ["SE_ONE", "SE_TWO", "SE_THREE"], "survey": [
                {"type": "calculate", "name": "EVT", "calculation": EVENT_CF, "bind__oc_external": "clinicaldata"},
                {"type": "calculate", "name": "TPT", "bind__oc_itemgroup": "VD",
                 "calculation": "pulldata('abc12_x_tpt','timepoint','event',${EVT})"},
                {"type": "text", "name": "SHOWN", "calculation": "${TPT}", "readonly": "yes"},
                {"type": "select_one NY", "name": "DONE", "relevant": "${TPT} != 'Baseline'"},
                {"type": "date", "name": "WHEN", "relevant": "${DONE}='Y' or ${SHOWN}='Baseline'"},
                {"type": "text", "name": "LATE", "relevant": "${TPT} = 'Week 4'"},
                {"type": "text", "name": "NEVER", "relevant": "${TPT} = 'No such visit'"}]},
            {"form_id": "DOSE", "visits_assigned": ["SE_TWO", "SE_THREE"], "survey": [
                {"type": "date", "name": "GIVEN", "concept": "EXSTDAT"}]},
            {"form_id": "LAB", "visits_assigned": ["SE_ONE"], "survey": [
                {"type": "csv-external", "name": "labranges"},
                {"type": "calculate", "name": "LOW", "calculation": "pulldata('labranges','lower','test_code','WBC')"},
                {"type": "calculate", "name": "UNIT",
                 "calculation": "instance('labranges')/root/item[test_code='WBC']/unit"},
                {"type": "select_one_from_file units_demo.csv", "name": "U"}]}]}


def _rules(spec):
    return {(f["form_id"], r["name"]): (r.get("relevant"), r.get("calculation"))
            for f in spec["forms"] for r in f["survey"]}


# ── names ─────────────────────────────────────────────────────────────────────

def test_one_spelling_for_the_file_and_the_reference():
    assert lc.lookup_name("AbC-12_X", lc.TPT) == "abc12_x_tpt"
    assert lc.lookup_name("Ab 12/x.Y", lc.LABRANGES) == "ab12xy_labranges"
    assert lc.lookup_name("", lc.TPT) == "study_tpt"


def test_files_and_every_reference_get_the_same_name():
    spec = _spec()
    report = lc.normalize_spec(spec)
    assert spec["timepoint_csv"]["filename"] == "abc12_x_tpt.csv"
    assert spec["labranges_csv"]["filename"] == "abc12_x_labranges.csv"
    rules = _rules(spec)
    assert rules[("VD", "TPT")][1] == "pulldata('abc12_x_tpt','timepoint','event',${EVT})"
    assert rules[("LAB", "LOW")][1] == "pulldata('abc12_x_labranges','lower','test_code','WBC')"
    assert rules[("LAB", "UNIT")][1] == "instance('abc12_x_labranges')/root/item[test_code='WBC']/unit"
    assert spec["forms"][2]["survey"][0]["name"] == "abc12_x_labranges"          # the declaring row follows
    assert spec["forms"][2]["survey"][3]["type"] == "select_one_from_file units_demo.csv"     # not a study lookup
    assert rules[("VD", "EVT")][1] == EVENT_CF                                   # clinicaldata is left alone
    assert report["references"] == 3 and len(report["files"]) == 2
    assert spec["study_meta"]["lookup_csv"]["names"] == {"tpt": "abc12_x_tpt", "labranges": "abc12_x_labranges"}


def test_normalising_twice_changes_nothing():
    spec = _spec()
    lc.normalize_spec(spec)
    import copy
    once = copy.deepcopy(spec["forms"]), copy.deepcopy(spec["timepoint_csv"])
    again = lc.normalize_spec(spec)
    assert (spec["forms"], spec["timepoint_csv"]) == once
    assert again["references"] == 0 and again["files"] == [] and again["rules"] == []


def test_names_are_left_alone_when_switched_off(monkeypatch):
    monkeypatch.setenv("LOOKUP_CSV_CANONICAL", "0")
    spec = _spec()
    lc.normalize_spec(spec)
    assert spec["timepoint_csv"]["filename"] == "AbC-12_X_tpt.csv"
    assert _rules(spec)[("LAB", "LOW")][1] == "pulldata('labranges','lower','test_code','WBC')"


# ── stable values ─────────────────────────────────────────────────────────────

def test_the_lookup_gains_a_visit_type_and_a_baseline_flag():
    spec = _spec()
    report = lc.normalize_spec(spec)
    rows = {r["event"]: (r["visit_type"], r["baseline"]) for r in spec["timepoint_csv"]["rows"]}
    assert rows == {"SE_ONE": ("SCHEDULED", "0"), "SE_TWO": ("SCHEDULED", "1"), "SE_THREE": ("SCHEDULED", "0"),
                    "SE_ANY": ("COMMON", "0")}
    assert report["baseline_event"] == "SE_TWO"            # the earliest visit with the exposure start date


def test_without_an_exposure_date_no_visit_is_the_baseline():
    spec = _spec()
    spec["forms"][1]["survey"][0].pop("concept")
    assert lc.normalize_spec(spec)["baseline_event"] is None
    assert {r["baseline"] for r in spec["timepoint_csv"]["rows"]} == {"0"}


def test_a_rule_on_the_looked_up_label_is_rewritten_to_a_stable_value():
    spec = _spec()
    report = lc.normalize_spec(spec)
    rules = _rules(spec)
    assert rules[("VD", "DONE")][0] == "${TPTBASE} != '1'"
    assert rules[("VD", "WHEN")][0] == "${DONE}='Y' or ${TPTBASE} = '1'"          # through an item that copies it
    assert rules[("VD", "LATE")][0] == "${EVT} = 'SE_THREE'"                      # a real label: its event
    assert rules[("VD", "NEVER")][0] == "${TPT} = 'No such visit'"                # no such label: reported
    assert report["unresolved"] == [{"form": "VD", "item": "TPT", "literal": "No such visit"}]
    names = [r["name"] for r in spec["forms"][0]["survey"]]
    assert names.index("TPTBASE") == names.index("TPT") + 1 and names.count("TPTBASE") == 1
    flag = spec["forms"][0]["survey"][names.index("TPTBASE")]
    assert flag["calculation"] == "pulldata('abc12_x_tpt','baseline','event',${EVT})"
    assert flag["type"] == "calculate" and flag["bind__oc_itemgroup"] == "VD"
    assert len(report["rules"]) == 3


def test_a_renamed_visit_label_does_not_change_any_rule():
    first, renamed = _spec(), _spec(label_two="Baseline / first dose (Day 0)")
    lc.normalize_spec(first)
    lc.normalize_spec(renamed)
    assert _rules(first) == _rules(renamed)
    # and renaming a visit in a specification that was already normalised leaves its rules as they are
    before = _rules(first)
    for row in first["timepoint_csv"]["rows"]:
        row["timepoint"] = "Renamed " + row["timepoint"]
    lc.normalize_spec(first)
    assert _rules(first) == before
    assert not any("Baseline" in str(v) or "Week 4" in str(v) for pair in before.values() for v in pair
                   if pair is not before[("VD", "NEVER")])


def test_rules_are_left_alone_when_switched_off(monkeypatch):
    monkeypatch.setenv("LOOKUP_STABLE_CODES", "0")
    spec = _spec()
    lc.normalize_spec(spec)
    assert _rules(spec)[("VD", "DONE")][0] == "${TPT} != 'Baseline'"
    assert "baseline" not in spec["timepoint_csv"]["rows"][0]


def test_the_timepoint_file_carries_the_stable_columns_only_when_the_rows_do():
    from build_xlsforms import write_timepoint_csv
    spec = _spec()
    with tempfile.TemporaryDirectory() as t:
        write_timepoint_csv(spec["timepoint_csv"], os.path.join(t, "old.csv"), {})
        assert open(os.path.join(t, "old.csv")).read().splitlines()[:2] == ["event,timepoint", "SE_ONE,Screening"]
        lc.normalize_spec(spec)
        write_timepoint_csv(spec["timepoint_csv"], os.path.join(t, "new.csv"), {})
        lines = open(os.path.join(t, "new.csv")).read().splitlines()
        assert lines[0] == "event,timepoint,visit_number,visit_type,baseline"
        assert lines[2] == "SE_TWO,Day 1 dosing,2,SCHEDULED,1" and lines[4] == "SE_ANY,Ongoing,,COMMON,0"


# ── the build has what the forms name ─────────────────────────────────────────

def _xlsx(path, survey, choices=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "survey"
    ws.append(["type", "name", "label", "calculation", "bind::oc:external", "choice_filter"])
    for row in survey:
        ws.append(list(row) + [None] * (6 - len(row)))
    ch = wb.create_sheet("choices")
    ch.append(["list_name", "name", "label"])
    for c in choices:
        ch.append(list(c))
    wb.save(path)


def _build(tmp_path, csv_name="s1_tpt.csv", header="event,timepoint,visit_number,visit_type,baseline"):
    forms, csv = tmp_path / "forms", tmp_path / "csv"
    forms.mkdir()
    csv.mkdir()
    _xlsx(forms / "VD.xlsx", [
        ("calculate", "EVT", "", EVENT_CF, "clinicaldata"),
        ("calculate", "TPT", "", "pulldata('s1_tpt','timepoint','event',${EVT})"),
        ("calculate", "BASE", "", "pulldata('s1_tpt','baseline','event',${EVT})"),
        ("select_one sites", "SITE", "Site", None, None, "instance('sites')/root/item[name = 'a']"),
        ("select_one_from_file units_demo.csv", "U", "Unit")], choices=[("sites", "a", "A")])
    _xlsx(forms / "PLAIN.xlsx", [("text", "A", "A")])
    (csv / csv_name).write_text(header + "\r\nSE_ONE,Screening,1,SCHEDULED,0\r\n")
    (forms / "units_demo.csv").write_text("name,label\r\nmg,mg\r\n")
    return forms, csv


def test_a_form_names_its_lookups_with_the_columns_it_reads(tmp_path):
    forms, _csv = _build(tmp_path)
    assert lc.xlsx_references(forms / "VD.xlsx") == {
        "s1_tpt.csv": {"timepoint", "event", "baseline"}, "units_demo.csv": set()}     # no clinicaldata, no own list
    assert lc.xlsx_references(forms / "PLAIN.xlsx") == {}


def test_a_build_with_every_lookup_passes(tmp_path):
    forms, csv = _build(tmp_path)
    assert lc.validate_build(str(forms), [str(csv)]) == []


def test_a_lookup_under_another_spelling_is_a_build_error(tmp_path):
    forms, csv = _build(tmp_path, csv_name="S1_tpt.csv")
    errors = lc.validate_build(str(forms), [str(csv)])
    assert [e["form_id"] for e in errors] == ["VD"]
    assert "lookup file s1_tpt.csv is referenced by the form but is not in the build" in errors[0]["error"]
    assert "the build has S1_tpt.csv: the name must match exactly, including case" in errors[0]["error"]


def test_a_missing_lookup_or_a_missing_column_is_a_build_error(tmp_path):
    forms, csv = _build(tmp_path, header="event,timepoint")
    errors = lc.validate_build(str(forms), [str(csv)])
    assert errors == [{"form_id": "VD", "error": "lookup file s1_tpt.csv has no column baseline that the form reads"}]
    os.remove(csv / "s1_tpt.csv")
    os.remove(forms / "units_demo.csv")
    assert sorted(e["error"].split(" is referenced")[0] for e in lc.validate_build(str(forms), [str(csv)])) == \
        ["lookup file s1_tpt.csv", "lookup file units_demo.csv"]


# ── the upload carries them ───────────────────────────────────────────────────

def test_a_form_is_uploaded_with_the_lookups_it_reads_from_both_folders(tmp_path):
    forms, csv = _build(tmp_path)
    (csv / "s1_labranges.csv").write_text("test_code,lower\r\n")          # not read by this form
    assert [p.name for p in vocab_attach.files_to_attach(forms / "VD.xlsx")] == ["s1_tpt.csv", "units_demo.csv"]
    assert vocab_attach.files_to_attach(forms / "VD.xlsx")[0].parent.name == "csv"
    assert vocab_attach.files_to_attach(forms / "PLAIN.xlsx") == []


def test_only_the_files_beside_the_form_are_attached_when_switched_off(tmp_path, monkeypatch):
    forms, _csv = _build(tmp_path)
    monkeypatch.setenv("LOOKUP_CSV_ATTACH", "0")
    assert [p.name for p in vocab_attach.files_to_attach(forms / "VD.xlsx")] == ["units_demo.csv"]


def test_the_publisher_attaches_through_that_function():
    src = open(os.path.join(_REPO, "oc_form_publisher.py")).read()
    assert "_sibling_csvs = files_to_attach(xlsx_path)" in src
    assert "csvs_to_attach(" not in src


# ── the pipeline's own form and build ─────────────────────────────────────────

def _pipeline_fns(*names):
    src = open(os.path.join(_REPO, "pipeline.py")).read()
    ns = {"os": os, "re": __import__("re"), "json": __import__("json"), "tempfile": tempfile,
          "SKILLS_DIR": os.path.join(_REPO, "skills"), "sys": sys}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "pipeline.py", "exec"), ns)
    return ns


def _visit_form(spec):
    return {r["name"]: r for r in spec["forms"][0]["survey"] if r.get("name")}


def test_the_injected_visit_date_form_reads_the_flag_under_the_build_s_name():
    ensure = _pipeline_fns("_ensure_required_forms")["_ensure_required_forms"]
    with open(os.devnull, "w") as null:
        import contextlib
        with contextlib.redirect_stdout(null):
            spec = ensure({"study_meta": {"protocol_number": "AbC-12_X"}, "forms": []}, "AbC-12_X", {})
    rows = _visit_form(spec)
    assert rows["TPTCALC"]["calculation"] == "pulldata('abc12_x_tpt','timepoint','event',${EVENT_CF})"
    assert rows["TPTBASE"]["calculation"] == "pulldata('abc12_x_tpt','baseline','event',${EVENT_CF})"
    assert rows["VISYN"]["relevant"] == "${TPTBASE} != '1'"
    assert rows["VISDT"]["relevant"] == "${VISYN}='Y' or ${TPTBASE} = '1'"
    assert not any("'Baseline'" in str(v) for r in rows.values() for v in r.values())


def test_the_injected_form_is_as_before_when_both_switches_are_off(monkeypatch):
    monkeypatch.setenv("LOOKUP_STABLE_CODES", "0")
    monkeypatch.setenv("LOOKUP_CSV_CANONICAL", "0")
    ensure = _pipeline_fns("_ensure_required_forms")["_ensure_required_forms"]
    import contextlib
    with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
        spec = ensure({"study_meta": {"protocol_number": "AbC-12_X"}, "forms": []}, "AbC-12_X", {})
    rows = _visit_form(spec)
    assert "TPTBASE" not in rows and rows["VISYN"]["relevant"] == "${TPTCALC} != 'Baseline'"
    assert rows["TPTCALC"]["calculation"] == "pulldata('abc12_x_tpt','timepoint','event',${EVENT_CF})"


def test_a_build_of_a_mixed_case_study_has_every_lookup_under_the_name_its_forms_use():
    fns = _pipeline_fns("_ensure_required_forms", "_add_scripts", "run_edc_build")
    import contextlib
    import zipfile
    spec = {"study_meta": {"protocol_number": "AbC-12_X", "study_id": "AbC-12_X", "study_title": "T"},
            "timepoint_csv": {"filename": "AbC-12_X_tpt.csv", "rows": [
                {"event": "SE_ONE", "timepoint": "Screening", "visit_number": 1, "arm": "ALL"}]},
            "labranges_csv": {"filename": "AbC-12_X_labranges.csv", "columns": ["test_code"], "rows": []},
            "forms": [], "schedule_of_events": {}, "study_settings": {}, "review_flags": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        spec = fns["_ensure_required_forms"](spec, "AbC-12_X", {})
        zip_bytes, log, _fj = fns["run_edc_build"](spec)
    names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
    assert [n.rsplit("/", 1)[1] for n in names if n.endswith(".csv")] == ["abc12_x_labranges.csv", "abc12_x_tpt.csv"]
    assert not [e for e in log["build_errors"] if "lookup" in str(e)]
    assert spec["timepoint_csv"]["filename"] == "abc12_x_tpt.csv"
    readme = zipfile.ZipFile(io.BytesIO(zip_bytes)).read([n for n in names if n.endswith("BUILD_README.txt")][0]).decode()
    assert "abc12_x_tpt.csv" in readme and "select the .xlsx and the .csv together" in readme


def test_the_build_reports_a_lookup_it_does_not_hold(monkeypatch):
    """With the names left as written (switch off) the mixed-case study breaks exactly as on 2026-10-10: the
    validation now says so as a build error instead of the form failing when it is opened."""
    monkeypatch.setenv("LOOKUP_CSV_CANONICAL", "0")
    fns = _pipeline_fns("_ensure_required_forms", "_add_scripts", "run_edc_build")
    import contextlib
    spec = {"study_meta": {"protocol_number": "AbC-12_X", "study_id": "AbC-12_X", "study_title": "T"},
            "timepoint_csv": {"filename": "AbC-12_X_tpt.csv", "rows": [
                {"event": "SE_ONE", "timepoint": "Screening", "visit_number": 1, "arm": "ALL"}]},
            "labranges_csv": {"filename": "AbC-12_X_labranges.csv", "columns": ["test_code"], "rows": []},
            "forms": [], "schedule_of_events": {}, "study_settings": {}, "review_flags": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        spec = fns["_ensure_required_forms"](spec, "AbC-12_X", {})
        _zip, log, _fj = fns["run_edc_build"](spec)
    errors = [e for e in log["build_errors"] if "lookup file" in str(e.get("error"))]
    assert len(errors) == 1 and errors[0]["form_id"] == "DOV"
    assert "abc12_x_tpt.csv is referenced by the form but is not in the build" in errors[0]["error"]
