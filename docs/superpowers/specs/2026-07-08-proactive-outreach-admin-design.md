# 主动外呼管理页设计

## 目标

在 Web 管理端新增「主动外呼」页面，用于单用户自用场景下查看并控制主动情感外呼功能。页面应让使用者能快速确认功能是否开启、superuser 是否配置、当前调度/发送记录是否存在，并能查看相关服务日志和 LLM 请求日志。

## 范围

- 新增 Admin API：读取主动外呼状态、业务记录、配置项，并支持更新配置、重载配置、触发一次真实检查。
- 新增 WebUI 页面：导航入口、控制面板、业务记录表、日志预览、LLM 请求预览。
- 保持现有运行语义：不增加 shadow/dry-run，不增加语义闸或禁止清单，不修改 vendor。

## 后端设计

新增 `api/admin/proactive_outreach_routes.py`，路由前缀 `/proactive-outreach`。

- `GET /proactive-outreach/status` 返回 proactive 配置、superuser 配置状态和数量、脱敏业务记录、状态统计、最近相关 LLM 请求摘要。
- `GET /proactive-outreach/logs` 返回 `proactive_outreach_log` 脱敏分页记录，支持 `status` 与 `target_fingerprint` 过滤，不在 URL 中传递原始用户 ID。
- `PUT /proactive-outreach/settings/{key}` 仅允许更新 `proactive_outreach.*`，复用 `SystemSetting` 与 `settings.invalidate()`；超级用户后来收敛为唯一环境变量，不再从 Admin 写入。
- `POST /proactive-outreach/settings/reload` 触发配置重载。
- `POST /proactive-outreach/run-once` 调用现有 `run_outreach_due_once` 或 `run_outreach_once`。这是一次真实检查，可能在 Judge 判定后真实发送。

## 前端设计

新增 `ProactiveOutreachPage`，挂载到 `/proactive-outreach`，放在「运行链路」导航组，靠近 TimingGate 和日志。

页面采用现有深色后台风格，信息密度偏高：

- 顶部概览：开关、superuser 数、业务记录数、最近状态、最近 next_check_at。
- 控制区：启停开关、superuser 输入、心跳/间隔/冲击概率配置、重载配置、执行一次检查。
- 业务记录：展示 id、时间、user、status、forced、next_check_at、reason/message，并可展开 grounding JSON。
- 运行日志：调用现有 `/logs/nanobot.log?q=Proactive outreach` 展示服务日志片段。
- LLM 请求：调用 `/llm-api-logs?source=classifier.timing_proactive` 展示 Judge/近期话题提炼请求，并提供 reply 路由筛选。

## 测试

- 后端测试覆盖路由注册、状态读取、配置更新限制、业务记录列表、手动触发调用。
- 前端静态测试覆盖导航、路由、页面关键 API 调用和控件文案。
- 运行相关定向测试后再跑全量 pytest。
