# Best-effort 吞异常补日志设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「静默吞异常补日志（best-effort 路径）」仍未完成。经当前代码核对，待办中的部分路径已经漂移：

- `prompts/manager.py:435` 实际为 `core/prompts/manager.py` 中 `PromptManager.render()` 的 trace 记录兜底。
- `context_builder.py:895` 的 deprecated 群画像入口已经拆到 `core/context_legacy.py`。
- `admin/system_routes.py:46` 实际为 `api/admin/system_routes.py`。
- `core/group_ingress/helpers.py:41,74` 实际为 `app/group_ingress/helpers.py`。
- `memory_digest/builder.py:65` 实际为 `app/memory_digest/builder.py`。
- `H11 save_log` 已在 `core/legacy_adapter.py` 中完成 `SQLAlchemyError` 捕获、`rollback`、`logger.exception` 和重新抛出，并已有测试覆盖。

本阶段目标是补齐 still-live 的 best-effort 失败日志，不改变主流程语义。

## 目标

1. 对仍在静默吞异常的 best-effort 路径补 `logger.debug`。
2. 保持原有 fallback 行为：trace 失败不影响 prompt 渲染，git 探测失败仍返回版本 fallback，坏 `meta_json` 仍返回空 dict，`talk_value` 读取失败仍返回 `0.5`，deprecated 群画像构建失败仍返回空字符串。
3. 日志只记录定位信息和异常摘要，不记录用户正文、prompt 内容、`variables`、`rendered_content`、完整 `meta_json` 或群记忆证据原文。
4. 更新 `docs/todo.md`，把路径漂移和 H11 已完成状态记录清楚。

## 非目标

- 不把这些 best-effort 路径升级为 `warning` 或阻断主流程。
- 不修改 `SQLiteMemory.save_log()` 的业务行为；它已经不是静默吞异常。
- 不处理 `api/routes.py` 中旧同名 helper，除非后续确认仍有 live 调用并单独设计。
- 不重构 `PromptTracer`、group ingress service、memory digest builder 或 context legacy 模块结构。

## 方案比较

### 方案 A：全部改为 `warning`

优点是生产日志更容易发现问题；缺点是这些路径多为容错和兼容场景，容器无 `.git`、历史坏 JSON、trace 数据库偶发失败都可能造成噪声。该方案不符合待办中「补 `logger.debug` 提升可调试性即可」的定位。

### 方案 B：只补 `debug` 日志并保持 fallback

优点是行为风险最低，打开 debug 时可定位丢失的 trace、git 版本探测、group meta 解析、talk_value fallback 和 memory digest meta 解析问题；缺点是默认生产日志不会主动告警。考虑到这些路径均为 best-effort，本阶段采用该方案。

### 方案 C：抽统一 safe JSON helper

优点是减少重复；缺点是会扩大改动面，影响多个解析点和日志命名，不适合本阶段低风险收口。该方案保留为后续代码清理候选。

## 设计细节

### `core/prompts/manager.py`

- 新增 `import logging`。
- 将已有 `logger_name = "nanobot.prompt_manager"` 变为真实 logger：`logger = logging.getLogger(logger_name)`。
- `PromptManager.render()` 中 `PromptTracer.record_render()` 的外层兜底从 `except Exception: pass` 改为 `except Exception as exc:`。
- 记录 `debug` 日志，包含 `prompt_key`、`mode`、`trace_id`、`run_id`、异常信息和 `exc_info=True`。
- 不记录 `variables` 或 `content`。

### `core/context_legacy.py`

- 新增 `import logging` 和 `logger = logging.getLogger("nanobot.context_legacy")`。
- `build_group_profile_context()` 外层 deprecated 兜底补 `debug` 日志，包含 `group_id`、异常信息和 `exc_info=True`。
- 继续返回 `""`。
- 不记录 profile 或 evidence 原文。

### `api/admin/system_routes.py`

- 新增 `import logging` 和 `logger = logging.getLogger("nanobot.admin")`，与 admin 子模块日志命名保持一致。
- `_git(args)` 捕获异常时记录 `debug`，包含 `args`、`cwd`、异常信息和 `exc_info=True`。
- 继续返回 `None`，保持 `/version` fallback。

### `app/group_ingress/helpers.py`

- `safe_meta(meta_json)` 捕获异常时记录 `debug`，包含 `meta_len` 和异常信息，不记录原始 `meta_json`。
- `get_group_talk_value(session_id)` 捕获异常时记录 `debug`，包含 `session_id`、`fallback=0.5` 和异常信息。
- 两处继续使用既有 `logger = logging.getLogger("nanobot.group_ingress")`。

### `app/memory_digest/builder.py`

- 新增 `import logging` 和 `logger = logging.getLogger("nanobot.memory_digest.builder")`。
- `_safe_meta(meta_json)` 捕获异常时记录 `debug`，包含 `meta_len` 和异常信息，不记录原始 `meta_json`。
- 继续返回 `{}`。

### `docs/todo.md`

- 将「静默吞异常补日志」描述中的漂移路径改为真实路径。
- 将 `H11 save_log` 标注为已完成，避免后续误以为仍要改 `SQLiteMemory.save_log()`。
- 实现完成后再标记整项完成。

## 测试策略

新增 focused regression 测试，先写红灯：

- `PromptManager.render()`：monkeypatch `PromptTracer.record_render` 抛异常，断言渲染仍返回内容，并用 `caplog` 捕获 `nanobot.prompt_manager` 的 debug 日志。
- `build_group_profile_context()`：monkeypatch `build_profile_with_evidence` 抛异常，断言返回 `""`，并捕获 `nanobot.context_legacy` debug。
- `/admin/version`：monkeypatch `subprocess.check_output` 抛异常，断言接口仍 200，且 `nanobot.admin` 有 debug 日志。
- `app.group_ingress.helpers.safe_meta()`：传入非法 JSON，断言返回 `{}`，捕获 `nanobot.group_ingress` debug，且日志不包含完整原始 JSON。
- `get_group_talk_value()`：monkeypatch `core.expression_memory.get_stream_config` 抛异常，断言返回 `0.5`，捕获 fallback debug。
- `MemoryDigestBuilder`：构造含非法 `meta_json` 的 `ChatLog`，断言 build 不崩，捕获 `nanobot.memory_digest.builder` debug。
- `SQLiteMemory.save_log()`：继续运行既有 rollback 测试，证明 H11 当前状态未被破坏。

提交前运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_prompt_manager.py \
  tests/test_group_memory.py::test_deprecated_group_profile_context_logs_build_failure \
  tests/test_admin_api.py::TestAuth::test_version_git_probe_failure_logs_debug \
  tests/test_group_ingress_helpers.py \
  tests/test_memory_digest_builder_quality.py \
  tests/test_kt_integration.py::TestSQLiteMemoryDetachedLogs::test_save_log_rolls_back_when_commit_fails \
  -q
```

最终运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 风险与约束

- 这些日志只能是 `debug`，避免污染正常生产日志。
- 日志不得输出完整 prompt、变量、`meta_json`、用户消息、文件 URL 或 evidence 原文。
- 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
- 不改变任何 API 响应、fallback 返回值、数据库写入行为或 prompt runtime 模板。
