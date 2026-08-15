# Nanobot Server — 项目约定

## 语言

所有输出、注释和 commit message 使用中文。代码标识符使用英文。

## Agent 工作方式

- **结果优先**：需求明确时直接执行最小充分方案，不先生成设计文档、长计划或流程说明。
- **严格守界**：只修改用户放入范围的文件；发现相邻问题先报告，不擅自扩大任务。
- **按需加载技能**：仅当用户点名技能，或任务与某个窄领域技能直接匹配时才加载。
- **少问关键问题**：只有缺失信息会实质改变结果或产生风险时才澄清，其余情况采用最保守的合理假设继续。
- **轻量规划**：简单任务不进入 plan/goal 模式；复杂任务可维护简短步骤，但除非用户要求，不创建计划文件。
- **谨慎使用子 Agent**：仅在多个独立子问题可明显并行时使用；简单改动、单点调查和同文件编辑由主 Agent 完成。
- **验证后再结论**：修改后运行与改动直接相关的最小验证；声称完成、修复或测试通过时必须给出实际证据。
- **提交需授权**：用户明确说“提交”之前不得 commit。
- **外部信息要查证**：API 和模型行为优先使用官方文档；社区经验只能作为待验证信号。

## 提交规范

提交时使用 `chinese-commit-conventions`，并遵守以下格式：

- type：`feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`
- scope：中文模块名
- subject：中文动宾短语，不超过 50 个字符
- 禁止 `git add -A` 和 `git add .`，必须按文件指定

## 测试

- 测试文件命名为 `tests/test_<module>.py`。
- 数据库测试优先使用 pytest 和 in-memory SQLite。
- 修改后先运行直接相关的最小测试。
- 提交前必须运行 `python -m pytest tests/ -v`，且结果为 0 failures。

## 当前架构

| 路径 | 职责 |
|------|------|
| `bootstrap/lifespan.py` | Composition Root；构建启动期 Agent 注册集和 Runtime 选择策略 |
| `core/agent_runtime/` | 框架无关的 Agent 注册表、Gateway Port 和 Native／KT 选择策略 |
| `nanobot_kt/agent_catalog.py` | 从受信 `creatures/` 目录加载并校验 `agent.yaml` |
| `nanobot_kt/multi_agent_runtime.py` | 为每个已注册 Agent 持有独立 `NanobotBridgePool` |
| `nanobot_kt/bridge.py` | Native／KT Agent Loop 的请求适配、会话级 Bridge 和工具执行 |
| `creatures/<agent_id>/` | Agent 身份、KT 配置、工具权限和 creature 本地文件 |
| `app/session_config/runtime.py` | 查询会话的 `database_only` 和 `agent_id` 绑定 |
| `core/agent_link/` | 外部 Agent Link 协议和 `target_agent_id` 路由 |
| `core/agent_collaboration/` | 冻结计划、任务板、认领、交付和人工复核 |
| `core/sandbox/` | 工作区、资产、权限、配额和 Sandbox 服务端策略 |
| `vendor/KohakuTerrarium/` | KT 升级对照与测试源码；默认 Runtime 镜像不复制该目录 |
| `prompts.v2.default/` | canonical Prompt Runtime 默认模板 |
| `data/prompts_v2/` | 当前部署使用的 Prompt Runtime 模板 |
| `webui/` | 管理端前端；会话策略位于 `/configs` |

### Agent、Creature 与 Runtime 的关系

- 一个 Agent 对应一个显式注册的 `creatures/<agent_id>/`，Nanobot 和 PAbot 使用完全相同的注册、Pool、Prompt、工具和模型路由结构。
- `AgentRuntimeRegistry` 只把稳定的 `agent_id` 映射到框架无关 Gateway，不在核心层实现 Agent Loop。
- 每个 Agent 拥有独立 `NanobotBridgePool`；同一 Agent 内再按 canonical session 隔离 Bridge。
- Native 与 KT 是 Agent Loop 的两种实现，不是两个 Agent。所有已注册 Agent 共享同一份启动期冻结的 `AgentRuntimeSelectionPolicy`。
- 仓库默认使用 Native，KT 默认关闭。注册 PAbot 或其他 creature 不等于启用 KT；同一个 Agent 可按冻结策略选择 Native 或 KT。
- 请求开始后只选择一次 Runtime；失败时禁止跨 Native／KT 重放本轮，以免重复工具副作用。
- HTTP 对话不依赖 KT conversation 持久化。每轮 conversation 都重新构建，历史从 `ConversationTurn` 注入。

