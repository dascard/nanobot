# Agent Harness 生态最终取舍矩阵与 Nanobot 现状映射

> 状态：阶段 0.5 完成
>
> 核验日期：2026-08-03
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md`
>
> 范围：Wave A～E 的全部高星项目、来源项目、正式协议、记忆／评测项目和小型 Harness
>
> 文档边界：这是后续实施依据，不是 README 能力声明；本文没有复制第三方源码，也没有授权提交、部署或修改生产数据。

## 1. 最终结论

完整源码核验与当前仓库映射后，结论不是“选择一个外部 Harness 替换 KT”，而是：

1. **保留 Nanobot 自有业务语义和框架无关 Port。** 外部项目只提供可独立验证的模式；KT、Native Runtime、ACP 和 A2A 都只能是 Adapter，不能成为 owner、历史、权限、Prompt、任务或 Sandbox 的事实源。
2. **先复用当前仓库已经成熟的底座。** Registry Kernel、Module Manifest、Prompt Runtime、Tool Registration／ToolPlan、Memory Provider、任务租约、Outbound Outbox、Sandbox／Workspace／Asset、模型路由、Hook／Policy、Telemetry 和 Eval 均已有真实实现，后续不得再造同名第二套系统。
3. **当前最大缺口是“权威运行语义”，不是“更多工具”。** `AgentRuntimePort` 仍缺统一 Run／Turn／Stream／Capability 合同；现有 `RuntimeEvent` 是脱敏、失败开放的 Telemetry，尚不是含提交边界的不可变 Run Event Ledger；通用 Permission／Approval 也尚未建立。
4. **KT 升级与 KT 解耦是两件事。** 当前子模块固定于 `v1.3.0`，仍通过补丁、私有成员、monkey patch、源码安装和 CI 子模块形成硬耦合。升级只能用来删除旧兼容写法；Native Runtime、optional Adapter 和构建拆分才负责解除硬依赖。
5. **KT 许可证本身提高了解耦优先级。** 当前 `KohakuTerrarium License 1.0` 基于 Apache-2.0 但增加产品／服务命名和可见署名要求，不能按普通 Apache-2.0 处理。后续目标版本必须重新核验许可证；改成 optional dependency 也不自动消除实际部署时的许可证义务。
6. **Sandbox 路线不需要重新选型。** 仓库已经实现独立 `sandboxd`、UDS 鉴权、owner workspace、不可变 Asset、租约、资源与网络策略、真实 Docker smoke 入口和回收治理。外部宿主 shell、E2B、OpenSandbox、OpenShell 与进程内 Docker 控制全部排除；剩余工作是部署实测和与统一 Run／Permission 的关联。
7. **Context 与缓存不是空白。** 当前已有 rolling summary、低／高水位、prefix epoch、cache shape 和 miss reason。后续增量是统一的 compaction decision、Tool Result Envelope、Artifact 引用和质量 benchmark，不是另建 Context Store。
8. **主动外呼与长期任务也不是空白。** Outbound 已有 claim、lease、fencing、attempt、outbox、ambiguous、replay 和 circuit；ScheduledTask、Job Kernel、Task Runtime 也有稳定合同。后续只抽取公共 Run 原语并接入 Event Ledger，不复制 DeerFlow、EverOS 或 Commonly 的任务实现。
9. **Skill、MCP 和协议必须后置。** Agent Skills／AGENTS.md／MCP／ACP／A2A 只有在 Runtime Event、Permission、Artifact、Workspace 和 capability negotiation 稳定后才能接入；否则会产生第二事实源或绕过现有安全边界。
10. **多 Agent 与自进化只做有界实验。** 首版最多单层固定角色、有限任务、有限并发、单调权限衰减和独立 verifier。自进化只能生成隔离候选，经固定数据、独立评测、人工批准和灰度后发布，禁止自动修改、批准、提交或推送主干。

因此，后续实现的真实优先顺序是：

```text
冻结行为基线
  → 补齐 Runtime Run／Stream／Capability 合同
  → 核验并升级 KT，删除私有兼容写法
  → 实验 Native Runtime，拆除默认 KT 构建依赖
  → 建立权威 Run Event／Permission／Checkpoint 合同
  → 在现有 Sandbox、Task、Outbox、Context、Registry 上做增量接入
  → 最后试验 Skill／MCP／ACP／A2A／多 Agent／受控进化
