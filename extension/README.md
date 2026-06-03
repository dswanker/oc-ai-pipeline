# OC Session Capture (Chrome Extension) — v1.0.1

Companion to oc-ai-pipeline. Captures your OpenClinica SSO cookies + localStorage from all open OpenClinica tabs so the Railway pipeline can publish XLSForm files and load UAT data on your behalf.

## Install (first time only)

1. Download `oc-session-capture.zip` from the shared Google Drive folder
2. Unzip it — you get a folder called `oc-session-capture`
3. **Save this folder somewhere permanent** (e.g. your Documents folder) — Chrome loads the extension directly from this folder, so don't delete or move it later
4. Open Chrome and go to `chrome://extensions`
5. Toggle **Developer mode** (top right)
6. Click **Load unpacked** and select the saved folder
7. The extension appears as **OC Session Capture 1.0.1** in your toolbar (pin it for easy access)

You only need to do this once. On future runs, skip straight to Use below.

## Use

1. In monday, the pipeline sets status to **Paused for Authentication** and writes a fresh link to the OC Auth Link column. Click that link.
2. The Railway page shows a one-time code — copy it.
3. Make sure you have at least one OpenClinica tab open in Chrome (any OC tab — build, design, or clinical host).
4. Click the **OC Session Capture** extension icon in your toolbar.
5. Paste the code, click **Capture & Send**.
6. You will see a green ✅ message listing how many cookies and tabs were captured.
7. Return to monday and set the AI Trigger back to **Send to AI**.

## What changed in v1.0.1

- **Multi-tab capture**: localStorage is now read from ALL open OpenClinica tabs, not just the active one. This ensures clinical host cookies (e.g. `cust1.eu.openclinica.io`) are captured alongside build app cookies — no need to be on any specific tab when capturing.

## How it works

- Reads OC cookies via `chrome.cookies.getAll({domain: "openclinica.io"})` — sweeps all subdomains
- Reads localStorage from every open `*.openclinica.io` tab via `chrome.scripting.executeScript`
- Merges all origins into Playwright's `storage_state` format
- POSTs to `https://oc-ai-pipeline-production.up.railway.app/api/session/upload` with the one-time code

## Updating the extension

When a new version is released:
1. Download the new zip from Google Drive
2. Unzip and replace the contents of your saved folder
3. Go to `chrome://extensions` → click the **reload icon** on OC Session Capture

## Permissions

- `cookies`: read OC session cookies across all openclinica.io subdomains
- `scripting`: read localStorage from all open OC tabs
- `storage`: reserved for future settings
- host_permissions for `*.openclinica.io`: scope cookie/script access to OC only
- host_permissions for the Railway URL: allow the upload POST
