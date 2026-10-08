"""Study Configuration: what a data manager sets in the OpenClinica study designer besides the forms themselves.

spec["study_configuration"] documents, for review in the Study Specification PDF / XLSX:
  events           oid, name, type (Visit-Based / Common), repeating, calendar (scheduler: trigger, relative event,
                   offset days; auto-close after N days), notifications (proposals only)
  forms at events  required, hidden, allow_add, participate, SDV level and items, permission tag, labels

Every value carries its source: "protocol" (with a verbatim quote), "pipeline default", "AI-proposed" or "DM".
Values a data manager set (source "DM") are never overwritten when the configuration is rebuilt.

Deterministic where possible: type / repeating / required use the same rules as the design-board builder; the
calendar comes from the scheduling pass, else from the visit labels of the Schedule of Activities ("Day 14 +/- 3"
-> offset 14 from the Day 0 event, auto-close 3 days after; "Week 2-3" -> offset 14, auto-close 7 days after).

Kill switch STUDY_CONFIG=0. Nothing here is published to OpenClinica.
"""
from __future__ import annotations
import copy
import os
import re

VERSION = 1
SRC_PROTOCOL = "protocol"
SRC_DEFAULT = "pipeline default"
SRC_AI = "AI-proposed"
SRC_DM = "DM"
TYPE_VISIT = "Visit-Based"
TYPE_COMMON = "Common"
_UNITS = {"day": 1, "d": 1, "week": 7, "wk": 7, "w": 7, "month": 30, "mo": 30}


_FLAG_KINDS = ("participate", "hidden", "permission tag", "permission tag link")


def enabled():
    return os.environ.get("STUDY_CONFIG", "1") != "0"


def val(value, source=SRC_DEFAULT, quote="", note=""):
    out = {"value": value, "source": source}
    if quote:
        out["quote"] = quote
    if note:
        out["note"] = note
    return out


def _squash(text):
    """Lower case, spaces removed, dashes unified; punctuation kept, so "Week 1-2" is not found in "Week 12"."""
    return re.sub(r"\s+", "", re.sub(r"[\u2010-\u2015\u2212]", "-", str(text or "").lower()))


# ── Events ───────────────────────────────────────────────────────────────────────

def event_kind(event_oid):
    """(type, repeating): the rule the design-board builder uses (pipeline._build_board_json)."""
    up = str(event_oid or "").upper()
    rep = "UNSCH" in up or "COMMON" in up
    return (TYPE_COMMON if rep else TYPE_VISIT), rep


def events_of(spec):
    out, seen = [], set()
    for r in ((spec or {}).get("timepoint_csv") or {}).get("rows") or []:
        oid = r.get("event") if isinstance(r, dict) else None
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, str(r.get("timepoint") or oid)))
    return out


_NUM = r"(-?\d+(?:\.\d+)?)"
_RANGE = re.compile(rf"\b(day|week|wk|month|mo|d|w)s?\.?\s*{_NUM}\s*(?:-|–|to|through)\s*(?:(?:day|week|wk|month|mo|d|w)s?\.?\s*)?{_NUM}", re.I)
_SINGLE = re.compile(rf"\b(day|week|wk|month|mo|d|w)s?\.?\s*{_NUM}(?!\s*(?:-|–)\s*\d)", re.I)
_WINDOW = re.compile(rf"(?:±|\+\s*/\s*-|\+-)\s*{_NUM}\s*(day|week|wk|month|mo|d|w)?s?", re.I)


