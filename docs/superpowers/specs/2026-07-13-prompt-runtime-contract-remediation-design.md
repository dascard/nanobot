# Prompt Runtime 契约整改设计

## 状态

- 日期：2026-07-13
- 状态：设计待按实施计划执行
- 范围：Nanobot Server 的 Prompt Runtime、终结动作来源、任务模板启用契约、请求级工具计划、私聊工具分级与审计
- 前置于：`docs/superpowers/specs/2026-07-12-session-guidance-design.md`
- 不涉及：QQbot 端、身份模板安全规则正文、自定义角色人设正文

## 背景

一次真实私聊请求显示，后端已经把当前发送者识别为超级用户，但最终模型仍回答
“不知道”。同一请求还暴露出几项彼此关联的契约偏差：

- 超级用户事实只通过可自定义的身份模板间接表达，代码生成的
  `<runtime_context>` 没有独立、稳定的 `is_super_user` 字段；
- `strict_audit=True` 没有强制保护 `base_contract`、平台策略、
  `runtime_context` 和 `identity_context` 等核心 section；
- 工具模板说明在 ToolPlan 构造和最终请求过滤阶段各 overlay 一次，导致同一工具
  说明重复，真实请求体无谓增大；
- Prompt Compiler 统计的 token 只包含 messages，哈希虽然包含 tools，但 tools
  仍会在出站前改变，而且 Compiler 使用的 schema 还含不会进入 wire payload 的管理
  元数据；
- 私聊把 `session_id` 回填进 `group_id`，既污染运行时事实，也可能误命中 group
  scope 的 `ToolOverride`；
- 超级用户身份同时被当作“权限上限”和“本轮必须启用全部工具”的信号，简单问题
  也会携带完整高权限工具集。
- Final Action 只凭 `role=tool` 和字符串 marker 判定，未校验真实工具名、
  `tool_call_id` 及前置 `assistant.tool_calls`；普通工具可伪造 `reply/no_reply`，
  普通工具或模型文本也可伪造日报 HTML 终结结果；
- 运行时动态元数据直接拼进 `<runtime_context>` system message，昵称等字段可以提前
  闭合标签并注入伪 section；同一原始分类输入还会同时进入 system 与 user；
- 关键任务模板只有“变量白名单”而没有“必需变量和输出 schema”启用门禁。当前工作
  区的 `memory_extract` 与 `timing_gate` 正文已经包含核心契约，但服务器旧 Runtime
  副本仍可永久覆盖 canonical default，非法 Memory 输出还会被当成合法空候选并标记
  日志已处理；
- `python_sandbox` 的 CPython `exec` 不是安全隔离边界：暴露的模块、连接和已加载对象
  可被用于重开可写 SQLite 连接或执行子进程；`PRAGMA query_only` 只保护预开连接；
- `persona_update` 信任模型传入的 `user_id`，没有绑定当前会话 actor，允许模型把画像
  更新操作指向其他用户；其 `instructions` 参数也没有执行语义；
- `ai_daily` 虽声明 `freshness/target_date`，但当前检索管线只从 query 猜测时间，显式
  参数没有贯穿缓存键、收集和筛选。

这些问题不应通过继续修改身份模板正文来掩盖。鉴权事实、工具集合、Prompt flow、
请求指纹和 token 统计都必须由代码契约决定，并能由测试从最终 wire 形态反向证明。

## 目标

- 把超级用户判断作为显式布尔事实，从路由/Bridge 一路透传到 Prompt Compiler。
- 无论自定义身份模板是否引用变量，代码生成的运行时 JSON 都明确输出布尔字段
  `"is_super_user": true|false`。
- `strict_audit=True` 对核心模板、平台/分支策略、运行时事实、身份和最终用户事件
  做唯一性、身份、状态、角色、索引和顺序校验。
- 每个请求只构造一次最终工具 schema；最终出口只能裁剪，不能再次读取模板或修改
  schema 正文。
- `prompt_sha256`、messages token、tool schema token 和总 token 对应同一份
  Nanobot 最终请求 envelope。
- 私聊的 `group_id` 在 Bridge、Prompt adapter 和工具覆盖解析链路中始终为空。
- 超级用户身份只决定可达到的权限上限；当前消息意图决定本轮实际
  `runtime_preset` 和工具集合。
- 私聊分类结果缺失或异常时收敛到 `lightweight`，不能静默回退为 `full`；群聊
  不使用私聊分类器，继续保留既有默认。
- 为后续全会话 `session_guidance` 提供可依赖的严格 Prompt Runtime 基线。
- Final Action 和富终结结果只接受经过工具名、调用 ID 和前置声明三方校验的真实工具
  结果；任何普通模型文本或其他工具输出都不能获得终结权限。
- system 只保留代码确认的稳定事实；不可信展示元数据以强转义 JSON 只出现于最后一条
  user event，并由 strict audit 验证结构。
