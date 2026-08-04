# Agent Harness 生态源码核验：记忆模型、检索与自进化评测

> 状态：来源项目第二组核验完成
>
> 核验日期：2026-08-03
>
> 对应路线：`.codex/plans/agent-harness-ecosystem-optimization-roadmap.md` 阶段 0.4 第二项
>
> 范围：EverOS、EverAlgo、HyperMem、EverMemBench、EvoAgentBench；LoCoMo 作为共同评测来源一并核验

## 1. 结论先行

这组六个来源提供了有价值的数据模型和评测反例，但没有一个项目可以整体移植为 Nanobot 的记忆、持久任务或自进化实现：

1. **EverOS 最值得参考的是分层记忆类型、owner scope、原始事实与派生索引分离，以及可检查的异步派生状态；不应把其 OME 当作可靠任务账本。** 当前崩溃恢复路径在“标记失败”和“重新入队”之间不是原子操作，源码明确承认这一窗口只提供 at-most-once；其 Markdown 锁也不能自动保证整个多进程写入链路安全。
2. **EverAlgo 最值得参考的是 persistence-free operator 边界、结构化输入／输出和不可变的聚类状态。** 它把存储、锁、调度和模型路由留给宿主，适合用于设计 Nanobot 的 Memory Port；但部分 operator 会调用注入的 LLM，因此“纯函数”只能理解为不内置持久化副作用，不能理解为确定性函数。
3. **EverOS 与 EverAlgo 的公开 LoCoMo 分类名称均有错误。** LoCoMo 原始类别 1/2/3/4 依次是 multi-hop、temporal、open-domain、single-hop；EverAlgo 把 1 和 4 写反，EverOS 则把 1、2、4 都标错。其 headline 指标还改用了自定义 LLM judge、排除了 adversarial 类别，不能直接与 LoCoMo 原论文的 token-F1 结果比较。
4. **HyperMem 的超图分层检索有研究价值，但仓库不足以支撑其公开分数。** 当前仓库没有测试、锁文件、数据集、原始结果和完整运行清单；脚本默认阶段、配置阈值与 README 不一致，内部无界重试可能挂起，评测异常还能缩小分母。
5. **EverMemBench 的数据集设计比当前评测仓库更值得采用。** 论文和固定版本数据集提供了跨 recall、awareness、profile 的 9 类任务和消息级证据引用；但当前官方评测代码不能直接读取当前 Hugging Face 数据格式，并会丢弃证据字段，因此不能把仓库声明视为一次可复现评测。
6. **EvoAgentBench 最值得参考的是训练／测试隔离、能力支持关系和按领域原生 verifier 评估。** 论文、当前 Hugging Face 数据集和榜单网站代表三个不同版本：论文是 4 个领域、528/267 个 train/test 任务；数据集后来加入 OmniMath，变成 5 个领域、1006/367；网站 CSV、生成后的 TypeScript 和页面领域样本数又互不一致。
7. **任何后续 benchmark 必须把数据版本、代码 commit、Prompt hash、模型／Provider 修订、随机种子、预期样本数、失败数和原始逐题结果写入不可变 Run Manifest。** 报告必须固定分母、失败即显式计错或整次运行失败，禁止用多个 judge pass 中的最大值作为 headline。
8. 本批只提取设计模式、数据契约和反例；**没有复制任何第三方代码，没有修改 README，也没有把外部 benchmark 分数写成 Nanobot 的能力声明。**

## 2. 核验方法和证据边界

### 2.1 方法

- 逐个固定官方 GitHub 仓库的完整 commit SHA，读取许可证、核心数据结构、写入／恢复路径、检索路径、评测入口、默认配置和结果产物；
- 对论文、GitHub 代码、Hugging Face 数据集和榜单站点分别固定来源，不把同一名称下的不同版本自动视为相同实验；
- 对 README 中的强声明回查实现；没有数据、运行产物或依赖锁时，不把宣传分数记为可复现证据；
- 对 LoCoMo 直接统计固定数据文件的类别数量、证据条数和跨 session 情况，并与官方评估代码交叉核对；
- 对 EverMemBench 下载固定数据版本后检查 topic、对话、消息、问题和引用完整性，并实际调用仓库 loader 验证格式兼容性；
- 对 EvoAgentBench 比较论文、固定数据集、网站 CSV、生成数据和前端聚合逻辑；
- GitHub star 只记录 2026-08-03 的筛选快照，不作为技术正确性或采用依据；
- 未运行需要外部闭源模型、大量 GPU 或大额调用费用的全量 benchmark，相关 headline 分数只作为“仓库声称”，不作为本次复现实验结果。

### 2.2 证据等级

| 等级 | 含义 | 后续用途 |
| --- | --- | --- |
| A | 固定 commit 的正式论文／数据、核心实现和测试或本地结构验证能够相互印证 | 可转化为 Nanobot 自有合同和验收项 |
| B | 有真实实现，但可靠性、复现材料或版本一致性不完整 | 只采用局部模式，必须独立实现和测试 |
| C | README、榜单或结果摘要缺少可审计产物，或内部来源相互冲突 | 仅作为研究线索，不用于能力声明 |
| X | 许可证不明、实现缺失或存在会破坏正确性的路径 | 排除直接复制或生产采用 |

## 3. 固定版本、热度和许可证边界

