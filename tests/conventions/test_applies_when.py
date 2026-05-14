"""Tests for conventions_engine.applies_when."""
from __future__ import annotations

import pytest

from conventions_engine import EntityContext, DSLEvaluationError
from conventions_engine import applies_when


def _ctx(form, spec=None):
    spec = spec or {"forms": [form]}
    return EntityContext(kind="form", entity=form, parent=spec, spec=spec,
                         path="forms[0]")


def _field_ctx(field, form, spec=None):
    spec = spec or {"forms": [form]}
    return EntityContext(kind="field", entity=field, parent=form, spec=spec,
                         path="forms[0].survey[0]")


# ─────────────────── Bare-value (equals shortcut) ───────────────────

def test_bare_value_equals_match(make_form):
    f = make_form(form_id="DM")
    assert applies_when.evaluate({"form.form_id": "DM"}, _ctx(f)).matched


def test_bare_value_equals_mismatch(make_form):
    f = make_form(form_id="DM")
    assert not applies_when.evaluate({"form.form_id": "AE"}, _ctx(f)).matched


def test_empty_applies_when_always_matches(make_form):
    assert applies_when.evaluate({}, _ctx(make_form())).matched


def test_missing_path_does_not_match(make_form):
    f = make_form()
    assert not applies_when.evaluate({"form.nonexistent_field": "X"}, _ctx(f)).matched


# ─────────────────── Operators ───────────────────

def test_explicit_equals_match(make_form):
    f = make_form(form_id="DM")
    assert applies_when.evaluate({"form.form_id": {"equals": "DM"}}, _ctx(f)).matched


def test_not_equals_match(make_form):
    f = make_form(form_id="DM")
    assert applies_when.evaluate({"form.form_id": {"not_equals": "AE"}}, _ctx(f)).matched


def test_in_match(make_form):
    f = make_form(form_id="AE")
    assert applies_when.evaluate({"form.form_id": {"in": ["AE", "CM", "DV"]}}, _ctx(f)).matched


def test_in_no_match(make_form):
    f = make_form(form_id="DM")
    assert not applies_when.evaluate({"form.form_id": {"in": ["AE", "CM"]}}, _ctx(f)).matched


def test_not_in_match(make_form):
    f = make_form(form_id="DM")
    assert applies_when.evaluate({"form.form_id": {"not_in": ["AE", "CM"]}}, _ctx(f)).matched


def test_matches_regex_match(make_field, make_form):
    field = make_field(name="VSORRES")
    form = make_form(survey=[field])
    assert applies_when.evaluate(
        {"field.name": {"matches": "^VS[A-Z]+$"}},
        _field_ctx(field, form),
    ).matched


def test_matches_regex_no_match(make_field, make_form):
    field = make_field(name="SUBJID")
    form = make_form(survey=[field])
    assert not applies_when.evaluate(
        {"field.name": {"matches": "^VS"}},
        _field_ctx(field, form),
    ).matched


def test_invalid_regex_does_not_match_silently(make_field, make_form):
    """An invalid regex should not crash applies_when; it returns False."""
    field = make_field(name="X")
    form = make_form(survey=[field])
    result = applies_when.evaluate(
        {"field.name": {"matches": "[invalid("}},
        _field_ctx(field, form),
    )
    assert not result.matched


def test_gt_lt_numeric(make_form):
    f = make_form(survey=[{"x": 1}] * 5)
    assert applies_when.evaluate(
        {"form.survey.length": {"gt": 3}}, _ctx(f),
    ).matched
    assert not applies_when.evaluate(
        {"form.survey.length": {"gt": 10}}, _ctx(f),
    ).matched
    assert applies_when.evaluate(
        {"form.survey.length": {"lt": 10}}, _ctx(f),
    ).matched


def test_gte_lte_boundaries(make_form):
    f = make_form(survey=[{"x": 1}] * 5)
    assert applies_when.evaluate(
        {"form.survey.length": {"gte": 5}}, _ctx(f),
    ).matched
    assert applies_when.evaluate(
        {"form.survey.length": {"lte": 5}}, _ctx(f),
    ).matched


def test_empty_operator(make_form):
    f = make_form(visits_assigned=[])
    assert applies_when.evaluate(
        {"form.visits_assigned": {"empty": True}}, _ctx(f),
    ).matched


def test_non_empty_operator(make_form):
    f = make_form(visits_assigned=["SE_X"])
    assert applies_when.evaluate(
        {"form.visits_assigned": {"non_empty": True}}, _ctx(f),
    ).matched


def test_present_operator_distinguishes_from_empty(make_form):
    f = make_form()
    f["maybe_field"] = ""    # present but empty string
    assert applies_when.evaluate(
        {"form.maybe_field": {"present": True}}, _ctx(f),
    ).matched
    assert applies_when.evaluate(
        {"form.maybe_field": {"empty": True}}, _ctx(f),
    ).matched


# ─────────────────── Logical operators ───────────────────

def test_implicit_all_of_top_level(make_form):
    f = make_form(form_id="AE", has_repeating_group=True)
    assert applies_when.evaluate(
        {"form.form_id": "AE", "form.has_repeating_group": True},
        _ctx(f),
    ).matched


def test_implicit_all_of_one_fails(make_form):
    f = make_form(form_id="AE", has_repeating_group=False)
    assert not applies_when.evaluate(
        {"form.form_id": "AE", "form.has_repeating_group": True},
        _ctx(f),
    ).matched


def test_any_of_match(make_form):
    f = make_form(form_id="DM")
    assert applies_when.evaluate(
        {"any_of": [
            {"form.form_id": "AE"},
            {"form.form_id": "DM"},
        ]},
        _ctx(f),
    ).matched


def test_none_of_match(make_form):
    f = make_form(form_id="ICF")
    assert applies_when.evaluate(
        {"none_of": [
            {"form.form_id": "AE"},
            {"form.form_id": "DM"},
        ]},
        _ctx(f),
    ).matched


def test_none_of_blocked(make_form):
    f = make_form(form_id="AE")
    assert not applies_when.evaluate(
        {"none_of": [{"form.form_id": "AE"}]},
        _ctx(f),
    ).matched


# ─────────────────── Soft markers ───────────────────

def test_soft_marker_collected(make_form):
    f = make_form(form_id="DM")
    result = applies_when.evaluate(
        {"form.form_id": "DM", "soft": "extra Claude-judgment criterion"},
        _ctx(f),
    )
    assert result.matched
    assert "extra Claude-judgment criterion" in result.soft_hints


def test_soft_marker_does_not_affect_match(make_form):
    f = make_form(form_id="DM")
    result = applies_when.evaluate(
        {"form.form_id": "AE", "soft": "irrelevant"},
        _ctx(f),
    )
    assert not result.matched
