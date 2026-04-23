#!/usr/bin/env python3
"""Diagnose duplicate list _ids in the debug board.json."""
import json
from collections import Counter
from pathlib import Path

p = Path.home() / "oc-ai-pipeline" / "fixtures" / "_debug_board.json"
with p.open() as f:
    b = json.load(f)

list_ids = [l["_id"] for l in b["lists"]]
counts   = Counter(list_ids)
dupes    = {i: c for i, c in counts.items() if c > 1}

print(f"Duplicate list _ids: {dupes}")
print()
print("Events sharing duplicate _ids:")
for dup_id in dupes:
    for l in b["lists"]:
        if l["_id"] == dup_id:
            print(f"  _id={dup_id}  eventOcoid={l['eventOcoid']!r}  title={l['title']!r}")
    print()

print("Cards per listId (shows which events got cards and which didn't):")
card_list_ids = Counter(c["listId"] for c in b["cards"])
for l in b["lists"]:
    cnt = card_list_ids.get(l["_id"], 0)
    marker = "" if cnt > 0 else "   ← NO CARDS"
    print(f"  {l['eventOcoid']:40}  cards={cnt}{marker}")
