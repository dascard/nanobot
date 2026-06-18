# client_meta 边界层校验实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 `/chat` 和 `/group/message` 增加 `client_meta` 轻量解析 / 校验，稳定 `platform`、`chat_type` 和 `trace.request_id`。

**架构：** 新增无 FastAPI 依赖的 `core/client_meta.py` 纯 helper，入口 route 捕获 helper 错误并转换为 HTTP 400。helper 只归一关键字段并保留其它扩展，避免破坏现有 QQbot `stickers`、`raw`、`business` 等兼容字段。

**技术栈：** Python 3.12、FastAPI、Pydantic、pytest、in-memory SQLite。

---

## 当前事实

- P2-2 响应信封已完成，最新文档收口提交为 `617aa25 docs(计划): 同步响应信封状态`。
- `docs/todo.md` 路线项 5 剩余尾项是 `client_meta` 边界层解析 / 校验。
- `api/routes.py` 当前 `_chat_request_platform()` 只做 `str(...).strip().lower()`，没有格式校验。
- `app/group_ingress/service.py` 继续从 `req.client_meta.platform` 读取平台；route 层先归一化 `req.client_meta`，因此 service 不需要重复校验。
- `docs/plan_walkthrough.md` 已同步 P2-2.5 状态，底部“下一步”已切到 P2-3「QQ 出站渲染契约」。

## 任务 1：新增 helper 红灯测试

**文件：**
- 创建：`tests/test_client_meta.py`

- [x] **步骤 1：编写失败的 helper 测试**

```python
import pytest


def test_normalize_client_meta_defaults_platform_and_chat_type():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(None, expected_chat_type="private")

    assert normalized["platform"] == "qq"
    assert normalized["chat_type"] == "private"


def test_normalize_client_meta_lowercases_platform_and_preserves_extensions():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(
        {"platform": " Web ", "stickers": [{"file": "s.png"}]},
        expected_chat_type="group",
    )

    assert normalized["platform"] == "web"
    assert normalized["chat_type"] == "group"
    assert normalized["stickers"] == [{"file": "s.png"}]


def test_normalize_client_meta_rejects_chat_type_mismatch():
    from core.client_meta import ClientMetaValidationError, normalize_client_meta

    with pytest.raises(ClientMetaValidationError, match="chat_type"):
        normalize_client_meta({"chat_type": "group"}, expected_chat_type="private")


def test_normalize_client_meta_trims_trace_request_id():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(
        {"trace": {"request_id": " req-" + "x" * 200}},
        expected_chat_type="private",
    )

    assert normalized["trace"]["request_id"].startswith("req-")
    assert len(normalized["trace"]["request_id"]) == 128


def test_normalize_client_meta_rejects_non_string_request_id():
    from core.client_meta import ClientMetaValidationError, normalize_client_meta

    with pytest.raises(ClientMetaValidationError, match="trace.request_id"):
        normalize_client_meta(
            {"trace": {"request_id": 123}},
            expected_chat_type="private",
        )
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_client_meta.py -v -p no:cacheprovider
```

预期：失败，核心错误为 `ModuleNotFoundError: No module named 'core.client_meta'`。

## 任务 2：实现 `core/client_meta.py`

**文件：**
- 创建：`core/client_meta.py`
- 测试：`tests/test_client_meta.py`

- [x] **步骤 1：编写最少实现**

