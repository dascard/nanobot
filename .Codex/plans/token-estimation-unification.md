# Token 估算统一实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用 `core.token_utils.estimate_tokens()` 收敛项目内多套 token 粗估实现。

**架构：** 新增共享工具模块，旧模块保留原函数名并代理到共享实现。业务窗口、prompt 预览和审计逻辑不改上限，只统一估算公式。

**技术栈：** Python、pytest、现有 FastAPI / prompt / session memory 模块。

---

### 任务 1：共享工具与一致性测试

**文件：**
- 创建：`core/token_utils.py`
- 创建：`tests/test_token_utils.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_estimate_tokens_counts_cjk_ascii_and_other_unicode():
    from core.token_utils import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") == 2
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("🙂🙂") == 1
    assert estimate_tokens("你a🙂") == 2
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_token_utils.py -q -p no:cacheprovider`
预期：模块不存在导致失败。

- [ ] **步骤 3：实现共享 helper**

在 `core/token_utils.py` 中实现 `is_cjk_char()` 和 `estimate_tokens()`。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -B -m pytest tests/test_token_utils.py -q -p no:cacheprovider`
预期：通过。

### 任务 2：迁移旧入口

**文件：**
- 修改：`core/context_builder.py`
- 修改：`app/session_memory/windowing.py`
- 修改：`core/prompts/manager.py`
- 修改：`core/prompt_v2/section_renderer.py`
- 修改：`api/admin_routes.py`
- 修改：`core/legacy_adapter.py`
- 测试：`tests/test_token_utils.py`

- [ ] **步骤 1：编写一致性测试**

```python
def test_legacy_token_estimators_share_same_formula():
    from api.admin_routes import _prompt_metrics
    from app.session_memory.windowing import estimate_tokens as window_tokens
    from core.context_builder import estimate_tokens as context_tokens
    from core.legacy_adapter import PromptAuditorAgent
    from core.prompt_v2.section_renderer import estimate_tokens as section_tokens
    from core.prompts.manager import _estimate_tokens as prompt_tokens
    from core.token_utils import estimate_tokens

    text = "你好 abc 🙂 全角："
    expected = estimate_tokens(text)

    assert context_tokens(text) == expected
    assert window_tokens(text) == expected
    assert section_tokens(text) == expected
    assert prompt_tokens(text) == expected
    assert _prompt_metrics(text)["estimated_tokens"] == expected
    assert PromptAuditorAgent._estimate_tokens(text) == expected
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -B -m pytest tests/test_token_utils.py -q -p no:cacheprovider`
预期：至少 context/windowing/legacy 入口与共享公式不一致。

- [ ] **步骤 3：迁移实现**

各旧入口 import `core.token_utils.estimate_tokens`。`PromptAuditorAgent` 对空文本保留
`max(1, estimate_tokens(text))` 的旧语义，其余路径直接返回共享结果。

- [ ] **步骤 4：运行目标测试**

运行：
`python -B -m pytest tests/test_token_utils.py tests/test_history.py tests/test_session_memory.py tests/test_prompt_v2.py tests/test_prompt_v2_template_admin.py -q -p no:cacheprovider --durations=20`

预期：通过。

### 任务 3：完整验证与提交

- [ ] **步骤 1：完整测试**

运行：`unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -v -p no:cacheprovider --durations=20`
预期：`0 failures`。

- [ ] **步骤 2：显式暂存**

运行：
`git add core/token_utils.py tests/test_token_utils.py core/context_builder.py app/session_memory/windowing.py core/prompts/manager.py core/prompt_v2/section_renderer.py api/admin_routes.py core/legacy_adapter.py docs/superpowers/specs/2026-06-17-token-estimation-unification-design.md .Codex/plans/token-estimation-unification.md`

- [ ] **步骤 3：提交**

提交信息：
`refactor(token): 统一 token 估算入口`