def parse_timing(label):
    """{"offset_days", "window_days", "phrase"} from a visit label, or None. A range opens at its first day and
    closes after its last ("Week 2-3" -> offset 14, window 7); "Day 14 +/- 3" -> offset 14, window 3."""
    text = str(label or "")
    m = _RANGE.search(text)
    if m:
        unit = _UNITS[m.group(1).lower()]
        a, b = float(m.group(2)), float(m.group(3))
        if b < a and b > 0 and a < 0:
            a, b = b, a
        lo, hi = sorted((a * unit, b * unit))
        return {"offset_days": int(lo), "window_days": int(hi - lo), "phrase": m.group(0).strip()}
    m = _SINGLE.search(text)
    if m:
        unit = _UNITS[m.group(1).lower()]
        out = {"offset_days": int(float(m.group(2)) * unit), "window_days": None, "phrase": m.group(0).strip()}
        w = _WINDOW.search(text, m.end())
        if w:
            out["window_days"] = int(float(w.group(1)) * _UNITS.get((w.group(2) or "day").lower(), 1))
            out["phrase"] = text[m.start():w.end()].strip()
        return out
    if re.search(r"\bbaseline\b", text, re.I):
        return {"offset_days": 0, "window_days": None, "phrase": "Baseline"}
    return None


def _manual(oid):
    up = str(oid or "").upper()
    return any(k in up for k in ("UNSCH", "COMMON", "EARLY", "_ET", "TERM", "WITHDR"))


def _source_for(phrase, squashed_protocol):
    """A timing found word for word in the protocol is protocol-sourced; otherwise it came from the analysis."""
    q = _squash(phrase)
    if phrase and squashed_protocol and len(q) >= 4:
        i = squashed_protocol.find(q)
        while i >= 0:  # not the start of a longer number ("Day 1" inside "Day 14")
            nxt = squashed_protocol[i + len(q):i + len(q) + 1]
            if not nxt.isdigit():
                return SRC_PROTOCOL, phrase
            i = squashed_protocol.find(q, i + 1)
    return SRC_AI, ""


def _calendar(spec, events, squashed):
    """event oid -> {"scheduler": {...}, "auto_close_after_days": V}."""
    sched = {str(e.get("event_oid")): e for e in (spec.get("scheduling") or []) if isinstance(e, dict) and e.get("event_oid")}
    parsed = {oid: parse_timing(name) for oid, name in events}
    scheduled = [oid for oid, _ in events if not _manual(oid)]
    index = scheduled[0] if scheduled else None
    zero = next((oid for oid in scheduled if parsed.get(oid) and parsed[oid]["offset_days"] == 0
                 and not (parsed[oid]["window_days"] or 0)), None) or index
    out = {}
    for oid, name in events:
        p, s = parsed.get(oid), sched.get(oid)
        if _manual(oid):
            out[oid] = {"scheduler": {"trigger": val("none (added by the site when needed)")},
                        "auto_close_after_days": val(None, note="not a calendar visit")}
            continue
        src, quote = _source_for((p or {}).get("phrase"), squashed)
        if s and (s.get("anchor_event_oid") is None or s.get("offset_target_days") is not None):
            anchor, offset = s.get("anchor_event_oid"), s.get("offset_target_days")
            note = "from the scheduling pass (the calendaring rules are built from the same values)"
            if anchor is None:
                scheduler = {"trigger": val("PARTICIPANT_CREATED", SRC_DEFAULT, note="index event: scheduled when the participant is created"),
                             "relative_event": val(None), "offset_days": val(None)}
            else:
                scheduler = {"trigger": val("relative to another event", src, quote, note),
                             "relative_event": val(anchor, src, quote), "offset_days": val(offset, src, quote)}
            wud = s.get("window_upper_days")
            if wud is None and p and p.get("window_days"):
                wud = p["window_days"]
            close = val(wud, src, quote) if wud is not None else val(None, note="no visit window found: no auto-close")
        elif oid == index:
            scheduler = {"trigger": val("PARTICIPANT_CREATED", SRC_DEFAULT, note="index event: scheduled when the participant is created"),
                         "relative_event": val(None), "offset_days": val(None)}
            close = (val(p["window_days"], src, quote) if p and p.get("window_days")
                     else val(None, note="no visit window found: no auto-close"))
        elif p and zero and oid != zero and p["offset_days"] > 0:
            scheduler = {"trigger": val("relative to another event", src, quote),
                         "relative_event": val(zero, src, quote, "the Day 0 event"), "offset_days": val(p["offset_days"], src, quote)}
            close = (val(p["window_days"], src, quote) if p.get("window_days")
                     else val(None, note="no visit window found: no auto-close"))
        else:
            scheduler = {"trigger": val("none (scheduled by the site)", note="no timing relative to another event found"),
                         "relative_event": val(None), "offset_days": val(None)}
            close = (val(p["window_days"], src, quote) if p and p.get("window_days")
                     else val(None, note="no visit window found: no auto-close"))
        out[oid] = {"scheduler": scheduler, "auto_close_after_days": close}
    return out


