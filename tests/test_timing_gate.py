"""TimingGate 群聊节奏判断器测试——continue/wait/no_reply/旧格式/非法/超时。"""

import json
from unittest.mock import MagicMock, patch

import pytest


def _mock_qwen_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = json.dumps({
        "choices": [{"message": {"content": content}}],
    }).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _patch_qwen_opener(mock_response: MagicMock):
    mock_opener = MagicMock()
    mock_opener.open.return_value = mock_response
    return patch("urllib.request.build_opener", return_value=mock_opener)


class TestParseOutput:
    def test_continue(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action": "continue"}')
        assert r["action"] == "continue"
        assert r["delay_seconds"] is None
        assert r["error_type"] is None

    def test_wait_with_delay(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action":"wait","delay_seconds":10,"reason":"用户可能在继续说话"}')
        assert r["action"] == "wait"
        assert r["delay_seconds"] == 10
        assert "继续说话" in r["reason"]

    def test_no_reply(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action":"no_reply","reason":"用户间对话"}')
        assert r["action"] == "no_reply"
        assert r["error_type"] is None

    def test_delay_clamp_too_low(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action":"wait","delay_seconds":1}')
        assert r["delay_seconds"] == 3

    def test_delay_clamp_too_high(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action":"wait","delay_seconds":99}')
        assert r["delay_seconds"] == 30

    def test_old_format_yes_is_continue(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output("是,7")
        assert r["action"] == "continue"
        assert r["reason"] == "旧格式兼容"

    def test_old_format_no_is_no_reply(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output("否,0")
        assert r["action"] == "no_reply"

    def test_invalid_output_is_no_reply(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output("hello world")
        assert r["action"] == "no_reply"
        assert r["error_type"] == "parse_error"

    def test_think_block_stripped(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output("<think>\n\n</think>\n\n{\"action\":\"continue\"}")
        assert r["action"] == "continue"

    def test_continue_ignores_delay(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        r = g._parse_output('{"action":"continue","delay_seconds":5}')
        assert r["action"] == "continue"
        assert r["delay_seconds"] is None


class TestJudge:
    def test_continue_scenario(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        mock = _mock_qwen_response('{"action":"continue"}')
        with _patch_qwen_opener(mock):
            r = g.judge("bot别名: nanobot\n[雀]: nanobot怎么回事")
        assert r["action"] == "continue"

    def test_no_reply_scenario(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        mock = _mock_qwen_response('{"action":"no_reply","reason":"用户间对话"}')
        with _patch_qwen_opener(mock):
            r = g.judge("[用户A]: 今天天气真好\n[用户B]: 是啊")
        assert r["action"] == "no_reply"

    def test_qwen_unavailable_falls_back(self):
        from clients.classifier_client import TimingGate
        g = TimingGate()
        with patch("urllib.request.build_opener", side_effect=Exception("connection refused")):
            r = g.judge("hello")
        assert r["action"] == "no_reply"
        assert r["error_type"] == "network_error"
