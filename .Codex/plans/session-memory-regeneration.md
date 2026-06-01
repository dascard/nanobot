# 摘要重生成与近期摘要可见性修复 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复 WebUI 摘要管理页缺少长期摘要重生成按钮、近期摘要只显示私聊、LLM 摘要任务不消费的问题。

**架构：** 后端在 admin session memory routes 中补齐重生成接口和列表数据源；windowing 层补旧数据 sender 兜底；bootstrap scheduler 启动 session summary worker；前端在同一摘要浏览器组件中区分近期生成和长期重生成操作。

**技术栈：** FastAPI、SQLAlchemy、pytest、React、Vite、axios。

---

## 文件结构

- 修改：`app/session_memory/admin_browser.py`
  - 近期摘要 session 列表纳入 `conversation_turns`。
- 修改：`app/session_memory/windowing.py`
  - 群聊 sender key 从 `meta_json` 和正文 `[用户名]` 提取。
- 修改：`api/admin/session_memory_routes.py`
  - 新增长期摘要重生成 admin endpoint。
- 修改：`core/daily_digest.py`
  - `generate_daily_digest_for_date` 支持 `session_id` 过滤。
- 修改：`bootstrap/schedulers.py`
  - 启动 session summary worker 轮询线程。
- 修改：`webui/src/App.jsx`
  - 近期摘要无 summary 时显示生成按钮；长期摘要显示重生成按钮。
- 测试：`tests/test_admin_session_memory_browser.py`
- 测试：`tests/test_session_memory.py`
- 测试：`tests/test_daily_digest.py`
- 测试：`tests/test_bootstrap_server.py`
- 测试：`tests/test_webui_admin_redesign.py`

## 任务 1：后端列表和 sender 兜底

- [ ] **步骤 1：编写失败测试**
  - 在 `tests/test_admin_session_memory_browser.py` 添加只存在
    `ConversationTurn` 的 `group_1`，请求 `kind=recent` 应返回该 session，
    `summary_count=0` 且 `turn_count=2`。
  - 在 `tests/test_session_memory.py` 添加 `should_rollup` 测试，旧正文
    `[用户名]甲`、`[用户名]乙` 应被识别为 2 个不同发言人。

- [ ] **步骤 2：运行红灯测试**
  - `python -m pytest tests/test_admin_session_memory_browser.py::test_admin_session_memory_recent_sessions_include_conversation_turn_only_groups tests/test_session_memory.py::test_group_rollup_sender_count_falls_back_to_username_marker -v`
  - 预期失败：列表缺少 `group_1`，sender 数仍为 0。

- [ ] **步骤 3：实现最小修复**
  - `admin_browser` SQL 的 `all_rows` 增加 `conversation_turns` 聚合来源。
  - `windowing` 增加 `_sender_key_for_turn()`，从 meta 或正文提取 sender。

- [ ] **步骤 4：运行绿灯测试**
  - 重跑步骤 2 命令，预期通过。

## 任务 2：长期摘要重生成接口

- [ ] **步骤 1：编写失败测试**
  - 在 `tests/test_admin_session_memory_browser.py` 添加 endpoint 测试，
    monkeypatch `api.admin.session_memory_routes.generate_daily_digest_for_date`，
    调用 `/session-memory/group_1/digests/run` 时应传入最新 `digest_date`、
    `user_id`、`session_id` 和 `force=True`。
  - 在 `tests/test_daily_digest.py` 添加 `session_id` 过滤测试。

- [ ] **步骤 2：运行红灯测试**
  - `python -m pytest tests/test_admin_session_memory_browser.py::test_admin_session_memory_long_digest_run_endpoint_regenerates_selected_session tests/test_daily_digest.py::test_generate_daily_digest_can_filter_specific_session -v`
  - 预期失败：endpoint 不存在，daily digest 不支持 session 过滤。

- [ ] **步骤 3：实现最小修复**
  - `core/daily_digest.py` 增加 `session_id` 参数并过滤规范化后的 sid。
  - `api/admin/session_memory_routes.py` 新增请求模型和 POST endpoint。

- [ ] **步骤 4：运行绿灯测试**
  - 重跑步骤 2 命令，预期通过。

## 任务 3：worker 启动和 WebUI 操作

- [ ] **步骤 1：编写失败测试**
  - 在 `tests/test_bootstrap_server.py` 增加调度器测试，验证生产模式会启动
    `session-summary-worker`。
  - 在 `tests/test_webui_admin_redesign.py` 扩展静态断言，要求存在
    `重新生成长期摘要`、`生成近期摘要` 和两个 API 调用。

- [ ] **步骤 2：运行红灯测试**
  - `python -m pytest tests/test_bootstrap_server.py::test_start_schedulers_starts_session_summary_worker tests/test_webui_admin_redesign.py::test_session_summary_browser_exposes_llm_regeneration_controls -v`
  - 预期失败：worker handle 和长期按钮不存在。

- [ ] **步骤 3：实现最小修复**
  - `bootstrap/schedulers.py` 增加 session summary worker loop handle。
  - `webui/src/App.jsx` 增加近期生成/长期重生成按钮及状态刷新。

- [ ] **步骤 4：运行绿灯测试**
  - 重跑步骤 2 命令，预期通过。

## 任务 4：集成验证和提交

- [ ] **步骤 1：运行相关测试**
  - `python -m pytest tests/test_admin_session_memory_browser.py tests/test_session_memory.py tests/test_daily_digest.py tests/test_bootstrap_server.py tests/test_webui_admin_redesign.py -v`

- [ ] **步骤 2：构建 WebUI**
  - `npm run build`

- [ ] **步骤 3：运行完整测试**
  - `python -m pytest tests/ -v`

- [ ] **步骤 4：精确暂存并提交**
  - `git add` 只包含本次修复文件。
  - `git commit -m "fix(摘要管理): 修复重生成入口和近期摘要列表"`
