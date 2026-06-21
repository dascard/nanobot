# GroupRuntime 状态与评分拆分实现计划

## 执行结果摘要（2026-06-21）

- 行为基线：新增 `tests/test_group_runtime_split_compat.py` 后运行
  `python -m pytest tests/test_group_runtime_split_compat.py -v`，
  结果为 `5 passed, 1 warning in 0.76s`。这些测试锁定拆分前已有兼容行为，
  因此记录为行为基线绿灯。
- 状态拆分定向：运行 `tests/test_group_runtime_split_compat.py`、
  `tests/test_group_runtime_ids.py`、`TestGateState`、
  `TestGroupPendingMessageDirected`、`TestShouldSuppressDirected`，
  结果为 `26 passed, 1 warning in 1.24s`。
- Scoring 定向：运行 `tests/test_group_runtime_split_compat.py`、
  `tests/test_timing_runtime.py`、`tests/test_timing_score.py`、
  `tests/test_group_runtime_ids.py`，结果为 `89 passed, 1 warning in 2.35s`。
- 相邻回归：运行群响应 envelope、group message structured、API timing gate
  和 KT note bot replied 相关测试，结果为 `32 passed, 21 warnings in 11.04s`。
- 静态与行数检查：`python -m compileall core/group_runtime -q` 无输出；
  相关文件 `git diff --check` 无输出；`asyncio.run` 在本次拆分范围内无匹配。
  行数为 `runtime.py` 722 行、`constants.py` 19 行、`state.py` 397 行、
  `scoring.py` 330 行、兼容测试 137 行。
- 全量回归：首次全量在 `tests/test_persona_preprocess.py` 暴露既有测试
  `hash()` seed 非确定性，固定 `PYTHONHASHSEED=135` 可稳定复现；已单独提交
  `4cbab07 test(画像预处理): 固定正交向量 mock`。修复后固定 seed 单测通过，
  `TestProcessCandidatesWithMockEmbedder` 结果为 `7 passed, 1 warning in 1.02s`。
  最终提交前全量 `python -m pytest tests/ -v` 结果为
  `1497 passed, 6 skipped, 139 warnings in 119.42s`。
- 子 agent 只读规格审查：未发现拆分结构、旧导入路径、循环依赖或
  `asyncio.run` 问题；其指出的计划摘要和暂存范围要求已在提交前处理。
- 阶段提交：设计提交 `b4ae8a5`；计划提交 `4d6614d`；测试稳定性提交
  `4cbab07`；实现提交 `0018d02`。
- 文档收口：已同步 `docs/todo.md` 与 `docs/plan_walkthrough.md`；提交前
  `git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/group-runtime-state-scoring-split.md`
  和占位符扫描脚本均无输出，全量 `python -m pytest tests/ -v` 结果为
  `1497 passed, 6 skipped, 139 warnings in 111.55s`。

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `core/group_runtime/runtime.py` 中的常量、状态模型、pending helper 和 scoring 私有方法拆到独立模块，让 `runtime.py` 降到 800 行以下，同时保持旧导入路径和主状态机行为不变。

**架构：** `constants.py` 持有群运行时常量；`state.py` 持有 `GroupPendingMessage`、`GroupChatState` 和 pending/scoring 纯 helper；`scoring.py` 提供 `GroupRuntimeScoringMixin`，承载原 `GroupRuntime` 的 scoring 私有方法。`runtime.py` 保留 `process_message()`、`handle_timer_fired()`、`_apply_gate_result()`、`_call_gate()`、`_build_timing_context()` 和全局单例，并从新模块导入重导出旧符号。

**技术栈：** Python 3.12、pytest、asyncio、dataclasses、项目既有 TimingGate / group runtime 模块。

---

## 文件职责

- 创建：`tests/test_group_runtime_split_compat.py`
  - 锁定旧 `core.timing_runtime` 和 `core.group_runtime.runtime` 导入兼容。
  - 锁定 pending payload 方向格式、引用格式、source id 顺序。
  - 锁定 `GroupPendingMessage.__post_init__()` 的 at-bot 优先级。
  - 锁定 wait delay clip、model confidence 解析和 `_score_timing()` 入参映射。
- 创建：`core/group_runtime/constants.py`
  - 持有原 `runtime.py` 中的群运行时常量和 trigger 集合。
- 创建：`core/group_runtime/state.py`
  - 持有 `GroupPendingMessage`、`GroupChatState`、pending payload helper、scoring 信号 helper、
    `_clip_timing_wait_delay()`、`_model_confidence_from_gate_result()` 和
    `should_suppress_directed_to_other()`。
- 创建：`core/group_runtime/scoring.py`
  - 持有 `GroupRuntimeScoringMixin`，迁移原 `GroupRuntime` 的 scoring / policy / cooldown
    / recent follow-up 私有方法。
