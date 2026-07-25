# Nanobot 硬编码治理与模块化单体总实施计划

> - 状态：执行版；按阶段顺序实施和验证，未经用户明确授权不提交。
> - 基线：`master`，`35b8904 fix(模型路由): 修复只读状态与日报硬编码模型`。
> - 形成日期：2026-07-23。
> - 最近修订：2026-07-24，阶段 0～8 与 A22 代码切片完成；回填阶段 9 本地
>   验收证据，并显式保留真实 Docker、生产部署、灰度和观察门禁。
> - 决策来源：此前架构审查、硬编码审计、框架参考讨论、Sandbox 管理讨论，以及表达／黑话／群体记忆的连续问答。

## 1. 本计划解决什么

这不是一次推倒重写，也不是再造通用插件平台。本计划要完成以下收敛：

1. 保留 `7023383 refactor(运行时): 建立模块化单体治理边界` 已经落地的 Port、Descriptor、Registry、生命周期和部署加固，不重复实现。
2. 补齐仍散落在 Python、WebUI、部署脚本和 Prompt 调用方中的硬编码语义判断、重复路由表、自由文本错误分支和隐式生命周期。
3. 用最小充分的类型化 Contract、Descriptor、Policy、Registry 和显式 Composition Root 建立模块边界。
4. 把私聊 Timing、新闻日报、表达／黑话／群体记忆这 3 条质量较差的语义链路改造成可验证、可审核、可运营的实现。
5. 保留协议解析、格式校验、安全 allowlist、资源上限和确定性数据一致性规则，不把所有正则一概删除。
6. 让现有 KT 只作为 `nanobot_kt` 后面的 Agent Runtime Adapter；本轮不升级 KT，也不迁移到 Pi。

最终形态仍是单体部署，但内部具有明确模块边界：

```text
OneBot / Web / Admin / Worker 协议适配器
                    │
                    ▼
            ApplicationModule
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
     Contract     Policy      Registry
        │           │           │
        └───────────┴───────────┘
                    │
                    ▼
        Application Service / Port
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
      KT Adapter  DB Adapter  外部 Provider
```

这里的 Registry 是代码所有、启动期构建、验证后冻结的目录，不是任意 Python 插件发现器。简单的两个分支仍可使用普通类型化 Policy，不强行包装成插件。

## 2. 状态定义

| 状态 | 含义 |
|---|---|
| 已完成 | 当前代码已有生产接线和直接测试，本计划只保留回归门禁 |
| 大部分完成 | 核心边界已落地，仍有少量旧入口或重复事实源 |
| 部分完成 | 已有可复用基础设施，但尚未覆盖该决策的完整生产路径 |
| 未完成 | 当前没有对应抽象，或现有实现仍是待替换的硬编码路径 |
| 需复核 | 代码表面存在实现，但必须通过行为测试或生产证据确认 |
| 非目标 | 已明确推迟或明确不做 |

## 3. 不可改变的实施原则

### 3.1 最小充分抽象

- 稳定扩展点使用 Descriptor、Policy、Registry 和类型化 Contract。
- 单一模块内部、不会扩展的两个简单分支保留普通函数。
- 不为“看起来更框架化”引入动态插件加载、目录扫描、同名覆盖或运行时执行第三方代码。
- 所有新抽象必须至少接入一条真实生产路径，不能只存在于测试替身。

### 3.2 确定性规则与语义判断分离

以下规则继续由代码确定性执行：

- 路径、URL、日期、数字、分页、MIME、CQ 码和协议格式解析；
- JSON Schema、OpenAPI、工具参数和数据库字段校验；
- Docker、Sandbox、权限、配额、资源限制和安全 allowlist；
- 精确 Hash、唯一约束、幂等键、租约 fencing 和状态机；
- 输出长度、超时、重试次数、冷却时间和明确数值策略；
- SQL 只读验证、Prompt 权威边界和 Trace 脱敏。

以下内容不能再由关键词或宽泛正则直接决定最终业务结果：

- 用户意图、回复复杂度、是否走 Casual Fast Path；
- 新闻是否属于 AI 行业、新闻重要性和未知实体语义；
- 群聊表达、黑话含义、风格、冲突和是否成为正式记忆；
- 模型错误文本是否代表某个可重试或兼容状态。

语义任务通过统一 Task Runtime 调用模型，输出必须符合版本化 Schema。确定性规则可以提供信号、召回候选或执行安全兜底，但不能伪装成语义真相。

### 3.3 Registry 统一约束

所有新旧 Registry 最终统一满足：

- 稳定、带命名空间的 ID；
- 重复名称和未授权覆盖直接拒绝；
- 构建完成后冻结；
- 依赖图必须无环；
- Descriptor 声明 owner、domain、版本、生命周期和能力；
- 每次组合生成不可变 `generation`、内容 Hash 和只读快照；
- 新 generation 在旁路完整构建和校验后原子替换，不能边注册边对外可见；
- Admin 和 WebUI 只能读取同一快照，不维护第二份枚举；
- 兼容别名必须进入 `CompatibilityRegistry`，带替代项、起始版本和移除条件；
- 冻结、冲突、依赖、优先级和 generation 都必须有合同测试。

以上共享不变量由阶段 0B 的最小 Registry Kernel 先行提供。阶段 1 的
`ReleaseImpactRegistry`、`AdminTableViewDescriptorRegistry` 和阶段 2 的
`CompositionRoot` 必须直接复用该 Kernel，禁止先各自实现临时 Registry、再在
阶段 3 返工。阶段 3 只负责领域 Registry 适配和组合，不重新定义共享语义。

### 3.4 人工、模型与规则的权威顺序

对需要语义审核的学习链路，固定优先级为：

```text
人工明确审核 > 模型结构化审核 > 正则或统计信号
```

人工明确创建、编辑、合并、解决冲突后，不再要求模型二次审核。后台任务可以追加证据，但不能覆盖人工管理的正文或释义。

## 4. 当前代码基线

### 4.1 已由 `7023383` 落地并保留的内容

| 能力 | 当前证据 | 本计划处理 |
|---|---|---|
| P0 异步数据库边界 | `run_session_phase_async()`、聊天阶段拆分及相关测试 | 保留回归，不重做 |
| SQLite WAL 维护 | `core/sqlite_maintenance.py` | 纳入 Durable Job／Telemetry，但不改正确性语义 |
| Bridge 显式生命周期 | `core/agent_runtime/lifecycle.py`、`nanobot_kt/runtime_adapter.py` | 保留，并纳入 Composition Root |
| 入站恢复与幂等 | `inbound_message_claims`、私聊／群聊恢复路径 | 保留，不引入第二套 claim |
| Agent Runtime Port | `core/agent_runtime/contracts.py` | 作为 KT／未来 Pi Adapter 的稳定边界 |
| Model Provider Port | `core/model_provider/` | 保留；补的是 Route Descriptor，不重做 Provider |
| Memory Provider Registry | `core/memory_provider/registry.py` | 保留；统一 Registry generation 语义 |
| Prompt Section Descriptor | `core/prompt_v2/section_descriptors.py` | 作为 Prompt Contribution 的基础 |
| Task Contract Registry | `core/prompt_v2/task_contracts.py` | 作为统一 Task Runtime 的基础 |
| Tool Descriptor Registry | `core/tool_registry.py` | 作为 ToolRegistration／Profile 的基础 |
| Runtime Event Registry | `core/runtime/events.py`、`event_registry.py` | 保留；补齐 Telemetry 和 Hook／Policy 分离 |
| 类型化配置 | `core/settings_specs.py`、`core/config_registry.py` | 保留；清理仍游离的硬编码配置 |
| CQRS-lite 起点 | `core/db/` 与兼容 `core/database.py` façade | 按垂直切片继续迁移，不大爆炸搬家 |
| Compose 加固、原子发布与 CI | `docker-compose*.yml`、`core/release/`、`quality-gate.yml` | 保留合同和故障注入回归 |
| Legacy Prompt 隔离 | 已迁到 `docs/legacy-prompts/` | 保持只读文档，不恢复生产加载 |

### 4.2 当前仍明确存在的缺口

第 5 节 A01～A27 矩阵是状态和实施阶段的唯一事实源。本表只保存尚需生产证据或
后续兼容退役的缺口；已经完成的代码缺口不再重复列入。

| 关联 ID | 缺口 | 当前证据与剩余门禁 |
|---|---|---|
| A04、A26、G54～G57 | 旧身份、旧表和 legacy fallback 尚未完成生产退役 | 代码已收口到 canonical identity、只读墓碑和 `CompatibilityRegistry`；仍需生产迁移对账、连续 30 天零使用、至少 1 次完整发布、备份恢复与三方批准 |
| A07、A13、P01～P08 | 私聊结构化决策尚未生产激活 | 关键词最终决策已退出主链；当前 SLO 基线未达到 active 门禁，只允许 disabled／observation |
| A07、A13、N01～N10 | 新闻模型仲裁尚未生产激活 | 关键词只产信号，删除 Policy 已结构化；当前 SLO 为 baseline-only，只允许 disabled／observation |
| A07、A13、G01～G60 | 群学习尚未完成生产迁移和白名单观察 | 7A～7D、Web、Scheduler、游标、Evidence Policy 和正式 `GroupMemory` 写入代码已完成；生产 Feature 仍关闭、schedule 仍应为空 |
| A18 | Endpoint Contract／生成 Client 尚未覆盖全部历史端点 | 首批群学习、群记忆、工具、审计和 Runtime Module 已类型化；其余端点显式保留为 compatibility，继续按功能纵切 |
| A22、A23、A25 | 宿主特权验证与生产 Artifact 证据未形成 | Verification Plan、Artifact Manifest 和单盘 loopback 合同已完成；仍需在生产宿主执行真实 Docker、四服务同 digest、整体回滚、配额和水位验收 |

### 4.3 已完成但必须防回归的 Sandbox 决定

以下内容已经进入当前代码，不再作为待实现功能重复安排：

- Sandbox 授权唯一键是 canonical `chat_stream_id`。
- `sandbox_access_grants` 是唯一授权事实源；`private_superuser` 和通用 `ToolOverride` 不能绕过。
- project ID 和配额事实源已迁入数据库；宿主 TSV 只作历史迁移证据。
- Web 可以异步修改 Workspace 配额，并通过 generation fencing 等待实际应用。
- 生产脚本不再要求 `--owner-id` 或 `--project-id`。
- 多个 canonical session 可以在未来绑定同一 Workspace；实际“合并已有 Workspace 内容”另行设计。
- `loopback` 已是正式支持的单盘方案。明确接受风险并留下
  `single_disk_logical_rollback_only` 标记后，物理双盘冗余不是生产启用的硬阻断。
- 单盘 loopback 仍必须具备独立文件系统视图、project quota、容量和水位限制；“独立盘符”解决逻辑隔离与配额，不伪装成物理灾备。
- Sandbox 的 Docker Socket、AppArmor、seccomp、断网、非 root、只读根和真实 Smoke 门禁保持不变。

## 5. 二十七项架构决定总矩阵

| ID | 已确认决定 | 当前状态 | 实施位置 |
|---|---|---|---|
| A01 | 删除任意 SQL `/db/query`，只保留结构化表浏览和命名报告 | 已完成：结构化 View、字段脱敏、游标分页和 Web 入口均已切换 | 阶段 1 |
| A02 | 建立代码所有的 `ReleaseImpactRegistry` | 已完成：代码所有 Descriptor、稳定影响报告、Golden 和 CI 门禁均已落地 | 阶段 0B、阶段 1 |
| A03 | 4 个固定服务采用同一 Artifact 的原子发布与整体回滚 | 已完成：生产与本地入口均不可拆分切换，任一服务失败整体回滚 | 阶段 1 |
| A04 | 会话身份统一到 canonical `chat_stream_id` | 代码身份面、阶段 7 学习表和兼容投影已完成；生产回填与旧兼容物理退役仍受 30 天门禁约束 | 阶段 2、阶段 7、9 |
| A05 | 建立 `ModelRouteDescriptorRegistry` | 已完成：14 个既有 route 的元数据、继承、Setting、Capability、Task／Output Contract、Trace、生命周期和 SLO 状态已统一到冻结快照 | 阶段 3 |
| A06 | 建立统一 Task Runtime 与 `TaskContract` | 已完成：Task Contract、Route、Prompt、Resilience、Provider、Schema 校验、后置校验和类型化结果已统一执行 | 阶段 3 |
| A07 | 格式／安全规则保留；自然语言语义交给结构化 Task | 清单、Golden 与 3 条主链代码治理已完成；生产模型激活和效果观察仍受 SLO／白名单门禁约束 | 阶段 0、5、6、7、9 |
| A08 | 建立 `PromptContributionRegistry` | 已完成：代码所有 Descriptor、Renderer Port、确定性排序、预览解释和 Persona 单路径均已落地 | 阶段 3 |
| A09 | Registry 名称稳定、冲突拒绝、启动冻结、原子 generation | 共享 Kernel及六类既有领域 Registry 适配已完成；后续新增 Registry 继续复用同一 Kernel | 阶段 0B、阶段 3 |
| A10 | Event、Hook、Policy 严格分离 | 已完成：Observer、受信 Transform 与确定性 Policy 已使用独立合同、Registry 和失败语义 | 阶段 3 |
| A11 | 建立类型化配置面 | 本计划范围已完成：新闻资源、分析方面、Route、Feature、SLO 和 Web 默认值均读取类型化 Descriptor／Setting | 阶段 3、6、7、8 |
| A12 | 建立安全内容 Rule Engine | 已完成：安全子集、确定性动作合并、正则预算与 fail-open／fail-closed 合同已落地 | 阶段 4 |
| A13 | 所有模型输出采用 Schema-first LLM Output Contract | 本计划三条语义主链已完成；遗留兼容 parser 只能经 `CompatibilityRegistry` 计量并按生产门禁退役 | 阶段 3、4、5、6、7、9 |
| A14 | 建立 `ToolRegistration` 与 `ToolProfileDescriptor` | 已完成：登记、Profile、Schema、执行绑定、Prompt usage、Trace、KT 投影和退役 tombstone 已统一到冻结快照 | 阶段 3 |
| A15 | 建立 Typed Failure 与 `ResiliencePolicy` | 已完成：11 类失败、稳定错误码、重试、退避、熔断引用和终态策略已统一；逐 Task SLO、运行时预算和审计 Manifest 已接线 | 阶段 4 |
| A16 | 建立 Durable Job Kernel | 已完成：通用合同、领域 Lease Adapter、fencing 和恢复门禁已接入现有生产状态机 | 阶段 4 |
| A17 | 建立 CQRS-lite 数据库边界 | 已完成首批垂直切片：ORM 模型拆包、兼容 façade、群记忆与设置 Query／Command／Port／Adapter 及架构门禁已落地 | 阶段 4 |
| A18 | FastAPI 原生路由 + Endpoint Contract + OpenAPI Client | 已覆盖群学习、群记忆、工具、审计和 Runtime Module Diagnostics；其余端点显式标记为 `compatibility`，继续按功能迁移 | 阶段 4、8 |
| A19 | 建立类型化身份面 | 已完成：Foundation 身份类型、Sandbox Principal、GroupMemory 和应用消费者已统一 | 阶段 2 |
| A20 | 建立统一 Telemetry Contract | 已完成：RuntimeEvent、指标 Descriptor、HTTP／Task／Model／Prompt／Tool／Job／Delivery 关联、生产无正文账本和显式进程生命周期均已统一 | 阶段 4 |
| A21 | 建立 `ModuleManifest`、`ApplicationModule`、`CompositionRoot`；KT 仅经 Adapter | 已完成九模块组合、真实 lifespan、领域贡献 Registry、Runtime Diagnostics 和 KT 边界 CI 门禁 | 阶段 0B、阶段 2、3、8 |
| A22 | 建立 `VerificationSuiteDescriptor` 与 `VerificationPlan` | 已完成冻结 Suite Registry、跨 Registry 校验、确定性 Plan、Golden、CLI、CI 和 Runtime Diagnostics 投影；宿主特权 suite 仍须在生产执行 | 阶段 9 |
| A23 | 建立 `BuildProfile` 与 `ArtifactManifest` | 已完成清单合同与生成／校验入口；阶段 9 负责生产证据验收 | 阶段 1、9 |
| A24 | 建立静态 `WebFeatureManifest` 与 Web Composition Root | 已完成：首批功能由冻结 Manifest 组合导航和 lazy route，冲突、未知字段和远程插件入口 fail closed；群学习业务已移出 `App.jsx` | 阶段 8 |
| A25 | 正式支持 `single_disk_loopback`；物理冗余只作风险记录 | 已完成，保留回归门禁 | 阶段 9 |
| A26 | 建立 `FeatureLifecycleRegistry` 与 `CompatibilityRegistry` | 已完成：Feature 状态、启用门禁、兼容项、移除条件、tombstone 和用量观测已统一 | 阶段 3 |
| A27 | 只建立类型化 `MessageContract` 和薄协议 Adapter，不建重型 Channel Registry | 已完成：统一入站／出站合同、Web／OneBot／KT／Transport Adapter、Agent Runtime DTO 和架构门禁均已落地 | 阶段 2 |

## 6. 硬编码判断的统一分类

阶段 0 必须先生成完整清单。任何命中不能仅凭“用了正则”就判定为坏代码，而要按下表分类。

| 类别 | 示例 | 处理方式 |
|---|---|---|
| 协议／语法 | CQ 码、URL、ISO 日期、JSON、SQL token、HTTP header | 保留正则；增加边界与性能测试 |
| 安全不变量 | 路径穿越、Docker digest、Token、Host allowlist、输出脱敏 | 保留在代码；默认 fail closed |
| 数据一致性 | Hash、唯一键、租约、状态迁移、分页上限 | 保留为类型化 Policy／State Machine |
| 可配置业务策略 | 来源权重、时间窗口、配额、重试、优先级 | 迁入 Descriptor／SettingSpec／Policy |
| 自然语言语义 | 意图、复杂度、新闻相关性、黑话释义、风格 | 正则只产信号或候选；模型结构化判断 |
| 兼容分支 | 旧 route、旧 session 前缀、旧错误文本 | 进入 `CompatibilityRegistry`，带移除期限 |
| 展示内容 | Prompt、模板回复、Web 标签、错误文案 | 进入 Prompt Runtime／静态 Manifest／资源文件 |

初始扫描已经发现的主要热点及计划处置如下：

| 热点 | 主要文件 | 处置 |
|---|---|---|
| 私聊语义路由 | `core/private_timing.py`、`core/reply_templates.py` | 删除关键词最终决策，改为单次结构化分类 |
| AI 日报相关性与排序 | `news_search/search_backend.py`、`pipeline/rank.py`、`config.py` | 类型化请求、来源 Descriptor、评分信号和批量模型仲裁 |
| 表达／黑话学习 | `core/expression_learner.py`、`core/expression_memory.py` | 退役旧自动链，正则只写候选 |
| 群分析固定分支 | `group_analysis/analyzer.py` | `aspects` 驱动，只执行被选分支 |
| Route 与模型配置 | `core/route_metadata.py`、`clients/classifier_client.py` | `ModelRouteDescriptorRegistry` |
| 工具与 Profile | `core/tool_registry.py`、ToolPlan、Admin Tool API | `ToolRegistration` + `ToolProfileDescriptor` |
| Prompt 贡献与优先级 | Prompt Flow、Section Descriptor、Bridge 注入 | `PromptContributionRegistry` |
| 自由文本错误兼容 | 私聊分类、Provider fallback、部分任务 parser | Typed Failure + `CompatibilityRegistry` |
| 多套 lease／retry | session summary、memory digest、semantic、delivery、Sandbox admin operation | Durable Job Kernel |
| Web 路由／导航／DTO | `webui/src/App.jsx` 及手写 `api.get/post` | Web Feature Manifest + OpenAPI Client |
| Admin 任意 SQL | `api/admin/db_browser_routes.py`、Web SQL 编辑器 | 删除，换结构化浏览和命名报告 |
| URL／发布安全 | `core/proactive_research.py`、`core/web_search/url_policy.py` | 属于确定性安全规则，保留 |
| SQL 只读防护 | `core/sql_readonly.py` | 保留；只移除 Admin 自由 SQL 产品入口 |
| Prompt／LLM 清洗 | `foundation/llm/request_sanitizer.py`、`safe_diagnostics.py` | 保留安全清洗，语义接受条件改 Schema |
| Sandbox 文件与镜像校验 | `core/sandbox/`、`sandboxd/` | 完整保留，不因本计划放宽 |

阶段 0 的清单必须覆盖所有生产 Python、WebUI 和部署脚本；测试、生成构建物和
`vendor/` 单独统计，不混入待改业务清单。

## 7. 删除、迁移与保留总表

### 7.1 确定删除或退出生产路径

- `POST /api/v1/admin/db/query`、WebUI 任意 SQL 文本框及其自由 SQL DTO。
- `core/private_timing.py` 中直接判断自然语言语义的常量和 helper，详见阶段 5。
- `core/reply_templates.py::get_casual_reply(text, is_superuser)` 的文本二次分类。
- `api/chat_pre_bridge_decision.py` 依赖 `TypeError` 文本或旧函数签名的兼容分支。
- `clients/classifier_client.py::PrivateDecisionClassifier._parse_fallback` 的自由文本标签兼容。
- `core/expression_learner.py` 直接写 `ExpressionMemory/JargonMemory` 的自动学习路径。
- `core/expression_memory.py` 依据 `confidence >= 0.75` 自动激活的逻辑。
- `BAD_LEARN_TERMS` 手工重复清单，改由 Tool／Task／Prompt Registry 派生保留词。
- 学习路径中的 `group_%`、`qq:%:group` 模糊前缀判断。
- 新闻相关性关键词直接删除候选的分支。
- `search_backend.py` 写死 `2025|2026` 的日期判断。
- 同义 route key、provider alias、旧 session 前缀散落在调用方的兼容判断。
- Prompt 目录中的业务执行实现；最终只保留模板和使用说明，执行代码进入应用服务或 Adapter。

### 7.2 兼容期保留，稳定后再删除

- `ExpressionMemory`、`JargonMemory` 表：迁移后只读，一个完整发布周期后独立删除。
- `GroupMemory.group_id` 旧格式：新增 canonical `chat_stream_id` 后保留只读 façade。
- `core/database.py`：继续作为兼容导出层，按垂直切片缩小，禁止再新增业务实现。
- `core/route_metadata.py`：先改为 Registry façade，再在兼容窗口结束后删除字典接口。
- `creatures/.../group_analysis/tool.py`：先变为薄兼容 shim，再由 `nanobot_kt/tools/` Adapter 接管。
- 已声明的旧 setting、route、tool 和 Prompt key：只通过 `CompatibilityRegistry` 保留。

### 7.3 明确保留

- `TimingSignals`、`TimingDecision`、`decide_timing()` 和数值评分／Cooldown。
- URL、代码块、Blob、密钥形态识别。
- Mention、Reply、`directed_to_other`、发送者类型等结构化信号。
- Casual Fast Path 能力本身，但只接受结构化分类结果。
- 新闻日期、URL、RSS、HTML 和来源签名解析。
- 精确归一化、Hash、唯一约束和 evidence 外键校验。
- 群聊正则常态提取能力，但只能写原始候选。
- Sandbox 全部安全边界和运维门禁。
- `sql_analysis` 的受限只读分析工具；它与 Admin 任意 SQL 产品入口不是同一能力。

以下按阶段 0～9 实施。每个阶段都必须满足自己的验收条件，不能用后续阶段的计划替代当前阶段的证据。

## 阶段 0A：建立完整硬编码清单和行为基线

### 原始问题

当前只能通过 `rg` 看到大量正则、字符串集合和分支，无法区分安全规则、协议解析、业务策略、自然语言语义和临时兼容逻辑。如果直接机械替换，容易删除必要安全校验；如果只改已知热点，又会继续遗漏隐藏路由。

