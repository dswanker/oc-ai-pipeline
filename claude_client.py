"""
claude_client.py — Anthropic API client for oc-ai-pipeline

Two modes:
  call_claude()  — plain Messages API, returns JSON text. Used for analysis
                   tasks where Claude reads a protocol or JSON and returns
                   structured data. Fast, no code execution, no skills.

  run_skill()    — Skills API with code execution. Used when we need Claude
                   to run Python scripts (reportlab, openpyxl) to generate
                   real binary output files (PDFs, XLSXs, ZIPs).
                   Returns {filename: bytes}.
"""

import anthropic, base64, json, os, asyncio, re
import contextvars, hashlib, time
import httpx  # used in the retry except clauses below (was missing: any other error surfaced as NameError)

MODEL       = "claude-opus-4-7"
MAX_TOKENS  = 16000         # for call_claude (JSON extraction). Opus 4.7
                             # NOTE: spec extraction uses EXTENDED_OUTPUT_MAX_TOKENS
                             # (see call_claude extended_output parameter)
EXTENDED_OUTPUT_MAX_TOKENS = 96000  # Opus 4.7 + output-128k beta; headroom over ~64K limit
                            # supports up to 128K output; 64K is plenty for
                            # a rich Study Spec with per-row metadata,
                            # XPaths, and aggressive optional-field
                            # population. Raise to 96K or 128K if a large
                            # study ever truncates again.
MAX_TOKENS_SKILL = 32000    # for run_skill (file generation, can be long)
MAX_RETRIES = 5

SKILL_BETAS = [
    "code-execution-2025-08-25",
    "skills-2025-10-02",
    "files-api-2025-04-14",
]


# ── Cost switches (all default OFF: the request is exactly what it was) ──────

def doc_first_enabled(main_analysis=False):
    """PROTOCOL_DOC_FIRST=1: the protocol PDF is the FIRST content block and carries a 1-hour cache marker, so
    every later call of the run that sends the same PDF reads it from the cache (a tenth of the input price)
    instead of paying for it again. Only the order of the blocks and the cache markers change.
    PROTOCOL_DOC_FIRST=checks: the same for every protocol call EXCEPT the main analysis (the extended-output
    call), whose request stays exactly as it is today; it then pays the full price for the protocol."""
    v = os.environ.get("PROTOCOL_DOC_FIRST", "0").strip().lower()
    return v == "1" or (v == "checks" and not main_analysis)


def doc_cache_ttl():
    """TTL of the protocol cache entry. 1h by default: the main analysis streams for longer than 5 minutes, so the
    calls after it would miss a 5-minute entry. PROTOCOL_DOC_CACHE_TTL=5m overrides."""
    return "5m" if os.environ.get("PROTOCOL_DOC_CACHE_TTL", "1h").strip().lower() == "5m" else "1h"


def build_content(prompt, pdf_bytes=None, extra_text=None, cache_prompt=True, images=None, main_analysis=False):
    """The content blocks of one call. Default order: prompt (cacheable) FIRST, then PDF + images + extra_text.
    With PROTOCOL_DOC_FIRST=1 and a PDF: the PDF first with its own cache marker, then the prompt (keeping its
    marker), images and extra_text."""
    prompt_block = {"type": "text", "text": prompt}
    if cache_prompt:
        prompt_block["cache_control"] = {"type": "ephemeral"}
    doc_block = None
    if pdf_bytes:
        doc_block = {
            "type": "document",
            "source": {
                "type":       "base64",
                "media_type": "application/pdf",
                "data":       base64.standard_b64encode(pdf_bytes).decode(),
            },
        }
    content = []
    if doc_block is not None and doc_first_enabled(main_analysis):
        cc = {"type": "ephemeral"}
        if doc_cache_ttl() == "1h":
            cc["ttl"] = "1h"
        doc_block["cache_control"] = cc
        content.append(doc_block)
        content.append(prompt_block)
    else:
        content.append(prompt_block)
        if doc_block is not None:
            content.append(doc_block)
    # Inject source EDC screenshots — placed after the PDF so Claude reads
    # protocol first, then sees the reference images.
    if images:
        for media_type, img_data in images:
            content.append({
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": media_type,
                    "data":       img_data,
                },
            })
    if extra_text:
        content.append({"type": "text", "text": extra_text})
    return content


# ── Model per step (MODEL_PROFILE; default = today's model, requests byte-identical) ──

