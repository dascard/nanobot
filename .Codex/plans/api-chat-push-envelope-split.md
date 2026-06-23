# 聊天推送信封拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `/chat` 断连后台 push 的标准响应信封组装和传输层图片展开拆到 `api/chat_push_envelope.py`，保留 `api.routes` 中的流式生命周期、push 调用点和父模块 patch point。

**架构：** 新模块只做纯数据组装：`ChatPushEnvelope` 保存目标与 envelope，`build_chat_push_envelope()` 复用 `chat_request_contract.resolve_push_target_id()` 和 `core.message_envelope.build_chat_response_envelope()`，`expand_chat_transport_answer()` 固定 `allow_base64=False`。`api.routes` 继续负责是否 push、何时 push、如何落库、如何处理后台任务、如何输出 SSE。

**技术栈：** Python 3.12、FastAPI、pytest、Pydantic `ChatProxyRequest`、标准响应信封。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-23-api-chat-push-envelope-split-design.md`
- [x] 设计提交：`e5c546a docs(普通API): 设计聊天推送信封拆分`

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`api.routes._stream_chat()` 的生成器边界、heartbeat、SSE 事件输出和 `persisted` 标记。
- 保留：`api.routes._persist_stream_result_after_runner_done()` 的调度点和主体生命周期。
- 保留：`push_envelope_to_qq()` 实际调用点在 `api.routes` 内。
- 保留：`_persist_chat_turn()`、`_finalize_private_buffer()`、`_resolve_push_target_id()`、
  `_chat_response_payload()`、`get_bridge()` 和 `BackgroundTasks.add_task()` 父模块 patch point。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块调用 `push_envelope_to_qq()`、`_persist_chat_turn()`、`_finalize_private_buffer()` 或 Bridge。
- 禁止：迁移完整 `_stream_chat()`、`StreamingResponse` 或 `_persist_stream_result_after_runner_done()`。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 文件职责

- 创建：`tests/test_api_chat_push_envelope_split.py`
  - 锁定新模块 import hygiene。
  - 锁定私聊 / 群聊 push target 和 envelope meta。
  - 锁定传输层图片展开使用 `allow_base64=False`。
  - 锁定父模块 wrapper 仍在 `api.routes`。
- 修改：4 个普通 API split 扫描测试：
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
- 修改：`tests/test_api_push_envelope.py`
  - 补充断连后台 push envelope meta 断言。
- 创建：`api/chat_push_envelope.py`
  - 提供 `ChatPushEnvelope`。
  - 提供 `build_chat_push_envelope()`。
  - 提供 `expand_chat_transport_answer()`。
- 修改：`api/routes.py`
  - 导入 `chat_push_envelope`。
  - 保留 `_expand_chat_transport_answer()` 和 `_build_chat_push_envelope()` 父模块 wrapper。
  - 将断连后台 push 的手写 envelope meta 改为调用新模块。
  - 将非流式和流式 done 的图片 token 传输展开改为调用 wrapper。
- 修改：`.Codex/plans/api-chat-push-envelope-split.md`
  - 随执行记录验证结果。
- 修改：`docs/todo.md`
  - 记录 P3 中 `api/routes.py` Chat Push Envelope 小刀进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加 2026-06-23 Chat Push Envelope 执行记录、提交列表和验证证据。

---

### 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_push_envelope_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`tests/test_api_push_envelope.py`

- [x] **步骤 1：编写新模块契约测试**

创建 `tests/test_api_chat_push_envelope_split.py`：

```python
from __future__ import annotations

from pathlib import Path

from api import routes
from api.chat_request_contract import ChatProxyRequest


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_push_envelope_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_push_envelope.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "push_envelope_to_qq(" not in source
    assert "_persist_chat_turn(" not in source
    assert "_finalize_private_buffer(" not in source
    assert "get_bridge(" not in source