## 多 Agent 配置

### 仓库默认注册集

| Agent | 目录 | 默认 Agent | 当前用途 |
|-------|------|------------|----------|
| Nanobot | `creatures/nanobot/` | 是 | 通用中文对话、记忆、任务和工具调用 |
| PAbot | `creatures/pabot/` | 否 | 专业研究、数据分析和受控操作 |

`nanobot` 始终由启动代码加入注册集；附加 Agent 由 `NANOBOT_AGENT_RUNTIME_ADDITIONAL_IDS` 显式列出。当前默认值为 `pabot`。系统不会扫描 `creatures/` 后自动注册新目录。

### Creature 目录合同

每个 Agent 至少包含以下文件：

```text
creatures/<agent_id>/
├── agent.yaml     # 注册清单和权限边界
├── config.yaml    # KT creature 配置
├── profile.md     # 注入 canonical Prompt 的受信身份说明
└── memory/
    └── MEMORY.md  # 可选的 creature 本地说明；不是业务会话存储
```

项目约定目录名、`agent.yaml.agent_id` 和 `config.yaml.name` 保持一致，其中 Loader 会强制校验前两者。`agent_id` 必须以小写字母开头，只能使用小写字母、数字以及分隔符 `.`、`_`、`-`，最长 64 个字符。注册文件必须是 creature 目录内的普通文件，禁止符号链接和越界路径。

新增 Agent 可从以下最小清单开始：

```yaml
schema_version: 1
agent_id: codebot
display_name: CodeBot
description: 面向代码研究与受控执行的 Agent。
profile_file: profile.md
config_file: config.yaml
allowed_tools:
  - knowledge_query
  - web_search
  - sandbox_exec
  - sandbox_poll
  - sandbox_write_stdin
  - sandbox_terminate
  - workspace_read
  - workspace_search
  - workspace_write
  - workspace_edit
  - asset_import
  - asset_publish
  - reply
  - no_reply
allow_dynamic_tools: false
allowed_entrypoints:
  - chat
  - agent_link
default: false
model_profile_id: ""
```

字段约束如下：

| 字段 | 说明 |
|------|------|
| `allowed_tools` | 使用 `"*"` 或非空白名单；生产新增 Agent 优先使用最小白名单 |
| `allow_dynamic_tools` | 是否接受受管入口临时注入的动态工具；与 `allowed_tools` 分开授权 |
| `allowed_entrypoints` | Registry 能力门禁；只声明实际需要的 `chat`、`agent_link`、`research`、`scheduled` 或 `a2a` |
| `default` | 整个注册集必须且只能有 1 个默认 Agent；附加 Agent 保持 `false` |
| `model_profile_id` | 可选的已验证模型 Profile；留空时使用共享 reply Route 和会话 Gateway 绑定 |
| `manifest_snapshot_sha256` | 可选的高级完整性约束；普通内置 Agent 可省略 |

`profile.md` 同时作用于 Native 和 KT Runtime，并进入 canonical `identity_context`。禁止在其中放 Token、密码、宿主路径或其他密钥。`config.yaml` 是 KT Adapter 的构造配置；即使默认使用 Native，也必须存在，以便 KT 灰度和目录完整性校验。

### 注册新 Agent

1. 新建 `creatures/<agent_id>/`，写入 `agent.yaml`、`profile.md` 和 `config.yaml`。
2. 让 `config.yaml` 声明的 KT 工具与 `agent.yaml.allowed_tools` 保持一致；动态工具不写入静态清单。
3. 在部署环境中加入附加 ID，例如：

   ```dotenv
   NANOBOT_AGENT_RUNTIME_ADDITIONAL_IDS=pabot,codebot
   ```

4. 重新构建 Runtime 镜像。生产容器从镜像读取 creature 文件，不能通过修改宿主仓库或容器可写层完成发布。
5. 重启 Nanobot Server，使启动期注册表重新构建。注册集、`agent.yaml`、`profile.md`、`config.yaml` 和 Agent 自有代码都不支持代码级热重载。
6. 通过管理接口 `GET /api/v1/admin/agent-runtimes` 确认 `agent_id`、入口、默认项和 Registry SHA-256。
7. 运行 `python -m pytest tests/test_multi_agent_runtime.py -v` 和与新增工具直接相关的测试；提交前再运行全量测试。

