"""
standalone.py — the logic-coverage skill on an EXISTING build (not a pipeline run).

Input: the customer's forms as XLSForm files, a ZIP of them, an ODM export, or the forms fetched read-only from an
OpenClinica study (reference_studies.py), plus optionally which form sits on which event.
Output (files): the XLSForms with the applied logic, the DVS workbook (checks, LOGIC_COVERAGE sheet, UAT cases from
the existing UAT generator) and the coverage report. Nothing is sent to OpenClinica: loading and read-back of the
UAT cases is the existing UAT loader's job.

  study_from_files(files, protocol_number, form_events) -> Study Specification-shaped dict
  run_existing(files, mode, protocol_number, out_dir, form_events) -> {"spec", "paths", "summary"}
mode "apply": logic is built on forms that carry none and proposed on forms that carry their own.
mode "report": nothing is changed; every gap is a proposal (the customer's forms are under test as they are).

CLI: python standalone.py <forms.zip | form.xlsx | export.xml ...> --out DIR [--mode apply|report] [--protocol ID]
"""
import contextlib
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "skills", "edc-builder", "scripts"),
           os.path.join(_ROOT, "skills", "dvs-specification", "scripts")):
    if _p not in sys.path:
        sys.path.append(_p)

import logic_coverage  # noqa: E402

SOURCE_LABEL = "existing build"


def odm_form_events(odm_bytes):
    """{form OID: [event OID, ...]} from an ODM's StudyEventDef / FormRef, in document order."""
    import xml.etree.ElementTree as ET
    out = {}
    try:
        root = ET.fromstring(odm_bytes if isinstance(odm_bytes, bytes) else str(odm_bytes).encode("utf-8"))
    except Exception:
        return out
    for ev in root.iter():
        if not ev.tag.endswith("}StudyEventDef") and ev.tag != "StudyEventDef":
            continue
        for ref in ev:
            if ref.tag.endswith("FormRef") and ref.get("FormOID") and ev.get("OID"):
                events = out.setdefault(ref.get("FormOID"), [])
                if ev.get("OID") not in events:
                    events.append(ev.get("OID"))
    return out


def _form_id(model):
    fid = str((model.get("settings") or {}).get("form_id") or model.get("form_oid") or "").strip()
    return fid[2:] if fid.upper().startswith("F_") else fid


def study_from_files(files, protocol_number="STUDY", form_events=None, tag_concepts=True):
    """A Study Specification-shaped dict for the forms in `files` ([(file name, bytes)]). Each form records where
    it came from and whether it carries logic of its own (customer_standard), which decides apply or propose.
    form_events: {form OID or id: [event OID]}; an ODM among the files supplies it when not given."""
    import standards_match as sm
    sources = sm.load_sources(list(files or []), None)
    events = {}
    for name, data in files or []:
        if sm.detect_kind(data) == "ODM_XML":
            events.update(odm_form_events(data))
    events.update(form_events or {})
    by_norm = {sm.norm_id(k): v for k, v in events.items()}
    forms = []
    for m in sources["forms"]:
        fid = _form_id(m)
        survey = [dict(r) for r in m["survey"]]
        for r in survey:
            r.setdefault("library_source", "CUSTOM")
        forms.append({
            "form_id": fid, "form_title": m.get("title") or fid, "cdash_domain": m.get("domain"),
            "survey": survey, "choices": [dict(c) for c in m.get("choices") or []],
            "settings": dict(m.get("settings") or {}) or {"form_id": fid, "form_title": m.get("title") or fid},
            "extra_cols": list(m.get("extra_cols") or []),
            "visits_assigned": list(by_norm.get(sm.norm_id(fid)) or by_norm.get(sm.norm_id(m.get("form_oid"))) or []),
            "has_repeating_group": bool(m.get("repeating")), "cross_form_dependencies": [],
            "customer_standard": {
                "form_name": m.get("title") or fid, "form_oid": m.get("form_oid"), "source": SOURCE_LABEL,
                "source_file": m.get("source_file"), "has_logic": bool(m.get("has_logic")),
                "verbatim": m.get("format") == "xlsform",
                "content_sha": sm.content_sha(survey, m.get("choices") or [], m.get("settings") or {}),
                "added_fields": [], "approved": []}})
    spec = {"study_meta": {"protocol_number": protocol_number, "input_mode": "existing_build",
                           "existing_build_files": sources["files"]},
            "forms": forms, "review_flags": {}}
    if tag_concepts:
        try:
            import cdisc_ct, cdisc_concepts
            std = cdisc_ct.load_standards(None)
            if std is not None:
                cdisc_concepts.tag_deterministic(spec, std, {})
        except Exception as e:
            spec["review_flags"].setdefault("logic_coverage", []).append(
                f"CDASH concepts could not be tagged ({type(e).__name__}): only the type-based checks apply")
    return spec


def build_forms(spec, forms_dir):
    """Write the XLSForms of `spec` with the EDC builder. Returns its build log."""
    from build_xlsforms import build_all_xlsforms
    os.makedirs(forms_dir, exist_ok=True)
    log = {k: [] for k in ("forms_built", "forms_skipped", "build_errors", "build_warnings", "placeholder_applied",
                           "oid_placeholders")}
    with contextlib.redirect_stdout(io.StringIO()):
        build_all_xlsforms(spec, forms_dir, log)
    return log


