# TimingGate 真实日志假阳率评估实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话中逐任务实现此计划。步骤使用复选框（`- [x]`）语法来跟踪进度。

**目标：** 用真实 `ChatLog` 中的 `timing_gate.scoring` 抽样评估 `s_ack`、`s_transport`、`w_marker` 假阳性风险，输出可复跑的 shadow 对比报告，记录样本量、误判类型和阈值建议。

**架构：** 在 `core/eval_sampling/` 增加纯函数模块，负责从 `ChatLog.meta_json.timing_gate.scoring.signals.sub_signals` 抽取信号样本并聚合统计；在 `evals/` 增加 CLI 脚本，连接运行 DB、调用纯函数、输出 JSON 报告。实现不直接调参，只报告候选误判与建议。

**技术栈：** Python 3.13、pytest、SQLAlchemy、现有 `core.database.ChatLog`、`core.eval_sampling.db_sampler`、`core.timing_score`。

---

## 文件结构

- 创建：`core/eval_sampling/timing_signal_audit.py`
  - 纯函数层。输入 `ChatLog` 行或 dict，输出信号样本与聚合报告。
  - 不打开数据库，不写文件，便于单元测试。
- 创建：`evals/timing_signal_audit.py`
  - CLI 层。负责打开 SQLite DB、查询 `ChatLog`、调用纯函数、写 JSON 报告。
  - 默认输出到 `evals/reports/timing_signal_audit_latest.json`。
- 创建：`tests/test_timing_signal_audit.py`
  - 覆盖样本抽取、信号过滤、报告统计、CLI 入口参数构造。
- 修改：`docs/plan_walkthrough.md`
  - 阶段 11 完成后同步状态，单独提交。

---

### 任务 1：抽取 timing scoring 信号样本

**文件：**
- 创建：`core/eval_sampling/timing_signal_audit.py`
- 测试：`tests/test_timing_signal_audit.py`

- [x] **步骤 1：编写失败的测试**

在 `tests/test_timing_signal_audit.py` 中新增：

```python
import json
from types import SimpleNamespace


def test_extract_timing_signal_samples_reads_scoring_sub_signals():
    from core.eval_sampling.timing_signal_audit import extract_timing_signal_samples

    row = SimpleNamespace(
        id=11,
        session_id="group_42",
        role="ambient",
        content="[A]: 好的",
        meta_json=json.dumps({
            "timing_gate": {
                "action": "no_reply",
                "reason": "ambient scoring shortcut",
                "trigger_reason": "ambient",
                "scoring": {
                    "stage": "rule_shortcut",
                    "action": "no_reply",
                    "model_used": False,
                    "signals": {
                        "sub_signals": {
                            "s_ack": 0.85,
                            "s_transport": 0.0,
                            "w_marker": 0.0,
                        }
                    },
                },
            }
        }, ensure_ascii=False),
    )

    samples = extract_timing_signal_samples([row], signal_names=("s_ack", "s_transport", "w_marker"))

    assert len(samples) == 1
    assert samples[0]["log_id"] == 11
    assert samples[0]["signal_name"] == "s_ack"
    assert samples[0]["signal_value"] == 0.85
    assert samples[0]["runtime_action"] == "no_reply"
    assert samples[0]["scoring_action"] == "no_reply"
    assert samples[0]["text_preview"] == "[A]: 好的"
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_signal_audit.py::test_extract_timing_signal_samples_reads_scoring_sub_signals -q -p no:cacheprovider
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'core.eval_sampling.timing_signal_audit'`。

- [x] **步骤 3：编写最少实现代码**

在 `core/eval_sampling/timing_signal_audit.py` 中实现：

```python
SIGNAL_NAMES = ("s_ack", "s_transport", "w_marker")

def safe_json(raw) -> dict:
    ...

def extract_timing_signal_samples(rows, *, signal_names=SIGNAL_NAMES, min_value=0.01) -> list[dict]:
    ...
```

实现细节：

- 只处理 `role == "ambient"` 且存在 `meta_json.timing_gate.scoring` 的行。
- 从 `scoring.signals.sub_signals` 读取信号值。
- 值小于 `min_value` 的信号不输出。
- 样本字段至少包含：
  - `log_id`
  - `session_id`
  - `signal_name`
  - `signal_value`
  - `runtime_action`
  - `trigger_reason`
  - `scoring_stage`
  - `scoring_action`
  - `model_used`
  - `model_action`
  - `action_mismatch`
  - `reason`
  - `text_preview`

- [x] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 2：聚合假阳率人工标注统计

**文件：**
- 修改：`core/eval_sampling/timing_signal_audit.py`
- 测试：`tests/test_timing_signal_audit.py`

- [x] **步骤 1：编写失败的测试**

新增：

```python
def test_build_timing_signal_audit_report_counts_labels_and_suggestions():
    from core.eval_sampling.timing_signal_audit import build_timing_signal_audit_report

    samples = [
        {"signal_name": "s_ack", "label": "false_positive", "runtime_action": "no_reply", "scoring_action": "no_reply"},
        {"signal_name": "s_ack", "label": "true_positive", "runtime_action": "continue", "scoring_action": "no_reply"},
        {"signal_name": "s_transport", "label": "unknown", "runtime_action": "wait", "scoring_action": "wait"},
        {"signal_name": "w_marker", "label": "false_positive", "runtime_action": "wait", "scoring_action": "continue"},
    ]

    report = build_timing_signal_audit_report(samples)

    assert report["total_samples"] == 4
    assert report["signals"]["s_ack"]["samples"] == 2
    assert report["signals"]["s_ack"]["false_positive_count"] == 1
    assert report["signals"]["s_ack"]["false_positive_rate"] == 0.5
    assert report["signals"]["w_marker"]["suggestion"] == "review_threshold"
    assert report["shadow"]["action_mismatch_count"] == 2
    assert report["shadow"]["action_mismatch_rate"] == 0.5
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_signal_audit.py::test_build_timing_signal_audit_report_counts_labels_and_suggestions -q -p no:cacheprovider
```

