# Web Search Provider 全量测试与 Bot 接入实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让所有 Web Search provider 都可测试，并新增 bot 可调用的 `web_search` 工具。

**架构：** 新增 `core/web_search/search_runtime.py` 作为统一 adapter 层，admin 测试接口和 KT 工具共享同一套搜索逻辑。`web_search` 工具通过 ToolPlan、KT config、Prompt V2 usage 模板接入，按已启用 provider 做顺序 fallback。

**技术栈：** FastAPI、SQLAlchemy、aiohttp、KT BaseTool、pytest、React/Vite。

---

## 文件结构

- 创建：`core/web_search/search_runtime.py`
  统一 provider adapter、结果归一化、fallback、脱敏错误。
- 修改：`core/web_search/provider_tests.py`
  改为调用 `search_runtime.search_provider()`，删除 `not_implemented` 分支。
- 修改：`core/web_search/provider_catalog.py`
  全部 provider 标记 `testable=True`，更新 You/Jina 默认 Base URL 与 capabilities。
- 创建：`creatures/nanobot/prompts/skills/web_search/tool.py`
  KT BaseTool 实现。
- 创建：`nanobot_kt/tools/web_search.py`
  creature config import bridge。
- 修改：`creatures/nanobot/config.yaml`
  加载 `web_search` 工具。
- 修改：`core/tool_registry.py`
  注册工具元数据。
- 修改：`core/tool_schema_preview.py`
  增加静态 OpenAI-compatible schema。
- 创建：`prompts.v2.default/tools/web_search/usage.md`
- 创建：`data/prompts_v2/tools/web_search/usage.md`
- 修改：`core/prompt_v2/template_registry.py`
  增加 `web_search` alias。
- 修改：`webui/src/features/web-search/WebSearchPage.jsx`
  删除“暂不测试”文案。
- 修改：`tests/test_admin_web_search_routes.py`
- 创建：`tests/test_web_search_tool.py`
- 修改：`tests/test_tool_schema_config.py`
- 修改：`tests/test_webui_app_split.py`

## 任务 1：先写失败测试

- [ ] **步骤 1：更新 admin provider 测试**

在 `tests/test_admin_web_search_routes.py` 中增加断言：

```python
def test_all_web_search_providers_are_testable(client, auth_header):
    data = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))
    assert all(item["testable"] is True for item in data["providers"])
```

并把旧的 `test_test_provider_not_implemented_returns_ok_false` 改成 Jina mock 成功测试：

```python
@pytest.mark.asyncio
async def test_jina_provider_test_uses_search_runtime(monkeypatch):
    from core.web_search.provider_settings import ProviderResolvedConfig
    from core.web_search.provider_tests import test_provider

    async def fake_search_provider(config, query, limit):
        assert config.provider_id == "jina"
        return [object(), object()]

    monkeypatch.setattr("core.web_search.provider_tests.search_provider", fake_search_provider)
    config = ProviderResolvedConfig("jina", True, "https://s.jina.ai", "jina-secret", True, "db")
    result = await test_provider("jina", config, "nanobot")

    assert result.ok is True
    assert result.sample_count == 2
```

- [ ] **步骤 2：新增 bot 工具测试**

创建 `tests/test_web_search_tool.py`：

```python
import pytest


def test_web_search_registered_in_tool_plan(db_session):
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)

    assert "web_search" in plan.sent_tool_names
    assert any(schema["function"]["name"] == "web_search" for schema in plan.sent_tool_schemas)


@pytest.mark.asyncio
async def test_web_search_tool_returns_structured_results(monkeypatch):
    from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool
    from core.web_search.search_runtime import WebSearchResult, WebSearchProviderResult

    async def fake_search_enabled_providers(db, query, limit, provider_id=""):
        return WebSearchProviderResult(
            provider_id="searxng",
            results=[
                WebSearchResult(
                    provider="searxng",
                    title="Nanobot",
                    url="https://example.test/nanobot",
                    snippet="搜索结果摘要",
                )
            ],
        )

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.web_search.tool.search_enabled_providers",
        fake_search_enabled_providers,
    )

    result = await WebSearchTool()._execute({"query": "nanobot", "limit": 5})

    assert result.exit_code == 0
    assert "https://example.test/nanobot" in result.output
    assert result.metadata["structured_content"]["provider_id"] == "searxng"


@pytest.mark.asyncio
async def test_web_search_tool_reports_no_enabled_provider(monkeypatch):
    from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool
    from core.web_search.search_runtime import WebSearchError

    async def fake_search_enabled_providers(db, query, limit, provider_id=""):
        raise WebSearchError("no_enabled_provider", "没有启用的搜索 provider")

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.web_search.tool.search_enabled_providers",
        fake_search_enabled_providers,
    )

    result = await WebSearchTool()._execute({"query": "nanobot"})

    assert result.exit_code != 0
    assert "没有启用" in result.error
```

