# P4-1 评测数据集与标注闭环设计

> 2026-06-18 · P4-1 设计。目标是把已有 `evals/`、`eval_candidates` 和候选采样能力收敛成可审阅、可标注、可安全晋升的最小闭环。

---

## 一、背景

P3-3 已完成 TimingGate 的稳定 baseline、门禁脚本和 CI workflow。P4-1 的目标不再是重复 TimingGate 门禁，而是把评测体系从“已有 runner + 零散 case”推进到“候选样本可以被人工标注并晋升为正式 case”。

现有基础能力已经存在：

- `evals/run.py` 能加载 case、分发 runner、生成 `SuiteReport`，并支持 baseline diff / gate。
- `evals/cases/candidates/` 默认不进入正式 eval，避免未标注样本污染门禁。
- `core/eval_sampling/*` 已能把日志和数据库样本写入 `eval_candidates` 表。
- Admin 已有候选列表、标注、忽略和提升入口，WebUI 也有候选页面。
- RAG benchmark 已有独立 `BenchmarkCase` / `BenchmarkResult` / `CaseScore` 体系，不走通用 `EvalCase`。

只读审计发现三个需要先收口的缺口：

- WebUI 标注请求发送 `expected_json`，后端 `LabelRequest` 只读取 `expected`，导致人工填写的 expected 可能被丢弃。
- `promote_candidate()` 只拒绝 `needs_label=true`，没有拒绝空 expected 或不可评分 expected，空 case 可能被提升后自然通过。
- 通用 promote 固定写入 `evals/cases/regression/`，没有明确区分“runner suite”和“case dataset”，不利于 per-capability 数据集扩展。

## 二、目标

P4-1 分成契约修复、离线闭环、数据集扩展三个阶段推进。

第一阶段必须先保证现有候选不会被错误标注或错误晋升：

- 后端标注接口兼容并规范 `expected` 字段，前端改为发送 `expected`。
- `label_candidate()` 和 `promote_candidate()` 拒绝空 expected、`needs_label` 和没有可评分断言的 expected。
- 明确 expected 的可评分 key 清单，并为历史未评分 key 建立显式处理策略。
- promote 支持明确目标 dataset，默认保持向后兼容，后续由 CLI 或 Admin 参数指定目标。

第二阶段打通离线标注闭环：

- 支持从 `eval_candidates` 导出 JSONL 给人工标注。
- 支持导入 labels JSONL，按 `case_id` 写回 expected、note 和状态。
- 支持 promote dry-run，提前发现空 expected、未知 key、目标文件冲突和 runner 不存在。
- 支持 promote apply，写出正式 EvalCase，并保留来源 metadata。

第三阶段扩展首批 per-capability 数据集：

- 首批只纳入确定性、离线可运行能力：`timing_gate`、`model_routing`、`reply_contract/group_reply`。
- RAG benchmark 保持现有独立入口，P4-1 只记录边界和复用规则，不把 runtime provider 放入通用 PR gate。
- Sticker、memory learning、moderation 可以继续使用现有 regression case；批量扩展放在候选闭环稳定后。

## 三、非目标

- 不重做 P3-3A 的 TimingGate 信号审计复跑。
- 不重做 P3-3B 的 TimingGate baseline、脚本和 CI workflow。
- 不调整 `core/timing_score.py` 阈值或权重。
- 不在 P4-1 第一阶段新增大型 Admin 标注工作台。
- 不把真实 ChatLog 明文样本提交到仓库。
- 不把 RAG runtime provider 或真实 DB 采样放入 PR 必跑门禁。
- 不一次性引入全新的 eval runner registry；现阶段保留 `run_case()` 的显式分发。

## 四、方案选择

### 方案 A：只修现有 Admin bug

修复 `expected_json` / `expected` 字段错配和空 expected promote。优点是快；缺点是仍没有离线 labels 导入、dry-run 和数据集边界，P4-1 主目标没有闭环。

### 方案 B：契约优先的离线闭环

先修 Admin / store 的硬缺口，再补 expected 契约、labels JSONL、promote dry-run / apply 和数据集目标参数。优点是能形成最小闭环，且每一步都可测试、可单独提交；缺点是需要触碰 `store.py`、Admin API、WebUI、eval contract 和文档。

### 方案 C：完整标注平台

同时做 Admin 标注表单、多人仲裁、批量操作、RAG 候选审阅和 CI suite 扩容。优点是最终体验完整；缺点是范围过大，会把数据契约、UI、运行历史和门禁策略混在一起。

推荐方案 B。它先修会造成错误数据的缺口，再建立离线闭环；Admin UI 增强和更多 suite gate 作为 P4 后续阶段推进。

