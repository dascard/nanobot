# Nanobot 架构风险修复设计

## 背景

本设计处理 2026-07-09 架构审查中确认的高风险问题。服务限定在可信内网，继续使用单一服务间 Bearer Token；启动日志继续输出完整 `NANOBOT_ADMIN_TOKEN`，不在本次范围内。

## 目标

1. 阻止敏感配置和受 Guardrail 保护的正文进入普通日志或审计详情。
2. 取消硬编码超级用户默认值，同时保留显式配置后的 passthrough 能力。
3. 消除用户正文与 Prompt 保留标记碰撞导致的空响应。
4. 让首选回复模型保留自动降级，并正确累计终态失败。
5. 保证 Bridge 异常和取消时清理 trace 与请求级 ContextVar。
6. 使用 SQLite 原生在线备份生成 WAL 一致快照。
7. 在 Agent 调用前为带 `message_id` 的 `/chat` 请求建立持久化幂等 claim。
8. 让 Prompt runtime 能识别 default 升级、人工覆盖和已知旧副本。
9. 让 Docker、worker 和 CI 使用同一份代码、KT patch 与验证链路。
10. 避免 Admin 稳定性测试中的同步分类调用阻塞 ASGI 事件循环。
11. 让容器部署使用显式且跨进程一致的 Admin Token，并提供真实 readiness。
12. 锁定 Python 运行依赖，使 CI 与生产镜像安装同一依赖集合。

## 非目标

- 不修改启动 Token 展示行为。
- 不引入终端用户认证、JWT、RBAC、PostgreSQL、Redis 或消息中间件。
- 不删除 `ChatLog` 档案，不改变 `ConversationTurn` 清理语义。
- 不把当前单机部署改造成水平扩展架构；只消除职责重叠并明确进程角色。

## 阶段一：安全与主链路

敏感设置写入仍由 Admin 完成，但审计详情对 `SettingDef.sensitive` 仅记录 `changed=true` 和值的 SHA-256 短指纹。聊天 INFO 日志只记录元数据，不记录 query 或文件 URL。超级用户配置后来由 2026-07-12 设计收敛为唯一环境变量，不再由 Admin 或数据库维护。

Prompt 审计改为检查编译器生成的 section 元数据，不扫描 user/assistant 正文。编译器为 singleton runtime section 记录稳定的 section id，审计只验证 id 的唯一性与必需分支。

`model.reply` 解释为首选模型：通过 enabled/capability/circuit 检查后置于自动候选首位，后续候选去重保留。空响应和系统错误在每次尝试都记失败；仅有效 reply、富工具结果或明确合法终态记成功。Bridge 使用请求级守卫，在异常、取消和所有返回路径幂等结束 trace 并重置 ToolPlan/final-tools ContextVar。

Admin TimingGate 稳定性测试保持串行和现有次数限制，但每次同步 `gate.judge()` 通过 `asyncio.to_thread()` 执行，避免阻塞处理聊天、健康检查和流式响应的事件循环。

## 阶段二：数据一致性

新增 SQLite 快照服务，使用标准库 `sqlite3.Connection.backup()` 从在线源库复制到临时或指定目标。Admin 下载通过临时文件返回并在响应结束后清理；迁移备份复用同一服务。

新增 `inbound_message_claims` 表，唯一键为 `(platform, chat_type, session_id, message_id)`。claim 状态为 `processing/completed/failed`，包含 lease、响应 JSON 和错误摘要。`message_id` 为空时保持现有行为；非空请求在请求归一化后、任何数据库写入、后台任务调度、模型或工具调用前原子 claim。完成请求可重放标准响应；在途重复请求返回 `duplicate_inflight`；过期或失败 claim 可重新获取。流式完成保存最终 done payload，而不是保存 SSE 文本。相同 claim 服务同时接入 `/chat` 与 `/group/message`，覆盖 blocked、silent、no-reply、异常和流式断连收尾，避免不同入口形成两套去重语义。

