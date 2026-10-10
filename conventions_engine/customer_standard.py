"""Customer standard forms (form["customer_standard"], set by standards_match.py) are used exactly as the customer
provided them. Every effect that would have changed a field, a choice list or a setting there is run against a
copy, and the difference is recorded as a PROPOSAL in study_meta.standards_match.proposals: listed in the DVS
(DVS_OC4, Status "Proposed") and applied only when a data manager sets Action = Approve
(standards_match.apply_proposal).

Two refinements (2026-10-10):
  * A standard that carries NO logic of its own (an ODM gives structure only) gets the engine's data checks
    built on it, each recorded with the reason. Proposals remain for standards that carry their own logic.
    STANDARD_LOGIC_FREE_APPLY=0 restores proposals only.
  * A rule about how the form file is authored (settings sheet, naming, layout) is a build rule, not a data check:
    it is kept in study_meta.standards_match.build_rules and never listed in the DVS.

Scheduling and other form-level attributes (visits_assigned and the like) are not form content and are still
applied. An approved (convention, field) pair is applied by the engine like on any other form from then on.
"""
from __future__ import annotations
import copy
import hashlib
from typing import Any, Dict, List, Optional

from . import ApplyResult, EntityContext, Flag

# Form-level keys that make up the form's content. Everything else on a form is scheduling / metadata.
CONTENT_KEYS = ("survey", "choices", "settings", "extra_cols", "cross_form_dependencies")
# Row keys the engine keeps for traceability; they are not part of the form and never make a proposal.
BOOKKEEPING = {"edit_checks", "edit_check_exprs", "edit_check_msgs", "edit_check_set_by", "edit_check_covered_by",
               "edit_check_details", "constraint_engine_only", "completion_status", "library_source", "flag_reason",
               "concept", "concept_qualifier", "concept_source", "cdash", "qrs"}
_FLAG = "review_flags.customer_standard_not_applied"


# Survey columns that hold form logic. A change to one of them on a data item is a data check; a change to
# anything else (settings, labels, appearance, names, layout) is a rule about how the form file is authored.
LOGIC_KEYS = ("constraint", "relevant", "required", "calculation")
LOGIC_FREE_REASON = ("Applied: the customer standard form carries no logic of its own ({source}), so engine "
                     "checks are built on it.")
HAS_LOGIC_NOTE = ("Proposed: the customer standard form carries its own logic, so the rules engine does not "
                  "change it. Set Action = Approve to add to the build.")


def _is_logic_key(key: Any) -> bool:
    # exactly the columns the form builder writes as logic; a directive nothing builds (e.g. a precision hint
    # no builder reads) changes no check in the form and is not one
    return str(key or "") in LOGIC_KEYS


def is_data_check(prop: Any) -> bool:
    """True when a proposal changes the logic of a data item (constraint, show-when, required, calculation).

    Decided by what the proposal targets, from its operations: a rule that only touches the form file's own
    metadata (the settings sheet) or how an item is named, labelled or laid out is a build rule. Build rules are
    not data checks and are never listed in the DVS."""
    if not isinstance(prop, dict):
        return False
    ops = prop.get("ops")
    if not isinstance(ops, list) or not ops:      # no operations recorded (a trimmed copy): judge by the target
        return bool(prop.get("target_field")) and prop.get("check_type") not in (None, "", "Other")
    for op in ops:
        if not isinstance(op, dict):
            continue
        if op.get("op") == "row" and any(_is_logic_key(k) for k in (op.get("set") or {})):
            return True
        if op.get("op") == "insert" and any(str((op.get("row") or {}).get(k) or "").strip() for k in LOGIC_KEYS):
            return True
    return False


def apply_when_logic_free() -> bool:
    """STANDARD_LOGIC_FREE_APPLY=0: engine checks on customer standard forms are proposals only, as before."""
    import os
    return os.environ.get("STANDARD_LOGIC_FREE_APPLY", "1").strip() != "0"


def logic_free(form: Any) -> bool:
    """A customer standard form that came with no logic of its own: an ODM gives structure only, and an XLSForm
    standard may simply have no constraint, show-when or calculation. Logic the pipeline added later (fields
    added from the protocol, approved or applied proposals) does not count."""
    if not is_standard(form):
        return False
    cs = form.get("customer_standard") or {}
    if cs.get("has_logic"):
        return False
    if not cs.get("verbatim", False):
        return True                                # structure-only source (ODM)
    added = set(cs.get("added_fields") or [])
    return not any(str(r.get("calculation") or "").strip() for r in form.get("survey") or []
                   if isinstance(r, dict) and r.get("library_source") == "CUSTOM"
                   and r.get("name") not in added and not r.get("standard_proposal"))


