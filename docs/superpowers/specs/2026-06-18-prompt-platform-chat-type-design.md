# Prompt platform × chat_type 二维适配设计

日期：2026-06-18

状态：设计完成，等待实现计划

## 背景

P1-6 已把 live 主路径收敛到 canonical Prompt Runtime，旧 V1 live 分支和 legacy 管理入口已经从主链路退出。P2-1 到 P2-3 已经沿 `platform` 维度完成工具策略、请求 / 响应信封、`client_meta` 边界校验和 QQ 出站 renderer。当前剩余缺口集中在提示词组装：真实入口已经知道 `client_meta.platform`，但 Prompt Runtime 仍只按 `chat_type` 选择分支模板。

这会导致两个问题。第一，`chat_type` 同时承担「群聊 / 私聊」和「QQ 平台」语义，`chat/branch_group.md`、`chat/branch_private.md` 等模板里写死「QQ 群聊 / QQ 私聊」。第二，Web 或其他平台接入时，即使请求带了 `platform=web`，编排图也无法选择平台专属规则，只能继续吃 QQ 专用说明。

本阶段目标是把 Prompt Runtime 的分支维度从一维 `chat_type` 扩展成二维 `platform × chat_type`。`chat_type` 继续表达会话语义，`platform` 表达平台语义，二者互不挤占。

## 现状审计

本设计基于 2026-06-18 主线程读码和 3 个子 agent 的只读报告。

- `/chat` 和 `/group/message` 已通过 `core/client_meta.py` 归一化 `client_meta.platform`。缺省值是 `qq`，传入值会执行 `strip().lower()`，并要求匹配 `^[a-z][a-z0-9_-]{0,31}$`。
- `NanobotBridge.handle_message()` 已从 `meta.platform` 解析 `platform`，并传给 `build_tool_plan()`、`record_runtime_tool_decision()` 和 executor session extra。
- `PromptRuntimeAssemblyContext`、`PromptRuntimeInput` 和 `PromptCompileRequest` 都没有 `platform` 字段。
- `build_prompt_runtime()` 构造 `PromptCompileRequest` 时只传 `chat_type`，没有传平台。
- `compile_prompt_plan()` 调用 `ordered_nodes_for_chat(flow, chat_type)`，`flow.py` 只认 `CHAT_TYPES = {"group", "private"}`。
- `flow.py` 的 `_applies()`、`validate_flow()` 和 `ordered_nodes_for_chat()` 只按 `chat_types` 过滤节点和边；冲突检测以 `(from, chat_type)` 为键。
- `build_template_values()` 和 `<runtime_context>` 只输出 `chat_type`，模板变量白名单没有 `platform`。
- 管理端有效预览 `EffectivePromptPreviewRequest` 只提供 `chat_type`，`preview_effective_prompt_v2()` 构造 tool plan 和 prompt request 时也没有平台参数。
- `prompts.v2.default` 与 `data/prompts_v2` 当前模板内容一致；运行时加载优先读取 `data/prompts_v2`，初始化只复制缺失文件，不覆盖已有文件。
- `chat/branch_group.md` 写死「当前对话发生在 QQ 群聊中」，并混入 `@`、斗图、表情包、群友等 QQ 语义；`chat/branch_private.md` 写死「QQ 私聊」。
- 工具 usage 中仍有少量 QQ / OneBot 词汇。首版应清理工具 usage 里的强平台词，但不扩展工具模板选择器。

## 方案比较

### 方案 A：只透传 `platform`，不改 flow

该方案把 `platform` 加进 `PromptCompileRequest`、模板变量和 `<runtime_context>`，但模板分支仍只按 `chat_type` 选择。

优点是改动范围小，能让模板直接看到平台。缺点是无法解决「按平台选择模板」这个核心问题，QQ 专属说明仍然需要塞在通用群聊或私聊模板中。它只能作为过渡，不满足 P2-4 目标。

### 方案 B：透传 `platform`，flow 支持 `platforms` 条件