```

## 2. 证据与决策口径

### 2.1 证据来源

本文不重复粘贴外部源码，所有外部结论均回指以下固定版本核验：

- `2026-08-03-source-verification-wave-a.md`：Runtime、事件、租约、权限、DAG 和 Provider；
- `2026-08-03-source-verification-wave-b.md`：平台、身份、记忆、Workspace、主动能力和进化；
- `2026-08-03-source-verification-wave-c-protocols.md`：OpenClaw、Bub/Tape、Agent Skills、AGENTS.md、ACP 和 A2A；
- `2026-08-03-source-verification-wave-d-memory-evaluation.md`：EverOS、EverAlgo、HyperMem、LoCoMo、EverMemBench 和 EvoAgentBench；
- `2026-08-03-source-verification-wave-e-small-harnesses.md`：Jeju、Waveloom、DSCode、Penguin Harness、Agent OS Harness 和 SEAJelly。

每份文件均记录了固定 commit／revision、许可证、源码路径、测试或数据核验以及 README 偏差。本文的“直接采用”始终表示**独立实现模式**，不表示复制第三方代码。

### 2.2 五类决策

| 决策 | 含义 | 进入稳定合同的条件 |
| --- | --- | --- |
| 直接采用模式 | 机制与本项目目标一致，已有 A 级源码／协议证据 | 仍需按 Nanobot 自有类型实现并通过本仓测试 |
| 兼容适配 | 外部语义有价值，但必须投影到现有事实源或只作为边界 Adapter | Adapter 可关闭、不可旁路 Core、不持有第二份权威状态 |
| 实验 | 可能有收益，但复杂度、真实收益或安全性尚未由本项目证明 | feature flag、shadow／离线评测、明确预算和退出条件 |
| 观察 | 只有弱证据、草案或当前没有真实需求 | 不冻结接口、不写 README、不进入默认依赖 |
| 排除 | 与安全、正确性、许可证或项目边界冲突 | 不实施；仅可转化为反例测试 |

### 2.3 侵入性、风险与测试成本

| 代码 | 含义 |
| --- | --- |
| `I1` | 单模块增量，无持久化迁移 |
| `I2` | 跨一个稳定 Port／Adapter，需要定向回归 |
| `I3` | 跨入口、数据库或运行时，需要灰度和迁移 |
| `I4` | 基础架构或事实源变化，需要双写／投影修复／回滚演练 |
| `R1` | 低风险，可直接回退到原路径 |
| `R2` | 中风险，可能产生兼容或性能回归 |
| `R3` | 高风险，涉及并发、恢复、权限或副作用 |
| `R4` | 极高风险，错误会导致越权、重复副作用或事实丢失 |
| `T1` | 纯单元／静态合同测试 |
| `T2` | 组件与 Fake Adapter 集成测试 |
| `T3` | 多入口、故障注入、数据库迁移或真实 Provider 测试 |
| `T4` | 真实 Docker／多进程／长任务／生产式灰度验证 |

### 2.4 许可证处理代码

| 代码 | 处理 |
| --- | --- |
| `L0` | 正式开放协议或本项目独立合同；仍记录版本和署名 |
| `L1` | MIT／Apache-2.0 等宽松来源；默认仍只借鉴模式 |
| `L2` | MPL、BUSL、Community License、修改版 Apache、PolyForm、CC BY-NC、定制 KT License 或带品牌 notice；禁止未经专项审查复制 |
| `L3` | 无许可证、许可证文本冲突或来源不完整；只允许独立研究，不复制代码／fixture |

## 3. 当前仓库能力地图

本节以 2026-08-03 当前工作树为准。它覆盖已经存在但旧调研文档撰写时可能尚未纳入的实现，因而是“是否重复建设”的最终判断依据。

| 能力 | 当前源码事实 | 后续处理 |
| --- | --- | --- |
| Registry Kernel | `core/registry/` 已提供排序、重复拒绝、依赖校验、generation、canonical JSON 和 SHA256 的不可变快照 | 全部新目录复用；禁止再建独立 Tool／Skill／Manifest hash 框架 |
| 模块级 Manifest | `core/modules/contracts.py` 和 `core/modules/composition.py` 已有 `ModuleManifest`、贡献声明、依赖、能力唯一所有者和显式生命周期 | 保留为代码模块 Composition；Agent Manifest 复用 Kernel，但不能把二者混成一个模型 |
| Agent Runtime Port | `core/agent_runtime/contracts.py` 是标准库-only 合同，已有 principal、features、plan refs、conversation、model route、interrupt 和 Fake Runtime | 增量加入 Run／Turn／Stream／Event／Capabilities；不另建 Runtime API |
| KT Adapter | `nanobot_kt/runtime_adapter.py` 已集中大部分 KT 映射，但仍 fallback 到 `_messages`、`_pending_events`、`_event_queue`、`_pending_injections`、私有 LLM client 等 | 升级后逐项删除；所有 fallback 必须有删除条件 |
| KT 构建依赖 | `.gitmodules`、`requirements.txt`、`requirements-prod.lock`、`Dockerfile`、CI 和 release workflow 仍要求本地子模块；还有 stream patch | Native Runtime 稳定后拆成 optional wheel／extra 或可选镜像层，并删除 submodule／patch |
| Prompt Runtime | `core/prompt_v2/` 已有模板注册、变量、section descriptor、compiler、flow、audit、迁移和运行时模板 | Agent Manifest 只引用其不可变快照；禁止引入第二套 Prompt builder |
| Tool Registry／Plan | `core/tool_registration.py`、`core/tool_plan.py` 已有冻结 Registration、稳定 schema 排序、执行绑定、Prompt keys、生命周期、请求级 SHA 和动态工具冲突拒绝 | 增补 namespace／provider／trust／permission／sandbox 和持久 session snapshot；不重做注册表 |
| Tool 执行控制 | `core/tool_execution_policy.py` 已有调用指纹、重复失败抑制、工具族阻断、最终动作阶段；实际 KT 拦截仍在 Adapter | 提取真正 `ToolExecutionPort`，让 Native 与 KT 共用执行合同 |
| Runtime Event | `core/runtime/events.py`、`event_registry.py` 已有类型化白名单、Registry snapshot、provenance、sink 和 emitter | 保留为事件描述与 Telemetry 基础；权威 Ledger 需要独立提交语义 |
| Telemetry Store | `RuntimeTelemetryEvent` 与 `SqlAlchemyRuntimeEventSink` 已做脱敏、幂等、短事务；`emit_runtime_event()` 明确 fail-open | 继续作为无正文观测投影，不能直接改名冒充 authoritative Run Ledger |
| Hook／Policy | `core/runtime/extensions.py` 已有冻结 Observer／Transform／Policy、稳定顺序、失败策略和受保护不变量 | 增补明确的 model／tool／completion 接入点和超时；不建第二个 Hook Registry |
| Job Kernel | `core/jobs/` 已有 job status、attempt、lease token、generation、heartbeat、settle 和 expired recovery | 抽取通用 ownership 原语；不复制外部文件队列 |
| Task Runtime | `core/task_runtime/` 已有 typed failure、deadline、idempotency、预算、模型 Port、retry／SLO／circuit 合同 | 复用确定性任务执行；Agent Run 不另起冲突的重试语义 |
| Scheduled Workflow | `core/db/models/scheduling.py` 已有版本化 program、execution snapshot、lease、step attempt、checkpoint、blocked／ambiguous | 复用 side-effect boundary 与恢复测试；不重建 scheduler |
| Outbound | `core/outbound/` 与 `core/db/models/outbound.py` 已有 run claim、writer fence、generation attempt、不可变 payload outbox、delivery attempt、circuit、replay 和 cutover | 作为 terminal receipt／delivery 的现成底座，接入 Run Event，不替换 |
| Sandbox／Workspace／Asset | `core/sandbox/`、`sandboxd/`、`docker/sandbox/`、部署脚本和测试已覆盖 UDS、owner、ACL、quota、lease、process、network、immutable asset、patch journal、Docker 安全参数和 reconcile | 只补统一 Permission／Run 关联并执行真实宿主验证；排除外部 Sandbox 平台 |
| Workspace 编辑 | `sandboxd/filesystem.py` 与 `unified_patch.py` 已有严格 diff、expected SHA、批量预校验、事务 journal 和 partial recovery 测试 | 已覆盖 DSCode/Waveloom 的主要可取模式；只把 outcome／receipt 投影到 Run Event |
| Memory Provider | `core/memory_provider/` 已有框架无关 capability、依赖、失败策略、prefetch、sync、tool、session、compaction 和 delegation 生命周期 | 不再创建新 Memory Port；补 owner/provenance/revision 与统一事件 |
| Session Context | `core/context_builder.py`、`app/session_memory/` 与 session models 已有 rolling summary、source watermarks、write fence、history clear、低／高水位和最近原文 | 增补统一 compaction decision 和跨来源预算，不替换 `ChatLog`／`ConversationTurn` |
| Cache Shape | `foundation/llm/cache_shape.py` 与 `core/tracing.py` 已记录 prefix epoch、system／tools／history hash、cache usage 和 miss reason | 先做真实 benchmark 和阈值调优；不采信外部缓存 headline |
| Model Provider | `core/model_provider/`、`clients/model_registry.py`、`clients/new_api_client.py` 已有 descriptor、route、能力过滤、成本排序、持久模型级熔断和健康探测 | Provider Doctor 增量扩展；账号／key failure scope 合并进现有 tracker，不另建路由器 |
| Agent Link | `core/agent_link/` 已有 v1 信封、版本校验、动态工具快照、冲突拒绝、幂等 submit、cancel、重连终态重发和请求限额 | 保持自有协议；其终态缓存仍是进程内视图，后续投影 Run Ledger；ACP/A2A 不替换它 |
| Eval | `evals/`、`core/eval_sampling/`、Admin／WebUI 已有 deterministic suite、baseline gate、候选标注、周期 manifest、趋势和 record-only review | 扩展统一 Run Manifest、运行分母 invariant、Runtime parity、Context／Memory／Permission 评测，不另建 Eval 平台 |
| 主动外呼 | `core/proactive/`、`core/outbound/` 已经接近 `Evaluate → Lease → Generate → Outbox → Deliver`，含 cooldown、幂等、ambiguous 和运营面 | 与通用 Trigger／Run Event 对齐；不复制 Raven Sentinel 数据库 |
| Skill | `creatures/nanobot/prompts/skills/` 主要是 KT creature 资源，只有部分 `SKILL.md`，没有独立多作用域、版本化、可安装 Registry | 在 Runtime／Permission 稳定后实现 Agent Skills 兼容 Provider，并逐步移出 KT 目录 |
| MCP | 非 vendor 业务代码没有独立 MCP Provider；当前能力主要来自 KT vendor | 后续实现可选 `McpProviderPort`，默认命名空间和冲突失败关闭；先不暴露生产配置 |
| Permission | ToolPlan 是“可见／可执行工具集合”，Sandbox 有专用 session grant，但没有统一 `allow/deny/ask/allow-once` 的 `PermissionPort` | 作为高优先级新合同；不得把 ToolPlan 或 Prompt 审批误称通用 Permission |
| Native Runtime | 尚无 `NativeAgentRuntime`；`core/agent_step.py` 和 ChatCompletion Port 提供了部分基础，但不是完整有界 Agent Loop | 在 KT 行为基线和 Runtime 合同后做 feature-flag 实验 |
| 多 Agent | 除 KT 内部 Subagent／memory subagent 外，没有 Nanobot 自有、可恢复、权限衰减的 DAG Runtime | 只做阶段 8 的深度 1 实验，不提前开放递归 |

## 4. 全量能力取舍矩阵

### 4.1 Runtime、Manifest 与 KT

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R01 | 最小 Runtime Adapter 与显式 capability negotiation（OpenClaw、ACP、A2A） | 直接采用模式 | 扩展现有 `AgentRuntimePort`；当前 `capabilities` 只是请求集合，没有 Runtime support descriptor | 让 KT／Native／远程 Adapter 可比较、可失败关闭 | `I2/R2/T2` | `L0/L1` | 保留旧 `execute_turn()` 兼容 façade，移除未被第二个 Runtime 使用的能力位 |
| R02 | 类型化 Run／Turn／Stream 事件（Maka、Qwen Code、Penguin） | 直接采用模式 | 当前只有 `execute_turn(stream: bool)` 和 raw result；补 `run()`／`run_stream()` 等价能力 | 消除按文本和 KT buffer 推断流式状态 | `I3/R3/T3` | `L1` | feature flag 回退旧桥，事件合同保持可向后解析 |
| R03 | Agent Manifest + compiler + immutable bundle（Jeju、LobeHub、Yao、Commonly） | 直接采用模式 | 复用 Registry Kernel、`ModuleManifest`、Prompt／Tool／Model snapshots；新增的是“Agent 部署输入”，不是模块 Manifest | 同一 Agent 可切换 Runtime，能力缺失在运行前暴露 | `I3/R3/T3` | `L1/L2`，只独立实现 | 编译失败继续使用现有显式 composition；未被消费字段删除而非永久保留 |
| R04 | Runtime 准备好的 attempt，Adapter 只执行（OpenClaw） | 直接采用模式 | 把 Prompt、ToolPlan、route、identity、budget、permission snapshot 在 Core 冻结后交给 Adapter | 防止 KT／协议 Adapter 重选模型或改权限 | `I3/R3/T3` | `L1` | 逐入口灰度；Adapter 缺能力时显式 fail-fast |
| R05 | KT 稳定版升级与公开 API 改写 | 兼容适配 | 当前为 `v1.3.0` + patch + 私有 fallback；先核验更高正式 tag，再逐项替换 | 删除 `_process_event`、conversation／queue／provider 私有访问和 monkey patch | `I3/R4/T3` | 当前 KT 为 `L2` 定制许可证；目标版重核 | 固定旧 optional wheel／镜像用于短期回滚；不长期维护双 patch 队列 |
| R06 | NativeAgentRuntime 有界模型—工具循环 | 实验 | 复用 ChatCompletion Port、ToolPlan、Prompt Runtime、ToolExecution Port；当前不存在完整实现 | 解除 KT 默认依赖并缩小主链复杂度 | `I4/R4/T4` | 本项目自有 `L0` | parity／灰度不达标即关闭 feature flag，KT Adapter 仍可选 |
| R07 | 双 Runtime 安全切换 | 实验 | Composition Root 已有模块能力，缺 per-request runtime selector、side-effect fence 与切换事件 | 支持渐进迁移和故障隔离 | `I4/R4/T4` | `L0` | 有副作用 turn 禁止自动跨 Runtime 重试；灰度可一键归零 |
| R08 | OpenClaw 大型实验 Harness 合同 | 排除 | 字段过多且泄漏其内部 Runner 类型，当前没有 Nanobot 消费方 | 无直接收益 | 不实施 | `L1` | 只有出现两个真实 Runtime 的共同需求才逐字段重评 |
| R09 | KT 作为 submodule／默认源码依赖 | 排除为目标状态 | 当前仍存在，属于待删除兼容状态 | 降低构建、许可证、镜像和升级耦合 | `I4/R3/T4` | `L2` | KT 改 optional extra／wheel；无 KT 时主链和非 Agent 服务必须可运行 |

### 4.2 Run Event、恢复、任务与 Artifact

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E01 | append-only Run Event 事实与 projection 分离（Maka、DeepChat、Bub） | 直接采用模式 | 复用事件 Descriptor／Registry；新建权威 Store 和提交规则，现有 Telemetry 保持观测投影 | 统一恢复、Viewer、审计和协议投影 | `I4/R4/T4` | `L1` | 先 shadow 双写；Ledger 不参与控制前可停写并保留旧业务表 |
| E02 | terminal fact → receipt/outbox → header projection（Maka、DeerFlow） | 直接采用模式 | 复用 Outbound Outbox、Scheduled attempt 和 existing transaction patterns；缺通用 terminal barrier | 避免“已发送但 Run 未完成”或重复发送 | `I4/R4/T4` | `L1` | 按入口切换；失败时回到原业务事务，不删除已写事实 |
| E03 | operation id + payload hash + generation + receipt（Orca、Maka、A2A 反例） | 直接采用模式 | Outbound、Scheduled、Sandbox 已有局部实现；统一命名和事件引用 | 安全恢复外部副作用 | `I3/R4/T4` | `L1` | 无 receipt 的旧工具永久标记 non-resumable，不自动补造结果 |
| E04 | 通用 Run lease／heartbeat／fencing／conditional takeover（DeerFlow） | 兼容适配 | 从 Job、Scheduled、Outbound、Sandbox 的成熟实现抽合同，不新建独立 scheduler | 单 owner、可接管、旧 worker 失权 | `I3/R4/T4` | `L1` | 各业务表继续保留专用字段；公共抽象无复用价值时只共享测试／语义 |
| E05 | Checkpoint／Resume／Fork／Rewind | 实验 | 首期只支持 Turn、计划和有 receipt 的安全边界；Workspace checkpoint 与业务 checkpoint 分层 | 长任务恢复与分支比较 | `I4/R4/T4` | `L1` | 不满足版本、权限、文件 hash 或副作用证明就停在 `ambiguous` |
| E06 | validated patch、hash conflict、batch partial outcome（DSCode、Waveloom） | 直接采用模式（已基本实现） | `sandboxd/filesystem.py` 已有批量预校验、journal、hash 和 partial recovery | 防止覆盖用户后续修改 | `I1/R2/T2` | `L1` | 仅补 Run receipt；不再造第三套文件 checkpoint |
| E07 | owner-scoped immutable Artifact（LobsterAI、A2A、Maka） | 兼容适配 | 复用 Sandbox `Asset`／`WorkspaceAsset` 和 `asset://sha256/`；不要复用含义不同的 release artifact | 大结果脱离消息正文，跨会话安全引用 | `I2/R3/T3` | `L1` | 若通用 `ArtifactPort` 过宽，保留 Asset Port 并做薄别名 Adapter |
| E08 | Durable Task 统一入口 | 兼容适配 | 复用 Job Kernel、Task Runtime、Scheduled、Outbound；统一 correlation／Run refs，不统一所有业务 schema | 降低恢复语义漂移 | `I3/R3/T3` | `L1` | 保留领域状态机；公共层只承载确实共同的 lease／result contract |
| E09 | 持久 pending input／approval admission（DeepChat、ACP） | 实验 | 当前 Agent Link 终态与工具 pending 主要在进程内；需先有 Permission 和 Ledger | 断线后继续人机协作 | `I3/R4/T4` | `L0/L1` | TTL 到期进入 cancelled／ambiguous；不恢复无法证明的交互 |
| E10 | JSONL／WebSocket ring／日志文本作为权威账本（DeerFlow、Bub、Penguin、Jeju） | 排除 | 可保留导出／调试用途，不承担多 worker 正确性 | 避免截断、争用和提交歧义 | 不实施 | 来源多为 `L1`，但实现不采用 | 任何调试 sink 可删除，不影响数据库事实 |
| E11 | EverOS OME／Commonly 当前 Task 作为生产 durable task | 排除 | 缺原子重入队或 lease／fence | 避免永久 claim 和重复执行 | 不实施 | `L1/L2` | 只保留反例故障测试 |
| E12 | semantic resume 宣称 bit-exact replay | 排除 | 外部系统、时间、模型和副作用无法逐字节复现 | 避免错误承诺 | 不实施 | `L1` | Viewer 明确区分 projection replay、semantic replay 和真实再执行 |

