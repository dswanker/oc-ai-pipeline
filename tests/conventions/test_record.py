"""Tests for conventions_engine.record."""
from __future__ import annotations

from conventions_engine import record, ApplyResult, Mutation, Flag, Overridden


def test_ensure_section_creates_list():
    spec = {}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_applied"] == []


def test_ensure_section_idempotent():
    spec = {"study_meta": {"conventions_applied": [{"a": 1}]}}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_applied"] == [{"a": 1}]


def test_ensure_section_overwrites_non_list():
    spec = {"study_meta": {"conventions_applied": "wrong type"}}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_applied"] == []


def test_record_application_basic(make_convention):
    spec = {}
    c = make_convention()
    result = ApplyResult(mutations_made=[
        Mutation(directive="set", path="form.x", old_value=None, new_value="v"),
    ])
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=result, overrode=[])
    entries = spec["study_meta"]["conventions_applied"]
    assert len(entries) == 1
    assert entries[0]["convention_id"] == c["id"]
    assert entries[0]["applied_to"] == "forms[0]"
    assert "set" in entries[0]["effect_summary"]
    assert "overrode" not in entries[0]


def test_record_application_includes_overrode(make_convention):
    spec = {}
    c = make_convention(id="s.x", scope="study", scope_id="P")
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.visits_assigned",
    )]
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    entry = spec["study_meta"]["conventions_applied"][0]
    assert entry["overrode"][0]["convention_id"] == "g.x"


def test_record_application_no_op_message_when_empty():
    spec = {}
    from conventions_engine import ApplyResult
    record.record_application(
        spec, {"id": "x", "scope": "global", "kind": "structured"},
        applied_to="forms[0]",
        effects_done=ApplyResult(),
        overrode=[],
    )
    assert "no-op" in spec["study_meta"]["conventions_applied"][0]["effect_summary"]
