"""Helpers so every generated document can show the type-ahead lookup lists (RxNorm, SNOMED CT, LOINC, UCUM).

The lists live in separate CSV files (spec["_omop_vocab_files"], and forms/*.csv in the build ZIP), so documents built from the
form's own choices would otherwise show only the question, not the list behind it.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Tuple

_REF = re.compile(r"select_(?:one|multiple)_from_file\s+(\S+?\.csv)", re.I)
TITLES = {
    "rxnorm_demo.csv": "RxNorm drug names",
    "snomed_demo.csv": "SNOMED CT diagnoses",
    "loinc_demo.csv": "LOINC laboratory tests",
    "ucum_units_demo.csv": "UCUM units",
    "snomed_procedures_demo.csv": "SNOMED CT procedures",
    "snomed_body_demo.csv": "SNOMED CT body structures",
    "icdo3_topography_demo.csv": "ICD-O-3 tumour sites",
}


def title(filename: str) -> str:
    return TITLES.get(filename, filename)


def parse_csv(text: str) -> List[Tuple[str, str]]:
    rows = list(csv.reader(io.StringIO(text)))[1:]
    return [(r[0], r[1]) for r in rows if len(r) >= 2]


def external_lists(spec: dict) -> Dict[str, List[Tuple[str, str]]]:
    return {fn: parse_csv(txt) for fn, txt in (spec.get("_omop_vocab_files") or {}).items()}


def form_list_names(form: dict) -> List[str]:
    seen: List[str] = []
    for row in form.get("survey") or []:
        m = _REF.search(str(row.get("type", "")))
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def sample(rows: List[Tuple[str, str]], n: int = 36) -> List[Tuple[str, str]]:
    """The OTHER row plus evenly spaced entries, so the sample shows the variety of the whole list."""
    other = [r for r in rows if r[0] == "OTHER"]
    rest = [r for r in rows if r[0] != "OTHER"]
    k = max(n - len(other), 1)
    step = max(len(rest) // k, 1)
    return other + rest[::step][:k]
