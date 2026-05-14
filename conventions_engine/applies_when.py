"""Filter DSL evaluator. See conventions/schema/dsl-operators.md."""
from __future__ import annotations
import re
from typing import Any, Dict, Iterable, List, Tuple

from . import EvaluateResult, DSLEvaluationError, EntityContext


# ──────────────────────────────────────────────────────────────────────
# Path resolution
# ──────────────────────────────────────────────────────────────────────

_SENTINEL_MISSING = object()


def _resolve_path(path: str, ctx: EntityContext) -> Any:
    """
    Resolve a dotted path against the entity context.

    Roots:
      study.*  → ctx.spec
      form.*   → ctx.entity if ctx.kind == 'form' else ctx.parent if ctx.kind == 'field'
      field.*  → ctx.entity if ctx.kind == 'field'
      event.*  → ctx.entity if ctx.kind == 'event'
      choice.* → ctx.entity if ctx.kind == 'choice'

    Path features:
      .name           → key access
      .arr[*].name    → fan out: returns a list of name values across arr
      .arr.length     → length of arr

    Returns _SENTINEL_MISSING if a non-terminal step doesn't exist.
    """
    parts = path.split(".")
    head = parts[0]
    rest = parts[1:]

    if head == "study":
        current: Any = ctx.spec
    elif head == "form":
        if ctx.kind == "form":
            current = ctx.entity
        elif ctx.kind == "field" or ctx.kind == "choice":
            current = ctx.parent
        else:
            return _SENTINEL_MISSING
    elif head == "field":
        if ctx.kind == "field":
            current = ctx.entity
        else:
            return _SENTINEL_MISSING
    elif head == "event":
        if ctx.kind == "event":
            current = ctx.entity
        else:
            return _SENTINEL_MISSING
    elif head == "choice":
        if ctx.kind == "choice":
            current = ctx.entity
        else:
            return _SENTINEL_MISSING
    else:
        return _SENTINEL_MISSING

    for step in rest:
        if step == "length":
            if isinstance(current, (list, str, dict)):
                return len(current)
            return _SENTINEL_MISSING

        if step.endswith("[*]"):
            key = step[:-3]
            if not isinstance(current, dict) or key not in current:
                return _SENTINEL_MISSING
            arr = current[key]
            if not isinstance(arr, list):
                return _SENTINEL_MISSING
            # Fan out: keep going with each item, accumulate results
            tail = ".".join(parts[parts.index(step) + 1:])
            if not tail:
                return arr
            return [_resolve_path_from_value(item, tail) for item in arr]

        if isinstance(current, dict):
            if step not in current:
                return _SENTINEL_MISSING
            current = current[step]
        else:
            return _SENTINEL_MISSING

    return current


def _resolve_path_from_value(value: Any, path: str) -> Any:
    """Continue path resolution from an arbitrary value (used inside fan-outs)."""
    parts = path.split(".")
    current = value
    for step in parts:
        if step == "length":
            if isinstance(current, (list, str, dict)):
                return len(current)
            return _SENTINEL_MISSING
        if isinstance(current, dict):
            if step not in current:
                return _SENTINEL_MISSING
            current = current[step]
        else:
            return _SENTINEL_MISSING
    return current


# ──────────────────────────────────────────────────────────────────────
# Comparison operators
# ──────────────────────────────────────────────────────────────────────

def _op_equals(actual: Any, expected: Any) -> bool:
    return actual == expected

def _op_in(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, list):
        raise DSLEvaluationError(f"'in' requires a list, got {type(expected).__name__}")
    return actual in expected

def _op_not_in(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, list):
        raise DSLEvaluationError(f"'not_in' requires a list, got {type(expected).__name__}")
    return actual not in expected

def _op_matches(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, str):
        raise DSLEvaluationError(f"'matches' requires a regex string, got {type(expected).__name__}")
    if not isinstance(actual, str):
        return False
    try:
        return bool(re.search(expected, actual))
    except re.error as e:
        raise DSLEvaluationError(f"invalid regex {expected!r}: {e}")

