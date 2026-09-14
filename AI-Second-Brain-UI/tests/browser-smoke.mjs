import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const previewUrl = process.env.BOUJOY_PREVIEW_URL || "http://127.0.0.1:8765/";
const vaultResponse = await fetch(new URL("/api/vault", previewUrl));
if (!vaultResponse.ok) throw new Error(`Vault API returned ${vaultResponse.status}.`);
const vault = await vaultResponse.json();
const activeContext = vault.files.find((file) => file.path === "00-System/Active-Context.md");
const focusPath = activeContext?.text.match(/^focus_path:\s*([^\r\n]+)$/mu)?.[1]?.trim();
const focusRecord = vault.files.find((file) => file.path === focusPath);
if (!focusRecord) throw new Error(`focus_path does not resolve: ${focusPath || "missing"}`);
const focusTitle = focusRecord.text.match(/^#\s+(.+)$/mu)?.[1]?.trim() || focusRecord.path;
const focusLines = focusRecord.text.split(/\r?\n/u);
const actionSection = ["下一步行动", "下一步", "后续行动", "Next"].map((title) => {
  const start = focusLines.findIndex((line) => line.replace(/^#{1,3}\s*/u, "").trim().toLocaleLowerCase("zh-CN") === title.toLocaleLowerCase("zh-CN") && /^#{1,3}\s+/u.test(line));
  if (start < 0) return "";
  const collected = [];
  for (let index = start + 1; index < focusLines.length && !/^#{1,3}\s+/u.test(focusLines[index]); index += 1) collected.push(focusLines[index]);
  return collected.join("\n");
}).find((section) => section.trim());
const expectedAction = actionSection
  ?.match(/^\s*(?:[-*+]|\d+[.)、])\s+(.+)$/mu)?.[1]
  ?.replace(/\[([^\]]+)\]\([^)]+\)/gu, "$1")
  ?.replace(/[`*_]/g, "")
  .trim();
if (!expectedAction) throw new Error(`No actionable next step in ${focusPath}.`);
const chromeCandidates = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  process.env.PROGRAMFILES ? join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe") : "",
  process.env["PROGRAMFILES(X86)"] ? join(process.env["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe") : "",
  process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe") : "",
  process.env.PROGRAMFILES ? join(process.env.PROGRAMFILES, "Microsoft/Edge/Application/msedge.exe") : "",
  process.env["PROGRAMFILES(X86)"] ? join(process.env["PROGRAMFILES(X86)"], "Microsoft/Edge/Application/msedge.exe") : "",
].filter(Boolean);
const chromePath = chromeCandidates.find(existsSync);
if (!chromePath) throw new Error("Chrome or Edge is required for the browser smoke test.");

const profile = mkdtempSync(join(tmpdir(), "boujoy-browser-smoke-"));
let browser;
let socket;
try {
browser = spawn(chromePath, [
  "--headless=new",
  ...(process.platform === "linux" ? ["--no-sandbox", "--disable-dev-shm-usage"] : []),
  "--disable-background-networking",
  "--disable-component-update",
  "--disable-default-apps",
  "--disable-extensions",
  "--disable-gpu",
  "--disable-sync",
  "--no-default-browser-check",
  "--no-first-run",
  "--remote-debugging-port=0",
  `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: "ignore" });

const devtoolsFile = join(profile, "DevToolsActivePort");
for (let attempt = 0; attempt < 200 && !existsSync(devtoolsFile); attempt += 1) await delay(50);
if (!existsSync(devtoolsFile)) throw new Error("Chrome DevTools did not start.");
const [port, websocketPath] = readFileSync(devtoolsFile, "utf8").trim().split(/\r?\n/u);
socket = new WebSocket(`ws://127.0.0.1:${port}${websocketPath}`);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
const consoleErrors = [];
const failedResources = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
  }
  if (message.method === "Runtime.exceptionThrown") consoleErrors.push(message.params.exceptionDetails.text || "Runtime exception");
  if (message.method === "Log.entryAdded" && message.params.entry.level === "error") consoleErrors.push(message.params.entry.text);
  if (message.method === "Network.responseReceived" && message.params.response.status >= 400) failedResources.push(`${message.params.response.status} ${message.params.response.url}`);
});
const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = nextId++;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
});

const { targetId } = await send("Target.createTarget", { url: previewUrl });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Runtime.enable", {}, sessionId);
await send("Log.enable", {}, sessionId);
await send("Page.enable", {}, sessionId);
await send("Network.enable", {}, sessionId);
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);

const evaluate = async (expression) => {
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Page evaluation failed");
  return result.result.value;
};

// Rendering follows animation frames, which CI can schedule later than a
// fixed sleep. Require the observable result within a bounded deadline.
const waitForPage = async (condition, label) => {
  const deadline = Date.now() + 5000;
  do {
    if (await evaluate(condition)) return;
    await delay(25);
  } while (Date.now() < deadline);
  throw new Error(`Timed out waiting for ${label}`);
};

for (let attempt = 0; attempt < 160; attempt += 1) {
  if (await evaluate("document.readyState === 'complete' && document.querySelector('#syncLabel')?.textContent === '本地同步中'")) break;
  await delay(50);
}

const overview = await evaluate(`({
  focus: document.querySelector('#focusCard')?.innerText || '',
  actions: document.querySelector('#actionList')?.innerText || '',
  files: document.querySelector('#fileCount')?.innerText || '',
  sync: document.querySelector('#syncLabel')?.textContent || '',
  contextBackHidden: document.querySelector('#closeSelection')?.hidden ?? false,
  contextBackDisplay: getComputedStyle(document.querySelector('#closeSelection')).display,
  overflow: document.documentElement.scrollWidth > window.innerWidth,
  externalResources: performance.getEntriesByType('resource').map((item) => item.name).filter((url) => !url.startsWith(location.origin)),
})`);
if (!overview.focus.includes(focusTitle)) throw new Error(`Homepage focus is stale: ${overview.focus}`);
if (!overview.actions.includes(expectedAction)) throw new Error(`Next actions are stale: ${overview.actions}`);
if (overview.sync !== "本地同步中") throw new Error(`Unexpected sync state: ${overview.sync}`);
if (!overview.contextBackHidden || overview.contextBackDisplay !== "none") throw new Error(`Context back control leaked into the global view: ${JSON.stringify(overview)}`);
if (overview.overflow) throw new Error("Desktop layout has horizontal overflow.");
if (overview.externalResources.length) throw new Error(`External resources detected: ${overview.externalResources.join(", ")}`);

