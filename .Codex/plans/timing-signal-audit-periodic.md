# TimingGate 信号周期审计实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 执行实现步骤，并在提交前使用 superpowers:verification-before-completion 和 superpowers:chinese-commit-conventions。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 TimingGate 真实日志信号审计接入周期性评测，并在缺少真实 DB 时产出可归档 skipped 报告。

**架构：** 新增一个独立 Bash 脚本负责 DB 路径、报告路径和缺库跳过策略；`scripts/run_eval_periodic.sh` 只新增 keep-going 调用步骤。审计核心继续复用 `evals.timing_signal_audit`，避免重复实现 Python 聚合逻辑。

**技术栈：** Bash、Python `json`、pytest、现有 `evals.timing_signal_audit` CLI、GitHub Actions artifact 路径。

---

## 文件结构

- 创建：`scripts/run_timing_signal_audit_periodic.sh`
  - 周期审计入口，处理环境变量、缺 DB 跳过报告、调用现有 CLI。
- 创建：`tests/test_timing_signal_audit_periodic.py`
  - 行为测试：缺 DB 时脚本退出 0 并写 skipped JSON；静态测试：周期脚本引用审计入口。
- 修改：`scripts/run_eval_periodic.sh`
  - 增加 `run_step "timing signal audit"`，保持 keep-going 语义。
- 修改：`docs/evals.md`
  - 记录周期审计报告、缺库跳过语义和环境变量。
- 修改：`.Codex/plans/timing-signal-audit-periodic.md`
  - 执行时勾选任务并记录验证证据。
- 修改：`docs/plan_walkthrough.md`
  - 收口时同步提交号、验证结果和下一阶段状态。

## 子 agent 分配决策

本阶段不并行分派实现 agent。原因：

- 新增测试、脚本和周期入口存在严格 TDD 顺序。
- 修改文件少，且 `scripts/run_eval_periodic.sh` 与测试断言共享同一接口。
- 多个 agent 同时修改计划、脚本和文档会增加冲突成本。

后续如果进入 RAG generated → manual 仲裁、通用候选队列统一、报告趋势聚合等互不干扰模块，再按模块分派只读审计或独立实现 agent。

## 任务 1：写红灯测试

**文件：**

- 创建：`tests/test_timing_signal_audit_periodic.py`
- 读取：`scripts/run_eval_periodic.sh`

- [ ] **步骤 1：编写失败的脚本行为测试**

写入测试：

```python
"""TimingGate 信号周期审计脚本测试。"""

import json
import os
import subprocess
from pathlib import Path


def test_timing_signal_audit_periodic_script_skips_missing_db(tmp_path):
    out = tmp_path / "reports" / "timing_signal_audit_latest.json"
    env = {
        **os.environ,
        "TIMING_SIGNAL_AUDIT_DB": str(tmp_path / "missing.db"),
        "TIMING_SIGNAL_AUDIT_OUT": str(out),
        "TIMING_SIGNAL_AUDIT_LIMIT": "17",
        "TIMING_SIGNAL_AUDIT_AFTER_ID": "5",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        ["bash", "scripts/run_timing_signal_audit_periodic.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["total_samples"] == 0
    assert payload["samples"] == []
    assert payload["source"]["mode"] == "skipped"
    assert payload["source"]["reason"] == "db_not_found"
    assert payload["source"]["db"] == str(tmp_path / "missing.db")
    assert payload["source"]["after_id"] == 5
    assert payload["source"]["limit"] == 17
```

- [ ] **步骤 2：编写失败的周期入口静态测试**

在同一文件追加：

```python
def test_eval_periodic_script_runs_timing_signal_audit_step():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert "timing signal audit" in text
    assert "scripts/run_timing_signal_audit_periodic.sh" in text
```

- [ ] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：失败。第一条测试应失败于 `scripts/run_timing_signal_audit_periodic.sh` 不存在；第二条测试应失败于 `run_eval_periodic.sh` 未引用该脚本。

## 任务 2：实现周期审计脚本

**文件：**

- 创建：`scripts/run_timing_signal_audit_periodic.sh`

- [ ] **步骤 1：新增最小脚本**

创建脚本：

```bash
#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1

DB="${TIMING_SIGNAL_AUDIT_DB:-data/nanobot.db}"
OUT="${TIMING_SIGNAL_AUDIT_OUT:-evals/reports/timing_signal_audit_latest.json}"
LIMIT="${TIMING_SIGNAL_AUDIT_LIMIT:-200}"
AFTER_ID="${TIMING_SIGNAL_AUDIT_AFTER_ID:-0}"
SIGNALS="${TIMING_SIGNAL_AUDIT_SIGNALS:-}"

if [[ ! -f "$DB" ]]; then
  python - "$OUT" "$DB" "$LIMIT" "$AFTER_ID" "$SIGNALS" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

from core.eval_sampling.timing_signal_audit import build_timing_signal_audit_report

out = Path(sys.argv[1])
db = sys.argv[2]
limit = int(sys.argv[3])
after_id = int(sys.argv[4])
signals = [item.strip() for item in sys.argv[5].split(",") if item.strip()]

payload = {
    **build_timing_signal_audit_report([]),
    "samples": [],
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "source": {
        "mode": "skipped",
        "reason": "db_not_found",
        "db": db,
        "after_id": after_id,
        "limit": limit,
        "signals": signals,
    },
}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Timing signal audit skipped: db_not_found db={db} out={out}")
PY
  exit 0
fi

args=(
  python -B -m evals.timing_signal_audit
  --db "$DB"
  --out "$OUT"
  --limit "$LIMIT"
  --after-id "$AFTER_ID"
)

if [[ -n "$SIGNALS" ]]; then
  args+=(--signals "$SIGNALS")
fi

"${args[@]}"
```

