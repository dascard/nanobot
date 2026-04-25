import pytest
from creatures.nanobot.prompts.skills.news_search.tool import WebTools
# 真实网络测试：需要外网可访问，且 HTTP 代理（SOCKS）环境正确配置
# 若环境不通则自动跳过，不阻塞 CI
_skip_live = pytest.mark.skipif(
    True,

    reason="需要外网连通（DDG/Trafilatura），当前环境 SOCKS 代理不可达"
)

@_skip_live
def test_live_search_real_results():
    """真实全网搜索测试：验证能否从互联网获取实时数据"""
    query = "DeepSeek AI news 2026"
    results = WebTools.search(query, max_results=3)
    
    # 验证是否拿到了结果
    assert len(results) > 0, "Real search should return at least one result"
    for r in results:
        assert r['title'], "Each result must have a title"
        assert r['href'].startswith("http"), "Each result must have a valid URL"
        print(f"  [Live Search Success] Found: {r['title']}")

@_skip_live
def test_live_extract_real_content():
    """真实网页抓取测试：验证能否正确从一个 AI 博客或新闻页提取正文"""
    # 使用一个相对稳定的 AI 相关链接
    test_url = "https://openai.com/news/"
    content = WebTools.extract(test_url)
    
    assert len(content) > 100, "Should extract a substantial amount of text from a news page"
    assert "OpenAI" in content or "AI" in content, "Content should contain relevant keywords"
    print(f"  [Live Extract Success] Extracted {len(content)} characters from {test_url}")

@_skip_live
def test_full_pipeline_intelligence_fetch():
    """验证完整的搜集摘要逻辑 (不含 LLM)"""
    from tools.web import search_and_extract_news
    
    report = search_and_extract_news("DeepSeek v4 release", max_results=1)
    
    assert "Title:" in report
    assert "URL:" in report
    assert "Content:" in report
    # 验证不是空报告
    assert len(report) > 50
    print("  [Live Pipeline Success] Intelligence report generated successfully.")
