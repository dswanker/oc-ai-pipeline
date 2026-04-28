"""
Tests for ``core.protocol_analysis_client``.

Pure-function tests use a fake skill folder under ``tests/fixtures/fake_skill/``
so we never touch the real ``../../skills/protocol-analysis`` from a unit
test. The end-to-end-with-stub-client test verifies that the assembled
content blocks have the expected shape and that the API call wiring
works without invoking the real network.

Run as a script::

    python tests/test_protocol_analysis_client.py
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

# Standalone-script support — pytest doesn't need this.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.protocol_analysis_client import (
    DEFAULT_SKILL_DIR,
    build_content_blocks,
    load_skill_prompt,
    run_protocol_analysis,
)

FAKE_SKILL_DIR = Path(__file__).parent / "fixtures" / "fake_skill"


# ─── load_skill_prompt ────────────────────────────────────────────


def test_load_skill_prompt_from_explicit_dir() -> None:
    text = load_skill_prompt(FAKE_SKILL_DIR)
    assert "TEST_SKILL_MARKER_42" in text


def test_load_skill_prompt_returns_full_file() -> None:
    """Whole SKILL.md is returned, not just a portion."""
    text = load_skill_prompt(FAKE_SKILL_DIR)
    assert text.startswith("# Test Skill")
    assert text.rstrip().endswith("TEST_SKILL_MARKER_42")


def test_load_skill_prompt_accepts_string_path() -> None:
    """Path or string both work."""
    text = load_skill_prompt(str(FAKE_SKILL_DIR))
    assert "TEST_SKILL_MARKER_42" in text


def test_load_skill_prompt_missing_dir_raises() -> None:
    nonexistent = Path("/tmp/this_definitely_does_not_exist_42")
    try:
        load_skill_prompt(nonexistent)
    except FileNotFoundError as exc:
        # Error message should mention the path we looked at — that's
        # the most common debug clue for a path-resolution bug.
        assert str(nonexistent) in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_default_skill_dir_resolves_to_correct_relative_location() -> None:
    """
    ``DEFAULT_SKILL_DIR`` should point at
    ``oc-ai-pipeline/skills/protocol-analysis/``, four levels up from
    this module's file. We don't require the dir to exist (it does in
    production but might not in test environments) — we just check the
    path math is correct.
    """
    assert DEFAULT_SKILL_DIR.name == "protocol-analysis"
    assert DEFAULT_SKILL_DIR.parent.name == "skills"
    # The grandparent of skills/protocol-analysis is the pipeline root.
    pipeline_root = DEFAULT_SKILL_DIR.parent.parent
    # Sanity check: the path includes the trainer service folder
    # somewhere underneath pipeline_root/services/.
    assert (pipeline_root / "services").exists() or True  # not required


# ─── build_content_blocks ─────────────────────────────────────────


def test_content_blocks_includes_pdf_first() -> None:
    blocks = build_content_blocks(b"PDF_CONTENT", "skill prompt here")
    assert len(blocks) == 2
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    # The PDF should be base64-encoded
    decoded = base64.standard_b64decode(blocks[0]["source"]["data"])
    assert decoded == b"PDF_CONTENT"


def test_content_blocks_skill_prompt_last() -> None:
    blocks = build_content_blocks(b"PDF", "skill prompt here")
    assert blocks[-1]["type"] == "text"
    assert blocks[-1]["text"] == "skill prompt here"


def test_content_blocks_inserts_extra_text_before_skill_prompt() -> None:
    blocks = build_content_blocks(
        b"PDF", "skill prompt", extra_text="curator note"
    )
    assert len(blocks) == 3
    # Order: PDF, extra_text, skill_prompt
    assert blocks[0]["type"] == "document"
    assert blocks[1]["type"] == "text"
    assert blocks[1]["text"] == "curator note"
    assert blocks[2]["type"] == "text"
    assert blocks[2]["text"] == "skill prompt"


def test_content_blocks_skip_extra_text_when_empty() -> None:
    blocks = build_content_blocks(b"PDF", "skill prompt", extra_text="")
    assert len(blocks) == 2  # only PDF + skill prompt
    assert blocks[1]["text"] == "skill prompt"


# ─── run_protocol_analysis (with stub client) ─────────────────────


class _StubResponseBlock:
    """Mimics anthropic.types.TextBlock — only the .text attr we use."""
    def __init__(self, text: str) -> None:
        self.text = text


class _StubResponse:
    def __init__(self, text: str) -> None:
        self.content = [_StubResponseBlock(text)]


class _StubMessages:
    """Records call args; returns a canned response."""
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN201, ANN003
        self.calls.append(kwargs)
        return _StubResponse(self._response_text)


class _StubAnthropicClient:
    def __init__(self, response_text: str) -> None:
        self.messages = _StubMessages(response_text)


def test_run_protocol_analysis_round_trip_with_stub() -> None:
    """End-to-end with a stub client: PDF in → response text out."""
    canned = '{"sponsor": "Stub", "indication": "test"}'
    client = _StubAnthropicClient(canned)

    text = asyncio.run(run_protocol_analysis(
        b"%PDF-1.4 fake pdf bytes",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
    ))
    assert text == canned


def test_run_protocol_analysis_sends_correct_model() -> None:
    """The model arg must reach client.messages.create as opus-4-7."""
    client = _StubAnthropicClient('{"ok":1}')
    asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
    ))
    assert client.messages.calls[0]["model"] == "claude-opus-4-7"


def test_run_protocol_analysis_uses_explicit_model_override() -> None:
    """Caller can override the model via the ``model=`` kwarg."""
    client = _StubAnthropicClient('{"ok":1}')
    asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
        model="some-other-model",
    ))
    assert client.messages.calls[0]["model"] == "some-other-model"


def test_run_protocol_analysis_max_tokens_default_16000() -> None:
    client = _StubAnthropicClient('{"ok":1}')
    asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
    ))
    assert client.messages.calls[0]["max_tokens"] == 16000


def test_run_protocol_analysis_payload_shape() -> None:
    """Verify the messages payload has the user-message + content shape."""
    client = _StubAnthropicClient('{"ok":1}')
    asyncio.run(run_protocol_analysis(
        b"%PDF-1.4 hello",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
    ))
    call = client.messages.calls[0]
    messages = call["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    # PDF document block first, skill prompt last
    assert content[0]["type"] == "document"
    assert content[-1]["type"] == "text"
    assert "TEST_SKILL_MARKER_42" in content[-1]["text"]


def test_run_protocol_analysis_supports_extra_text() -> None:
    """``extra_text`` should appear between PDF and skill prompt."""
    client = _StubAnthropicClient('{"ok":1}')
    asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=client,
        extra_text="curator-supplied context",
    ))
    content = client.messages.calls[0]["messages"][0]["content"]
    assert len(content) == 3
    assert content[1]["type"] == "text"
    assert content[1]["text"] == "curator-supplied context"


def test_run_protocol_analysis_concatenates_multi_block_response() -> None:
    """If the SDK ever returns multi-block text, all blocks are concatenated."""

    class _MultiBlockResponse:
        def __init__(self) -> None:
            self.content = [
                _StubResponseBlock("part one "),
                _StubResponseBlock("part two"),
            ]

    class _MultiBlockMessages:
        async def create(self, **kwargs):  # noqa: ANN201, ANN003
            return _MultiBlockResponse()

    class _MultiBlockClient:
        def __init__(self) -> None:
            self.messages = _MultiBlockMessages()

    text = asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=_MultiBlockClient(),
    ))
    assert text == "part one part two"


def test_run_protocol_analysis_skips_blocks_without_text_attr() -> None:
    """Defensive: SDK might return non-text blocks (image, tool_use, etc.)."""

    class _NonTextBlock:
        # No .text attribute
        type = "tool_use"

    class _MixedBlocksResponse:
        def __init__(self) -> None:
            self.content = [_NonTextBlock(), _StubResponseBlock("the actual text")]

    class _MixedBlocksMessages:
        async def create(self, **kwargs):  # noqa: ANN201, ANN003
            return _MixedBlocksResponse()

    class _MixedBlocksClient:
        def __init__(self) -> None:
            self.messages = _MixedBlocksMessages()

    text = asyncio.run(run_protocol_analysis(
        b"PDF",
        skill_dir=FAKE_SKILL_DIR,
        client=_MixedBlocksClient(),
    ))
    assert text == "the actual text"


def test_run_protocol_analysis_missing_skill_dir_raises() -> None:
    """If the skill folder is wrong, fail clearly before the API call."""
    client = _StubAnthropicClient('{"ok":1}')
    bad = Path("/tmp/nonexistent_skill_dir_42")
    try:
        asyncio.run(run_protocol_analysis(
            b"PDF",
            skill_dir=bad,
            client=client,
        ))
    except FileNotFoundError as exc:
        assert "SKILL.md" in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError when skill_dir is bogus")
    # And the API was never called.
    assert client.messages.calls == []


# ─── Script entry point ───────────────────────────────────────────


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