- 修改：`core/group_runtime/runtime.py`
  - 删除已迁移实现，改为导入并重导出旧符号。
  - `GroupRuntime` 继承 `GroupRuntimeScoringMixin`。
  - 保留主状态机、模型调用、context 构造、快照和全局单例。
- 修改：`.Codex/plans/group-runtime-state-scoring-split.md`
  - 实现后记录红灯、绿灯、定向回归、全量回归和提交号。
- 修改：`docs/todo.md`
  - 文档收口阶段更新 P3 超大文件拆分进展和 `runtime.py` 行数。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口阶段追加本阶段执行记录、验证证据和下一步建议。

## 任务 1：补拆分兼容红灯测试

**文件：**
- 创建：`tests/test_group_runtime_split_compat.py`

- [x] **步骤 1：新增旧导入路径兼容测试**

创建 `tests/test_group_runtime_split_compat.py`，写入：

```python
from __future__ import annotations


def test_group_runtime_split_keeps_legacy_import_paths():
    import core.group_runtime.runtime as runtime_module
    import core.timing_runtime as timing_runtime

    assert timing_runtime.PendingMessage is runtime_module.GroupPendingMessage
    assert timing_runtime.GateState is runtime_module.GroupChatState
    assert timing_runtime.GroupRuntime is runtime_module.GroupRuntime
    assert timing_runtime._pending_payload is runtime_module._pending_payload
    assert timing_runtime.MAX_PENDING == runtime_module.MAX_PENDING
    assert timing_runtime.BOT_REPLY_COOLDOWN_SEC == runtime_module.BOT_REPLY_COOLDOWN_SEC

    runtime = runtime_module.GroupRuntime()
    assert hasattr(runtime, "_score_timing")
    assert hasattr(runtime, "_cooldown_scoring_shortcut")
    assert callable(runtime_module.GroupRuntime._build_timing_context)
    assert callable(runtime_module.should_suppress_directed_to_other)
```

- [x] **步骤 2：新增 pending payload 方向格式测试**

在同一文件追加：

```python
def test_pending_payload_preserves_direction_reference_and_source_ids():
    from core.group_runtime.runtime import GroupPendingMessage, _pending_payload

    msgs = [
        GroupPendingMessage(
            sender_id="u1",
            sender_name="小明",
            message="@Nanobot 看看",
            message_id="m1",
            ts=1,
            is_at_bot=True,
            directed={"at_bot": True, "directed_to_other": False},
            mentions=[{"user_id": "bot", "nickname": "Nanobot", "is_bot": True}],
        ),
        GroupPendingMessage(
            sender_id="u2",
            sender_name="小红",
            message="@小明 这个呢",
            message_id="m2",
            ts=2,
            directed={"at_others": True, "reply_to_others": True, "directed_to_other": True},
            mentions=[{"user_id": "u1", "nickname": "小明", "is_bot": False}],
            reply_to={"sender_id": "u1", "sender_name": "小明", "content": "上一条消息"},
        ),
    ]

    payload = _pending_payload(msgs)

    assert payload["source_message_ids"] == ["m1", "m2"]
    assert "[指向性] @bot" in payload["pending_text"]
    assert "[指向性] @其他人: 小明" in payload["pending_text"]
    assert "[指向性] 回复其他人" in payload["pending_text"]
    assert "[引用] 小明: 上一条消息" in payload["pending_text"]
```

- [x] **步骤 3：新增 directed_to_other 派生优先级测试**

在同一文件追加：

```python
def test_pending_message_does_not_derive_directed_to_other_when_at_bot():
    from core.group_runtime.runtime import GroupPendingMessage

    msg = GroupPendingMessage(
        sender_id="u1",
        sender_name="小明",
        message="@Nanobot @小红",
        is_at_bot=True,
        directed={"at_bot": True, "at_others": True, "directed_to_other": True},
    )

    assert msg.is_directed_to_other is False
```

- [x] **步骤 4：新增纯 helper 边界测试**

在同一文件追加：

```python
def test_timing_wait_delay_and_model_confidence_helpers_keep_contract():
    from core.group_runtime.runtime import (
        _clip_timing_wait_delay,
        _model_confidence_from_gate_result,
    )

    assert _clip_timing_wait_delay(None) == 5
    assert _clip_timing_wait_delay("bad") == 5
    assert _clip_timing_wait_delay(1) == 3
    assert _clip_timing_wait_delay(999) == 15

    assert _model_confidence_from_gate_result({"model_confidence": "0.7"}) == 0.7
    assert _model_confidence_from_gate_result({"model_confidence": "nan"}) == 0.0
    assert _model_confidence_from_gate_result({"parse_quality": "legacy"}) == 0.5
    assert _model_confidence_from_gate_result({"parse_quality": "invalid"}) == 0.0
    assert _model_confidence_from_gate_result({"parse_quality": "network_error"}) == 0.0
    assert _model_confidence_from_gate_result({}) == 0.8
```

