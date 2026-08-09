# Nanobot Agent Harness 生态调研与优化总路线

> 状态：执行中（阶段 10.2 已完成，准备阶段 10.3）
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
- [x] 提供保留期、ACL、导出清单和删除策略。

上述两项仅指新 Run Ledger 合同：payload 只允许有界 JSON scalar，敏感正文类字段名在合同层拒绝，
输入、输出、错误、工具参数／结果和权限资源均只写 UTF-8 大小、字符数与 SHA-256；测试覆盖了原文不落账。
既有 LLM／Tool Trace 仍按原兼容合同保存自己的诊断内容，不因 Ledger shadow 双写而自动满足新的保留、
ACL、导出或删除要求。阶段 4.2 已新增框架无关的精确 owner／service／admin ACL、成功／失败／不确定终态
差异化保留期、安全导出清单、法律保留和受控删除：owner 不明的 legacy Run 只允许管理员读取，Service
身份必须精确绑定单个 Run 且不能删除；导出只包含 Ledger 事件元数据、摘要链、投影以及旧 Trace 的按表
数量和聚合摘要，不导出 Ledger payload、旧 Trace 行、隐藏推理或秘密值。

删除必须针对终态 Run，法律保留时一律拒绝；保留期到期和隐私请求使用不同的显式 reason code，并同时
要求 request ID、原始 Run ID 二次确认和当前导出清单 SHA-256。SQLite trigger 继续默认拒绝 Event／Head
删除，只在五分钟短事务授权存在时允许治理服务删除完整流；服务在同一事务中核对 high-water、删除
Ledger、AgentRun、Tool、Prompt、LLM、Reply Contract 和 Runtime Telemetry 证据，并写入只含 Run／请求
哈希、数量、策略版本和终态事件摘要的永久回执。重复请求只重放同一回执，变更后的清单、复用 request ID、
活跃 Run、过期授权、部分删除和跨 owner 访问均 fail closed。

验证证据：运行证据治理、Ledger、Schema Migration、受管设置、管理路由、OpenAPI、行为 Golden 和
Release Artifact 联合回归 113 passed；单独治理测试 9 passed；最终完整
`python -m pytest tests/ -v` 为 6507 passed、12 skipped、0 failed。真实 SQLite migration 下已验证普通删除、
协调头删除和过期授权删除均被 trigger 拒绝，治理服务可在受控授权事务内完整删除且不遗留授权行；安全
导出与删除回执测试确认旧 Trace 正文和 Authorization 内容不会进入清单或永久回执。

#### 4.3 Checkpoint、Resume、Fork 和 Rewind

- [x] 在 Turn、计划和安全工具边界保存 checkpoint。
- [x] 保存 workspace、Manifest、Prompt、模型、工具和 Artifact 的版本证明。
- [x] 恢复前验证权限、版本、文件状态和 side-effect receipt。
- [x] 外部副作用结果未知时进入 ambiguous 状态，不自动重放。
- [x] Fork 保留原运行事实，并产生新的 run lineage。

实现与验证证据：

- 新增框架无关 `RuntimeRecoveryPort`、版本化 Checkpoint 合同与 SQLAlchemy 权威 Adapter；Native Runtime
  在 Turn 开始、计划解析、工具执行前后、ambiguous 和 Turn 完成边界保存不可变状态。Checkpoint 与对应
  Ledger 事实在同一短事务中提交，按 Run 使用严格递增 sequence 和父 Checkpoint 链；SQLite trigger
  拒绝普通 UPDATE／DELETE，治理删除继续复用阶段 4.2 的短时授权与永久回执。
- Checkpoint 使用有界 canonical JSON 与确定性 gzip，保存模型可见 conversation、冻结模型路由、工具轮次和
  side-effect frontier；常见凭据字段与内联 token 在编码前直接替换为脱敏标记，不保留秘密派生摘要。
  Manifest、Prompt、精确候选模型路由、ToolPlan、Workspace Policy、Artifact Policy 与 Security Policy
  均携带 identity 和 SHA-256 固定点；实际读写文件和已发布 Artifact 另以逐项 proof 固定，避免无关资产新增
  错误阻断恢复。
- 有副作用工具统一由 Tool Descriptor 声明 `read_only`、`local_write` 或 `external`。本地写入和外部调用在
  dispatch 前先提交 prepared receipt，terminal receipt 与 prepared Ledger 事实复用 owner／correlation，
  并固定执行结果、文件和 Artifact proof。结果未知、返回合同失配、结算失败或重复 dispatch 请求均进入
  `AgentRuntimeAmbiguousError`；Bridge 不再切换候选模型重放，也不污染 Provider 健康度。
- 恢复 preflight 重新验证 owner ACL、Run／Checkpoint 摘要链、Runtime protocol 与恢复能力、全部版本固定点、
  Sandbox 工作区文件、Artifact ACL／hash 以及 receipt 与 Ledger 的双向锚点；进入真实 Runtime 前再次执行
  文件和 Artifact TOCTOU 校验。任何 prepared／ambiguous receipt 或外部结果未知都会 fail closed。
- Resume 选择最新可恢复 Checkpoint，Rewind 可选择更早边界，Fork 显式建立分支；三者均创建独立 child Run
  和 lineage Ledger 事实，源 Run／源 Checkpoint 保持不变。`execute_prepared()` 会把已验证状态恢复到声明
  `CHECKPOINT_RECOVERY` 的目标 Runtime，并真实执行 `RuntimeTurnKind.CONTINUE`，不是只记录 shadow 结果。
  并发 owner fencing 留在紧随其后的 4.4 Durable Task／Lease 中完成，因此 4.3 不暴露缺少租约保护的并发
  HTTP 执行入口。
- 生产组合根已为 Native Runtime 注入真实 Recovery Port；端到端 `NanobotBridge.handle_message()` 测试通过
  实际 reply 工具验证 `turn_started → plan_resolved → tool_ready → tool_completed → turn_completed` 五个
  Checkpoint 与 completed receipt 均落入权威数据库。KT Adapter 和不注入 Recovery Port 的直接 Native
  测试实例不会虚报恢复能力，未安装 KT 的生产 Native 组合仍声明并实际提供该能力。
- 专项恢复测试 12 passed；Runtime／Bridge／Ledger／Migration／Tool Registry／行为 Golden 广覆盖回归
  251 passed；Prompt Runtime 请求、模板和元数据审计 58 passed。Release Impact 精确识别 runtime、数据库、
  KT compatibility 与 Prompt Runtime 影响且无未归属生产路径；对应 Migration、KT compatibility 和
  canonical Prompt Runtime 形式门禁分别为 38、100、135 passed。架构边界、OpenAPI、行为基线、
  Release／Verification Golden、Ruff、Python 编译与 `git diff --check` 均通过；最终完整
  `python -m pytest tests/ -v` 为 6521 passed、12 skipped、0 failed。
- 已逐项检查 `prompts.v2.default/chat/*`、`prompts.v2.default/tasks/*`、工具 usage 模板、
  `core/prompt_v2/variables.py`、`core/prompt_v2/template_registry.py` 与 `data/prompts_v2/` 运行时副本。
  本阶段没有改变 `enriched_query`、历史注入、conversation 排列、模型可见工具输出或模板变量合同，
  因此默认及运行时模板无需修改；上述 Prompt Runtime 门禁证明现有模板解析与输出仍一致。

#### 4.4 Durable Task 和租约恢复

- [x] 统一聊天长任务、定时任务、主动外呼、research 和后台 Agent Run。
- [x] 使用 lease、heartbeat、owner fencing、cancel、timeout 和 reconcile。
- [x] 同一 Run 只能有一个有效执行 owner。
- [x] 客户端重连只恢复视图，不重复启动任务。
- [x] 投递使用幂等 receipt。

实现与验证证据：

- 新增框架无关的 `RunTaskControl`、`RunTaskLease`、`SqlAlchemyRunTaskService` 和公共 Job
  Adapter，把聊天、定时执行、主动外呼、research、恢复以及后台 Agent Run 投影到同一执行
  控制面。各领域原有任务状态机和幂等键仍是领域事实源，公共层不复制或合并它们。
- `RunTracer.start_run()` 现在于同一短事务创建 Agent Run、Ledger 接纳事实与执行租约；
  终态写入前必须同时匹配 owner、token、generation、attempt 和未过期 lease。`RunTaskOwner`
  在独立 Session 中持续 heartbeat，在取消、超时、丢失租约或心跳异常时使用稳定 reason code
  取消当前执行协程；无权旧 owner 不能续租或结算。
- 生产定时 worker 在现有单一调度循环中执行过期 Run reconcile，不新增第二个调度器。
  reconcile 使用 CAS 终结过期 owner，已存在 prepared／ambiguous 外部副作用回执时收敛为
  `ambiguous`，不猜测重放。Recovery child Run 采用先 prepared、后唯一 claim 的路径，
  Scheduled Workflow 和 Outbound Generation 的领域 claim 也补齐 generation／attempt fencing。
- 管理端 `GET /agent-runs/{run_id}` 只读返回 Durable Task 投影，不执行 claim 或重启；
  `POST /agent-runs/{run_id}/cancel` 只幂等记录取消请求，由现有 owner 心跳或 reconcile
  完成收敛。因此刷新或重连只恢复视图，不会产生第二个执行 owner。
- Durable Task 只保存可安全引用的 receipt ref；Outbound 实际投递仍由现有 Outbox／
  Delivery Attempt 的幂等 receipt 保护。重复绑定同一 receipt 是幂等操作，改绑其他
  receipt 会 fail closed；外部结果未知时不把已请求投递误报为 exactly-once 成功。
- Durable Task 与相关 Runtime／Bridge／Scheduled／Outbound／Proactive／Recovery 联合定向回归
  336 passed，Prompt Runtime 审计 414 passed；架构边界、OpenAPI 生成物、行为基线、
  Release／Verification Golden、决策规则清单、Task SLO Manifest、Ruff、Python 编译和
  `git diff --check` 均通过；最终完整 `python -m pytest tests/ -v` 为 6534 passed、
  12 skipped、0 failed。
- 已逐项检查 canonical Prompt Runtime 默认／运行时模板、变量表和注册表。本阶段
  没有改变 `enriched_query`、历史注入、conversation 排列、模型可见工具输出或模板变量
  合同，因此无需修改 `prompts.v2.default/*` 与 `data/prompts_v2/` 运行时副本。

