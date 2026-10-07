"""cdisc_ct.py: CDISC Controlled Terminology for the OC AI Pipeline (Phase 1).

Source today: NCI EVS SDTM Terminology (NCI EVS is CDISC's official CT publisher).
The backend is swappable: a CDISC Library API loader can replace _parse_evs()/load()
behind the same CTPackage interface once Library access works.

Report-only: nothing in this module changes build output. It binds every
select_one/select_multiple field to a CT codelist using general rules (never
study-, customer- or form-specific) and reports conformance.

CLI:  python3 cdisc_ct.py [--refresh] spec1.json [spec2.json ...]
"""
import csv, io, json, os, sys, time, urllib.request
from collections import Counter
from email.utils import parsedate_to_datetime

EVS_SDTM_URL = "https://evs.nci.nih.gov/ftp1/CDISC/SDTM/SDTM%20Terminology.txt"


def _cache_dir():
    d = os.environ.get("CDISC_CT_DIR") or (
        "/data/cdisc_ct" if os.path.isdir("/data")
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "cdisc_ct"))
    os.makedirs(d, exist_ok=True)
    return d


class CTPackage:
    """A loaded CT package: codelists keyed by CDISC submission short name."""

    def __init__(self, codelists, source, package_date):
        self.codelists = codelists
        self.source = source
        self.package_date = package_date

    def get(self, short_name):
        return self.codelists.get((short_name or "").strip().upper())


def _parse_evs(text):
    rows = csv.reader(io.StringIO(text), delimiter="\t")
    next(rows, None)  # header
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