- 用代码拥有的 TaskContract 保护关键任务的必需变量、输入角色、输出 schema 和失败
  策略；坏 Runtime 不启用、不覆盖，按安全链路回退。
- 在没有 OS 级隔离实现前 fail closed 禁用任意 Python 执行；只读 SQL 继续走已有受控
  查询工具。
- 将会写入用户数据的画像工具绑定当前运行时用户；缺上下文或目标不一致时拒绝执行。

## 非目标

- 不修改 QQbot 入站、推送、CQ renderer 或 QQbot 配置。
- 不修改 `data/prompts_v2/chat/identity_context.md` 或默认身份模板中的安全规则、
  角色设定、立绘提示词和说话方式。
- 不自动覆盖、合并或迁移服务器已有自定义身份模板正文。
- 不在本阶段拆分身份 Prompt 体量，也不把图片人设改成按需注入。
- 不在本阶段实现高风险工具的二阶段人工 approval 状态机。
- 不在本阶段重新设计可安全恢复任意 Python 执行的容器、seccomp 或微虚拟机运行时；
  本阶段只做 fail-closed 阻断和后续恢复接口边界。
- 不在本阶段实现画像“纠正、删除、重建”的新业务语义；未实现的参数从可调用 schema
  移除，提示词正文由后续专项统一修改。
- 不改变唯一超级用户环境变量的名称、配置格式或历史清理结果。
- 不把鉴权事实交给模型判断，也不接受用户消息、历史、画像或
  `session_guidance` 覆盖该事实。

## 已验证的现状与根因

| 问题 | 当前事实 | 根因 |
|---|---|---|
| 模型无法稳定回答超级用户身份 | 路由已进入超级用户 `serious/full` 分支，完整工具集也证明鉴权为真 | Prompt 没有不可被模板省略的代码事实；身份模板只是可变展示层 |
| 严格审计漏检核心 section | 删除 base/runtime/identity 后，现有 audit 仍可返回成功 | audit 只强制 persona、runtime tool、current user 和 branch policy |
| 工具说明重复 | ToolPlan schema 已 overlay，SDK/HTTP 出口过滤又 overlay | `filter_payload_tools()` 同时承担裁剪和内容重写 |
| 请求哈希与真实 wire tools 不同 | ToolPlan schema 含管理元数据，KT 转换只保留 type/function；出口还会二次 overlay | 没有请求级 wire schema 快照和统一 metrics 边界 |
| token 低估 | Compiler 只对 message content 调用 `estimate_tokens()` | tool schema 完全没有纳入统计 |
| 私聊出现伪 group_id | `meta.get("group_id", session_id)` 在字段缺失时回填私聊 session | 默认值与 chat type 无关，adapter 也未二次清空 |
| 简单超级用户问题携带完整工具集 | `_infer_effort()` 对所有超级用户请求至少返回 `full` | 授权身份与请求相关性被合并成一个判断 |
| 普通工具可伪造最终回复 | Final Action parser 不读取 tool name/call ID/assistant declaration | 终结权限绑定字符串内容而不是真实调用来源 |
| 普通工具或模型文本可伪造 HTML 终结 | Bridge 只识别 CSS marker/HTML 外形，并有初次和重试字符串旁路 | 富结果没有独立类型和来源证明 |
| runtime 标签可逃逸 | 动态值以 `key: raw value` 拼入 system | 没有结构化编码、角色隔离和长度上限 |
| 分类输入重复进入高权限角色 | 泛型任务 renderer 先插值 system，再复制为 user | 没有声明 payload variable 的角色契约 |
| 旧关键任务模板可永久失效 | Runtime body 永久覆盖 default，校验只拒绝未知变量 | 没有 required variables/output contract 启用门禁 |
| Memory 解析失败被静默消费 | 任意解析结果最终只看 `not candidates` | 合法空结果与契约失败没有类型区分 |
| Python 沙箱可越权写库/起进程 | 受限 builtins 仍暴露模块、原始对象和 CPython 对象图 | 语言级 blacklist 被误当成 OS 隔离 |
| 画像工具可跨用户写入 | `user_id` 完全来自模型参数，工具不接收 ToolContext | actor 与 target 没有服务端绑定 |
| 日报显式时间参数无效 | `freshness/target_date` 未进入 pipeline 和 cache key | schema 与执行器没有自动契约测试 |

## 核心设计原则

### 1. 事实、策略和内容分层

```text
代码鉴权事实
→ 请求级权限上限
→ 当前意图所需 runtime preset
→ 最终 ToolPlan / wire schema
→ Prompt 内容与审计
→ 模型请求
```

模型只消费最终结果，不参与鉴权、权限提升或工具解锁。可编辑模板和会话指导都属于
内容层，不能成为事实源。

### 2. 一次构造，多处只读

