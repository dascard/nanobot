# 普通 API Chat 非流式结果收尾拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes.proxy_chat()` 中非流式 Bridge 成功返回后的结果收尾拆到 `api/chat_non_streaming_result.py`，父模块继续保留 Bridge 调用、HTTP 异常控制流、SSE 和后台 evolution 调度。

**架构：** 新模块通过 context + callbacks 接收父模块 patch point，只处理 answer、reply meta、Prompt V2 audit failure、private buffer finalize、落库、transport answer 和 response payload。父模块在 `_do_chat()` 成功后调用 helper，根据结构化结果抛 HTTP 500、添加 evolution 后台任务或返回 payload。

**技术栈：** Python 3.12、asyncio、dataclass、FastAPI `HTTPException`（仅父模块）、pytest、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-non-streaming-result-split-design.md`
- [x] 设计提交：`b04c996 docs(普通API): 设计非流式结果收尾拆分`
- [x] 计划写入日期：2026-06-24

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：`get_bridge()`、`_do_chat()` 和 `chat_runtime_facade.call_bridge_non_streaming()` 在父模块。
- 保留：KT error path、`asyncio.CancelledError` path、HTTP 502 脱敏逻辑在父模块。
- 保留：`HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")` 在父模块。
- 保留：`background_tasks.add_task(evolution_task, req.user_id)` 在父模块。
- 保留：`_persist_chat_turn()`、`_chat_response_payload()`、`_expand_chat_transport_answer()`、`_finalize_private_buffer()`、`_pop_bridge_reply_meta()` 和 `_private_prompt_audit_failure_meta()` 作为父模块 facade。
- 保留：`_stream_chat()`、`StreamingResponse`、`chat_sse_loop` 和 `chat_streaming_result` 原边界。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块导入 FastAPI、`StreamingResponse`、`BackgroundTasks`、`core.daily_digest` 或 `push_envelope_to_qq`。
- 禁止：新模块调用 `get_bridge()`、`get_guardrail()`、`bridge.handle_message()` 或 `chat_runtime_facade.call_bridge_non_streaming()`。
- 禁止：新模块创建 DB session、`UnitOfWork` 或调用 `db.commit()`。
- 禁止：改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_non_streaming_result.py`
  - 定义 `ChatNonStreamingResultCallbacks`。
  - 定义 `ChatNonStreamingResultContext`。
  - 定义 `ChatNonStreamingResult`。
  - 实现 `finalize_non_streaming_chat_result()`。
  - 只通过 callbacks 访问父模块 facade。
- 修改：`api/routes.py`
  - 导入 `chat_non_streaming_result`。
  - 在非流式 `_do_chat()` 成功后构造 callbacks 和 context。
  - 用 helper outcome 替换原内联收尾代码。
  - 保留 KT error path、HTTP 500 抛出和 evolution 后台任务。
- 创建：`tests/test_api_chat_non_streaming_result_split.py`
  - 锁定新模块源码边界。
  - 单测正常成功路径、Prompt V2 audit failure、transport expand 失败降级、父模块接入。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_non_streaming_result.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-non-streaming-result-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 最终收口时记录 P3 `api/routes.py` 非流式结果收尾拆分进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时追加 2026-06-24 本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_non_streaming_result_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-non-streaming-result-split.md`

- [x] **步骤 1：创建非流式结果 split 测试文件**

创建 `tests/test_api_chat_non_streaming_result_split.py`，写入：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeDb:
    pass


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request(user_id: str = "u-non-stream", session_id: str = "private_u-non-stream") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        query="非流式请求",
        files=[],
        sender_name="",
        session_name=None,
        message_id="",
        source_message_ids=[],
        client_meta={"platform": "qq"},
    )
```

- [x] **步骤 2：新增 callback fake**

在同一文件追加：

```python
def _callbacks(
    calls: dict[str, list[Any]],
    *,
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    expand_raises: bool = False,
):
    from api.chat_non_streaming_result import ChatNonStreamingResultCallbacks

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
        return pending

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        if expand_raises:
            raise RuntimeError("expand failed")
        return f"expanded:{answer}"

    def chat_response_payload(req, **kwargs):
        calls.setdefault("payload", []).append((req, kwargs))
        return {
            "status": kwargs["status"],
            "answer": kwargs.get("answer", ""),
            "reply": kwargs.get("answer", ""),
            "messages": [{"type": "text", "text": kwargs.get("answer", "")}],
            "reply_meta": kwargs.get("reply_meta"),
            "unprocessed_logs": kwargs.get("unprocessed_logs"),
            "guardrail_status": kwargs.get("guardrail_status"),
        }

    return ChatNonStreamingResultCallbacks(
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        expand_chat_transport_answer=expand_chat_transport_answer,
        chat_response_payload=chat_response_payload,
    )
