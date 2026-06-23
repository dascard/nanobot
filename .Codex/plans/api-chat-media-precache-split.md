# 普通 API Chat Media Precache 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes._schedule_image_precache()` 的调度实现拆到 `api/chat_media_precache.py`，保留父模块 wrapper 和现有 `/chat` 行为。

**架构：** 新模块只负责图片预缓存后台任务调度，接收可注入的 `normalize_files` 与 `precache_image_sources` 依赖；父模块 `_schedule_image_precache()` 保持旧名称和 patch point，只委托新模块。`proxy_chat()`、Bridge、私聊缓冲、落库、SSE、push envelope 和 response envelope 均不迁移。

**技术栈：** Python 3.12、FastAPI `BackgroundTasks`、pytest、`rg` 静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-media-precache-split-design.md`
- [x] 设计提交：`b2a0660 docs(普通API): 设计聊天图片预缓存拆分`

## 边界与禁止事项

- 保留：`api.routes._schedule_image_precache.__module__ == "api.routes"`。
- 保留：`proxy_chat()` 继续调用父模块 `_schedule_image_precache()`。
- 保留：父模块 wrapper 传入 `_normalize_files`，让旧 patch point 仍能影响实际行为。
- 禁止：迁移 `/chat` 路由本体。
- 禁止：迁移 `StreamingResponse`、stream finalizer、Bridge 调用、guardrail、私聊缓冲、聊天落库、push envelope 或 response envelope。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块顶层导入 `nanobot_kt.image_pipeline`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：修改默认 Prompt Runtime 模板；本阶段不改变 prompt 输入或工具输出契约。

## 文件职责

- 创建：`api/chat_media_precache.py`
  - 承载 `schedule_image_precache()` 的唯一实现。
  - 默认复用 `api.chat_content_helpers.normalize_files`。
  - 生产路径在函数内部懒加载 `nanobot_kt.image_pipeline.precache_image_sources`。
- 修改：`api/routes.py`
  - 导入 `chat_media_precache`。
  - 将 `_schedule_image_precache()` 改为薄 wrapper。
- 创建：`tests/test_api_chat_media_precache_split.py`
  - 锁定新模块不导入父模块。
  - 锁定父模块 wrapper 契约和委托参数。
  - 覆盖 no-op 与 add_task 行为。
- 修改：`.Codex/plans/api-chat-media-precache-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 记录 P3 第二十一刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_media_precache_split.py`

- [x] **步骤 1：创建 split 测试文件**

创建 `tests/test_api_chat_media_precache_split.py`：

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_chat_media_precache_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_media_precache.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "from nanobot_kt.image_pipeline import precache_image_sources" not in source.splitlines()[:20]
```

- [x] **步骤 2：补父模块 wrapper 委托测试**

在同一文件追加：

```python
def test_parent_media_precache_wrapper_remains_in_routes_and_delegates(monkeypatch):
    from api import routes

    calls = []

    def fake_schedule(background_tasks, files, **kwargs):
        calls.append((background_tasks, files, kwargs))

    monkeypatch.setattr("api.chat_media_precache.schedule_image_precache", fake_schedule)

    background_tasks = object()
    routes._schedule_image_precache(
        background_tasks,
        [" img://a "],
        source_type="chat_request",
        source_name_prefix="session_message",
    )

    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert len(calls) == 1
    assert calls[0][0] is background_tasks
    assert calls[0][1] == [" img://a "]
    assert calls[0][2]["source_type"] == "chat_request"
    assert calls[0][2]["source_name_prefix"] == "session_message"
    assert calls[0][2]["normalize_files"] is routes._normalize_files
```

- [x] **步骤 3：补新模块行为测试**

在同一文件追加：

```python
def test_schedule_image_precache_noops_without_files_or_background_tasks():
    from api.chat_media_precache import schedule_image_precache

    class FakeBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    tasks = FakeBackgroundTasks()
    schedule_image_precache(
        tasks,
        ["", "  "],
        source_type="chat_request",
        source_name_prefix="empty",
        normalize_files=lambda files: [],
        precache_image_sources=lambda *args, **kwargs: None,
    )
    schedule_image_precache(
        None,
        ["img://a"],
        source_type="chat_request",
        source_name_prefix="none",
        normalize_files=lambda files: ["img://a"],
        precache_image_sources=lambda *args, **kwargs: None,
    )

    assert tasks.calls == []


def test_schedule_image_precache_adds_precache_task_with_normalized_files():
    from api.chat_media_precache import schedule_image_precache

    class FakeBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, func, *args, **kwargs):
            self.calls.append((func, args, kwargs))

    def fake_precache(*args, **kwargs):
        return None

    tasks = FakeBackgroundTasks()

    schedule_image_precache(
        tasks,
        [" raw "],
        source_type="chat_request",
        source_name_prefix="session_message",
        normalize_files=lambda files: ["img://a", "img://b"],
        precache_image_sources=fake_precache,
    )

    assert tasks.calls == [
        (
            fake_precache,
            (["img://a", "img://b"],),
            {
                "source_type": "chat_request",
                "source_name_prefix": "session_message",
            },
        )
    ]
```

