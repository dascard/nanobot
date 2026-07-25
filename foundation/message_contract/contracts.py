"""跨 HTTP、Agent Runtime 与传输 Adapter 共用的消息值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import unicodedata

from foundation.identity import (
    ActorIdentity,
    ChatStreamIdentity,
    Principal,
    RecipientIdentity,
)


class MessageContractError(ValueError):
    """消息合同不满足稳定约束。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class MessageAction(str, Enum):
    REPLY = "reply"
    NO_REPLY = "no_reply"
    WAIT = "wait"
    SILENT = "silent"
    BLOCKED = "blocked"


class MessagePhase(str, Enum):
    PROGRESS = "progress"
    FINAL = "final"


class RetractPolicy(str, Enum):
    NONE = "none"
    REPLACE_ON_FINAL = "replace_on_final"
    RETRACT_ON_ERROR = "retract_on_error"


class TextFormat(str, Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"


class AttachmentKind(str, Enum):
    IMAGE = "image"
    FILE = "file"
    ASSET = "asset"


def _coerce_enum(value: object, enum_type: type[Enum], *, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise MessageContractError(
            f"invalid_{field_name}",
            f"{field_name} 不是受支持的值",
        ) from exc


def _string(
    value: object,
    *,
    field_name: str,
    required: bool = False,
    max_chars: int,
    allow_line_breaks: bool = False,
) -> str:
    if type(value) is not str:
        raise MessageContractError(
            f"invalid_{field_name}",
            f"{field_name} 必须是字符串",
        )
    if required and not value.strip():
        raise MessageContractError(
            f"invalid_{field_name}",
            f"{field_name} 不能为空",
        )
    if len(value) > max_chars:
        raise MessageContractError(
            f"{field_name}_too_long",
            f"{field_name} 长度超出限制",
        )
    allowed_controls = {"\t", "\n", "\r"} if allow_line_breaks else set()
    if any(
        (
            unicodedata.category(char) in {"Cc", "Cs"}
            and char not in allowed_controls
        )
        for char in value
    ):
        raise MessageContractError(
            f"invalid_{field_name}",
            f"{field_name} 包含非法控制字符",
        )
    return value


def _optional_size(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise MessageContractError(
            f"invalid_{field_name}",
            f"{field_name} 必须是非负整数或 null",
        )
    return value


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
    format: TextFormat = TextFormat.PLAIN

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "text",
            _string(
                self.text,
                field_name="content_text",
                required=True,
                max_chars=1_000_000,
                allow_line_breaks=True,
            ),
        )
        object.__setattr__(
            self,
            "format",
            _coerce_enum(
                self.format,
                TextFormat,
                field_name="text_format",
            ),
        )


@dataclass(frozen=True, slots=True)
class ImageContent:
    ref: str
    media_type: str = ""
    alt_text: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ref",
            _string(
                self.ref,
                field_name="image_ref",
                required=True,
                max_chars=4096,
            ),
        )
        object.__setattr__(
            self,
            "media_type",
            _string(
                self.media_type,
                field_name="image_media_type",
                max_chars=255,
            ),
        )
        object.__setattr__(
            self,
            "alt_text",
            _string(
                self.alt_text,
                field_name="image_alt_text",
                max_chars=1000,
                allow_line_breaks=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class FileContent:
    ref: str
    name: str = ""
    media_type: str = ""
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ref",
            _string(
                self.ref,
                field_name="file_ref",
                required=True,
                max_chars=4096,
            ),
        )
        object.__setattr__(
            self,
            "name",
            _string(
                self.name,
                field_name="file_name",
                max_chars=512,
            ),
        )
        object.__setattr__(
            self,
            "media_type",
            _string(
                self.media_type,
                field_name="file_media_type",
                max_chars=255,
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _optional_size(
                self.size_bytes,
                field_name="file_size_bytes",
            ),
        )


@dataclass(frozen=True, slots=True)
class AssetContent:
    ref: str
    name: str = ""
    media_type: str = ""
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ref",
            _string(
                self.ref,
                field_name="asset_ref",
                required=True,
                max_chars=4096,
            ),
        )
        object.__setattr__(
            self,
            "name",
            _string(
                self.name,
                field_name="asset_name",
                max_chars=512,
            ),
        )
        object.__setattr__(
            self,
            "media_type",
            _string(
                self.media_type,
                field_name="asset_media_type",
                max_chars=255,
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _optional_size(
                self.size_bytes,
                field_name="asset_size_bytes",
            ),
        )


@dataclass(frozen=True, slots=True)
class ForwardContent:
    ref: str
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ref",
            _string(
                self.ref,
                field_name="forward_ref",
                required=True,
                max_chars=512,
            ),
        )
        object.__setattr__(
            self,
            "summary",
            _string(
                self.summary,
                field_name="forward_summary",
                max_chars=1000,
                allow_line_breaks=True,
            ),
        )


ContentPart = (
    TextContent
    | ImageContent
    | FileContent
    | AssetContent
    | ForwardContent
)
_CONTENT_PART_TYPES = (
    TextContent,
    ImageContent,
    FileContent,
    AssetContent,
    ForwardContent,
)


@dataclass(frozen=True, slots=True)
class MessageAttachment:
    kind: AttachmentKind
    ref: str
    name: str = ""
    media_type: str = ""
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _coerce_enum(
                self.kind,
                AttachmentKind,
                field_name="attachment_kind",
            ),
        )
        object.__setattr__(
            self,
            "ref",
            _string(
                self.ref,
                field_name="attachment_ref",
                required=True,
                max_chars=4096,
            ),
        )
        object.__setattr__(
            self,
            "name",
            _string(
                self.name,
                field_name="attachment_name",
                max_chars=512,
            ),
        )
        object.__setattr__(
            self,
            "media_type",
            _string(
                self.media_type,
                field_name="attachment_media_type",
                max_chars=255,
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            _optional_size(
                self.size_bytes,
                field_name="attachment_size_bytes",
            ),
        )


@dataclass(frozen=True, slots=True)
class MentionReference:
    actor: ActorIdentity
    display_name: str = ""
    is_bot: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.actor, ActorIdentity):
            raise MessageContractError(
                "invalid_mention_actor",
                "mention.actor 必须是 ActorIdentity",
            )
        object.__setattr__(
            self,
            "display_name",
            _string(
                self.display_name,
                field_name="mention_display_name",
                max_chars=256,
            ),
        )
        if type(self.is_bot) is not bool:
            raise MessageContractError(
                "invalid_mention_is_bot",
                "mention.is_bot 必须是布尔值",
            )


@dataclass(frozen=True, slots=True)
class ReplyReference:
    message_id: str
    actor: ActorIdentity | None = None
    actor_name: str = ""
    text: str = ""
    is_bot: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message_id",
            _string(
                self.message_id,
                field_name="reply_message_id",
                required=True,
                max_chars=512,
            ),
        )
        if self.actor is not None and not isinstance(
            self.actor,
            ActorIdentity,
        ):
            raise MessageContractError(
                "invalid_reply_actor",
                "reply.actor 必须是 ActorIdentity 或 null",
            )
        object.__setattr__(
            self,
            "actor_name",
            _string(
                self.actor_name,
                field_name="reply_actor_name",
                max_chars=256,
            ),
        )
        object.__setattr__(
            self,
            "text",
            _string(
                self.text,
                field_name="reply_text",
                max_chars=10_000,
                allow_line_breaks=True,
            ),
        )
        if type(self.is_bot) is not bool:
            raise MessageContractError(
                "invalid_reply_is_bot",
                "reply.is_bot 必须是布尔值",
            )