### 已确认决定

- 先审计全部生产代码，再改行为。
- 不把所有正则判为坏硬编码。
- 清单必须能说明 owner、用途、输入、输出、是否改变控制流、是否调用模型和最终处置。
- 阶段 0 只建立事实基线，不改变线上行为。

### 具体改动

1. 新增只读扫描器 `scripts/audit_decision_rules.py`，覆盖：
   - `core/`、`api/`、`app/`、`clients/`、`nanobot_kt/`、`creatures/`；
   - `bootstrap/`、`foundation/`、`sandboxd/`、`scripts/`；
   - `webui/src/`；
   - 排除 `tests/`、`vendor/`、`webui/dist/`、数据库和生成报告。
2. 扫描候选包括：
   - `re.compile/search/match/fullmatch/findall/finditer/sub`；
   - 字符串集合参与 `in`、`startswith`、`endswith` 和标签映射；
   - 自由文本异常、`TypeError` 文本、错误消息包含判断；
   - 分散 route/tool/task/provider key 集合；
   - 硬编码优先级、时间窗口、阈值、重试和服务列表；
   - WebUI 中重复路由、权限、导航、枚举和默认值。
3. 生成可审查的 `docs/architecture/decision-rule-inventory.json` 和 Markdown 摘要。每条至少包含：
   - `rule_id`；
   - 文件和行；
   - owner module；
   - 分类；
   - 是否影响最终控制流；
   - 输入边界；
   - 当前测试；
   - 处置：保留／配置化／Policy 化／模型化／兼容迁移／删除；
   - 关联计划阶段。
4. 人工复核所有“语义”与“兼容”候选。扫描器不能自动把某条规则改成模型调用。
   - 人工结论保存在
     `config/decision-rule-overrides.v1.json`，不得写回扫描器启发式；
   - 允许按路径、owner、检测器或原始分类批量复核，但必须同时固定预期数量和
     完整 rule ID 集合 SHA-256；
   - 批量集合新增、删除、替换或重叠时必须 fail closed，不能由宽泛 glob
     自动继承旧结论；
   - 单条复核结论优先于批量结论，并保留具体原因；
   - 扫描器自身和生成报告不进入清单，避免递归漂移。
5. 在修改任何生产行为前，先把当前关键行为固化为 characterization tests，
   尤其是：
   - Sandbox 安全规则；
   - URL、CQ、SQL、路径和 Prompt 权威校验；
   - 私聊、新闻、群学习现有行为；
   - route、tool、Prompt、setting 的当前快照。
6. 新增版本化 `docs/architecture/behavior-baseline.json`，记录：
   - baseline Git commit；
   - Python／KT／Prompt Runtime 版本；
   - fixture 和 golden snapshot 路径；
   - 每个 snapshot 的 SHA-256；
   - 分类：`preserve`／`known_bad`／`security_invariant`／`approved_change`；
   - 生成命令和环境约束。
7. Golden 冻结顺序固定为：
   - 在生产行为修改前生成 fixture；
   - 在 baseline commit 上生成 snapshot；
   - 独立复跑并确认 snapshot 确定性；
   - 后续 diff 默认必须为空；
   - 只有已批准的行为变更可以更新 `approved_change`，并保留旧／新差异证据。
8. 对 Admin `/db/query` 退役建立
   `docs/architecture/admin-db-query-migration-matrix.md`：
   - 盘点当前 WebUI 操作、测试场景和管理员确认的必要查询；
   - 映射到结构化 View 或命名报告；
   - 当前接口没有历史 SQL 审计，不能虚构 Top-N；
   - 如需短期观察，只记录规范化 query fingerprint、涉及表和计数，
     不记录完整 SQL、参数、结果或业务正文。
9. 采集语义 Task 基线：
   - 当前私聊规则短路、模型分类和完整 Agent 占比；
   - P50／P95／P99 延迟；
   - 模型调用次数、输入／输出 Token、失败率和 Schema 非法率；
   - 新闻每次运行候选数和模型调用数；
   - 群分析每 session 的消息数、Prompt 字符数、调用数和耗时；
   - 本地免费模型与计费 Provider 分开统计。
   - 聚合脚本固定为 `scripts/build_semantic_task_baseline.py`，报告写入
     `docs/architecture/semantic-task-performance-baseline.json`；
   - 只允许读取状态、延迟、Token／cost 数值、请求字节数和不透明 run
     分组，不读取或输出 Prompt、响应正文、身份、trace 或错误正文；
   - 当前 Schema 无法证明的指标必须写入 `observability_gaps`，不得从错误
     文本或不完整样本推断。

### 验收

- 扫描器在当前 HEAD 可重复运行，生成结果稳定。
- 每个生产代码命中项都有分类和 owner，不存在“未处理”占位。
- 确定性安全规则与自然语言语义规则能够分别统计。
- baseline fixture 和 golden snapshot 在任何行为修改前冻结，连续生成两次
  SHA-256 一致。
- `preserve` 和 `security_invariant` 的阶段 0A 前后 diff 为 0；
  `known_bad` 只记录事实，不被误当作永久正确行为。
- Admin 查询迁移矩阵中的每个必需场景都有明确替代项或经管理员确认退役。
- 语义 Task 基线报告具备调用量、延迟、Token、成本来源和失败率，不能只记录平均值。
- CI 初期只比较清单漂移并报告；待清单稳定后，新出现的未分类语义规则才转为阻断。

### 实施状态与证据（2026-07-23）

阶段 0A 已完成，未修改私聊、新闻、群学习、Sandbox 或 Prompt 的生产行为：

- 决策规则清单包含 5,247 条规则，其中 214 条已人工复核；
  `natural_language_semantic` 51 条、`compatibility` 99 条、
  `security_invariant` 700 条，清单连续生成稳定。
- 行为基线冻结 6 组 Golden；`preserve`、`security_invariant` 与
  `known_bad` 分类分开记录，Manifest 固定基线提交
  `35b8904eef449e8fd7beb7831685abf033249aa6`。
- Admin 任意 SQL 的现有入口、测试合同与结构化替代方案已写入迁移矩阵；由于当前
  没有查询历史审计，没有虚构生产 Top-N。
- 语义 Task 报告只读取生产 SQLite 的结构化聚合字段；无法从旧日志证明的
  Token、币种、Schema 非法率、候选数和完整端到端占比均记录为
  `observability_gaps`。
- `quality-gate.yml` 已执行清单漂移报告和 Golden 漂移阻断；清单在初期设置为
  `continue-on-error`，没有提前把全部启发式扫描差异升级成阻断。
- 定向回归：
  `tests/test_decision_rule_audit.py`、
  `tests/test_behavior_baseline.py`、
  `tests/test_semantic_task_baseline.py` 和 CI 合同测试合计 28 项通过。
- 脚本覆盖率：决策规则审计器 86%，行为基线生成器 80%，语义任务基线生成器
  87%。

## 阶段 0B：建立最小 Registry Kernel

### 原始问题

阶段 1 的 Release／Admin Registry 和阶段 2 的 Composition Root 都依赖冲突拒绝、
冻结、generation 和 snapshot。如果到阶段 3 才定义共享语义，前两个阶段会产生
临时实现和返工。

### 具体改动

新增轻量 `core/registry/`：

```text
core/registry/
├── __init__.py
├── contracts.py
├── builder.py
├── snapshot.py
└── validation.py
```

只提供跨领域不变量：

- `RegistryDescriptor` Protocol；
- `RegistryBuilder[T]`；
- `RegistrySnapshot[T]`；
- `RegistryGeneration`；
- 稳定 namespace／ID；
- 重复 ID 和未授权覆盖拒绝；
- 显式依赖拓扑和环检测；
- build 隔离；
- freeze；
- canonical JSON Hash；
- 不可变只读 snapshot；
- 新 generation 完整构建、验证后原子替换。

Kernel 不包含 Prompt、Tool、Model、Release 或 Admin 的业务字段，不提供动态目录扫描、
第三方代码加载和运行时同名覆盖。

### TDD 与验收

- 先写重复 ID、非法 ID、缺失依赖、依赖环、冻结后写入、Hash 确定性和原子替换失败的
  RED 测试，再实现最小 Kernel。
- 两次以不同注册顺序构建同一无序 Descriptor 集合时，snapshot Hash 一致。
- 构建新 generation 失败时，旧 snapshot 仍完整可读。
- Descriptor 对象和 snapshot 对外不可变。
- 阶段 1、2 不得再定义第二套 builder／generation／freeze。
- 现有领域 Registry 只在阶段 3 适配，不要求阶段 0B 一次性迁移。

### 实施状态与证据（2026-07-23）

阶段 0B 已完成，只新增共享 Kernel，没有迁移或改变现有领域 Registry：

- `core/registry/` 提供
  `RegistryDescriptor`、`RegistryBuilder`、`RegistrySnapshot` 和
  `RegistryGeneration` 四个公开概念。
- namespace／ID 使用稳定小写标识符；重复 ID、跨 namespace 注册、自依赖、
  缺失依赖和依赖环全部 fail closed，不存在运行时同名覆盖入口。
- Builder 成功冻结后不可写；Snapshot 的映射、顺序和 Descriptor 均不可变。
- canonical JSON 按 ID、依赖和 JSON object key 确定性排序，内容 Hash 不包含
  generation，因此相同内容以不同注册顺序和不同 generation 构建时 Hash 一致。
- Generation 在候选完整构建、拓扑验证和冻结后才以 compare-and-swap 发布；
  构建异常、验证失败和并发陈旧候选均不会替换上一代 Snapshot。
- `tests/test_registry_kernel.py` 共 20 项通过，`core.registry` 覆盖率 87%；
  Ruff、Python 3.11 编译、架构边界检查和 `git diff --check` 均通过。

## 阶段 1：Admin 数据访问、发布影响和原子 Artifact

### 1.1 删除 Admin 任意 SQL

#### 原始问题

`POST /api/v1/admin/db/query` 即使限制为 SELECT，仍让 UI 和后端围绕 SQL 文本形成产品契约。它会绕过字段脱敏、分页、稳定 DTO、表生命周期和未来 CQRS 边界。

#### 已确认决定

- 删除任意 SQL 产品入口。
- 保留结构化表浏览和命名报告。
- 保留内部迁移、健康检查和受限 `sql_analysis` 所需的 SQL。

#### 具体改动

1. 删除 `DbQuery`、`execute_readonly_query()` 路由和 Web SQL 编辑器。
2. 新增代码所有的 `AdminTableViewDescriptorRegistry`，每个可浏览对象声明：
   - 稳定 view ID；
   - 数据 owner；
   - 允许显示的字段；
   - 默认排序；
   - 可用过滤器；
   - 分页上限；
   - 脱敏策略；
   - 生命周期状态。
3. 表浏览 API 只接受 `view_id`、结构化 filter、cursor 和 limit，不接受 SQL 片段、列名表达式或排序表达式。
4. 跨表统计改为命名报告，例如：
   - 数据库概览；
   - 队列积压；
   - Sandbox 使用量；
   - 群学习运行趋势；
   - Prompt／Tool／Route Registry 快照。
5. Pydantic 响应模型明确单元格类型和脱敏状态。
6. `AdminTableViewDescriptorRegistry` 直接使用阶段 0B Registry Kernel，不定义
   私有 generation、freeze 或冲突语义。
7. 按阶段 0A 的查询迁移矩阵实现替代项；没有日志证据的场景不能以“Top-N 已覆盖”
   作为验收结论。

#### 验收

- 全仓不存在 `/db/query` 路由和 Web SQL 文本框。
- 未登记表、字段、filter 或 sort 均返回稳定错误码。
- 敏感字段不能通过 `SELECT *`、Join、别名或错误回显绕过。
- `admin-db-query-migration-matrix.md` 中所有“必须保留”场景都映射到已测试的
  View／Named Report；所有“退役”场景都有 owner 和理由。
- 未经映射的自由 SQL 使用量在连续观察窗口内为 0；只允许用 fingerprint 计数证明，
  不保存原始 SQL。

#### 实施状态与证据（2026-07-23）

阶段 1.1 已完成：

- 已删除 `POST /api/v1/admin/db/query`、`DbQuery`、任意只读 SQL
  parser／executor 和 Web SQL 编辑器。
- `core/admin/table_views.py` 使用阶段 0B Registry Kernel 声明结构化
  `AdminTableViewDescriptor`；API 只接受登记过的 `view_id`、等值 filter、
  opaque cursor 和 limit，Pydantic 对额外字段 fail closed。
- cursor 绑定 `view_id` 与规范化 filter Hash，不能跨视图或更换过滤条件重放。
- LLM 请求／响应／Header 原文、Sticker 宿主路径、设置密值和二进制内容均不会通过
  表浏览返回；长文本只返回有界预览。
- `docs/architecture/admin-db-query-migration-matrix.md` 已记录现有入口的退役或替代
  结论；没有把缺少生产查询日志冒充成 Top-N 使用证据。
- 后端合同和集成测试共 19 项通过；Web 合同测试、ESLint 和 Vite build 通过；
  阶段 0A 的安全 Golden 已以批准变更方式更新，其余五组 Golden Hash 未变化。

### 1.2 `ReleaseImpactRegistry`

#### 原始问题

当前改动影响哪些进程、镜像、Prompt、迁移和验证主要靠脚本约定与人工记忆。服务列表、构建范围和验证命令存在重复硬编码。

#### 已确认决定

- Registry 代码所有，不允许 Web 动态编辑。
- 它用于影响分析、构建计划和验证计划，不用于绕过 4 个固定服务的原子发布。
- 它直接复用阶段 0B Registry Kernel；本阶段不创建临时 Registry 基类。

#### Descriptor

每个 `ReleaseImpactDescriptor` 至少声明：

- `module_id`；
- 源文件 glob；
- 受影响的固定服务；
- 受影响的 Artifact；
- 必须执行的数据库迁移检查；
- Prompt Runtime 同步要求；
- Web build 要求；
- 真实 Docker／Sandbox 要求；
- 对应 `VerificationSuiteDescriptor`；
- owner 和生命周期。

#### 验收

- 任意 Git diff 都能生成稳定、可解释的影响报告。
- 固定的 diff fixture 具有 golden Verification Plan；相同 diff 连续生成两次的
  canonical JSON 和 Hash 完全一致。
- 新增未归属生产文件会被架构检查拒绝。
- 修改 Prompt 输入、工具输出或模板变量时，影响报告必须包含 Prompt Runtime 审计。
- 修改 Sandbox 安全代码时，不能只得到普通 pytest 计划，必须包含真实 Docker 验证要求。

#### 实施状态与证据（2026-07-23）

阶段 1.2 已完成：

- `core/release/impact.py` 定义代码所有、冻结的
  `ReleaseImpactDescriptorRegistry`，直接复用阶段 0B Registry Kernel；报告包含
  路径归属、固定服务、Artifact、迁移、Prompt Runtime、Web、真实 Docker 和验证
  suite 影响，以及 Registry generation／Hash 和报告 canonical Hash。
- Runtime、Prompt、数据库、Sandbox、Web、部署、运维、KT、测试、评测、文档、
  Sentinel 运行时资源和历史辅助工具均有明确 owner；新增未归属生产文件由
  `scripts/check_architecture.py` 阻断。
- `evals/` 和根目录历史测试可触发 `eval-gate`，但不会被误报为 Runtime Artifact；
  `sentinel/` 只影响实际挂载它的三个服务；大小写不同的本地 Agent 元数据目录不会
  被当成生产代码。
- `scripts/build_release_impact.py` 支持 Git diff、显式路径、strict 所有权检查和
  fixture Golden 写入／校验；固定 fixture 覆盖 Prompt、Sandbox、Web、Schema、
  文档、Eval、Sentinel 和根工程配置。
- `quality-gate.yml` 已加入非宽容的 Release Impact Golden 检查。
- `tests/test_release_impact.py` 共 38 项通过；`core.release` 与构建脚本联合语句
  覆盖率 96%；Golden 检查、架构所有权检查均通过。

### 1.3 `BuildProfile`、`ArtifactManifest` 与原子发布

#### 原始问题

当前生产已要求 digest，但缺少统一 Artifact Manifest；`deploy-production.sh` readiness 失败时没有整体恢复 4 个固定服务，本地构建默认还可能只重建 server。

#### 已确认决定

- `nanobot-server`、`session-summary-worker`、`outbound-delivery-worker`、
  `semantic-index-worker` 必须使用同一 Runtime Artifact。
- 发布要么 4 个服务全部切换并通过健康检查，要么全部恢复前一 Artifact。
- 不用 `ReleaseImpactRegistry` 跳过其中某个固定服务。

#### 具体改动

1. 定义 `BuildProfile`：
   - `nanobot-runtime`；
   - `nanobot-sandbox-python`；
   - `sandboxd`；
   - `webui`。
2. 构建生成 `ArtifactManifest`，至少记录：
   - Git 完整提交和 dirty 状态；
   - KT 固定提交；
   - Python lock Hash；
   - Web lock Hash；
   - canonical Prompt 默认集 Hash；
   - schema migration head；
   - OCI image digest；
   - SBOM／依赖清单位置；
   - 已执行验证 suite 和结果 Hash；
   - 构建时间与 builder 版本。
3. 生成版本化 `ReleaseManifest`，引用本次全部 Artifact。
4. 部署先保存前一 Release Manifest，再切换 4 个服务。
5. readiness、worker health、Runtime revision 和数据库迁移任一失败时，整体恢复前一 manifest。
6. 回滚后再次检查 4 个服务 revision 一致，不能只恢复 server。
7. 默认只保留当前和最近一个已验证回滚 Artifact，不做全局 prune。

#### 验收

- 通过故障注入证明第 2、3、4 个服务任一失败都会整体回滚。
- 4 个容器的 OCI revision、image digest 和 Runtime revision 完全一致。
- 发布日志不含 Token、业务正文或完整环境变量。
- 部署和回滚不触碰非 Nanobot 容器。

#### 实施状态与证据（2026-07-23）

阶段 1.3 已完成代码实现，未执行真实生产部署：

- `core/release/artifacts.py` 通过共享 Registry Kernel 冻结四个
  `BuildProfile`；`ArtifactManifest` 对正式 Runtime 强制记录 clean Git、
  KT commit、Python／Web／Prompt 输入 Hash、Schema migration head、OCI digest
  与 Image ID、SBOM／依赖清单位置、验证 suite／结果 Hash、构建时间和 builder
  版本。未知字段、可变 tag、缺失输入和 Manifest Hash 篡改均 fail closed。
- `ReleaseManifest` 将四个固定服务全部绑定到同一个 `nanobot-runtime` Artifact；
  `current`／`pending`／`rollback` 状态采用同目录原子替换，并能收敛部署进程中断后
  留下的 pending。
- `scripts/build_release_manifest.py` 提供 Artifact 生成、Release 组合和目标校验；
  目录 Hash 路径敏感且拒绝符号链接，Manifest 不记录 Token、业务正文或宿主绝对
  路径。
- `scripts/deploy-production.sh` 现在强制要求 digest 与匹配的
  `NANOBOT_RELEASE_MANIFEST`，实际编排由 `scripts/deploy_release.py` 和
  `core/release/deployment.py` 完成；四服务镜像引用、Image ID、OCI revision、
  Docker health、主服务 readiness 和数据库迁移 Head 必须全部通过。
- 第 2、3、4 个固定服务分别注入 unhealthy 时，测试均证明目标切换失败后会以旧
  Artifact 一次重建并复核全部四个服务；混合 revision 在任何变更前即被拒绝。
- 首次接管旧浮动 tag 时只把实际 Image ID 固定为受限 rollback 证据，不允许 observed
  Manifest 作为新发布目标；目标已运行时会补齐正式 built Manifest 而不重复重建。
- 成功发布后只保留 current 与 rollback 对应本地镜像；更旧镜像仅在没有任何容器引用
  时按精确 Image ID 删除，不调用全局清理，也不触碰非 Nanobot 容器。
- 本地 `scripts/docker-build.sh` 也改为一次重建四个固定服务并使用 Compose
  `--wait`；任一服务失败时以部署前镜像整体恢复。
- Release／部署／既有 Docker 合同定向组合共 100 项通过；新增 Release 实现联合语句
  覆盖率 83%；Ruff、Python 编译、Shell 语法、Release Impact Golden、架构边界和
  `git diff --check` 均通过。

## 阶段 2：模块、身份和消息边界

### 2.1 `ModuleManifest`、`ApplicationModule` 与 `CompositionRoot`

#### 原始问题

当前已有多个局部 Registry 和启动函数，但模块依赖、注册内容、启动顺序、健康状态和停止顺序仍散落在 `bootstrap/`、`bridge.py` 和各模块全局变量中。

#### 已确认决定

- 采用显式模块化单体，不使用动态第三方插件加载。
- KT 只能经 `nanobot_kt` Adapter 接入。
- 不一次性移动全部文件；先建立合同，再按垂直切片迁移。

#### 模块合同

`ModuleManifest` 至少包含：

- 稳定 `module_id` 和版本；
- owner、domain、lifecycle；
- 必需模块依赖和可选依赖；
- 提供的 Port／能力；
- Prompt、Tool、Task、Model Route、Event、Setting、Job、Endpoint 和 Web Feature 贡献；
- startup／shutdown 阶段；
- health／readiness 检查；
- feature flag；
- compatibility aliases；
- release impact。

`ApplicationModule` 只暴露：

- `manifest()`；
- `register(builder)`；
- `start(runtime_context)`；
- `stop()`；
- `health()`。

`CompositionRoot` 执行：

1. 收集内建模块；
2. 校验 ID、版本、依赖和循环；
3. 在隔离 builder 中注册全部 Descriptor；
4. 生成同一 generation；
5. 冻结 Registry；
6. 按拓扑顺序启动；
7. 启动失败时反向停止已启动模块；
8. 正常关停时反向停止；
9. 关停完成后 fail closed，不允许 getter 惰性复活。

Builder、generation、freeze 和 snapshot 必须来自阶段 0B Registry Kernel；
`CompositionRoot` 只负责编排 Module 和领域 Descriptor，不复制 Kernel。

#### 首批模块

- `runtime.agent`；
- `model.provider`；
- `prompt.runtime`；
- `tool.runtime`；
- `memory.runtime`；
- `delivery.outbound`；
- `group.memory`；
- `sandbox.control_plane`；
- `admin.api`。

后续模块化按业务纵切，不以“把 `core/` 全部搬进子目录”为完成标准。

#### 验收

- 依赖环、重复能力和部分启动都在启动期失败。
- Module 停止顺序与启动顺序严格相反。
- `core/`、`app/` 不导入 KT；私有 KT 字段访问只存在于 Adapter。
- `core/database.py` 和 `nanobot_kt/bridge.py` 的新增代码门禁为负增长或只允许 façade。
- 扩展现有 `scripts/check_architecture.py`，使用 AST 扫描整个 `core/` 和 `app/`：
  - 禁止静态和函数内动态导入 `nanobot_kt`、`kohakuterrarium`；
  - 唯一框架实现访问范围是显式 Adapter 目录；
  - 当前存量导入逐个迁移，禁止新增基线债务；
  - 正向和负向 fixture 都进入 `quality-gate.yml`；
  - 不新增与现有 AST 检查重叠的 import-linter 第二事实源。

#### 实施状态（2026-07-23）

阶段 2.1 已完成：

- `core/modules/` 已提供冻结的 `ModuleManifest`、`ApplicationModule`、
  `CompositionRoot`、共享 generation 和反向停止语义；
- `bootstrap/application_modules.py` 已声明固定九个内建模块，所有启动依赖由
  `bootstrap/lifespan.py` 在每次进程启动时显式注入，现有测试 monkeypatch 接缝
  保持可用；
- FastAPI lifespan 已改由 Composition Root 启动和停止，运行期保存
  `app.state.composition_root`，关停后清空且不能惰性复活；
