# TimingGate s_bot live path 收口实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让群聊 live 入口中的其他 bot sender 进入 TimingGate scoring，并让 `s_bot` soft reject 在真实路径生效。

**架构：** 保留当前 bot 自身回声的入口 hard stop；只把 `explicit_bot` / `client_meta` 标记为 `is_other_bot=True` 并继续交给 `GroupRuntime`。`GroupPendingMessage` 透传该字段，`_score_timing()` 聚合 pending 中的 other bot 标记后调用 `decide_timing(is_other_bot=True)`。

**技术栈：** Python、FastAPI route service、dataclass、pytest、in-memory SQLite。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-timing-gate-sbot-live-path-design.md`
- 设计提交：`6463ee8 docs(时机): 设计 s_bot live path 收口`
- 当前范围：群聊 ingress 分流、runtime pending 字段、scoring 参数透传、route 级 scoring meta 断言、文档状态同步。
- 不纳入本计划：TimingGate 阈值调参、Prompt Runtime 模板、Admin / WebUI、RAG fixture、生产 DB schema。

## 文件结构

- 修改：`tests/test_api.py`
  - 职责：覆盖其他 bot sender 进入 runtime，并覆盖群聊 ambient log 的 `timing_gate.scoring` 持久化。
- 修改：`tests/test_timing_runtime.py`
  - 职责：覆盖 `is_other_bot=True` 的 runtime pending 会传入 scoring 并产生 `s_bot` soft reject。
- 修改：`core/group_runtime/runtime.py`
  - 职责：在 `GroupPendingMessage` 增加 `is_other_bot`，创建 pending 时读取字段，scoring 时传入 `decide_timing()`。
- 修改：`app/group_ingress/service.py`
  - 职责：只对 `current_bot` hard stop；其他 bot sender 进入 runtime 并在 `timing_message` 中带 `is_other_bot=True`。
- 修改：`docs/todo.md`
  - 职责：记录 TimingGate s_bot live path 偏差已收口。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交边界和验证结果。
- 修改：`.Codex/plans/timing-gate-sbot-live-path.md`
  - 职责：执行时勾选步骤并记录红绿灯结果。

## 任务 1：其他 bot sender 进入 runtime 并触发 `s_bot`

**文件：**
- 修改：`tests/test_api.py`
- 修改：`tests/test_timing_runtime.py`
- 修改：`core/group_runtime/runtime.py`
- 修改：`app/group_ingress/service.py`
- 修改：`.Codex/plans/timing-gate-sbot-live-path.md`

- [ ] **步骤 1：编写 route 红灯测试**

在 `tests/test_api.py::TestGroupMessageStructured` 中替换或调整 `test_explicit_other_bot_sender_archived_but_skips_timing`，保留当前 bot hard stop 测试，新增其他 bot 进入 runtime 的断言：

```python
def test_explicit_other_bot_sender_enters_timing_with_s_bot_marker(self, client, db_session, monkeypatch):
    calls = []

    class FakeGroupRuntime:
        async def process_message(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {
                "action": "no_reply",
                "reason": "unit_test_other_bot_scoring",
                "generation": 0,
                "timing_scoring": {
                    "stage": "rule_shortcut",
                    "signals": {"sub_signals": {"s_bot": 0.70}},
                },
            }

        def note_bot_replied(self, *args, **kwargs):
            raise AssertionError("no reply should be sent in this test")

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: FakeGroupRuntime())

    resp = client.post("/api/v1/group/message", json={
        "group_id": "123456",
        "sender_id": "alice-bot",
        "sender_name": "[BOT]Alice",
        "message": "@Nanobot 帮我确认一下",
        "self_id": "999888",
        "bot_id": "999888",
        "bot_name": "Nanobot",
        "sender_is_bot": True,
        "is_at_bot": True,
        "mentions": [{"user_id": "999888", "nickname": "Nanobot"}],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "no_reply"
    assert data.get("hard_rule") != "bot_sender_no_timing"
    assert calls
    timing_message = calls[0][0][1]
    assert timing_message["is_other_bot"] is True

    meta = self._meta(db_session)
    assert meta["sender"]["is_bot"] is True
    assert meta["sender"]["bot_sender_kind"] == "explicit_bot"
    assert meta["timing_gate"]["scoring"]["signals"]["sub_signals"]["s_bot"] == 0.70
```

- [ ] **步骤 2：运行 route 红灯**

运行：

```bash
python -B -m pytest tests/test_api.py::TestGroupMessageStructured::test_explicit_other_bot_sender_enters_timing_with_s_bot_marker -v -p no:cacheprovider
```

预期：FAIL。当前代码会在 `bot_sender_no_timing` hard return，`calls` 为空或响应带 `hard_rule=bot_sender_no_timing`。

- [ ] **步骤 3：编写 runtime 红灯测试**

在 `tests/test_timing_runtime.py` 中新增测试：

```python
@pytest.mark.asyncio
async def test_other_bot_at_bot_uses_s_bot_scoring(monkeypatch):
    runtime = GroupRuntime()
    gate_calls = []

    async def fake_gate(group_id, pending, _ctx, trigger_reason):
        gate_calls.append((group_id, pending[0].is_other_bot, trigger_reason))
        return {
            "action": "continue",
            "reason": "other bot explicitly asked",
            "parse_quality": "json",
            "model_confidence": 0.8,
        }

    monkeypatch.setattr(runtime, "_call_gate", fake_gate)

    result = await runtime.process_message("g1", {
        "sender_id": "bot-a",
        "sender_name": "BotA",
        "message": "@bot 请确认",
        "is_at_bot": True,
        "is_other_bot": True,
    }, trigger_reason="at_bot")

    assert gate_calls == [("group_g1", True, "at_bot")]
    scoring = result["timing_scoring"]
    assert scoring["signals"]["sub_signals"]["s_bot"] == 0.70
    assert scoring["soft_reject_cap"] == 0.44
    assert scoring["stage"] == "model_assisted_conflict"
    assert scoring["model_used"] is True
```

- [ ] **步骤 4：运行 runtime 红灯**

运行：

```bash
python -B -m pytest tests/test_timing_runtime.py::test_other_bot_at_bot_uses_s_bot_scoring -v -p no:cacheprovider
```

预期：FAIL。当前 `GroupPendingMessage` 没有 `is_other_bot` 字段，或 scoring 中 `s_bot` 仍为 `0.0`。

- [ ] **步骤 5：实现最小生产代码**

修改 `core/group_runtime/runtime.py`：

- `GroupPendingMessage` 增加字段 `is_other_bot: bool = False`。
- `to_dict()` 增加 `"is_other_bot": self.is_other_bot`。
- `process_message()` 创建 `GroupPendingMessage` 时读取 `bool(message.get("is_other_bot"))`。
- `_score_timing()` 调用 `decide_timing()` 时传入 `is_other_bot=any(m.is_other_bot for m in msgs)`。

修改 `app/group_ingress/service.py`：

- `bot_sender_kind == "current_bot"` 时保持原 hard return。
- `explicit_bot` / `client_meta` 不 hard return。
- 组装 `timing_message` 时增加：

```python
"is_other_bot": bot_sender_kind in {"explicit_bot", "client_meta"},
```

- [ ] **步骤 6：运行任务 1 绿灯**

运行：

```bash
python -B -m pytest \
  tests/test_api.py::TestGroupMessageStructured::test_current_bot_sender_archived_but_skips_timing \
  tests/test_api.py::TestGroupMessageStructured::test_explicit_other_bot_sender_enters_timing_with_s_bot_marker \
  tests/test_timing_runtime.py::test_other_bot_at_bot_uses_s_bot_scoring \
  -v -p no:cacheprovider
```

预期：3 passed。

- [ ] **步骤 7：运行相邻回归**

运行：

```bash
python -B -m pytest tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 8：提交任务 1**

运行：

```bash
git add tests/test_api.py tests/test_timing_runtime.py core/group_runtime/runtime.py app/group_ingress/service.py .Codex/plans/timing-gate-sbot-live-path.md
git commit -m "fix(时机): 接入其他 bot 软抑制评分"
```

## 任务 2：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/timing-gate-sbot-live-path.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在路线项 10 的已完成列表中追加：其他 bot sender live path 已接入 `s_bot` soft reject，当前 bot 自身回声仍 hard stop。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

新增 “TimingGate s_bot live path 收口” 记录，写明设计提交、实现提交、红灯、绿灯和验证结果。

- [ ] **步骤 3：更新本计划执行记录**

在本文顶部或对应任务中追加真实提交 SHA 和验证输出。

- [ ] **步骤 4：文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|T[B]D|F[I]XME|x{3}|X{3}|\\x{2026}\\x{2026}|\\.\\.\\." docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-gate-sbot-live-path.md
rg -n $'\357\277\275' docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-gate-sbot-live-path.md
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-gate-sbot-live-path.md
```

预期：前两个命令无匹配，`git diff --check` 无输出。

- [ ] **步骤 5：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py -v -p no:cacheprovider
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 6：提交任务 2**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-gate-sbot-live-path.md
git commit -m "docs(时机): 收口 s_bot live path 状态"
```

## 提交边界

- 设计阶段：`docs(时机): 设计 s_bot live path 收口`（已完成：`6463ee8`）。
- 计划阶段：`docs(计划): 记录 s_bot live path 收口计划`。
- 任务 1：`fix(时机): 接入其他 bot 软抑制评分`。
- 任务 2：`docs(时机): 收口 s_bot live path 状态`。