const nativeCompositor = await evaluate(`(() => {
  const native = document.documentElement.classList.contains('native-shell');
  const body = getComputedStyle(document.body);
  const main = getComputedStyle(document.querySelector('.main-panel'));
  const context = getComputedStyle(document.querySelector('.context-panel'));
  return {
    native,
    backgroundAttachment: body.backgroundAttachment,
    mainBackdrop: main.backdropFilter || main.webkitBackdropFilter || 'none',
    contextBackdrop: context.backdropFilter || context.webkitBackdropFilter || 'none',
  };
})()`);
if (nativeCompositor.native && (nativeCompositor.backgroundAttachment.split(",").some((value) => value.trim() !== "scroll") || nativeCompositor.mainBackdrop !== "none" || nativeCompositor.contextBackdrop !== "none")) {
  throw new Error(`Native compositor safeguards are inactive: ${JSON.stringify(nativeCompositor)}`);
}
if (process.env.BOUJOY_OVERVIEW_SCREENSHOT_PATH) {
  await delay(350);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync(process.env.BOUJOY_OVERVIEW_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}

const releaseStyles = await evaluate(`(() => {
  const vault = document.querySelector('.vault-status');
  const vaultButton = document.querySelector('#connectVault');
  const card = document.querySelector('.knowledge-card');
  const tabs = document.querySelector('.memory-tabs');
  const styleOf = (node) => node ? getComputedStyle(node) : null;
  const vaultStyle = styleOf(vault);
  const buttonStyle = styleOf(vaultButton);
  const cardStyle = styleOf(card);
  const tabsStyle = styleOf(tabs);
  return {
    vaultFilter: vaultStyle?.filter || '',
    vaultButtonFilter: buttonStyle?.filter || '',
    vaultButtonTextShadow: buttonStyle?.textShadow || '',
    cardFilter: cardStyle?.filter || '',
    tabsFilter: tabsStyle?.filter || '',
  };
})()`);
const blueEdge = /rgb\(40, 66, 255\)|#2842ff/i;
if (blueEdge.test(releaseStyles.cardFilter) || blueEdge.test(releaseStyles.tabsFilter)) {
  throw new Error(`Release UI still exposes blue card or tab edges: ${JSON.stringify(releaseStyles)}`);
}
if (releaseStyles.vaultButtonTextShadow !== 'none' || releaseStyles.vaultButtonFilter !== 'none') {
  throw new Error(`Vault selector still has duplicated text or glow: ${JSON.stringify(releaseStyles)}`);
}
if (process.env.BOUJOY_VAULT_SCREENSHOT_PATH) {
  await evaluate("document.querySelector('.vault-status').open = true; true");
  await delay(200);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync(process.env.BOUJOY_VAULT_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
  await evaluate("document.querySelector('.vault-status').open = false; true");
}

const moreMenu = await evaluate(`(() => {
  const details = document.querySelector('#filterMore');
  const summary = details?.querySelector('summary');
  summary?.click();
  const menu = details?.querySelector('.filter-more-menu');
  const rect = menu?.getBoundingClientRect();
  const probe = rect ? document.elementFromPoint(rect.left + Math.min(20, rect.width / 2), rect.top + Math.min(20, rect.height / 2)) : null;
  const search = document.querySelector('.search-section');
  const row = document.querySelector('.filter-row');
  const searchStyle = search ? getComputedStyle(search) : null;
  const rowStyle = row ? getComputedStyle(row) : null;
  const buttonHeights = Array.from(row?.querySelectorAll('button, summary') || []).map((item) => Math.round(item.getBoundingClientRect().height));
  return {
    open: Boolean(details?.open),
    width: Math.round(rect?.width || 0),
    height: Math.round(rect?.height || 0),
    visibleAtPoint: Boolean(probe && menu?.contains(probe)),
    rect: rect ? { left: Math.round(rect.left), top: Math.round(rect.top), right: Math.round(rect.right), bottom: Math.round(rect.bottom) } : null,
    probe: probe ? { tag: probe.tagName, id: probe.id || '', className: String(probe.className || '') } : null,
    menuStyle: menu ? { display: getComputedStyle(menu).display, visibility: getComputedStyle(menu).visibility, pointerEvents: getComputedStyle(menu).pointerEvents, zIndex: getComputedStyle(menu).zIndex } : null,
    searchStyle: searchStyle ? { position: searchStyle.position, zIndex: searchStyle.zIndex, overflow: searchStyle.overflow, isolation: searchStyle.isolation } : null,
    rowStyle: rowStyle ? { position: rowStyle.position, zIndex: rowStyle.zIndex, overflow: rowStyle.overflow } : null,
    buttonHeights,
  };
})()`);
if (!moreMenu.open || moreMenu.width < 100 || moreMenu.height < 28 || !moreMenu.visibleAtPoint) {
  throw new Error(`More menu did not visibly open: ${JSON.stringify(moreMenu)}`);
}
if (moreMenu.buttonHeights.some((height) => height < 28 || height > 44)) {
  throw new Error(`Filter controls stretched after opening More: ${JSON.stringify(moreMenu.buttonHeights)}`);
}
if (process.env.BOUJOY_MORE_SCREENSHOT_PATH) {
  await delay(200);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync(process.env.BOUJOY_MORE_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}
await evaluate("document.querySelector('#filterMore [data-scope=content]')?.click(); true");
await delay(80);
const moreSelection = await evaluate(`({
  active: document.querySelector('#filterMore [data-scope=content]')?.getAttribute('aria-pressed') || '',
  query: document.querySelector('#activeQueryText')?.textContent || '',
})`);
if (moreSelection.active !== "true" || !moreSelection.query.includes("内容")) {
  throw new Error(`More menu option did not switch scope: ${JSON.stringify(moreSelection)}`);
}
await evaluate("document.querySelector('#filterRow [data-scope=library]')?.click(); true");

// Calibrate the headless browser's frame clock: some hosts run it at 30 Hz.
const baselineFrameMs = await evaluate(`(async () => {
  const deltas = [];
  let previous;
  await new Promise(resolve => {
    const sample = now => {
      if (previous != null) deltas.push(now - previous);
      previous = now;
      if (deltas.length >= 30) resolve(); else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  return deltas.sort((a, b) => a - b)[15];
})()`);
if (!(baselineFrameMs > 0 && baselineFrameMs <= 50)) throw new Error(`Unusable browser frame clock: ${baselineFrameMs} ms`);

const frameAudit = await evaluate(`(async () => {
  const scroller = document.scrollingElement;
  const deltas = [];
  let nativeScrollingObserved = false;
  let last = 0;
  await new Promise((resolve) => {
    const sample = (now) => {
      if (last) deltas.push(now - last);
      last = now;
      nativeScrollingObserved ||= document.documentElement.classList.contains('native-scrolling');
      if (scroller) {
        const limit = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        scroller.scrollTop = limit ? (deltas.length * 11) % limit : 0;
      }
      if (deltas.length >= 90) resolve(); else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  if (scroller) scroller.scrollTop = 0;
  const sorted = [...deltas].sort((a, b) => a - b);
  return {
    averageMs: Number((deltas.reduce((sum, value) => sum + value, 0) / deltas.length).toFixed(2)),
    p95Ms: Number(sorted[Math.floor(sorted.length * 0.95)].toFixed(2)),
    maxMs: Number(Math.max(...deltas).toFixed(2)),
    frames: deltas.length,
    nativeScrollingObserved,
  };
})()`);
frameAudit.baselineMs = Number(baselineFrameMs.toFixed(2));
if (frameAudit.frames !== 90 || frameAudit.p95Ms > Math.max(25, baselineFrameMs * 1.5)) {
  throw new Error(`Scroll frame pacing is unstable: ${JSON.stringify(frameAudit)}`);
}
if (nativeCompositor.native && !frameAudit.nativeScrollingObserved) throw new Error(`Native scroll compositor mode was never activated: ${JSON.stringify(frameAudit)}`);

await evaluate("document.querySelector('[data-view=atlas]').click() || true");
await waitForPage("Number(document.querySelector('#knowledgeGraph')?.dataset.zoom) === 1", "initial atlas frame");
const atlas = await evaluate(`({
  visible: !document.querySelector('#atlasView')?.hidden,
  canvasWidth: document.querySelector('#knowledgeGraph')?.width || 0,
  stats: document.querySelector('#atlasStats')?.innerText || '',
  controls: document.querySelectorAll('.atlas-controls button').length,
  zoom: Number(document.querySelector('#knowledgeGraph')?.dataset.zoom || 0),
  overflow: document.documentElement.scrollWidth > window.innerWidth,
})`);
if (!atlas.visible || atlas.canvasWidth < 100 || !atlas.stats.includes("节点") || atlas.controls !== 3 || atlas.zoom !== 1 || atlas.overflow) throw new Error(`Knowledge atlas did not render: ${JSON.stringify(atlas)}`);
await evaluate("document.querySelector('#atlasZoomIn')?.click(); true");
await waitForPage(`Number(document.querySelector('#knowledgeGraph')?.dataset.zoom) > ${atlas.zoom}`, "atlas zoom frame");
const atlasZoomed = await evaluate("Number(document.querySelector('#knowledgeGraph')?.dataset.zoom || 0)");
if (atlasZoomed <= atlas.zoom) throw new Error(`Knowledge atlas zoom control failed: ${atlasZoomed}`);
await evaluate("document.querySelector('#atlasReset')?.click(); true");
await waitForPage("Number(document.querySelector('#knowledgeGraph')?.dataset.zoom) === 1", "atlas reset frame");
if (process.env.BOUJOY_ATLAS_SCREENSHOT_PATH) {
  await delay(350);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync(process.env.BOUJOY_ATLAS_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}
const atlasSelected = await evaluate(`(() => {
  const button = document.querySelector('#atlasNodeList [data-open]');
  const path = button?.dataset.open || '';
  button?.click();
  return { path, selected: document.querySelector('#knowledgeGraph')?.dataset.selectedPath || '' };
})()`);
if (!atlasSelected.path || atlasSelected.selected !== atlasSelected.path) throw new Error(`Knowledge atlas click had no visible selection state: ${JSON.stringify(atlasSelected)}`);
for (let attempt = 0; attempt < 20; attempt += 1) {
  if (await evaluate("Boolean(document.querySelector('#readerDialog')?.open)")) break;
  await delay(20);
}
if (!(await evaluate("Boolean(document.querySelector('#readerDialog')?.open)"))) throw new Error("Knowledge atlas selection did not open the source Markdown.");
await evaluate("document.querySelector('#readerDialog').close(); true");
await evaluate("document.querySelector('[data-view=overview]').click() || true");

{
  const quickTarget = await send("Target.createTarget", { url: new URL("/quick-note.html?mode=floating&service=smoke", previewUrl).href });
  const quickSession = await send("Target.attachToTarget", { targetId: quickTarget.targetId, flatten: true });
  await send("Runtime.enable", {}, quickSession.sessionId);
  await send("Page.enable", {}, quickSession.sessionId);
  await send("Emulation.setDeviceMetricsOverride", { width: 420, height: 560, deviceScaleFactor: 1, mobile: false }, quickSession.sessionId);
  const evaluateQuick = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, quickSession.sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Quick-note evaluation failed");
    return result.result.value;
  };
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (await evaluateQuick("document.readyState === 'complete' && Boolean(document.querySelector('#noteForm'))")) break;
    await delay(25);
  }
  const quickLayout = await evaluateQuick(`({
    title: document.querySelector('h1')?.textContent || '',
    connected: document.querySelector('#connection')?.textContent || '',
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    fieldVisible: document.querySelector('#noteValue')?.getBoundingClientRect().height > 100,
  })`);
  if (!quickLayout.title.includes("随手记") || quickLayout.overflow || !quickLayout.fieldVisible) throw new Error(`Floating quick note layout failed: ${JSON.stringify(quickLayout)}`);
  if (process.env.BOUJOY_QUICK_NOTE_SCREENSHOT_PATH) {
    await delay(250);
    const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, quickSession.sessionId);
    writeFileSync(process.env.BOUJOY_QUICK_NOTE_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
  }
  await evaluateQuick(`(() => {
    window.__quickNotePost = null;
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const url = String(input);
      if (url.includes('/api/bok/v1/quick-notes') && (init.method || 'GET') === 'POST') {
        window.__quickNotePost = { url, body: init.body || '', idempotency: init.headers?.['Idempotency-Key'] || '' };
        return new Response(JSON.stringify({ path: '07-Quick-Notes/smoke.md', status: 'saved' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return realFetch(input, init);
    };
    const value = document.querySelector('#noteValue');
    value.value = '独立小窗保存自检';
    value.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('#noteForm').requestSubmit(document.querySelector('#noteSave'));
    return true;
  })()`);
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (await evaluateQuick("Boolean(window.__quickNotePost)")) break;
    await delay(25);
  }
  const quickSaved = await evaluateQuick(`({
    post: window.__quickNotePost,
    value: document.querySelector('#noteValue')?.value || '',
    state: document.querySelector('#draftState')?.textContent || '',
  })`);
  if (!quickSaved.post?.body.includes("独立小窗保存自检") || !quickSaved.post.idempotency || quickSaved.value || !quickSaved.state.includes("已记下来")) {
    throw new Error(`Floating quick note save flow failed: ${JSON.stringify(quickSaved)}`);
  }
  await send("Target.closeTarget", { targetId: quickTarget.targetId });
}

await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
await delay(150);
const mobileOverflow = await evaluate("document.documentElement.scrollWidth > window.innerWidth");
if (mobileOverflow) throw new Error("Mobile layout has horizontal overflow.");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);

