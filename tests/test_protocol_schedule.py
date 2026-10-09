"""The visit schedule follows the protocol's structure (protocol_schedule.py). Synthetic protocols only."""
import copy
import json

import protocol_schedule as sch


def line(*cells, label=""):
    """A layout-text line: the row label at the left edge, each (x, text) centred at column x."""
    out = " " + label if label else ""
    for x, text in cells:
        start = x - len(text) // 2
        out = out.ljust(start) + text
    return out


HEAD = [
    "Table 1: Schedule of Activities",
    "",
    line((30, "Enrolment"), (45, "Dose"), (60, "Dose"), (83, "Follow-up")),
    line((45, "1"), (60, "2")),
    line((60, "D15"), (75, "D29"), (90, "D57")),
    line((90, "(EOT)")),
]
ROWS = [
    line((30, "X"), label="Consent form"),
    "",
    line((30, "X"), (45, "X2"), (60, "X2"), (75, "X"), (90, "X"), label="Blood sample1"),
    "",
    line((45, "X"), (60, "X"), label="Diary card"),
    "",
]
SUB = [
    " Samples",
    line((30, "Pre-"), (60, "After Dose 1"), (90, "Day")),
    line((30, "dose"), (90, "29")),
    "",
    line((45, "1"), (60, "4"), (75, "8")),
    line((45, "hour"), (60, "hours"), (75, "hours"), (90, "D29")),
    line((30, "X3"), (45, "X"), (60, "X"), (75, "X"), label="Urine sample"),
    "",
]
NOTES = [
    "   1.  Blood samples are sent to the central laboratory on the day they are taken, as the manual describes.",
    "   2.  Blood is taken before dosing; an extra sample is taken at 1 hour, 4 hours, and 8 hours after dose 1 only.",
    "   3.  Pre-dose urine samples are taken on the day of the first dose, before the dose is given to the patient.",
]
PAGE = "\n".join(HEAD + ROWS + SUB + NOTES)
TEXT = PAGE + "\nAbbreviations: EOT, End of Treatment; D, day."
LIST_PAGE = "\n".join([
    "Table 3: Sampling times",
    "",
    " Timepoints                              Samples",
    "  Before:                                1. Urine",
    "     •   Start",
    "  After dose 1:",
    "     •   1 hour, 4 hours, and 8 hours",
    "     •   Day 15 (two weeks after dose 1)",
    "     •   Day 29",
    "",
    "The samples are stored frozen until they are shipped to the laboratory named in the manual for the study.",
])


def structure(pages=(PAGE,)):
    return sch.read(None, pages=list(pages))


def spec_with(events, forms=None):
    rows = [{"event": oid, "timepoint": text, "visit_number": i + 1, "arm": "ALL"} for i, (oid, text) in enumerate(events)]
    forms = forms or [{"form_id": "LB", "form_title": "Blood sample", "visits_assigned": [events[0][0]]},
                      {"form_id": "UR", "form_title": "Urine sample", "visits_assigned": [events[0][0]]},
                      {"form_id": "DS", "form_title": "End of Treatment", "visits_assigned": ["SE_EOT"]}]
    return {"timepoint_csv": {"rows": rows}, "forms": forms,
            "schedule_of_events": {"visit_mappings": [{"target_oid": oid, "target_name": text} for oid, text in events],
                                   "form_placements": [{"target_visit_oid": v, "form_id": f["form_id"]} for f in forms
                                                       for v in f["visits_assigned"]]},
            "study_meta": {"protocol_forms": {
                "checklist": [{"id": "R1", "type": "row", "label": "Consent form"},
                              {"id": "R2", "type": "row", "label": "Blood sample1"},
                              {"id": "R3", "type": "row", "label": "Urine sample"}],
                "assessments": [{"assessment": "blood sample", "form": "LB", "entries": ["R2"]},
                                {"assessment": "urine sample", "form": "UR", "entries": ["R3"]}]}}}