STEP = contextvars.ContextVar("ai_step", default=None)   # the pipeline step the current call belongs to
MODEL_OPUS_55, MODEL_SONNET_55 = "claude-opus-5-5", "claude-sonnet-5-5"
PROFILES = ("default", "opus55", "mixed55")
# every step that calls the model through this module (call_claude / run_skill), and what it does
STEPS = {
    "quick_analysis":       "trainer quick analysis of the protocol",
    "main_analysis":        "main protocol analysis (Study Specification)",
    "standards_close_call": "standards matching: pairs by meaning / close calls",
    "protocol_fields":      "protocol-specified fields of matched standard forms",
    "completeness":         "protocol completeness checklist",
    "basis":                "protocol basis of every form",
    "merged_checks":        "the three protocol checks as one call (PROTOCOL_CHECKS_MERGED)",
    "schedule_question":    "visit schedule: same-visit question",
    "concept_tagging":      "CDISC concept tagging",
    "standard_logic":       "AI logic for standard forms",
    "sdv_endpoint":         "Study Configuration: SDV endpoint link",
    "ai_edit_checks":       "AI-proposed edit checks",
    "pricing_summary":      "pricing summary",
    "dvs_added_checks":     "edited DVS: added checks",
    "dvs_build_json":       "DVS update: form JSON from the specification",
    "dvs_translate":        "DVS update: translate the edited DVS",
    "migration":            "migration pipeline call",
    "skill_run":            "skill run (code execution)",
}
# the steps that decide forms, visits and fields
DECIDING_STEPS = ("main_analysis", "completeness", "protocol_fields", "basis", "merged_checks", "schedule_question",
                  "standards_close_call")
THINKING_HEADROOM = 16000   # 5.5 models always think, and max_tokens covers thinking plus text: a third more,
                            # at least this much (the first Opus 5.5 main analysis used 101,602 output tokens)
MAX_OUTPUT_55 = 128000


def model_profile():
    v = os.environ.get("MODEL_PROFILE", "default").strip().lower()
    return v if v in PROFILES else "default"


def is_55(model):
    return bool(re.match(r"claude-(?:opus|sonnet|haiku|fable)-5", str(model or "")))


def model_for(step=None, today=MODEL):
    """The model a step's call uses. MODEL_STEP_<STEP>=<model id> overrides one step. Profiles: "default" is the
    model the call has always used; "opus55" moves every call that uses Opus 4.7 to Opus 5.5; "mixed55" gives
    Opus 5.5 to the steps that decide forms, visits and fields and Sonnet 5.5 to every other such call. A call
    that is not on Opus 4.7 today keeps its model in every profile."""
    over = os.environ.get(f"MODEL_STEP_{str(step).upper()}", "").strip() if step else ""
    if over:
        return over
    prof = model_profile()
    if prof == "default" or today != "claude-opus-4-7":
        return today
    if prof == "opus55":
        return MODEL_OPUS_55
    return MODEL_OPUS_55 if step in DECIDING_STEPS else MODEL_SONNET_55


def profile_map(profile):
    """{step: model} of a profile (for reports and tests)."""
    old = os.environ.get("MODEL_PROFILE")
    os.environ["MODEL_PROFILE"] = profile
    try:
        return {step: model_for(step) for step in STEPS}
    finally:
        if old is None:
            os.environ.pop("MODEL_PROFILE", None)
        else:
            os.environ["MODEL_PROFILE"] = old


def adapt_request(kwargs, betas, model):
    """(kwargs, betas) of a request for the model. Opus 4.7 and earlier: only the model id. A 5.5 model: no
    output-128k beta (128K output is standard and the header is to be removed), room in max_tokens for the thinking
    that always runs, and the effort level when MODEL_EFFORT sets one (else the model's default). Prompt and
    content are never changed."""
    kwargs = dict(kwargs, model=model)
    if not is_55(model):
        return kwargs, betas
    betas = [b for b in (betas or []) if not b.startswith("output-128k")] or None
    asked = int(kwargs.get("max_tokens") or 0)
    kwargs["max_tokens"] = min(MAX_OUTPUT_55, max(asked * 4 // 3, asked + int(
        os.environ.get("MODEL_THINKING_HEADROOM", str(THINKING_HEADROOM)))))
    effort = os.environ.get("MODEL_EFFORT", "").strip().lower()
    if effort:
        kwargs["output_config"] = {"effort": effort}
    return kwargs, betas


