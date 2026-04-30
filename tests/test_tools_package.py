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
         patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.extract_web_content") as mock_extract, \
         patch("creatures.nanobot.prompts.skills.news_search.tool._model_should_deepen") as mock_deepen:
        
        mock_search.return_value = [
            {
                "title": "Title A",
                "href": "https://example.com/a",
                "body": "body_a",
                "search_strategy": "web_ddg",
            }
        ]
        mock_extract.return_value = "Long content from A"
        mock_deepen.return_value = (False, "test")
        
        final_report = search_and_extract_news("query")
        
        assert "# AI 资讯速报" in final_report
        assert "## 结果概览" in final_report
        assert "| 序号 | 标题 | 来源 | 质量分 |" in final_report
        assert "## 详细条目" in final_report
        assert "### 1. Title A" in final_report
        assert "Title A" in final_report
        assert "Long content from A" in final_report
        assert "https://example.com/a" in final_report
        assert "query" in final_report


def test_combined_news_tool_output_matches_qqbot_markdown_render_patterns():
    """输出应包含 QQbot 复杂 Markdown 检测所需的标题和表格。"""
    with patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.search") as mock_search, \
         patch("creatures.nanobot.prompts.skills.news_search.tool.WebTools.extract_web_content") as mock_extract, \
         patch("creatures.nanobot.prompts.skills.news_search.tool._model_should_deepen") as mock_deepen:

        mock_search.return_value = [
            {
                "title": "DeepSeek 新发布",
                "href": "https://example.com/deepseek",
                "body": "价格更新与免费额度",
                "search_strategy": "rss:test",
            }
        ]
        mock_extract.return_value = "DeepSeek 提供了新的免费额度和更低的 token 价格。"
        mock_deepen.return_value = (True, "planner")

        final_report = search_and_extract_news("deepseek 最新资讯")

        assert "# AI 资讯速报" in final_report
        assert "## 高价值提醒" in final_report
        assert "| 序号 | 标题 | 来源 | 质量分 |" in final_report
        assert "### 1. DeepSeek 新发布" in final_report
