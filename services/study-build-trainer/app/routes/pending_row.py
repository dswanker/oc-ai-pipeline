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

from core.monday_client import INGEST_STATUS_LABELS, PATH_LABELS, MondayClient

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", status_code=201)
async def create_pending_row(
    response: Response,
    protocol_pdf: UploadFile | None = File(None),
    name: str = Form(...),
    sponsor_client: str | None = Form(None),
    source_pipeline_item: str | None = Form(None),
    protocol_number: str | None = Form(None),
    protocol_pdf_sha256: str | None = Form(None),
    study_spec_json: UploadFile | None = File(None),
    edc_build_zip: UploadFile | None = File(None),
    # Path-M (migration) fields
    odm_xml: UploadFile | None = File(None),
    source_system: str | None = Form(None),
    path: str | None = Form(None),
    ingest_status_key: str | None = Form(None),
) -> dict[str, int | str]:
    """
    Create a new corpus board row.

    Path B (protocol-PDF) and Path M (migration ODM-XML) both seed rows
    through this endpoint. Exactly one source artifact is required —
    either ``protocol_pdf`` or ``odm_xml``. The route validates ``path``
    against PATH_LABELS and dispatches dedup to the right finder.

    Form fields (Path B + shared):
      - protocol_pdf: the protocol PDF (multipart). Required on Path B,
        omit on Path M.
      - name: row title (typically the protocol number, e.g. ABT-CIP-10601).
      - sponsor_client: optional sponsor name. Used (with protocol_number)
        as the Path-B dedup key.
      - source_pipeline_item: optional oc-ai-pipeline item ID for
        traceability — links the corpus row back to the originating run.
      - protocol_number: optional protocol number, written to the
        protocol_number text column. On Path M the caller may pass the
        ODM study OID here as a fallback dedup key.
      - protocol_pdf_sha256: optional hex SHA-256 of protocol_pdf. The
        server always recomputes the canonical SHA-256 from pdf_bytes;
        a diverging client value is warning-logged, not fatal.
      - study_spec_json: optional pipeline Study Spec JSON, uploaded to
        the protocol_analysis_json file column.
      - edc_build_zip: optional pipeline EDC Build ZIP, uploaded to the
        predicted_edc_zip file column.

    Path-M fields:
      - odm_xml: the source EDC ODM XML (multipart). Required on Path M,
        omit on Path B.
      - source_system: vendor label (e.g. "Medidata Rave"). Written to
        the Source System text column. Combined with protocol_number it
        forms the Path-M dedup key.
      - path: one of PATH_LABELS keys ("protocol" | "migration"). Defaults
        to "protocol" when omitted to preserve legacy Path-B behaviour.
      - ingest_status_key: status to set on the new row. Defaults to
        "awaiting_build_completion" (Path B). Path-M callers pass
        "pending_ps_review".

    Returns:
      On create (HTTP 201):
        {"item_id": <new_id>, "status": "<label>"}

      On dedup skip (HTTP 200) — when the path-appropriate dedup key
      matches an existing row:
        {"action": "skipped", "existing_item_id": <id>, "status": <existing_label>}
    """
    if not name:
        raise HTTPException(400, "name is required")

    # ── Path detection + validation ─────────────────────────────────
    path_key = path or "protocol"
    if path_key not in PATH_LABELS:
        raise HTTPException(
            400,
            f"path must be one of {sorted(PATH_LABELS)} (got {path!r})",
        )

    effective_status_key = ingest_status_key or "awaiting_build_completion"
    if effective_status_key not in INGEST_STATUS_LABELS:
        raise HTTPException(
            400,
            f"ingest_status_key must be one of {sorted(INGEST_STATUS_LABELS)} "
            f"(got {ingest_status_key!r})",
        )

    # ── Source artifact — exactly one of protocol_pdf / odm_xml ─────
    pdf_bytes: bytes = b""
    if protocol_pdf is not None:
        pdf_bytes = await protocol_pdf.read()
    odm_bytes: bytes = b""
    if odm_xml is not None:
        odm_bytes = await odm_xml.read()

    if not pdf_bytes and not odm_bytes:
        raise HTTPException(
            400,
            "one of protocol_pdf (Path B) or odm_xml (Path M) is required",
        )

    # Server-computed SHA-256 is canonical when a PDF is present.
    computed_sha256: str | None = None
    if pdf_bytes:
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
        path=path_key,
        sponsor=sponsor_client,
        source_system=source_system,
        source_item=source_pipeline_item,
        pdf_bytes=len(pdf_bytes),
        odm_xml_bytes=len(odm_bytes),
        filename=(protocol_pdf.filename if protocol_pdf is not None else None),
        odm_filename=(odm_xml.filename if odm_xml is not None else None),
        protocol_number=protocol_number,
        protocol_pdf_sha256=computed_sha256,
        ingest_status=effective_status_key,
        study_spec_json_bytes=len(study_spec_bytes) if study_spec_bytes else 0,
        edc_build_zip_bytes=len(edc_build_bytes) if edc_build_bytes else 0,
    )

    async with MondayClient() as monday:
        # 0. Dedup — Path B uses (sponsor_client, protocol_number),
        #    Path M uses (source_system, protocol_number-or-study-oid).
        #    On match: read the row, optionally append a PDF-drift
        #    warning to human_notes, and short-circuit with HTTP 200.
        existing_id: int | None = None
        if path_key == "migration" and source_system and protocol_number:
            existing_id = await monday.find_existing_row_migration(
                source_system, protocol_number,
            )
        elif path_key == "protocol" and sponsor_client and protocol_number:
            existing_id = await monday.find_existing_row(
                sponsor_client, protocol_number,
            )

        if existing_id is not None:
            existing = await monday.get_item(existing_id)
            # PDF-drift detection is only meaningful when both sides have a
            # PDF SHA. Path M rows have no PDF so they always skip this.
            pdf_drift = (
                computed_sha256 is not None
                and existing.protocol_pdf_sha256 is not None
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
                path=path_key,
                sponsor=sponsor_client,
                source_system=source_system,
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

        # 1. Create the row in the caller-requested status.
        item_id = await monday.create_row(
            name=name,
            sponsor_client=sponsor_client,
            source_pipeline_item=source_pipeline_item,
            ingest_status_key=effective_status_key,
            source_system=source_system,
            path_key=path_key,
        )

        # 2. Upload the source artifact:
        #    - Path B → protocol PDF to the protocol file column
        #    - Path M → ODM XML to the source_odm_xml file column
        if pdf_bytes:
            filename = (protocol_pdf.filename if protocol_pdf is not None else None) \
                or f"{name}_protocol.pdf"
            await monday.upload_file_to_column(
                item_id=item_id,
                col_key="protocol",
                filename=filename,
                content=pdf_bytes,
            )
        if odm_bytes:
            odm_filename = (odm_xml.filename if odm_xml is not None else None) \
                or f"{name}_source.xml"
            await monday.upload_file_to_column(
                item_id=item_id,
                col_key="source_odm_xml",
                filename=odm_filename,
                content=odm_bytes,
            )

        # 3. Write text columns: PDF SHA (when we have a PDF) + optional
        #    protocol_number. source_system is written by create_row above.
        if computed_sha256:
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
        path=path_key,
        sponsor=sponsor_client,
        source_system=source_system,
        protocol_number=protocol_number,
        protocol_pdf_sha256=computed_sha256,
        ingest_status=effective_status_key,
        study_spec_uploaded=bool(study_spec_bytes),
        edc_build_uploaded=bool(edc_build_bytes),
        source_odm_uploaded=bool(odm_bytes),
    )

    return {
        "item_id": item_id,
        "status": INGEST_STATUS_LABELS[effective_status_key],
    }
