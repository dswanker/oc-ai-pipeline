"""Forms are protocol-driven (protocol_forms.py): every assessment the protocol requires maps to a form; a missing one
is added through customer standard -> CRF standards -> CDASHIG. Synthetic protocol, spec and standards only."""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol_forms as pf
import standards_match as sm
from tests.standards import fixtures as fx

PROTOCOL = (
    "Table 1 Schedule of Activities. Clinical assessments: history and physical before each dose.\n"
    "10.2 Demographics will be obtained from the patient and recorded on the eCRF.\n"
    "10.4 Previous and Concomitant Medications and Procedures. Any changes in concomitant medications or\n"
    "procedures will be reassessed as needed and recorded.\n"
    "10.5.1 Vital signs will be measured at every visit.\n"
    "10.5.2 A physical examination (PE) will be performed at the timepoints specified in the SoA.\n"
    "7.3 Radiation therapy will be given per standard of care and recorded.\n"
    "8.1 Adverse events will be recorded from consent to the end of the study.\n"
    "6.1 Inclusion: performance status must be ECOG 0-2.\n")

PR_SURVEY = [
    ["text", "PRTRT", "What procedure was performed?", "PR", "", "", "", "yes", "", "", "", "", "", ""],
    ["date", "PRSTDAT", "Procedure date", "PR", "", "", "", "yes", ". <= today()", "Not in the future", "", "", "", ""],
]
PR_SETTINGS = {"form_title": "Concomitant Procedures", "form_id": "PR", "version": "3", "style": "theme-grid"}

CDASH = {("PE", "PEDAT"): {"domain": "PE", "variable": "PEDAT", "label": "Physical Examination Date",
                           "question": "What was the date of the examination?", "core": "HR", "type": "Char"},
         ("PE", "PEPERF"): {"domain": "PE", "variable": "PEPERF", "label": "Physical Examination Performed",
                            "question": "Was the physical examination performed?", "core": "HR", "type": "Char"},
         ("PE", "PEDESC"): {"domain": "PE", "variable": "PEDESC", "label": "Abnormal Findings",
                            "question": "Describe [abnormal] findings", "core": "R/C", "type": "Char"},
         ("PE", "PEEVAL"): {"domain": "PE", "variable": "PEEVAL", "label": "Evaluator", "question": "", "core": "O",
                            "type": "Char"},
         ("PE", "STUDYID"): {"domain": "PE", "variable": "STUDYID", "label": "Study", "question": "", "core": "HR",
                             "type": "Char"},
         ("PR", "PRTRT"): {"domain": "PR", "variable": "PRTRT", "label": "Procedure Name", "question": "", "core": "HR",
                           "type": "Char"},
         ("VS", "VSDAT"): {"domain": "VS", "variable": "VSDAT", "label": "Date", "question": "", "core": "HR", "type": "Char"},
         ("AE", "AETERM"): {"domain": "AE", "variable": "AETERM", "label": "Term", "question": "", "core": "HR", "type": "Char"},
         ("DM", "BRTHDAT"): {"domain": "DM", "variable": "BRTHDAT", "label": "Birth", "question": "", "core": "HR", "type": "Char"}}


@pytest.fixture(autouse=True)
def synthetic_cdash(monkeypatch):
    by_var = {}
    for (_d, v), rec in CDASH.items():
        by_var.setdefault(v, []).append(rec)
    monkeypatch.setitem(sm._CDASH, "v", (None, CDASH, by_var, {d for d, _ in CDASH}))
    monkeypatch.delenv("PROTOCOL_FORMS_CHECK", raising=False)


def _spec():
    s = fx.spec()
    s["forms"].append(fx._f("DM", "Demographics", "DM", [("date", "BRTHDAT", "Date of birth", {})]))
    s["forms"].append(fx._f("RT", "External Beam Radiation Therapy", "PR",
                            [("date", "RTDAT", "Radiation date", {}), ("decimal", "RTDOSE", "Dose", {})]))
    return s


def _answer(items):
    return json.dumps({"assessments": items})


