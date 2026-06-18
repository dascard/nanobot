# P4-3 能力契约评测数据集扩展实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 `capability_reply_contract` 与 `capability_rendering_contract` 两个可复跑能力数据集，并让它们通过 baseline gate 独立验收。

**架构：** Reply contract 数据集复用现有 `reply_contract` / `group_reply` runner，不新增生产行为。Rendering contract 新增一个离线 runner，读取 `case.input.envelope` 后调用 `render_qq_outbound_envelope()`，把渲染结果映射到 `EvalOutput.reply_text`、`reply_meta` 和 `raw`，继续复用现有 scorer。Dataset 目录负责门禁维度，case 内 `suite` 负责 runner 分发。

**技术栈：** Python、pytest、JSON eval cases、现有 `evals` 框架、`core.qq_outbound_renderer`。

---

## 文件职责

- `evals/run.py`：注册 `rendering_contract` suite 分发。
- `evals/runners/rendering_runner.py`：新增离线渲染 runner；不访问网络、不写数据库、不调用 QQ push。
- `evals/expected_contract.py`：新增 `rendering_contract` suite preset，字段只引用已有可评分 key。
- `evals/cases/capability_reply_contract/*.json`：新增 reply contract 能力数据集。
- `evals/cases/capability_rendering_contract/*.json`：新增 rendering contract 能力数据集。
- `evals/baselines/capability_reply_contract.json`：新增 reply contract baseline。
- `evals/baselines/capability_rendering_contract.json`：新增 rendering contract baseline。
- `tests/test_eval_baseline.py`：覆盖 dataset / suite 分层、两个数据集离线运行、baseline gate。
- `tests/test_eval_candidate_contract.py`：覆盖 `rendering_contract` expected preset 与字段类型校验。
- `tests/test_qq_outbound_renderer.py`、`tests/test_push_envelope.py`：作为渲染相邻回归，不在 P4-3 中重写生产 renderer。
- `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md`：同步运行方式、阶段状态和验证记录。

## 子 agent 分工建议

- **Reply dataset agent：** 只读检查 `evals/runners/group_reply_runner.py`、现有 regression reply cases 和 `evals/scorers.py`，输出 3 个 reply case 的输入 / expected 字段核对结果。
- **Rendering runner agent：** 只读检查 `core/qq_outbound_renderer.py`、`tests/test_qq_outbound_renderer.py` 和 `tests/test_push_envelope.py`，输出 runner 输入输出映射和 5 个 rendering case 的断言核对结果。
- **Baseline / docs agent：** 只读检查 `evals/run.py`、`evals/baseline.py`、`docs/evals.md` 和已有 `evals/baselines/*.json`，输出 baseline 文件结构和 gate 命令核对结果。

主线程负责最终编辑、验证、diff 审查和 commit。若使用写入型子 agent，P4-3A 与 P4-3B 可以分开执行；不要让两个 agent 同时修改 `tests/test_eval_baseline.py` 或 `docs/plan_walkthrough.md`。

---

## 任务 1：P4-3A Reply Contract 数据集

**文件：**
- 创建：`evals/cases/capability_reply_contract/reply_quote_to_bot_001.json`
- 创建：`evals/cases/capability_reply_contract/reply_at_bot_mention_mode_001.json`
- 创建：`evals/cases/capability_reply_contract/reply_directed_to_other_no_reply_001.json`
- 创建：`evals/baselines/capability_reply_contract.json`
- 修改：`tests/test_eval_baseline.py`
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：编写 reply dataset 红灯测试**

在 `tests/test_eval_baseline.py` 末尾新增：

```python
def test_capability_reply_contract_dataset_uses_reply_runner():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_reply_contract")

    assert {case.id for case in cases} == {
        "reply_quote_to_bot_001",
        "reply_at_bot_mention_mode_001",
        "reply_directed_to_other_no_reply_001",
    }
    assert {case.suite for case in cases} == {"reply_contract"}
    report = run_suite("capability_reply_contract")
    assert report.total == len(cases)
    assert report.failed == 0
```