const personFixture = {
  configured: true,
  ready: true,
  claims: {
    total: 2,
    understanding: [{ id: "person-11111111111111111111111111111111", claim_type: "communication_preference", epistemic_status: "learned", statement: "回答先说结论。", source_refs: ["conversation:one"], support_count: 1, contradiction_count: 0, scope_kind: "global", scope_value: "", access_scope: ["personal-core"], version: 2, effective: true }],
    confirmed: [{ id: "person-11111111111111111111111111111111", claim_type: "communication_preference", epistemic_status: "learned", statement: "回答先说结论。", source_refs: ["conversation:one"], support_count: 1, contradiction_count: 0, scope_kind: "global", scope_value: "", access_scope: ["personal-core"], version: 2, effective: true }],
    review_required: [{ id: "person-22222222222222222222222222222222", claim_type: "identity", epistemic_status: "hypothesis", statement: "这是一条需要确认的身份判断。", source_refs: ["conversation:two", "conversation:three", "conversation:four"], support_count: 3, contradiction_count: 0, scope_kind: "global", scope_value: "", access_scope: ["personal-core"], version: 1, effective: false }],
    pending: [{ id: "person-22222222222222222222222222222222", claim_type: "identity", epistemic_status: "hypothesis", statement: "这是一条需要确认的身份判断。", source_refs: ["conversation:two", "conversation:three", "conversation:four"], support_count: 3, contradiction_count: 0, scope_kind: "global", scope_value: "", access_scope: ["personal-core"], version: 1, effective: false }],
    groups: { communication_preference: [{ id: "person-11111111111111111111111111111111", claim_type: "communication_preference", epistemic_status: "learned", statement: "回答先说结论。", source_refs: ["conversation:one"], support_count: 1, contradiction_count: 0, scope_kind: "global", scope_value: "", access_scope: ["personal-core"], version: 2, effective: true }] },
    profile: [{ key: "communication", label: "沟通方式", count: 1, statements: ["回答先说结论。"], claim_ids: ["person-11111111111111111111111111111111"] }],
  },
  observations: { counts: { accumulating: 1 }, recent: [{ id: "obs-11111111111111111111111111111111", candidate_statement: "写代码时先跑自检。", status: "accumulating", claim_type: "work_preference", occurred_at: "2026-08-23T01:00:00Z" }] },
  outcomes: { counts: { negative: 1 }, recent: [{ id: "outcome-11111111111111111111111111111111", outcome: "negative", note: "回答还是太绕", claim_ids: ["person-11111111111111111111111111111111"], created_at: "2026-08-23T01:15:00Z" }] },
  impacts: { count: 1, recent: [{ id: "impact-11111111111111111111111111111111", answer_ref: "answer-1", agent: "codex", project: "boujoy", task_summary: "检查 UI", claim_ids: ["person-11111111111111111111111111111111"], created_at: "2026-08-23T01:10:00Z" }] },
  cleanup: { count: 1, items: [{ claim_id: "person-11111111111111111111111111111111", statement: "回答先说结论。", reasons: ["negative_outcomes"], suggested_action: "review_or_expire", protected: true }] },
  timeline: [{ at: "2026-08-23T01:15:00Z", kind: "outcome", id: "outcome-11111111111111111111111111111111", label: "negative", status: "recorded" }],
  permissions: { default: "personal-core", active_count: 1, agents: [{ agent_id: "codex", status: "active", scopes: ["context:read", "conversation:observe"] }] },
};
await evaluate(`(() => {
  window.__personRequests = [];
  window.__realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = String(input);
    if (!url.includes('/api/bok/v1/person/')) return window.__realFetch(input, init);
    window.__personRequests.push({ url, method: init.method || 'GET', body: init.body || '' });
    if (url.includes('/person/dashboard')) return new Response(${JSON.stringify(JSON.stringify(personFixture))}, { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  };
  document.querySelector('[data-view="person"]').click();
  return true;
})()`);
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("!document.querySelector('#personWorkspace')?.hidden")) break;
  await delay(25);
}
const personProfile = await evaluate(`({
  visible: !document.querySelector('#personView')?.hidden && !document.querySelector('#personWorkspace')?.hidden,
  text: document.querySelector('#personView')?.innerText || '',
  tabs: document.querySelectorAll('#personTabs [role="tab"]').length,
  selected: document.querySelector('#personTabs [aria-selected="true"]')?.dataset.personTab || '',
  overflow: document.documentElement.scrollWidth > window.innerWidth,
})`);
if (!personProfile.visible || !personProfile.text.includes("回答先说结论") || !personProfile.text.includes("codex")) throw new Error(`Personal profile did not render: ${JSON.stringify(personProfile)}`);
if (personProfile.tabs !== 5 || personProfile.selected !== "profile") throw new Error("Personal tabs are incomplete.");
if (personProfile.overflow) throw new Error("Personal desktop layout has horizontal overflow.");
if (process.env.BOUJOY_SCREENSHOT_PATH) {
  await delay(600);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }, sessionId);
  writeFileSync(process.env.BOUJOY_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}

