"""cdisc_cdash.py: deterministic CDASH field-definition layer for the OC AI Pipeline. No AI, no tokens.

Source: CDASHIG metadata (member CSV, CDISC_STANDARDS_DIR; see cdisc_standards/README.md).
Applied by pipeline._apply_cdisc_ct after the CT layer, same hierarchy:
  * every field with a CDASH variable gets descriptive metadata in row["cdash"] (domain, variable,
    CDASHIG Core, SDTM target, version), whatever tier it came from;
  * CDASH-default fields only (not customer/OC-standard fields): an empty hint gets the CDASHIG CRF
    completion instruction, an empty label gets the CDASHIG prompt, when the text is free of
    placeholders and format instructions;
  * hints exclude identifiers OC4 captures itself, notes addressed to the sponsor/CRF designer, and
    text that cites raw submission codes site staff never see.
Nothing is written to OC4's bind::oc:briefdescription / description columns.
"""
import csv, os, re
import cdisc_ct

# Captured by OC4 itself or never a CRF question: no hint.
_NOT_CRF_QUESTIONS = {"STUDYID", "SITEID", "SUBJID", "USUBJID", "INVID", "INVNAM", "SPONSOR", "VISIT",
                      "VISITNUM", "EPOCH", "DOMAIN", "SEQ"}
_PLACEHOLDER = re.compile(r"\[|\]|DD-MON-YYYY|YYYY|using this format|format \(", re.I)
# Guidance written for the sponsor / CRF designer rather than the person entering data.
_DESIGNER_NOTE = re.compile(r"sponsor|if collected|self-report|asked about|code ?list|controlled terminology", re.I)
# Cites raw codes ("Y", F (female)) that site staff never see in OC4.
_RAW_CODE = re.compile(r'\("[A-Z]{1,3}"\)|\b[A-Z]{1,3} \((?:[a-z]+)\)')
HINT_MAX = 300

_MEMO = {}


def load_cdashig_fields():
    """(DOMAIN, VARIABLE) -> metadata dict from the CDASHIG CSV; {} if the member file is absent."""
    if "fields" in _MEMO:
        return _MEMO["fields"]
    m = cdisc_ct.manifest()
    p = os.path.join(cdisc_ct.member_dir(), m.get("cdashig_file", ""))
    out = {}
    if m.get("cdashig_file") and os.path.exists(p):
        with open(p, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                dom, var = (r.get("Domain") or "").strip().upper(), (r.get("CDASHIG Variable") or "").strip().upper()
                if not dom or not var:
                    continue
                cur = out.get((dom, var))
                rec = {"domain": dom, "variable": var, "label": (r.get("CDASHIG Variable Label") or "").strip(),
                       "question": (r.get("Question Text") or "").strip(), "prompt": (r.get("Prompt") or "").strip(),
                       "instruction": (r.get("Case Report Form Completion Instructions") or "").strip(),
                       "core": (r.get("CDASHIG Core") or "").strip(), "type": (r.get("Type") or "").strip(),
                       "sdtm_target": (r.get("SDTMIG Target") or "").strip(),
                       "mapping": (r.get("Mapping Instructions") or "").strip()}
                # first row wins; a later scenario row only fills gaps
                out[(dom, var)] = rec if cur is None else {k: cur[k] or rec[k] for k in cur}
    _MEMO["fields"] = out
    return out


def _usable(text):
    return bool(text) and len(text) <= HINT_MAX and not _PLACEHOLDER.search(text)


def _usable_hint(var, text):
    return (var not in _NOT_CRF_QUESTIONS and _usable(text)
            and not _DESIGNER_NOTE.search(text) and not _RAW_CODE.search(text))


def _resolve(fields, by_var, domain, var):
    rec = fields.get((domain, var))
    if rec:
        return rec
    cands = by_var.get(var) or []
    if len(cands) == 1:
        return cands[0]
    for c in cands:  # variable carries its domain prefix (AESEV -> AE)
        if var.startswith(c["domain"]):
            return c
    targets = {c["sdtm_target"] for c in cands if c["sdtm_target"]}
    if cands and len(targets) <= 1:
        # generic variable (VISIT, VISDAT): one SDTM target (or none) across every domain it appears in
        return next((c for c in cands if c["sdtm_target"]), cands[0])
    return None


def apply_to_spec(spec, std, protected_vars=frozenset()):
    """Apply CDASH field definitions. Returns a summary dict. Idempotent."""
    fields = load_cdashig_fields()
    if not fields:
        return {"skipped": "CDASHIG metadata not available"}
    by_var = {}
    for (d, v), rec in fields.items():
        by_var.setdefault(v, []).append(rec)
    protected = {p.upper() for p in protected_vars}
    n_meta = n_hint = n_label = 0
    for f in spec.get("forms") or []:
        if not isinstance(f, dict):
            continue
        domain = str(f.get("cdash_domain") or "").strip().upper()
        for row in f.get("survey") or []:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            t = str(row.get("type") or "").strip().lower()
            if t in ("begin group", "end group", "begin repeat", "end repeat", "note"):
                continue
            var = cdisc_ct.field_variable(row.get("name"), std)
            rec = _resolve(fields, by_var, domain, var)
            if not rec:
                continue
            prev = row.get("cdash") if isinstance(row.get("cdash"), dict) else {}
            meta = {"domain": rec["domain"], "variable": rec["variable"], "core": rec["core"],
                    "sdtm_target": rec["sdtm_target"], "cdashig": std.versions.get("cdashig")}
            meta.update({k: prev[k] for k in ("hint_from_cdashig", "label_from_cdashig") if prev.get(k)})
            row["cdash"] = meta
            n_meta += 1
            if rec["variable"] in protected or str(row.get("name")).upper() in protected:
                continue  # customer / OC standard field: descriptive metadata only
            if not str(row.get("hint") or "").strip() and _usable_hint(rec["variable"], rec["instruction"]):
                row["hint"] = rec["instruction"]
                meta["hint_from_cdashig"] = True
                n_hint += 1
            if not str(row.get("label") or "").strip() and _usable(rec["prompt"]):
                row["label"] = rec["prompt"]
                meta["label_from_cdashig"] = True
                n_label += 1
    return {"fields_with_cdash_metadata": n_meta, "hints_added": n_hint, "labels_added": n_label}
