"""usdm_input.py: USDM 4.0 as an accepted input. Small synthetic USDM document (no network, no CT download);
the DDF-RA examples are covered by tests/usdm/. Includes an end-to-end pass through the real conventions engine,
EDC builder and calendaring-rule extraction."""
import ast, contextlib, copy, io, json, os, sys, tempfile
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "edc-builder", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "calendaring-rules", "scripts"))
import cdisc_ct as c
import usdm_input as u

HDR = ("Code\tCodelist Code\tCodelist Extensible (Yes/No)\tCodelist Name\tCDISC Submission Value\t"
       "CDISC Synonym(s)\tCDISC Definition\tNCI Preferred Term\n")
ROWS = [("C66734", "", "No", "SDTM Domain Abbreviation", "DOMAIN", "", "", ""),
        ("C49622", "C66734", "", "", "VS", "Vital Signs", "", "Vital Signs Domain"),
        ("C49562", "C66734", "", "", "AE", "Adverse Events", "", "Adverse Event Domain"),
        ("C49626", "C66734", "", "", "EG", "ECG Test Results", "", "ECG Test Results Domain"),
        ("C66741", "", "Yes", "Vital Signs Test Name", "VSTEST", "", "", ""),
        ("C25208", "C66741", "", "", "Weight", "", "", "Weight"),
        ("C25347", "C66741", "", "", "Height", "", "", "Height")]
STD = c.Standards(c.CTPackage(c._parse_evs(HDR + "".join("\t".join(r) + "\n" for r in ROWS)), "test", "2026-01-01"), {}, {})


def _code(code, decode):
    return {"id": "c", "code": code, "codeSystem": "x", "codeSystemVersion": "1", "decode": decode, "instanceType": "Code"}


def _timing(i, kind, value, frm, to, lo=None, hi=None, label=""):
    return {"id": f"T{i}", "name": f"T{i}", "label": label, "type": _code("C1", kind), "value": value, "valueLabel": value,
            "relativeToFrom": _code("C2", "Start to Start"), "relativeFromScheduledInstanceId": frm,
            "relativeToScheduledInstanceId": to, "windowLower": lo, "windowUpper": hi,
            "windowLabel": f"-{lo}..+{hi}" if lo else None, "instanceType": "Timing"}


def _inst(i, enc, acts, epoch="EP1"):
    return {"id": f"I{i}", "name": f"I{i}", "encounterId": enc, "epochId": epoch, "activityIds": acts,
            "instanceType": "ScheduledActivityInstance"}