# ── Notifications (proposals only, rule-service format) ──────────────────────────

REMINDER_DAYS_BEFORE = 3


def _notification(name, event_oid, statuses, offset, when, subject, message):
    """A rule-service notification rule (same API as the calendaring rules). The recipient is left empty: who is
    notified is an operational decision, so a proposal is never published."""
    return {"name": name, "condition": "$TRUE", "type": "RUN_ON_SCHEDULE", "schedule": "DAILY", "time": "09:00:00",
            "criteria": {"type": "EVENT_CRITERIA", "eventOid": event_oid, "eventStatuses": list(statuses),
                         "offset": int(offset), "when": when, "range": 0},
            "actions": [{"type": "NOTIFICATION_ACTION", "ruleResultToTriggerOn": True, "toEmailAddress": "",
                         "emailSubject": subject, "emailMessage": message, "textMessage": message,
                         "toPhoneNumber": ""}]}


def notification_proposals(event):
    """Suggested notifications for one calendar event: a reminder before the visit, and an overdue notice when the
    visit window has passed without data entry. Timing, subject and message are suggestions."""
    cal = event.get("calendar") or {}
    trig = ((cal.get("scheduler") or {}).get("trigger") or {}).get("value") or ""
    if str(trig).startswith("none"):
        return []
    oid, name = event.get("oid"), event.get("name") or event.get("oid")
    out = [{"kind": "Visit reminder", "source": SRC_DEFAULT, "status": "proposal (not published)",
            "timing": f"{REMINDER_DAYS_BEFORE} day(s) before the event start date, daily at 09:00, while Scheduled",
            "recipient": "to be decided (site coordinator or $participant)",
            "rule": _notification(f"{name} Reminder", oid, ["SCHEDULED"], REMINDER_DAYS_BEFORE, "before",
                                  "Upcoming visit: ${event.name}",
                                  "Participant ${participant} at ${site.name} has ${event.name} scheduled in "
                                  f"{REMINDER_DAYS_BEFORE} days.")}]
    close = (cal.get("auto_close_after_days") or {}).get("value")
    if isinstance(close, (int, float)) and not isinstance(close, bool) and close > 0:
        out.append({"kind": "Visit overdue", "source": SRC_DEFAULT, "status": "proposal (not published)",
                    "timing": f"{int(close)} day(s) after the event start date (end of the visit window), daily at "
                              f"09:00, while Scheduled or Data Entry Started",
                    "recipient": "to be decided (site coordinator, data manager)",
                    "rule": _notification(f"{name} Overdue", oid, ["SCHEDULED", "DATA_ENTRY_STARTED"], int(close),
                                          "after", "Visit window ended: ${event.name}",
                                          "${event.name} for participant ${participant} at ${site.name} reached the "
                                          "end of its visit window and is not complete.")})
    return out


NOTIFICATION_HEADERS = ["Event OID", "Notification", "Timing", "Recipient", "Subject", "Message", "Status"]


def notification_rows(cfg):
    rows = []
    for e in (cfg or {}).get("events") or []:
        for n in e.get("notifications") or []:
            act = ((n.get("rule") or {}).get("actions") or [{}])[0]
            rows.append([e.get("oid"), n.get("kind") + (f" ({n['source']})" if n.get("source") else ""), n.get("timing"),
                         n.get("recipient"), act.get("emailSubject") or "", act.get("emailMessage") or "",
                         n.get("status") or "proposal (not published)"])
    return rows


# ── Forms at events ──────────────────────────────────────────────────────────────

def card_required(event_oid):
    """Required on Visit-Based events, not on Common ones (the design-board rule)."""
    return not event_kind(event_oid)[1]