```python
"""client_meta 边界解析与校验。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TRACE_STRING_KEYS = ("request_id", "correlation_id", "source")
_TRACE_VALUE_MAX_CHARS = 128


class ClientMetaValidationError(ValueError):
    """client_meta 字段不符合边界层约束。"""


def _normalize_platform(value: Any) -> str:
    if value is None or value == "":
        return "qq"
    if not isinstance(value, str):
        raise ClientMetaValidationError("platform must be a string")
    platform = value.strip().lower() or "qq"
    if not _PLATFORM_RE.fullmatch(platform):
        raise ClientMetaValidationError("platform must match ^[a-z][a-z0-9_-]{0,31}$")
    return platform


def _normalize_chat_type(value: Any, *, expected_chat_type: str) -> str:
    expected = str(expected_chat_type or "").strip().lower()
    if expected not in {"private", "group"}:
        raise ClientMetaValidationError("expected_chat_type must be private or group")
    if value is None or value == "":
        return expected
    if not isinstance(value, str):
        raise ClientMetaValidationError("chat_type must be a string")
    actual = value.strip().lower()
    if actual != expected:
        raise ClientMetaValidationError(f"chat_type must be {expected}")
    return expected


def _normalize_trace(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ClientMetaValidationError("trace must be an object")
    trace: dict[str, str] = {}
    for key in _TRACE_STRING_KEYS:
        if key not in value or value[key] in (None, ""):
            continue
        if not isinstance(value[key], str):
            raise ClientMetaValidationError(f"trace.{key} must be a string")
        normalized = value[key].strip()
        if normalized:
            trace[key] = normalized[:_TRACE_VALUE_MAX_CHARS]
    return trace


def normalize_client_meta(
    client_meta: Mapping[str, Any] | None,
    *,
    expected_chat_type: str,
) -> dict[str, Any]:
    raw = dict(client_meta) if isinstance(client_meta, Mapping) else {}
    normalized = dict(raw)
    normalized["platform"] = _normalize_platform(raw.get("platform"))
    normalized["chat_type"] = _normalize_chat_type(
        raw.get("chat_type"),
        expected_chat_type=expected_chat_type,
    )
    trace = _normalize_trace(raw.get("trace"))
    if trace:
        normalized["trace"] = trace
    else:
        normalized.pop("trace", None)
    return normalized


def client_meta_request_id(client_meta: Mapping[str, Any] | None) -> str:
    if not isinstance(client_meta, Mapping):
        return ""
    trace = client_meta.get("trace")
    if not isinstance(trace, Mapping):
        return ""
    return str(trace.get("request_id") or "")
```

- [x] **步骤 2：运行 helper 测试验证通过**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_client_meta.py -v -p no:cacheprovider
```

预期：`5 passed`。

## 任务 3：接入 `/chat` 与 `/group/message` 红灯测试

**文件：**
- 修改：`tests/test_chat_response_envelope.py`
- 修改：`tests/test_group_response_envelope.py`
- 可选修改：`tests/test_api.py`

- [x] **步骤 1：新增 `/chat` API 测试**

在 `tests/test_chat_response_envelope.py` 追加：

```python
def test_proxy_chat_meta_includes_normalized_trace_request_id(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "带 trace 回复"

        def pop_last_reply_meta(self, session_id):
            return {}

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "trace_user",
                "session_id": "private_trace_user",
                "query": "trace",
                "client_meta": {
                    "platform": " Web ",
                    "trace": {"request_id": " req-123 "},
                },
            },
        )

    data = response.json()
    assert response.status_code == 200
    assert data["meta"]["platform"] == "web"
    assert data["meta"]["request_id"] == "req-123"


def test_proxy_chat_rejects_conflicting_client_meta_chat_type(client, monkeypatch):
    _fast_private_reply(monkeypatch)

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            raise AssertionError("invalid client_meta must not reach bridge")

    with patch("api.routes.get_bridge", return_value=FakeBridge()):
        response = client.post(
            "/api/v1/chat",
            json={
                "user_id": "bad_meta_user",
                "session_id": "private_bad_meta_user",
                "query": "bad",
                "client_meta": {"chat_type": "group"},
            },
        )

    assert response.status_code == 400
    assert "client_meta" in response.json()["detail"]