FULL = [("SE_ENROLMENT", "Enrolment"), ("SE_DOSE_1", "Dose 1 (Day 1)"), ("SE_H1", "1 hour after dose 1"),
        ("SE_H4", "4 hours after dose 1"), ("SE_H8", "8 hours after dose 1"), ("SE_DOSE_2", "Dose 2 (Day 15)"),
        ("SE_D29", "Day 29"), ("SE_D57", "Day 57 (End of Treatment)"), ("SE_UNSCHEDULED", "Unscheduled visit"),
        ("SE_COMMON", "Common")]
SAME = json.dumps({"same_visit": [{"entry": "UX", "same_as": "UY", "quote":
                                   "Pre-dose urine samples are taken on the day of the first dose, before the dose is given to the patient."}]})


def answer(st, quote=None):
    q = sch.questions(st["units"])[0]["id"]
    dose1 = next(u["id"] for u in st["units"] if u["label"] == "Dose 1")
    a = json.loads(SAME)
    a["same_visit"][0].update(entry=q, same_as=dose1)
    if quote is not None:
        a["same_visit"][0]["quote"] = quote
    return json.dumps(a)


def run(events, fresh=True, ans="default", pages=(PAGE,), forms=None):
    st = structure(pages)
    spec = spec_with(events, forms)
    state = sch.apply(spec, st, answer(st) if ans == "default" else ans, TEXT, fresh=fresh,
                      aliases=[["End of Treatment", "EOT"]])
    return spec, state


def oids(spec):
    return [e["oid"] for e in sch.events_of(spec)]


def test_time_expressions_are_read_generically():
    d = sch.describe("Injection 2 W2-3")
    assert d["primary"] == ("at", "week", 2, 3) and d["name"] == ["injection", "2"] and not d["time_only"]
    assert sch.describe("Week 1 (6-8 days after dose #1)")["windows"] == [("at", "week", 1, 1), ("dur", "day", 6, 8)]
    assert sch.describe("Post Dose#1 2-4 hours")["time_only"] and sch.describe("24 hrs (+/- 4 hours)")["primary"] == ("dur", "hour", 24, 24)
    assert sch.describe("Day -28 to Day 0")["primary"] == ("at", "day", -28, 0)
    assert sch.describe("Dose #2")["windows"] == [] and sch.describe("Visit 3 (EOT)")["tags"] == ["EOT"]


def test_multi_row_and_spanning_headers_are_joined():
    t = sch.read_tables([PAGE])[0]
    assert [sch.full_label(c) for c in t["columns"]] == [
        "Enrolment", "Dose 1", "Dose 2 D15", "Follow-up: D29", "Follow-up: D57 (EOT)",
        "Pre-dose", "After Dose 1: 1 hour", "After Dose 1: 4 hours", "After Dose 1: 8 hours", "Day 29 D29"]
    rows = {r["label"]: r["marks"] for r in t["rows"]}
    assert rows["Blood sample1"] == {0: "X", 1: "X2", 2: "X2", 3: "X", 4: "X"} and rows["Diary card"] == {1: "X", 2: "X"}
    assert rows["Urine sample"] == {5: "X3", 6: "X", 7: "X", 8: "X"}
    assert sorted(t["notes"]) == [1, 2, 3]


def test_a_referenced_footnote_that_lists_times_gives_timepoints():
    t = sch.read_tables([PAGE])[0]
    assert [x["label"] for x in sch.note_timepoints(t)] == ["1 hour", "4 hours", "8 hours"]
    # a note with time words but no list of them, and a note nothing refers to, give none
    t2 = copy.deepcopy(t)
    t2["notes"] = {2: "The sample is taken within 2 weeks of dosing and no later than 4 weeks after it.",
                   9: "Taken at 1 hour, 2 hours and 3 hours."}
    assert sch.note_timepoints(t2) == []


def test_a_table_whose_rows_are_times_is_a_timepoint_list():
    tp = sch.timepoint_tables([LIST_PAGE])
    assert [sch.full_label(x) for x in tp[0]["timepoints"]] == [
        "Before: Start", "After dose 1: 1 hour", "After dose 1: 4 hours", "After dose 1: 8 hours",
        "After dose 1: Day 15 (two weeks after dose 1)", "After dose 1: Day 29"]
    assert sch.timepoint_tables(["Table 9: Doses by weight\n\n Weight      Dose\n  Low        One tablet\n  Middle     Two tablets\n  High       Three tablets\n"]) == []


