# 聊天安全门面拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api.routes._detect_guardrail()` 中的新旧 guardrail provider 兼容归一化逻辑拆到 `api/chat_guardrail_facade.py`，保留父模块 wrapper、`get_guardrail()` patch point 和 `/chat` 行为契约。

**架构：** 新模块只提供纯同步 helper，接收调用方传入的 guardrail 实例，不导入 `api.routes`，不获取 provider，也不触碰私聊缓冲、SSE、落库或 runtime payload。`api.routes` 继续负责 provider 获取、`asyncio.to_thread()` 调度、superuser 判断、异常清理和 response envelope，只把检测结果归一化与状态映射委托给门面模块。

**技术栈：** Python 3.11+、FastAPI、pytest、现有 `api.routes` 拆分测试约束。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-22-api-chat-guardrail-facade-split-design.md`
- [x] 设计提交：`db8c2fd docs(普通API): 设计聊天安全门面拆分`

## 边界与禁止事项

本计划只拆 guardrail 检测兼容逻辑，不扩大到其他 `/chat` 子系统。

- 保留：`api.routes.get_guardrail()` 作为测试和运行时 patch point。
- 保留：`api.routes._detect_guardrail.__module__ == "api.routes"`。
- 保留：`api.routes._build_guardrail_input()` wrapper 和 `api/chat_content_helpers.py` 现有职责。
- 保留：私聊缓冲状态、owner/follower 等待、`asyncio.to_thread()` 创建时机、SSE finalizer、落库、push 和 response envelope。
- 禁止：在新模块中导入 `api.routes`。
- 禁止：在新模块中调用 `get_guardrail()` 或 `get_bridge()`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：修改 Prompt Runtime 模板、`enriched_query`、metadata 结构或 streaming done 权威语义。

## 文件职责

- 创建：`tests/test_api_chat_guardrail_facade_split.py`
  - 锁定新模块不导入父模块、不使用同步 awaitable 包装。
  - 锁定 `detect_guardrail()` 对新 provider 与 legacy provider 的兼容行为。
  - 锁定 `guardrail_status_from_result()` 的状态值域。
  - 锁定父模块 `_detect_guardrail()` 仍来自 `api.routes`。
- 创建：`api/chat_guardrail_facade.py`
  - 提供 `detect_guardrail(guardrail, message, *, allow_passthrough=False) -> dict[str, Any]`。
  - 提供 `guardrail_status_from_result(result) -> str`，只返回 `injection`、`silent`、`safe`。
- 修改：`api/routes.py`
  - 导入 `chat_guardrail_facade`。
  - 将 `_detect_guardrail()` 改为薄 wrapper。
  - 将 `/chat` 内联 guardrail status 映射改为调用 `guardrail_status_from_result()`。
- 修改：`tests/test_api_history_log_routes_split.py`
  - 将 `api/chat_guardrail_facade.py` 加入拆分模块扫描清单。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 将 `api/chat_guardrail_facade.py` 加入拆分模块扫描清单。
- 修改：`tests/test_api_group_message_routes_split.py`
  - 将 `api/chat_guardrail_facade.py` 加入拆分模块扫描清单。
- 修改：`tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_guardrail_facade.py` 加入拆分模块扫描清单。
- 修改：`.Codex/plans/api-chat-guardrail-facade-split.md`
  - 随执行推进勾选步骤并记录验证结果。
- 修改：`docs/todo.md`
  - 收口记录 P3 中 `api/routes.py` guardrail 小刀拆分进度和剩余行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加 2026-06-23 的执行记录、提交列表和验证证据。

---

### 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_guardrail_facade_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：编写新门面契约测试**

在 `tests/test_api_chat_guardrail_facade_split.py` 写入：

```python
from __future__ import annotations

from pathlib import Path

from api import routes


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_guardrail_facade_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_guardrail_facade.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source


def test_parent_guardrail_wrapper_keeps_api_routes_module():
    assert routes._detect_guardrail.__module__ == "api.routes"


def test_detect_guardrail_prefers_detect_injection():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def __init__(self):
            self.detect_calls = []
            self.classify_calls = []

        def detect_injection(self, message, *, allow_passthrough=False):
            self.detect_calls.append((message, allow_passthrough))
            return {"status": "safe", "custom": "kept"}

        def classify(self, message, allow_injection_passthrough=False):
            self.classify_calls.append((message, allow_injection_passthrough))
            return {"status": "injection"}

    guardrail = Guardrail()
    result = detect_guardrail(guardrail, "hello", allow_passthrough=True)

    assert result == {"status": "safe", "custom": "kept"}
    assert guardrail.detect_calls == [("hello", True)]
    assert guardrail.classify_calls == []