def load(refresh=False):
    """Load the pinned CT package from cache. Downloads only when no cache exists
    or refresh=True (CT updates are an explicit action, not per pipeline run).
    Returns None if CT is unavailable; callers must degrade gracefully."""
    d = _cache_dir()
    raw_p, meta_p = os.path.join(d, "sdtm_terminology.txt"), os.path.join(d, "meta.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    if refresh or not os.path.exists(raw_p):
        try:
            req = urllib.request.Request(EVS_SDTM_URL, headers={"User-Agent": "oc-ai-pipeline"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                body, lm = resp.read(), resp.headers.get("Last-Modified")
            with open(raw_p, "wb") as fh:
                fh.write(body)
            meta = {"source": EVS_SDTM_URL, "fetched_at": time.time(),
                    "package_date": (parsedate_to_datetime(lm).date().isoformat()
                                     if lm else time.strftime("%Y-%m-%d"))}
            with open(meta_p, "w") as fh:
                json.dump(meta, fh)
            print(f"[cdisc-ct] downloaded CT package {meta['package_date']}", flush=True)
        except Exception as e:
            if not os.path.exists(raw_p):
                print(f"[cdisc-ct] CT unavailable (download failed, no cache): {e}", flush=True)
                return None
            print(f"[cdisc-ct] refresh failed, using cached CT: {e}", flush=True)
    with open(raw_p, encoding="utf-8", errors="replace") as fh:
        codelists = _parse_evs(fh.read())
    return CTPackage(codelists, meta.get("source", EVS_SDTM_URL), meta.get("package_date", "unknown"))


def bind(ct, field_name, values):
    """General field -> codelist binding. Returns (codelist, rule) or (None, None).
    1. exact: CDISC variable name equals a codelist short name (SEX, RACE, AESEV)
    2. domain_prefix: drop a 2-letter domain prefix (AEACN -> ACN, CMROUTE -> ROUTE)
    3. as_collected: fall back to an "As Collected" codelist (RACE -> RACEC)
    4. yes_no_values: the field's values map to NY by code, preferred term or synonym
    The CDISC Library's CDASHIG variable metadata will replace rules 1-2 when available."""
    name = (field_name or "").strip().upper()
    cl = ct.get(name)
    if cl:
        return cl, "exact"
    if len(name) > 4 and name[:2].isalpha():
        cl = ct.get(name[2:])
        if cl:
            return cl, "domain_prefix"
    # "As Collected" codelists (e.g. RACE -> RACEC, ETHNIC -> ETHNICC in current CT)
    cl = ct.get(name + "C")
    if cl and cl["name"].lower().endswith("as collected"):
        return cl, "as_collected"
    ny = ct.get("NY")
    if ny:
        mapped = {_to_submission(ny, v) for v in values if str(v).strip()}
        if mapped and None not in mapped and {"Y", "N"} <= mapped:
            return ny, "yes_no_values"
    return None, None


def _to_submission(cl, value):
    """Map a value to the codelist's submission value by code, preferred term or synonym."""
    v = str(value).strip().upper()
    if v in cl["terms"]:
        return cl["terms"][v]["value"]
    for t in cl["terms"].values():
        if v == t["preferred_term"].upper() or v in {s.upper() for s in t["synonyms"]}:
            return t["value"]
    return None


def assess_field(ct, row, form_choices):
    rtype = (row.get("type") or "").strip()
    list_name = rtype.split(" ", 1)[1].strip() if " " in rtype else ""
    opts = [c for c in form_choices
            if (c.get("list_name") or "").strip().lower() == list_name.lower()]
    values = [str(c.get("name", "")).strip() for c in opts]
    res = {"field": row.get("name"), "list_name": list_name, "n_values": len(values)}
    cl, rule = bind(ct, row.get("name"), values)
    if not cl:
        res["status"] = "unbound"
        return res
    terms = cl["terms"]
    extra = [v for v in values if v.upper() not in terms]
    case_mismatch = [v for v in values if v.upper() in terms and terms[v.upper()]["value"] != v]
    syn_map = {v: s for v in extra for s in [_to_submission(cl, v)] if s}
    if not values:
        status = "missing_list"
    elif not extra:
        status = "conformant" if len(values) == len(terms) else "subset"
    elif len(syn_map) == len(extra):
        status = "mappable"  # every non-CT value maps to a CT submission value
    elif cl["extensible"]:
        status = "extended"
    else:
        status = "nonconformant"
    res.update(codelist=cl["short_name"], codelist_code=cl["code"], extensible=cl["extensible"],
               bound_by=rule, status=status, extra_values=extra,
               case_mismatch=case_mismatch, synonym_matches=syn_map)
    return res


def report(spec, ct=None):
    """Assess every select field in a Study Spec JSON. Returns None if CT unavailable."""
    ct = ct or load()
    if ct is None or not isinstance(spec, dict):
        return None
    fields = []
    for f in spec.get("forms", []) or []:
        if not isinstance(f, dict):
            continue
        choices = f.get("choices") or []
        for row in f.get("survey") or []:
            if not isinstance(row, dict):
                continue
            t = (row.get("type") or "").strip().lower()
            if t.startswith("select_one ") or t.startswith("select_multiple "):
                r = assess_field(ct, row, choices)
                r["form_id"] = f.get("form_id")
                fields.append(r)
    return {"ct_source": ct.source, "ct_package_date": ct.package_date,
            "summary": dict(Counter(r["status"] for r in fields)),
            "bound_by": dict(Counter(r.get("bound_by") for r in fields if r.get("bound_by"))),
            "fields": fields}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--refresh"]
    pkg = load(refresh="--refresh" in sys.argv)
    if pkg is None:
        sys.exit("CT unavailable")
    print(f"CT {pkg.package_date}: {len(pkg.codelists)} codelists from {pkg.source}")
    for p in args:
        rep = report(json.load(open(p)), pkg)
        print(f"\n== {os.path.basename(p)}  {rep['summary']}  bound_by={rep['bound_by']}")
        for r in rep["fields"]:
            if r["status"] not in ("unbound", "conformant", "subset"):
                print(f"  {r['form_id']}.{r['field']} -> {r.get('codelist')} {r['status']} "
                      f"extra={r['extra_values']} syn={r['synonym_matches']}")
