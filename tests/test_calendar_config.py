"""Calendaring rules from the Study Configuration: auto-close from the Schedule of Activities windows, integrated
with the existing scheduler rules; notifications are proposals only. Synthetic spec."""
import copy
import io
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "skills", "calendaring-rules", "scripts"))
import study_config as sc
from extract_calendar_rules import extract_calendar_rules
from validate_rules import validate_rules, _validate_one_rule
from generate_rule_artifacts import generate_rule_artifacts

PROTOCOL = "Screening (Day -14 to Day 0), Baseline (Day 0), Day 14 ± 3 after Baseline, Week 8-10."


def spec(scheduling=None):
    s = {"study_meta": {"protocol_number": "SYN-004"},
         "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening (Day -14 to Day 0)"},
                                    {"event": "SE_BASELINE", "timepoint": "Baseline (Day 0)"},
                                    {"event": "SE_DAY14", "timepoint": "Day 14 ± 3"},
                                    {"event": "SE_WEEK8", "timepoint": "Week 8-10"},
                                    {"event": "SE_COMMON", "timepoint": "Common"}]},
         "forms": [{"form_id": "VS", "form_title": "Vital Signs", "visits_assigned": ["SE_DAY14"],
                    "survey": [{"type": "date", "name": "VSDAT", "label": "Date"}]}]}
    if scheduling is not None:
        s["scheduling"] = scheduling
    return copy.deepcopy(s)


SCHED = [{"event_oid": "SE_SCREENING", "anchor_event_oid": None, "offset_target_days": 0},
         {"event_oid": "SE_BASELINE", "anchor_event_oid": "SE_SCREENING", "offset_target_days": 14},
         {"event_oid": "SE_DAY14", "anchor_event_oid": "SE_BASELINE", "offset_target_days": 14},
         {"event_oid": "SE_WEEK8", "anchor_event_oid": "SE_BASELINE", "offset_target_days": 56, "window_upper_days": 10}]


def _rules(s):
    data = validate_rules(extract_calendar_rules(s, {}))
    return data, {r["name"]: r for r in data["rules"]}


def test_auto_close_comes_from_the_soa_window_when_the_scheduling_entry_has_none():
    s = spec(SCHED)
    before, by0 = _rules(copy.deepcopy(s))
    assert "SYN-004_autoclose_SE_DAY14" not in by0 and "SYN-004_autoclose_SE_WEEK8" in by0
    sc.apply(s, PROTOCOL)
    data, by = _rules(s)
    ac = by["SYN-004_autoclose_SE_DAY14"]
    assert ac["type"] == "RUN_ON_SCHEDULE" and ac["schedule"] == "DAILY" and ac["time"] == "23:00:00"
    assert ac["criteria"] == {"type": "EVENT_CRITERIA", "eventOid": "SE_DAY14",
                              "eventStatuses": ["SCHEDULED", "DATA_ENTRY_STARTED"], "offset": 3, "when": "after", "range": -1}
    act = ac["actions"][0]
    assert act["closeEvent"] is True and act["targetEventOid"] == "SE_DAY14" and act["type"] == "EVENT_ACTION"
    assert act["eventStatusesToTriggerOn"] == ["SCHEDULED", "DATA_ENTRY_STARTED"] and ac["_meta"]["confidence"] == "HIGH"
    # the scheduling pass's own window wins; the scheduler rules are untouched
    assert by["SYN-004_autoclose_SE_WEEK8"]["criteria"]["offset"] == 10
    assert by["SYN-004_autoclose_SE_SCREENING"]["criteria"]["offset"] == 14
    for name, rule in by0.items():
        assert {k: v for k, v in by[name].items() if k != "_meta"} == {k: v for k, v in rule.items() if k != "_meta"}
    assert "SYN-004_autoclose_SE_COMMON" not in by and "SYN-004_autoclose_SE_BASELINE" not in by


