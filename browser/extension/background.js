/* HAVEN browser connector (EXPERIMENTAL).
 *
 * Pushes tab identity/title/url/last-active to the native host. Deliberately
 * reads nothing else: no page bodies, no cookies, no form fields, no
 * history. Incognito windows run in a split context whose events are
 * dropped here as well as at the Core's ingest boundary.
 */

const HOST = "haven.browser";

function snapshot(tab) {
  return {
    type: "tabs",
    tab_id: String(tab.id),
    browser: "chrome",
    title: tab.title || "",
    url: tab.url || "",
    last_active_at: new Date().toISOString(),
    loading: tab.status === "loading",
    incognito: Boolean(tab.incognito),
  };
}

function pushAll() {
  chrome.tabs.query({}, (tabs) => {
    const message = {
      type: "snapshot",
      tabs: tabs.filter((tab) => !tab.incognito).map(snapshot),
    };
    chrome.nativeMessaging.sendNativeMessage(HOST, message, () => {
      void chrome.runtime.lastError; // host absent: the Core stays disconnected
    });
  });
}

chrome.tabs.onCreated.addListener(pushAll);
chrome.tabs.onRemoved.addListener(pushAll);
chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
  if (changeInfo.status || changeInfo.title || changeInfo.url) pushAll();
});
chrome.tabs.onActivated.addListener(pushAll);
chrome.windows.onFocusChanged.addListener(pushAll);

pushAll();
