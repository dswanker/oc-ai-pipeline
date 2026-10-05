"""lookup_lists: the helper that lets every generated document show the type-ahead lookup lists."""
from lookup_lists import external_lists, form_list_names, parse_csv, sample, title

CSV = "name,label\nOTHER,Other (not in list)\n5640,Ibuprofen\n8640,Prednisone\n"


def test_parse_skips_header_and_keeps_code_then_term():
    assert parse_csv(CSV) == [("OTHER", "Other (not in list)"), ("5640", "Ibuprofen"), ("8640", "Prednisone")]


def test_forms_report_the_lists_their_questions_use_once_each():
    form = {"survey": [{"type": "text"}, {"type": "select_one_from_file rxnorm_demo.csv"},
                       {"type": "select_multiple_from_file rxnorm_demo.csv"}, {"type": "select_one_from_file snomed_demo.csv"}]}
    assert form_list_names(form) == ["rxnorm_demo.csv", "snomed_demo.csv"]
    assert form_list_names({"survey": [{"type": "select_one yn"}]}) == []


def test_external_lists_come_from_the_spec_and_titles_are_readable():
    assert external_lists({"_omop_vocab_files": {"rxnorm_demo.csv": CSV}})["rxnorm_demo.csv"][1] == ("5640", "Ibuprofen")
    assert external_lists({}) == {} and title("loinc_demo.csv") == "LOINC laboratory tests" and title("x.csv") == "x.csv"


def test_sample_keeps_other_first_and_spreads_across_the_whole_list():
    rows = [("OTHER", "Other")] + [(str(i), f"Term {i}") for i in range(1000)]
    s = sample(rows, 36)
    assert s[0][0] == "OTHER" and len(s) <= 36
    assert int(s[-1][0]) > 500                      # reaches well into the list, not just the first entries
