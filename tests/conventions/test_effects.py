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


# ─────────────────────────── match (B.1c-2) ───────────────────────────

def test_match_dispatches_to_matched_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"set": {"field.constraint": ". >= 50 and . <= 250"}},
            "WEIGHT": {"set": {"field.constraint": ". >= 2 and . <= 300"}},
        },
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.basic")
    assert f["survey"][0]["constraint"] == ". >= 50 and . <= 250"
    assert len(result.mutations_made) == 1
    assert result.mutations_made[0].directive == "set"


def test_match_falls_through_to_default_when_no_case_matches(make_form, make_field):
    f = make_form(survey=[make_field(name="UNKNOWN_FIELD")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"set": {"field.constraint": "x"}},
        },
        "default": {"flag": {
            "category": "review_flags.no_range_defined",
            "message": "no range for ${field.name}",
        }},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.default")
    # Default block emitted a flag, not a mutation.
    assert "no range for UNKNOWN_FIELD" in spec["review_flags"]["no_range_defined"]
    assert "constraint" not in f["survey"][0]


def test_match_silent_noop_when_no_case_and_no_default(make_form, make_field):
    f = make_form(survey=[make_field(name="UNKNOWN")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.noop")
    assert result.mutations_made == []
    assert result.flags_raised == []
    assert "constraint" not in f["survey"][0]


def test_match_silent_noop_when_on_missing_and_no_default(make_form, make_field):
    """Missing `on` path with no default block: silent no-op."""
    f = make_form(survey=[{"type": "text"}])  # no name field
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.missing_no_default")
    assert result.mutations_made == []
    assert result.flags_raised == []
    assert "constraint" not in f["survey"][0]


def test_match_default_fires_when_on_resolves_to_missing(make_form, make_field):
    """Missing `on` path with default block: default dispatches (same posture as a miss)."""
    f = make_form(survey=[{"type": "text"}])  # no name field
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
        "default": {"set": {"field.constraint": "default-value"}},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.missing_default")
    assert f["survey"][0]["constraint"] == "default-value"


def test_match_case_keys_are_case_sensitive(make_form, make_field):
    f = make_form(survey=[make_field(name="height")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "uppercase-match"}}},
        "default": {"set": {"field.constraint": "default-match"}},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.case_sensitive")
    # field.name="height" should NOT match cases["HEIGHT"]; falls to default.
    assert f["survey"][0]["constraint"] == "default-match"


def test_match_case_block_can_use_any_directive(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {
                "set": {"field.constraint": ". >= 50"},
                "flag": {"category": "review_flags.measurement", "message": "height ok"},
            },
        },
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.multi_directive_case")
    assert f["survey"][0]["constraint"] == ". >= 50"
    assert "height ok" in spec["review_flags"]["measurement"]


def test_match_nested_match_dispatches(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT", type="integer")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"match": {
                "on": "field.type",
                "cases": {
                    "integer": {"set": {"field.constraint": "integer-height"}},
                    "decimal": {"set": {"field.constraint": "decimal-height"}},
                },
            }},
        },
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.nested")
    assert f["survey"][0]["constraint"] == "integer-height"


def test_match_requires_on_and_cases(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="on.*cases"):
        effects.apply_effect({"match": {"on": "field.name"}}, ctx, spec, "test.match.missing_cases")
    with pytest.raises(DSLEvaluationError, match="on.*cases"):
        effects.apply_effect({"match": {"cases": {}}}, ctx, spec, "test.match.missing_on")


def test_match_rejects_non_dict_payload(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="match payload"):
        effects.apply_effect({"match": "not-a-dict"}, ctx, spec, "test.match.bad_payload")


def test_match_rejects_non_dict_cases(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="cases.*dict"):
        effects.apply_effect(
            {"match": {"on": "field.name", "cases": ["not-a-dict"]}},
            ctx, spec, "test.match.bad_cases",
        )


def test_match_rejects_non_dict_case_value(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": "not-a-sub-effect-block"},
    }}
    with pytest.raises(DSLEvaluationError, match="sub-effect-block"):
        effects.apply_effect(eff, ctx, spec, "test.match.bad_case_value")


def test_match_rejects_soft_inside_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"soft": "guidance text"},
        },
    }}
    with pytest.raises(DSLEvaluationError, match="soft"):
        effects.apply_effect(eff, ctx, spec, "test.match.soft_in_case")


