# Prompt V1 资产删除与去版本化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 迁移仍依赖旧 PromptManager / legacy prompt 的后台任务，封存主回复 V1 live 分支，删除不再被 live 使用的旧资产，并为无版本 prompt runtime 命名提供兼容层。

**架构：** 先新增 V2 task template 渲染边界，把 `classifier_legacy` 和 `memory_extract` 从 `core.prompt_runtime` 迁出；再让 `NanobotBridge` / `build_prompt_runtime()` 只接受 canonical prompt runtime 主路径；最后按引用清点删除 legacy 模块、旧模板和 WebUI / admin 迁移页。历史 trace、旧 DB 字段和旧 URL 以兼容读取方式保留，不作为 live 执行路径。

**技术栈：** Python 3.13、pytest、FastAPI、SQLAlchemy in-memory SQLite、Prompt Runtime V2、React/Vite WebUI。

---

## 文件结构

- 创建：`core/prompt_v2/task_templates.py`
  - 提供后台任务模板渲染 helper，统一调用 `load_template()` 与 `render_scoped_template()`。
- 修改：`core/prompt_v2/variables.py`
  - 为 `tasks/classifier_legacy`、`tasks/private_decision`、`tasks/timing_gate`、`tasks/memory_extract` 增加作用域变量白名单。
- 修改：`core/prompt_v2/template_registry.py`
  - 增加 `classifier_legacy`、`private_decision` 到 V2 task template alias。
- 创建：`prompts.v2.default/tasks/classifier_legacy.md`
  - 迁移旧 `prompts.default/classifier_legacy.md` 的输出契约。
- 修改：`prompts.v2.default/tasks/memory_extract.md`
  - 从旧 `prompts.default/memory_extract.md` 迁移完整记忆抽取约束。
- 创建：`data/prompts_v2/tasks/classifier_legacy.md`
  - 让当前运行时目录立即具备新任务模板，不依赖下次 bootstrap 复制。
- 修改：`data/prompts_v2/tasks/memory_extract.md`
  - 同步默认模板内容，避免运行时覆盖继续保留占位文案。
- 修改：`clients/classifier_client.py`
  - `call_model_route()` 对分类器 route 使用 V2 task template，不再调用 `core.prompt_runtime.render_model_messages()`。
- 修改：`core/legacy_adapter.py`
  - `memory_extract` 使用 V2 task template，不再调用 `core.prompt_runtime.render_prompt_content()`。
- 修改：`tests/test_model_router.py`
  - 将 PromptManager managed-mode 测试改为 V2 task template 渲染测试。
- 修改：`tests/test_evolution.py`
  - 增加 `memory_extract` 使用 V2 task template 的红绿测试。
- 修改：`nanobot_kt/bridge.py`
  - P1-6 中封存 `v1` live override 后，engine 解析统一回 canonical 主路径。
- 修改：`nanobot_kt/prompt_runtime.py`
  - 删除或拒绝 `_build_v1_prompt()` live 分支。
- 修改：`tests/test_bridge_prompt_v2.py`
  - 更新显式 V1 override 测试为“不进入 live V1 / PromptAssembler”。
- 修改：`prompt_manifest.json`
  - 从 `v1 rollback_only + v2 active` 过渡到单 canonical prompt runtime，并保留旧路径说明。
- 修改：`creatures/nanobot/config.yaml`
  - 移除 `system_prompt_file: prompt.md` 依赖。
- 删除：`creatures/nanobot/prompt.md`
- 删除：`scripts/build_nanobot_prompt.py`
- 删除：`core/prompt_runtime.py`
- 删除：`core/prompt_assembler.py`
- 删除：`core/prompt_compiler.py`
- 删除：`core/legacy_prompt_runtime.py`
- 删除或缩减：`prompts.default/`、`prompts.legacy.default/`、`data/prompt_fragments/`、`data/runtime_prompt/prompt.md`
  - 删除前必须完成导出 / diff 说明和测试白名单。
- 修改：`api/admin_routes.py`
  - 删除旧 managed / legacy prompt GET 迁移路由，或将其改为明确下线响应；保留 V2 effective preview。
- 修改：`webui/src/App.jsx`
  - 删除 `/prompt-legacy`、`/prompts` 直达路由。
- 修改：`webui/src/features/prompt/PromptPages.jsx`
  - 删除 legacy / managed 只读组件；保留 V2 template workbench 和 effective preview。
- 修改：`tests/test_prompt_legacy_admin_readonly.py`、`tests/test_prompt_trace_admin.py`、`tests/test_webui_prompt_runtime_ui.py`
  - 将“只读迁移入口存在”断言改为“旧入口已下线或只保留 V2 导出/预览”。
- 修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`AGENTS.md`、`README.md`
  - 删除对 `creatures/nanobot/prompt.md` 和旧 prompt builder 的当前态要求，保留历史说明。

## 当前事实

