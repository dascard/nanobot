# 普通 API Chat Persona Lookup 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes.proxy_chat()` 中的 persona snapshot lookup、JSON parse 和初始 persona formatter 编排拆到 `api/chat_persona_lookup.py`。

**架构：** 新模块只负责 persona user_id 候选生成、`Persona` 查询、JSON object parse、formatter callback 调用和结构化 snapshot 返回。父模块保留 `Persona` model、日志、动态 `PersonaInjectionService`、Prompt Runtime payload、Bridge、SSE、response 和落库边界。

**技术栈：** Python 3.12、dataclass、pytest、FastAPI TestClient、SQLAlchemy in-memory SQLite、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-24-api-chat-persona-lookup-split-design.md`
- [x] 设计提交：`021aed2 docs(普通API): 设计画像查找拆分`
- [x] 计划写入日期：2026-06-24

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：父模块导入 `Persona` model 并通过 wrapper 注入给新模块。
- 保留：父模块 `_format_persona_for_prompt()` 作为 formatter patch point。
- 保留：父模块记录 persona fallback / missing / lookup 日志。
- 保留：`PersonaInjectionService`、`_ctx_debug` 更新和动态 persona context 覆盖在父模块。
- 保留：`_build_chat_context()`、history 注入和 `release_clean_session_transaction()` 在父模块。
- 保留：`safe_user_input`、`enriched_query`、`bridge_meta` 和 Prompt Runtime payload 在父模块。
- 保留：Bridge、SSE、message envelope、push envelope、非流式结果收尾和 evolution 在父模块。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块导入 FastAPI、`APIRouter`、`StreamingResponse`、`BackgroundTasks` 或 `HTTPException`。
- 禁止：新模块导入 `get_bridge()`、Bridge handle、Prompt Runtime 或 `build_chat_runtime_payload()`。
- 禁止：新模块调用 `_persist_chat_turn()`、`_chat_response_payload()`、`db.commit()` 或 `release_clean_session_transaction()`。
- 禁止：改 conversation 结构、历史注入、Prompt Runtime 模板、message envelope、push envelope 或 response envelope。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_persona_lookup.py`
  - 定义 `ChatPersonaSnapshot`。
  - 实现 `iter_persona_user_id_candidates()`。
  - 实现 `resolve_chat_persona_snapshot()`。
  - 只通过入参接收 DB session、persona model 和 formatter callback。
- 修改：`api/routes.py`
  - 导入 `api.chat_persona_lookup`。
  - 新增 `_resolve_chat_persona_snapshot()` 薄 wrapper。
  - 用 wrapper 替换 `proxy_chat()` 中内联 `_find_persona()`、JSON parse 和 formatter 调用。
  - 保留父模块日志和后续 dynamic persona injection。
- 创建：`tests/test_api_chat_persona_lookup_split.py`
  - 锁定新模块源码边界。
  - 覆盖候选顺序、fallback lookup、非法 JSON、父模块 wrapper 和 HTTP fallback metadata。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 补齐既有 `api/chat_media_precache.py`、`api/chat_persona_context.py` 扫描遗漏，并将 `api/chat_persona_lookup.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-persona-lookup-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 最终收口时记录 P3 `api/routes.py` persona lookup 拆分进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_persona_lookup_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-persona-lookup-split.md`

- [x] **步骤 1：创建测试文件基础结构**

创建 `tests/test_api_chat_persona_lookup_split.py`，写入：

```python
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")
```

- [x] **步骤 2：新增 fake DB helper**

在同一文件追加：

```python
class FakeField:
    def __eq__(self, other: object) -> tuple[str, object]:
        return ("user_id", other)


class FakePersonaModel:
    user_id = FakeField()


class FakeQuery:
    def __init__(self, db: "FakeDb") -> None:
        self.db = db
        self.current_user_id = ""

    def filter(self, expression: tuple[str, object]) -> "FakeQuery":
        assert expression[0] == "user_id"
        self.current_user_id = str(expression[1])
        self.db.queries.append(self.current_user_id)
        return self

    def first(self) -> Any | None:
        value = self.db.rows.get(self.current_user_id)
        if value is None:
            return None
        return SimpleNamespace(user_id=self.current_user_id, persona_json=value)


class FakeDb:
    def __init__(self, rows: dict[str, str]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, model: Any) -> FakeQuery:
        assert model is FakePersonaModel
        return FakeQuery(self)
```

