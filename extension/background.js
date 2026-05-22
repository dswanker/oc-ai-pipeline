// Service worker stub. Manifest V3 requires service_worker registration if declared.
// Not used in v1 — popup-driven flow handles all logic.
// Future: chrome.alarms-based periodic session refresh.
chrome.runtime.onInstalled.addListener(() => {
  console.log("OC Session Capture installed.");
});
