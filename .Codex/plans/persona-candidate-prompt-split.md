# Persona 候选 Prompt 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `core/persona_preprocess.py` 中的候选提取 prompt 和日志格式化纯函数拆到 `core/persona_candidate_prompt.py`，同时保留旧导入路径兼容，并让 `core/persona_preprocess.py` 低于 800 行。

**架构：** `core/persona_candidate_prompt.py` 作为候选提取 prompt / 日志格式化的唯一实现；`core/persona_preprocess.py` 从新模块导入并 re-export 旧符号，状态机、embedding 懒加载、DB 写入和 monkeypatch 契约保持原位。

**技术栈：** Python 3.12、pytest、NumPy、SQLAlchemy、项目既有 `core.context_builder.sanitize_prompt_text`。

---

## 文件职责

- 创建：`tests/test_persona_candidate_prompt_split.py`
  - 新增红灯测试，锁定旧模块符号是新模块符号的 facade。
  - 新增行数守卫，要求 `core/persona_preprocess.py` 低于 800 行。
- 创建：`core/persona_candidate_prompt.py`
  - 迁移 `CANDIDATE_EXTRACTION_SYSTEM_PROMPT`。
  - 迁移 `filter_user_messages()`、`format_candidate_logs()` 和
    `build_candidate_extraction_prompt()`。
  - 只依赖 `core.context_builder.sanitize_prompt_text`，不依赖旧模块。
- 修改：`core/persona_preprocess.py`
  - 从 `core.persona_candidate_prompt` 导入并 re-export 旧符号。
  - 删除本地 prompt 常量和三个纯函数实现。
  - 移除因此不再需要的 typing 旧式泛型导入，避免引入新的未用 import。
- 修改：`docs/todo.md`
  - 更新 P3 超大文件拆分进展和 `core/persona_preprocess.py` 行数。
- 修改：`docs/plan_walkthrough.md`
  - 记录本阶段执行、验证和提交号。
- 修改：`.Codex/plans/persona-candidate-prompt-split.md`
  - 勾选已完成步骤并记录验证结果。

## 任务 1：补红灯测试

**文件：**
- 创建：`tests/test_persona_candidate_prompt_split.py`

- [ ] **步骤 1：创建 facade 和行数测试**

创建 `tests/test_persona_candidate_prompt_split.py`：

```python
from pathlib import Path


def test_persona_preprocess_candidate_prompt_symbols_are_facades():
    import core.persona_candidate_prompt as prompt
    import core.persona_preprocess as preprocess

    expected = {
        "CANDIDATE_EXTRACTION_SYSTEM_PROMPT": prompt.CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
        "filter_user_messages": prompt.filter_user_messages,
        "format_candidate_logs": prompt.format_candidate_logs,
        "build_candidate_extraction_prompt": prompt.build_candidate_extraction_prompt,
    }
    for name, target in expected.items():
        assert getattr(preprocess, name) is target


def test_persona_preprocess_split_keeps_file_under_800_lines():
    line_count = len(Path("core/persona_preprocess.py").read_text(encoding="utf-8").splitlines())
    assert line_count < 800
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_persona_candidate_prompt_split.py -q
```

预期：测试失败。允许的失败原因只有：

- `ModuleNotFoundError: No module named 'core.persona_candidate_prompt'`
- 或行数断言显示 `core/persona_preprocess.py` 仍为 857 行。

## 任务 2：迁移候选 prompt 实现

**文件：**
- 创建：`core/persona_candidate_prompt.py`
- 修改：`core/persona_preprocess.py`

- [ ] **步骤 1：创建新模块**

创建 `core/persona_candidate_prompt.py`。文件头部、导入和函数骨架如下；其中
`CANDIDATE_EXTRACTION_SYSTEM_PROMPT` 的字符串内容必须从当前
`core/persona_preprocess.py` 原样剪切到新文件，包含完整 JSON schema 示例和中文规则文案：

