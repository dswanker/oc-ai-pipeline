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


# ───────────────────────── form.has_field (B.1c-1) ─────────────────────────

def test_has_field_matches_when_form_contains_named_field(make_form, make_field):
    f = make_form(survey=[
        make_field(name="HEIGHT", type="integer"),
        make_field(name="WEIGHT", type="integer"),
    ])
    aw = {"form.has_field": {"where": {"field.name": "HEIGHT"}}}
    assert applies_when.evaluate(aw, _ctx(f)).matched


def test_has_field_misses_when_no_field_matches(make_form, make_field):
    f = make_form(survey=[make_field(name="AGE", type="integer")])
    aw = {"form.has_field": {"where": {"field.name": "HEIGHT"}}}
    assert not applies_when.evaluate(aw, _ctx(f)).matched


def test_has_field_with_compound_where(make_form, make_field):
    f = make_form(survey=[
        make_field(name="HEIGHT", type="integer"),
        make_field(name="WEIGHT", type="text"),  # wrong type
    ])
    # Where clause requires both name match AND integer type.
    aw = {"form.has_field": {"where": {
        "all_of": [
            {"field.name": "WEIGHT"},
            {"field.type": "integer"},
        ]
    }}}
    assert not applies_when.evaluate(aw, _ctx(f)).matched


def test_has_field_with_regex_where(make_form, make_field):
    f = make_form(survey=[
        make_field(name="AESTDAT", type="date"),
        make_field(name="AEENDAT", type="date"),
    ])
    aw = {"form.has_field": {"where": {"field.name": {"matches": "^.*STDAT$"}}}}
    assert applies_when.evaluate(aw, _ctx(f)).matched


def test_has_field_works_from_field_context(make_form, make_field):
    f = make_form(survey=[
        make_field(name="HEIGHT", type="integer"),
        make_field(name="WEIGHT", type="integer"),
    ])
    # The convention's target=field; the current field is HEIGHT; the form
    # still gets walked for the has_field probe.
    aw = {"form.has_field": {"where": {"field.name": "WEIGHT"}}}
    assert applies_when.evaluate(aw, _field_ctx(f["survey"][0], f)).matched


def test_has_field_empty_survey_returns_false(make_form):
    f = make_form(survey=[])
    aw = {"form.has_field": {"where": {"field.name": "ANY"}}}
    assert not applies_when.evaluate(aw, _ctx(f)).matched


def test_has_field_requires_where(make_form):
    f = make_form()
    with pytest.raises(DSLEvaluationError, match="where"):
        applies_when.evaluate({"form.has_field": {}}, _ctx(f))


def test_has_field_rejects_non_form_context(make_form):
    spec = {"events": [{"event_oid": "SE_X"}]}
    ctx = EntityContext(kind="event", entity=spec["events"][0], parent=spec,
                        spec=spec, path="events[0]")
    aw = {"form.has_field": {"where": {"field.name": "X"}}}
    with pytest.raises(DSLEvaluationError, match="form.has_field"):
        applies_when.evaluate(aw, ctx)


def test_has_field_inner_soft_hints_do_not_leak(make_form, make_field):
    f = make_form(survey=[make_field(name="X")])
    aw = {"form.has_field": {"where": {
        "field.name": "X",
        "soft": "this hint must NOT leak to the outer evaluator",
    }}}
    result = applies_when.evaluate(aw, _ctx(f))
    assert result.matched
    assert "this hint must NOT leak to the outer evaluator" not in result.soft_hints


