import dataclasses
import hashlib
import inspect
import json
import shutil
from pathlib import Path

import pytest


def _metrics_tool(
    name: str,
    *,
    description: str = "测试工具",
    parameters: dict | None = None,
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
        },
    }


def test_prompt_request_metrics_cover_wire_messages_and_tools():
    from core.prompt_v2.request_metrics import calculate_request_metrics
    from core.prompt_v2.section_renderer import sha256_text, stable_json

    messages = [
        {"role": "system", "content": "系统规则"},
        {"role": "user", "content": "当前问题"},
    ]
    tools = [
        _metrics_tool("reply", description="发送最终回复"),
        _metrics_tool(
            "search",
            description="检索资料" * 40,
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ]

    metrics = calculate_request_metrics(messages=messages, tools=tools)

    assert metrics.message_token_estimate > 0
    assert metrics.tool_schema_token_estimate > 0
    assert metrics.token_estimate == (
        metrics.message_token_estimate + metrics.tool_schema_token_estimate
    )
    assert metrics.prompt_sha256 == sha256_text(stable_json({
        "messages": messages,
        "tools": tools,
    }))

    changed_tools = [
        tools[0],
        _metrics_tool(
            "search",
            description="扩展后的检索资料说明" * 100,
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
    ]
    changed = calculate_request_metrics(messages=messages, tools=changed_tools)
    assert changed.message_token_estimate == metrics.message_token_estimate
    assert changed.tool_schema_token_estimate != metrics.tool_schema_token_estimate
    assert changed.prompt_sha256 != metrics.prompt_sha256

    empty = calculate_request_metrics(messages=messages, tools=[])
    assert empty.tool_schema_token_estimate == 0
    assert empty.token_estimate == empty.message_token_estimate

    tuple_metrics = calculate_request_metrics(  # type: ignore[arg-type]
        messages=tuple(messages),
        tools=tuple(tools),
    )
    assert tuple_metrics == metrics


def test_prompt_request_metrics_ignore_non_wire_management_metadata():
    from core.prompt_v2.request_metrics import calculate_request_metrics
    from core.tool_plan import normalize_wire_tool_schema

    base = _metrics_tool("reply", description="发送回复")
    with_metadata_a = {
        **base,
        "category": "output",
        "risk_level": "low",
        "label": "回复工具",
    }
    with_metadata_b = {
        **base,
        "category": "other",
        "risk_level": "high",
        "label": "已变更管理标签",
    }
    wire_a = normalize_wire_tool_schema(with_metadata_a)
    wire_b = normalize_wire_tool_schema(with_metadata_b)

    assert wire_a == wire_b
    assert calculate_request_metrics(
        messages=[{"role": "user", "content": "你好"}],
        tools=[wire_a],
    ) == calculate_request_metrics(
        messages=[{"role": "user", "content": "你好"}],
        tools=[wire_b],
    )


def test_prompt_template_resolution_uses_real_default_path_and_raw_file_hash(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_loader import load_template

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_path = default_dir / "chat" / "main.md"
    default_path.parent.mkdir(parents=True)
    raw_bytes = "---\r\nname: 默认主模板\r\nversion: 7\r\n---\r\n同一正文\r\n".encode(
        "utf-8"
    )
    default_path.write_bytes(raw_bytes)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    template = load_template("chat/main")
    resolution = template.resolution

    assert resolution.template_key == "chat/main"
    assert resolution.active_source == "default"
    assert resolution.active_path == str(default_path)
    assert resolution.runtime_path is None
    assert resolution.default_path == str(default_path)
    assert resolution.active_sha256 == hashlib.sha256(raw_bytes).hexdigest()
    assert template.raw.encode("utf-8") == raw_bytes
    assert resolution.runtime_sha256 is None
    assert resolution.default_sha256 == resolution.active_sha256


def test_prompt_template_resolution_hashes_both_runtime_and_default_raw_bytes(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.template_loader import load_template

    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    default_path = default_dir / "chat" / "main.md"
    runtime_path = runtime_dir / "chat" / "main.md"
    default_path.parent.mkdir(parents=True)
    runtime_path.parent.mkdir(parents=True)
    default_bytes = b"---\r\nversion: 1\r\n---\r\ndefault\r\n"
    runtime_bytes = b"---\r\nversion: 2\r\n---\r\nruntime\r\n"
    default_path.write_bytes(default_bytes)
    runtime_path.write_bytes(runtime_bytes)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    resolution = load_template("chat/main").resolution

    assert resolution is not None
    assert resolution.active_source == "runtime"
    assert resolution.active_sha256 == hashlib.sha256(runtime_bytes).hexdigest()
    assert resolution.runtime_sha256 == hashlib.sha256(runtime_bytes).hexdigest()
    assert resolution.default_sha256 == hashlib.sha256(default_bytes).hexdigest()


def test_prompt_template_resolution_explicit_directory_is_built_in(tmp_path):
    from core.prompt_v2.template_loader import load_template
    from core.prompt_v2.template_resolution import build_template_trace_fields

    template_path = tmp_path / "chat" / "main.md"
    template_path.parent.mkdir(parents=True)
    raw_bytes = b"---\r\nversion: 1\r\n---\r\nbuilt in\r\n"
    template_path.write_bytes(raw_bytes)

    template = load_template("chat/main", template_dir=tmp_path)
    resolution = template.resolution

    assert resolution is not None
    assert resolution.active_source == "built_in"
    assert resolution.active_path == str(template_path)
    assert resolution.runtime_path is None
    assert resolution.default_path is None
    assert resolution.active_sha256 == hashlib.sha256(raw_bytes).hexdigest()
    trace_fields = build_template_trace_fields({"base_contract": resolution})
    assert trace_fields == {
        "prompt_source": "built_in",
        "prompt_runtime_path": "",
        "prompt_default_path": "",
    }


@pytest.mark.asyncio
async def test_prompt_template_resolution_reports_mixed_sources_by_flow_node(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.template_resolution import build_template_trace_fields

    repo_root = Path(__file__).resolve().parents[1]
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    shutil.copytree(repo_root / "prompts.v2.default", default_dir)
    shutil.copytree(repo_root / "data" / "prompts_v2", runtime_dir)
    (runtime_dir / "chat" / "branch_private.md").unlink()
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="web",
            session_id="private-template-resolution",
            user_id="template-resolution-user",
            user_input="检查模板来源",
            runtime_tool_prompt="[RuntimeTool]",
            debug={
                "template_path": "/forged/active.md",
                "template_paths": {"base_contract": "/forged/base.md"},
                "template_resolutions": {
                    "base_contract": {"active_source": "built_in"}
                },
                "request_prompt_sha256": "forged",
                "flow_path": "/forged/flow.json",
            },
        )
    )

    resolutions = plan.debug["template_resolutions"]
    trace_fields = build_template_trace_fields(resolutions)
    assert resolutions["base_contract"]["active_source"] == "runtime"
    assert resolutions["private_policy"]["active_source"] == "default"
    assert trace_fields["prompt_source"] == "mixed"
    assert trace_fields["prompt_runtime_path"] == resolutions["base_contract"]["runtime_path"]
    assert trace_fields["prompt_default_path"] == resolutions["base_contract"]["default_path"]
    assert plan.prompt_sha256 != resolutions["base_contract"]["active_sha256"]
    assert plan.debug["template_path"] != "/forged/active.md"
    assert plan.debug["flow_path"] != "/forged/flow.json"
    assert plan.debug["request_prompt_sha256"] == plan.prompt_sha256
    assert all(
        plan.debug["template_paths"][node_id] == resolution["active_path"]
        for node_id, resolution in resolutions.items()
    )
    serialized = json.dumps(resolutions, ensure_ascii=False)
    assert "同一正文" not in serialized
    assert all(
        not ({"body", "raw", "content"} & set(item))
        for item in resolutions.values()
    )


@pytest.mark.asyncio
async def test_prompt_template_resolution_metadata_does_not_change_wire_request_hash(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.template_resolution import build_template_trace_fields

    repo_root = Path(__file__).resolve().parents[1]
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    shutil.copytree(repo_root / "prompts.v2.default", default_dir)
    shutil.copytree(default_dir, runtime_dir)
    monkeypatch.setenv("NANOBOT_PROMPT_DEFAULT_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_RUNTIME_DIR", str(runtime_dir))
    original_build_template_values = compiler.build_template_values
    monkeypatch.setattr(
        compiler,
        "build_template_values",
        lambda request: original_build_template_values(
            request,
            current_time="2026-07-14 12:00:00 CST",
        ),
    )
    request = PromptCompileRequest(
        chat_type="private",
        platform="web",
        session_id="private-resolution-hash",
        user_id="resolution-hash-user",
        user_input="请求哈希不能包含模板来源元数据",
        runtime_tool_prompt="[RuntimeTool]",
    )

    runtime_plan = await compiler.compile_prompt_plan(request)
    for runtime_template in runtime_dir.rglob("*.md"):
        runtime_template.unlink()
    default_plan = await compiler.compile_prompt_plan(request)

    assert build_template_trace_fields(runtime_plan.template_resolutions)[
        "prompt_source"
    ] == "runtime"
    assert build_template_trace_fields(default_plan.template_resolutions)[
        "prompt_source"
    ] == "default"
    assert runtime_plan.template_resolutions != default_plan.template_resolutions
    assert runtime_plan.request_json == default_plan.request_json
    assert runtime_plan.prompt_sha256 == default_plan.prompt_sha256


def test_template_resolution_trace_serializer_drops_non_contract_fields():
    from core.prompt_v2.template_resolution import serialize_template_resolutions_json

    sentinel = "TEMPLATE_BODY_MUST_NOT_BE_PERSISTED"
    serialized = serialize_template_resolutions_json({
        "base_contract": {
            "template_key": "chat/main",
            "active_source": "runtime",
            "active_path": "/runtime/chat/main.md",
            "runtime_path": "/runtime/chat/main.md",
            "default_path": None,
            "active_sha256": "a" * 64,
            "runtime_sha256": "a" * 64,
            "default_sha256": None,
            "baseline_version": None,
            "drift_status": "untracked_legacy",
            "body": sentinel,
            "raw": sentinel,
            "content": sentinel,
        }
    })

    assert sentinel not in serialized
    assert set(json.loads(serialized)["base_contract"]) == {
        "template_key",
        "active_source",
        "active_path",
        "runtime_path",
        "default_path",
        "active_sha256",
        "runtime_sha256",
        "default_sha256",
        "baseline_version",
        "drift_status",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("template_key", ""),
        ("active_sha256", "short"),
        ("runtime_sha256", "not-a-sha"),
        ("drift_status", "unknown"),
    ],
)
def test_template_resolution_trace_serializer_rejects_invalid_contract_fields(
    field,
    value,
):
    from core.prompt_v2.template_resolution import serialize_template_resolutions_json

    resolution = {
        "template_key": "chat/main",
        "active_source": "runtime",
        "active_path": "/runtime/chat/main.md",
        "runtime_path": "/runtime/chat/main.md",
        "default_path": "/default/chat/main.md",
        "active_sha256": "a" * 64,
        "runtime_sha256": "a" * 64,
        "default_sha256": "b" * 64,
        "baseline_version": "1",
        "drift_status": "local_override",
    }
    resolution[field] = value

    with pytest.raises(ValueError):
        serialize_template_resolutions_json({"base_contract": resolution})


def test_prompt_v2_flow_selects_single_conditional_path_by_edge_condition():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "private_path", "type": "template", "template_key": "chat/custom_private"},
            {"id": "group_path", "type": "template", "template_key": "chat/custom_group"},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "private_path", "chat_types": ["private"]},
            {"from": "base", "to": "group_path", "chat_types": ["group"]},
            {"from": "private_path", "to": "tail", "chat_types": ["private"]},
            {"from": "group_path", "to": "tail", "chat_types": ["group"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "base",
        "private_path",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group")] == [
        "base",
        "group_path",
        "tail",
    ]


def test_prompt_v2_flow_rejects_ambiguous_outgoing_branch_condition():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "a", "type": "template", "template_key": "chat/custom_a"},
            {"id": "b", "type": "template", "template_key": "chat/custom_b"},
        ],
        "edges": [
            {"from": "base", "to": "a", "chat_types": ["private"]},
            {"from": "base", "to": "b", "chat_types": ["private"]},
        ],
    }

    with pytest.raises(PromptFlowError, match="同一条件只能有一条出边"):
        validate_flow(flow)


