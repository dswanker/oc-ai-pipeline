"""Tests for use_vocabulary, lookup_from and the new insert_after options (conventions_engine.effects)."""
from __future__ import annotations

import copy

import pytest

from conventions_engine import EntityContext, DSLEvaluationError
from conventions_engine import effects


def _row(t, n, **kw):
    r = {"type": t, "name": n, "label": n.title(), "bind__oc_itemgroup": "G", "completion_status": "COMPLETE", "library_source": "PROTOCOL_SPECIFIC"}
    r.update(kw)
    return r


def _spec(with_src=True):
    src = {"form_id": "SRC", "form_title": "Source form", "visits_assigned": ["E1", "E2"], "settings": {"crossform_references": ""},
           "survey": [{"type": "begin group", "name": "S_GRP"}, _row("date", "DOB", bind__oc_itemgroup="SF"), {"type": "end group", "name": ""}], "choices": []}
    tgt = {"form_id": "TGT", "form_title": "Target", "visits_assigned": ["E3"], "settings": {"crossform_references": ""}, "cross_form_dependencies": [],
           "survey": [{"type": "begin group", "name": "T_GRP"}, _row("text", "A"), _row("date", "DOB", label="Date of birth", appearance="w2"),
                      _row("text", "B", relevant="${A}='x'"), {"type": "end group", "name": ""}], "choices": []}
    return {"forms": [src, tgt] if with_src else [tgt], "review_flags": {}}


def _ctx(spec, fid, name):
    form = next(f for f in spec["forms"] if f["form_id"] == fid)
    field = next(r for r in form["survey"] if r.get("name") == name)
    return EntityContext(kind="field", entity=field, parent=form, spec=spec, path="x")


def _run(spec, directive, payload, fid="TGT", name="DOB"):
    return effects.apply_effect({directive: payload}, _ctx(spec, fid, name), spec, "t")


def _names(spec, fid="TGT"):
    return [r.get("name") for r in next(f for f in spec["forms"] if f["form_id"] == fid)["survey"]]


# ------------------------------------------------------------------ use_vocabulary
@pytest.fixture
def vocab_dir(tmp_path, monkeypatch):
    (tmp_path / "rx.csv").write_text("name,label\nOTHER,Other (not in list)\n5640,Ibuprofen\n")
    monkeypatch.setattr(effects, "_VOCAB_DIR", tmp_path)
    return tmp_path


def test_use_vocabulary_converts_registers_and_is_idempotent(vocab_dir):
    spec = _spec()
    _run(spec, "use_vocabulary", {"file": "rx.csv"}, name="A")
    a = next(r for r in spec["forms"][1]["survey"] if r["name"] == "A")
    assert a["type"] == "select_one_from_file rx.csv" and a["appearance"] == "minimal autocomplete"
    assert "5640,Ibuprofen" in spec["_omop_vocab_files"]["rx.csv"]
    snap = copy.deepcopy(spec)
    assert _run(spec, "use_vocabulary", {"file": "rx.csv"}, name="A").mutations_made == [] and spec == snap


def test_use_vocabulary_multi_uses_minimal(vocab_dir):
    spec = _spec()
    _run(spec, "use_vocabulary", {"file": "rx.csv", "multi": True}, name="A")
    a = next(r for r in spec["forms"][1]["survey"] if r["name"] == "A")
    assert a["type"] == "select_multiple_from_file rx.csv" and a["appearance"] == "minimal"


def test_use_vocabulary_missing_file_flags_and_changes_nothing(vocab_dir):
    spec = _spec(); before = copy.deepcopy(spec)
    result = _run(spec, "use_vocabulary", {"file": "ghost.csv"}, name="A")
    assert [f.category for f in result.flags_raised] == ["review_flags.vocabulary_missing"]
    spec_cmp, before_cmp = copy.deepcopy(spec), before
    spec_cmp.pop("review_flags"), before_cmp.pop("review_flags")
    assert spec_cmp == before_cmp


def test_use_vocabulary_is_field_scoped(vocab_dir):
    spec = _spec()
    with pytest.raises(DSLEvaluationError, match="field-scoped"):
        effects.apply_effect({"use_vocabulary": {"file": "rx.csv"}}, EntityContext(kind="study", entity=spec, parent=None, spec=spec, path=""), spec, "t")