- 模块内部的数据库维护、检索、主动运行时、Sandbox runner、Agent session 和
  Bridge 均有部分启动反向清理；启动取消也会清理已启动模块并继续传播取消；
- `core/agent_runtime/gateway.py` 作为 Composition Root 管理的窄 Port，承接共享
  Gateway、隔离任务 Gateway 和研究 Runtime 工厂；未绑定、重复绑定和关停后读取
  均 fail closed；
- `core/runtime_health.py`、`core/daily_digest.py`、`core/proactive_research.py` 和
  `app/group_ingress/service.py` 已移除 KT／Kohaku 直接导入；
- 研究预算守卫已经与 KT Plugin 基类分离，KT 私有字段及
  `PluginBlockError` 映射只存在于 `nanobot_kt/research_runtime.py` Adapter；
- `scripts/check_architecture.py` 已扫描整个 `core/`、`app/`，同时覆盖静态和
  函数内 import；正反 fixture 已进入现有测试与质量门禁；
- `core/database.py` 固定为不超过 1123 行，`nanobot_kt/bridge.py` 固定为不超过
  2759 行，新增逻辑必须进入所属模块或 Adapter；
- 阶段 2.1 合并定向验证共 290 项，结果为 290 passed、0 failed、0 skipped；
  Ruff、Python 编译、Release Impact Golden、架构边界和 `git diff --check`
  同时通过。

### 2.2 类型化身份面

#### 原始问题

`ChatStreamIdentity`、`RuntimePrincipal`、Sandbox grant、GroupMemory `group_id` 和旧
`group_%` 前缀仍不是同一身份合同。

#### 已确认决定

- canonical `chat_stream_id` 是会话级配置、学习白名单和 Sandbox grant 的唯一键。
- `user_id`、`group_id`、平台 ID 只作为身份字段或展示值，不能替代 canonical key。
- 不同 session 是否属于同一人、是否共享 Workspace，是显式关系，不靠字符串推断。

#### 具体改动

1. 在 `foundation/identity/` 定义：
   - `PlatformId`；
   - `ExternalSessionId`；
   - `ChatStreamIdentity`；
   - `Principal`；
   - `ActorIdentity`；
   - `RecipientIdentity`。
2. `core.chat_stream_identity` 变为兼容 façade。
3. 所有新表使用 canonical `chat_stream_id`。
4. `GroupMemory` 增加 canonical 字段并回填。canonical `chat_stream_id` 是唯一事实源；
   旧 `group_id` 只是同事务生成的兼容投影，不能独立修改。
5. 旧 `group_<id>` 和裸 ID 只由 `CompatibilityRegistry` 的入口 Adapter 解析一次。
6. 禁止业务查询继续使用 `LIKE 'group_%'` 或 `LIKE 'qq:%:group'` 识别群聊。
7. 为未来 Workspace 共享保留显式多 grant → 一 workspace 关系；本轮不做文件内容合并。
8. 回填和过渡规则：
   - 新写必须先验证 canonical ID；
   - Repository 在同一数据库事务内生成 legacy projection；
   - 读取优先 canonical，只对尚未回填的旧行执行一次 legacy fallback；
   - 两字段冲突时 fail closed 并进入迁移报告，不猜测 owner；
   - canonical 唯一约束生效后才允许统计 legacy fallback 使用量；
   - fallback 连续归零并满足兼容删除门禁后，旧字段才可独立删除。

#### 验收

- 生产业务模块不再按字符串前缀推断 chat type。
- 相同外部 ID 在不同 platform／chat type 下不会碰撞。
- 身份解析错误返回稳定错误码，不回显敏感原始字段。
- Sandbox、会话配置、群学习和 Prompt 注入对同一请求得到相同 canonical ID。
- 并发新写、分批回填和重启恢复不会产生 canonical／legacy 投影分叉。
- 人工构造字段冲突会 fail closed，且不会跨 session 读取或覆盖记忆。

#### 实施状态（2026-07-24）

阶段 2.2 已完成核心身份面：

- `foundation/identity/` 已定义并统一导出 `PlatformId`、
  `ExternalSessionId`、`ChatStreamIdentity`、`Principal`、
  `ActorIdentity` 和 `RecipientIdentity`；`core.chat_stream_identity`
  已收缩为兼容 façade；
- canonical ID 对 platform、外部会话 ID 和 chat type 做无歧义编码；裸 ID
  只有在调用方显式给出 platform 和 chat type 时才允许解析，非法编码、控制字符和
  代理码点均 fail closed；
- Sandbox 已删除本地 `Principal` 双源，统一使用 Foundation 身份合同；
- `GroupMemory` 已增加 canonical `chat_stream_id`，新写在同一事务内生成兼容投影；
  QQ 历史别名只用于尚未回填行的精确 fallback，非 QQ 不认领歧义旧行；
- 幂等迁移 `20260723_group_memory_canonical_identity` 会先验证全部历史行，再回填
  canonical 字段并建立唯一约束；投影冲突和 alias 合并冲突均拒绝迁移，不自动猜测或
  覆盖；
- 群记忆提取、检索、注入、Admin API、会话发现、日报、清理、评测采样和
  Prompt Runtime 上下文适配均已切换到类型化身份解析；
- Admin Session Memory Browser 已删除重复的 SQL `CASE`／`LIKE` 身份推断，SQL
  只负责聚合，Compatibility Adapter 负责严格归一；
- `scripts/check_architecture.py` 已扫描整个 `core/`、`app/`，禁止新增
  `group_`／`qq:` 前缀方法调用、ORM `LIKE` 和原始 SQL `LIKE` 身份推断，并包含
  正反 fixture；
- 阶段 2.2 最终组合验证为 364 passed、0 failed；身份与 GroupMemory 定向覆盖率
  88%；Ruff、Python 编译、架构边界、Release Impact Golden 和
  `git diff --check` 均通过；
- A19 至此完成。A04 只完成核心身份面；入口协议兼容收口、阶段 7 学习表迁移以及
  legacy fallback 归零后的字段退役仍按后续阶段实施，不在本阶段虚报完成。

### 2.3 类型化 `MessageContract`

#### 原始问题

OneBot、Web、KT、SSE、群入口和推送出口各自使用 dict／meta 字段，字段优先级和兼容行为难以验证。

#### 已确认决定

- 建立一个稳定 Message Contract。
- OneBot 和 Web 只做薄协议适配。
- 不建立动态 Channel Registry。

#### 合同范围

入站合同至少包含：

- `message_id`；
- canonical `chat_stream_id`；
- platform、chat type；
- actor／recipient／principal；
- 文本和类型化 content parts；
- attachments；
- mention、reply、引用消息；
- 受信 gateway metadata；
- request trace／idempotency 信息。

出站合同至少包含：

- action：reply／no_reply／wait／silent／blocked；
- recipient；
- text／image／file／asset parts；
- progress 与 final 语义；
- retract policy；
- transport-neutral error。

OneBot／Web Adapter 负责协议转换，KT Adapter 负责把合同转换为 Agent Runtime DTO。业务层不拼 CQ 码，不直接缓存 SSE 字符串。

#### 验收

- 同一业务结果可以分别渲染为 `/chat` JSON、SSE done、群入口响应和 QQ push。
- 协议 Adapter 的差异不进入核心 Policy。
- 未知 content part 和非法 metadata fail closed。
- 不新增 `ChannelRegistry`、动态 channel 插件或同名覆盖机制。

#### 实施状态（2026-07-24）

阶段 2.3 已完成：

- `foundation/message_contract/` 已定义框架无关的入站／出站合同，覆盖 canonical
  `chat_stream_id`、actor／recipient／principal、类型化内容段、附件、mention、
  reply、gateway metadata、trace／idempotency、reply／no-reply／wait／silent／
  blocked、progress／final、retract policy 和 transport-neutral error；
- Web `/chat` 与 OneBot／NapCat 群入口只负责协议事实转换；canonical 会话身份优先，
  `client_meta` 不能覆盖 chat type，未知 OneBot segment、控制字符 metadata 和
  身份冲突均在业务服务前以稳定 400 fail closed；
- `core.agent_runtime.message_gateway` 已作为业务调用 Port；生产
  `NanobotBridge`／`NanobotBridgePool` 只经 `MessageContractBridgeMixin` 接收
  类型化消息，KT Adapter 会生成 `AgentTurnRequest`、`RequestRuntimeContext` 和
  `RuntimePrincipal`，并以 canonical `chat_stream_id` 作为 Runtime session；
- 受信 MessageContract 会覆盖 KT 兼容 metadata 中的 platform、chat type、
  group、actor、principal、recipient、message 和 trace 字段；旧
  `handle_message(...)` 分支仅保留给尚未迁移的测试替身和兼容 Adapter；
- `/chat` JSON、SSE done、群入口、断连 QQ push、定时任务和主动外呼均已通过
  `OutboundMessageContract` 渲染；HTML 类型、既有 payload 字段、幂等哈希和
  outbox 重放合同保持不变，业务层没有新增 CQ 拼接；
- `scripts/check_architecture.py` 已在现有 AST 门禁中增加 MessageContract 专属检查：
  生产 Bridge 必须安装 typed mixin，且禁止新增 `ChannelRegistry`、
  `ChannelPluginRegistry` 或 `DynamicChannelRegistry`；`core/`、`app/` 不导入
  KT 的原门禁继续作为单一依赖方向事实源，没有增加重复检查器；
- Prompt Runtime 已核验：本阶段未改变 `enriched_query` 标记、历史结构、模板变量
  或工具输出合同，因此 canonical 与 runtime 模板无需修改；相关合同测试
  124 passed；
- 阶段完整定向组合为 443 passed、0 failed；定时／主动出站专项为
  82 passed、0 failed；MessageContract 联合覆盖率为 80%；Ruff、Python 编译、
  架构边界、Release Impact Golden 和 `git diff --check` 均通过；
  `nanobot_kt/bridge.py` 保持 2759 行，`core/database.py` 保持 1120 行。

A27 至此完成。本阶段没有建立 Channel Registry，也没有修改 KT vendor。

## 阶段 3：统一 Descriptor、Registry 和 Task Runtime

### 3.1 领域 Registry 适配

阶段 0B 已提供最小 Registry Kernel。本阶段不再定义第二套共享合同，只将现有
Model Provider、Memory Provider、Tool、Prompt Section、Task 和 Runtime Event
Registry 逐个适配到同一 builder、snapshot、generation、freeze 和 Hash 语义。

各 Registry 仍保留自己的 Descriptor 类型和领域校验；共享 Kernel 不能反向导入
任何领域模块。

#### 实施状态（2026-07-24）

阶段 3.1 已完成：

- Model Provider、Memory Provider、Tool、Prompt Section、Task Contract 和
  Runtime Event 六类 Registry 已接入同一个 `RegistryBuilder`／
  `RegistrySnapshot`；
- 六类 Descriptor 均提供稳定 namespace、ID、dependencies 和 canonical payload，
  统一获得 generation、freeze、规范化 JSON 与 SHA-256 语义；
- Memory Provider 原有 priority／dependency 执行顺序、工具所有权与领域错误合同
  保持不变；Task Contract 通过不可变适配 Descriptor 进入 Kernel，没有把兼容 API
  中可变的 `output_schema` 直接放入共享快照；
- Registry 资源 ID 支持 `tasks/private_decision` 一类受限路径，但 namespace 仍不
  允许 `/`，`../escape` 等路径穿越形式继续 fail closed；
- 新增领域适配测试经历 5 项 RED 后转绿，阶段 3.1 与既有 Registry 组合为
  105 passed、0 failed；Ruff 与 Python 编译通过。

### 3.2 `PromptContributionRegistry`

#### 原始问题

Prompt Section 已有 authority／trust，但“哪个模块贡献什么、何时插入、谁覆盖谁、失败怎么办”仍由 Flow、Bridge 和调用顺序共同决定。Persona 也仍可能通过 bridge meta 与注入服务形成双路径。

#### Descriptor

`PromptContributionDescriptor` 至少包含：

- `contribution_id`；
- owner module 和 domain；
- phase；
- priority；
- before／after dependencies；
- authority 和 trust；
- source precedence；
- applicable platform／chat type；
- required variables；
- editable；
- failure policy；
- multiplicity：singleton／many；
- renderer Port；
- sensitive trace policy。

#### 优先级规则

1. 显式 before／after 依赖是硬约束，必须先满足，循环或缺失依赖直接失败；
2. 对当前依赖已满足的候选先按 phase；
3. 再按数字 priority；
4. 同 phase、同 priority 且彼此无依赖的 singleton 冲突直接失败；
5. ID 只作为 `many` contribution 的稳定最终排序键，不用于掩盖 singleton 冲突；
6. 不以 import 顺序或 dict 插入顺序决定安全优先级。

依赖必须优先于 phase 是对现有可见合同的兼容约束：例如
`session_guidance.phase=policy`，但它显式依赖 identity，不能因为 phase 排序被提前到
身份事实之前。phase 与 priority 只在当前依赖已满足的候选中比较。

#### 具体迁移

- canonical chat Prompt；
- platform／chat type policy；
- runtime context；
- session guidance；
- persona；
- history；
- group memory；
- Tool usage；
- current user event。

Persona 只保留一个 Contribution Provider。数据检索可以复用现有服务，但不能同时把同一画像塞入 `bridge_meta` 和 `enriched_query`。

#### 验收

- Admin 预览能显示 contribution ID、来源、priority、依赖、authority、active source 和 generation。
- 任一 Prompt 构建都能解释最终顺序。
- 用户正文无法伪造高权威 section。
- 修改 contribution 输入时，canonical default 和必要 runtime 模板同步测试通过。

#### 实施状态（2026-07-24）

阶段 3.2 已完成：

- 新增 `core/prompt_v2/contribution_registry.py`，Contribution Descriptor 覆盖
  owner／domain、phase、priority、before／after、authority／trust、适用平台与
  chat type、required variables、multiplicity、Renderer Port、failure policy 和
  sensitive trace policy；
- Registry 复用阶段 0B Kernel 的冲突、依赖、冻结、generation、canonical JSON 和
  SHA-256，没有建立第二套共享 Registry 实现；
- Flow 只选择 active branch；代码侧 Contribution Registry 负责安全元数据和最终
  排序。依赖是硬约束，依赖满足后的候选按 phase、priority 和稳定 ID 排序；
  singleton 同 phase／priority 且无依赖时 fail closed；
- canonical 五个在线 platform／chat type 分支的最终 Contribution 顺序与改造前
  Flow 顺序完全一致；未知扩展只能成为低权威 `untrusted_data`、`many`
  contribution，Flow 不能覆盖 priority、multiplicity 或 renderer；
- 编译器已通过 `PromptContributionRendererPort` 渲染 template／runtime
  contribution，并验证声明的输入变量；Admin effective preview 随
  `flow_sections`／debug 暴露 contribution ID、owner、domain、phase、priority、
  before／after、authority／trust、active source、generation 和 Registry Hash；
- Persona 正文在线链路只由 `persona_reference` Contribution 注入一次；
  `enriched_query` 仍只包装当前用户输入，Bridge 没有第二条画像正文拼接路径；
- 新增 12 项 Contribution 专项测试，Prompt／Flow／Admin／审计组合回归为
  259 passed、0 failed；Contribution Registry 覆盖率 90%，与编译器联合覆盖率
  84%；Ruff、Python 编译、架构边界、Release Impact Golden 和
  `git diff --check` 均通过；
- 本阶段只增加编排元数据和 Renderer Port，没有改变模板变量、正文标记、历史结构
  或工具输出合同，因此 canonical default 与 `data/prompts_v2/` 运行时模板无需
  修改。

### 3.3 `ToolRegistration` 与 `ToolProfileDescriptor`

#### 原始问题

`ToolDescriptorRegistry` 已经统一部分元数据，但工具实现加载、wire schema、Prompt usage、默认开关、Profile 和旧兼容工具仍有多个事实源。

#### 具体改动

1. `ToolRegistration` 绑定：
   - Descriptor；
   - schema provider；
   - execution Port；
   - Prompt contribution；
   - trace／redaction policy；
   - lifecycle；
   - feature lifecycle。
2. `ToolProfileDescriptor` 只声明 profile ID 和工具能力集合，不复制工具 schema。
3. ToolPlan 从冻结 Registry + Capability Policy + session override 生成。
4. Admin 工具页、wire schema、Prompt usage 和测试读取同一 snapshot。
5. 已退休工具通过 `CompatibilityRegistry` 返回稳定 tombstone，不能重新执行。
6. 群分析实现迁入应用服务；`nanobot_kt/tools/group_analysis.py` 为薄 Adapter，Prompt 目录只保留模板和 usage。

#### 验收

- 新增工具只登记一次即可出现在 ToolPlan、Admin、Prompt 审计和 wire schema。
- wire schema 与 KT 实际发送内容逐字节一致。
- Profile 不得提高用户没有的权限。
- 退休 bash／read／write／edit／grep／glob 和 `python_sandbox` 继续 fail closed。

#### 实施状态（2026-07-24）

阶段 3.3 已完成：

- 新增 `core/tool_registration.py`，以冻结的 `ToolRegistrationRegistry`
  一次绑定 `ToolDescriptor`、Schema Provider、execution Port、Prompt template
  keys、Trace policy、feature lifecycle 和 active／retired／framework
  生命周期；
- 新增冻结的 `ToolProfileDescriptor` Registry，统一 `full`、`none`、
  `lightweight` 和 `research` 四类 Profile 及兼容别名；Profile 只能收窄
  Capability，不能扩大 session 已获权限；
- ToolPlan、Admin、wire schema、Prompt usage 和 KT 加载投影均读取同一
  Registration generation／SHA-256；缺 Schema、缺执行绑定、出现未登记 KT
  工具或丢失 active 工具时启动 fail closed；
- creature YAML 只保留声明输入，生产启动由
  `nanobot_kt/tool_registration_adapter.py` 投影冻结快照；`python_sandbox`
  已从 YAML 移除，bash／read／write／edit／grep／glob／memory_read／
  memory_write 作为无执行绑定的 retired tombstone 保留；
- `group_analysis` 的应用实现已迁入 `app/group_analysis/`，
  `nanobot_kt/tools/group_analysis.py` 只保留 KT 薄 Adapter，Prompt 目录不再
  承载执行代码；旧群号、legacy session ID 和 canonical stream ID 通过类型化
  `ChatStreamIdentity` 解析，不再由业务代码按字符串前缀推断；
- 当前阶段组合回归 210 passed、0 failed；身份迁移专项 40 passed、0 failed；
  Ruff、Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；
- 本阶段未改变 `enriched_query`、历史结构、模板变量、工具输入／输出合同或
  usage 正文，canonical default 与 `data/prompts_v2/` 运行时模板无需修改。

### 3.4 `ModelRouteDescriptorRegistry`

#### 原始问题

Provider Registry 已完成，但 `ROUTE_METADATA`、`_MODEL_SETTING_KEYS`、
`_REPLY_INHERITED_ROUTE_KEYS`、Admin 枚举和调用方 fallback 仍重复维护 route 语义。

#### Descriptor

每个 Model Route 声明：

- route key、label、domain、owner；
- required provider capabilities；
- 默认模型／候选策略；
- SettingSpec key；
- 是否继承其他 route；
- timeout、max tokens、thinking policy；
- P50／P95／P99 latency budget；
- 单请求调用数、日调用量、Token 和成本预算；
- fallback route；
- circuit breaker policy；
- Task Contract；
- output contract；
- trace policy；
- lifecycle。

#### 具体迁移

- `reply`、`fast`、`smart`；
- `timing_gate`、`timing_proactive`、`private_decision`；
- outreach 相关 route；
- `news_daily_quality`；
- `sticker_describe`；
- `session_summary`、`memory_digest`。

`35b8904` 已把日报质量模型接入 `news_daily_quality` route，本阶段只把 route 元数据和调用契约收敛，不重复改调用结果。

#### 验收

- `core/route_metadata.py` 只剩兼容查询 façade。
- 调用方不再维护 route key 集合。
- Route 继承、fallback、capability 和 setting provenance 可由 Admin 查看。
- 未登记 route 不能静默回退到 `reply`。
- `private_decision`、新闻和群学习 route 都经同一健康候选、成本上限和熔断
  Contract；调用方不能绕过 Route Descriptor 直接创建 Provider。

#### 实施状态（2026-07-24）

阶段 3.4 的 Registry 收敛已完成：

- 新增 `core/model_provider/route_registry.py`，14 个现有业务 route 均通过
  `ModelRouteDescriptorRegistry` 接入共享 Registry Kernel，获得冻结、
  generation、规范化 JSON、SHA-256、依赖排序和显式 alias 语义；
- 每个 Descriptor 已声明 owner／domain、Provider Capability、候选策略、
  Setting prefix／model key、继承与 model-only fallback、timeout／temperature／
  max tokens／thinking、Circuit Breaker policy、Task／Output Contract、Trace
  policy、生命周期、执行模式及 `baseline_only` SLO；没有把历史性能样本伪装成
  已批准预算；
- `core/route_metadata.py` 只剩 Registry 兼容投影与 Provider URL 辅助函数；
  `clients/classifier_client.py` 已删除 `_MODEL_SETTING_KEYS`、
  `_MODEL_FALLBACK_SETTING_KEYS` 和 `_REPLY_INHERITED_ROUTE_KEYS`，Prompt task
  key、Trace source、继承及 Provider capability 均读取 Descriptor；
- Config Registry 从 Descriptor 生成 reply 子 route 的默认 SettingSpec，并补齐
  5 个此前可写但未类型化登记的 provider key；启动期会核对所有 route 的
  SettingSpec 默认值和 Task／Output Contract，漂移即 fail closed；
- Admin route 枚举、编辑、测试、可选模型和 resolved 诊断均使用同一快照，并暴露
  generation／Hash、setting provenance、inheritance、fallback、capability、
  Task／Output Contract、Trace、lifecycle 与 SLO 状态；`vision` 仅作为显式
  alias；
- 未登记 route 在 Request、解析器和 Admin 均返回稳定失败，不再静默落到
  `local_llama` 或 `reply`；架构门禁禁止生产代码重新读取 `ROUTE_METADATA`
  或定义三类旧 route 映射；
- 本阶段定向组合回归 281 passed、0 failed；Registry 专项覆盖率 82%；Ruff、
  Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；
- 本阶段只收敛配置与调用契约，没有改变 Prompt 模板变量、消息结构或模型输出
  Schema，因此 canonical default 与 runtime Prompt 模板无需修改。

统一健康候选、实际成本预算执行和 Typed Failure／重试终态由紧随其后的阶段 3.5
`TaskRuntime` 接入；阶段 7 新增群学习审核 route 时必须登记到本 Registry，不能
恢复直接创建 Provider 的旧路径。

### 3.5 统一 Task Runtime 与 Schema-first Output

#### 原始问题

当前 `TaskContractRegistry` 主要验证模板变量和 parser owner。模型调用、route、超时、重试、Schema 校验、错误和 Telemetry 仍由每个调用方自写。

#### Task Runtime 输入

`TaskInvocation` 至少包含：

- task ID／contract version；
- route key；
- 类型化输入 DTO；
- Prompt contribution／template refs；
- request context；
- idempotency key；
- timeout budget；
- trace context。

#### Task Runtime 输出

`TaskResult[T]` 至少包含：

- parsed value；
- contract version；
- model route／provider／model；
- attempt count；
- latency；
- typed failure；
- raw output Hash 和字节数；
- validation diagnostics；
- run ID。

原始 Prompt 和完整模型输出默认不进入普通 Trace。

#### 执行顺序

1. 验证 Task Contract；
2. 渲染 Prompt；
3. 解析 Model Route；
4. 应用 Resilience Policy；
5. 调用 Provider；
6. 按 JSON Schema／类型解析；
7. 执行业务后置校验；
8. 生成 TaskResult 和 Telemetry。

#### 首批迁移