## 五、数据契约

### Candidate 记录

数据库里的 `eval_candidates` 继续作为候选源。候选的核心字段为：

```json
{
  "case_id": "cand_timing_gate_123",
  "suite": "timing_gate",
  "source": "db",
  "source_ref": "chatlog:123",
  "description": "action=continue reason=at_bot",
  "input": {},
  "expected": { "needs_label": true },
  "tags": ["sampled", "timing_gate"],
  "status": "candidate",
  "fingerprint": "abc123",
  "note": ""
}
```

约束：

- `status=candidate` 时允许 `expected.needs_label=true`。
- `status=labeled` 时 `expected` 必须非空，且不能包含 `needs_label=true`。
- `status=promoted` 表示已经写出正式 case 文件。
- `source_ref` 可以保留来源标识，但正式 case 不提交未脱敏聊天全文。

### Label JSONL

离线标注导入使用 JSONL，一行一条：

```json
{"case_id":"cand_timing_gate_123","expected":{"timing_action":"continue","should_reply":true},"note":"明确点名 bot 提问","labeler":"admin","reviewed_at":"2026-06-18T12:00:00+08:00"}
```

约束：

- `case_id` 必须存在。
- `expected` 必须通过可评分契约校验。
- `note`、`labeler`、`reviewed_at` 保留为审计信息，不参与评分。
- 导入时不创建正式 case，只把候选标记为 `labeled`。

### 正式 EvalCase

promote 写出的正式 case 保持通用 `EvalCase` 结构：

```json
{
  "id": "cand_timing_gate_123",
  "suite": "timing_gate",
  "description": "action=continue reason=at_bot",
  "input": {},
  "expected": {"timing_action": "continue", "should_reply": true},
  "tags": ["sampled", "timing_gate", "promoted"],
  "meta": {
    "origin": "eval_candidate",
    "source": "db",
    "source_ref": "chatlog:123",
    "fingerprint": "abc123"
  }
}
```

`meta` 是 P4-1 新增的可选字段；runner 和 scorer 不读取它，只用于来源追溯。

## 六、Expected 契约

P4-1 需要把“写了 expected 但没有实际评分”的风险显式化。

第一阶段采用保守策略：

- 新增 `evals/expected_contract.py` 或等价 helper，提供 `validate_expected_for_label(suite, expected)`。
- 该 helper 先服务 label / promote，不立刻让所有历史 formal case 因未知 key 失败。
- 对候选标注和 promote：未知 key 直接拒绝，避免新数据继续产生假通过。
- 对历史正式 case：补测试列出未知 key，并逐步补 runner / scorer 覆盖。

已知需要补齐的历史 key：

- `served_sticker_id`：sticker image endpoint 应输出实际服务的 sticker id。
- `send_source`：sticker public proxy 展开应输出来源类型。

可评分 key 清单以 `scorers.py` 实际支持为准，包括 `should_reply`、`timing_action`、`scoring`、`model_used`、`must_not_use`、`should_call_auto_routing`、`send_mode`、`reply_to_message_id`、`mentions`、`must_contain`、`must_not_contain`、`http_status`、`content_type_prefix`、`forbidden_terms`、`should_create_jargon`、`should_create_expression`、`no_reply`、`no_learn`、`no_context`、`should_enter_context`、`should_write_chatlog`、`should_write_conversation_turn`。

## 七、数据集边界

通用 eval 里需要明确两个概念：

- `suite`：决定使用哪个 runner，例如 `timing_gate`、`model_routing`。
- `dataset`：决定 case 文件放在哪个目录、用哪条命令运行，例如 `timing_gate`、`regression`、未来的 `reply_contract` 数据集。

现有 `evals/run.py` 已经隐式支持“目录名是 dataset，JSON 内 `suite` 是 runner”的模式，`regression` 目录就是混合 runner 数据集。P4-1 将把这个约定写入文档和测试。

promote 目标规则：

- 默认 dataset 仍为 `regression`，保持现有 Admin 行为兼容。
- CLI / Admin 可以显式传入 `target_dataset`。
- `timing_gate` 这类已有独立目录的 suite 可以直接 promote 到 `evals/cases/timing_gate/`。
- 对没有独立目录的 suite，不自动创建新目录，除非调用方显式指定并通过 dry-run。
- 所有目标路径必须拒绝覆盖已有文件。

## 八、接口边界

### Store 层

`core/eval_sampling/store.py` 负责状态机和文件写出：

- `label_candidate(db, case_id, expected, note=None, labeler=None)`：校验 expected 后标记为 `labeled`。
- `promote_candidate(db, case_id, target_dataset="regression", dry_run=False)`：校验状态、expected、目标路径和文件冲突。
- `validate_candidate_expected(suite, expected)`：或调用 `evals.expected_contract` 中的 helper。

### Admin API

后端兼容一段时间内的旧字段：

```json
{"expected": {"timing_action": "continue"}}
```

以及旧前端已经发送过的：

```json
{"expected_json": {"timing_action": "continue"}}
```

但 WebUI 会改为只发送 `expected`。接口错误时返回 400，不能把空 expected 写成 labeled。

### CLI

新增离线入口，例如：

```bash
python -m evals.candidates export --suite timing_gate --status candidate --out tmp/evals/timing_gate_candidates.jsonl
python -m evals.candidates import-labels --labels tmp/evals/timing_gate_labels.jsonl
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --dry-run
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --apply
```

CLI 只处理候选、标签和晋升，不运行真实 DB 采样；采样仍由现有 `evals/sample_from_db.py`、`evals/sample_from_logs.py` 和后台 scheduler 提供。

## 九、RAG 边界

RAG benchmark 不并入通用 `EvalCase`：

- 继续使用 `evals/rag_benchmark/schema.py` 的 `BenchmarkCase`。
- manual case 继续放在 `evals/cases/rag_benchmark/manual/`。
- generated case 继续默认写到 `tmp/rag_benchmark/generated/`。
- PR gate 只使用 deterministic / no-reranker 模式；runtime provider 运行作为人工或夜间任务。

P4-1 文档需要说明：通用 `candidates → labeled → promoted` 先覆盖 EvalCase runner；RAG 的人工标注和 promote 单独按 `BenchmarkCase` 体系设计。

## 十、验收标准

- [ ] WebUI 标注请求和后端 `LabelRequest` 字段一致，旧 `expected_json` 请求不会静默丢数据。
- [ ] 空 expected、`needs_label=true` 和不可评分 expected 不能进入 `labeled` 或 `promoted` 状态。
- [ ] promote dry-run 能报告目标 dataset、输出路径、文件冲突和 expected 契约错误。
- [ ] promote apply 能写出正式 EvalCase，并保留来源 `meta`。
- [ ] 默认 candidates 不进入正式 eval；只有 promote 后的 case 才进入目标 dataset。
- [ ] `regression` 目录作为混合 dataset、`suite` 作为 runner 的约定有测试保护。
- [ ] 历史未评分 expected key 至少被测试发现，并补齐 `served_sticker_id` / `send_source` 的 runner 或 scorer 覆盖。
- [ ] 首批 `timing_gate` 或 `model_routing` promoted case 可通过定向 eval。
- [ ] `docs/evals.md` 记录候选导出、标注导入、dry-run、promote、baseline 更新和隐私边界。

## 十一、子 agent 分工

P4-1 可以并行，但共享契约文件必须由主线程或单一 owner 维护。

- Contract owner：`evals/expected_contract.py`、`evals/scorers.py`、`evals/schema.py`、`tests/test_eval_candidate_contract.py`。
- Store / API owner：`core/eval_sampling/store.py`、`api/admin_routes.py`、对应 API / store 测试。
- WebUI owner：`webui/src/features/evals/EvalsPage.jsx`、WebUI 静态测试。
- CLI owner：`evals/candidates.py`、CLI 测试、`docs/evals.md`。
- Dataset owner：首批 promoted case 和 baseline 文档，不能同时改 `run.py` 和 scorer contract。

主线程负责审查契约、运行定向与全量验证、同步 `docs/todo.md` 和 `docs/plan_walkthrough.md`，并按阶段提交。

## 十二、风险与处理

- **错误标注风险**：后端同时校验字段和 expected 内容，前端只是辅助，不作为安全边界。
- **假通过风险**：候选 promote 前必须校验 expected key 已被 scorer 支持。
- **目录语义风险**：文档和测试固定 `dataset` / `suite` 两层语义，避免误把目录名当 runner。
- **隐私风险**：promote 不自动写入完整 ChatLog 明文；采样内容保持截断，并要求人工确认脱敏状态。
- **CI 膨胀风险**：P4-1 先做离线 CLI 和确定性 suite，RAG runtime、真实 DB 采样和大型 Admin 工作台不进 PR gate。

---

_设计依据：2026-06-18 P4-1 三个只读子 agent 审计结果、`docs/todo.md` 路线项 8、`docs/plan_walkthrough.md` 当前目标、现有 `evals/` 与 `core/eval_sampling/` 实现。_
