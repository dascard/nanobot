# 普通 API 聊天请求契约拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 `ChatProxyRequest` 与聊天请求元信息 helper 拆到 `api/chat_request_contract.py`，保留父模块导入面、wrapper `__module__`、`/chat` 路由本体和 monkeypatch 合同。

**架构：** 新模块 `api/chat_request_contract.py` 承载请求模型、clone、target / group 解析、platform / chat type 判定、client meta 归一化和私聊辅助 meta。`api.routes` 继续暴露 `ChatProxyRequest` 与 8 个旧下划线 helper wrapper，`proxy_chat()` 继续调用父模块名称，避免破坏已有 spy / monkeypatch 路径。

**技术栈：** Python 3.13、FastAPI、Pydantic、pytest、现有 `core.client_meta`、普通 API split 测试模板。

---

## 当前状态

- [x] 设计文档已提交：`docs/superpowers/specs/2026-06-22-api-chat-request-contract-split-design.md`。
- [x] 设计提交：`858ad07 docs(普通API): 设计聊天请求契约拆分`。
- [x] 计划提交：`eb8d120 docs(计划): 记录聊天请求契约拆分计划`。
- [x] 红灯测试提交：`fd0bb4b test(普通API): 锁定聊天请求契约拆分`。
- [x] 实现提交：`fd311d6 refactor(普通API): 拆分聊天请求契约`。
- [x] 文档收口提交：随本次 `docs(计划): 收口聊天请求契约拆分` 完成。

当前 `api/routes.py` 为 1468 行，剩余显式 route 为 `/chat` 与 `/health`。
本阶段只迁移请求契约和纯 helper，不迁移私聊缓冲、streaming finalizer、Prompt Runtime 输入组装、
KT bridge 调用、数据库落库或 response envelope。

本阶段不得新增：

- `asyncio.run()`
- `run_awaitable_sync`
- 同步函数包装 awaitable
- `api/chat_request_contract.py` 对 `api.routes` 的反向导入

## 文件职责

- 创建：`tests/test_api_chat_request_contract_split.py`
  - 锁定新模块禁止反向导入父模块、父模块 wrapper、请求字段默认值、clone 行为、
    target / group 解析、platform / chat type、client meta 归一化和私聊辅助 meta。
- 创建：`api/chat_request_contract.py`
  - 承载 `ChatProxyRequest`、`clone_chat_request()`、`resolve_push_target_id()`、
    `extract_group_id_from_chat_request()`、`chat_request_platform()`、
    `chat_request_type()`、`normalize_request_client_meta()`、
    `private_prompt_audit_failure_meta()` 和 `private_timing_meta()`。
- 修改：`api/routes.py`
  - 导入 `api.chat_request_contract`。
  - 将 `ChatProxyRequest` 改为新模块 re-export。
  - 将 8 个请求 helper 改为父模块 wrapper。
  - 移除父模块不再需要的 `BaseModel`、`ClientMetaValidationError`、
    `normalize_client_meta` 导入。
- 修改：`tests/test_api_history_log_routes_split.py`
  - 把 `api/chat_request_contract.py` 加入聊天 split 模块源码扫描。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 把 `api/chat_request_contract.py` 加入聊天 split 模块源码扫描。
- 修改：`tests/test_api_group_message_routes_split.py`
  - 把 `api/chat_request_contract.py` 加入聊天 split 模块源码扫描。
- 修改：`tests/test_api_sticker_media_routes_split.py`
  - 把 `api/chat_request_contract.py` 加入聊天 split 模块源码扫描。
- 修改：`.Codex/plans/api-chat-request-contract-split.md`
  - 每个阶段完成后勾选执行记录和验收结果。
- 修改：`docs/todo.md`
  - 收口时记录 P3 普通 API 聊天请求契约拆分进展。
- 修改：`docs/plan_walkthrough.md`
  - 收口时追加 2026-06-22 聊天请求契约拆分阶段记录。

## 并行策略

本阶段主要修改 `api/routes.py` 和相邻 split 测试，写入文件存在共享边界，不适合多个写入 agent 同时修改。
如需加速，只把只读审查委派给子 agent：