| 来源 | 固定版本 | 2026-08-03 star 快照 | 许可证核验 | 本批判断 |
| --- | --- | ---: | --- | --- |
| [EverOS](https://github.com/EverMind-AI/EverOS/tree/e5118c52a8d164815211343cb632617e6f010d4c) | `e5118c52a8d164815211343cb632617e6f010d4c` | 11,783 | 根目录 Apache-2.0；`NOTICE` 对 LoCoMo fixture 单列 CC BY-NC 4.0，CairoSVG optional extra 另有 LGPL-3.0 边界 | A/B：采用模型和派生状态模式，排除 OME 作为可靠账本 |
| [EverAlgo](https://github.com/EverMind-AI/EverAlgo/tree/d26fb2fdcb2f4ad672d310c242c793183969ac2e) | `d26fb2fdcb2f4ad672d310c242c793183969ac2e` | 23 | 根目录、README 和各包 `pyproject.toml` 声明 Apache-2.0，但 8 个包内 `LICENSE` 文本是 MIT | B/X：研究接口；许可证冲突澄清前不复制代码 |
| [HyperMem](https://github.com/EverMind-AI/HyperMem/tree/15c700908f3ec64d6931f253695e42c679a4c958) | `15c700908f3ec64d6931f253695e42c679a4c958` | 11 | Apache-2.0 | B/C：只参考超图检索概念，不采用实现或分数 |
| [EverMemBench](https://github.com/EverMind-AI/EverMemBench/tree/e10b3d52f0e4cfc5c124ad406b5d95c59c73738b) | `e10b3d52f0e4cfc5c124ad406b5d95c59c73738b` | 18 | 该 commit 无 `LICENSE` 或 `NOTICE` | A（论文／数据）／X（代码复制）：独立实现适配器和评测合同 |
| [EvoAgentBench](https://github.com/EverMind-AI/EvoAgentBench/tree/fdf8964da2914ddba69182f17a073548360cbd76) | `fdf8964da2914ddba69182f17a073548360cbd76` | 30 | MIT | A（论文方法）／C（网站分数）：只采用实验协议 |
| [LoCoMo](https://github.com/snap-research/locomo/tree/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376) | `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376` | 1,070 | CC BY-NC 4.0 | A：作为类别和官方指标的基准来源；不得混入商业运行数据 |

说明：

- star 数量是 GitHub API 在核验日返回的易变值，只用于回应“至少深入一定 star 的项目”的筛选要求；EverAlgo、HyperMem、EverMemBench 和 EvoAgentBench 虽然 star 较少，仍因原帖或来源链直接提及而逐项检查。
- EverMemBench 的当前 Hugging Face 数据集元数据声明 Apache-2.0，不会自动给无许可证的 GitHub 评测代码授权。
- LoCoMo 的非商业许可证会影响 fixture、派生数据和评测产物的使用边界；Nanobot 后续测试默认应使用自建的最小合成 fixture，不能直接把完整数据打入生产镜像。
- 本批没有依赖 GitHub 自动识别出的许可证标签作最终判断，而是读取了仓库内实际文本。

## 4. 跨项目确认的数据模型边界

### 4.1 原始事件、规范化记忆和派生索引必须分层

三个层次不能合为一张可任意覆盖的“memory”表：

| 层次 | 典型内容 | 正确性要求 | 参考来源 |
| --- | --- | --- | --- |
| 原始事实 | 对话消息、工具调用、任务结果、外部资产引用 | append-only、稳定 ID、owner、时间、来源可追溯 | EverOS `CanonicalMessage`、EverMemBench 消息引用 |
| 规范化记忆 | episode、atomic fact、foresight、profile、case、skill | 有版本、生成依据、状态和替代关系；不能丢失来源 | EverOS、EverAlgo |
| 派生索引 | embedding、BM25、topic、cluster、hyperedge、rerank cache | 可重建、可失效、可核对水位；不能反过来成为唯一事实源 | EverOS Cascade、HyperMem |

这与 Nanobot 现有 `ChatLog`（档案）和 `ConversationTurn`（工作内存）的分离原则一致。后续新增的长期记忆不能覆盖 `ChatLog`，也不能让向量库成为唯一事实源。

### 4.2 owner、scope 和来源是第一等字段

EverOS 把 user memory 与 agent memory 分开，并继续区分 app/project；EverAlgo 的 operator 则要求宿主提供已经限定范围的候选。对 Nanobot 的约束是：

- 每条原始事实和记忆都必须有 `owner_type`、`owner_id`、`workspace_id` 或等价作用域；
- `user`、`agent`、`group`、`project` 不能靠 Prompt 文本区分；
- 检索必须先做权限和 scope 过滤，再做向量／关键词召回和模型重排；
- 生成记忆必须保留 source event／message ID；删除或隐藏派生记忆不能删除档案事实；
- 跨 owner 共享知识必须通过显式授权和发布流程，不能靠共用全局可写索引。

### 4.3 Memory operator 与存储／调度应解耦

EverAlgo 的边界比 EverOS 的整体服务更适合 Nanobot 后续演进：operator 接收结构化输入和注入的 LLM／召回接口，返回新的值；数据库事务、锁、幂等、重试和索引更新由宿主负责。

但后续接口命名不能误导：调用 LLM 的 extractor、reflection 或 agentic rank 不是数学意义上的纯函数。正确合同应至少记录：

- 输入类型和输入内容 hash；
- Prompt 模板版本；
- 模型、Provider 和采样参数；
- 输出类型、来源引用和校验状态；
- 重试是否可能产生不同结果；
- 谁负责持久化、幂等键和并发控制。

### 4.4 检索答案和检索证据必须同时可评测

EverMemBench 的 `R` 字段提供消息级引用，LoCoMo 的 `evidence` 也能回到具体 session。只评最终答案准确率无法区分：

- 没检索到依据但模型猜对；
- 检索到正确依据但回答器失败；
- 检索结果包含越权或污染数据；
- 更新后的旧事实没有被正确压低；
- 评测适配器漏掉样本导致分数虚高。

因此 Nanobot 后续评测必须分开记录 retrieval recall／precision、evidence coverage、answer score、失败类型、延迟和 token／费用。

## 5. EverOS 源码核验

### 5.1 已检查源码和文档

- `README.md`
- `LICENSE`
- `NOTICE`
- `pyproject.toml`
- `docs/how-memory-works.md`
- `docs/storage_layout.md`
- `src/everos/memory/models.py`
- `src/everos/memory/search/dto.py`
- `src/everos/core/persistence/markdown/writer.py`
- `src/everos/core/persistence/locking.py`
- `src/everos/infra/persistence/sqlite/repos/md_change_state.py`
- `src/everos/memory/cascade/worker.py`
- `src/everos/infra/ome/records.py`
- `src/everos/infra/ome/_stores/run_record.py`
- `src/everos/infra/ome/_dispatch/runner.py`
- `src/everos/infra/ome/_background/crash_recovery.py`
- `benchmarks/README.md`
- `benchmarks/config.py`
- `benchmarks/config.toml`
- `benchmarks/run.py`
- `tests/e2e/test_add_flush_user_pipeline_e2e.py`
- `tests/e2e/test_add_flush_agent_pipeline_e2e.py`
- `tests/unit/test_benchmark_config.py`
- `tests/unit/test_infra/test_ome/test_crash_recovery.py`

### 5.2 数据模型与存储事实

EverOS 当前实现的核心不是“所有内容都进向量库”，而是分离事实源和派生查询面：

- Markdown 是 episode、fact、profile、case、skill 和 knowledge 等内容的可读事实源；
- SQLite 保存运行状态、审计、Cascade 状态、buffer 和 OME 记录；
- LanceDB 保存可重建的向量与 BM25 查询数据；
- user memory 包含 episode、atomic fact、foresight、profile；
- agent memory 包含 case 和 skill；
- knowledge 层包含 document 和 topic；
- scope 先区分 app/project，再区分 user/agent/knowledge；
- `CanonicalMessage` 包含 message ID、session、sender、role、timestamp、content 和 tool-call 结构；
- `/add` 先进入 buffer，boundary 形成 MemCell，episode 同步写入，部分派生与 agent memory 经 OME 异步处理，Cascade 再推进索引。

这是可参考的分层，但当前 domain adapter 会主动丢弃算法内部 ID，并由调用方决定 scope 和 source ID。Nanobot 如果采用类似 operator，必须由自己的 Port 层生成稳定业务 ID，不能把第三方内部 ID 暴露成长期合同。

### 5.3 查询边界

`src/everos/memory/search/dto.py` 和相关 service 表明：

- 搜索要求 `user_id` 与 `agent_id` 二选一；
- 支持 keyword、vector、hybrid 和 agentic 等检索方式；
- fact 作为 episode 的嵌套内容返回，profile 走单独结果；
- agent case／skill 与 user episode／profile 是不同查询面。

可采用的是“先按 owner 和类型形成候选，再选择召回策略”；不可采用的是把互斥参数和具体存储形态原样冻结为 Nanobot 公共 API。Nanobot 还需要 group/project/workspace 和授权快照，接口应由自身业务模型决定。

### 5.4 写入、锁和派生索引的可靠性边界

`writer.py` 采用同目录临时文件、flush、`fsync` 和 `os.replace`，并做路径 containment 检查；这是单个 Markdown 文件原子替换的良好模式。限制同样明确：

- 常用 per-path `asyncio.Lock` 只在当前进程和当前实例内生效；
- `locking.py` 提供 POSIX `fcntl.flock`，但必须由更高层调用方覆盖完整读改写临界区；
- SQLite 中的 Markdown change state 以路径为主键，状态为 pending／processing／done／failed；
- worker 启动时把遗留 processing 恢复为 pending，注释说明当前假设是单进程；
- LSN 使用读取 `MAX + 1` 的尽力生成方式，没有数据库唯一性约束时不能抵御并发 writer。

因此这些实现只能证明“每个阶段可观测”，不能证明多进程 exactly-once。Nanobot 的 Event Ledger、Outbox 和派生索引水位必须用数据库事务、唯一键、lease 和 generation fence 独立实现。

### 5.5 OME 崩溃恢复不是可靠任务队列

OME 的 run record 有状态、attempt 和错误信息，dispatcher 也有显式幂等注释；但关键失败窗口位于 `crash_recovery.py`：

1. 先把遗留运行标记为 crashed；
2. 再调用 `add_job` 重新入队；
3. 两步不是同一事务；
4. 第二步失败时，事件不会再被恢复。

源码注释将这个合同描述为 at-most-once，并要求需要 at-least-once 的策略自行增加外部监控。此外，`runner.py` 在终态写入失败时可能重放，而 `_record_start` 自身持久化失败只记录日志、没有可恢复 DB 记录。

结论：

- OME 可作为“异步派生任务实现案例”；
- 不得把它用作 Nanobot 的 Durable Task、消息投递、资产发布或外部副作用事实源；
- 后续 Event Ledger 必须把业务状态变化与 outbox 插入放在同一事务，worker 通过 claim lease、幂等键和 generation fence 执行。

### 5.6 LoCoMo benchmark 事实

EverOS benchmark 的优点包括阶段化 ADD → `wait_ready` → SEARCH → ANSWER → JUDGE、逐题 JSONL 和 `run_spec`；但存在以下影响解释的边界：

- 默认只检索 `speaker_a` owner，默认 `top_k=10`、agentic search；
- 图像消息没有文本时直接跳过，没有使用原始图像 caption；
- 排除 LoCoMo category 5 adversarial，只评 1,540 题；
- 回答默认 `gpt-4.1-mini`、judge 默认 `gpt-4o-mini`，judge temperature 为 0、每题 3 次多数票；
- `run_name` 同时成为 project ID，复用名称会复用 corpus，入口没有自动清空旧数据；
- 仓库只提供报告示例，没有提交可审计的真实逐题结果；
- 报告的 `max_accuracy` 是各 judge pass、pass 均值和多数票结果中的最大值，不应作为严谨 headline；
- `CATEGORY_NAMES` 把 category 1/2/4 写成 single-hop/multi-hop/temporal；正确名称应为 multi-hop/temporal/single-hop，四类中只有 category 3 的 open-domain 标签正确。

因此 EverOS 的 `run_spec` 和阶段产物结构可参考，公开 accuracy 不能直接拿来比较或写进 Nanobot 文档。

### 5.7 对 Nanobot 的取舍

| 能力 | 判断 | 原因 |
| --- | --- | --- |
| typed memory + owner scope | 采用模式 | 与本项目档案／工作内存分离兼容 |
| Markdown 事实源 + 可重建索引 | 实验 | 可审计性好，但要先验证并发、容量和恢复 |
| Cascade 状态／水位 | 采用模式 | 适合约束派生索引，不复用现有实现 |
| OME 作为普通异步派生 | 观察 | 可借鉴状态字段，仍需修正恢复语义 |
| OME 作为 Durable Task／Event Ledger | 排除 | 明确存在 at-most-once 丢失窗口 |
| benchmark headline | 排除 | 类别错误、指标不同、无真实逐题产物 |

## 6. EverAlgo 源码核验

### 6.1 已检查源码和文档

- `README.md`
- `LICENSE`
- 根目录及 8 个 package 的 `pyproject.toml`
- 8 个 package 内各自的 `LICENSE`
- `packages/everalgo-core/src/everalgo/types/chat.py`
- `packages/everalgo-core/src/everalgo/types/memories.py`
- `packages/everalgo-core/src/everalgo/types/agent.py`
- `packages/everalgo-core/src/everalgo/types/rank.py`
- `packages/everalgo-boundary/src/everalgo/boundary/chat.py`
- `packages/everalgo-boundary/src/everalgo/boundary/workspace.py`
- `packages/everalgo-clustering/src/everalgo/clustering/algorithm.py`
- `packages/everalgo-clustering/src/everalgo/clustering/state.py`
- `packages/everalgo-user-memory/src/everalgo/user_memory/episode.py`
- `packages/everalgo-user-memory/src/everalgo/user_memory/atomic_fact.py`
- `packages/everalgo-user-memory/src/everalgo/user_memory/foresight.py`
- `packages/everalgo-user-memory/src/everalgo/user_memory/profile.py`
- `packages/everalgo-agent-memory/src/everalgo/agent_memory/case.py`
- `packages/everalgo-agent-memory/src/everalgo/agent_memory/skill.py`
- `packages/everalgo-agent-memory/src/everalgo/agent_memory/profile.py`
- `packages/everalgo-rank/src/everalgo/rank/agentic.py`
- `packages/everalgo-rank/src/everalgo/rank/fusion.py`
- `packages/everalgo-rank/src/everalgo/rank/maxsim.py`
- `packages/everalgo-parser/src/everalgo/parser/video.py`
- `benchmarks/README.md`
- `benchmarks/common/stages/evaluate.py`
- `benchmarks/common/stages/types.py`
- `benchmarks/docs/variance-analysis.md`
- `benchmarks/results/locomo-93.51/REPRODUCTION.md`

### 6.2 Operator 数据模型

EverAlgo 当前要求 Python 3.12 以上，并拆成 core、boundary、clustering、rank、parser、user-memory、agent-memory 和 knowledge 包。关键结构包括：

- `MemCell` 使用可辨别联合区分普通 ChatMessage、ToolCallRequest 和 ToolCallResult；
- user memory 包含 Episode、Foresight、AtomicFact 和 Profile；
- agent memory 包含 AgentCase、AgentSkill 和 AgentProfile；
- skill 记录 confidence、maturity 和 source IDs；
- rank 输入由宿主提供预取的 sparse／dense 候选及 episode facts；
- clustering 返回新的冻结状态，由宿主持久化并负责锁；
- agentic rank 可以组合多查询、RRF、MaxSim、充分性判断和第二轮召回。

这类“operator 不拥有数据库”的边界适合 Nanobot。它使业务事实、权限、事务和模型策略仍由宿主掌控，也便于在 KT 与 Native Runtime 之间复用。

限制包括：

- extractor 和 agentic rank 会调用注入 LLM，结果仍会受模型版本、Prompt 和采样影响；
- skill maturity 更新是可选 operator，默认检索不会自动利用 maturity；
- `WorkspaceMemCellExtractor` 和 video parser 仍是占位实现；
- `extra=allow` 一类宽松模型有利于向前兼容，却会把拼写错误和未知字段静默带入；Nanobot 的持久合同不应照搬这一宽松度。

### 6.3 许可证冲突

根目录 `LICENSE`、README badge 和全部 package `pyproject.toml` 声明 Apache-2.0，但八个 package 自带的 `LICENSE` 文件是 MIT 文本。两套许可证没有在仓库中解释优先级。

处理原则：

- 可以阅读并提取抽象设计；
- 在上游澄清或法律审查之前，不复制实现、Prompt 或测试；
- 后续若只按公开行为独立实现，仍需记录来源和 clean-room 边界；
- 不把 GitHub API 的 Apache 标签当作解决冲突的证据。

### 6.4 LoCoMo 七阶段 benchmark

当前流程为 extract、可选 reflect、enrich、index、agentic search、answer、evaluate。仓库提供 `locomo-93.51` 的摘要和历史 variance 文档，但没有提交约 626 MB 中间结果及逐题 `eval_results.json`。

可确认事实：

- canonical 摘要声称 1,440/1,540 多数票正确，三次 judge 的平均准确率为 93.33%；
- extraction temperature 是 0.3，reflection 默认关闭；
- judge 为 `gpt-4o-mini`、temperature 0、每题 3 次；
- 历史五次运行的多数票范围为 92.40%–93.12%，均值 92.70%、标准差约 0.29 个百分点；
- 历史 variance 来自旧代码 `a0d3609` 和旧五阶段流程，不能证明当前七阶段的方差；
- 结果摘要包含 package 版本和配置，但缺少完整 git commit、数据 hash、Prompt hash、Provider 修订和 seed；
- category label 把 1 写成 single-hop、4 写成 multi-hop，正好与 LoCoMo 定义相反；
- adversarial 类别被排除，指标是自定义 LLM judge，而不是 LoCoMo 原始 token-F1。

`REPRODUCTION.md` 还声称 103 题的全部 judge pass 失败并被计错，但实现并非如此：

- `_judge_one` 的异常会向上传播，使整个 stage 明确失败；
- `stats.success` 被赋值为答对数量；
- `stats.failed = total - correct`，这里的 failed 是“答错数量”，不是 API 调用失败数量；
- 摘要的多数票正确数 1,440 还大于 1,540 - 103 = 1,437，从数值上也不可能存在 103 个全部 judge 失败且计错的样本。

因此这 103 应解释为 mean-of-runs 口径下的未正确样本数量，而不是 judge 基础设施失败。后续 Nanobot 报告必须分别命名 `incorrect_count`、`infrastructure_failure_count` 和 `missing_count`。

### 6.5 对 Nanobot 的取舍

| 能力 | 判断 | 原因 |
| --- | --- | --- |
| persistence-free typed operator | 直接采用模式 | 便于 Runtime、存储和模型解耦 |
| MemCell 工具调用联合类型 | 兼容适配 | 可映射现有消息，但由 Nanobot 定义稳定 schema |
| 宿主预取候选 + operator 重排 | 采用模式 | 权限过滤和数据库事务仍由本项目控制 |
| agentic 两轮召回 | 实验 | 必须先做成本、延迟和真实增益评测 |
| skill maturity | 观察 | 当前不是完整选择／淘汰系统 |
| 源码复制 | 暂时排除 | package 许可证冲突 |
| 93.51% headline | 排除 | 类别错误、产物缺失、指标不可直接比较 |

## 7. LoCoMo 对照核验

### 7.1 已检查来源

- `README.MD`
- `LICENSE.txt`
- `data/locomo10.json`
- `task_eval/evaluation.py`
- `task_eval/evaluate_qa.py`
- `task_eval/evaluation_stats.py`
- `static/paper/locomo.pdf`

### 7.2 类别和样本事实

固定数据包含 10 组长对话，QA 分布为：

| Category | 官方语义 | 数量 | 数据证据特征 |
| ---: | --- | ---: | --- |
| 1 | multi-hop | 282 | 276 题有多个 evidence，269 题跨 session |
| 2 | temporal | 321 | 需要时间推理 |
| 3 | open-domain | 96 | 结合对话与外部常识 |
| 4 | single-hop | 841 | 795 题只有一个 evidence，只有 2 题跨 session |
| 5 | adversarial | 446 | 无依据／不可回答的对抗样本 |
| 合计 | — | 1,986 | 前四类合计 1,540 |

这个直接统计同时解释了为何 EverOS 和 EverAlgo 对类别 1、4 的标签是错误的：类别 1 明显以跨 session、多 evidence 为主，类别 4 明显以单 evidence 为主；LoCoMo 论文和官方评估定义还确认 category 2 是 temporal，而不是 EverOS 所写的 multi-hop。

### 7.3 官方指标与第三方 headline 不同

LoCoMo 官方评估代码使用规范化、Porter stemming 后的 token-level F1，并按类别选择相应逻辑，同时包含 adversarial 评价。EverOS／EverAlgo 则：

- 排除 category 5；
- 用闭源 LLM 判定 CORRECT／WRONG；
- 多次 judge 再做多数票或均值；
- 可能改变回答模型、检索上下文和图像处理。

所以两类结果回答的是不同问题，不能放在一张“LoCoMo accuracy”表中直接排序。后续若采用 LoCoMo，至少并列报告：

1. 官方 token-F1；
2. 固定 Prompt 和固定模型版本的 LLM judge，仅作为辅助；
3. category 1–5 的正确标签与分项分数；
4. 检索证据 recall；
5. 图像消息处理策略；
6. 失败、跳过和缺失样本数量。

## 8. HyperMem 源码核验

### 8.1 已检查源码和脚本

- `README.md`
- `LICENSE`
- `requirements.txt`
- `hypermem/config.py`
- `hypermem/types.py`
- `hypermem/structure.py`
- `hypermem/extractors/topic_extractor.py`
- `hypermem/extractors/episode_extractor.py`
- `hypermem/extractors/fact_extractor.py`
- `hypermem/extractors/hypergraph_extractor.py`
- `hypermem/llm/embedding_provider.py`
- `hypermem/llm/reranker_provider.py`
- `hypermem/main/eval.py`
- `hypermem/main/stage1_memory_extraction.py`
- `hypermem/main/stage2_hypergraph_extraction.py`
- `hypermem/main/stage3_hypergraph_index.py`
- `hypermem/main/stage4_hypergraph_retrieval.py`
- `hypermem/main/stage5_response.py`
- `hypermem/main/stage6_eval.py`
- `scripts/run_eval.sh`
- `scripts/serve_embedding.sh`
- `scripts/serve_reranker.sh`

### 8.2 数据模型与检索方法

HyperMem 把会话提取为 Topic、Episode 和 Fact 三层节点，再建立 FactHyperedge 和 EpisodeHyperedge。边带有 role 和 weight，检索流程大致为：

1. LLM 增量提取 topic、episode 和 fact；
2. 构造超图；
3. 用 BM25 与 Qwen embedding 建索引；
4. 对超边 embedding 做 softmax 权重；
5. 以原始节点 embedding 与邻接聚合向量组合，默认 `alpha=0.5`；
6. 自顶向下从 topic 到 episode 再到 fact，结合 BM25、dense、RRF 和 reranker 返回上下文。

应注意：超边权重主要影响图连接和传播后的向量，最终相似度并没有把边权作为独立、可解释的评分项直接融合。因此“带权超图检索”不等于最终每个结果都有可审计的边权贡献。

### 8.3 实现与复现问题

仓库存在会直接影响运行和结果解释的问题：

- 没有测试目录、Python package manifest、锁文件、数据集或逐题结果；`requirements.txt` 只给下界或不固定版本；
- README 的论文链接是 `#`，clone 命令仍有 `<org>` 占位符；仓库本身无法复现 README 中的 ACL 2026／92.73 声明；
- README 写 topic/episode/fact 阈值为 15/25/30，配置默认值是 15/20/30，脚本默认又是 10/10/30；
- README 把一条命令描述为六阶段全流程，但 `run_eval.sh` 默认只跑 `2 3 4 5 6`，省略 memory extraction；
- topic 和 fact extraction 内有无界 `while True`；外层“最多 100 次重试”不能限制内部循环，调用异常或解析持续失败时可能无限挂起和消耗费用；
- stage 5 重试耗尽后返回空字符串，搜索结果缺失时可能直接不输出该题；
- stage 6 对一个 group 的异常返回空 group，最终分母只统计成功生成的评分条目，失败可能缩小分母并抬高 accuracy；
- 服务脚本硬编码 `/Evermind/sh_evermind/...` 路径，并假定 8 张 GPU 分为 0–3 与 4–7，不能视为通用运行入口；
- 没有 run manifest、git commit、数据 hash、Prompt hash、精确模型／Provider 版本、seed 或原始结果。

### 8.4 对 Nanobot 的取舍

| 能力 | 判断 | 原因 |
| --- | --- | --- |
| topic → episode → fact 分层 | 实验 | 适合评估复杂记忆，不应先验成为默认 schema |
| 超图传播与 top-down 检索 | 研究实验 | 需与简单 hybrid baseline 比较收益、成本和延迟 |
| 当前 extractor／eval 实现 | 排除 | 无界重试、分母变化、缺少测试和复现材料 |
| README 分数 | 排除 | 无数据、产物和一致运行配置支持 |

如果未来实验超图，应在独立 feature flag 下进行，原始事实仍存于 Nanobot 账本；每个派生节点和边记录 extractor 版本及 evidence，整个图必须可删除重建。

## 9. EverMemBench 核验

### 9.1 固定论文和数据集

- 论文：[EverMemBench, arXiv:2602.01313v3](https://arxiv.org/abs/2602.01313v3)
- 本次下载的论文 PDF SHA-256：`dfb8fb6f5372c04ec4f98088655acbe2447623f869000262c2efd84c1e4866cb`
- 数据集：[EverMind-AI/EverMemBench 固定 revision](https://huggingface.co/datasets/EverMind-AI/EverMemBench/tree/a6b210a32248e841967b7b64a64281d2ff3f669d)
- 数据集 revision：`a6b210a32248e841967b7b64a64281d2ff3f669d`
- 数据集元数据许可证：Apache-2.0

论文定义 5 个虚构项目、170 名员工、51,023 条消息、2,400 个问题，覆盖约 422.5 万 token。九类任务为：

| 维度 | 任务 | 数量 | 题型 |
| --- | --- | ---: | --- |
| Recall | single-hop | 213 | 开放回答 |
| Recall | multi-hop | 249 | 开放回答 |
| Recall | temporal | 300 | 开放回答 |
| Awareness | constraint | 402 | 多选 |
| Awareness | proactivity | 427 | 多选 |
| Awareness | update | 268 | 多选 |
| Profile | style | 176 | 多选 |
| Profile | skill | 169 | 多选 |
| Profile | role | 196 | 多选 |
| 合计 | 9 类 | 2,400 | 开放回答 762；多选 1,638 |

### 9.2 固定数据完整性实测

本次直接读取固定 revision 的五组 topic 文件，得到：

- 各项目问题数为 488、471、467、481、493，总计 2,400；
- 对话天数合计 1,263；
- 消息总数 51,023；
- 不重复 speaker 数 170；
- 每题平均约 6.65 个 reference entry；
- 共 15,964 个 reference entry、72,291 个 message pointer；
- 将所有 pointer 回查固定 dialogue 后，缺失数量为 0。

这些证据说明当前数据集内部引用是完整的，也说明 `R` 不是可忽略的装饰字段：它足以支持消息级 evidence recall 和错误归因。

### 9.3 GitHub 评测代码模型

已检查：

- `README.md`
- `requirements.txt`
- `env.template`
- `eval/cli.py`
- `eval/config/pipeline.yaml`
- `eval/config/prompts.yaml`
- `eval/src/core/data_models.py`
- `eval/src/core/loaders.py`
- `eval/src/core/qa_loader.py`
- `eval/src/core/pipeline.py`
- `eval/src/core/answerer.py`
- `eval/src/core/evaluator.py`
- `eval/src/adapters/base.py`
- `eval/src/adapters/evermemos_adapter.py`
- `eval/src/adapters/mem0_adapter.py`
- `eval/src/adapters/memobase_adapter.py`
- `eval/src/adapters/memos_adapter.py`
- `eval/src/adapters/zep_adapter.py`
- `eval/src/adapters/llm_adapter.py`
- `tools/analyze_results.py`

代码定义 GroupChatMessage、day/groups、Dataset、QAItem、SearchResult、AnswerResult、LightAnswerResult 和 EvaluationResult；主流程是 Add → Search → Answer → Evaluate，并提供 Memos、Mem0、Memobase、EverMemOS、Zep 和 full-dialogue LLM adapter。

### 9.4 当前发布数据与 loader 不兼容

对固定数据文件实际调用当前代码得到两类确定错误：

1. `load_groupchat_dataset(.../01/dialogue.json)` 抛出缺少 `dialogues` key 的 `ValueError`。当前 Hugging Face canonical dialogue 文件顶层是 list，loader 只接受包装成 dict 的旧格式。
2. `load_qa(.../01/qa_01.json, limit=1)` 抛出缺少 `question` 的 `ValueError`。当前文件顶层也是 list，字段是 `Q`、`A`、`R`；loader 将它路由到要求 `question` 的 legacy parser。

仓库另有 qars parser，但只在顶层为 `{qars: [...]}` 时进入；即使人为包装，它也会：

- 读取 Q 和 A，却丢弃 R；
- 不保存 topic／project metadata，写成空字典；
- 使后续评测无法计算论文所需的消息级 evidence recall 或 oracle 分析。

因此“数据公开”和“评测代码公开”不等于当前 commit 可以直接复现论文实验。Nanobot 若接入该 benchmark，必须为固定数据 revision 独立实现严格 loader，并用总数与引用完整性做门禁。

### 9.5 评测可靠性边界

当前评测实现还有以下问题：

- 多选题直接比较选择，开放题走 LLM judge；
- evaluator 默认 `num_runs=1`，CLI 没有暴露 `--num-runs`，普通运行并不是多 judge 共识；
- judge 重试耗尽返回 False，分母会保留，这一点比静默跳过可靠；
- search 重试耗尽会写出显式 `(Search failed)`，分母同样保留；
- full-dialogue LLM adapter 的 warmup 使用第一题，正式 batch 又包含第一题，产生重复调用费用；
- resume 只按 question ID、输出文件名和 system/user 组合判断，没有数据 hash、配置 hash、Prompt hash 或模型修订，旧结果可能污染新运行；
- 结果文件没有固定代码 commit、dataset revision、Prompt hash、Provider 修订、adapter 参数和各类失败数；
- 各 adapter 的 top-k、token budget、上下文格式和 group ID 行为并不统一，最终比较同时测量了记忆系统和 adapter 工程差异；
- `requirements.txt` 把多个可选厂商 SDK 混在一起且不锁版本。

### 9.6 对 Nanobot 的取舍

| 能力 | 判断 | 原因 |
| --- | --- | --- |
| 3 维度／9 任务评测框架 | 采用模式 | 比单一 recall 更接近主动性和画像需求 |
| 消息级 evidence pointer | 直接采用模式 | 支持检索质量和回答质量分离 |
| 当前数据 revision | 可选测试数据 | 受 Apache-2.0 约束，需固定 hash 和隔离下载 |
| 当前 GitHub loader | 排除 | 无法读取当前发布数据且丢证据 |
| 当前 adapter 横向分数 | 仅线索 | 输入预算和格式没有严格对齐 |
| 当前仓库代码复制 | 排除 | 固定 commit 没有许可证 |

## 10. EvoAgentBench 核验

### 10.1 论文、数据集和网站是三个版本

- 论文：[EvoAgentBench, arXiv:2607.05202v1](https://arxiv.org/abs/2607.05202v1)
- 本次下载的论文 PDF SHA-256：`fe081a5cd3b355de5aafd1751db97bbc92f5c2dfb7905f5a74350b815ec732b7`
- 数据集：[EverMind-AI/EvoAgentBench 固定 revision](https://huggingface.co/datasets/EverMind-AI/EvoAgentBench/tree/3ac46d860f2f89ff4000f03c9936b618d10570ad)
- 数据集 revision：`3ac46d860f2f89ff4000f03c9936b618d10570ad`
- 榜单网站源码：固定 GitHub commit `fdf8964da2914ddba69182f17a073548360cbd76`

三者不能混为一次实验：

| 来源 | 领域 | Train | Test | 主要用途 |
| --- | ---: | ---: | ---: | --- |
| 论文 v1 | 4 | 528 | 267 | 正式方法和实验结论 |
| 当前 HF 数据集 | 5 | 1,006 | 367 | 增加 OmniMath 后的任务划分／元数据 |
| 当前网站 | 5 | 未提供可审计划分 | 未提供可审计划分 | 展示聚合结果，不是评测 runner |

论文四个领域分别是 BrowseComp+ 154/65、SWE-bench Verified 87/56、LiveCodeBench 182/86、GDPVal 105/60。当前数据集保持这四组数量，又加入 OmniMath 478/100，因此总数变为 1,006/367。

### 10.2 论文中可参考的方法

论文把多个训练轨迹压缩为结构化 ability card，字段包括触发条件、执行步骤、证据、边界和角色；再用三个 LLM adjudicator 加专家审查做保守 canonicalization，构建能力图，并保证论文 test task 有 train-side ability 支持。

实验层面：

- 使用 OpenClaw 与 Nanobot 两种 scaffold；
- 使用 Qwen3.5-27B、Qwen3.5-397B 和 Gemma-4-31B 三种 backbone；
- 每个实例独立运行三次并报告标准误；
- 与 Memento、Reflexion Buffer、GEPA 等自动方法比较；
- Anchor 是 curator diagnostic，不是可部署的自动方法；
- 每个领域使用自身 verifier，再对领域分数做等权平均；
- 同时记录 turn/cost 变化。

最重要的负面结论是：每种自动进化方法都存在负迁移单元格。Nanobot 不能把“从成功轨迹提取 skill”直接等同于稳定提升，候选 skill 必须在隔离的 held-out 集上通过，并可按 scope、版本和来源回滚。

论文也明确存在外部有效性限制：源 benchmark 可能受预训练污染；测试任务优先选择有改进空间的样本，所以 delta 不是随机总体的无偏估计；每个领域 test 只有 56–86 个样本；能力抽取器只有一个。后续计划不能省略这些限定语。

### 10.3 当前 Hugging Face 数据集边界

固定数据实测确认：

- 原论文四个领域 train/test ID 数量与论文一致；
- 四个领域各自 train/test 没有 ID 重叠；
- 新增 OmniMath 有 478/100 个任务；
- OmniMath 文件直接包含题目数据，其余领域主要保存上游 benchmark ID 和元数据；
- 数据仓库没有完整 trace、ability graph、评测 runner、模型输出或 verifier 环境。

论文关于“每个测试任务均有训练侧能力支持”的结论来自四领域实验，不能自动外推到后来加入的 OmniMath。若 Nanobot 采用五领域版本，必须重新生成并验证 support graph。

### 10.4 网站源码不是可复现 benchmark

已检查：

- `README.md`
- `LICENSE`
- `package.json`
- `scripts/csv-to-data.js`
- `src/data/leaderboard.csv`
- `src/data/leaderboard-data.ts`
- `src/components/Leaderboard.tsx`
- `src/components/ResearchNarrative.tsx`
- `src/components/BenchmarkDomains.tsx`
- `src/components/SkillMethods.tsx`

固定 commit 中存在多组冲突：

- README 声称展示 EverOS、EvoSkill、Memento、OpenSpace 和 RB 五种方法；实际 CSV 只有 EverOS、GEPA、Memento、RB 四种；SkillMethods 也只启用四张卡，EvoSkill 和 OpenSpace 被注释；
- CSV 有 80 行，即 2 个 agent × 2 个 Qwen 模型 × 5 个领域 × 4 种方法，没有重复键；
- 已提交的 `leaderboard-data.ts` 有 114 行和 7 种方法，比 CSV 多 34 行；80 个共同键中有 64 个数值不同；
- `prebuild` 会从 CSV 重写 TypeScript，因此仓库 clean checkout 在构建前后使用的事实源不同；
- 页面 `DOMAIN_INFO` 中 SWE 为 101/26、LiveCode 为 97/39、GDP 为 87/58，与论文和当前 HF 数据集均不一致；只有 Browse 154/65 和 OmniMath 478/100 一致；
- “Overall” 只要求至少两个领域结果就做不加权平均，没有检查五领域完整覆盖；
- 成本汇总通过正则抽取百分比后直接平均，但 OmniMath 使用字符数变化，其余领域使用 turn 数变化，页面最终丢弃单位，得到的是无意义的混合量；
- 不同领域的原生分数语义不同，页面将其变化百分比并列排序，却没有原始运行、seed、误差条、run ID、代码 commit、模型／Provider 修订或 verifier 版本。

此外，网站 CSV 的方法、领域和模型组合也不是论文实验表：它加入 EverOS、OmniMath，移除 Anchor，只保留两个 Qwen backbone。网站结果只能视为产品展示数据，不能反向证明论文方法或某个记忆系统有效。

### 10.5 对 Nanobot 的取舍

| 能力 | 判断 | 原因 |
| --- | --- | --- |
| train/extract/evaluate 状态隔离 | 直接采用模式 | 防止测试泄漏和在线记忆污染 |
| ability card + source evidence | 实验 | 可作为候选 Skill 中间表示 |
| support graph | 实验 | 必须由固定数据和规则重新验证 |
| domain-native verifier | 采用模式 | 避免一个 LLM judge 覆盖所有任务 |
| 三次独立运行 + 标准误 | 采用模式 | 比三次 temperature=0 judge 更能反映运行方差 |
| 当前网站 Overall／cost | 排除 | 数据源、样本数和单位不一致 |
| 自动发布提取出的 Skill | 排除 | 论文自身显示普遍存在负迁移 |

## 11. README／论文／数据／实现不一致清单

| 来源 | 对外声明或容易形成的理解 | 固定源码／数据事实 | 处理 |
| --- | --- | --- | --- |
| EverOS | OME 可承担通用后台恢复 | 崩溃标记和重新入队非原子，明确为 at-most-once 窗口 | 不能用于可靠任务账本 |
| EverOS | LoCoMo 类别报告可直接解释 | category 1、2、4 标签错误，排除 adversarial，并改用 LLM judge | 仅保留逐题阶段设计 |
| EverOS | Max accuracy 是主要能力值 | 它取单次 pass、均值和多数票中的最大值 | 禁止作为 headline |
| EverAlgo | operator 是 pure function | 多个 operator 调用注入 LLM | 只称 persistence-free boundary |
| EverAlgo | 仓库是统一 Apache-2.0 | package 内 LICENSE 是 MIT，和元数据冲突 | 澄清前不复制 |
| EverAlgo | 103 题 judge 全部失败且计错 | 代码失败即抛异常；103 实为 `total - correct` | 分离答错与基础设施失败 |
| EverAlgo | C1 single-hop、C4 multi-hop | LoCoMo 正确含义相反 | 修正全部报告映射 |
| HyperMem | 一条命令执行完整六阶段 | 默认脚本省略 stage 1 | 不把 README 当运行合同 |
| HyperMem | 配置阈值明确 | README、配置和脚本给出三组值 | 所有参数进入 Run Manifest |
| HyperMem | 92.73 可由仓库复现 | 无数据、结果、锁文件或论文链接 | 分数不采信 |
| EverMemBench | 官方仓库可评当前官方数据 | 当前 loader 无法读取当前 HF 顶层格式 | 独立实现固定 revision loader |
| EverMemBench | 可评证据检索 | qars parser 丢弃 `R` | evidence 必须是一等产物 |
| EvoAgentBench | 论文、数据、网站是同一 benchmark | 领域数、任务数、方法和结果均存在版本分叉 | 各来源单独固定 revision |
| EvoAgentBench | 网站 Overall 和 cost 可横向排序 | 覆盖不完整也聚合，且 cost 混合 chars 与 turns | 不采用网站分数 |

## 12. 对 Nanobot 后续阶段的硬约束

### 12.1 对 Event Ledger 和 Durable Task

- 不以 EverOS OME 或文件队列作为生产账本；
- 业务状态与 outbox event 在同一数据库事务提交；
- 每个任务具有稳定 idempotency key、attempt、lease owner、lease expiry 和 generation；
- worker 完成写入必须校验 generation fence，旧 worker 不能覆盖新 claim；
- 外部副作用记录 request、result、可重试分类和不可重复边界；
- 派生索引只消费已提交 event，并记录连续水位和缺口；
- 恢复测试必须实际注入“提交前、提交后、claim 后、终态写入前、进程退出后”的故障。

### 12.2 对长期记忆和 Context Engine

- 原始 `ChatLog` 保持档案事实源，长期记忆是带来源和版本的派生物；
- `ConversationTurn` 仍只承担可清理的工作上下文，不被外部 memory schema 替换；
- memory type 至少区分 episode／fact／profile／case／skill，但首期只实现有明确消费方的最小集合；
- owner／workspace／授权过滤发生在召回前；
- 每个派生记忆保存 source message/event IDs、extractor、Prompt、模型和创建版本；
- 向量、BM25、cluster 和 graph 均为可重建索引，有状态、水位和重建入口；
- agentic 第二轮召回、超图和 reflection 必须在简单 hybrid baseline 之后以实验开关加入。

### 12.3 对 Skill 提取和自进化

- trajectory 只能产生候选 ability／skill，不能直接覆盖正式 Skill；
- 候选包含 trigger、procedure、evidence、boundary、role、来源 run 和适用 scope；
- 训练轨迹、候选提取、测试评估和线上发布使用隔离状态；
- 测试任务不得被写入待评候选的记忆或上下文；
- 为每个 test task 保存 train-side support 证据，但不能只靠 LLM 自报支持关系；
- 按领域使用确定性 verifier 优先，LLM judge 仅用于确实无法规则判定的任务；
- 任意一个关键领域负迁移都阻止全局自动发布；允许限定 scope 的实验版本；
- 发布必须经人工批准，保留旧版本和一键回滚。

### 12.4 对统一评测框架

每次 Run Manifest 至少包含：

- run ID、创建时间、代码 commit 和 dirty 状态；
- 数据集名称、revision、文件 hash、许可证和预期样本数；
- Runtime、Memory、Skill 和 adapter 版本；
- 全部 Prompt hash；
- 模型名、Provider、API base、模型 revision、sampling 参数；
- seed、并发、超时、retry、top-k、token／字符预算；
- train／test／warmup 的隔离标识；
- verifier／judge 版本和次数；
- correct、incorrect、infrastructure failure、timeout、explicitly excluded、missing 数量；
- 逐题输入引用、检索证据、答案、判定、延迟、token 和费用；
- 聚合公式及单位。

必须满足的 invariant：

```text
expected_total
  = correct
  + incorrect
  + infrastructure_failure
  + timeout
  + explicitly_excluded
```

`missing` 定义为预期总数减去等式右侧，必须为 0，否则整次运行无效。`explicitly_excluded` 只允许计入运行前写入 Manifest 的固定排除规则；运行中的跳过必须归入失败或 timeout。重试耗尽不得通过少写一行来缩小分母。不同单位不得求平均，领域覆盖不完整时不得计算 Overall。

### 12.5 对 LoCoMo／EverMemBench／EvoAgentBench 的具体接入顺序

1. 先实现合成 fixture 和评测框架 invariant，不下载第三方大数据进入仓库；
2. 实现 LoCoMo 固定 revision adapter，核对 1,986／1,540 数量和正确类别映射；
3. 同时报官方 token-F1 与辅助 LLM judge，后者固定 Prompt 和模型修订；
4. 实现 EverMemBench 固定 revision adapter，保留全部 `R` evidence，核对 2,400／51,023／170 和零缺失 pointer；
5. 将 recall、awareness、profile 分开报告，不把多选和开放回答混成无解释的单值；
6. 最后才实现 EvoAgentBench adapter，先选择论文四领域版本；若选择五领域 HF 版本，重新验证 OmniMath 的 support graph；
7. 用 domain-native verifier 和独立多次运行评估候选 Skill，禁止引用网站现有 Overall 作为基线。

## 13. 本批取舍矩阵

| 来源能力 | 直接采用模式 | 兼容／实验 | 观察 | 排除 |
| --- | --- | --- | --- | --- |
| EverOS typed memory／owner scope | ✓ | | | |
| EverOS Markdown 事实源 | | ✓ | | |
| EverOS Cascade 状态 | ✓ | | | |
| EverOS OME Durable Task | | | | ✓ |
| EverAlgo persistence-free operator | ✓ | | | |
| EverAlgo agentic rank／reflection | | ✓ | | |
| EverAlgo 源码复制 | | | | ✓（许可证待澄清） |
| HyperMem 分层／超图 | | ✓ | | |
| HyperMem 当前运行和分数 | | | | ✓ |
| LoCoMo 类别／官方 token-F1 | ✓ | | | |
| EverMemBench 9 类任务／evidence | ✓ | | | |
| EverMemBench 当前 loader／代码复制 | | | | ✓ |
| EvoAgentBench 状态隔离／native verifier | ✓ | | | |
| EvoAgentBench ability graph | | ✓ | | |
| EvoAgentBench 网站聚合分数 | | | | ✓ |

## 14. 本批完成边界与未完成范围

本文件完成了路线阶段 0.4 第二项：

- 六个仓库均固定 commit、许可证和源码路径；
- 对记忆类型、owner scope、持久化、恢复、检索和评测实现逐项核验；
- 对两份论文、两个固定 Hugging Face revision 和 LoCoMo 原始数据／评估代码交叉检查；
- 实际验证 EverMemBench 当前 loader 与当前数据不兼容；
- 实际核对 LoCoMo 类别分布和 EverMemBench 引用完整性；
- 比较 EvoAgentBench CSV、生成数据和前端聚合逻辑；
- 形成了明确的采用、实验和排除结论。

本批明确没有完成：

- 没有运行 EverOS、EverAlgo、HyperMem 或 EverMemBench 的付费全量 LLM benchmark；
- 没有复现任何 README／论文 headline 分数；
- 没有实现 Nanobot 的 Memory Port、Event Ledger 或 benchmark runner；
- 没有修改 README；
- 没有完成阶段 0.4 第三组 Jeju、waveloom、dscode、penguin-harness、agent-os-harness 和 seajelly 的源码核验；
- 没有提前勾选阶段 0.5 的全量取舍矩阵，因为第三组来源仍待核验。

只有在第三组来源完成并汇总全局重复能力后，才能把本文件的局部取舍转成最终实施顺序。
