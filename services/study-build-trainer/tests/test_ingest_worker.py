"""
Tests for ``workers.ingest_worker``.

The whole pipeline is testable with fakes because the worker takes
every collaborator as a constructor arg. The fakes capture inputs and
return canned outputs so we can verify both control flow (which steps
ran in which order) and final state (what monday columns got written,
what got indexed).

Run as a script::

    python tests/test_ingest_worker.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Standalone-script support
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.fingerprint import StudyFingerprint
from core.form_parser.base import FormDef, FormFormat, FormGroup, FormItem, ParsedForm
from core.monday_client import CorpusItem
from core.vector_store import IndexInput, RetrievedPair
from workers.ingest_worker import (
    IngestWorker,
    _extract_json_from_text,
    fingerprint_summary_text,
    make_pair_hash,
)
from workers.queue import IngestJob, IngestJobKind, IngestJobState


# ─── Pure helpers ────────────────────────────────────────────────


def test_make_pair_hash_format() -> None:
    assert make_pair_hash(42) == "row_42"
    assert make_pair_hash(18394025687) == "row_18394025687"


def test_make_pair_hash_is_deterministic() -> None:
    """Same item_id always produces the same hash."""
    assert make_pair_hash(123) == make_pair_hash(123)


def test_fingerprint_summary_includes_all_fields() -> None:
    fp = StudyFingerprint(
        sponsor="Candel Therapeutics, Inc.",
        intervention=["aglatimagene besadenovec", "valacyclovir"],
        indication="prostate cancer",
        phase="2",
        therapeutic_area="oncology",
        extraction_confidence=0.97,
    )
    s = fingerprint_summary_text(fp)
    assert "Candel" in s
    assert "prostate cancer" in s
    assert "phase: 2" in s
    assert "oncology" in s
    assert "0.97" in s


def test_fingerprint_summary_omits_empty_fields() -> None:
    """Don't render empty fields."""
    fp = StudyFingerprint(sponsor="Acme", phase="1")
    s = fingerprint_summary_text(fp)
    assert "sponsor: Acme" in s
    assert "phase: 1" in s
    # No indication or therapeutic_area should appear
    assert "indication:" not in s
    assert "therapeutic_area:" not in s


def test_fingerprint_summary_handles_notes() -> None:
    fp = StudyFingerprint(sponsor="X", notes="derived from EX form")
    s = fingerprint_summary_text(fp)
    assert "notes: derived from EX form" in s


# ─── _extract_json_from_text ────────────────────────────────────


def test_extract_json_from_pure_json() -> None:
    out = _extract_json_from_text('{"foo": "bar"}')
    assert out == {"foo": "bar"}


def test_extract_json_from_markdown_fence() -> None:
    out = _extract_json_from_text('Some prose\n```json\n{"k": 1}\n```\nMore prose')
    assert out == {"k": 1}


def test_extract_json_from_bare_fence() -> None:
    out = _extract_json_from_text('```\n{"k": 1}\n```')
    assert out == {"k": 1}


def test_extract_json_from_text_with_prose_around() -> None:
    out = _extract_json_from_text(
        'Here is the result: {"sponsor": "Acme"} hope that helps.'
    )
    assert out == {"sponsor": "Acme"}


def test_extract_json_returns_none_for_garbage() -> None:
    assert _extract_json_from_text("totally not JSON") is None


def test_extract_json_handles_nested_objects() -> None:
    payload = '{"a": {"b": {"c": 1}}}'
    out = _extract_json_from_text(payload)
    assert out == {"a": {"b": {"c": 1}}}


# ─── Fakes ───────────────────────────────────────────────────────