def test_build_chat_push_envelope_private_target_and_meta_contract():
    from api.chat_push_envelope import build_chat_push_envelope

    req = ChatProxyRequest(
        user_id="u-private",
        session_id="private_u-private",
        query="hello",
        client_meta={"platform": "web", "trace": {"request_id": "req-1"}},
    )

    built = build_chat_push_envelope(
        req,
        answer="推送正文",
        platform="web",
        chat_type="private",
        is_group=False,
        reply_meta={"send_mode": "quote", "_agent_result": "hidden"},
    )

    assert built.target_type == "private"
    assert built.target_id == "u-private"
    assert built.envelope["status"] == "ok"
    assert built.envelope["reply"] == "推送正文"
    assert built.envelope["messages"] == [{"type": "text", "text": "推送正文"}]
    assert built.envelope["reply_meta"] == {"send_mode": "quote"}
    assert built.envelope["meta"]["user_id"] == "u-private"
    assert built.envelope["meta"]["session_id"] == "private_u-private"
    assert built.envelope["meta"]["platform"] == "web"
    assert built.envelope["meta"]["chat_type"] == "private"
    assert built.envelope["meta"]["target_type"] == "private"
    assert built.envelope["meta"]["target_id"] == "u-private"


def test_build_chat_push_envelope_group_target_uses_request_contract():
    from api.chat_push_envelope import build_chat_push_envelope

    prefixed = ChatProxyRequest(
        user_id="u1",
        session_id="group_987654",
        query="group",
    )
    bare = ChatProxyRequest(
        user_id="u1",
        session_id="987654",
        query="group",
    )

    built_prefixed = build_chat_push_envelope(
        prefixed,
        answer="群回复",
        platform="qq",
        chat_type="group",
        is_group=True,
    )
    built_bare = build_chat_push_envelope(
        bare,
        answer="群回复",
        platform="qq",
        chat_type="group",
        is_group=True,
    )

    assert built_prefixed.target_type == "group"
    assert built_prefixed.target_id == "987654"
    assert built_prefixed.envelope["meta"]["target_id"] == "987654"
    assert built_bare.target_type == "group"
    assert built_bare.target_id == "987654"
    assert built_bare.envelope["meta"]["target_id"] == "987654"


def test_expand_chat_transport_answer_disables_base64(monkeypatch):
    from api import chat_push_envelope

    calls: list[tuple[str, bool]] = []

    def fake_expand(content: str, *, allow_base64: bool = True) -> str:
        calls.append((content, allow_base64))
        return "展开后的 CQ 图片"

    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        fake_expand,
    )

    assert chat_push_envelope.expand_chat_transport_answer("原始 [generated_image:1]") == "展开后的 CQ 图片"
    assert calls == [("原始 [generated_image:1]", False)]


def test_parent_chat_push_envelope_wrappers_remain_in_routes():
    assert routes._expand_chat_transport_answer.__module__ == "api.routes"
    assert routes._build_chat_push_envelope.__module__ == "api.routes"
    assert routes._chat_response_payload.__module__ == "api.routes"
```

- [x] **步骤 2：将新模块加入普通 API split 扫描清单**

在以下 4 个文件的 `chat_split_modules` 元组中加入
`"api/chat_push_envelope.py"`：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 3：加厚断连 push envelope 集成断言**

在 `tests/test_api_push_envelope.py::test_stream_disconnect_background_push_uses_envelope_and_no_base64`
末尾补充：

```python
    assert envelope["meta"]["user_id"] == "u-stream-envelope"
    assert envelope["meta"]["session_id"] == "private_u-stream-envelope"
    assert envelope["meta"]["target_type"] == "private"
    assert envelope["meta"]["target_id"] == "u-stream-envelope"
```

- [x] **步骤 4：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_push_envelope_split.py -v
```

预期：失败原因是 `api/chat_push_envelope.py` 不存在或父模块 wrapper 不存在。

- [x] **步骤 5：运行扫描红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：失败原因是扫描清单中的 `api/chat_push_envelope.py` 不存在。

- [x] **步骤 6：提交红灯测试**

运行：

```bash
git add \
  tests/test_api_chat_push_envelope_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_push_envelope.py
git diff --cached --check
git commit -m "test(普通API): 锁定聊天推送信封契约"
```

---

### 任务 2：新模块实现

**文件：**
- 创建：`api/chat_push_envelope.py`
- 修改：`.Codex/plans/api-chat-push-envelope-split.md`

- [x] **步骤 1：创建新模块**

写入 `api/chat_push_envelope.py`：