```

- [x] **步骤 3：新增 context helper**

在同一文件追加：

```python
def _context(
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    answer: str = "最终答案",
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    evolution_threshold: int = 5,
    expand_raises: bool = False,
):
    from api.chat_non_streaming_result import ChatNonStreamingResultContext

    req = req or _request()
    return ChatNonStreamingResultContext(
        req=req,
        persist_req=req,
        bridge=object(),
        answer=answer,
        platform="qq",
        bridge_meta={"chat_type": "private", "is_group": False},
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        empty_assistant_placeholder="（无回复内容）",
        evolution_threshold=evolution_threshold,
        callbacks=_callbacks(
            calls,
            reply_meta=reply_meta,
            pending=pending,
            expand_raises=expand_raises,
        ),
    )
```

- [x] **步骤 4：新增源码约束红灯**

在同一文件追加：

```python
def test_chat_non_streaming_result_module_does_not_import_parent_routes_or_runtime_side_effects():
    source = _source("api/chat_non_streaming_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "StreamingResponse" not in source
    assert "APIRouter" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "bridge.handle_message" not in source
    assert "call_bridge_non_streaming" not in source
    assert "SessionLocal" not in source
    assert "UnitOfWork" not in source
    assert "db.commit(" not in source
    assert "push_envelope_to_qq" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 5：新增正常成功路径红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_finalize_non_streaming_success_persists_raw_answer_and_returns_transport_payload():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    req = _request(user_id="u-success")
    context = _context(calls, req=req, pending=7, evolution_threshold=5)
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["pop_meta"] == [(context.bridge, "private_u-success")]
    assert calls["expand"] == ["最终答案"]
    assert calls["finalize"] == [("u-success", "最终答案", True)]
    assert calls["persist"] == [
        (
            db,
            req,
            "最终答案",
            "safe",
            {
                "timing_meta": {"private_decision": "ok"},
            },
        )
    ]
    assert calls["payload"][0][1]["answer"] == "expanded:最终答案"
    assert calls["payload"][0][1]["reply_meta"] == {}
    assert calls["payload"][0][1]["unprocessed_logs"] == 7
    assert result.payload is not None
    assert result.payload["status"] == "ok"
    assert result.payload["answer"] == "expanded:最终答案"
    assert result.pending == 7
    assert result.should_trigger_evolution is True
    assert result.prompt_audit_failed is False
```

- [x] **步骤 6：新增 Prompt V2 audit failure 红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_finalize_non_streaming_prompt_audit_failure_persists_placeholder_and_skips_payload():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    context = _context(
        calls,
        req=_request(user_id="u-audit"),
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        pending=9,
        evolution_threshold=5,
    )
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["audit_meta"] == [()]
    assert calls["finalize"] == [("u-audit", "（无回复内容）", True)]
    persisted = calls["persist"][0]
    assert persisted[0] is db
    assert persisted[2] == "（无回复内容）"
    assert persisted[3] == "safe"
    assert persisted[4] == {
        "assistant_meta": {
            "kind": "empty_reply",
            "no_context": True,
            "agent_result": "prompt_v2_audit_failed",
        },
        "assistant_processed": 1,
        "timing_meta": {"private_decision": "ok"},
    }
    assert calls.get("expand") is None
    assert calls.get("payload") is None
    assert result.payload is None
    assert result.pending is None
    assert result.should_trigger_evolution is False
    assert result.prompt_audit_failed is True
```

- [x] **步骤 7：新增 transport expand 失败降级红灯**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_finalize_non_streaming_keeps_raw_answer_when_transport_expand_fails():
    from api.chat_non_streaming_result import finalize_non_streaming_chat_result

    calls: dict[str, list[Any]] = {}
    context = _context(calls, answer="图片 [generated:image]", expand_raises=True)
    db = FakeDb()

    result = await finalize_non_streaming_chat_result(db, context)

    assert calls["expand"] == ["图片 [generated:image]"]
    assert calls["persist"][0][2] == "图片 [generated:image]"
    assert calls["payload"][0][1]["answer"] == "图片 [generated:image]"
    assert result.payload is not None
    assert result.payload["answer"] == "图片 [generated:image]"
```

- [x] **步骤 8：新增父模块接入红灯**

在同一文件追加：

```python
def test_parent_non_streaming_chat_delegates_result_finalize_and_keeps_http_boundaries():
    source = _source("api/routes.py")

    assert "chat_non_streaming_result" in source
    assert "chat_non_streaming_result.finalize_non_streaming_chat_result" in source
    assert "HTTPException(status_code=500, detail=\"系统暂时不可用，请稍后再试\")" in source
    assert "background_tasks.add_task(evolution_task, req.user_id)" in source
    assert "async def _do_chat()" in source
    assert "chat_runtime_facade.call_bridge_non_streaming" in source
    assert "except asyncio.CancelledError" in source
    assert "SAFE_STREAM_ERROR_MESSAGE" in source
```

- [x] **步骤 9：更新 split module 扫描清单**

在以下文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 路径列表中追加：

```python
"api/chat_non_streaming_result.py",
```

需要修改的文件：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 10：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_non_streaming_result_split.py \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

结果：FAIL，`9 failed, 1 warning in 6.80s`。失败原因符合预期：`api/chat_non_streaming_result.py` 不存在、`api.chat_non_streaming_result` 无法导入、`api.routes` 尚未接入 `chat_non_streaming_result`。

- [x] **步骤 11：提交红灯测试契约**

运行：

```bash
git add \
tests/test_api_chat_non_streaming_result_split.py \
tests/test_api_group_message_routes_split.py \
tests/test_api_agent_step_routes_split.py \
tests/test_api_history_log_routes_split.py \
tests/test_api_sticker_media_routes_split.py \
.Codex/plans/api-chat-non-streaming-result-split.md
git commit -m "test(普通API): 锁定非流式结果收尾契约"
```

---

## 任务 2：新增非流式结果 helper

**文件：**
- 创建：`api/chat_non_streaming_result.py`
- 修改：`.Codex/plans/api-chat-non-streaming-result-split.md`

- [ ] **步骤 1：创建 helper 模块**

创建 `api/chat_non_streaming_result.py`，写入：

```python
"""聊天非流式结果收尾 helper。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatNonStreamingResultCallbacks:
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    chat_response_payload: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ChatNonStreamingResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    answer: str
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    evolution_threshold: int
    callbacks: ChatNonStreamingResultCallbacks


@dataclass(frozen=True)
class ChatNonStreamingResult:
    payload: dict[str, Any] | None
    pending: int | None = None
    should_trigger_evolution: bool = False
    prompt_audit_failed: bool = False
```

- [ ] **步骤 2：实现内部 request attr 和 audit 判断**

在同一文件追加：

```python
def _request_attr(req: Any, name: str, default: Any = "") -> Any:
    return getattr(req, name, default)


def _is_prompt_audit_failed(reply_meta: dict[str, Any] | None) -> bool:
    return (reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed"
```

- [ ] **步骤 3：实现 `finalize_non_streaming_chat_result()`**

在同一文件追加：

```python
async def finalize_non_streaming_chat_result(
    db: Any,
    context: ChatNonStreamingResultContext,
) -> ChatNonStreamingResult:
    callbacks = context.callbacks
    req = context.req
    private_reply_meta = callbacks.pop_bridge_reply_meta(
        context.bridge,
        str(_request_attr(req, "session_id")),
    )

    if _is_prompt_audit_failed(private_reply_meta):
        logger.error(
            "[/chat] Prompt V2 audit failed: user=%s session=%s",
            _request_attr(req, "user_id"),
            _request_attr(req, "session_id"),
        )
        await callbacks.finalize_private_buffer(
            str(_request_attr(req, "user_id")),
            context.empty_assistant_placeholder,
        )
        callbacks.persist_chat_turn(
            db,
            context.persist_req,
            context.empty_assistant_placeholder,
            context.guardrail_status,
            assistant_meta=callbacks.private_prompt_audit_failure_meta(),
            assistant_processed=1,
            timing_meta=context.private_timing_meta,
        )
        return ChatNonStreamingResult(payload=None, prompt_audit_failed=True)

    answer = context.answer
    logger.info(
        "[/chat] Bridge returned: answer_len=%s, answer_stripped_empty=%s",
        len(answer),
        not answer.strip(),
    )
    if answer:
        logger.debug("[/chat] Answer preview: %s", answer[:300])
    else:
        logger.warning("[/chat] EMPTY ANSWER returned from bridge!")

    transport_answer = answer
    try:
        transport_answer = callbacks.expand_chat_transport_answer(answer)
    except Exception:
        logger.warning("[/chat] generated image ref expansion failed", exc_info=True)

    await callbacks.finalize_private_buffer(str(_request_attr(req, "user_id")), answer)
    pending = callbacks.persist_chat_turn(
        db,
        context.persist_req,
        answer,
        context.guardrail_status,
        timing_meta=context.private_timing_meta,
    )

    payload = callbacks.chat_response_payload(
        req,
        status="ok",
        answer=transport_answer,
        reply_meta=private_reply_meta,
        platform=context.platform,
        chat_type=str(context.bridge_meta.get("chat_type") or ""),
        unprocessed_logs=pending,
        guardrail_status=context.guardrail_status,
        include_answer_chunks=True,
    )
    return ChatNonStreamingResult(
        payload=payload,
        pending=pending,
        should_trigger_evolution=pending >= context.evolution_threshold,
    )
```

- [ ] **步骤 4：运行 helper 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_non_streaming_result_split.py \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

预期：只有父模块接入测试仍 FAIL；helper 行为测试和 split module 扫描通过。

- [ ] **步骤 5：提交 helper 模块**

运行：

```bash
git add api/chat_non_streaming_result.py .Codex/plans/api-chat-non-streaming-result-split.md
git commit -m "refactor(普通API): 增加非流式结果收尾助手"
```

---

## 任务 3：接入父模块

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-non-streaming-result-split.md`

- [ ] **步骤 1：导入新 helper**

在 `api/routes.py` 顶部已有 chat helper import 区增加：

```python
from api import chat_non_streaming_result
```

- [ ] **步骤 2：替换非流式成功收尾代码**

把 `_do_chat()` 成功后从：

```python
private_reply_meta = _pop_bridge_reply_meta(bridge, req.session_id)
if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
    ...
return _chat_response_payload(
    req,
    status="ok",
    answer=transport_answer,
    reply_meta=private_reply_meta,
    platform=platform,
    chat_type=str(bridge_meta.get("chat_type") or ""),
    unprocessed_logs=pending,
    guardrail_status=guardrail_status,
    include_answer_chunks=True,
)
```

替换为：

```python
non_streaming_callbacks = chat_non_streaming_result.ChatNonStreamingResultCallbacks(
    pop_bridge_reply_meta=_pop_bridge_reply_meta,
    private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
    finalize_private_buffer=_finalize_private_buffer,
    persist_chat_turn=_persist_chat_turn,
    expand_chat_transport_answer=_expand_chat_transport_answer,
    chat_response_payload=_chat_response_payload,
)
non_streaming_context = chat_non_streaming_result.ChatNonStreamingResultContext(
    req=req,
    persist_req=persist_req,
    bridge=bridge,
    answer=answer,
    platform=platform,
    bridge_meta=bridge_meta,
    guardrail_status=guardrail_status,
    private_timing_meta=private_timing_meta,
    empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
    evolution_threshold=EVOLUTION_THRESHOLD,
    callbacks=non_streaming_callbacks,
)
non_streaming_result = await chat_non_streaming_result.finalize_non_streaming_chat_result(
    db,
    non_streaming_context,
)
if non_streaming_result.prompt_audit_failed:
    raise HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")
if non_streaming_result.should_trigger_evolution:
    logger.info(
        "[/chat] Evolution triggered: user=%s, pending=%s, threshold=%s",
        req.user_id,
        non_streaming_result.pending,
        EVOLUTION_THRESHOLD,
    )
    background_tasks.add_task(evolution_task, req.user_id)
return non_streaming_result.payload
```

注意：不要移动 `try: answer = await _do_chat()`、`except asyncio.CancelledError` 和 `except Exception`。

- [ ] **步骤 3：运行父模块接入绿灯**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/test_api_chat_non_streaming_result_split.py -v
```

预期：`tests/test_api_chat_non_streaming_result_split.py` 全部 PASS。

- [ ] **步骤 4：运行相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_streaming_result_split.py \
tests/test_api_chat_push_envelope_split.py \
tests/test_api_chat_persistence_split.py \
tests/test_chat_response_envelope.py \
tests/test_streaming_response_envelope.py \
tests/test_api.py::test_proxy_chat \
tests/test_api.py::test_proxy_chat_kt_error_does_not_echo_internal_detail \
tests/test_api.py::test_private_prompt_v2_audit_failure_is_not_context_chat \
tests/test_asyncio_run_policy.py \
-v
```

预期：全部 PASS。

- [ ] **步骤 5：运行静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_non_streaming_result.py -q
git diff --check -- api/routes.py api/chat_non_streaming_result.py tests/test_api_chat_non_streaming_result_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py
wc -l api/routes.py api/chat_non_streaming_result.py tests/test_api_chat_non_streaming_result_split.py
```

预期：`compileall` 和 `git diff --check` 退出码为 0，`api/routes.py` 行数下降。

- [ ] **步骤 6：提交父模块接入**

运行：

```bash
git add api/routes.py .Codex/plans/api-chat-non-streaming-result-split.md
git commit -m "refactor(普通API): 接入非流式结果收尾助手"
```

---

## 任务 4：最终验证与文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-non-streaming-result-split.md`

- [ ] **步骤 1：运行全量验证**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 2：核对行数和 async 策略**

运行：

```bash
wc -l api/routes.py api/chat_non_streaming_result.py
rg -n "asyncio\\.run|run_awaitable_sync" api core clients nanobot_kt tests --glob '*.py'
```

预期：`api/routes.py` 行数下降；本阶段新增代码不包含 `asyncio.run` 或 `run_awaitable_sync`。已有合法测试 helper 或历史命中若存在，只记录不扩大范围。

- [ ] **步骤 3：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」的 `api/routes.py` 进展列表追加一条事实记录，必须包含：

- `api/routes.py` 第二十五刀拆出非流式 Bridge 成功结果收尾到 `api/chat_non_streaming_result.py`。
- 父模块继续保留 Bridge 调用、KT error path、HTTPException、evolution 后台任务和 SSE 路径。
- 新模块只通过 callbacks 处理 reply meta、Prompt V2 audit failure、private buffer finalize、原始 answer 落库、transport answer 展开和响应 payload。
- `api/routes.py` 的拆分前行数 `1121` 和拆分后实际行数。
- 红灯、helper / 接入绿灯、相邻回归和全量回归的实际输出摘要。

- [ ] **步骤 4：更新 `docs/plan_walkthrough.md`**

在顶部当前推进焦点后追加 2026-06-24 状态段，必须写入：

- 设计、计划、测试契约、helper、父模块接入和文档收口的实际提交号与提交标题。
- 新模块 `api/chat_non_streaming_result.py` 的职责。
- 父模块保留 Bridge 调用、KT error path、HTTPException、evolution 后台任务和 SSE 路径的边界。
- 红灯、定向绿灯、相邻回归、静态检查和全量回归的实际输出摘要。
- `api/routes.py` 当前实际行数。
- 下一刀建议评估私聊 pre-bridge 决策编排或 persona lookup 小刀。

- [ ] **步骤 5：更新本计划状态**

把所有已完成步骤改为 `[x]`，并在「执行记录」小节追加每个阶段的实际提交号、提交标题和最终全量 pytest 输出摘要。不要留下尖括号占位符。

- [ ] **步骤 6：运行文档检查**

运行：

```bash
git diff --check -- .Codex/plans/api-chat-non-streaming-result-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：`git diff --check` 退出码为 0；同时人工核对文档中已写入实际提交号、行数和验证摘要，没有未替换占位符。

- [ ] **步骤 7：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-non-streaming-result-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口非流式结果收尾拆分"
```

## 执行记录

- 设计提交：`b04c996 docs(普通API): 设计非流式结果收尾拆分`