该方案保留 `chat_types`，新增可选 `platforms` 条件。节点和边同时满足 `chat_type` 与 `platform` 时才进入拓扑排序。模板目录显式新增 `chat/platform/qq/*`，平台选择仍由 flow 的 `template_key` 完成。

优点是语义清晰：`chat_type` 只管群聊 / 私聊，`platform` 只管 QQ / Web / 其他平台。现有 flow 模型、模板 loader、审计和管理入口都可以小步扩展，不需要引入路径魔法。缺点是 `validate_flow()`、测试和默认模板需要同步升级。

### 方案 C：把 `chat_type` 扩成 `qq_group` / `web_private`

该方案用复合 key 表达二维条件，避免 flow 增加新字段。

优点是实现表面上集中在一个字段。缺点是破坏现有 `chat_type ∈ {group, private}` 的稳定语义，影响工具策略、TimingGate、响应信封、AgentRun 审计、管理预览和大量测试。它还会让 `chat_type` 在不同上下文里含义不一致。

推荐采用方案 B。

## 目标

- `platform` 从 Bridge 一路透传到 Prompt Runtime 编译请求。
- Prompt flow 支持 `platforms` 条件，并保持 `chat_types` 语义不变。
- `platforms` 缺省表示全平台通配，不等同于 `qq`。
- 同一 `from` 节点下，如果两个出边的 `chat_types` 条件有交集且 `platforms` 条件有交集，视为歧义并拒绝。
- 模板把平台无关规则留在 `chat/main.md`、`chat/branch_group.md`、`chat/branch_private.md`，把 QQ 专属规则迁到 `chat/platform/qq/*`。
- 管理端有效预览可以选择 `platform`，并与真实运行路径使用同一 flow 过滤口径。
- `prompts.v2.default` 与 `data/prompts_v2` 同步修改，避免 active runtime 继续读取旧模板。
- 定向测试覆盖 `qq × group`、`qq × private`、`web × private` 以及 flow 二维冲突检测。

## 非目标

- 不把 `platform` 塞进 `chat_type`。
- 不重写 Prompt Runtime 编译器。
- 不改 template loader 做平台路径自动解析；模板选择只由 flow 的 `template_key` 决定。
- 不扩展工具模板选择器，例如 `tools/<name>/platform/qq/usage.md` 不纳入首版。
- 不一次性迁移所有 task 模板。`tasks/timing_gate.md` 仍由 TimingGate 自身的 platform 策略继续演进。
- 不改变 `/chat`、`/group/message` 的请求 schema；它们已通过 `client_meta.platform` 提供平台信息。
- 不为平台做封闭枚举。`qq`、`web`、`synergy` 等值由边界校验格式约束和 flow 配置表达。

## 接口设计

### 运行时上下文

新增字段：

```python
PromptRuntimeAssemblyContext.platform: str
PromptRuntimeInput.platform: str
PromptCompileRequest.platform: str = "qq"
PromptPlan.platform: str
```

`PromptCompileRequest` 增加属性：

```python
@property
def normalized_platform(self) -> str:
    value = str(self.platform or "").strip().lower()
    return value or "qq"
```

归一化规则与 `client_meta` 保持一致：缺省 `qq`，空白值回落 `qq`，传入字符串执行 `strip().lower()`。Prompt Runtime 内部不再重复做正则校验，真实入口由 `core/client_meta.py` 负责；测试和直接调用方依赖默认值兼容旧调用。

### Bridge 透传

`NanobotBridge.handle_message()` 已有局部变量：

```python
platform = str(meta.get("platform") or "qq").strip().lower() or "qq"
```

实现时应把该值写入 `PromptRuntimeAssemblyContext(platform=platform)`，并由 `_build_prompt_runtime_input()` 传入 `PromptRuntimeInput(platform=context.platform)`。这条链路和 ToolPlan 已使用的平台值保持同源。

### 编译请求

`build_prompt_runtime()` 构造 `PromptCompileRequest` 时传入：

```python
platform=input.platform
```

`compile_prompt_plan()` 使用：

```python
chat_type = request.normalized_chat_type
platform = request.normalized_platform
ordered_nodes = ordered_nodes_for_chat(flow_state.flow, chat_type, platform=platform)
```

