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
MAX_TOKENS  = 16000
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
            print(f"call_claude — attempt {attempt+1}, blocks: {len(content)}", flush=True)
            response = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
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


def extract_json(text):
    """
    Extract the LARGEST valid JSON object or array from a Claude response.

    Claude often prefixes responses with "Here's the JSON:" or embeds small
    example objects before the real payload. Taking the first balanced `{`
    that parses can yield a stub. We instead scan for every balanced JSON
    candidate in the text and return the largest one that parses.
    Strips markdown code fences before parsing.
    """
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    candidates = []   # list of parsed JSON values
    sizes      = []   # character length of each candidate's source slice

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
                            candidates.append(json.loads(slice_))
                            sizes.append(len(slice_))
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            else:
                # unbalanced or ran off end — skip past this open char
                i = start + 1

    if not candidates:
        raise ValueError("No valid JSON found in Claude response")

    # Return the largest candidate by source length
    best_idx = max(range(len(candidates)), key=lambda k: sizes[k])
    print(f"extract_json: found {len(candidates)} candidate(s), "
          f"returning largest ({sizes[best_idx]} chars)", flush=True)
    return candidates[best_idx]


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
    """Download files by file_id. Returns {filename: bytes}.
    Tries beta.files API first, falls back to container files API."""
    results = {}
    for fid in file_ids:
        data     = None
        filename = None

        # Try 1: top-level beta.files API
        try:
            meta    = await client.beta.files.retrieve_metadata(
                file_id=fid, betas=["files-api-2025-04-14"])
            content = await client.beta.files.download(
                file_id=fid, betas=["files-api-2025-04-14"])
            data = await content.aread() if hasattr(content, "aread") else content.read()
            filename = meta.filename
        except Exception as e1:
            # Try 2: container files API (some skill outputs live only here)
            if container_id and hasattr(client.beta, "containers") and \
               hasattr(client.beta.containers, "files"):
                try:
                    meta    = await client.beta.containers.files.retrieve(
                        container_id=container_id, file_id=fid, betas=SKILL_BETAS)
                    content = await client.beta.containers.files.content(
                        container_id=container_id, file_id=fid, betas=SKILL_BETAS)
                    data = await content.aread() if hasattr(content, "aread") else content.read()
                    filename = getattr(meta, "path", None) or getattr(meta, "filename", None) or fid
                    if filename and "/" in filename:
                        filename = filename.rsplit("/", 1)[-1]
                except Exception as e2:
                    print(f"  Warning: failed to download {fid}: beta.files={e1} containers.files={e2}", flush=True)
                    continue
            else:
                print(f"  Warning: failed to download {fid}: {e1}", flush=True)
                continue

        if data is not None and filename:
            results[filename] = data
            print(f"  Downloaded: {filename} ({len(data)} bytes)", flush=True)
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
            print(f"run_skill — attempt {attempt+1} ({len(skill_ids)} skill(s))", flush=True)
            response = await client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                betas=SKILL_BETAS,
                container=container,
                messages=messages,
                tools=tools,
            )
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
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=SKILL_BETAS,
            container=container,
            messages=messages,
            tools=tools,
        )
        print(f"Continuation — stop_reason: {response.stop_reason}", flush=True)
        all_file_ids.extend(_extract_file_ids(response))

    # Fallback: if no file_ids were found in response blocks, list the
    # container's files directly. The Skills API may create files in the
    # container without referencing them in tool_result blocks.
    final_cont_id = getattr(getattr(response, "container", None), "id", None)
    if not all_file_ids and final_cont_id:
        print(f"  No file_ids in response blocks — listing container {final_cont_id}...", flush=True)
        try:
            if hasattr(client.beta, "containers") and hasattr(client.beta.containers, "files"):
                listing = await client.beta.containers.files.list(
                    container_id=final_cont_id,
                    betas=SKILL_BETAS,
                )
                fetched_ids = []
                # Iterate results (pagination handled by SDK or via .data attr)
                items = getattr(listing, "data", None) or listing
                for item in items:
                    fid = getattr(item, "id", None) or getattr(item, "file_id", None)
                    if fid:
                        fetched_ids.append(fid)
                print(f"  Container file listing: found {len(fetched_ids)} file(s)", flush=True)
                all_file_ids.extend(fetched_ids)
            else:
                print(f"  SDK has no beta.containers.files — cannot list container files", flush=True)
        except Exception as e:
            print(f"  Container file listing failed: {e}", flush=True)

    print(f"run_skill complete — {len(all_file_ids)} file(s)", flush=True)
    return await _download_files(client, all_file_ids, container_id=final_cont_id)