claim 使用随机 owner token 和条件更新实现 fencing：首次插入通过 SQLite `ON CONFLICT DO NOTHING` 裁决，failed 或 lease 过期记录只能由单条条件 UPDATE 接管；renew、complete 和 fail 必须同时匹配 `processing` 状态与 owner token，旧 worker 不得覆盖新 owner。lease 使用 UTC-naive 时间，默认 15 分钟，长请求由独立短事务续租。claim service 不返回 ORM 对象，且每次调用结束时必须提交或回滚并释放事务；dirty Session 禁止进入该服务，避免 claim 的内部 commit 顺带提交业务数据。

`response_json` 不保存任一入口的原始 HTTP dict，而保存版本化、传输无关的 `CompletedInboundResponse`。结果只表达 `respond/no_reply/silent/wait/blocked`、最终回复、清洗后的 reply meta、原因与必要群聊调度字段，不保存 user、session、group、request 等请求绑定身份。`/chat` 非流式、SSE done 与 `/group/message` 在重放时按当前请求重新组装各自兼容 envelope；未知版本、非法字段或损坏 JSON 必须 fail closed，不能自动重跑已经完成的外部副作用。

外部 LLM/推送 await 前将 ORM 数据复制成不可变 DTO 并结束只读事务，返回后使用新事务写结果。

## 阶段三：Prompt 与发布

runtime 目录保存 base-hash manifest。初始化时：runtime 等于当前 default 时记录基线；runtime 等于 manifest 中旧 base 时自动升级；runtime 同时偏离旧 base 与新 default 时保留并报告 conflict。当前旧 `tasks/timing_gate` 精确匹配已知 canonical 旧 hash，允许自动升级。Admin 返回 active/default/base hash 和 drift 状态。

Python 直接依赖和解析结果分别保存，生产锁包含固定版本与 hash；Docker 和 CI 只安装同一 lock，额外的 `imgkit`、`markdown2` 与 KT 传递依赖不得游离在锁外。KT patch 脚本必须能在不含 `.git` 的纯源码树首次应用并二次幂等校验。

Docker 构建按白名单复制运行文件，在安装 KT 前应用仓库 patch；构建上下文排除数据库、报告、计划和缓存。容器部署要求宿主机显式提供同一个 `NANOBOT_ADMIN_TOKEN`，缺失时 Compose/部署脚本 fail-fast；本地非容器自动生成行为和启动日志展示保持不变。部署脚本默认重建全部运行服务。Web 不再内嵌 session-summary consumer；进程角色通过环境变量明确。

现有 health 保持轻量 liveness，新增 readiness 检查 lifespan 初始化状态、数据库与本地 Prompt runtime；外部 LLM 深探测不作为容器重启条件。Compose 使用 server healthcheck 和 `service_healthy` 依赖。CI 执行 lock 安装与 `pip check`、KT clean-context patch smoke、完整 pytest、Prompt active 检查、WebUI lint/build、Compose config、Uvicorn readiness smoke；实际镜像构建至少在主分支或手动发布工作流执行。

## 失败与兼容策略

- 幂等表迁移只新增表和唯一约束，不改旧数据。
- Prompt 冲突绝不静默覆盖人工内容；服务继续启动但输出明确告警和健康状态。
- 首选模型不可用时自动降级；配置值仍决定第一次尝试。
- 快照失败返回错误，不回退到裸复制。
- 所有新增观测字段避免记录原始 secret、query 或完整 Prompt。

## 验证

每项行为先写失败测试并确认红灯，再做最小实现。阶段内运行相关测试，最终运行完整 `python -B -m pytest tests/ -v -p no:cacheprovider`、锁文件 hash 安装与 `pip check`、KT clean-context patch smoke、WebUI lint/build、`docker compose config`、Uvicorn readiness smoke、Prompt active-path 检查和 WAL 恢复验证。