- [ ] **步骤 2：运行 reply dataset 红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_baseline.py::test_capability_reply_contract_dataset_uses_reply_runner \
-v -p no:cacheprovider
```

预期：FAIL，`cases` 为空，说明 `capability_reply_contract` 数据集尚未创建。

- [ ] **步骤 3：创建 quote reply case**

创建 `evals/cases/capability_reply_contract/reply_quote_to_bot_001.json`：

```json
{
  "id": "reply_quote_to_bot_001",
  "suite": "reply_contract",
  "description": "用户引用 bot 历史消息时应使用 quote 发送模式。",
  "input": {
    "message": "这条我补充一下",
    "is_reply_to_bot": true,
    "reply_to_message_id": "bot-msg-42",
    "sender_id": "user-1",
    "sender_name": "用户一",
    "self_id": "bot-1"
  },
  "expected": {
    "should_reply": true,
    "required_tools": ["reply"],
    "send_mode": "quote",
    "reply_to_message_id": "bot-msg-42"
  },
  "tags": ["capability", "reply_contract", "quote"]
}
```

- [ ] **步骤 4：创建 at bot mention case**

创建 `evals/cases/capability_reply_contract/reply_at_bot_mention_mode_001.json`：

```json
{
  "id": "reply_at_bot_mention_mode_001",
  "suite": "reply_contract",
  "description": "用户 at bot 时应使用 mention 发送模式，并保留 mentions。",
  "input": {
    "message": "@nanobot 看一下这个问题",
    "is_at_bot": true,
    "mentions": [{"user_id": "10001"}],
    "sender_id": "user-2",
    "sender_name": "用户二",
    "self_id": "bot-1"
  },
  "expected": {
    "should_reply": true,
    "required_tools": ["reply"],
    "send_mode": "mention",
    "mentions": ["10001"]
  },
  "tags": ["capability", "reply_contract", "mention"]
}
```

- [ ] **步骤 5：创建 directed-to-other no-reply case**

创建 `evals/cases/capability_reply_contract/reply_directed_to_other_no_reply_001.json`：

```json
{
  "id": "reply_directed_to_other_no_reply_001",
  "suite": "reply_contract",
  "description": "用户明确指向他人且未 at bot 时应静默。",
  "input": {
    "message": "小明你怎么看这段日志",
    "is_directed_to_other": true,
    "trigger_reason": "ambient",
    "sender_id": "user-3",
    "sender_name": "用户三",
    "self_id": "bot-1"
  },
  "expected": {
    "should_reply": false,
    "forbidden_tools": ["reply"]
  },
  "tags": ["capability", "reply_contract", "directed_to_other", "no_reply"]
}
```

- [ ] **步骤 6：创建 reply baseline**

创建 `evals/baselines/capability_reply_contract.json`：

```json
{
  "suite": "capability_reply_contract",
  "total": 3,
  "passed": 3,
  "failed": 0,
  "pass_rate": 1.0,
  "failed_cases": []
}
```

- [ ] **步骤 7：运行 reply dataset 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_baseline.py::test_capability_reply_contract_dataset_uses_reply_runner \
-v -p no:cacheprovider
```

预期：PASS，`report.failed == 0`。

- [ ] **步骤 8：运行 reply baseline gate**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_reply_contract \
--baseline evals/baselines/capability_reply_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

预期：退出码 0，输出包含 `Gate passed`。

- [ ] **步骤 9：同步 P4-3A 文档状态**

在 `docs/evals.md` 的能力契约数据集规划段落补充 reply dataset 已落地的运行命令：

````markdown
Reply contract gate：

```bash
python -B -m evals.run --suite capability_reply_contract --baseline evals/baselines/capability_reply_contract.json --min-pass-rate 1.0 --max-new-failures 0
```
````

在 `docs/todo.md` 与 `docs/plan_walkthrough.md` 标记 P4-3A reply dataset 已完成，并记录步骤 7、步骤 8 的验证结果。

- [ ] **步骤 10：提交 P4-3A**

运行：

```bash
git add \
tests/test_eval_baseline.py \
evals/cases/capability_reply_contract/reply_quote_to_bot_001.json \
evals/cases/capability_reply_contract/reply_at_bot_mention_mode_001.json \
evals/cases/capability_reply_contract/reply_directed_to_other_no_reply_001.json \
evals/baselines/capability_reply_contract.json \
docs/evals.md \
docs/todo.md \
docs/plan_walkthrough.md
git commit -m "feat(评测): 扩展回复契约数据集"
```

---

## 任务 2：P4-3B Rendering Contract runner 与数据集

