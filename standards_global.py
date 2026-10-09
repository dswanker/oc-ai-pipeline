"""Global best-fit matching of protocol forms to customer standard forms (standards_match.py calls this).

A protocol analysis names its forms its own way, and two forms may share a CDASH domain (two dosing forms, two
laboratory forms). Pairing inside a domain by name similarity then follows whatever word the titles happen to share.
Here every protocol form is scored against every standard form it may be paired with, and ONE assignment is chosen
for all forms together: the one with the highest total score, each standard form used at most once.

Which pairs are allowed is unchanged (shared CDASH domain, same form id / title, or the same content by meaning).
The score decides between them:
  title    shared title words, each weighted by how rare it is in the form titles (a word in many titles of the
           standard says nothing and counts zero; nothing is hard-coded)
  subject  how far each form's questions and title name what the other form's title names
  fields   questions that ask for the same data point
  domain   same CDASH domain (EX / EC are both exposure) as one weighted signal

Names the protocol itself declares to be the same thing ("<name> (<code>)", "also known as", abbreviation lists) are
read from the protocol text by pattern and treated as one subject. Decisions that are close are sent to the validated
AI proposal call; without a validated answer the deterministic winner stands.

STANDARDS_MATCH_GLOBAL=0 restores domain-first matching. STANDARDS_MATCH_MARGIN sets the close-call margin."""
import collections
import os
import re

import standards_match as sm

W_TITLE, W_SUBJECT, W_FIELDS, W_DOMAIN = 0.35, 0.25, 0.20, 0.20
REPORTED = "reported"
FLAG_DUPLICATE = "duplicate_subject_forms"


def enabled():
    return os.environ.get("STANDARDS_MATCH_GLOBAL", "1") != "0"


def margin():
    try:
        return float(os.environ.get("STANDARDS_MATCH_MARGIN", "0.08"))
    except ValueError:
        return 0.08


def _log(msg):
    print(f"[standards-match] {msg}", flush=True)


# ── Names the protocol declares to be the same thing ─────────────────────────────

_SYM = re.compile(r"[®™©]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'’][A-Za-z0-9]+)*")
# function words end a name when reading backwards from a bracket (grammar, not vocabulary of any topic)
_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "and", "or", "as", "is", "are", "was",
         "were", "be", "been", "that", "which", "this", "these", "those", "from", "than", "then", "after", "before",
         "when", "if", "will", "may", "must", "should", "can", "not", "no", "each", "all", "any", "their", "its", "per",
         "via", "into", "during", "between", "both", "either", "also", "has", "have", "had", "who", "whom", "such"}
_SAME_AS = re.compile(r"\(\s*(?:also\s+|formerly\s+|previously\s+|hereafter\s+|herein\s+)*"
                      r"(?:known|referred\s+to|called|designated|abbreviated)\s+as\s+([^()]{2,240})\)", re.I)
_BRACKET = re.compile(r"\(\s*([A-Za-z0-9][A-Za-z0-9\-]{1,14})\s*\)")
_CODE_FIRST = re.compile(r"(?<![A-Za-z0-9\-])([A-Za-z][A-Za-z0-9]*-?\d[A-Za-z0-9\-]*)\s*\(\s*([a-z][a-z\-]+(?: [a-z][a-z\-]+){0,3})\s*\)")
_ABBREV = re.compile(r"\bAbbreviations?\s*:\s*")
_TOKEN = re.compile(r"q\d{3}name")


def _is_code(tok):
    """A code or abbreviation: one token with a capital letter and a digit or hyphen, or all capitals."""
    t = _SYM.sub("", str(tok or "")).strip()
    if not 2 <= len(t) <= 15 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]*", t):
        return False
    if not any(c.isalpha() for c in t) or not any(c.isupper() for c in t):
        return False
    return any(c.isdigit() for c in t) or "-" in t or (t.isupper() and len(t) <= 8)


def _count(low, phrase):
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", low))