- `private_decision`；
- `news_daily_quality`；
- `group_memory_learning`；
- `group_analysis_topics/titles/quotes/quality`；
- session summary；
- memory digest。

#### 验收

- 首批任务不再自行从文本中截取 JSON 或解释自由文本标签。
- Schema 非法、字段越界和业务 evidence 非法分别返回不同 Typed Failure。
- Prompt Runtime 模板、Task Contract、Model Route 和输出 Schema 可从同一 Admin 页面关联查看。

#### 实施状态（当前工作树）

阶段 3.5 已完成：

- 新增 `core/task_runtime/`，统一执行 Task Contract、Route Descriptor、
  Prompt 渲染、Resilience Policy、Provider Port、JSON Schema、业务后置校验、
  Typed Failure 和 `task.execute` 元数据事件；普通事件只记录输入／输出 Hash、
  字节数、模型、尝试次数、延迟和失败码，不记录 Prompt 或模型正文；
- `private_decision` 已删除“否／等待／是,复杂度”等自由文本兼容解析；日报质量、
  群分析四分支、Session Summary 和 Memory Digest 均经同一 Runtime，
  调用方不再自行截取 JSON 或调用 `json_repair()`；
- 预登记 `group_memory_learning_v1` 的严格 Schema、Task Contract、Route
  Descriptor、失败终态和 canonical／runtime Prompt；本阶段只建立合同，不启用
  候选 Writer，实际审核与写入仍按阶段 7B／7C 门禁切换；
- 新闻 `source_ids` 和群话题 `evidence_log_ids` 已增加确定性 scope 后置校验；
  Schema 非法、字段越界、证据越界、Provider 不可用、超时和输出容量分别返回
  稳定失败码；群分析使用 `GroupAnalysisTaskError` 携带字段，不解析异常文本；
- `RouteTaskModelAdapter` 按 Descriptor 的 `execution_mode` 选择 route completion
  或 chat completion；Task Runtime 纳入主服务和独立 Session Summary Worker
  的显式启停，停止后不会隐式重建；
- Admin 模型路由页现在关联展示 Task owner、失败策略、Output Contract 和完整
  JSON Schema，不再只能看到孤立的 route ID；
- 阶段组合回归 408 passed、0 failed；新增 Task／Registry／Admin 专项
  91 passed、0 failed；Ruff、Python 编译、Web lint／build、架构边界、
  Decision Rule Audit、Behavior Golden、Release Impact Golden 和
  `git diff --check` 均通过；
- 本阶段未启用 Sandbox、群学习 Writer 或任何生产 Feature，未修改 KT vendor。

### 3.6 Event、Hook 与 Policy 分离

#### 定义

- Event：已经发生的不可变事实，只读通知，不能修改返回值。
- Observer Hook：只读观察 Event，默认 fail open，不改变控制流。
- Transform Hook：显式声明可变输入／输出和执行阶段，只能由受信内建模块注册。
- Policy：同步、确定性的决策接口，返回类型化结果或 Typed Failure。

#### 约束

- Observer 不能返回替代业务结果。
- Transform Hook 不能绕过 ToolPlan、身份、Trace 脱敏或 Sandbox 安全策略。
- 安全 Policy 默认 fail closed。
- 业务可用性 Observer 默认 fail open。
- 不用字符串 `"block"`、异常 message 或 `None` 表达多个不同状态。

#### 验收

- 每个 Hook Descriptor 声明 kind、输入／输出 Contract、priority 和 failure policy。
- Event Sink 故障不改变业务结果。
- Policy 决策具有纯函数测试；Hook 顺序具有组合测试。

#### 实施状态（当前工作树）

阶段 3.6 已完成：

- 新增 `core/runtime/extensions.py`，分别定义不可混用的 Observer、
  Transform 与 Policy 合同；Descriptor 明确声明 kind、owner、domain、
  输入／输出 Contract、priority、failure policy、受信来源和受保护不变量，
  并复用统一 Registry Kernel 完成启动期注册与冻结；
- Runtime Event 已通过显式 `RuntimeObserverDispatcher` 投递；
  `runtime.logging` 是 `runtime.agent` 所有的 fail-open Observer。
  Observer 返回非空替代值会形成类型化合同失败，Observer 异常只记录异常类型，
  不保存异常正文，也不改变已经形成的业务 Event；
- Prompt Contribution 继续以现有 Contribution Registry 作为唯一排序事实源，
  没有新增第二套 Prompt 排序器；每个 Contribution 已声明受信 Transform
  Contract，Renderer 只接收顶层只读上下文并且必须返回
  `PromptContributionRenderResult`。身份、ToolPlan、Trace 脱敏和 Sandbox
  安全边界作为受保护不变量显式登记；
- Sandbox Access 已登记为安全 Policy，任何未处理的数据库或 Repository 异常
  都经类型化失败收敛为拒绝；Timing Model Mode 已改为 `TimingModelMode`
  枚举和类型化 `TimingModelPolicy`，调用方不再比较任意字符串或通过
  `getattr(..., "enabled")` 隐式表达状态，配置读取异常按可用性 Policy
  显式 fail open；
- Composition Root 已登记 `runtime.logging` Observer、
  `prompt.contribution` Transform、`sandbox.access` Policy 和
  `timing.model_mode` Policy 的模块所有权；没有增加目录扫描、动态导入、
  第三方插件加载或同名覆盖；
- RED 测试先确认 13 项合同缺失和 1 项模块贡献缺失，GREEN 后阶段组合回归
  240 passed、0 failed；新增横切模块联合覆盖率 85%，其中
  `core/runtime/extensions.py` 为 88%；Ruff、Python 编译、架构边界、
  Decision Rule Audit、Behavior Golden、Release Impact Golden 和
  `git diff --check` 均通过；
- 本阶段没有改变 Prompt 正文、模板变量、历史结构或工具输出合同，没有启用
  Sandbox、群学习或其他生产 Feature，也没有修改 KT vendor。

### 3.7 类型化配置、Feature 与 Compatibility 生命周期

#### `FeatureLifecycleRegistry`

状态固定为：

- experimental；
- hidden；
- preview；
- stable；
- deprecated；
- retired。

Descriptor 声明 owner、默认开关、支持 scope、数据迁移、回滚行为、启用门禁和移除条件。

#### `CompatibilityRegistry`

每条兼容项声明：

- alias ID；
- kind：setting／route／tool／endpoint／identity／schema；
- canonical replacement；
- introduced version；
- warning policy；
- removal gate；
- tombstone behavior；
- owner 和测试。

#### 配置收敛

- 所有可配置阈值进入 SettingSpec 或领域 Policy Descriptor。
- 安全不变量不能被数据库或 Web 降低。
- 环境、数据库、默认值的 precedence 只定义一次并保留 provenance。
- WebUI 不维护后端默认值副本。

#### 验收

- 旧 alias 的使用量可观测。
- 没有 owner 或移除条件的临时兼容分支不能进入主分支。
- retired feature 无法通过旧 setting、ToolOverride 或 URL 重新启用。

#### 实施状态（当前工作树）

阶段 3.7 已完成：

- 新增 `core/lifecycle/`，以共享 Registry Kernel 建立冻结的
  `FeatureLifecycleRegistry` 与 `CompatibilityRegistry`；Feature 状态固定为
  `experimental/hidden/preview/stable/deprecated/retired`，兼容类别固定为
  `setting/route/tool/endpoint/identity/schema`；
- Sandbox Feature 明确声明基础设施、sandboxd、AppArmor、固定镜像、
  Workspace 配额和显式 session grant 六类启用门禁，默认关闭且不支持群 scope；
  bash／read／write／edit／grep／glob／python_sandbox／memory_read／
  memory_write 均绑定 retired Feature 与无执行绑定的拒绝型 tombstone，任何
  scope 或 gate 都不能重新启用；
- Compatibility Descriptor 统一声明替代项、起始版本、owner、测试、告警策略、
  tombstone 行为和移除门禁；移除门禁固定为连续 30 天零使用、至少跨过 1 次完整
  发布、迁移对账、回滚演练、备份恢复演练及三方批准；
- 旧 `daily_digest.*` Setting 投影、`vision` Route alias、
  `classifier_legacy` 保留执行、`group_*/private_*` 身份入口以及
  `/render`、`/group_timing` 旧端点均接入同一 Compatibility Registry；
  `classifier_legacy` 只记录使用量而不错误转发到 `private_decision`；
- 兼容用量只聚合 compatibility ID、kind、count 和时间，不保存原始 alias、
  session ID、URL query 或正文；每次使用发出白名单化的
  `compatibility.alias_used` Runtime Event；
- Setting precedence 保持
  `database → environment → legacy_database → legacy_environment → default`，
  legacy provenance 新增 `compatibility_id`；身份兼容判断只允许存在于
  `core/chat_stream_identity.py` Adapter，架构门禁继续拒绝其他业务模块按前缀
  推断会话类型；
- `/api/v1/render` 与 `/api/v1/group_timing` 保持原业务结果，同时返回
  `Deprecation: true` 和由 Descriptor 生成的 successor `Link`；
- RED 先确认 11 项生命周期合同缺失，GREEN 后扩展为 13 项专项测试；
  设置、路由、身份、工具、旧 API、Runtime Event、Composition Root 和架构门禁
  组合回归为 137 passed、0 failed；生命周期包通过 Coverage.py 实测 91%；
  Ruff、Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；
- 本阶段没有改变 Prompt 正文、模板变量、历史结构、工具 wire schema 或输出
  合同，因此 canonical default 与 `data/prompts_v2/` 运行时模板无需修改；没有
  启用 Sandbox、群学习或其他生产 Feature，也没有修改 KT vendor。

## 阶段 4：运行时工程基础

### 4.1 安全内容 Rule Engine

统一当前 `ContentBlockRule`、`UserBlockRule`、`no_learn` 和代码侧安全规则的执行合同，但不允许任意规则提升权限。

`ContentRuleDescriptor` 至少包含：

- rule ID／版本／owner；
- scope；
- match kind；
- action：block／no_reply／no_context／no_learn／redact／signal；
- priority；
- input max length；
- match max count；
- failure policy；
- audit policy；
- 正反例和性能预算。

Web 可管理的规则必须经过安全子集校验。学习规则第一版只读，不允许 Web 保存任意正则，避免 ReDoS 和行为漂移。

#### 实施状态（当前工作树）

阶段 4.1 已完成：

- 新增 `core/content_rules/`，建立框架无关的
  `ContentRuleDescriptor`、`ContentRuleRegistry` 与
  `ContentRuleEngine`；规则按 priority 和 rule ID 确定性执行，结果只合并
  Descriptor 已声明的动作；
- 固定 `contains/exact/regex/identity` 四种匹配及
  `block/no_reply/no_context/no_learn/redact/signal` 六种动作，统一声明输入、
  命中数、执行时间、失败策略和审计策略边界；
- regex 使用带 timeout 的执行引擎并受输入长度、命中数和性能预算三重限制；
  失败明确执行 `fail_open` 或 `fail_closed`，Registry payload 只记录模式哈希
  和长度，不保存规则正文；
- `ContentBlockRule` 与 `UserBlockRule` 通过显式 Adapter 接入统一 Engine，
  保留既有数据库模型和对外结果 shape；多条内容规则按稳定顺序合并动作；
- Web 只允许创建 `contains/exact`、`global/session` 以及
  `no_reply/no_context/no_learn` 安全子集，session 必须使用 canonical
  `chat_stream_id`；历史 regex 只可关闭或删除，不能编辑、重新启用或新建；
- Composition Root 新增 `content_rule` 贡献类型，由 `runtime.agent` 模块唯一
  拥有 `content.rules`，没有引入目录扫描、动态 import 或同名覆盖；
- RED 首先得到 7 failed、1 passed，GREEN 后 Rule Engine 专项为 8 passed；
  内容规则、UserBlock、Admin API、群消息幂等和行为基线组合回归为
  109 passed、0 failed，扩大覆盖回归为 82 passed、0 failed；
  `core.content_rules` 通过 Coverage.py 实测 88%；
- 新增的 23 个确定性决策点已逐项人工复核：资源上限、NUL 和控制字符约束归为
  安全不变量，枚举和状态判断归为数据合同，旧 regex 单向关闭归为兼容迁移；
  Decision Rule Inventory 已由生成器更新；
- Ruff、Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；本阶段没有改变 Prompt
  Runtime 输入、模板变量、工具 wire schema 或模型输出合同。

### 4.2 Typed Failure 与 `ResiliencePolicy`

统一失败分类：

- validation；
- authorization；
- unavailable；
- timeout；
- rate_limited；
- transient_transport；
- contract_violation；
- conflict；
- quota；
- cancelled；
- permanent。

每个失败包含稳定 code、retryable、safe summary、cause type 和 trace ref，不包含完整 Prompt、正文、Token 或宿主路径。

`ResiliencePolicy` 声明：

- 最大 attempts；
- 总 timeout budget；
- 单次 timeout；
- backoff／jitter；
- 可重试 failure code；
- fallback route；
- circuit breaker；
- terminal action。

禁止由异常 message 或 HTTP body 文本决定重试。

首批语义 Task 的 terminal action 固定如下；具体 timeout、阈值和预算必须引用
版本化 SLO Descriptor，不能散落在调用方：

| Task | 同步尝试策略 | 熔断／失败终态 | 禁止行为 |
|---|---|---|---|
| `private_decision` | 单次请求最多 1 个 Task run；不在请求内重复做语义分类 | 进入正常 Agent Runtime | 不靠关键词或数值规则猜 casual／no_reply／wait |
| `news_relevance_review` | 每批最多 1 次 relevance review | 保留候选并执行保守数值降权 | 不清空日报，不把关键词当最终删除依据 |
| `news_daily_quality` | 按 route 预算重试；失败时使用确定性 digest | 返回有来源的降级摘要 | 不伪造模型质量结论 |
| `group_memory_learning` | 当前 run 失败后由 Durable Job 异步重试 | 候选保持 `pending_model_review`，不激活、不注入 | 不推进成功审核游标，不恢复旧自动激活 |
| `group_analysis_*` | 分支级隔离 | 其他报告分支继续，失败分支记录 Typed Failure | 不生成冒充模型结果的长期记忆 |

#### 实施状态（当前工作树）

阶段 4.2 已完成：

- 新增 `core/resilience/`，冻结
  `validation/authorization/unavailable/timeout/rate_limited/`
  `transient_transport/contract_violation/conflict/quota/cancelled/`
  `permanent` 共 11 类框架无关失败分类；
- 扩展 `TaskFailureCode` 并建立 code 到 category 的完整确定性映射；
  `TaskTypedFailure` 现同时包含稳定 code、category、retryable、safe summary、
  cause type、trace ref 和 terminal action，显式传入不一致 category 会拒绝；
- `ResiliencePolicyDescriptor` 现声明语义版本、owner、最大 attempts、总 timeout、
  单次 timeout、指数 backoff、jitter、可重试 category/code、fallback route、
  circuit breaker 引用、SLO 引用和 terminal action；构造期校验所有数值关系，
  并通过共享 Registry Kernel 构造后冻结；
- `TaskRuntime` 的总预算同时受 invocation 与 Policy 约束，单次请求使用剩余预算
  与 per-attempt 上限的较小值；重试和退避只读取类型化
  code/category/retryable，不读取异常 message 或 HTTP body；
- Provider HTTP 401/403、429、5xx 和其他 4xx 分别归类为
  `authorization_failed`、`rate_limited`、`transient_transport` 和
  `permanent_failure`；错误摘要不保存响应正文；
- `private_decision` 固定单次失败后进入正常 Agent Runtime；
  `news_daily_quality` 只对类型化瞬态失败按 route 预算重试，最终使用确定性
  digest；`group_memory_learning` 固定单次失败后
  `preserve_pending`，把后续重试交给阶段 4.3 Durable Job；
  `news_relevance_review` 预登记单次 `conservative_downrank` 策略；
  `group_analysis_*` 保持分支失败隔离；
- Composition Root 由 `runtime.agent` 唯一拥有 `task.resilience` Policy
  contribution；没有引入异常字符串路由、动态发现或第三方覆盖；
- RED 为 8 failed；HTTP 分类扩展 RED 为 4 failed；Composition Root
  所有权 RED 为 1 failed。GREEN 后 Typed Failure／Task Runtime 专项为
  29 passed，相关调用方和模块组合回归为 127 passed、0 failed；
  `core.resilience` 与 `core.task_runtime` 通过 Coverage.py 实测总覆盖率 87%；
- 新增的 13 个决策点已逐项人工复核，timeout/backoff/jitter 数值关系归为数据
  合同、控制字符归为安全不变量、HTTP 状态码归为协议语法；Ruff、Python 编译、
  架构边界、Decision Rule Audit、Behavior Golden、Release Impact Golden 和
  `git diff --check` 均通过；
- 阶段 4.7 已把目标 Policy 切换为按 invocation 解析
  `task_slo.by_invocation.v1`；未登记 SLO、超输入／输出预算和引用不一致均
  fail closed。是否允许阶段 5～7 从观察切为生效，仍由逐 Task Manifest 的
  样本、预算与观测完备性共同决定，不能只看 Descriptor 已冻结。

### 4.3 Durable Job Kernel

#### 原始问题

session summary、memory digest、semantic index、outbound、proactive、Sandbox admin operation 和未来群学习各自实现 claim、lease、retry 和恢复。

#### Kernel 合同

- `JobDescriptor`；
- `JobRecord`；
- `JobLease`；
- `JobHandler`；
- `JobRepositoryPort`；
- `JobResult`；
- `JobSchedulePolicy`；
- `JobRetryPolicy`。

状态至少为：

```text
pending → running → succeeded
                  ↘ retry_wait
                  ↘ failed
                  ↘ cancelled
```

所有 settle 操作同时校验 job ID、owner token、generation 和 running 状态。

#### 迁移顺序

1. 新群学习任务直接使用 Kernel；
2. session summary；
3. memory digest；
4. semantic index；
5. Sandbox admin operation；
6. 其他调度任务；
7. outbound 的成熟专用状态机通过 Port 对接，不强制降级成最小通用状态。

#### 验收

- 崩溃、超时、重复 worker 和旧 owner settle 都有并发测试。
- 失败重试不会重复外部副作用。
- 无任务的轮询不产生高频写放大。
- 每个 Job 都能关联 trace/run/task/tool ID。

#### 实施状态（当前工作树）

阶段 4.3 已完成 Kernel 合同和现有生产状态机的安全接入：

- 新增 `core/jobs/`，冻结框架无关的 `JobDescriptor`、`JobRecord`、
  `JobLease`、`JobCorrelation`、`JobFailure`、调度／重试 Policy、
  `JobRepositoryPort`、`JobHandlerPort` 与参考 Kernel 实现；
- 没有创建第二张通用 Job 表。Session Summary、Memory Digest、
  Semantic Index、Sandbox Admin Operation 和 Outbound 仍以各自领域表作为唯一
  事实源，业务写入与 Job 终态继续由领域状态机在同一事务中原子结算；
- 为 `session_summary_jobs` 增加独立幂等迁移
  `20260723_session_summary_job_fencing`，补齐 `lease_token`、
  `lease_expires_at`、`generation`、`attempt_count`、`finished_at`；
  历史 `running` 记录安全退回 `pending`，不会伪造有效租约；
- Session Summary 的 claim／heartbeat／preflight／finalize／fail 已统一传递
  不可变 `SessionSummaryJobLease`，并同时校验 job、worker、owner token、
  generation、attempt、running 状态和租约有效期；
- Memory Digest、Semantic Index 和 Sandbox Admin Operation 的续租、回收和
  结算同样补齐 worker、token、attempt／generation、source revision 与有效期
  fencing；旧执行者即使复用相同 worker 或 token 也不能结算新一代任务；
- Outbound 保留已成熟的专用状态机，不被强制降级为最小通用
  `JobResult`，避免破坏投递副作用和 outbox 终态的原子关系；
- 新增显式 `JobLeaseAdapterRegistry`，只把五个现有生产 claim／lease 投影为
  统一 `JobLease`，不复制领域数据、不动态扫描、不动态 import；
- Composition Root 由 `memory.runtime` 唯一拥有
  `runtime.job_kernel` contribution，启动时构建和校验 Adapter Registry，
  停止或启动失败时清理 `app.state.job_lease_adapters`；
- `JobCorrelation` 合同已经覆盖 request／session／turn／trace／run／task／
  tool／delivery／parent job；现有生产 Job 的统一持久化和跨链路观测由阶段
  4.6 完成，本阶段不虚报全部 correlation 已落库；
- RED 覆盖旧 owner、同 owner/token 不同 attempt、超时回收和历史迁移；
  GREEN 后组合回归为 250 passed、0 failed；`core/jobs` 使用 Coverage.py
  实测总覆盖率 89%；
- Ruff、Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；新增 token、
  generation、attempt 和 source revision 判断已人工归类为安全或数据一致性
  不变量；
- `group_memory_learning` 仍为 `reserved`，本阶段未启用群学习，也未改变生产
  Feature 开关。

### 4.4 CQRS-lite 数据库边界

按子域建立 Query Service、Command Service、Repository Port 和 Unit of Work：

- 查询返回不可变 DTO，不返回 ORM 对象；
- Command 明确事务边界；
- 外部 await 前结束同步事务；
- API 不直接拼跨域 ORM 查询；
- Admin 命名报告使用 Query Service；
- 兼容 `core/database.py` 只重导出模型和 Session。

优先迁移：

1. group learning／group memory；
2. model route／setting 查询；
3. admin browser／report；
4. session summary／memory digest jobs；
5. 剩余高频 import 的模型。

不以一次性拆空 `database.py` 为目标，也不为形式纯洁复制查询。

#### 实施状态（当前工作树）

阶段 4.4 已完成首批 CQRS-lite 垂直切片，并保留现有领域状态机：

- `core/database.py` 已从 1120 行、33 个 ORM Model 收敛为 172 行兼容 façade；
  Engine、Session、SQLite WAL／busy timeout、事务释放和初始化迁入
  `core/db/session.py`，ORM Model 按群记忆、知识、管理、观测、评测、语义等
  子域拆入 `core/db/models/`；
- 新增框架无关的 `group_memory_contracts.py` 与 `settings_contracts.py`，
  Query 返回冻结 DTO；SQLAlchemy 映射集中在显式 Adapter，不把 ORM 对象泄漏
  给 API 或应用服务；
- 群体记忆新增 Query／Command Service，管理员路由、提取、注入和检索调用方
  已迁移；修改、去重、注入计数和 injection mode 具有明确 commit／rollback
  边界；
- 群分析读取继续由 `app/group_analysis/repository.py` 作为显式 SQL Adapter
  承担；应用编排改用 `UnitOfWork`，在 `analyze_group()` 的外部 await 前释放
  只读事务，不再由应用服务直接构造 `SessionLocal`；
- SystemSetting 新增不可变 DTO、Repository Adapter 和 Query／Command
  Service；管理员模型路由的批量写入现在一次原子提交，失败统一 rollback，
  运行时 `SettingsService` 只经 Repository Port 读取；
- Admin Browser 已有 `AdminTableViewService` 命名 Query Service，因此未复制
  查询；Session Summary 与 Memory Digest 的 Job／Command 状态机继续作为领域
  持久化 Adapter 保留，避免为了形式纯洁破坏租约和终态原子性；
- 架构检查现显式登记纯数据库合同、已迁移消费者和 SQL Adapter；纯合同禁止
  依赖 SQLAlchemy／core 实现层，Adapter 禁止反向依赖 API、KT 或外部交付层；
- RED 从数据库专项 11 个失败和架构登记 1 个失败开始；GREEN 后数据库专项
  113 passed、设置链 221 passed、群记忆／群分析链 91 passed，去重后的阶段组合
  回归为 418 passed、0 failed；新增合同、Adapter 与 Service 使用 Coverage.py
  实测总覆盖率 87%；