启动阶段会 fail closed：目录不存在、未知字段、重复 ID、多个默认 Agent、非法工具策略或非法入口标识都会阻止 Runtime 注册，不得静默回退到 Nanobot。

### Agent 自有代码

- 身份差异优先放在 `profile.md`，能力差异优先通过工具白名单表达，不复制整套 Agent Loop。
- 业务实现必须保持框架无关；共享实现放在 `app/` 或 `core/`，KT 适配类放在 `nanobot_kt/tools/`。
- 仅属于某个 Agent 的领域代码可以放在对应 creature 包中，但不得直接依赖 `kohakuterrarium` 或 `nanobot_kt` 私有实现；由 Adapter 连接到 Agent Loop。
- `config.yaml` 只能引用已进入生产镜像的可导入模块。新增工具时必须补充权限、schema、Prompt 使用契约和测试。
- 不得为新 Agent 复制数据库、模型客户端、RAG、资产库或 Sandbox 控制面；这些都是共享服务，通过 Runtime Context 和 ACL 使用。

## 会话选择与热切换

管理端路径为“会话策略”(`/configs`)。编辑会话后，在“对话 Agent”中选择已注册 Agent 并保存；该绑定从下一次进入 Agent Loop 的请求开始生效，不需要重启。

也可以通过管理 API 更新已有 canonical 会话；以下示例同时选择 PAbot 并关闭仅入库模式：

```bash
curl -X PUT \
  http://127.0.0.1:8000/api/v1/admin/configs/qq:123456:group \
  -H "Authorization: Bearer ${NANOBOT_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"pabot","database_only":false}'
```

需要同时注意：

- `database_only` 默认开启，即“仅入库（不调用任何模型）”。此时会保存 `agent_id`，但不会调用任何 Agent。需要实测回复时必须显式关闭“仅入库”。
- 网页只能切换已注册且开放 `chat` 入口的 Agent；服务端保存时会再次校验 Registry。
- 删除整条会话覆写后，`agent_id` 恢复为 `nanobot`，`database_only` 恢复为系统默认开启。
- 会话绑定是配置热切换，不是代码热重载。修改注册集、manifest、profile、KT 配置或代码仍需重新构建并重启。
- 同一会话切换 Agent 后继续使用该会话原有的 `ConversationTurn`、RAG、画像和 owner 资产；当前没有按 Agent 自动分区历史。

以下 4 类配置不能混淆：

| 配置 | 控制对象 | 生效方式 |
|------|----------|----------|
| `NANOBOT_AGENT_RUNTIME_ADDITIONAL_IDS` | 启动时注册哪些附加 Agent | 重启 |
| `chat_stream_configs.agent_id` | 某个会话把新请求交给哪个 Agent | 保存后下一请求生效 |
| `NANOBOT_AGENT_RUNTIME_DEFAULT`、`NANOBOT_AGENT_RUNTIME_KT_*` | 每个 Agent Pool 选择 Native 还是 KT Loop | 重启 |
| `NANOBOT_MULTI_AGENT_ENABLED` | 是否开放冻结计划驱动的协作任务板 | 重启；默认关闭 |

## A2A 与多 Agent 协作

当前有两条不同的通信边界：

1. **Agent Link 定向对话**：外部 Agent Link 客户端连接 `/agent-link` 后，可在 `chat.submit.target_agent_id` 指定已注册 Agent。目标必须开放 `agent_link` 入口，服务端按 Registry 路由到对应 Pool。
2. **持久协作任务板**：`core/agent_collaboration/` 提供冻结计划、显式邀请、任务认领、租约、交付、批准／驳回和 append-only 事件。管理 API 与 Agent Link 的 `collaboration.status`、`collaboration.claim`、`collaboration.deliver` 共用这一事实源。

`allowed_entrypoints: [a2a]` 目前只是 Registry 能力声明，不会让一个本地 Agent 自动调用另一个 Agent。需要自动编排时，必须通过框架无关 Registry Port 和持久协作服务实现，禁止直接引用另一个 Agent 的 BridgePool、共享 conversation 或绕过邀请、租约与审批。

协作功能默认关闭。如需启用，在完成调用方和权限验证后设置：

```dotenv
NANOBOT_MULTI_AGENT_ENABLED=1
```

