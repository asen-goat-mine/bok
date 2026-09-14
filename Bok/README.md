# Bok Local Memory Core

Bok 是本地优先的个人长期记忆内核。Markdown 是唯一事实源；可重建的搜索/向量缓存，以及提议、版本、备份等本机运行状态都放在 `.bok/` 中。v0.5 将个人记忆入口改为“模型抽象理解优先”：没有合格的第三人称结构化解释就不生成个人观察，拒绝把用户原句或一次性任务命令冒充长期认知；同时补齐跨进程写锁、事务恢复、正式备份列表、按需加载大文件和可彻底遗忘错误认知的闭环。预览后端通过同源 Bok bridge 调用内核，不需要把 bearer token 放进浏览器，也不复制业务逻辑。

## 当前完成范围

- 安全 Markdown 新建、编辑、移动、回收、版本和撤销。
- 并发哈希校验、符号链接/路径穿越防护、原子替换和崩溃安全写入。
- 默认快速检索与“搜索全部”双层范围：工程基准、归档和低频工作区默认延后，但数据与能力完整保留。
- 段落级关键词/结构检索、来源权威排序、本地全量语义候选召回、云端最小候选重排、来源引用和 Token 预算；正式知识卡、风格卡、当前项目与资产定位按查询意图加权，Dashboard、导航索引和 Skill 正文不会再凭通用词抢占答案。
- Schema-on-read：把 frontmatter 和旧卡正文段落统一解析为 `type / role / status / source / tags / updated`；一级知识卡已迁移到最小字段，提交前会阻止字段缺失或未登记到 `Memory-Index` 的知识卡。
- “今天”数据、当前项目状态、关键决策、下一步和一键续接。
- 随手记一条一文件、收件箱状态、归档和转记忆队列。
- 非阻断记忆采集：先本地排队，再由大模型后台判断。
- 逐轮对话收据：`conversation_id + turn_id` 幂等、冲突保护、崩溃补投和处理状态查询。
- MCP 自动观察只把不含正文/ID/哈希的最小成功状态返回给 Agent；完整逐轮收据仍保留在本机状态与认证 API 中，原生查询能力不变。
- 外部引用、临时会话与“不记忆”在入口隔离；状态接口不返回原始正文。
- 对话事件和捕获队列一条一文件，避免高强度使用时反复重写一个持续膨胀的 JSON 数组。
- 普通后台记忆按 10～20 条或约 30 秒空闲窗口合并一次 Provider 请求；每条原始证据、分析结果、来源和遗忘路径仍独立保存。批量模型缺项时只回退补跑缺失项。
- 对话正文默认保留 14 天并可配置；到期时同步丢弃仍未处理的捕获正文，只保留无正文收据。
- 独立 Personal Core：未配置时安全禁用，不在共享 Vault 偷建个人画像目录；路径必须在项目和 Git 仓库之外。
- Personal Claim 一条一文件 Markdown；低风险偏好、习惯、选择模式、项目经历和能力认知在明确高置信表达或跨会话证据达标后进入 `learned`，不要求逐条确认；身份、长期目标、授权规则、敏感、冲突和低置信判断仍需用户介入。`learned` 不伪装成用户确认，并默认只留在 `personal-core`。
- 最小个人上下文：接口上限仍为默认 6 条 / 1,500 Token；日常 Agent 建议按需只取 4 条 / 512 Token。只返回任务与可见范围命中的有效 Claim（安全 `learned` 或用户确认的受保护 Claim）；MCP 提供搜索、上下文、对话观察、影响/Outcome、随手记和收件箱工具。
- Claim 版本 journal、启动修复、损坏/伪造/符号链接失败关闭，以及按文件签名失效的进程内缓存。
- Observation Ledger 一条一文件；相同对话回合天然幂等。单次观察只形成证据；普通低风险模式至少跨 3 次会话并覆盖 2 个日期或场景后才安静形成 `learned`，行为假设和决策模式使用更严格门槛。
- 个人观察必须来自模型/Agent 给出的第三人称抽象理解；近似照抄、临时项目指令冒充全局偏好和无解释关键词投影会被拒绝且不落入 Personal Core。跨回合证据使用不含原话的稳定 `concept_key` 合并，避免同一习惯换个说法就积累失败。
- 错误 Claim 可“彻底忘记”：一并清除相关观察、影响、Outcome、旧版本、包含它的私人备份、仍保留的来源对话正文及关联运行态/幂等响应；中断后会回滚或在下次启动完成。已经独立提交到项目 Vault 的普通知识卡不会被个人遗忘静默删除，接口会明确返回待复核路径；用户删除该卡后，活动路径会脱敏。
- Outcome 与影响分层记录：保存选择后的实际结果，以及哪些有效 Claim 影响过回答；不保存回答正文。
- 重复、过期、冲突、低置信、负向结果和长期未使用候选可视化；重要记忆不会因低频自动删除。
- 逐 Agent 哈希凭证、范围权限和撤销；浏览器只看到脱敏状态，不得到密钥或完整 token。
- Personal Core 独立备份、清单哈希校验、安全恢复前快照和路径/符号链接/压缩包边界保护。
- 普通高置信记忆自动提交并可撤销；重要、冲突、敏感和低置信内容进入待处理。
- 本地 Ollama 自动发现；未运行时可自动启动，模型不可用时内容继续排队。
- OpenAI-compatible BYOK 适配、macOS Keychain / Windows 当前用户加密凭证和网络级 Local Only。
- 认证 loopback Memory API、幂等键、限流、结构化错误和 1 MiB 请求上限。
- `AI-Second-Brain-UI` Bok 工作台：今天、语义搜索、版本编辑、记忆收件箱、随手记、活动/撤销、设置/双备份和“关于我”；令牌只留在本机后端，浏览器写操作必须提供同源证明。
- CLI、MCP stdio、备份、校验、恢复、活动记录和 Doctor。

