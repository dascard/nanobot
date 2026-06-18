# P2-4 Prompt platform × chat_type 二维适配实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 Prompt Runtime 按 `platform × chat_type` 二维选择模板，QQ 专属提示词进入平台分支，Web 等平台不再继承 QQ 规则。

**架构：** 保留 `chat_type ∈ {group, private}` 表达会话语义，新增 `platform` 表达客户端平台。Prompt flow 在节点和边上新增可选 `platforms` 条件，编译器按 `ordered_nodes_for_chat(flow, chat_type, platform="qq")` 过滤模板；Bridge、Admin 预览和模板变量共享同一个平台值。

**技术栈：** Python 3.12、FastAPI、Prompt Runtime、pytest、Markdown 模板、JSON flow 配置。

---

## 当前事实

- 设计文档：`docs/superpowers/specs/2026-06-18-prompt-platform-chat-type-design.md`，提交 `27e632f docs(提示词): 设计平台化提示词分支`。
- 当前计划文件路径按项目约定使用 `.Codex/plans/prompt-platform-chat-type.md`。
- `/chat` 和 `/group/message` 已通过 `core/client_meta.py` 归一化 `client_meta.platform`，缺省为 `qq`。
- `NanobotBridge.handle_message()` 已把 `platform` 传给 ToolPlan、runtime decision 和 executor session extra，但没有传给 Prompt Runtime。
- `PromptRuntimeAssemblyContext`、`PromptRuntimeInput`、`PromptCompileRequest` 和 `PromptPlan` 当前都没有 `platform` 字段。
- `core/prompt_v2/flow.py` 只支持 `chat_types` 条件，`ordered_nodes_for_chat(flow, chat_type)` 没有平台参数。
- `build_template_values()`、`build_runtime_context()` 和变量白名单没有 `platform`。
- Admin 有效预览 `EffectivePromptPreviewRequest` 没有 `platform` 字段，`preview_effective_prompt_v2()` 也没有把平台传给 ToolPlan 或 PromptCompileRequest。
- `prompts.v2.default` 与 `data/prompts_v2` 当前内容一致。运行时优先读取 `data/prompts_v2`，所以模板改动必须同步两个根目录。
- 现有无关脏文件包括 pycache、`docs/goal.md`、`tests/conftest.py`、`.codex/` 历史计划、历史待办清单文档、`nanobot.db` 等。执行本计划时不要回滚、删除或暂存这些文件。

## 文件结构

- 修改：`core/prompt_v2/schema.py`
  - `PromptCompileRequest` 增加 `platform` 和 `normalized_platform`。
  - `PromptPlan` 增加 `platform`。
- 修改：`core/prompt_v2/variables.py`
  - 全局变量白名单增加 `platform`。
- 修改：`core/prompt_v2/context_adapters.py`
  - `build_template_values()` 和 `build_runtime_context()` 输出平台。
- 修改：`core/prompt_v2/flow.py`
  - 节点和边支持 `platforms`。
  - 冲突检测扩展为 `chat_types × platforms` 二维交集。
  - `ordered_nodes_for_chat()` 增加 `platform="qq"` 默认参数。
- 修改：`core/prompt_v2/compiler.py`
  - 编译时使用 `request.normalized_platform` 过滤 flow。
  - `PromptPlan` 和 debug 记录平台。
- 修改：`nanobot_kt/prompt_runtime.py`
  - `PromptRuntimeInput` 增加 `platform`。
  - `build_prompt_runtime()` 构造 `PromptCompileRequest(platform=input.platform)`。
- 修改：`nanobot_kt/bridge.py`
  - `PromptRuntimeAssemblyContext` 增加 `platform`。
  - `_build_prompt_runtime_input()` 和 `handle_message()` 透传平台。
- 修改：`api/admin_routes.py`
  - `EffectivePromptPreviewRequest` 增加 `platform`。
- 修改：`app/prompt_runtime/preview_service.py`
  - 有效预览把平台传给 ToolPlan 和 Prompt Runtime，并在响应中返回。
- 修改：`prompts.v2.default/chat/flow.json`
- 修改：`data/prompts_v2/chat/flow.json`
- 修改：`prompts.v2.default/chat/main.md`
- 修改：`data/prompts_v2/chat/main.md`
- 修改：`prompts.v2.default/chat/branch_group.md`
- 修改：`data/prompts_v2/chat/branch_group.md`
- 修改：`prompts.v2.default/chat/branch_private.md`
- 修改：`data/prompts_v2/chat/branch_private.md`
- 创建：`prompts.v2.default/chat/platform/qq/common.md`
- 创建：`data/prompts_v2/chat/platform/qq/common.md`
- 创建：`prompts.v2.default/chat/platform/qq/group.md`
- 创建：`data/prompts_v2/chat/platform/qq/group.md`
- 修改：`prompts.v2.default/tools/reply/usage.md`
- 修改：`data/prompts_v2/tools/reply/usage.md`
- 修改：`prompts.v2.default/tools/sticker_search/usage.md`
- 修改：`data/prompts_v2/tools/sticker_search/usage.md`
- 修改：`prompts.v2.default/tools/image_generation/usage.md`
- 修改：`data/prompts_v2/tools/image_generation/usage.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/prompt-platform-chat-type.md`

测试文件：

