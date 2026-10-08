"""cdisc_qrs.py: QRS instruments (questionnaires, ratings, scales) from CDISC Controlled Terminology. No AI here.

SDTM CT (cdisc_ct.load) publishes, per instrument, a test-code codelist ("... Test Code", e.g. PHQ01TC), its test
names, the instrument category (QSCAT / FTCAT / CCCAT) and, for part of the instruments, response codelists
("... ORRES for <items> TN/TC") with their standardized results ("... STRESC for ..."). Each STRESC term's CDISC
definition quotes the original response it standardizes, which gives the response -> numeric score pairing.

  build_index(ct)        instrument -> items (test codes, names) -> response codelist + scores
  candidates(form, ix)   instruments a form names in its title / id / notes (deterministic text match)
  tag_by_order(spec, ix) deterministic item tagging by instrument and item order (after the validated AI tags)
  apply_to_spec(...)     row["qrs"] metadata on every questionnaire item; for CDASH-default (unprotected) items the
                         instrument's response codelist becomes the choice list. The numeric score per choice is
                         recorded in row["qrs"]["scores"] (spec metadata for --STRESN; never an XLSForm column)

Tags use the concept fields of cdisc_concepts.py: concept QSORRES / FTORRES / RSORRES + qualifier = test code.
CT carries codes and response terms, not the instruments' item wording: labels of questions are never changed.
Hierarchy: customer standards -> OC standards -> CDISC. Protected fields get descriptive metadata only.
Switch: CDISC_QRS=0 (pipeline). Idempotent.
"""
import hashlib, re

import cdisc_ct

CONCEPT_BY_DOMAIN = {"QS": "QSORRES", "FT": "FTORRES", "RS": "RSORRES"}
QRS_CONCEPTS = frozenset(CONCEPT_BY_DOMAIN.values())
_CATEGORY_LISTS = (("QS", "QSCAT"), ("FT", "FTCAT"), ("RS", "CCCAT"))
_TYPE_WORDS = re.compile(r"\s+(Questionnaire|Clinical Classification|Functional Test|Check-?list)\s*$", re.I)
_RESP = re.compile(r"^(?P<head>.+) (?P<kind>ORRES|STRESC) (?P<rel>for|the Same as) (?P<target>.+) TN/TC$")
_GENERIC_TAIL = re.compile(r"\s+(Scale|Index|Inventory|Test|Score|Items?)\s*$", re.I)
_NOT_A_QUESTION = re.compile(r"\b(total|score|subscale|subtotal|sum|index)\b", re.I)
_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_ANSWER_TYPES = ("select_one", "integer", "decimal")
SOURCE = "CDISC_QRS"
NAME_MAX = 32
STRONG_KEY_MIN = 5  # a single-token key this long (or any multi-token key) identifies an instrument on its own

_MEMO = {}


def _tokens(text):
    return re.findall(r"[A-Z0-9]+", str(text or "").upper())


def _norm(text):
    return " ".join(str(text or "").lower().split()).rstrip(".").strip()


def _code_num(term):
    m = re.search(r"\d+", term.get("code") or "")
    return int(m.group(0)) if m else 0


def _number(value):
    v = str(value).strip()
    if not _NUM.match(v):
        return None
    f = float(v)
    return int(f) if f == int(f) else f


class Index:
    def __init__(self, instruments, by_testcd, responses, version):
        self.instruments, self.by_testcd, self.responses, self.version = instruments, by_testcd, responses, version

    def instrument_of(self, testcd):
        return self.instruments.get(self.by_testcd.get(str(testcd or "").upper()))


def _pair_scores(orres, stresc):
    """{ORRES value upper: number} from the STRESC definitions (each quotes the original response it standardizes).
    Returns (scores, basis); scores only for responses with an explicit, unambiguous numeric standard result."""
    if not stresc:
        return {}, None
    by_norm = {_norm(t["value"]): t for t in orres["terms"].values()}
    scores = {}
    for st in stresc["terms"].values():
        num = _number(st["value"])
        d = _norm(st.get("definition"))
        if num is None or not d:
            continue
        hits = [n for n in by_norm if n and (d.endswith("-" + n) or d.endswith("- " + n))]
        if hits:  # longest match: "-non-smoker" must not also match "smoker"
            scores.setdefault(by_norm[max(hits, key=len)]["value"].upper(), []).append(num)
    scores = {k: v[0] for k, v in scores.items() if len(set(v)) == 1}
    return scores, ("ct_stresc_definition" if scores else None)