ITEMS = [
    {"name": "Demographics", "cdash_domain": "DM", "section": "10.2", "log": False, "events": ["SE_SCREENING"],
     "quote": "Demographics will be obtained from the patient and recorded on the eCRF."},
    {"name": "Concomitant medications", "cdash_domain": "CM", "section": "10.4", "log": True,
     "events": [], "quote": "Any changes in concomitant medications or procedures will be reassessed as needed"},
    {"name": "Concomitant procedures", "cdash_domain": "PR", "section": "10.4", "log": True,
     "events": [], "quote": "Any changes in concomitant medications or procedures will be reassessed as needed"},
    {"name": "Vital signs", "cdash_domain": "VS", "section": "10.5.1", "log": False, "events": ["SE_SCREENING"],
     "quote": "Vital signs will be measured at every visit."},
    {"name": "Physical examination", "cdash_domain": "PE", "section": "10.5.2", "log": False,
     "events": ["SE_SCREENING", "SE_NOT_AN_EVENT"],
     "quote": "A physical examination (PE) will be performed at the\ntimepoints specified in the SoA."},
    {"name": "Radiation therapy", "cdash_domain": "PR", "section": "7.3", "log": False, "events": ["SE_SCREENING"],
     "quote": "Radiation therapy will be given per standard of care"},
    {"name": "Adverse events", "cdash_domain": "AE", "section": "8.1", "log": True, "events": [],
     "quote": "Adverse events will be recorded from consent"},
    {"name": "Tumour biopsy", "cdash_domain": "PR", "section": "9.9", "log": False, "events": [],
     "quote": "A tumour biopsy will be taken at every visit."},
]


def _sources():
    return sm.load_sources([("PR.xlsx", fx.xlsform_bytes(PR_SURVEY, [], PR_SETTINGS)),
                            ("AEGEN.xlsx", fx.xlsform_bytes())])


def _run(spec=None, sources=None, crf=None, items=ITEMS):
    spec = spec or _spec()
    v = pf.validate_response(spec, _answer(items), PROTOCOL)
    st = pf.apply(spec, v["assessments"], sources, crf, PROTOCOL, v["rejected"])
    return spec, st, v


def test_only_assessments_with_a_verified_verbatim_quote_survive():
    v = pf.validate_response(_spec(), _answer(ITEMS), PROTOCOL)
    names = [a["name"] for a in v["assessments"]]
    assert "Tumour biopsy" not in names and v["rejected"] == {"quote not found in the protocol": 1}
    # the quote is found across a PDF line break; an event that does not exist is dropped
    pe = next(a for a in v["assessments"] if a["name"] == "Physical examination")
    assert pe["events"] == ["SE_SCREENING"] and pe["domain"] == "PE"
    assert pf.validate_response(_spec(), "not json", PROTOCOL)["assessments"] == []


def test_each_listed_assessment_keeps_its_domain_and_log_flag():
    v = pf.validate_response(_spec(), _answer(ITEMS), PROTOCOL)
    by = {a["name"]: a for a in v["assessments"]}
    assert by["Concomitant medications"]["domain"] == "CM" and by["Concomitant procedures"]["domain"] == "PR"
    assert by["Concomitant procedures"]["log"] is True
    assert pf.assessment_domain("Something new", "ZZ", {"PE"}) == (None, "no CDASH domain")
    assert pf.assessment_domain("Something new", "pe", {"PE"}) == ("PE", "AI-proposed domain")


def test_missing_forms_are_added_and_logged_with_the_protocol_section():
    spec, st, _ = _run(sources=_sources())
    ids = [f["form_id"] for f in spec["forms"]]
    assert st["added"] == ["CM", "PR", "PE"] and ids[-3:] == ["CM", "PR", "PE"]
    by = {r["assessment"]: r for r in st["assessments"]}
    assert by["Demographics"]["form"] == "DM" and by["Vital signs"]["form"] == "VS" and by["Adverse events"]["form"] == "AE"
    # a specific treatment is collected on the form that names it, not on a new one
    assert by["Radiation therapy"]["form"] == "RT" and by["Radiation therapy"]["added"] is False
    flags = spec["review_flags"][pf.FLAG]
    assert any("PE (Physical examination)" in m and "section 10.5.2" in m and "CDASHIG" in m for m in flags)
    assert any(m.startswith("PR (Concomitant Procedures)") and "section 10.4" in m and "OC4 standard" in m for m in flags)


