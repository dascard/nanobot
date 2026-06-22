# 普通 API Agent Step 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 `/render` 与 `/chat-step` HTTP 层拆到 `api/agent_step_routes.py`，保留旧导入兼容、普通 API 鉴权兼容和 SSE 传输语义。

**架构：** 新模块承载 `render_markdown()` 与 `chat_step()`，复用 `api.common_auth.verify_token` 和 `core.agent_step` 的协议逻辑。父模块在原 `/render`、`/chat-step` 所在位置 include 子 router，并 re-export 旧符号；`/chat`、`/group/message`、group timing、聊天落库和 Prompt Runtime 均不进入本阶段。

**技术栈：** FastAPI `APIRouter` / `StreamingResponse`、`core.agent_step`、pytest、FastAPI `TestClient`、现有普通 API split 测试模板。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-agent-step-routes-split-design.md`。
- 设计提交：`66a4b83 docs(普通API): 设计 Agent Step 路由拆分`。
- 设计勘误提交：`db83dfb docs(普通API): 修正 Agent Step 路由顺序设计`。
- `api/routes.py` 当前 1975 行。
- 本阶段迁移：
  - `render_markdown`
  - `chat_step`
  - `AgentStepRequest`
  - `agent_step_event_payload`
  - `run_agent_step`
  - `run_agent_step_stream`
  - `agent_step_sse_data`
- 本阶段保留在父模块：
  - `/chat`
  - `/group/message`
  - group timing 与 `update_group_name`
  - `StreamingResponse` import（父模块 `/chat` SSE 仍使用）
  - 私聊缓冲、guardrail、bridge、历史注入、落库和 push
  - Prompt Runtime 与 message envelope
  - `/health`

## 文件职责

- 创建：`tests/test_api_agent_step_routes_split.py`
  - 锁定拆分后的 endpoint module、旧导入兼容、普通 API token monkeypatch、route 顺序、SSE 触发条件和父模块边界。
- 创建：`api/agent_step_routes.py`
  - 承载 `/render` 与 `/chat-step` HTTP shell。
- 修改：`api/routes.py`
  - 删除本地 `/render` 与 `/chat-step` endpoint。
  - 删除不再直接使用的 `core.agent_step` import。
  - 从 `api.agent_step_routes` 导入并 re-export 迁移符号。
  - 在 `/chat` 前 include `agent_step_router`，保持 `/render` -> `/chat-step` -> `/chat` 顺序。
- 修改：`.Codex/plans/api-agent-step-routes-split.md`
  - 文档收口时勾选执行记录和验收清单。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 第八刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 阶段记录。

## 任务 1：补普通 API agent-step route split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_agent_step_routes_split.py`

- [ ] **步骤 1：创建测试文件**

创建 `tests/test_api_agent_step_routes_split.py`：