#### 4.5 Artifact 生命周期

- [x] 工具和模型先写 owner workspace。
- [x] 发布时生成 hash、MIME、大小、来源 Run、ACL 和不可变版本。
- [x] 预览、下载、消息渲染和跨会话引用只通过 `ArtifactPort`。
- [x] 不把 base64 或宿主真实路径写进消息历史。

实现与验证证据：

- 新增框架无关 `ArtifactPort` 合同和生产 `SqlAlchemyArtifactPort`。模型及工具生成内容先经
  `sandboxd` 将校验过的 CAS 临时对象物化到 owner workspace，再注册逻辑 Artifact；服务端决定
  workspace、存储键和目标路径，模型不能提交宿主路径或 Docker 参数。数据库以
  `(workspace_id, logical_name, version)` 保证不可变版本，记录稳定 Artifact ID、SHA-256、MIME、
  大小、来源 Run／来源类型、ACL hash 和创建时间；同名同内容重试保持幂等，同名新内容产生新版本。
- `asset_publish`、`asset_import`、聊天上传和图片生成均切换到生产 Artifact Port。图片模型输出在内存中
  验证 PNG 后流式发布，不再落入仓库 `generated_images/`；管理端测试生成入口也返回稳定
  `[artifact:<id>:v<version>]` 引用和签名预览地址。旧 generated-image 列表和文件读取仅保留为历史数据
  的只读兼容入口，新写入路径不再双写旧文件或元数据。
- 下载、预览、QQ／Web 消息最终渲染及跨会话引用统一先解析稳定 Artifact URI，并在最终传输边界按
  当前 owner 生成短期签名令牌；令牌 v2 绑定 Artifact ID、版本、owner、用途和过期时间。恢复服务会
  对 Checkpoint 中的 Artifact 逐项复核版本、workspace、ACL、hash、MIME 和大小，旧
  `asset://sha256` proof 只作为迁移兼容读取，不再成为新写入合同。
- `ChatLog` 与 `ConversationTurn` 在 ORM 持久化边界统一清除 data/base64、`file://`、Windows／POSIX
  宿主路径和旧短凭据；聊天、工具、ambient 与群聊等直接写入路径共用同一保护。消息正文只保存稳定
  Artifact 引用，不依赖 base64 或宿主真实路径重建大结果。
- 已同步检查并更新 image generation、asset publish/import、reply、sticker 和 QQ common 的 canonical
  Prompt Runtime 默认模板及受版本管理的运行时副本；`variables.py` 与 `template_registry.py` 的变量和
  注册合同仍然准确。Artifact／恢复／消息持久化／Sandbox／Prompt Runtime 联合定向回归 576 passed，
  管理端和 Artifact 最新回归 162 passed；最终完整 `python -m pytest tests/ -v` 为 6542 passed、
  12 skipped、0 failed。架构边界、OpenAPI 生成物、行为基线、Release／Verification Golden、决策规则、
  Task SLO、致命静态错误检查、Python 编译和 `git diff --check` 均通过。

验收条件：可重建任意已保留 Run 的状态和可见上下文；恢复不会重复有副作用操作；大结果不再依赖消息正文保存。

### 阶段 5：Context Engine、前缀缓存和 Plan Mode

#### 5.1 Context 分层

- [x] 固定 system、安全、工具合同和稳定策略段。
- [x] 分离 session、user、group、project 动态上下文。
- [x] 分离近期对话、记忆召回、工具结果和摘要。
- [x] 为各层设置独立 token 预算和溯源。
- [x] 保持 `ChatLog`、`ConversationTurn` 和模型上下文三者分离。

实现与验证证据：

- 新增框架无关 `core/context_engine.py`，定义稳定 system、安全策略、稳定策略、工具合同、
  动态上下文、记忆召回、摘要、近期对话、工具结果和当前请求十个 Context Layer，以及
  global／session／user／group／project／turn 六种作用域。每层使用服务端固定预算和
  `reject` 门禁；私聊与群聊近期上下文分别按 8k／24k token 限制，请求不能提高上限。
- canonical Prompt Runtime 现在从最终 messages、冻结 tool schemas 和 flow section 生成带
  schema version、逐项内容摘要、消息位置、作用域、稳定性、信任级别、来源引用、逐层预算和
  总摘要的 Context Manifest。Manifest 不保存正文，外部 user／group／session／message 标识只以
  SHA-256 引用出现；审计会验证签名、枚举、摘要、计量和 Prompt SHA，一旦超限或篡改即在模型调用前
  fail closed。
- `StructuredChatContext` 将 conversation contract、rolling／block summary、memory recall、
  project context 和 recent messages 分开返回。私聊继续只从 `ConversationTurn` 构建工作窗口，
  群聊继续只从 `ChatLog` 及其后台摘要构建上下文；兼容三元组仅保留给旧调用方。生产 `/chat`、
  群消息、群定时入口和管理端 effective preview 均已切换到结构化入口，不再把摘要和群体记忆
  拼进 `history_header`。
- Prompt Contribution／Flow 新增独立 `project_context` 与 `summary_context` 节点，并同步
  `prompts.v2.default/chat/*` 和 `data/prompts_v2/chat/*`。旧 header 中的受控摘要标签只在编译边界
  做迁移提取，以保持历史调用方兼容；模型可见顺序仍由 canonical Flow 唯一决定。
- Prompt Trace 只保存 Context Manifest 的 hash、entry 数、token 数和 policy ID；Run Ledger 的
  `run.prompt_resolved` 事件及管理投影保存同样的无正文证明。Prompt 有效预览返回完整无正文
  Manifest，支持逐层定位预算和来源，不把原始 Prompt、摘要、记忆或宿主路径写入 Ledger。
- Context／Prompt／账本定向回归 585 passed，聊天与群入口回归 286 passed，群服务与 API 回归
  141 passed；最终完整 `python -m pytest tests/ -v` 为 6548 passed、12 skipped、0 failed。
  架构边界、OpenAPI、行为 Golden、Release／Verification、决策规则、Task SLO、Ruff、Python
  编译和 `git diff --check` 均通过。

#### 5.2 前缀缓存稳定

- [x] 固定 Prompt section 和工具 schema 顺序。
- [x] 将请求级动态内容放在稳定前缀之后。
- [x] 避免时间戳、随机排序和无意义 request metadata 破坏缓存。
- [x] 按 Provider 记录 cached/uncached token、命中率、首 token 延迟和成本。
- [x] 缓存优化不得改变权限、事实和 Prompt Runtime 的 canonical 顺序。

实现与验证证据：

- canonical Prompt 编译器在生产路径统一按工具名和完整 schema 摘要冻结 Tool Schema 顺序，并从
  已审计 Flow、Context Manifest 和最终 messages 生成不含正文的签名 Prefix Cache Manifest。
  Manifest 明确稳定 message 数量、动态后缀起点、稳定前缀／工具／canonical 顺序摘要和 cache key；
  编译审计会重建并逐字段比对，篡改、重复工具名或非 canonical 顺序均在模型请求前 fail closed。
- 稳定前缀只覆盖 canonical system、安全和稳定策略段，身份模板也只允许角色名、别名、平台和会话类型等
  稳定变量；时间、用户／会话 ID、超级用户事实和请求正文继续由后续 runtime context 表达。原有
  `prompts.v2.default/chat/flow.json`、各分支模板和模型可见 canonical 顺序未移动，权限与超级用户事实
  仍由服务端运行时合同决定。
- Native Chat Completion Adapter 已直接接入生产 `LLMRequestTracer`，KT／SDK 路径继续使用同一追踪器；
  Codex Provider 使用当前签名前缀 cache key。缓存形状 v2 以 manifest 边界计算稳定前缀，忽略仅用于
  trace 的 request/run/trace metadata，并从 OpenAI、Anthropic、DeepSeek 等兼容 usage 归一化
  cached／uncached／write token。
- `llm_api_request_logs` 通过增量迁移新增输入／输出 token、首 token 延迟、微美元成本和成本来源；成本
  优先采用 Provider 报告，否则只在冻结输入／输出单价齐全时估算。管理接口直接按 Provider 聚合请求数、
  cache token 命中率、首 token 延迟和成本；Native Runtime 的 Usage 同步携带成本且不会重复写入 Ledger。
- Prompt／Context／缓存／成本／追踪／迁移定向回归分别完成 155 passed、130 passed，生产 Bridge、
  Streaming、Native Runtime、KT 兼容和行为基线联合回归 167 passed，迁移架构后生产路径回归 47 passed，
  指标语义回归 55 passed。最终完整 `python -m pytest tests/ -v` 为 6559 passed、12 skipped、
  0 failed；架构边界、OpenAPI、行为 Golden、Release／Verification Golden、决策规则、Task SLO、
  Ruff 致命规则、Python 编译和 `git diff --check` 均通过。

#### 5.3 分层压缩和工具输出治理

- [x] 实现 `notice → snip/prune → summary → hard limit` 水位。
- [x] 保持 assistant tool call 与 tool result 配对。
- [x] 大结果先发布为 Artifact，再向 Context 注入摘要和引用。
- [x] 增加 Unicode 清洗、边界标记、注入风险标注和安全截断。
- [x] 保存压缩原因、前后 token、保留项和质量评测证据。

实施记录（2026-08-04）：

- Native 生产 Runtime 在每次模型调用前执行确定性 Context 投影；固定四级水位、目标水位和硬上限均由
  受管设置解析。system 段、当前用户请求及其后消息为保护项，无法在不破坏保护项的前提下降至硬上限时
  失败关闭，不改写 `ChatLog`、`ConversationTurn` 或 Runtime 原始消息事实。
- assistant tool call 与对应 tool result 先校验为不可分割原子组；重复 call ID、孤立 result、名称不匹配和
  未完成批次均拒绝进入模型上下文。裁剪和摘要只处理完整原子组，并保存配对数量与有效性证据。
- 工具输出统一进入不可信结果信封，执行 NFC／换行规范化，清除 bidi、零宽和非法控制字符，并以固定
  begin/end 边界、风险标签、来源／清洗摘要和安全首尾摘录注入。风险标签只作提示，不声称消除注入。
- 超过字节或字符阈值的结果由生产 `SqlAlchemyToolResultArtifactPublisher` 在独立事务中发布为 owner
  workspace 的不可变 Artifact；模型只看到安全摘录和 `artifact://` 引用。发布失败时禁止继续注入截断
  结果；Artifact 事件在后续 tool activity／模型调用前进入 Event Ledger。
