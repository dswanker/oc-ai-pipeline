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
