# P2-2 响应信封实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 `/chat`、`/chat` SSE done、`/group/message` 和 QQ push 共享兼容响应信封，同时保留所有旧字段。

**架构：** 先在 `core/message_envelope.py` 冻结纯函数接口，再让 API、群聊、push 三个写入面按文件所有权独立落地。`api/routes.py` 是共享集成点，只允许一个 owner 修改；群聊和 push worker 不直接改它，避免并发写冲突。P2-2 只做兼容双写，不重写 CQ renderer、出站 segments 或 HTML-to-pic。

**技术栈：** Python 3.12、FastAPI、Starlette SSE、SQLAlchemy、pytest、in-memory SQLite。

---

## 当前事实

- 设计文档：`docs/superpowers/specs/2026-06-18-message-envelope-design.md`，提交 `c984036 docs(消息): 设计响应信封标准`。
- 当前计划文件路径按项目约定使用 `.Codex/plans/message-envelope.md`。
- `docs/todo.md` 路线项 5 的 P2-2 响应信封兼容双写已完成；共享 builder、`/chat`、`/group/message`、push 适配、route push 集成和响应侧文档均已落地，client_meta 边界层解析 / 校验作为后续小任务保留。
- `/chat` 非流式成功响应已兼容返回 `reply`、`messages`、`reply_meta` 和 `meta`，并保留 `status`、`user_id`、`answer`、`answer_chunks` 和 `unprocessed_logs`。
- `/chat` SSE done 已兼容返回 `reply`、`messages`、`reply_meta` 和 `meta`，并保留 `status="done"` 与 `answer`；SSE framing 仍是 `data: {...}`，不要改成 `event: done`。
- `/chat` 已把过滤后的 `private_reply_meta` 写入非流式成功响应和 SSE done 响应。
- `/group/message` route 只返回 `GroupIngressService.handle(req)` 的 dict；群聊响应包装已放在 `app/group_ingress/service.py`，route 层无需改动。
- `push_to_qq(target_type, target_id, message) -> bool` 的旧签名必须保持。
- `push_envelope_to_qq(target_type, target_id, envelope) -> bool` 已在 `core/daily_digest.py` 中作为旧 QQbot push 适配层落地；定时任务推送已先构造标准信封，再通过适配层派生旧 `message` 字段。
- `api/routes.py` 中手动定时任务运行和流式断连后台 push 已改用 `push_envelope_to_qq()`；流式断连路径仍在构造信封前执行 `expand_generated_image_refs_in_content(..., allow_base64=False)`。
- `docs/message-field-standard.md` 已新增响应信封章节，记录兼容双写字段、`messages` 首版结构、旧字段保留规则和 P2-2 / P2-3 边界。
- 现有无关脏文件包括 pycache、`docs/goal.md`、`tests/conftest.py`、`.codex/` 历史计划、`docs/TODO_LIST.md` 等。执行本计划时不要回滚、删除或暂存这些文件。

## 并行执行策略

本计划支持两种执行方式：

- **单工作区稳妥模式：** 主线程按任务顺序执行，每完成一个任务验证并 commit。适合当前工作区有较多无关脏文件的情况。
- **隔离 worktree 并行模式：** 先提交任务 1 的共享接口，再为互不干扰的 worker 创建独立 worktree / 分支；每个 worker 只修改自己的文件集合，主线程审查 diff 后集成。不要让多个 worker 同时直接写同一工作区。

文件所有权：

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| 接口 owner | `core/message_envelope.py`、`tests/test_message_envelope.py` | `api/routes.py`、`app/group_ingress/service.py`、`core/daily_digest.py` |
| API owner | `api/routes.py`、`tests/test_chat_response_envelope.py`、`tests/test_streaming_response_envelope.py` | `app/group_ingress/service.py`、`core/daily_digest.py` |
| 群聊 owner | `app/group_ingress/service.py`、`tests/test_group_response_envelope.py` | `api/routes.py`、`core/daily_digest.py` |
| push owner | `core/daily_digest.py`、`tests/test_push_envelope.py` | `api/routes.py`、群聊文件 |
| 集成 owner | `api/routes.py` 中 push call site、跨 worker 回归 | 群聊和 daily digest 已提交代码的逻辑重写 |
| 文档 owner | `docs/message-field-standard.md`、`docs/todo.md`、`docs/plan_walkthrough.md`、本计划 | 生产代码 |

并行边界：

- 任务 1 必须先完成并提交，因为后续 worker 都依赖 `core.message_envelope` 的函数签名。
- 任务 2、任务 3、任务 4 可在任务 1 之后并行执行，但只能在隔离 worktree 中并行写代码。
- 任务 5 必须等任务 2 和任务 4 完成后执行，因为它同时依赖 `push_envelope_to_qq()` 和 `api/routes.py` 的当前形态。
- 任务 6 必须等所有代码任务提交并完成验证后执行。

