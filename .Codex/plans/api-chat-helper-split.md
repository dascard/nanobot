# 普通 API Chat Helper 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中聊天内容 helper 与响应契约 helper 拆到独立模块，保留 `/chat` 路由本体、旧父模块 wrapper、monkeypatch 合同和流式 / 非流式响应语义。

**架构：** 新增 `api/chat_content_helpers.py` 承载文件归一化和多模态文本构造，新增 `api/chat_response_contract.py` 承载 SSE 事件规范化与 response envelope 适配。`api.routes` 继续保留旧下划线函数作为 wrapper，`proxy_chat()`、`_persist_chat_turn()` 和 `_stream_chat()` 仍调用父模块名称，确保既有测试和外部调试脚本的 `api.routes.*` 入口不失效。

**技术栈：** Python 3.13、FastAPI、Pydantic、pytest、现有 `core.context_builder`、`core.client_meta`、`core.message_envelope`、普通 API split 测试模板。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-chat-helper-split-design.md`。
- 设计提交：`7063415 docs(普通API): 设计聊天助手拆分`。
- 计划提交：`6cfd183 docs(计划): 记录聊天助手拆分计划`。
- 红灯测试提交：`b313580 test(普通API): 锁定聊天助手拆分契约`。
- 实现提交：`dd34229 refactor(普通API): 拆分聊天助手契约`。
- `api/routes.py` 已从 1709 行降至 1604 行，剩余显式 route 为 `/chat` 与 `/health`。
- 本阶段不迁移 `proxy_chat()`、`ChatProxyRequest`、`_private_buffers`、`_persist_chat_turn()`、
  `_safe_meta()`、`get_bridge`、`get_guardrail`、`CHAT_STREAM_QUEUE_MAXSIZE` 或 `/health`。
- 本阶段不修改 Prompt Runtime 模板、`enriched_query`、历史注入、conversation 结构或工具输出契约。
- 本阶段必须保留 `api.routes._normalize_files.__module__ == "api.routes"`、
  `api.routes._schedule_image_precache.__module__ == "api.routes"`、
  `api.routes._build_multimodal_user_input_text.__module__ == "api.routes"`、
  `api.routes._build_chatlog_user_content.__module__ == "api.routes"` 和
  `api.routes._build_conversation_user_content.__module__ == "api.routes"`。
- 本阶段不得新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 文件职责

- 创建：`tests/test_api_chat_helpers_split.py`
  - 锁定新 helper 模块存在、禁止反向导入父模块、父模块 wrapper 兼容、内容 helper 输出、响应 envelope 输出、SSE 编码和安全错误事件。
- 创建：`api/chat_content_helpers.py`
  - 承载 `normalize_files()`、`build_guardrail_input()`、`build_multimodal_user_input_text()`、`build_file_archive_summary()`、`build_chatlog_user_content()`、`build_conversation_user_content()`。
- 创建：`api/chat_response_contract.py`
  - 承载 `normalize_chat_stream_event()`、`chat_sse_data()`、`stream_error_event()`、`split_chat_answer_chunks()`、`chat_response_meta()`、`chat_response_payload()`。
- 修改：`api/routes.py`
  - 导入新 helper 模块。
  - 将旧 helper 改成薄 wrapper。
  - 保留 `_schedule_image_precache()` 在父模块。
  - 将 `_stream_chat()` 内联 SSE 编码与安全错误事件改为调用父模块 wrapper。
- 修改：`tests/test_api_sticker_media_routes_split.py`
  - 保留父模块 helper `__module__` 哨兵，并增加新模块禁用模式扫描。
- 修改：`tests/test_api_group_message_routes_split.py`
  - 保留 `/chat` 与 multimodal helper 父模块哨兵，并增加新模块禁用模式扫描。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 保留 `/chat` 父模块哨兵，必要时增加 response contract wrapper 哨兵。
- 修改：`.Codex/plans/api-chat-helper-split.md`
  - 文档收口时勾选执行记录和验收结果。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 普通 API chat helper 拆分进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 阶段记录。

## 任务 1：补 chat helper split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_chat_helpers_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`

