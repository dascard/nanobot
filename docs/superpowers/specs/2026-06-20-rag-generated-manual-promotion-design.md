# RAG Generated Case 提升为 Manual Case 设计

日期：2026-06-20

## 背景

RAG benchmark 已具备完整的 generated/manual 分层：

- `POST /api/v1/admin/rag/benchmark/sample` 从真实 SQLite DB 只读采样 generated case。
- `GET /api/v1/admin/rag/benchmark/cases` 同时列出 manual 和 generated case，并标记 generated 是否 stale。
- `GET /api/v1/admin/rag/benchmark/cases/{case_id}` 能查看 case 详情。
- `PUT /api/v1/admin/rag/benchmark/cases/{case_id}` 能保存 manual case，并写入审计记录。
- WebUI 已能刷新 generated、查看 generated、编辑 manual、运行 benchmark 和展示 gate。

当前缺口是人工仲裁链路不顺滑。文档已经约定 generated case 只作为本地 DB 采样候选，不进入稳定 baseline；人工确认后的样本应保存为 manual case。但目前用户只能手动复制 generated JSON，再用 manual 编辑器保存，没有 dry-run 预检、目标路径确认、冲突提示和来源记录。

## 目标

- 新增单条 generated case 提升为 manual case 的 Admin API。
- 支持 dry-run 和 apply 两阶段，延续现有 promote 预检习惯。
- apply 时写入 manual JSON 文件，并记录 Admin audit。
- 保留 generated case 的关键 provenance：原 case id、原 origin、`db_fingerprint`、`generated_at`、`generator_version` 和人工 note。
- WebUI 对 generated case 提供“提升为 Manual”入口，先 dry-run 展示目标 id/path，再二次确认 apply。
- 文档明确：promote 不自动更新 baseline，不提交 `tmp/` generated 产物，stale generated 需刷新后再提升。

## 非目标

- 不新增数据库表或长期仲裁队列。
- 不做批量 accept/reject/defer。
- 不自动更新 `evals/baselines/rag_benchmark.json`。
- 不修改 `evals.rag_benchmark.run` 的稳定 gate 行为。
- 不修改 sampler、retriever、adapter、scoring 或 report 格式。
- 不提交真实 DB 派生的 manual case。
- 不把 generated case 纳入 PR gate。

## 推荐方案

采用“单条 promote API + WebUI 二次确认”的最小闭环。

理由：

- 当前 generated 列表已经是可用的候选清单，无需先引入新表。
- 当前 manual 保存逻辑已经有写文件、备份和 audit，本阶段只补 generated → manual 的安全桥。
- dry-run/apply 便于测试，也符合现有评测候选 promote 的产品习惯。
- 文件范围小，主要集中在 `api/admin/rag_benchmark_routes.py`、`tests/test_rag_benchmark_admin.py`、`webui/src/features/rag/RagBenchmarkPage.jsx`、`tests/test_rag_benchmark_webui.py` 和 `docs/evals.md`。

## API 设计

新增请求模型：

```python
class BenchmarkCasePromoteRequest(BaseModel):
    target_case_id: str = ""
    dry_run: bool = True
    note: str = ""
    overwrite: bool = False
```

新增接口：

```http
POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual
```

请求示例：

```json
{
  "target_case_id": "memory_manual_exact_40",
  "dry_run": true,
  "note": "人工确认该 generated case 可进入 manual",
  "overwrite": false
}
```

响应示例：

```json
{
  "ok": true,
  "dry_run": true,
  "source_case_id": "memory_generated_exact_40",
  "target_case_id": "memory_manual_exact_40",
  "path": "evals/cases/rag_benchmark/manual/memory_manual_exact_40.json",
  "baseline_update_required": true,
  "case": {
    "id": "memory_manual_exact_40",
    "suite": "rag_benchmark",
    "source_type": "memory",
    "case_type": "positive",
    "status": "enabled",
    "query": "...",
    "filters": {},
    "expected": {
      "candidate_ids": ["memory_digest:42:digest:level2"],
      "hit_at": 5
    },
    "meta": {
      "origin": "manual",
      "promoted_from_case_id": "memory_generated_exact_40",
      "promoted_from_origin": "generated_exact",
      "promoted_at": "2026-06-20T12:00:00",
      "review_note": "人工确认该 generated case 可进入 manual",
      "db_fingerprint": {},
      "generated_at": "2026-06-20T11:50:00",
      "generator_version": "rag_benchmark:v1"
    }
  }
}
```

规则：

