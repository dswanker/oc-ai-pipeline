---
name: pricing-model
description: >
  Reads the output from the protocol-to-pricing-summary skill and applies
  the OpenClinica pricing model to generate a quote. Fetches live subscription
  rates from Google Drive on each run. Outputs four files: internal PDF,
  client PDF, internal XLSX, client XLSX. Use this skill whenever a user
  asks to generate a quote, proposal, or pricing estimate from a pricing
  summary. Always run protocol-to-pricing-summary BEFORE this skill.
---

# Pricing Model Skill

## Purpose

Apply the OpenClinica pricing model to a protocol pricing summary and
generate professional quote documents in PDF and XLSX format — both
internal (full breakdown) and client-facing (summary) versions.

**Outputs — 4 files per run:**
- `{PROTOCOL}_Quote_Internal.pdf` — Full breakdown with flag analysis, discounts
- `{PROTOCOL}_Quote_Client.pdf`   — Clean proposal without internal pricing detail
- `{PROTOCOL}_Quote_Internal.xlsx` — Internal XLSX with full calculation
- `{PROTOCOL}_Quote_Client.xlsx`  — Client-facing XLSX proposal

---

## Before You Begin — Read Reference File

Read `references/pricing_model.ini` for build fee rates and config.
The Google Drive file ID for live subscription rates is in `[google_drive]`.

---

## Step 1: Fetch Live Subscription Rates from Google Drive

**Always attempt to fetch live rates before calculating.**

```python
# The pricing spreadsheet file ID is in pricing_model.ini [google_drive]
file_id = "1wMtw9YbM0ctILcgJ5StYgVyvD0OcZp_jHASDQdruWqA"
```

Use the Google Drive MCP tool:
- Tool: `Google Drive:read_file_content`
- Input: `{"fileId": "1wMtw9YbM0ctILcgJ5StYgVyvD0OcZp_jHASDQdruWqA"}`

Then parse the result:
```python
from pricing_engine import parse_live_rates
live_rates = parse_live_rates(drive_result['fileContent'])
# live_rates = {"core_edc.commercial": 2600.0, "insight.commercial": 750.0, ...}
```

If Drive fetch fails or returns empty, set `live_rates = None`.
The engine will fall back to rates in `pricing_model.ini` automatically.
Always note in the chat whether live or fallback rates were used.

---

## Step 2: Read the Pricing Summary Input

The input is the JSON output from `protocol-to-pricing-summary`.
Key fields consumed by this skill:

```json
{
  "study_meta": {
    "protocol_number":             "PrTK05",
    "customer_segment":            "COMMERCIAL",
    "volume_studies":              1,
    "total_study_duration_months": 24
  },
  "review_flags": { ... }
}
```

**`customer_segment`** — COMMERCIAL | ACADEMIC | LOW_MARKET
  Set by protocol-to-pricing-summary based on sponsor/customer research.

**`volume_studies`** — number of studies in this contract
  Set by protocol-to-pricing-summary based on known contract context.

**`total_study_duration_months`** — drives contract_years and subscription total
  Extracted from protocol estimated dates.

---

## Step 3: Run the Pricing Calculation

```python
from pricing_engine import calculate_quote

quote = calculate_quote(
    pricing_summary_dict,
    live_rates=live_rates   # None if Drive unavailable
)
```

**Pricing logic applied:**

1. **Build fee** — flagged items (excl. choice_list_review) × 1 hr × $275
2. **Contingency** — 20% on (build hours + 40 PM hours), rounded to nearest hr
3. **PM** — fixed 40 hrs × $275
4. **Contract years** — `ceil(duration_months / 12)`
5. **Volume discount** — from table in ini (studies × years)
6. **Bundle check** — if Core EDC + Insight both present → Core Bundle price
7. **Platform discount** — 20% multiplicative if commercial AND
   (Core EDC + Insight without bundle) + ≥1 more module
8. **Final monthly** — `list_price × (1 - vol_disc) × (1 - plat_disc)`
9. **Module total** — `monthly × duration_months`

---

## Step 4: Generate Output Files

```python
from generate_quote_pdf  import build_quote_pdfs
from generate_quote_xlsx import build_quote_xlsx

protocol = quote['study_meta'].get('protocol_number', 'STUDY')
build_quote_pdfs(
    quote,
    f"/mnt/user-data/outputs/{protocol}_Quote_Internal.pdf",
    f"/mnt/user-data/outputs/{protocol}_Quote_Client.pdf"
)
build_quote_xlsx(
    quote,
    f"/mnt/user-data/outputs/{protocol}_Quote_Internal.xlsx",
    f"/mnt/user-data/outputs/{protocol}_Quote_Client.xlsx"
)
```

---

## Step 5: Present and Report

Use `present_files` to share all four files.

Report in chat:
- Whether live or fallback rates were used
- Segment, studies, contract years
- Volume discount %, platform discount % (if any), bundle applied (yes/no)
- Build fee total
- Each module name, net monthly, total
- Grand total

---

## Internal vs Client differences

| Element | Internal | Client |
|---------|----------|--------|
| Contingency | Shown as separate line | Absorbed into specialist hrs |
| Subscription discounts | List price, vol%, plat%, net | Net monthly only |
| Rates source | Shown (live/fallback) | Not shown |
| Flag analysis | Full category breakdown | Not shown |
| Confidential banner | Yes | No |
| Calculation footnotes | Full | Clean |

---

## When the Spreadsheet Changes

The Google Drive fetch in Step 1 automatically picks up any changes to
the pricing spreadsheet without any skill update needed. The skill reads
the live sheet on every run. If the structure of the rate table changes
significantly, update `parse_live_rates()` in `pricing_engine.py` and
repackage the skill.

---

## Segment Classification Rules (for protocol-to-pricing-summary)

The pricing summary skill should classify the customer as:

- **COMMERCIAL** — Large pharma, CRO, for-profit biotech/device with revenue >$25M
- **ACADEMIC** — Universities, hospitals, academic medical centers, non-profits
- **LOW_MARKET** — Startups, emerging device/diagnostic companies,
  pre-revenue or <$25M annual revenue
