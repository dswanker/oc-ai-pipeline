"""DVS extractor: engine-combined constraints are tested per clause; gate test values are real choice codes."""
import sys, os, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "dvs-specification", "scripts"))
from extract_dvs_from_forms import _split_and_clauses, extract_dvs_data


def test_split_only_engine_shape():
    assert _split_and_clauses("(a = 1) and (b <= today())") == ["a = 1", "b <= today()"]
    assert _split_and_clauses("(a) and (b) and (c)") == ["a", "b", "c"]
    for e in (". <= today()", "(a) or (b)", "(a)", "(a) and b", "x and (y)", ""):
        assert _split_and_clauses(e) is None


def _cases(survey, choices):
    spec = {"study_meta": {}, "forms": [{"form_id": "AE", "form_title": "Adverse Events"}]}
    with contextlib.redirect_stdout(io.StringIO()):
        d = extract_dvs_data(spec, {"forms": {"F_AE.xlsx": {"survey": survey, "choices": choices}}})
    return d["uat_cases"]


def test_combined_constraint_tests_every_clause():
    survey = [{"type": "date", "name": "AESTDAT", "label": "Start"},
              {"type": "date", "name": "AEENDAT", "label": "End",
               "constraint": "(. = '' or ${AESTDAT} = '' or . >= ${AESTDAT}) and (. <= today())",
               "constraint_message": "End date must be on or after the start date. Future dates are not allowed."}]
    sc = [c["Scenario"] for c in _cases(survey, []) if c["Item_OID"].endswith("AEENDAT")]
    assert any("on-or-after AESTDAT" in s and s.startswith("Sad") for s in sc)
    assert any("future date" in s and s.startswith("Sad") for s in sc)


def test_gate_values_are_real_choice_codes():
    survey = [{"type": "select_one yn", "name": "ONGO", "label": "Ongoing"},
              {"type": "date", "name": "AEENDAT", "label": "End", "relevant": "${ONGO} = 'no'"}]
    choices = [{"list_name": "yn", "name": "yes", "label": "Yes"}, {"list_name": "yn", "name": "no", "label": "No"}]
    loads = {c["Scenario"].split(":")[0]: str(c["Load_Value"]) for c in _cases(survey, choices)
             if c["Item_OID"].endswith("AEENDAT")}
    assert "ONGO=no" in loads["Shown path"] and "ONGO=yes" in loads["Hidden path"]
