# Prompt legacy 收口实现计划

计划日期：2026-06-17
状态：进行中，任务 1 / 2 已完成，任务 3 待执行

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 收口 Prompt legacy live 路径，让默认主回复、管理预览和评估入口都以 V2 为主，禁用 V2 audit 失败后的自动 V1 发送回退。

**架构：** P1-5 不删除 legacy 资产，只把它们降级为显式应急回滚或只读迁移材料。运行时保留 `prompt_runtime.engine=v1` 这类显式应急回滚入口，但 V2 audit 失败不得再自动 fallback 到 V1；管理面和评估面默认不再静默走 V1。

**技术栈：** Python 3.12、FastAPI、pytest、Prompt Runtime V2、现有 WebUI React/Vite 测试。

---

## 范围边界

本阶段要做：

- 禁用 live `fallback_v1` 发送路径。
- V2 路径下 `PromptRuntimeInput.prompt_mode` 统一为 `v2`，不再携带 V1 fallback prompt mode。
- `reply-test` 和 `reply-eval` 的默认与旧无版本 alias 收敛到 V2。
- legacy / managed prompt 管理入口降级为只读迁移入口，写操作需要明确阻断或隐藏。
- 文档明确 P1-5 后 legacy 资产仍存在，但不再是默认 live / 管理 / 评估路径。

本阶段不做：

- 不删除 `core/prompt_assembler.py`。
- 不删除 `core/legacy_prompt_runtime.py`。
- 不删除 `core/prompt_runtime.py`。
- 不删除 `prompts.default/`、`prompts.legacy.default/`、`creatures/nanobot/prompt.md`、`scripts/build_nanobot_prompt.py`。
- 不修改历史 `AgentRun` / trace 表结构。
- 不做 `prompt_v2` 去版本命名；那属于 P1-6 或后续阶段。

## 文件结构

- 修改：`nanobot_kt/prompt_runtime.py`
  - 禁用 V2 audit 失败后的 `_build_v1_prompt()` fallback。
  - 保留显式 `prompt_engine != "v2"` 的 V1 应急回滚入口。
- 修改：`nanobot_kt/bridge.py`
  - `_prompt_v2_audit_failure_policy()` 不再返回 `fallback_v1`。
  - `_build_prompt_runtime_input()` 在 V2 下写入 `prompt_mode="v2"`。
- 修改：`core/config_registry.py`
  - 更新 `prompt_runtime.v2_audit_failure_policy` 描述，标记 `fallback_v1` 已废弃且不再生效。
- 修改：`core/prompt_assembler.py`
  - 更新模块注释，删除“V2 live audit 失败时回退”的过时说法。
- 修改：`api/admin_routes.py`
  - reply-test / reply-eval 旧 alias 默认映射到 V2。
  - V2 请求不再下发 `prompt_system_mode_override="legacy"`。
  - legacy / managed prompt 写操作降级为只读迁移入口。
- 修改：`webui/src/App.jsx`
  - 将 legacy prompt 页面入口标记为迁移 / 只读，或从主导航移除。
- 修改：`webui/src/features/prompt/PromptPages.jsx`
  - 禁用 legacy / managed 写操作按钮，保留只读查看和导出。
- 修改：`tests/test_bridge_prompt_v2.py`
  - 改写 fallback 测试：即使配置 `fallback_v1`，也必须 fail-fast。
  - 更新 V2 `PromptRuntimeInput.prompt_mode` 断言。
- 修改：`tests/test_reply_admin.py`
  - 更新 reply-test / reply-eval 默认和 alias 映射。
- 修改：`tests/test_prompt_v2_template_admin.py`
  - 确认 V2 effective preview 不调用 `PromptAssembler`，并补充 V1 preview 被迁移提示拦截的测试。
- 修改：`tests/test_prompt_manifest.py`
  - 保持 V2 active / V1 rollback_only，增加 `fallback_v1` 不再是 active strategy 的断言。
- 修改：`tests/test_webui_prompt_runtime_ui.py`
  - 验证 legacy 页面不再作为主编辑入口。
- 修改：`docs/plan_walkthrough.md`
  - 标记 P1-5 执行状态和验证结果。
