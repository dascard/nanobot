# TimingGate 指向他人冲突语义补漏实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修正 TimingGate 模型 prompt 中 `directed_to_other` 被描述成硬 no_reply 的偏差，让纯指向他人保持 no_reply，而同时指向 bot、回复 bot 或处于余韵冲突时交给模型结合上下文裁量。

**架构：** 保持 `core.timing_score.decide_timing()` 和 runtime scoring 公式不变，仅同步模型输入语义、默认 Prompt Runtime 模板、prompt policy 测试和 timing_gate eval case。新增 paired cases 用来防止“只 @ 其他人”和“@bot + @其他人 / 余韵冲突”被混为同一类硬规则。

**技术栈：** Python、pytest、JSON eval case、Prompt Runtime markdown 模板、现有 `evals.run` / `scripts/run_timing_gate_gate.sh`。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md`
- 差距来源：只读 explorer 核对发现 `clients.classifier_client.TIMING_GATE_PROMPT` 与 `prompts.v2.default/tasks/timing_gate.md` 仍把 `[指向性] @其他人` / `回复其他人` 写成无条件 no_reply，而设计与当前 scoring 实现已经支持 `directed_to_other + at_bot/reply_to_bot/linger` 冲突升级到模型。
- 边界：本阶段不改 scoring 公式、不改 runtime gating、不自动调参、不更新生产配置、不处理 H29 / H30 大重构。

## 文件结构

- 修改：`tests/test_timing_gate_prompt_policy.py`
  - 增加 prompt 语义断言，要求内嵌 prompt 与默认模板同时区分“仅指向他人”和“同时指向 bot / 余韵冲突”。
  - 增加 eval suite paired case 断言，确保有纯 directed no_reply、`@bot + @其他人` 冲突、余韵冲突三类覆盖。
- 修改：`clients/classifier_client.py`
  - 更新 `TIMING_GATE_PROMPT` 第 6 条规则，表达纯 directed no_reply 与冲突裁量。
- 修改：`prompts.v2.default/tasks/timing_gate.md`
  - 与内嵌 prompt 保持同义，避免 Prompt Runtime 默认模板过时。
- 创建：`evals/cases/timing_gate/timing_gate_scoring_at_bot_with_other_mention_model.json`
  - 无 action 回放，用 `model_hint` 表示模型在 `@bot + @其他人` 冲突场景可裁量为 continue，验证 `stage=model_assisted_conflict`。
- 创建：`evals/cases/timing_gate/timing_gate_scoring_directed_other_linger_model.json`
  - 无 action 回放，用 `linger_score` 和 `model_hint` 表示余韵冲突可裁量为 continue，验证 `stage=model_assisted_conflict`。
- 修改：`evals/baselines/timing_gate.json`
  - 同步正式 suite case 数量，保持 gate baseline 与当前 suite 输出一致。
- 修改：`docs/todo.md`、`docs/plan_walkthrough.md`
  - 收口本阶段状态，记录提交号与验证结果。
- 修改：`.Codex/plans/timing-gate-directed-prompt-softening.md`
  - 勾选已完成步骤并记录验证摘要。

## 任务 1：写红灯测试和 paired eval case

**文件：**

- 修改：`tests/test_timing_gate_prompt_policy.py`
- 创建：`evals/cases/timing_gate/timing_gate_scoring_at_bot_with_other_mention_model.json`
- 创建：`evals/cases/timing_gate/timing_gate_scoring_directed_other_linger_model.json`

- [x] **步骤 1：增加 prompt 语义断言**

在 `tests/test_timing_gate_prompt_policy.py` 中增加 helper，检查内嵌 prompt 和默认模板都包含以下语义：

```python
def _assert_directed_to_other_softened_semantics(prompt: str) -> None:
    assert "仅指向其他人" in prompt
    assert "默认 no_reply" in prompt
    assert "同时指向 bot" in prompt
    assert "回复 bot" in prompt
    assert "余韵" in prompt
    assert "结合上下文" in prompt
```

- [x] **步骤 2：增加 paired eval case**

新增 `timing_gate_scoring_at_bot_with_other_mention_model.json`：

```json
{
  "id": "timing_gate_scoring_at_bot_with_other_mention_model",
  "suite": "timing_gate",
  "description": "@bot 同时 @其他人时不是纯 directed_to_other，冲突交给模型裁量",
  "input": {
    "text": "@nanobot @小明 这个报错你俩谁看一下？",
    "is_group": true,
    "is_at_bot": true,
    "is_directed_to_other": true,
    "trigger_reason": "at_bot",
    "model_hint": {
      "action": "continue",
      "confidence": 0.8,
      "reason": "用户同时点名 bot 和群友，需要结合上下文处理"
    }
  },
  "expected": {
    "timing_action": "continue",
    "should_reply": true,
    "scoring": {
      "stage": "model_assisted_conflict",
      "model_used": true,
      "action": "continue",
      "signals": {
        "explicit_direct_score": 0.95,
        "sub_signals": {
          "s_other": 0.75
        }
      }
    }
  },
  "tags": ["directed_to_other", "at_bot", "scoring", "model_conflict", "continue"]
}
```

新增 `timing_gate_scoring_directed_other_linger_model.json`：

```json
{
  "id": "timing_gate_scoring_directed_other_linger_model",
  "suite": "timing_gate",
  "description": "余韵期间出现指向他人的后续消息时，冲突交给模型裁量",
  "input": {
    "text": "@小明 刚才那个参数你怎么看？",
    "is_group": true,
    "is_directed_to_other": true,
    "trigger_reason": "ambient",
    "linger_score": 0.7,
    "model_hint": {
      "action": "continue",
      "confidence": 0.8,
      "reason": "仍处于 bot 对话余韵，需要结合上下文判断"
    }
  },
  "expected": {
    "timing_action": "continue",
    "should_reply": true,
    "scoring": {
      "stage": "model_assisted_conflict",
      "model_used": true,
      "action": "continue",
      "signals": {
        "direct_score": 0.7,
        "sub_signals": {
          "s_other": 0.75
        }
      }
    }
  },
  "tags": ["directed_to_other", "linger", "scoring", "model_conflict", "continue"]
}
```

- [x] **步骤 3：运行红灯**

运行：

```bash
python -B -m pytest tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：prompt 语义断言失败，因为当前 prompt 缺少“仅指向其他人 / 同时指向 bot / 余韵冲突”等新语义。

