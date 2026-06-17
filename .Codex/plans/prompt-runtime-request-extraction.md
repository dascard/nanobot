# Prompt Runtime 请求组装提取实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `NanobotBridge.handle_message()` 中的 Prompt Runtime 请求组装逻辑提取为可独立测试的边界，为 H29 巨函数拆分和后续 legacy 收口打底。

**架构：** 新增一个纯组装 helper，负责从 `meta`、上下文文本、`tool_plan`、trace 信息和运行时配置中构造 `PromptRuntimeInput`。`handle_message()` 仍负责 session lock、trace 生命周期、tool plan 构建、`build_prompt_runtime()` 调用、conversation 注入和模型调用，确保本阶段不改变业务行为。

**技术栈：** Python 3.12、pytest、FastAPI 现有测试夹具、`nanobot_kt.prompt_runtime.PromptRuntimeInput`。

---

## 文件结构

- 修改：`nanobot_kt/bridge.py`
  - 新增 `PromptRuntimeAssemblyContext` dataclass。
  - 新增 `_build_prompt_runtime_input()` 纯 helper。
  - 将 `handle_message()` 中 `source_message_ids`、`v1_prompt_mode`、`tool_schemas` 和 `PromptRuntimeInput(...)` 组装替换为 helper 调用。
- 修改：`tests/test_bridge_prompt_v2.py`
  - 增加纯 helper 测试，覆盖 V2 默认组装、V1 override 组装、非法 V1 mode fallback、tool schema 读取失败 fallback。
- 修改：`.Codex/plans/prompt-runtime-request-extraction.md`
  - 记录本阶段执行计划。

## 任务 1：为 Prompt Runtime 组装边界写红灯测试

**文件：**
- 修改：`tests/test_bridge_prompt_v2.py`

- [ ] **步骤 1：新增 helper 测试导入和最小 tool plan fake**

在 `tests/test_bridge_prompt_v2.py` 中增加测试用 fake：

```python
def _prompt_tool_plan(**overrides):
    defaults = {
        "runtime_tool_prompt": "<runtime_tool_prompt>工具</runtime_tool_prompt>",
        "sent_tool_schemas": [
            {"type": "function", "function": {"name": "reply"}},
        ],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)
```

- [ ] **步骤 2：新增 V2 组装测试**

新增测试：

```python
def test_bridge_build_prompt_runtime_input_for_v2(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    monkeypatch.setattr(bridge, "_prompt_system_mode", lambda: "managed")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v2",
            prompt_mode="v2",
            prompt_key="chat_group",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1001",
            user_id="u1",
            group_id="1001",
            sender_name="雀",
            query="当前问题",
            persona_text="画像",
            history_header="历史头",
            history_messages=[{"role": "user", "content": "旧消息"}],
            runtime_tool_prompt="工具提示",
            effort_constraint="short",
            trace_id="trace_1",
            run_id="run_1",
            is_group=True,
            meta={
                "sender_id": "sender_1",
                "session_name": "测试群",
                "trigger_reason": "direct",
                "timing_decision": "continue",
                "message_id": "msg_1",
                "source_message_ids": ["msg_0", "", None],
                "self_id": "bot_self",
                "bot_id": "bot_1",
                "character_name": "七濑",
                "bot_aliases": ["bot", ""],
                "group_profile_context": "群画像",
                "expression_context": "表达",
                "jargon_context": "黑话",
                "context_debug": {"group_memory_injected": True},
            },
            tool_plan=_prompt_tool_plan(),
        )
    )

    assert prompt_input.prompt_engine == "v2"
    assert prompt_input.prompt_mode == "managed"
    assert prompt_input.prompt_key == "chat_group"
    assert prompt_input.sender_id == "sender_1"
    assert prompt_input.bot_name == "七濑"
    assert prompt_input.source_message_ids == ["msg_0"]
    assert prompt_input.persona_text == "画像"
    assert prompt_input.tool_schemas == [
        {"type": "function", "function": {"name": "reply"}},
    ]
    assert prompt_input.debug == {"context_debug": {"group_memory_injected": True}}
    assert prompt_input.audit_failure_policy == "fail_fast"
```