明确不做：完整插件市场、团队协作、支付、原生移动端、Canvas、公开发布和完整复制 Obsidian。

## 最短启动方式

macOS：

```bash
./Bok/start-bok.command
```

Windows：

```powershell
Bok\start-bok.cmd
```

服务只监听 `127.0.0.1:8771`。首次启动会创建 `.bok/auth-token`；API 请求必须携带：

```text
Authorization: Bearer <本机令牌>
```

从现有预览 UI 调用时使用同源路径 `/api/bok/v1/...`。bridge 会在第一次请求时按需启动内部 Bok 服务并在服务端认证，前端代码不得读取令牌。安全级别更高的令牌轮换仍只允许可信本地 API 或 CLI。

Personal Core 不会自动替用户选择真实目录。首次使用时，从 Vault 根目录执行：

```bash
PYTHONPATH=Bok python3 -m bok_core --vault . \
  person setup "/absolute/path/Bok-Personal-Core" --confirm
```

只能选择项目 Vault 与 Git 仓库之外的空目录或已有 Bok Personal Core。浏览器 bridge 不允许选择或更换该路径。

## 丝滑使用契约

1. 随手记和对话事件先落本地，接口立即返回。
2. 模型在后台处理；普通回合按 10～20 条或约 30 秒窗口批量分析，离线或未启动时继续排队，不丢内容、不让用户等待。
3. 普通高置信记忆自动整理，只显示“已整理，可撤销”。
4. 重要记忆不弹窗阻断，只进入“需要关注”，旧内容保持有效。
5. 所有写入都有版本；重复点击可用 `Idempotency-Key` 避免重复创建。
6. 搜索只返回最相关的 3～6 个段落，优先展示不同来源，默认上限 2,500 Token；需要基准、归档或工程资料时切换 `scope=all`。
7. Local Only 时非 loopback 模型请求在网络层被拒绝。
8. 真实问法回归集固定验证知识回忆、项目续接、个人风格、精确路径和成片定位；提交前自动运行，避免导航页或工程示例再次压过正式来源。

## CLI 示例

从 Vault 根目录运行：

```bash
PYTHONPATH=Bok python3 -m bok_core --vault . health
PYTHONPATH=Bok python3 -m bok_core --vault . search "Bok 下一步"
PYTHONPATH=Bok python3 -m bok_core --vault . quick-note "突然想到的内容"
PYTHONPATH=Bok python3 -m bok_core --vault . capture "这段内容值得 Bok 判断"
PYTHONPATH=Bok python3 -m bok_core --vault . inbox
PYTHONPATH=Bok python3 -m bok_core --vault . resume
PYTHONPATH=Bok python3 -m bok_core --vault . backup
PYTHONPATH=Bok python3 -m bok_core --vault . doctor
PYTHONPATH=Bok python3 -m bok_core --vault . person health
PYTHONPATH=Bok python3 -m bok_core --vault . person list
PYTHONPATH=Bok python3 -m bok_core --vault . person backup
PYTHONPATH=Bok python3 -m bok_core --vault . person backups
```

`capture` 只负责快速排队；常驻 API 会自动后台处理。命令行也可以手动执行：

```bash
PYTHONPATH=Bok python3 -m bok_core --vault . process
```

## 后台整理设置

在“Bok 工作台 → 设置 → 后台整理”中暂停或恢复自动整理，选择轻量模式（每个队列最多 2 条、间隔 60 秒）或标准模式（最多 4 条、间隔 30 秒）。暂停在当前批次完成后生效，新内容继续在本地排队；切换模式不会取消暂停。设置保存在当前 Vault 的本机运行状态中，重启后保留。手动 `process` 命令仍由用户主动执行，不受自动后台暂停控制。