`ToolPlan.sent_tool_schemas` 是请求级最终 wire schema 快照。Prompt Compiler、KT
native tool adapter、Prompt render trace 和真实 SDK 请求都读取这份快照，不在各自
阶段重新构造或追加说明。

### 3. 严格审计验证结构，不审查自定义正文

本阶段审计 section 的 node identity、template key、runtime key、message role、状态、
索引和相对顺序，但不解析身份模板正文中的自然语言安全规则，也不尝试判定角色
设定是否合理。

### 4. 哈希和统计绑定同一 envelope

统一 envelope 定义为 Nanobot 完成消息清理和工具硬裁剪后、加入 model、temperature、
stream 等传输参数前的：

```json
{
  "messages": [],
  "tools": []
}
```

`prompt_sha256` 只覆盖这个 envelope。provider 特有 cache marker 或传输字段不属于
Prompt Runtime 指纹；如果 provider 必须改写 messages，则该转换必须在独立 provider
trace 中记录，不能静默冒充同一个 Prompt hash。

### 5. 终结权限绑定来源而不是内容

工具输出中的 marker、JSON 字段、HTML 标签和 CSS class 都只是数据，不能授予终结
权限。只有 KT conversation 中能证明以下关系的结果才是可信工具结果：

```text
assistant.tool_calls[id].function.name
== tool.tool_call_id 对应的声明名称
== tool.name
```

同一调用 ID 只能消费一次。匿名、孤儿、错名、重复或跨 assistant 边界的 tool result
全部忽略并进入契约失败路径。

### 6. 不可信数据不进入 system 指令面

代码鉴权事实和 canonical IDs 可以进入 system runtime JSON；昵称、会话显示名、触发
原因、bot 别名等展示元数据不进入 system，而是作为数据 JSON 与当前输入一起放在最后
一条 user event。编码防止结构逃逸，角色隔离防止不可信文本获得 system 权限。

## 详细设计

### Final Action provenance 与富终结结果

#### 唯一可信工具结果迭代器

在 `nanobot_kt/reply_contract.py` 建立唯一来源验证入口：

```python
@dataclass(frozen=True)
class VerifiedToolOutput:
    tool_name: str
    tool_call_id: str
    content: str
    message_index: int


def iter_verified_tool_outputs(messages: list[Any]) -> Iterator[VerifiedToolOutput]:
    ...
```

按 conversation 原顺序处理：

- 只从前置 `assistant.tool_calls` 建立待消费的 `id -> function.name`；
- tool message 必须同时具有非空 `name` 和 `tool_call_id`；
- tool name 必须与声明名称相同；
- tool result 必须出现在其声明之后、下一轮独立 user 边界之前；
- 每个 ID 只消费一次，重复声明或重复结果都不产生可信输出；
- 错误诊断只记录原因码、工具名和脱敏后的调用 ID hash，不复制工具正文。

`extract_reply_tool_output()` 和 `count_final_action_tool_calls()` 必须复用这个迭代器，
不得维护第二套宽松判定。

`reply` 只接受非空回复 payload；`no_reply` 只接受 `no_reply=true`。动作与工具名不一致
时拒绝，即使 marker 结构合法。`ReplyToolExtraction` 携带验证后的 `tool_name` 和
`tool_call_id`，供 trace 绑定真实来源。

#### 独立富结果类型

日报和群分析不再冒充 `reply`，使用独立 wire envelope 和运行时类型：

```python
@dataclass(frozen=True)
class RichTerminalOutput:
    html: str
    tool_name: str
    tool_call_id: str
    report_kind: Literal["ai_daily", "group_analysis"]
```

工具输出 envelope 使用独立 `NANOBOT_RICH_OUTPUT` 类型字段，并校验固定映射：

```text
ai_daily      -> ai_daily / news-brief
group_analysis -> group_analysis / group-analysis-report
```

CSS class 只验证内容形态，不授予权限。`RichTerminalOutput` 只能由
`VerifiedToolOutput` 构造，并以类型贯穿 `ModelLoopResult`、reply contract check 和最终
settlement；最终发送边界才取 `.html`。

以下兼容旁路全部关闭：

- 仅凭 response 看起来像 HTML 就判成功；
- 重试 response 看起来像 HTML 就判成功；
- 把任意字符串伪造成匿名 tool message 再解析 marker；
- `retry_marker_json_repair`；
- 普通 assistant JSON 作为正常 Final Action；
- 没有 tool name/call ID 的 reply runtime cache 作为可信最终证据。

项目当前生产使用 native tool format，因此 fail closed 不需要兼容无法提供 provenance
的 text/bracket 工具结果。

### 运行时元数据结构化边界

#### system runtime facts

`<runtime_context>` 改为单个 JSON object，只包含代码或边界层确认的稳定事实：

