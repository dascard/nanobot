# 普通 API 聊天落库拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 `_persist_chat_turn()` 的聊天落库实现拆到 `api/chat_persistence.py`，保留父模块兼容 wrapper、`/chat` 调用时机和既有 monkeypatch 合同。

**架构：** 新模块 `api/chat_persistence.py` 承载同步 DB writer、`ChatTurnPersistenceInput`、`safe_meta()` 和 `persist_chat_turn()`。`api.routes` 继续定义 `_safe_meta()` 与 `_persist_chat_turn()` 薄 wrapper，并让 `proxy_chat()`、流式 finalizer 和非流式分支继续调用父模块名称。测试先锁定 silent / injection / HTML / audit failure / timing meta / source ids / pending count / SQLite retry 等行为，再迁移实现。

**技术栈：** Python 3.13、FastAPI、Pydantic、SQLAlchemy、pytest、现有 `core.database`、`core.sqlite_retry`、`api.chat_content_helpers`、普通 API split 测试模板。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-chat-persistence-split-design.md`。
- 设计提交：`0e38393 docs(普通API): 设计聊天落库拆分`。
- 计划提交：`237efa0 docs(计划): 记录聊天落库拆分计划`。
- 红灯测试提交：`7d55196 test(普通API): 锁定聊天落库拆分契约`。
- 实现提交：`ea7e834 refactor(普通API): 拆分聊天落库 writer`。
- `api/routes.py` 已从 1604 行降至 1516 行，剩余显式 route 为 `/chat` 与 `/health`。
- 上一阶段已拆出 `api/chat_content_helpers.py` 与 `api/chat_response_contract.py`。
- 本阶段不迁移 `proxy_chat()`、`ChatProxyRequest`、`_private_buffers`、streaming runner、
  `get_bridge`、`get_guardrail`、`CHAT_STREAM_QUEUE_MAXSIZE` 或 `/health`。
- 本阶段不修改 Prompt Runtime 模板、`enriched_query`、历史注入、conversation 结构或工具输出契约。
- 本阶段不得新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 验证结果：红灯 `6 failed, 49 passed, 21 warnings in 11.14s`；新增 split 绿灯
  `10 passed, 1 warning in 1.07s`；相邻回归 `74 passed, 21 warnings in 8.49s`；
  `/chat` 行为回归 `8 passed, 21 warnings in 2.15s`；全量回归
  `1673 passed, 6 skipped, 139 warnings in 126.35s`；文档收口复验为红旗词扫描无输出、
  `git diff --check` 无输出、全量回归 `1673 passed, 6 skipped, 139 warnings in 123.49s`。

## 文件职责

- 创建：`tests/test_api_chat_persistence_split.py`
  - 锁定新 persistence 模块、父模块 wrapper、落库行为、meta 合同、pending 计数和禁用模式扫描。
- 创建：`api/chat_persistence.py`
  - 承载 `ChatTurnPersistenceInput`、`safe_meta()`、`persist_chat_turn()` 及 writer 私有 helper。
- 修改：`api/routes.py`
  - 导入 `api.chat_persistence`。
  - 将 `_safe_meta()` 与 `_persist_chat_turn()` 改为父模块 wrapper。
  - 移除父模块中已迁移的落库实现细节和不再需要的 `SensitiveData` 局部 import。
- 修改：`tests/test_api_history_log_routes_split.py`
  - 保留 `_persist_chat_turn()` / `_safe_meta()` 父模块哨兵，并把 `api/chat_persistence.py` 加入源码禁用模式扫描。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 保留聊天边界父模块哨兵，并把新模块加入源码禁用模式扫描。
- 修改：`tests/test_api_group_message_routes_split.py`
  - 保留 `/chat` 父模块边界，并把新模块加入源码禁用模式扫描。
- 修改：`tests/test_api_sticker_media_routes_split.py`
  - 保留聊天落库父模块边界，并把新模块加入源码禁用模式扫描。
- 修改：`.Codex/plans/api-chat-persistence-split.md`
  - 实现完成后勾选执行记录和验收结果。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 普通 API 聊天落库拆分进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 聊天落库拆分阶段记录。

## 任务 1：补聊天落库 split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_chat_persistence_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：创建新增 split 测试文件**

创建 `tests/test_api_chat_persistence_split.py`，核心测试内容如下：

```python
from __future__ import annotations

