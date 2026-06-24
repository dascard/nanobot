# 普通 API Chat Persona Lookup 拆分设计

日期：2026-06-24

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩 `api/routes.py`，文件为 1022 行。前序阶段已把 `/chat` 周边的 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helper、streaming result、SSE loop、non-streaming result、private buffer、push envelope、persona context、media precache、user block rules 和 private pre-bridge decision 拆出。

`proxy_chat()` 里还保留一段内联 persona snapshot lookup 逻辑：构造 user_id 变体、查询 `Persona`、解析 `persona_json`、格式化初始 `persona_text`、输出 lookup 日志。这段逻辑位于图片预缓存之后、会话历史构建之前，不参与 HTTP response、Bridge、SSE、Prompt Runtime 编译或 dynamic persona injection。它适合拆成低耦合 helper，继续降低 `api/routes.py` 的行数和职责密度。

## 只读审查结论

- 当前 `/chat` 剩余主流程仍承担多个阶段：用户 upsert、blocked early return、图片预缓存、persona snapshot lookup、history 构建、pre-bridge 决策、guardrail silent early return、persona injection、runtime payload、Bridge、SSE 和非流式收尾。
- `PersonaInjectionService` 是 runtime payload 前的动态画像注入逻辑，会读取 `history_messages` 和 `safe_user_input`，不能与本阶段的静态 persona snapshot lookup 混拆。
- 初始 persona snapshot lookup 只依赖 DB session、`Persona` model、JSON parser、persona formatter 和 logger，拆分后可以用 fake DB / fake formatter 做单元测试。
- 新模块不需要导入 FastAPI、APIRouter、StreamingResponse、BackgroundTasks、HTTPException、Bridge、Prompt Runtime 或 `api.routes`。

## 方案选择

### 方案 A：新增 `api/chat_persona_lookup.py`（推荐）

新模块提供 `resolve_chat_persona_snapshot()`，负责 user_id 变体生成、`Persona` 查询、JSON 解析、formatter 调用和 lookup debug 结构产出。父模块保留薄 wrapper `_resolve_chat_persona_snapshot()`，并继续负责日志输出、后续 `PersonaInjectionService` 覆盖和 Prompt Runtime payload。

优点：

- 行为边界清晰，和 Bridge / SSE / Prompt Runtime 没有直接耦合。
- 可以保留父模块 formatter patch point：`api.routes._format_persona_for_prompt()`。
- 单测不需要 FastAPI client，也不需要真实数据库连接。
- 对 `enriched_query`、conversation、message envelope 和 push envelope 无影响。

风险：

- 如果新模块直接导入 `api.routes` 或自行调用 `_format_persona_for_prompt()`，会破坏拆分边界；必须通过 callback 注入 formatter。
- 如果新模块负责记录父模块日志，会把 logger 文案和父模块 patch point 搬进去；本阶段只返回 debug 数据，由父模块继续记录现有日志。
- 如果把动态 `PersonaInjectionService` 一并迁走，会触碰 history / safe_user_input / Prompt Runtime 输入契约；本阶段不做。

### 方案 B：只抽 user_id 变体生成函数

只把 `"private_"` / `"group_"` 前缀变体生成拆为小函数。

优点是风险极低。缺点是行数收益很小，`proxy_chat()` 仍保留 DB 查询、JSON parse 和日志编排。

结论：不采用。

### 方案 C：合并静态 snapshot lookup 与动态 persona injection

把初始 persona snapshot lookup 和 `PersonaInjectionService` 都迁移到同一个模块。

优点是行数收益更高。缺点是会触碰 runtime payload 前的动态画像注入、history messages、`_ctx_debug` 更新和 Prompt Runtime 输入，风险超过本阶段目标。

结论：不采用。

## 目标

- 新增 `api/chat_persona_lookup.py`。
- 提取 `proxy_chat()` 中的 persona snapshot lookup：
  - 生成 user_id 查询候选：原始 ID、缺失的 `private_` / `group_` 前缀变体、剥离已有前缀后的 ID。
  - 去重后按顺序查询 `Persona.user_id`。
  - 命中 fallback 变体时返回 matched candidate 信息。
  - 缺失 persona 时返回空 JSON 字符串和空数据。
  - `persona_json` 非法或类型错误时降级为空数据。
  - 调用父模块注入的 formatter 生成 `persona_text`。
  - 返回 `ChatPersonaSnapshot`，包含 `persona_obj`、`persona_json`、`persona_data`、`persona_text` 和 debug 信息。
- 父模块继续负责：
  - 调用 `_resolve_chat_persona_snapshot()`。
  - 记录现有 `[/chat] Persona lookup` 日志。
  - 记录 fallback / missing persona 日志。
  - 动态 `PersonaInjectionService` 注入。
  - `_ctx_debug` 更新。
  - Prompt Runtime payload、Bridge、SSE、非流式收尾和 response。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()` 本体。
- 不迁移用户自动创建 / 用户名更新。
- 不迁移用户屏蔽规则 early return。
- 不迁移图片预缓存。
- 不迁移 `_build_chat_context()`、history 注入或事务释放。
- 不迁移 `PersonaInjectionService`。
- 不迁移 `safe_user_input`、`enriched_query`、`bridge_meta` 或 Prompt Runtime 模板。
- 不迁移 Bridge、SSE、message envelope、push envelope 或 evolution。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_persona_lookup.py`

