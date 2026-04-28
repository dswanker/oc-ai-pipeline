"""
ODM XML parser.

Implementation notes:

* CDISC ODM 1.3 spec: https://www.cdisc.org/standards/data-exchange/odm
* Parses defensively — tolerates missing optional fields (sponsor, etc.)
  rather than raising. The fingerprint extractor downstream is robust to
  partial input; the parser's job is to surface what's there, not enforce
  schema completeness.
* Security — XML can be a vector for entity-expansion / XXE attacks.
  We use lxml with ``resolve_entities=False``, ``no_network=True``,
  and ``huge_tree=False``, which collectively neutralize the standard
  attacks. Don't relax these without reading the lxml security FAQ.
* Async signature — kept to match ``FormParser.parse``. lxml itself is
  sync; for the file sizes we expect (≤ a few MB) running in-thread is
  fine. If we ever need to parse multi-hundred-MB exports, wrap the
  parse call in ``asyncio.to_thread``.
"""
from __future__ import annotations

import re
from typing import Any

from lxml import etree

from core.form_parser.base import (
    FormDef,
    FormFormat,
    FormGroup,
    FormItem,
    FormParser,
    ParsedForm,
)

# CDISC ODM 1.3 default namespace + the OpenClinica vendor extension.
_ODM_NS = "http://www.cdisc.org/ns/odm/v1.3"
_OC_NS = "http://www.openclinica.org/ns/odm_ext_v130/v3.1"
_NS = {"odm": _ODM_NS, "oc": _OC_NS}

# Form OID / name → CDASH domain mapping.
# Used to populate FormItem.domain for downstream filtering / retrieval.
# Keep the keys uppercase. We match against (form OID without the F_
# prefix) and against the form's Name attribute, taking the first hit.
_DOMAIN_BY_KEYWORD: dict[str, str] = {
    "DM": "DM",                     # Demographics
    "DEMOG": "DM",
    "IE": "IE",                     # Inclusion/Exclusion
    "INCLUSION": "IE",
    "EXCLUSION": "IE",
    "VS": "VS",                     # Vital Signs
    "VITAL": "VS",
    "CM": "CM",                     # Concomitant Meds
    "CONMED": "CM",
    "CONCOMITANT": "CM",
    "AE": "AE",                     # Adverse Events
    "ADVERSE": "AE",
    "EX": "EX",                     # Exposure
    "EXPOSURE": "EX",
    "LB": "LB",                     # Labs
    "LAB": "LB",
    "DS": "DS",                     # Disposition
    "DISPOSITION": "DS",
    "PE": "PE",                     # Physical Exam
    "PHYSICAL": "PE",
    "MH": "MH",                     # Medical History
    "MEDICAL HISTORY": "MH",
    "PSA": "LB",                    # PSA is technically a lab
    "BIOMARKER": "LB",
    "BIOSPECIMEN": "LB",
}

# Patterns we'll try (in order) on free-text fields when looking for
# sponsor identity. Anchored to begin a match — we take the first capture.
_SPONSOR_PATTERNS = [
    re.compile(r"\bsponsor[:\s]+([^.\n]+?)(?:[.\n]|$)", re.IGNORECASE),
    re.compile(r"\bsponsored\s+by[:\s]+([^.\n]+?)(?:[.\n]|$)", re.IGNORECASE),
]


