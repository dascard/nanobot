"""Agent、工具、Prompt、LLM 与回复合同观测模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, text

from core.db.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"
    run_id = Column(String, primary_key=True, index=True)
    trace_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="")
    group_id = Column(String, index=True, default="")
    run_type = Column(String, index=True, default="chat")
    prompt_mode = Column(String, index=True, default="legacy")
    prompt_key = Column(String, index=True, default="")
    prompt_source = Column(String, index=True, default="")
    prompt_runtime_path = Column(Text, default="")
    prompt_default_path = Column(Text, default="")
    prompt_sha256 = Column(String, index=True, default="")
    prompt_template_resolutions_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    model = Column(String, index=True, default="")
    status = Column(String, index=True, default="running")
    input_preview = Column(Text, default="")
    output_preview = Column(Text, default="")
    error = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    meta_json = Column(Text, default="{}")
    started_at = Column(DateTime, default=datetime.now, index=True)
    finished_at = Column(DateTime, nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    tool_call_id = Column(String, primary_key=True, index=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    tool_name = Column(String, index=True, default="")
    args_json = Column(Text, default="{}")
    result_preview = Column(Text, default="")
    status = Column(String, index=True, default="running")
    latency_ms = Column(Integer, default=0)
    error = Column(Text, default="")
    started_at = Column(DateTime, default=datetime.now, index=True)
    finished_at = Column(DateTime, nullable=True)


class PromptRenderLog(Base):
    __tablename__ = "prompt_render_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    prompt_key = Column(String, index=True, default="")
    mode = Column(String, index=True, default="preview")
    prompt_source = Column(String, index=True, default="")
    prompt_runtime_path = Column(Text, default="")
    prompt_default_path = Column(Text, default="")
    prompt_sha256 = Column(String, index=True, default="")
    prompt_template_resolutions_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    variables_json = Column(Text, default="{}")
    rendered_preview = Column(Text, default="")
    token_estimate = Column(Integer, default=0)
    warnings_json = Column(Text, default="[]")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class PromptFileVersion(Base):
    __tablename__ = "prompt_file_versions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    prompt_key = Column(String, index=True, default="")
    backup_name = Column(String, index=True, default="")
    content_hash = Column(String, index=True, default="")
    operator = Column(String, default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class LLMApiRequestLog(Base):
    __tablename__ = "llm_api_request_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    source = Column(String, index=True, default="")
    phase = Column(String, index=True, default="")
    round_index = Column(Integer, default=0)
    route_attempt_index = Column(Integer, default=0)
    provider = Column(String, index=True, default="")
    model = Column(String, index=True, default="")
    url = Column(Text, default="")
    method = Column(String, default="POST")
    request_json = Column(Text, default="{}")
    request_preview = Column(Text, default="")
    headers_json = Column(Text, default="{}")
    status = Column(String, index=True, default="created")
    error_category = Column(
        String(32),
        nullable=False,
        index=True,
        default="none",
        server_default=text("'none'"),
    )
    cache_status = Column(
        String,
        nullable=False,
        index=True,
        default="pending",
        server_default=text("'pending'"),
    )
    cache_hit = Column(Boolean, nullable=True)
    cache_hit_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cache_miss_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cache_write_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cache_details_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    input_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    output_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    first_token_latency_ms = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cost_microusd = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cost_source = Column(
        String,
        nullable=False,
        default="not_available",
        server_default=text("'not_available'"),
    )
    response_status = Column(Integer, default=0)
    response_json = Column(Text, default="{}")
    response_preview = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    message_sources_json = Column(Text, default="[]")
    request_lint_json = Column(Text, default="{}")
    actual_sent_tools_json = Column(Text, default="[]")
    runtime_enabled_tools_json = Column(Text, default="[]")
    runtime_disabled_tools_json = Column(Text, default="[]")
    framework_injected_tools_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now, index=True)


class ReplyContractCheckLog(Base):
    __tablename__ = "reply_contract_check_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    run_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    attempt = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    has_reply_tool = Column(Integer, default=0)
    has_no_reply_tool = Column(Integer, default=0)
    has_structured_fallback = Column(Integer, default=0)
    reply_tool_call_count = Column(Integer, default=0)
    no_reply_tool_call_count = Column(Integer, default=0)
    structured_fallback_count = Column(Integer, default=0)
    total_final_action_count = Column(Integer, default=0)
    result = Column(String, index=True, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class RuntimeTelemetryEvent(Base):
    """无正文 RuntimeEvent 持久化账本。"""

    __tablename__ = "runtime_telemetry_events"
    __table_args__ = (
        Index(
            "ix_runtime_telemetry_name_time",
            "name",
            "occurred_at",
        ),
        Index(
            "ix_runtime_telemetry_job_time",
            "job_id",
            "occurred_at",
        ),
        Index(
            "ix_runtime_telemetry_task_time",
            "task_run_id",
            "occurred_at",
        ),
    )

    event_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    domain = Column(String(64), nullable=False, index=True)
    phase = Column(String(32), nullable=False, index=True)
    occurred_at = Column(DateTime, nullable=False, index=True)

    request_id = Column(String(160), nullable=False, default="", index=True)
    session_id = Column(String(160), nullable=False, default="", index=True)
    turn_id = Column(String(160), nullable=False, default="", index=True)
    trace_id = Column(String(160), nullable=False, default="", index=True)
    run_id = Column(String(160), nullable=False, default="", index=True)
    task_id = Column(String(160), nullable=False, default="", index=True)
    task_run_id = Column(String(160), nullable=False, default="", index=True)
    job_id = Column(String(160), nullable=False, default="", index=True)
    tool_call_id = Column(String(160), nullable=False, default="", index=True)
    delivery_id = Column(String(160), nullable=False, default="", index=True)
    parent_job_id = Column(String(160), nullable=False, default="", index=True)

    registry_generation = Column(Integer, nullable=False)
    registry_sha256 = Column(String(64), nullable=False, index=True)
    module_id = Column(String(128), nullable=False, index=True)
    module_version = Column(String(64), nullable=False)
    artifact_revision = Column(String(128), nullable=False, default="", index=True)
    failure_code = Column(String(64), nullable=False, default="", index=True)
    attributes_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    dropped_attribute_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)


__all__ = [
    "AgentRun",
    "LLMApiRequestLog",
    "PromptFileVersion",
    "PromptRenderLog",
    "ReplyContractCheckLog",
    "RuntimeTelemetryEvent",
    "ToolCall",
]
