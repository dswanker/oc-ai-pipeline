"""Tests for conventions_engine.cascade."""
from __future__ import annotations

from conventions_engine import cascade


def test_resolve_empty_returns_empty():
    out = cascade.resolve({"global": [], "customer": [], "study": [], "errors": []})
    assert out == []


def test_resolve_single_global_passes_through(make_convention):
    c = make_convention()
    out = cascade.resolve({"global": [c], "customer": [], "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == c["id"]
    assert out[0].overrode == []


def test_resolve_customer_overrides_global(make_convention):
    g = make_convention(id="g.same_topic", scope="global", natural_key="topic_x")
    c = make_convention(id="c.same_topic", scope="customer", scope_id="cust1",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [g], "customer": [c], "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "c.same_topic"
    assert len(out[0].overrode) == 1
    assert out[0].overrode[0].convention_id == "g.same_topic"


def test_resolve_study_overrides_customer_and_global(make_convention):
    g = make_convention(id="g.x", scope="global", natural_key="topic_x")
    c = make_convention(id="c.x", scope="customer", scope_id="cust1",
                        natural_key="topic_x")
    s = make_convention(id="s.x", scope="study", scope_id="PROTO-1",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [g], "customer": [c], "study": [s], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "s.x"
    # Both lower-precedence conventions should be in overrode
    overridden_ids = {o.convention_id for o in out[0].overrode}
    assert overridden_ids == {"g.x", "c.x"}


def test_resolve_different_natural_keys_all_pass(make_convention):
    a = make_convention(id="a", natural_key="key_a")
    b = make_convention(id="b", natural_key="key_b")
    c = make_convention(id="c", natural_key="key_c")
    out = cascade.resolve({"global": [a, b, c], "customer": [], "study": [], "errors": []})
    ids = {r.convention["id"] for r in out}
    assert ids == {"a", "b", "c"}
    # No overrides for any of them
    for r in out:
        assert r.overrode == []


def test_resolve_advisory_can_override_structured(make_convention):
    """A study-scope advisory should mask a global structured rule."""
    g = make_convention(id="g.structured", scope="global", kind="structured",
                        natural_key="x")
    s = make_convention(id="s.advisory", scope="study", scope_id="P1",
                        kind="advisory", natural_key="x")
    out = cascade.resolve({"global": [g], "customer": [], "study": [s], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "s.advisory"
    assert out[0].overrode[0].convention_id == "g.structured"


def test_resolve_orders_by_scope_then_id(make_convention):
    a = make_convention(id="a.global", scope="global", natural_key="k1")
    b = make_convention(id="b.study", scope="study", scope_id="P",
                        natural_key="k2")
    out = cascade.resolve({"global": [a], "customer": [], "study": [b], "errors": []})
    # Global before study in deterministic ordering
    assert out[0].convention["scope"] == "global"
    assert out[1].convention["scope"] == "study"


def test_resolve_conventions_without_natural_key_pass_through(make_convention):
    c = make_convention()
    del c["natural_key"]
    out = cascade.resolve({"global": [c], "customer": [], "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention is c


def test_overrode_summary_describes_effect(make_convention):
    g = make_convention(id="g.x", scope="global", natural_key="k",
                        effect={"set": {"form.visits_assigned": ["SE_COMMON"]}})
    s = make_convention(id="s.x", scope="study", scope_id="P", natural_key="k")
    out = cascade.resolve({"global": [g], "customer": [], "study": [s], "errors": []})
    summary = out[0].overrode[0].would_have_done
    assert "set" in summary or "form.visits_assigned" in summary


def test_overrode_advisory_summary_includes_description(make_convention):
    g = make_convention(id="g.x", scope="global", kind="advisory",
                        natural_key="k",
                        description="A clear advisory description here.")
    s = make_convention(id="s.x", scope="study", scope_id="P", natural_key="k")
    out = cascade.resolve({"global": [g], "customer": [], "study": [s], "errors": []})
    summary = out[0].overrode[0].would_have_done
    assert "advisory" in summary.lower()


# ─────────────────── vendor peer-axis (B.1b Patch 2) ───────────────────

def test_resolve_vendor_overrides_global(make_convention):
    g = make_convention(id="g.x", scope="global", natural_key="topic_x")
    v = make_convention(id="v.x", scope="vendor", scope_id="redcap",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [g], "vendor": [v], "customer": [],
                           "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "v.x"
    assert out[0].convention["scope"] == "vendor"
    assert len(out[0].overrode) == 1
    assert out[0].overrode[0].convention_id == "g.x"
    assert out[0].overrode[0].scope == "global"


def test_resolve_customer_overrides_vendor(make_convention):
    """F2 sub-decision A: customer wins on natural_key collision with vendor."""
    v = make_convention(id="v.x", scope="vendor", scope_id="redcap",
                        natural_key="topic_x")
    c = make_convention(id="c.x", scope="customer", scope_id="cust1",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [], "vendor": [v], "customer": [c],
                           "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "c.x"
    assert out[0].convention["scope"] == "customer"
    assert len(out[0].overrode) == 1
    assert out[0].overrode[0].convention_id == "v.x"
    assert out[0].overrode[0].scope == "vendor"


def test_resolve_study_overrides_vendor_and_customer_and_global(make_convention):
    g = make_convention(id="g.x", scope="global", natural_key="topic_x")
    v = make_convention(id="v.x", scope="vendor", scope_id="redcap",
                        natural_key="topic_x")
    c = make_convention(id="c.x", scope="customer", scope_id="cust1",
                        natural_key="topic_x")
    s = make_convention(id="s.x", scope="study", scope_id="PROTO-1",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [g], "vendor": [v], "customer": [c],
                           "study": [s], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "s.x"
    overridden_ids = {o.convention_id for o in out[0].overrode}
    assert overridden_ids == {"g.x", "v.x", "c.x"}


def test_resolve_vendor_alone_passes_through(make_convention):
    v = make_convention(id="v.x", scope="vendor", scope_id="redcap",
                        natural_key="topic_x")
    out = cascade.resolve({"global": [], "vendor": [v], "customer": [],
                           "study": [], "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "v.x"
    assert out[0].convention["scope"] == "vendor"
    assert out[0].overrode == []


def test_resolve_vendor_and_customer_peer_ordering_in_output(make_convention):
    """Two non-colliding rules: both appear at scope_order=1, sorted by id."""
    v = make_convention(id="v.bravo", scope="vendor", scope_id="redcap",
                        natural_key="key_v")
    c = make_convention(id="c.alpha", scope="customer", scope_id="cust1",
                        natural_key="key_c")
    out = cascade.resolve({"global": [], "vendor": [v], "customer": [c],
                           "study": [], "errors": []})
    assert len(out) == 2
    # id-alphabetical tiebreak at scope_order=1 → "c.alpha" before "v.bravo".
    assert out[0].convention["id"] == "c.alpha"
    assert out[1].convention["id"] == "v.bravo"
    assert out[0].convention["scope"] == "customer"
    assert out[1].convention["scope"] == "vendor"


def test_resolve_no_vendor_key_in_loaded_treated_as_empty(make_convention):
    """Legacy 3-scope loaded dict (no 'vendor' key) doesn't crash."""
    g = make_convention(id="g.x", scope="global", natural_key="topic_x")
    out = cascade.resolve({"global": [g], "customer": [], "study": [],
                           "errors": []})
    assert len(out) == 1
    assert out[0].convention["id"] == "g.x"