启用后仍须遵守 owner/project ACL。Agent 之间共享大结果时传递不可变 `asset_id`、摘要和校验信息，不传宿主路径，也不把全局资产库作为共享可写目录。

## 共享资源与隔离边界

| 共享资源 | 隔离或约束方式 |
|----------|----------------|
| 模型 Provider、reply Route 和候选健康状态 | Agent 可用 `model_profile_id` 选择已验证 Profile，不能携带私有 API Key |
| Prompt Runtime | 共享模板和编译器；通过 `agent_id`、`agent_profile`、会话指导区分身份 |
| 工具实现与 schema Registry | `allowed_tools`、`allow_dynamic_tools` 和入口能力共同收窄 |
| 对话历史、画像、RAG 和长期记忆 | 按 canonical session、user、group、project 查询，不因 Agent 切换扩大范围 |
| Workspace 与 Asset | 按 owner/project ACL、授权和配额隔离；Agent 不是越权主体 |
| Run Ledger、Trace 和任务执行记录 | 必须记录实际 `agent_id`、Runtime、工具与终态，禁止伪造成功 |
| Agent Loop 状态 | 每个 Agent 独立 Pool，每个会话独立 Bridge，请求间不共享 conversation |

`creatures/<agent_id>/memory/` 只保存 creature 本地 KT 文件，不是业务会话、共享资产或长期知识的事实源。

## 数据模型

| 表 | 用途 |
|----|------|
| `users` | 用户 ID 和 `history_clear_at` 清除标记 |
| `chat_logs` | 原始消息档案，包含 tool 和 ambient，供进化与画像分析使用 |
| `conversation_turns` | 仅含 user/assistant 的精简对话上下文，专用于历史注入 |
| `chat_stream_configs` | 会话策略，包括默认开启的 `database_only` 和 `agent_id` |
| `personas` | 用户画像 JSON |
| `scheduled_tasks`、`scheduled_task_executions` | 定时任务定义和持久执行状态 |
| `agent_collaboration_boards`、`agent_collaboration_events` | 多 Agent 协作任务板和 append-only handoff 事件 |
| `workspaces`、`assets`、`workspace_assets` | 按 owner 隔离的工作区和不可变资产引用 |
| `run_ledger_events` | Agent、模型、工具、权限和终态的权威运行证据 |

分离原则：`ChatLog` 是档案馆，保留全部消息；`ConversationTurn` 是可清理的工作内存。历史清除只删除 `ConversationTurn` 并更新 `history_clear_at`，不得删除 `ChatLog`。

## 关键运行约束

- **会话仅入库默认值**：新会话和无覆写会话默认不调用模型；只有显式关闭 `database_only` 后才进入 Agent Loop。
- **模型路由**：只使用已验证 reply Route 中满足能力、预算和健康条件的候选；`model_profile_id` 不得绕过候选校验。
- **熔断器**：模型连续失败达到阈值后使用指数退避冷却；当前默认从 300 秒开始，最高 1800 秒，成功后清除失败状态。
- **Token 估算**：统一使用 `core.token_utils.estimate_tokens()`；当前 CJK 按 1.0、ASCII 按 0.35、其他 Unicode 按 0.8 粗估。
- **中文优先**：面向用户的默认 Prompt 和回复使用中文。
- **提示词同步**：修改 `enriched_query`、历史注入、conversation 结构、工具输出契约、Agent 身份输入或 Prompt Runtime 变量时，必须检查 `prompts.v2.default/`、必要的 `data/prompts_v2/`、`core/prompt_v2/variables.py` 和 `core/prompt_v2/template_registry.py`，同一变更内修正过时模板。

## Docker 与 Sandbox

### 技术边界

- KT／Nanobot 负责 Agent Loop、会话、工具和 Prompt；独立 `sandboxd` 负责受控调用 Docker Engine。
- 只有 `sandboxd` 可以访问 `/var/run/docker.sock`。Nanobot Server 和 Sandbox 容器禁止挂载 Docker Socket。
- `python_sandbox` 是硬禁用的遗留兼容工具；数据库分析使用 `sql_analysis`，通用代码执行使用 `sandbox_exec`。
- 模型只能提交工作区内命令和受限参数，不能指定宿主路径、镜像、volume、network mode、capability、device 或 namespace。
- Sandbox 使用专用 `nanobot-sandbox-*` 镜像，禁止复用 `nanobot-runtime`。

