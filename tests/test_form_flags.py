"""Form flag and permission tag proposals (form_flags.py): proposals only, documented in the Study Configuration.
Synthetic spec and protocol."""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import form_flags as ff
import study_config as sc

PROTOCOL = ("1. This is a randomized, double-blind, placebo-controlled study.\n"
            "7.2 The Sleep Diary is a patient-reported outcome and will be completed by the participant\n"
            "at home each morning.\n"
            "8.1 Safety laboratory samples are analysed by a central laboratory; results are transferred\n"
            "electronically.\n"
            "8.4 Tumour scans undergo independent central review by the imaging vendor.\n")


def _form(fid, title, domain=None, visits=("SE_V1",), **kw):
    return {"form_id": fid, "form_title": title, "cdash_domain": domain, "visits_assigned": list(visits),
            "survey": [{"type": "date", "name": fid + "DAT", "label": "Date"}], **kw}


def spec():
    return copy.deepcopy({
        "timepoint_csv": {"rows": [{"event": "SE_V1", "timepoint": "Visit 1"}, {"event": "SE_COMMON", "timepoint": "Common"}]},
        "forms": [_form("SLEEP", "Sleep Diary", "QS", is_epro=True),
                  _form("QOL", "Quality of Life Form", "QS", qrs_instruments=[{"instrument": "QOL1", "domain": "QS"}]),
                  _form("MOOD", "Mood Scale", None, is_epro=True),
                  _form("LB", "Laboratory", "LB"),
                  _form("SCAN", "Tumour Scan Assessment", "TU"),
                  _form("RAND", "Randomization", None),
                  _form("DA", "Drug Accountability", "DA"),
                  _form("CONTACT", "Participant Contact Details", None),
                  _form("VS", "Vital Signs", "VS"),
                  _form("AE", "Adverse Events", "AE", visits=("SE_COMMON",), has_repeating_group=True)]})


def _by(props):
    out = {}
    for p in props:
        out.setdefault((p["kind"], p["target"].split(" ")[0]), p)
    return out


def test_participate_only_with_a_protocol_sentence_or_flagged_as_unconfirmed():
    by = _by(ff.propose(spec(), PROTOCOL)["proposals"])
    p = by[("participate", "SLEEP")]
    assert p["proposed"] is True and p["current"] is False and p["source"] == "protocol"
    assert p["quote"].startswith("7.2 The Sleep Diary is a patient-reported outcome") and "\n" not in p["quote"]
    # a questionnaire the protocol does not describe as patient-completed is not proposed
    assert ("participate", "QOL") not in by
    # marked ePRO by the analysis, no protocol sentence: AI-proposed, to confirm
    m = by[("participate", "MOOD")]
    assert m["source"] == "AI-proposed" and m["quote"] == "" and "confirm" in m["rationale"]
    assert ("participate", "VS") not in by


def test_hidden_for_vendor_loaded_forms_with_the_protocol_sentence():
    by = _by(ff.propose(spec(), PROTOCOL)["proposals"])
    assert "central laboratory" in by[("hidden", "LB")]["quote"] and by[("hidden", "LB")]["source"] == "protocol"
    assert "independent central review" in by[("hidden", "SCAN")]["quote"]
    assert ("hidden", "VS") not in by and ("hidden", "AE") not in by
    assert not [p for p in ff.propose(spec(), "No vendor is mentioned.")["proposals"] if p["kind"] == "hidden"]


def test_permission_tags_are_proposals_and_never_written_to_the_board():
    res = ff.propose(spec(), PROTOCOL)
    by = _by(res["proposals"])
    assert by[("permission tag", "RAND")]["proposed"] == "Unblinded" and "double-blind" in by[("permission tag", "RAND")]["quote"]
    assert by[("permission tag", "DA")]["proposed"] == "Unblinded"
    assert by[("permission tag", "CONTACT")]["proposed"] == "PII" and by[("permission tag", "CONTACT")]["source"] == "pipeline default"
    assert res["permission_tags"] == [{"name": "PII", "color": "orange", "isConfigPermission": True, "type": "Form"},
                                      {"name": "Unblinded", "color": "red", "isConfigPermission": True, "type": "Form"}]
    link = next(p for p in res["proposals"] if p["kind"] == "permission tag link")
    assert link["rationale"].startswith("UNRESOLVED") and "not written to the design board" in link["rationale"]
    # an open-label study gets no Unblinded tag
    open_label = ff.propose(spec(), "This is an open-label study. " + PROTOCOL.split("\n", 1)[1])
    assert not [p for p in open_label["proposals"] if p.get("proposed") == "Unblinded"]
    src = open(os.path.join(ROOT, "pipeline.py")).read()
    assert 'return {"labels": [], "lists": lists, "cards": cards}' in src  # the board never carries tags


def test_proposals_live_in_the_study_configuration_and_change_no_value():
    s = spec()
    cfg = sc.apply(s, PROTOCOL)
    assert all(f["hidden"]["value"] is False and f["participate"]["value"] is False and f["permission_tag"]["value"] is None
               for f in cfg["forms"])
    # allow_add is a configuration value for forms on a Common event, not a proposal
    assert next(f for f in cfg["forms"] if f["form_id"] == "AE")["allow_add"]["value"] is True
    kinds = [p["kind"] for p in cfg["proposals"]]
    assert kinds.count("participate") == 2 and kinds.count("hidden") == 2 and kinds.count("permission tag") == 3
    title, note, headers, rows, _w = next(x for x in sc.sections(cfg) if x[0] == "PROPOSALS")
    assert "Permission tags proposed: PII, Unblinded (not written to the design board)" in note
    assert any(r[0] == "participate" and r[1].startswith("SLEEP") and r[2] == "True" and r[4].startswith('protocol: "7.2') for r in rows)
    again = sc.apply(s, PROTOCOL)  # rebuilt, not duplicated
    assert len(again["proposals"]) == len(kinds)
    assert sc.apply(spec(), "")["proposals"] == [p for p in sc.apply(spec(), "")["proposals"] if p["source"] != "protocol"]
