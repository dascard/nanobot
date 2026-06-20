# EvalCandidate 运营规则设计

日期：2026-06-20

## 背景

`EvalCandidate` 已经支持采样、导出、人工标注和单条晋升，但运营侧缺少队列摘要、晋升资格解释和批量预检。当前实现里还存在两个高风险缺口：

- `PATCH /evals/candidates/{case_id}` 可以直接写任意 `status`，绕过 `label` / `ignore` / `promote` 状态机。
- `plan_candidate_promotion()` 不校验 suite 是否可运行，`suite="error"` 等候选可能被晋升为正式 eval case。

本阶段目标是让候选队列从「能手工操作」提升到「能解释、能预检、能安全晋升」。范围保持保守：不改数据库表结构，不做批量 apply，不重构 eval runner。

## 目标

1. 给每条候选提供稳定的 readiness 派生字段，解释是否可标注、是否可晋升以及阻断原因。
2. 给候选列表提供 summary，支持运营快速判断当前过滤范围内的候选分布和阻断原因。
3. 提供批量 preflight 接口和 CLI dry-run 聚合，让运营能一次看到 ready / blocked，而不是遇到第一条 blocked 就中断。
4. 收窄状态写入路径：`labeled` 只能由 label 写入，`promoted` 只能由 promote 写入。
5. 阻止不可运行 suite 晋升，至少覆盖 `error` 和未知 suite。

## 非目标

- 不新增数据库字段或迁移脚本。readiness、summary 和 preflight 全部基于现有 `EvalCandidate` 行派生。
- 不实现批量 apply。批量写文件需要额外审计和回滚策略，留到后续阶段。
- 不要求 `target_dataset` 必须等于 `suite`。现有能力允许跨 dataset 存放 case，本阶段只保证名字安全和目标文件不冲突。
- 不全面重写 WebUI 标注表单。已有高级 JSON 模式继续承担未定制 suite 的标注入口。
- 不改 eval runner 的执行分发结构，只抽取或复制可运行 suite 常量，避免从 runner 反向耦合。

## 方案选择

### 方案 A：只扩展现有 promote dry-run

在单条 promote dry-run 中加入 `readiness`，WebUI 仍需要逐条点开才能看到原因。实现小，但无法解决队列摘要和批量预检。

### 方案 B：派生 readiness + summary + 只读 preflight

在 `core/eval_sampling/store.py` 中新增纯派生规则，列表返回每条 readiness，同时提供 summary 和批量 preflight。单条 promote 复用同一规则。实现量适中，写入风险低。

### 方案 C：新增候选审核工作流表

新增审核记录、批次、审批和回滚表。能力完整，但超出当前真实样本运营阶段，迁移和 UI 成本明显偏高。

推荐采用方案 B。它补齐当前运营阻塞点，又保持无 schema 变更和无批量写入。

## 后端设计

### 常量

在 `core/eval_sampling/store.py` 中定义可晋升 suite 集合：

```python
RUNNABLE_EVAL_SUITES = frozenset({
    "sticker",
    "memory_learning",
    "moderation",
    "model_routing",
    "group_reply",
    "reply_contract",
    "rendering_contract",
    "timing_gate",
})
```

这组值与 `evals/run.py` 当前可分发 runner 保持一致。`error` 和未知 suite 一律视为不可晋升。

### Readiness 契约

新增 `candidate_readiness(row, *, target_dataset=None) -> dict`。该函数不写 DB、不写文件。

返回示例：

```json
{
  "ready": true,
  "can_label": false,
  "can_promote": true,
  "status": "ready",
  "suite": "timing_gate",
  "target_dataset": "timing_gate",
  "target_path": "/repo/evals/cases/timing_gate/cand_1.json",
  "blocking_reasons": [],
  "warnings": []
}
```

阻断原因使用稳定 `code`，便于 API、CLI 和 WebUI 共用：

| code | 含义 |
| --- | --- |
| `invalid_status` | 当前状态不可晋升，例如 `candidate`、`ignored`、`promoted` 或未知状态 |
| `suite_not_runnable` | suite 不在可运行集合中 |
| `expected_invalid` | expected 为空、包含 `needs_label`、废弃字段、未知字段或类型不合法 |
| `target_dataset_invalid` | `target_dataset` 不是安全目录名 |
| `target_case_exists` | 目标 case 文件已存在 |

`candidate` 状态的候选可以 `can_label=true`，但 `can_promote=false`。`labeled` 且所有检查通过时 `ready=true`、`can_promote=true`。

### Summary 契约

新增 `candidate_queue_summary(db, *, suite="", status="", source="", target_dataset="") -> dict`。

返回字段：

```json
{
  "total": 12,
  "filters": {
    "suite": "timing_gate",
    "status": "",
    "source": "",
    "target_dataset": "timing_gate"
  },
  "by_status": {
    "candidate": 4,
    "labeled": 5,
    "ignored": 2,
    "promoted": 1
  },
  "by_suite": {
    "timing_gate": 8,
    "error": 4
  },
  "by_source": {
    "db": 7,
    "log": 5
  },
  "readiness": {
    "ready": 3,
    "blocked": 9
  },
  "top_blocking_reasons": [
    {"code": "invalid_status", "count": 6},
    {"code": "suite_not_runnable", "count": 3}
  ]
}
```

