# Agent Harness 生态源码核验：第一批 Runtime 与运行可靠性

> 状态：第一批核验完成
>
> 元数据快照时间：2026-08-03 12:47 UTC
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md` 阶段 0.1、0.2
>
> 范围：18 个高星候选的元数据快照，以及其中 8 个 Runtime／运行可靠性项目的固定 commit 源码核验

## 1. 结论先行

第一批源码核验没有得出“选择一个外部框架整体替换 Nanobot”的结论。更可靠的路线是保留 Nanobot 已有业务语义和框架无关 Port，按能力拆分吸收下列模式：

1. 以不可变 Runtime Event 作为运行事实，以 `AgentRun`、UI 消息、Trace 和统计作为可重建投影；
2. 终态事实先持久化，再更新可变状态头；对不确定的工具副作用停放并核对，不盲目重放；
3. 长任务使用带 token 的租约、心跳、失权围栏和条件接管，不能只靠“后台 task 仍在运行”；
4. Context 压缩、工具结果裁剪和前缀缓存分别建模，不能把“缩短上下文”当成“缓存稳定”；
5. 工具 Schema、Skill、MCP、权限与子 Agent 都必须有固定顺序、预算、快照身份和失败关闭边界；
6. 多 Agent 只适合先引入受限 DAG、预算继承、批准和执行回执，不适合直接开放无限自治团队；
7. KT 升级后仍应继续解除硬依赖。外部项目提供的是 Nanobot 自有 Runtime 合同的设计证据，不是把 KT 换成另一个硬耦合框架的理由。

本批发现多个“README 名称强于源码语义”的情况：Qwen Code 的 workflow snapshot 是尽力保存而非正确性事实源；CodePilot 的两类 checkpoint 分别是进程内文件回退和运行前检查；GoClaw 的“8 阶段”是概念流程而非 8 个已注册 Stage；Open Multi-Agent 的 checkpoint 是任务粒度快照而非事件溯源。后续实现不得只按功能名推断可靠性。

## 2. 核验方法和证据等级

### 2.1 方法

- 先通过 X 原帖文章确认候选集合，再通过 GitHub REST 元数据确认仓库身份；
- 将第一批仓库克隆到临时目录，并记录完整 commit SHA；
- 同时阅读运行入口、存储接口、关键实现和对应测试，README 只用于定位，不作为最终证据；
- 对持久化、并发、恢复和权限结论，必须能定位到具体源码路径；
- 对没有跨进程锁、事务或 durable receipt 的实现，明确降级为“单进程”“尽力保存”或“仅 UI 投影”；
- 本文只总结实现模式，不复制第三方代码。带非商业或延迟开源条款的项目只作为架构观察来源。

### 2.2 证据等级

| 等级 | 含义 | 是否可进入实现设计 |
| --- | --- | --- |
| A | 固定 commit 的实现与测试相互印证 | 可以，但仍需按 Nanobot 约束重新设计 |
| B | 固定 commit 有实现，测试或故障边界不完整 | 只能作为兼容适配或实验输入 |
| C | 只有 README、文档或产品界面声明 | 不可以 |
| U | 仓库不可访问、改名或尚未源码核验 | 不可以 |

星数只用于确认用户要求的调研覆盖面，不是代码质量、许可证可用性或架构适配度的证据。

## 3. 18 个高星候选的元数据快照

下表来自 GitHub REST API 的同一轮快照。`许可证探测` 是 GitHub API 的自动识别字段；`NOASSERTION` 不等于“没有许可证”，只表示必须在后续源码核验中直接读取仓库许可证。星数、默认分支和更新时间均会变化，不参与架构判断。

| 仓库 | Stars 快照 | 默认分支 | 许可证探测 | 已归档 |
| --- | ---: | --- | --- | --- |
| `lobehub/lobehub` | 81,167 | `canary` | `NOASSERTION` | 否 |
| `bytedance/deer-flow` | 79,096 | `main` | `MIT` | 否 |
| `stablyai/orca` | 36,316 | `main` | `MIT` | 否 |
| `esengine/DeepSeek-Reasonix` | 29,601 | `main-v2` | `MIT` | 否 |
| `QwenLM/qwen-code` | 26,574 | `main` | `Apache-2.0` | 否 |
| `YaoApp/yao` | 7,555 | `main` | `NOASSERTION` | 否 |
| `open-multi-agent/open-multi-agent` | 6,706 | `main` | `MIT` | 否 |
| `op7418/CodePilot` | 6,332 | `main` | `NOASSERTION` | 否 |
| `ThinkInAIXYZ/deepchat` | 6,186 | `dev` | `Apache-2.0` | 否 |
| `netease-youdao/LobsterAI` | 5,755 | `main` | `MIT` | 否 |
| `liliMozi/openhanako` | 5,721 | `main` | `Apache-2.0` | 否 |
| `KunAgent/Kun` | 5,626 | `master` | `NOASSERTION` | 否 |
| `phodal/auto-dev` | 4,522 | `master` | `MPL-2.0` | 否 |
| `nextlevelbuilder/goclaw` | 3,501 | `dev` | `NOASSERTION` | 否 |
| `EverMind-AI/Raven` | 3,486 | `main` | `Apache-2.0` | 否 |
| `yologdev/yoyo-evolve` | 1,851 | `main` | `MIT` | 否 |
| `Team-Commonly/commonly` | 1,283 | `main` | `NOASSERTION` | 否 |
| `maka-agent/maka-agent` | 1,136 | `main` | `Apache-2.0` | 否 |

快照只证明这 18 个仓库在本轮调研时满足原帖相关性和 stars 不低于 1000 的筛选条件。第二批项目尚未完成固定 commit 源码核验，不能因为出现在表中就进入实现接口。

## 4. 第一批固定版本

| 项目 | 固定 commit | 源码许可证 | 本批证据等级 | 主要结论 |
| --- | --- | --- | --- | --- |
| [DeerFlow](https://github.com/bytedance/deer-flow/tree/bf2cb19ce75d553b730183807786cb7f5bb35a40) | `bf2cb19ce75d553b730183807786cb7f5bb35a40` | MIT | A | 借鉴租约围栏、终态回执顺序和 Context 压缩；不采用多进程 JSONL |
| [DeepSeek-Reasonix](https://github.com/esengine/DeepSeek-Reasonix/tree/f8ea772bfa55be321e3a7c3b3ae2045a51680f36) | `f8ea772bfa55be321e3a7c3b3ae2045a51680f36` | MIT | A | 借鉴稳定前缀、分水位 Context 维护、Schema 快照和会话写租约 |
| [Qwen Code](https://github.com/QwenLM/qwen-code/tree/6060a1f96ed5db592a4f72f8e8e821c0b3fa1505) | `6060a1f96ed5db592a4f72f8e8e821c0b3fa1505` | Apache-2.0 | A | 借鉴事件续传、稳定工具顺序、Skill 校验、MCP 预算和团队批准 |
| [Open Multi-Agent](https://github.com/open-multi-agent/open-multi-agent/tree/04ade0fcbbe8b1a82d74206f9ff606255b9f8b51) | `04ade0fcbbe8b1a82d74206f9ff606255b9f8b51` | MIT | A | 借鉴 DAG、计划修订、非扩张预算、执行回执和 Eval Gate |
| [CodePilot](https://github.com/op7418/CodePilot/tree/3add0bf31f8fa3633e56e2e54e9c4f5bea257394) | `3add0bf31f8fa3633e56e2e54e9c4f5bea257394` | BUSL-1.1，2029-03-16 转 Apache-2.0 | A | 借鉴 Provider Doctor 和权限批准合同；不得复制受限实现 |
| [DeepChat](https://github.com/ThinkInAIXYZ/deepchat/tree/7a4b1ed95f568270f332eff5eabac33d4277a306) | `7a4b1ed95f568270f332eff5eabac33d4277a306` | Apache-2.0 | A | 借鉴 Tape 事实／投影分离、幂等 provenance 和持久 pending input |
| [GoClaw](https://github.com/nextlevelbuilder/goclaw/tree/149bb478abfc9131e7d05f5e0baa6a1855ef01df) | `149bb478abfc9131e7d05f5e0baa6a1855ef01df` | CC BY-NC 4.0 | A | 只借鉴概念：可替换 Pipeline、Prompt Mode、记忆分层和 RBAC |
| [Maka Agent](https://github.com/maka-agent/maka-agent/tree/24c3fd410b61bcde7a42a980373e9d159b2baaa8) | `24c3fd410b61bcde7a42a980373e9d159b2baaa8` | Apache-2.0 | A | 第一批最强 Runtime Event／恢复参考，但只分阶段吸收合同 |

## 5. 分项目源码核验

### 5.1 DeerFlow

#### 已检查源码

- `backend/packages/harness/deerflow/runtime/events/store/base.py`
- `backend/packages/harness/deerflow/runtime/events/store/jsonl.py`
- `backend/packages/harness/deerflow/runtime/context_compaction.py`
- `backend/packages/harness/deerflow/config/subagents_config.py`
- `backend/packages/harness/deerflow/runtime/runs/manager.py`
- `backend/packages/harness/deerflow/runtime/runs/worker.py`
- `backend/tests/test_multi_worker_run_ownership.py`
- `backend/tests/test_gateway_run_recovery.py`
- `backend/tests/test_run_worker_delivery.py`
- `backend/tests/test_run_event_store.py` 及同目录事件存储测试
- `backend/tests/test_context_compaction.py` 及 Subagent 配置测试

#### 源码确认的行为

- `RunEventStore` 同时保存面向显示的消息和执行 trace；同一 thread 内按 sequence 形成递增顺序。
- `put_if_absent` 被用作终态投递回执的幂等持久化原语。
- JSONL 实现按 run 写入 `.deer-flow/threads/{thread_id}/runs/{run_id}.jsonl`；批量写入在锁内分配 sequence，再一次追加，阻塞文件操作通过 `asyncio.to_thread` 执行。
- JSONL 实现明确只适用于单进程。多 worker 正确性需要数据库后端，不能把文件锁语义外推为跨进程一致性。
- Context compaction 从 checkpoint 读取状态，生成摘要，保留选定消息，然后写入新 checkpoint；返回压缩前后计数、token 和 checkpoint 标识，失败不会伪装成成功。
- Subagent 默认总数上限为 6，可配置范围为 1–50；并发范围为 1–4；自定义 agent 默认禁止再次调用 `task`、`ask_clarification` 和 `present_files`。
- 多 worker Run 使用 `owner_worker_id`、`lease_expires_at` 和 heartbeat。失去所有权后，本地任务被中止／取消，旧 worker 不得再写持久终态。
- orphan reconcile 只通过条件更新接管过期或空租约；恢复路径仅在接管获胜后写零投递终态回执。
- Worker 的关键提交顺序是：刷新 journal → 幂等写 delivery receipt → 写 durable terminal status，从而缩小“已发送但终态未记账”的崩溃窗口。

#### 对 Nanobot 的取舍

- **直接采用模式：** 租约 token、heartbeat、失权围栏、条件接管、终态回执排序、事件 category 与 sequence。
- **兼容适配：** Context compaction 的 checkpoint 输入／输出合同，接入现有 Prompt Runtime 和 `ConversationTurn` 时重新定义字段。
- **排除：** 用 JSONL 承担生产多 worker 事件账本；把过期 run 一律标错当作任意任务的透明续跑。
- **需要保留的边界：** 对非幂等工具，接管只恢复到安全边界；没有 side-effect receipt 时必须进入 `ambiguous`，不能自动重放。

### 5.2 DeepSeek-Reasonix

#### 已检查源码

- `internal/agent/agent.go`
- `internal/agent/compact.go`
- `internal/agent/prune.go`
- `internal/agent/cache_shape.go`
- `internal/agent/session.go`
- `internal/agent/session_lease.go`
- `internal/agent/session_events.go`
- `internal/agent/save.go`
- `internal/provider/provider.go`
- `internal/provider/schema_canonicalize.go`
- `internal/tool/tool.go`
- `internal/plugin/lazy.go`
- `internal/plugin/cache.go`
- `internal/checkpoint/checkpoint.go`
- 对应 cache shape、prompt stability、prune/compact、session lease、checkpoint、Schema canonicalization 和 plugin cache 测试

#### 源码确认的行为

- Provider 传输副本会清除消息 `CreatedAt`，避免每轮变化的时间戳破坏前缀缓存；持久会话本身不因此丢失时间信息。
- Model message normalization 有健康前缀的快速路径，并修复 tool call／tool result 配对。
- Context 采用分段水位：50% 提示、60% 裁剪陈旧工具结果、80% 压缩、90% 强制压缩；压缩目标约为 50%，近期尾部预算为 16,384 tokens。
- 60%–80% 区间先归档并用占位信息替换大工具结果；达到 80% 后先机械裁剪，仍超限才摘要。固定 user turn 和已有摘要保持原文，活动 turn 不折叠，tool pair 保持成对；摘要失败时有机械折叠兜底。
- 工具原始结果进入归档，占位内容保留工具名、原大小、归档引用和重新执行提示。
- MCP lazy Schema cache 在会话开始命中后固定占位 Schema；会话中发现 live Schema 漂移时延迟到下一会话生效，从而保持同一会话工具前缀稳定。
- Schema cache identity 对非秘密身份字段和 header／env 的键名做哈希，不把秘密值写入缓存键。
- Session writer lease 同时使用进程内预留和操作系统文件锁，记录 writer identity。
- Session event log 支持 append／replace；发现损坏尾部时先保留损坏字节再截断；压缩采用临时文件、`fsync` 和 rename。
- 文件 checkpoint 按 user turn 保存编辑前快照并持久化 sidecar，可以跨进程重启恢复，但适用范围主要是文件编辑副作用。

#### 对 Nanobot 的取舍

- **直接采用模式：** 稳定前缀诊断、动态字段从传输副本剥离、工具／Schema 固定排序、Context 水位和 MCP Schema 快照身份。
- **兼容适配：** Session 写租约映射到 Nanobot 的 session／owner；工具归档映射到 Artifact，而不是把大结果塞回消息。
- **实验：** “会话内 Schema 固定、下一会话刷新”需要先增加 stale／refresh 可观察字段，确认动态 MCP 的用户体验。
- **排除：** 把文件编辑 checkpoint 泛化成所有工具副作用的恢复机制。

### 5.3 Qwen Code

#### 已检查源码

- `packages/core/src/tools/tool-registry.ts`
- `packages/core/src/services/session-writer-lease.ts`
- `packages/acp-bridge/src/eventBus.ts`
- `packages/acp-bridge/src/transcript-replay.ts`
- `packages/acp-bridge/src/permissionMediator.ts`
- `packages/core/src/agents/background-tasks.ts`
- `packages/core/src/agents/workflow-snapshot.ts`
- `packages/core/src/agents/runtime/workflow-journal.ts`
- `packages/core/src/agents/team/TeamManager.ts`
- `packages/core/src/skills/skill-manager.ts` 及 Skill 激活／加载／curator 实现
- `packages/core/src/tools/mcp-workspace-budget.ts`
- 上述模块的同目录测试

#### 源码确认的行为

- `getFunctionDeclarations()` 先过滤 deferred tools，再按 canonical Schema name 全局稳定排序；设计文档、实现和测试一致。
- Session writer lease 定义 conflict、lost、transcript-changed 和 unavailable 等错误，记录 owner、进程启动身份和封存 transcript proof，并区分 reclaim／takeover 策略。
- Daemon 的每会话 event bus 提供单调 event ID、epoch、`Last-Event-ID` ring replay、subscriber 条数／字节上限、慢消费者告警和驱逐，以及超出 replay 预算后的显式 `resync-required`。默认 ring 容量为 8,000。
- 后台 Subagent 区分同步与异步语义，并发默认值为 10 且可配置；终态会产生通知，审批可向上冒泡，已完成任务和近期活动均有界保留。
- Workflow JSONL journal 通过滚动哈希和规范化选项复用最长未变的确定性 agent-call 前缀。
- Workflow snapshot 只保存终态摘要并保留最近 30 条。源码明确说明它是尽力保存的便利功能，不是正确性或 durable recovery 的权威事实源。
- Agent Team 有真实的 teammate 数量限制、plan mode、leader approval、消息／inbox 上限、转义和协作关闭流程。
- Skill 管理实现了多作用域发现、校验、路径穿越／symlink 防护和激活；listener 使用 timeout 与 `allSettled` 隔离失败。
- MCP workspace budget 对 reserved server name、警告／强制阈值、滞回和拒绝合并有明确实现。
- ACP permission mediator 和对应测试存在，不只是 README 声明。

#### 对 Nanobot 的取舍

- **直接采用模式：** 稳定工具排序、带 resync 的有界事件续传、writer lease proof、Skill 路径安全、MCP 预算和团队批准。
- **兼容适配：** ACP permission mediator 只能映射到 Nanobot 的统一 `PermissionPort`，不得旁路现有 owner 和 ToolPlan。
- **实验：** Agent Team 必须从单层、固定 teammate 上限、不可递归和父预算收窄开始。
- **排除：** 把 daemon replay ring 当作持久账本；把 workflow snapshot 当作恢复正确性依据。

### 5.4 Open Multi-Agent

#### 已检查源码

- `packages/core/src/orchestrator/scheduler.ts`
- `packages/core/src/orchestrator/budget.ts`
- `packages/core/src/orchestrator/governance.ts`
- `packages/core/src/orchestrator/task-execution.ts`
- `packages/core/src/task/queue.ts`
- `packages/core/src/memory/checkpoint.ts`
- `packages/core/src/memory/file-store.ts`
- `packages/core/src/observability/records.ts`
- `packages/core/src/observability/file-store.ts`
- `packages/core/src/eval/runner.ts`
- `packages/core/src/eval/gate.ts`
- `docs/checkpoint.md`、`docs/adaptive-recovery.md`、`docs/context-management.md` 及相关测试

#### 源码确认的行为

- DAG 调度支持 round-robin、least-busy、capability-match、dependency-first 和 composite；除 round-robin cursor 外可保持确定性，能力要求不满足时失败关闭。
- 子预算通过 `min(parent, requested)` 只能收窄父预算；token 和 cost 累加，超限产生 budget event 并停止。
- Governance 检查实际 execution receipt 中的角色、顺序、依赖和 review，不通过输出文本猜测流程是否执行。
- Plan repair 采用 append-only revision；TaskQueue 保留修订历史，校验 added／superseded／retargeted task，并在批准后才应用修订和记录 trace event。
- Checkpoint 在任务边界保存 completed results、task queue 和 shared memory；FileStore 通过临时文件、`fsync`、rename 替换快照，恢复时跳过已完成任务。
- Checkpoint 是覆盖式任务快照，不是 event source；被中断的任务从头重跑。FileStore 没有跨进程锁，InMemory 后端重启即丢失；checkpoint 写失败还可配置为非致命。
- FileTraceStore 使用 append-only NDJSON，并能恢复到最后 committed boundary；trace 和 execution receipt 有实际结构。
- Eval runner、gate、store 和 scorer 均有实现，不只是路线图。

#### 对 Nanobot 的取舍

- **直接采用模式：** 明确 DAG、计划冻结／修订／批准、预算只能向下收窄、execution receipt 和 Eval Gate。
- **兼容适配：** Task checkpoint 只用于可重入的工作流步骤；非幂等步骤必须再接 side-effect receipt。
- **实验：** capability-match 和 composite scheduler 仅在单 Agent Runtime 合同稳定后进入。
- **排除：** 把覆盖式 task snapshot 当成事件账本；在多进程生产环境使用无锁 FileStore。

### 5.5 CodePilot

#### 已检查源码

- `src/lib/file-checkpoint.ts`
- `src/app/api/chat/rewind/route.ts`
- `src/lib/provider-doctor.ts`
- `src/lib/provider-dns-preflight.ts`
- `src/app/api/providers/test/route.ts`
- `src/lib/task-scheduler.ts`
- `src/lib/permission/profile.ts`
- `src/lib/permission-registry.ts`
- `src/lib/permission-approval-token.ts`
- `src/lib/runtime/permission-adapter.ts`
- `src/lib/run-checkpoint.ts`
- `docs/exec-plans/deferred/chat-run-checkpoint.md`
- 对应 file/run checkpoint、Provider DNS、permission 和 scheduler 测试

#### 源码确认的行为

- Native file checkpoint 在每个 user turn 首次修改文件前保存内容；回退目标 turn 时，可恢复目标及之后 turn 的最早快照，从而保留开始前已有的未提交改动。
- Checkpoint stack 只是进程内 `Map`，最多保留 20 个；重启后丢失。逐文件恢复失败会被捕获并继续。
- Rewind route 先删除数据库消息，再恢复文件，并可能返回部分恢复列表，因此不是数据库与文件之间的事务性 rewind。
- SDK conversation 存在时可走 SDK 自带的 `rewindFiles`，与 Native 路径语义不同。
- `RunCheckpoint` 是发送请求前展示 provider／runtime／context-cost 状态的信任提示，不是运行恢复 checkpoint；当前 context-cost 只告知、不阻断，权限强化仍在延后计划中。
- Provider Doctor 实现了 CLI、认证、provider、model 和 live probe 的结构化诊断，并能给出修复建议；DNS preflight 可快速失败，但在代理可能代解析时跳过／放行。
- Provider 测试 API 和 task scheduler 有真实实现。
- Permission 由 profile、pending registry 和 HMAC approval token 组成。Token 绑定 permission ID 与持久化过期时间，使用常量时间校验，数据库状态保证单次使用；canonical adapter 在能力缺失时返回 `permission_unavailable` 并失败关闭。

#### 对 Nanobot 的取舍

- **直接采用模式：** Provider 诊断分类、分层 preflight、批准 token 与持久状态双重校验、canonical permission adapter。
- **兼容适配：** File checkpoint 只可作为代码编辑类工具的辅助回退，权威事实仍需 durable event／artifact／receipt。
- **观察：** 桌面端管理体验和 Scheduler UI；不把 Electron／本地进程模型带进服务端。
- **排除：** 将 Native checkpoint 宣传成跨重启恢复；将 `RunCheckpoint` 与 Runtime checkpoint 混为一谈。
- **许可证边界：** 当前源码是 BUSL-1.1，存在商业使用限制，2029-03-16 才切换为 Apache-2.0；本项目只记录独立设计模式，不复制实现。

### 5.6 DeepChat

#### 已检查源码

- `src/main/tape/domain/entry.ts`
- `src/main/tape/domain/facts.ts`
- `src/main/tape/domain/effectiveView.ts`
- `src/main/tape/domain/viewManifest.ts`
- `src/main/tape/infrastructure/sqlite/tapeEntryStore.ts`
- `src/main/tape/application/sessionTape.ts`
- `src/main/tape/application/reconcilerService.ts`
- `src/main/session/runtimeEvents.ts`
- `src/main/agent/acp/runtime/acpSessionManager.ts`
- `src/main/agent/acp/runtime/acpSessionController.ts`
- `src/main/agent/acp/compatibility/adapters.ts`
- `src/main/agent/deepchat/runtime/interactionCoordinator.ts`
- `src/main/agent/deepchat/runtime/pendingInputPump.ts`
- Tape、ACP 和 pending input 相关测试

#### 源码确认的行为

- Tape entry 包含 event、anchor、message、tool_call 和 tool_result；session 内唯一 provenance key 提供幂等写入依据。
- SQLite 保存 append-only 物理事实，effective projection 选择最新 message／tool fact 并处理 retraction；读取范围受 session 和 high-water 约束。
- Tape 与 transcript 分离。普通变更通过追加事实表达；物理 delete／reset 只属于明确的 session 生命周期。Clear 在事务中创建新的 tape incarnation。
- `SessionTape` 将能力拆为较小 Port，并实现 reconciliation、fork、lineage、recall 和 view manifest。
- Pending input 明确区分 Queue 与 Steer。Steer claim 不可回退，在事务中把消息标记为已读并预留 assistant ID；进程重启后 pending Steer 会再次唤醒。
- `DurablePendingInputClaim` 对 disposition 加围栏。如果持久化后 publish 抛错，read-back 会识别已提交结果，避免第二次状态迁移。
- ACP adapter 把 prompt、tool、permission 和 terminal event 映射到同一 projection／Tape；ACP session 有实际持久化。
- `SessionRuntimeEvents` 是进程内 renderer 更新／缓存失效总线，不是事实源；SQLite transcript／Tape 才是权威状态。

#### 对 Nanobot 的取舍

- **直接采用模式：** append-only fact 与 projection 分离、provenance 幂等键、high-water 读取、提交不确定后的 read-back、持久 pending input admission。
- **兼容适配：** Tape incarnation 映射到 Nanobot 的 history clear／session generation，不能删除 `ChatLog` 档案。
- **实验：** ACP session 通过统一 Runtime Event 和 Permission Port 接入，先做客户端兼容，不改变业务事实源。
- **排除：** 把进程内 `SessionRuntimeEvents` 当 durable ledger；直接复制 Electron 单应用的并发假设。

### 5.7 GoClaw

#### 已检查源码

- `internal/pipeline/pipeline.go`
- `internal/pipeline/stage.go`
- `internal/agent/systemprompt.go`
- `internal/eventbus/domain_event_bus.go`
- `internal/eventbus/event_types.go`
- `internal/eventbus/bus_impl.go`
- `internal/permissions/policy.go`
- `internal/memory/auto_injector.go`
- `internal/consolidation/episodic_worker.go`
- `internal/consolidation/semantic_worker.go`
- `internal/consolidation/dreaming_worker.go`
- `internal/pipeline/stages_test.go` 及相关 Prompt／Memory 测试

#### 源码确认的行为

- `NewDefaultPipeline` 实际注册 7 个 Stage 对象：Context 一次；迭代内 Prune、Think、Tool、Observe、Checkpoint；Finalize 一次。
- 源码注释中的“标准 8 阶段”是 `context → history → prompt → think → act → observe → memory → summarize` 的概念语义；`MemoryFlush` 由 Prune 内联调用而未注册为独立 Stage。因此不能围绕“恰好 8 个 Stage”设计接口。
- Pipeline 的 `BreakLoop` 会跑完剩余阶段再退出；`AbortRun` 跳过当前循环剩余阶段；Finalize 使用 `context.WithoutCancel`，且收尾错误不覆盖主结果。
- Prompt Mode 有 full／task／minimal／none，解析优先级为 runtime override → 自动识别 → config → default；cron 最高为 task，heartbeat 最高为 minimal，subagent 最高为 task。
- Domain event bus 有类型、异步队列、worker、重试和 TTL 去重，并覆盖 session、episodic、entity、run、tool、context、delegation 等事件。
- RBAC policy 有实际实现。
- 三层记忆链路存在：AutoInjector 搜索 episodic L0 摘要；Episodic worker 用幂等 source ID 和 90 天 TTL 生成摘要并发布事件；Semantic worker 从摘要提取知识图谱；Dreaming worker 按 debounce／threshold 和 recall score 合成长记忆。

#### 对 Nanobot 的取舍

- **兼容适配：** 把 Pipeline 视为可替换职责集合，而不是照搬固定阶段数；Prompt Mode 进入 Manifest／Runtime policy。
- **实验：** episodic → semantic → dreaming 记忆链路必须用中文样本验证事实漂移、召回收益和成本后再启用。
- **观察：** typed Domain Event 可作为模块解耦信号，但默认内存总线不承担恢复正确性。
- **排除：** 把 Domain Event Bus 当作 durable ledger；把“8 阶段”当作源码事实。
- **许可证边界：** CC BY-NC 4.0 禁止商业用途；这里只提炼独立模式，不复制代码。

### 5.8 Maka Agent

#### 已检查源码

- `packages/core/src/canonical-runtime-event.ts`
- `packages/core/src/runtime-event.ts`
- `packages/core/src/runtime-event-store.ts`
- `packages/core/src/agent-run.ts`
- `packages/core/src/tool-recovery-fact.ts`
- `packages/core/src/tool-recovery-bundle.ts`
- `packages/core/src/session-revisions.ts`
- `packages/core/src/permission-profile.ts`
- `packages/storage/src/sqlite-runtime-store.ts`
- `packages/runtime/src/terminal-run-commit.ts`
- `packages/runtime/src/runtime-ledger-repair.ts`
- `packages/runtime/src/session-manager.ts`
- `packages/runtime/src/runtime-kernel.ts`
- core、storage、runtime、runtime-host、headless 的对应测试

#### 源码确认的行为

- `RuntimeEvent` 是唯一内部运行事实模型，与 UI `SessionEvent`、trace、telemetry 和 stored messages 明确分离；后者都是投影。
- Canonical encoder 校验严格、无损 JSON 并稳定序列化，拒绝 `undefined`、accessor、自定义 serialization 和稀疏数组等不确定结构。
- `RuntimeEventStore` 定义 durability mode、不可变读取、durable terminal barrier、原子 recovery bundle、continuation authority claim、不可变前缀 digest／high-water 和 workspace version authority。
- SQLite store 是 canonical durability 实现，使用 `BEGIN IMMEDIATE` 事务、Schema capability 检查、event sequence／identity 约束，并要求 terminal event 是不可变 ledger 的最后一条。
- `ensureTerminalRuntimeEventDurable` 校验终态事件逐字段一致，确保最多一个 terminal；不存在时才追加。
- Tool recovery fact 的核对结果分为：`matches_expected_state` 可按已有 outcome evidence 记为完成；`matches_prior_state`、`diverged`、`unreadable` 均停放，禁止盲目重放。
- Recovery bundle 事务性提交；terminal commit 先写 durable terminal fact，再更新可变 run header。Header 更新失败可以后续修复，不能倒置事实顺序。
- `AgentRun` header 保存 lineage、continuation source digest／high-water、permission／orchestration／owner 元数据及状态。
- Safe-boundary continuation 先 claim、校验 digest，再在执行前复核；不匹配、能力关闭或状态不安全时停放。
- Startup repair 做确定性的终止／ledger repair，文档明确不承诺任意位置 warm resume。
- Headless runtime、fixture 和 eval 有实际实现；支持语义 replay，但不声称 wire-level bit-exact replay。

#### 对 Nanobot 的取舍

- **直接采用模式：** Runtime Event 事实／投影分离、终态事实先于 header、tool recovery fact、ambiguous parking、immutable prefix digest 和 continuation authority。
- **兼容适配：** 先给现有聊天、定时任务和主动外呼统一 event envelope，再逐步替换各自状态表的重复语义。
- **实验：** safe-boundary continuation 先用于有确定 checkpoint 和幂等 receipt 的工作流，普通聊天不宣称任意点续跑。
- **排除：** 一次性复制其完整架构；把 semantic replay 宣传成底层 API 请求逐字节回放。

## 6. 跨项目取舍矩阵

| 能力 | 主要证据 | 决策 | Nanobot 落点 | 明确不做 |
| --- | --- | --- | --- | --- |
| Runtime Event Ledger | Maka、DeepChat、DeerFlow | 直接采用模式 | 新增框架无关 Event 合同与数据库 Store；`AgentRun` 变为投影头 | 用日志文本、WebSocket ring 或 JSONL 代替权威账本 |
| 终态提交与恢复 | Maka、DeerFlow | 直接采用模式 | durable terminal → delivery receipt／outbox → header projection；恢复前 read-back | 在失权后写终态；不核对副作用就自动重放 |
| Run 租约 | DeerFlow、Reasonix、Qwen Code | 直接采用模式 | 带 token 的 owner lease、heartbeat、fencing、conditional takeover | 只按时间判断所有权；跨进程依赖内存锁 |
| Context 水位 | Reasonix、DeerFlow | 兼容适配 | 独立 Context Engine；保留活动 turn、tool pair、摘要来源和 artifact 引用 | 无水位地截断；把原始 `ChatLog` 当上下文删除 |
| 稳定前缀 | Reasonix、Qwen Code | 直接采用模式 | 固定 system／tool 顺序，剥离动态字段，记录 cache shape 和漂移原因 | 仅为了 cache 命中重排语义消息 |
| MCP／Skill | Qwen Code、Reasonix | 兼容适配 | 多 scope discovery、安全校验、Schema snapshot identity、budget／hysteresis | 会话中静默更换工具 Schema；Skill 越权访问路径 |
| Provider Doctor | CodePilot | 直接采用模式 | DNS／传输／认证／模型／真实请求分层诊断 | 把所有失败归类为“网络问题” |
| Permission | Qwen Code、CodePilot、Maka | 直接采用模式 | `PermissionPort`、profile、单次批准凭据、持久状态、失败关闭 | Prompt 自律；协议 Adapter 绕过 ToolPlan／owner |
| DAG／计划修订 | Open Multi-Agent | 实验 | 冻结计划、append-only revision、批准、执行回执、预算收窄 | 无限递归、多 Agent 自批准、根据输出文本推断已审查 |
| Prompt Mode／Pipeline | GoClaw | 兼容适配 | Manifest 编译出 full／task／minimal／none 等 policy；Stage 按职责而非固定数量 | 照搬“8 阶段”名称或 Go 实现 |
| 分层记忆 | GoClaw、Reasonix | 实验 | 基于现有 Digest／Persona／RAG 做离线中文 eval | 未评测就启用 LLM 自动长期事实写入 |
| ACP 事件与权限 | Qwen Code、DeepChat | 实验 | 协议 Adapter 投影统一 Runtime Event／Permission | ACP 成为第二事实源 |
| 文件 rewind | CodePilot、Reasonix | 观察／局部适配 | 仅代码编辑工具的辅助机制，与 Artifact／receipt 组合 | 宣称可恢复任意工具副作用 |

## 7. 与 Nanobot 现有实现的映射

### 7.1 已有能力，不重复建设

| 已有实现 | 当前事实 | 本批调研后的处理 |
| --- | --- | --- |
| `core/agent_runtime/contracts.py` | 已有只依赖标准库的 `AgentRuntimePort`、请求身份、模型路由、conversation 和工具调用合同 | 在该边界增量加入 Run／Event／Capabilities，不另起第二套 Runtime API |
| `core/model_provider/chat_runtime.py` | 已有框架无关 `ChatCompletionPort` 和启动期注入 | Provider Doctor 与前缀诊断接到该 Port 外围，不让 Runtime 直接持有供应商 SDK |
| `core/tool_registration.py` | 已有冻结 `ToolRegistration`、Schema provider、execution binding、Prompt key 和生命周期事实源 | 增加 canonical Schema identity／稳定顺序／MCP snapshot，不重做工具注册表 |
| `core/db/models/chat.py` | `ChatLog` 是完整档案，`ConversationTurn` 是可清理工作记忆 | Event Ledger 不替代二者；Context Engine 只消费受治理投影，不删除档案事实 |
| `core/db/models/observability.py` | 已有 `AgentRun`、`ToolCall`、LLM 请求记录和无正文 `RuntimeTelemetryEvent` | 保留为观测／投影；完整 Runtime Event 需单独合同，不能把 telemetry 扩成含正文事实源而破坏现有边界 |
| `core/db/models/scheduling.py` | ScheduledTask 已有冻结 program、执行实例、owner lease、step attempt 和 `ambiguous` 状态 | 复用其成熟租约／恢复语义，抽取通用合同，而不是从外部项目重建任务系统 |
| `core/prompt_v2/` | 已有 canonical Prompt Runtime、模板注册与审计 | Context／Prompt Mode／工具输出引用改动必须同步模板与变量，不允许 Runtime 私下拼 Prompt |
| `clients/model_registry.py` 与路由模块 | 已有候选排序、失败追踪和熔断 | Provider Doctor 补诊断，不替换现有业务路由策略 |

### 7.2 已确认的缺口

- `AgentRuntimePort` 当前仍以 `execute_turn()` 和读写 conversation 为主，尚无统一的类型化 `run_stream()`／`run_event()` 事实合同。
- `AgentRun`、`ToolCall`、Telemetry 和各业务执行表存在，但还没有统一的 immutable Runtime Event Store、terminal barrier 和 projection repair 规则。
- 部分 API、research、scheduled workflow、工具 wrapper 和模型逻辑仍直接依赖 `nanobot_kt`；解除 KT 硬依赖要继续沿现有 Port 把业务核心移出 Adapter。
- 工具注册已经框架无关，但实际执行和 KT Schema 投影仍由 `nanobot_kt/tool_registration_adapter.py` 等兼容层承担；KT 升级后应利用新公开 API 缩小这些桥接层。
- 当前 Context、摘要、工具大结果、Artifact 和 Prompt cache shape 尚未形成单一可诊断策略。
- ACP、A2A、Agent Skills 和 `agents.md` 尚未完成官方协议核验，本批不能据此冻结接口。

## 8. README 声明与源码事实不一致或容易误读的点

| 项目 | 容易误读的说法 | 固定 commit 的源码事实 | 后续约束 |
| --- | --- | --- | --- |
| DeerFlow | JSONL Run Event 看起来可直接用于服务端持久化 | 实现明确是单进程方案 | 生产多 worker 必须数据库事务与条件更新 |
| Qwen Code | workflow snapshot 容易被理解为 durable checkpoint | 源码明确是 best-effort convenience，不是正确性依据 | 只用于体验优化，恢复仍依赖 ledger／receipt |
| Open Multi-Agent | checkpoint／adaptive recovery 容易被理解为任意点续跑 | 任务边界覆盖式快照；中断任务从头运行；FileStore 无跨进程锁 | 仅对可重入 task 使用，副作用另记回执 |
| CodePilot | file checkpoint／RunCheckpoint 容易被理解为统一 Runtime 恢复 | 前者主要是内存文件快照，后者是发送前状态提示 | 文档和接口必须分别命名 |
| DeepChat | runtime event bus 容易与 Tape 混同 | event bus 是 renderer 通知；SQLite Tape／transcript 才是事实源 | Adapter event 只能投影 ledger |
| GoClaw | “8-stage pipeline”容易被理解为 8 个注册组件 | 实际注册 7 个 Stage，MemoryFlush 内联于 Prune | Nanobot 按职责建 Stage，不冻结数字 |
| Maka Agent | replay 容易被理解为底层调用逐字节重放 | 保证语义 replay，不承诺 bit-exact wire replay | Eval 明确比较层级和容差 |

## 9. 对后续实施顺序的约束

第一批源码证据支持路线文件中的总体顺序，但进一步收紧为：

1. 先冻结现有 KT 行为和框架无关夹具，避免升级前后无法区分语义回归；
2. 在 `core/agent_runtime` 内先定义 Event envelope、终态、stream 和 capabilities，不直接引入外部框架类型；
3. 复用 ScheduledTask 已有 lease／step／ambiguous 语义，设计通用 Run ownership 和 recovery contract；
4. 设计 Event Store 时以 Maka 的“事实先于投影”和 DeepChat 的 provenance／high-water 为基线，以 DeerFlow 的 terminal receipt 顺序补足投递边界；
5. 完成 KT 上游差异核验和升级后，再清理当前为 KT 旧版保留的私有字段访问、消息重排和工具注册妥协；
6. Native Runtime 只在同一契约测试能运行于 Fake Runtime 与 KT Adapter 后开始；
7. Context Engine、Skill／MCP、ACP 和多 Agent 能力依赖统一 Event／Permission／Budget 合同，不提前各自造状态机；
8. 第二批 10 个高星项目和来源协议完成源码核验前，不把它们的 README 能力写入实现规格。

## 10. 尚未完成的范围

本文没有完成以下工作，路线文件中的对应项继续保持未勾选：

- 其余 10 个高星项目的固定 commit 源码核验；
- OpenClaw、Bub/Tape、Agent Skills、`agents.md`、ACP、A2A 等来源项目和官方协议核验；
- EverOS／EverAlgo／HyperMem／EverMemBench／EvoAgentBench 的数据和评测方法核验；
- Jeju、waveloom、dscode、penguin-harness、agent-os-harness、seajelly 的实现核验；
- 覆盖全部项目的最终取舍矩阵、许可证风险表和退出路径；
- KT 当前固定版本与上游目标版本的 API／行为／测试差异表。

这些项目在完成固定 commit 和最短源码证据前均视为“未完成核验”，不得作为接口冻结依据。
