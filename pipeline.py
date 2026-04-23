"""
pipeline.py — oc-ai-pipeline orchestration

Architecture
────────────
  call_claude()  → JSON text  (analysis, fast, no code execution)
  run_skill()    → real binary files via Skills API + code execution

Flow (fresh run):
  1. call_claude  : protocol PDF  → Study Spec JSON
  2. run_skill    : JSON          → Study Spec PDF + XLSX
  3. call_claude  : JSON          → Protocol Summary JSON
  4. run_skill    : JSON          → Protocol Summary PDF
  5. run_skill    : JSON          → Quote PDFs + XLSXs   (pricing-quote skill)
  6. run_skill    : JSON          → EDC Build ZIP         (edc-builder skill)
  7. run_skill    : JSON + ZIP    → DVS XLSX              (dvs-specification skill)

Human-in-the-loop paths:
  A. Edited Study Spec XLSX uploaded  → skip steps 1-2, run 3-7
  B. Edited Build ZIP uploaded        → skip steps 1-6, run 7 only
  C. Edited DVS uploaded              → translate changes → rebuild ZIP + DVS
  D. Edited Quote XLSX uploaded       → regenerate Quote PDFs only
  E. Edited SOE CSV uploaded          → update SOE in OpenClinica
"""

import asyncio, io, json, os, sys, tempfile, zipfile, datetime as _dt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from monday_client import (get_item, download_file, upload_file, set_status,
                            append_log, set_text, download_column_file, COL)
from claude_client  import call_claude, extract_json, run_skill
from prompts        import (
    EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT,
    GENERATE_STUDY_SPEC_PROMPT, GENERATE_PROTOCOL_SUMMARY_PROMPT,
    PRICING_QUOTE_PROMPT, EDC_BUILD_PROMPT, DVS_PROMPT,
    DVS_TRANSLATE_PROMPT, SPEC_FROM_BUILD_PROMPT,
    QUOTE_PDF_FROM_XLSX_PROMPT,
)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills')

STATUS = {
    "not_started":            "Not Started",
    "analysis_running":       "Analysis Running",
    "analysis_complete":      "Analysis Complete",
    "build_pricing_running":  "Build + Pricing Running",
    "build_complete":         "Build Complete",
    "pricing_complete":       "Pricing Complete",
    "dvs_running":            "DVS Running",
    "dvs_complete":           "DVS Complete — Awaiting Review",
    "creating_oc_study":      "Creating OC Study",
    "all_complete":           "All Complete",
    "failed":                 "Failed",
}

