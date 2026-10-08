"""usdm_input.py: CDISC USDM 4.0 (DDF-RA) as an accepted input. Deterministic, no AI.

When a sponsor provides a USDM JSON, the visit structure is taken from it instead of being inferred:

  Encounter                                   -> study events (SE_*), in encounter order
  ScheduledActivityInstance (encounter + activities, sub-timelines, child activities)
                                              -> form placements (form.visits_assigned)
  Timing (value, type, windowLower/Upper)     -> spec["scheduling"] (offset and window in days; the calendaring
                                                 rules read it, and the AI scheduling pass is skipped)
  StudyArm / StudyEpoch / StudyCell           -> study_meta.arms, arm applicability per event
  EligibilityCriterion (+ Item)               -> inclusion / exclusion criteria rows (context for the IE form)

Activity -> form is many-to-many and is resolved by a general rule layer (never study-specific):
  1. Biomedical Concept codes  -> CDASH domain (CDISC COSMoS)   -> forms of that domain
  2. customer FORMS convention -> form key by form name (FORMS.csv), when provided
  3. CDISC domain names        -> domain (CT "SDTM Domain Abbreviation") -> forms of that domain
  4. form names                -> the activity names the form
  5. QRS instrument names      -> instrument (cdisc_qrs) -> forms that name it, else forms of its domain
  6. CDISC test names          -> domain of the CT test-name codelist (VSTEST, EGTEST, LBTEST ...) -> forms
  7. CDISC domain-name prefix  -> the one domain whose name starts with the activity name
Activities no rule resolves are listed for review (study_meta.usdm, review_flags.usdm_review), never guessed.

Only usdmVersion 4.0.x is accepted. Schema check: required attributes and cardinalities of every USDM class,
from DDF-RA dataStructure.yml (cdisc_standards/usdm/usdm_4_0_classes.json).
Content based on DDF-RA (GitHub) used under the CC-BY-4.0 license.
Switch: USDM_INPUT=0 (pipeline). Idempotent; the pipeline leaves the spec unchanged on any error.
"""
import csv, html, json, math, os, re

_HERE = os.path.dirname(os.path.abspath(__file__))
SUPPORTED = (4, 0)
OID_MAX = 40
_KEEP_EVENT = re.compile(r"COMMON|UNSCH", re.I)  # non-calendar events owned by the existing pipeline rules
_INCLUSION, _EXCLUSION = "C25532", "C25370"
_MEMO = {}


class UsdmError(ValueError):
    """The USDM input cannot be used (wrong version, not USDM, no study design)."""


# ── Load, version, schema ────────────────────────────────────────────────────────

def load(data):
    if isinstance(data, dict):
        return data
    try:
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8-sig")
        doc = json.loads(data)
    except Exception as e:
        raise UsdmError(f"USDM input rejected: the file is not valid JSON ({e})")
    if not isinstance(doc, dict):
        raise UsdmError("USDM input rejected: the JSON is not a USDM document (expected an object with 'study')")
    return doc


def check_version(doc):
    v = str(doc.get("usdmVersion") or "").strip()
    m = re.match(r"^(\d+)\.(\d+)(?:\.\d+)?$", v)
    if not m or (int(m.group(1)), int(m.group(2))) != SUPPORTED:
        raise UsdmError(f"USDM input rejected: usdmVersion {v or '(missing)'!r} is not supported. "
                        f"This pipeline accepts USDM 4.0.x only.")
    return v


def _classes():
    if "classes" not in _MEMO:
        with open(os.path.join(_HERE, "cdisc_standards", "usdm", "usdm_4_0_classes.json")) as fh:
            _MEMO["classes"] = json.load(fh)["classes"]
    return _MEMO["classes"]


