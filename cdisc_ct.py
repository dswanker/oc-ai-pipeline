"""cdisc_ct.py: deterministic CDISC standards layer for the OC AI Pipeline. No AI, no tokens.

Sources, pinned in cdisc_standards/manifest.json:
  * Controlled Terminology: NCI EVS dated SDTM Terminology archive file (public).
    NCI EVS is CDISC's official CT publisher; the dated file makes every build reproducible.
  * CDASH CRF specializations: CDISC COSMoS (public, MIT), vendored in cdisc_standards/cosmos/.
    Gives CDISC's recommended value list and display labels per CDASH variable.
  * CDASHIG metadata: CDISC member download. NEVER committed (the repo is public). Read from
    CDISC_STANDARDS_DIR (default /data/cdisc_standards on Railway, .cache/cdisc_standards locally).
    Gives the exact CDASH variable -> codelist binding. Optional: without it, binding falls back
    to the COSMoS codelist, then to general name rules.

Hierarchy (applied by pipeline._apply_cdisc_ct): customer standards and OC standards choice
lists are never touched. Only fields that default to CDASH get CDISC CT here.

CLI:  python3 cdisc_ct.py [--refresh] spec1.json [spec2.json ...]   (report only)
"""
import csv, io, json, os, re, sys, urllib.request
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
STANDARDS_REPO_DIR = os.path.join(_HERE, "cdisc_standards")
EVS_CURRENT_URL = "https://evs.nci.nih.gov/ftp1/CDISC/SDTM/SDTM%20Terminology.txt"
EVS_ARCHIVE_URL = "https://evs.nci.nih.gov/ftp1/CDISC/SDTM/Archive/SDTM%20Terminology%20{v}.txt"
FULL_LIST_MAX = 30  # a codelist up to this size may be used whole on a CRF


def manifest():
    with open(os.path.join(STANDARDS_REPO_DIR, "manifest.json")) as fh:
        return json.load(fh)


def _local_dir(env, railway, local):
    d = os.environ.get(env) or (railway if os.path.isdir("/data") else os.path.join(_HERE, ".cache", local))
    os.makedirs(d, exist_ok=True)
    return d


def _cache_dir():
    return _local_dir("CDISC_CT_DIR", "/data/cdisc_ct", "cdisc_ct")


def member_dir():
    return _local_dir("CDISC_STANDARDS_DIR", "/data/cdisc_standards", "cdisc_standards")


# ── Controlled Terminology ──────────────────────────────────────────────────────

class CTPackage:
    """A pinned CT release: codelists by submission short name and by NCI C-code."""

    def __init__(self, codelists, source, version):
        self.codelists = codelists
        self.by_code = {cl["code"]: cl for cl in codelists.values()}
        self.source = source
        self.version = version
        self.package_date = version  # backwards-compatible name

    def get(self, short_name):
        return self.codelists.get((short_name or "").strip().upper())

    def get_code(self, c_code):
        return self.by_code.get((c_code or "").strip())


def _parse_evs(text):
    rows = csv.reader(io.StringIO(text), delimiter="\t")
    next(rows, None)
    cls, terms = {}, []
    for r in rows:
        if len(r) < 8:
            continue
        code, parent, ext, name, sub, syn, _defn, pt = r[:8]
        if not parent:
            cls[code] = {"code": code, "short_name": sub.strip(), "name": name.strip(),
                         "extensible": ext.strip().lower() == "yes", "terms": {}}
        else:
            terms.append((parent, code, sub.strip(), syn, pt))
    for parent, code, sub, syn, pt in terms:
        cl = cls.get(parent)
        if cl is not None:
            cl["terms"][sub.upper()] = {
                "code": code, "value": sub, "preferred_term": pt.strip(),
                "synonyms": [s.strip() for s in syn.split(";") if s.strip()]}
    return {cl["short_name"].upper(): cl for cl in cls.values() if cl["short_name"]}


_CT_MEMO = {}


