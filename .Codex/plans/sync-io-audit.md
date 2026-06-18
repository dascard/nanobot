# P1-7 残余同步 IO 审计与收口实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 收口 P1-7 中仍可能落入 async 热路径的贴纸预览同步 IO，并为已经隔离的图片和工具层同步 IO 补回归守卫。

**架构：** 保持 `cache_sticker_preview()`、`prepare_image_parts()` 和工具内部同步 HTTP 函数为同步实现，但要求 async 调用点显式通过 `asyncio.to_thread()` 或 Starlette/FastAPI 线程边界卸载。第一刀只修 `background_tasks=None` 的贴纸缓存 fallback，不做全仓 urllib / requests 异步化。

**技术栈：** Python 3.13、FastAPI、Starlette `BackgroundTasks`、pytest、`asyncio.to_thread()`、in-memory SQLite。

---

## 文件结构

- 修改：`app/group_ingress/helpers.py`
  - 删除或弱化 `background_tasks is None` 时直接调用 `cache_sticker_preview()` 的同步 fallback。
- 修改：`app/group_ingress/service.py`
  - 在 async service 层为无 `BackgroundTasks` 场景显式 `await asyncio.to_thread(...)`，或集中跳过即时缓存。
- 修改：`tests/test_api.py` 或创建 `tests/test_group_ingress_sticker_preview.py`
  - 覆盖 `GroupIngressService.handle(..., background_tasks=None)` 不直接阻塞 event loop。
  - 覆盖有 `BackgroundTasks` 时仍只入队后台任务。
- 修改：`tests/test_kt_framework.py`
  - 增加 bridge 多模态附件必须通过 `asyncio.to_thread(prepare_image_parts, ...)` 的守卫。
- 修改：`tests/test_image_generation_tool.py`
  - 增加 `ImageGenerationTool._execute()` 使用 `asyncio.to_thread(self._call_new_api, ...)` 的守卫。
- 修改：`tests/test_image_summary_tool.py`
  - 增加 `ImageSummaryTool._execute()` 使用 `asyncio.to_thread(self._call_qwen, ...)` 的守卫。
- 修改：`tests/test_ai_daily_tool_and_sources.py`
  - 增加 `AiDailyTool._execute()` 使用 `asyncio.to_thread(_run_news_daily_pipeline, ...)` 的守卫。
- 修改：`docs/todo.md`
  - P1-7 完成后记录路线项 2 第三步已完成，保留后续 worker 占用优化。
- 修改：`docs/plan_walkthrough.md`
  - 更新 P1-7 设计、实现、验证和提交记录。

## 当前事实

- P1-6 已完成，下一优先级为 P1-7。
- `docs/superpowers/specs/2026-06-18-sync-io-audit-design.md` 已提交，提交号 `8ce5210`。
- 三个只读子 agent 已完成审计：
  - `image_pipeline` 主链路已由 bridge `asyncio.to_thread` 卸载。
  - `image_generation`、`image_summary`、`ai_daily` 当前工具入口已在 `_execute()` 内自行 `to_thread`。
  - `daily_digest_scheduler()` 的 `time.sleep()` 在独立线程中，不阻塞 ASGI event loop。
  - `core/compaction.py` 当前只从同步 `/context` endpoint 调用，风险是占用 worker，不是事件循环阻塞。
  - `app/group_ingress/helpers.py` 的 `background_tasks=None` fallback 是唯一确认的 async 热路径风险。

## 任务 1：贴纸预览 fallback 不再同步阻塞 async service

**文件：**
- 修改：`app/group_ingress/helpers.py`
- 修改：`app/group_ingress/service.py`
- 测试：`tests/test_group_ingress_sticker_preview.py` 或 `tests/test_api.py`

- [x] **步骤 1：写红灯测试**

新增测试，构造 `GroupIngressService.handle(..., background_tasks=None)`，让 sticker 注册路径命中 `background_tasks=None`。monkeypatch 同步缓存函数为会记录调用线程的 fake，并 monkeypatch `asyncio.to_thread` 使测试能断言 service 层显式卸载。

建议测试形态：