- `context.compaction_decided` 保存策略、原因、前后 token／消息数、保护项、保留／删除集合摘要、Artifact
  集合摘要和质量证明，不保存消息正文。Prompt Runtime 的 canonical／运行时模板已同步说明结果信封。
- Context、生产 Artifact Adapter、Native Runtime、Bridge 恢复和 Ledger 定向回归为 55 passed；设置
  兼容修复与原失败场景回归为 15 passed。最终完整 `python -m pytest tests/ -v` 为 6578 passed、
  12 skipped、0 failed；架构边界、OpenAPI、行为 Golden、Release／Verification Golden、决策规则、
  Task SLO、Ruff 致命规则、Python 编译和 `git diff --check` 均通过。

#### 5.4 Session Goal 和 Plan Mode

- [x] 为长任务定义目标、完成条件、预算和状态。
- [x] Plan Mode 只允许读取和写入计划资产。
- [x] 退出 Plan Mode 并获批准后才允许实施工具。
- [x] 工具可见性和写权限由服务端策略控制，不靠 Prompt 自律。

实施记录（2026-08-04）：

- 新增持久化 `SessionGoal`、不可变 `SessionPlanAsset` 和追加式控制事件，完整保存 owner、session、目标、
  完成条件、token／成本／时长／工具调用预算、状态、模式、版本与批准证明。SQLite trigger 禁止普通
  UPDATE／DELETE 计划资产和控制事件；目标投影使用乐观版本控制，并校验计划正文、摘要和批准证明一致性。
- 状态机强制执行 `planning → awaiting_approval → approved → executing → terminal`。批准后仍停留在
  Plan Mode，只有经过独立的显式 start 操作才进入执行模式；actor、owner、期望版本和批准计划摘要均在
  变更事务前验证，过期草稿、跨 owner、重复批准及未批准启动均被拒绝。
- 新增受令牌保护的 Session Goal 控制 API，以及专用 `session_plan_read`／`session_plan_write` 工具。
  模型工具参数不接收 goal、owner 或 session 标识，全部从受信 Request Runtime Context 解析；模型侧没有
  approve 或 start 工具，因此不能自行批准计划或切换执行模式。
- 生产 Bridge 在请求开始时冻结目标策略、计划版本和证明，并由服务端重建最终 ToolPlan。Plan Mode 只保留
  reply、no_reply、计划读取及允许时的计划写入；来源禁用项继续硬禁用。批准后禁止写计划，显式启动后才恢复
  实施工具。无 Session Goal 的请求不增加空运行时字段，保持既有 SDK 请求和 KT Adapter 合同不变。
- canonical Prompt Runtime 与受版本管理运行时模板已同步 Plan Mode 的不可信数据边界和服务端授权语义；
  模型上下文仅注入有界目标、条件、预算和计划摘录，完整计划通过专用读取工具按 owner ACL 获取。
- Session Goal 状态机、ACL、版本竞争、计划完整性、输入预算、工具身份、API 生命周期和生产 Bridge 联合
  定向回归 6 passed（兼容性回归复验）；最终完整 `python -m pytest tests/ -v` 为 6590 passed、
  12 skipped、0 failed。OpenAPI、行为基线、Release／Verification Golden、决策规则、Task SLO、架构边界、
  Ruff 致命规则、Python 编译和 `git diff --check` 均作为本模块提交前门禁执行。

验收条件：相同稳定输入产生可预测的 Prompt 前缀；缓存收益可量化；压缩后关键事实、工具配对和安全说明不丢失。

### 阶段 6：Skill、MCP、Hook 和协议兼容

#### 6.1 Agent Skills / SKILL.md 兼容

- [x] 支持内置、项目、Agent 和用户作用域。
- [x] 定义发现优先级和冲突策略。
- [x] 校验 YAML 元数据、版本、依赖和权限要求。
- [x] 支持按需加载、版本 pin、lock、升级、回滚和卸载。
- [x] 不执行来源不明的自动安装器或命令注入。

实施记录（2026-08-04）：

- 新增受管 Skill 不可变版本、资源文件、作用域绑定和追加式生命周期事件。内置 Skill 只从固定发布目录
  读取；项目、Agent、用户版本只接受管理员提交的字面 `SKILL.md` 与资源，不提供 URL 下载、安装器、
  subprocess 或模型可调用的安装入口。SQLite trigger 禁止普通更新／删除版本、资源和审计事件。
- 严格解析 Agent Skills frontmatter，并扩展校验 SemVer、依赖精确版本、权限声明、`allowed-tools`、
  license、compatibility、资源规范路径、文件类型、单文件／总 bundle 大小及循环依赖。未知字段、符号链接、
  FIFO、路径穿越、同版本正文漂移和跨作用域持久化投影均失败关闭。
- 可见性按 `user → agent → project → builtin` 冻结，每个同名 Skill 只保留最高优先级的合法版本；群聊不会
  读取用户私有作用域。权限、依赖和可执行工具集合在服务端解析，诊断不把正文或私有低优先级版本泄露给模型。
- 生命周期 API 支持显式安装、版本新增、pin／unpin、upgrade、rollback、uninstall 和 reinstall，并使用
  generation CAS 防止并发覆盖。每个请求生成包含 package ID、版本、内容摘要和元数据的精确 lock；后续
  切换 active 版本不会改变已冻结请求的读取结果。
- 生产 Bridge 仅在当前请求存在合法 lock 时动态启用 `skill` 工具并生成枚举 schema；Prompt 只注入有界目录，
  完整正文和 UTF-8 文本资源由工具按 lock 精确读取。Plan Mode、ToolPlan 硬禁用、未知 Skill、二进制资源和
  额外身份参数全部拒绝。canonical 与运行时 Prompt 模板已同步不可信目录／资源和授权指导边界。
- KT 2.0／1.4 的内置 Skill 自动发现路径在 Nanobot 生产 Agent 子类中被关闭，不扫描 cwd、HOME、Agent 目录
  或包目录，也不注册 KT 隐藏 slash skill 命令；Native 与 KT Adapter 都只消费 Nanobot 请求级精确 lock，
  因而本模块已经是生产主路径而非 shadow 双写。
- 外键开启场景下，版本父记录先于资源子记录落库，避免依赖 ORM 未声明 relationship 时的偶然插入顺序。
  Agent Skills、生命周期、Admin API、Runtime、KT 隔离、ToolPlan、Prompt、流式桥接和迁移定向回归均通过。
  最终完整 `python -m pytest tests/ -v` 为 6606 passed、12 skipped、0 failed；架构边界、OpenAPI、
  行为 Golden、Release／Verification Golden、决策规则、Task SLO、Ruff 致命规则、Python 编译和
  `git diff --check` 全部通过。

#### 6.2 Skill 检索和治理

- [x] 用 Registry 和现有 RAG 建立 Skill 描述索引。
- [x] 记录能力标签、权限、依赖、Prompt 成本和适用范围。
- [x] 延迟注入 Skill 正文和工具 schema。
- [x] 记录每个版本的调用、成功率、成本和评测结果。

实施记录（2026-08-04）：

- 每次请求先从可见的精确版本锁构建不可变 Skill Registry 快照，再把不含正文、资源、owner 和内部
  scope key 的描述投影同步到现有 `semantic_index_items` 与 FTS；检索使用精确 source ID 白名单、
  FTS 与词法回退，不新增第二套向量事实源。结果按适用范围过滤并补全依赖闭包，空查询或未命中时
  返回空锁，单项索引故障不会阻塞其他 Skill。
- Skill 元数据和运行时锁升级为 v2，显式记录能力标签、适用范围、工具、权限、依赖、正文与目录
  Prompt 成本；受管版本仍从不可变 `SKILL.md` 校验摘要后生成请求级锁，旧 v1 锁保持兼容读取。
- 生产桥接路径以原始用户查询选择最小运行时锁。只有存在命中项时才注入精简目录和带枚举的
  `skill` 工具 schema；Skill 正文与单个资源继续在实际调用时按精确锁延迟读取，未选中的 Skill
  无法通过猜名访问。Native 与 KT Adapter 消费同一份已筛选运行时属性，本能力不是 shadow 路径。
- 新增只追加的版本调用事实和评测事实，记录成功、失败、时延、Prompt token、资源字节、评测
  分数及成本；Admin API 可写入版本评测并汇总调用量、成功率与成本，事实表禁止更新和删除，且
  不保存正文、用户查询或 owner 明文。
- Agent Skills、检索治理、版本迁移、Admin API、Runtime、Prompt、OpenAPI 和桥接定向回归通过；
  完整 `python -m pytest tests/ -v` 为 6609 passed、12 skipped、0 failed。架构边界、OpenAPI、
  行为 Golden、Release／Verification Golden、决策规则、Task SLO、Ruff 致命规则、Python 编译和
  `git diff --check` 均纳入本模块最终门禁。

#### 6.3 MCP 控制面

- [x] 支持 stdio、SSE 和 HTTP transport。
- [x] 支持 OAuth 与请求级秘密引用。
- [x] 提供健康检查、启停、超时、重连和诊断。
- [x] 工具名默认带 server namespace，并检测冲突。
- [x] 配置原子替换；单个坏 MCP 不阻塞其他 MCP。
- [x] 秘密不得进入日志、metadata、子 Agent 或 Sandbox。

实施记录（2026-08-04）：

- 新增 Nanobot 自有 MCP 控制面和正式生产接入，使用官方 Python SDK 连接 stdio、旧版 SSE 与
  Streamable HTTP；SDK 固定在核心、测试和可选 KT 锁中的同一 `1.29.0` 版本。Native Runtime 与
  KT Adapter 消费同一请求级 MCP 快照、ToolPlan 和 Core execution port，不存在 shadow 分流；无启用
  server 或无 SDK 时不会影响 Native／非 Agent 主链路。
- server 配置通过 Registry 快照、内容摘要和 revision CAS 执行全量原子替换；启停同样生成新 revision。
  每个 server 独立发现、缓存、超时和诊断，坏配置、缺失秘密、错误身份、非法 schema、工具预算超限或
  namespace 冲突只隔离该 server。工具 wire name 默认加入 server namespace，调用前重新获取目录并比对
  冻结 schema；连接前失败可按上限重连，调用已发出后的未知结果标为 ambiguous，禁止自动重放。