```json
{
  "chat_type": "private",
  "group_id": "",
  "is_super_user": false,
  "platform": "qq",
  "session_id": "private_placeholder",
  "timezone": "Asia/Shanghai",
  "user_id": "placeholder-user"
}
```

空的私聊 `group_id` 可以省略；布尔值保持 JSON boolean，不能转回可由模板覆盖的自然
语言事实。

#### user message metadata

以下字段从 system runtime section 移到最后一条 user event 的 `<message_meta>` JSON：

```text
sender_name, session_name, trigger_reason, timing_decision,
current_message_id, bot_name, bot_aliases, self_id, bot_id
```

current user event 固定为：

```text
<message_meta>
{...}
</message_meta>
<user_input>
...
</user_input>
```

多模态输入把 metadata 作为第一段 text part，图片和用户文本仍保持原顺序语义；元数据
只出现一次。

统一 JSON helper 使用稳定排序和紧凑分隔符，序列化后把字面 `&`、`<`、`>`、
U+2028、U+2029 转义为 `\u0026`、`\u003c`、`\u003e`、`\u2028`、
`\u2029`。ID 上限 128 字符，名称 160，trigger/timing 64，aliases 最多 10 个且每个
80 字符。编码错误 fail closed，不回退到逐行拼接。

`persona_reference` 不再把原始 `user_id` 写入未转义 XML 属性；至少使用同一 JSON
编码表达引用实体。strict audit 校验 runtime/message metadata 标签唯一、body 可解析为
JSON object、关键字段类型正确，不能只做字符串计数。

### 关键任务模板启用契约

当前仓库的 active `memory_extract` 和 `timing_gate` 已包含关键变量，本阶段不改写其提示
词正文。代码新增不可由 Runtime frontmatter 覆盖的 `TaskContract`：

```python
@dataclass(frozen=True)
class TaskContract:
    required_variables: frozenset[str]
    payload_variables: frozenset[str]
    render_mode: str
    output_contract_id: str
    template_failure_policy: str
    output_failure_policy: str
```

首批契约：

| task | 必需变量 | 不可信 payload | 输出契约 | 失败策略 |
|---|---|---|---|---|
| `tasks/memory_extract` | `conversation, existing_memory` | 两者 | `memory_candidates_v1` | Runtime → default → 代码 fallback；失败保留未处理日志 |
| `tasks/timing_gate` | `pending_text` | `pending_text` | `timing_gate_v1` | Runtime → default → 稳定代码 fallback；仍失败则 `no_reply` |
| `tasks/classifier_legacy` | `system_prompt, message` | `message` | `legacy_reply_v1` | Runtime → default → 代码 fallback；真实消息只在 user |

其余 live task 必须登记契约或显式声明 `code_fallback_only`，并由 completeness test 锁定。

校验发生在三个边界：

- Admin create/save 在写文件前拒绝缺失必需 placeholder 的正文，旧文件字节不变；
- live render 校验模板引用、调用值和 payload 角色；非法 Runtime 不启用，也不自动覆盖；
- bootstrap 只报告 active/default/fallback 状态，关键 task 没有安全 fallback 时 fail closed。

为了不修改现有模板正文，system 中的 payload placeholder 只替换为稳定代码引用“见下一
条 user 消息”，真实原文只发送一次 user role。`classifier_legacy` 和 `timing_gate` 不再
把同一输入复制进 system。

Memory 输出必须区分：

- `{"candidates": []}`：合法空结果，可以标记日志 processed；
- 空正文、garbage、`{}`、顶层 list、`candidates` 非 list：契约失败，不能标记 processed；
- 有候选时只有状态机和数据库提交成功后才标记 processed。

### Python 执行 fail-closed 与画像 actor 绑定

#### Python sandbox

当前实现无法通过 Python module blacklist 形成安全边界。已经验证的直接绕过包括：

- 通过 `PRAGMA database_list` 获得路径并用 `sqlite3.connect()` 重开可写连接；
- 使用 raw connection 执行 `ATTACH`、extension 相关操作；
- 通过 CPython 对象图找到已加载的 `Popen`，在 import 被拦时仍执行子进程。

因此在完成 OS 级隔离前：

- `AnalysisSandbox.execute_python_analysis()` 固定 fail closed，不执行任何用户代码；
- KT `python_sandbox` 和 legacy `run_python_analysis` 都返回稳定的不可用错误；
- ToolPlan 默认不发送 `python_sandbox`，Admin 即使 override 也不能越过硬禁用；
- `run_query()` 继续保留，但统一只允许单条只读 SELECT/WITH，并在 SQLite authorizer
  层拒绝写入、ATTACH、危险 PRAGMA 和 extension；
- 错误不得返回数据库绝对路径或内部对象信息。

长期恢复需另立设计：宿主先执行受验证、限行、限列、限时的只读 SQL，把有界 JSON
rows 传给无数据库、文件系统和网络权限的 OS 隔离分析进程。