def _name_before(flat, low, pos):
    """The name that ends at pos: up to four preceding words, as far back as they nearly always occur together in
    the text (so the verb in front of a drug name is not taken for part of it)."""
    seg = re.split(r"[.;:,()\[\]]", flat[max(0, pos - 90):pos])[-1]
    run = []
    for w in reversed(seg.split()[-4:]):
        if w.lower() in _STOP or not _WORD.fullmatch(w):
            break
        run.insert(0, w)
    if not run:
        return ""
    base = _count(low, run[-1].lower())
    best = run[-1:]
    for k in range(2, len(run) + 1):
        if _count(low, " ".join(run[-k:]).lower()) < max(2, 0.6 * base):
            break
        best = run[-k:]
    return " ".join(best)


def _spelled_out(flat, pos, code):
    """The words in front of pos whose initials spell the code ("external beam radiation therapy (EBRT)")."""
    letters = [c for c in code.lower() if c.isalpha()]
    if len(letters) < 2 or any(c.isdigit() for c in code):
        return ""
    start0 = max(0, pos - 140)
    seg = flat[start0:pos]
    cut = max(seg.rfind(ch) for ch in ".;:()[]")
    seg = seg[cut + 1:]
    parts = [(m.group(0), m.start()) for m in re.finditer(r"[A-Za-z0-9]+", seg)]
    i, j, first = len(parts) - 1, len(letters) - 1, None
    while i >= 0 and j >= 0:
        w = parts[i][0].lower()
        if w[0] == letters[j]:
            first, j = parts[i][1], j - 1
        elif not (w in _STOP and first is not None):
            break
        i -= 1
    return seg[first:].strip() if j < 0 and first is not None else ""


def _key(name):
    """One key per name; a short code keeps its capitals (SOC and SoC are two abbreviations)."""
    t = re.sub(r"[\s\-]+", " ", _SYM.sub("", str(name or "")).strip())
    return t if " " not in t and len(t) <= 4 and any(c.isupper() for c in t) else t.lower()


def _valid_name(name):
    k = _key(name)
    return 2 <= len(k) <= 60 and any(c.isalpha() for c in k) and k.lower() not in _STOP and len(k.split()) <= 6


def aliases(protocol_text):
    """Groups of names the protocol states to be the same thing: [[name, name, ...]]. Read by pattern only:
    "<name> (<code>)", "<code> (<name>)", "<name> (also known as <a>, <b> and <c>)" and abbreviation lists
    ("Abbreviations: <code>, <name>; ..."). Never raises."""
    try:
        text = _SYM.sub("", str(protocol_text or ""))
        flat = re.sub(r"\s+", " ", text)
        low = flat.lower()
        pairs = []
        for m in _SAME_AS.finditer(flat):
            name = _name_before(flat, low, m.start())
            for other in re.split(r",|;|/|\band\b|\bor\b", m.group(1)):
                if name and _valid_name(other) and len(other.split()) <= 3:
                    pairs.append((name, other.strip()))
        for m in _BRACKET.finditer(flat):
            code = m.group(1)
            if _is_code(code):
                name = _spelled_out(flat, m.start(), code) or (
                    _name_before(flat, low, m.start()) if any(c.isdigit() for c in code) else "")
            elif re.fullmatch(r"[A-Za-z]{2,8}", code) and any(c.isupper() for c in code):
                name = _spelled_out(flat, m.start(), code)   # mixed capitals: only when the words in front spell it
            else:
                continue
            if name:
                pairs.append((name, code))
        for m in _CODE_FIRST.finditer(flat):
            if _is_code(m.group(1)) and not any(w in _STOP for w in m.group(2).split()):
                pairs.append((m.group(2), m.group(1)))
        for m in _ABBREV.finditer(text):
            block = re.split(r"\n\s*\n", text[m.end():m.end() + 2500])[0]
            entries = re.sub(r"\s+", " ", block).split(";")
            if not block.rstrip().endswith("."):
                entries = entries[:-1]   # the last entry may run into the text that follows
            for e in entries:
                mm = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9\-/ ]{0,40}?)\s*[,=]\s*([^,=]{2,80}?)\.?\s*", e)
                if not mm:
                    continue
                codes = [c.strip() for c in mm.group(1).split("/")]
                if all(_is_code(c) or re.fullmatch(r"[A-Z][A-Za-z]{1,7}", c) for c in codes):
                    pairs.extend((mm.group(2).strip(), c) for c in codes)
        parent, shown = {}, {}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in pairs:
            if not _valid_name(a) or not _valid_name(b) or _key(a) == _key(b):
                continue
            for n in (a, b):
                parent.setdefault(_key(n), _key(n))
                shown.setdefault(_key(n), n.strip())
            parent[find(_key(a))] = find(_key(b))
        groups = collections.OrderedDict()
        for k in parent:
            groups.setdefault(find(k), []).append(shown[k])
        return [g for g in groups.values() if 2 <= len(g) <= 12]
    except Exception as e:
        _log(f"protocol names not read ({type(e).__name__}: {e}); matching without them")
        return []


