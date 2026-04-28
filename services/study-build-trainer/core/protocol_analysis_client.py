"""
Trainer's wrapper around the protocol-analysis skill.

This is the trainer's counterpart to oc-ai-pipeline's ``claude_client.py``
``run_skill()`` function. Both services invoke the protocol-analysis
skill the same way, by loading the same skill files from disk and
sending them as a prompt alongside the protocol PDF.

Architectural intent (single source of truth):
  * The protocol-analysis logic lives in ``skills/protocol-analysis/``.
    SKILL.md plus its reference materials. **The skill is the source
    of truth.**
  * Both the pipeline and the trainer call into that same skill folder.
  * When the skill needs improvement, you fix it in one place and both
    consumers benefit on their next call.
  * The wrapper code (this module here, ``claude_client.py`` over
    there) is plumbing — small, structurally similar, kept in sync by
    convention.

When the trainer is eventually extracted to its own repo, the right
move is to migrate both consumers to the Anthropic Skills API in one
sweep. ``register_skills.py`` is already groundwork for that. Until
that migration, on-disk skill files work fine.

Module structure:

* ``DEFAULT_SKILL_DIR`` — points at ``../../skills/protocol-analysis``
  relative to this file. Override with ``skill_dir=`` for tests.
* ``load_skill_prompt`` — pure function, reads SKILL.md from a folder.
  Testable without any network calls.
* ``run_protocol_analysis`` — async function that does the real work.
  Mirrors ``claude_client.py``'s ``run_skill`` shape, including the
  retry/backoff loop on rate limits.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from anthropic import AsyncAnthropic

# structlog if available, else stdlib shim — same pattern used in
# core/fingerprint.py. Lets the module import where structlog isn't
# installed (the dev sandbox during testing).
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

        def debug(self, event: str, **kw: Any) -> None:
            _stdlogger.debug(self._fmt(event, kw))

    logger = _StdlibShimLogger()


# Resolve the path to the protocol-analysis skill folder by walking up
# from this file:
#   .../oc-ai-pipeline/services/study-build-trainer/core/protocol_analysis_client.py
#   ↑ parent           study-build-trainer/core/
#   ↑ parent.parent    study-build-trainer/
#   ↑ parent[3]        services/
#   ↑ parent[4]        oc-ai-pipeline/
#   + skills/protocol-analysis/
DEFAULT_SKILL_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills"
    / "protocol-analysis"
)


# ─── Pure helpers ─────────────────────────────────────────────────


def load_skill_prompt(skill_dir: Path | str | None = None) -> str:
    """
    Load the protocol-analysis skill's SKILL.md as a prompt string.

    Args:
        skill_dir: Folder containing SKILL.md. Defaults to the shared
            skills folder at ``../../skills/protocol-analysis``.

    Raises:
        FileNotFoundError: if SKILL.md isn't where expected. The
            message includes the path we looked at, since this is
            usually a path-resolution bug rather than a missing file.
    """
    folder = Path(skill_dir) if skill_dir else DEFAULT_SKILL_DIR
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(
            f"SKILL.md not found at {skill_md}. "
            f"If the trainer was extracted to its own repo, set skill_dir "
            f"explicitly or migrate to the Anthropic Skills API."
        )
    return skill_md.read_text(encoding="utf-8")


def build_content_blocks(
    pdf_bytes: bytes,
    skill_prompt: str,
    *,
    extra_text: str = "",
) -> list[dict[str, Any]]:
    """
    Assemble the user-message content blocks.

    Mirrors the shape produced by ``claude_client.py``'s ``run_skill``:
    PDF as a base64 document block first, optional extra text, then
    the skill prompt last. Order matters here — the skill prompt is
    placed at the end so it acts as the "instructions" framing the
    PDF that came before it.
    """
    blocks: list[dict[str, Any]] = []

    blocks.append({
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode(),
        },
    })

    if extra_text:
        blocks.append({"type": "text", "text": extra_text})

    blocks.append({"type": "text", "text": skill_prompt})

    return blocks


# ─── Main entry point ────────────────────────────────────────────


async def run_protocol_analysis(
    pdf_bytes: bytes,
    *,
    skill_dir: Path | str | None = None,
    client: "AsyncAnthropic | None" = None,
    api_key: str | None = None,
    model: str = "claude-opus-4-7",
    max_tokens: int = 16000,
    extra_text: str = "",
    max_retries: int = 5,
    initial_wait_seconds: int = 60,
) -> str:
    """
    Run the protocol-analysis skill against a PDF; return Claude's text.

    The output is whatever the skill produces — typically a JSON-ish
    structured payload, sometimes mixed with explanation text. This
    function does NOT parse the response. The caller decides how to
    interpret it (e.g. JSON-parse, store as text, etc.).

    Args:
        pdf_bytes: Raw bytes of the protocol PDF.
        skill_dir: Optional override for the skill folder. Defaults to
            the shared skills folder. Tests pass a fixture folder.
        client: Optional pre-built ``AsyncAnthropic`` client. If None,
            one is constructed from ``api_key`` (or
            ``settings.anthropic_api_key`` if both are None).
        api_key: Optional API key override. Only used when ``client``
            is None.
        model: Claude model identifier. Defaults to
            ``claude-opus-4-7`` per the design decision to use Opus
            for protocol analysis on both sides.
        max_tokens: Output cap. 16000 mirrors claude_client.py.
        extra_text: Optional supplementary text inserted between the
            PDF and the skill prompt. Useful for passing curator
            notes, e.g. "this is a Phase 2 oncology study from 2023."
        max_retries: How many times to retry on rate-limit errors
            before giving up. Default 5.
        initial_wait_seconds: First retry's backoff. Subsequent
            retries scale linearly: ``wait * (attempt + 1)``. Mirrors
            the pipeline's existing pattern.

    Returns:
        The text content of Claude's response.

    Raises:
        FileNotFoundError: if the skill folder is wrong.
        anthropic.APIError: if Claude fails for non-rate-limit reasons.
        anthropic.RateLimitError: if rate-limited and all retries
            are exhausted.
    """
    skill_prompt = load_skill_prompt(skill_dir)
    content = build_content_blocks(pdf_bytes, skill_prompt, extra_text=extra_text)

    # Lazy SDK + settings import. Only when we don't have an injected
    # client, so unit tests don't need anthropic / pydantic-settings
    # installed.
    if client is None:
        from anthropic import AsyncAnthropic

        if api_key is None:
            from app.config import settings
            api_key = settings.anthropic_api_key

        client = AsyncAnthropic(api_key=api_key)

    # Retry/backoff loop, mirrored from claude_client.py.
    # We re-import anthropic lazily inside the except clauses so the
    # module imports cleanly without the SDK present.
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            logger.info(
                "protocol_analysis.attempt",
                attempt=attempt + 1,
                content_blocks=len(content),
                model=model,
            )
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
            text = ""
            for block in response.content:
                block_text = getattr(block, "text", None)
                if isinstance(block_text, str):
                    text += block_text
            logger.info(
                "protocol_analysis.success",
                response_length=len(text),
            )
            return text

        except Exception as exc:  # noqa: BLE001 — narrow below
            # Identify rate-limit vs other API errors without forcing
            # an import of anthropic at module level.
            from anthropic import APIError, RateLimitError

            if isinstance(exc, RateLimitError):
                last_exc = exc
                if attempt < max_retries - 1:
                    wait = initial_wait_seconds * (attempt + 1)
                    logger.warning(
                        "protocol_analysis.rate_limited",
                        attempt=attempt + 1,
                        wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error("protocol_analysis.rate_limit_exhausted")
                raise
            if isinstance(exc, APIError):
                logger.error("protocol_analysis.api_error", error=str(exc))
                raise
            # Any other exception — let it propagate.
            raise

    # Defensive: shouldn't reach here unless retries == 0
    if last_exc:
        raise last_exc
    raise RuntimeError("protocol_analysis: exited retry loop without result")