import json
from pathlib import Path

from api import routes
from api.routes import ChatProxyRequest
from core.database import ChatLog, ConversationTurn, SensitiveData


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _make_req(**updates) -> ChatProxyRequest:
    data = {
        "user_id": "u-persist",
        "session_id": "private_u-persist",
        "query": "原始消息",
        "sender_name": "用户",
        "session_name": "私聊",
        "client_meta": {"platform": "qq", "trace": {"request_id": "req-1"}},
    }
    data.update(updates)
    return ChatProxyRequest(**data)


def _rows(db_session, model, **filters):
    return db_session.query(model).filter_by(**filters).all()


def test_chat_persistence_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_persistence.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_parent_persistence_wrappers_keep_api_routes_module():
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"


def test_safe_meta_facade_matches_chat_persistence_module():
    from api import chat_persistence

    assert routes._safe_meta('{"a": 1}') == {"a": 1}
    assert routes._safe_meta("[]") == {}
    assert routes._safe_meta("{bad") == {}
    assert routes._safe_meta('{"a": 1}') == chat_persistence.safe_meta('{"a": 1}')


def test_persist_chat_turn_silent_masks_logs_and_saves_sensitive_data(db_session):
    req = _make_req(
        query="敏感原文",
        files=["https://example.com/a.png"],
        user_id="u-silent",
        session_id="private_u-silent",
    )

    pending = routes._persist_chat_turn(
        db_session,
        req,
        "（数据中转，自动静默）",
        guardrail_status="silent",
    )

    assert pending == 2
    user_log = db_session.query(ChatLog).filter_by(user_id="u-silent", role="user").one()
    user_turn = db_session.query(ConversationTurn).filter_by(user_id="u-silent", role="user").one()
    sensitive = db_session.query(SensitiveData).filter_by(user_id="u-silent").one()
    assert user_log.content == "[敏感数据]"
    assert user_turn.content == "[敏感数据]"
    assert "敏感原文" in sensitive.content
    assert "https://example.com/a.png" in sensitive.content


def test_persist_chat_turn_injection_uses_safe_prompt_and_processed_minus_one(db_session):
    req = _make_req(user_id="u-injection", session_id="private_u-injection")

    pending = routes._persist_chat_turn(
        db_session,
        req,
        "拒绝注入",
        guardrail_status="injection",
    )

    assert pending == 0
    logs = _rows(db_session, ChatLog, user_id="u-injection")
    turns = _rows(db_session, ConversationTurn, user_id="u-injection")
    assert len(logs) == 2
    assert len(turns) == 2
    assert {log.processed for log in logs} == {-1}
    assert logs[0].content == "[安全提示: 检测到注入已被拦截]"
    assert turns[0].content == "[安全提示: 检测到注入已被拦截]"
    assert db_session.query(SensitiveData).filter_by(user_id="u-injection").count() == 0


def test_persist_chat_turn_html_answer_full_archive_summary_context(db_session):
    html = "<!doctype html><html><body>完整报告</body></html>"
    req = _make_req(user_id="u-html", session_id="private_u-html")

    routes._persist_chat_turn(db_session, req, html)

    assistant_log = db_session.query(ChatLog).filter_by(user_id="u-html", role="assistant").one()
    assistant_turn = db_session.query(ConversationTurn).filter_by(
        user_id="u-html",
        role="assistant",
    ).one()
    turn_meta = json.loads(assistant_turn.meta_json or "{}")
    assert assistant_log.content == html
    assert assistant_turn.content == f"[HTML报告: 已渲染为图片/HTML，{len(html)}字符]"
    assert turn_meta["kind"] == "artifact_summary"


def test_persist_chat_turn_source_ids_prepend_message_id_without_duplicate(db_session):
    req = _make_req(
        user_id="u-source",
        session_id="private_u-source",
        message_id="m1",
        source_message_ids=["m2", "m1"],
    )

    routes._persist_chat_turn(db_session, req, "已处理")

    user_log = db_session.query(ChatLog).filter_by(user_id="u-source", role="user").one()
    user_turn = db_session.query(ConversationTurn).filter_by(user_id="u-source", role="user").one()
    assert json.loads(user_log.source_message_ids_json) == ["m2", "m1"]
    assert json.loads(user_turn.source_message_ids_json) == ["m2", "m1"]


