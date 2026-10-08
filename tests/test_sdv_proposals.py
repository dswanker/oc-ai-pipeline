"""Item-level SDV proposals (sdv_proposals.py) and the design-board sdvItems output. Synthetic spec only."""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import sdv_proposals as sd
import study_config as sc

PROTOCOL = ("3.1 Primary endpoint: change from baseline in serum biomarker concentration at Week 8.\n"
            "3.2 Key secondary endpoint: proportion of participants with a grade 3 or higher adverse event.\n")


def _rows(group, *rows):
    return [{"type": t, "name": n, "label": l, "bind__oc_itemgroup": group, **extra} for t, n, l, extra in rows]


def spec():
    return copy.deepcopy({
        "study_meta": {"protocol_number": "SYN-003"},
        "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening"},
                                   {"event": "SE_WEEK8", "timepoint": "Week 8"},
                                   {"event": "SE_COMMON", "timepoint": "Common"}]},
        "forms": [
            {"form_id": "ICF", "form_title": "Informed Consent", "cdash_domain": None, "visits_assigned": ["SE_SCREENING"],
             "settings": {"version": "3"},
             "survey": _rows("IC", ("date", "ICFDAT", "Date of consent", {}), ("text", "ICFVER", "Consent version", {}))},
            {"form_id": "IE", "form_title": "Eligibility", "cdash_domain": "IE", "visits_assigned": ["SE_SCREENING"],
             "survey": _rows("IE", ("select_one NY", "IEYN", "All criteria met?", {}), ("text", "IECOM", "Comment", {}),
                             ("select_one ARM", "ARMCD", "Assigned group", {}))},
            {"form_id": "EX", "form_title": "Study Drug Administration", "cdash_domain": "EX", "visits_assigned": ["SE_WEEK8"],
             "survey": _rows("EX", ("date", "EXSTDAT", "Dose date", {}), ("decimal", "EXDOSE", "Dose", {}),
                             ("text", "EXLOT", "Lot number", {}), ("text", "EXCOM", "Comment", {}))},
            {"form_id": "AE", "form_title": "Adverse Events", "cdash_domain": "AE", "visits_assigned": ["SE_COMMON"],
             "survey": [{"type": "calculate", "name": "AEID_CALC", "calculation": "1 + 1"},
                        {"type": "begin repeat", "name": "AEREP"}]
                       + _rows("AE", ("text", "AETERM", "Adverse event <b>term</b>", {}), ("date", "AESTDAT", "Start", {}),
                               ("select_one NY", "AESER", "Serious?", {}), ("select_one OUT", "AEOUT", "Outcome", {}),
                               ("select_one GR", "AETOXGR", "Grade", {}),
                               ("text", "AEAGE", "Age at onset", {"calculation": "${AGE}", "readonly": "yes"}),
                               ("note", "AENOTE", "Complete one row per event", {}),
                               ("text", "AESECRET", "Internal", {"appearance": "hidden"}))
                       + [{"type": "end repeat", "name": ""}]},
            {"form_id": "DS", "form_title": "Disposition", "cdash_domain": "DS", "visits_assigned": ["SE_WEEK8"],
             "survey": _rows("DS", ("select_one DS", "DSDECOD", "Status", {}), ("date", "DTHDAT", "Date of death", {}),
                             ("text", "DSCOM", "Comment", {}))},
            {"form_id": "LB", "form_title": "Biomarker", "cdash_domain": "LB", "visits_assigned": ["SE_SCREENING", "SE_WEEK8"],
             "survey": _rows("LB", ("decimal", "BMK_LBORRES", "Serum biomarker concentration", {}),
                             ("date", "LBDAT", "Sample date", {}))},
            {"form_id": "CALC", "form_title": "Derived Only", "cdash_domain": None, "visits_assigned": ["SE_WEEK8"],
             "survey": [{"type": "calculate", "name": "X_CALC", "calculation": "1"},
                        {"type": "note", "name": "N1", "label": "Nothing to enter"}]},
        ]})