- OAuth client credentials、Bearer、自定义 Header 与 stdio 环境变量全部只保存为秘密引用；值使用独立
  派生密钥加密，只在发现／调用栈局部解析。stdio stderr 丢弃，异常压缩为无 URL／正文的稳定分类，诊断、
  run metadata、Runtime attributes、ToolPlan、子 Agent 与 Sandbox 均不接收秘密值；响应会移除 `_meta`
  并按本次已知凭据脱敏，所有 content block 和 structured content 作为不可信结果信封保留。
- 外部 server 自报的 `readOnlyHint` 不会下调服务端授权等级；MCP 一律按 external effect 进入 Native
  恢复边界。调用参数在解析秘密和建立连接前按本轮冻结 JSON Schema 校验，非 ambiguous 的调用前失败
  形成普通失败结果，只有已发出后无法确认的结果进入副作用 ambiguous 流程。
- Admin API 已提供配置读取／原子替换、server 启停、秘密 replace／clear、单项／批量健康检查和脱敏诊断；
  OpenAPI、canonical／runtime Prompt、行为 Golden、决策规则清单和依赖锁已同步。MCP 专项测试在本地
  SDK 和锁定的 `1.29.0` SDK 环境均为 12 passed；桥接、Native／KT、迁移、Prompt 与生成合同联合回归
  214 passed。最终完整 `python -m pytest tests/ -v` 为 6621 passed、12 skipped、0 failed；架构边界、
  OpenAPI、行为 Golden、Release／Verification Golden、决策规则、Task SLO、Ruff 致命规则、Python
  编译、模板一致性和 `git diff --check` 均通过。

#### 6.4 Hook / Plugin 生命周期

- [x] 支持 pre/post model、pre/post tool、event、interrupt 和 completion Hook。
- [x] 声明顺序、超时、失败策略、可读字段和可修改字段。
- [x] Hook 默认不能绕过 ToolPlan、Permission、Prompt Runtime 和 Event Ledger。
- [x] 插件异常必须形成可诊断事件。

实施记录（2026-08-04）：

- 新增显式组合、启动期冻结的异步 Plugin Manager，覆盖 pre/post model、pre/post tool、event、interrupt
  和 completion 七个切点。Plugin 与 Hook 均声明稳定 ID、SemVer、顺序、生命周期／调用超时、必需性、
  fail-open／fail-closed 策略及可读／可修改字段；加载按声明顺序、卸载按逆序执行。同名覆盖、目录扫描、
  动态导入和隐式第三方加载均不存在，可修改 Hook 只接受显式标记的受信内建实现。
- Native Runtime 的模型、工具、输入／输出事件、完成和中断链路直接执行受管 Hook；KT Runtime 只通过
  KT 1.4 公开 `BasePlugin` 与 `PluginManager.get_plugin()/register()` 接入模型和工具切点，并把 KT 会隔离的
  post Hook fail-closed 异常保存到请求上下文后重新抛出。显式启动和接管已运行 KT Agent 都会启动同一个
  Plugin Manager；Bridge 的实际 Native／KT 组合根均注入请求 Runtime 对应的 Manager，不存在 shadow 分流。
- Hook 输入是深度只读投影，身份、ToolPlan、Permission、Prompt Runtime 和 Event Ledger 只保存在宿主侧。
  Event 输入不暴露身份、计划或用户正文，输出事件不暴露 identity、正文、工具参数／结果，completion 不暴露
  raw result 或消息正文。Event Ledger 始终先提交再调用不可改写的 Event Hook；Event／Interrupt 强制 fail open。
- Pre Tool 只能改写 arguments，工具名不可改；每次补丁都按本轮冻结 ToolPlan 和 Draft 2020-12 JSON Schema
  复验，非法补丁按声明策略丢弃或阻断。Post Tool 只能在副作用结算完成后改写模型可见 output，不能改变
  receipt、状态或错误事实；模型切点保持观察模式，不能改写 Prompt Runtime 产物。
- Plugin 的加载、卸载、超时、执行、返回类型、未声明字段和宿主合同失败均归一为类型化错误，并通过新增的
  `agent.plugin_hook.failed` Runtime Event 写入权威 Ledger；诊断只保留稳定错误类型和分类，不记录异常正文、
  参数值或秘密。Agent Manifest 同步声明新切点、超时、失败策略和字段边界。
- Hook 生命周期、Native／KT 真实链路、接管已运行 Agent、冻结 Schema、Ledger 顺序、只读／脱敏投影和诊断
  事件专项回归通过；最终完整 `python -m pytest tests/ -v` 为 6637 passed、12 skipped、0 failed。架构边界、
  OpenAPI、行为 Golden、Release／Verification Golden、决策规则、Task SLO、Ruff 致命规则、Python 编译、
  canonical／runtime Prompt 模板一致性和 `git diff --check` 均通过。

#### 6.5 ACP、A2A 和 Headless 互操作试验

- [x] 评估 ACP 的 session、stream、tool activity 和 pending interaction 映射。
- [x] 评估 A2A 的 task、artifact 和状态交换。
- [x] 在 Agent Link 之外只实现薄 Adapter。
- [x] 内部事实源仍是 Nanobot Run/Event/Artifact 合同。
- [x] 通过兼容性和安全测试后再决定是否正式开放。

验收条件：Skill、MCP 和协议均可替换或停用；不存在凭据泄漏、隐式覆盖和绕过权限的扩展路径。

实施记录（2026-08-04）：

- 重新核验三个官方事实源的当前主干：ACP `541daf8fa488c6b93aad4a874ac050b3daf9b282`
  （schema v1.20.0，wire version 仍为整数 `1`；v2 明确仍是 Draft）、A2A
  `6dad7a125d0534a2be7617f4e13224303e54e944`（稳定协议 `1.0`，规范事实源为
  `package lf.a2a.v1` proto）和 Maka `076e653ffa31583668380662dc632717918e3f96`
  （Headless TaskRun 从 Runtime Event／Result 投影，评测器与交互 Runtime 分离）。实现不依据二手文章冻结字段。
- 新增默认关闭、仅 ADMIN 可显式启用的 ACP v1 Agent Adapter。`initialize`、`session/new`、
  `session/prompt`、`session/cancel` 和 `session/close` 会调用每个 session 独占的真实
  `AgentRuntimePort`；`RuntimeRunEvent` 直接投影为 message chunk、tool call/update、usage 和
  resource link，工具参数、工具结果、raw result、owner 和 host path 不进入 wire。ACP 不能提交 MCP
  Server、附加目录或非宿主分配的 cwd；资源链接只作为无授权含义的引用文本进入 Runtime。
- `AcpPermissionPort` 先执行内部 `PermissionPort`；只有内部结果为 `ask` 才创建有上限、有超时、可取消的
  `session/request_permission`。阶段 7.1 建立 session grant 前只公布 `allow_once` 与 `reject_once`，格式错误、
  超时、取消和客户端失败全部稳定映射为 deny；宿主必须把该 Port 放在权威 Ledger Adapter 内层。
- 新增实际 HTTPS A2A 1.0 JSON-RPC Client Adapter，只支持受信配置固定的 `JSONRPC` AgentInterface 和
  精确 HTTPS origin allowlist；禁用环境代理、重定向、自动发现、自动重试、SSE、push 和 Server 入口，
  每次请求固定发送 `A2A-Version: 1.0`。凭据只由 transport 注入 Authorization header，异常、响应和
  Task metadata 均不能回写凭据、owner、tenant 或本地 Run identity。首版只创建新任务，不接受未绑定的
 远端 task/context continuation。
- A2A `Task`、`Message`、`TaskState`、`Artifact` 和 `Part` 解析为有界不可变远端投影；阻塞
  `SendMessage` 只接受终态或 `INPUT_REQUIRED`／`AUTH_REQUIRED`，inline raw/data/text 有单项和总量上限，
  Artifact URL 不会自动抓取。远端 Task 永远不是本地 Run，远端 Artifact 也不会冒充已经发布的本地
  `RuntimeArtifactRef`；本地 `RuntimeRunIdentity` 只绑定一次交换的来源。
- 新增真实 Headless Runtime Adapter，直接调用既有 `run_event()`。宿主的权威 Event handler 始终先执行，
  随后才形成一次调用内的不可变事件引用和脱敏 evidence digest；不创建新 Event Store，不记录正文、工具
  参数／结果或 owner／actor，且支持事件数、文本量、并发和显式取消上限。
- 三个 Feature 都登记为 experimental、default off、ADMIN only，缺少协议兼容、安全、身份／凭据边界、
  Event Ledger 或 operator gate 时构造即失败。兼容性与安全专项回归为 23 passed；相邻 Runtime／Ledger／
  生命周期回归为 50 passed。实际生成的 ACP Initialize、NewSession、Prompt、Session Update 与 Permission
  Request 已通过官方 `schema/v1/schema.json` Draft 2020-12 校验；架构边界、OpenAPI、Release、Verification、
  Behavior、决策规则、Task SLO、Ruff、Python 编译、Prompt 模板一致性和 diff whitespace 门禁均通过。
- 正式开放决策：当前保持实验、默认关闭，不增加网络监听和公开 API。待阶段 7 完成统一权限／预算、身份、
  ACL 与可撤销 session grant 后，再单独评审 ACP transport 与 A2A 远端任务 continuation；这不影响显式调用
  当前 Adapter 时执行真实 Runtime／HTTPS 路径，不存在 shadow 分流。

### 阶段 7：权限、Sandbox、身份、工作区和记忆

#### 7.1 统一权限和预算

- [x] 为 Run、Turn、Tool 和 Subagent 声明模型、token、费用、步数、时间和并发预算。
- [x] 声明文件、网络、工具、Skill、MCP 和记忆访问范围。
- [x] `PermissionPort` 支持 `allow`、`deny`、`ask`、`allow-once` 和 session grant。
- [x] 所有决定进入 Event Ledger，并支持撤销临时授权。

实施记录（2026-08-04）：

- 新增不可变 `RuntimeGovernanceEnvelope`，同时冻结 Run、Turn、Tool、Subagent 四级预算与文件、网络、
  工具、Skill、MCP、记忆六类精确访问范围。子级预算不能扩张 Run 上限；访问授权不支持通配符，Runtime
  在同一 Run 的重试过程中拒绝更换权限集合、策略或提升工具／子 Agent 预算，仅允许在预先声明的模型集合
  内切换路由。
