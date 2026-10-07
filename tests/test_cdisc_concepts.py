"""Unit tests for cdisc_concepts.py (concept tagging). Synthetic CDASHIG rows, scripted Claude response."""
import copy, json
import cdisc_cdash as cd
import cdisc_concepts as cc
from test_cdisc_ct import STD


def _rec(dom, var, typ="Char"):
    return {"domain": dom, "variable": var, "label": var, "question": "", "prompt": "", "instruction": "",
            "core": "HR", "type": typ, "sdtm_target": var, "mapping": ""}


FIELDS = {(d, v): _rec(d, v, t) for d, v, t in [
    ("AE", "AESTDAT", "Char"), ("AE", "AEENDAT", "Char"), ("MH", "MHYN", "Char"), ("MH", "MHOCCUR", "Char"),
    ("IE", "IEORRES", "Char"), ("SU", "SUNCF", "Char"), ("VS", "VSORRES", "Char"), ("DM", "SUBJID", "Char")]}


def setup_module():
    cd._MEMO["fields"] = FIELDS


def teardown_module():
    cd._MEMO.clear()


def _spec():
    return {"forms": [
        {"form_id": "AE", "cdash_domain": "AE", "survey": [
            {"type": "date", "name": "AESTDAT"}, {"type": "date", "name": "ONSET_DT"},
            {"type": "begin group", "name": "G"}, {"type": "calculate", "name": "ICFDAT_CF"}]},
        {"form_id": "MH", "cdash_domain": "MH", "survey": [
            {"type": "select_one yn", "name": "PMHYN"}, {"type": "select_one yn", "name": "SURG"},
            {"type": "select_one yn", "name": "HOSP"}, {"type": "text", "name": "NOTES"}]},
        {"form_id": "IE", "cdash_domain": "IE", "survey": [
            {"type": "select_one yn", "name": "IEINC01"}, {"type": "select_one yn", "name": "IEINC02"},
            {"type": "select_one yn", "name": "IEEXC01"}]},
        {"form_id": "SU", "cdash_domain": "SU", "survey": [
            {"type": "select_one s", "name": "SMOKSTAT"}, {"type": "select_one s", "name": "ALCSTAT"}]},
    ]}


def _rows(spec):
    return {(f["form_id"], r["name"]): r for f in spec["forms"] for r in f["survey"]}


def test_deterministic_aliases_beat_names_and_skip_non_data():
    spec = _spec()
    n = cc.tag_deterministic(spec, STD, {"PMHYN": "MHYN", "AE.ONSET_DT": "AESTDAT", "NOTES": "NOTAVAR"})
    rows = _rows(spec)
    assert (rows[("AE", "AESTDAT")]["concept"], rows[("AE", "AESTDAT")]["concept_source"]) == ("AESTDAT", "cdash_name")
    assert rows[("MH", "PMHYN")]["concept_source"] == "customer_alias"
    assert rows[("AE", "ONSET_DT")]["concept"] == "AESTDAT"
    assert "concept" not in rows[("AE", "ICFDAT_CF")] and "concept" not in rows[("AE", "G")]
    assert n["alias_unknown_concept"] == 1 and "concept" not in rows[("MH", "NOTES")]


def test_claude_tags_are_validated():
    spec = _spec()
    cc.tag_deterministic(spec, STD, {"PMHYN": "MHYN"})
    resp = {"tags": [
        {"form_id": "MH", "field": "PMHYN", "concept": "MHOCCUR", "confidence": "high"},          # already tagged
        {"form_id": "MH", "field": "SURG", "concept": "MHOCCUR", "qualifier": "SURGERY", "confidence": "high"},
        {"form_id": "MH", "field": "HOSP", "concept": "MHOCCUR", "qualifier": "HOSPITAL", "confidence": "medium"},
        {"form_id": "MH", "field": "NOTES", "concept": "MHMADEUP", "confidence": "high"},           # not CDASHIG
        {"form_id": "IE", "field": "IEINC01", "concept": "IEORRES", "qualifier": "INC01", "confidence": "high"},
        {"form_id": "IE", "field": "IEINC02", "concept": "IEORRES", "qualifier": "INCLUSION", "confidence": "high"},
        {"form_id": "IE", "field": "IEEXC01", "concept": "IEORRES", "qualifier": "EXC01", "confidence": "high"},
        {"form_id": "SU", "field": "SMOKSTAT", "concept": "SUNCF", "qualifier": "TOBACCO", "confidence": "high"},
        {"form_id": "SU", "field": "ALCSTAT", "concept": "SUNCF", "qualifier": "TOBACCO", "confidence": "high"},  # duplicate pair
        {"form_id": "AE", "field": "ONSET_DT", "concept": "MHYN", "confidence": "high"},              # date vs non-date
        {"form_id": "XX", "field": "Q", "concept": "AESTDAT", "confidence": "high"},                   # unknown form
    ]}
    res = cc.apply_ai_response(spec, STD, "```json\n" + json.dumps(resp) + "\n```")
    rows = _rows(spec)
    assert rows[("MH", "PMHYN")]["concept_source"] == "customer_alias"
    assert (rows[("MH", "SURG")]["concept"], rows[("MH", "SURG")]["concept_qualifier"]) == ("MHOCCUR", "SURGERY")
    assert "concept" not in rows[("MH", "HOSP")] and "concept" not in rows[("MH", "NOTES")]
    assert [rows[("IE", n)]["concept_qualifier"] for n in ("IEINC01", "IEINC02", "IEEXC01")] == \
        ["INCLUSION", "INCLUSION", "EXCLUSION"]
    assert "concept" not in rows[("SU", "SMOKSTAT")] and "concept" not in rows[("SU", "ALCSTAT")]
    assert "concept" not in rows[("AE", "ONSET_DT")]
    assert res["accepted"] == 4
    assert res["rejected"] == {"already_tagged": 1, "not_high_confidence": 1, "concept_not_in_cdashig": 1,
                               "concept_not_unique_on_form": 2, "type_mismatch": 1, "unknown_form": 1}


def test_garbage_response_changes_nothing():
    spec = _spec(); before = copy.deepcopy(spec)
    assert cc.apply_ai_response(spec, STD, "not json at all")["accepted"] == 0
    assert spec == before


def test_request_lists_only_untagged_data_fields():
    spec = _spec()
    cc.tag_deterministic(spec, STD, {})
    prompt, extra = cc.build_request(spec, STD)
    fields_part = extra.split("FIELDS")[1]
    assert "ONSET_DT" in fields_part and "| AESTDAT |" not in fields_part and "ICFDAT_CF" not in fields_part
