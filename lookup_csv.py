"""
lookup_csv.py — the study's lookup CSVs (pulldata / external instance / select-from-file) work in every study.

Two faults of the 2026-10-10 build are closed here.

1. One name per lookup. A form says pulldata('<name>', ...) and OpenClinica looks for the media file <name>.csv
   attached to that form, exact spelling. The reference used to be built from the lower-cased protocol number
   and the file from the protocol number as written, so a protocol with an upper-case letter or a character the
   two sides treated differently produced a reference with no file. The name is now made once
   (lookup_name) and used for the file, for every reference and for the upload. The lookup CSVs were also
   never attached to the form upload: the uploader only looked beside the form, the lookups are in csv/.

2. No rule compares a looked-up display label. The timepoint lookup gains stable columns (visit_type, baseline)
   next to the label, and a rule that compared the label to a literal is rewritten to compare a stable value:
   the event OID when the literal is a label of the lookup, the baseline flag when it is the literal the
   pipeline's own visit-date form used to carry. A renamed visit then changes no rule.

  normalize_spec(spec)        names, references, stable columns, rules. Idempotent. Report in
                              study_meta.lookup_csv.
  xlsx_references(path)       the CSV files a built form needs ({"name.csv": {columns it reads}})
  validate_build(forms, csv)  every reference has its CSV, exact name, with the columns read: else an error
  files_for_form(path)        the CSV files to attach to that form's upload

Switches: LOOKUP_CSV_CANONICAL=0 (names left as they are), LOOKUP_STABLE_CODES=0 (rules left as they are),
LOOKUP_CSV_ATTACH=0 (only the files beside the form are attached, as before).
"""
import os
import re
from pathlib import Path

TPT, LABRANGES = "tpt", "labranges"
KINDS = (TPT, LABRANGES)
SPEC_KEY = {TPT: "timepoint_csv", LABRANGES: "labranges_csv"}
LABEL_COLUMN, EVENT_COLUMN, BASELINE_COLUMN, TYPE_COLUMN = "timepoint", "event", "baseline", "visit_type"
TPT_COLUMNS = [EVENT_COLUMN, LABEL_COLUMN, "visit_number", TYPE_COLUMN, BASELINE_COLUMN]
BASELINE_ITEM = "TPTBASE"
# The literal the pipeline's own visit-date form compared the timepoint label to until this change.
LEGACY_BASELINE_LITERAL = "Baseline"
FIRST_DOSE_CONCEPTS = ("EXSTDAT", "ECSTDAT")
RESERVED_INSTANCES = {"clinicaldata"}
EXPRESSION_KEYS = ("calculation", "relevant", "constraint", "required", "choice_filter", "default", "trigger")

_PULLDATA = re.compile(r"pulldata\s*\(\s*'([^']+)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*((?:[^()]|\([^()]*\))*)\)")
_PULLDATA_NAME = re.compile(r"(pulldata\s*\(\s*')([^']+)(')")
_INSTANCE = re.compile(r"(instance\s*\(\s*')([^']+)('\s*\))")
_FROM_FILE = re.compile(r"select_(?:one|multiple)_from_file\s+(\S+)", re.I)
_EXTERNAL_ROW = re.compile(r"^(?:csv|xml)-external$", re.I)


def _on(name):
    return os.environ.get(name, "1").strip() != "0"


def canonical_enabled():
    return _on("LOOKUP_CSV_CANONICAL")


def stable_enabled():
    return _on("LOOKUP_STABLE_CODES")


def attach_enabled():
    return _on("LOOKUP_CSV_ATTACH")


def canonical_stem(text) -> str:
    """Lower case, letters, digits and underscore only: the one spelling used for a file and its references."""
    return re.sub(r"[^a-z0-9_]", "", str(text or "").strip().lower()) or "study"


def lookup_name(protocol, kind) -> str:
    """The name of a study lookup: pulldata('<this>', ...) in a form, <this>.csv in the build and the upload."""
    return f"{canonical_stem(protocol)}_{kind}"


