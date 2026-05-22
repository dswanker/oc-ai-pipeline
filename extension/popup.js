const RAILWAY_BASE = "https://oc-ai-pipeline-production.up.railway.app";

const sameSiteMap = {
  no_restriction: "None",
  lax: "Lax",
  strict: "Strict",
  unspecified: "Lax",
};

function setStatus(kind, msg) {
  const el = document.getElementById("status");
  el.className = "status " + kind;
  el.textContent = msg;
}

async function captureAndSend() {
  const codeInput = document.getElementById("code");
  const btn = document.getElementById("capture-btn");
  const code = codeInput.value.trim();

  if (!code) {
    setStatus("error", "Please paste your one-time code first.");
    return;
  }

  btn.disabled = true;
  setStatus("info", "Reading active tab...");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) throw new Error("No active tab found.");

    const tabUrl = new URL(tab.url);
    if (!tabUrl.hostname.endsWith("openclinica.io")) {
      throw new Error(
        `This tab is ${tabUrl.hostname}. Please open the OpenClinica tab where you signed in, then click the extension again.`
      );
    }

    setStatus("info", "Reading cookies...");
    const chromeCookies = await chrome.cookies.getAll({ domain: "openclinica.io" });

    setStatus("info", `Captured ${chromeCookies.length} cookies. Reading storage...`);
    const [storageResult] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const items = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          items.push({ name: k, value: localStorage.getItem(k) });
        }
        return { origin: window.location.origin, localStorage: items };
      },
    });

    const ocOrigin = storageResult?.result?.origin || tabUrl.origin;
    const localStorageItems = storageResult?.result?.localStorage || [];

    setStatus("info", `Captured ${localStorageItems.length} storage keys. Sending...`);

    const playwrightCookies = chromeCookies.map((c) => ({
      name: c.name,
      value: c.value,
      domain: c.hostOnly
        ? c.domain
        : (c.domain.startsWith(".") ? c.domain : "." + c.domain),
      path: c.path,
      expires: c.session ? -1 : Math.floor(c.expirationDate),
      httpOnly: c.httpOnly,
      secure: c.secure,
      sameSite: sameSiteMap[c.sameSite] || "Lax",
    }));

    const storageState = {
      cookies: playwrightCookies,
      origins: [{ origin: ocOrigin, localStorage: localStorageItems }],
    };

    const resp = await fetch(`${RAILWAY_BASE}/api/session/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: code, storage_state: storageState }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Server returned ${resp.status}: ${errText}`);
    }

    const result = await resp.json();
    if (!result.ok) throw new Error(result.error || "Server reported failure");

    setStatus(
      "success",
      `✅ Captured for ${result.email || "user"}. ${playwrightCookies.length} cookies, ${localStorageItems.length} storage keys saved. Return to monday and re-trigger your pipeline.`
    );
    codeInput.value = "";
  } catch (e) {
    setStatus("error", `❌ ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("capture-btn").addEventListener("click", captureAndSend);
document.getElementById("code").addEventListener("keydown", (e) => {
  if (e.key === "Enter") captureAndSend();
});