ANSWER = json.dumps({"endpoints": [
    {"endpoint": "Biomarker change at Week 8", "kind": "primary",
     "quote": "change from baseline in serum biomarker\nconcentration at Week 8", "fields": ["LB.BMK_LBORRES", "LB.NOPE"]},
    {"endpoint": "Grade 3+ adverse events", "kind": "key secondary",
     "quote": "proportion of participants with a grade 3 or higher adverse event", "fields": ["AE.AETOXGR", "AE.AESER"]},
    {"endpoint": "Invented", "kind": "primary", "quote": "overall survival at five years", "fields": ["DS.DSDECOD"]}]})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("STUDY_CONFIG", "STUDY_CONFIG_SDV", "STUDY_CONFIG_SDV_AI"):
        monkeypatch.delenv(k, raising=False)


def _levels(cfg, fid):
    return {i["item"]: i["sdv"] for i in cfg["sdv_items"][fid]["items"]}


def test_critical_to_quality_items_are_required_by_rule():
    s = spec()
    cfg = sc.apply(s, PROTOCOL)
    assert _levels(cfg, "ICF") == {"ICFDAT": "Required", "ICFVER": "Optional"}
    assert _levels(cfg, "IE") == {"IEYN": "Required", "IECOM": "Optional", "ARMCD": "Required"}
    assert _levels(cfg, "EX") == {"EXSTDAT": "Required", "EXDOSE": "Required", "EXLOT": "Required", "EXCOM": "Optional"}
    ae = _levels(cfg, "AE")
    assert (ae["AESTDAT"], ae["AESER"], ae["AEOUT"], ae["AETERM"], ae["AETOXGR"]) == ("Required",) * 3 + ("Optional",) * 2
    assert _levels(cfg, "DS") == {"DSDECOD": "Required", "DTHDAT": "Required", "DSCOM": "Optional"}
    rat = {i["item"]: i["rationale"] for i in cfg["sdv_items"]["DS"]["items"]}
    assert rat["DTHDAT"] == "Critical to quality: death" and rat["DSDECOD"] == "Critical to quality: disposition"
    assert next(i for i in cfg["sdv_items"]["IE"]["items"] if i["item"] == "ARMCD")["rationale"].endswith("group assignment")


def test_calculated_hidden_derived_and_notes_are_not_applicable():
    cfg = sc.apply(spec(), PROTOCOL)
    ae = cfg["sdv_items"]["AE"]
    assert not {"AEID_CALC", "AEAGE", "AENOTE", "AESECRET", "AEREP"} & {i["item"] for i in ae["items"]}
    assert ae["not_applicable"] == 4
    assert cfg["sdv_items"]["CALC"] == {"level": "not_applicable_item_level", "items": [], "not_applicable": 2}
    assert cfg["sdv_items"]["AE"]["level"] == "item_level"


def test_endpoints_are_linked_only_with_a_verified_quote_and_known_fields():
    s = spec()
    v = sd.validate_response(s, ANSWER, PROTOCOL)
    assert [e["endpoint"] for e in v["endpoints"]] == ["Biomarker change at Week 8", "Grade 3+ adverse events"]
    assert v["rejected"] == {"unknown form or field": 1, "quote not found in the protocol": 1}
    cfg = sc.apply(s, PROTOCOL, v)
    lb = {i["item"]: i for i in cfg["sdv_items"]["LB"]["items"]}
    assert lb["BMK_LBORRES"]["sdv"] == "Required" and lb["BMK_LBORRES"]["source"] == "AI-proposed"
    assert "primary endpoint data (Biomarker change at Week 8)" in lb["BMK_LBORRES"]["rationale"] and lb["BMK_LBORRES"]["quote"]
    assert lb["LBDAT"]["sdv"] == "Optional"
    ae = {i["item"]: i for i in cfg["sdv_items"]["AE"]["items"]}
    assert ae["AETOXGR"]["sdv"] == "Required" and ae["AESER"]["source"] == "pipeline default"
    assert "also key secondary endpoint data" in ae["AESER"]["rationale"]
    # a rebuild without a new call keeps the verified links; a DM decision on an item is kept too
    next(i for i in cfg["sdv_items"]["LB"]["items"] if i["item"] == "LBDAT").update(sdv="Required", source="DM")
    again = sc.apply(s, PROTOCOL)
    assert _levels(again, "LB") == {"BMK_LBORRES": "Required", "LBDAT": "Required"}
    assert sd.validate_response(s, "nonsense", PROTOCOL)["endpoints"] == []
    assert sd.build_request(s, "") is None and "LB.BMK_LBORRES | Serum biomarker" in sd.build_request(s, PROTOCOL)[1]
    assert "AE.AEID_CALC" not in sd.build_request(s, PROTOCOL)[1]