子 agent 提示词约束：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件。
先写红灯测试并运行指定命令，确认失败原因与计划一致。
再写最小实现，运行定向测试和任务指定回归。
完成后提交本任务文件，commit message 使用中文 Conventional Commit。
返回：红灯输出摘要、绿灯输出摘要、提交号、改动文件列表、仍需主线程集成的点。
```

如果 worker 报告 `NEEDS_CONTEXT`，主线程只补充该 worker 范围内的上下文；如果 worker 需要修改禁止文件，停止该 worker，并把需求移到集成 owner。

## 共享接口契约

任务 1 提交后，后续任务只能使用以下接口，不直接复制响应拼装逻辑：

```python
def build_text_messages(reply: str) -> list[dict[str, str]]: ...

def sanitize_reply_meta(reply_meta: Mapping[str, Any] | None) -> dict[str, Any]: ...

def build_chat_response_envelope(
    *,
    status: str,
    answer: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]: ...

def build_group_response_envelope(
    *,
    action: str,
    reply: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    generation: int | None = None,
    reason: str = "",
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    duplicate_reply: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]: ...

def envelope_to_message(envelope: Mapping[str, Any] | None) -> str: ...
```

规则：

- `messages` 首版只派生 `text` 和 `html`。HTML 判定只认 `<article`、`<!doctype`、`<html` 前缀。
- `reply_meta` 对外只保留 `send_mode`、`reply_to_message_id`、`mentions`、`quote`、`at_sender`。
- `meta` 清理 `None` 和空字符串，不清理 `0`、`False`、空 list 或空 dict。
- 群聊 `action="continue"` 映射 `status="ok"`；`wait` 映射 `wait`；`no_reply` 映射 `no_reply`。
- `envelope_to_message()` 优先返回非空 `reply`，否则拼接 `messages` 中 `type in {"text", "html"}` 的 `text`，其他类型忽略。

## 任务 1：新增响应信封 builder

**文件：**
- 创建：`tests/test_message_envelope.py`
- 创建：`core/message_envelope.py`

- [x] **步骤 1：编写 builder 红灯测试**

创建 `tests/test_message_envelope.py`：

```python
from core.message_envelope import build_chat_response_envelope
from core.message_envelope import build_group_response_envelope
from core.message_envelope import build_text_messages
from core.message_envelope import envelope_to_message
from core.message_envelope import sanitize_reply_meta


def test_build_text_messages_handles_empty_text_and_html():
    assert build_text_messages("") == []
    assert build_text_messages("你好") == [{"type": "text", "text": "你好"}]
    html = "<article><h1>日报</h1></article>"
    assert build_text_messages(html) == [{"type": "html", "text": html}]


def test_sanitize_reply_meta_keeps_protocol_keys_only():
    raw = {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
        "mentions": ["10001"],
        "quote": {"message_id": "m1"},
        "at_sender": True,
        "_agent_result": "prompt_audit_failed",
        "_no_reply": True,
        "_no_reply_reason": "internal",
        "debug": "drop",
    }

    assert sanitize_reply_meta(raw) == {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
        "mentions": ["10001"],
        "quote": {"message_id": "m1"},
        "at_sender": True,
    }
    assert sanitize_reply_meta(None) == {}


def test_build_chat_response_envelope_filters_meta_and_messages():
    envelope = build_chat_response_envelope(
        status="ok",
        answer="你好",
        reply_meta={"send_mode": "normal", "_agent_result": "ok"},
        meta={
            "user_id": "u1",
            "session_id": "private_u1",
            "platform": "web",
            "chat_type": "private",
            "empty": "",
            "none_value": None,
            "count": 0,
        },
    )

    assert envelope == {
        "status": "ok",
        "reply": "你好",
        "messages": [{"type": "text", "text": "你好"}],
        "reply_meta": {"send_mode": "normal"},
        "meta": {
            "user_id": "u1",
            "session_id": "private_u1",
            "platform": "web",
            "chat_type": "private",
            "count": 0,
        },
    }


def test_build_group_response_envelope_preserves_action_fields():
    envelope = build_group_response_envelope(
        action="wait",
        reply="",
        generation=5,
        delay_seconds=8,
        reason="user may type more",
        meta={"platform": "qq", "chat_type": "group", "group_id": "789"},
    )

    assert envelope["status"] == "wait"
    assert envelope["action"] == "wait"
    assert envelope["reply"] == ""
    assert envelope["messages"] == []
    assert envelope["reply_meta"] == {}
    assert envelope["meta"]["generation"] == 5
    assert envelope["meta"]["delay_seconds"] == 8
    assert envelope["meta"]["reason"] == "user may type more"
    assert envelope["meta"]["platform"] == "qq"


def test_envelope_to_message_prefers_reply_then_textual_messages():
    assert envelope_to_message({"reply": "正文", "messages": [{"type": "text", "text": "忽略"}]}) == "正文"
    assert envelope_to_message(
        {
            "reply": "",
            "messages": [
                {"type": "text", "text": "A"},
                {"type": "html", "text": "<article>B</article>"},
                {"type": "image", "url": "https://example.com/a.png"},
            ],
        }
    ) == "A\n<article>B</article>"
    assert envelope_to_message({"messages": [{"type": "image", "url": "https://example.com/a.png"}]}) == ""
```

- [x] **步骤 2：运行 builder 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_message_envelope.py -v -p no:cacheprovider
```

预期：失败，报错包含 `ModuleNotFoundError: No module named 'core.message_envelope'`。

- [x] **步骤 3：实现最小 builder**

创建 `core/message_envelope.py`：

```python
"""响应信封构造工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_HTML_PREFIXES = ("<article", "<!doctype", "<html")
_REPLY_META_KEYS = {
    "send_mode",
    "reply_to_message_id",
    "mentions",
    "quote",
    "at_sender",
}
_TEXTUAL_MESSAGE_TYPES = {"text", "html"}


def _clean_dict(data: Mapping[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if not isinstance(data, Mapping):
        return cleaned
    for key, value in data.items():
        if value is None or value == "":
            continue
        cleaned[str(key)] = value
    return cleaned


def is_html_reply(reply: str) -> bool:
    text = str(reply or "").lstrip().lower()
    return text.startswith(_HTML_PREFIXES)


def build_text_messages(reply: str) -> list[dict[str, str]]:
    text = str(reply or "")
    if not text.strip():
        return []
    message_type = "html" if is_html_reply(text) else "text"
    return [{"type": message_type, "text": text}]


def sanitize_reply_meta(reply_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply_meta, Mapping):
        return {}
    return {
        key: reply_meta[key]
        for key in _REPLY_META_KEYS
        if key in reply_meta and reply_meta[key] is not None
    }


def build_chat_response_envelope(
    *,
    status: str,
    answer: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reply = str(answer or "")
    return {
        "status": str(status or "ok"),
        "reply": reply,
        "messages": build_text_messages(reply),
        "reply_meta": sanitize_reply_meta(reply_meta),
        "meta": _clean_dict(meta),
    }


def _status_for_group_action(action: str) -> str:
    normalized = str(action or "").strip() or "no_reply"
    if normalized == "continue":
        return "ok"
    if normalized in {"wait", "no_reply"}:
        return normalized
    return normalized


def build_group_response_envelope(
    *,
    action: str,
    reply: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    generation: int | None = None,
    reason: str = "",
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    duplicate_reply: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response_meta = _clean_dict(meta)
    response_meta.update(
        _clean_dict(
            {
                "generation": generation,
                "reason": reason,
                "delay_seconds": delay_seconds,
                "diagnostics": dict(diagnostics) if isinstance(diagnostics, Mapping) else None,
                "duplicate_reply": dict(duplicate_reply) if isinstance(duplicate_reply, Mapping) else None,
            }
        )
    )
    normalized_action = str(action or "no_reply")
    text = str(reply or "")
    return {
        "status": _status_for_group_action(normalized_action),
        "action": normalized_action,
        "reply": text,
        "messages": build_text_messages(text),
        "reply_meta": sanitize_reply_meta(reply_meta),
        "meta": response_meta,
    }


def envelope_to_message(envelope: Mapping[str, Any] | None) -> str:
    if not isinstance(envelope, Mapping):
        return ""
    reply = str(envelope.get("reply") or "")
    if reply.strip():
        return reply
    raw_messages = envelope.get("messages") or []
    if not isinstance(raw_messages, list):
        return ""
    parts: list[str] = []
    for item in raw_messages:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in _TEXTUAL_MESSAGE_TYPES:
            continue
        text = str(item.get("text") or "")
        if text:
            parts.append(text)
    return "\n".join(parts)
```

- [x] **步骤 4：运行 builder 绿灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_message_envelope.py -v -p no:cacheprovider
```

预期：`5 passed`。

- [x] **步骤 5：运行相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_message_envelope.py \
  tests/test_bridge_integration.py::TestReplyMeta \
  -v -p no:cacheprovider
```

预期：新增 builder 测试和现有 reply_meta store 测试全部通过。

- [x] **步骤 6：提交 builder**

```bash
git add core/message_envelope.py tests/test_message_envelope.py
git commit -m "feat(消息): 构建响应信封"
```

## 任务 2：API owner 接入 `/chat` 与 SSE done 信封

**文件：**
- 创建：`tests/test_chat_response_envelope.py`
- 创建：`tests/test_streaming_response_envelope.py`
- 修改：`api/routes.py`

**并行约束：** 本任务独占 `api/routes.py`。群聊 owner 和 push owner 不得修改 `api/routes.py`。

- [x] **步骤 1：编写 `/chat` 非流式红灯测试**

创建 `tests/test_chat_response_envelope.py`：

