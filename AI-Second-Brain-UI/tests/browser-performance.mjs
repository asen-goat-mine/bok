import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// Entirely synthetic Vault: this runner never starts Bok or reads user memory.
const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const longText = "# Large reader fixture\n\n" + Array.from({ length: 3600 }, (_, index) => `Paragraph ${index}: **memory** and [source](./card-1.md).`).join("\n\n");
const files = Array.from({ length: 320 }, (_, index) => ({
  path: `03-Knowledge/card-${index}.md`,
  text: `---\ntitle: Card ${index}\ntags: [memory]\n---\n# Card ${index}\n\n## 一句话结论\n\nReusable memory ${index}.\n\n` + "Knowledge for searching and reading. ".repeat(120),
  lastModified: 1_000_000 - index,
}));
files.push(
  { path: "02-Projects/focus.md", text: "# Fixture project\n\n## 下一步行动\n\n- Verify the interface.\n", lastModified: 2_000_000 },
  { path: "00-System/Active-Context.md", text: "---\nfocus_path: 02-Projects/focus.md\n---\n# Active Context\n", lastModified: 1 },
  { path: "00-System/Asset-Index.md", text: "# Assets\n\n[Fixture video](../04-Content/fixture.mp4)\n", lastModified: 1 },
  { path: "03-Knowledge/large.md", text: longText.slice(0, 4096), lastModified: 1, truncated: true },
);
files.forEach((file) => { file.size = Buffer.byteLength(file.path.endsWith("large.md") ? longText : file.text); file.contentHash = `fixture-${file.path}`; });
let fileRequests = 0;
let cleanupBlocked = true;
const cleanupWaiters = [];
const server = createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  const json = (value) => { response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(value)); };
  if (url.pathname === "/api/heartbeat") return json({ nativeShell: false });
  if (url.pathname === "/api/cleanup") {
    if (cleanupBlocked) { cleanupWaiters.push(() => json({ available: false })); return; }
    return json({ available: false });
  }
  if (url.pathname === "/api/vault") {
    response.setHeader("ETag", '"fixture-v1"');
    if (request.headers["if-none-match"] === '"fixture-v1"') { response.statusCode = 304; return response.end(); }
    return json({ root: "Synthetic performance fixture", files });
  }
  if (url.pathname === "/api/file") {
    fileRequests += 1;
    response.setHeader("Content-Type", url.searchParams.get("path")?.endsWith(".mp4") ? "video/mp4" : "text/markdown; charset=utf-8");
    return response.end(url.searchParams.get("path") === "03-Knowledge/large.md" ? longText : "");
  }
  const relative = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  const target = resolve(uiRoot, relative);
  if (!target.startsWith(uiRoot + "/") || !/^(?:index\.html|app\.js|atlas-physics\.js|styles\.css|assets\/[^/]+)$/u.test(relative) || !existsSync(target)) {
    response.statusCode = 404; return response.end();
  }
  response.setHeader("Content-Type", relative.endsWith(".js") ? "text/javascript" : relative.endsWith(".css") ? "text/css" : relative.endsWith(".html") ? "text/html" : "application/octet-stream");
  response.end(readFileSync(target));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const chrome = [process.env.BOUJOY_TEST_CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].find((path) => path && existsSync(path));
if (!chrome) { server.close(); throw new Error("Chrome is required; set BOUJOY_TEST_CHROME to its executable."); }
const profile = mkdtempSync(join(tmpdir(), "bok-ui-performance-"));
let browser;
let socket;
let closeBrowser;
try {
  browser = spawn(chrome, ["--headless=new", ...(process.platform === "linux" ? ["--no-sandbox", "--disable-dev-shm-usage"] : []), "--disable-background-networking", "--disable-component-update", "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const devtools = join(profile, "DevToolsActivePort");
  let browserDiagnostics = "";
  browser.stderr.on("data", (chunk) => { browserDiagnostics = (browserDiagnostics + chunk).slice(-8000); });
  browser.on("error", (error) => { browserDiagnostics += error.message; });
  const startupDeadline = Date.now() + 30_000;
  while (!existsSync(devtools) && Date.now() < startupDeadline && browser.exitCode === null && browser.signalCode === null) await delay(50);
  if (!existsSync(devtools)) throw new Error(`Chrome DevTools did not start (exit=${browser.exitCode}, signal=${browser.signalCode}): ${browserDiagnostics}`);
  const [port, path] = readFileSync(devtools, "utf8").trim().split(/\r?\n/u);
  socket = new WebSocket(`ws://127.0.0.1:${port}${path}`);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let nextId = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id); clearTimeout(entry.timer);
      if (message.error) entry.reject(new Error(message.error.message)); else entry.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timed out: ${method}`)); }, 20_000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  closeBrowser = () => socket.send(JSON.stringify({ id: ++nextId, method: "Browser.close" }));
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }, sessionId);
  await send("Page.navigate", { url: origin }, sessionId);
  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await evaluate("document.querySelector('#syncLabel')?.textContent === '本地同步中'")) break;
    await delay(25);
  }
  assert.equal(await evaluate("state.files.length"), files.length);
  assert.equal(await evaluate("isIgnoredPath('ChosenVault/.bok/versions/example/before.md')"), true);
  assert.equal(await evaluate("isIgnoredPath('ChosenVault/03-Knowledge/example.md')"), false);
  assert.equal(await evaluate("state.cleanupStatus"), null, "Reading the Vault must not wait for cleanup status.");
  cleanupBlocked = false;
  cleanupWaiters.splice(0).forEach((reply) => reply());
  const cards = await evaluate(`(() => {
    setView('library', 'library');
    const card = elements.cardGrid.firstElementChild;
    const video = elements.videoShowcase.querySelector('video');
    const initialCount = elements.cardGrid.children.length;
    selectRecord(card.dataset.path, false);
    const selectionPreserved = elements.cardGrid.firstElementChild === card;
    elements.loadMore.click();
    const paginationPreserved = elements.cardGrid.firstElementChild === card;
    state.search = 'memory'; renderCards(); renderCards();
    return { selectionPreserved, paginationPreserved, initialCount, videoPreserved: elements.videoShowcase.querySelector('video') === video, cardsAfterLoad: elements.cardGrid.children.length };
  })()`);
  assert.equal(cards.selectionPreserved, true);
  assert.equal(cards.paginationPreserved, true);
  assert.equal(cards.videoPreserved, true);
  assert.equal(cards.initialCount, 24);
  assert.equal(cards.cardsAfterLoad, 48);
  const search = await evaluate(`(() => {
    state.search = 'Reusable';
    let calls = 0; const original = searchMatch;
    searchMatch = (...args) => { calls += 1; return original(...args); };
    const first = filteredRecords('library');
    const firstPass = calls;
    const second = filteredRecords('library');
    searchMatch = original;
    return { firstPass, repeatedPass: calls - firstPass, sameResults: first === second };
  })()`);
  assert.ok(search.firstPass > 300);
  assert.equal(search.repeatedPass, 0);
  assert.equal(search.sameResults, true);

  const reader = await evaluate(`(async () => {
    const large = state.files.find(record => record.path === '03-Knowledge/large.md');
    const short = state.files.find(record => record.path === '03-Knowledge/card-1.md');
    let frames = 0; let reading = true; let parsed = 0;
    const originalBlocks = markdownBlocks;
    markdownBlocks = function*(...args) { parsed += 1; yield* originalBlocks(...args); };
    const countFrames = () => { frames += 1; if (reading) requestAnimationFrame(countFrames); };
    requestAnimationFrame(countFrames);
    const first = showReader(large);
    const dialogOpenedImmediately = elements.readerDialog.open;
    await first;
    const paragraphs = elements.markdownReader.querySelectorAll('p').length;
    await showReader(large);
    const parseCountAfterRepeat = parsed;
    const stale = showReader(large);
    await readerTask();
    await showReader(short);
    await stale;
    reading = false;
    markdownBlocks = originalBlocks;
    const newestOnly = !elements.markdownReader.textContent.includes('Paragraph 3599') && elements.readerTitle.textContent === short.title;
    const hydrated = !large.truncated;
    await readServerVault({ force: true });
    const hydrationSurvivesRefresh = !state.files.find(record => record.path === large.path).truncated;
    elements.readerDialog.close();
    return { frames, paragraphs, parseCountAfterRepeat, dialogOpenedImmediately, newestOnly, hydrated, hydrationSurvivesRefresh };
  })()`);
  assert.equal(reader.dialogOpenedImmediately, true);
  assert.equal(reader.paragraphs, 3600);
  assert.equal(reader.parseCountAfterRepeat, 1);
  assert.ok(reader.frames > 1, "Large Markdown should let the browser paint between batches.");
  assert.equal(reader.newestOnly, true);
  assert.equal(reader.hydrated, true);
  assert.equal(reader.hydrationSurvivesRefresh, true);

  const graph = await evaluate(`(async () => {
    state.reduceMotion = true;
    setView('atlas');
    await new Promise(requestAnimationFrame);
    const nodes = state.atlasNodes;
    state.atlasCamera.scale = 1.4;
    setView('library'); setView('atlas');
    await new Promise(requestAnimationFrame);
    const reusedLayout = state.atlasNodes === nodes && state.atlasCamera.scale === 1.4;
    let draws = 0; const original = drawAtlas;
    drawAtlas = (...args) => { draws += 1; return original(...args); };
    for (let index = 0; index < 100; index += 1) elements.knowledgeGraph.dispatchEvent(new PointerEvent('pointermove', {clientX:index, clientY:50}));
    const drawsBeforeFrame = draws;
    await new Promise(requestAnimationFrame);
    drawAtlas = original;
    const previousNodes = state.atlasNodes;
    state.files = state.files.map((record, index) => index === 0 ? { ...record, title: 'Updated fixture', contentHash: record.contentHash + '-updated' } : record);
    renderAll();
    await new Promise(requestAnimationFrame);
    const refreshesChangedData = state.atlasNodes !== previousNodes && state.atlasNodes.some(node => node.record.title === 'Updated fixture');
    return { reusedLayout, drawsBeforeFrame, draws, nodes: nodes.length, refreshesChangedData };
  })()`);
  assert.equal(graph.reusedLayout, true);
  assert.equal(graph.drawsBeforeFrame, 0);
  assert.equal(graph.draws, 1);
  assert.equal(graph.refreshesChangedData, true);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, fixtureFiles: files.length, cleanupNonblocking: true, cards, search, reader, graph, fileRequests }, null, 2));
} finally {
  if (browser && browser.exitCode === null && browser.signalCode === null) {
    const stopped = new Promise((resolve) => browser.once("exit", resolve));
    if (closeBrowser) {
      try { closeBrowser(); } catch { /* Fall back to terminating our child. */ }
      await Promise.race([stopped, delay(3000)]);
    }
    if (browser.exitCode === null && browser.signalCode === null) {
      browser.kill("SIGTERM");
      await Promise.race([stopped, delay(3000)]);
    }
    if (browser.exitCode === null && browser.signalCode === null) {
      browser.kill("SIGKILL");
      await Promise.race([stopped, delay(3000)]);
    }
  }
  socket?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
  // Async retries walk the directory again if Chromium finishes a profile
  // write during removal; retrying only rmdir can leave that new file behind.
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
