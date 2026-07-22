import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from core.legacy_adapter import ModelScoutAgent

@pytest.mark.asyncio
async def test_model_scout_agent_logic():
    """测试 Scout Agent 的分析逻辑是否正确映射到注册表指令"""
    written_models = []

    class FakeCatalogWriter:
        @property
        def adapter_id(self):
            return "fake_catalog"

        def upsert_models(self, models):
            written_models.extend(dict(model) for model in models)
            return len(models)

    agent = ModelScoutAgent(catalog_writer=FakeCatalogWriter())
    
    # Mock Provider
    mock_provider = MagicMock()
    # 模拟 LLM 返回的 JSON 格式解析结果
    mock_llm_json = json.dumps([
        {
            "id": "new-reasoning-model-v1",
            "intelligence": 9.8,
            "cost_input": 0.5,
            "cost_output": 1.5,
            "tags": ["reasoning", "smart"]
        }
    ])
    mock_provider.invoke_raw = AsyncMock(return_value=mock_llm_json)
    
    # 模拟搜索工具返回的数据，避免由于没有 scout_info 而触发真实搜索
    scout_info = "Discovery: new-reasoning-model-v1 launched with top performance."
    
    # 执行 Agent
    extracted_models = await agent.run(scout_info, mock_provider)
    
    # 验证提取到的模型数据
    assert len(extracted_models) == 1
    assert extracted_models[0]["id"] == "new-reasoning-model-v1"
    assert extracted_models[0]["intelligence"] == 9.8
    assert "reasoning" in extracted_models[0]["tags"]
    assert written_models == extracted_models
    
    # 验证 Provider 调用了正确的提示词
    mock_provider.invoke_raw.assert_called_once()
    args, kwargs = mock_provider.invoke_raw.call_args
    assert "new-reasoning-model-v1" in kwargs["query"]
    assert "模型目录情报提取器" in kwargs["system_prompt"]
    assert kwargs["model_tier"] == "reasoning"


@pytest.mark.asyncio
async def test_model_scout_rejects_invalid_output_without_writing_catalog():
    written_models = []

    class FakeCatalogWriter:
        @property
        def adapter_id(self):
            return "fake_catalog"

        def upsert_models(self, models):
            written_models.extend(models)
            return len(models)

    provider = MagicMock()
    provider.invoke_raw = AsyncMock(return_value='[{"provider":"unknown"}]')

    result = await ModelScoutAgent(FakeCatalogWriter()).run("已获取资料", provider)

    assert result == []
    assert written_models == []

@pytest.mark.asyncio
async def test_model_scout_search_trigger():
    """测试当传入情报为空时，是否正确触发了 Web 搜索"""
    agent = ModelScoutAgent()
    mock_provider = MagicMock()
    mock_provider.invoke_raw = AsyncMock(return_value="[]")
    
    # Mock 组合搜索工具
    with patch("core.legacy_adapter.search_and_extract_news") as mock_search:
        mock_search.return_value = "Mocked real-time news data"
        
        await agent.run("", mock_provider)
        
        # 验证 search_and_extract_news 被调用
        mock_search.assert_called_once()
        # 验证提取的情报传给了 LLM
        args, kwargs = mock_provider.invoke_raw.call_args
        assert "Mocked real-time news data" in kwargs["query"]