```python
from unittest.mock import patch

from tests.test_api import _fast_private_reply


def test_proxy_chat_returns_standard_envelope_and_filtered_reply_meta(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "标准回复"

        def pop_last_reply_meta(self, session_id):
            assert session_id == "private_envelope_user"
            return {
                "send_mode": "quote",
                "reply_to_message_id": "m-source",
                "mentions": ["10001"],
                "_agent_result": "ok",
                "_no_reply": True,
            }

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "envelope_user",
                "session_id": "private_envelope_user",
                "query": "生成标准信封",
                "client_meta": {"platform": "web"},
            },
        )

    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "ok"
    assert data["answer"] == "标准回复"
    assert data["reply"] == "标准回复"
    assert data["messages"] == [{"type": "text", "text": "标准回复"}]
    assert data["reply_meta"] == {
        "send_mode": "quote",
        "reply_to_message_id": "m-source",
        "mentions": ["10001"],
    }
    assert data["meta"]["user_id"] == "envelope_user"
    assert data["meta"]["session_id"] == "private_envelope_user"
    assert data["meta"]["platform"] == "web"
    assert data["meta"]["chat_type"] == "private"
    assert data["meta"]["unprocessed_logs"] >= 0
```

- [x] **步骤 2：编写 `/chat` 静默红灯测试**

在同一文件追加：

```python
def test_proxy_chat_no_reply_returns_empty_standard_envelope(client, monkeypatch):
    from core.private_timing import PrivateDecision

    class NoReplyGate:
        async def classify(self, *args, **kwargs):
            return PrivateDecision(
                "no_reply",
                "unit_test_no_reply",
                1.0,
                "unit_test",
                complexity=0,
                effort="silent",
                runtime_preset="none",
            )

    monkeypatch.setattr("core.private_timing.get_private_gate", lambda: NoReplyGate())

    response = client.post(
        "/api/v1/chat",
        json={
            "user_id": "empty_envelope_user",
            "session_id": "private_empty_envelope_user",
            "query": "不用回复",
            "client_meta": {"platform": "qq"},
        },
    )

    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "no_reply"
    assert data["user_id"] == "empty_envelope_user"
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["meta"]["platform"] == "qq"
    assert data["meta"]["chat_type"] == "private"
```

- [x] **步骤 3：编写 SSE done 红灯测试**

创建 `tests/test_streaming_response_envelope.py`：

```python
import json
from unittest.mock import patch


def test_stream_chat_done_includes_standard_envelope_and_reply_meta(client):
    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "流式最终答案"

        def pop_last_reply_meta(self, session_id):
            assert session_id == "private_stream_envelope_user"
            return {"send_mode": "normal", "_agent_result": "ok"}

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "user_id": "stream_envelope_user",
                "session_id": "private_stream_envelope_user",
                "query": "流式信封",
                "stream": True,
                "client_meta": {"platform": "web"},
            },
        ) as response:
            body = "".join(response.iter_text())

    events = []
    for chunk in body.split("\n\n"):
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[6:]))
    done_event = next(item for item in events if item.get("status") == "done")

    assert response.status_code == 200
    assert done_event["answer"] == "流式最终答案"
    assert done_event["reply"] == "流式最终答案"
    assert done_event["messages"] == [{"type": "text", "text": "流式最终答案"}]
    assert done_event["reply_meta"] == {"send_mode": "normal"}
    assert done_event["meta"]["user_id"] == "stream_envelope_user"
    assert done_event["meta"]["session_id"] == "private_stream_envelope_user"
    assert done_event["meta"]["platform"] == "web"
    assert done_event["meta"]["chat_type"] == "private"
```

- [x] **步骤 4：运行 API 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  -v -p no:cacheprovider
```

预期：失败，报错包含缺少 `reply`、`messages`、`reply_meta` 或 `meta`。

- [x] **步骤 5：在 `api/routes.py` 增加 chat 响应 helper**

在 `_pop_bridge_reply_meta()` 后添加：

```python
def _chat_response_meta(
    req: ChatProxyRequest,
    *,
    platform: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "user_id": req.user_id,
        "session_id": req.session_id,
        "platform": platform or "qq",
        "chat_type": "group" if not str(req.session_id).startswith("private_") else "private",
    }
    if unprocessed_logs is not None:
        meta["unprocessed_logs"] = unprocessed_logs
    if reason:
        meta["reason"] = reason
    if source:
        meta["source"] = source
    if intent:
        meta["intent"] = intent
    return meta


def _chat_response_payload(
    base: dict[str, Any],
    *,
    req: ChatProxyRequest,
    answer: str = "",
    reply_meta: dict | None = None,
    platform: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
) -> dict[str, Any]:
    from core.message_envelope import build_chat_response_envelope

    payload = dict(base)
    payload.update(
        build_chat_response_envelope(
            status=str(base.get("status") or "ok"),
            answer=answer,
            reply_meta=reply_meta,
            meta=_chat_response_meta(
                req,
                platform=platform,
                unprocessed_logs=unprocessed_logs,
                reason=reason,
                source=source,
                intent=intent,
            ),
        )
    )
    return payload
