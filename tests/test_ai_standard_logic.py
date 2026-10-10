"""AI-suggested logic for customer standard forms without logic, and gap-filling (ai_standard_logic.py):
structured output only, validated, listed as DVS proposals, never applied without Approve. Scripted responses."""
import contextlib, copy, io, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "dvs-specification", "scripts"))
import ai_standard_logic as asl
import dvs_edits
import standards_match as sm
from conventions_engine import apply_conventions
from standards import fixtures as fx
from test_standards_match import _dvs_bytes, _set_actions, _form

PROTOCOL = fx.PROTOCOL_TEXT + "\nWeight at onset must be between 30 and 250 kg for dosing."


def _odm_matched():
    with contextlib.redirect_stdout(io.StringIO()):
        return sm.apply(fx.spec(), sm.load_sources([("standard.xml", fx.ODM)]))


RESPONSE = json.dumps({"suggestions": [
    {"kind": "future_date", "form": "AE", "field": "AESTDAT", "message": "Start date cannot be in the future.", "rationale": "r"},
    {"kind": "date_order", "form": "AE", "field": "AEENDAT", "operator": ">=", "other_field": "AESTDAT", "rationale": "r"},
    {"kind": "range", "form": "AE", "field": "AEWT", "min": 30, "max": 250,
     "protocol_quote": "Weight at onset must be between 30 and 250 kg"},
    {"kind": "relevant", "form": "AE", "field": "AEENDAT", "when_field": "AESEV", "equals": "2"},
    # everything below must be rejected
    {"kind": "range", "form": "AE", "field": "AEWT", "min": 1, "max": 500, "protocol_quote": "weight must be 1 to 500 kg"},
    {"kind": "future_date", "form": "AE", "field": "AETERM"},
    {"kind": "future_date", "form": "AE", "field": "NOPE"},
    {"kind": "future_date", "form": "VS", "field": "VSDAT"},
    {"kind": "date_order", "form": "AE", "field": "AEENDAT", "operator": "~", "other_field": "AESTDAT"},
    {"kind": "date_order", "form": "AE", "field": "AEENDAT", "operator": ">=", "other_field": "AEWT"},
    {"kind": "relevant", "form": "AE", "field": "AETERM", "when_field": "AESEV", "equals": "9"},
    {"kind": "relevant", "form": "AE", "field": "AETERM", "when_field": "AESTDAT", "equals": "1"},
    {"kind": "calculation", "form": "AE", "field": "AEWT"},
    {"kind": "future_date", "form": "AE", "field": "AESTDAT"},
    "garbage"]})


def test_odm_only_standard_has_no_logic_and_is_a_target():
    out = _odm_matched()
    form = _form(out, "AE")
    assert form["customer_standard"]["has_logic"] is False
    (f, no_logic, gaps), = asl.targets(out)
    assert f is form and no_logic and "AESTDAT" in gaps and "AEWT" in gaps
    prompt, extra = asl.build_request(out, PROTOCOL)
    assert "NO LOGIC" in extra and "AESEV | select_one" in extra and "1=Mild" in extra and "PROTOCOL TEXT" in extra
    assert "GAP: date without a future-date check" in extra and "structured" in prompt
    assert asl.build_request(fx.spec(), PROTOCOL) is None            # no customer standard form: nothing to ask


def test_validation_accepts_real_references_and_rejects_the_rest():
    out = _odm_matched()
    v = asl.validate_response(out, RESPONSE, PROTOCOL)
    got = {(p["target_field"], p["category"]): p for p in v["proposals"]}
    assert set(got) == {("AESTDAT", "future_date"), ("AEENDAT", "date_order"), ("AEWT", "range"), ("AEENDAT", "relevant")}
    assert got[("AESTDAT", "future_date")]["logic"] == ". <= today()"
    assert got[("AEENDAT", "date_order")]["logic"] == ". = '' or ${AESTDAT} = '' or . >= ${AESTDAT}"
    assert got[("AEWT", "range")]["logic"] == ". >= 30 and . <= 250"
    assert got[("AEENDAT", "relevant")]["logic"] == "${AESEV} = '2'"
    assert got[("AEENDAT", "relevant")]["check_type"] == "Conditional Display"
    assert all(p["source"] == "AI-Suggested" and p["kind"] == "ai" and p["id"].startswith("AIS-") for p in v["proposals"])
    assert v["rejected"] == {"quote_not_in_protocol": 1, "incompatible_types": 2, "unknown_field": 1,
                             "not_a_customer_standard_form": 1, "bad_operator": 1, "bad_condition": 2,
                             "unknown_kind": 1, "duplicate": 1, "malformed": 1}
    assert v["proposed"] == 15
    for bad in ("not json", "", '{"suggestions": "x"}'):
        assert asl.validate_response(out, bad, PROTOCOL)["proposals"] == []


