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
  setStatus("info", "Reading cookies from all OpenClinica tabs...");

  try {
    // ── Cookies: sweep ALL openclinica.io domains ─────────────────────────
    const chromeCookies = await chrome.cookies.getAll({ domain: "openclinica.io" });
    setStatus("info", `Captured ${chromeCookies.length} cookies. Reading storage from all tabs...`);

    // ── localStorage: sweep ALL openclinica.io tabs, not just active one ──
    const allTabs = await chrome.tabs.query({ url: "*://*.openclinica.io/*" });
    if (allTabs.length === 0) {
      throw new Error(
        "No OpenClinica tabs found. Please open OpenClinica in this browser first, then click the extension again."
      );
    }

    const allOrigins = [];
    for (const tab of allTabs) {
      try {
        const [result] = await chrome.scripting.executeScript({
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
        if (result?.result?.localStorage?.length > 0) {
          allOrigins.push({
            origin: result.result.origin,
            localStorage: result.result.localStorage,
          });
        }
      } catch (tabErr) {
        // Non-fatal — skip tabs that block scripting (e.g. PDF pages)
        console.warn(`Skipped tab ${tab.id} (${tab.url}): ${tabErr.message}`);
      }
    }

    const totalStorageKeys = allOrigins.reduce((n, o) => n + o.localStorage.length, 0);
    setStatus("info", `Captured ${totalStorageKeys} storage keys from ${allOrigins.length} tab(s). Sending...`);

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
      origins: allOrigins,
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

    const tabUrls = allTabs.map(t => new URL(t.url).hostname).join(", ");
    setStatus(
      "success",
      `✅ Captured for ${result.email || "user"}. ${playwrightCookies.length} cookies, ` +
      `${totalStorageKeys} storage keys from ${allOrigins.length} tab(s): ${tabUrls}. ` +
      `Return to monday and re-trigger your pipeline.`
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