def test_prompt_v2_flow_orders_multiple_entry_nodes_by_topology():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "identity", "type": "template", "template_key": "chat/custom_identity"},
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "identity",
        "base",
        "tail",
    ]


def test_prompt_v2_flow_ignores_nodes_only_used_by_inactive_conditions():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "conditional_group", "type": "template", "template_key": "chat/custom_group"},
            {"id": "runtime_stage", "type": "template", "template_key": "chat/custom_runtime"},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "conditional_group", "chat_types": ["group"]},
            {"from": "conditional_group", "to": "runtime_stage", "chat_types": ["private"]},
            {"from": "runtime_stage", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "conditional_group",
        "runtime_stage",
        "tail",
    ]


def test_prompt_v2_flow_entry_is_derived_from_in_degree_not_node_order():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "identity", "type": "template", "template_key": "chat/custom_identity"},
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "identity"},
            {"from": "identity", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "base",
        "identity",
        "tail",
    ]


def test_prompt_v2_flow_filters_by_chat_type_and_platform():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "qq_common", "type": "template", "template_key": "chat/custom_qq_common", "platforms": ["qq"]},
            {"id": "group_path", "type": "template", "template_key": "chat/custom_group", "chat_types": ["group"]},
            {
                "id": "qq_group",
                "type": "template",
                "template_key": "chat/custom_qq_group",
                "chat_types": ["group"],
                "platforms": ["qq"],
            },
            {"id": "private_path", "type": "template", "template_key": "chat/custom_private", "chat_types": ["private"]},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "qq_common", "platforms": ["qq"]},
            {"from": "base", "to": "group_path", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "group_path", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_path", "to": "qq_group", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "qq_group", "to": "tail", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_path", "to": "tail", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "private_path", "chat_types": ["private"], "platforms": ["qq"]},
            {"from": "base", "to": "private_path", "chat_types": ["private"], "platforms": ["web"]},
            {"from": "private_path", "to": "tail", "chat_types": ["private"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="qq")] == [
        "base",
        "qq_common",
        "group_path",
        "qq_group",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="qq")] == [
        "base",
        "qq_common",
        "private_path",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="web")] == [
        "base",
        "private_path",
        "tail",
    ]


