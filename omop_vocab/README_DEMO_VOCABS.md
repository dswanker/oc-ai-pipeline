# Demonstration vocabularies (real codes, partial, not clinically reviewed)

These four lists power the type-ahead lookups on the BioIVT forms (`select_one_from_file` / `select_multiple_from_file`
with `minimal autocomplete`). The **stored value is the real code**, the label is the term staff see and search.
Every list starts with an `OTHER` row so a term that is not in the list can still be recorded (the form then shows an
"Other (not in list), specify" box and keeps the verbatim text).

| File | Entries | Source | What it contains |
|---|---|---|---|
| `rxnorm_demo.csv` | 9,089 | NLM RxNav *Prescribe* API | Prescribable RxNorm ingredients and brand names with real RxCUIs (names filtered to plain drug names) |
| `snomed_demo.csv` | 7,137 | HL7 public terminology server (tx.fhir.org) | Real SNOMED CT concept ids for disease families relevant to BioIVT (autoimmune, cancers, neurology, cardiometabolic, infectious ...) |
| `loinc_demo.csv` | 3,500 | NLM Clinical Tables (LOINC) | Real LOINC codes for quantitative lab tests on ordinary specimens (method-neutral names preferred) |
| `ucum_units_demo.csv` | 67 | curated | Common UCUM unit codes |

Regenerate with `.venv/bin/python3 omop_vocab/build_demo_vocabs.py` (needs internet; no keys).

## What these are NOT
* Not the full vocabularies and not a clinical mapping: terms were chosen by name filters and relevance, not reviewed by a clinician.
* Not licensed for production use as shipped. Confirm licences before real data: SNOMED CT (national/UMLS licence), LOINC (free licence with attribution), RxNorm (public, with NLM attribution).
* Not a replacement for the Option C mapping work (generic-to-brand, ATC classes, hierarchies, historical-data coding).
* The earlier placeholder lists (`*_sample.csv`, codes like `RXTEST-00001`) are no longer used by the BioIVT conventions.

## Attribution
* This material contains content from LOINC (http://loinc.org). LOINC is copyright Regenstrief Institute, Inc. and the LOINC Committee and is available at no cost under the license at http://loinc.org/license. LOINC is a registered United States trademark of Regenstrief Institute, Inc.
* This material includes SNOMED Clinical Terms (SNOMED CT), used by permission of SNOMED International. SNOMED CT is a registered trademark of SNOMED International. A licence is required for production use.
* RxNorm is a registered trademark of the U.S. National Library of Medicine (NLM). This product uses publicly available data courtesy of NLM, NIH, DHHS. NLM is not responsible for the product and does not endorse or recommend it.
* UCUM (Unified Code for Units of Measure) is copyright Regenstrief Institute, Inc. and is free to use.

## Size
OpenClinica's Reference Guide recommends `minimal autocomplete` for long lists. There is no documented hard row limit, but
very large lists can be slow, so these are kept to a few thousand entries each. Behaviour at this size in OC4 has to be
confirmed in a real form upload.