- [x] **步骤 3：新增源码边界红灯**

追加测试：

```python
def test_chat_persona_lookup_module_does_not_import_parent_routes_or_runtime_side_effects():
    path = ROOT / "api/chat_persona_lookup.py"
    assert path.exists()
    source = _source("api/chat_persona_lookup.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "FastAPI" not in source
    assert "APIRouter" not in source
    assert "StreamingResponse" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "get_bridge(" not in source
    assert "build_chat_runtime_payload" not in source
    assert "_persist_chat_turn" not in source
    assert "_chat_response_payload" not in source
    assert "db.commit(" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 4：新增候选顺序测试**

追加测试：

```python
def test_iter_persona_user_id_candidates_preserves_legacy_order_and_dedupes():
    from api.chat_persona_lookup import iter_persona_user_id_candidates

    assert iter_persona_user_id_candidates("u1") == ["u1", "private_u1", "group_u1"]
    assert iter_persona_user_id_candidates("private_u1") == ["private_u1", "group_private_u1", "u1"]
    assert iter_persona_user_id_candidates("group_u1") == ["group_u1", "private_group_u1", "u1"]
    assert iter_persona_user_id_candidates("") == ["", "private_", "group_"]
```

- [x] **步骤 5：新增 snapshot fallback 与 formatter 测试**

追加测试：

```python
def test_resolve_persona_snapshot_uses_first_matching_candidate_and_formatter():
    from api.chat_persona_lookup import resolve_chat_persona_snapshot

    formatter_calls: list[dict[str, Any]] = []

    def formatter(data: dict[str, Any]) -> str:
        formatter_calls.append(data)
        return f"persona:{data['persona_summary']}"

    db = FakeDb({"private_u1": json.dumps({"persona_summary": "fallback"}, ensure_ascii=False)})

    snapshot = resolve_chat_persona_snapshot(
        db,
        "u1",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )

    assert db.queries == ["u1", "private_u1"]
    assert snapshot.persona_obj is not None
    assert snapshot.lookup_user_id == "u1"
    assert snapshot.matched_user_id == "private_u1"
    assert snapshot.candidate_count == 3
    assert snapshot.persona_data == {"persona_summary": "fallback"}
    assert snapshot.persona_text == "persona:fallback"
    assert formatter_calls == [{"persona_summary": "fallback"}]
```

- [x] **步骤 6：新增 missing / invalid JSON 测试**

追加测试：

```python
def test_resolve_persona_snapshot_falls_back_to_empty_data_for_missing_or_invalid_json():
    from api.chat_persona_lookup import resolve_chat_persona_snapshot

    formatter_payloads: list[dict[str, Any]] = []

    def formatter(data: dict[str, Any]) -> str:
        formatter_payloads.append(data)
        return "empty" if not data else "unexpected"

    missing = resolve_chat_persona_snapshot(
        FakeDb({}),
        "missing",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )
    invalid = resolve_chat_persona_snapshot(
        FakeDb({"u-invalid": "not json"}),
        "u-invalid",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )
    array_value = resolve_chat_persona_snapshot(
        FakeDb({"u-array": "[1, 2, 3]"}),
        "u-array",
        persona_model=FakePersonaModel,
        format_persona=formatter,
    )

    assert missing.persona_obj is None
    assert missing.persona_json == "{}"
    assert missing.parse_failed is False
    assert invalid.persona_data == {}
    assert invalid.parse_failed is True
    assert array_value.persona_data == {}
    assert array_value.parse_failed is True
    assert formatter_payloads == [{}, {}, {}]
