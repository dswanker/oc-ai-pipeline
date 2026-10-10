"""
pipeline_modes.py — the "Pipeline Mode" entry points for a customer's EXISTING study.

  Full build   (default; a blank or missing column): today's pipeline, untouched.
  Logic + UAT  : take the existing forms, apply the logic-coverage skill (logic is built on forms that carry none,
                 proposed on forms that carry their own), deliver the updated XLSForms, the DVS and the coverage
                 report; then run UAT once the study carries those forms.
  UAT only     : take the existing forms as they are, generate the DVS from their OWN logic, run the logic-coverage
                 audit in report mode (proposals only, no form is changed), then UAT: load, browser, read-back,
                 UAT DVS Results, Traceability Matrix, Validation Report.

Neither mode analyses a protocol, matches standards, builds a study or publishes anything. The forms come from the
files in the Customer OC4 Standard(s) column (XLSForm files, a ZIP, an ODM) or, when there are none, read-only from
the OpenClinica study on the item (Study UUID + OC Subdomain).

The column does not exist on monday yet. Its specification is PIPELINE_MODE_COLUMN below (docs/PIPELINE_MODES.md);
once created, its id goes into monday_client.COL["pipeline_mode"]. Until then the column is found by its title.

Everything that touches monday or OpenClinica is passed in (`deps`), so the flow is testable without either.
"""
import io
import os
import sys
import zipfile

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_ROOT, "skills", "logic-coverage", "scripts"),):
    if _p not in sys.path:
        sys.path.append(_p)

FULL, LOGIC_UAT, UAT_ONLY = "full", "logic_uat", "uat_only"
MODE_LABELS = {FULL: "Full build", LOGIC_UAT: "Logic + UAT", UAT_ONLY: "UAT only"}
PIPELINE_MODE_COLUMN = {
    "title": "Pipeline Mode",
    "type": "status",                       # a single-choice status column (monday's dropdown with one value)
    "labels": [MODE_LABELS[FULL], MODE_LABELS[LOGIC_UAT], MODE_LABELS[UAT_ONLY]],
    "default": "(blank) — same as Full build",
    "col_key": "pipeline_mode",
}
SITE_CREATE_ENV = "UAT_ALLOW_SITE_CREATE"   # 1: the UAT load may create a test site; default: it asks instead


def _norm(text):
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def read_mode(cols, col_ids=None):
    """The mode an item asks for. `cols` is the item's {column id: column}. The column is looked up by its id when
    monday_client.COL has one, else by its title. Blank, missing or unknown: Full build."""
    col = None
    cid = (col_ids or {}).get(PIPELINE_MODE_COLUMN["col_key"])
    if cid:
        col = (cols or {}).get(cid)
    if col is None:
        col = next((c for c in (cols or {}).values() if isinstance(c, dict)
                    and _norm(c.get("title")) == _norm(PIPELINE_MODE_COLUMN["title"])), None)
    want = _norm((col or {}).get("text"))
    return next((m for m, label in MODE_LABELS.items() if want and _norm(label) == want), FULL)


def site_creation_allowed():
    return os.environ.get(SITE_CREATE_ENV, "0").strip() == "1"


def _text(cols, col_ids, key):
    return str(((cols or {}).get((col_ids or {}).get(key)) or {}).get("text") or "").strip()


