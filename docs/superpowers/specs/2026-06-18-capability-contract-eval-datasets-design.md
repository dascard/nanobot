# P4-3 能力契约评测数据集扩展设计

> 2026-06-18 · 在 P4-1 标注闭环和 P4-2 契约化标注工作台之上，扩展 `capability_reply_contract` 与 `capability_rendering_contract` 两个可复跑能力数据集。

---

## 背景

P4-1 已完成 expected 契约、候选标注、promote dry-run、离线 CLI、dataset / suite 边界和首个 `capability_model_routing` 能力数据集。P4-2 已完成 Admin expected contract endpoint、WebUI 契约化标注、`note` / `expected` 分离，以及 promote dry-run 到 apply 的预检流程。

当前缺口不是再造评测框架，而是把更多稳定能力沉淀为可复跑数据集。P4-3 聚焦两个能力面：

- `capability_reply_contract`：验证群聊回复合同，例如引用回复、at bot mention、指向他人时静默。
- `capability_rendering_contract`：验证响应信封到 QQ 出站消息的渲染合同，例如 text / html 顺序、图片 CQ 码、generated image public URL、缺失 public URL 的回退 token、`reply_meta` 保留。

---

## 目标

1. 新增 `capability_reply_contract` dataset 和 baseline，复用现有 `reply_contract` / `group_reply` runner 与 scorer。
2. 新增 `capability_rendering_contract` dataset 和 baseline，新增一个离线 `rendering_contract` runner，复用现有 scorer 字段。
3. 保持 dataset 与 suite 的分层语义：dataset 负责目录和门禁维度，case 内部 `suite` 负责选择 runner。
4. 让两个能力数据集都能通过 `python -B -m evals.run --suite <dataset> --baseline ... --min-pass-rate 1.0 --max-new-failures 0` 独立验收。
5. 让 Admin expected contract 能暴露 `rendering_contract` suite preset，方便后续候选样本标注进入同一契约体系。

---

## 非目标

- 不做 RAG 标注闭环和 RAG baseline gate；该工作属于 P4-4。
- 不接入更多 suite 的 PR gate 或周期性复跑；该工作属于 P4-5。
- 不修改生产 QQ renderer、真实 QQ push、响应信封结构或 API 响应格式。
- 不新增 WebUI 标注字段；P4-3 仅复用 P4-2 的契约化工作台。
- 不设计结构化 rendering scorer，例如 `rendered_items` 或 `cq_segments`；本阶段用 `must_contain` / `must_not_contain` / `reply_meta` 断言覆盖核心渲染合同。

---

## 设计选择

采用「数据集优先 + 最小 runner」方案。

`capability_reply_contract` 不新增 runner。该 dataset 下的 case 可以设置 `suite: "reply_contract"` 或 `suite: "group_reply"`，继续走 `evals.runners.group_reply_runner.run_group_reply_case()`。现有 scorer 已支持 `should_reply`、`required_tools`、`forbidden_tools`、`send_mode`、`reply_to_message_id`、`mentions`、`must_contain` 和 `must_not_contain`。

`capability_rendering_contract` 新增 `rendering_contract` runner。runner 只做离线渲染，不访问网络，不写数据库，不调用真实 QQ push。它读取 `case.input.envelope`，调用 `core.qq_outbound_renderer.render_qq_outbound_envelope()`，再把渲染结果映射为 `EvalOutput`，交给通用 scorer 评分。

放弃两个替代方案：

- 只加数据集、不加 rendering runner：无法覆盖响应信封到 QQ 出站消息的主渲染路径，`capability_rendering_contract` 名不副实。
- 新增结构化 rendering scorer：断言更精细，但会扩大 expected contract、Admin 表单和测试面，不适合作为 P4-3 首轮扩展。

---

## 数据集契约

### `capability_reply_contract`

目录：`evals/cases/capability_reply_contract/`

baseline：`evals/baselines/capability_reply_contract.json`

推荐 case：

- `reply_quote_to_bot_001.json`：用户回复 bot 历史消息时应使用 quote 模式，断言 `send_mode: "quote"` 与 `reply_to_message_id`。
- `reply_at_bot_mention_mode_001.json`：用户 at bot 时应使用 mention 模式，断言 `send_mode: "mention"` 和 `mentions`。
- `reply_directed_to_other_no_reply_001.json`：用户明确指向其他人时应静默，断言 `should_reply: false` 和禁止调用回复工具。

case 内部 `suite` 使用 `reply_contract`，除非需要沿用既有 `group_reply` case 语义。

### `capability_rendering_contract`

目录：`evals/cases/capability_rendering_contract/`

baseline：`evals/baselines/capability_rendering_contract.json`

runner suite：`rendering_contract`

runner 输入：

```json
{
  "envelope": {
    "reply": "可选旧字段回退",
    "messages": [
      {"type": "text", "text": "你好"},
      {"type": "image", "url": "https://example.test/a.png"}
    ],
    "reply_meta": {"send_mode": "quote", "reply_to_message_id": "42"}
  },
  "allow_base64": false
}
```

runner 输出映射：

