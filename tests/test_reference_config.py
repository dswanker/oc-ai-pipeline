"""Reference studies supply study configuration (reference_studies.py harvest, reference_config.py precedence).
Recorded synthetic board / rules JSON; no network (httpx.MockTransport)."""
import asyncio
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import httpx
import pytest

import reference_config as rc
import reference_studies as rs
import study_config as sc

STUDIES = [{"uuid": "uuid-alpha-1", "uniqueIdentifier": "ALPHA-01", "name": "Alpha one", "currentBoardUrl": "/b/BRDalpha/alpha"}]

BOARD = {
    "studyUuid": "uuid-alpha-1",
    "labels": [{"name": "Unblinded", "color": "red", "isConfigPermission": True, "type": "Form"},
               {"name": "Blue sticker", "color": "blue"}],
    "lists": [{"_id": "L1", "title": "Screening", "eventOcoid": "SE_SCREENING", "type": "Visit-Based", "isRepeating": False},
              {"_id": "L2", "title": "Day 14", "eventOcoid": "SE_DAY14", "type": "Visit-Based", "isRepeating": True},
              {"_id": "L3", "title": "Log", "eventOcoid": "SE_COMMON", "type": "Common", "isRepeating": True},
              {"_id": "L4", "title": "Old", "eventOcoid": "SE_OLD", "type": "Visit-Based", "archived": True}],
    "cards": [
        {"listId": "L1", "formOcoid": "F_VS", "title": "Vital Signs", "required": True, "hidden": False, "allowAdd": False,
         "participate": False, "sdv": "item_level", "itemLevelSdv": True, "sdvItems": [
             {"ocoid": "I_VITAL_VSDAT", "name": "VSDAT", "sdv": "required", "boardId": "b", "cardId": "c"},
             {"ocoid": "I_VITAL_SYSBP", "name": "SYSBP", "sdv": "optional"},
             {"ocoid": "I_VITAL_GONE", "name": "NOTINFORM", "sdv": "required"},
             {"ocoid": "I_VITAL_X", "name": "X", "sdv": "not_applicable"}]},
        {"listId": "L2", "formOcoid": "F_VS", "title": "Vital Signs", "required": False, "hidden": True, "allowAdd": False,
         "participate": True, "sdv": "item_level", "itemLevelSdv": True, "sdvItems": []},
        {"listId": "L3", "formOcoid": "F_AE", "title": "Adverse Events", "required": False, "allowAdd": True,
         "sdv": "not_applicable_item_level"},
        {"listId": "L1", "formOcoid": "F_GONE", "title": "Archived", "archived": True, "required": True}]}