await evaluate("document.querySelector('[data-person-tab=review]').click() || true");
const reviewText = await evaluate("document.querySelector('#personReviewPanel')?.innerText || ''");
if (!reviewText.includes("需要确认的身份判断") || !reviewText.includes("确认内容") || reviewText.includes("写代码时先跑自检")) {
  throw new Error(`Personal review queue did not isolate high-risk claims: ${reviewText}`);
}
await evaluate("document.querySelector('[data-person-tab=graph]').click() || true");
await delay(100);
const personGraph = await evaluate(`({
  visible: !document.querySelector('#personGraphPanel')?.hidden,
  confirmed: document.querySelector('#personGraphList')?.innerText || '',
  stats: document.querySelector('#personGraphStats')?.innerText || '',
  canvasWidth: document.querySelector('#personGraph')?.width || 0,
})`);
if (!personGraph.visible || !personGraph.confirmed.includes("回答先说结论") || personGraph.confirmed.includes("写代码时先跑自检") || personGraph.canvasWidth < 100) throw new Error(`Personal graph mixed pending or external content: ${JSON.stringify(personGraph)}`);
if (process.env.BOUJOY_PERSON_GRAPH_SCREENSHOT_PATH) {
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, sessionId);
  writeFileSync(process.env.BOUJOY_PERSON_GRAPH_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}