`PromptPlan` 增加 `platform`，`debug` 中记录 `platform`、`flow_node_ids` 和 `template_paths`。`prompt_key` 继续是元数据和兼容字段，不作为平台模板选择入口。

### 模板变量和 runtime_context

`build_template_values()` 增加：

```python
"platform": request.normalized_platform
```

`build_runtime_context()` 输出：

```text
<runtime_context>
platform: qq
chat_type: group
...
</runtime_context>
```

`core/prompt_v2/variables.py` 的全局变量白名单加入 `platform`，描述为「当前客户端平台」。这样模板可以显式引用 `{{ platform }}`，但首版不要求所有模板都引用它。

### 管理端预览

`EffectivePromptPreviewRequest` 增加：

```python
platform: str = "qq"
```

`preview_effective_prompt_v2()` 应：

- 归一化 `platform = str(body.platform or "qq").strip().lower() or "qq"`。
- 调用 `build_tool_plan(..., platform=platform, ...)`，让预览中的工具策略与真实入口一致。
- 构造 `PromptCompileRequest(platform=platform, ...)`。
- 响应顶层返回 `platform`，`prompt_plan` 中也带 `platform`。
- `_recent_prompt_preview_logs()` 暂不按 platform 过滤，因为 `AgentRun` 当前没有稳定平台列；这是审计展示限制，不影响编译预览。

## Flow 设计

### 条件字段

保留：

```json
{"chat_types": ["group"]}
```

新增：

```json
{"platforms": ["qq", "web"]}
```

字段语义：

- `chat_types` 缺省表示 `{"group", "private"}`。
- `platforms` 缺省表示全平台通配。
- `platforms` 允许字符串或数组，内部归一为小写去重数组。
- 平台值不做封闭枚举，格式可复用 `^[a-z][a-z0-9_-]{0,31}$`。

### 过滤函数

`_applies()` 扩展为同时判断两个维度：

```python
def _applies(item: dict[str, Any], chat_type: str, platform: str) -> bool:
    return _applies_chat_type(item, chat_type) and _applies_platform(item, platform)
```

保留旧函数名 `ordered_nodes_for_chat(flow, chat_type, platform="qq")`，避免直接调用方和测试一次性破坏。

非法 `chat_type` 继续回落 `private`，兼容现有行为。空平台回落 `qq`。

### 冲突检测

当前冲突检测以 `(from, chat_type)` 为键。二维 flow 需要改为集合交集检测：

```text
同一 from 节点下，edge A 与 edge B 都可能生效，
且 active_chat_types(A) ∩ active_chat_types(B) 非空，
且 active_platforms(A) ∩ active_platforms(B) 非空，
则该 from 节点存在歧义出边。
```

通配平台参与交集判断。也就是说，如果一条边缺省 `platforms`，另一条边写 `platforms=["qq"]`，它们在 `qq` 上重叠，不能同时从同一 `from` 节点出发。首版不引入「具体平台覆盖通配平台」的优先级规则，因为这会让 flow 行为变隐式。

节点条件也参与 active 条件计算。边和起止节点的 `chat_types`、`platforms` 都要求交集，交集为空则这条边在任何实际请求下都不会生效；实现可保留该边，但它不会进入 `outgoing_conditions`。

### 默认 flow

默认 flow 建议变为：

```json
{
  "from": "base_contract",
  "to": "qq_common_policy",
  "platforms": ["qq"]
}
```

推荐节点顺序：

1. `base_contract` → `chat/main`
2. `qq_common_policy` → `chat/platform/qq/common`，仅 `platforms=["qq"]`
3. `group_policy` → `chat/branch_group`，仅 `chat_types=["group"]`
4. `qq_group_policy` → `chat/platform/qq/group`，仅 `chat_types=["group"]` 且 `platforms=["qq"]`
5. `private_policy` → `chat/branch_private`，仅 `chat_types=["private"]`
6. `runtime_context`
7. `identity_context`
8. `persona_reference`
9. `conversation_context_header`
10. `history_messages`
11. `group_context`，仅 `chat_types=["group"]`
12. `effort_constraint`
13. `runtime_tool_prompt`
14. `current_user_event`

