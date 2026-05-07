"""
POST /pending-row — entry point from oc-ai-pipeline.

When a human checks "Send to Trainer" on the oc-ai-pipeline monday
board, the pipeline calls this endpoint to seed a new row on the
trainer's monday corpus board. The row starts in "Awaiting Build
Completion" status — meaning the protocol is attached but the human
hasn't uploaded the final form definitions yet.

Later, a human visits that row on the trainer board, uploads the
final ODM XML or XLSForm zip, and flips the trigger. The trainer's
existing webhook flow takes over from there (parse, embed, index).

This endpoint is the second entry point into the corpus alongside
direct human entry on the trainer board itself.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from core.monday_client import MondayClient

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", status_code=201)
async def create_pending_row(
    protocol_pdf: UploadFile = File(...),
    name: str = Form(...),
    sponsor_client: str | None = Form(None),
    source_pipeline_item: str | None = Form(None),
    protocol_number: str | None = Form(None),
    protocol_pdf_sha256: str | None = Form(None),
    study_spec_json: UploadFile | None = File(None),
    edc_build_zip: UploadFile | None = File(None),
) -> dict[str, int | str]:
    """
    Create a new corpus board row with the protocol PDF attached.

    Form fields:
      - protocol_pdf: the PDF file (multipart upload)
      - name: row title (typically the protocol number, e.g. ABT-CIP-10601)
      - sponsor_client: optional sponsor name to seed the Sponsor/Client column
      - source_pipeline_item: optional oc-ai-pipeline item ID for traceability
      - protocol_number: optional protocol number (Phase 1.2: accepted, logged, not stored)
      - protocol_pdf_sha256: optional hex SHA-256 of protocol_pdf (Phase 1.2: accepted, logged, not stored)
      - study_spec_json: optional pipeline Study Spec JSON file (Phase 1.2: accepted, logged, not stored)
      - edc_build_zip: optional pipeline EDC Build ZIP file (Phase 1.2: accepted, logged, not stored)

    Returns:
      {"item_id": <new_id>, "status": "awaiting_build_completion"}
    """
    if not name:
        raise HTTPException(400, "name is required")

    pdf_bytes = await protocol_pdf.read()
    if not pdf_bytes:
        raise HTTPException(400, "protocol_pdf is empty")

    logger.info(
        "pending_row.received",
        name=name,
        sponsor=sponsor_client,
        source_item=source_pipeline_item,
        pdf_bytes=len(pdf_bytes),
        filename=protocol_pdf.filename,
        protocol_number=protocol_number,
        protocol_pdf_sha256=protocol_pdf_sha256,
        study_spec_json_filename=study_spec_json.filename if study_spec_json else None,
        edc_build_zip_filename=edc_build_zip.filename if edc_build_zip else None,
    )

    async with MondayClient() as monday:
        # 1. Create the row in "Awaiting Build Completion" status.
        item_id = await monday.create_row(
            name=name,
            sponsor_client=sponsor_client,
            source_pipeline_item=source_pipeline_item,
            ingest_status_key="awaiting_build_completion",
        )

        # 2. Upload the protocol PDF to the row's protocol column.
        filename = protocol_pdf.filename or f"{name}_protocol.pdf"
        await monday.upload_file_to_column(
            item_id=item_id,
            col_key="protocol",
            filename=filename,
            content=pdf_bytes,
        )

    logger.info("pending_row.created", item_id=item_id, name=name)

    return {
        "item_id": item_id,
        "status": "awaiting_build_completion",
    }