def _placements(spec):
    out = {}
    soe = spec.get("schedule_of_events")
    for p in (soe.get("form_placements") if isinstance(soe, dict) else None) or []:
        if isinstance(p, dict):
            out[(p.get("target_visit_oid"), p.get("form_id"))] = p
    return out


def _form_cards(spec, known_events):
    cards, placements = [], _placements(spec)
    for form in spec.get("forms") or []:
        if not isinstance(form, dict) or not form.get("form_id"):
            continue
        for ev in dict.fromkeys(form.get("visits_assigned") or []):
            if ev not in known_events:
                continue
            etype, _rep = event_kind(ev)
            required = val(card_required(ev), SRC_DEFAULT,
                           note="Visit-Based: required; Common: not required")
            pl = placements.get((ev, form.get("form_id")))
            if pl is not None and pl.get("required") is False and required["value"]:
                required["note"] += ("; the protocol analysis marks this form optional at this visit"
                                     + (f" ({pl.get('notes')})" if pl.get("notes") else "") + ": review")
            allow_add = etype == TYPE_COMMON
            cards.append({
                "event_oid": ev, "form_id": form["form_id"], "form_title": form.get("form_title") or form["form_id"],
                "required": required,
                "hidden": val(False),
                "allow_add": val(allow_add, SRC_DEFAULT, note="repeating form on a Common event" if allow_add else ""),
                "participate": val(False),
                "sdv": {"level": val("not set", note="see SDV proposals"), "items": []},
                "permission_tag": val(None),
                "labels": [],
            })
    return cards


# ── Build ────────────────────────────────────────────────────────────────────────

def _keep_dm(new, old):
    """Values a data manager set (source "DM") survive a rebuild."""
    if isinstance(new, dict) and isinstance(old, dict):
        if old.get("source") == SRC_DM and "value" in old:
            return copy.deepcopy(old)
        return {k: _keep_dm(v, old.get(k)) if k in old else v for k, v in new.items()}
    return new


def build(spec, protocol_text=""):
    """The Study Configuration for a spec (the spec is not changed)."""
    events = events_of(spec)
    squashed = _squash(protocol_text)
    cal = _calendar(spec, events, squashed)
    ev_out = []
    for oid, name in events:
        etype, rep = event_kind(oid)
        ev = {"oid": oid, "name": name, "type": val(etype), "repeating": val(rep), "calendar": cal[oid]}
        ev["notifications"] = notification_proposals(ev)
        ev_out.append(ev)
    cfg = {"version": VERSION, "events": ev_out, "forms": _form_cards(spec, {oid for oid, _ in events}),
           "proposals": [], "sources": [SRC_PROTOCOL, SRC_DEFAULT, SRC_AI, SRC_DM]}
    old = spec.get("study_configuration") if isinstance(spec.get("study_configuration"), dict) else None
    if old:
        for k in ("sdv_items", "sdv_endpoints", "sdv_endpoint_check", "reference_config"):
            if k in old:
                cfg[k] = copy.deepcopy(old[k])
        old_ev = {e.get("oid"): e for e in old.get("events") or [] if isinstance(e, dict)}
        cfg["events"] = [_keep_dm(e, old_ev[e["oid"]]) if e["oid"] in old_ev else e for e in cfg["events"]]
        old_f = {(f.get("event_oid"), f.get("form_id")): f for f in old.get("forms") or [] if isinstance(f, dict)}
        cfg["forms"] = [_keep_dm(f, old_f[(f["event_oid"], f["form_id"])]) if (f["event_oid"], f["form_id"]) in old_f else f
                        for f in cfg["forms"]]
    return cfg