def _doc(version="4.0.0"):
    enc = [{"id": "E1", "name": "V1", "label": "Screening", "type": _code("C25716", "Visit"), "previousId": None, "nextId": "E2",
            "instanceType": "Encounter"},
           {"id": "E2", "name": "V2", "label": "Day 1", "type": _code("C25716", "Visit"), "previousId": "E1", "nextId": "E3",
            "instanceType": "Encounter"},
           {"id": "E3", "name": "V3", "label": "Week 2", "type": _code("C25716", "Visit"), "previousId": "E2", "nextId": "E4",
            "instanceType": "Encounter"},
           {"id": "E4", "name": "V4", "label": "Day -1", "type": _code("C25716", "Visit"), "previousId": "E3", "nextId": None,
            "instanceType": "Encounter"}]
    act = lambda i, name, **kw: {"id": i, "name": name, "label": name, "childIds": [], "biomedicalConceptIds": [],
                                 "bcSurrogateIds": [], "definedProcedures": [], "timelineId": None,
                                 "instanceType": "Activity", **kw}
    acts = [act("A1", "Informed consent"), act("A2", "Vital Signs", timelineId="TL2"), act("A3", "Blood pressure supine"),
            act("A4", "Demographics", biomedicalConceptIds=["BC1"]), act("A5", "Safety labs", childIds=["A6"]),
            act("A6", "Hematology panel"), act("A7", "Adverse event review"), act("A8", "Height and Weight"),
            act("A9", "Quality of life diary")]
    main = {"id": "TL1", "name": "Main", "mainTimeline": True, "entryCondition": "Enrolled", "instanceType": "ScheduleTimeline",
            "instances": [_inst(1, "E1", ["A1", "A4", "A2"]), _inst(2, "E2", ["A2", "A5", "A8"]),
                          _inst(3, "E3", ["A2", "A9"], "EP2"), _inst(4, "E4", ["A5"])],
            "timings": [_timing(1, "Before", "P2W", "I1", "I2", "P3D", "P0D", "Screening"),
                        _timing(2, "Fixed Reference", "P1D", "I2", "I2", label="Day 1"),
                        _timing(3, "After", "P14D", "I3", "I2", "P2D", "PT12H", "Week 2"),
                        _timing(4, "Before", "P1D", "I4", "I2")]}
    sub = {"id": "TL2", "name": "BP", "mainTimeline": False, "entryCondition": "Automatic", "instanceType": "ScheduleTimeline",
           "instances": [_inst(9, None, ["A3"])], "timings": []}
    ae = {"id": "TL3", "name": "Adverse Event Timeline", "mainTimeline": False, "entryCondition": "Subject has an AE",
          "instanceType": "ScheduleTimeline", "instances": [_inst(8, None, ["A7"])], "timings": []}
    design = {"id": "SD1", "name": "Design", "instanceType": "InterventionalStudyDesign", "encounters": enc, "activities": acts,
              "arms": [{"id": "ARM1", "name": "Active", "label": "Active drug", "description": "Active", "type": _code("C1", "Arm"),
                        "instanceType": "StudyArm"},
                       {"id": "ARM2", "name": "Placebo", "label": "Placebo", "description": "", "type": _code("C1", "Arm"),
                        "instanceType": "StudyArm"}],
              "epochs": [{"id": "EP1", "name": "Screening", "type": _code("C1", "Screening"), "previousId": None, "nextId": "EP2",
                          "instanceType": "StudyEpoch"},
                         {"id": "EP2", "name": "Treatment", "type": _code("C1", "Treatment"), "previousId": "EP1", "nextId": None,
                          "instanceType": "StudyEpoch"}],
              "studyCells": [{"id": "C1", "armId": "ARM1", "epochId": "EP1", "elementIds": [], "instanceType": "StudyCell"},
                             {"id": "C2", "armId": "ARM2", "epochId": "EP1", "elementIds": [], "instanceType": "StudyCell"},
                             {"id": "C3", "armId": "ARM1", "epochId": "EP2", "elementIds": [], "instanceType": "StudyCell"}],
              "scheduleTimelines": [main, sub, ae],
              "eligibilityCriteria": [
                  {"id": "X1", "name": "EX01", "identifier": "1", "category": _code("C25370", "Exclusion Criteria"),
                   "criterionItemId": "CI2", "instanceType": "EligibilityCriterion"},
                  {"id": "N2", "name": "IN02", "identifier": "2", "category": _code("C25532", "Inclusion Criteria"),
                   "criterionItemId": "CI3", "instanceType": "EligibilityCriterion"},
                  {"id": "N1", "name": "IN01", "identifier": "1", "category": _code("C25532", "Inclusion Criteria"),
                   "criterionItemId": "CI1", "instanceType": "EligibilityCriterion"}]}
    ver = {"id": "V1", "versionIdentifier": "1", "rationale": "x", "instanceType": "StudyVersion", "studyDesigns": [design],
           "studyIdentifiers": [{"id": "SI1", "text": "ABC-123", "scopeId": "O1", "instanceType": "StudyIdentifier"}],
           "titles": [{"id": "TT1", "text": "A study", "type": _code("C1", "Official Study Title"), "instanceType": "StudyTitle"}],
           "biomedicalConcepts": [{"id": "BC1", "name": "Sex", "label": "Sex", "reference": "/mdr/bc/C28421",
                                   "code": {"standardCode": _code("C28421", "Sex")}, "instanceType": "BiomedicalConcept"}],
           "eligibilityCriterionItems": [
               {"id": "CI1", "name": "IN01", "text": "<p>Age &ge; <usdm:tag name=\"min_age\"/> years</p>", "instanceType": "EligibilityCriterionItem"},
               {"id": "CI2", "name": "EX01", "text": "<p>Pregnant</p>", "instanceType": "EligibilityCriterionItem"},
               {"id": "CI3", "name": "IN02", "text": "<ul><li>Signed consent</li></ul>", "instanceType": "EligibilityCriterionItem"}]}
    return {"usdmVersion": version, "systemName": "t", "systemVersion": "1",
            "study": {"id": "S1", "name": "ABC", "instanceType": "Study", "versions": [ver]}}


