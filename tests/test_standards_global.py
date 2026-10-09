"""Global best-fit standards matching (standards_global.py): every protocol form is scored against every standard
form and one assignment is chosen for all of them; names the protocol declares to be the same thing count as one
subject; close calls go to the validated AI proposal; two forms for one subject are never silently kept; a field is
added only to the form its protocol text is about. Synthetic forms and texts only."""
import contextlib
import copy
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol_basis as pb
import standards_global as sg
import standards_match as sm
from tests.standards import fixtures as fx


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setitem(sm._CDASH, "v", (None, {}, {}, set()))
    for k in ("STANDARDS_MATCH_GLOBAL", "STANDARDS_MATCH_MARGIN", "STANDARDS_MATCH_BY_MEANING"):
        monkeypatch.delenv(k, raising=False)


def _std(oid, title, rows, domain=None):
    survey = [{"type": t, "name": n, "label": l, "bind__oc_itemgroup": oid} for t, n, l in rows]
    m = sm._model(oid, title, survey, [], {"form_title": title, "form_id": oid}, [], sm.SRC_XLSFORM, f"{oid}.xlsx", "xlsform")
    m["domain"] = domain
    return m


def _p(fid, title, rows, domain=None, visits=("SE_V1",)):
    return fx._f(fid, title, domain, [(t, n, l, {}) for t, n, l in rows], visits=visits)


# a study drug the protocol calls by a name and by a code, and a companion tablet
TEXT = ("Patients receive florixan (XR-77) by injection. Florixan is given twice. After each florixan injection the "
        "patient takes meridol tablets for 14 days. The number of meridol tablets taken must be recorded in the eCRF. "
        "The volume of florixan injected must be recorded in the eCRF.")
P_DRUG = _p("DRUGA", "Florixan Injection", [("date", "ADAT", "Date of injection"), ("decimal", "AVOL", "Volume injected (mL)")], "EX")
P_TAB = _p("DRUGB", "Meridol Administration", [("date", "BSTDAT", "First tablet date"), ("integer", "BTAKEN", "Tablets taken")], "EX")
S_DRUG = _std("EX", "XR-77 Administration", [("select_one YN", "EXYN", "Was XR-77 injected?"), ("date", "EXDAT", "Date of injection:")], "EX")
S_TAB = _std("EC", "Companion Agent Administration", [("select_one YN", "ECYN", "Was the meridol dose taken as per protocol?"),
                                                      ("date", "ECDAT", "Dose date:")], "EC")


def _pairs(out):
    return {p["form_id"]: s["form_oid"] for p, s, *_ in out}


def test_two_forms_of_one_domain_pair_by_subject_not_by_a_shared_generic_word(monkeypatch):
    forms = lambda: [copy.deepcopy(P_DRUG), copy.deepcopy(P_TAB)]
    info = {}
    out = sm.match_forms(forms(), [S_DRUG, S_TAB], None, info, sg.aliases(TEXT))
    assert _pairs(out) == {"DRUGA": "EX", "DRUGB": "EC"}
    d = info["details"][("DRUGA", "EX")]
    assert d["decided"] == "deterministic" and d["subject"] > 0.8 and d["domain"] == 1.0 and d["total"] > 0.6
    assert info["close"] == [] and info["aliases"] == [["florixan", "XR-77"]]
    # even without the protocol's names the questions decide: the tablet form goes to the form that asks about it
    assert _pairs(sm.match_forms(forms(), [S_DRUG, S_TAB]))["DRUGB"] == "EC"
    # the kill switch restores domain-first matching, where the shared word "Administration" decides
    monkeypatch.setenv("STANDARDS_MATCH_GLOBAL", "0")
    legacy = {}
    assert _pairs(sm.match_forms(forms(), [S_DRUG, S_TAB], None, legacy))["DRUGB"] == "EX" and "details" not in legacy


