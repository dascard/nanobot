"""代码所有的 Admin 表视图描述符与结构化只读查询服务。"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Literal

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from core.database import Base
from core.registry import (
    RegistryBuilder,
    RegistryGeneration,
    RegistrySnapshot,
)
from core.registry.validation import canonical_json


AdminFilterValueType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "datetime",
]
AdminSortDirection = Literal["asc", "desc"]
AdminViewLifecycle = Literal["active", "deprecated"]

_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_CURSOR_LENGTH = 2048
_MAX_CURSOR_OFFSET = 1_000_000
_FILTERABLE_COLUMNS = frozenset({
    "id",
    "key",
    "name",
    "user_id",
    "session_id",
    "group_id",
    "chat_stream_id",
    "run_id",
    "trace_id",
    "tool_name",
    "target_type",
    "source",
    "source_type",
    "provider",
    "model",
    "status",
    "category",
    "enabled",
})


class AdminTableViewError(RuntimeError):
    """Admin 表视图稳定领域错误。"""

    code = "admin_view_error"


class AdminTableViewNotFoundError(AdminTableViewError):
    code = "admin_view_not_found"


class AdminTableViewUnavailableError(AdminTableViewError):
    code = "admin_view_unavailable"


class AdminTableViewFilterError(AdminTableViewError):
    code = "admin_view_filter_invalid"


class AdminTableViewCursorError(AdminTableViewError):
    code = "admin_view_cursor_invalid"


class AdminTableViewLimitError(AdminTableViewError):
    code = "admin_view_limit_invalid"


@dataclass(frozen=True, slots=True)
class AdminTableFilterDescriptor:
    filter_id: str
    column: str
    value_type: AdminFilterValueType

    def to_dict(self) -> dict[str, str]:
        return {
            "filter_id": self.filter_id,
            "column": self.column,
            "value_type": self.value_type,
        }


@dataclass(frozen=True, slots=True)
class AdminTableSortDescriptor:
    column: str
    direction: AdminSortDirection
    tie_breaker: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "column": self.column,
            "direction": self.direction,
            "tie_breaker": self.tie_breaker,
        }


@dataclass(frozen=True, slots=True)
class AdminTableViewDescriptor:
    """一个代码所有、不可由请求提升权限的表视图。"""

    registry_id: str
    owner: str
    group_id: str
    group_label: str
    table_name: str
    description: str
    allowed_columns: tuple[str, ...]
    default_sort: AdminTableSortDescriptor
    filters: tuple[AdminTableFilterDescriptor, ...]
    max_limit: int = 200
    default_limit: int = 50
    redact_columns: tuple[str, ...] = ()
    preview_only_columns: tuple[str, ...] = ()
    max_text_length: int = 1000
    lifecycle: AdminViewLifecycle = "active"
    registry_namespace: str = field(
        default="admin_table_view",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def __post_init__(self) -> None:
        _require_sql_identifier(self.table_name, field_name="table_name")
        if not self.allowed_columns:
            raise ValueError("Admin table view 必须声明可见字段")
        _require_unique(self.allowed_columns, field_name="allowed_columns")
        for column in self.allowed_columns:
            _require_sql_identifier(column, field_name="allowed_column")
        if self.default_sort.column not in self.allowed_columns:
            raise ValueError("default_sort.column 必须属于 allowed_columns")
        if (
            self.default_sort.tie_breaker is not None
            and self.default_sort.tie_breaker not in self.allowed_columns
        ):
            raise ValueError(
                "default_sort.tie_breaker 必须属于 allowed_columns"
            )
        filter_ids = tuple(item.filter_id for item in self.filters)
        _require_unique(filter_ids, field_name="filters")
        for item in self.filters:
            if item.column not in self.allowed_columns:
                raise ValueError("filter.column 必须属于 allowed_columns")
        if not 1 <= self.default_limit <= self.max_limit:
            raise ValueError("default_limit 必须位于 max_limit 范围内")
        if self.max_text_length <= 0:
            raise ValueError("max_text_length 必须大于 0")
        for field_name, columns in (
            ("redact_columns", self.redact_columns),
            ("preview_only_columns", self.preview_only_columns),
        ):
            _require_unique(columns, field_name=field_name)
            if set(columns) - set(self.allowed_columns):
                raise ValueError(f"{field_name} 必须属于 allowed_columns")

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "owner": self.owner,
            "group_id": self.group_id,
            "group_label": self.group_label,
            "table_name": self.table_name,
            "description": self.description,
            "allowed_columns": self.allowed_columns,
            "default_sort": self.default_sort.to_dict(),
            "filters": tuple(item.to_dict() for item in self.filters),
            "max_limit": self.max_limit,
            "default_limit": self.default_limit,
            "redact_columns": self.redact_columns,
            "preview_only_columns": self.preview_only_columns,
            "max_text_length": self.max_text_length,
            "lifecycle": self.lifecycle,
        }

    def filter_by_id(
        self,
    ) -> dict[str, AdminTableFilterDescriptor]:
        return {item.filter_id: item for item in self.filters}

    def to_public_dict(self) -> dict[str, object]:
        redact = set(self.redact_columns)
        preview = set(self.preview_only_columns)
        return {
            "view_id": self.registry_id,
            "owner": self.owner,
            "group_id": self.group_id,
            "group_label": self.group_label,
            "description": self.description,
            "columns": [
                {
                    "name": column,
                    "display_policy": (
                        "redacted"
                        if column in redact
                        else "preview"
                        if column in preview
                        else "full"
                    ),
                }
                for column in self.allowed_columns
            ],
            "default_sort": self.default_sort.to_dict(),
            "filters": [item.to_dict() for item in self.filters],
            "max_limit": self.max_limit,
            "default_limit": self.default_limit,
            "lifecycle": self.lifecycle,
        }


@dataclass(frozen=True, slots=True)
class AdminTableRows:
    view_id: str
    total: int
    limit: int
    has_next: bool
    next_cursor: str | None
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    cell_meta: tuple[dict[str, dict[str, object]], ...]


@dataclass(frozen=True, slots=True)
class _ViewSeed:
    group_id: str
    group_label: str
    owner: str
    table_name: str
    description: str
    sort_column: str | None = None
    hidden_columns: tuple[str, ...] = ()
    redact_columns: tuple[str, ...] = ()
    preview_only_columns: tuple[str, ...] = ()
    max_text_length: int = 1000


def _seed(
    group_id: str,
    group_label: str,
    owner: str,
    table_name: str,
    description: str,
    **kwargs: object,
) -> _ViewSeed:
    return _ViewSeed(
        group_id=group_id,
        group_label=group_label,
        owner=owner,
        table_name=table_name,
        description=description,
        **kwargs,
    )


_VIEW_SEEDS = (
    _seed("core", "核心对话", "core.chat", "users", "用户与群聊实体。"),
    _seed(
        "core",
        "核心对话",
        "core.chat",
        "chat_logs",
        "原始消息存档，含 tool 与 ambient。",
        preview_only_columns=("content", "source_message_ids_json", "meta_json"),
    ),
    _seed(
        "core",
        "核心对话",
        "core.chat",
        "conversation_turns",
        "精简对话上下文。",
        preview_only_columns=("content", "source_message_ids_json", "meta_json"),
    ),
    _seed(
        "core",
        "核心对话",
        "core.memory",
        "memory_digests",
        "分层长期记忆摘要。",
        preview_only_columns=("content", "meta_json"),
    ),
    _seed(
        "core",
        "核心对话",
        "core.memory",
        "rolling_session_summaries",
        "滚动上下文摘要结果。",
        preview_only_columns=(
            "summary_text",
            "summary_json",
            "source_turn_ids_json",
            "issues_json",
            "meta_json",
        ),
    ),
    _seed(
        "core",
        "核心对话",
        "core.memory",
        "session_summary_jobs",
        "滚动摘要异步生成任务。",
        preview_only_columns=("source_turn_ids_json", "error", "meta_json"),
    ),
    _seed("persona", "画像与记忆", "core.persona", "personas", "用户画像。"),
    _seed(
        "persona",
        "画像与记忆",
        "core.persona",
        "persona_facts",
        "用户画像事实与聚类数据。",
        preview_only_columns=(
            "content",
            "source_log_ids",
            "evidence_log_ids_json",
            "candidate_meta_json",
        ),
    ),
    _seed(
        "persona",
        "画像与记忆",
        "core.persona",
        "persona_behaviors",
        "用户行为模式候选。",
        preview_only_columns=("pattern", "source_log_ids", "archive_meta_json"),
    ),
    _seed(
        "persona",
        "画像与记忆",
        "core.group_memory",
        "group_memories",
        "群体长期记忆。",
        hidden_columns=(
            "approval_source",
            "governance_mode",
            "approved_content_hash",
            "model_review_run_id",
            "model_contract_version",
            "human_reviewer_id",
            "human_reviewed_at",
            "human_action",
            "conflict_group_id",
            "version",
        ),
        preview_only_columns=("content", "evidence_log_ids_json", "meta_json"),
    ),
    _seed(
        "persona",
        "画像与记忆",
        "core.group_memory",
        "expression_memories",
        "旧表达记忆兼容数据。",
        preview_only_columns=("example_json",),
    ),
    _seed(
        "persona",
        "画像与记忆",
        "core.group_memory",
        "jargon_memories",
        "旧黑话记忆兼容数据。",
        preview_only_columns=("meaning", "examples_json"),
    ),
    _seed(
        "persona",
        "画像与记忆",
        "core.sticker",
        "sticker_memories",
        "表情包记忆。",
        hidden_columns=("local_path",),
        preview_only_columns=(
            "description",
            "tags_json",
            "emotions_json",
            "meta_json",
        ),
    ),
    _seed(
        "rag",
        "向量与知识库",
        "core.semantic",
        "semantic_index_items",
        "统一语义索引条目。",
        preview_only_columns=(
            "text",
            "lexical_text",
            "embedding_text",
            "meta_json",
        ),
    ),
    _seed(
        "rag",
        "向量与知识库",
        "core.semantic",
        "semantic_index_jobs",
        "语义索引异步任务。",
        preview_only_columns=("error", "meta_json"),
    ),
    _seed(
        "rag",
        "向量与知识库",
        "core.knowledge",
        "knowledge_sources",
        "外部知识来源。",
        preview_only_columns=("meta_json",),
    ),
    _seed(
        "rag",
        "向量与知识库",
        "core.knowledge",
        "knowledge_documents",
        "知识库文档和 ai_daily 入库记录。",
        preview_only_columns=("summary", "meta_json"),
    ),
    _seed(
        "rag",
        "向量与知识库",
        "core.knowledge",
        "knowledge_chunks",
        "知识库文档 chunk。",
        preview_only_columns=("text", "citation_json", "meta_json"),
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.tracing",
        "agent_runs",
        "一次模型或 Agent 处理请求。",
        sort_column="started_at",
        preview_only_columns=(
            "input_preview",
            "output_preview",
            "error",
            "meta_json",
        ),
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.tracing",
        "tool_calls",
        "工具调用记录。",
        sort_column="started_at",
        preview_only_columns=("args_json", "result_preview", "error"),
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.tracing",
        "llm_api_request_logs",
        "模型网关请求日志；只展示受限预览。",
        hidden_columns=(
            "headers_json",
            "request_json",
            "response_json",
            "message_sources_json",
        ),
        preview_only_columns=(
            "request_preview",
            "response_preview",
            "error",
            "request_lint_json",
            "actual_sent_tools_json",
            "runtime_enabled_tools_json",
            "runtime_disabled_tools_json",
            "framework_injected_tools_json",
        ),
        max_text_length=600,
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.tooling",
        "runtime_tool_decisions",
        "每轮运行时工具决策。",
        preview_only_columns=(
            "enabled_tools_json",
            "disabled_tools_json",
            "disabled_reasons_json",
            "effective_tools_json",
        ),
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.tooling",
        "tool_overrides",
        "工具权限覆盖。",
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.prompt_v2",
        "prompt_render_logs",
        "Prompt Runtime 渲染记录。",
        preview_only_columns=(
            "variables_json",
            "rendered_preview",
            "warnings_json",
            "error",
        ),
        max_text_length=600,
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.prompt_v2",
        "prompt_file_versions",
        "Prompt 文件版本。",
    ),
    _seed(
        "runtime",
        "LLM 与 Agent 调试",
        "core.reply",
        "reply_contract_check_logs",
        "reply/no_reply 合同审核日志。",
        preview_only_columns=("raw_output_preview",),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.chat",
        "chat_stream_configs",
        "会话级配置。",
        preview_only_columns=("session_guidance", "meta_json"),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.prompt_v2",
        "system_prompts",
        "用户级系统提示词。",
        preview_only_columns=("prompt_text",),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.scheduling",
        "scheduled_tasks",
        "定时任务。",
        preview_only_columns=("prompt_template", "last_error_summary"),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.admin",
        "admin_audit_logs",
        "管理员审计记录。",
        preview_only_columns=("detail_json",),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.admin",
        "admin_audit_outbox",
        "跨存储治理操作的持久审计意图。",
        preview_only_columns=(
            "request_detail_json",
            "result_detail_json",
        ),
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.policy",
        "user_block_rules",
        "用户屏蔽规则。",
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.policy",
        "content_block_rules",
        "内容屏蔽规则。",
    ),
    _seed(
        "rules",
        "配置与规则",
        "core.settings",
        "system_settings",
        "系统设置；值字段统一脱敏。",
        redact_columns=("value",),
    ),
)


def _require_sql_identifier(value: str, *, field_name: str) -> str:
    if _SQL_IDENTIFIER_PATTERN.fullmatch(str(value or "")) is None:
        raise ValueError(f"{field_name} 不是合法 SQL 标识符")
    return value


def _require_unique(values: tuple[str, ...], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} 不能包含重复项")


def _column_value_type(column: Any) -> AdminFilterValueType:
    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        return "string"
    if python_type is bool:
        return "boolean"
    if python_type is int:
        return "integer"
    if python_type is float:
        return "number"
    if python_type in {datetime, date}:
        return "datetime"
    return "string"


def _descriptor_from_seed(seed: _ViewSeed) -> AdminTableViewDescriptor:
    table = Base.metadata.tables.get(seed.table_name)
    if table is None:
        raise RuntimeError(
            f"Admin table view 缺少代码侧表模型: {seed.table_name}"
        )
    hidden = set(seed.hidden_columns)
    allowed_columns = tuple(
        column.name for column in table.columns if column.name not in hidden
    )
    primary_keys = tuple(column.name for column in table.primary_key.columns)
    sort_column = seed.sort_column
    if sort_column is None:
        sort_column = (
            "id"
            if "id" in allowed_columns
            else primary_keys[0]
            if primary_keys
            else allowed_columns[0]
        )
    tie_breaker = next(
        (
            column
            for column in primary_keys
            if column != sort_column and column in allowed_columns
        ),
        None,
    )
    filters = tuple(
        AdminTableFilterDescriptor(
            filter_id=column.name,
            column=column.name,
            value_type=_column_value_type(column),
        )
        for column in table.columns
        if (
            column.name in allowed_columns
            and column.name in _FILTERABLE_COLUMNS
        )
    )
    return AdminTableViewDescriptor(
        registry_id=seed.table_name,
        owner=seed.owner,
        group_id=seed.group_id,
        group_label=seed.group_label,
        table_name=seed.table_name,
        description=seed.description,
        allowed_columns=allowed_columns,
        default_sort=AdminTableSortDescriptor(
            column=sort_column,
            direction="desc",
            tie_breaker=tie_breaker,
        ),
        filters=filters,
        redact_columns=tuple(
            column
            for column in seed.redact_columns
            if column in allowed_columns
        ),
        preview_only_columns=tuple(
            column
            for column in seed.preview_only_columns
            if column in allowed_columns
        ),
        max_text_length=seed.max_text_length,
    )


def _build_registry() -> RegistrySnapshot[AdminTableViewDescriptor]:
    generation = RegistryGeneration[AdminTableViewDescriptor](
        "admin_table_view"
    )

    def configure(
        builder: RegistryBuilder[AdminTableViewDescriptor],
    ) -> None:
        for seed in _VIEW_SEEDS:
            builder.register(_descriptor_from_seed(seed))

    return generation.rebuild(configure)


ADMIN_TABLE_VIEW_REGISTRY = _build_registry()


def _quote_identifier(identifier: str) -> str:
    return f'"{_require_sql_identifier(identifier, field_name="identifier")}"'


def _normalize_filter_value(
    descriptor: AdminTableFilterDescriptor,
    value: object,
) -> object:
    if value is None:
        return None
    value_type = descriptor.value_type
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "1"}:
            return True
        if isinstance(value, str) and value.lower() in {"false", "0"}:
            return False
        raise AdminTableViewFilterError(
            f"过滤器 {descriptor.filter_id} 需要布尔值"
        )
    if value_type == "integer":
        if isinstance(value, bool):
            raise AdminTableViewFilterError(
                f"过滤器 {descriptor.filter_id} 需要整数"
            )
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise AdminTableViewFilterError(
                f"过滤器 {descriptor.filter_id} 需要整数"
            ) from exc
    if value_type == "number":
        if isinstance(value, bool):
            raise AdminTableViewFilterError(
                f"过滤器 {descriptor.filter_id} 需要数值"
            )
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise AdminTableViewFilterError(
                f"过滤器 {descriptor.filter_id} 需要数值"
            ) from exc
    if not isinstance(value, str):
        raise AdminTableViewFilterError(
            f"过滤器 {descriptor.filter_id} 需要字符串"
        )
    if len(value) > 512:
        raise AdminTableViewFilterError(
            f"过滤器 {descriptor.filter_id} 超过长度上限"
        )
    return value


def _filters_sha256(filters: Mapping[str, object]) -> str:
    return hashlib.sha256(
        canonical_json(dict(filters)).encode("utf-8")
    ).hexdigest()


def _encode_cursor(
    *,
    view_id: str,
    offset: int,
    filters_sha256: str,
) -> str:
    payload = canonical_json({
        "version": 1,
        "view_id": view_id,
        "offset": offset,
        "filters_sha256": filters_sha256,
    }).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(
    cursor: str | None,
    *,
    view_id: str,
    filters_sha256: str,
) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor:
        raise AdminTableViewCursorError("cursor 必须是非空字符串")
    if len(cursor) > _MAX_CURSOR_LENGTH:
        raise AdminTableViewCursorError("cursor 超过长度上限")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdminTableViewCursorError("cursor 格式无效") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "view_id",
        "offset",
        "filters_sha256",
    }:
        raise AdminTableViewCursorError("cursor payload 无效")
    offset = payload.get("offset")
    if (
        payload.get("version") != 1
        or payload.get("view_id") != view_id
        or payload.get("filters_sha256") != filters_sha256
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= _MAX_CURSOR_OFFSET
    ):
        raise AdminTableViewCursorError("cursor 与当前查询不匹配")
    return offset


def _serialize_cell(
    value: Any,
    *,
    descriptor: AdminTableViewDescriptor,
    column: str,
) -> tuple[object, dict[str, object]]:
    meta: dict[str, object] = {
        "kind": "null" if value is None else "value",
        "truncated": False,
        "full_length": None,
        "redacted": False,
    }
    if column in descriptor.redact_columns:
        meta.update({"kind": "redacted", "redacted": True})
        return "<redacted>", meta
    if value is None:
        return None, meta
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        size = len(value)
        meta.update({
            "kind": "binary",
            "truncated": True,
            "full_length": size,
        })
        return f"<binary {size} bytes>", meta
    display: object
    if isinstance(value, (str, int, float, bool)):
        display = value
    elif isinstance(value, (datetime, date)):
        display = value.isoformat()
    else:
        display = str(value)
    if isinstance(display, str):
        max_length = descriptor.max_text_length
        if column in descriptor.preview_only_columns:
            max_length = min(max_length, 300)
        meta.update({
            "kind": "text",
            "full_length": len(display),
        })
        if len(display) > max_length:
            meta["truncated"] = True
            return display[:max_length] + "...", meta
    return display, meta


class AdminTableViewService:
    """仅执行 Registry 已声明的固定投影、等值过滤和默认排序。"""

    def __init__(
        self,
        registry: RegistrySnapshot[AdminTableViewDescriptor],
    ) -> None:
        self.registry = registry

    def available_descriptors(
        self,
        db: Session,
    ) -> tuple[AdminTableViewDescriptor, ...]:
        bind = db.get_bind()
        inspector = inspect(bind)
        return tuple(
            descriptor
            for descriptor in self.registry
            if inspector.has_table(descriptor.table_name)
        )

    def query(
        self,
        db: Session,
        *,
        view_id: str,
        filters: Mapping[str, object],
        cursor: str | None,
        limit: int,
    ) -> AdminTableRows:
        descriptor = self.registry.get(view_id)
        if descriptor is None or descriptor.lifecycle != "active":
            raise AdminTableViewNotFoundError("视图不存在或不可用")
        if not inspect(db.get_bind()).has_table(descriptor.table_name):
            raise AdminTableViewUnavailableError("视图数据表尚未就绪")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= descriptor.max_limit
        ):
            raise AdminTableViewLimitError(
                f"limit 必须位于 1..{descriptor.max_limit}"
            )

        filter_by_id = descriptor.filter_by_id()
        normalized_filters: dict[str, object] = {}
        clauses: list[str] = []
        params: dict[str, object] = {}
        for index, filter_id in enumerate(sorted(filters)):
            filter_descriptor = filter_by_id.get(filter_id)
            if filter_descriptor is None:
                raise AdminTableViewFilterError(
                    f"未登记过滤器: {filter_id}"
                )
            value = _normalize_filter_value(
                filter_descriptor,
                filters[filter_id],
            )
            normalized_filters[filter_id] = value
            column = _quote_identifier(filter_descriptor.column)
            if value is None:
                clauses.append(f"{column} IS NULL")
                continue
            parameter = f"filter_{index}"
            clauses.append(f"{column} = :{parameter}")
            params[parameter] = value

        filters_sha256 = _filters_sha256(normalized_filters)
        offset = _decode_cursor(
            cursor,
            view_id=view_id,
            filters_sha256=filters_sha256,
        )
        where_sql = (
            " WHERE " + " AND ".join(clauses)
            if clauses
            else ""
        )
        table_sql = _quote_identifier(descriptor.table_name)
        columns_sql = ", ".join(
            _quote_identifier(column)
            for column in descriptor.allowed_columns
        )
        sort = descriptor.default_sort
        order_parts = [
            f"{_quote_identifier(sort.column)} {sort.direction.upper()}"
        ]
        if sort.tie_breaker is not None:
            order_parts.append(
                f"{_quote_identifier(sort.tie_breaker)} "
                f"{sort.direction.upper()}"
            )
        params.update({"limit": limit, "offset": offset})
        result = db.execute(
            text(
                f"SELECT {columns_sql} FROM {table_sql}"
                f"{where_sql} ORDER BY {', '.join(order_parts)} "
                "LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        columns = tuple(str(item) for item in result.keys())
        rows: list[dict[str, object]] = []
        cell_meta: list[dict[str, dict[str, object]]] = []
        for raw_row in result.fetchall():
            row: dict[str, object] = {}
            row_meta: dict[str, dict[str, object]] = {}
            for column, value in zip(columns, raw_row):
                display, meta = _serialize_cell(
                    value,
                    descriptor=descriptor,
                    column=column,
                )
                row[column] = display
                row_meta[column] = meta
            rows.append(row)
            cell_meta.append(row_meta)

        count_params = {
            key: value
            for key, value in params.items()
            if key not in {"limit", "offset"}
        }
        total = int(
            db.execute(
                text(
                    f"SELECT COUNT(*) FROM {table_sql}{where_sql}"
                ),
                count_params,
            ).scalar()
            or 0
        )
        next_offset = offset + len(rows)
        has_next = next_offset < total
        return AdminTableRows(
            view_id=view_id,
            total=total,
            limit=limit,
            has_next=has_next,
            next_cursor=(
                _encode_cursor(
                    view_id=view_id,
                    offset=next_offset,
                    filters_sha256=filters_sha256,
                )
                if has_next
                else None
            ),
            columns=columns,
            rows=tuple(rows),
            cell_meta=tuple(cell_meta),
        )