- [x] **步骤 5：新增 `_score_timing()` 入参映射测试**

在同一文件追加：

```python
def test_score_timing_maps_pending_signals_to_decide_timing(monkeypatch):
    import core.group_runtime.runtime as runtime_module

    captured = {}

    class FakeDecision:
        action = "continue"
        delay_seconds = None
        reason = "fake"
        stage = "rule_shortcut"
        signals = {}

    def fake_decide_timing(**kwargs):
        captured.update(kwargs)
        return FakeDecision()

    monkeypatch.setattr("core.timing_score.decide_timing", fake_decide_timing)

    runtime = runtime_module.GroupRuntime()
    state = runtime_module.GroupChatState()
    pending = [
        runtime_module.GroupPendingMessage(
            sender_id="u1",
            sender_name="小明",
            message="@Nanobot 这张图看看",
            is_at_bot=True,
            is_other_bot=True,
            segments=[{"type": "image"}],
            directed={"at_bot": True, "at_others": True},
            mentions=[{"user_id": "u2", "nickname": "小红", "is_bot": False}],
        )
    ]

    runtime._score_timing(
        state,
        "at_bot",
        pending=pending,
        force_direct_score=1.0,
        model_result={"action": "continue", "model_confidence": 0.9},
    )

    assert captured["is_at_bot"] is True
    assert captured["direct_call"] is True
    assert captured["is_other_bot"] is True
    assert captured["has_other_recipient"] is True
    assert captured["has_files"] is True
    assert captured["force_direct_score"] == 1.0
    assert captured["model_hint"].confidence == 0.9
```

- [x] **步骤 6：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_group_runtime_split_compat.py -v
```

预期：测试通过或至多暴露当前兼容缺口。由于这些测试锁定的是拆分前已有行为，若全部通过，
则记录为“行为基线绿灯”；后续迁移必须保持它们继续通过。

## 任务 2：拆出常量与状态模型

**文件：**
- 创建：`core/group_runtime/constants.py`
- 创建：`core/group_runtime/state.py`
- 修改：`core/group_runtime/runtime.py`

- [x] **步骤 1：创建 `constants.py`**

从 `runtime.py` 顶部迁移所有群运行时常量和 trigger 集合到
`core/group_runtime/constants.py`。

- [x] **步骤 2：创建 `state.py`**

从 `runtime.py` 迁移以下对象到 `core/group_runtime/state.py`：

- `_clip_timing_wait_delay()`
- `_model_confidence_from_gate_result()`
- `should_suppress_directed_to_other()`
- `GroupPendingMessage`
- `_format_direction_for_pending()`
- `_pending_payload()`
- `_pending_text_for_scoring()`
- `_has_file_segments()`
- `_has_directed_to_other_signal()`
- `_has_other_recipient_signal()`
- `GroupChatState`

实现要求：

- `state.py` 从 `constants.py` 导入常量。
- 继续使用 `import time as _time`，不引入全局可变 clock 封装。
- 不导入 `core.group_runtime.runtime`，避免循环依赖。

- [x] **步骤 3：收敛 `runtime.py` 顶部导入**

在 `runtime.py` 顶部从 `constants.py` 和 `state.py` 导入并重导出旧符号，删除原常量、
数据类和 helper 真实实现。

- [x] **步骤 4：运行状态拆分定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_group_runtime_split_compat.py \
  tests/test_group_runtime_ids.py \
  tests/test_timing_runtime.py::TestGateState \
  tests/test_timing_runtime.py::TestGroupPendingMessageDirected \
  tests/test_timing_runtime.py::TestShouldSuppressDirected \
  -v
```

预期：全部通过。

## 任务 3：拆出 scoring mixin

**文件：**
- 创建：`core/group_runtime/scoring.py`
- 修改：`core/group_runtime/runtime.py`

- [x] **步骤 1：创建 `scoring.py`**

新增 `GroupRuntimeScoringMixin`，从 `runtime.py` 迁移以下方法：

- `_active_linger_score()`
- `_should_cooldown()`
- `_activity_factor()`
- `_talk_threshold()`
- `_should_gate_by_frequency()`
- `_shadow_scoring()`
- `_timing_scoring_payload()`
- `_score_timing()`
- `_apply_scoring_shortcut()`
- `_policy_payload()`
- `_resolve_timing_model_policy()`
- `_apply_policy_scoring_decision()`
- `_cooldown_scoring_shortcut()`
- `_attach_shadow_scoring()`
- `_looks_like_recent_bot_followup()`

实现要求：