async def check_preconditions(mode, cols, deps, have_files):
    """What must hold before a mode runs, checked up front. Returns {"ok", "can_uat", "messages", "token",
    "env", "sites", "pause_for_auth"}. A message says what is wrong and what to do.

    Forms: uploaded files, or an existing study to read them from. UAT (always for UAT only; for Logic + UAT only
    its second half): the study is published to Test, a test site exists or creating one is allowed, the user can
    reach the subdomain, and the saved browser login is valid."""
    ids = deps.col_ids
    sub, uuid, email = _text(cols, ids, "oc_subdomain"), _text(cols, ids, "study_uuid"), _text(cols, ids, "oc_email")
    out = {"ok": True, "can_uat": True, "messages": [], "token": None, "env": None, "sites": [],
           "pause_for_auth": False}

    def stop(msg, uat_only_problem=False):
        out["messages"].append(msg)
        out["can_uat"] = False
        if not uat_only_problem or mode == UAT_ONLY:
            out["ok"] = False

    if not have_files and not (sub and uuid):
        stop("No forms to work on: upload the study's XLSForm files (or a ZIP, or an ODM export) to the Customer "
             "OC4 Standard(s) column, or fill in Study UUID and OC Subdomain so the forms are read from the study.")
        return out
    if not (sub and uuid):
        stop("UAT needs the study in OpenClinica: fill in Study UUID and OC Subdomain.", True)
        return out
    try:
        out["token"] = await deps.get_token(sub, email)
    except Exception as e:
        stop(f"{email or 'The OC user on this item'} cannot reach {sub} ({type(e).__name__}): check the OC "
             f"Subdomain and that this user has access to it, then authenticate again.", bool(have_files))
        return out
    try:
        out["env"] = await deps.test_environment(sub, uuid, email)
    except Exception as e:
        stop(f"The study is not published to Test ({str(e)[:140]}). Publish it to the Test environment first.", True)
        return out
    if str(out["env"].get("status") or "").upper() != "AVAILABLE":
        stop(f"The study's Test environment is {out['env'].get('status') or 'not available'}, not AVAILABLE. "
             f"Publish the study to Test first.", True)
    try:
        out["sites"] = await deps.list_sites(sub, out["env"].get("uuid"), email)
    except Exception as e:
        stop(f"The sites of the Test environment could not be read ({type(e).__name__}).", True)
    if out["can_uat"] and not out["sites"] and not site_creation_allowed():
        stop("The Test environment has no site, and this run may not create one. Add a test site in OpenClinica, "
             f"or allow the run to create one ({SITE_CREATE_ENV}=1), then run again.", True)
    if out["can_uat"]:
        try:
            valid = await deps.session_valid(sub, email)
        except Exception:
            valid = False
        if not valid:
            out["pause_for_auth"] = True
            stop("The saved browser login is not valid: authenticate with the link on the item, then run again "
                 "(a saved login is usable for about an hour).", True)
    return out


def _zip_forms(forms_dir, protocol):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(forms_dir)):
            if name.lower().endswith(".xlsx"):
                z.write(os.path.join(forms_dir, name), f"{protocol}_Updated_Forms/forms/{name}")
    return buf.getvalue()


