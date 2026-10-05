"""vocab_attach: only the CSVs a form references are attached to its upload."""
from pathlib import Path

import openpyxl

from vocab_attach import csvs_to_attach, referenced_csvs


def _form(path, types):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "survey"
    ws.append(["type", "name", "label"])
    for i, t in enumerate(types):
        ws.append([t, f"Q{i}", f"Question {i}"])
    wb.create_sheet("choices").append(["list_name", "name", "label"])
    wb.save(path)


def test_only_referenced_csvs_are_attached(tmp_path):
    f = tmp_path / "CM.xlsx"
    _form(f, ["text", "select_one_from_file rxnorm_demo.csv", "select_multiple_from_file snomed_demo.csv", "date"])
    siblings = [tmp_path / n for n in ("rxnorm_demo.csv", "snomed_demo.csv", "loinc_demo.csv", "ucum_units_demo.csv")]
    assert referenced_csvs(f) == {"rxnorm_demo.csv", "snomed_demo.csv"}
    assert [p.name for p in csvs_to_attach(f, siblings)] == ["rxnorm_demo.csv", "snomed_demo.csv"]


def test_form_without_lookups_gets_no_csv(tmp_path):
    f = tmp_path / "VS.xlsx"
    _form(f, ["text", "decimal", "select_one yn"])
    assert csvs_to_attach(f, [tmp_path / "rxnorm_demo.csv"]) == []


def test_reference_to_a_missing_sibling_is_ignored(tmp_path):
    f = tmp_path / "X.xlsx"
    _form(f, ["select_one_from_file ghost.csv"])
    assert csvs_to_attach(f, [tmp_path / "rxnorm_demo.csv"]) == []