@dataclass(frozen=True, slots=True)
class GatewayMetadata:
    source: str = ""
    session_name: str = ""
    self_id: str = ""
    bot_id: str = ""
    bot_name: str = ""
    bot_aliases: tuple[str, ...] = ()
    sender_is_bot: bool = False

    def __post_init__(self) -> None:
        for field_name, max_chars in (
            ("source", 128),
            ("session_name", 256),
            ("self_id", 512),
            ("bot_id", 512),
            ("bot_name", 256),
        ):
            object.__setattr__(
                self,
                field_name,
                _string(
                    getattr(self, field_name),
                    field_name=f"gateway_{field_name}",
                    max_chars=max_chars,
                ),
            )
        aliases = tuple(self.bot_aliases)
        if len(aliases) > 32:
            raise MessageContractError(
                "too_many_bot_aliases",
                "gateway.bot_aliases 数量超出限制",
            )
        object.__setattr__(
            self,
            "bot_aliases",
            tuple(
                _string(
                    alias,
                    field_name="gateway_bot_alias",
                    required=True,
                    max_chars=128,
                )
                for alias in aliases
            ),
        )
        if type(self.sender_is_bot) is not bool:
            raise MessageContractError(
                "invalid_gateway_sender_is_bot",
                "gateway.sender_is_bot 必须是布尔值",
            )


