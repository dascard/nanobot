import pytest


def test_web_search_registered_in_tool_plan(db_session):
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)

    assert "web_search" in plan.sent_tool_names
    schema = next(schema for schema in plan.sent_tool_schemas if schema["function"]["name"] == "web_search")
    props = schema["function"]["parameters"]["properties"]
    assert "provider" not in props


@pytest.mark.asyncio
async def test_web_search_tool_returns_structured_results(monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult, WebSearchResult
    from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool

    async def fake_search_enabled_providers(db, query, limit=5, provider_id=""):
        assert query == "nanobot"
        assert limit == 5
        assert provider_id == ""
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
    assert result.metadata["structured_content"]["results"][0]["title"] == "Nanobot"


@pytest.mark.asyncio
async def test_web_search_tool_accepts_legacy_max_results(monkeypatch):
    from core.web_search.search_runtime import WebSearchProviderResult
    from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool

    calls = []

    async def fake_search_enabled_providers(db, query, limit=5, provider_id=""):
        calls.append({"query": query, "limit": limit, "provider_id": provider_id})
        return WebSearchProviderResult(provider_id=provider_id or "searxng", results=[])

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.web_search.tool.search_enabled_providers",
        fake_search_enabled_providers,
    )

    result = await WebSearchTool()._execute(
        {"query": "nanobot", "max_results": 7, "provider": "brave"}
    )

    assert result.exit_code == 0
    assert calls == [{"query": "nanobot", "limit": 7, "provider_id": "brave"}]


def test_web_search_model_message_formatter_matches_tool_output(monkeypatch):
    from core.web_search.search_runtime import (
        WebSearchProviderResult,
        WebSearchResult,
        format_provider_result_for_model,
    )

    provider_result = WebSearchProviderResult(
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

    message = format_provider_result_for_model("nanobot", provider_result, limit=5)

    assert message.startswith("WEB_SEARCH_RESULTS_BEGIN")
    assert "QUERY: nanobot" in message
    assert "PROVIDER: searxng" in message
    assert "RESULT_COUNT: 1" in message
    assert "QUALITY: ok" in message
    assert "QUALITY_SCORE:" in message
    assert "QUALITY_REASON:" in message
    assert "URL: https://example.test/nanobot" in message
    assert "摘要: 搜索结果摘要" in message
    assert "只能基于以上 WEB_SEARCH_RESULTS 回答" in message
    assert message.endswith("WEB_SEARCH_RESULTS_END")


def test_web_search_prompt_declares_provider_is_runtime_selected():
    from pathlib import Path

    usage = Path("prompts.v2.default/tools/web_search/usage.md").read_text(encoding="utf-8")

    assert "系统按管理后台配置自动选择 provider" in usage
    assert "`provider` 留空" not in usage


@pytest.mark.asyncio
async def test_web_search_tool_reports_no_enabled_provider(monkeypatch):
    from core.web_search.search_runtime import WebSearchError
    from creatures.nanobot.prompts.skills.web_search.tool import WebSearchTool

    async def fake_search_enabled_providers(db, query, limit=5, provider_id=""):
        raise WebSearchError("no_enabled_provider", "没有启用的搜索 provider")

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.web_search.tool.search_enabled_providers",
        fake_search_enabled_providers,
    )

    result = await WebSearchTool()._execute({"query": "nanobot"})

    assert result.exit_code != 0
    assert "没有启用" in result.error
