# 出站投递、Prompt 与配置治理整改实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或
> `executing-plans` 逐任务实现本计划。步骤使用复选框（`- [ ]`）跟踪进度。

**目标：** 建立可恢复、可审计的主动出站投递状态机，修复 Prompt Runtime 在线分支与模板
治理缺口，并统一配置解析、敏感响应、健康检查和 worker 部署边界。

**架构：** 保留入站私聊专用 `ChatDeliveryOutbox`，新增 source-neutral 的
`OutboundRun`、`OutboundGenerationAttempt`、`OutboundDeliveryOutbox`、
`OutboundDeliveryAttempt`、`OutboundDeliveryCircuit` 和每个 `source_type` 唯一的
`OutboundDeliveryControl`。
Prompt Runtime 使用统一来源解析与显式模板迁移；设置、探测和日志通过共享解析/序列化器消除
多套默认值和敏感原值泄露。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy 2、SQLite、aiohttp、pytest、pytest-asyncio、
Docker Compose、Prompt Runtime V2。

---

## 执行约束

- 不修改 QQbot、OneBot/NapCat、QQ push 请求协议或 CQ renderer。
- 不修改身份、人设、图片提示或其他 Prompt Markdown 正文；只允许 flow JSON、合同代码、
  来源元数据和迁移工具变化。
- 不读取、修改或暂存工作区 `nanobot.db`；迁移测试使用临时 SQLite。
- 不记录具体超级用户账号、完整目标、完整消息、token、密钥或未脱敏响应正文。
- 不使用 `git add -A` 或 `git add .`。每个提交步骤都是检查点；在用户再次明确说“提交”前，
  保持未勾选且不执行。
- 每个任务遵循红灯、最小实现、定向绿灯、关联回归、独立复审的顺序。
- 测试命令统一清除代理环境：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest <tests> -v
```

## 文件职责

### 安全与配置

- 创建 `core/safe_diagnostics.py`：响应摘要长度限制、敏感键和凭据 URL 脱敏。
- 修改 `core/settings_service.py`：返回设置实际来源，支持 canonical/legacy env 优先级。
- 修改 `api/admin_routes.py`：GET/PUT/reset 共用敏感设置序列化器。
- 创建 `core/model_route_health.py`：共享模型路由探测和错误分类。
- 修改 `clients/classifier_client.py`、`bootstrap/network_check.py`、
  `api/admin/model_routes.py`：复用同一 classifier route 和探测器。
- 创建 `core/build_info.py`：环境变量优先、本地 Git 回退的构建元数据解析。
- 修改 `api/admin/system_routes.py`：复用构建元数据解析器。
- 修改 `config.py`、`core/config_registry.py`、`bootstrap/schedulers.py`：拆分记忆摘要调度配置、
  统一 Sentinel 路径和 session-summary worker mode。
- 修改 `docker-compose.yml`、`.env.example`、`requirements.txt`，创建 `pytest.ini`：部署和测试
  环境收敛。

### Prompt Runtime

- 修改 `prompts.v2.default/chat/flow.json`、`data/prompts_v2/chat/flow.json`：加入
  `internal/private`，不改 Prompt Markdown。
- 修改 `core/prompt_v2/flow_contract.py`、`flow.py`、`flow_migrations.py`：五个 live branch 和
  flow v2 语义迁移。
- 创建 `core/prompt_v2/template_resolution.py`：统一 active/runtime/default 路径、hash 与来源。
- 修改 `core/prompt_v2/template_loader.py`、`compiler.py`、`schema.py`、
  `nanobot_kt/prompt_runtime.py`、`core/tracing.py`：传播并持久化完整 resolution map。
- 修改 `core/prompt_v2/task_contracts.py`、`variables.py` 和记忆摘要调用适配器：补在线任务合同
  与非空输入校验；`private_decision` 继续 code fallback。
- 创建 `core/prompt_v2/template_baseline.py`、`template_migration.py` 和
  `scripts/manage_prompt_templates.py`：只读 audit 与显式 plan/apply/resolve/rollback。

### 主动出站

- 修改 `core/database.py`、`core/schema_migrations.py`：新增运行、生成尝试、投递、网络尝试、
  circuit 和 cutover control，并增加来源投影字段。
- 创建 `core/outbound_delivery_schema.py`：SQLite schema/index 严格验证。
- 创建 `core/outbound_delivery.py`：occurrence、generation attempt、outbox、delivery attempt、
  circuit 和 cutover control 的同步状态机。
- 创建 `core/outbound_transport.py`：结构化 `DeliveryOutcome` 和 QQ push HTTP 分类。
- 创建 `core/outbound_delivery_service.py`：一次 fenced 领取、传输和结算。
- 创建 `workers/outbound_delivery_worker.py`：独立投递 worker。
- 修改 `core/daily_digest.py`、`api/task_routes.py`：定时任务只生成并入队。
- 修改 `core/proactive_outreach.py`：保留评估租约，把发布阶段切换到通用 outbox。
- 创建 `api/admin/outbound_delivery_routes.py` 并修改 `api/routes.py`：脱敏查询、circuit reset 和
  人工重放。

## 任务 1：有界脱敏日志与敏感设置响应

**文件：**
- 创建：`core/safe_diagnostics.py`
- 修改：`core/daily_digest.py`
- 修改：`core/settings_service.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_safe_diagnostics.py`
- 测试：`tests/test_admin_api.py`
- 测试：`tests/test_config_registry.py`
- 测试：`tests/test_daily_digest.py`

- [x] **步骤 1：为安全摘要和三种设置响应写失败测试**

```python
def test_safe_response_summary_redacts_before_truncating():
    raw = '{"token":"secret-value","detail":"' + "x" * 5000 + '"}'
    value = safe_response_summary(raw, max_chars=256)
    assert len(value) <= 256
    assert "secret-value" not in value

def test_sensitive_setting_get_put_and_reset_never_return_raw_value(
    client, auth_header, monkeypatch,
):
    key = "model.providers.newapi.api_key"
    monkeypatch.setenv("NEW_API_KEY", "environment-secret")
    get_before = client.get("/api/v1/admin/settings", headers=auth_header)
    put = client.put(
        f"/api/v1/admin/settings/{key}",
        json={"value": "database-secret"},
        headers=auth_header,
    )
    reset = client.post(f"/api/v1/admin/settings/{key}/reset", headers=auth_header)
    assert put.json()["value"] is None
    assert reset.json()["value"] is None
    assert put.json()["configured"] is True
    assert reset.json()["configured"] is True
    assert reset.json()["source"] == "environment"
    combined = get_before.text + put.text + reset.text
    assert "database-secret" not in combined
    assert "environment-secret" not in combined
```

另用非敏感 `new_api.timeout` 断言 reset 后返回环境变量中的实际数值与
`source=environment`，防止错误回退为空/default 也通过。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_safe_diagnostics.py tests/test_admin_api.py tests/test_config_registry.py -v
```

预期：缺少 `safe_response_summary`、PUT 回显原值、reset 没有读取环境回退而失败。

- [x] **步骤 3：实现共享安全摘要与 `ResolvedSetting`**

```python
@dataclass(frozen=True)
class ResolvedSetting:
    key: str
    value: object
    source: Literal[
        "database", "environment", "legacy_database", "legacy_environment", "default"
    ]

def serialize_setting(defn: SettingDef, resolved: ResolvedSetting) -> dict[str, object]:
    if defn.sensitive:
        return {"value": None, "display_value": "****",
                "configured": bool(str(resolved.value or "").strip()),
                "source": resolved.source}
    return {"value": resolved.value, "display_value": str(resolved.value),
            "configured": True, "source": resolved.source}
```

`SettingsService` 的缓存同时保存值与来源；reset 删除 DB 行、提交、invalidate，再调用
`get_resolved(key)`。`core/daily_digest.py` 的失败日志只写 `safe_response_summary()`。

- [x] **步骤 4：运行定向绿灯与设置关联回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_safe_diagnostics.py tests/test_admin_api.py tests/test_config_registry.py \
  tests/test_admin_proactive_outreach.py -v
```

- [ ] **步骤 5：提交检查点（等待用户再次明确“提交”）**

```bash
git add core/safe_diagnostics.py core/daily_digest.py core/settings_service.py \
  api/admin_routes.py tests/test_safe_diagnostics.py tests/test_admin_api.py \
  tests/test_config_registry.py tests/test_daily_digest.py
git commit -m "fix(配置安全): 统一敏感响应与失败日志脱敏"
```

## 任务 2：统一分类器路由、探测、Sentinel 与构建信息

**文件：**
- 创建：`core/model_route_health.py`
- 创建：`core/build_info.py`
- 修改：`clients/classifier_client.py`
- 修改：`bootstrap/network_check.py`
- 修改：`bootstrap/lifespan.py`
- 修改：`api/admin/model_routes.py`
- 修改：`api/admin/system_routes.py`
- 修改：`config.py`
- 测试：`tests/test_classifier.py`
- 测试：`tests/test_admin_model_routes_split.py`
- 测试：`tests/test_bootstrap_server.py`
- 测试：`tests/test_deploy_config.py`
- 测试：`tests/test_admin_api.py`

- [x] **步骤 1：写运行路由与探测路由一致、错误分类和 env-first build info 红灯**

断言 `resolve_model_route("timing_gate")` 的最终 `base_url/model/api_key` 被启动探测和管理探测
逐字段复用，并 monkeypatch 唯一 `async probe_model_route` 证明 startup 与 Admin 均 await 同一函数
对象；分别模拟 timeout、connection refused、401、503、空模型列表和目标模型缺失。断言事件
循环内没有 `asyncio.run` 或同步网络 I/O。断言设置构建环境变量且没有 `.git` 时，启动和管理
接口返回相同 commit/branch/build time。另断言 Sentinel 无 env 时 config/loader 均为
`./sentinel`，env override 后两者仍一致。

向启动配置注入真实形态的 Admin/API token、带 userinfo/query credential 的 URL 和包含凭据的
异常对象，用 `caplog` 断言日志只包含 `configured=true/false`、结构化状态和逻辑 route；所有
原值、userinfo、query token 和完整异常文本零命中。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_classifier.py tests/test_admin_model_routes_split.py \
  tests/test_bootstrap_server.py tests/test_deploy_config.py -v
```

