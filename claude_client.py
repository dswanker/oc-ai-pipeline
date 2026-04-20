import anthropic, base64, os
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

async def run_skill(skill_prompt, pdf_bytes=None, xlsx_bytes=None, extra_text=""):
    content = []
    if pdf_bytes:
        content.append({"type":"document","source":{"type":"base64","media_type":"application/pdf","data":base64.standard_b64encode(pdf_bytes).decode()}})
    if xlsx_bytes:
        content.append({"type":"text","text":"[XLSX file attached as base64]\n"+base64.standard_b64encode(xlsx_bytes).decode()})
    if extra_text:
        content.append({"type":"text","text":extra_text})
    content.append({"type":"text","text":skill_prompt})
    response = client.messages.create(model="claude-sonnet-4-6", max_tokens=8192,
        messages=[{"role":"user","content":content}])
    return response.content[0].text