def build_index(ct):
    """QRS index for a CT release (memoized per release object)."""
    key = id(ct)
    if key in _MEMO:
        return _MEMO[key]
    cats = {}
    for dom, cl_name in _CATEGORY_LISTS:
        for t in (ct.get(cl_name) or {}).get("terms", {}).values():
            for syn in t["synonyms"]:
                cats.setdefault(syn.upper(), (dom, t["value"], t["preferred_term"]))
    instruments, by_testcd = {}, {}
    for cl in ct.codelists.values():
        sn = cl["short_name"].upper()
        if not (sn.endswith("TC") and cl["name"].endswith("Test Code") and sn[:-2] in cats):
            continue
        inst_id = sn[:-2]
        dom, category, cat_name = cats[inst_id]
        tn = ct.get(inst_id + "TN") or {"terms": {}}
        test_by_code = {t["code"]: t["value"] for t in tn["terms"].values()}
        items = {}
        for t in sorted(cl["terms"].values(), key=lambda t: t["value"]):
            items[t["value"].upper()] = {"testcd": t["value"], "test": test_by_code.get(t["code"], ""),
                                         "name": t["preferred_term"], "code": t["code"]}
        full = re.sub(r"\s+Test Code$", "", cl["name"])
        instruments[inst_id] = {"id": inst_id, "domain": dom, "category": category, "name": _TYPE_WORDS.sub("", full),
                                "full_name": full, "category_name": cat_name, "codelist": cl["short_name"],
                                "codelist_code": cl["code"], "items": items}
        for tc in items:
            by_testcd.setdefault(tc, inst_id)
    by_name = {cl["name"]: cl for cl in ct.codelists.values()}
    responses = {}
    for cl in ct.codelists.values():
        m = _RESP.match(cl["name"])
        if not m or m.group("kind") != "ORRES":
            continue
        stresc = by_name.get(f"{m.group('head')} STRESC {m.group('rel')} {m.group('target')} TN/TC")
        target = m.group("target").strip().upper()
        if target in instruments:  # one response list for the whole instrument
            codes = list(instruments[target]["items"])
        else:
            first, _, last = target.partition(" THROUGH ")
            inst = instruments.get(by_testcd.get(first))
            if inst is None:
                continue
            # "the Same as X": CT does not list which other items share X's responses; only X is bound
            last = last if (last and m.group("rel") == "for") else first
            codes = [c for c in inst["items"] if first <= c <= last] if last in inst["items"] else [first]
        scores, basis = _pair_scores(cl, stresc)
        rec = {"orres": cl, "stresc": stresc, "scores": scores, "score_basis": basis}
        for c in codes:
            responses.setdefault(c, rec)
    ix = Index(instruments, by_testcd, responses, getattr(ct, "version", ""))
    _MEMO[key] = ix
    return ix


# ── Which instruments does a form name? ──────────────────────────────────────────

def _keys(inst):
    """(token tuple, strong?) keys an instrument can be recognized by in form text."""
    out = {}
    name = inst["name"]
    for text in (inst["category"], inst["id"], name, inst["category_name"], _GENERIC_TAIL.sub("", name)):
        toks = tuple(_tokens(_TYPE_WORDS.sub("", str(text))))
        if not toks or (len(toks) == 1 and len(toks[0]) < 3):
            continue
        strong = len(toks) > 1 or len(toks[0]) >= STRONG_KEY_MIN
        out[toks] = out.get(toks, False) or strong
    return out


def _key_table(ix):
    if not hasattr(ix, "_key_table"):
        table = {}
        for inst in ix.instruments.values():
            for toks, strong in _keys(inst).items():
                table.setdefault(toks, []).append((inst["id"], strong))
        ix._key_table = table
        ix._key_max = max((len(k) for k in table), default=0)
    return ix._key_table


