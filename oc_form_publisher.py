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


def _strip_form_oid_prefix(oid: str) -> str:
    """Bridge a board card's formOcoid to its EDC-zip xlsx filename stem.

    Board cards reference forms by their OpenClinica-stored OID, which
    carries the F_ prefix OC adds internally (e.g. 'F_AE'). The xlsx files
    in the EDC zip keep bare filenames ('AE.xlsx'). Strip a leading F_ so a
    card OID matches its xlsx stem. Idempotent and backward-compatible with
    not-yet-migrated bare cards: 'F_AE' -> 'AE', 'AE' -> 'AE'."""
    o = str(oid or "").strip()
    if o.upper().startswith("F_"):
        return o[2:]
    return o


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


async def _detect_error_banner(page) -> str:
    """Return the text of a visible OC error banner/alert, or "" if none.

    DIAGNOSTIC ONLY. OC raises a red alert when it rejects an upload
    server-side (observed: "Upload version is successful while update the
    form is failed"; hypothesised: "An error occurred, please contact your
    system administrator") — that form never gets a version. Callers log
    the text and short-circuit the success-signal wait instead of burning
    the full timeout.

    Scans alert/toast/notification markup and requires an error keyword in
    the visible text, so benign info alerts don't false-trigger (a false
    positive would wrongly short-circuit a healthy upload). Returns the
    first matching banner's text, whitespace-collapsed and capped at 300
    chars. Never raises."""
    _ERR_KEYWORDS = ("error", "administrator", "failed", "fail",
                     "could not", "unable")
    for _sel in ('.alert-danger', '[role="alert"]', '.alert',
                 '.notification', '[class*="toast"]'):
        try:
            for el in await page.query_selector_all(_sel):
                if not await el.is_visible():
                    continue
                txt = ((await el.inner_text()) or "").strip()
                if txt and any(k in txt.lower() for k in _ERR_KEYWORDS):
                    return " ".join(txt.split())[:300]
        except Exception:
            pass
    return ""


async def _detect_success_banner(page) -> str:
    """Return the text of a visible OC green success banner, or "" if none.

    OC shows a green alert when an upload completes (text like "success",
    "successful", "uploaded"). This is the PRIMARY upload-success signal —
    when present the caller records success immediately and skips the
    form-version radio wait. Requires a success-styled element AND
    success-keyword text AND visibility, and EXCLUDES any text that also
    carries a failure keyword: OC's ambiguous "Upload version is successful
    while update the form is failed" must NOT read as a clean success — it
    falls through to the radio / REST-verify path instead. Never raises."""
    _OK_KEYWORDS = ("success", "successful", "uploaded")
    _FAIL_KEYWORDS = ("error", "failed", "fail", "unable", "could not")
    for _sel in ('.alert-success', '[class*="success"]',
                 '[class*="toast"]', '[role="status"]'):
        try:
            for el in await page.query_selector_all(_sel):
                if not await el.is_visible():
                    continue
                txt = ((await el.inner_text()) or "").strip()
                low = txt.lower()
                if (txt and any(k in low for k in _OK_KEYWORDS)
                        and not any(b in low for b in _FAIL_KEYWORDS)):
                    return " ".join(txt.split())[:300]
        except Exception:
            pass
    return ""


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
    # Form OID labels intentionally skipped because no XLSForm exists
    # in the EDC zip. These cards will never have versions — the publish
    # preflight must exclude them from missing-version checks so they
    # don't block publish for legitimately absent forms.
    no_xlsx_oids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public class ───────────────────────────────────────────────────────────