RULES = [
    {"name": "Screening Scheduler", "condition": "not(boolean(x))", "triggerType": ["PARTICIPANT_CREATED"],
     "actions": [{"type": "EVENT_ACTION", "ruleResultToTriggerOn": True, "targetEventOid": "SE_SCREENING",
                  "startDateExpression": "format-date(now(), '%Y-%m-%d')", "targetEventStatus": "SCHEDULED"}]},
    {"name": "Day 14 Scheduler", "condition": "$TRUE", "triggerType": ["EVENT_START_DATE_CHANGED"],
     "actions": [{"type": "EVENT_ACTION", "targetEventOid": "SE_DAY14", "relativeEventOid": "SE_SCREENING",
                  "startDateRelativeDays": 21, "targetEventStatus": "SCHEDULED"}]},
    {"name": "Follow-up Scheduler", "condition": "$TRUE", "triggerType": ["PARTICIPANT_CREATED"],
     "actions": [{"type": "EVENT_ACTION", "targetEventOid": "SE_FU", "relativeEventOid": "SE_SCREENING",
                  "startDateRelativeDays": 60, "targetEventStatus": "SCHEDULED"}]},
    {"name": "Auto Close Screening", "condition": "$TRUE", "type": "RUN_ON_SCHEDULE", "schedule": "DAILY", "time": "23:00:00",
     "criteria": {"type": "EVENT_CRITERIA", "eventOid": "SE_SCREENING", "eventStatuses": ["SCHEDULED"], "offset": 30,
                  "when": "after", "range": -1},
     "actions": [{"type": "EVENT_ACTION", "targetEventOid": "SE_SCREENING", "closeEvent": True}]},
    {"name": "Auto Close Day 14", "condition": "$TRUE", "type": "RUN_ON_SCHEDULE", "schedule": "DAILY", "time": "23:00:00",
     "criteria": {"type": "EVENT_CRITERIA", "eventOid": "SE_DAY14", "eventStatuses": ["SCHEDULED"], "offset": 30,
                  "when": "after", "range": -1},
     "actions": [{"type": "EVENT_ACTION", "targetEventOid": "SE_DAY14", "closeEvent": True}]},
    {"name": "Day 14 Reminder", "condition": "$TRUE", "type": "RUN_ON_SCHEDULE", "schedule": "DAILY", "time": "09:00:00",
     "criteria": {"type": "EVENT_CRITERIA", "eventOid": "SE_DAY14", "eventStatuses": ["SCHEDULED"], "offset": 2,
                  "when": "before", "range": 0},
     "actions": [{"type": "NOTIFICATION_ACTION", "ruleResultToTriggerOn": True,
                  "toEmailAddress": "coordinator@site-example.org, $participant", "emailSubject": "Visit ${event.name}",
                  "emailMessage": "Call 555-010-9999 or write to help@site-example.org about ${participant}.",
                  "textMessage": "Visit soon", "toPhoneNumber": "+1 555 010 1234"}]},
    {"name": "Old visit Reminder", "condition": "$TRUE", "type": "RUN_ON_SCHEDULE", "schedule": "DAILY", "time": "09:00:00",
     "criteria": {"type": "EVENT_CRITERIA", "eventOid": "SE_ELSEWHERE", "eventStatuses": ["SCHEDULED"], "offset": 1,
                  "when": "after", "range": 0},
     "actions": [{"type": "NOTIFICATION_ACTION", "toEmailAddress": "", "emailSubject": "Overdue", "emailMessage": "m",
                  "textMessage": "", "toPhoneNumber": ""}]},
]


class Server:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, request):
        url = str(request.url)
        self.calls.append((request.method, url))
        assert request.method == "GET" or url.endswith("/user-service/api/oauth/token"), "read-only apart from login"
        if url.endswith("/oauth/token"):
            return httpx.Response(200, text="TOKEN123")
        if "/study-service/api/studies" in url:
            return httpx.Response(200, json=STUDIES, headers={"X-Total-Count": "1"})
        if "/api/boards/" in url:
            return httpx.Response(200, json=BOARD)
        if "/rule-service/api/studies/" in url:
            assert url == "https://t.build.openclinica.io/rule-service/api/studies/uuid-alpha-1/rules"
            return httpx.Response(500) if "rules" in self.fail else httpx.Response(200, json=RULES)
        return httpx.Response(404)


def _fetch(server=None):
    server = server or Server()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as c:
            return await rs.fetch("t", "ALPHA-01", username="svc", password="pw", client=c)
    return asyncio.run(go()), server


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("REFERENCE_STUDY_CONFIG", "REFERENCE_STUDIES", "STUDY_CONFIG"):
        monkeypatch.delenv(k, raising=False)