```python
"""聊天推送信封辅助函数。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.chat_request_contract import ChatProxyRequest, resolve_push_target_id
from core.message_envelope import build_chat_response_envelope


@dataclass(frozen=True)
class ChatPushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


def build_chat_push_envelope(
    req: ChatProxyRequest,
    *,
    answer: str,
    platform: str,
    chat_type: str,
    is_group: bool,
    status: str = "ok",
    reply_meta: Mapping[str, Any] | None = None,
    extra_meta: Mapping[str, Any] | None = None,
) -> ChatPushEnvelope:
    target_type = "group" if is_group else "private"
    target_id = resolve_push_target_id(req, is_group)
    meta = {
        "platform": platform,
        "chat_type": chat_type,
        "user_id": req.user_id,
        "session_id": req.session_id,
        "target_type": target_type,
        "target_id": target_id,
    }
    if isinstance(extra_meta, Mapping):
        for key, value in extra_meta.items():
            if key not in {"platform", "chat_type", "user_id", "session_id", "target_type", "target_id"}:
                meta[str(key)] = value
    return ChatPushEnvelope(
        target_type=target_type,
        target_id=target_id,
        envelope=build_chat_response_envelope(
            status=status,
            answer=answer,
            reply_meta=reply_meta,
            meta=meta,
        ),
    )


def expand_chat_transport_answer(answer: str) -> str:
    from core.generated_images import expand_generated_image_refs_in_content

    return expand_generated_image_refs_in_content(answer, allow_base64=False)
```

- [x] **步骤 2：运行新模块绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_push_envelope_split.py -v
```

预期：父模块 wrapper 相关断言仍失败，新模块自身测试通过。

- [x] **步骤 3：提交新模块**

运行：

```bash
git add api/chat_push_envelope.py .Codex/plans/api-chat-push-envelope-split.md
git diff --cached --check
git commit -m "refactor(普通API): 增加聊天推送信封助手"
```

---

### 任务 3：父模块接入

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-push-envelope-split.md`

- [x] **步骤 1：导入新模块**

在 `api/routes.py` 的 `from api import (` 导入列表中加入：

```python
    chat_push_envelope,
```

- [x] **步骤 2：增加父模块 wrapper**

在 `_chat_response_payload()` 后增加：

```python
def _expand_chat_transport_answer(answer: str) -> str:
    return chat_push_envelope.expand_chat_transport_answer(answer)


def _build_chat_push_envelope(
    req: ChatProxyRequest,
    **kwargs: Any,
) -> chat_push_envelope.ChatPushEnvelope:
    return chat_push_envelope.build_chat_push_envelope(req, **kwargs)
```

- [x] **步骤 3：替换断连后台 push 组装**

将 `_persist_stream_result_after_runner_done()` 中的：

```python
                    from core.message_envelope import build_chat_response_envelope

                    # 推送前展开图片 token（禁用 base64，避免推送大负载）
                    push_answer = final_answer
                    try:
                        from core.generated_images import expand_generated_image_refs_in_content
                        push_answer = expand_generated_image_refs_in_content(final_answer, allow_base64=False)
                    except Exception:
                        pass

                    target_type = "private" if not bridge_meta.get("is_group") else "group"
                    target_id = _resolve_push_target_id(req, bool(bridge_meta.get("is_group")))
                    envelope = build_chat_response_envelope(
                        status="ok",
                        answer=push_answer,
                        meta={
                            "platform": platform,
                            "chat_type": str(bridge_meta.get("chat_type") or ""),
                            "user_id": req.user_id,
                            "session_id": req.session_id,
                            "target_type": target_type,
                            "target_id": target_id,
                        },
                    )
                    ok = await push_envelope_to_qq(target_type, target_id, envelope)
```

替换为：

```python
                    # 推送前展开图片 token（禁用 base64，避免推送大负载）
                    push_answer = final_answer
                    try:
                        push_answer = _expand_chat_transport_answer(final_answer)
                    except Exception:
                        pass

                    push_payload = _build_chat_push_envelope(
                        req,
                        answer=push_answer,
                        platform=platform,
                        chat_type=str(bridge_meta.get("chat_type") or ""),
                        is_group=bool(bridge_meta.get("is_group")),
                    )
                    ok = await push_envelope_to_qq(
                        push_payload.target_type,
                        push_payload.target_id,
                        push_payload.envelope,
                    )
```

- [x] **步骤 4：替换正常流式和非流式传输展开**

