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

    bindings = getattr(ctx, "bindings", None) or {}
    if head in bindings:
        current: Any = bindings[head]
    elif head == "study":
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

# ──────────────────────────────────────────────────────────────────────
# Templates: ${path|filter:arg:arg}
# ──────────────────────────────────────────────────────────────────────
#
# Only engine paths are substituted (roots study/form/field/event/choice and
# names bound with "as"). Anything else, e.g. an XLSForm reference ${AESTDAT},
# is left exactly as written. Filters:
#   replace:OLD:NEW     strip_suffix:X     strip_prefix:X     upper     lower

_ROOTS = ("study", "form", "field", "event", "choice")
# Innermost-first: ${${start.name}} renders start.name, leaving the XLSForm reference ${AESTDAT}.
_TEMPLATE = re.compile(r"\$\{([^${}]+)\}")


def _apply_filter(val: str, spec: str) -> str:
    name, *args = spec.split(":")
    if name == "replace" and len(args) == 2:
        return val.replace(args[0], args[1])
    if name == "strip_suffix" and len(args) == 1:
        return val[: -len(args[0])] if args[0] and val.endswith(args[0]) else val
    if name == "strip_prefix" and len(args) == 1:
        return val[len(args[0]):] if val.startswith(args[0]) else val
    if name == "upper":
        return val.upper()
    if name == "lower":
        return val.lower()
    raise DSLEvaluationError(f"Unknown template filter {spec!r}")


def render(template: str, ctx: EntityContext, strict: bool = False) -> str:
    """Substitute engine paths in a template. Non-engine ${...} is left verbatim.
    strict=True replaces an unresolvable engine path with <unresolved:path>; otherwise it is left as is."""
    bindings = getattr(ctx, "bindings", None) or {}

    def _sub(m: "re.Match[str]") -> str:
        expr = m.group(1)
        path, *filters = expr.split("|")
        head = path.split(".")[0]
        if head not in _ROOTS and head not in bindings:
            # XLSForm references are plain identifiers; a dotted path is an engine path left unbound.
            return f"<unresolved:{path}>" if (strict and "." in path) else m.group(0)
        val = _resolve_path(path, ctx)
        if val is _SENTINEL_MISSING:
            return f"<unresolved:{path}>" if strict else m.group(0)
        out = str(val)
        for f in filters:
            out = _apply_filter(out, f)
        return out
    return _TEMPLATE.sub(_sub, template)


