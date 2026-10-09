"""The protocol's own structure, read deterministically: the rows of its Schedule of Activities tables and its
numbered section headings. Together they are the checklist the protocol completeness check (protocol_forms.py) must
account for entry by entry, so an assessment cannot be missed because the model happened not to list it.

  rows      the first-column labels of every schedule table (Schedule of Activities / Assessments / Events ...),
            from the PDF's layout text: a table row is a line whose text starts in the table's first column and
            has cells to its right; a wrapped label is joined. Labels are kept as printed (a footnote number may
            still be attached).
  headings  the numbered headings, from the table of contents when the protocol has one (exact numbers and titles),
            else from the body, where a numbered line counts only when it continues the numbering (next sibling of
            an open section, or its first child).
  checklist every schedule row, plus the section headings of the chapter(s) that describe procedures and
            assessments (a chapter with sections whose title is a generic procedures / assessments / evaluations
            title), plus any section heading elsewhere that shares a word with a schedule row. When no such chapter
            exists, every section heading is on the checklist.

Nothing here decides what an assessment is or splits a heading: that is the model's job, per checklist entry.
Study-agnostic; no topic word lists. Never raises: on any problem the lists are simply shorter or empty.
"""
from __future__ import annotations
import io
import re

MAX_ROWS = 120
MAX_HEADINGS = 160
# the conventional names of the schedule table itself (the table type, not any protocol's content)
_SCHEDULE = re.compile(r"schedule\s+of\s+(?:study\s+)?(?:activities|assessments|events|procedures|evaluations)|"
                       r"time\s+and\s+events|study\s+flow\s*chart", re.I)
# generic chapter titles of the part of a protocol that describes what is done and recorded
_PROCEDURES = re.compile(r"\b(?:procedures?|assessments?|evaluations?)\b", re.I)
_TOC_LINE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+(\S.*?)\s*(?:\.\s?){3,}\s*(\d{1,4})\s*$")
_NUM_LINE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+(\S.{1,140}?)\s*$")
_STOP = {"and", "the", "for", "with", "from", "study", "other", "each", "per", "prior", "post", "pre", "all", "any",
         "not", "available"}


def _log(msg):
    print(f"[protocol-structure] {msg}", flush=True)


