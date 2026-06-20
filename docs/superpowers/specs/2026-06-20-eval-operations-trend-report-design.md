# EvalCandidate 运营趋势报表设计

日期：2026-06-20

## 背景

路线项 8 的稳定门禁、周期性复跑、RAG fixture 正例、候选 readiness、单条仲裁和批次审计已经完成。当前剩余运营问题不是继续增加状态按钮，而是让维护者能快速回答：

- 最近候选样本是否在积压？
- 当前可晋升样本和 blocked 样本分别有多少？
- 哪些 suite、source 或阻断原因占比最高？
- 哪些日期产生了异常多的候选？

子系统审计确认，周期评测 artifact、RAG benchmark、TimingSignal 审计和通用 `EvalCandidate` 都能提供趋势信号，但它们的语义不同。第一版先实现通用 `EvalCandidate` 运营趋势报表，不把 RAG generated/manual、TimingSignal shadow mismatch、通用 eval pass_rate 和 RAG hit@5 混入同一张表。评测 artifact 趋势需要统一 manifest 后再做，避免依赖 `latest.json` 覆盖语义或 shell stdout。

## 目标

第一版提供只读 `EvalCandidate` 运营趋势报表：

- 按 `EvalCandidate.created_at` 做日粒度分桶。
- 在每个日期桶内聚合当前 `status`、`suite`、`source`、readiness 和 blocking reason。
- 返回当前过滤范围的总体 summary，复用现有 readiness 规则。
- 提供 Admin API、CLI 和 WebUI 最小入口。
- 明确报表语义是“按创建日期分桶 + 当前状态快照”，不是历史状态迁移回放。

## 非目标

- 不新增数据库表或 migration。
- 不修改 `EvalCandidate` 状态机。
- 不新增批量 reject、defer、reopen、promote 或 batch apply。
- 不自动更新 baseline，不改 TimingGate、RAG 或 capability gate 阈值。
- 不解析 shell stdout。
- 不把 RAG benchmark generated/manual case 并入通用候选趋势。
- 不把通用 eval `pass_rate`、RAG `hit@5`、TimingSignal `false_positive_rate` 混成同一质量曲线。

## 数据口径

### 候选快照

数据源是 `eval_candidates` 表：

- `case_id`
- `suite`
- `source`
- `status`
- `priority`
- `created_at`
- `updated_at`
- `expected_json`
- `tags_json`

`status` 表示候选当前状态。某个候选如果 6 月 18 日创建、6 月 20 日被 reject，在 30 天趋势里仍会落在 6 月 18 日桶内，并在该桶的 `by_status.rejected` 中计数。这是“创建日期维度的当前状态快照”，不是“6 月 20 日发生了一次 reject”。

### Readiness

readiness 由 `candidate_readiness()` 实时派生，受以下因素影响：

- 当前候选状态是否为 `labeled`。
- suite 是否可运行。
- `expected` 是否满足 scorer 契约。
- `target_dataset` 是否安全。
- 目标 case 文件是否已经存在。

因此 readiness 不是历史字段，也不能倒推候选创建当天的 ready / blocked 状态。报表只展示当前重新计算结果。

### Blocking Reason

第一版按现有阻断原因聚合：

- `candidate_not_found`
- `target_dataset_invalid`
- `invalid_status`
- `suite_not_runnable`
- `expected_invalid`
- `target_case_exists`

每个日期桶输出 `top_blocking_reasons`，格式与现有候选 summary 保持一致：

```json
[
  {"code": "invalid_status", "count": 12}
]
```

## 接口设计

### Store 函数

新增只读函数：

```python
candidate_trend_report(
    db,
    *,
    days: int = 30,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]
```

行为：

- `days` 限制在 `1..90`。
- 查询 `created_at >= today - (days - 1)` 的候选。
- 支持 `suite`、`status`、`source` 过滤。
- `target_dataset` 传入 `candidate_readiness()`；为空时按候选自身 suite 计算。
- 不写 `AdminAuditLog`。
- 不修改候选。