#### persona_update

`PersonaUpdateTool.needs_context=True`，目标用户只从
`session.extra.nanobot_runtime_context.user_id` 取得。为兼容旧调用可以暂时保留
`user_id` 参数，但只允许为空或与 actor 完全相等；上下文缺失、不一致或私聊/群聊事实
不完整时 fail closed。

本阶段不伪造 `instructions` 的纠正/删除语义：从执行 schema 和静态 tool schema 中移除
该参数，现有后台提取流程只做当前已实现的“重新分析当前用户日志”。Admin 跨用户画像
管理继续走独立鉴权 API。

### ai_daily 显式时间参数贯穿

在进入缓存和检索前规范化：

- `freshness` 只接受 schema 枚举；
- `target_date` 必须是合法 `YYYY-MM-DD`，只有 `freshness=custom` 时必需；
- `today/latest/week/custom` 生成显式查询窗口并传入收集、筛选和缓存键；
- `target_date` 不再依赖把日期拼回自然语言 query 才生效；
- refresh/no_cache 仍只控制缓存，不改变时间语义。

契约测试从工具 schema 构造参数，捕获 pipeline 调用，证明每个公开参数被消费或明确
拒绝。`outreach_judge` 的示例文案属于本轮排除的提示词正文；解析器继续只接受单一
枚举值，不能为了错误示例放宽。

### 显式超级用户运行时事实

在以下两个数据对象增加布尔字段，默认值为 `False` 以兼容现有直接构造测试：

```python
PromptRuntimeInput.is_super_user: bool = False
PromptCompileRequest.is_super_user: bool = False
```

事实链固定为：

```text
API 路由现有 is_superuser 判断
→ Bridge metadata.is_superuser
→ PromptRuntimeAssemblyContext.is_super_user
→ PromptRuntimeInput.is_super_user
→ PromptCompileRequest.is_super_user
```

Bridge 传入的布尔值是本轮唯一事实源。`build_template_values()` 和
`build_runtime_context()` 必须读取同一个 `request.is_super_user`，不得在编译阶段
再次按 sender ID 读取环境变量并产生第二个判断。

代码生成的 `<runtime_context>` JSON 始终包含：

```json
{"is_super_user":true}
```

或：

```json
{"is_super_user":false}
```

`build_identity_context()` 仍可把同一值提供给 `{{ is_super_user }}`，但身份模板是否
引用该变量不影响运行时事实存在。测试必须使用一个完全不含超级用户占位符的自定义
身份模板，证明事实仍出现在 `<runtime_context>` 中。

### 核心 Prompt flow 与 strict audit

#### 保留 template section

| node_id | node_type | template_key | 生效条件 |
|---|---|---|---|
| `base_contract` | `template` | `chat/main` | 全部 |
| `qq_common_policy` | `template` | `chat/platform/qq/common` | QQ |
| `group_policy` | `template` | `chat/branch_group` | 群聊 |
| `qq_group_policy` | `template` | `chat/platform/qq/group` | QQ 群聊 |
| `private_policy` | `template` | `chat/branch_private` | 私聊 |
| `identity_context` | `template` | `chat/identity_context` | 全部 |

Web 当前没有独立 platform template，因此 audit 对 Web 不虚构一个新节点；它只要求
正确的 base + branch 组合，并拒绝 QQ policy 混入 Web。

#### 保留 runtime section

至少把以下节点纳入保留 identity 和 singleton 审计：

```text
runtime_context
persona_reference
runtime_tool_prompt
current_user_event
```

`session_guidance` 在后续功能阶段加入同一机制。审计不能用 fallback section 满足
严格 flow 契约；所有必需节点都必须来自 `origin="flow"`。

#### 审计维度

`audit_prompt_plan()` 在 `strict_audit=True` 时必须证明：

- 当前 platform/chat type 所需 section 恰好一次，不应出现的条件 section 为零；
- node ID、node type、template key/runtime key 与保留定义完全一致；
- 必需 section 状态为 `emitted`；允许为空的 section 只能使用契约允许的状态；
- 每个 `message_indexes` 均为有效下标、无重复越界引用；
- 模板和 system runtime section 指向 `role="system"`；
- history 只指向 `user/assistant`；
- `current_user_event` 指向最后一条且唯一的当前 `user` message；
- 核心相对顺序与 flow 一致；
- `runtime_context` 和 `identity_context` 缺失、改名、换类型、错 template、错序或
  指向错误 role 时全部 fail closed。

`compile_prompt_plan()` 默认使用严格审计。只有明确诊断旧 flow 的内部调用方可以显式
传 `strict_audit=False` 并返回 warnings；live runtime、启动预检和后续有效预览都必须
显式传 `strict_audit=True`。

### 请求级最终工具 schema

#### Wire schema 规范化

