# Best-effort 调试日志实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]` / `- [x]`）语法来跟踪进度。

## 执行结果摘要（2026-06-21）

状态：实现、验证与实现阶段提交已完成。

阶段提交：

- 设计提交：`4074b60 docs(日志): 设计 best-effort 调试日志`。
- 计划提交：`e210d7e docs(计划): 记录 best-effort 日志计划`。
- 实现提交：`ab08701 fix(日志): 补齐 best-effort 调试记录`。

验证记录：

- 红灯：实现前运行新增日志测试，结果为 `6 failed, 1 warning in 6.62s`，失败点均指向缺少 `debug` 日志。
- 绿灯：`6 passed, 1 warning in 1.52s`。
- 相邻回归：`35 passed, 1 warning in 3.24s`。
- 静态检查：`compileall` 无输出；目标文件裸吞异常扫描无匹配；`git diff --check` 无输出。
- 全量回归：`1503 passed, 6 skipped, 139 warnings in 112.76s`。
- 提交后检查：`git show --stat --oneline -1` 确认实现提交包含 10 个预期文件，目标代码与测试文件提交后干净。
- 文档门禁：`git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/best-effort-debug-logging.md` 与占位符扫描脚本均无输出。
- 文档提交前全量回归：`1503 passed, 6 skipped, 139 warnings in 118.42s`。

实现约束：

- 所有新增日志均为 `debug` 级别，保持原 fallback 行为不变。
- 不记录 prompt 正文、用户输入、完整 `meta_json`、文件 URL 或群记忆 evidence。
- `core/legacy_adapter.py::SQLiteMemory.save_log()` 仅复验既有 rollback + `logger.exception` 行为，未改业务逻辑。
- 未新增 `asyncio.run()`，未新增同步函数包装 awaitable。

**目标：** 为 `docs/todo.md` 中仍然静默吞异常的 best-effort 路径补 `debug` 日志，同时保持 fallback 行为不变。

**架构：** 每个模块使用本模块现有或相邻日志命名，只在原 `except Exception` 分支记录定位信息和异常摘要。日志不携带 prompt 正文、用户输入、完整 `meta_json` 或群记忆 evidence；H11 `SQLiteMemory.save_log()` 已有 rollback 和日志，仅保留回归验证。

**技术栈：** Python 3.12、pytest、caplog、FastAPI TestClient、项目既有 logging / SQLAlchemy 测试夹具。

---

## 文件职责

- 修改：`core/prompts/manager.py`
  - 增加 `nanobot.prompt_manager` logger。
  - `PromptManager.render()` 的 `PromptTracer.record_render()` 外层 fallback 记录 `debug`。
- 修改：`core/context_legacy.py`
  - 增加 `nanobot.context_legacy` logger。
  - deprecated `build_group_profile_context()` 失败返回空字符串前记录 `debug`。
- 修改：`api/admin/system_routes.py`
  - 增加 `nanobot.admin` logger。
  - `/version` 的 git 探测失败返回 `None` 前记录 `debug`。
- 修改：`app/group_ingress/helpers.py`
  - `safe_meta()` 解析失败记录 `debug`，不记录原始 `meta_json`。
  - `get_group_talk_value()` fallback `0.5` 前记录 `debug`。
- 修改：`app/memory_digest/builder.py`
  - 增加 `nanobot.memory_digest.builder` logger。
  - `_safe_meta()` 解析失败记录 `debug`，不记录原始 `meta_json`。
- 修改：`tests/test_prompt_manager.py`
  - 覆盖 trace fallback 日志。
- 修改：`tests/test_group_memory.py`
  - 覆盖 deprecated 群画像 fallback 日志。
- 修改：`tests/test_admin_api.py`
  - 覆盖 git 探测失败日志与 `/version` fallback。
- 创建：`tests/test_group_ingress_helpers.py`
  - 覆盖 `safe_meta()` 与 `get_group_talk_value()` fallback 日志。
- 修改：`tests/test_memory_digest_builder_quality.py`
  - 覆盖 memory digest `_safe_meta()` fallback 日志。
- 修改：`docs/todo.md`
  - 更新真实路径，标记 H11 已完成。
- 修改：`docs/plan_walkthrough.md`
  - 记录本阶段执行、验证和提交号。
- 修改：`.Codex/plans/best-effort-debug-logging.md`
  - 勾选任务并记录验证结果。

## 任务 1：补红灯测试

**文件：**
- 修改：`tests/test_prompt_manager.py`
- 修改：`tests/test_group_memory.py`
- 修改：`tests/test_admin_api.py`
- 创建：`tests/test_group_ingress_helpers.py`
- 修改：`tests/test_memory_digest_builder_quality.py`

- [x] **步骤 1：新增 PromptManager trace fallback 日志测试**

在 `tests/test_prompt_manager.py` 顶部添加：

