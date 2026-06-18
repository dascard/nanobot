# TimingGate 持续评估设计

> 2026-06-18 · P3-3 运营收尾设计。把「真实日志信号审计」和「`timing_gate` 回归门禁」从已有 CLI 能力收敛为可复跑、可接入 CI 的闭环。

---

## 一、背景

TimingGate 混合决策主线已经完成：群聊和私聊共享 scoring 公式，群聊 / 私聊 meta 均可回溯，真实日志信号审计 CLI 和 `evals.run` baseline / gate 能力也已存在。当前剩余问题不在核心决策算法，而在持续评估闭环：

- `evals/timing_signal_audit.py` 只能从数据库重新抽样并输出未标注报告，不能直接复跑人工标注后的样本。
- `timing_gate` eval 已有 baseline diff 和阈值门禁，但仓库没有稳定 baseline 文件、统一脚本或 CI workflow。
- 正式 `evals/cases/timing_gate/*.json` 全部带 `input.action`，多数 case 只验证 action 回放，不足以守住 `decide_timing()` scoring 路径。
- 通用 `candidates → labeled → promote` 雏形存在，但目标更大，属于 P4 评测体系扩展，不塞进 P3-3。

## 二、目标

P3-3 拆成两个可以独立提交和回滚的阶段。

### P3-3A：标注审计复跑入口

让 `timing_signal_audit` 支持复跑人工标注样本，不依赖重新连接真实数据库：

- 支持读取已有 audit report 的 `samples[]`。
- 支持读取 sidecar labels 文件，把 `log_id + signal_name` 匹配回样本。
- 复用现有 `build_timing_signal_audit_report()` 聚合逻辑，输出 `false_positive_rate`、`review_threshold` / `keep_threshold` / `needs_label`、shadow mismatch。
- 保留真实日志文本作为本地受控数据，不新增未脱敏真实聊天样本到仓库。

### P3-3B：TimingGate 回归门禁接入

让 `timing_gate` eval 成为仓库自包含、CI 可运行的 PR gate：

- 新增稳定 baseline：`evals/baselines/timing_gate.json`。
- 新增统一脚本：`scripts/run_timing_gate_gate.sh`。
- 新增 GitHub Actions workflow，先跑轻量 `timing_gate` gate。
- 补充正式 suite 中不带 `input.action` 的 scoring case，直接覆盖 `decide_timing()`。
- 补充 gate 成功路径和异常配置测试。
- 文档说明 baseline 更新规则、失败处理和命令入口。

## 三、非目标

- 不在 P3-3 做完整 candidates 产品化闭环。
- 不新增 Admin UI 标注页面。
- 不把真实聊天明文样本提交到仓库。
- 不在没有标注报告证据时调整 `core/timing_score.py` 阈值或权重。
- 不把 WebUI lint/build 混入 TimingGate 门禁；前端 CI 可后续单独设计。

## 四、方案选择

### 方案 A：只接 CI

只新增 baseline、脚本和 workflow。优点是快；缺点是无法解决路线清单中「更多人工标注样本复跑审计」的闭环，P3-3 仍会留下核心运营缺口。

### 方案 B：先补标注复跑，再接 CI

先让审计报告可复跑，再接入 PR gate。优点是同时覆盖路线清单的两个剩余点，且每一步都可独立测试和提交。缺点是文件数更多，需要更严格的 TDD 拆分。

### 方案 C：直接做完整 candidates/labeled 系统

把 sampling、标注、promote、suite 刷新和 baseline 更新全打通。优点是长期最完整；缺点是范围明显属于 P4，会扩大接口和数据治理风险。

推荐方案 B。P3-3A 解决真实标注复跑，P3-3B 解决 CI 门禁；P4 再扩展通用 candidates 闭环。

## 五、数据契约

### 审计报告输入

`--input-report` 读取已有 JSON 报告，至少包含：

```json
{
  "samples": [
    {
      "log_id": 101,
      "signal_name": "s_ack",
      "signal_value": 0.85,
      "runtime_action": "no_reply",
      "text_preview": "好的"
    }
  ]
}
```

### Sidecar labels

`--labels` 支持 JSON 或 JSONL。每条 label 至少包含：

```json
{"log_id": 101, "signal_name": "s_ack", "label": "false_positive", "note": "后半句有请求"}
```

合并规则：