- 只允许提升 `origin=generated` 的 case；manual case 调用返回 `409`。
- 默认 `target_case_id` 为空时使用源 case id。
- `target_case_id` 必须复用 `_case_id_or_400()` 校验。
- stale generated 返回 `409`，提示先刷新 generated。
- `overwrite=false` 且目标 manual 文件存在时返回 `409`。
- `dry_run=true` 不写文件、不写 audit，只返回将要写入的 case、path 和 `baseline_update_required=true`。
- `dry_run=false` 写入 `BENCHMARK_MANUAL_DIR/{target_case_id}.json`，若 overwrite 覆盖已有 manual，则复用现有 backup 目录保存旧文件。
- apply 写 audit action：`promote_rag_benchmark_generated_case`。
- apply 不更新 baseline，只返回 `baseline_update_required=true`。

## Case 转换规则

输入是 generated `BenchmarkCase`。输出 manual `BenchmarkCase`：

- `id` 改为 `target_case_id`。
- `suite`、`source_type`、`case_type`、`status`、`query`、`filters` 和 `expected` 保持源 case 内容。
- `meta.origin` 强制为 `manual`。
- `meta.promoted_from_case_id` 写源 case id。
- `meta.promoted_from_origin` 写源 case 原始 `meta.origin`，通常为 `generated_exact`。
- `meta.promoted_at` 写当前时间。
- `meta.review_note` 写请求 `note`，为空时写空字符串。
- 保留源 case 的 `db_fingerprint`、`generated_at`、`generator_version`、`sensitivity`、`source_table` 和 `source_id` 等 metadata。

不创建 disabled draft。原因是当前 CLI stable gate 不按 `status` 过滤；把草稿写进 manual 目录可能被稳定 gate 运行。本阶段只创建用户明确 apply 的 enabled manual case。

## WebUI 设计

在 RAG Benchmark 页中：

- generated case 的详情弹窗增加“提升为 Manual”按钮。
- manual 目录不可写或 generated case stale 时按钮禁用，并显示原因。
- 点击后先调用 dry-run，展示目标 `case_id`、目标 path 和生成后的 case JSON 摘要。
- 用户确认后调用 apply。
- apply 成功后刷新 case 列表和 status，并打开或保留 manual case 详情。

最小实现可以放在现有 `CaseEditor` 内，不拆新组件；避免扩大前端重构范围。

## 测试策略

后端 TDD：

- 新增 dry-run 测试：从 generated case dry-run promote，返回目标 case/path，manual 文件不存在。
- 新增 apply 测试：写入 manual JSON，保留 query/filters/expected，写 provenance meta 和 audit。
- 新增冲突测试：目标 manual 已存在且 `overwrite=false` 返回 `409`。
- 新增 stale 测试：generated fingerprint 与当前 DB 不同返回 `409`。
- 新增 manual 源测试：manual case 调用 promote 返回 `409`。
- 新增 unsafe target id 测试：返回 `400`。

前端静态测试：

- `RagBenchmarkPage.jsx` 包含“提升为 Manual”文案。
- 包含 `promote-manual` API 路径。
- 包含 dry-run/apply 两阶段状态或请求字段。
- 包含 stale/manual unwritable 禁用逻辑提示。

验证命令：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py -q -p no:cacheprovider
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -q -p no:cacheprovider
npm --prefix webui run build
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 风险与约束

- Generated case 来自本地真实 DB，可能包含敏感内容；本阶段不提交 `tmp/rag_benchmark/generated/*`。
- Promote 只是把人工确认的 case 写入 manual 目录，不代表自动进入稳定 baseline。
- 后续若要批量仲裁、reject/defer、长期队列或趋势统计，应另起阶段并考虑 DB schema。
- 后续若要支持 disabled draft，必须先确认 CLI 和 Web runner 是否统一跳过 disabled case。
- 如果目标 manual case 决定纳入稳定 gate，需要另行同步 `evals/baselines/rag_benchmark.json` 并运行 stable gate。

## 验收清单

- 后端 API 支持 dry-run/apply，且错误分支覆盖冲突、stale、manual 源和 unsafe id。
- apply 写入 manual JSON 并记录 `promote_rag_benchmark_generated_case` audit。
- WebUI generated case 可触发 dry-run 和 apply，manual 不显示该按钮。
- 文档说明 generated → manual 的人工仲裁规则和 baseline 边界。
- 不修改 RAG sampler、runner、scoring、fixture、baseline 和 gate 脚本。
- 定向测试、相邻回归、WebUI build 和全量测试均有新鲜验证结果。