def _num(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise DSLEvaluationError(f"expected numeric value, got {type(value).__name__}: {value!r}")

def _op_gt(actual: Any, expected: Any) -> bool: return _num(actual) > _num(expected)
def _op_gte(actual: Any, expected: Any) -> bool: return _num(actual) >= _num(expected)
def _op_lt(actual: Any, expected: Any) -> bool: return _num(actual) < _num(expected)
def _op_lte(actual: Any, expected: Any) -> bool: return _num(actual) <= _num(expected)

def _is_empty(value: Any) -> bool:
    if value is _SENTINEL_MISSING:
        return True
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


OPS = {
    "equals":     _op_equals,
    "not_equals": lambda a, e: not _op_equals(a, e),
    "in":         _op_in,
    "not_in":     _op_not_in,
    "matches":    _op_matches,
    "gt":         _op_gt,
    "gte":        _op_gte,
    "lt":         _op_lt,
    "lte":        _op_lte,
}


# ──────────────────────────────────────────────────────────────────────
# Main evaluator
# ──────────────────────────────────────────────────────────────────────

LOGICAL_KEYS = {"all_of", "any_of", "none_of"}


def _eval_condition(path: str, expr: Any, ctx: EntityContext) -> bool:
    """Evaluate one condition: a path with either a bare value or an operator dict."""
    actual = _resolve_path(path, ctx)

    # Bare value → equals shortcut
    if not isinstance(expr, dict):
        if actual is _SENTINEL_MISSING:
            return False
        return _op_equals(actual, expr)

    # Operator dict — handle special non-comparison operators first
    for k, v in expr.items():
        if k == "non_empty":
            result = not _is_empty(actual)
            if v is False:
                result = not result
            if not result:
                return False
            continue
        if k == "empty":
            result = _is_empty(actual)
            if v is False:
                result = not result
            if not result:
                return False
            continue
        if k == "present":
            result = actual is not _SENTINEL_MISSING
            if v is False:
                result = not result
            if not result:
                return False
            continue
        if k in OPS:
            if actual is _SENTINEL_MISSING:
                return False
            try:
                if not OPS[k](actual, v):
                    return False
            except DSLEvaluationError:
                return False
            continue
        raise DSLEvaluationError(f"Unknown operator {k!r} for path {path!r}")

    return True


def evaluate(applies_when: Dict[str, Any], ctx: EntityContext) -> EvaluateResult:
    """Evaluate an applies_when block against an entity context."""
    soft_hints: List[str] = []

    if not applies_when:
        # Empty applies_when is treated as "always matches"
        return EvaluateResult(matched=True, soft_hints=soft_hints)

    return _eval_block(applies_when, ctx, soft_hints)


def _eval_block(block: Dict[str, Any], ctx: EntityContext,
                soft_hints: List[str]) -> EvaluateResult:
    """Top-level keys form an implicit all_of."""
    for key, val in block.items():
        if key == "soft":
            if isinstance(val, str):
                soft_hints.append(val)
            continue

        if key == "all_of":
            for sub in val:
                r = _eval_block(sub, ctx, soft_hints)
                if not r.matched:
                    return EvaluateResult(matched=False, soft_hints=soft_hints)
            continue

        if key == "any_of":
            matched_any = False
            for sub in val:
                r = _eval_block(sub, ctx, soft_hints)
                if r.matched:
                    matched_any = True
                    # don't break — keep collecting soft hints from all branches
            if not matched_any:
                return EvaluateResult(matched=False, soft_hints=soft_hints)
            continue

        if key == "none_of":
            for sub in val:
                r = _eval_block(sub, ctx, soft_hints)
                if r.matched:
                    return EvaluateResult(matched=False, soft_hints=soft_hints)
            continue

        # Path condition
        if not _eval_condition(key, val, ctx):
            return EvaluateResult(matched=False, soft_hints=soft_hints)

    return EvaluateResult(matched=True, soft_hints=soft_hints)
