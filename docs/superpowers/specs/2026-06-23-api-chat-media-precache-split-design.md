# 普通 API Chat Media Precache 拆分设计

日期：2026-06-23

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成，当前活跃队列只剩
`api/routes.py`。第二十刀已经把 Chat Persona Context 格式化 helper 拆到
`api/chat_persona_context.py`，`api/routes.py` 降至 1236 行。下一刀继续沿
`/chat` 主链路做低风险小边界拆分，不迁移完整 `proxy_chat()`。

`api.routes._schedule_image_precache()` 目前只负责三件事：

- 通过父模块 `_normalize_files()` 过滤图片引用。
- 在没有有效文件或没有 `BackgroundTasks` 时直接返回。
- 懒加载 `nanobot_kt.image_pipeline.precache_image_sources` 并调用
  `background_tasks.add_task()`。

这段逻辑与 Bridge、私聊缓冲、guardrail、落库、SSE 和 response envelope 无关，
适合拆出为独立 helper，减少 `api/routes.py` 的职责密度。

## 目标

新增 `api/chat_media_precache.py`，承载 `/chat` 图片预缓存调度的唯一实现。父模块
`api.routes._schedule_image_precache()` 保持原名称、`__module__ == "api.routes"` 和
调用位置，只委托新模块执行。

本阶段完成后：

- `proxy_chat()` 仍在 `api.routes`。
- `_schedule_image_precache()` 仍是父模块 wrapper，可被测试或调用方 monkeypatch。
- 新模块不反向导入 `api.routes`。
- 新模块不新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 图片预缓存仍通过 Starlette `BackgroundTasks` 执行，不在请求协程内同步下载图片。

## 非目标

- 不迁移 `/chat` 路由本体。
- 不迁移 `StreamingResponse`、stream finalizer、SSE done/error/disconnect 分支。
- 不迁移 `get_bridge()`、Bridge 调用、runtime payload、Prompt Runtime 输入或
  `enriched_query` 组装。
- 不迁移 `_persist_chat_turn()`、私聊缓冲、guardrail、push envelope 或 response
  envelope。
- 不调整 `nanobot_kt.image_pipeline.precache_image_sources` 的实现、并发策略、缓存策略
  或错误处理。
- 不修改默认 Prompt Runtime 模板，因为本阶段不改变 prompt 输入、conversation 结构
  或工具输出契约。

## 接口设计

新模块：`api/chat_media_precache.py`

```python
from collections.abc import Callable
from typing import Any

from api import chat_content_helpers


def schedule_image_precache(
    background_tasks: Any,
    files: Any,
    *,
    source_type: str,
    source_name_prefix: str,
    normalize_files: Callable[[Any], list[str]] = chat_content_helpers.normalize_files,
    precache_image_sources: Callable[..., Any] | None = None,
) -> None:
    ...
```

接口要点：

- `background_tasks` 使用 `Any`，避免新模块为了类型标注导入 FastAPI。
- `files` 使用 `Any`，保持与 `ChatProxyRequest.files` 和旧 wrapper 的宽松输入一致。
- `normalize_files` 是可注入依赖，父模块 wrapper 传入 `_normalize_files`，确保
  `api.routes._normalize_files` 这个旧 patch point 仍能影响实际行为。
- `precache_image_sources` 是可注入依赖，测试可传入假函数；生产路径为空时在函数内部
  懒加载 `nanobot_kt.image_pipeline.precache_image_sources`。
- 无有效文件或 `background_tasks is None` 时直接返回，不触发懒加载。

父模块：`api/routes.py`

```python
def _schedule_image_precache(
    background_tasks: BackgroundTasks | None,
    files: Optional[List[str]],
    *,
    source_type: str,
    source_name_prefix: str,
) -> None:
    return chat_media_precache.schedule_image_precache(
        background_tasks,
        files,
        source_type=source_type,
        source_name_prefix=source_name_prefix,
        normalize_files=_normalize_files,
    )
```

`proxy_chat()` 调用点保持不变，仍在屏蔽规则检查后、画像查询前调度图片预缓存。

## 测试策略

新增 `tests/test_api_chat_media_precache_split.py`：

- 源码扫描：`api/chat_media_precache.py` 不导入父模块，不包含 `asyncio.run` 或
  `run_awaitable_sync`。
- 父模块 wrapper 契约：`routes._schedule_image_precache.__module__ == "api.routes"`。
- 父模块 wrapper 委托：monkeypatch `api.chat_media_precache.schedule_image_precache`，
  确认父模块传入 `background_tasks`、`files`、`source_type`、`source_name_prefix`
  和可调用的 `normalize_files`。
- 新模块行为：无文件或 `background_tasks is None` 时不调用 `add_task()`。
- 新模块行为：有效文件会调用 `background_tasks.add_task(precache_func, normalized_files,
  source_type=..., source_name_prefix=...)`，并保持文件顺序与过滤语义。

相邻回归：

- `tests/test_api_chat_helpers_split.py::test_legacy_parent_chat_helper_wrappers_keep_api_routes_module`
- `tests/test_api_chat_runtime_facade_split.py::test_chat_runtime_facade_uses_api_routes_get_bridge_patch_point`
- `tests/test_api_chat_runtime_facade_split.py::test_chat_runtime_facade_split_keeps_proxy_chat_in_parent_routes`
- `tests/test_asyncio_run_policy.py`

最终收口前运行全量 `python -B -m pytest -p no:cacheprovider tests/ -v`。

## 风险与约束

- 父模块 `_schedule_image_precache()` 不能消失。现有测试和私聊快速 helper 会直接
  monkeypatch 这个名字。
- 新模块不能顶层导入 `nanobot_kt.image_pipeline`，否则会扩大 `api.routes` 导入副作用。
- 父模块 wrapper 必须传入 `_normalize_files`，不能在新模块里固定复制当前函数对象，否则
  父模块 patch point 会失效。
- 不能把 `background_tasks.add_task()` 改成直接调用预缓存函数；图片处理仍必须离开请求主链路。
- 不能吞掉或记录原始图片 URL 以外的新敏感信息；本阶段不新增日志。

## 验收标准

- 设计文档、实现计划、红灯测试、新模块、父模块接入和文档收口各自独立提交。
- 红灯阶段能证明新模块或父模块委托尚不存在。
- 绿灯阶段 split 测试、相邻回归、静态检查和全量测试通过。
- `api/routes.py` 行数下降，且 `/chat` 对外请求 / 响应契约保持不变。