- `reply_text`：`QQOutboundRenderResult.message`。
- `should_reply`：`reply_text` 非空时为 `true`。
- `reply_meta`：`QQOutboundRenderResult.reply_meta`。
- `raw.rendered_message`：渲染后的最终 QQ 消息字符串。
- `raw.messages`：规范化后的消息 item。
- `raw.reply_meta`：规范化后的回复元信息。
- `raw.warnings`：renderer warnings，例如缺失 generated image public URL。

推荐 case：

- `render_text_html_order_001.json`：text / html 内容按消息数组顺序拼接。
- `render_image_url_as_cq_001.json`：图片 URL 渲染为 `[CQ:image,file=...]`。
- `render_generated_image_public_url_001.json`：generated image 有 public URL 时渲染为 CQ image。
- `render_generated_image_without_public_url_001.json`：generated image 缺失 public URL 时保留 `[generated_image:<id>]` 并记录 warning。
- `render_reply_meta_preserved_001.json`：quote / mention 元信息经过 `sanitize_reply_meta()` 后仍可被 scorer 断言。

---

## 代码边界

实现阶段预期触达文件：

- `evals/run.py`：注册 `rendering_contract` suite 分发。
- `evals/runners/rendering_runner.py`：新增离线 runner。
- `evals/expected_contract.py`：新增 `rendering_contract` suite preset，字段只使用已有可评分 key。
- `evals/cases/capability_reply_contract/*.json`：新增回复合同数据集。
- `evals/cases/capability_rendering_contract/*.json`：新增渲染合同数据集。
- `evals/baselines/capability_reply_contract.json` 与 `evals/baselines/capability_rendering_contract.json`：新增 baseline。
- `tests/test_eval_baseline.py`：覆盖两个新 dataset 的离线运行和 baseline 语义。
- `tests/test_eval_candidate_contract.py`：覆盖 `rendering_contract` expected preset 和字段校验。
- `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`：同步阶段状态与运行方式。

不应触达文件：

- `api/routes.py`、真实 `/chat`、`/group/message` 行为。
- QQbot push 网络调用路径。
- Prompt Runtime 模板。
- RAG benchmark 目录。
- WebUI 标注表单组件，除非现有契约 endpoint 已无法表达 `rendering_contract` preset。

---

## 子 agent 分工

P4-3 适合拆给多个互不写同一文件的子 agent 做只读审计或独立实现切片：

- Reply dataset agent：只读检查 `group_reply_runner`、现有 regression reply cases 和 scorer 字段，输出 `capability_reply_contract` case 清单与 expected 字段建议。
- Rendering runner agent：只读检查 `core/qq_outbound_renderer.py`、`tests/test_qq_outbound_renderer.py` 和 `tests/test_push_envelope.py`，输出 runner 输入输出契约和渲染 case 清单。
- Baseline / docs agent：只读检查 `evals/run.py`、`evals/baseline.py`、`docs/evals.md` 和已有 baseline 文件，输出 baseline 文件结构与运行命令。

主线程负责最终文件编辑、测试、diff 审查和 commit。子 agent 默认只读，除非后续阶段明确拆分为互不冲突的写入范围。

---

## 测试与验收

设计 / 文档阶段：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md
python - <<'PY'
from pathlib import Path
for path in [
    Path("docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md"),
    Path("docs/plan_walkthrough.md"),
    Path("docs/todo.md"),
    Path("docs/evals.md"),
]:
    data = path.read_text(encoding="utf-8")
    if "\ufffd" in data:
        raise SystemExit(f"U+FFFD found in {path}")
PY
git diff --check -- docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md docs/plan_walkthrough.md docs/todo.md docs/evals.md docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md
```

实现阶段定向验收：

```bash
python -B -m pytest tests/test_eval_candidate_contract.py -v -p no:cacheprovider
python -B -m pytest tests/test_eval_baseline.py::test_capability_reply_contract_dataset_uses_reply_runner tests/test_eval_baseline.py::test_capability_rendering_contract_dataset_runs_offline -v -p no:cacheprovider
python -B -m pytest tests/test_qq_outbound_renderer.py tests/test_push_envelope.py -v -p no:cacheprovider
python -B -m evals.run --suite capability_reply_contract --baseline evals/baselines/capability_reply_contract.json --min-pass-rate 1.0 --max-new-failures 0
python -B -m evals.run --suite capability_rendering_contract --baseline evals/baselines/capability_rendering_contract.json --min-pass-rate 1.0 --max-new-failures 0
```

最终回归：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

---

## 阶段拆分

1. P4-3 设计文档：写入本规格，校准路线文档并提交。
2. P4-3 实现计划：写入 `.Codex/plans/capability-contract-eval-datasets.md`，明确 TDD 步骤、文件清单、验证顺序和子 agent 协作边界。
3. P4-3A Reply Contract 数据集：新增 reply contract cases、baseline、测试和文档。
4. P4-3B Rendering Contract runner 与数据集：新增 runner、expected preset、rendering cases、baseline、测试和文档。
5. P4-3C 收口验证：运行两个能力数据集 gate、相关回归和全量测试，更新 walkthrough / todo 状态并提交。