- P1-5 已完成：`fallback_v1` 固定 fail-fast，legacy / managed 写接口返回 410，WebUI 旧页面只读，reply-test / reply-eval 默认 V2。
- 当前仍存在 V1 live 入口：`build_prompt_runtime()` 的 `input.prompt_engine != "v2"` 会调用 `_build_v1_prompt()`，并最终调用 `PromptAssembler`。
- 当前仍存在旧后台任务入口：`clients/classifier_client.call_model_route()` 通过 `core.prompt_runtime.render_model_messages()` 读取旧模板；`core/legacy_adapter.py` 通过 `render_prompt_content("memory_extract")` 读取旧模板。
- 当前仍存在硬依赖：`creatures/nanobot/config.yaml` 声明 `system_prompt_file: prompt.md`，`tests/test_prompt_contract.py` 依赖 `scripts/build_nanobot_prompt.py`。

## 任务 1：新增 V2 task template 渲染边界

**文件：**
- 创建：`core/prompt_v2/task_templates.py`
- 修改：`core/prompt_v2/variables.py`
- 修改：`core/prompt_v2/template_registry.py`
- 创建：`prompts.v2.default/tasks/classifier_legacy.md`
- 创建：`data/prompts_v2/tasks/classifier_legacy.md`
- 修改：`prompts.v2.default/tasks/memory_extract.md`
- 修改：`data/prompts_v2/tasks/memory_extract.md`
- 测试：`tests/test_prompt_v2.py`

- [x] **步骤 1：写红灯测试**

在 `tests/test_prompt_v2.py` 新增测试：

```python
def test_prompt_v2_renders_classifier_legacy_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    task_path = default_dir / "tasks" / "classifier_legacy.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 旧分类器兼容\nversion: 1\nkind: task\ntool_name: classifier_legacy\n---\n"
        "{{ system_prompt }}\n待判定消息:\n{{ message }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.task_templates import render_task_prompt

    rendered = render_task_prompt(
        "classifier_legacy",
        {"system_prompt": "旧系统", "message": "ping"},
        fallback_text="fallback",
    )

    assert "旧系统" in rendered
    assert "待判定消息:" in rendered
    assert "ping" in rendered
```

同文件新增 `memory_extract` 变量测试：

```python
def test_prompt_v2_renders_memory_extract_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    task_path = default_dir / "tasks" / "memory_extract.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 记忆抽取\nversion: 1\nkind: task\ntool_name: memory_extract\n---\n"
        "已有记忆:\n{{ existing_memory }}\n对话:\n{{ conversation }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(default_dir / "runtime"))

    from core.prompt_v2.task_templates import render_task_prompt

    rendered = render_task_prompt(
        "memory_extract",
        {"conversation": "用户: 喜欢 TypeScript", "existing_memory": "{}"},
        fallback_text="fallback",
    )

    assert "喜欢 TypeScript" in rendered
    assert "已有记忆:" in rendered
```

- [x] **步骤 2：运行红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_prompt_v2.py::test_prompt_v2_renders_classifier_legacy_task_template tests/test_prompt_v2.py::test_prompt_v2_renders_memory_extract_task_template -q -p no:cacheprovider
```

预期：FAIL，原因是 `core.prompt_v2.task_templates` 不存在，或变量白名单不允许 `conversation` / `existing_memory` / `message` / `system_prompt`。

- [x] **步骤 3：新增 V2 task helper**

创建 `core/prompt_v2/task_templates.py`：

```python
from __future__ import annotations

import logging
from typing import Any

from core.prompt_v2.template_loader import load_template
from core.prompt_v2.variables import render_scoped_template

logger = logging.getLogger("nanobot.prompt_v2.task_templates")


def render_task_prompt(prompt_key: str, values: dict[str, Any], *, fallback_text: str = "") -> str:
    try:
        template = load_template(prompt_key)
        rendered = render_scoped_template(template.prompt_key, template.body, values or {}).strip()
        return rendered or str(fallback_text or "")
    except Exception as exc:
        logger.warning("[PromptV2Task] render failed key=%s fallback=legacy error=%s", prompt_key, exc)
        return str(fallback_text or "")


def render_task_messages(
    prompt_key: str,
    values: dict[str, Any],
    *,
    fallback_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rendered = render_task_prompt(prompt_key, values, fallback_text="")
    if not rendered:
        return list(fallback_messages or [])
    return [{"role": "system", "content": rendered}]
```

- [x] **步骤 4：补变量作用域和 alias**

在 `core/prompt_v2/variables.py` 添加：

```python
_CLASSIFIER_TASK_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("message", "classifier_task", "待判定消息", "ping"),
    VariableDef("system_prompt", "classifier_task", "调用方旧系统提示", "只输出 JSON"),
    VariableDef("pending_text", "classifier_task", "待判定群聊文本", "ping"),
    VariableDef("recent_context", "classifier_task", "近期上下文", "上一句"),
    VariableDef("bot_name", "classifier_task", "机器人名称", "七濑"),
    VariableDef("group_profile", "classifier_task", "群体画像", "技术群"),
)

