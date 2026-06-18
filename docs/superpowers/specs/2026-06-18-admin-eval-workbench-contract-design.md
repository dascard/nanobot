# P4-2 Admin 标注工作台契约化设计

日期：2026-06-18

## 背景

P4-1 已经完成通用 `candidates → labeled → promoted` 闭环：后端可以拒绝空 `expected`、`needs_label=true` 和不可评分字段，promote 支持 dry-run 与 `target_dataset`，离线 CLI 也能导出、导入 labels 并晋升正式 case。当前缺口集中在 Admin WebUI 和 expected 契约表达：

- `evals/expected_contract.py` 目前只有全局 key 白名单，不校验字段类型和枚举值。
- Admin API 没有只读契约端点，前端无法从后端获得 canonical expected schema。
- WebUI 标注表单仍会生成 `expected_action`、`should_learn`、`quality`、`reason`、`delay_seconds` 等不可评分字段。
- WebUI promote 按钮直接 apply 到默认 `regression`，没有先 dry-run，也没有 `target_dataset` 输入和预检结果确认。

P4-2 的目标是把 Admin 标注工作台从「能调用后端」推进到「按后端契约生成可评分 expected，并在写正式 case 前完成预检确认」。

## 非目标

- 不重写 P4-1 已完成的 store 状态机、离线 CLI、baseline diff、runner / scorer 分发和 `capability_model_routing` 数据集。
- 不把 RAG benchmark 并入通用 `EvalCase`，RAG 标注闭环留到 P4-4。
- 不在本阶段新增更多 per-capability 数据集，也不扩更多 suite PR gate。
- 不引入 LLM judge。本阶段只约束 deterministic scorer 已读取的 expected 字段。
- 不把 Admin WebUI 做成完整数据标注平台。本阶段只补足人工标注、契约校验和 promote 预检所需的最小工作台。

## 方案选择

### 方案 A：前端硬编码 expected 字段

前端维护一份 suite 到字段的映射，提交前做本地过滤。实现最短，但契约会在前后端分裂；后端新增 scorer 字段后，前端容易再次滞后。

### 方案 B：后端暴露 expected contract，前端消费契约

后端在 `evals.expected_contract` 中维护字段 schema 和 suite presets，Admin API 暴露只读端点。WebUI 加载该端点后渲染表单，并在提交前用返回的 `scoreable_keys` 过滤。后端仍是最终校验者。

这是推荐方案。它复用 P4-1 的 `SCOREABLE_EXPECTED_KEYS`，并把 UI 从手写字段迁移到同一份契约。

### 方案 C：引入完整 JSON Schema / 表单 DSL

用标准 JSON Schema 描述所有字段，并生成复杂动态表单。长期更强，但本阶段成本过高；当前字段规模小，用轻量 schema 足够。

## 后端设计

### expected schema

`evals/expected_contract.py` 保持 `SCOREABLE_EXPECTED_KEYS` 作为 scorer 字段全集，同时新增字段 schema 和 suite presets：

- `scoreable_keys`：所有可评分 expected key，必须与 `SCOREABLE_EXPECTED_KEYS` 对齐。
- `field_schema`：每个字段的类型、枚举、中文标签、说明、是否高级字段。
- `suite_presets`：每类 suite 默认展示哪些字段。
- `deprecated_keys`：明确拒绝的旧 UI 字段，便于前端静态测试和错误提示。

字段类型采用小而稳定的集合：

| 类型 | 校验规则 | 例子 |
|------|----------|------|
| `boolean` | 必须是 JSON boolean | `should_reply`、`no_reply` |
| `string` | 必须是字符串 | `model_used`、`content_type_prefix` |
| `integer` | 必须是整数 | `http_status` |
| `string_or_number` | 字符串、整数或浮点数 | `reply_to_message_id`、`served_sticker_id` |
| `string_list` | 字符串数组 | `required_tools`、`forbidden_terms` |
| `array` | JSON 数组，元素可为对象或字符串 | `mentions` |
| `object` | JSON object | `scoring` |
| `enum` | 字符串且落在枚举集合内 | `timing_action`、`send_mode` |

