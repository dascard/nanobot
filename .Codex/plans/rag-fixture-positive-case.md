# RAG fixture 正例门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 RAG benchmark 增加仓库自包含 fixture-backed positive case，让稳定 gate 不再只有 `constraint_only`，并使 `hit@5` / `mrr` 指标具备真实召回信号。

**架构：** 新增 eval 专用 fixture DB builder，runner 在显式 `--fixture positive_v1` 时先创建固定 SQLite fixture DB，再追加 fixture case 并用现有只读路径执行。PR gate 和周期性 gate 切到 `manual+fixture` scope，baseline 与合同测试同步覆盖 positive metrics。

**技术栈：** Python、pytest、SQLAlchemy、SQLite FTS5、`core.semantic.indexer.upsert_semantic_chunks`、`evals.rag_benchmark.run`、Bash gate 脚本。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-rag-fixture-positive-case-design.md`
- 设计提交：`6cbce35 docs(评测): 设计 RAG fixture 正例门禁`
- 当前范围：fixture DB builder、fixture positive case、runner CLI、baseline 合同、PR / periodic gate、文档收口。
- 不纳入本计划：runtime provider、真实 DB 采样、Admin / WebUI 默认行为切换、三路 RAG 主链路重构。

## 实际执行记录

- 设计提交：`6cbce35 docs(评测): 设计 RAG fixture 正例门禁`。
- 计划提交：`375b9b3 docs(计划): 记录 RAG fixture 正例计划`。
- 任务 1 红灯：fixture DB 测试失败于 `ModuleNotFoundError: No module named 'evals.rag_benchmark.fixtures'`；fixture origin 聚合测试失败于 `KeyError: 'overall_fixture'`。
- 任务 1 绿灯：两个新增定向测试结果 `2 passed, 1 warning in 0.92s`；`tests/test_rag_benchmark.py` 结果 `15 passed, 1 warning in 1.22s`。

## 子 agent 分工建议

本计划可拆分，但不能让多个 worker 同时修改同一文件。

- Worker A：负责 `evals/rag_benchmark/fixtures.py` 和 fixture 直跑测试。写入范围建议为 `evals/rag_benchmark/fixtures.py`、`tests/test_rag_benchmark.py` 中新增 fixture 测试块。
- Worker B：在 Worker A 合并后负责 `evals/rag_benchmark/run.py` 的 CLI 接入和 CLI 测试。写入范围建议为 `evals/rag_benchmark/run.py`、`tests/test_rag_benchmark.py` 中 CLI 测试块。
- Worker C：在 runner 接入后负责脚本、baseline 合同和文档收口。写入范围建议为 `scripts/run_eval_pr_gate.sh`、`scripts/run_eval_periodic.sh`、`tests/test_eval_baseline.py`、`evals/baselines/rag_benchmark.json`、`docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`、本计划文件。

如果并行实现，Worker A 与 Worker B 不应同时改 `tests/test_rag_benchmark.py`。更稳妥的执行顺序是 A → B → C，每个阶段单独验证和提交。

## 文件结构

- 创建：`evals/rag_benchmark/fixtures.py`
  - 职责：创建固定 RAG fixture SQLite DB，seed memory positive 数据，返回与 seed 完全匹配的 `BenchmarkCase`。
- 修改：`evals/rag_benchmark/run.py`
  - 职责：新增 `--fixture` / `--fixture-db` 参数，启用 fixture 时构建 fixture DB、追加 fixture case、切换 benchmark DB 路径和 case scope。
- 修改：`evals/rag_benchmark/scoring.py`
  - 职责：为 `meta.origin == "fixture_exact"` 输出 `overall_fixture` 指标分组。
- 修改：`tests/test_rag_benchmark.py`
  - 职责：覆盖 fixture DB 只读 positive 命中、fixture origin 聚合、CLI `manual+fixture` gate、baseline 合同。
- 修改：`tests/test_eval_baseline.py`
  - 职责：守卫 PR / periodic gate 脚本启用 fixture 和 positive 指标阈值。
- 修改：`scripts/run_eval_pr_gate.sh`
  - 职责：RAG stable gate 改为显式启用 `positive_v1` fixture。
- 修改：`scripts/run_eval_periodic.sh`
  - 职责：周期性 RAG stable gate 改为显式启用 `positive_v1` fixture。
- 修改：`evals/baselines/rag_benchmark.json`
  - 职责：同步 `manual+fixture` deterministic baseline，包含 9 个 manual constraint case 和 1 个 memory positive fixture case。
- 修改：`docs/evals.md`
  - 职责：记录 RAG stable gate 已包含 fixture-backed positive case 和指标阈值。
- 修改：`docs/todo.md`
  - 职责：同步 P4 路线下一阶段状态。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交边界、验证命令和完成状态。
- 修改：`.Codex/plans/rag-fixture-positive-case.md`
  - 职责：执行完成后勾选步骤并记录实际验证结果。

## 任务 1：fixture builder 与正例直跑

**文件：**
- 创建：`evals/rag_benchmark/fixtures.py`
- 修改：`evals/rag_benchmark/scoring.py`
- 修改：`tests/test_rag_benchmark.py`

- [x] **步骤 1：编写 fixture DB 红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试：

```python
def test_rag_benchmark_fixture_db_supports_memory_positive_case(tmp_path):
    from evals.rag_benchmark.fixtures import build_fixture_db
    from evals.rag_benchmark.run import run_benchmark

    fixture_db = tmp_path / "positive.db"

    cases = build_fixture_db(fixture_db, preset="positive_v1")
    results, scores = run_benchmark(fixture_db, cases, provider_mode="deterministic")

    assert [case.id for case in cases] == ["memory_fixture_positive_001"]
    assert results[0].case_id == "memory_fixture_positive_001"
    assert results[0].candidate_ids[0] == "memory_digest:fixture-memory-positive-001:card:0"
    assert scores[0].ok is True
    assert scores[0].rank == 1
    assert scores[0].hit_at["5"] is True
    assert scores[0].mrr == 1.0
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `ModuleNotFoundError: No module named 'evals.rag_benchmark.fixtures'`。

