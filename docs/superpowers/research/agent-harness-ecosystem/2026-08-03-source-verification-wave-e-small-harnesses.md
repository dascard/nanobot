# Agent Harness 生态源码核验：小型 Harness、上下文压缩与自进化闭环

> 状态：来源项目第三组核验完成
>
> 核验日期：2026-08-03
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md` 阶段 0.4 第三项
>
> 范围：Jeju、Waveloom、DSCode、Penguin Harness、Agent OS Harness、SEAJelly

## 1. 结论先行

本批六个项目都存在真实实现，但“功能存在”与“可以直接进入 Nanobot 生产架构”是两回事。源码核验后的结论如下：

1. **Jeju 最值得参考的是声明式 Agent Manifest、编译期校验、类型化 Trajectory、受限团队拓扑和候选包式进化。** 它的本地 sandbox 实际仍是宿主机 `bash -lc`，权限 gate 只按工具类别授权，不能阻止 shell 越出 workspace；Trajectory 追加也缺少跨进程锁、`fsync` 和完整性链。因此只采用合同与数据结构思路，不采用本地执行安全声明或现有日志落盘实现。
2. **Waveloom 最值得参考的是四级 Context Compaction 决策、持久化水位和分层工具输出清洗。** Bubblewrap 后端在 Linux 上有真实隔离参数，但默认允许 sandbox 不可用时继续无隔离执行；文件历史只覆盖内建编辑工具，shell 写入不在历史中，多文件恢复也不是事务。可参考压缩状态机与后端能力探测，不能把它的“Checkpoint”理解为完整工作区恢复。
3. **DSCode 最值得参考的是 fail-closed 的平台后端选择、先校验再应用 Patch、带冲突检测的 Checkpoint，以及深度和并发受限的 detached-worktree Subagent。** 但它的会话和终端主体来自外部 `@earendil-works/pi-*` 包；Docker 参数缺少 CPU、内存、磁盘和输出上限，Linux workspace-write 模式没有只读根文件系统，shell 修改也不进入 Checkpoint。只能拆取局部合同，不能整体替代 Nanobot Runtime 或 `sandboxd`。
4. **Penguin Harness 最值得参考的是清晰的 ReAct Context Engine、Trace 恢复时的结构修复、Subagent Session，以及“冻结 benchmark → snapshot → 修改 → 严格提升才保留”的优化协议。** SDK 的默认审批是拒绝，但 CLI 和 Server 默认 `allow-all`；shell 和文件工具没有执行沙箱。公开 benchmark 只有聚合常量，没有任务、逐题结果、Trace、rubric 和价格快照，不能作为可复现能力证明。
5. **Agent OS Harness 只适合参考最小证据记录字段，不适合参考安全模型。** 路径检查是词法 containment，符号链接能逃逸；命令 allowlist 允许 `node -e`、`git -C` 等任意参数，进程继承完整环境，没有超时、资源上限或网络控制。所谓 secret-safe evidence 仍会记录原始工具输出预览，不能作为生产证据账本。
6. **SEAJelly 有真实的 Agent Loop、Session CAS、事件重试、Provider Key 冷却、MCP、E2B 和 GitHub/Vercel 自进化链路，但闭环没有达到可安全采用的标准。** 批量事件领取是“先查后改”，两个 worker 可同时领取同一事件；MCP 和子应用工具能静默覆盖内建工具；写入授权只验证 owner 和 Agent allowlist，用户确认仍由 Prompt 自律；`revertCommit` 不是反向应用目标提交，而是把当前分支整棵树换成目标提交的父树，会丢弃目标提交之后的所有更改。
7. **三项优先转化方向是：Manifest 编译合同、可投影的类型化运行事件、可持久化且单调的 Context Compaction 决策。** Patch／Checkpoint、Trace Resume、Subagent 和离线优化协议作为第二层实验能力。
8. **必须明确排除：宿主 shell 伪 sandbox、提示词审批、默认 allow-all、未限资源的容器、无 CAS 的事件领取、静默工具覆盖，以及以替换整棵 Git Tree 冒充单提交 revert。**
9. 本批已逐项固定仓库 commit、许可证、关键源码路径并实际运行可执行的本地测试；**没有复制第三方代码，没有修改 README，也没有把外部 benchmark 数字写成 Nanobot 的能力声明。**

## 2. 核验方法和证据边界

### 2.1 方法

- 通过 GitHub API 固定仓库身份、默认分支、完整 commit SHA、核验日 star 快照和许可证文本；
- 在固定 commit 的本地浅克隆中读取核心实现、默认配置、测试、示例与结果产物；
- 对安全声明沿实际命令执行、路径解析、环境继承、容器参数和失败回退路径检查，而不是只读工具描述；
- 对恢复声明沿事件领取、日志追加、Checkpoint 写入、Resume 解析和多文件失败窗口检查；
- 对自进化声明检查训练／测试隔离、候选隔离、审批凭据、测试门禁、分支策略、回滚语义和可审计产物；
- 对 benchmark 声明检查任务集、运行清单、模型修订、Prompt、逐题结果、失败分母和价格快照是否提交；
- 使用各仓库声明的 Go、Node、Bun 和 pnpm 版本运行最小完整验证；需要联网取依赖的阶段与离线测试阶段分开记录；
- GitHub star 只是 2026-08-03 的筛选快照，不作为代码正确性或采用优先级的证明。

### 2.2 证据等级

| 等级 | 含义 | 本路线中的使用方式 |
| --- | --- | --- |
| A | 固定源码、测试和运行结果能互相印证，关键边界清楚 | 可转化为 Nanobot 自有合同和验收项 |
| B | 有真实实现，但可靠性、安全性或复现材料不完整 | 只采用局部模式，必须独立实现和补测试 |
| C | README、截图或聚合数字缺少可审计运行产物 | 只保留为研究线索，不能进入能力声明 |
| X | 实现与声明矛盾，或存在明确的正确性／安全性缺陷 | 排除直接采用；可作为反例测试来源 |

### 2.3 本次没有验证的内容

- 没有调用需要付费模型的 HotpotQA、代码 Agent 或 RAG 全量 benchmark；
- 没有把 E2B、GitHub、Vercel 或第三方 Provider 接入真实生产账号；
- 没有把 Bubblewrap、Seatbelt 或 Docker 配置声明等同于完成真实隔离验证；
- 没有复现 README 中缺少原始数据的成本、缓存命中率或准确率 headline；
- 没有审计 DSCode 外部 `pi-*` 依赖的完整实现，本批只判断 DSCode 自身仓库拥有的适配和扩展代码。

## 3. 固定版本、热度与许可证

| 来源 | 默认分支与固定版本 | 核验日 star | 版本线索 | 许可证 | 本批等级 |
| --- | --- | ---: | --- | --- | --- |
| [cosmtrek/jeju](https://github.com/cosmtrek/jeju/tree/afca47e5d4ee6bc4b2f672d7dfa6b876e070b583) | `master` / `afca47e5d4ee6bc4b2f672d7dfa6b876e070b583` | 26 | 精确 tag `v0.12.0`；Go 1.25 | MIT | B；sandbox 和日志可靠性为 X |
| [Menfre01/waveloom](https://github.com/Menfre01/waveloom/tree/293d5cd11a9c6935656cd8bf77ff15431fc64e73) | `main` / `293d5cd11a9c6935656cd8bf77ff15431fc64e73` | 113 | 无精确 tag；Go 1.25.8 | Apache-2.0 | A/B；性能 headline 为 C |
| [thinkany-ai/dscode](https://github.com/thinkany-ai/dscode/tree/4bf74fcb7ea72a45901d4f7517d86f8712a28479) | `dev` / `4bf74fcb7ea72a45901d4f7517d86f8712a28479` | 116 | 包版本 `0.3.4`；Node ≥22.19；pnpm 10.12.2 | MIT | B；完整 Runtime 所有权需外部依赖核验 |
| [Prism-Shadow/penguin-harness](https://github.com/Prism-Shadow/penguin-harness/tree/4d5d55d15aa59ca422a44470fce2afb65f2701eb) | `main` / `4d5d55d15aa59ca422a44470fce2afb65f2701eb` | 322 | 精确 tag `v0.2.0`；Node ≥24；pnpm 11.18 | Apache-2.0 | B；benchmark headline 为 C，执行安全为 X |
| [VIONWILLIAMS/agent-os-harness](https://github.com/VIONWILLIAMS/agent-os-harness/tree/d1d48d265b68bb01038fde33907f0fc6dec13de0) | `main` / `d1d48d265b68bb01038fde33907f0fc6dec13de0` | 1 | 包版本 `0.1.0`；Bun | MIT | B（字段）／X（安全声明） |
| [seajelly-dev/seajelly](https://github.com/seajelly-dev/seajelly/tree/b0dde270758d9bc38ac09c6d4f68ba46cbcaefc2) | `main` / `b0dde270758d9bc38ac09c6d4f68ba46cbcaefc2` | 5 | 包版本 `0.1.10` | MIT | B；事件领取、审批和 revert 为 X |

说明：

- star 数量来自 2026-08-03 GitHub API 快照。Waveloom、DSCode 和 Penguin Harness 已超过 100 stars；其余三个因原帖或来源链直接提及，即使热度较低也完成同样的源码核验。
- `seajelly` 的仓库身份已解析为 `seajelly-dev/seajelly`。本批六个命名目标均可访问，没有需要标记为“仓库不可访问”的条目。
- 表中的许可证只说明阅读和再实现边界；本路线仍优先独立实现合同，不复制第三方源码。

## 4. 跨项目取舍矩阵

| 能力 | Jeju | Waveloom | DSCode | Penguin | Agent OS | SEAJelly | Nanobot 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 声明式 Manifest | 完整 schema + compiler | 配置型 | 无统一 Manifest | Agent 配置 | 极简 CLI | 数据库配置 | 采用 Jeju 的编译思想，定义自有 schema |
| Agent Loop | 有 | 有 | 主体依赖 `pi-*` | 完整 Context Engine | 极简 | 完整 `generateText` Loop | 保持 Native/KT Adapter 双实现，不整体迁移 |
| 类型化事件／Trace | Trajectory + projector | Transcript/decision | 外部 Session 为主 | Trace + resume | 最小 evidence | step log + event/session | 采用统一 Run Event；日志与 Context 分离 |
| Context 压缩 | 无重点 | 四级单调决策 | 90% 一次压缩 | 阈值摘要 | 无 | 40 条触发滚动摘要 | 优先参考 Waveloom，吸收 DSCode/Penguin 反例 |
| Patch／Checkpoint | 候选 bundle | 内建编辑备份 | Patch + conflict-aware checkpoint | Snapshot/trace | 无 | Git Tree commit | 自行实现 Port；多文件事务和 shell 边界另测 |
| Sandbox | 宿主 shell | bwrap/Seatbelt，可降级 | Seatbelt/Docker，fail-closed | 无 | 词法路径 + allowlist | E2B | 继续独立 `sandboxd` + Docker Engine |
| 权限审批 | 类别 gate | session permission | access controller | SDK deny，CLI/Server allow-all | allowlist | owner + Prompt 确认 | 宿主持久化 Approval，不依赖 Prompt |
| Subagent | lead/worker 有界 | 有 | 深度 1、并发 4、worktree | 独立 session、深度 1 | 无 | 无同等级编排 | 先采用有界拓扑和预算，不开放递归 |
| 自进化 | 候选包 + train/selection | 无完整闭环 | 无 | Skill 协议 | 无 | 直接 GitHub/Vercel | 只允许离线候选、冻结评测和人工发布 |
| 可复现 benchmark | 示例材料不足 | 性能声明不足 | 无提交结果 | 仅聚合数据 | 无 | 无冻结评测 | 建立自有 Run Manifest 和逐项结果 |

## 5. Jeju 源码核验

### 5.1 已检查源码与文档

- `README.md`、`LICENSE`、`go.mod`
- `docs/agent-manifest.md`
- `docs/agent-evolution-manifest.md`
- `internal/config/schema.go`
- `internal/compiler/compiler.go`
- `internal/agentpkg/manifest.go`
- `internal/trajectory/event.go`
- `internal/trajectory/projector.go`
- `internal/trajectory/file_sink.go`
- `internal/trajectory/recorder.go`
- `internal/team/manifest.go`
- `internal/team/controller.go`
- `internal/evolve/evolve.go`
- `internal/evolve/pareto.go`
- `internal/policy/gate.go`
- `internal/sandbox/local.go`
- `examples/hotpotqa-agent/` 下的 manifest、dataset builder、evaluator 和实验配置

### 5.2 Manifest 和编译边界是真实实现

`internal/config/schema.go` 中的 Agent Manifest 不只是 README 示例，包含模型、指令、runtime、workspace、tools、skills、permissions、output 和 evaluate 等结构。`internal/compiler/compiler.go` 在进入运行时前解析并校验引用，再生成可运行配置。

可采用的不是字段逐字复制，而是以下编译管线：

1. 静态 Manifest 只表达能力需求和资源引用；
2. compiler 解析文件、模板、工具和模型引用；
3. 编译期拒绝缺失引用、非法组合和越权声明；
4. 运行时只接收已解析、带版本和 hash 的不可变 bundle；
5. Run Event 记录实际采用的 bundle ID，而不是只记原始 YAML 路径。

Nanobot 需要额外加入 owner、workspace、Prompt Runtime 版本、Runtime Adapter、模型能力、审批策略和 Sandbox Profile；不能照搬 Jeju 的 schema。

### 5.3 Trajectory 数据模型可参考，落盘实现不可直接采用

`internal/trajectory/event.go` 定义类型化事件，`projector.go` 把事件投影为 span、artifact、metric 和 integrity 信息。这证明“事件事实源 + 可重建视图”在小型 Go Harness 中是可行的。

但当前文件实现有明确边界：

- `file_sink.go` 使用 append 模式写 JSONL，没有跨进程锁、`fsync`、checksum 或 hash chain；
- 事件序号通过读完整文件后用行数加一推导，多个 writer 会生成重复序号；
- `recorder.go` 只有进程内 mutex；
- sink 写入默认可失败后继续，`FailOnSinkError` 不是强制默认；
- reader 遇到损坏行会失败，最后一行截断也没有专门恢复合同。

因此只能采用 Event schema 与 projector 思路。Nanobot 的 Run Ledger 必须使用数据库唯一键、事务、单调序号或服务端分配的 event ID，并明确“记录失败是否终止有副作用的运行”。

### 5.4 Team 是受限拓扑，不是无限递归

`internal/team/manifest.go` 只支持 lead-worker 拓扑，成员必须预先声明。默认限制包括：

- `maxRounds = 3`；
- `maxTasks = 10`；
- `maxParallel = 3`；
- `retries = 1`；
- 结果经过 verifier gate。

`internal/team/controller.go` 把可并行任务分批执行，并保留 child reference。这正是 Nanobot 应优先采用的方向：先支持单层、固定角色、有限任务数、有限并发和父子 Trace，再考虑更复杂 DAG。

不可直接采用之处是：权限、预算、workspace owner 和外部副作用合同仍需由 Nanobot 宿主定义，不能仅由 team manifest 自报。

### 5.5 进化使用候选包，但评测材料不足

`internal/evolve/evolve.go` 与 `pareto.go` 具有真实候选生成和选择流程：

- 要求 train 与 selection 数据；
- test 数据是可选项；
- 编辑面与 forbidden surface 分开声明；
- 候选先形成 bundle，不直接修改当前源码；
- 支持 hill-climb 和 instance-wise Pareto 选择；
- mini-batch 可以充当进入下一轮的 gate。

这比“模型直接改主干”更接近安全自进化。Nanobot 可采用候选不可变、训练／选择隔离、明确可编辑面和选择门禁，但还必须增加：

- 冻结 test set，进化器不可读取答案和 rubric；
- 记录数据版本、Prompt、模型、seed、候选父版本和每项结果；
- 发布必须人工批准，并通过独立部署通道；
- 禁止候选自行修改 evaluator、测试、权限和发布配置；
- 回滚指向已验证 release，而不是任意生成版本。

HotpotQA 示例 README 报告的提升只有摘要。仓库有固定数据 ID manifest 和程序化 F1 evaluator，但没有提交生成数据、原始逐题结果、完整 run manifest、winner bundle 或 Provider 修订；默认 trial 数也很小。因此 headline 只能记为 C 级线索。

### 5.6 Sandbox 和权限声明与实际安全边界不一致

`internal/sandbox/local.go` 最终在宿主机调用 `bash -lc`，工作目录设为 workspace，并继承完整 `os.Environ()`。所谓 safe path 只保护内建文件工具，无法约束 shell 命令。

`internal/policy/gate.go` 能把工具分为 command、network、write 等类别；read-only 模式会拒绝部分类别，workspace/full 模式则允许相应类别。但一旦 shell 被允许，命令仍能：

- 访问 workspace 外的宿主路径；
- 读取继承的环境变量和凭据；
- 使用宿主网络；
- 启动未受资源限制的子进程。

结论：Jeju local sandbox 是执行适配器，不是安全隔离。Nanobot 不采用该实现，继续坚持只有 `sandboxd` 访问 Docker Socket、默认断网、非 root、只读根、资源上限和 owner workspace。

### 5.7 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| Manifest + compiler | 直接采用模式 | 定义自有 schema、compiler 和 immutable bundle |
| Trajectory + projector | 直接采用模式 | 复用思想，不复用 JSONL sink |
| bounded lead-worker team | 实验后采用 | 先做深度 1、固定角色、预算和 verifier |
| candidate-bundle evolution | 实验 | 冻结评测和发布门禁后再启用 |
| local sandbox | 排除 | 由 `sandboxd` 执行 |
| HotpotQA 提升数字 | 观察 | 不进入 README 或验收基线 |

## 6. Waveloom 源码核验

### 6.1 已检查源码与文档

- `README.md`、`LICENSE`、`go.mod`
- `docs/prefix-cache.md`、`docs/prefix-cache.en.md`
- `experiments/tool_cache/README.md`、`experiments/tool_cache/main.go`
- `pkg/compaction/compaction.go`、`compactor.go`、`settings.go`、`types.go`
- `pkg/tool/sanitize.go`
- `pkg/sandbox/config.go`、`manager.go`、`linux_bwrap.go`、`darwin_seatbelt.go`
- `cmd/waveloom/sandbox_setup.go`
- `pkg/filehistory/track.go`、`backup.go`、`rewind.go`、`state.go`
- `pkg/session/transcript.go`、`session_persist.go`
- 对应 unit、integration 和 E2E test

### 6.2 四级 Context Compaction 是本批最完整的压缩状态机

`pkg/compaction/compaction.go` 根据已使用 Context 比例选择四级策略：

| 区间 | 行为 | 目标 |
| --- | --- | --- |
| 低于 60% | Tier 0，不处理 | 保持完整上下文 |
| 60%–80% | Tier 1，snip | 去掉低价值长输出 |
| 80%–95% | Tier 2，prune | 更积极裁剪旧工具结果 |
| 95% 及以上 | Tier 3，summarize | 生成摘要并推进水位 |
| 98% 附近 | hard threshold | 防止继续无界增长 |

真正值得采用的不是阈值本身，而是：

- compaction decision、summary、cursor 和 watermark 被持久化；
- 决策具有单调性，旧决策不会因为重建 Context 而随意反转；
- 消息重建保持稳定顺序；
- 测试覆盖 tier 选择、设置、summary 和 E2E 压缩。

Nanobot 后续 Context Engine 应记录“为什么移除、替换或摘要某段内容”，并能从档案和决策记录重建同一 Context。阈值必须通过本项目模型窗口、缓存命中与任务基准调参，不能照搬 60/80/95/98。

文档中指向 `specs/compaction.md` 的链接在固定 commit 不存在，应视为文档缺口，不影响源码事实。

### 6.3 工具输出清洗是分层防护，不是 Prompt Injection 防火墙

`pkg/tool/sanitize.go` 实现：

- Unicode NFKC 规范化；
- 控制字符移除；
- 边界和风险标记；
- 最大约 256 KiB 的截断；
- 对疑似 Prompt Injection 文本做模式扫描并生成警告。

扫描结果不会强制拒绝输出，模型仍然能看到被标记的内容。因此它适合成为 Nanobot `ToolResultEnvelope` 的预处理层，但不能替代：

- 工具来源和信任等级；
- 数据／指令通道分离；
- 高风险工具二次审批；
- 输出 schema 验证；
- 敏感字段脱敏；
- 资产化大结果和引用式 Context。

### 6.4 Sandbox 有真实后端，但默认降级策略不适合生产

`pkg/sandbox/linux_bwrap.go` 使用 Bubblewrap：

- 根文件系统只读绑定；
- workspace 可读写；
- `/tmp` 和缓存使用临时文件系统；
- 隐藏常见凭据路径和 Docker Socket；
- drop capabilities、`die-with-parent`、新 session；
- 默认通过 `unshare-net` 断网；
- 清理环境变量。

但 `pkg/sandbox/config.go` 的默认值包括 sandbox 未启用、`failIfUnavailable=false` 和 `allowUnsandboxed=true`。`cmd/waveloom/sandbox_setup.go` 在后端不可用且未要求 fail-closed 时只记录信息并继续执行。配置还可开启网络或增加读写路径。

更重要的是，`pkg/tool/shell.go` 在命令转换失败或某些执行错误路径上可回退到原始命令，排除列表中的命令也能绕过 sandbox 包装。

Nanobot 可参考“能力探测 + 明确后端状态 + 参数编译”模式，但生产环境必须 fail-closed，且实际执行统一走独立 `sandboxd`，不能把无后端继续运行作为默认。

### 6.5 文件历史不是完整工作区 Checkpoint

`pkg/filehistory` 在内建 write/edit/apply-hunk 前备份文件，使用内容 hash 组织版本，并支持恢复。这一设计对轻量 undo 有价值，但边界包括：

- 只有显式调用 `TrackEdit` 的内建工具被覆盖；
- shell、外部程序和未接入的工具修改不会被跟踪；
- 备份失败在调用链中可被忽略；
- 多文件恢复逐个执行，失败时可能已经恢复前面的文件；
- 测试明确覆盖 partial restore，而不是承诺事务；
- 落盘缺少 `fsync` 和跨进程事务。

因此 Nanobot 的 Checkpoint 必须明确区分：

- 工具级 undo；
- Git/worktree snapshot；
- workspace snapshot；
- Run state checkpoint；
- 外部副作用补偿。

不能用“保存过部分文件旧内容”笼统声称整个任务可回滚。

### 6.6 Transcript 和缓存宣传的证据边界

`pkg/session/transcript.go` 使用 append JSONL，没有跨进程锁和 `fsync`。读取时会跳过任意损坏行，这能提高可用性，却会静默形成不完整 Trace；overwrite 使用固定临时文件名再 rename，同样缺少并发和目录持久化合同。

前缀缓存文档声称的高命中率和大幅成本下降没有提交真实请求 Trace、价格快照和原始统计。`experiments/tool_cache` 是手工实验程序和预期输出，不是可复现 benchmark 结果。可采用的是“稳定前缀、动态尾部、稳定工具顺序”的设计假设，数字必须由 Nanobot 自己测量。

### 6.7 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| 四级 compaction decision | 直接采用模式 | 自建阈值、决策表、水位和投影 |
| 工具输出规范化／标记／截断 | 直接采用模式 | 进入 ToolResultEnvelope，不声称消除注入 |
| bwrap/Seatbelt 参数 | 兼容参考 | 只供 `sandboxd` 策略比较，不作为依赖 |
| file history | 实验 | 定义为 tool-level undo，不叫完整 checkpoint |
| JSONL transcript | 排除为事实源 | Run Ledger 使用事务存储 |
| 缓存命中与成本 headline | 观察 | 建立本项目实测后再结论 |

## 7. DSCode 源码核验

### 7.1 已检查源码与边界

- 根目录 `package.json`、workspace 和锁文件
- `packages/core/src/dscode-extension.ts`
- `packages/core/src/sandbox.ts`
- `packages/core/src/patch.ts`
- `packages/core/src/checkpoint.ts`
- `packages/core/src/subagents.ts`
- `packages/core/src/compaction.ts`
- `packages/core/src/managed-process.ts`
- `packages/core/src/hooks.ts`
- `test/sandbox.test.ts`、`patch.test.ts`、`checkpoint.test.ts`、`managed-process.test.ts`、`hooks.test.ts`

DSCode 自身是扩展层，不是完整独立 Harness。会话树、终端和主要 Agent Loop 依赖版本固定为 `0.83.x` 的 `@earendil-works/pi-agent-core`、`pi-ai` 和 `pi-coding-agent` 等外部包。本批能确认的是 DSCode 添加的 DeepSeek payload 优化、工具和安全适配，不能把外部包能力归为 DSCode 自有实现。

### 7.2 Sandbox 选择比“宿主执行后再检查路径”更可靠，但仍不满足本项目基线

`packages/core/src/sandbox.ts` 的平台路径是：

- macOS：Seatbelt profile，拒绝 workspace／临时目录外写入并可拒绝网络，但 profile 仍允许广泛宿主读取；
- Linux/Windows：要求配置 `DSCODE_SANDBOX_IMAGE` 并通过 Docker 执行；
- sandbox 后端不可用时失败，不默认回退宿主；
- Docker 默认 `network=none`，审批后才能放开；
- 设置 uid/gid、`cap-drop=ALL`、`no-new-privileges`、PID 512；
- workspace 以受控 mount 传入；
- 从传入环境中删除模型密钥。

缺少的生产边界同样明确：

- 未设置 CPU、内存、磁盘、tmpfs、单次输出和总执行时间上限；
- image 没有固定 digest；
- workspace-write 模式没有 `--read-only` 根文件系统；
- 没有独立服务隔离 Docker Socket 的证据；
- danger-full 模式仍是宿主执行；
- Docker 参数由 CLI 进程直接组装，不符合 Nanobot 只有 `sandboxd` 接触 Docker Engine 的边界。

因此只参考 fail-closed、平台能力探测、网络默认关闭和环境过滤，不能复用为生产执行层。

### 7.3 Patch 和 Checkpoint 提供了有价值的冲突语义

`patch.ts` 的优点：

- 先解析并校验全部路径与 hunks；
- 校验通过后才开始文件变更；
- 每个文件使用同目录临时文件和 rename；
- Context mismatch 会失败，而不是模糊套用到错误位置。

限制是多文件提交仍非文件系统事务：如果第二个文件 rename 失败，第一个可能已生效；没有目录 `fsync`，进程崩溃后的持久性也未定义。

`checkpoint.ts` 保存 before/after 内容及 hash，undo 前检查当前内容是否仍匹配 after hash，能阻止覆盖用户后续修改。这种 optimistic conflict protection 值得采用。

但当前 checkpoint 主要围绕 `apply_patch`；扩展会阻止部分直接写工具，shell 修改仍可发生且不被 checkpoint 捕获。`dscode-extension.ts` 把 Patch 描述为 atomic 时只对“先验证”和单文件替换成立，不能解释为多文件事务。

Nanobot 后续合同应使用“validated batch + per-file atomic replace + batch outcome ledger”，并在跨文件失败时进入 `ambiguous/partial` 状态，而不是声称全局 atomic。

### 7.4 Subagent 有界且隔离修改，但缺少回收

`subagents.ts` 允许最多 8 个任务、并发 4、最大深度 1。implementer 在 detached Git worktree 中执行并返回 diff，由主 Agent 决定集成；这优于多个 Agent 共享同一工作区直接写文件。

固定 commit 中没有对应的 worktree cleanup 路径。临时目录和 Git worktree metadata 会累积。Nanobot 若采用此模式，必须增加：

- lease 和 owner；
- deadline、预算、取消；
- worktree 创建记录；
- 成功、失败、超时后的幂等清理；
- 清理失败的回收任务；
- diff 审查和合并冲突状态；
- 父子 Run Event 关联。

### 7.5 Compaction 与可复现性边界

`compaction.ts` 在约 90% Context 使用率触发一次摘要，预留约 32k、保留约 60k，然后用摘要和保留消息替换上下文。它是真实实现，但不是 Waveloom 那种持久化、单调、多级决策；重建后难以解释每段内容为何消失。

仓库没有提交可审计 benchmark 数据。文档提出 shadow eval 等方法只能算计划信号。

本地验证还发现一个测试夹具错误：`test/managed-process.test.ts` 构造 shell 字符串时没有转义 `process.stdout.write('done')` 中的内层单引号，shell 实际执行成 `write(done)`，触发 `ReferenceError`。直接复现与测试栈一致，因此剩余失败不是 `ManagedProcessRegistry` 的退出码逻辑失败，但也说明固定 commit 的全量测试并非 0 failure。

### 7.6 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| fail-closed sandbox selection | 直接采用模式 | 放到 `sandboxd` 策略编译，不复用进程内 Docker 控制 |
| Patch validation + conflict hash | 直接采用模式 | 独立实现，显式标注 batch partial 状态 |
| detached-worktree subagent | 实验 | 先补 lease、回收和父子账本 |
| one-shot compaction | 观察 | 作为 Waveloom 多级策略的对照 |
| 外部 `pi-*` Session/TUI | 待单独核验 | 不归入本轮可采用实现 |
| “atomic patch”宣传 | 修正文案概念 | 只承诺校验原子性和单文件 replace |

## 8. Penguin Harness 源码核验

### 8.1 已检查源码与文档

- 根目录 `package.json`、`pnpm-workspace.yaml`、锁文件
- `packages/core/src/engine/context-engine.ts`
- `packages/core/src/agent.ts`
- `packages/core/src/trace/writer.ts`、`resume.ts`
- `packages/core/src/environment/tools/subagent/`
- `packages/cli/src/approval.ts`
- `packages/server/src/runtime/approvals.ts`
- `packages/server/src/services/snapshot-service.ts`
- `packages/skills/skills/benchmark-design/SKILL.md`
- `packages/skills/skills/agent-evaluation/SKILL.md`
- `packages/skills/skills/agent-optimization/SKILL.md`
- `packages/landing/src/lib/benchmark-data.ts` 及对应测试
- Trace、Resume、Compaction、Subagent、Approval、Snapshot 和 Benchmark tests

### 8.2 Context Engine 是真实的 ReAct 控制器

`context-engine.ts` 处理模型调用、工具调用、审批、事件、steering、interruption、reconnect、Context 修复、compaction 和 Trace。默认最大轮次为 100。

同一步多个工具的审批按顺序处理，获批后的工具执行可以重叠；事件按完成时间产生，但进入下一次模型调用的 tool result 会恢复原始 tool-call 顺序。这一细节值得转化成 Nanobot Runtime 合同：

- UI 事件顺序与模型消息顺序是两个不同投影；
- 每个结果必须绑定稳定 `tool_call_id`；
- 并行执行不能改变下一轮工具结果配对；
- 重连和取消必须能判断哪些副作用已经开始。

### 8.3 审批默认值在不同入口不一致

Core SDK 的 `RunOptions` 在缺少审批回调时默认拒绝，这是安全默认。`packages/cli/src/approval.ts` 和 Server 的默认配置则使用 `allow-all`。

这意味着同一 Agent 在嵌入 SDK、CLI 和 Server 运行时可能具有不同权限。Nanobot 不接受“入口决定隐式默认”：

- 所有 Runtime Adapter 必须接收同一份持久化 Permission Snapshot；
- 未配置审批策略一律拒绝高风险动作；
- CLI、HTTP、Scheduler 和 Subagent 不得各自补一个不同默认；
- 批准事件要记录 approver、scope、参数摘要、过期时间和决策依据。

### 8.4 Trace 和 Resume 有结构修复，但不是字节级重放

`trace/writer.ts` 使用 JSONL append，并明确按单 writer MVP 设计；没有跨进程锁、`fsync` 或 hash chain。Context Engine 中 Trace 写入采用尽力策略，日志失败不会自动阻止工具继续执行。

`trace/resume.ts` 的优点：

- 最后一行被截断时可以忽略；
- 中间行损坏会失败；
- 恢复时修复消息结构和 tool-call/tool-result 配对；
- 可从 Trace 构造继续执行的 Context。

但它做的是语义恢复，不是严格 replay：模型、时间、外部系统和工具副作用不会被重新确定化。Nanobot 应区分：

- Resume：从安全 checkpoint 继续；
- Replay：从记录重建投影；
- Re-execute：重新调用模型或工具；
- Audit：只查看原始事件和产物。

四个术语不能混用。

### 8.5 Compaction、Subagent 和 Snapshot

Penguin 默认 Context 上限为 128k，并受模型 Context 的 75% cap 限制；压缩会摘要旧内容、丢弃部分内容并构造新 Context。摘要被拒绝时会重试。它提供了实用实现，但缺少 Waveloom 式持久化 compaction decision，因此适合作为摘要器与消息修复参考。

Subagent 使用独立 session 和独立 Trace，最大深度为 1，可继承审批策略。这与 Nanobot 目标拓扑相近，但共享 workspace 时仍需 owner ACL、写入隔离和合并协议。

`SnapshotService` 把 Agent state 打包，排除 vault；导入时先保存当前快照，再用目录 rename 交换并支持失败回滚。它适合配置／状态迁移，不是正在运行进程、数据库事务和外部副作用的一致性快照；同样没有完整 `fsync` 合同。

### 8.6 自优化 Skill 是好协议，不是强制控制器

`benchmark-design`、`agent-evaluation` 和 `agent-optimization` 三个 Skill 共同描述：

1. 固定 case × run；
2. 把 rubric 与优化 Agent 隔离；
3. 修改前 snapshot；
4. 运行评测；
5. 只有严格分数提升才保留；
6. 失败或无提升时回滚。

这是本批最完整的“优化纪律”文本，可作为 Nanobot 离线实验流程输入。但执行仍由模型遵守 Skill，没有独立状态机保证 evaluator 不被改、rubric 不泄漏、分母固定或发布受控。

Nanobot 后续必须把协议落成服务端 Run Manifest、不可变 dataset、Evaluator Port、候选 bundle 和审批状态，而不是只加入 Prompt。

### 8.7 Benchmark 聚合值不可独立复现

`packages/landing/src/lib/benchmark-data.ts` 提供两组聚合常量：

- 15 项数据：Penguin 66.67%，Claude 53.33%，Codex 53.33%；
- 40 × 2 代码任务：Penguin 71.25%，Claude 86.25%，Codex 71.25%；
- 同时给出 token 和成本聚合。

对应测试只验证常量、比例和页面计算，没有提交任务正文、逐次 Trace、rubric、grader 输出、失败清单、模型修订和价格快照。因此这些数字只能评为 C。

README 中低成本 RAG 示例同样只有脚本、截图或文案，没有完整 Trace 与成本账本，不能用于 Nanobot 的采购或能力判断。

### 8.8 构建顺序是可复现性缺口

在干净 checkout 中直接运行根目录 `pnpm typecheck`，先因 `@prismshadow/penguin-skills` 缺少构建产物失败；只构建 Skills 后，又因 Server 找不到 Core 构建产物失败。先运行 `pnpm -r build` 后，typecheck 和全部测试通过。

这不是产品逻辑失败，但说明根脚本缺少 fresh-clone prerequisite。后续引用 Penguin 的测试结果必须记录“先构建全部 workspace”，不能只写 `pnpm typecheck`。

### 8.9 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| Context Engine 事件／消息双顺序 | 直接采用模式 | 纳入 Runtime Event 与 tool pairing 合同 |
| Trace resume 结构修复 | 兼容适配 | 区分 resume、replay、re-execute、audit |
| 深度 1 Subagent Session | 实验 | 叠加 workspace ACL、预算和合并协议 |
| benchmark/optimization Skill | 直接采用协议思想 | 变成服务端强制状态机 |
| CLI/Server allow-all | 排除 | 全入口统一 fail-closed |
| shell/file host execution | 排除 | 统一走 `sandboxd` |
| 公开 benchmark 数字 | 观察 | 不进入能力声明 |

## 9. Agent OS Harness 源码核验

### 9.1 已检查源码与文档

- `README.md`
- `docs/APPLICATION.md`
- `docs/OPEN_SOURCE_BOUNDARY.md`
- `package.json`
- `src/harness.ts`
- `src/deepseek.ts`
- `src/tools.ts`
- `src/permissions.ts`
- `src/evidence.ts`
- `src/types.ts`
- `test/deepseek.test.ts`、`harness.test.ts`、`permissions.test.ts`

### 9.2 极简 Loop 和 Evidence 字段有教学价值

`src/harness.ts` 是顺序工具循环：保留完整 messages、限制 max turns、调用 DeepSeek、执行工具并把结果送回模型。没有 session resume、checkpoint、compaction、MCP、并发或 eval。

`src/evidence.ts` 的 JSONL 记录包含 run ID、时间、Prompt hash、工具名、输出摘要、大小和结果。文件使用 mode 0600，避免默认世界可读。这些字段可作为最小审计清单参考。

但 Prompt hash 对低熵 Prompt 可以被字典枚举；工具输出仍保存前 160 字符的原始预览，内容里的令牌、路径或个人数据不会因字段名不是 `token` 而自动脱敏。证据文件同样没有跨进程锁、`fsync`、hash chain 和 append 确认。

### 9.3 路径 containment 可被符号链接绕过

`src/permissions.ts` 使用 `resolve(workspace, path)` 后比较前缀，能阻止显式 `../`，但直接 read/write 会跟随已存在的符号链接。workspace 内的链接如果指向外部路径，检查仍看到链接路径在 workspace 内，而真实 I/O 已越界。

list 工具会跳过符号链接，但这不能保护直接 read/write。生产路径控制必须使用：

- 受控容器 mount；
- `openat2`/等价 no-follow 语义或服务端文件代理；
- 每次路径分量的 symlink 防护；
- owner 和 workspace ACL；
- 只允许虚拟路径，不向模型暴露宿主路径。

### 9.4 命令 allowlist 不是执行沙箱

`src/tools.ts` 限制可执行文件名为 bun、git、ls、node、npm、pwd、rg 等，并使用非 shell spawn。但参数没有语义限制：

- `node -e` 和 `bun -e` 能执行任意代码；
- `git -C /...` 能访问 workspace 外仓库；
- `rg /...` 能读取宿主路径；
- 子进程继承 `process.env`，包括模型密钥；
- 没有网络、超时、CPU、内存、PID、输出或进程树限制。

因此 allowlist 只能减少误调用的二进制名称，不能建立文件、网络或资源隔离。

### 9.5 文档声明边界

`docs/OPEN_SOURCE_BOUNDARY.md` 相对诚实地说明这些测试不是生产安全证明；README 和应用表格中更强的 containment、secret-safe ledger 表述则超出了实现。

Agent OS Harness 对 Nanobot 的主要价值是形成安全反例测试：

- symlink escape；
- allowed interpreter arbitrary code；
- inherited secret environment；
- unbounded process tree；
- raw output secret leakage；
- concurrent JSONL writer collision。

### 9.6 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| 最小 evidence 字段 | 兼容参考 | 加入 owner、bundle、审批、artifact 和完整性字段 |
| 0600 日志文件 | 采用为本地最低线 | 生产仍使用服务端权限和数据库 |
| 词法 containment | 排除 | 用 mount + no-follow + ACL |
| 命令 allowlist | 仅作为一层策略 | 不能替代容器隔离 |
| secret-safe 声明 | 排除 | 建立值级脱敏与泄漏测试 |

## 10. SEAJelly 源码核验

### 10.1 已检查源码与文档

- `README.md`、`LICENSE`、`package.json`
- `src/lib/agent/execution.ts`、`limits.ts`、`loop.ts`
- `src/lib/events/queue.ts`
- `src/lib/memory/session.ts`
- `src/lib/agent/provider.ts`
- `src/lib/agent/runtime-context.ts`
- `src/lib/mcp/client.ts`
- `src/lib/agent/tooling/toolkits/self-evolution.ts`
- `src/lib/agent/tooling/tools/self-evolution.ts`
- `skills/self-evolution-guide/SKILL.md`
- `src/lib/github/patch-harness.ts`
- `src/lib/github/api.ts`
- `src/lib/e2b/sandbox.ts`
- `src/app/preview/[id]/page.tsx`
- `src/app/api/admin/coding/e2b/preview/route.ts`
- `supabase/migrations/001_initial_schema.sql`
- Session、Queue、Runtime Context、Execution 等 unit tests

### 10.2 Agent Loop 和 Step Log 是真实实现

`execution.ts` 使用 Vercel AI SDK `generateText`：

- 默认上限由 `limits.ts` 设为 40 steps、65,536 output tokens、275 秒 wall time；
- 使用 `AbortController` 控制总时长；
- `onStepFinish` 记录模型和工具步骤；
- 部署状态连续轮询有终态和重复状态 guard；
- 自进化只读工具连续 24 步或同一路径 4 次会中止；
- 工具输入、输出和模型文本按 64 KiB 截断；
- usage 逐 step 记录。

`agent_step_logs` 表保存 trace、event、agent、channel、session、step、工具输入输出、模型文本、状态和延迟，`expires_at` 默认写为 7 天后。固定仓库中没有找到按该字段删除数据的清理任务，因此这只是到期标记，不能直接视为已落实的 7 天保留策略。日志写入异常在 `onStepFinish` 外层被吞掉，不阻止 Agent 继续。

这适合参考“结构化 step observation + 有界 Loop + 非业务关键 telemetry”的组合，但不能充当不可丢的外部副作用账本。副作用执行前后的关键事件必须由 Durable Run Ledger 强制写入。

当前脱敏主要按字段名或字符串中是否出现 token/secret 等关键词判断；它不能可靠识别任意值级密钥和个人信息。完整工具 I/O 进入数据库前仍需 schema-aware redaction。

### 10.3 批量事件领取存在真实竞态

`src/lib/events/queue.ts` 具有 pending、processing、failed、dead、lock timeout、retry 和指数式 backoff，单看状态字段较完整。

问题位于 `claimPendingEvents`：

1. 先查询最多 5 个 pending、过期 processing 或可重试 failed 事件；
2. 再逐条按 ID update；
3. update 条件只要求状态仍属于 pending/processing/failed；
4. 没有比较查询时的 status、`locked_until`、version 或 claim token；
5. 两个 worker 可以查询到同一行，并先后都成功 update 和返回同一事件。

对于 zombie row，后一个 worker甚至可能覆盖新的 lock 和 retry_count。测试使用单个模拟 client，只验证筛选和状态变化，没有并发争抢测试。

Nanobot Event Ledger 必须使用单条原子 SQL、`FOR UPDATE SKIP LOCKED`、数据库 RPC 或带 version/lease token 的 CAS；renew、complete 和 fail 也必须校验同一个 claim token，避免旧 worker 完成新 lease。

### 10.4 Session CAS 值得采用，但它不是 Event Ledger

`src/lib/memory/session.ts` 使用 version CAS，begin/finalize/fail 最多重试 8 次，并记录：

- pending turn marker；
- 最近 20 个 completed event ID；
- 同 session 其他 pending event 的 busy 状态；
- user message timestamp；
- active skill IDs；
- rolling summary 元数据。

消息超过 40 条时，摘要旧消息并保留最近 12 条；摘要失败则保留旧摘要并退回最近 40 条原始消息。Prompt 注入中声明最近原始消息优先于摘要。

优点是 session 写入具备 optimistic concurrency 和有限幂等窗口。限制包括：

- completed event ID 只保留 20 个，不能代替永久幂等账本；
- summary 是模型生成内容，没有消息级证据引用；
- summary 作为 System Prompt 内容注入，仍需防止旧对话中的恶意指令被持久化；
- Session CAS 不能修复 Queue claim 竞态；
- 外部工具副作用不因 Session CAS 自动 exactly-once。

Nanobot 可采用 CAS marker 作为 Context 工作内存协调，但 Run/Event 事实必须独立保存。

### 10.5 Provider Key Pool 是可用模式，计数不是强一致

`src/lib/agent/provider.ts`：

- 只选择 active 且不在 cooldown 的 key；
- 按 weight 随机选择首选 key；
- 解密失败时尝试下一个；
- 429／quota 类错误冷却 5 分钟；
- overload／capacity 类错误冷却 3 分钟；
- 全部冷却时返回最近可用时间。

可参考的是“候选过滤 → 权重选择 → 失败分类 → key 级冷却”。限制是 `call_count` 使用读出的旧值加一并异步尽力 update，并发下会丢计数；冷却写入也吞掉数据库错误。Nanobot 当前已有模型候选排序和 `ModelFailureTracker`，不应再造一套冲突路由，只应考虑把 failure scope 从 model 扩展到 provider account/key，并用原子计数。

### 10.6 Skill 激活和工具合并存在越权边界

`runtime-context.ts` 的 Skill 自动激活使用名称和描述 token 的 substring 匹配；旧 session 在 active skill 为空时会一次激活全部 Skills。它适合轻量发现，不适合作为权限判断或安全工具启用依据。

MCP 合并存在两层静默覆盖：

- `mcp/client.ts` 对多个 server 的工具使用 `Object.assign`，后连接 server 的同名工具覆盖前者；
- `runtime-context.ts` 使用 `{ ...filteredBuiltin, ...mcpResult.tools }`，MCP 同名工具覆盖内建工具；
- 随后的 sub-app 工具又通过赋值写入同一个 map，可覆盖现有工具。

没有命名空间、冲突拒绝、来源签名或权限降级检查。攻击或误配置的远程 MCP 可以用内建高信任工具名替换实现，而 Prompt 仍看到原名字。

Nanobot 必须在 Tool Registry 编译期：

- 为每个工具记录 provider、namespace、版本和 trust level；
- 默认拒绝同名；
- 只有显式 alias 可解决冲突；
- 内建安全工具名不可被外部 provider 覆盖；
- Prompt 工具描述和执行 registry 必须来自同一 compiled snapshot。

### 10.7 E2B 执行和公开 Preview 不符合本项目技术边界

`src/lib/e2b/sandbox.ts` 每次通过 `Sandbox.create({ apiKey })` 创建环境，运行 Python 或 JavaScript，最后 kill。调用没有显式 template、网络、egress、资源、总时长、输出或依赖安装策略。README 和工具 Prompt 还把它描述为可联网的 secure cloud sandbox。

HTML Preview 写入 `html_previews`：

- ID 为随机 24 hex 字符；
- `expires_at` 可以为空，保存函数没有设置到期时间；
- 页面 route 不要求登录，使用 service client 按 ID 读取；
- iframe 使用 `sandbox="allow-scripts"`，脚本不能同源访问父页面，但仍可能发起网络请求；
- 预览 URL 属于 bearer-like public link，缺少 owner 授权、撤销和默认过期。

根据本项目既定边界，E2B 不进入运行时依赖。代码执行继续使用稳定 Docker Engine/runc 和独立 `sandboxd`，默认 `network=none`；发布预览应走 owner-bound immutable Artifact，并设置过期、撤销和内容安全策略。

### 10.8 自进化审批是 Prompt 纪律，不是可验证授权

SEAJelly 的自进化不是空 README：

- `self-evolution.ts` toolkit 用中英文关键词判断 GitHub 工作流意图；
- Policy 和 Skill 要求先搜索、提出 diff、等待确认、提交、监控 Vercel、必要时回退；
- 写工具校验 Agent 是否在可选 `GITHUB_PIPELINE_ALLOWLIST`；
- push、patch 和 revert 校验当前 channel 是 owner；
- 操作写入 `agent_step_logs`；
- `patch-harness.ts` 先读取并应用全部 V4A diff，再创建一个 Git tree、commit 和非 force ref update；
- GitHub ref 更新能在并发分支推进时失败，而不是 force 覆盖。

但工具 execute 收不到、也不校验：

- 用户批准事件 ID；
- 被批准的文件、diff、branch 和 commit message hash；
- 批准者身份与有效期；
- “提案”和真实提交内容是否一致；
- 独立测试结果或冻结 evaluator；
- 发布环境策略。

因此“收到明确确认”完全靠模型遵守 Prompt。owner 校验只能证明调用来源 channel，不证明该调用获得了本次变更授权。

Nanobot 的自进化必须使用服务端 Proposal 和 Approval 实体：审批绑定 immutable candidate bundle hash、目标环境、风险范围和有效期，执行器只接受可验证 token。

### 10.9 `revertCommit` 的语义会丢失后续更改

`src/lib/github/api.ts` 的 `revertCommit`：

1. 读取要回退提交的第一个 parent；
2. 取 parent 的整棵 tree；
3. 读取目标 branch 当前 HEAD；
4. 创建一个以当前 HEAD 为 parent、但 tree 完全等于旧 parent tree 的新 commit；
5. 非 force 更新 branch ref。

如果历史为 `A → B → C → D`，请求“revert B”时，正确结果应在 D 上反向应用 `A..B` 的差异，保留 C、D；当前实现却创建一个内容等于 A 的新 commit，C 和 D 的全部文件更改都会消失。

这与工具描述中的“undo specific commit while preserving history”不一致，是正确性 X 级问题。不能通过先 compare 提示用户来修复语义。

Nanobot 后续若提供回滚：

- 配置发布回滚应切换到已验证 release/snapshot；
- Git revert 应在隔离 worktree 中执行真正的三方反向 patch；
- 冲突必须进入待人工处理状态；
- 生成 diff、测试和审批后才允许更新 ref；
- 不提供“任意提交整树替换”为默认工具。

### 10.10 对 Nanobot 的取舍

| 项目能力 | 判断 | 转化方式 |
| --- | --- | --- |
| 有界 Loop + step observation | 直接采用模式 | 融入 Runtime Event，但关键事件 fail-closed |
| Session CAS + pending marker | 直接采用模式 | 用作工作内存协调，不代替 Event Ledger |
| Provider key cooldown | 兼容现有实现 | 扩展现有 tracker，不创建第二套路由 |
| MCP/Tool registry | 反例驱动修复 | 默认拒绝命名冲突，记录 provider/trust |
| E2B sandbox | 排除 | 使用 `sandboxd` + Docker Engine |
| GitHub patch one-commit tree | 观察 | 只用于离线候选概念，不直接推主干 |
| Prompt-only approval | 排除 | Proposal/Approval 必须服务端可验证 |
| 当前 revert | 排除并加入测试反例 | 真正 reverse patch 或 release rollback |

## 11. 实际验证结果

### 11.1 运行环境

- Go 使用官方 `go1.25.8.linux-amd64`，并核对官方 SHA256；
- DSCode 使用官方 Node `v22.19.0` 和仓库声明的 pnpm `10.12.2`；
- Penguin 使用官方 Node `v24.0.0` 和仓库声明的 pnpm `11.18.x`；
- Agent OS 使用 Bun `1.2.14`；
- SEAJelly 使用 Node 20 和 pnpm 10.17；仓库未声明 `engines` 或 `packageManager`；
- Node tarball 的 SHA256 与官方 SHASUMS 对照；
- 依赖预取允许使用当前网络代理，真正测试阶段按本项目本地测试约定清除 HTTP/HTTPS/all proxy；
- 未使用生产密钥、真实 Provider、GitHub、Vercel 或 E2B 账号。

### 11.2 命令与结果

| 项目 | 验证命令 | 结果 |
| --- | --- | --- |
| Jeju | Go 1.25.8：`go test ./...` | 退出码 0，全部 package 通过 |
| Waveloom | `go mod download` 后，清除代理运行 Go 1.25.8 `go test ./...` | 退出码 0；首次完全离线运行因尚未缓存依赖而在 setup 阶段超时，不是测试失败 |
| DSCode | Node 22.19 + pnpm 10.12.2：frozen install、`pnpm build`、`pnpm package:check`；测试使用 `DSCODE_SANDBOX_IMAGE=node:20.19.4-bookworm-slim pnpm test` | build 与 package check 通过；测试 166 passed、1 failed、4 skipped；唯一失败已定位为 `managed-process.test.ts` 的 shell 引号夹具错误 |
| Penguin | Node 24 + pnpm 11.18：frozen install、`pnpm -r build`、`pnpm typecheck`、`pnpm test` | 全部通过；1892 tests passed、5 skipped。干净 checkout 直接 typecheck 会因 workspace 构建产物缺失失败 |
| Agent OS | `bun run check` | TypeScript、5 tests 和 build 全部通过 |
| SEAJelly | frozen install 后 `pnpm test:unit`、`pnpm exec tsc --noEmit`、`pnpm lint` | 54/54 unit tests、typecheck、lint 全部通过 |

### 11.3 测试结果不能证明的事项

- Jeju 测试通过不能把宿主 `bash -lc` 变成安全沙箱；
- Waveloom sandbox unit test 不能替代真实宿主 Bubblewrap/Seatbelt 攻击验证；
- DSCode 单元测试不能证明 Docker Socket 隔离和完整资源配额；
- Penguin 的 1892 个通过测试不能补出缺失的 benchmark 原始数据，也不能修复 `allow-all` 默认；
- Agent OS 的 5 个测试只覆盖预期 happy path，没有 symlink、secret 和资源逃逸测试；
- SEAJelly 的 Queue test 没有两个 worker 并发领取同一行的测试，GitHub revert 也没有 `A→B→C→D` 保留 C/D 的语义测试。

## 12. README 声明与源码事实偏差

| 项目 | 声明或容易形成的理解 | 固定源码事实 | 处理 |
| --- | --- | --- | --- |
| Jeju | local sandbox 提供安全执行 | 宿主 `bash -lc` + 完整环境；safe path 不约束 shell | 排除安全实现 |
| Jeju | HotpotQA 结果代表可复现实验 | 缺原始逐题结果、完整 Run Manifest、winner 和 Provider 修订 | 数字记 C |
| Waveloom | sandbox 能稳定保护 shell | 默认可在后端不可用时 unsandboxed 继续 | Nanobot 必须 fail-closed |
| Waveloom | file history 等价完整 checkpoint | 只覆盖接入的内建编辑工具，多文件可 partial restore | 改称 tool-level undo |
| Waveloom | 缓存命中和成本下降已证明 | 只有说明和手工实验程序，缺真实 Trace | 自行 benchmark |
| DSCode | Patch 是 atomic | 全量先校验、单文件 replace；多文件 I/O 失败仍可 partial | 合同显式区分 |
| Penguin | 默认审批安全 | SDK deny，但 CLI/Server 默认 allow-all | 统一 fail-closed |
| Penguin | benchmark 数字可比较 | 只有聚合常量，缺任务、Trace、rubric、模型和价格快照 | 不用于能力声明 |
| Agent OS | workspace-contained、secret-safe | symlink/参数逃逸、继承环境、原始输出 preview | 作为反例 |
| SEAJelly | secure E2B sandbox | 调用侧没有显式网络、资源和镜像策略 | 不进入本项目运行时 |
| SEAJelly | review-first self-evolution | 用户确认只靠 Prompt，execute 无 approval proof | 引入服务端审批实体 |
| SEAJelly | revert 单个 commit | 当前实现替换为目标 parent 的整棵 tree | 排除当前实现 |

## 13. 对后续实现阶段的直接输入

### 13.1 第一优先级：进入阶段 1 的合同

1. **Agent Manifest Compiler**
   - runtime、model capability、Prompt bundle、tool、Skill、permission、workspace、sandbox、budget 和 output schema；
   - 所有引用编译为稳定 ID、版本和 hash；
   - 编译期拒绝工具重名、缺失变量、无实现 Runtime 和权限冲突；
   - 生成 immutable bundle，Run 只引用 bundle。
2. **Run Event 与 Projection**
   - event ID、run/turn/tool call、actor、owner、时间、seq、payload schema version；
   - append 与业务状态同事务；
   - Trace、UI、Context、usage 和 audit 都是投影；
   - telemetry 可 best-effort，副作用 ledger 不可 best-effort。
3. **Context Compaction Decision**
   - 记录输入范围、策略、原因、模型窗口、token 估算、被替换引用、summary artifact 和 watermark；
   - 决策单调并可重建；
   - 最近原文、工具结果资产和长期档案分层；
   - 通过真实缓存命中、质量、token 和成本 benchmark 调阈值。
4. **Tool Registry**
   - 工具名、namespace、provider、版本、trust、permission、sandbox profile 和 output schema；
   - 默认拒绝同名，外部 MCP 不能覆盖内建工具；
   - Prompt 描述与 executor 来自同一个 compiled snapshot。

### 13.2 第二优先级：进入后续实验

- validated Patch batch、单文件原子替换、hash conflict 和 partial outcome；
- 深度 1 的 bounded Subagent，独立 Run/Trace、workspace lease 和幂等回收；
- Resume 时结构修复，但明确不等于副作用 replay；
- Session CAS、pending marker 和 recent completion 只用于工作内存；
- Provider account/key 级 cooldown 与现有 ModelFailureTracker 合并；
- candidate bundle、冻结 dataset、private rubric、严格提升和人工发布；
- Artifact 化大工具输出、摘要和预览。

### 13.3 明确不进入实施清单

- Jeju 的宿主 local sandbox；
- Waveloom 的 unsandboxed fallback 和不完整 file history 作为全局 Checkpoint；
- DSCode 进程内直接控制 Docker Engine；
- Penguin CLI/Server 的 `allow-all` 默认和宿主 shell；
- Agent OS 的词法 containment、命令 allowlist 和 JSONL evidence 作为安全边界；
- SEAJelly 的 E2B 依赖、静默工具覆盖、Prompt-only approval 和当前 revert；
- 任何缺少原始产物的 benchmark headline；
- 任何自动修改、自动批准并直接更新生产主干的自进化流程。

## 14. 可访问性、命名与未验证标记

本批计划中明确列出的六个仓库均已访问、固定 commit 并完成源码核验：

- Jeju：`cosmtrek/jeju`；
- Waveloom：`Menfre01/waveloom`；
- DSCode：`thinkany-ai/dscode`；
- Penguin Harness：`Prism-Shadow/penguin-harness`；
- Agent OS Harness：`VIONWILLIAMS/agent-os-harness`；
- SEAJelly：`seajelly-dev/seajelly`。

因此本批没有“仓库已删除／无法访问”的条目。路线中“seajelly 等自进化项目”的“等”不自动扩大为未命名仓库全集；任何后续新增来源必须单独记录仓库身份、固定版本和证据，不能把本文件结论外推给同名或相似项目。

## 15. 最终取舍

这组六个项目没有改变 Nanobot 的目标技术边界，反而提供了更具体的正反证：

- **Manifest、Event、Context Decision 和 Tool Registry 应成为 Nanobot 自有稳定合同；**
- **KT、Native Runtime、MCP、Skill、Subagent 和 Sandbox 都只能作为这些合同后的可替换实现；**
- **日志存在不等于可恢复，Checkpoint 存在不等于全局事务，owner 存在不等于获得本次批准；**
- **能启动容器不等于完成隔离，命令 allowlist 不等于 sandbox，Prompt 纪律不等于授权；**
- **自进化的正确顺序是离线候选、冻结评测、独立验证、人工发布和已验证版本回滚。**

阶段 0.5 应基于 Wave A–E 统一形成“直接采用模式、兼容适配、实验、观察、排除”矩阵，并映射 Nanobot 已有实现，删除重复建设项。只有能定位到固定源码、测试或正式协议的模式，才允许进入后续实现。
