"""
odm_reader.py — CDISC ODM 1.3.x XML reader for oc-ai-pipeline migration

Parses an ODM XML export from any source EDC system and produces a
normalised intermediate dict (OdmStudy) that odm_to_spec.py can then
transform into the OC4 Study Spec JSON.

Supports:
  - ODM 1.3.0, 1.3.1, 1.3.2 (namespace-aware and namespace-stripped)
  - MetaDataVersion with StudyEventDef, FormDef, ItemGroupDef, ItemDef,
    CodeList, MeasurementUnit
  - Vendor extensions: Medidata Rave, Viedoc, Oracle InForm, Castor,
    REDCap ODM exports (best-effort attribute harvesting)
  - ClinicalData / SubjectData for Phase 2 data migration (separate parse path)

Returns:
  parse_odm_metadata(xml_bytes) -> OdmStudy (dict)
  parse_odm_clinical_data(xml_bytes) -> list[OdmSubject]

OdmStudy structure
──────────────────
{
  "odm_version":        str,          # e.g. "1.3.2"
  "source_system":      str,          # detected vendor or "UNKNOWN"
  "source_system_version": str,
  "file_oid":           str,
  "file_type":          str,          # "Snapshot" | "Transactional"
  "creation_datetime":  str,
  "study": {
    "oid":              str,
    "name":             str,
    "description":      str,
    "protocol_name":    str,
    "metadata_version_oid": str,
    "metadata_version_name": str,
  },
  "events": [           # StudyEventDef rows
    {
      "oid":            str,          # e.g. "SCREEN"
      "name":           str,
      "repeating":      bool,
      "event_type":     str,          # "Scheduled"|"Unscheduled"|"Common"
      "form_refs":      [str],        # FormDef OIDs in order
      "vendor":         dict,         # raw vendor extension attrs
    }
  ],
  "forms": [            # FormDef rows
    {
      "oid":            str,
      "name":           str,
      "repeating":      bool,
      "item_group_refs": [str],       # ItemGroupDef OIDs in order
      "vendor":         dict,
    }
  ],
  "item_groups": [      # ItemGroupDef rows
    {
      "oid":            str,
      "name":           str,
      "repeating":      bool,
      "item_refs":      [             # ordered
        {"oid": str, "mandatory": bool, "order": int}
      ],
      "vendor":         dict,
    }
  ],
  "items": [            # ItemDef rows
    {
      "oid":            str,
      "name":           str,
      "data_type":      str,          # ODM DataType: text|integer|float|date|...
      "length":         int|None,
      "significant_digits": int|None,
      "label":          str,          # Question/TranslatedText
      "description":    str,
      "comment":        str,
      "cdash_alias":    str,          # CDASH variable name if present
      "sdtm_alias":     str,          # SDTM variable name if present
      "codelist_ref":   str|None,     # CodeList OID
      "units":          [str],        # MeasurementUnit labels
      "range_checks":   [             # RangeCheck elements
        {"comparator": str, "check_value": str, "soft_hard": str}
      ],
      "vendor":         dict,
    }
  ],
  "codelists": [        # CodeList rows
    {
      "oid":            str,
      "name":           str,
      "data_type":      str,
      "items": [
        {"coded_value": str, "decode": str, "order": int}
      ],
      "vendor":         dict,
    }
  ],
  "measurement_units": [
    {"oid": str, "name": str, "symbol": str}
  ],
  "protocol": {         # Protocol element — arms, epochs, study structure
    "study_name":       str,
    "arms": [{"oid": str, "name": str}],
    "epochs": [{"oid": str, "name": str, "order": int}],
    "study_event_refs": [{"ref_oid": str, "order": int, "mandatory": bool}],
  },
  "parse_warnings":     [str],        # non-fatal issues found during parse
}
"""

import re
import hashlib
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

# ── ODM namespace URIs ────────────────────────────────────────────────────────

ODM_NS_131  = "http://www.cdisc.org/ns/odm/v1.3"
ODM_NS_130  = "http://www.cdisc.org/ns/odm/v1.3"   # same in practice
ODM_NS_20   = "http://www.cdisc.org/ns/odm/v2.0"