- [x] **步骤 3：编写 fixture origin 聚合红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试：

```python
def test_rag_aggregate_scores_tracks_fixture_origin():
    from evals.rag_benchmark.schema import BenchmarkCase, CaseScore
    from evals.rag_benchmark.scoring import aggregate_scores

    case = BenchmarkCase(
        id="memory_fixture_positive_001",
        source_type="memory",
        case_type="positive",
        query="KohakuVQ 端口冲突",
        expected={"candidate_ids": ["memory_digest:fixture-memory-positive-001:card:0"]},
        meta={"origin": "fixture_exact"},
    )
    score = CaseScore(
        case_id=case.id,
        source_type="memory",
        case_type="positive",
        ok=True,
        rank=1,
        hit_at={"1": True, "3": True, "5": True},
        mrr=1.0,
    )

    metrics = aggregate_scores([case], [score])

    assert metrics["overall_fixture"]["total_cases"] == 1
    assert metrics["overall_fixture"]["positive_cases"] == 1
    assert metrics["overall_fixture"]["hit@5"] == 1.0
    assert metrics["overall_fixture"]["mrr"] == 1.0
```

- [x] **步骤 4：运行聚合红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_aggregate_scores_tracks_fixture_origin -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `KeyError: 'overall_fixture'`。

- [x] **步骤 5：新增 fixture builder**

创建 `evals/rag_benchmark/fixtures.py`：