def forms_json(forms_dir):
    """The built XLSForms read back as the DVS generator takes them."""
    import openpyxl
    out = {"forms": {}}

    def rows(wb, sheet):
        if sheet not in wb.sheetnames:
            return []
        rs = list(wb[sheet].iter_rows(values_only=True))
        if not rs:
            return []
        head = [str(h or "").strip() for h in rs[0]]
        return [d for d in ({head[i]: r[i] for i in range(len(head)) if i < len(r) and r[i] is not None}
                            for r in rs[1:]) if d]

    for name in sorted(os.listdir(forms_dir)):
        if name.lower().endswith(".xlsx"):
            wb = openpyxl.load_workbook(os.path.join(forms_dir, name), read_only=True, data_only=True)
            out["forms"][name] = {"survey": rows(wb, "survey"), "choices": rows(wb, "choices")}
    return out


def original_forms_json(spec):
    """The forms exactly as given, as the DVS generator takes them (report mode: the forms are not rebuilt)."""
    import standards_match as sm
    back = {v: k for k, v in sm._TO_JSON.items()}
    out = {"forms": {}}
    for f in spec.get("forms") or []:
        survey = [{back.get(k, k): v for k, v in r.items() if k not in sm.META_KEYS and v not in (None, "")}
                  for r in f.get("survey") or [] if isinstance(r, dict)]
        out["forms"][f"{f['form_id']}.xlsx"] = {"survey": survey, "choices": f.get("choices") or []}
    return out


def write_dvs(spec, fj, path):
    from extract_dvs_from_forms import extract_dvs_data
    from generate_dvs import build_dvs
    with contextlib.redirect_stdout(io.StringIO()):
        data = extract_dvs_data(spec, fj)
        build_dvs(data, path)
    return data


def coverage_report(spec):
    """The coverage report as text (Markdown): summary, the table by category, cross-form checks, helpers."""
    st = logic_coverage.state(spec)
    lines = ["# Logic coverage report", ""] + logic_coverage.summary_lines(spec) + [""]
    sec = logic_coverage.section(spec)
    if sec:
        _t, _n, headers, rows, _w = sec
        lines += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows] + [""]
    if st.get("cross_form"):
        lines += ["## Cross-form checks", ""]
        lines += [f"- {x['form']}.{x['field']}: {x['reference']} ({x.get('status', 'added')})" for x in st["cross_form"]]
        lines.append("")
    if st.get("not_generated"):
        lines += ["## Applicable, not generated", ""]
        lines += [f"- {x['form']}.{x['field']} ({x['category']}): {x['reason']}" for x in st["not_generated"]] + [""]
    if st.get("helpers"):
        lines += [f"## Derived helper items ({len(st['helpers'])})", ""]
        lines += [f"- {h['form']}.{h['item']}: {h['action']}" for h in st["helpers"]]
    return "\n".join(lines) + "\n"


def run_existing(files, mode="apply", protocol_number="STUDY", out_dir=None, form_events=None):
    """The whole standalone run. Returns {"spec", "summary", "build_log", "dvs", "paths"}; files are written when
    out_dir is given: forms/*.xlsx (apply mode), <protocol>_DVS.xlsx, <protocol>_Logic_Coverage.md."""
    spec = study_from_files(files, protocol_number, form_events)
    summary = logic_coverage.run(spec, mode)
    result = {"spec": spec, "summary": summary, "build_log": None, "dvs": None, "paths": {}}
    if not out_dir:
        return result
    os.makedirs(out_dir, exist_ok=True)
    if mode == "apply":
        forms_dir = os.path.join(out_dir, "forms")
        result["build_log"] = build_forms(spec, forms_dir)
        fj = forms_json(forms_dir)
        result["paths"]["forms"] = forms_dir
    else:
        fj = original_forms_json(spec)
    dvs_path = os.path.join(out_dir, f"{protocol_number}_DVS.xlsx")
    result["dvs"] = write_dvs(spec, fj, dvs_path)
    report_path = os.path.join(out_dir, f"{protocol_number}_Logic_Coverage.md")
    with open(report_path, "w") as fh:
        fh.write(coverage_report(spec))
    with open(os.path.join(out_dir, f"{protocol_number}_spec.json"), "w") as fh:
        json.dump(spec, fh)
    result["paths"].update(dvs=dvs_path, report=report_path)
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("apply", "report"), default="apply")
    ap.add_argument("--protocol", default="STUDY")
    a = ap.parse_args()
    res = run_existing([(os.path.basename(p), open(p, "rb").read()) for p in a.inputs], a.mode, a.protocol, a.out)
    print("\n".join(logic_coverage.summary_lines(res["spec"])))
    if res["build_log"] is not None:
        print(f"forms built: {len(res['build_log']['forms_built'])}, errors: {len(res['build_log']['build_errors'])}")
    print("files:", res["paths"])
