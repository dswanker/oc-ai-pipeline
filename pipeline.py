import asyncio, base64, json
from monday_client import get_item, download_file, upload_file, set_status, append_log, COL
from claude_client import run_skill
from prompts import EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT, PRICING_MODEL_PROMPT, EDC_BUILD_PROMPT, DVS_PROMPT

# Label IDs from Monday board (use id not index)
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
        pad = (4 - len(raw) % 4) % 4
        return base64.b64decode(raw + "=" * pad)
    except Exception as ex:
        print(f"extract_b64 error for {tag}: {ex}", flush=True)
        return None

async def run_pipeline(item_id):
    try:
        item = await get_item(item_id)
        cols = {c["id"]: c for c in item["column_values"]}
        protocol_url = cols.get(COL["protocol_pdf"],    {}).get("text")
        crf_url      = cols.get(COL["crf_library"],     {}).get("text")
        oc_std_url   = cols.get(COL["oc_standard"],     {}).get("text")
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "study")

        print(f"PROTOCOL URL: {protocol_url}", flush=True)
        print(f"PROTOCOL NUM: {protocol_num}", flush=True)

        if not protocol_url:
            await append_log(item_id, "ERROR: No protocol PDF found.")
            await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
            return

        from monday_client import get_asset_url
        assets = await get_asset_url(item_id)
        protocol_pdf = b""
        for asset in assets:
            if (asset.get("name") or "").lower().endswith(".pdf"):
                fresh = await get_asset_url(item_id)
                for fa in fresh:
                    if fa.get("id") == asset.get("id"):
                        pub_url = fa.get("public_url") or fa.get("url")
                        if pub_url:
                            protocol_pdf = await download_file(pub_url)
                        break
                if len(protocol_pdf) > 0:
                    break

        print(f"PROTOCOL PDF: {len(protocol_pdf)} bytes", flush=True)
        crf_pdf     = await download_file(crf_url)    if crf_url    else None
        oc_std_xlsx = await download_file(oc_std_url) if oc_std_url else None

        # STEP 1: EDC Structure
        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_running"])
        await append_log(item_id, "EDC Structure skill started.")
        print("Calling Claude for EDC Structure...", flush=True)

        struct_response = await run_skill(
            EDC_STRUCTURE_PROMPT,
            pdf_bytes  = protocol_pdf if len(protocol_pdf) > 0 else None,
            xlsx_bytes = oc_std_xlsx,
            extra_text = "Customer CRF library attached." if crf_pdf else ""
        )
        print(f"EDC Structure response length: {len(struct_response)}", flush=True)

        spec_pdf  = extract_b64(struct_response, "PDF")
        spec_xlsx = extract_b64(struct_response, "XLSX")
        spec_json = extract_b64(struct_response, "JSON")
        print(f"Extracted PDF:{spec_pdf is not None} XLSX:{spec_xlsx is not None} JSON:{spec_json is not None}", flush=True)

        if spec_pdf:  await upload_file(item_id, COL["spec_pdf"],  f"{protocol_num}_EDC_Structure.pdf",  spec_pdf)
        if spec_xlsx: await upload_file(item_id, COL["spec_xlsx"], f"{protocol_num}_EDC_Structure.xlsx", spec_xlsx)
        if spec_json: await upload_file(item_id, COL["spec_json"], f"{protocol_num}_EDC_Structure.json", spec_json)

        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_complete"])
        await append_log(item_id, "EDC Structure complete.")

        # STEP 2: EDC Build (sequential - wait 15s for rate limit)
        await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
        await append_log(item_id, "EDC Build started.")
        await asyncio.sleep(15)

        build_response = await run_skill(EDC_BUILD_PROMPT, xlsx_bytes=spec_xlsx)
        build_zip = extract_b64(build_response, "ZIP")
        if build_zip:
            await upload_file(item_id, COL["edc_build"], f"{protocol_num}_EDC_Build.zip", build_zip)
        await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
        await append_log(item_id, "EDC Build complete.")

        # STEP 3: Pricing (sequential - wait 15s for rate limit)
        await append_log(item_id, "Pricing started.")
        await asyncio.sleep(15)

        r1 = await run_skill(PRICING_SUMMARY_PROMPT, pdf_bytes=protocol_pdf if len(protocol_pdf) > 0 else None, xlsx_bytes=spec_xlsx)
        summary_pdf = extract_b64(r1, "PDF")
        if summary_pdf:
            await upload_file(item_id, COL["pricing_summary"], f"{protocol_num}_Pricing_Summary.pdf", summary_pdf)
        await asyncio.sleep(15)

        r2 = await run_skill(PRICING_MODEL_PROMPT, pdf_bytes=summary_pdf)
        quote_pdf  = extract_b64(r2, "PDF")
        quote_xlsx = extract_b64(r2, "XLSX")
        if quote_pdf:  await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.pdf",  quote_pdf)
        if quote_xlsx: await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.xlsx", quote_xlsx)
        await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
        await append_log(item_id, "Pricing complete.")

        # STEP 4: DVS
        if build_zip and spec_xlsx:
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_running"])
            await append_log(item_id, "DVS started.")
            await asyncio.sleep(15)

            dvs_response = await run_skill(
                DVS_PROMPT,
                xlsx_bytes = spec_xlsx,
                extra_text = "[EDC Build zip attached as base64]\n" + base64.standard_b64encode(build_zip).decode()
            )
            dvs_xlsx = extract_b64(dvs_response, "XLSX")
            if dvs_xlsx:
                await upload_file(item_id, COL["dvs_output"], f"{protocol_num}_DVS.xlsx", dvs_xlsx)
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_complete"])
            await append_log(item_id, "DVS complete.")

        await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
        await append_log(item_id, "Pipeline complete. All outputs uploaded.")

    except Exception as e:
        import traceback
        print(f"PIPELINE CRASHED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise
