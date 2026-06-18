# P1-7 残余同步 IO 审计与收口设计

> 2026-06-18 · P1-7 目标是完成路线项 2 的第三步：审计 compaction、image、sticker 与工具层残余同步 IO，确认哪些路径已被线程边界隔离，收口仍可能落入 async 热路径的调用点。

---

## 背景

`docs/todo.md` 路线项 2 已完成应用级 `aiohttp.ClientSession` 复用，核心 LLM 请求不再逐请求创建 session。剩余风险集中在同步 HTTP、文件 IO 和 `time.sleep` 是否仍可能直接运行在 ASGI 事件循环里。

本次审计按 AGENTS.md 的上下文预算要求拆给 3 个只读子 agent：

- 核心后台模块：`core/compaction.py`、`core/daily_digest.py`、`core/sticker_preview.py`。
- 图片与贴纸链路：`nanobot_kt/image_pipeline.py`、群聊入口、贴纸预览入口。
- 工具层：`image_generation`、`image_summary`、`ai_daily` 与 `news_daily` 子流水线。

审计结论不是“全仓不能出现 urllib / requests”，而是“不能在 async 热路径里直接执行阻塞 IO”。同步 worker、FastAPI 同步 endpoint、Starlette `BackgroundTasks` 线程池和显式 `asyncio.to_thread` 都是可接受边界。

## 审计结论

### 需要收口

`app/group_ingress/helpers.py` 的 `register_group_stickers_from_message()` 在 `background_tasks is None` 时会直接调用 `cache_sticker_preview(db, sticker_id)`。这个 helper 可从 `GroupIngressService.handle()` 的 async 路径进入，因此程序化调用如果不传 `BackgroundTasks`，会在事件循环里执行 DNS、`urllib.request.urlopen()`、`resp.read()`、本地文件写入和图片解码。

生产 `/group/message` 通常会注入 `BackgroundTasks`，主入口大体安全；但这个 fallback 是真实 async 热路径风险，且测试里已经存在 `background_tasks=None` 的调用形态。P1-7 的第一刀应删除或隔离这个同步 fallback。

### 已隔离，补守卫

- `NanobotBridge.handle_message()` 对图片附件调用 `await asyncio.to_thread(prepare_image_parts, ...)`，`image_pipeline` 内部的 urllib、本地文件读取、PIL 压缩和缓存写入不会直接阻塞事件循环。
- 私聊和群聊图片预缓存通过 Starlette `BackgroundTasks` 调度同步函数，Starlette 会把同步 background task 放入线程池。
- `image_generation`、`image_summary` 和当前注册的 `ai_daily` 都是 `ExecutionMode.DIRECT` 工具，但各自 `_execute()` 已用 `asyncio.to_thread()` 包住同步 HTTP / 抓取逻辑。
- `daily_digest_scheduler()` 的 `time.sleep()` 运行在独立 daemon thread，不在 ASGI 事件循环里。
- `core/compaction.py` 的 `requests.post()` 和 retry `time.sleep()` 当前只从同步 `/context` endpoint 调用，FastAPI 会放入线程池；风险是占用 worker，不是事件循环阻塞。

### 后续可优化但不纳入第一刀

- `/context` 的 compaction 远端 LLM 最坏会占用线程较久，可后续改成异步 HTTP 或显式后台压缩。
- `ai_daily` 会在线程里触发多源抓取和内部线程池，可后续接专用 bounded executor。
- 贴纸公开图片代理和 admin preview 是同步 endpoint，当前安全；如果未来改成 `async def`，必须显式 `to_thread`。

## 方案选择

### 方案 A：全仓异步化 urllib / requests

把所有 `urllib`、`requests` 和同步文件读取替换为 async HTTP / async 文件 IO。

结论：不采用。范围过大，且许多路径已经由线程边界隔离。一次性替换会同时触碰工具、新闻抓取、图片预处理和管理端点，回归面远超 P1-7。

### 方案 B：只修 `background_tasks=None` 贴纸 fallback，并补隔离守卫

把异步服务路径中的贴纸预览缓存调度从同步 helper 中拆出来：

- helper 只负责注册 sticker 记录，并在有 `BackgroundTasks` 时入队。
- async service 在没有 `BackgroundTasks` 时用 `await asyncio.to_thread(...)` 调用同步缓存，或跳过即时缓存并记录日志。
- 补测试证明 `background_tasks=None` 不再直接阻塞事件循环。
- 补测试证明图片附件处理和 Direct 工具阻塞调用仍走 `to_thread`。

结论：采用。它直接解决唯一确认的 async 热路径风险，同时把“已隔离”的路径变成回归守卫。

### 方案 C：只写审计文档，不改代码

记录当前大多数路径已经安全，不做实现。

结论：不采用。`background_tasks=None` 分支是可触发风险，且修复边界明确，不应只文档化。

## 实现边界

### 1. 贴纸缓存调度边界

目标文件：

- `app/group_ingress/helpers.py`
- `app/group_ingress/service.py`
- `api/routes.py` 中重复的旧 helper（如果仍保留等价 fallback）
- `tests/test_api.py` 或新增 `tests/test_group_ingress_sticker_preview.py`

预期行为：

- 有 `BackgroundTasks`：继续 `background_tasks.add_task(cache_sticker_preview_bg, sticker_id)`。
- 无 `BackgroundTasks` 且处于 async service：不得同步调用 `cache_sticker_preview()`；必须 `await asyncio.to_thread(...)` 或跳过即时缓存。
- helper 不应在不知道调用上下文的情况下执行阻塞下载。

### 2. 图片 pipeline 守卫

目标文件：

- `tests/test_kt_framework.py` 或 `tests/test_image_pipeline.py`

预期行为：

- `NanobotBridge.handle_message()` 在 `files` 存在时必须通过 `asyncio.to_thread(prepare_image_parts, ...)` 进入图片预处理。
- 测试只验证线程卸载边界，不联网、不读取真实远端图片。

### 3. Direct 工具守卫

目标文件：

- `tests/test_image_generation_tool.py`
- `tests/test_image_summary_tool.py`
- `tests/test_ai_daily_tool_and_sources.py`（如现有结构适合）

预期行为：

- `image_generation._execute()` 通过 `asyncio.to_thread(self._call_new_api, ...)` 执行同步生成请求。
- `image_summary._execute()` 通过 `asyncio.to_thread(self._call_qwen, ...)` 执行同步视觉请求。
- `ai_daily._execute()` 通过 `asyncio.to_thread(_run_news_daily_pipeline, ...)` 执行新闻抓取流水线。

这些测试用于防止后续工具重构把同步 HTTP 移回 event loop。

### 4. scheduler 与 compaction 记录

目标文件：

- `docs/todo.md`
- `docs/plan_walkthrough.md`

预期行为：

- 文档记录 `daily_digest_scheduler()` 的 `time.sleep()` 属于独立线程，不作为 P1-7 必修。
- 文档记录 `/context` compaction 当前不阻塞 event loop，但仍可能占用请求 worker，后续可单独优化。

## 验收标准

- [ ] `register_group_stickers_from_message()` 不再在 `background_tasks is None` 时直接执行 `cache_sticker_preview()`。
- [ ] `GroupIngressService.handle(..., background_tasks=None)` 的测试证明贴纸缓存不会阻塞 event loop，或明确跳过即时缓存。
- [ ] 图片附件进入 `prepare_image_parts()` 前有 `asyncio.to_thread` 回归测试。
- [ ] 当前注册的 Direct 工具同步 IO 有 `to_thread` 回归测试，至少覆盖 `image_generation`、`image_summary` 和 `ai_daily`。
- [ ] `rg` 审计结果区分“已隔离同步 IO”和“async 热路径风险”，不要求全仓零 urllib。
- [ ] 定向测试和全量测试通过后，更新 `docs/todo.md` 与 `docs/plan_walkthrough.md` 的 P1-7 状态。

## 测试计划

优先新增红灯测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py -k "group_message_image_auto_registers_sticker" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py -k "multimodal_event_for_files" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_image_generation_tool.py tests/test_image_summary_tool.py -q -p no:cacheprovider
```

实现后运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py tests/test_kt_framework.py tests/test_image_pipeline.py tests/test_image_generation_tool.py tests/test_image_summary_tool.py tests/test_ai_daily_tool_and_sources.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

## 风险与控制

- **误判同步边界。** 控制：测试聚焦调用边界，不只做静态 grep。
- **把 helper 改成 async 牵连过大。** 控制：保持同步 helper 只做注册；异步卸载放在 service 层。
- **默认 executor 被长任务占满。** 控制：本阶段只解决 event loop 阻塞；专用 executor / 限流作为后续优化。
- **测试引入真实 sleep 或联网。** 控制：所有测试用 monkeypatch / fake 函数，不访问外网，不真实等待。

## 后续

P1-7 收口后，路线项 2 可以标记为完成。下一优先级回到 P1-8 模型能力校验：为模型配置补 `supports_image` / `supports_tools` / `supports_stream`，并在请求构造前按能力过滤和降级。