```

- [x] **步骤 2：新增 `/group/message` API 测试**

在 `tests/test_group_response_envelope.py` 追加：

```python
@pytest.mark.asyncio
async def test_group_message_rejects_conflicting_client_meta_chat_type(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    async def fake_process(*args, **kwargs):
        raise AssertionError("invalid client_meta must not enter TimingGate")

    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    with pytest.raises(Exception) as exc:
        await group_message(
            GroupMessageRequest(
                group_id="bad-meta-group",
                sender_id="u-bad-meta",
                message="bad",
                client_meta={"chat_type": "private"},
            ),
            db_session,
            None,
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert "client_meta" in str(getattr(exc.value, "detail", ""))


@pytest.mark.asyncio
async def test_group_message_preserves_normalized_trace_in_ambient_log(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message
    from core.database import ChatLog

    async def fake_process(*args, **kwargs):
        return {"action": "no_reply", "generation": 1, "reason": "unit"}

    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    data = await group_message(
        GroupMessageRequest(
            group_id="trace-group",
            sender_id="u-trace",
            message="trace",
            client_meta={
                "platform": " Web ",
                "trace": {"request_id": " req-group-1 "},
                "stickers": [{"file": "s.png"}],
            },
        ),
        db_session,
        None,
    )

    assert data["status"] == "no_reply"
    ambient = db_session.query(ChatLog).filter_by(role="ambient").order_by(ChatLog.id.desc()).first()
    meta = json.loads(ambient.meta_json)
    assert meta["client_meta"]["platform"] == "web"
    assert meta["client_meta"]["chat_type"] == "group"
    assert meta["client_meta"]["trace"]["request_id"] == "req-group-1"
    assert meta["client_meta"]["stickers"] == [{"file": "s.png"}]
```

- [x] **步骤 3：运行 API 测试验证失败**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_chat_response_envelope.py \
  tests/test_group_response_envelope.py \
  -v -p no:cacheprovider
```

预期：新增测试失败，失败点为 `meta.request_id` 缺失、冲突 `chat_type` 未返回 400、群聊 ambient log 中 `client_meta` 未归一。

## 任务 4：接入 route 边界校验

**文件：**
- 修改：`api/routes.py`
- 修改：`app/group_ingress/service.py`
- 测试：`tests/test_chat_response_envelope.py`
- 测试：`tests/test_group_response_envelope.py`

- [x] **步骤 1：在 `api/routes.py` 导入 helper**

```python
from core.client_meta import (
    ClientMetaValidationError,
    client_meta_request_id,
    normalize_client_meta,
)
```

- [x] **步骤 2：新增 route 层转换函数**

放在 `_chat_request_platform()` 前后：

```python
def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized
```

- [x] **步骤 3：在 `/chat` 入口最前面归一化**

在 `proxy_chat()` 日志前加入：

```python
    _normalize_request_client_meta(req, expected_chat_type=_chat_request_type(req))
```

- [x] **步骤 4：在 `/group/message` 入口归一化**

在创建 `GroupIngressService` 前加入：

```python
    _normalize_request_client_meta(req, expected_chat_type="group")
```

- [x] **步骤 5：把 `request_id` 投影到 `/chat` 响应 meta**

在 `_chat_response_meta()` 里加入：

```python
    request_id = client_meta_request_id(req.client_meta)
    if request_id:
        meta["request_id"] = request_id
```

`app/group_ingress/service.py` 不需要单独投影 `request_id`；它的 `_response()` 继续通过 `req.client_meta` 读取平台，ambient log 会保存归一化后的 `client_meta`。

- [x] **步骤 6：运行 API 测试验证通过**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_client_meta.py \
  tests/test_chat_response_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_api.py::test_proxy_chat_passes_client_platform_to_bridge \
  tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate \
  tests/test_api.py::test_group_message_passes_client_platform_to_bridge \
  -v -p no:cacheprovider
```

预期：全部通过。

已验证：

- helper 红灯：`PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_client_meta.py -v -p no:cacheprovider`，结果 `5 failed, 1 warning`，失败原因均为 `ModuleNotFoundError: No module named 'core.client_meta'`。
- helper 绿灯：同一命令，结果 `5 passed, 1 warning in 0.60s`。
- API 红灯：`PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_chat_response_envelope.py tests/test_group_response_envelope.py -v -p no:cacheprovider`，结果 `4 failed, 5 passed, 21 warnings`。
- API 绿灯：`PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_client_meta.py tests/test_chat_response_envelope.py tests/test_group_response_envelope.py -v -p no:cacheprovider`，结果 `14 passed, 21 warnings in 2.70s`。

## 任务 5：同步文档状态并最终验证

**文件：**
- 修改：`docs/message-field-standard.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/client-meta-boundary-validation.md`

- [x] **步骤 1：同步 `docs/message-field-standard.md`**

在 `client_meta` 章节补运行时校验口径：

```markdown
运行时边界校验：

- `platform` 缺省为 `qq`，传入时必须是 `^[a-z][a-z0-9_-]{0,31}$`。
- `/chat` 和 `/group/message` 会校验 `client_meta.chat_type` 与入口事实一致。
- `trace.request_id`、`trace.correlation_id`、`trace.source` 必须是字符串，并裁剪到 128 字符。
- 其它扩展字段保留，但不应放完整平台 event。
```

- [x] **步骤 2：同步路线文档**

把 `docs/todo.md` 路线项 5 的状态改为：响应信封与 `client_meta` 关键字段边界校验已完成；P2-3 继续处理 QQ 出站渲染契约。

把 `docs/plan_walkthrough.md` 中过时的 P2-2 旧口径改为当前事实，并将底部“下一步”切到 P2-3。

- [x] **步骤 3：运行文档检查**

运行：

```bash
python - <<'PY'
from pathlib import Path
paths = [
    Path("docs/message-field-standard.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/client-meta-boundary-validation.md"),
    Path("docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md"),
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

git diff --check -- \
  docs/message-field-standard.md \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/client-meta-boundary-validation.md \
  docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md
```

预期：两条命令均无输出，退出码 0。

- [x] **步骤 4：运行定向回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_client_meta.py \
  tests/test_chat_response_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_api.py::test_proxy_chat_passes_client_platform_to_bridge \
  tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate \
  tests/test_api.py::test_group_message_passes_client_platform_to_bridge \
  -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 5：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

已验证：

- 文档占位词扫描：`docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/client-meta-boundary-validation.md docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，结果无输出，退出码 0。
- 文档格式检查：`git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/client-meta-boundary-validation.md docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md`，结果无输出，退出码 0。
- 定向回归：`PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_client_meta.py tests/test_chat_response_envelope.py tests/test_group_response_envelope.py tests/test_api.py::test_proxy_chat_passes_client_platform_to_bridge tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate tests/test_api.py::test_group_message_passes_client_platform_to_bridge -v -p no:cacheprovider`，结果 `17 passed, 21 warnings in 3.29s`。
- 全量回归：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider`，结果 `1272 passed, 6 skipped, 139 warnings in 86.68s`。

- [x] **步骤 6：提交**

```bash
git add \
  core/client_meta.py \
  tests/test_client_meta.py \
  tests/test_chat_response_envelope.py \
  tests/test_group_response_envelope.py \
  api/routes.py \
  docs/message-field-standard.md \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/client-meta-boundary-validation.md \
  docs/superpowers/specs/2026-06-18-client-meta-boundary-validation-design.md
git commit -m "feat(消息): 校验客户端元信息边界"
```

## 验收清单

- [x] `/chat` 缺省 `client_meta` 仍按 `platform=qq` 工作。
- [x] `/chat` 合法 `trace.request_id` 进入响应 `meta.request_id`。
- [x] `/chat` 冲突 `client_meta.chat_type` 返回 400，且不调用 Bridge。
- [x] `/group/message` 冲突 `client_meta.chat_type` 返回 400，且不进入 TimingGate。
- [x] `/group/message` 归一化后的 `client_meta` 写入 ambient log，且保留 `stickers` 等扩展。
- [x] P2-2 响应信封旧字段兼容不变。
- [x] 定向测试和全量测试通过。
