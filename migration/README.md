# EDC Migration Module

Transforms clinical study metadata from any source EDC system into the OC4 Study Spec JSON that the existing `oc-ai-pipeline` consumes.

## Structure

```
migration/
  __init__.py
  odm_reader.py      — parses CDISC ODM 1.3.x XML from any source EDC
  odm_to_spec.py     — transforms parsed ODM into OC4 Study Spec JSON
  odm_validator.py   — 3-layer ODM validation with compliance reporting

tests/migration/
  test_migration.py  — 81-test harness (no API calls, no external dependencies)
  fixtures/
    prtk05.xml       — real OC4 export (PrTK05 study)
    synthetic.xml    — synthetic multi-vendor ODM for edge case testing
```

## Running the tests

From the repo root:

```bash
PYTHONPATH=migration python3 tests/migration/test_migration.py -v
```

Expected output: `Ran 81 tests ... OK`

No API keys, no Monday.com connection, and no Railway/pipeline trigger required. Safe to run at any time.

## Validating an ODM file

```bash
PYTHONPATH=migration python3 migration/odm_validator.py path/to/file.xml
PYTHONPATH=migration python3 migration/odm_validator.py path/to/file.xml --verbose
```

Returns exit code 0 if safe to proceed, 1 if validation fails.

## What is tested

| Suite | Coverage |
|---|---|
| `TestOdmReader` | Parse real PrTK05 OC4 export, vendor detection (Medidata, Viedoc, Oracle, Castor, REDCap, OpenClinica), ODM versions 1.3 / 1.3.1 / 1.3.2, BOM handling, integrity warnings, clinical data parse |
| `TestOdmToSpec` | OID normalisation, OC-9 compliance (AE/CM/DV → SE_COMMON), form ID handling, settings schema, survey row structure, round-trip stability, JSON serialisability |
| `TestVendorRegistry` | All current vendors detectable, unknown vendors degrade gracefully, extension pattern documented |
| `TestOdmValidator` | 3-layer validation on real and synthetic files, compliance field status, dangling OID ref detection, malformed XML handling, can_proceed logic, report formatting |

## Validation layers

| Layer | What it checks |
|---|---|
| 1 — XML well-formedness | File is parseable XML. BOM handled. Failure here blocks everything. |
| 2 — ODM structural conformance | Root element, required attributes, ODMVersion, FileType, CreationDateTime format, Study/GlobalVariables/MetaDataVersion presence, minimum event/form/item counts |
| 3 — OID referential integrity | Protocol→StudyEventDef, StudyEventDef→FormDef, FormDef→ItemGroupDef, ItemGroupDef→ItemDef, ItemDef→CodeList. Dangling refs reported as warnings. |

Failures block migration (`can_proceed=False`). Warnings are logged but do not block.

## Compliance fields checked

Covers fields required for GDPR, CFR Part 21 Part 11, and ICH E6(R3) audit trail: `ODMVersion`, `FileOID`, `FileType`, `CreationDateTime`, `ProtocolName`, `StudyName`.

## Adding a new source EDC vendor

1. Open `migration/odm_reader.py`
2. Find the `_detect_vendor()` function
3. Add a detection rule for the new vendor's `Originator` string or namespace
4. Add the vendor to `test_all_current_14_vendors_detectable` in `tests/migration/test_migration.py`
5. Run the tests to confirm

No other files need to change.