def _name_re(name):
    toks = [t for t in re.split(r"[\s\-]+", _SYM.sub("", name).strip()) if t]
    body = r"[\s\-]*".join(re.escape(t) for t in toks)
    short_caps = len(toks) == 1 and len(toks[0]) <= 4 and any(c.isupper() for c in toks[0])
    plural = "" if _is_code(name) else "(?:e?s)?"
    return re.compile(r"(?<![A-Za-z0-9])" + body + plural + r"(?![A-Za-z0-9])", 0 if short_caps else re.I)


def canonicalizer(groups):
    """text -> text with every name of a group replaced by that group's one token."""
    pats = []
    for n, g in enumerate(groups or []):
        for name in g:
            try:
                pats.append((len(name), _name_re(name), f" q{n:03d}name "))
            except re.error:
                continue
    pats.sort(key=lambda t: -t[0])

    def canon(text):
        t = _SYM.sub("", str(text or ""))
        if not t or not pats:
            return t
        for _l, rx, rep in pats:
            t = rx.sub(rep, t)
        return t
    return canon


def _form_texts(form, standard):
    yield str(form.get("title") if standard else form.get("form_title") or "")
    for r in form.get("survey") or []:
        if isinstance(r, dict) and r.get("label"):
            yield str(r["label"])


def used_groups(groups, pforms, sforms):
    """The groups with a name that occurs in a form title or question of either side: all that can matter here."""
    if not groups:
        return []
    blob = "\n".join(t for f in pforms for t in _form_texts(f, False)) + "\n" + \
           "\n".join(t for f in sforms for t in _form_texts(f, True))
    out = []
    for g in groups:
        try:
            if any(_name_re(n).search(blob) for n in g):
                out.append(list(g))
        except re.error:
            continue
    return out[:200]


# ── Scoring ──────────────────────────────────────────────────────────────────────

def _profile(fid, title, domains, survey, choices, canon):
    rows = [{**r, "label": canon(r.get("label"))} for r in sm._m_rows(survey)]
    ch = [{"label": canon(c.get("label"))} for c in choices or [] if isinstance(c, dict)]
    prof = sm._m_profile(fid, canon(title), domains, rows, ch)
    prof["raw_title"] = str(title or "")
    return prof


def title_weights(p_profiles, s_profiles):
    """token -> weight. A word in more than a quarter of the standard's (or the protocol forms') titles is generic
    and weighs nothing; otherwise a word weighs less the more titles carry it. Computed from the forms themselves."""
    df_s, df_p = collections.Counter(), collections.Counter()
    for prof in s_profiles:
        df_s.update(set(prof["title_tokens"]))
    for prof in p_profiles:
        df_p.update(set(prof["title_tokens"]))
    n_s, n_p = len(s_profiles), len(p_profiles)

    def w(t):
        a, b = df_s.get(t, 0), df_p.get(t, 0)
        if (n_s >= 8 and a / n_s > 0.25) or (n_p >= 8 and b / n_p > 0.25):
            return 0.0
        return 1.0 / max(1, a, b)
    return w


def weighted_title(p, s, w):
    ta, tb = p["title_tokens"], s["title_tokens"]
    if not ta or not tb:
        return 0.0
    ma = {i for i, x in enumerate(ta) if any(sm._m_same(x, y) for y in tb)}
    mb = {j for j, y in enumerate(tb) if any(sm._m_same(x, y) for x in ta)}
    for src, dst, ms, md, ts, td in ((p["title"], s["title"], ma, mb, ta, tb), (s["title"], p["title"], mb, ma, tb, ta)):
        word, run = sm._acronym_of(src, dst)
        if word:
            ms |= {i for i, x in enumerate(ts) if x == sm._M_SYN.get(word, word)}
            md |= {j for j, y in enumerate(td) if any(y == sm._M_SYN.get(r, r) or sm._m_same(y, r) for r in run)}
    total = sum(w(x) for x in ta) + sum(w(y) for y in tb)
    if total <= 0:
        return 0.0
    return round((sum(w(ta[i]) for i in ma) + sum(w(tb[j]) for j in mb)) / total, 3)