- `RuntimeBudgetManager` 在真实物理模型请求、工具调用和子 Agent 入口执行模型次数、token、费用微单位、
  步数、时限和并发检查。Run／Turn 计数跨候选模型重试共享；Native、KT 公开 Plugin、Bridge 组合根和
  确定性直接工具执行均接入同一治理合同。KT 并发工具从 pre-tool 持有 reservation 到 post-tool，子任务中
  的 fail-closed 决定会回传请求主任务，不会因 `ContextVar` 任务隔离而丢失。
- 请求上下文从冻结 ToolPlan、当前模型路由、workspace／asset／sandbox、Skill lock、MCP snapshot 和记忆
  owner 生成精确授权。受控联网只授予真实 provider-backed 工具；Sandbox 与 MCP 分别保留独立授权来源，
  普通工具不能借用它们的权限。子 Agent 默认预算为零，阶段 8 显式建立编排合同前无法启动。
- `PermissionPort` 扩展为 allow、deny、ask、allow-once 与有界 session grant；高风险普通工具默认进入 ask，
  Sandbox 只接受既有 Sandbox session grant，MCP 写操作也不能自动放行。session grant 绑定 owner、session、
  action 和资源摘要，支持 TTL、跨进程账本重放和显式撤销；过期、冲突、缺少 session 或数据库失败均关闭式
  拒绝。ACP 的 `allow_always` 只映射为同一套有界 session grant，不创建旁路授权。
- 四级预算声明、每次允许／拒绝、Permission 决定、grant 发放、过期与撤销均同步写入权威 Event Ledger；
  账本只保留资源和原因摘要，不保存工具参数、用户正文或真实资源标识。生产 sink 要求业务 Run 已先接纳，
  测试也按同一权威顺序建立接纳事实，不以测试开关绕过治理。
- 预算边界、精确授权、模型重试、KT 并发、Native／KT／直接工具生产链路、ACP session grant、持久化重放、
  migration 和敏感字段专项回归均已通过；最终完整 `python -m pytest tests/ -v` 为 6677 passed、
  12 skipped、0 failed。架构、OpenAPI、Release／Verification／Behavior Golden、决策规则、Task SLO、
  Ruff 致命规则、Python 编译、Prompt 关键合同同步和 `git diff --check` 均通过。

#### 7.2 Sandbox 和工具安全

- [x] `sandboxd` 继续独占 Docker Socket。
- [x] 服务端决定镜像、挂载、网络、capability 和资源上限。
- [x] 验证非 root、只读根、默认断网、无 Docker Socket 和超时终止。
- [x] 验证 workspace 持久化和 owner 之间不可互读。
- [x] 不引入 OpenSandbox、OpenShell、E2B 运行时依赖。

实施记录（2026-08-05）：

- 审计既有生产链路时发现 `nanobot-sandbox-runtime-cleanup.service` 仍直接执行 `docker ps`，使“只有
  sandboxd 接触 Docker Socket”在定时维护路径上不成立。新增仅管理 Token 可访问的
  `/v1/admin/execution-state`：由 sandboxd 通过 Docker SDK 读取所有带双重所有权标签的运行中或残留
  容器，并合并一次性 Run 的内存 reservation；归属歧义、Docker 读取失败、容器残留或 reservation 未释放
  都按非静默处理。
- 新增标准库 UDS 维护探针。探针固定请求上述管理端点，对 Socket 符号链接、Token 文件权限、响应大小、
  HTTP 状态、JSON 类型、容器计数和 `quiesced` 交叉一致性执行失败关闭；Token 只进入进程内 HTTP header，
  不出现在命令参数和日志。runtime TTL 清理和生产管理入口均改用该探针，不再自行调用 Docker CLI。
- 定时清理 unit 改为依赖 `nanobot-sandboxd.service`，并通过 `InaccessiblePaths` 同时屏蔽
  `/var/run/docker.sock` 与 `/run/docker.sock`。sandboxd 配置也只接受固定本机
  `unix:///var/run/docker.sock`，拒绝 TCP Docker API 或任意替代 Unix Socket；Server、Worker 和 Sandbox
  仍只看到只读管理 UDS 或完全看不到控制面。
- 既有 canonical Profile、`build_container_kwargs()`、专用固定摘要镜像、AppArmor、seccomp、非 root、
  只读根、`cap-drop=ALL`、默认 `network=none`、固定 mount、CPU／内存／PID／tmpfs／超时／输出／quota
  约束保持不变。真实 Docker 六组矩阵继续覆盖容器 inspect、进程树超时终止、Lease 重建、Workspace／
  Runtime 持久化和 A／B Workspace 隔离；生产 `smoke` 仍要求每组至少一项测试且零 skipped 才能进入
  control-plane 安装。
- Sandbox 全量单元和合同回归为 374 passed、6 skipped、0 failed；6 个 skip 均为必须显式开启的真实 Docker
  测试。当前 WSL 开发宿主的生产 `--preflight-only` 真实返回 exit 2／`blocked`（非 root，且 Docker
  SecurityOptions 未提供 AppArmor），没有把跳过或阻塞伪装为 passed。阶段 11.3 仍必须在满足 AppArmor、
  project quota 和独立数据盘条件的真实部署宿主运行完整六组矩阵后才能最终交付。
- 依赖门禁确认所有 requirements 与 Compose 均未引入 OpenSandbox、OpenShell 或 E2B。最终完整
  `python -m pytest tests/ -v` 为 6684 passed、12 skipped、0 failed；架构边界、OpenAPI／客户端、
  Release／Verification／Behavior Golden、决策规则、Task SLO、Bash 语法、Ruff、Python 编译和
  `git diff --check` 均通过。`systemd-analyze verify` 已接受新增 unit 指令，但当前 WSL `/mnt/d` 源码挂载
  的文件模式及尚未安装的 `/opt/nanobot-server` 使其返回 exit 1；这不作为生产验收通过，仍由阶段 11.3
  在真实部署宿主复验。

#### 7.3 Agent Identity、Workspace 和 ACL

- [x] Agent identity 与 KT、Native 或远程 Runtime 解耦。
- [x] 明确 user、group、project、session 和 agent owner。
- [x] 明确私有和共享 workspace、ACL、配额和生命周期。
- [x] 切换 Runtime 不丢失身份、授权和记忆引用。
- [x] 模型只能看到容器内虚拟路径。

实施记录（2026-08-05）：

- `RequestRuntimeContext` 新增必填、不可变的稳定 `agent_id`，并与 `runtime_id`／Runtime 实现名分离；Bridge
  从 creature profile 派生 Agent 身份，Native、KT Adapter、确定性直接工具执行及行为基线均传递同一身份。
  切换 KT、Native 或后续远程 Runtime 只改变执行实现，不再改变 Agent owner、Skill 绑定或恢复引用。
- owner 合同明确区分 user、group、project、session、agent 和 actor：消息入口要求 principal 与接收方及
  canonical chat stream 一致；既有 Workspace owner 支持 user／group／project，私聊 Sandbox 以不可由请求方
  伪造的 session grant ID 隔离 Workspace，群聊则以 canonical 外部 group ID 共享。跨平台、跨 owner、跨
  session 的消息或 Workspace/grant 组合全部失败关闭。
- Sandbox 唯一 Access Policy 新增 grant 与 Workspace owner 的交叉绑定校验，并继续同时要求 Workspace active、
  Workspace quota、Runtime quota 和维护 generation 全部已应用。由此私有 Workspace、群组共享 Workspace、
  project owner、ACL、硬配额和既有 provision／quiesce／recycle 生命周期形成同一服务端授权链，模型元数据
  不能把一个会话的 grant 指向其他 owner 的 Workspace。
- 恢复证明拆分为 Runtime 中立的 MEMORY、WORKSPACE、ARTIFACT、SECURITY 四类作用域计划，以及 Native
  独有的 MANIFEST 计划。中立引用固定稳定 Agent 身份、canonical owner/session、Memory Provider Registry、
  ToolPlan、Sandbox grant／ACL 和 Workspace；不包含 KT／Native 实现名。legacy/canonical 会话别名和
  Runtime 切换均产生相同引用，实际群组 Workspace 也优先按 grant 精确解析。
- 模型可见路径继续只使用 `/workspace`、`/runtime` 和只读 `/inputs` 等容器内虚拟路径；宿主 owner key、
  Workspace 根目录和资产真实路径只在服务端授权及恢复摘要中使用，不进入 Runtime 请求。现有路径规范化、
  符号链接、路径穿越、跨 Workspace 及容器重建持久化合同测试继续覆盖该边界。
- Agent／Memory／Message／Sandbox／Recovery／Prompt Runtime 联合定向回归为 200 passed；首轮全量发现的
  旧测试夹具已按真实私聊 grant owner 和 64 位 ToolPlan 摘要合同修正，专项回归为 13 passed，Sandbox／
  Workspace 文件系统组复验为 98 passed。最终完整 `python -m pytest tests/ -v` 为 6689 passed、
  12 skipped、0 failed；架构边界、OpenAPI、Release／Verification／Behavior Golden、决策规则、Task SLO、
  Ruff、Python 编译和 `git diff --check` 均通过。

#### 7.4 记忆质量和作用域

- [x] 在现有数据模型上明确 working、episodic 和 semantic 层次。
- [x] 记录证据来源、置信度、冲突、衰减、删除和注入预算。
- [x] 区分 Agent 私有、用户私有、群组共享和项目共享记忆。
- [x] 知识图谱或新后端必须先通过真实中文会话评测。
- [x] 不因外部项目宣称直接替换当前 RAG 和画像治理。

验收条件：每次数据、工具、Sandbox 和子 Agent 访问都能解释“谁以什么授权访问什么资源”。

实施记录（2026-08-05）：

- 新增统一记忆治理清单，在不迁移现有表和事实源的前提下，把 `ConversationTurn`、滚动摘要、会话片段、
  原始 `ChatLog`、记忆摘要、画像事实、群记忆、知识文档和语义索引明确归入 working、episodic 或
  semantic 层，并标注 raw evidence、canonical memory 或 derived index 存储职责。每类来源同时声明证据
  来源、置信度、冲突策略、衰减、删除语义及注入项数／字符／token 预算；派生索引不再被描述成权威事实源。