class FormPublisher:
    """Upload XLSForm files to an OpenClinica study via Playwright."""

    # Per-form upload budget. Real uploads on OC can take a few seconds
    # each; 30s gives headroom without hanging indefinitely on a broken
    # selector.
    PER_FORM_TIMEOUT_MS = 30_000

    # Fallback wait for the form-version radio (input[type=radio]) — only
    # reached when the green success banner is NOT detected (e.g. re-uploads
    # that don't re-show the banner). The green banner is now the PRIMARY
    # success signal, so this is short: a genuinely failed upload fails fast
    # (~15s) instead of burning the old 90s. Tunable.
    UPLOAD_RADIO_TIMEOUT_MS = 15_000

    # When the success signal still doesn't appear, OC's REST API often
    # lags the UI — wait this long before the REST verify so a version
    # that's mid-propagation isn't misread as "never created".
    REST_VERIFY_PREWAIT_S = 12

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
                    # DDP frame capture: the XLSForm upload travels over
                    # Meteor's WebSocket (DDP), not HTTP. page.on('websocket')
                    # only fires for sockets opened AFTER registration, and
                    # the DDP socket opens during the board load below — so we
                    # collect sockets HERE, before navigation. The per-form
                    # upload loop attaches frame listeners to these sockets and
                    # detaches them after each upload's settle.
                    _ddp_sockets = []
                    page.on('websocket',
                            lambda ws: _ddp_sockets.append(ws))
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

                    # Browser console capture. The HTTP-layer
                    # interceptors (page.route, context.on('response'),
                    # CDP Network.requestWillBeSent) were all removed
                    # once we confirmed the XLSForm upload goes over
                    # WebSocket/DDP — no HTTP request to intercept.
                    # Console capture stays because OC/Meteor surfaces
                    # DDP-level errors and warnings through the browser
                    # console, which is the one channel that can still
                    # show an upload failure.
                    async def _capture_console(msg):
                        if msg.type not in ('error', 'warning'):
                            return
                        _txt = msg.text or ""
                        # Drop the SSO postMessage heartbeat warning (fires
                        # every ~2s all session) — pure noise that buries the
                        # log. Errors are always kept; they can flag a real
                        # upload failure.
                        if msg.type != 'error' and (
                                'postMessage' in _txt
                                or 'The target origin provided' in _txt):
                            return
                        print(f"[browser-console] {msg.type}: "
                              f"{_txt}", flush=True)

                    page.on(
                        'console',
                        lambda m: asyncio.ensure_future(
                            _capture_console(m)
                        ),
                    )

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
                    # Track OIDs we've already called getForm for this session.
                    # getForm registers a form-service definition keyed by title;
                    # calling it twice for the same OID creates suffixed clones
                    # (F_SLEEPQUALITY_7732, etc.). One call per OID per session
                    # is sufficient — clones and later cards reuse the result.
                    _getform_called_oids: set = set()
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

                    # ── BUCKET FORMS LOOKUP ────────────────────────────────
                    # Fetch all forms already registered in this bucket from
                    # the form-service REST API. Used to skip getForm when
                    # F_SLEEP etc. already exist — calling getForm on an
                    # existing name creates a suffixed clone (F_SLEEP_1793)
                    # instead of reusing F_SLEEP, which breaks publish.
                    _bucket_forms_by_name: dict = {}
                    _bucket_uuid: str = ""
                    _page_token: str = ""
                    try:
                        _page_info = await page.evaluate("""
                            () => {
                                const board = Boards.findOne(
                                    window.location.pathname.split('/')[2]);
                                const token = localStorage.getItem(
                                    'jhi_access_token');
                                return {
                                    bucketUuid: board ? board.bucketUuid : null,
                                    token: token
                                };
                            }
                        """)
                        _bucket_uuid = _page_info.get('bucketUuid') or ''
                        _page_token = _page_info.get('token') or ''
                        if _bucket_uuid and _page_token:
                            # Extract subdomain from study_url — 'subdomain'
                            # var is defined in the outer publish_all_forms
                            # function but is not in scope inside this async
                            # playwright block, so derive it here directly.
                            _subdomain_for_bucket = (
                                urlparse(study_url).hostname.split('.')[0]
                            )
                            _forms_url = (
                                f"https://{_subdomain_for_bucket}"
                                f".build.openclinica.io"
                                f"/form-service/api/buckets"
                                f"/{_bucket_uuid}/forms"
                            )
                            async with httpx.AsyncClient(timeout=15) as _fc:
                                _fr = await _fc.get(
                                    _forms_url,
                                    headers={
                                        "Authorization":
                                            f"Bearer {_page_token}"
                                    }
                                )
                            if _fr.status_code == 200:
                                for _f in _fr.json():
                                    _fname = _f.get('name', '')
                                    if _fname:
                                        _bucket_forms_by_name[_fname] = _f
                                print(
                                    f"[publisher] bucket-forms: "
                                    f"{len(_bucket_forms_by_name)} forms "
                                    f"already registered in bucket "
                                    f"{_bucket_uuid}",
                                    flush=True)
                            else:
                                print(
                                    f"[publisher] bucket-forms lookup "
                                    f"failed: {_fr.status_code} — will "
                                    f"call getForm normally",
                                    flush=True)
                        else:
                            print(
                                "[publisher] bucket-forms: no bucketUuid "
                                "or token available — will call getForm "
                                "normally",
                                flush=True)
                    except Exception as _bfe:
                        print(
                            f"[publisher] bucket-forms lookup error: "
                            f"{_bfe} — will call getForm normally",
                            flush=True)

                    # ── XLSForm hash store (fast-rerun skip) ──────────────
                    # Load hashes from previous run. If hash matches AND
                    # form already has a version → skip upload entirely.
                    import hashlib as _hashlib
                    import json as _json
                    _hash_store_path = (
                        f"/data/pipeline_upload_records/"
                        f"{self.item_id or 'unknown'}_hashes.json"
                    )
                    _hash_store: dict = {}
                    _hash_store_updated: dict = {}
                    try:
                        if os.path.exists(_hash_store_path):
                            with open(_hash_store_path) as _hf:
                                _hash_store = _json.load(_hf)
                            print(
                                f"[publisher] hash store loaded: "
                                f"{len(_hash_store)} entries",
                                flush=True)
                    except Exception as _hse:
                        print(
                            f"[publisher] hash store load error: {_hse}",
                            flush=True)
                    # ──────────────────────────────────────────────────────

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

                    # Session keepalive: ping page.evaluate every 60s
                    # while the upload loop runs so Keycloak's SSO
                    # window doesn't expire mid-run. Cumulative
                    # set_input_files waits (now 30s × 7 slow forms
                    # plus per-card overhead) used to drift past
                    # the auth window and trip the SSO-recovery path
                    # mid-loop. The ping is read-only so it can't
                    # collide with concurrent uploads.
                    async def _keepalive():
                        while True:
                            await asyncio.sleep(60)
                            try:
                                await page.evaluate('1')
                            except Exception:
                                break

                    keepalive_task = asyncio.ensure_future(_keepalive())

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

                                    # (2)+(3) Per-card minimongo diagnostics:
                                    # the event (list) type/repeating flag and
                                    # the card's link status. Looked up live
                                    # from the Cards/Lists collections so they
                                    # reflect the actual board; logged for
                                    # EVERY card.
                                    try:
                                        _cdiag = await page.evaluate(
                                            """(cid) => {
                                                const c = (typeof Cards !== 'undefined')
                                                    ? Cards.findOne(cid) : null;
                                                if (!c) return null;
                                                const l = (c.listId && typeof Lists !== 'undefined')
                                                    ? Lists.findOne(c.listId) : null;
                                                return {
                                                    listId: c.listId || '',
                                                    parentId: c._parentId || '',
                                                    listType: l ? (l.type || '') : '',
                                                    isRepeating: l ? (l.isRepeating === undefined
                                                        ? null : !!l.isRepeating) : null,
                                                    eventOid: l ? (l.eventOid || l.oid
                                                        || l.title || l.name || '') : '',
                                                };
                                            }""",
                                            card['card_id'])
                                    except Exception:
                                        _cdiag = None
                                    if _cdiag:
                                        print(f"[publisher] Card event type for "
                                              f"{form_name} (OID={oid}): "
                                              f"listType={_cdiag.get('listType')!r} "
                                              f"repeating={_cdiag.get('isRepeating')} "
                                              f"eventOid={_cdiag.get('eventOid')!r}",
                                              flush=True)
                                        print(f"[publisher] Card link status for "
                                              f"{form_name}: parentId="
                                              f"{_cdiag.get('parentId') or 'NONE'}",
                                              flush=True)
                                    else:
                                        print(f"[publisher] Card diag unavailable "
                                              f"for {form_name} (card "
                                              f"{card['card_id']!r} not in "
                                              f"minimongo)", flush=True)

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
                                        # Board OID is F_-prefixed (OC's
                                        # stored OID); xlsx filenames are
                                        # bare — strip F_ to match the stem.
                                        xlsx_path = xlsx_map.get(
                                            _strip_form_oid_prefix(pre_oid or oid))
                                        if not xlsx_path:
                                            print(f"[publisher] Skipping "
                                                  f"{form_name} "
                                                  f"(OID={oid!r}): no "
                                                  f"xlsx in EDC zip",
                                                  flush=True)
                                            if oid and oid not in result.no_xlsx_oids:
                                                result.no_xlsx_oids.append(oid)
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
                                            # (1) DDP frame capture for this
                                            # upload — the form upload travels
                                            # over Meteor's WebSocket, not
                                            # HTTP. Attach frame listeners to
                                            # the socket(s) collected at session
                                            # start; detached after the settle.
                                            # Logs the form-related 'method'
                                            # frame and its matching 'result'
                                            # (or any error result).
                                            _ddp_methods = {}
                                            _ddp_log = {"methods": 0,
                                                        "results": 0,
                                                        "errors": 0,
                                                        "failed": False,
                                                        "version_confirmed": False}
                                            # Track the uploadVersion method
                                            # call id(s) so we can wait for the
                                            # matching DDP result — the 7 simple
                                            # failing forms' results arrive
                                            # AFTER the 2s settle, so the fixed
                                            # window was missing them.
                                            _uploadver_ids = set()
                                            _upload_result_event = asyncio.Event()

                                            def _ddp_frames(payload):
                                                out = []
                                                try:
                                                    if not isinstance(payload, (str, bytes, bytearray)):
                                                        payload = getattr(payload, "payload", None)
                                                    if isinstance(payload, (bytes, bytearray)):
                                                        payload = payload.decode("utf-8", "replace")
                                                    if not isinstance(payload, str):
                                                        return out
                                                    s = payload.strip()
                                                    if not s or s[0] in ("o", "h"):
                                                        return out
                                                    if s[0] in ("a", "c"):
                                                        s = s[1:]
                                                    data = json.loads(s)
                                                    items = data if isinstance(data, list) else [data]
                                                    for it in items:
                                                        try:
                                                            obj = json.loads(it) if isinstance(it, str) else it
                                                            if isinstance(obj, dict):
                                                                out.append(obj)
                                                        except Exception:
                                                            pass
                                                except Exception:
                                                    pass
                                                return out

                                            def _on_frame_sent(payload):
                                                for m in _ddp_frames(payload):
                                                    if m.get("msg") != "method":
                                                        continue
                                                    _name = m.get("method", "") or ""
                                                    _ps = json.dumps(m.get("params", []), default=str)
                                                    if any(k in (_name + " " + _ps).lower()
                                                           for k in ("form", "upload", "xlsform", "design")):
                                                        _mid = m.get("id", "")
                                                        if _mid:
                                                            _ddp_methods[_mid] = _name
                                                        # uploadVersion is the
                                                        # call whose result we
                                                        # wait for below.
                                                        _nl = _name.lower()
                                                        if _mid and ("uploadversion" in _nl
                                                                or ("upload" in _nl
                                                                    and "version" in _nl)):
                                                            _uploadver_ids.add(_mid)
                                                        _ddp_log["methods"] += 1
                                                        print(f"[publisher] DDP method "
                                                              f"sent for {form_name}: "
                                                              f"method={_name} "
                                                              f"params={_ps[:1200]}",
                                                              flush=True)

                                            def _on_frame_recv(payload):
                                                for m in _ddp_frames(payload):
                                                    if m.get("msg") != "result":
                                                        continue
                                                    _mid = m.get("id", "")
                                                    _ours = _mid in _uploadver_ids
                                                    if "error" in m:
                                                        _ddp_log["errors"] += 1
                                                        print(f"[publisher] DDP error "
                                                              f"result for {form_name}: "
                                                              f"{json.dumps(m.get('error'), default=str)[:1200]}",
                                                              flush=True)
                                                        if _ours:
                                                            _ddp_log["failed"] = True
                                                    elif _mid in _ddp_methods:
                                                        _ddp_log["results"] += 1
                                                        print(f"[publisher] DDP result "
                                                              f"for {form_name}: "
                                                              f"{json.dumps(m.get('result'), default=str)[:1200]}",
                                                              flush=True)
                                                        if _ours:
                                                            _res = m.get("result")
                                                            if (isinstance(_res, dict)
                                                                    and isinstance(_res.get("container"), dict)
                                                                    and _res["container"].get("id")):
                                                                _ddp_log["version_confirmed"] = True
                                                    # The uploadVersion result
                                                    # (success OR error) ends
                                                    # the capture wait below.
                                                    if _ours:
                                                        _upload_result_event.set()

                                            for _ws in _ddp_sockets:
                                                try:
                                                    _ws.on("framesent", _on_frame_sent)
                                                    _ws.on("framereceived", _on_frame_recv)
                                                except Exception:
                                                    pass
                                            # If this card has no existing version, the form-service may
                                            # have no definition for it (e.g. the source board card was
                                            # never uploaded). Call getForm via Meteor DDP before
                                            # uploadVersion — this is what the OC Designer UI does when
                                            # creating a new card, and it registers the definition so
                                            # uploadVersion has something to attach the version to.
                                            # Without this, uploadVersion silently discards the upload.
                                            if (not existing_version
                                                    and not (_cdiag and _cdiag.get("parentId"))
                                                    and oid not in _getform_called_oids):
                                                _oid_label = oid[2:] if oid.startswith('F_') else oid

                                                # ── Bucket-hit check ──────────────────────────
                                                # If this OID name already exists in the form
                                                # service bucket, skip getForm entirely and use
                                                # the existing registration. Calling getForm on
                                                # an already-registered name creates a suffixed
                                                # clone (F_SLEEP_1793) instead of returning the
                                                # existing F_SLEEP — which breaks publish.
                                                if _oid_label in _bucket_forms_by_name:
                                                    _existing_bf = (
                                                        _bucket_forms_by_name[_oid_label])
                                                    _gf_ocoid = (
                                                        _existing_bf.get('ocoid', ''))
                                                    print(
                                                        f"[publisher] bucket-hit for "
                                                        f"{form_name}: reusing existing "
                                                        f"{_gf_ocoid!r} (skipped getForm)",
                                                        flush=True)
                                                    _getform_called_oids.add(oid)
                                                    if pre_oid and pre_oid != oid:
                                                        _getform_called_oids.add(pre_oid)
                                                    if _gf_ocoid and _gf_ocoid != oid:
                                                        print(
                                                            f"[publisher] bucket-hit OID "
                                                            f"mismatch for {form_name}: "
                                                            f"card has {oid!r} but bucket "
                                                            f"has {_gf_ocoid!r} — updating "
                                                            f"card and using bucket OID",
                                                            flush=True)
                                                        try:
                                                            await page.evaluate(
                                                                """([cardId, newOcoid]) => {
                                                                    Meteor.call(
                                                                        '/cards/update',
                                                                        {_id: cardId},
                                                                        {$set: {
                                                                            formOcoid: newOcoid,
                                                                            dateLastActivity: {
                                                                                $date: Date.now()
                                                                            }
                                                                        }},
                                                                        {}
                                                                    );
                                                                }""",
                                                                [card_meteor_id,
                                                                 _gf_ocoid],
                                                            )
                                                        except Exception as _upd_e:
                                                            print(
                                                                f"[publisher] card OID "
                                                                f"update failed for "
                                                                f"{form_name}: {_upd_e}",
                                                                flush=True)
                                                        oid = _gf_ocoid
                                                elif _oid_label not in _bucket_forms_by_name:
                                                # ── getForm (fresh registration) ──────────────
                                                # OID not in bucket — call getForm to register
                                                # it fresh. No conflict, no suffix appended.
                                                # Pass the OID suffix (e.g. "SLEEP" for F_SLEEP)
                                                # as the label so OC registers F_SLEEP exactly.
                                                    _gf_result = await page.evaluate("""
                                                    (oidLabel) => new Promise((resolve) => {
                                                        const board = Boards.findOne(
                                                            window.location.pathname.split('/')[2]);
                                                        const token = localStorage.getItem(
                                                            'jhi_access_token');
                                                        if (!board || !token) {
                                                            resolve({ok: false,
                                                                     err: 'no board or token'});
                                                            return;
                                                        }
                                                        // Use the OID suffix as the label so OC
                                                        // registers F_<oidLabel> — matching the
                                                        // pipeline-generated form OID exactly.
                                                        if (!oidLabel) {
                                                            resolve({ok: false,
                                                                     err: 'no oid label'});
                                                            return;
                                                        }
                                                        Meteor.call(
                                                            'getForm',
                                                            board.bucketUuid,
                                                            oidLabel,
                                                            token,
                                                            (err, result) => {
                                                                if (err) {
                                                                    resolve({ok: false,
                                                                             err: String(err)});
                                                                } else if (!result || !result.ocoid) {
                                                                    resolve({ok: false,
                                                                             err: 'no ocoid returned'});
                                                                } else {
                                                                    resolve({ok: true,
                                                                             ocoid: result.ocoid,
                                                                             id: result.id});
                                                                }
                                                            }
                                                        );
                                                    })
                                                """, _oid_label)
                                                    if _gf_result.get('ok'):
                                                        print(
                                                            f"[publisher] getForm OK for {form_name}: "
                                                            f"ocoid={_gf_result.get('ocoid')} "
                                                            f"id={_gf_result.get('id')}",
                                                            flush=True)
                                                        _getform_called_oids.add(oid)
                                                        # Also register pre_oid so clone cards
                                                        # (which still carry the short OID) never
                                                        # trigger a second getForm call.
                                                        if pre_oid and pre_oid != oid:
                                                            _getform_called_oids.add(pre_oid)
                                                        # If getForm returned a different OID than
                                                        # what the card has (common — form-service
                                                        # derives OID from title, e.g. F_SLEEPQUALITY
                                                        # for a card with formOcoid F_SLEEP), update
                                                        # the card's formOcoid in minimongo and update
                                                        # the local `oid` variable so uploadVersion
                                                        # uses the correct OID.
                                                        _gf_ocoid = _gf_result.get('ocoid', '')
                                                        if _gf_ocoid and _gf_ocoid != oid:
                                                            print(
                                                                f"[publisher] getForm OID mismatch "
                                                                f"for {form_name}: card has {oid!r} "
                                                                f"but form-service has {_gf_ocoid!r} "
                                                                f"— updating card and using "
                                                                f"form-service OID",
                                                                flush=True)
                                                            try:
                                                                await page.evaluate(
                                                                    """([cardId, newOcoid]) => {
                                                                        const c = Cards.findOne(cardId);
                                                                        if (c) {
                                                                            Cards.update(cardId, {
                                                                                $set: {formOcoid: newOcoid}
                                                                            });
                                                                        }
                                                                    }""",
                                                                    [card_meteor_id, _gf_ocoid],
                                                                )
                                                            except Exception as _upd_e:
                                                                print(
                                                                    f"[publisher] minimongo OID "
                                                                    f"update failed for {form_name}: "
                                                                    f"{_upd_e}",
                                                                    flush=True)
                                                            oid = _gf_ocoid
                                                    else:
                                                        print(
                                                            f"[publisher] getForm FAILED for "
                                                            f"{form_name}: "
                                                            f"{_gf_result.get('err')} — "
                                                            f"proceeding anyway; uploadVersion "
                                                            f"may fail",
                                                            flush=True)
                                            # ── Hash-based fast skip ──────────────────────
                                            # Compute MD5 of the XLSForm file. If the hash
                                            # matches the previous run AND the form already
                                            # has a version in the bucket, skip upload
                                            # entirely — the existing version is still valid.
                                            _oid_label_for_hash = (
                                                oid[2:] if oid.startswith('F_') else oid)
                                            _form_bytes = xlsx_path.read_bytes()
                                            _form_hash = _hashlib.md5(
                                                _form_bytes).hexdigest()
                                            _hash_store_updated[
                                                _oid_label_for_hash] = _form_hash
                                            _bf_entry = _bucket_forms_by_name.get(
                                                _oid_label_for_hash, {})
                                            _has_existing_version = bool(
                                                _bf_entry.get('versions'))
                                            if (_form_hash == _hash_store.get(
                                                    _oid_label_for_hash)
                                                    and _has_existing_version):
                                                print(
                                                    f"[publisher] hash-skip {form_name}: "
                                                    f"content unchanged, existing version "
                                                    f"present — skipping upload",
                                                    flush=True)
                                                confirmed_versioned_oids.add(oid)
                                                session_uploaded_oids.add(oid)
                                                if pre_oid and pre_oid != oid:
                                                    session_uploaded_oids.add(pre_oid)
                                                continue
                                            # ─────────────────────────────────────────────
                                            await page.set_input_files(
                                                'input.js-design-form-input',
                                                str(xlsx_path))
                                            # 1500ms settle: enough for the
                                            # browser to begin processing the
                                            # upload and for most uploadVersion
                                            # results to arrive. The DDP listener
                                            # captures results as they arrive;
                                            # the unconditional extended wait
                                            # below catches any that land just
                                            # after this window closes.
                                            await page.wait_for_timeout(1500)
                                            # (1) Extend the DDP capture window
                                            # unconditionally: always wait up to
                                            # 3s for the uploadVersion result
                                            # after the settle — even if no
                                            # uploadVersion method was seen yet —
                                            # so a result that fires just after
                                            # the settle is still captured. Only
                                            # proceed to the radio wait once the
                                            # 3s lapses without it.
                                            try:
                                                await asyncio.wait_for(
                                                    _upload_result_event.wait(),
                                                    timeout=3)
                                                print("[publisher] DDP extended "
                                                      "wait: uploadVersion "
                                                      "captured", flush=True)
                                            except asyncio.TimeoutError:
                                                print("[publisher] DDP extended "
                                                      "wait: timed out (3s), "
                                                      "proceeding to radio wait.",
                                                      flush=True)
                                            # (1) Detach the DDP frame listeners
                                            # and log a capture summary (keeps a
                                            # "none captured" path when no
                                            # form/upload frames were seen).
                                            for _ws in _ddp_sockets:
                                                try:
                                                    _ws.remove_listener("framesent", _on_frame_sent)
                                                    _ws.remove_listener("framereceived", _on_frame_recv)
                                                except Exception:
                                                    pass
                                            if (_ddp_log["methods"] or _ddp_log["results"]
                                                    or _ddp_log["errors"]):
                                                print(f"[publisher] DDP capture "
                                                      f"summary for {form_name}: "
                                                      f"methods={_ddp_log['methods']} "
                                                      f"results={_ddp_log['results']} "
                                                      f"errors={_ddp_log['errors']} "
                                                      f"(sockets={len(_ddp_sockets)})",
                                                      flush=True)
                                            else:
                                                print(f"[publisher] OC upload: none "
                                                      f"captured — no DDP form/upload "
                                                      f"frames for {form_name} "
                                                      f"(sockets={len(_ddp_sockets)})",
                                                      flush=True)
                                            # DDP verdicts for the timing
                                            # optimizations below: skip the
                                            # radio wait + pre-verify wait on a
                                            # confirmed failure, and skip the
                                            # REST verify on a confirmed success.
                                            _upload_failed_by_ddp = _ddp_log["failed"]
                                            _ddp_version_confirmed = _ddp_log["version_confirmed"]
                                            # ── Upload-result banner detection.
                                            # Read BEFORE the dismiss loop
                                            # below closes the banners. The
                                            # GREEN success banner is the
                                            # primary success signal — when
                                            # present we record success and
                                            # skip the radio wait. A RED error
                                            # banner means a server-side
                                            # rejection (no version) — short-
                                            # circuit the radio wait.
                                            _success_text = await _detect_success_banner(page)
                                            _banner_text = await _detect_error_banner(page)
                                            if _success_text:
                                                print(f'[publisher] SUCCESS BANNER '
                                                      f'detected for {form_name}: '
                                                      f'"{_success_text}"',
                                                      flush=True)
                                            if _banner_text:
                                                print(f'[publisher] ERROR BANNER '
                                                      f'detected for {form_name}: '
                                                      f'"{_banner_text}"',
                                                      flush=True)
                                            # Dismiss any OC error
                                            # banner. Observed text is
                                            # "Upload version is
                                            # successful while update
                                            # the form is failed" —
                                            # the upload DID land but
                                            # the banner overlays the
                                            # radio AND, critically,
                                            # an undismissed banner
                                            # also blocks the NEXT
                                            # card's upload from
                                            # firing (we'd see silent
                                            # cascading failures
                                            # across subsequent
                                            # forms). Iterate
                                            # selectors from most-
                                            # specific to most-
                                            # generic so we pick up
                                            # whichever variant of
                                            # OC's alert markup is
                                            # in play.
                                            for _sel in [
                                                '.alert-danger .close',
                                                '.alert .close',
                                                'button[data-dismiss="alert"]',
                                                '.notification .close',
                                                '[class*="alert"] [class*="close"]',
                                            ]:
                                                try:
                                                    _btn = await page.query_selector(_sel)
                                                    if _btn:
                                                        await _btn.click(timeout=1000)
                                                        await page.wait_for_timeout(300)
                                                        break
                                                except Exception:
                                                    pass
                                            # Small extra settle
                                            # before the radio wait —
                                            # after a banner dismiss
                                            # the DOM may briefly
                                            # reflow before the radio
                                            # mounts cleanly.
                                            await page.wait_for_timeout(1000)
                                            # Wait for upload confirmation
                                            # in two stages. Brought
                                            # back down to 30s after
                                            # the 90s ceiling pushed
                                            # cumulative wall-clock
                                            # past Keycloak's SSO
                                            # window on long runs
                                            # (7 slow forms × 90s =
                                            # 10.5 extra minutes of
                                            # dead time). The
                                            # _keepalive coroutine
                                            # started before the
                                            # upload loop pings
                                            # page.evaluate every
                                            # 60s to keep the
                                            # session warm; for the
                                            # genuinely slow forms
                                            # the 30s cap will still
                                            # time out and fall
                                            # through to the Stage 2
                                            # prevBtn check, but
                                            # subsequent uploads no
                                            # longer drag a dying
                                            # session along.
                                            try:
                                                # DDP already confirmed failure —
                                                # the radio will never appear, so
                                                # skip the wait entirely (saves
                                                # the full 15s).
                                                if _upload_failed_by_ddp:
                                                    raise TimeoutError(
                                                        "DDP confirmed failure")
                                                # Green success banner already
                                                # confirmed the upload — skip
                                                # the radio wait entirely and
                                                # fall through to success
                                                # recording. Otherwise: error
                                                # banner → short 3s; else the
                                                # 15s re-upload fallback.
                                                if not _success_text:
                                                    await page.wait_for_selector(
                                                        'input[type=radio]',
                                                        state='attached',
                                                        timeout=(3000 if _banner_text
                                                                 else self.UPLOAD_RADIO_TIMEOUT_MS))
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
                                                    # OC's REST API lags the
                                                    # UI — wait before the
                                                    # verify so a version
                                                    # that's still propagating
                                                    # isn't read as missing.
                                                    # Skip the wait when DDP
                                                    # already confirmed failure
                                                    # (no version will appear).
                                                    if not _upload_failed_by_ddp:
                                                        print(f"[publisher] "
                                                              f"waiting "
                                                              f"{self.REST_VERIFY_PREWAIT_S}s "
                                                              f"before REST verify "
                                                              f"of {form_name}",
                                                              flush=True)
                                                        await asyncio.sleep(
                                                            self.REST_VERIFY_PREWAIT_S)
                                                    # REST verify: did OC
                                                    # actually create the
                                                    # version despite the
                                                    # UI timeout? The
                                                    # publisher has no
                                                    # standalone token /
                                                    # subdomain / board_id —
                                                    # the token is
                                                    # self.auth_token
                                                    # (passed by pipeline's
                                                    # caller), subdomain +
                                                    # board_id parse out of
                                                    # study_url, and the OID
                                                    # is oid|pre_oid. Board
                                                    # JSON shape per
                                                    # pipeline._fetch_oc_versions_by_oid:
                                                    # top-level cards[] with
                                                    # formOcoid + versions[]
                                                    # (NOT lists[].cards[] /
                                                    # formVersionId).
                                                    # DDP already confirmed the
                                                    # version (result had a
                                                    # container with an id) — set
                                                    # the flag and skip the REST
                                                    # verify call entirely.
                                                    _version_created = False
                                                    if _ddp_version_confirmed:
                                                        _version_created = True
                                                        print(f"[publisher] DDP "
                                                              f"confirmed version "
                                                              f"for {form_name}: "
                                                              f"skipping REST verify",
                                                              flush=True)
                                                    try:
                                                        _host = urlparse(
                                                            study_url).hostname or ""
                                                        _subdomain = (
                                                            _host.split(".")[0]
                                                            if _host else "")
                                                        _board_id = ""
                                                        _bparts = study_url.split("/b/")
                                                        if len(_bparts) > 1:
                                                            _board_id = _bparts[1].split("/")[0]
                                                        _target_oid = (oid or pre_oid or "").upper()
                                                        if (not _version_created
                                                                and self.auth_token and _subdomain
                                                                and _board_id and _target_oid):
                                                            _vurl = (
                                                                f"https://{_subdomain}"
                                                                f".design.openclinica.io"
                                                                f"/api/boards/{_board_id}")
                                                            async with httpx.AsyncClient(
                                                                    timeout=10) as _vc:
                                                                _vr = await _vc.get(
                                                                    _vurl,
                                                                    headers={
                                                                        "Authorization":
                                                                            f"Bearer {self.auth_token}",
                                                                        "Content-Type":
                                                                            "application/json",
                                                                    })
                                                            if _vr.status_code == 200:
                                                                _bdata = _vr.json()
                                                                for _card in (_bdata.get("cards") or []):
                                                                    if ((_card.get("formOcoid") or "").upper()
                                                                            == _target_oid
                                                                            and _card.get("versions")):
                                                                        _version_created = True
                                                                        break
                                                        if _version_created and not _ddp_version_confirmed:
                                                            print(f"[publisher] REST "
                                                                  f"verify: version "
                                                                  f"confirmed for "
                                                                  f"{form_name} despite "
                                                                  f"UI timeout",
                                                                  flush=True)
                                                        elif not _version_created:
                                                            print(f"[publisher] REST "
                                                                  f"verify: NO version "
                                                                  f"found for "
                                                                  f"{form_name} — upload "
                                                                  f"likely failed",
                                                                  flush=True)
                                                    except Exception as _ve:
                                                        print(f"[publisher] REST verify "
                                                              f"error: {_ve}", flush=True)
                                                    # Reset the hung panel. On a
                                                    # failed upload OC leaves the
                                                    # panel in a "processing"
                                                    # state (form accepted but no
                                                    # radio activated), so the
                                                    # NEXT form's set_input_files
                                                    # fires on a stale panel with
                                                    # no DDP. Navigate back to the
                                                    # board so the next form opens
                                                    # a clean panel.
                                                    if not _version_created:
                                                        print(f"[publisher] reset "
                                                              f"panel state — "
                                                              f"navigating back to "
                                                              f"board after failed "
                                                              f"upload of {form_name}",
                                                              flush=True)
                                                        try:
                                                            await page.goto(study_url)
                                                            await page.wait_for_selector(
                                                                '.js-list',
                                                                timeout=30000)
                                                            await page.wait_for_timeout(1000)
                                                        except Exception as _ne:
                                                            print(f"[publisher] board "
                                                                  f"reload after panel "
                                                                  f"reset failed: {_ne}",
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
                                            # Also register pre_oid (short OID before getForm
                                            # rewrite) so clone cards carrying the short OID
                                            # hit the dedup check and skip re-upload,
                                            # preventing duplicate versions.
                                            if pre_oid and pre_oid != oid:
                                                session_uploaded_oids.add(pre_oid)
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

                    # Cancel the session keepalive — no more long
                    # waits to protect against from here. Don't await
                    # the task; cancel() is sufficient and we don't
                    # care about its final state.
                    try:
                        keepalive_task.cancel()
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

                    # ── Write updated hash store ───────────────────────
                    # Persist MD5 hashes for all forms processed this run
                    # so the next fast rerun can skip unchanged forms.
                    if _hash_store_updated:
                        try:
                            os.makedirs(
                                os.path.dirname(_hash_store_path),
                                exist_ok=True)
                            with open(_hash_store_path, 'w') as _hf:
                                _json.dump(_hash_store_updated, _hf)
                            print(
                                f"[publisher] hash store written: "
                                f"{len(_hash_store_updated)} entries",
                                flush=True)
                        except Exception as _hwe:
                            print(
                                f"[publisher] hash store write error: "
                                f"{_hwe}",
                                flush=True)
                    # ──────────────────────────────────────────────────

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
                        print("[publisher] Set-default phase starting "
                              f"(Meteor.call per card)...", flush=True)

                        import time as _bt_time
                        _batch_start_t = _bt_time.time()

                        _eligible = [
                            c for c in minicard_cards if c.get('card_id')
                        ]
                        _eligible_ids = [c['card_id'] for c in _eligible]

                        # Step 1: look up each card's most-recent version
                        # from minimongo (fast JS lookup).
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
                                                const v = c.versions[c.versions.length - 1];
                                                const vid = (v && v.ocoid) || null;
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
                        except Exception as _be:
                            print(f"[publisher] Version lookup failed "
                                  f"({_be}) — no cards will be defaulted",
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

                        # Log which cards have no version for diagnostics.
                        _card_by_id = {
                            c['card_id']: c for c in minicard_cards
                            if c.get('card_id')
                        }
                        if _no_version_ids:
                            _no_ver_names = [
                                (_card_by_id.get(cid, {}).get('form_name')
                                 or _card_by_id.get(cid, {}).get('form_oid')
                                 or cid)
                                for cid in _no_version_ids
                            ]
                            print(f"[publisher] {len(_no_version_ids)} cards "
                                  f"have no version (upload failed or not yet "
                                  f"propagated): {_no_ver_names}", flush=True)

                        # Step 2: For each card WITH a version, call
                        # Meteor.call('/cards/update', ...) via page.evaluate.
                        # This sends a REAL DDP method over the WebSocket to
                        # the OC server — unlike Cards.update() which is
                        # client-side minimongo only and never reaches the
                        # server. The ! warning in the designer UI clears
                        # when this call succeeds server-side.
                        _meteor_ok = []
                        _meteor_failed = []
                        for _cid, _vid in _card_version_map.items():
                            _crd = _card_by_id.get(_cid, {})
                            _fn = (_crd.get('form_name')
                                   or _crd.get('form_oid') or _cid)
                            try:
                                _mresult = await asyncio.wait_for(
                                    page.evaluate(
                                        """
                                        ([cardId, versionOcoid]) => new Promise((resolve) => {
                                            Meteor.call(
                                                '/cards/update',
                                                {_id: cardId},
                                                {$set: {
                                                    selected_form_version_ocoid: versionOcoid,
                                                    dateLastActivity: {$date: Date.now()}
                                                }},
                                                {},
                                                (err, result) => {
                                                    if (err) {
                                                        resolve({ok: false,
                                                                 err: String(err)});
                                                    } else {
                                                        resolve({ok: true,
                                                                 result: result});
                                                    }
                                                }
                                            );
                                        })
                                        """,
                                        [_cid, _vid],
                                    ),
                                    timeout=10,
                                )
                                if _mresult.get('ok'):
                                    _meteor_ok.append(_cid)
                                else:
                                    _err_msg = _mresult.get('err', 'unknown')
                                    _meteor_failed.append((_cid, _fn, _err_msg))
                                    print(f"[publisher] set-default "
                                          f"Meteor.call failed for {_fn}: "
                                          f"{_err_msg}", flush=True)
                            except asyncio.TimeoutError:
                                _meteor_failed.append((_cid, _fn, 'timeout'))
                                print(f"[publisher] set-default "
                                      f"Meteor.call timed out for {_fn}",
                                      flush=True)
                            except Exception as _me:
                                _meteor_failed.append((_cid, _fn, str(_me)))
                                print(f"[publisher] set-default "
                                      f"Meteor.call error for {_fn}: {_me}",
                                      flush=True)

                        _elapsed = _bt_time.time() - _batch_start_t
                        print(f"[publisher] set-default complete: "
                              f"{len(_meteor_ok)}/{len(_card_version_map)} "
                              f"succeeded in ~{_elapsed:.1f}s "
                              f"({len(_meteor_failed)} failed, "
                              f"{len(_no_version_ids)} no-version)",
                              flush=True)
                        result.forms_uploaded += len(_meteor_ok)

                        # Step 3: URL-nav fallback for Meteor.call failures.
                        # Cards that had a version but the DDP call failed
                        # get a direct radio-button click via page navigation.
                        if _meteor_failed:
                            print(f"[publisher] URL-nav fallback for "
                                  f"{len(_meteor_failed)} failed cards",
                                  flush=True)
                        for _cid, _fn, _err in _meteor_failed:
                            try:
                                await _nav_and_set_default()
                            except Exception as _rfe:
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
        """Establish a designer session by navigating via the build app.

        The designer (cust1.design.openclinica.io) redirects immediately
        to Keycloak when navigated to directly — even with a valid build
        app session. The only reliable path into the designer is to load
        the build app's My Studies page and click the "Design" button on
        the target study card. That redirect chain establishes the OIDC
        designer session automatically, exactly as a human would do it.

        Strategy:
          1. Navigate to build app My Studies (cust1.build.openclinica.io)
             using the captured session — this works because the extension
             captures the build app session.
          2. Wait for study cards to load (a.btn-design selector appears).
          3. Find the card matching the study name (extracted from study_url
             slug, e.g. "crs-135" → "CRS-135"). Fall back to first card
             (top-left = most recently created) if no match found.
          4. Click its "Design" button — browser follows the SSO redirect
             chain and lands in the designer board with a live session.
          5. Wait for AUTH_SUCCESS_SELECTOR (.js-back-to-sm) to appear —
             confirms the designer board rendered successfully.

        Returns True if AUTH_SUCCESS_SELECTOR appears within 30s of
        clicking Design; False otherwise.
        """
        from urllib.parse import urlparse

        # Extract subdomain and study name slug from study_url.
        # study_url is like: https://cust1.design.openclinica.io/b/BOARDID/crs-135
        parsed = urlparse(study_url)
        host_parts = parsed.hostname.split(".")  # ['cust1', 'design', 'openclinica', 'io']
        subdomain = host_parts[0]  # 'cust1'
        slug_parts = [p for p in parsed.path.split("/") if p]
        # path is ['b', 'BOARDID', 'crs-135'] — last part is the slug
        study_slug = slug_parts[-1] if slug_parts else ""
        # Convert slug to study ID format: 'crs-135' → 'CRS-135'
        study_name = study_slug.replace("-", "-").upper()

        my_studies_url = f"https://{subdomain}.build.openclinica.io/#/account-study"
        print(f"[auth-sso] navigating to My Studies via build app: {my_studies_url}",
              flush=True)
        print(f"[auth-sso] looking for study card matching: {study_name!r}",
              flush=True)

        try:
            await page.goto(my_studies_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Wait for study cards (Design buttons) to appear
            await page.wait_for_selector("a.btn-design", timeout=20000)
            print("[auth-sso] My Studies page loaded — study cards visible",
                  flush=True)

            # Find the Design button for the matching study.
            # Each card has div.ngx-ellipsis-inner containing the study ID text.
            # We walk up from each a.btn-design to its card container and check
            # if any ngx-ellipsis-inner text matches our study name.
            design_btn = await page.evaluate_handle("""(studyName) => {
                const btns = Array.from(document.querySelectorAll('a.btn-design'));
                // Try to find matching study card
                for (const btn of btns) {
                    // Walk up to card container (typically 5-6 levels)
                    let el = btn;
                    for (let i = 0; i < 8; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const names = el.querySelectorAll('div.ngx-ellipsis-inner');
                        for (const n of names) {
                            if (n.textContent.trim() === studyName) {
                                return btn;
                            }
                        }
                    }
                }
                // Fallback: first Design button (top-left = most recent study)
                return btns.length > 0 ? btns[0] : null;
            }""", study_name)

            # Check we got a valid element handle
            is_null = await page.evaluate("el => el === null", design_btn)
            if is_null:
                print("[auth-sso] no Design buttons found on My Studies page",
                      flush=True)
                return False

            # Log which study we're clicking into
            btn_href = await page.evaluate("el => el.href || ''", design_btn)
            print(f"[auth-sso] clicking Design button: {btn_href}", flush=True)

            # Click Design — this triggers the SSO redirect chain into designer
            await design_btn.click()

            # Wait for designer board to fully load
            await page.wait_for_selector(
                self.AUTH_SUCCESS_SELECTOR, timeout=30000)
            print(f"oc_form_publisher: authenticated as {self.user_email} "
                  f"via build app → designer redirect chain", flush=True)
            return True

        except Exception as e:
            print(f"[auth-sso] failed: {e}", flush=True)
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