```

- [x] **步骤 6：接入 `/chat` 非流式与 SSE done**

在 `/chat` 短路分支使用：

```python
return _chat_response_payload(
    {"status": "no_reply", "user_id": req.user_id},
    req=req,
    answer="",
    platform=client_platform,
    reason=_private_decision.reason,
)
```

在 `/chat` 成功分支使用：

```python
return _chat_response_payload(
    {
        "status": "ok",
        "user_id": req.user_id,
        "answer": transport_answer,
        "answer_chunks": answer_chunks,
        "unprocessed_logs": pending,
    },
    req=req,
    answer=transport_answer,
    reply_meta=private_reply_meta,
    platform=str(bridge_meta.get("platform") or client_platform),
    unprocessed_logs=pending,
)
```

在 `_stream_chat()` done 分支构造：

```python
done_payload = {
    "status": "done",
    "answer": transport_answer,
}
done_payload.update(
    build_chat_response_envelope(
        status="done",
        answer=transport_answer,
        reply_meta=private_reply_meta,
        meta=_chat_response_meta(
            req,
            platform=str(bridge_meta.get("platform") or client_platform),
        ),
    )
)
yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
```

不要改 `progress`、`delta`、`heartbeat` 和 `error` 事件结构。

- [x] **步骤 7：运行 API 绿灯和回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  tests/test_api.py::test_proxy_chat \
  tests/test_api.py::test_stream_chat_emits_progress_and_done_events \
  tests/test_streaming_api.py \
  -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 8：提交 API 信封**

```bash
git add api/routes.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py
git commit -m "feat(消息): 返回私聊响应信封"
```

## 任务 3：群聊 owner 接入 `/group/message` 响应信封

**文件：**
- 创建：`tests/test_group_response_envelope.py`
- 修改：`app/group_ingress/service.py`

**并行约束：** 本任务不得修改 `api/routes.py`。如果 route 层需要调整，交给任务 5。

- [x] **步骤 1：编写 continue 红灯测试**

创建 `tests/test_group_response_envelope.py`：

```python
import pytest


@pytest.mark.asyncio
async def test_group_message_continue_returns_standard_envelope(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    async def fake_process(*args, **kwargs):
        return {"action": "continue", "generation": 3, "reason": "reply now"}

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "群聊标准回复"

        def pop_last_reply_meta(self, session_id):
            return {
                "send_mode": "quote",
                "reply_to_message_id": "m1",
                "_agent_result": "ok",
            }

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())
    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    data = await group_message(
        GroupMessageRequest(
            group_id="envelope-group",
            sender_id="u-envelope",
            sender_name="信封测试",
            message="bot 你好",
            session_name="信封群",
            is_at_bot=True,
            client_meta={"platform": "web"},
            message_id="m-envelope-1",
        ),
        db_session,
        None,
    )

    assert data["action"] == "continue"
    assert data["status"] == "ok"
    assert data["reply"] == "群聊标准回复"
    assert data["messages"] == [{"type": "text", "text": "群聊标准回复"}]
    assert data["reply_meta"] == {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
    }
    assert data["generation"] == 3
    assert data["reason"] == "reply now"
    assert data["meta"]["platform"] == "web"
    assert data["meta"]["chat_type"] == "group"
    assert data["meta"]["group_id"] == "envelope-group"
    assert data["meta"]["generation"] == 3