- 修改：`docs/todo.md`
  - P1-5 完成后同步路线项 1 的实施状态。
- 创建或修改：`.Codex/plans/prompt-legacy-convergence.md`
  - 记录本阶段执行计划。

## 任务 1：运行时禁用 `fallback_v1` live 发送路径

**文件：**
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`nanobot_kt/prompt_runtime.py`
- 修改：`nanobot_kt/bridge.py`
- 修改：`core/config_registry.py`
- 修改：`core/prompt_assembler.py`

- [x] **步骤 1：改写 V2 audit fallback 红灯测试**

在 `tests/test_bridge_prompt_v2.py` 中将现有 `test_bridge_engine_v2_can_fallback_to_v1_when_audit_policy_allows` 改名并改写为：

```python
@pytest.mark.asyncio
async def test_bridge_engine_v2_ignores_fallback_v1_policy_when_audit_fails(monkeypatch, db_session):
    from core import database
    from core.database import AgentRun
    from core.prompt_v2.audit import PromptAuditError
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_AUDIT_FAILURE_POLICY", "fallback_v1")
    settings.invalidate()

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}
    seen_events = []

    async def fake_process_event(event):
        seen_events.append(event)
        bridge._output._buffer.append('{"action":"reply","content":"不应发送"}')
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    async def fake_compile(*_args, **kwargs):
        assert kwargs.get("strict_audit") is True
        raise PromptAuditError(["runtime_tool_prompt must appear once, got 0"])

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr(
        "core.prompt_assembler.PromptAssembler.build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("V2 audit failure must not fallback to PromptAssembler")
        ),
    )

    result = await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="group_1003",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "group",
            "is_group": True,
            "group_id": "1003",
            "runtime_preset": "none",
            "enable_reply_contract_retry": False,
        },
    )

    assert result == ""
    assert seen_events == []
    assert bridge.pop_last_reply_meta("group_1003")["_agent_result"] == "prompt_v2_audit_failed"
    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1003").first()
    assert run is not None
    assert '"prompt_v2_audit_failed": true' in run.meta_json
    assert "prompt_fallback" not in run.meta_json
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_ignores_fallback_v1_policy_when_audit_fails \
  -q -p no:cacheprovider
```

预期：失败。当前代码会在 `fallback_v1` 策略下调用 `PromptAssembler`，测试应因 `AssertionError("V2 audit failure must not fallback to PromptAssembler")` 失败。

实际：红灯集合 `4 failed, 1 warning`，失败原因覆盖 V2 `prompt_mode` 仍为旧 mode、`fallback_v1` 仍调用 `PromptAssembler`、配置描述仍宣传 `fallback_v1`。

- [x] **步骤 3：让 `_prompt_v2_audit_failure_policy()` 固定 fail-fast**

在 `nanobot_kt/bridge.py` 中把 helper 收敛为：

```python
    def _prompt_v2_audit_failure_policy(self) -> str:
        try:
            from core.settings_service import settings

            policy = str(
                settings.get("prompt_runtime.v2_audit_failure_policy", "fail_fast")
                or "fail_fast"
            ).strip().lower()
        except Exception:
            policy = "fail_fast"
        if policy == "fallback_v1":
            logger.warning("[PromptV2] fallback_v1 audit policy is deprecated; using fail_fast")
        return "fail_fast"
```

- [x] **步骤 4：删除 V2 audit fallback 发送分支**

在 `nanobot_kt/prompt_runtime.py` 中删除 `if input.audit_failure_policy != "fallback_v1"` 之后的 fallback 构造，改为始终抛出 `PromptRuntimeAuditFailure`：

```python
    except PromptAuditError as exc:
        audit_issues = list(getattr(exc, "issues", []) or [str(exc)])
        meta_update = {
            "prompt_engine": "v2",
            "prompt_v2_audit_failed": True,
            "audit_issues": audit_issues,
        }
        raise PromptRuntimeAuditFailure(
            f"Prompt Runtime V2 审计失败: {exc}",
            meta_update=meta_update,
        ) from exc
```

- [x] **步骤 5：V2 input 不再携带 V1 prompt mode**

