"""框架无关的类型化消息合同。"""

from foundation.message_contract.contracts import (
    AssetContent,
    AttachmentKind,
    ContentPart,
    FileContent,
    ForwardContent,
    GatewayMetadata,
    ImageContent,
    InboundMessageContract,
    MentionReference,
    MessageAction,
    MessageAttachment,
    MessageContractError,
    MessagePhase,
    MessageTrace,
    OutboundMessageContract,
    ReplyReference,
    RetractPolicy,
    TextContent,
    TextFormat,
    TransportError,
)
from foundation.message_contract.parsing import (
    content_part_to_payload,
    parse_content_part,
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
    "content_part_to_payload",
    "parse_content_part",
]
