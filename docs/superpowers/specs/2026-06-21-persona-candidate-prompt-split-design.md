# Persona 候选 Prompt 拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」仍未完成，当前剩余目标包括
`api/admin_routes.py`、`api/routes.py` 和 `core/persona_preprocess.py`。
其中 `core/persona_preprocess.py` 当前为 857 行，职责包含候选提取 prompt、日志格式化、
embedding 懒加载、画像状态机、聚类、冲突检测、衰减和摘要构建。

本阶段选择 `core/persona_preprocess.py` 作为低风险小步收尾。目标不是重写画像状态机，
而是先抽出最独立的候选提取 prompt 与日志格式化边界，让原文件低于 800 行。

## 目标

1. 新增 `core/persona_candidate_prompt.py`，承载候选提取 prompt 和日志格式化纯函数。
2. `core/persona_preprocess.py` 继续提供旧导入路径，外部调用方不需要迁移。
3. `core/persona_preprocess.py` 行数降到 800 行以下。
4. 保持候选提取 prompt 文案、日志清洗、schema 关键字和旧测试行为不变。

## 非目标

1. 不移动 `PersonaStateMachine`。
2. 不移动 `embed_text()`、`_get_embedder()`、`_get_nli()`、`_EMBEDDER_MODEL` 或 `_NLI_MODEL`。
3. 不改变 `core.persona_preprocess.embed_text` 的 monkeypatch 契约。
4. 不改变数据库写入、状态机合并、衰减、冲突检测或摘要构建行为。
5. 不改 prompt runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。

## 当前依赖

旧模块中需要保持兼容的符号：

- `CANDIDATE_EXTRACTION_SYSTEM_PROMPT`
- `filter_user_messages()`
- `format_candidate_logs()`
- `build_candidate_extraction_prompt()`
- `PersonaStateMachine`
- `content_hash()`
- `embed_text()`
- `_EMBEDDER_MODEL`
- `_NLI_MODEL`
- `_get_nli()`

已知调用方包括：

- `api/admin/persona_routes.py`
- `core/legacy_adapter.py`
- `creatures/nanobot/prompts/skills/persona_update/tool.py`
- `tests/test_persona_preprocess.py`
- `tests/test_admin_api.py`

## 方案

新增模块 `core/persona_candidate_prompt.py`：

- 从 `core.context_builder` 导入 `sanitize_prompt_text`。
- 定义 `CANDIDATE_EXTRACTION_SYSTEM_PROMPT`。
- 定义 `filter_user_messages(logs)`，只保留 `role=user` 日志。
- 定义 `format_candidate_logs(logs)`，继续按 `[log_id=...] user: ...` 格式输出，并继续用
  `sanitize_prompt_text(..., 500)` 清洗用户内容边界。
- 定义 `build_candidate_extraction_prompt(facts_summary, logs_text)`，继续支持 `logs_text`
  为字符串或日志 dict 列表。

调整 `core/persona_preprocess.py`：

- 删除本地 prompt 常量和三个纯函数实现。
- 从 `core.persona_candidate_prompt` 导入同名符号。
- 保留旧模块下同名符号，作为 re-export 兼容路径。
- 其他工具函数、状态机和模型懒加载保持原位。

## 测试策略

先补红灯测试：

- 新增 `tests/test_persona_candidate_prompt_split.py`。
- 断言旧模块的四个符号与新模块是同一个对象。
- 断言 `core/persona_preprocess.py` 行数低于 800。

实现后运行：

- `tests/test_persona_candidate_prompt_split.py -q`
- `tests/test_persona_preprocess.py::TestBuildPrompt -q`
- `tests/test_persona_preprocess.py -m "not slow" -q`
- `tests/test_admin_api.py -k "persona_update_fact_rejects_duplicate" -q`
- `python -m compileall core/persona_preprocess.py core/persona_candidate_prompt.py -q`
- `git diff --check`
- `python -m pytest tests/ -v`

## 风险与控制

风险：调用方仍从 `core.persona_preprocess` 导入候选 prompt 符号。

控制：旧模块继续 re-export，不要求调用方改路径。

风险：状态机测试通过 monkeypatch `core.persona_preprocess.embed_text` 控制 embedding。

控制：本阶段不移动 `embed_text()` 或 `PersonaStateMachine`，不改变 monkeypatch 路径。

风险：候选提取 prompt 文案或 schema 关键字变动影响 LLM 输出。

控制：迁移时只做机械移动；`TestBuildPrompt` 和新增 facade 测试锁定行为。

风险：抽出模块后产生循环依赖。

控制：新模块只依赖 `core.context_builder.sanitize_prompt_text`，不导入
`core.persona_preprocess`、数据库模型、NumPy 或模型加载逻辑。

## 验收标准

1. `core/persona_preprocess.py` 低于 800 行。
2. `core.persona_preprocess` 的旧导入路径仍可导入候选 prompt 符号。
3. 候选日志清洗和候选提取 prompt schema 测试通过。
4. 全量测试通过。
5. 本阶段不新增 `asyncio.run()`，不新增同步函数包装 awaitable。