### 4.3 Context、缓存与记忆

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | 原始事实／工作记忆／模型 Context 分层（Maka、Bub、EverOS） | 直接采用模式（现有原则） | 保持 `ChatLog`、`ConversationTurn`、Rolling Summary 和 Prompt plan 分离 | 防止压缩或清理破坏档案 | `I1/R1/T2` | `L1` | 架构门禁阻止新代码把三者合表 |
| C02 | stable prefix、固定工具顺序与 cache shape（Reasonix、Qwen Code、Waveloom） | 直接采用模式（已有大部） | 已有 Registry 排序、prefix epoch、cache hash 和 miss reason；缺真实 Provider 分层收益基准 | 降低延迟和成本，定位漂移 | `I2/R2/T3` | `L1` | 若 Provider 无稳定缓存收益，仅保留诊断，不改变 canonical 顺序 |
| C03 | notice → prune/snip → summary → hard limit 决策状态机（Reasonix、Waveloom） | 直接采用模式 | 复用现有 rolling summary 低／高水位；新增版本化 decision、原因、输入范围、token 前后和 artifact refs | 压缩可解释、可回放和可评测 | `I3/R3/T3` | `L1` | 阈值均可配置回滚；旧 summary 仍可读取 |
| C04 | assistant tool call／tool result 配对和事件／消息双顺序（Penguin） | 直接采用模式 | 纳入 Runtime Event 与 Context projector；当前 KT conversation inspection 是兼容实现 | 并行工具完成时仍保持模型协议合法 | `I2/R3/T3` | `L1` | 无法证明配对时停止下一轮模型调用，不猜测修复 |
| C05 | Tool Result Envelope：Unicode 规范化、边界、标记、截断、Artifact（Waveloom） | 直接采用模式 | 复用 Sandbox asset 和 tracing redaction；新增 canonical envelope，不能宣称消除 prompt injection | 控制上下文体积并保留来源 | `I3/R3/T3` | `L1` | 可关闭内容风险标注，但不能关闭大小和权限边界 |
| C06 | Session Goal／Plan Mode | 实验 | 当前已有计划文件与任务合同，但没有服务端只读 Plan Mode 权限状态机 | 长任务目标、预算和批准更清晰 | `I3/R3/T3` | `L1` | 未证明收益时保留普通单 Agent；Plan Mode 不获得写工具 |
| C07 | typed memory、owner scope、source evidence、revision、invalidation（EverOS、LobeHub、Commonly） | 直接采用模式 | 在现有 Persona、Digest、Group Memory、Semantic、MemoryProvider 上增量补字段／合同 | 可解释检索、冲突和删除 | `I3/R3/T3` | `L1/L2`，独立实现 | 按记忆类型逐表迁移；无消费方的类型不创建 |
| C08 | persistence-free Memory operator 边界（EverAlgo） | 兼容适配 | 映射到现有 MemoryProvider／Task Runtime；记录 input hash、Prompt、model、output 与幂等责任 | 提取／排序算法可测试、可替换 | `I2/R2/T2` | `L3` 冲突，禁止复制 | Provider 无收益即可卸载，不迁移档案事实 |
| C09 | 有界 Curator／第二轮 agentic retrieval（Raven、EverAlgo） | 实验 | 在简单 hybrid baseline 后 shadow，固定轮数和 token | 可能提升复杂中文问题召回 | `I2/R3/T3` | `L1/L3` | 任一质量／成本门禁不达标即关闭，不改变基础索引 |
| C10 | Hypergraph、cluster、reflection（HyperMem） | 观察 | 当前没有超过现有 Semantic／RAG 的本项目证据 | 保留研究可能性 | 不实施 | `L1`，分数为 C 级 | 只有真实中文数据证明收益才另立实验 |
| C11 | 向量／BM25／graph 成为唯一事实源 | 排除 | 派生索引必须可重建并有 watermark | 避免索引损坏导致事实丢失 | 不实施 | 与许可证无关 | 永久保留原始来源引用 |
| C12 | 外部缓存命中、成本和记忆 headline 数字 | 观察 | Reasonix／Waveloom／Penguin／HyperMem 缺完整原始运行账本 | 避免错误采购和 README 声明 | 不实施 | 来源各异 | 只接受 Nanobot 自己的固定 Run Manifest 与账单证据 |