def load(version=None, refresh=False):
    """Load a pinned CT release (default: manifest ct_version). Downloads the dated EVS archive
    file once per version and caches it. Returns None if unavailable; callers degrade gracefully."""
    version = version or manifest()["ct_version"]
    if version in _CT_MEMO and not refresh:
        return _CT_MEMO[version]
    raw_p = os.path.join(_cache_dir(), f"sdtm_terminology_{version}.txt")
    url = EVS_ARCHIVE_URL.format(v=version)
    if refresh or not os.path.exists(raw_p):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "oc-ai-pipeline"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = resp.read()
            if not body.startswith(b"Code\t"):
                raise ValueError("EVS returned something other than a CT text file")
            with open(raw_p, "wb") as fh:
                fh.write(body)
            print(f"[cdisc-ct] downloaded CT {version}", flush=True)
        except Exception as e:
            if not os.path.exists(raw_p):
                print(f"[cdisc-ct] CT {version} unavailable (download failed, no cache): {e}", flush=True)
                return None
            print(f"[cdisc-ct] refresh failed, using cached CT {version}: {e}", flush=True)
    with open(raw_p, encoding="utf-8", errors="replace") as fh:
        pkg = CTPackage(_parse_evs(fh.read()), url, version)
    _CT_MEMO[version] = pkg
    return pkg


# ── CDASH metadata: COSMoS CRF specializations (public) + CDASHIG (member, optional) ──

def _split(s):
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def load_crf_specs():
    """CDASH variable -> CDISC CRF specialization (codelist, recommended values, display labels).
    Per variable, the first row that carries a value list wins; otherwise the first row."""
    p = os.path.join(STANDARDS_REPO_DIR, "cosmos", manifest()["crf_specializations_file"])
    out = {}
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            var = (r.get("variable_name") or "").strip().upper()
            if not var or not r.get("codelist"):
                continue
            values, displays = _split(r.get("value_list")), _split(r.get("value_display_list"))
            cur = out.get(var)
            if cur is None or (not cur["values"] and values):
                out[var] = {"codelist": r["codelist"].strip(), "values": values,
                            "displays": displays if len(displays) == len(values) else [],
                            "selection": (r.get("selection_type") or "").strip()}
    return out


def load_cdashig():
    """CDASH variable -> list of CDISC codelist C-codes (general first, CDASH subset after),
    from the member CDASHIG metadata CSV. Returns {} when the file is not present."""
    m = manifest()
    p = os.path.join(member_dir(), m.get("cdashig_file", ""))
    if not m.get("cdashig_file") or not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            var = (r.get("CDASHIG Variable") or "").strip().upper()
            codes = _split(r.get("CDISC CT Codelist Code(s), Subset Codes(s)"))
            if var:
                # Union across all rows (scenarios/domains) for the variable, in first-seen order.
                # [] = CDASHIG defines the variable with no CDISC codelist anywhere.
                cur = out.setdefault(var, [])
                cur.extend(c for c in codes if c not in cur)
    return out


class Standards:
    """Everything the deterministic layer needs, with the versions used (stamped on each build)."""

    def __init__(self, ct, crf_specs, cdashig):
        self.ct, self.crf_specs, self.cdashig = ct, crf_specs, cdashig
        m = manifest()
        self.versions = {"ct_version": ct.version, "ct_source": ct.source,
                         "crf_specializations": m["crf_specializations_file"],
                         "cdashig": m.get("cdashig_version") if cdashig else None}


def load_standards(ct_version=None):
    ct = load(ct_version)
    if ct is None:
        return None
    return Standards(ct, load_crf_specs(), load_cdashig())


# ── Binding: which CDISC codelist governs a field ───────────────────────────────

def safe_name(value):
    """XLSForm-safe choice name from a CDISC submission value (same rule as customer choices)."""
    s = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", str(value))).strip("_")
    return "c_" + s if (not s or s[0].isdigit()) else s


def _to_submission(cl, value):
    """Map a value to the codelist's submission value by code, safe name, preferred term or synonym."""
    v = str(value or "").strip().upper()
    if not v:
        return None
    if v in cl["terms"]:
        return cl["terms"][v]["value"]
    for t in cl["terms"].values():
        if (v == safe_name(t["value"]).upper() or v == t["preferred_term"].upper()
                or v in {s.upper() for s in t["synonyms"]}):
            return t["value"]
    return None


