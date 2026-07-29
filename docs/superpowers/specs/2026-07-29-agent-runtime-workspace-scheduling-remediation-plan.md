# Agent Runtime、Workspace、定时任务与发布链路完整整改计划

> - 状态：本地代码实施与回归已完成；生产迁移、构建、部署、真实 Smoke 和凭据切换待授权执行。
> - 形成日期：2026-07-29。
> - 代码基线：Nanobot Server `1d34f3c33aaee780c2b2ee1fc427e3d7a50c9c5d`。
> - KT 基线：`6c2c5f1d059ac7f99379b0cddeea21da8e9b55c0`。
> - 生产核验：2026-07-29 的运行记录、容器、数据库、镜像与配置检查。
> - 关联设计：
>   - `2026-07-14-outbound-prompt-config-remediation-design.md`
>   - `2026-07-23-hardcoded-routing-and-modularization-master-plan.md`
> - 安全边界：本文不记录 Token 正文、宿主凭据正文、完整用户标识或其他敏感值。

## 1. 本计划解决什么

本计划收敛 5 组相互关联的问题：

1. 修复单次 Agent Run 与并发定时任务之间的 KT Session 和请求身份串线。
2. 让结构化工具失败、非重试语义、回复终态和 Trace 记录保持一致。
3. 将安全版 `workspace_*` 工具升级为可供 Code Agent 稳定使用的行读取、搜索和编辑能力。
4. 补齐定时任务的归属隔离、正确执行身份、并发调度、逐步恢复和统一程序结构。
5. 统一配置有效值、Agent Link 凭据、Runtime 发布身份、Sandbox 发布影响与真实 Smoke 的事实源。

最终目标不是增加更多模型工具，也不是重写 KT 或 Sandbox。目标是在现有边界内形成以下闭环：

```text
入口或调度器
  -> 不可变 RequestRuntimeContext
  -> 请求级 ContextVar
  -> Bridge 独占 KT Session
  -> 当前主体的 ToolPlan
  -> Nanobot 授权
  -> sandboxd
  -> 当前 owner Workspace
  -> 结构化结果与一致 Trace
```

定时任务则收敛为：

```text
统一任务定义
  = owner + trigger + program + state policy

trigger 产生 occurrence
  -> 持久执行实例
  -> 有界 worker 执行并逐步 checkpoint
  -> 可选工具调用
  -> 可选模型调用
  -> 不可变 outbox
  -> 独立投递 worker
```

## 2. 已核验事实与更正

### 2.1 Run、Trace 与并发上下文

`run_1a1630ed500c4b029589aca644c61eac` 是 `run_id`，
`a729bfa89c074231b0e25e7edfdf632a` 是同一条运行的 `trace_id`。它们不是两次运行。

原 Run 在 20:24:58 前可以正常使用 Sandbox。20:25:00，并发定时任务启动了另一个 Run：

- `run_id`：`run_ad8c4008a3484e8892639ea353721f3f`
- `session_id`：`group_1097666427`
- 该会话没有 Sandbox grant

随后，原 Run 从 20:25:01 起开始收到“当前会话没有显式 Sandbox 授权”。原群
`1061158966` 的 grant 始终为 `active + exec + developer`，对应 ToolPlan 也始终包含全部
Sandbox 工具。

根因是 KT 默认按配置名取得进程级全局 Session：

- `vendor/KohakuTerrarium/src/kohakuterrarium/bootstrap/agent_init.py`
  使用 `config.session_key or config.name`；
- `vendor/KohakuTerrarium/src/kohakuterrarium/core/session.py`
  将 Session 保存到进程级 `_sessions`；
- 旧实现把请求身份写入共享
  `session.extra["nanobot_runtime_context"]`；
- `core/daily_digest.py` 虽创建了“隔离” Bridge，但不同 Bridge 仍取得同一个
  `Session("nanobot")`。

因此，首要问题是请求身份和 KT Session 的并发隔离，不是 Sandbox grant 在运行中失效。

### 2.2 `stop=true` 的准确语义

同一条 `sandbox_exec` 命令在得到以下错误后又执行了 2 次，3 次调用的 command hash 完全相同：

```json
{
  "retryable": false,
  "stop": true
}
```

`stop=true` 在当前工具合同中的语义是停止重试当前错误或能力，不是无条件终止整个 Agent Run。
需要修复的是：

- 同名、同参数、不可重试的失败调用没有被抑制；
- 授权类失败后，同一工具族仍被继续调用；
- 运行时没有把结构化失败信封转化为稳定的循环控制状态。

本计划不把 `stop=true` 改成“终止整个 Run”，也不新增工具调用总数上限。

### 2.3 结构化业务失败被记为成功

目标 Run 共记录 33 次工具调用，其中 9 次返回结构化 `status=error`：

- `workspace_search invalid_path`：3 次；
- `sandbox_exec authorization_failed`：4 次；
- `workspace_read authorization_failed`：2 次。

数据库中的 `tool_calls.status` 却全部为 `success`。原因是：

- `nanobot_kt/tools/sandbox.py` 对结构化错误仍返回 `ToolResult(exit_code=0)`；
- `core/tool_tracing.py` 旧逻辑只检查异常、`error` 和退出码；
- Trace 没有解析工具结果中的稳定错误信封。

这不仅影响观测，也会让 KT Job 状态、失败统计和后续循环控制对同一结果得出不同结论。

### 2.4 没有模型路由重试

本次 22 个 LLM 请求均为：

- 同一个 provider：`newapi`；
- 同一个 model：`deepseek-v4-flash-max`；
- HTTP 200；
- `stream_success`。

这些请求是 KT 内部工具轮次，不是模型路由重试。本计划会为工具轮次和回复合同重试增加明确
Trace 来源，但不会把本次调用错误标记为“模型路由重试”。

### 2.5 末尾发生 1 次回复合同重试

20:25:45，模型直接输出文本，没有调用 `reply` 或 `no_reply`。系统只重试了 1 次；
20:25:50 仍直接输出文本，最终 Run 为：

- `status=suppressed`
- `error=no_tool_call`

本次回复合同重试没有重新调用普通工具。“最终回复阶段只允许 `reply/no_reply`”属于设计加固，
不能写成本次事故中已经发生的普通工具重入。

### 2.6 KT 轮次上限当前没有生效

基线配置把 `max_iterations: 12` 放在 `controller` 下：

```yaml
controller:
  max_iterations: 12
```

当前 KT 只从顶层读取 `config_data.get("max_iterations")`。生产容器直接加载后的
`loaded.max_iterations` 为 `None`，所以主 KT 链路没有生效的 12 轮上限。

`MAX_TOOL_ROUNDS=5` 只用于 `core/legacy_adapter.py`。`core/config_registry.py` 却把它描述成
“单次最大工具轮数”，容易让管理端误以为它控制当前 KT 主链路。

准确结论是：

- 当前没有总工具调用次数限制；
- 当前也没有生效的 KT 迭代硬上限；
- 不需要新增总工具调用次数限制；
- `max_iterations` 只作为请求级 LLM 迭代防死循环预算，不能宣传成工具次数限制。

### 2.7 定时任务并非每个扫描槽都调用模型

只有新领取并进入生成阶段的 occurrence 才会启动 Agent。以下情况不会调用模型：

- occurrence 已被幂等去重；
- 任务未到期；
- 任务已禁用；
- occurrence 未成功领取；
- 恢复检查发现已有可复用投递结果。

但新 occurrence 一旦进入当前 `_generate_task_message()`，仍会无条件启动 Agent。确定性提醒、
固定推送和简单状态判断也会走模型。

### 2.8 定时任务还存在更严重的安全与可靠性缺口

当前已确认：

- `scheduled_tasks` 没有 owner、creator 或 ACL 字段；
- `schedule_task list` 全局列出所有任务；
- 修改、启停、立即执行和删除只按全局 `task_id` 查找；
- 不同用户可能读取或操作彼此的任务；
- 私聊任务使用 `scheduled_task_<id>`，不是收件人的真实会话；
- `nanobot_kt/bridge.py` 会把缺少 `metadata.user_id` 的请求回退为 `session_id`，覆盖调用方传入的
  `user_id`；
- 历史、ToolPlan、Sandbox grant 和权限可能因此绑定到错误主体；
- 调度器逐个 `await` 任务生成，每个任务最长 600 秒，会阻塞后续到期任务；
- 补扫窗口最多 10 个分钟槽；
- outbox 只保障生成后的投递，不保障 Agent 内部多步骤工具执行；
- 崩溃恢复可能整体重跑，带副作用步骤没有逐步幂等和 checkpoint；
- 没有条件、循环、变量、游标、步骤状态和执行实例；
- 冻结快照未完整保存规范化 `interval/once` 调度、程序版本和状态；
- `model_trace_id` 已预留，但当前定时任务提交 outbox 时没有写入；
- `prompt_template` 创建时没有统一长度约束，执行时却静默截为 2000 字符。