def test_has_field_outer_soft_hints_preserved(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    aw = {
        "form.has_field": {"where": {"field.name": "HEIGHT"}},
        "soft": "outer hint should be kept",
    }
    result = applies_when.evaluate(aw, _ctx(f))
    assert result.matched
    assert "outer hint should be kept" in result.soft_hints


# ───────────────────── field.has_sibling (B.1c-1) ────────────────────────

def test_has_sibling_finds_other_field_in_same_form(make_form, make_field):
    stdat = make_field(name="AESTDAT", type="date", bind__oc_itemgroup="AE")
    endat = make_field(name="AEENDAT", type="date", bind__oc_itemgroup="AE")
    f = make_form(survey=[stdat, endat])
    aw = {"field.has_sibling": {"where": {"field.name": {"matches": "^.*ENDAT$"}}}}
    assert applies_when.evaluate(aw, _field_ctx(stdat, f)).matched


def test_has_sibling_does_not_match_self(make_form, make_field):
    only_stdat = make_field(name="AESTDAT", type="date")
    f = make_form(survey=[only_stdat])
    aw = {"field.has_sibling": {"where": {"field.name": "AESTDAT"}}}
    # Only AESTDAT exists; if self-exclusion is correct, no sibling matches.
    assert not applies_when.evaluate(aw, _field_ctx(only_stdat, f)).matched


def test_has_sibling_default_scope_is_same_form(make_form, make_field):
    stdat = make_field(name="AESTDAT", bind__oc_itemgroup="AE")
    endat = make_field(name="AEENDAT", bind__oc_itemgroup="DM")  # different ig
    f = make_form(survey=[stdat, endat])
    # No scope given — defaults to same_form, which includes different itemgroups.
    aw = {"field.has_sibling": {"where": {"field.name": "AEENDAT"}}}
    assert applies_when.evaluate(aw, _field_ctx(stdat, f)).matched


def test_has_sibling_same_itemgroup_excludes_other_itemgroups(make_form, make_field):
    stdat = make_field(name="AESTDAT", bind__oc_itemgroup="AE")
    endat_wrong = make_field(name="AEENDAT", bind__oc_itemgroup="DM")  # different ig
    f = make_form(survey=[stdat, endat_wrong])
    aw = {"field.has_sibling": {
        "where": {"field.name": "AEENDAT"},
        "scope": "same_itemgroup",
    }}
    assert not applies_when.evaluate(aw, _field_ctx(stdat, f)).matched


def test_has_sibling_same_itemgroup_includes_matching_itemgroup(make_form, make_field):
    stdat = make_field(name="AESTDAT", bind__oc_itemgroup="AE")
    endat = make_field(name="AEENDAT", bind__oc_itemgroup="AE")
    f = make_form(survey=[stdat, endat])
    aw = {"field.has_sibling": {
        "where": {"field.name": "AEENDAT"},
        "scope": "same_itemgroup",
    }}
    assert applies_when.evaluate(aw, _field_ctx(stdat, f)).matched


def test_has_sibling_same_itemgroup_skips_when_self_has_no_itemgroup(make_form, make_field):
    # If the current field has no itemgroup, same_itemgroup cannot match anything.
    self_field = make_field(name="HEADER", bind__oc_itemgroup="")
    other = make_field(name="AEENDAT", bind__oc_itemgroup="AE")
    f = make_form(survey=[self_field, other])
    aw = {"field.has_sibling": {
        "where": {"field.name": "AEENDAT"},
        "scope": "same_itemgroup",
    }}
    assert not applies_when.evaluate(aw, _field_ctx(self_field, f)).matched


def test_has_sibling_unknown_scope_raises(make_form, make_field):
    fld = make_field()
    f = make_form(survey=[fld])
    aw = {"field.has_sibling": {
        "where": {"field.name": "X"},
        "scope": "same_universe",
    }}
    with pytest.raises(DSLEvaluationError, match="scope"):
        applies_when.evaluate(aw, _field_ctx(fld, f))


def test_has_sibling_rejects_non_field_context(make_form):
    f = make_form()
    aw = {"field.has_sibling": {"where": {"field.name": "X"}}}
    with pytest.raises(DSLEvaluationError, match="field.has_sibling"):
        applies_when.evaluate(aw, _ctx(f))


def test_has_sibling_inner_soft_hints_do_not_leak(make_form, make_field):
    stdat = make_field(name="AESTDAT")
    endat = make_field(name="AEENDAT")
    f = make_form(survey=[stdat, endat])
    aw = {"field.has_sibling": {"where": {
        "field.name": "AEENDAT",
        "soft": "should not leak",
    }}}
    result = applies_when.evaluate(aw, _field_ctx(stdat, f))
    assert result.matched
    assert "should not leak" not in result.soft_hints
