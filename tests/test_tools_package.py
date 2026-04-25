import pytest
from unittest.mock import MagicMock, patch
from creatures.nanobot.prompts.skills.news_search.tool import WebTools, search_and_extract_news

def test_web_search_mock():
    """测试 WebSearchTool 是否能正确处理搜刮结果"""
    mock_results = [
        {"title": "AI News 1", "href": "http://test1.com", "body": "Snippet 1"},
        {"title": "AI News 2", "href": "http://test2.com", "body": "Snippet 2"},
    ]
    
    with patch("creatures.nanobot.prompts.skills.news_search.tool.DDGS") as mock_ddgs:
        # Mock DDGS context manager and text method
        mock_instance = mock_ddgs.return_value.__enter__.return_value
        mock_instance.text.return_value = mock_results
        
        results = WebTools.search("test query")
        
        assert len(results) == 2
        assert results[0]["title"] == "AI News 1"
        assert results[1]["href"] == "http://test2.com"

def test_web_extract_mock():
    """测试网页内容提取工具"""
    with patch("creatures.nanobot.prompts.skills.news_search.tool.trafilatura.fetch_url") as mock_fetch, \
         patch("creatures.nanobot.prompts.skills.news_search.tool.trafilatura.extract") as mock_extract:
        
        mock_fetch.return_value = "<html>content</html>"
        mock_extract.return_value = "Extracted Plain Text"
        
        content = WebTools.extract_web_content("http://example.com")
        
        assert content == "Extracted Plain Text"
        mock_fetch.assert_called_once_with("http://example.com", timeout=5)

def test_combined_news_tool():
    """测试组合出的新闻搜集工具逻辑"""
    with patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.search") as mock_search, \
         patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.extract_web_content") as mock_extract:
        
        mock_search.return_value = [
            {"title": "Title A", "href": "url_a", "body": "body_a"}
        ]
        mock_extract.return_value = "Long content from A"
        
        final_report = search_and_extract_news("query")
        
        assert "Title A" in final_report
        assert "Long content from A" in final_report
        assert "url_a" in final_report
