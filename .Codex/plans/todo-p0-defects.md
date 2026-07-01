# TODO P0/P1 缺陷修复实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。不要提交 commit，除非用户明确要求。

**目标：** 按 `docs/todo.md` 的优先级先修复可利用或会造成持久故障的 P0 缺陷，并顺带处理同模块的低风险 P1 边界。

**架构：** 每个缺陷只在所属模块内做最小修复：认证边界在 API dependency 层 fail-closed；私聊缓冲保证 owner/follower 都有超时和清理；定时任务把“生成内容”和“推送重试”解耦；后台 fire-and-forget 任务统一保留强引用；BridgePool 回收前确认 bridge 空闲。

**技术栈：** FastAPI、pytest、asyncio、SQLAlchemy、现有 `NanobotBridge` / `NewAPIClient` / `ScheduledTask`。

---

### 任务 1：C1 认证 token 空配置 fail-closed

**文件：**
- 修改：`api/routes.py`
- 测试：`tests/test_api_auth.py` 或新增 `tests/test_auth_policy.py`

- [ ] **步骤 1：编写失败测试**

测试 `verify_token()` 在 `NANOBOT_API_TOKEN` 为空时抛出 503，而不是直接放行：

```python
def test_verify_token_fails_closed_when_api_token_missing(monkeypatch):
    import pytest
    import api.routes as routes
    from fastapi import HTTPException

    monkeypatch.setattr(routes, "NANOBOT_API_TOKEN", "")

    with pytest.raises(HTTPException) as exc:
        routes.verify_token(authorization=None)

    assert exc.value.status_code == 503
```

- [ ] **步骤 2：运行红灯**

运行：`python -B -m pytest tests/test_auth_policy.py -q`

预期：失败，当前代码直接 `return`。

- [ ] **步骤 3：实现最小修复**

把 `verify_token()` 中 token 空配置分支改成：

```python
if not NANOBOT_API_TOKEN:
    raise HTTPException(status_code=503, detail="API token is not configured")
```

- [ ] **步骤 4：运行绿灯**

运行：`python -B -m pytest tests/test_auth_policy.py tests/test_api.py -q`

预期：全部通过。

### 任务 2：C2 ai_daily 兜底分支不再 NameError

**文件：**
- 修改：`creatures/nanobot/prompts/skills/news_search/tool.py`
- 测试：`tests/test_ai_daily_tool_and_sources.py`

- [ ] **步骤 1：编写失败测试**

monkeypatch `_run_news_daily_pipeline()` 返回空字符串，调用 `AiDailyTool.execute()`，断言返回的是兜底 HTML 而不是 NameError：

```python
def test_ai_daily_empty_pipeline_uses_fallback_html(monkeypatch):
    from creatures.nanobot.prompts.skills.news_search import tool as news_tool
    from tests.async_helpers import run_async

    monkeypatch.setattr(news_tool, "_run_news_daily_pipeline", lambda *args, **kwargs: "")

    result = run_async(news_tool.AiDailyTool().execute({"query": "今天 AI 新闻"}))

    assert result.error is None
    assert "暂无可用资讯" in result.output
```

- [ ] **步骤 2：运行红灯**

运行：`python -B -m pytest tests/test_ai_daily_tool_and_sources.py::test_ai_daily_empty_pipeline_uses_fallback_html -q`

预期：失败，当前兜底引用未定义名。

- [ ] **步骤 3：实现最小修复**

把 `render_html` 和 `FALLBACK_DIGEST` 从局部 import 提升到模块级安全导入，或者在 `_execute()` 兜底分支内明确 import：

```python
from creatures.nanobot.prompts.skills.news_search.news_daily.render import render_html
from creatures.nanobot.prompts.skills.news_search.news_daily.fallback import FALLBACK_DIGEST
```

- [ ] **步骤 4：运行绿灯**

运行：`python -B -m pytest tests/test_ai_daily_tool_and_sources.py tests/test_ai_daily_ingest.py -q`

预期：全部通过。

### 任务 3：E6 定时任务推送失败不重复跑 Agent

**文件：**
- 修改：`core/daily_digest.py`
- 测试：`tests/test_daily_digest.py`

- [ ] **步骤 1：编写失败测试**

构造 due task，mock `_generate_task_message()` 成功、`push_to_qq()` 返回 False，运行 `run_scheduled_tasks()`，断言 `last_run_at` 仍被推进：

```python
def test_scheduled_task_advances_last_run_when_push_fails(db_session, monkeypatch):
    from datetime import datetime, timedelta
    from core.database import ScheduledTask
    import core.daily_digest as daily_digest
    from tests.async_helpers import run_async

    task = ScheduledTask(
        name="失败推送",
        cron_expr="* * * * *",
        target_type="private",
        target_id="1",
        prompt_template="日报",
        enabled=True,
        last_run_at=datetime.now() - timedelta(minutes=2),
    )
    db_session.add(task)
    db_session.commit()

    monkeypatch.setattr(daily_digest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(daily_digest, "_generate_task_message", lambda _task: "内容")

    async def fake_push(*args, **kwargs):
        return False

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    run_async(daily_digest.run_scheduled_tasks())
    db_session.refresh(task)

    assert task.last_run_at is not None
    assert task.last_run_at > datetime.now() - timedelta(seconds=30)
```

