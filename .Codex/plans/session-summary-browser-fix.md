# Session 摘要浏览修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 test-driven-development 逐项实现。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复摘要管理页默认不可读、session 重复和测试数据混入的问题，并改善后续长期摘要生成质量。

**架构：** 后端管理浏览层负责只读归一化和展示字段派生，前端只按 tab 选择 `kind` 并展示对应 preview。长期摘要构建器只做输入清洗，不迁移历史数据。

**技术栈：** FastAPI、SQLAlchemy、pytest、React/Vite。

---

### 任务 1：后端摘要浏览归一化

**文件：**
- 修改：`app/session_memory/admin_browser.py`
- 修改：`api/admin/session_memory_routes.py`
- 测试：`tests/test_admin_session_memory_browser.py`

- [x] **步骤 1：编写失败测试**

新增测试覆盖：
- `group_42` 和旧 `42` 在列表中合并。
- 默认过滤 `group_memory_test` / `private_smoke`。
- `kind=recent` 只返回有近期摘要的 session，`kind=long` 只返回有长期摘要的 session。
- `group_42` 的详情能查到旧 `42` 的 digest。

- [x] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_admin_session_memory_browser.py -q`

- [x] **步骤 3：实现最小后端代码**

在 `AdminSessionMemoryBrowser` 中加入：
- `_canonical_session_id`
- `_session_aliases`
- `_is_system_session`
- `kind` 与 `include_system_sessions` 参数
- long digest preview/content 派生字段

- [x] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/test_admin_session_memory_browser.py -q`

### 任务 2：摘要生成清洗

**文件：**
- 修改：`app/memory_digest/builder.py`
- 创建：`tests/test_memory_digest_builder_quality.py`

- [x] **步骤 1：编写失败测试**

新增测试覆盖：
- `[sender]: [图片:1张]` 被识别为纯图片并过滤。
- `[sender]: 签到` 被识别为命令并过滤。
- 关键词不从重复 sender 前缀中提取。

- [x] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_memory_digest_builder_quality.py -q`

- [x] **步骤 3：实现最小清洗代码**

提取 `_message_body`，在 `_skip_reason` 和 `_format_valid_log` 中使用正文判断和正文摘要。

- [x] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/test_memory_digest_builder_quality.py -q`

### 任务 3：前端摘要页按类型展示

**文件：**
- 修改：`webui/src/App.jsx`
- 测试：`tests/test_webui_observability.py`

- [x] **步骤 1：编写失败测试**

断言源码包含：
- `kind: isRecent ? 'recent' : 'long'`
- `latest_digest_preview`
- 长期摘要空态文案不再使用 `无 active summary`

- [x] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/test_webui_observability.py -q`

- [x] **步骤 3：实现最小前端代码**

`loadSessions` 传递 `kind`，列表根据 mode 显示 `summary_count` 或 `digest_count`，长期摘要展示 `latest_digest_preview`。

- [x] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/test_webui_observability.py -q`

### 任务 4：回归验证

**文件：**
- 验证：`tests/test_admin_session_memory_browser.py`
- 验证：`tests/test_memory_digest_builder_quality.py`
- 验证：`tests/test_webui_observability.py`

- [x] **步骤 1：运行聚合测试**

运行：
`python -m pytest tests/test_admin_session_memory_browser.py tests/test_memory_digest_builder_quality.py tests/test_webui_observability.py -q`

- [x] **步骤 2：运行前端构建**

运行：`npm run build`，工作目录 `webui`。