- [x] **步骤 3：实现共享探测器和构建信息解析器**

唯一异步 `async probe_model_route(route, session)` 返回：

```python
@dataclass(frozen=True)
class ModelRouteHealth:
    status: Literal[
        "ready", "not_configured", "provider_disabled", "timeout",
        "connection_refused", "dns_error", "auth_failed", "client_error",
        "server_error", "invalid_models_response", "model_not_ready", "network_error",
    ]
    reachable: bool
    usable: bool
    status_code: int | None
    latency_ms: int
```

`run_startup_network_check()` 改为 async，`bootstrap/lifespan.py` 直接 await；Admin route 也 await
同一 probe。启动探测不再读取独立的 classifier URL 默认值。Sentinel 默认路径由 `config.py`
的唯一 resolver 返回 `./sentinel`，loader 不再直接读另一套默认值。`resolve_build_info()` 先读
镜像环境变量，再尝试 Git。

- [x] **步骤 4：运行定向绿灯**

使用步骤 2 的完整命令，预期全部通过。

- [ ] **步骤 5：提交检查点（等待授权）**

```bash
git add core/model_route_health.py core/build_info.py clients/classifier_client.py \
  bootstrap/network_check.py bootstrap/lifespan.py api/admin/model_routes.py api/admin/system_routes.py \
  config.py tests/test_classifier.py tests/test_admin_model_routes_split.py \
  tests/test_bootstrap_server.py tests/test_deploy_config.py tests/test_admin_api.py
git commit -m "fix(运行配置): 统一分类器探测与构建信息来源"
```

## 任务 3：拆分记忆摘要调度并收敛 worker 部署

**文件：**
- 修改：`core/config_registry.py`
- 修改：`core/settings_service.py`
- 修改：`api/admin_routes.py`
- 修改：`config.py`
- 修改：`core/daily_digest.py`
- 修改：`bootstrap/schedulers.py`
- 修改：`workers/semantic_index_worker.py`
- 修改：`docker-compose.yml`
- 修改：`.env.example`
- 修改：`README.md`
- 修改：`requirements.txt`
- 创建：`pytest.ini`
- 测试：`tests/test_config_registry.py`
- 测试：`tests/test_admin_api.py`
- 测试：`tests/test_daily_digest.py`
- 测试：`tests/test_deploy_config.py`
- 测试：`tests/test_bootstrap_server.py`
- 测试：`tests/test_semantic_index_worker.py`
- 测试：`tests/test_model_router.py`

- [x] **步骤 1：写 canonical/legacy 优先级、动态调度和单 worker 红灯**

使用临时 SQLite 覆盖 `memory_digest.scheduler_enabled`、`memory_digest.schedule_hour`、canonical
env、旧 `daily_digest.*` DB 行、旧 `DAILY_DIGEST_*` 和默认值，固定优先级为
`canonical DB > canonical env > legacy DB > legacy env > default`。覆盖 canonical/legacy DB 冲突、
`source=legacy_database`、一次性告警和重复解析/迁移幂等；修改 canonical DB 设置后下一轮
scheduler 使用新值。

解析 Compose，断言当前已存在的 session-summary 和 semantic-index worker 均无 `env_file`，实际
environment key 是设计 allowlist 的子集且包含必需集合；与管理 token、API token、superuser、
图片 token 和不相关 provider key 的 denylist 交集为空。确认 server 为 `external`，只有独立
session-summary worker 运行循环。outbound worker 尚未实现，其 Compose 服务和白名单测试延期到
任务 11，与真实 worker 同步交付。

静态断言 `requirements.txt` 包含 `pytest-asyncio`、根 `pytest.ini` 为 strict、`.env.example` 中
DIFY 零命中且包含固定 required key set，所有敏感示例为空。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_config_registry.py tests/test_admin_api.py tests/test_daily_digest.py \
  tests/test_bootstrap_server.py tests/test_deploy_config.py \
  tests/test_semantic_index_worker.py tests/test_model_router.py -v
```

- [x] **步骤 3：实现 canonical 设置和部署模式**

canonical key/env 为：

```text
memory_digest.scheduler_enabled / MEMORY_DIGEST_SCHEDULER_ENABLED
memory_digest.schedule_hour / MEMORY_DIGEST_SCHEDULE_HOUR
```

旧 DB/env 键只作一轮低优先级 alias，来源分别为 `legacy_database/legacy_environment` 并一次性
告警。`NANOBOT_SESSION_SUMMARY_WORKER_MODE` 严格接受
`embedded|external|disabled`；裸进程默认 embedded，Compose server 显式 external。删除 Dify
示例，补真实非敏感配置；session-summary 使用专用 `LLM_MODEL_SESSION_SUMMARY`，并保留
`LLM_MODEL_FAST` 一周期兜底。`SEMANTIC_INDEX_ENABLED=0` 时 worker 在领取任务前停止消费。加入
`pytest-asyncio` 并设置 `asyncio_mode = strict`。

- [x] **步骤 4：运行定向绿灯并验证 Compose 配置**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_config_registry.py tests/test_admin_api.py tests/test_daily_digest.py \
  tests/test_bootstrap_server.py tests/test_deploy_config.py \
  tests/test_semantic_index_worker.py tests/test_model_router.py -v
docker compose config --quiet
```

- [ ] **步骤 5：提交检查点（等待授权）**

```bash
git add core/config_registry.py core/settings_service.py api/admin_routes.py config.py core/daily_digest.py bootstrap/schedulers.py \
  workers/semantic_index_worker.py \
  docker-compose.yml .env.example README.md requirements.txt pytest.ini \
  tests/test_config_registry.py tests/test_admin_api.py tests/test_daily_digest.py tests/test_deploy_config.py \
  tests/test_bootstrap_server.py tests/test_semantic_index_worker.py tests/test_model_router.py
git commit -m "fix(调度部署): 拆分记忆摘要配置并限制 worker 环境"
```

## 任务 4：增加 `internal/private` Prompt live branch

**文件：**
- 修改：`prompts.v2.default/chat/flow.json`
- 修改：`data/prompts_v2/chat/flow.json`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/flow_contract.py`
- 修改：`core/prompt_v2/flow_migrations.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`scripts/manage_prompt_flow.py`
- 测试：`tests/test_prompt_v2_core_contract.py`
- 测试：`tests/test_prompt_flow_session_guidance_migration.py`
- 测试：`tests/test_outreach_prompt_runtime.py`
- 测试：`tests/test_bridge_prompt_v2.py`
- 测试：`tests/test_prompt_v2_template_registry.py`
- 测试：`tests/test_prompt_runtime_bootstrap.py`

- [x] **步骤 1：写五分支矩阵和 flow v2 迁移红灯**

```python
@pytest.mark.parametrize("platform,chat_type", [
    ("qq", "group"), ("qq", "private"),
    ("web", "group"), ("web", "private"),
    ("internal", "private"),
])
def test_live_prompt_branch_strict_compile(platform, chat_type): ...

def test_internal_group_is_rejected(): ...
def test_internal_private_has_private_policy_without_qq_policy(): ...
```

迁移测试证明只允许顶层 `version: 1 -> 2` 与唯一 `base_contract -> private_policy` 边加入
`internal` 两处语义变化，保留自定义字段、重复执行幂等，
核心边冲突时原文件字节不变。CLI 必须先 `plan` 再 `apply --plan-id`，apply 前 hash 变化时零
写入；bootstrap 不自动改写已有 runtime。增加真实 `build_prompt_runtime` 和 NanobotBridge 研究
链路测试，证明 `platform=internal` 到达严格编译器，不以 fake bridge 代替。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_prompt_v2_core_contract.py \
  tests/test_prompt_flow_session_guidance_migration.py \
  tests/test_outreach_prompt_runtime.py -v