class _FakeMondayClient:
    """Captures all monday method calls; returns canned data for reads."""

    def __init__(
        self,
        *,
        canned_item: CorpusItem,
        cached_files: dict[str, Path],
    ) -> None:
        self._canned_item = canned_item
        self._cached_files = cached_files
        self.calls: list[tuple[str, dict]] = []

    async def get_item(self, item_id: int) -> CorpusItem:
        self.calls.append(("get_item", {"item_id": item_id}))
        return self._canned_item

    async def cache_files_for_pair(
        self, item: CorpusItem, pair_hash: str, **kwargs
    ) -> dict[str, Path]:
        self.calls.append((
            "cache_files_for_pair",
            {"item_id": item.item_id, "pair_hash": pair_hash},
        ))
        return self._cached_files

    async def set_ingest_status(self, item_id: int, status_key: str) -> None:
        self.calls.append((
            "set_ingest_status",
            {"item_id": item_id, "status_key": status_key},
        ))

    async def set_trigger(self, item_id: int, trigger_key: str) -> None:
        self.calls.append((
            "set_trigger",
            {"item_id": item_id, "trigger_key": trigger_key},
        ))

    async def set_decision_needed(
        self, item_id: int, decision_key: str | None
    ) -> None:
        self.calls.append((
            "set_decision_needed",
            {"item_id": item_id, "decision_key": decision_key},
        ))

    async def set_long_text(
        self, item_id: int, col_key: str, text: str
    ) -> None:
        self.calls.append((
            "set_long_text",
            {"item_id": item_id, "col_key": col_key, "text": text},
        ))

    async def set_text(self, item_id: int, col_key: str, text: str) -> None:
        self.calls.append((
            "set_text",
            {"item_id": item_id, "col_key": col_key, "text": text},
        ))

    async def set_date(self, item_id: int, col_key: str, when) -> None:
        self.calls.append((
            "set_date",
            {"item_id": item_id, "col_key": col_key, "date": when},
        ))

    def call_kinds(self) -> list[str]:
        """Helper: just the names of methods called, in order."""
        return [name for name, _ in self.calls]

    def find_calls(self, name: str) -> list[dict]:
        return [args for n, args in self.calls if n == name]


class _FakeParser:
    """Returns a canned ParsedForm regardless of input."""

    def __init__(self, parsed: ParsedForm) -> None:
        self._parsed = parsed

    async def parse(self, data: bytes, *, filename: str | None = None) -> ParsedForm:
        return self._parsed


def _fake_parser_for(parser_obj: _FakeParser):
    """Mimics core.form_parser.parser_for — always returns our fake."""
    def factory(filename: str):
        return parser_obj
    return factory


class _FakeFingerprintExtractor:
    """Returns a canned StudyFingerprint; records overrides."""

    def __init__(self, fingerprint: StudyFingerprint) -> None:
        self._fingerprint = fingerprint
        self.last_overrides: dict | None = None
        self.call_count = 0

    async def extract(
        self,
        parsed: ParsedForm,
        *,
        overrides: dict[str, Any] | None = None,
    ) -> StudyFingerprint:
        self.last_overrides = overrides
        self.call_count += 1
        return self._fingerprint


