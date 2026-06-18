# P2-3 QQ 出站渲染契约实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 建立集中式 QQ 出站 renderer，让响应信封里的 `messages` / `reply` / `reply_meta` 稳定渲染为 QQbot 旧 `message` 字符串，同时保留旧字段和旧 push 签名。

**架构：** `core/qq_outbound_renderer.py` 是唯一新渲染边界，负责把响应信封转换成内部渲染结果和 legacy QQ message。`core/daily_digest.push_envelope_to_qq()` 改为调用 renderer，`push_to_qq(target_type, target_id, message)` 保持旧签名；schedule task、API 断连 push 和手动任务 run 继续走响应信封，不再各自展开 generated image。Prompt 文档同步短 token 与 renderer 的职责边界。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy、pytest、in-memory SQLite、现有 OneBot CQ 字符串兼容层。

---

## 当前事实

- 设计文档：`docs/superpowers/specs/2026-06-18-qq-outbound-rendering-contract-design.md`，提交 `c72ddb3 docs(渲染): 设计 QQ 出站契约`。
- 当前计划文件路径按项目约定使用 `.Codex/plans/qq-outbound-rendering-contract.md`。
- P2-2 响应信封已完成：`core/message_envelope.py` 输出 `reply`、`messages`、`reply_meta` 和 `meta`，旧字段兼容保留。
- P2-2.5 `client_meta` 已完成：`platform` 缺省 `qq`，`chat_type` 与入口事实一致，trace 字段已校验和裁剪。
- `push_envelope_to_qq()` 当前仍通过 `envelope_to_message()` 派生 legacy `message`，因此会忽略 `messages` 中非文本项。
- `push_to_qq(target_type, target_id, message) -> bool` 旧签名必须保持。
- `[generated_image:<id>]` 当前由散落的 `expand_generated_image_refs_in_content(..., allow_base64=False)` 调用展开。renderer 接管后，QQ-facing 路径仍禁止 `base64://`。
- `[sticker:<id>]` 当前通常在 `ReplyTool` 层提前展开。renderer 仍要兼容该短 token，避免绕过 reply 工具的出口丢表情。
- `creatures/nanobot/prompts/skills/schedule_task/tool.py` 的 `action == "run"` 仍直接调用 `push_to_qq()`，是 P2-3 必修绕过点。
- 工具说明存在两个物理根目录：`prompts.v2.default/tools/*/usage.md` 和 `data/prompts_v2/tools/*/usage.md`。两边必须同步修改。
- 现有无关脏文件包括 pycache、`docs/goal.md`、`tests/conftest.py`、`.codex/` 历史计划、`docs/TODO_LIST.md`、`nanobot.db` 等。执行本计划时不要回滚、删除或暂存这些文件。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `core/qq_outbound_renderer.py` | 新增 QQ 出站渲染纯函数和 `QQOutboundRenderResult`。 |
| `tests/test_qq_outbound_renderer.py` | 覆盖 renderer 的 text / html / image / sticker / CQ / 空信封行为。 |
| `core/daily_digest.py` | 让 `push_envelope_to_qq()` 使用 renderer；保留 `push_to_qq()` 旧签名。 |
| `tests/test_push_envelope.py` | 覆盖 push 信封渲染、generated image 展开和空 message 跳过。 |
| `creatures/nanobot/prompts/skills/schedule_task/tool.py` | 修复 `action == "run"` 直连旧 push 的绕过点。 |
| `tests/test_schedule_task_tool.py` | 覆盖 schedule task run 分支走响应信封和 renderer。 |
| `api/routes.py` | 只在必要时收敛手动任务 run / SSE 断连 push 的散落展开逻辑。 |
| `tests/test_api_push_envelope.py` | 覆盖 route push call site 不回退到旧 push。 |
| `app/group_ingress/service.py` | 只在测试发现群聊 rich message 被截断或破坏时调整。 |
| `tests/test_group_response_envelope.py` | 覆盖群聊生成图和 HTML 的响应信封边界。 |
| `prompts.v2.default/tools/*/usage.md` | 更新 reply / sticker_search / image_generation 工具说明。 |
| `data/prompts_v2/tools/*/usage.md` | 与 `prompts.v2.default` 同步的工具说明。 |
| `docs/message-field-standard.md` | 记录出站渲染契约和 P2-3 已实现边界。 |
| `docs/todo.md` | 同步路线项 7 状态。 |
| `docs/plan_walkthrough.md` | 同步当前阶段执行状态和验证记录。 |

