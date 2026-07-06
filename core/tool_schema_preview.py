"""构造 Web 预览用的 OpenAI-compatible tools schema。"""

from __future__ import annotations

import copy
import importlib as importlib
import json
import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from core.tool_registry import TOOL_METADATA, get_tool_def

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
            "在安全沙箱中执行 Python 数据分析脚本。用于 SQL 难以表达的统计/清洗/聚合逻辑，"
            "不是通用编程执行环境，也不是简单聊天记录查询的首选工具。"
            "查询上一句、历史发言、表结构或简单 SELECT 时先使用 sql_analysis；"
            "只有需要对 SQL 结果继续做复杂计算时才使用本工具。"
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
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "日报主题或自然语言请求；今天/最新类请求必须基于 runtime_context.current_time，不要自行编造年份。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "候选新闻数量（默认 8）；日报/最新资讯类请求会至少使用 8 条候选。",
                    "default": 8,
                },
                "freshness": {
                    "type": "string",
                    "description": "时效范围：today/latest/week/custom。今天、最新、日报、早报优先使用 today 或 latest。",
                    "enum": ["today", "latest", "week", "custom"],
                    "default": "latest",
                },
                "target_date": {
                    "type": "string",
                    "description": "目标日期，YYYY-MM-DD；仅用户明确指定日期时填写。",
                },
                "no_cache": {"type": "boolean", "description": "跳过缓存强制重新检索", "default": False},
                "refresh": {"type": "boolean", "description": "强制刷新", "default": False},
            },
            "required": ["query"],
        },
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
            "适合查询最新网页资料、官方文档、公告、产品信息和需要外部来源的问题。"
            "不要把搜索结果当作已验证事实；需要回答时应基于返回 URL 和摘要谨慎归纳。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词。应包含关键实体、限定词或时间范围，避免只传一个模糊词。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 5，最大 10。",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
                "provider": {
                    "type": "string",
                    "description": "可选 provider id，如 searxng、brave、serper、tavily、exa、firecrawl、linkup、you、jina、ddgs。留空则按已启用 provider 自动 fallback。",
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
            "按用户明确要求更新画像。仅当用户直接说“更新我的画像”“记住这点”“纠正/删除我的偏好”等画像维护请求时使用。"
            "普通聊天里出现的新信息不要主动调用本工具，后台画像进化链路会异步处理；也不要用它查询聊天记录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "要更新画像的用户 ID，优先使用 <runtime_context> 中的 user_id"},
                "instructions": {"type": "string", "description": "可选的画像维护指引，例如要记住、纠正或删除的具体偏好；留空则按最近日志全面更新"},
            },
            "required": ["user_id"],
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


def _with_background_option(parameters: dict[str, Any]) -> dict[str, Any]:
    params = copy.deepcopy(parameters or {"type": "object", "properties": {}})
    if not isinstance(params.get("properties"), dict):
        params["properties"] = {}
    params["properties"]["run_in_background"] = _background_schema()
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
            "parameters": _with_background_option(spec.get("parameters") or {}),
        },
        "source": "static",
    }


def _builtin_tool_schema(name: str) -> dict[str, Any] | None:
    if name not in KT_BUILTIN_SCHEMAS:
        return None
    td = get_tool_def(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": td.description if td else f"KT built-in tool: {name}",
            "parameters": _with_background_option(KT_BUILTIN_SCHEMAS[name]),
        },
        "source": "kt_builtin",
    }


def _metadata_fallback_schema(name: str) -> dict[str, Any]:
    td = get_tool_def(name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": td.description if td else f"Tool: {name}",
            "parameters": _with_background_option({
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Input content for the tool",
                    }
                },
            }),
        },
        "source": "metadata_fallback",
    }


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
    if tool_name in TOOL_METADATA:
        td = TOOL_METADATA[tool_name]
        result["category"] = td.category
        result["risk_level"] = td.risk_level
        result["label"] = td.label
    return result


def _base_tool_schema(name: str) -> dict[str, Any]:
    tool_name = str(name or "").strip()
    return _static_tool_schema(tool_name) or _builtin_tool_schema(tool_name) or _metadata_fallback_schema(tool_name)


def _ensure_known_tool(name: str) -> str:
    tool_name = str(name or "").strip()
    if not tool_name:
        raise ValueError("tool_name required")
    if tool_name not in TOOL_METADATA:
        raise ValueError(f"unknown tool: {tool_name}")
    return tool_name


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
    return _add_tool_metadata(schema, tool_name)


def build_tool_schema_config(db, name: str) -> dict[str, Any]:
    """返回 WebUI schema 编辑所需的默认、覆盖、可编辑和生效 schema。"""
    tool_name = _ensure_known_tool(name)
    override = load_tool_schema_override(db, tool_name)
    default_schema = _add_tool_metadata(_base_tool_schema(tool_name), tool_name)
    editable_schema = _add_tool_metadata(override or default_schema, tool_name)
    effective_schema = build_tool_schema(tool_name, db=db)
    return {
        "tool": tool_name,
        "default_schema": default_schema,
        "override_schema": _add_tool_metadata(override, tool_name) if override else None,
        "editable_schema": editable_schema,
        "tool_schema": effective_schema,
        "override_present": override is not None,
    }


def build_effective_tool_schemas(enabled: dict[str, bool], *, db=None) -> list[dict[str, Any]]:
    """按当前启用工具构造预览 schema；memory subagent 保持元数据兜底。"""
    schemas: list[dict[str, Any]] = []
    for name in sorted(n for n, ok in (enabled or {}).items() if ok):
        if name not in TOOL_METADATA:
            logger.warning("Skip unknown runtime tool schema: %s", name)
            continue
        schemas.append(build_tool_schema(name, db=db))
    return schemas
