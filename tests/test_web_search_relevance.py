from core.web_search.search_runtime import WebSearchResult


def test_cjk_query_accepts_word_matched_result():
    from core.web_search.relevance import judge_search_relevance

    result = WebSearchResult(
        provider="searxng",
        title="上海天气预报,上海7天天气预报,上海15天天气预报",
        url="https://www.weather.com.cn/weather/101020100.shtml",
        snippet="上海今日天气和未来一周天气预报，及时准确发布中央气象台天气信息。",
    )

    decision = judge_search_relevance("上海天气", [result])

    assert decision.ok is True
    assert decision.score >= 0.5
    assert "上海" in decision.matched_terms
    assert "天气" in decision.matched_terms


def test_weather_query_rejects_unmatched_result_without_brand_blacklist():
    from core.web_search.relevance import judge_search_relevance

    result = WebSearchResult(
        provider="searxng",
        title="Proton VPN Download",
        url="https://protonvpn.com/download",
        snippet="Download Proton VPN for Windows, macOS, Linux, Android and iOS.",
    )

    decision = judge_search_relevance("上海天气", [result])

    assert decision.ok is False
    assert decision.score < 0.5
    assert decision.reason == "结果未充分命中 query 关键词"
    assert decision.matched_terms == []


def test_english_technical_query_accepts_matching_result():
    from core.web_search.relevance import judge_search_relevance

    result = WebSearchResult(
        provider="brave",
        title="datetime — Basic date and time types",
        url="https://docs.python.org/3/library/datetime.html",
        snippet="Python 3.10 datetime timezone and UTC offset examples.",
    )

    decision = judge_search_relevance("Python 3.10 datetime UTC", [result])

    assert decision.ok is True
    assert decision.score >= 0.5
    assert "python" in decision.matched_terms
    assert "datetime" in decision.matched_terms


def test_empty_results_are_low_relevance():
    from core.web_search.relevance import judge_search_relevance

    decision = judge_search_relevance("上海天气", [])

    assert decision.ok is False
    assert decision.score == 0.0
    assert "无搜索结果" in decision.reason