- Ruff、Python 编译、架构边界、Decision Rule Audit、Behavior Golden、
  Release Impact Golden 和 `git diff --check` 均通过；新增的 canonical 身份
  匹配、最新注入记录选择、设置键长度和依赖方向门禁已人工归类为数据一致性或
  安全不变量；
- 本阶段未启用群学习、Sandbox 或其他生产 Feature，未改变 Prompt Runtime
  输入、模板变量、工具 wire schema，也未修改 KT vendor。

### 4.5 Endpoint Contract 与 OpenAPI Client

- FastAPI 继续使用原生 `APIRouter` 和 Pydantic。
- 每个外部／Admin endpoint 必须有稳定 `operation_id`、Request、Response、Error Schema 和分页合同。
- CI 导出规范化 OpenAPI，并生成 Web TypeScript Client。
- Web Feature 只能经生成 Client 或明确的薄 wrapper 调用 API。
- 手写 endpoint 字符串和重复 DTO 逐功能迁移，先从群体记忆和 Registry 管理页面开始。

#### 实施状态（当前工作树）

阶段 4.5 已完成 Endpoint Contract 基础和首批 Admin 垂直切片，其余端点没有
虚报为强类型完成：

- 新增 `api/endpoint_contracts.py`，统一稳定 `operationId`、成功／错误
  Response Schema、分页投影、合同生命周期以及 Endpoint Registry generation
  和 SHA-256 元数据；未迁移端点明确标记为 `compatibility`；
- 新增冻结的 `api/admin/endpoint_registry.py`，使用共享 Registry Kernel
  登记 12 个群记忆、工具管理和审计端点；owner 与现有
  `ApplicationModule` 对齐，由 Composition Root 的 `admin.api` 模块唯一拥有；
- 首批端点已补齐 Pydantic Request／Response、稳定错误 Schema 和显式
  `operation_id`；旧群记忆兼容端点继续可用并标记 `deprecated`；
- 已退役的 Prompt 多方法 410 tombstone 保留运行时兼容行为，但不再进入
  OpenAPI，避免为生成客户端制造重复 operation ID；
- 新增 `scripts/generate_openapi_client.py`，生成
  `docs/api/openapi.v1.json` 和
  `webui/src/api/generated/adminClient.ts`；`--check` 已接入
  `quality-gate.yml`，漂移会阻断 CI；
- Web 群记忆页和工具管理页已迁移到生成 Client，不再手写本批端点 URL 和
  DTO；后续端点仍按 Feature 垂直迁移，不一次性改写全部 API；
- TDD RED 最初为 4 个合同缺失失败，并补充了重复 operation ID、
  Composition Root 所有权和 Registry generation/hash 失败；GREEN 后
  Endpoint／Admin／Application Module 组合测试为 109 passed、0 failed；
- `api.endpoint_contracts`、`api.admin.endpoint_registry` 和生成器使用
  Coverage.py 实测总覆盖率 83%，三个文件分别为 84%、87%、82%；
- 规范化 OpenAPI、生成客户端、Web lint/build、Ruff、Python 编译、架构
  边界、Decision Rule Audit、Behavior Golden、Release Impact Golden 和
  `git diff --check` 均通过；Vite 现有约 797 KiB 主包告警属于阶段 8 的拆分
  范围，不作为本阶段阻断；
- 公开路由字面量、Schema 投影语法和生成客户端资源已按集合哈希完成人工
  归类；生成客户端不得人工编辑。

### 4.6 Telemetry Contract

在现有 RuntimeEvent 上补齐：

- correlation：request／session／turn／task／job／tool／delivery；
- Counter、Histogram、Gauge 的稳定名称和 label allowlist；
- Registry generation、module version 和 artifact revision；
- Typed Failure code；
- Job lease／retry／settlement；
- Prompt／Task／Model Route／Tool 关联；
- 内容 Hash、字节数和截断状态，不记录正文。

Telemetry 只观察事实，不改变业务控制流。

#### 实施状态（当前工作树）

阶段 4.6 已完成统一合同、生产持久化和关联链路，没有把 Telemetry 变成业务
控制面：

- 新增 `core.telemetry` 合同与冻结 Registry，统一 11 个 correlation 字段、
  Registry generation／SHA-256、模块版本和 Artifact revision，并登记 Counter、
  Histogram、Gauge 的稳定名称、单位、bucket 与低基数 label 白名单；
- `RuntimeEvent` 已覆盖 HTTP、Prompt、Task、Model Route、Tool、Memory、
  Durable Job 和 Delivery；事件只保存稳定 ID、类型化失败码、Hash、字节数、
  截断状态与耗时，不保存 Prompt、命令、文件正文、模型完整输出、stdout／
  stderr、密钥或 fencing token；
- 新增 `runtime_telemetry_events` 迁移和 SQLAlchemy Sink；写入使用独立短事务、
  `event_id` 幂等、冲突安全重查和二次敏感字段过滤；迁移连续运行两次后版本只
  记录一次，三个组合索引均已验证；
- HTTP Middleware 只记录规范化 route pattern、method、status 和 latency，
  接受合法 `X-Request-ID` 或生成服务端 ID，并在响应回写；Task、Model、
  Prompt、Tool、Bridge 和 Delivery 保留上游 request／session／turn／task／
  job 关联，不再用局部空 Context 覆盖父链；
- 既有五类 ORM Job 通过事务 `after_commit` Observer 发出 enqueued、claim、
  renew、retry 和 settle；回滚不发事件；通用 `DurableJobKernel` 同样发出
  claim／retry／settle，Telemetry 故障保持 fail-open；
- `runtime.telemetry` 已作为显式 `ApplicationModule` 接入 Composition Root；
  Server 与三个独立 Worker 均显式启动有界非阻塞缓冲 Sink、停止时 flush、
  卸载 Job Observer 并恢复日志 Sink；队列满或持久化失败只累计丢弃数，不阻塞
  业务线程；
- TDD RED 覆盖合同缺失、关联丢失、Kernel 未接线、事务回滚和 Worker 生命周期；
  GREEN 后阶段 4.6 跨模块组合回归为 434 passed、0 failed；专项覆盖率运行
  130 passed，Telemetry／RuntimeEvent／HTTP Middleware 联合覆盖率为 86%；
- Ruff、Python 编译、架构边界、OpenAPI 漂移、Decision Rule Audit、Behavior
  Golden、Release Impact Golden 和 `git diff --check` 均通过；新增协议语法、
  Job 状态投影、持久化脱敏和 Runtime 容量关系已经集合哈希人工归类。

### 4.7 语义 Task SLO 与成本预算

阶段 0A 先产出当前基线；阶段 5～7 任一 Feature 从观察切到生效前，必须冻结
版本化 `TaskSloDescriptor`。每个 Descriptor 至少声明：

- task／route ID 和 owner；
- 统计窗口与最小样本量；
- P50／P95／P99 latency budget；
- 单请求最大 Task run 和 Provider attempt；
- 输入字符／Token 和输出 Token 上限；
- 每日调用量和并发上限；
- 计费 Provider 的每日／每千次成本上限；
- 本地模型的 CPU／GPU 时间预算；
- timeout、unavailable、contract violation 和 fallback rate 上限；
- circuit breaker scope；
- 超预算 terminal action；
- baseline artifact 和批准人。

规则如下：

- 预算没有冻结前只能观察，不能切换正式行为。
- 本地免费模型可以不填货币成本，但不能省略调用量、延迟和资源预算。
- `private_decision` 的净增调用量必须按“原规则短路消息”单独统计，不能把已有分类
  调用全部算作新增。
- `TaskRuntime` 必须使用 `ModelRouteDescriptorRegistry` 的健康候选、成本 cap 和
  `ModelFailureTracker`；不得由分类器直接绕过熔断器创建 Provider。
- 验收报告同时给出绝对值和相对 baseline 的变化，不能只给平均值。

#### 实施状态（当前工作树）

阶段 4.7 已完成 SLO 合同、运行时硬上限和审计门禁；没有启用任何阶段 5～7
Feature：

- 新增冻结的 `TaskSloRegistry`，为 `timing_gate`、`private_decision`、
  `news_daily_quality`、四个 `group_analysis_*` 分支和
  `group_memory_learning` 声明版本、owner、基线、样本量、延迟、调用量、
  输入字符／Token、输出 Token、成本／资源、失败率、熔断 scope 和终态；
- `timing_gate` 与 `private_decision` 已冻结目标预算；新闻、群分析和群学习因
  样本不足、逐分支归因缺失、Token／币种化成本或类型化失败率缺失，明确保持
  `baseline_only`，`require_task_slo_activation()` 会拒绝；
- `ModelRouteDescriptor` 精确引用逐 Task SLO，目标 `ResiliencePolicy` 统一按
  invocation 解析；Registry 构造期对账 Task、Route、owner、输出上限、attempt
  和 terminal action，引用漂移会在导入时失败；
- `TaskRuntime` 在 Provider 调用前校验请求输出 Token 上限、渲染后输入字符和
  估算 Token；超限返回类型化 `quota_exceeded`，并使用对应 Policy 的安全终态，
  不把 Prompt 或超限正文写入异常与 Telemetry；
- `task.execute` 事件只增加 SLO ID／版本／状态、输入字符、估算 Token 和
  Provider 返回的合法非负数值 usage；字符串、负数和非整数 usage 被丢弃，
  Telemetry 故障仍不改变 Task 结果；
- 新增 `task-slo-manifest.v1.json` 及确定性生成器，固定基线 SHA-256、
  Registry generation／Hash、样本充分性、逐项预算结果、观测完备性和
  `activation_ready`；CI 已加入非宽容漂移检查；
- Manifest 没有把“预算已冻结”冒充“现有基线已通过”：
  `private_decision` 的旧基线失败率为 `0.223881`，高于 `0.10` 目标，
  因而 `baseline_pass=false`、`activation_ready=false`；新闻和群任务也全部
  `activation_ready=false`；
- TDD 先得到 4 个运行时失败和 3 个 Manifest 缺失失败；GREEN 后 SLO／Task
  Runtime／Telemetry／Route／Session Summary／Memory Digest／群分析等组合
  回归为 313 passed、0 failed。Ruff、Python 编译、架构边界、OpenAPI、
  Release Impact、Behavior Golden、Decision Rule Inventory、SLO Manifest 和
  `git diff --check` 均有独立门禁；新增 20 条规则已逐项或集合哈希人工复核。

## 阶段 5：私聊 Timing 与 Casual Fast Path 重构

### 5.1 原始问题

私聊当前不是“模型分类一次，然后执行类型化 Policy”，而是多层关键词互相覆盖：

1. `core/private_timing.py` 先用关键词推断 effort／intent；
2. casual 直接短路，不调用分类模型；
3. `core/reply_templates.py` 又对原始文本做一次关键词分类；
4. `chat_pre_bridge_decision.py` 用旧函数签名、`TypeError` 和硬编码回退文案兼容；
5. 分类器仍能把旧自由文本标签映射为结果。

这会导致改一组词影响多条路径，Web 配置和模型能力也无法解释最终决定。

### 5.2 已确认决定

- 私聊语义只分类一次。
- 结构化信号和数值 Timing 规则继续保留。
- 只有高置信、无冲突、有限 intent 才允许 Casual Template Fast Path。
- `is_superuser` 只属于 Capability Policy，不参与语义分类。
- 模型或 Schema 失败时进入正常 Agent 路径，不靠关键词猜测为 casual。

### 5.3 新 `PrivateDecision` 合同

```text
action         = reply_now | wait | no_reply
effort         = casual | short | serious
intent         = 稳定枚举
response_mode  = template | agent | none
confidence     = 0..1
parse_quality  = schema_valid | schema_repaired | invalid
error_type     = 稳定 Typed Failure code | null
```

可选诊断字段：

- `conflicting_signals`；
- `material_state`；
- `reason_code`；
- `contract_version`；
- `task_run_id`。

不再接受或保存模型生成的自然语言 `reason`。审计和后续分支只使用稳定
`reason_code`、Typed Failure、哈希、字节数和 Task run 标识。

### 5.4 确定删除的内容

从 `core/private_timing.py` 退出：

- `_NO_REPLY_SET`；
- `_WAIT_MARKERS`；
- `_TRANSPORT_PATTERNS`；
- `_TASK_PATTERNS`；
- `_DIAGNOSTIC_QUESTION_PATTERN`；
- `_DAILY_REQUEST_PATTERNS`；
- `_INLINE_MATERIAL_STRONG`；
- `_INLINE_MATERIAL_WEAK`；
- `_DIAGNOSTIC_MATERIAL_MARKERS`；
- `_REQUEST_MARKERS`；
- `_IDENTITY_PROBE_WORDS`；
- `_CHECK_CAPABILITY_WORDS`；
- `_IS_BOT_WORDS`；
- `_PERSONAL_PROBE_WORDS`；
- `_MISSING_MATERIAL_WORDS`；
- `_TOO_BROAD_WORDS`；
- `_looks_transport_only()`；
- `_looks_task_request()`；
- `_looks_daily_request()`；
- `_has_diagnostic_material()`；
- `_has_inline_material()`；
- `_infer_effort()`；
- `_private_model_confidence()`；
- `_is_low_confidence_private_parse()`。

其他删除：

- `get_casual_reply(text, is_superuser)`；
- `TypeError` 旧签名重试；
- `effort == "casual"` 无条件早退；
- `"你先说事"` 硬编码回退；
- `_parse_fallback()` 对 `NO_REPLY`、`WAIT`、`是,5` 等旧文本的兼容。

旧输出若确需一个发布周期兼容，只能由 `CompatibilityRegistry` 的版本化 parser 接受，并记录使用量；不能继续散落在主 parser。

### 5.5 明确保留

- `TimingSignals`；
- `TimingDecision`；
- `decide_timing()`；
- URL、代码块、Blob、密钥形态等确定性材料信号，但它们只用于安全处理和诊断，
  不再单独决定自然语言 intent、`no_reply`、`wait` 或 casual；
- Mention、Reply、directed-to-other 等结构化输入；
- Cooldown 和数值评分；
- `get_casual_reply(intent)`，但它只按稳定 intent 选择模板，不再读取原始用户文本；
- Casual Fast Path 的低延迟能力。

### 5.6 新执行顺序

1. 协议 Adapter 构建 Message Contract。
2. 确定性提取结构化 `TimingSignals`，不推断自然语言 intent；旧数值评分仅写入
   `semantic_effect=diagnostic_only` 的诊断字段。
3. `private_decision` Task Runtime 只调用一次。
4. Schema 和跨字段校验：
   - `response_mode=template` 必须属于允许的有限 intent；
   - `action=no_reply` 时 `response_mode=none`；
   - `confidence` 达到配置阈值；
   - 不存在结构化冲突。
5. `PrivateTimingPolicy` 只接收类型化模型结果、灰度模式和数值置信度门槛；
   Cooldown 等独立确定性限流继续由原有数值层负责，不能覆盖语义结果。
6. 高置信 template 才调用 `get_casual_reply(intent)`。
7. 其他 `reply_now` 进入正常 Agent Runtime。

### 5.7 测试与验收

- 旧关键词同义改写不再改变 Python 分支。
- 相同输入只出现 1 次 `private_decision` Task run。
- 模型超时、非法 JSON、未知 intent 和低置信都不会误入模板 Fast Path。
- 上述失败全部进入正常 Agent，不能由 `TimingSignals` 单独决定自然语言
  `no_reply`／`wait`／casual。
- superuser 和普通用户的语义分类相同；能力差异只体现在 ToolPlan。
- 模板回复只接受结构化 intent，不能重新读取 query。
- Eval 数据集覆盖寒暄、身份探询、缺材料、诊断、日报请求、长文本、附件、等待和不回复。
- Prompt Runtime 的 `private_decision` canonical 模板与 Schema 同步。
- 达到阶段 4.7 冻结的 P50／P95／P99、调用量、失败率、Token 和成本预算；
  超预算时 Feature 保持观察或回滚，不以降低 Schema 校验换性能。

### 5.8 实施状态

截至 2026-07-23，代码侧切换已经完成，但生产激活门禁未通过：

- `private_decision_v2` 已登记严格 Schema、Task Runtime、Route、SLO 和
  metadata-only Telemetry；每条进入灰度的非空私聊最多调用一次模型。
- `private_timing.rollout.default_mode=disabled`，默认不调用分类模型并进入正常
  Agent；可按 canonical session 设置 `observation`，观察模式只记录 proposal，
  不执行 `no_reply`、`wait` 或模板提案。
- `active` 除 session 配置外还要求
  `private_timing.rollout.active_allowed=true`；Feature Lifecycle 仍是
  `preview/default_enabled=false`，并要求离线 Eval、Task SLO、Token 可观测、
  显式 session allowlist 和运维批准全部通过。
- 当前阶段 4.7 基线中 `private_decision` 失败率为 `0.223881`，高于 `0.10`
  预算，因此 `activation_ready=false`。不得在本轮启用生产 active。
- 模型超时、供应商不可用、非法 JSON、未知 intent、交叉字段冲突、低置信和
  模板策略不满足时均进入正常 Agent；不再回退到关键词语义判断。
- Behavior Golden 已从旧关键词 effort 快照切换为 disabled、observation、
  active、低置信、模板、`no_reply` 提案和 Task Failure 的策略快照。

## 阶段 6：新闻搜索与 AI 日报语义治理

### 6.1 原始问题

新闻链路同时存在：

- `search_backend.py` 的日报、RSS、紧急、时限和 token blacklist；
- `rank.py` 的 `AI_KEYWORDS`／`NON_AI_PATTERNS`；
- `evidence_light.py` 的实体、claim 和 why-matters 映射；
- pipeline `config.py` 的来源、权重、时间窗口、实体和主题词；
- `AiDailyRequest` 已经类型化一部分输入，但其他入口仍从 query 再猜一次；
- 多份 36／48／72 小时窗口和新鲜度阶梯。

关键词目前不仅提供信号，还会直接过滤候选，容易错删未知模型、公司和跨领域新闻。

### 6.2 已确认决定

- 所有入口先归一为类型化 `NewsRequest`。
- 当前 `AiDailyRequest` 作为迁移基础，不再新增平行 DTO；最终成为
  `NewsRequest` 的日报具体类型或兼容别名。
- 日期、URL、RSS 和 HTML 等确定性解析继续保留。
- AI 关键词、排除词和品牌实体只作为评分／召回信号。
- 不因关键词未命中直接删除候选。
- 只有信号冲突、未知实体或边界候选进入一次批量结构化模型仲裁。
- 不为每条新闻单独调用模型。

### 6.3 类型化请求

`NewsRequest` 至少包含：

- query；
- request kind：search／daily_digest；
- freshness：today／latest／week／custom；
- window start／end；
- target date；
- max results；
- source policy ID；
- language；
- refresh／cache policy；
- reference time 和 timezone。

所有时间窗口在请求解析时计算一次，pipeline 不再从原始 query 重复推断。

### 6.4 来源与排序 Policy

1. 新增版本化 `NewsSourceDescriptorRegistry`。
2. canonical 来源清单迁到可校验资源文件，例如
   `resources/news/news_sources.v1.yaml`，不再散落 Python 字面量。
3. 每个来源声明：
   - source ID、URL、adapter kind；
   - enabled；
   - trust／quality weight；
   - freshness policy；
   - fetch timeout；
   - per-run limit；
   - domain；
   - lifecycle。
4. operator override 只允许修改明确可配置字段，不能把不安全 scheme 加入来源。
   - 版本化资源文件是来源 ID、URL、安全边界、字段 Schema 和默认值的 canonical
     事实源；
   - 数据库只保存通过 SettingSpec 校验的 operator override，不复制完整来源对象；
   - override 优先级为“显式有效 operator override > canonical 默认值”；
   - 无效、过期或已 retired 的 override fail closed 并保留 provenance；
   - Registry snapshot 是运行时唯一读取面，调用方不能分别读取 YAML 和数据库。
5. `NewsRankingPolicy` 集中：
   - 新鲜度窗口；
   - trust、freshness、relevance、diversity 权重；
   - 每来源／每实体配额；
   - top story 条件；
   - unknown date 策略。
6. `NewsSignalExtractor` 可以保留关键词和实体词典，但输出
   `positive_signals`、`negative_signals`、`unknown_entities`，不直接返回删除决定。

### 6.5 批量模型仲裁

对确定性信号无法稳定判断的一小批候选，调用一个 `news_relevance_review` Task：

- 输入有界 evidence card；
- 输出每个候选的 `relevant`、category、importance、entities、confidence 和 reason code；
- 只能引用本批 candidate ID；
- 后端校验 candidate ID、数量和 Schema；
- 模型失败时保留候选并降低评分，不直接清空日报。

日报质量摘要继续使用 `news_daily_quality` route；`35b8904` 已完成该 route 接线。

### 6.6 确定清理项

- `search_backend.py` 的 `2025|2026`；
- 新闻意图、RSS 优先、紧急程度和时间范围多份推断；
- token blacklist 多份维护；
- `AI_KEYWORDS`／`NON_AI_PATTERNS` 直接过滤；
- 多份来源 URL、启用状态和权重；
- 多份 36／48／72 小时窗口；
- 通过 source name／domain 字符串隐式决定重要性的分支；
- `why_matters` 的静态语义文案映射，改由结构化摘要或明确分类模板生成。

### 6.7 明确保留

- RSS／Atom／HTML 解析；
- URL canonicalization 和 domain 安全校验；
- 日期格式解析；
- 去重 Hash；
- 来源多样性和数值配额；
- 抓取超时、正文长度和缓存上限；
- deterministic fallback digest。

### 6.8 测试与验收

- 所有 search／daily 入口生成同一 `NewsRequest`。
- 修改年份不需要改 Python 正则。
- 未知 AI 公司或模型不会因关键词缺失被直接删除。
- 明显无关候选可以靠高置信信号降权，但最终删除必须有明确 Policy／模型结果。
- 单次日报最多 1 次 relevance review 和 1 次 quality summary，不出现 N 条新闻 N 次调用。
- 来源配置 Schema、权重、时效、超时和 URL 安全均有测试。
- 现有 RAG／新闻 benchmark 增加未知实体、跨领域、负关键词误伤和旧新闻用例。
- 达到阶段 4.7 冻结的批大小、P95／P99、每日调用量、Token 和成本预算。

### 6.9 实施状态

截至 2026-07-23，阶段 6 的代码切换和定向门禁已经完成，但生产模型审核仍未
激活：

- search／daily 已统一使用 `NewsRequest`；`AiDailyRequest` 仅保留为兼容别名。
  request kind、freshness、时间窗口、缓存策略、来源 Policy、语言和时区均由
  Adapter 显式构造，不再从 query 猜测日报、紧急程度、年份或 RSS 优先级。
- canonical 来源已迁入
  `resources/news/news_sources.v1.json`，由冻结的
  `NewsSourceDescriptorRegistry` 校验 HTTPS、domain、adapter kind、生命周期
  和 operator override 白名单；日报和普通搜索读取同一 Runtime snapshot。
- `NewsRankingPolicy` 已集中评分、来源配额、聚类和新鲜度合同。latest 请求窗口
  保持 72 小时，日报函数默认窗口保持 48 小时，top story 保持 36 小时；三者名称
  和用途已分开，避免集中迁移时改变旧调用合同。
- 旧 `AI_KEYWORDS`／`NON_AI_PATTERNS` 过滤、query 语义路由、source-name
  重要性判断和静态 `why_matters` 映射已退出主链。`NewsSignalExtractor`
  仍可使用词典和正则，但只输出信号、未知实体、边界原因和分数，不直接删除候选。
- `news_relevance_review` 已登记严格批量 Schema、Route、Task、Resilience、
  SLO、Feature 和 metadata-only 运行合同。单批 candidate ID 必须精确一致，
  默认 `disabled`；`observation` 只记录结构化 proposal，不改变排序。