```

- [x] **步骤 3：实现 live matrix 和语义迁移**

先交付 flow v2 `plan/apply` 并显式更新已知 runtime；只有 apply 后五分支 strict audit 通过，
才从单一 `LIVE_PROMPT_BRANCHES` 启用平台白名单。flow version 升级为 2，Web 私聊边的平台条件
扩展为 `web, internal`；不修改任何 Markdown 模板。启动只审计，不自动迁移已有 runtime。
该窄迁移只接受已经包含规范 `session_guidance` 节点及关系的当前 v1 基线；跳过此前版本的更旧
Flow 会以明确错误零写入，必须先完成上一阶段的 `session_guidance` Flow 迁移，不能在本次计划中
静默组合额外结构变化。

红灯证据：`24 failed, 102 passed`；失败集中在缺少 internal live branch、Flow v2、迁移 API/CLI
和启动仍自动改写旧 Flow。代码质量复审随后复现同一 `plan_id` 在成功响应丢失后不能重放，新增
顺序重放和并发双 apply 红灯为 `2 failed`；实现以 source/target/stale 三态关闭该缺口，并增加
真实锁入口观测、原子替换故障、严格 JSON 类型和路径安全回归。

- [x] **步骤 4：运行定向绿灯并确认 QQ/Web hash 不变**

使用步骤 2 命令，并新增断言四个既有分支在相同模板输入下 messages/section hash 不变。

验收证据：

- 任务 4 六文件矩阵：`162 passed, 1 warning`；
- 全量测试：`3421 passed, 6 skipped, 1136 warnings`；
- 两轮独立只读复审最终结论：`0 Critical / 0 Important / GO`；
- `ruff check api bootstrap clients core nanobot_kt workers scripts tests`、`compileall`、
  `git diff --check` 和 `docker compose config --quiet` 均通过；
- default/runtime Flow 字节一致，Prompt Markdown 和 QQ renderer 差异均为 0；
- 暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，敏感账号扫描命中为 0。

说明：`ruff check .` 仍会扫描任务外 `.codex/skills/ui-ux-pro-max` 第三方技能脚本并报告其既有
14 个 lint 问题；本任务未修改该目录，项目源码与测试范围的 Ruff 验证为全绿。

- [ ] **步骤 5：提交检查点（等待授权）**

按文件逐一 `git add`，提交信息：

```text
fix(Prompt流): 接入内部私聊严格分支
```

## 任务 5：统一 Prompt 模板来源与持久追踪

**文件：**
- 创建：`core/prompt_v2/template_resolution.py`
- 修改：`core/prompt_v2/template_loader.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`core/prompt_v2/schema.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 修改：`core/tracing.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 测试：`tests/test_prompt_v2.py`
- 测试：`tests/test_bridge_prompt_v2.py`
- 测试：`tests/test_prompt_trace_admin.py`
- 测试：`tests/test_schema_migrations.py`
- 测试：`tests/test_prompt_runtime_request_contract.py`
- 测试：`tests/test_prompt_runtime_session_guidance.py`
- 测试：`tests/test_streaming_bridge.py`

- [x] **步骤 1：写 default/runtime/mixed 三态来源红灯**

测试 runtime 缺失时 `runtime_path is None`，default 路径和 hash 正确；混合来源时
`prompt_source == "mixed"`。捕获最终 `PromptRenderLog` 与 `AgentRun`，断言完整 resolution map
逐节点一致且没有正文。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_prompt_trace_admin.py -v
```

- [x] **步骤 3：实现 `TemplateResolution` 单一解析器**

```python
@dataclass(frozen=True)
class TemplateResolution:
    template_key: str
    active_source: Literal["runtime", "default", "built_in"]
    active_path: str | None
    runtime_path: str | None
    default_path: str | None
    active_sha256: str
    runtime_sha256: str | None
    default_sha256: str | None
    baseline_version: str | None
    drift_status: str
```

`PromptPlan.debug["template_resolutions"]` 使用共享 serializer；旧 `template_paths` 只做兼容。
数据库新增不截断的 `prompt_template_resolutions_json`，迁移和 ORM 同步。

实现补充：模板文件摘要统一从原始 bytes 计算，保留 CRLF 与 frontmatter；调用方 debug 不能
覆盖代码拥有的路径、resolution 或请求 SHA。`prompt_sha256` 继续表示最终
`messages + tools` 请求信封，base 模板文件 SHA 只从 resolution map 读取。live runtime 缺少
`base_contract` 或 resolution 非法时 fail closed；PromptTracer 的 variables 副本不再重复保存
`template_resolutions/template_paths`，权威 map 只写入独立非截断列。

- [x] **步骤 4：运行定向和追踪关联回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_prompt_trace_admin.py \
  tests/test_llm_request_tracing.py tests/test_prompt_v2_template_admin.py -v
```

验收证据：

- 首轮合同红灯：`5 failed`，分别命中 loader 无 resolution、无共享模块、tracer 无 map 参数、
  Admin 无结构化来源和旧库无增量列；补充红灯覆盖 debug 伪造、fresh schema 缺 server default、
  live runtime 空 map 与追踪 variables 冗余；
- 扩展定向矩阵：`198 passed, 317 warnings`；
- 全量测试：`3430 passed, 6 skipped, 1197 warnings`；
- 独立只读复审在阻塞修复后结论为 `0 Critical / 0 Important / GO`；
- 项目范围 Ruff、`compileall`、`git diff --check` 和 `docker compose config --quiet` 均通过；
- default/runtime Flow 字节一致，Prompt Markdown 和 QQ renderer 差异均为 0；
- 暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，敏感账号扫描命中为 0。

- [ ] **步骤 5：提交检查点（等待授权）**

提交信息：`fix(Prompt追踪): 记录真实模板来源与路径`。

## 任务 6：补齐在线 task 输入合同并收窄静默回退

**文件：**
- 修改：`core/prompt_v2/task_contracts.py`
- 修改：`core/prompt_v2/variables.py`
- 修改：`core/prompt_v2/task_templates.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`app/memory_digest/llm_builder.py`
- 修改：`clients/classifier_client.py`
- 修改：`core/private_timing.py`
- 修改：`core/daily_digest.py`
- 测试：`tests/test_prompt_v2_task_contracts.py`
- 测试：`tests/test_memory_digest_builder_quality.py`
- 测试：`tests/test_classifier.py`
- 测试：`tests/test_memory_digest.py`
- 测试：`tests/test_prompt_v2_template_registry.py`
- 测试：`tests/test_prompt_runtime_bootstrap.py`
- 测试：`tests/test_prompt_v2.py`
- 测试：`tests/test_prompt_flow_session_guidance_migration.py`

- [x] **步骤 1：写 memory-digest 双模板、invocation manifest 和非空值红灯**

登记 `tasks/memory_digest_system` 与 `tasks/memory_digest_user`。逐个删除 required variable 均应
在启用前失败；调用值缺键、`None`、空字符串和空集合分别在每次 render 前失败。扫描 active
task 文件，要求每项有合同或显式 dormant/code-fallback 声明；invocation manifest 和源码快照
证明所有 live task 都通过统一 wrapper，不能直接调用底层 renderer 绕过合同。

对 `digest_source` 等关键值为空的工作流断言 summarizer/LLM 调用次数为 0，返回现有确定性摘要
或显式失败，日志/任务保持未完成且可重试，不写“已摘要”成功标记。

- [x] **步骤 2：写 `private_decision` 异常边界红灯**

`code_fallback_only` 不尝试读取模板；预期网络故障只由业务边界唯一 owner 转为结构化降级，
非法模型输出按 parser 策略处理，注入的 `TypeError` 等编程错误不得被
`clients/classifier_client.py` 的宽泛捕获转成正常 `reply_now/no_reply`。本轮不创建或修改
Prompt Markdown。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_prompt_v2_task_contracts.py tests/test_memory_digest_builder_quality.py \
  tests/test_classifier.py tests/test_memory_digest.py -v
```

- [x] **步骤 4：实现 `non_empty_call_values` 与 paired render mode**

```python
@dataclass(frozen=True)
class TaskContract:
    required_variables: frozenset[str]
    required_call_values: frozenset[str]
    non_empty_call_values: frozenset[str]
    # 保留现有输出合同字段
```

记忆摘要 system/user 继续分消息渲染，但统一经过 invocation wrapper 和合同校验。启动审计只
验证模板引用、parser 和 invocation 注册；动态值在每次 render 前 fail closed。只捕获预期的
模板缺失异常；模型故障由调用层结构化降级，意外编程错误传播。

- [x] **步骤 5：运行定向绿灯**

使用步骤 3 命令，预期全部通过。

验收证据：

- 首轮主合同红灯：`21 failed, 99 passed`，命中缺失 memory-digest 合同、空值放行、wrapper
  绕过、模板失败仍调用模型和分类器编程错误被吞；补充红灯分别命中失败强制重建归档旧摘要、
  fail-closed 模板策略未参与启动审计、摘要器 `ValueError/RuntimeError/FileNotFoundError` 被误降级；
- 任务 6 主矩阵：`136 passed, 30 warnings`；关联 Prompt、outreach、evolution 矩阵曾达到
  `479 passed, 30 warnings`，最终由全量测试覆盖最新差异；
- 全量测试：`3469 passed, 6 skipped, 1197 warnings`；迁移回归文件单独为 `56 passed`；
- 独立只读复审发现的两个 Important 异常边界问题均已按红绿循环修复，最终结论为
  `0 Critical / 0 Important / GO`；两个 Minor 为 parser owner 仍是声明性字符串、paired
  frontmatter 一致性仍在首次 render 时验证，均不阻塞且运行时 fail closed；
- 项目源码范围 Ruff、`compileall`、`git diff --check` 和 `docker compose config --quiet` 均通过；
  额外全目录 Ruff 仅命中未改动的 `.codex/skills/ui-ux-pro-max` 既有脚本告警；
- default/runtime Flow 字节一致，Prompt Markdown 和 QQ renderer 差异均为 0；
- 暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，当前工作区与 tracked 文件的敏感账号
  赋值扫描命中均为 0。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`fix(Prompt合同): 校验在线任务必需输入`。

## 任务 7：建立 Runtime 模板基线和显式迁移 CLI

**文件：**
- 创建：`core/prompt_v2/template_baseline.py`
- 创建：`core/prompt_v2/template_migration.py`
- 创建：`scripts/manage_prompt_templates.py`
- 修改：`core/prompt_v2/template_resolution.py`
- 修改：`core/prompt_v2/compiler.py`
- 修改：`core/tracing.py`
- 修改：`app/prompt_runtime/preview_service.py`
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`core/prompt_v2/template_store.py`
- 修改：`bootstrap/prompt_runtime.py`
- 测试：`tests/test_prompt_v2_template_registry.py`
- 创建：`tests/test_prompt_v2_template_migration.py`
- 修改：`tests/test_prompt_runtime_bootstrap.py`

