# Bok Memory API v1

## 基本约定

- 地址：`http://127.0.0.1:8771/v1`
- 只允许 loopback 绑定和访问。
- 每个请求必须有 `Authorization: Bearer <token>`。
- JSON 请求最大 1 MiB。
- 错误格式：`{"error":{"code":"...","message":"...","details":{}}}`。
- 创建、提交、回滚、写入、移动、回收和备份支持 `Idempotency-Key`；相同键和相同请求返回原结果，不同请求返回 `409`。
- 普通限流为单客户端每分钟 240 次。
- 客户端不应读取或写入 `.bok/`，只通过 API 操作运行状态。
- 现有预览 UI 使用 `/api/bok/v1/...` 同源桥接；浏览器不接触 Token。直接客户端仍使用 `http://127.0.0.1:8771/v1`。

## 读取接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 服务、索引、Provider、Local Only、收件箱和能力 |
| GET | `/today` | 当前项目、待关注、最近自动操作和 Quiet Mode 状态 |
| GET | `/memory/inbox?status=pending` | 记忆提议，不返回原始捕获正文 |
| GET | `/memory/captures?id=...` | 后台捕获状态，不返回原始正文 |
| GET | `/conversations/status?event_id=...` | 对话收据和下游捕获状态，不返回原始正文 |
| GET | `/person/health` | Personal Core 配置、就绪、状态计数和断链/损坏计数，不返回绝对路径 |
| GET | `/person/dashboard` | 已形成理解、受保护事项、证据、Outcome、影响、时间线、权限和清理总览；仅管理员 |
| GET | `/person/observations?status=all` | Observation Ledger 列表；仅管理员 |
| GET | `/person/cleanup` | 垃圾记忆清理候选；仅管理员 |
| GET | `/person/claims?id=...` | 读取单条 Claim；不传 ID 时按状态、类型和 limit 列表 |
| GET | `/person/claims/explain?id=...` | Claim 的来源、历史、范围和有效原因 |
| GET | `/person/versions?claim_id=...` | Claim 版本元数据与是否支持独立回滚 |
| GET | `/person/backups` | Personal Core 独立备份列表和校验状态；仅管理员 |
| GET | `/agents` | 脱敏的 Agent 凭证、范围与撤销状态；仅管理员 |
| GET | `/quick-notes` | 随手记列表与哈希 |
| GET | `/documents/read?path=...` | 文本、frontmatter、派生类型/更新时间和当前内容哈希 |
| GET | `/versions?path=...` | 文件版本元数据 |
| GET | `/activity` | 不含正文的最近操作 |

## 自动后台处理（仅管理员）

`GET /background` 读取当前 Vault 的自动处理设置；`POST /background` 合并更新，字段可省略。Agent 凭证不能调用此接口。浏览器可通过现有同源桥接调用，写入仍要求同源证明。

```json
{"paused": false, "batch_limit": 4, "interval_seconds": 30}
```

- `paused` 必须是布尔值；暂停在当前批次结束后生效。
- `batch_limit` 是每个队列每轮的上限，整数 1～20；`interval_seconds` 为两轮之间的间隔，整数 15～600。
- 默认值为以上示例。只更新批次/间隔不会恢复已暂停的处理。
- 新内容仍可捕获并留在本地队列；管理员手动处理接口不受自动暂停影响。
- 设置损坏时自动处理暂停，管理员可以重新提交有效设置恢复。

## 检索接口

### `POST /search`

```json
{
  "query": "Bok 下一步",
  "limit": 6,
  "token_budget": 2500,
  "path_prefix": "02-Projects",
  "tags": ["Bok"],
  "scope": "default",
  "semantic": true,
  "cloud_consent": false
}
```

返回段落、分数、命中原因、路径、标题、heading、标签、类型、更新时间和 Token 估算。`scope=default` 跳过已配置的低频工程/归档前缀以保持日常速度；`scope=all` 搜索完整目录，功能与数据不减少。显式 `path_prefix` 指向延后目录时会自动使用全部范围。

本地 Embedding 在规模预算内对当前范围执行完整语义候选召回，可找回零关键词重叠结果；云端 Embedding 只重排少量关键词候选，避免把整个 Vault 外发。Embedding 未配置或不可用时自动降级为本地关键词/结构检索，不阻断请求。结果优先覆盖不同文档，相关来源不足时才回填同一长文的更多章节。

### `POST /context`

与搜索相同，但额外返回 `[S1]` 格式的最小上下文和引用列表。