为了避免同一节点出现平台通配与平台专属出边冲突，建议把分支写成显式串联，而不是从 `base_contract` 同时分叉到通用和平台节点。例如 `base_contract -> qq_common_policy -> group_policy -> qq_group_policy -> runtime_context` 是清晰的；`base_contract -> group_policy` 与 `base_contract -> qq_common_policy` 在 `qq/group` 上会形成出边歧义。

## 模板迁移设计

### 通用模板

`chat/main.md` 继续承载所有平台共享规则：

- 角色定位。
- 输出契约。
- 安全规则。
- 上下文权限。
- 工具使用纪律。
- Markdown 和普通文本格式。

该模板不得出现 QQ、OneBot、CQ、NapCat、斗图、群友、私聊平台名等平台专属词。

`chat/branch_group.md` 改成通用群聊规则：

- 当前对话是群聊，不是私聊。
- 面向多人对话，保持简短，避免主持人式总结。
- 未被点名时谨慎发言。
- 群上下文、群记忆、表达和术语上下文只作为背景。
- 群分析、群日报等群聊工具仍可以保留，因为它们属于 chat_type 语义。

需要迁出的内容：

- 「QQ 群聊」改为「群聊」。
- `@`、斗图、表情包、群友等平台或 QQ 文化强相关规则迁到 `chat/platform/qq/group.md`。

`chat/branch_private.md` 改成通用私聊规则：

- 当前对话是私聊。
- 可以更完整地协助处理问题。
- 写入、删除、配置变更仍遵守现有确认规则。

需要迁出的内容：

- 「QQ 私聊」改为「私聊」。

### QQ 平台模板

新增：

```text
chat/platform/qq/common.md
chat/platform/qq/group.md
```

`chat/platform/qq/common.md` 承载 QQ 平台通用约定：

- 当前平台是 QQ，入站消息可能来自 NapCat / OneBot 兼容链路。
- 消息元数据可能包含 `message_id`、`self_id`、`bot_id`、`bot_aliases`。
- `[sticker:<id>]`、`[generated_image:<id>]` 是 Nanobot 内部短 token，最终由出口 renderer 转成 QQ 可发送内容。
- 不要求模型手写 OneBot CQ 码；直接 CQ 码仅作为兼容输入。
- `reply_meta` 表示发送意图，最终是否转成 QQ 引用或 @ 由出口层决定。

`chat/platform/qq/group.md` 承载 QQ 群聊专属约定：

- QQ 群里用户可能用 @、回复、群昵称、bot 昵称来点名。
- 群友闲聊、斗图、玩梗、抽卡、签到、金币、菜单命令等没有指向 bot 时，默认不要抢话。
- 表情包只在斗图、玩梗、用户明确要图或气氛适合时使用，避免刷屏。
- 群聊上下文中的 `[msg_id]`、时间、昵称是元数据，不要复述。

首版不强制新增 `chat/platform/qq/private.md`。当前 QQ 私聊差异较少，放在 `common.md` 和通用私聊模板即可。如果实现时发现必须承载 QQ 私聊专属规则，再在同一实现计划中作为独立任务加入。

### 模板副本同步

需要同步修改两个根目录：

```text
prompts.v2.default/chat/flow.json
data/prompts_v2/chat/flow.json
prompts.v2.default/chat/main.md
data/prompts_v2/chat/main.md
prompts.v2.default/chat/branch_group.md
data/prompts_v2/chat/branch_group.md
prompts.v2.default/chat/branch_private.md
data/prompts_v2/chat/branch_private.md
prompts.v2.default/chat/platform/qq/common.md
data/prompts_v2/chat/platform/qq/common.md
prompts.v2.default/chat/platform/qq/group.md
data/prompts_v2/chat/platform/qq/group.md
```

原因是 `data/prompts_v2` 是 active runtime 副本，`init_prompt_v2_runtime_dir()` 只复制缺失文件。如果只改默认目录，本地和线上运行时仍可能读取旧 runtime 模板。

