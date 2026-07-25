"""构造 Web 预览用的 OpenAI-compatible tools schema。"""

from __future__ import annotations

import copy
import importlib as importlib
import json
import logging
from types import MappingProxyType
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from core.group_learning.aspects import (
    GROUP_ANALYSIS_ASPECT_REGISTRY,
)
from core.tool_contracts.ai_daily import ai_daily_parameters_schema
from core.tool_registration import (
    TOOL_REGISTRATION_REGISTRY,
    ToolSchemaProviderPort,
    get_tool_registration,
    list_user_tool_registrations,
)
from core.tool_registry import get_tool_descriptor

logger = logging.getLogger("nanobot.tool_schema")

TOOL_SCHEMA_OVERRIDE_PREFIX = "tool.schema_override."

STATIC_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "sql_analysis": {
        "description": (
            "只读 SQL 分析工具，用于用户明确要求查询数据库、审计数据、检查表结构或调试 SQL 时使用。"
            "也用于查询聊天记录、上一句、刚才说过什么、历史发言、会话日志。"
            "不要将本工具作为业务工具的前置步骤。"
            "如果用户要分析群聊、生成群日报、总结某个群的消息，应直接调用 group_analysis，"
            "不要先用 SQL 查询群号、User 表或 ChatLog。"
            "可查询 SQLite 表：chat_logs 原始消息档案；conversation_turns 精简对话上下文；"
            "users 用户/群聊；personas 用户画像。SELECT/WITH 必须包含 LIMIT，禁止 SELECT *。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "要执行的只读 SQL 查询语句。必须是单条 SELECT/CTE 或只读 PRAGMA；"
                        "SELECT/WITH 必须包含 LIMIT；禁止 SELECT *；普通查询≤1000，"
                        "聚合查询≤5000，原文内容字段≤500。"
                    ),
                }
            },
            "required": ["sql"],
        },
    },
    "python_sandbox": {
        "description": (
            "当前部署已硬禁用任意 Python 执行，本工具不会进入真实请求。"
            "历史查询和有界统计应使用只读 sql_analysis。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "要执行的 Python 数据分析脚本。仅用于复杂统计/清洗/聚合；"
                        "简单聊天记录查询、上一句、表结构检查请改用 sql_analysis。"
                    ),
                }
            },
            "required": ["code"],
        },
    },
    "ai_daily": {
        "description": "聚合 AI/科技领域可信来源，生成可直接发送的 AI 日报或资讯简报 HTML。",
        "parameters": ai_daily_parameters_schema(),
    },
    "memory_query": {
        "description": (
            "查询已生成的长期/中期聊天摘要和召回卡片。"
            "只返回结构化摘要、预览和展开摘要，不返回原始 ChatLog 全文。"
            "它只覆盖已经被摘要过的历史；当前短期窗口或未摘要消息必须用 sql_analysis 查询原始日志。"
            "当用户问较早之前讨论过什么、某天聊过什么、或需要从摘要继续展开时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["search", "time", "expand", "aggregate"],
                    "description": "search=关键词检索；time=按时间列出；expand=按 digest_id 展开；aggregate=聚合预览。",
                },
                "source": {
                    "type": "string",
                    "enum": ["digest", "session_summary", "all"],
                    "description": "digest=跨天/中期摘要；session_summary=当前 session rolling summary；all=两类摘要统一 RAG 搜索。默认 digest。",
                },
                "query": {"type": "string", "description": "关键词。search 模式必填。"},
                "session_id": {"type": "string", "description": "会话 ID，例如 group_1097666427 或 private_0000000000。"},
                "user_id": {"type": "string", "description": "用户或群实体 ID；不确定时优先传 session_id。"},
                "digest_id": {"type": "integer", "description": "expand 模式要展开的摘要 ID。"},
                "summary_id": {"type": "integer", "description": "source=session_summary 时 expand 要展开的 rolling summary ID。"},
                "digest_date": {"type": "string", "description": "指定 YYYY-MM-DD 日期。"},
                "date_start": {"type": "string", "description": "范围开始日期，YYYY-MM-DD。"},
                "date_end": {"type": "string", "description": "范围结束日期，YYYY-MM-DD。"},
                "limit": {"type": "integer", "description": "返回条数，默认 5，最大 10。", "minimum": 1, "maximum": 10},
                "include_detail": {"type": "boolean", "description": "是否包含详细摘要层。默认 false；不会包含原始 ChatLog。"},
                "include_legacy": {"type": "boolean", "description": "是否包含旧格式摘要。默认 false。"},
            },
            "required": ["mode"],
        },
    },
    "knowledge_query": {
        "description": (
            "查询已入库的外部知识库，只返回带 citation 的结果。"
            "适合查询手工文档、已保存 URL 元数据和历史日报摘要；今天/刚刚/实时资讯仍优先用 ai_daily。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["search", "expand"], "description": "search=按关键词检索；expand=按 document_id + chunk_id 展开单个 chunk。"},
                "query": {"type": "string", "description": "检索关键词，search 模式必填。"},
                "document_id": {"type": "integer", "description": "expand 模式要展开的文档 ID。"},
                "chunk_id": {"type": "string", "description": "expand 模式要展开的 chunk_id。"},
                "limit": {"type": "integer", "description": "返回条数，默认 5，最大 10。", "minimum": 1, "maximum": 10},
                "min_trust_level": {"type": "string", "enum": ["low", "medium", "high"], "description": "最低 trust_level，默认 low。"},
                "source_type": {"type": "string", "description": "按知识来源类型过滤，如 ai_daily、manual_markdown、manual_file。"},
                "domain": {"type": "string", "description": "按资料域名过滤，如 openai.com。"},
                "date_start": {"type": "string", "description": "资料发布时间开始日期，YYYY-MM-DD；等价于 published_after。"},
                "date_end": {"type": "string", "description": "资料发布时间结束日期，YYYY-MM-DD；等价于 published_before。"},
                "published_after": {"type": "string", "description": "仅返回此日期之后的资料，YYYY-MM-DD。"},
                "published_before": {"type": "string", "description": "仅返回此日期之前的资料，YYYY-MM-DD。"},
            },
            "required": ["mode"],
        },
    },
    "web_search": {
        "description": (
            "使用管理后台配置的 Web Search provider 执行通用网页搜索，返回标题、URL 和摘要。"
            "系统会按后台启用顺序自动 fallback，并在第一个相关结果停止。"
            "适合查询最新网页资料、官方文档、公告、产品信息和需要外部来源的问题。"
            "不要把搜索结果当作已验证事实；需要回答时应基于返回 URL 和摘要谨慎归纳。"
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词。应包含关键实体、限定词或时间范围，避免只传一个模糊词。",
                    "maxLength": 1000,
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 5，最大 10。",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    "image_summary": {
        "description": (
            "生成图片摘要并输出结构化 JSON。当你需要 OCR、细节归档、版面分析或多图整理时使用。"
            "files 必须是 http(s) URL 或 data:image/... base64。不支持 QQ/OneBot file_id、CQ码、裸文件名、相对路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "图片 URL 列表。必须是 http(s) URL 或 data:image/... base64。不支持 QQ/OneBot file_id、CQ码、裸文件名、相对路径。",
                    "minItems": 1,
                },
                "focus": {"type": "string", "description": "可选，摘要重点，如 OCR、人物、场景、风险、表格"},
            },
            "required": ["files"],
        },
    },
    "image_generation": {
        "description": (
            "生成图片并返回可放进 reply(content) 的短 token。"
            "仅当用户明确要求画图、生成图片、做贴纸/头像/插画等新图片时使用。"
            "识别或解释已有图片时使用 image_summary，不要用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "图片生成提示词。保留用户要求的主体、风格、构图、文字和约束。",
                    "minLength": 1,
                    "maxLength": 4000,
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x1536", "1536x1024", "auto"],
                    "default": "1024x1024",
                    "description": "输出尺寸，默认 1024x1024。",
                },
                "quality": {
                    "type": "string",
                    "enum": ["auto", "high", "low", "medium"],
                    "default": "high",
                    "description": "图片质量，默认 high。",
                },
                "background": {
                    "type": "string",
                    "enum": ["auto", "opaque", "transparent"],
                    "default": "auto",
                    "description": "背景策略，默认 auto。",
                },
            },
            "required": ["prompt"],
        },
    },
    "persona_update": {
        "description": (
            "基于已持久化聊天日志刷新当前用户的整体画像。schema 无参数，"
            "不支持定点纠正、删除或重建具体画像事实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "schedule_task": {
        "description": (
            "管理定时推送任务。支持创建、查看、修改、启停、立即执行和删除。"
            "用户说'每天X点推送Y'时创建，'看看定时任务'时列出，'停掉XX任务'时禁用，"
            "'现在执行XX任务'时立即运行。cron 按 Asia/Shanghai 解释，格式为'分 时 日 月 周'。"
            "创建任务时如果用户没有明确目标会话，可使用当前 runtime_context 对应的私聊或群聊。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "create(创建) | list(列出) | update(修改) | toggle(启停) | run(立即执行) | delete(删除)",
                    "enum": ["create", "list", "update", "toggle", "run", "delete"],
                },
                "task_id": {"type": "integer", "description": "任务ID（update/toggle/delete 必填）"},
                "name": {"type": "string", "description": "任务名（create/update）"},
                "cron_expr": {"type": "string", "description": "cron 表达式（create/update），Asia/Shanghai 时区，格式'分 时 日 月 周'，如每天9点为 0 9 * * *"},
                "target_type": {"type": "string", "description": "推送类型: private 或 group；创建时留空则尝试使用当前会话类型", "enum": ["private", "group"]},
                "target_id": {"type": "string", "description": "QQ号或群号；创建时留空则尝试使用当前 runtime_context 的 user_id/group_id"},
                "prompt_template": {"type": "string", "description": "LLM 生成推送内容的提示模板，不是直接发送的固定文本"},
                "idempotency_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "description": "手动 run 必填；同一请求重试必须复用同一幂等键",
                },
            },
            "required": ["action"],
        },
    },
    "group_analysis": {
        "description": (
            "分析群聊消息并生成群日报。提取话题总结、活跃用户称号、金句和氛围。"
            "当用户要求总结群聊、分析群消息、生成群日报、查看某群近期讨论时使用。"
            "group_id 是被分析的群，可以直接传群号、group_前缀ID、session_id、stream_id或群名；"
            "用户说这个群/本群时才使用当前会话的 group_id。不要为了查找群号而先调用 sql_analysis。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "被分析群的群号、group_前缀ID、session_id、stream_id或群名；只知道群名时也直接传群名，不要先调用 sql_analysis"},
                "instructions": {"type": "string", "description": "可选的分析指引"},
                "window_hours": {"type": "integer", "description": "可选分析时间窗口，默认24小时；传0表示不限制历史范围", "default": 24, "minimum": 0, "maximum": 720},
                "aspects": {
                    "type": "array",
                    "description": (
                        "可选分析方面。省略时兼容生成话题、称号、金句和质量报告；"
                        "显式传入时只执行被选择的方面。"
                    ),
                    "items": {
                        "type": "string",
                        "enum": list(
                            GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids
                        ),
                    },
                    "minItems": 1,
                    "maxItems": len(
                        GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids
                    ),
                    "uniqueItems": True,
                },
            },
            "required": ["group_id"],
        },
    },
    "sticker_search": {
        "description": (
            "搜索当前群或全局表情包。当群聊在斗图、玩梗、发纯表情，或用户明确要表情包时使用。"
            "不要频繁发表情包；不确定是否合适时直接文字回复。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "表情包关键词、情绪或使用场景，如 震惊、拍桌、生气、疑惑"},
                "group_id": {"type": "string", "description": "当前群号，优先来自 runtime_context.group_id"},
                "limit": {"type": "integer", "description": "返回数量，默认 3，最大 8", "minimum": 1, "maximum": 8},
                "include_global": {"type": "boolean", "description": "是否同时搜索全局表情包，默认 true"},
            },
            "required": ["query"],
        },
    },
    "reply": {
        "description": "生成最终用户可见回复。调用后系统会把你的回复发送给用户。只调用一次，调用后不需要再输出任何文本。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "发送给用户的回复内容"},
                "reply_to_message_id": {"type": "string", "description": "（可选）要引用的消息 ID"},
                "mentions": {"type": "array", "items": {"type": "string"}, "description": "（可选）要 @ 的用户 QQ 号列表"},
                "quote": {"type": "boolean", "description": "（可选）是否引用被回复消息原文"},
                "at_sender": {"type": "boolean", "description": "（可选）是否 @ 当前消息发送者"},
                "send_mode": {"type": "string", "enum": ["normal", "quote", "mention", "quote_and_mention"], "description": "normal/quote/mention/quote_and_mention"},
            },
            "required": ["content"],
        },
    },
    "no_reply": {
        "description": (
            "主动决定不回复当前消息。当群聊内容不需要 bot 参与（闲聊、语气词、签到打卡、bot 未被点名等），"
            "调用此工具。调用后不会发送任何消息。和 reply() 互斥——每轮只调用其中一个。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "不回复的原因（内部日志用，不会发送给用户）"},
            },
            "required": ["reason"],
        },
    },
    "memory_read": {
        "description": "读取长期记忆/上下文，不用于查询 chat_logs 或 conversation_turns。",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要交给 memory_read subagent 执行的检索任务。"},
            },
            "required": ["task"],
        },
    },
    "memory_write": {
        "description": "写入长期记忆/上下文，不用于保存普通聊天日志。",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "要交给 memory_write subagent 执行的写入任务。"},
            },
            "required": ["task"],
        },
    },
    "sandbox_exec": {
        "description": "在固定镜像的一次性断网容器中执行命令，只能访问当前 Workspace 和已授权输入资产。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 16384,
                    "description": "在容器内部通过 /bin/sh -lc 执行的命令。",
                },
                "cwd": {
                    "type": "string",
                    "maxLength": 4096,
                    "description": "可选的 Workspace 相对工作目录。",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "description": "可选超时；只能申请不高于服务端上限的值。",
                },
            },
            "required": ["command"],
        },
    },
    "workspace_list": {
        "description": "分页列出当前持久 Workspace 中的文件和目录元数据。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "maxLength": 4096, "description": "Workspace 相对目录；空字符串表示根目录。"},
                "cursor": {"type": "string", "maxLength": 64, "description": "上一页返回的分页游标。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
            },
            "required": [],
        },
    },
    "workspace_read": {
        "description": "有界读取当前持久 Workspace 中的文本文件；二进制文件只返回元数据。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Workspace 相对文件路径。"},
                "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "按字节计的起始偏移。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 262144, "default": 65536, "description": "本次最多读取的字节数。"},
            },
            "required": ["path"],
        },
    },
    "workspace_search": {
        "description": "在当前持久 Workspace 中执行有界字面量搜索，不接受任意正则。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 1024, "description": "要查找的字面量文本。"},
                "path": {"type": "string", "maxLength": 4096, "description": "可选 Workspace 相对目录。"},
                "glob": {"type": "string", "maxLength": 512, "description": "可选的简单文件名 glob。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            "required": ["query"],
        },
    },
    "workspace_write": {
        "description": "向当前持久 Workspace 原子写入小文本；大文件必须走资产上传。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Workspace 相对文件路径。"},
                "content": {"type": "string", "maxLength": 262144, "description": "UTF-8 小文本内容。"},
                "overwrite": {"type": "boolean", "default": False, "description": "文件已存在时是否允许原子覆盖。"},
            },
            "required": ["path", "content", "overwrite"],
        },
    },
    "asset_import": {
        "description": "把当前附件引用或已经授权的 asset://sha256/... 链接到当前 Workspace。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "source_ref": {"type": "string", "minLength": 1, "maxLength": 1024, "description": "当前附件引用或已经授权的资产引用。"},
                "logical_name": {"type": "string", "minLength": 1, "maxLength": 512, "description": "可选的 Workspace 内逻辑文件名。"},
            },
            "required": ["source_ref"],
        },
    },
    "asset_publish": {
        "description": "把当前 Workspace 中的普通文件发布为不可变资产。",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Workspace 相对普通文件路径。"},
                "media_type": {"type": "string", "maxLength": 255, "default": "application/octet-stream", "description": "可选媒体类型。"},
            },
            "required": ["path"],
        },
    },
}


KT_BUILTIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "type": {"type": "string", "description": "Shell type (default: bash). Options: bash, zsh, sh, fish, pwsh"},
            "timeout": {"type": "number", "description": "Maximum execution time in seconds (0 = no timeout)."},
        },
        "required": ["command"],
    },
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "offset": {"type": "integer", "description": "Line offset (optional)"},
            "limit": {"type": "integer", "description": "Max lines (optional)"},
        },
        "required": ["path"],
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["path", "content"],
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old": {"type": "string", "description": "Exact text to find (search/replace mode)"},
            "new": {"type": "string", "description": "Replacement text (search/replace mode)"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
            "diff": {"type": "string", "description": "Unified diff content (diff mode, alternative to old/new)"},
        },
        "required": ["path"],
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
            "path": {"type": "string", "description": "Base directory (optional)"},
            "gitignore": {"type": "boolean", "description": "Follow .gitignore rules (default true)"},
        },
        "required": ["pattern"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search"},
            "path": {"type": "string", "description": "Directory or file to search"},
            "glob": {"type": "string", "description": "File glob filter (optional)"},
            "gitignore": {"type": "boolean", "description": "Follow .gitignore rules (default true)"},
        },
        "required": ["pattern"],
    },
}


def _background_schema() -> dict[str, str]:
    return {
        "type": "boolean",
        "description": "If true, run in background. Results delivered later, not immediately.",
    }