def test_concomitant_procedures_is_not_covered_by_a_radiation_form_of_the_same_domain():
    spec = _spec()
    a = {"name": "Concomitant procedures", "domain": "PR", "quote": "x", "log": True}
    assert pf.cover(a, spec["forms"]) == (None, "")
    spec["forms"].append(fx._f("CP", "Concomitant Procedures", "PR", [("text", "PRTRT", "Procedure", {})]))
    assert pf.cover(a, spec["forms"])[0]["form_id"] == "CP"


def test_cdashig_form_has_the_recommended_variables_and_the_protocol_visits():
    spec, _st, _ = _run()
    pe = next(f for f in spec["forms"] if f["form_id"] == "PE")
    assert [r["name"] for r in pe["survey"]] == ["PEDAT", "PEPERF", "PEDESC"]  # HR and R/C; no header, no optional
    assert [r["type"] for r in pe["survey"]] == ["date", "select_one NY", "text"]
    assert pe["survey"][2]["label"] == "Abnormal Findings"  # a question with placeholders falls back to the label
    assert {c["list_name"] for c in pe["choices"]} == {"NY"}
    assert pe["visits_assigned"] == ["SE_SCREENING"] and pe["cdash_domain"] == "PE"
    assert pe["protocol_required"]["section"] == "10.5.2" and pe["protocol_required"]["content_source"] == pf.SRC_CDASH
    assert {"target_visit_oid": "SE_SCREENING", "form_id": "PE", "required": True, "repeating": False,
            "notes": "Required by protocol 10.5.2"} in spec["schedule_of_events"]["form_placements"]
    # a log goes to the common event
    cm = next(f for f in spec["forms"] if f["form_id"] == "CM")
    assert cm["visits_assigned"] == ["SE_COMMON"] and cm["has_repeating_group"] is True


def test_a_matching_standard_form_is_used_and_never_left_unused():
    src = _sources()
    spec, _st, _ = _run(sources=src)
    pr = next(f for f in spec["forms"] if f["form_id"] == "PR")
    assert pr["form_title"] == "Concomitant Procedures" and pr["visits_assigned"] == ["SE_COMMON"]
    out = sm.apply(spec, src)
    pf.refresh_sources(out)
    pr = next(f for f in out["forms"] if f["form_id"] == "PR")
    assert pr["customer_standard"]["form_oid"] == "PR"
    assert [r["name"] for r in pr["survey"]] == ["PRTRT", "PRSTDAT"]  # the standard's content, exactly
    assert "PR" not in [s["form"] for s in sm.state(out)["standard_forms_not_used"]]
    rt = next(f for f in out["forms"] if f["form_id"] == "RT")
    assert not rt.get("customer_standard")  # the radiation form keeps its own content
    src_by = {f["form_id"]: f for f in pf.form_sources(out)}
    assert src_by["PR"]["source"].startswith("OC4 standard (uploaded XLSForm)")
    assert src_by["PR"]["required_by"][0]["section"] == "10.4" and src_by["PE"]["source"] == pf.SRC_CDASH


def test_precedence_crf_standards_then_placeholder_when_there_is_no_domain():
    spec = _spec()
    a = {"name": "Physical examination", "domain": "PE", "section": "10.5.2", "quote": "q", "events": [], "log": False}
    form, source, placement = pf.build_form(a, spec, None, {"PE": [{"variable_name": "PEDAT"}]})
    assert (form["form_id"], source) == ("PE", pf.SRC_CRF) and "first event" in placement
    b = {"name": "Randomisation call", "domain": None, "section": "5", "quote": "q", "events": ["SE_SCREENING"], "log": False}
    form, source, _ = pf.build_form(b, spec, None, None)
    assert source == pf.SRC_PLACEHOLDER and form["form_id"] == "RC"
    assert [r["type"] for r in form["survey"]] == ["select_one NY", "date"] and form["survey"][0]["completion_status"] == "FLAGGED"