def test_names_the_protocol_states_to_be_the_same_are_read_by_pattern():
    assert sg.aliases(TEXT) == [["florixan", "XR-77"]]                      # "<name> (<code>)": the verb is no part of it
    assert sg.aliases("XR-77 (florixan) is given twice.") == [["florixan", "XR-77"]]
    got = sg.aliases("Name of product:\nflorixan tosylate (also known as XR-77, AbC-tk and Florix).\nflorixan tosylate is new.")
    assert got == [["florixan tosylate", "XR-77", "AbC-tk", "Florix"]]
    got = sg.aliases("Abbreviations: QTL, quiet tidal level; XR-77/AbC-tk, florixan; ZZ, last entry runs on\ninto the text")
    assert got == [["quiet tidal level", "QTL"], ["florixan", "XR-77", "AbC-tk"]]
    assert sg.aliases("A quiet tidal level (QTL) was measured.") == [["quiet tidal level", "QTL"]]
    # two abbreviations that differ only in capitals stay apart; nothing to read gives nothing
    assert len(sg.aliases("standard of care (SoC) and system organ class (SOC)")) == 2
    assert sg.aliases("No names here (see Table 1).") == [] and sg.aliases(None) == []


def test_an_alias_makes_the_name_title_match_the_code_title():
    p = _p("DRUGA", "Florixan Administration", [("date", "ADAT", "Date given")])
    s = _std("STD1", "XR-77 Administration", [("date", "S1DAT", "Dose date:")])
    assert sm.match_forms([copy.deepcopy(p)], [s]) == []                     # nothing in common without the alias
    info = {}
    out = sm.match_forms([copy.deepcopy(p)], [s], None, info, [["florixan", "XR-77"]])
    assert _pairs(out) == {"DRUGA": "STD1"} and out[0][2].startswith("by meaning: title similarity 1.0")
    assert info["details"][("DRUGA", "STD1")]["title"] == 1.0


def test_generic_title_words_are_computed_from_the_standard_itself():
    titles = ["Alpha Worksheet", "Beta Worksheet", "Gamma Worksheet", "Delta Record", "Epsilon Record", "Zeta Panel",
              "Eta Panel", "Theta Sheet"]
    profs = [sm._m_profile(f"S{i}", t, [], []) for i, t in enumerate(titles)]
    w = sg.title_weights([], profs)
    assert w("worksheet") == 0.0                    # in more than a quarter of the titles: says nothing
    assert w("record") == 0.5 and w("theta") == 1.0 and w("unseen") == 1.0
    a, b = sm._m_profile("P", "Kappa Worksheet", [], []), profs[0]
    assert sg.weighted_title(a, b, w) == 0.0 and sm.title_meaning("Kappa Worksheet", "Alpha Worksheet") > 0


def test_the_assignment_maximises_the_total_and_uses_each_form_once():
    # greedy would take (0, 0) = 0.9 and leave row 1 with nothing; the best total is 0.8 + 0.7
    assert sg.assign({(0, 0): 0.9, (0, 1): 0.8, (1, 0): 0.7}) == {0: 1, 1: 0}
    assert sg.assign({(0, 0): 0.9, (1, 0): 0.4}) == {0: 0}                  # one standard form, used once
    assert sg.assign({}) == {}


CLOSE_P1 = _p("SCANA", "Tumour Scan Baseline", [("date", "ADAT", "Scan date")], "MI")
CLOSE_P2 = _p("SCANB", "Tumour Scan Report", [("date", "BDAT", "Scan date")], "MI")
CLOSE_S = _std("SCN", "Tumour Scan", [("date", "SCNDAT", "Date of scan:")], "MI")