def _with_background_option(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    params = copy.deepcopy(parameters or {"type": "object", "properties": {}})
    if not isinstance(params.get("properties"), dict):
        params["properties"] = {}
    descriptor = get_tool_descriptor(tool_name)
    if descriptor is None or descriptor.supports_background:
        params["properties"]["run_in_background"] = _background_schema()
    else:
        params["properties"].pop("run_in_background", None)
        required = params.get("required")
        if isinstance(required, list):
            params["required"] = [item for item in required if item != "run_in_background"]
    return params


def _static_tool_schema(name: str) -> dict[str, Any] | None:
    spec = STATIC_TOOL_SCHEMAS.get(name)
    if not spec:
        return None
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(spec.get("description") or ""),
            "parameters": _with_background_option(name, spec.get("parameters") or {}),
        },
        "source": "static",
    }


def _builtin_tool_schema(name: str) -> dict[str, Any] | None:
    if name not in KT_BUILTIN_SCHEMAS:
        return None
    descriptor = get_tool_descriptor(name)
    td = descriptor.definition if descriptor is not None else None
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": td.description if td else f"KT built-in tool: {name}",
            "parameters": _with_background_option(name, KT_BUILTIN_SCHEMAS[name]),
        },
        "source": "kt_builtin",
    }


class CanonicalToolSchemaProvider:
    """兼容现有静态／KT 内置 Schema 的代码侧 Provider。"""

    @property
    def provider_id(self) -> str:
        return "canonical"

    def build_schema(self, tool_name: str) -> dict[str, Any]:
        schema = (
            _static_tool_schema(tool_name)
            or _builtin_tool_schema(tool_name)
        )
        if schema is None:
            raise ValueError(
                f"tool {tool_name} 缺少 canonical schema"
            )
        return schema