## 任务 2：同步 prompt 与 baseline

**文件：**

- 修改：`clients/classifier_client.py`
- 修改：`prompts.v2.default/tasks/timing_gate.md`
- 修改：`evals/baselines/timing_gate.json`

- [x] **步骤 1：更新内嵌 prompt 和默认模板**

把第 6 条改成同义语义：

```text
6. 仅指向其他人时（例如 `[指向性] @其他人`、`[指向性] 回复其他人`，且没有同时 @bot、回复 bot、叫 bot 名字、处于 bot 对话余韵），默认 no_reply；但如果这条消息同时指向 bot、回复 bot，或仍处于 bot 对话余韵冲突中，不要按硬规则拒绝，结合上下文判断是否 continue / wait / no_reply。
```

- [x] **步骤 2：更新 baseline case 数**

运行：

```bash
python -B -m evals.run --suite timing_gate --baseline evals/baselines/timing_gate.json --min-pass-rate 1.0 --max-new-failures 0
```

先确认新增 case 全部通过且 baseline diff 只显示新增 case。随后把 `evals/baselines/timing_gate.json` 的 `total` / `passed` 更新为当前 suite 输出。

- [x] **步骤 3：运行绿灯和 gate**

运行：

```bash
python -B -m pytest tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
bash scripts/run_timing_gate_gate.sh
```

预期：测试通过，gate 输出 `Gate passed`。

## 任务 3：文档收口、验证与提交

**文件：**

- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/timing-gate-directed-prompt-softening.md`

- [x] **步骤 1：同步路线状态**

在 `docs/todo.md` 路线项 10 的已完成内容中补充 directed prompt 语义补漏；在 `docs/plan_walkthrough.md` 顶部当前状态和进度总览中记录本阶段提交与验证。

- [x] **步骤 2：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py tests/test_private_timing.py -q -p no:cacheprovider
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
bash scripts/run_timing_gate_gate.sh
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -m pytest tests/ -v
```

预期：全部退出码为 0。

- [x] **步骤 3：提交**

显式暂存本阶段文件：

```bash
git add \
  clients/classifier_client.py \
  prompts.v2.default/tasks/timing_gate.md \
  tests/test_timing_gate_prompt_policy.py \
  evals/cases/timing_gate/timing_gate_scoring_at_bot_with_other_mention_model.json \
  evals/cases/timing_gate/timing_gate_scoring_directed_other_linger_model.json \
  evals/baselines/timing_gate.json \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/timing-gate-directed-prompt-softening.md
git commit -m "fix(时机): 软化指向他人的提示词规则"
```

## 完成前核对清单

- [x] 纯 `directed_to_other` no_reply 语义仍被 prompt 和 eval case 保留。
- [x] `@bot + @其他人` 与 `directed_to_other + linger` 不再被 prompt 描述为硬 no_reply。
- [x] 内嵌 prompt 和 Prompt Runtime 默认模板同义。
- [x] 新增 paired cases 走 `model_assisted_conflict`，不是 `rule_shortcut`。
- [x] `scripts/run_timing_gate_gate.sh` 通过。
- [x] 不修改 scoring 公式、不更新生产参数、不改变 PR gate 或周期 gate。

## 验证记录

- 红灯：`python -B -m pytest tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider` 输出 `2 failed, 7 passed`，失败点为 prompt 缺少“仅指向其他人”等新语义。
- 绿灯：同一命令输出 `9 passed, 1 warning`。
- Eval gate：`python -B -m evals.run --suite timing_gate --baseline evals/baselines/timing_gate.json --min-pass-rate 1.0 --max-new-failures 0` 输出 `total=20 passed=20 failed=0` 和 `Gate passed`。
- TimingGate gate：`bash scripts/run_timing_gate_gate.sh` 输出 `total=20 passed=20 failed=0` 和 `Gate passed`。
- Timing 相邻回归：`tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py tests/test_private_timing.py` 输出 `94 passed, 1 warning`。
- Eval baseline 组合：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py` 输出 `34 passed, 1 warning`。
- 最终全量：`python -m pytest tests/ -v` 输出 `1447 passed, 6 skipped, 139 warnings in 108.22s`。