def _form_text(form):
    parts = [form.get("form_title"), re.sub(r"^F_", "", str(form.get("form_id") or ""))]
    parts += [r.get("label") for r in form.get("survey") or [] if isinstance(r, dict)
              and str(r.get("type") or "").strip().lower() in ("note", "begin group")]
    return [_tokens(p) for p in parts if p]


def candidates(form, ix):
    """{instrument id: strong?} for the instruments this form names. The longest key at a position wins, so
    "PHQ-9" never also reads as a shorter key inside it."""
    table = _key_table(ix)
    found = {}
    for toks in _form_text(form):
        i = 0
        while i < len(toks):
            for n in range(min(ix._key_max, len(toks) - i), 0, -1):
                hit = table.get(tuple(toks[i:i + n]))
                if hit:
                    for inst_id, strong in hit:
                        found[inst_id] = found.get(inst_id, False) or strong
                    i += n - 1
                    break
            i += 1
    return found


def catalogue_lines(spec, ix, max_items=600):
    """Prompt catalogue for the validated AI tagging: items of the instruments the study's forms name.
    Returns (lines, {form_id: [instrument ids]})."""
    lines, per_form, done, n = [], {}, set(), 0
    for form in spec.get("forms") or []:
        if not isinstance(form, dict):
            continue
        cand = sorted(candidates(form, ix))
        if not cand:
            continue
        per_form[str(form.get("form_id"))] = cand
        for inst_id in cand:
            inst = ix.instruments[inst_id]
            if inst_id in done or n + len(inst["items"]) > max_items:
                continue
            done.add(inst_id)
            n += len(inst["items"])
            lines.append(f"INSTRUMENT {inst_id} | {inst['category']} | {inst['full_name']} | "
                         f"concept {CONCEPT_BY_DOMAIN[inst['domain']]}")
            lines += [f"  {it['testcd']} | {it['name']}" for it in inst["items"].values()]
    return lines, {f: [c for c in ids if c in done] for f, ids in per_form.items()}


# ── Response matching ────────────────────────────────────────────────────────────

def _list_name(row):
    t = str(row.get("type") or "").strip()
    kind, _, ln = t.partition(" ")
    return ln.strip() if kind.lower() == "select_one" and ln.strip() else None


def _kind(row):
    return str(row.get("type") or "").strip().lower().split(" ")[0]


def _choice_rows(form, ln):
    return [c for c in form.get("choices") or [] if isinstance(c, dict) and str(c.get("list_name") or "").strip() == ln]


def _lead_number(label):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:[-–—:=.)]|$)", str(label or ""))
    return _number(m.group(1)) if m else None


def _strip_lead(label):
    return re.sub(r"^\s*-?\d+(?:\.\d+)?\s*[-–—:=.)]\s*", "", str(label or ""))


def map_choices(resp, rows):
    """Existing choice rows -> ORRES terms. Returns {choice name: term} or None when the list is not this
    instrument's response set. By label (or stored submission value) first; otherwise by score, when the choice
    codes and the numbers leading the labels agree and every one is a score of this response list."""
    terms = resp["orres"]["terms"]
    by_norm = {_norm(t["value"]): t for t in terms.values()}
    out = {}
    for c in rows:
        t = (by_norm.get(_norm(c.get("cdisc_submission_value"))) or by_norm.get(_norm(c.get("label")))
             or by_norm.get(_norm(_strip_lead(c.get("label")))))
        if t is None:
            out = None
            break
        out[str(c.get("name"))] = t
    if out is None and resp["scores"]:
        by_score = {}
        for val, num in resp["scores"].items():
            by_score.setdefault(num, []).append(terms[val])
        out = {}
        for c in rows:
            n_name, n_label = _number(c.get("name")), _lead_number(c.get("label"))
            num = n_name if n_name is not None else n_label
            if num is None or (n_name is not None and n_label is not None and n_name != n_label) \
                    or len(by_score.get(num, [])) != 1:
                return None
            out[str(c.get("name"))] = by_score[num][0]
    if out is None or len({t["code"] for t in out.values()}) != len(out):
        return None
    return out


# ── Deterministic tagging by instrument and item order ───────────────────────────

def _is_qrs(row):
    return row.get("concept") in QRS_CONCEPTS and bool(row.get("concept_qualifier"))


