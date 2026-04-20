import asyncio, base64, json
from monday_client import get_item, download_file, upload_file, set_status, append_log, COL
from claude_client import run_skill
from prompts import EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT, PRICING_MODEL_PROMPT, EDC_BUILD_PROMPT, DVS_PROMPT

# Label IDs matching Monday board configuration
STATUS = {
    "not_started":            5,
    "edc_structure_running":  0,
    "edc_structure_complete": 1,
    "build_pricing_running":  3,
    "build_complete":         4,
    "pricing_complete":       6,
    "dvs_running":            7,
    "dvs_complete":           8,
    "all_complete":           9,
    "failed":                 2,
}

def extract_b64(response, tag):
    s, e = f"==={tag}_START===", f"==={tag}_END==="
    if s not in response or e not in response: return None
    try:
        raw = response[response.index(s)+len(s):response.index(e)].strip()
        # Fix base64 padding
        raw += "=" * (4 - len(raw) % 4) if len(raw) % 4 else ""
        return base64.b64decode(raw)
    except Exception as ex:
        print(f"extract_b64 error for {tag}: {ex}", flush=True)
        return None

def get_file_url(column_value):
    if not column_value: return None
    val = str(column_value).strip()
    if val.startswith("http://") or val.startswith("https://"):
        print(f"FILE URL (direct): {val[:80]}", flush=True)
        return val
    try:
        data = json.loads(val)
        files = data.get("files", [])
        if files:
            url = files[0].get("url") or files[0].get("publicUrl")
            print(f"FILE URL (json): {url[:80] if url else None}", flush=True)
            return url
    except Exception as ex:
        print(f"FILE URL error: {ex}", flush=True)
    return None

async def run_pipeline(item_id):
    try:
        item = await get_item(item_id)
        cols = {c["id"]: c for c in item["column_values"]}
        protocol_url = get_file_url(cols.get(COL["protocol_pdf"],    {}).get("text"))
        crf_url      = get_file_url(cols.get(COL["crf_library"],     {}).get("text"))
        oc_std_url   = get_file_url(cols.get(COL["oc_standard"],     {}).get("text"))
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "study")
        print(f"PROTOCOL URL: {protocol_url}", flush=True)
        print(f"PROTOCOL NUM: {protocol_num}", flush=True)
        if not protocol_url:
            await append_log(item_id, "ERROR: No protocol PDF found. Please upload a protocol PDF and try again.")
            await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
            return
        # Fetch fresh asset URLs right before downloading
        from monday_client import get_asset_url
        assets = await get_asset_url(item_id)
        print(f"ASSETS FOUND: {len(assets)}", flush=True)

        # Get fresh public URL and download immediately
        protocol_pdf = b""
        for asset in assets:
            name = (asset.get("name") or "").lower()
            if name.endswith(".pdf"):
                # Fetch a completely fresh URL by re-querying assets
                fresh_assets = await get_asset_url(item_id)
                for fa in fresh_assets:
                    if fa.get("id") == asset.get("id"):
                        pub_url = fa.get("public_url") or fa.get("url")
                        if pub_url:
                            print(f"DOWNLOADING FRESH URL: {pub_url[:80]}", flush=True)
                            protocol_pdf = await download_file(pub_url)
                            print(f"PROTOCOL PDF: {len(protocol_pdf)} bytes", flush=True)
                        break
                if len(protocol_pdf) > 0:
                    break

        if len(protocol_pdf) == 0:
            print("WARNING: Protocol PDF downloaded as 0 bytes - proceeding without PDF", flush=True)

        crf_pdf     = await download_file(crf_url)    if crf_url    else None
        oc_std_xlsx = await download_file(oc_std_url) if oc_std_url else None
        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_running"])
        await append_log(item_id, "EDC Structure skill started.")
        print("Calling Claude for EDC Structure...", flush=True)
        struct_response = await run_skill(EDC_STRUCTURE_PROMPT, pdf_bytes=protocol_pdf,
            xlsx_bytes=oc_std_xlsx, extra_text="Customer CRF library attached." if crf_pdf else "")
        print(f"Claude response length: {len(struct_response)}", flush=True)
        print(f"Claude response preview: {struct_response[:300]}", flush=True)
        spec_pdf  = extract_b64(struct_response, "PDF")
        spec_xlsx = extract_b64(struct_response, "XLSX")
        spec_json = extract_b64(struct_response, "JSON")
        print(f"Extracted PDF:{spec_pdf is not None} XLSX:{spec_xlsx is not None} JSON:{spec_json is not None}", flush=True)
        if spec_xlsx: await upload_file(item_id, COL["spec_xlsx"], f"{protocol_num}_EDC_Structure.xlsx", spec_xlsx)
        if spec_pdf:  await upload_file(item_id, COL["spec_pdf"],  f"{protocol_num}_EDC_Structure.pdf",  spec_pdf)
        if spec_json: await upload_file(item_id, COL["spec_json"], f"{protocol_num}_EDC_Structure.json", spec_json)
        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_complete"])
        await append_log(item_id, "EDC Structure complete. Starting parallel build + pricing.")
        await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
        build_zip, _ = await asyncio.gather(
            asyncio.create_task(run_edc_build(item_id, protocol_num, spec_xlsx)),
            asyncio.create_task(run_pricing(item_id, protocol_num, protocol_pdf, spec_xlsx))
        )
        if build_zip and spec_xlsx:
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_running"])
            await append_log(item_id, "DVS skill started.")
            dvs_response = await run_skill(DVS_PROMPT, xlsx_bytes=spec_xlsx,
                extra_text="[EDC Build zip attached as base64]\n"+base64.standard_b64encode(build_zip).decode())
            dvs_xlsx = extract_b64(dvs_response, "XLSX")
            if dvs_xlsx:
                await upload_file(item_id, COL["dvs_output"], f"{protocol_num}_DVS.xlsx", dvs_xlsx)
            await append_log(item_id, "DVS complete.")
        await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
        await append_log(item_id, "Pipeline complete. All outputs uploaded.")
    except Exception as e:
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise

async def run_edc_build(item_id, protocol_num, spec_xlsx):
    response  = await run_skill(EDC_BUILD_PROMPT, xlsx_bytes=spec_xlsx)
    build_zip = extract_b64(response, "ZIP")
    if build_zip:
        await upload_file(item_id, COL["edc_build"], f"{protocol_num}_EDC_Build.zip", build_zip)
    await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
    await append_log(item_id, "EDC Build complete.")
    return build_zip

async def run_pricing(item_id, protocol_num, protocol_pdf, spec_xlsx):
    r1 = await run_skill(PRICING_SUMMARY_PROMPT, pdf_bytes=protocol_pdf, xlsx_bytes=spec_xlsx)
    summary_pdf = extract_b64(r1, "PDF")
    if summary_pdf:
        await upload_file(item_id, COL["pricing_summary"], f"{protocol_num}_Pricing_Summary.pdf", summary_pdf)
    r2 = await run_skill(PRICING_MODEL_PROMPT, pdf_bytes=summary_pdf)
    quote_pdf  = extract_b64(r2, "PDF")
    quote_xlsx = extract_b64(r2, "XLSX")
    if quote_pdf:  await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.pdf",  quote_pdf)
    if quote_xlsx: await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.xlsx", quote_xlsx)
    await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
    await append_log(item_id, "Pricing complete.")