### 2.9 定时任务 Prompt 状态需要分仓库与生产运行时描述

本代码基线中：

- `prompts.v2.default/tools/schedule_task/usage.md` 为 v2；
- `data/prompts_v2/tools/schedule_task/usage.md` 也为 v2；
- 两个文件 SHA-256 相同；
- `creatures/nanobot/prompts/skills/schedule_task/SKILL.md` 仍只描述 cron，确认过时。

生产 Prompt Runtime 位于 Compose 挂载目录，而不是仓库 `data/prompts_v2/`。生产核验发现的
v1 内容应归类为“生产运行时覆写漂移”，需要通过 Prompt Runtime audit/migration 迁移，不能仅
修改仓库文件后假定线上已同步。

### 2.10 Workspace 工具存在能力和正确性问题

当前实现已确认：

- `workspace_read.offset/limit` 按字节，而 KT `read` 按行，参数同名但语义相反；
- 任意字节边界会切断 UTF-8 中文字符；
- 解码失败后，正常文本会被误报为二进制并返回空内容；
- `workspace_search` 只有大小写敏感的字面量搜索；
- 没有正则、`ignore_case`、完整 `.gitignore`、文件名 glob 和 tree 语义；
- 每个文件只搜索前 1 MiB，后半部分命中会静默漏掉；
- 文件超过 1 MiB 但搜索队列已空时，`truncated` 仍可能为 `false`；
- 编辑只支持单文件严格 unified diff；
- 没有主流 `old/new/replace_all` 精确替换；
- 没有批量编辑；
- 文件工具始终相对 Workspace 根目录；
- `sandbox_exec` 中的 `cd` 不会成为后续文件工具的工作目录，模型容易反复构造错误前缀。

### 2.11 Sandbox 必须区分原始值、有效值和会话判定

生产 Compose 将以下业务环境变量写为安全启动值：

- `NANOBOT_SANDBOX_ENABLED=false`
- `NANOBOT_SANDBOX_EXEC_ENABLED=false`
- `NANOBOT_SANDBOX_GROUP_ENABLED=false`

但业务开关允许数据库优先覆写。2026-07-29 的生产数据库有效值为：

- `sandbox.enabled=true`
- `sandbox.exec_enabled=true`
- `sandbox.group_enabled=true`

对目标会话直接执行 Policy 校验时，`workspace_read` 和 `sandbox_exec` 均为已授权。因此不能把
容器原始环境变量写成当前有效状态。

一次完整判定至少包含：

1. 基础设施硬上限；
2. Sandbox 业务总开关；
3. Exec 业务开关；
4. 群聊业务开关；
5. canonical session identity；
6. session grant 状态与能力等级；
7. execution profile；
8. 会话执行硬上限；
9. Developer 网络硬上限；
10. Workspace 状态；
11. Workspace 配额 desired/applied/generation；
12. Runtime 配额 desired/applied/generation；
13. Workspace maintenance generation。

管理端和日志必须显示最终判定、各门的来源与禁用原因，不能只展示两个布尔值。

### 2.12 Agent Link Token 当前扩大了凭据授权面

未显式配置 `NANOBOT_AGENT_LINK_TOKEN` 时，当前实现回退复用 `NANOBOT_API_TOKEN`。生产中：

- 没有显式 Agent Link Token；
- 有效 Agent Link Token 来自 API Token 回退；
- API Token 从宿主 `.env` 注入；
- `.env` 已被 Git 忽略，不应提交；
- 两个接口复用同一凭据，使泄露、轮换和权限范围相互影响。

诊断只能报告 `configured`、`source` 和是否使用回退，不能输出 Token 正文或可逆摘要。

### 2.13 Runtime 发布与 Sandbox 发布必须分开

已确认当前生产是从本地构建并部署：

- 4 个 Runtime 服务使用同一个 `nanobot-runtime:1d34f3c-local`；
- 容器健康；
- 镜像 revision 等于当前 HEAD；
- 本地构建不依赖 GitHub Actions artifact。

GitHub Actions artifact 只包含：

- SBOM；
- 验证结果；
- `ArtifactManifest`；
- `ReleaseManifest`；
- 校验和。

OCI 镜像单独推送到 GHCR。当前 Action 只构建 Runtime，不构建 Restricted、Developer 和
egress-proxy Sandbox 镜像。

从 Sandbox 控制面提交 `991d095d` 到当前 HEAD，Sandbox 镜像构建输入没有变化。因此相关
Agent Link 提交不要求重建 Sandbox 镜像。复用固定 Sandbox 镜像表示保留相同 IMAGE ID，不是
重新构建后沿用旧 tag。

真实 Sandbox Smoke 需要 root 保护凭据。当前 `sudo -n` 无法取得所需权限，所以只能确认固定
镜像和 Profile manifest，不能声称真实 Smoke 已重跑完成。

### 2.14 本地构建的 dirty 身份存在漏报

`scripts/docker-build.sh` 使用：

```bash
git status --porcelain --untracked-files=no
```

但 Runtime Dockerfile 使用：

```dockerfile
COPY . .
```

未被 `.dockerignore` 排除的未跟踪源码可能进入镜像，镜像却仍被标记
`GIT_DIRTY=false`。本次生产构建时工作树干净，没有受此问题影响；后续构建仍需修复。

## 3. 目标、非目标与固定边界

### 3.1 目标

1. 并发 Run 永远不能读取、覆盖或继承其他 Run 的受信身份。
2. 不同 Bridge 不再共享 KT Session；停止或启动失败时不遗留 Session 注册项。
3. 结构化 `status=error` 在 KT Job、数据库 Trace、Runtime Event 和循环策略中都表示失败。
4. `retryable=false` 或 `stop=true` 能阻止相同失败参数再次执行。
5. 授权类错误能阻止同一工具族继续消耗调用，但不无条件结束整个 Run。
6. 回复合同重试最多 1 次，重试阶段只暴露 `reply/no_reply`。
7. `max_iterations=12` 真正作用于 KT 请求级 LLM 迭代预算。
8. 定时任务只能由其 owner 或显式管理员读取和操作。
9. 定时任务使用真实目标会话和执行主体解析历史、ToolPlan 与 Sandbox grant。
10. 简单确定性任务不调用模型；复杂任务可以使用统一程序、状态和 checkpoint。
11. Workspace 工具具备行读取、正则 grep、glob/tree、精确替换、diff 和批量编辑能力。
12. Workspace 的截断、跳过、超时和未扫描范围全部显式返回，禁止静默漏报。
13. 管理端能区分配置原始值、有效值、来源和当前会话的最终判定。
14. Agent Link 使用可独立轮换的凭据，并显式暴露是否仍在兼容回退。
15. Runtime 与 Sandbox 分别记录发布身份、影响范围、Smoke 证据和回滚对象。

### 3.2 非目标

- 不增加单次 Run 的工具调用总数上限。
- 不把 `stop=true` 改成无条件终止整个 Agent Run。
- 不重写 KT Agent Loop，也不升级到另一个 Agent 框架。
- 不把 KT 宿主 `read/edit/grep/glob/bash` 直接暴露给模型。
- 不允许模型指定宿主路径、Docker 参数、镜像、volume、network 或 capability。
- 不为提醒、监控、日报和工作流分别增加模型工具。
- 不承诺外部副作用 exactly-once；结果不确定时必须停在可审计状态。
- 不把 GitHub Actions artifact 当作 OCI 镜像包。
- 不因普通 Runtime 代码变化无条件重建 Sandbox 镜像。
- 不在日志、Admin API、ReleaseManifest 或测试快照中输出密钥正文。
- 不在本计划执行中自动提交、推送或部署。

### 3.3 必须保持的安全路径

所有 Workspace、资产和执行请求继续遵循：

```text
模型工具
  -> 当前请求 ToolPlan
  -> Nanobot SandboxAccessPolicy
  -> sandboxd UDS
  -> 当前 owner Workspace
  -> 固定服务端策略
```

任何兼容新版 KT 心智模型的工作都只能复用参数和返回语义，不能恢复 KT 的宿主文件访问。

## 4. 目标架构与核心合同

### 4.1 请求身份与 KT Session

每次请求构造不可变 `RequestRuntimeContext`。Adapter 在调用 KT 前将其绑定到请求级
`ContextVar`，工具只读取这个受信上下文：

```text
Bridge.handle_message
  -> RequestRuntimeContext
  -> runtime_context_scope()
  -> process_event()
  -> Executor 子任务 / asyncio.to_thread
  -> SandboxToolBase.require_current_runtime_context()
```

约束如下：

- 禁止再把请求身份写入 `Session.extra`；
- 工具传入的普通 `ToolContext` 不得覆盖受信身份；
- `ContextVar` 在子协程和 `asyncio.to_thread` 中继承；
- 请求结束、异常和取消都必须 reset；
- 缺少绑定时 fail closed；
- 每个 `NanobotBridge` 使用不可复用的 `session_key`；
- Bridge 停止或启动失败时从 KT `_sessions` 移除该 key；
- Session key 只用于 KT 内部隔离，不成为业务会话、权限或 Workspace 身份。