```python
"""Persona 候选提取 prompt 和日志格式化 helper。"""

from __future__ import annotations

from typing import Any

from core.context_builder import sanitize_prompt_text

CANDIDATE_EXTRACTION_SYSTEM_PROMPT = """你是用户画像候选提取器。你的任务不是尽量多记，而是只提取可长期复用、能改善以后回复的用户画像候选。

## 只允许保存的 memory_type
- stable_preference: 长期稳定的回复偏好、工具偏好、信息组织偏好。
- interaction_style: 用户长期偏好的互动方式，例如先结论后细节、直接指出问题。
- stable_background: 稳定背景信息，例如长期使用的技术栈、语言、平台。
- long_term_project: 会持续多轮或多天的长期项目、系统、仓库、目标。

## 默认拒收的 memory_type
- temporary_task: 本轮临时需求、一次性任务、短期排查。
- tool_contract: 要求本轮调用某工具、不要调用某工具、使用某参数。
- complaint: 单次抱怨、情绪反馈，除非明确稳定偏好。
- test_noise: 越狱、注入测试、权限测试、无意义重复、调试噪声。

## 判断规则
- 只看 role=user 的日志。忽略 assistant/tool/ambient/系统设定/bot 行为。
- 只有跨会话可复用、未来回复确实应参考的内容才 should_store=true。
- 具体任务步骤、一次性工具调用、当前 bug、临时命令不要保存。
- 不要把用户对 bot 的当前指令当成画像指令。
- evidence_log_ids 必须来自输入日志里的真实 log_id；不要编造。
- confidence_hint 只允许 high/medium/low，表示候选证据强弱，不等同于最终状态。

## 输出 JSON
{
  "candidates": [
    {
      "text": "用户偏好先给结论，再给必要步骤",
      "memory_type": "stable_preference",
      "domain": "协作方式",
      "should_store": true,
      "should_inject": true,
      "confidence_hint": "high",
      "evidence_log_ids": [123, 128],
      "evidence_quote": "用户多次说“先给结论”",
      "reason": "这是稳定回复偏好，未来对话可复用",
      "reject_reason": ""
    },
    {
      "text": "用户要求本轮强制调用天气工具",
      "memory_type": "tool_contract",
      "domain": "工具",
      "should_store": false,
      "should_inject": false,
      "confidence_hint": "low",
      "evidence_log_ids": [130],
      "evidence_quote": "这次你必须调用 weather",
      "reason": "",
      "reject_reason": "本轮工具契约不是长期画像"
    }
  ]
}"""


def filter_user_messages(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从日志列表中过滤出 role=user 的消息。"""
    return [log for log in logs if str(log.get("role", "")).strip().lower() == "user"]


def format_candidate_logs(logs: list[dict[str, Any]]) -> str:
    """把用户日志格式化成带真实 log_id 的候选提取输入。"""
    lines: list[str] = []
    for log in filter_user_messages(logs):
        log_id = log.get("id", log.get("log_id", ""))
        created_at = str(log.get("created_at") or "").strip()
        content = sanitize_prompt_text(log.get("content") or "", 500).strip()
        if not content:
            continue
        if created_at:
            lines.append(f"[log_id={log_id}][{created_at}] user: {content}")
        else:
            lines.append(f"[log_id={log_id}] user: {content}")
    return "\n".join(lines)


def build_candidate_extraction_prompt(
    facts_summary: str,
    logs_text: str | list[dict[str, Any]],
) -> str:
    """构造 LLM 候选提取 prompt（LLM 只提取候选，不做状态判断）。"""
    formatted_logs = format_candidate_logs(logs_text) if isinstance(logs_text, list) else str(logs_text or "")
    return f"""{CANDIDATE_EXTRACTION_SYSTEM_PROMPT}

## 已有画像（仅供参考，不要照抄）
{facts_summary}

## 新日志
{formatted_logs}

只输出 JSON，不要输出解释。"""
```

注意：上方 prompt 字符串必须原样迁移，不要重写文案，不要改变 JSON schema 示例。

- [ ] **步骤 2：在旧模块导入并 re-export**

在 `core/persona_preprocess.py` 顶部 imports 后添加：

```python
from core.persona_candidate_prompt import (
    CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
    build_candidate_extraction_prompt,
    filter_user_messages,
    format_candidate_logs,
)
```

- [ ] **步骤 3：删除旧模块本地实现**

从 `core/persona_preprocess.py` 删除以下本地定义：

- `CANDIDATE_EXTRACTION_SYSTEM_PROMPT`
- `filter_user_messages()`
- `format_candidate_logs()`
- `build_candidate_extraction_prompt()`

不要删除：