def test_rules_validate_have_unique_names_and_never_mix_date_styles():
    s = spec(SCHED)
    sc.apply(s, PROTOCOL)
    data, by = _rules(s)
    assert data["validation_summary"]["failed"] == 0 and len(by) == len(data["rules"])
    for r in data["rules"]:
        for a in r["actions"]:
            assert a["type"] != "NOTIFICATION_ACTION"  # notifications are never generated as rules
            if a.get("relativeEventOid") is not None:
                assert a.get("startDateExpression") is None
    # a spec that would produce the same rule twice keeps one
    s["scheduling"].append(dict(SCHED[2]))
    data2, _ = _rules(s)
    names = [r["name"] for r in data2["rules"]]
    assert len(names) == len(set(names)) and any("Duplicate rule name" in w for w in data2["warnings"])


def test_without_a_scheduling_block_the_configuration_supplies_offsets_and_windows():
    s = spec()
    legacy, by0 = _rules(copy.deepcopy(s))
    assert all(r["actions"][0].get("relativeEventOid") is None for r in legacy["rules"]) and len(by0) == 5
    sc.apply(s, PROTOCOL)
    data, by = _rules(s)
    d14 = by["SYN-004_sched_SE_DAY14"]["actions"][0]
    assert (d14["relativeEventOid"], d14["startDateRelativeDays"], d14["startDateExpression"]) == ("SE_BASELINE", 14, None)
    assert by["SYN-004_sched_SE_DAY14"]["_meta"]["confidence"] == "NEEDS_REVIEW"
    assert by["SYN-004_autoclose_SE_DAY14"]["criteria"]["offset"] == 3
    assert by["SYN-004_autoclose_SE_DAY14"]["_meta"]["confidence"] == "NEEDS_REVIEW"
    assert by["SYN-004_autoclose_SE_WEEK8"]["criteria"]["offset"] == 14
    assert data["validation_summary"]["failed"] == 0
    assert any("Study Configuration" in w for w in data["warnings"])
    # STUDY_CONFIG=0 (no configuration in the spec): the rules are exactly the legacy ones
    assert set(by0) == {r["name"] for r in legacy["rules"]}


def test_notifications_are_proposals_in_the_rule_service_format_and_never_published(tmp_path):
    s = spec(SCHED)
    cfg = sc.apply(s, PROTOCOL)
    d14 = next(e for e in cfg["events"] if e["oid"] == "SE_DAY14")
    kinds = [n["kind"] for n in d14["notifications"]]
    assert kinds == ["Visit reminder", "Visit overdue"]
    rem = d14["notifications"][0]["rule"]
    assert rem["type"] == "RUN_ON_SCHEDULE" and rem["schedule"] == "DAILY" and rem["time"] == "09:00:00"
    assert rem["criteria"] == {"type": "EVENT_CRITERIA", "eventOid": "SE_DAY14", "eventStatuses": ["SCHEDULED"],
                               "offset": 3, "when": "before", "range": 0}
    act = rem["actions"][0]
    assert act["type"] == "NOTIFICATION_ACTION" and act["ruleResultToTriggerOn"] is True
    assert act["toEmailAddress"] == "" and act["toPhoneNumber"] == ""  # recipients are an operational decision
    assert "${participant}" in act["emailMessage"] and "${event.name}" in act["emailSubject"] and "${site.name}" in act["textMessage"]
    over = d14["notifications"][1]["rule"]
    assert over["criteria"]["when"] == "after" and over["criteria"]["offset"] == 3
    assert all(_validate_one_rule(n["rule"]) == [] for e in cfg["events"] for n in e["notifications"])
    assert next(e for e in cfg["events"] if e["oid"] == "SE_COMMON")["notifications"] == []
    rows = {t: r for t, _n, _h, r, _w in sc.sections(cfg)}["NOTIFICATIONS (PROPOSALS)"]
    row = next(r for r in rows if r[:2] == ["SE_DAY14", "Visit reminder (pipeline default)"])
    assert row[-1] == "proposal (not published)" and row[3].startswith("to be decided")
    # the rules ZIP that gets published holds no notification
    z = generate_rule_artifacts(validate_rules(extract_calendar_rules(s, {})), str(tmp_path), {"build_warnings": []})
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        rule_files = [n for n in zf.namelist() if n.startswith("rules/") and n.endswith(".json")]
        assert rule_files and not any("NOTIFICATION_ACTION" in zf.read(n).decode() for n in rule_files)
        assert not any("Reminder" in json.loads(zf.read(n)).get("name", "") for n in rule_files)
