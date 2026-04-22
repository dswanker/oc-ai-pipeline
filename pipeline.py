"""
pipeline.py — oc-ai-pipeline main orchestration.

Flow:
  1. protocol-analysis  (protocol PDF → study spec PDF/XLSX/JSON + protocol summary PDF/JSON)
  2. edc-builder        (study spec XLSX → EDC build ZIP)
  3. pricing-quote      (protocol summary JSON → quote PDF/XLSX x2)
  4. dvs-specification  (study spec XLSX + build ZIP → DVS XLSX)

Skill IDs are read from Railway environment variables set by register_skills.py.
"""

import asyncio, os
from monday_client import get_item, download_file, upload_file, set_status, append_log, COL
from claude_client import run_skill
from prompts import (
    PROTOCOL_ANALYSIS_PROMPT,
    PRICING_QUOTE_PROMPT,
    EDC_BUILD_PROMPT,
    DVS_PROMPT,
)

# ── Skill IDs (set via Railway env vars after running register_skills.py) ──────
SKILL_IDS = {
    "protocol_analysis": os.environ.get("SKILL_ID_PROTOCOL_ANALYSIS", ""),
    "pricing_quote":     os.environ.get("SKILL_ID_PRICING_QUOTE",     ""),
    "edc_builder":       os.environ.get("SKILL_ID_EDC_BUILDER",       ""),
    "dvs_specification": os.environ.get("SKILL_ID_DVS_SPECIFICATION", ""),
}

STATUS = {
    "not_started":           "Not Started",
    "analysis_running":      "Analysis Running",
    "analysis_complete":     "Analysis Complete",
    "build_pricing_running": "Build + Pricing Running",
    "build_complete":        "Build Complete",
    "pricing_complete":      "Pricing Complete",
    "dvs_running":           "DVS Running",
    "dvs_complete":          "DVS Complete — Awaiting Review",
    "all_complete":          "All Complete",
    "failed":                "Failed",
}


def _find(files: dict, *patterns) -> bytes | None:
    """Return bytes for the first filename that ends with any of the patterns."""
    for pat in patterns:
        for name, data in files.items():
            if name.lower().endswith(pat.lower()):
                return data
    return None


