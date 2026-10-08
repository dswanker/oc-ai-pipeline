"""reference_studies.py: fetch the forms of up to 5 referenced OpenClinica studies as customer standard sources
("Reference OC Studies (up to 5)" monday column, COL["reference_studies"]).

READ-ONLY. Only these calls are made, on the item's own subdomain, with the service account:
  1. POST https://{sub}.build.openclinica.io/user-service/api/oauth/token        (standard token login)
  2. GET  https://{sub}.build.openclinica.io/study-service/api/studies?page=..&size=1000
  3. GET  https://{sub}.design.openclinica.io/api/boards/{boardId}
  4. GET  https://{sub}.build.openclinica.io/form-service/api/encrypted-versions/{key}/artifacts/{file}
Nothing is created, published or modified in OpenClinica.

The artifact URL carries an encrypted key that works without sign-in: it is treated like a password and is never
logged or stored. Log lines name studies, forms and file names only. The whole fetch never fails a build: every
error is turned into a log line and the study (or form) is skipped.

  parse_references(text)  -> (up to 5 references, log lines)
  fetch(subdomain, text)  -> {"referenced": [{"label", "forms": [(file, bytes)], "note"}], "log": [...], "timing": {...}}
The "referenced" list is what standards_match.load_sources takes; listed order is the priority order.
"""
import asyncio, os, re, time

MAX_REFERENCES = 5
PAGE_SIZE = 1000
CONCURRENCY = 6
TIMEOUT = 30.0
_KEY = re.compile(r"/encrypted-versions/([^/]+)/artifacts/")
_BOARD = re.compile(r"/b/([^/?#]+)")
_OID_KEYS = ("oid", "OID", "studyOid", "studyOID", "ocoid")


def enabled():
    return os.environ.get("REFERENCE_STUDIES", "1") != "0"


def parse_references(text):
    """Comma-separated study names / unique identifiers / OIDs. More than 5: the first 5 are used and it is logged."""
    refs = list(dict.fromkeys(r.strip() for r in re.split(r"[,;\n]+", str(text or "")) if r.strip()))
    log = []
    if len(refs) > MAX_REFERENCES:
        log.append(f"Reference OC Studies: {len(refs)} listed, only the first {MAX_REFERENCES} are used "
                   f"(not used: {', '.join(refs[MAX_REFERENCES:])})")
        refs = refs[:MAX_REFERENCES]
    return refs, log


def resolve_study(ref, studies):
    """(study, None) or (None, reason). Matches uniqueIdentifier, then name, then OID: exact (case-insensitive)
    first, then a unique contains-match. Ambiguous or not found is never guessed."""
    want = str(ref or "").strip().lower()
    if not want:
        return None, "empty reference"

    def values(s, field):
        if field == "oid":
            return [str(s.get(k)) for k in _OID_KEYS if s.get(k)]
        return [str(s.get(field))] if s.get(field) else []

    for field in ("uniqueIdentifier", "name", "oid"):
        hits = [s for s in studies if any(v.strip().lower() == want for v in values(s, field))]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f"ambiguous: {len(hits)} studies have this {'OID' if field == 'oid' else field}"
    for field in ("uniqueIdentifier", "name", "oid"):
        hits = [s for s in studies if any(want in v.strip().lower() for v in values(s, field))]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            names = ", ".join(str(s.get("uniqueIdentifier") or s.get("name")) for s in hits[:6])
            return None, f"ambiguous: {len(hits)} studies contain it ({names}{', ...' if len(hits) > 6 else ''})"
    return None, "not found"


def board_id(study):
    """The segment after /b/ in currentBoardUrl ("/b/<id>/<slug>" -> "<id>", not the slug)."""
    m = _BOARD.search(str((study or {}).get("currentBoardUrl") or ""))
    return m.group(1) if m else None


def select_version(card):
    """The version selected on the card (ocoid == selected_form_version_ocoid), else the latest non-archived."""
    versions = [v for v in card.get("versions") or [] if isinstance(v, dict)]
    sel = card.get("selected_form_version_ocoid")
    chosen = next((v for v in versions if sel and v.get("ocoid") == sel), None)
    if chosen is None:
        live = [v for v in versions if not v.get("archived")]
        if live:
            chosen = max(enumerate(live), key=lambda iv: (iv[1].get("id") if isinstance(iv[1].get("id"), (int, float))
                                                          else -1, iv[0]))[1]
    return chosen


def board_forms(board):
    """One entry per distinct formOcoid among the non-archived cards:
    [{"form": ocoid, "title", "file", "key"}] plus the forms that have no uploaded form version."""
    by_form = {}
    for card in (board or {}).get("cards") or []:
        if not isinstance(card, dict) or card.get("archived") or not card.get("formOcoid"):
            continue
        by_form.setdefault(str(card["formOcoid"]), []).append(card)
    forms, missing = [], []
    for ocoid, cards in by_form.items():
        entry = None
        # the same form sits on many events; prefer a card whose selected version exists
        for card in sorted(cards, key=lambda c: not any(v.get("ocoid") == c.get("selected_form_version_ocoid")
                                                        for v in c.get("versions") or [] if isinstance(v, dict))):
            v = select_version(card)
            links = (v or {}).get("uploadedFileLinks") or []
            m = _KEY.search(str((v or {}).get("previewURL") or ""))
            if v and links and m:
                entry = {"form": ocoid, "title": str(card.get("title") or ocoid), "file": str(links[0]),
                         "key": m.group(1), "version": str(v.get("ocoid") or "")}
                break
        if entry:
            forms.append(entry)
        else:
            missing.append(ocoid)
    return forms, missing


