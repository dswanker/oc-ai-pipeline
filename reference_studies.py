"""reference_studies.py: fetch the forms of up to 5 referenced OpenClinica studies as customer standard sources
("Reference OC Studies (up to 5)" monday column, COL["reference_studies"]).

READ-ONLY. Only these calls are made, on the item's own subdomain, with the service account:
  1. POST https://{sub}.build.openclinica.io/user-service/api/oauth/token        (standard token login)
  2. GET  https://{sub}.build.openclinica.io/study-service/api/studies?page=..&size=1000
  3. GET  https://{sub}.design.openclinica.io/api/boards/{boardId}
  4. GET  https://{sub}.build.openclinica.io/form-service/api/encrypted-versions/{key}/artifacts/{file}
  5. GET  https://{sub}.build.openclinica.io/rule-service/api/studies/{study_uuid}/rules   (study configuration)
Nothing is created, published or modified in OpenClinica.

Study configuration (REFERENCE_STUDY_CONFIG=0 disables): the design board already read in step 3 also gives each
event's type / repeating, each form-at-event card's required / hidden / allowAdd / participate / sdv /
itemLevelSdv / sdvItems, and the names of the permission tags (board labels with isConfigPermission); step 5 gives
the scheduler, auto-close and notification rules per event. Recipients of notifications are blanked before anything
is kept, and an email address or phone number is never logged.

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


# ── Study configuration of a referenced study ────────────────────────────────────

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])")


def config_enabled():
    return os.environ.get("REFERENCE_STUDY_CONFIG", "1") != "0"


def scrub(text):
    """Text with email addresses and phone numbers removed (tokens such as $participant are kept)."""
    return _PHONE.sub("[phone removed]", _EMAIL.sub("[email removed]", str(text or "")))


def board_config(board):
    """Configuration read from a design board: {"events": {oid: {...}}, "cards": [...], "permission_tags": [names]}.
    Archived lists and cards are skipped. The link between a form and a permission tag is not in the board JSON."""
    board = board or {}
    events, by_list = {}, {}
    for lst in board.get("lists") or []:
        if not isinstance(lst, dict) or lst.get("archived") or not lst.get("eventOcoid"):
            continue
        oid = str(lst["eventOcoid"])
        by_list[lst.get("_id")] = oid
        events[oid] = {"name": str(lst.get("title") or oid), "type": lst.get("type"), "repeating": bool(lst.get("isRepeating"))}
    cards = []
    for card in board.get("cards") or []:
        if not isinstance(card, dict) or card.get("archived") or not card.get("formOcoid"):
            continue
        items = []
        for it in card.get("sdvItems") or []:
            if isinstance(it, dict) and (it.get("ocoid") or it.get("name")) and str(it.get("sdv") or "").lower() in ("required", "optional"):
                items.append({"ocoid": str(it.get("ocoid") or ""), "name": str(it.get("name") or ""),
                              "sdv": str(it["sdv"]).lower()})
        cards.append({"event_oid": by_list.get(card.get("listId")), "form_oid": str(card["formOcoid"]),
                      "title": str(card.get("title") or ""), "required": card.get("required"), "hidden": card.get("hidden"),
                      "allow_add": card.get("allowAdd"), "participate": card.get("participate"), "sdv": card.get("sdv"),
                      "item_level_sdv": card.get("itemLevelSdv"), "sdv_items": items})
    tags = sorted({str(l.get("name")) for l in board.get("labels") or []
                   if isinstance(l, dict) and l.get("isConfigPermission") and l.get("name")})
    return {"events": events, "cards": cards, "permission_tags": tags}


def rules_config(rules):
    """Per event OID, from the study's rule-service rules: {"scheduler", "auto_close", "notifications"}.
    Notification recipients are blanked and addresses / numbers in the texts are removed."""
    out = {}

    def ev(oid):
        return out.setdefault(str(oid), {"scheduler": None, "auto_close": None, "notifications": []})

    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        crit = rule.get("criteria") if isinstance(rule.get("criteria"), dict) else {}
        for act in rule.get("actions") or []:
            if not isinstance(act, dict):
                continue
            kind = act.get("type")
            if kind == "EVENT_ACTION" and act.get("closeEvent") and act.get("targetEventOid"):
                if isinstance(crit.get("offset"), (int, float)):
                    ev(act["targetEventOid"])["auto_close"] = {"after_days": crit["offset"], "when": crit.get("when") or "after",
                                                               "rule": str(rule.get("name") or "")}
            elif kind == "EVENT_ACTION" and act.get("targetEventOid") and (
                    act.get("targetEventStatus") == "SCHEDULED" or act.get("startDateExpression") or act.get("relativeEventOid")):
                rel, off = act.get("relativeEventOid"), act.get("startDateRelativeDays")
                if not rel and rule.get("type") == "RUN_ON_SCHEDULE" and crit.get("eventOid") and crit["eventOid"] != act["targetEventOid"]:
                    rel, off = crit["eventOid"], crit.get("offset")
                trig = rule.get("triggerType")
                ev(act["targetEventOid"])["scheduler"] = {
                    "trigger": ", ".join(trig) if isinstance(trig, list) else str(trig or rule.get("type") or ""),
                    "relative_event": rel or None, "offset_days": off if isinstance(off, (int, float)) else None,
                    "rule": str(rule.get("name") or "")}
            elif kind == "NOTIFICATION_ACTION" and crit.get("eventOid"):
                clean = {"name": scrub(rule.get("name")), "condition": rule.get("condition") or "$TRUE",
                         "type": rule.get("type"), "schedule": rule.get("schedule"), "time": rule.get("time"),
                         "criteria": {k: crit.get(k) for k in ("type", "eventOid", "eventStatuses", "offset", "when", "range")},
                         "actions": [{"type": "NOTIFICATION_ACTION", "ruleResultToTriggerOn": act.get("ruleResultToTriggerOn", True),
                                      "toEmailAddress": "", "emailSubject": scrub(act.get("emailSubject")),
                                      "emailMessage": scrub(act.get("emailMessage")), "textMessage": scrub(act.get("textMessage")),
                                      "toPhoneNumber": ""}]}
                ev(crit["eventOid"])["notifications"].append(
                    {"rule": clean, "had_email_recipient": bool(str(act.get("toEmailAddress") or "").strip()),
                     "had_phone_recipient": bool(str(act.get("toPhoneNumber") or "").strip())})
    return out


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
    if config_enabled():
        # study configuration: from the board already read, plus the study's rules (read-only GET)
        try:
            cfg = board_config(board)
            cfg["identifier"], cfg["rules"], cfg["rules_note"] = ident, {}, ""
            uuid = study.get("uuid") or (board or {}).get("studyUuid")
            if uuid and re.match(r"^[A-Za-z0-9-]+$", str(uuid)):
                try:
                    rules, _r = await _get(client, f"https://{subdomain}.build.openclinica.io/rule-service/api/"
                                                   f"studies/{uuid}/rules", headers)
                    cfg["rules"] = rules_config(rules)
                except Exception as e:
                    cfg["rules_note"] = f"rules could not be read ({_safe(e)})"
            else:
                cfg["rules_note"] = "no study uuid: rules not read"
            out["config"] = cfg
            n_sdv = sum(1 for c in cfg["cards"] if c["sdv_items"])
            rc = cfg["rules"]
            out["log"].append(
                f"Reference OC Studies: {ident}: configuration read: {len(cfg['events'])} event(s), {len(cfg['cards'])} "
                f"form(s) at events ({n_sdv} with SDV items), {len(cfg['permission_tags'])} permission tag(s), "
                f"{sum(1 for v in rc.values() if v['scheduler'])} scheduler / "
                f"{sum(1 for v in rc.values() if v['auto_close'])} auto-close / "
                f"{sum(len(v['notifications']) for v in rc.values())} notification rule(s)"
                + (f"; {cfg['rules_note']}" if cfg["rules_note"] else ""))
        except Exception as e:
            out["log"].append(f"Reference OC Studies: {ident}: configuration could not be read ({_safe(e)})")

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


def _norm_id(x):
    """Comparison key for study identifiers: case, spaces, dashes and underscores ignored."""
    return re.sub(r"[\s_\-]+", "", str(x or "")).lower()


def is_own_study(ref, study, own_ids):
    """True when `ref` (as typed) or the resolved `study` is the item's own study. A study can never be a
    reference study to itself. own_ids: the item's study UUID, study identifier (protocol number) and study OID."""
    own = {_norm_id(i) for i in (own_ids or []) if str(i or "").strip()}
    if not own:
        return False
    cand = {_norm_id(ref)}
    if study:
        cand |= {_norm_id(study.get(k)) for k in ("uuid", "uniqueIdentifier", "name", "oid") if study.get(k)}
    # a study OID like S_PRTK05(TEST) also identifies the protocol
    own |= {_norm_id(re.sub(r"^S_|\(.*\)$", "", str(i))) for i in (own_ids or []) if str(i).startswith("S_")}
    return bool(cand & own)