def validate(doc, limit=200):
    """Schema problems (required attribute missing, wrong cardinality), as 'path: problem' strings."""
    classes, problems = _classes(), []
    if not isinstance(doc.get("study"), dict):
        return ["study: missing"]

    def walk(obj, path):
        if len(problems) >= limit:
            return
        if isinstance(obj, list):
            for i, x in enumerate(obj):
                walk(x, f"{path}[{i}]")
            return
        if not isinstance(obj, dict):
            return
        spec = classes.get(obj.get("instanceType"))
        if spec:
            for attr, (card, _types) in spec.items():
                val = obj.get(attr)
                upper = card.split("..")[-1]
                many = upper == "*" or (upper.isdigit() and int(upper) > 1)
                if card in ("1", "1..*") and (val is None or (many and val == [])):
                    problems.append(f"{path}.{attr}: required ({obj.get('instanceType')}, cardinality {card})")
                elif val is not None and many != isinstance(val, list):
                    problems.append(f"{path}.{attr}: expected {'a list' if many else 'a single value'} "
                                    f"({obj.get('instanceType')}, cardinality {card})")
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                walk(v, f"{path}.{k}")

    walk(doc["study"], "study")
    return problems[:limit]


# ── Extract ──────────────────────────────────────────────────────────────────────

def _chain(items):
    """Order by the previousId / nextId chain; anything outside the chain keeps list order."""
    by_id = {x.get("id"): x for x in items}
    out, seen = [], set()
    for head in [x for x in items if not x.get("previousId")]:
        cur = head
        while cur is not None and cur.get("id") not in seen:
            seen.add(cur.get("id"))
            out.append(cur)
            cur = by_id.get(cur.get("nextId"))
    return out + [x for x in items if x.get("id") not in seen]


def _oid(text, used, prefix="SE_"):
    s = re.sub(r"(?<![A-Za-z0-9])-(?=\d)", " MINUS ", str(text or ""))
    s = re.sub(r"[^A-Z0-9]+", "_", s.upper()).strip("_") or "EVENT"
    base = (prefix + s)[:OID_MAX].rstrip("_")
    name, k = base, 2
    while name in used:
        tail = f"_{k}"
        name, k = base[:OID_MAX - len(tail)] + tail, k + 1
    used.add(name)
    return name


def duration_days(value):
    """ISO 8601 duration -> days (float). Months count 30 days, years 365. None when not a duration."""
    m = re.match(r"^P(?:(\d+(?:\.\d+)?)Y)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?"
                 r"(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$", str(value or "").strip())
    if not m or not any(m.groups()):
        return None
    y, mo, w, d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return y * 365 + mo * 30 + w * 7 + d + h / 24 + mi / 1440 + s / 86400


def _decode(code):
    return str((code or {}).get("decode") or "").strip() if isinstance(code, dict) else ""


