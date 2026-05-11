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

import hashlib
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from core.monday_client import INGEST_STATUS_LABELS, MondayClient

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", status_code=201)
async def create_pending_row(
    response: Response,
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
      - protocol_number: optional protocol number, written to the
        protocol_number text column when supplied. Combined with
        sponsor_client it forms the dedup key for /pending-row reruns.
      - protocol_pdf_sha256: optional hex SHA-256 of protocol_pdf
        supplied by the caller. The server always computes the canonical
        SHA-256 from pdf_bytes; if the caller's value differs a warning
        is logged and the server-computed value is used.
      - study_spec_json: optional pipeline Study Spec JSON, uploaded to
        the protocol_analysis_json file column when supplied.
      - edc_build_zip: optional pipeline EDC Build ZIP, uploaded to the
        predicted_edc_zip file column when supplied.

    Returns:
      On create (HTTP 201):
        {"item_id": <new_id>, "status": "Awaiting Build Completion"}

      On dedup skip (HTTP 200) — when (sponsor_client, protocol_number)
      matches an existing corpus row:
        {"action": "skipped", "existing_item_id": <id>, "status": <existing_label>}
    """
    if not name:
        raise HTTPException(400, "name is required")

    pdf_bytes = await protocol_pdf.read()
    if not pdf_bytes:
        raise HTTPException(400, "protocol_pdf is empty")

    # Server-computed SHA-256 is canonical; warn if caller's value diverges.
    computed_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    if protocol_pdf_sha256 and protocol_pdf_sha256 != computed_sha256:
        logger.warning(
            "pending_row.sha256_mismatch",
            client_sha256=protocol_pdf_sha256,
            server_sha256=computed_sha256,
            name=name,
        )

    # Read optional file bodies up-front so we can size-log them and
    # reuse the bytes for the monday upload calls below.
    study_spec_bytes: bytes | None = None
    if study_spec_json is not None:
        study_spec_bytes = await study_spec_json.read() or None
    edc_build_bytes: bytes | None = None
    if edc_build_zip is not None:
        edc_build_bytes = await edc_build_zip.read() or None

    logger.info(
        "pending_row.received",
        name=name,
        sponsor=sponsor_client,
        source_item=source_pipeline_item,
        pdf_bytes=len(pdf_bytes),
        filename=protocol_pdf.filename,
        protocol_number=protocol_number,
        protocol_pdf_sha256=computed_sha256,
        study_spec_json_bytes=len(study_spec_bytes) if study_spec_bytes else 0,
        edc_build_zip_bytes=len(edc_build_bytes) if edc_build_bytes else 0,
    )

    async with MondayClient() as monday:
        # 0. Dedup: if both sponsor_client and protocol_number were
        #    supplied, look for an existing corpus row matching the pair.
        #    On match: read the row, optionally append a PDF-drift
        #    warning to human_notes, and short-circuit with HTTP 200.
        if sponsor_client and protocol_number:
            existing_id = await monday.find_existing_row(
                sponsor_client, protocol_number,
            )
            if existing_id is not None:
                existing = await monday.get_item(existing_id)
                pdf_drift = (
                    existing.protocol_pdf_sha256 is not None
                    and existing.protocol_pdf_sha256 != computed_sha256
                )
                if pdf_drift:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    warning = (
                        f"[PDF DRIFT WARNING {ts}] pipeline re-pushed "
                        f"protocol with different PDF bytes. "
                        f"Stored SHA-256: {existing.protocol_pdf_sha256}. "
                        f"Incoming SHA-256: {computed_sha256}. "
                        f"The existing row was NOT overwritten — "
                        f"investigate whether protocol content actually changed."
                    )
                    combined = (
                        f"{existing.human_notes}\n\n{warning}"
                        if existing.human_notes else warning
                    )
                    # TODO: if drift warnings become frequent, consider per-row
                    # lock or monday compare-and-swap.
                    await monday.set_long_text(existing_id, "human_notes", combined)
                logger.info(
                    "pending_row.skipped",
                    existing_item_id=existing_id,
                    sponsor=sponsor_client,
                    protocol_number=protocol_number,
                    pdf_drift=pdf_drift,
                    existing_status=existing.ingest_status,
                )
                response.status_code = 200
                return {
                    "action": "skipped",
                    "existing_item_id": existing_id,
                    "status": existing.ingest_status or "unknown",
                }

        # 1. Create the row in "Awaiting Build Completion" status.
        item_id = await monday.create_row(
            name=name,
            sponsor_client=sponsor_client,
            source_pipeline_item=source_pipeline_item,
            ingest_status_key="awaiting_build_completion",
        )

        # 2. Upload the protocol PDF to the protocol file column.
        filename = protocol_pdf.filename or f"{name}_protocol.pdf"
        await monday.upload_file_to_column(
            item_id=item_id,
            col_key="protocol",
            filename=filename,
            content=pdf_bytes,
        )

        # 3. Write canonical PDF SHA-256 + optional protocol_number text columns.
        await monday.set_text(item_id, "protocol_pdf_sha256", computed_sha256)
        if protocol_number:
            await monday.set_text(item_id, "protocol_number", protocol_number)

        # 4. Upload optional pipeline-side artifacts to their file columns.
        if study_spec_bytes:
            await monday.upload_file_to_column(
                item_id=item_id,
                col_key="protocol_analysis_json",
                filename=study_spec_json.filename or f"{name}_study_spec.json",
                content=study_spec_bytes,
            )
        if edc_build_bytes:
            await monday.upload_file_to_column(
                item_id=item_id,
                col_key="predicted_edc_zip",
                filename=edc_build_zip.filename or f"{name}_edc_build.zip",
                content=edc_build_bytes,
            )

    logger.info(
        "pending_row.created",
        item_id=item_id,
        name=name,
        sponsor=sponsor_client,
        protocol_number=protocol_number,
        protocol_pdf_sha256=computed_sha256,
        study_spec_uploaded=bool(study_spec_bytes),
        edc_build_uploaded=bool(edc_build_bytes),
    )

    return {
        "item_id": item_id,
        "status": INGEST_STATUS_LABELS["awaiting_build_completion"],
    }
