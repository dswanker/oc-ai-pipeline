"""Item-level SDV proposals for the Study Configuration (study_config.py).

Per form: every data field is Required, Optional or Not Applicable for source data verification.
  Required        critical-to-quality data: informed consent date, eligibility, primary / key secondary endpoint
                  data, investigational product / prodrug exposure and dosing, adverse event seriousness, outcome
                  and dates, death, disposition, randomization / group assignment
  Not Applicable  calculated, hidden, derived items and notes
  Optional        everything else

Fields are identified by their concept tag (cdisc_concepts) or CDASH-style name and by the form's CDASH domain.
Only the link from the protocol's endpoints to fields uses AI: one validated call, each endpoint with a verbatim
quote verified against the protocol text (STUDY_CONFIG_SDV_AI=0 skips it).

These are PROPOSALS, documented in the Study Configuration with their rationale. They are written to the design
board JSON (card.sdv = "item_level", card.sdvItems) only when STUDY_CONFIG_SDV=1.
"""
from __future__ import annotations
import json
import os
import re

REQUIRED, OPTIONAL, NA = "Required", "Optional", "Not Applicable"
LEVEL_ITEMS = "item_level"
LEVEL_NONE = "not_applicable_item_level"
SRC_DEFAULT, SRC_AI = "pipeline default", "AI-proposed"
MAX_ENDPOINTS = 12
_STRUCT = ("begin group", "end group", "begin repeat", "end repeat", "begin_group", "end_group", "begin_repeat",
           "end_repeat", "note", "calculate")
_DERIVED_NAME = re.compile(r"(_?CALC|_CF|_DISP)$", re.I)
_VALUE_TYPES = ("date", "datetime", "time", "integer", "decimal", "select_one", "select_multiple")


def board_enabled():
    """Write sdvItems to the design board JSON. Off unless STUDY_CONFIG_SDV=1: the designer's import of sdvItems
    has not been confirmed (it can only be confirmed by importing into OpenClinica)."""
    return os.environ.get("STUDY_CONFIG_SDV", "0") == "1"


def ai_enabled():
    return os.environ.get("STUDY_CONFIG_SDV_AI", "1") != "0"


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _type(row):
    return str(row.get("type") or "").strip().lower()


def is_data_field(row):
    return isinstance(row, dict) and bool(row.get("name")) and not _type(row).startswith(_STRUCT)


def _not_applicable(row):
    """(True, reason) for calculated, hidden, derived items and notes."""
    t = _type(row)
    if t.startswith(_STRUCT):
        return True, "calculated item or note"
    if str(row.get("calculation") or "").strip():
        return True, "derived (calculated) value"
    if "hidden" in str(row.get("appearance") or "").lower():
        return True, "hidden item"
    if str(row.get("bind__oc_external") or "").strip():
        return True, "value loaded from outside the form"
    if _DERIVED_NAME.search(str(row.get("name") or "")):
        return True, "derived (calculated) value"
    return False, ""


def _keys(row):
    """Upper-case names a rule may match: the concept tag, the field name and its parts (SYSBP_VSORRES)."""
    out = []
    for v in (row.get("concept"), row.get("name")):
        v = str(v or "").upper()
        if v:
            out.append(v)
            out += [p for p in v.split("_") if p]
    return out


def _domains(form):
    doms = [d for d in re.split(r"[^A-Z0-9]+", str(form.get("cdash_domain") or "").upper()) if d]
    cs = form.get("customer_standard") or {}
    if cs.get("domain"):
        doms.append(str(cs["domain"]).upper())
    return set(doms)


_AE = re.compile(r"^(AESER|AESDTH|AESLIFE|AESHOSP|AESDISAB|AESCONG|AESMIE|AESOD|AESCAN|AESERCRIT|AEOUT|AESTDAT|"
                 r"AEENDAT|AESTDTC|AEENDTC|AESERSTDAT|AESERENDAT)$")