async def run_pipeline(item_id):
    try:
        # ── 0. Fetch item and inputs from monday.com ───────────────────────────
        item = await get_item(item_id)
        cols = {c["id"]: c for c in item["column_values"]}

        crf_url    = cols.get(COL["crf_library"],  {}).get("text")
        oc_std_url = cols.get(COL["oc_standard"],  {}).get("text")
        protocol_num = cols.get(COL["protocol_number"], {}).get("text", "study")

        print(f"PROTOCOL: {protocol_num}", flush=True)

        # Download protocol PDF (from asset attachment)
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
                if protocol_pdf:
                    break

        print(f"Protocol PDF: {len(protocol_pdf)} bytes", flush=True)

        # Optional customer library files
        crf_pdf  = await download_file(crf_url)    if crf_url    else None
        oc_zip   = await download_file(oc_std_url) if oc_std_url else None

        # ── 1. Protocol Analysis ───────────────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["analysis_running"])
        await append_log(item_id, "Protocol Analysis started.")
        print("Running protocol-analysis skill...", flush=True)

        # Build extra_text to describe optional inputs
        extra_parts = []
        if crf_pdf:
            extra_parts.append("Customer CRF Library (PDF) attached — use as Priority 1 for form matching.")
        if oc_zip:
            extra_parts.append("Customer OC4 XLSForm Standards (ZIP) attached — use as Priority 2 for form matching.")
        extra_text = "\n".join(extra_parts)

        analysis_files = await run_skill(
            PROTOCOL_ANALYSIS_PROMPT,
            skill_ids=[SKILL_IDS["protocol_analysis"]],
            pdf_bytes=protocol_pdf if protocol_pdf else None,
            zip_bytes=oc_zip,
            extra_text=extra_text,
        )

        # Extract the five outputs by filename suffix
        spec_pdf  = _find(analysis_files, "_Study_Specification.pdf")
        spec_xlsx = _find(analysis_files, "_Study_Specification.xlsx")
        spec_json = _find(analysis_files, "_Study_Specification.json")
        ps_pdf    = _find(analysis_files, "_Protocol_Summary.pdf")
        ps_json   = _find(analysis_files, "_Protocol_Summary.json")

        print(f"Analysis outputs — "
              f"spec_pdf:{spec_pdf is not None} spec_xlsx:{spec_xlsx is not None} "
              f"spec_json:{spec_json is not None} ps_pdf:{ps_pdf is not None} "
              f"ps_json:{ps_json is not None}", flush=True)

        # Upload to monday.com
        if spec_pdf:  await upload_file(item_id, COL["spec_pdf"],  f"{protocol_num}_Study_Specification.pdf",  spec_pdf)
        if spec_xlsx: await upload_file(item_id, COL["spec_xlsx"], f"{protocol_num}_Study_Specification.xlsx", spec_xlsx)
        if spec_json: await upload_file(item_id, COL["spec_json"], f"{protocol_num}_Study_Specification.json", spec_json)
        if ps_pdf:    await upload_file(item_id, COL["pricing_summary"], f"{protocol_num}_Protocol_Summary.pdf",  ps_pdf)
        if ps_json:   await upload_file(item_id, COL["pricing_summary"], f"{protocol_num}_Protocol_Summary.json", ps_json)

        await set_status(item_id, COL["pipeline_status"], STATUS["analysis_complete"])
        await append_log(item_id, "Protocol Analysis complete.")
        await asyncio.sleep(15)

        # ── 2. EDC Build + 3. Pricing Quote (run in parallel) ─────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["build_pricing_running"])
        await append_log(item_id, "EDC Build and Pricing Quote started.")
        print("Running edc-builder and pricing-quote in parallel...", flush=True)

        async def run_edc_build():
            if not spec_xlsx:
                print("Skipping EDC Build — no Study Specification XLSX", flush=True)
                return {}
            return await run_skill(
                EDC_BUILD_PROMPT,
                skill_ids=[SKILL_IDS["edc_builder"]],
                xlsx_bytes=spec_xlsx,
            )

        async def run_pricing_quote():
            if not ps_json:
                print("Skipping Pricing Quote — no Protocol Summary JSON", flush=True)
                return {}
            return await run_skill(
                PRICING_QUOTE_PROMPT,
                skill_ids=[SKILL_IDS["pricing_quote"]],
                extra_text="[Protocol Summary JSON attached as base64]\n" +
                           __import__("base64").standard_b64encode(ps_json).decode(),
            )

        build_files, quote_files = await asyncio.gather(
            run_edc_build(),
            run_pricing_quote(),
        )

        # EDC Build outputs
        build_zip = _find(build_files, "_EDC_Build.zip", ".zip")
        if build_zip:
            await upload_file(item_id, COL["edc_build"], f"{protocol_num}_EDC_Build.zip", build_zip)
        await set_status(item_id, COL["pipeline_status"], STATUS["build_complete"])
        await append_log(item_id, "EDC Build complete.")

        # Pricing Quote outputs
        quote_int_pdf  = _find(quote_files, "_Quote_Internal.pdf")
        quote_cli_pdf  = _find(quote_files, "_Quote_Client.pdf")
        quote_int_xlsx = _find(quote_files, "_Quote_Internal.xlsx")
        quote_cli_xlsx = _find(quote_files, "_Quote_Client.xlsx")

        if quote_int_pdf:  await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote_Internal.pdf",  quote_int_pdf)
        if quote_cli_pdf:  await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote_Client.pdf",    quote_cli_pdf)
        if quote_int_xlsx: await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote_Internal.xlsx", quote_int_xlsx)
        if quote_cli_xlsx: await upload_file(item_id, COL["pricing_quote"], f"{protocol_num}_Quote_Client.xlsx",   quote_cli_xlsx)

        await set_status(item_id, COL["pipeline_status"], STATUS["pricing_complete"])
        await append_log(item_id, "Pricing Quote complete.")
        await asyncio.sleep(15)

        # ── 4. DVS ─────────────────────────────────────────────────────────────
        if spec_xlsx and build_zip:
            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_running"])
            await append_log(item_id, "DVS started.")
            print("Running dvs-specification skill...", flush=True)

            dvs_files = await run_skill(
                DVS_PROMPT,
                skill_ids=[SKILL_IDS["dvs_specification"]],
                xlsx_bytes=spec_xlsx,
                zip_bytes=build_zip,
            )

            dvs_xlsx = _find(dvs_files, "_DVS.xlsx", ".xlsx")
            if dvs_xlsx:
                await upload_file(item_id, COL["dvs_output"], f"{protocol_num}_DVS.xlsx", dvs_xlsx)

            await set_status(item_id, COL["pipeline_status"], STATUS["dvs_complete"])
            await append_log(item_id, "DVS complete.")
        else:
            print("Skipping DVS — missing spec_xlsx or build_zip", flush=True)

        # ── Done ───────────────────────────────────────────────────────────────
        await set_status(item_id, COL["pipeline_status"], STATUS["all_complete"])
        await append_log(item_id, "Pipeline complete. All outputs uploaded.")

    except Exception as e:
        import traceback
        print(f"PIPELINE CRASHED: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        await append_log(item_id, f"PIPELINE ERROR: {e}")
        await set_status(item_id, COL["pipeline_status"], STATUS["failed"])
        raise