```python
import logging
```

在文件末尾添加：

```python
def test_prompt_manager_logs_tracer_failure_without_failing_render(tmp_path, monkeypatch, caplog):
    from core.prompts import PromptManager

    prompt_dir = tmp_path / "prompts"
    write_template(
        prompt_dir,
        "group_chat.md",
        """---
name: 群聊回复
required_vars:
  - user_input
---
用户: {{ user_input }}
""",
    )

    def broken_record_render(**_kwargs):
        raise RuntimeError("trace boom")

    monkeypatch.setattr("core.tracing.PromptTracer.record_render", broken_record_render)
    manager = PromptManager(prompt_dir=prompt_dir, backup_dir=tmp_path / "backups")

    with caplog.at_level(logging.DEBUG, logger="nanobot.prompt_manager"):
        rendered = manager.render(
            "group_chat",
            {"user_input": "你好"},
            trace_id="trace-1",
            run_id="run-1",
            mode="shadow",
        )

    assert "用户: 你好" in rendered.content
    assert "trace boom" in caplog.text
    assert "trace-1" in caplog.text
    assert "run-1" in caplog.text
```

- [x] **步骤 2：新增 deprecated group profile fallback 日志测试**

在 `tests/test_group_memory.py` 顶部添加：

```python
import logging
```

在 `test_legacy_context_module_exports_group_context_builders()` 后添加：

```python
def test_deprecated_group_profile_context_logs_build_failure(monkeypatch, caplog):
    from core.context_legacy import build_group_profile_context

    def broken_build_profile_with_evidence(*_args, **_kwargs):
        raise RuntimeError("profile boom")

    monkeypatch.setattr(
        "core.group_memory.build_profile_with_evidence",
        broken_build_profile_with_evidence,
    )

    with caplog.at_level(logging.DEBUG, logger="nanobot.context_legacy"):
        context = build_group_profile_context("g_fail")

    assert context == ""
    assert "g_fail" in caplog.text
    assert "profile boom" in caplog.text
```

- [x] **步骤 3：新增 admin version git 探测 fallback 日志测试**

在 `tests/test_admin_api.py` 顶部添加：

```python
import logging
```

在 `TestAuth` 内 `test_version_ok()` 后添加：

```python
    def test_version_git_probe_failure_logs_debug(self, client, auth_header, monkeypatch, caplog):
        from api.admin import system_routes

        for key in (
            "NANOBOT_GIT_COMMIT",
            "NANOBOT_GIT_BRANCH",
            "NANOBOT_GIT_COMMIT_DATE",
            "NANOBOT_GIT_DIRTY",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(system_routes, "_VERSION_CACHE", None)

        def broken_check_output(*_args, **_kwargs):
            raise OSError("git missing")

        monkeypatch.setattr(system_routes.subprocess, "check_output", broken_check_output)

        with caplog.at_level(logging.DEBUG, logger="nanobot.admin"):
            response = client.get("/api/v1/admin/version", headers=auth_header)

        data = _ok(response)
        assert data["commit"] == "unknown"
        assert data["dirty"] is None
        assert "git missing" in caplog.text
        assert "rev-parse" in caplog.text
```

- [x] **步骤 4：新增 group ingress helper fallback 日志测试**

创建 `tests/test_group_ingress_helpers.py`：

```python
import logging


def test_safe_meta_logs_invalid_json_without_leaking_raw_meta(caplog):
    from app.group_ingress.helpers import safe_meta

    raw = "{bad json secret-token}"

    with caplog.at_level(logging.DEBUG, logger="nanobot.group_ingress"):
        result = safe_meta(raw)

    assert result == {}
    assert "invalid meta_json" in caplog.text
    assert "secret-token" not in caplog.text
    assert str(len(raw)) in caplog.text


def test_get_group_talk_value_logs_fallback(monkeypatch, caplog):
    from app.group_ingress.helpers import get_group_talk_value

    def broken_get_stream_config(*_args, **_kwargs):
        raise RuntimeError("config boom")

    monkeypatch.setattr("core.expression_memory.get_stream_config", broken_get_stream_config)

    with caplog.at_level(logging.DEBUG, logger="nanobot.group_ingress"):
        value = get_group_talk_value("group_123")

    assert value == 0.5
    assert "talk_value fallback" in caplog.text
    assert "group_123" in caplog.text
    assert "config boom" in caplog.text
```

- [x] **步骤 5：新增 memory digest meta fallback 日志测试**

在 `tests/test_memory_digest_builder_quality.py` 顶部添加：

```python
import logging
```

在 `_log()` 后添加：