# ------------------------------------------------------------------ lookup_from
def test_hidden_lookup_adds_helper_after_the_field_and_documents_it():
    spec = _spec()
    _run(spec, "lookup_from", {"from": "SRC.{self}"})
    assert _names(spec)[:4] == ["T_GRP", "A", "DOB", "DOB_CF"]
    h = next(r for r in spec["forms"][1]["survey"] if r["name"] == "DOB_CF")
    assert h["type"] == "calculate" and h["bind__oc_external"] == "clinicaldata"
    assert "F_SRC" in h["calculation"] and "ItemGroupName='SF'" in h["calculation"] and "ItemName='DOB'" in h["calculation"]
    assert "StudyEventOID" not in h["calculation"]                          # any event
    tgt = spec["forms"][1]
    assert tgt["settings"]["crossform_references"] == "E1,E2"
    assert tgt["cross_form_dependencies"][0]["target_field"] == "DOB_CF"
    dob = next(r for r in tgt["survey"] if r["name"] == "DOB")
    assert "relevant" not in dob                                            # hidden mode leaves the question alone


def test_autofill_hides_the_original_only_while_the_lookup_is_blank_and_mirrors_the_value():
    spec = _spec()
    _run(spec, "lookup_from", {"from": "SRC.{self}", "mode": "autofill", "from_label": "Source form"})
    tgt = spec["forms"][1]; rows = {r["name"]: r for r in tgt["survey"]}
    assert rows["DOB"]["relevant"] == "${DOB_CF}=''"
    m = rows["DOB_SF"]
    assert m["readonly"] == "yes" and m["calculation"] == "${DOB_CF}" and m["relevant"] == "${DOB_CF}!=''"
    assert m["label"] == "Date of birth (Source form)" and m["appearance"] == "w2" and m["bind__oc_itemgroup"] == "G"
    assert _names(spec)[2:5] == ["DOB", "DOB_CF", "DOB_SF"]


def test_autofill_keeps_an_existing_show_hide_rule():
    spec = _spec()
    _run(spec, "lookup_from", {"from": "SRC.DOB", "mode": "autofill", "name": "B_CF"}, name="B")
    b = next(r for r in spec["forms"][1]["survey"] if r["name"] == "B")
    assert b["relevant"] == "(${A}='x') and ${B_CF}=''"


def test_lookup_is_idempotent_and_quiet_when_the_source_form_is_absent():
    spec = _spec()
    _run(spec, "lookup_from", {"from": "SRC.{self}", "mode": "autofill"})
    snap = copy.deepcopy(spec)
    assert _run(spec, "lookup_from", {"from": "SRC.{self}", "mode": "autofill"}).mutations_made == [] and spec == snap
    none = _spec(with_src=False); before = copy.deepcopy(none)
    result = _run(none, "lookup_from", {"from": "SRC.{self}", "mode": "autofill"})
    assert none == before and result.flags_raised == []                      # not applicable: no change, no flag


def test_lookup_flags_when_form_exists_but_question_does_not():
    spec = _spec()
    result = _run(spec, "lookup_from", {"from": "SRC.NOPE"})
    assert [f.category for f in result.flags_raised] == ["review_flags.lookup_skipped"] and "DOB_CF" not in _names(spec)


def test_explicit_item_group_and_event_need_no_source_form():
    spec = _spec(with_src=False)
    _run(spec, "lookup_from", {"from": "DD.DTHDAT", "name": "DTHDAT_CF", "item_group": "DD", "event": "SE_COMMON"})
    h = next(r for r in spec["forms"][0]["survey"] if r["name"] == "DTHDAT_CF")
    assert "[@StudyEventOID='SE_COMMON']" in h["calculation"] and "F_DD" in h["calculation"] and "ItemGroupName='DD'" in h["calculation"]
    assert spec["forms"][0]["settings"]["crossform_references"] == "SE_COMMON"


# ------------------------------------------------------------------ insert_after options
def test_insert_after_can_inherit_the_parent_rule_and_register_a_vocabulary(vocab_dir):
    spec = _spec()
    _run(spec, "insert_after", {"name": "{self}_U", "type": "select_one_from_file rx.csv", "label": "Unit",
                                "inherit_relevant": True, "register_vocab": "rx.csv"}, name="B")
    u = next(r for r in spec["forms"][1]["survey"] if r["name"] == "B_U")
    assert u["relevant"] == "${A}='x'" and "inherit_relevant" not in u and "register_vocab" not in u
    assert "rx.csv" in spec["_omop_vocab_files"]
    spec2 = _spec()
    result = _run(spec2, "insert_after", {"name": "B_U", "type": "text", "register_vocab": "ghost.csv"}, name="B")
    assert "B_U" not in _names(spec2) and [f.category for f in result.flags_raised] == ["review_flags.vocabulary_missing"]