- [x] **步骤 1：创建新增 split 测试文件**

创建 `tests/test_api_chat_helpers_split.py`：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from api import routes


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_helper_modules_do_not_import_parent_routes_or_sync_awaitable():
    for path in ("api/chat_content_helpers.py", "api/chat_response_contract.py"):
        source = _source(path)

        assert "from api.routes" not in source
        assert "import api.routes" not in source
        assert "asyncio.run" not in source
        assert "run_awaitable_sync" not in source


def test_legacy_parent_chat_helper_wrappers_keep_api_routes_module():
    assert routes._normalize_files.__module__ == "api.routes"
    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert routes._build_guardrail_input.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"
    assert routes._build_chatlog_user_content.__module__ == "api.routes"
    assert routes._build_conversation_user_content.__module__ == "api.routes"
    assert routes._normalize_chat_stream_event.__module__ == "api.routes"
    assert routes._split_chat_answer_chunks.__module__ == "api.routes"
    assert routes._chat_response_payload.__module__ == "api.routes"


def test_chat_content_helpers_match_parent_facade():
    from api import chat_content_helpers

    files = ["", "  ", 42, "http://img.example/a.png", "token://b"]

    assert chat_content_helpers.normalize_files(files) == [
        "http://img.example/a.png",
        "token://b",
    ]
    assert routes._normalize_files(files) == chat_content_helpers.normalize_files(files)
    assert routes._build_guardrail_input("", files) == "[图片消息，共 2 张]"
    assert routes._build_guardrail_input("看看", files) == "看看\n[附带图片 2 张]"


def test_multimodal_user_input_text_keeps_existing_contract():
    assert routes._build_multimodal_user_input_text("你好", None) == "你好"
    assert (
        routes._build_multimodal_user_input_text("", ["img://a", "img://b"])
        == "[用户附带了 2 张图片，请结合图片内容理解并回答]"
    )
    assert (
        routes._build_multimodal_user_input_text("请看", ["img://a"])
        == "请看\n[用户附带了 1 张图片，请结合图片内容理解并回答]"
    )


def test_chatlog_and_conversation_content_keep_different_file_archive_contracts():
    files = ["http://img.example/a.png", "token://b"]

    chatlog_content = routes._build_chatlog_user_content("看看", files)
    conversation_content = routes._build_conversation_user_content("看看", files)

    assert chatlog_content == (
        "看看\n"
        "[图片附件 2 张]\n"
        "[图片1] http://img.example/a.png\n"
        "[图片2] token://b"
    )
    assert conversation_content == "看看\n[图片附件 2 张]"
    assert "http://img.example/a.png" not in conversation_content
    assert "token://b" not in conversation_content


def test_chat_stream_event_contract_is_available_through_parent_facade():
    from api import chat_response_contract

    assert routes._normalize_chat_stream_event({"status": "delta", "text": "你"}) == {
        "status": "delta",
        "text": "你",
    }
    assert routes._normalize_chat_stream_event({"status": "delta", "text": ""}) is None
    assert routes._normalize_chat_stream_event({"status": "final", "text": "完成"}) == {
        "status": "final",
        "text": "完成",
        "replace": True,
        "source": "bridge",
    }
    assert routes._normalize_chat_stream_event({"status": "progress", "step": "thinking"}) == {
        "status": "progress",
        "step": "thinking",
    }
    assert routes._normalize_chat_stream_event({"text": "missing status"}) is None
    assert routes._normalize_chat_stream_event({"status": "final", "text": "完成"}) == (
        chat_response_contract.normalize_chat_stream_event({"status": "final", "text": "完成"})
    )


def test_chat_sse_data_and_safe_error_event_contract():
    from api import chat_response_contract

    assert routes._chat_sse_data({"status": "delta", "text": "你好"}) == (
        'data: {"status": "delta", "text": "你好"}\n\n'
    )
    assert routes._stream_error_event() == {
        "status": "error",
        "message": routes.SAFE_STREAM_ERROR_MESSAGE,
    }
    assert routes._chat_sse_data(routes._stream_error_event()) == chat_response_contract.chat_sse_data(
        chat_response_contract.stream_error_event(routes.SAFE_STREAM_ERROR_MESSAGE)
    )