- [x] **步骤 1：写七种 drift 状态、baseline blob 完整性与启动零覆盖红灯**

覆盖 `in_sync`、`upgrade_available`、`local_override`、`diverged`、`runtime_missing`、
`untracked_legacy`、`invalid`。已有 runtime 文件在任何启动审计后必须字节不变；关键 active
模板 invalid 时 fail closed。旧 canonical 正文保存到 content-addressed blob；blob 缺失、文件
名 hash 与内容不符或 manifest 指向错误 hash 时，plan/apply 均 fail closed 且零写入。

无状态且 runtime 原始字节等于 canonical 时，只有显式 `adopt-in-sync` 才建立基线；不相等时
保持 `untracked_legacy`，分别测试 keep-runtime、use-default 和 merged-file。所有 hash 输入域是
包含 frontmatter 的原始字节，不得使用渲染正文 hash。

- [x] **步骤 2：写 plan/apply/resolve/rollback 红灯**

测试 plan ID 绑定 base/runtime/canonical hash；plan 后任一文件改变，apply 拒绝且零写入；
三方冲突零写入；成功 apply 先备份再原子替换。对 journal 的 prepared、files_installed、
state_committed 各崩溃点验证恢复；rollback 同时恢复文件、manifest 和迁移 lineage；符号链接和
路径穿越拒绝。

补充安全约束：plan 同时绑定操作类型、target/merged hash、绝对根、manifest hash/revision 与
lineage head；journal 逐文件记录 before/after。恢复遇到第三种字节时零写入并转人工处理。完整
compile 持共享治理锁，CLI、启动 provision、Admin CRUD 与 flow save 持同一排他锁；首次
provision 也必须经过 journal，不能自动认领已有同字节 legacy 文件。

状态变化 `untracked_legacy -> in_sync/local_override/diverged` 后，live PromptPlan、Admin preview、
PromptRenderLog 与 AgentRun 的 resolution map 必须逐字段一致。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_prompt_v2_template_registry.py \
  tests/test_prompt_v2_template_migration.py \
  tests/test_prompt_runtime_bootstrap.py -v
```

- [x] **步骤 4：实现基线清单和显式迁移**

状态目录和只读 baseline blob store 置于 runtime 模板根之外。CLI 提供 `audit`、`plan`、
`apply --plan-id`、`resolve` 和 `rollback`；三方比较必须从校验过的旧 canonical blob 读取，
并复用 flow 的无符号链接、文件锁、fsync、备份和原子替换原语。跨文件变更以持久 journal
协调文件安装与 manifest commit；恢复只能完成 hash 匹配的 plan，否则精确回滚。启动只
provision 缺失文件并写首次基线；已有文件只审计和告警。

- [x] **步骤 5：运行定向绿灯和 CLI 子进程测试**

使用步骤 3 命令，并逐个执行临时目录中的 CLI 子命令，断言 JSON 输出不包含正文。

完成记录（2026-07-14）：

- 已覆盖七种 drift 状态、原始字节 hash、write-once baseline blob、manifest/lineage/plan 绑定、
  三阶段 journal 恢复、第三种字节人工介入、显式 resolve/rollback 和首次 provision 崩溃恢复；
- compile、CLI、启动 provision、Admin 模板 CRUD 与 flow save 已共享模板治理锁，在线读取持有完整
  快照；现有 runtime 文件在启动审计中保持原字节；
- 所有可配置 Prompt 文件读取均通过非阻塞普通文件校验，FIFO、symlink 和其他非法存储输入不会挂起
  启动、在线渲染或 Admin 查询；inactive 非法模板仍进入全量审计；
- active flow、task 及显式登记的 tool usage/workflow 模板在 invalid 或双端缺失时 fail closed；没有
  模板合同的工具不会被误判为缺失；
- CLI 的 `audit`、`plan`、`apply`、`resolve`、`rollback` 均通过真实子进程合同测试，输出不包含
  模板正文；
- 任务 7 核心矩阵为 `116 passed`，Prompt 扩展矩阵为 `396 passed`，flow/异步策略关联矩阵为
  `175 passed`；独立只读复审结论为 `0 Critical / 0 Important / 0 Minor / GO`；
- 完整测试为 `3564 passed, 6 skipped, 0 failed`；项目源码范围 Ruff、`compileall` 和
  `git diff --check` 均通过；
- Prompt Markdown 和 QQ renderer 无差异，暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，
  敏感变量附近的长数字扫描命中为 0。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`feat(Prompt模板): 增加基线审计与显式迁移`。

## 任务 8：新增通用出站持久模型与严格迁移

**文件：**
- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 修改：`core/chat_delivery_outbox_schema.py`
- 创建：`core/outbound_delivery_schema.py`
- 创建：`tests/test_outbound_delivery_schema.py`
- 修改：`tests/test_sqlite_backup.py`
- 修改：`tests/test_chat_delivery_outbox.py`

- [x] **步骤 1：写 ORM/schema 等价、约束和旧数据迁移红灯**

测试六类记录、唯一索引、due/lease 索引、lease 三字段 CHECK、terminal 不可领取约束与 ORM
完全一致：run、generation attempt、outbox、delivery attempt、circuit 和每个 source 唯一的
control。outbox 必须包含 `cancelled/cancelled_at/cancel_reason_type`，attempt 分离 allocated sequence
和 request-started budget，并快照 endpoint config revision；control 对 `source_type` 唯一且包含
mode、epoch、future effective boundary、writer protocol/lease。
临时旧库中的
`ScheduledTask.last_run_at` 只回填 `last_attempt_at`，`last_success_at` 保持 NULL，状态为
`legacy_unknown`；旧 proactive `sending/ambiguous` 进入 `legacy_ambiguous_hold`，不创建
run/outbox，迁移零网络调用。旧 API/tool 不得把 `last_run_at` 当成功或去重依据；调用方行为
改造仍由任务 12 完成，本任务只冻结兼容投影和保守回填合同。

- [x] **步骤 2：写畸形同名表和重复迁移红灯**

错误 nullability、默认值、CHECK 或索引必须 fail closed；重复执行迁移幂等且只创建一次文件
快照。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_outbound_delivery_schema.py tests/test_sqlite_backup.py -v
```

- [x] **步骤 4：实现模型与迁移**

新增 `OutboundRun`、`OutboundGenerationAttempt`、`OutboundDeliveryOutbox`、
`OutboundDeliveryAttempt`、`OutboundDeliveryCircuit` 和 `OutboundDeliveryControl`，并给
`ScheduledTask`、`ProactiveOutreachLog` 增加兼容投影字段。完整 schema 约束按设计文档固定，
所有时间使用 UTC naive。

- [x] **步骤 5：运行定向绿灯**

使用步骤 3 命令，预期全部通过。

落地证据：

- 六张表的 ORM 与迁移路径在列、默认值、具名 CHECK、索引和外键上完全等价；28 个
  `DATETIME` 字段统一接受 SQLite/SQLAlchemy 的 UTC naive 规范文本，日期性质矩阵共验证
  `30,054` 个样例且 `0 mismatch`；
- `ScheduledTask.last_error_summary` 在 ORM 和旧库迁移路径均限制为 1000 字符；旧
  `last_run_at` 仅回填最近尝试，旧 proactive 不确定状态进入人工保留态；
- 任务 8 直接测试为 `133 passed`，schema/migration 联合矩阵为 `204 passed`，完整测试为
  `3699 passed, 6 skipped, 0 failed`；
- 两轮独立只读复审均为 `0 Critical / 0 Important / 0 Minor / GO`；项目源码范围 Ruff、
  `compileall` 和 `git diff --check` 均通过；
- Prompt Markdown 和 QQ renderer 无差异，暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，
  敏感变量附近的长数字扫描命中为 0。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`feat(出站投递): 新增运行队列与逐次尝试模型`。

## 任务 9：实现 occurrence、attempt、circuit 与 cutover 状态机

**文件：**
- 创建：`core/outbound_delivery.py`
- 创建：`tests/test_outbound_delivery.py`

- [x] **步骤 1：写 occurrence 唯一、生成审计和快照不可变红灯**

两个事务并发领取相同 occurrence key，只有一个 owner；同一调度窗口编辑目标、模板或 revision
仍命中原 run。每次模型调用前原子分配 generation attempt，旧 generation token 无法提交。
生成成功与 outbox 插入同事务；目标与 payload 均为不可变快照，来源编辑/删除不改变投递目标。
相同 idempotency key 但不同 payload hash 或 destination snapshot 抛出冲突。

- [x] **步骤 2：写 attempt 单调序号、分层 circuit、replay 聚合和 cutover 红灯**

领取事务递增 allocated attempt sequence，越过 `request_started` 才递增网络预算；发送前崩溃
把旧 attempt 结算为 `abandoned_before_send/cancelled_before_send`，不会复用序号或误耗尽预算；
旧 lease token 不能结算。401 打开
全 endpoint scope，目标不存在只打开 destination scope，固定 envelope/schema 错误打开
payload-contract scope，单正文 413 不开共享 circuit；producer 和 worker 均遵守 circuit，
瞬态故障只使用 outbox 双重预算。配置 revision 使用持久单调版本或显式
`NANOBOT_QQ_PUSH_CONFIG_REVISION`，attempt 快照实际 revision；密钥/URL 轮换时旧 401 不能污染
新 revision，无关设置和重启不改变 revision。ambiguous 默认不可领取；即使 manual request key
不同，同一 active
leaf 也只能 CAS 产生一个 replay，成功聚合为
`succeeded_after_ambiguous_replay` 且旧记录不变。