```python
def test_memory_digest_builder_logs_invalid_meta_without_leaking_raw_meta(caplog):
    bad_meta = "{bad json digest-secret}"
    log = _log(content="[Alice]: 讨论 memory digest 调试日志", log_id=9)
    log.meta_json = bad_meta

    with caplog.at_level(logging.DEBUG, logger="nanobot.memory_digest.builder"):
        result = MemoryDigestBuilder().build(
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-28",
            logs=[log],
        )

    assert result.status in {"active", "skipped"}
    assert "invalid meta_json" in caplog.text
    assert "digest-secret" not in caplog.text
    assert str(len(bad_meta)) in caplog.text
```

- [x] **步骤 6：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_prompt_manager.py::test_prompt_manager_logs_tracer_failure_without_failing_render \
  tests/test_group_memory.py::test_deprecated_group_profile_context_logs_build_failure \
  tests/test_admin_api.py::TestAuth::test_version_git_probe_failure_logs_debug \
  tests/test_group_ingress_helpers.py \
  tests/test_memory_digest_builder_quality.py::test_memory_digest_builder_logs_invalid_meta_without_leaking_raw_meta \
  -q
```

预期：至少这些新测试因为日志不存在而失败；如果某个测试因 patch 路径错误报错，先修测试直到失败原因指向缺少日志。

## 任务 2：实现 debug 日志

**文件：**
- 修改：`core/prompts/manager.py`
- 修改：`core/context_legacy.py`
- 修改：`api/admin/system_routes.py`
- 修改：`app/group_ingress/helpers.py`
- 修改：`app/memory_digest/builder.py`

- [x] **步骤 1：修改 `core/prompts/manager.py`**

在导入区加入 `logging`，并在 `logger_name` 后创建 logger：

```python
import logging
```

```python
logger_name = "nanobot.prompt_manager"
logger = logging.getLogger(logger_name)
```

将 trace fallback 改为：

```python
            except Exception as exc:
                logger.debug(
                    "[PromptManager] prompt trace skipped prompt_key=%s mode=%s trace_id=%s run_id=%s: %s",
                    tmpl.prompt_key,
                    mode or "preview",
                    trace_id or "",
                    run_id or "",
                    exc,
                    exc_info=True,
                )
```

- [x] **步骤 2：修改 `core/context_legacy.py`**

在导入区加入：

```python
import logging
```

并在 imports 后添加：

```python
logger = logging.getLogger("nanobot.context_legacy")
```

将 deprecated fallback 改为：

```python
    except Exception as exc:
        logger.debug(
            "[ContextLegacy] deprecated group profile context skipped group_id=%s: %s",
            group_id,
            exc,
            exc_info=True,
        )
        return ""
```

- [x] **步骤 3：修改 `api/admin/system_routes.py`**

在导入区加入：

```python
import logging
```

在 `_VERSION_CACHE` 附近添加：

```python
logger = logging.getLogger("nanobot.admin")
```

将 `_git()` fallback 改为：

```python
            except Exception as exc:
                logger.debug(
                    "[AdminVersion] git probe failed args=%s cwd=%s: %s",
                    args,
                    base,
                    exc,
                    exc_info=True,
                )
                return None
```

- [x] **步骤 4：修改 `app/group_ingress/helpers.py`**

将 `safe_meta()` fallback 改为：

```python
    except Exception as exc:
        logger.debug(
            "[GroupIngress] invalid meta_json ignored len=%d: %s",
            len(str(meta_json or "")),
            exc,
            exc_info=True,
        )
        return {}