def _kind_of(name):
    """Which of the pipeline's two lookups a reference or file name means, by its ending. None for any other."""
    stem = re.sub(r"\.csv$", "", str(name or "").strip(), flags=re.I).lower()
    for kind in KINDS:
        if stem == kind or stem.endswith("_" + kind):
            return kind
    return None


def _protocol(spec, protocol=None):
    return (spec.get("study_meta") or {}).get("protocol_number") or protocol or "STUDY"


def _forms(spec):
    return [f for f in spec.get("forms") or [] if isinstance(f, dict)]


def _rows(form):
    return [r for r in form.get("survey") or [] if isinstance(r, dict)]


# ── 1. names ──────────────────────────────────────────────────────────────────

def _rename(spec, names, report):
    for kind in KINDS:
        block = spec.get(SPEC_KEY[kind])
        if isinstance(block, dict):
            want = names[kind] + ".csv"
            if block.get("filename") != want:
                report["files"].append({"was": block.get("filename"), "now": want})
                block["filename"] = want

    def fix(m):
        kind = _kind_of(m.group(2))
        if kind and m.group(2) != names[kind]:
            report["references"] += 1
            return m.group(1) + names[kind] + m.group(3)
        return m.group(0)

    for form in _forms(spec):
        for row in _rows(form):
            for key, value in list(row.items()):
                if isinstance(value, str) and ("pulldata" in value or "instance" in value):
                    row[key] = _INSTANCE.sub(fix, _PULLDATA_NAME.sub(fix, value))
            if _EXTERNAL_ROW.match(str(row.get("type") or "").strip()):
                kind = _kind_of(row.get("name"))
                if kind and row.get("name") != names[kind]:
                    report["references"] += 1
                    row["name"] = names[kind]


# ── 2. stable columns and rules ───────────────────────────────────────────────

def _event_types(spec):
    out = {}
    for ev in spec.get("events") or []:
        if isinstance(ev, dict) and ev.get("event_oid"):
            kind = str(ev.get("event_type") or "").strip()
            out[ev["event_oid"]] = kind.upper() if kind else ("COMMON" if ev.get("is_repeating") else "")
    return out


def baseline_event(spec):
    """The event of first dosing: the earliest visit of the timepoint lookup that carries a form with the
    exposure start date (CDASH EXSTDAT / ECSTDAT). None when the study has no such item."""
    order = [r.get("event") for r in ((spec.get("timepoint_csv") or {}).get("rows") or []) if isinstance(r, dict)]
    types = _event_types(spec)
    events = set()
    for form in _forms(spec):
        if any(str(r.get("concept") or "").upper() in FIRST_DOSE_CONCEPTS for r in _rows(form)):
            events.update(v for v in form.get("visits_assigned") or [] if types.get(v) != "COMMON")
    hits = [e for e in order if e in events]
    return hits[0] if hits else None


def _stable_columns(spec, report):
    rows = (spec.get("timepoint_csv") or {}).get("rows")
    if not isinstance(rows, list):
        return
    types, base = _event_types(spec), baseline_event(spec)
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = types.get(row.get("event")) or str(row.get("type") or "").strip().upper() or "SCHEDULED"
        row[TYPE_COLUMN] = kind
        row[BASELINE_COLUMN] = "1" if base and row.get("event") == base else "0"
    report["baseline_event"] = base


def _label_items(form, tpt_name):
    """{item name: the event expression of its lookup} for items that hold the display label of a visit: the
    label column pulled from the timepoint lookup, and items calculated as exactly such an item."""
    items = {}
    for row in _rows(form):
        m = _PULLDATA.fullmatch(str(row.get("calculation") or "").strip())
        if m and m.group(1) == tpt_name and m.group(2) == LABEL_COLUMN and m.group(3) == EVENT_COLUMN and row.get("name"):
            items[row["name"]] = m.group(4).strip()
    grew = True
    while grew:
        grew = False
        for row in _rows(form):
            m = re.fullmatch(r"\$\{(\w+)\}", str(row.get("calculation") or "").strip())
            if m and m.group(1) in items and row.get("name") and row["name"] not in items:
                items[row["name"]] = items[m.group(1)]
                grew = True
    return items