@dataclass(frozen=True, slots=True)
class MessageTrace:
    request_id: str = ""
    trace_id: str = ""
    correlation_id: str = ""
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "trace_id",
            "correlation_id",
            "idempotency_key",
        ):
            object.__setattr__(
                self,
                field_name,
                _string(
                    getattr(self, field_name),
                    field_name=f"trace_{field_name}",
                    max_chars=256,
                ),
            )


def _identity_platform(identity: object) -> str:
    platform = getattr(identity, "platform", "")
    return str(getattr(platform, "value", platform))


@dataclass(frozen=True, slots=True)
class InboundMessageContract:
    message_id: str
    chat_stream: ChatStreamIdentity
    actor: ActorIdentity
    recipient: RecipientIdentity
    principal: Principal
    text: str = ""
    parts: tuple[ContentPart, ...] = ()
    attachments: tuple[MessageAttachment, ...] = ()
    mentions: tuple[MentionReference, ...] = ()
    reply_to: ReplyReference | None = None
    gateway: GatewayMetadata = field(default_factory=GatewayMetadata)
    trace: MessageTrace = field(default_factory=MessageTrace)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MessageContractError(
                "unsupported_message_schema",
                "入站消息 schema_version 必须为 1",
            )
        object.__setattr__(
            self,
            "message_id",
            _string(
                self.message_id,
                field_name="message_id",
                max_chars=512,
            ),
        )
        if not isinstance(self.chat_stream, ChatStreamIdentity):
            raise MessageContractError(
                "invalid_chat_stream",
                "chat_stream 必须是 ChatStreamIdentity",
            )
        if not isinstance(self.actor, ActorIdentity):
            raise MessageContractError(
                "invalid_actor",
                "actor 必须是 ActorIdentity",
            )
        if not isinstance(self.recipient, RecipientIdentity):
            raise MessageContractError(
                "invalid_recipient",
                "recipient 必须是 RecipientIdentity",
            )
        if not isinstance(self.principal, Principal):
            raise MessageContractError(
                "invalid_principal",
                "principal 必须是 Principal",
            )
        object.__setattr__(
            self,
            "text",
            _string(
                self.text,
                field_name="message_text",
                max_chars=1_000_000,
                allow_line_breaks=True,
            ),
        )
        parts = tuple(self.parts)
        if any(not isinstance(part, _CONTENT_PART_TYPES) for part in parts):
            raise MessageContractError(
                "unsupported_content_part",
                "parts 包含未知内容类型",
            )
        object.__setattr__(self, "parts", parts)
        attachments = tuple(self.attachments)
        if any(
            not isinstance(item, MessageAttachment)
            for item in attachments
        ):
            raise MessageContractError(
                "invalid_attachment",
                "attachments 包含非法附件",
            )
        object.__setattr__(self, "attachments", attachments)
        mentions = tuple(self.mentions)
        if any(
            not isinstance(item, MentionReference)
            for item in mentions
        ):
            raise MessageContractError(
                "invalid_mention",
                "mentions 包含非法提及",
            )
        object.__setattr__(self, "mentions", mentions)
        if self.reply_to is not None and not isinstance(
            self.reply_to,
            ReplyReference,
        ):
            raise MessageContractError(
                "invalid_reply_reference",
                "reply_to 必须是 ReplyReference 或 null",
            )
        if not isinstance(self.gateway, GatewayMetadata):
            raise MessageContractError(
                "invalid_gateway_metadata",
                "gateway 必须是 GatewayMetadata",
            )
        if not isinstance(self.trace, MessageTrace):
            raise MessageContractError(
                "invalid_message_trace",
                "trace 必须是 MessageTrace",
            )
        self._validate_identity_scope()

    def _validate_identity_scope(self) -> None:
        platform = self.chat_stream.platform
        identity_platforms = {
            _identity_platform(self.actor),
            _identity_platform(self.recipient),
            _identity_platform(self.principal),
        }
        identity_platforms.update(
            _identity_platform(mention.actor)
            for mention in self.mentions
        )
        if self.reply_to is not None and self.reply_to.actor is not None:
            identity_platforms.add(
                _identity_platform(self.reply_to.actor)
            )
        if identity_platforms != {platform}:
            raise MessageContractError(
                "identity_platform_mismatch",
                "消息身份不属于同一 platform",
            )

        expected_owner_type = (
            "group" if self.chat_stream.chat_type == "group" else "user"
        )
        if (
            self.principal.owner_type != expected_owner_type
            or self.recipient.recipient_type != expected_owner_type
        ):
            raise MessageContractError(
                "identity_scope_mismatch",
                "principal 或 recipient 与 chat type 不一致",
            )


