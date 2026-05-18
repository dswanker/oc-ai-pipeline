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