def test_units_merge_the_same_visit_with_a_reason():
    st = structure((PAGE, LIST_PAGE))
    labels = {u["label"]: u for u in st["units"]}
    # a spanning header is not the visit's name: the "Day 29" column of the second grid and the list's "Day 29"
    # are the D29 visit under "Follow-up"
    assert "Day 29 D29" in labels["Follow-up: D29"]["labels"] and "After dose 1: Day 29" in labels["Follow-up: D29"]["labels"]
    assert "1 hour" in labels["After Dose 1: 1 hour"]["labels"]
    # a named visit and a time-only label with the same time stay two visits when the time-only one has activity
    assert "After dose 1: Day 15 (two weeks after dose 1)" not in labels["Dose 2 D15"]["labels"]
    assert all(m["reason"] for m in st["merges"])
    # labels without a time of their own that are not a named visit column are put to the question
    assert [u["label"] for u in sch.questions(st["units"])] == ["Pre-dose", "Before: Start"]


def test_every_header_has_an_event_and_nothing_changes_when_the_schedule_is_complete():
    spec, state = run(FULL[:-3] + [("SE_D57", "Day 57 (End of Treatment)")] + FULL[-2:])
    assert state["fixes"] == [] and state["flags"] == []
    assert all(u["event"] for u in state["units"]) and len(sch.mapping_lines(spec)) == len(state["units"])


def test_missing_timepoints_are_added_with_the_forms_marked_in_their_column():
    events = [e for e in FULL if e[0] not in ("SE_H1", "SE_H4", "SE_H8")]
    spec, state = run(events)
    added = [f for f in state["fixes"] if f["fix"] == "added"]
    assert [f["unit"] for f in added] == ["After Dose 1: 1 hour", "After Dose 1: 4 hours", "After Dose 1: 8 hours"]
    assert all(f["forms"] == ["UR"] for f in added)   # the row marked there; never a form the schedule does not place
    got = oids(spec)
    assert got[1:5] == ["SE_DOSE_1"] + [f["event"] for f in added] and len(got) == len(events) + 3
    ur = next(f for f in spec["forms"] if f["form_id"] == "UR")
    assert all(f["event"] in ur["visits_assigned"] for f in added)
    assert [r["visit_number"] for r in spec["timepoint_csv"]["rows"]][:8] == list(range(1, 9))
    assert any("added event" in x for x in spec["review_flags"]["schedule_from_soa"])


def test_an_event_that_folds_several_timepoints_is_split():
    events = [("SE_ENROLMENT", "Enrolment"), ("SE_DOSE_1", "Dose 1 (pre-dose and 1h, 4h, 8h after)"),
              ("SE_DOSE_2", "Dose 2 (Day 15)"), ("SE_D29", "Day 29"), ("SE_D57", "Day 57 (EOT)")]
    spec, state = run(events)
    assert [f["fix"] for f in state["fixes"]] == ["split"] * 3 and all(f["from_event"] == "SE_DOSE_1" for f in state["fixes"])
    assert "SE_DOSE_1" in oids(spec) and len(oids(spec)) == 8


def test_two_labels_are_one_visit_only_with_a_verified_protocol_reason():
    events = [e for e in FULL]
    # with the protocol's sentence: Pre-dose is the Dose 1 visit, no event is added
    spec, state = run(events)
    pre = next(u for u in state["units"] if u["label"] == "Pre-dose")
    assert pre["status"] == "merged" and pre["same_visit_as"] == "Dose 1" and pre["event"] == "SE_DOSE_1"
    assert any(m.get("quote", "").startswith("Pre-dose urine samples") for m in state["merges"])
    assert not state["fixes"]
    # a quote that is not in the protocol, or does not name both, is no reason: Pre-dose becomes a visit of its own
    for bad in ("Pre-dose samples and dose 1 are the same visit.", "Blood samples are sent to the central laboratory on the day they are taken"):
        st = structure()
        spec, state = run(events, ans=answer(st, bad))
        assert state["rejected"] and [f["unit"] for f in state["fixes"]] == ["Pre-dose"]
        assert state["fixes"][0]["forms"] == ["UR"] and len(oids(spec)) == len(events) + 1
    # no answer at all (the call failed, or SCHEDULE_FROM_SOA_AI=0): nothing is added for it, it is flagged
    spec, state = run(events, ans=None)
    assert not state["fixes"] and any("Pre-dose" in x for x in state["flags"])