def test_persist_chat_turn_prompt_audit_meta_marks_assistant_processed(db_session):
    req = _make_req(user_id="u-audit", session_id="private_u-audit")

    routes._persist_chat_turn(
        db_session,
        req,
        "（无回复内容）",
        assistant_meta={
            "kind": "empty_reply",
            "no_context": True,
            "no_send": True,
            "agent_result": "prompt_v2_audit_failed",
        },
        assistant_processed=1,
    )

    assistant_log = db_session.query(ChatLog).filter_by(user_id="u-audit", role="assistant").one()
    assistant_turn = db_session.query(ConversationTurn).filter_by(
        user_id="u-audit",
        role="assistant",
    ).one()
    assistant_meta = json.loads(assistant_turn.meta_json or "{}")
    assert assistant_log.processed == 1
    assert assistant_meta["kind"] == "empty_reply"
    assert assistant_meta["no_context"] is True
    assert assistant_meta["no_send"] is True
    assert assistant_meta["agent_result"] == "prompt_v2_audit_failed"


def test_persist_chat_turn_timing_gate_written_to_all_expected_meta(db_session):
    req = _make_req(user_id="u-timing", session_id="private_u-timing")
    timing_meta = {
        "mode": "private",
        "action": "reply_now",
        "scoring": {"stage": "unit", "action": "continue"},
    }

    routes._persist_chat_turn(db_session, req, "计时回复", timing_meta=timing_meta)

    logs = _rows(db_session, ChatLog, user_id="u-timing")
    turns = _rows(db_session, ConversationTurn, user_id="u-timing")
    for row in [*logs, *turns]:
        meta = json.loads(row.meta_json or "{}")
        assert meta["timing_gate"] == timing_meta


def test_persist_chat_turn_pending_count_returns_zero_when_evolution_running(db_session):
    from core.evolution import _evolution_running

    req = _make_req(user_id="u-running", session_id="private_u-running")
    _evolution_running.add("u-running")
    try:
        pending = routes._persist_chat_turn(db_session, req, "正在进化时的回复")
    finally:
        _evolution_running.discard("u-running")

    assert pending == 0
    assert db_session.query(ChatLog).filter_by(user_id="u-running").count() == 2
    assert db_session.query(ConversationTurn).filter_by(user_id="u-running").count() == 2
```

- [x] **步骤 2：扩展相邻 split 测试源码扫描**

在这些文件的源码禁用模式测试中加入 `api/chat_persistence.py`：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

示例：

```python
for path in (
    "api/chat_content_helpers.py",
    "api/chat_response_contract.py",
    "api/chat_persistence.py",
):
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
  tests/test_api_chat_persistence_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
```

预期：FAIL。失败点应集中在 `api/chat_persistence.py` 文件不存在、`api.chat_persistence`
无法导入或相邻 split 测试无法读取新模块源码。

- [x] **步骤 4：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_persistence_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定聊天落库拆分契约"
```

## 任务 2：实现聊天落库模块与父模块 wrapper

**文件：**

- 创建：`api/chat_persistence.py`
- 修改：`api/routes.py`

- [x] **步骤 1：创建 `api/chat_persistence.py`**

创建文件并放入完整 writer 实现：

