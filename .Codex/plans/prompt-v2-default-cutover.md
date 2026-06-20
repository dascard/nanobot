# Prompt V2 默认接管实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

> **状态校准（2026-06-20）：** 本文件是 P1-3「Prompt V2 默认接管」的历史执行计划。实际实现、文档同步和验收均已完成；权威当前状态见 `docs/plan_walkthrough.md` 的 P1-3 进度表和「已完成阶段详情：Prompt V2 默认接管」。下方未勾选复选框保留为原始计划文本，不再表示当前待办。

**目标：** 让 Prompt V2 成为默认 live prompt 路径，同时保留显式 V1 回滚入口，并让启动初始化、admin preview、reply-test 与默认运行路径一致。

**架构：** 配置层把 `prompt_runtime.engine` 默认值切到 `v2`；bridge 层统一把缺省值、异常值和非法值回落到 V2，但 metadata / DB / env 显式 `v1` 仍走旧路径。启动层初始化 `data/prompts_v2`，只复制缺失模板，不覆盖运行时修改；admin 层把 preview 和 reply-test 默认入口切到 V2。

**技术栈：** Python 3.13、pytest、FastAPI / pydantic、SQLAlchemy in-memory SQLite、现有 `core.settings_service`、`nanobot_kt.prompt_runtime`、`core.prompt_v2`。

---

## 文件结构

- 修改：`core/config_registry.py`
  - 将 `prompt_runtime.engine` 注册默认值从 `v1` 改为 `v2`。
- 修改：`nanobot_kt/bridge.py`
  - 将 `_prompt_runtime_engine()` 与 `handle_message()` 内非法 engine fallback 改为 V2。
  - 保留 `prompt_runtime_engine_override="v1"` 和 `prompt_engine_override="v1"` 显式回滚。
- 修改：`core/prompt_v2/template_registry.py`
  - 新增 `init_prompt_v2_runtime_dir()`，复制 `prompts.v2.default` 中缺失的 `.md` / `.json` 文件到 `data/prompts_v2`。
  - 不覆盖已有 runtime 文件。
- 修改：`bootstrap/prompt_runtime.py`
  - 启动时调用 V2 runtime 初始化。
  - 若当前有效 `prompt_runtime.engine` 为 `v1`，记录显式回滚 warning。
- 修改：`api/admin_routes.py`
  - `EffectivePromptPreviewRequest.engine` 默认改为 `v2`。
  - `ReplyTestRunRequest.prompt_engine` 默认改为 `v2`，`variant` 默认改为 `v2_code_retry`。
  - `_resolve_reply_test_prompt_settings()` 的空值和非法值 fallback 改为 V2。
- 修改：`tests/test_bridge_prompt_v2.py`
  - 覆盖无 override 默认走 V2。
  - 覆盖显式 V1 override 仍走 `PromptAssembler`。
  - 覆盖非法 engine override 回落 V2。
- 修改：`tests/test_prompt_manifest.py`
  - 覆盖 manifest active engine 与 config registry 默认值一致。
- 修改：`tests/test_prompt_v2_template_registry.py`
  - 覆盖 V2 runtime 初始化复制缺失模板且不覆盖已有 runtime 文件。
- 创建：`tests/test_prompt_runtime_bootstrap.py`
  - 覆盖启动初始化调用 V2 helper。
  - 覆盖有效 engine 为 V1 时写入 warning。
- 修改：`tests/test_prompt_trace_admin.py`
  - 覆盖 effective preview 请求模型默认 engine 为 V2。
- 修改：`tests/test_reply_admin.py`
  - 覆盖 reply-test 请求模型和 resolver 默认走 V2。
- 修改：`docs/todo.md`
  - 在路线项 1 标记第一阶段「默认 engine 切 V2」已完成。
- 修改：`docs/plan_walkthrough.md`
  - 将 P1-3 状态从待执行改为已完成，并记录验证结果与提交号。

## 关键约束

- 本阶段不删除 legacy / V1 文件，不移除 `fallback_v1` 策略。
- 不自动迁移 DB 中已有 `prompt_runtime.engine=v1` 设置，只记录 warning。
- 不大拆 `handle_message()`；只改 engine 默认和非法 fallback。
- 修改 `prompt` 运行时入口时，必须确认 `creatures/nanobot/prompt.md` 没有被本阶段行为描述影响。若只改默认 engine，不改 enriched query、历史注入或 conversation 结构，则不需要同步该文件正文。