- [ ] **步骤 2：运行红灯测试确认仍有周期入口失败**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：缺 DB 行为测试通过；周期入口静态测试仍失败。

## 任务 3：接入周期评测脚本

**文件：**

- 修改：`scripts/run_eval_periodic.sh`

- [ ] **步骤 1：添加 keep-going 步骤**

在 RAG benchmark gate 之后、`exit "$status"` 之前增加：

```bash
run_step "timing signal audit" \
  bash scripts/run_timing_signal_audit_periodic.sh
```

- [ ] **步骤 2：运行定向测试验证通过**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：`2 passed`。

- [ ] **步骤 3：运行相邻测试**

运行：

```bash
python -B -m pytest tests/test_timing_signal_audit.py tests/test_eval_baseline.py -q -p no:cacheprovider
```

预期：全部通过。

## 任务 4：更新评测文档

**文件：**

- 修改：`docs/evals.md`

- [ ] **步骤 1：补充周期审计说明**

在「周期性复跑与报告归档」章节补充：

````markdown
周期性入口还会运行 TimingGate signal audit：

```bash
bash scripts/run_timing_signal_audit_periodic.sh
```

默认读取 `data/nanobot.db`，可通过 `TIMING_SIGNAL_AUDIT_DB` 指向真实 SQLite DB。
默认报告路径是 `evals/reports/timing_signal_audit_latest.json`，会被现有
`evals/reports/*.json` artifact 规则归档。CI 或本地缺少真实 DB 时，脚本会写出
`source.mode=skipped`、`source.reason=db_not_found` 的空报告并退出 0；这只表示
本轮没有可审计真实库，不表示信号质量通过。
````

- [ ] **步骤 2：检查文档空白**

运行：

```bash
git diff --check -- docs/evals.md
```

预期：无输出。

## 任务 5：执行脚本级验证

**文件：**

- 读取：`scripts/run_timing_signal_audit_periodic.sh`
- 读取：`scripts/run_eval_periodic.sh`
- 输出：`evals/reports/timing_signal_audit_latest.json`

- [ ] **步骤 1：验证独立脚本缺库跳过**

运行：

```bash
TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db \
TIMING_SIGNAL_AUDIT_OUT=tmp/timing_signal_audit_latest.json \
bash scripts/run_timing_signal_audit_periodic.sh
```

预期：退出码 `0`，输出包含 `Timing signal audit skipped`，并写入 `tmp/timing_signal_audit_latest.json`。

- [ ] **步骤 2：验证周期脚本 keep-going**

运行：

```bash
bash scripts/run_eval_periodic.sh
```

预期：稳定 gate 继续通过，最后出现 `timing signal audit: passed`。如果默认 `data/nanobot.db` 不存在，应同时生成 skipped 报告并保持退出码 `0`。

- [ ] **步骤 3：运行全量测试**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：全量通过，失败数为 0。

## 任务 6：实现阶段提交

**文件：**

- 暂存：`scripts/run_timing_signal_audit_periodic.sh`
- 暂存：`scripts/run_eval_periodic.sh`
- 暂存：`tests/test_timing_signal_audit_periodic.py`
- 暂存：`docs/evals.md`

- [ ] **步骤 1：检查 diff**

运行：

```bash
git diff --check -- scripts/run_timing_signal_audit_periodic.sh scripts/run_eval_periodic.sh tests/test_timing_signal_audit_periodic.py docs/evals.md
git diff --stat -- scripts/run_timing_signal_audit_periodic.sh scripts/run_eval_periodic.sh tests/test_timing_signal_audit_periodic.py docs/evals.md
```

预期：无空白错误，stat 只包含本阶段文件。

- [ ] **步骤 2：按文件暂存并提交**

运行：

```bash
git add scripts/run_timing_signal_audit_periodic.sh scripts/run_eval_periodic.sh tests/test_timing_signal_audit_periodic.py docs/evals.md
git commit -m "ci(评测): 接入时机信号周期审计"
```

预期：生成单独实现提交。

## 任务 7：文档收口提交

**文件：**

- 修改：`.Codex/plans/timing-signal-audit-periodic.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：勾选本计划任务并写入验证证据**

记录：

- 红灯测试输出。
- 绿灯定向测试输出。
- 相邻测试输出。
- 独立脚本缺库跳过验证输出。
- 周期脚本输出。
- 全量测试输出。
- 实现提交号。

- [ ] **步骤 2：更新 walkthrough**

在 `docs/plan_walkthrough.md` 顶部追加本阶段设计、计划、实现和验证结果，并在已完成基线表新增「TimingGate 信号周期审计」。

- [ ] **步骤 3：验证文档收口**

运行：

```bash
git diff --check -- .Codex/plans/timing-signal-audit-periodic.md docs/plan_walkthrough.md
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：无空白错误，定向测试通过。

- [ ] **步骤 4：按文件暂存并提交**

运行：

```bash
git add .Codex/plans/timing-signal-audit-periodic.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口时机信号周期审计状态"
```

预期：生成单独文档收口提交。
