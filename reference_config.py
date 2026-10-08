"""Reference studies supply study configuration, not just forms (reference_studies.py harvests it, read-only).

Precedence: the PROTOCOL decides what is needed (forms, events, visit windows).
  SDV          a form matched to a reference-study form takes that form's SDV items, by item name / OID, where the
               field exists (source "reference study <identifier>"); for every other item the rule-based proposals
               of sdv_proposals.py apply
  calendar     a protocol event keeps the protocol's scheduling and window; the reference study's value for the same
               event, or its pattern (most events auto-close N days after), is used only where the protocol is silent
  flags, type  never applied: a difference between the reference study and this study is a proposal with both values
  notifications  proposals, recipients blanked
  permission tags  names only; the form-to-tag link is stored outside the board JSON and is not confirmed

Every difference between a reference study and the protocol-driven configuration appears in the Study Configuration
as a proposal showing both values. Kill switch REFERENCE_STUDY_CONFIG=0.
"""
from __future__ import annotations
import collections
import os
import re


def enabled():
    return os.environ.get("REFERENCE_STUDY_CONFIG", "1") != "0"


def label(identifier):
    return f"reference study {identifier}"


def _norm(form_id):
    return re.sub(r"^F_", "", str(form_id or "").strip().upper())


def reduce(config):
    """The harvested configuration of one study in the compact form kept in the spec."""
    forms = {}
    for c in config.get("cards") or []:
        f = forms.setdefault(_norm(c.get("form_oid")), {"title": c.get("title") or "", "cards": {}, "sdv_items": {}})
        f["cards"][str(c.get("event_oid"))] = {k: c.get(k) for k in ("required", "hidden", "allow_add", "participate", "sdv")}
        for it in c.get("sdv_items") or []:
            name = str(it.get("name") or "").upper() or re.sub(r"^I_[A-Z0-9]+_", "", str(it.get("ocoid") or "").upper())
            if name:
                f["sdv_items"].setdefault(name, it.get("sdv"))
    return {"identifier": str(config.get("identifier") or ""), "events": config.get("events") or {},
            "rules": config.get("rules") or {}, "rules_note": config.get("rules_note") or "",
            "permission_tags": list(config.get("permission_tags") or []), "forms": forms}


def from_fetch(referenced):
    """[reduced config] from reference_studies.fetch()["referenced"], in listed order."""
    out = []
    for r in referenced or []:
        if isinstance(r, dict) and isinstance(r.get("config"), dict):
            out.append(reduce(r["config"]))
    return out


def _matched(spec, ref):
    """spec form id -> the reference study's form record, for forms matched to a form of that study."""
    out = {}
    for form in spec.get("forms") or []:
        cs = form.get("customer_standard") if isinstance(form, dict) else None
        # standards_match labels a form taken from a referenced study "referenced study <identifier>"
        if cs and str(cs.get("source") or "") == f"referenced study {ref['identifier']}":
            rf = ref["forms"].get(_norm(cs.get("form_oid") or form.get("form_id")))
            if rf:
                out[form["form_id"]] = rf
    return out


def sdv_carry(spec, refs):
    """{form_id: {"label": "reference study X", "items": {ITEM NAME: "required" | "optional"}}} for
    sdv_proposals.propose(carry=...). The first reference study (listed order) that has items for a form wins."""
    out = {}
    for ref in refs or []:
        for fid, rf in _matched(spec, ref).items():
            if fid not in out and rf.get("sdv_items"):
                out[fid] = {"label": label(ref["identifier"]), "items": dict(rf["sdv_items"])}
    return out


def _val(x):
    return x.get("value") if isinstance(x, dict) else None


def _show(x):
    return "Yes" if x is True else "No" if x is False else ("—" if x is None else str(x))


def _is_calendar_event(ev):
    trig = str(_val(((ev.get("calendar") or {}).get("scheduler") or {}).get("trigger")) or "")
    return not trig.startswith("none (added by the site")


