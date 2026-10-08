"""ai_edit_checks.py: AI-proposed edit checks (sequential dates, cross-form, protocol timing), validated and applied
deterministically. The AI never writes expressions: it proposes structured checks; this module validates every
reference and builds the logic with the conventions engine's own effects (lookup_from + add_constraint).

Each applied check: field.edit_checks gets "AI.nnn"; field.edit_check_details gets source/rationale/protocol
reference (shown in the DVS). Summary in study_meta.ai_edit_checks. Runs after the conventions engine, once per
spec (a spec that already has study_meta.ai_edit_checks is not re-proposed).
"""
import json, re

OPS = {">=", ">", "<=", "<", "=", "!="}
DATE_TYPES = ("date", "datetime")
NUM_TYPES = ("integer", "decimal")
MAX_CHECKS = 40

PROMPT = """You are a clinical data manager designing edit checks for an OpenClinica EDC study.

The STUDY below is fully defined (forms, visits, fields, CDISC concepts, existing checks). Propose ADDITIONAL data
cleaning checks that a careful data manager would add and that are NOT already present, especially:
  - sequential_dates: dates that must be in order (e.g. event -> awareness -> report; screening -> randomization ->
    first dose), within a form or across forms;
  - cross_form: values on one form that must be consistent with another form (dates, numbers);
  - protocol_timing: protocol-driven ordering between visits/forms.
Only propose checks you are confident are true for EVERY participant. Do not propose checks that duplicate the
existing constraints shown. Do not propose AE/medication dates vs first dose (those may legitimately precede dosing).

Answer ONLY with JSON, no prose, no code fences:
{"checks": [{"target_form": "...", "target_field": "...", "operator": ">=|>|<=|<|=|!=",
             "source_form": "...", "source_field": "...",
             "when": {"field": "<field on the target form>", "equals": "<choice code>"} (optional),
             "message": "<query text shown to the site, under 120 characters>",
             "rationale": "<one sentence>", "protocol_reference": "<section or empty>",
             "category": "sequential_dates|cross_form|protocol_timing"}]}
The check reads: target_field OPERATOR source_field (only when the optional condition holds). Use only form ids,
field names and choice codes that appear in the STUDY. Compare dates only with dates and numbers only with numbers.
"""


def _is_data(row):
    t = str(row.get("type") or "").lower()
    return (isinstance(row, dict) and row.get("name") and t not in ("note", "calculate", "begin group", "end group",
            "begin repeat", "end repeat") and not str(row["name"]).upper().endswith(("_CF", "_SF")))


def _kind(row):
    t = str(row.get("type") or "").lower().split(" ")[0]
    return "date" if t in DATE_TYPES else "num" if t in NUM_TYPES else "select" if t.startswith("select") else "text"


def _codes(form, row):
    t = str(row.get("type") or "")
    if " " not in t:
        return []
    ln = t.split(" ", 1)[1].strip()
    return [c for c in form.get("choices") or [] if isinstance(c, dict) and c.get("list_name") == ln]


def build_request(spec):
    sm = spec.get("study_meta") or {}
    head = {k: sm.get(k) for k in ("protocol_number", "study_title", "title", "phase", "design", "indication",
                                   "therapeutic_area") if sm.get(k)}
    lines = [f"STUDY: {json.dumps(head)}"]
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        lines.append(f"\nFORM {f.get('form_id')} | {f.get('form_title', '')} | visits: {', '.join(f.get('visits_assigned') or [])}")
        for r in f.get("survey") or []:
            if not _is_data(r):
                continue
            ch = _codes(f, r)
            chs = f" | choices: {'; '.join(str(c.get('name')) + '=' + str(c.get('label')) for c in ch[:6])}" if ch else ""
            con = f" | existing check: {str(r.get('constraint'))[:90]}" if r.get("constraint") else ""
            concept = f" | concept {r['concept']}" if r.get("concept") else ""
            lines.append(f"  {r['name']} | {str(r.get('type')).split(' ')[0]} | {str(r.get('label') or '')[:80]}{concept}{chs}{con}")
    return PROMPT, "\n".join(lines)