ToolPlan 在创建时把每个工具转换为唯一的 OpenAI-compatible wire 形态：

```json
{
  "type": "function",
  "function": {
    "name": "reply",
    "description": "...",
    "parameters": {}
  }
}
```

`category`、`risk_level`、`label`、`source` 等 Admin 展示元数据不得进入
`sent_tool_schemas`，因为 KT `ToolSchema.to_api_format()` 也不会发送这些字段。

#### Overlay 单点与幂等

`build_tool_schema()` 是模板说明 overlay 的唯一入口。overlay helper 必须做到：

- 识别并移除 description 尾部已有的生成型 `[V2ToolTemplate:...]` 区块；
- 按当前模板正文和 hash 追加一个区块；
- 连续调用两次结果逐字节相同；
- 模板变更时替换旧区块，而不是保留旧区块再追加；
- 普通人工 description 原文保持不变。

`filter_payload_tools()` 和 `filter_sdk_kwargs()` 只允许：

- 根据 `ToolPlan.sent_tool_names` 裁剪；
- 深拷贝返回，避免调用方修改冻结快照；
- 无工具时移除 `tools/tool_choice`。

它们不得读取 Prompt 模板、数据库或再次调用 overlay。

#### KT round-trip

`_tool_plan_native_schemas()` 转成 `ToolSchema` 后再调用 `to_api_format()`，结果必须与
`ToolPlan.sent_tool_schemas` 完全相同。该 round-trip 是 wire 快照可信度的回归门。

### Prompt hash 与 token 统计

`PromptPlan` 增加分项统计：

```python
message_token_estimate: int
tool_schema_token_estimate: int
token_estimate: int  # 兼容字段，等于前两项之和
```

统一算法：

```text
final_messages = Nanobot 规范化后的 messages
final_tools = ToolPlan 冻结后的 wire schemas
message_token_estimate = estimate_tokens(stable_json(final_messages))
tool_schema_token_estimate = estimate_tokens(stable_json(final_tools))
token_estimate = message_token_estimate + tool_schema_token_estimate
prompt_sha256 = sha256(stable_json({messages: final_messages, tools: final_tools}))
```

这里使用稳定 JSON 而不是只累加 message content，确保 role、结构化多模态内容和工具
参数 schema 都进入估算输入。估算仍是量级判断，不承诺等于 provider billing token。

`PromptTracer.record_render()` 继续写兼容的总 `token_estimate`，并在
`variables_json/debug` 中记录两个分项。Admin preview 同时返回分项。

真实出站层使用同一个 metrics helper，对每一笔已经过 Nanobot message sanitizer 和
final-tools filter 的 payload 再计算：

```text
request_lint_json.payload_metrics.prompt_sha256
request_lint_json.payload_metrics.message_token_estimate
request_lint_json.payload_metrics.tool_schema_token_estimate
request_lint_json.payload_metrics.token_estimate
```

这些字段只包含 hash 和计数，不复制 Prompt 或工具 schema 正文。初始模型请求的
outbound metrics 必须与 PromptPlan metrics 完全一致；工具循环产生的后续请求可以因
新增 assistant/tool 消息而不同，但每一笔都必须有自己的真实出站 metrics。

### 私聊 group_id 语义

Bridge 构造运行时上下文时使用：

```python
group_id = str(meta.get("group_id") or "").strip() if is_group else ""
```

`core/prompt_v2/context_adapters._request_group_id()` 再做一次防御：只要 normalized
chat type 不是 `group`，直接返回空字符串，不接受调用方误传值。

同一个空值传给：

- `build_tool_plan()`；
- `record_runtime_tool_decision()`；
- executor session runtime context；
- `PromptRuntimeInput`；
- `PromptCompileRequest`。

因此私聊不会查询 `ToolOverride(scope_type="group")`，`<runtime_context>` 也不会输出
`group_id` 行。群聊继续保留显式 group ID 和从 `group_<id>` 的安全回退。

### 超级用户权限上限与本轮工具相关性

`is_superuser` 不再直接等价于 `runtime_preset="full"`。判定顺序调整为：

```text
消息语义/材料是否完整
→ effort 与所需 preset
→ 根据用户权限应用可达到的上限
→ ToolPlan 合并默认、override 和硬约束
```

具体约束：

- 删除“句尾有问号即任务请求”的规则；
- 身份、能力、闲聊、缺少材料和普通短问句使用 `none` 或 `lightweight`；
- 只有已经被语义任务模式识别的明确分析、检索、代码或长材料任务，超级用户才可
  升到 `full`；
- 非超级用户继续受现有默认和 override 上限约束；
- 不新增只为某一句示例服务的脆弱硬编码关键词；
- `reply/no_reply` 等强制工具契约保持现状；
- 简单超级用户问题的请求不得包含 `bash/edit/write` 等高风险工具；
- 明确代码审查或文件操作任务仍可在超级用户权限允许时获得完整工具集。
- 私聊 `private_decision is None` 时默认 `lightweight`；只有群聊在没有私聊判定对象时
  继续使用既有 `full` 默认。