在 `nanobot_kt/bridge.py` 的 `_build_prompt_runtime_input()` 中保留 V1 prompt mode 解析，但只用于 `context.prompt_engine != "v2"`：

```python
        return PromptRuntimeInput(
            prompt_engine=context.prompt_engine,
            prompt_mode="v2" if context.prompt_engine == "v2" else context.prompt_mode,
            ...
        )
```

同步更新 `tests/test_bridge_prompt_v2.py::test_bridge_build_prompt_runtime_input_for_v2`：

```python
assert prompt_input.prompt_mode == "v2"
```

- [x] **步骤 6：更新配置和注释**

在 `core/config_registry.py` 中将描述改为：

```python
description="Prompt Runtime V2 live audit 失败策略；fallback_v1 已废弃，运行时固定 fail_fast",
```

在 `core/prompt_assembler.py` 顶部注释中删除“V2 live audit 失败时”的表述，改为：

```python
本模块仅保留给显式 V1 应急回滚、迁移对比和旧测试兼容使用。
新增提示词行为必须使用 `core.prompt_v2.compile_prompt_plan`。
```

- [x] **步骤 7：运行任务 1 定向测试**

运行：

```bash
python -B -m pytest \
  tests/test_bridge_prompt_v2.py \
  tests/test_prompt_manifest.py \
  tests/test_prompt_runtime_bootstrap.py \
  -q -p no:cacheprovider
```

预期：全部通过。

实际：`16 passed, 1 warning`。

- [x] **步骤 8：提交任务 1**

```bash
git add nanobot_kt/prompt_runtime.py nanobot_kt/bridge.py core/config_registry.py core/prompt_assembler.py tests/test_bridge_prompt_v2.py tests/test_prompt_manifest.py
git commit -m "refactor(提示词): 禁用旧版审计回退"
```

实际：已提交 `refactor(提示词): 禁用旧版审计回退`。

## 任务 2：reply-test 和 reply-eval 默认转向 V2-only

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`tests/test_reply_admin.py`

- [x] **步骤 1：更新 reply-test 设置红灯测试**

在 `tests/test_reply_admin.py` 中更新默认断言：

```python
def test_reply_test_request_defaults_to_prompt_v2():
    from api.admin_routes import ReplyTestRunRequest, _resolve_reply_test_prompt_settings

    body = ReplyTestRunRequest(message="你在吗")

    assert body.prompt_engine == "v2"
    assert body.variant == "v2_code_retry"
    assert _resolve_reply_test_prompt_settings(body) == ("v2", "v2", True)
```

新增旧 alias 不再静默回 V1 的测试：

```python
def test_reply_test_old_variants_map_to_v2_by_default():
    from api.admin_routes import ReplyTestRunRequest, _resolve_reply_test_prompt_settings

    assert _resolve_reply_test_prompt_settings(
        ReplyTestRunRequest(message="你在吗", variant="baseline")
    ) == ("v2", "v2", False)
    assert _resolve_reply_test_prompt_settings(
        ReplyTestRunRequest(message="你在吗", variant="prompt_only")
    ) == ("v2", "v2", False)
    assert _resolve_reply_test_prompt_settings(
        ReplyTestRunRequest(message="你在吗", variant="code_retry")
    ) == ("v2", "v2", True)
```

- [x] **步骤 2：更新 reply-eval 默认红灯测试**

在 `tests/test_reply_admin.py::test_reply_eval_case_crud_preview_and_run` 中将 run 请求从：

```python
json={"variant": "code_retry", "case_ids": ["reply_case_manual"]}
```

改成：

```python
json={"case_ids": ["reply_case_manual"]}
```

并更新断言：

```python
assert run_data["variant"] == "v2_code_retry"
assert run_data["results"][0]["prompt_sha256"] == "v" * 64
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_reply_admin.py::test_reply_test_request_defaults_to_prompt_v2 \
  tests/test_reply_admin.py::test_reply_test_old_variants_map_to_v2_by_default \
  tests/test_reply_admin.py::test_reply_eval_case_crud_preview_and_run \
  -q -p no:cacheprovider
```

预期：失败。当前 `_resolve_reply_test_prompt_settings()` 返回 `("v2", "legacy", True)`，旧 alias 会静默映射到 V1，`ReplyEvalRunIn` 默认仍是 `code_retry`。

