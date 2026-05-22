# OC Session Capture (Chrome Extension)

Companion to oc-ai-pipeline. Captures your OpenClinica SSO cookies + localStorage so the Railway pipeline can publish XLSForm files to OC on your behalf.

## Install (sideload)

1. Open Chrome and go to `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked**
4. Select this folder

The extension icon will appear in your toolbar (you may need to pin it).

## Use

1. In monday, click the **OC Auth Link** on your pipeline row. The Railway page shows a one-time code.
2. Open a new tab and sign in to OpenClinica normally at `https://cust1.design.openclinica.io` (or whichever subdomain).
3. While on the OC tab, click the extension icon.
4. Paste the code, click **Capture & Send**.
5. Return to monday and re-trigger your pipeline.

## How it works

- Reads OC cookies via `chrome.cookies.getAll({domain: "openclinica.io"})`
- Reads OC localStorage from the active tab via `chrome.scripting.executeScript`
- Transforms both into Playwright's `storage_state` format
- POSTs to `https://oc-ai-pipeline-production.up.railway.app/api/session/upload` with the one-time code; server validates the code and derives the user's email from it

## Permissions

- `cookies`: read OC session cookies (including httpOnly, which page JS can't see)
- `scripting` + `activeTab`: read localStorage from the OC tab
- `storage`: reserved for future settings
- host_permissions for `*.openclinica.io`: scope cookie/script access to OC only
- host_permissions for the Railway URL: allow the upload POST