# Vendor extension namespaces (best-effort detection)
VENDOR_NS = {
    "medidata":   "http://www.mdsol.com/ns/odm/metadata",
    "viedoc":     "http://www.viedoc.net/ns/odm",
    "oracle":     "http://www.oracle.com/ns/odm",
    "castor":     "http://www.castoredc.com/ns/odm",
    "redcap":     "https://projectredcap.org",
    "oc3":        "http://www.openclinica.org/ns/odm_ext_v130/v3.1",
    "oc4":        "http://openclinica.org/xforms",
}

# ODM DataType → XLSForm type mapping used by odm_to_spec
DATATYPE_MAP = {
    "text":             "text",
    "string":           "text",
    "integer":          "integer",
    "float":            "decimal",
    "double":           "decimal",
    "decimal":          "decimal",
    "date":             "date",
    "time":             "time",
    "datetime":         "dateTime",
    "partialdate":      "date",
    "partialtime":      "time",
    "partialdatetime":  "dateTime",
    "boolean":          "select_one yn",
    "uri":              "text",
    "base64binary":     "text",
    "hexbinary":        "text",
}


# ── Vendor detection ──────────────────────────────────────────────────────────

def _detect_vendor(root: ET.Element, raw_xml: bytes) -> tuple[str, str]:
    """Return (vendor_name, vendor_version) from root attributes or namespace hints."""
    attrs = root.attrib

    # Check Originator attribute (common in Medidata, Oracle)
    originator = attrs.get("Originator", "").lower()
    if "medidata" in originator or "rave" in originator:
        return "Medidata Rave", attrs.get("SourceSystem", "")
    if "oracle" in originator or "inform" in originator:
        return "Oracle InForm", attrs.get("SourceSystemVersion", "")
    if "viedoc" in originator:
        return "Viedoc", attrs.get("SourceSystemVersion", "")
    if "castor" in originator:
        return "Castor EDC", attrs.get("SourceSystemVersion", "")
    if "redcap" in originator or "REDCap" in originator:
        return "REDCap", attrs.get("SourceSystemVersion", "")
    if "openclinica" in originator.lower():
        return "OpenClinica", attrs.get("SourceSystemVersion", "")
    if "merative" in originator or "zelta" in originator:
        return "Zelta (Merative)", attrs.get("SourceSystemVersion", "")
    if "medrio" in originator:
        return "Medrio", attrs.get("SourceSystemVersion", "")
    if "veeva" in originator or "vault" in originator:
        return "Veeva Vault CDMS", attrs.get("SourceSystemVersion", "")

    # Fallback: sniff namespaces in raw XML
    raw_head = raw_xml[:2000].decode("utf-8", errors="ignore").lower()
    if "mdsol.com" in raw_head:
        return "Medidata Rave", ""
    if "viedoc.net" in raw_head:
        return "Viedoc", ""
    if "oracle.com" in raw_head:
        return "Oracle InForm", ""
    if "castoredc.com" in raw_head:
        return "Castor EDC", ""
    if "projectredcap.org" in raw_head:
        return "REDCap", ""
    if "merative" in raw_head or "zelta" in raw_head:
        return "Zelta (Merative)", ""
    if "openclinica.org" in raw_head or "openclinica.com" in raw_head:
        return "OpenClinica 4", ""

    return "UNKNOWN", attrs.get("SourceSystemVersion", "")


# ── Namespace helpers ─────────────────────────────────────────────────────────