await evaluate("document.querySelector('[data-person-tab=review]').click() || true");
await evaluate("document.querySelector('[data-person-action=confirm]').click() || true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("window.__personRequests.some((item) => item.url.includes('/claims/confirm'))")) break;
  await delay(25);
}
const confirmRequest = await evaluate("window.__personRequests.find((item) => item.url.includes('/claims/confirm')) || null");
if (!confirmRequest || confirmRequest.body.includes("access_scope") || confirmRequest.body.includes("all-agents")) throw new Error("Personal confirmation unexpectedly changed Agent access.");

await evaluate(`(() => {
  document.querySelector('[data-person-tab=profile]').click();
  return true;
})()`);
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("Boolean(document.querySelector('[data-claim-card=\"person-11111111111111111111111111111111\"]'))")) break;
  await delay(25);
}
const learnedAccess = await evaluate(`(() => {
  const card = document.querySelector('[data-claim-card="person-11111111111111111111111111111111"]');
  return {
    text: card?.innerText || '',
    asksForAuthorization: Boolean(card?.querySelector('[data-person-action=authorize]')),
  };
})()`);
if (learnedAccess.asksForAuthorization || !learnedAccess.text.includes("已供本机记忆上下文使用")) {
  throw new Error(`Learned low-risk memory still asks for extra authorization: ${JSON.stringify(learnedAccess)}`);
}

await evaluate("document.querySelector('[data-person-tab=evidence]').focus(); document.querySelector('[data-person-tab=evidence]').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); true");
const keyboardTab = await evaluate("document.querySelector('#personTabs [aria-selected=true]')?.dataset.personTab || ''");
if (keyboardTab !== "cleanup") throw new Error(`Personal keyboard tab navigation failed: ${keyboardTab}`);
await evaluate("document.querySelector('[data-person-tab=evidence]').click() || true");
const evidenceText = await evaluate("document.querySelector('#personEvidencePanel')?.innerText || ''");
if (!evidenceText.includes("回答还是太绕") || !evidenceText.includes("检查 UI") || !evidenceText.includes("记忆变化记录")) throw new Error("Personal evidence, impact or timeline is missing.");

await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
await delay(100);
const personMobileOverflow = await evaluate("document.documentElement.scrollWidth > window.innerWidth");
if (personMobileOverflow) throw new Error("Personal mobile layout has horizontal overflow.");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);

