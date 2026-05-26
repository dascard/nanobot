# Rolling Session Summary 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或等效 TDD 流程逐项实现。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 将当前 session 中被 recent raw window 挤出的旧 `ConversationTurn` 滚动压缩成独立的 session summary，并自动注入 Prompt。

**架构：** `ConversationTurn.id` 是唯一 coverage cursor；`rolling_session_summaries` 独立于 `memory_digests`、persona 和 group memory。Prompt 构成为 `persona/group context + rolling_session_summary + conversation_context_header + recent raw window + current user_input`。

**技术栈：** FastAPI、SQLAlchemy、SQLite schema migrations、pytest。

---

### 任务 1：窗口边界纯函数

**文件：**
- 创建：`app/session_memory/windowing.py`
- 测试：`tests/test_session_memory.py`

- [x] 编写失败测试：eligible 过滤 internal/no_context，raw window 按 `id` 而非 `created_at`，pending 满足 `last_covered < id < raw_start`。
- [x] 实现 `is_context_eligible_turn()`、`load_context_eligible_turns()`、`select_latest_raw_window()`、`select_pending_for_summary()`、`should_rollup()`。
- [x] 运行 `python -m pytest tests/test_session_memory.py -q`。

### 任务 2：模型、迁移和 summary service

**文件：**
- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 创建：`app/session_memory/rolling_summary.py`
- 创建：`app/session_memory/summarizer.py`
- 创建：`app/session_memory/renderer.py`
- 测试：`tests/test_schema_migrations.py`、`tests/test_session_memory.py`

- [x] 新增 `RollingSessionSummary` ORM。
- [x] 新增 `20260525_rolling_session_summaries` migration。
- [x] 实现 active summary 读取、归档、审计、保存和确定性 summarizer。
- [x] 运行 schema/session-memory 测试。

### 任务 3：接入上下文构建

**文件：**
- 修改：`core/context_builder.py`
- 修改：`api/routes.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 测试：`tests/test_history.py`、`tests/test_prompt_*`

- [x] 替换私聊 `build_session_memory()` 的时间硬 cutoff 为 id cursor + raw window。
- [x] 注入 `<rolling_session_summary>`，并保留 recent raw messages、gap marker 和长消息摘要化。
- [x] 群聊 ChatLog 上下文注入 active rolling summary。
- [x] PromptRuntime meta 扁平记录 rolling summary debug 字段。

### 任务 4：管理接口与清理

**文件：**
- 创建：`api/admin/session_memory_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`api/routes.py`

- [x] 新增 GET/run/archive rolling summary 管理接口。
- [x] `mark-clear` 归档用户 active rolling summaries。
- [x] dry-run 不写库，force 只跳过阈值但不允许空 pending。

### 任务 5：验证

- [x] 运行相关测试：`tests/test_session_memory.py tests/test_history.py tests/test_schema_migrations.py tests/test_admin_api.py`。
- [x] 运行 prompt/API 相关测试。
- [x] 运行全量测试：`python -m pytest tests/ -q`。
- [x] 运行空白检查：`git diff --check`。