- [ ] **步骤 3：运行测试确认失败**

运行：

```bash
python -m pytest tests/test_admin_web_search_routes.py::test_all_web_search_providers_are_testable tests/test_web_search_tool.py -v
```

预期：至少因为 `web_search` 未注册、`jina` 不可测试而 FAIL。

## 任务 2：实现统一搜索运行时

- [ ] **步骤 1：创建 `search_runtime.py`**

实现内容：

```python
@dataclass(frozen=True)
class WebSearchResult:
    provider: str
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    score: float | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class WebSearchProviderResult:
    provider_id: str
    results: list[WebSearchResult]
    elapsed_ms: int = 0


class WebSearchError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, provider_id: str = "", status: int | None = None):
        ...
```

每个 `_search_<provider>()` 都只返回 `list[WebSearchResult]` 或抛 `WebSearchError`。

- [ ] **步骤 2：接入 10 个 provider**

实现：

```python
async def search_provider(config: ProviderResolvedConfig, query: str, limit: int = 5) -> WebSearchProviderResult:
    ...

async def search_enabled_providers(db, query: str, limit: int = 5, provider_id: str = "") -> WebSearchProviderResult:
    ...
```

fallback 规则：

- 指定 `provider_id`：只测/搜该 provider。
- 未指定：按 catalog 顺序查启用 provider。
- 需要 key 且无 key：跳过，记录错误。
- 所有 provider 失败：抛最后一个错误；无启用 provider：抛 `no_enabled_provider`。

- [ ] **步骤 3：更新 `provider_tests.py`**

删除 provider 私有 HTTP 调用，改为：

```python
from core.web_search.search_runtime import WebSearchError, search_provider

try:
    result = await search_provider(config, query, limit=3)
    return _success(provider_id, start, len(result.results))
except WebSearchError as exc:
    return _failure(provider_id, start, exc.message, exc.error_code, config.api_key)
```

- [ ] **步骤 4：运行 admin 测试**

运行：

```bash
python -m pytest tests/test_admin_web_search_routes.py -v
```

预期：PASS。

## 任务 3：接入 bot 工具链

- [ ] **步骤 1：创建 KT 工具**

创建 `creatures/nanobot/prompts/skills/web_search/tool.py`，实现 `WebSearchTool(BaseTool)`：

- `tool_name` 返回 `web_search`。
- schema 参数：`query`、`limit`、`provider`。
- `_execute()` 中使用 `UnitOfWork()` 调 `search_enabled_providers()`。
- 输出最多 10 条，metadata 写 `structured_content`。

- [ ] **步骤 2：创建 import bridge**

创建 `nanobot_kt/tools/web_search.py`：

```python
from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool

__all__ = ["WebSearchTool"]
```

- [ ] **步骤 3：注册工具**

修改：

- `creatures/nanobot/config.yaml` 加 tool。
- `core/tool_registry.py` 加 `web_search`。
- `core/tool_schema_preview.py` 加静态 schema。
- `core/prompt_v2/template_registry.py` 加 alias。
- 新增两个 usage 模板。

- [ ] **步骤 4：运行工具测试**

运行：

```bash
python -m pytest tests/test_web_search_tool.py tests/test_tool_schema_config.py -v
```

预期：PASS。

## 任务 4：前端与文案收尾

- [ ] **步骤 1：更新前端文案**

`webui/src/features/web-search/WebSearchPage.jsx`：

- `not_implemented` 文案删除或保留为兜底未知错误。
- Badge 从“暂不测试”改为“可测试”。
- `stats.testable` 应等于 provider 总数。

- [ ] **步骤 2：更新静态测试**

`tests/test_webui_app_split.py` 添加：

```python
def test_web_search_page_no_longer_shows_not_tested_copy():
    source = WEB_SEARCH_PAGE.read_text(encoding="utf-8")
    assert "暂不测试" not in source
    assert "暂不支持连接测试" not in source
```

- [ ] **步骤 3：运行前端验证**

运行：

```bash
python -m pytest tests/test_webui_app_split.py -v
cd webui && npm run build
```

预期：pytest PASS，Vite build exit 0。

## 任务 5：最终验证

- [ ] **步骤 1：运行核心回归**

```bash
python -m pytest tests/test_admin_web_search_routes.py tests/test_web_search_tool.py tests/test_tool_schema_config.py tests/test_webui_app_split.py tests/test_prompt_v2_tool_template_integration.py -v
```

- [ ] **步骤 2：检查空白**

```bash
git diff --check
```

- [ ] **步骤 3：全量测试**

提交前运行：

```bash
python -m pytest tests/ -v
```

若全量测试失败，只修复与本次变更相关的问题；不要回滚用户已有脏文件。