class _FakeEmbedder:
    """Returns a deterministic vector based on content hash."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.embed_calls: list[Any] = []

    async def embed_protocol_analysis(self, analysis) -> list[float]:
        self.embed_calls.append(analysis)
        # Deterministic toy vector — content matters for any code that
        # cares about uniqueness, but doesn't need real embedding.
        seed = hash(json.dumps(analysis, sort_keys=True)) % 1_000_000
        return [(seed + i) % 7 / 7.0 for i in range(self.dim)]


class _FakeVectorStore:
    """Records IndexInput payloads."""

    def __init__(self) -> None:
        self.added: list[IndexInput] = []

    async def add(self, item: IndexInput) -> None:
        self.added.append(item)

    async def query(self, query_vec, k=10, filters=None):
        return []


@dataclass
class _ProtocolAnalysisRunner:
    """Captures the bytes it was called with; returns a canned response."""
    response_text: str = ""
    calls: list[bytes] = field(default_factory=list)

    async def __call__(self, pdf_bytes: bytes) -> str:
        self.calls.append(pdf_bytes)
        return self.response_text


# ─── Helpers to build CorpusItem / ParsedForm fixtures ─────────


def _make_parsed_form() -> ParsedForm:
    """Minimal ParsedForm that exercises the parser → fingerprint path."""
    items = [FormItem(oid="I_AGE", name="AGE", label="Age", data_type="integer")]
    group = FormGroup(oid="IG_DM", name="Demographics", items=items)
    forms = [FormDef(oid="F_DM", name="Demographics", title="Demographics", groups=[group])]
    return ParsedForm(
        source_format=FormFormat.ODM_XML,
        study_oid="S_TEST",
        study_name="Test Study",
        sponsor="From XML",
        forms=forms,
    )


def _make_corpus_item(
    *,
    item_id: int = 1234,
    has_form: bool = True,
    has_protocol: bool = True,
    has_analysis: bool = False,
    sponsor_client: str | None = None,
) -> CorpusItem:
    files: dict[str, list[dict[str, str]]] = {}
    if has_form:
        files["form_design"] = [{"asset_id": "f1", "name": "study.xml"}]
    if has_protocol:
        files["protocol"] = [{"asset_id": "p1", "name": "protocol.pdf"}]
    if has_analysis:
        files["protocol_analysis_json"] = [{"asset_id": "a1", "name": "analysis.json"}]
    return CorpusItem(
        item_id=item_id,
        name="Test Row",
        sponsor_client=sponsor_client,
        files_by_column=files,
    )


def _make_cached_files(
    tmpdir: Path,
    *,
    include_form: bool = True,
    include_protocol: bool = True,
    include_analysis: bool = False,
    analysis_content: dict | None = None,
    analysis_text: str | None = None,
) -> dict[str, Path]:
    """Materialize fake files on disk so the worker can read them."""
    out: dict[str, Path] = {}
    if include_form:
        f = tmpdir / "study.xml"
        f.write_text("<odm/>")
        out["form_design"] = f
    if include_protocol:
        p = tmpdir / "protocol.pdf"
        p.write_bytes(b"%PDF-1.4 fake pdf bytes")
        out["protocol"] = p
    if include_analysis:
        a = tmpdir / "analysis.json"
        if analysis_text is not None:
            a.write_text(analysis_text)
        else:
            a.write_text(json.dumps(analysis_content or {"sponsor": "Acme"}))
        out["protocol_analysis_json"] = a
    return out


def _make_worker(
    *,
    item: CorpusItem,
    cached_files: dict[str, Path],
    fingerprint: StudyFingerprint | None = None,
    analysis_response: str = '{"sponsor": "Skill Sponsor", "phase": "3"}',
) -> tuple[IngestWorker, _FakeMondayClient, _FakeFingerprintExtractor,
           _FakeEmbedder, _FakeVectorStore, _ProtocolAnalysisRunner]:
    monday = _FakeMondayClient(canned_item=item, cached_files=cached_files)
    parser = _FakeParser(_make_parsed_form())
    fp = fingerprint or StudyFingerprint(
        sponsor="Test Sponsor", indication="prostate cancer", phase="2",
        therapeutic_area="oncology", extraction_confidence=0.95,
    )
    extractor = _FakeFingerprintExtractor(fp)
    embedder = _FakeEmbedder(dim=4)
    store = _FakeVectorStore()
    runner = _ProtocolAnalysisRunner(response_text=analysis_response)
    worker = IngestWorker(
        monday=monday,
        embedder=embedder,
        vector_store=store,
        fingerprint_extractor=extractor,
        run_protocol_analysis=runner,
        parser_for_fn=_fake_parser_for(parser),
    )
    return worker, monday, extractor, embedder, store, runner


# ─── Happy paths ────────────────────────────────────────────────


def test_happy_path_with_curator_supplied_analysis_json() -> None:
    """All three files present; analysis JSON used directly."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        cached = _make_cached_files(
            tmpdir, include_analysis=True,
            analysis_content={"sponsor": "Curator Sponsor", "phase": "2"},
        )
        worker, monday, extractor, embedder, store, runner = _make_worker(
            item=item, cached_files=cached,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)

        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.DONE
        # The skill should NOT have been called because the JSON was provided
        assert runner.calls == []
        # One item should have been indexed
        assert len(store.added) == 1
        indexed = store.added[0]
        assert indexed.pair_hash == "row_1234"
        assert indexed.has_protocol is True
        # The embedded content should be the curator-supplied analysis
        assert embedder.embed_calls[0] == {
            "sponsor": "Curator Sponsor", "phase": "2"
        }