def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix: {http://...}Tag → Tag"""
    return tag.split("}")[-1] if "}" in tag else tag


def _find(el: ET.Element, local_name: str) -> ET.Element | None:
    """Find first child with matching local tag name (namespace-agnostic)."""
    for child in el:
        if _strip_ns(child.tag) == local_name:
            return child
    return None


def _findall(el: ET.Element, local_name: str) -> list[ET.Element]:
    """Find all children with matching local tag name."""
    return [c for c in el if _strip_ns(c.tag) == local_name]


def _translated_text(el: ET.Element | None) -> str:
    """Extract first TranslatedText value from a Question or Description element."""
    if el is None:
        return ""
    tt = _find(el, "TranslatedText")
    if tt is not None and tt.text:
        return tt.text.strip()
    # Some vendors put text directly on the element
    if el.text and el.text.strip():
        return el.text.strip()
    return ""


def _vendor_attrs(el: ET.Element) -> dict:
    """Collect all non-standard (namespaced) attributes into a flat dict."""
    out = {}
    for k, v in el.attrib.items():
        if k.startswith("{"):
            ns_uri, local = k[1:].split("}", 1)
            # Map namespace URI to a short vendor key
            vendor_key = "ext"
            for vname, vns in VENDOR_NS.items():
                if vns in ns_uri:
                    vendor_key = vname
                    break
            out[f"{vendor_key}:{local}"] = v
    return out


# ── Core parse functions ──────────────────────────────────────────────────────

def _parse_protocol(mdv: ET.Element) -> dict:
    """Parse Protocol element inside MetaDataVersion."""
    protocol = _find(mdv, "Protocol")
    result = {
        "study_name": "",
        "arms": [],
        "epochs": [],
        "study_event_refs": [],
    }
    if protocol is None:
        return result

    # StudyName (ODM 2.0 style sometimes)
    sn = _find(protocol, "StudyName")
    if sn is not None:
        result["study_name"] = sn.text or ""

    # Arms
    for arm in _findall(protocol, "Arm"):
        result["arms"].append({
            "oid": arm.get("OID", ""),
            "name": arm.get("Name", ""),
        })

    # Epochs
    for i, epoch in enumerate(_findall(protocol, "Epoch")):
        result["epochs"].append({
            "oid": epoch.get("OID", ""),
            "name": epoch.get("Name", ""),
            "order": i + 1,
        })

    # StudyEventRef — ordered references to events from the protocol
    for ref in _findall(protocol, "StudyEventRef"):
        result["study_event_refs"].append({
            "ref_oid": ref.get("StudyEventOID", ""),
            "order": int(ref.get("OrderNumber", 0) or 0),
            "mandatory": ref.get("Mandatory", "No").lower() == "yes",
        })

    return result


def _parse_events(mdv: ET.Element) -> list[dict]:
    events = []
    for se in _findall(mdv, "StudyEventDef"):
        # FormRef children — ordered
        form_refs = []
        for fr in _findall(se, "FormRef"):
            form_refs.append(fr.get("FormOID", ""))

        events.append({
            "oid":        se.get("OID", ""),
            "name":       se.get("Name", ""),
            "repeating":  se.get("Repeating", "No").lower() == "yes",
            "event_type": se.get("Type", "Scheduled"),  # Scheduled|Unscheduled|Common
            "form_refs":  form_refs,
            "vendor":     _vendor_attrs(se),
        })
    return events


def _parse_forms(mdv: ET.Element) -> list[dict]:
    forms = []
    for fd in _findall(mdv, "FormDef"):
        ig_refs = []
        for igr in _findall(fd, "ItemGroupRef"):
            ig_refs.append(igr.get("ItemGroupOID", ""))

        # Description / Alias
        desc_el = _find(fd, "Description")
        alias_el = _find(fd, "Alias")

        forms.append({
            "oid":              fd.get("OID", ""),
            "name":             fd.get("Name", ""),
            "repeating":        fd.get("Repeating", "No").lower() == "yes",
            "description":      _translated_text(desc_el),
            "alias":            alias_el.get("Name", "") if alias_el is not None else "",
            "item_group_refs":  ig_refs,
            "vendor":           _vendor_attrs(fd),
        })
    return forms


def _parse_item_groups(mdv: ET.Element) -> list[dict]:
    groups = []
    for igd in _findall(mdv, "ItemGroupDef"):
        item_refs = []
        for i, ir in enumerate(_findall(igd, "ItemRef")):
            item_refs.append({
                "oid":       ir.get("ItemOID", ""),
                "mandatory": ir.get("Mandatory", "No").lower() == "yes",
                "order":     int(ir.get("OrderNumber", i + 1) or i + 1),
            })

        desc_el = _find(igd, "Description")
        groups.append({
            "oid":        igd.get("OID", ""),
            "name":       igd.get("Name", ""),
            "repeating":  igd.get("Repeating", "No").lower() == "yes",
            "description": _translated_text(desc_el),
            "item_refs":  item_refs,
            "vendor":     _vendor_attrs(igd),
        })
    return groups


def _parse_items(mdv: ET.Element) -> list[dict]:
    items = []
    for itd in _findall(mdv, "ItemDef"):
        # Question label
        q_el   = _find(itd, "Question")
        desc_el = _find(itd, "Description")

        # CodeListRef
        clr = _find(itd, "CodeListRef")
        codelist_ref = clr.get("CodeListOID", "") if clr is not None else None

        # MeasurementUnitRef
        units = []
        for mur in _findall(itd, "MeasurementUnitRef"):
            units.append(mur.get("MeasurementUnitOID", ""))

        # RangeCheck elements
        range_checks = []
        for rc in _findall(itd, "RangeCheck"):
            cv = _find(rc, "CheckValue")
            range_checks.append({
                "comparator":  rc.get("Comparator", ""),
                "check_value": cv.text.strip() if cv is not None and cv.text else "",
                "soft_hard":   rc.get("SoftHard", "Soft"),
            })

        # Alias elements — CDASH and SDTM contexts
        cdash_alias = ""
        sdtm_alias  = ""
        for alias in _findall(itd, "Alias"):
            ctx = alias.get("Context", "").lower()
            if "cdash" in ctx:
                cdash_alias = alias.get("Name", "")
            elif "sdtm" in ctx:
                sdtm_alias = alias.get("Name", "")

        items.append({
            "oid":                itd.get("OID", ""),
            "name":               itd.get("Name", ""),
            "data_type":          itd.get("DataType", "text").lower(),
            "length":             _int_or_none(itd.get("Length")),
            "significant_digits": _int_or_none(itd.get("SignificantDigits")),
            "label":              _translated_text(q_el),
            "description":        _translated_text(desc_el),
            "comment":            itd.get("Comment", ""),
            "cdash_alias":        cdash_alias,
            "sdtm_alias":         sdtm_alias,
            "codelist_ref":       codelist_ref if codelist_ref else None,
            "units":              units,
            "range_checks":       range_checks,
            "vendor":             _vendor_attrs(itd),
        })
    return items


def _parse_codelists(mdv: ET.Element) -> list[dict]:
    codelists = []
    for cl in _findall(mdv, "CodeList"):
        cl_items = []
        for i, cli in enumerate(_findall(cl, "CodeListItem")):
            decode_el = _find(cli, "Decode")
            cl_items.append({
                "coded_value": cli.get("CodedValue", ""),
                "decode":      _translated_text(decode_el),
                "order":       int(cli.get("OrderNumber", i + 1) or i + 1),
            })
        # Also handle EnumeratedItem (simpler codelist format)
        for i, ei in enumerate(_findall(cl, "EnumeratedItem")):
            cl_items.append({
                "coded_value": ei.get("CodedValue", ""),
                "decode":      ei.get("CodedValue", ""),
                "order":       int(ei.get("OrderNumber", i + 1) or i + 1),
            })

        codelists.append({
            "oid":       cl.get("OID", ""),
            "name":      cl.get("Name", ""),
            "data_type": cl.get("DataType", "text").lower(),
            "items":     sorted(cl_items, key=lambda x: x["order"]),
            "vendor":    _vendor_attrs(cl),
        })
    return codelists


def _parse_measurement_units(study_el: ET.Element) -> list[dict]:
    """BasicDefinitions > MeasurementUnit"""
    units = []
    bd = _find(study_el, "BasicDefinitions")
    if bd is None:
        return units
    for mu in _findall(bd, "MeasurementUnit"):
        sym_el = _find(mu, "Symbol")
        units.append({
            "oid":    mu.get("OID", ""),
            "name":   mu.get("Name", ""),
            "symbol": _translated_text(sym_el),
        })
    return units


def _int_or_none(val: str | None) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ── Public API — metadata parse ───────────────────────────────────────────────

def parse_odm_metadata(xml_bytes: bytes) -> dict:
    """
    Parse ODM XML bytes and return a normalised OdmStudy dict.

    Handles:
    - BOM stripping
    - Namespace-prefixed and namespace-stripped XML
    - Partial/malformed documents (best-effort with warnings)

    Args:
        xml_bytes: Raw bytes of the ODM XML file

    Returns:
        OdmStudy dict (see module docstring for schema)

    Raises:
        ValueError: If the XML cannot be parsed at all
    """
    warnings: list[str] = []

    # Strip UTF-8 BOM if present
    if xml_bytes.startswith(b"\xef\xbb\xbf"):
        xml_bytes = xml_bytes[3:]

    # Parse XML
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        # Attempt to strip namespaces and retry (some exports have malformed NS)
        try:
            cleaned = re.sub(r' xmlns[^"]*"[^"]*"', "", xml_bytes.decode("utf-8", errors="replace"))
            root = ET.fromstring(cleaned.encode("utf-8"))
            warnings.append("Namespace declarations stripped during parse — vendor XML may be non-standard.")
        except ET.ParseError:
            raise ValueError(f"Cannot parse ODM XML: {e}") from e

    root_tag = _strip_ns(root.tag)
    if root_tag != "ODM":
        warnings.append(f"Root element is '{root_tag}', expected 'ODM'. Proceeding anyway.")

    # Detect vendor
    source_system, source_system_version = _detect_vendor(root, xml_bytes)

    # ODM root attributes
    odm_version        = root.get("ODMVersion", "1.3.2")
    file_oid           = root.get("FileOID", "")
    file_type          = root.get("FileType", "Snapshot")
    creation_datetime  = root.get("CreationDateTime", "")

    # Study element
    study_el = _find(root, "Study")
    if study_el is None:
        warnings.append("No <Study> element found in ODM. Metadata will be empty.")
        return {
            "odm_version": odm_version,
            "source_system": source_system,
            "source_system_version": source_system_version,
            "file_oid": file_oid,
            "file_type": file_type,
            "creation_datetime": creation_datetime,
            "study": {},
            "events": [],
            "forms": [],
            "item_groups": [],
            "items": [],
            "codelists": [],
            "measurement_units": [],
            "protocol": {},
            "parse_warnings": warnings,
        }

    study_oid  = study_el.get("OID", "")
    gsd        = _find(study_el, "GlobalVariables")
    study_name = ""
    study_desc = ""
    protocol_name = ""
    if gsd is not None:
        sn_el = _find(gsd, "StudyName")
        sd_el = _find(gsd, "StudyDescription")
        pn_el = _find(gsd, "ProtocolName")
        study_name    = sn_el.text.strip()  if sn_el  is not None and sn_el.text  else ""
        study_desc    = sd_el.text.strip()  if sd_el  is not None and sd_el.text  else ""
        protocol_name = pn_el.text.strip()  if pn_el  is not None and pn_el.text  else ""

    # MetaDataVersion — take first one
    mdv = _find(study_el, "MetaDataVersion")
    if mdv is None:
        warnings.append("No <MetaDataVersion> found. Study structure will be empty.")
        mdv_oid  = ""
        mdv_name = ""
        events      = []
        forms       = []
        item_groups = []
        items       = []
        codelists   = []
        protocol    = {}
    else:
        mdv_oid  = mdv.get("OID", "")
        mdv_name = mdv.get("Name", "")
        protocol    = _parse_protocol(mdv)
        events      = _parse_events(mdv)
        forms       = _parse_forms(mdv)
        item_groups = _parse_item_groups(mdv)
        items       = _parse_items(mdv)
        codelists   = _parse_codelists(mdv)

    measurement_units = _parse_measurement_units(study_el)

    # Integrity checks
    form_oids_in_events = {fr for ev in events for fr in ev["form_refs"]}
    defined_form_oids   = {f["oid"] for f in forms}
    missing_forms = form_oids_in_events - defined_form_oids
    if missing_forms:
        warnings.append(f"StudyEventDef references FormDef OIDs not defined: {sorted(missing_forms)}")

    item_oids_in_groups = {ir["oid"] for ig in item_groups for ir in ig["item_refs"]}
    defined_item_oids   = {i["oid"] for i in items}
    missing_items = item_oids_in_groups - defined_item_oids
    if missing_items:
        warnings.append(f"ItemGroupDef references ItemDef OIDs not defined: {sorted(missing_items)}")

    codelist_refs = {i["codelist_ref"] for i in items if i["codelist_ref"]}
    defined_cl    = {cl["oid"] for cl in codelists}
    missing_cls   = codelist_refs - defined_cl
    if missing_cls:
        warnings.append(f"ItemDef references CodeList OIDs not defined: {sorted(missing_cls)}")

    return {
        "odm_version":            odm_version,
        "source_system":          source_system,
        "source_system_version":  source_system_version,
        "file_oid":               file_oid,
        "file_type":              file_type,
        "creation_datetime":      creation_datetime,
        "study": {
            "oid":                     study_oid,
            "name":                    study_name,
            "description":             study_desc,
            "protocol_name":           protocol_name,
            "metadata_version_oid":    mdv_oid,
            "metadata_version_name":   mdv_name,
        },
        "events":            events,
        "forms":             forms,
        "item_groups":       item_groups,
        "items":             items,
        "codelists":         codelists,
        "measurement_units": measurement_units,
        "protocol":          protocol,
        "parse_warnings":    warnings,
    }


# ── Public API — clinical data parse (Phase 2) ───────────────────────────────

def parse_odm_clinical_data(xml_bytes: bytes) -> list[dict]:
    """
    Parse ClinicalData / SubjectData from an ODM XML file.

    Used in Phase 2 (data migration). Returns a list of subject dicts
    each containing their event + form + item data.

    Returns:
        [
          {
            "subject_key": str,
            "subject_oid": str,
            "site_oid":    str,
            "events": [
              {
                "event_oid":    str,
                "event_repeat": str,
                "forms": [
                  {
                    "form_oid":    str,
                    "form_repeat": str,
                    "item_groups": [
                      {
                        "group_oid":    str,
                        "group_repeat": str,
                        "items": [
                          {"item_oid": str, "value": str}
                        ]
                      }
                    ]
                  }
                ]
              }
            ]
          }
        ]
    """
    if xml_bytes.startswith(b"\xef\xbb\xbf"):
        xml_bytes = xml_bytes[3:]

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"Cannot parse ODM XML for clinical data: {e}") from e

    subjects = []

    for cd in _findall(root, "ClinicalData"):
        for sd in _findall(cd, "SubjectData"):
            subject = {
                "subject_key": sd.get("SubjectKey", ""),
                "subject_oid": sd.get("OID", sd.get("SubjectKey", "")),
                "site_oid":    "",
                "events":      [],
            }

            # SiteRef
            sr = _find(sd, "SiteRef")
            if sr is not None:
                subject["site_oid"] = sr.get("LocationOID", "")

            for sed in _findall(sd, "StudyEventData"):
                event_entry = {
                    "event_oid":    sed.get("StudyEventOID", ""),
                    "event_repeat": sed.get("StudyEventRepeatKey", "1"),
                    "forms":        [],
                }
                for fd in _findall(sed, "FormData"):
                    form_entry = {
                        "form_oid":    fd.get("FormOID", ""),
                        "form_repeat": fd.get("FormRepeatKey", "1"),
                        "item_groups": [],
                    }
                    for igd in _findall(fd, "ItemGroupData"):
                        ig_entry = {
                            "group_oid":    igd.get("ItemGroupOID", ""),
                            "group_repeat": igd.get("ItemGroupRepeatKey", "1"),
                            "items":        [],
                        }
                        for itd in _findall(igd, "ItemData"):
                            ig_entry["items"].append({
                                "item_oid": itd.get("ItemOID", ""),
                                "value":    itd.get("Value", ""),
                            })
                        form_entry["item_groups"].append(ig_entry)
                    event_entry["forms"].append(form_entry)
                subject["events"].append(event_entry)
            subjects.append(subject)

    return subjects


