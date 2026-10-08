"""Benchmark: USDM-seeded structure vs the DDF-RA example JSONs (no sponsor, no AI).

For each example the Study Spec is seeded from the USDM file (usdm_input.read + seed_spec) and compared with a
reference read straight from the JSON by the small independent reader below:

  event accuracy      seeded study events (names, in order) vs the example's encounters
  placement recall    (encounter, activity) pairs scheduled in the example that the seeded structure has
  placement accuracy  seeded pairs that are scheduled in the example, directly or through the sub-timeline /
                      child activity of a scheduled activity (USDM's own nesting); anything else is an error
  activity -> form    share of scheduled activities the general rule layer resolves against a generic CRF library
                      (one form per CDISC domain), with the unresolved ones listed

Run:  python3 tests/usdm/benchmark.py
Example files: see examples/ATTRIBUTION.md (DDF-RA, CC-BY-4.0).
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import usdm_input

EXAMPLES = ["CDISC_Pilot_Study.json", "EliLilly_NCT03421379_Diabetes.json", "Alexion_NCT04573309_Wilsons.json"]


def reference(doc):
    """Independent, direct read of the example: encounters in chain order and scheduled (encounter, activity) pairs."""
    sd = doc["study"]["versions"][0]["studyDesigns"][0]
    enc = {e["id"]: e for e in sd["encounters"]}
    order, cur = [], next(e for e in sd["encounters"] if not e.get("previousId"))
    while cur is not None:
        order.append(cur["id"])
        cur = enc.get(cur.get("nextId"))
    direct = {(i["encounterId"], a) for tl in sd["scheduleTimelines"] for i in tl["instances"]
              if i.get("encounterId") for a in i.get("activityIds") or []}
    acts = {a["id"]: a for a in sd["activities"]}
    nested = set()  # activities reached only through another activity (its children or the timeline it runs)
    tls = {t["id"]: t for t in sd["scheduleTimelines"]}
    for a in sd["activities"]:
        nested.update(a.get("childIds") or [])
        for i in (tls.get(a.get("timelineId")) or {}).get("instances") or []:
            nested.update(i.get("activityIds") or [])
    grew = True
    while grew:  # children of nested activities are nested too
        more = {c for n in nested for c in (acts.get(n) or {}).get("childIds") or []} - nested
        nested |= more
        grew = bool(more)
    return {"events": [(i, enc[i].get("label") or enc[i].get("name")) for i in order], "direct": direct, "nested": nested}


def generic_library(std):
    """One form per CDISC domain: a study-agnostic CRF library to measure the activity -> form rule layer."""
    forms = []
    cl = std.ct.get("DOMAIN") if std is not None else None
    for t in (cl or {}).get("terms", {}).values():
        if len(t["value"]) == 2:
            forms.append({"form_id": t["value"], "form_title": (t["synonyms"] or [t["preferred_term"]])[0],
                          "cdash_domain": t["value"], "visits_assigned": [], "survey": []})
    return forms


def score(path, std=None):
    doc = json.load(open(path))
    ref = reference(doc)
    structure, problems = usdm_input.read(doc)
    spec = {"study_meta": {"protocol_number": "BENCH"}, "forms": generic_library(std)}
    summary = usdm_input.seed_spec(spec, structure, std)
    rows = spec["timepoint_csv"]["rows"]
    seeded_events = [(r["event"], r["timepoint"]) for r in rows]
    ev_ok = sum(1 for (_i, name), (_o, got) in zip(ref["events"], seeded_events) if name == got)
    oid_of = {e["usdm_id"]: e["oid"] for e in structure["events"]}
    direct = {(oid_of[e], a) for e, a in ref["direct"] if e in oid_of}
    seeded = set(usdm_input.placements(structure))
    extra = seeded - direct
    explained = {p for p in extra if p[1] in ref["nested"]}
    scheduled = {a for _e, a in seeded}
    res = usdm_input.resolve_forms(structure, spec, std)
    resolved = [a for a in scheduled if a in res["resolved"]]
    name = {a["id"]: a["label"] for a in structure["activities"]}
    forms_ok = all(set(f["visits_assigned"]) == {e["oid"] for e in structure["events"]
                                                 if set(e["activities"]) & {a for a, r in res["resolved"].items()
                                                                            if f["form_id"] in r["forms"]}}
                   for f in spec["forms"] if f["visits_assigned"])
    return {
        "example": os.path.basename(path), "usdm_version": structure["usdm_version"], "schema_findings": len(problems),
        "events_reference": len(ref["events"]), "events_seeded": len(seeded_events),
        "event_accuracy": round(ev_ok / max(len(ref["events"]), 1), 4),
        "event_oids_unique": len({o for o, _ in seeded_events}) == len(seeded_events),
        "placements_reference": len(direct), "placements_seeded": len(seeded),
        "placement_recall": round(len(direct & seeded) / max(len(direct), 1), 4),
        "placement_accuracy": round((len(direct & seeded) + len(explained)) / max(len(seeded), 1), 4),
        "placements_from_nesting": len(explained), "placement_errors": len(extra - explained),
        "events_with_day": sum(1 for e in structure["events"] if e["day"] is not None),
        "scheduled_activities": len(scheduled), "activities_resolved": len(resolved),
        "activity_resolution": round(len(resolved) / max(len(scheduled), 1), 4),
        "resolution_basis": summary["activities_resolved"], "form_placements_consistent": forms_ok,
        "unresolved_activities": sorted(name[a] for a in scheduled if a not in res["resolved"])}


def run(std=None):
    return [score(os.path.join(HERE, "examples", f), std) for f in EXAMPLES]


if __name__ == "__main__":
    import cdisc_ct
    std = cdisc_ct.load_standards()
    for s in run(std):
        print(f"\n== {s['example']} (USDM {s['usdm_version']}, {s['schema_findings']} schema findings)")
        print(f"  events:     {s['events_seeded']}/{s['events_reference']}  accuracy {s['event_accuracy']:.1%}  "
              f"unique OIDs {s['event_oids_unique']}  with day {s['events_with_day']}")
        print(f"  placements: reference {s['placements_reference']}  seeded {s['placements_seeded']}  "
              f"recall {s['placement_recall']:.1%}  accuracy {s['placement_accuracy']:.1%}  "
              f"(from nesting {s['placements_from_nesting']}, errors {s['placement_errors']})")
        print(f"  activity -> form (generic library): {s['activities_resolved']}/{s['scheduled_activities']} "
              f"= {s['activity_resolution']:.1%}  by {s['resolution_basis']}")
        print(f"  unresolved (listed for review): {', '.join(s['unresolved_activities']) or 'none'}")
