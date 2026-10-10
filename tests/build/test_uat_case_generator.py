"""
UAT case generation (skills/dvs-specification/scripts/extract_dvs_from_forms.py).

The survey rows mirror the BioIVT form rows behind the generator problems in
docs/UAT_FAILURE_ANALYSIS_2026-10-06.md (root cause 4).
"""
import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "skills", "dvs-specification", "scripts"))

import extract_dvs_from_forms as gen  # noqa: E402
import uat_loader  # noqa: E402

_STRUCT = {
    "study_meta": {"protocol_number": "TEST-001"},
    "forms": [
        {"form_id": "FU", "form_title": "Follow-up Data", "visits_assigned": ["SE_FU"]},
        {"form_id": "EC", "form_title": "eConsent Donor Form", "visits_assigned": ["SE_FU"]},
    ],
}

_FU_SURVEY = [
    {"type": "calculate", "name": "DOB_CF", "bind::oc:external": "clinicaldata",
     "calculation": "instance('clinicaldata')/ODM/ClinicalData/SubjectData/@X"},
    {"type": "date", "name": "DOB", "label": "Date of Birth", "bind::oc:itemgroup": "FUDEM",
     "relevant": "${DOB_CF}=''"},
    {"type": "text", "name": "DOB_SF", "label": "Date of Birth (from the Short Form)",
     "bind::oc:itemgroup": "FUDEM", "relevant": "${DOB_CF}!=''",
     "calculation": "${DOB_CF}", "readonly": "yes"},
    {"type": "integer", "name": "LENGTH_STAY", "label": "Days in hospital",
     "bind::oc:itemgroup": "FUOUT", "constraint": ". >= 0 and . <= 365"},
    {"type": "integer", "name": "VISITNUM", "label": "Visit Number",
     "bind::oc:itemgroup": "FUOUT", "constraint": ". > 1 and . <= 100"},
    {"type": "decimal", "name": "CDRSCR", "label": "CDR score",
     "bind::oc:itemgroup": "FUOUT", "constraint": ". >= 0 and . <= 3"},
    {"type": "date", "name": "REMISSDT", "label": "Remission date", "bind::oc:itemgroup": "FUOUT",
     "constraint": ". <= today()"},
    {"type": "date", "name": "REOCCURDT", "label": "Recurrence date", "bind::oc:itemgroup": "FUOUT"},
    {"type": "integer", "name": "MONDISFREE", "label": "Number of months disease free",
     "bind::oc:itemgroup": "FUOUT", "constraint": ". >= 0 and . <= 600", "readonly": "yes",
     "calculation": "if(${REMISSDT}='' or ${REOCCURDT}='', '', "
                    "int((decimal-date-time(${REOCCURDT}) - decimal-date-time(${REMISSDT})) div 30.44))"},
    {"type": "date", "name": "COLLDT", "label": "Visit Date", "bind::oc:itemgroup": "FUOUT",
     "required": "yes", "constraint": ". <= today()", "calculation": "${DOB_CF}", "readonly": "yes"},
]

_EC_SURVEY = [
    {"type": "text", "name": "EMAIL", "label": "E-mail", "bind::oc:itemgroup": "A"},
    {"type": "text", "name": "CONFIRM_EMAIL", "label": "Confirm e-mail address",
     "bind::oc:itemgroup": "A", "constraint": ". = ${EMAIL}"},
]


@pytest.fixture(scope="module")
def cases():
    forms = {"forms": {"FU.xlsx": {"survey": _FU_SURVEY, "choices": []},
                       "EC.xlsx": {"survey": _EC_SURVEY, "choices": []}}}
    return gen.extract_dvs_data(_STRUCT, forms)["uat_cases"]


def _for(cases, name):
    return [c for c in cases if c["Item_Name"] == name]


def test_every_case_names_its_item(cases):
    assert all(c["Item_Name"] for c in cases)
    assert {c["Item_OID"] for c in _for(cases, "LENGTH_STAY")} == {"I_FOLLO_LENGTH_STAY"}


