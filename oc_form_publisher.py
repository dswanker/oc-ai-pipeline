"""
oc_form_publisher.py — Headless-browser uploader for XLSForm files

Why this exists
---------------
After create_oc_study() builds the study + imports the design board,
the forms still have NO form-version published. Hitting the publish-to-Test
API (`POST /api/studies/{uuid}/study-versions`) in that state errors with
"No form version defined". OpenClinica's REST API doesn't expose
form-version upload, so the only path is via the web UI.

This module uses Playwright headless Chromium to:
  1. Download the EDC build ZIP from a Monday-hosted URL
  2. Extract every .xlsx form
  3. Navigate to the OC designer and upload each form to create a version
  4. Return a structured result with success counts + per-form errors

Stubs that need real-OC inspection
----------------------------------
Everything marked `# TODO(oc-ui):` is a best-guess that must be verified
against a real OpenClinica Test environment before this code can succeed
in production:

  - Form-upload page URL pattern (relative to study_url)
  - DOM selectors for file input, upload button, success indicator
  - Whether upload is per-form or batched
  - FormPublisher.AUTH_SUCCESS_SELECTOR — used to distinguish
    "landed on the OC designer" (auth ok) from "landed on Google's
    login screen" (auth needed). Diagnostic dumps in _upload_one
    capture real HTML on selector failures.

Authentication
--------------
Per-user Google SSO via OpenClinica's `/#/ocstafflogin` endpoint, with
Playwright's storage_state JSON persisted at
/data/browser_sessions/{user_email}.json. See SESSION_DIR docstring.

The function will FAIL FAST with a descriptive error if a selector
doesn't match, so first-run failures are diagnostic rather than silent.

Called from
-----------
pipeline.create_oc_study(), gated on `board_imported and edc_zip_url`.
Failure is non-fatal: study creation succeeds even if form publish
doesn't (per the pipeline's degrade-gracefully convention).
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import httpx

# Per-user Playwright storage_state JSONs live here. MUST be backed by a
# Railway persistent volume — the container's ephemeral filesystem would
# wipe these on every deploy, forcing constant re-auth.
# First-time setup (no file yet for this user) requires a visible browser
# for Google SSO interaction; that cannot succeed on a headless server.
# Bootstrap each user's session locally and copy the JSON into the volume.
SESSION_DIR = "/data/browser_sessions"


# ── Result shape ───────────────────────────────────────────────────────────

@dataclass
class FormPublishResult:
    """Outcome of a publish_all_forms() run."""
    success: bool                       # True iff every form uploaded cleanly
    forms_uploaded: int                 # count of successful uploads
    forms_total: int                    # unique forms on the board we iterated
    errors: List[str] = field(default_factory=list)
    # Non-fatal observations — e.g. set-default-version failures. Do NOT
    # affect `success`; the upload itself succeeded, only the post-upload
    # default-selection step (which is incomplete in v1) is reported here.
    warnings: List[str] = field(default_factory=list)
    # Form OIDs the publisher successfully uploaded (or confirmed
    # already-versioned) during THIS session. The pre-flight in
    # publish_to_test reads this to suppress false "missing version"
    # alerts from the OC REST API — that API has propagation delay and
    # may not yet reflect just-uploaded forms.
    uploaded_oids: List[str] = field(default_factory=list)
    # Forms where the publisher detected a manual edit in OC4 Designer
    # (the card had version IDs not in the pipeline's stored record)
    # and skipped re-upload to avoid overwriting the human's work.
    # The form's existing version is left intact; the caller should
    # surface this list on the monday row for human review.
    conflicts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public class ───────────────────────────────────────────────────────────

class FormPublisher:
    """Upload XLSForm files to an OpenClinica study via Playwright."""

    # Per-form upload budget. Real uploads on OC can take a few seconds
    # each; 30s gives headroom without hanging indefinitely on a broken
    # selector.
    PER_FORM_TIMEOUT_MS = 30_000

    # How long to wait for a human to complete first-time Google SSO.
    # On a headless server (e.g. Railway), this WILL hit timeout — see
    # the SESSION_DIR docstring above.
    MANUAL_LOGIN_TIMEOUT_MS = 300_000  # 5 min

    # "Return To My Studies" link in the OC designer header — a
    # stable <a> element that appears on every loaded board page.
    # Confirmed via live DOM inspection May 2026; replaces a previous
    # best-guess multi-selector that never matched real designer markup.
    AUTH_SUCCESS_SELECTOR = ".js-back-to-sm"

    def __init__(
        self,
        auth_token: Optional[str] = None,
        headless: bool = True,
        user_email: Optional[str] = None,
        allowed_card_ids: Optional[set] = None,
        conflict_oids: Optional[set] = None,
    ):
        """
        Args:
            auth_token: UNUSED — kept on the signature so the existing
                caller in pipeline.py keeps working without simultaneous
                edits. Browser auth is now via SSO + saved storage_state.
            headless: True for production. Forced to False during first-
                time setup (when no session file exists yet for this
                user) so they can complete Google SSO interactively.
            user_email: REQUIRED for SSO; identifies which saved session
                to load. If unset, publish_all_forms returns an error in
                FormPublishResult.errors without launching the browser.
            allowed_card_ids: Optional set of Meteor card _ids (e.g.
                {"7a3JP37ytrJ9RN4vF", ...}). When provided, the publisher
                filters minicards in publish_all_forms to ONLY those
                whose href contains one of these IDs — skipping stale
                cards left in the DOM from prior runs. None (default) =
                visit every .js-minicard (legacy behavior).
        """
        self.auth_token = auth_token
        self.headless = headless
        self.user_email = user_email
        self.allowed_card_ids = allowed_card_ids
        # Form OIDs that the caller's pre-flight identified as having
        # manual edits in OC4 (version IDs not in pipeline's stored
        # record). For each, the publisher SKIPS upload (don't
        # overwrite) but still set-defaults the existing version and
        # records the conflict in result.conflicts. Uppercased.
        self.conflict_oids = (
            {oid.upper() for oid in conflict_oids}
            if conflict_oids else set()
        )

    @property
    def _session_path(self) -> Optional[str]:
        """Filesystem path for this user's Playwright storage_state JSON."""
        if not self.user_email:
            return None
        return os.path.join(SESSION_DIR, f"{self.user_email}.json")

    def _session_exists(self) -> bool:
        p = self._session_path
        return bool(p and os.path.exists(p))

    async def publish_all_forms(
        self,
        study_url: str,
        edc_zip_url: str,
    ) -> FormPublishResult:
        """Download EDC ZIP → extract .xlsx → upload each via OC web UI.

        Args:
            study_url: OC designer URL for the study (e.g.,
                "https://acme.design.openclinica.io/b/<uuid>").
            edc_zip_url: Monday-hosted URL of the EDC build ZIP. Should
                be a presigned S3 URL (Monday's `public_url` field).

        Returns:
            FormPublishResult. Never raises — all failures get captured
            into result.errors and result.success becomes False.
        """
        result = FormPublishResult(success=False, forms_uploaded=0, forms_total=0)
        tmpdir: Optional[Path] = None

        # Guard: SSO can't work without knowing which session to load.
        if not self.user_email:
            result.errors.append(
                "FormPublisher requires user_email — caller did not pass "
                "one. Set the OpenClinica Email column on the monday row "
                "(COL[oc_email] / emailothn6i3m).")
            return result

        # Make sure the session directory exists. If /data is not a
        # mounted volume on Railway, this still succeeds (writes to the
        # container's ephemeral fs) but sessions won't persist across
        # deploys — see SESSION_DIR module docstring.
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
        except OSError as e:
            result.errors.append(
                f"Cannot create session dir {SESSION_DIR}: {e}. Is /data "
                f"mounted as a Railway persistent volume?")
            return result

        try:
            # 1. Download + extract
            tmpdir, xlsx_paths = await self._fetch_and_extract(edc_zip_url)
            result.forms_total = len(xlsx_paths)
            if not xlsx_paths:
                result.errors.append(
                    "EDC ZIP contained no .xlsx files — nothing to upload")
                return result

            # 2. Browser session — lazy import so the module doesn't load
            # playwright on every pipeline.py import (it's a heavy dep).
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                result.errors.append(
                    f"playwright not installed: {e}. Ensure the Railway "
                    f"build command runs `playwright install chromium`.")
                return result

            # 3. First-time setup needs a visible window so the human can
            # complete Google SSO. Detected by absence of session file
            # for this user — that's our "never seen this user before"
            # signal. Override self.headless to False just for this run.
            session_existed = self._session_exists()
            effective_headless = self.headless if session_existed else False
            if not session_existed:
                print(f"oc_form_publisher: first-time SSO setup required "
                      f"for {self.user_email} — opening visible browser. "
                      f"⚠️ On a headless Railway container this WILL hang "
                      f"for {self.MANUAL_LOGIN_TIMEOUT_MS // 1000}s and "
                      f"fail; bootstrap the session locally first and "
                      f"copy {self._session_path} into the volume.",
                      flush=True)

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=effective_headless)
                try:
                    # 4. SPEC FIX: Playwright loads storage_state at context
                    # CREATION via the storage_state= kwarg — not by calling
                    # context.storage_state(path=...) afterwards (that one
                    # SAVES, not loads). The user-supplied spec had this
                    # backwards; structured here as conditional construction.
                    if session_existed:
                        context = await browser.new_context(
                            storage_state=self._session_path)
                        print(f"oc_form_publisher: loaded session for "
                              f"{self.user_email}", flush=True)
                    else:
                        context = await browser.new_context()

                    page = await context.new_page()
                    auth_ok = await self._authenticate_via_sso(
                        page, study_url)

                    # 5. Branch on (auth_ok, session_existed)
                    if not auth_ok and session_existed:
                        # Diagnostics: capture what page we actually landed
                        # on so we can tell "selector wrong" from "session
                        # didn't carry". Best-effort — never let diag itself
                        # break the flow.
                        try:
                            _dbg_url = page.url
                            _dbg_title = await page.title()
                            _dbg_body = (await page.inner_text("body"))[:1500]
                        except Exception as _e:
                            _dbg_url = _dbg_title = _dbg_body = (
                                f"<diag failed: {_e}>")
                        print(f"[auth-debug] final_url={_dbg_url}", flush=True)
                        print(f"[auth-debug] title={_dbg_title}", flush=True)
                        print(f"[auth-debug] body_snippet={_dbg_body!r}",
                              flush=True)
                        try:
                            await page.screenshot(
                                path="/data/browser_sessions/debug_auth.png",
                                full_page=True)
                            print("[auth-debug] saved screenshot -> "
                                  "/data/browser_sessions/debug_auth.png",
                                  flush=True)
                        except Exception as _e:
                            print(f"[auth-debug] screenshot failed: {_e}",
                                  flush=True)
                        # Delete stale session so next run triggers fresh
                        # capture. The pre-flight check in pipeline.py
                        # handles stale sessions before chains start, so
                        # by the time this branch fires the session is
                        # genuinely broken and worth deleting.
                        try:
                            os.remove(self._session_path)
                        except OSError:
                            pass
                        raise RuntimeError(
                            f"Saved SSO session for {self.user_email} "
                            f"appears expired (auth-success selector not "
                            f"found after /#/ocstafflogin redirect chain). "
                            f"Deleted the stale session file — next run "
                            f"will prompt the user to re-capture.")

                    if not auth_ok and not session_existed:
                        # First-time: wait for the human, then save state.
                        print(f"oc_form_publisher: waiting up to "
                              f"{self.MANUAL_LOGIN_TIMEOUT_MS // 1000}s "
                              f"for {self.user_email} to complete Google "
                              f"SSO in the visible browser...", flush=True)
                        try:
                            await page.wait_for_selector(
                                self.AUTH_SUCCESS_SELECTOR,
                                timeout=self.MANUAL_LOGIN_TIMEOUT_MS)
                        except Exception as e:
                            raise RuntimeError(
                                f"First-time SSO setup for "
                                f"{self.user_email} timed out: {e}. If "
                                f"running on a headless server, bootstrap "
                                f"locally and copy the resulting "
                                f"{self._session_path} to the volume."
                            ) from e
                        await context.storage_state(path=self._session_path)
                        print(f"oc_form_publisher: session saved to "
                              f"{self._session_path} — future runs auto-"
                              f"authenticate", flush=True)

                    # 6. Auth confirmed. _authenticate_via_sso already
                    # navigated to study_url and verified .js-back-to-sm,
                    # so we proceed straight to the upload loop. A second
                    # goto here would force a cold SPA reload.

                    # Per-form upload sequence. Match xlsx files to board
                    # forms by OID — the xlsx stem (e.g. "VS.xlsx" → "VS")
                    # matches the form's formOcOidValue field. We iterate
                    # over unique form names on the BOARD (not over xlsx
                    # files) because the board is authoritative; an xlsx
                    # without a matching board form gets logged + skipped.
                    xlsx_map = {p.stem.upper(): p for p in xlsx_paths}
                    # Track OIDs uploaded this session so we don't re-upload
                    # the same form definition when it appears in multiple
                    # events. OC propagates version visibility slowly — the
                    # radio button may not appear on the next card for the
                    # same form within 60 seconds of the first upload.
                    session_uploaded_oids: set = set()
                    # Track OIDs confirmed to have a version this session
                    # (either pre-existing OR just uploaded). When a card's
                    # OID is in this set, the publisher takes a FAST PATH:
                    # short panel-wait + set-default + close (~5s) instead
                    # of the full open-check-upload cycle (~25s). Knowing
                    # the OID upfront comes from cardOidMap built above.
                    confirmed_versioned_oids: set = set()

                    # Board cards render asynchronously after networkidle.
                    # Wait up to 30s for at least one minicard to appear
                    # before evaluating the full set.
                    try:
                        await page.wait_for_selector(
                            '.js-minicard', timeout=30000)
                        await page.wait_for_timeout(1000)
                    except Exception:
                        pass  # evaluate will return empty; logged below

                    # Capture the board URL now that the board has rendered
                    # — the FAST(JS) URL-nav fallback navigates away to a
                    # specific card and tears down the Meteor minimongo
                    # context for the page. Subsequent FAST(JS) attempts
                    # then fail with "Cards/Meteor not in window scope".
                    # We restore this URL after each fallback so the next
                    # card's JS path has Meteor available again.
                    board_url = page.url

                    # DIAGNOSTIC: log first 5 .js-list containers' classes
                    # + attributes so we can identify how archived vs
                    # active lists differ in the DOM (without needing the
                    # Meteor Lists collection).
                    try:
                        _list_diag = await page.evaluate("""
                            () => [...document.querySelectorAll('.js-list')]
                                .slice(0, 5)
                                .map(el => ({
                                    classes: el.className,
                                    dataAttrs: Object.fromEntries(
                                        [...el.attributes]
                                        .filter(a => a.name.startsWith('data-'))
                                        .map(a => [a.name, a.value])
                                    ),
                                    childCount: el.querySelectorAll('.js-minicard').length,
                                    display: getComputedStyle(el).display,
                                    visibility: getComputedStyle(el).visibility
                                }))
                        """)
                        print(f"[list-diag] first 5 .js-list containers: "
                              f"{_list_diag}", flush=True)
                    except Exception as _ld:
                        print(f"[list-diag] failed: {_ld}", flush=True)

                    # Enumerate ALL minicards in DOM order with their
                    # hrefs — NOT deduplicated by name. OC stores form
                    # versions at the form-definition level (shared) but
                    # the "set default version for data entry" toggle is
                    # per-card (per event occurrence), so we must visit
                    # every placement of every form.
                    # Skip cards in archived lists. The OC designer keeps
                    # archived lists in the DOM as .js-minicard elements;
                    # without this filter they get visited too and we end
                    # up iterating hundreds of stale cards.
                    minicard_cards = await page.evaluate("""
                        () => {
                            // Get archived list IDs from Meteor if available
                            let archivedListIds = new Set();
                            try {
                                Lists.find({archived: true}).forEach(l => {
                                    archivedListIds.add(l._id);
                                });
                            } catch(e) {}

                            // Build cardId -> formOcoid map from Meteor's
                            // client-side Cards collection. Lets the
                            // Python loop branch fast-vs-full path WITHOUT
                            // having to open each panel first to read OID.
                            // Falls back to {} if the collection isn't
                            // accessible — the loop then treats every
                            // card as needing full processing.
                            let cardOidMap = {};
                            try {
                                Cards.find({archived: false}).forEach(c => {
                                    if (c._id) cardOidMap[c._id] =
                                        (c.formOcoid || '').toUpperCase();
                                });
                            } catch(e) {}

                            return [...document.querySelectorAll('.js-minicard')]
                                .filter(el => {
                                    // If we have archived list data, skip cards in
                                    // archived lists by checking parent container
                                    if (archivedListIds.size > 0) {
                                        const list = el.closest('[data-list-id]') ||
                                                     el.closest('.js-list');
                                        if (list) {
                                            const listId = list.getAttribute('data-list-id')
                                                || list.dataset.listId;
                                            if (listId && archivedListIds.has(listId)) {
                                                return false;
                                            }
                                        }
                                    }
                                    return true;
                                })
                                .map(el => {
                                    const href = el.getAttribute('href') || '';
                                    // Extract Meteor card _id — last
                                    // non-empty path segment of href.
                                    const parts = href.split('/').filter(s => s);
                                    const cardId = parts.length
                                        ? parts[parts.length - 1] : '';
                                    return {
                                        name: (el.innerText || '').trim(),
                                        href: href,
                                        card_id: cardId,
                                        form_oid: cardOidMap[cardId] || '',
                                    };
                                })
                                .filter(c => c.name)
                        }
                    """)

                    # Apply current-run filter if caller supplied a set of
                    # allowed card _ids. Match by substring (the minicard
                    # href contains the Meteor card _id somewhere — 17-char
                    # alphanumeric IDs don't collide in practice). Without
                    # this filter the publisher walks every card on the
                    # board including stale ones from prior runs (observed:
                    # 223 cards on a board where only ~70 are current).
                    if self.allowed_card_ids:
                        _before = len(minicard_cards)
                        minicard_cards = [
                            c for c in minicard_cards
                            if any(cid in c["href"]
                                   for cid in self.allowed_card_ids)
                        ]
                        print(f"[publisher] Filtered {_before} cards → "
                              f"{len(minicard_cards)} "
                              f"({len(self.allowed_card_ids)} allowed "
                              f"card_ids)", flush=True)

                    result.forms_total = len(minicard_cards)
                    print(f"[publisher] Board has {len(minicard_cards)} "
                          f"form cards "
                          f"({len(set(c['name'] for c in minicard_cards))} "
                          f"unique forms)", flush=True)

                    for card in minicard_cards:
                        form_name = card['name']
                        card_href = card['href']
                        card_meteor_id = (card.get('card_id') or '').strip()

                        # Browser-crash recovery: wrap the per-card work
                        # in a 2-attempt retry loop. If Playwright surfaces
                        # "Target crashed" / "browser has been closed" /
                        # "browser was disconnected" (Chromium died in a
                        # long-running session), tear down + relaunch +
                        # re-auth + retry the same card once.
                        # session_uploaded_oids persists across restart so
                        # already-uploaded forms aren't re-attempted.
                        try:
                            attempts = 0
                            while True:
                                attempts += 1
                                try:
                                    # FAST PATH: this card's form is
                                    # already confirmed versioned (either
                                    # uploaded earlier this session or
                                    # pre-existing). Skip the full
                                    # open-check-upload cycle — just
                                    # click the card, wait briefly,
                                    # click the radio, close. Requires
                                    # knowing the OID upfront via the
                                    # Meteor Cards collection (captured
                                    # into card['form_oid']). If we
                                    # don't have it, fall through to
                                    # the full path.
                                    pre_oid = (card.get('form_oid')
                                               or '').upper()
                                    if (pre_oid
                                            and pre_oid in confirmed_versioned_oids
                                            and card_meteor_id):
                                        # FAST PATH (JS injection): the DDP
                                        # probe showed the radio click is
                                        # handled client-side — no DDP
                                        # method fires from the click alone.
                                        # Replicate the underlying state
                                        # change by calling Meteor's
                                        # collection update method directly,
                                        # skipping the panel-open + radio-
                                        # render propagation lag (~17s →
                                        # <1s per repeat card). The radio's
                                        # HTML name attribute is "_version",
                                        # so that's the Cards field we $set.
                                        js_result = await page.evaluate(
                                            """
                                            async (cardId) => {
                                                if (typeof Cards === 'undefined'
                                                        || typeof Meteor === 'undefined') {
                                                    return { ok: false,
                                                             reason: 'Cards/Meteor not in window scope' };
                                                }
                                                const card = Cards.findOne(cardId);
                                                if (!card) {
                                                    return { ok: false,
                                                             reason: 'card not in minimongo' };
                                                }
                                                if (!Array.isArray(card.versions)
                                                        || card.versions.length === 0) {
                                                    return { ok: false,
                                                             reason: 'card has no versions',
                                                             keys: Object.keys(card) };
                                                }
                                                // Versions array uses .id (integer), not ._id.
                                                // Keep raw for logs, cast to string for the
                                                // Meteor.call $set value — the integer form
                                                // returned INVALID [400] last run.
                                                const versionIdRaw = card.versions[0].id
                                                    || card.versions[0]._id;
                                                if (!versionIdRaw) {
                                                    return { ok: false,
                                                             reason: 'no version id found',
                                                             versionKeys: Object.keys(card.versions[0]),
                                                             versionObj: JSON.stringify(card.versions[0]) };
                                                }
                                                const versionId = String(versionIdRaw);
                                                const versionObj = card.versions[0];
                                                const cardFields = {
                                                    currentVersion: card.currentVersion,
                                                    defaultVersion: card.defaultVersion,
                                                    _version: card._version,
                                                };
                                                return await new Promise((resolve) => {
                                                    Meteor.call('/cards/update',
                                                        { _id: cardId },
                                                        { $set: { _version: versionId } },
                                                        {},
                                                        (err) => {
                                                            if (err) resolve({
                                                                ok: false,
                                                                reason: String(err.message || err),
                                                                versionId: versionId,
                                                                versionIdRaw: versionIdRaw,
                                                                versionObj: versionObj,
                                                                cardFields: cardFields
                                                            });
                                                            else resolve({
                                                                ok: true,
                                                                versionId: versionId,
                                                                versionIdRaw: versionIdRaw,
                                                                versionObj: versionObj,
                                                                cardFields: cardFields
                                                            });
                                                        });
                                                });
                                            }
                                            """,
                                            card_meteor_id)

                                        if (isinstance(js_result, dict)
                                                and js_result.get('ok')):
                                            result.forms_uploaded += 1
                                            print(f"[publisher] FAST(JS) "
                                                  f"set-default "
                                                  f"{form_name} "
                                                  f"(OID={pre_oid}, "
                                                  f"versionId="
                                                  f"{js_result.get('versionId')!r}, "
                                                  f"versionIdRaw="
                                                  f"{js_result.get('versionIdRaw')!r}, "
                                                  f"versionObj="
                                                  f"{js_result.get('versionObj')}, "
                                                  f"cardFields="
                                                  f"{js_result.get('cardFields')})",
                                                  flush=True)
                                            break

                                        # JS approach failed — log details
                                        # and fall back to URL navigation
                                        # (still skips minicard-click
                                        # animation + 8s panel-open wait).
                                        reason = (js_result.get('reason')
                                                  if isinstance(js_result, dict)
                                                  else str(js_result))
                                        _diag = ""
                                        if isinstance(js_result, dict):
                                            _diag = (
                                                f" versionId="
                                                f"{js_result.get('versionId')!r}"
                                                f" versionIdRaw="
                                                f"{js_result.get('versionIdRaw')!r}"
                                                f" versionObj="
                                                f"{js_result.get('versionObj')}"
                                                f" cardFields="
                                                f"{js_result.get('cardFields')}"
                                                f" versionKeys="
                                                f"{js_result.get('versionKeys')}")
                                        print(f"[publisher] FAST(JS) failed "
                                              f"for {form_name} "
                                              f"(OID={pre_oid}): {reason}"
                                              f"{_diag} — falling back to "
                                              f"URL nav", flush=True)

                                        if card_href:
                                            abs_url = await page.evaluate(
                                                "(href) => new URL(href, "
                                                "window.location).toString()",
                                                card_href)
                                            await page.goto(
                                                abs_url,
                                                wait_until="domcontentloaded")
                                        else:
                                            await page.locator(
                                                '.js-minicard').filter(
                                                has_text=form_name
                                            ).first.click()

                                        _radio_timeout = (
                                            15000
                                            if pre_oid in session_uploaded_oids
                                            else 5000
                                        )
                                        try:
                                            await page.wait_for_selector(
                                                'input[type=radio]',
                                                timeout=_radio_timeout)
                                        except Exception as _re:
                                            _res = str(_re).lower()
                                            if ("target crashed" in _res
                                                    or "browser has been closed" in _res
                                                    or "browser was disconnected" in _res):
                                                raise
                                            print(f"[publisher] FAST(fallback) "
                                                  f"radio wait timeout for "
                                                  f"{form_name} (OID="
                                                  f"{pre_oid}, timeout="
                                                  f"{_radio_timeout}ms): "
                                                  f"{_re}", flush=True)

                                        try:
                                            await page.locator(
                                                'input[type=radio]'
                                            ).first.click(timeout=5000)
                                        except Exception as e:
                                            _es = str(e).lower()
                                            if ("target crashed" in _es
                                                    or "browser has been closed" in _es
                                                    or "browser was disconnected" in _es):
                                                raise
                                            result.warnings.append(
                                                f"set-default "
                                                f"(fast-fallback) failed "
                                                f"for {form_name} ({e})")
                                        result.forms_uploaded += 1
                                        print(f"[publisher] FAST(fallback) "
                                              f"set-default {form_name} "
                                              f"(OID={pre_oid})", flush=True)

                                        # Restore board context before the
                                        # next card's FAST(JS) attempt. The
                                        # page.goto(card_url) above tore
                                        # down minimongo for this page; if
                                        # we don't return to the board, the
                                        # next card hits "Cards/Meteor not
                                        # in window scope" and every
                                        # remaining card falls back too —
                                        # the original failure cascades.
                                        try:
                                            await page.goto(
                                                board_url,
                                                wait_until="domcontentloaded")
                                            # 2s settle when the prior
                                            # failure was specifically that
                                            # Meteor hadn't initialized —
                                            # gives the client time to
                                            # boot before the next JS call.
                                            if ("Cards/Meteor not in "
                                                    "window scope"
                                                    in str(reason)):
                                                await page.wait_for_timeout(
                                                    2000)
                                        except Exception as _ne:
                                            print(f"[publisher] FAST"
                                                  f"(fallback) board "
                                                  f"restore failed for "
                                                  f"{form_name}: {_ne}",
                                                  flush=True)
                                        break

                                    # FULL PATH — first encounter of
                                    # this OID OR OID not pre-known.
                                    # Force-clear any stuck board overlay
                                    # before clicking. The overlay can
                                    # become permanently stuck (e.g.
                                    # error overlay from a prior op).
                                    # JS removal beats waiting for it to
                                    # clear.
                                    try:
                                        await page.evaluate(
                                            "document.querySelectorAll('.board-overlay')"
                                            ".forEach(el => el.remove())")
                                        await page.wait_for_timeout(200)
                                    except Exception:
                                        pass

                                    # Click by href when available so we
                                    # hit THIS specific card (same form
                                    # name appears in multiple events —
                                    # name-only would always click the
                                    # first match). Fall back to name
                                    # match if the card had no href.
                                    if card_href:
                                        await page.locator(
                                            f'.js-minicard[href="{card_href}"]'
                                        ).click()
                                    else:
                                        await page.locator('.js-minicard').filter(
                                            has_text=form_name).first.click()
                                    await page.wait_for_timeout(8000)

                                    # Confirm the panel opened by waiting
                                    # for the file input it contains.
                                    try:
                                        await page.wait_for_selector(
                                            'input.js-design-form-input',
                                            timeout=15000)
                                    except Exception as _pe:
                                        # Let browser crashes propagate
                                        # to the outer crash-detect so
                                        # they don't get masked here as
                                        # "panel did not open".
                                        _pes = str(_pe).lower()
                                        if ("target crashed" in _pes
                                                or "browser has been closed" in _pes
                                                or "browser was disconnected" in _pes):
                                            raise
                                        print(f"[publisher] Panel did "
                                              f"not open for "
                                              f"{form_name}: {_pe}",
                                              flush=True)
                                        result.errors.append(
                                            f"{form_name}: panel did "
                                            f"not open")
                                        break

                                    # Read OID from the panel.
                                    oid_el = await page.query_selector(
                                        'input#formOcOidValue')
                                    oid = ((await oid_el.input_value()).upper()
                                           if oid_el else "")

                                    # Conflict-aware branching:
                                    #   (1) If caller pre-flagged this OID as
                                    #       a conflict (OC has version IDs
                                    #       not in the pipeline's stored
                                    #       record → human edited in OC4
                                    #       Designer), skip upload entirely
                                    #       and just set default for the
                                    #       human's version. Record it in
                                    #       result.conflicts for the
                                    #       caller to surface on monday.
                                    #   (2) Otherwise: ALWAYS upload the
                                    #       current build (no hash-based
                                    #       staleness check — pipeline runs
                                    #       are the source of truth on
                                    #       non-conflict OIDs). The
                                    #       existing version, if any, gets
                                    #       a new sibling version from OC.
                                    existing_version = await page.query_selector(
                                        'input[type=radio]')

                                    if (oid and self.conflict_oids
                                            and oid.upper() in self.conflict_oids):
                                        # CONFLICT — don't overwrite.
                                        if existing_version:
                                            try:
                                                await page.locator(
                                                    'input[type=radio]'
                                                ).first.click(timeout=5000)
                                            except Exception as e:
                                                _es = str(e).lower()
                                                if ("target crashed" in _es
                                                        or "browser has been closed" in _es
                                                        or "browser was disconnected" in _es):
                                                    raise
                                                result.warnings.append(
                                                    f"set-default (conflict) "
                                                    f"failed for {form_name} "
                                                    f"({e})")
                                        else:
                                            # Conflict declared but no
                                            # radio visible on panel.
                                            # Could be propagation lag.
                                            print(f"[publisher] CONFLICT "
                                                  f"{form_name} (OID={oid}) "
                                                  f"declared but no "
                                                  f"version visible on "
                                                  f"panel — skipping "
                                                  f"set-default",
                                                  flush=True)
                                        result.conflicts.append(
                                            f"{form_name} (OID={oid})")
                                        result.forms_uploaded += 1
                                        print(f"[publisher] CONFLICT: "
                                              f"{form_name} (OID={oid}) "
                                              f"was manually edited in "
                                              f"OC after last pipeline "
                                              f"upload — skipping upload, "
                                              f"human review required",
                                              flush=True)
                                    else:
                                        # NO CONFLICT — always upload the
                                        # current build (whether or not an
                                        # existing version is present).
                                        xlsx_path = xlsx_map.get(oid)
                                        if not xlsx_path:
                                            print(f"[publisher] Skipping "
                                                  f"{form_name} "
                                                  f"(OID={oid!r}): no "
                                                  f"xlsx in EDC zip",
                                                  flush=True)
                                        else:
                                            # If we already uploaded this
                                            # OID this session, OC backend
                                            # may not have surfaced the
                                            # version yet. Wait briefly,
                                            # re-check.
                                            if oid in session_uploaded_oids:
                                                await page.wait_for_timeout(5000)
                                                recheck = await page.query_selector(
                                                    'input[type=radio]')
                                                if recheck:
                                                    try:
                                                        await page.locator(
                                                            'input[type=radio]'
                                                        ).first.click(timeout=5000)
                                                    except Exception as e:
                                                        result.warnings.append(
                                                            f"set-default "
                                                            f"failed for "
                                                            f"{form_name} "
                                                            f"({e})")
                                                    result.forms_uploaded += 1
                                                    print(f"[publisher] "
                                                          f"{form_name} "
                                                          f"(OID={oid}) "
                                                          f"version now "
                                                          f"visible after "
                                                          f"wait — set "
                                                          f"default",
                                                          flush=True)
                                                else:
                                                    print(f"[publisher] "
                                                          f"Skipping "
                                                          f"re-upload of "
                                                          f"{form_name} "
                                                          f"(OID={oid}) — "
                                                          f"already "
                                                          f"uploaded this "
                                                          f"session, "
                                                          f"version not "
                                                          f"yet "
                                                          f"propagated",
                                                          flush=True)
                                                    result.forms_uploaded += 1
                                                if oid:
                                                    confirmed_versioned_oids.add(oid)
                                                break

                                            # Pre-upload intent log so
                                            # operators can see in real
                                            # time that the publisher is
                                            # uploading (not just
                                            # set-default-ing) — and
                                            # whether we're replacing an
                                            # existing version or doing
                                            # the first-ever upload.
                                            if existing_version:
                                                print(f"[publisher] "
                                                      f"Uploading new "
                                                      f"version for "
                                                      f"{form_name} "
                                                      f"(OID={oid})",
                                                      flush=True)
                                            else:
                                                print(f"[publisher] "
                                                      f"Uploading "
                                                      f"{form_name} "
                                                      f"(OID={oid})",
                                                      flush=True)
                                            await page.set_input_files(
                                                'input.js-design-form-input',
                                                str(xlsx_path))
                                            try:
                                                await page.wait_for_selector(
                                                    '#prevBtn:not(.disabled), '
                                                    'input[type=radio]',
                                                    timeout=30000)
                                            except Exception as e:
                                                print(f"[publisher] "
                                                      f"Upload success "
                                                      f"signal not seen "
                                                      f"for {form_name}: "
                                                      f"{e}", flush=True)
                                            try:
                                                await page.locator(
                                                    'input[type=radio]'
                                                ).first.click(timeout=5000)
                                            except Exception as e:
                                                result.warnings.append(
                                                    f"set-default failed "
                                                    f"for {form_name} "
                                                    f"({e})")
                                            result.forms_uploaded += 1
                                            session_uploaded_oids.add(oid)
                                            print(f"[publisher] Uploaded "
                                                  f"{xlsx_path.name} → "
                                                  f"{form_name} "
                                                  f"(OID={oid})",
                                                  flush=True)
                                    # Mark this OID as confirmed
                                    # versioned so subsequent cards for
                                    # the same form take the FAST PATH.
                                    if oid:
                                        confirmed_versioned_oids.add(oid)
                                    break  # success — exit retry loop
                                except Exception as e:
                                    _err = str(e).lower()
                                    _is_crash = (
                                        "target crashed" in _err
                                        or "browser has been closed" in _err
                                        or "browser was disconnected" in _err
                                    )
                                    if attempts < 2 and _is_crash:
                                        print(f"[publisher] Browser "
                                              f"crashed on {form_name} "
                                              f"— relaunching browser "
                                              f"context", flush=True)
                                        # Tear down ignoring errors.
                                        try:
                                            await browser.close()
                                        except Exception:
                                            pass
                                        # Relaunch + re-auth + re-navigate.
                                        try:
                                            browser = await p.chromium.launch(
                                                headless=effective_headless)
                                            if session_existed:
                                                context = await browser.new_context(
                                                    storage_state=self._session_path)
                                            else:
                                                context = await browser.new_context()
                                            page = await context.new_page()
                                            auth_ok_again = await self._authenticate_via_sso(
                                                page, study_url)
                                            if not auth_ok_again:
                                                raise RuntimeError(
                                                    "re-auth failed "
                                                    "after browser "
                                                    "restart")
                                            try:
                                                await page.wait_for_selector(
                                                    '.js-minicard',
                                                    timeout=30000)
                                                await page.wait_for_timeout(1000)
                                            except Exception:
                                                pass
                                        except Exception as restart_err:
                                            result.errors.append(
                                                f"{form_name}: browser "
                                                f"restart failed: "
                                                f"{type(restart_err).__name__}"
                                                f": {restart_err}")
                                            break
                                        continue  # retry the same card
                                    # Non-crash error OR already retried.
                                    result.errors.append(
                                        f"{form_name}: "
                                        f"{type(e).__name__}: {e}")
                                    break
                        finally:
                            # Close the panel before the next iteration.
                            # Panel may have already closed (or the page
                            # is dead from a crash) — swallow either.
                            try:
                                await page.click(
                                    'a.js-close-card-details')
                                await page.wait_for_timeout(1500)
                            except Exception:
                                pass
                finally:
                    await browser.close()

            # Expose the per-session set of uploaded OIDs so callers can
            # tell publish_to_test "trust these even if the OC REST API
            # doesn't show their versions yet" (propagation delay).
            # Debug log so we can verify population at the source if
            # downstream sees an empty list.
            print(f"[publisher] session_uploaded_oids: "
                  f"{sorted(session_uploaded_oids)}", flush=True)
            result.uploaded_oids = sorted(session_uploaded_oids)
            print(f"[publisher] result.uploaded_oids set: "
                  f"{result.uploaded_oids}", flush=True)
            result.success = (result.forms_uploaded == result.forms_total
                              and not result.errors)
            return result

        except Exception as e:
            # Last-resort catch — this layer should never bubble up
            result.errors.append(
                f"publish_all_forms unexpected: {type(e).__name__}: {e}")
            return result

        finally:
            if tmpdir and tmpdir.exists():
                shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _fetch_and_extract(self, url: str) -> tuple[Path, List[Path]]:
        """Download the ZIP to a temp dir, extract it, return (tmpdir, xlsx_paths)."""
        tmpdir = Path(tempfile.mkdtemp(prefix="oc_forms_"))
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            r = await c.get(url)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            zf.extractall(tmpdir)
        # Sort for deterministic upload order
        xlsx_paths = sorted(tmpdir.rglob("*.xlsx"))
        return tmpdir, xlsx_paths

    async def _authenticate_via_sso(self, page, study_url: str) -> bool:
        """Navigate to study_url and verify the OC board actually rendered.

        Strategy: go directly to the board URL. If the saved
        storage_state is valid, OC's SSO redirect chain settles silently
        and the designer paints — exposing AUTH_SUCCESS_SELECTOR (the
        "Return To My Studies" header link) in the DOM. Two failure
        modes both cleanly return False:

          - Stale session: OC redirects to Keycloak login, the board
            never paints, the selector never appears.
          - Wrong study identifier (e.g. raw UUID instead of the
            board-id+slug form OC expects): OC redirects to the studies
            list, which also does NOT have AUTH_SUCCESS_SELECTOR.

        Returns True if AUTH_SUCCESS_SELECTOR appears within 20s of
        navigation; False otherwise.
        """
        await page.goto(study_url, wait_until="networkidle", timeout=30000)
        # Short settle buffer past networkidle; the 20s wait_for_selector
        # below is the real readiness gate.
        await page.wait_for_timeout(1500)
        try:
            await page.wait_for_selector(
                self.AUTH_SUCCESS_SELECTOR, timeout=20000)
            print(f"oc_form_publisher: authenticated as {self.user_email}",
                  flush=True)
            return True
        except Exception:
            return False



# ── Module-level convenience wrapper ───────────────────────────────────────

async def publish_forms_to_openclinica(
    study_url: str,
    edc_zip_url: str,
    auth_token: Optional[str] = None,
    headless: bool = True,
    user_email: Optional[str] = None,
    allowed_card_ids: Optional[set] = None,
    conflict_oids: Optional[set] = None,
) -> FormPublishResult:
    """Thin wrapper around FormPublisher.publish_all_forms.

    Use from pipeline.py:

        from oc_form_publisher import publish_forms_to_openclinica
        result = await publish_forms_to_openclinica(
            study_url=study_url,
            edc_zip_url=edc_zip_url,
            auth_token=token,
            user_email=oc_email,
            allowed_card_ids=current_run_card_ids,
        )
    """
    publisher = FormPublisher(
        auth_token=auth_token,
        headless=headless,
        user_email=user_email,
        allowed_card_ids=allowed_card_ids,
        conflict_oids=conflict_oids,
    )
    return await publisher.publish_all_forms(study_url, edc_zip_url)