def test_prompt_v2_flow_allows_disjoint_platform_branches_for_same_chat_type():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "qq", "type": "template", "template_key": "chat/custom_qq"},
            {"id": "web", "type": "template", "template_key": "chat/custom_web"},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [
            {"from": "base", "to": "qq", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "base", "to": "web", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq", "to": "tail", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "web", "to": "tail", "chat_types": ["group"], "platforms": ["web"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="qq")] == [
        "base",
        "qq",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="web")] == [
        "base",
        "web",
        "tail",
    ]


def test_prompt_v2_flow_rejects_overlapping_platform_branches():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base"},
            {"id": "group_path", "type": "template", "template_key": "chat/custom_group"},
            {"id": "qq", "type": "template", "template_key": "chat/custom_qq"},
        ],
        "edges": [
            {"from": "base", "to": "group_path", "chat_types": ["group"]},
            {"from": "base", "to": "qq", "chat_types": ["group"], "platforms": ["qq"]},
        ],
    }

    with pytest.raises(PromptFlowError, match="同一条件只能有一条出边"):
        validate_flow(flow)


def test_prompt_v2_flow_rejects_invalid_platform_values():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/custom_base", "platforms": ["QQ!"]},
            {"id": "tail", "type": "template", "template_key": "chat/custom_tail"},
        ],
        "edges": [{"from": "base", "to": "tail"}],
    }

    with pytest.raises(PromptFlowError, match="platforms 不支持"):
        validate_flow(flow)


def _mutate_reserved_flow_node(flow, node_id, invalid_fields):
    node = next(item for item in flow["nodes"] if item["id"] == node_id)
    replacement_id = invalid_fields.get("id", node_id)
    node.update(invalid_fields)
    if replacement_id == node_id:
        return
    for edge in flow["edges"]:
        if edge["from"] == node_id:
            edge["from"] = replacement_id
        if edge["to"] == node_id:
            edge["to"] = replacement_id


@pytest.mark.parametrize(
    ("node_id", "invalid_fields"),
    [
        ("persona_reference", {"id": "renamed_persona_reference"}),
        (
            "runtime_tool_prompt",
            {"type": "template", "template_key": "chat/main"},
        ),
        ("current_user_event", {"runtime_key": "runtime_context"}),
        ("private_policy", {"id": "renamed_private_policy"}),
        (
            "group_policy",
            {"type": "runtime", "runtime_key": "runtime_context"},
        ),
        ("group_policy", {"template_key": "chat/main"}),
    ],
    ids=(
        "persona-node-id",
        "runtime-tool-node-type",
        "current-user-runtime-key",
        "private-policy-node-id",
        "group-policy-node-type",
        "group-policy-template-key",
    ),
)
def test_prompt_v2_flow_rejects_reserved_section_identity(node_id, invalid_fields):
    import copy

    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError, validate_flow

    flow = copy.deepcopy(DEFAULT_FLOW)
    _mutate_reserved_flow_node(flow, node_id, invalid_fields)

    with pytest.raises(PromptFlowError):
        validate_flow(flow)


def test_prompt_v2_save_flow_rejects_renamed_reserved_node_before_write(
    tmp_path,
    monkeypatch,
):
    import copy

    from core.prompt_v2 import flow as flow_module

    flow = copy.deepcopy(flow_module.DEFAULT_FLOW)
    _mutate_reserved_flow_node(
        flow,
        "runtime_tool_prompt",
        {"id": "renamed_runtime_tool_prompt"},
    )
    runtime_path = tmp_path / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    with pytest.raises(flow_module.PromptFlowError):
        flow_module.save_flow(flow)

    assert runtime_path.exists() is False