实际：`5 failed, 20 warnings`，失败点覆盖默认 V2 prompt mode、旧 alias、V2 metadata 和 reply-eval 默认 variant。

- [x] **步骤 4：改 `_resolve_reply_test_prompt_settings()`**

在 `api/admin_routes.py` 中将旧 alias 默认映射到 V2：

```python
    if variant == "prompt_only":
        if not explicit_prompt_engine:
            engine = "v2"
        prompt_mode = "v2" if engine == "v2" else "managed"
        enable_retry = False
    elif variant == "code_retry":
        if not explicit_prompt_engine:
            engine = "v2"
        prompt_mode = "v2" if engine == "v2" else "legacy"
        enable_retry = enable_retry
    elif variant == "baseline":
        if not explicit_prompt_engine:
            engine = "v2"
        prompt_mode = "v2" if engine == "v2" else "legacy"
        enable_retry = False
    elif variant == "v1_baseline":
        engine = "v1"
        prompt_mode = "legacy"
        enable_retry = False
    elif variant == "v2_prompt_only":
        engine = "v2"
        prompt_mode = "v2"
        enable_retry = False
    elif variant == "v2_code_retry":
        engine = "v2"
        prompt_mode = "v2"
        enable_retry = enable_retry
```

- [x] **步骤 5：V2 metadata 不再下发 V1 prompt mode**

在 `_run_reply_test_once()` 构造 metadata 后只在 V1 时加入 `prompt_system_mode_override`：

```python
    metadata = {
        ...
        "prompt_runtime_engine_override": prompt_engine,
        "enable_reply_contract_retry": enable_retry,
        "dry_run": bool(body.dry_run),
    }
    if prompt_engine == "v1":
        metadata["prompt_system_mode_override"] = prompt_mode
```

同步测试中 `captured[-1]` 的断言：

```python
assert captured[-1]["prompt_runtime_engine_override"] == "v2"
assert "prompt_system_mode_override" not in captured[-1]
```

- [x] **步骤 6：修改 ReplyEvalRunIn 默认 variant**

在 `api/admin_routes.py` 中将 `ReplyEvalRunIn.variant` 默认值从 `"code_retry"` 改为 `"v2_code_retry"`：

```python
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "v2_code_retry"
```

- [x] **步骤 7：运行任务 2 定向测试**

运行：

```bash
python -B -m pytest tests/test_reply_admin.py -q -p no:cacheprovider
```

预期：全部通过。

实际：补充显式 V1 应急入口回归后，`tests/test_reply_admin.py` 通过，`14 passed, 20 warnings`。

- [x] **步骤 8：提交任务 2**

```bash
git add api/admin_routes.py tests/test_reply_admin.py
git commit -m "refactor(评测): 默认使用 V2 回复评估"
```

实际：已提交 `refactor(评测): 默认使用 V2 回复评估`。

## 任务 3：管理面 legacy 入口降级为只读迁移入口

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`tests/test_prompt_trace_admin.py`
- 修改：`tests/test_prompt_v2_template_admin.py`
- 修改：`tests/test_webui_prompt_runtime_ui.py`
- 修改：`webui/src/App.jsx`
- 修改：`webui/src/features/prompt/PromptPages.jsx`

- [ ] **步骤 1：为 V1 effective-preview 写红灯测试**

在 `tests/test_prompt_v2_template_admin.py` 新增测试：

```python
def test_effective_preview_rejects_v1_engine(tmp_path, monkeypatch):
    def fail_assembler(*_args, **_kwargs):
        raise AssertionError("V1 effective preview must not call PromptAssembler after P1-5")

    monkeypatch.setattr("core.prompt_assembler.PromptAssembler.build", fail_assembler)

    response = client.post(
        "/api/v1/admin/prompt/effective-preview",
        json={
            "engine": "v1",
            "chat_type": "group",
            "session_id": "group_1001",
            "user_id": "u1",
            "group_id": "1001",
            "prompt_key": "group_chat",
            "mode": "managed",
            "user_input": "LEGACY_PREVIEW_MARKER",
        },
        headers=_auth_header(),
    )

    assert response.status_code == 410
    assert "Prompt V1" in response.text
```