- Agent A：只读审查 `tests/test_api_chat_request_contract_split.py` 是否覆盖设计文档。
- Agent B：只读审查 `api/routes.py` wrapper 是否仍保持父模块 monkeypatch 入口。
- Agent C：只读审查源码扫描测试是否覆盖所有聊天 split 模块。

主线程负责最终编辑、运行验证和提交。

## 任务 1：补聊天请求契约 split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_chat_request_contract_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：创建新增 split 测试文件**

创建 `tests/test_api_chat_request_contract_split.py`：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import routes
from api.routes import ChatProxyRequest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_request_contract_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_request_contract.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_chat_proxy_request_defaults_stay_compatible():
    req = ChatProxyRequest()

    assert req.user_id == "default_user"
    assert req.session_id == "default_session"
    assert req.query == ""
    assert req.files is None
    assert req.sender_name is None
    assert req.session_name is None
    assert req.stream is False
    assert req.classification_request is False
    assert req.merged_messages is None
    assert req.message_id is None
    assert req.source_message_ids is None
    assert req.client_meta is None


def test_parent_request_contract_wrappers_keep_api_routes_module():
    assert routes._clone_chat_request.__module__ == "api.routes"
    assert routes._resolve_push_target_id.__module__ == "api.routes"
    assert routes._extract_group_id_from_chat_request.__module__ == "api.routes"
    assert routes._chat_request_platform.__module__ == "api.routes"
    assert routes._chat_request_type.__module__ == "api.routes"
    assert routes._normalize_request_client_meta.__module__ == "api.routes"
    assert routes._private_prompt_audit_failure_meta.__module__ == "api.routes"
    assert routes._private_timing_meta.__module__ == "api.routes"


def test_clone_chat_request_preserves_all_request_contract_fields():
    req = ChatProxyRequest(
        user_id="u1",
        session_id="private_u1",
        query="原文",
        files=["img://a"],
        sender_name="用户",
        session_name="私聊",
        stream=True,
        classification_request=True,
        merged_messages=["a", "b"],
        message_id="m1",
        source_message_ids=["m0"],
        client_meta={"platform": "qq", "chat_type": "private"},
    )

    cloned = routes._clone_chat_request(req, query="合并后", files=["img://b"])

    assert isinstance(cloned, ChatProxyRequest)
    assert cloned is not req
    assert cloned.user_id == "u1"
    assert cloned.session_id == "private_u1"
    assert cloned.query == "合并后"
    assert cloned.files == ["img://b"]
    assert cloned.sender_name == "用户"
    assert cloned.session_name == "私聊"
    assert cloned.stream is True
    assert cloned.classification_request is True
    assert cloned.merged_messages == ["a", "b"]
    assert cloned.message_id == "m1"
    assert cloned.source_message_ids == ["m0"]
    assert cloned.client_meta == {"platform": "qq", "chat_type": "private"}


@pytest.mark.parametrize(
    ("req", "is_group", "expected"),
    [
        (ChatProxyRequest(user_id="u-private", session_id="private_u-private"), False, "u-private"),
        (ChatProxyRequest(user_id="u1", session_id="group_987654"), True, "987654"),
        (ChatProxyRequest(user_id="u1", session_id="987654"), True, "987654"),
        (ChatProxyRequest(user_id="u-fallback", session_id=""), True, "u-fallback"),
    ],
)
def test_resolve_push_target_id_keeps_private_and_group_contract(req, is_group, expected):
    assert routes._resolve_push_target_id(req, is_group) == expected


@pytest.mark.parametrize(
    ("req", "expected"),
    [
        (ChatProxyRequest(user_id="u1", session_id="group_987654"), "987654"),
        (ChatProxyRequest(user_id="u1", session_id="987654"), "987654"),
        (ChatProxyRequest(user_id="u-fallback", session_id=""), "u-fallback"),
    ],
)
def test_extract_group_id_from_chat_request_keeps_fallback_contract(req, expected):
    assert routes._extract_group_id_from_chat_request(req) == expected