_SCHEMA_PROVIDERS: dict[str, ToolSchemaProviderPort] = MappingProxyType({
    "canonical": CanonicalToolSchemaProvider(),
})


def _tool_schema_override_key(name: str) -> str:
    return f"{TOOL_SCHEMA_OVERRIDE_PREFIX}{str(name or '').strip()}"


def _validate_tool_schema(tool_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    name = str(tool_name or "").strip()
    if not name:
        raise ValueError("tool_name required")
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if schema.get("type") != "function":
        raise ValueError("schema.type must be function")
    function = schema.get("function")
    if not isinstance(function, dict):
        raise ValueError("schema.function must be an object")
    function_name = str(function.get("name") or "").strip()
    if function_name != name:
        raise ValueError(f"function.name must be {name}")
    parameters = function.get("parameters")
    if parameters is None:
        function["parameters"] = {"type": "object", "properties": {}}
    elif not isinstance(parameters, dict):
        raise ValueError("function.parameters must be an object")
    return copy.deepcopy(schema)


def load_tool_schema_override(db, name: str) -> dict[str, Any] | None:
    """读取工具 schema 运行时覆盖。"""
    if db is None:
        return None
    tool_name = str(name or "").strip()
    if not tool_name:
        return None
    try:
        from core.database import SystemSetting

        row = db.query(SystemSetting).filter(SystemSetting.key == _tool_schema_override_key(tool_name)).first()
        if not row or not row.value:
            return None
        parsed = json.loads(row.value)
        return _validate_tool_schema(tool_name, parsed)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid tool schema override JSON for %s: %s", tool_name, exc)
        return None
    except SQLAlchemyError as exc:
        logger.warning("Failed to load tool schema override for %s: %s", tool_name, exc)
        return None


def save_tool_schema_override(db, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """保存工具 schema 覆盖。调用方负责 commit。"""
    if db is None:
        raise ValueError("db required")
    tool_name = _ensure_known_tool(name)
    normalized = _validate_tool_schema(tool_name, schema)
    from core.database import SystemSetting

    value = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    key = _tool_schema_override_key(tool_name)
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row:
        row = SystemSetting(key=key, value=value, description=f"{tool_name} runtime tool schema override")
        db.add(row)
    else:
        row.value = value
        row.description = f"{tool_name} runtime tool schema override"
    return normalized


def delete_tool_schema_override(db, name: str) -> bool:
    if db is None:
        raise ValueError("db required")
    tool_name = _ensure_known_tool(name)
    from core.database import SystemSetting

    row = db.query(SystemSetting).filter(SystemSetting.key == _tool_schema_override_key(tool_name)).first()
    if not row:
        return False
    db.delete(row)
    return True


def _add_tool_metadata(schema: dict[str, Any], tool_name: str) -> dict[str, Any]:
    result = copy.deepcopy(schema or {})
    registration = get_tool_registration(tool_name)
    descriptor = (
        registration.descriptor
        if registration is not None
        else get_tool_descriptor(tool_name)
    )
    if (
        registration is not None
        and descriptor is not None
        and not descriptor.framework_owned
    ):
        td = descriptor.definition
        result["category"] = td.category
        result["risk_level"] = td.risk_level
        result["label"] = td.label
        snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
        result["registration"] = {
            "generation": snapshot.generation,
            "sha256": snapshot.sha256,
            "schema_provider_id": registration.schema_provider_id,
            "lifecycle": registration.lifecycle,
        }
    return result


def _base_tool_schema(name: str) -> dict[str, Any]:
    tool_name = str(name or "").strip()
    registration = get_tool_registration(tool_name)
    if registration is None or registration.descriptor.framework_owned:
        raise ValueError(f"unknown tool: {tool_name}")
    provider = _SCHEMA_PROVIDERS.get(registration.schema_provider_id)
    if provider is None:
        raise ValueError(
            f"tool {tool_name} schema provider 未登记: "
            f"{registration.schema_provider_id}"
        )
    schema = provider.build_schema(tool_name)
    return _validate_tool_schema(tool_name, schema)


def _ensure_known_tool(name: str) -> str:
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("tool_name required")
    registration = get_tool_registration(tool_name)
    if (
        registration is None
        or registration.descriptor.framework_owned
    ):
        raise ValueError(f"unknown tool: {tool_name}")
    return tool_name


def validate_registered_tool_schemas() -> None:
    """启动期验证每个用户工具都能由登记的 Provider 生成同名 Schema。"""

    for registration in list_user_tool_registrations():
        schema = _base_tool_schema(registration.name)
        function = schema.get("function")
        function_name = (
            str(function.get("name") or "").strip()
            if isinstance(function, dict)
            else ""
        )
        if function_name != registration.name:
            raise ValueError(
                f"tool {registration.name} schema name 漂移: "
                f"{function_name!r}"
            )


def build_tool_schema(name: str, *, db=None, include_template_overlay: bool = True) -> dict[str, Any]:
    """按工具名构造单个 OpenAI-compatible schema，供运行时和管理端展示。"""
    tool_name = _ensure_known_tool(name)
    override = load_tool_schema_override(db, tool_name)
    schema = override or _base_tool_schema(tool_name)
    if override:
        schema["source"] = "runtime_override"
    try:
        from core.prompt_v2.tool_templates import overlay_tool_schema_description

        if include_template_overlay:
            schema = overlay_tool_schema_description(schema)
    except Exception:
        pass
    function = schema.get("function") if isinstance(schema, dict) else None
    parameters = function.get("parameters") if isinstance(function, dict) else None
    descriptor = get_tool_descriptor(tool_name)
    if (
        isinstance(parameters, dict)
        and descriptor is not None
        and not descriptor.supports_background
    ):
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            properties.pop("run_in_background", None)
        required = parameters.get("required")
        if isinstance(required, list):
            parameters["required"] = [item for item in required if item != "run_in_background"]
    return _add_tool_metadata(schema, tool_name)


def build_tool_schema_config(db, name: str) -> dict[str, Any]:
    """返回 WebUI schema 编辑所需的默认、覆盖、可编辑和生效 schema。"""
    tool_name = _ensure_known_tool(name)
    override = load_tool_schema_override(db, tool_name)
    default_schema = _add_tool_metadata(_base_tool_schema(tool_name), tool_name)
    editable_schema = _add_tool_metadata(override or default_schema, tool_name)
    effective_schema = build_tool_schema(tool_name, db=db)
    registration = get_tool_registration(tool_name)
    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
    return {
        "tool": tool_name,
        "default_schema": default_schema,
        "override_schema": _add_tool_metadata(override, tool_name) if override else None,
        "editable_schema": editable_schema,
        "tool_schema": effective_schema,
        "override_present": override is not None,
        "registration": {
            "generation": snapshot.generation,
            "sha256": snapshot.sha256,
            "schema_provider_id": (
                registration.schema_provider_id
                if registration is not None
                else ""
            ),
            "lifecycle": (
                registration.lifecycle
                if registration is not None
                else ""
            ),
        },
    }


def build_effective_tool_schemas(enabled: dict[str, bool], *, db=None) -> list[dict[str, Any]]:
    """按当前启用工具构造预览 schema；memory subagent 保持元数据兜底。"""
    schemas: list[dict[str, Any]] = []
    for name in sorted(n for n, ok in (enabled or {}).items() if ok):
        registration = get_tool_registration(name)
        if (
            registration is None
            or registration.descriptor.framework_owned
            or registration.lifecycle != "active"
        ):
            logger.warning("Skip unknown runtime tool schema: %s", name)
            continue
        schemas.append(build_tool_schema(name, db=db))
    return schemas