def _spec():
    f = lambda fid, title, dom, visits: {"form_id": fid, "form_title": title, "cdash_domain": dom, "visits_assigned": visits,
                                         "choices": [], "survey": [{"type": "text", "name": fid + "TXT", "label": "Value"}]}
    return {"study_meta": {"protocol_number": "ABC-123", "arms": [{"arm_name": "Placebo", "arm_code": "PBO", "planned_enrollment": 10}]},
            "timepoint_csv": {"filename": "abc_tpt.csv", "rows": [
                {"event": "SE_SCREEN", "timepoint": "Screening", "visit_number": 1, "arm": "BOTH"},
                {"event": "SE_MONTH_6", "timepoint": "Month 6", "visit_number": 2, "arm": "BOTH"},
                {"event": "SE_COMMON", "timepoint": "Common", "visit_number": 3, "arm": "BOTH"}]},
            "forms": [f("VS", "Vital Signs", "VS", ["SE_SCREEN"]), f("DM", "Demographics", "DM", ["SE_MONTH_6"]),
                      f("AE", "Adverse Events", "AE", ["SE_COMMON"]), f("ICF", "Informed Consent", "", ["SE_SCREEN", "SE_MONTH_6"]),
                      f("QOL", "Diary", "", ["SE_SCREEN", "SE_MONTH_6"]), f("LBHEM", "Hematology Panel", "LB", [])]}


def test_only_usdm_4_0_is_accepted():
    assert u.check_version(_doc("4.0.0")) == "4.0.0" and u.check_version(_doc("4.0.3")) == "4.0.3"
    for bad in ("3.6.0", "4.1.0", "5.0.0", "", "four"):
        with pytest.raises(u.UsdmError) as e:
            u.read(_doc(bad))
        assert "USDM input rejected" in str(e.value) and "4.0.x only" in str(e.value)
    with pytest.raises(u.UsdmError):
        u.read(b"not json")
    with pytest.raises(u.UsdmError):
        u.read(json.dumps([1, 2]))
    no_design = _doc()
    no_design["study"]["versions"][0]["studyDesigns"] = []
    with pytest.raises(u.UsdmError):
        u.read(json.dumps(no_design).encode())


def test_schema_check_reports_required_attributes_and_cardinality():
    doc = _doc()
    del doc["study"]["versions"][0]["studyDesigns"][0]["encounters"][1]["name"]            # Encounter.name: 1
    doc["study"]["versions"][0]["studyDesigns"][0]["activities"][0]["childIds"] = "A2"      # 0..*: must be a list
    problems = u.validate(doc)
    assert any("encounters[1].name: required (Encounter, cardinality 1)" in p for p in problems)
    assert any("activities[0].childIds: expected a list" in p for p in problems)
    assert u.validate({"usdmVersion": "4.0.0"}) == ["study: missing"]
    st, probs = u.read(doc)                                 # findings are reported, the structure is still read
    assert probs and len(st["events"]) == 4


def test_extract_events_placements_timings_arms_criteria():
    st, _ = u.read(json.dumps(_doc()).encode())
    ev = {e["oid"]: e for e in st["events"]}
    assert list(ev) == ["SE_SCREENING", "SE_DAY_1", "SE_WEEK_2", "SE_DAY_MINUS_1"]          # chain order, "-1" kept apart
    assert [e["visit_number"] for e in st["events"]] == [1, 2, 3, 4]
    assert (ev["SE_SCREENING"]["day"], ev["SE_DAY_1"]["day"], ev["SE_WEEK_2"]["day"], ev["SE_DAY_MINUS_1"]["day"]) == (-14, 0, 14, -1)
    assert (ev["SE_SCREENING"]["window_lower_days"], ev["SE_SCREENING"]["window_upper_days"]) == (-3, 0)
    assert (ev["SE_WEEK_2"]["window_lower_days"], ev["SE_WEEK_2"]["window_upper_days"]) == (-2, 1)   # 12 h rounds up to a day
    assert ev["SE_DAY_1"]["window_lower_days"] is None
    # sub-timeline (A2 runs TL2 -> A3) and child activities (A5 -> A6) are placed with their parent
    assert ev["SE_SCREENING"]["activities"] == ["A1", "A4", "A2", "A3"]
    assert ev["SE_DAY_1"]["activities"] == ["A2", "A3", "A5", "A6", "A8"]
    assert ev["SE_WEEK_2"]["arms"] == ["ACTIVE"] and ev["SE_SCREENING"]["arms"] == []       # study cells: arm applicability
    assert [a["arm_code"] for a in st["arms"]] == ["ACTIVE", "PLACEBO"] and [e["name"] for e in st["epochs"]] == ["Screening", "Treatment"]
    assert [t["name"] for t in st["conditional_timelines"]] == ["Adverse Event Timeline"]   # not a calendar visit
    assert [(x["category"], x["identifier"], x["text"]) for x in st["eligibility"]] == [
        ("INCLUSION", "1", "Age ≥ [min_age] years"), ("INCLUSION", "2", "- Signed consent"), ("EXCLUSION", "1", "Pregnant")]
    assert st["identifiers"] == ["ABC-123"] and st["title"] == "A study"
    assert u.duration_days("P1Y2M") == 425 and u.duration_days("PT36H") == 1.5 and u.duration_days("soon") is None
    sched = u.scheduling(st)
    assert [(s["event_oid"], s["anchor_event_oid"], s["offset_target_days"]) for s in sched] == [
        ("SE_SCREENING", None, 0), ("SE_DAY_1", "SE_SCREENING", 14), ("SE_WEEK_2", "SE_SCREENING", 28),
        ("SE_DAY_MINUS_1", "SE_SCREENING", 13)]


