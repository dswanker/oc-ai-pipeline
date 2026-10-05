"""Tests for the assemble_form and insert_after directives (conventions_engine.effects)."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from conventions_engine import EntityContext, DSLEvaluationError, apply_conventions
from conventions_engine import effects


def _row(t, n, **kw):
    r = {"type": t, "name": n, "label": n.title(), "bind__oc_itemgroup": "SF",
         "completion_status": "COMPLETE", "library_source": "PROTOCOL_SPECIFIC"}
    r.update(kw)
    return r


def _spec():
    a = {"form_id": "A", "form_title": "Form A", "cdash_domain": "DM", "visits_assigned": ["E1", "E2"], "reuse_count": 2,
         "settings": {"form_title": "Form A", "form_id": "A", "version": "1", "crossform_references": ""},
         "cross_form_dependencies": [],
         "choices": [{"list_name": "yn", "name": "Y", "label": "Yes"}, {"list_name": "yn", "name": "N", "label": "No"}],
         "survey": [{"type": "begin group", "name": "A_GRP", "appearance": "field-list"},
                    _row("text", "KEEP"),
                    _row("date", "D1", constraint=". <= today()", relevant="${KEEP}='x'"),
                    _row("select_one yn", "Q", appearance="w3"),
                    _row("text", "T1"),
                    {"type": "end group", "name": ""}]}
    b = {"form_id": "B", "form_title": "Form B", "cdash_domain": None, "visits_assigned": ["E1"], "reuse_count": 1,
         "settings": {"form_title": "Form B", "form_id": "B", "version": "1", "crossform_references": ""},
         "cross_form_dependencies": [], "choices": [],
         "survey": [{"type": "begin group", "name": "B_GRP", "appearance": "field-list"},
                    _row("date", "D2", bind__oc_itemgroup="BG"), _row("text", "T2", bind__oc_itemgroup="BG"),
                    {"type": "end group", "name": ""}]}
    return {"study_meta": {"protocol_number": "S"}, "forms": [a, b],
            "timepoint_csv": {"rows": [{"event": "E1"}, {"event": "E2"}, {"event": "SE_COMMON"}]},
            "schedule_of_events": {"form_placements": []}, "review_flags": {}}


def _study_ctx(spec):
    return EntityContext(kind="study", entity=spec, parent=None, spec=spec, path="")


def _run(spec, payload):
    return effects.apply_effect({"assemble_form": payload}, _study_ctx(spec), spec, "test.id")


BASE = {"form_id": "NEWF", "form_title": "New Form", "clone_from": "A", "visits": {"like": "A"},
        "fields": [{"from": "A.D1", "as": "DT", "label": "Date", "also_from": ["B.D2"], "relevant": None},
                   {"from": "A.Q"}]}


def _names(form):
    return [r.get("name") for r in form["survey"]]


# ---------------------------------------------------------------- assemble_form
def test_creates_form_moves_questions_and_records_lineage():
    spec = _spec()
    result = _run(spec, BASE)
    ids = [f["form_id"] for f in spec["forms"]]
    assert ids == ["A", "NEWF", "B"]                                   # placed right after the template
    new = spec["forms"][1]
    assert new["visits_assigned"] == ["E1", "E2"] and new["form_title"] == "New Form"
    assert new["settings"]["form_id"] == "NEWF" and new["settings"]["crossform_references"] == ""
    assert _names(new) == ["NEWF_GRP", "DT", "Q", ""]                   # group, merged date, select, end group
    dt = new["survey"][1]
    assert dt["label"] == "Date" and dt["constraint"] == ". <= today()" and "relevant" not in dt
    assert dt["bind__oc_itemgroup"] == "NEWF" and new["survey"][2]["bind__oc_itemgroup"] == "NEWF"
    assert {(c["list_name"], c["name"]) for c in new["choices"]} == {("yn", "Y"), ("yn", "N")}
    assert "D1" not in _names(spec["forms"][0]) and "Q" not in _names(spec["forms"][0])
    assert "D2" not in _names(spec["forms"][2])
    assert spec["forms"][0]["survey"][1]["name"] == "KEEP" and "T1" in _names(spec["forms"][0])
    lin = spec["study_meta"]["field_lineage"]
    assert {(l["old"], l["new"], l["how"]) for l in lin} == {("A.D1", "NEWF.DT", "merged"), ("B.D2", "NEWF.DT", "merged"), ("A.Q", "NEWF.Q", "moved")}
    assert [p["target_visit_oid"] for p in spec["schedule_of_events"]["form_placements"]] == ["E1", "E2"]
    assert any(m.directive == "assemble_form" for m in result.mutations_made)


def test_second_run_does_nothing():
    spec = _spec()
    _run(spec, BASE)
    snapshot = copy.deepcopy(spec)
    result = _run(spec, BASE)
    assert result.mutations_made == [] and spec == snapshot


@pytest.mark.parametrize("change", [
    {"fields": [{"from": "A.NOPE"}]},                                       # question not in this study
    {"visits": {"events": ["E1", "NOT_AN_EVENT"]}},                          # event not defined
    {"visits": {"like": "ZZ"}},                                             # form for events missing
    {"fields": [{"from": "A.D1", "also_from": ["B.T2"]}]},                   # merging a date into text
])
def test_skips_everything_and_flags_when_anything_is_missing(change):
    spec = _spec()
    before = copy.deepcopy(spec)
    result = _run(spec, {**BASE, **change})
    after = copy.deepcopy(spec)
    flags = after.pop("review_flags")
    before.pop("review_flags")
    assert after == before                                                  # nothing created, nothing moved
    assert flags.get("assemble_form_skipped") and "NEWF" in flags["assemble_form_skipped"][0]
    assert [f.category for f in result.flags_raised] == ["review_flags.assemble_form_skipped"]


def test_no_template_form_means_not_applicable_and_no_flag():
    spec = _spec()
    before = copy.deepcopy(spec)
    result = _run(spec, {**BASE, "clone_from": "ZZ"})
    assert spec == before and result.flags_raised == [] and result.mutations_made == []


def test_relevant_string_replaces_the_rule_and_missing_key_keeps_it():
    spec = _spec()
    _run(spec, {**BASE, "fields": [{"from": "A.D1", "relevant": "${KEEP}='z'"}, {"from": "A.T1"}]})
    new = spec["forms"][1]
    assert new["survey"][1]["relevant"] == "${KEEP}='z'" and "relevant" not in new["survey"][2]
    spec2 = _spec()
    _run(spec2, {**BASE, "fields": [{"from": "A.D1"}]})
    assert spec2["forms"][1]["survey"][1]["relevant"] == "${KEEP}='x'"       # kept when not mentioned


def test_fixed_events_helpers_and_crossform_setting():
    spec = _spec()
    helper = {"type": "calculate", "name": "HELPER_CF", "calculation": "1", "bind__oc_external": "clinicaldata"}
    _run(spec, {**BASE, "visits": {"events": ["SE_COMMON"]}, "helpers": [helper], "required": False})
    new = next(f for f in spec["forms"] if f["form_id"] == "NEWF")
    assert new["visits_assigned"] == ["SE_COMMON"] and new["settings"]["crossform_references"] == "SE_COMMON"
    assert _names(new)[:2] == ["HELPER_CF", "NEWF_GRP"]
    assert spec["schedule_of_events"]["form_placements"] == [
        {"target_visit_oid": "SE_COMMON", "form_id": "NEWF", "required": False, "repeating": False, "notes": ""}]


def test_assemble_form_requires_study_context():
    spec = _spec()
    f = spec["forms"][0]
    ctx = EntityContext(kind="form", entity=f, parent=spec, spec=spec, path="forms[0]")
    with pytest.raises(DSLEvaluationError, match="study-scoped"):
        effects.apply_effect({"assemble_form": BASE}, ctx, spec, "test.id")


# ---------------------------------------------------------------- insert_after
def _field_ctx(spec, fid, name):
    form = next(f for f in spec["forms"] if f["form_id"] == fid)
    field = next(r for r in form["survey"] if r.get("name") == name)
    return EntityContext(kind="field", entity=field, parent=form, spec=spec, path="x")


OTH = {"name": "{self}_OTH", "type": "text", "label": "Other, specify", "relevant": "${{self}}='other'"}


def test_insert_after_places_row_right_after_and_inherits():
    spec = _spec()
    effects.apply_effect({"insert_after": OTH}, _field_ctx(spec, "A", "Q"), spec, "t")
    names = _names(spec["forms"][0])
    assert names[names.index("Q") + 1] == "Q_OTH"
    row = spec["forms"][0]["survey"][names.index("Q_OTH")]
    assert row["relevant"] == "${Q}='other'" and row["appearance"] == "w3" and row["bind__oc_itemgroup"] == "SF"
    assert row["library_source"] == "PROTOCOL_SPECIFIC"


def test_insert_after_is_idempotent_and_field_scoped():
    spec = _spec()
    ctx = _field_ctx(spec, "A", "Q")
    effects.apply_effect({"insert_after": OTH}, ctx, spec, "t")
    n = len(spec["forms"][0]["survey"])
    effects.apply_effect({"insert_after": OTH}, ctx, spec, "t")
    assert len(spec["forms"][0]["survey"]) == n
    with pytest.raises(DSLEvaluationError, match="field-scoped"):
        effects.apply_effect({"insert_after": OTH}, _study_ctx(spec), spec, "t")


# ---------------------------------------------------------------- through the real engine
def _write(root, name, conv):
    (root / "conventions" / "customers" / "TESTCO").mkdir(parents=True, exist_ok=True)
    (root / "conventions" / "customers" / "TESTCO" / name).write_text(json.dumps(conv))


def _conv(natural_key, target, effect, applies_when=None):
    c = {"id": f"test.{natural_key}", "title": f"Test {natural_key}", "kind": "structured", "scope": "customer",
         "scope_id": "TESTCO", "status": "active", "natural_key": natural_key,
         "description": f"Test convention {natural_key} used only by the unit tests of the engine.",
         "target": target, "effect": effect, "created_at": "2026-01-01T00:00:00Z", "created_by": "human:test", "source": "unit test"}
    # The strict schema wants an object for structured conventions; an empty one means "always applies".
    c["applies_when"] = applies_when if applies_when is not None else {}
    return c


def test_both_directives_work_through_apply_conventions(tmp_path):
    shutil.copytree(Path(__file__).resolve().parent.parent.parent / "conventions_engine" / "conventions" / "schema",
                    tmp_path / "conventions" / "schema")
    _write(tmp_path, "assemble.json", _conv("assemble", "study", {"assemble_form": BASE}))
    _write(tmp_path, "insert.json", _conv("insert", "field", {"insert_after": OTH},
                                          {"field.name": "T1"}))
    spec = _spec()
    out = apply_conventions(spec, study_id="S1", customer_subdomain="TESTCO", repo_root=tmp_path)
    assert [f["form_id"] for f in out["forms"]] == ["A", "NEWF", "B"]
    assert "T1_OTH" in _names(out["forms"][0]) and "D1" not in _names(out["forms"][0])
    again = apply_conventions(copy.deepcopy(out), study_id="S1", customer_subdomain="TESTCO", repo_root=tmp_path)
    assert [f["form_id"] for f in again["forms"]] == ["A", "NEWF", "B"]          # second run changes nothing
    assert _names(again["forms"][0]).count("T1_OTH") == 1


def test_inapplicable_form_is_silent_through_apply_conventions(tmp_path):
    shutil.copytree(Path(__file__).resolve().parent.parent.parent / "conventions_engine" / "conventions" / "schema",
                    tmp_path / "conventions" / "schema")
    _write(tmp_path, "assemble.json", _conv("assemble", "study", {"assemble_form": {**BASE, "clone_from": "NOT_HERE"}}))
    out = apply_conventions(_spec(), study_id="S1", customer_subdomain="TESTCO", repo_root=tmp_path)
    assert [f["form_id"] for f in out["forms"]] == ["A", "B"]
    assert not out["review_flags"].get("assemble_form_skipped")           # no template form: not applicable, no flag