- `active` 同时要求 `news.relevance_review.active_allowed=true` 和 Task SLO
  激活门禁。当前 SLO 是 `baseline_only`、`activation_ready=false`，因此即使
  配置 active 也只能降级为 observation；不得在本轮生产启用。
- 真正 active 后也只有
  `relevant=false + confidence>=threshold + reason_code=clear_non_ai`
  同时成立才允许删除。低置信结果只降权；模型、Provider 或 Schema 失败保留全部
  候选并执行保守降权，不回退到关键词删除。
- Behavior Golden 已补齐 canonical 新 Prompt 文件覆盖，并形成阶段 5→阶段 6
  连续哈希链；`news_relevance_review` Prompt、新闻来源 Registry、Task SLO、
  Release Impact 和 Decision Rule Inventory 均已进入生成物门禁。
- 阶段 6 定向组合测试为 356 passed、0 failed。Ruff、Python 编译、架构边界、
  Behavior Golden、Task SLO Manifest、Release Impact、Decision Rule Inventory
  和 `git diff --check` 均通过；真实外部新闻抓取和生产模型调用不在本地定向门禁
  中，不能据此宣称生产效果已经验证。

## 阶段 7A～7D：表达、黑话、群体记忆和定时分析收敛

### 7.1 当前问题清单

这部分不是一个“改正则”的小任务，而是需要把两套割裂的记忆生命周期合并。

#### 旧表达／黑话链

- Web 只在会话配置中显示“注入表达／表达学习／黑话学习”3 个隐蔽开关。
- 学习周期没有读取 `enable_expression_learning` 或 `enable_jargon_learning`，开关实际不约束后台扫描。
- 每 10 分钟扫描最近 15 分钟，没有 `ChatLog.id` 游标。
- 同一消息会在重叠窗口中重复计分。
- `confidence >= 0.75` 会把未经模型或人工审核的候选自动设为 active。
- 黑话遇到同词不同释义时会更新或覆盖，不能表达冲突。
- 定义正则过宽：
  - `(.{1,10})就是(.{1,30})`；
  - `(.{1,10})的意思是(.{1,30})`；
  - `什么叫...就是...`；
  - `A=B`。
- `BAD_LEARN_TERMS` 手工重复工具名和内部词。
- `build_expression_context()`／`build_jargon_context()` 没有 canonical 生产调用点。
- `ExpressionMemory/JargonMemory` 与 `GroupMemory` 是两套独立事实源。

#### 当前 GroupMemory 链

- `group_analysis` 是模型可调用工具，不是自动调度器。
- 当前定时任务没有调用 `group_analysis` 或其共享分析服务。
- `analyze_group()` 固定创建 topics／titles／quotes／quality 4 个分支，不能选方面。
- `memory_candidates.extract_and_persist()` 当前主要只把 topic 写入 `GroupMemory`。
- `titles`、`quotes`、`quality` 被跳过；expressions／slang／style 没有统一学习分支。
- 精确内容 Hash 可以防完全重复，但 `cluster_key` 依赖空格分词，对中文近似内容效果差。
- Web 只展示 evidence ID 或有限展开，缺少完整候选、冲突、规则和运行视图。
- `merged_into_id` 已存在，但没有完整合并操作和审核账本。
- `GroupMemory.group_id` 仍依赖 `group_` 旧格式。

### 7.2 总体决定

- 退役旧 `ExpressionMemory/JargonMemory` 自动学习链。
- 表达、黑话和风格统一进入 `GroupMemory` 生命周期。
- 正则长期保留为常态信号提取器，但只能产生候选。
- 生成群聊记忆时，在同一次模型调用中同时提供：
  - 群聊上下文；
  - 正则候选；
  - 候选 evidence；
  - 同群、同类型的少量近似正式记忆；
  - 同批其他候选。
- 模型在同一次调用中：
  - 审核正则候选；
  - 修正文案或释义；
  - 拒绝误报；
  - 补充自己发现的新候选；
  - 决定 new／merge／alias／conflict／reject。
- 模型审核通过后，后端仍必须验证 evidence、scope、目标 ID 和 Evidence Policy。
- 不再调用第二个模型复审。
- 人工审核优先于模型审核，人工修改不需要再让模型看。
- 候选、证据、运行和正式记忆分表。

#### 7.2.1 分阶段切换和硬门禁

阶段 7 不作为一次大爆炸发布，固定拆成以下可独立验证和回滚的切片。

##### 阶段 7A：Schema 与只读基础设施

- 新增 schedule、stream state、candidate、evidence、run 5 张表和治理字段；
- 实现 Repository、Query Service、Aspect／Rule／Evidence Descriptor；
- 实现迁移 dry-run、冲突报告和数量／Hash 对账；
- 所有新 Feature 默认关闭；
- 不启用新 Writer，不改变现有读取结果；
- 数据库迁移可独立回滚应用版本，但不破坏性删除新表。

##### 阶段 7B：Candidate-only 观察

- 在启用候选扫描前先停止旧 Expression／Jargon 自动激活 Writer；
- 正则只能写 candidate／evidence；
- 模型审核可以旁路运行并记录决定，但不得写 active `GroupMemory`；
- candidate 和旁路模型结果不得进入 Prompt；
- 当前 `confidence >= 0.75` 自动 active 逻辑不能在双写观察期继续运行；
- 统计规则召回、误报、模型接受／拒绝／冲突、延迟、Token 和成本；
- 宁可暂时没有新的自动表达记忆，也不能继续生成未经审核的 active 记录。

##### 阶段 7C：受控治理写入

- 只对白名单 canonical session 开放；
- 模型审核和 Evidence Policy 同时通过后才写 active `GroupMemory`；
- 人工审核直接生效且不二次送模型；
- 启用新 Prompt 注入读取面；
- Scheduler、Web 和 Tool 使用同一 Application Service；
- 通过游标、幂等、并发、失败重试、冲突和回滚验收后逐 session 扩大。

##### 阶段 7D：旧数据与兼容退役

- 旧 Expression／Jargon 迁为待审候选；
- 旧表和旧读取接口只读；
- 记录旧 alias／fallback 使用量；
- 满足连续零使用、迁移对账、回滚和备份恢复门禁后，另开删除迁移；
- 任一回滚只能恢复旧读取，不能恢复旧自动写入或自动激活。

### 7.3 7 个可选分析方面

统一 Aspect Descriptor：

| aspect | 说明 | 定时默认 | 写长期 `GroupMemory` | Prompt 注入候选 |
|---|---|---:|---:|---:|
| `topics` | 稳定讨论话题 | 是 | 是，映射 `topic` | 是 |
| `expressions` | 群内常用表达 | 是 | 是，映射 `expression` | 是 |
| `slang` | 黑话／术语及释义 | 是 | 是，映射 `slang` | 是 |
| `style` | 群体交流风格 | 是 | 是，映射 `style` | 是 |
| `titles` | 活跃用户称号 | 否 | 否，只进入报告 | 否 |
| `quotes` | 金句 | 否 | 否，只进入报告 | 否 |
| `quality` | 聊天质量锐评 | 否 | 否，只进入报告 | 否 |

`GroupAnalysisAspectDescriptor` 是默认值、显示名、Task Contract、长期写入策略和
Prompt 注入能力的唯一来源。Web、API、工具 schema 和 Scheduler 不得各自维护这 7 个枚举。

现有 `relationship`、`event`、`preference` 等 `GroupMemory` 类型不因本阶段删除；它们不属于这 7 个定时分析方面，继续由现有受控来源或人工维护。

### 7.4 `group_analysis` 工具与共享服务

#### 已确认边界

- `group_analysis` 继续是工具。
- 自动定时提取复用相同分析能力，但不让模型在后台“调用工具”。
- 工具最多增加一个可选 `aspects` 参数，不重做工具语义。
- Scheduler、Web 手动提取和 Tool Adapter 调用同一个 Application Service。
- Scheduler 不解析 KT `ToolResult` 或 HTML 报告。

#### 新共享合同

```text
GroupAnalysisRequest
  chat_stream_id
  aspects[]
  window/cursor
  instructions
  trigger = tool | manual | schedule | migration_review

GroupAnalysisResult
  topics
  expressions
  slang
  style
  titles
  quotes
  quality
  source_log_ids
  task_runs
  report
```

#### 分支策略

- `topics`：保留独立 topics Task。
- `titles`：保留独立 titles Task。
- `quotes`：保留独立 quotes Task。
- `quality`：保留独立 quality Task。
- `expressions`、`slang`、`style`：合并为一个 `group_memory_learning` Task，根据选中 aspect 只输出需要的字段。
- 只创建被选择的分支；未选择方面不渲染 Prompt、不调用模型、不生成 fallback 数据。
- 任一报告分支失败不影响其他报告分支。
- 长期记忆分支失败时不推进学习游标，也不写半套正式记忆。

#### 默认行为

- 定时配置新建时默认选择 `topics/expressions/slang/style`。
- Web“立即提取”默认使用该 session 的定时选择；没有配置时使用上述 4 项。
- 为兼容现有显式工具调用，工具省略 `aspects` 时暂时保持现有报告默认
  `topics/titles/quotes/quality`；该兼容默认进入 `CompatibilityRegistry`，使用量归零后再评估是否切到 4 个长期记忆方面。

这一项是计划中的显式兼容选择，需在审核时确认。

### 7.5 数据模型

#### 7.5.1 `group_learning_schedules`

“存在该行”即表示进入自动学习白名单。至少包含：

- canonical `chat_stream_id` 主键；
- `enabled`；
- `aspects_json`；
- `interval_minutes`，默认 1440；
- `window_hours`，首次默认 24；
- `next_run_at`；
- `last_started_at`；
- `last_completed_at`；
- lease owner／expires；
- `consecutive_failures`；
- `last_error_code`；
- config generation；
- created／updated 时间。

白名单默认空。没有该行的 session 不做后台正则提取，也不做定时模型总结。

#### 7.5.2 `group_learning_stream_states`

至少包含：

- canonical `chat_stream_id` 主键；
- `last_scanned_chat_log_id`；
- `last_success_chat_log_id`；
- `last_candidate_watermark`；
- `rules_generation`；
- `last_success_run_id`；
- `last_success_at`；
- `last_error_code`；
- version／updated_at。

它只保存增量状态，不保存定时配置。

#### 7.5.3 `group_learning_candidates`

至少包含：

- candidate ID；
- canonical `chat_stream_id`；
- candidate type：topic／expression／slang／style；
- canonical content；
- meaning；
- normalized key；
- fingerprint／content Hash；
- source：rule／model／legacy_expression／legacy_jargon／legacy_group_memory／human；
- status；
- rule ID／rule version；
- first／last seen；
- hit count；
- source run ID；
- model decision；
- model contract version；
- model review run ID；
- merge target／alias target；
- promoted `GroupMemory` ID；
- conflict group ID；
- approval source；
- rejection／waiting reason code；
- created／updated 时间。

建议状态：

```text
raw
pending_model_review
waiting_for_evidence
accepted
rejected
merged
alias
conflict
superseded
```

#### 7.5.4 `group_learning_evidence`

至少包含：

- ID；
- `candidate_id`；
- `chat_log_id`；
- sender ID；
- source run／batch ID；
- `evidence_hash`；
- evidence kind；
- created_at。

约束：

```text
UNIQUE(candidate_id, chat_log_id)
```

旧 Expression/Jargon 示例文本不能伪造成 `ChatLog` evidence。

#### 7.5.5 `group_learning_runs`

至少包含：

- run ID；
- canonical session；
- trigger；
- selected aspects；
- cursor start／end；
- context-only 范围；
- candidate watermark；
- Rule Registry generation；
- Task Contract version；
- model route／provider／model；
- status；
- 原始／清洗／有效消息数；
- candidate／accept／reject／conflict／waiting 计数；
- error code；
- started／completed 时间；
- trace／job ID。

#### 7.5.6 `group_memories` 治理字段

新增或规范化：

- canonical `chat_stream_id`；
- `memory_type` 增加 `expression`；
- `approval_source = human | model`；
- `governance_mode = automatic | human_managed`；
- `approved_content_hash`；
- `model_review_run_id`；
- `model_contract_version`；
- `human_reviewer_id`；
- `human_reviewed_at`；
- `human_action`；
- `conflict_group_id`；
- `merged_into_id`；
- version／updated_at。

正式记忆继续保存 evidence 关联，但后续读取应通过规范化 evidence Query Service，不再只解析 JSON ID 列表。

### 7.6 `LearningSignalRuleRegistry`

#### 已确认决定

- 代码所有、版本化。
- 稳定 `rule_id` 和整数 version。
- 启动冻结；重复 ID、版本倒退或非法正则直接失败。
- 正则只能产生候选。
- Web 第一版可以查看和 dry-run，不能保存任意正则。

#### Rule Descriptor

每条规则至少包含：

- rule ID／version；
- candidate type；
- owner；
- pattern 或 deterministic extractor；
- canonicalizer；
- 最大输入长度；
- 每条消息最大匹配数；
- 每批最大候选数；
- scope；
- positive fixtures；
- negative fixtures；
- 性能预算；
- lifecycle；
- metrics labels。

#### 保留词

保留词从以下冻结快照派生：

- Tool Registry；
- Task Registry；
- Prompt Contribution Registry；
- Model Route Registry；
- Feature／Compatibility Registry；
- 明确的协议和系统标记。

不再手工维护 `BAD_LEARN_TERMS`。

#### Web 能力

- 查看规则；
- 查看版本和 owner；
- 对管理员提供的示例文本 dry-run；
- 查看命中、模型接受、拒绝、冲突、等待证据和晋级率；
- 全局或单 session 停用规则；
- 不提供任意 pattern 编辑器。

### 7.7 增量提取和定时调度

#### 白名单

- 自动学习只处理 `group_learning_schedules` 中显式存在且 enabled 的 canonical session。
- 白名单默认空。
- 超级用户、活跃群、已有 ChatStreamConfig 或已有 GroupMemory 都不能隐式绕过。
- 白名单同时约束后台正则候选提取和定时模型总结。
- 管理员手动“立即提取”不受白名单限制，但仍受权限、资源和审计约束。
- 定时学习与 Prompt 注入开关相互独立。
- 另有全局 `group_learning.enabled` kill switch。

#### 游标和窗口

- 默认每 24 小时运行。
- 第一次默认读取最近 24 小时。
- 后续严格按 `ChatLog.id` 增量，不用滚动重叠窗口。
- 可加载上一批 10～20 条消息作为 `context_only`，但不能重复记为 evidence。
- 无新消息不调用模型。
- 少于 3 条可分析新消息时暂缓，不推进游标。
- 单批设置消息数、字符数、候选数和模型 Prompt 预算。
- 模型、Schema、evidence、数据库或 settle 失败时不推进成功游标。
- 服务恢复后从成功游标继续。
- 手动补提取默认不倒退自动游标。

#### Job 语义

- 调度使用 Durable Job Kernel。
- 同一 session 同一 cursor range 具有稳定 idempotency key。
- lease fencing 阻止两个 worker 同时 settle。
- 成功写入候选、正式记忆、run 和 cursor 必须处在可恢复事务边界。
- 正则扫描可先提交候选；模型审核失败时候选保持 `pending_model_review`。
- `last_scanned_chat_log_id` 可以在候选和 evidence 持久化成功后推进；
  `last_success_chat_log_id` 只有模型审核、正式写入和 settle 全部成功后才能推进。
- 重试按 candidate watermark 和 `last_success_chat_log_id` 继续，不能因 scanned cursor
  前移而跳过待审候选。

### 7.8 模型审核与近似召回

#### 模型输入

每个 `group_memory_learning` Task 只接收：

- 本批清洗后的有界消息；
- 受信 `ChatLog.id` 和 sender ID；
- 正则候选；
- 每个候选少量同群同类型近似记忆；
- Evidence Policy 摘要；
- 选中 aspects。

#### 近似召回

- 精确归一化和 Hash 负责确定性去重。
- SQLite FTS、字符 n-gram、可用时 embedding 只负责召回。
- 召回候选限制在同 canonical session、同 memory type。
- 每个候选最多向模型提供少量近似项。
- 不使用单一 `SequenceMatcher` 或 embedding 阈值直接合并正式记忆。

#### 模型输出动作

每个候选必须是以下之一：

- `new`；
- `merge_into`；
- `add_alias`；
- `conflict_with`；
- `reject`。

模型还可以补充自己发现的新候选，但必须附合法 evidence ID。

#### 后端验证

- 所有 evidence ID 必须属于本次受信输入或允许的 context 范围；
- 正式 evidence 不能来自 context-only；
- merge／alias／conflict 目标必须属于当前 session、同类型和本次提供的近似集合；
- 模型同批输出要先做批内归一和冲突检测；
- 同词不同释义进入 conflict group，不能覆盖；
- 非法目标只拒绝该动作并记录 contract violation，不越权查询其他群。

### 7.9 Evidence Policy

Evidence Policy 代码所有、版本化。Web 可以查看未激活原因，第一版不能任意修改阈值。

| 类型 | 自动晋级最低证据 |
|---|---|
| `topic` | 至少 2 条 evidence，至少 2 人 |
| `expression` | 至少 2 条、至少 2 人；或同一人跨至少 2 个独立批次累计 3 次 |
| `slang` | 1 条明确释义；或至少 2 条一致用法，普通用法至少 2 人 |
| `style` | 至少 3 条、至少 2 人 |
| `titles`／`quotes`／`quality` | 只属于报告，不写长期记忆 |

模型返回 accept，但证据不足时：

```text
status = waiting_for_evidence
```

后续新 evidence 可以重新触发审核或满足已批准内容的 Evidence Policy。不能把模型 accept 直接等同于 active。

### 7.10 人工审核

#### 人工可执行动作

- 从零创建；
- 编辑并接受；
- 接受候选；
- 拒绝；
- 合并；
- 添加 alias；
- 解决冲突；
- 禁用／恢复；
- 标记为 human-managed；
- 显式交还自动管理。

#### 权威规则

- 人工从零创建、编辑、合并或解决冲突后可以直接 active，不需要模型再看。
- 最终记录必须保存批准内容 Hash、审核人、时间和动作。
- `human_managed` 正文和释义不能被后台模型覆盖。
- 后台可以追加 evidence。
- 对 human-managed 内容发现新冲突时，只创建冲突候选并提示管理员。
- 管理员显式“交还自动管理”后，后续模型才可以更新。

### 7.11 旧数据迁移

#### Expression／Jargon

- 所有旧记录迁入 `group_learning_candidates`。
- examples 只作为 legacy hint，不伪造成 evidence。
- 旧自动 active 统一变为 `pending_model_review`。
- 旧 `checked=true` 本身没有审核人和动作凭据，不能自动伪装成
  `approval_source=human`；若能由 AdminAuditLog 证明人工动作，才迁为 human-managed。
- 原表迁移后只读；所有新写被拒绝。

#### GroupMemory

- 保留 content、类型、evidence、source 和时间。
- 能证明人工审核的记录标为 human-managed。
- 有新版 model run／contract 凭据的记录保留 automatic 状态。
- 缺少新版审核凭据的旧记录降为 review／manual-only。
- 白名单 session 下一次定时总结时可重新审核。
- 不在迁移中删除旧内容。

#### 删除旧表

只有同时满足以下条件才执行独立删除迁移：

- 新链路稳定运行至少 30 个连续自然日，并跨过至少 1 次完整生产发布；
- 旧表只读期间没有写入；
- 旧 alias、legacy read fallback 和旧 API 使用量连续 30 天为 0；
- 回滚演练通过；
- 数据数量、Hash 和审核状态对账通过；
- 备份恢复演练通过。
- Release Owner、群记忆 Data Owner 和生产 Operator 三方在删除报告上确认。

### 7.12 Prompt 注入

- Prompt 注入只读取 active `GroupMemory`，不读取 candidate。
- `topics/expressions/slang/style` 由一个 Group Memory Contribution Provider 组装。
- 注入开关与定时学习开关独立。
- `build_expression_context()`／`build_jargon_context()` 在新 Contribution 接线完成后删除。
- 注入结果必须标记为不可信数据，不提升为 system policy。
- 每条注入内容带稳定 memory ID 和 evidence 摘要；普通 Trace 不保存正文。
- 人工禁用或冲突中的记忆不注入。

### 7.13 阶段 7A～7D 验收

- 7A 只新增 Schema／读面，Feature 关闭时生产行为不变。
- 7B 启用前旧自动激活 Writer 已停止；观察期没有新记录因
  `confidence >= 0.75` 自动进入 active。
- 7B 的 candidate、旁路审核结果和旧未审记录均不进入 Prompt。
- 7C 只对白名单 session 写正式记忆，关闭 Feature 可立即停止新写且不删除数据。
- 白名单为空时，后台不扫描任何群、不调用任何群学习模型。
- 同一 `ChatLog.id` 不会重复计入同一候选 evidence。
- 模型审核前没有正则候选可以 active。
- 人工审核后的修改不会触发模型二审。
- 同词不同释义形成 conflict，不覆盖。
- `topics/expressions/slang/style` 审核和 evidence 满足后真实写入 `GroupMemory`。
- `titles/quotes/quality` 不写长期记忆。
- 未选择的 aspect 不创建对应模型调用。
- 失败不推进成功游标，重启后可继续。
- 老 Expression／Jargon 数据完整迁移且原表停止写入。
- Prompt 注入只使用正式记忆。
- 生产学习路径不再使用 `group_%` 或 `LIKE 'qq:%:group'` 识别群聊。
- 达到阶段 4.7 冻结的每 session 调用量、P95／P99、Token、成本和失败率预算。
- 7D 删除迁移满足连续 30 天、至少 1 次生产发布、三方确认及全部数据门禁。

### 7.14 阶段 7A 实施状态（2026-07-23）

阶段 7A 的代码和定向门禁已完成，阶段 7B 尚未开始：

- 新增冻结的 Aspect、Learning Signal Rule、Evidence Policy 和保留词快照；
- 新增 `group_learning_schedules`、`group_learning_stream_states`、
  `group_learning_candidates`、`group_learning_evidence`、
  `group_learning_runs` 五张表；
- 给 `group_memories` 增加治理字段，但旧 API 投影和通用 Admin Table View
  暂不暴露这些字段；
- 新增前向幂等迁移
  `20260723_group_learning_stage7a_schema`；文件型 SQLite 在 ALTER
  旧 `group_memories` 前执行在线快照，回滚应用版本不删除新表；
- 新增只读 Repository Port、SQLAlchemy Query Adapter、Query Service
  和迁移 dry-run；查询面不提供 add／update／delete／commit；
- dry-run 只返回稳定 ID 和 Hash；`checked=true` 不会被解释为人工审核，
  同词不同释义报告为 conflict；
- `group_learning.enabled=false` 且 Feature Lifecycle 为 experimental；
  schedule 表默认空，不接 Scheduler、不启动 Writer、不调用模型、不写
  candidate 或 active `GroupMemory`；
- 群学习正则调用已在 Decision Rule Inventory 中人工标记为
  `natural_language_semantic / model_signal_only / reviewed`。

阶段 7A 定向回归结果为 `164 passed, 0 failed`，并覆盖迁移、GroupMemory、
RAG、Feature／Setting Registry、Behavior Golden、Release Impact、
Decision Rule Inventory 和 CQRS 边界。该结果不表示 7B 的 candidate-only
Writer、模型旁路审核或任何生产 Feature 已启用。

### 7.15 阶段 7B 实施状态（2026-07-24）

阶段 7B 的 candidate-only 观察链和定向门禁已经完成：

- `bootstrap.schedulers` 不再创建 `expression-learner` 线程或 handle；
  `core.expression_learner` 只保留不访问数据库的兼容墓碑；
- 旧 Expression／Jargon 表不再因 `confidence >= 0.75` 自动进入 active；
  只有已有 `checked` 兼容凭据可以维持旧表的人工状态语义；