def test_a_close_call_goes_to_the_validated_ai_proposal():
    forms = lambda: [copy.deepcopy(CLOSE_P1), copy.deepcopy(CLOSE_P2)]
    info = {}
    out = sm.match_forms(forms(), [CLOSE_S], None, info)
    assert _pairs(out) == {"SCANA": "SCN"} and "AMBIGUOUS" in out[0][4]      # no answer: the deterministic winner
    assert {c["form"] for c in info["close"]} == {"SCANA", "SCANB"} and info["close"][0]["decided"] == "deterministic"
    # both forms and the standard form they compete for are in the compact request
    req = sm.build_meaning_request({"forms": forms(), "study_meta": {}}, {"forms": [CLOSE_S]})
    assert req and all(x in req[1] for x in ("SCANA |", "SCANB |", "SCN |")) and "constraint" not in req[1]
    # a validated answer decides the close call
    info = {}
    out = sm.match_forms(forms(), [CLOSE_S], [("SCANB", "SCN", "the report form holds the scan result")], info)
    assert _pairs(out) == {"SCANB": "SCN"} and "close call, AI-proposed and validated" in out[0][2]
    assert info["details"][("SCANB", "SCN")]["decided"] == "AI" and info["ai_rejected"] == []
    # an answer that is no candidate of the close call is not used
    other = _std("OTH", "Concomitant Procedures", [("text", "PRTRT", "Procedure name")], "PR")
    info = {}
    out = sm.match_forms(forms(), [CLOSE_S, other], [("SCANB", "OTH", "wrong")], info)
    assert _pairs(out) == {"SCANA": "SCN"} and info["ai_rejected"][0]["standard_form"] == "OTH"


def test_a_clear_decision_is_not_a_close_call():
    info = {}
    sm.match_forms([copy.deepcopy(P_DRUG), copy.deepcopy(P_TAB)], [S_DRUG, S_TAB], None, info, sg.aliases(TEXT))
    assert info["close"] == []
    assert sm.build_meaning_request({"forms": [copy.deepcopy(P_DRUG), copy.deepcopy(P_TAB)], "study_meta": {}},
                                    {"forms": [S_DRUG, S_TAB]}, sg.aliases(TEXT)) is None


def _matched(extra=()):
    spec = {"forms": [copy.deepcopy(P_DRUG), copy.deepcopy(P_TAB)] + [copy.deepcopy(x) for x in extra], "study_meta": {}}
    with contextlib.redirect_stdout(io.StringIO()):
        return sm.apply(spec, {"forms": [S_DRUG, S_TAB], "files": [], "fingerprint": "f1"}, None, sg.aliases(TEXT))


def test_a_protocol_specified_field_follows_the_final_pairing():
    out = _matched()
    st = sm.state(out)
    assert st["method"] == "global" and {m["protocol_form"]: m["standard_form"] for m in st["matched"]} == {"DRUGA": "EX", "DRUGB": "EC"}
    assert st["matched"][0]["score_parts"]["total"] == st["matched"][0]["score"] and st["matched"][0]["decided"] == "deterministic"
    cands = {(c["form_id"], c["field"]) for c in sm.add_candidates(out)}
    assert ("EC", "BTAKEN") in cands and ("EX", "BTAKEN") not in cands       # the tablet count belongs to the tablet form
    answer = json.dumps({"additions": [
        {"form_id": "EC", "field": "BTAKEN", "covered_by": None, "quote": "The number of meridol tablets taken must be recorded in the eCRF."},
        # a sentence about the tablets never adds a field to the injection form
        {"form_id": "EX", "field": "AVOL", "covered_by": None, "quote": "The number of meridol tablets taken must be recorded in the eCRF."}]})
    with contextlib.redirect_stdout(io.StringIO()):
        res = sm.apply_additions(out, answer, TEXT)
    assert [(a["form_id"], a["field"]) for a in res["added"]] == [("EC", "BTAKEN")]
    assert res["rejected"] == [{"form_id": "EX", "field": "AVOL", "reason": "quote_names_another_forms_subject",
                                "other_form": "EC", "subject": "meridol", "quote": res["rejected"][0]["quote"]}]
    assert any("Field not added: EX.AVOL" in l for l in sm.summary_lines(out))
    # its own sentence does add it
    with contextlib.redirect_stdout(io.StringIO()):
        res = sm.apply_additions(out, json.dumps({"additions": [{"form_id": "EX", "field": "AVOL", "covered_by": None,
                                 "quote": "The volume of florixan injected must be recorded in the eCRF."}]}), TEXT)
    assert [(a["form_id"], a["field"]) for a in res["added"]] == [("EX", "AVOL")]


