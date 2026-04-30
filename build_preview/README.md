# build_preview/

Local-only renderer for the **Build Preview** PDF. Generates a visual
representation of an OC EDC build: a Schedule of Events matrix + every form
rendered in OpenClinica's real Enketo grid theme.

This module **does not call the Anthropic API**. It is pure Python + a small
Node browser bundle running inside headless Chromium via Playwright.

## Public API

```python
from build_preview import render_build_preview

pdf_bytes = render_build_preview(
    study_spec_pdf_bytes,   # bytes of the Study Spec PDF (protocol-analysis output)
    edc_zip_bytes,          # bytes of the EDC Build .zip   (edc-builder output)
    protocol_id_for_filename="ABT-CIP-10601",   # optional; used in temp paths only
)
```

Returns merged PDF bytes ready to upload to a Monday file column.

## How it works

1. Parse the Study Spec PDF for events + form↔event assignments (Section 1, 2, 3, 4)
2. Unzip the EDC build, locate `forms/*.xlsx`
3. Sanitize each XLSForm (strip known `*_PHANTOM` rows from edc-builder)
4. Convert each XLSForm → XForm XML via **pyxform**
5. Launch a single Chromium instance via Playwright
6. For each form: feed XForm to **enketo-transformer** (running in the page),
   get back the real Enketo HTML + model XML, expand `<itemset>` choices
   from the model, render with the real Enketo grid CSS, capture as PDF
7. Render Schedule of Events as a landscape PDF (matrix with X marks)
8. Merge SoE + per-form PDFs into one file

Wall-clock time: **~10 seconds per study** (15 forms). No Claude API tokens used.

## Vendored static assets

`vendor/` contains files that must ship with the deployment because the
build sandbox cannot fetch them at runtime:

| File              | Source                                                          | Purpose                              |
|-------------------|-----------------------------------------------------------------|--------------------------------------|
| `transformer.js`  | `enketo-transformer@4.2.0` web bundle                           | XForm → Enketo HTML conversion       |
| `grid.css`        | Compiled from `enketo-core` `src/sass/grid/grid.scss`           | Real Enketo data-entry styling       |
| `scaffold.html`   | Hand-written; loads `transformer.js` and exposes `__transform`  | Page that Playwright loads           |

To refresh these, see `vendor/REFRESH.md` (TODO if needed).

## Limitations / known issues

- **PHANTOM-row workaround.** edc-builder currently emits unbalanced
  `*_REP_END_PHANTOM` end_group rows that pyxform rejects. This module strips
  them so the preview renders, but the proper fix belongs in edc-builder.
- **PDF parser fragility.** The Study Spec parser relies on PDF text
  extraction. If protocol-analysis ever changes its layout, the parser may
  need updating. Long-term fix: have protocol-analysis emit `study_spec.json`
  alongside the PDF and read JSON instead.
- **Fonts.** The PDF uses Helvetica Neue / Arial fallback. The Microsoft
  Playwright Docker image includes those fonts; bare Linux containers may not.

## Local testing

```bash
# from repo root
python -c "
from build_preview import render_build_preview
spec = open('test_study_spec.pdf','rb').read()
zipb = open('test_edc_build.zip','rb').read()
pdf = render_build_preview(spec, zipb)
open('preview.pdf','wb').write(pdf)
"
```

Requires `playwright install chromium` to have been run once locally.