### `ChatPersonaSnapshot`

职责：把 persona snapshot lookup 的结果和父模块日志需要的 debug 字段收拢成一个结构。

字段：

- `persona_obj: Any | None`
- `persona_json: str`
- `persona_data: dict[str, Any]`
- `persona_text: str`
- `lookup_user_id: str`
- `matched_user_id: str | None`
- `candidate_count: int`
- `parse_failed: bool`

### `iter_persona_user_id_candidates()`

职责：复刻父模块内联候选顺序，并保证去重。

行为：

- 首项是原始 `uid`。
- 若原始 ID 不以 `private_` 开头，追加 `private_` 前缀版本。
- 若原始 ID 不以 `group_` 开头，追加 `group_` 前缀版本。
- 若原始 ID 以 `private_` 或 `group_` 开头，追加剥离前缀后的版本。
- 返回去重后的列表。

### `resolve_chat_persona_snapshot()`

接口：

```python
def resolve_chat_persona_snapshot(
    db: Any,
    user_id: str,
    *,
    persona_model: Any,
    format_persona: Callable[[dict[str, Any]], str],
) -> ChatPersonaSnapshot:
    raise NotImplementedError
```

行为：

- 按候选顺序执行 `db.query(persona_model).filter(persona_model.user_id == candidate).first()`。
- 命中后停止查询，记录 `matched_user_id`。
- 未命中时 `persona_obj=None`，`persona_json="{}"`。
- `persona_json` 只接受 JSON object；非 object 视为空数据。
- JSON parse 失败或 `TypeError` 时 `persona_data={}` 且 `parse_failed=True`。
- 始终调用 `format_persona(persona_data)`。

## 父模块接入设计

`api/routes.py` 增加薄 wrapper：

```python
def _resolve_chat_persona_snapshot(db: Session, user_id: str) -> chat_persona_lookup.ChatPersonaSnapshot:
    return chat_persona_lookup.resolve_chat_persona_snapshot(
        db,
        user_id,
        persona_model=Persona,
        format_persona=_format_persona_for_prompt,
    )
```

`proxy_chat()` 替换内联 `_find_persona()` 和 JSON parse：

```python
persona_snapshot = _resolve_chat_persona_snapshot(db, req.user_id)
persona_obj = persona_snapshot.persona_obj
persona_json_str = persona_snapshot.persona_json
persona_data = persona_snapshot.persona_data
persona_text = persona_snapshot.persona_text
```

父模块继续记录：

- fallback 命中：`Persona found via fallback`
- 未命中：`No persona for user_id`
- lookup 总结：`Persona lookup`

## 测试设计

新增 `tests/test_api_chat_persona_lookup_split.py`：

- `test_chat_persona_lookup_module_does_not_import_parent_routes_or_runtime_side_effects`
  - 新模块不导入 `api.routes`、FastAPI、Bridge、Prompt Runtime、`asyncio.run` 或 `run_awaitable_sync`。
- `test_iter_persona_user_id_candidates_preserves_legacy_order_and_dedupes`
  - 覆盖普通 ID、`private_` 前缀 ID、`group_` 前缀 ID。
- `test_resolve_persona_snapshot_uses_first_matching_candidate_and_formatter`
  - 用 fake DB 验证候选查询顺序、fallback 命中、formatter 输入。
- `test_resolve_persona_snapshot_falls_back_to_empty_data_for_missing_or_invalid_json`
  - 覆盖 persona 缺失、非法 JSON、JSON array。
- `test_parent_persona_lookup_wrapper_remains_patchable`
  - 父模块 wrapper 的 `__module__ == "api.routes"`，并可通过 monkeypatch 新模块函数观察调用。
- `test_proxy_chat_persona_fallback_still_reaches_bridge_metadata`
  - HTTP 回归：数据库只写 `private_persona-user` persona，请求使用裸 `persona-user`，Bridge metadata 中 `persona_text` 仍来自 fallback persona。

更新四个 chat split module 扫描清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

把 `api/chat_persona_lookup.py` 加入 chat split module 边界扫描。

## 验证计划

- 红灯：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_lookup_split.py tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
- 绿灯定向：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_lookup_split.py -v`
- 相邻回归：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_persona_context_split.py tests/test_api_chat_runtime_facade_split.py tests/test_api.py::test_proxy_chat_passes_history_header_to_bridge tests/test_api.py::test_proxy_chat_releases_db_transaction_before_bridge -v`
- 静态检查：
  `python -m compileall api/routes.py api/chat_persona_lookup.py -q`
- 文档 / diff：
  `git diff --check -- api/routes.py api/chat_persona_lookup.py tests/test_api_chat_persona_lookup_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py`
- 全量：
  `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -B -m pytest -p no:cacheprovider tests/ -v`

## 风险控制

- 新模块只返回数据，不构造 response，不写 DB，不提交事务。
- 父模块保留 `Persona` model、formatter wrapper 和日志 patch point。
- `PersonaInjectionService` 保持原位，避免影响 Prompt Runtime 变量和模板。
- 不改变 `persona_text` 的最终来源优先级：先 snapshot formatter，若动态 persona injection 有 context，则覆盖为动态 context。
- 不改变 history 注入、private pre-bridge、guardrail silent、Bridge、SSE 或 non-streaming result。
- 不新增同步函数包装 awaitable。
