# Nanobot Server — 项目约定

## 语言

所有输出、注释、commit message 使用**中文**。代码标识符使用英文。

## Agent 工作方式（GPT-5.6）

- **结果优先**：需求明确时直接执行最小充分方案，不先生成设计文档、长计划或流程说明。
- **严格守界**：只修改用户放入范围的文件；不得顺手重构、增加门禁或扩大任务。发现相邻问题时先报告，不自动处理。
- **按需加载技能**：仅当用户点名技能，或任务与某个窄领域技能直接匹配时才加载。不要因为“可能有帮助”而加载元工作流技能，也不要串联多个规划/审查技能。
- **少问关键问题**：只有缺失信息会实质改变结果或产生风险时才澄清；其余情况采用最保守的合理假设继续。
- **轻量规划**：简单任务不进入 plan/goal 模式。复杂任务可在回复中维护简短步骤，但除非用户要求，不创建计划文件。
- **谨慎使用子 agent**：仅在多个独立子问题可明显并行时使用；简单改动、单点调查和同文件编辑由主 agent 完成。
- **验证后再结论**：修改后运行与改动直接相关的最小验证；声称完成、修复或测试通过时必须给出实际证据。
- **提交需授权**：用户明确说“提交”之前不得 commit；提交时遵循 `chinese-commit-conventions`。
- **外部信息要查证**：API/模型行为优先使用官方文档；社区经验只能作为待验证信号。参考外部代码时优先使用 GitHub 代码搜索。

## 提交规范

遵循 chinese-commit-conventions 技能：
- type: feat / fix / refactor / test / docs / chore / perf
- scope: 中文模块名
- subject: 中文动宾短语，≤50 字符
- 禁止 `git add -A` / `git add .`，必须按文件指定

## 测试

- 测试文件命名：`tests/test_<module>.py`
- 使用 pytest + in-memory SQLite
- 测试必须在提交前运行：`python -m pytest tests/ -n auto --dist loadfile -v`（20 核并行约 2 分钟；`--dist worksteal` 更快但会拆散同文件用例，仅在需要时使用）
- 单文件/单测调试直接 `python -m pytest tests/test_<module>.py -v`，不加 `-n`（避免 worker 启动开销）
- 0 failures 才能提交

## 项目结构

| 路径 | 用途 |
|------|------|
| `api/routes.py` | FastAPI 聊天路由（proxy_chat 主链路） |
| `api/admin/` | 管理端路由（模型/工具/画像/群记忆/Sandbox 等） |
| `app/` | 领域服务模块（session_memory、group_memory、persona、memory_digest、group_learning、prompt_runtime 等） |
| `clients/` | 外部 API 客户端（new-api, model registry） |
| `nanobot_kt/bridge.py` | KT Agent 生命周期 + 请求处理 |
| `core/` | 数据库、进化、状态管理、Prompt v2、语义索引、主动外呼 |
| `core/sandbox/` | Sandbox Server 侧（授权/Lease 账本/管理操作，不接触 Docker） |
| `sandboxd/` | Sandbox 独立控制面（唯一接触 Docker Socket 与 `/srv/nanobot`，经 UDS 调用） |
| `creatures/nanobot/` | KT creature 配置 + 工具 |
| `vendor/KohakuTerrarium/` | KT 框架源码 |
| `webui/` | 管理前端（React + Vite；`npm run lint && npm run build`，测试 `npx vitest run`） |
| `tests/` | 测试 |
| `docs/superpowers/specs/` | 设计文档 |

## 数据模型

| 表 | 用途 | 写入方 |
|---|------|--------|
| `users` | 用户 ID + `history_clear_at` 清除标记 | `proxy_chat`、`mark-clear` |
| `chat_logs` | **原始消息存档**——进化/画像分析素材，含 tool、ambient | `_persist_chat_turn`、`log_ambient` |
| `conversation_turns` | **精简对话上下文**——仅 user/assistant，专用于历史注入 | `_persist_chat_turn` |
| `personas` / `persona_facts` | 用户画像 JSON / 治理化画像事实（证据、置信度、矛盾） | `evolution_task`、`PersonaStateMachine` |
| `rolling_session_summaries` | 短期窗口外的滚动会话摘要 | `app/session_memory` |
| `group_memories` | 群体记忆（含人工/模型治理来源与审批哈希） | `app/group_memory`、群学习治理 |
| `semantic_index_items` / `_fts` | 统一语义索引（memory/session_summary/knowledge/sticker 召回） | `semantic-index-worker` |
| `scheduled_tasks` | 定时推送任务 | `/chat/tasks` |
| `proactive_outreach_log` | 主动外呼评估与投递账本 | `core/proactive` |
| `sandbox_access_grants` / `sandbox_leases` / `sandbox_runs` | Sandbox 授权 / 租约 / 运行账本 | `core/sandbox`（reconciler + 管理操作） |

**分离原则**：`ChatLog` 是档案馆（保留全部，永不删除），`ConversationTurn` 是工作内存（可清理，时间窗口查询）。

## 关键约束

