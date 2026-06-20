# EvalCandidate 候选仲裁状态设计

> 2026-06-20 · 将通用评测候选从「标注 / 忽略 / 晋升」扩展为可运营的 `reject / defer / reopen` 决策流，用于承接真实样本运营动作。

## 背景

真实样本运营已经完成 3 个前置阶段：

- TimingGate 信号周期审计已经接入周期脚本和报告归档。
- RAG generated → manual 已有单条 dry-run / apply 仲裁入口。
- 通用 `EvalCandidate` 已具备 readiness、summary、批量 preflight、CLI dry-run 聚合和 WebUI 当前页预检。

当前缺口不在「能不能晋升」，而在「不晋升的样本为什么不晋升」。现有 `ignored` 只能表示粗粒度忽略，无法区分永久拒绝、暂缓处理、等待更多样本、目标数据集暂不接收或人工标注争议。后续批次审计和趋势报表也需要这些运营状态作为解释维度。

## 目标

本阶段新增通用 `EvalCandidate` 的候选仲裁语义：

1. 支持 `rejected` 和 `deferred` 两个状态。
2. 提供显式动作接口：`reject`、`defer`、`reopen`。
3. 每个动作记录统一审计 detail，包括前后状态、原因码、备注和暂缓期限。
4. WebUI 可以筛选、执行和查看这些运营动作。
5. CLI 至少能按新状态导出，方便离线复核。
6. 文档明确 RAG benchmark 与通用候选队列的边界。

## 非目标

- 不改数据库 schema。首轮复用 `EvalCandidate.status` 和 `note`，结构化审计写入 `AdminAuditLog.detail_json`。
- 不做批量 apply、批量 reject 或批量 defer。
- 不重写 readiness、summary、preflight 或 promote 规则。
- 不把 RAG generated / manual case 并入通用 `eval_candidates`。
- 不自动更新 `evals/baselines/*`。
- 不做趋势报表或阈值调参。

## 状态机

候选状态继续使用 `EvalCandidate.status` 字符串字段。

| 状态 | 含义 | 允许动作 |
|------|------|----------|
| `candidate` | 等待人工标注的候选 | `label`、`ignore`、`reject`、`defer` |
| `labeled` | 已补齐可评分 `expected` | `promote`、`reject`、`defer` |
| `ignored` | 旧的轻量忽略状态 | `reopen` |
| `deferred` | 暂缓处理，后续可以重新进入候选流 | `reopen`、`reject` |
| `rejected` | 人工确认不进入稳定样本 | `reopen` |
| `promoted` | 已写入目标 eval dataset | 无运营动作 |

关键约束：

- `PATCH /evals/candidates/{case_id}` 仍不能直接写入 `labeled`、`promoted`、`deferred` 或 `rejected`。
- `label_candidate()` 只允许从 `candidate` 或 `deferred` 进入 `labeled`，避免对 `ignored`、`rejected`、`promoted` 直接打标。
- `ignore_candidate()` 只保留旧的轻量忽略能力，允许从 `candidate` 或 `labeled` 进入 `ignored`。
- `reject_candidate()` 允许从 `candidate`、`labeled`、`deferred` 或 `ignored` 进入 `rejected`。
- `defer_candidate()` 允许从 `candidate` 或 `labeled` 进入 `deferred`。
- `reopen_candidate()` 允许从 `ignored`、`deferred` 或 `rejected` 回到 `candidate`。
- `promoted` 是终态，本阶段不提供 reopen。

## 原因码

首轮原因码采用固定白名单，避免自由文本难以聚合。

Reject 原因：

- `duplicate`：与已有 case 重复。
- `low_value`：样本价值低，不值得进入稳定集。
- `unsafe_or_sensitive`：包含不适合沉淀的敏感内容。
- `not_reproducible`：无法稳定复现。
- `out_of_scope`：不属于当前评测范围。
- `bad_sample`：采样质量差或上下文不足。

Defer 原因：

- `needs_more_context`：需要更多上下文才能判断。
- `needs_batch_review`：等待批次仲裁。
- `waiting_for_baseline`：等待目标 baseline 或 dataset 策略。
- `needs_product_decision`：需要产品或运营决策。
- `temporary_blocker`：临时阻断，后续可复查。

Reopen 原因：

- `new_evidence`：出现新证据。
- `operator_correction`：人工纠正误操作。
- `defer_expired`：暂缓到期后复开。
- `needs_relabel`：需要重新标注。

后端允许 `reason_code` 为空时使用 `unspecified`，但 WebUI 默认要求用户选择原因码。

## API 设计

新增 3 个显式动作端点：