---

### 任务 1：写默认 V2 的红灯测试

**文件：**
- 修改：`tests/test_bridge_prompt_v2.py`
- 修改：`tests/test_prompt_manifest.py`

- [ ] **步骤 1：把现有 V2 bridge 测试改成无 override 默认路径**

在 `tests/test_bridge_prompt_v2.py` 的 `test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event` 中，删除 metadata 里的显式 override：

```python
metadata={
    "prompt_system_mode_override": "managed",
    "chat_type": "group",
    "is_group": True,
    "group_id": "1001",
    "history_messages": [{"role": "user", "content": "旧历史"}],
    "history_header": "<conversation_context>历史</conversation_context>",
    "runtime_preset": "none",
    "reply_model": "fake-model",
    "enable_reply_contract_retry": False,
}
```

保留已有断言：

```python
assert captured_requests
assert [m.content for m in conversation._messages] == ["V2_SYSTEM_ONLY"]
```

- [ ] **步骤 2：补充 config registry 与 manifest 一致性测试**

在 `tests/test_prompt_manifest.py` 中新增：

```python
def test_prompt_runtime_config_default_matches_manifest_active_engine():
    import json
    from pathlib import Path

    from core.config_registry import SETTING_DEFS

    manifest = json.loads(Path("prompt_manifest.json").read_text(encoding="utf-8"))

    assert manifest["active_engine"] == "v2"
    assert SETTING_DEFS["prompt_runtime.engine"].default == manifest["active_engine"]
```

- [ ] **步骤 3：运行测试验证红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event tests/test_prompt_manifest.py::test_prompt_runtime_config_default_matches_manifest_active_engine -q -p no:cacheprovider
```

预期：FAIL。bridge 测试会进入 V1 路径并触发 `PromptAssembler must not run for successful V2 live requests`；manifest 测试会看到默认值仍为 `v1`。

---

### 任务 2：实现 config / bridge 默认 V2

**文件：**
- 修改：`core/config_registry.py`
- 修改：`nanobot_kt/bridge.py`
- 测试：`tests/test_bridge_prompt_v2.py`
- 测试：`tests/test_prompt_manifest.py`

- [ ] **步骤 1：修改配置默认值**

在 `core/config_registry.py` 中修改：

```python
"prompt_runtime.engine": SettingDef(
    key="prompt_runtime.engine", env_name="NANOBOT_PROMPT_ENGINE",
    default="v2", value_type="str",
    category="prompt", description="提示词运行引擎: v1/v2",
),
```

- [ ] **步骤 2：修改 bridge engine fallback**

在 `nanobot_kt/bridge.py` 中修改 `_prompt_runtime_engine()`：

```python
def _prompt_runtime_engine(self) -> str:
    try:
        from core.settings_service import settings

        engine = str(settings.get("prompt_runtime.engine", "v2") or "v2").strip().lower()
    except Exception:
        engine = "v2"
    return engine if engine in {"v1", "v2"} else "v2"
```

在 `handle_message()` 解析 metadata override 后修改非法值 fallback：

```python
if prompt_engine not in {"v1", "v2"}:
    prompt_engine = "v2"
```

- [ ] **步骤 3：运行红灯测试验证变绿**

运行任务 1 步骤 3 的命令。

预期：PASS。

- [ ] **步骤 4：补充显式 V1 与非法 engine 测试**

在 `tests/test_bridge_prompt_v2.py` 新增一个轻量测试，直接验证 resolver：

```python
def test_bridge_prompt_runtime_engine_defaults_to_v2_and_invalid_falls_back(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: None)
    assert bridge._prompt_runtime_engine() == "v2"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "bad-engine")
    assert bridge._prompt_runtime_engine() == "v2"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v1")
    assert bridge._prompt_runtime_engine() == "v1"