**文件：**
- 创建：`evals/runners/rendering_runner.py`
- 创建：`evals/cases/capability_rendering_contract/render_text_html_order_001.json`
- 创建：`evals/cases/capability_rendering_contract/render_image_url_as_cq_001.json`
- 创建：`evals/cases/capability_rendering_contract/render_generated_image_public_url_001.json`
- 创建：`evals/cases/capability_rendering_contract/render_generated_image_without_public_url_001.json`
- 创建：`evals/cases/capability_rendering_contract/render_reply_meta_preserved_001.json`
- 创建：`evals/baselines/capability_rendering_contract.json`
- 修改：`evals/run.py`
- 修改：`evals/expected_contract.py`
- 修改：`tests/test_eval_baseline.py`
- 修改：`tests/test_eval_candidate_contract.py`
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：编写 expected preset 红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
def test_rendering_contract_expected_preset_uses_scoreable_fields():
    from evals.expected_contract import expected_contract_payload
    from evals.expected_contract import validate_expected_contract

    payload = expected_contract_payload()
    fields = payload["suite_presets"]["rendering_contract"]["fields"]

    assert fields == [
        "should_reply",
        "send_mode",
        "reply_to_message_id",
        "mentions",
        "must_contain",
        "must_not_contain",
    ]
    validate_expected_contract(
        "rendering_contract",
        {
            "should_reply": True,
            "send_mode": "quote",
            "reply_to_message_id": "msg-1",
            "mentions": ["10001"],
            "must_contain": ["[CQ:image"],
            "must_not_contain": ["base64://"],
        },
    )
```

- [ ] **步骤 2：编写 rendering dataset 红灯测试**

在 `tests/test_eval_baseline.py` 新增：

```python
def test_capability_rendering_contract_dataset_runs_offline():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_rendering_contract")

    assert {case.id for case in cases} == {
        "render_text_html_order_001",
        "render_image_url_as_cq_001",
        "render_generated_image_public_url_001",
        "render_generated_image_without_public_url_001",
        "render_reply_meta_preserved_001",
    }
    assert {case.suite for case in cases} == {"rendering_contract"}
    report = run_suite("capability_rendering_contract")
    assert report.total == len(cases)
    assert report.failed == 0
```

- [ ] **步骤 3：运行 rendering 红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_rendering_contract_expected_preset_uses_scoreable_fields \
tests/test_eval_baseline.py::test_capability_rendering_contract_dataset_runs_offline \
-v -p no:cacheprovider
```

预期：FAIL。第一个失败来自缺少 `rendering_contract` preset，第二个失败来自缺少数据集或未知 suite。

- [ ] **步骤 4：新增 rendering expected preset**

在 `evals/expected_contract.py` 的 `SUITE_EXPECTED_PRESETS` 增加：

```python
    "rendering_contract": {
        "fields": [
            "should_reply",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ],
    },
```

- [ ] **步骤 5：实现离线 rendering runner**

创建 `evals/runners/rendering_runner.py`：

```python
"""响应信封渲染契约 eval runner。"""
from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from core.qq_outbound_renderer import render_qq_outbound_envelope
from evals.schema import EvalCase, EvalOutput


@contextmanager
def _temporary_generated_image_public_env(inp: Mapping[str, Any]):
    base_url = inp.get("public_base_url")
    token = inp.get("generated_image_token")
    keys = {
        "NANOBOT_PUBLIC_BASE_URL": None if base_url is None else str(base_url),
        "NANOBOT_GENERATED_IMAGE_TOKEN": None if token is None else str(token),
    }
    old = {key: os.environ.get(key) for key in keys}
    try:
        for key, value in keys.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_rendering_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    envelope = inp.get("envelope")
    if not isinstance(envelope, Mapping):
        return EvalOutput(
            case_id=case.id,
            suite=case.suite,
            errors=["input.envelope must be object"],
        )

    with _temporary_generated_image_public_env(inp):
        result = render_qq_outbound_envelope(
            envelope,
            allow_base64=bool(inp.get("allow_base64", False)),
        )

    return EvalOutput(
        case_id=case.id,
        suite=case.suite,
        reply_text=result.message,
        should_reply=bool(result.message),
        reply_meta=result.reply_meta,
        raw={
            "rendered_message": result.message,
            "messages": result.messages,
            "reply_meta": result.reply_meta,
            "warnings": result.warnings,
        },
    )
```

- [ ] **步骤 6：注册 rendering suite 分发**

在 `evals/run.py` 的 `run_case()` 中，在 `reply_contract` 分支后增加：

```python
    elif suite in ("rendering_contract",):
        from evals.runners.rendering_runner import run_rendering_case
        output = run_rendering_case(case)
```

- [ ] **步骤 7：创建 text / html 顺序 case**

创建 `evals/cases/capability_rendering_contract/render_text_html_order_001.json`：

```json
{
  "id": "render_text_html_order_001",
  "suite": "rendering_contract",
  "description": "text 与 html 消息按数组顺序渲染。",
  "input": {
    "envelope": {
      "messages": [
        {"type": "text", "text": "A"},
        {"type": "html", "text": "<article>B</article>"}
      ],
      "reply_meta": {"send_mode": "normal"}
    }
  },
  "expected": {
    "should_reply": true,
    "send_mode": "normal",
    "must_contain": ["A\\n<article>B</article>"]
  },
  "tags": ["capability", "rendering_contract", "text", "html"]
}
```

