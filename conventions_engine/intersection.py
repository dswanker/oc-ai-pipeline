"""
Conflict detection between structured conventions at promotion time.

Conservative: when we can't prove non-intersection, we return True.
False positives (annoy a reviewer) are far better than false negatives
(let contradictory conventions silently coexist).

Not applied to hybrid or advisory conventions (their behavior is too
unstructured to compare mechanically). Natural-key conflict detection
still applies to all kinds.
"""
from __future__ import annotations
from typing import Any, Dict, List

from . import ConflictReport


# ──────────────────────────────────────────────────────────────────────
# Pairwise intersection of single conditions
# ──────────────────────────────────────────────────────────────────────

def _to_operator_dict(expr: Any) -> Dict[str, Any]:
    """Bare value → {'equals': value}."""
    if isinstance(expr, dict):
        return dict(expr)
    return {"equals": expr}


def _ops_intersect(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Do these two operator dicts (on the same path) admit any value
    in common? Conservative: True when uncertain.
    """
    # Special-case empty/non_empty/present pairs first
    a_empty = a.get("empty"); a_non = a.get("non_empty"); a_present = a.get("present")
    b_empty = b.get("empty"); b_non = b.get("non_empty"); b_present = b.get("present")

    if a_empty is True and b_non is True:
        return False
    if a_non is True and b_empty is True:
        return False
    if a_empty is True and b_empty is True:
        return True
    if a_non is True and b_non is True:
        return True  # both require non-empty; could overlap on actual content

    # equals / in / not_in / not_equals
    def _set_from(ops: Dict[str, Any]):
        """Return ('exact', frozenset) / ('exclude', frozenset) / ('open', None)."""
        if "equals" in ops:
            return ("exact", frozenset([ops["equals"]]))
        if "in" in ops and isinstance(ops["in"], list):
            return ("exact", frozenset(ops["in"]))
        if "not_equals" in ops:
            return ("exclude", frozenset([ops["not_equals"]]))
        if "not_in" in ops and isinstance(ops["not_in"], list):
            return ("exclude", frozenset(ops["not_in"]))
        return ("open", None)

    a_kind, a_set = _set_from(a)
    b_kind, b_set = _set_from(b)

    if a_kind == "exact" and b_kind == "exact":
        return bool(a_set & b_set)
    if a_kind == "exact" and b_kind == "exclude":
        return bool(a_set - b_set)
    if a_kind == "exclude" and b_kind == "exact":
        return bool(b_set - a_set)
    if a_kind == "exclude" and b_kind == "exclude":
        return True  # both have infinite remainder; almost certainly overlap

    # matches regex pairs / one side regex
    if "matches" in a or "matches" in b:
        return True  # conservative: regexes can intersect in arbitrary ways

    # Numeric intervals: gt/gte/lt/lte
    def _interval(ops):
        lo = None; lo_closed = False
        hi = None; hi_closed = False
        if "gt" in ops:  lo, lo_closed = ops["gt"], False
        if "gte" in ops:
            if lo is None or ops["gte"] > lo: lo, lo_closed = ops["gte"], True
        if "lt" in ops:  hi, hi_closed = ops["lt"], False
        if "lte" in ops:
            if hi is None or ops["lte"] < hi: hi, hi_closed = ops["lte"], True
        return (lo, lo_closed, hi, hi_closed)

    has_numeric_a = any(k in a for k in ("gt", "gte", "lt", "lte"))
    has_numeric_b = any(k in b for k in ("gt", "gte", "lt", "lte"))
    if has_numeric_a or has_numeric_b:
        a_lo, a_lc, a_hi, a_hc = _interval(a)
        b_lo, b_lc, b_hi, b_hc = _interval(b)
        # Find effective lower bound and upper bound of the intersection
        lo = max((x for x in (a_lo, b_lo) if x is not None), default=None)
        hi = min((x for x in (a_hi, b_hi) if x is not None), default=None)
        if lo is None or hi is None:
            return True  # at least one side unbounded — overlap likely
        if lo < hi:
            return True
        if lo == hi:
            # Strict inequality on either side closes the interval
            if a_lo is not None and a_lo == lo and not a_lc: return False
            if b_lo is not None and b_lo == lo and not b_lc: return False
            if a_hi is not None and a_hi == hi and not a_hc: return False
            if b_hi is not None and b_hi == hi and not b_hc: return False
            return True
        return False

    # Default conservative
    return True


# ──────────────────────────────────────────────────────────────────────
# Flatten applies_when to a list of path→ops conditions, dropping
# logical operators conservatively.
# ──────────────────────────────────────────────────────────────────────

def _flatten_conditions(block: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Walk an applies_when block and return a dict of path → merged operator
    dict for top-level / all_of conditions. any_of / none_of are treated
    conservatively (they don't tighten the constraint set), so they
    don't contribute conditions to the flattened view.
    """
    flat: Dict[str, Dict[str, Any]] = {}
    for key, val in block.items():
        if key == "soft":
            continue
        if key == "all_of":
            for sub in val:
                for p, ops in _flatten_conditions(sub).items():
                    flat.setdefault(p, {}).update(_to_operator_dict(ops))
            continue
        if key in ("any_of", "none_of"):
            continue  # conservative: skip; intersection may still happen
        flat.setdefault(key, {}).update(_to_operator_dict(val))
    return flat


def intersects(applies_when_a: Dict[str, Any], applies_when_b: Dict[str, Any]) -> bool:
    """Do these two applies_when blocks admit at least one entity in common?"""
    if not applies_when_a and not applies_when_b:
        return True
    if not applies_when_a or not applies_when_b:
        return True  # empty matches everything

    flat_a = _flatten_conditions(applies_when_a)
    flat_b = _flatten_conditions(applies_when_b)

    # For each path present in both, the operator dicts must admit a common value.
    # Paths present in only one side: assume satisfiable (no constraint from the other).
    common_paths = set(flat_a.keys()) & set(flat_b.keys())
    for path in common_paths:
        if not _ops_intersect(flat_a[path], flat_b[path]):
            return False

    return True


# ──────────────────────────────────────────────────────────────────────
# Effect equivalence (rough — used to determine "disagree")
# ──────────────────────────────────────────────────────────────────────

def _effects_disagree(effect_a: Dict[str, Any], effect_b: Dict[str, Any]) -> bool:
    """Two effect blocks disagree if any shared directive has different payload."""
    if not effect_a or not effect_b:
        return False
    shared = set(effect_a.keys()) & set(effect_b.keys())
    if not shared:
        return False
    for k in shared:
        if k == "soft":
            continue
        if effect_a[k] != effect_b[k]:
            return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def detect_conflict(new_convention: Dict[str, Any],
                    existing_conventions: List[Dict[str, Any]]) -> ConflictReport:
    """Used at promotion time. Checks both natural-key and semantic conflicts."""
    report = ConflictReport(has_conflict=False)

    new_scope = new_convention.get("scope")
    new_nk = new_convention.get("natural_key")
    new_kind = new_convention.get("kind")

    for existing in existing_conventions:
        if existing.get("id") == new_convention.get("id"):
            continue  # don't compare a convention to itself
        if existing.get("status") == "archived":
            continue

        # Natural-key conflict: same scope (and same scope_id for non-global) + same natural_key
        if existing.get("natural_key") == new_nk and existing.get("scope") == new_scope:
            # For customer/study scope, also require matching scope_id
            same_scope_id = (
                new_scope == "global" or
                existing.get("scope_id") == new_convention.get("scope_id")
            )
            if same_scope_id:
                report.natural_key_conflicts.append({
                    "existing_id": existing.get("id"),
                    "natural_key": new_nk,
                    "scope": new_scope,
                })

        # Semantic conflict: only between two structured conventions at same scope
        if (new_kind == "structured"
                and existing.get("kind") == "structured"
                and existing.get("scope") == new_scope):
            same_scope_id = (
                new_scope == "global" or
                existing.get("scope_id") == new_convention.get("scope_id")
            )
            if same_scope_id and intersects(
                new_convention.get("applies_when", {}),
                existing.get("applies_when", {}),
            ) and _effects_disagree(
                new_convention.get("effect", {}),
                existing.get("effect", {}),
            ):
                report.semantic_conflicts.append({
                    "existing_id": existing.get("id"),
                    "scope": new_scope,
                })

    report.has_conflict = bool(report.natural_key_conflicts or report.semantic_conflicts)
    return report