- 新增 candidate-only Command Repository 和 Application Service，正则和
  `group_analysis` 只写 `group_learning_candidates`、
  `group_learning_evidence`、`group_learning_runs` 及 scanned cursor，
  不写 active `GroupMemory`，也不推进 success cursor；
- 相同 idempotency key、candidate fingerprint 和
  `(candidate_id, chat_log_id)` 均有数据库唯一约束和并发回查；真实文件
  SQLite 双 Session 竞争测试证明重放只产生 1 个 run、1 个 candidate 和
  1 条 evidence；
- `group_memory_learning_v1` 通过统一 TaskRuntime 做严格 Schema 和确定性
  scope 后置校验；非法 candidate、evidence、target 或 aspect 只能形成
  Typed Failure，不能越权查询或写入；
- 模型旁路审核只保存 action、修订内容、reason Hash、Task provenance、
  Token、延迟和成本观测；完整理由和原始输出正文不落库，模型失败时候选保持
  `pending_model_review`；
- 模型可以补充本批受信 evidence 支持的新候选；批内重复会被拒绝，跨批命中
  已有候选时只追加 evidence，不重复建行或覆盖人工审核 provenance；
- 人工 accept／edit_accept／reject 在阶段 7B 只更新 candidate；人工审核后
  不再送模型，后续规则或模型命中不能覆盖人工内容、审核人和原始 source run；
- canonical 与 runtime `group_memory_learning` Prompt 已同步声明
  candidate-only 边界，Task owner 已纠正为 `app.group_learning`；
- 7A→7B 文件 SQLite 迁移在 ALTER 前创建快照且连续执行幂等；
  Feature Lifecycle 已登记 7B 迁移，但
  `group_learning.enabled=false`、Feature `experimental`、schedule 默认空。

阶段 7B 定向矩阵结果为 `269 passed, 0 failed`；Architecture、
Behavior Golden、Release Impact、Decision Rule Inventory 和 Task SLO 门禁
均通过。当前仍没有群学习 Scheduler、白名单 session、正式记忆晋级或新 Prompt
注入写面，也没有执行生产模型调用或启用生产 Feature；这些属于阶段 7C，不能由
本节结果推断为已完成。

### 7.16 阶段 7C 实施状态（代码切片完成，2026-07-24）

阶段 7C 的受控治理写入、白名单调度、共享分析入口和 Prompt 注入代码切片已经
完成：

- `group_analysis` 已支持由冻结 Aspect Registry 驱动的可选 `aspects`；
  只创建、执行和渲染被选择的报告分支，工具省略参数时继续使用已登记的兼容
  默认，定时默认仍为 `topics/expressions/slang/style`；
- 工具 Schema、canonical Prompt Runtime 与运行时模板已同步七个合法方面，
  不再由工具、Prompt 和服务分别维护枚举；
- 新增独立 Governance Repository Port 和 Application Service；模型动作只有
  在 scope、provenance 和 Evidence Policy 均通过后才可创建 active
  `GroupMemory`，证据不足进入 `waiting_for_evidence`，拒绝和冲突不创建正式
  记忆；
- 人工 accept／edit_accept 可以直接创建 human-managed 正式记忆，不调用
  第二次模型；后台模型不能覆盖人工正文；
- Candidate、正式记忆、run 终态和 success cursor 在同一事务中结算；SQLite
  提交失败回归证明四者会一起回滚，不存在 SAVEPOINT 半提交；
- Schedule Command 只接受 canonical group session，表为空时不扫描任何群；
  `ChatLog.id` 增量游标区分 scanned／success，少于最低消息数或模型、合同、
  evidence、settle 失败时不推进 success cursor；
- 调度使用 Durable Job lease、owner token、generation、attempt number 和
  fencing；重复 claim、租约丢失、配置 generation 改变和进程重启均有确定性
  处理；
- Scheduler、Tool Adapter 和后续 Web 手动提取入口复用
  `GroupAnalysisApplicationService`，不让后台伪装成模型工具调用，也不解析
  KT `ToolResult`；
- `group_analysis_topics` 已进入统一 Task Runtime；冻结的 Task 输出在交给
  Pipeline 前显式 thaw，类型化 Provider 失败会记录 run 失败，不写正式记忆；
- 正式 Prompt Contribution 继续使用 canonical `group_context` ID，由
  `app.group_memory` 所有，只注入审核来源完整、无冲突且满足阈值的
  `topic/expression/slang/style`；Candidate、旧无审核 active、manual-only、
  never、冲突和缺少 provenance 的记录均不注入；
- 注入内容被标记为 `untrusted_background`，每条带稳定 memory ID 和 evidence
  摘要；普通 Trace 只使用 `hash_and_size`；
- 群记忆 RAG 缓存同时绑定 profile mode、请求预算、检索运行方式和正式记忆
  revision。人工禁用、冲突、删除或修改正式记忆后不能在 TTL 内继续注入旧
  上下文；
- `app.group_learning` 和 `app.group_analysis` 包初始化已去除 eager import，
  修复共享 Application Service 组合时的循环导入。

阶段 7C 扩展组合矩阵结果为 `338 passed, 0 failed`；群记忆注入和 RAG 定向
回归为 `28 passed`。Prompt 注入相关模块语句覆盖率为 `93%`，群学习 Pipeline
补齐 direct-topics 成功／失败路径后语句覆盖率为 `86%`。Architecture、
Behavior Golden、Release Impact、Decision Rule Inventory、Task SLO、
Prompt Runtime Audit、Ruff、编译和 `git diff --check` 均通过。

上述结论只表示阶段 7C 代码与本地门禁完成，不表示生产效果验收。
`group_learning.enabled` 继续默认关闭，schedule 表继续为空；没有创建生产
白名单、调用生产模型、部署或启用生产功能。旧 Expression／Jargon 数据迁移、
只读兼容面和旧入口退役属于阶段 7D，尚未由本节完成。

### 7.17 阶段 7D 实施状态（代码切片完成，2026-07-24）

阶段 7D 的旧数据迁移、只读兼容和旧 Writer 退役代码切片已经完成：

- 新增旧数据迁移审计与 Application Service；所有旧 Expression／Jargon 都先
  迁为 Candidate，旧样例只作为 legacy hint，不制造伪 `ChatLog` Evidence；
- `checked=true` 不再被视为人工审核。只有绑定旧记录 ID、精确目标类型、
  canonical session、正文／释义 Hash、管理员、审核时间和 schema version 的
  四类 `AdminAuditLog` 动作，才构成严格人工凭据；
- 无严格人工凭据的旧 active Expression／Jargon 统一进入
  `pending_model_review`；有完整凭据的记录可以生成 human-managed 正式记忆；
  缺少新版 model／human provenance 的旧 active `GroupMemory` 降为
  `review/manual_only`，不会继续进入 Prompt；
- 迁移提供 metadata-only dry-run 和显式 apply CLI。apply 必须同时提交
  source Hash、planned Hash 和管理员身份；输出只含数量、状态和 Hash，不输出
  旧正文。相同快照可以幂等重放，不推进 success cursor；
- 新增幂等迁移
  `20260724_group_learning_stage7d_legacy_read_only`，为
  `expression_memories`、`jargon_memories` 分别安装
  INSERT／UPDATE／DELETE 六个 SQLite 只读触发器；文件 SQLite 在首次安装前
  创建协调快照，旧表继续允许 SELECT，不执行删除；
- `upsert_expression`、`upsert_jargon`、`mark_expression_checked`、
  `mark_jargon_checked` 和旧 `memory_candidates` 第二 Writer 均变为记录
  Compatibility 使用量后稳定拒绝的墓碑；旧 Expression／Jargon 读取暂时保留并
  计量，删除仍受连续 30 天零使用、完整发布、迁移对账、回滚、备份恢复和三方
  批准门禁约束；
- `build_expression_context()`、`build_jargon_context()` 以及 Prompt DTO 中的
  `expression_context`、`jargon_context` 已删除；正式注入仍只经 canonical
  `group_context` Contribution 读取审核完整的 `GroupMemory`；
- Web“立即提取”已改为调用同一个
  `GroupAnalysisApplicationService`／`GroupLearningPipelineService`，支持可选
  aspects；手动提取不要求 schedule 白名单，但仍受全局 kill switch 约束，关闭
  时在任何模型调用前拒绝；
- Eval Sampling 已从旧 Expression／Jargon 表切换到
  `GroupLearningCandidate`；旧 `expression_learner` 只保留不访问数据库的调度
  墓碑，Eval Runner 改读正式冻结的 Learning Signal Rule Registry，不再维护
  第二份正则和保留词；
- Decision Rule Inventory 已删除只指向旧 `expression_learner` 的失效人工覆盖，
  并按当前规则集合重新冻结计数与 Hash。

阶段 7A～7D 组合矩阵结果为 `379 passed, 0 failed`；Ruff、Python 编译、
Architecture、Behavior Golden、Release Impact、Task SLO、Decision Rule
Inventory、Prompt Runtime Audit 和 `git diff --check` 均通过。

上述结论只表示阶段 7D 的代码路径和本地门禁完成。当前没有迁移生产旧数据、安装
生产只读触发器、创建 schedule 白名单、调用生产模型、部署或启用
`group_learning.enabled`。旧表物理删除不属于本次代码切片；只有满足
7.11 的生产观察和批准门禁后，才能另开删除迁移。

## 阶段 8：Web 管理工作台与静态 Web Composition Root

### 8.1 `WebFeatureManifest`

#### 原始问题

导航、路由、权限、页面组件、API 地址和默认枚举散落在 `webui/src/App.jsx` 及各页面。群体记忆页面仍以内联大组件存在，新增候选、规则、冲突和运行页面会继续放大中心文件。

#### 已确认决定

- 使用静态 Web Feature Manifest。
- 不支持运行时加载远程 JS 或第三方 Web 插件。
- 后端 Registry／Descriptor 是枚举和默认值事实源。

#### Manifest 字段

- feature ID；
- route；
- nav group／label／icon；
- component lazy import；
- required capability；
- lifecycle；
- backend endpoint operation IDs；
- required Registry generation；
- feature flag；
- owner；
- order。

Web Composition Root 在构建时验证 route、nav 和 feature ID 冲突。

#### 首批迁移

- Sandbox 管理；
- 群体记忆；
- Tool 管理；
- Prompt Runtime；
- Model Route；
- Registry／Module 诊断。

### 8.2 群体记忆工作台

现有“群体记忆”页面扩展为专用 feature 目录，包含：

1. 概览；
2. 正式记忆；
3. 学习候选；
4. 释义／内容冲突；
5. 提取规则；
6. 定时白名单；
7. 学习运行记录。

#### 概览

- 已发现 canonical session；
- 是否在自动学习白名单；
- 选中 aspects；
- 下次运行；
- 游标；
- 最近成功／失败；
- 正式记忆、候选、冲突和 waiting 数；
- Prompt 注入状态。

#### 正式记忆

- 内容、释义、类型、状态；
- approval source／governance mode；
- evidence 摘要和原消息受限预览；
- 模型 run／人工审核记录；
- merge／alias／conflict；
- 注入预览。

#### 候选

- rule／model／legacy 来源；
- 规则版本；
- evidence 摘要，而不是只有 ID；
- 模型决定；
- Evidence Policy 未满足原因；
- 接受、人工编辑并接受、拒绝、合并和解决冲突。

#### 提取规则

- 只读规则内容；
- 版本、owner、正反例；
- dry-run；
- 命中、接受、拒绝、冲突和等待证据率；
- 全局或单 session 启停；
- 第一版没有任意正则保存入口。

#### 定时白名单

- 选择真实 canonical session；
- enabled；
- 7 个 aspect 复选框；
- interval 和首次 window；
- 立即提取；
- 暂停／恢复；
- 显示 kill switch 和 Prompt 注入是两个独立状态。

#### 运行记录

- trigger、cursor、aspects；
- 消息／候选／审核计数；
- Task／model route；
- Typed Failure；
- 耗时；
- 重试和 lease；
- 不展示完整 Prompt、消息正文或模型原始输出。

### 8.3 隐蔽旧开关迁移

- 从会话通用配置页移除“表达学习／黑话学习”两个旧开关。
- `use_expression` 迁为群体记忆 Prompt 注入设置的兼容别名。
- 新定时白名单和 aspects 在群体记忆工作台管理。
- 迁移期旧 API 返回 lifecycle／replacement 信息，写入请求拒绝或转发到新合同。

### 8.4 Web 验收

- `App.jsx` 不再包含群体记忆业务实现。
- Web 不维护 7 个 aspect、memory type、状态和默认值副本。
- 所有写操作有确认、幂等 request ID、审计 reason 和结果状态。
- evidence 预览受权限、长度和敏感字段限制。
- OpenAPI 变更导致生成 Client diff，可在 CI 审查。
- lint、build、关键交互测试和可访问性检查通过。

### 8.5 实施状态（2026-07-24）

阶段 8 已完成，实际落地如下：

- 新增 Runtime Module Diagnostics 类型化 Admin API，只读投影当前
  `CompositionRoot`、Module／Contribution Registry、Manifest、生命周期和健康
  状态；Composition Root 缺失时返回稳定 `unavailable`，不泄漏异常正文。
- 新增静态 `WebFeatureManifest` 与 Web Composition Root。Prompt Runtime、
  Model Route、Tool、Sandbox、群学习和 Runtime Diagnostics 已由冻结 Manifest
  组合导航和 lazy route；重复 feature ID、route、导航顺序、未知字段、缺失 owner
  或 component 均在构建／模块加载期拒绝。
- Web 不支持远程 JS、动态 import 字符串、运行时插件发现或同名覆盖。构建前会运行
  独立冲突检查脚本。
- 群学习工作台已经覆盖概览、正式记忆、学习候选、冲突、只读提取规则、
  dry-run、全局／session 规则启停、定时白名单、7 aspects、立即提取、运行记录和
  kill switch。所有枚举、默认 aspects 和 Schedule Policy 均来自后端
  Descriptor，Web 不维护副本。
- 正式记忆第一版只读展示。旧 `GroupMemory` 编辑和 Prompt 注入写接口尚未携带
  `request_id + reason`，因此工作台不暴露这些写按钮，也不会以旧接口绕过治理
  合同；Prompt 注入状态与自动学习 kill switch 明确分开展示。
- Candidate Evidence 只显示后端生成的限长、脱敏预览；工作台不展示完整消息、
  Prompt、模型原始输出或 `GroupMemory.meta_json`。
- 所有群学习写操作都有显式确认、审计 reason 和 request ID。新增
  `admin_idempotency_records` 数据库唯一账本，在业务执行前抢占 request ID：
  并发相同请求只允许一个执行，成功结果可重放，同 ID 改变 payload、执行中状态和
  既往失败均稳定拒绝。账本只保存请求 SHA-256、安全结果和稳定错误码。
- 会话通用配置页已移除旧表达／黑话学习开关；旧写入返回 lifecycle／replacement，
  `use_expression` 只保留为 `group_profile_mode` 的兼容投影。
- 近期摘要和长期摘要保留独立路由，没有因删除旧内联 `MemoryPage` 而丢失能力。
- Endpoint Contract、OpenAPI 和生成 TypeScript Client 已同步。阶段 8 组合回归为
  `132 passed`；相关 Python Ruff、Web ESLint 和 Vite production build 均通过。
  Manifest lazy chunk 生效，主 JS 约 608 KiB；仍有 Vite 500 KiB 非阻断告警，留给
  后续非业务页面拆分，不影响本阶段验收。

上述完成状态仅代表代码和本地构建门禁。没有调用生产模型、创建生产白名单、迁移
生产旧数据、启用 `group_learning.enabled`、部署或修改生产 Prompt 注入状态。

## 阶段 9：迁移、验证、灰度与回滚

### 9.1 数据库迁移顺序

1. 备份并验证 SQLite 在线快照。
2. 新增 Module／Feature／Compatibility 所需元数据表（若确有持久数据）。
3. 新增 group learning 5 张表和 `group_memories` 治理字段。
4. 分批回填 canonical `chat_stream_id` 和同事务 legacy projection；冲突或无法解析的
   行进入迁移报告，不猜测归属。
5. 迁移旧 Expression／Jargon 到 candidate。
6. 给旧 GroupMemory 补 governance 状态。
7. 建立唯一约束和索引。
8. 阶段 7B 启用前停止旧自动激活 Writer，再启用 candidate-only 观察。
9. 阶段 7C 只对白名单 session 开启正式治理写入和 Prompt 注入。
10. 旧表转只读兼容。
11. 连续 30 天和至少 1 次生产发布均满足门禁后，阶段 7D 另开删除迁移。

所有迁移使用现有幂等 migration 机制；文件 SQLite 在变更前使用
`sqlite3.Connection.backup()`，不能裸复制 WAL 主库。

### 9.2 Feature 默认状态

- 所有现有 Sandbox 开关保持原状态，不因本计划自动启用。
- `group_learning.enabled=false` 默认关闭。
- `group_learning_schedules` 默认空。
- 新私聊 classifier 合同先通过离线 Eval 和指定 session 观察，再替换旧路径。
- 新闻新 ranking 先输出对比报告，不双写正式日报缓存。
- Prompt 注入和学习调度分别启用。

“观察”只记录新旧决策差异，不执行两次外部副作用，也不把观察结果写成正式记忆。

### 9.3 分层验证

#### 静态与架构

- `python scripts/check_architecture.py`；
- 硬编码清单无未分类新增项；
- Module 依赖无环；
- Registry 冲突／冻结／generation；
- OpenAPI diff；
- Prompt canonical／runtime 同步；
- `git diff --check`；
- Python compile 和 Ruff。

#### 单元／合同

- 每个 Descriptor 和 Policy；
- Task Schema；
- Typed Failure／Resilience；
- Message／Identity Contract；
- Job lease／fencing；
- Evidence Policy；
- Rule Registry 正反例和性能；
- 新闻和私聊语义 Eval。
- Task SLO Descriptor 的 P50／P95／P99、调用量、Token、成本、失败率和熔断测试。

#### 数据库／迁移

- in-memory SQLite 迁移幂等两次；
- 文件 SQLite WAL snapshot／restore；
- 旧身份冲突；
- 旧 Expression／Jargon 数量和 Hash 对账；
- 候选／evidence 唯一约束；
- 失败回滚和孤儿恢复。

#### API／Web

- Endpoint Contract；
- 生成 OpenAPI Client；
- Admin 权限和审计；
- 群体记忆工作台 E2E；
- SQL 编辑器确实消失；
- Web defaults 来自后端 Descriptor。

#### 集成

- OneBot／Web Message Adapter；
- KT 1.3 Adapter 合同；
- Prompt Contribution 顺序；
- ToolPlan／wire schema；
- 私聊 Fast Path；
- 新闻批量仲裁；
- 群学习 scheduler／manual／tool 三入口一致。
- `private_decision`、新闻和群学习调用均经过统一 Model Route 健康候选、成本 cap
  和熔断器。

#### 部署

- 4 服务同 digest；
- 整体回滚故障注入；
- Artifact Manifest 与 Runtime revision；
- readiness／worker health；
- 非 Nanobot 容器不变；
- 单盘 loopback 配额、水位和风险标记；
- Sandbox 真实 Docker 安全矩阵保持通过。

#### 完整测试

提交前按项目约定执行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v
```

涉及 Sandbox 安全边界时，还必须在具备真实 Docker、固定镜像、AppArmor 和
sandboxd 条件的验收环境执行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
NANOBOT_RUN_DOCKER_TESTS=1 \
python -m pytest tests/test_sandbox_security.py -v
```

完整测试必须 0 failures 才能提交。真实 Docker 测试和服务端 Smoke 不能被
skipped 单测冒充为通过；环境不满足时只能标记 `BLOCKED`，不能标记 `PASS`。

### 9.4 `VerificationSuiteDescriptor` 与 `VerificationPlan`

每个 suite 声明：

- suite ID；
- owner；
- 适用 ReleaseImpact；
- 命令；
- 前置条件；
- 超时；
- 是否允许 skip；
- 所需凭据；
- 输出 artifact；
- 成功判据；
- 环境清理；
- 安全等级。

`VerificationPlan` 由 Release Impact、Feature Lifecycle 和 Artifact Profile 合成，输出实际将执行的 suite。完整 pytest 仍是提交硬门禁，定向 suite 只用于更快反馈。

### 9.5 灰度顺序

1. 阶段 0A 清单、characterization tests、golden snapshot 和语义 Task 基线。
2. 阶段 0B Registry Kernel。
3. Registry／Module／Identity 等无行为变化基础设施。
4. 删除 Admin 任意 SQL，发布结构化替代。
5. 完成 Artifact／原子发布和故障回滚。
6. 私聊新合同离线 Eval 和 SLO 通过后，给单个 session 启用。
7. 新闻新 pipeline 对比和 SLO 通过后切换。
8. 阶段 7A 部署群学习 Schema、读面和 Web 工作台，但全局关闭。
9. 停止旧自动激活 Writer，阶段 7B 开始 candidate-only 观察。
10. 迁移旧数据为待审核，不自动注入。
11. 手动审核和提取验证。
12. 阶段 7C 只给 1 个 canonical group session 加白名单，默认 4 个长期方面。
13. 观察至少一个完整 24 小时周期且 SLO／质量门禁通过后逐 session 扩大。
14. 连续 30 天零使用并跨过生产发布后，阶段 7D 才退役旧读取入口和旧表。

### 9.6 回滚

- 关闭相关 Feature Lifecycle 开关。
- 停止新 Job claim，不删除 job／candidate／memory。
- Registry 恢复前一 generation。
- 4 个固定服务整体恢复前一 Release Manifest。
- 保留新表、新字段和迁移数据，不做破坏性降级。
- 恢复旧读取路径时也不能恢复旧自动写入和自动激活。
- Prompt Runtime 冲突保留人工内容，不静默覆盖。
- Sandbox Workspace、Asset、grant 和 quota 不因应用回滚删除。

### 9.7 实施状态与验收证据（2026-07-24）

阶段 9 的本地代码验收已完成；宿主特权验证、生产部署和灰度尚未执行，不能把
本节的本地 PASS 解释为整个生产计划完成。

#### A22 结构化验证计划

- `core/release/verification.py` 已实现
  `VerificationSuiteDescriptor`、冻结的 `VerificationSuiteRegistry`、
  `VerificationPlan`、Release Impact／Feature Lifecycle／Build Profile
  交叉引用校验、确定性 generation 和 canonical Hash。
- `backend-full` 固定为不可跳过提交门禁；`sandbox-real-docker` 固定为
  `host_privileged`、不可 skip，并声明 Docker 宿主访问前置条件。
- `scripts/build_verification_plan.py` 已支持显式 path、Git diff、Feature、
  Artifact Profile、strict、Golden 写入和漂移检查。
- Runtime Module Diagnostics 只读返回 Verification Registry 和 suite
  元数据；Web Runtime Diagnostics 不维护第二份 suite 枚举。

#### 本地验证结果

| 验收面 | 状态 | 证据 |
|---|---|---|
| 后端完整测试 | PASS | `5830 passed, 7 skipped, 0 failed`，耗时 `390.70s`；跳过项为 3 个真实外网工具、3 个 slow 外部模型和 1 个真实 Docker 安全矩阵 |
| 架构与 Golden | PASS | Architecture、Behavior Baseline、Release Impact、Verification Plan、Task SLO、OpenAPI Client、Decision Rule Inventory 和 `git diff --check` 全部退出 0 |
| 决策规则清单 | PASS | 新增 CQRS `count_candidates()` 的 canonical session 精确匹配后，清单更新为 5801 条、0 扫描错误；该规则与既有查询使用同一确定性协议分类 |
| Python 静态与编译 | PASS | Ruff `E9/F63/F7/F82` 对实际项目源码、测试和脚本无错误；编译缓存写入 `/var/tmp`，没有污染仓库 |
| Compose 合同 | PASS | 开发与生产 Compose 均通过只读 `config --quiet`；生产使用测试用固定 digest，不写 `.env` |
| Web | PASS | ESLint 退出 0；Vite production build 转换 1828 个模块，主 JS 607.99 KiB；500 KiB chunk 告警仍为已记录的非阻断项 |

