"""Web Search provider 元数据目录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProviderCatalogItem:
    id: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    requires_api_key: bool
    supports_base_url: bool
    default_base_url: str
    docs_url: str
    enabled_by_default: bool = False
    testable: bool = False
    api_key_optional: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


PROVIDER_CATALOG: dict[str, ProviderCatalogItem] = {
    "searxng": ProviderCatalogItem(
        id="searxng",
        name="SearXNG",
        description="自托管 meta search provider，适合作为通用搜索入口。",
        capabilities=("search",),
        requires_api_key=False,
        supports_base_url=True,
        default_base_url="",
        docs_url="https://docs.searxng.org/dev/search_api.html",
        testable=True,
    ),
    "serper": ProviderCatalogItem(
        id="serper",
        name="Serper",
        description="Google SERP API provider。",
        capabilities=("search",),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://google.serper.dev",
        docs_url="https://serper.dev/signup",
        testable=True,
    ),
    "brave": ProviderCatalogItem(
        id="brave",
        name="Brave Search",
        description="Brave Search API，提供通用网页搜索。",
        capabilities=("search",),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://api.search.brave.com/res/v1",
        docs_url="https://brave.com/search/api/",
        testable=True,
    ),
    "tavily": ProviderCatalogItem(
        id="tavily",
        name="Tavily",
        description="面向 Agent 和 RAG 的搜索 API。",
        capabilities=("search", "extract"),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://api.tavily.com",
        docs_url="https://docs.tavily.com/documentation/quickstart",
        testable=True,
    ),
    "ddgs": ProviderCatalogItem(
        id="ddgs",
        name="DuckDuckGo",
        description="基于本地 DDGS/duckduckgo_search 依赖的免费搜索 fallback。",
        capabilities=("search",),
        requires_api_key=False,
        supports_base_url=False,
        default_base_url="",
        docs_url="https://pypi.org/project/duckduckgo-search/",
        testable=True,
    ),
    "exa": ProviderCatalogItem(
        id="exa",
        name="Exa",
        description="语义网页搜索 API。",
        capabilities=("search",),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://api.exa.ai",
        docs_url="https://exa.ai/docs/reference/search-api-guide",
        testable=True,
    ),
    "firecrawl": ProviderCatalogItem(
        id="firecrawl",
        name="Firecrawl",
        description="网页抓取、抽取与搜索 API。",
        capabilities=("search", "crawl", "extract"),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://api.firecrawl.dev",
        docs_url="https://docs.firecrawl.dev/api-reference/introduction",
        testable=True,
    ),
    "linkup": ProviderCatalogItem(
        id="linkup",
        name="Linkup",
        description="通用 Web Search API。",
        capabilities=("search",),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://api.linkup.so",
        docs_url="https://docs.linkup.so/pages/documentation/platform/authentication",
        testable=True,
    ),
    "you": ProviderCatalogItem(
        id="you",
        name="You.com",
        description="You.com 搜索 API。",
        capabilities=("search",),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://ydc-index.io",
        docs_url="https://you.com/docs/administration/api-keys",
        testable=True,
    ),
    "jina": ProviderCatalogItem(
        id="jina",
        name="Jina Search",
        description="Jina Reader/Search 服务，可作为搜索与网页抽取组件。",
        capabilities=("search", "extract"),
        requires_api_key=True,
        supports_base_url=True,
        default_base_url="https://s.jina.ai",
        docs_url="https://api.jina.ai/docs",
        testable=True,
    ),
}


def list_provider_catalog() -> list[ProviderCatalogItem]:
    return list(PROVIDER_CATALOG.values())


def iter_provider_catalog() -> Iterable[ProviderCatalogItem]:
    return PROVIDER_CATALOG.values()


def get_provider_catalog(provider_id: str) -> ProviderCatalogItem | None:
    return PROVIDER_CATALOG.get(provider_id)


def is_known_provider(provider_id: str) -> bool:
    return provider_id in PROVIDER_CATALOG


def env_name_for(provider_id: str, field: str) -> str:
    normalized = provider_id.upper().replace("-", "_")
    return f"WEB_SEARCH_{normalized}_{field.upper()}"