这只是请求级最小权限选择，不替代未来高风险工具的二阶段 approval。

## 兼容性与迁移

### 运行时模板

`load_template()` 会优先读取 Runtime 文件并完整覆盖 default body；初始化遇到已存在
文件时不会用新默认正文覆盖。本阶段保留这一行为：

- 可以增加代码生成的 runtime fact 和 flow/audit 契约；
- 不自动编辑已有 `identity_context.md`；
- 默认模板若需新增变量示例，只能作为新安装默认值，不能宣称已更新现有服务器正文；
- 发布说明必须提醒管理员 Runtime 模板正文与默认模板是两套可独立存在的内容。

关键 task 是例外：Runtime 文件仍保留且不会被覆盖，但只有通过代码侧 TaskContract
才会进入 live 请求。无效 Runtime 在 Admin 中显示 `invalid_runtime` 和缺失变量名；服务
端使用通过同一契约的 default 或代码 fallback。这个行为不修改用户文件，只改变危险
模板是否有资格生效。

Final Action provenance 不提供匿名旧格式兼容。生产已经固定 native tool format；缺少
tool name/call ID 的历史测试 fixture 必须升级为真实调用对，不能用生产降级换取旧测试
通过。

### 数据库

本整改不要求数据库 schema migration。分项 token 可以先写入 Prompt debug/trace 的
JSON 字段；现有 `PromptRenderLog.token_estimate` 保留为总量。

`python_sandbox` 验证只使用临时 SQLite，禁止对工作区 `nanobot.db` 做攻击复现或写入。
Memory 失败语义只改变日志何时标记 processed，不删除现有 ChatLog。

### API

现有 Prompt preview 的 `token_estimate` 和 `prompt_sha256` 字段保持兼容，新增分项是
向后兼容扩展。工具 schema 编辑 API 仍可显示管理元数据，但 wire preview 必须使用
规范化后的最终 schema。

## 失败策略

- Final Action provenance 缺失、错名、错 ID、重复结果或动作不匹配：视为没有可信最终
  动作；按一次严格工具重试后 suppress，绝不把普通文本、marker 或 HTML 修复成成功。
- 富终结结果 kind/tool 不匹配或 HTML 结构非法：拒绝终结；不回退到字符串嗅探。
- runtime/message metadata JSON 编码或审计失败：live runtime fail closed，不调用模型。
- 关键 task Runtime 无效：不启用且不覆盖；只允许契约通过的 default/代码 fallback。
- Memory 输出契约失败：保留未处理状态等待重试；合法空 candidates 才可消费。
- TimingGate 模板、网络或解析失败：固定 `no_reply`，不能扩大为 `continue`。
- 任意 Python 执行请求：在 OS 隔离恢复前返回稳定禁用错误，不执行代码。
- persona_update 缺 actor context 或 target 不一致：拒绝且不读取、不修改任何目标画像。
- ai_daily 时间参数非法：参数校验失败，不静默按 latest 执行。
- 显式鉴权事实缺失：默认 `False`，live Bridge 必须总是显式传值；测试锁死。
- 核心 flow/audit 失败：live runtime fail closed，不调用模型。
- 工具 schema 无法规范化：ToolPlan 构造失败，不降级为未经审计的 registry 全量工具。
- 最终 SDK kwargs 与 PromptPlan envelope 不一致：测试失败；运行时可记录脱敏 mismatch
  摘要，但不得打印 schema 正文或用户 Prompt。
- 私聊收到非空 group ID：adapter 清空，并在 debug 中只记录布尔异常标记，不记录
  敏感原值。

## 测试策略

### 单元测试

- verified reply/no_reply 的完整 assistant declaration + tool result 成功；伪造 marker、错
  name、错 ID、孤儿/重复 ID、动作错配全部失败。
- verified ai_daily/group_analysis 独立富结果成功；普通工具 HTML 和 assistant HTML 失败。
- runtime/message metadata 闭合标签攻击、换行、引号和 Unicode 分隔符可 round-trip，
  Prompt 中没有字面伪闭合标签；persona entity 不可逃逸。
- TaskContract 的缺 placeholder、缺值、Runtime → default → code fallback、registry
  completeness 和 Admin 保存原子失败。
- Memory 空正文/garbage/错误结构不 processed，合法空数组 processed，DB 成功后才消费。
- python_sandbox 不执行 print、文件/进程/SQLite 重连；临时数据库前后 hash/值不变；
  SQL authorizer 拒绝写 CTE、ATTACH、危险 PRAGMA 和 extension。
