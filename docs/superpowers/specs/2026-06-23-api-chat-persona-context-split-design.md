# 普通 API Chat Persona Context 拆分设计

日期：2026-06-23

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩 `api/routes.py`。第十九刀完成后，`api/routes.py` 为 1333 行，剩余显式路由只有 `/chat` 和 `/health`。`/health` 收益很低，`/chat` 主链路仍承担用户注册、屏蔽检查、图片预缓存、画像格式化、私聊 timing、guardrail、runtime payload、Bridge、流式收尾、落库和 response envelope。

本阶段不迁移完整 `/chat` 路由，也不触碰 stream finalizer。目标是拆出低风险纯 helper：`_format_persona_for_prompt()`。该函数约 100 行，只负责把画像 JSON 压成 prompt 可消费的中文文本，依赖 `sanitize_prompt_text()` 和 `MAX_PERSONA_CHARS`，不访问 DB、不 await、不调用 Bridge，也不需要父模块状态。

## 方案比较

### 方案 A：Chat Persona Context 小刀（采用）

新增 `api/chat_persona_context.py`，迁移 `format_persona_for_prompt()`。`api.routes` 保留 `_format_persona_for_prompt()` 父模块 wrapper，并继续由 `proxy_chat()` 调用。

优点：

- 风险低，迁移的是纯格式化逻辑。
- 继续降低 `api/routes.py` 行数，为后续 `/chat` 主链路拆分让出上下文空间。
- 保留现有 `tests/test_api.py` 从 `api.routes` 导入 `_format_persona_for_prompt()` 的兼容契约。
- 不触碰 Prompt Runtime 输入字段名、`bridge_meta`、`persona_text` 传递、落库或 response envelope。

代价：

- 行数收益小于 stream finalizer。
- 仍保留 DB persona lookup 和 `PersonaInjectionService` 调用在父模块。

### 方案 B：Streaming finalizer 小内核

把 `_persist_stream_result_after_runner_done()` 或正常 SSE done 收尾拆到新模块。

优点：

- 行数收益更高。
- 能继续收敛断连后台与正常流式收尾重复逻辑。

代价：

- 会跨 `_finalize_private_buffer()`、`_persist_chat_turn()`、`_build_chat_push_envelope()`、`push_envelope_to_qq()`、`BackgroundTasks` 和 `UnitOfWork`。
- 更容易破坏断连后台 push、bounded queue drain、prompt audit failure no-context 落库和 SSE `done` 信封。

### 方案 C：完整 persona flow 迁移

同时迁移 `_find_persona()`、Persona 表查询、`PersonaInjectionService` 调用和 prompt budget 相关输入。

不采用。该方案会牵动 DB session 生命周期、`release_clean_session_transaction()` 边界、Prompt Runtime 输入和 persona injection debug 合并，不适合作为本轮小刀。

## 设计

### 新模块

新增 `api/chat_persona_context.py`，包含：

```python
def format_persona_for_prompt(
    persona_data: dict,
    max_chars: int = 1600,
) -> str:
    ...
```

实现完全承接当前 `_format_persona_for_prompt()` 语义：

- 空对象或非 dict 返回空字符串。
- 优先输出 `persona_summary` / `summary`。
- 输出 `response_style` / `communication_style`。
- 输出最多 5 个 `traits`。
- 输出最多 4 个 `preferences`。
- 输出 `pain_points` 前 300 字。
- 输出 `identity` 中非空键值。
- 对 `domain_profiles` 按 confidence 和 interaction count 排序，最多 3 个领域。
- 对 `facts` 按 confidence 和 evidence 排序，最多 10 条，保留「稳定画像事实」格式。
- 若没有结构化字段，回退输出最多 6 个标量字段。
- 最终统一调用 `core.context_builder.sanitize_prompt_text()`，按 `max_chars` 截断并清理 prompt 边界。

新模块不导入 `api.routes`，不依赖 FastAPI、SQLAlchemy、asyncio、Bridge 或 runtime state。

