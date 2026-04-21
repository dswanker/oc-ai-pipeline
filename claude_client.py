"""
claude_client.py — Anthropic API wrapper for oc-ai-pipeline

Claude is called for analytical tasks only and returns text or JSON.
Binary file generation is handled by skill scripts running locally on
this server — Claude is never asked to produce binary file bytes.
"""
import anthropic, base64, json, os, asyncio, re

MODEL = "claude-sonnet-4-5"


async def call_claude(prompt, pdf_bytes=None, extra_text=None, max_tokens=16000):
    """
    Call Claude with a text prompt and optional PDF attachment.
    Returns the full text response as a string.

    Args:
        prompt      : The main instruction string.
        pdf_bytes   : Raw PDF bytes to attach as a document, or None.
        extra_text  : Optional additional text block prepended before the prompt.
        max_tokens  : Max tokens in the response (default 16 000).
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

    max_retries  = 5
    wait_seconds = 60

    for attempt in range(max_retries):
        try:
            print(f"Claude API call — attempt {attempt + 1}, blocks: {len(content)}", flush=True)
            response = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
            text = response.content[0].text
            print(f"Claude API success — {len(text)} chars", flush=True)
            return text

        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                wait = wait_seconds * (attempt + 1)
                print(f"Rate limit — waiting {wait}s (attempt {attempt + 1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
            else:
                print("Rate limit — max retries exceeded", flush=True)
                raise

        except anthropic.APIError as e:
            print(f"Anthropic API error: {e}", flush=True)
            raise


def extract_json(text):
    """
    Extract the first valid JSON object or array from a Claude text response.

    Claude often wraps JSON in markdown code fences (```json ... ```) —
    this strips them before parsing.

    Returns the parsed dict or list.
    Raises ValueError if no valid JSON can be found.
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    # Find the outermost { } or [ ] block
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
                        break  # malformed — try next open character

    raise ValueError("No valid JSON found in Claude response")
