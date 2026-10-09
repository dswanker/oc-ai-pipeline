"""Standards matching by meaning (standards_match.py): a protocol form the CDASH domain pass cannot pair is matched
to the standard form that collects the same thing (titles, field labels, questions). Synthetic forms only."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import standards_match as sm
from tests.standards import fixtures as fx


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setitem(sm._CDASH, "v", (None, {}, {}, set()))
    monkeypatch.delenv("STANDARDS_MATCH_BY_MEANING", raising=False)


def _std(oid, title, rows, domain=None, choices=()):
    survey = [{"type": t, "name": n, "label": l, "bind__oc_itemgroup": oid} for t, n, l in rows]
    m = sm._model(oid, title, survey, [dict(zip(("list_name", "label", "name"), c)) for c in choices],
                  {"form_title": title, "form_id": oid}, [], sm.SRC_XLSFORM, f"{oid}.xlsx", "xlsform")
    m["domain"] = domain
    return m


def _p(fid, title, rows, domain=None, visits=("SE_V1", "SE_V2")):
    return fx._f(fid, title, domain, [(t, n, l, {}) for t, n, l in rows], visits=visits)


RAD_P = _p("XR", "External Beam Radiation Therapy", [("date", "XRSTDAT", "EBRT Start Date"), ("decimal", "XRDOSE", "Total Dose (Gy)"),
                                                     ("integer", "XRFRAC", "Number of Fractions")])
RAD_S = _std("RADSTD", "Radiation (EBRT)", [("date", "PRSTDAT", "Start Date of Radiation"), ("decimal", "PRDSTXT", "Total Dose Received"),
                                             ("integer", "RADFRNUM", "Number of fractions"), ("text", "PRLOC", "Anatomical Location")], "PR")
PROC_S = _std("PROC", "Concomitant Procedures", [("text", "PRTRT", "Procedure Name"), ("date", "PRSTDAT", "Start Date"),
                                                 ("date", "PRENDAT", "End Date")], "PR")
SPEC_P = _p("SPC", "Blood Specimen Collection", [("date", "SPCDAT", "Collection Date"), ("text", "SPCCOM", "Comments")])
SPEC_S = _std("BIOLOG", "Biospecimen Collection Log", [("date", "T1DT", "Date of collection:"), ("text", "T1KIT", "Kit ID:")])
DRUG_P = _p("EXB", "Compound B Administration", [("date", "BSTDAT", "Course Start Date"), ("decimal", "BDOSEAMT", "Amount given (g)")], "EX")
DRUG_S = _std("ECSTD", "Oral Agent Administration", [("date", "ECDAT", "Dose date:"),
                                                     ("select_one YN", "ECYN", "Was the compound B dose administered as per protocol?")], "EC")
VAC_P = _p("VAC", "Vaccination History", [("date", "VACDAT", "Date of Last Vaccine"), ("text", "VACCOM", "Comments")])


def test_titles_are_compared_by_meaning_synonyms_and_abbreviations():
    assert sm.title_meaning("External Beam Radiation Therapy", "Radiation (EBRT)") == 1.0
    assert sm.title_meaning("Semen Specimen Collection", "Biospecimen Worksheet - Semen Collection") > 0.8
    assert sm.title_meaning("Radiotherapy", "Radiation") == 1.0
    # sharing only words that say nothing about the assessment is no similarity
    assert sm.title_meaning("Date of Visit", "Date of Review") == 0.0
    assert sm.title_meaning("Vaccination History", "Concomitant Procedures") == 0.0


def test_forms_without_a_shared_domain_are_paired_by_what_they_collect():
    pforms = [copy.deepcopy(x) for x in (RAD_P, SPEC_P, DRUG_P, VAC_P)]
    out = sm.match_forms(pforms, [PROC_S, RAD_S, SPEC_S, DRUG_S])
    got = {p["form_id"]: (s["form_oid"], basis) for p, s, basis, _sc, _n in out}
    assert got["XR"][0] == "RADSTD" and got["SPC"][0] == "BIOLOG" and got["EXB"][0] == "ECSTD"
    # the basis of every pair is logged
    assert got["XR"][1].startswith("by meaning: title similarity 1.0") and "field(s) ask the same" in got["XR"][1]
    assert 'the questions name "compound"' in got["EXB"][1] and "both exposure forms" in got["EXB"][1]
    # a form nothing collects stays unmatched; the procedures log is not the radiation form's content
    assert "VAC" not in got and "PROC" not in {v[0] for v in got.values()}


def test_shared_dates_and_comments_alone_never_pair_two_forms():
    ev = sm.meaning_score(*[sm._m_profile(f["form_id"], f["form_title"], [], f["survey"]) for f in (VAC_P,)],
                          sm._m_profile("PROC", PROC_S["title"], ["PR"], PROC_S["survey"]))
    assert ev["field_hits"] == 0 and sm._meaning_basis(ev)[1] is False
    other = _p("OTH", "Other Therapy", [("date", "OSTDAT", "Start Date"), ("date", "OENDAT", "End Date")])
    assert sm.match_forms([other], [PROC_S]) == []


def test_kill_switch_keeps_domain_and_name_matching_only(monkeypatch):
    monkeypatch.setenv("STANDARDS_MATCH_BY_MEANING", "0")
    assert sm.match_forms([copy.deepcopy(RAD_P)], [RAD_S]) == []
    assert sm.build_meaning_request({"forms": [copy.deepcopy(RAD_P)]}, {"forms": [RAD_S]}) is None


def test_each_standard_form_is_used_once_the_better_match_wins_and_the_other_is_flagged():
    second = _p("XR2", "Radiation Summary", [("text", "XR2NOTE", "Notes")])
    spec = {"forms": [second, copy.deepcopy(RAD_P)], "study_meta": {}}
    out = sm.apply(spec, {"forms": [RAD_S], "files": [], "fingerprint": "f1"})
    by = {f["form_id"]: f for f in out["forms"]}
    assert "RADSTD" in by and by["RADSTD"]["customer_standard"]["replaced_form_id"] == "XR"
    assert "customer_standard" not in by["XR2"] and by["XR2"]["survey"][0]["name"] == "XR2NOTE"
    st = sm.state(out)
    assert st["contested"] == [{"form": "XR2", "standard_form": "RADSTD", "score": st["contested"][0]["score"], "won_by": "XR"}]
    assert any(m.startswith("XR2: also fits the customer standard form RADSTD") for m in out["review_flags"][sm.FLAG_CONTESTED])
    assert any("REVIEW: XR2 also fits RADSTD" in l for l in sm.summary_lines(out))


def test_a_form_matched_by_meaning_is_the_standard_exactly_and_keeps_its_visits():
    spec = {"forms": [copy.deepcopy(RAD_P)], "study_meta": {}}
    out = sm.apply(spec, {"forms": [RAD_S], "files": [], "fingerprint": "f1"})
    f = out["forms"][0]
    assert [r["name"] for r in f["survey"]] == ["PRSTDAT", "PRDSTXT", "RADFRNUM", "PRLOC"]
    assert f["form_id"] == "RADSTD" and f["visits_assigned"] == ["SE_V1", "SE_V2"] and f["cdash_domain"] == "PR"
    assert f["customer_standard"]["match_basis"].startswith("by meaning")
    assert spec["forms"][0]["form_id"] == "XR"   # the input is never mutated


def test_ai_proposed_pairs_are_validated_before_use():
    weak_p = _p("SCR", "Screening Checks", [("select_one YN", "SCRELIG", "Is the patient eligible for study participation?")])
    weak_s = _std("REV", "Sponsor Review", [("date", "REVDAT", "Date of review:"),
                                            ("select_one YN", "REVELIG", "Is the patient eligible for study participation?")])
    info = {}
    pairs = [("SCR", "REV", "both record the eligibility decision"), ("VAC", "REV", "second use"),
             ("NOPE", "REV", "unknown"), ("VAC", "PROC", "nothing in common")]
    out = sm.match_forms([weak_p, copy.deepcopy(VAC_P)], [weak_s, PROC_S], pairs, info)
    assert [(p["form_id"], s["form_oid"]) for p, s, *_ in out] == [("SCR", "REV")]
    assert out[0][2].startswith("by meaning, AI-proposed and validated: 1 field(s) ask the same")
    assert {(r["protocol_form"], r["reason"]) for r in info["ai_rejected"]} == {
        ("VAC", "form already paired"), ("NOPE", "unknown form id"), ("VAC", "no title, field or question overlap")}
    # without the proposal the pair is too weak for a deterministic match
    assert sm.match_forms([copy.deepcopy(weak_p)], [weak_s]) == []
    assert sm.parse_meaning_pairs('{"pairs": [{"protocol_form": "A", "standard_form": "B", "reason": "r"}, {"x": 1}]}') == [("A", "B", "r")]
    assert sm.parse_meaning_pairs("not json") == []


def test_the_ai_request_carries_compact_lists_only():
    s = copy.deepcopy(PROC_S)
    s["survey"][1]["constraint"] = ". <= today()"
    spec = {"forms": [copy.deepcopy(VAC_P), copy.deepcopy(RAD_P)], "study_meta": {}}
    prompt, extra = sm.build_meaning_request(spec, {"forms": [s, RAD_S], "files": [], "fingerprint": "f"})
    # the pair the deterministic pass finds is not sent; the lists hold ids, titles, domains, names and labels
    assert "VAC | Vaccination History" in extra and "PROC | Concomitant Procedures | CDASH domain: PR" in extra
    assert "XR |" not in extra and "RADSTD" not in extra and "today()" not in extra
    assert "PRTRT: Procedure Name" in extra and "pairs" in prompt


def test_a_form_added_for_a_standard_form_is_spliced_without_rematching_the_rest():
    spec = {"forms": [copy.deepcopy(RAD_P)], "study_meta": {}}
    src = {"forms": [RAD_S, PROC_S], "files": [], "fingerprint": "f1"}
    out = sm.apply(spec, src)
    out["forms"].append({**_p("PROC", "Concomitant procedures", [("date", "PRDAT", "Date")], "PR", visits=("SE_COMMON",)),
                         "protocol_required": {"assessment": "Concomitant procedures", "standard_form": "PROC"}})
    new = sm.splice_added(out, src)
    assert [m["standard_form"] for m in new] == ["PROC"] and sm.splice_added(out, src) == []
    f = out["forms"][-1]
    assert [r["name"] for r in f["survey"]] == ["PRTRT", "PRSTDAT", "PRENDAT"] and f["visits_assigned"] == ["SE_COMMON"]
    st = sm.state(out)
    assert len(st["matched"]) == 2 and st["standard_forms_not_used"] == [] and st["protocol_forms_without_standard"] == []


def test_the_adverse_event_form_is_not_matched_to_a_serious_adverse_event_standard():
    ae = _p("AE", "Adverse Event", [("text", "AETERM", "Adverse event term"), ("select_one YN", "AESER", "Serious?")])
    sae = _std("SAESTD", "Serious Adverse Event Report", [("date", "SAEAWDAT", "Date site became aware"), ("text", "SAENARR", "Narrative")])
    assert sm.match_forms([ae], [sae]) == [] and sm.match_forms([ae], [sae], [("AE", "SAESTD", "same events")]) == []
    own = _p("AESAE", "Serious Adverse Events", [("text", "SAEDESC", "Narrative Description")])
    assert [(p["form_id"], s["form_oid"]) for p, s, *_ in sm.match_forms([ae, own], [sae])] == [("AESAE", "SAESTD")]
