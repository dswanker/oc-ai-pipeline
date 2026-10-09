"""The visit schedule follows the protocol's own structure.

The protocol analysis names the study events its own way, and from run to run it may leave out a timepoint the
protocol defines, fold several timepoints into one event, or add an event next to the visit that already is it.
Here the protocol's schedule is read deterministically and the events are checked against it:

  columns     every column header of every Schedule of Activities table (protocol_structure finds the tables), from
              the PDF's layout text: header lines stacked over one column are joined, and a header printed once over
              several columns (a spanning header) is given to each of them
  timepoints  the timepoint lists the protocol defines elsewhere: a table whose first-column rows are time
              expressions, and a schedule footnote that a cell or a row label refers to and that enumerates time
              expressions. Time expressions are parsed generically (unit + number or range, number + unit); nothing
              is recognised by topic

Headers and timepoints that are the same visit form one UNIT, each merge with its reason: the same label, the same
time label on headers that carry no visit name of their own, or a header with no activity marked under it that has
the time label of a named visit. A unit without a time label that is not a visit column of its own (a relative
label such as "before <something>", an entry of a timepoint list) is put to one small validated AI question: does
the protocol state that it takes place at the same visit as another unit? The answer counts only with a verbatim
quote found in the protocol that names both.

Every unit must then be one event, and every event a unit (or the unscheduled / common / early-termination events
the pipeline uses):
  missing   a unit no event stands for gets an event, labelled as the protocol labels it; its forms are the forms
            of the schedule rows marked in that column (through the completeness check's row -> form mapping), or,
            for an entry of a timepoint list, the forms all other visits of that list share. Nothing is invented
  split     an event that names several units keeps its place for one of them; the others get their own event
  duplicate a second event for a unit that already has one is dropped, its forms moved to the event that stays

Everything is logged with the header or timepoint and the reason. Events change only on a fresh protocol analysis;
for a reused or edited specification the fixes are recorded and flagged. State: study_meta.schedule_from_soa.
Kill switch SCHEDULE_FROM_SOA=0 (SCHEDULE_FROM_SOA_AI=0 for the AI question alone). Study-agnostic. Never raises.
"""
from __future__ import annotations
import hashlib
import itertools
import json
import os
import re

import protocol_structure as ps

VERSION = 1
FLAG = "schedule_from_soa"
MAX_ADD = 12          # more missing events than this is not a fix-up: nothing is added, the finding is flagged
MAX_FREE = 9          # header columns without a top-line header that are tried against spanning headers
NO_PARENT = 2.0       # cost (characters) of leaving such a column without a spanning header


def enabled():
    return os.environ.get("SCHEDULE_FROM_SOA", "1") != "0"


def ai_enabled():
    return os.environ.get("SCHEDULE_FROM_SOA_AI", "1") != "0"


def _log(msg):
    print(f"[schedule] {msg}", flush=True)


# ── Time expressions (generic: a unit and a number or range) ─────────────────────

_UNITS = {"minute": ("minutes", "minute", "mins", "min"), "hour": ("hours", "hour", "hrs", "hr", "h"),
          "day": ("days", "day", "d"), "week": ("weeks", "week", "wks", "wk", "w"),
          "month": ("months", "month", "mos", "mo"), "year": ("years", "year", "yrs", "yr"),
          "cycle": ("cycles", "cycle", "c"), "visit": ("visits", "visit", "v")}
_UNIT_OF = {w: u for u, ws in _UNITS.items() for w in ws}
_U = "|".join(sorted(_UNIT_OF, key=len, reverse=True))
_SEP = r"(?:-|–|—|−|to|through)"
# label form: the unit, then a number or range (Week 2-3, W 4-5, Day -28 to Day 0)
_AT = re.compile(rf"(?<![A-Za-z0-9])({_U})\.?\s?([-−]?\d+(?:\.\d+)?)(?:\s*{_SEP}\s*(?:(?:{_U})\.?\s?)?([-−]?\d+(?:\.\d+)?))?"
                 rf"(?![A-Za-z0-9])", re.I)
# quantity form: a number or range, then the unit (2-4 hours, 24 hrs, 6-8 days)
_DUR = re.compile(rf"(?<![A-Za-z0-9.#])(\d+(?:\.\d+)?)(?:\s*{_SEP}\s*(\d+(?:\.\d+)?))?\s?({_U})(?![A-Za-z0-9])", re.I)
_PAREN = re.compile(r"\([^()]*\)")
# grammar words that make a label a point relative to something else, not a visit name of its own
_RELATIVE = {"post", "pre", "after", "before", "prior", "following"}
_ORDINAL = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6", "seventh": "7",
            "eighth": "8", "ninth": "9", "tenth": "10", "1st": "1", "2nd": "2", "3rd": "3"}
_FUNCTION = {"the", "a", "an", "of", "at", "and", "or", "to", "for", "in", "on", "is", "are", "be", "by", "with", "as"}


def _num(s):
    v = float(str(s).replace("−", "-"))
    return int(v) if v == int(v) else v


def times(text):
    """[{"kind": "at" | "dur", "unit", "lo", "hi", "start", "end", "text", "inner": inside brackets}] in text order."""
    t = str(text or "")
    inner = [m.span() for m in _PAREN.finditer(t)]
    out, taken = [], []
    for kind, rx in (("at", _AT), ("dur", _DUR)):
        for m in rx.finditer(t):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            if kind == "at":
                word, lo, hi = m.group(1), m.group(2), m.group(3)
                if len(word) == 1 and not word.isupper():
                    continue   # a single lower-case letter is not a unit label
            else:
                lo, hi, word = m.group(1), m.group(2), m.group(3)
            try:
                lo = _num(lo)
                hi = _num(hi) if hi is not None else lo
            except ValueError:
                continue
            taken.append(m.span())
            out.append({"kind": kind, "unit": _UNIT_OF[word.lower()], "lo": lo, "hi": hi, "start": m.start(),
                        "end": m.end(), "text": m.group(0), "inner": any(s <= m.start() and m.end() <= e for s, e in inner)})
    out.sort(key=lambda x: x["start"])
    return out


def _key(t):
    return (t["kind"], t["unit"], t["lo"], t["hi"])


def window_text(key):
    if not key:
        return ""
    kind, unit, lo, hi = key
    rng = f"{lo}" if lo == hi else f"{lo}-{hi}"
    return f"{unit} {rng}" if kind == "at" else f"{rng} {unit}s"


def _words(text):
    out = []
    for w in re.findall(r"[A-Za-z0-9]+", str(text or "")):
        w = _ORDINAL.get(w.lower(), w.lower())
        if w not in _FUNCTION:
            out.append(w)
    return out


