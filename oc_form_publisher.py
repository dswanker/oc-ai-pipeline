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

  - Auth method (bearer-cookie? localStorage token? full login form?)
  - Form-upload page URL pattern (relative to study_url)
  - DOM selectors for file input, upload button, success indicator
  - Whether upload is per-form or batched

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
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import httpx


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

    def __init__(
        self,
        auth_token: Optional[str] = None,
        headless: bool = True,
    ):
        """
        Args:
            auth_token: OC bearer token (from _get_oc_token in pipeline.py).
                If provided, it's injected as a cookie on the browser context
                before navigation. Cookie NAME is a best-guess — verify
                against real OC.
            headless: True for production, False for local debugging so you
                can watch the browser.
        """
        self.auth_token = auth_token
        self.headless = headless

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

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                try:
                    context = await browser.new_context()
                    await self._inject_auth(context, study_url)
                    page = await context.new_page()
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

    async def _inject_auth(self, context, study_url: str) -> None:
        """Best-guess auth injection. Real OC may need a different mechanism."""
        if not self.auth_token:
            return
        # TODO(oc-ui): verify the actual cookie name OC's web UI reads.
        # Common alternatives if "auth_token" doesn't work:
        #   - "JSESSIONID"  (Java/Spring servlet session)
        #   - "access_token" / "id_token"  (OIDC)
        #   - localStorage instead of cookie (would need page.add_init_script)
        domain = urlparse(study_url).hostname or ""
        await context.add_cookies([{
            "name":   "auth_token",
            "value":  self.auth_token,
            "domain": domain,
            "path":   "/",
            "secure": True,
        }])

    async def _upload_one(self, page, xlsx_path: Path) -> None:
        """Upload a single .xlsx file via the form-version UI.

        # TODO(oc-ui): selectors below are placeholders. Replace after a
        # one-time DOM inspection of the real OC designer.
        """
        FILE_INPUT_SELECTOR    = 'input[type="file"]'
        UPLOAD_BUTTON_SELECTOR = 'button[type="submit"]'
        SUCCESS_SELECTOR       = '.upload-success, .form-version-row, [data-test="upload-complete"]'

        await page.set_input_files(FILE_INPUT_SELECTOR, str(xlsx_path))
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
) -> FormPublishResult:
    """Thin wrapper around FormPublisher.publish_all_forms.

    Use from pipeline.py:

        from oc_form_publisher import publish_forms_to_openclinica
        result = await publish_forms_to_openclinica(
            study_url=study_url,
            edc_zip_url=edc_zip_url,
            auth_token=token,
        )
    """
    publisher = FormPublisher(auth_token=auth_token, headless=headless)
    return await publisher.publish_all_forms(study_url, edc_zip_url)
