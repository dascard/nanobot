# TimingGate s_bot live path 收口设计

> 2026-06-20 · 收口 TimingGate scoring 规格与群聊 live 入口之间的 `s_bot` 行为偏差。

## 背景

`docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md` 已把其他 bot sender 设计为 `s_bot=0.70` 的软抑制信号，并通过 `soft_reject_cap` 限制模型翻盘上限。纯函数 `core.timing_score.decide_timing()` 已实现该语义，测试也覆盖了 `is_other_bot=True` 时的 soft reject debug 字段。

当前群聊 live 入口仍在 `GroupIngressService` 中对所有 `bot_sender_kind` 直接返回 `bot_sender_no_timing`。这会让 `sender_is_bot=true` 或 `client_meta.sender_is_bot=true` 的其他 bot 消息永远无法进入 `GroupRuntime`，因此 live path 无法触发 `s_bot` scoring。

## 目标

- 保留当前 bot 自身回声的硬跳过，避免 bot 处理自己刚发出的消息。
- 让其他 bot sender 进入 `GroupRuntime.process_message()`，由 TimingGate scoring 处理 `s_bot` 软抑制。
- 在 pending message 中携带其他 bot 标记，并传给 `decide_timing(is_other_bot=True)`。
- 补充 route 级可观测测试，确认群聊 `ChatLog.meta_json.timing_gate.scoring` 写入真实 scoring 字段。

## 非目标

- 不改变 `core.timing_score` 的系数、阈值、`soft_reject_cap` 公式。
- 不让当前 bot 自身消息进入 TimingGate。
- 不修改 Prompt Runtime 模板、Admin UI 或 RAG 评测。
- 不新增生产数据库字段；其他 bot 标记只走运行时消息结构和日志 meta。

## 方案

### 1. 入口分流

`app/group_ingress/service.py` 当前读取 `meta.sender.bot_sender_kind` 后统一 hard return。改为只对 `current_bot` 保持 hard return：

- `current_bot`：继续 `no_reply`，`hard_rule=bot_sender_no_timing`。
- `explicit_bot` / `client_meta`：继续保存 ambient log，但不 hard return；后续进入 runtime。

这样可以保留防回声安全边界，同时让其他 bot sender 走 scoring。

### 2. runtime 消息结构

在 `core/group_runtime/runtime.py` 的 `GroupPendingMessage` 中增加 `is_other_bot: bool = False`，并在 `to_dict()` 中透出。`GroupIngressService` 组装 `timing_message` 时写入：

```python
"is_other_bot": bot_sender_kind in {"explicit_bot", "client_meta"},
```

`process_message()` 创建 `GroupPendingMessage` 时读取该字段。

### 3. scoring 接入

`GroupRuntime._score_timing()` 调用 `decide_timing()` 时传入：

```python
is_other_bot=any(m.is_other_bot for m in msgs),
```

这样 `s_bot` 与现有 `soft_reject_cap` 在 live path 生效。当前 bot 自身消息不进入 runtime，因此不会被误当作可回复消息。

### 4. 测试策略

先写红灯测试：

- `tests/test_api.py::test_explicit_other_bot_sender_enters_timing_with_s_bot_marker`
  - 构造 `sender_is_bot=true` 且 `@bot` 的群消息。
  - fake runtime 记录 `timing_message`。
  - 预期请求进入 runtime，`timing_message["is_other_bot"] is True`，响应不再带 `hard_rule=bot_sender_no_timing`。

- `tests/test_timing_runtime.py::test_other_bot_at_bot_uses_s_bot_scoring`
  - 构造 `is_at_bot=True`、`is_other_bot=True` 的 runtime 消息。
  - fake gate 返回 `continue` + `model_confidence=0.8`。
  - 预期 `timing_scoring.signals.sub_signals.s_bot == 0.70`，`soft_reject_cap == 0.44`，stage 是 `model_assisted_conflict` 或模型辅助路径。

- `tests/test_api.py::test_group_message_persists_timing_scoring_meta`
  - fake runtime 返回带 `timing_scoring` 的结果。
  - 断言 ambient `ChatLog.meta_json.timing_gate.scoring.stage` 和 `signals` 被写入。

保留现有当前 bot 防回声测试：

- `test_current_bot_sender_archived_but_skips_timing` 仍应通过。

## 验收

- 定向红灯在实现前失败，失败原因分别指向 hard return、缺少 `is_other_bot` 字段或缺少 scoring meta。
- 实现后定向测试通过：

```bash
python -B -m pytest \
  tests/test_api.py::TestGroupMessageStructured::test_explicit_other_bot_sender_enters_timing_with_s_bot_marker \
  tests/test_api.py::test_group_message_persists_timing_scoring_meta \
  tests/test_timing_runtime.py::test_other_bot_at_bot_uses_s_bot_scoring \
  -v -p no:cacheprovider
```

- 相邻回归通过：

```bash
python -B -m pytest tests/test_api.py tests/test_timing_runtime.py tests/test_timing_score.py -v -p no:cacheprovider
```

- 最终全量回归在实现收口后通过：

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

## 风险与约束

- 其他 bot 消息进入 runtime 后，仍可能被 scoring 判为 `continue`。这是设计允许的行为，但会受到 `s_bot` 和 `soft_reject_cap` 限制。
- 当前 bot 自身消息必须继续 hard stop，否则会引入自回声风险。
- 该阶段不改模型 prompt；如果后续发现模型对其他 bot 文本判断不稳，应通过 TimingGate eval 和真实日志标注运营处理。