def response_text(response):
    """The text of a reply: its text blocks (a 5.5 model's reply starts with thinking blocks). A refusal raises."""
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined the request (stop_reason refusal)")
    blocks = [b for b in (getattr(response, "content", None) or []) if getattr(b, "type", "text") == "text"]
    if not blocks:
        raise RuntimeError(f"the reply has no text (stop_reason {getattr(response, 'stop_reason', None)})")
    return blocks[0].text if len(blocks) == 1 else "".join(b.text for b in blocks)


# ── Usage record (every call; read by cost reports and tests) ────────────────

USAGE = []          # one dict per finished call, in order
PRICES = {"input": 5.0, "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0, "cache_read": 0.50}   # $ / MTok
# list prices per model, $ / MTok (Anthropic pricing page, 2026-10-09). A model not listed is priced as Opus 4.7.
MODEL_PRICES = {
    "claude-opus-4-7":   PRICES,
    "claude-opus-5-5":   {"input": 4.0, "output": 20.0, "cache_write_5m": 5.0, "cache_write_1h": 8.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0, "cache_read": 0.10},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write_5m": 3.75, "cache_write_1h": 6.0, "cache_read": 0.30},
}


def call_cost(rec):
    """Dollars of one usage record at its model's list prices (Opus 4.7 when the record names no model); a
    batched call costs half."""
    p = MODEL_PRICES.get(rec.get("model") or "", PRICES)
    usd = (rec.get("input", 0) * p["input"] + rec.get("output", 0) * p["output"]
           + rec.get("cache_write_5m", 0) * p["cache_write_5m"]
           + rec.get("cache_write_1h", 0) * p["cache_write_1h"]
           + rec.get("cache_read", 0) * p["cache_read"]) / 1_000_000
    return round(usd * (0.5 if rec.get("batch_id") else 1.0), 4)


def _record_usage(response, prompt, pdf_bytes, max_tokens, started, batch_id=None, batch_wait=None, model=None):
    u = getattr(response, "usage", None)
    cc = getattr(u, "cache_creation", None)
    crt = (getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0
    w1h = (getattr(cc, "ephemeral_1h_input_tokens", 0) or 0) if cc is not None else 0
    w5m = (getattr(cc, "ephemeral_5m_input_tokens", 0) or 0) if cc is not None else crt
    if cc is not None and not (w1h or w5m):
        w5m = crt
    rec = {"prompt": str(prompt or "")[:80], "prompt_sha": hashlib.sha256(str(prompt or "").encode()).hexdigest()[:12],
           "pdf_sha": hashlib.sha256(pdf_bytes).hexdigest()[:12] if pdf_bytes else None,
           "max_tokens": max_tokens,
           "input": (getattr(u, "input_tokens", 0) or 0) if u else 0,
           "output": (getattr(u, "output_tokens", 0) or 0) if u else 0,
           "cache_read": (getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0,
           "cache_write_5m": w5m, "cache_write_1h": w1h,
           "started": round(started, 1), "seconds": round(time.time() - started, 1),
           "batch_id": batch_id, "batch_wait": batch_wait}
    if model and model != MODEL:
        rec["model"] = model
    if STEP.get():
        rec["step_name"] = STEP.get()
    rec["usd"] = call_cost(rec)
    USAGE.append(rec)
    return rec


# ── Economy runs: the Message Batches API at half price ──────────────────────

_ECONOMY = contextvars.ContextVar("economy_run", default=None)


def economy_allowed():
    """ECONOMY_RUNS=0 switches economy mode off for every run."""
    return os.environ.get("ECONOMY_RUNS", "1") != "0"


def set_economy(on, log=None):
    """Mark the current run (this task and the tasks it starts) as an economy run. log: async callable(message)
    for the progress line while a batch is pending (at most one every 10 minutes)."""
    _ECONOMY.set({"log": log, "last_progress": time.time()} if on and economy_allowed() else None)


def economy_active():
    return _ECONOMY.get() is not None and economy_allowed()


_BATCH_UNSUPPORTED = ("stream", "speed")      # Message Batches API: parameters a batched request may not carry


def batch_unsupported(kwargs):
    """Why this request cannot be batched, or "". Per the Message Batches documentation everything call_claude
    sends is supported (PDF document blocks, images, cache_control incl. the 1-hour TTL, beta headers, max_tokens up
    to the model limit); only `stream`, `speed` and max_tokens 0 are not."""
    bad = [k for k in _BATCH_UNSUPPORTED if k in kwargs]
    if int(kwargs.get("max_tokens") or 0) < 1:
        bad.append("max_tokens 0")
    return ", ".join(bad)


async def _batch_message(client, kwargs, betas):
    """One call as a batch of one. Returns (message, batch id, seconds waited). Raises when the batch errors,
    expires, is canceled or outlives ECONOMY_MAX_WAIT_S (the caller then runs the call normally, once)."""
    api = client.beta.messages.batches if betas else client.messages.batches
    extra = {"betas": betas} if betas else {}
    batch = await api.create(requests=[{"custom_id": "call-1", "params": kwargs}], **extra)
    t0, delay, state = time.time(), 5.0, _ECONOMY.get() or {}
    max_wait = float(os.environ.get("ECONOMY_MAX_WAIT_S", str(24 * 3600)))
    print(f"call_claude economy — batch {batch.id} created", flush=True)
    try:
        while True:
            cur = await api.retrieve(batch.id, **extra)
            if getattr(cur, "processing_status", "") == "ended":
                break
            waited = time.time() - t0
            if waited > max_wait:
                raise TimeoutError(f"batch {batch.id} not finished after {int(waited)}s")
            if state.get("log") and time.time() - state.get("last_progress", 0) >= 600:
                state["last_progress"] = time.time()
                try:
                    await state["log"](f"Economy run: waiting for a batched AI call ({int(waited // 60)} min so far).")
                except Exception:
                    pass
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 60.0)
    except BaseException:
        try:   # a call that is given up (timeout, cancellation) must not keep running and be billed
            await api.cancel(batch.id, **extra)
        except Exception:
            pass
        raise
    waited = round(time.time() - t0, 1)
    result = None
    async for item in await api.results(batch.id, **extra):
        result = item.result
        break
    kind = getattr(result, "type", None)
    if kind != "succeeded":
        raise RuntimeError(f"batch {batch.id} result: {kind or 'missing'}")
    print(f"call_claude economy — batch {batch.id} done after {waited}s", flush=True)
    return result.message, batch.id, waited


# ── Plain Claude call — returns text ─────────────────────────────────────────

async def call_claude(prompt, pdf_bytes=None, extra_text=None, max_tokens=MAX_TOKENS,
                      cache_prompt=True, extended_output=False, images=None):
    """
    Call Claude with a prompt and optional PDF/images. Returns full text response.
    No skills, no code execution — used for JSON extraction tasks only.

    Prompt caching:
      When cache_prompt=True (default), the `prompt` block gets
      cache_control=ephemeral. Since we reorder content so `prompt` is FIRST,
      Anthropic caches it across runs, cutting cached-input cost by 90%.
      The PDF + extra_text + images come AFTER (they vary per run) so they're
      not included in the cache.

      Caching requires the prompt to be >= 1024 tokens for Opus. Our
      EDC_STRUCTURE_PROMPT (~4.3K tokens) and PRICING_SUMMARY_PROMPT
      (~1.3K tokens) both qualify. Smaller prompts skip caching automatically
      (Anthropic silently ignores cache_control when content is too short).

    images: optional list of (media_type, base64_data) tuples, e.g.
      [("image/png", "<b64>"), ...]. Injected after pdf_bytes, before
      extra_text. When more than 20 image blocks are present (counting the
      PDF as one document block), Anthropic enforces a 2000px per-side
      dimension limit — callers should pre-resize before passing.
    """
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        # 45-minute total timeout — covers large Protocol Analysis calls
        # (3 PDFs + screenshots + extended output). Without this the SDK
        # waits indefinitely on a hung stream.
        timeout=2700.0,
    )

    # Order: prompt (cacheable) FIRST, then PDF + images + extra_text (per-run).
    # The cache key is the literal block content up to & including the
    # cache_control marker — so anything BEFORE the marker gets cached.
    # (PROTOCOL_DOC_FIRST=1 puts the PDF first with its own marker: build_content.)
    content = build_content(prompt, pdf_bytes, extra_text, cache_prompt, images, main_analysis=extended_output)
    if pdf_bytes and doc_first_enabled(extended_output):
        print(f"call_claude — protocol first, cache ttl {doc_cache_ttl()}, pdf sha256 "
              f"{hashlib.sha256(pdf_bytes).hexdigest()[:12]} ({len(pdf_bytes)} bytes)", flush=True)
    _economy_tried = False

    for attempt in range(MAX_RETRIES):
        try:
            print(f"call_claude — attempt {attempt+1}, blocks: {len(content)} "
                  f"[streaming, cache={cache_prompt}, extended={extended_output}]", flush=True)
            _stream_kwargs = dict(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
            _betas = ["output-128k-2025-02-19"] if extended_output else None
            _model = model_for(STEP.get())
            if _model != MODEL:
                _stream_kwargs, _betas = adapt_request(_stream_kwargs, _betas, _model)
                print(f"call_claude — step {STEP.get()}: model {_model} (profile {model_profile()}), "
                      f"max_tokens {_stream_kwargs['max_tokens']}", flush=True)
            _started = time.time()
            response = _batch_id = _batch_wait = None
            if economy_active() and not _economy_tried:
                # Economy run: the same request as a batch of one (half price). A batch that errors or expires
                # is retried once as a normal call; a request the batch API cannot take runs normally.
                _economy_tried = True
                _why = batch_unsupported(_stream_kwargs)
                if _why:
                    print(f"call_claude economy — not batchable ({_why}); running normally", flush=True)
                else:
                    try:
                        response, _batch_id, _batch_wait = await _batch_message(client, _stream_kwargs, _betas)
                    except Exception as _be:
                        if "credit balance" in str(_be).lower():
                            raise
                        print(f"call_claude economy — batch failed ({type(_be).__name__}: {str(_be)[:200]}); "
                              f"retrying this call normally, once", flush=True)
                        response = None
                        _started = time.time()
            if response is not None:
                pass
            elif _betas:
                # output-128k-2025-02-19 beta raises the per-request output
                # cap from 32K to 128K for Opus 4.7. Required when the study
                # spec JSON exceeds ~64K tokens (large studies with many forms
                # and full per-row XLSForm metadata).
                _stream_kwargs["betas"] = _betas
                _cm = client.beta.messages.stream(**_stream_kwargs)
            else:
                _cm = client.messages.stream(**_stream_kwargs)
            if response is None:
                async with _cm as stream:
                    response = await stream.get_final_message()
            text = response.content[0].text if _model == MODEL else response_text(response)
            try:
                _record_usage(response, prompt, pdf_bytes, max_tokens, _started, _batch_id, _batch_wait, _model)
            except Exception:
                pass
            # Usage info — shows cache hit/miss
            u = getattr(response, "usage", None)
            if u:
                cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
                cache_crt  = getattr(u, "cache_creation_input_tokens", 0) or 0
                inp        = getattr(u, "input_tokens", 0) or 0
                out        = getattr(u, "output_tokens", 0) or 0
                print(f"call_claude usage — input={inp}, output={out}, "
                      f"cache_read={cache_read}, cache_created={cache_crt}",
                      flush=True)
            print(f"call_claude success — {len(text)} chars", flush=True)
            return text

        except anthropic.RateLimitError:
            if attempt < MAX_RETRIES - 1:
                wait = 60 * (attempt + 1)
                print(f"Rate limit — waiting {wait}s (attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Rate limit — max retries exceeded", flush=True)
                raise

        except anthropic.InternalServerError as e:
            # B2: Anthropic transient server errors (500/502/503/504) —
            # retry with exponential backoff. Distinct from BadRequestError
            # (which is a client-side problem we shouldn't retry).
            if attempt < MAX_RETRIES - 1:
                wait = 15 * (2 ** attempt)  # 15s, 30s, 60s, 120s
                print(f"Anthropic 5xx ({e}) — waiting {wait}s "
                      f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Anthropic 5xx — max retries exceeded", flush=True)
                raise

        except (anthropic.APIConnectionError, anthropic.APITimeoutError,
                httpx.TransportError) as e:
            # Transient connection/transport failures during a (possibly
            # long) streaming call: dropped sockets, incomplete chunked
            # reads, read timeouts. Two layers:
            #  - The SDK wraps errors at request-send time as
            #    APIConnectionError / APITimeoutError.
            #  - But a transport failure DURING stream consumption
            #    (stream.get_final_message → aiter_bytes) is raised RAW as
            #    httpx.RemoteProtocolError ("peer closed connection without
            #    sending complete message body") — the SDK does not wrap
            #    mid-stream errors. httpx.TransportError is the common base
            #    (RemoteProtocolError, ReadError, ConnectError, etc.), so we
            #    catch it directly here. This is exactly the crash we hit.
            # None of these are client errors — the request was valid — so
            # retry with exponential backoff rather than killing the run.
            if attempt < MAX_RETRIES - 1:
                wait = 15 * (2 ** attempt)  # 15s, 30s, 60s, 120s
                print(f"Anthropic connection/transport error "
                      f"({type(e).__name__}: {e}) — waiting {wait}s "
                      f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Anthropic connection/transport error — max retries "
                      "exceeded", flush=True)
                raise

        except (anthropic.BadRequestError,
                anthropic.AuthenticationError,
                anthropic.PermissionDeniedError,
                anthropic.NotFoundError) as e:
            # Client-side problems — no point retrying.
            # BadRequestError covers: invalid input, context too long,
            # credit balance exhausted (error code 400).
            print(f"Client error (not retrying): {e}", flush=True)
            raise

        except anthropic.APIError as e:
            # Catch-all for any other Anthropic API error we haven't
            # classified. Default to raising; better to fail loudly than
            # retry forever on an unknown error class.
            print(f"Unclassified API error: {e}", flush=True)
            raise


def extract_json(text, expected_keys=None):
    """
    Extract a valid JSON object or array from a Claude response.

    Selection order:
      1. If expected_keys is given, prefer the LARGEST parseable candidate
         whose top-level keys include ALL expected_keys (this avoids
         grabbing an inner form/row dict that happens to parse).
      2. Otherwise, prefer the LARGEST candidate whose top-level keys
         overlap with common study-spec markers (study_meta, forms).
      3. Fallback to the largest parseable candidate.

    Strips markdown code fences before parsing.
    """
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    candidates = []   # list of (parsed_value, source_slice_length)

    for open_ch, close_ch in [('{', '}'), ('[', ']')]:
        i = 0
        while i < len(text):
            if text[i] != open_ch:
                i += 1
                continue
            depth   = 0
            in_str  = False
            escaped = False
            start   = i
            matched = False
            for j in range(i, len(text)):
                ch = text[j]
                if in_str:
                    if escaped:
                        escaped = False
                    elif ch == '\\':
                        escaped = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        slice_ = text[start:j + 1]
                        try:
                            candidates.append((json.loads(slice_), len(slice_)))
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        matched = True
                        break
            if not matched:
                # unbalanced or ran off end — skip past this open char
                i = start + 1

    if not candidates:
        raise ValueError("No valid JSON found in Claude response")

    # Hints about which candidate is the top-level document
    TOP_LEVEL_MARKERS = {"study_meta", "forms", "patient_population",
                         "crf_summary", "review_flags", "timepoint_csv"}
    if expected_keys is None:
        expected_keys = []

    def _score(parsed, size):
        """Higher is better."""
        if not isinstance(parsed, dict):
            return (-1, size)  # arrays and scalars are last resort
        keys = set(parsed.keys())
        # Tier 1: contains ALL expected keys
        if expected_keys and all(k in keys for k in expected_keys):
            return (3, size)
        # Tier 2: contains any top-level study-spec marker
        if keys & TOP_LEVEL_MARKERS:
            return (2, size)
        # Tier 3: contains ANY expected key (partial match)
        if expected_keys and (keys & set(expected_keys)):
            return (1, size)
        # Tier 4: everything else
        return (0, size)

    best_idx = max(range(len(candidates)),
                   key=lambda k: _score(candidates[k][0], candidates[k][1]))
    best_parsed, best_size = candidates[best_idx]

    # Debug summary
    best_tier = _score(best_parsed, best_size)[0]
    top_keys_preview = ""
    if isinstance(best_parsed, dict):
        top_keys_preview = f", top keys: {list(best_parsed.keys())[:8]}"
    print(f"extract_json: found {len(candidates)} candidate(s), "
          f"returning size={best_size} tier={best_tier}{top_keys_preview}",
          flush=True)
    if best_tier == 0 and len(candidates) > 20:
        print(f"extract_json: WARNING — tier=0 with {len(candidates)} candidates "
              f"suggests Claude output fragmented JSON (many small objects) "
              f"instead of one unified spec. Pipeline will abort if forms=0.",
              flush=True)

    # B3: if the caller specified expected_keys but the best candidate didn't
    # match any of them (tier < 1), we probably have truncated output or a
    # fragment — not a real extraction. Raising here prevents the pipeline
    # from silently using an inner dict as if it were the full document.
    if expected_keys and best_tier < 1:
        raise ValueError(
            f"extract_json: no candidate matched any of expected_keys="
            f"{expected_keys}. Best candidate (tier={best_tier}, "
            f"size={best_size}{top_keys_preview}) is probably a fragment "
            f"from truncated output. Inspect the raw response and consider "
            f"raising max_tokens."
        )

    return best_parsed


# ── Skills API call — returns {filename: bytes} ───────────────────────────────

def _extract_file_ids(response):
    """Pull file_ids from a skill API response.
    Handles bash_code_execution_tool_result and related result types."""
    file_ids = []
    if response is None:
        return file_ids

    # Diagnostic: log all block types so we can see what the API returns
    block_types = [getattr(b, "type", None) or "?" for b in response.content]
    print(f"  response block types: {block_types}", flush=True)

    # Diagnostic: dump stdout/stderr tails from all bash/text_editor result
    # blocks so we can see what Claude was actually doing in the sandbox
    for i, block in enumerate(response.content):
        btype = getattr(block, "type", None) or ""
        if "code_execution_tool_result" not in btype:
            continue
        inner = getattr(block, "content", None)
        if inner is None:
            continue
        try:
            if hasattr(inner, "model_dump"):
                d = inner.model_dump()
                stdout = (d.get("stdout") or "")[:300]
                stderr = (d.get("stderr") or "")[:300]
                rc     = d.get("return_code")
                content_items = d.get("content") or []
                print(f"  [block {i} {btype}] rc={rc} content_items={len(content_items)}", flush=True)
                if stdout:
                    print(f"    stdout[:300]: {stdout!r}", flush=True)
                if stderr:
                    print(f"    stderr[:300]: {stderr!r}", flush=True)
        except Exception as diag_e:
            print(f"  [block {i}] diag failed: {diag_e}", flush=True)

    for block in response.content:
        btype = getattr(block, "type", None) or ""
        if "code_execution_tool_result" not in btype:
            continue
        inner = getattr(block, "content", None)
        if inner is None:
            continue

        # Inner can be a single object or a list depending on API version
        inner_list = inner if isinstance(inner, list) else [inner]
        for item in inner_list:
            # file_id may be on the item itself, or in a nested content list
            fid = getattr(item, "file_id", None)
            if fid:
                file_ids.append(fid)
                continue
            nested = getattr(item, "content", None)
            if nested:
                for sub in (nested if isinstance(nested, list) else [nested]):
                    fid = getattr(sub, "file_id", None)
                    if fid:
                        file_ids.append(fid)
    return file_ids


async def _download_files(client, file_ids, container_id=None):
    """Download files by file_id via beta.files API. Returns {filename: bytes}."""
    results = {}
    for fid in file_ids:
        try:
            meta    = await client.beta.files.retrieve_metadata(
                file_id=fid, betas=["files-api-2025-04-14"])
            content = await client.beta.files.download(
                file_id=fid, betas=["files-api-2025-04-14"])
            data = await content.aread() if hasattr(content, "aread") else content.read()
            filename = getattr(meta, "filename", None) or fid
            results[filename] = data
            print(f"  Downloaded: {filename} ({len(data)} bytes)", flush=True)
        except Exception as e:
            print(f"  Warning: failed to download {fid}: {e}", flush=True)
    return results


async def run_skill(prompt, skill_ids,
                    pdf_bytes=None, xlsx_bytes=None, zip_bytes=None,
                    extra_text=""):
    """
    Call the Skills API with code execution.
    Used only for generating real binary output files.
    Returns {filename: bytes}.
    """
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )

    content = []
    if pdf_bytes:
        content.append({"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode()
        }})
    if xlsx_bytes:
        content.append({"type": "text",
            "text": "[XLSX attached as base64]\n" +
                    base64.standard_b64encode(xlsx_bytes).decode()})
    if zip_bytes:
        content.append({"type": "text",
            "text": "[ZIP attached as base64]\n" +
                    base64.standard_b64encode(zip_bytes).decode()})
    if extra_text:
        content.append({"type": "text", "text": extra_text})
    content.append({"type": "text", "text": prompt})

    messages   = [{"role": "user", "content": content}]
    container  = {"skills": [
        {"type": "custom", "skill_id": sid, "version": "latest"}
        for sid in skill_ids
    ]}
    tools = [{"type": "code_execution_20250825", "name": "code_execution"}]

    response = None
    for attempt in range(MAX_RETRIES):
        try:
            print(f"run_skill — attempt {attempt+1} ({len(skill_ids)} skill(s)) [streaming]", flush=True)
            # Streaming is required for operations that may exceed 10 minutes.
            # The SDK's stream() context manager collects the full message.
            async with client.beta.messages.stream(
                model=model_for("skill_run"),
                max_tokens=MAX_TOKENS_SKILL,
                betas=SKILL_BETAS,
                container=container,
                messages=messages,
                tools=tools,
            ) as stream:
                response = await stream.get_final_message()
            print(f"run_skill response — stop_reason: {response.stop_reason}", flush=True)
            break

        except anthropic.RateLimitError:
            if attempt < MAX_RETRIES - 1:
                wait = 60 * (attempt + 1)
                print(f"Rate limit — waiting {wait}s", flush=True)
                await asyncio.sleep(wait)
            else:
                raise

        except anthropic.InternalServerError as e:
            # B2: same retry pattern as call_claude
            if attempt < MAX_RETRIES - 1:
                wait = 15 * (2 ** attempt)
                print(f"Skill 5xx ({e}) — waiting {wait}s", flush=True)
                await asyncio.sleep(wait)
            else:
                raise

        except (anthropic.APIConnectionError, anthropic.APITimeoutError,
                httpx.TransportError) as e:
            # Transient connection/transport failures (dropped socket,
            # incomplete chunked read, read timeout) during the streaming
            # skill call. The SDK wraps send-time errors as
            # APIConnectionError, but a mid-stream drop surfaces as raw
            # httpx.RemoteProtocolError (base: httpx.TransportError), so we
            # catch that too. Retry rather than crash; same backoff.
            if attempt < MAX_RETRIES - 1:
                wait = 15 * (2 ** attempt)
                print(f"Skill connection/transport error "
                      f"({type(e).__name__}: {e}) — waiting {wait}s", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Skill connection/transport error — max retries "
                      "exceeded", flush=True)
                raise

        except (anthropic.BadRequestError,
                anthropic.AuthenticationError,
                anthropic.PermissionDeniedError,
                anthropic.NotFoundError) as e:
            print(f"Skill client error (not retrying): {e}", flush=True)
            raise

        except anthropic.APIError as e:
            print(f"Skill API error: {e}", flush=True)
            raise

    all_file_ids = _extract_file_ids(response)

    # Handle pause_turn for long-running skill operations
    MAX_PAUSE = 10
    # Preserve the original skills list for subsequent container dicts
    original_skills = container.get("skills", [])
    for turn in range(MAX_PAUSE):
        if response.stop_reason != "pause_turn":
            break
        print(f"pause_turn — continuing (turn {turn+1})", flush=True)
        messages.append({"role": "assistant", "content": response.content})
        cont_id = getattr(getattr(response, "container", None), "id", None)
        if cont_id:
            # Build container dict explicitly to avoid key collision
            container = {"id": cont_id, "skills": original_skills}
        async with client.beta.messages.stream(
            model=model_for("skill_run"),
            max_tokens=MAX_TOKENS_SKILL,
            betas=SKILL_BETAS,
            container=container,
            messages=messages,
            tools=tools,
        ) as stream:
            response = await stream.get_final_message()
        print(f"Continuation — stop_reason: {response.stop_reason}", flush=True)
        all_file_ids.extend(_extract_file_ids(response))

    # TODO: we need a way to retrieve files Claude created in the container.
    # beta.files.list(scope_id=container_id) returned "invalid prefix".
    # The deep diagnostic above shows what Claude is doing in the sandbox;
    # use that output to figure out the right retrieval path.
    final_cont_id = getattr(getattr(response, "container", None), "id", None)
    if not all_file_ids and final_cont_id:
        print(f"  No file_ids in response blocks. container_id={final_cont_id}", flush=True)

    print(f"run_skill complete — {len(all_file_ids)} file(s)", flush=True)
    return await _download_files(client, all_file_ids, container_id=final_cont_id)
