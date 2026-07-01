# Prompt V2 Live Audit 收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 V2 在 live bridge 路径下遇到 PromptPlan 审计失败时 fail-fast，并明确默认切到 v2 前的验收清单。

**Architecture:** compiler 默认保持 preview 友好的 warning 模式，新增 strict audit 参数给 live 调用使用。bridge 的 v2 分支启用 strict audit，审计失败时结束 AgentRun、恢复工具裁剪状态并返回内部错误，不继续调用模型。Admin preview 的 v2 分支只依赖 V2 preview/compiler，v1 分支才导入 PromptAssembler。

**Tech Stack:** Python 3.12、pytest、FastAPI、KT bridge tracing。

---

### Task 1: strict audit 红灯测试

**Files:**
- Modify: `tests/test_prompt_v2.py`
- Modify: `tests/test_bridge_prompt_v2.py`

- [x] **Step 1: Write failing compiler test**

新增测试：当 audit 返回 issue 时，默认 compile 返回 warnings；`strict_audit=True` 抛出 `PromptAuditError`。

- [x] **Step 2: Write failing bridge test**

新增测试：v2 bridge 传入 `strict_audit=True`，遇到 `PromptAuditError` 不调用 `_process_event`。

- [x] **Step 3: Run red tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s \
  tests/test_prompt_v2.py::test_prompt_v2_strict_audit_raises_instead_of_returning_warning \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_fails_fast_when_prompt_audit_fails \
  -v
```

Expected before implementation: import or signature failure。

### Task 2: compiler 和 bridge 实现

**Files:**
- Modify: `core/prompt_v2/audit.py`
- Modify: `core/prompt_v2/compiler.py`
- Modify: `nanobot_kt/bridge.py`

- [x] **Step 1: Add PromptAuditError**

`PromptAuditError` 保存 issue 列表并把 issue 拼成异常消息。

- [x] **Step 2: Add strict_audit**

`compile_prompt_plan(..., strict_audit=True)` 在 audit failed 时抛出异常；默认仍返回 warnings。

- [x] **Step 3: Enable strict audit in bridge**

bridge 的 v2 live 路径调用 strict compiler，并在失败时结束 trace、恢复工具状态、返回系统错误。

- [x] **Step 4: Run green tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s \
  tests/test_prompt_v2.py::test_prompt_v2_strict_audit_raises_instead_of_returning_warning \
  tests/test_bridge_prompt_v2.py::test_bridge_engine_v2_fails_fast_when_prompt_audit_fails \
  -v
```

Expected: 2 passed。

### Task 3: preview 隔离和切换清单

**Files:**
- Modify: `api/admin_routes.py`
- Create: `docs/superpowers/specs/2026-05-23-prompt-v2-cutover-checklist.md`

- [x] **Step 1: Move PromptAssembler import to v1 branch**

`/prompt/effective-preview` 的 v2 分支只导入 V2 preview/compiler。

- [x] **Step 2: Document cutover checklist**

记录 v2 默认切换前必须满足的 bridge、preview、reply-test、Reply Eval、trace 和 canary 条件。

### Task 4: Verification

**Files:**
- Verify targeted behavior。

- [x] **Step 1: Run targeted prompt tests**

Run:

```bash
/home/dascard/anaconda3/bin/python -m pytest -s \
  tests/test_prompt_v2.py \
  tests/test_bridge_prompt_v2.py \
  tests/test_prompt_trace_admin.py::test_admin_prompt_and_trace_endpoints \
  tests/test_reply_admin.py \
  -v
```

Expected: all selected tests pass。

- [x] **Step 2: Run formatting check**

Run:

```bash
git diff --check
```

Expected: no output。
