# P2-1 工具 platform 维度配置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让运行时工具策略支持 platform 维度，并在 Admin / WebUI / 审计链路中可配置、可预览、可排查。

**架构：** 复用 `ToolOverride(scope_type="platform", scope_id="<platform>")` 表达平台全局覆盖；解析顺序固定为 `chat_type < platform < group < user`。真实入口把标准化 platform 传给 Bridge，Bridge 再传给 ToolPlan 和 runtime decision；`RuntimeToolDecision` 增加 `platform` 列作为审计维度。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy、SQLite schema migration、pytest、React WebUI。

---

## 当前事实

- 设计文档：`docs/superpowers/specs/2026-06-18-tool-platform-scope-design.md`，提交 `d221180 docs(工具): 设计平台维度配置`。
- 当前计划文件路径按项目约定使用 `.Codex/plans/tool-platform-scope.md`。
- `docs/todo.md` 路线项 4 是当前 P2 首项；P2-4 Prompt Runtime 平台化不纳入本计划。
- 任务 1 已完成并提交：`bb7489c feat(工具): 支持平台维度解析`。
- 任务 2 已完成并提交：`295e3f7 feat(工具): 记录平台维度决策`。
- 任务 3 已完成并提交：`73bbe8a feat(消息): 透传客户端平台`。真实入口已透传 platform 到 Bridge 和 ToolPlan。
- 当前下一步：任务 4「Admin API 支持 platform 覆盖和预览」。
- 现有无关脏文件包括 pycache、`docs/goal.md`、`tests/conftest.py`、`.codex/` 历史计划、`docs/TODO_LIST.md` 等。执行本计划时不要回滚、删除或暂存这些文件。

## 文件结构

- 修改：`core/runtime_tool_service.py`
  - 新增 platform 归一化 helper。
  - `resolve_effective_tools()` 查询和排序 platform override。
  - `record_runtime_tool_decision()` 写入 platform。
- 修改：`core/tool_plan.py`
  - `build_tool_plan()` 透传 platform。
- 修改：`core/final_tools.py`
  - `resolve_final_tools()` 透传 platform。
- 修改：`core/database.py`
  - `ToolOverride` 注释增加 platform scope。
  - `RuntimeToolDecision` 增加 `platform` 列。
- 修改：`core/schema_migrations.py`
  - 增加 `runtime_tool_decisions.platform` 补列迁移。
- 修改：`api/routes.py`
  - `/chat` 的 `bridge_meta` 写入 `platform`。
- 修改：`app/group_ingress/service.py`
  - 群聊 `_continue_to_bridge()` 的 `bridge_meta` 写入 platform。
- 修改：`nanobot_kt/bridge.py`
  - 从 metadata 读取 platform，并传给 ToolPlan 和 runtime decision。
- 修改：`api/admin_routes.py`
  - `ToolOverrideBody`、`/tools`、`/tools/effective`、`/tools/targets`、`/tools/decisions` 支持 platform。
- 修改：`webui/src/features/tools/ToolsPage.jsx`
  - 增加 platform selector 和「指定平台」覆盖对象。
- 修改：`docs/message-field-standard.md`
  - 补充工具策略消费 `client_meta.platform` 的口径。
- 修改：`docs/todo.md`
  - P2-1 完成后同步路线项 4 状态。
- 修改：`docs/plan_walkthrough.md`
  - P2-1 完成后同步任务和验证记录。
- 修改：`.Codex/plans/tool-platform-scope.md`
  - 执行过程中勾选任务、记录提交号和验证输出。

测试文件：