每个 source control 并发 CAS 只允许 `legacy_direct -> outbox_hold -> outbox_active`；回滚必须走
`outbox_active -> outbox_draining -> legacy_direct`。draining 停止所有 producer，只允许 worker
处理切换时既有 epoch；有 leased/ambiguous 时拒绝进入 legacy，worker 不能消费不匹配 epoch。
legacy direct 也先 claim 相同 occurrence；同 slot 正向/回滚均不二次生成。切换绑定未来
`effective_from`，旧协议 writer 仍存活或 lease 未过期时拒绝 CAS。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest tests/test_outbound_delivery.py -v
```

- [x] **步骤 4：按纯同步状态机实现最小 API**

```python
claim_outbound_run(...)
start_generation_attempt(...)
commit_generated_outbox(...)
fail_outbound_generation(...)
claim_due_outbox(...)
settle_delivery_attempt(...)
expire_stale_delivery_leases(...)
create_delivery_replay(...)
reset_delivery_circuit(...)
transition_delivery_control(...)
```

所有函数只修改传入 Session，不隐式 commit；调用方拥有事务。

- [x] **步骤 5：运行绿灯与并发循环**

使用步骤 3 命令，并把关键并发用例循环 50 次，预期零重复 owner/outbox/replay。

完成证据：

- occurrence、generation/delivery attempt、分层 circuit、manual replay、双预算与 cutover 均由
  同步事务状态机实现，公开函数未调用 `Session.commit()` 或 `Session.rollback()`；
- HTTP 分类按区间 fail closed：除 408/425/429 外的 4xx 必须永久失败，除 501/505 外的
  5xx 必须瞬态失败；401/403/405/501/505 的 endpoint scope 不可被 payload/destination 类别
  覆盖，400/413/422 单 payload 失败不污染共享 circuit；
- terminal settlement 和 manual replay 使用不可变请求指纹保证延迟重试幂等，配置 revision
  仅绑定真实 attempt/outbox；旧 revision circuit 不阻断新 revision 恢复；
- replay 成功后历史 ambiguous parent 保持原始审计状态，draining 仅阻断活动 ambiguous leaf，
  同时继续全量阻断 pending/retry_wait/leased/blocked 和 started attempt；
- 状态机测试为 `122 passed`，schema/migration 六文件关联矩阵为 `327 passed`；包含 11 个
  并发、强制提交顺序和 replay 回切节点的压力集合循环 50 轮，共 550 个节点且零失败；
- 完整测试为 `3822 passed, 6 skipped, 0 failed`；项目源码 Ruff、`compileall` 和
  `git diff --check` 均通过；
- 独立只读终审为 `0 Critical / 0 Important / GO`；剩余 Minor 是纯幂等快路径仍会参与
  SQLite 写锁竞争，作为后续性能与可用性优化；
- Prompt Markdown 和 QQ renderer 无差异，暂存区为空，`nanobot.db`、`cc2codex/` 均未被
  跟踪，敏感账号扫描命中为 0。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`feat(出站投递): 实现租约状态机与持久熔断`。

## 任务 10：把 QQ push 传输结果结构化

**文件：**
- 创建：`core/outbound_transport.py`
- 修改：`core/daily_digest.py`
- 修改：`core/safe_diagnostics.py`
- 修改：`workers/chat_delivery_worker.py`
- 测试：`tests/test_outbound_transport.py`
- 修改：`tests/test_safe_diagnostics.py`
- 修改：`tests/test_daily_digest.py`
- 修改：`tests/test_push_envelope.py`
- 修改：`tests/test_chat_delivery_worker.py`

- [x] **步骤 1：写 HTTP 分类和安全正文红灯**

参数化覆盖 2xx、408、425、429、400/401/403/404/405/410/413/415/422、500/501/502/503/504/505、
connect timeout、connection refused、写后 read timeout。断言 `Retry-After` 有上限，endpoint、
destination、payload-only、transient 和 ambiguous 分类正确。用无限/超大流式响应证明读取达到
字节上限即停止，不能先 `response.text()`；日志和 outcome 中没有长正文或敏感值。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_outbound_transport.py tests/test_push_envelope.py \
  tests/test_chat_delivery_worker.py -v
```

- [x] **步骤 3：实现 `DeliveryOutcome` 和兼容适配器**

新 transport 返回结构化结果；旧 `push_to_qq*` 明确通过适配器映射成原三态，保证私聊恢复
worker 行为不变。新通用出站代码禁止调用三态 API。

- [x] **步骤 4：运行定向绿灯**

使用步骤 2 命令，预期全部通过。

验收证据：

- 初始合同红灯为 `367 failed, 10 passed`，命中结构化 transport 缺失、无限正文读取、HTTP 分类、
  `Retry-After`、异常阶段和旧三态兼容缺口；后续对抗红灯分别复现混合百分号大小写、非规范
  JSON 转义、16 KiB 截断长消息回显和异常文本回显；
- 最终采用 fail-closed 诊断合同：失败正文只做有界白名单错误码解析，任意非空正文统一记录为
  `响应正文已省略`；异常对象文本不进入结果或日志，只按 `error_type` 返回固定中文摘要；
- 传输层矩阵为 `608 passed, 1 warning`，跨 `daily_digest`、push envelope、旧聊天 worker、主动外呼
  和 API 的关联矩阵为 `699 passed, 31 warnings`；
- 独立只读复审结论为 `0 Critical / 0 Important / 0 Minor / GO`，并通过真实本地 HTTP/TCP 探针
  验证重定向、限流、非法状态、TLS、非 HTTP URL、拒绝连接、损坏正文和读取失败；
- 全量测试为 `4438 passed, 6 skipped, 0 failed`；项目 Python 源码 Ruff、`compileall`、
  `git diff --check` 和 `docker compose config --quiet` 均通过；
- default/runtime Flow 字节一致，Prompt Markdown 与 QQ renderer/协议差异均为 0；暂存区为空，
  `nanobot.db`、`cc2codex/` 均未被跟踪，`cc2codex/` 保持忽略，工作区与提交历史敏感账号扫描为 0。

- [ ] **步骤 5：提交检查点（等待授权）**

提交信息：`fix(QQ投递): 结构化分类响应与网络故障`。

## 任务 11：实现独立 outbound delivery worker

**文件：**
- 创建：`core/outbound_delivery_service.py`
- 创建：`workers/outbound_delivery_worker.py`
- 修改：`core/outbound_delivery.py`
- 修改：`docker-compose.yml`
- 修改：`.env.example`
- 创建：`tests/test_outbound_delivery_worker.py`
- 修改：`tests/test_deploy_config.py`

- [x] **步骤 1：写一次领取、同 payload 重试和崩溃恢复红灯**

模拟两个 worker、503 后重试、请求 durable boundary 前崩溃、request_started 后中断和响应后
结算前崩溃。断言重试 payload hash 不变、模型调用为 0、attempt no 不复用、发送前残留 attempt
进入明确终态且不消耗 request-started budget、旧 owner 不能结算、无法证明结果时进入
ambiguous。达到 max request attempts 或 retry deadline 后 exhausted；circuit open、
outbox_hold、epoch 不匹配时 worker 不发送；outbox_draining 只消费切换快照内既有记录。

