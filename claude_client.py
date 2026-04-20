import anthropic, base64, os

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
    print(f"Calling Anthropic API - content blocks: {len(content)}", flush=True)
    try:
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=8192,
            messages=[{"role":"user","content":content}]
        )
        print(f"Anthropic API success - response length: {len(response.content[0].text)}", flush=True)
        return response.content[0].text
    except Exception as e:
        print(f"Anthropic API error: {e}", flush=True)
        raise