def describe(label):
    """What a header / timepoint / event label says: its time labels and the words that are not time."""
    label = re.sub(r"\s+", " ", str(label or "")).strip()
    ts = times(label)
    outer = [t for t in ts if not t["inner"]]
    rest = label
    for t in sorted(ts, key=lambda x: -x["start"]):
        rest = rest[:t["start"]] + " " + rest[t["end"]:]
    tags = []
    for m in _PAREN.finditer(rest):
        tags += [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", m.group(0)) if w.isupper() and 2 <= len(w) <= 6]
    name = _words(_PAREN.sub(" ", rest))
    relative = bool(name) and name[0] in _RELATIVE
    return {"label": label, "windows": [_key(t) for t in ts], "primary": _key(outer[0]) if outer else None,
            "outer": [_key(t) for t in outer], "name": name, "tags": tags,
            "time_only": not name or relative, "relative": relative}


# ── Layout helpers ───────────────────────────────────────────────────────────────

_RUN = re.compile(r"\S+(?: \S+)*")
_MARK = re.compile(r"^\(?[Xx✓✔√●•■]\)?(?:\s?\d{1,2}(?:\s?,\s?\d{1,2})*|[a-z*†‡§]{1,2})?\)?$")
_NOTE = re.compile(r"^\s*(\d{1,2})[.)]\s*(\S.*)$")
_BULLET = re.compile(r"^\s*(?:[•▪◦●■*\-–]|o(?=\s))\s*")
_TABLE = re.compile(r"^\s*Table\s+\d+[A-Za-z]?\s*[:.]\s*\S", re.I)


def _toks(line):
    return [(m.start(), m.end(), m.group(0)) for m in _RUN.finditer(line)]


def _mid(tok):
    return (tok[0] + tok[1]) / 2.0


def _join(parts):
    out = ""
    for p in parts:
        if not out:
            out = p
        elif out.endswith("-") and p[:1].islower():
            out += p
        else:
            out += " " + p
    return re.sub(r"\s+", " ", out).strip()


def _columns(header):
    """[{"label", "x"}] from the header lines of a grid (each a list of (start, end, text)). Tokens stacked over one
    another are one column. A column whose header starts below a line is offered to the headers of that line on
    either side: it takes the one whose text then sits centred over its columns (a spanning header)."""
    toks = [(li, s, e, t) for li, line in enumerate(header) for s, e, t in line]
    if len(toks) < 2:
        return []
    over = lambda a, b: a[1] < b[2] and b[1] < a[2]
    spanner = set()
    for i, a in enumerate(toks):
        hit = {}
        for j, b in enumerate(toks):
            if i != j and a[0] != b[0] and over(a, b):
                hit[b[0]] = hit.get(b[0], 0) + 1
        if any(n >= 2 for n in hit.values()):
            spanner.add(i)
    parent = list(range(len(toks)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(toks):
        for j in range(i + 1, len(toks)):
            b = toks[j]
            if i not in spanner and j not in spanner and a[0] != b[0] and over(a, b):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(toks)):
        if i not in spanner:
            groups.setdefault(find(i), []).append(i)
    cols = []
    for members in groups.values():
        members.sort(key=lambda i: toks[i][0])
        cols.append({"members": members, "x": sum((toks[i][1] + toks[i][2]) / 2.0 for i in members) / len(members),
                     "top": toks[members[0]][0], "prefix": []})
    cols.sort(key=lambda c: c["x"])
    for li in sorted({t[0] for t in toks}):
        heads = []   # the headers on this line: (centre, text, indexes of the columns they already stand over)
        for i, t in enumerate(toks):
            if t[0] != li:
                continue
            own = [k for k, c in enumerate(cols) if i in c["members"]] if i not in spanner else \
                  [k for k, c in enumerate(cols) if any(over(t, toks[m]) for m in c["members"])]
            if own:
                # a header that is alone in its column may be a spanning header printed between its columns
                solo = i not in spanner and len(own) == 1 and len(cols[own[0]]["members"]) == 1
                heads.append(((t[1] + t[2]) / 2.0, t[3], own, solo, i))
        heads.sort()
        free = [k for k, c in enumerate(cols) if c["top"] > li]
        if not heads or not free or len(free) > MAX_FREE:
            for h in heads:
                for k in h[2]:
                    if cols[k]["top"] > li:
                        cols[k]["prefix"].append(h[1])
                        cols[k]["top"] = li
            continue
        owned = {k for h in heads for k in h[2]}
        free = [k for k in free if k not in owned]
        options = []
        for k in free:
            left = max((n for n, h in enumerate(heads) if max(h[2]) < k), default=None)
            right = min((n for n, h in enumerate(heads) if min(h[2]) > k), default=None)
            options.append([None] + [o for o in (left, right) if o is not None])
        best = None
        for choice in itertools.product(*options) if free else [()]:
            took = {n for n in choice if n is not None}
            groups_ = {n: ([] if h[3] and n in took else list(h[2])) for n, h in enumerate(heads)}
            ok = True
            for k, n in zip(free, choice):
                if n is not None:
                    groups_[n].append(k)
            for n, ks in groups_.items():
                ks.sort()
                # a header stands over neighbouring columns only
                if any(b - a > 1 and not all(m in ks or m in heads[n][2] for m in range(a, b)) for a, b in zip(ks, ks[1:])):
                    ok = False
            if not ok:
                continue
            cost = sum(abs(heads[n][0] - sum(cols[k]["x"] for k in ks) / len(ks)) for n, ks in groups_.items())
            cost += NO_PARENT * sum(1 for n in choice if n is None)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, choice)
        for h in heads:
            for k in h[2]:
                if cols[k]["top"] > li:
                    cols[k]["prefix"].append(h[1])
                    cols[k]["top"] = li
        for k, n in zip(free, best[1] if best else ()):
            if n is not None:
                cols[k]["prefix"].append(heads[n][1])
                cols[k]["top"] = li
                if heads[n][3]:
                    cols[heads[n][2][0]]["spans"] = True   # it stands over other columns: not a column itself
                else:
                    for own in heads[n][2]:               # ... and over its own column, whose name it is not
                        if heads[n][4] in cols[own]["members"]:
                            cols[own]["members"].remove(heads[n][4])
                            cols[own]["prefix"].append(heads[n][1])
    out = []
    for c in cols:
        label = _join([toks[i][3] for i in c["members"]])
        if re.search(r"[A-Za-z0-9]", label) and not c.get("spans"):
            # "group": the spanning header over the column (a period or a reference point, not the visit's name)
            out.append({"label": label, "group": _join(c["prefix"]), "x": c["x"]})
    return out if len(out) >= 2 else []


def _grids(lines):
    """(grids, notes text lines, reached_end): the grids in the lines of one schedule table on one page. A grid is
    {"columns": [{"label", "x"}], "rows": [{"label", "marks": {column index: cell}}]}; a grid without header lines
    of its own (the table continues from the page before) has columns None."""
    body = []
    for ln in lines:
        if ps._is_prose(ln) or ps._is_note(ln):
            break
        body.append(ln.rstrip())
    reached_end = len(body) == len(lines)
    tail = lines[len(body):]
    indents = [len(ln) - len(ln.lstrip()) for ln in body if ln.strip() and re.search(r"\S\s{3,}\S", ln.strip())]
    if not indents:
        return [], tail, reached_end
    left = min(indents)
    grids, grid, header = [], None, []
    cur = None   # the row being read: {"label": [...], "cells": bool, "marks": [(x, text)]}

    def flush():
        nonlocal cur
        if cur and cur["cells"] and grid is not None:
            label = re.sub(r"\s+", " ", " ".join(cur["label"])).strip()
            if re.search(r"[A-Za-z]{2}", label):
                grid["rows"].append({"label": label, "cells": cur["marks"]})
        cur = None

    def start_grid():
        nonlocal grid, header
        cols = _columns(header) if sum(len(h) for h in header) >= 3 else []
        header = []
        if cols or grid is None:
            grid = {"columns": cols or None, "rows": []}
            grids.append(grid)

    for ln in body:
        if not ln.strip():
            flush()
            continue
        toks = _toks(ln)
        indent = toks[0][0]
        marks = [(_mid(t), t[2]) for t in toks if _MARK.match(t[2])]
        if indent > left + 2:
            if marks and cur is not None and not header:
                cur["marks"] += marks
                cur["cells"] = True
            elif not marks:
                flush()
                header.append(toks)
            continue
        label, cells = toks[0][2], toks[1:]
        if cells and not marks and (grid is None or header) and cur is None and not (grid and grid["rows"] and not header):
            # a header line that starts in the first column (the title of the row-label column): its cells are headers
            header.append(cells)
            continue
        if header or grid is None:
            flush()
            start_grid()
        if cur and cur["cells"] and (label[:1].isupper() or label[:1].isdigit()):
            flush()
        if cur is None:
            cur = {"label": [], "cells": False, "marks": []}
        cur["label"].append(label)
        cur["cells"] = cur["cells"] or bool(cells)
        cur["marks"] += [(_mid(t), t[2]) for t in cells if _MARK.match(t[2])]
    flush()
    return grids, tail, reached_end


def _notes(tail):
    """{number: text} of the numbered notes in the lines after a grid."""
    out, cur = {}, None
    for ln in tail:
        m = _NOTE.match(ln)
        if m and (cur is None or int(m.group(1)) == cur + 1 or int(m.group(1)) not in out):
            cur = int(m.group(1))
            out[cur] = m.group(2).strip()
        elif cur is not None and ln.strip() and not ps._caption(ln) and not _TABLE.match(ln):
            out[cur] += " " + ln.strip()
    return {n: re.sub(r"\s+", " ", t) for n, t in out.items()}


def _place(cells, columns):
    """{column index: cell text} for the marked cells of a row: each goes to the nearest column."""
    if not columns:
        return {}
    xs = [c["x"] for c in columns]
    gaps = sorted(b - a for a, b in zip(xs, xs[1:]))
    tol = max(4.0, 0.6 * (gaps[len(gaps) // 2] if gaps else 8.0))
    out = {}
    for x, text in cells:
        k = min(range(len(xs)), key=lambda i: abs(xs[i] - x))
        if abs(xs[k] - x) <= tol:
            out[k] = text
    return out


def read_tables(pages):
    """[{"table", "columns": [{"label", "grid"}], "rows": [{"label", "marks": {column index: cell}}],
    "notes": {n: text}}] for every schedule table of the layout-text pages (tables continue over page breaks)."""
    tables, order, carry = {}, [], None
    pages = ps._without_running(pages or [])
    try:
        for page in pages:
            lines = page.split("\n")
            caps = [i for i, ln in enumerate(lines) if ps._caption(ln)]
            segments = []
            if carry and (not caps or caps[0] > 3):
                segments.append((carry, lines[:caps[0]] if caps else lines))
            for n, i in enumerate(caps):
                end = caps[n + 1] if n + 1 < len(caps) else len(lines)
                segments.append((re.sub(r"\s+", " ", lines[i]).strip(), lines[i + 1:end]))
            carry = None
            for name, seg in segments:
                grids, tail, reached_end = _grids(seg)
                had_rows = any(g["rows"] for g in grids)
                if not had_rows and not (name in tables and _notes(tail)):
                    continue
                t = tables.get(name)
                if t is None:
                    t = tables[name] = {"table": name, "columns": [], "rows": [], "notes": {}, "_last": None}
                    order.append(name)
                for g in grids:
                    if g["columns"]:
                        base = len(t["columns"])
                        t["columns"] += [{"label": c["label"], "group": c["group"], "x": c["x"], "grid": base}
                                         for c in g["columns"]]
                        t["_last"] = (base, g["columns"])
                    if t["_last"] is None:
                        continue
                    base, cols = t["_last"]
                    for r in g["rows"]:
                        t["rows"].append({"label": r["label"],
                                          "marks": {base + k: v for k, v in _place(r["cells"], cols).items()}})
                for n, text in _notes(tail).items():
                    t["notes"].setdefault(n, text)
                carry = name if (reached_end and had_rows) else None
    except Exception as e:
        _log(f"schedule tables not read: {type(e).__name__}: {e}")
    out = []
    for name in order:
        t = tables[name]
        t.pop("_last", None)
        if len(t["columns"]) >= 2:
            out.append(t)
    return out


# ── Timepoint lists ──────────────────────────────────────────────────────────────

def _series(text):
    """Runs of time expressions that follow one another in a list (comma / "and" between them), brackets aside."""
    flat = _PAREN.sub(lambda m: " " * len(m.group(0)), text)
    ts = [t for t in times(text) if not t["inner"]]
    runs, cur = [], []
    for t in ts:
        if cur and re.fullmatch(r"[\s,;&]*(?:and|or)?[\s,;]*", flat[cur[-1]["end"]:t["start"]]):
            cur.append(t)
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = [t]
    if len(cur) > 1:
        runs.append(cur)
    return runs


def _with_bracket(text, t):
    """The time expression with the bracket that follows it directly ("W 1 (6-8 days)")."""
    m = re.match(r"\s*\([^()]*\)", text[t["end"]:])
    return (t["text"] + " " + m.group(0).strip()) if m else t["text"]


def note_timepoints(table):
    """[{"label", "note"}] from the numbered notes of a schedule table that a cell or a row label refers to and
    that enumerate time expressions (a list of three or more)."""
    refs = set()
    for r in table["rows"]:
        for cell in r["marks"].values():
            refs |= {int(n) for n in re.findall(r"\d{1,2}", cell)}
        m = re.search(r"[A-Za-z)](\d{1,2})(?:\s|$)", r["label"])
        if m:
            refs.add(int(m.group(1)))
    out = []
    for n, text in sorted(table["notes"].items()):
        if n not in refs:
            continue
        runs = _series(text)
        if not any(len(run) >= 3 for run in runs):
            continue
        listed = {(t["start"], t["end"]) for run in runs for t in run}
        for t in times(text):
            if t["inner"]:
                continue
            if (t["start"], t["end"]) in listed or t["kind"] == "at":
                out.append({"label": _with_bracket(text, t), "note": n})
    return out


def timepoint_tables(pages):
    """[{"table", "timepoints": [{"label"}]}] for the tables (other than schedule tables) whose first-column rows
    are time expressions: at least three rows, most of them a time expression. A row ending in a colon heads the
    rows under it and is part of their label."""
    out = []
    pages = ps._without_running(pages or [])
    try:
        for page in pages:
            lines = page.split("\n")
            caps = [i for i, ln in enumerate(lines) if _TABLE.match(ln) and not re.search(r"(?:\.\s?){3,}", ln)]
            for n, i in enumerate(caps):
                if ps._caption(lines[i]):
                    continue
                end = caps[n + 1] if n + 1 < len(caps) else len(lines)
                body = [ln for ln in lines[i + 1:end]]
                rows = [ln for ln in body if ln.strip()]
                if len(rows) < 4:
                    continue
                left = min(len(ln) - len(ln.lstrip()) for ln in rows)
                items, group, first = [], "", True
                for ln in rows:
                    if len(ln) - len(ln.lstrip()) > left + 12:
                        continue   # text of another column
                    if first:
                        first = False
                        continue   # the header row of the table
                    bullet = bool(_BULLET.match(ln))
                    cell = re.split(r"\s{3,}", _BULLET.sub("", ln).strip())[0].strip()
                    if not cell:
                        continue
                    if not bullet and not cell.endswith(":") and len(cell.split()) >= 8 and not times(cell[:30]):
                        break   # running text after the table
                    if cell.endswith(":") and not times(cell):
                        group = cell.rstrip(":").strip()
                        continue
                    items.append((group, cell))
                timed = [1 for _g, c in items if any(not t["inner"] for t in times(c))]
                if len(items) < 3 or len(timed) * 5 < len(items) * 3:
                    continue
                tps = []
                for g, c in items:
                    outer = [t for t in times(c) if not t["inner"]]
                    labels = [c] if len(outer) <= 1 else [_with_bracket(c, t) for t in outer]
                    for lab in labels:
                        tps.append({"label": lab, "group": g})
                out.append({"table": re.sub(r"\s+", " ", lines[i]).strip(), "timepoints": tps})
    except Exception as e:
        _log(f"timepoint tables not read: {type(e).__name__}: {e}")
    return out


# ── Units: the visits the protocol's schedule defines ────────────────────────────

def _label_key(d):
    return (tuple(d["name"]), d["primary"], tuple(sorted(t.lower() for t in d["tags"])))


def _numbers(d):
    return {w for w in d["name"] + d.get("group_words", []) if w.isdigit()}


def _described(label, group=""):
    """describe() of a header / timepoint; what stands over it (a spanning header, the heading of its rows) is
    shown in its label but is not its name."""
    d = describe(label)
    g = describe(group) if group else None
    if g:
        d["label"] = f"{g['label'].rstrip(':')}: {d['label']}"
        d["group_words"] = g["name"]
        d["group_primary"] = g["primary"]
    return d


def full_label(c):
    return _described(c["label"], c.get("group") or "")["label"]


def build_units(tables, tp_tables):
    """(units, merges). A unit: {"id", "label", "labels", "name", "tags", "primary", "windows", "time_only",
    "relative", "sources": [{"kind": "column" | "table" | "note", "table", "label"}], "rows": [(table, row label)],
    "marked": bool, "listed": bool, "lists": [...]}. merges: [{"label", "into", "reason"}]."""
    items = []
    for t in tables:
        for k, c in enumerate(t["columns"]):
            d = _described(c["label"], c.get("group"))
            rows = [(t["table"], r["label"]) for r in t["rows"] if k in r["marks"]]
            items.append(dict(d, sources=[{"kind": "column", "table": t["table"], "label": d["label"]}], rows=rows,
                              marked=bool(rows), listed=False, lists=[], seq=(0, len(items))))
        for tp in note_timepoints(t):
            d = describe(tp["label"])
            where = f"{t['table']}, note {tp['note']}"
            items.append(dict(d, sources=[{"kind": "note", "table": where, "label": d["label"]}], rows=[], marked=False,
                              listed=True, lists=[where], seq=(1, len(items))))
    for t in tp_tables:
        for tp in t["timepoints"]:
            d = _described(tp["label"], tp.get("group"))
            items.append(dict(d, sources=[{"kind": "table", "table": t["table"], "label": d["label"]}], rows=[],
                              marked=False, listed=True, lists=[t["table"]], seq=(1, len(items))))
    parent = list(range(len(items)))
    why = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(i, j, reason):
        a, b = find(i), find(j)
        if a != b:
            lo, hi = min(a, b), max(a, b)
            parent[hi] = lo
            why[hi] = reason
            m = {"label": items[max(i, j)]["label"], "into": items[min(i, j)]["label"], "reason": reason}
            if m["label"].lower() != m["into"].lower() and not any(
                    x["label"] == m["label"] and x["into"] == m["into"] for x in merges):
                merges.append(m)

    merges = []
    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if _label_key(a) == _label_key(b):
                union(i, j, "the same label, written differently")
            elif a["time_only"] and b["time_only"] and a["primary"] and a["primary"] == b["primary"]:
                na, nb = _numbers(a), _numbers(b)
                ga, gb = a.get("group_primary"), b.get("group_primary")
                if not (na and nb and na != nb) and not (ga and gb and ga != gb):
                    union(i, j, f"the same time label ({window_text(a['primary'])}) and no visit name of their own")
            elif not a["windows"] and not b["windows"] and a["name"] and b["name"] and (
                    a["listed"] or b["listed"] or a["time_only"] or b["time_only"]):
                wa, wb = set(a["name"] + a.get("group_words", [])), set(b["name"] + b.get("group_words", []))
                if wa <= wb or wb <= wa:
                    union(i, j, "one label contains the other (or is listed under it) and neither has a time label")
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    # a header with nothing marked under it that has the time label of exactly one named visit is that visit
    named = {}
    for root, members in groups.items():
        if any(not items[i]["time_only"] for i in members):
            for p in {items[i]["primary"] for i in members if items[i]["primary"]}:
                named.setdefault(p, []).append(root)
    for root, members in list(groups.items()):
        ms = [items[i] for i in members]
        p = ms[0]["primary"]
        if all(m["time_only"] for m in ms) and p and not any(m["marked"] or m["listed"] for m in ms) and len(named.get(p, [])) == 1:
            target = named[p][0]
            merges.append({"label": ms[0]["label"], "into": items[groups[target][0]]["label"],
                           "reason": f"nothing is marked under this header and it has the time label of that visit "
                                     f"({window_text(p)})"})
            groups[target] += members
            del groups[root]
    units = []
    for root, members in sorted(groups.items(), key=lambda kv: min(items[i]["seq"] for i in kv[1])):
        ms = sorted((items[i] for i in members), key=lambda m: (m["sources"][0]["kind"] != "column", m["time_only"], m["seq"]))
        head = ms[0]
        labels = list(dict.fromkeys(m["label"] for m in ms))
        units.append({"id": f"U{len(units) + 1}", "label": head["label"], "labels": labels,
                      "name": head["name"] if not head["time_only"] else [], "words": sorted({w for m in ms for w in m["name"]}),
                      "tags": sorted({t for m in ms for t in m["tags"]}), "primary": head["primary"],
                      "windows": list(dict.fromkeys(w for m in ms for w in m["windows"])),
                      "time_only": all(m["time_only"] for m in ms), "relative": all(m["relative"] for m in ms),
                      "sources": [s for m in ms for s in m["sources"]],
                      "rows": list(dict.fromkeys(r for m in ms for r in m["rows"])),
                      "marked": any(m["marked"] for m in ms), "listed": any(m["listed"] for m in ms),
                      "column": any(s["kind"] == "column" for m in ms for s in m["sources"]),
                      "named_column": any(not m["time_only"] and m["sources"][0]["kind"] == "column" for m in ms),
                      "lists": list(dict.fromkeys(x for m in ms for x in m["lists"]))})
    return units, merges


_DAYS = {"minute": 1 / 1440.0, "hour": 1 / 24.0, "day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}


def ordered(units, tables, tp_tables, merged=None):
    """Unit ids in schedule order: a unit with a study-time label by that time; the others right after the unit
    that precedes them where the protocol lists them (the column to their left, the timepoint above), or after
    the unit they are the same visit as (merged: {unit id: unit id})."""
    by_label = {}
    for u in units:
        for lab in u["labels"]:
            by_label[lab.lower()] = u["id"]
    prev = {}
    seqs = [[full_label(c) for c in t["columns"] if c["grid"] == g] for t in tables for g in sorted({c["grid"] for c in t["columns"]})]
    seqs += [[full_label(tp) for tp in t["timepoints"]] for t in tp_tables]
    for seq in seqs:
        ids = [by_label.get(lab.lower()) for lab in seq]
        for a, b in zip(ids, ids[1:]):
            if a and b and a != b:
                prev.setdefault(b, a)
    for a, b in (merged or {}).items():
        prev[a] = b
    timed = {}
    for u in units:
        p = u["primary"]
        if p and p[0] == "at" and p[1] in _DAYS:
            timed[u["id"]] = (p[2] * _DAYS[p[1]], p[3] * _DAYS[p[1]], 0 if u["time_only"] else 1)
    order = [uid for uid, _k in sorted(timed.items(), key=lambda kv: kv[1])]
    rest = [u["id"] for u in units if u["id"] not in timed]
    for _ in range(len(rest) + 1):
        for uid in list(rest):
            p = prev.get(uid)
            if p is None:
                order.insert(0 if not [x for x in order if x not in timed] else
                             max(order.index(x) for x in order if x not in timed) + 1, uid)
                rest.remove(uid)
            elif p in order:
                # after its predecessor and after anything already placed behind that predecessor the same way
                k = order.index(p) + 1
                while k < len(order) and order[k] not in timed and prev.get(order[k]) in order[:k]:
                    k += 1
                order.insert(k, uid)
                rest.remove(uid)
    return order + rest


# ── The AI question: is a unit without a time label the same visit as another? ───

PROMPT = """You read the visit schedule of a clinical trial PROTOCOL.

ENTRIES are column headers of its Schedule of Activities tables and timepoints it lists in other tables or
footnotes. The QUESTION entries have no time label of their own. For each QUESTION entry decide: does the protocol
STATE that it takes place at the same visit (the same day, the same attendance) as one of the OTHER entries?

Rules:
1. Answer "same_as" only when a sentence, a footnote or a table of the protocol states it. That two entries share a
   word, or that it would be usual, is not a statement.
2. Give the statement as "quote", copied VERBATIM from the protocol (one sentence or one footnote sentence, at most
   300 characters). A quote that is not found in the protocol text is discarded.
3. "same_as" must be the id of one of the OTHER entries, never of a QUESTION entry.
4. When the protocol does not state it, the entry is a visit of its own: return it under "own_visit".
5. Answer for EVERY QUESTION entry, once.

Return ONLY JSON:
{"same_visit": [{"entry": "<question entry id>", "same_as": "<other entry id>", "quote": "<verbatim protocol text>"}],
 "own_visit": ["<question entry id>"]}
"""


def questions(units):
    """The units put to the AI question: no time label, and not a visit column with a name of its own."""
    return [u for u in units if not u["windows"] and not u["named_column"]]


def build_request(units, schedule_text):
    """(prompt, extra_text) or None when no unit needs the question."""
    qs = questions(units)
    others = [u for u in units if u not in qs]
    if not qs or not others or not str(schedule_text or "").strip():
        return None
    line = lambda u: f"{u['id']} | {' / '.join(u['labels'][:4])} | {'; '.join(dict.fromkeys(s['table'] for s in u['sources']))}"
    extra = ("QUESTION ENTRIES (id | label(s) | where it is from):\n" + "\n".join(line(u) for u in qs)
             + "\n\nOTHER ENTRIES (id | label(s) | where it is from):\n" + "\n".join(line(u) for u in others)
             + "\n\nPROTOCOL TEXT (the pages with the schedule tables, their footnotes and the timepoint tables):\n"
             + str(schedule_text)[:120_000])
    return PROMPT, extra


def validate_answer(units, response_text, protocol_text):
    """{"merged": {question unit id: {"into", "quote"}}, "rejected": {reason: n}, "ok": bool}. A merge counts only
    with a quote found in the protocol that names both units (a word of each)."""
    import protocol_forms as pf
    out, rejected = {}, {}

    def rej(w):
        rejected[w] = rejected.get(w, 0) + 1

    data = pf._parse(response_text)
    if not isinstance(data, dict) or not isinstance(data.get("same_visit", []), list):
        return {"merged": {}, "rejected": {"unparseable response": 1}, "ok": False}
    by_id = {u["id"]: u for u in units}
    q_ids = {u["id"] for u in questions(units)}
    squashed = pf._squash(protocol_text)
    for it in data.get("same_visit") or []:
        if not isinstance(it, dict):
            continue
        a, b, quote = str(it.get("entry") or "").strip(), str(it.get("same_as") or "").strip(), str(it.get("quote") or "").strip()
        if a not in q_ids or a in out:
            rej("not a question entry")
            continue
        if b not in by_id or b in q_ids:
            rej("the other entry is not a visit of its own")
            continue
        if not pf.quote_in(quote, squashed):
            rej("quote not found in the protocol")
            continue
        qw = set(_words(quote))
        names = lambda u: {w for w in u["words"] if w not in _RELATIVE} | {w.lower() for w in u["tags"]}
        if not (names(by_id[a]) & qw) or not (names(by_id[b]) & qw):
            rej("the quote does not name both entries")
            continue
        out[a] = {"into": b, "quote": quote[:400]}
    return {"merged": out, "rejected": rejected, "ok": True}


# ── Events against units ─────────────────────────────────────────────────────────

_SPECIAL = re.compile(r"unsched|unschd|unsch\b|common|early[\s_\-]*(?:term|disc|withdr)|\bET\b", re.I)


def _rows(spec):
    return [r for r in ((spec.get("timepoint_csv") or {}).get("rows") or []) if isinstance(r, dict) and r.get("event")]


def events_of(spec):
    """[{"oid", "text", "name"}] in the order of the specification."""
    names = {}
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict):
        for m in soe.get("visit_mappings") or []:
            if isinstance(m, dict) and m.get("target_oid"):
                names[str(m["target_oid"])] = str(m.get("target_name") or "")
    out, seen = [], set()
    for r in _rows(spec):
        oid = str(r["event"])
        if oid in seen:
            continue
        seen.add(oid)
        out.append({"oid": oid, "text": str(r.get("timepoint") or ""), "name": names.get(oid, ""), "arm": r.get("arm")})
    return out


def is_special(ev):
    return bool(_SPECIAL.search(f"{ev['oid'].replace('_', ' ')} {ev['text']} {ev['name']}"))


def _event_profile(ev, canon):
    """What an event says about itself, in its label, its name and its OID; what stands in brackets is kept
    apart (a bracket mentions, it does not name)."""
    oid_text = re.sub(r"^SE[_ ]", "", ev["oid"]).replace("_", " ")
    texts = [ev["text"], ev["name"]]
    wins, outer = set(), set()
    for t in texts + [oid_text]:
        d = describe(t)
        wins |= set(d["windows"])
        outer |= set(d["outer"])
    full = " ".join(texts + [oid_text])
    inner_text = " ".join(m.group(0)[1:-1] for m in _PAREN.finditer(full))
    return {"windows": wins, "outer": outer, "outer_words": set(_words(_PAREN.sub(" ", full))),
            "words": set(_words(_PAREN.sub(" ", full))) | set(_words(inner_text)),
            "canon": set(_words(canon(" ".join(texts)))), "text": " ".join(texts)}


def _acronym_in(tag, text):
    words = re.findall(r"[A-Za-z]+", str(text or ""))
    n = len(tag)
    return any("".join(w[0] for w in words[i:i + n]).upper() == tag.upper() for i in range(len(words) - n + 1))


def score(unit, prof, canon):
    """How well an event stands for a unit: its time label (3), its name (up to 3), a name the protocol gives it
    in brackets (3). What the event only mentions in brackets counts half. An event that carries the time labels
    of several units is scored on names only."""
    s = 0.0
    if not prof.get("folded"):
        if unit["primary"] and unit["primary"] in prof["outer"]:
            s += 3.25   # an event that carries one visit's time label and another visit's name is the first
        elif unit["primary"] and unit["primary"] in prof["windows"]:
            s += 1.5
        elif any(w in prof["windows"] for w in unit["windows"]):
            s += 1.0
    weight = lambda w: 1.0 if w in prof["outer_words"] else 0.5 if w in prof["words"] else 0.0
    name = unit["name"] or ([w for w in unit["words"] if w not in _RELATIVE] if not unit["primary"] else [])
    if name:
        got = sum(weight(w) for w in name)
        nums = [w for w in name if w.isdigit()]
        if nums and not all(w in prof["words"] for w in nums):
            got = 0   # the same name with another number is another visit
        s += 3.0 * got / len(name)
    for tag in unit["tags"]:
        ct = set(_words(canon(tag)))
        if tag.lower() in prof["words"] or (ct and ct <= prof["canon"] and ct != {tag.lower()}) or _acronym_in(tag, prof["text"]):
            s += 3.0
            break
    return round(s, 3)


def match_events(units, events, merged, canon):
    """({unit id: event oid}, {event oid: unit id it duplicates}, [unmapped event oids], {oid: profile}).
    merged: {unit id: unit id it is the same visit as} (AI-confirmed)."""
    import standards_global as smg
    target = lambda uid: merged.get(uid, uid)
    live = [u for u in units if u["id"] not in merged]
    profs = {}
    for ev in events:
        p = _event_profile(ev, canon)
        p["folded"] = len({u["id"] for u in live if u["primary"] and u["primary"] in p["windows"]}) >= 2 and \
            len({u["primary"] for u in live if u["primary"] and u["primary"] in p["windows"]}) >= 2
        profs[ev["oid"]] = p
    scores, best, later = {}, {}, {}
    open_q = {u["id"] for u in questions(units) if u["id"] not in merged}
    for ev in events:
        if is_special(ev):
            continue
        for u in units:
            sc = score(u, profs[ev["oid"]], canon)
            uid = target(u["id"])
            if sc < 3.0:
                continue
            if uid in open_q:
                later[(ev["oid"], uid)] = sc   # a unit that is not established as a visit never takes an event
                continue                       # from one that is
            scores[(ev["oid"], uid)] = max(scores.get((ev["oid"], uid), 0), sc)
            if sc > best.get(ev["oid"], (0, None))[0]:
                best[ev["oid"]] = (sc, uid)
    chosen = smg.assign(scores)
    for (oid, uid), sc in sorted(later.items(), key=lambda kv: -kv[1]):
        if oid not in chosen and oid not in best and uid not in chosen.values():
            chosen[oid] = uid
    unit_event = {uid: oid for oid, uid in chosen.items()}
    dup, unmapped = {}, []
    for ev in events:
        oid = ev["oid"]
        if is_special(ev) or oid in chosen:
            continue
        if oid in best and best[oid][1] in unit_event:
            dup[oid] = best[oid][1]
        else:
            unmapped.append(oid)
    return unit_event, dup, unmapped, profs


# ── Fix-ups ──────────────────────────────────────────────────────────────────────

def _slug(label, taken):
    base = "SE_" + re.sub(r"[^A-Z0-9]+", "_", str(label).upper()).strip("_")[:40].strip("_")
    out, i = base, 2
    while out in taken:
        out, i = f"{base}_{i}", i + 1
    return out


def _row_forms(spec):
    """{squashed schedule-row label: [form ids]} through the completeness check: checklist row -> its assessments
    -> the form each maps to."""
    import protocol_forms as pf
    st = pf.state(spec)
    key = lambda s: re.sub(r"[^a-z0-9]+", "", str(s).lower())
    entry_label = {c["id"]: key(c["label"]) for c in st.get("checklist") or [] if c.get("type") == "row"}
    have = {str(f.get("form_id")) for f in spec.get("forms") or [] if isinstance(f, dict)}
    out = {}
    for r in st.get("assessments") or []:
        if not r.get("form") or str(r["form"]) not in have:
            continue
        for e in r.get("entries") or []:
            if e in entry_label:
                bucket = out.setdefault(entry_label[e], [])
                if str(r["form"]) not in bucket:
                    bucket.append(str(r["form"]))
    return out


def _fixed_everywhere(form):
    vs = [str(v).upper() for v in form.get("visits_assigned") or []]
    return any("COMMON" in v or v == "ALL_EVENTS" for v in vs)


def _move_forms(spec, old, new):
    for f in spec.get("forms") or []:
        if isinstance(f, dict) and isinstance(f.get("visits_assigned"), list) and old in f["visits_assigned"]:
            f["visits_assigned"] = list(dict.fromkeys(new if v == old else v for v in f["visits_assigned"]))
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
        seen, kept = set(), []
        for p in soe["form_placements"]:
            if isinstance(p, dict) and p.get("target_visit_oid") == old:
                p = dict(p, target_visit_oid=new)
            k = (p.get("target_visit_oid"), p.get("form_id")) if isinstance(p, dict) else id(p)
            if k not in seen:
                seen.add(k)
                kept.append(p)
        soe["form_placements"] = kept


def _drop_event(spec, oid):
    tc = spec.get("timepoint_csv") or {}
    if isinstance(tc.get("rows"), list):
        tc["rows"] = [r for r in tc["rows"] if not (isinstance(r, dict) and r.get("event") == oid)]
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("visit_mappings"), list):
        soe["visit_mappings"] = [m for m in soe["visit_mappings"] if not (isinstance(m, dict) and m.get("target_oid") == oid)]
    if isinstance(spec.get("events"), list):
        spec["events"] = [e for e in spec["events"] if not (isinstance(e, dict) and e.get("event_oid") == oid)]
    if isinstance(spec.get("scheduling"), list):
        spec["scheduling"] = [e for e in spec["scheduling"] if not (isinstance(e, dict) and e.get("event_oid") == oid)]


def _add_event(spec, oid, label, after, arm):
    rows = spec.setdefault("timepoint_csv", {}).setdefault("rows", [])
    idx = next((i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("event") == after), None)
    row = {"event": oid, "timepoint": label, "visit_number": 0, "arm": arm or "ALL"}
    if idx is None:
        first_special = next((i for i, r in enumerate(rows) if isinstance(r, dict) and
                              _SPECIAL.search(f"{str(r.get('event')).replace('_', ' ')} {r.get('timepoint')}")), len(rows))
        rows.insert(0 if after == "" else first_special, row)
    else:
        rows.insert(idx + 1, row)
    soe = spec.get("schedule_of_events")
    if isinstance(soe, dict) and isinstance(soe.get("visit_mappings"), list):
        vm = soe["visit_mappings"]
        k = next((i for i, m in enumerate(vm) if isinstance(m, dict) and m.get("target_oid") == after), len(vm) - 1 if after else -1)
        vm.insert(k + 1, {"source_oid": None, "source_name": None, "target_oid": oid, "target_name": label,
                          "action": "pending", "notes": "added from the protocol's Schedule of Activities"})


def _renumber(spec):
    """Scheduled events are numbered in order; the unscheduled / common events keep their numbers."""
    n = 0
    for r in _rows(spec):
        if _SPECIAL.search(f"{str(r.get('event')).replace('_', ' ')} {r.get('timepoint')}"):
            continue
        n += 1
        if isinstance(r.get("visit_number"), (int, float)) or "visit_number" in r:
            r["visit_number"] = n


def state(spec):
    sm = (spec or {}).get("study_meta") if isinstance(spec, dict) else None
    return (sm or {}).get("schedule_from_soa") or {} if isinstance(sm, dict) else {}


def fingerprint(spec, protocol_text):
    import protocol_forms as pf
    h = hashlib.sha256(pf._squash(protocol_text).encode()).hexdigest()
    return hashlib.sha256(json.dumps([VERSION, h, [e["oid"] for e in events_of(spec)]]).encode()).hexdigest()


def needs_check(spec, protocol_text):
    if not enabled() or not isinstance(spec, dict) or not str(protocol_text or "").strip() or not _rows(spec):
        return False
    return state(spec).get("fingerprint") != fingerprint(spec, protocol_text)


def read(pdf_bytes, pages=None):
    """{"tables", "timepoint_tables", "units", "merges", "order", "schedule_text"} read from the protocol PDF (or
    from its layout-text pages when given)."""
    pages = pages if pages is not None else (ps.layout_pages(pdf_bytes) if pdf_bytes else [])
    tables = read_tables(pages)
    tps = timepoint_tables(pages) if tables else []
    units, merges = build_units(tables, tps) if tables else ([], [])
    text = ps.schedule_text(pages)
    clean = ps._without_running(pages)
    extra = [p for p in clean if any(t["table"] in re.sub(r"\s+", " ", p) for t in tps) and p not in text]
    return {"tables": tables, "timepoint_tables": tps, "units": units, "merges": merges,
            "order": ordered(units, tables, tps) if units else [],
            "schedule_text": text + ("\n" + "\n".join(extra) if extra else "")}


def apply(spec, structure, answer_text, protocol_text, fresh=False, aliases=None):
    """Check the events of the spec against the units and fix them (fresh analysis) or record what would be fixed.
    answer_text: the AI answer to the same-visit question, or None. Mutates spec; returns the state."""
    import standards_global as smg
    units = structure["units"]
    by_id = {u["id"]: u for u in units}
    canon = smg.canonicalizer(aliases or [])
    fixes, flags = [], []
    v = validate_answer(units, answer_text, protocol_text) if answer_text else {"merged": {}, "rejected": {}, "ok": False}
    merged = {a: m["into"] for a, m in v["merged"].items()}
    merges = list(structure["merges"])
    for a, m in v["merged"].items():
        merges.append({"label": by_id[a]["label"], "into": by_id[m["into"]]["label"],
                       "reason": "the protocol states they take place at the same visit", "quote": m["quote"]})
    unresolved = [u for u in questions(units) if u["id"] not in merged]
    events = events_of(spec)
    unit_event, dup, unmapped, _profs = match_events(units, events, merged, canon)
    by_oid = {e["oid"]: e for e in events}
    scheduled = [e for e in events if not is_special(e)]
    if scheduled and len(unit_event) * 2 < len(scheduled):
        # most events stand for nothing the schedule reading found: the reading is not a basis for changing events
        st = {"version": VERSION, "status": "not_applied", "applied": False, "units": [], "merges": [], "fixes": [],
              "flags": [], "note": f"only {len(unit_event)} of {len(scheduled)} scheduled events match a column header "
                                   f"or timepoint read from the protocol; the events were left as they are",
              "events": len(events), "fingerprint": fingerprint(spec, protocol_text)}
        spec.setdefault("study_meta", {})["schedule_from_soa"] = st
        _log(st["note"])
        return st
    # an event that names several units: one keeps it, the others are split off
    folded = {}
    for ev in events:
        p = _profs.get(ev["oid"]) or {}
        if p.get("folded"):
            folded[ev["oid"]] = [u["id"] for u in units if u["primary"] and u["primary"] in p["windows"]
                                 and u["id"] not in merged and unit_event.get(u["id"]) != ev["oid"]]
    taken = {e["oid"] for e in events}
    apply_now = bool(fresh)
    verb = (lambda done, would: done) if apply_now else (lambda done, would: would)
    # 1. duplicates
    for oid, uid in dup.items():
        keep = unit_event[uid]
        fixes.append({"fix": "duplicate", "event": oid, "kept": keep, "unit": by_id[uid]["label"],
                      "text": f"{verb('dropped', 'would drop')} event {oid} ({by_oid[oid]['text']}): the protocol's schedule has "
                              f"one visit \"{by_id[uid]['label']}\", which is event {keep}; its forms "
                              f"{verb('were moved', 'would move')} to {keep}"})
        if apply_now:
            _move_forms(spec, oid, keep)
            _drop_event(spec, oid)
    # 2. missing units (and splits)
    row_forms = _row_forms(spec)
    key = lambda s: re.sub(r"[^a-z0-9]+", "", str(s).lower())
    forms = {str(f.get("form_id")): f for f in spec.get("forms") or [] if isinstance(f, dict)}
    missing = [by_id[uid] for uid in ordered(units, structure["tables"], structure["timepoint_tables"], merged)
               if uid not in unit_event and uid not in merged and (by_id[uid]["marked"] or by_id[uid]["listed"])]
    missing = [u for u in missing if u["windows"] or u not in unresolved or answer_text is not None and v["ok"]]
    not_added = [u for u in units if u["id"] not in unit_event and u["id"] not in merged and u not in missing]
    if len(missing) > MAX_ADD:
        flags.append(f"{len(missing)} headers / timepoints of the protocol's schedule have no event; that is more than a "
                     f"fix-up adds ({MAX_ADD}): review the visit schedule")
        not_added += missing
        missing = []
    order = ordered(units, structure["tables"], structure["timepoint_tables"], merged)
    arms = {}
    for uid, oid in unit_event.items():
        tabs = frozenset(s["table"] for s in by_id[uid]["sources"] if s["kind"] == "column")
        arms.setdefault(tabs, []).append(by_oid[oid].get("arm"))
    for u in missing:
        oid = _slug(u["label"], taken)
        taken.add(oid)
        # after the latest event of the visits before it (the analysis may order two visits of one period its own way)
        before = [unit_event[x] for x in order[:order.index(u["id"])] if x in unit_event]
        pos = {r["event"]: i for i, r in enumerate(_rows(spec))}
        after = max(before, key=lambda oid: pos.get(oid, -1)) if before else ""
        tabs = frozenset(s["table"] for s in u["sources"] if s["kind"] == "column")
        pool = [a for a in arms.get(tabs, []) if a]
        arm = max(set(pool), key=pool.count) if pool else "ALL"
        fids, basis = [], []
        for table, row in u["rows"]:
            for fid in row_forms.get(key(row), []):
                if fid in forms and fid not in fids and not _fixed_everywhere(forms[fid]):
                    fids.append(fid)
                    basis.append(f"{fid}: row \"{row}\"")
        if not u["rows"] and u["lists"]:
            sibs = [unit_event[x["id"]] for x in units if x is not u and x["id"] in unit_event and set(x["lists"]) & set(u["lists"])]
            if sibs:
                for fid, f in forms.items():
                    va = set(f.get("visits_assigned") or [])
                    if not _fixed_everywhere(f) and all(s in va for s in sibs):
                        fids.append(fid)
                        basis.append(f"{fid}: on every other visit of {u['lists'][0]}")
        split = next((ev for ev, uids in folded.items() if u["id"] in uids), None)
        src = "; ".join(dict.fromkeys(f"{s['table']}: \"{s['label']}\"" for s in u["sources"]))
        fixes.append({"fix": "split" if split else "added", "event": oid, "unit": u["label"], "after": after, "forms": fids,
                      "from_event": split,
                      "text": (f"{verb('split', 'would split')} event {split} ({by_oid[split]['text']}): it merges distinct "
                               f"timepoints without a protocol reason; " if split else "")
                              + f"{verb('added', 'would add')} event {oid} for \"{u['label']}\" ({src})"
                              + (f"; forms from the schedule: {', '.join(basis)}" if basis else
                                 "; no form: nothing in the schedule places a form there, review")})
        if apply_now:
            _add_event(spec, oid, u["label"], after, arm)
            for fid in fids:
                f = forms[fid]
                f.setdefault("visits_assigned", [])
                if oid not in f["visits_assigned"]:
                    f["visits_assigned"].append(oid)
                soe = spec.get("schedule_of_events")
                if isinstance(soe, dict) and isinstance(soe.get("form_placements"), list):
                    soe["form_placements"].append({"target_visit_oid": oid, "form_id": fid, "required": True,
                                                   "repeating": False, "notes": "Schedule of Activities"})
            unit_event[u["id"]] = oid
    if apply_now and (dup or missing):
        _renumber(spec)
    for u in not_added:
        if u in unresolved and not u["windows"]:
            flags.append(f"\"{u['label']}\" of the protocol's schedule has no time label and no event; whether it is a "
                         f"visit of its own was not established: review")
        elif not (u["marked"] or u["listed"]):
            continue
        else:
            flags.append(f"\"{u['label']}\" of the protocol's schedule has no event: review")
    for oid in unmapped:
        flags.append(f"event {oid} ({by_oid[oid]['text']}) matches no column header or timepoint of the protocol's schedule: review")
    recs = []
    for u in units:
        into = merged.get(u["id"])
        recs.append({"id": u["id"], "label": u["label"], "labels": u["labels"], "window": window_text(u["primary"]),
                     "sources": list(dict.fromkeys(s["table"] for s in u["sources"])),
                     "event": unit_event.get(into or u["id"]) if apply_now or (into or u["id"]) in unit_event else None,
                     "same_visit_as": by_id[into]["label"] if into else None,
                     "status": ("merged" if into else "event" if u["id"] in unit_event else
                                "no activity marked" if not (u["marked"] or u["listed"]) else "no event")})
    st = {"version": VERSION, "status": "done", "applied": apply_now, "units": recs, "merges": merges, "fixes": fixes,
          "flags": flags, "unmapped_events": unmapped, "rejected": v["rejected"],
          "events": len(events_of(spec)), "tables": [t["table"] for t in structure["tables"]],
          "timepoint_tables": [t["table"] for t in structure["timepoint_tables"]]}
    st["fingerprint"] = fingerprint(spec, protocol_text)
    spec.setdefault("study_meta", {})["schedule_from_soa"] = st
    bucket = spec.setdefault("review_flags", {}).setdefault(FLAG, [])
    for line in [f["text"] for f in fixes] + flags:
        if line not in bucket:
            bucket.append(line)
    if not bucket:
        spec["review_flags"].pop(FLAG, None)
    return st


def summary_lines(spec):
    st = state(spec)
    if st.get("status") == "not_applied":
        return [f"Visit schedule against the protocol: not applied ({st.get('note')})."]
    if not st or st.get("status") != "done":
        return []
    units = st.get("units") or []
    lines = [f"Visit schedule against the protocol: {len(st.get('tables') or [])} Schedule of Activities table(s) and "
             f"{len(st.get('timepoint_tables') or [])} timepoint table(s) give {len(units)} visit(s); "
             f"{len(st.get('fixes') or [])} fix(es){'' if st.get('applied') else ' recorded, not applied (reused specification)'}; "
             f"{st.get('events')} event(s)."]
    for f in st.get("fixes") or []:
        lines.append("  * " + f["text"])
    for m in st.get("merges") or []:
        # the log names the merges that are a decision; labels that are the same label written twice are in the state
        if m.get("quote") or "nothing is marked" in m["reason"]:
            lines.append(f"  = \"{m['label']}\" is the visit \"{m['into']}\": {m['reason']}"
                         + (f" (\"{m['quote']}\")" if m.get("quote") else ""))
    for x in st.get("flags") or []:
        lines.append("  ? " + x)
    return lines


def mapping_lines(spec):
    """One line per header / timepoint of the protocol's schedule: the event that stands for it."""
    out = []
    for u in state(spec).get("units") or []:
        what = (f"same visit as \"{u['same_visit_as']}\" -> {u.get('event')}" if u.get("same_visit_as") else
                u.get("event") or u["status"])
        out.append(f"{' / '.join(u['labels'])}  [{'; '.join(u['sources'])}]  ->  {what}")
    return out
