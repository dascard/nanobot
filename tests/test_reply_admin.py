import json

import pytest


@pytest.fixture
def auth_header(monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    return {"Authorization": "Bearer test-token"}


def _install_fake_reply_bridge(monkeypatch, reply_text="测试回复", capture=None):
    class FakeBridge:
        async def handle_message(self, message, *, user_id="", session_id="", sender_name="", metadata=None, stream_queue=None):
            from core.tracing import LLMRequestTracer, ReplyContractTracer, RunTracer

            if capture is not None:
                capture.append(dict(metadata or {}))
            trace_id = metadata.get("trace_id") if metadata else ""
            prompt_engine = str((metadata or {}).get("prompt_runtime_engine_override") or "v1")
            prompt_mode = str((metadata or {}).get("prompt_system_mode_override") or "legacy")
            prompt_sha = ("v" if prompt_engine == "v2" else ("m" if prompt_mode == "managed" else "l")) * 64
            run = RunTracer.start_run(
                trace_id=trace_id,
                session_id=session_id,
                user_id=user_id,
                chat_type=metadata.get("chat_type", "group") if metadata else "group",
                group_id=metadata.get("group_id", "") if metadata else "",
                run_type="reply_test",
                prompt_mode=prompt_engine if prompt_engine == "v2" else prompt_mode,
                prompt_key="chat_group" if prompt_engine == "v2" else "group_chat",
                prompt_source="Prompt Runtime V2" if prompt_engine == "v2" else ("PromptManager runtime template" if prompt_mode == "managed" else "Legacy runtime prompt"),
                prompt_sha256=prompt_sha,
                input_preview=message,
            )
            ReplyContractTracer.record_check(
                trace_id=trace_id,
                run_id=run.run_id,
                session_id=session_id,
                attempt=0,
                raw_output="第一次没有工具调用",
                result="no_tool_call",
            )
            if metadata.get("enable_reply_contract_retry", True):
                ReplyContractTracer.record_check(
                    trace_id=trace_id,
                    run_id=run.run_id,
                    session_id=session_id,
                    attempt=1,
                    raw_output=reply_text,
                    has_reply_tool=True,
                    result="retry_success",
                )
            log_id = LLMRequestTracer.record_request(
                trace_id=trace_id,
                run_id=run.run_id,
                source="replyer",
                model="fake-model",
                request={"messages": [{"role": "user", "content": message}]},
            )
            LLMRequestTracer.finish_request(
                log_id=log_id,
                response={"choices": [{"message": {"content": reply_text}}]},
                response_status=200,
                status="success",
                latency_ms=3,
            )
            RunTracer.finish_run(run.run_id, status="success", output_preview=reply_text)
            return reply_text

    monkeypatch.setattr("nanobot_kt.bridge.get_bridge", lambda: FakeBridge())


def test_reply_test_run_returns_attempts_final_and_llm_logs(client, auth_header, monkeypatch):
    _install_fake_reply_bridge(monkeypatch, reply_text="在，怎么了")

    resp = client.post(
        "/api/v1/admin/reply-test/run",
        headers=auth_header,
        json={
            "message": "你在吗",
            "chat_type": "group",
            "session_id": "test-group-1",
            "sender_id": "123",
            "sender_name": "tester",
            "variant": "code_retry",
            "enable_reply_contract_retry": True,
            "dry_run": True,
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["run_id"]
    assert data["first_attempt"]["called_reply"] is False
    assert data["retry_attempt"]["called_reply"] is True
    assert data["final"] == {"action": "reply", "content": "在，怎么了"}
    assert data["metrics"]["retry_used"] is True
    assert data["llm_api_request_logs"]
    assert json.loads(data["llm_api_request_logs"][0]["response_json"])["choices"][0]["message"]["content"] == "在，怎么了"


def test_reply_test_prompt_only_uses_managed_prompt_without_retry(client, auth_header, monkeypatch):
    captured = []
    _install_fake_reply_bridge(monkeypatch, reply_text="可以", capture=captured)

    resp = client.post(
        "/api/v1/admin/reply-test/run",
        headers=auth_header,
        json={
            "message": "你在吗",
            "chat_type": "group",
            "session_id": "test-group-prompt-only",
            "sender_id": "123",
            "sender_name": "tester",
            "variant": "prompt_only",
            "enable_reply_contract_retry": True,
            "dry_run": True,
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert captured[-1]["prompt_system_mode_override"] == "managed"
    assert captured[-1]["enable_reply_contract_retry"] is False
    assert data["prompt_sha256"] == "m" * 64
    assert data["retry_attempt"]["enabled"] is False


def test_reply_test_supports_prompt_engine_v2(client, auth_header, monkeypatch):
    captured = []
    _install_fake_reply_bridge(monkeypatch, reply_text="可以", capture=captured)

    resp = client.post(
        "/api/v1/admin/reply-test/run",
        headers=auth_header,
        json={
            "message": "你在吗",
            "chat_type": "group",
            "session_id": "test-group-v2",
            "sender_id": "123",
            "sender_name": "tester",
            "prompt_engine": "v2",
            "variant": "code_retry",
            "enable_reply_contract_retry": True,
            "dry_run": True,
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert captured[-1]["prompt_runtime_engine_override"] == "v2"
    assert captured[-1]["enable_reply_contract_retry"] is True
    assert data["prompt_engine"] == "v2"
    assert data["prompt_mode"] == "v2"
    assert data["prompt_sha256"] == "v" * 64


def test_group_agent_result_uses_popped_no_reply_meta():
    from api.routes import _derive_group_agent_result

    class FakeBridge:
        def is_no_reply_session(self, _session_id):
            return False

        def is_fake_tool_call_claim(self, _session_id):
            return False

        def is_no_tool_call(self, _session_id):
            return False

    result = _derive_group_agent_result(
        FakeBridge(),
        "group_1",
        {"_no_reply": True, "_agent_result": "no_reply_tool"},
    )

    assert result == "no_reply_tool"


def test_group_agent_result_preserves_prompt_v2_audit_failure():
    from api.routes import _derive_group_agent_result

    result = _derive_group_agent_result(
        object(),
        "group_1",
        {"_agent_result": "prompt_v2_audit_failed"},
    )

    assert result == "prompt_v2_audit_failed"


def test_reply_eval_case_crud_preview_and_run(client, auth_header, monkeypatch):
    _install_fake_reply_bridge(monkeypatch, reply_text="可以")

    create = client.post(
        "/api/v1/admin/reply-eval/cases",
        headers=auth_header,
        json={
            "case_id": "reply_case_manual",
            "title": "直接问候",
            "chat_type": "group",
            "input_text": "你在吗",
            "expected_action": "reply",
            "expected_keywords": ["可以"],
            "tags": ["manual"],
        },
    )
    assert create.status_code == 200, create.text

    update = client.put(
        "/api/v1/admin/reply-eval/cases/reply_case_manual",
        headers=auth_header,
        json={"title": "直接问题", "expected_action": "reply"},
    )
    assert update.status_code == 200, update.text
    assert update.json()["title"] == "直接问题"

    cases = client.get("/api/v1/admin/reply-eval/cases", headers=auth_header)
    assert cases.status_code == 200
    assert cases.json()["items"][0]["case_id"] == "reply_case_manual"

    preview = client.post("/api/v1/admin/reply-eval/generate-preview", headers=auth_header, json={})
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["items"]) >= 40

    save = client.post(
        "/api/v1/admin/reply-eval/save-generated",
        headers=auth_header,
        json={"items": [preview.json()["items"][0]]},
    )
    assert save.status_code == 200, save.text
    assert save.json()["saved"] == 1

    run = client.post(
        "/api/v1/admin/reply-eval/run",
        headers=auth_header,
        json={"variant": "code_retry", "case_ids": ["reply_case_manual"]},
    )
    assert run.status_code == 200, run.text
    run_data = run.json()
    assert run_data["variant"] == "code_retry"
    assert run_data["total"] == 1
    assert run_data["passed"] == 1
    assert run_data["results"][0]["agent_run_id"]
    assert run_data["results"][0]["trace_id"]
    assert run_data["results"][0]["prompt_sha256"] == "l" * 64
    assert run_data["metrics"]["expected_action_accuracy"] == 1.0
    assert run_data["metrics"]["retry_success_rate"] == 1.0

    runs = client.get("/api/v1/admin/reply-eval/runs", headers=auth_header)
    assert runs.status_code == 200
    assert runs.json()["items"][0]["id"] == run_data["id"]

    detail = client.get(f"/api/v1/admin/reply-eval/runs/{run_data['id']}", headers=auth_header)
    assert detail.status_code == 200
    assert detail.json()["results"][0]["case_id"] == "reply_case_manual"


def test_reply_eval_supports_v2_named_variants(client, auth_header, monkeypatch):
    captured = []
    _install_fake_reply_bridge(monkeypatch, reply_text="可以", capture=captured)

    create = client.post(
        "/api/v1/admin/reply-eval/cases",
        headers=auth_header,
        json={
            "case_id": "reply_case_v2",
            "title": "V2 直接问候",
            "chat_type": "group",
            "input_text": "你在吗",
            "expected_action": "reply",
            "expected_keywords": ["可以"],
        },
    )
    assert create.status_code == 200, create.text

    run = client.post(
        "/api/v1/admin/reply-eval/run",
        headers=auth_header,
        json={"variant": "v2_code_retry", "case_ids": ["reply_case_v2"]},
    )

    assert run.status_code == 200, run.text
    data = run.json()
    assert data["variant"] == "v2_code_retry"
    assert data["metrics"]["reply_call_rate"] == 1.0
    assert data["metrics"]["expected_action_accuracy"] == 1.0
    assert data["metrics"]["no_tool_call_rate"] == 1.0
    assert data["metrics"]["fake_tool_claim_rate"] == 0.0
    assert captured[-1]["prompt_runtime_engine_override"] == "v2"
    assert captured[-1]["enable_reply_contract_retry"] is True
