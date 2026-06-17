# 私聊接入 TimingGate 评分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话中逐步骤执行。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让私聊 `PrivateTimingGate` 使用 `core.timing_score.decide_timing()` 作为统一决策公式，分类器只在模糊或冲突场景中提供模型提示。

**架构：** 在 `core/private_timing.py` 中新增私聊 scoring helper，把 `TimingDecision` 附到 `PrivateDecision`。规则明显的私聊输入直接由 shared scoring 短路；需要模型时先调用现有 `PrivateDecisionClassifier`，再把其 action 作为 `TimingModelHint` 回灌到同一公式，最终 action 仍由 scoring 决定。

**技术栈：** Python 3.13、pytest、dataclasses、现有 `core.timing_score` 纯函数。

---

### 任务 1：私聊 scoring helper 与 rule shortcut

**文件：**
- 修改：`core/private_timing.py`
- 测试：`tests/test_private_timing.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_private_timing.py` 中新增：

```python
@pytest.mark.asyncio
async def test_private_task_request_uses_shared_scoring_without_classifier():
    class ExplodingClassifier:
        def classify(self, *_args, **_kwargs):
            raise AssertionError("classifier should not be called")

    gate = PrivateTimingGate(classifier=ExplodingClassifier())

    decision = await gate.classify("帮我总结一下", user_id="u-private")

    assert decision.action == "reply_now"
    assert decision.raw_label == "scoring_rule_shortcut"
    assert decision.timing_scoring["stage"] == "rule_shortcut"
    assert decision.timing_scoring["signals"]["sub_signals"]["is_private"] is True
    assert decision.effort == "short"
    assert decision.runtime_preset == "lightweight"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_private_timing.py::test_private_task_request_uses_shared_scoring_without_classifier -q -p no:cacheprovider
```

预期：FAIL，报错显示 `PrivateDecision` 没有 `timing_scoring` 或分类器被调用。

- [ ] **步骤 3：编写最少实现代码**

在 `PrivateDecision` 增加 `timing_scoring: dict | None = None`。新增 `_score_private_timing()`，内部调用：

```python
decide_timing(
    text,
    is_group=False,
    is_private=True,
    has_files=has_files,
    model_hint=model_hint,
)
```

在 `PrivateTimingGate.classify()` 中，完成 empty hard stop 后先计算 scoring；若 `scoring.stage == "rule_shortcut"`，再调用 `_infer_effort()` 得到 `effort/runtime_preset`，将 `continue` 映射为 `reply_now` 返回，并附上 `asdict(scoring)`。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令，预期 PASS。

---

### 任务 2：分类器结果作为模型提示回灌 scoring

**文件：**
- 修改：`core/private_timing.py`
- 测试：`tests/test_private_timing.py`

- [ ] **步骤 1：编写失败的测试**

新增：

```python
@pytest.mark.asyncio
async def test_private_conflict_uses_classifier_as_scoring_model_hint():
    class ReplyClassifier:
        calls = 0

        def classify(self, text, has_files):
            self.calls += 1
            return {
                "action": "reply_now",
                "complexity": 4,
                "reason": "用户要求查看链接",
                "raw": "{\"action\":\"reply_now\"}",
            }

    classifier = ReplyClassifier()
    gate = PrivateTimingGate(classifier=classifier)

    decision = await gate.classify("帮我看看 https://example.com", user_id="u-private")

    assert classifier.calls == 1
    assert decision.action == "reply_now"
    assert decision.complexity == 4
    assert decision.raw_label == "{\"action\":\"reply_now\"}"
    assert decision.timing_scoring["stage"] == "model_assisted_conflict"
    assert decision.timing_scoring["model_used"] is True
    assert decision.timing_scoring["model_action"] == "reply_now"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_private_timing.py::test_private_conflict_uses_classifier_as_scoring_model_hint -q -p no:cacheprovider
```

预期：FAIL，当前代码直接使用分类器 action，未把结果回灌 scoring，也没有 `timing_scoring`。

- [ ] **步骤 3：编写最少实现代码**

`PrivateTimingGate.classify()` 调用分类器后，用分类器结果构造 `TimingModelHint`。正常 JSON 结果置信度设为 `0.8`；fallback / invalid / legacy reason 设为 `0.5`。再次调用 `_score_private_timing(..., model_result=result)`，最终 action 使用 scoring action 映射结果。

- [ ] **步骤 4：运行测试验证通过**

运行同一步骤 2 命令，预期 PASS。

---

### 任务 3：验证与提交

**文件：**
- 修改：`core/private_timing.py`
- 修改：`tests/test_private_timing.py`
- 创建：`.Codex/plans/timing-gate-scoring-phase8-private.md`

- [ ] **步骤 1：运行私聊定向测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_private_timing.py -q -p no:cacheprovider
```

- [ ] **步骤 2：运行 timing 回归**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate.py tests/test_timing_gate_prompt_policy.py tests/test_private_timing.py -q -p no:cacheprovider
```

- [ ] **步骤 3：运行全量测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

- [ ] **步骤 4：检查 diff**

```bash
git diff --check -- core/private_timing.py tests/test_private_timing.py .Codex/plans/timing-gate-scoring-phase8-private.md
```

- [ ] **步骤 5：按文件暂存并提交**

`.Codex` 大小写路径在当前仓库需要用 `git update-index --cacheinfo` 暂存计划文件：

```bash
git add core/private_timing.py tests/test_private_timing.py
blob=$(git hash-object -w .Codex/plans/timing-gate-scoring-phase8-private.md)
git update-index --add --cacheinfo 100644,$blob,.Codex/plans/timing-gate-scoring-phase8-private.md
git diff --cached --check -- core/private_timing.py tests/test_private_timing.py .Codex/plans/timing-gate-scoring-phase8-private.md
git commit -m "refactor(时机门控): 私聊接入共享评分"
```