## 并行执行策略

本计划适合子 agent 驱动，但必须先完成任务 1，因为所有出口都依赖 renderer 接口。

| 角色 | 可修改文件 | 禁止修改 |
| --- | --- | --- |
| renderer owner | `core/qq_outbound_renderer.py`、`tests/test_qq_outbound_renderer.py` | `api/routes.py`、`core/daily_digest.py`、prompt 文档 |
| push owner | `core/daily_digest.py`、`tests/test_push_envelope.py` | `api/routes.py`、schedule task 工具 |
| schedule owner | `creatures/nanobot/prompts/skills/schedule_task/tool.py`、`tests/test_schedule_task_tool.py` | `api/routes.py`、prompt usage 文档 |
| route owner | `api/routes.py`、`tests/test_api_push_envelope.py`、`tests/test_group_response_envelope.py` | renderer 实现、prompt usage 文档 |
| prompt owner | 6 个工具 usage 文档、prompt 一致性扫描 | 生产代码 |
| 文档 owner | `docs/message-field-standard.md`、`docs/todo.md`、`docs/plan_walkthrough.md`、本计划 | 生产代码 |

子 agent 提示词模板：

```markdown
你只负责本任务列出的文件。不得修改未列入的文件。
先写红灯测试并运行指定命令，确认失败原因与计划一致。
再写最小实现，运行定向测试和任务指定回归。
不要使用 git add . 或 git add -A，只暂存本任务文件。
commit message 使用中文 Conventional Commit。
返回：红灯输出摘要、绿灯输出摘要、提交号、改动文件列表、仍需主线程集成的点。
```

任务依赖：

- 任务 1 必须先完成并提交。
- 任务 2、任务 3、任务 5 可在任务 1 后并行。
- 任务 4 依赖任务 2，因为 route push 的最终行为以 `push_envelope_to_qq()` 为准。
- 任务 6 依赖任务 1 和任务 2，因为 prompt 文案要描述已经存在的 renderer 边界。
- 任务 7 必须最后执行，负责文档状态、全量验证和收口提交。

## 共享接口契约

