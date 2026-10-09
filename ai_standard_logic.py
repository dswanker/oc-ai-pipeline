"""ai_standard_logic.py: AI-suggested logic for customer standard forms that have none (an ODM-only standard, or a
form with no constraints / show-when at all) and for obvious gaps (dates without a date-order or future check,
numeric fields without a range the protocol states, conditional fields without a show-when).

The AI never writes expressions. It returns structured suggestions; this module validates every reference (form is
a matched customer standard form, fields exist, types are compatible, choice codes are real, a range is backed by a
verbatim protocol quote) and builds the logic itself. Nothing is applied: each accepted suggestion becomes a
proposal in study_meta.standards_match.proposals (kind "ai", Check Source "AI-Suggested"), listed in DVS_OC4 with
Status "Proposed" and applied only when a data manager sets Action = Approve (standards_match.apply_proposal).
The standard's own logic is never changed. One call per run and per set of sources. Kill switch AI_STANDARD_LOGIC=0.
"""
import json, os, re
import spec_trim

import standards_match as sm

SOURCE = "AI-Suggested"
MAX_SUGGESTIONS = 80
OPS = {">=": "on or after", ">": "after", "<=": "on or before", "<": "before"}
DATE_TYPES = ("date", "datetime")
NUM_TYPES = ("integer", "decimal")
_CONDITIONAL = re.compile(r"\b(if (yes|no|other|so)\b|specify|please (describe|explain)|reason (for|not)|if not)", re.I)

PROMPT = """You are a clinical data manager reviewing eCRF forms of an OpenClinica study.

The FORMS below are a customer's standard forms, used exactly as the customer provided them. Some carry no edit
checks or show-when logic at all (marked NO LOGIC); others have gaps (fields marked GAP). Suggest ONLY the missing
logic a careful data manager would add, as structured suggestions. Never restate logic a field already has.

Kinds you may suggest:
  future_date  a date that cannot be in the future (visit, assessment, start, collection, birth dates).
               {"kind": "future_date", "form": "...", "field": "..."}
  date_order   two dates on the SAME form that must be in order (end on or after start, and the like).
               {"kind": "date_order", "form": "...", "field": "<later date>", "operator": ">=|>|<=|<",
                "other_field": "<the date it is compared with>"}
  range        a numeric field with limits the PROTOCOL TEXT states (an eligibility limit, a dose, an age).
               {"kind": "range", "form": "...", "field": "...", "min": <number or null>, "max": <number or null>,
                "protocol_quote": "<VERBATIM text from the PROTOCOL TEXT that states the limit>"}
               Suggest a range ONLY with a verbatim quote; it is checked against the protocol character by character.
  relevant     a field that is only meaningful for one answer of another field on the SAME form ("If other,
               specify", an end date shown only when not ongoing, a reason shown only when not done).
               {"kind": "relevant", "form": "...", "field": "<field to show>", "when_field": "<select field>",
                "equals": "<one choice CODE of when_field>"}

Each suggestion also carries "message" (query text shown to the site, under 120 characters; not for relevant) and
"rationale" (one sentence). Use only form ids, field names and choice codes that appear in FORMS. Suggest only
what is true for EVERY participant. Answer ONLY with JSON, no prose, no code fences:
{"suggestions": [ ... ]}
"""


def enabled():
    return os.environ.get("AI_STANDARD_LOGIC", "1") != "0"


def _kind(row):
    t = str(row.get("type") or "").strip().lower().split(" ")[0]
    return ("date" if t in DATE_TYPES else "num" if t in NUM_TYPES else "multi" if t.startswith("select_multiple")
            else "select" if t.startswith("select") else "calc" if t == "calculate" else "text")


def _codes(form, row):
    t = str(row.get("type") or "")
    if not t.startswith("select") or " " not in t:
        return []
    ln = t.split(" ", 1)[1].strip()
    return [c for c in form.get("choices") or [] if isinstance(c, dict) and c.get("list_name") == ln]


def _rows(form):
    return [r for r in form.get("survey") or [] if sm.is_data_row(r)]


def gaps(form):
    """{field name: [gap, ...]} for one matched form: what a data manager would expect and the form lacks."""
    out = {}
    rows = _rows(form)
    n_dates = sum(1 for r in rows if _kind(r) == "date")
    for r in rows:
        k, g = _kind(r), []
        if k == "date" and not str(r.get("constraint") or "").strip():
            g.append("date without a future-date check" + (" or date-order check" if n_dates > 1 else ""))
        if k == "num" and not str(r.get("constraint") or "").strip():
            g.append("number without a range")
        if k in ("text", "date", "num", "select") and not str(r.get("relevant") or "").strip() \
                and _CONDITIONAL.search(str(r.get("label") or "")):
            g.append("conditional wording without a show-when")
        if g:
            out[r["name"]] = g
    return out