# ── Utility helpers for downstream use ───────────────────────────────────────

def build_item_lookup(odm_study: dict) -> dict[str, dict]:
    """Return {item_oid: item_dict} for fast lookup."""
    return {i["oid"]: i for i in odm_study.get("items", [])}


def build_codelist_lookup(odm_study: dict) -> dict[str, dict]:
    """Return {codelist_oid: codelist_dict} for fast lookup."""
    return {cl["oid"]: cl for cl in odm_study.get("codelists", [])}


def build_form_item_map(odm_study: dict) -> dict[str, list[str]]:
    """
    Return {form_oid: [item_oid, ...]} — all items reachable from each form,
    in the order they appear (group order → item order).
    """
    ig_lookup = {ig["oid"]: ig for ig in odm_study.get("item_groups", [])}
    result = {}
    for form in odm_study.get("forms", []):
        item_oids = []
        for ig_oid in form.get("item_group_refs", []):
            ig = ig_lookup.get(ig_oid)
            if ig is None:
                continue
            for ir in sorted(ig["item_refs"], key=lambda x: x["order"]):
                item_oids.append(ir["oid"])
        result[form["oid"]] = item_oids
    return result


def odm_datatype_to_xlsform(odm_dtype: str, codelist_oid: str | None = None) -> str:
    """Convert ODM DataType + optional codelist presence to XLSForm type string."""
    if codelist_oid:
        return "select_one " + _safe_list_name(codelist_oid)
    return DATATYPE_MAP.get(odm_dtype.lower(), "text")


