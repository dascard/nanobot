# P3-2 私聊 TimingGate 评分持久化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行红灯、绿灯、重构；完成前使用 superpowers:verification-before-completion。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 私聊 `PrivateDecision.timing_scoring` 已计算后，必须随私聊 ChatLog meta 持久化，避免私聊时机决策缺少可回溯评分证据。

**架构：** 在 `/chat` 私聊路径中，把 `_private_decision` 转成一个稳定的 `timing_gate` meta 片段，再传给 `_persist_chat_turn()`。`_persist_chat_turn()` 负责把该片段合并到 user ChatLog meta、assistant ChatLog meta 和 ConversationTurn meta；群聊路径不走该私聊 helper，避免影响既有群聊 timing 事件。

**技术栈：** FastAPI route、SQLAlchemy ORM、pytest、in-memory SQLite。

---

## 文件结构

- 修改：`api/routes.py`
  - 新增私聊 timing meta helper。
  - 扩展 `_persist_chat_turn()` 支持 `timing_meta` 可选参数。
  - 在私聊 `no_reply`、`casual_template`、guardrail `silent`、stream 成功 / 错误 / prompt audit、非流式成功 / 错误 / prompt audit 路径传入 timing meta。
- 修改：`tests/test_api.py`
  - 新增私聊 ChatLog meta 回归测试，覆盖最终回复和 `no_reply` 两条关键路径。
- 修改：`docs/todo.md`
  - 实现后把路线项 10 的“私聊可观测待补齐”改为已补齐，并保留 CI / 真实标注为运营项。
- 修改：`docs/plan_walkthrough.md`
  - 记录 P3-2 的提交、验证命令和下一步。

## 根因记录

- `core/private_timing.PrivateDecision` 已包含 `timing_scoring` 字段。
- `api/routes.py` 中 `_private_decision` 已在私聊路由内可用。
- `_persist_chat_turn()` 当前只把 `req.client_meta` 写入 user ChatLog meta，把 `assistant_meta` 写入 assistant ChatLog meta；没有 `timing_scoring` 参数，因此私聊评分没有进入 ChatLog。
- 群聊已有独立 timing event meta；本计划不改变群聊逻辑。

## 任务 1：写失败测试

**文件：**
- 修改：`tests/test_api.py`

- [ ] **步骤 1：新增私聊成功回复持久化评分测试**

在 `tests/test_api.py` 中新增测试，使用 fake private gate 返回带 `timing_scoring` 的 `PrivateDecision`，并让 fake bridge 返回固定文本：

```python
def test_proxy_chat_persists_private_timing_scoring_meta(client, db_session, monkeypatch):
    from core.private_timing import PrivateDecision

    class Gate:
        async def classify(self, *args, **kwargs):
            return PrivateDecision(
                "reply_now",
                "unit_test_reason",
                1.0,
                "unit_test_raw",
                complexity=5,
                effort="short",
                runtime_preset="lightweight",
                timing_scoring={
                    "stage": "rule_shortcut",
                    "action": "continue",
                    "signals": {"sub_signals": {"is_private": True}},
                },
            )

    class Guardrail:
        def classify(self, *args, **kwargs):
            return {"status": "reply", "complexity": 5}

    class Bridge:
        async def handle_message(self, *args, **kwargs):
            return "私聊回复"

    monkeypatch.setattr("core.private_timing.get_private_gate", lambda: Gate())
    monkeypatch.setattr("api.routes.get_guardrail", lambda: Guardrail())
    monkeypatch.setattr("api.routes.get_bridge", lambda: Bridge())
    monkeypatch.setattr("api.routes.PRIVATE_BUFFER_WINDOW_SECONDS", 0.0)

    response = client.post(
        "/api/v1/chat",
        json={"user_id": "u-private-score", "session_id": "private_u-private-score", "query": "帮我看一下"},
    )

    assert response.status_code == 200, response.text
    rows = (
        db_session.query(ChatLog)
        .filter(ChatLog.user_id == "u-private-score")
        .order_by(ChatLog.id)
        .all()
    )
    assert len(rows) == 2
    user_meta = json.loads(rows[0].meta_json)
    assistant_meta = json.loads(rows[1].meta_json)
    assert user_meta["timing_gate"]["mode"] == "private"
    assert user_meta["timing_gate"]["action"] == "reply_now"
    assert user_meta["timing_gate"]["scoring"]["stage"] == "rule_shortcut"
    assert user_meta["timing_gate"]["scoring"]["signals"]["sub_signals"]["is_private"] is True
    assert assistant_meta["timing_gate"]["mode"] == "private"
    assert assistant_meta["timing_gate"]["scoring"]["action"] == "continue"
```

- [ ] **步骤 2：新增私聊 no_reply 持久化评分测试**

在同一文件中新增测试，fake private gate 返回 `no_reply`，断言 ChatLog 已写入评分 meta，且 assistant meta 也包含同一 `timing_gate` 摘要：

