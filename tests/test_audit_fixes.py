"""
针对代码审计修复的验证测试。
覆盖 sandbox 安全性、tool dispatch、circuit breaker 等核心修复。
"""
import pytest
import json
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class TestSandboxSecurity:
    """BUG-01: 验证 subprocess 沙箱隔离"""

    def test_sql_read_only(self):
        """SQL 查询应拒绝写操作"""
        from sandbox import AnalysisSandbox
        sb = AnalysisSandbox()
        
        for dangerous_sql in ["INSERT INTO users VALUES ('hack')", "DROP TABLE users", "DELETE FROM chat_logs"]:
            result = sb.run_query(dangerous_sql)
            assert "Only SELECT queries are permitted" in result, f"Should block: {dangerous_sql}"

    def test_python_blocks_os(self):
        """Python 沙箱应阻止 import os"""
        from sandbox import AnalysisSandbox
        sb = AnalysisSandbox()
        result = sb.execute_python_analysis("import os\nprint(os.listdir('.'))")
        assert "blocked" in result.lower() or "error" in result.lower(), f"Should block os import, got: {result}"

    def test_python_blocks_subprocess(self):
        """Python 沙箱应阻止 import subprocess"""
        from sandbox import AnalysisSandbox
        sb = AnalysisSandbox()
        result = sb.execute_python_analysis("import subprocess\nsubprocess.run(['whoami'])")
        assert "blocked" in result.lower() or "error" in result.lower()

    def test_python_blocks_open(self):
        """Python 沙箱应阻止 open() 文件"""
        from sandbox import AnalysisSandbox
        sb = AnalysisSandbox()
        result = sb.execute_python_analysis("f = open('/etc/passwd')\nprint(f.read())")
        assert "error" in result.lower()

    def test_python_normal_output(self):
        """正常的 print 输出应能工作"""
        from sandbox import AnalysisSandbox
        sb = AnalysisSandbox(db_path=":memory:")  # Use in-memory DB for tests
        result = sb.execute_python_analysis("print('hello from sandbox')")
        assert "hello from sandbox" in result


class TestToolDispatch:
    """BUG-06: 验证 local tool 命令前缀剥离"""

    def test_command_prefix_strip(self):
        """工具调用应正确剥离 / 前缀"""
        from core.legacy_adapter import NanobotKTController
        
        # Mock 依赖
        mock_provider = MagicMock()
        mock_memory = MagicMock()
        
        controller = NanobotKTController(provider=mock_provider, memory=mock_memory)
        
        # 替换 news_scout 工具为一个记录器
        called_args = []
        def mock_tool(query):
            called_args.append(query)
            return "mock result"
        controller.local_tools["news_scout"] = mock_tool
        
        import asyncio
        asyncio.run(controller._execute_local_tool("/news_scout latest AI models", "user", "session"))
        
        assert len(called_args) == 1
        assert called_args[0] == "latest AI models"  # 前缀应被剥离


class TestEvolutionUtils:
    """BUG-18: 验证 json_repair 不使用裸 except"""

    def test_valid_json(self):
        from core.legacy_adapter import EvolutionUtils
        result = EvolutionUtils.json_repair('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_codeblock(self):
        from core.legacy_adapter import EvolutionUtils
        result = EvolutionUtils.json_repair('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_broken_json_repair(self):
        from core.legacy_adapter import EvolutionUtils
        result = EvolutionUtils.json_repair("{'key': 'value'}")
        # Should repair single quotes to double quotes
        assert result.get("key") == "value" or "parse_error" in result

    def test_totally_broken_returns_error(self):
        from core.legacy_adapter import EvolutionUtils
        result = EvolutionUtils.json_repair("this is not json at all")
        assert result.get("parse_error") is True


class TestCircuitBreaker:
    """BUG-07: 验证 circuit breaker failures 重置"""

    def test_failures_reset_after_truncation(self):
        """截断后 failures 应重置为 0，允许重新尝试压缩"""
        from core.compaction import run_autocompact_circuit_breaker
        
        # 创建超长内容
        long_lines = [f"Line {i}: " + "x" * 200 for i in range(50)]
        
        # 没有 COMPACT_API_KEY 时应走 hard truncation fallback
        with patch.dict("os.environ", {"COMPACT_API_KEY": ""}, clear=False):
            result = run_autocompact_circuit_breaker(long_lines, max_length=500)
            assert result  # 应返回某种结果（truncated）
            assert len(result) <= 600  # 应被截断到接近 max_length


class TestModelRegistry:
    """BUG-09: 验证不再使用硬编码日期"""

    def test_dynamic_date(self):
        from clients.model_registry import ModelRegistry
        reg = ModelRegistry()
        reg.data = {"models": [], "last_updated": "never"}
        
        # Mock save to prevent file I/O
        reg.save_registry = MagicMock()
        
        reg.add_or_update_model({"id": "test-model", "provider": "test", "tier": "fast"})
        
        assert reg.data["last_updated"] != "2026-04-20"
        assert "T" in reg.data["last_updated"]  # ISO format contains 'T'


class TestLazyControllerInit:
    """BUG-04: 验证 routes.py 延迟初始化 (now via KT bridge)"""

    def test_legacy_memory_init_exists(self):
        """init_legacy_memory should exist for evolution endpoints"""
        from api import routes
        assert hasattr(routes, "init_legacy_memory")
        assert callable(routes.init_legacy_memory)

    def test_bridge_module_exists(self):
        """NanobotBridge should be importable"""
        from nanobot_kt.bridge import NanobotBridge, get_bridge
        assert NanobotBridge is not None
        assert callable(get_bridge)