```python
"""RAG benchmark fixture 数据。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.database import Base
from core.semantic.adapters import SemanticChunk
from core.semantic.indexer import upsert_semantic_chunks
from evals.rag_benchmark.schema import BenchmarkCase


FIXTURE_PRESET = "positive_v1"
MEMORY_CASE_ID = "memory_fixture_positive_001"
MEMORY_SOURCE_ID = "fixture-memory-positive-001"
MEMORY_SOURCE_SUB_ID = "card:0"
MEMORY_CANDIDATE_ID = f"memory_digest:{MEMORY_SOURCE_ID}:{MEMORY_SOURCE_SUB_ID}"
MEMORY_USER_ID = "rag_fixture_user"
MEMORY_SESSION_ID = "rag_fixture_session"
MEMORY_QUERY = "KohakuVQ 端口冲突"
MEMORY_INDEX_VERSION = "fixture:v1:memory"


def _ensure_supported_preset(preset: str) -> None:
    if preset != FIXTURE_PRESET:
        raise ValueError(f"unsupported rag benchmark fixture preset: {preset}")


def _memory_positive_case() -> BenchmarkCase:
    return BenchmarkCase(
        id=MEMORY_CASE_ID,
        suite="rag_benchmark",
        source_type="memory",
        case_type="positive",
        query=MEMORY_QUERY,
        filters={
            "source": "digest",
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
        },
        expected={
            "candidate_ids": [MEMORY_CANDIDATE_ID],
            "hit_at": 5,
            "expected_source_type": "memory_digest",
        },
        meta={
            "origin": "fixture_exact",
            "sensitivity": "safe",
            "fixture": FIXTURE_PRESET,
        },
    )


def fixture_cases(preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """返回 fixture preset 对应的 case 描述，不写数据库。"""

    _ensure_supported_preset(str(preset))
    return [_memory_positive_case()]


def seed_positive_fixture_db(db: Session) -> list[BenchmarkCase]:
    """向已创建 schema 的数据库写入 positive fixture 数据。"""

    text = (
        "KohakuVQ 服务部署时出现 uvicorn 8000 端口冲突，"
        "处理方式是检查占用进程、释放端口或切换启动端口。"
    )
    chunk = SemanticChunk(
        source_type="memory_digest",
        source_id=MEMORY_SOURCE_ID,
        source_sub_id=MEMORY_SOURCE_SUB_ID,
        title="KohakuVQ 端口冲突排查",
        text=text,
        lexical_text=f"{MEMORY_QUERY} uvicorn 8000 端口占用 排查",
        embedding_text=f"{MEMORY_QUERY} uvicorn 8000 端口占用 排查",
        metadata={
            "user_id": MEMORY_USER_ID,
            "session_id": MEMORY_SESSION_ID,
            "fixture": FIXTURE_PRESET,
        },
        visibility="recall",
        quality_score=0.9,
        trust_level="medium",
        source_prior=0.65,
    )
    upsert_semantic_chunks(db, [chunk], index_version=MEMORY_INDEX_VERSION)
    return fixture_cases(FIXTURE_PRESET)


def _unlink_sqlite_files(path: Path) -> None:
    for raw in (str(path), f"{path}-wal", f"{path}-shm"):
        target = Path(raw)
        if target.exists():
            target.unlink()


def build_fixture_db(path: str | Path, *, preset: str = FIXTURE_PRESET) -> list[BenchmarkCase]:
    """覆盖创建 fixture SQLite 文件库，并返回 fixture cases。"""

    _ensure_supported_preset(str(preset))
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _unlink_sqlite_files(db_path)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = SessionLocal()
        try:
            return seed_positive_fixture_db(db)
        finally:
            db.close()
    finally:
        engine.dispose()
```

- [x] **步骤 6：增加 fixture origin 指标分组**

修改 `evals/rag_benchmark/scoring.py`：

```python
def _origin_group(case: BenchmarkCase) -> str:
    origin = str(case.meta.get("origin") or "manual_hard")
    if origin == "generated_exact":
        return "overall_exact"
    if origin == "generated_weak":
        return "overall_weak"
    if origin == "fixture_exact":
        return "overall_fixture"
    return "overall_manual"
```