列表接口可以直接附带同一 summary：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "summary": {}
}
```

旧客户端忽略新增字段即可兼容。

### Preflight 契约

新增只读接口：

```http
POST /api/v1/admin/evals/candidates/preflight
```

请求体：

```json
{
  "case_ids": ["cand_1"],
  "suite": "timing_gate",
  "status": "labeled",
  "source": "db",
  "target_dataset": "timing_gate",
  "limit": 200
}
```

规则：

- `case_ids` 为空时按过滤条件查询，默认只预检 `status="labeled"`。
- `case_ids` 非空时按这些 ID 查询，保持输入顺序；不存在的 ID 作为 blocked item 返回。
- `limit` 限制在 `1..500`。
- 接口只返回预检结果，不写文件、不改 DB、不审计为 apply。

响应：

```json
{
  "ok": false,
  "total": 2,
  "ready": 1,
  "blocked": 1,
  "target_dataset": "timing_gate",
  "items": [
    {
      "case_id": "cand_1",
      "suite": "timing_gate",
      "status": "labeled",
      "target_dataset": "timing_gate",
      "path": "/repo/evals/cases/timing_gate/cand_1.json",
      "readiness": {"ready": true, "blocking_reasons": []}
    },
    {
      "case_id": "cand_error_1",
      "suite": "error",
      "status": "labeled",
      "target_dataset": "timing_gate",
      "path": "",
      "readiness": {
        "ready": false,
        "blocking_reasons": [{"code": "suite_not_runnable", "message": "suite is not runnable"}]
      }
    }
  ]
}
```

### Promote 规则收敛

`plan_candidate_promotion()` 改为复用 readiness：

1. 获取候选，不存在时继续抛 `candidate not found`。
2. 计算 readiness。
3. 只允许 `ready=true` 时构建 case JSON。
4. 阻断时抛出包含第一个 `code` 的 `ValueError`，API 层后续可以映射成结构化错误。

`promote_candidate()` 保持重新执行 plan 再写文件，避免 dry-run 和 apply 之间目标文件状态变化。

### PATCH 状态约束

`PATCH /evals/candidates/{case_id}` 保留 `priority` 和 `note`。`status` 只允许下面的保守操作：

- `ignored`：允许把 `candidate` 或 `labeled` 标记为忽略。
- `candidate`：仅允许从 `ignored` 恢复，便于误忽略后重新处理。

拒绝直接 PATCH 到 `labeled` 或 `promoted`。标注必须走 `/label`，晋升必须走 `/promote`。

## CLI 设计

`python -m evals.candidates promote` 的 dry-run 行为改为聚合结果：

```json
{
  "count": 2,
  "ready": 1,
  "blocked": 1,
  "items": [
    {"case_id": "cand_1", "ready": true, "path": "/repo/evals/cases/timing_gate/cand_1.json"},
    {"case_id": "cand_error_1", "ready": false, "error": "suite_not_runnable"}
  ]
}
```

dry-run 不因单条 blocked 中断。`--apply` 保持严格：只要批次中存在 blocked item，就拒绝整体 apply，返回清晰错误，不做部分写入。

后续可以新增 `summary` 子命令，但本阶段最小实现以 `promote` dry-run 的聚合结果满足批量预检。

## WebUI 设计

候选列表页新增轻量运营可见性：

- 在表格上方展示 summary：total、candidate、labeled、ready、blocked、ignored、promoted。
- 表格新增「资格」列，展示 `ready` / `blocked` badge 和首个阻断原因。
- `labeled` 但 `readiness.ready=false` 时禁用「提升」按钮，并在按钮 title 中展示原因。
- 详情弹窗展示完整 `readiness` JSON。
- 新增「预检当前页」按钮，只对当前页候选调用 preflight，不做跨分页全量选择。

此设计避免多选状态、跨页选择和批量 apply 的复杂度，同时给运营提供当前页批量判断能力。

## 测试策略

### 后端

- `candidate_readiness()` 覆盖 `candidate`、`labeled ready`、`suite=error`、expected 无效、目标文件已存在、非法 `target_dataset`。
- `plan_candidate_promotion()` 对 `suite=error` 和未知 suite 抛错，且不写文件、不改状态。
- `GET /evals/candidates` 返回每条 readiness 和 summary。
- `POST /evals/candidates/preflight` 对 mixed ready / blocked 返回聚合结果。
- `PATCH /evals/candidates/{case_id}` 拒绝 `status="labeled"`、`status="promoted"` 和未知状态，仍允许 `priority` / `note`。

### CLI

- dry-run mixed readiness 返回 ready / blocked 统计，不写文件。
- apply 遇到 blocked 批次时整体拒绝，不做部分写入。
- 原有单条 ready dry-run / apply 行为保持兼容。

### WebUI

- 静态测试覆盖 summary 字段消费、`readiness` 字段展示、blocked 提升按钮禁用和 preflight 调用。
- `npm --prefix webui run build` 作为前端构建验证。

## 验收标准

1. 不可运行 suite 不能晋升为正式 eval case。
2. 不能通过 PATCH 直接把候选改成 `labeled` 或 `promoted`。
3. 候选列表 API 返回 summary 和 readiness，旧字段保持不变。
4. 批量 preflight 对 mixed candidates 返回完整 ready / blocked 结果，不被第一条错误中断。
5. CLI dry-run 能作为批量预检使用，apply 不发生部分成功。
6. WebUI 能在列表和详情中解释阻断原因。
7. 定向测试、WebUI 静态测试、WebUI build 和全量测试通过后才能收口。

## 迭代切分

1. 后端 readiness / summary / 状态约束。
2. 后端 preflight API 与 CLI 聚合 dry-run。
3. WebUI summary、资格列和当前页 preflight。
4. 文档收口和 `docs/plan_walkthrough.md` 状态更新。

每个切分完成后独立验证并提交，避免把接口、CLI 和 UI 风险混在一个提交里。