```http
POST /api/v1/admin/evals/candidates/{case_id}/reject
POST /api/v1/admin/evals/candidates/{case_id}/defer
POST /api/v1/admin/evals/candidates/{case_id}/reopen
```

请求体：

```json
{
  "reason_code": "low_value",
  "note": "样本只是普通寒暄，不进入稳定集",
  "defer_until": "2026-06-30"
}
```

字段规则：

- `reason_code`：可选字符串，超过 64 字符则拒绝。
- `note`：可选字符串，后端裁剪到 1000 字符。
- `defer_until`：仅 `defer` 使用，可选 ISO 日期或日期时间字符串；本阶段只记录到审计 detail，不参与自动复开。

响应体复用 `_candidate_dict()`，包含最新 `status`、`note` 和 `readiness`。

审计 detail 统一为：

```json
{
  "before_status": "candidate",
  "after_status": "deferred",
  "reason_code": "needs_more_context",
  "note": "缺少后续回复，等下一轮样本",
  "defer_until": "2026-06-30"
}
```

动作名：

- `reject_candidate`
- `defer_candidate`
- `reopen_candidate`

## Store 设计

`core/eval_sampling/store.py` 新增：

- 状态常量：`ACTIVE_CANDIDATE_STATUSES`、`REVIEW_HOLD_STATUSES` 等最小集合。
- 原因码常量：`REJECT_REASON_CODES`、`DEFER_REASON_CODES`、`REOPEN_REASON_CODES`。
- `reject_candidate(db, case_id, reason_code="", note="")`。
- `defer_candidate(db, case_id, reason_code="", note="", defer_until="")`。
- `reopen_candidate(db, case_id, reason_code="", note="")`。

函数返回：

```python
{
    "candidate": _candidate_dict(row),
    "audit": {
        "before_status": before,
        "after_status": row.status,
        "reason_code": normalized_reason,
        "note": normalized_note,
        "defer_until": normalized_defer_until,
    },
}
```

这样 API 层可以复用同一份审计 payload，测试也能直接断言 store 行为。

## WebUI 设计

`EvalsPage.jsx` 做最小改动：

- 状态筛选增加 `deferred` 和 `rejected`。
- 状态 badge 增加 `deferred` 与 `rejected` 的颜色区分。
- `candidate` 和 `labeled` 行增加「暂缓」「拒绝」按钮。
- `ignored`、`deferred`、`rejected` 行增加「复开」按钮。
- 使用一个小 modal 输入原因码、备注和可选暂缓期限。
- 成功后关闭 modal，刷新候选列表和详情。

WebUI 不做批量操作，不改 promote modal，不改 preflight 行为。

## CLI 设计

首轮不新增批量决策子命令。现有：

```bash
python -m evals.candidates export --status deferred --out /tmp/deferred.jsonl
python -m evals.candidates export --status rejected --out /tmp/rejected.jsonl
```

即可满足离线复核。后续如果需要批量导入 reject/defer，再单独设计 `import-decisions`，避免本阶段扩大 blast radius。

## RAG 边界

RAG benchmark 继续保持独立：

- generated case 位于 `tmp/rag_benchmark/generated`，不是持久 review queue。
- generated → manual 的单条 promote 已由 RAG Admin API 处理。
- 本阶段不为 RAG generated 增加 `rejected/deferred` 状态。
- 如需记录 RAG generated 的拒绝原因，后续应设计 sidecar 或 batch audit，不把它写进通用 `EvalCandidate`。

## 测试计划

后端：

- `candidate -> rejected` 成功，返回审计 payload。
- `candidate/labeled -> deferred` 成功，记录 `defer_until`。
- `deferred/rejected/ignored -> candidate` 成功。
- `promoted -> reject/defer/reopen` 被拒绝。
- `label_candidate()` 拒绝从 `ignored/rejected/promoted` 直接打标。
- Admin 端点写入统一 `AdminAuditLog.detail_json`。

CLI：

- `export --status deferred` 和 `export --status rejected` 能导出对应候选。

WebUI 静态守卫：

- 状态筛选包含 `deferred` 和 `rejected`。
- 源码包含 `/reject`、`/defer`、`/reopen` 调用。
- 存在原因码、备注和 `defer_until` 控件。

验证命令：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py tests/test_webui_admin_redesign.py -q -p no:cacheprovider
npm --prefix webui run build
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 后续阶段

完成本阶段后，下一步才适合推进：

- 人工仲裁批次审计。
- 真实样本趋势报表。
- RAG generated reject / defer sidecar。
- `defer_until` 自动复开或到期提醒。