def test_activity_to_form_rule_layer_never_guesses():
    st, _ = u.read(_doc())
    res = u.resolve_forms(st, _spec(), STD, forms_catalog=[("QOL", "Quality of life diary")])
    got = {a: (r["forms"], r["basis"]) for a, r in res["resolved"].items()}
    assert got["A4"] == (["DM"], "bc_code")                       # Biomedical Concept C28421 -> DM (COSMoS)
    assert got["A9"] == (["QOL"], "forms_convention")             # customer FORMS convention
    assert got["A2"] == (["VS"], "cdisc_domain_name")             # CDISC domain name
    assert got["A1"] == (["ICF"], "form_name") and got["A6"] == (["LBHEM"], "form_name")
    assert got["A8"] == (["VS"], "cdisc_test_name")               # "Height and Weight": CT vital-sign test names
    assert got["A7"] == (["AE"], "cdisc_domain_name")             # "Adverse event review" contains the domain name
    assert set(res["unresolved"]) == {"A3", "A5"}                 # listed, not guessed
    assert "A9" in u.resolve_forms(st, _spec(), STD)["unresolved"]  # without the convention it stays unresolved


def test_seed_spec_makes_usdm_authoritative_and_is_idempotent():
    st, _ = u.read(_doc())
    spec = _spec()
    s = u.seed_spec(spec, st, STD)
    rows = spec["timepoint_csv"]["rows"]
    assert [(r["event"], r["timepoint"], r["visit_number"], r["arm"]) for r in rows] == [
        ("SE_SCREENING", "Screening", 1, "BOTH"), ("SE_DAY_1", "Day 1", 2, "BOTH"), ("SE_WEEK_2", "Week 2", 3, "ACTIVE"),
        ("SE_DAY_MINUS_1", "Day -1", 4, "BOTH"), ("SE_COMMON", "Common", 5, "BOTH")]   # pipeline-owned event kept
    v = {f["form_id"]: f["visits_assigned"] for f in spec["forms"]}
    assert v["VS"] == ["SE_SCREENING", "SE_DAY_1", "SE_WEEK_2"]         # from the schedule of activities
    assert v["DM"] == ["SE_SCREENING"] and v["ICF"] == ["SE_SCREENING"]
    assert v["LBHEM"] == ["SE_DAY_1", "SE_DAY_MINUS_1"]                 # child activity of "Safety labs"
    assert v["AE"] == ["SE_COMMON"]                                     # not scheduled in USDM: keeps its common event
    assert v["QOL"] == ["SE_SCREENING"]                                 # unresolved: own visit remapped by name, Month 6 dropped
    assert s["events_not_in_usdm_removed"] == ["SE_MONTH_6"] and s["visits_dropped"] == ["QOL: SE_MONTH_6"]
    assert s["unresolved_activities"] == ["Blood pressure supine", "Safety labs", "Quality of life diary"]
    assert s["unscheduled_unresolved_activities"] == [] and s["forms_on_conditional_timelines"] == ["AE"]
    assert s["forms_without_activity"] == ["QOL"]
    assert (s["events"], s["placements"], s["forms_placed"]) == (4, 14, 4)
    assert s["eligibility_criteria"] == {"inclusion": 2, "exclusion": 1}
    assert spec["scheduling"][1] == {"event_oid": "SE_DAY_1", "anchor_event_oid": "SE_SCREENING", "offset_target_days": 14,
                                     "window_lower_days": None, "window_upper_days": None, "repeating": False,
                                     "arm": "BOTH", "conditional_trigger": None, "source": "USDM"}
    assert spec["study_meta"]["arms"] == [{"arm_name": "Active drug", "arm_code": "ACTIVE", "description": "Active"},
                                         {"planned_enrollment": 10, "arm_name": "Placebo", "arm_code": "PLACEBO", "description": ""}]
    assert [x["item"] for x in spec["review_flags"]["usdm_review"]] == [
        "USDM activities without a form", "Forms not named by a USDM activity", "Visits that are not USDM encounters"]
    assert spec["forms"][0]["survey"] == _spec()["forms"][0]["survey"]   # form content is never touched
    once = copy.deepcopy(spec)
    u.seed_spec(spec, st, STD)
    assert spec == once


