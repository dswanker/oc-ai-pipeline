"""Tests for conventions_engine.record."""
from __future__ import annotations

from conventions_engine import record, ApplyResult, Mutation, Flag, Overridden


def test_ensure_section_creates_list():
    spec = {}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_engine_applied"] == []


def test_ensure_section_idempotent():
    spec = {"study_meta": {"conventions_engine_applied": [{"a": 1}]}}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_engine_applied"] == [{"a": 1}]


def test_ensure_section_overwrites_non_list():
    spec = {"study_meta": {"conventions_engine_applied": "wrong type"}}
    record.ensure_section(spec)
    assert spec["study_meta"]["conventions_engine_applied"] == []


def test_record_application_basic(make_convention):
    spec = {}
    c = make_convention()
    result = ApplyResult(mutations_made=[
        Mutation(directive="set", path="form.x", old_value=None, new_value="v"),
    ])
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=result, overrode=[])
    entries = spec["study_meta"]["conventions_engine_applied"]
    assert len(entries) == 1
    assert entries[0]["convention_id"] == c["id"]
    assert entries[0]["applied_to"] == "forms[0]"
    assert "set" in entries[0]["effect_summary"]
    assert "overrode" not in entries[0]
    # Phase C.2: mutations field is always present (may be empty list).
    assert "mutations" in entries[0]
    assert entries[0]["mutations"] == [
        {"path": "forms[0].x", "directive": "set",
         "old": None, "new": "v"},
    ]


def test_record_application_includes_overrode(make_convention):
    spec = {}
    c = make_convention(id="s.x", scope="study", scope_id="P")
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.visits_assigned",
    )]
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    entry = spec["study_meta"]["conventions_engine_applied"][0]
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
    assert "no-op" in spec["study_meta"]["conventions_engine_applied"][0]["effect_summary"]


# ─────────────────── customer_vendor_conflicts bucket (B.1b Patch 3) ───────────────────

def test_ensure_section_creates_customer_vendor_conflicts_bucket():
    spec = {}
    record.ensure_section(spec)
    assert spec["study_meta"]["customer_vendor_conflicts"] == []


def test_record_application_records_customer_over_vendor_conflict(make_convention):
    """F2 sub-decision A: customer winner + vendor in overrode populates the bucket."""
    spec = {}
    customer_conv = make_convention(
        id="c.x", scope="customer", scope_id="acme",
        natural_key="topic_x",
    )
    overrode = [Overridden(
        convention_id="v.x", scope="vendor", kind="structured",
        would_have_done="set form.foo", scope_id="redcap",
    )]
    record.record_application(spec, customer_conv, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    bucket = spec["study_meta"]["customer_vendor_conflicts"]
    assert len(bucket) == 1
    assert bucket[0] == {
        "natural_key": "topic_x",
        "customer_id": "acme",
        "vendor_slug": "redcap",
        "winner": "customer",
        "losing_effect_summary": "set form.foo",
    }


def test_record_application_no_conflict_for_customer_over_global(make_convention):
    """Customer winner + global loser → no customer_vendor_conflicts entry."""
    spec = {}
    customer_conv = make_convention(id="c.x", scope="customer", scope_id="acme",
                                    natural_key="topic_x")
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.foo",
    )]
    record.record_application(spec, customer_conv, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    assert spec["study_meta"]["customer_vendor_conflicts"] == []


def test_record_application_no_conflict_for_vendor_winner(make_convention):
    """Vendor winner over global → no customer_vendor_conflicts entry
    (bucket only fires when CUSTOMER is the winner)."""
    spec = {}
    vendor_conv = make_convention(id="v.x", scope="vendor", scope_id="redcap",
                                  natural_key="topic_x")
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.foo",
    )]
    record.record_application(spec, vendor_conv, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    assert spec["study_meta"]["customer_vendor_conflicts"] == []


def test_record_application_multiple_customer_vendor_conflicts_accumulate(make_convention):
    """Two separate applications, each customer-over-vendor → bucket grows."""
    spec = {}
    for nk, vendor_slug in (("topic_a", "redcap"), ("topic_b", "castor")):
        cust = make_convention(id=f"c.{nk}", scope="customer", scope_id="acme",
                               natural_key=nk)
        ov = [Overridden(
            convention_id=f"v.{nk}", scope="vendor", kind="structured",
            would_have_done=f"set form.{nk}", scope_id=vendor_slug,
        )]
        record.record_application(spec, cust, applied_to="forms[0]",
                                  effects_done=ApplyResult(), overrode=ov)
    bucket = spec["study_meta"]["customer_vendor_conflicts"]
    assert len(bucket) == 2
    assert {e["vendor_slug"] for e in bucket} == {"redcap", "castor"}
    assert {e["natural_key"] for e in bucket} == {"topic_a", "topic_b"}


def test_overrode_serialization_includes_scope_id(make_convention):
    """Existing entry['overrode'] list gains scope_id field for every entry."""
    spec = {}
    c = make_convention(id="s.x", scope="study", scope_id="P",
                        natural_key="topic_x")
    overrode = [Overridden(
        convention_id="g.x", scope="global", kind="structured",
        would_have_done="set form.foo",  # scope_id defaults to "" for global
    )]
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=overrode)
    entry = spec["study_meta"]["conventions_engine_applied"][0]
    assert entry["overrode"][0]["scope_id"] == ""
    assert entry["overrode"][0]["scope"] == "global"


