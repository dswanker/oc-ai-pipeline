"""Tests for conventions_engine.attribution."""
from __future__ import annotations

from conventions_engine import attribution


# ─────────────────── happy path ───────────────────

def test_attribute_changes_single_match():
    """One diff row, one applied entry with a matching mutation → attributed."""
    diff_rows = [
        {"field_path": "forms[0].visits_assigned",
         "before_value": ["SE_SCREEN"],
         "after_value": ["SE_COMMON"]},
    ]
    applied = [{
        "convention_id": "form_placement.common_visit",
        "mutations": [{
            "path": "forms[0].visits_assigned",
            "directive": "set",
            "old": ["SE_SCREEN"],
            "new": ["SE_COMMON"],
        }],
    }]
    out = attribution.attribute_changes(diff_rows, applied)
    assert len(out) == 1
    assert out[0]["convention_id"] == "form_placement.common_visit"
    # Other fields preserved
    assert out[0]["field_path"] == "forms[0].visits_assigned"
    assert out[0]["before_value"] == ["SE_SCREEN"]
    assert out[0]["after_value"] == ["SE_COMMON"]


def test_attribute_changes_no_match_returns_none():
    """Diff row whose path doesn't match any mutation → convention_id None."""
    diff_rows = [
        {"field_path": "forms[2].survey[0].label",
         "before_value": "old", "after_value": "new"},
    ]
    applied = [{
        "convention_id": "x.y",
        "mutations": [{"path": "forms[0].visits_assigned",
                       "directive": "set", "old": None, "new": ["A"]}],
    }]
    out = attribution.attribute_changes(diff_rows, applied)
    assert out[0]["convention_id"] is None


# ─────────────────── latest-wins on path collision ───────────────────

def test_attribute_changes_latest_wins_on_path_collision():
    """When two entries mutate the same path, the later one is the cause
    (its mutation produced the final post-value seen in the diff)."""
    diff_rows = [
        {"field_path": "forms[0].visits_assigned",
         "before_value": ["SE_SCREEN"], "after_value": ["SE_BASELINE"]},
    ]
    applied = [
        {"convention_id": "g.first",
         "mutations": [{"path": "forms[0].visits_assigned",
                        "directive": "set", "old": ["SE_SCREEN"],
                        "new": ["SE_COMMON"]}]},
        {"convention_id": "s.second",
         "mutations": [{"path": "forms[0].visits_assigned",
                        "directive": "set", "old": ["SE_COMMON"],
                        "new": ["SE_BASELINE"]}]},
    ]
    out = attribution.attribute_changes(diff_rows, applied)
    # Later entry "s.second" wins (it produced the final post-value).
    assert out[0]["convention_id"] == "s.second"


# ─────────────────── degenerate inputs ───────────────────

def test_attribute_changes_empty_diff_returns_empty():
    """No diff rows → empty output, regardless of applied log size."""
    applied = [{
        "convention_id": "x.y",
        "mutations": [{"path": "anywhere", "directive": "set",
                       "old": None, "new": 1}],
    }]
    assert attribution.attribute_changes([], applied) == []


def test_attribute_changes_empty_applied_log_every_row_unattributed():
    """No applied entries → every row gets convention_id=None but the diff
    fields survive."""
    diff_rows = [
        {"field_path": "forms[0].x", "before_value": 1, "after_value": 2},
        {"field_path": "forms[1].y", "before_value": "a", "after_value": "b"},
    ]
    out = attribution.attribute_changes(diff_rows, [])
    assert len(out) == 2
    assert all(r["convention_id"] is None for r in out)
    assert out[0]["field_path"] == "forms[0].x"
    assert out[1]["field_path"] == "forms[1].y"


# ─────────────────── shape robustness ───────────────────

def test_attribute_changes_entries_without_mutations_key_skipped():
    """Pre-C.2 applied-log entries that lack the mutations field shouldn't
    crash the index build — they just contribute nothing to attribution."""
    diff_rows = [
        {"field_path": "forms[0].x", "before_value": 1, "after_value": 2},
    ]
    applied = [
        {"convention_id": "legacy.no_mutations"},  # missing mutations key
        {"convention_id": "modern.has_mutations",
         "mutations": [{"path": "forms[0].x", "directive": "set",
                        "old": 1, "new": 2}]},
    ]
    out = attribution.attribute_changes(diff_rows, applied)
    assert out[0]["convention_id"] == "modern.has_mutations"


def test_attribute_changes_input_rows_not_mutated():
    """Caller's diff_rows must not be modified — enrichment is a shallow copy."""
    original = [{"field_path": "forms[0].x", "before_value": 1, "after_value": 2}]
    snapshot = [dict(r) for r in original]
    _ = attribution.attribute_changes(original, [])
    assert original == snapshot
    assert "convention_id" not in original[0]


def test_attribute_changes_multiple_diff_rows_independently_attributed():
    """Each diff row gets its own lookup; one matched + one unmatched coexist."""
    diff_rows = [
        {"field_path": "forms[0].a", "before_value": 1, "after_value": 2},
        {"field_path": "forms[0].b", "before_value": 3, "after_value": 4},
    ]
    applied = [{
        "convention_id": "x.a_only",
        "mutations": [{"path": "forms[0].a", "directive": "set",
                       "old": 1, "new": 2}],
    }]
    out = attribution.attribute_changes(diff_rows, applied)
    assert out[0]["convention_id"] == "x.a_only"
    assert out[1]["convention_id"] is None