```

- [x] **步骤 2：编写 wait 与 audit 红灯测试**

在同一文件追加：

```python
@pytest.mark.asyncio
async def test_group_message_wait_returns_empty_standard_envelope(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    async def fake_process(*args, **kwargs):
        return {"action": "wait", "generation": 5, "delay_seconds": 8, "reason": "user may type more"}

    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    data = await group_message(
        GroupMessageRequest(
            group_id="wait-envelope",
            sender_id="u-wait",
            sender_name="等待测试",
            message="我想问一下",
            session_name="等待群",
            is_at_bot=True,
            client_meta={"platform": "qq"},
        ),
        db_session,
        None,
    )

    assert data["action"] == "wait"
    assert data["status"] == "wait"
    assert data["delay_seconds"] == 8
    assert data["generation"] == 5
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["meta"]["delay_seconds"] == 8


@pytest.mark.asyncio
async def test_group_message_prompt_audit_failure_keeps_standard_envelope(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return ""

        def pop_last_reply_meta(self, session_id):
            return {"_agent_result": "prompt_v2_audit_failed"}

    class FakeRuntime:
        async def process_message(self, *args, **kwargs):
            return {"action": "continue", "generation": 1, "reason": "audit failure path"}

        def note_bot_replied(self, *args, **kwargs):
            raise AssertionError("audit failure must not mark bot as replied")

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: FakeRuntime())

    data = await group_message(
        GroupMessageRequest(
            group_id="audit-envelope",
            sender_id="u-audit-envelope",
            sender_name="审计信封",
            message="触发审计失败",
            session_name="审计信封群",
            is_at_bot=True,
            message_id="m-audit-envelope-1",
        ),
        db_session,
        None,
    )

    assert data["action"] == "no_reply"
    assert data["status"] == "no_reply"
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["reason"] == "prompt_v2_audit_failed"
    assert data["diagnostics"]["agent_result"] == "prompt_v2_audit_failed"
    assert data["meta"]["diagnostics"]["agent_result"] == "prompt_v2_audit_failed"
```

- [x] **步骤 3：运行群聊红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_group_response_envelope.py -v -p no:cacheprovider
```

预期：失败，报错包含缺少 `status`、`messages` 或 `meta`。

- [x] **步骤 4：在 `GroupIngressService` 增加响应 helper**

在 `GroupIngressService` 类内添加：

```python
    def _platform_from_request(self, req: Any) -> str:
        client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
        return str(client_meta.get("platform") or "qq").strip().lower() or "qq"

    def _response(
        self,
        req: Any,
        *,
        action: str,
        reply: str = "",
        reply_meta: dict | None = None,
        generation: int | None = 0,
        reason: str = "",
        delay_seconds: int | float | None = None,
        diagnostics: dict | None = None,
        duplicate_reply: dict | None = None,
        extra: dict | None = None,
    ) -> dict:
        from core.message_envelope import build_group_response_envelope

        base: dict[str, Any] = {"action": action}
        if reply or action == "continue":
            base["reply"] = reply
        if reply_meta is not None:
            base["reply_meta"] = reply_meta
        if generation is not None:
            base["generation"] = generation
        if delay_seconds is not None:
            base["delay_seconds"] = delay_seconds
        if reason:
            base["reason"] = str(reason)[:120]
        if diagnostics:
            base["diagnostics"] = diagnostics
        if duplicate_reply:
            base["duplicate_reply"] = duplicate_reply
        if extra:
            base.update(extra)

        base.update(
            build_group_response_envelope(
                action=action,
                reply=reply,
                reply_meta=reply_meta,
                generation=generation,
                reason=str(reason)[:120],
                delay_seconds=delay_seconds,
                diagnostics=diagnostics,
                duplicate_reply=duplicate_reply,
                meta={
                    "platform": self._platform_from_request(req),
                    "chat_type": "group",
                    "group_id": req.group_id,
                    "message_id": req.message_id or "",
                    "sender_id": req.sender_id,
                },
            )
        )
        return base
```

- [x] **步骤 5：替换 `handle()` 和 `_continue_to_bridge()` 的可控返回**

将 duplicate、DB lock、bot sender、user blocked、content blocked、timing wait、timing no_reply、duplicate reply suppressed、prompt audit failure、continue success 和 bridge exception 分支改为 `_response(...)`。

示例：

```python
return self._response(req, action="no_reply", reason="duplicate_message")
```

```python
return self._response(
    req,
    action=action,
    delay_seconds=result.get("delay_seconds"),
    generation=result.get("generation", 0),
    reason=str(result.get("reason", ""))[:120],
)
```

```python
return self._response(
    req,
    action="continue",
    reply=transport_answer,
    reply_meta=reply_meta,
    generation=result.get("generation", 0),
    reason=str(result.get("reason", ""))[:120],
)
```

- [x] **步骤 6：运行群聊绿灯和回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_group_response_envelope.py \
  tests/test_api.py::test_group_message_passes_client_platform_to_bridge \
  tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation \
  tests/test_api.py::test_group_message_wait_returns_generation \
  -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 7：提交群聊信封**

```bash
git add app/group_ingress/service.py tests/test_group_response_envelope.py
git commit -m "feat(消息): 返回群聊响应信封"
```

## 任务 4：push owner 新增信封推送适配

**文件：**
- 创建：`tests/test_push_envelope.py`
- 修改：`core/daily_digest.py`

**并行约束：** 本任务不改 `api/routes.py`。手动任务运行和流式断连 push 的 route 集成放到任务 5。

- [x] **步骤 1：编写 push helper 红灯测试**

创建 `tests/test_push_envelope.py`：

```python
import inspect

from tests.async_helpers import run_async


def test_push_envelope_to_qq_keeps_legacy_push_signature():
    from core import daily_digest

    assert list(inspect.signature(daily_digest.push_to_qq).parameters) == [
        "target_type",
        "target_id",
        "message",
    ]


def test_push_envelope_to_qq_derives_message_from_reply(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "private",
            "u1",
            {
                "reply": "推送正文",
                "messages": [{"type": "text", "text": "忽略"}],
            },
        )
    )

    assert result is True
    assert calls == [("private", "u1", "推送正文")]


def test_push_envelope_to_qq_falls_back_to_textual_messages(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "group",
            "g1",
            {
                "reply": "",
                "messages": [
                    {"type": "text", "text": "A"},
                    {"type": "html", "text": "<article>B</article>"},
                    {"type": "image", "url": "https://example.com/a.png"},
                ],
            },
        )
    )

    assert result is True
    assert calls == [("group", "g1", "A\n<article>B</article>")]


def test_push_envelope_to_qq_skips_empty_message(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(daily_digest.push_envelope_to_qq("group", "g1", {"messages": []}))

    assert result is False
    assert calls == []
```

- [x] **步骤 2：运行 push 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_push_envelope.py -v -p no:cacheprovider
```

预期：失败，报错包含 `AttributeError: module 'core.daily_digest' has no attribute 'push_envelope_to_qq'`。

- [x] **步骤 3：实现 `push_envelope_to_qq()`**

在 `core/daily_digest.py` 的 `push_to_qq()` 后添加：

```python
async def push_envelope_to_qq(target_type: str, target_id: str, envelope: dict) -> bool:
    """从标准响应信封派生旧 QQbot push message。"""
    from core.message_envelope import envelope_to_message

    message = envelope_to_message(envelope)
    if not message.strip():
        logger.warning("Skip empty QQ push envelope target_type=%s target_id=%s", target_type, target_id)
        return False
    return await push_to_qq(target_type, target_id, message)
```

- [x] **步骤 4：让定时任务使用信封适配层**

在 `run_scheduled_tasks()` 中替换：

```python
ok = await push_to_qq(task.target_type, task.target_id, content)
```

为：

```python
from core.message_envelope import build_chat_response_envelope

envelope = build_chat_response_envelope(
    status="ok",
    answer=content,
    meta={
        "platform": "qq",
        "chat_type": "scheduled_task",
        "task_id": task.id,
        "task_name": task.name,
        "target_type": task.target_type,
        "target_id": task.target_id,
    },
)
ok = await push_envelope_to_qq(task.target_type, task.target_id, envelope)
```

保留 `last_run_at` 在 push 前推进的现有语义。

- [x] **步骤 5：运行 push 绿灯和回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_push_envelope.py \
  tests/test_daily_digest.py \
  -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 6：提交 push 适配**

```bash
git add core/daily_digest.py tests/test_push_envelope.py
git commit -m "feat(推送): 支持信封推送适配"
```

## 任务 5：主线程集成 `api/routes.py` 的 push call site

**文件：**
- 修改：`api/routes.py`
- 创建或修改：`tests/test_api_push_envelope.py`

**并行约束：** 任务 2 和任务 4 完成并提交后再执行本任务。本任务由主线程或 API owner 执行，不分派给群聊 / push worker。

- [x] **步骤 1：编写 route push 红灯测试**

创建 `tests/test_api_push_envelope.py`，覆盖手动定时任务运行使用 `push_envelope_to_qq()`，并确认流式断连 push 仍保留 `expand_generated_image_refs_in_content(..., allow_base64=False)` 的边界。

```python
def test_run_scheduled_task_now_uses_push_envelope(client, db_session, monkeypatch):
    from core.database import ScheduledTask

    task = ScheduledTask(
        user_id="u1",
        name="测试任务",
        prompt_template="提醒我喝水",
        target_type="private",
        target_id="u1",
        schedule_type="daily",
        schedule_value="08:00",
        enabled=True,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    async def fake_generate(*args, **kwargs):
        return "任务内容"

    calls = []

    async def fake_push(target_type, target_id, envelope):
        calls.append((target_type, target_id, envelope))
        return True

    monkeypatch.setattr("core.daily_digest._generate_task_message", fake_generate)
    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", fake_push)

    response = client.post(
        f"/api/v1/tasks/{task.id}/run",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert calls[0][0:2] == ("private", "u1")
    assert calls[0][2]["reply"] == "任务内容"
    assert calls[0][2]["meta"]["chat_type"] == "scheduled_task"
```

- [x] **步骤 2：运行 route push 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api_push_envelope.py -v -p no:cacheprovider
```

预期：失败，原因是 route 仍调用 `push_to_qq()` 或 monkeypatch 没有被触发。

- [x] **步骤 3：改造 `api/routes.py` push call site**

在 `run_scheduled_task_now` 中将 import 改为：

```python
from core.daily_digest import _generate_task_message, push_envelope_to_qq
from core.message_envelope import build_chat_response_envelope
```

并将推送调用改为：

```python
envelope = build_chat_response_envelope(
    status="ok",
    answer=content,
    meta={
        "platform": "qq",
        "chat_type": "scheduled_task",
        "task_id": t.id,
        "task_name": t.name,
        "target_type": t.target_type,
        "target_id": t.target_id,
    },
)
ok = await push_envelope_to_qq(t.target_type, t.target_id, envelope)
```

流式断连后台 push 路径如果仍需直接调用旧 `push_to_qq()`，必须先保留：

```python
expanded = await expand_generated_image_refs_in_content(content, allow_base64=False)
```

再用 `build_chat_response_envelope(..., answer=expanded, meta={...})` 包装后调用 `push_envelope_to_qq()`。

- [x] **步骤 4：运行 route push 绿灯和 API 回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_api_push_envelope.py \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 5：提交 route push 集成**

```bash
git add api/routes.py tests/test_api_push_envelope.py
git commit -m "feat(推送): 接入路由信封推送"
```

## 任务 6：文档收口与最终验证

**文件：**
- 修改：`docs/message-field-standard.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/message-envelope.md`

- [x] **步骤 1：补充响应信封文档**

在 `docs/message-field-standard.md` 的入口章节后新增：

```markdown
## 响应信封

P2-2 起，`/chat`、`/chat` SSE done、`/group/message` 和 QQ push 适配共享以下标准字段。第一阶段采用兼容双写：旧字段继续保留，新字段并行新增。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 客户端状态。推荐值：`ok`、`silent`、`no_reply`、`wait`、`error`、`done`。 |
| `action` | string | 群聊 TimingGate / 出站动作。群聊继续保留 `continue`、`wait`、`no_reply`。 |
| `reply` | string | 传输层正文，等价于旧 `/chat.answer` 或旧 `/group/message.reply`。 |
| `messages` | list[object] | 标准化输出消息数组。P2-2 首版只使用 `text` 和 `html`。 |
| `reply_meta` | object | 发送意图元数据。只允许 `send_mode`、`reply_to_message_id`、`mentions`、`quote`、`at_sender`。 |
| `meta` | object | 平台、会话、路由、调度、诊断和追踪信息。 |

`messages` 首版结构：

```json
[
  {
    "type": "text",
    "text": "正文"
  }
]
```

HTML 正文使用：

```json
[
  {
    "type": "html",
    "text": "<article>...</article>"
  }
]
```

图片、@、引用和完整出站 `segments` 协议由 P2-3「QQ 出站渲染契约」负责，不在 P2-2 定义。
```

- [x] **步骤 2：同步路线文档状态**

更新：

- `docs/todo.md` 路线项 5：标记响应信封兼容双写已落地，P2-3 仍负责出站渲染契约。
- `docs/plan_walkthrough.md`：把 P2-2 任务 1-6 勾选，记录每个阶段提交号和最终验证输出。
- `.Codex/plans/message-envelope.md`：勾选已执行任务，记录最终验证输出。

- [x] **步骤 3：运行文档占位词扫描**

运行：

```bash
python - <<'PY'
from pathlib import Path
paths = [
    Path("docs/message-field-standard.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/message-envelope.md"),
]
needles = [
    "待" + "定",
    "后续" + "实现",
    "类似" + "任务",
    "添加" + "适当",
    "为" + "上述",
    "\ufffd",
]
failed = False
for path in paths:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if any(needle in line for needle in needles) or "T" + "ODO:" in line or "T" + "ODO：" in line:
            print(f"{path}:{line_no}:{line}")
            failed = True
raise SystemExit(1 if failed else 0)
PY
```

预期：无输出，退出码 0。

结果：无输出，退出码 0。

- [x] **步骤 4：运行格式检查**

运行：

```bash
git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/message-envelope.md
```

预期：无输出，退出码 0。

结果：无输出，退出码 0。

- [x] **步骤 5：运行 P2-2 定向回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_message_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_push_envelope.py \
  tests/test_api_push_envelope.py \
  tests/test_api.py \
  tests/test_streaming_api.py \
  tests/test_daily_digest.py \
  tests/test_reply_contract.py \
  tests/test_bridge_integration.py \
  -v -p no:cacheprovider
```

预期：全部通过。

结果：`130 passed, 21 warnings in 23.94s`。

- [x] **步骤 6：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

结果：`1263 passed, 6 skipped, 139 warnings in 90.13s`。

- [x] **步骤 7：提交文档收口**

```bash
git add docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/message-envelope.md
git commit -m "docs(计划): 同步响应信封状态"
```

## 验收清单

- [x] `/chat` 非流式成功响应包含 `reply`、`messages`、`reply_meta` 和 `meta`，同时保留 `answer`、`answer_chunks`、`status`、`user_id` 和 `unprocessed_logs`。
- [x] `/chat` 静默 / 不回复短路响应包含空 `reply`、空 `messages`、空 `reply_meta` 和基础 `meta`。
- [x] `/chat` SSE done 事件包含 `reply`、`messages`、`reply_meta` 和 `meta`，同时保留 `status="done"` 和 `answer`。
- [x] `/group/message` continue / wait / no_reply / prompt audit 分支都包含标准信封字段，同时保留旧调度字段。
- [x] 对外 `reply_meta` 不暴露 `_agent_result`、`_no_reply` 和 `_no_reply_reason`。
- [x] `push_to_qq(target_type, target_id, message) -> bool` 旧签名保持不变。
- [x] `push_envelope_to_qq(target_type, target_id, envelope) -> bool` 可从 `reply` 或 `messages` 派生旧 `message`。
- [x] `api/routes.py` 中 push call site 由单一 owner 集成，没有与群聊 / push worker 发生写冲突。
- [x] `docs/message-field-standard.md` 记录响应信封标准和 P2-2 / P2-3 边界。
- [x] 定向测试和全量测试通过。