def _safe(e):
    """An error for the log without its URL (which may carry the artifact key)."""
    return type(e).__name__ + (f" {e.response.status_code}" if getattr(e, "response", None) is not None else "")


async def _get(client, url, headers, binary=False):
    """GET with one retry. Raises on the second failure."""
    last = None
    for attempt in (1, 2):
        try:
            r = await client.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code >= 500 and attempt == 1:
                continue
            r.raise_for_status()
            return (r.content if binary else r.json()), r
        except Exception as e:
            last = e
            if attempt == 2:
                raise
    raise last


async def _token(client, subdomain, username, password):
    r = await client.post(f"https://{subdomain}.build.openclinica.io/user-service/api/oauth/token",
                          json={"username": username, "password": password}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text.strip()


async def _all_studies(client, subdomain, headers):
    out, page = [], 0
    while True:
        data, r = await _get(client, f"https://{subdomain}.build.openclinica.io/study-service/api/studies"
                                     f"?page={page}&size={PAGE_SIZE}", headers)
        batch = data if isinstance(data, list) else (data or {}).get("content") or []
        out += batch
        total = r.headers.get("X-Total-Count")
        if not batch or len(batch) < PAGE_SIZE or (str(total).isdigit() and len(out) >= int(total)) or page > 50:
            return out
        page += 1


async def _fetch_study(client, subdomain, headers, ref, study, sem):
    ident = str(study.get("uniqueIdentifier") or study.get("name") or ref)
    label = f"referenced study {ident}"
    t0 = time.monotonic()
    out = {"label": label, "forms": [], "note": "", "seconds": 0.0, "log": []}
    bid = board_id(study)
    if not bid:
        out["note"] = "the study has no design board"
        out["log"].append(f"Reference OC Studies: {ident}: no design board found; skipped")
        return out
    try:
        board, _r = await _get(client, f"https://{subdomain}.design.openclinica.io/api/boards/{bid}", headers)
    except Exception as e:
        out["note"] = f"design board could not be read ({_safe(e)})"
        out["log"].append(f"Reference OC Studies: {ident}: design board could not be read ({_safe(e)}); skipped")
        return out
    forms, missing = board_forms(board)
    failed = []

    async def one(f):
        async with sem:
            try:
                data, _r = await _get(client, f"https://{subdomain}.build.openclinica.io/form-service/api/"
                                              f"encrypted-versions/{f['key']}/artifacts/{f['file']}", headers, binary=True)
                return f, data
            except Exception as e:
                failed.append(f"{f['form']} ({_safe(e)})")
                return f, None

    for f, data in await asyncio.gather(*(one(f) for f in forms)):
        if data:
            out["forms"].append((f["file"], data))
    out["seconds"] = round(time.monotonic() - t0, 1)
    out["log"].append(f"Reference OC Studies: {ident}: {len(out['forms'])} form(s) fetched in {out['seconds']}s"
                      + (f"; no uploaded form: {', '.join(sorted(missing))}" if missing else "")
                      + (f"; could not be fetched: {', '.join(sorted(failed))}" if failed else ""))
    notes = ([f"no uploaded form: {', '.join(sorted(missing))}"] if missing else []) + \
            ([f"not fetched: {', '.join(sorted(failed))}"] if failed else [])
    out["note"] = "; ".join(notes)
    return out


async def fetch(subdomain, text, username=None, password=None, client=None):
    """Resolve and fetch the referenced studies. Never raises. Returns {"referenced", "log", "timing"}."""
    result = {"referenced": [], "log": [], "timing": {}}
    try:
        refs, log = parse_references(text)
        result["log"] += log
        if not refs or not enabled():
            return result
        sub = str(subdomain or "").strip()
        username = username or os.environ.get("OC_API_USERNAME")
        password = password or os.environ.get("OC_API_PASSWORD")
        if not sub or not re.match(r"^[A-Za-z0-9-]+$", sub):
            result["log"].append("Reference OC Studies: the item has no usable OpenClinica subdomain; none fetched")
            return result
        if not username or not password:
            result["log"].append("Reference OC Studies: service account not configured; none fetched")
            return result
        import httpx
        own = client is None
        client = client or httpx.AsyncClient()
        t0 = time.monotonic()
        try:
            try:
                headers = {"Authorization": "Bearer " + await _token(client, sub, username, password)}
                studies = await _all_studies(client, sub, headers)
            except Exception as e:
                result["log"].append(f"Reference OC Studies: {sub} could not be read ({_safe(e)}); none fetched")
                return result
            result["timing"]["studies_listed"] = len(studies)
            result["timing"]["list_seconds"] = round(time.monotonic() - t0, 1)
            sem = asyncio.Semaphore(CONCURRENCY)
            seen = set()
            for ref in refs:
                study, why = resolve_study(ref, studies)
                if study is None:
                    result["log"].append(f"Reference OC Studies: '{ref}' {why} on {sub}; skipped")
                    continue
                uid = study.get("uuid") or study.get("uniqueIdentifier")
                if uid in seen:
                    result["log"].append(f"Reference OC Studies: '{ref}' is the same study as an earlier reference; skipped")
                    continue
                seen.add(uid)
                got = await _fetch_study(client, sub, headers, ref, study, sem)
                result["log"] += got.pop("log")
                result["timing"][got["label"]] = got.pop("seconds")
                result["referenced"].append(got)
            result["timing"]["total_seconds"] = round(time.monotonic() - t0, 1)
        finally:
            if own:
                await client.aclose()
    except Exception as e:
        result["log"].append(f"Reference OC Studies: fetch failed ({_safe(e)}); none used")
    return result
