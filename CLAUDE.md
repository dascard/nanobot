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
writing-plans → 实现计划     → .claude/plans/<topic>.md
test-driven-development     → 先写测试，红-绿-重构
verification-before-completion → 验证通过后才能声称完成
chinese-commit-conventions  → 规范化提交
```

平凡任务（单文件 fix、typo）可以跳过 brainstorm/plan，但**必须**经过 verification-before-completion。

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
- **提示词同步**：修改 `enriched_query` 组装逻辑、历史注入方式、conversation 结构时，**必须检查 `creatures/nanobot/prompt.md` 是否仍然准确**。如果 prompt 引用的标记（如 `<user_input>`、`<history_context>`）或行为描述（如"历史通过 conversation 注入"）已过时，必须在同一 PR 中更新