def _stable_rules(spec, tpt_name, report):
    labels = {}
    for row in (spec.get("timepoint_csv") or {}).get("rows") or []:
        if isinstance(row, dict) and row.get("event"):
            labels.setdefault(str(row.get("timepoint") or "").strip(), []).append(row["event"])
    for form in _forms(spec):
        items = _label_items(form, tpt_name)
        if not items:
            continue
        needs_flag = {}

        def fix(m, _items=items, _form=form, _needs=needs_flag):
            item, op, literal = m.group(1), m.group(2), m.group(3)
            if item not in _items:
                return m.group(0)
            arg, events = _items[item], labels.get(literal.strip())
            if events:
                parts = [f"{arg} {op} '{e}'" for e in events]
                new = parts[0] if len(parts) == 1 else "(" + (" or " if op == "=" else " and ").join(parts) + ")"
            elif literal.strip().lower() == LEGACY_BASELINE_LITERAL.lower():
                _needs[item] = arg
                new = f"${{{BASELINE_ITEM}}} {op} '1'"
            else:
                report["unresolved"].append({"form": _form.get("form_id"), "item": item, "literal": literal})
                return m.group(0)
            report["rules"].append({"form": _form.get("form_id"), "was": m.group(0), "now": new})
            return new

        pattern = re.compile(r"\$\{(\w+)\}\s*(!=|=)\s*'([^']*)'")
        for row in _rows(form):
            for key in EXPRESSION_KEYS:
                if isinstance(row.get(key), str) and "${" in row[key]:
                    row[key] = pattern.sub(fix, row[key])
        if needs_flag and not any(r.get("name") == BASELINE_ITEM for r in _rows(form)):
            item, arg = next(iter(needs_flag.items()))
            at = next(i for i, r in enumerate(form["survey"]) if isinstance(r, dict) and r.get("name") == item)
            src = form["survey"][at]
            flag = {k: src[k] for k in ("bind__oc_itemgroup", "completion_status", "library_source", "flag_reason")
                    if k in src}
            flag.update(type="calculate", name=BASELINE_ITEM, label="",
                        calculation=f"pulldata('{tpt_name}','{BASELINE_COLUMN}','{EVENT_COLUMN}',{arg})")
            form["survey"].insert(at + 1, flag)


def normalize_spec(spec, protocol=None) -> dict:
    """Bring the lookups of `spec` to one name and its rules to stable values, in place. Returns the report
    (also kept in study_meta.lookup_csv): files renamed, references rewritten, the baseline event, rules
    converted, comparisons with a literal that is no label of the lookup (they can never be true)."""
    report = {"files": [], "references": 0, "baseline_event": None, "rules": [], "unresolved": []}
    if not isinstance(spec, dict):
        return report
    names = {k: lookup_name(_protocol(spec, protocol), k) for k in KINDS}
    if canonical_enabled():
        _rename(spec, names, report)
    tpt_name = re.sub(r"\.csv$", "", str((spec.get("timepoint_csv") or {}).get("filename") or names[TPT] + ".csv"),
                      flags=re.I)
    if stable_enabled():
        _stable_columns(spec, report)
        _stable_rules(spec, tpt_name, report)
    meta = spec.setdefault("study_meta", {})
    if isinstance(meta, dict):
        kept = meta.get("lookup_csv") if isinstance(meta.get("lookup_csv"), dict) else {}
        meta["lookup_csv"] = {
            "names": {k: names[k] for k in KINDS}, "baseline_event": report["baseline_event"],
            "files_renamed": (kept.get("files_renamed") or []) + report["files"],
            "references_rewritten": int(kept.get("references_rewritten") or 0) + report["references"],
            "rules_converted": (kept.get("rules_converted") or []) + report["rules"],
            "unresolved": report["unresolved"]}
    return report


def dov_rows_rule(tpt_name):
    """The calculation of the baseline flag for a form that looks its visit up by the current event."""
    return f"pulldata('{tpt_name}','{BASELINE_COLUMN}','{EVENT_COLUMN}',${{EVENT_CF}})"


# ── 3. what a built form needs, and whether the build has it ──────────────────