def _names(a, b, w):
    """Weighted share of what a's title names that b names too, in its title or its questions."""
    total = sum(w(t) for t in a["title_tokens"])
    if total <= 0:
        return 0.0
    pool = set(b["title_tokens"]) | set(b["words"])
    got = sum(w(t) for t in a["title_tokens"] if t in pool or any(sm._m_same(t, x) for x in pool))
    return got / total


def subject(p, s, w):
    return round((_names(p, s, w) + _names(s, p, w)) / 2, 3)


def _candidate(p, s, P, S, pdom, w):
    """The pair's eligibility (why it may be paired at all, as before) and its score, or None."""
    ev = sm.meaning_score(P, S)
    same_dom = bool(s.get("domain")) and s["domain"] in pdom
    same_id = sm.norm_id(p.get("form_id")) == sm.norm_id(s["form_oid"]) != ""
    basis = kind = ""
    if same_dom and not sm._log_mismatch(p, s, sm._similarity(p, s)):
        basis, kind = f"CDASH domain {s['domain']}", "domain"
    elif os.environ.get("STANDARDS_MATCH_BY_NAME", "1") != "0" and not same_dom and (
            same_id or (sm._title_score(p.get("form_title"), s["title"]) >= 0.9 and not (pdom and s.get("domain")))):
        basis = (f"{'same form id' if same_id else 'form title'} (no shared CDASH domain: protocol "
                 f"{'/'.join(pdom) or 'none'}, standard {s.get('domain') or 'none'})")
        kind = "name"
    elif sm.meaning_enabled() and not same_dom:
        text, strong = sm._meaning_basis(ev)
        if strong and not sm._log_mismatch(p, s, ev["title"]) and not sm._qualifier_mismatch(p.get("form_title"), s["title"]):
            basis, kind = f"by meaning: {text}", "meaning"
    if not basis:
        return None
    title, subj = weighted_title(P, S, w), subject(P, S, w)
    dom = 1.0 if same_dom or ev["family"] else 0.0 if ev["conflict"] else 0.5
    total = W_TITLE * title + W_SUBJECT * subj + W_FIELDS * ev["fields"] + W_DOMAIN * dom + (0.02 if same_id else 0.0)
    return {"basis": basis, "kind": kind, "score": round(max(total, 0.001), 3), "ev": ev,
            "parts": {"title": title, "subject": subj, "fields": ev["fields"], "domain": dom}}