def test_context_text_is_the_authoritative_structure():
    st, _ = u.read(_doc())
    t = u.context_text(st)
    assert "AUTHORITATIVE" in t and "SE_WEEK_2 | Week 2 | Treatment | Week 2 |" in t
    assert "SE_DAY_1: Vital Signs; Blood pressure supine; Safety labs; Hematology panel; Height and Weight" in t
    assert "Adverse Event Timeline (when: Subject has an AE): Adverse event review" in t
    assert "INCLUSION 1: Age ≥ [min_age] years" in t and "ARMS: ACTIVE = Active drug; PLACEBO = Placebo" in t


def test_end_to_end_engine_builder_and_calendar_rules():
    from conventions_engine import apply_conventions
    from build_xlsforms import build_all_xlsforms, write_timepoint_csv
    from extract_calendar_rules import extract_calendar_rules
    st, _ = u.read(_doc())
    spec = _spec()
    u.seed_spec(spec, st, STD)
    log = {k: [] for k in ("forms_built", "forms_skipped", "build_errors", "build_warnings", "placeholder_applied", "oid_placeholders")}
    with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(spec, study_id="T", customer_subdomain="", client_name="")
        build_all_xlsforms(copy.deepcopy(spec), t, log)
        write_timepoint_csv(spec["timepoint_csv"], os.path.join(t, "tpt.csv"), log)
        tpt = open(os.path.join(t, "tpt.csv")).read()
        rules = extract_calendar_rules(spec, {"forms": {}})
    assert not log["build_errors"] and len(log["forms_built"]) == len(spec["forms"])
    assert "SE_SCREENING" in tpt and "SE_DAY_MINUS_1" in tpt and "SE_MONTH_6" not in tpt
    text = json.dumps(rules)
    assert "SE_WEEK_2" in text and "SE_DAY_1" in text                    # calendaring reads the USDM-seeded scheduling


def _pipeline_fns(*names):
    src = open(os.path.join(os.path.dirname(__file__), "..", "pipeline.py")).read()
    tree = ast.parse(src)
    ns = {"os": os, "json": json, "io": io}
    for name in names:
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.get_source_segment(src, node), "<pipeline>", "exec"), ns)
    return ns


def test_pipeline_layer_kill_switch_and_failure_leave_spec_unchanged(monkeypatch):
    ns = _pipeline_fns("_usdm_forms_catalog", "_apply_usdm", "_usdm_log_line")
    monkeypatch.setattr(c, "load_standards", lambda v=None: STD)
    st, _ = u.read(_doc())
    spec = _spec()
    before = copy.deepcopy(spec)
    assert ns["_apply_usdm"](spec, None) is spec                                    # no USDM file: nothing happens
    monkeypatch.setenv("USDM_INPUT", "0")
    assert ns["_apply_usdm"](spec, st) is spec and spec == before
    monkeypatch.delenv("USDM_INPUT")
    monkeypatch.setattr(u, "seed_spec", lambda *a, **k: 1 / 0)
    with contextlib.redirect_stdout(io.StringIO()):
        assert ns["_apply_usdm"](spec, st) is spec and spec == before
    monkeypatch.undo()
    monkeypatch.setattr(c, "load_standards", lambda v=None: STD)
    forms_csv = b"Form Key,Form Name,Fixed Interval\nQOL,Quality of life diary,1\n"
    with contextlib.redirect_stdout(io.StringIO()):
        out = ns["_apply_usdm"](spec, st, [("FORMS.csv", forms_csv)])
    assert spec == before and out is not spec                                       # works on a copy
    assert next(f for f in out["forms"] if f["form_id"] == "QOL")["visits_assigned"] == ["SE_WEEK_2"]
    line = ns["_usdm_log_line"](out)
    assert line.startswith("USDM structure applied: 4 events and 14 activity placements seeded; 5 forms placed")
    assert "Blood pressure supine" in line and ns["_usdm_log_line"](before) == ""
