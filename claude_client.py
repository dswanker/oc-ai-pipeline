import anthropic, base64, os, asyncio

async def run_skill(skill_prompt, pdf_bytes=None, xlsx_bytes=None, extra_text=""):
    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY","").strip())
    content = []
    if pdf_bytes:
        content.append({"type":"document","source":{
            "type":"base64","media_type":"application/pdf",
            "data":base64.standard_b64encode(pdf_bytes).decode()}})
    if xlsx_bytes:
        content.append({"type":"text","text":
            "[XLSX file attached as base64]\n"+
            base64.standard_b64encode(xlsx_bytes).decode()})
    if extra_text:
        content.append({"type":"text","text":extra_text})
    content.append({"type":"text","text":skill_prompt})

    # Retry logic with exponential backoff for rate limit errors
    max_retries = 5
    wait_seconds = 60

    for attempt in range(max_retries):
        try:
            print(f"Calling Anthropic API - attempt {attempt+1} - content blocks: {len(content)}", flush=True)
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                messages=[{"role":"user","content":content}]
            )
            print(f"Anthropic API success - response length: {len(response.content[0].text)}", flush=True)
            return response.content[0].text

        except anthropic.RateLimitError as e:
            if attempt < max_retries - 1:
                wait = wait_seconds * (attempt + 1)
                print(f"Rate limit hit - waiting {wait}s before retry (attempt {attempt+1}/{max_retries})", flush=True)
                await asyncio.sleep(wait)
            else:
                print(f"Rate limit - max retries exceeded", flush=True)
                raise

        except anthropic.APIError as e:
            print(f"Anthropic API error: {e}", flush=True)
            raise