并修改 `aggregate_scores()` 中的分组列表：

```python
for group in ("overall_exact", "overall_weak", "overall_manual", "overall_fixture"):
    group_cases = [case for case in case_list if _origin_group(case) == group]
    group_scores = [by_id[case.id] for case in group_cases if case.id in by_id]
    report[group] = _metric_block(group_cases, group_scores)
```

- [x] **步骤 7：运行任务 1 绿灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case tests/test_rag_benchmark.py::test_rag_aggregate_scores_tracks_fixture_origin -v -p no:cacheprovider
```

预期：2 passed。

- [x] **步骤 8：运行 RAG 单文件回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py -v -p no:cacheprovider
```

预期：现有 RAG benchmark 测试全部通过。

- [x] **步骤 9：提交任务 1**

运行：

```bash
git add evals/rag_benchmark/fixtures.py evals/rag_benchmark/scoring.py tests/test_rag_benchmark.py
git commit -m "feat(评测): 增加 RAG fixture 正例数据"
```

## 任务 2：runner CLI 接入 fixture

**文件：**
- 修改：`evals/rag_benchmark/run.py`
- 修改：`tests/test_rag_benchmark.py`

- [ ] **步骤 1：编写 CLI fixture 红灯测试**

在 `tests/test_rag_benchmark.py` 中新增测试：

```python
def test_rag_benchmark_cli_runs_manual_fixture_positive_gate(tmp_path, capsys):
    from evals.rag_benchmark import run as rag_run

    manual = tmp_path / "manual"
    generated = tmp_path / "generated"
    reports = tmp_path / "reports"
    fixture_db = tmp_path / "fixture.db"
    manual.mkdir()
    generated.mkdir()
    (manual / "constraint.json").write_text(
        json.dumps(
            {
                "id": "constraint",
                "suite": "rag_benchmark",
                "source_type": "sticker",
                "case_type": "constraint_only",
                "query": "表情包",
                "expected": {
                    "candidate_ids": [],
                    "allow_empty": True,
                    "max_reranker_candidates": 10,
                },
                "meta": {"origin": "manual_hard"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "suite": "rag_benchmark",
                "provider_mode": "deterministic",
                "case_scope": "manual+fixture",
                "metrics": {
                    "overall": {
                        "total_cases": 2,
                        "positive_cases": 1,
                        "passed_cases": 2,
                        "pass_rate": 1.0,
                        "hit@5": 1.0,
                        "mrr": 1.0,
                        "degraded_rate": 0.0,
                        "case_false_positive_rate": 0.0,
                        "unexpected_source_rate": 0.0,
                    }
                },
                "case_scores": [
                    {"case_id": "constraint", "ok": True, "errors": []},
                    {
                        "case_id": "memory_fixture_positive_001",
                        "ok": True,
                        "rank": 1,
                        "hit_at": {"1": True, "3": True, "5": True},
                        "errors": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = rag_run.main(
        [
            "--manual",
            str(manual),
            "--generated",
            str(generated),
            "--report-out",
            str(reports),
            "--provider-mode",
            "deterministic",
            "--manual-only",
            "--fixture",
            "positive_v1",
            "--fixture-db",
            str(fixture_db),
            "--baseline",
            str(baseline),
            "--min-pass-rate",
            "1.0",
            "--min-hit-at-5",
            "1.0",
            "--min-mrr",
            "1.0",
            "--max-new-failures",
            "0",
            "--max-degraded-rate",
            "0.0",
            "--max-unexpected-source-rate",
            "0.0",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads((reports / "latest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Gate passed" in captured.out
    assert report["case_scope"] == "manual+fixture"
    assert report["metrics"]["overall"]["positive_cases"] == 1
    assert report["metrics"]["overall"]["hit@5"] == 1.0
    assert report["metrics"]["overall"]["mrr"] == 1.0
```

- [ ] **步骤 2：运行 CLI 红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `unrecognized arguments: --fixture positive_v1 --fixture-db`。

