import json
from types import SimpleNamespace

from tests.async_helpers import run_async


def test_sandbox_trace_sanitizers_omit_commands_file_bodies_and_process_output():
    from core.tracing import sanitize_tool_trace_args, sanitize_tool_trace_result

    command_secret = "echo SUPER_SECRET_COMMAND"
    stdout_secret = "SUPER_SECRET_STDOUT"
    args = sanitize_tool_trace_args("sandbox_exec", {
        "command": command_secret,
        "cwd": "/srv/nanobot/private",
        "timeout_seconds": 20,
    })
    result = sanitize_tool_trace_result("sandbox_exec", json.dumps({
        "status": "success",
        "summary": "完成",
        "artifacts": [],
        "data": {
            "run_id": "sbxrun_1",
            "exit_code": 0,
            "termination_reason": "completed",
            "stdout": stdout_secret,
            "stderr": "",
            "stdout_truncated": False,
        },
    }))
    serialized = json.dumps({"args": args, "result": result}, ensure_ascii=False)

    assert command_secret not in serialized
    assert stdout_secret not in serialized
    assert "/srv/nanobot" not in serialized
    assert args["cwd"] == "[INVALID_PATH]"
    assert len(args["command_sha256"]) == 64
    assert len(result["data"]["stdout_sha256"]) == 64


def test_workspace_trace_sanitizers_omit_write_read_and_search_text():
    from core.tracing import sanitize_tool_trace_args, sanitize_tool_trace_result

    write_secret = "WRITE_BODY_SECRET"
    read_secret = "READ_BODY_SECRET"
    match_secret = "MATCH_BODY_SECRET"
    payload = {
        "write_args": sanitize_tool_trace_args("workspace_write", {
            "path": "notes/a.txt",
            "content": write_secret,
            "overwrite": False,
        }),
        "read_result": sanitize_tool_trace_result("workspace_read", {
            "status": "success",
            "summary": "读取完成",
            "artifacts": [],
            "data": {
                "path": "notes/a.txt",
                "offset": 0,
                "returned_bytes": len(read_secret),
                "size_bytes": len(read_secret),
                "eof": True,
                "binary": False,
                "content": read_secret,
            },
        }),
        "search_result": sanitize_tool_trace_result("workspace_search", {
            "status": "success",
            "summary": "搜索完成",
            "artifacts": [],
            "data": {
                "matches": [{"path": "notes/a.txt", "line": 1, "text": match_secret}],
                "scanned_files": 1,
                "truncated": False,
            },
        }),
    }
    serialized = json.dumps(payload, ensure_ascii=False)

    assert write_secret not in serialized
    assert read_secret not in serialized
    assert match_secret not in serialized
    assert payload["write_args"]["content_omitted"] is True
    assert payload["read_result"]["data"]["content_omitted"] is True
    assert payload["search_result"]["data"]["matches"][0]["text_omitted"] is True


def test_asset_publish_trace_omits_short_lived_transport_token():
    from core.tracing import sanitize_tool_trace_result

    token = "SIGNED_ASSET_TRANSPORT_TOKEN_MUST_NOT_PERSIST"
    result = sanitize_tool_trace_result("asset_publish", {
        "status": "success",
        "summary": "发布完成",
        "artifacts": [{
            "type": "asset",
            "ref": f"asset://sha256/{'a' * 64}",
            "logical_name": "results/report.csv",
            "size_bytes": 42,
            "transport_token": token,
            "reply_token": f"[asset_download:{token}]",
        }],
        "data": {
            "ref": f"asset://sha256/{'a' * 64}",
            "logical_name": "results/report.csv",
            "size_bytes": 42,
            "media_type": "text/csv",
            "transport_token": token,
            "reply_token": f"[asset_download:{token}]",
            "recipient_type": "user",
            "recipient_id": "10001",
            "expires_at": 9999999999,
        },
    })

    serialized = json.dumps(result, ensure_ascii=False)
    assert token not in serialized
    assert "transport_token" not in serialized
    assert "reply_token" not in serialized
    assert result["data"]["ref"] == f"asset://sha256/{'a' * 64}"


def test_tool_tracer_persists_only_sandbox_audit_metadata(db_session):
    from core.database import ToolCall
    from core.tracing import ToolTracer

    call_id = ToolTracer.start_tool_call(
        "trace-sandbox",
        "run-sandbox",
        "sandbox_exec",
        {"command": "echo TRACE_COMMAND_SECRET", "cwd": "/srv/nanobot/private"},
    )
    ToolTracer.finish_tool_call(
        call_id,
        result=json.dumps({
            "status": "success",
            "summary": "完成",
            "artifacts": [],
            "data": {
                "run_id": "sbxrun_trace",
                "exit_code": 0,
                "termination_reason": "completed",
                "stdout": "TRACE_STDOUT_SECRET",
                "stderr": "",
            },
        }),
        error="",
    )
    row = db_session.query(ToolCall).filter_by(tool_call_id=call_id).one()

    persisted = row.args_json + row.result_preview + row.error
    assert "TRACE_COMMAND_SECRET" not in persisted
    assert "TRACE_STDOUT_SECRET" not in persisted
    assert "/srv/nanobot" not in persisted
    assert "command_sha256" in row.args_json
    assert "stdout_sha256" in row.result_preview


def test_executor_trace_context_exposes_current_tool_call_only_during_execution(
    db_session,
):
    from core.tool_tracing import install_executor_tracing
    from core.tracing_context import (
        get_tool_trace_context,
        reset_trace_context,
        set_trace_context,
    )

    observed = []

    class Executor:
        async def _run_tool(self, _job_id, _tool, _args, _is_direct=False):
            observed.append(get_tool_trace_context())
            return SimpleNamespace(output="ok", error="", exit_code=0)

    executor = Executor()
    tool = SimpleNamespace(tool_name="workspace_list")
    install_executor_tracing(executor)
    trace_tokens = set_trace_context("trace-context", "run-context")
    try:
        run_async(executor._run_tool("job-1", tool, {}, True))
    finally:
        reset_trace_context(trace_tokens)

    assert len(observed) == 1
    assert observed[0].startswith("tool_")
    assert get_tool_trace_context() == ""