_MEMORY_EXTRACT_VARIABLES: tuple[VariableDef, ...] = (
    VariableDef("conversation", "memory_extract", "待抽取的对话文本", "用户: 喜欢 TypeScript"),
    VariableDef("existing_memory", "memory_extract", "已有用户记忆", "{}"),
)
```

并在 `_scoped_variables()` 中返回：

```python
if normalized in {"tasks/classifier_legacy", "tasks/private_decision", "tasks/timing_gate"}:
    return _CLASSIFIER_TASK_VARIABLES
if normalized == "tasks/memory_extract":
    return _MEMORY_EXTRACT_VARIABLES
```

在 `core/prompt_v2/template_registry.py` 的 `_LEGACY_ALIASES` 添加：

```python
"classifier_legacy": "tasks/classifier_legacy",
"private_decision": "tasks/private_decision",
```

并在 `_TASK_TOOL_NAMES` 添加：

```python
"tasks/classifier_legacy": "classifier_legacy",
"tasks/private_decision": "private_decision",
```

- [x] **步骤 5：迁移模板内容**

新增 `prompts.v2.default/tasks/classifier_legacy.md` 和 `data/prompts_v2/tasks/classifier_legacy.md`：

```markdown
---
name: 旧分类器兼容
version: 1
kind: task
tool_name: classifier_legacy
description: 旧二分类回复判定模板，保留原输出契约。
---
{{ system_prompt }}

待判定消息:
{{ message }}
```

将 `prompts.v2.default/tasks/memory_extract.md` 与 `data/prompts_v2/tasks/memory_extract.md` 改为：

```markdown
---
name: 记忆抽取
version: 1
kind: task
tool_name: memory_extract
description: 从对话中提取稳定用户记忆。
---
你负责从对话中提取稳定、可复用的用户信息。

只提取:
- 长期偏好
- 稳定事实
- 项目或工作约束
- 明确反复出现的行为模式

不要提取:
- 机器人行为、工具行为或系统提示
- 一次性的情绪表达
- 对当次回复的临时要求
- NEW/UPDATE/ARCHIVE 这类状态标签

已有记忆:
{{ existing_memory }}

对话:
{{ conversation }}
```

- [x] **步骤 6：运行任务 1 测试**

运行步骤 2 命令。

预期：PASS。

- [ ] **步骤 7：提交任务 1**

运行：

```bash
git add core/prompt_v2/task_templates.py core/prompt_v2/variables.py core/prompt_v2/template_registry.py prompts.v2.default/tasks/classifier_legacy.md data/prompts_v2/tasks/classifier_legacy.md prompts.v2.default/tasks/memory_extract.md data/prompts_v2/tasks/memory_extract.md tests/test_prompt_v2.py
git commit -m "feat(提示词): 添加后台任务模板渲染"
```

## 任务 2：迁移 `classifier_legacy` 到 V2 task template

**文件：**
- 修改：`clients/classifier_client.py`
- 修改：`tests/test_model_router.py`

- [x] **步骤 1：改写红灯测试**

将 `tests/test_model_router.py` 中 `test_call_model_route_uses_prompt_manager_in_managed_mode` 改名为 `test_call_model_route_uses_v2_task_template_for_classifier_routes`，并改成：

```python
def test_call_model_route_uses_v2_task_template_for_classifier_routes(self, tmp_path, monkeypatch):
    import json

    from clients.classifier_client import call_model_route

    default_dir = tmp_path / "prompts_v2"
    task_path = default_dir / "tasks" / "timing_gate.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: Timing Gate\nversion: 1\nkind: task\ntool_name: timing_gate\n---\n"
        "V2 判定: {{ pending_text }} / {{ bot_name }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(tmp_path / "runtime_v2"))
    monkeypatch.setattr(
        "core.prompt_runtime.render_model_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("classifier routes must not use old PromptManager runtime")
        ),
    )

    values = {
        "prompt_system.mode": "managed",
        "model.route.timing_gate.base_url": "http://local-test/v1",
        "model.route.timing_gate.model": "unit-model",
        "model.route.timing_gate.max_tokens": 80,
        "model.route.timing_gate.temperature": 0,
        "model.route.timing_gate.timeout": 5,
        "model.route.timing_gate.enable_thinking": "false",
    }
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda key, default=None: values.get(key, default),
    )

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

    class FakeOpener:
        def open(self, req, timeout=0):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args, **_kwargs: FakeOpener())

    assert call_model_route(route_key="timing_gate", system_prompt="legacy system", user_message="ping") == "ok"

    messages = captured["payload"]["messages"]
    assert messages == [{"role": "system", "content": "V2 判定: ping / "}]
    assert "legacy system" not in json.dumps(messages, ensure_ascii=False)
    assert captured["payload"]["enable_thinking"] is False
```

- [x] **步骤 2：运行红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_model_router.py::TestClassifierRouteProviderResolution::test_call_model_route_uses_v2_task_template_for_classifier_routes -q -p no:cacheprovider
```

