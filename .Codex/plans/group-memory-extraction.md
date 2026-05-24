# 群体记忆提取触发实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 实现本计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让群体记忆可从 Web 管理端直接触发提取，并让现有提取结果真正进入群画像注入。

**架构：** 新增 `app/group_memory/extraction_service.py` 作为复用服务，Admin API 只负责认证和 DTO 转换。WebUI 群体记忆页调用 overview 与 extract 接口，不在前端拼业务判断。

**技术栈：** FastAPI、SQLAlchemy、pytest、React/Vite。

---

### 任务 1：补后端失败测试

**文件：**
- 修改：`tests/test_group_memory.py`
- 修改：`tests/test_admin_api.py`
- 创建：`tests/test_group_memory_extraction_service.py`

- [ ] 编写 `query_injectable` 应返回单次分析 topic 的测试。
- [ ] 编写 overview 接口应列出有日志但无记忆群的测试。
- [ ] 编写 extract 接口应调用服务并返回统计的测试。
- [ ] 运行目标测试，确认失败来自功能缺失。

### 任务 2：实现提取服务

**文件：**
- 创建：`app/group_memory/__init__.py`
- 创建：`app/group_memory/extraction_service.py`

- [ ] 定义 `GroupMemoryExtractionResult` dataclass。
- [ ] 实现 `extract_group_memories(db, group_id, window_hours, instructions)`。
- [ ] 复用 `GroupAnalysisRepository`、`filter_analyzable_logs`、`dedupe_group_logs`、`build_analysis_payload`、`analyze_group`、`extract_and_persist`。
- [ ] 返回 raw/eligible/deduped/prompt message/source log/stat 计数。

### 任务 3：接入 Admin API

**文件：**
- 修改：`api/admin_routes.py`

- [ ] 新增 `/group-memories/overview`。
- [ ] 新增 `/groups/{group_id:path}/memories/extract`。
- [ ] 用 `_audit_request` 记录手动提取动作。

### 任务 4：调整注入门槛

**文件：**
- 修改：`core/group_memory.py`

- [ ] `query_injectable()` 使用 `CONFIDENCE_FLOOR`。
- [ ] `build_profile_with_evidence()` 使用同一口径。
- [ ] `should_inject()` 不再要求非事件类重复出现两次。

### 任务 5：改 WebUI 群体记忆页

**文件：**
- 修改：`webui/src/App.jsx`

- [ ] 加载群体记忆 overview。
- [ ] 点击群列表加载对应记忆。
- [ ] 增加窗口选择和“提取记忆”按钮。
- [ ] 提取后刷新 overview 和当前群记忆。

### 任务 6：验证

- [ ] 运行目标 pytest。
- [ ] 运行 `python -m pytest tests/ -v`。
- [ ] 运行 `npm run build`。
- [ ] 运行 `git diff --check`。