def spec():
    vs = {"form_id": "VS", "form_title": "Vital Signs", "cdash_domain": "VS", "visits_assigned": ["SE_SCREENING", "SE_DAY14"],
          "customer_standard": {"form_oid": "VS", "source": "referenced study ALPHA-01", "domain": "VS"},
          "survey": [{"type": "date", "name": "VSDAT", "label": "Date"}, {"type": "integer", "name": "SYSBP", "label": "Systolic"},
                     {"type": "integer", "name": "PULSE", "label": "Pulse"}, {"type": "calculate", "name": "X", "calculation": "1"}]}
    ex = {"form_id": "EX", "form_title": "Dosing", "cdash_domain": "EX", "visits_assigned": ["SE_DAY14"],
          "customer_standard": {"form_oid": "EX", "source": "uploaded XLSForm", "domain": "EX"},
          "survey": [{"type": "date", "name": "EXSTDAT", "label": "Dose date"}]}
    ds = {"form_id": "DS", "form_title": "Disposition", "cdash_domain": "DS", "visits_assigned": ["SE_FU"],
          "customer_standard": {"form_oid": "VS", "source": "referenced study OTHER"},
          "survey": [{"type": "date", "name": "DSSTDAT", "label": "Date"}]}
    return copy.deepcopy({
        "timepoint_csv": {"rows": [{"event": "SE_SCREENING", "timepoint": "Screening (Day -14 to Day 0)"},
                                   {"event": "SE_BASELINE", "timepoint": "Baseline (Day 0)"},
                                   {"event": "SE_DAY14", "timepoint": "Day 14 ± 3"},
                                   {"event": "SE_FU", "timepoint": "Follow-up call"},
                                   {"event": "SE_WEEK8", "timepoint": "Week 8"},
                                   {"event": "SE_COMMON", "timepoint": "Common"}]},
        "forms": [vs, ex, ds]})


def test_board_configuration_is_harvested_without_archived_parts_or_the_tag_link():
    cfg = rs.board_config(BOARD)
    assert cfg["events"] == {"SE_SCREENING": {"name": "Screening", "type": "Visit-Based", "repeating": False},
                             "SE_DAY14": {"name": "Day 14", "type": "Visit-Based", "repeating": True},
                             "SE_COMMON": {"name": "Log", "type": "Common", "repeating": True}}
    assert [(c["event_oid"], c["form_oid"]) for c in cfg["cards"]] == [("SE_SCREENING", "F_VS"), ("SE_DAY14", "F_VS"), ("SE_COMMON", "F_AE")]
    vs = cfg["cards"][0]
    assert (vs["required"], vs["hidden"], vs["allow_add"], vs["participate"], vs["sdv"], vs["item_level_sdv"]) == \
        (True, False, False, False, "item_level", True)
    assert vs["sdv_items"] == [{"ocoid": "I_VITAL_VSDAT", "name": "VSDAT", "sdv": "required"},
                               {"ocoid": "I_VITAL_SYSBP", "name": "SYSBP", "sdv": "optional"},
                               {"ocoid": "I_VITAL_GONE", "name": "NOTINFORM", "sdv": "required"}]
    assert cfg["permission_tags"] == ["Unblinded"]  # tag names only; nothing links a tag to a form


def test_rules_are_harvested_per_event_with_recipients_blanked():
    r = rs.rules_config(RULES)
    assert r["SE_SCREENING"]["scheduler"]["trigger"] == "PARTICIPANT_CREATED" and r["SE_SCREENING"]["scheduler"]["relative_event"] is None
    assert (r["SE_DAY14"]["scheduler"]["relative_event"], r["SE_DAY14"]["scheduler"]["offset_days"]) == ("SE_SCREENING", 21)
    assert r["SE_DAY14"]["auto_close"] == {"after_days": 30, "when": "after", "rule": "Auto Close Day 14"}
    n = r["SE_DAY14"]["notifications"][0]
    act = n["rule"]["actions"][0]
    assert act["toEmailAddress"] == "" and act["toPhoneNumber"] == "" and n["had_email_recipient"] and n["had_phone_recipient"]
    assert act["emailMessage"] == "Call [phone removed] or write to [email removed] about ${participant}."
    assert n["rule"]["criteria"]["offset"] == 2 and n["rule"]["criteria"]["when"] == "before"
    assert "site-example" not in str(r) and "555" not in str(r)
    assert rs.rules_config(None) == {} and rs.rules_config({"not": "a list"}) == {}