### 4.4 Tool、Skill、MCP、Hook 与协议

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| X01 | Tool namespace／provider／version／trust／permission／sandbox snapshot（Reasonix、Jeju、SEAJelly 反例） | 直接采用模式 | 扩展现有 Tool Registration 和 ToolPlan；当前已有 SHA、generation、排序和冲突拒绝 | 动态工具不可静默覆盖，Prompt 与 executor 同源 | `I2/R3/T3` | `L1` | 新字段有默认迁移；外部 provider 可整体停用 |
| X02 | `SKILL.md` canonical format 与渐进披露（Agent Skills） | 直接采用模式 | 新建 `SkillProviderPort`，复用 Registry、Prompt Runtime、Workspace path guard 和 Artifact hash | 可移植 Skill，不再绑定 KT creature | `I3/R3/T3` | 代码 Apache-2.0、文档 CC-BY-4.0，`L1/L2` | 首期只读内置／项目 Skill；关闭 provider 不影响基础工具 |
| X03 | Skill 多作用域发现、pin／lock／rollback | 兼容适配 | scope 为 builtin/project/agent/user，必须显式优先级和诊断 | 可治理升级与来源漂移 | `I3/R3/T3` | `L1/L2` | 不提供自动安装；版本回退到上一内容 hash |
| X04 | `allowed-tools` 自动授权 | 排除 | 只解释为 capability request，再与 ToolPlan／Permission／Sandbox 求交 | 防止 Skill 自授予权限 | 不实施 | `L0` | Parser 保留字段但不直接生效 |
| X05 | AGENTS.md 最近文件优先的嵌套发现 | 兼容适配 | 与 Skill 分开扫描；复用 Workspace realpath／no-follow／size limit | 项目指令兼容主流工具 | `I2/R3/T3` | `L0/L1` | 可关闭项目指令；错误返回诊断而非静默忽略 |
| X06 | AGENTS.md 社区 v1.1 提案字段 | 观察 | 官方稳定约定仍是普通 Markdown | 避免冻结虚构 schema | 不实施 | `L0` | 等正式版本和 conformance tests 后复核 |
| X07 | MCP stdio／SSE／HTTP Provider 控制面 | 兼容适配 | 新增薄 `McpProviderPort`，工具仍编译进 ToolPlan；当前非 vendor 无独立实现 | 外部工具标准化接入 | `I3/R4/T4` | MCP／参考多为 `L0/L1` | 默认关闭；单 server 故障隔离；删除配置即可卸载 |
| X08 | MCP 工具默认 server namespace 与 schema 内容快照 | 直接采用模式 | 利用现有 duplicate rejection 和 Registry snapshot；补 raw schema bytes/hash、transport revision | 防止会话中 schema 漂移和覆盖 | `I2/R3/T3` | `L0/L1` | snapshot 不一致时拒绝续跑，重新开 Run |
| X09 | MCP／子应用工具静默覆盖 builtin（SEAJelly） | 排除 | 当前 Agent Link 已拒绝内置冲突，MCP 必须保持同样语义 | 防止越权 | 不实施 | `L1` | 冲突直接诊断，不提供 last-wins 开关 |
| X10 | 类型化 Hook interception（Yao、Bub） | 兼容适配 | 扩展现有 Hook Registry 的接入点、timeout、读写字段和事件；不加载任意脚本 | 可观察和受控变换 | `I2/R3/T3` | `L1/L2`，独立实现 | Hook 可逐个禁用；安全 Hook fail-closed，观察 Hook fail-open |
| X11 | 任意脚本获得完整 Runtime 参数 | 排除 | 违反受保护不变量、owner 与 ToolPlan 边界 | 避免插件越权 | 不实施 | `L2` 来源尤其禁止复制 | 仅允许声明式、类型化 binding |
| X12 | ACP v1 Adapter | 兼容适配／实验 | 映射 Agent Link 之外的 session、stream、tool activity、permission 和 pending interaction；内部仍用 Run Event | IDE／客户端互操作 | `I3/R4/T4` | `L0/L1` | 默认关闭；协议失败不影响 QQ／Web／Agent Link |
| X13 | ACP v2 Draft | 观察／实验 | 必须 version negotiation + feature flag，并保留 v1 | 跟踪未来能力 | `I2/R3/T3` | `L0/L1` | 草案漂移即删除实验 Adapter |
| X14 | A2A 1.0 Client Adapter | 实验 | 只作为远程 Agent task／artifact 投影，先做 allowlist、TLS、principal 和 SSRF 防护 | 与独立 Agent 协作 | `I3/R4/T4` | `L0/L1` | 删除远程 endpoint 配置；内部 Run 不受影响 |
| X15 | A2A Server／push webhook | 观察／延后 | 当前没有明确需求和威胁模型 | 避免提前扩大公网攻击面 | 不实施 | `L0/L1` | 有真实调用方后另立安全计划 |
| X16 | A2A Task 作为内部 Run／租约事实源 | 排除 | A2A 不保证 exactly-once、lease、fence 或 attempt | 保持内部正确性 | 不实施 | `L0` | 只保留 ID 映射与外部状态投影 |
| X17 | Agent Link v1 | 兼容适配（保留） | 现有自有协议继续服务 MeaPet；终态和动态工具接统一 Ledger／Permission | 不破坏现有客户端 | `I2/R3/T3` | 本项目 `L0` | 保持 v1 兼容；新 extension 通过协商加入 |