def _render_where(obj: Any, ctx: EntityContext) -> Any:
    """Render templates inside a where block against the OUTER context before probing candidates."""
    if isinstance(obj, str):
        return render(obj, ctx) if "${" in obj else obj
    if isinstance(obj, dict):
        return {k: _render_where(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render_where(v, ctx) for v in obj]
    return obj


_YES = {"Y", "YES", "1", "TRUE"}
_NO = {"N", "NO", "0", "FALSE"}


def _yes_no_codes(candidate: Dict[str, Any], form: Dict[str, Any]) -> Dict[str, str]:
    """For a select_one bound field, the codes its own list uses for Yes and No (by code or label)."""
    t = str(candidate.get("type") or "").strip()
    if not t.lower().startswith("select_one "):
        return {}
    ln = t.split(" ", 1)[1].strip()
    out: Dict[str, str] = {}
    for c in form.get("choices") or []:
        if not isinstance(c, dict) or str(c.get("list_name") or "").strip() != ln:
            continue
        code = str(c.get("name") or "").strip()
        keys = {code.upper(), str(c.get("label") or "").strip().upper(),
                str(c.get("cdisc_submission_value") or "").strip().upper()}
        if keys & _YES and "_yes_code" not in out:
            out["_yes_code"] = code
        if keys & _NO and "_no_code" not in out:
            out["_no_code"] = code
    return out


def _bind(ctx: EntityContext, payload: Dict[str, Any], candidate: Dict[str, Any], form: Dict[str, Any]) -> None:
    name = payload.get("as")
    if not name:
        return
    if name in _ROOTS:
        raise DSLEvaluationError(f"'as' name {name!r} is reserved")
    ctx.bindings[name] = {**candidate, "_form_id": form.get("form_id"), **_yes_no_codes(candidate, form)}


def _op_equals(actual: Any, expected: Any) -> bool:
    return actual == expected

def _op_in(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, list):
        raise DSLEvaluationError(f"'in' requires a list, got {type(expected).__name__}")
    return actual in expected

def _op_contains(actual: Any, expected: Any) -> bool:
    """List-valued path (e.g. study.forms[*].form_id) contains the value. False for anything that is not a list."""
    return isinstance(actual, list) and expected in actual

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
    "contains":   _op_contains,
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


# ──────────────────────────────────────────────────────────────────────
# Structural quantifiers (B.1c-1)
# ──────────────────────────────────────────────────────────────────────
#
# form.has_field — does the form contain any field matching `where`?
# field.has_sibling — does the current field's neighbourhood (configurable
#   scope) contain any OTHER field matching `where`?
#
# Both take a nested condition sub-expression. Soft hints from the inner
# probe are discarded — a quantifier describes a hypothetical entity, not
# the entity the outer convention is being applied to.

def _get_form_for_quantifier(ctx: EntityContext) -> Dict[str, Any]:
    """Resolve which form to iterate for a structural quantifier."""
    if ctx.kind == "form":
        return ctx.entity
    if ctx.kind == "field" or ctx.kind == "choice":
        return ctx.parent
    raise DSLEvaluationError(
        f"form.has_field / field.has_sibling requires form-, field-, or "
        f"choice-scoped context, got {ctx.kind!r}"
    )


def _eval_has_field(payload: Dict[str, Any], ctx: EntityContext) -> bool:
    """form.has_field — existential quantifier over form.survey."""
    if not isinstance(payload, dict) or "where" not in payload:
        raise DSLEvaluationError("form.has_field requires a 'where' sub-expression")
    where = _render_where(payload["where"], ctx)
    form = _get_form_for_quantifier(ctx)
    survey = form.get("survey", []) or []
    for i, candidate in enumerate(survey):
        temp_ctx = EntityContext(
            kind="field", entity=candidate, parent=form,
            spec=ctx.spec, path=f"<has_field-probe[{i}]>",
        )
        # Fresh discarded soft_hints — probe results don't leak guidance.
        if _eval_block(where, temp_ctx, []).matched:
            _bind(ctx, payload, candidate, form)
            return True
    return False


def _eval_has_sibling(payload: Dict[str, Any], ctx: EntityContext) -> bool:
    """field.has_sibling — existential quantifier over the current field's neighbourhood."""
    if ctx.kind != "field":
        raise DSLEvaluationError(
            f"field.has_sibling requires field-scoped context, got {ctx.kind!r}"
        )
    if not isinstance(payload, dict) or "where" not in payload:
        raise DSLEvaluationError("field.has_sibling requires a 'where' sub-expression")
    where = _render_where(payload["where"], ctx)
    scope = payload.get("scope", "same_form")
    if scope not in ("same_form", "same_itemgroup"):
        raise DSLEvaluationError(
            f"field.has_sibling scope must be 'same_form' or 'same_itemgroup', got {scope!r}"
        )
    form = ctx.parent
    survey = form.get("survey", []) or []
    self_id = id(ctx.entity)
    self_ig = ctx.entity.get("bind__oc_itemgroup", "") if isinstance(ctx.entity, dict) else ""
    for i, candidate in enumerate(survey):
        if id(candidate) == self_id:
            continue  # exclude self
        if scope == "same_itemgroup":
            cand_ig = candidate.get("bind__oc_itemgroup", "") if isinstance(candidate, dict) else ""
            if cand_ig != self_ig or self_ig == "":
                continue
        # scope == "same_form": any other field qualifies; no additional filter.
        temp_ctx = EntityContext(
            kind="field", entity=candidate, parent=form,
            spec=ctx.spec, path=f"<has_sibling-probe[{i}]>",
        )
        if _eval_block(where, temp_ctx, []).matched:
            _bind(ctx, payload, candidate, form)
            return True
    return False


def _eval_study_has_field(payload: Dict[str, Any], ctx: EntityContext) -> bool:
    """study.has_field — existential quantifier over every form's survey (cross-form rules).
    "other_forms": true (default) skips the current entity's own form. "where" may test form.* too."""
    if not isinstance(payload, dict) or "where" not in payload:
        raise DSLEvaluationError("study.has_field requires a 'where' sub-expression")
    where = _render_where(payload["where"], ctx)
    own = ctx.parent if ctx.kind in ("field", "choice") else (ctx.entity if ctx.kind == "form" else None)
    for fi, form in enumerate(ctx.spec.get("forms", []) or []):
        if not isinstance(form, dict) or (payload.get("other_forms", True) and form is own):
            continue
        for i, candidate in enumerate(form.get("survey", []) or []):
            if not isinstance(candidate, dict):
                continue
            temp_ctx = EntityContext(kind="field", entity=candidate, parent=form, spec=ctx.spec,
                                     path=f"<study_has_field-probe[{fi}][{i}]>")
            if _eval_block(where, temp_ctx, []).matched:
                _bind(ctx, payload, candidate, form)
                return True
    return False


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

        if key == "form.has_field":
            if not _eval_has_field(val, ctx):
                return EvaluateResult(matched=False, soft_hints=soft_hints)
            continue

        if key == "field.has_sibling":
            if not _eval_has_sibling(val, ctx):
                return EvaluateResult(matched=False, soft_hints=soft_hints)
            continue

        if key == "study.has_field":
            if not _eval_study_has_field(val, ctx):
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
