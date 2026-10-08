"""dvs_edits.py: read the DM's decisions from an edited DVS (DVS_OC4 "Action" column) and apply them to the Study
Spec deterministically, before the normal EDC builder runs. Replaces the old path where Claude regenerated every
form from an edited DVS. Only DVS files with the Action column use this path; older DVS files are untouched.

  Approve  AI-proposed row -> ai_edit_checks.apply_proposal (validated, same path as every engine check)
  Reject   AI-proposed row -> dropped from the proposals and never proposed again
  Delete   any check       -> the clause / show-when / required flag is removed; engine checks are suppressed so the
                              rules engine never re-adds them
  Change   any check       -> query message updated (a changed plain-English logic description needs translation)
  Add      new row         -> plain-English translation (separate step); recorded as pending until then

Every decision is recorded in study_meta.edit_check_decisions with its outcome.
"""
import io, json

ACTIONS = ("approve", "reject", "delete", "change", "add")


def parse_actions(xlsx_bytes):
    """Rows of DVS_OC4 that carry an Action. None when the DVS is not the new (Action column) format."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    except Exception:
        return None
    if "DVS_OC4" not in wb.sheetnames:
        return None
    ws = wb["DVS_OC4"]
    header_row = next((i for i in range(1, 6) if str(ws.cell(row=i, column=1).value or "").strip() == "Action"), None)
    if header_row is None:
        return None
    hdr = [str(c.value).strip() if c.value is not None else "" for c in ws[header_row]]
    out = []
    for r in ws.iter_rows(min_row=header_row + 1, values_only=True):
        row = {h: ("" if v is None else str(v).strip()) for h, v in zip(hdr, r) if h}
        act = row.get("Action", "").strip().lower()
        if act in ACTIONS:
            row["Action"] = act
            out.append(row)
    return out


def _forms(spec):
    return [f for f in spec.get("forms") or [] if isinstance(f, dict)]


def _find(spec, form_oid, item):
    for f in _forms(spec):
        fid = str(f.get("form_id") or "")
        if form_oid in (fid, "F_" + fid) or fid == "F_" + str(form_oid):
            row = next((r for r in f.get("survey") or [] if isinstance(r, dict) and r.get("name") == item), None)
            return f, row
    return None, None


def _norm(x):
    return " ".join(str(x or "").split())


def _split(expr):
    try:
        import os, sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "dvs-specification", "scripts"))
        from extract_dvs_from_forms import _split_and_clauses
        return _split_and_clauses(expr) or [expr]
    except Exception:
        return [expr]


def _delete(spec, row):
    f, r = _find(spec, row.get("Target Form OID"), row.get("Target Item Name"))
    if r is None:
        return "not_found", "form/item not in the study"
    ctype, rule = row.get("Check Type", ""), row.get("Rule / Proposal ID", "")
    if ctype in ("Constraint", "Cross-form") and "required" not in row.get("Expression / Calculation", ""):
        target = _norm(row.get("Expression / Calculation"))
        cur = str(r.get("constraint") or "")
        if not cur:
            return "already", "no constraint on this item"
        clauses = _split(cur)
        keep = [c for c in clauses if _norm(c) != target]
        if len(keep) == len(clauses):
            if _norm(cur) == target:
                keep = []
            else:
                return "not_found", "that logic is no longer on the item"
        removed = [c for c in clauses if c not in keep]
        # engine rules that stood aside because this author clause covered them must not come back
        for cid, ref in (r.get("edit_check_covered_by") or {}).items():
            if any(ref in c for c in removed) and cid not in (r.get("edit_checks_suppressed") or []):
                r.setdefault("edit_checks_suppressed", []).append(cid)
                if cid in (r.get("edit_checks") or []):
                    r["edit_checks"].remove(cid)
        r["constraint"] = keep[0] if len(keep) == 1 else " and ".join(f"({c})" for c in keep)
        msgs = r.get("edit_check_msgs") or {}
        if rule and msgs.get(rule) and r.get("constraint_message"):
            r["constraint_message"] = _norm(str(r["constraint_message"]).replace(msgs[rule], ""))
        if not r["constraint"]:
            r.pop("constraint", None)
            r.pop("constraint_message", None)
        if rule:
            for k in ("edit_checks",):
                if rule in (r.get(k) or []):
                    r[k].remove(rule)
            (r.get("edit_check_exprs") or {}).pop(rule, None)
            if rule not in (r.get("edit_checks_suppressed") or []):
                r.setdefault("edit_checks_suppressed", []).append(rule)
        return "applied", "check removed"
    if ctype in ("Conditional Display",) or (ctype == "Cross-form" and _norm(r.get("relevant")) == _norm(row.get("Expression / Calculation"))):
        if not r.get("relevant"):
            return "already", "no show-when on this item"
        r.pop("relevant", None)
        r.setdefault("edit_check_suppressed_paths", []).append("relevant")
        return "applied", "show-when removed (always shown)"
    if ctype == "Required":
        r["required"] = "no"
        r.setdefault("edit_check_suppressed_paths", []).append("required")
        return "applied", "no longer required"
    return "unsupported", f"delete not supported for check type {ctype!r}"


def _approve(spec, row):
    import ai_edit_checks as ai
    try:
        prop = json.loads(row.get("Machine Data") or "{}")
    except Exception:
        prop = {}
    if not prop.get("id"):
        return "unsupported", "Approve applies to AI-proposed rows only"
    f, r = _find(spec, prop.get("target_form"), prop.get("target_field"))
    if r is not None and prop["id"] in (r.get("edit_checks") or []):
        return "already", "already in the build"
    if ai.apply_proposal(spec, prop):
        props = ((spec.get("study_meta") or {}).get("ai_edit_checks") or {}).get("proposals") or []
        props[:] = [p for p in props if p.get("id") != prop["id"]]
        return "applied", "added to the build"
    return "failed", "could not be applied to the current study (fields changed or an equivalent check exists)"


def _reject(spec, row):
    pid = row.get("Rule / Proposal ID") or row.get("Check ID")
    ae = spec.setdefault("study_meta", {}).setdefault("ai_edit_checks", {})
    props = ae.get("proposals") or []
    before = len(props)
    props[:] = [p for p in props if p.get("id") != pid]
    ae.setdefault("rejected_ids", [])
    if pid not in ae["rejected_ids"]:
        ae["rejected_ids"].append(pid)
    return ("applied", "proposal rejected") if len(props) < before else ("already", "proposal no longer listed")


def _change(spec, row):
    f, r = _find(spec, row.get("Target Form OID"), row.get("Target Item Name"))
    if r is None:
        return "not_found", "form/item not in the study"
    new_msg = row.get("Constraint / Required / Relevant Message", "")
    if new_msg and row.get("Check Type") in ("Constraint", "Cross-form") and new_msg != r.get("constraint_message"):
        rule = row.get("Rule / Proposal ID", "")
        if rule and (r.get("edit_check_msgs") or {}).get(rule) == new_msg:
            return "already", "message already updated"
        old_piece = (r.get("edit_check_msgs") or {}).get(rule)
        cur = str(r.get("constraint_message") or "")
        if old_piece and old_piece in cur:
            # this check's own sentence only; the field's other checks keep their messages
            r["constraint_message"] = cur.replace(old_piece, new_msg)
            r["edit_check_msgs"][rule] = new_msg
        else:
            r["constraint_message"] = new_msg
        r["constraint_message_locked"] = True  # the DM's wording: the EDC builder must not normalize it
        return "applied", "query message updated (logic unchanged)"
    return "pending", "logic changes from the plain-English description are translated in a later step"


def apply_actions(spec, rows):
    """Apply DM decisions to the spec. Returns {"applied", "already", "failed", "pending", "results": [...]}."""
    results = []
    for row in rows or []:
        act = row["Action"]
        try:
            if act == "approve":
                status, note = _approve(spec, row)
            elif act == "reject":
                status, note = _reject(spec, row)
            elif act == "delete":
                status, note = _delete(spec, row)
            elif act == "change":
                status, note = _change(spec, row)
            else:
                status, note = "pending", "plain-English additions are translated in a later step"
        except Exception as e:
            status, note = "failed", f"error: {e}"
        results.append({"action": act, "check_id": row.get("Check ID"), "rule_id": row.get("Rule / Proposal ID"),
                        "form": row.get("Target Form OID"), "item": row.get("Target Item Name"),
                        "description": row.get("Plain-English Description"), "status": status, "note": note})
    dec = spec.setdefault("study_meta", {}).setdefault("edit_check_decisions", [])
    dec.extend(results)
    summ = {k: sum(1 for r in results if r["status"] == k) for k in ("applied", "already", "failed", "pending",
                                                                      "not_found", "unsupported")}
    return {**summ, "results": results}