`validate_expected_contract(suite, expected)` 继续作为唯一写入校验入口，新增类型和枚举检查。错误消息必须包含字段名，便于 API 和 WebUI 直接展示。

### Admin contract endpoint

新增只读接口：

```text
GET /api/v1/admin/evals/expected-contract
```

响应结构：

```json
{
  "scoreable_keys": ["should_reply", "timing_action"],
  "field_schema": {
    "timing_action": {
      "type": "enum",
      "values": ["continue", "wait", "no_reply"],
      "label": "时机动作",
      "description": "TimingGate 最终动作"
    }
  },
  "suite_presets": {
    "timing_gate": {
      "fields": ["timing_action", "should_reply", "scoring"]
    }
  },
  "deprecated_keys": ["expected_action", "should_learn", "quality"]
}
```

该端点只读，不依赖数据库，不改变候选状态。

### label API

`LabelRequest.normalized_expected()` 保留 `expected_json` 兼容，但新增冲突保护：

- 只传 `expected`：使用 `expected`。
- 只传 `expected_json`：兼容旧客户端。
- 两者都传且内容相同：接受。
- 两者都传且内容不同：返回 400，避免客户端误以为旧字段生效。

最终写入仍走 `label_candidate()` 和 `validate_expected_contract()`。

### promote API

后端已有 dry-run 能力。本阶段补齐 apply 响应，使 dry-run 与 apply 的响应字段对称：

- dry-run 返回 `dry_run=true`、`case_id`、`suite`、`target_dataset`、`path`、`case`。
- apply 返回 `dry_run=false`、`ok=true`、`case_id`、`suite`、`target_dataset`、`path`。

这样前端可以在 apply 成功后展示真实目标，而不是写死 `regression`。

## WebUI 设计

### 加载契约

`EvalsPage.jsx` 在候选页加载时调用 `/evals/expected-contract`，保存到 `expectedContract`。如果端点失败，标注 modal 仍允许高级 JSON 模式，但必须在界面内展示 `labelError`，不能静默回退到旧字段。

### 标注表单

表单只允许提交契约内字段：

- `timing_gate`：展示 `timing_action`、`should_reply`，`scoring` 只在高级 JSON 模式填写。
- `memory_learning`：展示 `no_learn`、`should_create_jargon`、`should_create_expression`、`forbidden_terms`。
- `group_reply` / `reply_contract`：展示 `should_reply`、`required_tools`、`forbidden_tools`、`send_mode`、`reply_to_message_id`、`mentions`、`must_contain`、`must_not_contain`。
- `model_routing`：展示 `model_used`、`must_not_use`、`should_call_auto_routing`。
- `moderation`：展示 `no_reply`、`no_learn`、`no_context`、`should_enter_context`、`should_write_chatlog`、`should_write_conversation_turn`。
- 其他 suite：默认展示空 `{}` 的 JSON 编辑器，用户必须显式填写契约字段。

人工解释不再写入 `expected.reason`。它进入 `note` 字段，与 scorer 输入分离。

提交前客户端做轻量检查：

- `expected` 不能为空。
- 不允许 `needs_label=true`。
- 不允许未知字段和 deprecated 字段。
- JSON 模式解析失败时展示内联错误，不调用 API。

后端仍负责最终校验。前端检查只用于减少错误往返。

### promote 预检 UI

`labeled` 行的「提升」按钮改为打开 promote modal：

1. 用户输入 `target_dataset`，默认值可以来自当前 candidate 的 `suite` 或 `regression`。
2. 点击「预检」发送 `{ dry_run: true, target_dataset }`。
3. modal 展示后端返回的 `target_dataset`、`path`、`case.id`、`case.suite` 和 `case.expected`。
4. dry-run 成功后才启用「确认提升」。
5. 点击「确认提升」发送 `{ dry_run: false, target_dataset }`。
6. apply 成功后关闭 modal、刷新候选列表，并展示后端返回的真实 `path`。