def test_standards_never_add_or_remove_forms_on_their_own():
    src = _sources()
    spec = _spec()
    before = [f["form_id"] for f in spec["forms"]]
    items = [i for i in ITEMS if i["name"] in ("Demographics", "Vital signs", "Adverse events")]
    # a protocol that does not ask for concomitant procedures
    text = "\n".join(l for l in PROTOCOL.split("\n") if "rocedures" not in l)
    v = pf.validate_response(spec, _answer(items), text)
    st = pf.apply(spec, v["assessments"], src, None, text, v["rejected"])
    assert st["added"] == [] and [f["form_id"] for f in spec["forms"]] == before
    out = sm.apply(spec, src)
    # not required by the protocol: not added, and not forced onto the radiation form that shares its domain
    assert "PR" in [s["form"] for s in sm.state(out)["standard_forms_not_used"]]
    assert not next(f for f in out["forms"] if f["form_id"] == "RT").get("customer_standard")
    assert len(out["forms"]) == len(before)


def test_an_assessment_named_only_in_eligibility_text_is_covered_by_the_field_that_names_it():
    spec = _spec()
    spec["forms"].append(fx._f("IE", "Eligibility", "IE", [("select_one YN", "IEECOG", "ECOG performance status 0-2", {})]))
    a = {"name": "ECOG performance status", "domain": "RS", "quote": "q", "log": False}
    f, basis = pf.cover(a, spec["forms"])
    assert f["form_id"] == "IE" and basis == "named on a field or choice"


def test_runs_once_per_protocol_and_sources_and_kill_switch(monkeypatch):
    spec, _st, _ = _run(sources=_sources())
    assert pf.needs_check(spec, PROTOCOL, _sources()) is False
    assert pf.needs_check(spec, PROTOCOL + " amended", _sources()) is True
    assert pf.needs_check(spec, PROTOCOL, None) is True
    assert pf.needs_check(_spec(), "", None) is False  # no protocol text: quotes cannot be verified
    assert pf.build_request(_spec(), "") is None
    prompt, extra = pf.build_request(_spec(), PROTOCOL)
    assert "SE_SCREENING | Screening" in extra and "PROTOCOL TEXT" in extra and "AEGEN" not in prompt + extra
    assert "PROTOCOL TEXT" not in pf.build_request(_spec(), PROTOCOL, with_text=False)[1]
    monkeypatch.setenv("PROTOCOL_FORMS_CHECK", "0")
    assert pf.needs_check(_spec(), PROTOCOL, None) is False
    # a second run on the same spec adds nothing more
    again = copy.deepcopy(spec)
    v = pf.validate_response(again, _answer(ITEMS), PROTOCOL)
    assert pf.apply(again, v["assessments"], _sources(), None, PROTOCOL)["added"] == []


def test_the_analysis_context_no_longer_contains_the_standards_catalog():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py")).read()
    assert "CUSTOMER STANDARD FORMS — catalog" not in src and "catalog_text(_std_sources)" not in src
    # the completeness check runs after standards matching, on the final forms, at every place a spec is prepared
    assert src.count("await _protocol_forms_step(") == 1
    lines = src.split("\n")
    posts = [i for i, l in enumerate(lines) if "struct_json = await _post_match_forms_step(" in l]
    assert len(posts) == 4 and all("_standards_match_step(" in lines[i - 1] for i in posts)


def test_summary_lines_for_the_monday_log():
    spec, _st, _ = _run(sources=_sources())
    lines = pf.summary_lines(spec)
    assert lines[0].startswith("Protocol-required forms: 7 assessment(s)") and "3 form(s) added" in lines[0]
    assert any(l.startswith("  + PE:") and "protocol 10.5.2" in l for l in lines)


# ── Coverage by meaning; nothing is added twice ──────────────────────────────────