def test_integer_fields_get_whole_number_mid_range_values(cases):
    mids = {c["Item_Name"]: c["Load_Value"] for c in cases
            if c["Scenario"].startswith("Mid-range")}
    assert mids["LENGTH_STAY"] == "182"     # was 182.5
    assert mids["VISITNUM"] == "50"         # was 50.5
    assert mids["CDRSCR"] == "1.5"          # a decimal field keeps its midpoint
    assert all("." not in c["Load_Value"] for c in _for(cases, "LENGTH_STAY") + _for(cases, "VISITNUM"))


def test_calc_path_loads_typed_values_only_into_fields_a_user_enters(cases):
    calc = {c["Item_Name"]: c for c in cases if c["Scenario"].startswith("Calc path")}
    assert calc["MONDISFREE"]["Load_Value"] == "REMISSDT=2026-02-01, REOCCURDT=2026-02-01"
    # a source pulled from another form cannot be loaded here: nothing is loaded
    assert calc["DOB_SF"]["Load_Value"] == ""
    assert "DOB_CF" in calc["DOB_SF"]["Input Data"]
    assert calc["COLLDT"]["Load_Value"] == ""
    assert not any("Test value" in c["Load_Value"] for c in cases)


def test_boundary_values_are_not_written_into_calculated_fields(cases):
    for name in ("MONDISFREE", "COLLDT"):
        for c in _for(cases, name):
            if c["Scenario"].startswith("Calc path"):
                continue
            # nothing is loaded into the field's own item
            assert c["Load_Value"] == "" or "=" in c["Load_Value"], (name, c["Scenario"])
    scenarios = [c["Scenario"] for c in _for(cases, "MONDISFREE")]
    assert not any("happy path: value" in s or "sad path: value" in s for s in scenarios)
    assert any(s.startswith("Calculated field: constraint rule") for s in scenarios)
    # a field a user does enter keeps its boundary cases
    assert len([c for c in _for(cases, "LENGTH_STAY") if "path: value" in c["Scenario"]]) == 5


def test_no_sad_or_happy_path_row_has_a_blank_load_value(cases):
    for c in cases:
        if c["Scenario"].startswith(("Sad path", "Happy path")):
            assert c["Load_Value"].strip(), (c["Item_Name"], c["Scenario"])


def test_value_that_must_equal_another_field_gets_a_two_step_case(cases):
    happy, sad = _for(cases, "CONFIRM_EMAIL")
    assert happy["Load_Value"] == "EMAIL=Sample text, then this value=Sample text"
    assert sad["Load_Value"] == "EMAIL=Sample text, then this value=Sample text_DIFFERENT"
    assert sad["Expected Result"].startswith("Constraint fires")


def test_cross_form_relevant_rules_get_shown_and_hidden_cases(cases):
    """`relevant: ${DOB_CF}=''` is a visibility rule, not a constraint."""
    dob = _for(cases, "DOB")
    assert [c["Scenario"].split(":")[0] for c in dob] == ["Shown path", "Hidden path"]
    assert dob[0]["Load_Value"] == "DOB_CF=(blank)"
    assert dob[0]["Expected Result"] == "Field 'Date of Birth' is VISIBLE."
    assert not any("constraint" in c["Expected Result"].lower() for c in dob)


def test_rows_that_write_the_same_item_never_share_a_participant(cases):
    written = {}
    for c in cases:
        for slot in gen._odm_slots(c):
            key = (c["Participant_ID"], slot)
            assert key not in written, (key, c["UAT Case ID"], written[key])
            written[key] = c["UAT Case ID"]
    # rows the loader does not load stay on the first participant, except a multi-step test, which has its own
    for c in cases:
        if not gen._odm_slots(c):
            assert (c["Participant_ID"] == "UAT-P001") == (not c.get("Setup_Steps"))


def test_generator_and_loader_agree_on_what_is_loaded(cases):
    rows = [{k: str(v) for k, v in c.items()} for c in cases]
    for pid, prows in uat_loader._group_by_participant(rows).items():
        xml = uat_loader._build_odm_xml("S_X", "SITE", "SS_1", "P1", prows)
        loaded = sorted(x.split('"')[0] for x in xml.split('<ItemData ItemOID="')[1:])
        expected = sorted(slot[1] for r in prows for slot in gen._odm_slots(r))
        assert loaded == expected, pid
        assert len(loaded) == len(set(loaded)), pid