### `POST /sources`

只返回检索依据，适合“为什么召回这条”界面。

### `POST /project/resume`

```json
{"path":"02-Projects/bok-second-brain-product-strategy.md","token_budget":1600}
```

路径留空时读取 `00-System/Active-Context.md` 的 `focus_path`。

## 安静记忆接口

### `POST /conversations/observe`

Agent 和聊天客户端的推荐入口。Bok 先原子保存逐轮收据，再把符合策略的用户正文交给后台记忆队列，立即返回 `202`。

```json
{
  "conversation_id": "chat-123",
  "turn_id": "turn-456",
  "role": "user",
  "content": "本轮新增内容",
  "memory_mode": "default",
  "external_content": false,
  "client": "boujoy-harness",
  "agent": "coding-agent",
  "project": "02-Projects/bok-second-brain-product-strategy.md",
  "personal_signals": [],
  "cloud_consent": false
}
```

- `(conversation_id, turn_id)` 是天然幂等键；相同轮次重放返回同一结果，不同正文或策略返回 `409`。
- `memory_mode=default` 允许后台判断；`session_only` 只进入短期缓冲；`do_not_remember` 不保存正文。
- `external_content=true` 表示网页、文档、附件或转发材料，不能直接形成用户身份和偏好。
- `personal_signals` 最多 8 条，只允许客户端传递清楚的 explicit/observed 支持或反证信号；不得让模型为了凑数编造。一次 observed 行为不会直接成为可用偏好。
- 公开响应和状态接口不返回 `content`、请求指纹或云端授权标记。

### `POST /conversations/reconcile`

补投已经落账、但在进入下游捕获队列前中断的用户事件。Bok 启动时也会执行一次有上限的自动补投。

完整契约见 [Personal Operating Model v1](PERSON-MODEL-v1.md)。

### `POST /memory/capture`

兼容已有客户端的低层入口。先写入本地分片队列并立即返回 `202`，大模型在后台判断。新聊天客户端优先使用 `/conversations/observe`，因为它额外提供逐轮收据、入口隔离和崩溃补投。

```json
{
  "material": "需要判断的本轮新增内容",
  "source": {"type":"conversation","ref":"turn-123"},
  "cloud_consent": false
}
```

### `POST /memory/process`

手动处理队列；常驻服务会自动调用。

### `POST /memory/propose`

同步高级接口，会等待模型返回。普通交互不要使用它。

### `POST /memory/commit`

```json
{"proposal_id":"...","confirm_important":true}
```

只有重要、冲突、敏感、低置信或目标重要的记忆需要 `confirm_important`。

### `POST /memory/reject` / `POST /memory/rollback`

拒绝未提交提议，或撤销已提交版本。重要/冲突/敏感/低置信提议的回滚同样必须传 `confirm_important: true`。

## Personal Core 与 Claim

### `POST /person/setup`

只允许受信本机 API 或 CLI；现有浏览器同源 bridge 明确拒绝此路由。目标必须是项目 Vault 和 Git 仓库之外的绝对路径，并且是空目录或已有 Bok Personal Core。

```json
{
  "path": "/absolute/path/Bok-Personal-Core",
  "confirm": true
}
```

返回配置状态和目录名，不返回绝对路径。未配置时其他 Personal Claim 写接口返回 `503 personal_core_not_configured`。

### `POST /person/claims/propose`

```json
{
  "statement": "回答先给结论，再给必要依据。",
  "claim_type": "communication_preference",
  "scope_kind": "global",
  "scope_value": "",
  "confidence": 1.0,
  "sensitivity": "private",
  "source_refs": ["conversation:chat-123:turn-4"],
  "expires_at": ""
}
```

返回 `explicit`，不会立即进入 Agent 上下文。相同当前表述、已拒绝表述或被纠正的历史表述只返回原 Claim，不创建重复文件。

### Claim 状态动作