新配置默认 `auto_start_local_model: false`；用户可自行启动 Ollama 后使用本地模型，已有配置中的显式选项继续生效。模型不可用时内容留在本地队列。

## 接入 Codex 与其他 Agent

Codex CLI/桌面端可注册本机 stdio MCP；在 Vault 根目录执行：

```bash
codex mcp add bok \
  --env PYTHONPATH="$PWD/Bok" \
  -- python3 -m bok_core --vault "$PWD" mcp
```

用 `codex mcp get bok` 核对配置，新任务或重启 Codex 后使用。工作区 `AGENTS.md` 已规定：工具可用时每个用户原生回合静默调用 `bok_observe_conversation`，同一回合复用稳定 ID；附件/网页标记为外部材料，敏感或“不记忆”内容不保存正文，单次行为不会直接成为长期偏好。该 MCP 写入只返回紧凑确认；需要 ID、哈希和处理细节时使用本机认证 API/status，不把完整收据重复塞回 Agent 上下文。

不支持 stdio MCP 的本机客户端使用 loopback API。管理员可以通过 `/v1/agents/issue` 发放一次性明文、可撤销、按范围限制的 Agent token；不要把管理员 bearer token 或 Agent token 写进 Markdown、Git、前端或日志。

## 模型选择

默认配置是：

- `provider: auto`
- `local_only: true`
- 自动发现本机 Ollama。
- 本地服务未启动时，在第一次后台判断时尝试启动。
- 没有可用模型时，捕获保持 `waiting_for_model`，不会降级到云端。

需要云端模型时，复制 `config.example.json` 到 `.bok/config.json`，将 `local_only` 显式改为 `false`，配置 Provider 和 `env:` / `keychain:` 凭证引用。即使如此，每次云端请求仍必须传 `cloud_consent: true`；Bok 不默认上传整个知识库。

## 运行状态

`.bok/` 只保存本机运行数据并已加入 Git 忽略：

- `auth-token`：本机 API 令牌。
- `state/`：记忆收件箱、快速采集队列、幂等结果。
- `state/conversations/`：短期对话正文与不含正文的逐轮处理收据。
- `state/captures/`：一条一文件的后台捕获分片；完成或保留期到期后移除正文。
- `state/agent-credentials.json`：逐 Agent 的哈希凭证、权限和撤销状态，不保存可恢复明文 token。
- `versions/`：每次写入前的版本快照。
- `trash/`：可恢复删除。
- `backups/`：带文件哈希清单的 ZIP。
- `cache/`：可删除重建的 Embedding 等派生缓存。
- `activity.jsonl`：不含正文的操作记录。

独立 Personal Core 使用 `Claims/`、`Observations/`、`Outcomes/`、`Impacts/` 和 `Archive/` 保存可读 Markdown；其 `.bok/backups/`、版本与清理决策仅是本机恢复/运行状态。外部材料、临时会话和不记忆内容不会进入个人理解，影响记录也不保存回答正文。

## 测试

```bash
./Bok/run-tests.command
```

测试覆盖逐轮收据、天然幂等、MCP 紧凑回执、受控小批次/空闲窗口处理、缺项逐条回退、跨进程写锁、策略隔离、崩溃补投、保留期清理、分片队列、旧队列迁移、Personal Core 隔离、第三人称抽象信号与原句拒绝、低风险安静学习与旧卡升级迁移、Claim 全状态动作、内容确认与 Agent 授权分离、Observation/Outcome/影响学习、彻底忘记及中断恢复、垃圾清理保护、逐 Agent 凭证、知识库与 Personal Core 双备份、精确/合并恢复与事务回滚、损坏 ZIP 结构化失败、范围过滤、历史/拒绝守卫、损坏与符号链接失败关闭、替代链和版本 journal 中断恢复、真实问题检索回归、真实 HTTP API、MCP 与 UI bridge；模型判断与向量召回固定使用模拟 Provider，不启动或调用用户的本地模型。

Windows：

```powershell
Bok\run-tests.cmd
```

详细契约：

- [产品需求](docs/PRD-v0.1.md)
- [Memory API](docs/API-v1.md)
- [Markdown Schema](docs/SCHEMA-v1.md)
- [体验契约](docs/UX-CONTRACT.md)
- [个人运行模型 v1](docs/PERSON-MODEL-v1.md)
- [Personal Core 实现路径](docs/PERSONAL-CORE-IMPLEMENTATION.md)
- [安全与测试](docs/SECURITY-AND-TESTS.md)
- [UI 实现与验收清单](docs/UI-HANDOFF.md)
