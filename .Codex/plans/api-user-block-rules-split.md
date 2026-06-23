# 普通 API 用户屏蔽规则拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把私聊和群聊重复的 `UserBlockRule` 匹配逻辑收敛到 `core/user_block_rules.py`，保留现有入口 wrapper 和运行时行为。

**架构：** `core.user_block_rules.is_user_blocked()` 负责查询启用规则并按 `target_type`、`all` 和归一化 `group_id` 判断命中；`api.routes._check_user_blocked()` 与 `app.group_ingress.helpers.check_user_blocked()` 继续作为异常兜底 wrapper。`/chat`、`GroupIngressService`、Admin CRUD、ChatLog、timing annotation、Bridge、SSE、push envelope 和 response envelope 均不迁移。

**技术栈：** Python 3.12、SQLAlchemy ORM、pytest、FastAPI 路由兼容测试、`rg` 静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-user-block-rules-split-design.md`
- [x] 设计提交：`f9309d7 docs(普通API): 设计用户屏蔽规则拆分`

## 边界与禁止事项

- 保留：`api.routes._check_user_blocked.__module__ == "api.routes"`。
- 保留：`app.group_ingress.helpers.check_user_blocked()` 作为群聊入口 monkeypatch 点。
- 保留：`evals/runners/moderation_runner.py` 继续可导入 `api.routes._check_user_blocked`。
- 保留：`/chat` 命中 block 后只写 `ChatLog`、返回 `status="silent"`、不进入图片预缓存 / guardrail / Bridge / `ConversationTurn`。
- 保留：群聊命中 block 后 annotate timing event，返回 `action="no_reply"`、`reason="user_blocked"`、`generation=0`。
- 保留：异常 fail-open 行为，由两个 wrapper 记录 warning 后返回 `False`。
- 禁止：修改 Admin block rule CRUD、DB schema、WebUI 或 JS。
- 禁止：解释 `rule_mode` / `reason` 为新动作。
- 禁止：新增 `target_type` 枚举校验、`user_id` 归一化、`strip()` 或 `lower()`。
- 禁止：修正「group 规则带 `group_id` 但请求未传 `group_id` 仍命中」的旧语义。
- 禁止：新模块导入 `api.routes` 或 `app.group_ingress.helpers`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 文件职责

- 创建：`core/user_block_rules.py`
  - 承载 `is_user_blocked()` 的唯一匹配实现。
  - 默认使用 `core.database.UserBlockRule` 与 `core.group_runtime.ids.normalize_group_session_id`。
  - 提供 `rule_model` 与 `group_id_normalizer` 依赖注入点，便于测试。
- 修改：`api/routes.py`
  - 导入 `core.user_block_rules`。
  - 将 `_check_user_blocked()` 改为薄 wrapper，保留异常日志和 `False` 兜底。
- 修改：`app/group_ingress/helpers.py`
  - 导入 `core.user_block_rules`。
  - 将 `check_user_blocked()` 改为薄 wrapper，保留异常日志和 `False` 兜底。
- 创建：`tests/test_user_block_rules.py`
  - 锁定 core helper 不反向导入入口模块。
  - 锁定 private / all / group / disabled / legacy missing group 行为。
  - 锁定两个 wrapper 保留并委托 core helper。
- 修改：`tests/test_api.py`
  - 增加群聊 block 行为回归，确认 block 后不进入 TimingGate / Bridge。
- 修改：`.Codex/plans/api-user-block-rules-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 记录 P3 第二十二刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_user_block_rules.py`
- 修改：`tests/test_api.py`

- [x] **步骤 1：创建 core split 测试文件**

创建 `tests/test_user_block_rules.py`，写入以下测试骨架和 fake 查询对象：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class Field:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, value: Any):  # type: ignore[override]
        return (self.name, value)


class FakeRuleModel:
    user_id = Field("user_id")
    enabled = Field("enabled")


@dataclass
class FakeRule:
    user_id: str
    target_type: str = "private"
    group_id: str = ""
    enabled: int = 1
    rule_mode: str = "log_only"
    reason: str = ""


class FakeQuery:
    def __init__(self, rows: list[FakeRule]):
        self._rows = rows
        self._filters: tuple[Any, ...] = ()

    def filter(self, *conditions: Any):
        self._filters = conditions
        return self

    def all(self) -> list[FakeRule]:
        filters = dict(self._filters)
        return [
            row
            for row in self._rows
            if row.user_id == filters.get("user_id")
            and row.enabled == filters.get("enabled")
        ]


class FakeDb:
    def __init__(self, rows: list[FakeRule]):
        self.rows = rows
        self.model = None

    def query(self, model: Any) -> FakeQuery:
        self.model = model
        return FakeQuery(self.rows)
```