const memoryProposal = {
  id: "proposal-11111111111111111111111111111111",
  requires_review: true,
  review_reasons: ["important"],
  target_path: "03-Knowledge/bok-ui-memory.md",
  analysis: { memory_type: "project", summary: "完善 Bok 工作台交互", reason: "用户明确要求完成 UI 并自检。", action: "create", confidence: 0.96 },
};
const memoryFixture = {
  today: { project: { title: focusTitle, path: focusPath, next_actions: "- 完成 UI 自检\n- 检查移动端布局" }, attention: { count: 1, important_memories: [memoryProposal], captures: [] }, recent_activity: [{ action: "document_write", path: focusPath, at: "2026-08-23T02:00:00Z", version_id: "version-doc-1" }] },
  inbox: { items: [memoryProposal] },
  notes: { items: [{ path: "07-Quick-Notes/2026-08-23-ui.md", status: "inbox", preview: "检查 Bok 工作台移动端", created: "2026-08-23T02:05:00Z", content_hash: "note-hash" }] },
  activity: { items: [{ action: "backup_created", at: "2026-08-23T02:10:00Z", details: { backup_id: "backup-ui-smoke", file_count: 42 } }, { action: "document_write", path: focusPath, at: "2026-08-23T02:00:00Z", version_id: "version-doc-1" }] },
  health: { vault: "AI-Second-Brain-Lite", local_only: true, provider: { available: true, resolved_type: "ollama", model: "local-memory", endpoint: "loopback" }, index: { documents: 42, chunks: 180, scope: "default" }, personal_core: { configured: true, ready: true, name: "Bok-Personal-Core" }, personal_learning: { impacts: 3 }, agent_credentials: { count: 2 } },
  versions: { items: [{ version_id: "version-doc-1", metadata: {} }] },
  personalBackups: { configured: true, items: [{ backup_id: "personal-backup-20260823T020000.000000Z-1234abcd", created_at: "2026-08-23T02:00:00Z", file_count: 12, valid: true }] },
  search: { results: [{ path: focusPath, type: "project", updated: "2026-08-23", title: focusTitle, heading: "下一步行动", snippet: "完成 UI 自检并检查移动端布局", why: ["current_project", "semantic_match"] }], token_estimate: 180, semantic: { status: "ready" } },
};
await evaluate(`(() => {
  window.__memoryRequests = [];
  window.__memoryBaseFetch = window.fetch.bind(window);
  const fixture = ${JSON.stringify(memoryFixture)};
  const json = (value) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  window.fetch = async (input, init = {}) => {
    const url = String(input);
    const parsed = new URL(url, location.href);
    const path = parsed.pathname;
    if (!path.startsWith('/api/bok/v1/')) return window.__memoryBaseFetch(input, init);
    const method = init.method || 'GET';
    window.__memoryRequests.push({ url, path, method, body: init.body || '' });
    if (path.endsWith('/today')) return json(fixture.today);
    if (path.endsWith('/memory/inbox')) return json(fixture.inbox);
    if (path.endsWith('/quick-notes') && method === 'GET') return json(fixture.notes);
    if (path.endsWith('/activity')) return json(fixture.activity);
    if (path.endsWith('/health')) return json(fixture.health);
    if (path.endsWith('/background')) {
      fixture.health.background = { paused: false, batch_limit: 4, interval_seconds: 30, ...fixture.health.background, ...JSON.parse(init.body || '{}') };
      return json(fixture.health.background);
    }
    if (path.endsWith('/versions')) return json(fixture.versions);
    if (path.endsWith('/person/backups') && method === 'GET') return json(fixture.personalBackups);
    if (path.endsWith('/search')) return json(fixture.search);
    if (path.endsWith('/documents/read')) return json({ path: ${JSON.stringify(focusPath)}, text: '# ${focusTitle}\\n\\n## 下一步行动\\n\\n- 完成 UI 自检', content_hash: 'document-hash', important: false });
    if (path.endsWith('/quick-notes')) return json({ path: '07-Quick-Notes/smoke.md', status: 'saved' });
    if (path.endsWith('/person/backups/create')) return json({ backup_id: 'personal-backup-new', file_count: 12 });
    if (path.endsWith('/person/backups/verify')) return json({ valid: true, file_count: 12, errors: [] });
    if (path.endsWith('/person/backups/restore')) return json({ restored: 12, safety_backup: 'personal-backup-safety' });
    if (path.endsWith('/backups/create')) return json({ backup_id: 'backup-new', file_count: 42 });
    if (path.endsWith('/backups/verify')) return json({ valid: true, file_count: 42, errors: [] });
    if (path.endsWith('/backups/restore')) return json({ restored: 42, safety_backup: 'backup-safety' });
    if (path.endsWith('/documents/write')) return json({ version_id: 'version-new', path: ${JSON.stringify(focusPath)} });
    return json({ status: 'ok' });
  };
  document.querySelector('[data-view="memory"]').click();
  return true;
})()`);
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("!document.querySelector('#memoryWorkspace')?.hidden")) break;
  await delay(25);
}
const memoryOverview = await evaluate(`({
  visible: !document.querySelector('#memoryView')?.hidden && !document.querySelector('#memoryWorkspace')?.hidden,
  text: document.querySelector('#memoryView')?.innerText || '',
  tabs: document.querySelectorAll('#memoryTabs [role="tab"]').length,
  selected: document.querySelector('#memoryTabs [aria-selected="true"]')?.dataset.memoryTab || '',
  overflow: document.documentElement.scrollWidth > window.innerWidth,
})`);
if (!memoryOverview.visible || !memoryOverview.text.includes(focusTitle) || !memoryOverview.text.includes("完善 Bok 工作台交互")) throw new Error(`Bok workspace did not render: ${JSON.stringify(memoryOverview)}`);
if (memoryOverview.tabs !== 6 || memoryOverview.selected !== "today") throw new Error("Bok workspace tabs are incomplete.");
if (memoryOverview.overflow) throw new Error("Bok workspace desktop layout has horizontal overflow.");
if (process.env.BOUJOY_MEMORY_SCREENSHOT_PATH) {
  await delay(600);
  const screenshot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }, sessionId);
  writeFileSync(process.env.BOUJOY_MEMORY_SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
}

await evaluate(`(() => {
  document.querySelector('[data-memory-tab="search"]').click();
  const input = document.querySelector('#memorySearchInput');
  input.value = 'UI 自检';
  document.querySelector('#memorySearchForm').requestSubmit();
  return true;
})()`);
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("document.querySelector('#memorySearchResults')?.innerText.includes('完成 UI 自检')")) break;
  await delay(25);
}
const memorySearchText = await evaluate("document.querySelector('#memorySearchResults')?.innerText || ''");
if (!memorySearchText.includes("完成 UI 自检") || !memorySearchText.includes("语义相近")) throw new Error("Bok semantic search result is incomplete.");

const quickNoteLaunch = await evaluate(`(() => {
  window.__quickPopup = null;
  window.open = (...args) => {
    window.__quickPopup = args;
    return { focus() {} };
  };
  document.querySelector('#quickNoteLauncher').click();
  return {
    args: window.__quickPopup,
    inlineDialogOpen: Boolean(document.querySelector('#quickNoteDialog')?.open),
  };
})()`);
if (!nativeCompositor.native && (!quickNoteLaunch.args?.[0]?.includes("quick-note.html") || !quickNoteLaunch.args[0].includes("service=") || !quickNoteLaunch.args[1]?.includes(new URL(previewUrl).port) || !quickNoteLaunch.args[2]?.includes("popup=yes") || quickNoteLaunch.inlineDialogOpen)) {
  throw new Error(`Quick note did not launch as an independent window: ${JSON.stringify(quickNoteLaunch)}`);
}
if (nativeCompositor.native && (quickNoteLaunch.args !== null || quickNoteLaunch.inlineDialogOpen)) throw new Error(`Native quick note incorrectly fell back to an in-page popup: ${JSON.stringify(quickNoteLaunch)}`);

