"""工具应用服务与 KT Adapter 的依赖边界回归。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_tool_service_result_freezes_metadata() -> None:
    from core.tool_contracts.result import ToolServiceResult

    result = ToolServiceResult(
        output="ok",
        exit_code=0,
        metadata={"source": "test"},
    )

    assert result.success is True
    with pytest.raises(TypeError):
        result.metadata["source"] = "changed"  # type: ignore[index]


def test_sql_service_rejects_input_before_opening_sandbox() -> None:
    from app.tool_services.sql_analysis import execute_sql_analysis

    def fail_if_opened():
        raise AssertionError("非法 SQL 不得创建 Sandbox")

    empty = execute_sql_analysis("", sandbox_factory=fail_if_opened)
    unsafe = execute_sql_analysis(
        "DELETE FROM chat_logs",
        sandbox_factory=fail_if_opened,
    )

    assert empty.error == "Missing 'sql' argument"
    assert unsafe.error and unsafe.error.startswith(
        "Invalid SQL for sql_analysis:"
    )


def test_reply_wire_contract_is_framework_independent() -> None:
    from core.tool_contracts.reply import (
        REPLY_MARKER,
        build_reply_payload,
    )

    payload = build_reply_payload(
        "  你好  ",
        mentions=[" 123 ", "bad", "456"],
        send_mode="unsupported",
    )[REPLY_MARKER]

    assert payload == {
        "content": "你好",
        "reply_to_message_id": None,
        "mentions": ["123", "456"],
        "quote": False,
        "at_sender": False,
        "send_mode": "normal",
    }


def test_expired_creature_tool_aliases_are_deleted() -> None:
    skills = Path("creatures/nanobot/prompts/skills")
    aliases = (
        "image_generation/tool.py",
        "image_summary/tool.py",
        "knowledge_query/tool.py",
        "memory_query/tool.py",
        "news_search/tool.py",
        "persona_update/tool.py",
        "python_sandbox/tool.py",
        "reply/tool.py",
        "sandbox/tool.py",
        "schedule_task/tool.py",
        "sql_analysis/tool.py",
        "sticker_search/tool.py",
        "web_search/tool.py",
    )

    assert all(not (skills / relative).exists() for relative in aliases)


def test_tool_application_services_do_not_import_kt() -> None:
    from pathlib import Path

    from scripts.check_architecture import check_kt_framework_boundaries

    paths = tuple(sorted(Path("app/tool_services").glob("*.py"))) + (
        Path("core/tool_contracts/reply.py"),
        Path("core/tool_contracts/result.py"),
    )

    assert check_kt_framework_boundaries(paths) == []


def test_creature_tool_paths_are_framework_independent() -> None:
    from scripts.check_architecture import check_creature_tool_boundaries

    assert check_creature_tool_boundaries() == []


def test_creature_tool_boundary_rejects_framework_implementation(
    tmp_path,
) -> None:
    from scripts.check_architecture import check_creature_tool_boundaries

    direct = tmp_path / "tool.py"
    direct.write_text(
        "from kohakuterrarium.tool import BaseTool\n"
        "class DirectTool(BaseTool):\n"
        "    pass\n",
        encoding="utf-8",
    )
    adapter = tmp_path / "adapter_tool.py"
    adapter.write_text(
        "from nanobot_kt.tools import reply as _adapter\n",
        encoding="utf-8",
    )

    errors = check_creature_tool_boundaries((direct, adapter))

    assert any("kohakuterrarium.tool" in error for error in errors)
    assert any("nanobot_kt.tools" in error for error in errors)