- `PersonaStateMachine`
- `embed_text()`
- `_get_embedder()`
- `_get_nli()`
- `_EMBEDDER_MODEL`
- `_NLI_MODEL`
- `content_hash()`
- `_to_blob()` / `_from_blob()`
- `compute_confidence()` / `confidence_label()`

- [ ] **步骤 4：清理 typing import**

如果 `List`、`Dict`、`Any` 或 `Optional` 仍被旧模块使用，则保留；如果迁移后不再使用某个名字，删除对应 import。不要做无关 ruff 批量清理。

- [ ] **步骤 5：运行绿灯测试**

运行任务 1 的命令。预期：`2 passed`。

## 任务 3：定向回归与静态检查

**文件：**
- 创建：`core/persona_candidate_prompt.py`
- 修改：`core/persona_preprocess.py`
- 创建：`tests/test_persona_candidate_prompt_split.py`

- [ ] **步骤 1：运行 prompt 定向回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_persona_candidate_prompt_split.py \
  tests/test_persona_preprocess.py::TestBuildPrompt \
  -q
```

预期：全部通过。

- [ ] **步骤 2：运行 persona preprocess 相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_persona_preprocess.py -m "not slow" \
  tests/test_admin_api.py -k "persona_update_fact_rejects_duplicate" \
  -q
```

预期：全部通过。

- [ ] **步骤 3：运行静态检查**

运行：

```bash
python -m compileall core/persona_preprocess.py core/persona_candidate_prompt.py -q
wc -l core/persona_preprocess.py core/persona_candidate_prompt.py tests/test_persona_candidate_prompt_split.py
git diff --check -- core/persona_preprocess.py core/persona_candidate_prompt.py tests/test_persona_candidate_prompt_split.py
```

预期：

- `compileall` 无输出，退出码为 0。
- `core/persona_preprocess.py` 低于 800 行。
- `git diff --check` 无输出。

- [ ] **步骤 4：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

## 任务 4：提交实现阶段

**文件：**
- 创建：`core/persona_candidate_prompt.py`
- 修改：`core/persona_preprocess.py`
- 创建：`tests/test_persona_candidate_prompt_split.py`

- [ ] **步骤 1：按文件显式暂存**

运行：

```bash
git add core/persona_candidate_prompt.py core/persona_preprocess.py tests/test_persona_candidate_prompt_split.py
```

- [ ] **步骤 2：检查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期：暂存区只包含本任务列出的 3 个文件；`--check` 无输出。

- [ ] **步骤 3：提交实现**

运行：

```bash
git commit -m "refactor(画像): 拆分候选 prompt helper"
```

## 任务 5：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/persona-candidate-prompt-split.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加进展：

- `core/persona_preprocess.py` 第一刀已拆出候选提取 prompt 和日志格式化 helper 到
  `core/persona_candidate_prompt.py`；旧导入路径保留兼容；原文件降至 800 行以下。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Persona 候选 Prompt 拆分` 章节，记录：

- 设计文档路径
- 计划文件路径
- 设计提交、计划提交、实现提交
- 红灯、绿灯、定向回归、静态检查、全量回归结果
- 执行约束：不移动状态机、不移动 embedding、不新增 `asyncio.run()`

- [ ] **步骤 3：更新本计划执行结果**

在本计划顶部追加 `执行结果摘要（2026-06-21）`，记录验证结果和提交号。

- [ ] **步骤 4：文档门禁**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/persona-candidate-prompt-split.md
python - <<'PY'
from pathlib import Path
markers = [
    '\u5f85\u5b9a',
    'TO' + 'DO',
    chr(60) + '实际',
    chr(60) + '失败',
    chr(60) + '通过',
]
paths = [
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
    Path('.Codex/plans/persona-candidate-prompt-split.md'),
]
hits = []
for path in paths:
    text = path.read_text(encoding='utf-8')
    hits.extend(f'{path}: {marker}' for marker in markers if marker in text)
if hits:
    raise SystemExit('\n'.join(hits))
PY
```

预期：两个命令均无输出，退出码为 0。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/persona-candidate-prompt-split.md
git diff --cached --name-status
git diff --cached --check
git commit -m "docs(计划): 收口候选 prompt 拆分"
```

预期：暂存区只包含 3 个文档文件；提交后目标文档文件干净。