- 新增不可变 `MemoryAccessContext` 和 Agent 私有、用户私有、群组共享、项目共享四类作用域。授权上下文只从
  受信的 principal、session、agent 和 Runtime 身份派生，并验证 canonical／legacy 会话别名；模型提交的
  `user_id`、`session_id` 和 `group_id` 不再进入工具 schema，也不能覆盖服务端身份。记忆 Provider 的结果
  metadata 会解释访问主体、授权来源、Provider、工具、资源、会话和项目范围。
- 私有摘要、滚动摘要、群记忆、画像和知识检索在列表、搜索、聚合、RAG recall、按 ID 展开及 parent chain
  各入口都执行同一作用域过滤；按 ID 或来源命中不能再绕过 owner。知识文档写入显式 Agent／Project scope，
  旧的未标注文档按既有部署事实收口为 `nanobot` 项目共享；语义 chunk 只继承来源作用域，不扩大授权。
- 画像和群记忆注入同时执行项数、字符和共享 token 估算预算，并在 debug evidence 中记录授权、候选数、
  实际注入量及预算消耗。Sandbox 与子 Agent 继续复用阶段 7.1／7.3 的统一治理信封和稳定身份，因此记忆、
  工具、工作区与子任务均能用同一 principal／agent／session／grant 链解释访问主体、授权和资源边界。
- 新记忆后端或知识图谱采用失败关闭的注册门禁：必须提供至少 50 段真实中文会话的不可变评测 manifest，
  每项通过且不存在作用域泄漏或删除失败，并证明质量正增益；缺失、伪造摘要或未注册候选均不能成为活动后端。
  本阶段没有因外部项目宣传引入图后端或替换现有 SQLite／RAG／画像治理，活动后端仍为现有实现。
- 同步更新 `memory_query`、`knowledge_query`、`sticker_search` 的 canonical Prompt Runtime 使用契约，以及
  已受版本控制的 sticker 运行时模板；行为 Golden 和决策规则清单已按当前源码重新生成。
- 记忆治理新增 11 项端到端专项测试；记忆／RAG 联合定向回归为 224 passed，画像／群记忆预算回归为
  47 passed。最终完整 `python -m pytest tests/ -v` 为 6699 passed、12 skipped、0 failed，耗时
  520.98 秒；架构边界、OpenAPI、Release／Verification／Behavior Golden、决策规则、Task SLO、Ruff、
  Python 编译和 `git diff --check` 均通过。

### 阶段 8：有界多 Agent 编排和协作

#### 8.1 显式 DAG 编排

- [x] 定义角色、任务、依赖、输入、输出合同和完成条件。
- [x] 定义预算、并发、审批、取消、汇总和 checkpoint。
- [x] Worker 不直接 peer-to-peer。
- [x] 服务端拒绝无限递归和无预算 spawn。
- [x] 单 Agent 主链路继续作为默认模式。

实现记录（2026-08-05）：

- 新增框架无关且不可变的多 Agent 合同，显式声明 coordinator、worker、reviewer、aggregator 角色，
  任务依赖、窄 JSON 输入／输出、完成条件、聚合节点、计划版本和内容 Hash。计划构造时拒绝重复节点、
  未知依赖、循环、未绑定字段、缺失聚合来源和修改后仍复用旧审批；执行批次按依赖与 `task_id` 确定生成。
- 新增真实 `AgentTaskExecutor` 调用路径和有界 DAG 调度器。Worker 只接收协调者解析后的输入、依赖回执与
  固定的 `status`、`summary`、`next_actions`、`artifacts`、`data` 输出合同，不持有 peer send 或调度器；
  请求级深度绑定同时禁止 Worker 再次 spawn，因而不是只记录计划而不执行任务的 Shadow 路径。
- 计划预算显式覆盖任务数、并发、模型调用、token、成本、时间、输出、checkpoint 和单层 spawn；实际
  Worker 用量计入父 Run、Turn 与 Subagent 账户。预约绑定 scope 和 Turn，身份必须与预算账户的 Run、
  Turn、owner 完全一致；无父预算、剩余预算不足、超时、取消未确认、输出合同不满足或检查点保存失败均
  关闭式终止，且失败依赖不会触发聚合，也不会发生隐式重试。
- 每个任务终态生成不可变 receipt 与单调 checkpoint，内存 Store 明确仅供测试；生产启用仍必须满足持久
  checkpoint 和事件账本门禁。`multi_agent_orchestration_v1` 保持 experimental、默认关闭，因此当前生产
  单 Agent 主链路不变；后续 8.2～8.4 再接入最小权限子 Runtime、持久恢复和人机入口。
- 新增 14 项专项测试，并完成 Runtime／KT／生命周期联合回归。架构边界、OpenAPI、Behavior、Release、
  Verification、决策规则、Task SLO、Ruff、Python 编译和 `git diff --check` 均通过；最终完整
  `python -m pytest tests/ -v` 在 Linux `/var/tmp` basetemp 下为 6713 passed、12 skipped、0 failed，
  耗时 603.80 秒。

#### 8.2 子 Agent 权限和模型分工

- [x] 子 Agent 只继承最小 workspace、工具、网络、Skill、MCP、记忆和预算。
- [x] 探索和检索任务可使用低成本模型。
- [x] 验证和裁判使用独立高质量模型。
- [x] 子 Agent 通过结构化 output contract 返回结果。

实现记录（2026-08-05）：

- 将任务用途、模型等级、模型路由、能力要求和运行预算纳入已批准计划 Hash。探索／检索任务可选择固定的
  economy 路由；验证／裁判／聚合任务必须选择 quality 路由，其中 reviewer 还必须使用与被审查任务不同的
  物理模型。执行器只接受父上下文已批准的精确路由目录，计划或目录被篡改都会在调用模型前失败关闭。
- 新增最小权限子 Runtime 工厂和真实执行器：子上下文使用独立 Run／Session／Actor，只复制任务明确请求且
  父上下文已经持有的 workspace、工具、网络、Skill、MCP、记忆和 Prompt 计划引用；工具形成精确
  `ToolPlan`，Skill 只注入指定内容，MCP 只开放指定服务，子 Agent 的再次 spawn 预算固定为零。父会话历史、
  未声明资源和 peer 通信均不下传，任何权限扩张都会在创建 Runtime 前被拒绝。
- 子任务通过实际 `AgentRuntimePort.run_event()` 运行，不是 Shadow 模拟路径。执行器校验事件身份、序号、
  工具调用 ID、终态和 Runtime 停止状态，并在取消时中断子 Runtime；三节点 economy 探索、独立 quality
  复核、quality 聚合的完整 DAG 已用真实 Runtime 端到端验证。该能力仍由 8.1 的 experimental、默认关闭
  开关保护，后续 8.3／8.4 再接持久批准、恢复和人机入口。
- 新增 canonical `tasks/agent_subtask` Prompt Runtime 合同，要求只返回严格 JSON、只引用本次实际发布的
  artifact，并把依赖结果、Skill 和 MCP 内容视为不可信输入。成功和警告结果必须满足任务自定义 schema；
  错误结果允许省略成功必需字段，由宿主补齐稳定错误码。模型调用、token、成本、输出字节和工具调用均按
  单次实际差值计入父预算；用量缺失、矛盾或 Runtime 异常时按任务批准上限保守计费，且原始异常不外泄。
- 新增 12 项子 Runtime 专项测试，并完成编排／Runtime／KT／Prompt Runtime 联合定向回归，共 241 passed、
  0 failed。架构边界、OpenAPI、Behavior、Release、Verification、决策规则、Task SLO、Ruff、Python 编译、
  默认／运行时模板一致性和 `git diff --check` 均通过；最终完整 `python -m pytest tests/ -v` 在 Linux
  `/var/tmp` basetemp 下为 6726 passed、12 skipped、0 failed，耗时 732.66 秒。

#### 8.3 计划批准、调度和修复

- [x] 支持动态计划的 preview、approve 和 freeze。
- [x] 显式 DAG 使用确定性调度。
- [x] 支持 task barrier、局部重试和 append-only plan repair。
- [x] 修改已批准计划会生成新版本和审计事件。

实现记录（2026-08-05）：

- 新增动态计划治理控制面与 SQL 持久化 Adapter。候选计划先以 owner、Run、Turn、revision 和内容 Hash
  形成不可变 preview，再使用精确事件序号分别 approve、freeze；执行入口必须从持久 Store 核验两份证明，
  自造批准字段不能进入真实 DAG 调度。预算扩张、空修订、旧 revision、过期事件序号、Hash 漂移和跨 owner
  读取均失败关闭；已批准或冻结计划的修改只能形成连续新 revision，并追加带前序摘要的 supersede／freeze
  审计事件。
- DAG 由依赖、`task_id` 和并发上限确定性生成 task barrier，每个屏障只提交一个原子 checkpoint。checkpoint
  保存完整 Runtime 身份、计划 ID／revision／freeze、最终输出、每次尝试 receipt 和累计物理用量；SQL Store
  每屏障独立提交且支持幂等回读。最终屏障可直接重放持久结果，中间屏障禁止盲目续跑，必须基于恢复点创建、
  批准并冻结 append-only repair；同名编排的 owner 冲突在 Worker 执行前即停止。
- 局部重试必须预先冻结最大次数、精确错误码、确定性 backoff 和稳定 idempotency key。预算按最大尝试数保守
  预约，权限、预算、模型、输入和输出合同在重试间不扩张；权限、身份、预算、停止不确定等治理错误不可重试。
  每次尝试使用独立子 Run／Session／Actor，但携带同一幂等键和连续失败回执；实际模型、token、工具、成本与
  输出用量即使触发拒绝仍进入累计 checkpoint 证据。
- 新增计划修订、审计事件和 checkpoint 三张表，事件通过复合外键绑定计划 revision；SQLite 为三表安装禁止
  UPDATE／DELETE 的追加式触发器。首计划与 preview 事件、批量 supersede／freeze 事件都在 savepoint 中
  原子写入，并在并发唯一键冲突后保持外层事务可用。canonical `tasks/agent_subtask` 模板升级至 v2，明确
  `attempt_no`／`idempotency_key` 只属于冻结重试元数据，默认与运行时模板保持一致。
