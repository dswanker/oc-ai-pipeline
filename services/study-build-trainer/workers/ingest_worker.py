"""
Ingest worker — runs one ingest job to completion.

Phase 1 scope: protocol IS required. CT.gov fallback is deferred.
The flow is deterministic and has three terminal states:
  * indexed
  * awaiting_human (curator must supply something)
  * awaiting_build_completion (auto-stubbed by pipeline; no form yet)
  * failed (something blew up; curator should investigate)

Happy path (curator-supplied row, both halves present):

    1. Fetch row from monday.
    2. Validate inputs. If form_design missing AND protocol present
       → Awaiting Build Completion (this is the auto-stub path).
       If protocol missing entirely → Awaiting Human / Supply Protocol.
    3. Status → parsing_form.
    4. Cache all files (form_design + protocol + analysis JSON) into
       corpus/files/<pair_hash>/.
    5. Parse form_design → ParsedForm.
    6. Extract fingerprint from form (sponsor, indication, etc).
       Apply curator-supplied "Sponsor/Client" as override.
    7. Get protocol-analysis JSON. If curator uploaded one, use it.
       Otherwise call the protocol-analysis skill against the
       protocol PDF.
    8. Format the analysis JSON canonically; embed.
    9. Index in the vector store under pair_hash = "row_<item_id>".
   10. Write fingerprint summary, indexed_pair_hash, index_date back
       to monday. Set status=indexed, trigger=dont_send, clear
       decision_needed.

Each step is a private method so failures are localized. Any uncaught
exception in any step lands in `_handle_failure`, which sets monday
to a clearly-described failed state and writes a diagnostic message
to human_notes.

Test seam: ``IngestWorker`` accepts every collaborator (monday client,
parsers, fingerprint extractor, protocol-analysis client, embedder,
vector store) via its constructor. Production code uses
``app.deps.get_*`` dependency providers; tests inject fakes.
"""
from __future__ import annotations

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
    """Deterministic pair_hash from monday item_id.

    Phase 1 design choice: tie identity to the monday row, not the
    file content. Re-ingesting the same row overwrites cleanly. If we
    later need content-hash semantics (e.g. to detect "the form was
    re-uploaded with corrections"), we can extend this without
    churning the existing index keys.
    """
    return f"row_{item_id}"