def field_variable(field_name, std):
    """CDASH variable for a spec field. Handles OC item names like I_AE_AESEV."""
    name = (field_name or "").strip().upper()
    cands = [name]
    if name.startswith("I_") and name.count("_") >= 2:
        parts = name.split("_")
        cands += ["_".join(parts[2:]), parts[-1]]
    for c in cands:
        if c in std.cdashig or c in std.crf_specs:
            return c
    return name


def _overlap(cl, values):
    return sum(1 for v in values if _to_submission(cl, v))


def bind(std, var, values=()):
    """Returns (codelist, bound_by, note). Order: CDASHIG -> COSMoS CRF specialization -> name rules.
    CDASHIG is authoritative: a variable it defines without a codelist is sponsor-defined and is
    never bound by a fallback. A codelist CDISC names but the pinned release lacks is reported,
    never guessed. Fallback bindings (not CDASHIG) require at least one study value to match."""
    ct, name, values = std.ct, (var or "").strip().upper(), [v for v in values if str(v).strip()]
    if name in std.cdashig:
        codes = std.cdashig[name]
        if not codes:
            return None, None, "sponsor_defined_per_cdashig"
        present = [ct.get_code(c) for c in codes if ct.get_code(c)]
        if not present:
            return None, None, "codelist_not_in_release:" + ";".join(codes)
        # Several codelists listed (general + CDASH subset, or alternatives such as DSDECOD):
        # the one matching most study values wins; ties go to the last listed (the CDASH subset).
        best = max(range(len(present)), key=lambda i: (_overlap(present[i], values), i))
        cl = present[best]
        if values and cl["extensible"] and _overlap(cl, values) == 0:
            # Nothing the study collects is in the codelist: applying CT would only relabel the list.
            return None, None, f"no_value_overlap:{cl['short_name']}"
        return cl, "cdashig", None
    cl, bound_by, note = None, None, None
    spec = std.crf_specs.get(name)
    if spec:
        cl = ct.get_code(spec["codelist"])
        bound_by = "crf_specialization"
        if not cl:
            return None, None, "codelist_not_in_release:" + spec["codelist"]
    elif ct.get(name):
        cl, bound_by = ct.get(name), "exact"
    elif len(name) > 4 and name[:2].isalpha() and ct.get(name[2:]):
        cl, bound_by = ct.get(name[2:]), "domain_prefix"
    elif ct.get(name + "C") and ct.get(name + "C")["name"].lower().endswith("as collected"):
        cl, bound_by = ct.get(name + "C"), "as_collected"
    else:
        ny = ct.get("NY")
        mapped = {_to_submission(ny, v) for v in values} if ny else set()
        if values and None not in mapped and {"Y", "N"} <= mapped:
            return ny, "yes_no_values", None
        return None, None, None
    if values and _overlap(cl, values) == 0:
        return None, None, f"no_value_overlap:{cl['short_name']}"
    return cl, bound_by, note


# ── Resolution: which values a CDASH-default field gets ─────────────────────────

def _ct_label(term):
    v = term["value"]
    if len(v) <= 3 and term["preferred_term"]:
        return term["preferred_term"]
    return v.title() if v.isupper() else v