```python
from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient


_AGENT_STEP_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/render"),
    ("POST", "/api/v1/chat-step"),
)

_AGENT_STEP_ROUTE_EXPORTS = (
    "AgentStepRequest",
    "agent_step_event_payload",
    "run_agent_step",
    "run_agent_step_stream",
    "agent_step_sse_data",
    "render_markdown",
    "chat_step",
)


def _api_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _api_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _api_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def _route_index(path: str, method: str) -> int:
    for index, (route_path, route) in enumerate(_api_route_entries()):
        if route_path == path and method in getattr(route, "methods", set()):
            return index
    raise AssertionError(f"missing route: {method} {path}")


def _invalid_step_request(*, stream: bool = False) -> dict:
    return {
        "protocol": "bad-protocol",
        "run_id": "split-run",
        "input": {"user_message": "hello"},
        "tools": [],
        "tool_results": [],
        "stream": stream,
    }


def _sse_events(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block.removeprefix("data: ")))
    return events


def test_api_agent_step_routes_are_registered_from_split_module():
    for method, path in _AGENT_STEP_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.agent_step_routes"}


def test_legacy_api_routes_agent_step_imports_still_work():
    from api import agent_step_routes
    from api import routes

    for name in _AGENT_STEP_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(agent_step_routes, name)

    body = routes.AgentStepRequest(run_id="run-1")
    assert body.run_id == "run-1"
    assert body.protocol == "agent-step.v1"


def test_split_agent_step_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/chat-step",
            json=_invalid_step_request(),
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/chat-step",
            json=_invalid_step_request(),
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json()["status"] == "error"
    assert ok.json()["error"]["code"] == "invalid_protocol"
    assert wrong.status_code == 401


def test_api_agent_step_routes_are_not_registered_twice():
    for method, path in _AGENT_STEP_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_agent_step_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/agent_step_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_agent_step_routes_keep_order_before_chat():
    render_index = _route_index("/api/v1/render", "GET")
    chat_step_index = _route_index("/api/v1/chat-step", "POST")
    chat_index = _route_index("/api/v1/chat", "POST")

    assert render_index < chat_step_index < chat_index


def test_render_route_stays_public_and_deprecated(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/render?text=hello")

    assert response.status_code == 200
    assert response.json() == {"status": "deprecated"}


def test_chat_step_accept_header_triggers_sse_without_stream_flag(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        with test_client.stream(
            "POST",
            "/api/v1/chat-step",
            json=_invalid_step_request(stream=False),
            headers={
                "Authorization": "Bearer split-token",
                "Accept": "text/event-stream",
            },
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(body)
    assert events[0] == {"status": "progress", "text": "正在判断需要的业务工具..."}
    assert events[-1]["status"] == "error"
    assert events[-1]["error"]["code"] == "invalid_protocol"


def test_chat_step_stream_flag_triggers_sse_without_accept_header(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        with test_client.stream(
            "POST",
            "/api/v1/chat-step",
            json=_invalid_step_request(stream=True),
            headers={"Authorization": "Bearer split-token"},
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [event["status"] for event in _sse_events(body)] == ["progress", "error"]


def test_api_agent_step_async_boundaries_remain_coroutines():
    from api import agent_step_routes
    from api import routes

    assert inspect.iscoroutinefunction(agent_step_routes.chat_step)
    assert inspect.iscoroutinefunction(routes.chat_step)


def test_chat_and_group_boundaries_stay_in_parent_routes():
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes.group_message.__module__ == "api.routes"
    assert routes.group_timing_timer.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py
```

预期：FAIL。失败点应指向 `/render` 与 `/chat-step` endpoint 仍注册在 `api.routes`、
`api.agent_step_routes` 尚不存在，以及 `api/agent_step_routes.py` 文件尚不存在。

- [ ] **步骤 3：提交红灯测试**

运行：

```bash
git add tests/test_api_agent_step_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定 Agent Step 路由拆分契约"
```

## 任务 2：拆出 `api/agent_step_routes.py` 并提交

**文件：**

