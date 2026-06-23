# 普通 API Chat Streaming Result 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes._stream_chat()` 内的 stream runner 结果收尾逻辑拆到 `api/chat_streaming_result.py`，保留 `/chat` route、本体 SSE 主循环和父模块 monkeypatch facade。

**架构：** 新模块只接管 runner 完成后的落库、private buffer finalize、Prompt V2 audit no-send 和断连 push。父模块继续创建 runner task、stream queue、SSE event loop、done envelope 和 `StreamingResponse`，并通过 context + callbacks 把可 patch facade 注入新模块。

**技术栈：** Python 3.12、asyncio task / queue、FastAPI `StreamingResponse`、SQLAlchemy session / `UnitOfWork`、pytest、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-streaming-result-split-design.md`
- [x] 设计提交：`7b003e2 docs(普通API): 设计流式结果收尾拆分`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：`StreamingResponse(_stream_chat(), media_type="text/event-stream")` 在父模块。
- 保留：`CHAT_STREAM_QUEUE_MAXSIZE` 在父模块读取并影响 `stream_queue`。
- 保留：SSE heartbeat、queue wait、delta coalescing、error event、done envelope 和 evolution trigger 在父模块。
- 保留：`api.routes._persist_chat_turn`、`_finalize_private_buffer`、`_pop_bridge_reply_meta`、`_private_prompt_audit_failure_meta`、`_expand_chat_transport_answer`、`_build_chat_push_envelope` 作为父模块 facade。
- 保留：request DB 与后台 `UnitOfWork` 新 DB 的区别。
- 保留：Prompt V2 audit 失败不 push、assistant meta 使用 no-context failure meta。
- 禁止：迁移完整 `_stream_chat()`。
- 禁止：迁移 `proxy_chat()` 或 `/chat` route。
- 禁止：改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 禁止：改 `ChatProxyRequest.stream`、Bridge `stream` metadata 或 KT `Message.stream`。
- 禁止：新模块导入 `api.routes`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_streaming_result.py`
  - 定义 `ChatStreamResultCallbacks`。
  - 定义 `ChatStreamResultContext`。
  - 实现 `persist_stream_result_after_runner_done()`。
  - 只通过 callbacks 调用父模块 facade 和 push 函数。
- 修改：`api/routes.py`
  - 导入 `chat_streaming_result`。
  - 在 `_stream_chat()` 内构造 callbacks 和 context。
  - 将旧 `_persist_stream_result_after_runner_done()` 局部函数改为委托新模块。
- 创建：`tests/test_api_chat_streaming_result_split.py`
  - 锁定新模块源码约束。
  - 单测成功持久化、Prompt V2 audit no-send、后台 push / `UnitOfWork` / drain。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_streaming_result.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-streaming-result-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 记录 P3 第二十三刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_streaming_result_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-streaming-result-split.md`

- [ ] **步骤 1：创建 streaming result split 测试文件**

创建 `tests/test_api_chat_streaming_result_split.py`，写入：

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@dataclass
class FakePushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


class FakeDb:
    pass


def _request(user_id: str = "u-stream", session_id: str = "private_u-stream") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        query="流式请求",
        files=[],
        sender_name="",
        session_name=None,
        message_id="",
        source_message_ids=[],
        client_meta={"platform": "qq"},
    )


async def _runner_sets(result_holder: dict[str, Any], key: str, value: Any) -> None:
    result_holder[key] = value


def _callbacks(
    calls: dict[str, list[Any]],
    *,
    reply_meta: dict[str, Any] | None = None,
    push_ok: bool = True,
):
    from api.chat_streaming_result import ChatStreamResultCallbacks

    async def drain_stream_queue_until_task_done(stream_queue, runner_task):
        calls.setdefault("drain", []).append((stream_queue, runner_task))

    def pop_bridge_reply_meta(bridge, session_id: str):
        calls.setdefault("pop_meta", []).append((bridge, session_id))
        return reply_meta or {}

    def private_prompt_audit_failure_meta():
        calls.setdefault("audit_meta", []).append(())
        return {"kind": "empty_reply", "no_context": True, "agent_result": "prompt_v2_audit_failed"}

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True):
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    def persist_chat_turn(db, req, answer, guardrail_status=None, **kwargs):
        calls.setdefault("persist", []).append((db, req, answer, guardrail_status, kwargs))
        return 3

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        return f"expanded:{answer}"

    def build_chat_push_envelope(req, **kwargs):
        calls.setdefault("envelope", []).append((req, kwargs))
        return FakePushEnvelope(
            target_type="private",
            target_id=req.user_id,
            envelope={"reply": kwargs["answer"], "meta": {"chat_type": kwargs["chat_type"]}},
        )

    async def push_envelope_to_qq(target_type: str, target_id: str, envelope: dict[str, Any]) -> bool:
        calls.setdefault("push", []).append((target_type, target_id, envelope))
        return push_ok

    return ChatStreamResultCallbacks(
        drain_stream_queue_until_task_done=drain_stream_queue_until_task_done,
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        expand_chat_transport_answer=expand_chat_transport_answer,
        build_chat_push_envelope=build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
    )


def _context(
    result_holder: dict[str, Any],
    runner_task: asyncio.Task[Any],
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    reply_meta: dict[str, Any] | None = None,
):
    from api.chat_streaming_result import ChatStreamResultContext

    req = req or _request()
    return ChatStreamResultContext(
        req=req,
        persist_req=req,
        bridge=object(),
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=asyncio.Queue(maxsize=1),
        platform="qq",
        bridge_meta={"chat_type": "private", "is_group": False},
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        empty_assistant_placeholder="（无回复内容）",
        callbacks=_callbacks(calls, reply_meta=reply_meta),
    )
```