解析 Compose，断言 outbound worker 无 `env_file`，其 key 精确属于设计文档列出的 DB、日志、
QQ push、config revision、batch/lease/poll 集合，且不接收任何模型 key、
Admin/API token、superuser 或图片 token。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_outbound_delivery_worker.py tests/test_deploy_config.py -v
```

- [x] **步骤 3：实现 worker**

worker 只加载 DB、出口配置和 transport，不导入模型客户端；使用有界 batch、短租约、稳定
owner、双重重试预算、full-jitter 退避与 shutdown event。每次 attempt 在 HTTP 前持久化，并在
`request_started` durable boundary 后才进入可能已发送区间，结算在 fenced 事务。
领取、期限终结和租约恢复按 endpoint 隔离；退避、deadline 与结算时间使用 HTTP 完成时刻。
worker 不读取或覆盖 outbox 已持久化的 max-attempt/deadline 预算。

- [x] **步骤 4：运行定向绿灯和导入边界测试**

断言 worker 源码/模块图不导入 `clients.new_api_client`、Bridge 或模型 registry。

**任务 11 验证证据（2026-07-15）：**

- 初始红灯：新测试因缺少 `core.outbound_delivery_service` 在收集期失败；实现后独立审查新增的
  endpoint 隔离和 HTTP 完成时钟用例为 `4 failed`，均准确命中生产缺口。
- 定向绿灯：worker 与部署合同 `45 passed`；异步入口策略与任务定向集 `48 passed`。
- 关联回归：状态机、worker、结构化 transport、QQ envelope 与部署合同 `782 passed`。
- 独立只读复审：`0 Critical / 0 Important / 0 Minor / GO`；额外验证跨 endpoint 清理、真实延迟
  503、deadline/lease、指数上限、统一日志和导入边界。
- 完整回归首次发现 `asyncio.run` 策略回归：`1 failed / 4469 passed / 6 skipped`；按仓库既有
  `new_event_loop` 入口修复后，重新运行得到 `4470 passed / 6 skipped / 0 failed`。
- 产品代码 Ruff、`compileall`、`git diff --check`、`docker compose config --quiet` 全部通过；
  `ruff check .` 只命中未参与产品构建的 `.codex/skills/ui-ux-pro-max/` 既有 14 项告警，未修改该
  工具包。
- default/runtime flow 字节一致；Prompt Markdown 与 QQ renderer 无本任务差异；暂存区为空；
  已知敏感账号在工作区、跟踪文件和全部 Git 历史中均为零命中；`nanobot.db`、`cc2codex/`
  均未被跟踪，`cc2codex/` 继续由 `.gitignore` 排除；`package-lock.json` 无本任务差异。
- 未执行 `git add` 或 `git commit`；步骤 5 继续等待用户明确授权。

- [ ] **步骤 5：提交检查点（等待授权）**

提交信息：`feat(出站投递): 增加独立投递 worker`。

## 任务 12：定时任务改为生成并入队

**文件：**
- 创建：`core/scheduled_task_outbound.py`
- 修改：`core/daily_digest.py`
- 修改：`api/task_routes.py`
- 修改：`core/outbound_delivery.py`
- 修改：`core/outbound_delivery_service.py`
- 修改：`core/tool_schema_preview.py`
- 修改：`creatures/nanobot/prompts/skills/schedule_task/tool.py`
- 修改：`.env.example`
- 修改：`tests/test_daily_digest.py`
- 修改：`tests/test_api_push_envelope.py`
- 修改：`tests/test_schedule_task_tool.py`
- 创建：`tests/test_scheduled_task_outbound.py`
- 修改：`tests/test_outbound_delivery.py`
- 修改：`tests/test_outbound_delivery_worker.py`
- 修改：`tests/test_deploy_config.py`

- [x] **步骤 1：替换旧“失败也推进 last_run”测试为状态合同红灯**

失败生成更新 `last_attempt_at` 但没有 outbox；生成成功只得到 queued outbox；HTTP 失败不更新
`last_success_at`；worker 成功后才更新 success。历史 `last_run_at` 仅与 attempt 投影同步。

- [x] **步骤 2：写重复 tick、进程崩溃和 circuit gate 红灯**

相同 cron slot 两次 tick 只调用模型一次并创建一个 outbox；同一 slot 中编辑目标、prompt 或
revision 仍不二次生成；编辑/禁用只按状态取消安全记录。生成后进程崩溃可恢复；endpoint circuit
打开后下一 slot 只登记 blocked run，模型调用次数为 0；destination circuit 不误封其他目标。

验证 `legacy_direct -> outbox_hold -> outbox_active` 切换窗口：兼容 legacy/new producer 都先领取
同一 occurrence，hold 阶段只入队不发送，同一 slot 切换不二次生成。回滚必须进入
outbox_draining，期间 producer 零新增、worker 只消费既有 epoch；存在 leased/ambiguous 时失败，
安全 drain 后以未来 effective slot 才允许 legacy。旧协议实例/lease 仍存活时切换被拒绝。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_daily_digest.py tests/test_api_push_envelope.py \
  tests/test_schedule_task_tool.py tests/test_scheduled_task_outbound.py -v
```

- [x] **步骤 4：实现 producer 与手动运行幂等键**

cron occurrence key 只包含 task ID 和规范化计划触发槽；目标、任务类型、prompt 和 revision 是
首次领取时的快照。手动 API 必须提供 request idempotency key。producer 在同一事务内校验
delivery control/cutover epoch 并领取，在事务外生成，在 fenced 事务内入队，不直接发 HTTP。

实现补充：producer 在与任务更新、停用和删除相同的来源控制锁内重读任务，并按最新 cron
复核槽位；过期且无任何 outbox 的 generation run 使用首次快照恢复，畸形快照一次性隔离。
`legacy_direct` 只由专用兼容 drain 恢复原 leaf，普通 worker 永不领取；批次内单 leaf 异常隔离，
不会阻断后续记录或吞掉取消信号。无 outbox 的 blocked run 在来源变更时安全终结，不能在熔断
重置后复活旧 prompt。Bridge 超时与普通异常分别记录 `generation_timeout` 和
`generation_error`，日志和持久化摘要不保存异常正文。

- [x] **步骤 5：运行定向绿灯和端到端 worker 测试**

使用步骤 3 命令并追加 `tests/test_outbound_delivery_worker.py`。

**任务 12 验证证据（2026-07-15）：**

- 审查红灯依次复现来源快照竞态、blocked run 复活、legacy leaf 崩溃后无 owner、生成异常被吞、
  锁外旧 cron 槽和批次头部异常饥饿；每项均先失败再以最小实现转绿。
- 最终 Task 12 调度、API、工具、worker 与部署矩阵：`111 passed`；状态机、worker 和定时任务
  关联矩阵：`182 passed`；出站 schema：`134 passed`；结构化 transport：`608 passed`。
- 请求级工具 overlay：`33 passed`；Bridge Prompt 组装：`20 passed`；首轮全量环境失败所涉及的
  文件在可写、无全局 runtime 覆盖的验证副本中为 `358 passed`。
- 完整测试在明确排除 `.git`、`nanobot.db`、`cc2codex/`、`.env`、缓存和运行日志的 `/tmp`
  验证副本中运行：`4515 passed, 6 skipped, 0 failed`，验证副本的 Task 12 核心源码与工作区逐字节一致。
- 两轮独立只读复审先复现剩余竞态，再验证真实双会话串行化、上海/UTC cron 子集、legacy
  fencing、单 leaf 隔离和 `CancelledError` 传播，最终结论为
  `0 Critical / 0 Important / 0 Minor / GO`。
- Ruff、`compileall`、`git diff --check` 和 `docker compose config --quiet` 均通过；Prompt
  Markdown 与 QQ renderer 差异为 0，canonical/runtime flow 字节一致。
- 敏感账号在工作区、跟踪文件和全部 Git 历史中均为零命中；运行时代码没有旧管理员变量别名
  或数字赋值。暂存区为空，`nanobot.db`、`cc2codex/` 均未被跟踪，`cc2codex/` 继续由
  `.gitignore` 排除，`package-lock.json` 无工作区或暂存差异。
- 未执行 `git add` 或 `git commit`；步骤 6 继续等待用户明确授权。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`fix(定时任务): 生成后持久入队再投递`。

## 任务 13：主动外呼改为通用 outbox 发布

**文件：**
- 修改：`core/proactive_outreach.py`
- 修改：`core/outbound_delivery.py`
- 修改：`api/admin/proactive_outreach_routes.py`
- 修改：`tests/test_proactive_outreach.py`
- 修改：`tests/test_proactive_delivery_concurrency.py`
- 修改：`tests/test_outreach_contract_regressions.py`
- 创建：`tests/test_proactive_outbound_delivery.py`

- [x] **步骤 1：写评估租约到 run/outbox 的原子关联红灯**

并发评估只生成一个 outreach run/outbox；原评估 grounding 和 judge log 保留。候选 fenced
CAS、唯一 run 建立、`outbound_run_id` 写入和来源状态投影必须在同一事务中完成；分别在候选
CAS 后和 run insert 后注入崩溃，事务均完全回滚。生成完成后不直接调用 push。

- [x] **步骤 2：写 circuit、历史清除和未知结果红灯**

circuit 打开时 judge/research/generator 调用均为 0；history clear 与 worker claim、
`request_started`、settlement 三处竞态均受 fence 保护：安全状态取消，可能已发送状态进入
ambiguous，delivered 保留事实。网络重试不产生新语义正文；遗留 sending/ambiguous 迁移为
`legacy_ambiguous_hold`，无 payload、不可自动重放，重复迁移幂等。worker 来源投影必须校验
candidate version/fence，旧结算不能覆盖新候选。

- [x] **步骤 3：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_proactive_outreach.py tests/test_proactive_delivery_concurrency.py \
  tests/test_outreach_contract_regressions.py \
  tests/test_proactive_outbound_delivery.py -v
```

- [x] **步骤 4：实现通用发布接入**

保留现有 evaluation lease、CAS 和 idempotency key；`deliver_outreach_once()` 变成“领取运行、
生成、入队”的 producer。worker 结算时投影旧 `sent/failed/ambiguous` 状态供兼容查询。

- [x] **步骤 5：运行定向绿灯和模拟脚本**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_proactive_outreach.py tests/test_proactive_delivery_concurrency.py \
  tests/test_outreach_contract_regressions.py \
  tests/test_proactive_outbound_delivery.py tests/test_outbound_delivery_worker.py -v
python scripts/run_proactive_outreach_simulation.py --help
```

**完成证据（2026-07-15）：**

- 主动外呼 producer 只冻结正文并持久入队；outbox 模式不执行 HTTP，legacy 模式由独立恢复
  drain 接管，结构化保留 401/429/503、退避、deadline、request boundary 和 ambiguous 事实。
- history clear 使用逐 linkage savepoint；blocked/no-outbox 可在 circuit 恢复后复用原 run，且
  `legacy_direct → outbox_hold`、`outbox_active → outbox_draining` 都会阻止遗失可恢复 run。
- 上海本地 naive 业务时间在进入 Outbound gate/run 前转换为 UTC naive；业务 `created_at` 与
  writer/claim/delivery 的真实 UTC 运行时钟分离。禁用模型生成时恢复线程仍启动，短 drain poll
  与长模型 fallback 心跳互不影响。
- legacy drain 会先终结超过 deadline 的 leaf；逐 leaf savepoint 隔离 fencing/CAS，来源 revision
  变化时保留新来源，但旧 run/outbox 仍收敛为 `failed/retry_exhausted`，不会再次发送 HTTP。
- Admin run-once 在动作前持久化请求审计，成功审计前回滚运行时遗留脏状态；已提交 outbox 事实
  保留，未提交 ORM 对象不会被审计 commit 顺带持久化。管理查询会验证来源 revision linkage。
- Task 13 扩大定向矩阵：`465 passed, 3900 warnings, 0 failed`。首次全量发现 4 个旧测试缺少
  必需 control 夹具，补齐后相关模块 `8 passed`；最终全量：
  `4550 passed, 6 skipped, 5130 warnings, 0 failed`，耗时 797.68 秒。