@pytest.mark.parametrize(
    "invalid_variant",
    [
        "missing-singleton",
        "fully-renamed-singleton",
        "platform-excluded-singleton",
        "platform-excluded-policy",
    ],
)
def test_prompt_v2_save_flow_rejects_runtime_contract_that_strict_audit_would_reject(
    tmp_path,
    monkeypatch,
    invalid_variant,
):
    import copy

    from core.prompt_v2 import flow as flow_module

    flow = copy.deepcopy(flow_module.DEFAULT_FLOW)
    if invalid_variant == "missing-singleton":
        flow["nodes"] = [
            node for node in flow["nodes"] if node["id"] != "runtime_tool_prompt"
        ]
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if "runtime_tool_prompt" not in {edge["from"], edge["to"]}
        ]
        flow["edges"].append(
            {"from": "effort_constraint", "to": "current_user_event"}
        )
    elif invalid_variant == "fully-renamed-singleton":
        _mutate_reserved_flow_node(
            flow,
            "runtime_tool_prompt",
            {
                "id": "custom_runtime_context",
                "runtime_key": "runtime_context",
            },
        )
    elif invalid_variant == "platform-excluded-singleton":
        node = next(
            item for item in flow["nodes"] if item["id"] == "runtime_tool_prompt"
        )
        node["platforms"] = ["qq"]
    else:
        node = next(item for item in flow["nodes"] if item["id"] == "private_policy")
        node["platforms"] = ["qq"]

    runtime_path = tmp_path / invalid_variant / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    with pytest.raises(flow_module.PromptFlowError):
        flow_module.save_flow(flow)

    assert runtime_path.exists() is False


def test_prompt_v2_save_flow_accepts_default_platform_branches(tmp_path, monkeypatch):
    from pathlib import Path

    from core.prompt_v2 import flow as flow_module

    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    runtime_path = tmp_path / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    result = flow_module.save_flow(flow)

    assert result["saved"] is True
    assert result["flow"] == flow_module.validate_flow(flow)
    assert runtime_path.exists() is True


def test_prompt_v2_load_flow_rejects_dangling_runtime_symlink(
    tmp_path,
    monkeypatch,
):
    from core.prompt_v2 import flow as flow_module

    runtime_path = tmp_path / "runtime" / "chat" / "flow.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.symlink_to(tmp_path / "missing-flow.json")
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)
    monkeypatch.setattr(
        flow_module,
        "default_flow_path",
        lambda: tmp_path / "missing-default-flow.json",
    )

    with pytest.raises(flow_module.PromptFlowError, match="符号链接"):
        flow_module.load_flow()


def test_prompt_v2_load_flow_rejects_symlinked_runtime_parent(
    tmp_path,
    monkeypatch,
):
    from pathlib import Path

    from core.prompt_v2 import flow as flow_module

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    escaped_chat = tmp_path / "escaped-chat"
    escaped_chat.mkdir()
    (escaped_chat / "flow.json").write_text(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (runtime_root / "chat").symlink_to(
        escaped_chat,
        target_is_directory=True,
    )
    runtime_path = runtime_root / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    with pytest.raises(flow_module.PromptFlowError, match="符号链接"):
        flow_module.load_flow()


def test_prompt_v2_template_values_and_runtime_context_include_platform():
    from core.prompt_v2.context_adapters import build_runtime_context, build_template_values
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.variables import render_scoped_template

    request = PromptCompileRequest(chat_type="group", platform="Web", session_id="group_1001")
    values = build_template_values(request, current_time="2026-06-18 10:00:00 CST")

    assert request.normalized_platform == "web"
    assert values["platform"] == "web"
    assert render_scoped_template("chat/main", "platform={{ platform }}", values) == "platform=web"

    runtime_context = build_runtime_context(request, current_time=values["current_time"])
    runtime_facts = json.loads(
        runtime_context.split("<runtime_context>", 1)[1]
        .split("</runtime_context>", 1)[0]
        .strip()
    )
    assert runtime_facts["platform"] == "web"
    assert runtime_facts["chat_type"] == "group"
    assert values["group_id"] == "1001"
    assert runtime_facts["group_id"] == "1001"
    assert runtime_facts["is_super_user"] is False


def test_prompt_v2_private_runtime_context_clears_misrouted_group_id():
    from core.prompt_v2.context_adapters import build_runtime_context, build_template_values
    from core.prompt_v2.schema import PromptCompileRequest

    request = PromptCompileRequest(
        chat_type="private",
        session_id="private_placeholder",
        group_id="should-not-leak",
    )

    values = build_template_values(request, current_time="2026-07-13 02:00:00 CST")
    runtime_context = build_runtime_context(request, current_time=values["current_time"])
    runtime_facts = json.loads(
        runtime_context.split("<runtime_context>", 1)[1]
        .split("</runtime_context>", 1)[0]
        .strip()
    )

    assert values["group_id"] == ""
    assert "group_id" not in runtime_facts


def test_prompt_v2_template_values_and_runtime_context_use_explicit_super_user_fact(
    monkeypatch,
):
    from core.prompt_v2.context_adapters import build_runtime_context, build_template_values
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr("core.identity.is_super_user_id", lambda _value: False)
    request = PromptCompileRequest(
        chat_type="private",
        sender_id="placeholder-user",
        is_super_user=True,
    )

    values = build_template_values(request, current_time="2026-07-13 01:30:00 CST")
    runtime_context = build_runtime_context(request, current_time=values["current_time"])
    runtime_facts = json.loads(
        runtime_context.split("<runtime_context>", 1)[1]
        .split("</runtime_context>", 1)[0]
        .strip()
    )

    assert values["is_super_user"] == "true"
    assert runtime_facts["is_super_user"] is True


@pytest.mark.asyncio
async def test_prompt_v2_compile_plan_exposes_platform(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert plan.platform == "web"
    assert plan.debug["platform"] == "web"
    assert plan.debug["flow_node_ids"][0] == "base_contract"
    assert "private_policy" in plan.debug["flow_node_ids"]
    assert plan.debug["flow_node_ids"][-1] == "current_user_event"


@pytest.mark.asyncio
async def test_prompt_v2_default_flow_selects_qq_platform_templates():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="group", platform="qq", user_input="你好"),
    )

    assert plan.platform == "qq"
    assert plan.debug["platform"] == "qq"
    assert "qq_common_policy" in plan.debug["flow_node_ids"]
    assert "qq_group_policy" in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert '"platform":"qq"' in joined
    assert "QQ 平台" in joined
    assert "QQ 群聊" in joined


@pytest.mark.asyncio
async def test_prompt_v2_default_flow_skips_qq_templates_for_web_private():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert plan.platform == "web"
    assert plan.debug["platform"] == "web"
    assert "qq_common_policy" not in plan.debug["flow_node_ids"]
    assert "qq_group_policy" not in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert '"platform":"web"' in joined
    assert "QQ 平台" not in joined
    assert "OneBot" not in joined
    assert "CQ 码" not in joined