预期：FAIL，当前代码仍调用 `core.prompt_runtime.render_model_messages()`。

- [x] **步骤 3：修改 `call_model_route()`**

在 `clients/classifier_client.py` 中替换旧 `render_model_messages()` 分支：

```python
        try:
            from core.prompt_v2.task_templates import render_task_messages

            prompt_key = {
                "timing_gate": "timing_gate",
                "private_decision": "private_decision",
                "classifier_legacy": "classifier_legacy",
            }.get(route_key, "")
            if prompt_key:
                messages = render_task_messages(
                    prompt_key,
                    {
                        "message": user_message,
                        "system_prompt": system_prompt,
                        "pending_text": user_message,
                        "recent_context": "",
                        "bot_name": "",
                        "group_profile": "",
                    },
                    fallback_messages=messages,
                )
        except Exception as e:
            logger.warning("[call_model_route] Prompt V2 task render fallback route=%s error=%s", route_key, e)
```

- [x] **步骤 4：运行任务 2 测试**

运行步骤 2 命令。

预期：PASS。

- [x] **步骤 5：运行分类器路由回归**

运行：

```bash
python -B -m pytest tests/test_model_router.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 6：提交任务 2**

运行：

```bash
git add clients/classifier_client.py tests/test_model_router.py
git commit -m "refactor(提示词): 分类器改用任务模板"
```

## 任务 3：迁移 `memory_extract` 到 V2 task template

**文件：**
- 修改：`core/legacy_adapter.py`
- 修改：`tests/test_evolution.py`

- [ ] **步骤 1：写红灯测试**

在 `tests/test_evolution.py` 新增：

```python
def test_legacy_adapter_memory_extract_uses_v2_task_template(monkeypatch):
    import asyncio

    from core.legacy_adapter import LegacyEvolutionEngine

    captured = {}

    async def fake_run(logs, provider):
        return {}

    class FakeMemory:
        def get_unprocessed_logs(self, user_id):
            return [{"id": 1, "role": "user", "content": "我长期使用 Python"}]

        def get_user_persona(self, user_id):
            return "{}"

        def mark_logs_processed(self, ids):
            captured["processed"] = ids

    class FakeProvider:
        async def invoke_raw(self, *, query, system_prompt, user_id, model_tier):
            captured["query"] = query
            return '{"candidates":[]}'

    monkeypatch.setattr(
        "core.prompt_v2.task_templates.render_task_prompt",
        lambda key, values, fallback_text="": f"V2 记忆模板: {values['conversation']} / {values['existing_memory']}",
    )
    monkeypatch.setattr(
        "core.prompt_runtime.render_prompt_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("memory_extract must not use old PromptManager runtime")
        ),
    )

    engine = LegacyEvolutionEngine.__new__(LegacyEvolutionEngine)
    engine.memory = FakeMemory()
    engine.provider = FakeProvider()
    engine.log_analyst = type("Analyst", (), {"run": fake_run})()

    asyncio.run(engine.evolve("u1"))

    assert "V2 记忆模板:" in captured["query"]
    assert "我长期使用 Python" in captured["query"]
    assert captured["processed"] == [1]
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_evolution.py::test_legacy_adapter_memory_extract_uses_v2_task_template -q -p no:cacheprovider
```

预期：FAIL，当前代码仍导入并调用 `core.prompt_runtime.render_prompt_content()`。

- [ ] **步骤 3：修改 `core/legacy_adapter.py`**

将旧渲染分支替换为：

```python
        try:
            from core.prompt_v2.task_templates import render_task_prompt

            extraction_prompt = render_task_prompt(
                "memory_extract",
                {"conversation": logs_text, "existing_memory": existing_persona},
                fallback_text=extraction_prompt,
            )
        except Exception:
            pass
```

- [ ] **步骤 4：运行任务 3 测试**

运行步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：运行 evolution / memory 回归**

运行：

```bash
python -B -m pytest tests/test_evolution.py tests/test_prompt_v2.py tests/test_group_memory_extraction_service.py tests/test_group_memory_injection.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 6：提交任务 3**

运行：

```bash
git add core/legacy_adapter.py tests/test_evolution.py
git commit -m "refactor(记忆): 记忆抽取改用任务模板"
```

## 任务 4：封存主回复 V1 live 分支

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_streaming_bridge.py`

- [ ] **步骤 1：改写红灯测试**

在 `tests/test_bridge_prompt_v2.py` 中将 `test_bridge_resolve_prompt_runtime_engine_honors_v1_override_and_invalid_falls_back` 改为：

```python
def test_bridge_resolve_prompt_runtime_engine_treats_v1_as_canonical_runtime(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v1")

    assert bridge._prompt_runtime_engine() == "v2"
    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "v1"}) == "v2"
    assert bridge._resolve_prompt_runtime_engine({"prompt_engine_override": "v1"}) == "v2"
    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "bad"}) == "v2"
