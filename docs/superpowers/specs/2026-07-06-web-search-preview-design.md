# Web Search 预览测试设计

## 背景

管理后台已经支持 Web Search provider 配置和连接测试，但连接测试只返回成功/失败与样本数量，无法看到真实搜索结果，也无法确认 bot 工具输出给模型的消息形态。用户需要在 Web 端输入测试信息并直接查看搜索结果和“发送给模型的消息”。

## 目标

- 在“搜索 API”页面提供一个可输入查询词、返回条数和 provider 的搜索预览区。
- 搜索预览调用后端已配置 provider，返回真实归一化搜索结果。
- 搜索预览展示 bot 工具会输出给模型的文本，避免后台测试与 bot 实际行为分叉。
- Provider 卡片展示每种搜索 API 的调用次数、成功次数、失败次数和最近错误。
- 保留现有 provider 卡片连接测试，不改变配置保存与密钥脱敏行为。

## 非目标

- 不在预览中调用 LLM。
- 不新增并发聚合、rerank 或 provider 健康度策略。
- 不展示 API Key 或 provider 请求头。
- 不保存查询词、URL 或搜索结果到调用统计表。
- 不把被 `.gitignore` 忽略的运行时 `data/` 模板纳入版本管理。

## 方案

新增 `POST /api/v1/admin/web-search/preview`。请求体包含：

- `query`: 必填搜索词，去空格后不能为空。
- `limit`: 返回条数，限制在 1 到 10。
- `provider`: 可选 provider id，留空时按已启用 provider 顺序 fallback。

后端调用 `search_enabled_providers(db, query, limit, provider_id)` 获取 `WebSearchProviderResult`，再调用统一格式化函数生成模型消息。成功返回：

- `ok: true`
- `provider_id`
- `elapsed_ms`
- `results`
- `message`

失败时返回 HTTP 200 与：

- `ok: false`
- `error_code`
- `provider_id`
- `message`

未知 provider 继续由 `resolve_provider_config`/catalog 语义处理，预览接口不吞掉认证鉴权错误。

## 关键边界

模型消息格式必须只有一处来源。当前 `WebSearchTool` 自己拼接输出文本，本次把拼接逻辑下沉到 `core.web_search.search_runtime.format_provider_result_for_model()`，让 admin 预览和 KT 工具共享同一格式。

## 前端交互

在 provider 列表上方新增测试面板：

- 搜索词输入框。
- Provider 下拉框，默认“自动 fallback”，其余选项来自 provider catalog。
- 数字输入框，范围 1 到 10。
- “搜索”按钮。

结果区展示：

- 运行状态、provider、耗时、结果数量。
- 搜索结果列表：标题、URL、摘要、时间。
- “发送给模型的消息”文本块。

错误区展示后端返回的错误码翻译与原始 message。

## 调用统计

新增 `web_search_provider_usage` 聚合表。每个 provider 一行，字段包括：

- `total_calls`
- `success_calls`
- `failure_calls`
- `last_called_at`
- `last_success_at`
- `last_error_at`
- `last_error_code`
- `last_duration_ms`

统计由运行时搜索路径和 provider 连接测试共同写入。缺少 API Key、provider 未启用等没有真正出站搜索的短路错误不计入调用次数；provider 认证失败、限流、超时、坏响应等会计入失败次数。

## 测试

- 后端路由测试：preview 成功返回结果和模型消息。
- 后端路由测试：preview 传入 provider、limit 时透传到搜索运行时。
- 后端路由测试：preview 失败返回 `ok:false`，不 500。
- 工具测试：`WebSearchTool` 输出继续来自共享格式化函数。
- 前端结构测试：WebSearch 页面包含 preview API 调用和“发送给模型的消息”展示。
- 统计测试：provider 列表默认返回零调用；preview/test 成功或失败后对应 provider 计数递增。
- 迁移测试：schema migration 创建 `web_search_provider_usage` 表。