def test_suggestions_are_proposed_in_the_dvs_and_applied_only_on_approve(monkeypatch):
    # AI suggestions are never applied without Approve. The engine's own checks on a logic-free standard are a
    # separate rule (tests/test_standard_logic_free.py); it is switched off here to look at the suggestions alone.
    monkeypatch.setenv("STANDARD_LOGIC_FREE_APPLY", "0")
    out = _odm_matched()
    pristine = copy.deepcopy(_form(out, "AE")["survey"])
    asl.store(out, asl.validate_response(out, RESPONSE, PROTOCOL))
    assert asl.already_done(out)
    assert _form(out, "AE")["survey"] == pristine and sm.integrity(out) == []      # nothing applied
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(out, study_id="T", customer_subdomain="", client_name="")
    ai = [p for p in sm.proposals(out) if p["kind"] == "ai"]
    assert len(ai) == 4                                                           # the engine pass keeps them
    assert [sm.core_row(r) for r in _form(out, "AE")["survey"]] == [sm.core_row(r) for r in pristine]
    rng = next(p for p in ai if p["category"] == "range")
    rel = next(p for p in ai if p["category"] == "relevant")
    fut = next(p for p in ai if p["category"] == "future_date")
    xb, seen = _set_actions(_dvs_bytes(out), {rng["id"]: "Approve", rel["id"]: "Approve", fut["id"]: "Reject"})
    assert seen[rng["id"]]["Status"] == "Proposed" and seen[rng["id"]]["Check Source"] == "AI-Suggested"
    assert seen[rng["id"]]["Expression / Calculation"] == ". >= 30 and . <= 250"
    res = dvs_edits.apply_actions(out, dvs_edits.parse_actions(xb))
    assert res["applied"] == 3, res["results"]
    rows = {r["name"]: r for r in _form(out, "AE")["survey"]}
    assert rows["AEWT"]["constraint"] == ". >= 30 and . <= 250" and rows["AEENDAT"]["relevant"] == "${AESEV} = '2'"
    assert "constraint" not in rows["AESTDAT"]
    left = {p["id"] for p in sm.proposals(out)}
    assert rng["id"] not in left and rel["id"] not in left and fut["id"] not in left
    # a rejected suggestion is not proposed again, an approved one is not proposed twice
    v2 = asl.validate_response(out, RESPONSE, PROTOCOL)
    assert v2["rejected"].get("rejected_by_dm_earlier", 0) >= 1 and v2["rejected"].get("already_checked", 0) >= 1
    assert v2["rejected"].get("already_has_show_when") == 1
    assert not any(p["target_field"] in ("AEWT", "AESTDAT") or p["category"] == "relevant" for p in v2["proposals"])


def test_not_proposed_twice_when_the_rules_engine_already_proposes_it():
    out = _odm_matched()
    with contextlib.redirect_stdout(io.StringIO()):
        apply_conventions(out, study_id="T", customer_subdomain="", client_name="")
    eng = next((p for p in sm.proposals(out) if p["kind"] == "engine" and p["logic"] == ". <= today()"), None)
    if eng is None:
        return
    v = asl.validate_response(out, json.dumps({"suggestions": [
        {"kind": "future_date", "form": eng["target_form"], "field": eng["target_field"]}]}), PROTOCOL)
    assert v["proposals"] == [] and v["rejected"] == {"already_proposed_by_rules_engine": 1}


def test_form_with_logic_is_only_a_target_for_its_gaps_and_kill_switch(monkeypatch):
    with contextlib.redirect_stdout(io.StringIO()):
        out = sm.apply(fx.spec(), sm.load_sources([("AEGEN.xlsx", fx.xlsform_bytes())]))
    (f, no_logic, gaps), = asl.targets(out)
    assert not no_logic and "AEENDAT" in gaps and "AESTDAT" not in gaps      # AESTDAT already has its check
    v = asl.validate_response(out, json.dumps({"suggestions": [
        {"kind": "future_date", "form": "AEGEN", "field": "AESTDAT"},
        {"kind": "date_order", "form": "AEGEN", "field": "AEENDAT", "operator": ">=", "other_field": "AESTDAT"}]}), "")
    assert [p["target_field"] for p in v["proposals"]] == ["AEENDAT"] and v["rejected"] == {"already_checked": 1}
    monkeypatch.setenv("AI_STANDARD_LOGIC", "0")
    assert asl.enabled() is False