如果该文件没有可复用 `client` fixture，则沿用文件内已有 `TestClient(app)` 和 `override_get_db()` 写法。

- [ ] **步骤 2：为 legacy 写接口阻断写红灯测试**

在 `tests/test_prompt_trace_admin.py` 或新增 `tests/test_prompt_legacy_admin_readonly.py` 中添加：

```python
def test_legacy_prompt_write_endpoints_are_readonly(client, auth_header):
    fragment = client.put(
        "/api/v1/admin/prompt/fragments/00_identity.md",
        headers=auth_header,
        json={"content": "不应写入"},
    )
    assert fragment.status_code == 410

    build = client.post("/api/v1/admin/prompt/build", headers=auth_header)
    assert build.status_code == 410

    reset = client.post("/api/v1/admin/prompt/reset", headers=auth_header)
    assert reset.status_code == 410
```

- [ ] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_prompt_v2_template_admin.py::test_effective_preview_rejects_v1_engine \
  tests/test_prompt_legacy_admin_readonly.py::test_legacy_prompt_write_endpoints_are_readonly \
  -q -p no:cacheprovider
```

预期：失败。当前 V1 preview 会调用 `PromptAssembler`，legacy 写接口仍可写。

- [ ] **步骤 4：新增 legacy 只读错误 helper**

在 `api/admin_routes.py` helper 区加入：

```python
def _legacy_prompt_write_disabled() -> HTTPException:
    return HTTPException(
        status_code=410,
        detail="Legacy prompt 编辑已降级为只读迁移入口；请改用 Prompt Runtime V2 模板",
    )
```

- [ ] **步骤 5：拦截 V1 effective-preview**

在 `preview_effective_prompt()` 中处理 `body.engine != "v2"`：

```python
    if body.engine != "v2":
        raise HTTPException(
            status_code=410,
            detail="Prompt V1 effective preview 已降级为只读迁移入口；请使用 engine=v2",
        )
```

保留历史 trace 查看接口，不要改 `agent-runs` 或 `prompt-render-logs` 读取逻辑。

- [ ] **步骤 6：拦截 legacy 写接口**

给这些写接口增加：

```python
    raise _legacy_prompt_write_disabled()
```

需要拦截的接口包括：

- `PUT /prompts/{prompt_key}`
- prompt rollback 写接口
- `PUT /prompt/fragments/{name}`
- `POST /prompt/build`
- `POST /prompt/reset`
- `POST /prompt/init`
- legacy backup rollback 写接口

GET /prompt、GET /prompt/fragments、GET /prompt/fragments/{name}/default、GET history 类接口保留。

- [ ] **步骤 7：更新 WebUI legacy 入口**

在 `webui/src/App.jsx` 中把导航文案从“Legacy 回滚”改为“Legacy 迁移只读”，或从主导航移除。

在 `webui/src/features/prompt/PromptPages.jsx` 中禁用写按钮，显示只读状态。按钮禁用示例：

```jsx
<button className="btn" disabled title="Legacy prompt 已降级为只读迁移入口">
  保存
</button>
```

- [ ] **步骤 8：运行任务 3 定向测试**

运行：

```bash
python -B -m pytest \
  tests/test_prompt_v2_template_admin.py \
  tests/test_prompt_trace_admin.py \
  tests/test_webui_prompt_runtime_ui.py \
  -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 9：提交任务 3**

```bash
git add api/admin_routes.py tests/test_prompt_v2_template_admin.py tests/test_prompt_trace_admin.py tests/test_prompt_legacy_admin_readonly.py tests/test_webui_prompt_runtime_ui.py webui/src/App.jsx webui/src/features/prompt/PromptPages.jsx
git commit -m "refactor(提示词): 降级旧版管理入口"
```

## 任务 4：同步文档和后续删除清单

**文件：**
- 修改：`docs/plan_walkthrough.md`
- 修改：`docs/todo.md`
- 修改：`docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md`
- 修改：`.Codex/plans/prompt-legacy-convergence.md`

- [ ] **步骤 1：同步 `docs/todo.md` 路线项 1**