任务 1 提交后，后续任务只使用以下接口：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class QQOutboundRenderResult:
    message: str
    messages: list[dict[str, Any]]
    reply_meta: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def render_qq_outbound_envelope(
    envelope: Mapping[str, Any] | None,
    *,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    ...


def render_qq_message_items(
    messages: list[Mapping[str, Any]],
    *,
    reply_meta: Mapping[str, Any] | None = None,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    ...
```

规则：

- `allow_base64` 默认且生产调用保持 `False`。
- `messages` 非空时按数组顺序渲染；为空时从 `reply` 派生一个 `text` message。
- `text` 和 `html` 都使用 `text` 字段。
- `image.url` 渲染 `[CQ:image,file=<url>]`。
- `image.generated_image_id` 和正文里的 `[generated_image:<id>]` 通过 `public_generated_image_url()` 查公开 URL；无 URL 时保留短 token 并记录 warning。
- 正文里的 `[sticker:<id>]` 通过 `expand_sticker_refs_in_content()` 兼容展开；直接 CQ 码保持原样。
- `reply_meta` 使用 `sanitize_reply_meta()` 过滤后放入 result，不在第一刀派生 CQ at / reply。

## 任务 1：新增 QQ 出站 renderer

**文件：**
- 创建：`core/qq_outbound_renderer.py`
- 创建：`tests/test_qq_outbound_renderer.py`

- [x] **步骤 1：编写 renderer 红灯测试**

创建 `tests/test_qq_outbound_renderer.py`：

```python
from core.qq_outbound_renderer import render_qq_outbound_envelope
from core.qq_outbound_renderer import render_qq_message_items


def test_render_text_and_html_in_order():
    result = render_qq_message_items(
        [
            {"type": "text", "text": "A"},
            {"type": "html", "text": "<article>B</article>"},
        ]
    )

    assert result.message == "A\n<article>B</article>"
    assert result.messages == [
        {"type": "text", "text": "A"},
        {"type": "html", "text": "<article>B</article>"},
    ]


def test_render_falls_back_to_reply_when_messages_empty():
    result = render_qq_outbound_envelope({"reply": "你好", "messages": []})

    assert result.message == "你好"
    assert result.messages == [{"type": "text", "text": "你好"}]


def test_render_image_url_as_cq_image():
    result = render_qq_message_items(
        [{"type": "image", "url": "https://example.test/a.png"}]
    )

    assert result.message == "[CQ:image,file=https://example.test/a.png]"


def test_render_generated_image_token_uses_public_url(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: f"https://cdn.test/{image_id}.png",
    )

    result = render_qq_outbound_envelope(
        {"reply": "图：[generated_image:img_1]", "messages": []}
    )

    assert result.message == "图：[CQ:image,file=https://cdn.test/img_1.png]"
    assert result.warnings == []


def test_render_generated_image_without_public_url_keeps_token(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: None,
    )

    result = render_qq_outbound_envelope(
        {"reply": "[generated_image:img_1]", "messages": []}
    )

    assert result.message == "[generated_image:img_1]"
    assert "base64://" not in result.message
    assert result.warnings == ["generated_image_without_public_url:img_1"]


def test_render_keeps_reply_meta_without_deriving_cq_at():
    result = render_qq_outbound_envelope(
        {
            "reply": "你好",
            "messages": [],
            "reply_meta": {
                "send_mode": "quote",
                "mentions": ["10001"],
                "at_sender": True,
                "_agent_result": "drop",
            },
        }
    )

    assert result.message == "你好"
    assert result.reply_meta == {
        "send_mode": "quote",
        "mentions": ["10001"],
        "at_sender": True,
    }
    assert "[CQ:at" not in result.message


def test_render_empty_envelope_returns_empty_message():
    result = render_qq_outbound_envelope(None)

    assert result.message == ""
    assert result.messages == []
```

- [x] **步骤 2：运行 renderer 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_qq_outbound_renderer.py -v -p no:cacheprovider
```

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'core.qq_outbound_renderer'`。

- [x] **步骤 3：实现最小 renderer**

创建 `core/qq_outbound_renderer.py`：

```python
"""QQ 出站响应信封渲染。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.generated_images import public_generated_image_url
from core.message_envelope import sanitize_reply_meta
from core.sticker_memory import expand_sticker_refs_in_content

_GENERATED_IMAGE_RE = re.compile(r"\[generated_image:([A-Za-z0-9_.:-]+)\]")


@dataclass(slots=True)
class QQOutboundRenderResult:
    message: str
    messages: list[dict[str, Any]]
    reply_meta: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def render_qq_outbound_envelope(
    envelope: Mapping[str, Any] | None,
    *,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    if not envelope:
        return QQOutboundRenderResult(message="", messages=[], reply_meta={})

    raw_messages = envelope.get("messages")
    messages = _normalize_messages(raw_messages)
    if not messages:
        reply = str(envelope.get("reply") or "")
        if reply:
            messages = [{"type": "text", "text": reply}]

    return render_qq_message_items(
        messages,
        reply_meta=envelope.get("reply_meta"),
        allow_base64=allow_base64,
    )


def render_qq_message_items(
    messages: list[Mapping[str, Any]],
    *,
    reply_meta: Mapping[str, Any] | None = None,
    allow_base64: bool = False,
) -> QQOutboundRenderResult:
    rendered: list[str] = []
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []

    for item in messages:
        normalized_item = dict(item)
        normalized.append(normalized_item)
        text = _render_item(normalized_item, allow_base64=allow_base64, warnings=warnings)
        if text:
            rendered.append(text)

    return QQOutboundRenderResult(
        message="\n".join(rendered),
        messages=normalized,
        reply_meta=sanitize_reply_meta(reply_meta),
        warnings=warnings,
    )


def _normalize_messages(raw_messages: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_messages, list):
        return []
    return [dict(item) for item in raw_messages if isinstance(item, Mapping)]


def _render_item(
    item: Mapping[str, Any],
    *,
    allow_base64: bool,
    warnings: list[str],
) -> str:
    item_type = str(item.get("type") or "text")
    if item_type in {"text", "html"}:
        return _render_text(str(item.get("text") or ""), allow_base64=allow_base64, warnings=warnings)
    if item_type == "image":
        return _render_image(item, warnings=warnings)
    return ""


def _render_text(text: str, *, allow_base64: bool, warnings: list[str]) -> str:
    expanded = _GENERATED_IMAGE_RE.sub(
        lambda match: _render_generated_image_token(match.group(1), warnings=warnings),
        text,
    )
    return expand_sticker_refs_in_content(expanded)


def _render_image(item: Mapping[str, Any], *, warnings: list[str]) -> str:
    url = str(item.get("url") or "")
    if url:
        return f"[CQ:image,file={url}]"
    image_id = str(item.get("generated_image_id") or "")
    if image_id:
        return _render_generated_image_token(image_id, warnings=warnings)
    return ""


def _render_generated_image_token(image_id: str, *, warnings: list[str]) -> str:
    url = public_generated_image_url(image_id)
    if url:
        return f"[CQ:image,file={url}]"
    warnings.append(f"generated_image_without_public_url:{image_id}")
    return f"[generated_image:{image_id}]"
```

注意：`allow_base64` 在首版作为显式参数保留，但生产路径必须传默认值 `False`。如果后续确实要支持 base64，需要另起任务并补安全说明。

- [x] **步骤 4：运行 renderer 绿灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_qq_outbound_renderer.py -v -p no:cacheprovider
```

预期：PASS，至少 `7 passed`。

- [x] **步骤 5：运行相邻回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_qq_outbound_renderer.py \
  tests/test_message_envelope.py \
  tests/test_sticker_tool.py \
  tests/test_image_generation_tool.py \
  -v -p no:cacheprovider
```

预期：PASS。若 `tests/test_sticker_tool.py` 或 `tests/test_image_generation_tool.py` 文件名不同，先用 `rg --files tests | rg 'sticker|image_generation'` 查真实文件，再只替换命令里的路径。

- [x] **步骤 6：Commit**

运行：

```bash
git add core/qq_outbound_renderer.py tests/test_qq_outbound_renderer.py
git commit -m "feat(渲染): 添加 QQ 出站渲染器"
```

## 任务 2：让 push 信封使用 renderer

**文件：**
- 修改：`core/daily_digest.py`
- 修改：`tests/test_push_envelope.py`

- [ ] **步骤 1：编写 push 红灯测试**

在 `tests/test_push_envelope.py` 增加：

```python
@pytest.mark.asyncio
async def test_push_envelope_renders_generated_image_token(monkeypatch):
    sent = {}

    async def fake_push_to_qq(target_type, target_id, message):
        sent["target_type"] = target_type
        sent["target_id"] = target_id
        sent["message"] = message
        return True

    monkeypatch.setattr("core.daily_digest.push_to_qq", fake_push_to_qq)
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: f"https://cdn.test/{image_id}.png",
    )

    ok = await push_envelope_to_qq(
        "group",
        "123",
        {
            "reply": "[generated_image:img_1]",
            "messages": [{"type": "text", "text": "[generated_image:img_1]"}],
            "reply_meta": {},
        },
    )

    assert ok is True
    assert sent == {
        "target_type": "group",
        "target_id": "123",
        "message": "[CQ:image,file=https://cdn.test/img_1.png]",
    }


@pytest.mark.asyncio
async def test_push_envelope_never_sends_base64_when_public_url_missing(monkeypatch):
    sent = {}

    async def fake_push_to_qq(target_type, target_id, message):
        sent["message"] = message
        return True

    monkeypatch.setattr("core.daily_digest.push_to_qq", fake_push_to_qq)
    monkeypatch.setattr("core.qq_outbound_renderer.public_generated_image_url", lambda image_id: None)

    ok = await push_envelope_to_qq(
        "group",
        "123",
        {"reply": "[generated_image:img_1]", "messages": []},
    )

    assert ok is True
    assert sent["message"] == "[generated_image:img_1]"
    assert "base64://" not in sent["message"]
```

- [ ] **步骤 2：运行 push 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_push_envelope.py -v -p no:cacheprovider
```

预期：FAIL，新测试收到的 `message` 仍是 `[generated_image:img_1]` 或非 renderer 输出。

- [ ] **步骤 3：修改 `push_envelope_to_qq()`**

在 `core/daily_digest.py` 中把 `envelope_to_message()` 调用改为：

```python
from core.qq_outbound_renderer import render_qq_outbound_envelope


async def push_envelope_to_qq(
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any] | None,
) -> bool:
    rendered = render_qq_outbound_envelope(envelope, allow_base64=False)
    message = rendered.message
    if not message:
        logger.info("Skip QQ push because rendered message is empty")
        return False
    if rendered.warnings:
        logger.warning("QQ outbound render warnings: %s", rendered.warnings)
    return await push_to_qq(target_type, target_id, message)
```

保留现有函数的参数名和返回 bool 语义。不要修改 `push_to_qq()` 签名。

- [ ] **步骤 4：运行 push 绿灯与定时任务回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_push_envelope.py \
  tests/test_daily_digest.py \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

运行：

```bash
git add core/daily_digest.py tests/test_push_envelope.py
git commit -m "feat(推送): 使用 QQ 出站渲染器"
```

## 任务 3：修复 schedule task run 绕过点

**文件：**
- 修改：`creatures/nanobot/prompts/skills/schedule_task/tool.py`
- 修改：`tests/test_schedule_task_tool.py`

- [ ] **步骤 1：编写 schedule run 红灯测试**

在 `tests/test_schedule_task_tool.py` 增加或调整测试，核心断言如下：

```python
@pytest.mark.asyncio
async def test_schedule_task_run_uses_push_envelope(monkeypatch, db_session):
    calls = []

    async def fake_push_envelope_to_qq(target_type, target_id, envelope):
        calls.append((target_type, target_id, envelope))
        return True

    async def forbidden_push_to_qq(*args, **kwargs):
        raise AssertionError("schedule_task run must not call push_to_qq directly")

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.schedule_task.tool.push_envelope_to_qq",
        fake_push_envelope_to_qq,
    )
    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.schedule_task.tool.push_to_qq",
        forbidden_push_to_qq,
    )

    # 使用测试文件现有 helper 创建一条可 run 的任务，然后执行 action="run"。
    result = await run_schedule_tool_action(
        action="run",
        task_id="task-1",
        db=db_session,
    )

    assert result["ok"] is True
    assert calls
    target_type, target_id, envelope = calls[0]
    assert target_type in {"private", "group"}
    assert target_id
    assert envelope["reply"]
    assert envelope["messages"]
```

如果测试文件没有 `run_schedule_tool_action` helper，按现有 fixture 调用真实 `ScheduleTaskTool._execute()`；不要新增与生产代码脱节的 fake 工具入口。

- [ ] **步骤 2：运行 schedule 红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_schedule_task_tool.py -v -p no:cacheprovider
```

预期：FAIL，失败原因是直接调用了被禁止的 `push_to_qq()`，或模块没有导入 `push_envelope_to_qq`。

- [ ] **步骤 3：修改 schedule task run 分支**

在 `creatures/nanobot/prompts/skills/schedule_task/tool.py` 中引入：

```python
from core.message_envelope import build_chat_response_envelope
from core.daily_digest import push_envelope_to_qq
```

把 `action == "run"` 的直接 push 改为：

```python
envelope = build_chat_response_envelope(
    status="ok",
    answer=message,
    meta={
        "platform": "qq",
        "chat_type": "private" if target_type == "private" else "group",
        "source": "schedule_task_tool",
        "task_id": task_id,
    },
)
ok = await push_envelope_to_qq(target_type, target_id, envelope)
```

如果该工具当前运行在同步 `_execute()` 中，不要新增 `asyncio.run()`。应保持现有 async 调用链，必要时把工具方法改为 `async def` 并更新测试，避免在同步函数里强行跑 awaitable。

- [ ] **步骤 4：运行 schedule 绿灯与 push 回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_schedule_task_tool.py \
  tests/test_push_envelope.py \
  tests/test_daily_digest.py \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

运行：

```bash
git add creatures/nanobot/prompts/skills/schedule_task/tool.py tests/test_schedule_task_tool.py
git commit -m "fix(定时任务): 统一运行推送出口"
```

## 任务 4：收敛 route push 回归

**文件：**
- 修改：`api/routes.py`
- 修改：`tests/test_api_push_envelope.py`
- 修改：`tests/test_group_response_envelope.py`

- [ ] **步骤 1：补 route push 回归测试**

在 `tests/test_api_push_envelope.py` 增加断言：route 手动任务 run 和 SSE 断连后台 push 不直接调用 `push_to_qq()`。

```python
@pytest.mark.asyncio
async def test_route_background_push_uses_envelope_renderer(monkeypatch, async_client):
    pushed = []

    async def fake_push_envelope_to_qq(target_type, target_id, envelope):
        pushed.append((target_type, target_id, envelope))
        return True

    async def forbidden_push_to_qq(*args, **kwargs):
        raise AssertionError("route push must use push_envelope_to_qq")

    monkeypatch.setattr("api.routes.push_envelope_to_qq", fake_push_envelope_to_qq)
    monkeypatch.setattr("api.routes.push_to_qq", forbidden_push_to_qq, raising=False)

    # 复用当前测试文件已有的断连或手动任务 helper 触发后台 push。
    await trigger_existing_background_push(async_client)

    assert pushed
    assert pushed[0][2]["messages"]
```

如果现有测试已有相同行为覆盖，只把断言扩展为检查 renderer 入口，不复制一套新的大 fixture。

- [ ] **步骤 2：运行 route 红灯或确认既有覆盖**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_api_push_envelope.py -v -p no:cacheprovider
```

预期：在修改生产代码前，新增测试能证明当前 route 依赖信封入口；如果已经 PASS，记录为「既有 route 已满足，保留回归测试」。

- [ ] **步骤 3：按测试结果调整 route**

如果测试失败，仅修改 `api/routes.py` 中仍直连旧 push 或仍提前展开 generated image 的 call site：

```python
envelope = build_chat_response_envelope(
    status="ok",
    answer=answer,
    reply_meta=reply_meta,
    meta={
        "platform": platform,
        "chat_type": "private",
        "source": "stream_disconnect_push",
    },
)
await push_envelope_to_qq(target_type, target_id, envelope)
```

如果测试已经 PASS，不做生产代码改动，只提交回归测试。

- [ ] **步骤 4：运行 API / 群聊相关回归**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_api_push_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_api.py \
  tests/test_streaming_api.py \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

如果改了生产代码：

```bash
git add api/routes.py tests/test_api_push_envelope.py tests/test_group_response_envelope.py
git commit -m "refactor(推送): 收敛路由推送出口"
```

如果只补测试：

```bash
git add tests/test_api_push_envelope.py tests/test_group_response_envelope.py
git commit -m "test(推送): 固化路由信封出口"
```

## 任务 5：保护富媒体响应信封边界

**文件：**
- 修改：`tests/test_group_response_envelope.py`
- 修改：`tests/test_message_envelope.py`
- 按测试结果可修改：`core/message_envelope.py`、`app/group_ingress/service.py`

- [ ] **步骤 1：编写富媒体边界测试**

在 `tests/test_message_envelope.py` 增加：

```python
from core.qq_outbound_renderer import render_qq_outbound_envelope


def test_renderer_does_not_drop_image_messages_even_if_legacy_envelope_helper_ignores_them():
    envelope = {
        "reply": "",
        "messages": [{"type": "image", "url": "https://example.test/a.png"}],
        "reply_meta": {},
    }

    result = render_qq_outbound_envelope(envelope)

    assert result.message == "[CQ:image,file=https://example.test/a.png]"
```

在 `tests/test_group_response_envelope.py` 增加 HTML 不截断或不破坏的现有 service 测试，断言 `messages[0]["type"] == "html"` 且 `reply` 保留完整 HTML。

- [ ] **步骤 2：运行富媒体红灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_message_envelope.py \
  tests/test_group_response_envelope.py \
  -v -p no:cacheprovider
```

预期：如果 renderer 已在任务 1 处理 image，第一项 PASS；若群聊 HTML 仍被截断，相关测试 FAIL。

- [ ] **步骤 3：按失败点最小修改**

如果 `core/message_envelope.py` 需要扩展，不改变 `envelope_to_message()` 旧兼容行为，只确保 builder 能保留显式传入的 `messages`：

```python
def build_group_response_envelope(..., messages: list[Mapping[str, Any]] | None = None, ...):
    envelope_messages = [dict(item) for item in messages] if messages is not None else build_text_messages(reply)
```

如果 `app/group_ingress/service.py` 对 HTML 过早调用 `format_group_reply_for_transport()`，只在 `is_html_reply` 为 true 时跳过文本截断：

```python
transport_reply = answer if is_html_reply else format_group_reply_for_transport(answer, max_chars=4000)
```

- [ ] **步骤 4：运行富媒体绿灯**

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_message_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_qq_outbound_renderer.py \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：Commit**

运行：

```bash
git add tests/test_message_envelope.py tests/test_group_response_envelope.py core/message_envelope.py app/group_ingress/service.py
git commit -m "test(渲染): 保护富媒体信封边界"
```

如果某个生产文件没有变更，不要把它放进 `git add`。

## 任务 6：同步 prompt 工具说明

**文件：**
- 修改：`prompts.v2.default/tools/reply/usage.md`
- 修改：`prompts.v2.default/tools/sticker_search/usage.md`
- 修改：`prompts.v2.default/tools/image_generation/usage.md`
- 修改：`data/prompts_v2/tools/reply/usage.md`
- 修改：`data/prompts_v2/tools/sticker_search/usage.md`
- 修改：`data/prompts_v2/tools/image_generation/usage.md`

- [ ] **步骤 1：定位旧文案**

运行：

```bash
rg -n "reply_token|send_code|CQ:image|generated_image|sticker" \
  prompts.v2.default/tools/reply/usage.md \
  prompts.v2.default/tools/sticker_search/usage.md \
  prompts.v2.default/tools/image_generation/usage.md \
  data/prompts_v2/tools/reply/usage.md \
  data/prompts_v2/tools/sticker_search/usage.md \
  data/prompts_v2/tools/image_generation/usage.md
```

预期：输出当前短 token 和 CQ 码说明位置。

- [ ] **步骤 2：修改 6 个 usage 文档**

统一口径：

```markdown
`reply(content)` 可以包含自然语言、`[sticker:<id>]` 和 `[generated_image:<id>]`。
这些短 token 是 Nanobot 内部稳定引用。出口 renderer 会在 QQ 发送前把它们转换成可发送内容。
优先使用工具返回的 `reply_token`，不要手写 OneBot CQ 码；历史 CQ 码仍会被兼容。
```

对 `sticker_search`：

```markdown
优先把 `reply_token` 放入 `reply(content)`。`send_code` 仅用于兼容旧模型输出，不是首选格式。
```

对 `image_generation`：

```markdown
生成图片后，把工具返回的 `[generated_image:<id>]` 放入 `reply(content)`。不要把 base64 或本地文件路径写进回复。
```

- [ ] **步骤 3：验证两个 prompt 根目录一致**

运行：

```bash
diff -u prompts.v2.default/tools/reply/usage.md data/prompts_v2/tools/reply/usage.md
diff -u prompts.v2.default/tools/sticker_search/usage.md data/prompts_v2/tools/sticker_search/usage.md
diff -u prompts.v2.default/tools/image_generation/usage.md data/prompts_v2/tools/image_generation/usage.md
```

预期：如果两个根目录原本就存在路径或标题差异，diff 可以有已知差异；本任务新增的短 token 口径必须一致。若 diff 太大，改用 `rg -n` 精确检查新增句子在两个根目录都存在。

- [ ] **步骤 4：运行 prompt 文档扫描**

运行：

```bash
python - <<'PY'
from pathlib import Path

paths = [
    Path("prompts.v2.default/tools/reply/usage.md"),
    Path("prompts.v2.default/tools/sticker_search/usage.md"),
    Path("prompts.v2.default/tools/image_generation/usage.md"),
    Path("data/prompts_v2/tools/reply/usage.md"),
    Path("data/prompts_v2/tools/sticker_search/usage.md"),
    Path("data/prompts_v2/tools/image_generation/usage.md"),
]
required = [
    "出口 renderer",
    "reply_token",
    "[generated_image:",
]
for path in paths:
    text = path.read_text(encoding="utf-8")
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise SystemExit(f"{path} missing {missing}")
PY
```

预期：无输出，退出码 0。

- [ ] **步骤 5：Commit**

运行：

```bash
git add \
  prompts.v2.default/tools/reply/usage.md \
  prompts.v2.default/tools/sticker_search/usage.md \
  prompts.v2.default/tools/image_generation/usage.md \
  data/prompts_v2/tools/reply/usage.md \
  data/prompts_v2/tools/sticker_search/usage.md \
  data/prompts_v2/tools/image_generation/usage.md
git commit -m "docs(提示词): 说明出站渲染职责"
```

## 任务 7：文档收口与全量验证

**文件：**
- 修改：`docs/message-field-standard.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/qq-outbound-rendering-contract.md`

- [ ] **步骤 1：同步消息字段标准**

在 `docs/message-field-standard.md` 增加「响应出站渲染契约」小节，写明：

```markdown
响应信封里的 `messages` 是出站内容 canonical 数组，`reply` 是兼容 fallback。
QQ 出站由 `core.qq_outbound_renderer.render_qq_outbound_envelope()` 统一渲染为旧 `message` 字符串。
QQ-facing 路径禁止 base64 fallback；生成图优先公开 URL，无公开 URL 时保留短 token 并记录 warning。
```

- [ ] **步骤 2：同步路线项 7**

把 `docs/todo.md` 路线项 7 更新为已完成 P2-3 主线，并列出仍保留的相邻演进项：

```markdown
**现状（2026-06-18 已落地）**：QQ 出站 renderer 已成为 push 信封的统一渲染入口……
```

不要修改路线项编号。

- [ ] **步骤 3：同步 walkthrough**

在 `docs/plan_walkthrough.md`：

- 把 P2-3 从「待执行」更新为「实现中」或「已完成」，按实际代码任务完成状态选择。
- 增加本计划路径和各任务提交号。
- 记录定向测试和全量测试的新鲜输出。

- [ ] **步骤 4：运行 P2-3 定向回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
  tests/test_qq_outbound_renderer.py \
  tests/test_push_envelope.py \
  tests/test_schedule_task_tool.py \
  tests/test_api_push_envelope.py \
  tests/test_message_envelope.py \
  tests/test_group_response_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 5：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：PASS，失败数为 0。

- [ ] **步骤 6：运行文档扫描**

运行：

```bash
python - <<'PY'
from pathlib import Path

paths = [
    Path("docs/message-field-standard.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path(".Codex/plans/qq-outbound-rendering-contract.md"),
]
needles = [
    "待" + "定",
    "后续" + "实现",
    "类似" + "任务",
    "添加" + "适当",
    "为" + "上述",
    "\ufffd",
]
failed = False
for path in paths:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if any(needle in line for needle in needles) or "T" + "ODO:" in line or "T" + "ODO：" in line:
            print(f"{path}:{line_no}:{line}")
            failed = True
raise SystemExit(1 if failed else 0)
PY
```

预期：无输出，退出码 0。

- [ ] **步骤 7：运行 diff 检查**

运行：

```bash
git diff --check -- \
  docs/message-field-standard.md \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/qq-outbound-rendering-contract.md
```

预期：无输出，退出码 0。

- [ ] **步骤 8：Commit**

运行：

```bash
git add \
  docs/message-field-standard.md \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/qq-outbound-rendering-contract.md
git commit -m "docs(计划): 同步 QQ 出站渲染计划"
```

## 最终验收清单

- [ ] `core/qq_outbound_renderer.py` 存在，且 `render_qq_outbound_envelope()` 是 QQ push 信封的唯一渲染入口。
- [ ] `push_to_qq(target_type, target_id, message) -> bool` 旧签名未改。
- [ ] `[generated_image:<id>]` 有公开 URL 时渲染为 `[CQ:image,file=<url>]`。
- [ ] 无公开 URL 时保留 `[generated_image:<id>]`，且 `message` 不包含 `base64://`。
- [ ] `[sticker:<id>]` 在 renderer 层可兼容展开。
- [ ] schedule task `action == "run"` 不再直接调用 `push_to_qq()`。
- [ ] `/chat`、SSE done、`/group/message` 的旧字段继续兼容。
- [ ] prompt usage 文档说明短 token 和 renderer 职责，两个 prompt 根目录口径一致。
- [ ] P2-3 定向回归和全量回归通过。
