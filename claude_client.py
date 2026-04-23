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

MODEL       = "claude-opus-4-7"
MAX_TOKENS  = 64000         # for call_claude (JSON extraction). Opus 4.7
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


# ── Plain Claude call — returns text ─────────────────────────────────────────

async def call_claude(prompt, pdf_bytes=None, extra_text=None, max_tokens=MAX_TOKENS):
    """
    Call Claude with a prompt and optional PDF. Returns full text response.
    No skills, no code execution — used for JSON extraction tasks only.
    """
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )

    content = []
    if pdf_bytes:
        content.append({
            "type": "document",
            "source": {
                "type":       "base64",
                "media_type": "application/pdf",
                "data":       base64.standard_b64encode(pdf_bytes).decode(),
            },
        })
    if extra_text:
        content.append({"type": "text", "text": extra_text})
    content.append({"type": "text", "text": prompt})

    for attempt in range(MAX_RETRIES):
        try:
            print(f"call_claude — attempt {attempt+1}, blocks: {len(content)} [streaming]", flush=True)
            async with client.messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                response = await stream.get_final_message()
            text = response.content[0].text
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

        except anthropic.APIError as e:
            print(f"API error: {e}", flush=True)
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
                model=MODEL,
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
            model=MODEL,
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
