"""
NanobotBridge — Lifecycle manager that wraps KT's Agent for HTTP request/response usage.

Replaces the old manual NanobotKTController + UnifiedProvider setup.
The bridge creates a KT Agent from the creature config, manages its lifecycle,
and provides a simple async handle_message() interface for use in routes.py.
"""

import asyncio
import hashlib
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional
from zoneinfo import ZoneInfo

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.llm.message import make_multimodal_content
from openai import AsyncOpenAI

from nanobot_kt.output import BufferedOutput
from nanobot_kt.request_scope import BridgeRequestScope
from nanobot_kt.image_pipeline import prepare_image_parts
from nanobot_kt.reply_contract import RichTerminalOutput
from nanobot_kt.model_attempts import (
    AttemptOutcome,
    classify_attempt_outcome,
    merge_model_candidates,
)
from clients.new_api_client import NewAPIClient
from clients.model_registry import model_supports_capabilities, registry
from core.llm_sdk_tracing import install_openai_chat_completion_tracer
from core.session_guidance import resolve_session_guidance

from config import (
    NEW_API_KEY,
    NEW_API_BASE_URL,
    LLM_MODEL_REPLY,
    REPLY_MODEL_INTEL_FLOOR,
    REPLY_MODEL_INTEL_BOOST,
    REPLY_MODEL_MAX_COST,
)

logger = logging.getLogger("nanobot.kt.bridge")

if TYPE_CHECKING:
    from nanobot_kt.prompt_runtime import PromptRuntimeInput


def _current_time_label() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S CST")


def _registry_provider_for_route(provider_id: str) -> str:
    from nanobot_kt.model_runtime import registry_provider_for_route

    return registry_provider_for_route(provider_id)


def _is_news_request(query: str) -> bool:
    q = (query or "").lower()
    markers = ("news", "latest", "today", "资讯", "新闻", "快讯", "日报", "早报", "发布")
    return any(marker in q for marker in markers)


def _is_group_analysis_request(query: str) -> bool:
    q = (query or "").lower()
    direct_markers = ("群聊总结", "群总结", "群日报", "分析群", "总结群", "分析这个群", "总结这个群")
    if any(marker in q for marker in direct_markers):
        return True
    group_markers = ("群", "群聊", "这个群")
    analysis_markers = ("分析", "总结", "日报", "概览", "回顾")
    return any(group_marker in q for group_marker in group_markers) and any(
        analysis_marker in q for analysis_marker in analysis_markers
    )


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_message_content_to_text(item) for item in content)
    if isinstance(content, dict):
        parts: list[str] = []
        for key in ("text", "content", "output"):
            if key in content:
                parts.append(_message_content_to_text(content.get(key)))
        return "\n".join(part for part in parts if part)
    return ""


def _conversation_msg_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def _conversation_msg_content(msg: Any) -> str:
    if isinstance(msg, dict):
        return _message_content_to_text(msg.get("content", ""))
    return _message_content_to_text(getattr(msg, "content", ""))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _strip_kt_framework_prompt_sections(text: str) -> str:
    """移除 KT 自动追加的工具/技能说明。

    真实工具权限由 API tools schema 和 RuntimeTool 决定；这些大段说明容易
    和运行时裁剪后的 schema 不一致。
    """
    content = str(text or "")
    markers = (
        "\n\n## Available Sub-Agents",
        "\n\n## Available Functions",
        "\n\n## Skills",
        "\n\n## Tool Usage",
    )
    indexes = [content.find(marker) for marker in markers if content.find(marker) >= 0]
    if not indexes:
        return content
    return content[:min(indexes)].rstrip()


async def _call_tracker_method(tracker: Any, method_name: str, model_id: str) -> None:
    if tracker is None:
        return
    method = getattr(tracker, method_name, None)
    if not method:
        return
    result = method(model_id)
    if inspect.isawaitable(result):
        await result


@dataclass(frozen=True)
class PromptRuntimeAssemblyContext:
    prompt_engine: str
    prompt_mode: str
    prompt_key: str
    chat_type: str
    runtime_chat_type: str
    session_id: str
    user_id: str
    group_id: str
    sender_name: str
    query: str
    persona_text: str
    history_header: str
    history_messages: list[dict[str, Any]]
    runtime_tool_prompt: str
    effort_constraint: str
    trace_id: str
    run_id: str
    is_group: bool
    meta: dict[str, Any]
    tool_plan: Any
    session_guidance: str = field(default="", repr=False)
    session_guidance_chat_stream_id: str = ""
    session_guidance_resolution_status: str = "not_requested"
    is_super_user: bool = False
    platform: str = "qq"


@dataclass
class BridgeRuntimeToolState:
    persona_text: str
    history_messages: Any
    history_header: str
    is_group: bool
    effort_constraint: str
    runtime_preset: str
    chat_type: str
    runtime_chat_type: str
    group_id: str
    user_id: str
    platform: str
    tool_plan: Any
    runtime_tool_prompt: str
    effective_tools: list[str]
    final_tools_token: Any
    tool_plan_token: Any


@dataclass
class BridgeEventPayload:
    event_content: Any
    image_parts: list[Any]
    required_capabilities: dict[str, bool]


@dataclass
class ModelLoopResult:
    response: str
    result: Any
    target_model: str
    terminal_output: RichTerminalOutput | None
    selected_candidate: dict[str, Any] | None
    attempts: int
    health_status: AttemptOutcome


@dataclass
class ReplyResolution:
    response: str
    agent_result: str
    no_reply: bool
    no_tool_call: bool
    output_preview: str
    finish_status: str
    error: str = ""


@dataclass
class BridgeTraceFinalizer:
    bridge: Any
    run_id: str
    trace_tokens: Any
    run_meta: dict[str, Any]
    started_at: float
    now: Any
    final_tools_token: Any = None
    tool_plan_token: Any = None
    closed: bool = False

    def set_tool_tokens(
        self,
        *,
        final_tools_token: Any = None,
        tool_plan_token: Any = None,
    ) -> None:
        if final_tools_token is not None:
            self.final_tools_token = final_tools_token
        if tool_plan_token is not None:
            self.tool_plan_token = tool_plan_token

    def finish(
        self,
        status: str,
        *,
        output_preview: str = "",
        error: str = "",
        model: str = "",
    ) -> None:
        if self.closed:
            return
        self.closed = True

        def run_step(label: str, callback: Callable[[], None]) -> None:
            try:
                callback()
            except Exception as exc:
                logger.warning("Bridge trace cleanup step %s failed: %s", label, exc, exc_info=True)

        run_step("restore_saved_tools", self.bridge._restore_saved_tools)

        def finish_run() -> None:
            from core.tracing import RunTracer

            RunTracer.finish_run(
                self.run_id,
                status=status,
                output_preview=output_preview,
                error=error,
                latency_ms=int((self.now() - self.started_at) * 1000),
                model=model,
                meta=self.run_meta,
            )

        run_step("finish_run", finish_run)
        if self.tool_plan_token is not None:
            tool_plan_token = self.tool_plan_token
            self.tool_plan_token = None

            def reset_tool_plan() -> None:
                from core.tool_plan import reset_current_tool_plan

                reset_current_tool_plan(tool_plan_token)

            run_step("reset_tool_plan", reset_tool_plan)
        if self.final_tools_token is not None:
            final_tools_token = self.final_tools_token
            self.final_tools_token = None

            def reset_final_tools() -> None:
                from core.final_tools import reset_current_final_tools

                reset_current_final_tools(final_tools_token)

            run_step("reset_final_tools", reset_final_tools)
        if self.trace_tokens is not None:
            trace_tokens = self.trace_tokens
            self.trace_tokens = None

            def reset_trace() -> None:
                from core.tracing_context import reset_trace_context

                reset_trace_context(trace_tokens)

            run_step("reset_trace", reset_trace)