def test_chat_response_payload_contract_stays_compatible():
    req = SimpleNamespace(
        user_id="u1",
        session_id="private_u1",
        client_meta={"platform": "qq", "request_id": "req-1"},
    )

    payload = routes._chat_response_payload(
        req,
        status="ok",
        answer="第一段\n\n第二段",
        reply_meta={"send_mode": "reply", "_agent_result": "internal"},
        include_answer_chunks=True,
        guardrail_status="safe",
    )

    assert payload["status"] == "ok"
    assert payload["reply"] == "第一段\n\n第二段"
    assert payload["answer"] == payload["reply"]
    assert payload["messages"] == [{"type": "text", "text": "第一段\n\n第二段"}]
    assert payload["reply_meta"] == {"send_mode": "reply"}
    assert payload["answer_chunks"] == ["第一段", "第二段"]
    assert payload["meta"]["user_id"] == "u1"
    assert payload["meta"]["session_id"] == "private_u1"
    assert payload["meta"]["platform"] == "qq"
    assert payload["meta"]["chat_type"] == "private"
    assert payload["meta"]["request_id"] == "req-1"
    assert payload["meta"]["guardrail_status"] == "safe"
```

- [x] **步骤 2：扩展相邻 split 测试的源码扫描**

在 `tests/test_api_sticker_media_routes_split.py`、`tests/test_api_group_message_routes_split.py`
或 `tests/test_api_agent_step_routes_split.py` 的禁用模式测试中加入：

```python
for path in ("api/chat_content_helpers.py", "api/chat_response_contract.py"):
    source = Path(path).read_text(encoding="utf-8")
    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py
```

预期：FAIL。失败点应集中在 `api/chat_content_helpers.py` 和
`api/chat_response_contract.py` 不存在，以及 `api.routes._chat_sse_data`、
`api.routes._stream_error_event` 尚未定义。

- [x] **步骤 4：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_helpers_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定聊天助手拆分契约"
```

## 任务 2：实现 chat content helper 模块与父模块 wrapper

**文件：**

- 创建：`api/chat_content_helpers.py`
- 修改：`api/routes.py`
- 测试：`tests/test_api_chat_helpers_split.py`

- [x] **步骤 1：创建 `api/chat_content_helpers.py`**

创建文件：

```python
"""聊天内容 helper。"""

from __future__ import annotations

from typing import Optional

from core.context_builder import sanitize_prompt_text


def normalize_files(files: Optional[list[str]]) -> list[str]:
    return [file for file in (files or []) if isinstance(file, str) and file.strip()]


def build_guardrail_input(query: str, files: Optional[list[str]]) -> str:
    normalized_files = normalize_files(files)
    text = str(query or "").strip()
    if normalized_files and text:
        return f"{text}\n[附带图片 {len(normalized_files)} 张]"
    if normalized_files:
        return f"[图片消息，共 {len(normalized_files)} 张]"
    return query


def build_multimodal_user_input_text(
    query: str,
    files: Optional[list[str]],
    *,
    max_chars: int = 0,
) -> str:
    text = sanitize_prompt_text(query, max_chars) if query else ""
    normalized_files = normalize_files(files)
    parts: list[str] = []
    if text.strip():
        parts.append(text)
    if normalized_files:
        parts.append(f"[用户附带了 {len(normalized_files)} 张图片，请结合图片内容理解并回答]")
    return "\n".join(parts)


def build_file_archive_summary(files: Optional[list[str]], *, include_refs: bool) -> str:
    normalized_files = normalize_files(files)
    if not normalized_files:
        return ""

    header = f"[图片附件 {len(normalized_files)} 张]"
    if not include_refs:
        return header

    lines = [header]
    preview_limit = 3
    for idx, file_ref in enumerate(normalized_files[:preview_limit], start=1):
        lines.append(f"[图片{idx}] {file_ref}")
    remaining = len(normalized_files) - preview_limit
    if remaining > 0:
        lines.append(f"[其余 {remaining} 张图片地址省略]")
    return "\n".join(lines)


def build_chatlog_user_content(query: str, files: Optional[list[str]]) -> str:
    text = str(query or "").strip()
    file_summary = build_file_archive_summary(files, include_refs=True)
    if text and file_summary:
        return f"{text}\n{file_summary}"
    if file_summary:
        return file_summary
    return query


def build_conversation_user_content(query: str, files: Optional[list[str]]) -> str:
    text = str(query or "").strip()
    file_summary = build_file_archive_summary(files, include_refs=False)
    if text and file_summary:
        return f"{text}\n{file_summary}"
    if file_summary:
        return file_summary
    return query
```