```python
"""聊天落库 writer。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from api import chat_content_helpers
from core.database import ChatLog, ConversationTurn, SensitiveData
from core.sqlite_retry import run_sqlite_locked_retry


logger = logging.getLogger("nanobot.api")


@dataclass(frozen=True)
class ChatTurnPersistenceInput:
    user_id: str
    session_id: str
    query: str
    files: list[str] | None = None
    sender_name: str | None = None
    session_name: str | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict[str, Any] | None = None


def safe_meta(meta_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _source_message_ids_json(req: ChatTurnPersistenceInput) -> str:
    source_ids = list(req.source_message_ids or [])
    if req.message_id and req.message_id not in source_ids:
        source_ids.insert(0, req.message_id)
    return json.dumps(source_ids, ensure_ascii=False) if source_ids else "[]"


def _turn_answer(answer: str, guardrail_status: str | None) -> tuple[str, str]:
    turn_answer = answer
    turn_answer_kind = "casual_template" if guardrail_status == "casual_template" else "chat"
    if answer:
        answer_lower = answer.lstrip()[:500].lower()
        html_markers = ("<!doctype", "<html", "<head", "<body", "<article", "<style")
        if any(answer_lower.startswith(marker) for marker in html_markers):
            turn_answer = f"[HTML报告: 已渲染为图片/HTML，{len(answer)}字符]"
            turn_answer_kind = "artifact_summary"
        elif len(answer) > 2000:
            turn_answer = answer[:2000] + "\n...[截断]"
    return turn_answer, turn_answer_kind


def persist_chat_turn(
    db: Session,
    req: ChatTurnPersistenceInput,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict[str, Any] | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict[str, Any] | None = None,
) -> int:
    is_injection = guardrail_status == "injection"
    is_silent = guardrail_status == "silent"
    processed_val = -1 if is_injection else 0
    assistant_processed_val = processed_val if assistant_processed is None else int(assistant_processed)
    archive_user_content = chat_content_helpers.build_chatlog_user_content(req.query, req.files)
    context_user_content = chat_content_helpers.build_conversation_user_content(req.query, req.files)

    if is_silent:
        archive_display_content = "[敏感数据]"
        context_display_content = "[敏感数据]"
    else:
        archive_display_content = "[安全提示: 检测到注入已被拦截]" if is_injection else archive_user_content
        context_display_content = "[安全提示: 检测到注入已被拦截]" if is_injection else context_user_content

    source_ids_json = _source_message_ids_json(req)
    meta = json.dumps(req.client_meta or {}, ensure_ascii=False)
    turn_answer, turn_answer_kind = _turn_answer(answer, guardrail_status)

    user_meta = safe_meta(meta)
    user_meta["kind"] = "chat"
    if timing_meta:
        user_meta["timing_gate"] = timing_meta

    assistant_turn_meta: dict[str, Any] = {"kind": turn_answer_kind}
    if timing_meta:
        assistant_turn_meta["timing_gate"] = timing_meta
    if assistant_meta:
        assistant_turn_meta.update(assistant_meta)

    assistant_chat_meta = dict(assistant_meta or {})
    if timing_meta:
        assistant_chat_meta["timing_gate"] = timing_meta

    def operation() -> None:
        if is_silent:
            db.add(SensitiveData(
                user_id=req.user_id,
                session_id=req.session_id,
                content=archive_user_content,
                guardrail_status="silent",
                sender_name=req.sender_name or "",
                session_name=req.session_name or "",
            ))
        db.add(ChatLog(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=archive_display_content,
            sender_name=req.sender_name or "",
            session_name=req.session_name or "",
            processed=processed_val,
            message_id=req.message_id,
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps(user_meta, ensure_ascii=False),
        ))
        db.add(ChatLog(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=answer,
            sender_name="nanobot",
            session_name=req.session_name or "",
            processed=assistant_processed_val,
            meta_json=json.dumps(assistant_chat_meta, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="user",
            content=context_display_content,
            source_message_ids_json=source_ids_json,
            meta_json=json.dumps(user_meta, ensure_ascii=False),
        ))
        db.add(ConversationTurn(
            user_id=req.user_id,
            session_id=req.session_id,
            role="assistant",
            content=turn_answer,
            meta_json=json.dumps(assistant_turn_meta, ensure_ascii=False),
        ))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="chat_turn_persist",
        logger=logger,
    )

    from core.evolution import _evolution_running

    if req.user_id in _evolution_running:
        return 0
    return db.query(ChatLog).filter(ChatLog.user_id == req.user_id, ChatLog.processed == 0).count()
```

- [x] **步骤 2：修改 `api/routes.py` 导入**

在现有 helper 导入附近加入：

```python
from api import chat_content_helpers, chat_persistence, chat_response_contract
```

如果当前已有：

```python
from api import chat_content_helpers, chat_response_contract
```

替换为包含 `chat_persistence` 的导入。

- [x] **步骤 3：替换 `_safe_meta()` 实现**

将父模块 `_safe_meta()` 改为 wrapper：

```python
def _safe_meta(meta_json: str) -> dict:
    return chat_persistence.safe_meta(meta_json)
```

- [x] **步骤 4：替换 `_persist_chat_turn()` 实现**

保留函数签名，将函数体改为适配 `ChatTurnPersistenceInput`：