class NanobotBridge:
    """
    Wraps a KT Agent for use as a request/response handler.

    Lifecycle:
        bridge = NanobotBridge("creatures/nanobot")
        await bridge.start()
        response = await bridge.handle_message("hello", user_id="u1", session_id="s1")
        await bridge.stop()
    """

    # 每轮重建 conversation，不跨请求复用（DB 是唯一事实源）
    CONVERSATION_TTL_SECONDS = 0

    def __init__(self, creature_path: str = "creatures/nanobot"):
        self.creature_path = creature_path
        self._output = BufferedOutput()
        self._agent: Agent | None = None
        self._session_locks: dict[str, asyncio.Lock] = {}  # 按 session 分锁
        self._last_prompt_render_meta: dict[str, Any] = {}

    def _disable_config_prompt(self, config: Any) -> None:
        """禁用 KT config 内置 prompt，主链路统一由 canonical prompt runtime 注入。"""
        config.system_prompt = ""

    async def start(self) -> None:
        """Initialize the KT agent from creature config."""
        logger.info(f"Loading KT agent from {self.creature_path}")
        config = load_agent_config(self.creature_path)
        self._disable_config_prompt(config)
        config.include_tools_in_prompt = False
        config.include_hints_in_prompt = False
        config.skill_index_budget_bytes = 0
        logger.info("[Prompt] config prompt disabled; canonical prompt runtime will inject messages")
        self._agent = Agent(
            config,
            output_module=self._output,
            pwd="./workspace",  # 文件操作沙箱：read/write/edit 等工具只能在此目录内操作
        )
        # 强制 block 模式——即使模型重试也不允许访问 workspace 外的路径
        if hasattr(self._agent, 'executor') and hasattr(self._agent.executor, '_path_guard'):
            self._agent.executor._path_guard.mode = "block"
            logger.info("[NanobotBridge] File tool sandbox enforced (mode=block, cwd=./workspace)")
        if hasattr(self._agent, 'executor'):
            try:
                from core.tool_tracing import install_executor_tracing

                install_executor_tracing(self._agent.executor)
                logger.info("[NanobotBridge] Tool tracing wrapper installed")
            except Exception as e:
                logger.warning("[NanobotBridge] Tool tracing wrapper install failed: %s", e)
        try:
            from nanobot_kt.tool_runtime import (
                install_tool_plan_guard,
                install_tool_plan_native_schema_filter,
            )

            if install_tool_plan_guard(self._agent):
                logger.info("[NanobotBridge] ToolPlan guard plugin installed")
            if install_tool_plan_native_schema_filter(self._agent):
                logger.info("[NanobotBridge] ToolPlan native schema filter installed")
        except Exception as e:
            logger.warning("[NanobotBridge] ToolPlan runtime install failed: %s", e)

        try:
            from nanobot_kt.tool_runtime import ensure_tool_plan_runtime

            ensure_tool_plan_runtime(self._agent)
        except Exception as e:
            logger.error("[NanobotBridge] ToolPlan runtime unavailable: %s", e)
            raise RuntimeError(f"ToolPlan runtime unavailable: {e}") from e

        # Critical: Agent must be started so _running=True; otherwise
        # _process_event() drops all events and returns empty output.
        await self._agent.start()
        self._strip_framework_prompt_from_conversation()

        # 注入 agent 引用到 output，tool_done 时触发 interrupt
        if hasattr(self._output, '_agent_ref'):
            self._output._agent_ref = self._agent

        # DeepSeek thinking 禁用——阻止模型复述系统提示词
        if hasattr(self._agent.controller, "llm") and hasattr(self._agent.controller.llm, "extra_body"):
            llm = self._agent.controller.llm
            model = getattr(llm.config, "model", "") if hasattr(llm, "config") else ""
            if getattr(llm, "extra_body", None) is None:
                llm.extra_body = {}
            from core.model_route_options import apply_enable_thinking_to_payload
            apply_enable_thinking_to_payload(llm.extra_body, model, "auto")
            if "thinking" in llm.extra_body:
                logger.info("[NanobotBridge] DeepSeek thinking disabled via extra_body")

        # 获取 KT 工具列表（多级 fallback）
        tools_list: list[str] = []
        try:
            raw = self._agent.registry.list_tools() or []
            tools_list = list(raw)
        except Exception:
            pass
        if not tools_list and hasattr(self._agent.registry, "_tools"):
            raw_tools = getattr(self._agent.registry, "_tools", {})
            if isinstance(raw_tools, dict):
                tools_list = list(raw_tools.keys())
                logger.info("[NanobotBridge] Used registry._tools fallback (%d tools)", len(tools_list))
        normalized: list[str] = []
        for item in tools_list:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict) and item.get("name"):
                normalized.append(str(item["name"]))
            elif hasattr(item, "name"):
                normalized.append(str(item.name))
        tools_list = sorted(set(normalized))
        if not tools_list:
            logger.warning("[ToolRegistry] KT tool list empty; registry type=%s",
                           type(self._agent.registry).__name__ if hasattr(self._agent, 'registry') else 'N/A')
        logger.info(f"KT Agent '{config.name}' initialized with {len(tools_list)} tools: {tools_list}")

        # 工具注册表一致性检查
        try:
            from core.tool_registry import TOOL_METADATA
            kt_tools = set(tools_list)
            meta_tools = set(TOOL_METADATA.keys())
            # subagent 不在 KT registry._tools 中，跳过
            missing_meta = kt_tools - meta_tools
            missing_kt = {t for t in meta_tools - kt_tools
                          if t not in {"memory_read", "memory_write"}}
            if missing_meta:
                logger.warning("[ToolRegistry] KT tools missing metadata: %s", sorted(missing_meta))
            if missing_kt:
                logger.warning("[ToolRegistry] TOOL_METADATA has tools not loaded by KT: %s", sorted(missing_kt))
            self._tool_registry_info = {
                "kt_loaded": sorted(kt_tools),
                "missing_meta": sorted(missing_meta),
                "missing_kt": sorted(missing_kt),
            }
        except Exception as e:
            logger.warning("[ToolRegistry] consistency check failed: %s", e)
            self._tool_registry_info = {}

    def _strip_framework_prompt_from_conversation(self) -> None:
        """清理 KT 聚合器自动追加的 framework prompt 段落。"""
        try:
            ctrl = getattr(self._agent, "controller", None)
            conv = getattr(ctrl, "conversation", None)
            messages = getattr(conv, "_messages", None)
            if not messages:
                return
            for msg in messages:
                if _conversation_msg_role(msg) != "system":
                    continue
                before = _conversation_msg_content(msg)
                after = _strip_kt_framework_prompt_sections(before)
                if after != before:
                    msg.content = after
                    if hasattr(ctrl, "config"):
                        ctrl.config.system_prompt = after
                    logger.info(
                        "[Prompt] stripped KT framework sections chars=%d→%d",
                        len(before), len(after),
                    )
                break
        except Exception as e:
            logger.warning("[Prompt] strip KT framework sections failed: %s", e)

        # 检查 controller 配置
        ctrl = getattr(self._agent, 'controller', None)
        if ctrl:
            logger.info(f"[KT Agent] Controller type: {type(ctrl).__name__}")
        else:
            logger.warning("[KT Agent] No controller attribute found! Agent attributes: %s",
                           [a for a in dir(self._agent) if not a.startswith('__')])

    async def stop(self) -> None:
        """Shutdown the agent."""
        if self._agent:
            await self._agent.stop()
        logger.info("KT Agent stopped")

    PERSONA_MARKER = "<persona_reference"
    RUNTIME_MARKER = "<runtime_context>"

    # 所有运行时注入的 system 消息前缀——每轮 reset 时统一清理
    DYNAMIC_SYSTEM_PREFIXES = (
        "<identity_context>",
        "<session_guidance>",
        "<runtime_context>",
        "<persona_reference",
        "[PersonaContext]",
        "[CurrentTimeContext]",
        "[GroupRestriction]",
        "[RuntimeTool]",
        "<group_recent_context>",
        "<group_memory_context",
        "[GroupProfileContext]",
        "[ExpressionContext]",
        "[JargonContext]",
        "<history_context>",
        "<conversation_context>",
        "## 群聊行为",
        "## 群聊上下文使用规则",
        "## 当前回复目标",
        "## 群聊发言时机",
        "## 内部控制消息",
        "## 私聊行为",
        "本轮只随口接一句。",
        "本轮简短处理。",
        "本轮认真处理。",
        "[ManagedPrompt]",
        "<reply_contract_retry>",
    )

    def _build_persona_system_reference(self, user_id: str, persona_text: str) -> str:
        from core.prompt_v2.context_adapters import build_persona_reference

        return build_persona_reference(user_id, persona_text)

    def _build_runtime_context(
        self,
        *,
        user_id: str,
        session_id: str,
        sender_name: str,
        meta: dict[str, Any],
    ) -> str:
        chat_type = str(meta.get("chat_type") or "").strip().lower()
        is_group = bool(meta.get("is_group", False))
        if chat_type not in ("private", "group"):
            chat_type = "group" if is_group or str(session_id).startswith("group_") else "private"

        effective_session_id = str(meta.get("session_id") or session_id or "").strip()
        effective_user_id = str(meta.get("user_id") or user_id or "").strip()
        group_id = str(meta.get("group_id") or "").strip()
        if not group_id and chat_type == "group" and effective_session_id.startswith("group_"):
            group_id = effective_session_id[len("group_"):]

        message_id = str(meta.get("message_id") or "").strip()
        source_ids = meta.get("source_message_ids")

        from core.prompt_v2.context_adapters import build_runtime_context
        from core.prompt_v2.schema import PromptCompileRequest

        request = PromptCompileRequest(
            chat_type=chat_type,
            platform=str(meta.get("platform") or "qq"),
            session_id=effective_session_id,
            user_id=effective_user_id,
            group_id=group_id,
            sender_name=sender_name,
            is_super_user=meta.get("is_superuser") is True,
            session_name=str(meta.get("session_name") or ""),
            trigger_reason=str(meta.get("trigger_reason") or ""),
            timing_decision=str(meta.get("timing_decision") or ""),
            current_message_id=message_id,
            source_message_ids=list(source_ids) if isinstance(source_ids, list) else [],
            self_id=str(meta.get("self_id") or ""),
            bot_id=str(meta.get("bot_id") or ""),
            bot_name=str(meta.get("bot_name") or ""),
            bot_aliases=list(meta.get("bot_aliases") or [])
            if isinstance(meta.get("bot_aliases"), list)
            else [],
        )
        return build_runtime_context(request, current_time=_current_time_label())

    def _prompt_runtime_engine(self) -> str:
        try:
            from core.settings_service import settings

            engine = str(settings.get("prompt_runtime.engine", "prompt") or "prompt").strip().lower()
        except Exception:
            engine = "prompt"
        if engine == "v1":
            logger.warning("[PromptRuntime] engine=v1 is removed from live path; using canonical runtime")
        return "prompt"

    def _resolve_prompt_runtime_engine(self, meta: dict[str, Any]) -> str:
        prompt_engine = str(
            meta.get("prompt_runtime_engine_override")
            or meta.get("prompt_engine_override")
            or self._prompt_runtime_engine()
        ).strip().lower()
        if prompt_engine == "v1":
            logger.warning("[PromptRuntime] v1 metadata override ignored after P1-6")
        return "prompt"

    def _prompt_v2_audit_failure_policy(self) -> str:
        try:
            from core.settings_service import settings

            policy = str(
                settings.get("prompt_runtime.v2_audit_failure_policy", "fail_fast")
                or "fail_fast"
            ).strip().lower()
        except Exception:
            policy = "fail_fast"
        if policy == "fallback_v1":
            logger.warning("[PromptRuntime] fallback_v1 audit policy is deprecated; using fail_fast")
        return "fail_fast"

    def _build_prompt_runtime_input(
        self,
        context: PromptRuntimeAssemblyContext,
    ) -> "PromptRuntimeInput":
        from nanobot_kt.prompt_runtime import PromptRuntimeInput

        meta = dict(context.meta or {})
        source_message_ids = [
            str(x) for x in (meta.get("source_message_ids") or [])
            if str(x).strip()
        ]
        try:
            tool_schemas = list(context.tool_plan.sent_tool_schemas)
        except Exception as e:
            raise RuntimeError("ToolPlan schema 快照失败") from e

        prompt_key = context.prompt_key
        if context.prompt_engine not in {"v2", "prompt", "canonical"} or prompt_key in {"group_chat", "private_chat"}:
            prompt_key = "chat_group" if context.is_group else "chat_private"

        return PromptRuntimeInput(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key=prompt_key,
            chat_type=context.chat_type,
            runtime_chat_type=context.runtime_chat_type,
            platform=context.platform,
            session_id=context.session_id,
            user_id=context.user_id,
            group_id=context.group_id,
            sender_name=context.sender_name,
            sender_id=str(meta.get("sender_id") or meta.get("user_id") or context.user_id),
            session_name=str(meta.get("session_name") or ""),
            trigger_reason=str(meta.get("trigger_reason") or ""),
            timing_decision=str(meta.get("timing_decision") or ""),
            current_message_id=str(meta.get("message_id") or ""),
            source_message_ids=source_message_ids,
            self_id=str(meta.get("self_id") or ""),
            bot_id=str(meta.get("bot_id") or ""),
            bot_name=str(meta.get("bot_name") or meta.get("character_name") or ""),
            bot_aliases=list(meta.get("bot_aliases") or []),
            user_input=context.query,
            persona_text=context.persona_text or "无已存储画像",
            session_guidance=context.session_guidance,
            session_guidance_chat_stream_id=(
                context.session_guidance_chat_stream_id
            ),
            session_guidance_resolution_status=(
                context.session_guidance_resolution_status
            ),
            history_header=context.history_header,
            history_messages=context.history_messages,
            runtime_tool_prompt=context.runtime_tool_prompt,
            effort_constraint=context.effort_constraint,
            trace_id=context.trace_id,
            run_id=context.run_id,
            is_group=context.is_group,
            is_super_user=context.is_super_user is True,
            group_profile_context=str(meta.get("group_profile_context") or ""),
            expression_context=str(meta.get("expression_context") or ""),
            jargon_context=str(meta.get("jargon_context") or ""),
            tool_schemas=tool_schemas,
            debug={"context_debug": meta.get("context_debug") or {}},
            audit_failure_policy=self._prompt_v2_audit_failure_policy(),
        )

    def _history_context_text(self, history_header: str, history_messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        if history_header:
            parts.append(history_header)
        for msg in history_messages or []:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def _remove_system_contexts(self, conv: Any, prefixes: tuple[str, ...]) -> None:
        if not hasattr(conv, "_messages"):
            return
        conv._messages = [
            m for m in getattr(conv, "_messages", [])
            if not (
                _conversation_msg_role(m) == "system"
                and _conversation_msg_content(m).startswith(prefixes)
            )
        ]

    from nanobot_kt.reply_contract import ALLOWED_SEND_MODES as _ALLOWED_SEND_MODES

    def _extract_reply_from_tool_output(self, session_id: str = "") -> str:
        """从 conversation 中提取 reply() / no_reply() 工具的结构化输出。

        返回 reply 文本内容；同时将 reply_meta 存入 per-session dict。
        如果是 no_reply() 工具，则记录 no_reply 标志并返回空字符串。
        """
        if not self._agent:
            return ""
        try:
            from nanobot_kt.reply_contract import extract_reply_tool_output

            messages = self._agent.controller.conversation.get_messages()
            # ReplyDebug: 临时诊断日志——确认 conversation 尾部是否有 tool 消息
            logger.debug("[ReplyDebug] conversation messages=%d", len(messages))
            for i, msg in enumerate(messages[-8:]):
                role = _conversation_msg_role(msg)
                text = _conversation_msg_content(msg)
                logger.debug(
                    "[ReplyDebug] tail[%d] role=%s len=%d head=%r",
                    i, role, len(text), text[:300],
                )
            result = extract_reply_tool_output(messages)
            if result.no_reply:
                if session_id:
                    store = self._reply_meta_store()
                    entry = store.get(session_id, {})
                    entry["_no_reply"] = True
                    entry["_no_reply_reason"] = result.no_reply_reason
                    store[session_id] = entry
                logger.info("[Reply] no_reply tool called, reason=%s", result.no_reply_reason)
                return ""
            if result.reply_text:
                if session_id and result.reply_meta:
                    self._reply_meta_store()[session_id] = result.reply_meta
                return result.reply_text
        except Exception as e:
            logger.debug("[Reply] extraction failed: %s", e)

        return ""

    def _reply_meta_store(self) -> dict:
        if not hasattr(self, "_reply_meta_by_session_cache"):
            self._reply_meta_by_session_cache = {}
        return self._reply_meta_by_session_cache

    def pop_last_reply_meta(self, session_id: str = "") -> dict | None:
        return self._reply_meta_store().pop(session_id, None)

    def is_no_reply_session(self, session_id: str) -> bool:
        store = self._reply_meta_store()
        return bool(store.get(session_id, {}).get("_no_reply", False))

    def is_no_tool_call(self, session_id: str) -> bool:
        store = self._reply_meta_store()
        return bool(store.get(session_id, {}).get("_no_tool_call", False))

    def is_fake_tool_call_claim(self, session_id: str) -> bool:
        store = self._reply_meta_store()
        return store.get(session_id, {}).get("_agent_result") == "fake_tool_call_claim"

    def _log_agent_result(self, session_id: str, result: str):
        """记录 agent 结果类型到 meta store，供 routes.py 读取。"""
        if session_id:
            store = self._reply_meta_store()
            entry = store.get(session_id, {})
            entry["_agent_result"] = result
            store[session_id] = entry

    def _build_reply_contract_retry_prompt(self, raw_model_output: str) -> str:
        from nanobot_kt.reply_contract import build_reply_contract_retry_prompt

        return build_reply_contract_retry_prompt(raw_model_output)

    def _count_final_action_tool_calls(self) -> dict[str, int]:
        try:
            from nanobot_kt.reply_contract import count_final_action_tool_calls

            conv = self._agent.controller.conversation if self._agent else None
            if conv is None:
                return {}
            if hasattr(conv, "get_messages"):
                messages = conv.get_messages()
            elif hasattr(conv, "to_messages"):
                messages = conv.to_messages()
            else:
                messages = []
            return count_final_action_tool_calls(messages)
        except Exception as e:
            logger.debug("[Reply] final action count failed: %s", e)
            return {}

    def _record_reply_contract_check(
        self,
        *,
        trace_id: str,
        run_id: str,
        session_id: str,
        attempt: int,
        raw_output: str,
        has_reply_tool: bool,
        has_no_reply_tool: bool,
        has_structured_fallback: bool,
        reply_tool_call_count: int | None = None,
        no_reply_tool_call_count: int | None = None,
        structured_fallback_count: int | None = None,
        total_final_action_count: int | None = None,
        result: str,
    ) -> None:
        try:
            from core.tracing import ReplyContractTracer

            real_counts = self._count_final_action_tool_calls()
            real_total = int(real_counts.get("total_final_action_count", 0) or 0)
            if real_total > 0:
                reply_tool_call_count = (
                    int(real_counts.get("reply_tool_call_count", 0) or 0)
                    if reply_tool_call_count is None else reply_tool_call_count
                )
                no_reply_tool_call_count = (
                    int(real_counts.get("no_reply_tool_call_count", 0) or 0)
                    if no_reply_tool_call_count is None else no_reply_tool_call_count
                )
                structured_fallback_count = (
                    int(real_counts.get("structured_fallback_count", 0) or 0)
                    if structured_fallback_count is None else structured_fallback_count
                )
                total_final_action_count = (
                    real_total if total_final_action_count is None else total_final_action_count
                )

            ReplyContractTracer.record_check(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                attempt=attempt,
                raw_output=raw_output,
                has_reply_tool=has_reply_tool,
                has_no_reply_tool=has_no_reply_tool,
                has_structured_fallback=has_structured_fallback,
                reply_tool_call_count=reply_tool_call_count,
                no_reply_tool_call_count=no_reply_tool_call_count,
                structured_fallback_count=structured_fallback_count,
                total_final_action_count=total_final_action_count,
                result=result,
            )
        except Exception as e:
            logger.debug("[Reply] contract check log skipped: %s", e)

    async def _run_reply_contract_retry_once(
        self,
        *,
        raw_model_output: str,
        trace_id: str,
        run_id: str,
        llm_source: str = "replyer",
    ) -> tuple[Any, str]:
        retry_prompt = self._build_reply_contract_retry_prompt(raw_model_output)
        from nanobot_kt.kt_adapter import create_user_event

        event = create_user_event(retry_prompt)

        self._output.clear()
        self._clear_controller_event_state()
        from core.llm_trace_context import llm_trace_scope
        from nanobot_kt.kt_adapter import process_event

        with llm_trace_scope(trace_id=trace_id, run_id=run_id, source=llm_source):
            retry_result = await process_event(self._agent, event)
        retry_response = self._output.get_response()
        if not retry_response and retry_result:
            retry_response = str(retry_result)
        return retry_result, retry_response

    def _parse_structured_final_action(self, buffer_text: str) -> dict | None:
        from nanobot_kt.reply_contract import parse_structured_final_action

        return parse_structured_final_action(buffer_text)

    def _restore_saved_tools(self):
        """兼容旧清理路径；ToolPlan 模式不再改写 registry._tools。"""
        self._saved_tools = {}
        self._tool_cleanup_needed = False

    def _clear_controller_event_state(self) -> None:
        """清理 KT controller 的跨请求事件残留。

        Nanobot 的 HTTP 入口是无状态的：每个请求都会从 DB 重建上下文。
        KT controller 本身是长生命周期对象，上一轮未消费的 pending event
        如果留到下一轮，会优先于当前 user_input 被处理，导致真实 LLM 请求缺失
        本轮 <user_input>。
        """
        ctrl = getattr(getattr(self, "_agent", None), "controller", None)
        if ctrl is None:
            return

        pending = getattr(ctrl, "_pending_events", None)
        pending_count = len(pending) if isinstance(pending, list) else 0
        if isinstance(pending, list):
            pending.clear()

        drained = 0
        queue = getattr(ctrl, "_event_queue", None)
        if isinstance(queue, asyncio.Queue):
            # 真实 KT controller 使用 asyncio.Queue；单测中的 MagicMock 也有
            # get_nowait，但不会抛 QueueEmpty，不能按队列 drain。
            while True:
                try:
                    queue.get_nowait()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
                except Exception:
                    break

        injections = getattr(ctrl, "_pending_injections", None)
        injection_count = len(injections) if isinstance(injections, list) else 0
        if isinstance(injections, list):
            injections.clear()

        if pending_count or drained or injection_count:
            logger.warning(
                "[SessionRuntime] Cleared stale KT event state pending=%d queued=%d injections=%d",
                pending_count,
                drained,
                injection_count,
            )

    def _extract_last_rich_tool_output(
        self,
        allowed_report_kinds: tuple[str, ...] = ("ai_daily", "group_analysis"),
    ) -> RichTerminalOutput | None:
        if not self._agent:
            return None

        try:
            from nanobot_kt.reply_contract import extract_rich_terminal_output

            conv = self._agent.controller.conversation
            if hasattr(conv, "get_messages"):
                payloads = conv.get_messages()
            elif hasattr(conv, "to_messages"):
                payloads = conv.to_messages()
            else:
                payloads = []
            return extract_rich_terminal_output(
                payloads,
                allowed_report_kinds=allowed_report_kinds,
            )
        except Exception as e:
            logger.debug(f"[NanobotBridge] rich tool output fallback failed: {e}")
        return None

    def _prepare_output_for_request(
        self,
        *,
        stream_queue: asyncio.Queue[dict[str, Any]] | None,
        stream_enabled: bool,
    ) -> None:
        self._output.clear()
        if stream_queue is not None and stream_enabled:
            self._output.enable_stream(stream_queue)
        else:
            disable_stream = getattr(self._output, "disable_stream", None)
            if callable(disable_stream):
                disable_stream()

        try:
            from core.reply_runtime_cache import clear_last_reply

            clear_last_reply()
        except Exception:
            pass

    async def _prepare_event_payload(
        self,
        *,
        prompt_event_content: str,
        files: Any,
        tool_schemas: list[dict[str, Any]],
    ) -> BridgeEventPayload:
        image_parts: list[Any] = []
        if files:
            image_parts = await asyncio.to_thread(
                prepare_image_parts,
                files,
                source_type="qq",
                source_name_prefix="attachment",
                detail="low",
            )
            event_content = make_multimodal_content(prompt_event_content, images=image_parts)
        else:
            event_content = prompt_event_content
        required_capabilities = {"supports_stream": True}
        if image_parts:
            required_capabilities["supports_image"] = True
        if tool_schemas:
            required_capabilities["supports_tools"] = True
        return BridgeEventPayload(
            event_content=event_content,
            image_parts=image_parts,
            required_capabilities=required_capabilities,
        )

    async def _run_model_loop(
        self,
        *,
        candidate_models: list[dict[str, Any]],
        route_plan: Any,
        event_content: Any,
        query: str,
        session_id: str,
        meta: dict[str, Any],
        tracker: Any,
        trace_id: str,
        run_id: str,
        reply_llm_source: str,
        create_user_event: Any,
        process_event: Any,
    ) -> ModelLoopResult:
        model_iterator = iter(candidate_models)
        max_attempts = min(len(candidate_models), 8) if candidate_models else 5
        result = None
        response = ""
        target_model = ""
        terminal_output = None
        selected_candidate = None
        attempts = 0
        health_status = "pending"
        next_event = create_user_event(event_content, stream=meta["stream"])

        for attempt in range(max_attempts):
            self._output.clear()
            event = next_event
            next_event = create_user_event(event_content, stream=meta["stream"])

            # Get next model from ordered list
            try:
                candidate = next(model_iterator)
                selected_candidate = candidate
                target_model = candidate["id"]
            except StopIteration:
                logger.warning(f"[Model Router] No more candidates after {attempt} attempts")
                break
            attempts = attempt + 1
            health_status = "pending"

            # Update KT agent's LLM model + provider base_url/api_key
            if hasattr(self._agent, 'controller') and hasattr(self._agent.controller, 'llm') and hasattr(self._agent.controller.llm, 'config'):
                self._agent.controller.llm.config.model = target_model
                # 同步 route provider 的 base_url / api_key 到 controller
                llm = self._agent.controller.llm
                if hasattr(llm.config, 'temperature') and route_plan.temperature is not None:
                    llm.config.temperature = float(route_plan.temperature)
                if hasattr(llm.config, 'max_tokens'):
                    llm.config.max_tokens = route_plan.max_tokens
                if hasattr(llm, "extra_body"):
                    from core.model_route_options import apply_enable_thinking_to_payload
                    if getattr(llm, "extra_body", None) is None:
                        llm.extra_body = {}
                    apply_enable_thinking_to_payload(
                        llm.extra_body,
                        target_model,
                        route_plan.enable_thinking,
                    )
                _target_base_url = str(route_plan.base_url or "").rstrip("/")
                _target_api_key = str(route_plan.api_key or "")
                _current_base_url = str(getattr(llm, 'base_url', '') or "").rstrip("/")
                _current_api_key = str(getattr(llm, '_api_key', '') or "")
                _current_timeout = float(getattr(llm, '_timeout', 120.0) or 120.0)
                _base_url_changed = bool(_target_base_url and _current_base_url != _target_base_url)
                _api_key_changed = _current_api_key != _target_api_key
                _timeout_changed = _current_timeout != route_plan.timeout
                if _base_url_changed or _api_key_changed or _timeout_changed:
                    llm.base_url = _target_base_url
                    llm._api_key = _target_api_key
                    llm._timeout = route_plan.timeout
                    llm._client = AsyncOpenAI(
                        api_key=_target_api_key,
                        base_url=_target_base_url,
                        timeout=route_plan.timeout,
                        max_retries=getattr(llm, '_max_retries', 3),
                        default_headers=getattr(llm, '_extra_headers', {}),
                    )
                    logger.info(
                        "[Model Router] Switched provider base_url=%s api_key_changed=%s timeout_changed=%s",
                        _target_base_url[:80],
                        _api_key_changed,
                        _timeout_changed,
                    )
                llm.provider_name = route_plan.provider_id or route_plan.registry_provider
                install_openai_chat_completion_tracer(
                    llm,
                    provider=llm.provider_name,
                    base_url=_target_base_url,
                )
                logger.info(
                    f"[Model Router] Attempt {attempt+1}: {target_model} "
                    f"(intel={candidate.get('intelligence')}, "
                    f"cost={candidate.get('cost_input_1m')})"
                )

            try:
                logger.info(f"[NanobotBridge] Calling _process_event (Attempt {attempt+1})...")
                from core.llm_trace_context import llm_trace_scope
                with llm_trace_scope(trace_id=trace_id, run_id=run_id, source=reply_llm_source):
                    result = await process_event(self._agent, event)
                logger.info(f"[NanobotBridge] _process_event returned: type={type(result)}, value={result}")
            except Exception as e:
                logger.error(f"[NanobotBridge] Agent processing error: {e}", exc_info=True)
                self._output._buffer.append(f"\n[系统内部错误] {str(e)}")

            response = self._output.get_response()
            logger.info(
                f"[NanobotBridge] Attempt {attempt+1} response: "
                f"len={len(response)}, empty={not response.strip()}, "
                f"has_sys_err={'[系统内部错误]' in response}, "
                f"has_tool_err={'[工具错误]' in response}, "
                f"preview={response[:100] if response else '(EMPTY)'}"
            )
            # 只接受绑定真实工具调用的结构化富结果，不从文本或缓存猜测 HTML。
            terminal_output = self._extract_last_rich_tool_output()
            if terminal_output:
                logger.info("[NanobotBridge] Using preserved tool HTML output (replacing buffer)")
                self._output._buffer = [terminal_output.html]
                await _call_tracker_method(tracker, "record_success", target_model)
                health_status = "success"
                break

            reply_text = self._extract_reply_from_tool_output(session_id)
            if self.is_no_reply_session(session_id):
                logger.info("[NanobotBridge] no_reply() called, stopping model loop")
                await _call_tracker_method(tracker, "record_success", target_model)
                health_status = "success"
                break
            if reply_text:
                from core.reply_postprocess import strip_chat_end_punct
                reply_text = strip_chat_end_punct(reply_text)
                logger.info("[NanobotBridge] reply() called len=%d, stopping model loop", len(reply_text))
                await _call_tracker_method(tracker, "record_success", target_model)
                health_status = "success"
                break

            attempt_outcome = classify_attempt_outcome(response)
            if attempt_outcome == "failure":
                logger.warning(f"[NanobotBridge] Framework error. Recording failure for {target_model}")
                await _call_tracker_method(tracker, "record_failure", target_model)
                health_status = "failure"

                # reasoning_content: 只 ban 出错的特定模型，不波及同厂商其他模型
                if "reasoning_content" in response:
                    logger.warning("[NanobotBridge] reasoning_content — banning %s only", target_model)

                if attempt >= max_attempts - 1:
                    break

                # Conversation rollback logic
                tool_results_preserved = False
                if hasattr(self._agent.controller, 'conversation'):
                    msgs = self._agent.controller.conversation.get_messages()
                    user_idx = self._agent.controller.conversation.find_last_user_index()
                    last_tool_idx = -1
                    if user_idx >= 0:
                        for i in range(len(msgs) - 1, user_idx, -1):
                            if msgs[i].role == "tool":
                                last_tool_idx = i
                                break
                    if last_tool_idx >= 0:
                        strip_from = last_tool_idx + 1
                        if strip_from < len(msgs):
                            self._agent.controller.conversation.truncate_from(strip_from)
                            logger.info(f"[NanobotBridge] Preserved tool results (idx≤{last_tool_idx})")
                        from kohakuterrarium.core.events import TriggerEvent
                        next_event = TriggerEvent(type="user_input", content="")
                        tool_results_preserved = True
                    elif user_idx >= 0:
                        content = msgs[user_idx].content
                        if isinstance(content, str) and content == query:
                            self._agent.controller.conversation.truncate_from(user_idx)
                        next_event = create_user_event(event_content, stream=meta["stream"])
                if not tool_results_preserved:
                    next_event = create_user_event(event_content, stream=meta["stream"])
                continue

            break

        return ModelLoopResult(
            response=response,
            result=result,
            target_model=target_model,
            terminal_output=terminal_output,
            selected_candidate=selected_candidate,
            attempts=attempts,
            health_status=health_status,
        )

    async def _check_reply_contract(
        self,
        *,
        session_id: str,
        response: str,
        result: Any,
        terminal_output: RichTerminalOutput | None,
        target_model: str,
        query: str,
        meta: dict[str, Any],
        event_content: Any,
        create_user_event: Any,
        process_event: Any,
        trace_id: str,
        run_id: str,
        reply_llm_source: str,
    ) -> ReplyResolution:
        """验证最终动作；只有具有真实工具来源的结果可以发送给用户。"""

        def _mark_no_reply(agent_result: str, reason: str = "") -> None:
            if session_id:
                store = self._reply_meta_store()
                entry = store.get(session_id, {})
                entry["_no_reply"] = True
                if reason:
                    entry["_no_reply_reason"] = reason
                store[session_id] = entry
            self._log_agent_result(session_id, agent_result)

        def _record(
            *,
            attempt: int,
            raw_output: str,
            has_reply_tool: bool = False,
            has_no_reply_tool: bool = False,
            result_name: str,
        ) -> None:
            self._record_reply_contract_check(
                trace_id=trace_id,
                run_id=run_id,
                session_id=session_id,
                attempt=attempt,
                raw_output=raw_output,
                has_reply_tool=has_reply_tool,
                has_no_reply_tool=has_no_reply_tool,
                has_structured_fallback=False,
                result=result_name,
            )

        def _reply_resolution(
            text: str,
            *,
            attempt: int,
            raw_output: str,
            agent_result: str,
            trace_result: str,
        ) -> ReplyResolution:
            from core.reply_postprocess import strip_chat_end_punct

            reply_text = str(text or "").strip()
            if not reply_text.lstrip().startswith("<"):
                reply_text = strip_chat_end_punct(reply_text)
            _record(
                attempt=attempt,
                raw_output=raw_output,
                has_reply_tool=True,
                result_name=trace_result,
            )
            self._log_agent_result(session_id, agent_result)
            return ReplyResolution(
                response=reply_text,
                agent_result=agent_result,
                no_reply=False,
                no_tool_call=False,
                output_preview=reply_text,
                finish_status="success",
            )

        def _no_reply_resolution(
            *,
            attempt: int,
            raw_output: str,
            agent_result: str,
            trace_result: str,
            reason: str = "",
        ) -> ReplyResolution:
            _record(
                attempt=attempt,
                raw_output=raw_output,
                has_no_reply_tool=True,
                result_name=trace_result,
            )
            _mark_no_reply(agent_result, reason)
            return ReplyResolution(
                response="",
                agent_result=agent_result,
                no_reply=True,
                no_tool_call=False,
                output_preview="",
                finish_status="no_reply",
                error=reason,
            )

        def _rich_resolution(
            rich: RichTerminalOutput,
            *,
            attempt: int,
            raw_output: str,
            agent_result: str,
            trace_result: str,
        ) -> ReplyResolution:
            _record(
                attempt=attempt,
                raw_output=raw_output,
                result_name=trace_result,
            )
            self._log_agent_result(session_id, agent_result)
            logger.info(
                "[Reply] verified rich terminal output tool=%s call_id=%s kind=%s len=%d",
                rich.tool_name,
                rich.tool_call_id,
                rich.report_kind,
                len(rich.html),
            )
            return ReplyResolution(
                response=rich.html,
                agent_result=agent_result,
                no_reply=False,
                no_tool_call=False,
                output_preview=rich.html,
                finish_status="success",
            )

        def _suppress(agent_result: str) -> ReplyResolution:
            if session_id:
                store = self._reply_meta_store()
                entry = store.get(session_id, {})
                entry["_no_tool_call"] = True
                store[session_id] = entry
            self._log_agent_result(session_id, agent_result)
            return ReplyResolution(
                response="",
                agent_result=agent_result,
                no_reply=False,
                no_tool_call=True,
                output_preview="",
                finish_status="suppressed",
                error=agent_result,
            )

        # 保留签名中的上下文字段，便于调用方和审计日志稳定；它们不能授权最终动作。
        _ = (result, target_model, query, event_content, create_user_event, process_event)

        buffer_text = self._output.get_response() if hasattr(self._output, "get_response") else ""
        response_text = response.strip() if isinstance(response, str) else ""
        raw_output = buffer_text or response_text

        reply_text = self._extract_reply_from_tool_output(session_id)
        if self.is_no_reply_session(session_id):
            reason = str(
                self._reply_meta_store().get(session_id, {}).get("_no_reply_reason", "")
            )
            return _no_reply_resolution(
                attempt=0,
                raw_output=raw_output,
                agent_result="no_reply_tool",
                trace_result="ok",
                reason=reason,
            )
        if reply_text:
            return _reply_resolution(
                reply_text,
                attempt=0,
                raw_output=raw_output,
                agent_result="reply_tool",
                trace_result="ok",
            )

        verified_rich = terminal_output or self._extract_last_rich_tool_output()
        if verified_rich is not None:
            return _rich_resolution(
                verified_rich,
                attempt=0,
                raw_output=raw_output,
                agent_result="rich_tool_output",
                trace_result="ok",
            )

        from nanobot_kt.reply_contract import detect_no_tool_call_result

        agent_result = detect_no_tool_call_result(raw_output)
        _record(
            attempt=0,
            raw_output=raw_output,
            result_name=agent_result,
        )

        retry_enabled = meta.get("enable_reply_contract_retry", True) is not False
        if not retry_enabled:
            logger.warning(
                "[Reply] NO VERIFIED FINAL ACTION - suppressing output session=%s result=%s",
                session_id,
                agent_result,
            )
            return _suppress(agent_result)

        try:
            logger.warning(
                "[Reply] %s - retrying reply contract once session=%s",
                agent_result,
                session_id,
            )
            _, retry_response = await self._run_reply_contract_retry_once(
                raw_model_output=raw_output,
                trace_id=trace_id,
                run_id=run_id,
                llm_source=reply_llm_source,
            )
            retry_buffer = (
                self._output.get_response()
                if hasattr(self._output, "get_response")
                else ""
            )
            retry_response_text = (
                retry_response.strip() if isinstance(retry_response, str) else ""
            )
            retry_raw_output = retry_buffer or retry_response_text

            retry_reply_text = self._extract_reply_from_tool_output(session_id)
            if self.is_no_reply_session(session_id):
                reason = str(
                    self._reply_meta_store()
                    .get(session_id, {})
                    .get("_no_reply_reason", "")
                )
                return _no_reply_resolution(
                    attempt=1,
                    raw_output=retry_raw_output,
                    agent_result="retry_success",
                    trace_result="retry_success",
                    reason=reason,
                )
            if retry_reply_text:
                return _reply_resolution(
                    retry_reply_text,
                    attempt=1,
                    raw_output=retry_raw_output,
                    agent_result="retry_success",
                    trace_result="retry_success",
                )

            retry_rich = self._extract_last_rich_tool_output()
            if retry_rich is not None:
                return _rich_resolution(
                    retry_rich,
                    attempt=1,
                    raw_output=retry_raw_output,
                    agent_result="retry_success",
                    trace_result="retry_success",
                )

            _record(
                attempt=1,
                raw_output=retry_raw_output,
                result_name="suppressed",
            )
        except Exception as exc:
            logger.error(
                "[Reply] contract retry failed session=%s: %s",
                session_id,
                exc,
                exc_info=True,
            )
            _record(
                attempt=1,
                raw_output=str(exc),
                result_name="suppressed",
            )

        logger.warning(
            "[Reply] retry produced no verified final action - suppressing session=%s",
            session_id,
        )
        return _suppress(agent_result)

    async def handle_message(
        self,
        query: str,
        *,
        user_id: str = "",
        session_id: str = "",
        sender_name: str = "",
        metadata: dict[str, Any] | None = None,
        stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> str:
        """
        Send a user message to the KT agent and return the response.

        If stream_queue is provided, concise progress events are pushed
        during processing (one per distinct tool call, plus errors).
        """
        if not self._agent:
            return "Error: Agent not initialized"
        if not str(session_id or "").strip():
            raise ValueError("session_id 不能为空")

        # SessionRuntime: 按 session 分锁，同 session 串行，不同 session 并发
        import time as _time
        if not hasattr(self, "_session_locks"):
            self._session_locks = {}
        sess_lock = self._session_locks.setdefault(session_id, asyncio.Lock())

        # Interrupt: 如果该 session 正在处理，发 interrupt 信号
        if sess_lock.locked() and hasattr(self._agent, '_interrupt_requested'):
            logger.info("[SessionRuntime] Interrupt flag set for session=%s", session_id)
            self._agent._interrupt_requested = True

        async with BridgeRequestScope(
            sess_lock,
            self._output,
            dry_run=bool((metadata or {}).get("dry_run")),
        ) as request_scope:
            t_start = _time.time()
            meta = dict(metadata or {})
            meta["stream"] = bool(stream or meta.get("stream"))
            is_group = bool(meta.get("is_group", False))
            chat_type = "group" if is_group else "private"
            platform = str(meta.get("platform") or "qq").strip().lower() or "qq"
            group_id = ""
            if is_group:
                group_id = str(meta.get("group_id") or "").strip()
                session_group_id = str(session_id or "").strip()
                if not group_id and session_group_id.startswith("group_"):
                    group_id = session_group_id[len("group_"):]
            prompt_engine = self._resolve_prompt_runtime_engine(meta)
            prompt_mode = "prompt"
            prompt_key = "chat_group" if is_group else "chat_private"
            from core.tracing import RunTracer, new_trace_id
            from core.tracing_context import set_trace_context

            trace_id = str(meta.get("trace_id") or new_trace_id())
            meta["trace_id"] = trace_id
            run_meta = {
                "sender_name": sender_name,
                "is_group": is_group,
                "message_id": meta.get("message_id", ""),
                "prompt_engine": prompt_engine,
                "platform": platform,
                "chat_type": chat_type,
            }
            run_handle = RunTracer.start_run(
                trace_id=trace_id,
                session_id=session_id,
                user_id=user_id,
                chat_type=chat_type,
                group_id=group_id,
                run_type="chat",
                prompt_mode=prompt_mode,
                prompt_key=prompt_key,
                prompt_source="",
                prompt_runtime_path="",
                prompt_default_path="",
                prompt_sha256="",
                input_preview=query,
                meta=run_meta,
            )
            trace_tokens = set_trace_context(trace_id, run_handle.run_id)
            trace_finalizer = BridgeTraceFinalizer(
                bridge=self,
                run_id=run_handle.run_id,
                trace_tokens=trace_tokens,
                run_meta=run_meta,
                started_at=t_start,
                now=_time.time,
            )
            request_scope.bind_trace_finalizer(trace_finalizer)

            self._prepare_output_for_request(
                stream_queue=stream_queue,
                stream_enabled=meta["stream"],
            )
            logger.info("[SessionRuntime] START session=%s user=%s query_len=%d",
                        session_id, user_id, len(query))

            # 每轮重建 conversation——DB 是唯一事实源，不在内存中跨请求复用
            from nanobot_kt.kt_adapter import (
                install_conversation_order_guard,
                reset_conversation_to_system,
            )

            if install_conversation_order_guard(self._agent):
                logger.info("[PromptRuntime] KT conversation order guard installed")
            before_len, after_len = reset_conversation_to_system(self._agent)
            if before_len or after_len:
                logger.info("[SessionRuntime] Reset conversation: %d→%d (system=%d)",
                            before_len, after_len, after_len)
            self._clear_controller_event_state()

            # --- Prompt runtime 输入：只收集结构化上下文，不在 bridge 手工注入 prompt ---
            persona_text = str(meta.get("persona_text", "")).strip()
            history_messages = meta.get("history_messages", [])
            history_header = str(meta.get("history_header", "")).strip()
            is_super_user = meta.get("is_superuser") is True
            # --- Dynamic runtime preset enforcement ---
            effort_constraint = str(meta.get("effort_constraint", "")).strip()
            runtime_preset = str(meta.get("runtime_preset", "full")).strip()
            runtime_chat_type = "private_superuser" if (not is_group and is_super_user) else chat_type
            user_id = str(meta.get("user_id", session_id or "")).strip()

            from core.final_tools import set_current_final_tools
            from core.tool_plan import build_tool_plan, set_current_tool_plan
            from core.runtime_tool_service import record_runtime_tool_decision
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                guidance = resolve_session_guidance(
                    uow.db,
                    platform=platform,
                    chat_type=chat_type,
                    session_id=session_id,
                )
                run_meta.update(guidance.debug)
                tool_plan = build_tool_plan(
                    chat_type=runtime_chat_type, group_id=group_id, user_id=user_id,
                    platform=platform,
                    runtime_preset=runtime_preset, db=uow.db,
                )
                decision_recorded = record_runtime_tool_decision(
                    session_id=session_id,
                    message_id=meta.get("message_id", ""),
                    chat_type=runtime_chat_type,
                    group_id=group_id,
                    user_id=user_id,
                    platform=platform,
                    runtime_preset=runtime_preset,
                    enabled=tool_plan.enabled,
                    disabled=tool_plan.disabled,
                    effective_tools=sorted(tool_plan.executable_tool_names),
                    db=uow.db,
                )
                if decision_recorded:
                    try:
                        uow.commit()
                    except Exception as e:
                        uow.rollback()
                        logger.warning("[Bridge] failed to commit runtime tool decision: %s", e)
            final_tools_token = set_current_final_tools(tool_plan)
            trace_finalizer.set_tool_tokens(final_tools_token=final_tools_token)
            tool_plan_token = set_current_tool_plan(tool_plan)
            trace_finalizer.set_tool_tokens(tool_plan_token=tool_plan_token)
            enabled = dict(tool_plan.enabled or {})
            disabled = dict(tool_plan.disabled or {})
            runtime_tool_prompt = tool_plan.runtime_tool_prompt
            effective_tools = sorted(tool_plan.executable_tool_names)
            logger.info("[Bridge] runtime_preset=%s chat=%s effective=%s tool_plan=%s",
                        runtime_preset, runtime_chat_type, effective_tools, tool_plan.sha256[:12])
            meta["_runtime_preset"] = runtime_preset
            meta["_disabled_tools"] = {k: v for k, v in disabled.items()}
            try:
                executor_session = getattr(getattr(self._agent, "executor", None), "_session", None)
                if executor_session is not None and hasattr(executor_session, "extra"):
                    executor_session.extra["nanobot_runtime_context"] = {
                        "chat_type": chat_type,
                        "runtime_chat_type": runtime_chat_type,
                        "is_group": is_group,
                        "is_super_user": is_super_user,
                        "session_id": session_id,
                        "group_id": group_id,
                        "user_id": user_id,
                        "platform": platform,
                        "sender_name": sender_name,
                    }
            except Exception as e:
                logger.debug("[Bridge] failed to expose runtime context to tools: %s", e)

            # ---------------------------------------------

            from nanobot_kt.prompt_runtime import (
                PromptRuntimeAuditFailure,
                build_prompt_runtime,
            )

            prompt_input = self._build_prompt_runtime_input(
                PromptRuntimeAssemblyContext(
                    prompt_engine=prompt_engine,
                    prompt_mode=prompt_mode,
                    prompt_key=prompt_key,
                    chat_type=chat_type,
                    runtime_chat_type=runtime_chat_type,
                    platform=platform,
                    session_id=session_id,
                    user_id=user_id,
                    group_id=group_id,
                    sender_name=sender_name,
                    query=query,
                    persona_text=persona_text,
                    session_guidance=guidance.text,
                    session_guidance_chat_stream_id=guidance.chat_stream_id,
                    session_guidance_resolution_status=guidance.status,
                    history_header=history_header,
                    history_messages=history_messages,
                    runtime_tool_prompt=runtime_tool_prompt,
                    effort_constraint=effort_constraint,
                    trace_id=trace_id,
                    run_id=run_handle.run_id,
                    is_group=is_group,
                    is_super_user=is_super_user,
                    meta=meta,
                    tool_plan=tool_plan,
                )
            )
            try:
                prompt_build = await build_prompt_runtime(prompt_input)
            except PromptRuntimeAuditFailure as e:
                logger.error("[PromptRuntime] live audit failed: %s", e)
                run_meta.update(e.meta_update)
                self._log_agent_result(session_id, "prompt_v2_audit_failed")
                trace_finalizer.finish("error", error=str(e))
                return ""
            run_meta.update(prompt_build.meta_update)
            self._last_prompt_render_meta = {
                "prompt_source": prompt_build.prompt_source,
                "prompt_runtime_path": prompt_build.prompt_runtime_path,
                "prompt_default_path": prompt_build.prompt_default_path,
                "prompt_sha256": prompt_build.prompt_sha256,
                "prompt_template_resolutions": prompt_build.prompt_template_resolutions,
            }
            try:
                RunTracer.update_prompt_source(
                    run_handle.run_id,
                    **self._last_prompt_render_meta,
                )
            except Exception:
                pass
            from nanobot_kt.kt_adapter import apply_prompt_messages, create_user_event, process_event

            applied_messages = apply_prompt_messages(self._agent, prompt_build.pre_event_messages)
            if applied_messages:
                logger.info(
                    "[PromptRuntime] built key=%s mode=%s source=%s pre_messages=%d sha=%s",
                    prompt_build.prompt_key,
                    prompt_build.prompt_mode,
                    prompt_build.prompt_source,
                    applied_messages,
                    prompt_build.prompt_sha256[:12],
                )

            logger.debug(f"[NanobotBridge] Agent initialized: {self._agent is not None}")
            logger.debug(f"[NanobotBridge] Output module: {self._output}")
            logger.debug(f"[NanobotBridge] Agent output_module attr: {getattr(self._agent, '_output_module', 'NOT SET')}")

            # Create a user input event for the KT controller
            event_payload = await self._prepare_event_payload(
                prompt_event_content=prompt_build.event_content,
                files=meta.get("files"),
                tool_schemas=prompt_input.tool_schemas,
            )
            image_parts = event_payload.image_parts
            event_content = event_payload.event_content
            required_capabilities = event_payload.required_capabilities
            event = create_user_event(event_content, stream=meta["stream"])
            logger.info("[NanobotBridge] Event created, about to call _process_event")

            # --- Dynamic Model Routing (new priority-ordered system) ---
            route_client = None
            raw_query = str(meta.get("raw_query", query)).strip() or query
            reply_llm_source = "replyer.group_chat" if is_group else "replyer.private_chat"
            try:
                tracker = NewAPIClient.get_failure_tracker()
            except Exception as e:
                tracker = None
                logger.warning(f"[Model Router] Failure tracker unavailable: {e}")

            # 从 WebUI route 读取 provider 配置，让 route provider 控制实际 API 调用
            try:
                from nanobot_kt.model_runtime import resolve_reply_route_plan

                route_plan = resolve_reply_route_plan(
                    default_base_url=NEW_API_BASE_URL,
                    default_api_key=NEW_API_KEY,
                )
            except RuntimeError as e:
                logger.error("[Model Router] reply route disabled: %s", e)
                trace_finalizer.finish("error", error=str(e))
                return f"[系统内部错误] {e}"
            _route_provider_id = route_plan.provider_id
            _route_registry_provider = route_plan.registry_provider
            _route_timeout = route_plan.timeout
            _route_temperature = route_plan.temperature
            _route_max_tokens = route_plan.max_tokens
            _route_enable_thinking = route_plan.enable_thinking
            _client_base_url = route_plan.base_url
            _client_api_key = route_plan.api_key
            logger.info(
                "[Model Router] route provider=%s registry_provider=%s base_url=%s timeout=%s temperature=%s max_tokens=%s enable_thinking=%s",
                _route_provider_id,
                _route_registry_provider,
                _client_base_url[:80] if _client_base_url else "(empty)",
                _route_timeout,
                _route_temperature,
                _route_max_tokens,
                _route_enable_thinking,
            )

            try:
                route_client = NewAPIClient(
                    api_key=_client_api_key,
                    base_url=_client_base_url,
                    registry_provider=_route_registry_provider,
                )
                existing = registry.get_models_by_provider(_route_registry_provider)
                if not existing:
                    logger.info("[Model Router] Registry empty, forcing model sync...")
                    await route_client.sync_models_to_registry(force=True)
                else:
                    await route_client.sync_models_to_registry(force=False)

                messages_for_routing = [{"role": "user", "content": raw_query}]
                meta_complexity = meta.get("complexity")
                if isinstance(meta_complexity, int) and 1 <= meta_complexity <= 10:
                    complexity = meta_complexity
                    logger.info("[Model Router] using metadata complexity=%s", complexity)
                else:
                    complexity = route_client.estimate_complexity(messages_for_routing, tools=[{}])
                base_intel_floor = max(1, complexity - 1)
                reply_intel_floor = max(
                    base_intel_floor + max(0, REPLY_MODEL_INTEL_BOOST),
                    max(1, REPLY_MODEL_INTEL_FLOOR),
                )
                automatic_candidates = route_client.get_ordered_candidates(
                    provider=_route_registry_provider,
                    intel_floor=reply_intel_floor,
                    max_cost=REPLY_MODEL_MAX_COST,
                    required_capabilities=required_capabilities,
                )
                from core.settings_service import settings
                manual_reply_model = str(
                    meta.get("reply_model")
                    or settings.get("model.reply")
                    or LLM_MODEL_REPLY
                    or ""
                ).strip()
                preferred_candidate = None
                if manual_reply_model:
                    info = registry.get_model_info(manual_reply_model)
                    if info is None:
                        logger.warning(
                            "[ReplyModel] configured model unknown: %s, falling back to auto",
                            manual_reply_model,
                        )
                        manual_reply_model = ""
                    elif info.get("enabled", True) is False:
                        logger.warning(
                            "[ReplyModel] configured model disabled: %s, falling back to auto",
                            manual_reply_model,
                        )
                        manual_reply_model = ""
                    elif not model_supports_capabilities(info, required_capabilities):
                        logger.warning(
                            "[ReplyModel] configured model lacks capabilities: %s required=%s, falling back to auto",
                            manual_reply_model,
                            required_capabilities,
                        )
                        manual_reply_model = ""
                    elif (
                        tracker is not None
                        and tracker.sync_is_disabled(manual_reply_model) is True
                    ):
                        logger.warning(
                            "[ReplyModel] configured model circuit-disabled: %s, falling back to auto",
                            manual_reply_model,
                        )
                        manual_reply_model = ""

                if manual_reply_model:
                    preferred_candidate = dict(info)
                    preferred_candidate.setdefault("id", manual_reply_model)
                    logger.info(
                        "[ReplyModel] manual=%s complexity=%s base_floor=%s reply_floor=%s",
                        manual_reply_model, complexity, base_intel_floor, reply_intel_floor,
                    )

                candidates = merge_model_candidates(
                    preferred_candidate,
                    automatic_candidates,
                )
                logger.info(
                    "[ReplyModel] auto complexity=%s base_floor=%s reply_floor=%s max_cost=%.3f required=%s",
                    complexity, base_intel_floor, reply_intel_floor, REPLY_MODEL_MAX_COST, required_capabilities,
                )
                logger.info(
                    f"[Model Router] complexity={complexity}, intel_floor={reply_intel_floor}, "
                    f"candidates={[(c['id'][:30], c.get('intelligence')) for c in candidates[:8]]}"
                )
            except Exception as e:
                logger.error(f"[Model Router] Failed to route: {e}", exc_info=True)
                candidates = []
            # --------------------------------------------------------

            if (
                not candidates
                and image_parts
                and required_capabilities.get("supports_image")
            ):
                event_content = (
                    f"{prompt_build.event_content}\n\n"
                    "[系统提示：当前没有可用视觉模型，图片内容未被读取。请不要推测图片内容。]"
                )
                required_capabilities = dict(required_capabilities)
                required_capabilities.pop("supports_image", None)
                logger.warning(
                    "[Model Router] no vision-capable candidates; degrading image request to text-only content required=%s",
                    required_capabilities,
                )
                if (
                    route_client is not None
                    and "_route_registry_provider" in locals()
                    and "reply_intel_floor" in locals()
                ):
                    try:
                        candidates = route_client.get_ordered_candidates(
                            provider=_route_registry_provider,
                            intel_floor=reply_intel_floor,
                            max_cost=REPLY_MODEL_MAX_COST,
                            required_capabilities=required_capabilities,
                        )
                        logger.info(
                            "[Model Router] degraded text-only candidates=%s",
                            [(c.get("id", "")[:30], c.get("intelligence")) for c in candidates[:8]],
                        )
                    except Exception as e:
                        logger.error("[Model Router] degraded text-only route failed: %s", e, exc_info=True)

            if not candidates:
                logger.warning("[Model Router] No healthy candidates available")

            # --- Context budget awareness ---
            est_tokens = len(query) // 2
            if candidates:
                ctx_window = candidates[0].get("context_window", 128000)
                logger.info(
                    f"[Context Budget] estimated_tokens={est_tokens}, "
                    f"context_window={ctx_window}, "
                    f"usage={est_tokens / ctx_window * 100:.1f}%"
                )
            # ----------------------------------

            model_loop = await self._run_model_loop(
                candidate_models=candidates,
                route_plan=route_plan,
                event_content=event_content,
                query=query,
                session_id=session_id,
                meta=meta,
                tracker=tracker,
                trace_id=trace_id,
                run_id=run_handle.run_id,
                reply_llm_source=reply_llm_source,
                create_user_event=create_user_event,
                process_event=process_event,
            )
            response = model_loop.response
            result = model_loop.result
            target_model = model_loop.target_model
            terminal_output = model_loop.terminal_output

            if not target_model:
                logger.warning("[Model Router] No model attempt was made")
                trace_finalizer.finish("empty", model="")
                return ""

            logger.info("[NanobotBridge] Checking output buffer...")
            response = self._output.get_response()
            buffer_list = self._output._buffer if hasattr(self._output, '_buffer') else []
            buffer_len = len(buffer_list)

            # 如果 output buffer 为空，尝试从返回值获取
            if not response and result:
                logger.info("[NanobotBridge] Buffer empty, using _process_event return value")
                response = str(result) if result else ""

            # 事后只补提取已验证的富结果，不从 assistant 文本猜测 HTML。
            final_terminal = self._extract_last_rich_tool_output()
            if final_terminal and final_terminal.html.strip() != response.strip():
                logger.info("[NanobotBridge] post-loop HTML replacement")
                terminal_output = final_terminal
                response = final_terminal.html
            
            logger.info(f"[NanobotBridge] After processing: response_len={len(response)}, buffer_chunks={buffer_len}")
            if response:
                logger.debug(f"[NanobotBridge] Response preview: {response[:200]}")
            else:
                logger.warning("[NanobotBridge] EMPTY RESPONSE!")
                logger.warning(f"[NanobotBridge] buffer={buffer_list}, result={result}")

            reply_resolution = await self._check_reply_contract(
                session_id=session_id,
                response=response,
                result=result,
                terminal_output=terminal_output,
                target_model=target_model,
                query=query,
                meta=meta,
                event_content=event_content,
                create_user_event=create_user_event,
                process_event=process_event,
                trace_id=trace_id,
                run_id=run_handle.run_id,
                reply_llm_source=reply_llm_source,
            )
            response = reply_resolution.response
            reply_source = reply_resolution.agent_result
            if target_model:
                if (
                    reply_resolution.finish_status in {"success", "no_reply"}
                    and model_loop.health_status != "success"
                ):
                    await _call_tracker_method(tracker, "record_success", target_model)
                    model_loop.health_status = "success"
                elif (
                    reply_resolution.finish_status in {"suppressed", "error"}
                    and model_loop.health_status != "failure"
                ):
                    await _call_tracker_method(tracker, "record_failure", target_model)
                    model_loop.health_status = "failure"
            if reply_resolution.finish_status in {"no_reply", "suppressed"}:
                trace_finalizer.finish(
                    reply_resolution.finish_status,
                    output_preview=reply_resolution.output_preview,
                    error=reply_resolution.error,
                    model=target_model,
                )
                return response

            if not response.strip():
                logger.warning("[NanobotBridge] KT agent returned empty response after strip")
                trace_finalizer.finish("empty", model=locals().get("target_model", ""))
                return ""

            elapsed_ms = int((_time.time() - t_start) * 1000)
            response_source = reply_source
            logger.info("[SessionRuntime] DONE session=%s latency=%dms resp_len=%d source=%s",
                        session_id, elapsed_ms, len(response), response_source)

            # 惰性清理：session_locks 过大时扫一遍过期锁（无等待者 = unlocked）
            if len(self._session_locks) > 200:
                stale_sids = [sid for sid, lock in list(self._session_locks.items())
                              if not lock.locked()]
                for sid in stale_sids:
                    self._session_locks.pop(sid, None)
                if stale_sids:
                    logger.info("[SessionRuntime] Cleaned %d idle session locks", len(stale_sids))

            # bot 回复后通知 GroupRuntime——触发 cooldown
            if response and meta.get("is_group") and not meta.get("dry_run"):
                try:
                    from core.timing_runtime import get_group_runtime
                    get_group_runtime().note_bot_replied(session_id)
                except Exception as e:
                    logger.warning("[GroupRuntime] note_bot_replied failed: %s", e)

            if stream and stream_queue is not None and response:
                writer = getattr(self._output, "write_final", None)
                if writer is not None:
                    await writer(str(response), replace=True, source="bridge")

            trace_finalizer.finish(
                "success",
                output_preview=response,
                model=locals().get("target_model", ""),
            )
            return response

    @property
    def agent(self) -> Agent | None:
        """Access the underlying KT agent for advanced operations."""
        return self._agent


class NanobotBridgePool:
    """按会话隔离 KT Agent，避免全局单例锁阻塞不同用户。"""

    def __init__(self, creature_path: str = "creatures/nanobot"):
        self.creature_path = creature_path
        self._bridges: dict[str, NanobotBridge] = {}
        self._bridge_last_used: dict[str, float] = {}
        self._bridge_inflight: dict[str, int] = {}
        self._stop_tasks: set[asyncio.Task] = set()
        self._create_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._started = False
        self._stopping = False
        self.BRIDGE_TTL_SECONDS = 600  # 10 分钟无使用则回收
        self.BRIDGE_STOP_TIMEOUT_SECONDS = 30  # stop 等待 inflight 的上限，超时强制回收

    async def start(self) -> None:
        async with self._create_lock:
            if self._stopping:
                raise RuntimeError("BridgePool is stopping")
            self._started = True
        logger.info("[NanobotBridgePool] started")

    @property
    def _tool_registry_info(self) -> dict:
        """从第一个 child bridge 获取工具注册表信息。"""
        for b in self._bridges.values():
            info = getattr(b, '_tool_registry_info', {})
            if info and info.get("kt_loaded"):
                return info
        return {}

    @property
    def bridge_count(self) -> int:
        return len(self._bridges)

    async def ensure_registry_probe(self):
        """确保至少有一个 child bridge 提供 registry 信息。"""
        if not self._tool_registry_info.get("kt_loaded"):
            await self._get_bridge("_admin_registry_probe")

    async def stop(self) -> None:
        async with self._stop_lock:
            bridges: list[NanobotBridge] = []
            try:
                import time as _t
                async with self._create_lock:
                    self._stopping = True
                    self._started = False

                deadline = _t.monotonic() + max(0.0, float(self.BRIDGE_STOP_TIMEOUT_SECONDS))
                forced = False
                while True:
                    async with self._create_lock:
                        inflight = dict(self._bridge_inflight)
                        if not inflight:
                            bridges = list(self._bridges.values())
                            self._bridges.clear()
                            self._bridge_last_used.clear()
                            self._bridge_inflight.clear()
                            break
                    if _t.monotonic() >= deadline:
                        # inflight 长时间未归零（在途请求卡死）——不再无限等待，
                        # 强制回收所有 bridge，避免 stop 永久挂起阻塞进程关闭。
                        logger.warning(
                            "[BridgePool] stop inflight timeout after %.1fs, forcing shutdown: %s",
                            float(self.BRIDGE_STOP_TIMEOUT_SECONDS),
                            inflight,
                        )
                        async with self._create_lock:
                            bridges = list(self._bridges.values())
                            self._bridges.clear()
                            self._bridge_last_used.clear()
                            self._bridge_inflight.clear()
                        forced = True
                        break
                    logger.debug(
                        "[BridgePool] waiting for inflight requests before stop: %s",
                        inflight,
                    )
                    await asyncio.sleep(0.01)

                if bridges:
                    await asyncio.gather(*(bridge.stop() for bridge in bridges), return_exceptions=True)
                if self._stop_tasks:
                    await asyncio.gather(*list(self._stop_tasks), return_exceptions=True)
                if forced:
                    logger.info("[NanobotBridgePool] stopped (forced after inflight timeout)")
                else:
                    logger.info("[NanobotBridgePool] stopped")
            finally:
                async with self._create_lock:
                    self._stopping = False

    def _session_key(self, user_id: str = "", session_id: str = "") -> str:
        sid = str(session_id or "").strip()
        if sid:
            return sid
        uid = str(user_id or "").strip()
        return f"user:{uid}" if uid else "_default"

    def _track_stop_task(self, bridge: NanobotBridge, key: str) -> asyncio.Task:
        task = asyncio.create_task(bridge.stop(), name=f"bridge-stop:{key}")
        self._stop_tasks.add(task)

        def _done(done: asyncio.Task) -> None:
            self._stop_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                logger.debug("[BridgePool] stop task cancelled for session=%s", key)
            except Exception as exc:
                logger.warning(
                    "[BridgePool] stop task failed for session=%s: %s",
                    key,
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_done)
        return task

    async def _get_or_create_bridge_locked(self, key: str) -> NanobotBridge:
        import time as _t

        if self._stopping:
            raise RuntimeError("BridgePool is stopping")

        # TTL 清理只回收空闲 bridge；正在处理请求的 bridge 由 release 刷新 last_used。
        now = _t.time()
        stale = [
            k for k, ts in list(self._bridge_last_used.items())
            if now - ts > self.BRIDGE_TTL_SECONDS and self._bridge_inflight.get(k, 0) <= 0
        ]
        for k in stale:
            b = self._bridges.pop(k, None)
            self._bridge_last_used.pop(k, None)
            self._bridge_inflight.pop(k, None)
            if b:
                self._track_stop_task(b, k)
        if stale:
            logger.info("[BridgePool] Cleaned %d stale bridges", len(stale))

        bridge = self._bridges.get(key)
        if bridge is None:
            bridge = NanobotBridge(self.creature_path)
            await bridge.start()
            self._bridges[key] = bridge
            logger.info("[NanobotBridgePool] created child bridge for session=%s", key)
        self._bridge_last_used[key] = _t.time()
        return bridge

    async def _get_bridge(self, key: str) -> NanobotBridge:
        async with self._create_lock:
            return await self._get_or_create_bridge_locked(key)

    async def _acquire_bridge(self, key: str) -> NanobotBridge:
        async with self._create_lock:
            bridge = await self._get_or_create_bridge_locked(key)
            self._bridge_inflight[key] = self._bridge_inflight.get(key, 0) + 1
            return bridge

    async def _release_bridge(self, key: str) -> None:
        import time as _t

        async with self._create_lock:
            count = self._bridge_inflight.get(key, 0)
            if count <= 1:
                self._bridge_inflight.pop(key, None)
                if key in self._bridges:
                    self._bridge_last_used[key] = _t.time()
            else:
                self._bridge_inflight[key] = count - 1

    async def handle_message(
        self,
        query: str,
        *,
        user_id: str = "",
        session_id: str = "",
        sender_name: str = "",
        metadata: dict[str, Any] | None = None,
        stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> str:
        if not self._started:
            await self.start()
        key = self._session_key(user_id=user_id, session_id=session_id)
        bridge = await self._acquire_bridge(key)
        try:
            return await bridge.handle_message(
                query,
                user_id=user_id,
                session_id=session_id,
                sender_name=sender_name,
                metadata=metadata,
                stream_queue=stream_queue,
                stream=stream,
            )
        finally:
            await self._release_bridge(key)

    def pop_last_reply_meta(self, session_id: str = "") -> dict | None:
        """从对应会话的 child bridge 取出最近一次 reply_meta。"""
        key = self._session_key(session_id=session_id)
        bridge = self._bridges.get(key)
        if bridge is not None:
            return bridge.pop_last_reply_meta(session_id)
        for bridge in self._bridges.values():
            meta = bridge.pop_last_reply_meta(session_id)
            if meta is not None:
                return meta
        return None

    @property
    def agent(self) -> Agent | None:
        for bridge in self._bridges.values():
            return bridge.agent
        return None


# Module-level singleton (initialized by server.py lifespan)
_bridge: Optional["NanobotBridgePool"] = None


def get_bridge() -> NanobotBridgePool:
    """Get the global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = NanobotBridgePool()
    return _bridge


async def init_bridge() -> NanobotBridgePool:
    """Initialize and start the global bridge. Called from server.py lifespan."""
    global _bridge
    _bridge = NanobotBridgePool()
    await _bridge.start()
    return _bridge


async def shutdown_bridge() -> None:
    """Shutdown the global bridge. Called from server.py lifespan."""
    global _bridge
    if _bridge:
        await _bridge.stop()
        _bridge = None