def test_match_rejects_unknown_directive_inside_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"frobnicate": {"field.constraint": "x"}},
        },
    }}
    with pytest.raises(DSLEvaluationError, match="Unknown effect directive"):
        effects.apply_effect(eff, ctx, spec, "test.match.unknown_directive")




# ─────────────────────────── match (B.1c-2) ───────────────────────────

def test_match_dispatches_to_matched_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"set": {"field.constraint": ". >= 50 and . <= 250"}},
            "WEIGHT": {"set": {"field.constraint": ". >= 2 and . <= 300"}},
        },
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.basic")
    assert f["survey"][0]["constraint"] == ". >= 50 and . <= 250"
    assert len(result.mutations_made) == 1
    assert result.mutations_made[0].directive == "set"


def test_match_falls_through_to_default_when_no_case_matches(make_form, make_field):
    f = make_form(survey=[make_field(name="UNKNOWN_FIELD")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"set": {"field.constraint": "x"}},
        },
        "default": {"flag": {
            "category": "review_flags.no_range_defined",
            "message": "no range for ${field.name}",
        }},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.default")
    # Default block emitted a flag, not a mutation.
    assert "no range for UNKNOWN_FIELD" in spec["review_flags"]["no_range_defined"]
    assert "constraint" not in f["survey"][0]


def test_match_silent_noop_when_no_case_and_no_default(make_form, make_field):
    f = make_form(survey=[make_field(name="UNKNOWN")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.noop")
    assert result.mutations_made == []
    assert result.flags_raised == []
    assert "constraint" not in f["survey"][0]


def test_match_silent_noop_when_on_missing_and_no_default(make_form, make_field):
    """Missing `on` path with no default block: silent no-op."""
    f = make_form(survey=[{"type": "text"}])  # no name field
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
    }}
    result = effects.apply_effect(eff, ctx, spec, "test.match.missing_no_default")
    assert result.mutations_made == []
    assert result.flags_raised == []
    assert "constraint" not in f["survey"][0]


def test_match_default_fires_when_on_resolves_to_missing(make_form, make_field):
    """Missing `on` path with default block: default dispatches (same posture as a miss)."""
    f = make_form(survey=[{"type": "text"}])  # no name field
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "x"}}},
        "default": {"set": {"field.constraint": "default-value"}},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.missing_default")
    assert f["survey"][0]["constraint"] == "default-value"


def test_match_case_keys_are_case_sensitive(make_form, make_field):
    f = make_form(survey=[make_field(name="height")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": {"set": {"field.constraint": "uppercase-match"}}},
        "default": {"set": {"field.constraint": "default-match"}},
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.case_sensitive")
    # field.name="height" should NOT match cases["HEIGHT"]; falls to default.
    assert f["survey"][0]["constraint"] == "default-match"


def test_match_case_block_can_use_any_directive(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {
                "set": {"field.constraint": ". >= 50"},
                "flag": {"category": "review_flags.measurement", "message": "height ok"},
            },
        },
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.multi_directive_case")
    assert f["survey"][0]["constraint"] == ". >= 50"
    assert "height ok" in spec["review_flags"]["measurement"]


def test_match_nested_match_dispatches(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT", type="integer")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"match": {
                "on": "field.type",
                "cases": {
                    "integer": {"set": {"field.constraint": "integer-height"}},
                    "decimal": {"set": {"field.constraint": "decimal-height"}},
                },
            }},
        },
    }}
    effects.apply_effect(eff, ctx, spec, "test.match.nested")
    assert f["survey"][0]["constraint"] == "integer-height"


def test_match_requires_on_and_cases(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="on.*cases"):
        effects.apply_effect({"match": {"on": "field.name"}}, ctx, spec, "test.match.missing_cases")
    with pytest.raises(DSLEvaluationError, match="on.*cases"):
        effects.apply_effect({"match": {"cases": {}}}, ctx, spec, "test.match.missing_on")


def test_match_rejects_non_dict_payload(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="match payload"):
        effects.apply_effect({"match": "not-a-dict"}, ctx, spec, "test.match.bad_payload")


def test_match_rejects_non_dict_cases(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="cases.*dict"):
        effects.apply_effect(
            {"match": {"on": "field.name", "cases": ["not-a-dict"]}},
            ctx, spec, "test.match.bad_cases",
        )


def test_match_rejects_non_dict_case_value(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {"HEIGHT": "not-a-sub-effect-block"},
    }}
    with pytest.raises(DSLEvaluationError, match="sub-effect-block"):
        effects.apply_effect(eff, ctx, spec, "test.match.bad_case_value")