@pytest.mark.parametrize(
    ("client_meta", "expected"),
    [
        (None, "qq"),
        ({}, "qq"),
        ({"platform": " QQ "}, "qq"),
        ({"platform": "Web"}, "web"),
        ("bad-meta", "qq"),
        ({"platform": "   "}, "qq"),
    ],
)
def test_chat_request_platform_defaults_and_normalizes(client_meta, expected):
    req = ChatProxyRequest(client_meta=client_meta)

    assert routes._chat_request_platform(req) == expected


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("private_u1", "private"),
        ("group_123", "group"),
        ("123", "group"),
        ("", "group"),
    ],
)
def test_chat_request_type_uses_private_prefix_only(session_id, expected):
    assert routes._chat_request_type(ChatProxyRequest(session_id=session_id)) == expected


def test_normalize_request_client_meta_writes_normalized_meta():
    req = ChatProxyRequest(
        user_id="u1",
        session_id="private_u1",
        client_meta={"platform": " QQ ", "chat_type": "private"},
    )

    normalized = routes._normalize_request_client_meta(req, expected_chat_type="private")

    assert normalized["platform"] == "qq"
    assert normalized["chat_type"] == "private"
    assert req.client_meta is normalized


def test_normalize_request_client_meta_maps_validation_error_to_http_400():
    req = ChatProxyRequest(client_meta={"chat_type": "group"})

    with pytest.raises(HTTPException) as exc_info:
        routes._normalize_request_client_meta(req, expected_chat_type="private")

    assert exc_info.value.status_code == 400
    assert "invalid client_meta" in str(exc_info.value.detail)


def test_private_prompt_audit_failure_meta_stays_exact():
    assert routes._private_prompt_audit_failure_meta() == {
        "kind": "empty_reply",
        "no_context": True,
        "no_send": True,
        "agent_result": "prompt_v2_audit_failed",
    }


def test_private_timing_meta_returns_none_for_missing_or_invalid_scoring():
    assert routes._private_timing_meta(None) is None
    assert routes._private_timing_meta(SimpleNamespace(timing_scoring=None)) is None
    assert routes._private_timing_meta(SimpleNamespace(timing_scoring="bad")) is None


def test_private_timing_meta_extracts_expected_fields():
    decision = SimpleNamespace(
        action="reply_now",
        reason="窗口结束",
        effort="normal",
        runtime_preset="fast",
        timing_scoring={"score": 0.8},
    )

    assert routes._private_timing_meta(decision) == {
        "mode": "private",
        "action": "reply_now",
        "reason": "窗口结束",
        "effort": "normal",
        "runtime_preset": "fast",
        "scoring": {"score": 0.8},
    }