await evaluate("document.querySelector('[data-memory-tab=inbox]').click(); document.querySelector('[data-memory-action=commit-proposal]').click(); document.querySelector('#memoryActionForm').requestSubmit(document.querySelector('#memoryActionConfirm')); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/memory/commit'))")) break;
  await delay(25);
}
const commitRequest = await evaluate("window.__memoryRequests.find((item) => item.path.endsWith('/memory/commit')) || null");
if (!commitRequest?.body.includes("confirm_important")) throw new Error("Important memory confirmation was not explicit.");

await evaluate("document.querySelector('[data-memory-tab=search]').click(); document.querySelector('#memorySearchResults [data-memory-open]').click(); document.querySelector('#editCard').click(); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("document.querySelector('#documentEditDialog')?.open")) break;
  await delay(25);
}
await evaluate("document.querySelector('#documentEditValue').value += '\\n- 浏览器编辑自检'; document.querySelector('#documentEditForm').requestSubmit(document.querySelector('#documentEditSave')); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/documents/write'))")) break;
  await delay(25);
}
const documentWrite = await evaluate("window.__memoryRequests.find((item) => item.path.endsWith('/documents/write')) || null");
if (!documentWrite?.body.includes("document-hash") || !documentWrite.body.includes("浏览器编辑自检")) throw new Error("Versioned Markdown edit did not preserve its expected hash.");
await evaluate("document.querySelector('#readerDialog').close(); document.querySelector('[data-memory-tab=settings]').click(); document.querySelector('#memoryCreateBackup').click(); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/backups/create'))")) break;
  await delay(25);
}
if (!(await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/backups/create'))"))) throw new Error("Local backup action was not sent.");
await evaluate("document.querySelector('#memoryCreatePersonalBackup').click(); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/person/backups/create'))")) break;
  await delay(25);
}
if (!(await evaluate("window.__memoryRequests.some((item) => item.path.endsWith('/person/backups/create'))"))) throw new Error("Personal Core backup action was not sent.");

// Exercise actual settings controls and re-rendering against the contract fixture.
for (const [action, expected] of [
  ["pause", { paused: true, batch_limit: 4, interval_seconds: 30 }],
  ["light", { paused: true, batch_limit: 2, interval_seconds: 60 }],
  ["resume", { paused: false, batch_limit: 2, interval_seconds: 60 }],
  ["standard", { paused: false, batch_limit: 4, interval_seconds: 30 }],
]) {
  const before = await evaluate("window.__memoryRequests.filter(item => item.path.endsWith('/background')).length");
  await evaluate(`document.querySelector('[data-background-control="${action}"]').click(); true`);
  let matched = false;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    matched = await evaluate(`(() => {
      const card = document.querySelector('[data-background-control]')?.closest('article');
      return window.__memoryRequests.filter(item => item.path.endsWith('/background')).length > ${before}
        && card?.querySelector('[data-background-control="${expected.paused ? "resume" : "pause"}"]:not(:disabled)') != null
        && card.innerText.includes('最多 ${expected.batch_limit} 条，间隔 ${expected.interval_seconds} 秒');
    })()`);
    if (matched) break;
    await delay(25);
  }
  if (!matched) throw new Error(`Background settings failed after ${action}`);
}

await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
await delay(100);
const memoryMobileOverflow = await evaluate("document.documentElement.scrollWidth > window.innerWidth");
if (memoryMobileOverflow) throw new Error("Bok workspace mobile layout has horizontal overflow.");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);

for (const tab of ["today", "search", "inbox", "notes", "activity", "settings"]) {
  await evaluate(`document.querySelector('[data-memory-tab="${tab}"]').click(); true`);
  await delay(30);
  const audit = await evaluate(`(() => {
    const panel = document.querySelector('[data-memory-panel="${tab}"]');
    return {
      selected: document.querySelector('#memoryTabs [aria-selected=true]')?.dataset.memoryTab || '',
      visible: Boolean(panel && !panel.hidden),
      textLength: (panel?.innerText || '').trim().length,
      panelOverflow: Boolean(panel && panel.scrollWidth > panel.clientWidth + 2),
    };
  })()`);
  if (audit.selected !== tab || !audit.visible || audit.textLength < 2 || audit.panelOverflow) throw new Error(`Bok tab audit failed for ${tab}: ${JSON.stringify(audit)}`);
}

await evaluate("document.querySelector('[data-view=person]').click(); true");
for (let attempt = 0; attempt < 80; attempt += 1) {
  if (await evaluate("!document.querySelector('#personWorkspace')?.hidden")) break;
  await delay(25);
}
for (const tab of ["profile", "graph", "review", "evidence", "cleanup"]) {
  await evaluate(`document.querySelector('[data-person-tab="${tab}"]').click(); true`);
  await delay(tab === "graph" ? 100 : 30);
  const audit = await evaluate(`(() => {
    const panel = document.querySelector('[data-person-panel="${tab}"]');
    return {
      selected: document.querySelector('#personTabs [aria-selected=true]')?.dataset.personTab || '',
      visible: Boolean(panel && !panel.hidden),
      textLength: (panel?.innerText || '').trim().length,
      panelOverflow: Boolean(panel && panel.scrollWidth > panel.clientWidth + 2),
    };
  })()`);
  if (audit.selected !== tab || !audit.visible || audit.textLength < 2 || audit.panelOverflow) throw new Error(`Personal tab audit failed for ${tab}: ${JSON.stringify(audit)}`);
}