def test_fetch_reads_the_rules_read_only_and_never_logs_addresses(capsys, monkeypatch):
    res, server = _fetch()
    cfg = res["referenced"][0]["config"]
    assert cfg["identifier"] == "ALPHA-01" and cfg["permission_tags"] == ["Unblinded"] and "SE_DAY14" in cfg["rules"]
    assert ("GET", "https://t.build.openclinica.io/rule-service/api/studies/uuid-alpha-1/rules") in server.calls
    assert all(m == "GET" or u.endswith("/oauth/token") for m, u in server.calls)
    line = next(l for l in res["log"] if "configuration read" in l)
    assert "3 event(s), 3 form(s) at events (1 with SDV items), 1 permission tag(s), 3 scheduler / 2 auto-close / 2 notification rule(s)" in line
    text = "\n".join(res["log"]) + capsys.readouterr().out
    assert "site-example" not in text and "555" not in text and "TOKEN123" not in text
    # rules unavailable: the board configuration is still used, and the build goes on
    res2, _ = _fetch(Server(fail={"rules"}))
    cfg2 = res2["referenced"][0]["config"]
    assert cfg2["rules"] == {} and "rules could not be read" in cfg2["rules_note"] and len(cfg2["cards"]) == 3
    # kill switch: no rules call, no configuration
    monkeypatch.setenv("REFERENCE_STUDY_CONFIG", "0")
    res3, server3 = _fetch()
    assert "config" not in res3["referenced"][0] and not any("rule-service" in u for _m, u in server3.calls)
    assert rc.from_fetch(res3["referenced"]) == []


def _cfg(monkeypatch=None):
    refs = rc.from_fetch(_fetch()[0]["referenced"])
    s = spec()
    return s, sc.apply(s, "Day 14 ± 3 after Baseline.", None, refs)


def test_sdv_items_are_carried_over_for_a_matched_form_and_rules_apply_elsewhere():
    s, cfg = _cfg()
    vs = {i["item"]: i for i in cfg["sdv_items"]["VS"]["items"]}
    assert vs["VSDAT"]["sdv"] == "Required" and vs["VSDAT"]["source"] == "reference study ALPHA-01"
    assert vs["SYSBP"]["sdv"] == "Optional" and vs["SYSBP"]["source"] == "reference study ALPHA-01"
    assert vs["PULSE"]["sdv"] == "Optional" and vs["PULSE"]["source"] == "pipeline default"  # not in the reference: the rule
    assert "X" not in vs and "NOTINFORM" not in vs  # a calculated item stays Not Applicable; a field we do not have is ignored
    # a form matched to an uploaded file, or to another study, is not touched by ALPHA-01
    assert cfg["sdv_items"]["EX"]["items"][0]["source"] == "pipeline default" and cfg["sdv_items"]["EX"]["items"][0]["sdv"] == "Required"
    assert cfg["sdv_items"]["DS"]["items"][0]["source"] == "pipeline default"
    # the difference between the reference value and the rule is a proposal with both values
    p = next(p for p in cfg["proposals"] if p["kind"] == "SDV")
    assert p["target"] == "VS: VSDAT" and p["current"] == "Required" and p["proposed"] == "Optional" and p["source"] == "reference study ALPHA-01"
    card = next(f for f in cfg["forms"] if (f["event_oid"], f["form_id"]) == ("SE_SCREENING", "VS"))
    assert [i["item"] for i in card["sdv"]["items"]] == ["VSDAT"] and card["sdv"]["items"][0]["source"] == "reference study ALPHA-01"


def test_protocol_window_wins_and_the_reference_fills_only_where_the_protocol_is_silent():
    _s, cfg = _cfg()
    ev = {e["oid"]: e for e in cfg["events"]}
    d14 = ev["SE_DAY14"]["calendar"]
    assert d14["auto_close_after_days"]["value"] == 3 and d14["auto_close_after_days"]["source"] == "protocol"
    assert (d14["scheduler"]["relative_event"]["value"], d14["scheduler"]["offset_days"]["value"]) == ("SE_BASELINE", 14)
    by = {(p["kind"], p["target"]): p for p in cfg["proposals"]}
    ac = by[("event auto-close", "event SE_DAY14")]
    assert (ac["proposed"], ac["current"], ac["source"]) == ("30 day(s)", "3 day(s)", "reference study ALPHA-01")
    sch = by[("event scheduling", "event SE_DAY14")]
    assert sch["proposed"] == "21 day(s) after SE_SCREENING" and sch["current"] == "14 day(s) after SE_BASELINE"
    # follow-up: the protocol gives no timing, the reference study's rule is used and says so
    fu = ev["SE_FU"]["calendar"]
    assert fu["scheduler"]["relative_event"] == {"value": "SE_SCREENING", "source": "reference study ALPHA-01"}
    assert fu["scheduler"]["offset_days"]["value"] == 60 and "protocol is silent" in fu["scheduler"]["trigger"]["note"]
    # no window and no rule for the event: the reference study's pattern (2 of 2 auto-close rules use 30 days)
    assert fu["auto_close_after_days"]["value"] == 30 and "pattern" in fu["auto_close_after_days"]["note"]
    assert ev["SE_WEEK8"]["calendar"]["auto_close_after_days"]["source"] == "reference study ALPHA-01"
    assert ev["SE_COMMON"]["calendar"]["auto_close_after_days"]["value"] is None  # not a calendar visit
    assert ev["SE_SCREENING"]["calendar"]["auto_close_after_days"]["value"] == 14  # the protocol's own window