@pytest.mark.asyncio
async def test_prompt_v2_compiles_group_plan_without_duplicate_dynamic_sections():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            session_id="group_1001",
            user_id="group_1001",
            group_id="1001",
            sender_name="雀",
            sender_id="0000000000",
            bot_name="nanobot",
            bot_aliases=["bot"],
            current_message_id="m1",
            trigger_reason="direct_mention",
            timing_decision="continue",
            user_input="当前问题",
            persona_text="画像文本",
            history_header="<conversation_context>\n群聊历史说明\n</conversation_context>",
            history_messages=[
                {"role": "user", "content": "[msg_id]old\n[发言内容]旧问题"},
                {"role": "assistant", "content": "[发言内容]旧回复"},
            ],
            group_profile_context="[GroupProfileContext]\ngroup_id: 1001\n- style: 轻松\n[/GroupProfileContext]",
            runtime_tool_prompt="[RuntimeTool]\n规则：必须 reply/no_reply",
            debug={
                "message_token_estimate": -1,
                "tool_schema_token_estimate": -1,
                "token_estimate": -1,
            },
            tool_schemas=[
                _metrics_tool("reply", description="发送最终回复"),
                _metrics_tool(
                    "no_reply",
                    description="决定不回复",
                    parameters={
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                    },
                ),
            ],
        )
    )

    assert dataclasses.is_dataclass(plan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.engine = "v1"
    assert isinstance(plan, PromptPlan)
    assert plan.engine == "prompt"
    assert plan.chat_type == "group"
    assert plan.prompt_key == "chat_group"
    assert plan.messages_without_current_user == plan.messages[:-1]
    assert plan.current_user_content == plan.messages[-1]["content"]
    assert plan.request_json["messages"] == plan.messages
    assert plan.request_json["tools"] == plan.tool_schemas
    assert len(plan.prompt_sha256) == 64
    assert plan.message_token_estimate > 0
    assert plan.tool_schema_token_estimate > 0
    assert plan.token_estimate == (
        plan.message_token_estimate + plan.tool_schema_token_estimate
    )
    from core.prompt_v2.section_renderer import sha256_text, stable_json

    assert plan.prompt_sha256 == sha256_text(stable_json(plan.request_json))
    assert plan.debug["message_token_estimate"] == plan.message_token_estimate
    assert plan.debug["tool_schema_token_estimate"] == plan.tool_schema_token_estimate
    assert plan.debug["token_estimate"] == plan.token_estimate
    detached_request = plan.request_json
    detached_request["messages"][0]["content"] = "外部修改"
    detached_request["tools"][0]["function"]["description"] = "外部修改"
    assert plan.messages[0]["content"] != "外部修改"
    assert plan.tool_schemas[0]["function"]["description"] != "外部修改"
    assert plan.prompt_sha256 == sha256_text(stable_json(plan.request_json))
    assert plan.section_hashes["base_contract"]
    assert plan.section_hashes["runtime_tool_prompt"]
    assert plan.debug["history_message_count"] == 2
    assert plan.debug["flow_node_ids"][:4] == [
        "base_contract",
        "qq_common_policy",
        "group_policy",
        "qq_group_policy",
    ]
    assert all(
        {"node_id", "node_type", "template_key", "runtime_key"}.issubset(section)
        for section in plan.flow_sections
    )

    roles = [m["role"] for m in plan.messages]
    assert roles == [
        "system",
        "system",
        "system",
        "system",
        "system",
        "system",
        "user",
        "system",
        "user",
        "assistant",
        "user",
        "system",
        "user",
    ]

    contents = [str(m["content"]) for m in plan.messages]
    joined = "\n".join(contents)
    assert "## QQ 平台" in joined
    assert "## QQ 群聊" in joined
    assert "## 群聊行为" in joined
    assert "## 私聊行为" not in joined
    assert "[GroupProfileContext]" in joined
    assert "[ExpressionContext]" not in joined
    assert "[JargonContext]" not in joined
    assert sum("<user_input>" in c for c in contents) == 1
    assert sum("[RuntimeTool]" in c for c in contents) == 1
    assert sum('"section":"persona_reference"' in c for c in contents) == 1
    assert not any("<persona_reference" in c for c in contents)
    assert "当前问题" in plan.current_user_content
    assert "当前问题" not in "\n".join(contents[:-1])
    serialized_plan = json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))
    assert serialized_plan["engine"] == "prompt"
    assert serialized_plan["flow_sections"] == plan.flow_sections


@pytest.mark.asyncio
async def test_prompt_v2_flow_sections_describe_output_status_and_message_indexes(
    monkeypatch,
):
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    ordered_nodes = [
        {"id": "base_contract", "type": "template", "template_key": "chat/main"},
        {
            "id": "private_policy",
            "type": "template",
            "template_key": "chat/branch_private",
        },
        {"id": "runtime_context", "type": "runtime", "runtime_key": "runtime_context"},
        {
            "id": "effort_constraint",
            "type": "runtime",
            "runtime_key": "effort_constraint",
        },
        {
            "id": "runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        },
        {
            "id": "current_user_event",
            "type": "runtime",
            "runtime_key": "current_user_event",
        },
        {
            "id": "duplicate_runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        },
    ]
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow={}, path=Path("test-flow.json"), source="test"),
    )
    monkeypatch.setattr(
        compiler,
        "ordered_nodes_for_chat",
        lambda _flow, _chat_type, platform: ordered_nodes,
    )
    original_load_template = compiler.load_template

    def load_template(template_key):
        template = original_load_template(template_key)
        if template_key == "chat/main":
            return dataclasses.replace(template, body="BASE SECTION")
        return template

    monkeypatch.setattr(compiler, "load_template", load_template)

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="web",
            user_id="u1",
            user_input="CURRENT USER SECTION",
            persona_text="FALLBACK PERSONA",
            runtime_tool_prompt="RUNTIME TOOL SECTION",
        ),
        strict_audit=False,
    )
    sections = {section["node_id"]: section for section in plan.flow_sections}
    legacy_fields = {"node_id", "node_type", "template_key", "runtime_key"}

    assert all(legacy_fields.issubset(section) for section in plan.flow_sections)

    base = sections["base_contract"]
    assert (base["origin"], base["status"]) == ("flow", "emitted")
    assert [plan.messages[index]["content"] for index in base["message_indexes"]] == [
        "BASE SECTION"
    ]

    runtime_tool = sections["runtime_tool_prompt"]
    assert (runtime_tool["origin"], runtime_tool["status"]) == ("flow", "emitted")
    assert [
        plan.messages[index]["content"]
        for index in runtime_tool["message_indexes"]
    ] == ["RUNTIME TOOL SECTION"]

    effort = sections["effort_constraint"]
    assert (effort["origin"], effort["status"], effort["message_indexes"]) == (
        "flow",
        "empty",
        [],
    )

    duplicate = sections["duplicate_runtime_tool_prompt"]
    assert (
        duplicate["origin"],
        duplicate["status"],
        duplicate["message_indexes"],
    ) == ("flow", "skipped_duplicate", [])

    persona = sections["persona_reference"]
    assert (persona["origin"], persona["status"]) == ("fallback", "emitted")
    assert persona["message_indexes"]
    assert all(
        "FALLBACK PERSONA" in str(plan.messages[index]["content"])
        for index in persona["message_indexes"]
    )

    current_user = sections["current_user_event"]
    assert (current_user["origin"], current_user["status"]) == ("flow", "emitted")
    assert current_user["message_indexes"] == [len(plan.messages) - 1]
    assert plan.messages[current_user["message_indexes"][0]] == plan.messages[-1]