```

在同文件新增一个 handle_message 级测试，复制现有 V2 成功测试的最小 fake bridge 环境，但 metadata 使用：

```python
metadata={
    "prompt_runtime_engine_override": "v1",
    "chat_type": "group",
    "is_group": True,
    "group_id": "1004",
    "runtime_preset": "none",
    "reply_model": "fake-model",
    "enable_reply_contract_retry": False,
}
```

断言：

```python
assert build_calls
assert build_calls[0].prompt_key == "group_chat"
assert not captured_requests
```

同一测试或相邻测试再传：

```python
"prompt_runtime_engine_override": "not-a-real-engine"
```

断言 `captured_requests` 非空，证明非法 override 回落 V2。

- [ ] **步骤 5：运行 bridge / manifest 定向测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_prompt_manifest.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add tests/test_bridge_prompt_v2.py tests/test_prompt_manifest.py core/config_registry.py nanobot_kt/bridge.py
git commit -m "feat(提示词): 默认使用 V2 运行时"
```

---

### 任务 3：实现 V2 runtime 目录初始化

**文件：**
- 修改：`core/prompt_v2/template_registry.py`
- 修改：`tests/test_prompt_v2_template_registry.py`

- [ ] **步骤 1：编写 runtime 初始化测试**

在 `tests/test_prompt_v2_template_registry.py` 新增：

```python
def test_prompt_v2_init_runtime_dir_copies_missing_files_without_overwrite(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    (default_dir / "chat").mkdir(parents=True)
    (default_dir / "chat" / "main.md").write_text("DEFAULT MAIN\n", encoding="utf-8")
    (default_dir / "chat" / "flow.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (runtime_dir / "chat").mkdir(parents=True)
    (runtime_dir / "chat" / "main.md").write_text("RUNTIME MAIN\n", encoding="utf-8")

    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

    result = init_prompt_v2_runtime_dir()

    assert result["runtime_dir"] == str(runtime_dir)
    assert result["source_dir"] == str(default_dir)
    assert result["copied"] == ["chat/flow.json"]
    assert (runtime_dir / "chat" / "main.md").read_text(encoding="utf-8") == "RUNTIME MAIN\n"
    assert (runtime_dir / "chat" / "flow.json").read_text(encoding="utf-8") == '{"nodes": [], "edges": []}\n'
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_prompt_v2_template_registry.py::test_prompt_v2_init_runtime_dir_copies_missing_files_without_overwrite -q -p no:cacheprovider
```

预期：FAIL，报错 `cannot import name 'init_prompt_v2_runtime_dir'`。

- [ ] **步骤 3：新增初始化 helper**

在 `core/prompt_v2/template_registry.py` 中新增：

```python
def init_prompt_v2_runtime_dir() -> dict[str, Any]:
    source_dir = default_template_dir()
    runtime_dir = runtime_template_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if not source_dir.exists():
        return {"source_dir": str(source_dir), "runtime_dir": str(runtime_dir), "copied": copied}

    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file() or source_path.suffix not in {".md", ".json"}:
            continue
        rel = source_path.relative_to(source_dir)
        target_path = runtime_dir / rel
        if target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(rel.as_posix())

    return {"source_dir": str(source_dir), "runtime_dir": str(runtime_dir), "copied": copied}
```

- [ ] **步骤 4：运行测试验证通过**

运行任务 3 步骤 2 的命令。

预期：PASS。

---

### 任务 4：启动时调用 V2 初始化并记录 V1 显式回滚

**文件：**
- 修改：`bootstrap/prompt_runtime.py`
- 创建：`tests/test_prompt_runtime_bootstrap.py`

- [ ] **步骤 1：编写 bootstrap 测试**

创建 `tests/test_prompt_runtime_bootstrap.py`：