```

- [x] **步骤 7：新增父模块 wrapper 和 HTTP fallback 回归**

追加测试：

```python
def test_parent_persona_lookup_wrapper_remains_patchable(monkeypatch):
    from api import chat_persona_lookup
    from api import routes

    calls: list[tuple[Any, str, Any]] = []

    def fake_resolver(db, user_id, *, persona_model, format_persona):
        calls.append((db, user_id, persona_model))
        return chat_persona_lookup.ChatPersonaSnapshot(
            persona_obj=None,
            persona_json="{}",
            persona_data={},
            persona_text="patched",
            lookup_user_id=user_id,
            matched_user_id=None,
            candidate_count=1,
            parse_failed=False,
        )

    monkeypatch.setattr(chat_persona_lookup, "resolve_chat_persona_snapshot", fake_resolver)
    db = object()

    assert routes._resolve_chat_persona_snapshot.__module__ == "api.routes"
    snapshot = routes._resolve_chat_persona_snapshot(db, "u-patched")
    assert snapshot.persona_text == "patched"
    assert calls == [(db, "u-patched", routes.Persona)]


def test_proxy_chat_persona_fallback_still_reaches_bridge_metadata(client, db_session, monkeypatch):
    from core.database import Persona
    from unittest.mock import AsyncMock, patch

    db_session.add(
        Persona(
            user_id="private_persona-user",
            persona_json=json.dumps({"persona_summary": "fallback persona"}, ensure_ascii=False),
        )
    )
    db_session.commit()

    monkeypatch.setattr("api.routes._schedule_image_precache", lambda *args, **kwargs: None)

    mock_bridge = AsyncMock()
    mock_bridge.handle_message = AsyncMock(return_value="画像回复")

    with patch("api.routes.get_bridge", return_value=mock_bridge):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "persona-user",
                "session_id": "group_persona",
                "query": "你好",
                "client_meta": {"platform": "qq", "chat_type": "group"},
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_bridge.handle_message.await_args
    assert "fallback persona" in kwargs["metadata"]["persona_text"]