### 4.2 工具失败信封

结构化失败的唯一最低合同为：

```json
{
  "status": "error",
  "summary": "面向模型的有界摘要",
  "next_actions": [],
  "artifacts": [],
  "error": {
    "code": "authorization_failed",
    "retryable": false,
    "hint": "下一步建议",
    "stop": true
  }
}
```

运行时处理规则：

| 条件 | KT Job | Trace | 后续调用 |
|---|---|---|---|
| `status=success` | success | success | 正常 |
| `status=error, retryable=true, stop=false` | error | error | 允许调整或重试 |
| `status=error, retryable=false` | error | error | 禁止同名同参重试 |
| `status=error, stop=true` | error | error | 禁止同名同参重试 |
| 授权类错误 | error | error | 阻止同一工具族 |
| 回复合同重试 | 独立来源 | 独立来源 | 只允许 `reply/no_reply` |

`stop` 不作为整个 Run 的全局中断位。

### 4.3 调用指纹与工具族

同参调用指纹使用：

```text
sha256(canonical_json(tool_name, args))
```

JSON 必须稳定排序、保留 Unicode，并对非 JSON 原生值使用明确归一化。请求级状态只在当前 Run
中生效，不跨 Run 缓存。

首期工具族：

| 工具前缀 | 工具族 |
|---|---|
| `workspace_*` | `workspace` |
| `sandbox_*` | `sandbox_process` |
| `asset_*` | `asset` |
| 其他工具 | 精确工具名 |

授权类失败至少包括：

- `authorization_failed`
- `sandbox_not_enabled`
- `asset_not_authorized`

若后续确认某个错误影响整个 Sandbox grant，而不只影响一个工具族，应增加明确的
`capability_scope` 字段，不能靠错误文本推断。

### 4.4 LLM 轮次与终态来源

Trace 需要区分：

- `agent.tool_round`：KT 正常工具轮次；
- `agent.final_action`：首次最终回复阶段；
- `agent.reply_contract_retry`：唯一一次回复合同重试；
- `model.route_retry`：只有模型路由真实切换或传输重试时才使用。

每个来源记录单调 `round_index`。本次事故的 22 个请求应归入工具轮次和最终回复，不得归入
`model.route_retry`。

### 4.5 Workspace 路径合同

模型只能看到 Workspace 相对路径。所有文件工具统一接受可选 `cwd`：

- `cwd=""` 表示 Workspace 根目录；
- `path` 相对 `cwd` 解析；
- 解析后必须仍位于当前 Workspace；
- 禁止绝对路径、`..`、符号链接逃逸和宿主路径；
- 不从前一次 `sandbox_exec` 的 shell 状态隐式推断 `cwd`；
- `sandbox_exec` 返回有效 `cwd`，模型需要在后续文件工具中显式传入。

这样既解决反复路径前缀问题，也避免不同并发工具共享可变“当前目录”。

### 4.6 Workspace 工具面

模型侧稳定工具收敛为：

- `workspace_read`
- `workspace_search`
- `workspace_edit`
- `workspace_write`
- `sandbox_exec`
- `schedule_task`

`workspace_list` 和 `workspace_apply_patch` 在兼容期可以保留内部别名，但不与新工具同时进入模型
Prompt。`asset_import`、`asset_publish` 和 Sandbox 进程控制工具仍按实际 ToolPlan 按需暴露，
不属于本次 Code Agent 文件契约收敛范围。

#### `workspace_read`

```text
workspace_read(path, offset=0, limit=200, cwd="")
```

- `offset`：从 0 开始的行偏移；
- `limit`：读取行数；
- UTF-8 解码按完整字符和完整行进行；
- 返回带稳定行号的文本；
- 返回 `start_offset`、`returned_lines`、`next_offset`、`total_lines` 和 `eof`；
- 超长行显式标记 `line_truncated=true`；
- 总输出达到上限时返回 `output_truncated=true` 和可继续的 `next_offset`；
- 二进制判定基于内容特征，不以任意切片解码失败作为唯一依据。

#### `workspace_search`

```text
workspace_search(
  mode,
  pattern="",
  path="",
  glob="",
  limit=50,
  ignore_case=false,
  max_depth=null,
  cursor="",
  cwd=""
)
```

`mode` 只有 3 种：

- `content`：正则 grep；
- `files`：文件名 glob；
- `tree`：目录树。

返回统一 item：

```json
{
  "path": "src/example.py",
  "line": 42,
  "text": "匹配行",
  "type": "file",
  "truncated": false
}
```

未适用字段省略。全局返回包含：

- `items`
- `scanned_files`
- `scanned_bytes`
- `skipped_binary_files`
- `skipped_ignored_files`
- `truncated`
- `truncation_reason`
- `next_cursor`

实现要求：

- 使用有超时能力的正则实现；
- 限制 pattern 长度、编译时间、匹配时间和总扫描预算；
- 尊重 Workspace 内 `.gitignore`；
- 固定跳过 `.git`、`node_modules`、缓存和虚拟环境目录；
- 跳过二进制文件；
- 不再固定只读每个文件前 1 MiB；
- 达到时间、文件数、字节数或结果数上限时必须返回
  `truncated=true`；
- 通过 `next_cursor` 继续扫描，禁止静默漏报。

#### `workspace_edit`

```text
workspace_edit(operations, cwd="")
```

每个 operation 二选一：

```json
{
  "path": "src/example.py",
  "old": "旧文本",
  "new": "新文本",
  "replace_all": false
}
```

或：

```json
{
  "diff": "多文件 unified diff"
}
```

规则：

- `old` 必须精确匹配；
- 0 处命中返回稳定失败；
- 多处命中且 `replace_all=false` 时拒绝修改；
- `replace_all=true` 时返回实际替换次数；
- diff 必须校验目标均位于当前 Workspace；
- 支持多文件批量编辑；
- 先完成全部路径、内容、配额和磁盘水位校验，再进入写阶段；
- 在 Workspace 写锁内写临时文件、`fsync` 并原子替换；
- 多文件批次使用事务日志和恢复流程，不能把部分完成伪装成成功；
- 修改结果返回每个文件的旧/新 SHA-256、替换次数和恢复状态；
- Trace 只记录元数据和 hash，不记录正文或 diff。

### 4.7 统一定时任务结构

不增加“提醒”“监控”“日报”“工作流”等任务类型。所有任务使用一个版本化合同：

```json
{
  "schema_version": 1,
  "owner": {
    "chat_stream_id": "qq:private:<external-session-id>",
    "created_by_actor_id": "<actor-id>"
  },
  "trigger": {
    "kind": "once|interval|cron",
    "timezone": "Asia/Shanghai",
    "spec": {}
  },
  "program": {
    "version": 1,
    "steps": []
  },
  "limits": {
    "max_steps": 100,
    "max_loop_iterations": 100,
    "max_duration_seconds": 600
  }
}
```

`program.steps` 只包含少量内部语句，不增加模型工具：

- `set`：写入变量；
- `tool`：调用当前 ToolPlan 允许的普通工具；
- `model`：按需调用模型；
- `branch`：条件分支；
- `loop`：有界循环；
- `wait`：持久等待到明确时刻；
- `emit`：生成待投递结果。

简单提醒可以只使用 `emit`，模型调用数为 0。需要浏览、看图、检索或总结的任务才使用
`tool/model`。

### 4.8 定时任务归属与执行身份

任务定义至少保存：

- `owner_chat_stream_id`
- `owner_platform`
- `owner_chat_type`
- `owner_session_id`
- `created_by_actor_id`
- `definition_version`
- `program_json`
- `program_sha256`
- `created_at`
- `updated_at`

访问规则：

- 普通工具调用只列出当前 `owner_chat_stream_id` 的任务；
- 查改、启停、运行和删除同时按 `task_id + owner_chat_stream_id` 查询；
- 群任务归群会话所有，创建者只作为审计 actor；
- 私聊任务归私聊会话所有；
- 管理端跨 owner 操作必须使用独立 Admin API、显式审计和理由；
- 默认投递目标必须与 owner 会话一致；
- 跨会话投递需要独立授权，不能仅因模型传入 `target_id` 就允许。

执行时使用冻结的 owner principal 构造 `RequestRuntimeContext`：

- 私聊任务使用收件人的真实私聊 session；
- 群任务使用真实群 session；
- 不再使用 `scheduled_task_<id>` 作为权限主体；
- 历史、ToolPlan、Sandbox grant 和 Workspace 都按同一 canonical identity 解析；
- 每次执行仍重新读取当前授权，冻结主体不能绕过后来撤销的 grant。

### 4.9 定时任务执行状态

新增持久执行实例和步骤尝试：

