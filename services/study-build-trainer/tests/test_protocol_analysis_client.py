"""
Tests for core.protocol_analysis_client (current API: inline ANALYSIS_PROMPT, single-turn call, retry on rate limits).

History: this file originally tested a skill-folder loader (load_skill_prompt / DEFAULT_SKILL_DIR /
build_content_blocks / extra_text). The client was rewritten around an inline prompt (see the 2026-05-06 reverts of
Patches 14-14.2), so those tests were retired on 2026-10-08 and the behavioral ones rewritten against the current API.
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.protocol_analysis_client import ANALYSIS_PROMPT, run_protocol_analysis


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Resp:
    def __init__(self, *blocks) -> None:
        self.content = list(blocks)


class _Messages:
    """Records call kwargs; returns queued responses or raises queued exceptions."""
    def __init__(self, *outcomes) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):  # noqa: ANN201, ANN003
        self.calls.append(kwargs)
        out = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(out, Exception):
            raise out
        return out


class _Client:
    def __init__(self, *outcomes) -> None:
        self.messages = _Messages(*outcomes)


def _run(client, **kw):
    return asyncio.run(run_protocol_analysis(b"%PDF-1.4 fake", client=client, **kw))


def _rate_limit():
    from anthropic import RateLimitError
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)


def test_round_trip_returns_response_text() -> None:
    canned = '{"sponsor": "Stub", "indication": "test"}'
    assert _run(_Client(_Resp(_Block(canned)))) == canned


def test_default_model_and_max_tokens() -> None:
    client = _Client(_Resp(_Block("{}")))
    _run(client)
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-4-7" and call["max_tokens"] == 16000


def test_model_override() -> None:
    client = _Client(_Resp(_Block("{}")))
    _run(client, model="some-other-model")
    assert client.messages.calls[0]["model"] == "some-other-model"


def test_payload_is_pdf_document_then_inline_prompt() -> None:
    client = _Client(_Resp(_Block("{}")))
    _run(client)
    msgs = client.messages.calls[0]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert len(content) == 2
    assert content[0]["type"] == "document"
    assert base64.standard_b64decode(content[0]["source"]["data"]) == b"%PDF-1.4 fake"
    assert content[1] == {"type": "text", "text": ANALYSIS_PROMPT}


def test_no_temperature_sent() -> None:
    """Opus 4.7 rejects any temperature (incl. 0) with a 400; the client must never send it."""
    client = _Client(_Resp(_Block("{}")))
    _run(client)
    assert "temperature" not in client.messages.calls[0]


def test_multi_block_response_concatenated() -> None:
    assert _run(_Client(_Resp(_Block("part one "), _Block("part two")))) == "part one part two"


def test_blocks_without_text_are_skipped() -> None:
    class _ToolUse:
        type = "tool_use"
    assert _run(_Client(_Resp(_ToolUse(), _Block("the actual text")))) == "the actual text"


def test_rate_limit_is_retried_then_succeeds() -> None:
    client = _Client(_rate_limit(), _Resp(_Block('{"ok": 1}')))
    assert _run(client, initial_wait_seconds=0) == '{"ok": 1}'
    assert len(client.messages.calls) == 2


def test_rate_limit_exhausting_retries_raises() -> None:
    from anthropic import RateLimitError
    client = _Client(_rate_limit())
    with pytest.raises(RateLimitError):
        _run(client, initial_wait_seconds=0, max_retries=2)
    assert len(client.messages.calls) == 2


def test_other_errors_are_not_retried() -> None:
    client = _Client(ValueError("bad request"))
    with pytest.raises(ValueError):
        _run(client, initial_wait_seconds=0)
    assert len(client.messages.calls) == 1
