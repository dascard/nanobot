# Admin `/db/query` 迁移矩阵

状态：阶段 1.1 已实施
基线提交：`35b8904eef449e8fd7beb7831685abf033249aa6`
目标阶段：阶段 1（删除任意只读 SQL 产品契约）

## 1. 证据边界

当前系统没有保存 `/api/v1/admin/db/query` 的历史 SQL、调用者、频次或查询结果，
因此不能声称掌握“现网 Top-N SQL”。本矩阵只依据以下可复核证据：

- 阶段 0A 时 WebUI 只有
  `webui/src/App.jsx::DbPage.runSql()` 一个任意 SQL 入口，现已删除；
- 阶段 0A 时后端只有
  `api/admin/db_browser_routes.py::execute_readonly_query()` 一个执行入口，现已删除；
- `tests/test_admin_db_browser.py` 中的调用全部是安全合同和序列化测试，不是生产
  管理员常用查询样本；
- 仓库中没有保存查询、查询模板、收藏查询或命名报告引用 `/db/query`；
- 结构化表浏览已经提供白名单、分页、默认排序、字段隐藏、脱敏和长文本预览。

本轮不增加完整 SQL 日志。若阶段 1 实施前确有短期观察需求，只允许记录：

- 规范化 query fingerprint；
- 涉及的白名单表名；
- 调用计数和成功／失败类别；
- 不记录 SQL 原文、参数、结果、用户身份或消息正文。

## 2. 已知调用场景

| ID | 当前证据 | 当前能力 | 结构化替代 | 决定 | 阶段 1 验收 |
|---|---|---|---|---|---|
| DBQ-01 | WebUI `DbPage` 的 SQL 文本框 | 管理员手写任意 `SELECT` | 删除文本框；改用 `GET /db/views` 和 `POST /db/views/{view_id}/rows` | 替代 | 已完成；页面只发送结构化 filter、cursor 和 limit |
| DBQ-02 | `SELECT updated_at FROM content_block_rules` 测试 | 验证关键字检测不会误伤 `updated_at` | `content_block_rules` View 返回登记字段并应用固定排序、游标和脱敏 | 替代 | 已改为结构化 View 合同测试 |
| DBQ-03 | `SELECT pattern, embedding FROM persona_behaviors` 测试 | 验证二进制和长文本安全序列化 | `persona_behaviors` View 使用统一安全序列化器 | 替代 | 已改为 View 序列化测试 |
| DBQ-04 | 查询 `sensitive_data`／`sqlite_master` 测试 | 验证 SQL 入口拒绝敏感表 | 删除 SQL 入口；表浏览白名单继续拒绝这些表 | 替代并强化 | 路由不存在，白名单测试保留 |
| DBQ-05 | 非白名单 JOIN 测试 | 验证正则提取 JOIN 表并拒绝 | 删除 SQL 入口；结构化 API 不接受 SQL 或 JOIN | 退役 | 删除 SQL parser 合同测试 |
| DBQ-06 | 多语句／写语句／非法 SQL 测试 | 验证只读 SQL parser | 删除 SQL 入口后不再需要产品层 SQL parser | 退役 | 全仓无自由 SQL DTO／validator |
| DBQ-07 | 缺失列返回“内部错误”测试 | 验证不回显数据库异常 | 结构化表浏览和命名报告继续统一安全错误 | 替代 | 结构化端点错误不含 SQL／内部异常 |
| DBQ-08 | 任意过滤、聚合、跨表诊断 | 仓库无保存查询或 UI 工作流证据 | 有明确运营问题时新增命名报告 DTO；未登记报告拒绝 | 按需新增，不保留后门 | ReleaseImpactRegistry 中可列出报告 owner 和字段合同 |

## 3. 当前结构化覆盖

`AdminTableViewDescriptorRegistry` 已直接复用共享 Registry Kernel，按领域分组：

- 核心对话：`users`、`chat_logs`、`conversation_turns`、摘要和摘要任务；
- 画像与记忆：画像事实、行为、群记忆、表达、黑话和表情；
- RAG：索引、任务、知识来源、文档和 chunk；
- Runtime：Agent Run、Tool Call、模型请求日志、工具决策和 Prompt 日志；
- 配置与规则：会话配置、任务、审计、屏蔽规则和系统设置。

以下高敏字段由后端 Descriptor 统一隐藏、脱敏或只给预览，调用方不能上传列名、
排序或 SQL 来绕过：

- `headers_json`；
- `request_json`、`response_json`、`message_sources_json`；
- Prompt 变量和渲染正文；
- embedding／二进制列；
- 长消息和长期记忆正文。

## 4. 命名报告准入合同

阶段 1 若发现表浏览不能回答某个明确运营问题，只能新增命名报告，至少声明：

- 稳定 report ID、owner module 和用途；
- 输入 DTO、允许过滤字段、分页和排序上限；
- 输出 DTO 及每个字段的数据敏感等级；
- 固定 SQL／Repository 实现，调用方不能上传 SQL 片段；
- 字段脱敏、最大扫描窗口、超时和行数上限；
- 审计事件和 characterization test；
- ReleaseImpactRegistry 影响项和移除条件。

不接受下列替代形式：

- `query`、`where`、`order_by` 或 `join` 自由文本；
- 前端拼 SQL；
- “管理员权限高，所以允许任意 SELECT”；
- 通过 GraphQL／模板语言重新包装任意数据库表达式。

## 5. 阶段 1 可执行验收

阶段 1 完成时必须同时满足：

1. `POST /api/v1/admin/db/query` 路由不存在；
2. `DbQuery`、`execute_readonly_query`、只为自由 SQL 服务的 parser 全部删除；
3. WebUI 没有 SQL 文本框和 `api.post('/db/query', ...)`；
4. 表浏览的白名单、分页、字段隐藏、脱敏和安全序列化测试继续通过；
5. 每个保留的管理员查询场景都映射到表浏览或登记过的命名报告；
6. 全仓搜索 `/db/query` 只允许出现在迁移文档和明确的 tombstone 测试中；
7. 不以“未知管理员可能会用”为理由保留任意 SQL 后门。

## 6. 阶段 1.1 实施证据

- `POST /api/v1/admin/db/query`、`DbQuery`、SQL parser 和 Web SQL 编辑器均已删除。
- View Descriptor 显式声明 owner、可见字段、固定排序、登记过滤器、分页上限、
  脱敏／预览策略和生命周期；Snapshot 暴露 generation 与 SHA-256。
- 行查询只接受 `filters`、opaque `cursor` 和 `limit`；额外 `query`、
  `order_by` 等字段由 Pydantic `extra="forbid"` 拒绝。
- Cursor 绑定 `view_id` 和规范化 filter Hash，不能跨视图或更换过滤条件重放。
- 原安全 Golden 中的 SQL parser 快照已通过批准变更
  `stage1_admin_structured_views` 替换为结构化 View 安全快照；其余五组 Golden
  SHA-256 未变化。