def _eligible(form):
    import cdisc_concepts
    return [r for r in form.get("survey") or [] if cdisc_concepts._is_data_field(r) and _kind(r) in _ANSWER_TYPES
            and (not r.get("concept") or _is_qrs(r))]


def _fits(form, row, code, ix):
    """A field can hold this item: when CT publishes the item's responses, a select field's existing choices must
    all be responses of that list."""
    resp, ln = ix.responses.get(code), _list_name(row)
    if not resp or not ln:
        return True
    rows = _choice_rows(form, ln)
    return not rows or map_choices(resp, rows) is not None


def _order_plan(form, inst, strong, ix):
    """{id(row): test code} for untagged fields, or {} when the order is not unambiguous."""
    codes = list(inst["items"])
    fields = _eligible(form)
    anchors = {i: str(r["concept_qualifier"]).upper() for i, r in enumerate(fields)
               if _is_qrs(r) and str(r["concept_qualifier"]).upper() in inst["items"]}
    other = any(_is_qrs(r) and str(r["concept_qualifier"]).upper() not in inst["items"] for r in fields)
    plan = {}
    # 1. between two tagged items whose distance in the form equals their distance in the instrument
    pos = sorted(anchors)
    for a, b in zip(pos, pos[1:]):
        ia, ib = codes.index(anchors[a]), codes.index(anchors[b])
        if b - a > 1 and ib - ia == b - a:
            for k in range(1, b - a):
                plan[a + k] = codes[ia + k]
    # 2. the whole form is the instrument: as many answer fields as items (all items, the items CT publishes
    #    responses for, or the items that are not totals), in the same order as any tagged item
    if not other:
        with_resp = [c for c in codes if c in ix.responses]
        questions = [c for c in codes if not _NOT_A_QUESTION.search(inst["items"][c]["test"] or inst["items"][c]["name"])]
        selects = [i for i, r in enumerate(fields) if _kind(r) == "select_one"]
        for idxs, variant in ((list(range(len(fields))), codes), (selects, with_resp), (selects, questions)):
            if not variant or len(idxs) != len(variant):
                continue
            if any(anchors.get(i, c) != c for i, c in zip(idxs, variant)):
                continue
            # without tagged items, the instrument must be named unambiguously or prove itself by its responses
            proven = bool(anchors) or strong or all(
                c in ix.responses and _list_name(fields[i]) and _choice_rows(form, _list_name(fields[i]))
                for i, c in zip(idxs, variant))
            if proven:
                plan.update({i: c for i, c in zip(idxs, variant) if i not in anchors})
                break
    out = {}
    for i, code in plan.items():
        if not _fits(form, fields[i], code, ix):
            return {}
        out[id(fields[i])] = code
    return out


def tag_by_order(spec, ix):
    """Tag untagged questionnaire items by instrument and item order. Never overrides an existing tag.
    Returns counts."""
    import cdisc_concepts
    n = {"tagged": 0, "forms": 0}
    for form in spec.get("forms") or []:
        if not isinstance(form, dict):
            continue
        cand = dict(candidates(form, ix))
        for r in form.get("survey") or []:
            if isinstance(r, dict) and _is_qrs(r):
                inst = ix.instrument_of(r["concept_qualifier"])
                if inst:
                    cand.setdefault(inst["id"], False)
        plans = {}
        for inst_id in sorted(cand):
            p = _order_plan(form, ix.instruments[inst_id], cand[inst_id], ix)
            if p:
                plans[inst_id] = p
        claimed = [rid for p in plans.values() for rid in p]
        if len(claimed) != len(set(claimed)):
            continue  # two instruments claim the same field: ambiguous, left for review
        rows = {id(r): r for r in form.get("survey") or [] if isinstance(r, dict)}
        before = n["tagged"]
        for inst_id, p in plans.items():
            concept = CONCEPT_BY_DOMAIN[ix.instruments[inst_id]["domain"]]
            taken = {str(r.get("concept_qualifier")).upper() for r in rows.values() if _is_qrs(r)}
            for rid, code in p.items():
                if code not in taken:
                    n["tagged"] += cdisc_concepts._set(rows[rid], concept, "qrs_instrument", code)
        n["forms"] += n["tagged"] > before
    return n


# ── Apply: metadata, response lists, scores ──────────────────────────────────────

