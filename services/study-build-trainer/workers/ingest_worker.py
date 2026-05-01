"""
Ingest worker — runs one ingest job to completion.

Phase 1 scope: protocol IS required. CT.gov fallback is deferred.
The flow is deterministic and has these terminal states:
  * indexed
  * awaiting_human (curator must supply something)
  * awaiting_build_completion (auto-stubbed by pipeline; no form yet)
  * missing_odm_xml / missing_xls_forms / missing_both_files
      (trigger fired but required files not uploaded)
  * failed (something blew up; curator should investigate)

Happy path (both ODM XML and XLSForm ZIP present):

    1. Fetch row from monday.
    2. Validate inputs. Both form_design (ODM XML) AND final_xls_forms
       (XLSForm ZIP) must be present. If either is missing, set the
       appropriate "Missing..." status and stop — no work is done.
       If no form at all but protocol present → Awaiting Build Completion.
       If protocol missing entirely → Awaiting Human / Supply Protocol.
    3. Status → parsing_form.
    4. Cache all files (form_design + final_xls_forms + protocol +
       analysis JSON) into corpus/files/<pair_hash>/.
    5. Parse form_design (ODM XML) → ParsedForm.
    6. Extract fingerprint from form.
    7. Get protocol-analysis JSON (cached or fresh from skill).
    8. Status → generating_predicted_build. Run edc-builder skill to
       produce a predicted EDC ZIP for comparison.
    9. Status → comparing_builds. Run accuracy scorer and write score
       + XLSX report to the accuracy columns on the monday row.
   10. Embed analysis JSON.
   11. Index in the vector store under pair_hash = "row_<item_id>".
   12. Write fingerprint summary, indexed_pair_hash, index_date, and
       status → indexed back to monday.

Each step is a private method so failures are localized.

Test seam: ``IngestWorker`` accepts every collaborator via its
constructor. Production code uses ``app.deps.get_*`` dependency
providers; tests inject fakes.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.fingerprint import StudyFingerprint
from core.form_parser import parser_for
from core.form_parser.base import ParsedForm
from core.monday_client import (
    COL,
    CorpusItem,
    MondayClient,
)
from core.vector_store import IndexInput, VectorStore
from workers.queue import IngestJob, IngestJobKind, IngestJobState

if TYPE_CHECKING:  # pragma: no cover
    from core.embed import Embedder
    from core.fingerprint import FingerprintExtractor


# Logger — same shim pattern used elsewhere in core/.
try:
    import structlog

    logger = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging

    _stdlogger = logging.getLogger(__name__)

    class _StdlibShimLogger:
        @staticmethod
        def _fmt(event: str, kw: dict[str, Any]) -> str:
            if not kw:
                return event
            tail = " ".join(f"{k}={v!r}" for k, v in kw.items())
            return f"{event} {tail}"

        def info(self, event: str, **kw: Any) -> None:
            _stdlogger.info(self._fmt(event, kw))

        def warning(self, event: str, **kw: Any) -> None:
            _stdlogger.warning(self._fmt(event, kw))

        def error(self, event: str, **kw: Any) -> None:
            _stdlogger.error(self._fmt(event, kw))

        def exception(self, event: str, **kw: Any) -> None:
            _stdlogger.exception(self._fmt(event, kw))

        def debug(self, event: str, **kw: Any) -> None:
            _stdlogger.debug(self._fmt(event, kw))

    logger = _StdlibShimLogger()


# ─── Helpers ─────────────────────────────────────────────────────


def make_pair_hash(item_id: int) -> str:
    """Deterministic pair_hash from monday item_id."""
    return f"row_{item_id}"


def fingerprint_summary_text(fp: StudyFingerprint) -> str:
    """One-line summary of a StudyFingerprint for the Fingerprint column."""
    parts: list[str] = []
    if fp.sponsor:
        parts.append(f"sponsor: {fp.sponsor}")
    if fp.indication:
        parts.append(f"indication: {fp.indication}")
    if fp.phase:
        parts.append(f"phase: {fp.phase}")
    if fp.therapeutic_area:
        parts.append(f"therapeutic_area: {fp.therapeutic_area}")
    if fp.intervention:
        parts.append(f"intervention: {', '.join(fp.intervention)}")
    parts.append(f"confidence: {fp.extraction_confidence:.2f}")
    if fp.notes:
        parts.append(f"notes: {fp.notes}")
    return " | ".join(parts)


# ─── The worker ──────────────────────────────────────────────────


class IngestWorker:
    """Runs one ingest job to completion. Stateless across jobs."""

    def __init__(
        self,
        *,
        monday: MondayClient,
        embedder: "Embedder",
        vector_store: VectorStore,
        fingerprint_extractor: "FingerprintExtractor",
        run_protocol_analysis: Any,  # async callable: (pdf_bytes) -> str
        parser_for_fn: Any = parser_for,  # injectable for tests
    ) -> None:
        self.monday = monday
        self.embedder = embedder
        self.vector_store = vector_store
        self.fingerprint_extractor = fingerprint_extractor
        self.run_protocol_analysis = run_protocol_analysis
        self._parser_for = parser_for_fn

    # ── Top-level entry point ────────────────────────────────────

    async def process(self, job: IngestJob) -> None:
        """Run one job. Mutates job.state. Returns None."""
        if job.kind != IngestJobKind.START:
            raise ValueError(
                f"IngestWorker handles START jobs only (got {job.kind})"
            )

        if job.monday_item_id is None:
            raise ValueError("START job requires monday_item_id")

        item_id = job.monday_item_id
        log_ctx = {"job_id": job.job_id, "item_id": item_id}
        logger.info("ingest.start", **log_ctx)

        try:
            await self._run(job, item_id, log_ctx)
        except _MissingFiles as exc:
            # Validation failure — human forgot to upload a required
            # file before flipping Trigger. Set the descriptive status
            # so they know exactly what to fix. Job is not "failed"
            # (no bug), just incomplete.
            job.state = IngestJobState.AWAITING_HUMAN
            await self.monday.set_ingest_status(item_id, exc.status_key)
            await self.monday.set_long_text(
                item_id, "human_notes", exc.message
            )
            logger.info(
                "ingest.missing_files",
                status=exc.status_key, reason=exc.message, **log_ctx,
            )
        except _AwaitingHuman as exc:
            job.state = IngestJobState.AWAITING_HUMAN
            await self._set_awaiting_human(item_id, exc.decision_key, exc.message)
            logger.info("ingest.awaiting_human", reason=exc.message, **log_ctx)
        except _AwaitingBuildCompletion as exc:
            job.state = IngestJobState.AWAITING_HUMAN
            await self._set_awaiting_build_completion(item_id, exc.message)
            logger.info("ingest.awaiting_build_completion", **log_ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest.failed", **log_ctx)
            job.state = IngestJobState.FAILED
            job.error = str(exc)
            await self._handle_failure(item_id, exc)

    # ── The pipeline itself ──────────────────────────────────────

    async def _run(
        self,
        job: IngestJob,
        item_id: int,
        log_ctx: dict[str, Any],
    ) -> None:
        # 1. Fetch row
        item = await self.monday.get_item(item_id)
        logger.info("ingest.row_loaded", name=item.name, **log_ctx)

        # 2. Validate inputs — check both required files are present.
        #    This is the first gate: if files are missing the job stops
        #    here with a descriptive status. The human re-uploads and
        #    re-triggers; no heavy work is wasted.
        self._validate_inputs(item)

        # 3. Status → parsing_form
        await self.monday.set_ingest_status(item_id, "parsing_form")

        # 4. Cache all files locally (ODM XML, XLSForm ZIP, protocol PDF,
        #    analysis JSON if present)
        pair_hash = make_pair_hash(item_id)
        cached = await self.monday.cache_files_for_pair(item, pair_hash)

        if "form_design" not in cached:
            raise RuntimeError("form_design (ODM XML) failed to download")
        if "final_xls_forms" not in cached:
            raise RuntimeError("final_xls_forms (XLSForm ZIP) failed to download")

        # 5. Parse ODM XML → ParsedForm (used for structural layers:
        #    Study, Events, Form placement)
        form_design_path = cached["form_design"]
        parsed = await self._parse_form(form_design_path)
        logger.info(
            "ingest.form_parsed",
            forms=len(parsed.forms),
            sponsor_in_form=parsed.sponsor,
            **log_ctx,
        )

        # 6. Extract fingerprint from parsed ODM structure
        fp = await self._extract_fingerprint(parsed, item)
        logger.info(
            "ingest.fingerprint",
            sponsor=fp.sponsor, phase=fp.phase, indication=fp.indication,
            confidence=fp.extraction_confidence, **log_ctx,
        )

        # 7. Get the analysis JSON (for embedding / retrieval)
        analysis_dict = await self._get_analysis_json(cached, item, log_ctx)

        # 8. Status → generating_predicted_build
        #    Run the edc-builder skill (same code the pipeline uses) to
        #    produce a predicted EDC ZIP from the cached analysis JSON.
        await self.monday.set_ingest_status(item_id, "generating_predicted_build")
        logger.info("ingest.generating_predicted_build", **log_ctx)
        predicted_edc_zip: bytes | None = None
        try:
            loop = asyncio.get_event_loop()
            predicted_edc_zip = await loop.run_in_executor(
                None, lambda: self._generate_predicted_build(analysis_dict, log_ctx)
            )
        except Exception as e:
            logger.warning("ingest.predicted_build_failed", error=str(e), **log_ctx)

        # 9. Status → comparing_builds
        #    Run accuracy scorer and write score + XLSX to monday.
        await self.monday.set_ingest_status(item_id, "comparing_builds")
        logger.info("ingest.comparing_builds", **log_ctx)
        if (predicted_edc_zip
                and "form_design" in cached
                and "final_xls_forms" in cached):
            try:
                loop = asyncio.get_event_loop()
                accuracy = await loop.run_in_executor(
                    None,
                    lambda: self._score_accuracy(
                        cached["form_design"],
                        cached["final_xls_forms"],
                        analysis_dict,
                        predicted_edc_zip,
                        pair_hash,
                    ),
                )
                await self.monday.set_number(
                    item_id, "accuracy_score", accuracy["overall_pct"]
                )
                protocol = (analysis_dict.get("study_meta", {})
                            .get("protocol_number", pair_hash))
                await self.monday.upload_file_to_column(
                    item_id, "accuracy_report",
                    f"{protocol}_Accuracy_Report.xlsx",
                    accuracy["xlsx_bytes"],
                )
                logger.info("ingest.accuracy_scored",
                            overall_pct=accuracy["overall_pct"],
                            diff_count=accuracy["diff_count"], **log_ctx)
            except Exception as e:
                logger.warning("ingest.accuracy_failed", error=str(e), **log_ctx)

        # 10. Embed
        query_vec = await self.embedder.embed_protocol_analysis(analysis_dict)
        logger.info("ingest.embedded", dim=len(query_vec), **log_ctx)

        # 11. Index
        xlsforms_path = cached["final_xls_forms"]
        await self.vector_store.add(IndexInput(
            pair_hash=pair_hash,
            embedding=query_vec,
            monday_item_id=item_id,
            sponsor=fp.sponsor,
            indication=fp.indication,
            phase=fp.phase,
            therapeutic_area=fp.therapeutic_area,
            nct_id=None,  # CT.gov deferred
            has_protocol=("protocol" in cached) or ("protocol_analysis_json" in cached),
            form_design_path=str(form_design_path),
            protocol_path=str(cached.get("protocol", "") or "") or None,
            fingerprint_json=json.dumps(analysis_dict),
        ))
        logger.info("ingest.indexed", pair_hash=pair_hash, **log_ctx)

        # 12. Write back to monday
        await self._write_back_metadata(item_id, fp, pair_hash)

        job.state = IngestJobState.DONE
        logger.info("ingest.done", pair_hash=pair_hash, **log_ctx)

    # ── Sub-steps ────────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(item: CorpusItem) -> None:
        """
        Gate on required files before doing any work.

        For full ingest, BOTH files are required:
          * form_design     — ODM XML (actual build, structural layers)
          * final_xls_forms — XLSForm ZIP (actual build, form layers)
        """
        has_xml      = bool(item.files_by_column.get("form_design"))
        has_xls      = bool(item.files_by_column.get("final_xls_forms"))
        has_protocol = bool(item.files_by_column.get("protocol"))
        has_analysis = bool(item.files_by_column.get("protocol_analysis_json"))

        # Auto-stub: pipeline created this row; human hasn't uploaded
        # form files yet. Wait quietly.
        if not has_xml and not has_xls and has_protocol:
            raise _AwaitingBuildCompletion(
                "Neither ODM XML nor XLSForm ZIP uploaded yet; "
                "protocol is present. Upload both files and re-trigger."
            )

        # No protocol context at all — can't do anything useful.
        if not has_protocol and not has_analysis:
            raise _AwaitingHuman(
                decision_key="supply_protocol",
                message="No protocol or protocol-analysis JSON attached.",
            )

        # Both files missing (but protocol is present).
        if not has_xml and not has_xls:
            raise _MissingFiles(
                status_key="missing_both_files",
                message=(
                    "Both ODM XML and XLSForm ZIP are required before "
                    "triggering ingest. Please upload both files and "
                    "re-set Trigger to 'Send to Trainer'."
                ),
            )

        # Only ODM XML missing.
        if not has_xml:
            raise _MissingFiles(
                status_key="missing_odm_xml",
                message=(
                    "ODM XML file is missing. Please upload the study's "
                    "ODM XML export to the 'Form Design' column and "
                    "re-set Trigger to 'Send to Trainer'."
                ),
            )

        # Only XLSForm ZIP missing.
        if not has_xls:
            raise _MissingFiles(
                status_key="missing_xls_forms",
                message=(
                    "XLSForm ZIP is missing. Please upload the final "
                    "EDC build ZIP to the 'Final XLS Forms' column and "
                    "re-set Trigger to 'Send to Trainer'."
                ),
            )

        # Both files present — fall through to ingest.

    async def _parse_form(self, form_path: Path) -> ParsedForm:
        """Pick a parser based on filename and run it."""
        parser = self._parser_for(form_path.name)
        with open(form_path, "rb") as f:
            data = f.read()
        return await parser.parse(data, filename=form_path.name)

    async def _extract_fingerprint(
        self,
        parsed: ParsedForm,
        item: CorpusItem,
    ) -> StudyFingerprint:
        """Run the fingerprint extractor with optional curator overrides."""
        overrides: dict[str, Any] = {}
        if item.sponsor_client:
            overrides["sponsor"] = item.sponsor_client

        return await self.fingerprint_extractor.extract(
            parsed,
            overrides=overrides or None,
        )

    async def _get_analysis_json(
        self,
        cached: dict[str, Path],
        item: CorpusItem,
        log_ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the protocol-analysis JSON.

        Uses cached JSON if available; otherwise calls the
        protocol-analysis skill on the protocol PDF.
        """
        if "protocol_analysis_json" in cached:
            json_path = cached["protocol_analysis_json"]
            text = json_path.read_text(encoding="utf-8")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "ingest.analysis_json_parse_failed",
                    path=str(json_path), error=str(exc), **log_ctx,
                )


        # Check for previously generated analysis JSON on disk (Railway volume).
        # This avoids re-running the expensive skill on every re-trigger.
        if "protocol" in cached:
            disk_cache = cached["protocol"].parent / "analysis.generated.json"
            if disk_cache.exists():
                try:
                    data = json.loads(disk_cache.read_text(encoding="utf-8"))
                    if data:
                        logger.info("ingest.analysis_loaded_from_disk",
                                    path=str(disk_cache), **log_ctx)
                        # Upload to monday column if not already there
                        await self._upload_analysis_json(
                            item, data, disk_cache.read_bytes(), log_ctx
                        )
                        return data
                except (json.JSONDecodeError, OSError):
                    pass  # Fall through to re-generate

        if "protocol" not in cached:
            raise RuntimeError(
                "Cannot produce analysis JSON: no protocol PDF cached."
            )
        pdf_bytes = cached["protocol"].read_bytes()
        logger.info("ingest.running_protocol_analysis", **log_ctx)
        response_text = await self.run_protocol_analysis(pdf_bytes)

        analysis_dict = _extract_json_from_text(response_text)
        if not analysis_dict:
            raise RuntimeError(
                "Protocol analysis ran but returned no parseable JSON."
            )

        cache_path = cached["protocol"].parent / "analysis.generated.json"
        cache_path.write_text(json.dumps(analysis_dict, indent=2))
        logger.info("ingest.analysis_cached", path=str(cache_path), **log_ctx)
        await self._upload_analysis_json(
            item, analysis_dict, cache_path.read_bytes(), log_ctx
        )
        return analysis_dict

    async def _upload_analysis_json(
        self,
        item: CorpusItem,
        analysis_dict: dict[str, Any],
        json_bytes: bytes,
        log_ctx: dict[str, Any],
    ) -> None:
        """Upload analysis JSON to the monday protocol_analysis_json column.
        Non-fatal — logs a warning if it fails.
        Skips if the column already has a file attached to avoid duplicates.
        """
        # Skip if already uploaded
        if item.files_by_column.get("protocol_analysis_json"):
            return
        try:
            protocol = (analysis_dict.get("study_meta", {}).get("protocol_number")
                        or item.name
                        or "analysis")
            await self.monday.upload_file_to_column(
                item.item_id, "protocol_analysis_json",
                f"{protocol}_analysis.json",
                json_bytes,
            )
            logger.info("ingest.analysis_uploaded_to_monday", **log_ctx)
        except Exception as e:
            logger.warning("ingest.analysis_upload_failed",
                           error=str(e), **log_ctx)

    def _generate_predicted_build(
        self,
        analysis_dict: dict[str, Any],
        log_ctx: dict[str, Any],
    ) -> bytes:
        """
        Generate a predicted EDC build ZIP from the cached analysis JSON.
        Uses the edc-builder scripts directly (same as pipeline.run_edc_build).
        Returns zip bytes. Synchronous — call via run_in_executor.
        """
        import sys
        import os
        import tempfile

        skills_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "skills"
        )
        edc_scripts = os.path.join(skills_dir, "edc-builder", "scripts")
        if edc_scripts not in sys.path:
            sys.path.insert(0, edc_scripts)

        from build_xlsforms  import build_all_xlsforms, write_timepoint_csv, write_labranges_csv
        from build_checklist import build_checklist_pdf, build_checklist_xlsx
        from build_package   import build_package

        protocol = (analysis_dict.get("study_meta", {}).get("protocol_number")
                    or "STUDY")
        build_log = {
            "forms_built": [], "forms_skipped": [], "build_errors": [],
            "build_warnings": [], "placeholder_applied": [], "oid_placeholders": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            forms_dir     = os.path.join(tmp, "forms")
            csv_dir       = os.path.join(tmp, "csv")
            checklist_dir = os.path.join(tmp, "checklist")
            package_dir   = os.path.join(tmp, "package")
            for d in (forms_dir, csv_dir, checklist_dir, package_dir):
                os.makedirs(d, exist_ok=True)

            build_all_xlsforms(analysis_dict, forms_dir, build_log)
            write_timepoint_csv(
                analysis_dict.get("timepoint_csv", {}),
                os.path.join(csv_dir, f"{protocol}_tpt.csv"), build_log,
            )
            write_labranges_csv(
                analysis_dict.get("labranges_csv", {}),
                os.path.join(csv_dir, f"{protocol}_labranges.csv"), build_log,
            )
            build_checklist_pdf(
                analysis_dict, build_log,
                os.path.join(checklist_dir, f"{protocol}_Build_Checklist.pdf"),
            )
            build_checklist_xlsx(
                analysis_dict, build_log,
                os.path.join(checklist_dir, f"{protocol}_Build_Checklist.xlsx"),
            )
            zip_path  = build_package(
                analysis_dict, build_log,
                forms_dir, csv_dir, checklist_dir, package_dir,
            )
            zip_bytes = open(zip_path, "rb").read()

        logger.info("ingest.predicted_build_generated",
                    bytes=len(zip_bytes), **log_ctx)
        return zip_bytes

    def _score_accuracy(
        self,
        actual_xml: Path,
        actual_xls: Path,
        analysis_dict: dict[str, Any],
        predicted_edc_zip: bytes,
        pair_hash: str,
    ) -> dict[str, Any]:
        """
        Run the accuracy scorer and return results + XLSX bytes.
        Synchronous — call via run_in_executor.
        """
        import sys
        import os
        import tempfile

        # core_dir is the core/ directory — NOT the workers/ directory
        # where ingest_worker.py lives.
        workers_dir = os.path.dirname(os.path.abspath(__file__))
        core_dir    = os.path.join(os.path.dirname(workers_dir), "core")
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from generate_accuracy_report import generate_accuracy_report

        protocol = (analysis_dict.get("study_meta", {}).get("protocol_number")
                    or pair_hash)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "accuracy_report.xlsx")
            result   = generate_accuracy_report(
                actual_xml_bytes        = actual_xml.read_bytes(),
                actual_xls_bytes        = actual_xls.read_bytes(),
                predicted_spec_json     = analysis_dict,
                predicted_edc_zip_bytes = predicted_edc_zip,
                output_path             = out_path,
                study_name              = protocol,
            )
            result["xlsx_bytes"] = open(out_path, "rb").read()

        return result

    async def _write_back_metadata(
        self,
        item_id: int,
        fp: StudyFingerprint,
        pair_hash: str,
    ) -> None:
        """Final monday updates after a successful index."""
        await self.monday.set_long_text(
            item_id, "fingerprint", fingerprint_summary_text(fp)
        )
        await self.monday.set_text(
            item_id, "indexed_pair_hash", pair_hash
        )
        await self.monday.set_date(
            item_id, "index_date", date.today()
        )
        await self.monday.set_decision_needed(item_id, None)
        await self.monday.set_ingest_status(item_id, "indexed")
        await self.monday.set_trigger(item_id, "dont_send")

    # ── Failure paths ────────────────────────────────────────────

    async def _set_awaiting_human(
        self,
        item_id: int,
        decision_key: str,
        message: str,
    ) -> None:
        await self.monday.set_decision_needed(item_id, decision_key)
        await self.monday.set_long_text(item_id, "human_notes", message)
        await self.monday.set_ingest_status(item_id, "awaiting_human")

    async def _set_awaiting_build_completion(
        self,
        item_id: int,
        message: str,
    ) -> None:
        await self.monday.set_long_text(item_id, "human_notes", message)
        await self.monday.set_ingest_status(item_id, "awaiting_build_completion")

    async def _handle_failure(
        self,
        item_id: int,
        exc: BaseException,
    ) -> None:
        """Best-effort cleanup on unexpected failure."""
        try:
            await self.monday.set_ingest_status(item_id, "failed")
            await self.monday.set_long_text(
                item_id, "human_notes",
                f"Ingest failed: {type(exc).__name__}: {exc}"[:1000],
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "ingest.failure_handler_failed", item_id=item_id
            )