_DEATH = re.compile(r"^(DTHDAT|DTHDTC|DTHFL|DDDAT|DDORRES|DDTEST|AESDTH|DTHCAUS|DEATHDAT)$")
_DISP = re.compile(r"^(DSDECOD|DSTERM|DSSTDAT|DSSTDTC|DSCAT|DSSCAT)$")
_RAND = re.compile(r"^(RAND|RANDDAT|RANDDTC|RANDNO|RANDNUM|RANDID|ARM|ARMCD|ACTARM|ACTARMCD|TRTGRP|COHORT|KITNO|KITID)$")
_CONSENT = re.compile(r"^(RFICDAT|RFICDTC|ICFDAT|ICDAT|CONSDAT|CONSENTDAT|DSSTDAT_IC|ICFDTC)$")


def classify(form, row):
    """(level, rationale) for one survey row by rule; endpoint links are added by propose()."""
    na, why = _not_applicable(row)
    if na:
        return NA, why
    keys, doms, t = _keys(row), _domains(form), _type(row)
    valued = t.startswith(_VALUE_TYPES)
    title = str(form.get("form_title") or "")
    if any(_CONSENT.match(k) for k in keys) or (re.search(r"consent", title, re.I) and t.startswith("date")):
        return REQUIRED, "Critical to quality: informed consent date"
    if any(_DEATH.match(k) for k in keys) or ("DD" in doms and valued):
        return REQUIRED, "Critical to quality: death"
    if any(_AE.match(k) for k in keys):
        return REQUIRED, "Critical to quality: adverse event seriousness, outcome and dates"
    if any(_RAND.match(k) for k in keys):
        return REQUIRED, "Critical to quality: randomization / group assignment"
    if "DS" in doms and (any(_DISP.match(k) for k in keys) or valued):
        return REQUIRED, "Critical to quality: disposition"
    if "IE" in doms and valued:
        return REQUIRED, "Critical to quality: eligibility"
    if doms & {"EX", "EC"} and (valued or any(re.search(r"DOSE|DOSU|TRT|LOT", k) for k in keys)):
        return REQUIRED, "Critical to quality: investigational product exposure and dosing"
    return OPTIONAL, "Not critical to quality by rule"


# ── Endpoints -> fields (validated AI) ───────────────────────────────────────────

PROMPT = """You link the PRIMARY and KEY SECONDARY endpoints of a clinical trial protocol to eCRF fields.

For each primary endpoint and each key secondary endpoint stated in the protocol (objectives / endpoints section):
- "endpoint": a short name
- "kind": "primary" or "key secondary"
- "quote": the endpoint copied VERBATIM from the protocol (one sentence or fragment, at most 300 characters). An
  entry whose quote is not found in the protocol text is discarded.
- "fields": the eCRF fields that hold the data the endpoint is computed from, each as "FORM_ID.FIELD_NAME", using
  ONLY forms and fields from the FIELDS list below. Do not list a field merely related to the topic; list the
  values the endpoint is actually derived from. Empty when no field holds that data.
At most 12 endpoints. Return ONLY JSON:
{"endpoints": [{"endpoint": "...", "kind": "primary", "quote": "...", "fields": ["LB.PSAORRES"]}]}
"""


def build_request(spec, protocol_text, max_fields=4000, max_chars=600_000, with_text=True):
    if not str(protocol_text or "").strip():
        return None
    lines, n = [], 0
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        for r in f.get("survey") or []:
            if is_data_field(r) and not _not_applicable(r)[0] and n < max_fields:
                lines.append(f"{f.get('form_id')}.{r.get('name')} | {str(r.get('label') or '')[:80]}")
                n += 1
    if not lines:
        return None
    extra = "FIELDS (FORM_ID.FIELD_NAME | label):\n" + "\n".join(lines)
    if with_text:
        extra += "\n\nPROTOCOL TEXT:\n" + str(protocol_text)[:max_chars]
    return PROMPT, extra