### 4.5 Permission、Sandbox、身份与 Workspace

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S01 | 统一 `PermissionPort`：allow／deny／ask／allow-once／session grant（Qwen Code、CodePilot、Maka） | 直接采用模式 | ToolPlan 和 Sandbox grant 是输入，不是通用审批；新增不可伪造 approval proof 与撤销 | 所有入口统一最小权限 | `I4/R4/T4` | CodePilot `L2`，仅独立实现模式 | 首期只接高风险工具；关闭 ask 时默认 deny，不回退 allow-all |
| S02 | capability 单调衰减（OpenHanako、Kun、OpenClaw） | 直接采用模式 | parent、host allowlist、owner policy、Runtime capability 只做交集；当前 Subagent 不具备统一实现 | 防止子 Agent 扩权 | `I3/R4/T4` | `L1/L2` | 多 Agent 关闭后不影响单 Agent；任何无法证明的继承直接 deny |
| S03 | 现有 `sandboxd` Docker 安全边界 | 直接采用模式（保持） | 独立 UDS 服务、固定 profile、非 root、只读根、cap drop、no-new-privileges、网络和资源限制均已有代码／测试 | 真实隔离执行 | `I1/R4/T4` | 本项目 `L0` | 真实 smoke 未通过则 Sandbox 保持 disabled，不降级宿主执行 |
| S04 | owner Workspace、ACL、quota、lease、immutable Asset | 直接采用模式（已有） | 复用 `Workspace`、`SandboxAccessGrant`、quota bindings、leases 和 Assets | 跨容器持久且 owner 隔离 | `I1/R4/T4` | 本项目 `L0` | 配额或身份不可确认时 fail-closed |
| S05 | Workspace Agent overlay／project identity（Kun、AutoDev） | 兼容适配 | 复用受信 principal、workspace path guard；新增配置文件 discovery 时保存 project stable ID 与 content hash | 项目级 Agent 配置可移植 | `I2/R3/T3` | `L2`，禁止复制 Kun；AutoDev `L2` | overlay 错误可诊断并停用，不影响基础聊天 |
| S06 | 宿主 `bash -lc`、词法 containment、命令 allowlist 作为 sandbox（Jeju、Agent OS） | 排除 | 与本项目强隔离边界冲突 | 避免路径、参数、环境和 symlink 逃逸 | 不实施 | `L1` | 转成安全反例测试 |
| S07 | sandbox backend 不可用时 unsandboxed fallback（Waveloom、Yoyo） | 排除 | 所有执行必须 fail-closed | 避免静默失去隔离 | 不实施 | `L1` | 返回稳定 unavailable 错误，不自动换宿主执行 |
| S08 | E2B、OpenSandbox、OpenShell 作为运行时依赖 | 排除 | 与既定 Docker Engine／runc／sandboxd 边界冲突 | 控制安全面和长期依赖 | 不实施 | 各异 | 仅可重新威胁建模后由用户另行批准 |
| S09 | 模型指定镜像、volume、network、capability 或宿主路径 | 排除 | 参数只由服务端 policy compiler 生成 | 防止容器逃逸和数据越权 | 不实施 | 与许可证无关 | 永久硬拒绝 |

