# Nanobot Server — 项目约定

## 语言

所有输出、注释、commit message 使用**中文**。代码标识符使用英文。

## 技能工作流（强制）

**任何非平凡任务（≥3 个文件或新功能）必须按以下顺序使用 skills。不确定是否适用时，先调用 Skill 工具检查。**

1% 可能 = 必须调用。这不是可选的。"只是一个简单的问题"、"让我先探索一下"、"我记得这个技能"——这些都是合理化。调用技能。

特别提醒：
- **探索/分析需求时** → 优先用 brainstorming 提出澄清问题，而不是直接给方案
- **实现完成后声称成功前** → 必须经过 verification-before-completion
- **提交时** → 必须遵循 chinese-commit-conventions
- **审查他人代码或自审时** → 调用 chinese-code-review
- **参考外部项目实现时** → 用 GitHub MCP tools，不要用 WebSearch

任何非平凡任务（≥3 个文件或新功能）必须按以下顺序使用 skills：

```
brainstorming → 设计文档     → docs/superpowers/specs/YYYY-MM-DD-<topic>.md
writing-plans → 实现计划     → .Codex/plans/<topic>.md
test-driven-development     → 先写测试，红-绿-重构
verification-before-completion → 验证通过后才能声称完成
chinese-commit-conventions  → 规范化提交
```

平凡任务（单文件 fix、typo）可以跳过 brainstorm/plan，但**必须**经过 verification-before-completion。

## 任务前置输出（强制）

每次执行任务前，必须在任何读文件、运行命令、调用工具或修改代码之前，先用中文输出两段简短说明：

- **任务摘要**：用 1-3 句话说明本轮要解决什么、预期交付物是什么。
- **Walkthrough**：用 2-5 句话说明准备如何推进，包括要读哪些关键文件、如何拆分步骤、准备如何验证。

如果任务来自持续目标或自动续跑，也必须重新输出任务摘要和 walkthrough，确认当前动作仍然对齐最新目标。若用户中途改需求，以最新用户消息为准，先更新任务摘要和 walkthrough，再继续执行。
如果一个任务被拆成多个阶段，每个阶段开始前也要更新一次任务摘要和 Walkthrough，说明本阶段的边界、预期提交和验证方式。

## 上下文预算与子 agent（强制）

遇到需要阅读大量代码、多个独立子系统、长文档或大范围测试失败时，优先把互不依赖的读码/梳理任务委派给子 agent，避免主上下文窗口被无关细节挤爆。不要把所有探索性读码都塞进主线程；主线程负责决策、编辑、验证和提交。

- 适合委派：`docs/todo.md` 这类长待办梳理、独立设计文档解读、不同模块的根因调查、多个测试文件的失败分析。
- 强烈建议委派：预计要读 5 个以上文件、单文件超过 800 行、需要同时理解 2 个以上子系统、或测试失败分布在 3 个以上文件时。
- 不适合委派：需要主线程立即编辑的单点改动、多个 agent 会同时修改同一文件的工作、必须统一判断的最终集成决策。
- 委派时必须给子 agent 明确范围、文件路径、输出格式和禁止事项；默认让子 agent 只读不改，除非任务已拆出互不冲突的写入范围。
- 子 agent 返回后，主线程必须审查其结论，再决定是否采纳；不能把子 agent 报告直接当作完成证明。

## 提交规范

遵循 chinese-commit-conventions 技能：
- type: feat / fix / refactor / test / docs / chore / perf
- scope: 中文模块名
- subject: 中文动宾短语，≤50 字符
- 禁止 `git add -A` / `git add .`，必须按文件指定

## 测试

- 测试文件命名：`tests/test_<module>.py`
- 使用 pytest + in-memory SQLite
- 测试必须在提交前运行：`python -m pytest tests/ -v`
- 0 failures 才能提交

## 项目结构

| 路径 | 用途 |
|------|------|
| `api/routes.py` | FastAPI 路由和端点 |
| `clients/` | 外部 API 客户端（new-api, dify, model registry） |
| `nanobot_kt/bridge.py` | KT Agent 生命周期 + 请求处理 |
| `core/` | 数据库、进化、状态管理 |
| `creatures/nanobot/` | KT creature 配置 + 工具 |
| `vendor/KohakuTerrarium/` | KT 框架源码 |
| `tests/` | 测试 |
| `docs/superpowers/specs/` | 设计文档 |

## 数据模型

| 表 | 用途 | 写入方 |
|---|------|--------|
| `users` | 用户 ID + `history_clear_at` 清除标记 | `proxy_chat`、`mark-clear` |
| `chat_logs` | **原始消息存档**——进化/画像分析素材，含 tool、ambient | `_persist_chat_turn`、`log_ambient` |
| `conversation_turns` | **精简对话上下文**——仅 user/assistant，专用于历史注入 | `_persist_chat_turn` |
| `personas` | 用户画像 JSON | `evolution_task` |
| `scheduled_tasks` | 定时推送任务 | `/chat/tasks` |

**分离原则**：`ChatLog` 是档案馆（保留全部，永不删除），`ConversationTurn` 是工作内存（可清理，时间窗口查询）。

## 关键约束

- **HTTP 请求无状态**：KT conversation 每次清空，历史通过 `_build_session_memory()` 从 `ConversationTurn` 注入
- **历史清除**：`mark-clear` 打 `history_clear_at` 标记 + 删 `ConversationTurn`；`ChatLog` 保留不删
- **模型路由**：`get_ordered_candidates()` 按 priority score 排序，向下遍历（便宜优先）
- **熔断器**：`ModelFailureTracker` 连续 3 次失败后自动禁用 5min
- **Token 估算**：CJK 字符按 1.0，ASCII 按 0.35（不求精确，量级判断）
- **中文优先**：bot 使用者是中文用户，所有 prompt 和回复用中文
- **提示词同步**：修改 `enriched_query` 组装逻辑、历史注入方式、conversation 结构、工具输出契约或 prompt runtime 输入时，**必须检查 canonical Prompt Runtime 模板是否仍然准确**，重点包括 `prompts.v2.default/chat/*`、`prompts.v2.default/tasks/*`、`prompts.v2.default/tools/*/usage.md`、`core/prompt_v2/variables.py` 和 `core/prompt_v2/template_registry.py`。如果模板引用的变量、标记或行为描述已过时，必须在同一 PR 中更新默认模板与必要的 `data/prompts_v2/` 运行时模板。

## 禁止行为（反复犯错的教训）

### 1. 未验证就声称完成
- ✗ 没测 Qwen 连通性就说端口改了 OK
- ✗ 没测 news_search 就说"网络问题非代码"
- ✗ 没跑 benchmark 就说 prompt 优化好了
- ✓ **任何修改后，必须实际运行验证命令并确认输出**

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
- ✓ **复杂问题先用 brainstorming 探索方案，别急着写代码**

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