def assign(scores):
    """{(row, col): score > 0} -> {row: col} with the highest total score, each row and column used at most once
    (optimal assignment, Hungarian method; a row may stay unassigned)."""
    rows, cols = sorted({r for r, _ in scores}), sorted({c for _, c in scores})
    if not rows:
        return {}
    n, m = len(rows), len(cols) + len(rows)
    ri, ci = {r: i for i, r in enumerate(rows)}, {c: j for j, c in enumerate(cols)}
    cost = [[1e-6] * len(cols) + [0.0] * n for _ in rows]
    for (r, c), sc in scores.items():
        cost[ri[r]][ci[c]] = -float(sc)
    inf = float("inf")
    u, v, match, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        match[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = match[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break
        while True:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1
            if j0 == 0:
                break
    out = {}
    for j in range(1, len(cols) + 1):
        if match[j] and (rows[match[j] - 1], cols[j - 1]) in scores:
            out[rows[match[j] - 1]] = cols[j - 1]
    return out


def close_calls(cand, chosen, m=None):
    """{protocol index: set of standard indexes} for the decisions that are close: without the chosen pair, another
    assignment scores within the margin of the best one. That is the case when a form's best and second-best
    standard forms are within the margin and the second is free to take, and when two protocol forms compete for
    one standard form within the margin. The sets hold what the forms concerned are paired with either way."""
    m = margin() if m is None else m
    scores = {k: c["score"] for k, c in cand.items()}
    total = sum(scores[(p, s)] for p, s in chosen.items())
    close = collections.defaultdict(set)
    for p, s0 in chosen.items():
        rest = {k: v for k, v in scores.items() if k != (p, s0)}
        alt = assign(rest)
        if total - sum(rest[(a, b)] for a, b in alt.items()) > m:
            continue
        close[p].add(s0)
        for q in set(chosen) | set(alt):
            if chosen.get(q) != alt.get(q):
                close[q].update(x for x in (chosen.get(q), alt.get(q)) if x is not None)
    return close


def match_forms(pforms, sforms, ai_pairs=None, info=None, aliases=None):
    """[(protocol form, standard form, basis, score, note)] one-to-one, by one global assignment per source tier
    (an original XLSForm before a referenced study before an ODM). info receives contested, ai_rejected, details
    (score breakdown per pair), close (close calls and who decided them) and aliases (the name groups used)."""
    info = info if isinstance(info, dict) else {}
    info.update({"contested": [], "ai_rejected": [], "details": {}, "close": [], "aliases": []})
    groups = used_groups(aliases, pforms, sforms)
    info["aliases"] = groups
    canon = canonicalizer(groups)
    pdoms = {i: sm._protocol_domains(p) for i, p in enumerate(pforms)}
    P = {i: _profile(p.get("form_id"), p.get("form_title"), pdoms[i], p.get("survey"), p.get("choices"), canon)
         for i, p in enumerate(pforms)}
    S = {j: _profile(s["form_oid"], s["title"], [s.get("domain")], s["survey"], s.get("choices"), canon)
         for j, s in enumerate(sforms)}
    w = title_weights(list(P.values()), list(S.values()))
    cand = {}
    for i, p in enumerate(pforms):
        for j, s in enumerate(sforms):
            c = _candidate(p, s, P[i], S[j], pdoms[i], w)
            if c:
                cand[(i, j)] = c
    pid = lambda i: str(pforms[i].get("form_id"))
    sid = lambda j: str(sforms[j]["form_oid"])
    proposed = []
    for a, b, reason in ai_pairs or []:
        i = next((k for k, x in enumerate(pforms) if sm.norm_id(x.get("form_id")) == sm.norm_id(a)), None)
        j = next((k for k, x in enumerate(sforms) if sm.norm_id(x["form_oid"]) == sm.norm_id(b)), None)
        if i is None or j is None:
            info["ai_rejected"].append({"protocol_form": a, "standard_form": b, "reason": "unknown form id"})
        else:
            proposed.append((i, j, reason))
    out, used_p, used_s, decided = [], {}, {}, {}
    tier_of = lambda s: (sm._tier(s["source"]), s.get("ref_order", 0))
    for tier in sorted({tier_of(s) for s in sforms}):
        sub = {(i, j): c for (i, j), c in cand.items()
               if i not in used_p and j not in used_s and tier_of(sforms[j]) == tier}
        chosen = assign({k: c["score"] for k, c in sub.items()})
        close = close_calls(sub, chosen)
        fixed = {}
        for i, j, reason in proposed:
            if j in close.get(i, ()) and (i, j) in sub and i not in fixed and j not in fixed.values():
                fixed[i] = j
                decided[i] = reason or "AI"
        if fixed:
            rest = {k: c["score"] for k, c in sub.items() if k[0] not in fixed and k[1] not in fixed.values()}
            chosen = {**assign(rest), **fixed}
        for i in sorted(close):
            info["close"].append({"form": pid(i), "candidates": sorted(
                ({"standard_form": sid(j), "score": sub[(i, j)]["score"]} for j in close[i] if (i, j) in sub),
                key=lambda d: -d["score"]), "chosen": sid(chosen[i]) if i in chosen else None,
                "decided": "AI" if i in fixed else "deterministic"})
        for i, j in sorted(chosen.items()):
            used_p[i], used_s[j] = j, i
            c = sub[(i, j)]
            dom = sforms[j].get("domain")
            crowd_s = {b for (a, b) in sub if sub[(a, b)]["kind"] == "domain" and sforms[b].get("domain") == dom}
            crowd_p = {a for (a, b) in sub if sub[(a, b)]["kind"] == "domain" and sforms[b].get("domain") == dom}
            note = ""
            if c["kind"] == "domain" and (len(crowd_s) > 1 or len(crowd_p) > 1):
                note = (f"{len(crowd_s)} standard and {len(crowd_p)} protocol form(s) share domain {dom}; chosen by "
                        f"best overall fit {c['score']}")
            others = sorted(((sub[(i, b)]["score"], sid(b)) for (a, b) in sub if a == i and b != j), reverse=True)
            if i in close:
                entry = next((x for x in reversed(info["close"]) if x["form"] == pid(i)), {})
                alt = ", ".join(f"{d['standard_form']} ({d['score']})" for d in entry.get("candidates") or []
                                if d["standard_form"] != sid(j))
                tail = (f"close call decided by the AI proposal ({alt})" if i in fixed
                        else f"AMBIGUOUS, close alternatives: {alt}" if alt else "AMBIGUOUS, another form scores close")
                note = f"{note}; {tail}" if note else tail
            basis = _readable(c["basis"], groups)
            if i in fixed:
                basis = f"{basis}; close call, AI-proposed and validated"
            info["details"][(pid(i), sid(j))] = {**c["parts"], "total": c["score"],
                                                 "decided": "AI" if i in fixed else "deterministic",
                                                 "alternatives": [{"standard_form": n, "score": sc} for sc, n in others[:3]]}
            out.append((pforms[i], sforms[j], basis, c["score"], note))
    # a protocol form that fits a standard form by meaning but lost it to a better match
    for (i, j), c in sorted(cand.items(), key=lambda kv: -kv[1]["score"]):
        if i not in used_p and j in used_s and c["kind"] == "meaning" and cand[(used_s[j], j)]["kind"] == "meaning":
            info["contested"].append({"form": pid(i), "standard_form": sid(j), "score": c["score"],
                                      "won_by": pid(used_s[j])})
    # pairs the AI proposed for forms still unpaired: each one validated deterministically
    for i, j, reason in proposed:
        rej = {"protocol_form": pid(i), "standard_form": sid(j)}
        if used_p.get(i) == j and i in decided:
            continue
        if i in used_p or j in used_s:
            if used_p.get(i) != j:
                info["ai_rejected"].append({**rej, "reason": "form already paired"})
            continue
        ev = sm.meaning_score(P[i], S[j])
        text, _strong = sm._meaning_basis(ev)
        if not sm.meaning_enabled() or not sm._meaning_minimum(ev) or sm._log_mismatch(pforms[i], sforms[j], ev["title"]) \
                or sm._qualifier_mismatch(pforms[i].get("form_title"), sforms[j]["title"]):
            info["ai_rejected"].append({**rej, "reason": "no title, field or question overlap"
                                        + (" (different CDASH domains)" if ev["conflict"] else "")})
            continue
        used_p[i], used_s[j] = j, i
        title, subj = weighted_title(P[i], S[j], w), subject(P[i], S[j], w)
        info["details"][(pid(i), sid(j))] = {"title": title, "subject": subj, "fields": ev["fields"],
                                             "domain": 0.0 if ev["conflict"] else 0.5, "total": ev["score"],
                                             "decided": "AI", "alternatives": []}
        out.append((pforms[i], sforms[j], f"by meaning, AI-proposed and validated: {_readable(text, groups)}",
                    ev["score"], reason))
    return out


# ── A protocol-specified field belongs to the subject of its form ────────────────

def _state_groups(spec):
    return sm.state(spec).get("aliases") or []


def _titles(spec, form):
    yield form.get("form_title")
    orig = (spec.get("standards_originals") or {}).get(form.get("form_id"))
    if isinstance(orig, dict):
        yield orig.get("form_title")
    yield (form.get("customer_standard") or {}).get("form_name")


def _form_domains(form):
    return {d for d in re.split(r"[^A-Z0-9]+", str(form.get("cdash_domain") or "").upper()) if d}


def _siblings(a, b):
    da, db = _form_domains(a), _form_domains(b)
    return bool(da & db) or (bool(da & sm._EXPOSURE) and bool(db & sm._EXPOSURE))


def other_subject(spec, form, quote):
    """(other form id, word) when the quote names what a sibling form (same CDASH domain, or both exposure forms)
    is about and nothing this form is about; else None. Two dosing forms: a sentence about one drug does not add a
    field to the other drug's form."""
    try:
        canon = canonicalizer(_state_groups(spec))
        toks = lambda text: set(sm._m_tokens(canon(text)))
        forms = [f for f in spec.get("forms") or [] if isinstance(f, dict) and f.get("form_id")]
        title = {id(f): set().union(*[toks(t) for t in _titles(spec, f) if t]) for f in forms}
        df = collections.Counter(t for f in forms for t in title[id(f)])
        special = lambda t: df[t] == 1 and (len(t) >= 5 or bool(_TOKEN.fullmatch(t))) and t not in sm._M_GENERIC
        q = toks(quote)
        named = lambda t: t in q or any(sm._m_same(t, x) for x in q)
        if any(special(t) and named(t) for t in title[id(form)]):
            return None
        mine = set(title[id(form)])
        for r in form.get("survey") or []:
            if isinstance(r, dict) and r.get("provenance") != sm.ADDED:
                mine |= toks(r.get("label"))
        for g in forms:
            if g is form or not _siblings(form, g):
                continue
            for t in sorted(title[id(g)]):
                if special(t) and named(t) and not any(sm._m_same(t, y) for y in mine):
                    return str(g.get("form_id")), t
        return None
    except Exception as e:
        _log(f"subject check of an added field skipped ({type(e).__name__}: {e})")
        return None


# ── Two forms for one subject ────────────────────────────────────────────────────

def _same_subject(spec, a, b, canon):
    """The word(s) both forms are about when a and b cover the same subject, else "": their titles name the same
    thing once the protocol's own names are read as one, or (same CDASH domain) each form's title is fully named
    by the other form's title and questions."""
    if sm._qualifier_mismatch(a.get("form_title"), b.get("form_title")) or \
            bool(sm._RUNNING_LOG.search(str(a.get("form_title") or ""))) != bool(sm._RUNNING_LOG.search(str(b.get("form_title") or ""))):
        return ""
    da, db = _form_domains(a), _form_domains(b)
    if da and db and not _siblings(a, b):
        return ""
    for ta in filter(None, _titles(spec, a)):
        for tb in filter(None, _titles(spec, b)):
            ca, cb = canon(ta), canon(tb)
            if sm.title_meaning(ca, cb) == 1.0 and sm.title_meaning(cb, ca) == 1.0:
                return " ".join(sm._m_tokens(ca))
    if da and db:
        pa = _profile(a.get("form_id"), a.get("form_title"), list(da), a.get("survey"), a.get("choices"), canon)
        pb_ = _profile(b.get("form_id"), b.get("form_title"), list(db), b.get("survey"), b.get("choices"), canon)
        one = lambda t: 1.0
        if pa["title_tokens"] and pb_["title_tokens"] and _names(pa, pb_, one) == 1.0 and _names(pb_, pa, one) == 1.0 \
                and any(_TOKEN.fullmatch(t) or len(t) >= 5 for t in pa["title_tokens"]):
            return " ".join(pa["title_tokens"])
    return ""


def _readable(word, groups):
    def rep(m):
        n = int(m.group(0)[1:4])
        return " / ".join(groups[n][:3]) if n < len(groups) else m.group(0)
    return _TOKEN.sub(rep, word)


def ensure_aliases(spec, protocol_text):
    """A specification matched before the protocol's names were read has none stored: read them now, so the
    duplicate-subject guard sees two forms that carry two names of one thing. Mutates the matching state only."""
    st = sm.state(spec)
    if not st or "aliases" in st or not str(protocol_text or "").strip():
        return
    forms = [{"title": f.get("form_title"), "survey": f.get("survey")} for f in spec.get("forms") or []
             if isinstance(f, dict)]
    st["aliases"] = used_groups(aliases(protocol_text), [], forms)


def duplicates(spec):
    """[(form a, form b, subject)] for every two forms of the spec that cover the same subject."""
    groups = _state_groups(spec)
    canon = canonicalizer(groups)
    forms = [f for f in spec.get("forms") or [] if isinstance(f, dict) and f.get("form_id")]
    out = []
    for i, a in enumerate(forms):
        for b in forms[i + 1:]:
            subj = _same_subject(spec, a, b, canon)
            if subj:
                out.append((a, b, _readable(subj, groups)))
    return out


def duplicate_guard(spec, fresh=False):
    """After matching and the basis check: two forms for one subject are never silently kept. The form that is not
    a customer standard form (the weaker match), or the one without a protocol basis, is not built on a fresh
    analysis and is listed under Forms not built; on a reused specification, or when another form's logic reads it,
    or when neither form is the weaker one, both stay and the pair is flagged. Mutates spec; returns the records.
    Never raises."""
    try:
        if not enabled() or not isinstance(spec, dict) or not sm.state(spec):
            return []
        import protocol_basis as pb
        basis = {str(r.get("form_id")): r for r in pb.state(spec).get("forms") or [] if isinstance(r, dict)}
        records = []
        for a, b, subj in duplicates(spec):
            ia, ib = str(a["form_id"]), str(b["form_id"])
            if not any(f is a for f in spec["forms"]) or not any(f is b for f in spec["forms"]):
                continue   # one of the two is already gone
            std = lambda f: bool(f.get("customer_standard"))
            fixed = lambda f: bool(f.get("convention_required")) or basis.get(str(f["form_id"]), {}).get("checked") is False
            sup = lambda f: basis.get(str(f["form_id"]), {}).get("supported") is not False
            loser = why = None
            if std(a) != std(b):
                loser = b if std(a) else a
                why = "the other form is the customer's standard form for it"
            elif not std(a) and sup(a) != sup(b):
                loser = b if sup(a) else a
                why = "no protocol text asks for it under this form"
            keep = b if loser is a else a
            rec = {"form_id": str((loser or b)["form_id"]), "form_title": (loser or b).get("form_title") or "",
                   "content_source": pb.source_kind(loser or b), "duplicate_of": str(keep["form_id"]), "subject": subj,
                   "mention": "", "action": REPORTED}
            base = (f"same subject as {keep['form_id']} ({keep.get('form_title')}): both are about \"{subj}\"")
            if loser is None or fixed(loser):
                rec["reason"] = f"{base}; both forms are kept, review which one the study needs"
            else:
                readers = [x for x in pb.referenced_by(spec, loser) if x != str(keep["form_id"])]
                visits = ", ".join(str(v) for v in loser.get("visits_assigned") or [])
                where = f" (it was scheduled at {visits})" if visits else ""
                if readers:
                    rec["reason"] = f"{base}; {why}; kept because {', '.join(readers)} reads its fields"
                elif not fresh:
                    rec.update(action=pb.WOULD_REMOVE, reason=f"{base}; {why}; {pb.NOT_FRESH}")
                else:
                    before = pb.events_with_forms(spec)
                    pb.remove_form(spec, loser)
                    after = pb.events_with_forms(spec)
                    rec.update(action=pb.REMOVED, reason=f"{base}; {why}{where}")
                    rec["events_emptied"] = sorted(e for e, fs in before.items() if fs and not after.get(e))
            records.append(rec)
            msg = (f"{rec['form_id']} ({rec['form_title']}): " + ("not built, " if rec["action"] == pb.REMOVED else "")
                   + rec["reason"])
            bucket = spec.setdefault("review_flags", {}).setdefault(FLAG_DUPLICATE, [])
            if msg not in bucket:
                bucket.append(msg)
            for e in rec.get("events_emptied") or []:
                bucket.append(f"event {e} has no form left after {rec['form_id']} was not built; review")
            _log(msg)
        st = spec["study_meta"]["standards_match"]
        REMOVED = pb.REMOVED
        gone = {r["form_id"] for r in records if r["action"] == REMOVED}
        present = {str(f.get("form_id")) for f in spec.get("forms") or [] if isinstance(f, dict)}
        old = [r for r in st.get("duplicates") or [] if r.get("action") == REMOVED
               and r.get("form_id") not in gone | present]
        st["duplicates"] = old + records
        return records
    except Exception as e:
        _log(f"duplicate-subject guard skipped ({type(e).__name__}: {e})")
        return []


def not_built(spec):
    """Records of the duplicate-subject guard that belong under Forms not built."""
    import protocol_basis as pb
    return [r for r in sm.state(spec).get("duplicates") or [] if r.get("action") in (pb.REMOVED, pb.WOULD_REMOVE)]


def duplicate_lines(spec):
    import protocol_basis as pb
    return [f"  Same subject, two forms: {r['form_id']} ({r['form_title']}): "
            + ("not built, " if r["action"] == pb.REMOVED else "") + r["reason"]
            for r in sm.state(spec).get("duplicates") or []]