def _choice_name(term, score, used):
    if score is not None:
        return str(score)
    base = cdisc_ct.safe_name(term["value"])[:NAME_MAX].strip("_") or "c"
    name, k = base, 2
    while name.upper() in used:
        name, k = f"{base[:NAME_MAX - 3]}_{k}", k + 1
    return name


def _resolve(resp, existing_map, rows=None):
    """Choices for an item: the study's selection when it already uses the instrument's responses, else the
    whole response list; instrument order (CT concept order)."""
    terms = sorted(resp["orres"]["terms"].values(), key=_code_num)
    if existing_map:
        keep = {t["code"] for t in existing_map.values()}
        terms = [t for t in terms if t["code"] in keep]
    scores = {t["value"].upper(): resp["scores"].get(t["value"].upper()) for t in terms}
    nums = [s for s in scores.values() if s is not None]
    by_score = len(nums) == len(terms) and len(set(nums)) == len(nums)  # every response has its own score
    # a study that shows the grade in front of the response ("0 - Fully active") keeps showing it
    lead = {t["code"]: _lead_number(c.get("label")) for c in (rows or []) for t in [(existing_map or {}).get(str(c.get("name")))] if t}
    out, used = [], set()
    for t in terms:
        score = scores[t["value"].upper()]
        name = _choice_name(t, score if by_score else None, used)
        used.add(name.upper())
        label = f"{score} - {t['value']}" if (score is not None and lead.get(t["code"]) == score) else t["value"]
        out.append({"name": name, "label": label, "term": t, "score": score})
    return out


def _meta(ix, inst, code, resp):
    it = inst["items"][code]
    m = {"instrument": inst["id"], "category": inst["category"], "instrument_name": inst["name"],
         "domain": inst["domain"], "testcd": it["testcd"], "test": it["test"], "test_code": it["code"],
         "testcd_codelist": inst["codelist"], "testcd_codelist_code": inst["codelist_code"], "ct_version": ix.version}
    if resp:
        m.update({"orres_codelist": resp["orres"]["short_name"], "orres_codelist_code": resp["orres"]["code"]})
        if resp["stresc"]:
            m.update({"stresc_codelist": resp["stresc"]["short_name"], "stresc_codelist_code": resp["stresc"]["code"]})
    return m


