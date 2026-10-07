"""Tests for engine bindings ("as"), template filters, study.has_field and add_constraint."""
from __future__ import annotations
import copy
import pytest
from conventions_engine import EntityContext, DSLEvaluationError
from conventions_engine import applies_when, effects
from conventions_engine.applies_when import render


def _spec():
    return {"forms": [
        {"form_id": "AE", "survey": [
            {"type": "date", "name": "AESTDAT", "bind__oc_itemgroup": "AE"},
            {"type": "date", "name": "AEENDAT", "bind__oc_itemgroup": "AE",
             "constraint": ". <= today()", "constraint_message": "Cannot be in the future."},
            {"type": "date", "name": "CMENDAT", "bind__oc_itemgroup": "AE"},
        ]},
        {"form_id": "ICF", "visits_assigned": ["SE_SCREENING"], "survey": [
            {"type": "date", "name": "ICFDAT", "bind__oc_itemgroup": "ICF"},
        ]},
    ]}


def _fctx(spec, form_i, field_i):
    form = spec["forms"][form_i]
    return EntityContext(kind="field", entity=form["survey"][field_i], parent=form, spec=spec,
                         path=f"forms[{form_i}].survey[{field_i}]")


# ── templates ──────────────────────────────────────────────────────────

def test_render_filters_and_leaves_xlsform_refs():
    spec = _spec(); ctx = _fctx(spec, 0, 1)
    assert render("${field.name|replace:ENDAT:STDAT}", ctx) == "AESTDAT"
    assert render("${field.name|strip_suffix:ENDAT}", ctx) == "AE"
    assert render(". >= ${AESTDAT}", ctx) == ". >= ${AESTDAT}"          # XLSForm ref untouched
    assert render("${field.nope}", ctx) == "${field.nope}"
    assert render("${field.nope}", ctx, strict=True) == "<unresolved:field.nope>"
    with pytest.raises(DSLEvaluationError):
        render("${field.name|bogus}", ctx)


# ── bindings ───────────────────────────────────────────────────────────

def test_has_sibling_binds_templated_partner():
    spec = _spec()
    aw = {"field.name": {"matches": "^.*ENDAT$"},
          "field.has_sibling": {"where": {"field.name": "${field.name|replace:ENDAT:STDAT}"}, "as": "start"}}
    ctx = _fctx(spec, 0, 1)
    assert applies_when.evaluate(aw, ctx).matched
    assert ctx.bindings["start"]["name"] == "AESTDAT" and ctx.bindings["start"]["_form_id"] == "AE"
    assert not applies_when.evaluate(aw, _fctx(spec, 0, 2)).matched   # CMENDAT has no CMSTDAT


def test_study_has_field_finds_other_form():
    spec = _spec()
    aw = {"field.name": "AESTDAT", "study.has_field": {"where": {"field.name": "ICFDAT"}, "as": "icf"}}
    ctx = _fctx(spec, 0, 0)
    assert applies_when.evaluate(aw, ctx).matched
    assert ctx.bindings["icf"]["_form_id"] == "ICF"
    own = {"field.name": "AESTDAT", "study.has_field": {"where": {"field.name": "AEENDAT"}}}
    assert not applies_when.evaluate(own, _fctx(spec, 0, 0)).matched   # own form skipped by default
    own["study.has_field"]["other_forms"] = False
    assert applies_when.evaluate(own, _fctx(spec, 0, 0)).matched


def test_reserved_binding_name_rejected():
    spec = _spec()
    with pytest.raises(DSLEvaluationError):
        applies_when.evaluate({"field.has_sibling": {"where": {"field.name": "AESTDAT"}, "as": "field"}},
                              _fctx(spec, 0, 1))


# ── add_constraint ─────────────────────────────────────────────────────

def _apply(effect, ctx):
    return effects.apply_effect(effect, ctx, ctx.spec, "test.conv")


def test_add_constraint_ands_with_existing_and_is_idempotent():
    spec = _spec(); ctx = _fctx(spec, 0, 1)
    applies_when.evaluate({"field.has_sibling": {"where": {"field.name": "AESTDAT"}, "as": "start"}}, ctx)
    eff = {"add_constraint": {"expr": ". = '' or ${${start.name}} = '' or . >= ${${start.name}}",
                              "message": "End date must be on or after the start date.",
                              "check_id": "CDISC.DATE_ORDER"}}
    _apply(eff, ctx)
    row = spec["forms"][0]["survey"][1]
    assert row["constraint"] == "(. <= today()) and (. = '' or ${AESTDAT} = '' or . >= ${AESTDAT})"
    assert row["constraint_message"] == "Cannot be in the future. End date must be on or after the start date."
    assert row["edit_checks"] == ["CDISC.DATE_ORDER"]
    before = copy.deepcopy(spec)
    _apply(eff, ctx)
    assert spec == before


def test_add_constraint_unresolved_is_flagged_not_written():
    spec = _spec(); ctx = _fctx(spec, 0, 2)
    res = _apply({"add_constraint": {"expr": ". >= ${${start.name}}"}}, ctx)
    assert "constraint" not in spec["forms"][0]["survey"][2]