@pytest.mark.asyncio
async def test_prompt_v2_compiles_private_plan_and_keeps_rules_separate():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            session_id="private_u1",
            user_id="u1",
            sender_name="用户",
            user_input="<user_input>\n你好\n</user_input>",
            persona_text="无已存储画像",
            history_header="<conversation_context>\n私聊历史说明\n</conversation_context>",
            history_messages=[{"role": "user", "content": "上文"}],
            runtime_tool_prompt="[RuntimeTool]\n规则：必须 reply/no_reply",
        )
    )

    joined = "\n".join(str(m["content"]) for m in plan.messages)
    assert plan.chat_type == "private"
    assert plan.prompt_key == "chat_private"
    assert "## 私聊行为" in joined
    assert "## 群聊行为" not in joined
    assert "## 群聊发言时机" not in joined
    assert plan.current_user_content == (
        '<message_meta>\n{"sender_name":"用户"}\n</message_meta>\n'
        "<user_input>\n你好\n</user_input>"
    )
    assert sum("<user_input>" in str(m["content"]) for m in plan.messages) == 1


@pytest.mark.asyncio
async def test_prompt_v2_moves_group_profile_header_after_history_messages():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            user_input="当前输入",
            history_header=(
                "[GroupProfileContext]\n"
                "group_id: 1001\n"
                "- style: 轻松\n"
                "[/GroupProfileContext]\n"
                "<conversation_context>\n群聊历史说明\n</conversation_context>"
            ),
            history_messages=[{"role": "user", "content": "UNIQUE_HISTORY_MESSAGE"}],
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    contents = [str(m["content"]) for m in plan.messages]
    header_idx = next(i for i, c in enumerate(contents) if "<conversation_context>" in c)
    history_idx = next(i for i, c in enumerate(contents) if "UNIQUE_HISTORY_MESSAGE" in c)
    profile_idx = next(i for i, c in enumerate(contents) if "[GroupProfileContext]" in c)

    assert header_idx < history_idx < profile_idx
    assert sum("[GroupProfileContext]" in c for c in contents) == 1
    assert "[GroupProfileContext]" not in contents[header_idx]


@pytest.mark.asyncio
async def test_prompt_v2_moves_group_memory_context_after_history_messages():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            user_input="当前输入",
            history_header=(
                '<group_memory_context group_id="group_1001" selected_count="1">\n'
                "- 群风格: 轻松\n"
                "</group_memory_context>\n"
                "<conversation_context>\n群聊历史说明\n</conversation_context>"
            ),
            history_messages=[{"role": "user", "content": "UNIQUE_HISTORY_MESSAGE"}],
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    contents = [str(m["content"]) for m in plan.messages]
    header_idx = next(i for i, c in enumerate(contents) if "<conversation_context>" in c)
    history_idx = next(i for i, c in enumerate(contents) if "UNIQUE_HISTORY_MESSAGE" in c)
    memory_idx = next(
        i
        for i, c in enumerate(contents)
        if 'selected_count=\\"1\\"' in c
    )

    assert header_idx < history_idx < memory_idx
    assert sum('selected_count=\\"1\\"' in c for c in contents) == 1
    assert "<group_memory_context" not in contents[header_idx]
    assert "[GroupProfileContext]" not in contents[memory_idx]


def test_prompt_v2_audit_reports_duplicate_required_sections():
    import copy

    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("group")
    for node_id in (
        "persona_reference",
        "runtime_tool_prompt",
        "current_user_event",
    ):
        flow_sections.append(
            copy.deepcopy(
                next(section for section in flow_sections if section["node_id"] == node_id)
            )
        )
    flow_sections.append(
        next(
            section
            for section in _valid_prompt_v2_flow_sections("private")
            if section["node_id"] == "private_policy"
        )
    )

    plan = PromptPlan(
        engine="v2",
        chat_type="group",
        prompt_key="chat_group",
        messages=[{"role": "user", "content": "正文不参与结构审计"}],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=1,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan, audit_messages=False)
    assert audit.ok is False
    assert any(
        "required flow section current_user_event must appear once, got 2" in issue
        for issue in audit.issues
    )
    assert any(
        "required flow section runtime_tool_prompt must appear once, got 2" in issue
        for issue in audit.issues
    )
    assert any(
        "required flow section persona_reference must appear once, got 2" in issue
        for issue in audit.issues
    )
    assert any("forbidden flow section private_policy" in issue for issue in audit.issues)


def _valid_prompt_v2_flow_sections(chat_type: str) -> list[dict[str, object]]:
    from core.prompt_v2.flow_contract import required_contracts

    return [
        {
            "node_id": contract.node_id,
            "node_type": contract.node_type,
            "template_key": contract.template_key,
            "runtime_key": contract.runtime_key,
            "origin": "flow",
            "status": "emitted",
            "message_indexes": [],
        }
        for contract in required_contracts("qq", chat_type)
    ]


def test_prompt_v2_audit_rejects_sections_without_output_metadata():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("private")
    base = next(
        section for section in flow_sections if section["node_id"] == "base_contract"
    )
    base.pop("origin")
    base.pop("status")
    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan, audit_messages=False)

    assert audit.ok is False
    assert "base_contract origin is required" in audit.issues
    assert "base_contract status is required" in audit.issues