- [x] **步骤 2：在 `api/routes.py` 中导入模块**

在现有 API 子模块导入附近加入：

```python
from api import chat_content_helpers
```

- [x] **步骤 3：将父模块内容 helper 改成 wrapper**

用 wrapper 替换父模块中的内容 helper 主体：

```python
def _normalize_files(files: Optional[List[str]]) -> list[str]:
    return chat_content_helpers.normalize_files(files)


def _build_guardrail_input(query: str, files: Optional[List[str]]) -> str:
    return chat_content_helpers.build_guardrail_input(query, files)


def _build_multimodal_user_input_text(query: str, files: Optional[List[str]], *, max_chars: int = 0) -> str:
    return chat_content_helpers.build_multimodal_user_input_text(query, files, max_chars=max_chars)


def _build_file_archive_summary(files: Optional[List[str]], *, include_refs: bool) -> str:
    return chat_content_helpers.build_file_archive_summary(files, include_refs=include_refs)


def _build_chatlog_user_content(query: str, files: Optional[List[str]]) -> str:
    return chat_content_helpers.build_chatlog_user_content(query, files)


def _build_conversation_user_content(query: str, files: Optional[List[str]]) -> str:
    return chat_content_helpers.build_conversation_user_content(query, files)
```

保持 `_schedule_image_precache()` 留在父模块，并继续调用 `_normalize_files(files)`。

- [x] **步骤 4：运行内容 helper 定向测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py \
  -k "content or multimodal or chatlog or conversation or wrappers"
```

预期：content 相关断言 PASS；response contract 相关断言仍可因任务 3 未完成而 FAIL。

## 任务 3：实现 chat response contract 模块与父模块 wrapper

**文件：**

- 创建：`api/chat_response_contract.py`
- 修改：`api/routes.py`
- 测试：`tests/test_api_chat_helpers_split.py`

- [x] **步骤 1：创建 `api/chat_response_contract.py`**

创建文件：

```python
"""聊天响应契约 helper。"""

from __future__ import annotations

import json
from typing import Any

from core.client_meta import client_meta_request_id
from core.message_envelope import build_chat_response_envelope


def normalize_chat_stream_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None

    status = str(event.get("status") or "")
    if status == "delta":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        normalized = dict(event)
        normalized["status"] = "delta"
        normalized["text"] = text
        return normalized

    if status == "final":
        text = event.get("text", "")
        if text is None:
            text = ""
        text = str(text)
        if not text:
            return None
        return {
            "status": "final",
            "text": text,
            "replace": bool(event.get("replace", True)),
            "source": str(event.get("source") or "bridge"),
        }

    if status:
        normalized = dict(event)
        normalized["status"] = status
        return normalized

    return None