```python
def test_proxy_chat_no_reply_persists_private_timing_scoring_meta(client, db_session, monkeypatch):
    from core.private_timing import PrivateDecision

    class Gate:
        async def classify(self, *args, **kwargs):
            return PrivateDecision(
                "no_reply",
                "ambient_ack",
                1.0,
                "rule_ack",
                effort="ignore",
                runtime_preset="none",
                timing_scoring={"stage": "rule_shortcut", "action": "no_reply"},
            )

    monkeypatch.setattr("core.private_timing.get_private_gate", lambda: Gate())
    monkeypatch.setattr("api.routes.PRIVATE_BUFFER_WINDOW_SECONDS", 0.0)

    response = client.post(
        "/api/v1/chat",
        json={"user_id": "u-private-no-reply", "session_id": "private_u-private-no-reply", "query": "嗯"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_reply"
    rows = (
        db_session.query(ChatLog)
        .filter(ChatLog.user_id == "u-private-no-reply")
        .order_by(ChatLog.id)
        .all()
    )
    assert len(rows) == 2
    assert json.loads(rows[0].meta_json)["timing_gate"]["scoring"]["action"] == "no_reply"
    assert json.loads(rows[1].meta_json)["timing_gate"]["reason"] == "ambient_ack"
```

- [ ] **步骤 3：运行红灯测试**

运行：

```bash
python -m pytest tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta -v
```

预期：两个测试失败，失败原因为 `timing_gate` 不存在或缺少 `scoring` 字段。

## 任务 2：实现私聊 timing meta 持久化

**文件：**
- 修改：`api/routes.py`

- [ ] **步骤 1：新增 meta helper**

在 `api/routes.py` 中新增 helper，把 `PrivateDecision` 转成稳定的 meta 片段：

```python
def _private_timing_meta(decision: Any | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    scoring = getattr(decision, "timing_scoring", None)
    if not isinstance(scoring, dict):
        return None
    return {
        "mode": "private",
        "action": str(getattr(decision, "action", "") or ""),
        "reason": str(getattr(decision, "reason", "") or ""),
        "effort": str(getattr(decision, "effort", "") or ""),
        "runtime_preset": str(getattr(decision, "runtime_preset", "") or ""),
        "scoring": scoring,
    }
```

- [ ] **步骤 2：扩展 `_persist_chat_turn()` 参数**

给 `_persist_chat_turn()` 增加 keyword-only 参数 `timing_meta: dict | None = None`。合并规则：

```python
user_meta = _safe_meta(meta)
user_meta["kind"] = "chat"
if timing_meta:
    user_meta["timing_gate"] = timing_meta

assistant_turn_meta = {"kind": turn_answer_kind}
if timing_meta:
    assistant_turn_meta["timing_gate"] = timing_meta
if assistant_meta:
    assistant_turn_meta.update(assistant_meta)

assistant_chat_meta = dict(assistant_meta or {})
if timing_meta:
    assistant_chat_meta["timing_gate"] = timing_meta
```

assistant ChatLog 的 `meta_json` 改为 `assistant_chat_meta`。

- [ ] **步骤 3：私聊路由调用点传入 timing meta**

在 `_private_decision` 得到后创建：

```python
private_timing_meta = _private_timing_meta(_private_decision)
```

将以下 `_persist_chat_turn()` 调用传入 `timing_meta=private_timing_meta`：

- 私聊 `no_reply`
- 私聊 `casual_template`
- guardrail `silent`
- stream runner error / prompt audit / success / abort 后台持久化
- 非流式 bridge error / prompt audit / success

群聊和非私聊路径保持 `timing_meta=None`。

- [ ] **步骤 4：运行绿灯测试**

运行：

```bash
python -m pytest tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta -v
```

预期：全部通过。

## 任务 3：回归与文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/private-timing-scoring-meta.md`

- [ ] **步骤 1：运行定向回归**

运行：

```bash
python -m pytest tests/test_private_timing.py tests/test_api.py -k "private_timing or private_buffer or proxy_chat_persists_private_timing_scoring_meta or proxy_chat_no_reply_persists_private_timing_scoring_meta" -v
```

预期：全部通过，失败数为 0。

- [ ] **步骤 2：运行相邻回归**

运行：

```bash
python -m pytest tests/test_api.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py -v
```

预期：全部通过，失败数为 0。

- [ ] **步骤 3：同步文档状态**

更新：

- `docs/todo.md` 路线项 10：私聊 `timing_scoring` 已持久化，剩余改为 CI / 真实标注运营项。
- `docs/plan_walkthrough.md`：记录 P3-2 完成状态、验证命令和提交号。
- `.Codex/plans/private-timing-scoring-meta.md`：勾选已完成步骤，记录验证结果。

- [ ] **步骤 4：运行格式检查**

运行：

```bash
git diff --check
```

预期：无输出，退出码为 0。

- [ ] **步骤 5：阶段提交**

只暂存本阶段文件：

```bash
git add api/routes.py tests/test_api.py docs/todo.md docs/plan_walkthrough.md .Codex/plans/private-timing-scoring-meta.md
git commit -m "feat(时机): 持久化私聊评分元信息"
```