`data/prompts_v2` 可能受 ignore 规则影响。提交时必须按文件显式暂存，必要时对新增 runtime 模板使用 `git add -f <path>`。

### 工具 usage 文案

首版不扩展工具模板选择器，但应清理通用 usage 中的强平台词：

- `tools/reply/usage.md`
- `tools/sticker_search/usage.md`
- `tools/image_generation/usage.md`

这些文件中「QQ 发送前」「OneBot CQ 码」等说明应压缩成中性口径，例如「出口 renderer 会转换成当前平台可发送内容」。直接 CQ 码兼容性放到 QQ 平台模板里描述。

## 测试计划

### Flow 单元测试

覆盖文件：`tests/test_prompt_v2.py` 或新建更聚焦的 flow 测试。

需要覆盖：

- `ordered_nodes_for_chat(flow, "group", platform="qq")` 包含 `qq_common_policy` 和 `qq_group_policy`。
- `ordered_nodes_for_chat(flow, "private", platform="qq")` 包含 `qq_common_policy` 和 `private_policy`，不包含 `qq_group_policy`。
- `ordered_nodes_for_chat(flow, "private", platform="web")` 不包含任何 QQ 平台模板。
- 节点级 `platforms` 和边级 `platforms` 同时参与过滤。
- `platforms` 缺省为通配。
- 同一 `from` 下通配平台出边与 `platforms=["qq"]` 出边在同一 `chat_type` 下冲突。
- 非法 `chat_types` 仍被拒绝，非法 `platforms` 也被拒绝。
- 旧调用 `ordered_nodes_for_chat(flow, "group")` 默认等价于 `platform="qq"`，保护兼容。

### Runtime 编译测试

覆盖文件：`tests/test_prompt_v2.py`、`tests/test_bridge_prompt_v2.py`、`tests/test_kt_framework.py`。

需要覆盖：

- `PromptCompileRequest(platform="web")` 编译后的 `PromptPlan.platform == "web"`。
- `build_template_values()` 可以渲染 `{{ platform }}`。
- `<runtime_context>` 包含 `platform: qq` 或 `platform: web`。
- `compile_prompt_plan()` 的 `debug.flow_node_ids` 能反映二维 flow 结果。
- `NanobotBridge._build_prompt_runtime_input()` 会把 `PromptRuntimeAssemblyContext.platform` 传给 `PromptRuntimeInput`。
- `handle_message()` 从 `meta.platform` 传给 Prompt Runtime，和 ToolPlan 使用同一个平台值。
- `build_prompt_runtime()` 构造 `PromptCompileRequest` 时包含 `platform`。

### Admin 预览测试

覆盖文件：`tests/test_admin_api.py`。

需要覆盖：

- `/prompt/effective-preview` 请求体允许 `platform="web"`。
- 预览返回顶层 `platform`。
- 预览调用 `build_tool_plan(..., platform="web", ...)`。
- 预览编译结果不包含 QQ 平台模板节点。
- 旧请求不传 platform 时仍默认为 `qq`。

### 模板和注册表测试

覆盖文件：`tests/test_prompt_v2_template_registry.py`、`tests/test_prompt_v2_template_loader.py` 或现有相邻测试。

需要覆盖：

- `chat/platform/qq/common` 和 `chat/platform/qq/group` 可以 list / get / reset。
- 旧 alias `chat_branch_group`、`chat_branch_private` 不变。
- runtime 目录新增平台模板后，default 和 runtime 副本保持一致。
- `chat/main.md` 不包含 QQ / OneBot / CQ / NapCat 等平台词。
- `chat/branch_group.md` 和 `chat/branch_private.md` 不包含 QQ 平台词。
- QQ 专属词集中在 `chat/platform/qq/*` 或 task/tool 专属模板中。

### 集成回归

实现阶段每个任务按风险选择定向测试。最终收口至少运行：

```bash
python -m pytest tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py -v
python -m pytest tests/ -v
```

## 子 agent 分工

P2-4 涉及 Prompt Runtime、Bridge、Admin、模板和测试，适合拆给多个互不写同一文件的子 agent 并行推进。建议主线程先完成接口计划和 owner 划分，再分派只改各自范围的任务。

