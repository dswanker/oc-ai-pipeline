"""
prompts.py — Skill invocation prompts for the oc-ai-pipeline.

These prompts tell each skill what inputs are attached and what to produce.
No base64 return markers — skills write real files via the code execution
sandbox and the API returns them as file_ids automatically.
"""

PROTOCOL_ANALYSIS_PROMPT = """
Run the protocol-analysis skill on the attached protocol PDF.

If a Customer CRF Library (PDF documents) is attached, use it as Priority 1
for form matching. If a Customer OC4 XLSForm Standards zip is attached, use
it as Priority 2. Fall back to CDASH defaults for any unmatched forms.

Produce all five outputs:
- {PROTOCOL}_Study_Specification.pdf
- {PROTOCOL}_Study_Specification.xlsx
- {PROTOCOL}_Study_Specification.json
- {PROTOCOL}_Protocol_Summary.pdf
- {PROTOCOL}_Protocol_Summary.json

Follow the skill instructions in SKILL.md exactly.
"""

PRICING_QUOTE_PROMPT = """
Run the pricing-quote skill on the attached Protocol Summary.

The attached file is the Protocol Summary JSON output from the
protocol-analysis skill. Use it to generate the four quote output files:
- {PROTOCOL}_Quote_Internal.pdf
- {PROTOCOL}_Quote_Client.pdf
- {PROTOCOL}_Quote_Internal.xlsx
- {PROTOCOL}_Quote_Client.xlsx

Follow the skill instructions in SKILL.md exactly.
"""

EDC_BUILD_PROMPT = """
Run the edc-builder skill on the attached Study Specification.

The attached file is the Study Specification XLSX output from the
protocol-analysis skill. Use it to build all XLSForms and produce the
full EDC build package:
- {PROTOCOL}_EDC_Build.zip

Follow the skill instructions in SKILL.md exactly.
"""

DVS_PROMPT = """
Run the dvs-specification skill.

The attached XLSX is the Study Specification from the protocol-analysis skill.
The attached ZIP is the EDC Build output from the edc-builder skill.
Use both to generate the Data Validation Specification:
- {PROTOCOL}_DVS.xlsx

Follow the skill instructions in SKILL.md exactly.
"""