```

- [x] **步骤 2：扩展相邻 split 测试的源码扫描**

在以下 4 个文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 路径元组中追加
`"api/chat_request_contract.py"`：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

修改后的元组应包含：

```python
for path in (
    "api/chat_content_helpers.py",
    "api/chat_response_contract.py",
    "api/chat_persistence.py",
    "api/chat_request_contract.py",
):
    source = Path(path).read_text(encoding="utf-8")
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_request_contract_split.py -v
```

预期：失败，至少包含 `FileNotFoundError: ... api/chat_request_contract.py`，因为实现模块尚未创建。

- [x] **步骤 4：运行相邻扫描红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：失败，原因同样是 `api/chat_request_contract.py` 尚未创建。

- [x] **步骤 5：红灯测试提交**

确认暂存区只包含测试文件：

```bash
git add tests/test_api_chat_request_contract_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定聊天请求契约拆分"
```

验收结果：

- 新增 split 红灯：`1 failed, 25 passed, 1 warning in 6.36s`，失败点为
  `FileNotFoundError: api/chat_request_contract.py`。
- 相邻扫描红灯：`4 failed, 1 warning in 6.50s`，4 个失败均为新模块尚未创建。
- 红灯测试提交：`fd0bb4b test(普通API): 锁定聊天请求契约拆分`。

## 任务 2：实现请求契约模块并提交

**文件：**

- 创建：`api/chat_request_contract.py`
- 修改：`api/routes.py`

- [x] **步骤 1：新增 `api/chat_request_contract.py`**

创建文件：

```python
"""聊天请求契约与请求元信息 helper。"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from core.client_meta import ClientMetaValidationError, normalize_client_meta


class ChatProxyRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str = "default_session"
    query: str = ""
    files: Optional[List[str]] = None
    sender_name: Optional[str] = None
    session_name: Optional[str] = None
    stream: bool = False
    classification_request: bool = False
    merged_messages: list[str] | None = None
    message_id: str | None = None
    source_message_ids: list[str] | None = None
    client_meta: dict | None = None


def clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    if hasattr(req, "model_dump"):
        data = req.model_dump()
    else:
        data = req.dict()
    data.update(updates)
    return ChatProxyRequest(**data)


def resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    if not is_group:
        return req.user_id
    session_id = str(req.session_id or "")
    if session_id.startswith("group_"):
        return session_id[len("group_"):]
    return session_id or req.user_id


def extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    session_id = str(req.session_id or "").strip()
    if session_id.startswith("group_"):
        return session_id[len("group_"):]
    return session_id or str(req.user_id or "").strip()


def chat_request_platform(req: ChatProxyRequest) -> str:
    client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
    return str(client_meta.get("platform") or "qq").strip().lower() or "qq"


def chat_request_type(req: ChatProxyRequest) -> str:
    return "private" if str(req.session_id).startswith("private_") else "group"


def normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized


def private_prompt_audit_failure_meta() -> dict[str, Any]:
    return {
        "kind": "empty_reply",
        "no_context": True,
        "no_send": True,
        "agent_result": "prompt_v2_audit_failed",
    }


def private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    scoring = getattr(decision, "timing_scoring", None)
    if not isinstance(scoring, dict):
        return None
    return {
        "mode": "private",
        "action": str(getattr(decision, "action", "") or ""),
        "reason": str(getattr(decision, "reason", "") or ""),
        "effort": str(getattr(decision, "effort", "") or ""),
        "runtime_preset": str(getattr(decision, "runtime_preset", "") or ""),
        "scoring": scoring,
    }
```

- [x] **步骤 2：修改 `api/routes.py` 导入**

将：

```python
from pydantic import BaseModel
```

删除。

将：

```python
from core.client_meta import (
    ClientMetaValidationError,
    normalize_client_meta,
)
```

删除。

将：

```python
from api import chat_content_helpers, chat_persistence, chat_response_contract
```

替换为：

```python
from api import (
    chat_content_helpers,
    chat_persistence,
    chat_request_contract,
    chat_response_contract,
)
```

- [x] **步骤 3：修改 `api/routes.py` 请求模型与 helper**

将父模块中的 `class ChatProxyRequest(BaseModel): ...` 替换为：

```python
ChatProxyRequest = chat_request_contract.ChatProxyRequest
```

将 `_clone_chat_request()` 实现替换为：

```python
def _clone_chat_request(req: ChatProxyRequest, **updates) -> ChatProxyRequest:
    return chat_request_contract.clone_chat_request(req, **updates)
```

将 `_resolve_push_target_id()` 实现替换为：

```python
def _resolve_push_target_id(req: ChatProxyRequest, is_group: bool) -> str:
    return chat_request_contract.resolve_push_target_id(req, is_group)
```

将 `_extract_group_id_from_chat_request()` 实现替换为：

```python
def _extract_group_id_from_chat_request(req: ChatProxyRequest) -> str:
    return chat_request_contract.extract_group_id_from_chat_request(req)
```

将 `_chat_request_platform()` 实现替换为：

```python
def _chat_request_platform(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_platform(req)
```

将 `_chat_request_type()` 实现替换为：

```python
def _chat_request_type(req: ChatProxyRequest) -> str:
    return chat_request_contract.chat_request_type(req)
```

将 `_normalize_request_client_meta()` 实现替换为：

```python
def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    return chat_request_contract.normalize_request_client_meta(
        req,
        expected_chat_type=expected_chat_type,
    )
```

将 `_private_prompt_audit_failure_meta()` 实现替换为：

```python
def _private_prompt_audit_failure_meta() -> dict:
    return chat_request_contract.private_prompt_audit_failure_meta()
```

将 `_private_timing_meta()` 实现替换为：

```python
def _private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    return chat_request_contract.private_timing_meta(decision)
```

- [x] **步骤 4：运行定向绿灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_request_contract_split.py -v
```

预期：全部通过。

- [x] **步骤 5：运行相邻扫描绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：全部通过。

- [x] **步骤 6：运行 `/chat` 相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_helpers_split.py \
  tests/test_api_chat_persistence_split.py \
  -v
```

预期：全部通过。

- [x] **步骤 7：实现提交**

确认暂存区只包含实现相关文件：

```bash
git add api/chat_request_contract.py api/routes.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分聊天请求契约"
```

验收结果：

- 请求契约 split 绿灯：`26 passed, 1 warning in 0.94s`。
- 相邻扫描绿灯：`4 passed, 1 warning in 1.01s`。
- `/chat` helper / persistence 相邻回归：`18 passed, 1 warning in 1.62s`。
- 实现提交：`fd311d6 refactor(普通API): 拆分聊天请求契约`。

## 任务 3：全量验证与文档收口

**文件：**

- 修改：`.Codex/plans/api-chat-request-contract-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 2：更新执行计划状态**

在本文件「当前状态」中填写提交哈希，并把已完成任务的复选框改为 `[x]`。
在对应任务末尾追加实际验证结果，例如：

```markdown
验收结果：`tests/test_api_chat_request_contract_split.py` 全部通过；全量回归
`1673 passed, 6 skipped, ... warnings in ...s`。
```

- [x] **步骤 3：更新 `docs/todo.md`**

在 P3 普通 API 超大文件拆分记录中补一条聊天请求契约拆分进展，说明：

```markdown
- 2026-06-22：继续拆分 `api/routes.py`，新增 `api/chat_request_contract.py`，
  父模块保留 `ChatProxyRequest` 与请求 helper wrapper，`/chat` 路由本体未迁移。
```

- [x] **步骤 4：更新 `docs/plan_walkthrough.md`**

追加 2026-06-22 阶段记录，包含：

```markdown
## 2026-06-22：普通 API 聊天请求契约拆分

- 已完成：新增 `api/chat_request_contract.py`，拆出 `ChatProxyRequest` 与请求元信息 helper。
- 兼容合同：`api.routes` 保留 `ChatProxyRequest` 和 8 个父模块 wrapper，`proxy_chat()` 仍走父模块名称。
- 验证：记录定向测试、相邻回归和全量回归结果。
- 下一步：评估 runtime / guardrail facade 或私聊缓冲状态机，但先补齐对应状态机测试。
```

- [x] **步骤 5：运行文档红旗扫描与空白检查**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' \
  .Codex/plans/api-chat-request-contract-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
git diff --check -- \
  .Codex/plans/api-chat-request-contract-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
```

预期：`rg` 无匹配，`git diff --check` 退出码为 0。

- [x] **步骤 6：文档收口提交**

确认暂存区只包含收口文档：

```bash
git add .Codex/plans/api-chat-request-contract-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天请求契约拆分"
```

验收结果：

- 全量回归：`1699 passed, 6 skipped, 139 warnings in 127.31s`。
- 行数检查：`api/routes.py` 1468 行，`api/chat_request_contract.py` 96 行，
  `tests/test_api_chat_request_contract_split.py` 196 行。

## 验收清单

- [x] `api/chat_request_contract.py` 不导入 `api.routes`。
- [x] `api/chat_request_contract.py` 不包含 `asyncio.run` 或 `run_awaitable_sync`。
- [x] `routes.ChatProxyRequest` 仍可导入，字段默认值保持不变。
- [x] 8 个父模块 wrapper 的 `__module__` 仍是 `"api.routes"`。
- [x] `_clone_chat_request()` 不丢 `client_meta`、`source_message_ids`、`merged_messages`、
  `message_id` 和 `stream`。
- [x] `_normalize_request_client_meta()` 成功时回写 `req.client_meta`，失败时抛 HTTP 400。
- [x] `_private_prompt_audit_failure_meta()` 输出精确保持。
- [x] `_private_timing_meta()` 保持 `None` / 非 dict scoring / dict scoring 三类行为。
- [x] `proxy_chat()`、`_stream_chat()`、私聊缓冲和落库逻辑未迁移。
- [x] 全量 `python -B -m pytest -p no:cacheprovider tests/ -v` 失败数为 0。