- 修改：`tests/test_prompt_v2.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`tests/test_prompt_v2_template_admin.py`
- 修改：`tests/test_prompt_v2_template_registry.py`

## 并行执行策略

先由主线程完成任务 1 的接口 / flow 提交。任务 1 提交后，可以把任务 2 和任务 3 分派给不同子 agent；任务 4 必须等待任务 1 和任务 3 的模板 / flow 已落地；任务 5 由主线程完成收口。

文件所有权：

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| Agent A：Flow 和 schema | `core/prompt_v2/*`、`tests/test_prompt_v2.py` | Bridge、Admin、模板正文 |
| Agent B：Bridge 和 Admin | `nanobot_kt/bridge.py`、`nanobot_kt/prompt_runtime.py`、`api/admin_routes.py`、`app/prompt_runtime/preview_service.py`、对应测试 | `core/prompt_v2/flow.py`、模板正文 |
| Agent C：模板迁移 | `prompts.v2.default/**`、`data/prompts_v2/**`、模板扫描测试 | Python runtime 代码 |
| 主线程：集成收口 | 文档、计划、跨模块验证 | 回滚无关脏文件 |

子 agent 通用提示词：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件。
先写红灯测试并运行指定命令，确认失败原因与计划一致。
再写最小实现，运行定向测试和任务指定回归。
完成后提交本任务文件，commit message 使用中文 Conventional Commit。
返回：红灯输出摘要、绿灯输出摘要、提交号、改动文件列表、仍需主线程集成的点。
```

如果子 agent 需要修改禁止文件，停止该 agent，把需求交给主线程重新分配。不要让两个 agent 同时编辑同一文件。

## 共享接口契约

任务 1 完成后，后续任务必须使用以下接口：

```python
@dataclass(frozen=True)
class PromptPlan:
    engine: str
    chat_type: str
    platform: str
    prompt_key: str
    messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    section_hashes: dict[str, str]
    prompt_sha256: str
    token_estimate: int
    warnings: list[str]
    debug: dict[str, Any]


@dataclass
class PromptCompileRequest:
    chat_type: str = "private"
    platform: str = "qq"

    @property
    def normalized_platform(self) -> str:
        value = str(self.platform or "").strip().lower()
        return value or "qq"


def ordered_nodes_for_chat(
    flow: dict[str, Any],
    chat_type: str,
    platform: str = "qq",
) -> list[dict[str, Any]]:
    ...
```

`platforms` 条件规则：

- `platforms` 缺省表示全平台通配。
- `platforms` 可以是字符串或数组。
- 非空平台值必须匹配 `^[a-z][a-z0-9_-]{0,31}$`。
- 同一 `from` 节点下，如果两条出边的 `chat_types` 条件有交集且 `platforms` 条件有交集，`validate_flow()` 必须抛出 `PromptFlowError`。
- 不引入「更具体平台覆盖通配平台」规则。

## 任务 1：Prompt Runtime core 支持 platform 维度

**文件：**
- 修改：`tests/test_prompt_v2.py`
- 修改：`core/prompt_v2/schema.py`
- 修改：`core/prompt_v2/variables.py`
- 修改：`core/prompt_v2/context_adapters.py`
- 修改：`core/prompt_v2/flow.py`
- 修改：`core/prompt_v2/compiler.py`

- [ ] **步骤 1：编写 flow 二维筛选红灯测试**

在 `tests/test_prompt_v2.py` 追加：

```python
def test_prompt_v2_flow_filters_by_chat_type_and_platform():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "qq_common", "type": "template", "template_key": "chat/platform/qq/common", "platforms": ["qq"]},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group", "chat_types": ["group"]},
            {"id": "qq_group", "type": "template", "template_key": "chat/platform/qq/group", "chat_types": ["group"], "platforms": ["qq"]},
            {"id": "private_policy", "type": "template", "template_key": "chat/branch_private", "chat_types": ["private"]},
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "qq_common", "platforms": ["qq"]},
            {"from": "base", "to": "group_policy", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "group_policy", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_policy", "to": "qq_group", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "qq_group", "to": "tail", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_policy", "to": "tail", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "private_policy", "chat_types": ["private"], "platforms": ["qq"]},
            {"from": "base", "to": "private_policy", "chat_types": ["private"], "platforms": ["web"]},
            {"from": "private_policy", "to": "tail", "chat_types": ["private"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="qq")] == [
        "base",
        "qq_common",
        "group_policy",
        "qq_group",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="qq")] == [
        "base",
        "qq_common",
        "private_policy",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="web")] == [
        "base",
        "private_policy",
        "tail",
    ]
```

- [ ] **步骤 2：编写 flow 冲突和平台格式红灯测试**

继续追加：

```python
def test_prompt_v2_flow_rejects_overlapping_platform_branches():
    import pytest
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "generic", "type": "template", "template_key": "chat/branch_group"},
            {"id": "qq", "type": "template", "template_key": "chat/platform/qq/group"},
        ],
        "edges": [
            {"from": "base", "to": "generic", "chat_types": ["group"]},
            {"from": "base", "to": "qq", "chat_types": ["group"], "platforms": ["qq"]},
        ],
    }

    with pytest.raises(PromptFlowError, match="同一条件只能有一条出边"):
        validate_flow(flow)


def test_prompt_v2_flow_rejects_invalid_platform_values():
    import pytest
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main", "platforms": ["QQ!"]},
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [{"from": "base", "to": "tail"}],
    }

    with pytest.raises(PromptFlowError, match="platforms 不支持"):
        validate_flow(flow)
```

- [ ] **步骤 3：编写编译请求和变量红灯测试**

追加：

```python
def test_prompt_v2_template_values_and_runtime_context_include_platform():
    from core.prompt_v2.context_adapters import build_runtime_context, build_template_values
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.variables import render_scoped_template

    request = PromptCompileRequest(chat_type="group", platform="Web", session_id="group_1001")
    values = build_template_values(request, current_time="2026-06-18 10:00:00 CST")

    assert request.normalized_platform == "web"
    assert values["platform"] == "web"
    assert render_scoped_template("chat/main", "platform={{ platform }}", values) == "platform=web"

    runtime_context = build_runtime_context(request, current_time=values["current_time"])
    assert "platform: web" in runtime_context
    assert "chat_type: group" in runtime_context


@pytest.mark.asyncio
async def test_prompt_v2_compile_plan_exposes_platform(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "web_private", "type": "template", "template_key": "chat/branch_private", "chat_types": ["private"], "platforms": ["web"]},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "web_private", "chat_types": ["private"], "platforms": ["web"]},
            {"from": "web_private", "to": "current_user_event", "chat_types": ["private"], "platforms": ["web"]},
        ],
    }
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: compiler.PromptFlow(flow=flow, path=compiler.Path("test-flow.json"), source="test"),
    )

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert plan.platform == "web"
    assert plan.debug["platform"] == "web"
    assert plan.debug["flow_node_ids"] == ["base", "web_private", "current_user_event"]
```

- [ ] **步骤 4：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py -k "platform or overlapping_platform" -v -p no:cacheprovider
```

预期：失败，原因包括 `ordered_nodes_for_chat()` 不接受 `platform`、`PromptCompileRequest` 不接受 `platform` 或模板变量 `platform` 未进入白名单。

- [ ] **步骤 5：实现 `PromptCompileRequest` 和 `PromptPlan` 平台字段**

在 `core/prompt_v2/schema.py` 中加入：

```python
@dataclass(frozen=True)
class PromptPlan:
    engine: str
    chat_type: str
    platform: str
    prompt_key: str
    messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    section_hashes: dict[str, str]
    prompt_sha256: str
    token_estimate: int
    warnings: list[str]
    debug: dict[str, Any]
```

在 `PromptCompileRequest` 的 `chat_type` 后加入字段和属性：

```python
platform: str = "qq"

@property
def normalized_platform(self) -> str:
    value = str(self.platform or "").strip().lower()
    return value or "qq"
```

同时更新所有直接构造 `PromptPlan(...)` 的测试和源码调用，传入 `platform="qq"` 或 `platform=plan.platform`。

- [ ] **步骤 6：实现模板变量和 runtime context 平台输出**

在 `core/prompt_v2/variables.py` 的 `_GLOBAL_VARIABLES` 里加入：

```python
VariableDef("platform", "global", "当前客户端平台", "qq"),
```

在 `core/prompt_v2/context_adapters.py` 中让 `build_template_values()` 返回：

```python
"platform": request.normalized_platform,
```

让 `build_runtime_context()` 开头变为：

```python
platform = request.normalized_platform
lines = ["<runtime_context>", f"platform: {platform}", f"chat_type: {chat_type}"]
```

- [ ] **步骤 7：实现 flow `platforms` 归一化和二维冲突检测**

在 `core/prompt_v2/flow.py` 中加入平台正则和归一化：

```python
import re

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _normalize_platform(platform: str) -> str:
    return str(platform or "").strip().lower() or "qq"


def _normalize_platforms(item: dict[str, Any], *, label: str) -> list[str]:
    raw = item.get("platforms")
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    values = [str(value).strip().lower() for value in raw if str(value).strip()]
    invalid = sorted(value for value in set(values) if not _PLATFORM_RE.fullmatch(value))
    if invalid:
        raise PromptFlowError(f"{label}.platforms 不支持: {', '.join(invalid)}")
    return sorted(set(values), key=values.index)
```

加入通配哨兵和条件 helper：

```python
_ANY_PLATFORM = "*"


def _condition_platforms(item: dict[str, Any]) -> set[str]:
    platforms = _normalize_platforms(item, label="edge")
    return set(platforms or [_ANY_PLATFORM])


def _platform_sets_overlap(left: set[str], right: set[str]) -> bool:
    return _ANY_PLATFORM in left or _ANY_PLATFORM in right or bool(left & right)
```

把 `_applies()` 改为：

```python
def _applies(item: dict[str, Any], chat_type: str, platform: str) -> bool:
    chat_types = item.get("chat_types")
    if chat_types and chat_type not in {str(value).strip().lower() for value in chat_types}:
        return False
    platforms = item.get("platforms")
    if not platforms:
        return True
    if isinstance(platforms, str):
        platforms = [platforms]
    return platform in {str(value).strip().lower() for value in platforms}
```

在 `validate_flow()` 中归一化节点和边的 `platforms`，并用已归一化的 `node_conditions` 与 `outgoing_conditions` 检测歧义。实现时可以把 `outgoing_conditions` 保存为列表：

```python
outgoing_conditions: dict[str, list[tuple[set[str], set[str], str]]] = {}
```

每条 active edge 检查：

```python
for previous_chat_types, previous_platforms, previous_end in outgoing_conditions.get(start, []):
    if active_chat_types & previous_chat_types and _platform_sets_overlap(active_platforms, previous_platforms):
        overlap_chat = ", ".join(sorted(active_chat_types & previous_chat_types))
        raise PromptFlowError(
            f"node {start} 在 {overlap_chat} 同一条件只能有一条出边: {previous_end}, {end}"
        )
outgoing_conditions.setdefault(start, []).append((active_chat_types, active_platforms, end))
```

最后把 `ordered_nodes_for_chat()` 签名改为：

```python
def ordered_nodes_for_chat(flow: dict[str, Any], chat_type: str, platform: str = "qq") -> list[dict[str, Any]]:
```

并在函数内使用归一化后的 `platform` 调用 `_applies(dict(node), chat_type, platform)`。

- [ ] **步骤 8：编译器传入平台并写入 debug**

在 `core/prompt_v2/compiler.py` 中加入：

```python
platform = request.normalized_platform
ordered_nodes = ordered_nodes_for_chat(flow_state.flow, chat_type, platform=platform)
```

debug 增加：

```python
"platform": platform,
```

构造 `PromptPlan` 时传入 `platform=platform`；审计失败返回的新 `PromptPlan` 也保留 `platform=plan.platform`。

- [ ] **步骤 9：运行任务 1 定向测试**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py -k "platform or overlapping_platform" -v -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py -v -p no:cacheprovider
```

预期：两条命令均通过。

- [ ] **步骤 10：提交任务 1**

只暂存任务 1 文件：

```bash
git add tests/test_prompt_v2.py core/prompt_v2/schema.py core/prompt_v2/variables.py core/prompt_v2/context_adapters.py core/prompt_v2/flow.py core/prompt_v2/compiler.py
git commit -m "feat(提示词): 支持平台化编排条件"
```

## 任务 2：Bridge 和 Admin 预览透传 platform

**文件：**
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`tests/test_admin_api.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`api/admin_routes.py`
- 修改：`app/prompt_runtime/preview_service.py`

- [x] **步骤 1：编写 Bridge 输入映射红灯测试**

在 `tests/test_bridge_prompt_v2.py` 追加或扩展现有 `_build_prompt_runtime_input` 测试：

```python
def test_bridge_build_prompt_runtime_input_passes_platform(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge()
    tool_plan = type("ToolPlan", (), {"sent_tool_schemas": []})()

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private",
            platform="web",
            session_id="private_u1",
            user_id="u1",
            group_id="",
            sender_name="用户",
            query="你好",
            persona_text="画像",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]",
            effort_constraint="",
            trace_id="trace-1",
            run_id="run-1",
            is_group=False,
            meta={"platform": "web", "user_id": "u1"},
            tool_plan=tool_plan,
        )
    )

    assert prompt_input.platform == "web"
```

- [x] **步骤 2：编写 `handle_message()` 透传红灯测试**

在 `tests/test_kt_framework.py` 的平台 ToolPlan 测试附近补断言：

```python
async def fake_build_prompt_runtime(prompt_input):
    captured["prompt_platform"] = prompt_input.platform
    return FakePromptRuntimeResult()
```

在测试末尾追加：

```python
assert captured["prompt_platform"] == "web"
```

如果现有 fake result 不是公共 helper，复用同文件已存在的 fake result 结构，不新建跨文件依赖。

- [x] **步骤 3：编写 `build_prompt_runtime()` 透传红灯测试**

在 `tests/test_bridge_prompt_v2.py` 中现有 `fake_compile_prompt_plan` 测试旁追加断言：

```python
async def fake_compile_prompt_plan(request, *, strict_audit=False):
    captured["compile_platform"] = request.platform
    return PromptPlan(
        engine="prompt",
        chat_type="private",
        platform=request.normalized_platform,
        prompt_key="chat_private",
        messages=[{"role": "user", "content": "<user_input>\n你好\n</user_input>"}],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="a" * 64,
        token_estimate=1,
        warnings=[],
        debug={},
    )
```

构造 `PromptRuntimeInput(..., platform="web", ...)` 后断言：

```python
assert captured["compile_platform"] == "web"
```

- [x] **步骤 4：编写 Admin 预览红灯测试**

在 `tests/test_prompt_v2_template_admin.py` 增加：

```python
def test_effective_prompt_preview_accepts_platform(client, auth_header, monkeypatch):
    captured = {}

    async def fake_compile_prompt_plan(request):
        captured["compile_platform"] = request.platform
        from core.prompt_v2.schema import PromptPlan
        return PromptPlan(
            engine="prompt",
            chat_type=request.normalized_chat_type,
            platform=request.normalized_platform,
            prompt_key=request.normalized_prompt_key,
            messages=[{"role": "user", "content": "<user_input>\nhi\n</user_input>"}],
            tool_schemas=[],
            section_hashes={},
            prompt_sha256="b" * 64,
            token_estimate=1,
            warnings=[],
            debug={"platform": request.normalized_platform, "flow_node_ids": ["base"]},
        )

    def fake_build_tool_plan(**kwargs):
        captured["tool_platform"] = kwargs.get("platform")
        return type("ToolPlan", (), {
            "enabled": {},
            "disabled": {},
            "runtime_tool_prompt": "",
            "sent_tool_schemas": [],
            "sha256": "tool-sha",
        })()

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile_prompt_plan)
    monkeypatch.setattr("app.prompt_runtime.preview_service.build_tool_plan", fake_build_tool_plan, raising=False)

    response = client.post(
        "/api/admin/prompt/effective-preview",
        headers=auth_header,
        json={"chat_type": "private", "platform": "web", "user_input": "hi"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["platform"] == "web"
    assert data["prompt_plan"]["platform"] == "web"
    assert captured["compile_platform"] == "web"
    assert captured["tool_platform"] == "web"
```

如果 monkeypatch 路径不匹配当前导入方式，按实际导入点调整，但保留 4 个断言。

- [x] **步骤 5：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py -k "platform" -v -p no:cacheprovider
```

预期：失败，原因包括 dataclass 不接受 `platform`、`PromptRuntimeInput` 没有平台字段或预览响应缺少 `platform`。

- [x] **步骤 6：实现 Bridge 和 runtime 透传**

在 `nanobot_kt/bridge.py` 中修改 dataclass：

```python
class PromptRuntimeAssemblyContext:
    prompt_engine: str
    prompt_mode: str
    prompt_key: str
    chat_type: str
    runtime_chat_type: str
    platform: str
```

在 `_build_prompt_runtime_input()` 返回值中加入：

```python
platform=context.platform,
```

在 `handle_message()` 构造 context 时加入：

```python
platform=platform,
```

在 `nanobot_kt/prompt_runtime.py` 的 `PromptRuntimeInput` 中加入：

```python
platform: str = "qq"
```

构造 `PromptCompileRequest` 时加入：

```python
platform=input.platform,
```

- [x] **步骤 7：实现 Admin 预览平台参数**

在 `api/admin_routes.py` 的 `EffectivePromptPreviewRequest` 加入：

```python
platform: str = "qq"
```

在 `app/prompt_runtime/preview_service.py` 中计算：

```python
platform = _strip(getattr(body, "platform", "")) or "qq"
platform = platform.lower()
```

传给 ToolPlan：

```python
tool_plan = build_tool_plan(
    chat_type=chat_type,
    group_id=group_id,
    user_id=user_id,
    platform=platform,
    runtime_preset=runtime_preset,
    db=db,
)
```

传给 PromptCompileRequest：

```python
platform=platform,
```

响应 dict 中加入：

```python
"platform": platform,
```

- [x] **步骤 8：运行任务 2 定向测试**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py -k "platform" -v -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py -v -p no:cacheprovider
```

预期：两条命令均通过。

- [x] **步骤 9：提交任务 2**

```bash
git add tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_prompt_v2_template_admin.py nanobot_kt/bridge.py nanobot_kt/prompt_runtime.py api/admin_routes.py app/prompt_runtime/preview_service.py .Codex/plans/prompt-platform-chat-type.md
git commit -m "feat(提示词): 透传提示词平台上下文"
```

## 任务 3：迁移 flow 和提示词模板

**文件：**
- 修改：`tests/test_prompt_v2.py`
- 修改：`tests/test_prompt_v2_template_registry.py`
- 修改：`prompts.v2.default/chat/flow.json`
- 修改：`data/prompts_v2/chat/flow.json`
- 修改：`prompts.v2.default/chat/main.md`
- 修改：`data/prompts_v2/chat/main.md`
- 修改：`prompts.v2.default/chat/branch_group.md`
- 修改：`data/prompts_v2/chat/branch_group.md`
- 修改：`prompts.v2.default/chat/branch_private.md`
- 修改：`data/prompts_v2/chat/branch_private.md`
- 创建：`prompts.v2.default/chat/platform/qq/common.md`
- 创建：`data/prompts_v2/chat/platform/qq/common.md`
- 创建：`prompts.v2.default/chat/platform/qq/group.md`
- 创建：`data/prompts_v2/chat/platform/qq/group.md`
- 修改：`prompts.v2.default/tools/reply/usage.md`
- 修改：`data/prompts_v2/tools/reply/usage.md`
- 修改：`prompts.v2.default/tools/sticker_search/usage.md`
- 修改：`data/prompts_v2/tools/sticker_search/usage.md`
- 修改：`prompts.v2.default/tools/image_generation/usage.md`
- 修改：`data/prompts_v2/tools/image_generation/usage.md`

- [x] **步骤 1：编写默认模板二维编译红灯测试**

在 `tests/test_prompt_v2.py` 追加：

```python
@pytest.mark.asyncio
async def test_prompt_v2_default_flow_selects_qq_platform_templates():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="group", platform="qq", user_input="你好"),
    )

    assert "qq_common_policy" in plan.debug["flow_node_ids"]
    assert "qq_group_policy" in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert "QQ 平台" in joined
    assert "QQ 群聊" in joined


@pytest.mark.asyncio
async def test_prompt_v2_default_flow_skips_qq_templates_for_web_private():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert "qq_common_policy" not in plan.debug["flow_node_ids"]
    assert "qq_group_policy" not in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert "QQ 平台" not in joined
    assert "OneBot" not in joined
    assert "CQ 码" not in joined
```

- [x] **步骤 2：编写模板隔离和注册表红灯测试**

在 `tests/test_prompt_v2_template_registry.py` 追加：

```python
def test_prompt_platform_templates_are_addressable():
    from core.prompt_v2.template_loader import load_template
    from core.prompt_v2.template_registry import resolve_template_key

    assert resolve_template_key("chat/platform/qq/common") == "chat/platform/qq/common"
    assert load_template("chat/platform/qq/common").body
    assert load_template("chat/platform/qq/group").body


def test_prompt_platform_words_are_isolated_to_platform_templates():
    from pathlib import Path

    default_root = Path("prompts.v2.default")
    main = (default_root / "chat" / "main.md").read_text(encoding="utf-8")
    group = (default_root / "chat" / "branch_group.md").read_text(encoding="utf-8")
    private = (default_root / "chat" / "branch_private.md").read_text(encoding="utf-8")
    qq_common = (default_root / "chat" / "platform" / "qq" / "common.md").read_text(encoding="utf-8")
    qq_group = (default_root / "chat" / "platform" / "qq" / "group.md").read_text(encoding="utf-8")

    forbidden_common = ("QQ", "OneBot", "CQ 码", "NapCat")
    for text in (main, group, private):
        for needle in forbidden_common:
            assert needle not in text

    assert "QQ 平台" in qq_common
    assert "OneBot" in qq_common
    assert "QQ 群聊" in qq_group
```

- [x] **步骤 3：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py -k "default_flow_selects_qq_platform_templates or default_flow_skips_qq_templates or platform_templates_are_addressable or platform_words_are_isolated" -v -p no:cacheprovider
```

预期：失败，原因包括平台模板文件不存在、默认 flow 不包含平台节点或通用模板仍含 QQ 词。

- [x] **步骤 4：更新两个 `chat/flow.json`**

把 `prompts.v2.default/chat/flow.json` 和 `data/prompts_v2/chat/flow.json` 同步改成同一结构：

```json
{
  "version": 1,
  "nodes": [
    {"id": "base_contract", "type": "template", "label": "system: base contract", "template_key": "chat/main"},
    {"id": "qq_common_policy", "type": "template", "label": "system: QQ platform policy", "template_key": "chat/platform/qq/common", "platforms": ["qq"]},
    {"id": "group_policy", "type": "template", "label": "system: group policy", "template_key": "chat/branch_group", "chat_types": ["group"]},
    {"id": "qq_group_policy", "type": "template", "label": "system: QQ group policy", "template_key": "chat/platform/qq/group", "chat_types": ["group"], "platforms": ["qq"]},
    {"id": "private_policy", "type": "template", "label": "system: private policy", "template_key": "chat/branch_private", "chat_types": ["private"]},
    {"id": "runtime_context", "type": "runtime", "label": "system: runtime_context", "runtime_key": "runtime_context"},
    {"id": "identity_context", "type": "template", "label": "system: identity_context", "template_key": "chat/identity_context"},
    {"id": "persona_reference", "type": "runtime", "label": "system: persona_reference", "runtime_key": "persona_reference"},
    {"id": "conversation_context_header", "type": "runtime", "label": "system: conversation_context_header", "runtime_key": "conversation_context_header"},
    {"id": "history_messages", "type": "runtime", "label": "history: messages", "runtime_key": "history_messages"},
    {"id": "group_context", "type": "runtime", "label": "system: group profile / expression / jargon", "runtime_key": "group_context", "chat_types": ["group"]},
    {"id": "effort_constraint", "type": "runtime", "label": "system: effort_constraint", "runtime_key": "effort_constraint", "optional": true},
    {"id": "runtime_tool_prompt", "type": "runtime", "label": "system: runtime_tool_prompt", "runtime_key": "runtime_tool_prompt"},
    {"id": "current_user_event", "type": "runtime", "label": "user: current_user_input", "runtime_key": "current_user_event"}
  ],
  "edges": [
    {"from": "base_contract", "to": "qq_common_policy", "platforms": ["qq"]},
    {"from": "base_contract", "to": "group_policy", "chat_types": ["group"], "platforms": ["web"]},
    {"from": "base_contract", "to": "private_policy", "chat_types": ["private"], "platforms": ["web"]},
    {"from": "qq_common_policy", "to": "group_policy", "chat_types": ["group"], "platforms": ["qq"]},
    {"from": "qq_common_policy", "to": "private_policy", "chat_types": ["private"], "platforms": ["qq"]},
    {"from": "group_policy", "to": "qq_group_policy", "chat_types": ["group"], "platforms": ["qq"]},
    {"from": "qq_group_policy", "to": "runtime_context", "chat_types": ["group"], "platforms": ["qq"]},
    {"from": "group_policy", "to": "runtime_context", "chat_types": ["group"], "platforms": ["web"]},
    {"from": "private_policy", "to": "runtime_context", "chat_types": ["private"]},
    {"from": "runtime_context", "to": "identity_context"},
    {"from": "identity_context", "to": "persona_reference"},
    {"from": "persona_reference", "to": "conversation_context_header"},
    {"from": "conversation_context_header", "to": "history_messages"},
    {"from": "history_messages", "to": "group_context", "chat_types": ["group"]},
    {"from": "history_messages", "to": "effort_constraint", "chat_types": ["private"]},
    {"from": "group_context", "to": "effort_constraint", "chat_types": ["group"]},
    {"from": "effort_constraint", "to": "runtime_tool_prompt"},
    {"from": "runtime_tool_prompt", "to": "current_user_event"}
  ]
}
```

如果要支持 `synergy` 或其他平台，不能在本任务写死到 flow；缺省非 QQ 平台先走 web 口径需要由调用方传 `platform="web"`。

- [x] **步骤 5：通用化群聊和私聊模板**

把两个根目录下 `chat/branch_group.md` 的开头改成：

```markdown
## 群聊行为

当前对话发生在群聊中。

- 你在多人对话里，不是私聊。说话对象是整个群；需要泛称时用"大家"，不要默认把所有话都说成一对一。
- 不要主动开启新话题，顺着当前讨论往下接。
- 别人没点名你、没叫 bot 名字时，你只是普通参与者。不用每条都接，也不用每条都发表意见。
- 看到有人问具体问题，可以给出简短有用的回答。但不要长篇大论，群聊不是文档站。
- 群里可能有多条消息混在一起。回复时针对当前 user event，不要逐条回应历史里的每句话。
```

保留「群聊上下文使用规则」「群聊发言时机」「群聊工具补充」，但去掉 `@`、斗图、表情包、群友等 QQ 专属词。

把两个根目录下 `chat/branch_private.md` 的开头改成：

```markdown
## 私聊行为

当前对话发生在私聊中。

私聊中可以更认真、更细致地帮用户处理问题，但仍然保持自然口吻：
```

- [x] **步骤 6：新增 QQ 平台模板**

创建两个根目录的 `chat/platform/qq/common.md`：

```markdown
---
name: QQ 平台规则
version: 1
kind: chat
description: Prompt Runtime QQ 平台通用规则；由编排图在 platform=qq 下接入。
---
## QQ 平台

当前客户端平台是 QQ，入站消息可能来自 NapCat 或 OneBot 兼容链路。

- `<runtime_context>` 中可能包含 message_id、self_id、bot_id、bot_aliases 等平台元数据，只用于理解当前消息来源，不要复述。
- `[sticker:<id>]` 和 `[generated_image:<id>]` 是 Nanobot 内部短 token。可以把工具返回的短 token 原样放进 `reply(content)`。
- 出口 renderer 会把短 token 转成 QQ 可发送内容。不要为了发送图片或表情包手写 OneBot CQ 码。
- 直接 CQ 码只作为兼容旧输出的输入格式，不是推荐输出格式。
- `reply_meta` 只表达引用、@ 或发送模式等意图，最终是否转成 QQ 引用或 @ 由出口层决定。
```

创建两个根目录的 `chat/platform/qq/group.md`：

```markdown
---
name: QQ 群聊规则
version: 1
kind: chat
description: Prompt Runtime QQ 群聊规则；由编排图在 platform=qq 且 chat_type=group 下接入。
---
## QQ 群聊

当前对话发生在 QQ 群聊中。

- QQ 群里用户可能通过 @、回复、群昵称或 bot 昵称来点名你。没有明确指向你时，默认不要抢话。
- 群友闲聊、斗图、玩梗、签到、抽卡、金币、菜单命令等没有指向 bot 时，优先调用 `no_reply(reason=...)`。
- 表情包只在斗图、玩梗、用户明确要图或气氛明显适合时使用。不要频繁发表情包，不要用表情包替代必要文字说明。
- 群聊上下文里的 `[msg_id]`、`[时间]`、`[用户名]`、`[发言内容]` 是消息元数据，不要复述。
```

- [x] **步骤 7：清理工具 usage 平台词**

在两个根目录的 `tools/reply/usage.md`、`tools/sticker_search/usage.md`、`tools/image_generation/usage.md` 中，把「QQ 发送前」「OneBot CQ 码」改成平台无关表述：

```markdown
- 这些短 token 是 Nanobot 内部稳定引用，出口 renderer 会转换成当前平台可发送内容。
- 优先使用工具返回的 `reply_token`，不要手写平台私有消息码；平台私有码只用于兼容旧输出，不是推荐格式。
```

- [x] **步骤 8：验证模板目录一致**

运行：

```bash
diff -qr prompts.v2.default data/prompts_v2
```

预期：无输出，退出码 0。

- [x] **步骤 9：运行任务 3 定向测试**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py -k "default_flow_selects_qq_platform_templates or default_flow_skips_qq_templates or platform_templates_are_addressable or platform_words_are_isolated" -v -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py -v -p no:cacheprovider
```

预期：两条命令均通过。

- [x] **步骤 10：提交任务 3**

如果 `data/prompts_v2` 新文件受 ignore 影响，使用 `git add -f` 精确暂存新增文件：

```bash
git add tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py prompts.v2.default/chat/flow.json data/prompts_v2/chat/flow.json prompts.v2.default/chat/main.md data/prompts_v2/chat/main.md prompts.v2.default/chat/branch_group.md data/prompts_v2/chat/branch_group.md prompts.v2.default/chat/branch_private.md data/prompts_v2/chat/branch_private.md prompts.v2.default/tools/reply/usage.md data/prompts_v2/tools/reply/usage.md prompts.v2.default/tools/sticker_search/usage.md data/prompts_v2/tools/sticker_search/usage.md prompts.v2.default/tools/image_generation/usage.md data/prompts_v2/tools/image_generation/usage.md
git add -f prompts.v2.default/chat/platform/qq/common.md data/prompts_v2/chat/platform/qq/common.md prompts.v2.default/chat/platform/qq/group.md data/prompts_v2/chat/platform/qq/group.md
git commit -m "feat(提示词): 拆分 QQ 平台模板"
```

## 任务 4：平台化提示词集成回归

**文件：**
- 修改：`tests/test_prompt_v2.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`tests/test_admin_api.py`

- [ ] **步骤 1：补集成断言**

根据任务 1 到任务 3 的落地结果，补齐以下断言：

```python
assert plan.platform == "qq"
assert "platform: qq" in "\n".join(str(message["content"]) for message in plan.messages)
assert "qq_common_policy" in plan.debug["flow_node_ids"]
```

Web 私聊断言：

```python
assert plan.platform == "web"
assert "platform: web" in "\n".join(str(message["content"]) for message in plan.messages)
assert "qq_common_policy" not in plan.debug["flow_node_ids"]
assert "QQ 平台" not in "\n".join(str(message["content"]) for message in plan.messages)
```

Bridge 断言：

```python
assert captured["tool_plan_platform"] == "web"
assert captured["decision_platform"] == "web"
assert captured["prompt_platform"] == "web"
```

Admin 预览断言：

```python
assert data["platform"] == "web"
assert data["prompt_plan"]["platform"] == "web"
assert "qq_common_policy" not in data["debug"].get("flow_node_ids", [])
```

- [ ] **步骤 2：运行提示词平台定向回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py -k "platform or prompt_v2" -v -p no:cacheprovider
```

预期：通过。

- [ ] **步骤 3：运行 Prompt Runtime 相关完整回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py tests/test_prompt_v2_template_registry.py -v -p no:cacheprovider
```

预期：通过。

- [ ] **步骤 4：提交任务 4**

```bash
git add tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py
git commit -m "test(提示词): 覆盖平台化提示词链路"
```

如果任务 4 没有新增文件改动，只记录验证输出到任务 5 文档收口，不创建空提交。

## 任务 5：文档收口和最终验收

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/prompt-platform-chat-type.md`

- [ ] **步骤 1：同步 `docs/todo.md` 路线项 9**

把路线项 9 的「现状」更新为已落地口径，必须包含：

```markdown
- Prompt Runtime 已按 `platform × chat_type` 过滤 flow。
- `platform` 已从 Bridge metadata 进入 `PromptCompileRequest`、`PromptPlan`、`debug` 和 `<runtime_context>`。
- QQ 专属规则已迁入 `chat/platform/qq/common.md` 与 `chat/platform/qq/group.md`。
- `web × private` 不再注入 QQ 平台模板。
```

保留相邻演进项：

```markdown
- 工具模板 selector 暂不按平台拆分。
- TimingGate task 模板的平台化仍由 TimingGate 路线独立推进。
```

- [ ] **步骤 2：同步 `docs/plan_walkthrough.md`**

把 P2-4 表格状态改为已完成，并新增本阶段详情，记录：

```markdown
- 设计文档提交：`27e632f docs(提示词): 设计平台化提示词分支`
- 实现计划文件：`.Codex/plans/prompt-platform-chat-type.md`
- 任务 1 提交号和验证输出
- 任务 2 提交号和验证输出
- 任务 3 提交号和验证输出
- 任务 4 提交号和验证输出
- 最终全量回归输出
```

- [ ] **步骤 3：勾选本计划任务**

在 `.Codex/plans/prompt-platform-chat-type.md` 中把已完成步骤从 `- [ ]` 改为 `- [x]`，并在每个任务末尾记录实际提交号。

- [ ] **步骤 4：运行文档扫描**

运行：

```bash
python - <<'PY'
from pathlib import Path
paths = [
    Path(".Codex/plans/prompt-platform-chat-type.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
]
needles = ["待" + "定", "后续" + "实现", "类似" + "任务", "添加" + "适当", "为" + "上述", "\ufffd"]
for path in paths:
    text = path.read_text(encoding="utf-8")
    for needle in needles:
        if needle in text:
            raise SystemExit(f"{path}: found {needle}")
    if "T" + "ODO" in text:
        raise SystemExit(f"{path}: found {'T' + 'ODO'}")
print("scan ok")
PY
git diff --check -- .Codex/plans/prompt-platform-chat-type.md docs/todo.md docs/plan_walkthrough.md
```

预期：输出 `scan ok`，`git diff --check` 无输出，退出码 0。

- [ ] **步骤 5：运行最终回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_prompt_v2.py tests/test_bridge_prompt_v2.py tests/test_kt_framework.py tests/test_admin_api.py tests/test_prompt_v2_template_registry.py -v -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：两条命令均通过。

- [ ] **步骤 6：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/prompt-platform-chat-type.md
git commit -m "docs(计划): 收口平台化提示词状态"
```

## 最终验收清单

- [ ] `platform` 从 Bridge metadata 进入 `PromptRuntimeInput`、`PromptCompileRequest`、`PromptPlan`、`debug` 和 `<runtime_context>`。
- [ ] `ordered_nodes_for_chat()` 支持 `platform` 参数，旧调用默认 `qq`。
- [ ] flow 配置支持 `platforms`，并能拒绝二维条件重叠的歧义出边。
- [ ] `qq × group` 注入通用群聊模板、QQ common 模板和 QQ 群聊模板。
- [ ] `qq × private` 注入通用私聊模板和 QQ common 模板。
- [ ] `web × private` 不注入 QQ 平台模板。
- [ ] `chat/main.md`、`chat/branch_group.md`、`chat/branch_private.md` 不再写死 QQ。
- [ ] `prompts.v2.default` 和 `data/prompts_v2` 的相关模板保持一致。
- [ ] Admin 有效预览支持 platform，并让 ToolPlan 与 PromptCompileRequest 使用同一个平台值。
- [ ] Prompt Runtime 定向回归通过。
- [ ] 全量 `python -m pytest tests/ -v` 通过。