- [x] **步骤 2：新增新模块源码约束红灯**

在 `tests/test_user_block_rules.py` 中追加：

```python
def test_user_block_rules_module_does_not_import_entrypoints_or_sync_awaitable():
    source = _source("core/user_block_rules.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "from app.group_ingress.helpers" not in source
    assert "import app.group_ingress.helpers" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 3：新增 core helper 行为红灯**

在 `tests/test_user_block_rules.py` 中追加：

```python
def test_private_rule_matches_private_target():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="private")])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)
    assert db.model is FakeRuleModel


def test_all_rule_matches_private_and_group_targets():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="all", group_id="group_999")])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)
    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="123",
        rule_model=FakeRuleModel,
    )


def test_group_rule_matches_normalized_group_id_formats():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="qq:123:group",
        rule_model=FakeRuleModel,
    )


def test_group_rule_with_group_id_mismatch_does_not_match():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert not is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="456",
        rule_model=FakeRuleModel,
    )


def test_group_rule_with_group_id_and_missing_request_group_keeps_legacy_match():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="group_123")])

    assert is_user_blocked(db, "u1", target_type="group", rule_model=FakeRuleModel)


def test_group_rule_without_group_id_matches_any_group():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="group", group_id="")])

    assert is_user_blocked(
        db,
        "u1",
        target_type="group",
        group_id="group_999",
        rule_model=FakeRuleModel,
    )


def test_disabled_rule_is_ignored():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([FakeRule(user_id="u1", target_type="all", enabled=0)])

    assert not is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)


def test_rule_mode_and_reason_do_not_affect_matching():
    from core.user_block_rules import is_user_blocked

    db = FakeDb([
        FakeRule(
            user_id="u1",
            target_type="private",
            rule_mode="unknown",
            reason="任何原因",
        )
    ])

    assert is_user_blocked(db, "u1", target_type="private", rule_model=FakeRuleModel)
```

- [x] **步骤 4：新增 wrapper 委托与异常兜底红灯**

在 `tests/test_user_block_rules.py` 中追加：

```python
def test_api_routes_wrapper_delegates_and_fails_open_on_exception(monkeypatch):
    from api import routes

    calls = []

    def fake_is_user_blocked(db, user_id, **kwargs):
        calls.append((db, user_id, kwargs))
        return True

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", fake_is_user_blocked)

    db = object()
    assert routes._check_user_blocked(db, "u1", target_type="group", group_id="123")
    assert routes._check_user_blocked.__module__ == "api.routes"
    assert calls == [
        (db, "u1", {"target_type": "group", "group_id": "123"}),
    ]

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", raise_error)

    assert not routes._check_user_blocked(db, "u1", target_type="private")


def test_group_ingress_wrapper_delegates_and_fails_open_on_exception(monkeypatch):
    from app.group_ingress import helpers

    calls = []

    def fake_is_user_blocked(db, user_id, **kwargs):
        calls.append((db, user_id, kwargs))
        return True

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", fake_is_user_blocked)

    db = object()
    assert helpers.check_user_blocked(db, "u1", target_type="group", group_id="123")
    assert helpers.check_user_blocked.__module__ == "app.group_ingress.helpers"
    assert calls == [
        (db, "u1", {"target_type": "group", "group_id": "123"}),
    ]

    def raise_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("core.user_block_rules.is_user_blocked", raise_error)

    assert not helpers.check_user_blocked(db, "u1", target_type="group", group_id="123")
```

- [x] **步骤 5：新增群聊行为回归红灯**

在 `tests/test_api.py` 的 `/group/message` 测试区域追加：

```python
@pytest.mark.asyncio
async def test_group_message_blocked_user_returns_no_reply_and_skips_runtime(db_session, monkeypatch):
    from unittest.mock import AsyncMock
    from api.routes import GroupMessageRequest, group_message
    from core.database import ChatLog, UserBlockRule

    db_session.add(UserBlockRule(
        user_id="blocked-group-user",
        target_type="group",
        group_id="group_987",
        enabled=1,
    ))
    db_session.commit()

    async def fail_process(*args, **kwargs):
        raise AssertionError("blocked group message must not enter TimingGate")

    mock_bridge = AsyncMock()
    mock_bridge.handle_message = AsyncMock(
        side_effect=AssertionError("blocked group message must not enter Bridge")
    )
    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fail_process)
    monkeypatch.setattr("api.routes.get_bridge", lambda: mock_bridge)

    data = await group_message(
        GroupMessageRequest(
            group_id="qq:987:group",
            sender_id="blocked-group-user",
            sender_name="屏蔽用户",
            message="这条消息应该被屏蔽",
            session_name="屏蔽测试群",
            message_id="m-blocked-group-1",
        ),
        db_session,
        None,
    )

    assert data["action"] == "no_reply"
    assert data["reason"] == "user_blocked"
    assert data["generation"] == 0
    ambient = (
        db_session.query(ChatLog)
        .filter_by(user_id="blocked-group-user", role="ambient")
        .one()
    )
    assert ambient.processed == 1
    assert "user_blocked" in (ambient.meta_json or "")
    mock_bridge.handle_message.assert_not_called()