在路线项 1 的实施状态中加入：

```markdown
P1-5 已完成：live `fallback_v1` 发送路径已禁用，reply-test / reply-eval 默认转向 V2，legacy / managed 管理写入口降级为只读迁移入口。显式 `prompt_runtime.engine=v1` 应急回滚仍保留，legacy 资产删除延后到 P1-6。
```

- [ ] **步骤 2：同步 `docs/plan_walkthrough.md`**

将 P1-5 状态改为已完成，并把下一步候选改为 P1-6：

```markdown
| P1-5 | 已完成 | Prompt legacy 收口 | 已禁用 live fallback_v1，评估 / 管理入口转向 V2，legacy 页面只读 | `refactor(提示词): 收敛旧版回退路径` |
| P1-6 | 待写计划 | 删除冗余提示词资产并去版本化 | 迁移后台 task prompt，删除 V1 / legacy 冗余资产，去掉 V2 命名后缀 | `refactor(提示词): 统一提示词运行时命名` |
```

- [ ] **步骤 3：同步默认接管设计文档**

在 `docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md` 的后续步骤中标记：

```markdown
补充（P1-5）：`fallback_v1` 已从 live 路径移除；V2 audit 失败统一 fail-fast。显式 V1 应急回滚入口暂保留到 P1-6 资产删除阶段前。
```

- [ ] **步骤 4：记录 P1-6 前置迁移清单**

在本计划末尾或 `docs/plan_walkthrough.md` 增加 P1-6 前置清单：

- 迁移 `clients/classifier_client.py` 的 `timing_gate/private_decision/classifier_legacy` 到 V2 task 模板。
- 迁移 `core/legacy_adapter.py` 的 `memory_extract` 到 V2 task 模板。
- 清点 `prompts.default/group_analysis*.md` 和 `sql_analysis.md` 是否仍被 live 读取。
- 迁移 `data/prompts/`、`data/prompt_fragments/`、`data/runtime_prompt/prompt.md` 中仍有价值的运行时文案。
- 移除 `creatures/nanobot/config.yaml` 对 `prompt.md` 的依赖后，才能删除 `creatures/nanobot/prompt.md`。
- 删除旧 admin 写接口和旧 WebUI 页面前，保留导出 / 迁移说明。

- [ ] **步骤 5：文档格式检查**

运行：

```bash
git diff --check -- \
  docs/plan_walkthrough.md \
  docs/todo.md \
  docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md \
  .Codex/plans/prompt-legacy-convergence.md
```

预期：无输出。

- [ ] **步骤 6：提交任务 4**

```bash
git add docs/plan_walkthrough.md docs/todo.md docs/superpowers/specs/2026-06-17-prompt-v2-default-cutover-design.md .Codex/plans/prompt-legacy-convergence.md
git commit -m "docs(提示词): 同步旧版收口状态"
```

## 任务 5：最终验证

**文件：**
- 不新增文件；验证本阶段全部改动。

- [ ] **步骤 1：运行 P1-5 定向回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_bridge_prompt_v2.py \
  tests/test_prompt_runtime_bootstrap.py \
  tests/test_prompt_manifest.py \
  tests/test_prompt_v2.py \
  tests/test_prompt_v2_template_admin.py \
  tests/test_prompt_trace_admin.py \
  tests/test_reply_admin.py \
  tests/test_webui_prompt_runtime_ui.py \
  -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 2：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [ ] **步骤 3：检查禁止项**

运行：

```bash
rg -n 'prompt_fallback|fallback_v1|PromptAssembler\\.build' nanobot_kt api tests docs core -g '!**/__pycache__/**'
```

预期：

- `prompt_fallback` 不再出现在 live V2 成功 / audit 失败断言中。
- `fallback_v1` 只允许出现在废弃配置说明、迁移文档或负向测试中。
- `PromptAssembler.build` 只允许出现在显式 V1 rollback、只读迁移、旧测试或负向守卫测试中。

- [ ] **步骤 4：最终提交状态检查**

运行：

```bash
git status --short
git log --oneline -5
```

预期：本阶段文件已分阶段提交；工作区只剩与本阶段无关的既有脏文件。