#### `ScheduledTaskExecution`

| 字段 | 语义 |
|---|---|
| `id` | 执行实例 ID |
| `task_id` / `task_version` | 冻结定义来源 |
| `occurrence_key` | occurrence 幂等键 |
| `scheduled_for` | 逻辑触发时刻 |
| `owner_snapshot_json` | 冻结执行主体 |
| `trigger_snapshot_json` | 完整规范化 trigger |
| `program_snapshot_json` | 完整程序与版本 |
| `state_json` | 变量、游标和中间状态 |
| `current_step_id` | 恢复位置 |
| `status` | pending/running/waiting/succeeded/failed/blocked/ambiguous |
| `lease_owner/token/expires_at` | worker fencing |
| `agent_trace_id` / `agent_run_id` | Agent Trace 关联 |
| `outbound_run_id` | 最终 outbox 关联 |
| `created_at/started_at/finished_at` | 审计时间 |

唯一约束为 `(task_id, occurrence_key)`。

#### `ScheduledTaskStepAttempt`

| 字段 | 语义 |
|---|---|
| `execution_id` / `step_id` | 所属执行与稳定步骤 ID |
| `attempt_no` | 单调尝试序号 |
| `idempotency_key` | `execution:step:attempt-policy` 派生键 |
| `operation` | set/tool/model/branch/loop/wait/emit |
| `status` | started/succeeded/failed/blocked/ambiguous |
| `input_sha256/output_sha256` | 脱敏审计 |
| `tool_call_id/model_trace_id` | 外部调用关联 |
| `checkpoint_json` | 成功后的恢复点 |
| `error_type/error_summary` | 有界结构化错误 |
| `started_at/completed_at` | 审计时间 |

带副作用工具必须接收稳定幂等键。无法证明未执行的步骤进入 `ambiguous`，不自动整体重跑。

### 4.10 调度与执行解耦

当前 `run_scheduled_tasks()` 不再同步等待模型：

```text
分钟扫描
  -> 发现到期 trigger
  -> 原子创建 ScheduledTaskExecution
  -> 推进 next_fire_at
  -> 立即返回

generation/workflow worker
  -> 有界并发领取 execution
  -> 逐步执行和 checkpoint
  -> emit 后提交 outbox
```

要求：

- 扫描延迟不受单个 600 秒任务影响；
- worker 并发数可配置，但不是模型工具次数限制；
- 每个 owner 可配置并发互斥策略；
- lease 过期可恢复；
- 10 分钟补扫不再是唯一恢复机制；
- trigger occurrence 由数据库状态和 `next_fire_at` 恢复；
- `once/interval/cron` 全部进入冻结快照；
- 任务编辑不能改变已经领取的 execution；
- outbox 继续负责投递可靠性，workflow execution 负责生成前的步骤可靠性。

### 4.11 Prompt 与任务长度

删除“创建不限制、执行静默截为 2000 字符”的双重语义：

- 定义单一 `MAX_SCHEDULED_TASK_PROGRAM_BYTES`；
- 首期建议 64 KiB；
- 单个 `model` 步骤 prompt 首期限制 16,000 个 Unicode 字符；
- API、Tool Schema、数据库服务和执行器使用同一常量；
- 超限时创建或更新直接返回可诊断错误；
- 执行时发现历史脏数据，标记任务 `blocked`，不静默截断后执行；
- Prompt Runtime 只描述实际支持的 trigger、program 和状态语义。

## 5. 分阶段实施

### 阶段 0：冻结回归证据

先补能稳定复现问题的测试，不改生产行为。

#### 0.1 并发串线回归

构造 2 个并发请求：

- 请求 A：有 Sandbox grant；
- 请求 B：无 Sandbox grant；
- 使用 barrier 保证 B 在 A 工具执行前后切换；
- 验证 A 始终读取 A 的 `session_id/group_id/user_id`；
- 验证 B 始终被拒绝；
- 验证请求结束后 `ContextVar` 为空；
- 验证 `asyncio.create_task` 与 `asyncio.to_thread` 都继承各自上下文。

再构造 2 个独立 Bridge：

- 配置名都为 `nanobot`；
- Session key 必须不同；
- scratchpad、channels 和 `extra` 不互通；
- stop 后 `_sessions` 无残留 key。

#### 0.2 结构化失败回归

用 `ToolResult(exit_code=0)` 包装结构化 `status=error`，验证：

- `tool_calls.status=error`；
- Runtime Event phase 为 `failed`；
- `failure_code`、`retryable`、`stop` 被记录；
- 工具正文仍按现有脱敏策略处理；
- 不因 Trace 解析失败阻断工具主流程。

#### 0.3 循环控制回归

覆盖：

- 相同工具、相同参数、`retryable=false`：第 2 次被抑制；
- 相同工具、不同参数：允许；
- `retryable=true, stop=false`：允许；
- `stop=true`：只抑制相同失败能力，不结束整个 Run；
- `authorization_failed`：同族被抑制，其他工具族允许；
- 回复合同重试：普通工具被拒绝，`reply/no_reply` 允许。

#### 0.4 定时任务越权回归

建立 owner A、owner B 和 Admin 三种主体，验证：

- A 不能 list/get/update/toggle/run/delete B 的任务；
- 只知道全局 task ID 也不能越权；
- 群任务按群 owner 隔离；
- Admin 走独立审计入口；
- 旧无 owner 任务在迁移前 fail closed。

#### 0.5 Workspace 当前缺陷回归

覆盖：

- 中文 UTF-8 字符恰好跨旧字节边界；
- 从中文字符中间 offset 读取；
- 1 MiB 之后才出现搜索命中；
- 达到扫描预算时 `truncated=true`；
- `.gitignore`；
- 大小写与正则；
- 多命中精确替换拒绝；
- 多文件批次中某一文件校验失败时不写任何文件；
- `cwd` 解析和 `..` 逃逸。

### 阶段 1：修复上下文隔离、失败识别和终态

#### 1.1 请求身份

1. 新增框架无关的 `core/agent_runtime/request_scope.py`。
2. `Kt13RuntimeAdapter.execute_turn()` 在 `process_event()` 外绑定请求级上下文。
3. Sandbox、`schedule_task` 和 `persona_update` 等读取身份的工具改用请求级上下文。
4. 删除所有生产路径对 `session.extra["nanobot_runtime_context"]` 的写入和读取。
5. 缺少请求级绑定时 fail closed。

#### 1.2 KT Session

1. 每个 Bridge 生成唯一 `config.session_key`。
2. Bridge stop 时调用 `remove_session(session_key)`。
3. start 失败路径也执行相同清理。
4. 保留业务 `session_id` 与 KT 内部 Session key 的严格分离。

#### 1.3 Trace 与 KT Job

1. Sandbox Adapter 根据结构化 `status` 设置 `ToolResult.exit_code`。
2. 结构化失败填入安全、稳定的 `error` 摘要。
3. Trace 独立解析结构化信封，不能只依赖退出码。
4. 数据库 `ToolCall.status`、Runtime Event 和 KT Job 状态统一。
5. Runtime Event Registry 增加 `failure_code/error_type/retryable/stop`。
6. 更新对应 Golden。

#### 1.4 循环控制

1. 建立请求级 `ToolExecutionState`。
2. 记录失败调用指纹与工具族。
3. 在执行前拦截已知不可重试的同参调用。
4. 授权类失败阻止同族调用。
5. 拦截结果使用稳定结构化错误，不依赖自然语言。
6. 不调用 Agent interrupt，不设置整个 Run 的硬停止位。

#### 1.5 回复合同

1. 保持最多 1 次重试。
2. 通过 ToolPlan 只减不增地裁剪到 `reply/no_reply`。
3. 回复合同重试使用独立 LLM Trace source。
4. 最终仍无 Tool Call 时继续 `suppressed/no_tool_call`。

#### 1.6 KT 迭代预算

1. 将 `max_iterations: 12` 移到 YAML 顶层。
2. 启动测试断言
   `load_agent_config("creatures/nanobot").max_iterations == 12`。
3. 启动日志记录有效预算，不记录请求正文。
4. 将 `max_tool_rounds` 描述改为“旧 legacy adapter 工具轮次上限”。
5. 管理端不得把 `MAX_TOOL_ROUNDS` 展示为当前 KT 主链路限制。
6. 不新增工具总数限制。

### 阶段 2：先修定时任务的归属和执行身份

这是独立安全切片，不能等待完整 workflow engine。

#### 2.1 数据迁移

对 `scheduled_tasks` 增加 owner、actor、定义版本和更新时间字段。

旧任务迁移规则：

1. 能从 `target_type/target_id` 生成 canonical owner 的任务，绑定到该目标会话。
2. 目标格式无效或主体不明确的任务设为 disabled。
3. 无法安全迁移的任务标记 `owner_migration_required`。
4. 不删除历史任务、outbox 或投递记录。
5. 迁移可重复执行并有行数、hash 和异常清单。