def _a(name, domain, quote="x", log=False, section="9.1"):
    return {"name": name, "domain": domain, "quote": quote, "log": log, "section": section, "events": []}


def test_a_form_titled_for_the_assessment_covers_it_whatever_its_domain():
    spec = _spec()
    spec["forms"].append(fx._f("RADSTD", "Radiation (EBRT)", None, [("date", "PRSTDAT", "Start Date of Radiation", {})]))
    spec["forms"] = [f for f in spec["forms"] if f["form_id"] != "RT"]
    f, basis = pf.cover(_a("External beam radiation therapy", "PR"), spec["forms"])
    assert f["form_id"] == "RADSTD" and basis == "form title (by meaning)"
    # a form whose title names the assessment covers it although the model gave another domain
    spec["forms"].append(fx._f("DC", "Disease Characteristics", "MI", [("date", "MIDAT", "Date of Biopsy", {})]))
    f, basis = pf.cover(_a("Disease assessment", "RS"), spec["forms"])
    assert f["form_id"] == "DC" and basis == "named in the form title"
    # the concomitant log of another domain does not cover it
    assert pf.cover(_a("Concomitant procedures", "PR", log=True), spec["forms"]) == (None, "")


def test_compliance_is_covered_by_the_administration_form_with_dosing_fields():
    spec = _spec()
    spec["forms"] += [
        fx._f("EXA", "Compound A Administration", "EX", [("date", "EXSTDAT", "Injection date", {}), ("decimal", "EXDOSE", "Dose", {})]),
        fx._f("ECB", "Prodrug Administration", "EC", [("date", "ECDAT", "Dose date", {}),
                                                      ("select_one NY", "ECSTAT", "Did the participant complete the regimen?", {})]),
        fx._f("DIARY", "Diary Administration", None, [("date", "DIDAT", "Date handed out", {})])]
    a = _a("Treatment compliance", "EC", quote="Injection information along with prodrug compliance will be documented in the eCRF.")
    f, basis = pf.cover(a, spec["forms"])
    assert f["form_id"] == "ECB" and basis == "dosing / compliance fields on the administration form"
    st = pf.apply(spec, [a], None, None, PROTOCOL)
    assert st["added"] == [] and st["assessments"][0]["skipped_addition"] is True
    assert any(l.startswith('  = "Treatment compliance": no form added, covered by ECB') for l in pf.summary_lines(spec))
    # without any administration form that has dosing fields, the form is added
    bare = _spec()
    assert pf.apply(bare, [a], None, None, PROTOCOL)["added"] != []


def test_no_form_is_added_when_its_content_would_be_a_standard_form_already_in_use():
    spec = _spec()
    src = _sources()
    # the radiation form took the customer's Concomitant Procedures standard (the 2026-10-08 fresh-run defect)
    next(f for f in spec["forms"] if f["form_id"] == "RT")["customer_standard"] = {"form_oid": "PR", "source": "uploaded XLSForm"}
    st = pf.apply(spec, [_a("Concomitant procedures", "PR", log=True)], src, None, PROTOCOL)
    r = st["assessments"][0]
    assert st["added"] == [] and r["form"] == "RT" and "which RT already uses" in r["basis"] and r["skipped_addition"]
    assert [f["form_id"] for f in spec["forms"]].count("PR") == 0


DEATH_PLAIN = _a("Death", "DD", quote="The applicable eCRF page(s) pertaining to death should be completed.", log=True)
DEATH_DETAIL = _a("Death", "DD", quote="The cause of death and whether an autopsy was performed will be recorded.", log=True)


def _ds_spec():
    s = _spec()
    s["forms"].append(fx._f("DS", "Disposition", "DS", [("date", "DSSTDAT", "Date", {})]))
    return s