- [x] **步骤 4：运行红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_media_precache_split.py -v
```

预期：

- 失败。
- 失败原因为 `api/chat_media_precache.py` 不存在，或父模块尚未委托新模块。

- [x] **步骤 5：提交红灯测试**

```bash
git add tests/test_api_chat_media_precache_split.py .Codex/plans/api-chat-media-precache-split.md
git commit -m "test(普通API): 锁定聊天图片预缓存契约"
```

---

## 任务 2：新增新模块实现

**文件：**
- 创建：`api/chat_media_precache.py`

- [x] **步骤 1：创建新模块**

创建 `api/chat_media_precache.py`：

```python
"""聊天图片预缓存调度 helper。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api import chat_content_helpers


def schedule_image_precache(
    background_tasks: Any,
    files: Any,
    *,
    source_type: str,
    source_name_prefix: str,
    normalize_files: Callable[[Any], list[str]] = chat_content_helpers.normalize_files,
    precache_image_sources: Callable[..., Any] | None = None,
) -> None:
    normalized_files = normalize_files(files)
    if not normalized_files or background_tasks is None:
        return

    if precache_image_sources is None:
        from nanobot_kt.image_pipeline import precache_image_sources as precache_image_sources_func
    else:
        precache_image_sources_func = precache_image_sources

    background_tasks.add_task(
        precache_image_sources_func,
        normalized_files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
    )
```

- [x] **步骤 2：运行新模块阶段测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_media_precache_split.py -v
```

预期：

- 新模块源码扫描和行为测试通过。
- 父模块 wrapper 委托测试仍失败，因为 `api.routes` 尚未导入并委托新模块。

- [x] **步骤 3：提交新模块**

```bash
git add api/chat_media_precache.py .Codex/plans/api-chat-media-precache-split.md
git commit -m "refactor(普通API): 增加聊天图片预缓存助手"
```

---

## 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`

- [ ] **步骤 1：导入新模块**

在 `from api import (...)` 中加入：

```python
    chat_media_precache,
```

- [ ] **步骤 2：替换 `_schedule_image_precache()` 实现**

把原函数体替换为：

```python
def _schedule_image_precache(
    background_tasks: BackgroundTasks | None,
    files: Optional[List[str]],
    *,
    source_type: str,
    source_name_prefix: str,
) -> None:
    return chat_media_precache.schedule_image_precache(
        background_tasks,
        files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
        normalize_files=_normalize_files,
    )
```

保留函数名、参数和 `proxy_chat()` 调用点。

- [ ] **步骤 3：运行定向绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_media_precache_split.py -v
```

预期：

- 全部通过。

- [ ] **步骤 4：运行相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_helpers_split.py::test_legacy_parent_chat_helper_wrappers_keep_api_routes_module \
  tests/test_api_chat_runtime_facade_split.py::test_chat_runtime_facade_uses_api_routes_get_bridge_patch_point \
  tests/test_api_chat_runtime_facade_split.py::test_chat_runtime_facade_split_keeps_proxy_chat_in_parent_routes \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 5：静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_media_precache.py -q
wc -l api/routes.py api/chat_media_precache.py tests/test_api_chat_media_precache_split.py
git diff --check -- api/routes.py api/chat_media_precache.py tests/test_api_chat_media_precache_split.py .Codex/plans/api-chat-media-precache-split.md
```

预期：

- `compileall` 退出码 0。
- `api/routes.py` 行数下降。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 6：提交父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-media-precache-split.md
git commit -m "refactor(普通API): 接入聊天图片预缓存助手"
```

---

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-media-precache-split.md`

- [ ] **步骤 1：更新计划执行记录**

在本计划底部追加执行记录，至少包含：

- 红灯输出摘要。
- 新模块阶段输出摘要。
- 父模块接入定向 / 相邻回归输出摘要。
- 行数检查。
- 全量测试结果。
- 提交列表。

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」中追加第二十一刀进展，记录：

- 新模块路径。
- 父模块 wrapper 保留。
- `/chat`、Bridge、私聊缓冲、落库、SSE、push envelope 和 response envelope 均未迁移。
- 新模块没有反向导入父模块，也没有同步包装 awaitable。
- `api/routes.py` 的真实行数变化和验证结果。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-23 普通 API Chat Media Precache 拆分` 小节，包含：

- 状态。
- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表完成状态。
- 验证记录。
- 执行约束和下一步建议。

- [ ] **步骤 4：文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-media-precache-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-media-precache-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：

- `rg` 无输出，退出码 1。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 5：最终全量验证**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：

- 0 failures。
- 记录 passed / skipped / warnings 和耗时。

- [ ] **步骤 6：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-media-precache-split.md
git commit -m "docs(计划): 收口聊天图片预缓存拆分"
```

---

## 执行记录

- 2026-06-23 任务 1 红灯测试：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_media_precache_split.py -v`
  退出码 1，`4 failed, 1 warning`。失败点为 `api/chat_media_precache.py`
  不存在、`api.chat_media_precache` 无法导入和
  `ModuleNotFoundError: No module named 'api.chat_media_precache'`，符合红灯预期。
- 2026-06-23 任务 1 提交：
  随本次 `test(普通API): 锁定聊天图片预缓存契约` 提交。
- 2026-06-23 任务 2 新模块阶段验证：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_media_precache_split.py -v`
  退出码 1，`1 failed, 3 passed, 1 warning`。新模块源码扫描、no-op
  和 `add_task()` 行为测试通过；唯一失败为
  `test_parent_media_precache_wrapper_remains_in_routes_and_delegates`，
  父模块 `_schedule_image_precache()` 仍执行旧实现，符合新模块已实现但父模块尚未接入的阶段预期。
- 2026-06-23 任务 2 提交：
  随本次 `refactor(普通API): 增加聊天图片预缓存助手` 提交。