- 新增 15 项计划治理、持久恢复与真实子 Runtime 重试专项测试，并扩展既有编排预算、checkpoint、owner 隔离
  和迁移顺序回归。架构边界、OpenAPI、Behavior、Release／Verification Golden、决策规则、Task SLO、Ruff、
  Python 编译、模板一致性和 `git diff --check` 均通过；最终完整 `python -m pytest tests/ -v` 在 Linux
  `/var/tmp` basetemp 下为 6741 passed、12 skipped、0 failed，耗时 629.39 秒。

#### 8.4 人机与多 Agent 协作入口

- [x] 先复用群聊、Agent Link 和现有任务表。
- [x] 支持 `@agent`、任务认领、交付物和人工审批。
- [x] 评估 room、pod、task board 和 handoff 数据模型。
- [x] 本阶段不复制 Commonly、LobeHub 或 Orca 的完整 UI。

验收条件：多 Agent 运行有确定预算、权限、结束条件、恢复点和责任归属；关闭多 Agent 后不影响现有单 Agent 行为。

实现记录（2026-08-05）：

- 新增不可变协作任务板和追加式 Hash 链事件。任务板绑定 owner、已批准并冻结的计划版本、Runtime 身份、
  根输入、来源和截止时间；任务认领直接复用现有 `RunTaskControl` 的 lease、fencing、状态与恢复语义，未另建
  调度器。认领、交付、批准和拒绝均记录稳定责任主体、任务、交付物摘要与审计事件，原始 lease token 不进入
  事件或状态投影；每个 task barrier 继续使用阶段 8.3 的持久 checkpoint，支持幂等恢复和局部修复。
- 新增真实 Agent Link 协作端口。只有握手时声明并获准协作能力的连接才能查询、认领和交付；服务端从受信
  平台及设备身份派生 actor，忽略客户端伪造身份，并在异步 WebSocket 主循环之外执行数据库事务。过期邀请、
  未声明能力、跨 owner、失效 lease 和旧 fencing 均失败关闭，且协议错误不会断开其他合法会话能力。
- 群聊入口新增严格、确定性的 `@agent` 命令语法，支持分派、状态、带交付物摘要的批准和拒绝。命令只允许
  超级用户使用，owner 从 canonical 消息 principal 获取而不是信任群别名；匹配成功后绕过模型但仍复用既有
  消息持久化和恢复路径。人工批准已经提交但 checkpoint 推进暂时失败时，接口明确返回
  `checkpoint_pending`，不会谎报回滚，可由相同请求幂等恢复。
- 新增受管理员令牌保护的计划 preview／approve／freeze、任务板、认领、交付和人工审查 API。协作能力由
  `agent.multi_agent.enabled` 控制，默认关闭且只能从环境或默认配置读取；关闭时 Agent Link 不广告能力，
  非严格群聊文本继续进入原单 Agent Timing／Bridge 主链路，管理协作接口拒绝执行，因此现有单 Agent 行为
  保持不变。
- 数据模型评估结论为：现有 owner／source 已能表达 room 边界，冻结角色与 DAG 已能表达 pod，新增不可变
  task board 负责责任和执行投影，追加式事件链负责 handoff 与审批证据。本阶段因此没有增加 room／pod 表，
  也没有复制 Commonly、LobeHub 或 Orca 的完整 UI；控制面保持为群聊、Agent Link 和管理 API。
- 本阶段没有改变 `enriched_query`、历史注入、conversation、工具输出或 Prompt Runtime 输入合同；检查
  canonical `chat`、`tasks`、`tools` 模板及变量注册后无需修改模板。新增协作、Agent Link、群聊与治理路径
  已完成定向和联合回归；最终完整 `python -m pytest tests/ -v` 为 6765 passed、12 skipped、0 failed，
  耗时 614.18 秒。OpenAPI、行为基线、Release／Verification Golden、决策规则、Task SLO、架构边界、
  Ruff、Python 编译和 `git diff --check` 均作为本模块提交前门禁执行。

### 阶段 9：Gateway、主动能力和 Provider 治理

#### 9.1 多渠道 Gateway 与远程会话控制

- [x] 统一 QQ、Web、Agent Link 和未来 IM 的 session binding。
- [x] 支持状态、pending approval、pending question、stop、resume 和 model switch。
- [x] 远程客户端只能控制已有授权 Run。
- [x] 任何远程操作不得绕过身份、ACL、ToolPlan 和 Sandbox。

实现记录（2026-08-05）：

- 新增统一 Gateway session binding、不可变 Run binding 和追加式控制审计事实。QQ、Web 与 Agent Link
  都从 canonical 消息合同生成同一类型化 admission，并在 Run Ledger、AgentRun 和 Durable Task 接纳事务内
  原子写入；未来 IM 只需复用相同合同，渠道 Adapter 不直接写内部表。客户端伪造字符串元数据、伪造
  `gateway.source`、私聊 Runtime user 与受信 principal 不一致或 admission／Runtime session 不一致都会
  失败关闭，类型化内部标记在 Trace 接纳后立即移除，不进入 Prompt、持久元数据或模型上下文。
- Gateway 状态只以 Run Ledger 为权威来源，并用 Durable Task 表示真实停止请求；状态投影包含 terminal、
  pending approval、pending question、stop 和 resume 能力。控制主体必须精确匹配不可变 Run binding 的
  owner、transport 与 Runtime session，只有已通过管理令牌认证的管理员可显式越过渠道范围；不存在绑定、
  Ledger 缺失或事实不一致均拒绝控制。stop、resume 和 model switch 使用按主体隔离的请求 Hash、指纹冲突
  检查和追加式审计，支持并发安全的幂等重放。
- Agent Link 将 status、stop、resume 和 model switch 作为逐项可协商能力，服务端只广告客户端声明且
  Composition Root 已注入的能力。控制身份完全来自已认证 peer；协议错误返回 `session.error`，不关闭合法
  连接。resume 只为终态或等待人工交互的 Run 授权渠道续接，并要求已同步工具快照和完整标准 chat 对象；
  授权后重新进入既有 `chat.submit` 路径，因此继续执行消息身份、ToolPlan、工具权限和 Sandbox 门禁，而不
  伪造 checkpoint replay 或建立旁路执行器。
- 模型切换只接受当前已验证 reply Route 中的 Profile，公开描述不包含凭据、Base URL 或 Codex 账号 ID。
  会话投影使用 generation CAS 保存“下一 Run 生效”的 pending Profile；新 Run 接纳时才将其提升为 active，
  当前 Run 的候选链保持冻结。模型 Profile 解析通过框架无关 Port 由 Composition Root 注入，Gateway 核心和
  管理 API 不依赖 KT；KT 仅保留候选验证与 Route 投影 Adapter，`bridge.py` 仍低于冻结体积上限。
- 新增管理员 Gateway 状态／停止／续接授权／模型切换 API，以及 Agent Link 与真实消息 Adapter／RunTracer
  的端到端回归。OpenAPI、Behavior、Release／Verification Golden、决策规则、Task SLO 和架构边界均通过；
  65 项 Gateway 联合回归及 293 项契约、Golden、Prompt Runtime 回归通过。本阶段未改变 `enriched_query`、
  历史注入、conversation、工具输出或 Prompt Runtime 输入合同；检查 canonical `chat`、`tasks`、`tools`
  模板、变量注册和模板注册表后无需修改模板。
- 最终完整 `python -m pytest tests/ -v` 在清除代理变量并使用 Linux `/var/tmp` basetemp 后为
  6783 passed、12 skipped、0 failed，耗时 631.29 秒；全仓致命 Ruff 规则、本轮变更文件完整 Ruff、Python
  编译和 `git diff --check` 均通过。全仓普通 Ruff 另报告 8 个既有未使用导入，均位于本阶段未修改的基线
  文件，按严格守界约定未顺手改动，也不影响本阶段 0 failure 验收。

#### 9.2 主动能力和 Sentinel 收敛

- [x] 将主动外呼、定时任务、事件触发和 heartbeat 统一为 `Trigger → Evaluate → Lease → Run → Deliver`。
- [x] 默认关闭并保留冷却、预算、幂等和 ambiguous 冻结。
- [x] 保存用户反馈和运行证据，供后续评测。
- [x] 不允许主动任务自行扩大权限。

实现记录（2026-08-05）：

- 新增框架无关、不可变的类型化 Trigger 信封，统一表达 schedule、manual、event 和 heartbeat，并冻结精确
  owner、来源、幂等摘要、TTL、治理快照、工具／交付／子 Agent 授权和硬预算。Trigger 与父 Run 通过类型化
  Ledger binding 关联；普通字符串 metadata 不能伪造权威绑定。运行阶段、预算预留和终态均进入追加式
  Ledger，终态写入支持暂时性持久化失败后的幂等重试，且禁止重试时改写最终状态。
- 主动外呼在评估 lease 成功后创建统一 Trigger 和父 Run，模型判断、生成、质量检查、研究及交付分别消费
  冻结预算；每日配额默认 2，设为 0 时暂停新候选。功能继续默认关闭，并保留冷却、静默时间、幂等和
  ambiguous 冻结。评估依据只作为服务端持久化证据，不进入 judge／generator 模型输入，避免内部触发证据
  污染用户可见输出。
- 定时工作流把冻结 Trigger 信封保存进现有任务快照，并在每一步执行前校验 owner、TTL、精确工具和交付授权。
  模型步骤在入队时冻结 ToolPlan，Bridge 只接受受信类型化约束并在 Runtime 扩展后再次收窄；循环数据中的
  伪造 `tool` 字段不能被解释为授权。旧记录只能从自身冻结快照安全回填，不能读取后来变更的在线任务；旧模型
  步骤缺失授权时只保留 `reply`／`no_reply`，主动任务不能通过递归结构或兼容回退扩大权限。
- 新增脱敏终态证据采样和可信反馈事实。采样只保存状态、计数、标识和内容摘要，不保存用户 ID、消息正文或
  Judge 文本；相关子查询避免已采样记录阻塞后续候选。用户报告和运营复核通过受管理员令牌保护的接口写入，
  相同证据幂等、冲突证据拒绝覆盖，为后续离线评测提供可追溯来源。
- 本阶段检查了 canonical `chat`、`tasks`、`tools` 模板、变量与模板注册表。受信 Trigger metadata 在 Prompt
  编译前被消费并投影为安全摘要，模型可见业务结果、`enriched_query`、历史、conversation 和工具输出合同
  均未改变，因此无需修改模板。前端已同步每日主动配额设置并重建正式发布资产。