### Agent A：Flow 和 schema

负责文件：

- `core/prompt_v2/schema.py`
- `core/prompt_v2/flow.py`
- `core/prompt_v2/compiler.py`
- `core/prompt_v2/context_adapters.py`
- `core/prompt_v2/variables.py`
- 相关 flow / compiler 测试

接口约定：

- 输出 `PromptCompileRequest.normalized_platform`。
- 保留 `ordered_nodes_for_chat(flow, chat_type, platform="qq")` 函数名。
- `PromptPlan` 带 `platform`。
- 不改 Bridge、API route 和模板正文。

### Agent B：Bridge 和 Admin 预览

负责文件：

- `nanobot_kt/bridge.py`
- `nanobot_kt/prompt_runtime.py`
- `api/admin_routes.py`
- `app/prompt_runtime/preview_service.py`
- 对应 Bridge / API 测试

接口约定：

- 使用 Agent A 定义的 `platform` 字段。
- 从现有 `meta.platform` 取值，不重新定义平台校验规则。
- 预览默认平台为 `qq`，请求可传 `web`。
- 不改 flow 校验算法，不改模板正文。

### Agent C：模板迁移

负责文件：

- `prompts.v2.default/chat/*`
- `data/prompts_v2/chat/*`
- `prompts.v2.default/tools/*/usage.md`
- `data/prompts_v2/tools/*/usage.md`
- 模板扫描测试

接口约定：

- 使用 flow 中显式 `template_key`，不依赖 loader 自动拼路径。
- 两个模板根目录保持内容一致。
- QQ 专属规则只放进 `chat/platform/qq/*` 或明确的 QQ task / tool 文档。
- 不改 Python runtime 代码。

### 主线程集成

主线程负责：

- 合并各 agent 的 diff。
- 解决跨文件接口不一致。
- 运行定向和全量验证。
- 按阶段提交：接口 / flow、Bridge / Admin、模板迁移、文档收口分别提交。

## 风险与应对

- flow 条件变成二维后，隐式覆盖规则容易产生歧义：首版不做覆盖优先级，冲突即报错。
- `data/prompts_v2` runtime 副本可能覆盖 default 修改：实现阶段必须同步两个根目录，并用 `diff -qr prompts.v2.default data/prompts_v2` 验证。
- 管理预览如果不传 `platform` 给 ToolPlan，会出现「提示词按 web，工具仍按 qq」的错觉：预览和真实入口必须共用同一平台值。
- 模板变量白名单若漏加 `platform`，模板引用会被 `validate_scoped_template()` 拒绝：变量和渲染测试要同批提交。
- 工具 usage 首版不平台化，QQ 兼容说明可能无处安放：只把平台无关的短 token 规则留在工具 usage，QQ 兼容细节放入 `chat/platform/qq/common.md`。
- `chat_type` 语义被误用会扩散到工具策略和 TimingGate：实现评审时禁止引入 `qq_group` 这类复合会话类型。
- 全量测试时间较长，但本阶段触及 Prompt Runtime 核心路径：最终提交前必须运行全量测试。

## 验收标准

- `platform` 从 Bridge metadata 进入 `PromptRuntimeInput`、`PromptCompileRequest`、`PromptPlan`、`debug` 和 `<runtime_context>`。
- `ordered_nodes_for_chat()` 支持 `platform` 参数，旧调用默认 `qq`。
- flow 配置支持 `platforms`，并能拒绝二维条件重叠的歧义出边。
- `qq × group` 会注入通用群聊模板和 QQ 群聊模板。
- `qq × private` 会注入通用私聊模板和 QQ common 模板。
- `web × private` 不会注入 QQ 平台模板。
- `chat/main.md`、`chat/branch_group.md`、`chat/branch_private.md` 不再写死 QQ。
- `prompts.v2.default` 和 `data/prompts_v2` 的相关模板保持一致。
- 管理端有效预览支持 platform，并让 tool plan 与 prompt compile 使用同一个平台值。
- 定向回归和全量回归通过。
