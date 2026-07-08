# 主动外呼管理页实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Nanobot Admin WebUI 中新增主动外呼页面，展示并控制主动情感外呼配置、业务记录、服务日志和 LLM 请求日志。

**架构：** 新增一个专用 admin 路由模块聚合主动外呼状态和操作，前端新增一个拆分 feature page 并挂到现有 app shell。复用 `SystemSetting`、`ProactiveOutreachLog`、`llm_api_request_logs`、现有日志接口与 `run_outreach_due_once/run_outreach_once`。

**技术栈：** FastAPI、SQLAlchemy、React、Tailwind、pytest、静态源码断言测试。

---

### 任务 1：后端主动外呼 Admin API

**文件：**
- 创建：`api/admin/proactive_outreach_routes.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_admin_proactive_outreach.py`

- [ ] **步骤 1：编写失败的测试**

新增测试：
- `test_admin_proactive_outreach_routes_are_registered`
- `test_proactive_outreach_status_reports_settings_logs_and_llm`
- `test_proactive_outreach_setting_update_is_scoped`
- `test_proactive_outreach_run_once_uses_existing_runtime`

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_admin_proactive_outreach.py -v`
预期：新路由不存在，测试失败。

- [ ] **步骤 3：实现路由**

实现：
- `GET /api/v1/admin/proactive-outreach/status`
- `GET /api/v1/admin/proactive-outreach/logs`
- `PUT /api/v1/admin/proactive-outreach/settings/{key:path}`
- `POST /api/v1/admin/proactive-outreach/settings/reload`
- `POST /api/v1/admin/proactive-outreach/run-once`

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_admin_proactive_outreach.py -v`
预期：全部通过。

### 任务 2：前端页面与导航

**文件：**
- 创建：`webui/src/features/proactive-outreach/ProactiveOutreachPage.jsx`
- 修改：`webui/src/App.jsx`
- 测试：`tests/test_webui_app_split.py`

- [ ] **步骤 1：编写失败的静态测试**

断言：
- App 导入 `ProactiveOutreachPage`
- 导航包含 `/proactive-outreach` 和「主动外呼」
- 路由包含 `path="/proactive-outreach"`
- 页面调用 `/proactive-outreach/status`、`/proactive-outreach/logs`、`/proactive-outreach/run-once`
- 页面包含「业务记录」「运行日志」「LLM 请求」。

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_webui_app_split.py -k proactive -v`
预期：页面和路由尚不存在，测试失败。

- [ ] **步骤 3：实现页面**

页面使用现有 `Card`、`MiniStat`、`Badge`、`ActionButton`、`JsonBlock`，保持后台管理工具风格。控件包括启停、superuser、间隔配置、重载、执行一次检查、业务记录展开、日志刷新、LLM source 筛选。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_webui_app_split.py -k proactive -v`
预期：通过。

### 任务 3：验证与收尾

**文件：**
- 修改：无新增实现文件，验证现有改动。

- [ ] **步骤 1：运行定向测试**

运行：
- `python -m pytest tests/test_admin_proactive_outreach.py -v`
- `python -m pytest tests/test_webui_app_split.py -k proactive -v`

- [ ] **步骤 2：运行全量测试**

运行：`python -m pytest tests/ -v`
预期：0 failures。

- [ ] **步骤 3：检查约束**

运行：
- `git diff --name-only -- vendor`
- `git -C vendor/KohakuTerrarium status --short`
- `git status --short`

预期：vendor 无输出；不自动 commit。