```python
import logging


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_init_prompt_runtimes_initializes_prompt_v2(monkeypatch):
    from bootstrap import prompt_runtime

    called = {}

    monkeypatch.setattr("core.prompts.manager.PromptManager.init_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/managed",
        "source_dir": "/tmp/managed-default",
    })
    monkeypatch.setattr("core.legacy_prompt_runtime.init_legacy_prompt_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/legacy",
        "source_dir": "/tmp/legacy-default",
    })
    monkeypatch.setattr("core.prompt_v2.template_registry.init_prompt_v2_runtime_dir", lambda: called.setdefault("v2", {
        "copied": ["chat/main.md"],
        "runtime_dir": "/tmp/v2",
        "source_dir": "/tmp/v2-default",
    }))
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, _default=None: "v2")

    logger = logging.getLogger("test.prompt_runtime.v2")
    handler = _ListHandler()
    logger.addHandler(handler)
    try:
        prompt_runtime.init_prompt_runtimes(logger)
    finally:
        logger.removeHandler(handler)

    assert called["v2"]["copied"] == ["chat/main.md"]
    assert any("[PromptV2] initialized 1 templates" in msg for msg in handler.messages)


def test_init_prompt_runtimes_warns_when_effective_engine_is_v1(monkeypatch, caplog):
    from bootstrap import prompt_runtime

    monkeypatch.setattr("core.prompts.manager.PromptManager.init_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/managed",
        "source_dir": "/tmp/managed-default",
    })
    monkeypatch.setattr("core.legacy_prompt_runtime.init_legacy_prompt_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/legacy",
        "source_dir": "/tmp/legacy-default",
    })
    monkeypatch.setattr("core.prompt_v2.template_registry.init_prompt_v2_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/v2",
        "source_dir": "/tmp/v2-default",
    })
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, _default=None: "v1")

    logger = logging.getLogger("test.prompt_runtime.rollback")
    with caplog.at_level(logging.WARNING):
        prompt_runtime.init_prompt_runtimes(logger)

    assert "Prompt Runtime 当前有效 engine=v1" in caplog.text
    assert "显式回滚" in caplog.text
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_prompt_runtime_bootstrap.py -q -p no:cacheprovider
```

预期：FAIL，原因是 bootstrap 还没有调用 V2 初始化，且没有 V1 warning。

- [ ] **步骤 3：接入 bootstrap**

在 `bootstrap/prompt_runtime.py` 的 managed prompt 初始化后、legacy 初始化前加入：

```python
    try:
        from core.prompt_v2.template_registry import init_prompt_v2_runtime_dir

        v2_result = init_prompt_v2_runtime_dir()
        if v2_result["copied"]:
            logger.info(
                "[PromptV2] initialized %d templates from %s -> %s",
                len(v2_result["copied"]),
                v2_result["source_dir"],
                v2_result["runtime_dir"],
            )
        else:
            logger.info("[PromptV2] runtime templates ready: %s", v2_result["runtime_dir"])
    except Exception as exc:
        logger.warning("[PromptV2] init_runtime_dir failed: %s", exc)
```

在函数末尾加入：

```python
    try:
        from core.settings_service import settings

        effective_engine = str(settings.get("prompt_runtime.engine", "v2") or "v2").strip().lower()
        if effective_engine == "v1":
            logger.warning(
                "Prompt Runtime 当前有效 engine=v1；这是显式回滚状态，请检查 DB setting 或 NANOBOT_PROMPT_ENGINE"
            )
    except Exception as exc:
        logger.warning("[PromptRuntime] failed to inspect effective engine: %s", exc)
```

- [ ] **步骤 4：运行 bootstrap 与 template registry 测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_prompt_runtime_bootstrap.py tests/test_prompt_v2_template_registry.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add core/prompt_v2/template_registry.py bootstrap/prompt_runtime.py tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py
git commit -m "feat(提示词): 初始化 V2 运行时模板"
```

---

### 任务 5：admin preview 和 reply-test 默认切到 V2

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`tests/test_prompt_trace_admin.py`
- 修改：`tests/test_reply_admin.py`

- [ ] **步骤 1：编写 admin 默认值测试**

在 `tests/test_prompt_trace_admin.py` 新增：

```python
def test_effective_prompt_preview_request_defaults_to_v2():
    from api.admin_routes import EffectivePromptPreviewRequest

    body = EffectivePromptPreviewRequest()

    assert body.engine == "v2"
```

在 `tests/test_reply_admin.py` 新增：

```python
def test_reply_test_request_defaults_to_prompt_v2():
    from api.admin_routes import ReplyTestRunRequest, _resolve_reply_test_prompt_settings

    body = ReplyTestRunRequest(message="你在吗")

    assert body.prompt_engine == "v2"
    assert body.variant == "v2_code_retry"
    assert _resolve_reply_test_prompt_settings(body) == ("v2", "legacy", True)
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_prompt_trace_admin.py::test_effective_prompt_preview_request_defaults_to_v2 tests/test_reply_admin.py::test_reply_test_request_defaults_to_prompt_v2 -q -p no:cacheprovider
```

预期：FAIL，默认值仍为 `v1` / `code_retry`。

- [ ] **步骤 3：修改 request 默认值和 resolver fallback**

在 `api/admin_routes.py` 中修改：

```python
class EffectivePromptPreviewRequest(BaseModel):
    ...
    engine: Literal["v1", "v2"] = "v2"