- `POST /person/claims/confirm`：`claim_id` 和可选 `source_ref`；只确认内容，结果保持 `personal-core`。
- `POST /person/claims/authorize`：`claim_id`、必填 `access_scope` 和可选 `source_ref`；仅有效的 `learned` 或已确认 Claim 可调用。传 `personal-core` 收回显式 Agent 授权。
- `POST /person/claims/correct`：`claim_id`、新 `statement`、必填 `source_ref`，可选适用范围；不修改访问授权。
- `POST /person/claims/reject`：`claim_id`、必填 `reason`、可选 `source_ref`。
- `POST /person/claims/forget`：`claim_id` 和 `confirm_forget: true`。会清除该 Claim、关联证据/影响/结果、版本、命中的私人备份、仍保留的来源对话正文及相关幂等缓存，不能撤销。若同一回合已另行提交为项目知识卡，响应通过 `derived_memory_requiring_review` 返回路径，不静默删除另一事实源。
- `POST /person/claims/supersede`：`claim_id`、新 `statement`、必填 `source_ref`，可选范围。
- `POST /person/claims/rollback`：`version_id` 和 `confirm_important: true`；有替代关系的 Claim 禁止单边回滚。

所有状态动作支持 HTTP `Idempotency-Key`。系统不会伪造 `confirmed`：普通低风险理解只会进入可追溯、可纠正、可遗忘的 `learned`；身份、长期目标、授权规则、敏感、冲突和低置信判断仍没有自动生效路径。确认接口也没有隐式授权路径。

### `POST /person/context`

```json
{
  "task": "修改一个已有产品的回答风格",
  "agent": "codex",
  "project": "02-Projects/example.md",
  "limit": 6,
  "token_budget": 1500
}
```

只返回当前任务和范围命中的有效 Claim，以 `[P1]` 引用组装最小上下文：低风险 `learned` 可由受信本机进程按任务使用；受保护的 confirmed Claim 仍需显式 Agent/项目授权。管理员可指定 `agent`；独立 Agent token 会由服务端绑定自己的 agent ID，不能在请求体冒充其他 Agent。

### Observation、影响、Outcome 与清理

- `POST /person/observations/process`：仅处理尚未投影的新证据；仅管理员。
- `POST /person/impacts`：记录 `answer_ref`、任务、Agent、项目和实际使用的 `claim_ids`，不保存回答正文；需要 `impact:write`。
- `POST /person/outcomes`：记录 positive / negative / neutral、评分、返工和短注释；需要 `outcome:write`。
- `POST /person/cleanup`：对候选执行 `dismiss / keep / expire`；重要记忆的过期必须 `confirm_important: true`，仅管理员。

## 随手记

- `POST /quick-notes`：创建一条一文件 Markdown。
- `POST /quick-notes/promote`：把正文送入非阻断记忆队列。
- `POST /quick-notes/archive`：更新状态，需要当前 `expected_hash`。

## 外部采集

- `POST /web-clips`：保存标题、URL、正文和标签到 `01-Inbox/Web-Clips/`，不调用模型。
- `POST /import/markdown`：导入 Markdown 到指定可写路径或自动生成的 `01-Inbox/Imports/` 路径；目标已存在时拒绝覆盖。

## 文档与版本

- `POST /documents/write`：新建或写入；编辑已有文件必须传 `expected_hash`。
- `POST /documents/move`：移动/重命名；必须传源哈希。
- `POST /documents/trash`：可恢复回收；必须传哈希。
- `POST /documents/rollback`：按 `version_id` 回退；涉及重要卡片时必须传 `confirm_important: true`。

重要卡片的写入、移动、回收和回滚必须额外传 `confirm_important: true`。

## 备份

- `POST /backups/create`
- `POST /backups/verify`
- `POST /backups/restore`

恢复要求 `confirm_vault` 精确等于当前 Vault 文件夹名，并会先自动生成安全备份。

Personal Core 使用独立路由和独立 ZIP，不混入知识库备份：

- `POST /person/backups/create`
- `POST /person/backups/verify`：传 `backup_id`。
- `POST /person/backups/restore`：传 `backup_id` 和精确的 `confirm_personal_core`。

Personal Core 恢复前也会自动创建安全备份；压缩包成员、文件数、单文件/总大小、符号链接和路径逃逸均受限。

## Provider 与凭证

密钥不通过普通配置保存。CLI 的 `credential-set` 写入 macOS Keychain 或 Windows 当前用户加密存储；配置只记录 `keychain:name` 或 `env:NAME` 引用。`POST /auth/rotate` 可轮换本地 API Token，不能使用幂等缓存。

Agent 客户端可以使用 `POST /agents/issue` 领取一次性明文 token，并用 `POST /agents/revoke` 撤销。磁盘只保存 token 哈希；支持 `vault:read`、`memory:capture`、`context:read`、`conversation:observe`、`impact:write` 和 `outcome:write` 范围。发放、列表和撤销只允许本机管理员 token，浏览器页面不会得到明文凭证。
