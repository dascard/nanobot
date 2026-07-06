import pytest


def test_web_search_registered_in_tool_plan(db_session):
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(chat_type="private", runtime_preset="full", db=db_session)

    assert "web_search" in plan.sent_tool_names
    assert any(schema["function"]["name"] == "web_search" for schema in plan.sent_tool_schemas)


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

    assert message.startswith("web_search: query=nanobot provider=searxng count=1")
    assert "URL: https://example.test/nanobot" in message
    assert "摘要: 搜索结果摘要" in message


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