def test_a_second_end_event_next_to_the_visit_the_schedule_labels_so_is_dropped():
    events = FULL[:8] + [("SE_EOT", "End of Treatment")] + FULL[8:]
    spec, state = run(events)
    assert [(f["fix"], f["event"], f["kept"]) for f in state["fixes"]] == [("duplicate", "SE_EOT", "SE_D57")]
    assert "SE_EOT" not in oids(spec) and len(oids(spec)) == len(events) - 1
    ds = next(f for f in spec["forms"] if f["form_id"] == "DS")
    assert ds["visits_assigned"] == ["SE_D57"]
    assert {"target_visit_oid": "SE_D57", "form_id": "DS"} in spec["schedule_of_events"]["form_placements"]
    # when it is the only such event it IS that visit
    only = [e for e in events if e[0] != "SE_D57"]
    spec, state = run(only)
    assert not state["fixes"] and next(u for u in state["units"] if "EOT" in u["label"])["event"] == "SE_EOT"


def test_an_event_the_schedule_does_not_have_is_flagged_not_removed():
    spec, state = run(FULL + [("SE_PHONE", "Telephone contact")])
    assert state["unmapped_events"] == ["SE_PHONE"] and "SE_PHONE" in oids(spec)


def test_reused_specification_is_recorded_not_changed():
    events = [e for e in FULL if e[0] != "SE_H4"] + [("SE_EOT", "End of Treatment")]
    spec, state = run(events, fresh=False)
    assert oids(spec) == [e[0] for e in events] and not state["applied"]
    assert sorted(f["fix"] for f in state["fixes"]) == ["added", "duplicate"] and all("would" in f["text"] for f in state["fixes"])


def test_events_that_do_not_fit_the_reading_are_left_alone():
    spec, state = run([("SE_A", "Alpha"), ("SE_B", "Beta"), ("SE_C", "Gamma"), ("SE_ENROLMENT", "Enrolment")])
    assert state["status"] == "not_applied" and oids(spec) == ["SE_A", "SE_B", "SE_C", "SE_ENROLMENT"]


def test_kill_switches(monkeypatch):
    spec = spec_with(FULL)
    assert sch.needs_check(spec, TEXT)
    monkeypatch.setenv("SCHEDULE_FROM_SOA", "0")
    assert not sch.enabled() and not sch.needs_check(spec, TEXT)
    monkeypatch.delenv("SCHEDULE_FROM_SOA")
    monkeypatch.setenv("SCHEDULE_FROM_SOA_AI", "0")
    assert sch.enabled() and not sch.ai_enabled()


def test_the_question_names_only_labels_without_a_time_of_their_own():
    st = structure()
    prompt, extra = sch.build_request(st["units"], st["schedule_text"] or PAGE)
    q = extra.split("OTHER ENTRIES")[0]
    assert "Pre-dose" in q and "Dose 1" not in q.replace("Pre-dose", "") and "VERBATIM" in prompt
    assert sch.build_request([u for u in st["units"] if u["label"] != "Pre-dose"], PAGE) is None


def test_an_added_event_goes_after_the_latest_event_of_the_visits_before_it():
    # the analysis lists Day 29 before Dose 2 (Day 15); the missing 4-hour timepoint still goes right after the 1-hour one
    events = [("SE_ENROLMENT", "Enrolment"), ("SE_D29", "Day 29"), ("SE_DOSE_1", "Dose 1 (Day 1)"), ("SE_H1", "1 hour after dose 1"),
              ("SE_H8", "8 hours after dose 1"), ("SE_DOSE_2", "Dose 2 (Day 15)"), ("SE_D57", "Day 57 (EOT)")]
    spec, state = run(events)
    got = oids(spec)
    assert got.index("SE_H1") + 1 == got.index(state["fixes"][0]["event"]) and len(state["fixes"]) == 1