### 网络与权限

- `restricted` Profile 使用 `network=none`。
- `developer` Profile 只允许策略文件中的域名和端口，并拒绝内网、回环、链路本地和保留地址；还必须同时满足基础设施上限、会话执行上限、developer 网络上限和当前 owner 的授权。
- `trusted_developer` 当前不可授予，不得通过改请求参数绕过。
- 所有执行容器必须使用非 root、只读根文件系统、`cap-drop=ALL`、`no-new-privileges`、受控 seccomp/AppArmor、PID/CPU/内存/磁盘/输出/超时上限。
- 超时必须终止整个进程树；禁止 privileged、额外 capability、宿主设备、宿主凭据目录和任意 bind mount。

### 工作区与资产

- 长期数据位于 `/srv/nanobot/workspaces/`、`/srv/nanobot/assets/` 和 `/srv/nanobot/runtime/`，不得放入容器可写层、仓库 `./workspace` 或 `/app/workspace`。
- 容器停止、删除或重建不得删除 Workspace；删除 Workspace 或 Asset 必须是独立、显式且通过权限校验的操作。
- Workspace 可读写挂载到 `/workspace`，Runtime 缓存挂载到 `/runtime`，已授权输入资产只读挂载到 `/inputs`。
- 全局资产库不得可写挂载给 Sandbox；结果先写 Workspace，再通过 `asset_publish` 发布为不可变资产。
- 新增持久目录时必须同步检查 `.dockerignore`，防止工作区、资产、缓存、密钥和数据库进入镜像。

### 构建、部署与运维

- 本地源码验证可直接构建带准确 Git revision 和 dirty 状态的 `nanobot-runtime` 镜像；生产部署必须显式指定不可变镜像身份。
- Runtime 镜像必须包含运行所需的 `creatures/<agent_id>/agent.yaml`、`profile.md`、`config.yaml` 和代码；`.agents/`、测试、文档、工作区、资产和密钥不得进入生产镜像。
- 第三方大依赖位于稳定层，业务代码位于其后；`vendor/` 默认不进入构建上下文。KT 依赖变更必须更新锁定来源，不得无故重建 PyTorch 等大依赖层。
- 默认只保留当前部署镜像和最近 1 个已验证回滚镜像。新镜像通过健康检查和直接相关测试后才能淘汰旧镜像。
- 部署或启用 Sandbox 前检查 `df -h`、`df -i`、`docker system df` 和数据目录所在文件系统。
- 未经用户明确授权，禁止执行 `docker system prune`、`docker image prune -a`、`docker volume prune`、`docker compose down -v` 等破坏性命令。获批后也必须先列出对象，再按明确名称或 IMAGE ID 定向清理。
- Sandbox 安全必须实测非 root、只读根文件系统、网络策略、无 Docker Socket、资源限制、超时终止、Workspace 跨容器持久化和 owner 间隔离，不能只检查配置文件。

## 服务配置

服务地址以部署环境为准，禁止在代码或文档中固化当前宿主 IP：

| 环境变量 | 用途 |
|----------|------|
| `NEW_API_BASE_URL` | LLM API 网关 |
| `QQBOT_PUSH_URL` | QQ 主动推送入口 |
| `CLASSIFIER_API_URL` | 本地 Qwen 分类器 |
| `NANOBOT_SANDBOXD_SOCKET` | Nanobot Server 到 `sandboxd` 的 Unix Socket |

容器访问宿主服务通常使用 Docker bridge 地址，外部访问使用部署宿主地址，两者不能混写。本地连通性测试前清除代理：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
```

## 禁止行为

- 未运行实际验证就声称功能已完成、API 已恢复、任务已推送或部署健康。
- 用 fallback 结果伪装模型、工具、日报或主动外呼成功。
- 在 Runtime Registry 之外硬编码 `agent_id` 分支，或直接跨 Agent 访问 BridgePool。
- 自动扫描并注册 `creatures/`、运行时静默替换 Registry，或在失败时偷偷回退默认 Agent。
- 把 `NANOBOT_AGENT_RUNTIME_DEFAULT` 误当成默认 Agent 配置；它只控制 Native／KT Runtime。
- 工具业务核心直接依赖 KT 私有成员，或把 Agent 专有逻辑塞回共享 Agent Loop。
- 未经授权提交代码、修改生产凭据、扩大网络权限或执行破坏性 Docker 清理。