预期：FAIL，报错 `cannot import name 'build_timing_signal_audit_report'` 或断言失败。

- [x] **步骤 3：编写最少实现代码**

新增：

```python
FALSE_POSITIVE_LABELS = {"false_positive", "fp", "误判", "假阳性"}
TRUE_POSITIVE_LABELS = {"true_positive", "tp", "正确"}

def normalize_label(value: str) -> str:
    ...

def build_timing_signal_audit_report(samples: list[dict]) -> dict:
    ...
```

报告字段：

- `total_samples`
- `labeled_samples`
- `signals.<signal>.samples`
- `signals.<signal>.labeled_samples`
- `signals.<signal>.false_positive_count`
- `signals.<signal>.true_positive_count`
- `signals.<signal>.unknown_count`
- `signals.<signal>.false_positive_rate`
- `signals.<signal>.actions`
- `signals.<signal>.suggestion`
- `shadow.total_samples`
- `shadow.action_mismatch_count`
- `shadow.action_mismatch_rate`
- `shadow.mismatches_by_signal`

建议规则：

- 有标注且 `false_positive_rate >= 0.2` → `review_threshold`
- 无标注 → `needs_label`
- 其他 → `keep_threshold`

shadow 对比规则：

- `runtime_action` 来自 `timing_gate.action`
- `scoring_action` 来自 `timing_gate.scoring.action`
- 两者不同则 `action_mismatch=True`
- 报告只记录差异，不自动判定谁正确，也不调参

- [x] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 3：CLI 从运行 DB 输出 shadow 对比报告

**文件：**
- 创建：`evals/timing_signal_audit.py`
- 测试：`tests/test_timing_signal_audit.py`

- [x] **步骤 1：编写失败的测试**

新增：

```python
def test_timing_signal_audit_cli_writes_report(tmp_path, db_session):
    import json
    from core.database import ChatLog
    from evals.timing_signal_audit import run_audit

    db_session.add(ChatLog(
        user_id="group_42",
        session_id="group_42",
        role="ambient",
        content="[A]: 好的",
        meta_json=json.dumps({
            "timing_gate": {
                "action": "wait",
                "trigger_reason": "ambient",
                "scoring": {
                    "stage": "rule_shortcut",
                    "action": "no_reply",
                    "model_used": False,
                    "signals": {"sub_signals": {"s_ack": 0.85}},
                },
            }
        }, ensure_ascii=False),
    ))
    db_session.commit()
    out = tmp_path / "audit.json"

    report = run_audit(db_session, output_path=out, limit=20)

    assert report["total_samples"] == 1
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["signals"]["s_ack"]["samples"] == 1
    assert payload["shadow"]["action_mismatch_count"] == 1
```

- [x] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_signal_audit.py::test_timing_signal_audit_cli_writes_report -q -p no:cacheprovider
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'evals.timing_signal_audit'`。

- [x] **步骤 3：编写最少实现代码**

在 `evals/timing_signal_audit.py` 中实现：

```python
def query_timing_rows(db, *, after_id=0, limit=200):
    from core.database import ChatLog
    return (
        db.query(ChatLog)
        .filter(ChatLog.role == "ambient", ChatLog.id > after_id)
        .order_by(ChatLog.id.asc())
        .limit(limit * 5)
        .all()
    )

def run_audit(db, *, output_path, after_id=0, limit=200, signal_names=SIGNAL_NAMES):
    ...
```

CLI 参数：

- `--db`，默认 `data/nanobot.db`
- `--out`，默认 `evals/reports/timing_signal_audit_latest.json`
- `--after-id`，默认 `0`
- `--limit`，默认 `200`
- `--signals`，默认 `s_ack,s_transport,w_marker`

输出 JSON 包含：

- 聚合报告字段
- `samples` 列表，便于人工标注
- `generated_at`
- `source`，包含 `db`、`after_id`、`limit`

- [x] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 4：验证与提交

**文件：**
- 创建：`core/eval_sampling/timing_signal_audit.py`
- 创建：`evals/timing_signal_audit.py`
- 创建：`tests/test_timing_signal_audit.py`
- 创建：`.Codex/plans/timing-gate-scoring-phase11-log-audit.md`

- [x] **步骤 1：运行阶段 11 定向测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

- [x] **步骤 2：运行 TimingGate 回归**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

- [x] **步骤 3：运行全量测试**

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

- [x] **步骤 4：检查 diff 并提交实现**

```bash
git diff --check -- core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py tests/test_timing_signal_audit.py .Codex/plans/timing-gate-scoring-phase11-log-audit.md
git add core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py tests/test_timing_signal_audit.py
blob=$(git hash-object -w .Codex/plans/timing-gate-scoring-phase11-log-audit.md)
git update-index --add --cacheinfo 100644,$blob,.Codex/plans/timing-gate-scoring-phase11-log-audit.md
git diff --cached --check -- core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py tests/test_timing_signal_audit.py .Codex/plans/timing-gate-scoring-phase11-log-audit.md
git commit -m "feat(评测): 添加时机信号日志审计"
```

- [x] **步骤 5：同步阶段文档并单独提交**

修改 `docs/plan_walkthrough.md`：

- 阶段 11 状态改为「已完成」
- 记录实现提交 hash
- 下一步改为阶段 12

运行并提交：

```bash
git diff --check -- docs/plan_walkthrough.md
git add docs/plan_walkthrough.md
git commit -m "docs(计划): 同步日志审计进度"
```