```

新增 `build_prompt_runtime()` 单元测试：

```python
@pytest.mark.asyncio
async def test_build_prompt_runtime_rejects_v1_live_prompt(monkeypatch):
    from nanobot_kt.prompt_runtime import PromptRuntimeInput, build_prompt_runtime

    monkeypatch.setattr(
        "core.prompt_assembler.PromptAssembler.build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PromptAssembler must not run after V1 live branch removal")
        ),
    )

    with pytest.raises(ValueError, match="unsupported prompt engine"):
        await build_prompt_runtime(PromptRuntimeInput(
            prompt_engine="v1",
            prompt_mode="legacy",
            prompt_key="group_chat",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1",
            user_id="u1",
            group_id="1",
            sender_name="雀",
            sender_id="u1",
            session_name="",
            trigger_reason="",
            timing_decision="",
            current_message_id="",
            source_message_ids=[],
            self_id="",
            bot_id="",
            bot_name="",
            bot_aliases=[],
            user_input="hi",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="t",
            run_id="r",
            is_group=True,
        ))
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
python -B -m pytest tests/test_bridge_prompt_v2.py::test_bridge_resolve_prompt_runtime_engine_treats_v1_as_canonical_runtime tests/test_bridge_prompt_v2.py::test_build_prompt_runtime_rejects_v1_live_prompt -q -p no:cacheprovider
```

预期：FAIL，当前 resolver 仍允许 `v1`，`build_prompt_runtime()` 仍调用 `PromptAssembler`。

- [ ] **步骤 3：修改 bridge engine resolver**

在 `nanobot_kt/bridge.py` 中把 `_prompt_runtime_engine()` 和 `_resolve_prompt_runtime_engine()` 固定为新主路径：

```python
    def _prompt_runtime_engine(self) -> str:
        try:
            from core.settings_service import settings
            engine = str(settings.get("prompt_runtime.engine", "v2") or "v2").strip().lower()
        except Exception:
            engine = "v2"
        if engine == "v1":
            logger.warning("[PromptRuntime] engine=v1 is removed from live path; using canonical runtime")
        return "v2"

    def _resolve_prompt_runtime_engine(self, meta: dict[str, Any]) -> str:
        prompt_engine = str(
            meta.get("prompt_runtime_engine_override")
            or meta.get("prompt_engine_override")
            or self._prompt_runtime_engine()
        ).strip().lower()
        if prompt_engine == "v1":
            logger.warning("[PromptRuntime] v1 metadata override ignored after P1-6")
        return "v2"
```

在 `handle_message()` 中删除非 V2 prompt key / mode 分支，让主路径只生成 `chat_group` / `chat_private`。

- [ ] **步骤 4：修改 `build_prompt_runtime()`**

在 `nanobot_kt/prompt_runtime.py` 中删除 `_v1_prompt_key()`、`_v1_prompt_mode()` 和 `_build_v1_prompt()`，并在入口加 guard：

```python
async def build_prompt_runtime(input: PromptRuntimeInput) -> PromptRuntimeResult:
    if input.prompt_engine not in {"v2", "canonical", "prompt"}:
        raise ValueError(f"unsupported prompt engine for live runtime: {input.prompt_engine}")
```

保留 `v2` 作为兼容输入，返回结果仍可先写 `prompt_mode="v2"`，去版本化在后续任务处理。

- [ ] **步骤 5：更新受影响测试**

更新 `tests/test_bridge_prompt_v2.py` 中显式 V1 override 断言：不再期待 `prompt_key == "group_chat"` 或 `prompt_mode == "managed"`，改为期待 `chat_group` / `v2`。

更新 `tests/test_streaming_bridge.py` 中 fake `build_prompt_runtime()` 的字段断言，确保它接受 `v2` 兼容值。

- [ ] **步骤 6：运行任务 4 回归**

运行：

```bash
python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_streaming_bridge.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 7：提交任务 4**

运行：

```bash
git add nanobot_kt/bridge.py nanobot_kt/prompt_runtime.py tests/test_bridge_prompt_v2.py tests/test_streaming_bridge.py
git commit -m "refactor(提示词): 封存旧版运行时分支"
```

## 任务 5：下线 legacy 管理页和旧 admin 迁移路由

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`webui/src/App.jsx`
- 修改：`webui/src/features/prompt/PromptPages.jsx`
- 修改：`tests/test_prompt_legacy_admin_readonly.py`
- 修改：`tests/test_prompt_trace_admin.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`

- [ ] **步骤 1：写红灯测试**

将 `tests/test_webui_prompt_runtime_ui.py::test_prompt_preview_defaults_to_v2_and_prompt_path_redirects` 改为断言旧直达路由不存在：

```python
assert '<Route path="/prompt" element={<Navigate to="/prompt-preview" replace />} />' in source
assert '<Route path="/prompt-legacy"' not in source
assert '<Route path="/prompts"' not in source
```

将 `test_legacy_prompt_pages_are_readonly_migration_views` 改名为 `test_legacy_prompt_pages_are_removed_from_webui`：

