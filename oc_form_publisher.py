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
    forms_total: int                    # total .xlsx files in the ZIP
    errors: List[str] = field(default_factory=list)

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

    # Selectors that, if present after the /#/ocstafflogin redirect
    # chain settles, indicate we are authenticated and on the OC
    # designer UI. Comma-separated for or-match. TODO(oc-ui): replace
    # with confirmed selectors once we see real-OC HTML via the
    # diagnostic dumps in _upload_one.
    AUTH_SUCCESS_SELECTOR = (
        '[data-test="study-board"], .main-nav, #app-content')

    def __init__(
        self,
        auth_token: Optional[str] = None,
        headless: bool = True,
        user_email: Optional[str] = None,
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
        """
        self.auth_token = auth_token
        self.headless = headless
        self.user_email = user_email

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
                        # TEMP: disabled during auth diagnosis — see chat.
                        # Keeps the captured session intact so a false-
                        # positive "expired" verdict doesn't force a needless
                        # re-capture before we know if the selector is wrong.
                        # try:
                        #     os.remove(self._session_path)
                        # except OSError:
                        #     pass
                        raise RuntimeError(
                            f"Saved SSO session for {self.user_email} "
                            f"appears expired (auth-success selector not "
                            f"found after /#/ocstafflogin redirect chain). "
                            f"Session file PRESERVED for diagnosis (delete "
                            f"temporarily disabled — see [auth-debug] logs).")

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

                    # 6. Auth confirmed — navigate to the study and upload.
                    await page.goto(study_url, wait_until="networkidle",
                                    timeout=self.PER_FORM_TIMEOUT_MS)

                    for xlsx in xlsx_paths:
                        try:
                            await self._upload_one(page, xlsx)
                            result.forms_uploaded += 1
                        except Exception as e:
                            result.errors.append(
                                f"{xlsx.name}: {type(e).__name__}: {e}")
                finally:
                    await browser.close()

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
        """Navigate to /#/ocstafflogin and report whether auth succeeded.

        If the browser context was created with a valid saved
        storage_state, the SSO redirect chain completes silently and we
        land on the OC designer UI. If the session is stale (or absent),
        we end up on Google's login screen instead — caller handles both
        branches.

        Returns True if AUTH_SUCCESS_SELECTOR appears within 5s of the
        redirect chain settling; False otherwise.
        """
        domain = urlparse(study_url).hostname or ""
        sso_url = f"https://{domain}/#/ocstafflogin"
        await page.goto(sso_url, wait_until="networkidle")
        # Give multi-step SSO redirects (IdP → callback → OC) a moment
        # to settle past the initial networkidle.
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_selector(
                self.AUTH_SUCCESS_SELECTOR, timeout=5000)
            print(f"oc_form_publisher: authenticated as {self.user_email}",
                  flush=True)
            return True
        except Exception:
            return False

    async def _upload_one(self, page, xlsx_path: Path) -> None:
        """Upload a single .xlsx file via the form-version UI.

        # TODO(oc-ui): selectors below are placeholders. Replace after a
        # one-time DOM inspection of the real OC designer.
        """
        FILE_INPUT_SELECTOR    = 'input[type="file"]'
        UPLOAD_BUTTON_SELECTOR = 'button[type="submit"]'
        SUCCESS_SELECTOR       = '.upload-success, .form-version-row, [data-test="upload-complete"]'

        try:
            await page.set_input_files(FILE_INPUT_SELECTOR, str(xlsx_path))
        except Exception as e:
            # Dump page state on failure (typically TimeoutError when the
            # file-input selector can't be found — most often because we
            # landed on a login screen instead of the designer; cookie
            # name is a placeholder, see _inject_auth TODO).
            import time
            ts   = int(time.time())
            png  = f"/tmp/oc_upload_error_{xlsx_path.stem}_{ts}.png"
            html = f"/tmp/oc_upload_error_{xlsx_path.stem}_{ts}.html"
            try:
                await page.screenshot(path=png, full_page=True)
                Path(html).write_text(await page.content(), encoding="utf-8")
            except Exception as dump_err:
                print(f"oc_form_publisher: page-dump failed: {dump_err}",
                      flush=True)
            raise RuntimeError(
                f"{type(e).__name__}: {e}  "
                f"[page dumps saved: {png}, {html}]") from e

        await page.click(UPLOAD_BUTTON_SELECTOR)
        await page.wait_for_selector(
            SUCCESS_SELECTOR, timeout=self.PER_FORM_TIMEOUT_MS,
        )


# ── Module-level convenience wrapper ───────────────────────────────────────

async def publish_forms_to_openclinica(
    study_url: str,
    edc_zip_url: str,
    auth_token: Optional[str] = None,
    headless: bool = True,
    user_email: Optional[str] = None,
) -> FormPublishResult:
    """Thin wrapper around FormPublisher.publish_all_forms.

    Use from pipeline.py:

        from oc_form_publisher import publish_forms_to_openclinica
        result = await publish_forms_to_openclinica(
            study_url=study_url,
            edc_zip_url=edc_zip_url,
            auth_token=token,
            user_email=oc_email,
        )
    """
    publisher = FormPublisher(
        auth_token=auth_token,
        headless=headless,
        user_email=user_email,
    )
    return await publisher.publish_all_forms(study_url, edc_zip_url)