def validate_response(spec, response_text, protocol_text):
    """{"links": {(form_id, field): {...}}, "endpoints": [...], "rejected": {reason: n}}."""
    rejected, links, kept = {}, {}, []

    def rej(why):
        rejected[why] = rejected.get(why, 0) + 1

    m = re.search(r"\{.*\}", str(response_text or ""), re.S)
    try:
        items = json.loads(m.group(0)).get("endpoints") if m else None
    except Exception:
        items = None
    if not isinstance(items, list):
        return {"links": {}, "endpoints": [], "rejected": {"unparseable response": 1}}
    known = {}
    for f in spec.get("forms") or []:
        if isinstance(f, dict):
            for r in f.get("survey") or []:
                if is_data_field(r) and not _not_applicable(r)[0]:
                    known[(str(f.get("form_id")), str(r.get("name")))] = True
    squashed = _squash(protocol_text)
    for it in items:
        if not isinstance(it, dict) or not str(it.get("endpoint") or "").strip():
            rej("no endpoint name")
            continue
        if len(kept) >= MAX_ENDPOINTS:
            rej("over the limit")
            continue
        quote = str(it.get("quote") or "").strip()
        q = _squash(quote)
        if len(q) < 12 or q not in squashed:
            rej("quote not found in the protocol")
            continue
        kind = "primary" if str(it.get("kind") or "").strip().lower() == "primary" else "key secondary"
        fields = []
        for ref in it.get("fields") or []:
            fid, _, name = str(ref).partition(".")
            if (fid, name) in known:
                fields.append((fid, name))
            else:
                rej("unknown form or field")
        ep = {"endpoint": str(it["endpoint"]).strip()[:120], "kind": kind, "quote": quote[:400],
              "fields": [f"{a}.{b}" for a, b in fields]}
        kept.append(ep)
        for key in fields:
            links.setdefault(key, ep)
    return {"links": links, "endpoints": kept, "rejected": rejected}


# ── Proposals into the Study Configuration ───────────────────────────────────────

def form_items(form, links=None):
    """[{item, label, sdv, rationale, source, quote?}] for every named row of a form."""
    out = []
    for r in form.get("survey") or []:
        if not isinstance(r, dict) or not r.get("name") or _type(r).startswith(("begin", "end")):
            continue
        level, why = classify(form, r)
        rec = {"item": str(r["name"]), "label": re.sub(r"<[^>]+>", "", str(r.get("label") or ""))[:120], "sdv": level,
               "rationale": why, "source": SRC_DEFAULT}
        ep = (links or {}).get((str(form.get("form_id")), str(r["name"])))
        if ep and level != NA and level != REQUIRED:
            rec.update(sdv=REQUIRED, source=SRC_AI, quote=ep["quote"],
                       rationale=f"Critical to quality: {ep['kind']} endpoint data ({ep['endpoint']})")
        elif ep and level == REQUIRED:
            rec["rationale"] += f"; also {ep['kind']} endpoint data ({ep['endpoint']})"
        out.append(rec)
    return out


def propose(spec, cfg, endpoint_result=None):
    """Fill cfg["sdv_items"] (per form) and each form-at-event's sdv (level + Required items). Values a data manager
    set (source "DM") on an item are kept. Returns counts."""
    links = (endpoint_result or {}).get("links") or {}
    if endpoint_result is None:  # rebuild: keep the endpoint links verified earlier
        for ep in cfg.get("sdv_endpoints") or []:
            for ref in ep.get("fields") or []:
                fid, _, name = str(ref).partition(".")
                links.setdefault((fid, name), ep)
    prev = cfg.get("sdv_items") if isinstance(cfg.get("sdv_items"), dict) else {}
    per_form, counts = {}, {REQUIRED: 0, OPTIONAL: 0, NA: 0}
    for form in spec.get("forms") or []:
        if not isinstance(form, dict) or not form.get("form_id"):
            continue
        items = form_items(form, links)
        dm = {i.get("item"): i for i in (prev.get(form["form_id"]) or {}).get("items") or [] if i.get("source") == "DM"}
        items = [dm.get(i["item"], i) for i in items]
        level = LEVEL_ITEMS if any(i["sdv"] in (REQUIRED, OPTIONAL) for i in items) else LEVEL_NONE
        per_form[form["form_id"]] = {"level": level, "items": [i for i in items if i["sdv"] != NA],
                                     "not_applicable": sum(1 for i in items if i["sdv"] == NA)}
    cfg["sdv_items"] = per_form
    cfg["sdv_endpoints"] = (endpoint_result or {}).get("endpoints") or cfg.get("sdv_endpoints") or []
    seen = set()
    for card in cfg.get("forms") or []:
        pf = per_form.get(card.get("form_id"))
        if not pf:
            continue
        if (card.get("sdv") or {}).get("level", {}).get("source") != "DM":
            card["sdv"] = {"level": {"value": pf["level"], "source": SRC_DEFAULT,
                                     "note": "item-level SDV proposal; see SDV ITEMS"},
                           "items": [{"item": i["item"], "sdv": i["sdv"], "rationale": i["rationale"], "source": i["source"]}
                                     for i in pf["items"] if i["sdv"] == REQUIRED]}
        if card.get("form_id") not in seen:
            seen.add(card.get("form_id"))
            for i in pf["items"]:
                counts[i["sdv"]] += 1
            counts[NA] += pf["not_applicable"]
    return counts