```python
def test_legacy_prompt_pages_are_removed_from_webui():
    app_source = APP_JS.read_text(encoding="utf-8")
    prompt_source = PROMPT_JS.read_text(encoding="utf-8")

    assert "PromptPage" not in app_source
    assert "ManagedPromptsPage" not in app_source
    assert "export function PromptPage()" not in prompt_source
    assert "export function ManagedPromptsPage()" not in prompt_source
    assert "/prompt/fragments" not in prompt_source
    assert "/prompts/" not in prompt_source
```

在 `tests/test_prompt_legacy_admin_readonly.py` 中把旧 GET 只读测试改为下线策略：

```python
@pytest.mark.parametrize("path", [
    "/api/v1/admin/prompts",
    "/api/v1/admin/prompts/group_chat",
    "/api/v1/admin/prompt",
    "/api/v1/admin/prompt/fragments",
    "/api/v1/admin/prompt/backups",
])
def test_legacy_prompt_read_endpoints_are_gone(client, auth_header, path):
    response = client.get(path, headers=auth_header)
    assert response.status_code in {404, 410}
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
python -B -m pytest tests/test_prompt_legacy_admin_readonly.py tests/test_prompt_trace_admin.py tests/test_webui_prompt_runtime_ui.py -q -p no:cacheprovider
```

预期：FAIL，旧 GET / WebUI route 仍存在。

- [ ] **步骤 3：删除后端旧 route 区块**

在 `api/admin_routes.py` 删除 managed prompt route 区块和 legacy prompt route 区块，保留 `POST /prompt/effective-preview` 的 V2 分支。删除不再使用的 `PromptSaveRequest`、`PromptPreviewRequest`、`PromptRollbackRequest` 和 `_prompt_metrics()`。

如果为了兼容旧客户端选择 410 而不是 404，保留最小 route：

```python
@router.api_route("/prompt/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
def legacy_prompt_routes_removed(path: str, _auth=Depends(verify_admin)):
    raise HTTPException(410, "Legacy prompt 管理入口已下线；请使用 Prompt 模板页面")
```

- [ ] **步骤 4：删除前端旧页面**

在 `webui/src/App.jsx` 删除 `PromptPage`、`ManagedPromptsPage` import 和 `/prompt-legacy`、`/prompts` route。保留 `/prompt` 到 `/prompt-preview` redirect。

在 `webui/src/features/prompt/PromptPages.jsx` 删除 `PromptPage` 和 `ManagedPromptsPage` 组件，以及只服务它们的 state / API 调用。

- [ ] **步骤 5：运行任务 5 回归**

运行：

```bash
python -B -m pytest tests/test_prompt_legacy_admin_readonly.py tests/test_prompt_trace_admin.py tests/test_webui_prompt_runtime_ui.py -q -p no:cacheprovider
cd webui && npm run build
```

预期：PASS。

- [ ] **步骤 6：提交任务 5**

运行：

```bash
git add api/admin_routes.py webui/src/App.jsx webui/src/features/prompt/PromptPages.jsx tests/test_prompt_legacy_admin_readonly.py tests/test_prompt_trace_admin.py tests/test_webui_prompt_runtime_ui.py
git commit -m "refactor(提示词): 下线旧版管理入口"
```

## 任务 6：删除 legacy prompt 资产与构建脚本

**文件：**
- 修改：`creatures/nanobot/config.yaml`
- 删除：`creatures/nanobot/prompt.md`
- 删除：`scripts/build_nanobot_prompt.py`
- 删除：`core/prompt_runtime.py`
- 删除：`core/prompt_assembler.py`
- 删除：`core/prompt_compiler.py`
- 删除：`core/legacy_prompt_runtime.py`
- 删除或缩减：`prompts.default/`
- 删除：`prompts.legacy.default/`
- 修改：`tests/test_prompt_contract.py`
- 修改：`tests/test_prompt_manager.py`
- 修改：`tests/test_prompt_assembler.py`
- 修改：`tests/test_legacy_prompt_runtime.py`
- 修改：`tests/test_prompt_manifest.py`

- [ ] **步骤 1：写守卫红灯测试**

新增 `tests/test_prompt_legacy_removal.py`：

```python
from pathlib import Path


def test_legacy_prompt_modules_are_removed_from_live_tree():
    removed = [
        "core/prompt_runtime.py",
        "core/prompt_assembler.py",
        "core/prompt_compiler.py",
        "core/legacy_prompt_runtime.py",
        "scripts/build_nanobot_prompt.py",
        "creatures/nanobot/prompt.md",
    ]
    for path in removed:
        assert not Path(path).exists(), f"{path} should be removed after P1-6"


def test_nanobot_config_no_longer_requires_legacy_prompt_file():
    config = Path("creatures/nanobot/config.yaml").read_text(encoding="utf-8")
    assert "system_prompt_file: prompt.md" not in config
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
python -B -m pytest tests/test_prompt_legacy_removal.py -q -p no:cacheprovider
```

预期：FAIL，旧文件仍存在且 config 仍声明 `prompt.md`。

- [ ] **步骤 3：移除 config 依赖**