### 4.6 Provider、Gateway、主动能力与多 Agent

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G01 | Provider Doctor 分层诊断（CodePilot） | 直接采用模式 | 扩展 `probe_model_route()`：配置、DNS、连接、TLS、认证、models、最小 completion、stream／tool／image；不替换路由 | 故障可定位，不再笼统归“网络” | `I2/R2/T3` | CodePilot `L2`，独立实现 | 诊断只读，可整体关闭；不影响正常请求 |
| G02 | Provider account/key 级 failure scope 与 cooldown（SEAJelly） | 兼容适配 | 合并进 `ModelFailureTracker`／credential runtime；原子计数，区分 model、provider、account | 多账号故障隔离 | `I3/R3/T3` | `L1` | 未配置 account identity 时保持现有 model scope |
| G03 | 新的 weighted key pool 替换当前排序 | 排除 | 当前已有能力过滤、成本／质量排序和熔断 | 避免冲突路由策略 | 不实施 | `L1` | 只吸收 failure scope，不吸收 selector |
| G04 | Gateway session／pending／stop／resume／model switch | 兼容适配 | 复用 QQ、Web、Agent Link、message contract；以统一 Run／Permission 为事实源 | 多入口一致控制 | `I3/R4/T4` | `L1` | 各 channel Adapter 可独立关闭，不能写内部表 |
| G05 | 主动能力 `Trigger → Evaluate → Lease → Run → Deliver` | 直接采用模式（已有大部） | 复用 `core/proactive` 与 Outbound；只补 Trigger envelope、Run Event 和统一 budget | 减少定时／主动状态机重复 | `I2/R3/T3` | Raven `L1`，现实现自有 | 保留现有 scheduler 开关与 cooldown；默认关闭主动能力 |
| G06 | Sentinel pending／feedback／TTL／quiet／quota（Raven） | 兼容适配 | 映射现有 proactive candidate、outbound、settings 和 eval sampling，不复制数据库 | 反馈驱动且不骚扰用户 | `I2/R3/T3` | `L1` | 每个子策略可关闭；不影响手动聊天 |
| G07 | 深度 1 lead-worker／DAG（Jeju、Open Multi-Agent、DSCode、Penguin） | 实验 | 新增单层 lineage、固定 role、任务／并发／token 预算、独立 Run 和 verifier；默认单 Agent | 复杂检索与验证可并行 | `I4/R4/T4` | `L1` | feature flag 关闭即回单 Agent；禁止遗留 worktree／lease |
| G08 | append-only plan revision、approve、freeze、execution receipt（Open Multi-Agent） | 实验 | 依赖 Permission、Ledger 和 Artifact；不从模型文本推断批准 | 可审计计划修复 | `I3/R4/T4` | `L1` | 未批准 revision 永不执行；可丢弃候选计划 |
| G09 | 无限递归、peer-to-peer、子 Agent 自批准 | 排除 | 违反预算、权限和责任归属 | 避免失控成本与越权 | 不实施 | 与许可证无关 | 服务端硬上限和叶子规则 |
| G10 | Orca／Commonly／LobeHub 完整工作台或 fleet UI | 观察／排除当前范围 | 现阶段无产品需求，且 Runtime 价值与 UI 无关 | 避免大规模偏航 | 不实施 | `L1/L2` | 有明确用户流程后单独立项 |

### 4.7 Trace、Eval 与受控进化

| ID | 能力与来源 | 决策 | 当前复用点与缺口 | 收益 | 侵入／风险／测试 | 许可证 | 退出方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V01 | 统一 Run Viewer／Trace projection（DeepChat、Maka、Penguin） | 兼容适配 | 复用 AgentRun、ToolCall、LLM logs、Telemetry、Admin；从 Ledger 投影，不展示隐藏推理 | 一条时间线定位失败、成本和版本 | `I3/R3/T3` | `L1` | Viewer 可删除并重建，不影响事实 |
| V02 | 隐私最小 evidence：hash、size、status、safe preview（Agent OS 的可取字段） | 直接采用模式 | 延续 Telemetry 白名单和 tracing redaction；关键事实另存可授权 payload ref | 可审计且减少敏感数据 | `I2/R4/T3` | `L1` | 泄漏测试失败即停止相关 sink；不记录 CoT |
| V03 | 统一 Eval Run Manifest 与分母 invariant（EverMemBench、EvoAgentBench、Penguin） | 直接采用模式 | 扩展现有 periodic manifest；加入 code dirty、dataset revision/hash、Prompt/model/provider、预算、failure/timeout/excluded/missing | 防止漏样本和不可比较分数 | `I2/R3/T3` | `L1/L3`，独立实现 | 旧报告保持可读；字段不足的 run 标 invalid，不补造 |
| V04 | retrieval evidence 与 answer 分开评测（LoCoMo、EverMemBench） | 直接采用模式 | 扩展现有 RAG benchmark schema，保留 evidence refs、recall、answer、failure type | 区分“猜对”和“有据回答” | `I2/R2/T3` | LoCoMo `L2`，EverMemBench code `L3`；默认合成 fixture | 数据许可证不满足时只跑自建 fixture |
| V05 | domain-native verifier + 多次独立运行 + 标准误（EvoAgentBench） | 直接采用模式 | 在 deterministic gate 与真实模型 benchmark 之间增加明确层级 | 更可靠比较 Runtime／Skill 候选 | `I2/R2/T3` | `L1` | 费用超预算时只保留 deterministic gate，不伪造统计 |
| V06 | LoCoMo／EverMemBench 固定 revision Adapter | 实验 | 不把完整非商业数据打入生产镜像；先校验数量、类别、evidence 和 loader | 评估中文长期记忆边界 | `I2/R2/T3` | LoCoMo `L2`；EverMemBench code `L3` | 数据／代码许可不清则只保留 schema-compatible 合成集 |
| V07 | candidate bundle、冻结 train/test、private rubric、人工 promotion（Jeju、Raven、Yoyo、Penguin） | 实验 | 复用现有 eval candidate／record-only review，增加 immutable bundle hash 和发布状态 | 安全优化 Prompt／Skill／有限 Manifest | `I3/R4/T4` | `L1` | 任一领域负迁移、成本或安全失败即拒绝；保留旧 bundle |
| V08 | trajectory 提取 Skill 候选 | 实验 | 只读脱敏 Run Event，输出候选区，不能覆盖正式 Skill | 从真实失败提取流程 | `I2/R3/T3` | `L1` | 停止 extractor 即可；候选有 TTL，不影响生产 |
| V09 | 网站 Overall、混合单位 cost、缺原始产物的 benchmark headline | 排除 | 多个项目存在覆盖不完整、常量聚合或单位混用 | 避免错误结论 | 不实施 | 各异 | 只接受本项目固定 manifest 的逐题结果 |
| V10 | Prompt-only approval、自动修改／提交／tag／push／自动批准主干（SEAJelly、Yoyo） | 排除 | 与提交授权、服务端审批和安全边界冲突 | 防止不可控变更 | 不实施 | `L1` | 仅生成 patch／candidate artifact，由人工决定 |
| V11 | 替换整棵 Git tree 冒充单提交 revert（SEAJelly） | 排除并加入反例 | 会丢弃目标提交后的改动 | 防止破坏工作树 | 不实施 | `L1` | 只允许真正 reverse patch 或 release rollback |

## 5. 明确删除的重复建设建议

以下建议曾可从外部项目名称直观推导，但当前仓库已经有更适合的实现。它们从后续“新建系统”清单中删除，改为增量接入：