将流式 done 分支和非流式分支中的 `expand_generated_image_refs_in_content(answer, allow_base64=False)`
调用改为 `_expand_chat_transport_answer(answer)`，保留 `try/except` 和现有 warning 日志。

- [x] **步骤 5：运行接入绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_push_envelope_split.py \
  tests/test_api_push_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_response_envelope.py \
  -v
```

预期：全部通过。

- [x] **步骤 6：运行相邻断连回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder \
  tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner \
  tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder \
  tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send \
  -v
```

预期：全部通过。

- [x] **步骤 7：运行扫描绿灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_asyncio_run_policy.py \
  -v
```

预期：全部通过。

- [x] **步骤 8：记录行数并提交父模块接入**

运行：

```bash
wc -l api/routes.py api/chat_push_envelope.py tests/test_api_chat_push_envelope_split.py
git add api/routes.py .Codex/plans/api-chat-push-envelope-split.md
git diff --cached --check
git commit -m "refactor(普通API): 接入聊天推送信封助手"
```

---

### 任务 4：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-chat-push-envelope-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过，跳过数量和警告数量记录到本计划与 `docs/plan_walkthrough.md`。

- [ ] **步骤 2：更新 P3 进度**

在 `docs/todo.md` 的 P3 超大文件拆分条目中追加 `api/routes.py` 第十八刀进展，说明：

- 新增 `api/chat_push_envelope.py`。
- 断连后台 push envelope 手写 meta 已迁移到 helper。
- 非流式和流式 done 的图片 token 传输展开复用 helper。
- `/chat` 路由本体、SSE 主循环、stream finalizer、push 调用点、DB 持久化和父模块 patch point 均保持不变。
- 全量回归结果。

- [ ] **步骤 3：更新 walkthrough**

在 `docs/plan_walkthrough.md` 追加 `2026-06-23 普通 API Chat Push Envelope 拆分` 章节，记录：

- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表全部勾选。
- 验证记录。
- 执行约束。
- 下一步候选。

- [ ] **步骤 4：运行文档自检**

运行：

```bash
rg -n -P 'T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}' .Codex/plans/api-chat-push-envelope-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-chat-push-envelope-split.md docs/todo.md docs/plan_walkthrough.md
```

预期：扫描无输出，diff 检查无输出。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-chat-push-envelope-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口聊天推送信封拆分"
```

---

## 验证记录

- 红灯测试：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_push_envelope_split.py -v`
  - 结果：`5 failed, 1 warning`；失败原因是 `api/chat_push_envelope.py`
    和父模块 wrapper 不存在，符合预期红灯。
- 扫描红灯：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
  - 结果：`4 failed, 1 warning`；失败原因是扫描清单中的
    `api/chat_push_envelope.py` 不存在，符合预期红灯。
- 新模块阶段验证：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_push_envelope_split.py -v`
  - 结果：`4 passed, 1 failed, 1 warning`；失败原因只剩
    `api.routes._expand_chat_transport_answer` wrapper 不存在，符合新模块阶段边界。
- 父模块接入组合验证：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_chat_push_envelope_split.py tests/test_api_push_envelope.py tests/test_chat_response_envelope.py tests/test_streaming_response_envelope.py -v`
  - 结果：`13 passed, 21 warnings`。
- 断连相邻回归：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send -v`
  - 结果：`4 passed, 1 warning`。
- 扫描与 `asyncio.run` 策略验证：
  - 命令：`python -B -m pytest -p no:cacheprovider tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_asyncio_run_policy.py -v`
  - 结果：`7 passed, 1 warning`。
- 行数记录：
  - 命令：`wc -l api/routes.py api/chat_push_envelope.py tests/test_api_chat_push_envelope_split.py`
  - 结果：`api/routes.py` 为 1331 行，`api/chat_push_envelope.py`
    为 68 行，`tests/test_api_chat_push_envelope_split.py` 为 120 行。
- 死 facade 清理证据：
  - 命令：`rg -n "_chat_response_meta" api tests docs/superpowers/specs/2026-06-23-api-chat-push-envelope-split-design.md .Codex/plans/api-chat-push-envelope-split.md`
  - 结果：无命中；未使用的 `_chat_response_meta()` 父模块 facade 已删除。
