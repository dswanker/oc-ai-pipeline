"""Unit tests for cdisc_cdash.py (CDASH field-definition layer). Synthetic CDASHIG rows, no member file."""
import copy
import cdisc_ct as c
import cdisc_cdash as cd
from test_cdisc_ct import STD


def _rec(dom, var, instr="", prompt="", core="HR", target=None):
    return {"domain": dom, "variable": var, "label": var, "question": "", "prompt": prompt,
            "instruction": instr, "core": core, "type": "Char", "sdtm_target": target or var}


FIELDS = {
    ("AE", "AESER"): _rec("AE", "AESER", "Assess if the adverse event should be classified as serious."),
    ("AE", "AETERM"): _rec("AE", "AETERM", "Record only 1 diagnosis per line.", prompt="Adverse Event"),
    ("AE", "AEOUT"): _rec("AE", "AEOUT", "If collected on the CRF, the sponsor provides instructions."),
    ("AE", "AEONGO"): _rec("AE", "AEONGO", 'Record as ongoing ("Y") if not ended.'),
    ("AE", "AESTDAT"): _rec("AE", "AESTDAT", "Record the start date using this format (DD-MON-YYYY)."),
    ("DM", "SUBJID"): _rec("DM", "SUBJID", "Record the identifier for the subject."),
    ("CM", "CMTRT"): _rec("CM", "CMTRT", "Record the [medication/treatment] name."),
}


def _spec():
    return {"forms": [{"form_id": "AE", "cdash_domain": "AE", "choices": [], "survey": [
        {"type": "text", "name": "AETERM", "label": ""},
        {"type": "select_one ny", "name": "AESER", "label": "Serious?"},
        {"type": "select_one out", "name": "AEOUT", "label": "Outcome"},
        {"type": "select_one ny", "name": "AEONGO", "label": "Ongoing"},
        {"type": "date", "name": "AESTDAT", "label": "Start"},
        {"type": "text", "name": "SUBJID", "label": "Subject"},
        {"type": "text", "name": "CMTRT", "label": "Med"},
        {"type": "text", "name": "CUSTOM1", "label": "Sponsor field"},
        {"type": "select_one ny", "name": "AESER2", "label": "x"},
    ]}]}


def setup_module():
    cd._MEMO["fields"] = FIELDS


def teardown_module():
    cd._MEMO.clear()


def test_hints_labels_and_filters():
    spec = _spec()
    summ = cd.apply_to_spec(spec, STD)
    rows = {r["name"]: r for r in spec["forms"][0]["survey"]}
    assert rows["AESER"]["hint"].startswith("Assess if")            # site-facing instruction
    assert rows["AETERM"]["label"] == "Adverse Event"                # empty label -> CDASHIG prompt
    assert "hint" not in rows["AEOUT"]                               # sponsor/designer note
    assert "hint" not in rows["AEONGO"]                              # cites raw code ("Y")
    assert "hint" not in rows["AESTDAT"]                             # date-format instruction
    assert "hint" not in rows["SUBJID"]                              # identifier OC4 captures
    assert "hint" not in rows["CMTRT"]                               # placeholder text
    assert "cdash" not in rows["CUSTOM1"]                            # not a CDASH variable
    assert rows["AESER"]["cdash"]["sdtm_target"] == "AESER" and rows["CMTRT"]["cdash"]["domain"] == "CM"
    assert summ["hints_added"] == 2 and summ["labels_added"] == 1


def test_protected_fields_get_metadata_only():
    spec = _spec()
    cd.apply_to_spec(spec, STD, protected_vars={"AESER", "AETERM"})
    rows = {r["name"]: r for r in spec["forms"][0]["survey"]}
    assert "hint" not in rows["AESER"] and rows["AETERM"]["label"] == ""
    assert rows["AESER"]["cdash"]["variable"] == "AESER"


def test_existing_hint_and_label_win_and_idempotent():
    spec = _spec()
    spec["forms"][0]["survey"][1]["hint"] = "Protocol-specific help"
    cd.apply_to_spec(spec, STD)
    assert spec["forms"][0]["survey"][1]["hint"] == "Protocol-specific help"
    twice = copy.deepcopy(spec)
    cd.apply_to_spec(twice, STD)
    assert twice == spec