def resolve(std, cl, var, existing):
    """Deterministic value list for a CDASH-default field bound to codelist `cl`.
    existing: [(name, label)] from the spec. Returns dict(choices, rule, unmapped) or None (leave as is).
      1. CDISC recommends values for this variable (COSMoS): use them; keep a study subset only
         when every study value maps into the recommendation.
      2. No recommendation: study values that all map to CT keep their selection, with CT codes.
      3. Some values do not map: small non-extensible codelist -> full codelist; extensible ->
         mapped values plus the rest as sponsor extensions; large non-extensible -> mapped values
         plus the rest, flagged nonconformant.
      4. No values at all: small codelist -> full codelist; large -> leave (nothing to choose from)."""
    terms = cl["terms"]
    mapped, unmapped = [], []
    for n, lab in existing:
        sv = _to_submission(cl, n) or _to_submission(cl, lab)
        if sv:
            if sv not in mapped:
                mapped.append(sv)
        else:
            unmapped.append((n, lab))
    spec = std.crf_specs.get(var)
    rec, rec_lab = [], {}
    if spec and spec["codelist"] == cl["code"]:
        rec = [v for v in spec["values"] if v.upper() in terms]
        rec_lab = {v.upper(): d for v, d in zip(spec["values"], spec["displays"])}

    def ct_choice(sv):
        t = terms[sv.upper()]
        return {"name": safe_name(t["value"]), "label": rec_lab.get(sv.upper()) or _ct_label(t),
                "submission_value": t["value"], "code": t["code"], "extension": False}

    ext = []
    if rec:
        mset = {m.upper() for m in mapped}
        if existing and mset and not unmapped and mset < {r.upper() for r in rec}:
            values, rule = [r for r in rec if r.upper() in mset], "subset_of_cdisc_recommended"
        else:
            values, rule = rec, "cdisc_recommended"
        if unmapped and cl["extensible"]:
            values = values + [m for m in mapped if m.upper() not in {v.upper() for v in values}]
            ext = [{"name": safe_name(n), "label": lab or n, "submission_value": None, "code": None,
                    "extension": True} for n, lab in unmapped]
            rule += "_plus_extensions"
    elif existing and not unmapped:
        full = {k for k in terms}
        values = mapped
        rule = "full_codelist" if {m.upper() for m in mapped} == full else "mapped_to_ct"
        if rule == "full_codelist":
            values = [t["value"] for t in terms.values()]  # canonical order, stable across runs
    elif not existing:
        if len(terms) > FULL_LIST_MAX:
            return None
        values, rule = [t["value"] for t in terms.values()], "full_codelist"
    elif not cl["extensible"] and len(terms) <= FULL_LIST_MAX:
        values, rule = [t["value"] for t in terms.values()], "full_codelist"
    else:
        values = mapped
        rule = "mapped_plus_extensions" if cl["extensible"] else "mapped_with_nonconformant"
        ext = [{"name": safe_name(n), "label": lab or n, "submission_value": None, "code": None,
                "extension": True} for n, lab in unmapped]
    out, seen = [], set()
    for c in [ct_choice(v) for v in values] + ext:
        if c["name"].upper() not in seen:
            seen.add(c["name"].upper())
            out.append(c)
    return {"choices": out, "rule": rule, "unmapped": [n for n, _ in unmapped]}


# ── Apply to a Study Spec (CDASH-default fields only) ───────────────────────────

def _select(row):
    t = (row.get("type") or "").strip() if isinstance(row, dict) else ""
    kind, _, ln = t.partition(" ")
    return (kind, ln.strip()) if kind.lower() in ("select_one", "select_multiple") and ln.strip() else (None, None)


def _renames(cl, existing, new_names):
    """Old choice name -> new choice name, plus old names that no longer exist."""
    ren, dropped = {}, []
    for n, lab in existing:
        sv = _to_submission(cl, n) or _to_submission(cl, lab)
        cand = safe_name(sv) if sv else safe_name(n)
        if cand in new_names:
            ren[n] = cand
        elif n not in new_names:
            dropped.append(n)
    return ren, dropped


def _is_expr_key(k):
    base = str(k).split("::")[0].lower()
    return base not in ("name", "type", "cdisc_ct") and not base.startswith(("label", "hint", "media"))


