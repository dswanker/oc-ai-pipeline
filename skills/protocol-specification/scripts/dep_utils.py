"""
dep_utils.py — Dependency extraction utilities for EDC structure outputs.

Extracts [FormOID].[ItemOID] dependency references from:
  1. Declared cross_form_dependencies entries in the form JSON
  2. XPath strings in calculation, constraint, and relevant columns

Used by generate_pdf.py, generate_xlsx.py, and the JSON assembly step.
"""

import re


# ── XPath patterns that indicate a cross-form reference ──────────────────────
# Matches ItemOID='FORM.FIELD' patterns
_ITEM_OID_RE = re.compile(r"@ItemOID='([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)'")

# Matches FormOID='FORM' then later ItemOID='FORM.FIELD' (already captured above)
# Also matches pulldata('labranges',...) and pulldata('{study}_tpt',...) — we skip those
_PULLDATA_RE = re.compile(r"pulldata\('([^']+)'")

# Matches instance('clinicaldata') references — confirms it's a cross-form ref
_CLINICALDATA_RE = re.compile(r"instance\('clinicaldata'\)")

# Fields to scan for XPath expressions
XPATH_FIELDS = ["calculation", "constraint", "relevant", "required", "default"]


def extract_row_dependencies(row: dict) -> list:
    """
    Extract [FormOID].[ItemOID] dependencies from a single survey row.
    Scans calculation, constraint, relevant, required, and default fields.
    Returns a sorted deduplicated list of 'FORM.FIELD' strings.
    """
    deps = set()

    for field in XPATH_FIELDS:
        val = str(row.get(field, "") or "")
        if not val:
            continue

        # Only process strings that reference clinicaldata (cross-form)
        if not _CLINICALDATA_RE.search(val):
            continue

        # Extract ItemOID references — these are in 'FORM.FIELD' format
        for form_oid, item_name in _ITEM_OID_RE.findall(val):
            deps.add(f"{form_oid}.{item_name}")

    return sorted(deps)


def extract_declared_dependencies(form: dict) -> list:
    """
    Extract dependencies from the cross_form_dependencies array.
    Returns a sorted deduplicated list of 'SOURCE_FORM.SOURCE_FIELD' strings.
    """
    deps = set()
    for dep in form.get("cross_form_dependencies", []):
        src_form  = dep.get("source_form", "")
        src_field = dep.get("source_field", "")
        if src_form and src_field:
            deps.add(f"{src_form}.{src_field}")
    return sorted(deps)


def extract_all_form_dependencies(form: dict) -> list:
    """
    Extract ALL dependencies for a form — both declared and XPath-extracted.
    Returns a sorted deduplicated list of 'FORM.FIELD' strings.
    """
    deps = set()

    # Source 1: declared cross_form_dependencies
    for d in extract_declared_dependencies(form):
        deps.add(d)

    # Source 2: scan all survey rows
    for row in form.get("survey", []):
        for d in extract_row_dependencies(row):
            deps.add(d)

    return sorted(deps)


def annotate_survey_with_dependencies(survey: list) -> list:
    """
    Add a 'dependencies' key to each survey row containing the
    list of [FormOID].[ItemOID] references found in that row.
    Returns the annotated survey list (modifies in place and returns).
    """
    for row in survey:
        row["dependencies"] = extract_row_dependencies(row)
    return survey


def format_deps_short(deps: list, max_items: int = 4) -> str:
    """
    Format a dependency list for display in a table cell.
    Shows up to max_items, appends '+N more' if truncated.
    """
    if not deps:
        return ""
    shown = deps[:max_items]
    result = ", ".join(shown)
    if len(deps) > max_items:
        result += f" +{len(deps) - max_items} more"
    return result


def format_deps_full(deps: list) -> str:
    """Format full dependency list, one per line."""
    return "\n".join(deps) if deps else ""


if __name__ == "__main__":
    # Quick test
    test_row = {
        "type": "calculate",
        "name": "AGE_CF",
        "calculation": (
            "instance('clinicaldata')/ODM/ClinicalData/SubjectData/"
            "StudyEventData[@StudyEventOID='SE_BASELINE']/"
            "FormData[@FormOID='DM']/"
            "ItemGroupData[@ItemGroupOID='DM.DM']/"
            "ItemData[@ItemOID='DM.AGE']/@Value"
        ),
    }
    deps = extract_row_dependencies(test_row)
    print(f"Row deps: {deps}")  # Should be ['DM.AGE']

    test_row2 = {
        "type": "calculate",
        "name": "EXDAT_CF",
        "calculation": (
            "instance('clinicaldata')/ODM/ClinicalData/SubjectData/"
            "StudyEventData[@StudyEventOID='SE_C1']/"
            "FormData[@FormOID='EX']/"
            "ItemGroupData[@ItemGroupOID='EX.EX']/"
            "ItemData[@ItemOID='EX.EXDAT']/@Value"
        ),
    }
    deps2 = extract_row_dependencies(test_row2)
    print(f"Row deps2: {deps2}")  # Should be ['EX.EXDAT']

    # Test with pulldata — should not return deps
    test_row3 = {
        "type": "calculate",
        "name": "TPTCALC",
        "calculation": "pulldata('prtk05_tpt','timepoint','event',${EVENT_CF})",
    }
    deps3 = extract_row_dependencies(test_row3)
    print(f"Row deps3 (pulldata, expect empty): {deps3}")  # Should be []