```

将 `get_group_talk_value()` fallback 改为：

```python
    except Exception as exc:
        logger.debug(
            "[GroupIngress] talk_value fallback session_id=%s fallback=0.5: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return 0.5
```

- [x] **步骤 5：修改 `app/memory_digest/builder.py`**

在导入区加入：

```python
import logging
```

在 imports 后添加：

```python
logger = logging.getLogger("nanobot.memory_digest.builder")
```

将 `_safe_meta()` fallback 改为：

```python
    except Exception as exc:
        logger.debug(
            "[MemoryDigest] invalid meta_json ignored len=%d: %s",
            len(str(meta_json or "")),
            exc,
            exc_info=True,
        )
        return {}
```

- [x] **步骤 6：运行绿灯测试**

运行任务 1 的红灯命令。预期：全部通过。

## 任务 3：回归验证与文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/best-effort-debug-logging.md`

- [x] **步骤 1：运行相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_prompt_manager.py \
  tests/test_group_memory.py \
  tests/test_admin_api.py::TestAuth \
  tests/test_group_ingress_helpers.py \
  tests/test_memory_digest_builder_quality.py \
  tests/test_memory_digest.py::test_memory_digest_builder_generates_schema_v2_cards_and_filters_noise \
  tests/test_memory_digest.py::test_memory_digest_builder_skips_when_only_noise \
  tests/test_kt_integration.py::TestSQLiteMemoryDetachedLogs::test_save_log_rolls_back_when_commit_fails \
  -q
```

预期：全部通过。

- [x] **步骤 2：运行静态检查**

运行：

```bash
python -m compileall core/prompts/manager.py core/context_legacy.py api/admin/system_routes.py app/group_ingress/helpers.py app/memory_digest/builder.py -q
rg -n "except Exception:\\s*(pass|return \\{\\}|return None|return \"\"|return 0\\.5)" core/prompts/manager.py core/context_legacy.py api/admin/system_routes.py app/group_ingress/helpers.py app/memory_digest/builder.py
git diff --check
```

预期：`compileall` 和 `git diff --check` 无输出；`rg` 不再命中本阶段目标中的裸吞异常。

- [x] **步骤 3：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 4：更新 `docs/todo.md`**

将 P3「静默吞异常补日志」标记为完成，记录：

- `core/prompts/manager.py` trace fallback 已补 `debug`。
- `core/context_legacy.py` deprecated 群画像 fallback 已补 `debug`。
- `api/admin/system_routes.py` git 探测 fallback 已补 `debug`。
- `app/group_ingress/helpers.py` `safe_meta()` 和 `get_group_talk_value()` fallback 已补 `debug`。
- `app/memory_digest/builder.py` `_safe_meta()` fallback 已补 `debug`。
- `core/legacy_adapter.py::SQLiteMemory.save_log()` 已有 rollback + `logger.exception` + 既有回归测试，本次未改业务行为。

- [x] **步骤 5：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Best-effort 吞异常补日志` 章节，记录设计文档、计划文件、完成项、验证结果和提交号。

- [x] **步骤 6：更新本计划执行结果**

在本计划顶部追加 `执行结果摘要（2026-06-21）`，记录红灯、绿灯、相邻回归、静态检查、全量回归和提交号。

- [x] **步骤 7：文档门禁**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/best-effort-debug-logging.md
python - <<'PY'
from pathlib import Path
markers = [
    '\u5f85\u5b9a',
    'TO' + 'DO',
    chr(60) + '实际',
    chr(60) + '失败',
    chr(60) + '通过',
]
paths = [
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
    Path('.Codex/plans/best-effort-debug-logging.md'),
]
hits = []
for path in paths:
    text = path.read_text(encoding='utf-8')
    hits.extend(f'{path}: {marker}' for marker in markers if marker in text)
if hits:
    raise SystemExit('\n'.join(hits))
PY
```

预期：两个命令均无输出，退出码为 0。

## 任务 4：提交实现阶段

**文件：**
- 修改：`core/prompts/manager.py`
- 修改：`core/context_legacy.py`
- 修改：`api/admin/system_routes.py`
- 修改：`app/group_ingress/helpers.py`
- 修改：`app/memory_digest/builder.py`
- 修改：`tests/test_prompt_manager.py`
- 修改：`tests/test_group_memory.py`
- 修改：`tests/test_admin_api.py`
- 创建：`tests/test_group_ingress_helpers.py`
- 修改：`tests/test_memory_digest_builder_quality.py`

- [x] **步骤 1：按文件显式暂存**

运行：

```bash
git add \
  core/prompts/manager.py \
  core/context_legacy.py \
  api/admin/system_routes.py \
  app/group_ingress/helpers.py \
  app/memory_digest/builder.py \
  tests/test_prompt_manager.py \
  tests/test_group_memory.py \
  tests/test_admin_api.py \
  tests/test_group_ingress_helpers.py \
  tests/test_memory_digest_builder_quality.py
```

- [x] **步骤 2：检查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期：暂存区只包含本任务列出的 10 个实现与测试文件；`--check` 无输出。

- [x] **步骤 3：提交实现**

运行：

```bash
git commit -m "fix(日志): 补齐 best-effort 调试记录"
```

- [x] **步骤 4：提交后检查**

运行：

```bash
git show --stat --oneline -1
git status --short -- \
  core/prompts/manager.py \
  core/context_legacy.py \
  api/admin/system_routes.py \
  app/group_ingress/helpers.py \
  app/memory_digest/builder.py \
  tests/test_prompt_manager.py \
  tests/test_group_memory.py \
  tests/test_admin_api.py \
  tests/test_group_ingress_helpers.py \
  tests/test_memory_digest_builder_quality.py
```

预期：目标文件提交后干净。

## 任务 5：提交文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/best-effort-debug-logging.md`

- [x] **步骤 1：运行文档门禁**

运行任务 3 的文档门禁命令，预期无输出，退出码为 0。

- [x] **步骤 2：运行提交前全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 3：按文件显式暂存文档**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/best-effort-debug-logging.md
```

- [x] **步骤 4：检查并提交文档收口**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
git commit -m "docs(计划): 收口 best-effort 日志状态"
```

预期：暂存区只包含 3 个文档文件；提交后目标文档文件干净。