```python
@pytest.mark.asyncio
async def test_group_ingress_sticker_preview_without_background_tasks_uses_to_thread(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    def fake_cache(db, sticker_id, *, force=False):
        calls.append(("cache", sticker_id, force))
        return None

    monkeypatch.setattr("app.group_ingress.service.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("core.sticker_preview.cache_sticker_preview", fake_cache)
    # 构造最小 GroupMessageRequest：segments 含 image/sticker 信息，bridge 不应被调用。
    # 调用 GroupIngressService.handle(req, background_tasks=None)。
    # 断言 calls 中存在 fake_to_thread 对 fake_cache 的卸载调用。
```

如果现有测试辅助构造成本较高，可先用 `register_group_stickers_from_message()` 单元测试固定 helper 不直接调用 `core.sticker_preview.cache_sticker_preview()`，再补 service 层调用测试。

- [x] **步骤 2：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_group_ingress_sticker_preview.py -q -p no:cacheprovider
```

若测试落在 `tests/test_api.py`，运行对应单测节点。

预期：FAIL，当前 `background_tasks=None` 分支会直接同步调用 `cache_sticker_preview()`，不会经过 `asyncio.to_thread()`。

实际：`tests/test_api.py::test_group_message_sticker_preview_without_background_tasks_uses_to_thread` 失败，调用顺序第一项为 `direct_cache`，符合红灯预期。

- [x] **步骤 3：最小实现**

实现原则：

- `app/group_ingress/helpers.py` 只在 `background_tasks` 存在时入队 `cache_sticker_preview_bg`。
- `background_tasks is None` 时，helper 返回注册结果，不直接缓存。
- `app/group_ingress/service.py` 在 async 上下文中收集需要即时缓存的 sticker id，并在 handle 流程中用 `await asyncio.to_thread(cache_sticker_preview, db, sticker_id, force=force)` 处理，或明确跳过即时缓存并记录日志。
- 不改 `cache_sticker_preview()` 的同步实现。

- [x] **步骤 4：运行绿灯**

运行步骤 2 的测试。

预期：PASS。

实际：新增测试通过，`1 passed, 1 warning`。

- [x] **步骤 5：运行贴纸相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py -k "sticker or group_message_image_auto_registers_sticker" -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_sticker_memory.py -q -p no:cacheprovider
```

预期：PASS。

实际：`tests/test_api.py -k "sticker or group_message_image_auto_registers_sticker"` 通过，`6 passed, 70 deselected, 20 warnings`；`tests/test_sticker_memory.py` 通过，`10 passed, 1 warning`。

- [x] **步骤 6：提交任务 1**

运行：

```bash
git add app/group_ingress/helpers.py app/group_ingress/service.py tests/test_group_ingress_sticker_preview.py tests/test_api.py
git commit -m "fix(贴纸): 隔离预览缓存同步 IO"
```

只暂存实际修改过的文件。

## 任务 2：补图片附件 `to_thread` 守卫

**文件：**
- 修改：`tests/test_kt_framework.py`

- [x] **步骤 1：写红灯测试**

在已有多模态附件测试附近新增断言：当 `files` 存在时，`NanobotBridge.handle_message()` 必须调用 `nanobot_kt.bridge.asyncio.to_thread`，且第一个参数是 `prepare_image_parts`。

测试要 monkeypatch `asyncio.to_thread` 为 async fake，直接调用传入函数或返回预构造 image parts，避免真实图片下载。

- [x] **步骤 2：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py -k "multimodal_event_for_files or image_parts" -q -p no:cacheprovider
```

预期：如果当前测试没有守卫，应先 FAIL 于缺少断言或 fake 未被调用；若当前实现已经调用 `to_thread`，可用临时反向 monkeypatch 或先断言一个当前不存在的调用细节确认测试有效。

实际：新增守卫后临时把 `NanobotBridge.handle_message()` 改为直接调用 `prepare_image_parts(...)`，单测失败于 `assert to_thread_calls`，证明测试能捕获缺少线程卸载的回归；随后已恢复生产代码。

- [x] **步骤 3：最小实现**

当前生产代码已使用 `await asyncio.to_thread(prepare_image_parts, ...)`。如果测试红灯暴露实现缺口，只做最小修正；否则不改生产代码，只提交测试守卫。

- [x] **步骤 4：运行绿灯与回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_kt_framework.py tests/test_image_pipeline.py -q -p no:cacheprovider
```

