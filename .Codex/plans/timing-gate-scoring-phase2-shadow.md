# TimingGate 评分体系阶段二 Shadow 接入计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将阶段一的 TimingGate scoring 纯函数以 shadow 字段接入群聊 runtime、ChatLog meta 和 admin timing events，不改变现有 `action` / `hard_rule` / wait 行为。

**架构：** `GroupRuntime` 在每个已有决策返回点附加 `timing_scoring` 解释字段，字段由 `core.timing_score.decide_timing()` 计算。`app.group_ingress.helpers.annotate_group_timing_event()` 将其写入 `meta_json.timing_gate.scoring`，admin API 原样透出 `scoring`，供调试页和后续真实日志评估使用。

**技术栈：** Python dataclass/asdict、pytest、现有 `GroupRuntime` / `ChatLog.meta_json` / admin API 测试。

---

### 任务 1：Runtime Shadow 字段红灯

**文件：**
- 修改：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_returns_no_reply`

- [x] **步骤 1：编写失败的测试**

在 `TestGroupRuntime` 中添加测试，构造一条 `is_directed_to_other=True` 的 ambient 群消息，验证现有 hard rule 行为保持不变，同时返回 `timing_scoring`：

```python
@pytest.mark.asyncio
async def test_directed_to_other_hard_rule_includes_shadow_scoring(self):
    runtime = GroupRuntime()

    result = await runtime.process_message("g1", {
        "sender_id": "u1",
        "sender_name": "A",
        "message": "张三你看看这个",
        "is_directed_to_other": True,
    }, trigger_reason="ambient")

    assert result["action"] == "no_reply"
    assert result["hard_rule"] == "directed_to_other_no_bot_target"
    scoring = result["timing_scoring"]
    assert scoring["stage"] == "rule_shortcut"
    assert scoring["action"] == "no_reply"
    assert scoring["signals"]["sub_signals"]["s_other"] == 0.75
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_returns_no_reply -q
```

预期：FAIL，失败原因是返回结果缺少 `timing_scoring`。

### 任务 2：Meta/Admin 透出红灯

**文件：**
- 修改：`tests/test_admin_api.py`
- 测试：`tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring`

- [x] **步骤 1：编写失败的测试**

在已有 timing events 测试附近增加一条 ChatLog，`meta_json.timing_gate.scoring` 带最小结构，断言 `/api/v1/admin/timing-gate/events` 返回 `scoring` 且旧字段仍存在：

```python
def test_timing_gate_events_returns_scoring(self, client, auth_header):
    from core.database import ChatLog, SessionLocal

    db = SessionLocal()
    try:
        db.add(ChatLog(
            user_id="group_100",
            session_id="group_100",
            role="tool",
            content="群消息",
            meta_json=json.dumps({
                "timing_gate": {
                    "mode": "message",
                    "action": "no_reply",
                    "reason": "directed_to_other_no_bot_target",
                    "hard_rule": "directed_to_other_no_bot_target",
                    "scoring": {
                        "stage": "rule_shortcut",
                        "action": "no_reply",
                        "signals": {"sub_signals": {"s_other": 0.75}},
                    },
                }
            }, ensure_ascii=False),
        ))
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/admin/timing-gate/events", headers=auth_header)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["action"] == "no_reply"
    assert item["hard_rule"] == "directed_to_other_no_bot_target"
    assert item["scoring"]["stage"] == "rule_shortcut"
    assert item["scoring"]["signals"]["sub_signals"]["s_other"] == 0.75
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
python -B -m pytest tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring -q
```

预期：FAIL，失败原因是 admin event 未返回 `scoring`。

### 任务 3：实现 Runtime Shadow Scoring

**文件：**
- 修改：`core/group_runtime/runtime.py`
- 测试：`tests/test_timing_runtime.py`

- [x] **步骤 1：添加 JSON-safe 转换与计算 helper**

在 `core/group_runtime/runtime.py` 中新增私有 helper：

```python
def _has_file_segments(pending: list[GroupPendingMessage]) -> bool:
    return any(
        any(str(seg.get("type") or "").lower() in {"image", "file"} for seg in (msg.segments or []))
        for msg in pending
    )


def _timing_decision_to_dict(decision: TimingDecision) -> dict:
    data = asdict(decision)
    return data
```

并在 `GroupRuntime` 中提供 `_shadow_scoring(...)`，以 pending 合并文本、直接指向、directed_to_other、文件信号、cooldown 生成 `decide_timing()` 结果。

- [x] **步骤 2：在已有返回点附加字段**

对以下返回路径附加同一个 `timing_scoring` 字段，不改变原有 `action`：
- wait refresh
- `directed_to_other` hard rule
- cooldown wait
- force continue
- talk_value gate wait
- rate limit wait
- generation mismatch / state cleaned up（有 snapshot 时附加；无 state 时可省略）
- `_apply_gate_result()` 返回结果

- [x] **步骤 3：运行 runtime 测试验证通过**

运行：

```bash
python -B -m pytest tests/test_timing_runtime.py::TestProcessMessageDirected::test_process_message_directed_to_other_returns_no_reply -q
```

预期：PASS。

### 任务 4：实现 Meta/Admin 透传

**文件：**
- 修改：`app/group_ingress/helpers.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_admin_api.py`

- [x] **步骤 1：写入 ChatLog meta**

在 `annotate_group_timing_event()` 的 `timing` 字典中加入：

```python
"scoring": result.get("timing_scoring"),
```

保留现有过滤规则，空值不落库，非空 dict 正常落库。

- [x] **步骤 2：admin timing event 透出**

在 `_timing_event_dict()` 返回值中加入：

```python
"scoring": timing.get("scoring") or {},
```

旧字段 `action/reason/raw/error_type/parse_error/fallback_action/hard_rule` 不改名。

- [x] **步骤 3：运行 admin 测试验证通过**

运行：

```bash
python -B -m pytest tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring -q
```

预期：PASS。

### 任务 5：阶段二回归、审查与提交

**文件：**
- 测试：`tests/test_timing_score.py`
- 测试：`tests/test_timing_runtime.py`
- 测试：`tests/test_timing_gate.py`
- 测试：`tests/test_timing_gate_prompt_policy.py`
- 测试：`tests/test_admin_api.py`

- [x] **步骤 1：运行 timing 回归**

运行：

```bash
python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py -q
```

预期：全部 PASS。

- [x] **步骤 2：运行 admin targeted 回归**

运行：

```bash
python -B -m pytest tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_stats tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring -q
```

预期：全部 PASS。

- [x] **步骤 3：运行 timing eval**

运行：

```bash
python -m evals.run --suite timing_gate
```

预期：退出码 0，`failed=0`。

- [ ] **步骤 4：Commit**

只暂存阶段二相关文件：

```bash
git add .Codex/plans/timing-gate-scoring-phase2-shadow.md core/group_runtime/runtime.py app/group_ingress/helpers.py api/admin_routes.py tests/test_timing_runtime.py tests/test_admin_api.py
git commit -m "feat(时机门控): 接入评分 shadow 日志"
```