- persona_update 只允许当前 actor，跨用户 ID 和缺 context 拒绝；拒绝路径 DB 不变。
- ai_daily 的 freshness/target_date 进入 pipeline 和 cache key，非法组合被拒绝。
- 显式 `is_super_user` 从 Bridge 到 runtime/template values 一致透传。
- 无超级用户占位符的 identity template 下，runtime fact 仍存在。
- 四个 platform/chat type 分支的核心 section audit 矩阵。
- 每个保留节点的缺失、重复、改名、错类型、错 key、错 role、越界和错序失败用例。
- overlay 连续执行、模板 hash 更新和人工 description 保留。
- ToolPlan wire schema 不含管理元数据，KT round-trip 完全相等。
- messages/tools 分项 token 与总量关系、hash 可重复性。
- 私聊 group ID 清空、群聊保留、私聊不命中 group override。
- 超级用户简单问题不获得完整高风险工具集，明确任务仍可获得所需工具。

### 集成测试

捕获一轮模拟 OpenAI SDK 最终 kwargs，至少证明：

- `<runtime_context>` 含正确 `is_super_user`；
- 私聊不含 `group_id`；
- 每个工具的 V2 模板 marker 恰好一次；
- wire tools 与 ToolPlan 快照完全一致；
- 重算的 `prompt_sha256` 与 PromptPlan/AgentRun 一致；
- 总 token 等于 messages 与 tools 分项之和；
- 简单身份问题没有完整高风险工具集合；
- 模型只调用一次，Prompt strict audit 先于模型调用成功。
- `python_sandbox` 输出 Final Action marker 或报告 HTML 时均不能终结。
- assistant 普通文本输出 marker、结构化 reply JSON 或报告 HTML 时不能终结。
- classifier/timing 的不可信 payload 在完整 messages 中恰好出现一次且 role=user。
- 关键 task Runtime 故意替换为无变量旧模板时，live 不使用该正文且 fallback 可观测。

### 回归测试

- 普通私聊、超级用户私聊、群聊、Web 四条链路。
- Admin effective preview 与 live runtime 对相同输入的 envelope 一致。
- `reply/no_reply` 最终输出契约不变。
- 研究预设、轻量预设、full 预设和 ToolOverride 优先级不变。
- Prompt 默认 flow 与 Runtime flow 均通过强化后的验证。
- ReplyContractTracer/Admin 只统计 verified unique call ID，不把普通工具 JSON 计为
  structured fallback。
- ai_daily/group_analysis 生产者返回独立富结果 envelope，QQ 出口最终 HTML 行为保持。
- legacy Python 执行入口与 KT 工具同样 fail closed。

## 验收标准

- 只有真实配对的 `reply/no_reply` 能产生 Final Action；任意其他工具 marker 无效。
- 只有真实配对且 kind 匹配的 ai_daily/group_analysis 能产生富终结结果。
- 初次、重试和 runtime cache 中不存在裸字符串终结旁路。
- runtime_context 为可解析 JSON 且只含稳定事实；不可信 metadata 只在最后 user event。
- 关键 task 缺少必需变量时不能启用；坏 Runtime 不被覆盖且安全 fallback 可追踪。
- Memory 契约错误不会把日志标记为已处理。
- 任意 Python 用户代码在安全隔离恢复前都不会执行，临时数据库攻击测试保持不变。
- persona_update 的 target 与当前 runtime actor 强绑定，跨用户请求无副作用。
- ai_daily 的显式时间参数真实影响 pipeline 与缓存隔离。
- 后端鉴权为真时，代码生成的运行时事实明确为 JSON boolean `"is_super_user":true`，不依赖身份
  模板正文。
- 删除或篡改任一核心 section 后，`strict_audit=True` 必然拒绝。
- 最终请求中同一工具模板说明只出现一次。
- `PromptPlan.request_json` 与捕获的 Nanobot 最终 `messages/tools` 完全一致。
- `prompt_sha256` 可由最终 `messages/tools` 独立重算。
- `token_estimate` 包含工具 schema，并有 messages/tools 分项。
- 私聊 Prompt 和工具覆盖解析都没有 group scope 污染。
- 简单超级用户问题不再携带完整高风险工具集；明确任务能力不被永久降级。
- 未修改 QQbot，未修改服务器自定义身份模板正文，未引入任何具体超级用户 ID。

## 与 Session Guidance 的依赖关系

本整改必须先完成，随后再执行全会话专属指导计划。后续计划可以依赖：

- 核心 strict audit 已能保护 flow 的必需节点和顺序；
- `session_guidance` 只需作为新的保留、可空 singleton 插入
  `identity_context` 与 `persona_reference` 之间；
- Prompt hash/token 已覆盖最终 wire tools；
- canonical 私聊 runtime context 不再携带伪 group ID；
- `session_guidance` 无法改变代码鉴权事实或工具集合。

统一执行顺序由 `.codex/plans/session-guidance-rollout.md` 维护。