# ─── Internal control-flow exceptions ───────────────────────────


_DECISION_KEYS_AVAILABLE = frozenset({
    "supply_protocol",
    "supply_form_design",
    "investigate_ingest_failure",
})


@dataclass
class _MissingFiles(Exception):
    """One or both required files absent when trigger fired."""
    status_key: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class _AwaitingHuman(Exception):
    """Curator action needed. Not a real failure."""
    decision_key: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class _AwaitingBuildCompletion(Exception):
    """Auto-stubbed row, form not yet uploaded."""
    message: str

    def __str__(self) -> str:
        return self.message


# ─── JSON extraction helper ──────────────────────────────────────


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a free-text response."""
    import re

    s = text.strip()

    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass

    m = re.search(r"```json\s*(.+?)```", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    m = re.search(r"```\s*(\{.+?\})\s*```", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                snippet = s[start:i + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    pass
                start = -1

    return None


# ─── Module-level entry point used by the queue ─────────────────


async def process_job(job: IngestJob) -> None:
    """
    Module-level shim so workers/queue.py can import process_job.
    Wires up real dependencies via app.deps.
    """
    from app.deps import (
        get_embedder,
        get_fingerprint_extractor,
        get_monday_client,
        get_vector_store,
    )
    from core.protocol_analysis_client import run_protocol_analysis

    monday = get_monday_client()
    embedder = get_embedder()
    vector_store = get_vector_store()
    fingerprint_extractor = get_fingerprint_extractor()

    worker = IngestWorker(
        monday=monday,
        embedder=embedder,
        vector_store=vector_store,
        fingerprint_extractor=fingerprint_extractor,
        run_protocol_analysis=run_protocol_analysis,
    )
    await worker.process(job)