def test_flags_types_notifications_and_tags_are_proposals_only():
    _s, cfg = _cfg()
    cards = {(f["event_oid"], f["form_id"]): f for f in cfg["forms"]}
    assert cards[("SE_DAY14", "VS")]["hidden"]["value"] is False and cards[("SE_DAY14", "VS")]["participate"]["value"] is False
    kinds = {(p["kind"], p["target"]): p for p in cfg["proposals"]}
    assert kinds[("hidden", "VS at SE_DAY14")]["proposed"] == "Yes" and kinds[("hidden", "VS at SE_DAY14")]["current"] == "No"
    assert kinds[("participate", "VS at SE_DAY14")]["proposed"] == "Yes"
    assert kinds[("required", "VS at SE_DAY14")]["proposed"] == "No" and kinds[("required", "VS at SE_DAY14")]["current"] == "Yes"
    rep = kinds[("event repeating", "event SE_DAY14")]
    assert (rep["proposed"], rep["current"]) == ("Yes", "No")
    assert next(e for e in cfg["events"] if e["oid"] == "SE_DAY14")["repeating"]["value"] is False
    notes = next(e for e in cfg["events"] if e["oid"] == "SE_DAY14")["notifications"]
    ref = next(n for n in notes if n["kind"] == "Reference study notification")
    assert ref["status"] == "proposal (not published)" and ref["rule"]["actions"][0]["toEmailAddress"] == ""
    assert ref["recipient"] == "blanked (the reference study had an email recipient and a phone recipient)"
    assert ref["timing"] == "2 day(s) before the event start date, daily at 09:00:00"
    other = next(p for p in cfg["proposals"] if p["kind"] == "notification")
    assert other["target"] == "reference event SE_ELSEWHERE (not an event of this study)"
    tags = next(p for p in cfg["proposals"] if p["kind"] == "permission tags")
    assert tags["proposed"] == "Unblinded" and "UNRESOLVED" in tags["rationale"] and "every event" in tags["rationale"]
    assert "site-example" not in str(cfg) and "555" not in str(cfg)
    assert "reference study ALPHA-01" in cfg["sources"] and cfg["reference_studies"][0]["identifier"] == "ALPHA-01"
    assert "Configuration also read from reference study ALPHA-01" in sc.summary_line(cfg)


def test_rebuild_keeps_the_harvest_and_kill_switch_ignores_it(monkeypatch):
    s, cfg = _cfg()
    n = len(cfg["proposals"])
    again = sc.apply(s, "Day 14 ± 3 after Baseline.")  # a later run without a fetch
    assert len(again["proposals"]) == n and again["sdv_items"]["VS"]["items"][0]["source"] == "reference study ALPHA-01"
    monkeypatch.setenv("REFERENCE_STUDY_CONFIG", "0")
    off = sc.apply(s, "Day 14 ± 3 after Baseline.")
    assert off["sdv_items"]["VS"]["items"][0]["source"] == "pipeline default"
    assert not [p for p in off["proposals"] if p["source"].startswith("reference study")]
    assert next(e for e in off["events"] if e["oid"] == "SE_FU")["calendar"]["auto_close_after_days"]["value"] is None