- [ ] **步骤 2：运行红灯**

运行：`python -B -m pytest tests/test_daily_digest.py::test_scheduled_task_advances_last_run_when_push_fails -q`

预期：失败，当前只有 push 成功才更新 `last_run_at`。

- [ ] **步骤 3：实现最小修复**

在内容生成成功后、调用 `push_to_qq()` 前设置 `task.last_run_at = now` 并提交；push 成败只影响日志和后续独立重试，不影响 cron 去重。

- [ ] **步骤 4：运行绿灯**

运行：`python -B -m pytest tests/test_daily_digest.py tests/test_schedule_task_tool.py -q`

预期：全部通过。

### 任务 4：E4/E3 后台 create_task 保留强引用

**文件：**
- 修改：`clients/new_api_client.py`
- 修改：`nanobot_kt/bridge.py`
- 测试：`tests/test_new_api_client.py` 或 `tests/test_model_router.py`、`tests/test_kt_framework.py`

- [ ] **步骤 1：编写失败测试**

对 NewAPIClient 增加测试：调用内部 record helper 后，任务对象进入类级 `_background_tasks`，完成后自动移除，并且异常会被日志消费。

- [ ] **步骤 2：实现公共 helper**

在 `NewAPIClient` 内新增：

```python
_background_tasks: set[asyncio.Task] = set()

@classmethod
def _track_background_task(cls, task: asyncio.Task) -> asyncio.Task:
    cls._background_tasks.add(task)
    task.add_done_callback(cls._background_tasks.discard)
    return task
```

所有 `asyncio.create_task(...)` 改为 `self._track_background_task(asyncio.create_task(...))`。

- [ ] **步骤 3：BridgePool stop task 同样保留强引用**

在 `nanobot_kt/bridge.py` 为 `b.stop()` 的 fire-and-forget 增加模块级或类级 task set，使用相同 `add_done_callback(discard)` 模式。

- [ ] **步骤 4：验证**

运行：`python -B -m pytest tests/test_model_router.py tests/test_kt_framework.py -q`

预期：全部通过。

### 任务 5：E2 BridgePool TTL 不 stop 忙碌 bridge

**文件：**
- 修改：`nanobot_kt/bridge.py`
- 测试：`tests/test_kt_framework.py`

- [ ] **步骤 1：编写失败测试**

构造 bridge pool 中一个 stale bridge，并让对应 session lock 处于 locked 状态；调用 `_get_bridge()` 触发 TTL sweep，断言 stale 但 busy 的 bridge 没被 pop/stop。

- [ ] **步骤 2：实现最小修复**

TTL 回收前检查该 key 对应的 session lock：

```python
if session_lock and session_lock.locked():
    continue
```

或引入 `bridge._active_requests` 引用计数，`handle_message` 进入/退出维护，TTL sweep 只回收 `active_requests == 0` 的 bridge。

- [ ] **步骤 3：验证**

运行：`python -B -m pytest tests/test_kt_framework.py -q`

预期：全部通过。

### 任务 6：E1 私聊缓冲取消不永久死锁

**文件：**
- 修改：`api/routes.py`
- 测试：`tests/test_api.py` 或新增 `tests/test_private_buffer.py`

- [ ] **步骤 1：编写失败测试**

直接操作私聊缓冲 helper 或通过 `_proxy_chat` owner 路径模拟 `asyncio.CancelledError`，断言 `_finalize_private_buffer()` 总会 set `done` 并清理 `_private_buffers`。

- [ ] **步骤 2：实现最小修复**

owner 区域改为 `try/finally`，finally 中保证调用 `_finalize_private_buffer(user_id, generation, ...)`；follower 等待 `done_event.wait()` 改成 `asyncio.wait_for(..., timeout=PRIVATE_BUFFER_MAX_WAIT_SECONDS)`；进入新请求前清理 deadline 已过的 buffer。

- [ ] **步骤 3：验证**

运行：`python -B -m pytest tests/test_api.py tests/test_private_buffer.py -q`

预期：全部通过。

### 任务 7：同模块 P1 小修

**文件：**
- 修改：`api/routes.py`
- 修改：`core/daily_digest.py`
- 测试：对应局部测试

- [ ] **步骤 1：H15 `/search_logs` limit 上界**

把 `limit: int = 50` 改成 `Query(default=50, ge=1, le=200)`，并测试 `limit=10000` 返回 422。

- [ ] **步骤 2：H_DIGEST_QUERY 日期过滤**

`generate_daily_digest_for_date()` 在 SQL 层按日期范围过滤 `ChatLog.created_at`，测试只读取目标日期日志。

- [ ] **步骤 3：C4 定时任务模板净化**

`_generate_task_message()` 拼接 prompt 前对 `task.prompt_template` 调用 `sanitize_prompt_text(prompt, 2000)`，测试 `</task_template>` 被转义或剥离，不越界。

### 验收命令

```bash
python -B -m pytest tests/test_auth_policy.py tests/test_ai_daily_tool_and_sources.py tests/test_daily_digest.py tests/test_schedule_task_tool.py -q
python -B -m pytest tests/test_model_router.py tests/test_kt_framework.py tests/test_api.py -q
python -B -m pytest tests/ -q
```