- [ ] **步骤 8：创建 image URL CQ case**

创建 `evals/cases/capability_rendering_contract/render_image_url_as_cq_001.json`：

```json
{
  "id": "render_image_url_as_cq_001",
  "suite": "rendering_contract",
  "description": "图片 URL 渲染为 OneBot CQ image。",
  "input": {
    "envelope": {
      "messages": [
        {"type": "image", "url": "https://example.test/a.png"}
      ]
    }
  },
  "expected": {
    "should_reply": true,
    "must_contain": ["[CQ:image,file=https://example.test/a.png]"],
    "must_not_contain": ["base64://"]
  },
  "tags": ["capability", "rendering_contract", "image_url"]
}
```

- [ ] **步骤 9：创建 generated image public URL case**

创建 `evals/cases/capability_rendering_contract/render_generated_image_public_url_001.json`：

```json
{
  "id": "render_generated_image_public_url_001",
  "suite": "rendering_contract",
  "description": "generated image 有 public base URL 时渲染为 CQ image。",
  "input": {
    "public_base_url": "https://nanobot.example.test",
    "generated_image_token": "eval-token",
    "envelope": {
      "messages": [
        {"type": "image", "generated_image_id": "img_public_001"}
      ]
    }
  },
  "expected": {
    "should_reply": true,
    "must_contain": [
      "[CQ:image,file=https://nanobot.example.test/api/v1/generated-images/img_public_001/image?token=eval-token]"
    ],
    "must_not_contain": ["[generated_image:img_public_001]", "base64://"]
  },
  "tags": ["capability", "rendering_contract", "generated_image", "public_url"]
}
```

- [ ] **步骤 10：创建 generated image 无 public URL case**

创建 `evals/cases/capability_rendering_contract/render_generated_image_without_public_url_001.json`：

```json
{
  "id": "render_generated_image_without_public_url_001",
  "suite": "rendering_contract",
  "description": "generated image 缺少 public URL 时保留 token 并记录 warning。",
  "input": {
    "envelope": {
      "messages": [
        {"type": "image", "generated_image_id": "img_missing_001"}
      ]
    }
  },
  "expected": {
    "should_reply": true,
    "must_contain": [
      "[generated_image:img_missing_001]",
      "generated_image_without_public_url:img_missing_001"
    ],
    "must_not_contain": ["base64://"]
  },
  "tags": ["capability", "rendering_contract", "generated_image", "fallback"]
}
```

- [ ] **步骤 11：创建 reply meta 保留 case**

创建 `evals/cases/capability_rendering_contract/render_reply_meta_preserved_001.json`：

```json
{
  "id": "render_reply_meta_preserved_001",
  "suite": "rendering_contract",
  "description": "渲染后保留 quote 与 mentions 元信息，并清理内部字段。",
  "input": {
    "envelope": {
      "messages": [{"type": "text", "text": "收到"}],
      "reply_meta": {
        "send_mode": "quote",
        "reply_to_message_id": "msg-77",
        "mentions": ["10001"],
        "_agent_result": "drop"
      }
    }
  },
  "expected": {
    "should_reply": true,
    "send_mode": "quote",
    "reply_to_message_id": "msg-77",
    "mentions": ["10001"],
    "must_contain": ["收到"],
    "must_not_contain": ["_agent_result"]
  },
  "tags": ["capability", "rendering_contract", "reply_meta"]
}
```

- [ ] **步骤 12：创建 rendering baseline**

创建 `evals/baselines/capability_rendering_contract.json`：

```json
{
  "suite": "capability_rendering_contract",
  "total": 5,
  "passed": 5,
  "failed": 0,
  "pass_rate": 1.0,
  "failed_cases": []
}
```