1. **不新建 Tool Registry。** 复用 `ToolRegistrationRegistry`、`ToolPlan` 和 Registry Kernel，只补外部 provider、trust、permission、sandbox 与 session snapshot。
2. **不新建 Memory Provider。** 复用 `MemoryProviderPort` 和 Registry，只补 provenance、scope、revision、事件和评测。
3. **不新建 Prompt Builder。** Agent Manifest 编译结果只能引用 canonical Prompt Runtime plan／template／flow hash。
4. **不把 Telemetry 表升级改名成 Event Ledger。** 保持无正文、失败开放观测语义；权威 Ledger 另设事务边界并向 Telemetry 投影。
5. **不新建通用 Scheduler。** Job、Task Runtime、Scheduled Workflow、Outbound 和 Sandbox 已有成熟租约／fence；只抽公共合同。
6. **不新建主动外呼状态机。** 现有 Proactive + Outbound 已覆盖主要链路，只统一 Trigger、Run Event 和反馈引用。
7. **不新建 Sandbox 平台。** 继续建设和实测现有 `sandboxd`；外部 Sandbox 只作为反例或设计信号。
8. **不新建 Workspace／Asset Store。** 复用 owner-scoped Workspace 和 content-addressed Asset；通用 Artifact 只是 Port／别名与来源字段。
9. **不新建文件 Patch／Undo 系统。** 当前 `sandboxd` 已有严格 patch、expected hash、批量 journal 和 partial recovery；只接 receipt 与 checkpoint。
10. **不新建模型路由器。** 复用现有 Descriptor、route、cost／quality 排序和 ModelFailureTracker；只补 Doctor 与 credential scope。
11. **不新建 Hook Registry。** 复用 Runtime Hook／Policy Registry，只增加实际接入点、timeout 和诊断事件。
12. **不复制外部 Eval 平台。** 扩展现有 deterministic gate、RAG benchmark、candidate workflow、periodic manifest 和 review。
13. **不把 ModuleManifest 改名成 AgentManifest。** 两者生命周期和所有者不同；共享 Registry Kernel，但保持独立 schema。
14. **不以 ACP／A2A 替代 Agent Link。** 三者面向不同边界；ACP／A2A 只做可选 Adapter。
15. **不以 KT MCP／Skill 实现定义 Nanobot 合同。** 先把 Provider 合同放入 Core，再由 KT 或 Native Adapter 使用。

## 6. README／论文／类型声明与源码事实偏差总表

本节集中记录所有会影响取舍的偏差。详细源码路径和固定 commit 见 Wave A～E；这里的结论只用于防止后续再次按项目宣传语设计接口。

| 项目／来源 | 容易形成的理解 | 固定源码／数据事实 | 最终处理 |
| --- | --- | --- | --- |
| DeerFlow | JSONL Run Event 可直接用于服务端可靠持久化 | 明确是单进程方案，生产多 worker 缺事务／条件更新 | 只采用 lease／terminal 模式，排除 JSONL 事实源 |
| Qwen Code | workflow snapshot 是 durable checkpoint | 源码明确是 best-effort convenience | 只作体验快照，恢复依赖 Ledger／receipt |
| Open Multi-Agent | checkpoint／adaptive recovery 支持任意点续跑 | 任务粒度覆盖式快照；中断任务重跑；FileStore 无跨进程锁 | 只对可重入 task 实验 |
| CodePilot | file checkpoint 与 RunCheckpoint 是统一恢复 | 前者偏进程内文件回退，后者是运行前状态提示 | 分开命名；BUSL 实现不复制 |
| DeepChat | runtime event bus 就是 Tape 事实源 | event bus 是 renderer 通知，SQLite Tape／transcript 才承担事实 | 协议事件只做 projection |
| GoClaw | “8-stage pipeline”是八个注册组件 | 固定版本实际注册七个 Stage，MemoryFlush 内联 Prune | 按职责设计，不冻结数字；NC 代码不复制 |
| Maka Agent | replay 是底层调用 bit-exact 重放 | 保证的是语义 replay | Eval 明确比较层级 |
| LobeHub | Runtime state／stream 是完整账本 | 主要是可变状态、流和 step claim，缺不可变终态合同 | 只借鉴 identity／memory 字段；Community License 实现不复制 |
| Orca | Run／fleet 是服务端 durable scheduler | Run 主要是 namespace／inbox，dispatch 多由 CLI／Skill 驱动 | 只借鉴 provenance、receipt、unknown outcome |
| Yao | Hook／delegate 已经是受控扩展 | V8 Hook 可改宽 Runtime 参数，委派缺明确最大深度 | 收窄为类型化 Hook；修改版许可证代码不复制 |
| LobsterAI | 产品与 Runtime 分离意味着多 Runtime 已落地 | 固定版本只有 OpenClaw engine | 只借鉴 Adapter／patch 交付，不提前宣称可替换 |
| OpenHanako | Skill snapshot 冻结 Skill 内容 | 实际主要冻结启用列表和来源 pointer，内容仍可漂移 | pointer snapshot 与 bytes snapshot 分开 |
| Kun | AGENTS.md 支持完整层级继承 | 当前主要加载全局与 Workspace 根文件 | 按正式 AGENTS 最小约定独立实现；PolyForm 代码不复制 |
| AutoDev | SubAgent persistence 是 durable 状态 | Manager 是进程内 map | 恢复必须由 Nanobot Ledger／lease 保证 |
| Raven | Trace／TokenWise 等于预算执行与事实账本 | Trace 尽力写，TokenWise 主要是 usage 记录 | 预算和账本用服务端合同 |
| Yoyo | worktree 隔离等于安全 sandbox | 源码明确 cwd 不是 sandbox，失败可回退当前目录 | 执行统一走 `sandboxd`，排除破坏性 reset／自动 push |
| Commonly | workstation／durable task 已实现 | 无具体 Workstation 强隔离模型；Task 无 lease／fence | workstation 观察；当前 task 实现排除 |
| OpenClaw | 公开 Harness 是通用稳定 SDK | 官方明确标实验，且镜像内部 Runner 类型 | 只抽最小能力，不复制大型合同 |
| Bub/Tape | append-only Tape 默认就是可靠 Event Store | 默认 JSONL 缺事务、checksum、跨进程锁和提交边界 | 采用 facts／view 分层，排除 FileTapeStore |
| Agent Skills | `allowed-tools` 是自动授权 | 字段仍实验且格式不提供权限语义 | 只作为 capability request |
| AGENTS.md | 社区 v1.1 字段是正式 schema | 官方稳定约定只是普通 Markdown，无官方 parser/schema | v1.1 仅观察 |
| ACP | 仓库内 v2 schema 代表稳定版 | v1 是稳定 wire；v2 是 Draft | v1 可选 Adapter，v2 双门禁实验 |
| ACP | `session/load` 自带持久会话事实 | 协议只规定行为，持久化由 Agent 实现 | ACP 不成为第二事实源 |
| A2A | “production-ready 1.0”含 exactly-once | send 只 MAY 幂等，Task 无 lease／fence／attempt | 只作外部 Adapter |
| A2A | `tenant` 可直接作为 owner | 规范定义为 opaque routing key | owner 必须来自认证 principal |
| EverOS | OME 可承担通用后台恢复 | 崩溃标记与重新入队非原子，存在 at-most-once 窗口 | 排除为 durable task |
| EverOS | LoCoMo category／Max accuracy 可直接比较 | 类别标签映射错误、排除 adversarial，Max 取多种统计最大值 | 只采用分层模型，不采用 headline |
| EverAlgo | operator 是 pure function，仓库统一 Apache-2.0 | 多个 operator 调 LLM；包内 LICENSE 与元数据冲突 | 称 persistence-free；许可证澄清前不复制 |
| HyperMem | 一条命令跑完整六阶段且 92.73 可复现 | 默认脚本省略 stage 1；阈值多套；缺数据、结果、锁和论文链接 | 只观察超图概念 |
| EverMemBench | 官方 loader 可读取当前官方数据并保留证据 | 当前 loader 与 HF 顶层格式不兼容，qars parser 丢 `R`；代码无许可证 | 独立固定 revision adapter，evidence 一等化 |
| EvoAgentBench | 论文、数据、网站是同一 benchmark，Overall/cost 可横比 | 领域、任务、方法和版本分叉；覆盖不完整也聚合，cost 单位混用 | 只采用隔离状态和 native verifier |
| Jeju | local sandbox 安全限制 workspace | 实际宿主 `bash -lc` + 完整环境，safe path 不约束 shell | 排除安全实现 |
| Jeju | HotpotQA 提升可复现 | 缺逐题结果、完整 Run Manifest、winner 和 Provider revision | 数字仅观察 |
| Waveloom | sandbox 不可用时仍能安全运行；file history 是完整 checkpoint | 可 unsandboxed fallback；history 只覆盖内建编辑，多文件可 partial | 排除 fallback；只采用 compaction／sanitation |
| Waveloom | 缓存命中和成本下降已证明 | 只有说明与手工实验程序，缺真实 Trace | 用 Nanobot 自测 |
| DSCode | Patch 是全局 atomic，Docker sandbox 已完整限额 | 全量先校验、单文件替换，多文件仍可 partial；容器缺完整 CPU／内存／磁盘／输出边界 | 只采用 hash conflict；Sandbox 不采用 |
| Penguin Harness | 默认审批安全，benchmark 数字可比较 | SDK 默认 deny，但 CLI／Server allow-all；数字是聚合常量，缺原始任务／Trace／价格 | 统一 fail-closed；只采用优化纪律 |
| Agent OS Harness | workspace-contained、secret-safe | symlink／解释器参数可逃逸，继承完整环境，preview 含原始输出 | 作为安全反例 |
| SEAJelly | secure E2B、review-first evolution、事件可安全 claim | E2B 调用缺显式网络／资源策略；审批只靠 Prompt；批量 claim 先查后改可竞态 | E2B／Prompt approval／claim 实现排除 |
| SEAJelly | revert 单个 commit | 实现把当前树替换为目标 parent 的整棵 tree，丢后续变更 | 排除并加入反例测试 |