- 两份独立只读复审最终均为 `0 Critical / 0 Important / GO`；项目代码范围 Ruff、compileall、
  `git diff --check` 均通过。全仓 Ruff 只在用户侧 `.codex/skills/ui-ux-pro-max` 发现 14 个既有
  lint 问题，本任务未修改该目录。
- 未执行 `git add` 或 `git commit`；步骤 6 继续等待用户明确授权。

- [ ] **步骤 6：提交检查点（等待授权）**

提交信息：`fix(主动外呼): 使用持久出站队列发布`。

## 任务 14：提供脱敏管理查询、熔断恢复与人工重放

**文件：**
- 创建：`api/admin/outbound_delivery_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`core/outbound_delivery.py`
- 创建：`tests/test_admin_outbound_delivery.py`

- [x] **步骤 1：写权限、脱敏和状态转换红灯**

列表只返回内部 ID、来源、状态、时间、错误类别、目标指纹和 payload hash 前缀；不返回 target、
payload、响应正文。delivered 禁止 replay；ambiguous 缺少 `confirm_duplicate_risk=true` 被拒绝；
circuit 未关闭时 replay 被拒绝；replay 成功展示 `succeeded_after_ambiguous_replay`，原记录不改。
cutover API 只允许合法 CAS，hold/drain 条件不满足时拒绝进入 legacy；legacy ambiguous hold 提供
显式 resolve。所有动作写操作者和原因。

- [x] **步骤 2：运行红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest tests/test_admin_outbound_delivery.py -v
```

- [x] **步骤 3：实现只读查询和显式动作端点**

端点仅调用状态机，不直接更新 ORM；manual request key 是唯一幂等输入。circuit reset、cancel、
legacy resolve、delivery control transition 和 replay 都要求非空原因，并复用现有 Admin 鉴权与
审计。

- [x] **步骤 4：运行定向绿灯和 Admin 回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_admin_outbound_delivery.py tests/test_admin_api.py \
  tests/test_admin_proactive_outreach.py -v
```

- [ ] **步骤 5：提交检查点（等待授权）**

提交信息：`feat(出站管理): 增加熔断恢复与人工重放`。

### 任务 14A：Admin transition 后原子释放临时 writer

**文件：**
- 修改：`core/outbound_delivery.py`（writer result 类型与 acquire 相邻状态机）
- 修改：`api/admin/outbound_delivery_routes.py`（control transition 事务）
- 修改：`tests/test_outbound_delivery.py`（release CAS 单元测试）
- 修改：`tests/test_admin_outbound_delivery.py`（真实 producer 接管集成测试）

- [x] **步骤 1：编写 Admin transition 后真实 producer 立即接管的失败测试**

扩展 `test_control_transition_uses_server_writer_identity_and_reason`：Admin 完成
`legacy_direct -> outbox_hold` 后，在同一 boundary 之前用不同 owner/token 调用
`claim_outbound_run()`，断言 `acquired is True`。调用使用独立 source/occurrence、固定
`NOW + 1 second`、当前协议和测试 endpoint revision；随后断言 control 的 writer 身份属于真实
producer，而 Admin 响应和审计仍不含 owner/token。

- [x] **步骤 2：运行集成红灯**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY HOME=/tmp/nanobot-test-home \
  LOG_DIR=/tmp/nanobot-test-logs python -m pytest \
  tests/test_admin_outbound_delivery.py::test_control_transition_uses_server_writer_identity_and_reason -v
```

预期：Admin transition 为 200，但 `claim_outbound_run()` 因其他 writer 仍持有 lease 抛
`OutboundSafetyError`；证明临时 Admin lease 未释放。

- [x] **步骤 3：编写核心 release 的 stale CAS 与成功释放红灯**

在 `tests/test_outbound_delivery.py` 使用 `_seed_control()` 和
`acquire_or_renew_delivery_writer()`：

```python
lease = acquire_or_renew_delivery_writer(..., now=NOW)
with pytest.raises(OutboundFencingError):
    outbound_delivery_state.release_delivery_writer(
        db_session,
        source_type=SOURCE_TYPE,
        owner="producer-a",
        token="writer-a",
        protocol_version=2,
        expected_writer_version=lease.writer_version - 1,
        now=NOW,
    )
released = outbound_delivery_state.release_delivery_writer(
    db_session,
    source_type=SOURCE_TYPE,
    owner="producer-a",
    token="writer-a",
    protocol_version=2,
    expected_writer_version=lease.writer_version,
    now=NOW,
)
assert released.writer_version == lease.writer_version + 1
```

stale 调用后 lease 三字段和版本保持不变；成功后 owner/token/lease 全为 `None`。

- [x] **步骤 4：运行核心红灯**

运行新核心测试，预期因 `release_delivery_writer` 尚不存在而失败；不能是 fixture 或 SQL 约束错误。

- [x] **步骤 5：实现最小 release 状态机并接入 Admin 事务**

在 `core/outbound_delivery.py` 新增只含 `applied/source_type/writer_version` 的冻结 result 类型。
`release_delivery_writer()` 规范化输入，调用 `_locked_current()`，以 `_require_writer()` 校验有效
身份，再验证精确 expected version；UPDATE 谓词同时包含 source、owner、token、protocol、version
和 `lease_expires_at > current`，成功更新为：

```python
{
    OutboundDeliveryControl.writer_version: expected_writer_version + 1,
    OutboundDeliveryControl.writer_owner: None,
    OutboundDeliveryControl.writer_token: None,
    OutboundDeliveryControl.writer_lease_expires_at: None,
    OutboundDeliveryControl.updated_at: current,
}
```

影响行数不是 1 时抛 `OutboundFencingError`；只 flush/expire，不 commit/rollback。Admin route 在
transition 成功后、审计前调用 release，以 transition result version 为 expected version；审计和
响应使用 release result 的最终 version。所有动作仍由原 route 单次 commit，release 失败进入现有
固定错误与 rollback 分支。

- [x] **步骤 6：重跑 A 的核心与 Admin 测试确认绿灯**

预期核心 stale CAS/成功释放测试通过，Admin transition 后真实 producer 可立即 claim；最终
writer version 单调递增，响应/审计不含身份或 token。

### 任务 14B：提供 legacy resolve 的脱敏发现列表

**文件：**
- 修改：`api/admin/outbound_delivery_routes.py`（严格 serializer 与 GET list）
- 修改：`tests/test_admin_outbound_delivery.py`（GET→POST workflow）

- [x] **步骤 1：把 resolve 集成测试改成先查询安全 CAS token**

删除测试对 `proactive_outreach_source_revision(row)` 的直接调用。seed 后请求：

```python
listed = admin_outbound.client.get(
    "/api/v1/admin/outbound-delivery/legacy-proactive",
    headers=_auth(),
)
assert listed.status_code == 200
assert set(listed.json()) == {"total", "items", "page", "limit"}
item = listed.json()["items"][0]
assert set(item) == {
    "id", "source_type", "status", "created_at", "source_revision",
}
```

断言只列 `legacy_ambiguous_hold`、默认顺序为 `created_at DESC, id DESC`、分页范围生效并通过
`_assert_no_secret()`；POST resolve 的 expected created/revision 只取自 item。

- [x] **步骤 2：运行 B 红灯**

运行 GET→POST workflow 测试，预期当前 GET 返回 404；POST 尚未执行，不接受从 ORM 偷取 revision。

- [x] **步骤 3：实现严格 serializer 与分页 GET**

新增 serializer，唯一返回：

```python
{
    "id": int(row.id),
    "source_type": "proactive_outreach",
    "status": str(row.status),
    "created_at": _datetime(row.created_at),
    "source_revision": (
        outbound_delivery.proactive_outreach_source_revision(row)
    ),
}
```

GET 固定过滤 `status == "legacy_ambiguous_hold"`，按 `created_at.desc(), id.desc()` 排序，使用
`page=Query(1, ge=1)`、`limit=Query(50, ge=1, le=200)`；顶层仅返回 total/items/page/limit。
不改 POST resolve 或核心 created_at+revision CAS。

- [x] **步骤 4：重跑 B workflow 确认绿灯**

预期 GET 返回严格白名单，敏感 sentinel 均不出现，GET item 可直接完成 POST resolve。

### 任务 14C：固定 Admin 动作时钟并关闭测试客户端

**文件：**
- 修改：`api/admin/outbound_delivery_routes.py`（统一 `_utc_now()` 与显式 now）
- 修改：`tests/test_admin_outbound_delivery.py`（聚焦时钟测试与 fixture 资源关闭）

- [x] **步骤 1：编写 replay 显式时钟红灯**

测试保留 `retry_deadline_at = NOW + 1 day`，保存真实核心 `_utc_naive` 后 monkeypatch：

```python
def shifted_utc_naive(value=None):
    if value is None:
        return NOW + timedelta(days=2)
    return real_utc_naive(value)

monkeypatch.setattr(outbound_delivery_state, "_utc_naive", shifted_utc_naive)
monkeypatch.setattr(outbound_delivery_routes, "_utc_now", lambda: NOW, raising=False)
```

调用 replay 并断言 200。当前 route 未传 `now`，核心收到 `None` 后会认为 deadline 已过期。

- [x] **步骤 2：运行 C 红灯**

运行聚焦 replay 测试，预期得到 422 而非 200，失败原因必须是 route 未显式传时钟。

- [x] **步骤 3：实现 route 时钟并固定 fixture**

新增：