class ODMXMLParser(FormParser):
    """Parses a CDISC ODM 1.3 XML export into a ParsedForm."""

    format = FormFormat.ODM_XML

    async def parse(self, data: bytes, *, filename: str | None = None) -> ParsedForm:
        root = self._parse_xml(data)

        # The ODM root may contain multiple <Study> elements in theory.
        # In practice OpenClinica exports emit one. We take the first.
        study = root.find("odm:Study", _NS)
        if study is None:
            raise ValueError("ODM document contains no <Study> element")

        study_oid = study.get("OID")
        global_vars = study.find("odm:GlobalVariables", _NS)
        study_name = _text(global_vars, "odm:StudyName") if global_vars is not None else None
        protocol_name = _text(global_vars, "odm:ProtocolName") if global_vars is not None else None
        study_description = (
            _text(global_vars, "odm:StudyDescription") if global_vars is not None else None
        )

        sponsor = self._extract_sponsor(study, study_description)

        # Index every definition by OID so we can resolve refs in O(1)
        # while walking events / forms.
        item_defs = self._index_items(study)
        group_defs = self._index_item_groups(study, item_defs)
        form_defs = self._build_forms(study, group_defs)

        raw_metadata: dict[str, Any] = {
            "study_oid": study_oid,
            "protocol_name": protocol_name,
            "study_description": study_description,
            "form_count": len(form_defs),
            "item_count": sum(len(g.items) for f in form_defs for g in f.groups),
            "study_events": [
                {"oid": ev.get("OID"), "name": ev.get("Name")}
                for ev in study.findall(".//odm:StudyEventDef", _NS)
            ],
        }

        # Attempt to pull additional structured metadata from the
        # OpenClinica vendor extension. Available iff the ODM was
        # exported with extensions on (typical for OC instances).
        oc_details = self._extract_openclinica_details(study)
        if oc_details:
            raw_metadata["openclinica_details"] = oc_details

        return ParsedForm(
            source_format=FormFormat.ODM_XML,
            study_oid=study_oid,
            study_name=study_name,
            sponsor=sponsor,
            forms=form_defs,
            raw_metadata=raw_metadata,
        )

    # ─── XML loading ──────────────────────────────────────────────

    @staticmethod
    def _parse_xml(data: bytes) -> etree._Element:
        """Parse XML safely (no entity expansion, no network)."""
        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            huge_tree=False,
            remove_blank_text=False,
        )
        try:
            return etree.fromstring(data, parser)
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"Invalid ODM XML: {exc}") from exc

    # ─── Sponsor extraction ───────────────────────────────────────

    def _extract_sponsor(
        self,
        study: etree._Element,
        study_description: str | None,
    ) -> str | None:
        """
        Try to recover a sponsor name from any of three locations,
        in order of preference:

          1. ``OpenClinica:Sponsor`` element (vendor extension)
          2. Free-text patterns in StudyDescription
             (e.g. "Sponsor: Acme Therapeutics")
          3. None
        """
        # 1. OpenClinica vendor extension
        sponsor_el = study.find(".//oc:Sponsor", _NS)
        if sponsor_el is not None and sponsor_el.text:
            text = sponsor_el.text.strip()
            if text:
                return text

        # 2. Pattern match in StudyDescription
        if study_description:
            for pat in _SPONSOR_PATTERNS:
                m = pat.search(study_description)
                if m:
                    return m.group(1).strip()

        return None

    @staticmethod
    def _extract_openclinica_details(study: etree._Element) -> dict[str, str] | None:
        """Pull all OpenClinica:StudyDescriptionParameters children as a flat dict."""
        params = study.find(".//oc:StudyDescriptionParameters", _NS)
        if params is None:
            return None
        out: dict[str, str] = {}
        for child in params:
            # Strip namespace from the tag name — keys are easier to read.
            local = etree.QName(child).localname
            if child.text and child.text.strip():
                out[local] = child.text.strip()
        return out or None

    # ─── Definition indexing ──────────────────────────────────────

    @staticmethod
    def _index_items(study: etree._Element) -> dict[str, FormItem]:
        """OID → FormItem for every ``<ItemDef>`` in the study."""
        out: dict[str, FormItem] = {}
        for item_def in study.findall(".//odm:ItemDef", _NS):
            oid = item_def.get("OID") or ""
            name = item_def.get("Name") or ""
            data_type = item_def.get("DataType") or ""

            label = ""
            question = item_def.find("odm:Question", _NS)
            if question is not None:
                label = _text(question, "odm:TranslatedText") or ""

            # If a CodeListRef is present and the DataType isn't explicit
            # about it, treat as a coded field.
            has_codelist = item_def.find("odm:CodeListRef", _NS) is not None
            if has_codelist and data_type == "text":
                data_type = "select_one"

            out[oid] = FormItem(
                oid=oid,
                name=name,
                label=label,
                data_type=data_type,
                domain=None,  # set later, when we know the form context
            )
        return out

    @staticmethod
    def _index_item_groups(
        study: etree._Element,
        item_defs: dict[str, FormItem],
    ) -> dict[str, FormGroup]:
        """OID → FormGroup, with items resolved from ItemRef → ItemDef."""
        out: dict[str, FormGroup] = {}
        for group_def in study.findall(".//odm:ItemGroupDef", _NS):
            oid = group_def.get("OID") or ""
            name = group_def.get("Name") or ""
            items: list[FormItem] = []

            # Order by OrderNumber when present; ItemRefs without one go
            # at the end in document order.
            refs = list(group_def.findall("odm:ItemRef", _NS))
            refs.sort(key=lambda r: _to_int(r.get("OrderNumber")))
            for ref in refs:
                ref_oid = ref.get("ItemOID") or ""
                if ref_oid in item_defs:
                    items.append(item_defs[ref_oid])
                # Silently skip dangling refs — common in partial exports.

            out[oid] = FormGroup(oid=oid, name=name, items=items)
        return out

    @staticmethod
    def _build_forms(
        study: etree._Element,
        group_defs: dict[str, FormGroup],
    ) -> list[FormDef]:
        """Walk FormDefs, resolve ItemGroupRefs, infer CDASH domain per form."""
        forms: list[FormDef] = []
        for form_def in study.findall(".//odm:FormDef", _NS):
            oid = form_def.get("OID") or ""
            name = form_def.get("Name") or ""
            title = name  # ODM doesn't separate name and title

            domain = _infer_domain(oid, name)

            groups: list[FormGroup] = []
            refs = list(form_def.findall("odm:ItemGroupRef", _NS))
            refs.sort(key=lambda r: _to_int(r.get("OrderNumber")))
            for ref in refs:
                ref_oid = ref.get("ItemGroupOID") or ""
                src_group = group_defs.get(ref_oid)
                if src_group is None:
                    continue

                # Stamp the inferred domain onto each item in the group.
                # This lets the fingerprint extractor and retrieval code
                # filter by CDASH domain without re-parsing.
                items_with_domain = [
                    FormItem(
                        oid=it.oid,
                        name=it.name,
                        label=it.label,
                        data_type=it.data_type,
                        domain=domain,
                    )
                    for it in src_group.items
                ]
                groups.append(FormGroup(oid=src_group.oid, name=src_group.name, items=items_with_domain))

            forms.append(FormDef(oid=oid, name=name, title=title, groups=groups))

        # Stable ordering of forms — by OrderNumber on first matching
        # StudyEventDef/FormRef if present, else document order.
        return forms


# ─── Module-level helpers ─────────────────────────────────────────

def _text(parent: etree._Element | None, xpath: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(xpath, _NS)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _to_int(s: str | None) -> int:
    """OrderNumber → int, with missing/garbage values sorting last."""
    try:
        return int(s) if s is not None else 999_999
    except ValueError:
        return 999_999


def _infer_domain(form_oid: str, form_name: str) -> str | None:
    """
    Infer a CDASH domain from a form's OID or Name.

    Strategy: strip the F_ prefix from the OID if present, then try to
    match each known keyword as a token. Fall back to scanning the
    form name in upper case.
    """
    oid_stripped = form_oid.removeprefix("F_").upper()
    name_upper = form_name.upper()

    # OID-based: prefer exact prefix match (F_DM → DM)
    for keyword, domain in _DOMAIN_BY_KEYWORD.items():
        if oid_stripped == keyword or oid_stripped.startswith(keyword + "_"):
            return domain

    # Name-based: substring match, longest-keyword-first to avoid
    # false hits (e.g. "Vital Signs" should match VITAL before VS).
    for keyword in sorted(_DOMAIN_BY_KEYWORD, key=len, reverse=True):
        if keyword in name_upper:
            return _DOMAIN_BY_KEYWORD[keyword]

    return None