def protected_form(ctx: EntityContext) -> Optional[Dict[str, Any]]:
    """The customer standard form this entity belongs to, or None."""
    form = ctx.entity if ctx.kind == "form" else ctx.parent if ctx.kind in ("field", "choice") else None
    return form if isinstance(form, dict) and form.get("customer_standard") else None


def is_standard(form: Any) -> bool:
    return isinstance(form, dict) and bool(form.get("customer_standard"))


def _entity_name(ctx: EntityContext) -> str:
    return str((ctx.entity or {}).get("name") or "") if ctx.kind in ("field", "choice") else ""


def _approved(form: Dict[str, Any], conv_id: str, name: str) -> bool:
    return any(a.get("convention_id") == conv_id and a.get("field") == name
               for a in (form.get("customer_standard") or {}).get("approved") or [])


def _has_directive(effect: Any, name: str) -> bool:
    if isinstance(effect, dict):
        return any(k == name or _has_directive(v, name) for k, v in effect.items())
    if isinstance(effect, list):
        return any(_has_directive(v, name) for v in effect)
    return False


def _source(conv: Dict[str, Any], check_id: Optional[str]) -> str:
    """Check Source shown in the DVS: CDISC CORE (id) / CDISC Standard / Global Rule / Customer Rule."""
    cid = str(check_id or "")
    if cid.startswith("CORE-"):
        return f"CDISC CORE ({cid})"
    if cid.startswith(("SDTM.", "CDISC.")):
        return "CDISC Standard"
    try:
        import edit_check_meta
        src = edit_check_meta.source_of_convention(conv.get("id"), edit_check_meta._global_ids())
        if src.startswith("CDISC"):
            return src
    except Exception:
        pass
    return "Global Rule" if conv.get("scope") == "global" else "Customer Rule"


def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k not in BOOKKEEPING}


