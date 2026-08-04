# Agent Harness 生态源码核验：上游框架、指令格式与互操作协议

> 状态：来源项目与协议第一组核验完成
>
> 核验日期：2026-08-03
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md` 阶段 0.4 第一项
>
> 范围：OpenClaw、Bub/Tape、Agent Skills、AGENTS.md、Agent Client Protocol（ACP）和 Agent2Agent Protocol（A2A）

## 1. 结论先行

这组六项来源不能被合并成一个“新 Agent 框架”。它们分属不同层次，解决的问题也不同：

1. OpenClaw 证明 Runtime 可以在 Provider、Model、Channel 和业务会话之外形成独立执行层，但其公开 Harness 合同仍标记为实验性，而且明显携带当前内部 Runner 的大量类型，不适合原样移入 Nanobot；
2. Bub 的 Tape 很好地展示了“不可覆盖的事实记录”和“每次重建的 Context 视图”应当分离，但默认 JSONL Store 不具备跨进程锁、事务、校验和、崩溃提交边界或可靠恢复，不能直接作为生产 Event Ledger；
3. Agent Skills 定义的是 `SKILL.md` 包装、发现和渐进加载格式，不是权限协议。`allowed-tools` 仍为实验字段，宿主必须把它解释为能力请求，再与 owner、Workspace、工具和 Sandbox 策略求交；
4. AGENTS.md 当前稳定约定故意保持为普通 Markdown，没有必填字段、Schema、官方 Parser 或一致性测试。最近文件优先和用户指令优先可以作为发现约定，但不能把社区中的 v1.1 提案当成已发布规范；
5. ACP 当前稳定线是 wire protocol v1。它服务于代码编辑器／客户端与 Coding Agent 之间的双向控制，适合映射 session、stream、tool activity、permission 和 pending interaction；v2 在 2026-07-20 发布的仍是 Draft，必须同时经过版本协商和 feature flag，且不能放弃 v1；
6. A2A 当前最新发布版是 1.0.0，面向独立、通常不透明的远程 Agent 之间发现、消息和长任务交换。它没有提供 exactly-once、执行租约、generation fence 或内部 Event Ledger，不能替代 Nanobot 的本地 Runtime／Run 正确性合同；
7. ACP 的 session update、A2A 的 Task、Bub 的 Tape、OpenClaw 的 subagent registry 和 Nanobot 的 Event Ledger 虽然都记录“过程”，但语义、所有权、持久性和恢复保证不同，后续实现必须分层建模；
8. 本批没有复制任何第三方代码。所有结论都对应固定 commit 的官方仓库、协议正文、实现或测试。

## 2. 核验方法与证据等级

### 2.1 方法

- 只使用六个项目的官方仓库，分别固定完整 commit SHA；
- 先读许可证，再读协议／设计文档、核心数据结构、执行入口、失败路径和测试；
- 对“稳定协议”“append-only”“Sandbox”“多 Runtime”“恢复”“权限”等强声明，检查是否有源码或规范性措辞支持；
- 协议正文与辅助教程冲突时，优先采用明确标注的规范性来源；A2A 的数据对象以 `a2a.proto` 为唯一权威定义；
- 将当前稳定内容、草案、社区提案和实现便利行为分别标注，避免把未来方向冻结成 Nanobot 接口；
- 只提取设计模式和反例，不复制实现。

### 2.2 证据等级

| 等级 | 含义 | 后续用途 |
| --- | --- | --- |
| A | 固定 commit 的正式协议、核心实现和测试能够相互印证 | 可进入 Nanobot 设计，仍需独立实现 |
| B | 有真实实现或官方说明，但稳定性、可靠性或安全边界不完整 | 仅作兼容层、实验或反例 |
| C | 路线图、草案、提案或产品描述 | 不进入稳定合同 |
| U | 无法固定版本、无法访问或只有二手转述 | 不进入设计依据 |

## 3. 固定版本与许可证边界

| 来源 | 固定 commit | 许可证原文结论 | 本批证据等级 | 处理方式 |
| --- | --- | --- | --- | --- |
| [OpenClaw](https://github.com/openclaw/openclaw/tree/096c929ecf6ec2df9ec2b3a959799807b0442bdc) | `096c929ecf6ec2df9ec2b3a959799807b0442bdc` | 根目录 MIT，并另有第三方 notices | A/B | 研究 Runtime、策略和恢复边界；不复制大型实验合同 |
| [Bub](https://github.com/bubbuild/bub/tree/2933d08a4d92bcff18821332f96408a50a9ffbed) | `2933d08a4d92bcff18821332f96408a50a9ffbed` | Apache-2.0 | A/B | 采用 Tape／Context 概念，排除默认文件存储作为可靠账本 |
| [Agent Skills](https://github.com/agentskills/agentskills/tree/38a2ff82958afee88dadf4831509e6f7e9d8ef4e) | `38a2ff82958afee88dadf4831509e6f7e9d8ef4e` | 根目录和 `skills-ref` 为 Apache-2.0；`docs` 为 CC-BY-4.0 | A | 独立实现格式兼容；引用文档时保留署名边界 |
| [AGENTS.md](https://github.com/agentsmd/agents.md/tree/d1ac7f063d20e70015ed6732664049ae4ba9d74e) | `d1ac7f063d20e70015ed6732664049ae4ba9d74e` | MIT，版权声明为 OpenAI | A/B | 采用最小发现约定，不臆造不存在的 Schema |
| [ACP](https://github.com/agentclientprotocol/agent-client-protocol/tree/f2d6f889bf6ca68294547f30b335325735a9d99f) | `f2d6f889bf6ca68294547f30b335325735a9d99f` | Apache-2.0 | A（v1）／C（v2 Draft） | v1 做可选 Adapter；v2 只跟踪和试验 |
| [A2A](https://github.com/a2aproject/A2A/tree/2cdf197805cf3eb780714f730cdfd24bce1c9998) | `2cdf197805cf3eb780714f730cdfd24bce1c9998` | Apache-2.0 | A（v1.0） | 只作为远程 Agent 边界；内部可靠性由 Nanobot 自己保证 |

Agent Skills 的代码和文档采用不同许可证，不能把整个仓库笼统写成 Apache-2.0。AGENTS.md、ACP 和 A2A 的名称也不意味着相关品牌或商标自动随代码许可证授权。

## 4. 六项来源所在的正确层次

| 层次 | 来源／协议 | 负责什么 | 明确不负责什么 |
| --- | --- | --- | --- |
| 项目指令 | AGENTS.md | 按目录提供人类编写的项目说明 | 权限授予、沙箱、结构化配置 Schema |
| 可移植能力包 | Agent Skills | Skill 元数据、说明和按需资源组织 | 自动授权、脚本可信、Runtime 状态 |
| 本地 Agent Runtime | OpenClaw Harness、KT Adapter | 执行已经准备好的 Agent Turn | Nanobot 业务事实、owner 授权、长期数据所有权 |
| 事实与上下文模式 | Bub Tape | 追加事实与 Context 选择分离 | 默认实现下的事务账本和多 Worker 恢复 |
| 客户端控制协议 | ACP | IDE／客户端与 Coding Agent 的 session、stream、工具活动和批准交互 | Agent 间任务委派、Nanobot 内部事实源 |
| Agent 间协议 | A2A | 远程 Agent 发现、消息、Task、Artifact 和异步更新 | 内部执行租约、exactly-once、副作用恢复 |
| 工具协议 | MCP | Agent 与工具／资源连接 | Agent 间长任务协作、业务 Run 状态 |
| Nanobot 内部合同 | Runtime Port、Event Ledger、Permission Port | 运行事实、恢复、权限、投影和 Adapter 边界 | 对外协议的展示格式 |

因此后续可以同时支持 AGENTS.md、Agent Skills、MCP、ACP 和 A2A，但它们不能共用一个未经区分的 `session`、`event` 或 `permission` 数据结构。

## 5. 分来源源码与协议核验

### 5.1 OpenClaw

#### 已检查源码与文档

- `docs/concepts/agent-runtimes.md`
- `docs/concepts/agent-loop.md`
- `docs/concepts/multi-agent.md`
- `docs/gateway/sandbox-vs-tool-policy-vs-elevated.md`
- `docs/gateway/sandboxing.md`
- `docs/plugins/sdk-agent-harness.md`
- `src/agents/harness/types.ts`
- `src/agents/harness/registry.ts`
- `src/agents/harness/runtime-plugin.ts`
- `src/agents/runtime/index.ts`
- `src/agents/runtime-plugins.ts`
- `src/agents/runtime-capabilities.ts`
- `src/agents/agent-runtime-config.ts`
- `src/agents/conversation-tool-policy-pipeline.ts`
- `src/agents/agent-tools.before-tool-call.approval.ts`
- `src/config/agent-limits.ts`
- `src/agents/subagent-depth.ts`
- `src/agents/subagent-capabilities.ts`
- `src/agents/subagent-spawn.ts`
- `src/agents/subagent-registry.store.sqlite.ts`
- `src/agents/subagent-registry-restart-recovery.ts`
- `src/agents/subagent-run-liveness.ts`
- `src/agents/sandbox/validate-sandbox-security.ts`
- `src/agents/sandbox/docker.ts`
- `src/agents/sandbox/workspace-authority.ts`
- 对应 Harness、Runtime selection、Tool approval、Subagent persistence／recovery 和 Sandbox tests

#### 源码确认的行为

- OpenClaw 明确区分 Provider、Model、Agent Runtime、Harness、CLI backend 和 Channel。Harness 是执行已经准备好的 Turn 的低层实现，不是 Provider、Channel 或工具注册表。
- Core 在选择 Harness 前已经解析 Provider／Model、认证、thinking level、Context budget、transcript、Workspace、Sandbox、Tool policy、fallback 和 Channel delivery。Harness 不应再次选择模型、替换凭据或改写宿主策略。
- 当前同时存在内置 OpenClaw Runtime、Codex／Copilot 等插件 Harness，以及独立的 CLI backend／ACP 路径。这证明“框架可替换”需要稳定的选择、支持探测和能力协商，而不是在业务代码中判断具体框架名称。
- `AgentHarness` 合同覆盖 support probing、run attempt、结果分类、compaction、reset／dispose、side question、session fork、artifact validation、auth fingerprint、usage snapshot 和 MCP catalog 等大量能力。
- 官方 `sdk-agent-harness.md` 明确将该接口标为实验性，并说明参数类型有意镜像当前 embedded runner。它适合 OpenClaw 自己的受信插件，不适合直接成为 Nanobot 的稳定 Port。
- `runIsolatedCompletion` 要求新鲜、无复用线程、字面意义上的零工具调用面；如果 Harness 无法证明原生 Runtime 没有环境内置工具，就必须不声明该能力，由调用方失败关闭。
- Tool policy 按 profile、provider、全局、agent、group、sender、sandbox、subagent、runtime 和 inherited policy 等层组合。批准桥在表面不可用或超时时不能静默允许。
- 默认子 Agent 总并发为 8、单父级直接子节点为 5、spawn depth 为 1；默认 depth-1 子 Agent 是叶子，只有显式配置才允许继续嵌套。
- 子 Agent 会持久化 lineage、role、control scope 和继承后的 tool allow／deny envelope。解析时不能只相信 session key 的形状，避免伪造键获得主 Agent 能力。
- SQLite subagent registry 记录 generation、终态、投递和恢复信息；重启恢复使用 lifecycle generation、回执和有限尝试，能表达未知／待投递状态。
- 这套 registry 是子 Agent 生命周期专用状态，不等于全局 Event Ledger；`subagent-run-liveness` 对未结束记录按时间老化也不是 lease owner、heartbeat 和 fencing。
- OpenClaw 明确说明 Workspace 是默认 cwd，而不是 Sandbox。多 Agent 的 workspace、agentDir 和 session store 隔离，也不自动使插件的全局 Store 变成 tenant-scoped。
- Sandbox 路径已经包含只读根、network mode 检查、非 root、cap drop 和 `no-new-privileges` 等机制，但 OpenClaw Core 自己管理容器的实现不符合本项目“只有独立 sandboxd 可访问 Docker Socket”的边界。

#### 对 Nanobot 的取舍

- **直接采用模式：** Core 准备完整 Runtime attempt，Adapter 只执行；显式 capability negotiation；Runtime 选择失败关闭；权限分层求交；子 Agent lineage 和能力单调衰减。
- **兼容适配：** 只定义 Nanobot 需要的最小 `AgentRuntimePort`，通过 KT／未来 Runtime Adapter 转换，避免泄漏 OpenClaw 或 KT 类型。
- **实验：** session fork、native compaction、runtime artifact attestation 必须分别有能力位和合同测试，不能成为首版必选方法。
- **排除：** 原样复制庞大的实验 `AgentHarness`；让 Runtime 重新解析模型／权限；把 cwd 当 Sandbox；让主服务直连 Docker Socket；整体引入 OpenClaw Gateway 产品层。
- **对 KT 升级的启示：** KT 升级后应将现有“迁就 KT”的业务拼装移到 Core／Adapter 边界，而不是把 OpenClaw 的另一套大型 Harness 合同叠加到 KT 上。

### 5.2 Bub 与 Tape

#### 已检查源码与测试

- `src/bub/tape.py`
- `src/bub/builtin/tape.py`
- `src/bub/builtin/store.py`
- `src/bub/builtin/agent.py`
- `src/bub/framework.py`
- `src/bub/hooks/runtime.py`
- `src/bub/hooks/specs.py`
- `src/bub/hooks/interception.py`
- `src/bub/tools.py`
- `src/bub/turn.py`
- `tests/test_file_tape_store_entry_ids.py`
- `tests/test_fork_store_merge_back.py`
- `tests/test_agent_hooks.py`
- `website/src/content/docs/docs/concepts/tape-and-context.mdx`

#### 源码确认的行为

- `TapeEntry` 是 frozen dataclass，内置 `message`、`system`、`anchor`、`tool_call`、`tool_result`、`error` 和 `event` 类型，ID 由 Store 在写入时分配。
- `TapeStore` 合同只有 list、reset、fetch 和 append；Context 选择由 `TapeContext`、anchor 和 query 另行完成。事实存储和“这次给模型看什么”没有混为一体。
- Handoff 追加一个 anchor 和一个 `handoff` event，后续 Context 默认从最后 anchor 之后构建；状态可以随 anchor 保存。
- session tape 名称是 resolved workspace 与 session ID 的 MD5 截断组合。这里的哈希用于稳定命名，不是认证、抗碰撞安全或 owner 隔离证明。
- `ForkTapeStore` 先写内存，退出时逐条 merge 回父 Store；临时 fork 可以丢弃。这是有用的上下文分支模式，但不是原子事务提交。
- `FileTapeStore` 使用 JSONL 和单个 `TapeFile` 实例内的 `threading.Lock`。它没有跨进程锁、数据库事务、`fsync`、checksum 或 commit marker；两个实例／进程可以竞争 ID 和追加顺序。
- 读取时会静默跳过损坏 JSON 行或无法转换的 entry；这有利于尽力读取，却会掩盖生产账本损坏。
- reset 直接 unlink 当前 JSONL，只有调用方显式传入 `archive=True` 才先写备份。因此“append-only”只成立于一次 Tape 生命周期内，不等于永久不可变档案。
- `record_chat` 分别追加 system、消息、工具调用、工具结果、错误、assistant，再追加 `run` event。进程在中间崩溃时可能留下部分 Turn，且没有原子终止屏障。
- Context overflow 自动 handoff 最多重试一次；当前 handoff 记录错误原因，但没有自动生成足以重建旧上下文的结构化摘要或状态快照。
- Agent-loop interception Hook 支持 before LLM、before tool 的 proceed／replace／deny，以及 after observer。
- `AgentHooks` 捕获 before hook 异常并跳过失败实现。对于普通可观测扩展这是故障隔离；对于安全否决 Hook，这等价于失败后继续执行，不能作为 Nanobot 的授权边界。

#### 对 Nanobot 的取舍

- **直接采用模式：** append-only facts 与可重建 Context view 分离；anchor／handoff 作为有版本的 Context 边界；fork 只产生候选增量。
- **兼容适配：** TapeEntry 的开放 `kind` 应收敛为 Nanobot 版本化 Runtime Event；未知类型保留原 payload，但不能越过权限或状态机校验。
- **实验：** anchor 后摘要需要离线回放验证信息损失，不能只因 Context overflow 就丢弃旧事实。
- **排除：** 以 `FileTapeStore` 作为多 Worker Event Ledger；静默跳过损坏记录；把 fail-open Hook 当 Permission Port；把多次 append 冒充原子 Turn。

### 5.3 Agent Skills

#### 已检查规范、参考实现与测试

- `docs/specification.mdx`
- `docs/client-implementation/adding-skills-support.mdx`
- `skills-ref/src/skills_ref/parser.py`
- `skills-ref/src/skills_ref/validator.py`
- `skills-ref/src/skills_ref/prompt.py`
- `skills-ref/tests/`
- 根目录、`skills-ref` 和 `docs` 的独立许可证文件

#### 正式格式与客户端建议

- Skill 是一个目录，至少包含带 YAML frontmatter 的 `SKILL.md`；`scripts/`、`references/` 和 `assets/` 都是可选资源目录。
- `name` 和 `description` 必填。名称最长 64，描述最长 1024；可选字段包括 `license`、最长 500 的 `compatibility`、字符串 map `metadata` 和实验性的 `allowed-tools`。
- 名称必须与父目录一致、为小写字母／数字／连字符组合，不能以连字符开头或结尾，也不能包含连续连字符。
- 正文是自由 Markdown。推荐三层渐进披露：会话启动只加载 name／description；激活时读取完整说明；脚本、参考和资产按需读取。
- 官方建议 `SKILL.md` 小于 500 行、激活内容少于约 5000 tokens，文件引用保持一层深度；这些是推荐值，不是协议硬限制。
- 规范没有规定 Skill 必须安装在哪里。客户端指南将项目级和用户级 `.agents/skills/` 作为跨客户端约定，并建议项目级覆盖用户级、同级冲突确定性处理和明确告警。
- 客户端指南建议限制扫描深度／目录数，跳过 `.git`、`node_modules` 等目录，并在加载仓库自带 Skill 前执行项目 trust check。
- 云端或 Sandbox Agent 不应假定能看到用户主机目录；用户／组织 Skill 需要通过受控配置仓库、上传、注册表或内置资产供给。
- 指南有意建议宽松兼容：名称不匹配可告警后加载，缺少描述或 YAML 完全不可解析则跳过并记录诊断。

#### 规范与参考实现的偏差

- 规范要求文件名恰为 `SKILL.md`，`skills-ref` 的 `find_skill_md()` 却同时接受小写 `skill.md`。Nanobot 应将前者作为 canonical，后者只作为带诊断的兼容模式。
- 规范表格和示例将名称字符集描述为 `a-z`、`0-9` 与连字符；正文又出现“unicode lowercase alphanumeric”的模糊措辞。参考 validator 使用 Python `isalnum()`，实际接受更多 Unicode 字母／数字。
- 因此不能简单宣称 reference validator 与文字规范完全一致。Nanobot 首版应选择严格 ASCII canonical name，同时保留显示名称和可解释的兼容诊断。
- 参考 `to_prompt()` 在空列表时仍输出空 `<available_skills>`，而客户端指南建议没有 Skill 时完全省略 catalog／activation tool。这同样说明参考库是互操作辅助，不是所有客户端行为的规范来源。

#### 安全与权限边界

- `allowed-tools` 明确标为实验字段，并且语义是 Skill 声明的“预批准工具”字符串；它没有定义宿主身份、Workspace、参数约束、审批记录或 Sandbox。
- Nanobot 只能将它解析成 requested capability，再与宿主 allowlist、owner、Agent Manifest、会话策略和父 Agent 权限求交。
- Skill 目录内出现脚本不代表脚本可信。脚本执行仍必须经过 `sandbox_exec`、资源限制、网络策略和副作用审批。
- 目录发现必须固定 realpath、禁止越出已授权根目录、控制 symlink、文件大小、总文件数和读取预算。

#### 对 Nanobot 的取舍

- **直接采用模式：** canonical `SKILL.md` 元数据、渐进披露、项目／用户作用域、确定性冲突和诊断。
- **扩展字段：** 为每个 Skill 保存 source、owner、version／content hash、trust、compatibility result、capability request 和激活快照。
- **兼容适配：** 小写文件名和非严格名称只在明确兼容模式下接受，并显示告警；不能污染 canonical ID。
- **排除：** 通过关键词硬匹配自动触发；看到 `allowed-tools` 就授权；直接在主服务执行任意 Skill 脚本。

### 5.4 AGENTS.md

#### 已检查官方仓库内容

- `README.md`
- `components/FAQSection.tsx`
- `components/HowToUseSection.tsx`
- `components/AboutSection.tsx`
- 根目录 `LICENSE`

固定 commit 的官方仓库主要是说明网站，没有独立 Parser、JSON Schema、语法包或 conformance tests。

#### 官方约定确认的行为

- 文件名是 `AGENTS.md`，内容就是标准 Markdown；没有必填字段，标题和组织方式由项目自行决定。
- 推荐在仓库根目录放一份；大型 monorepo 可以在子项目继续放置嵌套文件。
- 官方 FAQ 的冲突规则是：离被编辑文件最近的 `AGENTS.md` 优先，用户在聊天中的显式指令覆盖文件指令。
- 常见内容包括项目概览、构建／测试命令、代码风格、安全注意事项、提交和 PR 约定，但这些只是建议主题，不是字段 Schema。
- 官方网站建议迁移旧文件时可以创建 symlink 兼容；这是本地开发便利，不表示服务端扫描器可以跟随任意 symlink。
- 项目目前由 Linux Foundation 下的 Agentic AI Foundation 托管。

#### 稳定约定与提案边界

- 固定 commit 中没有版本化的 v1.1 规范、frontmatter Schema、引用语法或精确定义的累积算法。
- GitHub [issue #135](https://github.com/agentsmd/agents.md/issues/135) 中的 jurisdiction、accumulation、frontmatter 和 progressive disclosure 属于提案讨论，不是当前稳定标准。
- 因而首版实现只能冻结官方已经说明的最小语义：沿目标路径向祖先查找、最近层优先、用户指令最高；更复杂的“累积还是替换”需要 Nanobot 自己明确并测试，不能冒充官方规范。

#### 对 Nanobot 的取舍

- **直接采用模式：** `AGENTS.md` 文件名、标准 Markdown、根与嵌套发现、目标文件最近层优先、显式用户指令最高。
- **需要自定义并公开：** 多层文件是否累积、同目录重复来源、最大字节数、编码错误、扫描根和诊断格式。
- **安全约束：** 只在授权 Workspace realpath 内查找；不跟随越界 symlink；内容只是 Prompt 指令，不授予工具、网络、文件或提交权限。
- **排除：** 把 v1.1 issue 当稳定规范；支持未定义的任意 `@include`；把构建命令自动视为批准执行。

### 5.5 Agent Client Protocol（ACP）

#### 已检查正式协议、Schema 与公告

- `README.md`
- `docs/protocol/v1/overview.mdx`
- `docs/protocol/v1/initialization.mdx`
- `docs/protocol/v1/authentication.mdx`
- `docs/protocol/v1/session-setup.mdx`
- `docs/protocol/v1/prompt-turn.mdx`
- `docs/protocol/v1/tool-calls.mdx`
- `docs/protocol/v1/file-system.mdx`
- `docs/protocol/v1/terminals.mdx`
- `docs/protocol/v1/cancellation.mdx`
- `docs/protocol/v1/session-list.mdx`
- `docs/protocol/v1/session-delete.mdx`
- `agent-client-protocol-schema/src/v1/`
- `schema/v1/schema.json`
- `docs/announcements/acp-v2-draft.mdx`
- `docs/protocol/v2/migration.mdx`
- `agent-client-protocol-schema/src/v2/`

#### v1 稳定合同

- README 明确区分 crate／Schema artifact 版本和 wire protocol 版本；当前稳定 wire protocol 是整数 `1`，兼容性必须看 `initialize.protocolVersion`，不能从包版本推断。
- ACP 使用双向 JSON-RPC 2.0。Client 与 Agent 都能发起方法并处理对方的 request／notification。
- 初始化必须协商 major protocol version 和 capability。省略的 capability 必须视为不支持，不能乐观调用。
- Agent 的最小方法包括 `initialize`、按需 `authenticate`、`session/new` 和 `session/prompt`；稳定 v1 还通过 capability 扩展 load、resume、list、delete、close、mode、config 和 logout。
- Client 的基础反向能力是 `session/request_permission`；文件读写、terminal、elicitation 等都必须先检查 capability。
- 所有协议文件路径要求绝对路径。这只是编辑器协议的表示规则，不等于该绝对路径已被 Nanobot owner／Workspace／Sandbox 授权。
- `session/prompt` 期间，Agent 通过 `session/update` 发送 plan、消息 chunk、tool call／update、usage 等事件，最终用 prompt response 的 `stopReason` 结束 Turn。
- v1 message ID 仍是可选；tool call ID 在会话内唯一。Adapter 不能假设每个文本 chunk 都有稳定事件 ID，必须自己建立去重和顺序投影策略。
- Tool 状态是 `pending`、`in_progress`、`completed`、`failed`。Permission options 是 `allow_once`、`allow_always`、`reject_once`、`reject_always`；它们是客户端交互提示，不直接替代 Nanobot 服务端授权。
- 取消 Turn 时，Client 必须把未决 permission 响应为 cancelled；Agent 应尽快终止模型和工具，并把原 prompt 正常结束为 `stopReason: cancelled`，不能只抛出底层 abort 异常。
- 通用 `$/cancel_request` 是可选能力，规范只要求支持方最终对原 request 返回结果或 `-32800`。嵌套活动级联是 MAY，不可当成强保证。
- `session/load` 恢复上下文并向 Client 重放完整会话；`session/resume` 恢复但明确不重放。这是展示／控制语义，实际持久化仍归 Agent 所有。
- `session/list` 是发现机制，`session/delete` 只规定从列表结果删除等外部语义；ACP 不会自动把 Nanobot 的 `ChatLog`、`ConversationTurn` 或 Event Ledger 变成它的事实源。

#### v2 Draft 边界

- 官方公告明确写明 v2 是 2026-07-20 发布的首个 Draft，内容在稳定前仍会变化。
- v2 将 prompt response 从“Turn 完成”改为“消息已接纳”，允许 session update 在 Turn 外继续；用稳定 ID 和 patch 统一更新消息、工具调用与 terminal output。
- v2 引入结构化文件变更、更加一般化的 permission subject 和更广的扩展枚举。
- 官方要求实现必须同时经过版本协商和 feature flag，生产环境不要默认启用，并继续并行支持 v1-only peer。
- 因此 Nanobot 当前不能以 v2 的 background update、required message ID 或 patch 语义重写内部稳定 Event 合同；这些只能先进入 Adapter 实验夹具。

#### 对 Nanobot 的取舍

- **直接采用模式：** v1 初始化／capability negotiation、session load 与 resume 区分、工具活动投影、取消和 pending permission 交互。
- **兼容适配：** ACP session ID 映射到 Nanobot 外部 binding；ACP update 映射为 Event Ledger 投影，不能反向成为业务事实源。
- **权限边界：** `session/request_permission` 必须调用 `PermissionPort`；客户端选择只有在 owner、scope、operation identity 和有效期一致时才生成服务端 receipt。
- **文件与 terminal：** 绝对路径先映射到容器虚拟路径并经过 Workspace scope；不允许把宿主真实路径、Docker Socket 或任意 terminal 权限直接暴露给 ACP Agent。
- **实验：** v2 Adapter 独立 feature flag、双版本合同夹具和降级测试；在 v2 稳定前不进入默认能力声明。
- **排除：** 把 ACP 当 A2A；把 update notification 当不可变事实；把 Client UI 的“always allow”直接升级成无范围的永久授权。

### 5.6 Agent2Agent Protocol（A2A）

#### 已检查规范、Proto 与专题文档

- `README.md`
- `docs/specification.md`
- `specification/a2a.proto`
- `docs/definitions.md`
- `docs/announcing-1.0.md`
- `docs/whats-new-v1.md`
- `docs/topics/key-concepts.md`
- `docs/topics/life-of-a-task.md`
- `docs/topics/streaming-and-async.md`
- `docs/topics/agent-discovery.md`
- `docs/topics/multi-tenancy.md`
- `docs/topics/enterprise-ready.md`
- `docs/topics/a2a-and-mcp.md`

#### 权威版本与数据模型

- 官方规范标注最新发布版本为 `1.0.0`；Proto package 为 `lf.a2a.v1`。
- 规范明确声明 `a2a.proto` 是所有数据对象和 request／response 的唯一权威定义，JSON Schema、SDK 和其他派生物必须从 Proto 生成，不能手工修改。
- 仓库中的源文件路径是 `specification/a2a.proto`；文档构建时以 `spec/a2a.proto` 的形式引用。后续引用应固定仓库源路径和 commit。
- v1 支持 JSON-RPC、gRPC 和 HTTP+JSON 三种官方 binding；AgentInterface 分别声明 URL、binding、可选 tenant 和 protocol version，客户端按有序列表选择首个支持项。
- AgentCard 描述名称、说明、Provider、Agent 版本、supported interfaces、capabilities、security schemes／requirements、输入输出媒体类型、Skills 和可选 JWS signatures。
- Agent Skill 在 A2A 中只是远程能力描述，包含 id、name、description、tags、examples、输入输出模式和安全要求；它不是 Agent Skills 的 `SKILL.md` 包，也不应复用同一数据表语义。
- Part 统一表示 text、raw bytes、URL 或任意 JSON data，并携带 metadata、filename 和 media type。远程 URL 和 raw data 都需要 Nanobot 额外的下载、大小、MIME 和 Artifact 安全策略。

#### Message、Task 与 Artifact 生命周期

- `SendMessage` 可以直接返回一个无状态 Message，也可以创建／更新有状态 Task；Task 是跨 Turn 长任务的核心单位。
- Message ID 由消息创建方生成；client message 可带 context ID、task ID 和 reference task IDs。context ID 用于关联多个可并行 Task，不代表 owner 或权限。
- Task 状态包括 unspecified、submitted、working、completed、failed、canceled、input_required、rejected 和 auth_required。
- completed、failed、canceled、rejected 是终态；input_required 与 auth_required 是中断态。终态 Task 不可重新启动，follow-up／refinement 应在相同 context 下创建新 Task，并引用旧 Task。
- Artifact 在 Task 内有唯一 ID、名称、说明、parts、metadata 和 extensions；流式 artifact update 用相同 ID 的 `append` 和 `last_chunk` 组合增量。
- `SendStreamingMessage` 的 Task 流先发送当前 Task，再发送 status／artifact updates；`SubscribeToTask` 也必须先返回当前 Task，避免 Get 与 Subscribe 之间丢窗口。
- Get／List Task 支持限制 history；unset 的实际返回量仍由服务端决定，0 表示不返回，正数是最多最近 N 条。协议不是无限历史保存承诺。
- List Task 必须按认证调用方做可见性过滤、游标分页，并按 status timestamp 降序；授权检查必须在可能泄漏资源存在性的查询前执行。
- 取消只是“尝试取消”，不保证一定成功；终态 Task、订阅和后续消息都有明确限制。

#### 幂等、恢复和可靠性边界

- Get 类操作天然幂等；Cancel Task 和删除 push config 被定义为幂等。
- `SendMessage` 仅为 **MAY be idempotent**，服务端可以利用 message ID 检测重复。协议没有 exactly-once 保证，也没有统一 idempotency key／payload hash receipt。
- Task 数据模型没有 attempt、lease owner、heartbeat、lease expiry、generation、fencing token 或 ambiguous outcome。
- A2A 定义的是跨系统可见状态，不定义远端 Agent 内部如何调度、持久化、接管崩溃 Worker 或处理非幂等副作用。
- push notification 允许重试并可能重复，官方明确要求接收方幂等处理。通知只应作为“状态可能变化”的信号，接收后通过 Get Task 获取权威当前状态。
- 因此 A2A Task 可以映射 Nanobot Run 的外部视图，但不能直接充当内部 Run、Event Ledger、Outbox 或恢复控制面。

#### 安全与多租户边界

- 生产 HTTP binding 必须使用 HTTPS，gRPC 必须使用 TLS；客户端应验证服务端证书。
- 身份由 transport／HTTP 层建立，协议 payload 不直接承载用户身份。AgentCard 宣告 API key、HTTP auth、OAuth 2.0、OpenID Connect 或 mTLS 等方案，凭据通过带外流程取得。
- 服务端必须认证每次请求，并在每个操作上按用户、组织、项目、Workspace 或 tenant 等宿主模型执行授权。
- AgentInterface 的 `tenant` 是由服务端解释的 opaque routing key。客户端必须回显卡片中的值，但它不是身份、ACL 或可信 owner 声明。
- `TASK_STATE_AUTH_REQUIRED` 只表示任务需要额外授权；规范明确禁止仅凭该状态转换就认为某个操作已获授权。授权范围、表示、有效期和撤销仍由实现、凭据发行方或扩展定义。
- AgentCard JWS signature 是可选能力。验证方还必须拥有可信密钥来源、处理 key rotation／revocation 并完成 JCS canonicalization；“卡片带 signature”本身不是完整信任链。
- Push URL 是服务端主动出网入口，官方要求防 SSRF、拒绝私网／localhost／link-local、设置超时、指数退避和有限重试；接收方必须验签／验 token、防重放并幂等处理。
- Nanobot 当前 Sandbox 默认断网，A2A 网络访问只能由服务端受控 Adapter 执行，不能交给 Sandbox 内模型任意连接。

#### 官方文档内部偏差

- `docs/whats-new-v1.md` 宣称 Task 有 `createdAt`／`lastModified`、push config 有 `createdAt`，但固定 commit 的权威 `specification/a2a.proto` 中没有这些字段。
- 专题 `streaming-and-async.md` 表述流在终态或中断态关闭，而核心规范的 Task lifecycle stream 明确要求在终态关闭；中断态后的连接处理存在文档口径差异。
- 这两处都必须以 Proto 数据模型和核心规范的明确要求为准。Adapter 还应把“连接关闭”与“Task 已终结”分开，最终以收到的 TaskState 判断。

#### 对 Nanobot 的取舍

- **直接采用模式：** AgentCard discovery、版本／binding negotiation、Message／Task／Artifact 映射、Task 终态不可重启、Get／Subscribe 补窗口和授权作用域。
- **兼容适配：** A2A task ID、context ID 和 message ID 只作为外部 binding；内部另存 owner、run ID、attempt、generation、idempotency receipt 和 provenance。
- **权限边界：** tenant 只路由；每次操作仍从认证 principal 派生 owner scope。`auth_required` 映射到 Permission／Credential flow，而不是自动许可。
- **Artifact 边界：** raw／URL Part 先导入受控 Artifact Store，校验大小、MIME、来源和 owner；不能把外部 URL 或文件名直接映射为宿主路径。
- **实验：** 首版只做受控 Client Adapter 和固定 allowlist；Server 暴露、push webhook 和跨组织 AgentCard trust 分别做独立威胁建模。
- **排除：** 把 A2A 当内部 subagent API；把 Task 当 durable scheduler；依赖 message ID 获得 exactly-once；把 tenant 当授权。

## 6. README、教程、草案与稳定事实的差异

| 来源 | 容易误读的声明 | 固定 commit 的事实 | 对 Nanobot 的约束 |
| --- | --- | --- | --- |
| OpenClaw | 已有插件 Harness，容易被当成成熟通用 Runtime 标准 | 官方文档明确标为实验性，参数镜像内部 Runner | 只提炼最小 Port，不复制合同 |
| OpenClaw | Workspace／多 Agent 隔离容易被当成 Sandbox／tenant isolation | 官方明确 Workspace 只是 cwd，插件 Store 仍可能全局 | Sandbox 和 owner scope 独立执行 |
| Bub | Tape 被描述为 append-only | reset 可 unlink，逐条写可产生半个 Turn，JSONL 可静默跳过坏行 | 只采用事实／Context 分离，不采用默认 Store |
| Bub | Hook 支持 deny，容易被当成安全策略点 | before hook 异常被隔离并跳过，最终可能 proceed | 安全否决必须失败关闭 |
| Agent Skills | 规范和 `skills-ref` 容易被理解为完全一致 | 小写文件名、Unicode name 和空 catalog 行为存在偏差 | canonical 与兼容模式分开 |
| Agent Skills | `allowed-tools` 容易被理解为授权 | 字段仍为实验性，规范未定义宿主权限和 Sandbox | 只能作为 capability request |
| AGENTS.md | 社区 v1.1 讨论容易被当成正式版 | 固定官方仓库只有普通 Markdown 的最小约定 | 不冻结提案字段和引用语义 |
| ACP | 仓库已包含 v2 schema，容易被当成稳定版 | README 明确 stable wire version 为 1，v2 公告明确是 Draft | v1 默认，v2 双门禁实验 |
| ACP | `session/load` 像持久会话事实源 | 协议只规定恢复和重放行为，持久化归 Agent 实现 | ACP 只做 Adapter／projection |
| A2A | “production-ready 1.0”容易被扩展为 exactly-once | Send 只 MAY 幂等，Task 无 lease／fence／attempt | 内部可靠性合同必须自建 |
| A2A | `tenant` 字段容易被当 owner | 规范明确它是 opaque routing key | owner 从认证 principal 派生 |
| A2A | `whats-new-v1.md` 宣称新增时间字段 | 权威 Proto 没有对应字段 | 代码生成与合同以 Proto 为准 |

## 7. 本批能力取舍矩阵

| 能力 | 主要证据 | 决策 | 进入阶段 | 前置条件／退出方式 |
| --- | --- | --- | --- | --- |
| 最小 Runtime Adapter | OpenClaw | 直接采用模式 | 阶段 1、3 | Nanobot 类型独立，合同测试覆盖 KT |
| Runtime capability negotiation | OpenClaw、ACP、A2A | 直接采用模式 | 阶段 1、3、6 | 缺失能力视为不支持，显式选择失败关闭 |
| 大型 OpenClaw Harness 合同 | OpenClaw | 排除 | 不实施 | 等 Nanobot 有两个真实 Runtime 后按需要扩展 |
| 分层 Tool policy | OpenClaw | 兼容适配 | 阶段 4、8 | 宿主／owner／parent policy 单调求交 |
| 子 Agent lineage 与深度 | OpenClaw | 直接采用模式 | 阶段 5、10 | 持久 envelope、预算、generation 和恢复 |
| Tape facts／Context view 分离 | Bub | 直接采用模式 | 阶段 2、7 | Event schema 版本化，projection 可重建 |
| JSONL FileTapeStore | Bub | 排除 | 不实施 | 需要数据库事务、checksum 和多 Worker 证明 |
| Anchor／handoff | Bub | 实验后采用 | 阶段 7 | 信息损失回放、摘要来源和版本快照 |
| Hook interception | Bub | 兼容适配 | 阶段 3、4 | 安全 Hook 失败关闭，观察 Hook 可隔离失败 |
| `SKILL.md` canonical 格式 | Agent Skills | 直接采用模式 | 阶段 8 | 严格 Parser、诊断、来源和 content hash |
| Skill 渐进披露 | Agent Skills | 直接采用模式 | 阶段 7、8 | catalog 预算、按需加载和会话快照 |
| `allowed-tools` 自动授权 | Agent Skills | 排除 | 不实施 | 始终转为请求能力并与宿主策略求交 |
| AGENTS.md 嵌套发现 | AGENTS.md | 兼容适配 | 阶段 8 | Workspace realpath、大小和 symlink 限制 |
| AGENTS.md v1.1 提案字段 | AGENTS.md issue | 观察 | 不进入合同 | 等官方合并、版本和 conformance tests |
| ACP v1 Adapter | ACP | 兼容适配 | 阶段 6 | capability、取消、权限和双向 RPC 夹具 |
| ACP v2 | ACP | 实验 | 阶段 6 后 | version negotiation + feature flag，保留 v1 |
| A2A Client Adapter | A2A | 实验后采用 | 阶段 6 | allowlist、TLS、principal mapping、幂等投影 |
| A2A Server／push | A2A | 延后 | 阶段 6 后 | 独立威胁建模、SSRF 防护、重放和配额 |
| A2A Task 作为内部 Run | A2A | 排除 | 不实施 | 只作为外部投影，不替换 Ledger |
| AgentCard JWS trust | A2A | 实验 | 阶段 6 | 可信 key store、JCS、rotation、revocation |

## 8. 对后续实现的具体约束

### 8.1 Runtime 与 KT

- `AgentRuntimePort` 只接收 Nanobot 已经解析好的 model route、auth binding、Prompt、Context、工具快照、权限和预算；
- KT Adapter 不再拥有业务历史、模型降级、数据库事务、owner scope 或 Sandbox 策略；
- KT 升级后优先删除为了兼容旧 KT 而存在的消息重排、全局 conversation 清理和框架类型泄漏，再按新 API 写法简化 Adapter；
- Runtime 可选能力采用显式 capability，缺失时走可解释降级或失败，不用 `hasattr` 式业务分叉扩散到路由层；
- 只有通过第二个真实 Adapter 和合同测试，才能对外宣称 Runtime 可替换。

### 8.2 Event、Tape、Trace 与协议投影

- Event Ledger 记录不可变运行事实；Context、ACP update、A2A Task、UI message、Trace 和 usage 都是可重建或可丢弃的投影；
- 一个 Turn／Attempt 必须有明确的 accepted、terminal 或 ambiguous commit 边界，不能依赖“最后通常会写 run event”；
- 幂等至少绑定 operation ID、payload hash、principal、attempt／generation 和结果 receipt；外部 message ID 只能作为输入之一；
- 连接关闭、客户端取消、Task 中断和内部终态必须分别建模，不能从 transport EOF 推断执行成功。

### 8.3 Skill 与项目指令

- `SKILL.md` 与 `AGENTS.md` 分开扫描、分开解析、分开显示来源；两者都只产生 Prompt 输入和能力请求，不产生权限；
- 所有动态文本保存 source、owner、realpath、版本／hash、加载时间、诊断和会话快照；
- 项目级内容必须在 Workspace 信任后加载，目录扫描有深度、数量、大小和 symlink 上限；
- Prompt Runtime 模板必须明确：项目指令和 Skill 不能覆盖系统安全规则、用户显式请求或工具权限。

### 8.4 ACP 与 A2A

- ACP 绑定的是“一个客户端如何控制一个 Coding Agent”；A2A 绑定的是“两个独立 Agent 如何交换消息和 Task”，两者不共用 session 状态机；
- 两种 Adapter 都通过统一 Event／Permission／Artifact Port 接入，但保留自己的外部 ID、版本、capability 和错误语义；
- ACP v1 是默认互操作基线，v2 只做可关闭实验；A2A 固定 1.0.0 语义并从 AgentInterface 协商 binding／version；
- 外部绝对路径、URL、raw bytes、MCP server 和 webhook 都必须先经过 owner、Workspace、Artifact、网络和 Sandbox 边界；
- 外部协议不直接写 `ChatLog`、`ConversationTurn` 或 Runtime 内部表，由 Adapter 生成规范化命令／事件后再由 Core 持久化。

## 9. 本批完成边界

本文件完成路线阶段 0.4 的第一项：六个官方来源均已固定 commit，读取了正式协议或核心源码，并记录许可证、具体路径、稳定／草案边界、源码偏差和 Nanobot 取舍。

尚未完成、不得提前勾选的内容：

- EverOS、EverAlgo、HyperMem、EverMemBench、EvoAgentBench 的数据模型和评测方法；
- Jeju、waveloom、dscode、penguin-harness、agent-os-harness、seajelly 的真实实现；
- 阶段 0.4 全部来源完成后的统一缺失项目清单；
- 阶段 0.5 全量能力取舍矩阵和本仓库现有实现映射。