- [ ] **步骤 13：运行 rendering 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_rendering_contract_expected_preset_uses_scoreable_fields \
tests/test_eval_baseline.py::test_capability_rendering_contract_dataset_runs_offline \
-v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 14：运行渲染相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_qq_outbound_renderer.py tests/test_push_envelope.py \
-v -p no:cacheprovider
```

预期：PASS，确认新增 eval runner 未修改生产 renderer / push 合同。

- [ ] **步骤 15：运行 rendering baseline gate**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_rendering_contract \
--baseline evals/baselines/capability_rendering_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

预期：退出码 0，输出包含 `Gate passed`。

- [ ] **步骤 16：同步 P4-3B 文档状态**

在 `docs/evals.md` 的能力契约数据集规划段落补充 rendering gate 命令：

````markdown
Rendering contract gate：

```bash
python -B -m evals.run --suite capability_rendering_contract --baseline evals/baselines/capability_rendering_contract.json --min-pass-rate 1.0 --max-new-failures 0
```
````

在 `docs/todo.md` 与 `docs/plan_walkthrough.md` 标记 P4-3B rendering runner / dataset 已完成，并记录步骤 13、步骤 14、步骤 15 的验证结果。

- [ ] **步骤 17：提交 P4-3B**

运行：

```bash
git add \
evals/run.py \
evals/runners/rendering_runner.py \
evals/expected_contract.py \
tests/test_eval_baseline.py \
tests/test_eval_candidate_contract.py \
evals/cases/capability_rendering_contract/render_text_html_order_001.json \
evals/cases/capability_rendering_contract/render_image_url_as_cq_001.json \
evals/cases/capability_rendering_contract/render_generated_image_public_url_001.json \
evals/cases/capability_rendering_contract/render_generated_image_without_public_url_001.json \
evals/cases/capability_rendering_contract/render_reply_meta_preserved_001.json \
evals/baselines/capability_rendering_contract.json \
docs/evals.md \
docs/todo.md \
docs/plan_walkthrough.md
git commit -m "feat(评测): 扩展渲染契约数据集"
```

---

## 任务 3：P4-3C 收口验证

**文件：**
- 修改：`.Codex/plans/capability-contract-eval-datasets.md`
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：运行 P4-3 定向测试集合**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py tests/test_eval_baseline.py \
-v -p no:cacheprovider
```

结果：PASS，`34 passed, 21 warnings in 3.10s`，覆盖 P4-3 新增测试。

- [x] **步骤 2：运行两个能力数据集 gate**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_reply_contract \
--baseline evals/baselines/capability_reply_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

结果：退出码 0，输出 `Suite: capability_reply_contract total=3 passed=3 failed=0 pass_rate=100.0%` 和 `Gate passed`。

再运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_rendering_contract \
--baseline evals/baselines/capability_rendering_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

结果：退出码 0，输出 `Suite: capability_rendering_contract total=5 passed=5 failed=0 pass_rate=100.0%` 和 `Gate passed`。

- [x] **步骤 3：运行渲染相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_qq_outbound_renderer.py tests/test_push_envelope.py \
-v -p no:cacheprovider
```

结果：PASS，`17 passed, 1 warning in 0.72s`。

- [x] **步骤 4：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

结果：0 failures，`1353 passed, 6 skipped, 139 warnings in 99.23s`。

- [x] **步骤 5：同步最终文档状态**

在 `docs/plan_walkthrough.md` 更新 P4-3：

```markdown
状态：P4-3 已完成。P4-3A Reply Contract 数据集、P4-3B Rendering Contract runner / 数据集、两个能力数据集 baseline gate、渲染相邻回归和全量回归均已完成。
```

在 `docs/todo.md` 的路线项 8 更新现状：

```markdown
P4-3 已完成 `capability_reply_contract` / `capability_rendering_contract` 数据集、baseline 和离线 gate。
```

在 `docs/evals.md` 保留两个能力数据集 gate 命令和 dataset / suite 说明。

- [x] **步骤 6：文档和 diff 自检**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" .Codex/plans/capability-contract-eval-datasets.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：无输出。

运行：

```bash
python - <<'PY'
from pathlib import Path
for path in [
    Path(".Codex/plans/capability-contract-eval-datasets.md"),
    Path("docs/evals.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
]:
    data = path.read_text(encoding="utf-8")
    if "\ufffd" in data:
        raise SystemExit(f"U+FFFD found in {path}")
print("U+FFFD scan passed")
PY
```

预期：输出 `U+FFFD scan passed`。

运行：

```bash
git diff --check -- \
.Codex/plans/capability-contract-eval-datasets.md \
docs/evals.md \
docs/todo.md \
docs/plan_walkthrough.md
```

预期：无输出。

- [x] **步骤 7：提交 P4-3C 收口**

运行：

```bash
git add .Codex/plans/capability-contract-eval-datasets.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口能力数据集状态"
```

---

## 总体验收命令

P4-3 完成后，以下命令都必须有新鲜输出证据：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py tests/test_eval_baseline.py \
-v -p no:cacheprovider
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_qq_outbound_renderer.py tests/test_push_envelope.py \
-v -p no:cacheprovider
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_reply_contract \
--baseline evals/baselines/capability_reply_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run \
--suite capability_rendering_contract \
--baseline evals/baselines/capability_rendering_contract.json \
--min-pass-rate 1.0 \
--max-new-failures 0
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```