- 创建：`api/agent_step_routes.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：创建 `api/agent_step_routes.py`**

创建 `api/agent_step_routes.py`：

```python
"""普通 API Agent Step 与遗留渲染路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from api.common_auth import verify_token
from core.agent_step import (
    AgentStepRequest,
    agent_step_event_payload,
    run_agent_step,
    run_agent_step_stream,
    sse_data as agent_step_sse_data,
)

router = APIRouter(tags=["agent-step"])


@router.get("/render")
async def render_markdown(text: str):
    """遗留端点，已弃用。目前直接内嵌 base64 返回"""
    return {"status": "deprecated"}


@router.post("/chat-step", dependencies=[Depends(verify_token)])
async def chat_step(req: AgentStepRequest, accept: str = Header(default="")):
    """SynergyOpt 等外部编排方使用的 HTTP 半 ReAct step/resume 端点。"""
    wants_stream = req.stream or "text/event-stream" in str(accept or "").lower()

    if wants_stream:
        async def _event_stream():
            yield agent_step_sse_data({
                "status": "progress",
                "text": "正在判断需要的业务工具...",
            })
            async for event in run_agent_step_stream(req):
                yield agent_step_sse_data(event)

        return StreamingResponse(_event_stream(), media_type="text/event-stream")

    response = await run_agent_step(req)
    return agent_step_event_payload(response)
```

- [ ] **步骤 2：修改 `api/routes.py` imports 与 re-export**

删除父模块中不再直接使用的 import：

```python
from core.agent_step import (
    AgentStepRequest,
    agent_step_event_payload,
    run_agent_step,
    run_agent_step_stream,
    sse_data as agent_step_sse_data,
)
```

新增普通 API split import：

```python
from api.agent_step_routes import (
    AgentStepRequest,
    agent_step_event_payload,
    chat_step,
    render_markdown,
    router as agent_step_router,
    run_agent_step,
    run_agent_step_stream,
    agent_step_sse_data,
)
```

保留 `StreamingResponse`，因为父模块 `/chat` 流式响应仍使用。保留 `Header`，因为父模块
仍在 import 行中使用且后续可以由静态检查确认是否还需要。

- [ ] **步骤 3：删除父模块本地 `/render` 与 `/chat-step` 定义，并在原位置 include**

删除 `api/routes.py` 中本地：

- `render_markdown()`
- `chat_step()`

在原 `/render` 与 `/chat-step` 所在位置，也就是 `/chat` 定义前加入：

```python
router.include_router(agent_step_router)
```

不要把该 include 移到文件尾部；需要保持 `/render` -> `/chat-step` -> `/chat` 的注册顺序。

- [ ] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_agent_step_routes_split.py
```

预期：PASS。

- [ ] **步骤 5：运行 agent-step 行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_agent_step_api.py
```

预期：PASS。

- [ ] **步骤 6：运行普通 API split 相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_evolution_routes_split.py \
  tests/test_api_memory_routes_split.py \
  tests/test_api_model_routes_split.py \
  tests/test_api_task_routes_split.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

- [ ] **步骤 7：运行 `/chat` 流式相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py
```

预期：PASS。

- [ ] **步骤 8：运行静态检查**

运行：

```bash
python -B -m py_compile api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/agent_step_routes.py
git diff --check -- api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py
wc -l api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py
```

预期：

- `py_compile` 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数低于 1975。

- [ ] **步骤 9：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 10：提交 Agent Step 路由拆分**

运行：

```bash
git add api/routes.py api/agent_step_routes.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分 Agent Step 路由"
```

## 任务 3：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-agent-step-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新计划执行记录**

在本计划的「当前状态」中记录：

- 计划提交。
- 红灯测试提交。
- 实现提交。
- 红灯测试结果。
- split 绿灯结果。
- agent-step 行为回归结果。
- 普通 API split 相邻回归结果。
- `/chat` 流式相邻回归结果。
- 静态检查结果。
- `wc -l api/routes.py api/agent_step_routes.py tests/test_api_agent_step_routes_split.py` 行数。
- 全量回归结果。

- [ ] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 第八刀已拆出 `/render` 与 `/chat-step` HTTP 层。
- 新模块 `api/agent_step_routes.py`。
- 旧导入兼容、普通 API token monkeypatch、route order 和 SSE 边界。
- `api/routes.py` 最新行数。
- 下一候选为 group utility / legacy timing routes，或继续审计更低风险 route-only 边界。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-22 的 Agent Step route-only 拆分执行记录，包含：

- 选择 Agent Step 的原因。
- 设计、设计勘误、计划、红灯、实现和收口提交。
- 计划列表。
- 验证命令和结果。
- 下一步建议。

- [ ] **步骤 4：文档格式与状态检查**

运行：

```bash
rg -n "^- \\[ \\]" .Codex/plans/api-agent-step-routes-split.md
rg -n "T[O]DO|待[定]|后续实[现]|占[位]|\\x{FFFD}" .Codex/plans/api-agent-step-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-agent-step-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：第一个 `rg` 在收口后无命中；第二个 `rg` 无命中；`git diff --check` 无输出；
`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [ ] **步骤 5：运行最终定向回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_agent_step_routes_split.py \
  tests/test_agent_step_api.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-agent-step-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口 Agent Step 路由拆分"
```

## 最终验收清单

- [ ] `tests/test_api_agent_step_routes_split.py` 经历红灯再绿灯。
- [ ] `api/agent_step_routes.py` 已创建。
- [ ] `api.agent_step_routes` 不导入 `api.routes`。
- [ ] `api.agent_step_routes` 不包含 `asyncio.run` 或 `run_awaitable_sync`。
- [ ] `api.routes` re-export `AgentStepRequest`、agent-step 执行/序列化对象和 2 个 endpoint。
- [ ] `/render` 与 `/chat-step` endpoint 注册来源均为 `api.agent_step_routes`。
- [ ] `/render` 与 `/chat-step` 没有重复注册。
- [ ] `/render`、`/chat-step`、`/chat` 保持原注册顺序。
- [ ] `/render` 继续无 bearer 鉴权并返回 deprecated 响应。
- [ ] `/chat-step` 继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- [ ] `/chat-step` 继续支持 `Accept: text/event-stream` 和 body `stream=true` 两种 SSE 触发。
- [ ] `/chat-step` SSE 首事件和 framing 不变。
- [ ] `/chat` 与 `/group/message` 主链路未迁移。
- [ ] group timing 与 `update_group_name()` 未迁移。
- [ ] 现有 `tests/test_agent_step_api.py` 行为回归通过。
- [ ] `tests/test_asyncio_run_policy.py` 通过。
- [ ] 全量 `tests/` 回归 0 failures。
- [ ] 每个阶段性改动都有独立 commit。
