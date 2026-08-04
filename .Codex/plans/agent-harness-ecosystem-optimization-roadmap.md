# Nanobot Agent Harness 生态调研与优化总路线

> 状态：执行中（阶段 4.1，Run/Event Ledger 已成为执行写入与管理端权威事实源）
>
> 建立日期：2026-08-03
>
> 调研入口：[DeepSeek Harness 内测招募帖评论区开源项目荟萃分析](https://x.com/i/status/2083921996967227637)
>
> 适用仓库：Nanobot Server
>
> 提交约束：本轮已获用户明确授权；每完成一个可独立验收模块后执行全量测试、提交并推送。

## 1. 目的

本计划用于保存本轮外部 Agent Harness 生态调研形成的全部后续优化方向，避免因会话压缩只保留 KT 升级等局部结论。

计划不把第三方项目列表或尚未实现的能力写进 README。README 只在相关能力实现、验证并成为稳定运行事实后更新。

本路线包含以下主线：

1. 对原帖高星项目及其来源项目进行源码级核验；
2. 完善框架无关的 Agent Runtime 合同；
3. 建立声明式 Agent Manifest 与可移植能力包；
4. 升级 KT，清理旧版兼容妥协，并解除 KT 硬依赖；
5. 实现 Native Agent Runtime；
6. 建立事件账本、恢复、Artifact 和长期任务语义；
7. 优化 Context Engine 与前缀缓存；
8. 完善 Skill、MCP、Hook 和协议兼容层；
9. 统一权限、Sandbox、身份、工作区和记忆作用域；
10. 引入有界多 Agent 编排和远程会话控制；
11. 建立可观察、可回放、可评测、受控自进化的运行闭环。

## 2. 计划边界

### 2.1 必须保持的现有原则

- `ChatLog` 是完整档案，保留 tool、ambient 等事件；`ConversationTurn` 是可清理的工作记忆。
- HTTP 请求保持无状态，历史由服务端按请求重建。
- canonical Prompt Runtime 是 Prompt、历史、工具说明和运行时输入的唯一权威组装边界。
- 模型选择继续使用现有能力过滤、优先级、成本排序和熔断机制。
- `sandboxd` 是唯一允许接触 Docker Socket 的组件。
- Sandbox 默认断网、最小权限，并与 owner、ACL 和配额绑定。
- 外部项目只能提供待验证的设计信号，不能凭 README 自述直接进入实现。
- 修改历史注入、conversation、工具输出、`enriched_query` 或 Prompt Runtime 输入时，必须同步检查 canonical 模板。

### 2.2 本计划不自动包含的事项

- 不因为外部项目流行而重做整个桌面端或 TUI。
- 不建设通用低代码工作流平台。
- 不直接复制 LobeHub、Commonly 或 Orca 的完整协作 UI。
- 不因为 GoClaw 使用 PostgreSQL 而启动数据库迁移。
- 不引入 OpenSandbox、OpenShell、E2B 作为运行时依赖。
- 不把 Docker Socket 挂载给 Nanobot Server 或 Sandbox 容器。
- 不开放无限递归、无预算的多 Agent 对话。
- 不允许生产 Agent 自动修改主干、自动提交或自行批准进化结果。
- 不把 KT `main` 分支直接作为生产依赖。
- 不在本计划阶段直接引入全局知识图谱、Computer Use 或 Kubernetes Sandbox。

## 3. 已确认的调研范围

### 3.1 原帖高匹配且 stars 不低于 1000 的项目

以下 18 个项目已确认仓库身份、当前元数据和 README。这里的“已确认”不代表已经完成源码级核验；源码核验属于阶段 0 的后续任务。

| 项目 | 初步可参考方向 | 当前结论 |
| --- | --- | --- |
| [lobehub/lobehub](https://github.com/lobehub/lobehub) | Agent 作为工作单元、可编辑记忆、插件控制面 | 参考 Agent 产品模型和可解释记忆，不复制完整 UI |
| [bytedance/deer-flow](https://github.com/bytedance/deer-flow) | 长任务、Session Goal、Context 压缩、Checkpoint、Lease、Subagent、Gateway | 高优先级源码核验对象 |
| [stablyai/orca](https://github.com/stablyai/orca) | Agent fleet、worktree、任务入口和审查体验 | 主要作为代码工作区产品观察项 |
| [esengine/DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix) | 稳定前缀、缓存经济学、配置与插件驱动 | 高优先级 Context/缓存参考 |
| [QwenLM/qwen-code](https://github.com/QwenLM/qwen-code) | Auto Memory、Skill、Subagent、Agent Team、MCP、Daemon、ACP | 高优先级互操作与团队能力参考 |
| [YaoApp/yao](https://github.com/YaoApp/yao) | Hook、分层 Memory API、MCP、声明式能力 | 参考合同和 Hook，不引入低代码平台 |
| [open-multi-agent/open-multi-agent](https://github.com/open-multi-agent/open-multi-agent) | 动态 DAG、确定性调度、批准、Checkpoint、预算、Trace、Eval | 高优先级多 Agent 和恢复参考 |
| [op7418/CodePilot](https://github.com/op7418/CodePilot) | Provider 诊断、Checkpoint/Rewind、Skill/MCP 管理、Scheduler | 参考运行控制和管理端体验 |
| [ThinkInAIXYZ/deepchat](https://github.com/ThinkInAIXYZ/deepchat) | Tape/Trace、ACP、恢复、远程会话控制 | 高优先级事件账本和远程控制参考 |
| [netease-youdao/LobsterAI](https://github.com/netease-youdao/LobsterAI) | 产品层与 Runtime 分离、配置同步、运行时修复、权限和 Artifact | 参考 Adapter 和运行维护边界 |
| [liliMozi/openhanako](https://github.com/liliMozi/openhanako) | 人格、自治、插件和 Skill | README 信息不足，必须先查源码再决定 |
| [KunAgent/Kun](https://github.com/KunAgent/Kun) | 多工作区、Agent Graph、受限子 Agent、AGENTS/Skill/MCP | 参考作用域和编排，不复制多工作台 UI |
| [phodal/auto-dev](https://github.com/phodal/auto-dev) | Agent as Tool、Subagent、AGENTS.md、Skill、MCP、A2A | 参考组合模式，Kotlin Multiplatform 本身不进入本项目 |
| [nextlevelbuilder/goclaw](https://github.com/nextlevelbuilder/goclaw) | 分阶段 Agent Pipeline、Prompt Mode、三层记忆、RBAC、Domain Event、Trace | 高优先级运行管线和权限参考 |
| [EverMind-AI/Raven](https://github.com/EverMind-AI/Raven) | Memory-first、Curator、Sentinel、SkillForge、TokenWise、Evolver | 高优先级记忆、主动能力和自进化参考 |
| [yologdev/yoyo-evolve](https://github.com/yologdev/yoyo-evolve) | 测试门禁自进化、Trajectory、Checkpoint、后台任务、工具输出压缩 | 参考离线进化和恢复，不采用自动提交主干模式 |
| [Team-Commonly/commonly](https://github.com/Team-Commonly/commonly) | 可移植 Agent Identity、Runtime 解耦、共享/私有记忆、Task Board | 高优先级身份和协作边界参考 |
| [maka-agent/maka-agent](https://github.com/maka-agent/maka-agent) | Event Log 即 Runtime、Context 不等于 History、Durable Task、Headless Eval | 高优先级事件溯源和恢复参考 |

### 3.2 来源项目和高相关小项目

| 项目或标准 | 调研原因 |
| --- | --- |
| [openclaw/openclaw](https://github.com/openclaw/openclaw) | LobsterAI 的 Runtime 来源，也是 GoClaw 的架构参考 |
| [bubbuild/bub](https://github.com/bubbuild/bub) | DeepChat 所述 Tape 思路的重要来源；提供 Hook-first 和 append-only context 模式 |
| [Agent Skills](https://agentskills.io/) | `SKILL.md` 可移植格式和生态兼容边界 |
| [agents.md](https://agents.md/) | 项目级 Agent 指令发现约定 |
| ACP | Qwen Code、DeepChat、Orca 等项目使用的 Agent 客户端协议候选 |
| A2A | AutoDev 等项目使用的 Agent 间任务交换协议候选 |
| [EverMind-AI/EverOS](https://github.com/EverMind-AI/EverOS) | Raven 的长期记忆底座 |
| EverAlgo、HyperMem、EverMemBench、EvoAgentBench | Raven 的提取、记忆和自进化评测来源 |
| [cosmtrek/jeju](https://github.com/cosmtrek/jeju) | 声明式 Agent Manifest、Trajectory、受限团队和 GEPA 式进化 |
| [Menfre01/waveloom](https://github.com/Menfre01/waveloom) | DeepSeek 前缀缓存、工具输出安全、Checkpoint、Plan Mode |
| [thinkany-ai/dscode](https://github.com/thinkany-ai/dscode) | Tree Session、稳定工具顺序、OS Sandbox、权限与 Provider 隔离 |
| [Prism-Shadow/penguin-harness](https://github.com/Prism-Shadow/penguin-harness) | Benchmark 驱动的 Agent/Skill 优化 |
| [VIONWILLIAMS/agent-os-harness](https://github.com/VIONWILLIAMS/agent-os-harness) | 不保存原始 Prompt/推理的最小证据账本 |
| seajelly 等自进化项目 | 用于交叉核验进化闭环是否有真实评测、冻结和回滚边界 |

## 4. 目标架构

```text
QQ / Web / Agent Link / Scheduler / Proactive Trigger
                         │
                         ▼
                Application Orchestration
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
      Agent Manifest Compiler   Run / Event Ledger
             │                       │
             ▼                       ▼
        AgentRuntimePort       Checkpoint / Artifact
             │
     ┌───────┴────────┐
     ▼                ▼
NativeAgentRuntime  KTAgentRuntimeAdapter（可选）
     │                │
     └───────┬────────┘
             ▼
Prompt Runtime / Context Engine / Model Provider / Tool Execution
Skill / MCP / Hook / Permission / Memory / Sandbox / Subagent
```

关键边界：

- Nanobot 拥有业务语义和稳定合同。
- KT 只实现可选 Agent Loop Adapter。
- Runtime 不直接拥有长期历史、业务数据库和 Docker 权限。
- Event Ledger 记录运行事实，Context Engine 只决定下一次推理看到什么。
- Artifact 是大结果和生成资产的唯一跨会话引用边界。
- Permission 和 Sandbox 是强制边界，不依赖 Prompt 自律。

## 5. 按顺序执行的完整计划

### 阶段 0：完成源码级生态调研

#### 0.1 确认原帖和高星项目全集

- [x] 获取原帖文章正文和图表。
- [x] 确认原帖筛选方法、样本局限和 18 个高星项目名单。
- [x] 核对 18 个仓库当前身份、默认分支、活跃度、许可证和 README。
- [x] 将当前星数等易变数据标注为调研时快照，不作为架构判断依据。

#### 0.2 源码核验第一批：Runtime 与运行可靠性

- [x] 深入 deer-flow 的运行入口、checkpoint channel、lease heartbeat、reconcile、Context 和 Subagent 限制。
- [x] 深入 DeepSeek-Reasonix 的固定前缀、消息维护、插件和 Provider 结构。
- [x] 深入 qwen-code 的 Agent Team、Skill、MCP、Daemon 和 ACP 实现。
- [x] 深入 open-multi-agent 的 DAG、scheduler、plan approval、checkpoint、budget、trace 和 eval。
- [x] 深入 CodePilot 的 checkpoint/rewind、Provider diagnostics、Scheduler 和权限模型。
- [x] 深入 deepchat 的 Tape schema、Trace projection、ACP session 和远程 pending interaction。
- [x] 深入 goclaw 的 8 阶段 pipeline、Prompt Mode、Domain Event、RBAC 和三层记忆。
- [x] 深入 maka-agent 的 Runtime Event Log、projection、durable task、resume 和 headless eval。

第一批固定 commit、源码路径、测试证据、许可证边界和 Nanobot 取舍已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-source-verification-wave-a.md`。

#### 0.3 源码核验第二批：平台、身份、记忆和扩展

- [x] 深入 lobehub 的 Agent 数据模型、可编辑记忆和插件控制面。
- [x] 深入 orca 的 Agent fleet、worktree 隔离、snapshot 和任务入口；区分 UI 能力与 Runtime 能力。
- [x] 深入 yao 的 Hook、Memory scope、MCP 和声明式编译边界。
- [x] 深入 LobsterAI 的 OpenClaw Runtime Adapter、配置同步、修复和 Artifact/权限边界。
- [x] 深入 openhanako 的人格、自治、插件和 Skill 真实实现；README 不足时以代码为准。
- [x] 深入 Kun 的 workspace、Agent Graph、Subagent 授权和项目配置发现。
- [x] 深入 auto-dev 的 Agent as Tool、AGENTS.md、Skill、MCP 和 A2A 适配。
- [x] 深入 Raven 的 Curator、Sentinel、SkillForge、TokenWise、Tracing 和 Evolver。
- [x] 深入 yoyo-evolve 的 evolution workflow、trajectory extraction、test gate、session 和工具安全。
- [x] 深入 Commonly 的 portable identity、pod memory、runtime event、task claim 和 workstation。

第二批固定 commit、源码路径、测试证据、许可证边界、README 偏差和 Nanobot 取舍已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-source-verification-wave-b.md`。

#### 0.4 核验来源项目和协议

- [x] 对 OpenClaw、Bub/Tape、Agent Skills、agents.md、ACP、A2A 读取官方协议或源码。
- [x] 对 EverOS、EverAlgo、HyperMem、EverMemBench 和 EvoAgentBench 核对数据模型与评测方法。
- [x] 对 Jeju、waveloom、dscode、penguin-harness、agent-os-harness 和 seajelly 核对真实实现。
- [x] 记录版本、commit、许可证、文件路径和必要的最短源码证据。
- [x] 对无法访问、已改名或已删除的项目标记为“未验证”，不得据此设计接口。

OpenClaw、Bub/Tape、Agent Skills、AGENTS.md、ACP 和 A2A 的固定 commit、正式协议／源码路径、许可证、稳定／草案边界、文档偏差和 Nanobot 取舍已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-source-verification-wave-c-protocols.md`。

EverOS、EverAlgo、HyperMem、EverMemBench、EvoAgentBench 和共同评测来源 LoCoMo 的数据模型、持久化／恢复边界、固定数据版本、评测缺陷、许可证与 Nanobot 取舍已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-source-verification-wave-d-memory-evaluation.md`。

Jeju、Waveloom、DSCode、Penguin Harness、Agent OS Harness 和 SEAJelly 的真实实现、固定版本、测试结果、安全／恢复边界、自进化缺陷与 Nanobot 取舍已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-source-verification-wave-e-small-harnesses.md`。

#### 0.5 形成取舍矩阵

- [x] 为每个能力标注“直接采用模式、兼容适配、实验、观察、排除”。
- [x] 映射本仓库已有实现，去掉重复建设建议。
- [x] 记录收益、侵入性、迁移风险、许可证、测试成本和退出方式。
- [x] 单独记录 README 声明与源码事实不一致的项目。

全量能力取舍、当前仓库映射、重复建设排除项、许可证边界、README／论文／类型声明偏差以及分阶段退出方式已记录在
`docs/superpowers/research/agent-harness-ecosystem/2026-08-03-final-adoption-matrix.md`。

验收条件：所有进入实现阶段的外部模式，都能定位到官方仓库的具体源码、测试或协议文档；只有 README 宣传语的项目不能进入实施清单。

### 阶段 1：冻结行为并完善 Runtime 合同

#### 1.1 冻结升级前行为基线

- [x] 普通聊天与流式聊天。
- [x] 单次和多轮工具调用、ToolPlan、工具循环上限。
- [x] 模型选择、降级、熔断、Provider 错误归一化。
- [x] canonical Prompt Runtime、历史注入、历史清除和消息顺序。
- [x] 取消、超时、断流和客户端断开。
- [x] research、定时任务、主动外呼和确定性工具执行。
- [x] Codex 多账号切换、账号级熔断和故障转移。
- [x] Agent Link 动态工具与 Sandbox 工具。
- [x] 构造不依赖 KT 类型的可重放夹具和 Fake Runtime。

阶段 1.1 已把 `tests/fixtures/agent_runtime_behavior_cases.json`、
`tests/golden/architecture_behavior/agent_runtime.json` 接入现有行为基线生成器和 Manifest，
固定普通、流式、三轮工具链、中断信号、模型路由、conversation 与生命周期，且重放路径只依赖
`core.agent_runtime.AgentRuntimePort`／`FakeAgentRuntime`。定向升级前回归矩阵为 261 passed，
模型候选降级、熔断和不合格 fallback 补充矩阵为 14 passed。回归同时发现本地
`data/prompts_v2/chat/identity_context.md` 仍为带发送者字段的 v1；已与 canonical v2 同步，
恢复群聊历史前缀跨发送者稳定性。

#### 1.2 统一 Run、Turn 和 Event

- [x] 定义 `run_id`、`turn_id`、`correlation_id`、`tool_call_id`、actor 和 owner。
- [x] 定义接纳、运行、等待批准、等待输入、取消、超时、成功、失败和 ambiguous 状态。
- [x] 定义文本增量、工具活动、usage、artifact、错误和结束事件。
- [x] 扩展 `AgentRuntimePort`，提供 `run()`、`run_stream()` 和 `run_event()` 等价能力。
- [x] Runtime 合同层不得导入 KT、FastAPI、SQLAlchemy 或具体 Provider SDK。

已在 `core/agent_runtime/contracts.py` 增加 `RuntimeRunIdentity`、`RuntimeActor`、
`RuntimeRunStatus`、显式状态迁移和 `RuntimeRunEvent` 类型化 payload；终态只能由 `end` 事件表达，
事件合同本身不冒充后续 authoritative Ledger。`RequestRuntimeContext` 已携带 turn／correlation／actor，
MessageContract 与 KT Adapter 只投影受信 owner／actor 字段。`AgentRuntimePort` 现已提供返回最终结果的
`run()`、异步类型化事件流 `run_stream()` 和 callback + 最终结果形式的 `run_event()`；旧
`execute_turn()` 只保留为兼容 façade。KT Adapter 通过 `BufferedOutput` 的请求级 ContextVar 信号捕获
真实 token 增量，并在执行完成后从 conversation／LLM usage 投影真实 tool call 和 usage；异常、取消与
超时均形成 error／end 终态，事件 ID 对每次 emission 唯一且 sequence 严格递增。主 Bridge 最终结果路径
已切换到 `run()`，旧 SSE 字典协议暂保持不变作为阶段 3.4 前的兼容出口；后续迁移只需把类型化事件投影
到旧 SSE，不再读取合成的最终文本冒充流式。行为基线已通过 `run_event()` 重放并固定去除 event ID／时间
后的事件序列。新增合同、输出和 Golden 定向回归 23 passed；KT Bridge 49 passed，Prompt／旧流式／
新合同联合回归 51 passed，均仅有既有 StarletteDeprecationWarning。

#### 1.3 补齐核心 Port

- [x] `ConversationPort`：读取、替换和重建单轮上下文。
- [x] `ToolExecutionPort`：确定性工具执行，不借用 KT executor。
- [x] `RunEventSink`：写入类型化运行事件。
- [x] `CheckpointStore`：保存和恢复安全边界。
- [x] `ArtifactPort`：发布和读取不可变资产。
- [x] `PermissionPort`：审批、拒绝和临时授权。
- [x] `SkillProviderPort`、`McpProviderPort` 和 `RuntimeCapabilities`。

`ConversationPort` 已从总 Runtime 合同中抽为可单独结构化检查的最小快照读写能力，Fake 与 KT Adapter
共用相同测试；单轮重建继续使用原子 `replace_conversation()`，不新增第二套 conversation 状态。
`RunEventSink` 已定义为异步 append Port，并提供严格 event ID 去重的 `InMemoryRunEventSink`；它明确只供
测试和进程内投影，不宣称具备阶段 4 authoritative Ledger 的持久化、事务或恢复保证。定向合同回归
12 passed。`ToolExecutionPort` 进一步定义了冻结 request／result、execution binding、幂等键、超时和
工具终态；`RegisteredToolExecutionPort` 只按显式 port ID 分派且缺失时 fail closed。生产定时任务的
直接工具入口已改由 `KtRegisteredToolExecutionAdapter` 调用工具公开 `execute()`，不再访问
`agent.executor.submit/wait_for/cancel`；ToolPlan、受信 Runtime context、UTF-8 输出上限、data URL
物化／剥离和 ToolCall trace 均保留，显式 tool_call_id 从 Port 一直关联到数据库 Trace。工具合同、注册、
策略、定时 workflow 与 tracing 联合回归 67 passed，另有无 executor 的真实入口集成测试 1 passed。

`CheckpointStore`、`ArtifactPort` 和 `PermissionPort` 已定义不可变合同及进程内参考实现：Checkpoint 按
Run 严格递增并验证 parent、owner 隔离和幂等 ID；Artifact 只接受 owner workspace 内的规范 POSIX
相对路径，按 SHA-256 发布不可变内容并分块读取；Permission 支持 `allow`、`deny`、`ask` 和
`allow-once`，未配置 action 默认拒绝，重复 request ID 只有同一请求可幂等重放。它们不冒充阶段 4／7
才会实现的生产持久化、Event Ledger 事务、session grant、撤销和 ACL 控制面。
`SkillProviderPort` 采用描述快照与内容摘要固定点分离，支持 builtin／project／agent／user scope 和
owner 作用域加载；`McpProviderPort` 只固定 server/tool 二元命名空间、原始 JSON Schema bytes/hash 和
`ToolExecutionPort` binding，不在 Core 合同中放入 transport、endpoint、配置或凭据。实际 Skill
发现／安装、MCP stdio／SSE／HTTP、OAuth、健康检查和配置控制面仍属于阶段 6。`RuntimeCapabilities`
只声明 Runtime 自身真实能力，组合 Provider 由各自 Port 单独预检。三组核心 Port 联合合同回归
25 passed，仅有既有 StarletteDeprecationWarning。

#### 1.4 收敛 Composition Root 和依赖方向

- [x] HTTP、定时任务、主动外呼、research 和 Agent Link 只依赖 Port。
- [x] 把 `creatures/` 与 `api/admin/` 中直接使用 KT 类型的实现拆成工具核心和 KT 包装器。
- [x] 架构门禁禁止 `core/`、`app/`、`api/` 和工具核心新增 KT import。
- [x] KT 私有访问只允许临时存在于有删除期限的兼容文件。

HTTP 与群聊交付层现统一从 `core.agent_runtime.gateway` 解析类型化
`AgentMessageGatewayPort`；隔离定时工作流、主动外呼和 research 分别使用
`ManagedAgentGatewayPort`／`ResearchAgentRuntimePort`，Agent Link 只绑定
`AgentLinkChatPort`。图片预缓存、模型原生工具目录、preset 连通性与 Codex 账号管理也已拆为
框架无关 Port，并由 Composition Root 注入 KT Adapter；`api/` 已不存在
`nanobot_kt`／`kohakuterrarium` import。架构扫描的 KT 边界已从 `core/app` 扩展为
`core/app/api`，包含函数内动态 import，定向 composition-root 回归 14 passed，完整
`scripts/check_architecture.py` 扫描通过。`api/admin` 的直接 KT 类型已经清零。

工具实现已拆为 `app/tool_services/`／`app/persona/update_service.py` 的框架无关应用服务、
`core/tool_contracts/` 的冻结结果与 wire 合同，以及 `nanobot_kt/tools/` 的薄 KT Adapter。
`creatures/**/tool.py` 只保留声明“阶段 3.8 删除”的模块别名；AI Daily 的纯收集／归一化／排序
pipeline 继续留在 creature 核心且不导入 KT。架构门禁会拒绝 creature 工具中的 KT 类型、夹带实现的
旧路径和没有删除期限的 Adapter 别名。

所有已知 KT 1.3 私有字段、方法和 monkey patch 访问已集中到
`nanobot_kt/kt_private_compat.py`，文件明确声明阶段 3.3/3.4 删除；范围包含 Conversation、Controller
队列、Agent 中断、PluginManager、native tool schema、OpenAI transport、Codex Provider 状态和
模型上下文上限。`scripts/check_architecture.py` 同时扫描属性访问与 `getattr/hasattr/setattr` 字符串访问，
禁止其他 `nanobot_kt` 模块重新引入这些私有依赖。新增边界、Codex 和 Provider 回归 48 passed，
Runtime／ToolPlan／Prompt／流式 Bridge 回归 73 passed，行为 Golden 回归 6 passed，完整架构扫描通过。

验收条件：同一套 Runtime 契约测试已运行于 Fake Runtime 和 KT Adapter；Native Runtime 在阶段 3.6
实现后必须加入同一套测试，届时完成第三个实现的验收。

### 阶段 2：声明式 Agent Manifest 与能力预检

#### 2.1 定义 Agent Manifest

- [x] Agent identity、显示信息和 owner。
- [x] Runtime 选择和最低 capability。
- [x] 模型路由、Prompt bundle 和输出合同。
- [x] Skill、MCP、工具和 Hook。
- [x] 记忆策略、workspace、Artifact 和 Sandbox Profile。
- [x] 权限、token、费用、步数、时间和并发预算。
- [x] 评测集、灰度范围、版本、来源和内容摘要。
- [x] 凭据只通过秘密引用，不进入 Manifest 正文。

已新增框架无关的 `core/agent_manifest/`：Manifest 及所有嵌套值对象均为冻结 dataclass，
Runtime 最低能力直接复用 `RuntimeCapability`，owner 直接复用受信 `RuntimePrincipal`；模型只引用
route key，Prompt、输出合同和可选扩展可声明版本与 SHA-256 固定点。状态策略显式引用 memory、
workspace、Artifact 和 Sandbox Profile；预算分别限制 token、费用微单位、步骤、时长和并发。
Manifest 同时包含评测集、门禁、灰度 scope、版本、来源 revision 和按规范 JSON 计算的内容摘要，
声明顺序不同不会改变摘要。凭据结构只暴露 `provider_id + secret_id + binding`，不存在 value、header、
endpoint 或 API key 字段，并拒绝常见 token/JWT/Bearer 明文形态。新增合同回归 7 passed，完整架构扫描
已将 `core/agent_manifest` 纳入无 KT／FastAPI／SQLAlchemy／Provider SDK 依赖门禁并通过。

#### 2.2 编译和预检

- [x] 将 Manifest 编译为不可变运行快照。
- [x] 检查 Runtime 和模型能力。
- [x] 检查工具、Skill、MCP 命名冲突与依赖。
- [x] 检查 workspace ACL、配额、Sandbox Profile 和预算。
- [x] 生成可审计诊断；缺失能力时 fail closed。

`core.agent_manifest.compile_agent_manifest()` 现接收 Manifest 与显式、不可变的编译环境快照，成功时
固定 Runtime capability、模型 Route revision、Prompt／输出合同摘要、扩展目录版本、Workspace／安全
快照摘要和预算上限，并生成自身 SHA-256 的 `CompiledAgentSnapshot`。扩展目录统一检查 Skill 名称、
MCP server 名称、MCP 导出工具与原生工具名、Hook 名称、精确版本、可选内容摘要和 binding 依赖；同一
namespace 的重复导出会失败。Workspace 预检同时验证 owner、ACL、Profile、Memory／Artifact／Sandbox
策略、已应用配额和 Sandbox 就绪状态，安全预检验证 Permission Policy、动作和只含 ID 的秘密引用，
五类预算不得超过部署环境 ceiling。每个阶段产生稳定 severity／code／path／message 诊断；任一 error
都会抛出 `AgentManifestCompilationError`，不会产生部分运行快照，异常文本也不回显 secret ID。
Manifest／编译专用回归 12 passed，与既有 Skill／MCP／Checkpoint／Artifact／Permission Port 联合回归
20 passed，完整架构扫描通过。

验收条件：编译合同已把 Runtime 选择限制为一个独立字段，身份、权限、记忆、扩展和预算固定点不依赖
具体 Runtime；阶段 3.6/3.7 接入 Native 与 KT 双实现后执行真实切换验收。

### 阶段 3：KT 升级、清理和解除硬依赖

#### 3.1 选择升级目标

- [x] 只选择正式稳定 tag。
- [x] 核验 headless/programmatic build、typed turn、typed stream、Conversation、工具和插件注入、模型切换。
- [x] 核验 Python 版本、依赖、许可证和发布活跃度。
- [x] KT `main` 只做 API 试验，不进入生产锁文件。

生产升级目标确定为 KT `v1.4.0`（commit
`9e4ff291814b65423df5e94f4fa3f84dbb82692d`），不是浮动 `main`、nightly 或 prerelease。
该 tag 是 2026-05-11 的 annotated tag；PyPI 同日发布的 sdist 和 wheel 使用 Trusted Publishing，
provenance 均固定到同一 tag commit。上游截至 2026-08-03 仍持续发布 nightly，说明项目仍活跃，但这些
nightly 只作为 API 观察样本，不进入依赖或构建合同。

公开 API 核验结论如下：

- headless/programmatic：`Terrarium.with_creature()`、`Creature.chat()`、`Agent.start()`、
  `Agent.inject_input()` 和 `Agent.set_output_handler()` 均有正式文档与实现；
- Conversation：`append()`、`append_message()`、`to_messages()`、`get_messages()`、
  `truncate_from()` 和 `clear()` 可替代 Nanobot 当前的私有字段访问；
- 扩展：`Registry.register_tool()/unregister_tool()/list_tools()` 与
  `PluginManager.register()/list_plugins()/get_plugin()` 是公开入口；
- 模型：`Agent.switch_model()` 是公开入口；Nanobot 自己的动态路由与账号池仍由 Adapter 管理；
- typed turn：上游只有私有 `_TurnResult`，没有可依赖的公开单轮结果合同；
- typed stream：`Terrarium.subscribe()` 提供 `AsyncIterator[EngineEvent]`，但
  `Creature.chat()` 仅提供 `AsyncIterator[str]`，且 `v1.4.0`、`v2.0.0` 都没有公开 `run_stream()`。
  因此类型化 Turn/Stream 继续以 Nanobot 的 `RuntimeRunEvent`、`RuntimeRunResult` 为规范合同，
  KT 事件只在 Adapter 内转换。

没有选择同为正式版的 `v2.0.0`：它要求 `pydantic>=2.12,<2.13`，与本项目当前生产和测试锁中的
`pydantic==2.13.4`、`pydantic-core==2.46.4` 冲突；同时新增认证、分布式宿主和移动端打包相关硬依赖，
而 `v1.4.0..v2.0.0` 涉及 1411 个文件，不属于本轮 KT Adapter 清理所需能力。`v1.4.0` 仅新增本项目
已经直接依赖的 Pillow，Python 要求仍为 `>=3.10`，兼容当前 Python 3.11 生产镜像和 Python 3.12
测试环境。

`v1.3.0`、`v1.4.0`、`v2.0.0` 的 `LICENSE` 内容摘要完全相同（SHA-256
`44dd2da754bb5f3319ecf7283195b08337c2567fedf3cf482f3da2fcc5f83132`）：它不是标准 Apache-2.0，
而是带命名与可见归属要求的 `KohakuTerrarium License 1.0`。升级不会新增许可证变化，但部署或分发
仍须单独确认命名与归属合规；这也是阶段 3.8 将 KT 收敛为可选 Runtime、避免业务核心派生于 KT 的
明确约束。

#### 3.2 完成行为等价升级

- [x] 在 `nanobot_kt` 内迁移构造、消息、输出、插件、工具、模型和中断。
- [x] 先通过阶段 1 的现有行为基线。
- [x] 不在本步骤改变历史、Prompt、ToolPlan、模型路由和持久化语义。

KT 可选依赖已在 `requirements-kt.in` 与 `requirements-kt.lock` 中固定到 `v1.4.0` commit
`9e4ff291814b65423df5e94f4fa3f84dbb82692d`；阶段 3.8 完成后不再从本地 submodule 安装。构造和生命周期统一
经 `KtRuntimeAdapter`，conversation、工具目录、事件注入和中断只调用公开 API；模型路由改为按公开
Provider 构造器创建新实例并替换公开引用，ToolPlan wire schema 由 Provider Adapter 在公开 `chat()`
边界注入。Codex 多账号实现已收回 `nanobot_kt/codex_provider.py`，不再继承或改写 KT Codex Provider
私有状态。阶段 1 行为 Golden 为 6 passed；升级后的 Runtime／KT Bridge／Prompt／ToolPlan／research／
Codex 联合矩阵为 372 passed，仅有既有 Starlette 弃用警告。

#### 3.3 清理为少改 KT 而存在的妥协

- [x] 使用公开 `Agent.inject_event()` 和输出事件边界，不假设上游存在 `run_stream()`。
- [x] 使用公开 Conversation API。
- [x] 使用公开 PluginManager、模型、工具和插件注入 API。
- [x] 删除 `_process_event` 访问。
- [x] 删除 `_messages`、`_metadata` 和 `_maybe_truncate` 访问或 monkey patch。
- [x] 删除 `_pending_events`、`_pending_injections` 和 `_event_queue` 操作。
- [x] 删除 `_output_module`、`_get_native_tool_schemas` 和 Provider 私有状态依赖。

普通输入选择公开 `Agent.inject_event()`，而不是 `Agent.inject_input()`：Nanobot 必须把受信 stream／actor／
owner 上下文绑定到事件，同时不能让以 `/` 开头的普通 HTTP 消息意外进入 KT 自带 slash-command 解释器；
继续工具轮次也必须使用不追加新 user message 的公开自定义事件入口。该选择仍属于上游正式公开 API，且
保留 Nanobot 现有消息语义。`kt_private_compat.py` 和旧 `kt_adapter.py` 已删除，架构扫描不再保留临时例外，
当前 `scripts/check_architecture.py` 严格扫描通过。

#### 3.4 删除本地补丁型行为

- [x] 删除 KT `Message.stream` 补丁。
- [x] 删除构建期 stream patch 和应用脚本。
- [x] 删除对 BufferedOutput 私有缓冲区的读取。
- [x] 删除按文本标记剥离 KT framework prompt 的逻辑。
- [x] 删除 Controller/Conversation monkey patch 和请求结束清队列逻辑。
- [x] 流式行为由 KT 公开输出在 Adapter 内转换为 Nanobot 类型化事件，不读取私有缓冲区。

`patches/kohakuterrarium/stream-message-flag.patch`、`scripts/apply_kohaku_patches.sh`、Docker `kt-source`
补丁阶段以及两个 CI 工作流的补丁步骤已删除；可选 KT 直接从已固定的上游 commit 安装，默认
生产镜像不再接收 KT 源码或补丁上下文。Bridge 的流式请求改调
`AgentRuntimePort.run_event()`：`BufferedOutput` 的公开输出信号先由 KT Adapter 转成 `RuntimeRunEvent`，
再投影为既有 SSE 字典协议；运行期错误、富结果和最终回复均不再直接读写 `_buffer`。输出／流式／构建／
清理定向回归 20 passed。

#### 3.5 收回 Nanobot 业务语义

- [x] HTTP 无状态轮次和 `ConversationTurn` 历史由 Nanobot 管理。
- [x] canonical Prompt Runtime 和 Context 压缩由 Nanobot 管理。
- [x] per-request ToolPlan 和 wire schema 由 Nanobot 管理。
- [x] 动态模型路由、熔断和 Codex 多账号池由 Nanobot 管理。
- [x] 请求级事件协议和定时任务确定性执行不依赖 KT 私有对象。

KT conversation 配置固定为 `max_messages: 0`，每个 HTTP 请求由 Bridge 通过公开 Conversation Port 清空并
重建 canonical Prompt Runtime 产物；KT compact manager 同时禁用，避免第二套截断规则改变消息顺序。
Prompt Runtime 默认模板和运行时模板审计未发现 KT、framework prompt、`max_messages` 或私有截断行为的
过时陈述，现有 `<conversation_context>` 与 user／assistant 历史注入说明仍准确，因此本阶段无需改模板。
ToolPlan、模型候选／熔断、Codex 账号健康和确定性工具执行均保留 Nanobot 事实源。

#### 3.6 实现最小 NativeAgentRuntime

- [x] 基于 `ChatCompletionPort`、`ToolExecutionPort`、Prompt Runtime 和 ToolPlan。
- [x] 实现有界模型—工具循环。
- [x] 实现流式事件、取消、超时、usage、重试和错误归一化。
- [x] 先覆盖主回复链路，不复制 KT 的全部功能。
- [x] 与 KT Adapter 运行同一套合同测试。

`core/agent_runtime/native.py` 只组合框架无关模型／工具 Port：Bridge 生成的 canonical Prompt Runtime
conversation 通过稳定 Conversation Port 输入，请求引用的 ToolPlan 摘要必须与当前冻结计划一致后才发送
wire schema 和执行工具。Runtime 对模型步数、工具轮数、单步重试、整轮／模型路由／deadline 超时设置硬上限；
只在尚未产生流式输出时重试模型，部分流已输出后中断则进入 `ambiguous`，终止型回复工具失败不会误报成功。
多轮模型 usage 按整个 Turn 累计，停止会进入 `STOPPING` 并等待活跃 Turn 取消。Native 与 KT 已在同一测试函数
中运行生命周期、能力、conversation 和执行合同；Phase 3 回归矩阵 387 passed，行为基线 6 passed，架构扫描与
定向 Ruff 检查均通过。

#### 3.7 支持双 Runtime 和安全切换

- [x] Composition Root 支持 Native 默认、KT 可选和按灰度范围选择。
- [x] Runtime 不可用时显式 fail-fast。
- [x] 有副作用 Turn 不得在无记录的情况下跨 Runtime 自动重试。
- [x] 切换 Runtime 必须产生事件并保持幂等边界。

Composition Root 在启动时冻结 `AgentRuntimeSelectionPolicy`：生产默认选择 Native，
KT 只能在显式启用后通过精确 session allowlist 或稳定万分比灰度命中；直接构造
Bridge／Pool 的旧调用继续保留 KT 兼容默认，不会偷偷改变测试或外部嵌入语义。每个请求只选择
一次 Runtime，已选定 Runtime 失败后不会跨 Runtime 回退；切换会先拒绝正在执行的同 session
Turn，再停止旧 Bridge，并发出只含 session／policy hash 而不含原始标识的
`agent.runtime_selection` 事件。

Native 的 OpenAI-compatible 主回复使用独立 `ReplyRouteChatCompletionAdapter`：路由在请求内冻结，
Anthropic、Codex、Provider-native tools 和不安全的保留 `extra_body` 均在模型／工具副作用前
fail-fast。Native 主回复已用进程内伪模型完整跑通“路由 → 模型 → ToolPlan → reply
工具 → 回复合同”。阶段 3.7 结束时保留的“Native 临时启动 KT Agent 来注册／执行工具”过渡边界
已在 3.8 删除；Native 现在通过独立应用服务与 `bootstrap/native_tool_runtime.py` 组装工具，不再创建 KT Agent。

验证证据：完整 `python -m pytest tests/ -v` 为 6479 passed、12 skipped、0 failed；Prompt
测试簇 493 passed；行为 Golden、Release Impact Golden、Verification Plan Golden、架构边界、
Ruff 和 `git diff --check` 全部通过。Runtime 选择和传输未改变 conversation、历史注入、
工具输出或 chat Prompt Runtime 输入合同，因此无需修改 chat 模板；全量验证发现的
`session_summary_output` 默认／运行时模板版本漏同步已一并修正。

#### 3.8 删除 KT submodule 和构建硬耦合

- [x] 删除 `.gitmodules` 和 `vendor/KohakuTerrarium/`。
- [x] 删除 KT patch、apply 脚本和子模块状态校验。
- [x] KT 改为独立可选依赖集，不进入核心依赖或默认生产镜像。
- [x] 更新 requirements、锁文件、Dockerfile、Compose 和 CI checkout/install。
- [x] 更新 release manifest、构建脚本和部署文档。
- [x] 未安装 KT 时，Native Runtime 与非 Agent 业务仍能导入、启动和测试。

实现与验证证据：

- 核心依赖不再包含 KT；`requirements-kt.in` 固定远程 v1.4.0 commit，
  `requirements-kt.lock` 在核心测试锁约束下独立生成。实际安装后为非 editable 的
  `KohakuTerrarium 1.4.0`，加载路径位于 site-packages，不指向仓库 `vendor/`。
- Native 工具注册、执行、图片 Provider、新闻 Provider 和 research composition 均经框架无关 Port 组装；
  `core/`、`app/` 和 `api/` 不 import KT，旧 creature `tool.py` 别名实现已删除。
- `tests/test_native_without_kt.py` 在新子进程阻断 KT 和全部 optional-only import root，实际启停
  Model Runtime 与 Server，并跑通 Native 回复；该验证反向发现并补齐了核心依赖 `jsonschema`。
- 默认生产 Docker 构建上下文为 3.66 MB，不包含 submodule、KT 锁或可选文档；实际构建镜像
  `nanobot-runtime:phase-3-8-verify`（image ID
  `sha256:592e8e3c2a21cef621b3fa58cbe0958b7d98c88f4530400bc1edd0a19b028968`，640070812 bytes）。
  镜像在 `network=none`、只读根文件系统与受限 tmpfs 下以 UID 10001 启动，确认无 KT 包、
  无 vendor、无 `.gitmodules` 且 Server／Model Runtime 可正常启停。
- `docs/kt-runtime-compatibility.md` 记录了可选安装、显式启用、兼容套件、升级、回滚和非标准
  `KohakuTerrarium License 1.0` 注意事项；Release Impact 使用 `kt_compatibility` 而非 submodule SHA 作为边界。
- 形式 KT 兼容套件 97 passed；无 KT／部署组合回归 118 passed；媒体与新闻 Port 回归 6 passed；
  最终完整 `python -m pytest tests/ -v` 为 6474 passed、12 skipped、0 failed；架构、行为基线、
  Release Impact、Verification Plan、决策规则清单、本阶段代码 Ruff、Python 编译和 diff 检查均通过。

验收条件已满足：业务层不 import KT；正常请求不访问 KT 私有成员；生产构建不要求 Git submodule；
Native Runtime 能独立提供主回复链路。从工作区移除的 submodule 仍有可恢复备份，未执行不可恢复删除。

### 阶段 4：事件账本、恢复、长期任务和 Artifact

#### 4.1 Append-only Run/Event Ledger

- [x] 记录请求接纳、状态迁移、模型调用、工具调用、权限决定、usage、Artifact、交付和终止。
- [x] 事件不可原地修改；纠正使用后续事件表达。
- [ ] 会话、管理端、恢复和模型上下文通过投影生成。
- [x] 事件 schema 带版本和迁移策略。

阶段性实现证据（第三项仍需随恢复与 Context Engine 完成）：

- 新增独立于可丢弃 Telemetry 的 `core/run_ledger/` 合同、SQLAlchemy Adapter 和
  `run_ledger_events`／`run_ledger_stream_heads` 迁移。事件按 Run 分配严格递增 sequence，
  使用 event ID 幂等、payload／事件摘要和前向 hash chain；同一 Run 不得切换 owner，终止后只能追加
  `run.event_corrected`。SQLite 迁移通过数据库 trigger 直接拒绝事实表的 UPDATE／DELETE。
- `RunTracer` 在同一数据库事务中写入接纳、running、Prompt 固定点、usage 和终止事实；带 Run 关联的
  `model.request`、`tool.execute` 等净化 Runtime Event 与类型化 Runtime Run Event 均先通过权威 Sink
  入账。真实 Sandbox 权限决定在工具副作用前提交，Artifact 登记与不可变资产事实共用事务；聊天和
  主动外呼的每次交付 Attempt 使用独立 Run，保留来源 Run 关联并将成功、失败、取消或不确定结果终态化。
  请求接纳、状态、模型、工具、权限、usage、Artifact、交付和终止已形成完整生产接线。
- 管理端通过固定 high-water、完整分页和摘要链校验生成权威 Run 投影；状态、时间、筛选和排序不再读取
  legacy `AgentRun` 当前值。独立交付 Run 即使没有 `AgentRun` 行也可列出、查看详情和分页事件；只有
  迁移前完全没有 Ledger 的旧记录才显式标记为 `legacy_compat`。会话、恢复和模型上下文仍需在后续
  Checkpoint／Context Engine 切片改由 Ledger 投影生成，因此第三项继续保持未完成。
- 第二个 shadow 切片把 Prompt Runtime 解析完成后的 mode、key、Prompt hash 与模板解析清单 hash
  作为 `run.prompt_resolved` 后续事实，与 legacy `AgentRun` Prompt 头在同一事务中双写；模板正文、
  runtime/default 路径和解析清单原文不进入 Ledger。投影现在可重建安全的 Prompt／模型／工具／usage／
  Artifact context manifest，并在管理端对 legacy header 给出 `projection_consistent` 与稳定 reason code。
  legacy readiness 现只作为迁移审计信息，不能覆盖 Ledger 投影。
- 带 Run 的 Runtime Event、类型化 Runtime Event、接纳、Prompt 固定点、工具前权限决定、Artifact 和
  终止事实均采用 fail-closed 权威屏障；Ledger 失败不会进入模型重试、Provider 熔断或普通工具错误归一化。
  请求清理和 Trace Context 在终止入账失败时仍完整释放。无 Run 的纯观测事件继续 fail-open。
  当前切换只保证事实先行和提交不确定 read-back；完整恢复及外部副作用 exactly-once 仍属于 4.3／4.4。
- 验证证据：Ledger／Telemetry／Runtime Event／管理端 Trace／LLM Trace／Schema Migration／Release Artifact
  联合回归 126 passed；Bridge 关联上下文与 Ledger 联合回归 36 passed；最终完整
  `python -m pytest tests/ -v` 为 6483 passed、12 skipped、0 failed。架构边界、OpenAPI 生成物、
  行为基线、Release Impact Golden、Verification Plan Golden、决策规则清单、Task SLO Manifest、
  致命静态错误检查、定向 Ruff、Python 编译和 `git diff --check` 均通过。
  第二个 shadow 切片的 Ledger／Trace／Telemetry／Migration／Bridge 联合回归为 155 passed；对应架构、
  OpenAPI、行为基线、Release／Verification Golden、决策规则和静态检查再次通过。
- 权威切换切片的 Ledger／Runtime Event／Bridge／Native Runtime／权限／Artifact／投递／管理端联合回归
  为 252 passed；最终完整 `python -m pytest tests/ -v` 为 6497 passed、12 skipped、0 failed。
  管理端漂移测试证明 legacy 状态和时间不能覆盖 Ledger；模型或工具入账失败不会触发第二次模型调用、
  Provider 健康度变更或普通工具错误归一化。

#### 4.2 隐私安全的运行证据

- [x] 默认记录 hash、大小、时间、状态、模型/工具标识和脱敏摘要。
- [x] 不记录 API key、OAuth token、隐藏推理和无必要的完整工具输出。
- [ ] 提供保留期、ACL、导出清单和删除策略。

上述两项仅指新 Run Ledger 合同：payload 只允许有界 JSON scalar，敏感正文类字段名在合同层拒绝，
输入、输出、错误、工具参数／结果和权限资源均只写 UTF-8 大小、字符数与 SHA-256；测试覆盖了原文不落账。
既有 LLM／Tool Trace 仍按原兼容合同保存自己的诊断内容，不因 Ledger shadow 双写而自动满足新的保留、
ACL、导出或删除要求；这些治理能力及旧证据迁移仍属于 4.2 后续任务。

#### 4.3 Checkpoint、Resume、Fork 和 Rewind

- [ ] 在 Turn、计划和安全工具边界保存 checkpoint。
- [ ] 保存 workspace、Manifest、Prompt、模型、工具和 Artifact 的版本证明。
- [ ] 恢复前验证权限、版本、文件状态和 side-effect receipt。
- [ ] 外部副作用结果未知时进入 ambiguous 状态，不自动重放。
- [ ] Fork 保留原运行事实，并产生新的 run lineage。

#### 4.4 Durable Task 和租约恢复

- [ ] 统一聊天长任务、定时任务、主动外呼、research 和后台 Agent Run。
- [ ] 使用 lease、heartbeat、owner fencing、cancel、timeout 和 reconcile。
- [ ] 同一 Run 只能有一个有效执行 owner。
- [ ] 客户端重连只恢复视图，不重复启动任务。
- [ ] 投递使用幂等 receipt。

#### 4.5 Artifact 生命周期

- [ ] 工具和模型先写 owner workspace。
- [ ] 发布时生成 hash、MIME、大小、来源 Run、ACL 和不可变版本。
- [ ] 预览、下载、消息渲染和跨会话引用只通过 `ArtifactPort`。
- [ ] 不把 base64 或宿主真实路径写进消息历史。

验收条件：可重建任意已保留 Run 的状态和可见上下文；恢复不会重复有副作用操作；大结果不再依赖消息正文保存。

### 阶段 5：Context Engine、前缀缓存和 Plan Mode

#### 5.1 Context 分层

- [ ] 固定 system、安全、工具合同和稳定策略段。
- [ ] 分离 session、user、group、project 动态上下文。
- [ ] 分离近期对话、记忆召回、工具结果和摘要。
- [ ] 为各层设置独立 token 预算和溯源。
- [ ] 保持 `ChatLog`、`ConversationTurn` 和模型上下文三者分离。

#### 5.2 前缀缓存稳定

- [ ] 固定 Prompt section 和工具 schema 顺序。
- [ ] 将请求级动态内容放在稳定前缀之后。
- [ ] 避免时间戳、随机排序和无意义 request metadata 破坏缓存。
- [ ] 按 Provider 记录 cached/uncached token、命中率、首 token 延迟和成本。
- [ ] 缓存优化不得改变权限、事实和 Prompt Runtime 的 canonical 顺序。

#### 5.3 分层压缩和工具输出治理

- [ ] 实现 `notice → snip/prune → summary → hard limit` 水位。
- [ ] 保持 assistant tool call 与 tool result 配对。
- [ ] 大结果先发布为 Artifact，再向 Context 注入摘要和引用。
- [ ] 增加 Unicode 清洗、边界标记、注入风险标注和安全截断。
- [ ] 保存压缩原因、前后 token、保留项和质量评测证据。

#### 5.4 Session Goal 和 Plan Mode

- [ ] 为长任务定义目标、完成条件、预算和状态。
- [ ] Plan Mode 只允许读取和写入计划资产。
- [ ] 退出 Plan Mode 并获批准后才允许实施工具。
- [ ] 工具可见性和写权限由服务端策略控制，不靠 Prompt 自律。

验收条件：相同稳定输入产生可预测的 Prompt 前缀；缓存收益可量化；压缩后关键事实、工具配对和安全说明不丢失。

### 阶段 6：Skill、MCP、Hook 和协议兼容

#### 6.1 Agent Skills / SKILL.md 兼容

- [ ] 支持内置、项目、Agent 和用户作用域。
- [ ] 定义发现优先级和冲突策略。
- [ ] 校验 YAML 元数据、版本、依赖和权限要求。
- [ ] 支持按需加载、版本 pin、lock、升级、回滚和卸载。
- [ ] 不执行来源不明的自动安装器或命令注入。

#### 6.2 Skill 检索和治理

- [ ] 用 Registry 和现有 RAG 建立 Skill 描述索引。
- [ ] 记录能力标签、权限、依赖、Prompt 成本和适用范围。
- [ ] 延迟注入 Skill 正文和工具 schema。
- [ ] 记录每个版本的调用、成功率、成本和评测结果。

#### 6.3 MCP 控制面

- [ ] 支持 stdio、SSE 和 HTTP transport。
- [ ] 支持 OAuth 与请求级秘密引用。
- [ ] 提供健康检查、启停、超时、重连和诊断。
- [ ] 工具名默认带 server namespace，并检测冲突。
- [ ] 配置原子替换；单个坏 MCP 不阻塞其他 MCP。
- [ ] 秘密不得进入日志、metadata、子 Agent 或 Sandbox。

#### 6.4 Hook / Plugin 生命周期

- [ ] 支持 pre/post model、pre/post tool、event、interrupt 和 completion Hook。
- [ ] 声明顺序、超时、失败策略、可读字段和可修改字段。
- [ ] Hook 默认不能绕过 ToolPlan、Permission、Prompt Runtime 和 Event Ledger。
- [ ] 插件异常必须形成可诊断事件。

#### 6.5 ACP、A2A 和 Headless 互操作试验

- [ ] 评估 ACP 的 session、stream、tool activity 和 pending interaction 映射。
- [ ] 评估 A2A 的 task、artifact 和状态交换。
- [ ] 在 Agent Link 之外只实现薄 Adapter。
- [ ] 内部事实源仍是 Nanobot Run/Event/Artifact 合同。
- [ ] 通过兼容性和安全测试后再决定是否正式开放。

验收条件：Skill、MCP 和协议均可替换或停用；不存在凭据泄漏、隐式覆盖和绕过权限的扩展路径。

### 阶段 7：权限、Sandbox、身份、工作区和记忆

#### 7.1 统一权限和预算

- [ ] 为 Run、Turn、Tool 和 Subagent 声明模型、token、费用、步数、时间和并发预算。
- [ ] 声明文件、网络、工具、Skill、MCP 和记忆访问范围。
- [ ] `PermissionPort` 支持 `allow`、`deny`、`ask`、`allow-once` 和 session grant。
- [ ] 所有决定进入 Event Ledger，并支持撤销临时授权。

#### 7.2 Sandbox 和工具安全

- [ ] `sandboxd` 继续独占 Docker Socket。
- [ ] 服务端决定镜像、挂载、网络、capability 和资源上限。
- [ ] 验证非 root、只读根、默认断网、无 Docker Socket 和超时终止。
- [ ] 验证 workspace 持久化和 owner 之间不可互读。
- [ ] 不引入 OpenSandbox、OpenShell、E2B 运行时依赖。

#### 7.3 Agent Identity、Workspace 和 ACL

- [ ] Agent identity 与 KT、Native 或远程 Runtime 解耦。
- [ ] 明确 user、group、project、session 和 agent owner。
- [ ] 明确私有和共享 workspace、ACL、配额和生命周期。
- [ ] 切换 Runtime 不丢失身份、授权和记忆引用。
- [ ] 模型只能看到容器内虚拟路径。

#### 7.4 记忆质量和作用域

- [ ] 在现有数据模型上明确 working、episodic 和 semantic 层次。
- [ ] 记录证据来源、置信度、冲突、衰减、删除和注入预算。
- [ ] 区分 Agent 私有、用户私有、群组共享和项目共享记忆。
- [ ] 知识图谱或新后端必须先通过真实中文会话评测。
- [ ] 不因外部项目宣称直接替换当前 RAG 和画像治理。

验收条件：每次数据、工具、Sandbox 和子 Agent 访问都能解释“谁以什么授权访问什么资源”。

### 阶段 8：有界多 Agent 编排和协作

#### 8.1 显式 DAG 编排

- [ ] 定义角色、任务、依赖、输入、输出合同和完成条件。
- [ ] 定义预算、并发、审批、取消、汇总和 checkpoint。
- [ ] Worker 不直接 peer-to-peer。
- [ ] 服务端拒绝无限递归和无预算 spawn。
- [ ] 单 Agent 主链路继续作为默认模式。

#### 8.2 子 Agent 权限和模型分工

- [ ] 子 Agent 只继承最小 workspace、工具、网络、Skill、MCP、记忆和预算。
- [ ] 探索和检索任务可使用低成本模型。
- [ ] 验证和裁判使用独立高质量模型。
- [ ] 子 Agent 通过结构化 output contract 返回结果。

#### 8.3 计划批准、调度和修复

- [ ] 支持动态计划的 preview、approve 和 freeze。
- [ ] 显式 DAG 使用确定性调度。
- [ ] 支持 task barrier、局部重试和 append-only plan repair。
- [ ] 修改已批准计划会生成新版本和审计事件。

#### 8.4 人机与多 Agent 协作入口

- [ ] 先复用群聊、Agent Link 和现有任务表。
- [ ] 支持 `@agent`、任务认领、交付物和人工审批。
- [ ] 评估 room、pod、task board 和 handoff 数据模型。
- [ ] 本阶段不复制 Commonly、LobeHub 或 Orca 的完整 UI。

验收条件：多 Agent 运行有确定预算、权限、结束条件、恢复点和责任归属；关闭多 Agent 后不影响现有单 Agent 行为。

### 阶段 9：Gateway、主动能力和 Provider 治理

#### 9.1 多渠道 Gateway 与远程会话控制

- [ ] 统一 QQ、Web、Agent Link 和未来 IM 的 session binding。
- [ ] 支持状态、pending approval、pending question、stop、resume 和 model switch。
- [ ] 远程客户端只能控制已有授权 Run。
- [ ] 任何远程操作不得绕过身份、ACL、ToolPlan 和 Sandbox。

#### 9.2 主动能力和 Sentinel 收敛

- [ ] 将主动外呼、定时任务、事件触发和 heartbeat 统一为 `Trigger → Evaluate → Lease → Run → Deliver`。
- [ ] 默认关闭并保留冷却、预算、幂等和 ambiguous 冻结。
- [ ] 保存用户反馈和运行证据，供后续评测。
- [ ] 不允许主动任务自行扩大权限。

#### 9.3 Provider 诊断和成本治理

- [ ] 在现有模型目录、能力过滤、排序和熔断上增加连接诊断。
- [ ] 描述请求协议、stream、tool、image、reasoning 和 cache 能力。
- [ ] 记录首 token 延迟、总延迟、token、缓存、成本和错误类别。
- [ ] 路由只依据可验证 Descriptor 和运行证据，不依据模型名猜测。

验收条件：不同入口共享同一 Run 语义；Provider 能力、费用和故障均可观测、可验证、可回退。

### 阶段 10：可观察性、评测和受控自进化

#### 10.1 统一 Trace 和离线 Run Viewer

- [ ] 将 LLM、Prompt、Tool、Memory、MCP、Sandbox、Subagent、Cache、Artifact 和 Delivery span 关联到 Run/Turn。
- [ ] 提供脱敏时间线、DAG、token/cost waterfall 和上下文 manifest。
- [ ] 显示失败点、重试、恢复和版本，不展示隐藏推理。

#### 10.2 回放、对比和故障注入

- [ ] 使用冻结 Event 和模型替身进行确定性回放。
- [ ] 支持 Runtime、Prompt、模型、Skill 和 Context 策略 A/B diff。
- [ ] 注入超时、断流、工具失败、DB 锁、lease 丢失和 Sandbox 重启。
- [ ] 验证恢复不会重复副作用。

#### 10.3 扩展评测门禁

- [ ] Runtime 合同和 Native/KT 等价评测。
- [ ] Prompt 稳定、缓存和 Context 压缩评测。
- [ ] 记忆注入、Skill 选择和 MCP 评测。
- [ ] 权限、恢复、成本和长任务评测。
- [ ] 多 Agent 完成率、协作成本和失败传播评测。
- [ ] 区分离线确定性 gate、真实模型 benchmark 和线上只读采样。

#### 10.4 受控自进化

- [ ] 只允许离线生成候选。
- [ ] 冻结 baseline、训练集、验证集和测试集。
- [ ] 经过安全、成本和质量门禁。
- [ ] 人工批准后再灰度。
- [ ] 允许优化 Prompt、Skill、路由和有限 Manifest 字段。
- [ ] 禁止生产 Agent 直接修改、提交或批准主干代码。

#### 10.5 经验提取和 Skill 候选

- [ ] 从成功和失败 trajectory 中离线提取流程、失败模式和 Skill 草案。
- [ ] 去重、脱敏，并保留来源 Run 和评测证据。
- [ ] 候选进入独立区域，不直接覆盖正式 Skill。
- [ ] 通过独立评测和人工批准后才能发布新版本。

验收条件：任何自动优化都能回答“基于哪些数据、比哪个基线好、花费多少、谁批准、如何回滚”。

### 阶段 11：波次交付、排除项复核和最终收口

#### 11.1 明确延后或排除

- [ ] 桌面/TUI 重做。
- [ ] 通用低代码平台和完整 Agent 市场。
- [ ] Orca 式代码 worktree fleet。
- [ ] PostgreSQL 多租户迁移。
- [ ] Kubernetes Sandbox、Computer Use 和全局知识图谱。
- [ ] 只有出现明确需求和评测收益后，才为这些项目另立计划。

#### 11.2 分波次交付

1. **Wave A：** Runtime 合同、KT 升级与去妥协、Native Runtime、解除 submodule。
2. **Wave B：** Event Ledger、Checkpoint、Artifact、Durable Task 和 Context Engine。
3. **Wave C：** Skill、MCP、Hook、权限、身份、workspace 和记忆作用域。
4. **Wave D：** 多 Agent、ACP/A2A 试验、Gateway 和主动能力收敛。
5. **Wave E：** 评测驱动的经验提取和受控自进化。

每个 Wave 必须独立完成：

- shadow 或只读验证；
- 有限用户/会话灰度；
- 明确回滚入口；
- 删除无继续价值的临时兼容代码；
- 同步对应测试和事实文档。

#### 11.3 最终验证

- [x] 运行直接相关的定向测试。
- [x] 运行 `python -m pytest tests/ -v`，要求 0 failures。
- [x] 运行 `python scripts/check_architecture.py`。
- [x] 运行 `git diff --check` 和必要的 Python 编译检查。
- [x] 验证依赖锁、Docker 镜像、Compose 配置和 CI。
- [ ] 涉及 WebUI 时运行 lint、build 和相关静态测试。
- [ ] 在真实部署宿主验证 Sandbox 隔离矩阵。
- [x] 验证未安装 KT 时 Native Runtime 和非 Agent 功能可正常启动。
- [ ] 验证 KT/Native 行为基线、恢复幂等、缓存收益和权限闭环。
- [ ] 所有能力成为运行事实后，再更新 README、运维和迁移文档。

## 6. KT 清理专项验收清单

KT 相关工作完成时，至少满足：

- [x] 业务代码不直接 import `kohakuterrarium`。
- [x] 不在正常路径访问 `_process_event`。
- [x] 不访问 `_messages`、`_metadata` 或 `_maybe_truncate`。
- [x] 不清理 `_pending_events`、`_pending_injections` 或 `_event_queue`。
- [x] 不 monkey patch `_get_native_tool_schemas`。
- [x] 不读取或复制 Provider 的 `_client`、`_tokens` 等私有状态。
- [x] 不再维护 KT `Message.stream` patch。
- [x] 不再通过字符串标记删除 KT Prompt。
- [x] 定时工具执行不借用 `agent.executor`。
- [x] `nanobot_kt` 只保留公开 API Adapter 和必要类型映射。
- [x] 没有 KT 包和 submodule 时，Native 主链路仍可运行。

## 7. 架构与质量门禁

后续实施需要逐步增加以下机器检查：

- KT import 只能出现在可选 Adapter 包。
- Runtime 合同不能反向依赖 Adapter。
- Tool、Skill、MCP、Provider 和 Prompt Registry 冻结后禁止隐式覆盖。
- Event schema、Manifest、Artifact 和 Checkpoint 都必须带版本。
- 任何外部副作用必须有幂等键或明确的 ambiguous 策略。
- Prompt Runtime 输入变化必须检查默认模板和运行时模板。
- 新增持久目录必须检查 `.dockerignore`、配额、备份和 owner ACL。
- 新增 Sandbox 能力必须通过真实隔离验证，不能只检查配置文本。
- 自进化候选不能绕过测试、评测、人工批准和灰度。

## 8. 计划维护规则

- 完成项必须附实际命令、测试结果、源码证据或运行证据。
- 外部项目版本变化后，不直接覆盖旧结论；新增带日期的复核记录。
- 计划任务发生拆分时，保留原任务与新任务的对应关系。
- 相邻问题只记录，不未经授权扩大实施范围。
- 用户未明确要求提交前，始终保持未提交状态。
- README 只描述已经实现且通过验证的能力，不承担候选路线或调研笔记职责。