```

修改 `ReplyTestRunRequest`：

```python
class ReplyTestRunRequest(BaseModel):
    ...
    prompt_engine: Literal["v1", "v2"] = "v2"
    variant: Literal[
        "baseline",
        "prompt_only",
        "code_retry",
        "v1_baseline",
        "v2_prompt_only",
        "v2_code_retry",
    ] = "v2_code_retry"
```

修改 `_resolve_reply_test_prompt_settings()`：

```python
def _resolve_reply_test_prompt_settings(body: ReplyTestRunRequest) -> tuple[str, str, bool]:
    variant = str(body.variant or "v2_code_retry")
    engine = str(body.prompt_engine or "v2")
    prompt_mode = "legacy"
    enable_retry = bool(body.enable_reply_contract_retry)
    ...
    if engine not in {"v1", "v2"}:
        engine = "v2"
    return engine, prompt_mode, enable_retry
```

- [ ] **步骤 4：运行 admin 定向测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_prompt_trace_admin.py tests/test_reply_admin.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add api/admin_routes.py tests/test_prompt_trace_admin.py tests/test_reply_admin.py
git commit -m "feat(管理端): 默认使用 V2 提示词测试"
```

---

### 任务 6：文档状态同步与全量验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：同步 `docs/todo.md` 路线项 1 状态**

在 `docs/todo.md` 的路线项 1「粗略路径」后追加 2026-06-17 状态说明：

```markdown
- **实施状态（2026-06-17）**：第一步默认 engine 切到 V2 已落地；`data/prompts_v2` 启动初始化、admin effective preview 默认 V2、reply-test 默认 `v2_code_retry` 已同步。V1 / legacy 资产仍作为显式回滚路径保留，删除旧资产和去版本化进入下一阶段。
```

- [ ] **步骤 2：同步 `docs/plan_walkthrough.md`**

把后续优先级表中的 P1-3 状态改为 `已完成`，并在“下一步详细计划：Prompt V2 默认接管”中把阶段 C / D / E 标记为已完成，写入定向测试和全量测试结果。

- [ ] **步骤 3：运行完整定向验证**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_bridge_prompt_v2.py tests/test_prompt_manifest.py tests/test_prompt_v2_template_registry.py tests/test_prompt_runtime_bootstrap.py tests/test_prompt_trace_admin.py tests/test_reply_admin.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 4：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [ ] **步骤 5：检查工作区与格式**

运行：

```bash
git diff --check
git status --short
```

确认只剩本阶段相关文件需要提交，且无 whitespace error。

- [ ] **步骤 6：Commit**

```bash
git add docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(提示词): 同步 V2 默认接管进度"
```

---

## 验收清单

- [ ] 无 metadata override、无 env override、无 DB override 时，`NanobotBridge` 默认调用 V2 compiler。
- [ ] 显式 `prompt_runtime_engine_override="v1"` 时仍走 V1 路径。
- [ ] 非法 engine 设置回落到 V2，而不是 V1。
- [ ] `prompt_runtime.engine` 注册默认值为 `v2`。
- [ ] `EffectivePromptPreviewRequest.engine` 默认值为 `v2`。
- [ ] `ReplyTestRunRequest.prompt_engine` 默认值为 `v2`，默认 variant 为 `v2_code_retry`。
- [ ] 启动初始化会准备 `data/prompts_v2`，且不覆盖已有 runtime 修改。
- [ ] 若有效 engine 仍为 `v1`，启动日志明确提示这是显式回滚状态。
- [ ] 现有 V2 bridge、preview、reply-test 测试通过。
- [ ] `python -B -m pytest tests/ -q -p no:cacheprovider --durations=20` 通过。

## 执行顺序

1. 任务 1 和任务 2 合并为第一个代码提交。
2. 任务 3 和任务 4 合并为第二个代码提交。
3. 任务 5 单独作为第三个代码提交。
4. 任务 6 作为文档同步提交。
5. 每个提交前只暂存对应文件，不使用 `git add .` 或 `git add -A`。