- [ ] **步骤 3：实现 runner 参数和 case 合并**

修改 `evals/rag_benchmark/run.py`，新增 import：

```python
from evals.rag_benchmark.fixtures import FIXTURE_PRESET, build_fixture_db
```

新增 helper：

```python
def _fixture_enabled(args: argparse.Namespace) -> bool:
    return str(args.fixture or "none") != "none"


def _case_scope(args: argparse.Namespace) -> str:
    parts = ["manual"] if args.manual_only else ["manual", "generated"]
    if _fixture_enabled(args):
        parts.append("fixture")
    return "+".join(parts)


def _benchmark_db_path(args: argparse.Namespace) -> str | Path:
    if _fixture_enabled(args):
        return args.fixture_db
    return args.db
```

修改 parser：

```python
parser.add_argument("--fixture", choices=["none", FIXTURE_PRESET], default="none")
parser.add_argument(
    "--fixture-db",
    default=f"tmp/rag_benchmark/fixtures/{FIXTURE_PRESET}.db",
)
```

修改 `main()` 中 cases 和 DB 路径组装：

```python
cases = load_cases(manual_dir=args.manual, generated_dir=_generated_dir(args))
if _fixture_enabled(args):
    cases.extend(build_fixture_db(args.fixture_db, preset=str(args.fixture)))
results, scores = run_benchmark(
    _benchmark_db_path(args),
    cases,
    use_runtime_providers=not args.no_runtime_providers,
    provider_mode=provider_mode,
)
```

- [ ] **步骤 4：运行任务 2 绿灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate -v -p no:cacheprovider
```

预期：1 passed。

- [ ] **步骤 5：运行 RAG 单文件回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 6：提交任务 2**

运行：

```bash
git add evals/rag_benchmark/run.py tests/test_rag_benchmark.py
git commit -m "feat(评测): 支持 RAG fixture 门禁入口"
```

## 任务 3：baseline 合同、脚本和稳定 baseline

**文件：**
- 修改：`tests/test_rag_benchmark.py`
- 修改：`tests/test_eval_baseline.py`
- 修改：`scripts/run_eval_pr_gate.sh`
- 修改：`scripts/run_eval_periodic.sh`
- 修改：`evals/baselines/rag_benchmark.json`

- [ ] **步骤 1：更新 baseline 合同红灯测试**

替换 `tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract`：

```python
def test_rag_benchmark_baseline_file_matches_manual_gate_contract():
    from evals.rag_benchmark.baseline import load_rag_baseline
    from evals.rag_benchmark.cases import load_cases
    from evals.rag_benchmark.fixtures import fixture_cases

    baseline = load_rag_baseline("evals/baselines/rag_benchmark.json")
    manual_cases = [
        case
        for case in load_cases(
            manual_dir="evals/cases/rag_benchmark/manual",
            generated_dir="tmp/rag_benchmark/__contract_empty__",
        )
        if case.status == "enabled"
    ]
    fixture = fixture_cases("positive_v1")
    stable_cases = manual_cases + fixture
    baseline_scores = {
        str(item.get("case_id") or ""): item
        for item in baseline["case_scores"]
    }
    stable_case_ids = {case.id for case in stable_cases}

    assert baseline["suite"] == "rag_benchmark"
    assert baseline["provider_mode"] == "deterministic"
    assert baseline["case_scope"] == "manual+fixture"
    assert baseline["metrics"]["overall"]["total_cases"] == len(stable_cases)
    assert baseline["metrics"]["overall"]["positive_cases"] >= 1
    assert baseline["metrics"]["overall"]["hit@5"] > 0
    assert baseline["metrics"]["overall"]["mrr"] > 0
    assert set(baseline_scores) == stable_case_ids
    assert "case_scores" in baseline
    assert all("case_id" in item and "ok" in item for item in baseline["case_scores"])
    fixture_score = baseline_scores["memory_fixture_positive_001"]
    assert fixture_score["ok"] is True
    assert fixture_score["hit_at"]["5"] is True