def _parse(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
    try:
        data = json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        data = json.loads(m.group(0)) if m else {}
    return data.get("checks") if isinstance(data, dict) else []


def _resolve(spec, p):
    """Validate one proposal against the study. Returns (normalized proposal, None) or (None, reject reason)."""
    forms = {str(f.get("form_id")): f for f in spec.get("forms") or [] if isinstance(f, dict)}
    if not isinstance(p, dict):
        return None, "malformed"
    tf, sf = forms.get(str(p.get("target_form"))), forms.get(str(p.get("source_form")))
    op = str(p.get("operator") or "").strip()
    if tf is None or sf is None:
        return None, "unknown_form"
    tr = next((r for r in tf.get("survey") or [] if isinstance(r, dict) and r.get("name") == p.get("target_field")), None)
    sr = next((r for r in sf.get("survey") or [] if isinstance(r, dict) and r.get("name") == p.get("source_field")), None)
    if tr is None or sr is None or not _is_data(tr) or not _is_data(sr):
        return None, "unknown_field"
    if tr is sr:
        return None, "self_reference"
    if op not in OPS:
        return None, "bad_operator"
    kt, ks = _kind(tr), _kind(sr)
    if kt != ks or (kt in ("select", "text") and op not in ("=", "!=")):
        return None, "incompatible_types"
    same = tf is sf
    src = sr["name"] if same else f"{sr['name']}_CF"
    con = str(tr.get("constraint") or "")
    if "${" + src + "}" in con or "${" + sr["name"] + "}" in con:
        return None, "already_checked"
    logic = f". = '' or ${{{src}}} = '' or . {op} ${{{src}}}"
    when = p.get("when") if isinstance(p.get("when"), dict) and p.get("when").get("field") else None
    if when:
        wr = next((r for r in tf.get("survey") or [] if isinstance(r, dict) and r.get("name") == when.get("field")), None)
        if wr is None or str(when.get("equals")) not in [str(c.get("name")) for c in _codes(tf, wr)]:
            return None, "bad_condition"
        logic = f"${{{wr['name']}}} != '{when['equals']}' or ({logic})"
        when = {"field": wr["name"], "equals": str(when["equals"])}
    msg = (str(p.get("message") or "").strip()[:160]
           or f"{tr.get('label') or tr['name']} is inconsistent with {sr.get('label') or sr['name']}.")
    return {"target_form": tf.get("form_id"), "target_field": tr["name"], "operator": op,
            "source_form": sf.get("form_id"), "source_field": sr["name"], "when": when, "message": msg,
            "logic": logic, "cross_form": None if same else f"{sf.get('form_id')}.{sr['name']}",
            "rationale": str(p.get("rationale") or "")[:300],
            "protocol_reference": str(p.get("protocol_reference") or "")[:120],
            "category": str(p.get("category") or "")}, None


def validate_response(spec, text, id_prefix="AI"):
    """Validate the AI's proposals WITHOUT applying them. Returns {"proposed", "proposals", "rejected"}; each
    proposal carries an id (AI.nnn), the exact logic it would add and its cross-form dependency."""
    rejects, out, seen = {}, [], set()
    try:
        raw = _parse(text) or []
    except Exception:
        return {"proposed": 0, "proposals": [], "rejected": {"unparseable_response": 1}}
    for p in raw:
        if len(out) >= MAX_CHECKS:
            rejects["over_cap"] = rejects.get("over_cap", 0) + 1
            continue
        norm, why = _resolve(spec, p)
        if why:
            rejects[why] = rejects.get(why, 0) + 1
            continue
        key = (norm["target_form"], norm["target_field"], norm["operator"], norm["source_form"], norm["source_field"])
        if key in seen:
            rejects["duplicate"] = rejects.get("duplicate", 0) + 1
            continue
        seen.add(key)
        out.append({"id": f"{id_prefix}.{len(out) + 1:03d}", **norm})
    return {"proposed": len(raw), "proposals": out, "rejected": rejects}


def apply_proposal(spec, prop, source="AI-Proposed"):
    """Apply one approved proposal through the conventions engine (lookup_from + add_constraint).
    Re-validates against the current study first. Returns True when the check is in the build."""
    from conventions_engine import EntityContext
    from conventions_engine import effects
    norm, why = _resolve(spec, prop)
    if why:
        return False
    forms = {str(f.get("form_id")): f for f in spec.get("forms") or [] if isinstance(f, dict)}
    tf, sf = forms[str(norm["target_form"])], forms[str(norm["source_form"])]
    tr = next(r for r in tf["survey"] if isinstance(r, dict) and r.get("name") == norm["target_field"])
    cid = prop["id"]
    src = norm["source_field"] if tf is sf else f"{norm['source_field']}_CF"
    effect = {"add_constraint": {"expr": norm["logic"], "message": norm["message"], "check_id": cid,
                                 "requires_field": src}}
    if tf is not sf:
        effect = {"lookup_from": {"from": f"{norm['source_form']}.{norm['source_field']}", "name": src,
                                  "purpose": f"{source} check {cid}"}, **effect}
    ctx = EntityContext(kind="field", entity=tr, parent=tf, spec=spec, path=f"edit_check:{cid}")
    effects.apply_effect(effect, ctx, spec, f"edit_checks.{cid}")
    if cid not in (tr.get("edit_checks") or []):
        return False
    tr.setdefault("edit_check_details", []).append(
        {"id": cid, "source": source, "category": norm["category"], "rationale": norm["rationale"],
         "protocol_reference": norm["protocol_reference"]})
    return True


def apply_response(spec, text):
    """Validate and apply every proposal (used by tests and explicit opt-in paths)."""
    v = validate_response(spec, text)
    applied = [p for p in v["proposals"] if apply_proposal(spec, p)]
    return {"proposed": v["proposed"], "applied": len(applied), "rejected": v["rejected"], "checks": applied}