def test_death_details_form_only_when_the_protocol_asks_for_details():
    spec = _ds_spec()
    st = pf.apply(spec, [DEATH_PLAIN], None, None, PROTOCOL)
    r = st["assessments"][0]
    assert st["added"] == [] and r["form"] == "DS" and "does not ask for death details" in r["basis"]
    spec = _ds_spec()
    st = pf.apply(spec, [DEATH_DETAIL], None, None, PROTOCOL)
    assert len(st["added"]) == 1 and spec["forms"][-1]["cdash_domain"] == "DD"


def test_death_details_answer_always_and_never():
    spec = _ds_spec()
    st = pf.apply(spec, [DEATH_DETAIL], None, None, PROTOCOL, death=pf.DEATH_NEVER)
    assert st["added"] == [] and "DEATH_DETAILS_FORM = Never" in st["assessments"][0]["basis"]
    # Always: a form is added even when the protocol lists no death assessment, once
    spec = _ds_spec()
    st = pf.apply(spec, [], None, None, PROTOCOL, death=pf.DEATH_ALWAYS)
    assert len(st["added"]) == 1 and st["assessments"][-1]["convention"] == "DEATH_DETAILS_FORM = Always"
    assert any("the customer always collects death details" in m for m in spec["review_flags"][pf.FLAG])
    again = pf.apply(spec, [DEATH_PLAIN], None, None, PROTOCOL, death=pf.DEATH_ALWAYS)
    assert again["added"] == []
    # the answer is part of what the check ran for: a new answer runs it again, the default leaves old runs valid
    assert pf.fingerprint(PROTOCOL, None) == pf.fingerprint(PROTOCOL, None, pf.DEATH_PROTOCOL) != pf.fingerprint(PROTOCOL, None, pf.DEATH_ALWAYS)
    assert pf.needs_check(spec, PROTOCOL, None, pf.DEATH_ALWAYS) is False and pf.needs_check(spec, PROTOCOL, None) is True


def test_no_assessment_is_invented_from_protocol_phrases():
    """Only the model's verified list counts: no hard-coded phrase adds or splits an assessment."""
    items = [i for i in ITEMS if i["name"] != "Concomitant procedures"]
    names = [a["name"] for a in pf.validate_response(_spec(), _answer(items), PROTOCOL)["assessments"]]
    assert "Concomitant procedures" not in names
    combined = [dict(ITEMS[0], name="Medical history and physical examination")]
    names = [a["name"] for a in pf.validate_response(_spec(), _answer(combined), PROTOCOL)["assessments"]]
    assert names == ["Medical history and physical examination"]
    assert not hasattr(pf, "supplement") and not hasattr(pf, "_split_combined")


def test_one_of_two_administration_forms_is_chosen_by_the_subject_its_questions_name():
    spec = _spec()
    spec["forms"] += [
        fx._f("EX", "Alphavir Administration", "EX", [("date", "EXSTDAT", "Injection date", {})]),
        fx._f("EC", "Prodrug Administration", "EC", [("select_one NY", "ECYN", "Was the betacillin dose administered as per protocol?", {})])]
    f, basis = pf.cover(_a("Betacillin administration", "EX"), spec["forms"])
    assert f["form_id"] == "EC" and basis == "CDASH domain EX, named on the form"


# ── Every "record" instruction of an entry is an assessment ──────────────────────

TWO = ("7.4 Prior treatments and other items. At baseline, prior treatments will be recorded in the eCRF. The date of "
       "the last gadget inspection will be recorded in the eCRF if it took place within 6 months.")


def test_a_heading_whose_text_asks_for_two_unrelated_data_items_yields_two_assessments():
    checklist = {"entries": [{"id": "H1", "type": "heading", "number": "7.4", "label": "Prior treatments and other items"}]}
    answer = json.dumps({"entries": [{"id": "H1", "assessments": [
        {"name": "Prior treatments", "cdash_domain": "CM", "section": "7.4", "kind": "record", "log": True,
         "quote": "At baseline, prior treatments will be recorded in the eCRF."},
        {"name": "Gadget inspection history", "cdash_domain": "CM", "section": "7.4", "kind": "record",
         "quote": "The date of the last gadget inspection will be recorded in the eCRF if it took place within 6 months."}]}],
        "extra": []})
    v = pf.validate_response(_spec(), answer, TWO, checklist)
    assert [a["name"] for a in v["assessments"]] == ["Prior treatments", "Gadget inspection history"]
    assert v["assessments"][0]["quote"] != v["assessments"][1]["quote"] and v["coverage"]["H1"]["assessments"] == [
        "Prior treatments", "Gadget inspection history"]
    # the instruction asks for exactly this, for every entry, and the request carries it
    p, _extra = pf.build_request(_spec(), TWO, checklist=checklist)
    assert "EVERY distinct data item" in p and "ONE ASSESSMENT PER DATA ITEM" in p and "its own quote" in p