错误展示使用 `promoteError`，不再依赖 `alert()` 承载工作台内错误。

## 数据流

标注流程：

```text
WebUI load expected-contract
  → 用户打开 label modal
  → 表单 / JSON 生成 expected + note
  → 前端契约检查
  → POST /evals/candidates/{case_id}/label
  → validate_expected_contract()
  → EvalCandidate.status = labeled
```

晋升流程：

```text
用户打开 promote modal
  → POST /promote { dry_run: true, target_dataset }
  → plan_candidate_promotion()
  → 展示 path 和 case 摘要
  → POST /promote { dry_run: false, target_dataset }
  → promote_candidate()
  → 写 evals/cases/<target_dataset>/<case_id>.json
  → EvalCandidate.status = promoted
```

## 子 agent 分工

P4-2 可以拆给互不干扰的子 agent 执行：

- **后端契约 agent：** 修改 `evals/expected_contract.py`、`api/admin_routes.py` 和 `tests/test_eval_candidate_contract.py`。产出 P4-2A 提交。
- **WebUI 工作台 agent：** 修改 `webui/src/features/evals/EvalsPage.jsx` 和 `tests/test_webui_admin_redesign.py`，只依赖 P4-2A 的 API 契约。产出 P4-2B 提交。
- **文档 / 验证 agent：** 只读审查最终 diff、运行文档扫描和推荐验证命令，检查是否误碰 P4-1 store / CLI / runner。该 agent 不直接改文件，结果由主线程采纳。

后端和前端可以并行读码，但写入必须按顺序集成：先合后端契约，再合前端消费契约，最后统一跑 WebUI build 和全量 pytest。

## 测试计划

后端：

- `tests/test_eval_candidate_contract.py` 覆盖 expected-contract endpoint。
- `validate_expected_contract()` 覆盖空对象、`needs_label`、未知字段、deprecated 字段、类型错误、枚举错误和合法组合。
- Admin label API 覆盖 `expected` / `expected_json` 冲突。
- Admin promote API 覆盖 apply 响应字段与 dry-run 对齐。

前端：

- `tests/test_webui_admin_redesign.py` 静态检查 WebUI 会加载 `/evals/expected-contract`。
- 静态检查标注表单不再把 `expected_action`、`should_learn`、`quality`、`reason`、`delay_seconds` 写入 expected。
- 静态检查 promote 流程先 dry-run，携带 `target_dataset`，成功后再 apply。
- 静态检查 `labelError` 和 `promoteError` 存在。
- `npm --prefix webui run build` 验证 React 构建。

回归：

- P4-1 候选闭环回归：`tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py`。
- WebUI 静态回归：`tests/test_webui_admin_redesign.py`。
- 提交前全量：`python -B -m pytest tests/ -v -p no:cacheprovider`。

## 验收标准

- Admin API 暴露 expected contract，并且 `scoreable_keys` 与 `SCOREABLE_EXPECTED_KEYS` 对齐。
- 后端 label 校验能拒绝错误类型和非法枚举，例如 `{"should_reply":"false"}`、`{"timing_action":123}`、`{"timing_action":"bad"}`。
- WebUI 标注请求只提交可评分 expected 字段，人工解释进入 `note`。
- WebUI 不再出现旧 expected key 的提交路径：`expected_action`、`should_learn`、`quality`、`reason`、`delay_seconds`。
- WebUI promote 必须先 dry-run，再 apply；apply 必须携带同一个 `target_dataset`。
- P4-1 的 store、CLI、dataset / suite 语义不被重写。
- 定向测试、WebUI build 和全量 pytest 通过后，才能把 P4-2 标记为已完成。