SDV_HEADERS = ["Form", "Item", "Label", "SDV", "Rationale", "Source"]


def sdv_rows(cfg):
    rows = []
    for fid, pf in (cfg.get("sdv_items") or {}).items():
        req = [i for i in pf.get("items") or [] if i.get("sdv") == REQUIRED]
        for i in req:
            rows.append([fid, i["item"], i.get("label") or "", REQUIRED, i.get("rationale") or "",
                         (i.get("source") or "") + (f': "{i["quote"]}"' if i.get("quote") else "")])
        n_opt = sum(1 for i in pf.get("items") or [] if i.get("sdv") == OPTIONAL)
        rows.append([fid, f"({n_opt} other item(s))" if n_opt else "(no other items)", "",
                     OPTIONAL if n_opt else "—",
                     f"{pf.get('not_applicable', 0)} calculated / hidden / derived item(s) and notes: Not Applicable"
                     + ("" if req or n_opt else "; form SDV: not applicable"), SRC_DEFAULT])
    return rows


# ── Design board JSON (confirmed OC4 card format, 2026-10-08) ────────────────────

def _prefix(form_title):
    """The 5-character prefix OpenClinica uses in item and item group OIDs (first 5 alphanumerics of the title)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(form_title or "")).upper()[:5]


def _item_type(row):
    t = _type(row)
    return ("date" if t.startswith("date") else "integer" if t == "integer" else "real" if t == "decimal"
            else "select" if t.startswith("select") else "text")


def board_items(spec, form):
    """card.sdvItems for a form, or None when the spec has no SDV proposals for it. Not Applicable items are left
    out. boardId / listId / cardId / _id are assigned by OpenClinica."""
    cfg = spec.get("study_configuration") if isinstance(spec, dict) else None
    pf = ((cfg or {}).get("sdv_items") or {}).get(form.get("form_id"))
    if pf is None:
        return None
    rows = {str(r.get("name")): r for r in form.get("survey") or [] if isinstance(r, dict) and r.get("name")}
    prefix = _prefix(form.get("form_title"))
    fid = str(form.get("form_id") or "")
    form_oid = fid if fid.upper().startswith("F_") else f"F_{fid}"
    version = str((form.get("settings") or {}).get("version") or "1")
    in_repeat, repeating = False, {}
    for r in form.get("survey") or []:
        if isinstance(r, dict):
            t = _type(r)
            if t.startswith("begin repeat") or t.startswith("begin_repeat"):
                in_repeat = True
            elif t.startswith("end repeat") or t.startswith("end_repeat"):
                in_repeat = False
            elif r.get("name"):
                repeating[str(r["name"])] = in_repeat
    out = []
    for i in pf.get("items") or []:
        r = rows.get(i["item"])
        if r is None or i.get("sdv") not in (REQUIRED, OPTIONAL):
            continue
        group = str(r.get("bind__oc_itemgroup") or "MAIN")
        out.append({"ocoid": f"I_{prefix}_{str(i['item']).upper()}", "name": i["item"], "formOID": form_oid,
                    "versions": [version], "itemGroupName": group, "itemGroupOid": f"IG_{prefix}_{group.upper()}",
                    "itemType": _item_type(r), "itemLabel": i.get("label") or "",
                    "repeating": bool(repeating.get(i["item"])), "sdv": i["sdv"].lower()})
    return out


def card_sdv(spec, form):
    """{"sdv", "itemLevelSdv", "sdvItems"?} for a board card, or None to keep the legacy card (flag off, or no
    proposals in the spec)."""
    if not board_enabled():
        return None
    items = board_items(spec, form)
    if items is None:
        return None
    if not items:
        return {"sdv": LEVEL_NONE, "itemLevelSdv": True}
    return {"sdv": LEVEL_ITEMS, "itemLevelSdv": True, "sdvItems": items}