def test_cards_and_sections_show_the_required_items_with_rationale():
    s = spec()
    cfg = sc.apply(s, PROTOCOL, sd.validate_response(s, ANSWER, PROTOCOL))
    card = next(f for f in cfg["forms"] if (f["event_oid"], f["form_id"]) == ("SE_WEEK8", "LB"))
    assert card["sdv"]["level"]["value"] == "item_level" and [i["item"] for i in card["sdv"]["items"]] == ["BMK_LBORRES"]
    assert next(f for f in cfg["forms"] if f["form_id"] == "CALC")["sdv"]["level"]["value"] == "not_applicable_item_level"
    assert cfg["sdv_counts"] == {"Required": 13, "Optional": 6, "Not Applicable": 6}
    secs = {t: (h, rows) for t, _n, h, rows, _w in sc.sections(cfg)}
    rows = secs["SDV ITEMS"][1]
    assert ["DS", "DTHDAT", "Date of death", "Required", "Critical to quality: death", "pipeline default"] in rows
    assert any(r[0] == "AE" and r[1] == "(1 other item(s))" and "4 calculated" in r[4] for r in rows)
    assert any(r[1] == "BMK_LBORRES" and r[5].startswith('AI-proposed: "change from baseline') for r in rows)
    form_row = next(r for r in secs["FORMS AT EVENTS"][1] if r[0] == "SE_WEEK8" and r[1] == "LB")
    assert form_row[7] == "item_level" and form_row[8] == "BMK_LBORRES"
    assert "SDV proposals: 13 item(s) Required, 6 Optional, 6 Not Applicable." in sc.summary_line(cfg)


def test_board_json_is_unchanged_unless_the_flag_is_on(monkeypatch, pipeline_board):
    s = spec()
    sc.apply(s, PROTOCOL)
    legacy = pipeline_board(s)
    assert all(c["sdv"] == "required_item_level" and "sdvItems" not in c for c in legacy["cards"])
    monkeypatch.setenv("STUDY_CONFIG_SDV", "1")
    board = pipeline_board(s)
    cards = {(c["formOcoid"], c["listId"]): c for c in board["cards"]}
    ae = next(c for c in board["cards"] if c["formOcoid"] == "F_AE")
    assert ae["sdv"] == "item_level" and ae["itemLevelSdv"] is True
    assert {i["name"]: i["sdv"] for i in ae["sdvItems"]} == {"AETERM": "optional", "AESTDAT": "required", "AESER": "required",
                                                             "AEOUT": "required", "AETOXGR": "optional"}
    item = next(i for i in ae["sdvItems"] if i["name"] == "AESTDAT")
    assert item == {"ocoid": "I_ADVER_AESTDAT", "name": "AESTDAT", "formOID": "F_AE", "versions": ["1"],
                    "itemGroupName": "AE", "itemGroupOid": "IG_ADVER_AE", "itemType": "date", "itemLabel": "Start",
                    "repeating": True, "sdv": "required"}
    assert next(i for i in ae["sdvItems"] if i["name"] == "AETERM")["itemLabel"] == "Adverse event term"
    icf = next(c for c in board["cards"] if c["formOcoid"] == "F_ICF")
    assert icf["sdvItems"][0]["versions"] == ["3"] and icf["sdvItems"][0]["repeating"] is False
    calc = next(c for c in board["cards"] if c["formOcoid"] == "F_CALC")
    assert calc["sdv"] == "not_applicable_item_level" and "sdvItems" not in calc
    assert not any(k in i for c in board["cards"] for i in c.get("sdvItems", []) for k in ("boardId", "listId", "cardId", "_id"))
    assert len(cards) == len(board["cards"])
    # flag on but no proposals in the spec (STUDY_CONFIG=0): legacy card
    plain = pipeline_board(spec())
    assert all(c["sdv"] == "required_item_level" for c in plain["cards"])


@pytest.fixture
def pipeline_board():
    import ast
    src = open(os.path.join(ROOT, "pipeline.py")).read()
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_build_board_json")
    ns = {}
    exec(compile(ast.get_source_segment(src, node), "<pipeline>", "exec"), ns)
    return ns["_build_board_json"]
