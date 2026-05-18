"""
attribution.py — Match diff rows to the conventions that caused them.

Phase C.2 Step 2 — companion to conventions_engine.diff. diff.deep_diff()
returns pure structural changes; this module attributes each change to
the convention application that produced it.

USAGE
─────
    from conventions_engine import diff, attribution

    pre = copy.deepcopy(spec)
    apply_conventions(spec, study_id=..., customer_subdomain=...)
    changes = diff.deep_diff(pre, spec)
    attributed = attribution.attribute_changes(
        changes,
        spec["study_meta"]["conventions_engine_applied"],
    )
    spec["study_meta"]["convention_conflicts"] = attributed

ATTRIBUTION SEMANTICS
─────────────────────
Each diff row has a `field_path` like "forms[3].survey[2].constraint".
The conventions_engine_applied log (built by record.record_application
in Phase C.2 Step 2) has per-mutation paths stored spec-absolute, so
matching is string-equality.

Match rules:
  • For each diff row, scan the applied log for entries whose
    mutations[*].path equals the row's field_path.
  • If a match is found, attach convention_id from that entry.
  • Latest wins: if multiple conventions touched the same path,
    the later entry (last in the log) is the recorded cause —
    that's the convention whose mutation actually produced the
    final post-value seen in the diff.
  • If no match is found, convention_id is None — usually means
    the change came from somewhere other than the conventions
    engine (manual mutation, OC-9 backstop, etc.).

WHAT THIS DOES NOT DO
─────────────────────
  • Does NOT verify the diff row's before/after values match the
    mutation's old/new. A mutation might have run on a different
    starting value (pre-pre-convention) than what the diff sees;
    we attribute on path identity only, which is the right call
    for the Phase C.2 reviewer-facing output ("what convention
    touched this field, regardless of value history").
  • Does NOT handle path renormalisation. If the engine's
    recorded mutation path differs from the diff's field_path
    by something cosmetic (extra/missing brackets, dot vs slash),
    matching fails silently. record.py's _to_absolute_path is
    the canonical translator; both producers must agree on its
    output format.
"""
from __future__ import annotations
from typing import Any, Dict, List


def attribute_changes(diff_rows: List[Dict[str, Any]],
                       applied_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich each diff row with the convention_id of the mutation that
    produced it, when one can be identified.

    Args:
      diff_rows:   Output from diff.deep_diff(). Each row has keys
                   field_path, before_value, after_value.
      applied_log: spec["study_meta"]["conventions_engine_applied"].
                   Each entry has convention_id, applied_to, and (since
                   Phase C.2 Step 2) a mutations list with spec-absolute
                   path strings.

    Returns:
      New list of dicts — same field_path/before_value/after_value as
      the input, plus a convention_id field (str or None). Input rows
      are not mutated; a shallow-copy enrichment is produced.

    Behaviour:
      • Latest wins on path collisions across entries.
      • Entries without a "mutations" key (pre-C.2 records) are skipped
        for index-building — they contribute nothing to attribution.
      • Empty applied_log → every row gets convention_id=None.
      • Empty diff_rows → returns [].

    >>> diff_rows = [{"field_path": "forms[0].visits_assigned",
    ...               "before_value": ["SE_SCREEN"],
    ...               "after_value": ["SE_COMMON"]}]
    >>> applied = [{
    ...     "convention_id": "form_placement.common_visit_safety_admin",
    ...     "mutations": [{
    ...         "path": "forms[0].visits_assigned",
    ...         "directive": "set",
    ...         "old": ["SE_SCREEN"],
    ...         "new": ["SE_COMMON"],
    ...     }],
    ... }]
    >>> r = attribute_changes(diff_rows, applied)
    >>> r[0]["convention_id"]
    'form_placement.common_visit_safety_admin'
    >>> attribute_changes([], [])
    []
    >>> attribute_changes(diff_rows, [])[0]["convention_id"] is None
    True
    """
    # Build path → convention_id index. Iterate the log in order so
    # later entries naturally overwrite earlier — that's the
    # "latest wins" semantic.
    path_to_conv: Dict[str, str] = {}
    for entry in applied_log:
        for mut in entry.get("mutations", []) or []:
            p = mut.get("path")
            if p is not None:
                path_to_conv[p] = entry.get("convention_id")

    # Enrich diff rows (shallow copy to avoid mutating caller's input).
    out: List[Dict[str, Any]] = []
    for row in diff_rows:
        enriched = dict(row)
        enriched["convention_id"] = path_to_conv.get(row.get("field_path"))
        out.append(enriched)
    return out