def _plain(text):
    t = re.sub(r"<usdm:tag\s+name=\"([^\"]*)\"\s*/?>", r"[\1]", str(text or ""))
    t = re.sub(r"<li[^>]*>", " - ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return " ".join(t.split())


def _instance_days(timeline):
    """Day of every scheduled instance relative to the timeline's fixed reference (0), following the timings."""
    day, timings = {}, timeline.get("timings") or []
    for t in timings:
        if "fixed" in _decode(t.get("type")).lower():
            day[t.get("relativeFromScheduledInstanceId")] = 0.0
    changed = True
    while changed:
        changed = False
        for t in timings:
            frm, to = t.get("relativeFromScheduledInstanceId"), t.get("relativeToScheduledInstanceId")
            d = duration_days(t.get("value"))
            if frm in day or to not in day or d is None:
                continue
            kind = _decode(t.get("type")).lower()
            day[frm] = day[to] - d if "before" in kind else day[to] + d
            changed = True
    return day


def extract(doc):
    """Normalized structure from a USDM 4.0 document (first study version, first study design)."""
    study = doc.get("study") or {}
    versions = study.get("versions") or []
    designs = (versions[0].get("studyDesigns") or []) if versions else []
    if not designs:
        raise UsdmError("USDM input rejected: the document has no study version with a study design")
    ver, sd = versions[0], designs[0]
    notes = []
    if len(versions) > 1 or len(designs) > 1:
        notes.append(f"{len(versions)} study version(s), {len(designs)} study design(s): the first of each is used")

    acts_raw = _chain(sd.get("activities") or [])
    act_by_id = {a.get("id"): a for a in acts_raw}
    bc_by_id = {b.get("id"): b for b in ver.get("biomedicalConcepts") or []}
    sur_by_id = {b.get("id"): b for b in ver.get("bcSurrogates") or []}
    timelines = sd.get("scheduleTimelines") or []
    tl_by_id = {t.get("id"): t for t in timelines}
    main = next((t for t in timelines if t.get("mainTimeline")), timelines[0] if timelines else {})

    def bc_code(b):
        code = (((b.get("code") or {}).get("standardCode") or {}).get("code") or "").strip()
        return code or str(b.get("reference") or "").rstrip("/").split("/")[-1]

    activities = []
    for a in acts_raw:
        bcs = [bc_by_id[i] for i in a.get("biomedicalConceptIds") or [] if i in bc_by_id]
        activities.append({
            "id": a.get("id"), "name": a.get("name") or "", "label": a.get("label") or a.get("name") or "",
            "bc_codes": [c for c in (bc_code(b) for b in bcs) if c],
            "bc_names": [b.get("label") or b.get("name") for b in bcs]
                        + [sur_by_id[i].get("label") or sur_by_id[i].get("name") for i in a.get("bcSurrogateIds") or []
                           if i in sur_by_id],
            "procedures": [p.get("label") or p.get("name") for p in a.get("definedProcedures") or []],
            "children": [c for c in a.get("childIds") or [] if c in act_by_id],
            "timeline_id": a.get("timelineId") if a.get("timelineId") in tl_by_id else None})
    act_info = {a["id"]: a for a in activities}
    sub_timelines = {a["timeline_id"] for a in activities if a["timeline_id"]}

    def expand(ids, seen=None):
        """Scheduled activities plus their child activities and the activities of the sub-timeline they run."""
        seen, out = (seen if seen is not None else set()), []
        for i in ids or []:
            if i not in act_info or i in seen:
                continue
            seen.add(i)
            out.append(i)
            out += expand(act_info[i]["children"], seen)
            tl = tl_by_id.get(act_info[i]["timeline_id"])
            if tl is not None and tl is not main:
                for inst in tl.get("instances") or []:
                    out += expand(inst.get("activityIds"), seen)
        return out

    epochs = {e.get("id"): e for e in sd.get("epochs") or []}
    arms_raw = sd.get("arms") or []
    used_arm = set()
    arms = [{"id": a.get("id"), "arm_code": _oid(a.get("name"), used_arm, prefix="")[:20], "arm_name": a.get("label") or a.get("name"),
             "description": a.get("description") or "", "type": _decode(a.get("type"))} for a in arms_raw]
    cells = {(c.get("armId"), c.get("epochId")) for c in sd.get("studyCells") or []}

    days = _instance_days(main)
    timing_of = {t.get("relativeFromScheduledInstanceId"): t for t in main.get("timings") or []}
    by_enc = {}
    for inst in main.get("instances") or []:
        if inst.get("encounterId"):
            by_enc.setdefault(inst["encounterId"], []).append(inst)
    events, used = [], set()
    for n, enc in enumerate(_chain(sd.get("encounters") or []), 1):
        insts = by_enc.get(enc.get("id"), [])
        timed = sorted((i for i in insts if i.get("id") in days), key=lambda i: days[i["id"]])
        first = timed[0] if timed else None
        t = timing_of.get(first.get("id")) if first else None
        lo, hi = (duration_days(t.get("windowLower")), duration_days(t.get("windowUpper"))) if t else (None, None)
        epoch_ids = [i.get("epochId") for i in insts if i.get("epochId")]
        epoch = epochs.get(epoch_ids[0]) if epoch_ids else None
        applies = [a["arm_code"] for a in arms if epoch is None or not cells or (a["id"], epoch.get("id")) in cells]
        acts = []
        for i in insts:
            acts += [x for x in expand(i.get("activityIds")) if x not in acts]
        events.append({
            "oid": _oid(enc.get("label") or enc.get("name"), used), "name": enc.get("label") or enc.get("name") or "",
            "usdm_id": enc.get("id"), "usdm_name": enc.get("name") or "", "visit_number": n,
            "type": _decode(enc.get("type")), "epoch": (epoch or {}).get("label") or (epoch or {}).get("name") or "",
            "day": days[first["id"]] if first else None,
            "timing_label": (t or {}).get("label") or (t or {}).get("valueLabel") or "",
            "window_lower_days": None if lo is None else -int(math.ceil(lo - 1e-9)),
            "window_upper_days": None if hi is None else int(math.ceil(hi - 1e-9)),
            "window_label": (t or {}).get("windowLabel") or "",
            "arms": applies if len(applies) != len(arms) else [], "activities": acts})

    conditional = []
    for tl in timelines:
        if tl is main or tl.get("id") in sub_timelines:
            continue
        acts = []
        for inst in tl.get("instances") or []:
            acts += [x for x in expand(inst.get("activityIds")) if x not in acts]
        conditional.append({"name": tl.get("label") or tl.get("name") or "", "entry_condition": tl.get("entryCondition") or "",
                            "activities": acts})

    items = {i.get("id"): i for i in ver.get("eligibilityCriterionItems") or []}
    criteria = []
    for c in sd.get("eligibilityCriteria") or []:
        code = (c.get("category") or {}).get("code")
        dec = _decode(c.get("category")).lower()
        cat = ("INCLUSION" if code == _INCLUSION or "inclusion" in dec else
               "EXCLUSION" if code == _EXCLUSION or "exclusion" in dec else "")
        item = items.get(c.get("criterionItemId")) or {}
        criteria.append({"category": cat, "identifier": str(c.get("identifier") or ""), "name": c.get("name") or "",
                         "text": _plain(item.get("text")) or c.get("label") or c.get("description") or ""})
    order = {"INCLUSION": 0, "EXCLUSION": 1, "": 2}
    criteria.sort(key=lambda c: (order[c["category"]], [int(x) if x.isdigit() else x for x in re.findall(r"\d+|\D+", c["identifier"])]))

    idents = [i.get("text") for i in ver.get("studyIdentifiers") or [] if i.get("text")]
    titles = {_decode(t.get("type")): t.get("text") for t in ver.get("titles") or []}
    return {"usdm_version": str(doc.get("usdmVersion")), "study_name": study.get("name") or "",
            "identifiers": idents, "title": titles.get("Official Study Title") or titles.get("Brief Study Title") or "",
            "events": events, "activities": activities, "arms": [{k: v for k, v in a.items() if k != "id"} for a in arms],
            "epochs": [{"name": e.get("label") or e.get("name"), "type": _decode(e.get("type"))} for e in _chain(sd.get("epochs") or [])],
            "conditional_timelines": conditional, "eligibility": criteria, "notes": notes}


def placements(structure):
    """[(event oid, activity id)] of the USDM-seeded structure."""
    return [(e["oid"], a) for e in structure["events"] for a in e["activities"]]


# ── Activity -> form rule layer ──────────────────────────────────────────────────

def _toks(text):
    out = []
    for t in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        out.append(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t)
    return tuple(out)


def _contains(hay, needle):
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _bc_domains():
    """Biomedical Concept C-code -> CDASH domains, from the vendored COSMoS CRF specializations."""
    if "bc" not in _MEMO:
        out = {}
        try:
            import cdisc_ct
            p = os.path.join(cdisc_ct.STANDARDS_REPO_DIR, "cosmos", cdisc_ct.manifest()["crf_specializations_file"])
            with open(p, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    bc, dom = (r.get("bc_id") or "").strip(), (r.get("domain") or "").strip().upper()
                    if bc and dom and dom not in out.setdefault(bc, []):
                        out[bc].append(dom)
        except Exception as e:
            print(f"[usdm] COSMoS BC map unavailable: {e}", flush=True)
        _MEMO["bc"] = out
    return _MEMO["bc"]


def _domain_names(std):
    """Token tuple of a CDISC domain name -> domain code, from CT 'SDTM Domain Abbreviation'."""
    out = {}
    cl = std.ct.get("DOMAIN") if std is not None and getattr(std, "ct", None) is not None else None
    for t in (cl or {}).get("terms", {}).values():
        for name in t["synonyms"] + [re.sub(r"\s+Domain$", "", t["preferred_term"] or "")]:
            k = _toks(name)
            if k:
                out.setdefault(k, t["value"].upper())
    return out


def _test_names(std):
    """Token tuple of a CDISC test name -> domains, from the CT test-name codelists (VSTEST, EGTEST, LBTEST ...)."""
    ct = getattr(std, "ct", None) if std is not None else None
    if ct is None:
        return {}
    key = ("tests", id(ct))
    if key not in _MEMO:
        out = {}
        for sn, cl in ct.codelists.items():
            if not re.match(r"^[A-Z]{2}TEST$", sn):
                continue
            for t in cl["terms"].values():
                for name in [t["value"]] + t["synonyms"]:
                    k = _toks(name)
                    if k and sn[:2] not in out.setdefault(k, []):
                        out[k].append(sn[:2])
        _MEMO[key] = out
    return _MEMO[key]


def _qrs_forms(text, forms, by_domain, std):
    """Forms for an activity that names a QRS instrument: forms naming the same instrument, else its domain's."""
    try:
        import cdisc_qrs
        ix = cdisc_qrs.build_index(std.ct)
        cand = cdisc_qrs.candidates({"form_title": text}, ix)
        if not cand:
            return []
        named = [f["form_id"] for f in forms if set(cdisc_qrs.candidates(f, ix)) & set(cand)]
        return named or [fid for i in cand for fid in by_domain.get(ix.instruments[i]["domain"], [])]
    except Exception:
        return []


def resolve_forms(structure, spec, std=None, forms_catalog=None):
    """{activity id: {"forms": [form ids], "basis": rule}} for activities a rule resolves, plus the unresolved.
    forms_catalog: [(form key, form name)] from the customer's FORMS convention, optional."""
    forms = [f for f in spec.get("forms") or [] if isinstance(f, dict) and f.get("form_id")]
    by_domain, names = {}, []
    for f in forms:
        d = str(f.get("cdash_domain") or "").strip().upper()
        if d:
            by_domain.setdefault(d, []).append(f["form_id"])
        names.append((f["form_id"], _toks(f.get("form_title")), _toks(re.sub(r"^F_", "", str(f["form_id"])))))
    ids = {str(f["form_id"]).upper(): f["form_id"] for f in forms}
    ids.update({re.sub(r"^F_", "", k): v for k, v in list(ids.items())})
    catalog = [(str(k).strip().upper(), _toks(n)) for k, n in (forms_catalog or []) if k and n]
    bc_map, dom_names, tests = _bc_domains(), _domain_names(std), _test_names(std)
    resolved, unresolved = {}, []
    for a in structure["activities"]:
        texts = [t for t in {_toks(a["name"]), _toks(a["label"])} if t]
        hit, basis = [], None

        def take(found, rule):
            nonlocal hit, basis
            if found and not hit:
                hit, basis = list(dict.fromkeys(found)), rule

        take([fid for c in a["bc_codes"] for d in bc_map.get(c, []) for fid in by_domain.get(d, [])], "bc_code")
        take([ids[k] for k, n in catalog if k in ids and any(t == n for t in texts)], "forms_convention")
        take([fid for k, d in dom_names.items() if any(t == k or (len(k) > 1 and _contains(t, k)) for t in texts)
              for fid in by_domain.get(d, [])], "cdisc_domain_name")
        take([fid for fid, title, short in names
              if any(t == title or t == short or (len(title) > 1 and _contains(t, title))
                     or (len(t) > 1 and _contains(title, t)) for t in texts)], "form_name")
        if not hit and std is not None:
            take([fid for txt in (a["label"], a["name"]) for fid in _qrs_forms(txt, forms, by_domain, std)],
                 "qrs_instrument")
        if not hit and tests:
            # the activity is a CDISC test name, or a list of them ("Height and Weight"), all of one domain
            for t in (a["label"], a["name"]):
                parts = [_toks(p) for p in re.split(r",|/|&|\band\b", str(t), flags=re.I) if _toks(p)]
                doms = [set(tests.get(p, [])) for p in parts]
                common = set.intersection(*doms) if doms and all(doms) else set()
                if len(common) == 1:
                    take([fid for fid in by_domain.get(next(iter(common)), [])], "cdisc_test_name")
        if not hit:
            pre = {d for k, d in dom_names.items() for t in texts
                   if len(t) < len(k) and k[:len(t)] == t and len("".join(t)) >= 3}
            if len(pre) == 1:
                take([fid for fid in by_domain.get(next(iter(pre)), [])], "cdisc_domain_name_prefix")
        take([fid for k, d in dom_names.items() if any(_toks(n) == k for n in a["bc_names"] + a["procedures"])
              for fid in by_domain.get(d, [])], "bc_name")
        if hit:
            resolved[a["id"]] = {"forms": hit, "basis": basis}
        else:
            unresolved.append(a["id"])
    return {"resolved": resolved, "unresolved": unresolved}


# ── Seed / enforce on a Study Spec ───────────────────────────────────────────────

def _event_rows(structure):
    rows = []
    for e in structure["events"]:
        arm = "BOTH" if not e["arms"] else "|".join(e["arms"])
        rows.append({"event": e["oid"], "timepoint": e["name"], "visit_number": e["visit_number"], "arm": arm})
    return rows


def scheduling(structure):
    """spec["scheduling"] entries: the first event is the index event; the others are offsets from it in days."""
    ev = structure["events"]
    if not ev:
        return []
    base = ev[0]["day"]
    out = []
    for i, e in enumerate(ev):
        offset = None if (base is None or e["day"] is None) else int(round(e["day"] - base))
        out.append({"event_oid": e["oid"], "anchor_event_oid": None if i == 0 else ev[0]["oid"],
                    "offset_target_days": 0 if i == 0 else offset,
                    "window_lower_days": e["window_lower_days"], "window_upper_days": e["window_upper_days"],
                    "repeating": False, "arm": "BOTH" if not e["arms"] else "|".join(e["arms"]),
                    "conditional_trigger": None, "source": "USDM"})
    return out


def seed_spec(spec, structure, std=None, forms_catalog=None):
    """Make the USDM structure authoritative on a Study Spec: events, scheduling, form placements, arms.
    Forms keep their content. Events owned by the pipeline's own rules (common / unscheduled) are kept.
    Returns the summary also stored in study_meta.usdm. Idempotent."""
    events = structure["events"]
    usdm_oids = [e["oid"] for e in events]
    tpt = spec.get("timepoint_csv") if isinstance(spec.get("timepoint_csv"), dict) else {}
    old_rows = [r for r in tpt.get("rows") or [] if isinstance(r, dict)]
    by_name = {}
    for e in events:
        for k in {_toks(e["name"]), _toks(e["usdm_name"]), _toks(re.sub(r"^SE_", "", e["oid"]))}:
            if k:
                by_name.setdefault(k, e["oid"])
    remap, kept_rows, removed = {}, [], []
    for r in old_rows:
        oid = str(r.get("event") or "")
        if oid in usdm_oids:
            continue
        if _KEEP_EVENT.search(oid):
            kept_rows.append(r)
            continue
        target = by_name.get(_toks(r.get("timepoint"))) or by_name.get(_toks(re.sub(r"^SE_", "", oid)))
        if target:
            remap[oid] = target
        else:
            removed.append(oid)
    rows = _event_rows(structure)
    for k, r in enumerate(kept_rows, len(rows) + 1):
        rows.append({**r, "visit_number": k})
    spec["timepoint_csv"] = {**tpt, "rows": rows}
    if not spec["timepoint_csv"].get("filename"):
        proto = (spec.get("study_meta") or {}).get("protocol_number") or "STUDY"
        spec["timepoint_csv"]["filename"] = f"{proto}_tpt.csv"

    old_sched = [s for s in spec.get("scheduling") or [] if isinstance(s, dict)
                 and s.get("source") != "USDM" and _KEEP_EVENT.search(str(s.get("event_oid") or ""))]
    spec["scheduling"] = scheduling(structure) + old_sched

    res = resolve_forms(structure, spec, std, forms_catalog)
    acts_by_form = {}
    for aid, r in res["resolved"].items():
        for fid in r["forms"]:
            acts_by_form.setdefault(fid, set()).add(aid)
    valid = set(usdm_oids) | {str(r.get("event")) for r in kept_rows}
    placed, without, conditional_only, dropped = 0, [], [], []
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        cur = [str(v) for v in f.get("visits_assigned") or []]
        special = [v for v in cur if _KEEP_EVENT.search(v)]
        acts = acts_by_form.get(f.get("form_id")) or set()
        from_usdm = [e["oid"] for e in events if acts & set(e["activities"])]
        if from_usdm:
            new = from_usdm + [v for v in special if v not in from_usdm]
            placed += 1
        else:
            # no scheduled activity names this form: keep its own visits where they are USDM events
            new = []
            for v in cur:
                t = v if (v in valid or _KEEP_EVENT.search(v)) else remap.get(v)
                if t is None:
                    dropped.append(f"{f.get('form_id')}: {v}")
                elif t not in new:
                    new.append(t)
            # named only by an activity of a conditional timeline (adverse events, early termination ...):
            # not a calendar placement, the form keeps its own (common / unscheduled) events
            (conditional_only if acts else without).append(f.get("form_id"))
        f["visits_assigned"] = new

    sm = spec.setdefault("study_meta", {})
    # what an earlier seeding of this spec already removed stays on record (a second pass finds nothing to remove)
    prev = sm.get("usdm") if isinstance(sm.get("usdm"), dict) else {}
    removed = list(dict.fromkeys(list(prev.get("events_not_in_usdm_removed") or []) + removed))
    dropped = list(dict.fromkeys(list(prev.get("visits_dropped") or []) + dropped))
    if structure["arms"]:
        old = {str(a.get("arm_name") or "").lower(): a for a in sm.get("arms") or [] if isinstance(a, dict)}
        sm["arms"] = [{**{k: v for k, v in old.get(str(a["arm_name"]).lower(), {}).items()
                         if k not in ("arm_name", "arm_code", "description")},
                       "arm_name": a["arm_name"], "arm_code": a["arm_code"], "description": a["description"]}
                      for a in structure["arms"]]
        sm["number_of_arms"] = len(structure["arms"])
    name = {a["id"]: a["label"] for a in structure["activities"]}
    scheduled = {aid for e in events for aid in e["activities"]}
    crit = structure["eligibility"]
    summary = {
        "usdm_version": structure["usdm_version"], "study": structure["study_name"],
        "events": len(events), "placements": len(placements(structure)), "forms_placed": placed,
        "activities": len(structure["activities"]),
        "activities_resolved": {b: sum(1 for r in res["resolved"].values() if r["basis"] == b)
                                for b in sorted({r["basis"] for r in res["resolved"].values()})},
        "unresolved_activities": [name[a] for a in res["unresolved"] if a in scheduled],
        "unscheduled_unresolved_activities": [name[a] for a in res["unresolved"] if a not in scheduled],
        "forms_without_activity": without, "forms_on_conditional_timelines": conditional_only,
        "events_not_in_usdm_removed": removed, "visits_dropped": dropped,
        "epochs": [e["name"] for e in structure["epochs"]],
        "conditional_timelines": [{"name": t["name"], "entry_condition": t["entry_condition"],
                                   "activities": [name[a] for a in t["activities"] if a in name]}
                                  for t in structure["conditional_timelines"]],
        "eligibility_criteria": {"inclusion": sum(1 for c in crit if c["category"] == "INCLUSION"),
                                 "exclusion": sum(1 for c in crit if c["category"] == "EXCLUSION")},
        "notes": structure["notes"]}
    sm["usdm"] = summary
    flags = []
    if summary["unresolved_activities"]:
        flags.append({"item": "USDM activities without a form",
                      "reason": "No general rule (Biomedical Concept, FORMS convention, CDISC domain name, form name) "
                                "matched these scheduled activities to a form; place them by hand: "
                                + "; ".join(summary["unresolved_activities"])})
    if without:
        flags.append({"item": "Forms not named by a USDM activity",
                      "reason": "Visits kept from the form's own assignment where they are USDM events: "
                                + ", ".join(str(x) for x in without)})
    if dropped:
        flags.append({"item": "Visits that are not USDM encounters",
                      "reason": "Removed from the form (USDM is authoritative for events): " + "; ".join(dropped)})
    rf = spec.get("review_flags")
    if flags or (isinstance(rf, dict) and "usdm_review" in rf):
        if not isinstance(rf, dict):
            rf = spec["review_flags"] = {}
        rf["usdm_review"] = flags
    return summary


# ── Context for the protocol-analysis step (the main prompt is not changed) ──────

def context_text(structure, max_criteria=120):
    """Authoritative structure block passed as extra text: the analysis fills form content only."""
    name = {a["id"]: a["label"] for a in structure["activities"]}
    L = ["USDM STUDY STRUCTURE (CDISC USDM " + structure["usdm_version"] + ", provided by the sponsor) - AUTHORITATIVE",
         "The visit structure below is final. Use exactly these study events (event OIDs and names), in this order, in",
         "timepoint_csv and in every form's visits_assigned. Do not add, rename, merge or drop scheduled visits.",
         "Design the forms and their content; place each form at the events where its assessment is scheduled below.",
         "", "STUDY EVENTS (event OID | name | epoch | timing | window | arms):"]
    for e in structure["events"]:
        L.append(f"{e['oid']} | {e['name']} | {e['epoch'] or '-'} | {e['timing_label'] or '-'} | "
                 f"{e['window_label'] or '-'} | {', '.join(e['arms']) or 'all'}")
    L += ["", "SCHEDULE OF ACTIVITIES (event OID: activities):"]
    for e in structure["events"]:
        L.append(f"{e['oid']}: " + "; ".join(name[a] for a in e["activities"] if a in name))
    if structure["conditional_timelines"]:
        L += ["", "CONDITIONAL / UNSCHEDULED TIMELINES (not calendar visits):"]
        L += [f"{t['name']} (when: {t['entry_condition'] or '-'}): " + "; ".join(name[a] for a in t["activities"] if a in name)
              for t in structure["conditional_timelines"]]
    if structure["arms"]:
        L += ["", "ARMS: " + "; ".join(f"{a['arm_code']} = {a['arm_name']}" for a in structure["arms"])]
    if structure["epochs"]:
        L += ["EPOCHS: " + ", ".join(e["name"] for e in structure["epochs"])]
    crit = structure["eligibility"]
    if crit:
        L += ["", "ELIGIBILITY CRITERIA (one criterion row each, in this order):"]
        L += [f"{c['category'] or 'CRITERION'} {c['identifier']}: {c['text']}" for c in crit[:max_criteria]]
    return "\n".join(L)


def read(data):
    """bytes / str / dict -> (structure, schema problems). Raises UsdmError when the input cannot be used."""
    doc = load(data)
    check_version(doc)
    problems = validate(doc)
    return extract(doc), problems
