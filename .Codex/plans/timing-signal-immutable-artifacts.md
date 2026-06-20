# TimingSignal 不可变 Artifact 加厚实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在周期评测中为 TimingSignal audit 同轮写出 latest、dated 和 run-scoped 三类报告，并让 manifest 与 workflow artifact 能优先追溯不可变报告。

**架构：** `scripts/run_timing_signal_audit_periodic.sh` 继续作为审计入口，新增 `TIMING_SIGNAL_AUDIT_EXTRA_OUTS` 接收额外输出路径，并把主输出 payload 原样复制到额外路径。`scripts/run_eval_periodic.sh` 负责派生本轮 dated / run-scoped 路径，调用审计脚本后把三类报告按 run-scoped、dated、latest 顺序写入 step `report_paths`。`evals.periodic_manifest` schema 保持不变，workflow 只追加 run-scoped TimingSignal audit artifact glob。

**技术栈：** Bash、Python 标准库 `json` / `shutil`、pytest、GitHub Actions artifact、现有 `evals.timing_signal_audit` CLI。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-timing-signal-immutable-artifacts-design.md`
- 前置状态：真实样本运营 1-9、周期运行 manifest、跨 artifact 趋势和只读调参分析已完成。
- 产物边界：本计划只加厚 TimingSignal audit artifact，不自动调参、不生成调参 proposal、不更新 baseline、不改变 PR gate 或周期 gate。

## 文件结构

- 修改：`scripts/run_timing_signal_audit_periodic.sh`
  - 职责：新增 `TIMING_SIGNAL_AUDIT_EXTRA_OUTS`，缺 DB skipped 分支和正常审计分支都复制同一 payload 到额外路径。
- 修改：`tests/test_timing_signal_audit_periodic.py`
  - 职责：覆盖缺 DB 时 latest、dated、run-scoped 三个输出都存在且 payload 完全一致。
- 修改：`scripts/run_eval_periodic.sh`
  - 职责：派生 `TIMING_SIGNAL_AUDIT_LATEST_OUT`、`TIMING_SIGNAL_AUDIT_DATED_OUT`、`TIMING_SIGNAL_AUDIT_RUN_OUT`，并用三类路径记录 TimingSignal step。
- 修改：`tests/test_eval_baseline.py`
  - 职责：静态守卫周期脚本的三类 TimingSignal audit 路径、`report_paths` 顺序和 workflow artifact glob。
- 修改：`.github/workflows/timing-gate-eval.yml`
  - 职责：上传 `evals/reports/runs/**/timing_signal_audit.json`。
- 修改：`.gitignore`
  - 职责：忽略本地周期 smoke 生成的 run-scoped eval 报告目录。
- 修改：`docs/evals.md`
  - 职责：记录 TimingSignal audit 三类输出、缺 DB skipped 语义和排查入口。
- 修改：`docs/todo.md`
  - 职责：同步真实样本运营下一步状态。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段设计、计划、实现提交和验证证据。
- 修改：`.Codex/plans/timing-signal-immutable-artifacts.md`
  - 职责：执行过程中勾选步骤并补充实际验证记录。

## 接口约定

- `TIMING_SIGNAL_AUDIT_OUT`：主输出路径，默认仍为 `evals/reports/timing_signal_audit_latest.json`。
- `TIMING_SIGNAL_AUDIT_EXTRA_OUTS`：可选额外输出路径，使用 `:` 分隔；空段忽略；路径中不支持冒号。
- 审计脚本必须先生成主输出，再复制主输出到额外输出，保证同一轮 latest、dated、run-scoped payload 完全一致。
- 缺 DB 时三个输出都写 `source.mode == "skipped"`、`source.reason == "db_not_found"`，脚本退出码为 0。
- 周期脚本 TimingSignal step 的 `report_paths` 顺序固定为：
  1. `evals/reports/runs/${PERIODIC_RUN_ID}/timing_signal_audit.json`
  2. `evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json`
  3. `evals/reports/timing_signal_audit_latest.json`
- `evals.periodic_manifest` 不新增字段；它继续读取 `report_paths` 中第一个存在的 JSON 生成摘要。

## 子 agent 分配与边界

- 推荐用一个实现子 agent 顺序执行任务 1 和任务 2，因为二者共享 `TIMING_SIGNAL_AUDIT_EXTRA_OUTS` 接口，必须先让审计脚本契约稳定。
- 任务 3 可交给独立子 agent 执行，但必须只修改 `.github/workflows/timing-gate-eval.yml` 和对应测试断言。
- 任务 4 可交给文档子 agent 草拟变更，但主线程必须审查措辞、验证命令和提交号后再合入。
- 不并行分派多个写入型实现 agent 修改 `tests/test_eval_baseline.py`，避免同一测试文件冲突。
- 可并行分派只读审查 agent：一个审查审计脚本契约，一个审查周期 manifest / workflow 归档契约，一个审查文档口径；它们只输出发现，不写文件。

## 任务 1：审计脚本复制额外输出

**文件：**

- 修改：`tests/test_timing_signal_audit_periodic.py`
- 修改：`scripts/run_timing_signal_audit_periodic.sh`

- [x] **步骤 1：编写失败的缺 DB 三输出测试**

在 `tests/test_timing_signal_audit_periodic.py` 中把 `test_timing_signal_audit_periodic_script_skips_missing_db` 扩展为：

```python
def test_timing_signal_audit_periodic_script_skips_missing_db(tmp_path):
    latest = tmp_path / "reports" / "timing_signal_audit_latest.json"
    dated = tmp_path / "reports" / "2026-06-20-timing_signal_audit.json"
    run_scoped = tmp_path / "reports" / "runs" / "unit_run" / "timing_signal_audit.json"
    missing_db = tmp_path / "missing.db"
    env = {
        **os.environ,
        "TIMING_SIGNAL_AUDIT_DB": str(missing_db),
        "TIMING_SIGNAL_AUDIT_OUT": str(latest),
        "TIMING_SIGNAL_AUDIT_EXTRA_OUTS": f"{run_scoped}:{dated}",
        "TIMING_SIGNAL_AUDIT_LIMIT": "17",
        "TIMING_SIGNAL_AUDIT_AFTER_ID": "5",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        ["bash", "scripts/run_timing_signal_audit_periodic.sh"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert latest.exists()
    assert dated.exists()
    assert run_scoped.exists()

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert json.loads(dated.read_text(encoding="utf-8")) == payload
    assert json.loads(run_scoped.read_text(encoding="utf-8")) == payload
    assert payload["total_samples"] == 0
    assert payload["samples"] == []
    assert payload["source"]["mode"] == "skipped"
    assert payload["source"]["reason"] == "db_not_found"
    assert payload["source"]["db"] == str(missing_db)
    assert payload["source"]["after_id"] == 5
    assert payload["source"]["limit"] == 17
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py::test_timing_signal_audit_periodic_script_skips_missing_db -q -p no:cacheprovider
```

预期：失败，`dated.exists()` 或 `run_scoped.exists()` 为 `False`。

- [x] **步骤 3：实现 `TIMING_SIGNAL_AUDIT_EXTRA_OUTS` 复制**

在 `scripts/run_timing_signal_audit_periodic.sh` 的变量区新增：

```bash
EXTRA_OUTS="${TIMING_SIGNAL_AUDIT_EXTRA_OUTS:-}"
```

在缺 DB 判断前新增函数：

```bash
copy_extra_outputs() {
  if [[ -z "$EXTRA_OUTS" ]]; then
    return 0
  fi
  python - "$OUT" "$EXTRA_OUTS" <<'PY'
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
for raw in sys.argv[2].split(":"):
    item = raw.strip()
    if not item:
        continue
    dst = Path(item)
    if dst.resolve() == src.resolve():
        continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"Timing signal audit copied: out={dst}")
PY
}
```

在缺 DB 分支的 Python here-doc 后、`exit 0` 前调用：

```bash
  copy_extra_outputs
  exit 0
```

在正常审计分支的 `"${args[@]}"` 后调用：

```bash
"${args[@]}"
copy_extra_outputs
```

- [x] **步骤 4：运行绿灯测试**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py::test_timing_signal_audit_periodic_script_skips_missing_db -q -p no:cacheprovider
```

预期：`1 passed`。

- [x] **步骤 5：运行脚本文件回归**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：所有 `tests/test_timing_signal_audit_periodic.py` 测试通过。

- [x] **步骤 6：提交任务 1**

运行：

```bash
git add tests/test_timing_signal_audit_periodic.py scripts/run_timing_signal_audit_periodic.sh
git commit -m "fix(评测): 复制时机信号审计报告"
```

## 任务 2：周期入口派生并索引三类报告

**文件：**

- 修改：`tests/test_eval_baseline.py`
- 修改：`scripts/run_eval_periodic.sh`

- [x] **步骤 1：编写失败的周期脚本路径守卫**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_periodic_script_indexes_immutable_timing_signal_audit_reports():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert 'TIMING_SIGNAL_AUDIT_LATEST_OUT="${TIMING_SIGNAL_AUDIT_OUT:-evals/reports/timing_signal_audit_latest.json}"' in text
    assert 'TIMING_SIGNAL_AUDIT_DATED_OUT="evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json"' in text
    assert 'TIMING_SIGNAL_AUDIT_RUN_OUT="evals/reports/runs/${PERIODIC_RUN_ID}/timing_signal_audit.json"' in text
    assert 'export TIMING_SIGNAL_AUDIT_OUT="$TIMING_SIGNAL_AUDIT_LATEST_OUT"' in text
    assert "${TIMING_SIGNAL_AUDIT_RUN_OUT}:${TIMING_SIGNAL_AUDIT_DATED_OUT}" in text
    assert '"$TIMING_SIGNAL_AUDIT_RUN_OUT|$TIMING_SIGNAL_AUDIT_DATED_OUT|$TIMING_SIGNAL_AUDIT_LATEST_OUT"' in text
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_periodic_script_indexes_immutable_timing_signal_audit_reports -q -p no:cacheprovider
```

预期：失败，周期脚本还没有三类 TimingSignal audit 路径变量。

- [x] **步骤 3：修改周期脚本输出变量**

在 `scripts/run_eval_periodic.sh` 生成 `PERIODIC_STEPS_JSONL` 之后替换当前单一路径变量：

```bash
TIMING_SIGNAL_AUDIT_LATEST_OUT="${TIMING_SIGNAL_AUDIT_OUT:-evals/reports/timing_signal_audit_latest.json}"
TIMING_SIGNAL_AUDIT_DATED_OUT="evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json"
TIMING_SIGNAL_AUDIT_RUN_OUT="evals/reports/runs/${PERIODIC_RUN_ID}/timing_signal_audit.json"
TIMING_SIGNAL_AUDIT_EXTRA_OUTS_PREFIX="${TIMING_SIGNAL_AUDIT_RUN_OUT}:${TIMING_SIGNAL_AUDIT_DATED_OUT}"
export TIMING_SIGNAL_AUDIT_OUT="$TIMING_SIGNAL_AUDIT_LATEST_OUT"
export TIMING_SIGNAL_AUDIT_EXTRA_OUTS="${TIMING_SIGNAL_AUDIT_EXTRA_OUTS_PREFIX}${TIMING_SIGNAL_AUDIT_EXTRA_OUTS:+:${TIMING_SIGNAL_AUDIT_EXTRA_OUTS}}"
```

把 TimingSignal audit `run_step` 的 `report_paths` 参数改为：

```bash
"$TIMING_SIGNAL_AUDIT_RUN_OUT|$TIMING_SIGNAL_AUDIT_DATED_OUT|$TIMING_SIGNAL_AUDIT_LATEST_OUT"
```

- [x] **步骤 4：运行绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_periodic_script_indexes_immutable_timing_signal_audit_reports -q -p no:cacheprovider
```

预期：`1 passed`。

- [x] **步骤 5：运行周期脚本与 manifest 相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：两个测试文件全部通过。

- [x] **步骤 6：提交任务 2**

运行：

```bash
git add tests/test_eval_baseline.py scripts/run_eval_periodic.sh
git commit -m "ci(评测): 索引时机信号不可变报告"
```

## 任务 3：Workflow 上传 run-scoped TimingSignal audit

**文件：**

- 修改：`tests/test_eval_baseline.py`
- 修改：`.github/workflows/timing-gate-eval.yml`

- [x] **步骤 1：编写失败的 workflow artifact 守卫**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_workflow_uploads_run_scoped_timing_signal_audit():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "evals/reports/runs/**/timing_signal_audit.json" in text
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_workflow_uploads_run_scoped_timing_signal_audit -q -p no:cacheprovider
```

预期：失败，workflow artifact 路径尚未包含 run-scoped TimingSignal audit。

- [x] **步骤 3：追加 workflow artifact glob**

在 `.github/workflows/timing-gate-eval.yml` 的上传路径中加入：

```yaml
            evals/reports/runs/**/timing_signal_audit.json
```

保留已有：

```yaml
            evals/reports/*.json
            evals/reports/periodic_manifest_*.json
            evals/reports/runs/**/manifest.json
```

- [x] **步骤 4：运行绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_workflow_uploads_run_scoped_timing_signal_audit -q -p no:cacheprovider
```

预期：`1 passed`。

- [x] **步骤 5：运行 workflow 相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider
```

预期：`tests/test_eval_baseline.py` 全部通过。

- [x] **步骤 6：提交任务 3**

运行：

```bash
git add tests/test_eval_baseline.py .github/workflows/timing-gate-eval.yml
git commit -m "ci(评测): 归档时机信号运行报告"
```

## 任务 3.5：忽略 run-scoped 评测运行产物

**文件：**

- 修改：`tests/test_eval_baseline.py`
- 修改：`.gitignore`

- [x] **步骤 1：编写失败的忽略规则守卫**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_run_scoped_reports_are_gitignored():
    text = Path(".gitignore").read_text(encoding="utf-8")

    assert "evals/reports/runs/" in text
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_run_scoped_reports_are_gitignored -q -p no:cacheprovider
```

结果：`1 failed, 1 warning in 6.22s`，失败点为 `.gitignore` 缺少 `evals/reports/runs/`。

- [x] **步骤 3：追加忽略规则**

在 `.gitignore` 的 eval 报告段落加入：

```gitignore
evals/reports/runs/
```

- [x] **步骤 4：运行绿灯和相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_run_scoped_reports_are_gitignored -q -p no:cacheprovider
python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider
```

结果：

- 单条测试 `1 passed, 1 warning in 0.52s`。
- `tests/test_eval_baseline.py` 为 `25 passed, 1 warning in 1.04s`。

- [x] **步骤 5：提交任务 3.5**

运行：

```bash
git add .gitignore tests/test_eval_baseline.py
git commit -m "chore(评测): 忽略运行级评测报告"
```

提交：`95c88fe chore(评测): 忽略运行级评测报告`。

## 任务 4：文档收口与端到端验证

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/timing-signal-immutable-artifacts.md`

- [x] **步骤 1：更新评测文档**

在 `docs/evals.md` 的 TimingSignal audit 周期审计段落中记录：

```markdown
周期运行会同时写出三类 TimingSignal audit 报告：

- `evals/reports/timing_signal_audit_latest.json`：兼容本地查看和现有 CLI 默认输入。
- `evals/reports/YYYY-MM-DD-timing_signal_audit.json`：按日期归档，便于人工浏览。
- `evals/reports/runs/<run_id>/timing_signal_audit.json`：按运行 ID 固化，作为调参分析和复盘的优先证据。

缺少真实 DB 时三类路径都会写同一份 skipped 报告，`source.mode` 为
`skipped`，`source.reason` 为 `db_not_found`，周期脚本仍继续执行后续
manifest 写入。排查周期性失败时，优先查看
`evals/reports/runs/<run_id>/timing_signal_audit.json`，再查看 dated 和 latest。
```

- [x] **步骤 2：更新路线文档**

在 `docs/todo.md` 和 `docs/plan_walkthrough.md` 中把“补充更厚的 TimingSignal 不可变 artifact”更新为本阶段已进入执行，并记录：

```markdown
TimingSignal 不可变 artifact 加厚阶段已将周期审计报告扩展为 latest、dated 和 run-scoped 三类输出，manifest 优先索引 run-scoped 报告，workflow artifact 归档 run-scoped TimingSignal audit。
```

- [x] **步骤 3：勾选计划并写入验证记录**

在 `.Codex/plans/timing-signal-immutable-artifacts.md` 中把已完成步骤从 `- [ ]` 改为 `- [x]`，并在文末追加实际执行记录：

执行记录必须包含设计提交、计划提交、每个实现阶段的提交、红灯命令、绿灯命令和结果统计。记录中只写已经发生的真实提交号与真实命令输出摘要。

- [x] **步骤 4：运行文档与定向回归验证**

运行：

```bash
rg -n "T[O]DO|待[定]|占[位]|待[执]行|FIX[ME]|后续[实]现|添加[适]当|为上[述]" .Codex/plans/timing-signal-immutable-artifacts.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
python - <<'PY'
from pathlib import Path

paths = [
    ".Codex/plans/timing-signal-immutable-artifacts.md",
    "docs/evals.md",
    "docs/todo.md",
    "docs/plan_walkthrough.md",
]
bad = chr(0xFFFD)
for item in paths:
    text = Path(item).read_text(encoding="utf-8")
    if bad in text:
        raise SystemExit(f"replacement character found in {item}")
PY
git diff --check -- .Codex/plans/timing-signal-immutable-artifacts.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
python -B -m pytest tests/test_timing_signal_audit_periodic.py tests/test_eval_baseline.py tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py -q -p no:cacheprovider
```

预期：

- 前三个检查命令无输出。
- pytest 命令全部通过。

- [x] **步骤 5：运行周期脚本 smoke**

运行：

```bash
TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db \
PERIODIC_RUN_ID=immutable_artifact_smoke \
bash scripts/run_eval_periodic.sh
```

预期：

- 脚本退出码为 0。
- `evals/reports/timing_signal_audit_latest.json` 存在。
- `evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json` 存在。
- `evals/reports/runs/immutable_artifact_smoke/timing_signal_audit.json` 存在。
- `evals/reports/runs/immutable_artifact_smoke/manifest.json` 的 TimingSignal step `report_paths` 以 run-scoped 报告开头。

- [x] **步骤 6：运行全量回归**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：0 failures。

- [x] **步骤 7：提交任务 4**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/timing-signal-immutable-artifacts.md
git commit -m "docs(评测): 收口时机信号不可变报告"
```

## 最终核对

- [x] `TIMING_SIGNAL_AUDIT_OUT` 默认值保持 `evals/reports/timing_signal_audit_latest.json`。
- [x] 缺 DB skipped 报告复制到 latest、dated、run-scoped 三类路径。
- [x] 正常 DB 审计报告复制到 latest、dated、run-scoped 三类路径。
- [x] 周期 manifest 的 TimingSignal step 优先索引 run-scoped 报告。
- [x] Workflow artifact 包含 `evals/reports/runs/**/timing_signal_audit.json`。
- [x] 文档说明 latest 兼容入口和 run-scoped 优先证据的区别。
- [x] 每个阶段都有独立提交，且没有暂存无关文件。

## 执行记录

- 设计提交：`712cb0f docs(评测): 设计时机信号不可变报告`。
- 计划提交：`59d7e60 docs(计划): 记录时机信号不可变报告计划`。
- 任务 1 审计脚本复制：红灯 `1 failed, 1 warning in 6.18s`；绿灯单条 `1 passed, 1 warning in 0.84s`；文件回归 `3 passed, 1 warning in 0.74s`；提交 `ca2a90c fix(评测): 复制时机信号审计报告`。
- 任务 2 周期入口索引：红灯 `1 failed, 1 warning in 6.03s`；绿灯单条 `1 passed, 1 warning in 0.84s`；相邻回归 `26 passed, 1 warning in 1.83s`；提交 `df78dfd ci(评测): 索引时机信号不可变报告`。
- 任务 3 workflow 归档：红灯 `1 failed, 1 warning in 6.05s`；绿灯单条 `1 passed, 1 warning in 0.48s`；文件回归 `24 passed, 1 warning in 1.00s`；提交 `bad632b ci(评测): 归档时机信号运行报告`。
- 任务 3.5 忽略运行级产物：红灯 `1 failed, 1 warning in 6.22s`；绿灯单条 `1 passed, 1 warning in 0.52s`；文件回归 `25 passed, 1 warning in 1.04s`；提交 `95c88fe chore(评测): 忽略运行级评测报告`。
- 文档扫描：红旗词扫描无输出，U+FFFD 扫描无输出，`git diff --check` 无输出。
- 定向回归：`python -B -m pytest tests/test_timing_signal_audit_periodic.py tests/test_eval_baseline.py tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py -q -p no:cacheprovider` 结果 `42 passed, 1 warning in 2.65s`。
- 周期脚本 smoke：`TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db PERIODIC_RUN_ID=immutable_artifact_smoke bash scripts/run_eval_periodic.sh` 退出码 0；内部 eval guard `32 passed, 1 warning in 1.90s`；所有子 gate 通过；TimingSignal audit 写出 latest、dated、run-scoped 三类 skipped 报告。
- Smoke JSON 校验：三类 TimingSignal audit payload 完全一致，`source.mode=skipped`；`evals/reports/runs/immutable_artifact_smoke/manifest.json` 的 TimingSignal step `report_paths` 顺序为 run-scoped、dated、latest。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider` 结果 `1431 passed, 6 skipped, 139 warnings in 106.38s`。