- **HTTP 请求无状态**：KT conversation 每次清空，历史通过 `_build_session_memory()` 从 `ConversationTurn` 注入
- **历史清除**：`mark-clear` 打 `history_clear_at` 标记 + 删 `ConversationTurn`；`ChatLog` 保留不删
- **模型路由**：`get_ordered_candidates()` 按 priority score 排序，向下遍历（便宜优先）
- **熔断器**：`ModelFailureTracker` 连续 3 次失败后自动禁用 5min
- **Sandbox 边界**：Nanobot Server 不挂 Docker Socket、不知道宿主 Workspace 路径，只经 sandboxd UDS 操作；Profile 只能由 `SandboxAccessGrant` 决定，模型不能在参数中选择；Server 是 `SandboxLease`/`SandboxRun` 业务账本唯一写入方（周期 reconciler 主动拉取 sandboxd 事实）
- **RAG 降级**：reranker 未配置或运行时失败时按 `allow_degraded` 决定——允许则退回 semantic/lexical 门控并在结果标注 `degraded`/`fallback_reason`，不允许则 fail-closed；不得把 reranker 异常当空分数导致候选整批静默丢弃
- **主动外呼**：默认关闭（`proactive_outreach.enabled`），目标用户仅来自 `NANOBOT_SUPER_USER_IDS`；调度阈值与管理端 run-once 必须共用同一托管配置
- **测试超时兜底**：`pytest.ini` 设 120s 单测超时（`pytest-timeout`），任何卡死用例会被打断报失败而非无限挂起
- **Token 估算**：CJK 字符按 1.0，ASCII 按 0.35（不求精确，量级判断）
- **中文优先**：bot 使用者是中文用户，所有 prompt 和回复用中文
- **提示词同步**：修改 `enriched_query` 组装逻辑、历史注入方式、conversation 结构、工具输出契约或 prompt runtime 输入时，**必须检查 canonical Prompt Runtime 模板是否仍然准确**，重点包括 `prompts.v2.default/chat/*`、`prompts.v2.default/tasks/*`、`prompts.v2.default/tools/*/usage.md`、`core/prompt_v2/variables.py` 和 `core/prompt_v2/template_registry.py`。如果模板引用的变量、标记或行为描述已过时，必须在同一 PR 中更新默认模板与必要的 `data/prompts_v2/` 运行时模板。

## 禁止行为（反复犯错的教训）

### 1. 未验证就声称完成
- ✗ 没测 Qwen 连通性就说端口改了 OK
- ✗ 没测 news_search 就说"网络问题非代码"
- ✗ 没跑 benchmark 就说 prompt 优化好了
- ✗ 测试还在跑（甚至卡死挂起）就基于"上一次 exit 0"或未完成的运行声称通过
- ✓ **任何修改后，必须实际运行验证命令并确认输出**
- ✓ **声称测试通过前，必须确认本次全量运行真正结束且 0 failures，不复用旧结果**

### 1b. 改内部调用签名不查全替身
- ✗ 给 `run_scheduled_tasks` 加 `at` 参数，却没同步测试里的 `fake_run_scheduled_tasks` 替身，导致循环内异常被吞、测试无限挂起
- ✓ **改动函数签名/契约时，grep 全仓所有调用方与 mock/替身点一并更新**

### 2. 急于提交
- ✗ 多次在测试没跑完、用户没确认时就要 commit
- ✓ **用户明确说"提交"之前不 git commit**

### 3. 照搬外部回答，不做独立搜索
- ✗ 用户贴了一段 AI 分析，直接当正确答案
- ✗ 用户说"用 qualifire 模型"，没验证就加进代码
- ✓ **用户提出的方案也是假设，必须自己查证**
- ✓ **用 GitHub MCP / Context7 / WebSearch 独立调研**

### 4. 提 naive 技术方案
- ✗ "用关键词匹配做领域分类"——不可靠
- ✗ "asyncio.Lock 嵌套 + Event.wait"——必然死锁
- ✗ "_preprocess 重排消息"——hacky
- ✓ **复杂且需求不明确时先确认关键约束；需求明确时直接实施最小方案**

### 5. 代码改了但没读现有实现
- ✗ PRAGMA regex `\b` 不知道实际 SQL 格式
- ✗ 声称改了 QQBOT_PUSH_URL 但实际没改
- ✓ **改之前 grep 相关代码，读明白现有逻辑**

### 6. 不用好用的工具
- ✗ 用 WebSearch/WebFetch 搜网页——结果不可控、解析易出错、吃上下文
- ✗ 明明有 Tavily（tavily-search/tavily-crawl/tavily-cli）却不用
- ✓ **网页搜索优先用 Tavily**——LLM 优化结果，干净结构化输出
- ✓ **代码搜索用 GitHub MCP**——search_code/search_repositories
- ✓ **API/库文档用 Context7**——query-docs

## 服务器地址

| 服务 | 地址 | 用途 |
|------|------|------|
| new-api (LLM) | `10.60.42.158:9000` | LLM API 网关 |
| nanobot server | `10.60.42.158:8000` | 主服务 |
| QQbot push | `10.60.42.158:8082/nanobot/push` | 向 QQ 推送消息 |
| Qwen classifier | `172.17.0.1:9999` | 容器内访问宿主机的 llama-server |

本地测试时需 `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY` 清除代理。