def fingerprint_summary_text(fp: StudyFingerprint) -> str:
    """One-line summary of a StudyFingerprint, for the Fingerprint
    long-text column on monday."""
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
            # HUMAN_RESPONSE flow not used in Phase 1 (no CT.gov branch
            # that requires resumption). Curator just re-flips Trigger
            # on the row, which produces a fresh START job.
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
        except _AwaitingHuman as exc:
            # Expected outcome — curator action needed. Set the
            # appropriate monday state and END here. Job state is
            # AWAITING_HUMAN, not FAILED.
            job.state = IngestJobState.AWAITING_HUMAN
            await self._set_awaiting_human(item_id, exc.decision_key, exc.message)
            logger.info("ingest.awaiting_human", reason=exc.message, **log_ctx)
        except _AwaitingBuildCompletion as exc:
            # Auto-stub from pipeline. Form not yet uploaded by human.
            # Don't try to do anything else — wait.
            job.state = IngestJobState.AWAITING_HUMAN
            await self._set_awaiting_build_completion(item_id, exc.message)
            logger.info("ingest.awaiting_build_completion", **log_ctx)
        except Exception as exc:  # noqa: BLE001
            # Unexpected failure — surface clearly, don't crash the
            # worker so the queue can keep going.
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

        # 2. Validate inputs and decide branch
        self._validate_inputs(item)

        # 3. Status → parsing_form
        await self.monday.set_ingest_status(item_id, "parsing_form")

        # 4. Cache all files locally
        pair_hash = make_pair_hash(item_id)
        cached = await self.monday.cache_files_for_pair(item, pair_hash)

        if "form_design" not in cached:
            # Should be caught by validation, but defense-in-depth.
            raise RuntimeError("form_design failed to download")

        # 5. Parse form_design
        form_design_path = cached["form_design"]
        parsed = await self._parse_form(form_design_path)
        logger.info(
            "ingest.form_parsed",
            forms=len(parsed.forms),
            sponsor_in_form=parsed.sponsor,
            **log_ctx,
        )

        # 6. Extract fingerprint
        fp = await self._extract_fingerprint(parsed, item)
        logger.info(
            "ingest.fingerprint",
            sponsor=fp.sponsor, phase=fp.phase, indication=fp.indication,
            confidence=fp.extraction_confidence, **log_ctx,
        )

        # 7. Get the analysis JSON — curator-supplied or freshly generated
        analysis_dict = await self._get_analysis_json(cached, item, log_ctx)

        # 8. Embed
        query_vec = await self.embedder.embed_protocol_analysis(analysis_dict)
        logger.info("ingest.embedded", dim=len(query_vec), **log_ctx)

        # 9. Index
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

        # 10. Write back to monday
        await self._write_back_metadata(item_id, fp, pair_hash)

        job.state = IngestJobState.DONE
        logger.info("ingest.done", pair_hash=pair_hash, **log_ctx)

    # ── Sub-steps ────────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(item: CorpusItem) -> None:
        """
        Decide the branch based on which files are attached.

        Three valid happy entrypoints:
          * Both form_design AND (protocol OR analysis JSON) → ingest.
          * No form_design, but protocol present (auto-stub) → wait
            for human to upload form.
          * Anything else → curator action needed.
        """
        has_form = bool(item.files_by_column.get("form_design"))
        has_protocol = bool(item.files_by_column.get("protocol"))
        has_analysis = bool(item.files_by_column.get("protocol_analysis_json"))

        if not has_form and has_protocol:
            # Auto-stub from pipeline (or curator who's still working
            # on the form upload). Wait for them.
            raise _AwaitingBuildCompletion(
                "Form Design not yet uploaded; protocol is present."
            )

        if not has_form:
            raise _AwaitingHuman(
                decision_key="supply_form_design"
                    if "supply_form_design" in _DECISION_KEYS_AVAILABLE
                    else "supply_protocol",
                message="No Form Design attached.",
            )

        if not has_protocol and not has_analysis:
            raise _AwaitingHuman(
                decision_key="supply_protocol",
                message="No protocol or protocol-analysis JSON attached.",
            )

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
        """Run the fingerprint extractor.

        Curator-supplied "Sponsor/Client" is passed as a ground-truth
        override per the design. Any other curator overrides could be
        wired here (none today).
        """
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

        Two paths:
          * Curator (or pipeline auto-stub) attached the JSON → load
            from disk. This is the cheap path.
          * Only the protocol PDF is present → call the
            protocol-analysis skill on the PDF, parse the response.
            Cache the result for re-use.

        We never re-run the skill if a JSON is present. Single source
        of truth: the JSON is what gets indexed regardless of how it
        was produced.
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
                # Fall through to running it ourselves; the curator's
                # JSON was malformed.

        # No usable JSON. Run protocol-analysis on the protocol PDF.
        if "protocol" not in cached:
            raise RuntimeError(
                "Cannot produce analysis JSON: no protocol PDF cached."
            )
        pdf_bytes = cached["protocol"].read_bytes()
        logger.info("ingest.running_protocol_analysis", **log_ctx)
        response_text = await self.run_protocol_analysis(pdf_bytes)

        # The skill should return JSON-shaped text. Be defensive
        # about markdown fences and stray prose.
        analysis_dict = _extract_json_from_text(response_text)
        if not analysis_dict:
            raise RuntimeError(
                "Protocol analysis ran but returned no parseable JSON."
            )

        # Cache it so subsequent re-ingests skip the API call.
        cache_path = cached["protocol"].parent / "analysis.generated.json"
        cache_path.write_text(json.dumps(analysis_dict, indent=2))
        logger.info("ingest.analysis_cached", path=str(cache_path), **log_ctx)
        return analysis_dict

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
        """Mark the row as requiring curator action."""
        await self.monday.set_decision_needed(item_id, decision_key)
        await self.monday.set_long_text(item_id, "human_notes", message)
        await self.monday.set_ingest_status(item_id, "awaiting_human")
        # Trigger stays at "send_to_trainer" so curator can flip it
        # again after addressing whatever's missing.

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
            await self.monday.set_decision_needed(
                item_id, "investigate_ingest_failure"
            )
            await self.monday.set_long_text(
                item_id, "human_notes",
                f"Ingest failed: {type(exc).__name__}: {exc}"[:1000],
            )
        except Exception:  # noqa: BLE001
            # If we can't even talk to monday, don't crash the worker.
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
    """
    Pull the first JSON object out of a free-text response.

    Handles:
      * Pure JSON ({"foo": "bar"})
      * JSON wrapped in ```json ... ``` fences
      * JSON wrapped in bare ``` ... ``` fences
      * JSON preceded or followed by free-text prose
    """
    import re

    s = text.strip()

    # 1) Try whole-string parse
    if s.startswith("{"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass

    # 2) Try ```json ... ``` fence
    m = re.search(r"```json\s*(.+?)```", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) Try bare ``` ... ``` fence containing an object
    m = re.search(r"```\s*(\{.+?\})\s*```", s, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 4) Try the first balanced {...} block in the text
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
    Module-level shim so workers/queue.py can `from workers.ingest_worker
    import process_job` — matching the existing import. Wires up
    real dependencies via app.deps.
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