def _safe_list_name(oid: str) -> str:
    """Produce a safe XLSForm list_name from an OID (alphanumeric + underscores)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", oid)


def summarise(odm_study: dict) -> str:
    """Return a human-readable one-page summary of the parsed ODM study."""
    s = odm_study.get("study", {})
    lines = [
        f"ODM Study Summary",
        f"═════════════════",
        f"  Study name:      {s.get('name', '—')}",
        f"  Protocol name:   {s.get('protocol_name', '—')}",
        f"  Source system:   {odm_study.get('source_system', '—')} "
        f"v{odm_study.get('source_system_version', '—')}",
        f"  ODM version:     {odm_study.get('odm_version', '—')}",
        f"  File type:       {odm_study.get('file_type', '—')}",
        f"  Created:         {odm_study.get('creation_datetime', '—')}",
        f"",
        f"  Events:          {len(odm_study.get('events', []))}",
        f"  Forms:           {len(odm_study.get('forms', []))}",
        f"  Item groups:     {len(odm_study.get('item_groups', []))}",
        f"  Items:           {len(odm_study.get('items', []))}",
        f"  Codelists:       {len(odm_study.get('codelists', []))}",
        f"  Measurement units: {len(odm_study.get('measurement_units', []))}",
    ]
    warnings = odm_study.get("parse_warnings", [])
    if warnings:
        lines.append(f"")
        lines.append(f"  Warnings ({len(warnings)}):")
        for w in warnings:
            lines.append(f"    ⚠  {w}")
    return "\n".join(lines)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, json

    if len(sys.argv) < 2:
        print("Usage: python odm_reader.py <odm_file.xml> [--clinical-data] [--json]")
        sys.exit(1)

    path = sys.argv[1]
    clinical = "--clinical-data" in sys.argv
    as_json  = "--json" in sys.argv

    with open(path, "rb") as f:
        data = f.read()

    if clinical:
        result = parse_odm_clinical_data(data)
        if as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Parsed {len(result)} subjects.")
    else:
        result = parse_odm_metadata(data)
        if as_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(summarise(result))