- Trigger、主动外呼、定时工作流、Bridge、证据、反馈、Prompt 和生成合同定向联合回归为 456 passed。
  最终完整 `python -m pytest tests/ -v` 在清除代理变量并使用 Linux `/var/tmp` basetemp 后为
  6800 passed、12 skipped、0 failed，耗时 623.34 秒；前端 lint、测试和生产构建，架构边界、OpenAPI、
  行为基线、Release／Verification Golden、决策规则、Task SLO、受控范围 Ruff、Python 编译和
  `git diff --check` 均通过。

#### 9.3 Provider 诊断和成本治理

- [x] 在现有模型目录、能力过滤、排序和熔断上增加连接诊断。
- [x] 描述请求协议、stream、tool、image、reasoning 和 cache 能力。
- [x] 记录首 token 延迟、总延迟、token、缓存、成本和错误类别。
- [x] 路由只依据可验证 Descriptor 和运行证据，不依据模型名猜测。

验收条件：不同入口共享同一 Run 语义；Provider 能力、费用和故障均可观测、可验证、可回退。

实现记录（2026-08-09）：

- 扩展不可变 `ProviderDescriptor`，固定 Provider 的实际请求协议、请求路径以及 chat、stream、tool、image、
  reasoning 和 cache 能力与证据来源；模型目录规范化仅接受目录显式字段、受控覆盖、Provider API 和成功运行
  Trace 等可审计来源。排序、首选模型和熔断后的候选过滤统一消费该证据，删除依据模型名称推断能力的路径；未
  观测到能力保持 unknown，不会被误判为不支持或被当作可路由事实。
- 新增只读 Provider Doctor，按配置、DNS、TCP、TLS、鉴权、目录、模型、最小 completion 以及可选 stream、
  tool、image 探测分层返回状态、耗时、阻断层、稳定错误类别和是否可重试。探测固定超时和 1 MiB 响应上限，
  显式禁用环境代理，不返回凭据、上游正文或隐藏推理；不支持的请求协议透明报告 unsupported，不伪造成功。
- 将 LLM Trace 的错误归一为稳定类别，并补齐首 token 延迟、总延迟、输入／输出 token、缓存命中／未命中／
  写入 token、成本和成本来源。新增数据库迁移、Provider 近 30 天脱敏运行证据汇总及 Admin API／WebUI 展示；
  成功 Trace 只提供正向能力证据，请求和响应正文不会进入汇总结果。SQLAlchemy 持久化汇总适配器位于
  `clients/`，框架无关 Provider 合同与诊断层继续通过无 SQLAlchemy／FastAPI／KT／具体 SDK 的架构门禁。
- 管理端模型连接页展示 Descriptor、分层 Doctor 与运行证据，模型目录展示能力证据，LLM 日志页支持错误类别、
  延迟、缓存和成本聚合；OpenAPI 和正式 WebUI 发布资产同步更新。前端 lint、17 项测试和生产构建通过；架构
  边界、OpenAPI、Release Impact、Verification Plan、Task SLO、决策规则、行为基线、受控 Ruff、Python 编译
  与 `git diff --check` 全部通过。
- 本阶段未改变 `enriched_query`、历史注入、conversation、工具输出或 Prompt Runtime 输入合同；canonical
  `chat`、`tasks`、`tools` 模板、变量和模板注册表经全量回归确认无需修改。最终完整
  `python -m pytest tests/ -v` 在清除代理变量并使用 Linux `/var/tmp` basetemp 后为 6815 passed、
  12 skipped、0 failed，耗时 679.22 秒。

### 阶段 10：可观察性、评测和受控自进化

#### 10.1 统一 Trace 和离线 Run Viewer

- [x] 将 LLM、Prompt、Tool、Memory、MCP、Sandbox、Subagent、Cache、Artifact 和 Delivery span 关联到 Run/Turn。
- [x] 提供脱敏时间线、DAG、token/cost waterfall 和上下文 manifest。
- [x] 显示失败点、重试、恢复和版本，不展示隐藏推理。

实现记录（2026-08-09）：

- 新增框架无关的 `core/observability/run_view.py`，仅从已持久化且经过白名单处理的证据生成版本化离线
  Run Viewer；统一关联 Run/Turn 下的 LLM、Prompt、Tool、Memory、MCP、Sandbox、Subagent、Cache、
  Artifact、Delivery、Checkpoint、副作用回执和恢复操作，并输出脱敏时间线、contains/retry DAG、
  token/cost waterfall、失败点、重试、恢复和版本集合。Viewer 不调用模型、不执行工具、不恢复任务，且不
  接受 Prompt／消息正文、工具参数／结果、Sandbox 命令／输出、凭据或隐藏推理正文。
- 新增 SQLAlchemy 只读 Adapter，将 Runtime Telemetry、Sandbox Run、Workspace Asset、Checkpoint、
  Side Effect 和 Recovery 与现有 AgentRun、ToolCall、PromptRenderLog、LLMApiRequestLog、Run Ledger
  投影合并；Admin `GET /agent-runs/{run_id}` 返回独立 `viewer` 读模型。Prompt Trace 增加经 canonical
  校验且限制为 256 KiB 的完整无正文 Context Manifest 列和幂等迁移；旧记录继续回退到 Run Ledger
  指纹，不伪造完整清单。
- Runtime Event Registry 新增 `mcp.call` 与 `subagent.execute`，在实际 MCP 请求边界和子 Agent 执行器
  记录 parent Run、child task run、Turn、ToolCall、模型、尝试次数、时延、用量、失败类别和可重试性；
  Descriptor 不允许参数、结果、任务输入、Skill 正文、总结或隐藏推理进入事件。取消、模糊状态和缺失调用
  ID 均有稳定终态与唯一 span，Telemetry 持久化仅新增受控 token 指标字段。
- 管理端 Run 详情页新增离线 Viewer，展示时间线、DAG、成本瀑布、Context Manifest、失败／重试／恢复和
  版本证据。原 LLM Trace 页面递归省略 `reasoning_content`、`reasoning`、`reasoning_text`、`thinking`
  和 `thinking_content`，包括脱敏 Raw JSON，仅保留 reasoning token、字符数和耗时等计量；生产 WebUI
  资产同步更新。
- 定向后端回归为 198 passed；前端 lint、19 项测试和生产构建通过。架构边界、OpenAPI、Release Impact、
  Verification Plan、Task SLO、决策规则、行为基线、受控 Ruff、Python 编译与 `git diff --check` 全部
  通过；Runtime Registry 和 Admin Table View 的受控行为 Golden 及决策规则清单已用项目生成器同步。
- 本阶段只新增观测持久化和展示，没有改变 `enriched_query`、历史注入、conversation、工具输出合同或
  Prompt Runtime 输入；canonical `chat`、`tasks`、`tools` 模板、变量与模板注册表经全量回归确认无需
  修改。最终完整 `python -m pytest tests/ -v` 在清除代理变量并使用 Linux `/var/tmp` basetemp 后为
  6819 passed、12 skipped、0 failed，耗时 725.24 秒。

#### 10.2 回放、对比和故障注入

- [x] 使用冻结 Event 和模型替身进行确定性回放。
- [x] 支持 Runtime、Prompt、模型、Skill 和 Context 策略 A/B diff。
- [x] 注入超时、断流、工具失败、DB 锁、lease 丢失和 Sandbox 重启。
- [x] 验证恢复不会重复副作用。

实现记录（2026-08-09）：

- 新增框架无关的 `core/replay` 稳定合同和执行引擎。冻结 fixture 只接受连续 Event 序号、安全标识符、
  状态、计数和 SHA-256，不保存消息、Prompt、工具参数／结果或隐藏推理正文；输入使用严格字段白名单、
  数量上限和版本校验。回放明确标记为离线 semantic replay 和 `wire_exact=false`，不把语义恢复夸大为
  逐字节请求重放。
- 冻结模型替身将每个响应绑定到 fixture、Runtime／Prompt／模型／Skill／Context 五维策略指纹、步骤和
  前序状态的请求摘要；摘要或步骤不匹配时拒绝运行，且模型外部调用数固定为零。冻结工具替身只消费只读
  结果或已落定的副作用回执；副作用工具缺回执、回执未终止或状态不一致时 fail closed，回放不会调用真实
  工具，副作用执行数与重复执行数均固定为零。
- A/B 对比在同一冻结 Event 上分别执行 baseline 与 candidate，逐维报告 Runtime、Prompt、模型、Skill
  集和 Context 策略的 ID／摘要变化，同时比较终态、输出摘要、状态摘要、Trace 和 token／成本差值；报告
  不自动宣布质量赢家，输出变化必须交给后续质量评测。
- 故障矩阵将模型超时、流中断、工具失败、DB 锁、lease 丢失和 Sandbox 重启拆成六次独立运行，避免前一
  终态掩盖后续覆盖。DB 锁使用有界 checkpoint 重试，lease 丢失立即取消，Sandbox 重启先恢复冻结
  checkpoint 再复用副作用回执；矩阵强制检查故障已命中、恢复已发生、回执已复用且无重复副作用。
- 新增 `python -m evals.replay compare|fault-matrix` 实际 CLI，以及受管理员认证的
  `POST /evals/replay/compare`、`POST /evals/replay/fault-matrix`；两条管理接口真实执行回放并把安全报告
  写入现有 EvalRun／EvalRunResult，审计只记录 fixture ID、报告摘要和汇总字段。OpenAPI 生成物和兼容
  Admin façade 已同步，不存在只观测不执行的 shadow 分支。
- 新增 10 项回放专项测试；Runtime、恢复、评测 API 与治理门禁定向回归为 201 passed。架构边界、
  OpenAPI、Release Impact、Verification Plan、Task SLO、行为基线、决策规则清单、受控 Ruff、Python
  编译与 `git diff --check` 均通过；决策规则清单已用项目生成器同步。
- 本阶段没有修改 `enriched_query`、历史注入、conversation、工具输出合同或 Prompt Runtime 输入；
  canonical `chat`、`tasks`、`tools` 模板、变量和模板注册表经完整回归确认无需修改。最终完整
  `python -m pytest tests/ -v --basetemp=/var/tmp/nanobot-pytest-stage10-2` 在清除代理变量后为
  6829 passed、12 skipped、0 failed，耗时 758.45 秒。

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