def test_detect_guardrail_legacy_reply_maps_to_safe_and_keeps_passthrough():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def __init__(self):
            self.calls = []

        def classify(self, message, allow_injection_passthrough=False):
            self.calls.append((message, allow_injection_passthrough))
            return {"status": "reply", "complexity": 5}

    guardrail = Guardrail()
    result = detect_guardrail(guardrail, "hello", allow_passthrough=True)

    assert guardrail.calls == [("hello", True)]
    assert result == {
        "status": "safe",
        "complexity": 5,
        "injection": False,
        "passthrough": True,
    }


def test_detect_guardrail_legacy_silent_maps_to_silent():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "silent", "complexity": 0}

    result = detect_guardrail(Guardrail(), "hello", allow_passthrough=True)

    assert result == {
        "status": "silent",
        "complexity": 0,
        "injection": False,
        "passthrough": True,
    }


def test_detect_guardrail_legacy_injection_maps_to_injection():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "injection", "complexity": 0}

    result = detect_guardrail(Guardrail(), "hello", allow_passthrough=True)

    assert result == {
        "status": "injection",
        "complexity": 0,
        "injection": True,
        "passthrough": False,
    }


def test_detect_guardrail_legacy_non_dict_falls_back_to_safe():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return "bad"

    result = detect_guardrail(Guardrail(), "hello")

    assert result == {
        "status": "safe",
        "injection": False,
        "passthrough": False,
    }


def test_guardrail_status_from_result_maps_known_and_unknown_values():
    from api.chat_guardrail_facade import guardrail_status_from_result

    assert guardrail_status_from_result({"status": "injection"}) == "injection"
    assert guardrail_status_from_result({"status": "silent"}) == "silent"
    assert guardrail_status_from_result({"status": "safe"}) == "safe"
    assert guardrail_status_from_result({"status": "reply"}) == "safe"
    assert guardrail_status_from_result({}) == "safe"
    assert guardrail_status_from_result(None) == "safe"


def test_parent_guardrail_wrapper_matches_new_module():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "reply", "complexity": 3}

    parent_guardrail = Guardrail()
    module_guardrail = Guardrail()

    assert routes._detect_guardrail(
        parent_guardrail,
        "hello",
        allow_passthrough=True,
    ) == detect_guardrail(
        module_guardrail,
        "hello",
        allow_passthrough=True,
    )
```

- [x] **步骤 2：将新模块加入扫描测试清单**

在以下 4 个文件的 `module_path` 清单中追加 `"api/chat_guardrail_facade.py"`：

```python
"api/chat_guardrail_facade.py",
```

文件列表：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 3：运行新门面测试验证红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v
```

预期：失败，原因是 `api/chat_guardrail_facade.py` 尚不存在，至少出现 `FileNotFoundError` 或 `ModuleNotFoundError`。

- [x] **步骤 4：运行扫描测试验证红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：4 个测试失败，原因是扫描清单包含的新模块文件尚不存在。

- [ ] **步骤 5：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_guardrail_facade_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定聊天安全门面契约"
```

---

### 任务 2：新增 guardrail 门面模块

**文件：**
- 创建：`api/chat_guardrail_facade.py`

- [ ] **步骤 1：编写最小门面实现**

在 `api/chat_guardrail_facade.py` 写入：

```python
"""聊天 guardrail 兼容门面。"""

from __future__ import annotations

from typing import Any


def detect_guardrail(
    guardrail: Any,
    message: str,
    *,
    allow_passthrough: bool = False,
) -> dict[str, Any]:
    if hasattr(guardrail, "detect_injection"):
        result = guardrail.detect_injection(
            message,
            allow_passthrough=allow_passthrough,
        )
        return result if isinstance(result, dict) else {}

    result = guardrail.classify(
        message,
        allow_injection_passthrough=allow_passthrough,
    )
    if not isinstance(result, dict):
        result = {}

    status = str(result.get("status") or "").strip()
    if status == "silent":
        return {
            **result,
            "status": "silent",
            "injection": False,
            "passthrough": bool(allow_passthrough),
        }

    injection = status == "injection"
    return {
        **result,
        "status": "injection" if injection else "safe",
        "injection": injection,
        "passthrough": bool(allow_passthrough and not injection),
    }