@dataclass(frozen=True, slots=True)
class TransportError:
    code: str
    retryable: bool
    safe_summary: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "code",
            _string(
                self.code,
                field_name="transport_error_code",
                required=True,
                max_chars=128,
            ),
        )
        if type(self.retryable) is not bool:
            raise MessageContractError(
                "invalid_transport_error_retryable",
                "transport_error.retryable 必须是布尔值",
            )
        object.__setattr__(
            self,
            "safe_summary",
            _string(
                self.safe_summary,
                field_name="transport_error_summary",
                required=True,
                max_chars=512,
                allow_line_breaks=True,
            ),
        )


@dataclass(frozen=True, slots=True)
class OutboundMessageContract:
    action: MessageAction
    recipient: RecipientIdentity
    parts: tuple[ContentPart, ...] = ()
    phase: MessagePhase = MessagePhase.FINAL
    retract_policy: RetractPolicy = RetractPolicy.NONE
    error: TransportError | None = None
    trace: MessageTrace = field(default_factory=MessageTrace)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MessageContractError(
                "unsupported_message_schema",
                "出站消息 schema_version 必须为 1",
            )
        object.__setattr__(
            self,
            "action",
            _coerce_enum(
                self.action,
                MessageAction,
                field_name="message_action",
            ),
        )
        if not isinstance(self.recipient, RecipientIdentity):
            raise MessageContractError(
                "invalid_recipient",
                "recipient 必须是 RecipientIdentity",
            )
        parts = tuple(self.parts)
        if any(not isinstance(part, _CONTENT_PART_TYPES) for part in parts):
            raise MessageContractError(
                "unsupported_content_part",
                "parts 包含未知内容类型",
            )
        object.__setattr__(self, "parts", parts)
        object.__setattr__(
            self,
            "phase",
            _coerce_enum(
                self.phase,
                MessagePhase,
                field_name="message_phase",
            ),
        )
        object.__setattr__(
            self,
            "retract_policy",
            _coerce_enum(
                self.retract_policy,
                RetractPolicy,
                field_name="retract_policy",
            ),
        )
        if self.error is not None and not isinstance(
            self.error,
            TransportError,
        ):
            raise MessageContractError(
                "invalid_transport_error",
                "error 必须是 TransportError 或 null",
            )
        if not isinstance(self.trace, MessageTrace):
            raise MessageContractError(
                "invalid_message_trace",
                "trace 必须是 MessageTrace",
            )
        self._validate_action()

    def _validate_action(self) -> None:
        if self.action is MessageAction.REPLY:
            if not self.parts:
                raise MessageContractError(
                    "missing_outbound_content",
                    "reply 必须包含至少一个内容段",
                )
        elif self.parts:
            raise MessageContractError(
                "unexpected_outbound_content",
                "非 reply 动作不能携带内容段",
            )

        if self.phase is MessagePhase.PROGRESS:
            if self.action is not MessageAction.REPLY:
                raise MessageContractError(
                    "invalid_progress_action",
                    "progress 只允许 reply 动作",
                )
            if self.retract_policy is RetractPolicy.NONE:
                raise MessageContractError(
                    "invalid_retract_policy",
                    "progress 必须声明最终替换或错误撤回策略",
                )
        elif self.retract_policy is not RetractPolicy.NONE:
            raise MessageContractError(
                "invalid_retract_policy",
                "final 消息不能声明 progress 撤回策略",
            )

        if self.error is not None and self.action is not MessageAction.BLOCKED:
            raise MessageContractError(
                "invalid_transport_error_action",
                "transport-neutral error 只能用于 blocked 动作",
            )

    @property
    def text(self) -> str:
        return "\n".join(
            part.text
            for part in self.parts
            if isinstance(part, TextContent)
        )


__all__ = [
    "AssetContent",
    "AttachmentKind",
    "ContentPart",
    "FileContent",
    "ForwardContent",
    "GatewayMetadata",
    "ImageContent",
    "InboundMessageContract",
    "MentionReference",
    "MessageAction",
    "MessageAttachment",
    "MessageContractError",
    "MessagePhase",
    "MessageTrace",
    "OutboundMessageContract",
    "ReplyReference",
    "RetractPolicy",
    "TextContent",
    "TextFormat",
    "TransportError",
]