def _words(text):
    out = []
    for w in re.findall(r"[a-z]{4,}", str(text or "").lower()):
        if w not in _STOP:
            out.append(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _alike(a, b):
    """Two labels name the same thing when they share a content word (or one word starts the other)."""
    wa, wb = _words(a), _words(b)
    return any(x == y or (min(len(x), len(y)) >= 5 and (x.startswith(y[:5]) and y.startswith(x[:5]))) for x in wa for y in wb)


# ── Headings ─────────────────────────────────────────────────────────────────────

def _key(number):
    return tuple(int(p) for p in number.split("."))


def _continues(open_key, key):
    """key continues the numbering after open_key: the first child, or the next sibling of it or of an ancestor."""
    if not open_key:
        return len(key) == 1
    if key == open_key + (1,):
        return True
    for depth in range(1, len(open_key) + 1):
        if key == open_key[:depth - 1] + (open_key[depth - 1] + 1,):
            return True
    return False


def headings_from_toc(text):
    lines = str(text or "").split("\n")
    out, seen, i = [], set(), 0
    while i < len(lines):
        line = lines[i]
        m = _TOC_LINE.match(line)
        if not m:
            # a title that wraps: the leader dots / page number are on the next line
            n = _NUM_LINE.match(line)
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if n and not re.search(r"(?:\.\s?){3,}", line) and re.fullmatch(r"(?:.{0,80}?(?:\.\s?){3,}\s*)?\d{1,4}", nxt) and out:
                extra = re.sub(r"\s*(?:\.\s?){3,}\s*\d{1,4}$|^\d{1,4}$", "", nxt).strip()
                m = _TOC_LINE.match(f"{n.group(1)} {n.group(2)} {extra} ..... 1")
                i += 1
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({"number": m.group(1), "title": re.sub(r"\s+", " ", m.group(2)).strip(" ."), "level": m.group(1).count(".") + 1})
        i += 1
    return out


def headings_from_body(text):
    out, open_key = [], ()
    for line in str(text or "").split("\n"):
        m = _NUM_LINE.match(line)
        if not m:
            continue
        title = m.group(2).strip()
        if not title[:1].isalpha() or title[-1:] in ".,;:" or len(title.split()) > 14 or re.search(r"(?:\.\s?){3,}", title):
            continue
        key = _key(m.group(1))
        if _continues(open_key, key):
            out.append({"number": m.group(1), "title": re.sub(r"\s+", " ", title), "level": len(key)})
            open_key = key
    return out


def headings(text):
    """[{"number", "title", "level"}] in document order; from the table of contents when it has a real outline."""
    try:
        toc = headings_from_toc(text)
        if len(toc) >= 5 and any(h["level"] > 1 for h in toc):
            return toc
        return headings_from_body(text)
    except Exception as e:
        _log(f"headings not read: {type(e).__name__}")
        return []


# ── Schedule rows ────────────────────────────────────────────────────────────────

def layout_pages(pdf_bytes):
    """The PDF's pages as layout text (columns kept by spacing), or [] when it cannot be read."""
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return [(pg.extract_text(extraction_mode="layout") or "") for pg in reader.pages]
    except Exception as e:
        _log(f"layout text unavailable: {type(e).__name__}")
        return []


def _is_prose(line):
    s = line.strip()
    return len(s) > 100 and not re.search(r"\S\s{3,}\S", s)


def _is_note(line):
    return bool(re.match(r"^\s*(?:\d{1,2}|[a-z])[.)]\s*\S", line)) and len(line.strip()) > 40


def _caption(line):
    s = line.strip()
    return bool(s) and len(s) < 110 and _SCHEDULE.search(s) and not re.search(r"(?:\.\s?){3,}", s) and not s.endswith(".")


def _grid_rows(lines):
    """(rows, reached_end): first-column labels of the grid in these lines. A label line starts at the grid's left
    edge; a row is a run of label lines of which at least one has cells to its right."""
    body = []
    for ln in lines:
        if _is_prose(ln) or _is_note(ln):
            break
        body.append(ln.rstrip())
    reached_end = len(body) == len(lines)
    indents = [len(ln) - len(ln.lstrip()) for ln in body if ln.strip() and re.search(r"\S\s{3,}\S", ln.strip())]
    if not indents:
        return [], reached_end
    left = min(indents)
    rows, cur, cur_cells = [], [], False

    def flush():
        nonlocal cur, cur_cells
        if cur and cur_cells:
            label = re.sub(r"\s+", " ", " ".join(cur)).strip()
            if re.search(r"[A-Za-z]{2}", label):
                rows.append(label)
        cur, cur_cells = [], False

    for ln in body:
        if not ln.strip():
            flush()
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent > left + 2:
            continue   # a column header or a cell line without a label
        parts = re.split(r"\s{3,}", ln.strip(), maxsplit=1)
        label, cells = parts[0], len(parts) > 1
        if cur and cur_cells and (label[:1].isupper() or label[:1].isdigit()):
            flush()   # the row above is complete; a capitalised line starts the next one
        cur.append(label)
        cur_cells = cur_cells or cells
    flush()
    return rows, reached_end


def _running(pages):
    """Lines printed on most pages (running header / footer), compared without their digits."""
    count = {}
    for page in pages:
        for k in {re.sub(r"[\d\s]+", " ", ln).strip().lower() for ln in page.split("\n") if ln.strip()}:
            count[k] = count.get(k, 0) + 1
    return {k for k, n in count.items() if k and len(pages) >= 4 and n * 2 > len(pages)}


def _without_running(pages):
    run = _running(pages)
    return ["\n".join("" if re.sub(r"[\d\s]+", " ", ln).strip().lower() in run else ln for ln in page.split("\n"))
            for page in pages]


def schedule_rows(pages):
    """[{"table", "label"}] from layout-text pages: the rows of every schedule table, continued over page breaks."""
    out, seen = [], set()
    pages = _without_running(pages or [])
    carry = None   # the table that ran to the end of the previous page
    try:
        for page in pages or []:
            lines = page.split("\n")
            caps = [i for i, ln in enumerate(lines) if _caption(ln)]
            segments = []
            if carry and (not caps or caps[0] > 3):
                segments.append((carry, lines[:caps[0]] if caps else lines))
            for n, i in enumerate(caps):
                end = caps[n + 1] if n + 1 < len(caps) else len(lines)
                segments.append((re.sub(r"\s+", " ", lines[i]).strip(), lines[i + 1:end]))
            carry = None
            for table, seg in segments:
                rows, reached_end = _grid_rows(seg)
                for label in rows:
                    k = re.sub(r"[^a-z0-9]+", "", label.lower())
                    if k and k not in seen and len(out) < MAX_ROWS:
                        seen.add(k)
                        out.append({"table": table, "label": label})
                carry = table if (reached_end and rows) else None
    except Exception as e:
        _log(f"schedule rows not read: {type(e).__name__}")
    return out


def schedule_text(pages):
    """The text of the pages that hold a schedule table (rows and footnotes): what a "schedule row" quote must be in."""
    out, carry = [], False
    for page in pages or []:
        has = any(_caption(ln) for ln in page.split("\n"))
        if has or carry:
            out.append(page)
        carry = has
    return "\n".join(out)


# ── The checklist ────────────────────────────────────────────────────────────────

def _chapter_of(h):
    return h["number"].split(".")[0]


def procedure_headings(heads, rows):
    """The headings on the checklist: the sections of the chapter(s) that describe procedures and assessments, and
    any heading named like a schedule row. ([headings], how the chapters were found)."""
    by_chapter = {}
    for h in heads:
        by_chapter.setdefault(_chapter_of(h), []).append(h)
    labels = [r["label"] for r in rows]
    chosen, why = set(), {}
    for ch, hs in by_chapter.items():
        top = next((h for h in hs if h["level"] == 1), None)
        subs = [h for h in hs if h["level"] > 1]
        if top is not None and subs and _PROCEDURES.search(top["title"]):
            chosen.add(ch)
            why[ch] = "chapter title"
    out = []
    if not chosen:
        out = [h for h in heads if h["level"] > 1 or len(by_chapter[_chapter_of(h)]) == 1]
        return out[:MAX_HEADINGS], "no procedures chapter recognised: every section heading is on the checklist"
    for h in heads:
        ch = _chapter_of(h)
        if ch in chosen:
            if h["level"] > 1 or len(by_chapter[ch]) == 1:
                out.append(h)
        elif h["level"] > 1 and any(_alike(h["title"], lab) for lab in labels):
            out.append(h)
    how = "; ".join(f"chapter {ch} ({why[ch]})" for ch in sorted(chosen, key=lambda c: int(c)))
    return out[:MAX_HEADINGS], how


def checklist(protocol_text, pdf_bytes=None):
    """{"entries": [{"id", "type": "row" | "heading", "label", "table" | "number"}], "rows", "headings", "how",
    "schedule_text"}. Empty entries when the protocol has neither a readable schedule table nor numbered headings."""
    pages = layout_pages(pdf_bytes) if pdf_bytes else []
    rows = schedule_rows(pages)
    heads = headings(protocol_text)
    picked, how = procedure_headings(heads, rows) if heads else ([], "no numbered headings found")
    entries = [{"id": f"R{i}", "type": "row", "label": r["label"], "table": r["table"]} for i, r in enumerate(rows, 1)]
    entries += [{"id": f"H{i}", "type": "heading", "label": h["title"], "number": h["number"]} for i, h in enumerate(picked, 1)]
    return {"entries": entries, "rows": rows, "headings": picked, "all_headings": len(heads), "how": how,
            "schedule_text": schedule_text(pages)}


def log_lines(cl):
    rows, heads = cl.get("rows") or [], cl.get("headings") or []
    lines = [f"Protocol structure: {len(rows)} Schedule of Activities row(s), {len(heads)} procedures heading(s) of "
             f"{cl.get('all_headings', 0)} numbered heading(s) ({cl.get('how')})."]
    lines += [f"  row: {r['label']} [{r['table']}]" for r in rows]
    lines += [f"  heading: {h['number']} {h['title']}" for h in heads]
    return lines