def test_happy_path_runs_protocol_analysis_when_no_json() -> None:
    """When only protocol PDF is present, the skill is invoked."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=False)
        cached = _make_cached_files(tmpdir, include_analysis=False)
        worker, monday, extractor, embedder, store, runner = _make_worker(
            item=item, cached_files=cached,
            analysis_response='{"sponsor": "Skill Sponsor", "phase": "3"}',
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)

        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.DONE
        # The skill was called exactly once with the PDF bytes
        assert len(runner.calls) == 1
        assert runner.calls[0].startswith(b"%PDF-")
        # The analysis JSON we embedded came from the skill response
        assert embedder.embed_calls[0] == {
            "sponsor": "Skill Sponsor", "phase": "3"
        }
        # And the generated analysis was cached to disk
        assert (cached["protocol"].parent / "analysis.generated.json").exists()


def test_happy_path_writes_back_indexed_metadata() -> None:
    """After indexing, monday gets fingerprint, hash, date, status, trigger."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        cached = _make_cached_files(tmpdir, include_analysis=True)
        fp = StudyFingerprint(
            sponsor="X Co", indication="cancer", phase="2",
            therapeutic_area="oncology", extraction_confidence=0.9,
        )
        worker, monday, _, _, _, _ = _make_worker(
            item=item, cached_files=cached, fingerprint=fp,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        # Check that we set fingerprint long-text, indexed_pair_hash, index_date,
        # ingest_status=indexed, trigger=dont_send, decision_needed=None
        kinds = monday.call_kinds()
        # Final phase calls (after the indexing) should include each
        assert "set_long_text" in kinds
        assert "set_text" in kinds  # for indexed_pair_hash
        assert "set_date" in kinds

        # Content of the writes
        long_text_calls = monday.find_calls("set_long_text")
        fp_calls = [c for c in long_text_calls if c["col_key"] == "fingerprint"]
        assert len(fp_calls) == 1
        assert "X Co" in fp_calls[0]["text"]
        assert "cancer" in fp_calls[0]["text"]

        text_calls = monday.find_calls("set_text")
        hash_calls = [c for c in text_calls if c["col_key"] == "indexed_pair_hash"]
        assert hash_calls[0]["text"] == "row_1234"

        # set_decision_needed should be called with None at the end
        dn_calls = monday.find_calls("set_decision_needed")
        assert dn_calls[-1]["decision_key"] is None

        # Final ingest_status should be 'indexed'
        status_calls = monday.find_calls("set_ingest_status")
        assert status_calls[-1]["status_key"] == "indexed"

        # Final trigger should be 'dont_send'
        trigger_calls = monday.find_calls("set_trigger")
        assert trigger_calls[-1]["trigger_key"] == "dont_send"


def test_curator_sponsor_override_is_passed_to_extractor() -> None:
    """The Sponsor/Client text column should flow through as override."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(
            has_analysis=True,
            sponsor_client="Candel Therapeutics, Inc.",
        )
        cached = _make_cached_files(tmpdir, include_analysis=True)
        worker, _, extractor, _, _, _ = _make_worker(
            item=item, cached_files=cached,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert extractor.last_overrides == {
            "sponsor": "Candel Therapeutics, Inc."
        }


def test_no_curator_sponsor_means_no_overrides() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True, sponsor_client=None)
        cached = _make_cached_files(tmpdir, include_analysis=True)
        worker, _, extractor, _, _, _ = _make_worker(
            item=item, cached_files=cached,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert extractor.last_overrides is None


def test_indexed_pair_carries_metadata_to_vector_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        cached = _make_cached_files(tmpdir, include_analysis=True)
        fp = StudyFingerprint(
            sponsor="X Co", indication="cancer", phase="2",
            therapeutic_area="oncology", extraction_confidence=0.9,
        )
        worker, _, _, _, store, _ = _make_worker(
            item=item, cached_files=cached, fingerprint=fp,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=42)
        asyncio.run(worker.process(job))

        idx = store.added[0]
        assert idx.pair_hash == "row_42"
        assert idx.monday_item_id == 42
        assert idx.sponsor == "X Co"
        assert idx.indication == "cancer"
        assert idx.phase == "2"
        assert idx.therapeutic_area == "oncology"
        assert idx.has_protocol is True
        assert idx.form_design_path == str(cached["form_design"])


# ─── Awaiting paths ─────────────────────────────────────────────


def test_awaiting_build_completion_when_protocol_only() -> None:
    """Auto-stub from pipeline: only protocol attached, no form yet."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        # NOTE: validation runs BEFORE files are downloaded, so the
        # cached_files arg here is irrelevant — we just need the item
        # to indicate no form_design but a protocol.
        item = _make_corpus_item(has_form=False, has_protocol=True)
        worker, monday, _, _, store, _ = _make_worker(
            item=item, cached_files={},
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.AWAITING_HUMAN
        # Nothing got indexed
        assert store.added == []
        # ingest_status should be set to awaiting_build_completion
        status_calls = monday.find_calls("set_ingest_status")
        assert any(c["status_key"] == "awaiting_build_completion"
                   for c in status_calls)


def test_awaiting_human_when_no_form_no_protocol() -> None:
    """Empty row — nothing to do."""
    with tempfile.TemporaryDirectory() as tmp:
        item = _make_corpus_item(has_form=False, has_protocol=False)
        worker, monday, _, _, store, _ = _make_worker(
            item=item, cached_files={},
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.AWAITING_HUMAN
        assert store.added == []
        status_calls = monday.find_calls("set_ingest_status")
        assert status_calls[-1]["status_key"] == "awaiting_human"
        dn_calls = monday.find_calls("set_decision_needed")
        # Should be supply_protocol (or supply_form_design if available)
        last_decision = dn_calls[-1]["decision_key"]
        assert last_decision in ("supply_protocol", "supply_form_design")


def test_awaiting_human_when_form_but_no_protocol() -> None:
    """Form present, protocol/JSON missing."""
    with tempfile.TemporaryDirectory() as tmp:
        item = _make_corpus_item(has_form=True, has_protocol=False, has_analysis=False)
        worker, monday, _, _, store, _ = _make_worker(
            item=item, cached_files={},
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.AWAITING_HUMAN
        assert store.added == []
        dn_calls = monday.find_calls("set_decision_needed")
        assert dn_calls[-1]["decision_key"] == "supply_protocol"


# ─── Failure path ───────────────────────────────────────────────


def test_failure_in_skill_call_marks_failed_and_writes_diagnostic() -> None:
    """When the skill raises, the job is marked failed and monday is updated."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=False)
        cached = _make_cached_files(tmpdir, include_analysis=False)

        class _Boom:
            calls: list = []

            async def __call__(self, pdf_bytes: bytes) -> str:
                self.calls.append(pdf_bytes)
                raise RuntimeError("Anthropic API exploded")

        runner = _Boom()

        # Manually wire a worker with the boom runner.
        monday = _FakeMondayClient(canned_item=item, cached_files=cached)
        parser = _FakeParser(_make_parsed_form())
        extractor = _FakeFingerprintExtractor(StudyFingerprint(sponsor="X"))
        embedder = _FakeEmbedder()
        store = _FakeVectorStore()
        worker = IngestWorker(
            monday=monday,
            embedder=embedder,
            vector_store=store,
            fingerprint_extractor=extractor,
            run_protocol_analysis=runner,
            parser_for_fn=_fake_parser_for(parser),
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.FAILED
        assert "Anthropic API exploded" in (job.error or "")
        # Nothing got indexed
        assert store.added == []
        # Monday should show 'failed' status and 'investigate' decision
        status_calls = monday.find_calls("set_ingest_status")
        assert status_calls[-1]["status_key"] == "failed"
        dn_calls = monday.find_calls("set_decision_needed")
        assert dn_calls[-1]["decision_key"] == "investigate_ingest_failure"


def test_invalid_analysis_json_falls_through_to_running_skill() -> None:
    """If the curator-uploaded JSON is malformed, run the skill instead."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        # Write an invalid-JSON file
        cached = _make_cached_files(
            tmpdir, include_analysis=True,
            analysis_text="this is not json {{{",
        )
        worker, _, _, _, store, runner = _make_worker(
            item=item, cached_files=cached,
            analysis_response='{"sponsor": "Skill Did It", "phase": "1"}',
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        assert job.state == IngestJobState.DONE
        # Skill ran because the JSON was malformed
        assert len(runner.calls) == 1
        assert store.added[0].pair_hash == "row_1234"


def test_unexpected_job_kind_raises() -> None:
    """HUMAN_RESPONSE jobs aren't supported in Phase 1.

    The worker raises ValueError directly. The queue wrapper
    (workers/queue.py:_run) catches that and marks the job FAILED
    — that's tested separately at the queue level, if we ever add
    queue-level tests. Here we just verify the worker raises.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        cached = _make_cached_files(tmpdir, include_analysis=True)
        worker, _, _, _, _, _ = _make_worker(
            item=item, cached_files=cached,
        )
        job = IngestJob(kind=IngestJobKind.HUMAN_RESPONSE, monday_item_id=1234)
        try:
            asyncio.run(worker.process(job))
        except ValueError as exc:
            assert "START" in str(exc)
        else:
            raise AssertionError("Expected ValueError on non-START job")


# ─── Status transitions ─────────────────────────────────────────


def test_status_progresses_through_parsing_form_to_indexed() -> None:
    """The happy path should set parsing_form before indexed."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        item = _make_corpus_item(has_analysis=True)
        cached = _make_cached_files(tmpdir, include_analysis=True)
        worker, monday, _, _, _, _ = _make_worker(
            item=item, cached_files=cached,
        )
        job = IngestJob(kind=IngestJobKind.START, monday_item_id=1234)
        asyncio.run(worker.process(job))

        statuses = [
            c["status_key"]
            for c in monday.find_calls("set_ingest_status")
        ]
        # parsing_form must come before indexed
        assert statuses.index("parsing_form") < statuses.index("indexed")


# ─── Script runner ──────────────────────────────────────────────


if __name__ == "__main__":
    import traceback

    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception:  # noqa: BLE001
            failed.append((t.__name__, traceback.format_exc()))
            print(f"  FAIL  {t.__name__}")

    print()
    print(f"Ran {len(tests)} tests, {len(failed)} failures.")
    for name, tb in failed:
        print()
        print(f"── {name} ──")
        print(tb)
    sys.exit(1 if failed else 0)