def guardrail_status_from_result(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "safe"
    status = str(result.get("status") or "").strip()
    if status == "injection":
        return "injection"
    if status == "silent":
        return "silent"
    return "safe"
```

- [ ] **步骤 2：运行门面测试验证绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v
```

预期：当前测试全部通过。

- [ ] **步骤 3：运行扫描测试验证绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：4 个扫描测试全部通过。

- [ ] **步骤 4：提交新模块**

运行：

```bash
git add api/chat_guardrail_facade.py
git diff --cached --check
git commit -m "refactor(普通API): 增加聊天安全门面"
```

---

### 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`

- [ ] **步骤 1：导入新门面模块**

在 `api/routes.py` 的 `from api import (...)` 列表中加入：

```python
chat_guardrail_facade,
```

- [ ] **步骤 2：保留父模块 wrapper 并委托新模块**

将 `api.routes._detect_guardrail()` 改为：

```python
def _detect_guardrail(guardrail, message: str, *, allow_passthrough: bool = False) -> dict:
    return chat_guardrail_facade.detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )
```

- [ ] **步骤 3：替换 `/chat` 内联状态映射**

将 `/chat` 中根据 `result["status"]` 手写设置 `guardrail_status` 的分支替换为：

```python
guardrail_status = chat_guardrail_facade.guardrail_status_from_result(result)
```

- [ ] **步骤 4：运行门面测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v
```

预期：全部通过。

- [ ] **步骤 5：运行 guardrail 与私聊缓冲邻近回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api.py::test_superuser_bypasses_injection_guardrail \
  tests/test_api.py::test_superuser_image_only_message_bypasses_injection_guardrail \
  tests/test_api.py::test_image_only_message_uses_multimodal_prompt_placeholder \
  tests/test_api.py::test_private_buffer_silent_releases_waiters \
  tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
  tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request \
  tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds \
  tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer \
  tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer \
  -v
```

预期：全部通过。

- [ ] **步骤 6：运行 asyncio 禁用策略回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py -v
```

预期：全部通过，且未发现除 main guard 外的新增 `asyncio.run`。

- [ ] **步骤 7：提交父模块接入**

运行：

```bash
git add api/routes.py
git diff --cached --check
git commit -m "refactor(普通API): 接入聊天安全门面"
```

---

### 任务 4：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-chat-guardrail-facade-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：记录计划执行结果**

在本计划的「验证记录」中写入每个阶段的命令、结果和提交哈希：

```markdown
## 验证记录

- 红灯测试：
  - `python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v`
  - 结果：按预期失败，原因是 `api/chat_guardrail_facade.py` 尚不存在。
- 新模块绿灯：
  - `python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v`
  - 结果：全部通过。
- 父模块接入回归：
  - `python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py -v`
  - 结果：全部通过。
```

- [ ] **步骤 2：记录行数变化**

运行：

```bash
wc -l api/routes.py api/chat_guardrail_facade.py tests/test_api_chat_guardrail_facade_split.py
```

将输出写入本计划、`docs/todo.md` 和 `docs/plan_walkthrough.md`，用于追踪 P3 超大文件拆分进度。

- [ ] **步骤 3：更新待办和 walkthrough**

在 `docs/todo.md` 中记录 `api/routes.py` 已完成 guardrail 小刀拆分，并保留 P3 对剩余 `/chat` 体积的后续拆分方向。在 `docs/plan_walkthrough.md` 中追加 `2026-06-23` 记录，包含设计、计划、红灯、新模块、父模块接入和全量验证提交。

- [ ] **步骤 4：运行文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-guardrail-facade-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-guardrail-facade-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：计划缺陷扫描无输出，diff 检查无输出。

- [ ] **步骤 5：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，跳过数量和警告数量记录到 `docs/plan_walkthrough.md`。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-guardrail-facade-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天安全门面拆分"
```

---

## 验证记录

- 红灯测试：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_guardrail_facade_split.py -v`
  - 结果：8 failed、1 passed、1 warning；失败原因是 `api/chat_guardrail_facade.py` 不存在，符合预期红灯。
- 扫描红灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：4 failed、1 warning；失败原因是扫描清单中的 `api/chat_guardrail_facade.py` 不存在，符合预期红灯。
- 计划文档自检：
  - 命令：`rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-guardrail-facade-split.md`
  - 结果：无输出，命令退出码为 1，表示未命中计划缺陷模式。
  - 命令：`git diff --check -- .Codex/plans/api-chat-guardrail-facade-split.md`
  - 结果：无输出，命令退出码为 0。