```

- [x] **步骤 8：更新 chat split 扫描清单**

在以下文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 清单中补齐 `"api/chat_media_precache.py"`、`"api/chat_persona_context.py"`，并追加 `"api/chat_persona_lookup.py"`：

```text
tests/test_api_group_message_routes_split.py
tests/test_api_agent_step_routes_split.py
tests/test_api_history_log_routes_split.py
tests/test_api_sticker_media_routes_split.py
```

- [x] **步骤 9：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_persona_lookup_split.py \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

预期：测试失败，失败原因是 `api/chat_persona_lookup.py` 不存在或父模块 wrapper 不存在。

实际：`9 failed, 1 passed, 21 warnings in 7.66s`。失败原因为 `api/chat_persona_lookup.py` 不存在、`api.chat_persona_lookup` 无法 import、父模块 `_resolve_chat_persona_snapshot` 尚不存在、四个 chat split module 扫描清单读取新模块失败；HTTP fallback 回归在旧内联实现下已通过。

- [x] **步骤 10：提交红灯测试**

```bash
git add tests/test_api_chat_persona_lookup_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py .Codex/plans/api-chat-persona-lookup-split.md
git commit -m "test(普通API): 锁定画像查找拆分契约"
```

说明：本步骤随红灯测试提交自身完成。

---

## 任务 2：新增 persona lookup helper

**文件：**
- 创建：`api/chat_persona_lookup.py`
- 修改：`.Codex/plans/api-chat-persona-lookup-split.md`

- [x] **步骤 1：新增模块与 dataclass**

创建 `api/chat_persona_lookup.py`：

```python
"""聊天 persona snapshot lookup helper。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatPersonaSnapshot:
    persona_obj: Any | None
    persona_json: str
    persona_data: dict[str, Any]
    persona_text: str
    lookup_user_id: str
    matched_user_id: str | None
    candidate_count: int
    parse_failed: bool
```

- [x] **步骤 2：实现候选生成**

追加：

```python
def iter_persona_user_id_candidates(uid: str) -> list[str]:
    candidates: list[str] = [uid]
    for prefix in ("private_", "group_"):
        if not uid.startswith(prefix):
            candidates.append(f"{prefix}{uid}")
    for prefix in ("private_", "group_"):
        if uid.startswith(prefix):
            candidates.append(uid[len(prefix):])
    return list(dict.fromkeys(candidates))
```

- [x] **步骤 3：实现 JSON object parse**

追加：

```python
def _parse_persona_json(value: str) -> tuple[dict[str, Any], bool]:
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data, False
```

- [x] **步骤 4：实现 resolver**

追加：

```python
def resolve_chat_persona_snapshot(
    db: Any,
    user_id: str,
    *,
    persona_model: Any,
    format_persona: Callable[[dict[str, Any]], str],
) -> ChatPersonaSnapshot:
    candidates = iter_persona_user_id_candidates(str(user_id or ""))
    persona_obj = None
    matched_user_id: str | None = None
    for candidate in candidates:
        persona_obj = db.query(persona_model).filter(persona_model.user_id == candidate).first()
        if persona_obj is not None:
            matched_user_id = candidate
            break

    persona_json = str(getattr(persona_obj, "persona_json", "{}") if persona_obj else "{}")
    persona_data, parse_failed = _parse_persona_json(persona_json)
    persona_text = format_persona(persona_data)
    return ChatPersonaSnapshot(
        persona_obj=persona_obj,
        persona_json=persona_json,
        persona_data=persona_data,
        persona_text=persona_text,
        lookup_user_id=str(user_id or ""),
        matched_user_id=matched_user_id,
        candidate_count=len(candidates),
        parse_failed=parse_failed,
    )
```

- [x] **步骤 5：运行 helper 定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_lookup_split.py -v
```

预期：helper 行为测试通过，父模块 wrapper / HTTP fallback 测试仍失败。

实际：`5 passed, 1 failed, 21 warnings in 6.94s`。唯一失败为父模块 `_resolve_chat_persona_snapshot` 尚未存在；HTTP fallback 回归仍由旧内联实现通过。

- [x] **步骤 6：提交 helper**

```bash
git add api/chat_persona_lookup.py .Codex/plans/api-chat-persona-lookup-split.md
git commit -m "refactor(普通API): 增加画像查找助手"
```

说明：本步骤随 helper 提交自身完成。

---

## 任务 3：接入父模块

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-persona-lookup-split.md`

- [x] **步骤 1：导入新模块**

在 `from api import (` 列表中加入：

```python
    chat_persona_lookup,
```

- [x] **步骤 2：新增父模块 wrapper**

在 `_format_persona_for_prompt()` 附近增加：

```python
def _resolve_chat_persona_snapshot(db: Session, user_id: str) -> chat_persona_lookup.ChatPersonaSnapshot:
    return chat_persona_lookup.resolve_chat_persona_snapshot(
        db,
        user_id,
        persona_model=Persona,
        format_persona=_format_persona_for_prompt,
    )
```

- [x] **步骤 3：替换 `proxy_chat()` 内联 persona lookup**

把内联 `_find_persona()`、`persona_json_str`、`json.loads()` 和 formatter 逻辑替换为：

```python
    persona_snapshot = _resolve_chat_persona_snapshot(db, req.user_id)
    persona_obj = persona_snapshot.persona_obj
    persona_json_str = persona_snapshot.persona_json
    persona_data = persona_snapshot.persona_data
    persona_text = persona_snapshot.persona_text
    if persona_snapshot.matched_user_id and persona_snapshot.matched_user_id != persona_snapshot.lookup_user_id:
        logger.info(
            "[/chat] Persona found via fallback: tried=%s, matched=%s",
            persona_snapshot.lookup_user_id,
            persona_snapshot.matched_user_id,
        )
    if persona_obj is None:
        logger.debug(
            "[/chat] No persona for user_id=%s (tried %s variants)",
            req.user_id,
            persona_snapshot.candidate_count,
        )
```

保留下方 `logger.info("[/chat] Persona lookup:")` 语义对应的总结日志，字段改为读取 `persona_snapshot` 结果。

- [x] **步骤 4：运行定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_lookup_split.py -v
```

预期：全部通过。

实际：`6 passed, 21 warnings in 1.39s`。

- [x] **步骤 5：提交父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-persona-lookup-split.md
git commit -m "refactor(普通API): 接入画像查找助手"
```

说明：本步骤随父模块接入提交自身完成。

---

## 任务 4：验证和文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-persona-lookup-split.md`

- [ ] **步骤 1：运行 split 扫描和相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_persona_context_split.py \
tests/test_api_chat_runtime_facade_split.py \
tests/test_api.py::test_proxy_chat_passes_history_header_to_bridge \
tests/test_api.py::test_proxy_chat_releases_db_transaction_before_bridge \
-v
```

预期：全部通过。

- [ ] **步骤 2：运行静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_persona_lookup.py -q
git diff --check -- \
api/routes.py \
api/chat_persona_lookup.py \
tests/test_api_chat_persona_lookup_split.py \
tests/test_api_group_message_routes_split.py \
tests/test_api_agent_step_routes_split.py \
tests/test_api_history_log_routes_split.py \
tests/test_api_sticker_media_routes_split.py \
.Codex/plans/api-chat-persona-lookup-split.md
wc -l api/routes.py api/chat_persona_lookup.py tests/test_api_chat_persona_lookup_split.py
```

预期：compileall 退出码 0；`git diff --check` 无输出；记录行数。

- [ ] **步骤 3：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 4：提交验证记录**

把任务 1 到任务 4 的实际命令输出摘要写回本计划，然后提交：

```bash
git add .Codex/plans/api-chat-persona-lookup-split.md
git commit -m "docs(计划): 记录画像查找拆分验证"
```

- [ ] **步骤 5：更新 `docs/todo.md`**

在 P3 超大文件拆分记录中追加本阶段结果：

```markdown
- 进展：`api/routes.py` 第二十七刀已拆出 Chat persona snapshot lookup 到 `api/chat_persona_lookup.py`；父模块继续保留日志、动态 `PersonaInjectionService`、Prompt Runtime payload、Bridge、SSE、response 和落库边界。
```

- [ ] **步骤 6：更新 `docs/plan_walkthrough.md`**

追加本阶段收口记录，包含：

```markdown
## 2026-06-24 普通 API Chat Persona Lookup 拆分

状态：设计、计划、红灯测试、helper 拆分、父模块接入、相邻回归、全量验证和阶段提交均已完成。
```

- [ ] **步骤 7：运行最终文档检查**

运行：

```bash
rg -n "TO""DO|待""定|后续""实现|补充""细节|\\x3c[^>]+\\x3e|\\.\\.\\." \
.Codex/plans/api-chat-persona-lookup-split.md \
docs/superpowers/specs/2026-06-24-api-chat-persona-lookup-split-design.md || true
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-persona-lookup-split.md
```

预期：计划和设计文档没有占位红旗；`git diff --check` 无输出。

- [ ] **步骤 8：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-persona-lookup-split.md
git commit -m "docs(计划): 收口画像查找拆分"
```

---

## 最终完成标准

- `api/chat_persona_lookup.py` 不导入父模块、FastAPI、Bridge 或 Prompt Runtime。
- `api/routes.py` 只保留 persona lookup wrapper、日志、dynamic persona injection 和 runtime payload 边界。
- persona user_id fallback 顺序与旧实现一致。
- persona JSON 非法时仍降级为空画像。
- 父模块 `_format_persona_for_prompt()` patch point 保持可用。
- `PersonaInjectionService` 覆盖 `persona_text` 的行为不变。
- `tests/test_api_chat_persona_lookup_split.py`、四个 chat split module 扫描测试、相邻回归和全量测试通过。
- `api/routes.py` 行数继续下降。
- 每个阶段性改动均已按文件精确暂存并 commit。