- key 为 `(log_id, signal_name)`。
- sidecar label 覆盖样本内已有 `label`。
- `note`、`annotator` 等额外字段保留到样本上，但聚合逻辑只读取 `label`。
- 不认识的 label 统一按现有 `normalize_label()` 归为 `unknown`。

### CI baseline

`evals/baselines/timing_gate.json` 使用 `SuiteReport` 结构：

```json
{
  "suite": "timing_gate",
  "total": 18,
  "passed": 18,
  "failed": 0,
  "pass_rate": 1.0,
  "failed_cases": []
}
```

当新增正式 case 后，baseline total 必须随当前绿色 suite 更新。

## 六、实现边界

### P3-3A 文件边界

- `core/eval_sampling/timing_signal_audit.py`：新增纯函数 helper，例如 `merge_timing_signal_labels()`。
- `evals/timing_signal_audit.py`：新增 `--input-report`、`--labels` 或等价参数，支持不连 DB 的复跑模式。
- `tests/test_timing_signal_audit.py`：先写失败测试，覆盖 report 复跑、sidecar 合并和建议状态。
- 文档：更新 `docs/todo.md`、`docs/plan_walkthrough.md`、本设计文档和实现计划状态。

### P3-3B 文件边界

- `evals/run.py`：可新增 `--no-write-report` 或 `--report-dir`，减少 CI 副作用。
- `evals/baselines/timing_gate.json`：新增稳定 baseline。
- `evals/cases/timing_gate/timing_gate_scoring_*.json`：新增 2 到 3 个无 `input.action` 的 scoring case。
- `scripts/run_timing_gate_gate.sh`：统一本地和 CI 命令。
- `.github/workflows/timing-gate-eval.yml`：新增 PR gate。
- `tests/test_eval_baseline.py`、`tests/test_timing_gate_prompt_policy.py`：补 gate 和 suite 守卫测试。
- `docs/evals.md` 或 README：记录命令、baseline 更新规则和失败处理。

## 七、验收标准

- [x] `timing_signal_audit` 可在无 DB 的情况下复跑已有 labeled report。
- [x] sidecar JSON / JSONL label 可按 `log_id + signal_name` 合并。
- [x] 标注复跑报告能输出 labeled 样本数、假阳率和建议状态。
- [x] `timing_gate` gate 有仓库内 baseline，不依赖 `evals/reports/latest.json`。
- [x] `scripts/run_timing_gate_gate.sh` 本地可直接运行并返回正确 exit code。
- [x] CI workflow 调用统一脚本，并显式设置测试环境变量，避免 `.env` 写入副作用。
- [x] 正式 `timing_gate` suite 至少包含 2 个无 `input.action` 的 scoring case。
- [x] 定向测试、eval gate 和全量测试通过后再提交实现阶段。

## 八、子 agent 分工建议

两个实现阶段可并行做只读审计，但代码写入应分阶段执行，避免共享文件冲突。

- P3-3A worker：负责 `core/eval_sampling/timing_signal_audit.py`、`evals/timing_signal_audit.py`、`tests/test_timing_signal_audit.py`。
- P3-3B worker：负责 `evals/run.py`、`evals/baselines/`、`evals/cases/timing_gate/`、`tests/test_eval_baseline.py`、`tests/test_timing_gate_prompt_policy.py`、`scripts/run_timing_gate_gate.sh`、`.github/workflows/`。
- 主线程：负责审查 diff、运行验证、同步 `docs/todo.md` / `docs/plan_walkthrough.md` / 本设计文档，并按阶段提交。

## 九、风险与处理

- **真实样本隐私风险**：仓库只提交脱敏 case 和通用 baseline，不提交原始 ChatLog 文本。
- **CI 依赖漂移**：首个 workflow 只跑轻量 gate；完整依赖锁定另开阶段处理。
- **报告写入副作用**：优先用 `--no-write-report` 或临时 `--report-dir` 让 CI 不污染工作区。
- **baseline 误更新**：文档要求只有所有正式 case 通过、且行为变化已审查后才能刷新 baseline。
- **P4 范围膨胀**：通用 `EvalCandidate` promote 目录、Admin 标注、候选晋升策略留到 P4。

---

_设计依据：2026-06-18 三个子 agent 只读审计结果、`docs/todo.md` 路线项 10 / 路线项 8、`docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md`。_