#### 2.2 工具与 API 授权

1. `schedule_task` 从请求级上下文取得 owner。
2. list 只返回当前 owner 的任务。
3. 所有 mutation 同时按 owner 过滤。
4. 创建时默认目标为当前 owner 会话。
5. 跨目标投递必须经过显式 Policy。
6. Admin 跨 owner 操作使用独立端点和审计记录。

#### 2.3 正确执行主体

1. 删除私聊 `scheduled_task_<id>` 权限身份。
2. 由冻结 owner snapshot 构造真实 `RequestRuntimeContext`。
3. 修复 Bridge 将调用方 `user_id` 回退覆盖为 `session_id` 的逻辑。
4. 明确 `actor_user_id` 与会话 owner 的区别。
5. Agent Run、Outbound Run 和 generation attempt 写入可直接关联的 trace/run ID。

#### 2.4 长度与快照

1. 创建和更新时统一校验长度。
2. 删除执行时静默截断。
3. 快照增加 `schedule_kind/schedule_spec`、时区、owner、定义版本和完整性 hash。
4. 恢复时验证快照版本与 hash。

### 阶段 3：升级安全版 Workspace 工具

#### 3.1 sandboxd 文件服务

1. `read_file` 改为按行读取和 UTF-8 增量解码。
2. 增加真实二进制判定、长行限制和输出预算。
3. `search_files` 增加 `content/files/tree` 模式。
4. 引入锁定版本的超时正则和 gitignore 匹配依赖。
5. 增加扫描 cursor、时间/文件/字节预算和显式截断原因。
6. 新增 `edit_files`，统一精确替换和 unified diff。
7. 多文件编辑使用 Workspace 写锁、配额检查、磁盘水位、临时文件和恢复日志。
8. 保留现有 SafeWorkspaceFilesystem 的 `openat`/路径约束，不回退到普通宿主 `Path` 拼接。

#### 3.2 服务端调用链

同步修改：

- sandboxd 请求/响应模型；
- UDS 路由；
- `core/sandbox/client.py`；
- `core/sandbox/tool_service.py`；
- `nanobot_kt/tools/sandbox.py`；
- Tool Descriptor、Access Contract 和 ToolPlan；
- Trace 脱敏。

#### 3.3 兼容迁移

1. 新模型工具名为 `workspace_edit`。
2. `workspace_apply_patch` 保留为内部兼容别名 1 个迁移周期。
3. 兼容别名不得与新工具同时进入模型 schema。
4. `workspace_list` 的能力并入 `workspace_search(mode=tree/files)`。
5. 兼容使用进入 `CompatibilityRegistry` 和 telemetry。
6. 连续观察零使用后再删除旧别名。

### 阶段 4：实现统一任务程序和持久执行器

#### 4.1 拆分调度与执行

1. 调度器只发现 trigger 并创建 execution。
2. 新 worker 有界并发领取 execution。
3. 领取、续租、checkpoint 和结算都使用 fencing token。
4. 单个慢任务不阻塞其他到期任务。
5. 恢复不依赖 10 个分钟槽的内存补扫。

#### 4.2 程序解释器

1. 解析版本化 `program`。
2. 校验步骤 ID 唯一、引用存在、控制流可达。
3. 统一变量和状态表达式，不执行任意 Python。
4. 条件、循环和等待均有显式边界。
5. 每个步骤成功后持久 checkpoint。
6. `tool` 步骤只能调用当前执行主体 ToolPlan 中的工具。
7. `model` 步骤才调用模型。
8. `emit` 产生不可变 Message Envelope。

#### 4.3 副作用和恢复

1. 每个步骤生成稳定幂等键。
2. 已确认成功的步骤不重复执行。
3. 瞬态失败按步骤策略重试。
4. 不可重试失败进入 `failed/blocked`。
5. 无法判断是否已产生副作用时进入 `ambiguous`。
6. `ambiguous` 需要人工确认，不能从头自动重跑。
7. outbox 继续复用已有投递状态机，不在 workflow 内直接发 QQ HTTP。

#### 4.4 旧任务兼容

旧 `prompt_template` 任务迁移为：

```json
{
  "steps": [
    {
      "id": "legacy_model",
      "op": "model",
      "prompt": "<原 prompt_template>"
    },
    {
      "id": "legacy_emit",
      "op": "emit",
      "from": "legacy_model.output"
    }
  ]
}
```

迁移后行为保持“调用模型后投递”，但获得 owner、执行实例、trace 和 checkpoint。后续由用户或
Admin 显式改成确定性步骤，不做语义猜测式自动改写。

### 阶段 5：配置、凭据与发布事实源

#### 5.1 Sandbox 有效配置诊断

扩展 Sandbox Admin 状态，分别显示：

- 原始环境值；
- 数据库覆写值；
- 有效值；
- 来源；
- 是否为硬上限；
- 当前 session 的逐门判定；
- 最终允许/拒绝；
- 稳定 reason code。

敏感字段只返回：

```json
{
  "configured": true,
  "source": "environment",
  "fallback": false
}
```

不得返回值、前后缀或可逆 hash。

#### 5.2 Agent Link 独立凭据

分两步迁移：

1. 兼容期：
   - 保留 API Token 回退；
   - 启动日志和 Admin 诊断明确显示 `source=api_token_fallback`；
   - 每次启动只告警 1 次；
   - 文档要求生产显式设置 Agent Link Token。
2. 收紧期：
   - 生产部署显式注入独立 Token；
   - 完成客户端切换和回滚演练；
   - 关闭生产回退；
   - API Token 与 Agent Link Token 独立轮换。

收紧期必须在所有现有客户端完成迁移后实施，不能直接使在线客户端全部离线。

#### 5.3 Runtime 发布身份

每次本地或 CI 构建记录：

- Git full SHA；
- Git dirty 状态；
- KT SHA；
- 构建上下文 hash；
- 镜像 tag/reference；
- IMAGE ID；
- registry digest（如有）；
- 构建时间；
- 部署时间；
- 4 个 Runtime 服务实际 IMAGE ID；
- 回滚镜像 IMAGE ID；
- Smoke 结果与证据路径。

生产构建优先使用 exact SHA 的干净 worktree。开发构建可以允许 dirty，但必须准确标记。

#### 5.4 修复未跟踪文件漏报

最低修复：

- dirty 检查包含未跟踪文件；
- 测试证明未跟踪且未被 `.dockerignore` 排除的源码会使 `GIT_DIRTY=true`；
- 生产模式拒绝 dirty 构建。

完整修复：

- 根据实际 Docker build context 生成确定性 manifest；
- manifest 记录路径、大小和 SHA-256；
- 生成 `build_context_sha256`；
- ReleaseManifest 同时保存 Git SHA 和 build context hash；
- `.dockerignore` 中的凭据、数据、Workspace、资产和 Runtime 目录继续强制排除。

#### 5.5 Runtime 与 Sandbox 发布影响矩阵

| 变化 | Runtime 镜像 | Sandbox 镜像 | sandboxd | 真实 Sandbox Smoke |
|---|---:|---:|---:|---:|
| 普通 API/Agent Link/Prompt 代码 | 重建 | 不重建 | 不重装 | 不要求完整矩阵 |
| `nanobot_kt/tools/sandbox.py` 合同 | 重建 | 通常不重建 | 视 UDS 合同而定 | 相关工具 Smoke |
| `sandboxd/` 或 UDS 合同 | 重建客户端侧 | 视镜像输入而定 | 重装 | 必须 |
| Sandbox Dockerfile/依赖 | 不一定 | 重建对应镜像 | 可能不变 | 必须 |
| Profile/AppArmor/seccomp/网络策略 | 不一定 | 视输入而定 | 重装/刷新 manifest | 必须 |
| 仅 Runtime 文档或测试 | 按发布策略 | 不重建 | 不重装 | 不要求 |

影响判断由代码所有的输入清单和 hash 决定，不靠提交标题或人工猜测。

#### 5.6 Smoke 分层

Runtime Smoke：

- 4 个实际部署容器使用同一 IMAGE ID；
- `/api/v1/health`；
- 真实聊天接口；
- Agent Link WebSocket 握手与最小消息往返；
- 任务/Worker 基本读取；
- Prompt Runtime hash 和迁移状态。

Sandbox Smoke 仅在影响矩阵命中时执行：

- 非 root；
- 只读根；
- 无 Docker Socket；
- 默认无网络；
- CPU/内存/PID/tmpfs 限制；
- 超时终止进程树；
- Workspace 跨容器重建持久；
- owner 隔离；
- UTF-8 读取、搜索、编辑；
- 配额和水位；
- 固定镜像 IMAGE ID 与 Profile manifest。

缺少 root 保护凭据时，状态必须写成 `BLOCKED_NOT_RUN`，不能以服务 active 或容器 healthy
代替。

### 阶段 6：Prompt、Schema、Admin 与 Golden 同步

