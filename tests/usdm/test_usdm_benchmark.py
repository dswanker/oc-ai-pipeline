"""USDM-seeded structure vs the DDF-RA example JSONs (CDISC_Pilot, EliLilly_NCT03421379, Alexion_NCT04573309).
The structure scores do not need CDISC CT; the activity -> form coverage (needs CT) is printed by benchmark.py."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import benchmark
import usdm_input

EXPECTED = {"CDISC_Pilot_Study.json": (12, 118), "EliLilly_NCT03421379_Diabetes.json": (7, 49),
            "Alexion_NCT04573309_Wilsons.json": (50, 376)}


def test_events_and_placements_match_the_examples():
    scores = benchmark.run(None)
    assert [s["example"] for s in scores] == list(EXPECTED)
    for s in scores:
        assert (s["events_reference"], s["placements_reference"]) == EXPECTED[s["example"]]
        assert s["usdm_version"] == "4.0.0"
        assert s["event_accuracy"] == 1.0 and s["events_seeded"] == s["events_reference"] and s["event_oids_unique"]
        assert s["placement_recall"] == 1.0 and s["placement_accuracy"] == 1.0 and s["placement_errors"] == 0
        assert s["events_with_day"] == s["events_reference"]            # every visit gets a day from the timings


def test_seeding_the_examples_is_idempotent_and_keeps_form_content():
    import copy, json
    for name in EXPECTED:
        structure, _ = usdm_input.read(open(os.path.join(benchmark.HERE, "examples", name), "rb").read())
        spec = {"study_meta": {"protocol_number": "B"}, "forms": [
            {"form_id": "VS", "form_title": "Vital Signs", "cdash_domain": "VS", "visits_assigned": ["SE_OLD"],
             "survey": [{"type": "integer", "name": "SYSBP", "label": "Systolic"}], "choices": []}]}
        usdm_input.seed_spec(spec, structure)
        once = copy.deepcopy(spec)
        usdm_input.seed_spec(spec, structure)
        assert json.dumps(spec, sort_keys=True) == json.dumps(once, sort_keys=True)
        assert spec["forms"][0]["survey"] == [{"type": "integer", "name": "SYSBP", "label": "Systolic"}]
        assert len(spec["timepoint_csv"]["rows"]) == len(structure["events"]) == len(spec["scheduling"])
        assert "USDM" in usdm_input.context_text(structure)
