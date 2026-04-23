---
name: pricing-quote
description: >
  Reads the Protocol Summary output from the protocol-analysis skill and
  applies the OpenClinica pricing model to generate a quote. Outputs four
  files: internal PDF, client PDF, internal XLSX, client XLSX. Use this
  skill whenever a user asks to generate a quote, proposal, or pricing
  estimate from a protocol summary. Always run protocol-analysis BEFORE
  this skill.
---

# Pricing Quote Skill

## Purpose

Apply the OpenClinica pricing model to a Protocol Summary and generate
professional quote documents in PDF and XLSX format — both internal
(full breakdown) and client-facing (summary) versions.

**Outputs — 4 files per run:**
- `{PROTOCOL}_Quote_Internal.pdf` — Full breakdown with flag analysis, discounts
- `{PROTOCOL}_Quote_Client.pdf`   — Clean proposal without internal pricing detail
- `{PROTOCOL}_Quote_Internal.xlsx` — Internal XLSX with full calculation
- `{PROTOCOL}_Quote_Client.xlsx`  — Client-facing XLSX proposal

---

## Before You Begin — Read Reference File

Read `references/pricing_model.ini` for all rates, discount tables, and config.

**Rates are baked into the ini file (Option B).** No Google Drive fetch is
needed or performed. The `rates_effective_date` field in `[rates]` shows
when the rates were last updated. This date appears on internal quotes.

When rates change: update `rates_effective_date` and the relevant values in
the ini, then re-upload the skill via the Skills API.

---

## Step 1: Read the Protocol Summary Input

The input is the Protocol Summary JSON output from the `protocol-analysis` skill.
Key fields consumed:

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
**`volume_studies`** — number of studies in this contract
**`total_study_duration_months`** — drives contract_years and subscription total

---

## Step 2: Run the Pricing Calculation

```python
from pricing_engine import calculate_quote

quote = calculate_quote(protocol_summary_dict)
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

## Step 3: Generate Output Files

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

## Step 4: Present and Report

Use `present_files` to share all four files.

Report in chat:
- Rates effective date (from ini)
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
| Rates effective date | Shown | Not shown |
| Flag analysis | Full category breakdown | Not shown |
| Confidential banner | Yes | No |
| Calculation footnotes | Full | Clean |

---

## Segment Classification Rules

The protocol-analysis skill classifies the customer as:

- **COMMERCIAL** — Large pharma, CRO, for-profit biotech/device with revenue >$25M
- **ACADEMIC** — Universities, hospitals, academic medical centers, non-profits
- **LOW_MARKET** — Startups, emerging device/diagnostic companies,
  pre-revenue or <$25M annual revenue

---

## When Rates Change

1. Update `rates_effective_date` in `references/pricing_model.ini`
2. Update the relevant rate values in the same file
3. Re-upload the skill via the Skills API (one command, ~5 minutes)

The Google Sheet remains the team's working reference for pricing discussions.
The ini is the source of truth for quote generation.