- `scoring.py` 从 `constants.py` 和 `state.py` 导入所需常量、类型和 helper。
- `GroupRuntimeScoringMixin` 不持有 `_states`，只访问 `self.timing_model_policy_resolver`
  和本类其他方法。
- 不导入 `GroupRuntime`，避免循环依赖。

- [x] **步骤 2：让 `GroupRuntime` 继承 mixin**

在 `runtime.py` 中导入 `GroupRuntimeScoringMixin`，将类定义改为：

```python
class GroupRuntime(GroupRuntimeScoringMixin):
    """管理所有群的运行时状态——兼容旧 timing_runtime API。"""
```

删除已经迁移到 mixin 的方法真实实现。

- [x] **步骤 3：运行 scoring 定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_group_runtime_split_compat.py \
  tests/test_timing_runtime.py \
  tests/test_timing_score.py \
  tests/test_group_runtime_ids.py \
  -v
```

预期：全部通过。

## 任务 4：运行相邻回归与行数复核

**文件：**
- 不修改文件

- [x] **步骤 1：运行 API / 群响应相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_group_response_envelope.py \
  tests/test_api.py::test_group_message_ambient_enters_timing_gate \
  tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate \
  tests/test_api.py::TestGroupMessageStructured \
  tests/test_kt_framework.py::TestNoteBotReplied \
  tests/test_kt_framework.py::TestNoteBotRepliedBridge \
  -v
```

预期：全部通过。

- [x] **步骤 2：运行语法和格式检查**

运行：

```bash
python -m compileall core/group_runtime -q
git diff --check
wc -l core/group_runtime/runtime.py core/group_runtime/constants.py core/group_runtime/state.py core/group_runtime/scoring.py tests/test_group_runtime_split_compat.py
```

预期：`compileall` 和 `git diff --check` 无输出；`runtime.py` 小于 800 行。

- [x] **步骤 3：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

## 任务 5：提交实现阶段

**文件：**
- 创建：`tests/test_group_runtime_split_compat.py`
- 创建：`core/group_runtime/constants.py`
- 创建：`core/group_runtime/state.py`
- 创建：`core/group_runtime/scoring.py`
- 修改：`core/group_runtime/runtime.py`
- 修改：`.Codex/plans/group-runtime-state-scoring-split.md`

- [x] **步骤 1：记录执行结果**

在本计划顶部追加 `执行结果摘要`，记录：

- 基线测试结果。
- 状态拆分定向测试结果。
- scoring 定向测试结果。
- 相邻回归结果。
- `compileall`、`git diff --check` 和 `wc -l` 结果。
- 全量回归结果。

- [x] **步骤 2：按文件显式暂存**

运行：

```bash
git add \
  tests/test_group_runtime_split_compat.py \
  core/group_runtime/constants.py \
  core/group_runtime/state.py \
  core/group_runtime/scoring.py \
  core/group_runtime/runtime.py \
  .Codex/plans/group-runtime-state-scoring-split.md
```

- [x] **步骤 3：检查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期：暂存区只包含本任务 6 个文件；`--check` 无输出。

- [x] **步骤 4：提交实现**

运行：

```bash
git commit -m "refactor(群运行时): 拆分状态与评分逻辑"
```

## 任务 6：同步进度文档

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/group-runtime-state-scoring-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下补充：

- `core/group_runtime/runtime.py` 当前拆分为 `constants.py`、`state.py`、`scoring.py`
  和主流程 `runtime.py`。
- `runtime.py` 从 1385 行降至实际 `wc -l` 输出值。
- 旧 `core.timing_runtime` 和 `core.group_runtime.runtime` 导入路径保留。

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加本阶段完成记录，包含：

- 设计提交号。
- 计划提交号。
- 实现提交号。
- 基线、定向、相邻和全量验证结果。
- 行数变化。
- 下一步建议：继续拆 `api/routes.py` 或处理静默吞异常补日志。

- [x] **步骤 3：验证文档**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/group-runtime-state-scoring-split.md
python - <<'PY'
from pathlib import Path
markers = [
    chr(60) + '实际行数',
    chr(60) + '失败数量',
    chr(60) + '通过数量',
    '\u5f85\u5b9a',
    'TO' + 'DO',
]
paths = [
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
    Path('.Codex/plans/group-runtime-state-scoring-split.md'),
]
hits = []
for path in paths:
    text = path.read_text(encoding='utf-8')
    hits.extend(f'{path}: {marker}' for marker in markers if marker in text)
if hits:
    raise SystemExit('\n'.join(hits))
PY
```

预期：两个命令均无输出，退出码均为 0。

- [x] **步骤 4：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/group-runtime-state-scoring-split.md
git diff --cached --name-status
git diff --cached --check
git commit -m "docs(计划): 收口群运行时拆分状态"
```