def _rewrite_refs(form, field, renames, dropped):
    """Rewrite ${field} = 'old', 'old' = ${field}, selected(${field}, 'old') in this form's survey.
    Returns (number of expressions changed, dropped values still referenced)."""
    ref = r"\$\{" + re.escape(str(field)) + r"\}"
    changed, still = 0, set()
    for row in form.get("survey") or []:
        if not isinstance(row, dict):
            continue
        for k, v in list(row.items()):
            if not isinstance(v, str) or "${" + str(field) + "}" not in v or not _is_expr_key(k):
                continue
            new = v
            for old, nw in renames.items():
                if old == nw:
                    continue
                o = re.escape(old)
                rep = lambda m: m.group(1) + m.group(2) + nw + m.group(2)
                new = re.sub(r"(" + ref + r"\s*!?=\s*)(['\"])" + o + r"\2", rep, new)
                new = re.sub(r"(selected\(\s*" + ref + r"\s*,\s*)(['\"])" + o + r"\2", rep, new)
                new = re.sub(r"(['\"])" + o + r"\1(\s*!?=\s*" + ref + r")",
                             lambda m: m.group(1) + nw + m.group(1) + m.group(2), new)
            for old in dropped:
                if re.search(ref + r"[^'\"]{0,12}(['\"])" + re.escape(old) + r"\1", new) or \
                        re.search(r"(['\"])" + re.escape(old) + r"\1\s*!?=\s*" + ref, new):
                    still.add(old)
            if new != v:
                row[k] = new
                changed += 1
    return changed, sorted(still)


def _referenced(form, field, values):
    """Values of `field` that this form's expressions compare against."""
    ref = r"\$\{" + re.escape(str(field)) + r"\}"
    found = set()
    for row in form.get("survey") or []:
        for k, v in (row.items() if isinstance(row, dict) else []):
            if not isinstance(v, str) or "${" + str(field) + "}" not in v or not _is_expr_key(k):
                continue
            for val in values:
                o = re.escape(val)
                if (re.search(ref + r"\s*!?=\s*(['\"])" + o + r"\1", v)
                        or re.search(r"selected\(\s*" + ref + r"\s*,\s*(['\"])" + o + r"\1", v)
                        or re.search(r"(['\"])" + o + r"\1\s*!?=\s*" + ref, v)):
                    found.add(val)
    return sorted(found)


def apply_to_spec(spec, std, protected_vars=frozenset()):
    """Give every CDASH-default select field its CDISC CT list. Fields whose variable is in
    protected_vars (customer or OC standards lists) and lists injected from customer CRF
    standards are never changed. Idempotent. Returns per-field decisions."""
    protected = {p.upper() for p in protected_vars}
    decisions = []
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        fid, choices = f.get("form_id"), f.setdefault("choices", [])
        lists = {}
        for c in choices:
            if isinstance(c, dict):
                lists.setdefault(str(c.get("list_name") or "").strip(), []).append(c)
        ours = {ln for ln, rows in lists.items() if rows and all(r.get("source") == "CDISC_CT" for r in rows)}
        made, repointed = {}, set()
        for row in f.get("survey") or []:
            kind, ln = _select(row)
            if not kind:
                continue
            var = field_variable(row.get("name"), std)
            d = {"form_id": fid, "field": row.get("name"), "variable": var, "list_name": ln}
            rows = lists.get(ln, [])
            if var in protected or str(row.get("name", "")).upper() in protected or \
                    any(r.get("source") == "crf_standards_injection" for r in rows):
                decisions.append({**d, "tier": "customer_or_oc_standard", "action": "kept"})
                continue
            existing = [(str(r.get("name", "")).strip(), str(r.get("label", "")).strip()) for r in rows]
            cl, bound_by, note = bind(std, var, [n for n, _ in existing])
            if not cl:
                decisions.append({**d, "tier": "cdash_default", "action": "no_cdisc_codelist", "note": note})
                continue
            res = resolve(std, cl, var, existing)
            if res is None:
                decisions.append({**d, "tier": "cdash_default", "action": "left_as_is",
                                  "codelist": cl["short_name"], "note": "large codelist, no study values"})
                continue
            names = {c["name"] for c in res["choices"]}
            _, dropped0 = _renames(cl, existing, names)
            kept = _referenced(f, row.get("name"), dropped0)
            if kept:
                # A value still used by skip logic or checks is kept (flagged) rather than breaking the form.
                lab = dict(existing)
                res["choices"] += [{"name": safe_name(n), "label": lab.get(n) or n, "submission_value": None,
                                    "code": None, "extension": True} for n in kept if safe_name(n) not in names]
                res["rule"] += "_kept_referenced"
            content = tuple(c["name"] for c in res["choices"])
            new = "ct_" + cl["short_name"].lower()
            if res["rule"] not in ("cdisc_recommended", "full_codelist"):
                # Named by content, so fields with the same CDISC value set share one list.
                tail = "_".join(content).lower()
                if len(tail) > 24:
                    import hashlib
                    tail = hashlib.sha1("|".join(content).encode()).hexdigest()[:8]
                new += "_" + tail
            if (new in lists and new not in ours) or (new in made and made[new] != content):
                new += "_cdisc"
            if new not in made:
                made[new] = content
                choices[:] = [c for c in choices if not (isinstance(c, dict) and c.get("list_name") == new)]
                choices.extend({"list_name": new, "name": c["name"], "label": c["label"], "source": "CDISC_CT",
                                "cdisc_submission_value": c["submission_value"], "cdisc_code": c["code"],
                                "cdisc_codelist": cl["code"], "sponsor_extension": c["extension"]}
                               for c in res["choices"])
            if ln != new:
                repointed.add(ln)
            row["type"] = f"{kind} {new}"
            ren, dropped = _renames(cl, existing, {c["name"] for c in res["choices"]})
            n_expr, refs_dropped = _rewrite_refs(f, row.get("name"), ren, dropped)
            prev = row.get("cdisc_ct") if isinstance(row.get("cdisc_ct"), dict) else {}
            unmapped_hist = list(prev.get("unmapped_values") or []) \
                if prev.get("codelist_code") == cl["code"] else []
            unmapped_hist += [u for u in res["unmapped"] if u not in unmapped_hist]
            row["cdisc_ct"] = {"codelist": cl["short_name"], "codelist_code": cl["code"],
                               "extensible": cl["extensible"], "rule": res["rule"], "bound_by": bound_by,
                               "ct_version": std.ct.version, "unmapped_values": unmapped_hist}
            decisions.append({**d, "tier": "cdash_default", "action": "cdisc_ct_applied", "list_name": new,
                              "codelist": cl["short_name"], "rule": res["rule"], "bound_by": bound_by,
                              "unmapped_values": res["unmapped"], "dropped_values": dropped,
                              "expressions_rewritten": n_expr,
                              "expressions_referencing_dropped_values": refs_dropped})
        still = {_select(r)[1] for r in (f.get("survey") or []) if _select(r)[1]}
        f["choices"] = [c for c in choices if not (isinstance(c, dict) and c.get("list_name") in repointed
                                                    and c.get("list_name") not in still)]
    return decisions


