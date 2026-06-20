# EvalCandidate 人工仲裁批次审计设计

日期：2026-06-20

## 背景

通用 `EvalCandidate` 队列已经具备单条标注、晋升预检、拒绝、暂缓和复开能力。当前缺口不是批量修改状态，而是把一次人工批次复核的输入范围、候选快照、人工结论和阻断原因沉淀为可追溯记录。

批次审计优先服务真实样本运营：操作者可以在同一批候选上记录「已看过哪些样本」「这些样本当时处于什么状态」「哪些 readiness 阻断存在」「人工倾向是什么」。后续真实样本趋势报表可以复用这些审计记录，而不是从零散单条操作中猜测一次复核的上下文。

## 目标

- 新增只读批次计划能力，按 `case_ids` 或明确过滤条件生成候选批次审计快照。
- 新增批次审计记录能力，写入一条 `AdminAuditLog`，记录批次级 JSON detail。
- 新增 Admin API：`POST /api/v1/admin/evals/candidates/batch-audit`。
- 新增 CLI 只读命令：`python -m evals.candidates audit`，用于本地导出同一套报告。
- WebUI 候选页新增「批次审计」只读弹窗，复用当前页候选和 preflight 结果展示审计快照。
- 文档同步 `docs/evals.md`、`docs/todo.md` 和 `docs/plan_walkthrough.md`。

## 非目标

- 不新增数据库表，不修改 `EvalCandidate` schema。
- 不实现批量 `reject`、`defer`、`reopen`、`label` 或 `promote`。
- 不把审计 decision 解释为状态变更；状态变更继续使用已有单条端点。
- 不自动更新 RAG baseline、TimingGate 阈值或 eval case 文件。
- 不把 RAG benchmark generated / manual case 并入通用 `EvalCandidate`。
- 不把 batch audit 纳入 PR fail-fast gate。

## 数据边界

第一版复用 `AdminAuditLog`：

- `action`: `audit_eval_candidate_batch`
- `target_type`: `eval_candidate_batch`
- `target_id`: 稳定生成的 `batch_<timestamp>_<hash>`
- `detail_json`: 批次审计详情

`detail_json` 包含：

- `batch_id`
- `filters`: `case_ids`、`suite`、`status`、`source`、`target_dataset`、`limit`
- `batch_note`
- `total`、`ready`、`blocked`
- `counts.by_status`
- `counts.by_suite`
- `counts.by_source`
- `counts.by_decision`
- `counts.by_reason_code`
- `counts.by_blocking_reason`
- `items`: 每条候选的 `case_id`、`exists`、`suite`、`source`、`before_status`、`priority`、`decision`、`reason_code`、`note`、`defer_until`、`readiness`、`errors`

`AdminAuditLog.detail_json` 是 Text JSON，不适合高频结构化查询。后续如果趋势报表需要按 `reason_code`、`batch_id`、`case_id` 高效聚合，应另行设计 `eval_candidate_batch_audits` 与 `eval_candidate_batch_items` 表。

## 请求模型

Admin API 请求体：

```json
{
  "dry_run": true,
  "case_ids": ["cand_1", "cand_2"],
  "suite": "",
  "status": "",
  "source": "",
  "target_dataset": "timing_gate",
  "limit": 200,
  "batch_note": "2026-06-20 人工复核第一批",
  "decisions": [
    {
      "case_id": "cand_1",
      "decision": "needs_label",
      "reason_code": "unspecified",
      "note": "可进入标注",
      "defer_until": "",
      "expected_status": "candidate",
      "expected_updated_at": "2026-06-20 12:00:00"
    }
  ]
}
```

字段规则：

- `dry_run=true` 只生成计划，不写审计日志。
- `dry_run=false` 重新生成计划并写一条 `AdminAuditLog`。
- `case_ids` 优先于过滤条件；传入 `case_ids` 时保持输入顺序。
- 未传 `case_ids` 时，必须至少提供 `suite`、`status` 或 `source` 之一，避免误审全量。
- `limit` 范围为 1-500。
- `batch_note` 最多保留 1000 字符。
- `decisions` 是人工审计结论，不触发状态变更。

支持的 `decision`：

- `noop`: 已查看，无进一步动作。
- `needs_label`: 建议后续标注。
- `promote_ready`: 建议后续晋升，但必须由 promote dry-run / apply 执行。
- `reject`: 建议单条拒绝。
- `defer`: 建议单条暂缓。
- `reopen`: 建议单条复开。

原因码规则复用现有单条仲裁集合：

- `reject` 只能使用 reject 原因码。
- `defer` 只能使用 defer 原因码，允许 `defer_until`。
- `reopen` 只能使用 reopen 原因码。
- `noop`、`needs_label`、`promote_ready` 默认使用 `unspecified`，不允许 `defer_until`。