任何工具或任务合同变化必须在同一实现切片同步：

1. `core/tool_schema_preview.py`
2. KT 工具注册和执行绑定
3. `core/tool_registry.py`
4. Sandbox Access Contract
5. Trace 参数与结果脱敏
6. `prompts.v2.default/tools/*/usage.md`
7. 仓库 `data/prompts_v2/tools/*/usage.md`
8. 生产 Prompt Runtime audit/migration
9. `core/prompt_v2/template_registry.py`
10. Admin 工具说明
11. Tool/Runtime Registry Golden
12. 相关 Prompt Golden 与 eval fixture

定时任务额外同步：

- `prompts.v2.default/tools/schedule_task/usage.md`
- `data/prompts_v2/tools/schedule_task/usage.md`
- `creatures/nanobot/prompts/skills/schedule_task/SKILL.md`
- 程序 schema/version；
- Prompt Runtime 迁移记录；
- 生产 runtime/default hash 对比。

生产 Runtime 模板升级流程固定为：

```text
audit
  -> 显示 default/runtime hash 与版本
  -> plan migration
  -> 备份
  -> apply
  -> resolve
  -> smoke
  -> 保留 rollback
```

不能只覆盖仓库 `data/prompts_v2` 后假定挂载目录已更新。

## 6. 主要文件范围

| 切片 | 主要文件 |
|---|---|
| 请求上下文 | `core/agent_runtime/request_scope.py`、`nanobot_kt/runtime_adapter.py` |
| KT Session 隔离 | `nanobot_kt/bridge.py`、`core/agent_runtime/gateway.py` |
| 工具循环控制 | `core/tool_execution_policy.py`、`nanobot_kt/tool_runtime.py` |
| Sandbox 工具结果 | `nanobot_kt/tools/sandbox.py` |
| Trace | `core/tool_tracing.py`、`core/tracing.py`、`core/runtime/event_registry.py` |
| KT 预算 | `creatures/nanobot/config.yaml`、`core/config_registry.py` |
| 定时任务模型 | `core/db/models/scheduling.py`、`core/schema_migrations.py` |
| 定时任务工具 | `creatures/nanobot/prompts/skills/schedule_task/tool.py` |
| 调度与执行 | `core/daily_digest.py`、`core/scheduled_task_outbound.py`、新增 workflow 模块 |
| Outbound 关联 | `core/outbound/`、`core/db/models/outbound.py` |
| Workspace 文件服务 | `sandboxd/filesystem.py`、`sandboxd/app.py` |
| Workspace 客户端 | `core/sandbox/client.py`、`core/sandbox/tool_service.py` |
| Workspace Schema | `core/tool_schema_preview.py`、`core/tool_registry.py` |
| Prompt | `prompts.v2.default/tools/`、`data/prompts_v2/tools/` |
| Sandbox Admin | `api/admin/sandbox_routes.py`、对应 WebUI 页面与生成 Client |
| Agent Link 凭据 | `config.py`、部署模板、启动诊断与文档 |
| 发布 | `scripts/docker-build.sh`、ReleaseManifest 构建与部署脚本 |
| Golden | `tests/golden/architecture_behavior/` 及相关 Prompt Golden |

只在实际切片需要时修改表中对应文件，不因本计划顺手重构相邻模块。

## 7. 数据迁移与兼容策略

### 7.1 数据库

- 所有 schema 变化通过 `core/schema_migrations.py` 增量迁移；
- 测试使用内存或临时 SQLite；
- 不删除 `ChatLog`、旧 task、outbox、attempt 或 Trace；
- 先加 nullable/默认字段，再回填，再启用非空约束；
- owner 迁移无法证明时 fail closed；
- 每次迁移记录版本、行数、异常数和校验 hash；
- 回滚应用版本时，新列保持向前兼容，不做破坏性降级。

### 7.2 工具兼容

- `workspace_apply_patch` 到 `workspace_edit` 使用 Compatibility Registry；
- 旧名不进入新 Prompt；
- 旧调用进入 telemetry；
- 观察期结束前不删除执行别名；
- 参数语义变化不得在同一工具名下静默解释旧 payload。

`workspace_read` 的 `offset/limit` 语义从字节改为行属于不兼容变化。迁移时必须：

1. 升级 schema version；
2. 同步 Prompt；
3. 更新全部调用方和测试；
4. 拒绝带旧协议版本的远端调用；
5. 在 Agent Link/MCP 暴露时声明新版本。

### 7.3 Prompt Runtime

- 仓库 canonical 与生产 runtime 分别计算 hash；
- 生产 override 有已知基线时自动生成迁移计划；
- 无法证明来源时只报告，不自动覆盖；
- apply 前备份；
- apply 后运行 Prompt 编译、变量合同和工具 schema Golden；
- 失败时恢复原 runtime 目录。

## 8. 验证矩阵

### 8.1 最小定向测试

每个切片先运行直接相关测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest \
  tests/test_agent_runtime_port.py \
  tests/test_bridge_prompt_v2.py \
  tests/test_tool_execution_policy.py \
  tests/test_sandbox_tools.py \
  tests/test_sandbox_trace_policy.py \
  -v
```

定时任务：

```bash
python -m pytest \
  tests/test_schedule_task_tool.py \
  tests/test_scheduled_task_outbound.py \
  tests/test_schema_migrations.py \
  tests/test_daily_digest.py \
  -v
```

Workspace：

```bash
python -m pytest \
  tests/test_sandboxd_api.py \
  tests/test_sandbox_filesystem.py \
  tests/test_sandbox_tools.py \
  tests/test_sandbox_trace_policy.py \
  -v
```

发布与配置：

```bash
python -m pytest \
  tests/test_config_registry.py \
  tests/test_deploy_config.py \
  tests/test_release_manifest_cli.py \
  tests/test_agent_link_websocket.py \
  -v
```

测试文件名以仓库实际存在为准；新增测试继续使用 `tests/test_<module>.py`。

### 8.2 全量测试

提交前必须运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```

要求 0 failures。若全量测试包含 GUI/Qt 测试，必须先单独审计并在隔离进程中以 headless 模式
运行，设置每文件超时，禁止让 Qt 事件循环占用当前交互终端。当前 Nanobot Server 测试目录未
检出 PyQt/PySide/`qtbot` 引用；若后续引入，必须单独分组。

### 8.3 并发与故障注入

必须覆盖：

- 两个有不同 grant 的并发 Run；
- 主 Bridge 与隔离定时任务 Bridge 并发；
- 工具线程在另一请求进入后仍读取原 ContextVar；
- Bridge start/stop 异常清理；
- structured error + exit code 0；
- Trace 写库暂时失败时工具仍返回；
- worker 在每个步骤前后崩溃；
- lease 过期后旧 owner 提交；
- outbox 已创建但 execution 未结算；
- 多文件编辑中途失败和恢复；
- 搜索超时、预算耗尽和 cursor 续扫。

### 8.4 真实 Sandbox

只在具备生产前置条件的宿主运行。证据必须包括命令时间、Git SHA、镜像 IMAGE ID、Profile
manifest hash 和每项结果。无凭据时保留为明确阻塞项，不能用普通 pytest 替代。

### 8.5 生产观察指标

上线后至少观察：

| 指标 | 目标 |
|---|---|
| `runtime_context_mismatch_total` | 0 |
| 结构化 `status=error` 与 `ToolCall.status=error` 一致率 | 100% |
| `duplicate_non_retryable_call_suppressed_total` | 可解释，且不再执行底层工具 |
| `tool_family_blocked_total` | 与授权类错误关联 |
| 普通工具出现在 reply contract retry | 0 |
| 跨 owner 定时任务访问成功 | 0 |
| 确定性任务模型调用数 | 0 |
| 调度扫描时长受单任务 600 秒阻塞 | 0 |
| 搜索截断但 `truncated=false` | 0 |
| Runtime 4 服务 IMAGE ID 不一致 | 0 |
| Agent Link 使用 API Token 回退 | 完成迁移后为 0 |

## 9. 发布、灰度与回滚

### 9.1 发布顺序

1. 先发布上下文隔离、Trace 和循环控制。
2. 观察并发 Run 与工具失败指标。
3. 发布定时任务 owner/identity 安全迁移。
4. 完成旧任务 owner 回填和越权负例验证。
5. 发布 Workspace 新合同及兼容别名。
6. 发布 workflow execution worker，先迁移旧 prompt 任务。
7. 启用确定性任务和复杂程序。
8. 切换独立 Agent Link Token。
9. 最后关闭凭据回退和旧工具别名。

### 9.2 灰度

- 上下文隔离先在测试和单 worker 环境压测，再进入生产；
- 定时任务 owner 迁移先 audit-only，再 enforce；
- Workspace 新 schema 先用于受控 Sandbox 会话；
- workflow worker 先只领取迁移后的 legacy model+emit 程序；
- 生产 Prompt Runtime 迁移按模板逐个 apply；
- Agent Link Token 双凭据兼容期内完成客户端切换。

