# Nanobot 模块化单体治理计划

## 目标

在不改写现有业务语义、不升级 KT 的前提下，先修复生产正确性问题，再用显式
Port、Descriptor、Policy、Registry、生命周期和 composition root 包住现有实现。
每个新抽象必须接入至少一条真实生产路径，不能只停留在独立样例或测试替身。

## 实施切片

1. **P0 生产正确性**
   - 同步 SQLAlchemy 阶段整体移出事件循环，同一 Session 不跨线程使用。
   - 私聊与群聊在 Bridge 前保存可恢复的原始入站消息，维持 claim fencing 语义。
   - 增加 SQLite WAL 可观测维护 owner，不在 busy 状态下危险截断。
   - Bridge 使用显式启动、停止和 fail-closed 生命周期；关闭后不允许惰性复活。
   - 区分 liveness/readiness，readiness 只检查本地必要依赖。

2. **Agent Runtime Port**
   - 建立不依赖 KT、FastAPI、SQLAlchemy 的不可变请求/响应合同。
   - KT 私有字段访问集中到 Adapter，提供 Fake Runtime 和生命周期状态机。
   - 现有 Bridge 从 composition factory 获取 Runtime，并逐步委托生命周期、
     ToolPolicy、conversation、model route、interrupt 等能力。

3. **Descriptor / Policy / Registry**
   - Prompt section 明确来源、阶段、优先级、重复策略、信任级别和编辑能力。
   - Tool 元数据收敛为类型化 Descriptor；wire schema、路由和管理端读取同一 Registry。
   - Memory provider 定义能力、优先级、fallback 与诊断合同，并接入真实检索链路。
   - Prompt/Tool/Memory 注册表冻结后拒绝隐式覆盖；覆盖必须声明策略与来源。

4. **模型 Provider 与配置单一事实源**
   - transport client 不再依赖 core 业务策略；请求清洗、工具过滤、trace 等由上层
     Policy/Adapter 注入。
   - Provider 通过 sync/async Port、Capability Descriptor 和冻结 Registry 暴露。
   - SettingSpec 统一类型、默认值、来源优先级、敏感级别、owner、reloadability、
     deprecation 与跨字段校验，并保留 provenance。

5. **部署与架构门禁**
   - 固定生产镜像 digest；四个固定服务使用非 root、只读根、cap drop、资源限制、
     healthcheck，并保持 sandboxd/Docker Socket 边界。
   - 架构检查覆盖真实合同目录及 clients/core 依赖方向，不检查不存在的占位目录。
   - CI 执行架构检查、致命 lint、Compose 校验、完整后端测试与前端 lint/build。

6. **验证与交付**
   - 先执行各边界契约和 P0 定向回归，再执行完整 `python -m pytest tests/ -v`。
   - 执行 `git diff --check`、Python 编译、架构检查、Compose config；条件允许时执行
     前端 lint/build。
   - 不在未经授权时提交；不覆盖工作区内与本任务无关的用户改动。

## 完成门槛

- 新合同不反向依赖 Adapter 或框架。
- 每个 Registry 的优先级、冲突和冻结语义都有测试。
- Bridge shutdown 后无法通过 getter 复活。
- 异步 HTTP 主路径不持有同步 DB Session 跨越外部 await。
- Prompt、Tool、Memory、Provider 与 Setting 至少各有一条真实调用链使用新合同。
- 完整测试 0 failures；若环境型检查无法运行，必须明确列出而不能宣称完成。

## 执行状态（2026-07-21）

- [x] P0 生产正确性
- [x] Agent Runtime Port 与 KT Adapter
- [x] Prompt / Tool / Memory / Retrieval Descriptor、Policy 与冻结 Registry
- [x] Model Provider Port、Registry 与已迁移模型路由的 SettingSpec 单一来源
- [x] Compose 加固、架构门禁与通用 CI
- [x] 生产接线审计与完整验证

最新验收证据：

- `python -m pytest tests/ -v`：5237 passed，7 skipped，0 failed。
- `python scripts/check_architecture.py`：通过。
- `python -m ruff check ...`：通过。
- `python -m compileall -q ...`：通过。
- 开发与生产 override 的 `docker compose ... config --quiet`：通过。
- `webui` 的 `npm run lint` 与 `npm run build`：通过；仅保留既有的大 chunk 警告。

本计划不包含 KT 升级、插件能力扩大、生产部署或 Sandbox 开关启用。`core/database.py`、
`nanobot_kt/bridge.py` 等兼容 façade 仍保留，后续只能按垂直切片继续迁移，不能恢复旧双轨。
