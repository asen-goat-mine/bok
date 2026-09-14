# Changelog

## Unreleased

- Protect concurrent document creation/moves and verify rollback snapshot integrity before restoring.
- Keep startup responsive during slow indexing, avoid reverse DNS during loopback binding, expose recoverable retry states, and reject stale native startup events.
- Reuse unchanged preview records, batch long-document rendering, preserve existing media cards while loading more, and reduce graph simulation work.
- Respect path/tag filters in semantic search and route generic resume requests to the current focus project.
- Add persistent background pause/resume and light/standard controls. Automatic processing defaults to four items per queue per round, with a 30-second interval. A pause takes effect after the active batch; queued content remains local.
- New configurations no longer start Ollama automatically; existing explicit configuration values are preserved.
- Add synthetic regression coverage for storage races, startup, retrieval, cache behavior, long readers and background settings, plus native Rust checks in CI.


## 0.6.0 — 2026-08-25

First public release.

- Local-first Markdown memory core, CLI, MCP server, and authenticated loopback Memory API.
- Quiet conversation observation, project resume, knowledge capture, quick notes, and Personal Core.
- Traceable claims, versions, impacts, cleanup, recovery, and complete forgetting.
- Hybrid keyword and metadata retrieval with optional embedding support.
- Native macOS shell and Windows source build pipeline.
- Visual knowledge, project, graph, health, and personal-memory workspaces.
- File-allowlisted share builds, privacy scans, and cross-platform contract tests.

---

## 0.6.0 — 2026-08-25（中文）

首个公开版本。

- 本地优先的 Markdown 记忆内核、CLI、MCP 与认证 loopback Memory API。
- 安静对话观察、项目续接、知识沉淀、随手记与 Personal Core。
- 可追溯的理解、版本、影响、清理、恢复与彻底遗忘。
- 关键词和元数据混合检索，并支持可选 Embedding。
- macOS 原生壳与 Windows 源码构建链路。
- 知识、项目、图谱、健康与个人记忆可视化工作台。
- 白名单分享构建、隐私扫描与跨平台合同测试。