def apply(spec, protocol_text="", endpoint_result=None, reference=None):
    """Write spec["study_configuration"] (with the item-level SDV proposals, sdv_proposals.py). endpoint_result: the
    validated endpoint-to-field links of this run, or None to keep the ones verified earlier. reference: the
    configuration harvested from the reference studies in this run (reference_config.from_fetch), or None to keep
    what an earlier run harvested. Returns the configuration, or None when disabled or on error (spec unchanged)."""
    if not enabled() or not isinstance(spec, dict):
        return None
    try:
        cfg = build(spec, protocol_text)
        try:
            import form_flags
            flags = form_flags.propose(spec, protocol_text)
            cfg["proposals"] = [p for p in cfg.get("proposals") or [] if p.get("kind") not in _FLAG_KINDS] + flags["proposals"]
            cfg["permission_tags"] = flags["permission_tags"]
        except Exception as e:
            print(f"[study-config] form flag proposals skipped: {type(e).__name__}: {e}", flush=True)
        refs = []
        try:
            import reference_config
            if reference_config.enabled():
                refs = reference if reference is not None else (cfg.get("reference_config") or [])
            cfg["reference_config"] = refs
        except Exception as e:
            print(f"[study-config] reference study configuration skipped: {type(e).__name__}: {e}", flush=True)
        try:
            import sdv_proposals
            carry = reference_config.sdv_carry(spec, refs) if refs else None
            cfg["sdv_counts"] = sdv_proposals.propose(spec, cfg, endpoint_result, carry)
        except Exception as e:
            print(f"[study-config] SDV proposals skipped: {type(e).__name__}: {e}", flush=True)
        if refs:
            try:
                reference_config.merge(spec, cfg, refs, val, notification_proposals)
            except Exception as e:
                print(f"[study-config] reference study configuration not merged: {type(e).__name__}: {e}", flush=True)
        spec["study_configuration"] = cfg
        return cfg
    except Exception as e:
        print(f"[study-config] skipped: {type(e).__name__}: {e}", flush=True)
        return None


# ── Rendering helpers (PDF and XLSX use the same rows) ───────────────────────────

def _show(v):
    x = v.get("value") if isinstance(v, dict) else v
    if x is None or x == "":
        return "—"
    if isinstance(x, bool):
        return "Yes" if x else "No"
    return str(x)


def _src(*values):
    out = []
    for v in values:
        if isinstance(v, dict) and v.get("source"):
            s = v["source"] + (f': "{v["quote"]}"' if v.get("quote") else "")
            if s not in out:
                out.append(s)
    return "; ".join(out)


def _notes(*values):
    out = []
    for v in values:
        if isinstance(v, dict) and v.get("note") and v["note"] not in out:
            out.append(v["note"])
    return "; ".join(out)


EVENT_HEADERS = ["Event OID", "Event name", "Type", "Repeating", "Scheduled", "Relative to", "Offset (days)",
                 "Auto-close after (days)", "Source", "Notes"]
FORM_HEADERS = ["Event OID", "Form", "Form title", "Required", "Hidden", "Allow add", "Participate", "SDV",
                "SDV Required items", "Permission tag", "Source", "Notes"]


def event_rows(cfg):
    rows = []
    for e in (cfg or {}).get("events") or []:
        c = e.get("calendar") or {}
        s = c.get("scheduler") or {}
        rows.append([e.get("oid"), e.get("name"), _show(e.get("type")), _show(e.get("repeating")),
                     _show(s.get("trigger")), _show(s.get("relative_event")), _show(s.get("offset_days")),
                     _show(c.get("auto_close_after_days")),
                     _src(s.get("trigger"), s.get("offset_days"), c.get("auto_close_after_days")),
                     _notes(s.get("trigger"), s.get("relative_event"), c.get("auto_close_after_days"))])
    return rows


def form_rows(cfg):
    rows = []
    for f in (cfg or {}).get("forms") or []:
        sdv = f.get("sdv") or {}
        req = [i.get("item") for i in sdv.get("items") or [] if i.get("sdv") == "Required"]
        rows.append([f.get("event_oid"), f.get("form_id"), f.get("form_title"), _show(f.get("required")),
                     _show(f.get("hidden")), _show(f.get("allow_add")), _show(f.get("participate")),
                     _show(sdv.get("level")), ", ".join(str(i) for i in req) or "—", _show(f.get("permission_tag")),
                     _src(f.get("required"), f.get("hidden"), f.get("allow_add"), f.get("participate"),
                          sdv.get("level"), f.get("permission_tag")),
                     _notes(f.get("required"), f.get("hidden"), f.get("allow_add"), f.get("participate"),
                            f.get("permission_tag"))])
    return rows