def apply_to_spec(spec, std, protected_vars=None):
    """row["qrs"] on every tagged questionnaire item. For unprotected select_one items whose responses CT
    publishes: the response codelist becomes the choice list (score per choice, expressions rewritten).
    protected_vars=None means "tier unknown": metadata only, no list is changed. Returns decisions."""
    ix = build_index(std.ct)
    protected = None if protected_vars is None else {str(p).upper() for p in protected_vars}
    decisions = []
    for form in spec.get("forms") or []:
        if not isinstance(form, dict):
            continue
        fid, choices = form.get("form_id"), form.get("choices")
        made, repointed, used_inst = {}, set(), []
        for row in form.get("survey") or []:
            if not isinstance(row, dict) or not _is_qrs(row):
                continue
            code = str(row["concept_qualifier"]).upper()
            inst = ix.instrument_of(code)
            d = {"form_id": fid, "field": row.get("name"), "testcd": code}
            if inst is None or CONCEPT_BY_DOMAIN[inst["domain"]] != row.get("concept"):
                decisions.append({**d, "action": "unknown_test_code"})
                continue
            resp = ix.responses.get(code)
            meta = _meta(ix, inst, code, resp)
            if inst["id"] not in used_inst:
                used_inst.append(inst["id"])
            d["instrument"] = inst["id"]
            ln = _list_name(row)
            rows = _choice_rows(form, ln) if ln else []
            existing_map = map_choices(resp, rows) if (resp and rows) else None
            if existing_map:
                meta["scores"] = {n: resp["scores"][t["value"].upper()] for n, t in existing_map.items()
                                  if t["value"].upper() in resp["scores"]}
                if meta["scores"]:
                    meta["score_basis"] = resp["score_basis"]
            name_u = str(row.get("name") or "").upper()
            is_protected = (protected is None or name_u in protected or code in protected
                            or bool(form.get("customer_standard"))  # customer standard form: never changed
                            or any(r.get("source") == "crf_standards_injection" for r in rows))
            if not resp or not ln or not isinstance(choices, list):
                row["qrs"] = meta
                decisions.append({**d, "action": "metadata_only",
                                  "note": "no_response_codelist_in_ct" if not resp else "not_a_select_one_field"})
                continue
            if is_protected:
                row["qrs"] = meta
                decisions.append({**d, "action": "kept", "tier": "customer_or_oc_standard"
                                  if protected is not None else "tier_unknown"})
                continue
            if rows and existing_map is None:
                row["qrs"] = meta
                decisions.append({**d, "action": "left_as_is", "note": "responses_do_not_match_instrument"})
                continue
            res = _resolve(resp, existing_map, rows)
            content = tuple(c["name"] for c in res)
            new = "qrs_" + resp["orres"]["short_name"].lower()
            if len(res) != len(resp["orres"]["terms"]):
                new += "_" + hashlib.sha1("|".join(content).encode()).hexdigest()[:8]
            cur = _choice_rows(form, new)
            if (cur and not all(r.get("source") == SOURCE for r in cur)) or (new in made and made[new] != content):
                new += "_cdisc"
            if new not in made:
                made[new] = content
                choices[:] = [c for c in choices if not (isinstance(c, dict) and c.get("list_name") == new)]
                choices.extend({"list_name": new, "name": c["name"], "label": c["label"], "source": SOURCE,
                                "cdisc_submission_value": c["term"]["value"], "cdisc_code": c["term"]["code"],
                                "cdisc_codelist": resp["orres"]["code"]} for c in res)
            renames = {}
            if existing_map:
                new_by_code = {c["term"]["code"]: c["name"] for c in res}
                renames = {old: new_by_code[t["code"]] for old, t in existing_map.items()}
            n_expr = 0
            if any(o != nw for o, nw in renames.items()):
                n_expr, _ = cdisc_ct._rewrite_refs(form, row.get("name"), renames, [])
            if ln != new:
                repointed.add(ln)
                row["type"] = f"select_one {new}"
            meta["scores"] = {c["name"]: c["score"] for c in res if c["score"] is not None}
            if meta["scores"]:
                meta["score_basis"] = resp["score_basis"]
            else:
                meta.pop("scores", None)
                meta.pop("score_basis", None)
            row["qrs"] = meta
            decisions.append({**d, "action": "response_codelist_applied", "list_name": new,
                              "codelist": resp["orres"]["short_name"], "scored": bool(meta.get("scores")),
                              "expressions_rewritten": n_expr})
        if used_inst:
            form["qrs_instruments"] = [{"instrument": i, "category": ix.instruments[i]["category"],
                                        "name": ix.instruments[i]["name"], "domain": ix.instruments[i]["domain"]}
                                       for i in used_inst]
        if repointed and isinstance(choices, list):
            still = {_list_name(r) for r in form.get("survey") or [] if isinstance(r, dict)}
            still |= {str(r.get("type") or "").partition(" ")[2].strip() for r in form.get("survey") or []
                      if isinstance(r, dict)}
            form["choices"] = [c for c in choices if not (isinstance(c, dict) and c.get("list_name") in repointed
                                                          and c.get("list_name") not in still)]
    return decisions


def summarize(decisions):
    acts = {}
    for d in decisions:
        acts[d["action"]] = acts.get(d["action"], 0) + 1
    return {"items": len(decisions), "actions": acts,
            "instruments": sorted({d["instrument"] for d in decisions if d.get("instrument")}),
            "scored_items": sum(1 for d in decisions if d.get("scored"))}


def describe(row):
    """One line for DVS / reports: instrument, item and response codelist of a questionnaire item, or ""."""
    q = row.get("qrs") if isinstance(row, dict) and isinstance(row.get("qrs"), dict) else None
    if not q:
        return ""
    s = f"QRS instrument: {q.get('category')} ({q.get('instrument')}), item {q.get('testcd')}"
    if q.get("orres_codelist"):
        s += f"; response codelist {q['orres_codelist']} ({q.get('orres_codelist_code')})"
        if q.get("scores"):
            s += ", scored"
    return s + f"; CDISC CT {q.get('ct_version')}"