从 `creatures/nanobot/config.yaml` 删除：

```yaml
system_prompt_file: prompt.md
```

确认 `NanobotBridge._load_legacy_prompt_into_config()` 已不再需要被调用；若仍被调用，在任务 4 或本任务中删除调用点。

- [ ] **步骤 4：删除旧模块和脚本**

删除：

```bash
git rm core/prompt_runtime.py core/prompt_assembler.py core/prompt_compiler.py core/legacy_prompt_runtime.py scripts/build_nanobot_prompt.py creatures/nanobot/prompt.md
```

删除或缩减测试：

- `tests/test_prompt_contract.py` 删除对 `build_nanobot_prompt.py` 和 `prompt.md` 的断言，保留到 V2 模板合同测试中。
- `tests/test_prompt_assembler.py` 删除或改为历史 fixture 文档测试。
- `tests/test_legacy_prompt_runtime.py` 删除。
- `tests/test_prompt_manager.py` 中只保留不依赖 live 的 PromptManager 单元测试，或随 `core.prompts` 下线策略删除。

- [ ] **步骤 5：删除旧模板目录生产依赖**

运行引用清点：

```bash
rg -n "prompts.default|prompts.legacy.default|data/prompt_fragments|data/runtime_prompt|core.prompt_runtime|PromptAssembler|legacy_prompt_runtime|build_nanobot_prompt|creatures/nanobot/prompt.md" core clients api nanobot_kt bootstrap tests docs README.md AGENTS.md
```

将仍属于 live import 的引用清零。文档历史引用保留，但当前态文档要标明已删除。

删除旧目录时显式指定：

```bash
git rm -r prompts.legacy.default
git rm -r prompts.default
git rm -r data/prompt_fragments data/runtime_prompt
```

如果 `data/prompts/` 仍包含运行时用户覆盖，先保留并在文档中标为迁移备份，不在本任务删除。

- [ ] **步骤 6：运行任务 6 回归**

运行：

```bash
python -B -m pytest tests/test_prompt_legacy_removal.py tests/test_prompt_manifest.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 7：提交任务 6**

运行：

```bash
git add creatures/nanobot/config.yaml tests/test_prompt_legacy_removal.py tests/test_prompt_contract.py tests/test_prompt_manager.py tests/test_prompt_manifest.py
git rm core/prompt_runtime.py core/prompt_assembler.py core/prompt_compiler.py core/legacy_prompt_runtime.py scripts/build_nanobot_prompt.py creatures/nanobot/prompt.md
git rm -r prompts.legacy.default prompts.default data/prompt_fragments data/runtime_prompt
git commit -m "refactor(提示词): 删除旧版提示词资产"
```

## 任务 7：建立无版本 canonical prompt 命名兼容层

**文件：**
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`api/admin/prompt_v2_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`webui/src/App.jsx`
- 修改：`webui/src/features/prompt/PromptPages.jsx`
- 修改：`prompt_manifest.json`
- 修改：`tests/test_prompt_manifest.py`
- 修改：`tests/test_prompt_v2_template_registry.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`
- 修改：`tests/test_reply_admin.py`

- [ ] **步骤 1：写红灯测试**

在 `tests/test_prompt_manifest.py` 中新增：

```python
def test_prompt_manifest_declares_single_canonical_engine_with_legacy_aliases():
    import json
    from pathlib import Path

    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["active_engine"] == "prompt"
    assert manifest["engines"]["prompt"]["status"] == "active"
    assert "v1" not in manifest["engines"]
    assert manifest["compat_aliases"]["v2"] == "prompt"
```

在 `tests/test_prompt_v2_template_registry.py` 中新增：

```python
def test_prompt_template_registry_prefers_canonical_env_names(tmp_path, monkeypatch):
    default_dir = tmp_path / "prompt_defaults"
    runtime_dir = tmp_path / "prompt_runtime"
    default_dir.mkdir()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import default_template_dir, runtime_template_dir

    assert default_template_dir() == default_dir
    assert runtime_template_dir() == runtime_dir
```

- [ ] **步骤 2：运行红灯**

运行：

```bash
python -B -m pytest tests/test_prompt_manifest.py::test_prompt_manifest_declares_single_canonical_engine_with_legacy_aliases tests/test_prompt_v2_template_registry.py::test_prompt_template_registry_prefers_canonical_env_names -q -p no:cacheprovider
```

预期：FAIL，manifest 仍声明 `v2/v1`，registry 仍优先 `NANOBOT_PROMPT_V2_*`。

- [ ] **步骤 3：修改 registry env 兼容层**

在 `core/prompt_v2/template_registry.py` 中让无版本 env 优先、旧 env 作为 fallback：

```python
def default_template_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_DEFAULT_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_DEFAULT_DIR")
        or (_repo_root() / "prompts.default")
    )


def runtime_template_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_RUNTIME_DIR")
        or os.environ.get("NANOBOT_PROMPT_V2_RUNTIME_DIR")
        or (_repo_root() / "data" / "prompts")
    )
```