PROPOSAL_HEADERS = ["Proposal", "Applies to", "Proposed", "Current / other value", "Source", "Rationale"]


def proposal_rows(cfg):
    rows = []
    for p in (cfg or {}).get("proposals") or []:
        rows.append([p.get("kind") or "", p.get("target") or "", str(p.get("proposed") if p.get("proposed") is not None else "—"),
                     str(p.get("current") if p.get("current") is not None else "—"),
                     (p.get("source") or "") + (f': "{p["quote"]}"' if p.get("quote") else ""), p.get("rationale") or ""])
    return rows


def label_of(ref):
    return f"reference study {ref.get('identifier')}"


def summary_line(cfg):
    if not cfg:
        return ""
    ev = cfg.get("events") or []
    rel = sum(1 for e in ev if (((e.get("calendar") or {}).get("scheduler") or {}).get("relative_event") or {}).get("value"))
    close = sum(1 for e in ev if ((e.get("calendar") or {}).get("auto_close_after_days") or {}).get("value") is not None)
    c = cfg.get("sdv_counts") or {}
    sdv = (f" SDV proposals: {c.get('Required', 0)} item(s) Required, {c.get('Optional', 0)} Optional, "
           f"{c.get('Not Applicable', 0)} Not Applicable." if c else "")
    refs = cfg.get("reference_studies") or []
    if refs:
        sdv += " Configuration also read from " + ", ".join(label_of(r) for r in refs) + "."
    return (f"Study Configuration: {len(ev)} event(s) ({rel} scheduled relative to another event, {close} with "
            f"auto-close), {len(cfg.get('forms') or [])} form(s) at events, {len(cfg.get('proposals') or [])} "
            f"proposal(s). See the Study Configuration section of the Study Specification." + sdv)


def sections(cfg):
    """[(title, note, headers, rows, relative column widths)] for the Study Specification PDF and XLSX."""
    if not cfg:
        return []
    out = [("EVENTS", "Type, repeating and calendar per event. Auto-close: the event is closed N days after its start "
            "date when it is still Scheduled or Data Entry Started.", EVENT_HEADERS, event_rows(cfg),
            [11, 15, 7, 6, 11, 9, 5, 6, 14, 16]),
           ("FORMS AT EVENTS", "One row per form at an event (a card on the design board).", FORM_HEADERS,
            form_rows(cfg), [10, 7, 13, 5, 5, 5, 6, 9, 14, 7, 9, 10])]
    if notification_rows(cfg):
        out.append(("NOTIFICATIONS (PROPOSALS)", "Suggested notifications in the rule-service format. They are never "
                    "published: recipients are an operational decision. Tokens such as ${participant}, ${event.name} "
                    "and ${site.name} are filled in by OpenClinica (check the token list in the study designer).",
                    NOTIFICATION_HEADERS, notification_rows(cfg), [10, 11, 22, 14, 13, 22, 8]))
    if cfg.get("sdv_items"):
        import sdv_proposals
        eps = cfg.get("sdv_endpoints") or []
        note = ("Item-level SDV proposals. Required = critical-to-quality data; calculated, hidden and derived items "
                "and notes are Not Applicable; all other items are Optional."
                + (" Endpoints linked to fields: " + "; ".join(f"{e['endpoint']} ({e['kind']})" for e in eps) + "." if eps else ""))
        out.append(("SDV ITEMS", note, sdv_proposals.SDV_HEADERS, sdv_proposals.sdv_rows(cfg), [8, 12, 24, 7, 30, 19]))
    if cfg.get("proposals"):
        tags = ", ".join(t["name"] for t in cfg.get("permission_tags") or [])
        out.append(("PROPOSALS", "Nothing below is applied or published; a data manager decides."
                    + (f" Permission tags proposed: {tags} (not written to the design board)." if tags else ""),
                    PROPOSAL_HEADERS,
                    proposal_rows(cfg), [12, 16, 18, 14, 18, 22]))
    return out