## 响应模型

```json
{
  "ok": true,
  "dry_run": true,
  "batch_id": "batch_20260620_ab12cd34",
  "audit_log_id": null,
  "total": 2,
  "ready": 1,
  "blocked": 1,
  "counts": {
    "by_status": {"candidate": 2},
    "by_suite": {"timing_gate": 2},
    "by_source": {"db": 2},
    "by_decision": {"needs_label": 1, "defer": 1},
    "by_reason_code": {"unspecified": 1, "needs_batch_review": 1},
    "by_blocking_reason": {"invalid_status": 1}
  },
  "items": [
    {
      "case_id": "cand_1",
      "exists": true,
      "suite": "timing_gate",
      "source": "db",
      "before_status": "candidate",
      "decision": "needs_label",
      "reason_code": "unspecified",
      "readiness": {"ready": false, "blocking_reasons": []},
      "errors": []
    }
  ]
}
```

`ok=false` 表示本批次存在缺失候选、状态漂移、非法 decision 或其他逐项错误。`dry_run=false` 时只要 `ok=false`，接口返回 400 且不写 `AdminAuditLog`。

## 后端设计

新增 store 层函数：

- `plan_candidate_batch_audit(...) -> dict`
  - 只读。
  - 复用 `preflight_candidate_promotions()` 的候选排序和 readiness 计算。
  - 对每条 candidate 合并人工 decision、readiness 和错误。
  - 生成 counts 与 batch id。
- `record_candidate_batch_audit(db, plan, *, ip_address="") -> dict`
  - 不使用会吞异常的 `_audit()` helper。
  - 直接写入 `AdminAuditLog`，失败时向上抛错。
  - 不修改 `EvalCandidate`。

Admin API：

- `POST /api/v1/admin/evals/candidates/batch-audit`
  - `dry_run=true`: 返回 plan。
  - `dry_run=false`: plan 必须 `ok=true`，写审计日志后返回 `audit_log_id`。
  - 非法请求返回 400；未授权沿用 admin token 401 / 503。

## CLI 设计

新增：

```bash
python -m evals.candidates audit --suite timing_gate --status candidate --target-dataset timing_gate --out /tmp/candidate-audit.json
```

CLI 行为：

- 默认只读，不写 `AdminAuditLog`。
- 输出和 Admin dry-run 相同的 JSON 结构。
- `--out` 存在时写 JSON 文件；否则打印到 stdout。
- 不提供 `--apply`。

## WebUI 设计

候选页新增「批次审计」按钮，放在 summary 区域，和「预检当前页」并列。

弹窗内容：

- 当前页 `total`、`ready`、`blocked`。
- `counts` 聚合。
- `items` JSON 明细。
- 只提供关闭和刷新，不提供批量提交按钮。

WebUI 第一版可以复用 `/evals/candidates/preflight` 结果生成只读审计视图；后续如果需要把 WebUI 批次审计写入 `AdminAuditLog`，再接入 `/batch-audit` 的 `dry_run=false`。

## 测试策略

后端：

- `plan_candidate_batch_audit()` dry-run 不写 `AdminAuditLog`，不改候选状态。
- `record_candidate_batch_audit()` 写入一条 `AdminAuditLog`，detail 包含批次范围、counts、items 和 readiness 阻断码。
- `dry_run=false` 遇 missing case、重复 `case_ids`、未知 decision、非法 reason code 或状态漂移时拒绝且不写日志。
- 混合 ready / blocked 候选应返回完整 items，不因第一条 blocked 中断。

CLI：

- `audit` 子命令输出 `summary`、`counts`、`items`、`readiness.blocking_reasons`。
- 执行后候选状态不变，目标 case 文件不存在。

WebUI：

- 静态测试守住「批次审计」按钮、`/evals/candidates/preflight`、`top_blocking_reasons`、`blocking_reasons`。
- 静态测试守住不出现批量写入路径或文案：`/evals/candidates/batch-triage`、`批量拒绝`、`批量暂缓`、`批量应用`。

最终验证：

- 定向后端测试。
- CLI 测试。
- WebUI 静态测试。
- WebUI build。
- 全量 `python -B -m pytest tests/ -q -p no:cacheprovider`。

## 风险与后续

- 用 `AdminAuditLog.detail_json` 保存批次明细会增加单行 JSON 体积；第一版限制 `limit <= 500`。
- 审计记录不是工作流状态，不能表达「批次处理中」「多人领取」「复核完成」等协作状态。
- 如果后续要做真实样本趋势报表，应优先读取 batch audit detail 与单条 triage audit，再决定是否需要结构化 batch 表。
- 如果后续要做批量状态变更，应新增独立 `batch-triage` 设计，强制 all-or-nothing，并复用单条状态机函数。
