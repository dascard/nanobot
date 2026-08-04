# Agent Harness 生态源码核验：第二批平台、身份、记忆与扩展

> 状态：第二批核验完成
>
> 元数据快照时间：2026-08-03 12:47 UTC
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md` 阶段 0.3
>
> 范围：其余 10 个 stars 不低于 1000 的候选项目；全部固定到完整 commit 后读取实现、测试和许可证

## 1. 结论先行

第二批源码核验进一步确认：Nanobot 不应选择另一个大型项目整体替换 KT，也不应把某个项目宣称的“Agent 平台”直接当成已验证的 Runtime。适合后续吸收的是边界明确、可独立验证的机制：

1. Agent 的身份、配置、人格、Skill、权限和记忆应由 Nanobot 自有 Manifest 编译，而不是由某个 Runtime 私有对象决定；
2. 子 Agent 必须继承父级权限上界，并继续收紧工具、Skill、模型、工作区、预算和递归能力；“换一个上下文调用 LLM”不等于安全委派；
3. Workspace、Agent Profile、Skill 和运行配置都需要稳定身份、来源、版本、快照或内容哈希，不能只保存显示名称；
4. 任务抢占必须有租约、token、心跳和 fencing。只有 `pending → claimed` 的原子更新仍会在 worker 崩溃后永久卡住；
5. 事件 claim／ack、幂等 mutation receipt、运行时 generation 和 `outcome_unknown` 是恢复边界；普通状态行和可轮转 Trace 不能冒充不可变 Event Ledger；
6. 主动能力应使用提案、去重、静默时段、额度、待确认状态和反馈闭环，不应让定时 Prompt 直接产生无限副作用；
7. 自进化只适合离线候选、隔离工作树、冻结内核、成对评测和人工提升；自动提交并推送主干的模式明确排除；
8. 上游框架集成应固定版本、记录补丁、校验补丁前提并区分配置热同步和重启影响，但大量长期下游补丁本身是需要解除耦合的警报；
9. README 中的“多 Runtime”“workstation”“durable task”“checkpoint”等名称必须回到源码语义核验，不能按产品文案冻结接口；
10. 本批没有复制任何第三方代码。带商业限制、非商业限制或文件级 copyleft 的仓库只提供架构观察和独立重构输入。

## 2. 核验方法和证据等级

### 2.1 方法

- 复用第一批的 GitHub 元数据快照，星数只用于覆盖用户要求的高星项目范围；
- 将 10 个仓库分别克隆到临时目录，记录完整 commit SHA，结论只对应固定版本；
- 同时检查数据结构、执行入口、持久化、权限、恢复、测试和许可证；README 只用于定位；
- 对“多 Runtime”“自治”“checkpoint”“持久化”“隔离”等强声明，查找实际实现和失败路径；
- 对缺少租约、原子写、锁、围栏、递归限制或服务端权限校验的能力，明确降级为单进程、尽力保存、产品层能力或反例；
- 本文只记录模式，不复制实现；许可证结论以仓库根目录的 `LICENSE`／`NOTICE` 为准。

### 2.2 证据等级

| 等级 | 含义 | 后续用途 |
| --- | --- | --- |
| A | 固定 commit 的实现、测试或多处调用相互印证，失败边界可定位 | 可进入 Nanobot 设计，但仍需独立实现 |
| B | 固定 commit 有真实实现，但测试、持久化、安全或恢复边界不完整 | 只作为兼容适配、实验或反例 |
| C | 只在 README、架构图或类型接口中出现 | 不进入实现合同 |
| U | 无法访问、已删除或尚未固定版本核验 | 不进入实现合同 |

同一仓库可以同时包含 A、B、C 级结论。例如 LobsterAI 的 OpenClaw 配置交付是 A 级证据，但“多 Runtime”在当前版本只有一个实现，只能算 C 级产品方向。

## 3. 固定版本和许可证边界

| 项目 | 固定 commit | 仓库许可证原文结论 | 本批证据等级 | 许可证处理 |
| --- | --- | --- | --- | --- |
| [LobeHub](https://github.com/lobehub/lobehub/tree/93fd6f854f6eed7cdf1d4990a7bdded1e6bc0e00) | `93fd6f854f6eed7cdf1d4990a7bdded1e6bc0e00` | LobeHub Community License，基于 Apache-2.0 并限制派生作品商业分发 | A/B | 只研究数据模型；不得直接复制受限实现 |
| [Orca](https://github.com/stablyai/orca/tree/e08eba674c195596228834bf3c1ef4f94e6b118e) | `e08eba674c195596228834bf3c1ef4f94e6b118e` | MIT | A | 可借鉴模式，复制仍需保留版权和许可声明 |
| [Yao](https://github.com/YaoApp/yao/tree/d0bd9cc284c9cfc805f03331e9ca50f66dd0a39a) | `d0bd9cc284c9cfc805f03331e9ca50f66dd0a39a` | 修改版 Apache-2.0，含品牌、证书校验和企业规模商业授权条款 | B | 只研究合同；不得按 Apache-2.0 普通项目处理 |
| [LobsterAI](https://github.com/netease-youdao/LobsterAI/tree/8a809de79188944fc934cb574fbcc43c8e29ee1a) | `8a809de79188944fc934cb574fbcc43c8e29ee1a` | MIT | A/B | 可借鉴 Adapter 维护模式 |
| [OpenHanako](https://github.com/liliMozi/openhanako/tree/8293cf068e4a1934274576ddd21df5f584cd6645) | `8293cf068e4a1934274576ddd21df5f584cd6645` | Apache-2.0 | A/B | 独立重构并保留必要 notices |
| [Kun](https://github.com/KunAgent/Kun/tree/d86d44455a554b217a1ea03d62c37517b849dee7) | `d86d44455a554b217a1ea03d62c37517b849dee7` | PolyForm Noncommercial 1.0.0，商业用途需书面许可 | A | 只能作为设计观察，禁止复制到本项目 |
| [AutoDev](https://github.com/phodal/auto-dev/tree/23777fe2d3f1526694f3dd324d82595f651f28bc) | `23777fe2d3f1526694f3dd324d82595f651f28bc` | MPL-2.0 | B | 若复制 Covered Software 会触发文件级义务；本计划不复制 |
| [Raven](https://github.com/EverMind-AI/Raven/tree/c89460fa9f285e55942510b71722681e08645e83) | `c89460fa9f285e55942510b71722681e08645e83` | Apache-2.0；`NOTICES.md` 另列 nanobot、hermes-agent、ink 等 MIT 来源 | A/B | 需同时检查第三方 notices；这里只独立实现模式 |
| [Yoyo](https://github.com/yologdev/yoyo-evolve/tree/1714c3db0ad35a1672eee4afbb012adb37327ee5) | `1714c3db0ad35a1672eee4afbb012adb37327ee5` | MIT | A/B | 只借鉴离线证据和门禁，不采用自动推送流程 |
| [Commonly](https://github.com/Team-Commonly/commonly/tree/e13bf0fae798c941508ff0834765c3416709d579) | `e13bf0fae798c941508ff0834765c3416709d579` | Apache-2.0；`NOTICE` 明确品牌不随 Apache 许可 | A/B | 不使用品牌资产；只独立实现合同 |

GitHub API 在第一批快照中对 LobeHub、Yao、Kun 和 Commonly 返回过 `NOASSERTION`。直接读取许可证后可见：前三者不能被误记为普通宽松许可证，Commonly 则是带品牌 notice 的 Apache-2.0。

## 4. 跨项目确认的设计边界

### 4.1 身份、配置和 Manifest

- LobeHub、Yao、Kun 和 Commonly 都证明 Agent 需要独立于一次会话的身份和配置实体；
- 可靠身份至少包含稳定 ID、owner、workspace／project、来源、版本和 Runtime 绑定，显示名称不能作为唯一键；
- Manifest 编译后要产生可审计快照，并将 Runtime 能力、模型、工具、Skill、MCP、权限和工作区约束统一收口；
- 外部 Manifest 不能直接获得服务端能力。所有声明必须与宿主 allowlist 求交。

### 4.2 子 Agent 与权限衰减

- OpenHanako 和 Kun 都实现了“子级只能比父级更窄”的方向；
- 子 Agent 默认必须失去再次委派、创建工作流、修改长期记忆、创建定时任务和管理全局配置等高风险能力；
- 委派预算不仅是并发数，还包括深度、总生成数、总 token、总时间、工具调用数和 Artifact 大小；
- Yao 和 AutoDev 的委派路径缺少足够明确的深度或能力门禁，只能作为组合 API 参考。

### 4.3 持久任务和恢复

- Orca 的 mutation receipt、worker incarnation、generation fence 和 `start_unknown`／`stop_unknown` 说明“不确定结果”必须是一等状态；
- Kun 的租约、append-only journal、expected sequence、checksum、snapshot、outbox 和 persist-before-publish 接近可迁移的可靠运行模式；
- Commonly 的任务 claim 虽然原子，但没有 lease、expiry、token 和 fencing；worker 崩溃后可能留下永久 `claimed`，不能作为 Nanobot 的 durable task 方案；
- 事件记录、Trace、消息投递和任务状态是不同层级，不能只建一个可变状态表就宣称 Event Sourcing。

### 4.4 记忆、Context 和 Skill

- LobeHub 证明可编辑用户记忆需要类型、证据、置信度、访问和向量索引等解释字段；
- OpenHanako 证明事实抽取需要来源、失效和编译快照，但其 Skill 会话快照只冻结引用，不冻结内容字节；
- Commonly 证明共享记忆必须逐元素标注可见性和 provenance，而不是只按整份文档划分 private／public；
- Raven 的 Curator 适合作为“下一次推理看什么”的有界 Context 策略，但其文件存储不能替代 Nanobot 的持久账本；
- Skill 检索需要来源隔离、固定融合算法、失败遥测和权限过滤，不能把搜索结果直接注入 Prompt。

### 4.5 上游框架维护

- LobsterAI 固定 OpenClaw 版本、维护补丁集、执行补丁前提校验，并区分配置热同步与重启；
- 同一固定版本维护 27 个下游补丁，也说明长期 fork 成本已经很高；
- Nanobot 升级 KT 时应建立“小补丁、强校验、可删除”的临时机制，同时优先通过 Port 和 Adapter 删除补丁，而不是复制长期 fork 模式；
- 只有一个 Runtime 实现的接口仍然可以作为解耦准备，但不能据此宣称已实现多 Runtime。

## 5. 分项目源码核验

### 5.1 LobeHub

#### 已检查源码

- `packages/database/src/schemas/agent.ts`
- `packages/types/src/agent/agentConfig.ts`
- `packages/types/src/agent/agencyConfig.ts`
- `packages/database/src/schemas/agentSkill.ts`
- `packages/database/src/models/agentSkill.ts`
- `packages/database/src/schemas/userMemories/index.ts`
- `packages/database/src/schemas/userMemories/persona.ts`
- `packages/memory-user-memory/src/schemas/identity.ts`
- `packages/database/src/models/plugin.ts`
- `apps/server/src/modules/AgentRuntime/types.ts`
- `packages/database/src/models/__tests__/userMemories.test.ts`

#### 源码确认的行为

- Agent 是独立数据库实体，配置覆盖模型、system role、模型参数、agency、chat、workspace、user 和公开性，而不是一次聊天中的临时结构。
- Agent 与知识库、文件和 Workspace 的关联单独建模，能够区分 owner 和作用域。
- Skill 保存来源（内置、市场、用户）、manifest、正文、编辑数据、资源和压缩包内容哈希，并具有 owner 与唯一性约束。
- 用户记忆按 context、preference、activity、identity、experience 等层分拆；实现中包含向量、访问、捕获、置信度和来源证据等字段。
- Plugin 有 owner、workspace、user、manifest、settings、custom parameters 和 source 等控制面字段。
- `AgentRuntime` 类型支持内存／Redis 状态与流，以及由 owner 原子 claim、refresh、release 的 step；它仍是运行状态和事件分发抽象，不是不可变正确性账本。

#### 对 Nanobot 的取舍

- **直接采用模式：** 可编辑 Agent／Skill／Memory 的稳定身份、owner、workspace、来源、版本和证据字段。
- **兼容适配：** 将 LobeHub 的丰富产品模型压缩为 Nanobot Manifest 编译输入，不复制其完整数据库和 UI。
- **实验：** 分层记忆的置信度、证据和人工编辑冲突处理，先在现有 `personas`／记忆表上做小范围验证。
- **排除：** 把 Redis stream 或可变 Runtime state 直接作为 Event Ledger；复制完整产品 schema。
- **许可证边界：** Community License 限制派生作品商业分发，设计只能独立重构。

### 5.2 Orca

#### 已检查源码

- `src/main/runtime/orchestration/types.ts`
- `src/main/runtime/orchestration/db.ts`
- `src/main/runtime/rpc/methods/orchestration-worker-start-receipt.ts`
- `src/shared/worktree-ownership.ts`
- `src/shared/agent-scratch-worktrees.ts`
- 同目录 orchestration CLI、worker lifecycle 和数据库测试

#### 源码确认的行为

- SQLite orchestration 保存 Run namespace／inbox、任务依赖与父子关系、dispatch context、decision gate、有序消息、delivery 和 mutation receipt。
- Run 主要是命名空间与 home inbox；实际调度和 worker placement 由 CLI／Skill 驱动，不能把它误解为通用服务端调度器。
- Worker 状态显式区分 `start_unknown`、`stop_unknown`、residual resources 和 residual effects；未知结果要求 inspect 或 abandon，不能伪造为普通失败。
- delivery 使用 generation、fence 和 ack；mutation receipt 对 caller、request 与 payload hash 去重。
- worker launch token hash、process incarnation 和 stale dispatch 拒绝共同阻止旧进程继续提交。
- Worktree ownership 区分 `orca-managed`、`agent-scratch`、`external`、`unknown-legacy`；已知 scratch 目录会被显式隐藏。

#### 对 Nanobot 的取舍

- **直接采用模式：** 资源 provenance、ownership 分类、worker incarnation、mutation receipt 和 `outcome_unknown`。
- **兼容适配：** 将 generation／fence 映射到 Run lease token，把 worktree ownership 泛化为 Workspace／Artifact 来源。
- **排除：** 复制 Electron／代码工作区产品层；把 Run namespace 当作已经完成的 durable scheduler。

### 5.3 Yao

#### 已检查源码

- `agent/assistant/types.go`
- `agent/assistant/load.go`
- `agent/assistant/hook/create.go`
- `agent/assistant/hook/next.go`
- `agent/assistant/next.go`
- `agent/assistant/mcp.go`
- `agent/assistant/permission.go`
- `agent/config/resolved.go`
- `agent/context/interfaces.go`
- `agent/context/types.go`
- `agent/context/context.go`
- `agent/memory/memory.go`
- `agent/store/types/types.go`

#### 源码确认的行为

- `AssistantModel` 将 identity、connector、prompt、preset、知识库、数据库、MCP、workflow、sandbox、search、dependencies、permission 和 owner 声明在包配置中。
- Create／Next hook 是可执行的 V8 TypeScript，并可以改写较宽的 Runtime 参数或发起委派；这比普通模板 Hook 的权限大得多。
- MCP 工具名使用 `server__tool` 前缀；默认总量上限 20，每工具最多 3 个示例，并带并行调用和可重试分类。
- 当前顺序跟随 server／list 返回顺序；未看到对工具集合做 canonical sort 和会话快照身份。
- Memory scope 分为 User、Team、Chat、Context；默认过期策略分别是不自动过期、不自动过期、24 小时和 30 分钟。fork 共享前三层，Context 独立。
- 中央 permission 在 `Authorized` 未提供时失败关闭，但工具级细粒度 capability 分散在各模块，尚不足以替代统一权限合同。
- 委派会在相同 Context 中递归流转；没有找到明确的最大委派深度。工具循环另有默认 5 次上限，两者不能混为一谈。

#### 对 Nanobot 的取舍

- **直接采用模式：** 声明式 Agent package 编译、按作用域建模的 Memory，以及 fork 后临时 Context 隔离。
- **兼容适配：** Hook 必须收敛为带类型输入输出、声明 capability、超时和副作用级别的 Nanobot Hook。
- **实验：** MCP 并行与 fallback 需要在稳定排序、Schema 快照和预算门禁完成后再测试。
- **排除：** 任意脚本直接改写 Runtime；没有显式深度和预算的递归委派。
- **许可证边界：** 修改版 Apache-2.0 有明确商业和证书校验限制，不复制源码。

### 5.4 LobsterAI

#### 已检查源码

- `package.json`
- `docs/architecture-openclaw-gui-cowork.md`
- `scripts/ensure-openclaw-version.cjs`
- `scripts/apply-openclaw-patches.cjs`
- `scripts/openclaw-runtime-host.cjs`
- `src/main/libs/agentEngine/types.ts`
- `src/main/libs/agentEngine/coworkEngineRouter.ts`
- `src/main/libs/agentEngine/openclawRuntimeAdapter.ts`
- `src/main/libs/openclawEngineManager.ts`
- `src/main/libs/openclawConfigSync.ts`
- `src/main/libs/openclawConfigDelivery.ts`
- `src/main/libs/openclawConfigImpact.ts`
- `src/main/libs/openclawGatewayRepair.ts`
- `src/main/artifactLocalFileProtocol.ts`

#### 源码确认的行为

- 当前版本固定 OpenClaw `v2026.6.1`，仓库内维护 27 个下游 patch 文件。
- 补丁流程会回到干净 tag，按版本应用 patch，并通过校验器检查预期源码片段和测试前提，避免 patch 静默错位。
- `CoworkRuntime` 定义了统一事件和生命周期接口，但 `CoworkAgentEngine` 当前只有 `openclaw` 一个实现；因此这是 Adapter 抽象，不是已验证的多 Runtime。
- Engine manager 包含启动阶段、loopback 健康检查、readiness timeout、自动重启上限、无效配置抑制和诊断。
- 配置落盘使用临时文件加 rename，并按影响分为无动作、同步或重启。
- 运行中配置交付使用 `config.get` 的 `baseHash` 与 `config.set` ack；冲突时重新获取并重试，失败后才进入有限频率的重启 fallback。
- Gateway repair 在活动 session／task 存在时会阻止操作，并在修复前备份。
- 本地 Artifact 协议把 URL 路径映射为本地路径；在检查范围内未见宿主根目录 allowlist，不适合直接进入服务端安全边界。

#### 对 Nanobot 的取舍

- **直接采用模式：** 固定上游版本、补丁 manifest、强前提校验、配置影响分析、base hash 和 ack。
- **兼容适配：** KT Adapter 可借鉴 Engine lifecycle，但运行健康和业务 Run 状态必须仍由 Nanobot 定义。
- **排除：** 长期积累大型 patch 队列；把任意本地路径协议移入服务端；因接口存在就宣称多 Runtime。
- **对 KT 的直接启示：** 升级时为临时补丁记录原因、上游 issue、前提校验和删除条件；最终目标仍是把业务移出 KT，而不是维护另一个重 fork。

### 5.5 OpenHanako

#### 已检查源码

- `core/agent.ts`
- `core/capability-policy.ts`
- `core/session-permission-mode.ts`
- `lib/tools/subagent-tool.ts`
- `lib/tools/subagent-tool-policy.ts`
- `lib/skills/session-skill-snapshot.ts`
- `lib/permission/tool-invocation-permission.ts`
- `core/session-manifest/checkpoint.ts`
- `lib/checkpoint-store.ts`
- `lib/memory/fact-store.ts`
- `lib/memory/compiled-memory-snapshot.ts`
- `packages/plugin-runtime/src/index.ts`
- `lib/resource-io/resource-refs.ts`
- `lib/desk/automation-suggestion-receipt.ts`
- `lib/desk/heartbeat.ts`

#### 源码确认的行为

- Agent 身份、路径、配置、Memory、Skill 和 Tool 独立组织；人格由可编辑 identity、能力模板和 ishiki 文件组合，guest 使用 public 版本。
- Capability policy 对缺失 capability 或 principal 失败关闭，并检查 scope、transport 和 expiry；本地 owner 有单独路径。
- Session permission mode 包含 auto、operate、ask、read-only，并区分信息型和副作用型工具。
- Subagent 固定屏蔽递归委派、工作流／会话 fan-out、长期记忆写入、automation／cron、channel／DM／notify、安装和设置工具。
- 单 session 最多 10 个子 Agent，全局最多 20 个；子权限从父权限继续衰减，默认不转发批准，Prompt 型批准采用 `deny_on_prompt`。
- Tool invocation 使用冻结的 JSON 输入快照并限制深度、条目和字符串长度；能力由工具命名空间或宿主注册委派。
- Skill session snapshot 只冻结启用列表、来源身份和指针，内容仍从源读取；源被删除时返回明确 unavailable，而不是伪装为不可变内容快照。
- Fact store 使用 SQLite、tag、session／time、FTS、PII scrub、按 session 事务替换和事实失效；随后编译为 today／week／long-term 等可消费快照。
- Session manifest checkpoint 是升级迁移回执，普通 checkpoint 主要保存文本文件编辑前内容，不能泛化为任意 Run 恢复。
- Plugin runtime 定义版本化资源引用、expected-write conflict、执行边界、network abstraction 和 tool context；只看到接口不能证明所有宿主都执行了强 Sandbox。
- “自治”主要由 heartbeat、cron、automation suggestion 和 revision receipt 驱动，不是通用 durable scheduler。

#### 对 Nanobot 的取舍

- **直接采用模式：** 人格来源分离、Capability snapshot、父子权限衰减、子 Agent 禁止递归、事实失效和类型化资源引用。
- **兼容适配：** Skill snapshot 必须明确区分“引用冻结”和“内容冻结”，内容快照另存 hash／Artifact。
- **实验：** 主动提案 receipt 可映射到 Nanobot 现有主动外呼，但需先统一审批、去重和额度。
- **排除：** 桌面本地路径信任、按绝对路径恢复文件、把 Plugin 接口声明当成 Sandbox 事实。

### 5.6 Kun

#### 已检查源码

- `kun/src/delegation/workspace-agents.ts`
- `kun/src/delegation/child-agent-executor.ts`
- `kun/src/instructions/instruction-runtime.ts`
- `kun/src/graph/project-agent-registry.ts`
- `kun/src/graph/graph-worker-security.ts`
- `kun/src/graph/graph-attempt-leases.ts`
- `kun/src/graph/graph-run-store.ts`
- `kun/src/services/runtime-event-recorder.ts`
- `kun/src/adapters/tool/mcp-facade-provider.ts`
- 对应 workspace agent、graph lease、run store、runtime event 和 MCP tests

#### 源码确认的行为

- Workspace Agent 从 `.kun/agents/*.md` 加载，使用 realpath containment、`O_NOFOLLOW`、最多 32 个文件和每文件 64 KiB 限制，并固定排序。
- Workspace profile 默认只读；工具与宿主 allowlist 求交；不能选择 provider／model／reasoning、加载 Skill 或再次调用委派／生成子 Agent。
- Child executor 在提供主存储时创建持久 side thread，否则退化为内存；每线程有 sequence。
- 模型、Skill、读写路径、Artifact 和工具能力均使用上界或交集；read-only 强制生效，blocked 集合取并集。
- 全局与 Workspace `AGENTS.md` 有大小限制、realpath containment、`O_NOFOLLOW` 和缓存；当前实现只加载全局加根目录，并非完整的目录层级指令继承。
- Project registry 以 canonical path、git common dir 和规范化 remote hash 形成项目身份；Profile 带版本、导入合并、evidence、scoring、routing、lifecycle、audit 和 learning candidate。
- Graph worker security 生成 sandbox root 和路径化工具／模型／Skill／读写／Artifact allowlist。
- Graph attempt lease 以不超过 TTL 三分之一的频率续租，续租失败即 abort；持久写入状态是权威，支持 accepted／released 语义。
- Graph run store 使用 append-only JSONL、expected sequence、幂等键、checksum、snapshot/replay 和损坏检测；大 payload 外置 Artifact 并记录 SHA-256。
- Runtime event 先持久化再发布，以 lifecycle generation 围栏；transient event 明确不持久化。
- MCP facade 只暴露 connected、usable、visible、trusted server，并对调用执行按需批准。

#### 对 Nanobot 的取舍

- **直接采用模式：** Workspace overlay 安全、单调权限衰减、项目稳定身份、版本化 Profile、journal/outbox/lease 语义。
- **兼容适配：** JSONL 只适合单机实验；Nanobot 生产 Event Ledger 仍需数据库事务和跨 worker 条件更新。
- **改进后采用：** Workspace Agent 配置错误目前会被静默丢弃；Nanobot 必须输出可诊断错误。
- **排除：** 复制整个代码 Agent 工作台或 Graph 系统；PolyForm Noncommercial 源码不得进入项目。

### 5.7 AutoDev

#### 已检查源码

- `mpp-core/src/commonMain/kotlin/cc/unitmesh/agent/tool/impl/AskAgentTool.kt`
- `mpp-core/src/commonMain/kotlin/cc/unitmesh/agent/core/SubAgent.kt`
- `mpp-core/src/commonMain/kotlin/cc/unitmesh/agent/core/SubAgentManager.kt`
- `mpp-core/src/commonMain/kotlin/cc/unitmesh/agent/context/AgentContextDiscovery.kt`
- `mpp-core/src/commonMain/kotlin/cc/unitmesh/devins/command/ClaudeSkillCommand.kt`
- `mpp-core/src/commonMain/kotlin/cc/unitmesh/agent/mcp/McpClientManager.kt`
- `mpp-idea/mpp-idea-core/src/main/kotlin/cc/unitmesh/devti/a2a/A2AClientConsumer.kt`
- `mpp-idea/mpp-idea-core/src/main/kotlin/cc/unitmesh/devti/a2a/AutodevToolAgentCard.kt`
- 对应 A2A 和工具测试

#### 源码确认的行为

- Agent-as-Tool 使用进程内 map 管理 SubAgent，每个 Agent 有独立 LLM Context，可返回状态摘要和问题；长内容阈值为 8000。
- `SubAgentManager` 的注释提到 persistence，但该类本身没有 durable store，也未在此路径看到明确的递归深度、总预算或安全能力门禁。
- Agent Context 从 git root 到 cwd 搜索配置，但每个位置只选择优先级最高的一种文件／目录；总长度限制约 32 KB。
- 长度计算使用字符数而不是实际字节，路径处理也未见类似 Kun 的 symlink／`O_NOFOLLOW` 加固。
- Skill 扫描项目根目录直接子目录和 `~/.claude/skills`，解析 frontmatter；没有资源身份、内容 hash、会话快照和 capability gate。
- MCP 顺序连接与发现 stdio／SSE server；动态工具初始 `enabled=false`，名称为 `server_tool`。
- MCP Adapter 把原始输入 Schema 折叠为单个 JSON string 参数，只返回第一段 content block，并把部分错误编码成 `ToolResult.Success`。这些行为会丢失 Schema、内容和失败语义，是明确反例。
- A2A 集成使用官方 Java client 和 AgentCard／JSON-RPC，但客户端以 agentName 作为响应 map key、固定 120 秒阻塞，并忽略部分 Task event；手写 ToolAgentCard 主要是 IDE 兼容层。

#### 对 Nanobot 的取舍

- **参考模式：** Agent-as-Tool 的最小组合体验和项目指令发现概念。
- **协议观察：** A2A 接口必须以后续官方 0.4 协议核验为准，不能从 AutoDev 的 IDE Adapter 反推服务端合同。
- **排除：** 进程内 map 冒充持久状态、无界递归、Schema 降格为 JSON 字符串、只取首段内容、错误包装为成功。
- **许可证边界：** MPL-2.0 是文件级 copyleft；本计划不复制实现。

### 5.8 Raven

#### 已检查源码

- `raven/context_engine/curator.py`
- `raven/context_engine/segments/curator.py`
- `raven/proactive_engine/sentinel/types.py`
- `raven/proactive_engine/sentinel/trigger_policy/policy.py`
- `raven/proactive_engine/sentinel/feedback/persistence.py`
- `raven/proactive_engine/sentinel/executor/pending_decision.py`
- `raven/proactive_engine/sentinel/executor/decision_router.py`
- `raven/memory_engine/skill_forge/fusion.py`
- `raven/memory_engine/skill_forge/gate.py`
- `raven/memory_engine/skill_forge/router.py`
- `raven/token_wise/usage_tracker.py`
- `raven/tracing/context.py`
- `raven/tracing/store.py`
- `raven/evolver/applier/path_guard.py`
- `raven/evolver/orchestrator/gates/pipeline.py`
- `raven/evolver/orchestrator/sealed/runner.py`
- `raven/evolver/orchestrator/state/journal.py`
- `raven/evolver/orchestrator/DESIGN.md`
- 对应 Curator、Sentinel、SkillForge、TokenWise、Tracing 和 Evolver tests

#### 源码确认的行为

- Curator 是只处理内部 Context 的有界 LLM loop，最多 12 步；它不能使用用户工具或直接回答用户。
- Curator 通过 manifest、archive、retrieve、relevance、memory、working state 和 build-context 工具整理上下文，保持精确固定前缀预算，并有 fast path、确定性 fallback 和 assembler validation。
- Curator 的部分 archive／manifest 是普通文件写入，未见统一原子写和锁；适合作为 Context policy，不是 durable ledger。
- Sentinel action 包括 skip、nudge、inject、defer、spawn，并记录 proactivity score、priority、topic 和带 TTL 的 pending decision。
- Trigger policy 集中执行额度、静默时段、session cooldown、内容／主题去重、dismissal 和自适应参数。
- Feedback persistence 使用文件锁、临时文件和 rename；Pending store 每个 channel／recipient 只保留一个 live decision，新决定显式 supersede 旧决定。
- Decision router 先走确定性 `/pick`、yes／no，再在置信度足够时调用 LLM；歧义输入回到普通聊天。
- SkillForge 对固定来源并发检索，每来源独立 overfetch 和失败隔离，使用加权 RRF、跨来源名称去重和来源遥测；LLM gate 再过滤相关性和可执行工具。
- Skill gate 基础设施失败时会 fallback 到 top N。这种 fail-open 可能注入无关或不可信内容，Nanobot 应改为权限过滤失败关闭、相关性可降级。
- TokenWise UsageTracker 记录 session／day／process 和 JSONL，但本身不执行预算；Tracing 提供 no-op／no-throw span 和 Artifact，存储同样是尽力可观察性，不是正确性账本。
- Evolver 冻结 evaluator、gate、ledger、sandbox 和配置 Schema 等不可修改路径；候选在临时 Git index／worktree 中产生，不先修改主工作树。
- 提升流程包含 screen、full confirm、paired／significance、beacon attribution、sealed test、baseline／ratchet 和可恢复 journal。
- `DESIGN.md` 明确记录部分 attribution 路径尚未接通并可能 fail-open，不能把所有门禁理解为全链路生效。

#### 对 Nanobot 的取舍

- **直接采用模式：** Curator 的有界 Context loop、Sentinel 的 pending／feedback／policy 状态机、SkillForge 的来源隔离与 RRF、Evolver 的 immutable kernel 和隔离候选。
- **兼容适配：** Trace／usage 作为 Event Ledger 的投影和预算输入，不作为事实源。
- **实验：** Curator 和主动触发必须先做离线回放与 shadow evaluation，再影响线上上下文或发消息。
- **排除：** 直接在线自修改、将 fail-open Skill gate 用于不可信来源、将普通 JSONL Trace 当作 durable event store。

### 5.9 Yoyo

#### 已检查源码

- `.github/workflows/evolve.yml`
- `scripts/evolve.sh`
- `scripts/extract_trajectory.py`
- `src/safety.rs`
- `src/session.rs`
- `src/commands_spawn.rs`
- `skills/analyze-trajectory/SKILL.md`

#### 源码确认的行为

- GitHub workflow 每 8 小时运行一次，使用并发队列、150 分钟超时和 contents／issues 写权限；失败后在 15／45 分钟重试。
- `evolve.sh` 先执行 Cargo build／test，再读取 trajectory、规划和实现；Prompt 明确要求 Agent 直接 commit，流程还会执行 `git add -A`、tag 并 push main。
- 失败恢复包含 reset／checkout 等破坏性 Git 操作。这与 Nanobot 的提交授权和工作区保护规则冲突。
- Trajectory extractor 汇总最近 10 个 session、14 天数据和 CI failures，并设置输入上限与 fail-soft 行为；这是可复用的“受限证据摘要”模式。
- Safety 模块用启发式规则识别未解析变量、超大命令、redirect、关键路径 rm／cp、chmod 和防火墙命令；它只能是附加 guard，不能替代 Sandbox。
- Session checkpoint／rewind 面向本地编辑会话；Context 使用率到 70% 时可通过退出码 2 请求 checkpoint。
- Spawn 支持最多 10 个并行后台任务、worktree 隔离和 branch／commit handoff，并显式承认 cwd 固定不是 Sandbox，绝对路径仍可逃逸。
- Worktree 创建失败会回退到当前目录，后台 tracker 主要是进程内状态；两者都不满足服务端强隔离和恢复要求。

#### 对 Nanobot 的取舍

- **直接采用模式：** 有上限的 trajectory／CI 证据提取、候选评测门禁和后台完成通知。
- **兼容适配：** 隔离候选必须使用服务端强制 Workspace／Sandbox，不允许失败后回退到共享目录。
- **排除：** 自动提交／tag／push 主干、`git add -A`、自动执行破坏性 reset、用正则命令检查替代 Sandbox。

### 5.10 Commonly

#### 已检查源码

- `packages/types/src/agent.ts`
- `backend/utils/agentManifestRegistry.ts`
- `backend/models/AgentProfile.ts`
- `backend/models/AgentMemory.ts`
- `backend/services/agentMemoryService.ts`
- `backend/models/AgentEvent.ts`
- `backend/services/agentEventService.ts`
- `backend/models/Task.ts`
- `backend/routes/tasksApi.ts`
- `backend/routes/agentsRuntime.ts`
- `backend/services/managedAgentsAdapter.ts`
- `cli/src/lib/session-store.js`
- `cli/src/lib/memory-bridge.js`
- 对应 manifest、memory visibility／provenance、event claim／ack、task idempotency 和 runtime token tests

#### 源码确认的行为

- Manifest registry 对 name、semver、capability、context scope、integration、model、runtime type、connection、memory、port、config schema 和 hook 做规范化与限制。
- Agent Profile 以 agentName、instanceId 和 pod 组织 purpose、instruction、persona、tool policy、context policy、integration、model preference、status 和统计。
- Agent Memory 的 canonical envelope 以 `(agentName, instanceId)` 唯一，包含 soul、long-term、daily、dedup state、relationship、shared、runtime metadata、system exchange 和 cycle。
- Memory 支持 private／pod／public 可见性、write provenance、最多 10 个历史版本、source runtime、sync dedup key、revision 和 last-seen revision。
- 服务端重新盖章 byte size、time、schema 和 provenance；同步 dedup 使用日期、source runtime、规范化内容的 SHA-256。
- private 内容不跨 Agent，pod 内容只在同 pod 分享，public 可分享；数组元素可单独标注可见性。
- 普通 memory patch 路径是读取、内存 merge、再更新；源码注释也承认未来应加 optimistic concurrency。当前并发写存在 lost update 风险。
- Agent Event 通过原子 claim 从 pending 变 delivered，再显式 ack；last-seen revision 使用 `$max`，并能重排超过 10 分钟的 stuck delivery。
- Event 记录会原地改变状态并最终 GC，因此是可靠投递队列，不是不可变 Event Ledger。
- Task 创建使用 `sourceRef` 幂等和冲突处理，claim 以原子 `pending → claimed` 选出赢家。
- Task 没有 lease、expiry、token 或 fencing；worker 崩溃后可能永久 stranded，不能称为完整 durable task。
- Runtime token 与 installation／pod 绑定，服务端从 token 派生身份并检查 pod membership。
- CLI session store 只保留每 Agent／pod 的 session ID 和最多 500 个 handled event ID，普通 `writeFileSync` 没有原子 rename／锁。
- `managedAgentsAdapter.ts` 明确仍是 scaffolding，API key 也是 placeholder；不能计为已经实现的 managed runtime。
- README 宣称每个 Agent 拥有独立“workstation”，但在固定版本源码中没有找到具体 Workstation model 或强隔离 Runtime 实现。

#### 对 Nanobot 的取舍

- **直接采用模式：** Runtime 无关身份、Memory envelope 的 visibility／provenance／revision、Event claim／ack checkpoint 和 `sourceRef` 幂等。
- **改进后采用：** Memory 更新必须加乐观并发；Task claim 必须补 lease、heartbeat、token、fencing 和接管。
- **排除：** 把可变并会 GC 的投递事件当作 Event Ledger；把 scaffolding 或 README 的 workstation 当作已完成能力。
- **品牌边界：** Apache-2.0 不包含 Commonly 名称、Logo 和品牌资产。

## 6. README／类型声明与源码事实的差异

| 项目 | 容易误读的声明 | 固定 commit 的源码事实 | 对 Nanobot 的约束 |
| --- | --- | --- | --- |
| LobeHub | Runtime state／stream 容易被当成完整运行账本 | 主要是状态、流和 step claim；未提供不可变终态事实合同 | Event Ledger 必须独立设计 |
| Orca | Run／fleet 容易被当成服务端自动调度 | Run 是 namespace／inbox，dispatch 主要由 CLI／Skill 驱动 | 只借鉴 receipt 和 ownership |
| Yao | Hook／delegate 容易被当成受控扩展 | V8 Hook 可改写宽 Runtime 参数，委派无明确最大深度 | Hook 和委派必须收窄权限 |
| LobsterAI | “产品与 Runtime 分离”容易被理解为多 Runtime 已落地 | `CoworkAgentEngine` 当前只有 OpenClaw | 先做合同测试，再宣称可替换 |
| OpenHanako | Skill snapshot 容易被当成内容不可变快照 | 只冻结启用列表和来源指针，内容仍从源读取 | Snapshot 必须区分 pointer 和 bytes |
| Kun | `AGENTS.md` 支持容易被理解为完整层级继承 | 当前主要加载全局与 Workspace 根文件 | 官方协议核验后再冻结查找规则 |
| AutoDev | SubAgent “persistence”注释容易被当成 durable | Manager 本身是进程内 map | 恢复必须基于独立持久状态 |
| Raven | Trace／TokenWise 容易被当成预算与账本 | 一个是尽力观测，一个主要记录 usage | 预算执行与事实存储另建合同 |
| Yoyo | Worktree 隔离容易被当成安全沙箱 | 源码明确 cwd 不是 sandbox，失败还能回退当前目录 | 隔离失败必须失败关闭 |
| Commonly | Workstation／durable task 容易被当成已实现 | 无具体 Workstation 实体；Task 无 lease／fence | 不采纳产品名，采纳可验证原语 |

## 7. 第二批能力取舍矩阵

| 能力 | 主要证据 | 决策 | 进入阶段 | 前置条件／退出方式 |
| --- | --- | --- | --- | --- |
| Agent Manifest 与可编辑 Profile | LobeHub、Yao、Commonly | 直接采用模式 | 阶段 3 | 先冻结 Runtime Port；Manifest 编译失败关闭 |
| 人格来源分层 | OpenHanako、Commonly | 兼容适配 | 阶段 3、9 | 区分系统身份、用户覆盖和公共默认 |
| Capability 单调衰减 | OpenHanako、Kun | 直接采用模式 | 阶段 8、10 | 父级上界、宿主 allowlist、显式拒绝原因 |
| Workspace Agent overlay | Kun、AutoDev | 改进后采用 | 阶段 8、9 | realpath、`O_NOFOLLOW`、大小限制、错误诊断 |
| Project／Agent 稳定身份 | Kun、Commonly | 直接采用模式 | 阶段 3、9 | 显示名不得作为唯一键 |
| 分层、可解释记忆 | LobeHub、OpenHanako、Commonly | 实验后采用 | 阶段 7、9 | provenance、visibility、revision、失效和并发控制 |
| Skill 引用／内容快照 | OpenHanako、Kun | 兼容适配 | 阶段 8 | pointer 与 bytes 分开，记录 hash 和 Artifact |
| MCP 动态发现 | Yao、Kun、AutoDev | 兼容适配 | 阶段 8 | 稳定排序、Schema 原样保留、预算、trust 和审批 |
| 上游 patch 管理 | LobsterAI | 临时采用 | KT 升级子计划 | patch 数量门槛、前提校验、删除期限 |
| 配置影响／交付 ack | LobsterAI | 直接采用模式 | 阶段 2、3 | base hash、冲突重试、热更新／重启分类 |
| Mutation receipt／未知结果 | Orca | 直接采用模式 | 阶段 2、5 | 幂等键、payload hash、人工核对路径 |
| Journal／Outbox／Lease | Kun | 兼容适配 | 阶段 2、5 | 数据库事务、sequence、checksum、fencing |
| Event claim／ack | Commonly | 直接采用模式 | 阶段 2、5 | 与不可变 Event Ledger 分层 |
| Durable task | Commonly | 排除当前实现 | 阶段 5 | 补齐 lease／expiry／token／fence 后重评 |
| 有界 Curator | Raven | Shadow 实验 | 阶段 7 | 离线回放、固定预算、确定性 fallback |
| 主动提案状态机 | Raven、OpenHanako | 兼容适配 | 阶段 10 | 去重、静默、额度、TTL、确认、反馈 |
| Skill 多源检索 | Raven | 实验 | 阶段 8 | 来源 allowlist、权限先过滤、失败遥测 |
| 离线受控进化 | Raven、Yoyo | 实验 | 阶段 12 | 冻结内核、隔离候选、held-out eval、人工提升 |
| 自动提交／推送主干 | Yoyo | 排除 | 不实施 | 与仓库提交授权冲突 |
| 本地路径 Artifact 协议 | LobsterAI | 排除 | 不实施 | 使用 owner-scoped Artifact Store |
| README workstation | Commonly | 观察 | 不进入合同 | 等待可验证模型和隔离实现 |

## 8. 对 Nanobot 后续实施的具体约束

### 8.1 Runtime、Run 与 Event

- `AgentRuntimePort` 只暴露 Nanobot 自有请求、事件、取消和能力合同，不导入 KT、OpenClaw 或其他框架类型；
- Run 持久化至少记录 owner、generation／lease token、attempt、terminal event、delivery receipt 和 ambiguous reason；
- 投递队列可以原地更新 claim／ack 状态，但不可变 Runtime Event 必须保留 sequence、actor、provenance 和 payload identity；
- Trace、usage、UI message 和可变 `AgentRun` 都是投影，允许从 Event Ledger 修复；
- 非幂等工具没有回执时，恢复停在 `ambiguous`，等待核对或人工选择。

### 8.2 Manifest、Skill、MCP 与 Hook

- Manifest compiler 负责把人格、模型策略、工具、Skill、MCP、Memory、Workspace 和权限编译成固定版本快照；
- 动态来源全部带 source、version／hash、owner 和加载诊断；
- MCP 保留原始 JSON Schema 与全部 content block，错误保持错误；工具名排序和 Schema snapshot identity 在会话内稳定；
- Hook 使用类型化输入输出、超时、capability、side-effect class 和失败策略，禁止任意脚本直接获得 Runtime 全权；
- Skill snapshot 明确记录引用快照和内容快照的差异，源漂移必须可观察。

### 8.3 身份、记忆与 Workspace

- Agent identity、Runtime identity、user／group／project owner 和 session identity 分开；
- Workspace、Memory、Artifact 和 Task 的授权由服务端从身份派生，客户端不得自报 owner；
- 记忆写入记录 source、evidence、visibility、confidence、revision 和 invalidation；并发更新使用版本条件；
- Workspace 配置发现使用 realpath containment、拒绝 symlink 逃逸、大小和数量限制，并返回可定位错误；
- 外部项目的桌面本地路径模型不能进入 Nanobot 服务端。

### 8.4 子 Agent、主动能力和进化

- 子 Agent 权限只能收紧，默认禁止递归委派和长期副作用；预算跨父子累计而非每层重置；
- 主动能力先生成 proposal，再执行去重、静默时段、额度、TTL、审批与反馈状态机；
- Context Curator 只决定 Prompt 输入，不拥有历史事实，也不能绕过 canonical Prompt Runtime；
- 自进化只产生隔离候选和评测证据，不能自动修改、提交或推送生产分支；
- Safety regex 只能补充 Sandbox，不能替代 `sandboxd`、Docker 参数强制和 owner 隔离。

### 8.5 KT 升级和解耦

- 升级前冻结当前行为，先用框架无关 fixture 建立契约测试；
- 上游版本固定到 tag／commit；任何临时 patch 必须有来源、前提校验、对应测试和删除条件；
- 升级后利用新公开 API 重写旧版兼容写法，删除私有字段访问、消息重排和工具执行妥协；
- KT 仅保留为可选 `KTAgentRuntimeAdapter`，不得继续由 API、research、scheduler 或业务核心直接导入；
- 若 patch 队列持续增长或 Adapter 需要侵入业务合同，应优先缩小 KT 能力面或切换 Native Runtime，而不是长期 fork。

## 9. 阶段 0.3 的完成边界

本文件只完成路线阶段 0.3 的 10 个高星项目源码核验。每个勾选项都有：

- 可复现的完整 commit；
- 仓库内许可证原文结论；
- 具体实现路径；
- 至少一项可采用模式和一项失败／排除边界；
- README 或类型声明可能造成的误读说明。

以下内容仍未完成，因此路线中的阶段 0.4、0.5 必须继续保持未勾选：

- OpenClaw、Bub／Tape、Agent Skills、`agents.md`、ACP 和 A2A 的官方协议或来源源码核验；
- EverOS、EverAlgo、HyperMem、EverMemBench 和 EvoAgentBench 的数据模型与评测方法；
- Jeju、waveloom、dscode、penguin-harness、agent-os-harness 和 seajelly 的真实实现；
- 覆盖第一批、第二批和来源项目的统一最终取舍矩阵；
- KT 当前固定版本、目标版本、下游改动和可删除兼容层的逐项差异表。

在这些来源完成固定版本核验前，不依据第二手项目的 README 描述冻结 ACP、A2A、Agent Skills、`agents.md`、Tape 或进化评测接口。