```

- [ ] **步骤 2：运行 baseline 合同红灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL，失败原因包含 `assert 'manual' == 'manual+fixture'` 或 `positive_cases` 仍为 0。

- [ ] **步骤 3：更新脚本守卫红灯测试**

在 `tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites` 中增加断言：

```python
assert "--fixture positive_v1" in text
assert "--fixture-db tmp/rag_benchmark/fixtures/positive_v1.db" in text
assert "--min-hit-at-5 1.0" in text
assert "--min-mrr 1.0" in text
```

在 `tests/test_eval_baseline.py::test_eval_periodic_script_runs_stable_suites` 中增加同样断言。

- [ ] **步骤 4：运行脚本守卫红灯**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites tests/test_eval_baseline.py::test_eval_periodic_script_runs_stable_suites -v -p no:cacheprovider
```

预期：FAIL，失败原因是脚本尚未包含 `--fixture positive_v1`。

- [ ] **步骤 5：更新 PR gate 脚本**

修改 `scripts/run_eval_pr_gate.sh` 的 RAG benchmark 命令，加入 fixture 和 positive 指标阈值：

```bash
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --fixture positive_v1 \
  --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --min-hit-at-5 1.0 \
  --min-mrr 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

- [ ] **步骤 6：更新 periodic gate 脚本**

修改 `scripts/run_eval_periodic.sh` 的 RAG benchmark 命令，保持和 PR gate 同样的 fixture 参数与阈值：

```bash
run_step "rag benchmark manual fixture deterministic gate" \
  python -B -m evals.rag_benchmark.run \
    --manual evals/cases/rag_benchmark/manual \
    --generated tmp/rag_benchmark/empty \
    --provider-mode deterministic \
    --manual-only \
    --fixture positive_v1 \
    --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
    --baseline evals/baselines/rag_benchmark.json \
    --min-pass-rate 1.0 \
    --min-hit-at-5 1.0 \
    --min-mrr 1.0 \
    --max-new-failures 0 \
    --max-degraded-rate 0.0 \
    --max-unexpected-source-rate 0.0
```

- [ ] **步骤 7：运行脚本守卫绿灯**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites tests/test_eval_baseline.py::test_eval_periodic_script_runs_stable_suites -v -p no:cacheprovider
```

预期：2 passed。

- [ ] **步骤 8：运行 RAG fixture gate 生成报告**

运行：