def merge(spec, cfg, refs, val, notification_proposals):
    """Apply the precedence to a built Study Configuration (cfg is changed in place). `val` and
    `notification_proposals` are study_config's helpers (passed in to avoid a circular import)."""
    if not refs:
        return
    proposals = [p for p in cfg.get("proposals") or [] if not str(p.get("source") or "").startswith("reference study ")]
    cfg["reference_studies"] = [{"identifier": r["identifier"], "events": len(r["events"]), "forms": len(r["forms"]),
                                 "permission_tags": r["permission_tags"], "rules_note": r.get("rules_note") or ""}
                                for r in refs]
    events = {e["oid"]: e for e in cfg.get("events") or []}
    for ref in refs:
        src = label(ref["identifier"])
        if src not in cfg.setdefault("sources", []):
            cfg["sources"].append(src)
        matched = _matched(spec, ref)

        # event type / repeating: never applied, differences are proposals
        for oid, rev in ref["events"].items():
            ev = events.get(oid)
            if not ev:
                continue
            for key, theirs in (("type", rev.get("type")), ("repeating", rev.get("repeating"))):
                ours = _val(ev.get(key))
                if theirs is not None and ours is not None and str(theirs).lower() != str(ours).lower():
                    proposals.append({"kind": f"event {key}", "target": f"event {oid}", "proposed": _show(theirs),
                                      "current": _show(ours), "source": src, "quote": "",
                                      "rationale": f"The reference study has {key} = {_show(theirs)} for this event; "
                                                   f"this study has {_show(ours)}."})

        # calendar: protocol first, the reference study only where the protocol is silent
        closes = [r["auto_close"]["after_days"] for r in ref["rules"].values() if r.get("auto_close")]
        common = collections.Counter(closes).most_common(1)
        pattern = common[0] if common and common[0][1] >= 2 else None
        for oid, ev in events.items():
            cal, changed = ev.get("calendar") or {}, False
            rr = ref["rules"].get(oid) or {}
            sch = cal.get("scheduler") or {}
            rs = rr.get("scheduler")
            if rs and (rs.get("relative_event") or rs.get("trigger")):
                ours_rel, ours_off = _val(sch.get("relative_event")), _val(sch.get("offset_days"))
                silent = str(_val(sch.get("trigger")) or "").startswith("none (scheduled by the site")
                if silent and rs.get("relative_event") and rs["relative_event"] in events:
                    cal["scheduler"] = {"trigger": val("relative to another event", src, note="the protocol is silent; from the reference study's rule"),
                                        "relative_event": val(rs["relative_event"], src), "offset_days": val(rs.get("offset_days"), src)}
                    changed = True
                elif ours_rel and rs.get("relative_event") and (rs["relative_event"], rs.get("offset_days")) != (ours_rel, ours_off):
                    proposals.append({"kind": "event scheduling", "target": f"event {oid}",
                                      "proposed": f"{rs.get('offset_days')} day(s) after {rs['relative_event']}",
                                      "current": f"{ours_off} day(s) after {ours_rel}", "source": src, "quote": "",
                                      "rationale": "The reference study schedules this event differently. The protocol "
                                                   "value of this study is kept."})
            rc = rr.get("auto_close")
            ours_close = _val(cal.get("auto_close_after_days"))
            if rc and ours_close is None and _is_calendar_event(ev):
                cal["auto_close_after_days"] = val(rc["after_days"], src, note="the protocol gives no visit window; from the reference study's rule")
                changed = True
            elif rc and ours_close is not None and rc["after_days"] != ours_close and \
                    (cal.get("auto_close_after_days") or {}).get("source") != src:
                proposals.append({"kind": "event auto-close", "target": f"event {oid}", "proposed": f"{rc['after_days']} day(s)",
                                  "current": f"{ours_close} day(s)", "source": src, "quote": "",
                                  "rationale": "The reference study closes this event after a different number of days. "
                                               "The protocol window of this study is kept."})
            elif not rc and ours_close is None and pattern and _is_calendar_event(ev):
                cal["auto_close_after_days"] = val(pattern[0], src, note=f"the protocol gives no visit window; pattern of the "
                                                   f"reference study ({pattern[1]} of {len(closes)} auto-close rules use {pattern[0]} day(s))")
                changed = True
            if changed:
                ev["calendar"] = cal
                ev["notifications"] = notification_proposals(ev)

        # notifications of the reference study: proposals, recipients blanked
        for oid, rr in ref["rules"].items():
            for n in rr.get("notifications") or []:
                crit = (n.get("rule") or {}).get("criteria") or {}
                had = [w for w, k in (("an email recipient", "had_email_recipient"), ("a phone recipient", "had_phone_recipient")) if n.get(k)]
                entry = {"kind": "Reference study notification", "source": src, "status": "proposal (not published)",
                         "timing": f"{crit.get('offset')} day(s) {crit.get('when')} the event start date, "
                                   f"{str((n.get('rule') or {}).get('schedule') or '').lower()} at {(n.get('rule') or {}).get('time')}",
                         "recipient": "blanked" + (f" (the reference study had {' and '.join(had)})" if had else ""),
                         "rule": n.get("rule")}
                if oid in events:
                    events[oid].setdefault("notifications", []).append(entry)
                else:
                    act = ((n.get("rule") or {}).get("actions") or [{}])[0]
                    proposals.append({"kind": "notification", "target": f"reference event {oid} (not an event of this study)",
                                      "proposed": f"{(n.get('rule') or {}).get('name')}: {entry['timing']}; subject \"{act.get('emailSubject')}\"",
                                      "current": None, "source": src, "quote": "",
                                      "rationale": "Notification of the reference study for an event this study does not have. Recipients blanked."})

        # form-at-event flags: never applied, differences are proposals (one per form and flag)
        diffs = collections.OrderedDict()
        for card in cfg.get("forms") or []:
            rf = matched.get(card.get("form_id"))
            if not rf:
                continue
            same = rf["cards"].get(str(card.get("event_oid")))
            for key in ("required", "hidden", "allow_add", "participate"):
                ours = _val(card.get(key))
                if same is not None:
                    theirs = same.get(key)
                else:
                    vals = [c.get(key) for c in rf["cards"].values() if isinstance(c.get(key), bool)]
                    theirs = (any(vals) if key != "required" else (sum(vals) * 2 > len(vals))) if vals else None
                if isinstance(theirs, bool) and isinstance(ours, bool) and theirs != ours:
                    d = diffs.setdefault((card["form_id"], key, theirs, ours), [])
                    d.append(str(card.get("event_oid")) + ("" if same is not None else " (no such event in the reference study: its other events)"))
        for (fid, key, theirs, ours), evs in diffs.items():
            proposals.append({"kind": key.replace("_", " "), "target": f"{fid} at {', '.join(evs[:6])}" + (f" and {len(evs) - 6} more" if len(evs) > 6 else ""),
                              "proposed": _show(theirs), "current": _show(ours), "source": src, "quote": "",
                              "rationale": f"The reference study has {key.replace('_', ' ')} = {_show(theirs)} for this form; "
                                           f"this study's configuration has {_show(ours)}."})

        # permission tags: names only
        if ref["permission_tags"]:
            proposals.append({"kind": "permission tags", "target": "study", "proposed": ", ".join(ref["permission_tags"]),
                              "current": ", ".join(t["name"] for t in cfg.get("permission_tags") or []) or None, "source": src, "quote": "",
                              "rationale": "Permission tags defined in the reference study. UNRESOLVED: a tag applies to a form "
                                           "in every event, and the form-to-tag link is stored outside the board JSON, so which "
                                           "forms carry which tag cannot be read. Nothing is written to the design board."})

    # SDV differences recorded by sdv_proposals.propose(carry=...)
    for d in cfg.get("sdv_differences") or []:
        proposals.append({"kind": "SDV", "target": f"{d['form']}: {', '.join(d['items'][:12])}" + (f" and {len(d['items']) - 12} more" if len(d["items"]) > 12 else ""),
                          "proposed": d["rule"], "current": d["reference"], "source": d["label"], "quote": "",
                          "rationale": f"The reference study has these items as {d['reference']}; the critical-to-quality rules "
                                       f"would make them {d['rule']}. The reference study's value is used."})
    cfg["proposals"] = proposals
