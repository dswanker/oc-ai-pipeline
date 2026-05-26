"""
oc4_reader.py — OC4-flavoured ODM XML reader for Pass 2 gap analysis.

Pass 1 gap analysis compares source ODM (whatever vendor) → spec JSON
(what we're about to build). Pass 2 compares source ODM → refined OC4
ODM that OC exports back after the human has reviewed and tweaked the
build. The two readers do almost the same job — except OC4's
multi-select fields use a vendor-namespaced
`<OpenClinica:MultiSelectListRef>` element on ItemDef instead of the
standard `<CodeListRef>`. Standard odm_reader misses that, which
makes multi-select items in an OC4 export look like unbound text and
gap_analysis flags them as unmapped or type-mismatched.

Status: STUB. Pass 1 already works using odm_reader directly; Pass 2
is not yet wired. This module exists so the import paths are ready
when Pass 2 lands — the parsing body is a TODO for a follow-on
session. Today it just delegates to parse_odm_metadata, which is
correct for any OC4 ODM that doesn't use multi-select fields.

Public API
──────────
    parse_oc4_odm_metadata(xml_bytes: bytes) -> dict

Returns the same OdmStudy dict shape as odm_reader.parse_odm_metadata.
See odm_reader's module docstring for the full schema.
"""

from __future__ import annotations

from odm_reader import parse_odm_metadata


def parse_oc4_odm_metadata(xml_bytes: bytes) -> dict:
    """Parse an OC4-exported ODM XML.

    Currently delegates to odm_reader.parse_odm_metadata unchanged.
    Single-select fields with standard `<CodeListRef>` already parse
    correctly; the gap exists only for multi-select fields, which OC4
    declares via a vendor-namespaced element.

    TODO (Pass 2): after delegating to parse_odm_metadata, walk the
    XML again and find every ItemDef carrying
    `<OpenClinica:MultiSelectListRef CodeListOID="..."/>`
    (vendor namespace URI: http://www.openclinica.com/odm). For each
    match:
      1. Set the item's `codelist_ref` to the @CodeListOID.
      2. Mark the item as multi-select (a new bool field on the item
         dict, e.g. `multi_select: True`) so the downstream
         odm_to_spec → gap_analysis path treats it as
         `select_multiple` instead of `select_one`.
      3. Make sure odm_to_spec / gap_analysis honour the new bool —
         right now they look only at codelist_ref presence and infer
         single-select.
    """
    # Delegation is intentional. Replace with the full implementation
    # when Pass 2 wiring is needed.
    return parse_odm_metadata(xml_bytes)


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python oc4_reader.py <odm_file.xml>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1], "rb") as f:
        result = parse_oc4_odm_metadata(f.read())
    print(json.dumps(result, indent=2, default=str))