### 9.3 回滚

- Runtime 回滚到最近 1 个已验证 IMAGE ID；
- 数据库新增列和表保留，不做破坏性 down migration；
- 新 workflow worker 可停止，旧 outbox worker继续投递已生成 payload；
- owner enforcement 出现非安全兼容问题时只能回到 audit-only，不能恢复全局无 ACL 操作；
- Prompt Runtime 从备份恢复；
- Workspace 新工具可关闭 exposure，旧内部兼容别名继续服务已知旧调用；
- Agent Link 独立 Token 切换失败时可在明确时间窗内恢复兼容回退，并记录安全告警；
- Sandbox 镜像未发生输入变化时不创建伪回滚镜像。

## 10. 风险与处置

| 风险 | 影响 | 处置 |
|---|---|---|
| ContextVar 未传播到工具线程 | 再次身份串线或 fail closed | 并发 barrier + `to_thread` 测试 |
| 唯一 Session key 泄漏 | 进程内存增长 | start/stop 异常清理与 registry 计数 |
| 错误信封解析失败 | Trace 再次误报 | Adapter 状态 + Trace 独立解析双层防线 |
| 工具族划分过宽 | 阻止无关能力 | 首期按前缀，后续用显式 capability scope |
| owner 回填错误 | 用户失去任务或越权 | 可审计迁移，无法证明时禁用 |
| 程序恢复重复副作用 | 重复外部操作 | 步骤幂等键、ambiguous 终态、人工确认 |
| 正则 ReDoS | sandboxd CPU 耗尽 | 超时正则、输入限制、总扫描预算 |
| 多文件编辑部分写入 | Workspace 不一致 | 事务日志、临时文件、恢复测试 |
| Prompt runtime 覆写遮蔽修复 | 线上仍运行旧合同 | audit/plan/apply/resolve/rollback |
| 独立 Token 切换中断客户端 | Agent Link 离线 | 兼容期、连接观测、分步关闭回退 |
| dirty 状态漏报 | 镜像身份不可信 | 干净 worktree + build context hash |
| 无 root 凭据却误报 Smoke | 发布证据失真 | `BLOCKED_NOT_RUN`，禁止健康状态替代 |

## 11. 完成标准

全部满足后，才能把本计划标记为完成：

1. 并发有授权/无授权 Run 的身份隔离测试稳定通过。
2. 生产代码不再读写 `session.extra["nanobot_runtime_context"]`。
3. 每个 Bridge 使用独立 KT Session，生命周期结束无泄漏。
4. 结构化业务错误在 KT Job、Trace 和 Runtime Event 中全部记录为失败。
5. 同参不可重试调用与授权失败同族调用在底层执行前被抑制。
6. `stop=true` 没有被实现为整个 Run 的无条件硬停止。
7. 回复合同重试只允许 `reply/no_reply`，并具有独立 Trace 来源。
8. `load_agent_config()` 实际得到 `max_iterations=12`。
9. Admin 不再把 `MAX_TOOL_ROUNDS` 宣传为 KT 主链路限制。
10. 定时任务具备 owner/creator/ACL，跨 owner 操作全部拒绝。
11. 定时任务使用真实会话主体解析历史、ToolPlan、Sandbox 和 Workspace。
12. 调度扫描不再同步等待最长 600 秒的 Agent 生成。
13. execution 和 step attempt 可以从 checkpoint 恢复。
14. 确定性任务无需模型即可完成并投递。
15. `workspace_read` 按行且不会切断 UTF-8。
16. `workspace_search` 支持正则、忽略大小写、gitignore、glob/tree 和显式续扫。
17. `workspace_edit` 支持精确替换、diff 和多文件批次，并保持安全边界。
18. 所有工具访问仍经过 Nanobot 授权和 sandboxd。
19. Prompt default、仓库 runtime、生产 runtime、Tool Schema 与 Golden 已同步。
20. Sandbox Admin 能显示原始值、有效值、来源和逐门判定。
21. 生产 Agent Link 使用独立 Token，不再回退复用 API Token。
22. Runtime 发布记录 Git SHA、build context hash、IMAGE ID/digest、部署时间和回滚身份。
23. Sandbox 影响矩阵能证明何时需要重建、重装和真实 Smoke。
24. 所有定向测试与 `python -m pytest tests/ -v` 为 0 failures。
25. 需要真实 Sandbox Smoke 的切片已有实际证据；未具备凭据的项没有被虚假标记为通过。

## 12. 已确定决策

以下问题不再重复讨论细枝末节：

- 不设工具调用总数上限。
- 顶层 `max_iterations=12` 只作请求级 LLM 迭代防死循环预算。
- `stop=true` 只停止当前失败能力的无效重试，不终止整个 Run。
- 工具失败以结构化信封为事实源。
- 最终回复合同最多重试 1 次，且只允许 `reply/no_reply`。
- Workspace 继续经由 sandboxd，不恢复 KT 宿主文件工具。
- 文件工具使用显式 `cwd`，不共享隐式 shell 当前目录。
- 一个 `workspace_search` 承载 grep/glob/tree，不继续增加相似搜索工具。
- 一个 `workspace_edit` 承载精确替换、diff 和批量编辑。
- 一个 `schedule_task` 管理统一任务结构，不新增大量任务类型工具。
- 简单任务默认不调用模型，模型只是程序中的可选步骤。
- 定时任务必须有 owner，不能再按全局 task ID 无隔离操作。
- Runtime 和 Sandbox 是两条独立发布控制面。
- 普通 Runtime 代码变化不触发无条件 Sandbox 镜像重建。
- 缺少真实 Smoke 凭据时明确报告未执行。
- Agent Link 最终使用独立凭据；兼容回退只用于迁移期。
- 不提交密钥、生产数据库、Prompt Runtime 挂载内容或 Workspace 数据。

## 13. 2026-07-29 本地执行记录

本节记录本轮实际落地结果，区分“仓库代码已实现”和“生产环境已验收”。当前工作位于隔离
worktree，基线仍为
`1d34f3c33aaee780c2b2ee1fc427e3d7a50c9c5d`。本轮没有 commit、push、Docker
构建、服务重启或生产数据写入。

### 13.1 阶段执行状态

| 阶段 | 本地状态 | 实际结果 |
|---|---|---|
| 0. 回归证据 | 完成 | 增加请求隔离、失败抑制、Workspace v2、任务 workflow、构建上下文和 Token 诊断回归 |
| 1. Agent Runtime | 完成 | 请求身份改为 `ContextVar`；每个 Bridge 使用独立 KT Session；结构化错误统一进入 KT Job/Trace；同参不可重试调用和工具族重复调用在执行前抑制；回复合同重试仅暴露 `reply/no_reply` |
| 2. 任务归属与身份 | 完成 | 新增 owner/creator/迁移状态；API 与工具按 owner 查询；定时执行使用真实会话主体；冻结快照和 Trace 关联补齐 |
| 3. Workspace v2 | 完成 | 行范围 UTF-8 读取；正则、忽略大小写、gitignore、glob/files/tree 搜索；显式 `cwd`；精确替换、unified diff、多文件原子编辑、事务恢复和内容 hash |
| 4. 统一任务程序 | 完成 | 新增 versioned program、execution、step attempt、租约和 fencing；支持 set/tool/model/branch/loop/wait/emit；确定性程序不调用模型；同 owner 串行、不同 owner 有界并行 |
| 5. 配置与发布事实源 | 代码完成 | Sandbox Admin 展示配置来源和逐门判定；Agent Link Token 回退产生脱敏诊断与告警；构建上下文 hash、未跟踪文件检查、四服务镜像身份和 `BLOCKED_NOT_RUN` 证据脚本已实现 |
| 6. Prompt、Schema、Admin、Golden | 完成 | canonical/runtime Prompt、工具 Schema、OpenAPI、管理端类型、行为 Golden、验证计划和决策清单已同步 |

### 13.2 Agent Runtime 与失败语义

本轮完成了以下收敛：

1. `core/agent_runtime/request_scope.py` 保存不可变请求身份，工具线程和异步子任务读取当前
   `ContextVar`，不再依赖可被并发覆盖的 KT `session.extra`。
2. 每个 `NanobotBridge` 使用随机独占的 `session_key`；正常停止和启动失败都会移除对应
   KT Session。
3. Bridge 新增的请求状态、Trace 收尾和确定性工具执行逻辑已拆分到
   `nanobot_kt/bridge_state.py` 与 `nanobot_kt/direct_tool_execution.py`。公开兼容导入和
   `execute_registered_tool()` 调用面保持不变。
4. `core/tool_execution_policy.py` 统一解析结构化失败、计算稳定调用指纹，并记录当前请求已
   失败的调用和能力族。`retryable=false` 或 `stop=true` 不终止整个 Run，只阻止已经证明
   无效的重复执行。
