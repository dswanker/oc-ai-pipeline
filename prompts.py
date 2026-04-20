EDC_STRUCTURE_PROMPT = """You are running the protocol-to-edc-structure skill.
The attached PDF is a clinical trial protocol. Follow the skill instructions to produce the EDC Structure outputs.
Return three base64-encoded files using these exact markers:
===PDF_START===
[base64 encoded PDF bytes]
===PDF_END===
===XLSX_START===
[base64 encoded XLSX bytes]
===XLSX_END===
===JSON_START===
[base64 encoded JSON bytes]
===JSON_END===
"""

PRICING_SUMMARY_PROMPT = """You are running the protocol-to-pricing-summary skill.
The attached PDF is the protocol. The attached XLSX is the EDC structure output.
Follow the skill instructions to produce a pricing summary.
Return:
===PDF_START===
[base64 encoded pricing summary PDF]
===PDF_END===
"""

PRICING_MODEL_PROMPT = """You are running the pricing-model skill.
The attached file is the pricing summary. Follow the skill instructions to produce a quote.
Return:
===PDF_START===
[base64 encoded quote PDF]
===PDF_END===
===XLSX_START===
[base64 encoded quote XLSX]
===XLSX_END===
"""

EDC_BUILD_PROMPT = """You are running the edc-builder skill.
The attached XLSX is the EDC Structure specification. Follow the skill instructions to build all XLSForms.
Return:
===ZIP_START===
[base64 encoded zip bytes]
===ZIP_END===
"""

DVS_PROMPT = """You are running the dvs-specification skill.
The attached XLSX is the EDC Structure specification. The attached ZIP is the EDC Build output.
Follow the skill instructions to generate the DVS.
Return:
===XLSX_START===
[base64 encoded DVS XLSX]
===XLSX_END===
"""