- [ ] **步骤 3：新增 V1 override 组装测试**

新增测试：

```python
def test_bridge_build_prompt_runtime_input_for_v1_uses_prompt_mode(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    monkeypatch.setattr(bridge, "_prompt_system_mode", lambda: "shadow")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v1",
            prompt_mode="managed",
            prompt_key="group_chat",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1001",
            user_id="u1",
            group_id="1001",
            sender_name="雀",
            query="当前问题",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="trace_1",
            run_id="run_1",
            is_group=True,
            meta={"prompt_mode_override": "bad"},
            tool_plan=_prompt_tool_plan(sent_tool_schemas=[]),
        )
    )

    assert prompt_input.prompt_engine == "v1"
    assert prompt_input.prompt_mode == "managed"
    assert prompt_input.prompt_key == "group_chat"
    assert prompt_input.persona_text == "无已存储画像"
```

- [ ] **步骤 4：运行红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_for_v2 \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_for_v1_uses_prompt_mode \
  -q -p no:cacheprovider
```

预期：失败，报 `ImportError` 或 `AttributeError`，因为 `PromptRuntimeAssemblyContext` / `_build_prompt_runtime_input` 尚不存在。

## 任务 2：实现最小组装 helper

**文件：**
- 修改：`nanobot_kt/bridge.py`

- [ ] **步骤 1：新增 dataclass 导入和上下文类型**

在文件顶部已有 import 附近加入：

```python
from dataclasses import dataclass
```

在 `NanobotBridge` 类之前新增：

```python
@dataclass(frozen=True)
class PromptRuntimeAssemblyContext:
    prompt_engine: str
    prompt_mode: str
    prompt_key: str
    chat_type: str
    runtime_chat_type: str
    session_id: str
    user_id: str
    group_id: str
    sender_name: str
    query: str
    persona_text: str
    history_header: str
    history_messages: list[dict[str, Any]]
    runtime_tool_prompt: str
    effort_constraint: str
    trace_id: str
    run_id: str
    is_group: bool
    meta: dict[str, Any]
    tool_plan: Any
```

- [ ] **步骤 2：新增 `_build_prompt_runtime_input()`**

在 `_prompt_v2_audit_failure_policy()` 后面新增：

```python
    def _build_prompt_runtime_input(
        self,
        context: PromptRuntimeAssemblyContext,
    ) -> "PromptRuntimeInput":
        from nanobot_kt.prompt_runtime import PromptRuntimeInput

        meta = dict(context.meta or {})
        source_message_ids = [
            str(x) for x in (meta.get("source_message_ids") or [])
            if str(x).strip()
        ]
        v1_prompt_mode = str(
            meta.get("prompt_system_mode_override")
            or meta.get("prompt_mode_override")
            or self._prompt_system_mode()
        ).strip().lower()
        if v1_prompt_mode not in {"legacy", "shadow", "managed"}:
            v1_prompt_mode = "shadow"
        try:
            tool_schemas = list(context.tool_plan.sent_tool_schemas)
        except Exception as exc:
            logger.warning("[PromptV2] failed to read tool schemas: %s", exc)
            tool_schemas = []

        return PromptRuntimeInput(
            prompt_engine=context.prompt_engine,
            prompt_mode=v1_prompt_mode if context.prompt_engine == "v2" else context.prompt_mode,
            prompt_key=context.prompt_key,
            chat_type=context.chat_type,
            runtime_chat_type=context.runtime_chat_type,
            session_id=context.session_id,
            user_id=context.user_id,
            group_id=context.group_id,
            sender_name=context.sender_name,
            sender_id=str(meta.get("sender_id") or meta.get("user_id") or context.user_id),
            session_name=str(meta.get("session_name") or ""),
            trigger_reason=str(meta.get("trigger_reason") or ""),
            timing_decision=str(meta.get("timing_decision") or ""),
            current_message_id=str(meta.get("message_id") or ""),
            source_message_ids=source_message_ids,
            self_id=str(meta.get("self_id") or ""),
            bot_id=str(meta.get("bot_id") or ""),
            bot_name=str(meta.get("bot_name") or meta.get("character_name") or ""),
            bot_aliases=list(meta.get("bot_aliases") or []),
            user_input=context.query,
            persona_text=context.persona_text or "无已存储画像",
            history_header=context.history_header,
            history_messages=context.history_messages,
            runtime_tool_prompt=context.runtime_tool_prompt,
            effort_constraint=context.effort_constraint,
            trace_id=context.trace_id,
            run_id=context.run_id,
            is_group=context.is_group,
            group_profile_context=str(meta.get("group_profile_context") or ""),
            expression_context=str(meta.get("expression_context") or ""),
            jargon_context=str(meta.get("jargon_context") or ""),
            tool_schemas=tool_schemas,
            debug={"context_debug": meta.get("context_debug") or {}},
            audit_failure_policy=self._prompt_v2_audit_failure_policy(),
        )