5. Sandbox 工具返回结构化业务错误时，KT Job、`tool_calls.status` 和 Runtime Trace 都记录
   为失败，不再因 `exit_code=0` 误报成功。
6. 回复合同重试使用独立 Trace 来源，并通过收窄后的 ToolPlan 只允许 `reply/no_reply`。
7. `max_iterations: 12` 已移到 KT 实际读取的配置顶层；管理端说明明确
   `MAX_TOOL_ROUNDS=5` 只属于 legacy adapter。

架构检查曾识别出两个门禁问题，本轮没有通过抬高阈值规避：

- `core/daily_digest.py` 不再直接导入 `nanobot_kt`。生产 workflow 回调通过
  `core/scheduled_workflow_runtime.py` 的框架无关绑定，由 `bootstrap/lifespan.py`
  Composition Root 注入。
- delivery worker 早于 Agent Runtime 启动时不会领取 execution；回调未绑定阶段返回空批次。
- `nanobot_kt/bridge.py` 从 3113 行降至 2720 行，低于现有 2759 行兼容上限。

### 13.3 定时任务

任务安全与执行可靠性已按统一结构实现：

- `scheduled_tasks` 增加 owner chat stream、platform、chat type、真实 session、creator、
  definition version、program 与迁移状态字段。
- 旧任务能证明目标归属时自动回填；无法证明 owner 或无法规范化 program 时
  `enabled=0` 失败关闭。
- program 迁移不会复用或覆盖 `delivery_status`、`last_error_summary`。这两个字段继续只表示
  历史投递 projection，避免把既有成功记录改写为迁移失败。
- `schedule_task` 的 list/get/update/enable/run/delete 均以当前 owner 为作用域；不存在仅凭
  全局 `task_id` 跨主体操作的路径。
- trigger 扫描只冻结 occurrence 并创建 execution，不再同步等待最长 600 秒的 Agent。
- execution worker 使用租约、fencing token、owner mutex、稳定步骤幂等键和持久
  checkpoint。worker 崩溃后，安全步骤可恢复；无法判断外部副作用是否已发生时进入
  `ambiguous`，不盲目重放。
- `wait` 保存唤醒 checkpoint；循环和嵌套步骤继续受静态及运行时预算约束。
- 只有 `model` 步骤调用 LLM；`tool` 直接在真实 ToolPlan、请求身份和 Trace 边界内调用注册
  工具；`emit` 复用现有 immutable outbox。
- 同 owner 的 execution 串行领取，不同 owner 可以在配置的有界并发内同时运行。
- 模型 Trace 只由 `model` 步骤写入并传递到 `emit`；普通工具 Trace 不再冒充模型 Trace。
- canonical Prompt、仓库 runtime Prompt 和旧 Skill 均已升级到同一版统一任务合同。

### 13.4 Workspace

安全文件访问路径仍为：

```text
模型工具 -> Nanobot ToolPlan/授权 -> sandboxd -> 当前 owner Workspace
```

没有恢复 KT 的宿主 `read/edit/grep/glob/bash`，也没有放宽 Docker、宿主路径或跨 owner
边界。具体合同变化如下：

- `workspace_read.offset/limit` 改为从 0 开始的行偏移和行数；返回行号、总行数、`eof` 与
  `next_offset`，不会在 UTF-8 中文字符中间切片。
- `workspace_search` 统一承载 content/files/tree 三种模式，支持有时限的正则、
  `ignore_case`、gitignore、glob、显式续扫与总扫描预算。超过 1 MiB 的文件不再静默漏掉
  后半部分命中。
- 所有文件工具支持显式 Workspace 相对 `cwd`；它不会继承前一次
  `sandbox_exec` 的 shell `cd` 状态。
- `workspace_edit` 同时支持严格 old/new/replace_all、unified diff 和多文件批次。所有目标
  先预检，再通过临时文件、事务日志和恢复流程原子结算；返回替换次数及前后 SHA-256。
- 旧 `workspace_apply_patch` 保留为兼容入口，但 Prompt 引导统一使用
  `workspace_edit`。

### 13.5 配置、凭据与发布

已完成的代码能力：

- Sandbox Admin 状态区分环境原始值、数据库覆盖后的有效值、值来源，以及业务总开关、
  exec、Developer 网络上限、群开关、session grant、Profile、Workspace 与配额等逐门结果。
- Agent Link Token 诊断只返回“是否配置、来源、是否回退”，从不返回密钥正文；回退到
  `NANOBOT_API_TOKEN` 时每进程告警一次。
- 文档和 `.env.example` 明确要求生产使用独立 `NANOBOT_AGENT_LINK_TOKEN`，不把 API Token
  复用当作最终配置。
- `.dockerignore` 同时排除大小写不同的 `.codex/` 与 `.Codex/`，并继续排除测试、文档、
  数据、工作区、资产、运行时缓存和密钥。
- `scripts/build_context_manifest.py` 对实际 Docker build context 中的每个文件记录路径、
  mode、大小和 SHA-256，生成整体 build-context hash，并列出仍会进入上下文的未跟踪文件。
- `scripts/docker-build.sh --production` 会拒绝实际构建上下文中的已修改或未跟踪文件，不再
  使用 `--untracked-files=no` 产生“干净镜像”假象。
- Runtime 镜像写入 Git revision 和 build-context hash；发布证据脚本校验四个 Runtime
  服务使用同一 IMAGE ID，并记录回滚镜像。
- 无法执行真实服务 Smoke 时，证据固定为 `BLOCKED_NOT_RUN`，不会用容器 healthy 代替。
- Release workflow 继续只构建 Runtime；Sandbox Restricted、Developer 和 egress-proxy
  镜像由独立影响矩阵决定是否重建。

本轮在当前未提交工作区实际运行 build-context manifest，成功扫描 1861 个有效上下文文件，
总大小 19,609,723 字节，并识别出 13 个会进入构建上下文的本轮未跟踪源码文件。因此生产模式
在 commit 前拒绝构建是预期且正确的结果；该次 dirty-context hash 不是可发布镜像身份。

### 13.6 最终验证证据

静态和生成物检查：

```text
python scripts/check_architecture.py
  -> 架构边界检查通过

python scripts/build_release_impact.py --check-golden
python scripts/build_verification_plan.py --check-golden
python scripts/audit_decision_rules.py --check
python scripts/build_behavior_baseline.py --check
python scripts/generate_openapi_client.py --check
  -> 全部退出码 0

python -m ruff check api app bootstrap clients core creatures nanobot_kt \
  sandboxd scripts tests workers config.py server.py
python -m ruff check <本轮修改的 KT vendor 文件>
  -> All checks passed
```

Qt 审计：

- `tests/` 共 355 个 `test_*.py` 文件；
- 未检出 PySide、PyQt、pytest-qt、`qtbot`、`QApplication`、`QCoreApplication`、
  `QWidget`、`QThread`、`QTimer`、Qt/GUI pytest marker 或 `QT_QPA_PLATFORM`；
- 因此没有在当前终端启动 Qt 事件循环，也没有为了回避 Qt 而缩减测试覆盖面。

清除全部代理变量后的最终完整测试：

```text
python -m pytest tests/ -q
  -> 6231 passed, 12 skipped, 0 failed
  -> 125.46 秒
```

12 个 skip 均来自仓库既有条件化测试。11203 条 warning 主要是 Python 3.12 下 SQLite 默认
datetime adapter 的弃用告警，另有 1 条 Starlette/httpx 兼容弃用告警；没有把 warning
静默当作测试失败，也没有在本轮扩大范围处理这些相邻问题。

### 13.7 尚未执行的生产验收

以下项目需要生产权限、受保护凭据或用户对外部状态变更的明确授权，本轮没有执行：

1. 未 commit、push 或创建 PR。
2. 未构建新的 Runtime 或 Sandbox 镜像，未更新 tag、digest 或回滚镜像。
3. 未重启或替换服务器上的四个 Runtime 服务。
4. 未在生产数据库执行 owner/program migration，也未改变任何线上任务。
5. 未迁移生产挂载目录中的 Prompt Runtime override。
6. 未实际配置独立 `NANOBOT_AGENT_LINK_TOKEN`；生产在完成运维切换前仍可能使用兼容回退。
7. 未执行聊天接口、Agent Link roundtrip、任务 worker 和生产 Prompt hash 的部署后 Smoke。
8. 未执行需要 root 保护凭据的真实 Sandbox 隔离 Smoke；状态必须保持
   `BLOCKED_NOT_RUN`。
9. 未进行灰度流量观察，因此第 8.5 节的生产指标尚无上线后数据。

所以，本轮可以判定为“仓库代码实施和本地回归完成”，不能判定为“生产发布完成”或“完整计划
全部验收完成”。进入生产阶段前，仍需先形成干净 commit，再按第 9 节顺序执行迁移、构建、
部署、真实 Smoke、凭据切换和灰度观察。