SKILL_IDS = {
    "protocol_analysis": os.environ.get("SKILL_ID_PROTOCOL_ANALYSIS", ""),
    "pricing_quote":     os.environ.get("SKILL_ID_PRICING_QUOTE",     ""),
    "edc_builder":       os.environ.get("SKILL_ID_EDC_BUILDER",       ""),
    "dvs_specification": os.environ.get("SKILL_ID_DVS_SPECIFICATION", ""),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find(files: dict, *patterns) -> bytes | None:
    """Return bytes for first filename ending with any pattern."""
    for pat in patterns:
        for name, data in files.items():
            if name.lower().endswith(pat.lower()):
                return data
    return None


def _xl_header_row(ws, headers, bg="1B3A6B", fg="FFFFFF"):
    fill = PatternFill("solid", fgColor=bg)
    font = Font(name="Arial", bold=True, color=fg, size=10)
    aln  = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font, cell.fill, cell.alignment = font, fill, aln

def _xl_data_row(ws, values, bold=False):
    ws.append(values)
    for cell in ws[ws.max_row]:
        cell.font = Font(name="Arial", bold=bold, size=9)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

def _xl_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# ── XLSForm ZIP builder (local, from JSON) ────────────────────────────────────

def _xlsform_zip(build_json):
    """Convert EDC Build JSON into a ZIP of XLSForm xlsx files. Returns bytes."""
    forms   = build_json.get("forms", {})
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, form_data in forms.items():

            if filename.endswith('.csv'):
                if isinstance(form_data, str):
                    zf.writestr(filename, form_data)
                elif isinstance(form_data, list):
                    import csv as _csv
                    cbuf = io.StringIO()
                    if form_data:
                        writer = _csv.DictWriter(cbuf, fieldnames=form_data[0].keys())
                        writer.writeheader()
                        writer.writerows(form_data)
                    zf.writestr(filename, cbuf.getvalue())
                continue

            wb   = Workbook()
            ws_s = wb.active
            ws_s.title = "survey"

            survey = form_data.get("survey", [])
            if survey:
                hdrs = list(survey[0].keys())
                _xl_header_row(ws_s, hdrs)
                for row in survey:
                    _xl_data_row(ws_s, [row.get(h, "") for h in hdrs])

            ws_c = wb.create_sheet("choices")
            choices = form_data.get("choices", [])
            if choices:
                hdrs = list(choices[0].keys())
                _xl_header_row(ws_c, hdrs)
                for row in choices:
                    _xl_data_row(ws_c, [row.get(h, "") for h in hdrs])

            ws_t = wb.create_sheet("settings")
            settings = form_data.get("settings", {})
            if settings:
                _xl_header_row(ws_t, list(settings.keys()))
                _xl_data_row(ws_t, list(settings.values()))

            xbuf = io.BytesIO()
            wb.save(xbuf)
            zf.writestr(filename, xbuf.getvalue())

        checklist = build_json.get("study_checklist")
        if checklist and isinstance(checklist, list) and checklist:
            import csv as _csv
            cbuf = io.StringIO()
            writer = _csv.DictWriter(cbuf, fieldnames=checklist[0].keys())
            writer.writeheader()
            writer.writerows(checklist)
            zf.writestr("study_checklist.csv", cbuf.getvalue())

    zip_buf.seek(0)
    return zip_buf.getvalue()


def _read_zip_xlsforms(zip_bytes):
    """Read a ZIP of XLSForm xlsx files. Returns forms dict."""
    import openpyxl
    forms = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith('.xlsx') or name.startswith('__'):
                continue
            src = openpyxl.load_workbook(io.BytesIO(zf.read(name)))
            form_data = {}
            for sheet_name in ['survey', 'choices', 'settings']:
                if sheet_name in src.sheetnames:
                    ws   = src[sheet_name]
                    rows = list(ws.values)
                    if not rows:
                        form_data[sheet_name] = [] if sheet_name != 'settings' else {}
                        continue
                    headers = [str(h).strip() if h else '' for h in rows[0]]
                    if sheet_name == 'settings':
                        form_data[sheet_name] = dict(zip(headers, [
                            str(v) if v is not None else '' for v in rows[1]
                        ])) if len(rows) > 1 else {}
                    else:
                        form_data[sheet_name] = [
                            {h: (str(v) if v is not None else '')
                             for h, v in zip(headers, row)}
                            for row in rows[1:]
                            if any(v is not None for v in row)
                        ]
                else:
                    form_data[sheet_name] = [] if sheet_name != 'settings' else {}
            forms[os.path.basename(name)] = form_data
    print(f"Read {len(forms)} XLSForm(s) from ZIP", flush=True)
    return {"forms": forms}


def _dvs_xlsx_to_text(dvs_bytes):
    """Extract DVS XLSX as structured text for Claude to read."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(dvs_bytes))
    lines = []
    for sheet_name in wb.sheetnames:
        ws   = wb[sheet_name]
        rows = list(ws.values)
        if not rows:
            continue
        lines.append(f"\n=== Sheet: {sheet_name} ===")
        headers = [str(h) if h else '' for h in rows[0]]
        lines.append('\t'.join(headers))
        for row in rows[1:]:
            if any(v is not None for v in row):
                lines.append('\t'.join(str(v) if v is not None else '' for v in row))
    return '\n'.join(lines)


# ── Pricing model — run scripts locally ───────────────────────────────────────

def _add_scripts(skill_name):
    path = os.path.join(SKILLS_DIR, skill_name, "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)


def run_pricing_model(pricing_summary_dict,
                      additional_sub_disc=0.0, additional_svc_disc=0.0):
    """Run pricing-quote scripts locally. Returns dict of file bytes."""
    _add_scripts("pricing-quote")
    from pricing_engine      import calculate_quote
    from generate_quote_pdf  import build_quote_pdfs
    from generate_quote_xlsx import build_quote_xlsx

    quote    = calculate_quote(pricing_summary_dict,
                               additional_sub_disc=additional_sub_disc,
                               additional_svc_disc=additional_svc_disc)
    protocol = quote["study_meta"].get("protocol_number", "STUDY")

    with tempfile.TemporaryDirectory() as tmp:
        paths = {
            "internal_pdf":  os.path.join(tmp, f"{protocol}_Quote_Internal.pdf"),
            "client_pdf":    os.path.join(tmp, f"{protocol}_Quote_Client.pdf"),
            "internal_xlsx": os.path.join(tmp, f"{protocol}_Quote_Internal.xlsx"),
            "client_xlsx":   os.path.join(tmp, f"{protocol}_Quote_Client.xlsx"),
        }
        build_quote_pdfs(quote, paths["internal_pdf"], paths["client_pdf"])
        build_quote_xlsx(quote, paths["internal_xlsx"], paths["client_xlsx"])
        return {k: open(v, "rb").read() for k, v in paths.items()}


# ── OpenClinica Study Service API ─────────────────────────────────────────────

async def _get_oc_token(subdomain):
    import httpx
    username = os.environ.get("OC_API_USERNAME", "").strip()
    password = os.environ.get("OC_API_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError("OC_API_USERNAME or OC_API_PASSWORD not set")
    url = f"https://{subdomain}.build.openclinica.io/user-service/api/oauth/token"
    print(f"Getting OC auth token from {url}", flush=True)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url,
                         headers={"Content-Type": "application/json"},
                         json={"username": username, "password": password})
    if r.status_code != 200:
        raise RuntimeError(f"OC auth failed {r.status_code}: {r.text[:200]}")
    return r.text.strip()


async def _check_study_exists(subdomain, token, protocol_num):
    import httpx
    url = f"https://{subdomain}.build.openclinica.io/study-service/api/studies"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url,
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                        params={"archived": "false", "size": 500})
    if r.status_code != 200:
        return None
    uid = protocol_num[:30].lower()
    for s in r.json():
        if s.get("uniqueIdentifier", "").lower() == uid:
            return s.get("uuid")
    return None


def _build_board_json(struct_json):
    """
    Build a board.json payload for the OpenClinica Study Designer
    from the Study Specification JSON.

    board.json structure:
      lists = Events (one per timepoint row)
      cards = Forms (one per form per event it is assigned to)

    Uses Meteor-style 17-char random IDs generated from the OIDs
    so the import is deterministic and repeatable.
    """
    import hashlib

    def _meteor_id(seed):
        """Generate a stable 17-char alphanumeric ID from a seed string."""
        chars = "23456789ABCDEFGHJKLMNPQRSTWXYZabcdefghijkmnopqrstuvwxyz"
        h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
        result = []
        for _ in range(17):
            result.append(chars[h % len(chars)])
            h //= len(chars)
        return ''.join(result)

    timepoint_rows = struct_json.get("timepoint_csv", {}).get("rows", [])
    forms          = struct_json.get("forms", [])

    # Build event list (lists)
    lists = []
    event_id_map = {}   # event_oid → meteor _id
    for i, row in enumerate(timepoint_rows):
        event_oid   = row.get("event", f"SE_EVENT{i+1}")
        label       = row.get("timepoint", event_oid)
        meteor_id   = _meteor_id(event_oid)
        event_id_map[event_oid] = meteor_id

        # Determine if repeating — common events don't have visit windows
        is_repeating = "UNSCH" in event_oid.upper() or "COMMON" in event_oid.upper()
        event_type   = "Common" if is_repeating else "Visit-Based"

        lists.append({
            "_id":         meteor_id,
            "title":       label,
            "sort":        i,
            "eventOcoid":  event_oid,
            "isRepeating": is_repeating,
            "type":        event_type,
        })

    # Build form cards
    cards = []
    card_sort = {}          # event_oid → current sort index
    original_card_id = {}  # form_id → first meteor card _id (for _parentId)

    for form in forms:
        form_id      = form.get("form_id", "")
        form_title   = form.get("form_title", form_id)
        visits       = form.get("visits_assigned", [])
        first_card   = True

        for event_oid in visits:
            if event_oid not in event_id_map:
                continue
            list_id  = event_id_map[event_oid]
            sort_idx = card_sort.get(event_oid, 0)
            card_sort[event_oid] = sort_idx + 1

            # Generate stable card ID from form+event combination
            card_id  = _meteor_id(f"{form_id}_{event_oid}")

            card = {
                "_id":      card_id,
                "title":    form_title,
                "listId":   list_id,
                "formOcoid": form_id,
                "sort":     sort_idx,
            }

            # First occurrence is the original; subsequent ones reference it
            if first_card:
                original_card_id[form_id] = card_id
                first_card = False
            else:
                card["_parentId"] = original_card_id[form_id]

            cards.append(card)

    return {"labels": [], "lists": lists, "cards": cards}


async def _get_board_id(subdomain, study_uuid, is_production):
    """
    Get the Study Designer board ID for a newly created study.
    The board ID is embedded in the currentBoardUrl returned by the study-service.
    URL format: https://{subdomain}.design.openclinica(-dev).io/b/{boardId}/...
    """
    import httpx
    token    = await _get_oc_token(subdomain)
    base_url = f"https://{subdomain}.build.openclinica.io"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{base_url}/study-service/api/studies/{study_uuid}",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch study details: {r.status_code} {r.text[:200]}")
    data          = r.json()
    board_url     = data.get("currentBoardUrl", "")
    # Extract board ID from URL: .../b/{boardId}/...
    if "/b/" in board_url:
        parts    = board_url.split("/b/")
        board_id = parts[1].split("/")[0]
        print(f"Board ID: {board_id}", flush=True)
        return board_id
    raise RuntimeError(f"Could not extract board ID from URL: {board_url}")


async def _import_board(subdomain, board_id, board_json, is_production):
    """
    Import the board.json into the study designer.
    POST {designer_url}/api/importStudy/{boardId}
    """
    import httpx
    token       = await _get_oc_token(subdomain)
    env_suffix  = "" if is_production else "-dev"
    designer_url = f"https://{subdomain}.design.openclinica{env_suffix}.io"
    endpoint    = f"{designer_url}/api/importStudy/{board_id}"

    print(f"Importing board to: {endpoint}", flush=True)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=board_json,
        )
    print(f"Board import: {r.status_code} {r.text[:200]}", flush=True)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Board import failed {r.status_code}: {r.text[:300]}")
    return True


async def create_oc_study(subdomain, struct_json, is_production=False):
    """
    Create a study in OpenClinica and import the Study Design Board (SOE).

    Steps:
    1. Create study shell via study-service API
       (skips if study already exists)
    2. Build board.json from struct_json (events + forms)
    3. Get the board ID from the newly created study
    4. Import board.json via study designer API
    """
    import httpx
    token    = await _get_oc_token(subdomain)
    headers  = {"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}
    base_url = f"https://{subdomain}.build.openclinica.io"
    meta     = struct_json.get("study_meta", {})
    protocol_num = meta.get("protocol_number", "STUDY")

    # ── Step 1: Create or find the study ──────────────────────────────────────
    existing_uuid = await _check_study_exists(subdomain, token, protocol_num)
    if existing_uuid:
        print(f"Study already exists (uuid: {existing_uuid}) — skipping creation.", flush=True)
        study_uuid = existing_uuid
    else:
        type_map  = {"interventional": "INTERVENTIONAL", "observational": "OBSERVATIONAL"}
        phase_map = {"phase i": "PHASEI", "phase 1": "PHASEI",
                     "phase ii": "PHASEII", "phase 2": "PHASEII",
                     "phase iii": "PHASEIII", "phase 3": "PHASEIII",
                     "phase iv": "PHASEIV", "phase 4": "PHASEIV"}
        today      = _dt.date.today().isoformat()
        dur_months = int(meta.get("total_study_duration_months", 24) or 24)
        end_date   = (_dt.date.today().replace(
                       year=_dt.date.today().year + dur_months // 12)).isoformat()

        payload = {
            "name":               meta.get("study_title", protocol_num),
            "description":        meta.get("description",
                                   f"{protocol_num} — {meta.get('indication', '')}"),
            "uniqueIdentifier":   protocol_num[:30],
            "type":               type_map.get(str(meta.get("type","")).lower(),
                                               "INTERVENTIONAL"),
            "phase":              phase_map.get(str(meta.get("study_phase","")).lower().strip(),
                                               "OTHER_NON_IND"),
            "expectedStartDate":  today,
            "expectedEndDate":    end_date,
            "expectedEnrollment": int(meta.get("expected_enrollment", 0) or 0),
            "collectSex":         True,
            "collectDateOfBirth": "ONLY_THE_YEAR",
            "collectPersonId":    "ALWAYS",
        }

        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{base_url}/study-service/api/studies",
                             headers=headers, json=payload)
        print(f"OC Study API: {r.status_code} {r.text[:300]}", flush=True)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"OC Study API returned {r.status_code}: {r.text[:300]}")
        study_uuid = r.json().get("uuid", "")
        if not study_uuid:
            raise RuntimeError("Study created but no UUID returned")

    env_suffix   = "" if is_production else "-dev"
    designer_url = f"https://{subdomain}.design.openclinica{env_suffix}.io"
    study_url    = f"{designer_url}/b/{study_uuid}"

    # ── Step 2: Build board.json from struct_json ──────────────────────────────
    print("Building board.json from Study Specification...", flush=True)
    board_json = _build_board_json(struct_json)
    print(f"Board: {len(board_json['lists'])} events, "
          f"{len(board_json['cards'])} form cards", flush=True)

    # ── Step 3: Get the board ID ───────────────────────────────────────────────
    try:
        board_id = await _get_board_id(subdomain, study_uuid, is_production)
    except Exception as e:
        print(f"Could not get board ID: {e}", flush=True)
        raise

    # ── Step 4: Import board.json ──────────────────────────────────────────────
    await _import_board(subdomain, board_id, board_json, is_production)
    print(f"Study design board imported successfully.", flush=True)

    return study_url


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def run_pipeline(item_id):
    try:
        # ── 0. Fetch item from monday.com ─────────────────────────────────────
        item         = await get_item(item_id)
        cols         = {c["id"]: c for c in item["column_values"]}
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "STUDY")
        crf_url      = cols.get(COL["crf_library"],     {}).get("text")
        oc_std_url   = cols.get(COL["oc_standard"],     {}).get("text")
        oc_subdomain = cols.get(COL["oc_subdomain"],    {}).get("text", "").strip()

        _now    = _dt.datetime.utcnow()
        version = f"V{_now.strftime('%m%d')}.{_now.strftime('%H%M')}"
        print(f"Protocol: {protocol_num} | Version: {version}", flush=True)

        def _pct(col_key):
            raw = cols.get(COL[col_key], {}).get("text", "").strip()
            try:
                return float(raw) / 100.0 if raw else 0.0
            except ValueError:
                return 0.0

        additional_sub_disc = _pct("subscription_discount")
        additional_svc_disc = _pct("services_discount")

        output_raw        = cols.get(COL["output_requested"], {}).get("text", "") or ""
        output_selections = {s.strip().lower() for s in output_raw.split(",") if s.strip()}
        run_all = len(output_selections) == 0
        def _want(label):
            return run_all or label.lower() in output_selections
        print(f"Output requested: {output_raw!r} | run_all={run_all}", flush=True)

        create_study_val = cols.get(COL["create_study"], {}).get("value")
        try:
            parsed = json.loads(create_study_val or "{}")
            create_study = bool(parsed.get("checked", False)) if isinstance(parsed, dict) else bool(parsed)
        except Exception:
            create_study = False

        oc_production_val = cols.get(COL["oc_production"], {}).get("value")
        try:
            parsed = json.loads(oc_production_val or "{}")
            oc_production = bool(parsed.get("checked", False)) if isinstance(parsed, dict) else bool(parsed)
        except Exception:
            oc_production = False

        print(f"Create OC Study: {create_study} | Subdomain: {oc_subdomain} | Production: {oc_production}", flush=True)

        # ── 1. Check for human-uploaded inputs ────────────────────────────────
        edited_spec_xlsx  = await download_column_file(item_id, COL["edited_spec_input"])
        edited_build_zip  = await download_column_file(item_id, COL["build_input"])
        edited_dvs_xlsx   = await download_column_file(item_id, COL["dvs_input"])
        edited_quote_xlsx = await download_column_file(item_id, COL["quote_input"])
        edited_soe_csv    = await download_column_file(item_id, COL["soe_input"])

        print(f"Human inputs — spec:{edited_spec_xlsx is not None} "
              f"build:{edited_build_zip is not None} dvs:{edited_dvs_xlsx is not None} "
              f"quote:{edited_quote_xlsx is not None} soe:{edited_soe_csv is not None}",
              flush=True)

        # ── Path D: Edited Quote XLSX → regenerate Quote PDFs ─────────────────
        if edited_quote_xlsx:
            await append_log(item_id, "Edited Quote XLSX detected — regenerating PDFs.")
            print("Path D: regenerating quote PDFs from edited XLSX...", flush=True)
            try:
                quote_files = await run_skill(
                    QUOTE_PDF_FROM_XLSX_PROMPT,
                    skill_ids=[SKILL_IDS["pricing_quote"]],
                    xlsx_bytes=edited_quote_xlsx,
                )
                q_int_pdf = _find(quote_files, "_Quote_Internal.pdf")
                q_cli_pdf = _find(quote_files, "_Quote_Client.pdf")
                if q_int_pdf:
                    await upload_file(item_id, COL["pricing_quote"],
                                      f"{protocol_num}_Quote_Internal_{version}.pdf", q_int_pdf)
                if q_cli_pdf:
                    await upload_file(item_id, COL["pricing_quote"],
                                      f"{protocol_num}_Quote_Client_{version}.pdf", q_cli_pdf)
                await append_log(item_id, "Quote PDFs regenerated from edited XLSX.")
            except Exception as e:
                print(f"Quote PDF regeneration error: {e}", flush=True)
                await append_log(item_id, f"Quote PDF regeneration error: {e}")
            await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
            await append_log(item_id, "Pipeline complete.")
            return

        # ── Path E: Edited SOE CSV → update OpenClinica ───────────────────────
        if edited_soe_csv and oc_subdomain:
            await append_log(item_id, "Edited SOE CSV detected — updating OpenClinica.")
            print("Path E: updating SOE in OpenClinica...", flush=True)
            # TODO: implement SOE update API call when OC API supports it
            await append_log(item_id, "SOE update in OpenClinica — not yet implemented.")
            await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
            return

        # ── Download protocol PDF ──────────────────────────────────────────────
        from monday_client import get_asset_url
        protocol_pdf = b""
        assets = await get_asset_url(item_id)
        for asset in assets:
            if (asset.get("name") or "").lower().endswith(".pdf"):
                url = asset.get("public_url") or asset.get("url")
                if url:
                    protocol_pdf = await download_file(url)
                if protocol_pdf:
                    break
        print(f"Protocol PDF: {len(protocol_pdf)} bytes", flush=True)

        crf_pdf  = await download_file(crf_url)    if crf_url    else None
        oc_zip   = await download_file(oc_std_url) if oc_std_url else None

        # ── Steps 1-2: Study Specification ────────────────────────────────────
        struct_json = None

        if edited_spec_xlsx:
            # Path A: User uploaded edited Study Spec XLSX
            await append_log(item_id, "Edited Study Specification XLSX detected.")
            print("Path A: reading edited Study Spec XLSX...", flush=True)
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(edited_spec_xlsx))
            # Try to find JSON sheet
            for sheet_name in wb.sheetnames:
                if 'json' in sheet_name.lower() or 'spec' in sheet_name.lower():
                    ws = wb[sheet_name]
                    raw = '\n'.join(str(cell.value or '') for row in ws.iter_rows() for cell in row)
                    try:
                        struct_json = extract_json(raw)
                        print("Extracted JSON from edited Study Spec XLSX.", flush=True)
                        break
                    except ValueError:
                        pass
            if struct_json is None:
                await append_log(item_id, "Could not extract JSON from edited XLSX — running fresh analysis.")

        if struct_json is None and _want("protocol specification"):
            await set_status(item_id, COL["pipeline_status"], STATUS["analysis_running"])
            await append_log(item_id, "Protocol Analysis started.")

            extra_parts = []
            if crf_pdf:
                extra_parts.append("Customer CRF Library (PDF) attached — use as Priority 1.")
            if oc_zip:
                extra_parts.append("Customer OC4 XLSForm Standards (ZIP) attached — use as Priority 2.")

            print("Step 1: Claude extracting Study Spec JSON...", flush=True)
            struct_text = await call_claude(
                EDC_STRUCTURE_PROMPT,
                pdf_bytes  = protocol_pdf or None,
                extra_text = "\n".join(extra_parts) if extra_parts else None,
            )
            try:
                struct_json = extract_json(struct_text)
                if isinstance(struct_json, list):
                    struct_json = {"study_meta": {"protocol_number": protocol_num},
                                   "forms": struct_json, "review_flags": {}}
            except ValueError:
                struct_json = {"study_meta": {"protocol_number": protocol_num},
                               "forms": [], "review_flags": {}}
                print("Warning: Study Spec JSON not valid", flush=True)

            # Upload raw JSON
            await upload_file(item_id, COL["spec_json"],
                              f"{protocol_num}_Study_Specification_{version}.json",
                              json.dumps(struct_json, indent=2).encode())

            await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
            await append_log(item_id, "Study Spec files, Protocol Summary, and EDC Build starting in parallel.")

            # Shared state for parallel chains
            pricing_json = {"study_meta": {"protocol_number": protocol_num}}

            # ── Parallel chains after Step 1 ──────────────────────────────────
            # Chain A: Study Spec PDF + XLSX (Step 2)
            # Chain B: Protocol Summary JSON → PDF + Quote (Steps 3-5)
            # Chain C: EDC Build → DVS (Steps 6-7)
            # All three chains only need struct_json and run independently.

            # ── Chain A: Study Spec files ──────────────────────────────────────
            async def chain_a():
                print("Chain A: Generating Study Spec PDF + XLSX...", flush=True)
                try:
                    spec_files = await run_skill(
                        GENERATE_STUDY_SPEC_PROMPT,
                        skill_ids=[SKILL_IDS["protocol_analysis"]],
                        extra_text="Study Specification JSON:\n" + json.dumps(struct_json),
                    )
                    spec_pdf  = _find(spec_files, "_Study_Specification.pdf")
                    spec_xlsx = _find(spec_files, "_Study_Specification.xlsx")
                    uploads = []
                    if spec_pdf:
                        uploads.append(upload_file(item_id, COL["spec_pdf"],
                            f"{protocol_num}_Study_Specification_{version}.pdf", spec_pdf))
                    if spec_xlsx:
                        uploads.append(upload_file(item_id, COL["spec_xlsx"],
                            f"{protocol_num}_Study_Specification_{version}.xlsx", spec_xlsx))
                    if uploads:
                        await asyncio.gather(*uploads)
                    print("Chain A complete.", flush=True)
                except Exception as e:
                    print(f"Chain A error: {e}", flush=True)
                    await append_log(item_id, f"Study Spec file generation error: {e}")

            # ── Chain B: Protocol Summary JSON → PDF + Quote ───────────────────
            async def chain_b():
                nonlocal pricing_json
                if not _want("protocol summary") and not _want("price quote"):
                    return

                if _want("protocol summary"):
                    print("Chain B: Claude extracting Protocol Summary JSON...", flush=True)
                    struct_slim = {
                        "study_meta":   struct_json.get("study_meta", {}),
                        "review_flags": struct_json.get("review_flags", {}),
                        "forms":        [{"name": f.get("name"), "domain": f.get("domain"),
                                          "complexity": f.get("complexity"),
                                          "visits_assigned": f.get("visits_assigned", [])}
                                         for f in struct_json.get("forms", [])],
                    }
                    pricing_text = await call_claude(
                        PRICING_SUMMARY_PROMPT,
                        extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
                    )
                    try:
                        pricing_json = extract_json(pricing_text)
                        if isinstance(pricing_json, list):
                            pricing_json = {"study_meta": {"protocol_number": protocol_num}}
                    except ValueError:
                        print("Warning: Protocol Summary JSON not valid", flush=True)

                    # Steps 4 + 5 in parallel: Protocol Summary PDF + Pricing Quote
                    async def gen_ps_pdf():
                        print("Chain B: Generating Protocol Summary PDF...", flush=True)
                        try:
                            ps_files = await run_skill(
                                GENERATE_PROTOCOL_SUMMARY_PROMPT,
                                skill_ids=[SKILL_IDS["protocol_analysis"]],
                                extra_text="Protocol Summary JSON:\n" + json.dumps(pricing_json),
                            )
                            ps_pdf = _find(ps_files, "_Protocol_Summary.pdf")
                            uploads = [upload_file(item_id, COL["pricing_summary"],
                                f"{protocol_num}_Protocol_Summary_{version}.json",
                                json.dumps(pricing_json, indent=2).encode())]
                            if ps_pdf:
                                uploads.append(upload_file(item_id, COL["pricing_summary"],
                                    f"{protocol_num}_Protocol_Summary_{version}.pdf", ps_pdf))
                            await asyncio.gather(*uploads)
                        except Exception as e:
                            print(f"Protocol Summary PDF error: {e}", flush=True)
                            await append_log(item_id, f"Protocol Summary PDF error: {e}")

                    async def gen_quote():
                        if not _want("price quote"):
                            return
                        print("Chain B: Generating Price Quote (local scripts)...", flush=True)
                        try:
                            loop = asyncio.get_event_loop()
                            qf = await loop.run_in_executor(
                                None,
                                lambda: run_pricing_model(
                                    pricing_json,
                                    additional_sub_disc=additional_sub_disc,
                                    additional_svc_disc=additional_svc_disc,
                                )
                            )
                            await asyncio.gather(
                                upload_file(item_id, COL["pricing_quote"],
                                    f"{protocol_num}_Quote_Internal_{version}.pdf",  qf["internal_pdf"]),
                                upload_file(item_id, COL["pricing_quote"],
                                    f"{protocol_num}_Quote_Client_{version}.pdf",    qf["client_pdf"]),
                                upload_file(item_id, COL["pricing_quote"],
                                    f"{protocol_num}_Quote_Internal_{version}.xlsx", qf["internal_xlsx"]),
                                upload_file(item_id, COL["pricing_quote"],
                                    f"{protocol_num}_Quote_Client_{version}.xlsx",   qf["client_xlsx"]),
                            )
                            await append_log(item_id, "Price Quote complete — 4 files uploaded.")
                        except Exception as e:
                            print(f"Price Quote error: {e}", flush=True)
                            await append_log(item_id, f"Price Quote error: {e}")

                    await asyncio.gather(gen_ps_pdf(), gen_quote())
                    await append_log(item_id, "Protocol Summary + Price Quote complete.")
                    print("Chain B complete.", flush=True)

            # ── Chain C: EDC Build → DVS ──────────────────────────────────────
            build_zip_holder  = [None]   # mutable container for async closure
            build_json_holder = [{"forms": {}}]

            async def chain_c():
                if not _want("study build zip"):
                    return
                print("Chain C: EDC Build starting...", flush=True)
                await _run_edc_and_dvs()
                print("Chain C complete.", flush=True)

            async def _run_edc_and_dvs():
                nonlocal build_zip_holder, build_json_holder

                if edited_build_zip:
                    # Path B: User uploaded edited XLSForm ZIP
                    print("Path B: using user-uploaded XLSForm ZIP...", flush=True)
                    await append_log(item_id, "Using user-uploaded XLSForm ZIP.")
                    try:
                        build_json_holder[0] = _read_zip_xlsforms(edited_build_zip)
                        build_zip_holder[0]  = edited_build_zip
                    except Exception as e:
                        print(f"Error reading build ZIP: {e}", flush=True)
                        await append_log(item_id, f"Error reading build ZIP: {e}")

                elif edited_dvs_xlsx:
                    # Path C: User uploaded edited DVS → translate to XLSForms
                    print("Path C: translating DVS changes to XLSForms...", flush=True)
                    await append_log(item_id, "Translating DVS input to XLSForm updates.")
                    dvs_text = _dvs_xlsx_to_text(edited_dvs_xlsx)

                    struct_slim = {
                        "study_meta": struct_json.get("study_meta", {}) if struct_json else {},
                        "forms":      struct_json.get("forms", []) if struct_json else [],
                    }
                    base_build_text = await call_claude(
                        EDC_BUILD_PROMPT,
                        extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
                    )
                    try:
                        base_build = extract_json(base_build_text)
                        if isinstance(base_build, list):
                            base_build = {"forms": {}}
                    except ValueError:
                        base_build = {"forms": {}}

                    updated_text = await call_claude(
                        DVS_TRANSLATE_PROMPT,
                        extra_text=("Current XLSForm JSON:\n" + json.dumps(base_build) +
                                    "\n\nDVS Changes:\n" + dvs_text),
                    )
                    try:
                        build_json_holder[0] = extract_json(updated_text)
                    except ValueError:
                        build_json_holder[0] = base_build
                    build_zip_holder[0] = _xlsform_zip(build_json_holder[0])

                else:
                    # Fresh run: edc-builder skill
                    print("Chain C: Running edc-builder skill...", flush=True)
                    struct_slim = {
                        "study_meta": struct_json.get("study_meta", {}) if struct_json else {},
                        "forms":      struct_json.get("forms", []) if struct_json else [],
                    }
                    try:
                        build_files = await run_skill(
                            EDC_BUILD_PROMPT,
                            skill_ids=[SKILL_IDS["edc_builder"]],
                            extra_text="Study Specification JSON:\n" + json.dumps(struct_slim),
                        )
                        build_zip_holder[0] = _find(build_files, "_EDC_Build.zip", ".zip")
                        if build_zip_holder[0]:
                            build_json_holder[0] = _read_zip_xlsforms(build_zip_holder[0])
                    except Exception as e:
                        print(f"EDC Build error: {e}", flush=True)
                        await append_log(item_id, f"EDC Build error: {e}")

                if build_zip_holder[0]:
                    await upload_file(item_id, COL["edc_build"],
                                      f"{protocol_num}_EDC_Build_{version}.zip",
                                      build_zip_holder[0])
                    await append_log(item_id, "EDC Build complete — ZIP uploaded.")

                # DVS
                print("Chain C: Running DVS...", flush=True)
                dvs_slim = {}
            for fname, fdata in build_json_holder[0].get("forms", {}).items():
                survey = fdata.get("survey", [])
                relevant_rows = [
                    {k: v for k, v in row.items()
                     if k in ('type','name','label','constraint',
                               'constraint_message','calculation',
                               'relevant','required')}
                    for row in survey
                    if any(row.get(k) for k in ('constraint','calculation','relevant'))
                ]
                dvs_slim[fname] = {"survey": relevant_rows}

            try:
                dvs_files = await run_skill(
                    DVS_PROMPT,
                    skill_ids=[SKILL_IDS["dvs_specification"]],
                    extra_text=("Study Specification JSON:\n" +
                                json.dumps(struct_json.get("study_meta", {})
                                           if struct_json else {}) +
                                "\n\nEDC Build survey data:\n" +
                                json.dumps({"forms": dvs_slim})),
                )
                dvs_xlsx = _find(dvs_files, "_DVS.xlsx", ".xlsx")
                if dvs_xlsx:
                    await upload_file(item_id, COL["dvs_output"],
                                      f"{protocol_num}_DVS_{version}.xlsx", dvs_xlsx)
                await append_log(item_id, "DVS complete.")
            except Exception as e:
                print(f"DVS error: {e}", flush=True)
                await append_log(item_id, f"DVS error: {e}")

            # ── Launch all three chains in parallel ────────────────────────────
            await asyncio.gather(chain_a(), chain_b(), chain_c())

            await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
            await append_log(item_id, "Study Specification complete.")

        # ── Create OC Study ───────────────────────────────────────────────────
        if create_study and oc_subdomain and struct_json:
            await set_status(item_id, COL["pipeline_status"], STATUS["creating_oc_study"])
            env_label = "production" if oc_production else "test"
            await append_log(item_id, f"Creating study in OpenClinica {env_label} ({oc_subdomain})...")
            try:
                study_url = await create_oc_study(oc_subdomain, struct_json, is_production=oc_production)
                await set_text(item_id, COL["oc_study_url"], study_url)
                await append_log(item_id, f"Study + design board created: {study_url}")
            except Exception as e:
                print(f"OC Study error: {e}", flush=True)
                await append_log(item_id, f"OC Study creation failed: {e}")
        elif create_study and not oc_subdomain:
            await append_log(item_id, "Create Study requested but no OC Subdomain — skipped.")

        # ── Done ──────────────────────────────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
        await append_log(item_id, "Pipeline complete. All outputs uploaded.")

    except Exception as e:
        import traceback
        print(f"PIPELINE CRASHED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise
