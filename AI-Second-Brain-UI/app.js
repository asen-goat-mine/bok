"use strict";

const CONFIG = {
  pollInterval: 5000,
  cardPageSize: 24,
  ignoredDirectories: new Set([
    ".bok", ".cache", ".codebuddy", ".codex", ".git", ".agents", ".mypy_cache", ".nox",
    ".openai", ".pytest_cache", ".ruff_cache", ".tox", ".venv", ".workbuddy",
    "__pypackages__", "node_modules", "site-packages", "venv", "__pycache__", "99-Logs", "_dist",
  ]),
};

const CATEGORY_MAP = [
  { prefix: "AI-Second-Brain-UI/", nav: "projects", type: "项目", color: "teal", symbol: "UI" },
  { prefix: "02-Projects/", nav: "projects", type: "项目", color: "blue", symbol: "P" },
  { prefix: "03-Knowledge/", nav: "knowledge", type: "知识", color: "teal", symbol: "K" },
  { prefix: "04-Content/", nav: "content", type: "内容", color: "amber", symbol: "C" },
  { prefix: "05-Prompts/", nav: "prompts", type: "提示词", color: "violet", symbol: "Pr" },
  { prefix: "06-Business/", nav: "business", type: "商业", color: "rose", symbol: "B" },
  { prefix: "90-Archive/", nav: "archive", type: "归档", color: "gray", symbol: "A" },
  { prefix: "98-Skills/Codex-Skills/", nav: "skills", type: "Skill", color: "cyan", symbol: "S" },
];

const state = {
  rootHandle: null,
  fallbackFiles: [],
  files: [],
  rawFileCount: 0,
  fingerprint: "",
  serverMode: false,
  nativeShell: false,
  nativeFolderPicker: false,
  vaultName: "",
  serverEtag: "",
  syncing: false,
  serverSyncing: false,
  watcher: null,
  lastSync: null,
  view: "overview",
  scope: "library",
  search: "",
  visibleCardCount: CONFIG.cardPageSize,
  selectedPath: null,
  currentReaderPath: null,
  readerRequest: 0,
  atlasFrame: null,
  atlasNodes: [],
  atlasEdges: [],
  atlasGroups: [],
  atlasPointer: { x: -9999, y: -9999 },
  atlasSelectedPath: null,
  atlasCamera: { x: 0, y: 0, scale: 1 },
  atlasDrag: null,
  atlasSuppressClick: false,
  atlasSimulationAlpha: 1,
  atlasLastTime: 0,
  atlasGraphKey: "",
  atlasWidth: 0,
  atlasHeight: 0,
  personData: null,
  personTab: "profile",
  personGraphNodes: [],
  personGraphEdges: [],
  personGraphPointer: { x: -9999, y: -9999 },
  personGraphFrame: null,
  resizeFrame: null,
  searchFrame: null,
  personLoading: false,
  personActionRunning: false,
  memoryData: null,
  memoryTab: "today",
  memoryLoading: false,
  memorySearchScope: "default",
  memorySearchResults: null,
  cleanupStatus: null,
  documentEdit: null,
  reduceMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  navList: $("#navList"),
  libraryNavGroup: $("#libraryNavGroup"),
  mainEyebrow: $("#mainEyebrow"),
  mainTitle: $("#mainTitle"),
  mainSubtitle: $("#mainSubtitle"),
  syncChip: $("#syncChip"),
  syncLabel: $("#syncLabel"),
  statusDot: $("#statusDot"),
  statusMeter: $("#statusMeter"),
  fileCount: $("#fileCount"),
  vaultPath: $("#vaultPath"),
  connectVault: $("#connectVault"),
  folderFallback: $("#folderFallback"),
  manualRefresh: $("#manualRefresh"),
  healthRefresh: $("#healthRefresh"),
  cleanupNow: $("#cleanupNow"),
  searchSection: $("#searchSection"),
  searchInput: $("#searchInput"),
  searchShortcut: $("#searchShortcut"),
  filterRow: $("#filterRow"),
  filterMore: $("#filterMore"),
  activeQuery: $("#activeQuery"),
  activeQueryText: $("#activeQueryText"),
  clearFilters: $("#clearFilters"),
  overviewView: $("#overviewView"),
  memoryView: $("#memoryView"),
  libraryView: $("#libraryView"),
  pipelineView: $("#pipelineView"),
  atlasView: $("#atlasView"),
  healthView: $("#healthView"),
  personView: $("#personView"),
  focusCard: $("#focusCard"),
  pipelinePreview: $("#pipelinePreview"),
  pipelineBoard: $("#pipelineBoard"),
  pipelineSummary: $("#pipelineSummary"),
  overviewCardGrid: $("#overviewCardGrid"),
  overviewResultCount: $("#overviewResultCount"),
  viewAllCards: $("#viewAllCards"),
  videoShowcase: $("#videoShowcase"),
  videoResultCount: $("#videoResultCount"),
  videoEmpty: $("#videoEmpty"),
  cardsTitle: $("#cardsTitle"),
  resultCount: $("#resultCount"),
  cardGrid: $("#cardGrid"),
  loadMoreRow: $("#loadMoreRow"),
  loadMore: $("#loadMore"),
  emptyState: $("#emptyState"),
  atlasStage: $("#atlasStage"),
  knowledgeGraph: $("#knowledgeGraph"),
  atlasStats: $("#atlasStats"),
  atlasLegend: $("#atlasLegend"),
  atlasTooltip: $("#atlasTooltip"),
  atlasEmpty: $("#atlasEmpty"),
  atlasNodeList: $("#atlasNodeList"),
  atlasZoomOut: $("#atlasZoomOut"),
  atlasReset: $("#atlasReset"),
  atlasZoomIn: $("#atlasZoomIn"),
  healthScore: $("#healthScore"),
  healthGrid: $("#healthGrid"),
  healthActions: $("#healthActions"),
  memoryRefresh: $("#memoryRefresh"),
  memoryQuickNote: $("#memoryQuickNote"),
  memoryLoading: $("#memoryLoading"),
  memoryUnavailable: $("#memoryUnavailable"),
  memoryWorkspace: $("#memoryWorkspace"),
  memoryHero: $("#memoryHero"),
  memoryTabs: $("#memoryTabs"),
  memoryInboxBadge: $("#memoryInboxBadge"),
  memoryNotesBadge: $("#memoryNotesBadge"),
  memoryProjectCard: $("#memoryProjectCard"),
  memoryAttentionCard: $("#memoryAttentionCard"),
  memoryTodayActivity: $("#memoryTodayActivity"),
  memorySearchForm: $("#memorySearchForm"),
  memorySearchInput: $("#memorySearchInput"),
  memorySearchScope: $("#memorySearchScope"),
  memorySearchSubmit: $("#memorySearchSubmit"),
  memorySearchMeta: $("#memorySearchMeta"),
  memorySearchResults: $("#memorySearchResults"),
  memoryInboxList: $("#memoryInboxList"),
  memoryNewNote: $("#memoryNewNote"),
  memoryNoteList: $("#memoryNoteList"),
  memoryActivityList: $("#memoryActivityList"),
  memorySettingsGrid: $("#memorySettingsGrid"),
  memoryCreateBackup: $("#memoryCreateBackup"),
  memoryBackupList: $("#memoryBackupList"),
  memoryPersonalBackupSection: $("#memoryPersonalBackupSection"),
  memoryCreatePersonalBackup: $("#memoryCreatePersonalBackup"),
  memoryPersonalBackupList: $("#memoryPersonalBackupList"),
  personRefresh: $("#personRefresh"),
  personProcess: $("#personProcess"),
  personLoading: $("#personLoading"),
  personUnavailable: $("#personUnavailable"),
  personWorkspace: $("#personWorkspace"),
  personHero: $("#personHero"),
  personTabs: $("#personTabs"),
  personPendingBadge: $("#personPendingBadge"),
  personCleanupBadge: $("#personCleanupBadge"),
  personProfileSummary: $("#personProfileSummary"),
  personPermissionSummary: $("#personPermissionSummary"),
  personGroups: $("#personGroups"),
  personGraph: $("#personGraph"),
  personGraphStage: $("#personGraphStage"),
  personGraphStats: $("#personGraphStats"),
  personGraphLegend: $("#personGraphLegend"),
  personGraphTooltip: $("#personGraphTooltip"),
  personGraphEmpty: $("#personGraphEmpty"),
  personGraphList: $("#personGraphList"),
  personReviewList: $("#personReviewList"),
  personObservationList: $("#personObservationList"),
  personOutcomeList: $("#personOutcomeList"),
  personImpactList: $("#personImpactList"),
  personTimelineList: $("#personTimelineList"),
  personCleanupList: $("#personCleanupList"),
  contextPanel: $("#contextPanel"),
  contextTitle: $("#contextTitle"),
  closeSelection: $("#closeSelection"),
  actionList: $("#actionList"),
  timeline: $("#timeline"),
  tagCloud: $("#tagCloud"),
  lastSync: $("#lastSync"),
  toast: $("#toast"),
  cleanupDialog: $("#cleanupDialog"),
  cleanupSummary: $("#cleanupSummary"),
  cleanupList: $("#cleanupList"),
  confirmCleanup: $("#confirmCleanup"),
  readerDialog: $("#readerDialog"),
  readerType: $("#readerType"),
  readerTitle: $("#readerTitle"),
  readerPath: $("#readerPath"),
  markdownReader: $("#markdownReader"),
  closeReader: $("#closeReader"),
  copyCodex: $("#copyCodex"),
  editCard: $("#editCard"),
  copyPath: $("#copyPath"),
  revealFile: $("#revealFile"),
  deleteCard: $("#deleteCard"),
  deleteCardDialog: $("#deleteCardDialog"),
  deleteCardSummary: $("#deleteCardSummary"),
  confirmDeleteCard: $("#confirmDeleteCard"),
  personEditDialog: $("#personEditDialog"),
  personEditForm: $("#personEditForm"),
  personEditKicker: $("#personEditKicker"),
  personEditTitle: $("#personEditTitle"),
  personEditHint: $("#personEditHint"),
  personEditValue: $("#personEditValue"),
  personEditClaimId: $("#personEditClaimId"),
  personEditMode: $("#personEditMode"),
  personEditConfirm: $("#personEditConfirm"),
  personConfirmDialog: $("#personConfirmDialog"),
  personConfirmForm: $("#personConfirmForm"),
  personConfirmTitle: $("#personConfirmTitle"),
  personConfirmHint: $("#personConfirmHint"),
  personConfirmClaimId: $("#personConfirmClaimId"),
  personConfirmAction: $("#personConfirmAction"),
  personConfirmButton: $("#personConfirmButton"),
  quickNoteLauncher: $("#quickNoteLauncher"),
  quickNoteDialog: $("#quickNoteDialog"),
  quickNoteForm: $("#quickNoteForm"),
  quickNoteValue: $("#quickNoteValue"),
  quickNoteDraftState: $("#quickNoteDraftState"),
  quickNoteCount: $("#quickNoteCount"),
  quickNoteSave: $("#quickNoteSave"),
  documentEditDialog: $("#documentEditDialog"),
  documentEditForm: $("#documentEditForm"),
  documentEditTitle: $("#documentEditTitle"),
  documentEditPath: $("#documentEditPath"),
  documentEditValue: $("#documentEditValue"),
  documentImportantConfirm: $("#documentImportantConfirm"),
  documentEditSave: $("#documentEditSave"),
  memoryActionDialog: $("#memoryActionDialog"),
  memoryActionForm: $("#memoryActionForm"),
  memoryActionTitle: $("#memoryActionTitle"),
  memoryActionHint: $("#memoryActionHint"),
  memoryActionReason: $("#memoryActionReason"),
  memoryActionMode: $("#memoryActionMode"),
  memoryActionReference: $("#memoryActionReference"),
  memoryActionConfirm: $("#memoryActionConfirm"),
  backupRestoreDialog: $("#backupRestoreDialog"),
  backupRestoreForm: $("#backupRestoreForm"),
  backupRestoreHint: $("#backupRestoreHint"),
  backupVaultName: $("#backupVaultName"),
  backupVaultConfirm: $("#backupVaultConfirm"),
  backupRestoreId: $("#backupRestoreId"),
  backupRestoreKind: $("#backupRestoreKind"),
  backupRestoreConfirm: $("#backupRestoreConfirm"),
};

function normalizePath(path = "") {
  return String(path).replaceAll("\\", "/").replace(/^\/+/, "");
}

function resolveVaultPath(basePath, target) {
  let clean = String(target || "").trim().replace(/^<|>$/gu, "").split("#")[0].split("?")[0];
  try { clean = decodeURIComponent(clean); } catch { /* keep encoded input */ }
  if (!clean) return normalizePath(basePath);
  const stack = clean.startsWith("/") ? [] : normalizePath(basePath).split("/").slice(0, -1);
  for (const part of clean.replace(/^\/+/, "").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") stack.pop();
    else stack.push(part);
  }
  return normalizePath(stack.join("/"));
}

function isIgnoredPath(path) {
  const parts = normalizePath(path).split("/");
  return parts.slice(0, -1).some((part) => CONFIG.ignoredDirectories.has(part));
}

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function stripMarkdown(text = "") {
  return String(text)
    .replace(/^---[\s\S]*?---\s*/u, "")
    .replace(/```[\s\S]*?```/gu, " ")
    .replace(/!\[([^\]]*)\]\([^)]+\)/gu, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/gu, "$1")
    .replace(/<[^>]+>/gu, " ")
    .replace(/[#>*_`~|]/gu, " ")
    .replace(/^\s*(?:[-+] |\d+[.)、]\s+)/gmu, "")
    .replace(/\s+/gu, " ")
    .trim();
}

function readFrontmatter(text) {
  const match = String(text).match(/^---\s*\n([\s\S]*?)\n---/u);
  if (!match) return {};
  return match[1].split(/\r?\n/u).reduce((result, line) => {
    const separator = line.indexOf(":");
    if (separator < 1 || /^\s/u.test(line)) return result;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (value.startsWith("[") && value.endsWith("]")) {
      value = value.slice(1, -1).split(",").map((item) => item.trim().replace(/^["']|["']$/gu, "")).filter(Boolean);
    } else {
      value = value.replace(/^["']|["']$/gu, "");
    }
    result[key] = value;
    return result;
  }, {});
}

function termList(raw) {
  if (Array.isArray(raw)) return raw.flatMap((item) => termList(item));
  if (typeof raw !== "string") return [];
  return raw.split(/[,，、;；\n]+/u).map((item) => item.replace(/^\s*[-*+]\s*/u, "").replace(/^#/u, "").trim()).filter(Boolean);
}

function sectionLines(text, titles) {
  const titleSet = new Set(titles.map((title) => title.toLocaleLowerCase("zh-CN")));
  const lines = String(text).split(/\r?\n/u);
  for (let index = 0; index < lines.length; index += 1) {
    const heading = lines[index].match(/^#{1,3}\s*(.+?)\s*$/u);
    if (!heading || !titleSet.has(heading[1].toLocaleLowerCase("zh-CN"))) continue;
    const collected = [];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      if (/^#{1,3}\s+/u.test(lines[cursor])) break;
      collected.push(lines[cursor]);
    }
    return collected;
  }
  return [];
}

function sectionText(text, titles) {
  return stripMarkdown(sectionLines(text, titles).join("\n"));
}

function sectionList(text, titles) {
  const output = [];
  sectionLines(text, titles).forEach((line) => {
    const match = line.match(/^\s*[-*+]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$/u);
    if (match) output.push(...termList(match[1]));
  });
  return output;
}

function extractTitle(text, path, frontmatter) {
  if (frontmatter.title) return String(frontmatter.title);
  const heading = String(text).match(/^#\s+(.+)$/mu);
  if (heading) return stripMarkdown(heading[1]);
  return normalizePath(path).split("/").at(-1).replace(/\.md$/iu, "").replaceAll("-", " ");
}

function getCategory(path) {
  const normalized = normalizePath(path);
  return CATEGORY_MAP.find((category) => normalized.startsWith(category.prefix)) || {
    nav: "system", type: "系统", color: "gray", symbol: "M",
  };
}

function inferVisibility(path, frontmatter, category) {
  const explicit = String(frontmatter.visibility || "").toLocaleLowerCase("en-US");
  if (["library", "technical", "archive"].includes(explicit)) return explicit;
  const normalized = normalizePath(path);
  const name = normalized.split("/").at(-1).toLocaleLowerCase("en-US");
  if (category.nav === "archive" || normalized.startsWith("90-Archive/")) return "archive";
  if (category.nav === "skills" && name === "skill.md") return "library";
  if (normalized === "AI-Second-Brain-UI/README.md") return "library";
  const technicalName = /(?:^readme|report|qa|brief|template|storyboard|alignment|changelog|checklist|preflight|audit|manual)/iu.test(name);
  const technicalPath = /\/(?:work|input|qa|reports?|docs?|references?|scripts?|assets?|templates?|tests?)\//iu.test(`/${normalized}`);
  if (technicalName || technicalPath) return "technical";
  return "library";
}

function extractTags(text, frontmatter, category) {
  const tags = new Set();
  termList(frontmatter.tags).forEach((tag) => tags.add(tag));
  sectionList(text, ["相关标签", "标签", "Tags"]).forEach((tag) => tags.add(tag));
  for (const match of String(text).matchAll(/(?:^|\s)#([\p{L}\p{N}_-]{2,28})/gu)) {
    tags.add(match[1]);
    if (tags.size >= 8) break;
  }
  tags.add(category.type);
  return [...tags].slice(0, 8);
}

function extractAliases(text, frontmatter) {
  const aliases = new Set();
  [frontmatter.aliases, frontmatter.alias, frontmatter.keywords].forEach((raw) => termList(raw).forEach((term) => aliases.add(term)));
  sectionList(text, ["别名", "关键词", "触发词", "相关词", "Aliases"]).forEach((term) => aliases.add(term));
  return [...aliases].slice(0, 32);
}

function localDateFromTimestamp(timestamp) {
  const date = new Date(timestamp);
  const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function extractUpdated(text, frontmatter, file) {
  const declared = frontmatter.updated || frontmatter.date || frontmatter.modified;
  const declaredMatch = String(declared || "").match(/\d{4}-\d{2}-\d{2}/u);
  if (declaredMatch) return declaredMatch[0];
  const inline = String(text).match(/更新时间[：:]\s*(?:\*\*)?(\d{4}-\d{2}-\d{2})/u);
  if (inline) return inline[1];
  return localDateFromTimestamp(file.lastModified);
}

function extractActionItems(text) {
  const actionTitles = ["下一步行动", "下一步", "后续行动", "Next"];
  const lines = actionTitles.map((title) => sectionLines(text, [title])).find((section) => section.some((line) => line.trim())) || [];
  const items = [];
  lines.forEach((line) => {
    const task = line.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.+)$/u);
    const bullet = line.match(/^\s*(?:[-*+]|\d+[.)、])\s+(.+)$/u);
    if (task) items.push({ text: stripMarkdown(task[2]), done: /x/iu.test(task[1]) });
    else if (bullet) items.push({ text: stripMarkdown(bullet[1]), done: false });
  });
  if (!items.length) {
    stripMarkdown(lines.join("\n")).split(/[。；;\n]+/u).map((line) => line.trim()).filter((line) => line.length > 2).forEach((line) => items.push({ text: line, done: false }));
  }
  return items.slice(0, 8);
}

function createRecord(path, text, file, metadata = {}) {
  const normalizedPath = normalizePath(path);
  const frontmatter = readFrontmatter(text);
  const category = getCategory(normalizedPath);
  const title = extractTitle(text, normalizedPath, frontmatter);
  const body = stripMarkdown(text);
  const conclusion = sectionText(text, ["一句话结论", "结论", "摘要"]);
  return {
    path: normalizedPath,
    text,
    frontmatter,
    title,
    excerpt: (conclusion || body.replace(title, "").trim() || "Markdown 文件").slice(0, 180),
    category,
    role: String(frontmatter.role || category.nav),
    status: String(frontmatter.status || ""),
    visibility: inferVisibility(normalizedPath, frontmatter, category),
    tags: extractTags(text, frontmatter, category),
    aliases: extractAliases(text, frontmatter),
    updated: extractUpdated(text, frontmatter, file),
    lastModified: file.lastModified,
    size: file.size,
    truncated: metadata.truncated === true,
    contentHash: String(metadata.contentHash || ""),
    actions: extractActionItems(text),
  };
}

function contentFingerprint(record) {
  if (record.contentHash) return `${record.title.trim().toLocaleLowerCase("zh-CN")}\u241f${record.contentHash}`;
  const normalized = record.text.replace(/^---[\s\S]*?---\s*/u, "").replace(/\r\n?/gu, "\n").replace(/[ \t]+/gu, " ").replace(/\n{3,}/gu, "\n\n").trim().toLocaleLowerCase("zh-CN");
  return `${record.title.trim().toLocaleLowerCase("zh-CN")}\u241f${normalized}`;
}

function dedupeRecords(records) {
  const unique = new Map();
  const ordered = [...records].sort((a, b) => b.lastModified - a.lastModified || a.path.length - b.path.length);
  for (const record of ordered) {
    const key = contentFingerprint(record);
    const existing = unique.get(key);
    if (existing) {
      existing.duplicateCount += 1;
      existing.duplicatePaths.push(record.path);
    } else {
      unique.set(key, { ...record, duplicateCount: 1, duplicatePaths: [record.path] });
    }
  }
  return [...unique.values()].sort((a, b) => b.lastModified - a.lastModified);
}

async function walkDirectory(handle, prefix = "", output = []) {
  const entries = [];
  for await (const entry of handle.values()) entries.push(entry);
  entries.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  for (const entry of entries) {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.kind === "directory") {
      if (!CONFIG.ignoredDirectories.has(entry.name)) await walkDirectory(entry, path, output);
      continue;
    }
    if (!entry.name.toLocaleLowerCase("en-US").endsWith(".md")) continue;
    const file = await entry.getFile();
    output.push(createRecord(path, await file.text(), file));
  }
  return output;
}

function recordsFromFallback(fileList) {
  return Promise.all([...fileList]
    .filter((file) => file.name.toLocaleLowerCase("en-US").endsWith(".md") && !isIgnoredPath(file.webkitRelativePath || file.name))
    .map(async (file) => {
      const relative = normalizePath(file.webkitRelativePath || file.name);
      const parts = relative.split("/");
      return createRecord(parts.length > 1 ? parts.slice(1).join("/") : relative, await file.text(), file);
    }));
}

function makeFingerprint(records) {
  return records.map((record) => `${record.path}:${record.lastModified}:${record.size}:${record.contentHash || ""}`).sort().join("|");
}

let cleanupStatusRequest = null;

async function readCleanupStatus() {
  if (!/^https?:$/u.test(window.location.protocol)) return null;
  if (cleanupStatusRequest) return cleanupStatusRequest;
  cleanupStatusRequest = (async () => {
    try {
      const response = await fetch(`/api/cleanup?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Cleanup status returned ${response.status}`);
      const payload = await response.json();
      state.cleanupStatus = payload;
      return payload;
    } catch { return state.cleanupStatus; }
  })();
  try { return await cleanupStatusRequest; }
  finally { cleanupStatusRequest = null; }
}

function refreshCleanupStatus() {
  readCleanupStatus().then(() => { if (state.view === "health") renderHealth(); });
}

async function readNativeShellStatus() {
  try {
    const response = await fetch(`/api/heartbeat?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Heartbeat returned ${response.status}`);
    const payload = await response.json();
    state.nativeShell = payload.nativeShell === true;
    state.nativeFolderPicker = payload.nativeFolderPicker === true || state.nativeShell;
  } catch {
    state.nativeShell = false;
    state.nativeFolderPicker = false;
  }
  document.documentElement.classList.toggle("native-shell", state.nativeShell);
}

async function readServerVault({ force = false, announce = false } = {}) {
  if (state.serverSyncing) return;
  state.serverSyncing = true;
  try {
    const response = await fetch(`/api/vault?t=${Date.now()}`, {
      cache: "no-store",
      headers: !force && state.serverEtag ? { "If-None-Match": state.serverEtag } : {},
    });
    if (response.status === 304) {
      state.serverMode = true;
      refreshCleanupStatus();
      state.lastSync = new Date();
      elements.lastSync.textContent = formatTime(state.lastSync);
      if (state.view === "health") renderHealth();
      setSyncState("live", "本地同步中");
      return;
    }
    if (!response.ok) throw new Error(`Vault server returned ${response.status}`);
    const payload = await response.json();
    refreshCleanupStatus();
    state.serverEtag = response.headers.get("ETag") || "";
    const previousRecords = new Map(state.files.map((record) => [record.path, record]));
    const records = payload.files.map((file) => {
      const previous = previousRecords.get(normalizePath(file.path));
      if (previous && previous.lastModified === file.lastModified && previous.size === file.size
        && previous.contentHash === String(file.contentHash || "")) return previous;
      return createRecord(file.path, file.text, { lastModified: file.lastModified, size: file.size },
        { truncated: file.truncated, contentHash: file.contentHash });
    });
    const fingerprint = makeFingerprint(records);
    if (!force && fingerprint === state.fingerprint) {
      if (state.view === "health") renderHealth();
      return;
    }
    state.serverMode = true;
    state.vaultName = String(payload.root || "AI-Second-Brain-Lite");
    state.rawFileCount = records.length;
    state.files = dedupeRecords(records);
    if (state.selectedPath && !state.files.some((record) => record.path === state.selectedPath)) state.selectedPath = null;
    if (state.currentReaderPath && !state.files.some((record) => record.path === state.currentReaderPath)) {
      state.currentReaderPath = null;
      if (elements.readerDialog.open) elements.readerDialog.close();
      if (elements.deleteCardDialog.open) elements.deleteCardDialog.close();
    }
    state.fingerprint = fingerprint;
    state.lastSync = new Date();
    elements.vaultPath.dataset.serverName = payload.root || "AI-Second-Brain-Lite";
    renderAll();
    setSyncState("live", "本地同步中");
    if (announce) announceRead();
  } finally {
    state.serverSyncing = false;
  }
}

function announceRead() {
  const folded = Math.max(0, state.rawFileCount - state.files.length);
  showToast(folded ? `已读取 ${state.rawFileCount} 个文件，折叠 ${folded} 个重复副本。` : `已读取 ${state.rawFileCount} 个 Markdown 文件。`);
}

async function bootstrapLocalServer() {
  if (!/^https?:$/u.test(window.location.protocol)) return false;
  try {
    await readNativeShellStatus();
    await readServerVault({ force: true, announce: true });
    startWatcher();
    return true;
  } catch { return false; }
}

async function connectVault() {
  if (state.serverMode && state.nativeFolderPicker) {
    elements.connectVault.disabled = true;
    showToast("正在打开文件夹选择器…");
    try {
      const response = await fetch("/api/native/select-vault", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.native !== true) throw new Error("native folder picker unavailable");
      if (result.cancelled === true) {
        showToast("已取消选择，当前知识库没有变化。");
        return;
      }
      if (typeof result.url === "string" && result.url.startsWith("http://127.0.0.1:")) {
        showToast(`正在切换到 ${result.vaultName || "新知识库"}…`);
        window.location.assign(result.url);
        return;
      }
      showToast("请选择 Markdown 文件夹；选好后应用会自动重新打开。");
    } catch {
      showToast("文件夹选择器没有打开，请完全退出 Bok 后重试。");
    } finally {
      window.setTimeout(() => { elements.connectVault.disabled = false; }, 700);
    }
    return;
  }
  if (state.serverMode) return readServerVault({ force: true, announce: true });
  if (!("showDirectoryPicker" in window)) {
    elements.folderFallback.click();
    showToast("兼容模式需要重新选择文件夹才能刷新。");
    return;
  }
  try {
    state.rootHandle = await window.showDirectoryPicker({ mode: "read" });
    state.fallbackFiles = [];
    await refreshVault({ force: true, announce: true });
    startWatcher();
  } catch (error) {
    if (error.name !== "AbortError") showToast("无法读取文件夹，请检查浏览器权限。");
  }
}

async function refreshVault({ force = false, announce = false } = {}) {
  if (state.syncing) return;
  if (!state.rootHandle && !state.fallbackFiles.length) return showToast("请先选择知识库文件夹。");
  state.syncing = true;
  setSyncState("syncing", "正在检查更新");
  try {
    const records = state.rootHandle ? await walkDirectory(state.rootHandle) : await recordsFromFallback(state.fallbackFiles);
    const fingerprint = makeFingerprint(records);
    if (force || fingerprint !== state.fingerprint) {
      state.rawFileCount = records.length;
      state.files = dedupeRecords(records);
      state.fingerprint = fingerprint;
      state.lastSync = new Date();
      if (state.selectedPath && !state.files.some((record) => record.path === state.selectedPath)) state.selectedPath = null;
      renderAll();
      if (announce) announceRead();
      else if (!force) showToast("检测到文件变化，界面已更新。");
    }
    setSyncState("live", state.rootHandle ? "自动同步中" : "手动刷新");
  } catch (error) {
    console.error(error);
    setSyncState("error", "读取失败");
    showToast("读取知识库失败，请重新选择文件夹。");
  } finally { state.syncing = false; }
}

function startWatcher() {
  if (state.watcher) window.clearInterval(state.watcher);
  if (state.serverMode) state.watcher = window.setInterval(() => {
    if (document.visibilityState === "visible") readServerVault().catch(() => setSyncState("error", "同步暂停"));
  }, CONFIG.pollInterval);
  else if (state.rootHandle) state.watcher = window.setInterval(() => {
    if (document.visibilityState === "visible") refreshVault();
  }, CONFIG.pollInterval);
}

function setSyncState(mode, label) {
  elements.syncChip.dataset.state = mode;
  elements.syncChip.classList.toggle("is-live", mode === "live");
  elements.syncChip.classList.toggle("is-syncing", mode === "syncing");
  elements.syncLabel.textContent = label;
}

function scopeAllows(record, scope = state.scope) {
  if (scope === "all") return true;
  if (scope === "library") return record.visibility === "library" && record.category.nav !== "system";
  if (scope === "skills") return record.category.nav === "skills";
  if (scope === "projects") return record.category.nav === "projects" && record.visibility !== "archive";
  return record.category.nav === scope && record.visibility === "library";
}

const searchFieldCache = new WeakMap();
const filteredRecordCache = { files: null, query: "", scopes: new Map() };

function searchMatch(record, tokens, fullQuery) {
  if (!tokens.length) return { score: 0, reason: "" };
  let cached = searchFieldCache.get(record);
  if (!cached || cached.text !== record.text) {
    cached = { text: record.text, fields: [
      ["标题", record.title, 100], ["别名", record.aliases.join(" "), 70], ["标签", record.tags.join(" "), 45],
      ["路径", record.path, 25], ["摘要", record.excerpt, 15], ["正文", record.text, 5],
    ].map(([label, value, weight]) => [label, String(value).toLocaleLowerCase("zh-CN"), weight]) };
    searchFieldCache.set(record, cached);
  }
  const fields = cached.fields;
  if (!tokens.every((token) => fields.some(([, value]) => value.includes(token)))) return null;
  let score = 0;
  let reason = "正文";
  let strongest = -1;
  fields.forEach(([label, value, weight]) => {
    const hits = tokens.filter((token) => value.includes(token)).length;
    if (hits) score += hits * weight;
    if (hits && weight > strongest) { strongest = weight; reason = label; }
    if (fullQuery && value.includes(fullQuery)) score += Math.round(weight * 0.8);
  });
  return { score, reason };
}

function filteredRecords(scope = state.scope) {
  const query = state.search.trim().toLocaleLowerCase("zh-CN");
  if (filteredRecordCache.files !== state.files || filteredRecordCache.query !== query) {
    filteredRecordCache.files = state.files;
    filteredRecordCache.query = query;
    filteredRecordCache.scopes.clear();
  }
  if (filteredRecordCache.scopes.has(scope)) return filteredRecordCache.scopes.get(scope);
  const tokens = query.replace(/[^\p{L}\p{N}_+.#-]+/gu, " ").split(/\s+/u).filter(Boolean);
  const result = state.files.map((record) => {
    if (!scopeAllows(record, scope)) return null;
    const match = searchMatch(record, tokens, query);
    return match ? { record, ...match } : null;
  }).filter(Boolean).sort((a, b) => b.score - a.score || b.record.lastModified - a.record.lastModified);
  filteredRecordCache.scopes.set(scope, result);
  return result;
}

function updateScopeControls() {
  elements.filterRow.querySelectorAll("[data-scope]").forEach((button) => {
    const active = button.dataset.scope === state.scope;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const labels = { library: "精选", projects: "项目", knowledge: "知识", content: "内容", prompts: "提示词", business: "商业", skills: "Skills", all: "全部文件" };
  const conditions = [];
  if (state.scope !== "library") conditions.push(`范围：${labels[state.scope] || state.scope}`);
  if (state.search.trim()) conditions.push(`搜索：${state.search.trim()}`);
  if (elements.filterMore && ["content", "prompts", "business", "skills", "all"].includes(state.scope)) elements.filterMore.open = true;
  elements.activeQuery.hidden = conditions.length === 0;
  elements.activeQueryText.textContent = conditions.join(" · ");
}

function renderAll() {
  renderStatus();
  renderFocus();
  renderPipeline();
  renderCards();
  renderGlobalContext();
  renderHealth();
  renderView();
  if (state.view === "atlas") renderAtlas();
}

function renderStatus() {
  const raw = state.rawFileCount || state.files.length;
  elements.fileCount.textContent = `${state.files.length || raw} 条 Markdown`;
  elements.vaultPath.textContent = state.serverMode ? (elements.vaultPath.dataset.serverName || "Boujoy知识库") : (state.rootHandle?.name || "已通过兼容模式读取");
  elements.statusDot.classList.toggle("is-live", Boolean(state.files.length));
  if (elements.statusMeter) elements.statusMeter.style.width = state.files.length ? "100%" : "12%";
  elements.connectVault.textContent = "重新选择文件夹";
  elements.lastSync.textContent = formatTime(state.lastSync);
}

function currentProjectRecord() {
  const active = state.files.find((record) => record.path === "00-System/Active-Context.md");
  const dashboard = state.files.find((record) => record.path === "DASHBOARD.md");
  const explicitPath = String(active?.frontmatter?.focus_path || active?.frontmatter?.project_path || "").trim();
  if (explicitPath) {
    const exact = state.files.find((record) => record.path === normalizePath(explicitPath));
    if (exact) return exact;
  }
  const projects = state.files.filter((record) => record.category.nav === "projects" && record.visibility === "library").sort((a, b) => b.lastModified - a.lastModified);
  const explicitName = (active?.text || dashboard?.text || "").match(/\*\*项目\*\*[：:]\s*([^\n]+)/u)?.[1]?.replace(/[。.\s]+$/u, "");
  if (explicitName && !/暂无/u.test(explicitName)) {
    const named = projects.find((record) => record.title.includes(explicitName));
    if (named) return named;
  }
  return projects[0] || active || dashboard || state.files[0];
}

function renderFocus() {
  const record = currentProjectRecord();
  if (!record) return;
  const rawProjectState = stripMarkdown(String(record.frontmatter.stage || record.status || ""));
  const projectState = rawProjectState && rawProjectState.length <= 12 && !/[-_/]/u.test(rawProjectState) ? rawProjectState : "正在推进";
  elements.focusCard.innerHTML = `
    <div class="focus-project">
      <div class="focus-copy">
        <div class="focus-labels"><span>CURRENT PROJECT</span><i>${escapeHtml(projectState)}</i></div>
        <h3>${escapeHtml(record.title)}</h3>
        <div class="focus-meta"><span>${escapeHtml(record.category.type)}</span><time datetime="${escapeHtml(record.updated)}">更新 ${escapeHtml(record.updated)}</time></div>
        <span class="focus-registration" aria-hidden="true"></span>
      </div>
    </div>
    <button class="primary-button" data-open="${escapeHtml(record.path)}">打开</button>`;
  elements.focusCard.querySelector("[data-open]")?.addEventListener("click", () => selectRecord(record.path, true));
}

function parseTableRows(lines) {
  const tableStart = lines.findIndex((line, index) => line.includes("|") && /^\s*\|?\s*:?-{3,}/u.test(lines[index + 1] || ""));
  if (tableStart < 0) return [];
  const split = (line) => line.trim().replace(/^\||\|$/gu, "").split("|").map((cell) => cell.trim());
  const headers = split(lines[tableStart]);
  const rows = [];
  for (let index = tableStart + 2; index < lines.length && lines[index].includes("|"); index += 1) {
    const cells = split(lines[index]);
    if (cells.some(Boolean)) rows.push(Object.fromEntries(headers.map((header, cellIndex) => [header, cells[cellIndex] || ""])));
  }
  return rows;
}

function cellLink(cell) {
  const match = String(cell).match(/\[([^\]]+)\]\(([^)]+)\)/u);
  return match ? { label: stripMarkdown(match[1]), path: normalizePath(match[2].split("#")[0]) } : null;
}

function stageState(cell, linked) {
  const plain = stripMarkdown(cell);
  if (/待补|暂无|待开始|以官方资料核验/u.test(plain)) return "missing";
  if (/完整链路|完成|终版|发布|已有/u.test(plain) || linked) return "done";
  return plain ? "progress" : "missing";
}

function pipelineData() {
  const dashboard = state.files.find((record) => record.path === "DASHBOARD.md");
  if (!dashboard) return [];
  return parseTableRows(sectionLines(dashboard.text, ["内容生产链"])).map((row) => {
    const knowledgeCell = row["技术知识"] || "";
    const copyCell = row["口播文案"] || "";
    const animationCell = row["动画 / 成片"] || row["动画/成片"] || "";
    const knowledgeLink = cellLink(knowledgeCell);
    const copyLink = cellLink(copyCell);
    const animationLink = cellLink(animationCell);
    return {
      topic: stripMarkdown(row["主题"] || "未命名主题"),
      status: stripMarkdown(row["当前状态"] || ""),
      stages: [
        { label: "知识", text: stripMarkdown(knowledgeCell), link: knowledgeLink, state: stageState(knowledgeCell, knowledgeLink) },
        { label: "文案", text: stripMarkdown(copyCell), link: copyLink, state: stageState(copyCell, copyLink) },
        { label: "动画 / 成片", text: stripMarkdown(animationCell), link: animationLink, state: stageState(animationCell, animationLink) },
      ],
    };
  });
}

function renderPipeline() {
  const items = pipelineData();
  const completed = items.filter((item) => /完整链路/u.test(item.status)).length;
  elements.pipelineSummary.textContent = `${completed} 条完整链路 · ${items.length} 个主题`;
  elements.pipelinePreview.innerHTML = items.slice(0, 4).map((item) => {
    const done = item.stages.filter((stage) => stage.state === "done").length;
    return `<button class="pipeline-mini" data-topic="${escapeHtml(item.topic)}"><strong>${escapeHtml(item.topic)}</strong><span>${done}/3 阶段具备</span><i><b style="width:${Math.round(done / 3 * 100)}%"></b></i></button>`;
  }).join("") || `<p class="muted">Dashboard 暂无生产链数据。</p>`;
  elements.pipelineBoard.innerHTML = items.length ? items.map((item) => `
    <article class="pipeline-row">
      <header><div><h3>${escapeHtml(item.topic)}</h3><p>${escapeHtml(item.status)}</p></div><span class="pipeline-state ${/完整链路/u.test(item.status) ? "is-complete" : ""}">${/完整链路/u.test(item.status) ? "已贯通" : "推进中"}</span></header>
      <div class="pipeline-stages">${item.stages.map((stage) => {
        const content = `<span class="stage-dot"></span><div><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.text || "尚未登记")}</small></div>`;
        return stage.link ? `<button class="pipeline-stage is-${stage.state}" data-open="${escapeHtml(stage.link.path)}">${content}</button>` : `<div class="pipeline-stage is-${stage.state}">${content}</div>`;
      }).join("")}</div>
    </article>`).join("") : `<div class="pipeline-empty-state"><strong>还没有生产链路</strong><p>添加第一个真实项目后，这里会显示从知识到成片的进度。</p></div>`;
  elements.pipelineBoard.querySelectorAll("[data-open]").forEach((button) => button.addEventListener("click", () => selectRecord(button.dataset.open, true)));
  elements.pipelinePreview.querySelectorAll("[data-topic]").forEach((button) => button.addEventListener("click", () => setView("pipeline")));
}

function cardMarkup(item, index = 0) {
  const { record, reason } = item;
  const visibility = record.visibility === "technical" ? "技术文件" : record.visibility === "archive" ? "归档" : "";
  const rawType = String(record.frontmatter.type || "").trim();
  const typeLabels = { project: "项目", knowledge: "知识", "technical-knowledge": "知识", content: "内容", prompt: "提示词", business: "商业", skill: "Skill", archive: "归档" };
  const typeLabel = typeLabels[rawType.toLocaleLowerCase("en-US")] || (/\p{Script=Han}/u.test(rawType) ? rawType : record.category.type);
  return `<article class="knowledge-card${record.path === state.selectedPath ? " is-selected" : ""}" data-path="${escapeHtml(record.path)}" tabindex="0" role="button" aria-label="打开 ${escapeHtml(record.title)}" style="--delay:${Math.min(index * 28, 220)}ms">
    <div class="card-top"><span class="card-type">${escapeHtml(typeLabel)}</span>${visibility ? `<span class="visibility-chip">${visibility}</span>` : ""}</div>
    <h3>${escapeHtml(record.title)}</h3>
    ${reason ? `<span class="match-reason">命中：${escapeHtml(reason)}</span>` : ""}
    <footer><time datetime="${escapeHtml(record.updated)}">${escapeHtml(record.updated)}</time></footer>
  </article>`;
}

const boundCardContainers = new WeakSet();
const cardGridCache = new WeakMap();

function bindCards(container) {
  if (boundCardContainers.has(container)) return;
  boundCardContainers.add(container);
  container.addEventListener("click", (event) => {
    const card = event.target.closest("[data-path]");
    if (card && container.contains(card)) selectRecord(card.dataset.path, true);
  });
  container.addEventListener("keydown", (event) => {
    const card = event.target.closest("[data-path]");
    if (card && container.contains(card) && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectRecord(card.dataset.path, true);
    }
  });
}

function renderCardGrid(container, items) {
  const keys = items.map(({ record, reason }) => JSON.stringify([
    record.path, record.title, record.updated, record.frontmatter.type, record.visibility, record.category.type, reason,
  ]));
  const previous = cardGridCache.get(container) || [];
  const canAppend = previous.length <= keys.length && previous.every((key, index) => key === keys[index]);
  if (canAppend) {
    if (keys.length > previous.length) container.insertAdjacentHTML("beforeend", items.slice(previous.length).map((item, index) => cardMarkup(item, index + previous.length)).join(""));
  } else container.innerHTML = items.map(cardMarkup).join("");
  cardGridCache.set(container, keys);
  bindCards(container);
}

function updateCardSelection() {
  [elements.cardGrid, elements.overviewCardGrid].forEach((container) => {
    container.querySelectorAll("[data-path]").forEach((card) => card.classList.toggle("is-selected", card.dataset.path === state.selectedPath));
  });
}

function renderCards() {
  updateScopeControls();
  const all = filteredRecords();
  const visible = all.slice(0, state.visibleCardCount);
  const remaining = Math.max(0, all.length - visible.length);
  const scopeLabels = { library: "精选内容", projects: "项目", knowledge: "知识库", content: "内容", prompts: "提示词", business: "商业", skills: "Skills", all: "全部文件" };
  elements.cardsTitle.textContent = scopeLabels[state.scope] || "内容";
  elements.resultCount.textContent = `${all.length} 条结果 · 已展示 ${visible.length}`;
  renderCardGrid(elements.cardGrid, visible);
  elements.cardGrid.hidden = !all.length;
  elements.emptyState.hidden = Boolean(all.length);
  elements.loadMoreRow.hidden = remaining === 0;
  elements.loadMore.textContent = `再加载 ${Math.min(CONFIG.cardPageSize, remaining)} 条`;

  const overviewItems = filteredRecords("library").slice(0, 4);
  elements.overviewResultCount.textContent = `${overviewItems.length} 条`;
  renderCardGrid(elements.overviewCardGrid, overviewItems);
  updateCardSelection();
  renderVideoShowcase();
}

function isVideoPath(value = "") {
  return /\.(?:mp4|mov|m4v|webm)$/iu.test(String(value).split(/[?#]/u)[0]);
}

function videoLabel(path, sourceTitle = "") {
  const name = normalizePath(path).split("/").at(-1).replace(/\.(?:mp4|mov|m4v|webm)$/iu, "");
  const cleaned = name.replace(/[-_]+/gu, " ").replace(/\b(?:1080p60|480p15|silent|preview|final|synced|master)\b/giu, "").replace(/\s+/gu, " ").trim();
  return cleaned || sourceTitle || "库内视频";
}

function indexedVideos() {
  const index = state.files.find((record) => record.path === "00-System/Asset-Index.md");
  if (!index) return [];
  const videos = new Map();
  const add = (rawPath, sourceTitle, label = "") => {
    let target = String(rawPath || "").trim().replace(/[，。、；;]+$/u, "");
    if (!isVideoPath(target) || /^https?:\/\//iu.test(target)) return;
    target = normalizePath(target.replace(/^<|>$/gu, ""));
    if (!target.startsWith("04-Content/") && !target.startsWith("01-Inbox/")) return;
    if (!videos.has(target)) videos.set(target, { path: target, title: label || videoLabel(target, sourceTitle), sourceTitle });
  };
  for (const match of index.text.matchAll(/\[([^\]]+)\]\(([^)]+)\)/gu)) add(resolveVaultPath(index.path, match[2].split("#")[0]), index.title, stripMarkdown(match[1]));
  for (const match of index.text.matchAll(/`([^`]+\.(?:mp4|mov|m4v|webm))`/giu)) add(match[1], index.title);
  return [...videos.values()].slice(0, 18);
}

let videoShowcaseKey = "";

function renderVideoShowcase() {
  const videos = indexedVideos();
  const key = JSON.stringify(videos);
  if (key === videoShowcaseKey) return;
  videoShowcaseKey = key;
  elements.videoResultCount.textContent = `${videos.length} 个视频`;
  elements.videoEmpty.hidden = videos.length > 0;
  elements.videoShowcase.innerHTML = videos.map((video, index) => `
    <article class="video-card" style="--delay:${Math.min(index * 35, 280)}ms">
      <video controls preload="metadata" src="/api/file?path=${encodeURIComponent(video.path)}" aria-label="${escapeHtml(video.title)}"></video>
      <div class="video-card-body"><strong>${escapeHtml(video.title)}</strong><span>${escapeHtml(video.sourceTitle)}</span><small>${escapeHtml(video.path)}</small></div>
    </article>`).join("");
}

const VIEW_COPY = {
  overview: ["LOCAL-FIRST KNOWLEDGE", "你的第二大脑", "项目、知识与方法，在一个本地空间持续生长。"],
  memory: ["BOK MEMORY", "今天要继续什么", "快速记录、可靠检索、安静记忆和可撤销操作。"],
  library: ["知识检索", "找到可以复用的内容", "一套范围筛选，不让分类条件互相打架。"],
  pipeline: ["内容生产", "从知识走到成片", "看见每个主题所处阶段和缺失环节。"],
  atlas: ["关系探索", "知识全景", "用真实引用连接分散的 Markdown。"],
  health: ["只读诊断", "健康中心", "快速确认索引、环境和待整理事项。"],
  person: ["PERSONAL CORE", "关于我", "看见它记住了什么、为什么记住，以及哪条记忆影响了回答。"],
};

function setView(view, scope) {
  const viewChanged = state.view !== view;
  state.view = view;
  if (scope) state.scope = scope;
  state.visibleCardCount = CONFIG.cardPageSize;
  state.selectedPath = null;
  renderView();
  if (view === "library" || view === "overview") renderCards();
  if (view === "atlas") renderAtlas();
  if (view === "health") renderHealth();
  if (view === "memory" && (viewChanged || !state.memoryData)) loadMemoryWorkspace();
  if (view === "person" && (viewChanged || !state.personData)) loadPersonDashboard();
  renderGlobalContext();
  if (viewChanged) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
}

function renderView() {
  document.body.dataset.view = state.view;
  ["overview", "memory", "library", "pipeline", "atlas", "health", "person"].forEach((view) => {
    elements[`${view}View`].hidden = state.view !== view;
  });
  const copy = VIEW_COPY[state.view] || VIEW_COPY.overview;
  [elements.mainEyebrow.textContent, elements.mainTitle.textContent, elements.mainSubtitle.textContent] = copy;
  const special = ["memory", "pipeline", "atlas", "health", "person"].includes(state.view);
  elements.searchSection.hidden = special;
  document.body.classList.toggle("context-hidden", special);
  elements.navList.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === state.view && (state.view !== "library" || button.dataset.scope === state.scope);
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  if (elements.libraryNavGroup) elements.libraryNavGroup.open = state.view === "library";
  updateScopeControls();
  if (state.view !== "atlas" && state.atlasFrame) {
    cancelAnimationFrame(state.atlasFrame);
    state.atlasFrame = null;
  }
}

function healthMetrics() {
  const index = state.files.find((record) => record.path === "00-System/Index-Health.md");
  const cleanup = state.files.find((record) => record.path === "00-System/Cleanup-Candidates.md");
  const queue = state.files.find((record) => record.path === "00-System/Memory-Queue.md");
  const active = state.files.find((record) => record.path === "00-System/Active-Context.md");
  const numberAfter = (text, label) => Number(String(text || "").match(new RegExp(`(?:${label})\\s*\\|\\s*(\\d+)`, "iu"))?.[1] || 0);
  const missing = numberAfter(index?.text, "Missing index paths|失效索引路径");
  const paths = numberAfter(index?.text, "Checked index paths|已检查索引路径");
  const topics = numberAfter(index?.text, "Memory-Index topics|全局索引主题");
  const cleanupRows = parseTableRows(sectionLines(cleanup?.text || "", ["摘要"]));
  let cleanupItems = cleanupRows.map((row) => ({ label: stripMarkdown(row["类别"] || "待整理"), count: Number(stripMarkdown(row["数量"] || "0")) || 0 }));
  let cleanupTotal = cleanupItems.reduce((sum, item) => sum + item.count, 0);
  if (state.serverMode && state.cleanupStatus) {
    cleanupTotal = Number(state.cleanupStatus.count || 0);
    cleanupItems = [
      { label: "当前仍存在", count: cleanupTotal },
      { label: "报告中已不存在", count: Number(state.cleanupStatus.already_absent_count || 0) },
    ];
  }
  const queueCount = /当前队列[\s\S]*暂无/u.test(queue?.text || "") ? 0 : sectionLines(queue?.text || "", ["当前队列"]).filter((line) => /^\s*[-*+]\s+/u.test(line)).length;
  const runtimeReady = /已通过预检和真实渲染/u.test(active?.text || "");
  const skillsReady = /均已同步到\s*`~\/\.codex\/skills`|三份自定义 Skill 已完成迁移/u.test(active?.text || "");
  return { index, cleanup, queue, active, missing, paths, topics, cleanupItems, cleanupTotal, queueCount, runtimeReady, skillsReady };
}

function renderHealth() {
  const data = healthMetrics();
  elements.cleanupNow.hidden = !data.cleanupTotal || !state.serverMode;
  const checks = [data.missing === 0, data.queueCount === 0, data.runtimeReady, data.skillsReady];
  const passed = checks.filter(Boolean).length;
  elements.healthScore.innerHTML = `<div class="score-ring" style="--score:${passed / checks.length * 100}"><strong>${passed}/${checks.length}</strong></div><div><span class="health-label">${passed === checks.length ? "核心系统可用" : "有项目需要确认"}</span><h3>${data.missing === 0 ? "索引完整，工作流已就绪" : `${data.missing} 条索引路径失效`}</h3><p>健康中心默认只读；清理操作仅处理报告中的候选并移入系统废纸篓。</p></div>`;
  const cards = [
    { title: "索引完整性", value: data.missing === 0 ? "正常" : `${data.missing} 条失效`, detail: `${data.topics} 个主题 · ${data.paths} 条路径`, state: data.missing === 0 ? "good" : "warn", path: data.index?.path },
    { title: "记忆候选", value: `${data.queueCount} 条`, detail: data.queueCount ? "等待价值判断" : "当前队列为空", state: data.queueCount ? "warn" : "good", path: data.queue?.path },
    { title: "制作环境", value: data.runtimeReady ? "可开工" : "待确认", detail: "Manim · Remotion · Whisper · FFmpeg", state: data.runtimeReady ? "good" : "warn", path: data.active?.path },
    { title: "Skill 同步", value: data.skillsReady ? "已同步" : "待确认", detail: "Vault 源版本与 Codex 运行目录", state: data.skillsReady ? "good" : "warn", path: data.active?.path },
    { title: "清理候选", value: `${data.cleanupTotal} 项`, detail: data.cleanupItems.slice(0, 3).map((item) => `${item.label} ${item.count}`).join(" · "), state: data.cleanupTotal ? "neutral" : "good", path: data.cleanup?.path },
  ];
  elements.healthGrid.innerHTML = cards.map((card) => `<button class="health-card is-${card.state}" ${card.path ? `data-open="${escapeHtml(card.path)}"` : "disabled"}><span class="health-dot"></span><div><span>${escapeHtml(card.title)}</span><strong>${escapeHtml(card.value)}</strong><p>${escapeHtml(card.detail)}</p></div><span class="health-arrow">→</span></button>`).join("");
  const activeActions = (data.active?.actions || []).filter((item) => !/备份/u.test(item.text)).slice(0, 4);
  const cleanupAction = data.cleanupTotal ? { text: `查看 ${data.cleanupTotal} 项清理候选，确认后再处理`, done: false, path: data.cleanup?.path } : null;
  const actions = [...activeActions, ...(cleanupAction ? [cleanupAction] : [])];
  elements.healthActions.innerHTML = actions.map((item) => `<button class="health-action" ${item.path ? `data-open="${escapeHtml(item.path)}"` : ""}><span class="task-box ${item.done ? "is-done" : ""}">${item.done ? "✓" : ""}</span><strong>${escapeHtml(item.text)}</strong><span>→</span></button>`).join("") || `<p class="muted">目前没有需要处理的系统事项。</p>`;
  [...elements.healthGrid.querySelectorAll("[data-open]"), ...elements.healthActions.querySelectorAll("[data-open]")].forEach((button) => button.addEventListener("click", () => selectRecord(button.dataset.open, true)));
}

async function bokRequest(path, { method = "GET", body = null, idempotency = "" } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== null) headers["Content-Type"] = "application/json";
  if (idempotency) headers["Idempotency-Key"] = idempotency;
  const response = await fetch(`/api/bok/v1/${path}`, {
    method,
    headers,
    body: body === null ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || payload.error || `Bok 请求失败（${response.status}）`);
    error.code = payload.error?.code || "bok_request_failed";
    error.details = payload.error?.details || {};
    error.status = response.status;
    throw error;
  }
  return payload;
}

const MEMORY_ACTIVITY_LABELS = {
  backup_created: "创建了本地备份",
  backup_restored: "恢复了本地备份",
  quick_note_create: "保存了一条随手记",
  quick_note_archive: "归档了一条随手记",
  quick_note_promote: "把随手记整理为长期记忆",
  memory_create: "创建了一张长期记忆卡",
  memory_update: "更新了一张长期记忆卡",
  document_write: "保存了 Markdown 新版本",
  rollback: "撤销了一次修改",
  trash: "把文件移到可恢复废纸篓",
  move: "移动了 Markdown 文件",
  web_clip_create: "保存了网页摘录",
  markdown_import: "导入了 Markdown",
};

const MEMORY_WHY_LABELS = {
  all_terms: "完整命中问题",
  term_match: "关键词相关",
  title_match: "标题相关",
  tag_match: "标签相关",
  path_match: "路径相关",
  current_project: "当前项目优先",
  semantic_match: "语义相近",
};

const MEMORY_REVIEW_LABELS = {
  important: "重要内容",
  sensitive: "可能含敏感信息",
  low_confidence: "判断置信度较低",
  conflict: "与现有内容有冲突",
  important_target: "将修改重要旧记忆",
};

function memoryListItems(value, limit = 5) {
  return String(value || "").split(/\r?\n/u)
    .map((line) => line.replace(/^\s*(?:[-*+]|\d+[.)、])\s+/u, "").replace(/\[([^\]]+)\]\([^)]+\)/gu, "$1").replace(/[`*_]/gu, "").trim())
    .filter(Boolean).slice(0, limit);
}

function memoryActivityText(item) {
  return MEMORY_ACTIVITY_LABELS[item.action] || String(item.action || "记录了一次操作").replaceAll("_", " ");
}

function memoryActivityMarkup(items, { compact = false } = {}) {
  if (!items.length) return `<div class="person-empty"><strong>还没有操作记录</strong><p>随手记、记忆提交和 Markdown 修改会出现在这里。</p></div>`;
  const versions = new Map((state.memoryData?.versions?.items || []).map((item) => [item.version_id, item]));
  return items.map((item) => {
    const version = versions.get(item.version_id);
    const proposalId = version?.metadata?.proposal_id || "";
    const undoable = item.version_id && !["rollback", "trash", "backup_restore", "move_create"].includes(item.action);
    const undo = undoable ? `<button class="person-action quiet" data-memory-action="${proposalId ? "rollback-proposal" : "rollback-document"}" data-reference="${escapeHtml(proposalId || item.version_id)}">撤销</button>` : "";
    return `<article class="memory-activity-item${compact ? " is-compact" : ""}"><span class="memory-activity-dot"></span><div><strong>${escapeHtml(memoryActivityText(item))}</strong><p>${escapeHtml(item.path || item.details?.backup_id || "Bok 本机运行状态")}</p><time>${escapeHtml(personDate(item.at))}</time></div>${undo}</article>`;
  }).join("");
}

async function loadMemoryWorkspace({ announce = false } = {}) {
  if (state.memoryLoading) return;
  state.memoryLoading = true;
  elements.memoryLoading.hidden = false;
  elements.memoryUnavailable.hidden = true;
  elements.memoryWorkspace.hidden = true;
  try {
    if (!state.serverMode) throw new Error("Bok 工作台需要通过本地 App 或预览服务打开；纯文件夹模式仍保持只读。");
    const [today, inbox, notes, activity, health, versions, backups, personalBackups] = await Promise.all([
      bokRequest("today"),
      bokRequest("memory/inbox?status=pending&limit=100"),
      bokRequest("quick-notes?limit=100"),
      bokRequest("activity?limit=100"),
      bokRequest("health"),
      bokRequest("versions?limit=100"),
      bokRequest("backups?limit=50"),
      bokRequest("person/backups?limit=50"),
    ]);
    state.memoryData = { today, inbox, notes, activity, health, versions, backups, personalBackups };
    elements.memoryWorkspace.hidden = false;
    renderMemoryWorkspace();
    if (announce) showToast("Bok 工作台已刷新。");
  } catch (error) {
    state.memoryData = null;
    elements.memoryUnavailable.querySelector("p").textContent = error.message;
    elements.memoryUnavailable.hidden = false;
  } finally {
    state.memoryLoading = false;
    elements.memoryLoading.hidden = true;
  }
}

function renderMemoryWorkspace() {
  const data = state.memoryData;
  if (!data) return;
  const pending = data.inbox?.items || [];
  const notes = data.notes?.items || [];
  const attention = data.today?.attention || {};
  const provider = data.health?.provider || {};
  const project = data.today?.project;
  elements.memoryInboxBadge.textContent = pending.length;
  elements.memoryNotesBadge.textContent = notes.filter((item) => item.status === "inbox").length;
  elements.memoryHero.innerHTML = `<div><span class="section-kicker">QUIET MEMORY</span><h2>${project ? `继续：${escapeHtml(project.title)}` : "今天还没有聚焦项目"}</h2><p>${attention.count ? `${attention.count} 项受保护候选已在后台保留，不会反复催你确认。` : "没有需要打断你的记忆事项。"}</p></div><div class="memory-hero-stats"><div><strong>${pending.length}</strong><span>后台候选</span></div><div><strong>${notes.filter((item) => item.status === "inbox").length}</strong><span>待整理随手记</span></div><div><strong>${data.health?.local_only ? "本地" : "授权"}</strong><span>模型边界</span></div><div><strong>${provider.available ? "在线" : "排队"}</strong><span>记忆模型</span></div></div>`;

  const nextActions = memoryListItems(project?.next_actions, 4);
  elements.memoryProjectCard.innerHTML = project ? `<div class="memory-card-heading"><div><span class="section-kicker">CURRENT PROJECT</span><h3>${escapeHtml(project.title)}</h3><p>${escapeHtml(project.path)}</p></div><button class="person-action" data-memory-open="${escapeHtml(project.path)}">打开项目</button></div><div class="memory-next-list">${nextActions.map((item, index) => `<div><span>${index + 1}</span><p>${escapeHtml(item)}</p></div>`).join("") || "<p class=\"muted\">项目卡暂时没有下一步行动。</p>"}</div>` : `<div class="person-empty"><strong>还没有当前项目</strong><p>在 Active-Context 中设置 focus_path 后，这里会自动续接。</p></div>`;
  const important = attention.important_memories || [];
  const captures = attention.captures || [];
  elements.memoryAttentionCard.innerHTML = `<div class="memory-card-heading"><div><span class="section-kicker">PROTECTED QUEUE</span><h3>${attention.count ? `${attention.count} 项安静保留` : "后台队列为空"}</h3><p>普通习惯会自动形成理解；只有重要决策、冲突或敏感内容留作按需查看。</p></div></div><div class="memory-attention-list">${important.slice(0, 3).map((item) => `<button data-memory-tab-go="inbox"><strong>${escapeHtml(item.analysis?.summary || "受保护记忆候选")}</strong><span>按需查看 →</span></button>`).join("")}${captures.slice(0, 3).map((item) => `<div><strong>${item.status === "waiting_for_model" ? "等待本地模型" : "后台处理需要关注"}</strong><span>内容已经安全排队，不会自动发到云端</span></div>`).join("") || (!important.length ? `<p class="muted">没有需要你处理的事项。</p>` : "")}</div>`;
  elements.memoryTodayActivity.innerHTML = memoryActivityMarkup((data.today?.recent_activity || []).slice(0, 6), { compact: true });

  elements.memoryInboxList.innerHTML = pending.map((item) => {
    const analysis = item.analysis || {};
    const reasons = (item.review_reasons || []).map((reason) => MEMORY_REVIEW_LABELS[reason] || reason);
    return `<article class="memory-inbox-card"><div class="person-memory-meta"><span>${escapeHtml(analysis.memory_type || "memory")}</span><span class="person-status">${item.requires_review ? "受保护候选" : "后台候选"}</span></div><h3>${escapeHtml(analysis.summary || analysis.title || "未命名记忆")}</h3><p>${escapeHtml(analysis.reason || "Bok 根据新内容形成了这条候选。")}</p><div class="person-memory-chips"><span>${escapeHtml(analysis.action || "create")}</span><span>置信度 ${Math.round(Number(analysis.confidence || 0) * 100)}%</span><span>${escapeHtml(item.target_path || "待定路径")}</span></div>${reasons.length ? `<div class="memory-review-reasons">${reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : ""}<div class="person-memory-actions"><button class="person-action primary" data-memory-action="commit-proposal" data-reference="${escapeHtml(item.id)}">采用结论</button><button class="person-action danger" data-memory-action="reject-proposal" data-reference="${escapeHtml(item.id)}">忽略候选</button></div></article>`;
  }).join("") || `<div class="person-empty"><strong>记忆收件箱是空的</strong><p>普通高置信内容会安静保存并可撤销；重要或冲突内容才来到这里。</p></div>`;

  elements.memoryNoteList.innerHTML = notes.map((item) => `<article class="memory-note-card"><div><span class="person-status">${escapeHtml(item.status === "promoted" ? "已整理" : item.status === "archived" ? "已归档" : "待整理")}</span><time>${escapeHtml(personDate(item.created))}</time></div><p>${escapeHtml(item.preview || "空白随手记")}</p><small>${escapeHtml(item.path)}</small><div class="person-memory-actions"><button class="person-action" data-memory-open="${escapeHtml(item.path)}">打开</button>${item.status === "inbox" ? `<button class="person-action primary" data-memory-action="promote-note" data-path="${escapeHtml(item.path)}">整理为记忆</button><button class="person-action quiet" data-memory-action="archive-note" data-path="${escapeHtml(item.path)}" data-hash="${escapeHtml(item.content_hash)}">归档</button>` : ""}</div></article>`).join("") || `<div class="person-empty"><strong>还没有随手记</strong><p>按 Command/Ctrl + Shift + N，直接开始输入。</p></div>`;
  elements.memoryActivityList.innerHTML = memoryActivityMarkup(data.activity?.items || []);
  renderMemorySettings();
  renderMemoryTab();
}

function renderMemorySettings() {
  const data = state.memoryData;
  const health = data?.health || {};
  const provider = health.provider || {};
  const index = health.index || {};
  const personal = health.personal_core || {};
  const learning = health.personal_learning || {};
  const cards = [
    { label: "数据边界", value: health.local_only ? "Local Only 已开启" : "允许逐次云端授权", detail: health.local_only ? "非 loopback 请求会在网络层被拒绝" : "每次云端调用仍需明确授权", good: health.local_only },
    { label: "记忆模型", value: provider.available ? `${provider.resolved_type || "provider"} · ${provider.model || "已就绪"}` : "当前离线，内容安全排队", detail: provider.endpoint === "loopback" ? "只连接本机模型" : (provider.endpoint || "未配置模型"), good: provider.available },
    { label: "检索索引", value: `${index.documents || index.document_count || 0} 个文档`, detail: `${index.chunks || index.chunk_count || 0} 个段落块 · ${health.index?.scope || "默认范围"}`, good: true },
    { label: "Personal Core", value: personal.configured ? (personal.ready ? "已启用" : "需要检查") : "尚未启用", detail: personal.configured ? `${learning.impacts || 0} 次回答影响记录` : "不会在共享知识库偷写个人画像", good: personal.ready },
    { label: "Agent 权限", value: `${health.agent_credentials?.count || 0} 个本机凭证`, detail: "Token 只保存哈希，可单独撤销", good: true },
    { label: "Quiet Mode", value: "安静自动", detail: "普通内容自动保存可撤销，重要内容进入收件箱", good: true },
  ];
  const nativeAgentCard = state.nativeShell ? `<article class="memory-setting-card is-good"><span>Agent 衔接</span><strong>一键连接 Codex</strong><p>连接后请新建一个 Codex 任务；每个原生用户回合会静默进入 Bok 观察管线。</p><button class="person-action" type="button" data-connect-codex>连接 Codex</button></article>` : "";
  const background = health.background || {};
  const backgroundCard = `<article class="memory-setting-card"><span>后台整理</span><strong>${background.paused ? "已暂停，内容继续安全排队" : "小批处理，降低后台占用"}</strong><p>暂停在当前批次完成后生效，内容继续安全排队。</p><div class="person-memory-actions"><button class="person-action" data-background-control="${background.paused ? "resume" : "pause"}">${background.paused ? "恢复后台整理" : "暂停后台整理"}</button><button class="person-action" data-background-control="light">轻量模式</button><button class="person-action" data-background-control="standard">标准模式</button></div><small>每个队列每轮最多 ${Number(background.batch_limit || 4)} 条，间隔 ${Number(background.interval_seconds || 30)} 秒</small></article>`;
  elements.memorySettingsGrid.innerHTML = cards.map((item) => `<article class="memory-setting-card${item.good ? " is-good" : ""}"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong><p>${escapeHtml(item.detail)}</p></article>`).join("") + backgroundCard + nativeAgentCard;
  const backups = data.backups?.items || [];
  elements.memoryBackupList.innerHTML = backups.map((item) => `<article class="memory-backup-card"><div><strong>${escapeHtml(item.backup_id)}</strong><span>${escapeHtml(personDate(item.created_at))} · ${Number(item.file_count || 0)} 个 Markdown · ${item.valid ? "校验正常" : "需要检查"}</span></div><div><button class="person-action" data-memory-action="verify-backup" data-reference="${escapeHtml(item.backup_id)}">校验</button><button class="person-action danger" data-memory-action="restore-backup" data-reference="${escapeHtml(item.backup_id)}">恢复</button></div></article>`).join("") || `<p class="muted">还没有本地备份。</p>`;
  const personalBackups = data.personalBackups?.items || [];
  elements.memoryPersonalBackupSection.hidden = data.personalBackups?.configured !== true;
  elements.memoryPersonalBackupList.innerHTML = personalBackups.map((item) => `<article class="memory-backup-card"><div><strong>${escapeHtml(item.backup_id)}</strong><span>${escapeHtml(personDate(item.created_at))} · ${Number(item.file_count || 0)} 个 Markdown · ${item.valid ? "校验正常" : "需要检查"}</span></div><div><button class="person-action" data-memory-action="verify-personal-backup" data-reference="${escapeHtml(item.backup_id)}">校验</button><button class="person-action danger" data-memory-action="restore-personal-backup" data-reference="${escapeHtml(item.backup_id)}">恢复</button></div></article>`).join("") || `<p class="muted">还没有私人记忆备份。</p>`;
}

function renderMemoryTab() {
  elements.memoryTabs.querySelectorAll("[data-memory-tab]").forEach((button) => {
    const active = button.dataset.memoryTab === state.memoryTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  elements.memoryWorkspace.querySelectorAll("[data-memory-panel]").forEach((panel) => { panel.hidden = panel.dataset.memoryPanel !== state.memoryTab; });
  if (state.memoryTab === "search") window.setTimeout(() => elements.memorySearchInput.focus(), 0);
}

function setMemoryTab(tab) {
  state.memoryTab = tab;
  renderMemoryTab();
}

async function runMemorySearch() {
  const query = elements.memorySearchInput.value.trim();
  if (!query) return showToast("先输入要找的内容。");
  elements.memorySearchSubmit.disabled = true;
  elements.memorySearchSubmit.textContent = "搜索中…";
  elements.memorySearchMeta.textContent = state.memorySearchScope === "all" ? "正在搜索完整知识库…" : "正在搜索日常高频范围…";
  try {
    const result = await bokRequest("search", { method: "POST", body: { query, limit: 12, token_budget: 2500, scope: state.memorySearchScope, semantic: true } });
    state.memorySearchResults = result;
    const semantic = result.semantic?.status === "ready" ? "本地语义召回已启用" : result.semantic?.status === "disabled" ? "当前使用关键词与结构检索" : "语义能力已安全降级";
    elements.memorySearchMeta.textContent = `${result.results?.length || 0} 条结果 · ${result.token_estimate || 0} 估算 Token · ${semantic}`;
    elements.memorySearchResults.innerHTML = (result.results || []).map((item, index) => `<article class="memory-search-card" data-memory-open="${escapeHtml(item.path)}"><div><span>[S${index + 1}] ${escapeHtml(item.type || "note")}</span><time>${escapeHtml(item.updated || "")}</time></div><h3>${escapeHtml(item.title)}</h3><strong>${escapeHtml(item.heading || "正文")}</strong><p>${escapeHtml(item.snippet || "")}</p><div class="memory-why">${(item.why || []).map((reason) => `<span>${escapeHtml(MEMORY_WHY_LABELS[reason] || reason)}</span>`).join("")}</div><button class="person-action">打开来源</button></article>`).join("") || `<div class="person-empty"><strong>没有找到匹配内容</strong><p>换一种说法，或切换到“搜索全部”。</p></div>`;
  } catch (error) {
    elements.memorySearchMeta.textContent = error.message;
    elements.memorySearchResults.innerHTML = `<div class="person-empty"><strong>搜索没有完成</strong><p>${escapeHtml(error.message)}</p></div>`;
  } finally {
    elements.memorySearchSubmit.disabled = false;
    elements.memorySearchSubmit.textContent = "搜索";
  }
}

function quickNoteDraftKey() {
  const namespace = state.vaultName || state.rootHandle?.name || state.memoryData?.health?.vault || "local";
  return `bok.quick-note-draft.v2:${namespace}`;
}

function readQuickNoteDraft() {
  try {
    const raw = localStorage.getItem(quickNoteDraftKey());
    if (!raw) return "";
    const value = JSON.parse(raw);
    const updatedAt = Number(value?.updatedAt || 0);
    if (!value || typeof value.text !== "string" || Date.now() - updatedAt > 24 * 60 * 60 * 1000) {
      localStorage.removeItem(quickNoteDraftKey());
      return "";
    }
    return value.text;
  } catch {
    try { localStorage.removeItem(quickNoteDraftKey()); } catch { /* best effort */ }
    return "";
  }
}

function writeQuickNoteDraft(value) {
  try {
    if (value) localStorage.setItem(quickNoteDraftKey(), JSON.stringify({ text: value, updatedAt: Date.now() }));
    else localStorage.removeItem(quickNoteDraftKey());
  } catch { /* local draft is best-effort */ }
}

function updateQuickNoteMeta() {
  const length = elements.quickNoteValue.value.length;
  elements.quickNoteCount.textContent = `${length} / 20000`;
  elements.quickNoteDraftState.textContent = length ? "草稿已在本机保留" : "还没有内容";
}

function openQuickNote() {
  elements.quickNoteValue.value = readQuickNoteDraft();
  updateQuickNoteMeta();
  elements.quickNoteDialog.showModal();
  window.setTimeout(() => elements.quickNoteValue.focus(), 0);
}

function openQuickNoteWindow() {
  if (state.nativeShell) {
    fetch("/api/native/quick-note", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(async (response) => {
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.native !== true) throw new Error("native quick note unavailable");
    }).catch(() => showToast("随手记小窗没有打开，请完全退出 Bok 后重试。"));
    return;
  }
  const url = new URL("./quick-note.html", window.location.href);
  url.searchParams.set("mode", "floating");
  url.searchParams.set("service", window.location.port || "local");
  const windowName = `boujoy-quick-note-${window.location.port || "local"}`;
  const left = Math.max(0, window.screenX + window.outerWidth - 460);
  const top = Math.max(0, window.screenY + 88);
  const popup = window.open(url.href, windowName, `popup=yes,width=420,height=560,left=${left},top=${top},resizable=yes,scrollbars=no`);
  if (popup) {
    popup.focus();
    return;
  }
  showToast("浏览器阻止了独立随手记窗口，请允许此页面打开弹窗后重试。");
}

async function saveQuickNote() {
  const text = elements.quickNoteValue.value.trim();
  if (!text) return showToast("随手记还是空的。");
  elements.quickNoteSave.disabled = true;
  elements.quickNoteSave.textContent = "保存中…";
  try {
    await bokRequest("quick-notes", { method: "POST", body: { text, source: "boujoy-ui" }, idempotency: `ui-quick-note-${crypto.randomUUID?.() || Date.now()}` });
    writeQuickNoteDraft("");
    elements.quickNoteValue.value = "";
    elements.quickNoteDialog.close();
    await Promise.all([readServerVault({ force: true }), loadMemoryWorkspace()]);
    showToast("已记下来。");
  } catch (error) {
    showToast(`保存失败：${error.message}`);
  } finally {
    elements.quickNoteSave.disabled = false;
    elements.quickNoteSave.innerHTML = "记下来 <kbd>⌘↵</kbd>";
  }
}

function openMemoryAction(mode, reference) {
  elements.memoryActionMode.value = mode;
  elements.memoryActionReference.value = reference;
  elements.memoryActionReason.value = "";
  const needsReason = mode === "reject-proposal";
  elements.memoryActionReason.hidden = !needsReason;
  elements.memoryActionReason.required = needsReason;
  const copy = {
    "commit-proposal": ["确认保存这条记忆？", "Bok 会原子写入 Markdown，并保留可撤销版本。", "确认保存"],
    "reject-proposal": ["不保存这条记忆？", "说明原因可以帮助避免相同误判；不会保存原始长对话。", "确认不保存"],
    "rollback-proposal": ["撤销这次记忆提交？", "对应 Markdown 会恢复到提交前版本，操作本身也会留下记录。", "确认撤销"],
    "rollback-document": ["撤销这次 Markdown 修改？", "只有文件仍与该版本一致时才会执行，避免覆盖之后的新修改。", "确认撤销"],
  }[mode];
  if (!copy) return;
  [elements.memoryActionTitle.textContent, elements.memoryActionHint.textContent, elements.memoryActionConfirm.textContent] = copy;
  elements.memoryActionDialog.showModal();
  if (needsReason) elements.memoryActionReason.focus();
}

async function submitMemoryAction() {
  const mode = elements.memoryActionMode.value;
  const reference = elements.memoryActionReference.value;
  const reason = elements.memoryActionReason.value.trim();
  if (mode === "reject-proposal" && !reason) return showToast("请简单写明不保存的原因。");
  elements.memoryActionConfirm.disabled = true;
  const label = elements.memoryActionConfirm.textContent;
  elements.memoryActionConfirm.textContent = "处理中…";
  try {
    if (mode === "commit-proposal") await bokRequest("memory/commit", { method: "POST", body: { proposal_id: reference, confirm_important: true }, idempotency: `ui-memory-commit-${reference}` });
    else if (mode === "reject-proposal") await bokRequest("memory/reject", { method: "POST", body: { proposal_id: reference, reason }, idempotency: `ui-memory-reject-${reference}` });
    else if (mode === "rollback-proposal") await bokRequest("memory/rollback", { method: "POST", body: { proposal_id: reference, confirm_important: true }, idempotency: `ui-memory-rollback-${reference}` });
    else if (mode === "rollback-document") await bokRequest("documents/rollback", { method: "POST", body: { version_id: reference, confirm_important: true }, idempotency: `ui-document-rollback-${reference}` });
    elements.memoryActionDialog.close();
    await readServerVault({ force: true });
    await loadMemoryWorkspace();
    showToast(mode.includes("rollback") ? "已撤销，旧版本仍保留。" : mode === "reject-proposal" ? "已标记为不保存。" : "记忆已保存，可在活动页撤销。");
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.memoryActionConfirm.disabled = false;
    elements.memoryActionConfirm.textContent = label;
  }
}

async function runMemoryInlineAction(action, target) {
  const reference = target.dataset.reference || "";
  if (["commit-proposal", "reject-proposal", "rollback-proposal", "rollback-document"].includes(action)) return openMemoryAction(action, reference);
  target.disabled = true;
  try {
    if (action === "promote-note") {
      const result = await bokRequest("quick-notes/promote", { method: "POST", body: { path: target.dataset.path }, idempotency: `ui-note-promote-${target.dataset.path}` });
      showToast(result.status === "waiting_for_model" ? "已安全排队，等待本地模型。" : "已进入安静整理队列。");
    } else if (action === "archive-note") {
      await bokRequest("quick-notes/archive", { method: "POST", body: { path: target.dataset.path, expected_hash: target.dataset.hash }, idempotency: `ui-note-archive-${target.dataset.hash}` });
      showToast("随手记已归档。");
    } else if (action === "verify-backup" || action === "verify-personal-backup") {
      const personal = action === "verify-personal-backup";
      const result = await bokRequest(personal ? "person/backups/verify" : "backups/verify", { method: "POST", body: { backup_id: reference }, idempotency: `ui-${personal ? "person-" : ""}backup-verify-${reference}` });
      showToast(result.valid ? `备份有效，共 ${result.file_count} 个 Markdown。` : `备份校验失败：${result.errors?.length || 0} 项异常。`);
      return;
    } else if (action === "restore-backup" || action === "restore-personal-backup") {
      const personal = action === "restore-personal-backup";
      elements.backupRestoreId.value = reference;
      elements.backupRestoreKind.value = personal ? "personal" : "vault";
      elements.backupVaultName.textContent = personal ? (state.memoryData?.health?.personal_core?.name || "") : (state.memoryData?.health?.vault || "");
      elements.backupRestoreHint.textContent = personal ? "这会让 Personal Core 的 Markdown 精确回到该备份状态；备份后新建的 Markdown 也会移除。操作前会自动生成当前安全备份，失败会自动回滚。" : "这会让知识库 Markdown 精确回到该备份状态；备份后新建的 Markdown 也会移除。恢复前会自动生成当前安全备份，失败会自动回滚。";
      elements.backupVaultConfirm.value = "";
      elements.backupRestoreDialog.showModal();
      elements.backupVaultConfirm.focus();
      return;
    }
    await readServerVault({ force: true });
    await loadMemoryWorkspace();
  } catch (error) {
    showToast(error.message);
  } finally {
    target.disabled = false;
  }
}

async function createMemoryBackup() {
  elements.memoryCreateBackup.disabled = true;
  elements.memoryCreateBackup.textContent = "备份中…";
  try {
    const result = await bokRequest("backups/create", { method: "POST", body: {}, idempotency: `ui-backup-create-${Date.now()}` });
    await loadMemoryWorkspace();
    showToast(`备份完成：${result.file_count} 个 Markdown。`);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.memoryCreateBackup.disabled = false;
    elements.memoryCreateBackup.textContent = "立即备份";
  }
}

async function createPersonalMemoryBackup() {
  elements.memoryCreatePersonalBackup.disabled = true;
  elements.memoryCreatePersonalBackup.textContent = "备份中…";
  try {
    const result = await bokRequest("person/backups/create", { method: "POST", body: {}, idempotency: `ui-person-backup-create-${Date.now()}` });
    await loadMemoryWorkspace();
    showToast(`私人记忆备份完成：${result.file_count} 个 Markdown。`);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.memoryCreatePersonalBackup.disabled = false;
    elements.memoryCreatePersonalBackup.textContent = "备份私人记忆";
  }
}

async function restoreMemoryBackup() {
  const backupId = elements.backupRestoreId.value;
  const kind = elements.backupRestoreKind.value;
  const confirmVault = elements.backupVaultConfirm.value.trim();
  if (confirmVault !== elements.backupVaultName.textContent) return showToast("知识库名称不一致，未执行恢复。");
  elements.backupRestoreConfirm.disabled = true;
  elements.backupRestoreConfirm.textContent = "恢复中…";
  try {
    const personal = kind === "personal";
    const result = await bokRequest(personal ? "person/backups/restore" : "backups/restore", { method: "POST", body: personal ? { backup_id: backupId, confirm_personal_core: confirmVault, mode: "exact" } : { backup_id: backupId, confirm_vault: confirmVault, mode: "exact" }, idempotency: `ui-${personal ? "person-" : ""}backup-restore-${backupId}-${Date.now()}` });
    elements.backupRestoreDialog.close();
    await readServerVault({ force: true });
    await loadMemoryWorkspace();
    showToast(`恢复完成；安全备份 ${result.safety_backup} 已保留。`);
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.backupRestoreConfirm.disabled = false;
    elements.backupRestoreConfirm.textContent = "确认恢复";
  }
}

const EDITABLE_ROOTS = new Set(["01-Inbox", "02-Projects", "03-Knowledge", "04-Content", "05-Prompts", "06-Business", "07-Quick-Notes", "90-Archive"]);

function canEditRecord(record) {
  return Boolean(record && state.serverMode && EDITABLE_ROOTS.has(normalizePath(record.path).split("/")[0]) && record.path.toLocaleLowerCase("en-US").endsWith(".md"));
}

async function openDocumentEditor() {
  const record = currentReaderRecord();
  if (!canEditRecord(record)) return;
  elements.editCard.disabled = true;
  try {
    const document = await bokRequest(`documents/read?path=${encodeURIComponent(record.path)}`);
    state.documentEdit = document;
    elements.documentEditTitle.textContent = `编辑：${record.title}`;
    elements.documentEditPath.textContent = record.path;
    elements.documentEditValue.value = document.text || "";
    elements.documentImportantConfirm.checked = false;
    elements.documentEditDialog.showModal();
    elements.documentEditValue.focus();
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.editCard.disabled = false;
  }
}

async function saveDocumentEditor() {
  const document = state.documentEdit;
  if (!document) return;
  elements.documentEditSave.disabled = true;
  elements.documentEditSave.textContent = "保存中…";
  try {
    await bokRequest("documents/write", { method: "POST", body: { path: document.path, text: elements.documentEditValue.value, expected_hash: document.content_hash, confirm_important: elements.documentImportantConfirm.checked }, idempotency: `ui-document-save-${document.content_hash}-${Date.now()}` });
    elements.documentEditDialog.close();
    state.documentEdit = null;
    await readServerVault({ force: true });
    const refreshed = state.files.find((item) => item.path === document.path);
    if (refreshed) showReader(refreshed);
    showToast("已保存新版本，可在 Bok 工作台撤销。");
  } catch (error) {
    if (error.code === "important_confirmation_required") showToast("这是重要内容，请勾选底部确认后再保存。");
    else if (error.status === 409 || error.status === 428) showToast("文件已在别处变化，未覆盖；关闭编辑器后重新打开再修改。");
    else showToast(error.message);
  } finally {
    elements.documentEditSave.disabled = false;
    elements.documentEditSave.textContent = "保存新版本";
  }
}

const PERSON_TYPE_LABELS = {
  identity: "身份与自我认知",
  long_term_goal: "长期目标",
  communication_preference: "沟通方式",
  work_preference: "工作习惯",
  decision_pattern: "决策方式",
  authority_rule: "协作边界",
  public_identity: "公开身份",
  capability_claim: "掌握的能力",
  project_experience: "项目经历",
  knowledge_claim: "知识与认知",
  negative_preference: "明确不喜欢",
  temporary_state: "临时状态",
  behavior_hypothesis: "行为假设",
};

const PERSON_STATUS_LABELS = {
  explicit: "重要信息，需你介入",
  observed: "观察中",
  hypothesis: "重要判断，需你介入",
  learned: "长期观察后形成",
  contradicted: "发现冲突，需你介入",
  confirmed: "你已确认",
  rejected: "已拒绝",
  superseded: "已被新记忆替代",
  expired: "已过期",
  pending: "待整理",
  accumulating: "正在积累证据",
  projected: "已投影为候选记忆",
  excluded_sensitive: "敏感内容已排除",
  recorded: "已记录",
};

const PERSON_CLEANUP_REASON_LABELS = {
  duplicate: "内容重复",
  rejected: "已拒绝",
  superseded: "已被新记忆替代",
  expired: "已经过期",
  contradictory_evidence: "存在相互冲突的证据",
  negative_outcomes: "近期带来较多负面结果",
  stale_180_days: "超过 180 天未使用",
};

const PERSON_CLEANUP_ACTION_LABELS = {
  review: "人工复核",
  merge_or_expire: "合并或设为过期",
  keep_as_history: "保留为历史",
  review_conflict: "处理冲突后再决定",
  review_or_expire: "复核或设为过期",
};

function personDate(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parsed);
}

async function loadPersonDashboard({ announce = false } = {}) {
  if (state.personLoading) return;
  state.personLoading = true;
  elements.personLoading.hidden = false;
  elements.personUnavailable.hidden = true;
  elements.personWorkspace.hidden = true;
  try {
    if (!state.serverMode) throw new Error("个人记忆中心需要通过本地 App 或预览服务打开。纯浏览器文件夹模式仍保持只读。");
    state.personData = await bokRequest("person/dashboard?limit=500");
    if (!state.personData.configured) {
      elements.personProcess.disabled = true;
      elements.personUnavailable.querySelector("p").textContent = "Personal Core 尚未配置。它必须位于知识库和 Git 仓库之外，浏览器不会获得或显示绝对路径。";
      elements.personUnavailable.hidden = false;
    } else {
      elements.personProcess.disabled = false;
      elements.personWorkspace.hidden = false;
      renderPersonDashboard();
      if (announce) showToast("个人记忆已刷新。");
    }
  } catch (error) {
    state.personData = null;
    elements.personProcess.disabled = true;
    elements.personUnavailable.querySelector("p").textContent = error.message;
    elements.personUnavailable.hidden = false;
  } finally {
    state.personLoading = false;
    elements.personLoading.hidden = true;
  }
}

function claimSourceText(claim) {
  const sources = Array.isArray(claim.source_refs) ? claim.source_refs : [];
  return sources.length ? `${sources.length} 条来源 · 支持 ${claim.support_count || 0} · 冲突 ${claim.contradiction_count || 0}` : "暂无来源";
}

function personClaimMarkup(claim, { review = false } = {}) {
  const status = PERSON_STATUS_LABELS[claim.epistemic_status] || claim.epistemic_status;
  const scope = claim.scope_kind === "global" ? "所有场景" : `${claim.scope_kind} · ${claim.scope_value || "未命名"}`;
  const accessScopes = Array.isArray(claim.access_scope) ? claim.access_scope : [];
  const agentAuthorized = accessScopes.some((item) => item === "all-agents" || item.startsWith("agent:") || item.startsWith("project:"));
  const quietlyLearned = claim.epistemic_status === "learned";
  const access = quietlyLearned && accessScopes.includes("personal-core") && !agentAuthorized
    ? "已供本机记忆上下文使用"
    : accessScopes.includes("all-agents")
    ? "所有本机 Agent 可用"
    : agentAuthorized
      ? "已按指定范围授权"
      : "仅私人记忆中心可见";
  const accessAction = quietlyLearned && !agentAuthorized
    ? ""
    : `<button class="person-action ${agentAuthorized ? "quiet" : "primary"}" data-person-action="${agentAuthorized ? "revoke-access" : "authorize"}" data-claim-id="${escapeHtml(claim.id)}">${agentAuthorized ? "恢复本地默认范围" : "允许本机 Agent"}</button>`;
  const actions = review
    ? `<button class="person-action primary" data-person-action="confirm" data-claim-id="${escapeHtml(claim.id)}">确认内容</button><button class="person-action" data-person-action="correct" data-claim-id="${escapeHtml(claim.id)}">纠正</button><button class="person-action danger" data-person-action="reject" data-claim-id="${escapeHtml(claim.id)}">拒绝</button><button class="person-action danger" data-person-action="forget" data-claim-id="${escapeHtml(claim.id)}">彻底忘记</button>`
    : `${accessAction}<button class="person-action" data-person-action="correct" data-claim-id="${escapeHtml(claim.id)}">纠正</button><button class="person-action" data-person-action="explain" data-claim-id="${escapeHtml(claim.id)}">查看依据</button><button class="person-action quiet" data-person-action="expire" data-claim-id="${escapeHtml(claim.id)}">设为过期</button><button class="person-action danger" data-person-action="forget" data-claim-id="${escapeHtml(claim.id)}">彻底忘记</button>`;
  return `<article class="person-memory-card" data-claim-card="${escapeHtml(claim.id)}">
    <div class="person-memory-meta"><span>${escapeHtml(PERSON_TYPE_LABELS[claim.claim_type] || claim.claim_type)}</span><span class="person-status">${escapeHtml(status)}</span></div>
    <h3>${escapeHtml(claim.statement)}</h3>
    <p>${escapeHtml(claimSourceText(claim))}</p>
    <div class="person-memory-chips"><span>${escapeHtml(scope)}</span><span>${escapeHtml(access)}</span><span>版本 ${escapeHtml(claim.version)}</span></div>
    <div class="person-memory-actions">${actions}</div>
    <details><summary>来源与记录</summary><div class="person-source-list">${(claim.source_refs || []).map((item) => `<code>${escapeHtml(item)}</code>`).join("") || "<span>暂无来源</span>"}</div></details>
  </article>`;
}

function renderPersonDashboard() {
  const data = state.personData;
  if (!data?.configured) return;
  const understanding = data.claims?.understanding || data.claims?.confirmed || [];
  const reviewRequired = data.claims?.review_required || data.claims?.pending || [];
  const profile = data.claims?.profile || [];
  const observations = data.observations?.recent || [];
  const outcomes = data.outcomes?.recent || [];
  const impacts = data.impacts?.recent || [];
  const timeline = data.timeline || [];
  const cleanup = data.cleanup?.items || [];
  const accumulating = Number(data.observations?.counts?.accumulating || 0) + Number(data.observations?.counts?.pending || 0);
  elements.personHero.innerHTML = `<div><span class="section-kicker">YOUR MEMORY, YOUR CONTROL</span><h2>${understanding.length ? `已经形成 ${understanding.length} 条对你的理解` : "正在从长期相处中了解你"}</h2><p>${reviewRequired.length ? `${reviewRequired.length} 条高风险、冲突或重大判断需要你介入；普通习惯不会逐条打扰。` : "普通习惯会安静更新，当前没有需要你介入的事项。"}</p></div><div class="person-stat-grid"><div><strong>${understanding.length}</strong><span>已形成理解</span></div><div><strong>${reviewRequired.length}</strong><span>需介入</span></div><div><strong>${accumulating}</strong><span>观察中</span></div><div><strong>${data.impacts?.count || 0}</strong><span>实际采用</span></div></div>`;
  elements.personPendingBadge.textContent = reviewRequired.length;
  elements.personCleanupBadge.textContent = cleanup.length;

  elements.personProfileSummary.innerHTML = profile.length
    ? `<div class="section-heading"><div><h2>现在它怎样理解你</h2><p>按长期对话、真实选择、纠正和项目经历持续更新，不保存对话原文。</p></div><span class="result-count">${profile.length} 个维度</span></div><div class="person-profile-grid">${profile.map((dimension) => `<article class="person-profile-card"><div><strong>${escapeHtml(dimension.label)}</strong><span>${dimension.count} 条理解</span></div><ul>${(dimension.statements || []).map((statement) => `<li>${escapeHtml(statement)}</li>`).join("")}</ul></article>`).join("")}</div>`
    : `<div class="person-empty"><strong>正在积累对你的理解</strong><p>清晰的偏好、反复选择和项目行为会逐渐形成可更新的多维画像。</p></div>`;

  const agents = data.permissions?.agents || [];
  const activeAgents = agents.filter((item) => item.status === "active");
  elements.personPermissionSummary.innerHTML = `<div><div><strong>${activeAgents.length ? `${activeAgents.length} 个本机 Agent 已连接` : "暂未连接本机 Agent"}</strong><p>低风险且证据充分的理解可直接用于本机回答；身份、敏感信息、重大规则、冲突和低置信判断仍会停下等你介入。</p></div></div><div class="person-agent-list">${activeAgents.map((item) => `<span title="${escapeHtml((item.scopes || []).join(" · "))}">${escapeHtml(item.agent_id)}</span>`).join("") || "<span>Personal Core</span>"}</div>`;

  const groups = data.claims?.groups || {};
  const groupEntries = Object.entries(groups).sort((left, right) => (PERSON_TYPE_LABELS[left[0]] || left[0]).localeCompare(PERSON_TYPE_LABELS[right[0]] || right[0], "zh-CN"));
  elements.personGroups.innerHTML = groupEntries.map(([type, claims]) => `<section class="person-group"><div class="section-heading"><div><span class="section-kicker">${escapeHtml(type.replaceAll("_", " ").toUpperCase())}</span><h2>${escapeHtml(PERSON_TYPE_LABELS[type] || type)}</h2></div><span class="result-count">${claims.length} 条</span></div><div class="person-card-grid">${claims.map((claim) => personClaimMarkup(claim)).join("")}</div></section>`).join("") || `<div class="person-empty"><strong>还没有稳定理解</strong><p>观察会先积累，证据足够后再安静形成理解。</p></div>`;
  elements.personReviewList.innerHTML = reviewRequired.map((claim) => personClaimMarkup(claim, { review: true })).join("") || `<div class="person-empty"><strong>没有需要你介入的事项</strong><p>普通偏好和习惯会在后台更新；这里只保留高风险、冲突或重大判断。</p></div>`;

  elements.personObservationList.innerHTML = observations.map((item) => `<article class="person-stream-item"><span class="person-event-dot"></span><div><strong>${escapeHtml(item.candidate_statement || "敏感内容已排除，未保存正文")}</strong><p>${escapeHtml(PERSON_STATUS_LABELS[item.status] || item.status)} · ${escapeHtml(PERSON_TYPE_LABELS[item.claim_type] || item.claim_type || "未分类")}</p><time>${escapeHtml(personDate(item.occurred_at || item.updated_at))}</time></div></article>`).join("") || `<p class="muted">还没有观察记录。</p>`;
  elements.personOutcomeList.innerHTML = outcomes.map((item) => `<article class="person-stream-item"><span class="person-event-dot is-${escapeHtml(item.outcome || "neutral")}"></span><div><strong>${item.outcome === "positive" ? "这次回答有效" : item.outcome === "negative" ? "这次回答需要改进" : "已记录中性结果"}</strong><p>${escapeHtml(item.note || `${(item.claim_ids || []).length} 条记忆参与`)}</p><time>${escapeHtml(personDate(item.created_at))}</time></div></article>`).join("") || `<p class="muted">还没有回答结果。</p>`;
  const outcomeByAnswer = new Map(outcomes.map((item) => [item.answer_ref, item]));
  elements.personImpactList.innerHTML = impacts.map((item) => {
    const feedback = outcomeByAnswer.get(item.answer_ref);
    const actions = feedback
      ? `<span class="person-feedback is-${escapeHtml(feedback.outcome || "neutral")}">${feedback.outcome === "positive" ? "已标记有帮助" : feedback.outcome === "negative" ? "已标记需要返工" : "已记录结果"}</span>`
      : `<div class="person-memory-actions"><button class="person-action" data-person-action="outcome-positive" data-impact-id="${escapeHtml(item.id)}">有帮助</button><button class="person-action danger" data-person-action="outcome-negative" data-impact-id="${escapeHtml(item.id)}">需要返工</button></div>`;
    return `<article class="person-impact-card"><div><span>${escapeHtml(item.agent || "本机 Agent")}</span><time>${escapeHtml(personDate(item.created_at))}</time></div><h3>${escapeHtml(item.task_summary || "未命名任务")}</h3><p>${(item.claim_ids || []).length} 条记忆影响了这次回答</p>${actions}</article>`;
  }).join("") || `<p class="muted">还没有影响记录。Agent 使用个人上下文并完成回答后，这里才会出现。</p>`;
  elements.personTimelineList.innerHTML = timeline.map((item) => `<article class="person-timeline-item"><time>${escapeHtml(personDate(item.at))}</time><span class="person-timeline-line" aria-hidden="true"></span><div><strong>${escapeHtml(item.label || "记忆记录")}</strong><p>${escapeHtml(item.kind === "claim" ? "个人认识" : item.kind === "outcome" ? "回答结果" : "对话观察")} · ${escapeHtml(PERSON_STATUS_LABELS[item.status] || item.status || "已记录")}</p></div></article>`).join("") || `<p class="muted">还没有记忆变化记录。</p>`;
  elements.personCleanupList.innerHTML = cleanup.map((item) => `<article class="person-cleanup-card"><div><span>${escapeHtml(item.protected ? "重要记忆 · 不会自动删除" : "普通记忆")}</span><strong>${escapeHtml((item.reasons || []).map((reason) => PERSON_CLEANUP_REASON_LABELS[reason] || reason).join(" · "))}</strong></div><h3>${escapeHtml(item.statement)}</h3><p>建议：${escapeHtml(PERSON_CLEANUP_ACTION_LABELS[item.suggested_action] || item.suggested_action || "人工复核")}</p><div class="person-memory-actions"><button class="person-action" data-person-action="cleanup-keep" data-claim-id="${escapeHtml(item.claim_id)}">保留并忽略建议</button><button class="person-action quiet" data-person-action="expire" data-claim-id="${escapeHtml(item.claim_id)}">设为过期</button><button class="person-action danger" data-person-action="forget" data-claim-id="${escapeHtml(item.claim_id)}">彻底忘记</button></div></article>`).join("") || `<div class="person-empty"><strong>没有垃圾记忆候选</strong><p>重要记忆不会因为低频自动删除。</p></div>`;
  renderPersonTab();
}

function renderPersonTab() {
  elements.personTabs.querySelectorAll("[data-person-tab]").forEach((button) => {
    const active = button.dataset.personTab === state.personTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  elements.personWorkspace.querySelectorAll("[data-person-panel]").forEach((panel) => { panel.hidden = panel.dataset.personPanel !== state.personTab; });
  if (state.personTab === "graph") window.requestAnimationFrame(renderPersonGraph);
}

const PERSON_GRAPH_COLORS = ["#ff3e91", "#276bf0", "#0ca595", "#f1a22b", "#8358d5", "#dc5548", "#278ca7"];

function buildPersonGraph(claims, width, height) {
  const grouped = new Map();
  claims.forEach((claim) => {
    const type = claim.claim_type || "other";
    if (!grouped.has(type)) grouped.set(type, []);
    grouped.get(type).push(claim);
  });
  const groups = [...grouped.entries()].sort((left, right) => (PERSON_TYPE_LABELS[left[0]] || left[0]).localeCompare(PERSON_TYPE_LABELS[right[0]] || right[0], "zh-CN"));
  const compact = groups.length <= 2;
  const center = { x: compact ? width * 0.20 : width * 0.5, y: height * 0.52 };
  const nodes = [{ kind: "self", id: "self", label: "你", x: center.x, y: center.y, radius: 30, width: 60, height: 60, color: "#172b2d" }];
  const edges = [];
  const groupRadiusX = Math.min(width * 0.32, 270);
  const groupRadiusY = Math.min(height * 0.30, 150);
  groups.forEach(([type, items], groupIndex) => {
    const angle = -Math.PI / 2 + groupIndex / Math.max(groups.length, 1) * Math.PI * 2;
    const compactY = center.y + (groupIndex - (groups.length - 1) / 2) * 126;
    const groupNode = {
      kind: "group",
      id: `group:${type}`,
      type,
      label: PERSON_TYPE_LABELS[type] || type,
      x: compact ? width * 0.47 : center.x + Math.cos(angle) * groupRadiusX,
      y: compact ? compactY : center.y + Math.sin(angle) * groupRadiusY,
      radius: 22,
      width: 78,
      height: 38,
      color: PERSON_GRAPH_COLORS[groupIndex % PERSON_GRAPH_COLORS.length],
    };
    const groupNodeIndex = nodes.push(groupNode) - 1;
    edges.push({ a: 0, b: groupNodeIndex, kind: "group" });
    items.forEach((claim, claimIndex) => {
      const spread = (claimIndex - (items.length - 1) / 2) * 0.46;
      const claimAngle = angle + spread;
      const distance = 82 + Math.min(34, claimIndex * 6);
      const claimNode = {
        kind: "claim",
        id: claim.id,
        claim,
        label: claim.statement,
        x: compact ? Math.min(width - 70, width * 0.75 + (claimIndex % 2) * 112) : groupNode.x + Math.cos(claimAngle) * distance,
        y: compact ? groupNode.y + (claimIndex - (items.length - 1) / 2) * 54 : groupNode.y + Math.sin(claimAngle) * distance,
        radius: 8 + Math.min(4, Number(claim.support_count || 0)),
        width: 116,
        height: 40,
        color: groupNode.color,
      };
      const claimNodeIndex = nodes.push(claimNode) - 1;
      edges.push({ a: groupNodeIndex, b: claimNodeIndex, kind: "claim" });
    });
  });
  return { nodes, edges, groups };
}

function personGraphHit(node, point, padding = 0) {
  if (node.kind === "self") return Math.hypot(node.x - point.x, node.y - point.y) <= node.radius + padding;
  return Math.abs(node.x - point.x) <= node.width / 2 + padding && Math.abs(node.y - point.y) <= node.height / 2 + padding;
}

function renderPersonGraph() {
  if (!elements.personGraph || state.personTab !== "graph" || state.view !== "person") return;
  const claims = (state.personData?.claims?.understanding || state.personData?.claims?.confirmed || []).filter((claim) => claim.effective !== false);
  elements.personGraphEmpty.hidden = claims.length > 0;
  elements.personGraph.hidden = claims.length === 0;
  elements.personGraphStats.textContent = claims.length ? `${claims.length} 条长期理解` : "0 条";
  elements.personGraphList.innerHTML = claims.map((claim) => `<li><button type="button" data-person-graph-claim="${escapeHtml(claim.id)}"><strong>${escapeHtml(PERSON_TYPE_LABELS[claim.claim_type] || claim.claim_type || "未分类")}</strong><span>${escapeHtml(claim.statement)}</span></button></li>`).join("");
  elements.personGraphList.querySelectorAll("[data-person-graph-claim]").forEach((button) => button.addEventListener("click", () => revealPersonClaim(button.dataset.personGraphClaim)));
  if (!claims.length) {
    elements.personGraphLegend.innerHTML = "";
    return;
  }
  const graph = buildPersonGraph(claims, elements.personGraphStage.clientWidth, elements.personGraphStage.clientHeight);
  state.personGraphNodes = graph.nodes;
  state.personGraphEdges = graph.edges;
  elements.personGraphLegend.innerHTML = graph.groups.map(([type], index) => `<span><i style="background:${PERSON_GRAPH_COLORS[index % PERSON_GRAPH_COLORS.length]}"></i>${escapeHtml(PERSON_TYPE_LABELS[type] || type)}</span>`).join("");
  drawPersonGraph();
}

function drawPersonGraph() {
  const canvas = elements.personGraph;
  if (!canvas || canvas.hidden) return;
  const context = canvas.getContext("2d");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);
  const hoveredIndex = state.personGraphNodes.findIndex((node) => personGraphHit(node, state.personGraphPointer, 8));
  const connected = new Set();
  if (hoveredIndex >= 0) {
    connected.add(hoveredIndex);
    state.personGraphEdges.forEach((edge) => {
      if (edge.a === hoveredIndex) connected.add(edge.b);
      if (edge.b === hoveredIndex) connected.add(edge.a);
    });
  }
  state.personGraphEdges.forEach((edge) => {
    const first = state.personGraphNodes[edge.a];
    const second = state.personGraphNodes[edge.b];
    const active = hoveredIndex < 0 || connected.has(edge.a) && connected.has(edge.b);
    context.globalAlpha = active ? 0.58 : 0.10;
    context.strokeStyle = second.color;
    context.lineWidth = edge.kind === "group" ? 2 : 1.2;
    context.setLineDash(edge.kind === "group" ? [6, 5] : []);
    context.beginPath();
    context.moveTo(first.x, first.y);
    const bend = (first.x + second.x) / 2 + (second.y - first.y) * 0.08;
    context.quadraticCurveTo(bend, (first.y + second.y) / 2, second.x, second.y);
    context.stroke();
  });
  context.setLineDash([]);
  state.personGraphNodes.forEach((node, index) => {
    const active = index === hoveredIndex;
    context.globalAlpha = hoveredIndex < 0 || connected.has(index) ? 1 : 0.25;
    context.save();
    context.translate(node.x, node.y);
    if (node.kind === "self") {
      context.fillStyle = "rgba(255,62,145,.28)";
      context.fillRect(-34, -31, 68, 68);
      context.fillStyle = node.color;
      context.fillRect(-30, -35, 60, 60);
    } else if (node.kind === "group") {
      context.rotate((hashNumber(node.id) % 7 - 3) * Math.PI / 180);
      context.translate(4, 5);
      paperScrapPath(context, node.width, node.height, hashNumber(node.id));
      context.fillStyle = "rgba(23,43,45,.16)";
      context.fill();
      context.translate(-4, -5);
      paperScrapPath(context, node.width, node.height, hashNumber(node.id));
      context.fillStyle = node.color;
      context.fill();
    } else {
      context.rotate((hashNumber(node.id) % 5 - 2) * Math.PI / 180);
      context.translate(4, 5);
      paperScrapPath(context, node.width, node.height, hashNumber(node.id));
      context.fillStyle = "rgba(23,43,45,.16)";
      context.fill();
      context.translate(-4, -5);
      paperScrapPath(context, node.width, node.height, hashNumber(node.id));
      context.fillStyle = "#fffaf0";
      context.fill();
      context.strokeStyle = node.color;
      context.lineWidth = active ? 3 : 1.5;
      context.stroke();
      context.fillStyle = `${node.color}66`;
      context.fillRect(-16, -node.height / 2 - 3, 32, 7);
    }
    if (node.kind !== "claim") {
      context.fillStyle = "white";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.font = node.kind === "self" ? '22px "Fusion Pixel", sans-serif' : '10px "Microsoft YaHei UI", sans-serif';
      const label = node.kind === "self" ? node.label : node.label.slice(0, 5);
      context.fillText(label, 0, node.kind === "self" ? -4 : 0);
    } else {
      context.fillStyle = "#172b2d";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.font = '9px "Microsoft YaHei UI", sans-serif';
      const label = node.label.length > 12 ? `${node.label.slice(0, 12)}…` : node.label;
      context.fillText(label, 0, 2);
    }
    context.restore();
  });
  context.globalAlpha = 1;
  const hovered = hoveredIndex >= 0 ? state.personGraphNodes[hoveredIndex] : null;
  if (hovered?.kind === "claim") {
    elements.personGraphTooltip.hidden = false;
    elements.personGraphTooltip.innerHTML = `<strong>${escapeHtml(hovered.claim.statement)}</strong><span>${escapeHtml(PERSON_TYPE_LABELS[hovered.claim.claim_type] || hovered.claim.claim_type || "未分类")} · ${escapeHtml(PERSON_STATUS_LABELS[hovered.claim.epistemic_status] || "长期理解")}</span>`;
    elements.personGraphTooltip.style.left = `${Math.min(width - 250, Math.max(12, hovered.x + 16))}px`;
    elements.personGraphTooltip.style.top = `${Math.min(height - 90, Math.max(12, hovered.y - 20))}px`;
    canvas.style.cursor = "pointer";
  } else {
    elements.personGraphTooltip.hidden = true;
    canvas.style.cursor = "default";
  }
}

function revealPersonClaim(claimId) {
  state.personTab = "profile";
  renderPersonTab();
  window.requestAnimationFrame(() => {
    const card = elements.personGroups.querySelector(`[data-claim-card="${CSS.escape(claimId)}"]`);
    card?.scrollIntoView({ behavior: state.reduceMotion ? "auto" : "smooth", block: "center" });
    card?.classList.add("is-revealed");
    window.setTimeout(() => card?.classList.remove("is-revealed"), 1400);
  });
}

function openPersonConfirmation(action, claimId) {
  const claim = allPersonClaims().find((item) => item.id === claimId);
  elements.personConfirmAction.value = action;
  elements.personConfirmClaimId.value = claimId;
  if (action === "expire") {
    elements.personConfirmTitle.textContent = "把这条记忆设为过期？";
    elements.personConfirmHint.textContent = `“${claim?.statement || "这条记忆"}”将不再影响回答，来源、旧版本和操作记录仍会保留。`;
    elements.personConfirmButton.textContent = "设为过期";
  } else if (action === "forget") {
    elements.personConfirmTitle.textContent = "彻底忘记这条认识？";
    elements.personConfirmHint.textContent = `“${claim?.statement || "这条认识"}”及其观察证据、影响记录、旧版本和相关私人备份会被清除，对应对话原文也会被遗忘。此操作无法撤销。`;
    elements.personConfirmButton.textContent = "确认彻底忘记";
  }
  elements.personConfirmDialog.showModal();
}

function allPersonClaims() {
  const data = state.personData?.claims || {};
  const understanding = data.understanding || data.confirmed || [];
  const reviewRequired = data.review_required || data.pending || [];
  return [...understanding, ...reviewRequired];
}

function openPersonEdit(mode, reference) {
  elements.personEditMode.value = mode;
  elements.personEditClaimId.value = reference;
  elements.personEditValue.value = "";
  elements.personEditValue.readOnly = false;
  elements.personEditConfirm.hidden = false;
  if (mode === "correct") {
    const claim = allPersonClaims().find((item) => item.id === reference);
    elements.personEditKicker.textContent = "CORRECT MEMORY";
    elements.personEditTitle.textContent = "纠正这条记忆";
    elements.personEditHint.textContent = "旧表述会进入历史，不会被无痕覆盖。";
    elements.personEditValue.value = claim?.statement || "";
  } else if (mode === "reject") {
    elements.personEditKicker.textContent = "REJECT MEMORY";
    elements.personEditTitle.textContent = "为什么拒绝它？";
    elements.personEditHint.textContent = "拒绝记录会阻止相同内容被后台静默重建。";
  } else if (mode === "outcome-negative") {
    elements.personEditKicker.textContent = "NEGATIVE OUTCOME";
    elements.personEditTitle.textContent = "哪里需要返工？";
    elements.personEditHint.textContent = "结果会用于降低错误记忆的可信度，不保存整段回答。";
  } else if (mode === "explain") {
    elements.personEditKicker.textContent = "MEMORY SOURCE";
    elements.personEditTitle.textContent = "这条认识从哪里来";
    elements.personEditHint.textContent = "只显示来源引用，不展开整段私人对话。";
  }
  elements.personEditDialog.showModal();
  elements.personEditValue.focus();
}

async function runPersonAction(action, target) {
  if (state.personActionRunning) return;
  const claimId = target.dataset.claimId || "";
  state.personActionRunning = true;
  target.disabled = true;
  try {
    if (action === "confirm") {
      const claim = allPersonClaims().find((item) => item.id === claimId);
      await bokRequest("person/claims/confirm", { method: "POST", body: { claim_id: claimId, source_ref: "ui:user-confirmation" }, idempotency: `ui-confirm-${claimId}-${claim?.version || 0}` });
      showToast("内容已确认，仍只保留在私人记忆中心。 ");
    } else if (action === "authorize" || action === "revoke-access") {
      const claim = allPersonClaims().find((item) => item.id === claimId);
      const accessScope = action === "authorize" ? ["all-agents"] : ["personal-core"];
      await bokRequest("person/claims/authorize", { method: "POST", body: { claim_id: claimId, access_scope: accessScope, source_ref: `ui:${action}` }, idempotency: `ui-${action}-${claimId}-${claim?.version || 0}` });
      showToast(action === "authorize" ? "已单独授权本机 Agent 使用。 " : "已收回 Agent 授权，内容仍保留。 ");
    } else if (action === "correct" || action === "reject") {
      openPersonEdit(action, claimId);
      return;
    } else if (action === "explain") {
      const detail = await bokRequest(`person/claims/explain?id=${encodeURIComponent(claimId)}`);
      const sources = (detail.explanation?.sources || []).join("\n") || "暂无来源";
      openPersonEdit("explain", claimId);
      elements.personEditTitle.textContent = "这条认识从哪里来";
      elements.personEditHint.textContent = `${detail.explanation?.basis || "unknown"} · ${detail.explanation?.effective_reason || "unknown"}`;
      elements.personEditValue.value = sources;
      elements.personEditValue.readOnly = true;
      elements.personEditConfirm.hidden = true;
      return;
    } else if (action === "expire" || action === "forget") {
      openPersonConfirmation(action, claimId);
      return;
    } else if (action === "cleanup-keep") {
      await bokRequest("person/cleanup", { method: "POST", body: { claim_id: claimId, action: "keep" }, idempotency: `ui-keep-${claimId}` });
      showToast("已保留，不再显示这条清理建议。 ");
    } else if (action.startsWith("outcome-")) {
      const impact = (state.personData?.impacts?.recent || []).find((item) => item.id === target.dataset.impactId);
      if (!impact) throw new Error("找不到这次影响记录。 ");
      if (action === "outcome-negative") { openPersonEdit("outcome-negative", impact.id); return; }
      await bokRequest("person/outcomes", { method: "POST", body: { answer_ref: impact.answer_ref, outcome: "positive", agent: impact.agent, project: impact.project || "", claim_ids: impact.claim_ids, source_ref: `ui:feedback:${impact.id}`, rating: 5 }, idempotency: `ui-outcome-positive-${impact.id}` });
      showToast("已记录这次回答有效。 ");
    }
    await loadPersonDashboard();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.personActionRunning = false;
    target.disabled = false;
  }
}

async function submitPersonConfirmation() {
  const action = elements.personConfirmAction.value;
  const claimId = elements.personConfirmClaimId.value;
  if (!["expire", "forget"].includes(action) || !claimId || state.personActionRunning) return;
  state.personActionRunning = true;
  elements.personConfirmButton.disabled = true;
  elements.personConfirmButton.textContent = "处理中…";
  try {
    const result = action === "forget"
      ? await bokRequest("person/claims/forget", { method: "POST", body: { claim_id: claimId, confirm_forget: true }, idempotency: `ui-forget-${claimId}` })
      : await bokRequest("person/cleanup", { method: "POST", body: { claim_id: claimId, action: "expire", confirm_important: true }, idempotency: `ui-expire-${claimId}` });
    elements.personConfirmDialog.close();
    await loadPersonDashboard();
    if (action === "forget") {
      const warning = result.uninspectable_backups?.length ? "；有无法校验的旧备份需要手动检查" : "";
      const derived = Array.isArray(result.derived_memory_requiring_review) ? result.derived_memory_requiring_review : [];
      const derivedWarning = derived.length ? `；另有 ${derived.length} 条已写入知识库的内容未自动删除，需要单独检查` : "";
      showToast(`已彻底忘记个人认识${warning}${derivedWarning}。`);
    } else showToast("已设为过期，来源和旧版本仍保留。");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.personActionRunning = false;
    elements.personConfirmButton.disabled = false;
    elements.personConfirmButton.textContent = "确认";
  }
}

async function submitPersonEdit() {
  const mode = elements.personEditMode.value;
  const reference = elements.personEditClaimId.value;
  const value = elements.personEditValue.value.trim();
  if (mode === "explain") { elements.personEditDialog.close(); return; }
  if (!value) return showToast("请填写内容。");
  if (state.personActionRunning) return;
  state.personActionRunning = true;
  elements.personEditConfirm.disabled = true;
  const buttonLabel = elements.personEditConfirm.textContent;
  elements.personEditConfirm.textContent = "保存中…";
  try {
    if (mode === "correct") {
      await bokRequest("person/claims/correct", { method: "POST", body: { claim_id: reference, statement: value, source_ref: `ui:correction:${Date.now()}` }, idempotency: `ui-correct-${reference}-${Date.now()}` });
    } else if (mode === "reject") {
      await bokRequest("person/claims/reject", { method: "POST", body: { claim_id: reference, reason: value, source_ref: "ui:user-rejection" }, idempotency: `ui-reject-${reference}` });
    } else if (mode === "outcome-negative") {
      const impact = (state.personData?.impacts?.recent || []).find((item) => item.id === reference);
      if (!impact) throw new Error("找不到这次影响记录。 ");
      await bokRequest("person/outcomes", { method: "POST", body: { answer_ref: impact.answer_ref, outcome: "negative", agent: impact.agent, project: impact.project || "", claim_ids: impact.claim_ids, source_ref: `ui:feedback:${impact.id}`, rating: 1, rework: true, note: value }, idempotency: `ui-outcome-negative-${impact.id}` });
    }
    elements.personEditDialog.close();
    elements.personEditValue.readOnly = false;
    elements.personEditConfirm.hidden = false;
    await loadPersonDashboard();
    showToast("个人记忆已更新。 ");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.personActionRunning = false;
    elements.personEditConfirm.disabled = false;
    elements.personEditConfirm.textContent = buttonLabel;
  }
}

function cleanupCandidates() {
  if (state.serverMode && Array.isArray(state.cleanupStatus?.items)) return [...state.cleanupStatus.items];
  const report = state.files.find((record) => record.path === "00-System/Cleanup-Candidates.md");
  if (!report) return [];
  const candidates = new Set();
  const candidateSections = report.text.split(/^## E\. 完全重复的 Markdown\s*$/mu)[0];
  for (const match of candidateSections.matchAll(/`([^`]+)`/gu)) {
    const value = normalizePath(match[1].trim());
    if (value && !value.startsWith("00-System/") && !value.startsWith("tools/")) candidates.add(value);
  }
  return [...candidates].filter((path) => path !== "00-System/Cleanup-Candidates.md");
}

function openCleanupDialog() {
  const candidates = cleanupCandidates();
  if (!candidates.length) return showToast("目前没有可清理候选。");
  state.cleanupPaths = candidates;
  elements.cleanupSummary.textContent = `将把 ${candidates.length} 项候选移到系统废纸篓。知识卡、正式成品和 Asset-Index 不会被处理。`;
  elements.cleanupList.innerHTML = candidates.slice(0, 12).map((path) => `<li>${escapeHtml(path)}</li>`).join("") + (candidates.length > 12 ? `<li>……以及其他 ${candidates.length - 12} 项</li>` : "");
  elements.cleanupDialog.showModal();
}

async function executeCleanup() {
  const paths = state.cleanupPaths || [];
  if (!paths.length || !state.serverMode) return;
  elements.confirmCleanup.disabled = true;
  elements.confirmCleanup.textContent = "处理中…";
  try {
    const response = await fetch("/api/cleanup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths }) });
    const result = await response.json();
    if (!response.ok || !result.verified || (result.remaining || []).length) {
      const failedPath = result.failed?.[0]?.path;
      const detail = result.failed?.[0]?.detail;
      throw new Error([result.error || "cleanup_failed", failedPath, detail].filter(Boolean).join(" · "));
    }
    elements.cleanupDialog.close();
    await readCleanupStatus();
    const absent = Number(result.already_absent || 0);
    showToast(`清理完成：移入废纸篓 ${result.moved} 项${absent ? `，另有 ${absent} 项已不存在` : ""}，残留核验通过。`);
    await readServerVault({ force: true, announce: false });
  } catch (error) {
    showToast(`清理失败：${error.message}`);
  } finally {
    elements.confirmCleanup.disabled = false;
    elements.confirmCleanup.textContent = "移到废纸篓";
  }
}

function selectRecord(path, openReader = true) {
  const normalized = normalizePath(path);
  const record = state.files.find((item) => item.path === normalized);
  if (!record) { showToast(`未找到知识库文件：${normalized}`); return; }
  state.selectedPath = record.path;
  renderSelectedContext(record);
  elements.contextPanel.classList.add("has-selection");
  if (state.view === "library" || state.view === "overview") updateCardSelection();
  if (openReader) showReader(record);
}

function renderSelectedContext(record) {
  elements.contextTitle.textContent = record.title;
  elements.closeSelection.hidden = false;
  const actions = record.actions.length ? record.actions.slice(0, 4) : [{ text: record.excerpt, done: false }];
  renderContextActions(record, actions);
  elements.timeline.innerHTML = `<div class="timeline-item"><span class="timeline-dot is-accent"></span><div><strong>最后更新</strong><small>${escapeHtml(record.updated)}</small></div></div><div class="timeline-item"><span class="timeline-dot"></span><div><strong>${escapeHtml(record.frontmatter.type || record.category.type)}</strong><small>${escapeHtml(record.path)}</small></div></div>`;
  renderTagCloud(record.tags);
}

function contextActionMarkup(item, record) {
  return `<button class="action-item" data-reader="${escapeHtml(record.path)}" title="${escapeHtml(item.text)}"><span class="task-box ${item.done ? "is-done" : ""}">${item.done ? "✓" : ""}</span><strong>${escapeHtml(item.text || "查看项目详情")}</strong></button>`;
}

function renderContextActions(record, actions) {
  if (!record || !actions.length) {
    elements.actionList.innerHTML = `<p class="muted">连接知识库后显示下一步。</p>`;
    return;
  }
  const visible = actions.slice(0, 2);
  const extra = actions.slice(2);
  elements.actionList.innerHTML = visible.map((item) => contextActionMarkup(item, record)).join("") + (extra.length ? `<details class="action-more"><summary>查看全部 ${actions.length}</summary><div class="action-more-list">${extra.map((item) => contextActionMarkup(item, record)).join("")}</div></details>` : "");
  elements.actionList.querySelectorAll("[data-reader]").forEach((button) => button.addEventListener("click", () => showReader(record)));
}

function renderGlobalContext() {
  if (state.selectedPath) {
    const selected = state.files.find((record) => record.path === state.selectedPath);
    if (selected) { renderSelectedContext(selected); return; }
    state.selectedPath = null;
  }
  elements.contextPanel.classList.remove("has-selection");
  elements.contextTitle.textContent = "当前工作";
  elements.closeSelection.hidden = true;
  const record = currentProjectRecord();
  const actions = (record?.actions || []).filter((item) => !/备份/u.test(item.text)).slice(0, 4);
  renderContextActions(record, actions);
  const recent = filteredRecords("library").slice(0, 3).map((item) => item.record);
  elements.timeline.innerHTML = recent.map((record, index) => `<button class="timeline-item" data-path="${escapeHtml(record.path)}"><span class="timeline-dot${index === 0 ? " is-accent" : ""}"></span><strong>${escapeHtml(record.title)}</strong></button>`).join("");
  elements.timeline.querySelectorAll("[data-path]").forEach((button) => button.addEventListener("click", () => selectRecord(button.dataset.path, true)));
  renderTagCloud([]);
}

function renderTagCloud(tags) {
  const block = elements.tagCloud.closest(".context-block");
  if (block) block.hidden = tags.length === 0;
  elements.tagCloud.innerHTML = tags.map((tag) => `<button data-tag="${escapeHtml(tag)}">#${escapeHtml(tag)}</button>`).join("");
  elements.tagCloud.querySelectorAll("[data-tag]").forEach((button) => button.addEventListener("click", () => {
    state.search = button.dataset.tag;
    state.scope = "library";
    elements.searchInput.value = state.search;
    setView("library", "library");
  }));
}

function collectTopTags(records, limit) {
  const counts = new Map();
  records.forEach((record) => record.tags.forEach((tag) => counts.set(tag, (counts.get(tag) || 0) + 1)));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([tag]) => tag);
}

function renderInline(raw, recordPath) {
  const tokens = [];
  const hold = (html) => { const token = `%%TOKEN_${tokens.length}%%`; tokens.push(html); return token; };
  let prepared = String(raw).replace(/`([^`\n]+)`/gu, (_, code) => hold(`<code>${escapeHtml(code)}</code>`));
  prepared = prepared.replace(/!\[([^\]]*)\]\(([^)\n]+)\)/gu, (_, alt, destination) => hold(renderMedia(alt, destination, recordPath)));
  prepared = prepared.replace(/\[([^\]]+)\]\(([^)\n]+)\)/gu, (_, label, destination) => hold(renderLink(label, destination, recordPath)));
  let html = escapeHtml(prepared)
    .replace(/\*\*([^*\n]+)\*\*/gu, "<strong>$1</strong>")
    .replace(/~~([^~\n]+)~~/gu, "<del>$1</del>")
    .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/gu, "<em>$1</em>");
  tokens.forEach((token, index) => { html = html.replace(`%%TOKEN_${index}%%`, token); });
  return html;
}

function destinationOnly(destination) {
  const trimmed = String(destination).trim();
  if (trimmed.startsWith("<")) return trimmed.match(/^<([^>]+)>/u)?.[1] || trimmed;
  return trimmed.match(/^(\S+)/u)?.[1] || trimmed;
}

function renderLink(label, destination, recordPath) {
  const target = destinationOnly(destination);
  if (/^https?:\/\//iu.test(target)) return `<a href="${escapeHtml(target)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  if (/^(?:mailto:|tel:)/iu.test(target)) return `<a href="${escapeHtml(target)}">${escapeHtml(label)}</a>`;
  if (target.startsWith("#")) return `<a href="${escapeHtml(target)}">${escapeHtml(label)}</a>`;
  const resolved = resolveVaultPath(recordPath, target);
  if (/\.md$/iu.test(resolved)) return `<button class="inline-link" type="button" data-md-path="${escapeHtml(resolved)}">${escapeHtml(label)}</button>`;
  return `<a href="/api/file?path=${encodeURIComponent(resolved)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
}

function renderMedia(alt, destination, recordPath) {
  const target = destinationOnly(destination);
  if (/^https?:\/\//iu.test(target)) return `<a class="external-media" href="${escapeHtml(target)}" target="_blank" rel="noopener noreferrer">打开外部媒体：${escapeHtml(alt || target)}</a>`;
  const resolved = resolveVaultPath(recordPath, target);
  const src = `/api/file?path=${encodeURIComponent(resolved)}`;
  if (/\.(?:mp4|mov|m4v|webm)$/iu.test(resolved)) return `<figure><video controls preload="metadata" src="${src}"></video><figcaption>${escapeHtml(alt || resolved)}</figcaption></figure>`;
  if (/\.(?:mp3|m4a|wav|aac|ogg|flac)$/iu.test(resolved)) return `<figure><audio controls preload="metadata" src="${src}"></audio><figcaption>${escapeHtml(alt || resolved)}</figcaption></figure>`;
  return `<figure><img src="${src}" alt="${escapeHtml(alt)}" loading="lazy" /><figcaption>${escapeHtml(alt || resolved)}</figcaption></figure>`;
}

function* markdownBlocks(markdown, recordPath) {
  const source = String(markdown).replace(/^---[\s\S]*?---\s*/u, "");
  const lines = source.split(/\r?\n/u);
  const isFence = (line) => /^\s*```/u.test(line);
  const isList = (line) => /^\s*(?:[-*+] |\d+[.)、]\s+)/u.test(line);
  const isSpecial = (line, next) => !line.trim() || isFence(line) || /^#{1,6}\s+/u.test(line) || /^\s*>/u.test(line) || /^\s*(?:---+|\*\*\*+)\s*$/u.test(line) || isList(line) || (line.includes("|") && /^\s*\|?\s*:?-{3,}/u.test(next || ""));
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const fence = line.match(/^\s*```\s*([^\s`]*)/u);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !isFence(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      yield `<div class="code-block"><div class="code-toolbar"><span>${escapeHtml(fence[1] || "code")}</span><button type="button" data-copy-code>复制代码</button></div><pre><code>${escapeHtml(code.join("\n"))}</code></pre></div>`;
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}/u.test(lines[index + 1] || "")) {
      const tableLines = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) tableLines.push(lines[index++]);
      const split = (value) => value.trim().replace(/^\||\|$/gu, "").split("|").map((cell) => cell.trim());
      const headers = split(tableLines[0]);
      const rows = tableLines.slice(2).map(split);
      yield `<div class="table-wrap"><table><thead><tr>${headers.map((cell) => `<th>${renderInline(cell, recordPath)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${headers.map((_, cellIndex) => `<td>${renderInline(row[cellIndex] || "", recordPath)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/u);
    if (heading) {
      const level = heading[1].length;
      const title = stripMarkdown(heading[2]);
      const id = `section-${index}-${title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/gu, "")}`;
      yield `<h${level} id="${escapeHtml(id)}">${renderInline(heading[2], recordPath)}</h${level}>`;
      index += 1; continue;
    }
    if (/^\s*(?:---+|\*\*\*+)\s*$/u.test(line)) { yield "<hr>"; index += 1; continue; }
    if (/^\s*>/u.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/u.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/u, ""));
      yield `<blockquote>${quote.map((item) => renderInline(item, recordPath)).join("<br>")}</blockquote>`;
      continue;
    }
    if (isList(line)) {
      const listLines = [];
      while (index < lines.length && isList(lines[index])) listLines.push(lines[index++]);
      const ordered = /^\s*\d+[.)、]\s+/u.test(listLines[0]);
      const hasTasks = listLines.some((item) => /^\s*[-*+]\s+\[[ xX]\]/u.test(item));
      const tag = ordered ? "ol" : "ul";
      yield `<${tag}${hasTasks ? ' class="task-list"' : ""}>${listLines.map((item) => {
        const task = item.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.+)$/u);
        const content = task ? task[2] : item.replace(/^\s*(?:[-*+] |\d+[.)、]\s+)/u, "");
        return `<li>${task ? `<span class="task-box ${/x/iu.test(task[1]) ? "is-done" : ""}">${/x/iu.test(task[1]) ? "✓" : ""}</span>` : ""}${renderInline(content, recordPath)}</li>`;
      }).join("")}</${tag}>`;
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && !isSpecial(lines[index], lines[index + 1])) paragraph.push(lines[index++]);
    yield `<p>${paragraph.map((item) => renderInline(item, recordPath)).join("<br>")}</p>`;
  }
}

function markdownToHtml(markdown, recordPath) {
  return [...markdownBlocks(markdown, recordPath)].join("\n");
}

const fullRecordLoads = new WeakMap();

async function ensureFullRecord(record) {
  if (!record.truncated || !state.serverMode) return record;
  if (fullRecordLoads.has(record)) return fullRecordLoads.get(record);
  const loading = (async () => {
    const response = await fetch(`/api/file?path=${encodeURIComponent(record.path)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`完整内容读取失败（${response.status}）`);
    const text = await response.text();
    Object.assign(record, createRecord(record.path, text,
      { lastModified: record.lastModified, size: record.size },
      { truncated: false, contentHash: record.contentHash }));
    filteredRecordCache.files = null;
    return record;
  })();
  fullRecordLoads.set(record, loading);
  try { return await loading; }
  finally { fullRecordLoads.delete(record); }
}

const readerContentCache = new Map();
let readerCacheCharacters = 0;
const readerTask = () => new Promise((resolve) => window.setTimeout(resolve, 0));

function readerIsCurrent(record, request) {
  return state.readerRequest === request && state.currentReaderPath === record.path && elements.readerDialog.open;
}

async function renderReaderRecord(record, request) {
  const cached = readerContentCache.get(record.path);
  const reusable = cached?.text === record.text;
  const blocks = reusable ? cached.blocks : [];
  const source = reusable ? blocks : markdownBlocks(record.text, record.path);
  let batch = [];
  let started = performance.now();
  let firstBatch = true;
  for (const block of source) {
    if (!readerIsCurrent(record, request)) return;
    if (!reusable) blocks.push(block);
    batch.push(block);
    if (batch.length < 60 && performance.now() - started < 8) continue;
    if (firstBatch) { elements.markdownReader.innerHTML = ""; firstBatch = false; }
    elements.markdownReader.insertAdjacentHTML("beforeend", batch.join("\n"));
    batch = [];
    await readerTask();
    started = performance.now();
  }
  if (!readerIsCurrent(record, request)) return;
  if (firstBatch) elements.markdownReader.innerHTML = "";
  if (batch.length) elements.markdownReader.insertAdjacentHTML("beforeend", batch.join("\n"));
  if (cached) { readerCacheCharacters -= cached.characters; readerContentCache.delete(record.path); }
  const characters = record.text.length + blocks.reduce((total, block) => total + block.length, 0);
  // Keep repeat reads fast without retaining an unbounded second copy of a Vault.
  if (characters <= 4_000_000) {
    readerContentCache.set(record.path, { text: record.text, blocks, characters });
    readerCacheCharacters += characters;
  }
  while (readerContentCache.size > 8 || readerCacheCharacters > 4_000_000) {
    const oldest = readerContentCache.keys().next().value;
    readerCacheCharacters -= readerContentCache.get(oldest).characters;
    readerContentCache.delete(oldest);
  }
}

async function showReader(record) {
  const request = ++state.readerRequest;
  state.currentReaderPath = record.path;
  elements.editCard.hidden = !canEditRecord(record);
  elements.deleteCard.hidden = !canDeleteRecord(record);
  elements.readerType.textContent = record.frontmatter.type || record.category.type;
  elements.readerTitle.textContent = record.title;
  elements.readerPath.textContent = record.path;
  elements.markdownReader.innerHTML = '<div class="reader-loading" role="status">正在读取内容…</div>';
  if (!elements.readerDialog.open) elements.readerDialog.showModal();
  elements.markdownReader.scrollTop = 0;
  try {
    await readerTask();
    if (!readerIsCurrent(record, request)) return;
    await ensureFullRecord(record);
    if (!readerIsCurrent(record, request)) return;
    elements.readerType.textContent = record.frontmatter.type || record.category.type;
    elements.readerTitle.textContent = record.title;
    await renderReaderRecord(record, request);
  } catch (error) {
    if (readerIsCurrent(record, request)) {
      elements.markdownReader.innerHTML = `<div class="person-empty"><strong>完整内容读取失败</strong><p>${escapeHtml(error.message)}</p></div>`;
    }
  }
}

function canDeleteRecord(record) {
  if (!record || !state.serverMode || !record.path.toLocaleLowerCase("en-US").endsWith(".md")) return false;
  const path = normalizePath(record.path);
  return ![
    "AGENTS.md", "DASHBOARD.md", "README.md",
  ].includes(path) && ![
    "00-System/", "AI-Second-Brain-UI/", "98-Skills/", "99-Logs/",
  ].some((prefix) => path.startsWith(prefix));
}

function openDeleteCardDialog() {
  const record = currentReaderRecord();
  if (!record || elements.deleteCard.hidden) return;
  const references = state.files.filter((item) => item.path !== record.path && item.text.includes(record.path));
  const referenceWarning = references.length
    ? `当前有 ${references.length} 个文件引用它：${references.slice(0, 3).map((item) => item.title).join("、")}${references.length > 3 ? "等" : ""}。移除后这些入口会失效，索引不会自动改写。`
    : "未检测到其他 Markdown 的直接路径引用；索引仍不会自动改写。";
  elements.deleteCardSummary.textContent = `将把“${record.title}”移到系统废纸篓：${record.path}。${referenceWarning}`;
  elements.deleteCardDialog.showModal();
}

async function deleteCurrentCard() {
  const record = currentReaderRecord();
  if (!record || elements.deleteCard.hidden) return;
  elements.confirmDeleteCard.disabled = true;
  elements.confirmDeleteCard.textContent = "处理中…";
  try {
    const response = await fetch("/api/delete-card", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: record.path }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "delete_failed");
    elements.deleteCardDialog.close();
    elements.readerDialog.close();
    state.currentReaderPath = null;
    showToast("知识卡已移到系统废纸篓。");
    await readServerVault({ force: true, announce: false });
  } catch (error) {
    showToast(`删除失败：${error.message || "请重启预览窗口后重试"}`);
  } finally {
    elements.confirmDeleteCard.disabled = false;
    elements.confirmDeleteCard.textContent = "移到废纸篓";
  }
}

async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const input = document.createElement("textarea");
    input.value = text; input.style.position = "fixed"; input.style.opacity = "0";
    document.body.append(input); input.select(); document.execCommand("copy"); input.remove();
  }
  showToast(successMessage);
}

function currentReaderRecord() {
  return state.files.find((record) => record.path === state.currentReaderPath);
}

async function revealCurrentFile() {
  const record = currentReaderRecord();
  if (!record) return;
  if (!state.serverMode) {
    await copyText(record.path, "已复制路径；兼容模式无法直接打开 Finder。");
    return;
  }
  try {
    const response = await fetch(`/api/reveal?path=${encodeURIComponent(record.path)}`, { method: "POST" });
    if (!response.ok) throw new Error(String(response.status));
    showToast("已在 Finder 中显示文件。");
  } catch { showToast("Finder 定位失败，路径已保留在阅读器顶部。"); }
}

const ATLAS_COLORS = { projects: "#4f78d7", knowledge: "#168d7c", content: "#d98b3a", prompts: "#8065c9", business: "#cf6f68", skills: "#2f9db2", archive: "#8e8a82", system: "#71808c" };

function hashNumber(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) { hash ^= value.charCodeAt(index); hash = Math.imul(hash, 16777619); }
  return hash >>> 0;
}

function recordRelations(record, records) {
  const targets = new Set();
  for (const match of record.text.matchAll(/\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)/giu)) targets.add(resolveVaultPath(record.path, match[1]));
  [record.frontmatter.related, record.frontmatter.derived_from].forEach((raw) => termList(raw).forEach((value) => {
    const normalized = resolveVaultPath(record.path, value);
    const match = records.find((item) => item.path === normalized || item.title.toLocaleLowerCase("zh-CN") === value.toLocaleLowerCase("zh-CN"));
    if (match) targets.add(match.path);
  }));
  return targets;
}

function buildAtlas(records, width, height) {
  const categories = [...new Set(records.map((record) => record.category.nav))];
  const categoryIndex = new Map(categories.map((category, index) => [category, index]));
  const groups = categories.map((category, group) => {
    const angle = group / Math.max(1, categories.length) * Math.PI * 2 - Math.PI / 2;
    const sample = records.find((record) => record.category.nav === category);
    return {
      category,
      label: sample?.category.type || category,
      x: width * 0.5 + Math.cos(angle) * Math.min(width * 0.17, 190),
      y: height * 0.5 + Math.sin(angle) * Math.min(height * 0.15, 82),
      color: ATLAS_COLORS[category] || "#168d7c",
      count: records.filter((record) => record.category.nav === category).length,
    };
  });
  const groupOffsets = new Map();
  const nodes = records.map((record) => {
    const seed = hashNumber(record.path);
    const group = categoryIndex.get(record.category.nav) || 0;
    const offset = groupOffsets.get(group) || 0;
    groupOffsets.set(group, offset + 1);
    const localAngle = offset * 2.399963 + (seed % 41) / 41 * 0.35;
    const localRadius = 28 + Math.sqrt(offset) * 8.5;
    const groupCenter = groups[group];
    const x = Math.max(20, Math.min(width - 20, groupCenter.x + Math.cos(localAngle) * localRadius));
    const y = Math.max(54, Math.min(height - 20, groupCenter.y + Math.sin(localAngle) * localRadius * 0.62));
    return { record, group, x, y, vx: 0, vy: 0, radius: 4.8, degree: 0, color: ATLAS_COLORS[record.category.nav] || "#168d7c" };
  });
  const indexByPath = new Map(nodes.map((node, index) => [node.record.path, index]));
  const edgeKeys = new Set();
  const edges = [];
  const add = (a, b, kind) => {
    if (a === undefined || b === undefined || a === b) return;
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key); edges.push({ a, b, kind });
  };
  nodes.forEach((node, source) => recordRelations(node.record, records).forEach((path) => add(source, indexByPath.get(path), "reference")));
  const connected = new Set(edges.flatMap((edge) => [edge.a, edge.b]));
  const byTag = new Map();
  nodes.forEach((node, index) => node.record.tags.filter((tag) => tag !== node.record.category.type).slice(0, 3).forEach((tag) => {
    const key = tag.toLocaleLowerCase("zh-CN");
    if (!byTag.has(key)) byTag.set(key, []);
    byTag.get(key).push(index);
  }));
  byTag.forEach((indices) => {
    const anchor = indices.find((index) => connected.has(index)) ?? indices[0];
    indices.filter((index) => !connected.has(index)).slice(0, 4).forEach((index) => { add(anchor, index, "tag"); connected.add(index); });
  });
  edges.forEach((edge) => { nodes[edge.a].degree += 1; nodes[edge.b].degree += 1; });
  nodes.forEach((node) => { node.radius = 4.8 + Math.min(4.8, node.degree * 0.56); });
  return { nodes, edges, groups };
}

function paperScrapPath(context, width, height, seed = 0) {
  const notch = 2 + seed % 4;
  context.beginPath();
  context.moveTo(-width / 2 + notch, -height / 2);
  context.lineTo(width / 2 - 5, -height / 2 + 1);
  context.lineTo(width / 2, -height / 2 + 5 + notch);
  context.lineTo(width / 2 - 2, height / 2 - 4);
  context.lineTo(width / 2 - 7, height / 2);
  context.lineTo(-width / 2 + 4, height / 2 - 1);
  context.lineTo(-width / 2, height / 2 - 5 - notch);
  context.lineTo(-width / 2 + 1, -height / 2 + 5);
  context.closePath();
}

function renderAtlas() {
  if (state.atlasFrame) cancelAnimationFrame(state.atlasFrame);
  state.atlasFrame = null;
  const records = state.files.filter((record) => scopeAllows(record, "library"));
  const width = elements.atlasStage.clientWidth;
  const height = elements.atlasStage.clientHeight;
  const key = makeFingerprint(records);
  elements.atlasEmpty.hidden = records.length > 0;
  elements.knowledgeGraph.hidden = records.length === 0;
  if (key === state.atlasGraphKey && records.length && state.atlasNodes.length) {
    if (width !== state.atlasWidth || height !== state.atlasHeight) {
      const scaleX = width / Math.max(1, state.atlasWidth);
      const scaleY = height / Math.max(1, state.atlasHeight);
      [...state.atlasNodes, ...state.atlasGroups].forEach((node) => { node.x *= scaleX; node.y *= scaleY; });
      state.atlasCamera.x *= scaleX;
      state.atlasCamera.y *= scaleY;
    }
    state.atlasWidth = width; state.atlasHeight = height;
    requestAtlasDraw();
    return;
  }
  state.atlasGraphKey = key;
  state.atlasWidth = width; state.atlasHeight = height;
  elements.atlasNodeList.innerHTML = records.slice().sort((a, b) => a.title.localeCompare(b.title, "zh-CN")).map((record) => `<li><button data-open="${escapeHtml(record.path)}"><span style="--node-color:${ATLAS_COLORS[record.category.nav] || "#168d7c"}"></span><strong>${escapeHtml(record.title)}</strong><small>${escapeHtml(record.category.type)}</small></button></li>`).join("");
  elements.atlasNodeList.querySelectorAll("[data-open]").forEach((button) => button.addEventListener("click", () => activateAtlasNode(button.dataset.open)));
  if (!records.length) { state.atlasNodes = []; state.atlasEdges = []; state.atlasGroups = []; return; }
  const graph = buildAtlas(records, width, height);
  state.atlasNodes = graph.nodes; state.atlasEdges = graph.edges; state.atlasGroups = graph.groups;
  state.atlasSimulationAlpha = state.reduceMotion ? 0 : 1;
  state.atlasLastTime = 0;
  state.atlasCamera = { x: 0, y: 0, scale: 1 };
  const references = graph.edges.filter((edge) => edge.kind === "reference").length;
  elements.atlasStats.innerHTML = `<span><strong>${records.length}</strong> 节点</span><span><strong>${references}</strong> 真实引用</span><span><strong>${graph.edges.length - references}</strong> 标签补边</span>`;
  const counts = new Map();
  records.forEach((record) => counts.set(record.category.nav, { label: record.category.type, count: (counts.get(record.category.nav)?.count || 0) + 1 }));
  elements.atlasLegend.innerHTML = [...counts.entries()].map(([key, item]) => `<span><i style="background:${ATLAS_COLORS[key] || "#168d7c"}"></i>${escapeHtml(item.label)} ${item.count}</span>`).join("");
  requestAtlasDraw();
}

function requestAtlasDraw() {
  if (!state.atlasFrame && state.view === "atlas" && document.visibilityState === "visible") {
    state.atlasFrame = requestAnimationFrame(drawAtlas);
  }
}

function activateAtlasNode(path) {
  const node = state.atlasNodes.find((item) => item.record.path === path);
  if (!node) return;
  state.atlasSelectedPath = path;
  elements.knowledgeGraph.dataset.selectedPath = path;
  if (state.atlasFrame) cancelAnimationFrame(state.atlasFrame);
  state.atlasFrame = null;
  requestAtlasDraw();
  window.setTimeout(() => selectRecord(path, true), state.reduceMotion ? 0 : 140);
}

function atlasWorldToScreen(point, width, height) {
  return BokAtlasPhysics.worldToScreen(point, state.atlasCamera, width, height);
}

function atlasScreenToWorld(point, width, height) {
  return BokAtlasPhysics.screenToWorld(point, state.atlasCamera, width, height);
}

function atlasNodeAt(point, width, height) {
  return state.atlasNodes.find((node) => {
    const screen = atlasWorldToScreen(node, width, height);
    return Math.hypot(screen.x - point.x, screen.y - point.y) <= node.radius * state.atlasCamera.scale + 8;
  });
}

function stepAtlasPhysics(width, height) {
  const result = BokAtlasPhysics.step({
    nodes: state.atlasNodes, edges: state.atlasEdges, groups: state.atlasGroups, alpha: state.atlasSimulationAlpha,
  }, width, height);
  state.atlasSimulationAlpha = result.alpha;
  return result.movement;
}

function changeAtlasZoom(factor, point) {
  const canvas = elements.knowledgeGraph;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const focus = point || { x: width / 2, y: height / 2 };
  const camera = state.atlasCamera;
  const previous = camera.scale;
  const next = Math.max(0.45, Math.min(2.6, previous * factor));
  const world = atlasScreenToWorld(focus, width, height);
  camera.scale = next;
  camera.x = focus.x - width / 2 - (world.x - width / 2) * next;
  camera.y = focus.y - height / 2 - (world.y - height / 2) * next;
  requestAtlasDraw();
}

function resetAtlasCamera() {
  state.atlasCamera = { x: 0, y: 0, scale: 1 };
  requestAtlasDraw();
}

function drawAtlas(time = 0) {
  state.atlasFrame = null;
  if (state.view !== "atlas" || document.visibilityState !== "visible") return;
  const canvas = elements.knowledgeGraph;
  const context = canvas.getContext("2d");
  const width = canvas.clientWidth; const height = canvas.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) { canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); }
  context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
  const animate = !state.reduceMotion && document.visibilityState === "visible" && !elements.readerDialog.open;
  const movement = animate && state.atlasSimulationAlpha > 0.012 ? stepAtlasPhysics(width, height) : 0;
  const hoveredNode = atlasNodeAt(state.atlasPointer, width, height);
  const hoveredIndex = hoveredNode ? state.atlasNodes.indexOf(hoveredNode) : -1;
  const selectedIndex = state.atlasNodes.findIndex((node) => node.record.path === state.atlasSelectedPath);
  const focusIndex = hoveredIndex >= 0 ? hoveredIndex : selectedIndex;
  const connected = new Set();
  if (focusIndex >= 0) {
    connected.add(focusIndex);
    state.atlasEdges.forEach((edge) => {
      if (edge.a === focusIndex) connected.add(edge.b);
      if (edge.b === focusIndex) connected.add(edge.a);
    });
  }
  context.save();
  context.translate(width / 2 + state.atlasCamera.x, height / 2 + state.atlasCamera.y);
  context.scale(state.atlasCamera.scale, state.atlasCamera.scale);
  context.translate(-width / 2, -height / 2);
  state.atlasEdges.forEach((edge) => {
    const first = state.atlasNodes[edge.a]; const second = state.atlasNodes[edge.b];
    const active = focusIndex < 0 || connected.has(edge.a) && connected.has(edge.b);
    context.globalAlpha = active ? 1 : 0.06;
    context.strokeStyle = edge.kind === "reference" ? "rgba(28,53,58,.34)" : "rgba(70,91,96,.15)";
    context.lineWidth = edge.kind === "reference" ? 1.15 : 0.7;
    context.setLineDash([]);
    context.beginPath();
    context.moveTo(first.x, first.y);
    context.lineTo(second.x, second.y);
    context.stroke();
  });
  const labelBoxes = [];
  state.atlasNodes.forEach((node, index) => {
    const active = index === hoveredIndex;
    const selected = index === selectedIndex;
    context.globalAlpha = focusIndex < 0 || connected.has(index) ? 1 : 0.13;
    if (selected) {
      context.strokeStyle = "rgba(255,62,145,.72)";
      context.lineWidth = 2.2;
      context.beginPath(); context.arc(node.x, node.y, node.radius + 7, 0, Math.PI * 2); context.stroke();
    }
    if (active) {
      context.fillStyle = `${node.color}22`;
      context.beginPath(); context.arc(node.x, node.y, node.radius + 10, 0, Math.PI * 2); context.fill();
    }
    context.fillStyle = node.color;
    context.strokeStyle = "#fffaf0";
    context.lineWidth = active ? 3 : 1.7;
    context.beginPath(); context.arc(node.x, node.y, active ? node.radius + 2 : node.radius, 0, Math.PI * 2); context.fill(); context.stroke();
    if (node.degree >= 4 || active || selected) {
      context.font = `${active || selected ? 10 : 8.5}px "Microsoft YaHei UI", sans-serif`;
      context.textAlign = "center";
      const label = node.record.title.length > 12 ? `${node.record.title.slice(0, 12)}…` : node.record.title;
      const labelWidth = context.measureText(label).width;
      const box = { left: node.x - labelWidth / 2 - 3, right: node.x + labelWidth / 2 + 3, top: node.y + node.radius + 5, bottom: node.y + node.radius + 18 };
      const overlaps = labelBoxes.some((item) => box.left < item.right && box.right > item.left && box.top < item.bottom && box.bottom > item.top);
      if (!overlaps || active || selected) {
        context.fillStyle = "#172b2d";
        context.fillText(label, node.x, node.y + node.radius + 14);
        labelBoxes.push(box);
      }
    }
  });
  context.restore();
  context.globalAlpha = 1;
  const hovered = hoveredIndex >= 0 ? state.atlasNodes[hoveredIndex] : null;
  if (hovered) {
    const screen = atlasWorldToScreen(hovered, width, height);
    elements.atlasTooltip.hidden = false;
    elements.atlasTooltip.innerHTML = `<strong>${escapeHtml(hovered.record.title)}</strong><span>${escapeHtml(hovered.record.category.type)} · ${hovered.degree} 个关联</span>`;
    elements.atlasTooltip.style.left = `${Math.max(12, Math.min(width - 230, screen.x + 14))}px`;
    elements.atlasTooltip.style.top = `${Math.max(52, Math.min(height - 72, screen.y - 22))}px`;
    canvas.style.cursor = "pointer";
  } else {
    elements.atlasTooltip.hidden = true;
    canvas.style.cursor = state.atlasDrag ? "grabbing" : "grab";
  }
  canvas.dataset.zoom = state.atlasCamera.scale.toFixed(3);
  const keepAnimating = animate && (state.atlasSimulationAlpha > 0.02 || movement > 0.025);
  state.atlasFrame = keepAnimating ? requestAnimationFrame(drawAtlas) : null;
}

function formatTime(date) {
  if (!date) return "尚未同步";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

let toastTimer;
function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => elements.toast.classList.remove("is-visible"), 3000);
}

window.showNativeVaultError = (message) => showToast(String(message || "无法切换知识库。"));

function clearConditions() {
  state.search = ""; state.scope = "library"; state.visibleCardCount = CONFIG.cardPageSize;
  elements.searchInput.value = "";
  if (state.view === "overview") renderCards(); else setView("library", "library");
}

elements.connectVault.addEventListener("click", connectVault);
elements.manualRefresh.addEventListener("click", () => state.serverMode ? readServerVault({ force: true, announce: true }) : refreshVault({ force: true, announce: true }));
elements.healthRefresh.addEventListener("click", () => state.serverMode ? readServerVault({ force: true, announce: true }) : refreshVault({ force: true, announce: true }));
elements.memoryRefresh.addEventListener("click", () => loadMemoryWorkspace({ announce: true }));
[elements.memoryQuickNote, elements.memoryNewNote, elements.quickNoteLauncher].forEach((button) => button.addEventListener("click", openQuickNoteWindow));
elements.quickNoteValue.addEventListener("input", () => {
  writeQuickNoteDraft(elements.quickNoteValue.value);
  updateQuickNoteMeta();
});
elements.quickNoteValue.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    saveQuickNote();
  }
});
elements.quickNoteForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  saveQuickNote();
});
elements.memoryTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-memory-tab]");
  if (button) setMemoryTab(button.dataset.memoryTab);
});
elements.memoryTabs.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...elements.memoryTabs.querySelectorAll("[data-memory-tab]")];
  const current = tabs.indexOf(document.activeElement);
  let next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : current + (event.key === "ArrowRight" ? 1 : -1);
  next = (next + tabs.length) % tabs.length;
  event.preventDefault();
  setMemoryTab(tabs[next].dataset.memoryTab);
  tabs[next].focus();
});
elements.memorySearchForm.addEventListener("submit", (event) => { event.preventDefault(); runMemorySearch(); });
elements.memorySearchScope.addEventListener("click", (event) => {
  const button = event.target.closest("[data-memory-scope]");
  if (!button) return;
  state.memorySearchScope = button.dataset.memoryScope;
  elements.memorySearchScope.querySelectorAll("[data-memory-scope]").forEach((item) => item.classList.toggle("is-active", item === button));
  elements.memorySearchMeta.textContent = state.memorySearchScope === "all" ? "将搜索完整知识库，包括系统说明和归档。" : "优先搜索日常知识；需要时再切换“搜索全部”。";
});
elements.memoryView.addEventListener("click", async (event) => {
  const backgroundControl = event.target.closest("[data-background-control]");
  if (backgroundControl) {
    event.preventDefault();
    const controls = elements.memorySettingsGrid.querySelectorAll("[data-background-control]");
    controls.forEach((button) => { button.disabled = true; });
    const choices = { pause: { paused: true }, resume: { paused: false }, light: { batch_limit: 2, interval_seconds: 60 }, standard: { batch_limit: 4, interval_seconds: 30 } };
    try {
      await bokRequest("background", { method: "POST", body: choices[backgroundControl.dataset.backgroundControl] });
      await loadMemoryWorkspace();
      showToast("后台整理设置已保存。");
    } catch (error) { showToast(`设置未更新：${error.message}`); }
    finally { controls.forEach((button) => { button.disabled = false; }); }
    return;
  }
  const connectCodex = event.target.closest("[data-connect-codex]");
  if (connectCodex) {
    event.preventDefault();
    const original = connectCodex.textContent;
    connectCodex.disabled = true;
    connectCodex.textContent = "正在连接…";
    try {
      const response = await fetch("/api/native/connect-codex", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.ok !== true) throw new Error(result.message || "连接没有完成。");
      connectCodex.textContent = "已连接";
      showToast(result.message || "Codex 已连接 Bok。");
    } catch (error) {
      connectCodex.textContent = original;
      connectCodex.disabled = false;
      showToast(error.message || "连接 Codex 失败。");
    }
    return;
  }
  const action = event.target.closest("[data-memory-action]");
  if (action) {
    event.preventDefault();
    await runMemoryInlineAction(action.dataset.memoryAction, action);
    return;
  }
  const tab = event.target.closest("[data-memory-tab-go]");
  if (tab) {
    setMemoryTab(tab.dataset.memoryTabGo);
    elements.memoryTabs.querySelector(`[data-memory-tab="${tab.dataset.memoryTabGo}"]`)?.focus();
    return;
  }
  const source = event.target.closest("[data-memory-open]");
  if (!source) return;
  const record = state.files.find((item) => item.path === source.dataset.memoryOpen);
  if (record) showReader(record);
  else showToast("来源刚刚发生变化，请刷新工作台后再打开。");
});
elements.memoryActionForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  submitMemoryAction();
});
elements.memoryCreateBackup.addEventListener("click", createMemoryBackup);
elements.memoryCreatePersonalBackup.addEventListener("click", createPersonalMemoryBackup);
elements.backupRestoreForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  restoreMemoryBackup();
});
elements.editCard.addEventListener("click", openDocumentEditor);
elements.documentEditForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  saveDocumentEditor();
});
elements.documentEditValue.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase("en-US") === "s") {
    event.preventDefault();
    saveDocumentEditor();
  }
});
elements.personRefresh.addEventListener("click", () => loadPersonDashboard({ announce: true }));
elements.personProcess.addEventListener("click", async () => {
  if (state.personActionRunning) return;
  state.personActionRunning = true;
  elements.personProcess.disabled = true;
  const label = elements.personProcess.textContent;
  elements.personProcess.textContent = "正在整理…";
  try {
    const result = await bokRequest("person/observations/process", { method: "POST", body: { limit: 500 }, idempotency: `ui-person-process-${Date.now()}` });
    await loadPersonDashboard();
    showToast(`已整理 ${result.processed || 0} 条观察，形成 ${result.learned || 0} 条可用理解；需介入项 ${Math.max(0, Number(result.projected || 0) - Number(result.learned || 0))} 条。`);
  } catch (error) { showToast(error.message); }
  finally {
    state.personActionRunning = false;
    elements.personProcess.disabled = false;
    elements.personProcess.textContent = label;
  }
});
elements.personTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-person-tab]");
  if (!button) return;
  state.personTab = button.dataset.personTab;
  renderPersonTab();
});
elements.personTabs.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...elements.personTabs.querySelectorAll("[data-person-tab]")];
  const current = tabs.indexOf(document.activeElement);
  let next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : current + (event.key === "ArrowRight" ? 1 : -1);
  next = (next + tabs.length) % tabs.length;
  event.preventDefault();
  state.personTab = tabs[next].dataset.personTab;
  renderPersonTab();
  tabs[next].focus();
});
elements.personView.addEventListener("click", (event) => {
  const button = event.target.closest("[data-person-action]");
  if (button) runPersonAction(button.dataset.personAction, button);
});
elements.personEditForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  submitPersonEdit();
});
elements.personConfirmForm.addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  submitPersonConfirmation();
});
elements.personEditDialog.addEventListener("close", () => {
  elements.personEditValue.readOnly = false;
  elements.personEditConfirm.hidden = false;
});
elements.cleanupNow.addEventListener("click", openCleanupDialog);
elements.confirmCleanup.addEventListener("click", (event) => { event.preventDefault(); executeCleanup(); });
elements.folderFallback.addEventListener("change", async (event) => { state.rootHandle = null; state.fallbackFiles = [...event.target.files]; await refreshVault({ force: true, announce: true }); });
elements.loadMore.addEventListener("click", () => { state.visibleCardCount += CONFIG.cardPageSize; renderCards(); });
elements.viewAllCards.addEventListener("click", () => setView("library", "library"));
elements.navList.addEventListener("click", (event) => {
  const button = event.target.closest(".nav-item");
  if (button) setView(button.dataset.view, button.dataset.scope);
});
elements.filterRow.addEventListener("click", (event) => {
  const button = event.target.closest("[data-scope]");
  if (!button) return;
  state.scope = button.dataset.scope; state.visibleCardCount = CONFIG.cardPageSize;
  setView(state.view === "overview" && state.scope === "library" ? "overview" : "library", state.scope);
});
elements.searchInput.addEventListener("input", (event) => {
  state.search = event.target.value;
  state.visibleCardCount = CONFIG.cardPageSize;
  if (state.searchFrame) cancelAnimationFrame(state.searchFrame);
  state.searchFrame = requestAnimationFrame(() => {
    state.searchFrame = null;
    renderCards();
  });
});
elements.clearFilters.addEventListener("click", clearConditions);
document.querySelectorAll("[data-clear-filters]").forEach((button) => button.addEventListener("click", clearConditions));
document.querySelectorAll("[data-go-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.goView)));
elements.closeSelection.addEventListener("click", () => { state.selectedPath = null; updateCardSelection(); renderGlobalContext(); });
elements.closeReader.addEventListener("click", () => elements.readerDialog.close());
elements.readerDialog.addEventListener("close", () => {
  state.readerRequest += 1;
  elements.markdownReader.querySelectorAll("video, audio").forEach((media) => media.pause());
  if (state.view !== "atlas") return;
  requestAtlasDraw();
});
elements.readerDialog.addEventListener("click", (event) => {
  if (event.target === elements.readerDialog) elements.readerDialog.close();
});
elements.markdownReader.addEventListener("click", (event) => {
  const internal = event.target.closest("[data-md-path]");
  if (internal) { selectRecord(internal.dataset.mdPath, true); return; }
  const copy = event.target.closest("[data-copy-code]");
  if (copy) copyText(copy.closest(".code-block")?.querySelector("code")?.textContent || "", "代码已复制。");
});
elements.copyCodex.addEventListener("click", () => {
  const record = currentReaderRecord();
  if (record) copyText(`请读取并基于知识库文件 \`${record.path}\` 继续工作。\n\n目标：${record.title}`, "已复制给 Codex 的任务指令。");
});
elements.copyPath.addEventListener("click", () => { const record = currentReaderRecord(); if (record) copyText(record.path, "文件路径已复制。"); });
elements.revealFile.addEventListener("click", revealCurrentFile);
elements.deleteCard.addEventListener("click", openDeleteCardDialog);
elements.confirmDeleteCard.addEventListener("click", (event) => { event.preventDefault(); deleteCurrentCard(); });
elements.knowledgeGraph.addEventListener("pointerdown", (event) => {
  const bounds = elements.knowledgeGraph.getBoundingClientRect();
  const point = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  state.atlasPointer = point;
  if (atlasNodeAt(point, elements.knowledgeGraph.clientWidth, elements.knowledgeGraph.clientHeight)) return;
  state.atlasSuppressClick = false;
  state.atlasDrag = { startX: point.x, startY: point.y, cameraX: state.atlasCamera.x, cameraY: state.atlasCamera.y, moved: false };
  elements.knowledgeGraph.setPointerCapture?.(event.pointerId);
  elements.knowledgeGraph.style.cursor = "grabbing";
});
elements.knowledgeGraph.addEventListener("pointermove", (event) => {
  const bounds = elements.knowledgeGraph.getBoundingClientRect();
  const point = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  if (state.atlasDrag) {
    const deltaX = point.x - state.atlasDrag.startX;
    const deltaY = point.y - state.atlasDrag.startY;
    state.atlasDrag.moved ||= Math.hypot(deltaX, deltaY) > 3;
    state.atlasSuppressClick = state.atlasDrag.moved;
    state.atlasCamera.x = state.atlasDrag.cameraX + deltaX;
    state.atlasCamera.y = state.atlasDrag.cameraY + deltaY;
    state.atlasPointer = { x: -9999, y: -9999 };
  } else state.atlasPointer = point;
  requestAtlasDraw();
});
const finishAtlasDrag = (event) => {
  if (!state.atlasDrag) return;
  if (elements.knowledgeGraph.hasPointerCapture?.(event.pointerId)) elements.knowledgeGraph.releasePointerCapture(event.pointerId);
  state.atlasSuppressClick = state.atlasDrag.moved;
  state.atlasDrag = null;
  elements.knowledgeGraph.style.cursor = "grab";
};
elements.knowledgeGraph.addEventListener("pointerup", finishAtlasDrag);
elements.knowledgeGraph.addEventListener("pointercancel", (event) => { finishAtlasDrag(event); state.atlasSuppressClick = false; });
elements.knowledgeGraph.addEventListener("pointerleave", () => {
  if (state.atlasDrag) return;
  state.atlasPointer = { x: -9999, y: -9999 };
  requestAtlasDraw();
});
elements.knowledgeGraph.addEventListener("click", () => {
  if (state.atlasSuppressClick) { state.atlasSuppressClick = false; return; }
  const node = atlasNodeAt(state.atlasPointer, elements.knowledgeGraph.clientWidth, elements.knowledgeGraph.clientHeight);
  if (node) activateAtlasNode(node.record.path);
});
elements.knowledgeGraph.addEventListener("wheel", (event) => {
  event.preventDefault();
  const bounds = elements.knowledgeGraph.getBoundingClientRect();
  changeAtlasZoom(event.deltaY < 0 ? 1.12 : 0.89, { x: event.clientX - bounds.left, y: event.clientY - bounds.top });
}, { passive: false });
elements.atlasZoomOut.addEventListener("click", () => changeAtlasZoom(0.82));
elements.atlasReset.addEventListener("click", resetAtlasCamera);
elements.atlasZoomIn.addEventListener("click", () => changeAtlasZoom(1.22));
elements.personGraph.addEventListener("pointermove", (event) => {
  const bounds = elements.personGraph.getBoundingClientRect();
  state.personGraphPointer = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  if (!state.personGraphFrame) state.personGraphFrame = requestAnimationFrame(() => {
    state.personGraphFrame = null;
    drawPersonGraph();
  });
});
elements.personGraph.addEventListener("pointerleave", () => {
  state.personGraphPointer = { x: -9999, y: -9999 };
  if (!state.personGraphFrame) state.personGraphFrame = requestAnimationFrame(() => {
    state.personGraphFrame = null;
    drawPersonGraph();
  });
});
elements.personGraph.addEventListener("click", () => {
  const node = state.personGraphNodes.find((item) => item.kind === "claim" && personGraphHit(item, state.personGraphPointer, 8));
  if (node) revealPersonClaim(node.id);
});
let nativeScrollFrame = null;
let nativeScrollIdleTimer = null;
document.addEventListener("scroll", () => {
  if (!state.nativeShell) return;
  if (!nativeScrollFrame) {
    nativeScrollFrame = requestAnimationFrame(() => {
      nativeScrollFrame = null;
      document.documentElement.classList.add("native-scrolling");
    });
  }
  if (nativeScrollIdleTimer) window.clearTimeout(nativeScrollIdleTimer);
  nativeScrollIdleTimer = window.setTimeout(() => {
    document.documentElement.classList.remove("native-scrolling");
    nativeScrollIdleTimer = null;
  }, 120);
}, { capture: true, passive: true });
window.addEventListener("resize", () => {
  if (state.resizeFrame) cancelAnimationFrame(state.resizeFrame);
  state.resizeFrame = requestAnimationFrame(() => {
    state.resizeFrame = null;
    if (state.view === "atlas") renderAtlas();
    if (state.view === "person" && state.personTab === "graph") renderPersonGraph();
  });
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    if (state.serverMode) readServerVault().catch(() => setSyncState("error", "同步暂停"));
    else if (state.rootHandle) refreshVault();
  }
  if (state.view !== "atlas") return;
  if (document.visibilityState === "visible") {
    requestAtlasDraw();
  }
  else if (state.atlasFrame) { cancelAnimationFrame(state.atlasFrame); state.atlasFrame = null; }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLocaleLowerCase("en-US") === "n") {
    event.preventDefault();
    openQuickNoteWindow();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("en-US") === "k") { event.preventDefault(); elements.searchInput.focus(); }
  if (event.key === "Escape" && state.selectedPath && !elements.readerDialog.open) { state.selectedPath = null; updateCardSelection(); renderGlobalContext(); }
});

const platformName = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent;
elements.searchShortcut.textContent = /win/iu.test(platformName) ? "Ctrl K" : "⌘ K";
setSyncState("idle", "正在连接");
renderView();
bootstrapLocalServer().then((connected) => { if (!connected) setSyncState("idle", "等待连接"); });