def summarize(decisions):
    return {"actions": dict(Counter(d["action"] for d in decisions)),
            "rules": dict(Counter(d["rule"] for d in decisions if d.get("rule"))),
            "bound_by": dict(Counter(d["bound_by"] for d in decisions if d.get("bound_by"))),
            "not_in_release": sorted({d["note"] for d in decisions
                                      if str(d.get("note") or "").startswith("codelist_not_in_release")}),
            "expressions_rewritten": sum(d.get("expressions_rewritten", 0) for d in decisions),
            "expressions_referencing_dropped_values": [
                f"{d['form_id']}.{d['field']}:{d['expressions_referencing_dropped_values']}"
                for d in decisions if d.get("expressions_referencing_dropped_values")]}


if __name__ == "__main__":
    import copy
    args = [a for a in sys.argv[1:] if a != "--refresh"]
    if "--refresh" in sys.argv:
        load(refresh=True)
    std = load_standards()
    if std is None:
        sys.exit("CDISC standards unavailable")
    print(f"Standards: {std.versions}  CDASHIG vars={len(std.cdashig)}  COSMoS vars={len(std.crf_specs)}")
    for p in args:
        spec = copy.deepcopy(json.load(open(p)))
        dec = apply_to_spec(spec, std)
        print(f"\n== {os.path.basename(p)}  {summarize(dec)}")
        for d in dec:
            if d["action"] == "cdisc_ct_applied" and d["rule"] not in ("mapped_to_ct", "subset_of_cdisc_recommended"):
                print(f"  {d['form_id']}.{d['field']} -> {d['list_name']} [{d['rule']}, {d['bound_by']}]"
                      + (f" unmapped={d['unmapped_values']}" if d["unmapped_values"] else "")
                      + (f" dropped={d['dropped_values']}" if d["dropped_values"] else ""))