def test_prompt_v2_audit_does_not_treat_explicit_empty_status_as_legacy():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("private")
    next(
        section
        for section in flow_sections
        if section["runtime_key"] == "persona_reference"
    )["status"] = ""
    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan, audit_messages=False)

    assert audit.ok is False
    assert "persona_reference status is invalid" in audit.issues


@pytest.mark.parametrize(
    ("runtime_key", "invalid_fields"),
    [
        ("persona_reference", {"node_id": "renamed_persona_reference"}),
        ("runtime_tool_prompt", {"node_type": "template"}),
        ("current_user_event", {"template_key": "chat/main"}),
    ],
    ids=("wrong-node-id", "wrong-node-type", "unexpected-template-key"),
)
def test_prompt_v2_audit_rejects_invalid_singleton_section_identity(runtime_key, invalid_fields):
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("private")
    section = next(item for item in flow_sections if item["runtime_key"] == runtime_key)
    section.update(invalid_fields)
    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan, audit_messages=False)

    assert audit.ok is False
    assert any(runtime_key in issue for issue in audit.issues)


@pytest.mark.parametrize(
    ("chat_type", "invalid_fields"),
    [
        ("private", {"node_id": "renamed_private_policy"}),
        ("private", {"node_type": "runtime"}),
        ("private", {"runtime_key": "unexpected_policy_runtime"}),
        ("private", {"template_key": "chat/main"}),
        ("group", {"node_id": "renamed_group_policy"}),
        ("group", {"node_type": "runtime"}),
        ("group", {"runtime_key": "unexpected_policy_runtime"}),
        ("group", {"template_key": "chat/main"}),
    ],
    ids=(
        "private-wrong-node-id",
        "private-wrong-node-type",
        "private-unexpected-runtime-key",
        "private-wrong-template-key",
        "group-wrong-node-id",
        "group-wrong-node-type",
        "group-unexpected-runtime-key",
        "group-wrong-template-key",
    ),
)
def test_prompt_v2_audit_rejects_invalid_policy_section_identity(chat_type, invalid_fields):
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections(chat_type)
    policy_node_id = f"{chat_type}_policy"
    next(
        section for section in flow_sections if section["node_id"] == policy_node_id
    ).update(invalid_fields)
    plan = PromptPlan(
        engine="prompt",
        chat_type=chat_type,
        prompt_key=f"chat_{chat_type}",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan, audit_messages=False)

    assert audit.ok is False
    assert any(policy_node_id in issue for issue in audit.issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_audit", [True, False], ids=("strict", "non-strict"))
async def test_prompt_v2_compile_audit_rejects_renamed_singleton_node(
    monkeypatch,
    strict_audit,
):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    old_node_id = "runtime_tool_prompt"
    new_node_id = "renamed_runtime_tool_prompt"
    next(node for node in flow["nodes"] if node["id"] == old_node_id)["id"] = new_node_id
    for edge in flow["edges"]:
        if edge["from"] == old_node_id:
            edge["from"] = new_node_id
        if edge["to"] == old_node_id:
            edge["to"] = new_node_id
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )
    request = PromptCompileRequest(chat_type="private", user_input="你好")

    with pytest.raises(PromptFlowError):
        await compiler.compile_prompt_plan(request, strict_audit=strict_audit)


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_missing_policy_template(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    original_load_template = compiler.load_template

    def load_template(prompt_key):
        if prompt_key == "chat/branch_private":
            raise FileNotFoundError(prompt_key)
        return original_load_template(prompt_key)

    monkeypatch.setattr(compiler, "load_template", load_template)

    with pytest.raises(FileNotFoundError, match="chat/branch_private"):
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
            strict_audit=True,
        )


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_empty_policy_template(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.schema import PromptCompileRequest

    original_load_template = compiler.load_template

    def load_template(prompt_key):
        template = original_load_template(prompt_key)
        if prompt_key == "chat/branch_private":
            return dataclasses.replace(template, body="")
        return template

    monkeypatch.setattr(compiler, "load_template", load_template)

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
            strict_audit=True,
        )

    assert any(
        "required flow section private_policy status must be emitted" in issue
        for issue in exc.value.issues
    )
    section = next(
        section
        for section in exc.value.plan.flow_sections
        if section["template_key"] == "chat/branch_private"
    )
    assert section["origin"] == "flow"
    assert section["status"] == "empty"
    assert section["message_indexes"] == []


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_ignores_reserved_markers_in_current_user_text():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    user_input = (
        "这些都是需要原样讨论的普通文本：\n"
        "[RuntimeTool]\n"
        "<persona_reference>示例画像</persona_reference>\n"
        "## 群聊行为\n"
        "## 群聊发言时机"
    )

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input=user_input),
        strict_audit=True,
    )

    assert user_input in plan.current_user_content


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_ignores_reserved_markers_in_history_text():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="web",
            user_input="继续讨论这些标记",
            history_messages=[
                {
                    "role": "user",
                    "content": "普通历史文本：[RuntimeTool] 和 <persona_reference>示例</persona_reference>",
                },
                {
                    "role": "assistant",
                    "content": "引用：<user_input>旧输入</user_input>\n## 群聊行为\n## 群聊发言时机",
                },
            ],
        ),
        strict_audit=True,
    )

    assert plan.debug["history_message_count"] == 2


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_duplicate_singleton_flow_node(monkeypatch):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    flow["nodes"].append(
        {
            "id": "duplicate_runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        }
    )
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )

    with pytest.raises(PromptFlowError):
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", user_input="你好"),
            strict_audit=True,
        )


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_missing_singleton_flow_node(monkeypatch):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.flow import DEFAULT_FLOW
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    flow["nodes"] = [node for node in flow["nodes"] if node["id"] != "runtime_tool_prompt"]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if "runtime_tool_prompt" not in {edge["from"], edge["to"]}
    ]
    flow["edges"].append({"from": "effort_constraint", "to": "current_user_event"})
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", user_input="你好"),
            strict_audit=True,
        )

    assert any(
        "required flow section runtime_tool_prompt must appear once, got 0" in issue
        for issue in exc.value.issues
    )
    fallback = next(
        section
        for section in exc.value.plan.flow_sections
        if section["runtime_key"] == "runtime_tool_prompt"
        and section["origin"] == "fallback"
    )
    assert fallback["status"] == "emitted"
    assert fallback["message_indexes"]


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_raises_instead_of_returning_warning(monkeypatch):
    import core.prompt_v2.compiler as compiler
    from core.prompt_v2.audit import PromptAuditError, PromptAuditResult
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr(
        compiler,
        "audit_prompt_plan",
        lambda _plan: PromptAuditResult(ok=False, issues=["audit broken"]),
    )

    preview_plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(chat_type="group", user_input="你好"),
        strict_audit=False,
    )
    assert "audit broken" in preview_plan.warnings
    assert preview_plan.message_token_estimate > 0
    assert preview_plan.tool_schema_token_estimate == 0
    assert preview_plan.token_estimate == preview_plan.message_token_estimate
    assert preview_plan.debug["message_token_estimate"] == (
        preview_plan.message_token_estimate
    )
    assert preview_plan.debug["tool_schema_token_estimate"] == 0

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="group", user_input="你好"),
            strict_audit=True,
        )

    assert "audit broken" in str(exc.value)