const pageTargets = [
  { view: "overview", scope: "library", id: "overviewView" },
  { view: "memory", scope: "library", id: "memoryView" },
  { view: "atlas", scope: "library", id: "atlasView" },
  { view: "pipeline", scope: "library", id: "pipelineView" },
  { view: "health", scope: "library", id: "healthView" },
  { view: "person", scope: "library", id: "personView" },
  ...["projects", "knowledge", "content", "prompts", "business", "skills", "all"].map((scope) => ({ view: "library", scope, id: "libraryView" })),
];
const pageAudits = [];
for (const target of pageTargets) {
  await evaluate(`document.querySelector('[data-view="${target.view}"][data-scope="${target.scope}"]')?.click(); true`);
  await delay(target.view === "atlas" ? 120 : 45);
  const audit = await evaluate(`(() => {
    const view = document.querySelector('#${target.id}');
    const rect = view?.getBoundingClientRect();
    return {
      view: document.body.dataset.view || '',
      hidden: view?.hidden ?? true,
      width: Math.round(rect?.width || 0),
      height: Math.round(rect?.height || 0),
      textLength: (view?.innerText || '').trim().length,
      viewportOverflow: document.documentElement.scrollWidth > window.innerWidth,
      activeNavCount: document.querySelectorAll('#navList .nav-item.is-active').length,
      activeNavView: document.querySelector('#navList .nav-item.is-active')?.dataset.view || '',
      activeNavFilter: getComputedStyle(document.querySelector('#navList .nav-item.is-active')).filter,
      unwantedTitleDecoration: [...document.querySelectorAll('#${target.id} .atlas-toolbar > div:first-child, #${target.id} .person-graph-copy h2')].some((node) => {
        const content = getComputedStyle(node, '::after').content;
        return content && content !== 'none' && content !== 'normal';
      }),
    };
  })()`);
  if (audit.view !== target.view || audit.hidden || audit.width < 200 || audit.height < 100 || audit.textLength < 2 || audit.viewportOverflow || audit.activeNavCount !== 1 || audit.activeNavView !== target.view || audit.activeNavFilter !== 'none' || audit.unwantedTitleDecoration) {
    throw new Error(`Desktop page audit failed for ${target.view}/${target.scope}: ${JSON.stringify(audit)}`);
  }
  pageAudits.push({ ...target, desktop: audit });
}

await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, sessionId);
for (const target of pageTargets) {
  await evaluate(`document.querySelector('[data-view="${target.view}"][data-scope="${target.scope}"]')?.click(); true`);
  await delay(target.view === "atlas" ? 120 : 35);
  const audit = await evaluate(`(() => {
    const view = document.querySelector('#${target.id}');
    const rect = view?.getBoundingClientRect();
    return {
      hidden: view?.hidden ?? true,
      width: Math.round(rect?.width || 0),
      height: Math.round(rect?.height || 0),
      viewportOverflow: document.documentElement.scrollWidth > window.innerWidth,
    };
  })()`);
  if (audit.hidden || audit.width < 200 || audit.height < 100 || audit.viewportOverflow) {
    throw new Error(`Mobile page audit failed for ${target.view}/${target.scope}: ${JSON.stringify(audit)}`);
  }
  const entry = pageAudits.find((item) => item.view === target.view && item.scope === target.scope);
  if (entry) entry.mobile = audit;
}
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);
await evaluate("document.querySelector('[data-view=overview]').click() || true");

const searchQuery = focusTitle;
await evaluate(`(() => {
  const input = document.querySelector('#searchInput');
  input.value = ${JSON.stringify(focusTitle)};
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`);
await delay(100);
const searchText = await evaluate("document.querySelector('#overviewCardGrid')?.innerText || document.querySelector('#cardGrid')?.innerText || ''");
if (!searchText.includes(searchQuery)) throw new Error(`Focus project search returned no visible result: ${searchQuery}`);

const openedSearchResult = await evaluate(`(() => {
  const cards = [...document.querySelectorAll('#overviewCardGrid [data-path], #cardGrid [data-path]')];
  const match = cards.find((card) => card.innerText.includes(${JSON.stringify(focusTitle)}));
  match?.click();
  return Boolean(match);
})()`);
if (!openedSearchResult) throw new Error(`Focus project search result could not be opened: ${searchQuery}`);
await delay(100);
const reader = await evaluate(`({
  open: Boolean(document.querySelector('#readerDialog')?.open),
  title: document.querySelector('#readerTitle')?.textContent || '',
  deleteVisible: !document.querySelector('#deleteCard')?.hidden,
  contextBackHidden: document.querySelector('#closeSelection')?.hidden ?? true,
  contextBackText: document.querySelector('#closeSelection')?.textContent?.trim() || '',
})`);
if (!reader.open || !reader.title.includes(searchQuery)) throw new Error(`Focus project reader did not open: ${searchQuery}`);
if (!reader.deleteVisible) throw new Error("Existing delete-card feature unexpectedly disappeared.");
if (reader.contextBackHidden || reader.contextBackText !== "返回") throw new Error(`Selected context back control is unclear: ${JSON.stringify(reader)}`);
if (consoleErrors.length || failedResources.length) {
  throw new Error(`Browser errors: ${consoleErrors.join(" | ")} | Failed resources: ${failedResources.join(" | ") || "unknown"}`);
}

console.log(JSON.stringify({ status: "PASS", overview, moreMenu, moreSelection, frameAudit, memoryOverview, personProfile, reader, auditedPages: pageAudits.length, auditedMemoryTabs: 6, auditedPersonTabs: 5 }, null, 2));
} finally {
  socket?.close();
  if (browser?.exitCode === null) {
    browser.kill("SIGTERM");
    await Promise.race([
      new Promise((resolve) => browser.once("exit", resolve)),
      delay(3000),
    ]);
  }
  if (browser?.exitCode === null) {
    browser.kill("SIGKILL");
    await Promise.race([
      new Promise((resolve) => browser.once("exit", resolve)),
      delay(1000),
    ]);
  }
  let cleanupError;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      cleanupError = undefined;
      break;
    } catch (error) {
      cleanupError = error;
      await delay(200 * (attempt + 1));
    }
  }
  if (cleanupError) console.warn(`Browser profile cleanup deferred: ${cleanupError.code || cleanupError.message}`);
}