async def fetch(subdomain, text, username=None, password=None, client=None, own_ids=None):
    """Resolve and fetch the referenced studies. Never raises. Returns {"referenced", "log", "timing"}.
    own_ids: the item's own study UUID / identifier / OID; a reference to the item's own study is always skipped."""
    result = {"referenced": [], "log": [], "timing": {}}
    try:
        refs, log = parse_references(text)
        result["log"] += log
        kept = []
        for ref in refs:
            if is_own_study(ref, None, own_ids):
                result["log"].append(f"Reference OC Studies: '{ref}' is this item's own study; a study can never "
                                     f"reference itself; skipped")
            else:
                kept.append(ref)
        refs = kept
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
                if is_own_study(ref, study, own_ids):
                    result["log"].append(f"Reference OC Studies: '{ref}' resolves to this item's own study; a study "
                                         f"can never reference itself; skipped")
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


async def fetch_study_forms(subdomain, study_ref, token, client=None):
    """The forms of ONE existing study, by its UUID (or identifier / OID), with a bearer token the caller already
    holds. For the pipeline modes that test or extend a customer's existing study ("UAT only", "Logic + UAT"):
    here the study IS the item's own, so the own-study exclusion of fetch() does not apply.

    READ-ONLY: the same GETs as fetch() (studies list, design board, form artifacts, rules); no token login.
    Never raises. Returns {"forms": [(file, bytes)], "study": {"uuid", "identifier", "name"} or None,
    "config": {...} or None, "log": [...], "note": ""}."""
    out = {"forms": [], "study": None, "config": None, "log": [], "note": ""}
    try:
        sub, ref = str(subdomain or "").strip(), str(study_ref or "").strip()
        if not sub or not re.match(r"^[A-Za-z0-9-]+$", sub) or not ref or not token:
            out["note"] = "subdomain, study and token are all needed"
            return out
        import httpx
        own = client is None
        client = client or httpx.AsyncClient()
        try:
            headers = {"Authorization": "Bearer " + str(token)}
            try:
                studies = await _all_studies(client, sub, headers)
            except Exception as e:
                out["note"] = f"the studies of {sub} could not be read ({_safe(e)})"
                out["log"].append(f"Existing study: {out['note']}")
                return out
            study = next((s for s in studies if str(s.get("uuid") or "").lower() == ref.lower()), None)
            why = None
            if study is None:
                study, why = resolve_study(ref, studies)
            if study is None:
                out["note"] = f"study {why or 'not found'} on {sub}"
                out["log"].append(f"Existing study: '{ref}' {why or 'not found'} on {sub}")
                return out
            got = await _fetch_study(client, sub, headers, ref, study, asyncio.Semaphore(CONCURRENCY))
            out["forms"], out["config"], out["note"] = got.get("forms") or [], got.get("config"), got.get("note") or ""
            out["study"] = {"uuid": study.get("uuid"), "identifier": study.get("uniqueIdentifier"),
                            "name": study.get("name")}
            out["log"] += [line.replace("Reference OC Studies:", "Existing study:") for line in got.get("log") or []]
        finally:
            if own:
                await client.aclose()
    except Exception as e:
        out["note"] = f"fetch failed ({_safe(e)})"
        out["log"].append(f"Existing study: fetch failed ({_safe(e)})")
    return out
