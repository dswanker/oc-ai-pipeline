"""Effect DSL applier. See conventions/schema/dsl-operators.md."""
from __future__ import annotations
import re
from typing import Any, Dict, List

from . import ApplyResult, Mutation, Flag, DSLEvaluationError, EntityContext
from .applies_when import _resolve_path, _SENTINEL_MISSING


# ──────────────────────────────────────────────────────────────────────
# Path-based writes
# ──────────────────────────────────────────────────────────────────────

def _set_path(path: str, value: Any, ctx: EntityContext) -> Any:
    """
    Write `value` into the entity context at `path`. Returns the old
    value (or _SENTINEL_MISSING). Only supports simple dotted paths
    rooted at the same place applies_when reads from; fan-outs and
    .length are not writable.
    """
    parts = path.split(".")
    head = parts[0]
    rest = parts[1:]

    if head == "study":
        current: Any = ctx.spec
    elif head == "form":
        if ctx.kind == "form":
            current = ctx.entity
        elif ctx.kind in ("field", "choice"):
            current = ctx.parent
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "field":
        if ctx.kind == "field":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "event":
        if ctx.kind == "event":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    elif head == "choice":
        if ctx.kind == "choice":
            current = ctx.entity
        else:
            raise DSLEvaluationError(f"Cannot write to {path!r} for target {ctx.kind!r}")
    else:
        raise DSLEvaluationError(f"Unknown path root: {head!r}")

    if not rest:
        raise DSLEvaluationError(f"Cannot overwrite root context {path!r}")

    for step in rest[:-1]:
        if "[*]" in step or step == "length":
            raise DSLEvaluationError(f"Fan-out / length not writable: {path!r}")
        if not isinstance(current, dict):
            raise DSLEvaluationError(f"Cannot traverse non-dict at {step!r} in {path!r}")
        if step not in current:
            current[step] = {}
        current = current[step]

    final_key = rest[-1]
    if "[*]" in final_key or final_key == "length":
        raise DSLEvaluationError(f"Fan-out / length not writable: {path!r}")
    if not isinstance(current, dict):
        raise DSLEvaluationError(f"Cannot write to non-dict at {final_key!r} in {path!r}")
    old = current.get(final_key, _SENTINEL_MISSING)
    current[final_key] = value
    return old


# ──────────────────────────────────────────────────────────────────────
# Template variable substitution for flag messages
# ──────────────────────────────────────────────────────────────────────

_TEMPLATE_VAR = re.compile(r"\$\{([^}]+)\}")


def _interpolate(template: str, ctx: EntityContext) -> str:
    """Replace ${path} occurrences with resolved values."""
    def _sub(m: "re.Match[str]") -> str:
        path = m.group(1)
        val = _resolve_path(path, ctx)
        if val is _SENTINEL_MISSING:
            return f"<unresolved:{path}>"
        return str(val)
    return _TEMPLATE_VAR.sub(_sub, template)


# ──────────────────────────────────────────────────────────────────────
# Effect directives
# ──────────────────────────────────────────────────────────────────────

def _do_set(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        old = _set_path(path, value, ctx)
        old_repr = None if old is _SENTINEL_MISSING else old
        result.mutations_made.append(Mutation(
            directive="set", path=path, old_value=old_repr, new_value=value,
        ))


def _do_ensure(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING or current is None or current == "" or current == [] or current == {}:
            _set_path(path, value, ctx)
            result.mutations_made.append(Mutation(
                directive="ensure", path=path, old_value=None, new_value=value,
            ))


def _do_require(payload: Any, ctx: EntityContext, result: ApplyResult) -> None:
    paths = payload if isinstance(payload, list) else [payload]
    for path in paths:
        val = _resolve_path(path, ctx)
        # For fan-out paths, val is a list — require each non-empty
        if isinstance(val, list):
            empties = [i for i, v in enumerate(val) if v in (None, "", [], {}, _SENTINEL_MISSING)]
            if empties:
                result.flags_raised.append(Flag(
                    category="review_flags.constraint_review",
                    message=f"Required path {path} has empty values at indexes {empties}",
                ))
        elif val is _SENTINEL_MISSING or val in (None, "", [], {}):
            result.flags_raised.append(Flag(
                category="review_flags.constraint_review",
                message=f"Required path {path} is empty",
            ))


def _do_flag(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    category = payload.get("category", "review_flags.constraint_review")
    message_template = payload.get("message", "")
    result.flags_raised.append(Flag(
        category=category,
        message=_interpolate(message_template, ctx),
    ))


def _do_append_to(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING:
            _set_path(path, [value], ctx)
            result.mutations_made.append(Mutation(
                directive="append_to", path=path, old_value=None, new_value=[value],
            ))
            continue
        if not isinstance(current, list):
            raise DSLEvaluationError(f"append_to target {path!r} is not a list")
        if value in current:
            continue  # idempotent
        new = current + [value]
        _set_path(path, new, ctx)
        result.mutations_made.append(Mutation(
            directive="append_to", path=path, old_value=current, new_value=new,
        ))


def _do_remove_from(payload: Dict[str, Any], ctx: EntityContext, result: ApplyResult) -> None:
    for path, value in payload.items():
        current = _resolve_path(path, ctx)
        if current is _SENTINEL_MISSING or not isinstance(current, list):
            continue
        if value not in current:
            continue
        new = [x for x in current if x != value]
        _set_path(path, new, ctx)
        result.mutations_made.append(Mutation(
            directive="remove_from", path=path, old_value=current, new_value=new,
        ))


DIRECTIVES = {
    "set":         _do_set,
    "ensure":      _do_ensure,
    "require":     _do_require,
    "flag":        _do_flag,
    "append_to":   _do_append_to,
    "remove_from": _do_remove_from,
}


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def apply_effect(effect: Dict[str, Any], ctx: EntityContext,
                 spec: Dict[str, Any], convention_id: str) -> ApplyResult:
    """
    Apply an effect block. Mutates spec / ctx.entity in place. Soft
    directives are accumulated, not applied.

    Effects within one block execute in source order.
    """
    result = ApplyResult()
    if not effect:
        return result

    for key, payload in effect.items():
        if key == "soft":
            if isinstance(payload, str):
                result.soft_directives.append(payload)
            continue
        if key in DIRECTIVES:
            DIRECTIVES[key](payload, ctx, result)
            continue
        raise DSLEvaluationError(f"Unknown effect directive {key!r} in {convention_id!r}")

    # Flags raised need to land in spec.review_flags.<category>
    for flag in result.flags_raised:
        review_flags = spec.setdefault("review_flags", {})
        # category is dotted like "review_flags.constraint_review" — we strip prefix
        cat = flag.category
        if cat.startswith("review_flags."):
            cat = cat[len("review_flags."):]
        bucket = review_flags.setdefault(cat, [])
        if flag.message not in bucket:
            bucket.append(flag.message)

    return result
