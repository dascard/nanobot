# Prompt Runtime V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立 Prompt Runtime V2，并让 preview、reply-test、reply-eval 和 live bridge 能通过 `prompt_runtime.engine=v2` 使用同一个 `PromptPlan` compiler。

**Architecture:** V2 新建在 `core/prompt_v2/`，只依赖结构化上下文、模板目录、工具 schema 和现有 `build_chat_context()`。旧 `PromptAssembler`、`PromptManager`、legacy fragment builder 继续保留为 v1 回滚路径，V2 内部不调用它们。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、pytest、现有 KT bridge/OpenAI-compatible messages。

---

### Task 1: V2 Schema 与审计测试

**Files:**
- Create: `tests/test_prompt_v2.py`
- Create: `core/prompt_v2/__init__.py`
- Create: `core/prompt_v2/schema.py`
- Create: `core/prompt_v2/audit.py`

- [ ] **Step 1: Write failing tests**

覆盖：

- `PromptPlan` 为 frozen dataclass。
- `messages_without_current_user` 不含最后 user message。
- `current_user_content` 只取最后 user message。
- audit 能发现 `<user_input>`、`[RuntimeTool]`、`<persona_reference` 重复。

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: FAIL because `core.prompt_v2` does not exist.

- [ ] **Step 3: Implement minimal schema and audit**

实现 `PromptPlan`、`PromptCompileRequest`、`PromptAuditResult` 与 `audit_prompt_plan(plan)`。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: PASS for schema/audit tests.

### Task 2: Template Loader 与 Section Renderer

**Files:**
- Modify: `tests/test_prompt_v2.py`
- Create: `core/prompt_v2/template_loader.py`
- Create: `core/prompt_v2/section_renderer.py`
- Create: `prompts.v2.default/chat_group.md`
- Create: `prompts.v2.default/chat_private.md`
- Create: `prompts.v2.default/timing_gate.md`
- Create: `prompts.v2.default/reply_contract_retry.md`
- Create: `prompts.v2.default/memory_extract.md`
- Create: `prompts.v2.default/sql_analysis.md`

- [ ] **Step 1: Write failing tests**

覆盖：

- loader 从 `prompts.v2.default` 加载模板，不依赖 `PromptManager`。
- 群聊模板包含群聊规则、不包含私聊规则。
- 私聊模板包含私聊规则、不包含群聊规则。
- renderer 为每个 section 生成稳定 sha256。

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: FAIL on missing loader/renderer/templates.

- [ ] **Step 3: Implement loader, renderer and templates**

模板内容从现有成熟 `prompts.default/group_chat.md`、`prompts.default/private_chat.md` 整理迁移，但删除当前用户输入和历史拼装变量。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: PASS.

### Task 3: Compiler 与 Context Adapter

**Files:**
- Modify: `tests/test_prompt_v2.py`
- Create: `core/prompt_v2/context_adapters.py`
- Create: `core/prompt_v2/compiler.py`
- Create: `core/prompt_v2/preview.py`

- [ ] **Step 1: Write failing tests**

覆盖：

- 群聊 message 顺序符合 spec。
- 私聊 message 顺序符合 spec。
- `prompt_sha256`、`section_hashes`、`token_estimate`、`warnings`、`debug` 存在。
- 当前输入、runtime tool prompt、persona reference 只出现一次。
- V2 compiler 源码不导入 `core.legacy_prompt_runtime`。

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: FAIL on missing compiler.

- [ ] **Step 3: Implement compiler**

`compile_prompt_plan(request)` 接收 `PromptCompileRequest`，按 chat_type 加载模板、渲染 runtime/context/persona/history/tool/current user sections，并执行 audit。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_prompt_v2.py -v`
Expected: PASS.

### Task 4: Admin Effective Preview 接入 V2

**Files:**
- Modify: `api/admin_routes.py`
- Modify: `tests/test_prompt_trace_admin.py`

- [ ] **Step 1: Write failing tests**

`/prompt/effective-preview` 传 `engine=v2` 时应返回：

- `engine == "v2"`
- `request_json.messages == messages`
- `prompt_plan.prompt_sha256`
- `section_hashes`
- `warnings`
- `debug`

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_prompt_trace_admin.py -v`
Expected: FAIL because request model does not accept engine.

- [ ] **Step 3: Implement preview adapter**

在 v2 分支调用 `core.prompt_v2.preview.build_preview_plan()`；v1 保持原行为。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_prompt_trace_admin.py -v`
Expected: PASS.

### Task 5: Reply Test 与 Reply Eval 接入 V2

**Files:**
- Modify: `api/admin_routes.py`
- Modify: `tests/test_reply_admin.py`

- [ ] **Step 1: Write failing tests**

覆盖：

- `/reply-test/run` 支持 `prompt_engine=v2`，metadata 传 `prompt_runtime_engine_override=v2`。
- `v1_baseline` 映射到 `prompt_engine=v1` 且 retry disabled。
- `v2_prompt_only` 映射到 `prompt_engine=v2` 且 retry disabled。
- `v2_code_retry` 映射到 `prompt_engine=v2` 且 retry enabled。

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_reply_admin.py -v`
Expected: FAIL on unsupported enum values.

- [ ] **Step 3: Implement mapping**

保留旧 `baseline/prompt_only/code_retry` 兼容，同时新增 v1/v2 命名。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_reply_admin.py -v`
Expected: PASS.

### Task 6: Bridge 接入 `prompt_runtime.engine=v2`

**Files:**
- Modify: `nanobot_kt/bridge.py`
- Modify: `core/config_registry.py`
- Create: `tests/test_bridge_prompt_v2.py`

- [ ] **Step 1: Write failing tests**

覆盖：

- engine override 为 `v2` 时 bridge 调用 `compile_prompt_plan`。
- bridge 用 plan pre-event messages reset conversation。
- user event content 等于 plan current user content。
- engine v2 不读取 `prompt_system.mode`。

- [ ] **Step 2: Run red test**

Run: `python -m pytest tests/test_bridge_prompt_v2.py -v`
Expected: FAIL because bridge has no engine routing.

- [ ] **Step 3: Implement bridge v2 branch**

新增 `_prompt_runtime_engine()`；v1 使用旧 `PromptAssembler` 分支，v2 使用 `PromptCompileRequest` + `compile_prompt_plan`。更新 AgentRun prompt metadata 和 PromptRenderLog 审计字段。

- [ ] **Step 4: Run green test**

Run: `python -m pytest tests/test_bridge_prompt_v2.py -v`
Expected: PASS.

### Task 7: Verification

**Files:**
- Modify as needed based on failed tests.

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_prompt_v2.py tests/test_prompt_trace_admin.py tests/test_reply_admin.py tests/test_bridge_prompt_v2.py -v`
Expected: 0 failures.

- [ ] **Step 2: Run full tests**

Run: `python -m pytest tests/ -v`
Expected: 0 failures.

- [ ] **Step 3: Run real-chain smoke**

Run with proxies cleared:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
```

Then call preview and one reply dry-run with `prompt_engine=v2`; inspect latest `LLMApiRequestLog.request_json.messages` and confirm it is structurally equal to preview `request_json.messages`.