```python
def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

replay 调用 `create_delivery_replay(..., now=_utc_now())`；transition 将现有内联
`datetime.now(...)` 改成 `_utc_now()`。fixture 使用 monkeypatch 将 route `_utc_now` 固定为
`NOW`，所有 transition boundary 都从 `NOW` 推导，不把 deadline 改成真实执行日。

fixture 先创建 `client = TestClient(app)`，在 `finally` 中先 `client.close()`，再清除 dependency
override、drop 临时 schema 和 dispose engine；避免用 context manager 触发额外 production lifespan。

- [x] **步骤 4：重跑 C 与完整 Admin 出站测试确认绿灯**

预期聚焦测试 200，固定日期 replay/transition 全部稳定，客户端资源无泄漏。

### 任务 14D：第二阶段验证与自审

- [x] **步骤 1：运行核心和 Admin 回归**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY HOME=/tmp/nanobot-test-home \
  LOG_DIR=/tmp/nanobot-test-logs python -m pytest \
  tests/test_admin_outbound_delivery.py tests/test_outbound_delivery.py \
  tests/test_admin_api.py tests/test_admin_proactive_outreach.py -v
```

预期：0 failures。

- [x] **步骤 2：运行目标静态与语法检查**

```bash
python -m ruff check \
  core/outbound_delivery.py api/admin/outbound_delivery_routes.py \
  tests/test_outbound_delivery.py tests/test_admin_outbound_delivery.py
python -m compileall -q core/outbound_delivery.py api/admin/outbound_delivery_routes.py
git diff --check
```

预期全部退出码 0。目标文件若仍为未跟踪，普通 Git diff 无基线，需要在报告中明确限制。

- [x] **步骤 3：执行中文代码自审和完成前验证**

重点复核 release 的 source lock/CAS/事务回滚、GET allowlist、固定错误响应、时钟注入以及未改
Prompt/QQ 协议。Critical/Important 必须修复并重新执行步骤 1–2。

**任务 14 完成证据（2026-07-15）：**

- 初始管理合同覆盖鉴权、严格脱敏查询、真实状态动作和原子审计；幂等 replay 复用已投递 child
  的新增用例先以 `status=pending` 红灯失败，修复后返回真实 `delivered` 与
  `succeeded_after_ambiguous_replay`，且不创建第三条 outbox。
- 规格复审复现 `outbox_draining -> legacy_direct` 会漏过跨 epoch legacy run；新增
  `claimed/generating/queued/delivering/blocked/ambiguous` 六状态参数化测试先为 `6 failed`，
  门禁复用跨 epoch legacy 检查后为 `6 passed`。
- 第二阶段质量审查依次复现三个阻塞红灯：Admin transition 后真实 producer 无法立即 acquire
  `1 failed`；核心 writer release 合同缺失 `2 failed`；legacy GET 返回 404 `1 failed`；route 未
  显式传时钟导致 replay 422 `1 failed`。所有红灯均按预期原因失败后再做最小实现。
- `release_delivery_writer()` 使用 source lock 和 owner/token/protocol/version/有效 lease CAS，
  清空临时 writer 三字段并递增版本；Admin 事务固定为
  `acquire -> transition -> release -> audit -> commit`。legacy hold 新增鉴权五字段分页 GET，
  客户端可直接用返回的 `created_at/source_revision` 完成原 POST CAS。replay 与 transition 共用
  可替换 UTC 时钟，测试客户端显式关闭。
- 主线程新鲜运行 Task 14 四文件矩阵：`263 passed in 22.40s`，退出码 0；其中 Admin 出站
  `14 passed`、核心状态机 `152 passed`、旧 Admin 与主动外呼管理 `97 passed`。
- 目标 Ruff 为 `All checks passed`，两个生产模块 `compileall` 退出码 0，`git diff --check`
  退出码 0。
- 修复后的独立规格复审为 `0 Critical / 0 Important / GO`；独立中文代码质量复审为
  `0 必须修复 / 0 阻塞建议 / GO`。非阻塞建议是后续增加 transition 专属 release/审计失败
  故障注入测试；静态事务路径与现有 cancel 审计失败测试已证明当前实现无分叉。
- 未执行 `git add` 或 `git commit`；提交检查点继续等待用户明确授权。

- [ ] **步骤 4：提交检查点（等待用户明确授权）**

本轮禁止 `git add` 或 `git commit`；这里只记录未来检查点，不构成提交授权。

## 任务 15：跨链路验收与独立中文代码审查

**文件：**
- 修改：本计划文档，仅更新真实完成状态和验证证据。

- [x] **步骤 1：运行核心跨链路矩阵**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest \
  tests/test_outbound_delivery_schema.py tests/test_outbound_delivery.py \
  tests/test_outbound_transport.py tests/test_outbound_delivery_worker.py \
  tests/test_scheduled_task_outbound.py tests/test_proactive_outbound_delivery.py \
  tests/test_prompt_v2_core_contract.py tests/test_prompt_v2_task_contracts.py \
  tests/test_prompt_v2_template_migration.py tests/test_admin_outbound_delivery.py -v
```

- [x] **步骤 2：运行完整测试**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 3：运行静态和语法验证**

```bash
python -m ruff check core clients api bootstrap workers tests scripts
python -m compileall -q core clients api bootstrap workers scripts tests
git diff --check
docker compose config --quiet
```

- [x] **步骤 4：运行边界与敏感信息检查**

```bash
git diff --cached --name-only
git ls-files nanobot.db cc2codex
git check-ignore -v cc2codex
git diff --name-only -- creatures/nanobot/prompts data/prompts_v2 prompts.v2.default
rg -n "NANOBOT_SUPER_USER_IDS=.+|SUPER_USER_IDS=.+|ADMIN_USER_ID=.+" \
  --glob '!nanobot.db' --glob '!cc2codex/**' .
```

验收：暂存区为空；`nanobot.db` 和 `cc2codex` 未跟踪；除获批 flow JSON 外 Prompt Markdown 无
差异；无具体超级用户账号或敏感配置值。

- [x] **步骤 5：执行中文代码审查**

按 `chinese-code-review` 检查安全、并发、迁移、兼容性和测试缺口；Critical/Important 必须修复
并重新执行步骤 1-4。独立复审必须确认：同 occurrence 不因任务编辑重复生成、网络重试不重新
生成、circuit scope 不误封/漏封、切换回滚无双写、主动外呼候选与 run 原子关联、历史字段不
伪造成功、Prompt 路径真实、baseline blob 完整、敏感响应不回显原值。

- [x] **步骤 6：更新文档证据**

只勾选实际通过的步骤，记录完整 pytest 数量、静态检查结果、Compose 验证和仓库边界结果。

### Task 15 验收证据（2026-07-16）

- 最后修复涉及的 5 个主动外呼测试文件为 `249 passed`；主动外呼关联矩阵为
  `419 passed`；核心跨链路矩阵为 `1171 passed`。
- 主线程在最终提交前重新运行全量测试，共收集 4642 项，结果为
  `4636 passed, 6 skipped, 5131 warnings in 707.16s`，退出码 0。警告来自既有
  Starlette 与 Python 3.12 SQLite datetime 弃用提示，没有测试失败。
- `python -m ruff check core clients api bootstrap workers tests scripts` 返回
  `All checks passed!`；`compileall`、`git diff --check` 与
  `docker compose config --quiet` 均以退出码 0 完成。
- 仓库边界检查确认暂存区为空；`nanobot.db` 与 `cc2codex` 均未被 Git 跟踪，
  `cc2codex/` 由 `.gitignore` 明确忽略，验证过程没有读取数据库内容。Prompt Markdown
  精确路径检查无差异，两份 `flow.json` 内容一致；路径级差异中的
  `creatures/nanobot/prompts/skills/schedule_task/tool.py` 是调度工具执行器，不是 Prompt
  模板正文。敏感标识扫描只命中 README 占位符和对应测试断言，没有具体账号或配置值。
- 独立规格复审结论为 `0 Critical / 0 Important / GO`；独立中文质量复审与主线程最终
  自审结论均为 `0 Critical / 0 Important / 1 Minor / GO`。已确认 occurrence 幂等、
  网络重试复用持久正文、circuit scope、cutover CAS 与回滚、候选与 run/outbox 原子关联、
  历史清除边界、Prompt 来源与 baseline blob、敏感响应遮蔽等合同均有实现和回归测试覆盖。
- 唯一非阻塞 Minor：`mark_clear` 使用本地 naive 时间，而统一出站取消函数把 naive 时间按
  UTC 解释，可能使取消记录的 `completed_at`、`updated_at`、`cancelled_at` 比真实 UTC
  晚约 8 小时。该问题不影响历史清除安全、worker preflight、request boundary、CAS 或
  投递终态；后续应统一旧表本地时间与出站账本 UTC 时间契约，本轮不扩展状态机时间迁移范围。
- 用户于 2026-07-16 明确授权完成后提交；本轮将按明确文件清单暂存，并随本提交完成最终
  检查点，不包含数据库、研究资料、其他计划或界面快照。

- [x] **步骤 7：最终提交检查点（已获用户明确授权）**

逐文件核对并按任务分组 `git add <明确文件列表>`，禁止批量暂存；使用中文 Conventional Commit。

## 计划自检

- 21 条审查意见分别映射到任务 1-14，没有留给未定义的“后续处理”。
- occurrence、六类持久记录、HTTP 分类、cutover、legacy 字段和 Prompt drift 枚举与设计文档
  一致。
- 所有生产改动先有失败测试；每个测试命令包含明确路径和预期失败原因。
- 计划不要求修改 QQbot、CQ renderer、Prompt Markdown 正文或工作区数据库。
- 所有提交步骤均为未勾选检查点，不构成本轮提交授权。