# ─────────────────── per-mutation paths (Phase C.2 Step 2) ───────────────────

def test_to_absolute_path_study_root():
    """study.X writes to spec root → absolute path is just suffix."""
    assert record._to_absolute_path("study.review_flags",
                                     applied_to="", target="study") == "review_flags"


def test_to_absolute_path_same_entity_target():
    """form.X on a form-target convention → applied_to.X."""
    assert record._to_absolute_path("form.visits_assigned",
                                     applied_to="forms[3]", target="form") == "forms[3].visits_assigned"


def test_to_absolute_path_field_target_same_entity():
    """field.X on a field-target convention → applied_to.X."""
    assert record._to_absolute_path("field.constraint",
                                     applied_to="forms[3].survey[2]",
                                     target="field") == "forms[3].survey[2].constraint"


def test_to_absolute_path_field_target_parent_traversal():
    """field-target convention writing form.X → strip last segment of applied_to."""
    assert record._to_absolute_path("form.has_repeating_group",
                                     applied_to="forms[3].survey[2]",
                                     target="field") == "forms[3].has_repeating_group"


def test_to_absolute_path_choice_target_parent_traversal():
    """choice-target convention writing form.X → strip last segment."""
    assert record._to_absolute_path("form.choices",
                                     applied_to="forms[3].choices[7]",
                                     target="choice") == "forms[3].choices"


def test_to_absolute_path_no_prefix_returned_as_is():
    """A path with no dot is returned unchanged (edge case)."""
    assert record._to_absolute_path("nodot",
                                     applied_to="forms[0]", target="form") == "nodot"


def test_extract_mutations_multiple_per_entry(make_convention):
    """A single application with multiple mutations produces multiple rows."""
    result = ApplyResult(mutations_made=[
        Mutation(directive="set", path="form.visits_assigned",
                 old_value=["SE_SCREEN"], new_value=["SE_COMMON"]),
        Mutation(directive="ensure", path="form.style",
                 old_value=None, new_value="theme-grid"),
    ])
    muts = record._extract_mutations(result, applied_to="forms[3]", target="form")
    assert muts == [
        {"path": "forms[3].visits_assigned", "directive": "set",
         "old": ["SE_SCREEN"], "new": ["SE_COMMON"]},
        {"path": "forms[3].style", "directive": "ensure",
         "old": None, "new": "theme-grid"},
    ]


def test_record_application_field_target_mutations_translated_to_absolute(make_convention):
    """End-to-end: a field-target convention with a field.X mutation lands
    in entry['mutations'] with a spec-absolute path."""
    spec = {}
    c = make_convention(target="field")
    result = ApplyResult(mutations_made=[
        Mutation(directive="set", path="field.constraint",
                 old_value=None, new_value=". <= today()"),
    ])
    record.record_application(spec, c, applied_to="forms[1].survey[4]",
                              effects_done=result, overrode=[])
    entry = spec["study_meta"]["conventions_engine_applied"][0]
    assert entry["mutations"] == [
        {"path": "forms[1].survey[4].constraint", "directive": "set",
         "old": None, "new": ". <= today()"},
    ]


def test_record_application_empty_mutations_present_as_empty_list(make_convention):
    """When ApplyResult has no mutations (e.g. advisory or flag-only),
    entry['mutations'] is still present as []."""
    spec = {}
    c = make_convention()
    record.record_application(spec, c, applied_to="forms[0]",
                              effects_done=ApplyResult(), overrode=[])
    entry = spec["study_meta"]["conventions_engine_applied"][0]
    assert entry["mutations"] == []