def test_match_rejects_soft_inside_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"soft": "guidance text"},
        },
    }}
    with pytest.raises(DSLEvaluationError, match="soft"):
        effects.apply_effect(eff, ctx, spec, "test.match.soft_in_case")


def test_match_rejects_unknown_directive_inside_case(make_form, make_field):
    f = make_form(survey=[make_field(name="HEIGHT")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "HEIGHT": {"frobnicate": {"field.constraint": "x"}},
        },
    }}
    with pytest.raises(DSLEvaluationError, match="Unknown effect directive"):
        effects.apply_effect(eff, ctx, spec, "test.match.unknown_directive")


# ─────────────────────────── default_value (B.1c-3) ───────────────────────────

def test_default_value_writes_to_field_default_when_empty(make_form, make_field):
    f = make_form(survey=[make_field(name="AESER")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    result = effects.apply_effect(
        {"default_value": "Y"}, ctx, spec, "test.default_value.basic",
    )
    assert f["survey"][0]["default"] == "Y"
    assert len(result.mutations_made) == 1
    assert result.mutations_made[0].directive == "default_value"
    assert result.mutations_made[0].path == "field.default"
    assert result.mutations_made[0].new_value == "Y"


def test_default_value_is_noop_when_field_default_already_set(make_form, make_field):
    fld = make_field(name="AESER")
    fld["default"] = "N"  # already populated
    f = make_form(survey=[fld])
    spec, ctx = _field_ctx(f["survey"][0], f)
    result = effects.apply_effect(
        {"default_value": "Y"}, ctx, spec, "test.default_value.idempotent",
    )
    assert f["survey"][0]["default"] == "N"  # unchanged
    assert result.mutations_made == []


def test_default_value_overwrites_explicit_empty_string(make_form, make_field):
    fld = make_field(name="AESER")
    fld["default"] = ""  # empty string, treated as missing
    f = make_form(survey=[fld])
    spec, ctx = _field_ctx(f["survey"][0], f)
    effects.apply_effect(
        {"default_value": "Y"}, ctx, spec, "test.default_value.empty_string",
    )
    assert f["survey"][0]["default"] == "Y"


def test_default_value_overwrites_explicit_none(make_form, make_field):
    fld = make_field(name="AESER")
    fld["default"] = None  # explicit None, treated as missing
    f = make_form(survey=[fld])
    spec, ctx = _field_ctx(f["survey"][0], f)
    effects.apply_effect(
        {"default_value": "Y"}, ctx, spec, "test.default_value.none",
    )
    assert f["survey"][0]["default"] == "Y"


def test_default_value_rejects_form_context(make_form):
    f = make_form()
    spec, ctx = _form_ctx(f)
    with pytest.raises(DSLEvaluationError, match="field-scoped"):
        effects.apply_effect(
            {"default_value": "Y"}, ctx, spec, "test.default_value.non_field",
        )


def test_default_value_rejects_empty_payload(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="non-empty"):
        effects.apply_effect(
            {"default_value": ""}, ctx, spec, "test.default_value.empty",
        )


def test_default_value_rejects_none_payload(make_form, make_field):
    f = make_form(survey=[make_field()])
    spec, ctx = _field_ctx(f["survey"][0], f)
    with pytest.raises(DSLEvaluationError, match="non-empty"):
        effects.apply_effect(
            {"default_value": None}, ctx, spec, "test.default_value.none_payload",
        )


def test_default_value_composes_with_match_for_oc7_7f(make_form, make_field):
    # OC-7 7F's AESEV → AESER cascade: match on field name, default_value in the case.
    aesev = make_field(name="AESEV")
    aeser = make_field(name="AESER")
    f = make_form(survey=[aesev, aeser])
    spec, ctx = _field_ctx(aeser, f)
    eff = {"match": {
        "on": "field.name",
        "cases": {
            "AESER": {"default_value": "Y"},
        },
    }}
    effects.apply_effect(eff, ctx, spec, "test.default_value.match_compose")
    assert aeser["default"] == "Y"
    assert "default" not in aesev  # only the matched field gets the default


def test_default_value_can_write_non_string_values(make_form, make_field):
    f = make_form(survey=[make_field(name="X")])
    spec, ctx = _field_ctx(f["survey"][0], f)
    # Integer payload for a numeric default.
    effects.apply_effect(
        {"default_value": 42}, ctx, spec, "test.default_value.integer",
    )
    assert f["survey"][0]["default"] == 42