def targets(spec):
    """Matched forms that lack logic entirely or have gaps: [(form, no_logic, gaps)]."""
    out = []
    for f in sm.matched_forms(spec):
        no_logic = not sm.has_logic(f.get("survey"))
        g = gaps(f)
        if no_logic or g:
            out.append((f, no_logic, g))
    return out


def already_done(spec):
    st = sm.state(spec)
    return bool(st) and (st.get("ai_logic") or {}).get("fingerprint") == st.get("fingerprint")


def build_request(spec, protocol_text="", max_protocol_chars=400_000, max_fields=250):
    """(prompt, extra_text), or None when no matched form needs logic."""
    todo = targets(spec)
    if not todo:
        return None
    lines = []
    for f, no_logic, g in todo:
        lines.append(f"\nFORM {f.get('form_id')} | {f.get('form_title')} | CDASH domain {f.get('cdash_domain') or '-'}"
                     + (" | NO LOGIC" if no_logic else ""))
        rows = _rows(f)
        for r in rows[:max_fields]:
            ch = _codes(f, r)
            bits = [str(r["name"]), str(r.get("type") or "").split(" ")[0], spec_trim.label(r.get("label"), 90)]
            if ch:
                bits.append("choices: " + "; ".join(f"{c.get('name')}={spec_trim.text(c.get('label'))}" for c in ch[:8]))
            if r.get("constraint"):
                bits.append(f"has check: {str(r['constraint'])[:80]}")
            if r.get("relevant"):
                bits.append(f"shown when: {str(r['relevant'])[:80]}")
            if r["name"] in g:
                bits.append("GAP: " + ", ".join(g[r["name"]]))
            lines.append("  " + " | ".join(bits))
        if len(rows) > max_fields:
            lines.append(f"  (+{len(rows) - max_fields} more fields not listed)")
    extra = "FORMS:" + "\n".join(lines)
    if str(protocol_text or "").strip():
        extra += "\n\nPROTOCOL TEXT (for ranges only):\n" + str(protocol_text)[:max_protocol_chars]
    return PROMPT, extra


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return int(x) if x.is_integer() else x