- [ ] **步骤 2：新增源码约束红灯**

在 `tests/test_api_chat_streaming_result_split.py` 中追加：

```python
def test_chat_streaming_result_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_streaming_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "from core.daily_digest import push_envelope_to_qq" not in source
    assert "import core.daily_digest" not in source
```

- [ ] **步骤 3：新增成功持久化红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_persist_stream_result_success_uses_result_holder_and_request_db():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "最终答案"))
    req = _request(user_id="u-success")
    context = _context(result_holder, runner_task, calls, req=req)
    db = FakeDb()

    await persist_stream_result_after_runner_done(context, push=False, persist_db=db)

    assert calls["finalize"] == [("u-success", "最终答案", True)]
    assert calls["persist"] == [
        (
            db,
            req,
            "最终答案",
            "safe",
            {
                "assistant_meta": None,
                "assistant_processed": None,
                "timing_meta": {"private_decision": "ok"},
            },
        )
    ]
    assert calls.get("push") is None
```

- [ ] **步骤 4：新增 Prompt V2 audit no-send 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_persist_stream_result_prompt_audit_failure_uses_meta_and_skips_push():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", ""))
    context = _context(
        result_holder,
        runner_task,
        calls,
        req=_request(user_id="u-audit"),
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
    )
    db = FakeDb()

    await persist_stream_result_after_runner_done(context, push=True, persist_db=db)

    assert calls["audit_meta"] == [()]
    assert calls["finalize"] == [("u-audit", "（无回复内容）", True)]
    persisted = calls["persist"][0]
    assert persisted[0] is db
    assert persisted[2] == "（无回复内容）"
    assert persisted[4]["assistant_meta"] == {
        "kind": "empty_reply",
        "no_context": True,
        "agent_result": "prompt_v2_audit_failed",
    }
    assert persisted[4]["assistant_processed"] == 1
    assert calls.get("push") is None
```

- [ ] **步骤 5：新增后台 push / UnitOfWork / drain 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_persist_stream_result_background_push_uses_unit_of_work_and_drain(monkeypatch):
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "后台答案"))
    context = _context(result_holder, runner_task, calls, req=_request(user_id="u-bg"))
    uow_db = FakeDb()

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = uow_db
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)

    await persist_stream_result_after_runner_done(
        context,
        push=True,
        persist_db=None,
        drain_stream=True,
    )

    assert calls["drain"] == [(context.stream_queue, runner_task)]
    assert calls["persist"][0][0] is uow_db
    assert calls["expand"] == ["后台答案"]
    assert calls["envelope"][0][1]["answer"] == "expanded:后台答案"
    assert calls["push"] == [
        ("private", "u-bg", {"reply": "expanded:后台答案", "meta": {"chat_type": "private"}})
    ]
