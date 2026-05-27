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
import json
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


# ── SSO redirect detection ────────────────────────────────────────────────
#
# When a Keycloak SSO session expires mid-run, OC silently redirects the
# browser to the auth page. Meteor isn't loaded there, so JS evaluations
# return "Cards/Meteor not in window scope" and URL navs fail with no
# clear signal. Detect by URL substring — async signature for symmetry
# with Playwright callers and so future implementations can do network
# probes if needed.

async def _session_expired(page) -> bool:
    """True when the page has been redirected to the OC/Keycloak login
    flow. Checks page.url against the two known marker substrings.
    Returns False on any unexpected exception so detection failures
    don't compound the original problem."""
    try:
        url = page.url
    except Exception:
        return False
    if not url:
        return False
    return ("auth.openclinica.io" in url
            or "openid-connect/auth" in url)


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
        item_id: Optional[str] = None,
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
            item_id: Optional Monday item id. Required for mid-run SSO
                recovery — _recover_session posts the auth link back to
                this row's oc_auth_link column and appends to its run
                log when a Keycloak session expires mid-publish. When
                None, recovery falls back to silent-retry only and
                hard-fails immediately if that doesn't suffice.
        """
        self.auth_token = auth_token
        self.headless = headless
        self.user_email = user_email
        self.allowed_card_ids = allowed_card_ids
        self.item_id = item_id
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

    # ── Mid-run SSO recovery ─────────────────────────────────────────────

    async def _try_silent_session_recovery(
        self, page, board_url: str,
    ) -> bool:
        """Step 1 of mid-run recovery: navigate back to the board and
        check whether minicards render. Sometimes the Playwright page
        just slipped to the auth page while Keycloak's server-side
        session is still alive; a plain navigate brings it back with
        no user action needed.

        Returns True iff `.js-minicard` appears within 10s AND we
        weren't redirected back to the auth page during navigation."""
        try:
            await page.goto(board_url, wait_until="domcontentloaded")
        except Exception as e:
            print(f"[publisher] silent recovery goto failed: {e}",
                  flush=True)
            return False
        if await _session_expired(page):
            return False
        try:
            await page.wait_for_selector(".js-minicard", timeout=10_000)
            return True
        except Exception:
            return False

    async def _recover_session(
        self, page, context, board_url: str, form_name: str,
    ) -> bool:
        """Step 1-3 recovery flow for a mid-run SSO expiry.

        Step 1 — silent goto: works when Keycloak still has us in a
            server-side session; the Playwright tab just slipped.
        Step 2 — re-auth: post the auth link back to Monday, then poll
            the user's session_state JSON for an mtime change every 5s.
            When the operator completes the auth flow, the
            /api/session/upload handler in main.py writes the fresh
            session file; we detect that and reload cookies into the
            running context.
        Step 3 — hard fail: after 10 minutes with no fresh session,
            return False. Caller is expected to mark remaining cards
            as errors and exit the per-card loop.

        Returns True on recovery, False on hard fail. Never raises.
        """
        print(f"[publisher] SSO session expired at card {form_name} — "
              f"attempting recovery", flush=True)

        # Step 1: silent recovery — cheapest case
        if await self._try_silent_session_recovery(page, board_url):
            print(f"[publisher] silent session recovery succeeded for "
                  f"{form_name}", flush=True)
            return True

        # Step 2 prerequisites — without item_id we can't tell the
        # operator their session needs refreshing, and without a
        # known session-file path we can't watch for the new upload.
        if not self.item_id:
            print(f"[publisher] re-auth needed but no item_id was "
                  f"provided to FormPublisher — hard-failing recovery",
                  flush=True)
            return False
        if not self._session_path:
            print(f"[publisher] re-auth needed but user_email is unset "
                  f"— cannot locate session file; hard-failing",
                  flush=True)
            return False

        # Lazy import — keeps the publisher importable in test
        # contexts where the monday/auth modules aren't wired up.
        try:
            from monday_client import COL, append_log, set_link
            from auth_manager import AuthManager
        except Exception as e:
            print(f"[publisher] re-auth imports failed ({e}); "
                  f"hard-failing recovery", flush=True)
            return False

        auth_link = AuthManager().generate_auth_link(
            self.user_email,
            "https://oc-ai-pipeline-production.up.railway.app",
        )
        try:
            await set_link(
                self.item_id, COL["oc_auth_link"], auth_link,
                text="Re-authenticate OpenClinica",
            )
            await append_log(
                self.item_id,
                f"⚠️ Session expired mid-publish — please re-authenticate "
                f"via the OC Auth Link. Publisher will resume "
                f"automatically once a fresh session is uploaded "
                f"(10 min timeout):\n\n{auth_link}",
            )
        except Exception as e:
            # Posting to Monday failed — operator won't see the link.
            # Still poll the session file in case a fresh one lands
            # via some other path, but mark this as best-effort.
            print(f"[publisher] failed to post re-auth link to Monday: "
                  f"{e} — continuing to poll session file anyway",
                  flush=True)

        # Step 3: poll session file mtime
        DEADLINE_S = 600          # 10 min total wait budget
        POLL_INTERVAL_S = 5
        try:
            initial_mtime = os.path.getmtime(self._session_path)
        except OSError:
            initial_mtime = 0.0   # file doesn't exist yet → any write wins

        elapsed_s = 0
        while elapsed_s < DEADLINE_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            elapsed_s += POLL_INTERVAL_S
            try:
                cur_mtime = os.path.getmtime(self._session_path)
            except OSError:
                continue
            if cur_mtime <= initial_mtime:
                continue

            print(f"[publisher] fresh session detected after {elapsed_s}s "
                  f"— reloading cookies", flush=True)
            try:
                with open(self._session_path) as f:
                    state = json.load(f)
                cookies = state.get("cookies", []) or []
                # Replace the context's cookie jar wholesale —
                # otherwise stale expired cookies might shadow the
                # fresh ones with the same name+domain.
                await context.clear_cookies()
                if cookies:
                    await context.add_cookies(cookies)
                await page.goto(board_url, wait_until="domcontentloaded")
                await page.wait_for_selector(".js-minicard", timeout=15_000)
                if await _session_expired(page):
                    # Fresh session was also stale, somehow. Continue
                    # polling — bump the baseline so we don't re-load
                    # this same file on the next iteration.
                    initial_mtime = cur_mtime
                    continue
                print(f"[publisher] session recovered after {elapsed_s}s — "
                      f"resuming from {form_name}", flush=True)
                return True
            except Exception as e:
                print(f"[publisher] cookie reload failed ({e}) — "
                      f"continuing to poll", flush=True)
                initial_mtime = cur_mtime
                continue

        print(f"[publisher] session recovery TIMED OUT after "
              f"{DEADLINE_S}s — hard-failing remaining cards",
              flush=True)
        return False

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
                    # Track OIDs that have had their per-session FAST(JS)
                    # propagation poll completed. Just-uploaded forms
                    # (OID in session_uploaded_oids) need the Meteor
                    # client to ingest the new version object into
                    # minimongo over DDP — propagation latency varies
                    # by form (some land in <2s, some take 30s+). The
                    # first FAST(JS) attempt polls Cards.findOne every
                    # 2s up to 60s waiting for `versions` to appear;
                    # subsequent cards for the same OID hit minimongo
                    # in steady state and skip the poll. One poll per
                    # OID, not per card.
                    fast_path_warmed: set = set()
                    # Flag set by mid-run SSO recovery when the 10-min
                    # re-auth window elapses with no fresh session.
                    # Checked at the top of the per-card loop so
                    # remaining cards short-circuit to errors instead
                    # of cascading through the same expired session.
                    _session_lost: bool = False

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

                    # Diagnostic: confirms the minicard JS extraction
                    # actually populated form_oid. The early-exit at
                    # the top of the per-card loop gates on
                    # `pre_oid in session_uploaded_oids`; if all
                    # form_oid values come back empty, that check
                    # never fires and the loop falls through to FULL
                    # PATH for every card. Showing the first 5 here
                    # surfaces the issue in the run log before any
                    # uploads happen.
                    oids_in_cards = [c.get('form_oid', '')
                                     for c in minicard_cards]
                    print(f"[publisher] form_oid sample (first 5): "
                          f"{oids_in_cards[:5]}", flush=True)

                    for card in minicard_cards:
                        form_name = card['name']
                        card_href = card['href']
                        card_meteor_id = (card.get('card_id') or '').strip()

                        # Bail-out gate: if mid-run SSO recovery
                        # exhausted its 10-min window, mark every
                        # remaining card as an error and skip
                        # processing. The loop continues only to
                        # populate `result.errors` consistently.
                        if _session_lost:
                            result.errors.append(
                                f"{form_name}: skipped — SSO session "
                                f"expired and recovery timed out"
                            )
                            continue

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

                                    # Early exit: if this OID was
                                    # uploaded earlier in THIS run
                                    # (multiple cards share the same
                                    # form — only the first triggers
                                    # the upload), skip all minicard
                                    # navigation. The post-loop batch
                                    # phase handles set-default for
                                    # every card by card_id. Without
                                    # this short-circuit, each
                                    # duplicate card still clicked the
                                    # minicard + waited 8s for the
                                    # panel, dragging the per-loop
                                    # tail out for minutes after the
                                    # last unique form uploaded.
                                    #
                                    # Doesn't gate on card_meteor_id
                                    # (unlike the FAST PATH check
                                    # below) because the batch phase
                                    # iterates minicard_cards directly
                                    # and looks up each card's id from
                                    # there — even a card we skip
                                    # here gets set-defaulted later.
                                    #
                                    # `break` (not `continue`): we're
                                    # inside `while True: attempts += 1`
                                    # so `continue` would loop back to
                                    # the same condition. `break` exits
                                    # the attempts loop and lets the
                                    # outer `for card` move on.
                                    if pre_oid and pre_oid in session_uploaded_oids:
                                        confirmed_versioned_oids.add(pre_oid)
                                        break

                                    if (pre_oid
                                            and pre_oid in confirmed_versioned_oids
                                            and card_meteor_id):
                                        # FAST PATH retired in favor
                                        # of the post-loop batch
                                        # phase (see "Batch set-
                                        # default phase" below).
                                        # The per-card Cards.update
                                        # + URL-nav fallback that
                                        # used to live here cost
                                        # ~30s per card on the slow
                                        # path and dominated long
                                        # runs (~60 min for 121
                                        # cards). Cards whose form
                                        # is already confirmed-
                                        # versioned just skip the
                                        # per-card work; one batch
                                        # JS call sets every default
                                        # at the end of the run.
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
                                    # Scroll the card into view before
                                    # clicking — boards have several
                                    # columns, late ones (AE/AESAE/CM/DV/
                                    # DS) live in columns scrolled off-
                                    # screen and the click times out
                                    # waiting for the element to become
                                    # actionable. scroll_into_view_if_needed
                                    # is a no-op when the card is already
                                    # visible, so safe to always run.
                                    if card_href:
                                        _mc = page.locator(
                                            f'.js-minicard[href="{card_href}"]')
                                    else:
                                        _mc = page.locator(
                                            '.js-minicard').filter(
                                            has_text=form_name).first
                                    await _mc.scroll_into_view_if_needed(
                                        timeout=5000)
                                    await _mc.click()
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
                                        # Set-default for the existing
                                        # version is handled by the
                                        # post-loop batch phase along
                                        # with every other card.
                                        if not existing_version:
                                            print(f"[publisher] CONFLICT "
                                                  f"{form_name} (OID={oid}) "
                                                  f"declared but no "
                                                  f"version visible on "
                                                  f"panel — batch phase "
                                                  f"will look it up via "
                                                  f"minimongo",
                                                  flush=True)
                                        result.conflicts.append(
                                            f"{form_name} (OID={oid})")
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
                                                # Already uploaded this
                                                # OID earlier in the loop
                                                # (multiple cards share
                                                # the same form). Skip
                                                # re-upload; the batch
                                                # phase sets the default.
                                                print(f"[publisher] "
                                                      f"Skipping re-upload "
                                                      f"of {form_name} "
                                                      f"(OID={oid}) — "
                                                      f"already uploaded "
                                                      f"this session; "
                                                      f"batch phase will "
                                                      f"set-default",
                                                      flush=True)
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
                                            # Dismiss OC's red error
                                            # banner if it shows up
                                            # immediately after the
                                            # file is set. The banner
                                            # text observed in the
                                            # field is "Upload version
                                            # is successful while
                                            # update the form is
                                            # failed" — the upload DID
                                            # land but the banner
                                            # overlays the form-version
                                            # radio and blocks our
                                            # subsequent wait_for_
                                            # selector. Clicking its
                                            # close button lets the
                                            # radio render and the
                                            # wait below fires
                                            # normally. 2s gives OC
                                            # time to show the banner
                                            # (it appears post-upload,
                                            # not instantly); selector
                                            # union covers a few
                                            # variants of OC's alert
                                            # markup. Wrapped in
                                            # try/except: no banner
                                            # = no-op.
                                            await page.wait_for_timeout(2000)
                                            try:
                                                close_btn = await page.query_selector(
                                                    '.alert .close, '
                                                    '.alert button[data-dismiss], '
                                                    '.notification-close, '
                                                    '[class*="alert"] .close'
                                                )
                                                if close_btn:
                                                    await close_btn.click()
                                                    await page.wait_for_timeout(500)
                                            except Exception:
                                                pass
                                            # Wait for upload confirmation
                                            # in two stages with a
                                            # generous primary ceiling.
                                            # 7 forms (SLEEP/SF12/EX/
                                            # AE/AESAE/CM/DV) take
                                            # noticeably longer than 30s
                                            # for OC to process the
                                            # XLSForm and surface the
                                            # radio; the previous 30s
                                            # cap timed those out
                                            # silently and the upload
                                            # never actually created a
                                            # version on the OC side,
                                            # which then broke publish-
                                            # to-test with "No form
                                            # version defined". 90s
                                            # accommodates the slow
                                            # forms.
                                            try:
                                                await page.wait_for_selector(
                                                    'input[type=radio]',
                                                    state='attached',
                                                    timeout=90000)
                                            except Exception as e:
                                                # Stage 2 fallback for
                                                # forms that don't ship
                                                # a radio at all. 10s
                                                # is enough — if
                                                # neither selector
                                                # fires within their
                                                # budgets the upload
                                                # is suspect.
                                                try:
                                                    await page.wait_for_selector(
                                                        '#prevBtn:not(.disabled)',
                                                        timeout=10000)
                                                except Exception:
                                                    print(f"[publisher] "
                                                          f"Upload success "
                                                          f"signal not seen "
                                                          f"for {form_name}: "
                                                          f"{e}",
                                                          flush=True)
                                            # set-default for this card
                                            # happens in the post-loop
                                            # batch phase — no per-card
                                            # radio click here.
                                            # `oid` is read from the
                                            # panel's formOcOidValue
                                            # input which sometimes
                                            # comes back empty on slow-
                                            # rendering forms; fall
                                            # back to `pre_oid` (from
                                            # card['form_oid']) so the
                                            # early-exit short-circuit
                                            # for subsequent duplicate
                                            # cards still fires.
                                            session_uploaded_oids.add(
                                                oid if oid else pre_oid)
                                            print(f"[publisher] Uploaded "
                                                  f"{xlsx_path.name} → "
                                                  f"{form_name} "
                                                  f"(OID={oid})",
                                                  flush=True)
                                    # Mark this OID as confirmed
                                    # versioned so subsequent cards for
                                    # the same form take the FAST PATH.
                                    # Same pre_oid fallback as above.
                                    if oid or pre_oid:
                                        confirmed_versioned_oids.add(
                                            oid if oid else pre_oid)
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
                            # `timeout=1000` caps the click attempt at
                            # 1s instead of Playwright's 30s default;
                            # when the early-exit short-circuit fires
                            # (no panel was opened for this card), the
                            # close button isn't present and waiting
                            # 30s × duplicate-card-count produced a
                            # multi-minute tail after the last unique
                            # form uploaded. Fail fast and continue.
                            try:
                                await page.click(
                                    'a.js-close-card-details',
                                    timeout=1000)
                                await page.wait_for_timeout(1500)
                            except Exception:
                                pass

                    # Boundary log: the upload loop has completed.
                    # Confirms we exit the per-card loop cleanly and
                    # shows the _session_lost flag value before the
                    # batch-phase gate evaluates it. If you see
                    # "Upload loop complete" but not "Batch prep:
                    # navigating to ..." then the hang is in the
                    # if-check or its prelude, not in goto.
                    print("[publisher] Upload loop complete — entering "
                          "batch phase check", flush=True)
                    print(f"[publisher] _session_lost={_session_lost}",
                          flush=True)

                    # ── Batch set-default phase ────────────────────────
                    # The upload loop above did NOT click any radios.
                    # All set-default work happens here in one shot:
                    #   (1) ask minimongo for every card's current
                    #       versions[0].id via a single page.evaluate;
                    #   (2) Cards.update each card's _version in a
                    #       second page.evaluate that returns per-card
                    #       success/failure.
                    # Cards that fail the batch (or have no version
                    # visible in minimongo yet) fall through to a
                    # per-card URL-nav fallback below. On a healthy
                    # run with 121 cards this collapses ~60 min of
                    # per-card navigation into <1s of batched JS.
                    if not _session_lost:
                        # ── Batch prep ────────────────────────────────
                        # The per-card upload loop may have left us on
                        # a card detail page (or, if SSO slipped, the
                        # auth page). Navigate back to the board so
                        # Meteor/Cards is guaranteed in scope before
                        # the batch JS runs — otherwise the bulk
                        # lookup hangs silently inside page.evaluate
                        # waiting on an undefined `Cards`.
                        #
                        # The whole prep block (goto + wait_for_selector
                        # + 5s settle) is wrapped together so any hang
                        # or error surfaces with a clear log line; an
                        # earlier version of this code printed nothing
                        # before the goto and silently hung when
                        # navigation got stuck.

                        # (The 45s minimongo propagation wait that
                        # used to live here was removed once the
                        # root cause was diagnosed as upload
                        # confirmation timing out at 30s instead of
                        # a propagation issue. The upload loop now
                        # waits up to 90s per form, so by the time
                        # we reach this point every successful
                        # upload has its version visible to OC.)
                        print(f"[publisher] Batch prep: navigating to "
                              f"board {study_url!r}", flush=True)
                        try:
                            # SSO can expire during the upload loop's
                            # dead time (~3.5 min × 7 hidden-radio
                            # forms historically — even with the
                            # poll-and-break shortening, long runs
                            # still push the cumulative wall clock
                            # past Keycloak's session window). If the
                            # current URL shows we're already on the
                            # auth page, the upcoming page.goto would
                            # bounce through the callback chain and
                            # never re-mount the board; run recovery
                            # first so the goto lands on a clean
                            # board session.
                            if await _session_expired(page):
                                print("[publisher] SSO expired before "
                                      "batch phase — recovering",
                                      flush=True)
                                await self._recover_session(
                                    page, context, study_url,
                                    "batch-prep",
                                )
                            await page.goto(
                                study_url,
                                wait_until="domcontentloaded",
                            )
                            await page.wait_for_selector(
                                ".js-minicard", timeout=30_000,
                            )
                            # Give slow-propagating forms a few seconds
                            # to land in minimongo after the upload
                            # loop's final wait_for_selector returned.
                            # Empirically the difference between
                            # "version_id visible at this moment" and
                            # "visible 5s later" is the difference
                            # between batch success and batch-then-
                            # fallback for a handful of OIDs.
                            await page.wait_for_timeout(5000)
                        except Exception as _ne:
                            print(f"[publisher] Batch prep FAILED "
                                  f"({type(_ne).__name__}: {_ne}) — "
                                  f"batch phase will likely fall "
                                  f"through to URL nav fallback",
                                  flush=True)
                        print("[publisher] Batch set-default starting...",
                              flush=True)

                        import time as _bt_time
                        _batch_start_t = _bt_time.time()

                        _eligible = [
                            c for c in minicard_cards if c.get('card_id')
                        ]
                        _eligible_ids = [c['card_id'] for c in _eligible]
                        print(f"[publisher] Batch set-default: looking up "
                              f"versions for {len(_eligible_ids)} cards in "
                              f"minimongo", flush=True)

                        # Step 1: bulk version lookup (30s ceiling via
                        # asyncio.wait_for — page.evaluate itself has
                        # no native timeout argument, so without this
                        # wrapper a hung JS execution would block the
                        # whole publish indefinitely).
                        try:
                            _versions_by_card = await asyncio.wait_for(
                                page.evaluate(
                                    """
                                    (cardIds) => {
                                        const out = {};
                                        for (const cid of cardIds) {
                                            const c = Cards.findOne(cid);
                                            if (c && Array.isArray(c.versions)
                                                    && c.versions.length) {
                                                const v = c.versions[0];
                                                const vid = (v && (v.id || v._id)) || null;
                                                if (vid !== null) out[cid] = vid;
                                            }
                                        }
                                        return out;
                                    }
                                    """,
                                    _eligible_ids,
                                ),
                                timeout=30,
                            )
                        except asyncio.TimeoutError:
                            print(f"[publisher] Batch version lookup "
                                  f"timed out after 30s — falling back "
                                  f"to per-card URL nav for everything",
                                  flush=True)
                            _versions_by_card = {}
                        except Exception as _be:
                            print(f"[publisher] Batch version lookup "
                                  f"failed ({_be}) — falling back to "
                                  f"per-card URL nav for everything",
                                  flush=True)
                            _versions_by_card = {}

                        _card_version_map = {
                            cid: vid
                            for cid, vid in _versions_by_card.items()
                            if vid is not None
                        }
                        _no_version_ids = [
                            cid for cid in _eligible_ids
                            if cid not in _card_version_map
                        ]
                        if _no_version_ids:
                            print(f"[publisher] {len(_no_version_ids)} "
                                  f"cards have no version visible in "
                                  f"minimongo — they'll go through "
                                  f"URL-nav fallback", flush=True)

                        # Step 2: batch Cards.update (same 30s ceiling
                        # as Step 1 — single hung Cards.update inside
                        # the JS loop would otherwise block forever).
                        _batch_results: dict = {}
                        if _card_version_map:
                            try:
                                _batch_results = await asyncio.wait_for(
                                    page.evaluate(
                                        """
                                        async (cardVersionMap) => {
                                            const results = {};
                                            for (const [cardId, versionId] of Object.entries(cardVersionMap)) {
                                                try {
                                                    Cards.update(cardId,
                                                        {$set: {_version: versionId}});
                                                    results[cardId] = {ok: true};
                                                } catch(e) {
                                                    results[cardId] = {ok: false,
                                                                        error: e.toString()};
                                                }
                                            }
                                            return results;
                                        }
                                        """,
                                        _card_version_map,
                                    ),
                                    timeout=30,
                                )
                            except asyncio.TimeoutError:
                                print(f"[publisher] Batch Cards.update "
                                      f"timed out after 30s — every "
                                      f"card will retry via URL nav",
                                      flush=True)
                                _batch_results = {
                                    cid: {"ok": False,
                                          "error": "batch update timed out"}
                                    for cid in _card_version_map
                                }
                            except Exception as _ue:
                                print(f"[publisher] Batch Cards.update "
                                      f"failed ({_ue}) — every card "
                                      f"will retry via URL nav",
                                      flush=True)
                                _batch_results = {
                                    cid: {"ok": False, "error": str(_ue)}
                                    for cid in _card_version_map
                                }

                        _batch_ok = [
                            cid for cid, r in _batch_results.items()
                            if r.get("ok")
                        ]
                        _batch_failed = [
                            cid for cid, r in _batch_results.items()
                            if not r.get("ok")
                        ]
                        _elapsed = _bt_time.time() - _batch_start_t
                        print(f"[publisher] FAST(JS) batch set-default: "
                              f"{len(_batch_ok)} cards updated in "
                              f"~{_elapsed:.1f}s "
                              f"({len(_batch_failed)} batch failures, "
                              f"{len(_no_version_ids)} no-version)",
                              flush=True)
                        result.forms_uploaded += len(_batch_ok)

                        # Step 3: URL-nav fallback for batch failures +
                        # cards that had no version in minimongo. This
                        # is the ONLY place per-card URL nav happens
                        # in the new design; on a clean run the list
                        # is empty and we skip it entirely.
                        _fallback_ids = _batch_failed + _no_version_ids
                        if _fallback_ids:
                            print(f"[publisher] URL-nav fallback for "
                                  f"{len(_fallback_ids)} cards",
                                  flush=True)
                        _card_by_id = {
                            c['card_id']: c for c in minicard_cards
                            if c.get('card_id')
                        }
                        for _cid in _fallback_ids:
                            _card = _card_by_id.get(_cid)
                            if _card is None:
                                continue
                            _fn = _card.get('name', _cid)
                            _href = _card.get('href', '')

                            # Inner helper: full nav-and-set-default
                            # sequence as one unit, so we can retry it
                            # cleanly after an SSO recovery without
                            # duplicating the body. Raises on any
                            # failure; caller decides whether to retry.
                            #
                            # Minicard CLICK navigation only — never
                            # page.goto a card URL. Direct URL nav
                            # triggers the Keycloak callback redirect
                            # chain (oidc/auth → /callback → board),
                            # and Meteor's client router doesn't re-
                            # mount the form panel after the bounce,
                            # so no radio ever renders regardless of
                            # state='attached'. The board is already
                            # loaded at this point (batch prep
                            # navigated to it), so clicking the
                            # minicard opens the panel inline the
                            # same way the upload loop does.
                            async def _nav_and_set_default():
                                # Whole body wrapped in try/finally so
                                # the panel ALWAYS closes after each
                                # card whether the radio click
                                # succeeded, the wait_for_selector
                                # timed out, or any earlier step raised.
                                # Without the finally, a failure mid-
                                # sequence left the panel open and its
                                # board overlay then blocked the next
                                # card's minicard click — the very
                                # cascade we hit in b1364b4.
                                try:
                                    # Dismiss any panel left open from
                                    # the previous card up front too —
                                    # the finally below guarantees
                                    # cleanup on the happy path, but
                                    # the first iteration after the
                                    # upload loop may still inherit a
                                    # stale panel.
                                    try:
                                        await page.keyboard.press('Escape')
                                        await page.wait_for_timeout(500)
                                    except Exception:
                                        pass
                                    _mcl = (
                                        page.locator(
                                            f'.js-minicard[href="{_href}"]')
                                        if _href
                                        else page.locator(
                                            '.js-minicard').filter(
                                            has_text=_fn).first
                                    )
                                    await _mcl.scroll_into_view_if_needed(
                                        timeout=5000)
                                    await _mcl.click()
                                    # Brief settle so the panel has a
                                    # chance to mount before we probe
                                    # for the radio. Matches the
                                    # timing the upload loop uses
                                    # after a minicard click.
                                    await page.wait_for_timeout(3000)
                                    # state='attached' (not the default
                                    # 'visible'): for SLEEP/SF12/EX/AE/
                                    # AESAE/CM/DV the radio renders
                                    # hidden in the DOM until
                                    # minimongo processes the version
                                    # — visible state would time out
                                    # the full 15s. Same fix as the
                                    # upload-loop wait in commit
                                    # 6e7b213; needed here too
                                    # because this fallback is what
                                    # makes publish-to-test succeed
                                    # for those forms when the batch
                                    # missed them.
                                    await page.wait_for_selector(
                                        'input[type=radio]',
                                        state='attached',
                                        timeout=15_000)
                                    await page.locator(
                                        'input[type=radio]'
                                    ).first.click(timeout=5000)
                                finally:
                                    # Always close the panel before the
                                    # next card's _nav_and_set_default
                                    # call. Best-effort — no panel open
                                    # means Escape is a no-op.
                                    try:
                                        await page.keyboard.press('Escape')
                                        await page.wait_for_timeout(300)
                                    except Exception:
                                        pass

                            try:
                                await _nav_and_set_default()
                                result.forms_uploaded += 1
                                print(f"[publisher] Fallback set-default "
                                      f"OK: {_fn}", flush=True)
                            except Exception as _rfe:
                                # SSO can expire during the long-running
                                # batch-fallback phase (the URL navs
                                # trigger Keycloak redirects). If the
                                # current URL shows we're in the auth
                                # flow, run the existing recovery (silent
                                # navigate-back → re-auth prompt → cookie
                                # reload) and retry the nav once. Same
                                # mechanism the upload loop uses; we just
                                # invoke it from this phase too.
                                _url_now = page.url or ''
                                _sso_lost = (
                                    await _session_expired(page)
                                    or '/callback' in _url_now
                                )
                                _retried_ok = False
                                if _sso_lost:
                                    print(f"[publisher] Fallback SSO "
                                          f"expiry for {_fn} "
                                          f"(url={_url_now!r}) — "
                                          f"recovering + retrying once",
                                          flush=True)
                                    try:
                                        recovered = await self._recover_session(
                                            page, context, study_url,
                                            _fn,
                                        )
                                    except Exception as _rec_e:
                                        recovered = False
                                        print(f"[publisher] Fallback "
                                              f"SSO recovery crashed: "
                                              f"{type(_rec_e).__name__}"
                                              f": {_rec_e}", flush=True)
                                    if recovered:
                                        try:
                                            await _nav_and_set_default()
                                            result.forms_uploaded += 1
                                            print(f"[publisher] Fallback "
                                                  f"set-default OK "
                                                  f"after SSO recovery: "
                                                  f"{_fn}", flush=True)
                                            _retried_ok = True
                                        except Exception as _rfe2:
                                            # Retry failed — fall
                                            # through to the failure
                                            # path with the new error.
                                            _rfe = _rfe2
                                if not _retried_ok:
                                    result.warnings.append(
                                        f"Fallback set-default failed "
                                        f"for {_fn}: {_rfe}"
                                    )
                                    print(f"[publisher] Fallback set-"
                                          f"default FAILED: {_fn}: "
                                          f"{_rfe}", flush=True)

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
    item_id: Optional[str] = None,
) -> FormPublishResult:
    """Thin wrapper around FormPublisher.publish_all_forms.

    Pass item_id when calling from the pipeline so mid-run SSO recovery
    can post a fresh auth link back to the row. Without item_id the
    publisher still runs and still recovers silently when Keycloak's
    server-side session is alive, but a true session expiry hard-fails
    the remaining cards because there's no way to prompt the operator.
    """
    publisher = FormPublisher(
        auth_token=auth_token,
        headless=headless,
        user_email=user_email,
        allowed_card_ids=allowed_card_ids,
        conflict_oids=conflict_oids,
        item_id=item_id,
    )
    return await publisher.publish_all_forms(study_url, edc_zip_url)