预期：PASS。

实际：守卫单测通过，`1 passed, 1 warning`；图片相关回归通过，`59 passed, 1 warning`。

- [x] **步骤 5：提交任务 2**

运行：

```bash
git add tests/test_kt_framework.py tests/test_image_pipeline.py
git commit -m "test(图片): 守卫附件预处理线程卸载"
```

只暂存实际修改过的文件。

## 任务 3：补 Direct 工具 `to_thread` 守卫

**文件：**
- 修改：`tests/test_image_generation_tool.py`
- 修改：`tests/test_image_summary_tool.py`
- 修改：`tests/test_ai_daily_tool_and_sources.py`

- [ ] **步骤 1：写红灯测试**

分别为 3 个工具新增测试：

- `ImageGenerationTool._execute()` 调用 `asyncio.to_thread(self._call_new_api, ...)`。
- `ImageSummaryTool._execute()` 调用 `asyncio.to_thread(self._call_qwen, files, focus)`。
- `AiDailyTool._execute()` 调用 `asyncio.to_thread(_run_news_daily_pipeline, query, "quality", max_results)`。

测试通过 monkeypatch `asyncio.to_thread` 记录函数对象和参数，不访问外网。

- [ ] **步骤 2：运行红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_image_generation_tool.py tests/test_image_summary_tool.py tests/test_ai_daily_tool_and_sources.py -q -p no:cacheprovider
```

预期：新增测试在未实现守卫断言或当前调用不匹配时 FAIL。若当前生产代码已经满足，先确认测试能通过反向 monkeypatch 失败，保证测试不是空断言。

- [ ] **步骤 3：最小实现**

当前生产代码已具备 `to_thread` 调用。若测试暴露缺口，只做局部修正；否则只提交测试。

- [ ] **步骤 4：运行绿灯**

运行步骤 2 命令。

预期：PASS。

- [ ] **步骤 5：提交任务 3**

运行：

```bash
git add tests/test_image_generation_tool.py tests/test_image_summary_tool.py tests/test_ai_daily_tool_and_sources.py
git commit -m "test(工具): 守卫同步调用线程卸载"
```

只暂存实际修改过的文件。

## 任务 4：同步文档并最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/sync-io-audit.md`

- [ ] **步骤 1：同步路线状态**

在 `docs/todo.md` 路线项 2 记录：

- P1-7 已完成残余同步 IO 审计。
- 贴纸 `background_tasks=None` fallback 已收口。
- image pipeline、Direct 工具、scheduler 和 compaction 的边界已明确。
- `/context` compaction worker 占用和工具专用 executor 属于后续优化，不阻塞路线项 2 完成。

在 `docs/plan_walkthrough.md` 记录 P1-7 提交号和验证结果。

- [ ] **步骤 2：运行引用和静态检查**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/sync-io-audit.md
rg -n "cache_sticker_preview\\(" app/group_ingress api/routes.py api/admin_routes.py core/sticker_preview_jobs.py
rg -n "requests\\.post|urllib\\.request\\.urlopen|time\\.sleep|asyncio\\.to_thread" core/compaction.py core/daily_digest.py core/sticker_preview.py nanobot_kt/image_pipeline.py app/group_ingress creatures/nanobot/prompts/skills
```

预期：`git diff --check` 无输出；`rg` 结果能区分同步边界，不要求清零。

- [ ] **步骤 3：运行定向测试**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api.py tests/test_sticker_memory.py tests/test_kt_framework.py tests/test_image_pipeline.py tests/test_image_generation_tool.py tests/test_image_summary_tool.py tests/test_ai_daily_tool_and_sources.py -q -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 4：运行全量测试**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：0 failures。

- [ ] **步骤 5：提交文档收尾**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/sync-io-audit.md
git commit -m "docs(计划): 同步同步 IO 收口状态"
```

## 执行顺序

1. 任务 1：贴纸预览 fallback 不再同步阻塞 async service。
2. 任务 2：补图片附件 `to_thread` 守卫。
3. 任务 3：补 Direct 工具 `to_thread` 守卫。
4. 任务 4：同步文档并最终验证。

每个任务完成后必须单独验证、单独提交。不要使用 `git add .` 或 `git add -A`。
