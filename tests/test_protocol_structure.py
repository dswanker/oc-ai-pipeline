"""The protocol's own structure as a checklist (protocol_structure.py) and the completeness check that must account
for every entry of it (protocol_forms.py). Synthetic protocols only."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol_basis as pb
import protocol_forms as pf
import protocol_structure as ps
import standards_match as sm
from tests.standards import fixtures as fx

TOC = """TABLE OF CONTENTS
1. INTRODUCTION ........................................ 3
1.1. Background ........................................ 3
2. STUDY POPULATION .................................... 5
2.1. Inclusion Criteria ................................ 5
3. STUDY TREATMENT ..................................... 7
3.1. Gadget Therapy .................................... 7
3.2. Dose Rules ........................................ 8
4. STUDY PROCEDURES AND ASSESSMENTS ................... 10
4.1. Widget Reading and Gadget Count ................... 10
4.2. Definition of a Widget
Event ................................................. 11
4.3. Sprocket Review ................................... 12
4.3.1. Sprocket Photographs ............................ 12
5. STATISTICS .......................................... 14
5.1. Sample Size ....................................... 14
"""
BODY = """2.1 Inclusion Criteria
Sprocket score must be between 0 and 2.
3.1 Gadget Therapy
Gadget therapy is given per local practice.
4.1 Widget Reading and Gadget Count
The widget reading will be recorded in the eCRF at every visit. The gadget count will be recorded in the eCRF at
screening.
4.2 Definition of a Widget Event
A widget event is any unplanned widget reading.
4.3 Sprocket Review
4.3.1 Sprocket Photographs
Photographs are stored by the sponsor.
"""
PROTOCOL = TOC + BODY
PAGE = "\n".join([
    "Table 1:     Schedule of Activities (main study)",
    "",
    "                                   Screening        Week 1         Week 2          Week 4",
    "                                                    Day 1",
    " Widget reading1                       X               X              X               X",
    "",
    " Gadget therapy and",
    " gadget count                          X2                             X",
    " Sprocket review                                       X",
    " (if available)"])
NEXT_PAGE = "\n".join([
    " Cog diary                             X               X",
    "",
    "Abbreviations: a long line of prose that explains every abbreviation used in the table above, one after the other, at length.",
    "      1.  The widget reading is taken before any other procedure and never later than the visit window allows.",
    " Not a row                             X"])
Q_WIDGET = "The widget reading will be recorded in the eCRF at every visit."
Q_GADGET = "The gadget count will be recorded in the eCRF at screening."
Q_ELIG = "Sprocket score must be between 0 and 2."


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setitem(sm._CDASH, "v", (None, {}, {}, set()))
    for k in ("PROTOCOL_FORMS_CHECK", "PROTOCOL_FORMS_CHECKLIST", "PROTOCOL_BASIS_CHECK"):
        monkeypatch.delenv(k, raising=False)


def _checklist():
    rows = ps.schedule_rows([PAGE, NEXT_PAGE])
    heads, how = ps.procedure_headings(ps.headings(PROTOCOL), rows)
    entries = [{"id": f"R{i}", "type": "row", "label": r["label"], "table": r["table"]} for i, r in enumerate(rows, 1)]
    entries += [{"id": f"H{i}", "type": "heading", "label": h["title"], "number": h["number"]} for i, h in enumerate(heads, 1)]
    return {"entries": entries, "rows": rows, "headings": heads, "how": how, "all_headings": len(ps.headings(PROTOCOL)),
            "schedule_text": PAGE + "\n" + NEXT_PAGE}


def _spec():
    return {"study_meta": {}, "review_flags": {}, "schedule_of_events": {"form_placements": []},
            "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening"}]},
            "forms": [fx._f("WID", "Widget Reading", None, [("decimal", "WIDVAL", "Widget reading", {})])]}


def _a(name, quote, kind="record", section="4.1"):
    return {"name": name, "cdash_domain": None, "section": section, "quote": quote, "kind": kind, "events": [], "log": False}


def test_schedule_rows_are_the_first_column_labels_of_every_schedule_table_across_pages():
    rows = ps.schedule_rows([PAGE, NEXT_PAGE])
    assert [r["label"] for r in rows] == ["Widget reading1", "Gadget therapy and gadget count",
                                          "Sprocket review (if available)", "Cog diary"]
    assert {r["table"] for r in rows} == {"Table 1: Schedule of Activities (main study)"}
    # prose that only mentions the table, and a contents line with leader dots, start no table
    assert ps.schedule_rows(["The timing is given in the Schedule of Activities and its footnotes below.\n Row    X",
                             "Table 1: Schedule of Activities ......... 9\n Row    X"]) == []
    # running headers printed on every page are not rows
    pages = [f"Sponsor name              Confidential\nProtocol X                page {i}\n{PAGE if i == 2 else 'text'}" for i in range(6)]
    assert "Sponsor name" not in [r["label"] for r in ps.schedule_rows(pages)]


def test_headings_come_from_the_table_of_contents_with_exact_numbers_and_titles():
    heads = ps.headings(PROTOCOL)
    assert [h["number"] for h in heads] == ["1", "1.1", "2", "2.1", "3", "3.1", "3.2", "4", "4.1", "4.2", "4.3", "4.3.1", "5", "5.1"]
    assert {h["number"]: h["title"] for h in heads}["4.2"] == "Definition of a Widget Event"
    # without a table of contents: numbered lines count only when they continue the numbering
    body = ("1 Introduction\n1.1 Background\nIn 2019 a study showed\n1. a list item that ends with a full stop.\n"
            "1.2 Rationale\n4.7 Not a heading here\n2 Procedures\n2.1 Widget Reading\n")
    assert [h["number"] for h in ps.headings(body)] == ["1", "1.1", "1.2", "2", "2.1"]


def test_the_checklist_is_every_row_plus_the_procedures_chapter_and_headings_named_like_a_row():
    cl = _checklist()
    assert [e["label"] for e in cl["entries"] if e["type"] == "row"] == [r["label"] for r in cl["rows"]]
    # chapter 4 by its generic title; 3.1 because a schedule row names it; nothing from background, population, statistics
    assert [h["number"] for h in cl["headings"]] == ["3.1", "4.1", "4.2", "4.3", "4.3.1"]
    assert "chapter 4 (chapter title)" in cl["how"]
    lines = ps.log_lines(cl)
    assert lines[0].startswith("Protocol structure: 4 Schedule of Activities row(s), 5 procedures heading(s) of 14")
    assert "  row: Cog diary [Table 1: Schedule of Activities (main study)]" in lines and "  heading: 4.3 Sprocket Review" in lines
    # no procedures chapter by title: every section heading is on the checklist
    heads, how = ps.procedure_headings([{"number": "1", "title": "Methods", "level": 1}, {"number": "1.1", "title": "Readings", "level": 2},
                                        {"number": "2", "title": "Ethics", "level": 1}], [])
    assert [h["number"] for h in heads] == ["1.1", "2"] and how.startswith("no procedures chapter recognised")
    assert ps.checklist("", None)["entries"] == []
    src = open(ps.__file__).read() + open(pf.__file__).read()
    assert "supplement" not in src and "_NAMED_IN_TEXT" not in src


def test_the_request_carries_the_checklist_and_a_follow_up_only_the_missing_entries():
    cl = _checklist()
    prompt, extra = pf.build_request(_spec(), PROTOCOL, checklist=cl)
    assert "R2 | row | Table 1: Schedule of Activities (main study) | Gadget therapy and gadget count" in extra
    assert "H2 | heading | 4.1 | Widget Reading and Gadget Count" in extra and "For EVERY checklist entry" in prompt
    _p, extra2 = pf.build_request(_spec(), PROTOCOL, checklist=cl, only=["H3"])
    assert "H3 | heading | 4.2 |" in extra2 and "H2 | heading" not in extra2 and "Answer ONLY these entries" in extra2
    assert "(empty: give every assessment under \"extra\")" in pf.build_request(_spec(), PROTOCOL)[1]


def test_a_heading_that_names_two_assessments_yields_both_and_a_none_heading_yields_nothing():
    cl = _checklist()
    ids = [e["id"] for e in cl["entries"]]
    answer = {"entries": [{"id": "H2", "assessments": [_a("Widget reading", Q_WIDGET), _a("Gadget count", Q_GADGET)]},
                          {"id": "H3", "none": "a definition"}]
              + [{"id": i, "none": "not an assessment"} for i in ids if i not in ("H2", "H3")], "extra": []}
    v = pf.validate_response(_spec(), json.dumps(answer), PROTOCOL, cl, cl["schedule_text"])
    assert [a["name"] for a in v["assessments"]] == ["Widget reading", "Gadget count"] and v["missing"] == []
    assert v["coverage"]["H2"] == {"status": "assessments", "assessments": ["Widget reading", "Gadget count"], "discarded": 0}
    assert v["coverage"]["H3"] == {"status": "none", "reason": "a definition", "assessments": []}
    spec = _spec()
    st = pf.apply(spec, v["assessments"], None, None, PROTOCOL, v["rejected"], checklist=pf.coverage_records(cl, v))
    # the widget form existed; the gadget count gets a form; the "none" heading produced nothing
    assert st["added"] == ["GC"] and [f["form_id"] for f in spec["forms"]] == ["WID", "GC"]
    rec = {c["id"]: c for c in st["checklist"]}
    assert rec["H2"]["assessments"] == ["Widget reading", "Gadget count"] and rec["H3"]["status"] == "none" and st["not_assessed"] == []
    assert any(line.startswith("  Checklist from the protocol: 4 Schedule of Activities row(s) and 5 procedures heading(s); "
                               "1 with assessments, 8 none, 0 not assessed.") for line in pf.summary_lines(spec))


def test_a_skipped_heading_triggers_one_follow_up_and_what_stays_unanswered_is_not_assessed():
    cl = _checklist()
    ids = [e["id"] for e in cl["entries"]]
    calls = []

    async def call(prompt, extra):
        calls.append(extra)
        if len(calls) == 1:   # the first answer leaves out H2 and H4 (and answers H5 without assessments or a reason)
            return json.dumps({"entries": [{"id": i, "none": "nothing to record"} for i in ids if i not in ("H2", "H4", "H5")]
                               + [{"id": "H5", "assessments": []}], "extra": []})
        return json.dumps({"entries": [{"id": "H2", "assessments": [_a("Gadget count", Q_GADGET)]},
                                       {"id": "H5", "none": "kept by the sponsor"}], "extra": []})

    v = asyncio.run(pf.assess(_spec(), PROTOCOL, call, cl))
    assert len(calls) == 2 and "Answer ONLY these entries" in calls[1]
    assert [ln.split(" | ")[0] for ln in calls[1].split("CHECKLIST")[1].split("\n")[1:4]] == ["H2", "H4", "H5"]
    assert [a["name"] for a in v["assessments"]] == ["Gadget count"] and v["missing"] == ["H4"]
    spec = _spec()
    st = pf.apply(spec, v["assessments"], None, None, PROTOCOL, v["rejected"], checklist=pf.coverage_records(cl, v))
    assert st["not_assessed"] == ["H4"]
    assert "  ? not assessed: heading 4.3 \"Sprocket Review\"" in pf.summary_lines(spec)
    # an entry whose assessments all fail quote verification is reported, not dropped silently
    bad = pf.validate_response(_spec(), json.dumps({"entries": [{"id": "H2", "assessments": [_a("Gadget count", "The gadget count is written down somewhere else entirely.")]}]}), PROTOCOL, cl)
    spec3 = _spec()
    pf.apply(spec3, [dict(_a("Widget reading", Q_WIDGET), entries=[])], None, None, PROTOCOL, checklist=pf.coverage_records(cl, bad))
    assert "  ? heading 4.1 \"Widget Reading and Gadget Count\": 1 assessment(s) given, none with a quote found in the protocol" in pf.summary_lines(spec3)
    # a complete first answer needs no second call; a failed follow-up leaves the entries not assessed
    calls.clear()

    async def complete(prompt, extra):
        calls.append(extra)
        return json.dumps({"entries": [{"id": i, "none": "nothing"} for i in ids], "extra": []})

    assert asyncio.run(pf.assess(_spec(), PROTOCOL, complete, cl))["missing"] == [] and len(calls) == 1

    async def failing(prompt, extra):
        calls.append(extra)
        if len(calls) > 2:
            raise RuntimeError("no credit")
        return json.dumps({"entries": [{"id": "R1", "none": "nothing"}], "extra": []})

    calls[:] = ["x"]
    v = asyncio.run(pf.assess(_spec(), PROTOCOL, failing, cl))
    assert len(v["missing"]) == len(ids) - 1


def test_no_form_from_an_eligibility_criterion_or_a_heading_but_from_a_schedule_row_or_a_record_instruction():
    cl = _checklist()
    items = [_a("Sprocket score", Q_ELIG, "eligibility", "2.1"),
             _a("Sprocket review", "Sprocket Review", "heading", "4.3"),
             _a("Gadget therapy", "Gadget therapy is given per local practice.", "course", "3.1"),
             _a("Cog diary", "Cog diary", "schedule", "Table 1"),
             _a("Gadget count", Q_GADGET, "record"),
             _a("Sprocket photographs", "Photographs are stored by the sponsor.", "schedule", "4.3.1")]
    v = pf.validate_response(_spec(), json.dumps({"entries": [{"id": "H2", "assessments": items}], "extra": []}),
                             PROTOCOL, cl, cl["schedule_text"])
    kinds = {a["name"]: a["kind"] for a in v["assessments"]}
    # a "schedule" passage that is not on a schedule-table page is not a schedule row
    assert kinds["Sprocket photographs"] == "other" and v["rejected"]["schedule passage is not in a schedule table"] == 1
    spec = _spec()
    st = pf.apply(spec, v["assessments"], None, None, PROTOCOL, v["rejected"])
    assert st["added"] == ["CD", "GC"] and [f["form_id"] for f in spec["forms"]] == ["WID", "CD", "GC"]
    recs = {r["assessment"]: r for r in st["assessments"]}
    assert recs["Sprocket score"]["not_built"] and "an eligibility condition, not a requirement to record data" in recs["Sprocket score"]["reason"]
    assert "a section heading without an instruction to record" in recs["Sprocket review"]["reason"]
    assert recs["Gadget therapy"]["not_built"] and recs["Sprocket photographs"]["not_built"]
    # listed in the Study Specification as not built, with the reason
    title, _n, _h, rows, _w = pb.section(spec)
    assert title == "FORMS NOT BUILT" and [r[1] for r in rows] == ["Sprocket score", "Sprocket review", "Gadget therapy", "Sprocket photographs"]
    assert rows[0][3] == "Not built" and rows[0][5] == Q_ELIG
    assert any(line.startswith("  x \"Sprocket score\" (protocol 2.1): no form built") for line in pf.summary_lines(spec))
    # such an assessment still maps to a form that exists (nothing is added, nothing is listed as not built)
    spec2 = _spec()
    st2 = pf.apply(spec2, [dict(v["assessments"][0], name="Widget reading")], None, None, PROTOCOL)
    assert st2["added"] == [] and st2["assessments"][0]["form"] == "WID" and not st2["assessments"][0].get("not_built")


def test_the_basis_check_judges_forms_the_completeness_check_added():
    def run(basis_answer):
        spec = _spec()
        pf.apply(spec, [dict(_a("Gadget count", Q_GADGET), entries=["H2"]), dict(_a("Widget reading", Q_WIDGET), entries=["H2"])],
                 None, None, PROTOCOL)
        assert [f["form_id"] for f in spec["forms"]] == ["WID", "GC"]
        assert [f["form_id"] for f in pb.candidates(spec, PROTOCOL)] == ["WID", "GC"]   # the added form is a candidate
        st = pb.apply(spec, json.dumps({"forms": [{"form_id": "GC", **basis_answer}]}), PROTOCOL, fresh=True)
        return spec, st

    # the basis check finds no requirement for the added form: it is not built, and listed with the reason
    spec, st = run({"quotes": [{"text": Q_ELIG, "section": "2.1", "kind": "eligibility"}], "fields": []})
    assert [f["form_id"] for f in spec["forms"]] == ["WID"] and [r["form_id"] for r in st["removed"]] == ["GC"]
    assert "an eligibility condition" in st["removed"][0]["reason"]
    assert pf.state(spec)["assessments"][0]["added"] is False and pf.state(spec)["assessments"][0]["form"] is None
    pf.refresh_sources(spec)
    assert pf.state(spec)["added"] == [] and [r[0] for r in pb.section(spec)[3]] == ["GC"]
    # the analysis form is supported by the assessment that maps to it (a record instruction)
    assert "assessment of the completeness check" in {r["form_id"]: r for r in st["forms"]}["WID"]["basis"]
    # with a record instruction that names its field the added form stays
    spec, st = run({"quotes": [{"text": Q_GADGET, "section": "4.1", "kind": "record"}], "fields": ["GCPERF"]})
    assert [f["form_id"] for f in spec["forms"]] == ["WID", "GC"] and st["removed"] == []
    # an assessment whose passage is no requirement does not vouch for the form it maps to
    spec = _spec()
    spec["forms"][0]["library_match"] = {"status": "CUSTOM", "source_type": "protocol"}
    pf.apply(spec, [dict(_a("Widget reading", Q_ELIG, "eligibility"), entries=[])], None, None, PROTOCOL)
    st = pb.apply(spec, json.dumps({"forms": [{"form_id": "WID", "quotes": [], "fields": []}]}), PROTOCOL, fresh=True)
    assert spec["forms"] == [] and st["removed"][0]["form_id"] == "WID"


def test_kill_switch_for_the_checklist(monkeypatch):
    assert pf.checklist_enabled()
    monkeypatch.setenv("PROTOCOL_FORMS_CHECKLIST", "0")
    assert not pf.checklist_enabled()
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py")).read()
    step = src[src.index("async def _protocol_forms_step("):src.index("async def _protocol_basis_step(")]
    helper = src[src.index("async def _protocol_checklist("):src.index("async def _merged_protocol_checks(")]
    assert "_pf.checklist_enabled()" in helper and "_ps.checklist(" in helper and "_ps.log_lines(" in helper
    assert "_protocol_checklist(" in step and "_pf.assess(" in step


def test_the_same_assessment_under_a_longer_name_is_covered_by_the_form_titled_and_fielded_for_it():
    forms = [fx._f("WC", "Widget Characteristics", None, [("text", "WCGRD", "Widget grading result", {}), ("date", "WCDAT", "Date", {})]),
             fx._f("SP", "Sprocket Log", None, [("text", "SPTXT", "Comment", {})])]
    a = {"name": "Widget assessment / grading", "domain": "RS", "quote": "q", "log": False}
    f, basis = pf.cover(a, forms)
    assert f["form_id"] == "WC" and basis == "named in the form title and its fields"
    # a different assessment that only shares a word with the title is not covered
    assert pf.cover({"name": "Widget calibration", "domain": "RS", "quote": "q", "log": False}, forms)[0] is None
    assert pf.cover({"name": "Sprocket imaging", "domain": "RS", "quote": "q", "log": False}, forms)[0] is None