```python
def _persist_chat_turn(
    db: Session,
    req: ChatProxyRequest,
    answer: str,
    guardrail_status: str | None = None,
    *,
    assistant_meta: dict | None = None,
    assistant_processed: int | None = None,
    timing_meta: dict | None = None,
) -> int:
    """Persist a chat turn to both ChatLog (evolution) and ConversationTurn (context)."""
    return chat_persistence.persist_chat_turn(
        db,
        chat_persistence.ChatTurnPersistenceInput(
            user_id=req.user_id,
            session_id=req.session_id,
            query=req.query,
            files=req.files,
            sender_name=req.sender_name,
            session_name=req.session_name,
            message_id=req.message_id,
            source_message_ids=req.source_message_ids,
            client_meta=req.client_meta,
        ),
        answer,
        guardrail_status,
        assistant_meta=assistant_meta,
        assistant_processed=assistant_processed,
        timing_meta=timing_meta,
    )
```

不要修改 `proxy_chat()` 的 `_persist_chat_turn(...)` 调用点。

- [x] **步骤 5：运行新增测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_chat_persistence_split.py
```

预期：PASS，新增 split 测试全部通过。

- [x] **步骤 6：运行相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_chat_persistence_split.py \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_tracing_sqlite_retry.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。`tests/test_tracing_sqlite_retry.py::test_chat_turn_persist_retries_sqlite_locked_commit`
必须继续覆盖 `chat_turn_persist` 的 SQLite locked retry。

- [x] **步骤 7：运行 `/chat` 行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  tests/test_api.py::test_private_prompt_v2_audit_failure_is_not_context_chat \
  tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
  tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
  tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages \
  tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request
```

预期：PASS。该组验证父模块 `_persist_chat_turn()` spy、stream 后台落库、audit failure、
timing meta 和 buffered request 仍保持原语义。

- [x] **步骤 8：运行静态检查**

运行：

```bash
python -B -m py_compile api/routes.py api/chat_persistence.py tests/test_api_chat_persistence_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/chat_persistence.py
git diff --check -- \
  api/routes.py \
  api/chat_persistence.py \
  tests/test_api_chat_persistence_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
```

预期：`py_compile` 成功；`rg` 无匹配，退出码为 1；`git diff --check` 无输出。

- [x] **步骤 9：提交实现**

运行：

```bash
git add api/routes.py \
  api/chat_persistence.py \
  tests/test_api_chat_persistence_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分聊天落库 writer"
```

## 任务 3：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-chat-persistence-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新本计划执行状态**

在本文件「当前状态」中追加提交哈希和验证结果，勾选已完成步骤。

- [x] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」的 `api/routes.py` 进展列表中追加聊天落库拆分记录，内容包含：

- 新模块 `api/chat_persistence.py`。
- 父模块 `_persist_chat_turn()` / `_safe_meta()` wrapper 保留。
- `/chat`、`proxy_chat()`、私聊缓冲、streaming runner、Prompt Runtime 输入组装和 `/health` 仍留父模块。
- 新增测试覆盖 silent / injection / HTML / audit failure / timing meta / source ids / pending count。
- `api/routes.py` 行数变化和验证结果。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-22 普通 API 聊天落库拆分` 小节，记录：

- 阶段目标和边界。
- 设计、计划、红灯测试、实现和文档收口提交。
- 计划列表勾选。
- 红灯、绿灯、相邻回归、`/chat` 回归、静态检查和全量回归结果。
- 仍不拆 `/chat` 路由本体、私聊缓冲、streaming runner 和 `/health`。

- [x] **步骤 4：运行文档检查与全量回归**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\\x{FFFD}' \
  .Codex/plans/api-chat-persistence-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
git diff --check -- \
  .Codex/plans/api-chat-persistence-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：红旗词扫描无输出；`git diff --check` 无输出；全量测试 PASS。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-persistence-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天落库拆分"
```

## 最终验收清单

- [x] `api/chat_persistence.py` 承载落库实现，且不反向导入父模块。
- [x] `api.routes._persist_chat_turn()` 和 `_safe_meta()` 仍是父模块 wrapper。
- [x] `proxy_chat()` 的调用点仍走父模块 wrapper。
- [x] silent / injection / HTML / audit failure / timing meta / source ids / pending count 契约均有测试。
- [x] SQLite locked retry 仍使用 `chat_turn_persist` label。
- [x] 新模块没有 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- [x] 不改变 Prompt Runtime 模板、`enriched_query`、conversation 结构或工具输出契约。
- [x] 定向回归、相邻回归、`/chat` 回归、静态检查和全量回归均通过。