def chat_sse_data(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_error_event(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


def split_chat_answer_chunks(answer: str) -> list[str]:
    text = str(answer or "")
    if text.lstrip().startswith("<article") or text.lstrip().startswith("<!doctype") or text.lstrip().startswith("<html"):
        return [text]
    if not text.strip():
        return []
    if "\n\n" in text:
        return [c.strip() for c in text.split("\n\n") if c.strip()]
    if "\n" in text:
        return [c.strip() for c in text.split("\n") if c.strip()]
    return [text]


def _chat_request_platform(req: Any) -> str:
    client_meta = getattr(req, "client_meta", None)
    client_meta = client_meta if isinstance(client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def _chat_request_type(req: Any) -> str:
    return "private" if str(getattr(req, "session_id", "")).startswith("private_") else "group"


def chat_response_meta(
    req: Any,
    *,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    client_meta = getattr(req, "client_meta", None)
    meta: dict[str, Any] = {
        "user_id": getattr(req, "user_id", ""),
        "session_id": getattr(req, "session_id", ""),
        "platform": platform or _chat_request_platform(req),
        "chat_type": chat_type or _chat_request_type(req),
    }
    request_id = client_meta_request_id(client_meta)
    if request_id:
        meta["request_id"] = request_id
    if unprocessed_logs is not None:
        meta["unprocessed_logs"] = unprocessed_logs
    if reason:
        meta["reason"] = reason
    if source:
        meta["source"] = source
    if intent:
        meta["intent"] = intent
    if guardrail_status:
        meta["guardrail_status"] = guardrail_status
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    return meta


def chat_response_payload(
    req: Any,
    *,
    status: str,
    answer: str = "",
    reply_meta: dict | None = None,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    include_answer_chunks: bool = False,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    payload = build_chat_response_envelope(
        status=status,
        answer=answer,
        reply_meta=reply_meta,
        meta=chat_response_meta(
            req,
            platform=platform,
            chat_type=chat_type,
            unprocessed_logs=unprocessed_logs,
            reason=reason,
            source=source,
            intent=intent,
            guardrail_status=guardrail_status,
            extra_meta=extra_meta,
        ),
    )
    payload["user_id"] = getattr(req, "user_id", "")
    payload["answer"] = payload["reply"]
    if unprocessed_logs is not None:
        payload["unprocessed_logs"] = unprocessed_logs
    if reason:
        payload["reason"] = reason
    if source:
        payload["source"] = source
    if intent:
        payload["intent"] = intent
    if include_answer_chunks:
        payload["answer_chunks"] = split_chat_answer_chunks(payload["reply"])
    return payload
```

- [x] **步骤 2：在 `api/routes.py` 中导入模块**

在现有 API 子模块导入附近加入：

```python
from api import chat_response_contract
```

如果 `api/routes.py` 顶部的 `client_meta_request_id` 和 `build_chat_response_envelope`
只剩 response wrapper 使用，同步从父模块 import 列表中移除它们。

- [x] **步骤 3：将父模块 response helper 改成 wrapper**

用 wrapper 替换父模块中 `_normalize_chat_stream_event()`、`_split_chat_answer_chunks()`、
`_chat_response_meta()` 和 `_chat_response_payload()` 的主体，并新增
`_chat_sse_data()`、`_stream_error_event()`：

```python
def _normalize_chat_stream_event(event: Any) -> dict[str, Any] | None:
    return chat_response_contract.normalize_chat_stream_event(event)


def _chat_sse_data(event: dict[str, Any]) -> str:
    return chat_response_contract.chat_sse_data(event)


def _stream_error_event() -> dict[str, str]:
    return chat_response_contract.stream_error_event(SAFE_STREAM_ERROR_MESSAGE)


def _split_chat_answer_chunks(answer: str) -> list[str]:
    return chat_response_contract.split_chat_answer_chunks(answer)


def _chat_response_meta(
    req: ChatProxyRequest,
    *,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    return chat_response_contract.chat_response_meta(
        req,
        platform=platform,
        chat_type=chat_type,
        unprocessed_logs=unprocessed_logs,
        reason=reason,
        source=source,
        intent=intent,
        guardrail_status=guardrail_status,
        extra_meta=extra_meta,
    )


def _chat_response_payload(
    req: ChatProxyRequest,
    *,
    status: str,
    answer: str = "",
    reply_meta: dict | None = None,
    platform: str = "",
    chat_type: str = "",
    unprocessed_logs: int | None = None,
    reason: str = "",
    source: str = "",
    intent: str = "",
    guardrail_status: str | None = None,
    include_answer_chunks: bool = False,
    extra_meta: dict | None = None,
) -> dict[str, Any]:
    return chat_response_contract.chat_response_payload(
        req,
        status=status,
        answer=answer,
        reply_meta=reply_meta,
        platform=platform,
        chat_type=chat_type,
        unprocessed_logs=unprocessed_logs,
        reason=reason,
        source=source,
        intent=intent,
        guardrail_status=guardrail_status,
        include_answer_chunks=include_answer_chunks,
        extra_meta=extra_meta,
    )
```

- [x] **步骤 4：让 `_stream_chat()` 使用 SSE wrapper**

在 `api/routes.py` 的 `_stream_chat()` 内删除内联 `_encode_sse()`，将调用改为：

```python
yield _chat_sse_data(pending_delta)
yield _chat_sse_data(next_event)
yield _chat_sse_data({"status": "heartbeat"})
yield _chat_sse_data(_stream_error_event())
yield _chat_sse_data(done_payload)
```

不要移动 runner、queue、drain、background push 或持久化逻辑。

- [x] **步骤 5：运行 helper split 测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py
```

预期：PASS。

## 任务 4：运行相邻与行为回归并提交实现

**文件：**

- 修改：`api/chat_content_helpers.py`
- 修改：`api/chat_response_contract.py`
- 修改：`api/routes.py`
- 测试：`tests/test_api_chat_helpers_split.py`
- 测试：相邻 split 与 `/chat` 行为测试

- [x] **步骤 1：运行 split 相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_memory_routes_split.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

- [x] **步骤 2：运行 `/chat` 流式与信封回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py \
  tests/test_api_push_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_message_envelope.py
```

预期：PASS。

- [x] **步骤 3：运行 `/chat` 私聊缓冲和持久化关键 nodeid**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_proxy_chat_kt_error_does_not_echo_internal_detail \
  tests/test_api.py::test_stream_chat_emits_progress_and_done_events \
  tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
  tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
  tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
  tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request
```

预期：PASS。

- [x] **步骤 4：运行静态检查**

运行：

```bash
python -B -m py_compile \
  api/routes.py \
  api/chat_content_helpers.py \
  api/chat_response_contract.py \
  tests/test_api_chat_helpers_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" \
  api/chat_content_helpers.py \
  api/chat_response_contract.py
git diff --check -- \
  api/routes.py \
  api/chat_content_helpers.py \
  api/chat_response_contract.py \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py
wc -l api/routes.py api/chat_content_helpers.py api/chat_response_contract.py tests/test_api_chat_helpers_split.py
```

预期：

- `py_compile` 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数下降。

- [x] **步骤 5：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [x] **步骤 6：提交实现**

运行：

```bash
git add api/routes.py \
  api/chat_content_helpers.py \
  api/chat_response_contract.py \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分聊天助手契约"
```

## 任务 5：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-chat-helper-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新本计划执行记录**

在本文件的「执行记录」章节追加实际命令和结果。记录必须包含红灯失败数量与原因、
split 绿灯通过数量、`/chat` 行为回归通过数量、静态检查结果和全量测试通过数量。

- [x] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」的 `api/routes.py` 进展列表中新增一条：

```markdown
  - 进展：`api/routes.py` 第十一刀已拆出 chat content / response contract helper 到
    `api/chat_content_helpers.py` 与 `api/chat_response_contract.py`；旧 `api.routes`
    继续保留同名 wrapper，`/chat` 路由本体、`ChatProxyRequest`、私聊缓冲、
    `_persist_chat_turn()`、`_safe_meta()`、`get_bridge` / `get_guardrail` monkeypatch、
    `CHAT_STREAM_QUEUE_MAXSIZE` 和 `/health` 仍留在父模块。保留 SSE delta 合并、
    安全错误事件、response envelope、`answer_chunks`、ChatLog 完整图片归档和
    ConversationTurn 图片摘要语义。
```

行数写入 `wc -l` 的实际输出，验证结果写入任务 4 中各命令的实际通过数量。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在 2026-06-22 记录中追加 chat helper 拆分阶段：

```markdown
### 2026-06-22：普通 API Chat Helper 拆分

- 目标：拆出聊天内容 helper 与响应契约 helper，保留 `/chat` 编排和旧父模块兼容入口。
- 已完成：设计、计划、红灯测试、实现、定向回归、静态检查和全量回归。
- 保留：`proxy_chat()`、`ChatProxyRequest`、私聊缓冲、聊天落库、Prompt Runtime 输入组装、
  bridge/SSE runner 和 `/health` 仍留在 `api.routes`。
```

- [x] **步骤 4：验证文档**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' \
  .Codex/plans/api-chat-helper-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
git diff --check -- \
  .Codex/plans/api-chat-helper-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
```

预期：`rg` 无命中，`git diff --check` 无输出。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-helper-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天助手拆分"
```

## 执行记录

- 设计：`7063415 docs(普通API): 设计聊天助手拆分`。
- 计划：`6cfd183 docs(计划): 记录聊天助手拆分计划`。
- 红灯测试：`b313580 test(普通API): 锁定聊天助手拆分契约`。
  - 首次红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py`
    -> `8 failed, 35 passed, 21 warnings in 10.08s`；其中 7 个失败来自新模块缺失，
    另 1 个失败暴露测试误用顶层 `request_id`。已按真实契约改为
    `client_meta.trace.request_id`。
  - 修正后红灯：同一命令 -> `7 failed, 36 passed, 21 warnings in 10.03s`；
    失败点集中在 `api/chat_content_helpers.py`、`api/chat_response_contract.py`
    不存在或无法导入，符合 TDD 预期。
- 实现：`dd34229 refactor(普通API): 拆分聊天助手契约`。
- Helper split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py`
  -> `8 passed, 1 warning in 0.89s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_memory_routes_split.py tests/test_asyncio_run_policy.py`
  -> `62 passed, 21 warnings in 7.57s`。
- `/chat` 流式与信封回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_streaming_api.py tests/test_streaming_response_envelope.py tests/test_api_push_envelope.py tests/test_chat_response_envelope.py tests/test_message_envelope.py`
  -> `21 passed, 21 warnings in 7.21s`。
- `/chat` 私聊缓冲和持久化关键 nodeid：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_proxy_chat_kt_error_does_not_echo_internal_detail tests/test_api.py::test_stream_chat_emits_progress_and_done_events tests/test_api.py::test_stream_chat_passes_stream_flag_to_bridge tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request`
  -> `11 passed, 21 warnings in 2.86s`。
- 静态检查：`python -B -m py_compile api/routes.py api/chat_content_helpers.py api/chat_response_contract.py tests/test_api_chat_helpers_split.py`
  成功；`rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/chat_content_helpers.py api/chat_response_contract.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/chat_content_helpers.py api/chat_response_contract.py tests/test_api_chat_helpers_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1604 行，`api/chat_content_helpers.py` 76 行，
  `api/chat_response_contract.py` 163 行，`tests/test_api_chat_helpers_split.py` 144 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1662 passed, 6 skipped, 139 warnings in 125.10s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_helpers_split.py tests/test_streaming_response_envelope.py tests/test_asyncio_run_policy.py`
  -> `13 passed, 21 warnings in 3.66s`。

## 验收清单

- [x] `api/chat_content_helpers.py` 不导入 `api.routes`。
- [x] `api/chat_response_contract.py` 不导入 `api.routes`。
- [x] 新模块无 `asyncio.run` 和 `run_awaitable_sync`。
- [x] 父模块 helper wrapper 的 `__module__` 仍为 `"api.routes"`。
- [x] `_schedule_image_precache()` 继续留在父模块并可被 monkeypatch。
- [x] `proxy_chat()`、`ChatProxyRequest`、`_private_buffers`、`_persist_chat_turn()` 和 `_safe_meta()` 仍留在父模块。
- [x] SSE delta 合并、安全错误事件和 done envelope 语义不变。
- [x] 非流式响应继续包含 `answer_chunks`。
- [x] ChatLog 继续保存图片引用，ConversationTurn 继续只保存图片数量摘要。
- [x] 全量测试 0 failures。
