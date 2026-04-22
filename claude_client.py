"""
claude_client.py — Anthropic API client with Skills + Code Execution support.

Skills run in a code execution sandbox and return output files as file_ids
via the Files API. This client handles:
  - Passing input files (PDFs, XLSXs, ZIPs) as base64 in message content
  - Adding skill IDs and code execution tool to every request
  - Handling pause_turn for long-running skill operations
  - Extracting output file_ids from the response
  - Rate limit retries with exponential backoff
"""

import anthropic, base64, os, asyncio

BETAS = [
    "code-execution-2025-08-25",
    "skills-2025-10-02",
    "files-api-2025-04-14",
]
MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000
MAX_PAUSE_TURNS = 10
MAX_RETRIES = 5


def _build_content(pdf_bytes=None, xlsx_bytes=None, zip_bytes=None,
                   extra_text="", prompt=""):
    """Assemble the message content blocks."""
    content = []

    if pdf_bytes:
        content.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode()
            }
        })

    if xlsx_bytes:
        content.append({
            "type": "text",
            "text": "[XLSX file attached as base64]\n" +
                    base64.standard_b64encode(xlsx_bytes).decode()
        })

    if zip_bytes:
        content.append({
            "type": "text",
            "text": "[ZIP file attached as base64]\n" +
                    base64.standard_b64encode(zip_bytes).decode()
        })

    if extra_text:
        content.append({"type": "text", "text": extra_text})

    if prompt:
        content.append({"type": "text", "text": prompt})

    return content


def extract_output_file_ids(response):
    """
    Pull file_ids out of a skill response.
    Skills write output files to the container; the API returns them as
    file_id references in bash_code_execution_tool_result blocks.
    """
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


async def download_output_files(client, file_ids):
    """
    For each file_id, fetch metadata (to get the filename) and download
    the content. Returns a dict of {filename: bytes}.
    """
    results = {}
    for fid in file_ids:
        try:
            meta = await client.beta.files.retrieve_metadata(
                file_id=fid, betas=["files-api-2025-04-14"]
            )
            content = await client.beta.files.download(
                file_id=fid, betas=["files-api-2025-04-14"]
            )
            data = await content.aread() if hasattr(content, "aread") else content.read()
            results[meta.filename] = data
            print(f"  Downloaded: {meta.filename} ({len(data)} bytes)", flush=True)
        except Exception as e:
            print(f"  Warning: failed to download file_id {fid}: {e}", flush=True)
    return results


async def run_skill(skill_prompt, skill_ids,
                    pdf_bytes=None, xlsx_bytes=None, zip_bytes=None,
                    extra_text=""):
    """
    Call one or more skills via the Messages API with code execution.

    Returns a dict of {filename: bytes} for every output file the skill
    produced.
    """
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )

    content = _build_content(
        pdf_bytes=pdf_bytes,
        xlsx_bytes=xlsx_bytes,
        zip_bytes=zip_bytes,
        extra_text=extra_text,
        prompt=skill_prompt,
    )
    messages = [{"role": "user", "content": content}]

    container = {
        "skills": [
            {"type": "custom", "skill_id": sid, "version": "latest"}
            for sid in skill_ids
        ]
    }
    tools = [{"type": "code_execution_20250825", "name": "code_execution"}]

    response = None
    all_file_ids = []

    for attempt in range(MAX_RETRIES):
        try:
            print(f"Calling Skills API — attempt {attempt+1} "
                  f"({len(skill_ids)} skill(s))", flush=True)

            response = await client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                betas=BETAS,
                container=container,
                messages=messages,
                tools=tools,
            )
            print(f"Response received — stop_reason: {response.stop_reason}",
                  flush=True)
            break

        except anthropic.RateLimitError:
            if attempt < MAX_RETRIES - 1:
                wait = 60 * (attempt + 1)
                print(f"Rate limit — waiting {wait}s "
                      f"(attempt {attempt+1}/{MAX_RETRIES})", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Rate limit — max retries exceeded", flush=True)
                raise

        except anthropic.APIError as e:
            print(f"API error: {e}", flush=True)
            raise

    # Collect file_ids from this response turn
    all_file_ids.extend(extract_output_file_ids(response))

    # Handle pause_turn — skill is still running; continue until done
    for turn in range(MAX_PAUSE_TURNS):
        if response.stop_reason != "pause_turn":
            break

        print(f"pause_turn received — continuing (turn {turn+1})", flush=True)
        messages.append({"role": "assistant", "content": response.content})

        # Reuse the same container (include container id for continuity)
        cont_id = getattr(getattr(response, "container", None), "id", None)
        if cont_id:
            container = {"id": cont_id, **container}

        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=BETAS,
            container=container,
            messages=messages,
            tools=tools,
        )
        print(f"Continuation response — stop_reason: {response.stop_reason}",
              flush=True)
        all_file_ids.extend(extract_output_file_ids(response))

    print(f"Skill complete — {len(all_file_ids)} output file(s)", flush=True)

    # Download all output files and return as {filename: bytes}
    return await download_output_files(client, all_file_ids)