旧 V1 `prompts.default/` 已在任务 6 删除后，才允许把 canonical 默认目录改回 `prompts.default`。

- [ ] **步骤 4：更新 manifest 和 API/UI 文案**

将 `prompt_manifest.json` 改为：

```json
{
  "version": 2,
  "active_engine": "prompt",
  "engines": {
    "prompt": {
      "status": "active",
      "default_dir": "prompts.default",
      "runtime_dir": "data/prompts",
      "backup_dir": "data/prompts_history"
    }
  },
  "compat_aliases": {
    "v2": "prompt"
  }
}
```

新增无版本 admin router alias，例如 `/api/v1/admin/prompt/templates`，旧 `/prompt-v2/templates` 保留为兼容路由并返回相同结果。

WebUI 新主路由使用 `/prompt-templates`，旧 `/prompt-v2-templates` redirect 到新路由。

- [ ] **步骤 5：保留行为语义字段兼容**

暂不重命名以下字段：

- `prompt_v2_audit_failed`
- `v2_code_retry`
- `prompt_mode` 历史字段

在代码注释或测试中明确它们是兼容字段，避免一次性破坏 API / trace / eval。

- [ ] **步骤 6：运行任务 7 回归**

运行：

```bash
python -B -m pytest tests/test_prompt_manifest.py tests/test_prompt_v2_template_registry.py tests/test_reply_admin.py tests/test_webui_prompt_runtime_ui.py -q -p no:cacheprovider
cd webui && npm run build
```

预期：PASS。

- [ ] **步骤 7：提交任务 7**

运行：

```bash
git add core/prompt_v2/template_registry.py api/admin/prompt_v2_routes.py api/admin_routes.py webui/src/App.jsx webui/src/features/prompt/PromptPages.jsx prompt_manifest.json tests/test_prompt_manifest.py tests/test_prompt_v2_template_registry.py tests/test_reply_admin.py tests/test_webui_prompt_runtime_ui.py
git commit -m "refactor(提示词): 建立无版本运行时命名"
```

## 任务 8：文档同步与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`AGENTS.md`
- 修改：`README.md`
- 修改：`docs/superpowers/specs/2026-06-17-prompt-v1-asset-removal-design.md`

- [ ] **步骤 1：同步当前态文档**

在 `docs/todo.md` 路线项 1 更新：

- 后台任务 prompt 已迁移到 V2 task template。
- 主回复 live 不再存在 V1 / legacy prompt 分支。
- 旧资产删除范围和保留兼容字段。
- 去版本化完成范围和仍保留的兼容 alias。

在 `docs/plan_walkthrough.md` 勾选 P1-6 任务，并记录每个阶段提交号和验证结果。

在 `AGENTS.md` / `README.md` 中删除“修改 prompt 逻辑必须检查 `creatures/nanobot/prompt.md`”这类当前态要求，改为检查 canonical prompt templates。

- [ ] **步骤 2：运行文档和引用守卫**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md AGENTS.md README.md docs/superpowers/specs/2026-06-17-prompt-v1-asset-removal-design.md
rg -n "core.prompt_runtime|PromptAssembler|legacy_prompt_runtime|build_nanobot_prompt|system_prompt_file: prompt.md" core clients api nanobot_kt bootstrap webui/src tests
```

预期：

- `git diff --check` 无输出。
- `rg` 在 live 源码中无命中；若测试 fixture 命中，测试名称必须显示历史兼容语义。

- [ ] **步骤 3：运行 P1-6 定向测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/test_model_router.py tests/test_evolution.py tests/test_bridge_prompt_v2.py tests/test_prompt_v2.py tests/test_prompt_v2_template_registry.py tests/test_prompt_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/test_reply_admin.py tests/test_prompt_trace_admin.py tests/test_prompt_legacy_admin_readonly.py tests/test_webui_prompt_runtime_ui.py -q -p no:cacheprovider
python -B -m pytest tests/test_api.py tests/test_streaming_bridge.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 4：运行 WebUI 构建**

运行：

```bash
cd webui
npm run build
```

预期：`✓ built`，无编译错误。

- [ ] **步骤 5：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [ ] **步骤 6：提交文档收尾**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md AGENTS.md README.md docs/superpowers/specs/2026-06-17-prompt-v1-asset-removal-design.md
git commit -m "docs(提示词): 同步旧版资产删除状态"
```

## 执行顺序

1. 任务 1：建立 V2 task template 渲染边界。
2. 任务 2：迁移分类器 prompt。
3. 任务 3：迁移记忆抽取 prompt。
4. 任务 4：封存主回复 V1 live 分支。
5. 任务 5：下线旧管理页和旧 admin 迁移路由。
6. 任务 6：删除 legacy prompt 资产与构建脚本。
7. 任务 7：建立无版本 canonical prompt 命名兼容层。
8. 任务 8：文档同步与最终验证。

每个任务完成后必须单独验证、单独提交。不要使用 `git add .` 或 `git add -A`。
