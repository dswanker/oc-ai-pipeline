"""
diff.py — Pre/post convention spec diffing for Phase C.2.

Used by pipeline.py's XLS update path (Path X.1) to detect what
apply_conventions() changed when the engine runs on a user-edited
Study Spec XLSX.

USAGE PATTERN
─────────────
    import copy
    from conventions_engine import apply_conventions, diff

    pre = copy.deepcopy(spec)
    apply_conventions(spec, study_id=..., customer_subdomain=...)
    changes = diff.deep_diff(pre, spec)

    spec["study_meta"]["convention_conflicts"] = changes

SEMANTICS — "CONFLICTS" IS LOOSE TERMINOLOGY
────────────────────────────────────────────
The output field is named `convention_conflicts` by Phase C.2
convention, but a two-snapshot pre/post diff captures EVERY engine
mutation on the user's spec — not just mutations that override user
intent.

True conflict detection needs THREE snapshots:
    (1) Original system-generated baseline
    (2) User-edited XLSX upload
    (3) Post-convention spec

A real conflict = field where (1) != (2) AND (2) != (3) — i.e. the
user edited it AND the engine then changed it. Without (1), we can't
distinguish "user deliberately edited this field" from "user left it
as the system emitted it"; every engine mutation on (3) shows up in
the diff regardless of whether (2) was a user edit.

For Phase C.2 this is acceptable — reviewers see every engine
mutation on their uploaded spec and can judge each. Three-way diff
is a possible Phase C.3+ enhancement when needed.

LIST ORDER
──────────
Lists are diffed positionally — element i in `before` compared to
element i in `after`. If the user reorders survey rows in their
XLSX upload, the diff shows every row as changed even when content
is identical (because before[i] != after[i] after the reorder).
Key-aware matching (e.g., by `name` field) is a possible enhancement
if reorderings become common in practice.

NO CONVENTION ATTRIBUTION HERE
──────────────────────────────
This module returns pure structural diffs — `field_path`,
`before_value`, `after_value` only. Attribution to specific
conventions (the `convention_id` field that consumers may want on
each row) requires reading
`spec["study_meta"]["conventions_engine_applied"]` and matching
mutation paths back to convention ids. That's the job of a separate
`attribute_changes()` function added in Phase C.2 Step 2 (alongside
the record.py enhancement that captures per-mutation paths). Pure
structural diffing here keeps this module engine-independent and
unit-testable in isolation.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List


def deep_diff(before: Any, after: Any) -> List[Dict[str, Any]]:
    """Compute leaf-level differences between two spec values.

    Returns a list of change rows. Each row has three keys:
        field_path    — dotted/bracketed path (e.g. "forms[0].survey[5].required")
        before_value  — value in the pre-convention spec (None if added)
        after_value   — value in the post-convention spec (None if removed)

    Recursion rules:
      - dict vs dict: recurse into shared keys; keys present in only
        one side become add/remove rows with the missing side as None.
      - list vs list: recurse positionally on each index; extra
        elements in either side become add/remove rows.
      - dict vs list (or any other type mismatch): treated as a leaf
        change — one row with the values as-is.
      - scalar vs scalar: equality check; emit one row if different.
      - both None / both equal: no row emitted.

    Order of rows: dicts iterate keys in sorted order; lists iterate
    by index. Deterministic across runs for stable test diffing.

    >>> deep_diff({"a": 1}, {"a": 1})
    []
    >>> deep_diff({"a": 1}, {"a": 2})
    [{'field_path': 'a', 'before_value': 1, 'after_value': 2}]
    >>> deep_diff({}, {"new_field": "hi"})
    [{'field_path': 'new_field', 'before_value': None, 'after_value': 'hi'}]
    >>> deep_diff({"old": "bye"}, {})
    [{'field_path': 'old', 'before_value': 'bye', 'after_value': None}]
    >>> deep_diff({"forms": [{"survey": [{"required": "no"}]}]}, {"forms": [{"survey": [{"required": "yes"}]}]})
    [{'field_path': 'forms[0].survey[0].required', 'before_value': 'no', 'after_value': 'yes'}]
    """
    changes: List[Dict[str, Any]] = []
    _walk(before, after, "", changes)
    return changes


def _walk(before: Any, after: Any, path: str, changes: List[Dict[str, Any]]) -> None:
    """Recursive walker. Appends to `changes` in place."""
    # Both equal (including both None, both empty dict/list) — nothing to record.
    if before == after:
        return

    # Both dicts → recurse into shared keys; emit add/remove for unique keys.
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after)):
            child_path = f"{path}.{k}" if path else k
            b_has = k in before
            a_has = k in after
            if b_has and a_has:
                _walk(before[k], after[k], child_path, changes)
            elif b_has:
                changes.append({
                    "field_path": child_path,
                    "before_value": before[k],
                    "after_value": None,
                })
            else:
                changes.append({
                    "field_path": child_path,
                    "before_value": None,
                    "after_value": after[k],
                })
        return

    # Both lists → positional recurse; emit add/remove for length mismatch.
    if isinstance(before, list) and isinstance(after, list):
        for i in range(max(len(before), len(after))):
            child_path = f"{path}[{i}]"
            b_has = i < len(before)
            a_has = i < len(after)
            if b_has and a_has:
                _walk(before[i], after[i], child_path, changes)
            elif b_has:
                changes.append({
                    "field_path": child_path,
                    "before_value": before[i],
                    "after_value": None,
                })
            else:
                changes.append({
                    "field_path": child_path,
                    "before_value": None,
                    "after_value": after[i],
                })
        return

    # Type mismatch (e.g. dict→list, list→scalar) OR scalar difference.
    # Record as a leaf change at this path. `path` is "" only when the
    # caller passed non-dict inputs directly at the top level — the spec
    # is always a dict in production so this is an edge case for direct
    # diff() callers.
    changes.append({
        "field_path": path,
        "before_value": before,
        "after_value": after,
    })


# ─────────────── Phase C.4: three-way conflict filter helpers ───────────────

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_SENTINEL_MISSING = object()


def _resolve_path(spec: Any, field_path: str) -> Any:
    """Walk `spec` by a spec-absolute path like 'forms[3].survey[2].required'
    and return the value at that location.

    Returns _SENTINEL_MISSING if any step doesn't exist (key absent in a
    dict, index out of range in a list, attempted traversal through a
    scalar, or spec itself is None). Caller converts to a render-friendly
    value (typically None) when surfacing to users.

    >>> _resolve_path({"a": 1}, "a")
    1
    >>> _resolve_path({"forms": [{"x": "v"}]}, "forms[0].x")
    'v'
    >>> _resolve_path({"forms": [{"x": "v"}]}, "forms[0].y") is _SENTINEL_MISSING
    True
    >>> _resolve_path({"a": 1}, "a.b") is _SENTINEL_MISSING
    True
    >>> _resolve_path({"forms": []}, "forms[0]") is _SENTINEL_MISSING
    True
    """
    if spec is None:
        return _SENTINEL_MISSING
    current: Any = spec
    for match in _PATH_TOKEN.finditer(field_path):
        key, idx = match.group(1), match.group(2)
        if key is not None:
            if not isinstance(current, dict) or key not in current:
                return _SENTINEL_MISSING
            current = current[key]
        else:
            i = int(idx)
            if not isinstance(current, list) or i >= len(current):
                return _SENTINEL_MISSING
            current = current[i]
    return current


def filter_to_user_intersected(engine_changes: List[Dict[str, Any]],
                                user_change_paths,
                                baseline_spec: Any) -> List[Dict[str, Any]]:
    """Filter engine_changes to rows the user also touched, enriching each
    with the baseline value looked up from baseline_spec.

    Phase C.4 — true conflict detection. A conflict requires both:
      - the engine modified the field (it's in engine_changes), AND
      - the user modified the field (its path is in user_change_paths).

    Args:
      engine_changes:     output of deep_diff(user_edit, post_convention),
                          already enriched with convention_id by
                          attribute_changes (Phase C.2). Each row has:
                            field_path, before_value (=user_value),
                            after_value (=engine_value), convention_id
      user_change_paths:  set-like collection of paths the user touched
                          (typically computed via
                          {r['field_path'] for r in
                           deep_diff(baseline, user_edit)})
      baseline_spec:      the pre-user-edit spec, used to look up
                          baseline_value for each retained row. MUST be
                          non-None; caller decides whether to use the
                          two-way Phase C.2 fallback when no baseline
                          is available.

    Returns:
      List of conflict rows with the Phase C.4 5-key schema:
        field_path, baseline_value, user_value, engine_value, convention_id
      baseline_value is None when the baseline spec doesn't have the
      path (defensive — happens when the user added a brand-new field
      that didn't exist in the baseline).

    >>> filter_to_user_intersected(
    ...     engine_changes=[{"field_path": "a", "before_value": "u",
    ...                      "after_value": "e", "convention_id": "c1"}],
    ...     user_change_paths={"a"},
    ...     baseline_spec={"a": "b"})
    [{'field_path': 'a', 'baseline_value': 'b', 'user_value': 'u', 'engine_value': 'e', 'convention_id': 'c1'}]
    >>> filter_to_user_intersected(
    ...     engine_changes=[{"field_path": "a", "before_value": "u",
    ...                      "after_value": "e", "convention_id": "c1"}],
    ...     user_change_paths=set(),
    ...     baseline_spec={"a": "b"})
    []
    >>> filter_to_user_intersected(
    ...     engine_changes=[{"field_path": "new", "before_value": "u",
    ...                      "after_value": "e", "convention_id": None}],
    ...     user_change_paths={"new"},
    ...     baseline_spec={})
    [{'field_path': 'new', 'baseline_value': None, 'user_value': 'u', 'engine_value': 'e', 'convention_id': None}]
    """
    paths = set(user_change_paths)
    out: List[Dict[str, Any]] = []
    for row in engine_changes:
        path = row.get("field_path")
        if path not in paths:
            continue
        baseline_val = _resolve_path(baseline_spec, path)
        if baseline_val is _SENTINEL_MISSING:
            baseline_val = None
        out.append({
            "field_path":     path,
            "baseline_value": baseline_val,
            "user_value":     row.get("before_value"),
            "engine_value":   row.get("after_value"),
            "convention_id":  row.get("convention_id"),
        })
    return out