```

- [ ] **步骤 3：运行红灯测试验证转绿**

运行：

```bash
python -B -m pytest \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_for_v2 \
  tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_for_v1_uses_prompt_mode \
  -q -p no:cacheprovider
```

预期：2 个测试通过。

## 任务 3：让 `handle_message()` 使用 helper

**文件：**
- 修改：`nanobot_kt/bridge.py`

- [ ] **步骤 1：替换内联 `PromptRuntimeInput(...)`**

将 `handle_message()` 中 `source_message_ids`、`v1_prompt_mode`、`tool_schemas` 和内联 `PromptRuntimeInput(...)` 替换为：

```python
                prompt_input = self._build_prompt_runtime_input(
                    PromptRuntimeAssemblyContext(
                        prompt_engine=prompt_engine,
                        prompt_mode=prompt_mode,
                        prompt_key=prompt_key,
                        chat_type=chat_type,
                        runtime_chat_type=runtime_chat_type,
                        session_id=session_id,
                        user_id=user_id,
                        group_id=group_id,
                        sender_name=sender_name,
                        query=query,
                        persona_text=persona_text,
                        history_header=history_header,
                        history_messages=history_messages,
                        runtime_tool_prompt=runtime_tool_prompt,
                        effort_constraint=effort_constraint,
                        trace_id=trace_id,
                        run_id=run_handle.run_id,
                        is_group=is_group,
                        meta=meta,
                        tool_plan=tool_plan,
                    )
                )
                prompt_build = await build_prompt_runtime(prompt_input)
```

- [ ] **步骤 2：保持异常处理不变**

`except PromptRuntimeAuditFailure`、`run_meta.update(prompt_build.meta_update)`、`RunTracer.update_prompt_source()` 和 `apply_prompt_messages()` 不移动。

- [ ] **步骤 3：运行 bridge prompt 相关测试**

运行：

```bash
python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_streaming_bridge.py -q -p no:cacheprovider
```

预期：全部通过。

## 任务 4：验证和提交

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 创建：`.codex/plans/prompt-runtime-request-extraction.md`

- [ ] **步骤 1：格式检查**

运行：

```bash
git diff --check -- nanobot_kt/bridge.py tests/test_bridge_prompt_v2.py .codex/plans/prompt-runtime-request-extraction.md
```

预期：无输出。

- [ ] **步骤 2：定向回归**

运行：

```bash
python -B -m pytest \
  tests/test_bridge_prompt_v2.py \
  tests/test_streaming_bridge.py \
  tests/test_prompt_runtime_bootstrap.py \
  tests/test_prompt_manifest.py \
  tests/test_reply_admin.py \
  -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 3：全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [ ] **步骤 4：提交计划**

如果只提交计划文件：

```bash
git add .codex/plans/prompt-runtime-request-extraction.md
git commit -m "docs(计划): 记录提示词运行时组装提取计划"
```

- [ ] **步骤 5：提交实现**

实现完成后：

```bash
git add nanobot_kt/bridge.py tests/test_bridge_prompt_v2.py
git commit -m "refactor(桥接): 提取提示词运行时组装"
```