### 父模块 wrapper

`api.routes` 新增导入：

```python
from api import chat_persona_context
```

并保留旧 wrapper：

```python
def _format_persona_for_prompt(persona_data: dict, max_chars: int = MAX_PERSONA_CHARS) -> str:
    return chat_persona_context.format_persona_for_prompt(
        persona_data,
        max_chars=max_chars,
    )
```

现有 `proxy_chat()` 不直接导入新模块，仍调用父模块 `_format_persona_for_prompt()`，保留测试和 monkeypatch 入口。

## 保留边界

本阶段保留在 `api.routes`：

- `/chat` 路由本体。
- `ChatProxyRequest` 和所有 request wrapper。
- `_find_persona()` 嵌套 DB 查询逻辑。
- `PersonaInjectionService` 调用和 `_ctx_debug.update()`。
- `release_clean_session_transaction()` 调用点。
- Prompt Runtime 输入组装、`bridge_meta`、`persona_text` 字段名和 `prompt_budget` 日志。
- PrivateTimingGate、guardrail、Bridge、落库、SSE、push envelope 和 response envelope。
- `/health`。

本阶段不修改默认 Prompt Runtime 模板；因为 `persona_text` 的字段名、位置和内容格式保持不变，只更换实现所在模块。

## 测试策略

新增 `tests/test_api_chat_persona_context_split.py`：

1. `test_chat_persona_context_module_does_not_import_parent_routes_or_sync_awaitable`
   - 扫描新模块源码，禁止导入 `api.routes`、`asyncio.run` 和 `run_awaitable_sync`。

2. `test_parent_persona_formatter_wrapper_remains_in_routes`
   - 断言 `routes._format_persona_for_prompt.__module__ == "api.routes"`。
   - 断言父模块 wrapper 输出等于新模块输出。

3. `test_format_persona_for_prompt_preserves_structured_contract`
   - 覆盖 summary、style、traits、preferences、identity、domain profiles 和 facts。
   - 断言输出仍包含既有中文章节名和排序后的事实。

4. `test_format_persona_for_prompt_falls_back_to_scalar_fields_and_sanitizes`
   - 覆盖无结构化字段时的 scalar fallback。
   - 覆盖 prompt 边界清理和 `max_chars` 截断。

保留现有 `tests/test_api.py::test_format_persona_facts_without_truncated_json` 作为父模块行为回归。

红灯预期：

- 新测试导入 `api.chat_persona_context` 失败。
- 父模块 wrapper 对比失败，因为新模块尚不存在。

## 验证命令

红灯：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_context_split.py -v
```

绿灯：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_persona_context_split.py \
  tests/test_api.py::test_format_persona_facts_without_truncated_json \
  -v
```

相邻回归：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_runtime_facade_split.py \
  tests/test_api.py::test_proxy_chat \
  tests/test_api.py::test_private_prompt_v2_audit_failure_is_not_context_chat \
  -v
```

静态检查：

```bash
python -m compileall api/routes.py api/chat_persona_context.py -q
python -B -m pytest -p no:cacheprovider tests/test_asyncio_run_policy.py -v
wc -l api/routes.py api/chat_persona_context.py tests/test_api_chat_persona_context_split.py
```

最终回归：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 风险与规避

- **Prompt 文本格式漂移**：用父模块 wrapper 对比新模块输出，并保留既有 facts 回归。
- **父模块 monkeypatch 入口消失**：`proxy_chat()` 继续调用 `_format_persona_for_prompt()` wrapper。
- **意外反向导入**：新增源码扫描测试，禁止新模块导入 `api.routes`。
- **Prompt Runtime 模板误更新**：本阶段不改变 `persona_text` 字段名、bridge metadata 或 runtime input；仅移动纯格式化实现，默认模板无需变更。
- **范围扩大到 DB / injection**：设计明确不迁移 `_find_persona()` 和 `PersonaInjectionService`，避免改变事务释放时机。
