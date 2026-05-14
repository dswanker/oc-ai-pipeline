"""Tests for conventions_engine.intersection."""
from __future__ import annotations

from conventions_engine import intersection


def test_empty_blocks_intersect():
    assert intersection.intersects({}, {})


def test_one_empty_block_intersects():
    assert intersection.intersects({}, {"form.form_id": "DM"})
    assert intersection.intersects({"form.form_id": "DM"}, {})


def test_equals_equals_same_value():
    assert intersection.intersects(
        {"form.form_id": "DM"}, {"form.form_id": "DM"},
    )


def test_equals_equals_different_values():
    assert not intersection.intersects(
        {"form.form_id": "DM"}, {"form.form_id": "AE"},
    )


def test_equals_in_overlap():
    assert intersection.intersects(
        {"form.form_id": "AE"},
        {"form.form_id": {"in": ["AE", "CM"]}},
    )


def test_equals_in_no_overlap():
    assert not intersection.intersects(
        {"form.form_id": "ICF"},
        {"form.form_id": {"in": ["AE", "CM"]}},
    )


def test_in_in_overlap():
    assert intersection.intersects(
        {"form.form_id": {"in": ["AE", "CM"]}},
        {"form.form_id": {"in": ["CM", "DV"]}},
    )


def test_in_in_no_overlap():
    assert not intersection.intersects(
        {"form.form_id": {"in": ["AE", "CM"]}},
        {"form.form_id": {"in": ["VS", "DM"]}},
    )


def test_in_not_in_overlap():
    assert intersection.intersects(
        {"form.form_id": {"in": ["AE", "CM"]}},
        {"form.form_id": {"not_in": ["AE"]}},
    )


def test_in_not_in_no_overlap_when_in_subset_of_not_in():
    assert not intersection.intersects(
        {"form.form_id": {"in": ["AE", "CM"]}},
        {"form.form_id": {"not_in": ["AE", "CM"]}},
    )


def test_empty_non_empty_never_intersect():
    assert not intersection.intersects(
        {"form.visits_assigned": {"empty": True}},
        {"form.visits_assigned": {"non_empty": True}},
    )


def test_regex_conservative_true():
    """Two regexes admit unknown overlap; we return True."""
    assert intersection.intersects(
        {"field.name": {"matches": "^A"}},
        {"field.name": {"matches": "^B"}},
    )


def test_different_paths_do_not_constrain_each_other():
    """Different paths can both be satisfied by different facets of the entity."""
    assert intersection.intersects(
        {"form.form_id": "DM"},
        {"form.has_repeating_group": True},
    )


def test_numeric_intervals_overlap():
    assert intersection.intersects(
        {"form.survey.length": {"gt": 100}},
        {"form.survey.length": {"lt": 300}},
    )


def test_numeric_intervals_no_overlap():
    assert not intersection.intersects(
        {"form.survey.length": {"gt": 300}},
        {"form.survey.length": {"lt": 100}},
    )


# ─────────────────── detect_conflict ───────────────────

def test_detect_conflict_no_conflict(make_convention):
    a = make_convention(id="a", natural_key="key_a")
    b = make_convention(id="b", natural_key="key_b")
    report = intersection.detect_conflict(a, [b])
    assert not report.has_conflict


def test_detect_conflict_natural_key(make_convention):
    a = make_convention(id="a", natural_key="key_x")
    b = make_convention(id="b", natural_key="key_x")
    report = intersection.detect_conflict(a, [b])
    assert report.has_conflict
    assert len(report.natural_key_conflicts) == 1


def test_detect_conflict_natural_key_only_same_scope(make_convention):
    a = make_convention(id="a", scope="global", natural_key="k")
    b = make_convention(id="b", scope="study", scope_id="P", natural_key="k")
    report = intersection.detect_conflict(a, [b])
    assert not report.has_conflict


def test_detect_conflict_semantic_overlap_disagree(make_convention):
    a = make_convention(
        id="a", scope="global", natural_key="diff_key_1",
        kind="structured",
        applies_when={"form.form_id": "DM"},
        effect={"set": {"form.visits_assigned": ["SE_SCREENING"]}},
    )
    b = make_convention(
        id="b", scope="global", natural_key="diff_key_2",
        kind="structured",
        applies_when={"form.form_id": "DM"},
        effect={"set": {"form.visits_assigned": ["SE_BASELINE"]}},
    )
    report = intersection.detect_conflict(a, [b])
    assert report.has_conflict
    assert len(report.semantic_conflicts) == 1


def test_detect_conflict_semantic_skipped_for_advisory(make_convention):
    a = make_convention(id="a", kind="advisory", natural_key="diff_a")
    b = make_convention(id="b", kind="structured", natural_key="diff_b",
                        applies_when={"form.form_id": "DM"},
                        effect={"set": {"form.visits_assigned": ["X"]}})
    report = intersection.detect_conflict(a, [b])
    assert not report.has_conflict


def test_detect_conflict_archived_ignored(make_convention):
    a = make_convention(id="a", natural_key="k")
    b = make_convention(id="b", natural_key="k", status="archived")
    report = intersection.detect_conflict(a, [b])
    assert not report.has_conflict


def test_detect_conflict_self_ignored(make_convention):
    c = make_convention(id="self", natural_key="k")
    report = intersection.detect_conflict(c, [c])
    assert not report.has_conflict