def test_two_forms_for_one_subject_are_never_silently_kept():
    twin = _p("DRUGC", "XR-77 Administration", [("date", "CDAT", "Date given")], "EX", visits=("SE_V9",))
    other = _p("VS", "Vital Signs", [("decimal", "SYSBP", "Systolic blood pressure")], "VS")
    out = _matched([other])
    out["forms"].append(copy.deepcopy(twin))   # a second form for the study drug, added after matching
    assert [(a["form_id"], b["form_id"], s) for a, b, s in sg.duplicates(out)] == [("EX", "DRUGC", "florixan / XR-77 administration")]
    fresh = copy.deepcopy(out)
    with contextlib.redirect_stdout(io.StringIO()):
        recs = sg.duplicate_guard(fresh, fresh=True)
    assert [f["form_id"] for f in fresh["forms"]] == ["EX", "EC", "VS"]      # the standard form stays, its twin is not built
    assert recs[0]["action"] == pb.REMOVED and recs[0]["duplicate_of"] == "EX" and "SE_V9" in recs[0]["reason"]
    assert fresh["study_meta"]["protocol_basis_removed"]["DRUGC"]["form_title"] == "XR-77 Administration"
    rows = pb.section(fresh)[3]
    assert rows[0][0] == "DRUGC" and rows[0][3] == "Not built" and "same subject as EX" in rows[0][4]
    assert any("DRUGC" in m and "not built" in m for m in fresh["review_flags"][sg.FLAG_DUPLICATE])
    assert any("Same subject, two forms: DRUGC" in l for l in sg.duplicate_lines(fresh))
    # a reused specification keeps its forms: recorded and flagged only
    reused = copy.deepcopy(out)
    with contextlib.redirect_stdout(io.StringIO()):
        recs = sg.duplicate_guard(reused, fresh=False)
    assert len(reused["forms"]) == 4 and recs[0]["action"] == pb.WOULD_REMOVE and pb.section(reused)[3][0][3].startswith("Kept")
    # two customer standard forms for one subject: both stay, the pair is reported
    both = copy.deepcopy(out)
    both["forms"][1]["form_title"] = "Florixan Administration"
    with contextlib.redirect_stdout(io.StringIO()):
        recs = sg.duplicate_guard(both, fresh=True)
    assert any(r["action"] == sg.REPORTED and "both forms are kept" in r["reason"] for r in recs)


def test_a_specification_matched_earlier_gets_the_protocol_names_for_the_guard():
    out = _matched()
    out["forms"].append(_p("DRUGC", "Florixan Administration", [("date", "CDAT", "Date given")], "EX"))
    del out["study_meta"]["standards_match"]["aliases"]
    assert sg.duplicates(out) == []                       # two names of one thing: not seen without the protocol
    sg.ensure_aliases(out, TEXT)
    assert sm.state(out)["aliases"] == [["florixan", "XR-77"]]
    assert [(a["form_id"], b["form_id"]) for a, b, _s in sg.duplicates(out)] == [("EX", "DRUGC")]
    sg.ensure_aliases(out, "other text (ZZ-1)")           # already stored: left alone
    assert sm.state(out)["aliases"] == [["florixan", "XR-77"]]


def test_the_guard_is_off_with_the_kill_switch_and_never_fails(monkeypatch):
    out = _matched()
    out["forms"].append(_p("DRUGC", "XR-77 Administration", [("date", "CDAT", "Date given")], "EX"))
    monkeypatch.setenv("STANDARDS_MATCH_GLOBAL", "0")
    assert sg.duplicate_guard(copy.deepcopy(out), fresh=True) == []
    monkeypatch.delenv("STANDARDS_MATCH_GLOBAL")
    with contextlib.redirect_stdout(io.StringIO()):
        assert len(sg.duplicate_guard(copy.deepcopy(out), fresh=True)) == 1
    assert sg.duplicate_guard({"forms": "broken", "study_meta": {"standards_match": {"x": 1}}}, fresh=True) == []
    assert sg.duplicate_guard({"forms": []}, fresh=True) == []