def _resolve(spec, s, protocol_norm):
    """Validate one suggestion. Returns (proposal, None) or (None, reject reason)."""
    if not isinstance(s, dict):
        return None, "malformed"
    form = next((f for f in sm.matched_forms(spec) if sm.norm_id(f.get("form_id")) == sm.norm_id(s.get("form"))), None)
    if form is None:
        return None, "not_a_customer_standard_form"
    rows = {r["name"]: r for r in _rows(form)}
    row = rows.get(str(s.get("field")))
    if row is None:
        return None, "unknown_field"
    kind, name, k = str(s.get("kind") or ""), row["name"], _kind(row)
    label = str(row.get("label") or name).strip().rstrip(":").strip() or name
    cur_con, cur_rel = str(row.get("constraint") or "").strip(), str(row.get("relevant") or "").strip()
    check_type, clause, msg, plain, extra = "Constraint", "", str(s.get("message") or "").strip()[:160], "", {}
    if kind == "future_date":
        if k != "date":
            return None, "incompatible_types"
        if "today()" in cur_con:
            return None, "already_checked"
        clause = ". <= today()"
        msg = msg or f"{label} cannot be in the future."
        plain = f"{label} cannot be in the future."
    elif kind == "date_order":
        other = rows.get(str(s.get("other_field")))
        op = str(s.get("operator") or "").strip()
        if other is None:
            return None, "unknown_field"
        if other is row:
            return None, "self_reference"
        if op not in OPS:
            return None, "bad_operator"
        if k != "date" or _kind(other) != "date":
            return None, "incompatible_types"
        if "${" + other["name"] + "}" in cur_con:
            return None, "already_checked"
        clause = f". = '' or ${{{other['name']}}} = '' or . {op} ${{{other['name']}}}"
        olabel = str(other.get("label") or other["name"]).strip().rstrip(":").strip()
        plain = f"{label} must be {OPS[op]} {olabel}."
        msg = msg or plain
    elif kind == "range":
        lo, hi = _num(s.get("min")), _num(s.get("max"))
        quote = str(s.get("protocol_quote") or "").strip()
        if k != "num":
            return None, "incompatible_types"
        if lo is None and hi is None:
            return None, "no_limits"
        if lo is not None and hi is not None and lo > hi:
            return None, "bad_limits"
        if cur_con:
            return None, "already_checked"
        nq = sm._norm_text(quote)
        if len(nq) < 12 or nq not in protocol_norm:
            return None, "quote_not_in_protocol"
        parts = ([f". >= {lo}"] if lo is not None else []) + ([f". <= {hi}"] if hi is not None else [])
        clause = " and ".join(parts)
        plain = (f"{label} must be between {lo} and {hi}." if lo is not None and hi is not None else
                 f"{label} must be at least {lo}." if lo is not None else f"{label} must be at most {hi}.")
        msg = msg or plain
        extra = {"protocol_reference": quote[:300]}
    elif kind == "relevant":
        when = rows.get(str(s.get("when_field")))
        code = str(s.get("equals") if s.get("equals") is not None else "")
        if when is None:
            return None, "unknown_field"
        if when is row:
            return None, "self_reference"
        if cur_rel:
            return None, "already_has_show_when"
        if _kind(when) not in ("select", "multi") or code not in [str(c.get("name")) for c in _codes(form, when)]:
            return None, "bad_condition"
        clause = (f"selected(${{{when['name']}}}, '{code}')" if _kind(when) == "multi"
                  else f"${{{when['name']}}} = '{code}'")
        choice = next(str(c.get("label")) for c in _codes(form, when) if str(c.get("name")) == code)
        wlabel = str(when.get("label") or when["name"]).strip().rstrip(":").strip()
        check_type, msg = "Conditional Display", ""
        plain = f"{label} is shown only when {wlabel} is {choice}."
    else:
        return None, "unknown_kind"
    if check_type == "Constraint":
        new = f"({cur_con}) and ({clause})" if cur_con else clause
        op = {"op": "row", "field": name, "clause": clause, "message": msg,
              "set": {"constraint": new, "constraint_message": sm._join_message(row.get("constraint_message"), msg)},
              "before": {"constraint": row.get("constraint"), "constraint_message": row.get("constraint_message")}}
    else:
        op = {"op": "row", "field": name, "set": {"relevant": clause}, "before": {"relevant": row.get("relevant")}}
    pid = sm.proposal_id("ai", form.get("form_id"), name, check_type, clause)
    return {"id": pid, "kind": "ai", "convention_id": None, "check_id": pid, "source": SOURCE, "category": kind,
            "title": plain, "plain": plain, "target_form": form.get("form_id"), "target_field": name,
            "check_type": check_type, "logic": clause, "message": msg,
            "rationale": str(s.get("rationale") or "")[:300], "protocol_reference": extra.get("protocol_reference", ""),
            "ops": [op]}, None


def validate_response(spec, text, protocol_text=""):
    """Validate the AI's suggestions WITHOUT applying anything. Returns {"proposed", "proposals", "rejected"}."""
    rejects, out, seen = {}, [], set()

    def reject(why):
        rejects[why] = rejects.get(why, 0) + 1

    try:
        raw = sm._parse_json(text, "suggestions")
    except Exception:
        return {"proposed": 0, "proposals": [], "rejected": {"unparseable_response": 1}}
    proto = sm._norm_text(protocol_text)
    # logic the rules engine already proposes for the same field is not proposed twice
    engine = {(sm.norm_id(p.get("target_form")), p.get("target_field"), " ".join(str(p.get("logic") or "").split()))
              for p in sm.proposals(spec) if p.get("kind") == "engine"}
    st = sm.state(spec)
    rejected_ids = set(st.get("rejected_ids") or [])
    for s in raw:
        if len(out) >= MAX_SUGGESTIONS:
            reject("over_cap"); continue
        prop, why = _resolve(spec, s, proto)
        if why:
            reject(why); continue
        key = (sm.norm_id(prop["target_form"]), prop["target_field"], " ".join(prop["logic"].split()))
        if prop["id"] in seen:
            reject("duplicate"); continue
        if prop["id"] in rejected_ids:
            reject("rejected_by_dm_earlier"); continue
        if key in engine:
            reject("already_proposed_by_rules_engine"); continue
        seen.add(prop["id"])
        out.append(prop)
    return {"proposed": len(raw), "proposals": out, "rejected": rejects}


def store(spec, result):
    """Record the validated proposals (never applied) and remember that this set of sources was done."""
    st = sm.state(spec)
    if not st:
        return
    sm.set_proposals(spec, "ai", result.get("proposals") or [])
    st["ai_logic"] = {"fingerprint": st.get("fingerprint"), "proposed": result.get("proposed", 0),
                      "accepted": len(result.get("proposals") or []), "rejected": result.get("rejected") or {}}
