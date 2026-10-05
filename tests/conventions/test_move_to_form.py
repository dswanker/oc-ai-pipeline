"""Tests for the move_to_form effect directive and its tombstone/sweep
mechanism (conventions_engine.effects._do_move_to_form,
sweep_pending_removals, and the sweep call wired into
apply_conventions in __init__.py).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conventions_engine import EntityContext, DSLEvaluationError, apply_conventions
from conventions_engine import effects


def _field_ctx(field, form, spec):
    return EntityContext(
        kind="field", entity=field, parent=form, spec=spec,
        path="forms[0].survey[0]",
    )


def _spec_two_forms():
    form_a = {
        "form_id": "A",
        "survey": [
            {"type": "select_one yn", "name": "FLD1", "label": "one"},
        ],
        "choices": [
            {"list_name": "yn", "name": "yes", "label": "Yes"},
            {"list_name": "yn", "name": "no", "label": "No"},
        ],
    }
    form_b = {"form_id": "B", "survey": [], "choices": []}
    return {"forms": [form_a, form_b], "review_flags": {}}


# ─────────────────── basic behavior ───────────────────

def test_move_to_form_requires_field_context():
    spec = _spec_two_forms()
    form_a = spec["forms"][0]
    ctx = EntityContext(kind="form", entity=form_a, parent=spec, spec=spec, path="forms[0]")
    with pytest.raises(DSLEvaluationError, match="field-scoped"):
        effects.apply_effect({"move_to_form": "B"}, ctx, spec, "test.id")


def test_move_to_form_rejects_unknown_target():
    spec = _spec_two_forms()
    form_a = spec["forms"][0]
    field = form_a["survey"][0]
    ctx = _field_ctx(field, form_a, spec)
    with pytest.raises(DSLEvaluationError, match="NOPE"):
        effects.apply_effect({"move_to_form": "NOPE"}, ctx, spec, "test.id")


def test_move_to_form_noop_when_already_home():
    spec = _spec_two_forms()
    form_a = spec["forms"][0]
    field = form_a["survey"][0]
    ctx = _field_ctx(field, form_a, spec)
    result = effects.apply_effect({"move_to_form": "A"}, ctx, spec, "test.id")
    assert result.mutations_made == []
    assert form_a["survey"] == [field]


def test_move_to_form_appends_to_target_and_tombstones_source():
    spec = _spec_two_forms()
    form_a, form_b = spec["forms"]
    field = form_a["survey"][0]
    ctx = _field_ctx(field, form_a, spec)
    effects.apply_effect({"move_to_form": "B"}, ctx, spec, "test.id")

    # Not removed from A yet -- that's the sweep's job, not the directive's.
    assert field in form_a["survey"]
    assert field.get(effects._PENDING_REMOVAL_KEY) == "A"

    # Present in B, itemgroup repointed, tombstone key not leaked onto
    # the copy that lives in its new home.
    assert len(form_b["survey"]) == 1
    moved = form_b["survey"][0]
    assert moved["name"] == "FLD1"
    assert moved["bind__oc_itemgroup"] == "B"
    assert effects._PENDING_REMOVAL_KEY not in moved


def test_move_to_form_copies_missing_choices_only():
    spec = _spec_two_forms()
    form_a, form_b = spec["forms"]
    field = form_a["survey"][0]
    ctx = _field_ctx(field, form_a, spec)
    effects.apply_effect({"move_to_form": "B"}, ctx, spec, "test.id")
    assert {c["name"] for c in form_b["choices"]} == {"yes", "no"}

    # Re-running against a form that already has the list shouldn't duplicate.
    form_c = {"form_id": "C", "survey": [], "choices": [
        {"list_name": "yn", "name": "yes", "label": "Yes"},
        {"list_name": "yn", "name": "no", "label": "No"},
    ]}
    spec2 = {"forms": [form_a, form_c], "review_flags": {}}
    field2 = form_a["survey"][0]
    ctx2 = _field_ctx(field2, form_a, spec2)
    effects.apply_effect({"move_to_form": "C"}, ctx2, spec2, "test.id")
    assert len(form_c["choices"]) == 2  # no duplicates added


def test_sweep_pending_removals_strips_tombstoned_rows_only():
    spec = _spec_two_forms()
    form_a = spec["forms"][0]
    field = form_a["survey"][0]
    other = {"type": "text", "name": "OTHER"}
    form_a["survey"].append(other)
    field[effects._PENDING_REMOVAL_KEY] = "A"

    removed = effects.sweep_pending_removals(spec)
    assert removed == 1
    assert form_a["survey"] == [other]


def test_sweep_pending_removals_is_noop_when_nothing_tombstoned():
    spec = _spec_two_forms()
    form_a = spec["forms"][0]
    before = list(form_a["survey"])
    removed = effects.sweep_pending_removals(spec)
    assert removed == 0
    assert form_a["survey"] == before


# ─────────────────── the actual risk this was built for ───────────────────

def test_multiple_matched_fields_in_same_form_all_move_correctly(tmp_path):
    """
    The scenario _do_move_to_form's docstring is worried about: a single
    move_to_form convention whose applies_when matches MULTIPLE fields
    living in the SAME source form. If removal happened immediately
    (naive .remove() during the field-loop's enumerate), the second
    matching field would be skipped because the list shrinks out from
    under the live iteration. This proves that doesn't happen: all
    three matching fields end up in the target form, none silently
    left behind in the source.

    Runs through the real apply_conventions() orchestration (not just
    apply_effect in isolation), since the tombstone-then-sweep timing
    is a property of __init__.py's loop structure, not of the
    directive function alone.
    """
    repo_root = tmp_path
    conv_dir = repo_root / "conventions" / "customers" / "TESTCO"
    conv_dir.mkdir(parents=True)
    real_schema_dir = Path(__file__).resolve().parent.parent.parent / "conventions_engine" / "conventions" / "schema"
    shutil.copytree(real_schema_dir, repo_root / "conventions" / "schema")

    convention = {
        "id": "test.move_many",
        "title": "test",
        "kind": "structured",
        "scope": "customer",
        "scope_id": "TESTCO",
        "status": "active",
        "natural_key": "test_move_many",
        "description": "Test convention: moves every select_one movegroup field to the TARGET form in one pass.",
        "target": "field",
        "applies_when": {"field.type": {"in": ["select_one movegroup"]}},
        "effect": {"move_to_form": "TARGET"},
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "human:test",
        "source": "test",
    }
    (conv_dir / "move_many.json").write_text(json.dumps(convention))

    spec = {
        "forms": [
            {
                "form_id": "SOURCE",
                "survey": [
                    {"type": "select_one movegroup", "name": "F1", "label": "one"},
                    {"type": "select_one movegroup", "name": "F2", "label": "two"},
                    {"type": "text", "name": "UNRELATED", "label": "stays put"},
                    {"type": "select_one movegroup", "name": "F3", "label": "three"},
                ],
                "choices": [
                    {"list_name": "movegroup", "name": "a", "label": "A"},
                ],
            },
            {"form_id": "TARGET", "survey": [], "choices": []},
        ],
        "review_flags": {},
    }

    apply_conventions(spec, study_id="S1", customer_subdomain="TESTCO", repo_root=repo_root)

    source = next(f for f in spec["forms"] if f["form_id"] == "SOURCE")
    target = next(f for f in spec["forms"] if f["form_id"] == "TARGET")

    source_names = {r["name"] for r in source["survey"]}
    target_names = {r["name"] for r in target["survey"]}

    assert source_names == {"UNRELATED"}
    assert target_names == {"F1", "F2", "F3"}
    assert not any(effects._PENDING_REMOVAL_KEY in r for r in target["survey"])