async def run_mode(item_id, cols, mode, deps):
    """Run "Logic + UAT" or "UAT only" for one item. Returns {"status", "applied", "proposed", "uat": ...}.
    status: "stopped" (a precondition; nothing was produced), "paused_for_auth", "files_ready" (Logic + UAT
    produced the files; the study does not carry them yet), "uat_done", "uat_failed", "failed"."""
    import tempfile
    import logic_coverage
    import standalone
    ids = deps.col_ids
    log = deps.append_log
    result = {"status": "failed", "applied": 0, "proposed": 0, "uat": None}
    proto = (_text(cols, ids, "protocol_number") or "STUDY").replace("/", "-").replace(" ", "_")
    sub, uuid, email = _text(cols, ids, "oc_subdomain"), _text(cols, ids, "study_uuid"), _text(cols, ids, "oc_email")
    await log(item_id, f"Pipeline Mode: {MODE_LABELS[mode]}. No protocol analysis, no standards matching, no build "
                       f"and no publish: the existing forms are "
                       + ("tested as they are." if mode == UAT_ONLY else "given the missing logic, then tested."))
    try:
        files = [(n, b) for n, b in (await deps.download_files(item_id, ids["oc_standard"]) or []) if b]
    except Exception as e:
        files = []
        await log(item_id, f"The files in the Customer OC4 Standard(s) column could not be read ({type(e).__name__}).")
    pre = await check_preconditions(mode, cols, deps, bool(files))
    for m in pre["messages"]:
        await log(item_id, f"⚠️ {MODE_LABELS[mode]}: {m}")
    if pre["pause_for_auth"] and mode == UAT_ONLY:
        await deps.pause_for_auth(item_id)
        result["status"] = "paused_for_auth"
        return result
    if not pre["ok"]:
        await deps.set_status(item_id, "failed")
        result["status"] = "stopped"
        return result

    # ── the forms ──
    source = "the files uploaded to the item"
    if not files:
        got = await deps.fetch_study_forms(sub, uuid, pre["token"])
        for line in got.get("log") or []:
            await log(item_id, line)
        files, source = got.get("forms") or [], f"the OpenClinica study on {sub} (read-only)"
        if not files:
            await log(item_id, f"⚠️ {MODE_LABELS[mode]}: no form could be read from the study "
                               f"({got.get('note') or 'no uploaded form versions'}). Upload the XLSForm files instead.")
            await deps.set_status(item_id, "failed")
            result["status"] = "stopped"
            return result
    form_events = {}
    if pre["env"] and pre["token"]:
        try:   # which form sits on which event, from the published study (the UAT cases need it)
            xml = await deps.fetch_metadata(sub, pre["env"].get("oid") or _text(cols, ids, "study_oid"), pre["token"])
            form_events = standalone.odm_form_events((xml or "").encode("utf-8"))
        except Exception as e:
            await log(item_id, f"The study's events could not be read ({type(e).__name__}); UAT cases will lack "
                               f"their visit.")
    if pre["env"] and pre["env"].get("oid") and not _text(cols, ids, "study_oid"):
        await deps.set_text(item_id, ids["study_oid"], pre["env"]["oid"])

    spec = standalone.study_from_files(files, proto, form_events)
    n_forms = len(spec["forms"])
    if not n_forms:
        await log(item_id, f"⚠️ {MODE_LABELS[mode]}: none of the files from {source} is an XLSForm or an ODM.")
        await deps.set_status(item_id, "failed")
        result["status"] = "stopped"
        return result
    placed = sum(1 for f in spec["forms"] if f.get("visits_assigned"))
    await log(item_id, f"{MODE_LABELS[mode]}: {n_forms} form(s) from {source}; {placed} placed on a visit of the "
                       f"study.")
    if await deps.has_protocol(item_id):
        await log(item_id, "A protocol is on the item. In this mode it is not analysed and changes no form.")

    # ── logic coverage: report only for UAT only, apply for Logic + UAT ──
    summary = logic_coverage.run(spec, "report" if mode == UAT_ONLY else "apply")
    result["applied"], result["proposed"] = summary["counts"]["applied"], summary["counts"]["proposed"]
    helpers_changed = sum(1 for h in summary.get("helpers") or [] if not h["action"].startswith("reported"))
    await log(item_id, "\n".join(logic_coverage.summary_lines(spec)))
    with tempfile.TemporaryDirectory() as tmp:
        if mode == LOGIC_UAT:
            forms_dir = os.path.join(tmp, "forms")
            build_log = standalone.build_forms(spec, forms_dir)
            if build_log["build_errors"]:
                await log(item_id, f"⚠️ Logic + UAT: {len(build_log['build_errors'])} form(s) did not build: "
                                   + "; ".join(str(e)[:120] for e in build_log["build_errors"][:5]))
            fj = standalone.forms_json(forms_dir)
            await deps.upload_file(item_id, ids["edc_build"], f"{proto}_Updated_Forms.zip", _zip_forms(forms_dir, proto))
        else:
            fj = standalone.original_forms_json(spec)
        dvs_path = os.path.join(tmp, f"{proto}_DVS.xlsx")
        dvs = standalone.write_dvs(spec, fj, dvs_path)
        with open(dvs_path, "rb") as fh:
            await deps.upload_file(item_id, ids["dvs_output"], f"{proto}_DVS.xlsx", fh.read())
    drafts = sum(1 for r in dvs["dvs_oc4"] if r.get("Status") != "Proposed")
    await log(item_id, f"DVS generated from the forms: {drafts} check(s) in the forms, "
                       f"{len(dvs['dvs_oc4']) - drafts} proposed, {len(dvs['uat_cases'])} UAT case(s). "
                       f"Coverage per form and category: LOGIC_COVERAGE sheet.")

    if mode == LOGIC_UAT and (result["applied"] or helpers_changed):
        await log(item_id, f"Logic + UAT: {result['applied']} check(s) were added to the forms"
                           + (f" and {helpers_changed} derived item(s) made calculated or read-only" if helpers_changed else "")
                           + ". The study in OpenClinica does not carry these forms yet, so UAT was not run. Upload "
                             "and publish the forms of the Updated Forms ZIP (EDC Build column), then set Send to AI "
                             "again with Pipeline Mode = Logic + UAT: with nothing left to add, the run goes on to UAT.")
        await deps.set_status(item_id, "build_complete")
        result["status"] = "files_ready"
        return result
    if not pre["can_uat"]:
        if pre["pause_for_auth"]:
            await deps.pause_for_auth(item_id)
            result["status"] = "paused_for_auth"
        else:
            await deps.set_status(item_id, "build_complete")
            result["status"] = "files_ready"
        return result

    # ── UAT: the existing loader (load, browser, read-back, results, matrix, report) ──
    titles = {f"F_{f['form_id']}": f.get("form_title") or "" for f in spec["forms"]}
    await deps.set_status(item_id, "uat_loading")
    try:
        uat = await deps.run_uat_loader(item_id, titles)
    except Exception as e:
        await log(item_id, f"UAT Loader ERROR: {type(e).__name__}: {str(e)[:200]}")
        await deps.set_status(item_id, "uat_failed")
        result["status"] = "uat_failed"
        return result
    result["uat"] = uat
    ok = bool(uat.get("success"))
    if not ok:
        await log(item_id, f"UAT load FAILED: {'; '.join(uat.get('errors') or [])[:400]}")
    await deps.set_status(item_id, "all_complete" if ok else "uat_failed")
    result["status"] = "uat_done" if ok else "uat_failed"
    return result
