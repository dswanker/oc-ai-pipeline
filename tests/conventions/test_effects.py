"""Tests for conventions_engine.effects."""
from __future__ import annotations

import pytest

from conventions_engine import EntityContext, DSLEvaluationError
from conventions_engine import effects


def _form_ctx(form, spec=None):
    spec = spec or {"forms": [form], "review_flags": {}}
    return spec, EntityContext(
        kind="form", entity=form, parent=spec, spec=spec, path="forms[0]"
    )


def _field_ctx(field, form, spec=None):
    spec = spec or {"forms": [form], "review_flags": {}}
    return spec, EntityContext(
        kind="field", entity=field, parent=form, spec=spec,
        path="forms[0].survey[0]",
    )


# ─────────────────── set ───────────────────

def test_set_overwrites_existing(make_form):
    f = make_form(visits_assigned=["SE_SCREENING"])
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"set": {"form.visits_assigned": ["SE_COMMON"]}}, ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_COMMON"]
    assert len(result.mutations_made) == 1
    assert result.mutations_made[0].directive == "set"


def test_set_creates_missing_intermediate_dict(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"set": {"form.metadata.note": "hello"}}, ctx, spec, "test.id",
    )
    assert f["metadata"] == {"note": "hello"}


def test_set_writes_into_study_context(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"set": {"study.something": "value"}}, ctx, spec, "test.id",
    )
    assert spec["something"] == "value"


# ─────────────────── ensure ───────────────────

def test_ensure_skips_when_present(make_form):
    f = make_form(visits_assigned=["SE_SCREENING"])
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"ensure": {"form.visits_assigned": ["SE_COMMON"]}}, ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_SCREENING"]  # unchanged
    assert result.mutations_made == []


def test_ensure_writes_when_missing(make_form):
    f = make_form()
    del f["has_repeating_group"]
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"ensure": {"form.has_repeating_group": False}}, ctx, spec, "test.id",
    )
    assert f["has_repeating_group"] is False


def test_ensure_writes_when_empty_list(make_form):
    f = make_form(visits_assigned=[])
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"ensure": {"form.visits_assigned": ["SE_BASELINE"]}}, ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_BASELINE"]


def test_ensure_writes_when_empty_string(make_form):
    f = make_form()
    f["notes"] = ""
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"ensure": {"form.notes": "default note"}}, ctx, spec, "test.id",
    )
    assert f["notes"] == "default note"


# ─────────────────── require ───────────────────

def test_require_raises_flag_when_missing(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"require": "form.this_does_not_exist"}, ctx, spec, "test.id",
    )
    assert len(result.flags_raised) == 1
    assert "constraint_review" in spec["review_flags"]


def test_require_no_flag_when_present(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"require": "form.form_id"}, ctx, spec, "test.id",
    )
    assert result.flags_raised == []


def test_require_accepts_list_of_paths(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"require": ["form.form_id", "form.missing_field"]}, ctx, spec, "test.id",
    )
    # Only the missing one should flag
    assert len(result.flags_raised) == 1


# ─────────────────── flag ───────────────────

def test_flag_records_message(make_form):
    f = make_form(form_id="VS")
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"flag": {"category": "review_flags.naming", "message": "VS issue"}},
        ctx, spec, "test.id",
    )
    assert "naming" in spec["review_flags"]
    assert "VS issue" in spec["review_flags"]["naming"]


def test_flag_template_interpolation(make_form):
    f = make_form(form_id="VS", survey=[{"x": 1}] * 250)
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"flag": {
            "category": "review_flags.constraint_review",
            "message": "Form ${form.form_id} has ${form.survey.length} items",
        }}, ctx, spec, "test.id",
    )
    assert "Form VS has 250 items" in spec["review_flags"]["constraint_review"]


def test_flag_template_unresolved_marker_shown(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"flag": {
            "category": "review_flags.x",
            "message": "Missing: ${form.does_not_exist}",
        }}, ctx, spec, "test.id",
    )
    assert "unresolved" in str(spec["review_flags"]).lower()


def test_flag_idempotent_message(make_form):
    f = make_form(form_id="VS")
    spec, ctx = _form_ctx(f)
    for _ in range(3):
        effects.apply_effect(
            {"flag": {"category": "review_flags.x", "message": "same message"}},
            ctx, spec, "test.id",
        )
    # Same message recorded only once
    assert spec["review_flags"]["x"].count("same message") == 1


# ─────────────────── append_to / remove_from ───────────────────

def test_append_to_appends_new_value(make_form):
    f = make_form(visits_assigned=["SE_SCREENING"])
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"append_to": {"form.visits_assigned": "SE_BASELINE"}},
        ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_SCREENING", "SE_BASELINE"]


def test_append_to_idempotent(make_form):
    f = make_form(visits_assigned=["SE_COMMON"])
    spec, ctx = _form_ctx(f)
    for _ in range(3):
        effects.apply_effect(
            {"append_to": {"form.visits_assigned": "SE_COMMON"}},
            ctx, spec, "test.id",
        )
    assert f["visits_assigned"] == ["SE_COMMON"]


def test_remove_from_removes(make_form):
    f = make_form(visits_assigned=["SE_SCREENING", "SE_COMMON"])
    spec, ctx = _form_ctx(f)
    effects.apply_effect(
        {"remove_from": {"form.visits_assigned": "SE_COMMON"}},
        ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_SCREENING"]


def test_remove_from_missing_value_is_noop(make_form):
    f = make_form(visits_assigned=["SE_SCREENING"])
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"remove_from": {"form.visits_assigned": "SE_X"}},
        ctx, spec, "test.id",
    )
    assert f["visits_assigned"] == ["SE_SCREENING"]
    assert result.mutations_made == []


# ─────────────────── soft directives ───────────────────

def test_soft_effect_collected_not_applied(make_form):
    f = make_form(form_id="VS")
    spec, ctx = _form_ctx(f)
    result = effects.apply_effect(
        {"soft": "use CDASH naming"}, ctx, spec, "test.id",
    )
    assert "use CDASH naming" in result.soft_directives
    assert result.mutations_made == []


def test_unknown_directive_raises(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    with pytest.raises(DSLEvaluationError, match="Unknown effect directive"):
        effects.apply_effect(
            {"set_magic": {"form.x": 1}}, ctx, spec, "test.id",
        )