- 修改：`tests/test_tool_plan.py`
- 修改：`tests/test_schema_migrations.py`
- 修改：`tests/test_api.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`tests/test_admin_api.py`
- 修改：`tests/test_webui_admin_redesign.py` 或 `tests/test_webui_app_split.py`

## 任务 1：后端工具解析支持 platform scope

**文件：**
- 修改：`tests/test_tool_plan.py`
- 修改：`core/runtime_tool_service.py`
- 修改：`core/tool_plan.py`
- 修改：`core/final_tools.py`

- [x] **步骤 1：编写 platform override 红灯测试**

在 `tests/test_tool_plan.py` 追加测试：

```python
def test_platform_override_precedence_between_chat_type_group_and_user(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add_all([
        ToolOverride(tool_name="memory_query", scope_type="chat_type", scope_id="private", enabled=0, reason="私聊禁用"),
        ToolOverride(tool_name="memory_query", scope_type="platform", scope_id="web", enabled=1, reason="Web 放开"),
        ToolOverride(tool_name="memory_query", scope_type="group", scope_id="g1", enabled=0, reason="群覆盖禁用"),
        ToolOverride(tool_name="memory_query", scope_type="user", scope_id="u1", enabled=1, reason="用户覆盖放开"),
    ])
    db_session.commit()

    enabled, disabled = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="g1",
        user_id="u1",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled["memory_query"] is True
    assert "memory_query" not in disabled

    enabled_without_user, disabled_without_user = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="g1",
        user_id="",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_without_user["memory_query"] is False
    assert disabled_without_user["memory_query"] == "群覆盖禁用"

    enabled_platform_only, disabled_platform_only = resolve_effective_tools(
        chat_type="private",
        platform="web",
        group_id="",
        user_id="",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_platform_only["memory_query"] is True
    assert "memory_query" not in disabled_platform_only
```

- [x] **步骤 2：编写硬约束红灯测试**

继续在 `tests/test_tool_plan.py` 追加：

```python
def test_platform_override_cannot_bypass_none_or_hard_constraints(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add_all([
        ToolOverride(tool_name="memory_query", scope_type="platform", scope_id="web", enabled=1, reason="Web 放开"),
        ToolOverride(tool_name="reply", scope_type="platform", scope_id="web", enabled=0, reason="错误禁用回复"),
        ToolOverride(tool_name="write", scope_type="platform", scope_id="web", enabled=1, reason="错误放开写文件"),
    ])
    db_session.commit()

    enabled_none, disabled_none = resolve_effective_tools(
        chat_type="private",
        platform="web",
        runtime_preset="none",
        db=db_session,
    )
    assert enabled_none["memory_query"] is False
    assert disabled_none["memory_query"] == "运行时预设=none"

    enabled_group, disabled_group = resolve_effective_tools(
        chat_type="group",
        platform="web",
        runtime_preset="full",
        db=db_session,
    )
    assert enabled_group["reply"] is True
    assert "reply" not in disabled_group
    assert enabled_group["write"] is False
    assert disabled_group["write"] == "群聊强制禁用"
```

- [x] **步骤 3：编写 ToolPlan / FinalTools 透传红灯测试**

追加测试：

```python
def test_build_tool_plan_and_final_tools_pass_platform(db_session):
    from core.database import ToolOverride
    from core.final_tools import resolve_final_tools
    from core.tool_plan import build_tool_plan

    db_session.add(ToolOverride(
        tool_name="image_generation",
        scope_type="platform",
        scope_id="web",
        enabled=0,
        reason="Web 禁用图片生成",
    ))
    db_session.commit()

    plan = build_tool_plan(chat_type="private", platform="web", runtime_preset="full", db=db_session)
    final_tools = resolve_final_tools(chat_type="private", platform="web", runtime_preset="full", db=db_session)

    assert plan.enabled["image_generation"] is False
    assert "image_generation" not in plan.sent_tool_names
    assert "image_generation" not in final_tools.allowed
    assert final_tools.disabled["image_generation"] == "Web 禁用图片生成"
```

- [x] **步骤 4：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_tool_plan.py -k "platform_override or pass_platform" -v -p no:cacheprovider
```

预期：失败，报错包含 `unexpected keyword argument 'platform'` 或平台覆盖未生效。

- [x] **步骤 5：实现 platform 归一化和解析**

在 `core/runtime_tool_service.py` 添加 helper：

```python
def normalize_tool_platform(platform: str = "") -> str:
    return str(platform or "").strip().lower()
```

修改 `resolve_effective_tools()` 签名和查询：

```python
def resolve_effective_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    db=None,
) -> tuple[dict[str, bool], dict[str, str]]:
    chat_type = normalize_tool_chat_type(chat_type)
    platform = normalize_tool_platform(platform)
    runtime_preset = normalize_runtime_preset(runtime_preset)
```

DB override 查询用 `or_` 组装：

```python
from sqlalchemy import or_

conditions = [
    (ToolOverride.scope_type == "chat_type") & (ToolOverride.scope_id == chat_type),
]
if platform:
    conditions.append((ToolOverride.scope_type == "platform") & (ToolOverride.scope_id == platform))
if group_id:
    conditions.append((ToolOverride.scope_type == "group") & (ToolOverride.scope_id == group_id))
if user_id:
    conditions.append((ToolOverride.scope_type == "user") & (ToolOverride.scope_id == user_id))

rows = db.query(ToolOverride).filter(or_(*conditions)).all()
for row in sorted(rows, key=lambda r: {
    "chat_type": 1,
    "platform": 2,
    "group": 3,
    "user": 4,
}.get(r.scope_type, 9)):
    ...
```

- [x] **步骤 6：透传 ToolPlan / FinalTools platform**

在 `core/tool_plan.py`：

```python
def build_tool_plan(
    *,
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    db: Any = None,
) -> ToolPlan:
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        runtime_preset=runtime_preset,
        db=db,
    )
```

在 `core/final_tools.py`：

```python
def resolve_final_tools(
    chat_type: str = "group",
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    runtime_preset: str = "full",
    db: Any = None,
) -> FinalToolSet:
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        runtime_preset=runtime_preset,
        db=db,
    )
```

- [x] **步骤 7：运行绿灯和相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_tool_plan.py tests/test_final_tools.py -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 8：提交任务 1**

```bash
git add tests/test_tool_plan.py core/runtime_tool_service.py core/tool_plan.py core/final_tools.py
git commit -m "feat(工具): 支持平台维度解析"
```

## 任务 2：运行时决策记录 platform 并补迁移

**文件：**
- 修改：`tests/test_tool_plan.py`
- 修改：`tests/test_schema_migrations.py`
- 修改：`core/database.py`
- 修改：`core/schema_migrations.py`
- 修改：`core/runtime_tool_service.py`
- 修改：`api/admin_routes.py`

- [x] **步骤 1：编写 decision 写入红灯测试**

修改 `tests/test_tool_plan.py::test_record_runtime_tool_decision_can_use_injected_db`：

```python
record_runtime_tool_decision(
    session_id="s1",
    message_id="m1",
    chat_type="group",
    group_id="g1",
    user_id="u1",
    platform="web",
    runtime_preset="lightweight",
    enabled={"reply": True, "python_sandbox": False},
    disabled={"python_sandbox": "运行时轻量预设"},
    effective_tools=["reply"],
    db=db_session,
)
...
assert row.platform == "web"
```

预期当前失败：`record_runtime_tool_decision()` 不接受 `platform`，或 ORM 字段不存在。

- [x] **步骤 2：编写迁移红灯测试**

在 `tests/test_schema_migrations.py` 追加：

```python
def test_runtime_tool_decision_platform_column_added_to_existing_table(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from core.schema_migrations import run_schema_migrations

    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE runtime_tool_decisions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, "
            "message_id TEXT, "
            "chat_type TEXT, "
            "group_id TEXT, "
            "user_id TEXT, "
            "runtime_preset TEXT, "
            "enabled_tools_json TEXT, "
            "disabled_tools_json TEXT, "
            "disabled_reasons_json TEXT, "
            "effective_tools_json TEXT, "
            "created_at DATETIME"
            ")"
        ))

    run_schema_migrations(engine, db_path=str(db_path))

    columns = {col["name"] for col in inspect(engine).get_columns("runtime_tool_decisions")}
    assert "platform" in columns

    run_schema_migrations(engine, db_path=str(db_path))
    columns_again = {col["name"] for col in inspect(engine).get_columns("runtime_tool_decisions")}
    assert "platform" in columns_again
```

- [x] **步骤 3：编写 Admin decisions 红灯测试**

在 `tests/test_admin_api.py::TestToolAdmin` 增加：

```python
def test_tool_decisions_returns_platform(self, client, auth_header, db_session):
    from core.runtime_tool_service import record_runtime_tool_decision

    record_runtime_tool_decision(
        session_id="s-platform",
        message_id="m1",
        chat_type="private",
        platform="web",
        runtime_preset="full",
        enabled={"reply": True},
        disabled={},
        effective_tools=["reply"],
        db=db_session,
    )
    db_session.commit()

    r = client.get("/api/v1/admin/tools/decisions", headers=auth_header)
    assert r.status_code == 200, r.text
    item = next(x for x in r.json()["items"] if x["session_id"] == "s-platform")
    assert item["platform"] == "web"
```

- [x] **步骤 4：运行红灯**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_tool_plan.py::test_record_runtime_tool_decision_can_use_injected_db tests/test_schema_migrations.py::test_runtime_tool_decision_platform_column_added_to_existing_table tests/test_admin_api.py::TestToolAdmin::test_tool_decisions_returns_platform -v -p no:cacheprovider
```

预期：失败。

- [x] **步骤 5：实现 ORM 字段和写入**

在 `core/database.py`：

```python
class ToolOverride(Base):
    """工具权限覆盖——per-chat_type/per-platform/per-group/per-user 启用/禁用。"""
    ...
    scope_type = Column(String, nullable=False)  # "chat_type" | "platform" | "group" | "user"
```

在 `RuntimeToolDecision` 中添加：

```python
platform = Column(String, default="")
```

在 `record_runtime_tool_decision()` 中添加参数和写入：

```python
platform: str = "",
...
platform=normalize_tool_platform(platform),
```

- [x] **步骤 6：实现 schema migration**

在 `core/schema_migrations.py` 增加：

```python
def _runtime_tool_decision_platform_column(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "runtime_tool_decisions", {
        "platform": "TEXT DEFAULT ''",
    })
```

在 `MIGRATIONS` 末尾追加：

```python
("20260618_runtime_tool_decision_platform", "runtime tool decision platform column", _runtime_tool_decision_platform_column),
```

- [x] **步骤 7：让 Admin decisions 返回 platform**

在 `api/admin_routes.py::list_runtime_preset_decisions()` 的 item 中加入：

```python
"platform": getattr(r, "platform", "") or "",
```

- [x] **步骤 8：运行绿灯和迁移回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_tool_plan.py tests/test_schema_migrations.py tests/test_admin_api.py::TestToolAdmin -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 9：提交任务 2**

```bash
git add tests/test_tool_plan.py tests/test_schema_migrations.py tests/test_admin_api.py core/database.py core/schema_migrations.py core/runtime_tool_service.py api/admin_routes.py
git commit -m "feat(工具): 记录平台维度决策"
```

## 任务 3：真实入口透传 platform 到 Bridge 和 ToolPlan

**文件：**
- 修改：`tests/test_api.py`
- 修改：`tests/test_kt_framework.py`
- 修改：`api/routes.py`
- 修改：`app/group_ingress/service.py`
- 修改：`nanobot_kt/bridge.py`

- [x] **步骤 1：编写 `/chat` 透传红灯测试**

在 `tests/test_api.py` 追加或扩展现有 bridge metadata 测试：

```python
def test_proxy_chat_passes_client_platform_to_bridge(client, monkeypatch, auth_header):
    seen = {}

    class FakeBridge:
        async def handle_message(self, query, session_id, metadata=None, **kwargs):
            seen.update(metadata or {})
            return "ok"

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())

    r = client.post("/api/v1/chat", json={
        "query": "hello",
        "user_id": "u-web",
        "session_id": "s-web",
        "client_meta": {"platform": "web"},
    }, headers=auth_header)
    assert r.status_code == 200, r.text
    assert seen["platform"] == "web"
```

- [x] **步骤 2：编写群聊 Bridge 透传红灯测试**

扩展现有 `test_group_message_passes_client_platform_to_timing_gate` 附近测试：

```python
def test_group_message_passes_client_platform_to_bridge(client, monkeypatch):
    seen = {}

    class FakeBridge:
        async def handle_message(self, query, session_id, metadata=None, **kwargs):
            seen.update(metadata or {})
            return {"action": "reply", "content": "ok"}

    monkeypatch.setattr("app.group_ingress.service.get_bridge", lambda: FakeBridge(), raising=False)

    r = client.post("/api/v1/group/message", json={
        "group_id": "1001",
        "message": "bot 你好",
        "message_id": "m-web",
        "sender_id": "u1",
        "is_at_bot": True,
        "client_meta": {"platform": "web"},
    })
    assert r.status_code == 200, r.text
    assert seen["platform"] == "web"
```

如果现有群聊测试使用 service 级 fake bridge，而不是 patch `get_bridge`，沿用现有 fixture 写法。

- [x] **步骤 3：编写 Bridge 调用 ToolPlan 红灯测试**

在 `tests/test_kt_framework.py` 增加：

```python
@pytest.mark.asyncio
async def test_bridge_passes_platform_to_tool_plan_and_decision(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge

    calls = {}

    def fake_build_tool_plan(**kwargs):
        calls["tool_plan_platform"] = kwargs.get("platform")
        from core.tool_plan import ToolPlan
        return ToolPlan.from_effective_tools(
            enabled={"reply": True, "no_reply": True},
            disabled={},
            chat_type="private",
            tool_schemas=[],
        )

    def fake_record_runtime_tool_decision(**kwargs):
        calls["decision_platform"] = kwargs.get("platform")
        return True

    monkeypatch.setattr("nanobot_kt.bridge.build_tool_plan", fake_build_tool_plan, raising=False)
    monkeypatch.setattr("nanobot_kt.bridge.record_runtime_tool_decision", fake_record_runtime_tool_decision, raising=False)

    bridge = NanobotBridge()
    await bridge.handle_message("hello", session_id="s-web", metadata={"platform": "web"})

    assert calls["tool_plan_platform"] == "web"
    assert calls["decision_platform"] == "web"
```

按当前 `NanobotBridge` 测试夹具调整初始化和 controller fake，保持不联网。

- [x] **步骤 4：运行红灯**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py -k "client_platform_to_bridge" tests/test_kt_framework.py -k "platform_to_tool_plan" -v -p no:cacheprovider
```

预期：失败，platform 没有进入 metadata 或 ToolPlan。

- [x] **步骤 5：实现 `/chat` platform metadata**

在 `api/routes.py` 构造 `bridge_meta` 前解析：

```python
client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
platform = str(client_meta.get("platform") or "qq").strip().lower() or "qq"
```

写入：

```python
"platform": platform,
```

- [x] **步骤 6：实现群聊 `_continue_to_bridge()` platform metadata**

在 `app/group_ingress/service.py` 的 `_continue_to_bridge()` 内解析：

```python
client_meta = req.client_meta if isinstance(req.client_meta, dict) else {}
platform = str(client_meta.get("platform") or "qq").strip().lower() or "qq"
```

写入 `bridge_meta`：

```python
"platform": platform,
```

- [x] **步骤 7：实现 Bridge 透传**

在 `nanobot_kt/bridge.py::handle_message()` 取值：

```python
platform = str(meta.get("platform") or "qq").strip().lower() or "qq"
```

调用：

```python
tool_plan = build_tool_plan(
    chat_type=runtime_chat_type,
    group_id=group_id,
    user_id=user_id,
    platform=platform,
    runtime_preset=runtime_preset,
    db=uow.db,
)
```

记录：

```python
decision_recorded = record_runtime_tool_decision(
    ...,
    platform=platform,
    ...
)
```

- [x] **步骤 8：运行绿灯和入口回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py tests/test_kt_framework.py -v -p no:cacheprovider
```

预期：全部通过。

- [x] **步骤 9：提交任务 3**

```bash
git add tests/test_api.py tests/test_kt_framework.py api/routes.py app/group_ingress/service.py nanobot_kt/bridge.py
git commit -m "feat(消息): 透传客户端平台"
```

## 任务 4：Admin API 支持 platform 覆盖和预览

**文件：**
- 修改：`tests/test_admin_api.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：编写 platform override API 红灯测试**

在 `tests/test_admin_api.py::TestToolAdmin` 增加：

```python
def test_tool_platform_override_can_be_created_and_previewed(self, client, auth_header):
    r = client.put("/api/v1/admin/tools/image_generation/override", json={
        "scope_type": "platform",
        "scope_id": "web",
        "enabled": False,
        "reason": "Web 禁用图片生成",
    }, headers=auth_header)
    assert r.status_code == 200, r.text

    effective = client.get(
        "/api/v1/admin/tools/effective",
        params={"chat_type": "private", "platform": "web"},
        headers=auth_header,
    )
    assert effective.status_code == 200, effective.text
    data = effective.json()
    assert data["platform"] == "web"
    assert data["enabled"].get("image_generation") is None
    assert data["disabled"]["image_generation"] == "Web 禁用图片生成"
```

- [ ] **步骤 2：编写 `/tools` 和 targets 红灯测试**

继续追加：

```python
def test_tools_list_reports_platform_override_and_targets(self, client, auth_header):
    client.put("/api/v1/admin/tools/image_generation/override", json={
        "scope_type": "platform",
        "scope_id": "web",
        "enabled": False,
        "reason": "Web 禁用图片生成",
    }, headers=auth_header)

    tools = client.get("/api/v1/admin/tools", params={"platform": "web"}, headers=auth_header)
    assert tools.status_code == 200, tools.text
    data = tools.json()
    assert data["platform"] == "web"
    item = next(x for x in data["tools"] if x["name"] == "image_generation")
    assert item["override_present"] is True
    assert item["override_enabled"] is False
    assert item["runtime_effective"] is False

    targets = client.get(
        "/api/v1/admin/tools/targets",
        params={"scope_type": "platform"},
        headers=auth_header,
    )
    ids = {item["id"] for item in targets.json()["items"]}
    assert {"qq", "web", "synergy"}.issubset(ids)
```

- [ ] **步骤 3：运行红灯**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_admin_api.py::TestToolAdmin -k "platform" -v -p no:cacheprovider
```

预期：失败，API 拒绝 platform 或预览未传 platform。

- [ ] **步骤 4：实现 Admin 校验和归一化**

在 `api/admin_routes.py` 引入 `normalize_tool_platform` 并调整校验：

```python
allowed_scope_types = {"group", "user", "chat_type", "platform"}
if body.scope_type not in allowed_scope_types:
    raise HTTPException(400, "scope_type must be group/user/chat_type/platform")
if body.scope_type == "platform":
    body.scope_id = normalize_tool_platform(body.scope_id)
    if not body.scope_id:
        raise HTTPException(400, "scope_id required for platform scope")
```

如果 Pydantic model 不允许直接写 `body.scope_id`，使用局部变量 `scope_id` 并在查询和写入中使用该变量。

- [ ] **步骤 5：实现 `/tools` platform preview**

给 `list_tools()` 增加参数：

```python
platform: str = "qq",
```

归一化后传入两次 `resolve_effective_tools()`。返回顶层加入：

```python
"platform": platform,
```

override state 的 scope 选择顺序：

```python
if user_id:
    override_scope_type = "user"
    override_scope_id = str(user_id).strip()
elif group_id:
    override_scope_type = "group"
    override_scope_id = str(group_id).strip()
elif platform:
    override_scope_type = "platform"
    override_scope_id = platform
```

- [ ] **步骤 6：实现 `/tools/effective` 和 `/tools/targets`**

`get_effective_tools()` 增加 platform 参数并返回。

`list_tool_targets()` 对 `scope_type="platform"` 直接返回：

```python
items = [
    {"id": "qq", "label": "QQ (qq)", "name": "QQ", "scope_type": "platform", "source": "builtin", "recent_at": ""},
    {"id": "web", "label": "Web (web)", "name": "Web", "scope_type": "platform", "source": "builtin", "recent_at": ""},
    {"id": "synergy", "label": "Synergy (synergy)", "name": "Synergy", "scope_type": "platform", "source": "builtin", "recent_at": ""},
]
```

再合并已有 `ToolOverride(scope_type="platform")` 的 `scope_id`，避免重复。

- [ ] **步骤 7：运行绿灯和 Admin 回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_admin_api.py::TestToolAdmin -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 8：提交任务 4**

```bash
git add tests/test_admin_api.py api/admin_routes.py
git commit -m "feat(工具): 支持平台覆盖接口"
```

## 任务 5：WebUI 工具页增加 platform selector

**文件：**
- 修改：`webui/src/features/tools/ToolsPage.jsx`
- 修改：`tests/test_webui_admin_redesign.py` 或 `tests/test_webui_app_split.py`

- [ ] **步骤 1：编写 WebUI 红灯测试**

在已有 WebUI 静态测试中追加：

```python
def test_tools_page_exposes_platform_scope_controls():
    source = Path("webui/src/features/tools/ToolsPage.jsx").read_text(encoding="utf-8")

    assert "tool-platform-select" in source
    assert "指定平台" in source
    assert "platform:" in source
    assert "scope_type: 'platform'" in source or 'scope_type: "platform"' in source
```

如果测试文件未 import `Path`，在文件顶部添加：

```python
from pathlib import Path
```

- [ ] **步骤 2：运行红灯**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py -k "tools_page_exposes_platform" -v -p no:cacheprovider
```

预期：失败，页面没有 platform 控件。

- [ ] **步骤 3：实现 ToolsPage 状态和 API 参数**

在 `ToolsPage.jsx` 添加：

```javascript
const platformOptions = [
  { key: 'qq', label: 'QQ' },
  { key: 'web', label: 'Web' },
  { key: 'synergy', label: 'Synergy' },
]
const [platform, setPlatform] = useState('qq')
```

`api.get('/tools')` 参数加入：

```javascript
platform,
```

`load` 的 dependency 加入 `platform`。

- [ ] **步骤 4：实现覆盖对象 platform**

`overrideScope` 下拉新增：

```jsx
<option value="platform">指定平台</option>
```

`scopeForTab()` 支持：

```javascript
if (overrideScope === 'platform') {
  return {
    scope_type: 'platform',
    scope_id: platform.trim(),
  }
}
```

`loadTargets()` 继续调用 `/tools/targets?scope_type=platform`，并在选择 target 时设置 `platform`：

```javascript
if (overrideScope === 'platform') {
  setPlatform(target.id)
}
```

- [ ] **步骤 5：实现 UI 控件**

在默认模板和指定覆盖区域上方加入：

```jsx
<label htmlFor="tool-platform-select" className="text-xs text-slate-500">
  预览平台
  <select id="tool-platform-select" value={platform} onChange={e => setPlatform(e.target.value)}
    className="mt-1 block min-w-[140px] rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
    {platformOptions.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
  </select>
</label>
```

保持现有布局，不新增大面积页面结构。

- [ ] **步骤 6：运行 WebUI 静态测试**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py tests/test_webui_app_split.py -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 7：运行 WebUI 构建**

```bash
cd webui && npm run build
```

预期：构建成功。Vite chunk size warning 可记录，不作为失败。

- [ ] **步骤 8：提交任务 5**

```bash
git add webui/src/features/tools/ToolsPage.jsx tests/test_webui_admin_redesign.py tests/test_webui_app_split.py
git commit -m "feat(工具): 配置平台覆盖"
```

只暂存实际修改过的测试文件；如果只改了其中一个测试文件，不暂存另一个。

## 任务 6：文档收口与最终验证

**文件：**
- 修改：`docs/message-field-standard.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/tool-platform-scope.md`

- [ ] **步骤 1：同步消息字段标准**

在 `docs/message-field-standard.md` 的 `client_meta.platform` 附近补充：

```markdown
工具策略解析使用标准化后的 `client_meta.platform`。缺省值为 `qq`，用于兼容现有 QQ / NapCat 调用；新平台 adapter 必须显式传入平台名。
```

- [ ] **步骤 2：同步 TODO 路线项 4**

在 `docs/todo.md` 的路线项 4 里把 P2-1 状态改为已落地口径，至少包含：

- `ToolOverride(scope_type="platform")` 已支持。
- `RuntimeToolDecision.platform` 已写入。
- `/chat`、`/group/message`、Bridge 和 Admin API 已透传 platform。
- WebUI 工具页可配置平台覆盖。

- [ ] **步骤 3：同步 plan walkthrough 和本计划**

在 `docs/plan_walkthrough.md`：

- 把 P2-1 状态从「设计中」改为「已完成」。
- 记录每个任务提交号。
- 记录最终验证命令和输出。
- 下一优先级切到 P2-2「标准化请求 / 响应信封」。

在 `.Codex/plans/tool-platform-scope.md`：

- 勾选已完成任务。
- 记录每个任务提交号。
- 记录最终验证输出。

- [ ] **步骤 4：运行文档扫描**

```bash
python - <<'PY'
from pathlib import Path

paths = [
    Path("docs/message-field-standard.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/tool-platform-scope.md"),
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
git diff --check -- docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/tool-platform-scope.md
```

预期：`rg` 无输出，`git diff --check` 无输出。

- [ ] **步骤 5：运行 P2-1 定向回归**

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_tool_plan.py \
  tests/test_admin_api.py::TestToolAdmin \
  tests/test_schema_migrations.py \
  tests/test_api.py \
  tests/test_kt_framework.py \
  tests/test_webui_admin_redesign.py \
  tests/test_webui_app_split.py \
  -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 6：运行全量测试**

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 7：提交文档收口**

```bash
git add docs/message-field-standard.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/tool-platform-scope.md
git commit -m "docs(计划): 同步工具平台配置状态"
```

## 执行顺序

1. 任务 1：后端解析支持 platform scope。
2. 任务 2：审计记录和迁移。
3. 任务 3：真实入口透传 platform。
4. 任务 4：Admin API 支持平台覆盖。
5. 任务 5：WebUI 工具页配置平台覆盖。
6. 任务 6：文档收口与最终验证。

每个任务完成后必须运行对应验证命令并单独提交。禁止使用 `git add .` 或 `git add -A`。

## 最终验收清单

- [x] `resolve_effective_tools(platform="web")` 能应用 `ToolOverride(scope_type="platform", scope_id="web")`。
- [x] precedence 固定为 `chat_type < platform < group < user`，并由测试覆盖。
- [x] `runtime_preset=none`、`force_enabled`、群聊 `force_disabled_group` 不会被 platform override 绕过。
- [x] `RuntimeToolDecision.platform` 在新库和旧库迁移后都存在，并能通过 `/tools/decisions` 查询。
- [x] `/chat` 和 `/group/message` 都把 `client_meta.platform` 传到 Bridge。
- [x] Bridge 把 platform 传给 `build_tool_plan()` 和 `record_runtime_tool_decision()`。
- [ ] Admin API 能创建、删除、预览 platform override。
- [ ] WebUI 工具页能选择 platform 并配置「指定平台」覆盖。
- [ ] `docs/message-field-standard.md`、`docs/todo.md`、`docs/plan_walkthrough.md` 和本计划同步当前状态。
- [ ] 全量测试通过。