def test_a_data_item_no_form_names_gets_a_form_and_is_not_parked_on_a_form_of_the_models_domain():
    def log_form(*extra):
        return fx._f("VS", "Vital Signs", "VS", [("date", "VSDAT", "Date", {}), ("text", "VSHX", "Relevant history", {})] + list(extra))
    spec = _spec()
    spec["forms"].append(log_form())
    v = pf.validate_response(spec, json.dumps({"entries": [], "extra": [
        {"name": "Gadget inspection history", "cdash_domain": "VS", "section": "7.4", "kind": "record",
         "quote": "The date of the last gadget inspection will be recorded in the eCRF if it took place within 6 months."}]}), TWO)
    a = v["assessments"][0]
    assert a["domain"] == "VS" and a["domain_basis"] == "AI-proposed domain"
    assert pf.cover(a, spec["forms"]) == (None, "")          # "history" on that form does not make it collect this
    st = pf.apply(spec, v["assessments"], None, None, TWO, v["rejected"])
    assert len(st["added"]) == 1
    added = next(f for f in spec["forms"] if f["form_id"] == st["added"][0])
    assert added["protocol_required"]["quote"].startswith("The date of the last gadget inspection")
    # once a form names it (its own form, or a field added to another form), that form collects it
    spec2 = _spec()
    spec2["forms"].append(log_form(("date", "GADDAT", "Date of last gadget inspection", {})))
    assert pf.cover(a, spec2["forms"])[0]["form_id"] == "VS"


def test_all_items_kill_switch_restores_the_instruction_and_the_mapping(monkeypatch):
    assert pf.prompt() == pf.PROMPT_ALL != pf.PROMPT
    monkeypatch.setenv("PROTOCOL_FORMS_ALL_ITEMS", "0")
    assert pf.prompt() == pf.PROMPT
    spec = _spec()
    spec["forms"].append(fx._f("VS", "Vital Signs", "VS", [("date", "VSDAT", "Date", {})]))
    a = {"name": "Gadget inspection history", "domain": "VS", "domain_basis": "AI-proposed domain", "quote": "x", "log": False}
    assert pf.cover(a, spec["forms"])[0]["form_id"] == "VS"


def test_kind_names_switch_adds_one_generic_naming_rule_and_is_off_by_default(monkeypatch):
    assert not pf.kind_names_enabled() and pf.prompt() == pf.PROMPT_ALL
    monkeypatch.setenv("PROTOCOL_FORMS_KIND_NAMES", "1")
    p = pf.prompt()
    assert p != pf.PROMPT_ALL and p.replace(pf._RULE_KIND, "") == pf.PROMPT_ALL
    assert "by the KIND of data it collects" in p and "never by the wording of the sentence" in p
    assert p.index("13. NAME each assessment") < p.index("Return ONLY JSON:")
    assert pf.build_request(_spec(), TWO)[0] == p
    # the rule names no study content, and quote verification is what it was
    v = pf.validate_response(_spec(), json.dumps({"entries": [], "extra": [
        {"name": "Prior treatments", "kind": "record", "quote": "This sentence is not in the protocol at all."}]}), TWO)
    assert v["assessments"] == [] and v["rejected"] == {"quote not found in the protocol": 1}
    monkeypatch.setenv("PROTOCOL_FORMS_ALL_ITEMS", "0")
    assert pf.prompt().replace(pf._RULE_KIND, "") == pf.PROMPT