```bash
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --fixture positive_v1 \
  --fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --min-hit-at-5 1.0 \
  --min-mrr 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

第一次运行在 baseline 更新前预期失败，原因是 baseline `case_scope` 或 positive metrics 仍旧。失败报告仍会写入 `tmp/rag_benchmark/reports/latest.json`，用于刷新 baseline 前核对实际输出。

- [ ] **步骤 9：更新 `evals/baselines/rag_benchmark.json`**

用 `tmp/rag_benchmark/reports/latest.json` 中以下字段刷新 baseline：

- `suite`
- `provider_mode`
- `case_scope`
- `metrics`
- `failed_cases`
- `case_scores`
- `cases`
- `results`
- `scores`

刷新后确认：

- `case_scope == "manual+fixture"`
- `metrics.overall.total_cases == 10`
- `metrics.overall.positive_cases == 1`
- `metrics.overall.hit@5 == 1.0`
- `metrics.overall.mrr == 1.0`
- `memory_fixture_positive_001` 在 `case_scores` 中 `ok=true`

- [ ] **步骤 10：运行 baseline 合同绿灯**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：1 passed。

- [ ] **步骤 11：运行 RAG fixture gate 绿灯**

运行步骤 8 的同一命令。

预期输出：

```text
cases=10 passed=10 failed=0
Gate passed
```

- [ ] **步骤 12：运行相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 13：提交任务 3**

运行：

```bash
git add tests/test_rag_benchmark.py tests/test_eval_baseline.py scripts/run_eval_pr_gate.sh scripts/run_eval_periodic.sh evals/baselines/rag_benchmark.json
git commit -m "test(评测): 固化 RAG fixture 正例门禁"
```

## 任务 4：文档收口

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-fixture-positive-case.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

记录 RAG stable gate 的当前范围：

```markdown
RAG stable gate 当前使用 `manual+fixture` scope：仓库内 9 个 manual `constraint_only` case 加 1 个 `positive_v1` memory fixture case。PR gate 使用 deterministic provider、固定 SQLite fixture DB 和 `--min-hit-at-5 1.0` / `--min-mrr 1.0`，用于防止正例召回退化。
```

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P4 路线项 8 的当前状态中补充：

```markdown
P4-5D 已完成 fixture-backed positive RAG case：`evals.rag_benchmark` stable gate 已从 `manual` 切到 `manual+fixture`，baseline 包含 `memory_fixture_positive_001`，`positive_cases` 从 0 提升到 1，`hit@5` / `mrr` 进入真实门禁。
```

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

在进度总览和 P4-5C 后新增 P4-5D 记录：

```markdown
| P4-5D | 已完成 | RAG fixture 正例门禁 | 新增固定 memory fixture DB、`manual+fixture` stable gate 和 positive metrics baseline | `docs(评测): 设计 RAG fixture 正例门禁` / `docs(计划): 记录 RAG fixture 正例计划` / `feat(评测): 增加 RAG fixture 正例数据` / `feat(评测): 支持 RAG fixture 门禁入口` / `test(评测): 固化 RAG fixture 正例门禁` |
```

并记录实际验证命令和输出。

- [ ] **步骤 4：更新本计划执行记录**

在本文顶部新增实际执行记录。执行记录只写已经发生的提交，不预先写空 SHA；每完成一个任务后追加一条真实记录，例如：

```markdown
## 实际执行记录

- 设计提交：`6cbce35 docs(评测): 设计 RAG fixture 正例门禁`。
- 计划提交：`提交短 SHA docs(计划): 记录 RAG fixture 正例计划`。
```

后续任务记录必须使用真实提交短 SHA 和真实 commit subject。

- [ ] **步骤 5：文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|T[B]D|F[I]XME|x{3}|X{3}|\\x{2026}\\x{2026}|\\.\\.\\." docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-positive-case.md
rg -n $'\357\277\275' docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-positive-case.md
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-positive-case.md
```

预期：前两个命令无匹配，`git diff --check` 无输出。

- [ ] **步骤 6：运行文档相邻回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
bash scripts/run_eval_periodic.sh
```

预期：全部通过，RAG gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`。

- [ ] **步骤 7：提交任务 4**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/rag-fixture-positive-case.md
git commit -m "docs(评测): 收口 RAG fixture 正例状态"
```

## 最终验证

- [ ] **步骤 1：运行定向回归**

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 2：运行 PR gate**

```bash
bash scripts/run_eval_pr_gate.sh
```

预期：所有子 gate 通过，RAG gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`。

- [ ] **步骤 3：运行周期性 gate**

```bash
bash scripts/run_eval_periodic.sh
```

预期：所有子 gate 通过，RAG gate 输出 `cases=10 passed=10 failed=0` 和 `Gate passed`。

- [ ] **步骤 4：运行全量测试**

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

## 提交边界

- 设计阶段：`docs(评测): 设计 RAG fixture 正例门禁`（已完成：`6cbce35`）。
- 实现计划：`docs(计划): 记录 RAG fixture 正例计划`。
- 任务 1：`feat(评测): 增加 RAG fixture 正例数据`。
- 任务 2：`feat(评测): 支持 RAG fixture 门禁入口`。
- 任务 3：`test(评测): 固化 RAG fixture 正例门禁`。
- 任务 4：`docs(评测): 收口 RAG fixture 正例状态`。
