import asyncio, base64
from monday_client import get_item, download_file, upload_file, set_status, append_log, COL
from claude_client import run_skill
from prompts import EDC_STRUCTURE_PROMPT, PRICING_SUMMARY_PROMPT, PRICING_MODEL_PROMPT, EDC_BUILD_PROMPT, DVS_PROMPT

STATUS = {"not_started":0,"edc_structure_running":1,"edc_structure_complete":2,
    "build_pricing_running":3,"build_complete":4,"pricing_complete":5,
    "dvs_running":6,"dvs_complete":7,"all_complete":8,"failed":9}

def extract_b64(response, tag):
    s, e = f"==={tag}_START===", f"==={tag}_END==="
    if s not in response: return None
    return base64.b64decode(response[response.index(s)+len(s):response.index(e)].strip())

async def run_pipeline(item_id):
    try:
        item = await get_item(item_id)
        cols = {c["id"]:c for c in item["column_values"]}
        protocol_url = cols.get(COL["protocol_pdf"],{}).get("value")
        crf_url      = cols.get(COL["crf_library"],{}).get("value")
        oc_std_url   = cols.get(COL["oc_standard"],{}).get("value")
        protocol_num = cols.get(COL["protocol_number"],{}).get("text","study")

        if not protocol_url:
            await append_log(item_id, "ERROR: No protocol PDF found. Aborting.")
            await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
            return

        protocol_pdf = await download_file(protocol_url)
        crf_pdf      = await download_file(crf_url)    if crf_url    else None
        oc_std_xlsx  = await download_file(oc_std_url) if oc_std_url else None

        await set_status(item_id, COL["pipeline_status"], STATUS["edc_structure_running"])
        await append_log(item_id, "EDC Structure skill started.")

        struct_response = await run_skill(EDC_STRUCTURE_PROMPT, pdf_bytes=protocol_pdf,
            xlsx_bytes=oc_std_xlsx, extra_text="Customer CRF library attached." if crf_pdf else "")

        spec_pdf  = extract_b64(struct_response, "PDF")
        spec_xlsx = extract_b64(struct_response, "XLSX")
        spec_json = extract_b64(struct_response, "JSON")

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
            if dvs_xlsx: await upload_file(item_id, COL["dvs_output"], f"{protocol_num}_DVS.xlsx", dvs_xlsx)
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
    if build_zip: await upload_file(item_id, COL["edc_build"], f"{protocol_num}_EDC_Build.zip", build_zip)
    await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
    await append_log(item_id, "EDC Build complete.")
    return build_zip

async def run_pricing(item_id, protocol_num, protocol_pdf, spec_xlsx):
    r1 = await run_skill(PRICING_SUMMARY_PROMPT, pdf_bytes=protocol_pdf, xlsx_bytes=spec_xlsx)
    summary_pdf = extract_b64(r1, "PDF")
    if summary_pdf: await upload_file(item_id, COL["pricing_summary"], f"{protocol_num}_Pricing_Summary.pdf", summary_pdf)
    r2 = await run_skill(PRICING_MODEL_PROMPT, pdf_bytes=summary_pdf)
    quote_pdf  = extract_b64(r2, "PDF")
    quote_xlsx = extract_b64(r2, "XLSX")
    if quote_pdf:  await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.pdf",  quote_pdf)
    if quote_xlsx: await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote.xlsx", quote_xlsx)
    await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
    await append_log(item_id, "Pricing complete.")