当前 KT 还有一项**本仓库直接核验的文档风险**：`vendor/KohakuTerrarium/` 的 `pyproject.toml` 版本为 `1.3.0`，许可证为 `KohakuTerrarium-1.0`；不能因为许可证文字“Based on Apache-2.0”就把它记录为 Apache-2.0。后续依赖清单、发布审计和部署说明必须使用真实许可证标识。

## 7. 收敛后的实施顺序

### 7.1 Wave A：Runtime 与 KT 解耦

1. 先扩充现有行为 Golden，覆盖聊天、流式、工具循环、路由、Prompt、历史、取消、research、定时、主动、Codex 账号、Agent Link 和 Sandbox。
2. 在现有 `core/agent_runtime` 上定义 Runtime support descriptor、Run／Turn IDs、typed stream event 和错误／终态合同。
3. 固定当前 KT `v1.3.0` 行为，查询上游正式 tag，并建立 API／许可证／依赖／测试差异表。
4. 只在 `nanobot_kt` 内完成升级；先等价，再删除私有访问、monkey patch、buffer 读取和 Prompt 字符串清理。
5. 抽出 `ToolExecutionPort`，让 deterministic tool、KT 和 Native 共用同一执行入口。
6. 实验 NativeAgentRuntime；通过 Fake／KT／Native 同一套合同和故障夹具后，才切主回复灰度。
7. Native 可独立运行后，删除 submodule、patch 和默认源码安装；KT 改为 optional Adapter，并完成许可证专项复核。

### 7.2 Wave B：权威 Run 语义

1. 复用 Runtime Event Descriptor，新建 authoritative Run Event Store，不改变 Telemetry 的失败开放语义。
2. 先接无副作用／只读 shadow 事件，再接 accepted／terminal barrier。
3. 将 Outbound receipt、Scheduled step attempt、Sandbox run 和 tool mutation receipt 关联到 Run。
4. 从现有各领域状态机抽 lease／fence 公共合同，不合并领域表。
5. 只在安全边界实现 checkpoint／resume／fork；无 receipt 的副作用进入 ambiguous。
6. 用现有 Asset Store 实现薄 `ArtifactPort`。

### 7.3 Wave C：Context、Permission 和扩展

1. 在当前 rolling summary 和 prefix epoch 上增加 compaction decision 与本项目 benchmark。
2. 建立 Tool Result Envelope，并把大结果发布为 Asset／Artifact。
3. 实现统一 PermissionPort；先接高风险工具、Sandbox 和未来 subagent。
4. 扩展 Tool Registration 的外部来源字段和 session snapshot。
5. 实现只读 Agent Skills／AGENTS.md discovery，再做版本 pin／治理。
6. MCP 仅在 Permission、Tool snapshot 和秘密引用完成后进入关闭默认的实验。
7. 复用现有 Hook Registry 增加接入点，不加载任意脚本。

### 7.4 Wave D：互操作与多 Agent

1. 将 Agent Link 终态和 pending interaction 投影到 Run Ledger。
2. 试验 ACP v1，v2 仅观察；A2A 先做 allowlisted client，Server／push 延后。
3. 实验深度 1、固定角色、有预算、有 verifier 的子 Agent。
4. 在 Ledger／Permission 上增加 plan preview／approve／freeze／revision。
5. 保持单 Agent 为默认；关闭实验后现有聊天行为不变。

### 7.5 Wave E：评测与受控进化

1. 扩展现有 Eval manifest 与分母 invariant，不创建新平台。
2. 增加 Runtime parity、Context／cache、Memory evidence、Permission／recovery 和多 Agent 成本评测。
3. 只使用固定 revision、明确许可证的数据；默认提交合成 fixture，不把非商业大数据打入生产镜像。
4. trajectory 只生成 candidate bundle；冻结 train／test、使用 domain-native verifier、多次独立运行和人工 promotion。
5. 禁止自动修改、批准、提交、tag、push 或 destructive revert。

## 8. 每类能力的退出原则

后续任何实验都必须在实现前写明以下退出路径：

- **Runtime／KT：** 保持旧 Adapter 的短期可选回滚，但不保留无限 patch 队列；新能力没有第二个真实 Runtime 消费时删除。
- **Ledger：** 先 shadow；在成为控制事实源前允许停写。成为事实源后只能通过版本化迁移和 projection rebuild 回滚，不能删除事实。
- **Permission：** 审批服务异常时 fail-closed；不得回退 allow-all。
- **Sandbox：** readiness／真实隔离失败即关闭执行能力；不得回退宿主 shell。
- **Context／Memory：** 新策略可按 feature flag 回到最近原文 + 现有 summary；不得删除 `ChatLog`。
- **Skill／MCP／协议：** Provider／Adapter 可独立卸载；其外部 ID、配置或事件不能成为内部唯一事实。
- **多 Agent：** 关闭后回单 Agent；必须回收 lease、临时 workspace／worktree 和 pending task。
- **自进化：** 拒绝候选或回滚到上一 immutable bundle；不得通过 Git reset／tree replace 恢复。

## 9. 阶段 0.5 验收记录

阶段 0.5 的四项要求均满足：

1. 所有进入后续路线的能力均标注为“直接采用模式、兼容适配、实验、观察或排除”；
2. 已逐项映射当前仓库，实现了 Registry、Prompt、Tool、Memory、Task、Outbound、Sandbox、Context、Provider、Hook、Agent Link、Eval 和 Proactive 的去重；
3. 每个能力记录了收益、侵入性、风险、测试成本、许可证处理和退出方式；
4. README／论文／类型声明与固定源码事实的偏差已单独汇总。

阶段 0 完成不表示任何候选能力已经成为 Nanobot 运行事实。下一步只进入阶段 1：先冻结升级前行为并完善 Runtime 合同；README 继续不变。