def _diff(real: Dict[str, Any], shadow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Operations that turn the real form's content into the shadow's (the engine only adds or edits in place)."""
    ops: List[Dict[str, Any]] = []
    r_rows = [r for r in real.get("survey") or [] if isinstance(r, dict)]
    s_rows = [r for r in shadow.get("survey") or [] if isinstance(r, dict)]
    r_names = {r.get("name") for r in r_rows if r.get("name")}
    kept, prev = [], None
    for row in s_rows:
        name = row.get("name")
        if name and name not in r_names:
            ops.append({"op": "insert", "after": prev, "row": _clean(row)})
        else:
            kept.append(row)
        prev = name or prev
    if len(kept) == len(r_rows):
        for old, new in zip(r_rows, kept):
            a, b = _clean(old), _clean(new)
            changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if changed:
                ops.append({"op": "row", "field": old.get("name"),
                            "set": {k: b.get(k) for k in sorted(changed)},
                            "before": {k: a.get(k) for k in sorted(changed)}})
    have = {(c.get("list_name"), str(c.get("name"))) for c in real.get("choices") or [] if isinstance(c, dict)}
    for c in shadow.get("choices") or []:
        if isinstance(c, dict) and (c.get("list_name"), str(c.get("name"))) not in have:
            ops.append({"op": "choice", "choice": dict(c)})
    rs, ss = real.get("settings") or {}, shadow.get("settings") or {}
    for k in sorted(set(rs) | set(ss)):
        if rs.get(k) != ss.get(k):
            ops.append({"op": "setting", "key": k, "value": ss.get(k), "before": rs.get(k)})
    r_deps = real.get("cross_form_dependencies") or []
    for d in shadow.get("cross_form_dependencies") or []:
        if d not in r_deps:
            ops.append({"op": "dependency", "value": copy.deepcopy(d)})
    return ops


def _describe(ops, target, shadow_row, real_row):
    """(check_type, logic, message, check_id) for the DVS row, from the change to the target field."""
    check_id = None
    if isinstance(shadow_row, dict):
        new_ids = [c for c in shadow_row.get("edit_checks") or [] if c not in ((real_row or {}).get("edit_checks") or [])]
        check_id = new_ids[0] if new_ids else None
    op = next((o for o in ops if o["op"] == "row" and o.get("field") == target), None)
    if op is None:
        ins = next((o for o in ops if o["op"] == "insert"), None)
        if ins is not None:
            r = ins["row"]
            return "Other", f"add {r.get('type')} {r.get('name')}" + (f": {r.get('calculation')}" if r.get("calculation") else ""), "", check_id
        ch = [o for o in ops if o["op"] == "choice"]
        if ch:
            return "Other", "add choice(s): " + ", ".join(f"{o['choice'].get('list_name')}.{o['choice'].get('name')}" for o in ch), "", check_id
        st = next((o for o in ops if o["op"] == "setting"), None)
        return "Other", (f"settings.{st['key']} = {st['value']}" if st else "form change"), "", check_id
    s, before = op["set"], op["before"]
    if "constraint" in s:
        cur, new = str(before.get("constraint") or ""), str(s.get("constraint") or "")
        clause = (shadow_row or {}).get("edit_check_exprs", {}).get(check_id) if check_id else None
        if not clause:
            pre = f"({cur}) and ("
            clause = new[len(pre):-1] if cur and new.startswith(pre) and new.endswith(")") else new
        msg = ((shadow_row or {}).get("edit_check_msgs") or {}).get(check_id) if check_id else None
        if not msg:
            old_m, new_m = str(before.get("constraint_message") or ""), str(s.get("constraint_message") or "")
            msg = new_m[len(old_m):].lstrip(" .") if old_m and new_m.startswith(old_m) else new_m
        op["clause"], op["message"] = clause, msg
        return "Constraint", clause, msg or "", check_id
    if "relevant" in s:
        return "Conditional Display", str(s.get("relevant") or ""), "", check_id
    if "required" in s:
        return "Required", f"required = {s.get('required')}", str(s.get("required_message") or ""), check_id
    if "calculation" in s:
        return "Calculation", str(s.get("calculation") or ""), "", check_id
    k = sorted(s)[0]
    return "Other", f"{k} = {s.get(k)}", "", check_id


def begin_pass(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Start collecting this pass's proposals (the engine runs several times per build; each pass rebuilds them)."""
    return []


def end_pass(spec: Dict[str, Any], collected: List[Dict[str, Any]]) -> None:
    sm = spec.get("study_meta") if isinstance(spec.get("study_meta"), dict) else None
    st = (sm or {}).get("standards_match")
    if not isinstance(st, dict):
        return
    rejected = set(st.get("rejected_ids") or [])
    seen, mine, build_rules = set(), [], []
    for p in collected:
        if p["id"] in seen or p["id"] in rejected:
            continue
        seen.add(p["id"])
        if not is_data_check(p):
            # how the form file is authored (settings sheet, naming, layout): a build rule, never a DVS row
            build_rules.append({k: p.get(k) for k in ("id", "convention_id", "title", "target_form",
                                                      "target_field", "logic")})
            continue
        mine.append(p)
    st["build_rules"] = build_rules
    st["proposals"] = [p for p in st.get("proposals") or [] if p.get("kind") != "engine"] + mine
    if apply_when_logic_free():
        _apply_on_logic_free(spec, st, mine)


def _apply_on_logic_free(spec: Dict[str, Any], st: Dict[str, Any], proposed: List[Dict[str, Any]]) -> None:
    """Build this pass's engine checks on the customer standard forms that carry no logic of their own, through
    the same deterministic step a data manager's Approve uses. Each applied check is recorded with the reason
    (study_meta.standards_match.auto_applied, and on the form's customer_standard.approved); a check that cannot
    be applied stays a proposal."""
    import standards_match
    forms = {standards_match.norm_id(f.get("form_id")): f for f in spec.get("forms") or [] if isinstance(f, dict)}
    done = {a.get("id") for a in st.get("auto_applied") or []}
    for p in proposed:
        form = forms.get(standards_match.norm_id(p.get("target_form")))
        if form is None or not logic_free(form):
            continue
        status, _note = standards_match.apply_proposal(spec, p)
        if status not in ("applied", "already"):
            continue
        cs = form.get("customer_standard") or {}
        reason = LOGIC_FREE_REASON.format(source=cs.get("source") or "structure-only source")
        for a in cs.get("approved") or []:
            if a.get("id") == p.get("id"):
                a["auto"], a["check_id"], a["check_type"] = reason, p.get("check_id"), p.get("check_type")
        if p.get("id") not in done:
            done.add(p.get("id"))
            st.setdefault("auto_applied", []).append({
                "id": p.get("id"), "convention_id": p.get("convention_id"), "check_id": p.get("check_id"),
                "source": p.get("source"), "target_form": p.get("target_form"),
                "target_field": p.get("target_field"), "check_type": p.get("check_type"),
                "logic": p.get("logic"), "reason": reason})


def applied_reason(form: Any, field: Any, check_type: str = "", check_id: Any = None,
                   convention_id: Any = None) -> str:
    """Why a check on a customer standard form is in the build without a data manager's approval ("" if it is
    not one of the checks applied on a logic-free standard)."""
    for a in ((form or {}).get("customer_standard") or {}).get("approved") or []:
        if not a.get("auto") or a.get("field") != field:
            continue
        if check_id and check_id in (a.get("check_id"), a.get("id")):
            return a["auto"]
        if convention_id and a.get("convention_id") == convention_id:
            return a["auto"]
        if not check_id and not convention_id and (not check_type or a.get("check_type") == check_type):
            return a["auto"]
    return ""


def dry_run(conv: Dict[str, Any], ctx: EntityContext, spec: Dict[str, Any], canonical_lists: Dict[str, Any],
            collected: List[Dict[str, Any]]) -> Optional[ApplyResult]:
    """Handle one matched (convention, entity) on a customer standard form.

    Returns None when the engine should apply the effect normally (a DM approved it); otherwise the effect has been
    evaluated on a copy, scheduling changes were applied, any content change was recorded as a proposal, and the
    (empty) result is returned."""
    from . import effects
    form = protected_form(ctx)
    effect = conv.get("effect") or {}
    name = _entity_name(ctx)
    if _approved(form, conv.get("id"), name):
        return None
    result = ApplyResult()
    if _has_directive(effect, "move_to_form"):
        result.flags_raised.append(Flag(_FLAG, f"{conv.get('id')}: would move {form.get('form_id')}.{name} to another "
                                               f"form; fields are never removed from a customer standard form"))
        _flush(spec, result)
        return result
    shadow = copy.deepcopy(form)
    forms = spec.get("forms") or []
    shadow_spec = dict(spec)
    shadow_spec["forms"] = [shadow if f is form else f for f in forms]
    shadow_spec["review_flags"] = copy.deepcopy(spec.get("review_flags") or {})
    if isinstance(spec.get("_omop_vocab_files"), dict):
        shadow_spec["_omop_vocab_files"] = dict(spec["_omop_vocab_files"])
    if ctx.kind == "form":
        entity, parent = shadow, shadow_spec
    else:
        key = "survey" if ctx.kind == "field" else "choices"
        idx = next((i for i, r in enumerate(form.get(key) or []) if r is ctx.entity), None)
        if idx is None:
            return result
        entity, parent = shadow[key][idx], shadow
    sctx = EntityContext(kind=ctx.kind, entity=entity, parent=parent, spec=shadow_spec, path=ctx.path,
                         bindings=ctx.bindings)
    applied = effects.apply_effect(effect, sctx, shadow_spec, conv["id"], canonical_lists=canonical_lists)
    result.soft_directives = applied.soft_directives
    for k in set(form) | set(shadow):  # scheduling and other form-level attributes are not form content
        if k not in CONTENT_KEYS and k != "customer_standard" and form.get(k) != shadow.get(k):
            if k in shadow:
                form[k] = shadow[k]
            else:
                form.pop(k, None)
    ops = _diff(form, shadow)
    if not ops:
        return result
    real_row = ctx.entity if ctx.kind == "field" else None
    shadow_row = entity if ctx.kind == "field" else None
    check_type, logic, message, check_id = _describe(ops, name, shadow_row, real_row)
    pid = "STD-" + hashlib.sha1(f"{conv.get('id')}|{form.get('form_id')}|{name}".encode()).hexdigest()[:8].upper()
    collected.append({
        "id": pid, "kind": "engine", "convention_id": conv.get("id"), "check_id": check_id,
        "source": _source(conv, check_id), "title": conv.get("title") or "",
        "target_form": form.get("form_id"), "target_field": name, "check_type": check_type,
        "logic": logic, "message": message, "rationale": conv.get("description") or conv.get("title") or "",
        "ops": ops})
    return result


def _flush(spec: Dict[str, Any], result: ApplyResult) -> None:
    for flag in result.flags_raised:
        cat = flag.category[len("review_flags."):] if flag.category.startswith("review_flags.") else flag.category
        bucket = spec.setdefault("review_flags", {}).setdefault(cat, [])
        if flag.message not in bucket:
            bucket.append(flag.message)