def text_references(text, into=None) -> dict:
    """{"name.csv": {columns read}} for the lookups one cell names."""
    out = into if into is not None else {}
    text = str(text or "")
    for m in _PULLDATA.finditer(text):
        out.setdefault(m.group(1) + ".csv", set()).update(c for c in (m.group(2), m.group(3)) if c)
    for m in _PULLDATA_NAME.finditer(text):
        out.setdefault(m.group(2) + ".csv", set())
    for m in _FROM_FILE.finditer(text):
        out.setdefault(m.group(1), set())
    for m in _INSTANCE.finditer(text):
        out.setdefault("instance:" + m.group(2), set())
    return out


def xlsx_references(xlsx_path) -> dict:
    """{"name.csv": {columns read}} for a built XLSForm. instance('x') counts unless x is a reserved instance,
    one of the form's own choice lists, or a source the form declares in bind::oc:external."""
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    try:
        refs, own = {}, set(RESERVED_INSTANCES)
        if "choices" in wb.sheetnames:
            rows = list(wb["choices"].iter_rows(values_only=True))
            head = [str(h or "").strip().lower() for h in rows[0]] if rows else []
            at = head.index("list_name") if "list_name" in head else (head.index("list name") if "list name" in head else 0)
            own.update(str(r[at]).strip() for r in rows[1:] if r and len(r) > at and r[at])
        if "survey" in wb.sheetnames:
            rows = list(wb["survey"].iter_rows(values_only=True))
            head = [str(h or "").strip().lower() for h in rows[0]] if rows else []
            ext = [i for i, h in enumerate(head) if "external" in h]
            for row in rows[1:]:
                own.update(str(row[i]).strip() for i in ext if i < len(row) and row[i])
                for cell in row:
                    if isinstance(cell, str):
                        text_references(cell, refs)
        out = {}
        for name, cols in refs.items():
            if name.startswith("instance:"):
                if name[9:] in own:
                    continue
                name = name[9:] + ".csv"
            out.setdefault(name, set()).update(cols)
        return out
    finally:
        wb.close()


def _csv_header(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [c.strip() for c in fh.readline().strip().split(",")]
    except Exception:
        return []


def validate_build(forms_dir, csv_dirs) -> list:
    """[{form_id, error}] for every lookup a built form names that the build does not hold under that exact
    name, or that lacks a column the form reads."""
    have = {}
    for d in [forms_dir] + list(csv_dirs or []):
        if d and os.path.isdir(d):
            for name in os.listdir(d):
                if name.lower().endswith(".csv"):
                    have.setdefault(name, os.path.join(d, name))
    errors = []
    for fname in sorted(os.listdir(forms_dir)) if os.path.isdir(forms_dir) else []:
        if not fname.lower().endswith(".xlsx"):
            continue
        for name, cols in sorted(xlsx_references(os.path.join(forms_dir, fname)).items()):
            form_id = os.path.splitext(fname)[0]
            if name not in have:
                near = [h for h in have if h.lower() == name.lower()]
                errors.append({"form_id": form_id, "error": (
                    f"lookup file {name} is referenced by the form but is not in the build"
                    + (f" (the build has {near[0]}: the name must match exactly, including case)" if near else ""))})
                continue
            missing = sorted(c for c in cols if c not in _csv_header(have[name]))
            if missing:
                errors.append({"form_id": form_id, "error": (
                    f"lookup file {name} has no column {', '.join(missing)} that the form reads")})
    return errors


def files_for_form(xlsx_path, candidates=None) -> list:
    """The CSV files to upload with a form: those it references, found beside it or in the build's csv/ folder."""
    xlsx_path = Path(xlsx_path)
    if candidates is None:
        candidates = sorted(xlsx_path.parent.glob("*.csv"))
        if attach_enabled():
            candidates += sorted((xlsx_path.parent.parent / "csv").glob("*.csv"))
    wanted = set(xlsx_references(xlsx_path))
    seen, out = set(), []
    for p in candidates:
        p = Path(p)
        if p.name in wanted and p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return sorted(out, key=lambda p: p.name)