@pytest.mark.asyncio
async def test_prompt_v2_identity_context_renders_whitelisted_variables():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            sender_id="0000000000",
            bot_name="七濑",
            bot_aliases=["小七", "bot"],
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    identity = next(str(m["content"]) for m in plan.messages if "<identity_context>" in str(m["content"]))
    assert "你叫 七濑" in identity
    assert "别人可能这样叫你" in identity
    assert "小七" in identity
    assert "bot" in identity
    assert "{{" not in identity
    assert "}}" not in identity


@pytest.mark.asyncio
async def test_prompt_v2_runtime_fact_survives_identity_template_without_placeholder(
    monkeypatch,
):
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    original_load_template = compiler.load_template

    def load_template_without_authorization_placeholder(template_key):
        loaded = original_load_template(template_key)
        if template_key != "chat/identity_context":
            return loaded
        return SimpleNamespace(
            body="<identity_context>\n固定身份\n</identity_context>",
            path=loaded.path,
            resolution=loaded.resolution,
        )

    monkeypatch.setattr(compiler, "load_template", load_template_without_authorization_placeholder)

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            sender_id="placeholder-user",
            is_super_user=True,
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    identity = next(
        str(message["content"])
        for message in plan.messages
        if str(message["content"]).startswith("<identity_context>")
    )
    runtime_context = next(
        str(message["content"])
        for message in plan.messages
        if str(message["content"]).startswith("<runtime_context>")
    )
    runtime_facts = json.loads(
        runtime_context.split("<runtime_context>", 1)[1]
        .split("</runtime_context>", 1)[0]
        .strip()
    )

    assert identity == "<identity_context>\n固定身份\n</identity_context>"
    assert runtime_facts["is_super_user"] is True


@pytest.mark.asyncio
async def test_prompt_v2_identity_context_uses_non_empty_default_values(monkeypatch):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr("core.identity.NANOBOT_CHARACTER_NAME", "nanobot")
    monkeypatch.setattr("core.identity.NANOBOT_BOT_ALIASES", {"nanobot"})
    monkeypatch.setattr("core.identity.NANOBOT_SUPER_USER_IDS", set())

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    identity = next(str(m["content"]) for m in plan.messages if "<identity_context>" in str(m["content"]))
    assert "你叫 nanobot" in identity
    assert "nanobot" in identity
    assert "super_user_id:" not in identity
    assert "{{" not in identity
    assert "character_name:" not in identity


def test_prompt_v2_section_variables_are_whitelisted_by_scope():
    from core.prompt_v2.variables import PromptVariableError, render_scoped_template

    rendered = render_scoped_template(
        "identity_context",
        "你叫 {{character_name}}\n{{ name_hint }}\n{{ alias_names }}\n{{is_super_user}}",
        {
            "character_name": "七濑",
            "name_hint": "小七",
            "alias_names": "小七\n七七",
            "is_super_user": "true",
        },
    )
    assert rendered == "你叫 七濑\n小七\n小七\n七七\ntrue"

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ user_input }}", {"user_input": "禁止"})

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ unknown_name }}", {})


def test_prompt_v2_renders_classifier_legacy_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    task_path = default_dir / "tasks" / "classifier_legacy.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 旧分类器兼容\nversion: 1\nkind: task\ntool_name: classifier_legacy\n---\n"
        "{{ system_prompt }}\n待判定消息:\n{{ message }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.task_templates import TASK_PAYLOAD_MARKER, render_task_messages

    messages = render_task_messages(
        "classifier_legacy",
        {"system_prompt": "旧系统", "message": "ping"},
        fallback_messages=[
            {"role": "system", "content": "fallback"},
            {"role": "user", "content": "ping"},
        ],
    )

    assert "旧系统" in messages[0]["content"]
    assert "待判定消息:" in messages[0]["content"]
    assert TASK_PAYLOAD_MARKER in messages[0]["content"]
    assert "ping" not in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "ping"}


def test_prompt_v2_renders_memory_extract_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    task_path = default_dir / "tasks" / "memory_extract.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 记忆抽取\nversion: 1\nkind: task\ntool_name: memory_extract\n---\n"
        "已有记忆:\n{{ existing_memory }}\n对话:\n{{ conversation }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(default_dir / "runtime"))

    from core.prompt_v2.task_templates import render_task_prompt

    rendered = render_task_prompt(
        "memory_extract",
        {"conversation": "用户: 喜欢 TypeScript", "existing_memory": "{}"},
        fallback_text="fallback",
    )

    assert "喜欢 TypeScript" in rendered
    assert "已有记忆:" in rendered


def test_prompt_v2_templates_are_isolated_from_prompt_manager_and_legacy_runtime():
    import core.prompt_v2.compiler as compiler
    from core.prompt_v2.template_loader import load_template

    source = inspect.getsource(compiler)
    assert "core.legacy_prompt_runtime" not in source
    assert "core.prompt_runtime" not in source
    assert "PromptManager" not in source
    assert "prompt_assembler" not in source
    assert "build_nanobot_prompt" not in source

    main = load_template("chat/main").body
    group = load_template("chat/branch_group").body
    private = load_template("chat/branch_private").body
    assert "{{ user_input }}" not in main
    assert "{{ user_input }}" not in group
    assert "{{ user_input }}" not in private
    assert "## 群聊行为" not in main
    assert "## 私聊行为" not in main
    assert "## 群聊行为" in group
    assert "## 私聊行为" not in group
    assert "## 私聊行为" in private
    assert "## 群聊行为" not in private
