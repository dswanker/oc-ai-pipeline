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
    Extract the first valid JSON object or array from a Claude text response.
    Strips markdown code fences before parsing.
    """
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    for open_ch, close_ch in [('{', '}'), ('[', ']')]:
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError("No valid JSON found in Claude response")


# ── Skills API call — returns {filename: bytes} ───────────────────────────────

def _extract_file_ids(response):
    """Pull file_ids from a skill API response."""
    file_ids = []
    for block in response.content:
        if getattr(block, "type", None) == "bash_code_execution_tool_result":
            inner = getattr(block, "content", None)
            if inner and getattr(inner, "type", None) == "bash_code_execution_result":
                for item in getattr(inner, "content", []):
                    fid = getattr(item, "file_id", None)
                    if fid:
                        file_ids.append(fid)
    return file_ids


async def _download_files(client, file_ids):
    """Download files by file_id. Returns {filename: bytes}."""
    results = {}
    for fid in file_ids:
        try:
            meta    = await client.beta.files.retrieve_metadata(
                file_id=fid, betas=["files-api-2025-04-14"])
            content = await client.beta.files.download(
                file_id=fid, betas=["files-api-2025-04-14"])
            data = await content.aread() if hasattr(content, "aread") else content.read()
            results[meta.filename] = data
            print(f"  Downloaded: {meta.filename} ({len(data)} bytes)", flush=True)
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
    for turn in range(MAX_PAUSE):
        if response.stop_reason != "pause_turn":
            break
        print(f"pause_turn — continuing (turn {turn+1})", flush=True)
        messages.append({"role": "assistant", "content": response.content})
        cont_id = getattr(getattr(response, "container", None), "id", None)
        if cont_id:
            container = {"id": cont_id, **container}
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

    print(f"run_skill complete — {len(all_file_ids)} file(s)", flush=True)
    return await _download_files(client, all_file_ids)
