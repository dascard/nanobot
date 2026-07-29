import json
from types import SimpleNamespace

from tests.async_helpers import run_async


def test_metadata_only_trace_policy_comes_from_tool_descriptor_registry(monkeypatch):
    import core.tracing as tracing
    from core.tool_registry import list_tool_descriptors

    metadata_only_names = {
        descriptor.name
        for descriptor in list_tool_descriptors()
        if descriptor.trace_policy == "metadata_only"
    }
    assert metadata_only_names
    for tool_name in metadata_only_names:
        assert tracing._uses_metadata_only_trace(tool_name) is True

    future_name = "future_metadata_only_tool"
    original_get_descriptor = tracing.get_tool_descriptor

    def _get_descriptor(name: str):
        if name == future_name:
            return SimpleNamespace(trace_policy="metadata_only")
        return original_get_descriptor(name)

    monkeypatch.setattr(tracing, "get_tool_descriptor", _get_descriptor)
    sanitized = tracing.sanitize_tool_trace_args(
        future_name,
        {"secret_payload": "MUST_NOT_ENTER_TRACE"},
    )

    assert sanitized == {"args_omitted": True}
    assert not hasattr(tracing, "SANDBOX_TRACE_TOOL_NAMES")


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


def test_process_and_patch_trace_sanitizers_keep_only_audit_metadata():
    from core.tracing import sanitize_tool_trace_args, sanitize_tool_trace_result

    stdin_secret = "STDIN_SECRET_MUST_NOT_PERSIST"
    patch_secret = "PATCH_SECRET_MUST_NOT_PERSIST"
    output_secret = "PROCESS_OUTPUT_MUST_NOT_PERSIST"
    payload = {
        "stdin_args": sanitize_tool_trace_args(
            "sandbox_write_stdin",
            {
                "process_id": "sbxrun_trace",
                "chars": stdin_secret,
            },
        ),
        "patch_args": sanitize_tool_trace_args(
            "workspace_apply_patch",
            {
                "path": "src/example.py",
                "patch": f"@@ -1 +1 @@\n-old\n+{patch_secret}\n",
            },
        ),
        "poll_result": sanitize_tool_trace_result(
            "sandbox_poll",
            {
                "status": "success",
                "summary": "轮询完成",
                "artifacts": [],
                "data": {
                    "process_id": "sbxrun_trace",
                    "execution_status": "running",
                    "process_state": "running",
                    "next_cursor": "v1:10:0",
                    "stdout_delta": output_secret,
                    "stderr_delta": "",
                    "active_processes": [{
                        "process_id": "sbxrun_trace",
                        "state": "running",
                        "command": "SECRET_COMMAND",
                    }],
                },
            },
        ),
    }
    serialized = json.dumps(payload, ensure_ascii=False)

    assert stdin_secret not in serialized
    assert patch_secret not in serialized
    assert output_secret not in serialized
    assert "SECRET_COMMAND" not in serialized
    assert payload["stdin_args"]["chars_omitted"] is True
    assert len(payload["stdin_args"]["chars_sha256"]) == 64
    assert payload["patch_args"]["patch_omitted"] is True
    assert len(payload["patch_args"]["patch_sha256"]) == 64
    assert payload["poll_result"]["data"]["stdout_delta_omitted"] is True
    assert len(
        payload["poll_result"]["data"]["stdout_delta_sha256"]
    ) == 64


def test_workspace_edit_trace_omits_exact_text_and_diff_bodies():
    from core.tracing import sanitize_tool_trace_args, sanitize_tool_trace_result

    old_secret = "OLD_EDIT_SECRET"
    new_secret = "NEW_EDIT_SECRET"
    diff_secret = "DIFF_EDIT_SECRET"
    args = sanitize_tool_trace_args(
        "workspace_edit",
        {
            "cwd": "project",
            "operations": [
                {
                    "path": "src/a.py",
                    "old": old_secret,
                    "new": new_secret,
                    "replace_all": False,
                },
                {
                    "diff": (
                        "diff --git a/b.py b/b.py\n"
                        "--- a/b.py\n"
                        "+++ b/b.py\n"
                        "@@ -1 +1 @@\n"
                        "-old\n"
                        f"+{diff_secret}\n"
                    ),
                },
            ],
        },
    )
    result = sanitize_tool_trace_result(
        "workspace_edit",
        {
            "status": "success",
            "summary": "编辑完成",
            "artifacts": [],
            "data": {
                "protocol_version": 2,
                "file_count": 1,
                "recovery_status": "not_needed",
                "files": [{
                    "path": "project/src/a.py",
                    "size_bytes": 12,
                    "replacement_count": 1,
                    "old_sha256": "a" * 64,
                    "new_sha256": "b" * 64,
                }],
            },
        },
    )
    serialized = json.dumps(
        {"args": args, "result": result},
        ensure_ascii=False,
    )

    assert old_secret not in serialized
    assert new_secret not in serialized
    assert diff_secret not in serialized
    assert args["cwd"] == "project"
    assert args["operations"][0]["path"] == "src/a.py"
    assert args["operations"][0]["old_omitted"] is True
    assert args["operations"][1]["diff_omitted"] is True
    assert len(args["operations"][1]["diff_sha256"]) == 64
    assert result["data"]["files"][0]["path"] == "project/src/a.py"


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


def test_executor_marks_structured_business_error_as_failed(
    db_session,
    monkeypatch,
):
    from core.database import ToolCall
    from core.tool_tracing import install_executor_tracing
    from core.tracing_context import (
        reset_trace_context,
        set_trace_context,
    )

    payload = {
        "status": "error",
        "summary": "当前会话没有显式 Sandbox 授权",
        "next_actions": [],
        "artifacts": [],
        "error": {
            "code": "authorization_failed",
            "retryable": False,
            "hint": "",
            "stop": True,
        },
    }
    events = []
    monkeypatch.setattr(
        "core.runtime.event_bus.emit_runtime_event",
        lambda name, phase, **kwargs: events.append((name, phase, kwargs)),
    )

    class Executor:
        async def _run_tool(self, _job_id, _tool, _args, _is_direct=False):
            return SimpleNamespace(
                output=json.dumps(payload, ensure_ascii=False),
                error="",
                exit_code=0,
                metadata={"structured_content": payload},
            )

    executor = Executor()
    tool = SimpleNamespace(tool_name="sandbox_exec")
    install_executor_tracing(executor)
    trace_tokens = set_trace_context("trace-structured", "run-structured")
    try:
        result = run_async(
            executor._run_tool(
                "job-structured",
                tool,
                {"command": "pwd"},
                True,
            )
        )
    finally:
        reset_trace_context(trace_tokens)

    assert result.exit_code == 0
    row = (
        db_session.query(ToolCall)
        .filter_by(trace_id="trace-structured", run_id="run-structured")
        .one()
    )
    assert row.status == "error"
    assert "error_omitted" in row.error
    failed = [
        kwargs["attributes"]
        for name, phase, kwargs in events
        if name == "tool.execute" and phase == "failed"
    ]
    assert len(failed) == 1
    assert failed[0]["tool_name"] == "sandbox_exec"
    assert failed[0]["failure_code"] == "authorization_failed"
    assert failed[0]["error_type"] == "structured_tool_error"
    assert failed[0]["retryable"] is False
    assert failed[0]["stop"] is True
    assert failed[0]["result_truncated"] is False