响应结构：

```json
{
  "ok": true,
  "filters": {
    "days": 30,
    "bucket": "day",
    "suite": "timing_gate",
    "status": "",
    "source": "",
    "target_dataset": "timing_gate"
  },
  "summary": {
    "total": 12,
    "by_status": {},
    "by_suite": {},
    "by_source": {},
    "readiness": {"ready": 2, "blocked": 10},
    "top_blocking_reasons": []
  },
  "buckets": [
    {
      "date": "2026-06-20",
      "created": 3,
      "by_status": {"candidate": 2, "labeled": 1},
      "by_suite": {"timing_gate": 3},
      "by_source": {"db": 3},
      "readiness": {"ready": 1, "blocked": 2},
      "top_blocking_reasons": [{"code": "invalid_status", "count": 2}]
    }
  ]
}
```

### Admin API

新增只读接口：

```http
GET /api/v1/admin/evals/candidates/trend
```

查询参数：

- `days`: 默认 `30`，范围 `1..90`。
- `suite`: 可选。
- `status`: 可选。
- `source`: 可选。
- `target_dataset`: 可选。

路由必须定义在 `/api/v1/admin/evals/candidates/{case_id}` 之前，避免 `trend` 被动态路由吞掉。

### CLI

新增子命令：

```bash
python -m evals.candidates trend \
  --days 30 \
  --suite timing_gate \
  --status labeled \
  --source db \
  --target-dataset timing_gate \
  --out /tmp/candidate-trend.json
```

CLI 使用同一个 store 函数，默认写 JSON 到 stdout；传 `--out` 时写入文件并打印摘要。

### WebUI

在 Eval 评测页新增「趋势报表」tab：

- 复用候选页的 suite / status / source 过滤器。
- 新增 `days` 输入，默认 30。
- 顶部显示 `total`、`ready`、`blocked`、今日新增。
- 表格展示日期、created、状态分布、ready / blocked、top blocking reason。
- 展示完整 JSON payload，方便运营人员复制和排查。

第一版不引入图表库。表格能满足当前运营扫描需求，后续如果要做折线图，再基于稳定 JSON 结构扩展。

## 测试计划

### 后端契约

新增 `tests/test_eval_candidate_contract.py` 用例：

- `candidate_trend_report()` 按日期分桶。
- `suite`、`status`、`source`、`target_dataset` 过滤生效。
- readiness 和 top blocking reasons 使用现有规则。
- API 返回结构稳定。
- 调用趋势接口不写 `AdminAuditLog`，不修改候选状态。

### CLI

新增 `tests/test_eval_candidates_cli.py` 用例：

- `python -m evals.candidates trend --out <path>` 写出 JSON。
- stdout 摘要包含 total、ready、blocked。
- CLI 与 API / store 响应字段一致。

### WebUI

新增 `tests/test_webui_admin_redesign.py` 静态用例：

- 页面包含「趋势报表」tab。
- 调用 `/evals/candidates/trend`。
- 展示 `by_status`、`by_source`、`top_blocking_reasons`。
- 保持无批量拒绝、批量暂缓、批量应用入口。

## 后续扩展

评测 artifact 趋势单独设计，不进入本阶段。后续应先补周期运行 manifest，再解析 JSON artifact：

- `stable_eval_gate`：通用 eval suite 的 `pass_rate`、`gate.passed`、new / fixed / still failed cases。
- `rag_stable_gate`：`provider_mode=deterministic`、`case_scope=manual+fixture` 的 RAG 稳定门禁趋势。
- `rag_real_sample`：包含 generated case 的真实样本观察趋势，按 `provider_mode`、`generator_version`、`db_fingerprint` 隔离。
- `timing_signal_audit`：真实 ambient 日志信号审计趋势，区分 shadow mismatch 和人工误判率。

这些 metric family 必须分线展示，不能合并为单一通过率。