```

- [x] **步骤 6：运行红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_user_block_rules.py -v
```

预期：

- 失败。
- 失败原因为 `core/user_block_rules.py` 不存在，或 wrapper 尚未委托新模块。

然后运行新增群聊行为测试：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api.py::test_group_message_blocked_user_returns_no_reply_and_skips_runtime -v
```

预期：

- 如果当前旧实现已经满足行为，允许通过；此测试作为迁移回归守卫。
- 若失败，失败点必须来自群聊 block 行为缺失，而不是测试 setup 错误。

- [x] **步骤 7：提交红灯测试**

```bash
git add tests/test_user_block_rules.py tests/test_api.py .Codex/plans/api-user-block-rules-split.md
git commit -m "test(普通API): 锁定用户屏蔽规则契约"
```

---

## 任务 2：新增 core helper

**文件：**
- 创建：`core/user_block_rules.py`
- 修改：`.Codex/plans/api-user-block-rules-split.md`

- [ ] **步骤 1：创建新模块**

创建 `core/user_block_rules.py`：

```python
"""用户屏蔽规则匹配 helper。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.database import UserBlockRule
from core.group_runtime.ids import normalize_group_session_id


def _matches_user_block_rule(
    rule: Any,
    *,
    target_type: str,
    group_id: str,
    group_id_normalizer: Callable[[str], str],
) -> bool:
    if getattr(rule, "target_type", "") not in (target_type, "all"):
        return False

    if getattr(rule, "target_type", "") == "group" and getattr(rule, "group_id", ""):
        norm_group = group_id_normalizer(group_id) if group_id else ""
        if norm_group and group_id_normalizer(str(rule.group_id)) != norm_group:
            return False

    return True


def is_user_blocked(
    db: Any,
    user_id: str,
    *,
    target_type: str = "private",
    group_id: str = "",
    rule_model: Any = UserBlockRule,
    group_id_normalizer: Callable[[str], str] = normalize_group_session_id,
) -> bool:
    rules = db.query(rule_model).filter(
        rule_model.user_id == user_id,
        rule_model.enabled == 1,
    ).all()
    return any(
        _matches_user_block_rule(
            rule,
            target_type=target_type,
            group_id=group_id,
            group_id_normalizer=group_id_normalizer,
        )
        for rule in rules
    )
```

- [ ] **步骤 2：运行 core 定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_user_block_rules.py -v
```

预期：

- core helper 行为测试通过。
- wrapper 委托测试仍失败，失败原因为父模块尚未委托 `core.user_block_rules.is_user_blocked()`。

- [ ] **步骤 3：静态检查**

运行：

```bash
python -m compileall core/user_block_rules.py -q
git diff --check -- core/user_block_rules.py tests/test_user_block_rules.py tests/test_api.py .Codex/plans/api-user-block-rules-split.md
```

预期：

- `compileall` 退出码 0。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 4：提交 core helper**

```bash
git add core/user_block_rules.py .Codex/plans/api-user-block-rules-split.md
git commit -m "refactor(普通API): 增加用户屏蔽规则助手"
```

---

## 任务 3：接入私聊和群聊 wrapper

**文件：**
- 修改：`api/routes.py`
- 修改：`app/group_ingress/helpers.py`
- 修改：`.Codex/plans/api-user-block-rules-split.md`

- [ ] **步骤 1：接入 `api.routes` wrapper**

在 `api/routes.py` 的 `from api import (...)` 后增加：

```python
from core import user_block_rules
```

将 `_check_user_blocked()` 改为：

```python
def _check_user_blocked(db, user_id: str, target_type: str = "private", group_id: str = "") -> bool:
    """检查用户是否被屏蔽——命中规则时返回 True。"""
    try:
        return user_block_rules.is_user_blocked(
            db,
            user_id,
            target_type=target_type,
            group_id=group_id,
        )
    except Exception as e:
        logger.warning("[BlockRule] check failed user=%s group=%s: %s", user_id, group_id, e)
    return False
```

保留函数名、参数、日志和返回值。

- [ ] **步骤 2：接入群聊 helper wrapper**

在 `app/group_ingress/helpers.py` 顶部导入区增加：

```python
from core import user_block_rules
```

将 `check_user_blocked()` 改为：

```python
def check_user_blocked(db, user_id: str, target_type: str = "private", group_id: str = "") -> bool:
    try:
        return user_block_rules.is_user_blocked(
            db,
            user_id,
            target_type=target_type,
            group_id=group_id,
        )
    except Exception as exc:
        logger.warning("[BlockRule] check failed user=%s group=%s: %s", user_id, group_id, exc)
    return False
```

同时删除该函数内部不再需要的局部 `UserBlockRule` 导入。

- [ ] **步骤 3：运行定向绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_user_block_rules.py \
  tests/test_api.py::test_group_message_blocked_user_returns_no_reply_and_skips_runtime \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 4：运行相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestBlockRule::test_create_and_list \
  tests/test_admin_api.py::TestBlockRule::test_toggle_enabled \
  tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files \
  tests/test_api_group_message_routes_split.py::test_api_group_message_route_does_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api.py::test_group_ingress_service_does_not_import_api_routes \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：

- 全部通过。

- [ ] **步骤 5：静态检查和行数记录**

运行：

```bash
python -m compileall api/routes.py app/group_ingress/helpers.py core/user_block_rules.py -q
wc -l api/routes.py app/group_ingress/helpers.py core/user_block_rules.py tests/test_user_block_rules.py
git diff --check -- api/routes.py app/group_ingress/helpers.py core/user_block_rules.py tests/test_user_block_rules.py tests/test_api.py .Codex/plans/api-user-block-rules-split.md
```

预期：

- `compileall` 退出码 0。
- `api/routes.py` 行数下降。
- `git diff --check` 无输出，退出码 0。

- [ ] **步骤 6：提交 wrapper 接入**

```bash
git add api/routes.py app/group_ingress/helpers.py .Codex/plans/api-user-block-rules-split.md
git commit -m "refactor(普通API): 接入用户屏蔽规则助手"
```

---

## 任务 4：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-user-block-rules-split.md`

- [ ] **步骤 1：更新计划执行记录**

在本计划底部追加执行记录，至少包含：

- 红灯输出摘要。
- core helper 阶段输出摘要。
- wrapper 接入定向 / 相邻回归输出摘要。
- 行数检查。
- 全量测试结果。
- 提交列表。

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」中追加第二十二刀进展，记录：

- 新模块路径。
- 私聊和群聊 wrapper 保留。
- `/chat`、`GroupIngressService`、Admin CRUD、ChatLog、timing annotation、Bridge、SSE、push envelope 和 response envelope 均未迁移。
- 新模块没有反向导入入口模块，也没有同步包装 awaitable。
- `api/routes.py` 的真实行数变化和验证结果。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-23 普通 API 用户屏蔽规则拆分` 小节，包含：

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
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-user-block-rules-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-user-block-rules-split.md docs/todo.md docs/plan_walkthrough.md
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
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-user-block-rules-split.md
git commit -m "docs(计划): 收口用户屏蔽规则拆分"
```

---

## 执行记录

- 2026-06-23 设计阶段：
  写入 `docs/superpowers/specs/2026-06-23-api-user-block-rules-split-design.md`，
  并随 `f9309d7 docs(普通API): 设计用户屏蔽规则拆分` 提交。
- 2026-06-23 任务 1 core split 红灯：
  `python -B -m pytest -p no:cacheprovider tests/test_user_block_rules.py -v`
  退出码 1，`11 failed, 1 warning`。失败点为 `core/user_block_rules.py`
  不存在、`core.user_block_rules` 无法导入，以及 wrapper 委托 monkeypatch
  找不到目标模块，符合红灯预期。
- 2026-06-23 任务 1 群聊行为回归：
  `python -B -m pytest -p no:cacheprovider tests/test_api.py::test_group_message_blocked_user_returns_no_reply_and_skips_runtime -v`
  退出码 0，`1 passed, 1 warning`。该测试锁定旧行为：群聊 user block
  命中后返回 `no_reply/user_blocked`，不会进入 TimingGate 或 Bridge。
- 2026-06-23 任务 1 提交：
  随本次 `test(普通API): 锁定用户屏蔽规则契约` 提交。