```

- [ ] **步骤 6：更新 split module 扫描清单**

在以下文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 路径列表中追加：

```python
"api/chat_streaming_result.py",
```

需要修改的文件：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [ ] **步骤 7：运行红灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：

- 失败。
- 失败点为 `api/chat_streaming_result.py` 不存在，或 `api.chat_streaming_result` 无法导入。

- [ ] **步骤 8：提交红灯测试**

```bash
git add tests/test_api_chat_streaming_result_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-streaming-result-split.md
git commit -m "test(普通API): 锁定流式结果收尾契约"
```

---

## 任务 2：新增 streaming result 模块

**文件：**
- 创建：`api/chat_streaming_result.py`
- 修改：`.Codex/plans/api-chat-streaming-result-split.md`

- [ ] **步骤 1：创建新模块**

创建 `api/chat_streaming_result.py`：

```python
"""聊天流式结果收尾 helper。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatStreamResultCallbacks:
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class ChatStreamResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    result_holder: MutableMapping[str, Any]
    runner_task: asyncio.Task[Any]
    stream_queue: asyncio.Queue[Any]
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    callbacks: ChatStreamResultCallbacks


def _request_attr(req: Any, name: str, default: Any = "") -> Any:
    return getattr(req, name, default)


def _is_prompt_audit_failed(reply_meta: dict[str, Any] | None) -> bool:
    return (reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed"


async def persist_stream_result_after_runner_done(
    context: ChatStreamResultContext,
    *,
    push: bool,
    persist_db: Any | None = None,
    drain_stream: bool = False,
) -> None:
    callbacks = context.callbacks
    drain_task = (
        asyncio.create_task(
            callbacks.drain_stream_queue_until_task_done(
                context.stream_queue,
                context.runner_task,
            )
        )
        if drain_stream
        else None
    )
    try:
        await context.runner_task
        if drain_task is not None:
            await drain_task

        final_answer = context.empty_assistant_placeholder
        assistant_meta = None
        assistant_processed = None
        should_push = False

        if "error" in context.result_holder:
            err_msg = str(context.result_holder.get("error") or "unknown")
            logger.error(
                "[/chat] Stream-aborted runner failed: user=%s, session=%s, error=%s",
                _request_attr(context.req, "user_id"),
                _request_attr(context.req, "session_id"),
                err_msg,
            )
        else:
            private_reply_meta = callbacks.pop_bridge_reply_meta(
                context.bridge,
                str(_request_attr(context.req, "session_id")),
            )
            if _is_prompt_audit_failed(private_reply_meta):
                assistant_meta = callbacks.private_prompt_audit_failure_meta()
                assistant_processed = 1
            else:
                answer = str(context.result_holder.get("answer") or "")
                if answer.strip():
                    final_answer = answer
                    should_push = push

        await callbacks.finalize_private_buffer(
            str(_request_attr(context.req, "user_id")),
            final_answer,
        )

        def _write(db_for_write: Any) -> None:
            callbacks.persist_chat_turn(
                db_for_write,
                context.persist_req,
                final_answer,
                context.guardrail_status,
                assistant_meta=assistant_meta,
                assistant_processed=assistant_processed,
                timing_meta=context.private_timing_meta,
            )

        if persist_db is not None:
            _write(persist_db)
        else:
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                if uow.db is None:
                    raise RuntimeError("UnitOfWork session is not open")
                _write(uow.db)

        if should_push:
            push_answer = final_answer
            try:
                push_answer = callbacks.expand_chat_transport_answer(final_answer)
            except Exception:
                pass

            push_payload = callbacks.build_chat_push_envelope(
                context.req,
                answer=push_answer,
                platform=context.platform,
                chat_type=str(context.bridge_meta.get("chat_type") or ""),
                is_group=bool(context.bridge_meta.get("is_group")),
            )
            ok = await callbacks.push_envelope_to_qq(
                push_payload.target_type,
                push_payload.target_id,
                push_payload.envelope,
            )
            if ok:
                logger.info(
                    "[/chat] Stream-aborted result pushed: user=%s, len=%s",
                    _request_attr(context.req, "user_id"),
                    len(final_answer),
                )
            else:
                logger.error(
                    "[/chat] Stream-aborted result push failed: user=%s, session=%s, len=%s",
                    _request_attr(context.req, "user_id"),
                    _request_attr(context.req, "session_id"),
                    len(final_answer),
                )
    except Exception as exc:
        logger.error("[/chat] Background finish failed: %s", exc)
    finally:
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)
```

- [ ] **步骤 2：运行新模块定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_streaming_result_split.py -v
```

预期：

- 新模块单测通过。
- split 扫描修改后的跨文件测试也应在下一步单独运行。

- [ ] **步骤 3：运行 split 扫描绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 4：静态检查**

运行：

```bash
python -m compileall api/chat_streaming_result.py -q
git diff --check -- api/chat_streaming_result.py tests/test_api_chat_streaming_result_split.py \
  tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-streaming-result-split.md
```

预期：

- `compileall` 退出码 0。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 5：提交新模块**

```bash
git add api/chat_streaming_result.py .Codex/plans/api-chat-streaming-result-split.md
git commit -m "refactor(普通API): 增加流式结果收尾助手"
```

---

## 任务 3：接入父模块 `_stream_chat()` 收尾器

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-streaming-result-split.md`

- [ ] **步骤 1：导入新模块**

在 `api/routes.py` 的 `from api import (...)` 导入列表中加入：

```python
chat_streaming_result,
```

- [ ] **步骤 2：构造 callbacks 与 context**

在 `_stream_chat()` 创建 `runner_task`、`heartbeat_interval` 和 `coalescer` 后，增加：

```python
        from core.daily_digest import push_envelope_to_qq

        stream_result_callbacks = chat_streaming_result.ChatStreamResultCallbacks(
            drain_stream_queue_until_task_done=chat_streaming_helpers.drain_stream_queue_until_task_done,
            pop_bridge_reply_meta=_pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
            finalize_private_buffer=_finalize_private_buffer,
            persist_chat_turn=_persist_chat_turn,
            expand_chat_transport_answer=_expand_chat_transport_answer,
            build_chat_push_envelope=_build_chat_push_envelope,
            push_envelope_to_qq=push_envelope_to_qq,
        )
        stream_result_context = chat_streaming_result.ChatStreamResultContext(
            req=req,
            persist_req=persist_req,
            bridge=bridge,
            result_holder=result_holder,
            runner_task=runner_task,
            stream_queue=stream_queue,
            platform=platform,
            bridge_meta=bridge_meta,
            guardrail_status=guardrail_status,
            private_timing_meta=private_timing_meta,
            empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
            callbacks=stream_result_callbacks,
        )
```

说明：

- late import `push_envelope_to_qq` 保持与旧逻辑相同的导入时机。
- `_persist_chat_turn` 和其他父模块 facade 在请求执行时注入，继续兼容 monkeypatch。

- [ ] **步骤 3：替换局部收尾函数**

将 `_persist_stream_result_after_runner_done()` 的主体替换为委托：

```python
        async def _persist_stream_result_after_runner_done(
            *,
            push: bool,
            persist_db: Session | None = None,
            drain_stream: bool = False,
        ) -> None:
            await chat_streaming_result.persist_stream_result_after_runner_done(
                stream_result_context,
                push=push,
                persist_db=persist_db,
                drain_stream=drain_stream,
            )
```

删除旧局部函数中的落库、`UnitOfWork`、push envelope、异常吞吐和 drain task cleanup 代码。

- [ ] **步骤 4：运行 streaming result 定向绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 5：运行 streaming 相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_api_chat_push_envelope_split.py \
  tests/test_chat_response_envelope.py \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 6：静态检查和行数记录**

运行：

```bash
python -m compileall api/routes.py api/chat_streaming_result.py -q
wc -l api/routes.py api/chat_streaming_result.py tests/test_api_chat_streaming_result_split.py
git diff --check -- api/routes.py api/chat_streaming_result.py tests/test_api_chat_streaming_result_split.py \
  tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-streaming-result-split.md
```

预期：

- `compileall` 退出码 0。
- `api/routes.py` 行数下降。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 7：提交父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-streaming-result-split.md
git commit -m "refactor(普通API): 接入流式结果收尾助手"
```

---

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-streaming-result-split.md`

- [ ] **步骤 1：运行最终全量验证**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：

- 0 failures。
- 记录 passed / skipped / warnings 和耗时。

- [ ] **步骤 2：更新计划执行记录**

在本计划底部追加执行记录，至少包含：

- 红灯输出摘要。
- 新模块定向输出摘要。
- 父模块接入定向 / streaming 相邻回归输出摘要。
- 行数检查。
- 全量测试结果。
- 提交列表。

- [ ] **步骤 3：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」中追加第二十三刀进展，记录：

- 新模块路径。
- 父模块保留 `/chat` route、完整 `_stream_chat()` 主循环和 `StreamingResponse`。
- request DB / 后台 `UnitOfWork` 行为保持。
- Prompt V2 audit no-send、断连 push、bounded queue drain 行为保持。
- 新模块没有反向导入父模块，也没有同步包装 awaitable。
- `api/routes.py` 的真实行数变化和验证结果。

- [ ] **步骤 4：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-23 普通 API Chat Streaming Result 拆分` 小节，包含：

- 状态。
- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表完成状态。
- 验证记录。
- 执行约束和下一步建议。

- [ ] **步骤 5：文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-streaming-result-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-streaming-result-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：

- `rg` 无输出，退出码 1。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 6：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-streaming-result-split.md
git commit -m "docs(计划): 收口流式结果收尾拆分"
```

---

## 执行记录

- 2026-06-23 设计阶段：
  写入 `docs/superpowers/specs/2026-06-23-api-chat-streaming-result-split-design.md`，
  并随 `7b003e2 docs(普通API): 设计流式结果收尾拆分` 提交。
