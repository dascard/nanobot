"""任意 Python 执行硬禁用的安全回归测试。"""

from __future__ import annotations

import hashlib
import shlex
import sqlite3

import pytest

from tests.async_helpers import run_async


_DISABLED_HINTS = ("disabled", "禁用", "不可用")


def _assert_disabled_message(message: object) -> None:
    text = str(message or "").strip()
    assert text, "硬禁用必须返回稳定且非空的错误"
    assert any(hint in text.lower() for hint in _DISABLED_HINTS), text


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _RecordingSandbox:
    def __init__(self) -> None:
        self.calls = 0

    def execute_python_analysis(self, code: str) -> str:
        self.calls += 1
        return f"UNSAFE_EXECUTED:{code}"


def test_execute_python_analysis_is_fail_closed(tmp_path):
    from sandbox import AnalysisSandbox

    db_path = tmp_path / "analysis.sqlite3"
    sqlite3.connect(db_path).close()
    sandbox = AnalysisSandbox(db_path=str(db_path))

    first = sandbox.execute_python_analysis("print('UNSAFE_PRINT_EXECUTED')")
    second = sandbox.execute_python_analysis("print('A_DIFFERENT_PROGRAM')")

    assert first == second, "不同用户代码必须得到同一个稳定禁用错误"
    assert "UNSAFE_PRINT_EXECUTED" not in first
    assert "A_DIFFERENT_PROGRAM" not in second
    _assert_disabled_message(first)


def test_python_sandbox_cannot_reopen_database_for_write(tmp_path):
    from sandbox import AnalysisSandbox

    db_path = tmp_path / "guarded.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sandbox_guard (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sandbox_guard (value) VALUES ('original')")

    digest_before = _sha256(db_path)
    sandbox = AnalysisSandbox(db_path=str(db_path))
    result = sandbox.execute_python_analysis(
        "\n".join(
            [
                "path = conn.execute('PRAGMA database_list').fetchone()[2]",
                "writer = sqlite3.connect(path)",
                "writer.execute(\"UPDATE sandbox_guard SET value = 'changed'\")",
                "writer.commit()",
                "writer.close()",
                "print('UNSAFE_SQLITE_WRITE_EXECUTED')",
            ]
        )
    )

    with sqlite3.connect(db_path) as conn:
        value = conn.execute("SELECT value FROM sandbox_guard").fetchone()[0]

    assert value == "original"
    assert _sha256(db_path) == digest_before
    assert "UNSAFE_SQLITE_WRITE_EXECUTED" not in result
    _assert_disabled_message(result)


def test_python_sandbox_cannot_reach_popen_via_object_graph(tmp_path):
    from sandbox import AnalysisSandbox

    db_path = tmp_path / "analysis.sqlite3"
    sqlite3.connect(db_path).close()
    marker_path = tmp_path / "popen-escaped.marker"
    shell_command = f"printf escaped > {shlex.quote(str(marker_path))}"
    attack = "\n".join(
        [
            "for candidate in ().__class__.__bases__[0].__subclasses__():",
            "    if getattr(candidate, '__name__', '') == 'Popen':",
            f"        process = candidate(['/bin/sh', '-c', {shell_command!r}])",
            "        process.wait()",
            "        print('UNSAFE_POPEN_EXECUTED')",
        ]
    )

    result = AnalysisSandbox(db_path=str(db_path)).execute_python_analysis(attack)

    assert not marker_path.exists()
    assert "UNSAFE_POPEN_EXECUTED" not in result
    _assert_disabled_message(result)


def test_python_sandbox_tool_returns_disabled_without_executing():
    from nanobot_kt.tools.python_sandbox import PythonSandboxTool

    recording_sandbox = _RecordingSandbox()
    tool = PythonSandboxTool()
    tool._sandbox = recording_sandbox

    result = run_async(tool.execute({"code": "print('UNSAFE_TOOL_EXECUTED')"}))

    assert recording_sandbox.calls == 0
    assert not result.success
    _assert_disabled_message(result.error or result.get_text_output())


@pytest.mark.parametrize("chat_type", ["private", "group", "private_superuser"])
@pytest.mark.parametrize("runtime_preset", ["full", "lightweight"])
def test_python_sandbox_is_hard_disabled_after_database_override(
    db_session,
    chat_type,
    runtime_preset,
):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    user_id = f"python-sandbox-{chat_type}-{runtime_preset}"
    db_session.add(
        ToolOverride(
            tool_name="python_sandbox",
            scope_type="user",
            scope_id=user_id,
            enabled=1,
            reason="尝试越过 Python 硬禁用",
        )
    )
    db_session.commit()

    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id="sandbox-test-group" if chat_type == "group" else "",
        user_id=user_id,
        platform="test",
        runtime_preset=runtime_preset,
        db=db_session,
    )

    assert enabled["python_sandbox"] is False
    assert "python_sandbox" in disabled


@pytest.mark.parametrize("chat_type", ["private", "group", "private_superuser"])
@pytest.mark.parametrize("runtime_preset", ["full", "lightweight"])
def test_python_sandbox_is_absent_from_wire_tool_plan_after_override(
    db_session,
    chat_type,
    runtime_preset,
):
    from core.database import ToolOverride
    from core.tool_plan import build_tool_plan

    user_id = f"python-wire-{chat_type}-{runtime_preset}"
    db_session.add(
        ToolOverride(
            tool_name="python_sandbox",
            scope_type="user",
            scope_id=user_id,
            enabled=1,
            reason="尝试把 Python 放回 wire schema",
        )
    )
    db_session.commit()

    plan = build_tool_plan(
        chat_type=chat_type,
        group_id="sandbox-test-group" if chat_type == "group" else "",
        user_id=user_id,
        platform="test",
        runtime_preset=runtime_preset,
        db=db_session,
    )
    schema_names = {
        schema["function"]["name"]
        for schema in plan.sent_tool_schemas
    }

    assert "python_sandbox" not in plan.sent_tool_names
    assert "python_sandbox" not in schema_names


def test_legacy_run_python_analysis_is_disabled_without_executing(monkeypatch):
    from core.legacy_adapter import NanobotKTController

    recording_sandbox = _RecordingSandbox()
    controller = object.__new__(NanobotKTController)
    controller.sandbox = recording_sandbox
    monkeypatch.setattr(
        "core.tool_tracing.begin_tool_trace",
        lambda *_args, **_kwargs: ("", 0.0),
    )

    result = controller._execute_native_tool(
        "run_python_analysis",
        {"code": "print('UNSAFE_LEGACY_EXECUTED')"},
    )

    assert recording_sandbox.calls == 0
    assert "UNSAFE_LEGACY_EXECUTED" not in result
    _assert_disabled_message(result)