#### 真实 Docker 与生产状态

当前验收主机是本地 WSL／开发 Docker，不是具备生产 Sandbox 前置条件的宿主：

- 当前 UID 为 1000，不能执行要求 root 的真实安全矩阵；
- Docker SecurityOptions 只有 builtin seccomp 和 cgroup namespace，没有
  AppArmor；内核 AppArmor 状态为 `N`；
- `/srv/nanobot`、生产配置、sandboxd systemd unit 和 UDS 均不存在；
- 仅存在 `nanobot-sandbox-python:poc-20260720` 旧 PoC 镜像，不能代替生产固定
  digest、AppArmor 和 project quota 验收；
- 根文件系统当前约 737 GiB 可用、inode 使用率约 4%，只说明本地容量充足，
  不构成生产 loopback、备份或水位证据。

因此阶段 9 的状态必须拆分记录：

| 里程碑 | 状态 |
|---|---|
| 本地代码验收 | PASS |
| 真实 Docker Sandbox 安全矩阵 | BLOCKED（当前宿主缺少 root、AppArmor、sandboxd、生产数据目录和固定生产镜像） |
| 生产数据库备份／迁移 | NOT RUN |
| 四服务同 Artifact 部署与整体回滚 | NOT RUN |
| 私聊／新闻 observation 灰度 | NOT RUN |
| 群学习单 session 白名单灰度 | NOT RUN |
| 完整 24 小时观察 | NOT RUN |
| 连续 30 天兼容零使用与物理退役 | NOT RUN |

所有 Feature 默认状态保持关闭；本地验收没有调用生产模型、创建生产白名单、修改
生产数据库、部署、启用 Sandbox／群学习／私聊 active／新闻 active，或执行任何
Docker prune。

## 10. 明确非目标

- 本轮不升级 KT 1.3。
- 本轮不实现 `Kt20RuntimeAdapter`。
- 本轮不迁移到 Pi 或其他 Agent 框架。
- OpenClaw、Hermes、Maibot 只作为设计参考，不成为运行时依赖。
- 不修改 `vendor/KohakuTerrarium`。
- 不新增重型动态 Channel Registry。
- 不开放任意第三方 Python／Web 插件加载。
- 不处理数据保留期限、真实删除和 Trace TTL。
- 不重写成熟的 Sandbox 安全架构。
- 不因单盘部署缺少物理冗余而阻断 loopback；只诚实记录风险。
- 不把所有正则删除。
- 不允许 Web 任意编辑可能导致 ReDoS 的学习正则。
- 不让模型决定权限、Docker 参数、宿主路径、SQL、配额或安全 allowlist。
- 不通过自由文本错误信息改变控制流。
- 不在本阶段实现不同 Workspace 内容合并；只保留多个 session 显式绑定同一 Workspace 的模型。
- 不一次性拆空 `core/database.py`、`bridge.py` 或 `App.jsx`；按真实功能纵切。

## 11. 参考框架的取舍

### KT 2.0

吸收：

- Prompt contribution 与 lifecycle Hook 分离；
- 明确数字优先级；
- `pre_*`／`post_*`／observer 语义；
- 显式阻断错误；
- 插件 option schema。

不照搬：

- 直接升级；
- 让业务层访问 KT 私有字段；
- 用 KT 全局 Hook 绕过请求级 ToolPlan。

### Hermes

吸收：

- Observer 与 Middleware 分离；
- session／turn／request／tool call 独立关联 ID；
- manifest、版本、依赖和能力；
- 工具策略、审批、执行和结果转换的明确顺序。

不照搬：

- 动态 Python 插件发现；
- 用户／项目目录任意覆盖内建工具；
- Hermes CLI／Gateway。

### OpenClaw／Maibot

吸收：

- 渠道协议适配与业务能力分离；
- 群聊长期上下文、表达和风格是同一记忆域；
- 配置和运营界面应读取后端能力描述。

不照搬：

- 为了少量协议建立重型 Channel 插件平台；
- 把外部框架的数据模型直接复制进 Nanobot；
- 把外部项目的关键词、正则和默认阈值当作已验证事实。

## 12. 预计实施顺序和依赖

```text
阶段 0A 硬编码事实清单／Golden／性能基线
                    │
                    ▼
          阶段 0B Registry Kernel
             ┌──────┴─────────┐
             ▼                ▼
阶段 1 发布／Admin       阶段 2 Module／Identity／Message
             └──────┬─────────┘
                    │
                    ▼
        阶段 3 Registry／Task Runtime
             │
             ▼
        阶段 4 Failure／Job／DB／API／Telemetry
          ┌──┴────────┬───────────┐
          ▼           ▼           ▼
       阶段 5      阶段 6      阶段 7A～7D
       私聊         新闻         群学习分段切换
          └───────────┴───────────┘
                      │
                      ▼
                 阶段 8 Web
                      │
                      ▼
                 阶段 9 验证／灰度
```

阶段 1 的发布改造可以在阶段 2 期间独立进行，但两者都必须先使用阶段 0B
Registry Kernel。任何业务行为切换前必须先具备整体回滚能力。阶段 5、6、7
可以分别开发，不能各自重造 Task Runtime、Typed Failure、SLO 或 Registry。

## 13. 完成定义

只有同时满足以下条件，才能称为本计划完成：

### 架构边界

- 所有 Application Module 都有 Manifest、依赖和生命周期。
- KT 只通过 `nanobot_kt` Adapter 被调用，`core/` 和 `app/` 不导入 KT。
- 上述 KT 边界由 `scripts/check_architecture.py` 扫描全部 `core/`／`app/` 并在
  `quality-gate.yml` 阻断，不只是文档约定。
- Prompt、Tool、Model Route、Task、Event、Setting、Feature 和 Compatibility 都有唯一 Registry。
- Registry 具备冲突拒绝、冻结、generation、Hash 和 Admin introspection。
- Event、Observer Hook、Transform Hook 和 Policy 没有混用返回语义。
- `core/database.py`、`bridge.py`、`App.jsx` 不再吸收新增跨域业务逻辑。

### 硬编码治理

- 决策规则清单覆盖全部生产源文件，没有未分类新增项。
- 自然语言关键词和宽泛正则不再直接决定私聊、新闻和群学习最终结果。
- 协议、安全、格式和数据一致性规则保留并有性能／边界测试。
- 兼容判断只存在于 `CompatibilityRegistry` 指定 Adapter。

### 私聊

- 每条私聊最多一次语义分类。
- Casual Fast Path 只接受高置信结构化 intent。
- superuser 不参与语义分类。
- 非法模型输出进入 Typed Failure，不解释自由文本。
- 私聊分类满足冻结的调用量、P95／P99、失败率、Token 和成本预算。

### 新闻

- 所有入口使用同一类型化请求。
- 来源、权重、时效和策略不再散落。
- 关键词只能产信号，不能单独删除候选。
- 模型仲裁按批调用，不按新闻逐条调用。
- 新闻 Task 满足冻结的批大小、调用量、P95／P99、Token 和成本预算。

### 群学习

- 旧 Expression／Jargon 自动写入和自动激活完全停止。
- 正则候选必须经模型或人工审核。
- 人工修改不再模型二审。
- 自动白名单默认空，按 canonical session 配置。
- 定时任务按 `ChatLog.id` 游标增量运行。
- 7 个 aspect 的默认值只有 Descriptor 一个来源。
- 4 个长期方面审核通过后真实进入 `GroupMemory`。
- evidence、冲突、merge、alias 和治理来源可审计。
- Web 能看到候选、证据摘要、冲突、规则、白名单和运行记录。
- 7A～7D 每个切片都有独立迁移、验证和回滚证据；观察期不继续旧自动激活。

### 发布与验证

- 4 个固定服务使用同一 Artifact，并可整体回滚。
- Artifact Manifest 可以追溯 Git、KT、依赖、Prompt、迁移、digest 和验证结果。
- Admin 任意 SQL 已删除。
- OpenAPI Client、Web lint/build、架构检查、完整 pytest 全部通过。
- 完整 pytest 为 0 failures。
- 单盘 loopback 和 Sandbox 真实安全边界均有独立验收证据。

## 14. 问答覆盖索引

本节只用于反查此前决定是否进入计划，不新增设计。

### 14.1 架构与抽象化 27 项

| 问答项 | 决定 | 计划位置 |
|---|---|---|
| A01 | 删除 Admin 任意 SQL | 5、8.1.1 |
| A02 | `ReleaseImpactRegistry` | 5、阶段 0B、8.1.2 |
| A03 | 4 服务原子发布／整体回滚 | 5、8.1.3 |
| A04 | canonical `chat_stream_id` | 5、8.2.2、7.5、7.7 |
| A05 | `ModelRouteDescriptorRegistry` | 5、8.3.4 |
| A06 | Task Runtime／`TaskContract` | 5、8.3.5 |
| A07 | 确定性规则与语义判断分离 | 3.2、6、阶段 5～7 |
| A08 | `PromptContributionRegistry` | 5、8.3.2 |
| A09 | Registry 冲突／冻结／generation | 3.3、阶段 0B、8.3.1 |
| A10 | Event／Hook／Policy 分离 | 8.3.6 |
| A11 | 类型化配置 | 8.3.7、阶段 6～8 |
| A12 | 安全内容 Rule Engine | 8.4.1 |
| A13 | Schema-first LLM Output | 8.3.5、阶段 5～7 |
| A14 | ToolRegistration／ToolProfile | 8.3.3 |
| A15 | Typed Failure／Resilience | 8.4.2 |
| A16 | Durable Job Kernel | 8.4.3、7.7 |
| A17 | CQRS-lite | 8.4.4 |
| A18 | Endpoint Contract／OpenAPI Client | 8.4.5、阶段 8 |
| A19 | 类型化身份 | 8.2.2 |
| A20 | Telemetry Contract | 8.4.6 |
| A21 | Module Manifest／Composition Root／KT Adapter | 阶段 0B、8.2.1 |
| A22 | Verification Suite／Plan | 9.4 |
| A23 | Build Profile／Artifact Manifest | 8.1.3、9.4 |
| A24 | Web Feature Manifest／Composition Root | 阶段 8 |
| A25 | 正式支持单盘 loopback | 4.3、9.3、10 |
| A26 | Feature Lifecycle／Compatibility Registry | 8.3.7 |
| A27 | Message Contract，不建 Channel Registry | 8.2.3、10 |

### 14.2 原始 Nanobot 架构审查项

| ID | 原问题 | 处置 |
|---|---|---|
| R01 | async 路由内同步 SQLAlchemy 阻塞事件循环 | 已由模块化提交完成；4.1 保留回归 |
| R02 | 多进程 SQLite WAL、短 busy timeout、无 checkpoint owner | 已有 `sqlite_maintenance`；纳入 Job／Telemetry |
| R03 | shutdown 后 Bridge 可惰性复活 | 已由 Agent Runtime lifecycle 修复；Composition Root 防回归 |
| R04 | 私聊／群聊异常路径丢原始入站消息 | 已由 claim／recovery 修复；不重造 |
| R05 | Compose 浮动 tag、无资源和健康加固 | prod digest、加固、四服务原子发布和整体回滚均已完成 |
| R06 | `clients/` 与 `core/` 边界模糊 | `clients/` 定位为 outbound Adapter，只允许依赖 Contract；阶段 2、3 收敛 route 业务依赖 |
| R07 | `core/database.py` God Module | `core/db/` 已起步；8.4.4 按垂直切片迁移 |
| R08 | 工具实现位于 Prompt 目录 | 8.3.3 迁到应用服务／`nanobot_kt` 薄 Adapter |
| R09 | Persona 双路径 | 8.3.2 收敛为单一 Prompt Contribution |
| R10 | Legacy Prompt 化石易误改 | 已迁 `docs/legacy-prompts/`，明确不恢复 |
| R11 | 通用 CI 不足 | `quality-gate.yml` 已完成；9.4 结构化 suite |
| R12 | SQLite／sandboxd 单故障域 | 当前单机规模接受并记录；不在本轮引入集群 |

### 14.3 硬编码与正则治理问答

| ID | 已确认内容 | 计划位置 |
|---|---|---|
| H01 | 先拉取当前代码再审计，不能照旧报告直接改 | 文档基线、4、8.0 |
| H02 | 找出全部生产硬编码判断和正则路由 | 6、阶段 0 |
| H03 | 明确规则保留，不在“全硬编码”和“全模型”两个极端间二选一 | 3.1、3.2、6 |
| H04 | 参考 OpenClaw／Hermes／Maibot，但不照搬 | 11 |
| H05 | 不靠关键词做自然语言领域分类 | 阶段 5、6、7 |
| H06 | 不通过错误字符串改变控制流 | 8.4.2、阶段 5 |
| H07 | 正则可做召回／提取信号，不能直接激活语义结果 | 3.2、7.6～7.9 |
| H08 | 配置、默认值、枚举和路由不能在 Web／API／Scheduler 重复 | 3.3、8.3.7、8 |
| H09 | 删除前必须列出“删什么／留什么” | 7、阶段 5～7 |

### 14.4 私聊 Timing 问答

| ID | 已确认内容 | 计划位置 |
|---|---|---|
| P01 | 私聊只进行一次语义分类 | 5.2～5.6 |
| P02 | 保留数值 TimingSignals／Cooldown | 5.5 |
| P03 | 删除 `_infer_effort` 和关键词 intent 级联 | 5.4 |
| P04 | Casual Fast Path 保留，但必须高置信和有限 intent | 5.2、5.6 |
| P05 | 模板层不再二次读取原始文本分类 | 5.4～5.6 |
| P06 | superuser 只影响能力，不影响语义 | 5.2、5.7 |
| P07 | 非法／低置信输出进入 Agent，不猜 casual | 5.2、5.6 |
| P08 | 旧自由文本 parser 退出主路径 | 5.4 |

### 14.5 新闻与 AI 日报问答

| ID | 已确认内容 | 计划位置 |
|---|---|---|
| N01 | 统一类型化 `NewsRequest` | 6.2、6.3 |
| N02 | 复用当前 `AiDailyRequest`，不造平行 DTO | 6.2 |
| N03 | 日期／URL／RSS／HTML 正则保留 | 6.2、6.7 |
| N04 | 来源 URL／启用／权重／时效迁出散落字面量 | 6.4 |
| N05 | AI 关键词和负面词只作评分信号 | 6.2、6.4 |
| N06 | 关键词不能直接删除候选 | 6.2、6.6 |
| N07 | 只对冲突／未知候选做模型仲裁 | 6.5 |
| N08 | 不为每条新闻调用模型 | 6.5、6.8 |
| N09 | 清理写死年份和多份 36／48／72 小时窗口 | 6.6 |
| N10 | `35b8904` 已完成日报模型 route，不重复实现 | 4.2、6.5 |

### 14.6 表达／黑话／群体记忆问答

| ID | 已确认内容 | 计划位置 |
|---|---|---|
| G01 | 旧学习开关无效、滚动窗口重复、自动 active 是现状缺陷 | 7.1 |
| G02 | 退役旧 Expression／Jargon 自动链 | 7.2、7.11 |
| G03 | 表达、黑话、风格统一进 `GroupMemory` | 7.2、7.5.6 |
| G04 | 旧数据迁移，不直接删除 | 7.11 |
| G05 | 正则常态提取，只产生候选 | 7.2、7.6 |
| G06 | 群上下文、正则候选、evidence、近似记忆同批交给模型 | 7.2、7.8 |
| G07 | 模型审核、修正、拒绝并补充新候选 | 7.2、7.8 |
| G08 | 同一次调用决定 new／merge／alias／conflict／reject | 7.2、7.8 |
| G09 | 不做第二次模型审核 | 7.2 |
| G10 | 人工审核优先，人工修改不再模型复审 | 3.4、7.10 |
| G11 | 候选与正式记忆分表 | 7.5 |
| G12 | `group_learning_stream_states` | 7.5.2 |
| G13 | `group_learning_candidates` | 7.5.3 |
| G14 | `group_learning_evidence` 唯一 evidence | 7.5.4 |
| G15 | `group_learning_runs` | 7.5.5 |
| G16 | schedule 行就是显式白名单 | 7.5.1、7.7 |
| G17 | `LearningSignalRuleRegistry` 代码所有、版本化、冻结 | 7.6 |
| G18 | Web 可查看／dry-run／指标／启停，不能任意改正则 | 7.6、8.2 |
| G19 | `BAD_LEARN_TERMS` 从各 Registry 派生 | 7.6 |
| G20 | 精确 Hash 去重，FTS／n-gram／embedding 只召回 | 7.8 |
| G21 | 不用相似度阈值直接合并正式记忆 | 7.8 |
| G22 | 同词不同释义进入冲突组，不覆盖 | 7.8、7.13 |
| G23 | 模型批内也要归并 | 7.8 |
| G24 | 7 个方面固定为 topics／expressions／slang／style／titles／quotes／quality | 7.3 |
| G25 | 定时默认只勾选可注入的前 4 项 | 7.3、7.4 |
| G26 | `group_analysis` 仍是工具，只增加可选 `aspects` | 7.4 |
| G27 | Scheduler／Web／Tool 复用共享服务，不解析 HTML ToolResult | 7.4 |
| G28 | `analyze_group()` 只创建选中分支 | 7.4 |
| G29 | expressions／slang／style 合并为 1 个学习 Task | 7.4 |
| G30 | 审核后的长期方面必须真实写入 `GroupMemory` | 7.2、7.13 |
| G31 | 自动提取只对 canonical session 白名单 | 7.7 |
| G32 | 白名单默认空，无 superuser／活跃群绕过 | 7.7 |
| G33 | 白名单同时约束正则提取和定时模型总结 | 7.7 |
| G34 | 管理员立即提取不受白名单限制 | 7.7 |
| G35 | 学习和 Prompt 注入开关独立，并有全局 kill switch | 7.7、7.12 |
| G36 | 默认 24 小时；首次回看 24 小时 | 7.5.1、7.7 |
| G37 | 后续按 `ChatLog.id` 游标，不用重叠窗口 | 7.7 |
| G38 | 可带 10～20 条 context-only，但不重复作 evidence | 7.7 |
| G39 | 无新消息不调用模型；少于 3 条暂缓且不推进游标 | 7.7 |
| G40 | 有消息数和 Prompt 字符预算 | 7.7 |
| G41 | 失败不推进，重启后按游标继续 | 7.7 |
| G42 | 手动补提取默认不倒退自动游标 | 7.7 |
| G43 | topic Evidence Policy：2 条、2 人 | 7.9 |
| G44 | expression Evidence Policy：2 条 2 人，或同人跨批 3 次 | 7.9 |
| G45 | slang Evidence Policy：明确释义 1 条，或一致用法 2 条 | 7.9 |
| G46 | style Evidence Policy：3 条、2 人 | 7.9 |
| G47 | titles／quotes／quality 不写长期记忆 | 7.3、7.9 |
| G48 | 模型 accept 但证据不足为 `waiting_for_evidence` | 7.9 |
| G49 | Evidence Policy 代码所有、版本化，Web 第一版只读 | 7.9 |
| G50 | 人工可从零创建、编辑、合并和解决冲突后直接 active | 7.10 |
| G51 | 记录 approval source、governance mode、Hash、审核人／时间／动作／model run | 7.5.6、7.10 |
| G52 | 后台不得覆盖 human-managed，只能追加 evidence／新冲突 | 7.10 |
| G53 | 可显式交还自动管理 | 7.10 |
| G54 | 旧样例不能伪装为 `ChatLog` evidence | 7.5.4、7.11 |
| G55 | 旧自动 active 变为 pending model review | 7.11 |
| G56 | 无新版审核凭据的旧 GroupMemory 降为 review／manual-only | 7.11 |
| G57 | 旧表先只读墓碑，稳定和回滚后才删除 | 7.11 |
| G58 | Web 包含概览、正式记忆、候选、冲突、规则、白名单、运行 | 8.2 |
| G59 | Web 显示真实 evidence 摘要，不只显示 ID | 8.2 |
| G60 | 学习开关从通用配置页迁出，注入开关独立保留 | 8.3 |

### 14.7 Sandbox 管理与单盘问答

| ID | 已确认内容 | 计划处置 |
|---|---|---|
| S01 | 授权按 canonical session，不按固定 owner | 已完成；4.3 防回归 |
| S02 | 能力分级为 off／Workspace／Assets／Exec | 已完成；不重做 |
| S03 | Workspace 配额可以独立修改 | 已完成；不重做 |
| S04 | project ID 和 quota mapping 以数据库为事实源 | 已完成；不恢复 TSV 日常写入 |
| S05 | 部署脚本只管基础设施，不管用户授权 | 已完成；不恢复 owner 参数 |
| S06 | private superuser 不能绕过显式 grant | 已完成；安全回归 |
| S07 | 未来可让多个 session 共享 Workspace | 8.2.2 保留显式关系；内容合并非目标 |
| S08 | 没有第二块盘时允许单盘 loopback | 已完成；4.3、9.3 |
| S09 | 物理硬盘损坏不是所有功能的无限阻断理由 | 4.3、10：风险记录，不冒充灾备 |

### 14.8 框架迁移问答

| ID | 已确认内容 | 计划处置 |
|---|---|---|
| F01 | 未来可能从 KT 迁到 Pi | 依赖 AgentRuntimePort；本轮不实施 |
| F02 | 原生迁移不应改业务层 | 8.2.1 的 Adapter 合同 |
| F03 | 当前先以固定 KT 1.3 为基线，不追最新版 | 10、11 |
| F04 | KT 2.0 可能修复 Bug，但直接升级会重新泄漏私有字段 | 11，另开升级任务 |
| F05 | OpenClaw／Hermes 是自有框架参考，不直接复用为 Nanobot Runtime | 10、11 |
| F06 | 本轮无需修改 KT vendor | 10 |
| F07 | 不为消息格式建立重型 Channel；类型化 Message Contract 足够 | 8.2.3 |

## 15. 经审查固定的 5 个实施决定

用户要求修订后直接按顺序实施。为避免实现阶段再次临场猜测，以下采用保守且
可回滚的固定决定：

1. `group_analysis` 工具省略 `aspects` 时，在兼容窗口内保持现有
   `topics/titles/quotes/quality`；新定时配置和 Web 立即提取默认
   `topics/expressions/slang/style`。两类入口分别统计使用量，不把新默认反向套到
   旧显式工具调用。
2. 旧 Expression／Jargon 的 `checked=true` 没有审核人、审核时间和动作凭据时，
   不视为人工已审核；只有 `AdminAuditLog` 能证明人工动作时才迁为
   `human_managed`，否则进入 `pending_model_review`。
3. `GroupMemory.chat_stream_id` 是唯一事实源；旧 `group_id` 是同事务生成的兼容投影。
   迁移期读取优先 canonical，只对未回填旧行 fallback；字段冲突 fail closed，
   不做破坏性原地改名或独立双写。
4. 新闻来源 canonical 配置放版本化资源文件；数据库只保存受 SettingSpec／Schema
   限制的 operator override。有效显式 override 覆盖 canonical 默认值，运行时只读
   冻结 Registry snapshot，并保留 provenance。
5. 旧表、旧 alias、legacy fallback 和旧 API 至少保留 30 个连续自然日，并跨过
   至少 1 次完整生产发布。只有连续 30 天使用量为 0、迁移对账、回滚演练、
   备份恢复演练全部通过，且 Release Owner、群记忆 Data Owner、生产 Operator
   三方确认后，才允许独立删除迁移。

这些决定不授权降低安全边界或跳过 Feature 灰度。实施证据推翻某项前提时，停止
对应切片并更新计划，不静默改变其他决定。
