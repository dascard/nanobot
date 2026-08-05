"""Native／可选 KT Agent 的 HTTP 请求生命周期 Bridge。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from functools import partial
from uuid import uuid4
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from zoneinfo import ZoneInfo

from nanobot_kt.output import BufferedOutput
from nanobot_kt.bridge_runtime_support import (
    bind_bridge_runtime_correlation,
    bind_native_recovery_model_plan,
    bridge_agent_id,
    bridge_memory_registry_snapshot,
    build_attempt_cache_context,
    build_bridge_llm_cache_context,
    build_child_bridge,
    build_request_runtime_plans,
    compatibility_runtime_selection_policy,
    set_bridge_runtime_model_route,
)
from nanobot_kt.request_scope import BridgeRequestScope
from nanobot_kt.image_pipeline import prepare_image_parts
from nanobot_kt.optional_message_api import make_multimodal_content
from nanobot_kt.reply_contract import RichTerminalOutput
from nanobot_kt.message_adapter import MessageContractBridgeMixin
from nanobot_kt.runtime_context_adapter import (
    build_fallback_request_runtime_context,
    build_request_runtime_context,
)
from nanobot_kt.runtime_event_delivery import (
    build_runtime_event_handler,
    create_default_run_event_sink,
)
from nanobot_kt.bridge_state import (
    BridgeEventPayload,
    BridgeRuntimeToolState as BridgeRuntimeToolState,
    BridgeTraceFinalizer,
    ModelLoopResult,
    PromptRuntimeAssemblyContext,
    ReplyResolution,
    bind_run_task_owner,
    prepare_bridge_run_meta,
)
from nanobot_kt.direct_tool_execution import (
    DirectToolExecutionResult,
    execute_registered_tool,
)
from nanobot_kt.model_attempts import (
    classify_attempt_outcome,
    merge_model_candidates,
    record_candidate_health,
)
from clients.new_api_client import NewAPIClient
from clients.model_registry import model_supports_capabilities, registry
from core.agent_runtime import (
    AgentRuntimeKind,
    AgentRuntimeAmbiguousError,
    AgentRuntimePort,
    AgentRuntimeSelection,
    AgentRuntimeSelectionPolicy,
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeAttribute,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeTurnKind,
)
from core.llm_sdk_tracing import install_openai_chat_completion_tracer
from core.session_guidance import resolve_session_guidance
from config import (
    NEW_API_KEY,
    NEW_API_BASE_URL,
)

logger = logging.getLogger("nanobot.kt.bridge")

if TYPE_CHECKING:
    from clients.reply_route_chat_completion_adapter import (
        ReplyRouteChatCompletionAdapter,
    )
    from core.model_provider.route_plan import ReplyRoutePlan
    from core.runtime.plugin_lifecycle import RuntimePluginManager
    from nanobot_kt.prompt_runtime import PromptRuntimeInput


# 仅用于兼容测试注入；生产路径在显式选择 KT 后才解析上游 API。
Agent = load_agent_config = None


def _current_time_label() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S CST")


def _is_news_request(query: str) -> bool:
    q = (query or "").lower()
    markers = (
        "news",
        "latest",
        "today",
        "资讯",
        "新闻",
        "快讯",
        "日报",
        "早报",
        "发布",
    )
    return any(marker in q for marker in markers)


def _is_group_analysis_request(query: str) -> bool:
    q = (query or "").lower()
    direct_markers = (
        "群聊总结",
        "群总结",
        "群日报",
        "分析群",
        "总结群",
        "分析这个群",
        "总结这个群",
    )
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


class NanobotBridge(MessageContractBridgeMixin):
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

    def __init__(
        self,
        creature_path: str = "creatures/nanobot",
        *,
        runtime_kind: AgentRuntimeKind | str = AgentRuntimeKind.KT,
        runtime_plugin_manager_factory: (
            Callable[[str], RuntimePluginManager] | None
        ) = None,
    ):
        try:
            self.runtime_kind = AgentRuntimeKind(runtime_kind)
        except ValueError as exc:
            raise ValueError("runtime_kind 必须是 native 或 kt") from exc
        self.creature_path = creature_path
        self._output = BufferedOutput()
        self._agent: Any | None = None
        self._runtime: AgentRuntimePort | None = None
        self._agent_id = Path(creature_path).name or "agent"
        self._runtime_name = self._agent_id
        self._native_completion_port: ReplyRouteChatCompletionAdapter | None = None
        self._memory_runtime: Any = None
        self._active_route_plan: ReplyRoutePlan | None = None
        self._session_locks: dict[str, asyncio.Lock] = {}  # 按 session 分锁
        self._last_prompt_render_meta: dict[str, Any] = {}
        self._kt_session_key = f"nanobot-bridge-{uuid4().hex}"
        self._run_event_sink = create_default_run_event_sink()
        self._runtime_plugin_manager_factory = runtime_plugin_manager_factory

    def _build_runtime(self, *, initially_started: bool) -> AgentRuntimePort:
        from nanobot_kt.bridge_runtime_support import (
            build_bridge_agent_runtime,
        )

        return build_bridge_agent_runtime(
            self,
            initially_started=initially_started,
        )

    def _require_runtime(self) -> AgentRuntimePort:
        runtime = getattr(self, "_runtime", None)
        if runtime is None:
            # 兼容直接注入测试 Agent 的旧夹具；生产启动路径始终显式创建 Runtime。
            runtime = self._build_runtime(initially_started=True)
            self._runtime = runtime
        return runtime

    def _apply_runtime_model_route(
        self,
        agent: object,
        route: RuntimeModelRoute,
    ) -> None:
        transport = self._active_route_plan
        if transport is None:
            raise RuntimeError("模型传输计划尚未绑定")
        from nanobot_kt.model_provider_adapter import apply_kt_preset_model_route

        apply_kt_preset_model_route(
            agent,
            route,
            transport,
            tracer_installer=install_openai_chat_completion_tracer,
        )

    def _disable_config_prompt(self, config: Any) -> None:
        """关闭 KT 自有 Prompt 与历史裁剪，统一交给 canonical runtime。"""

        config.system_prompt = ""
        config.max_messages = 0

    async def start(self) -> None:
        """启动 Bridge；任一步失败都清理已创建的 KT Session。"""

        try:
            await self._start()
        except BaseException:
            await self._cleanup_failed_start()
            raise

    async def _start(self) -> None:
        """从 creature 配置初始化所选 Runtime。"""
        logger.info(
            "Loading %s agent runtime from %s",
            self.runtime_kind.value,
            self.creature_path,
        )
        from core.tool_schema_preview import validate_registered_tool_schemas

        validate_registered_tool_schemas()
        tool_projection = None
        agent_type: type[Any] | None = None
        if self.runtime_kind is AgentRuntimeKind.KT:
            from nanobot_kt.optional_agent_api import resolve_kt_agent_api
            agent_factory, config_loader = resolve_kt_agent_api(
                Agent, load_agent_config
            )
            from nanobot_kt.tool_registration_adapter import (
                apply_tool_registration_projection,
            )

            config = config_loader(self.creature_path)
            tool_projection = apply_tool_registration_projection(config)
            # KT 默认按 config.name 复用进程级 Session；每个 Bridge 必须拥有
            # 独立 Session，避免共享 Agent 与隔离任务互相覆盖状态。
            config.session_key = self._kt_session_key
            self._disable_config_prompt(config)
            config.include_tools_in_prompt = False
            config.include_hints_in_prompt = False
            config.skill_index_budget_bytes = 0
            self._runtime_name = str(config.name or self._runtime_name)
            logger.info(
                "[Prompt] config prompt disabled; canonical prompt runtime will inject messages"
            )
            self._agent = agent_factory(
                config,
                output_module=self._output,
                pwd="./workspace",
            )
            agent_type = agent_factory
        self._runtime = self._build_runtime(initially_started=False)
        # ToolPlan 是执行权限边界，必须在 Agent 可能处理事件前完成安装与校验。
        tool_policy = self._runtime.install_tool_policy()
        logger.info(
            "[NanobotBridge] ToolPlan runtime ready guard=%s schema_filter=%s",
            tool_policy.guard_installed,
            tool_policy.schema_filter_installed,
        )
        await self._runtime.start()
        from core.memory_provider import MemoryProviderInitContext
        from nanobot_kt.memory_runtime import build_memory_provider_runtime

        self._memory_runtime = build_memory_provider_runtime()
        try:
            await self._memory_runtime.initialize(
                MemoryProviderInitContext(runtime_id=self._runtime.runtime_id)
            )
        except BaseException:
            await self._runtime.stop()
            raise
        self._output.set_interrupt_callback(
            lambda reason: self._require_runtime().interrupt(reason=reason)
        )

        tools_list = list(self._runtime.list_tool_names())
        if self.runtime_kind is AgentRuntimeKind.NATIVE:
            from nanobot_kt.bridge_runtime_support import (
                build_native_tool_registry_runtime_info,
            )

            self._tool_registry_info = build_native_tool_registry_runtime_info(
                tools_list
            )
        else:
            from nanobot_kt.tool_registration_adapter import (
                build_tool_registry_runtime_info,
            )

            self._tool_registry_info = build_tool_registry_runtime_info(
                tools_list,
                tool_projection,
                strict=(
                    agent_type is not None
                    and self._agent is not None
                    and self._agent.__class__ is agent_type
                ),
            )
        if not tools_list:
            logger.warning(
                "[ToolRegistry] Runtime tool list empty; registry type=%s",
                type(self._agent.registry).__name__
                if self._agent is not None and hasattr(self._agent, "registry")
                else "N/A",
            )
        logger.info(
            "%s Agent '%s' initialized with %d tools: %s",
            self.runtime_kind.value,
            self._runtime_name,
            len(tools_list),
            tools_list,
        )

    async def _cleanup_failed_start(self) -> None:
        """启动失败时尽力释放局部 Runtime，并保留原始异常。"""

        memory_runtime = getattr(self, "_memory_runtime", None)
        runtime = getattr(self, "_runtime", None)
        try:
            if memory_runtime is not None and memory_runtime.initialized:
                await memory_runtime.shutdown()
        except BaseException as exc:
            logger.warning(
                "Bridge 启动失败后清理 Memory Runtime 失败 error_type=%s",
                type(exc).__name__,
            )
        try:
            if (
                runtime is not None
                and runtime.state is not RuntimeLifecycleState.STOPPED
            ):
                await runtime.stop()
        except BaseException as exc:
            logger.warning(
                "Bridge 启动失败后清理 Agent Runtime 失败 error_type=%s",
                type(exc).__name__,
            )
        self._output.set_interrupt_callback(None)
        self._remove_kt_session()

    def _remove_kt_session(self) -> Exception | None:
        """移除当前 Bridge 独占的 KT 进程级 Session。"""

        if self.runtime_kind is AgentRuntimeKind.NATIVE:
            return None
        try:
            from kohakuterrarium.core.session import remove_session

            remove_session(self._kt_session_key)
            return None
        except Exception as exc:
            logger.warning(
                "KT Session 清理失败 session_key=%s error_type=%s",
                self._kt_session_key,
                type(exc).__name__,
            )
            return exc

    async def stop(self) -> None:
        """Shutdown the agent."""
        runtime = getattr(self, "_runtime", None)
        memory_runtime = getattr(self, "_memory_runtime", None)
        first_error: BaseException | None = None
        try:
            if memory_runtime is not None and memory_runtime.initialized:
                await memory_runtime.shutdown()
        except BaseException as exc:
            first_error = exc
        try:
            if (
                runtime is not None
                and runtime.state is not RuntimeLifecycleState.STOPPED
            ):
                await runtime.stop()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        try:
            self._output.set_interrupt_callback(None)
            session_error = self._remove_kt_session()
            if first_error is None and session_error is not None:
                first_error = session_error
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        logger.info("%s Agent stopped", self.runtime_kind.value)
        if first_error is not None:
            raise first_error

    async def execute_registered_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        user_id: str,
        session_id: str,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str = "",
        trace_id: str = "",
        timeout_seconds: float = 600.0,
        additional_tool_schemas: tuple[dict[str, Any], ...] = (),
    ) -> DirectToolExecutionResult:
        """委托独立执行器，Bridge 仅保留兼容调用面。"""

        return await execute_registered_tool(
            self,
            tool_name,
            args,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            timeout_seconds=timeout_seconds,
            additional_tool_schemas=additional_tool_schemas,
        )

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
            chat_type = (
                "group"
                if is_group or str(session_id).startswith("group_")
                else "private"
            )

        effective_session_id = str(meta.get("session_id") or session_id or "").strip()
        effective_user_id = str(meta.get("user_id") or user_id or "").strip()
        group_id = str(meta.get("group_id") or "").strip()
        if (
            not group_id
            and chat_type == "group"
            and effective_session_id.startswith("group_")
        ):
            group_id = effective_session_id[len("group_") :]

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

            engine = (
                str(settings.get("prompt_runtime.engine", "prompt") or "prompt")
                .strip()
                .lower()
            )
        except Exception:
            engine = "prompt"
        if engine == "v1":
            logger.warning(
                "[PromptRuntime] engine=v1 is removed from live path; using canonical runtime"
            )
        return "prompt"

    def _resolve_prompt_runtime_engine(self, meta: dict[str, Any]) -> str:
        prompt_engine = (
            str(
                meta.get("prompt_runtime_engine_override")
                or meta.get("prompt_engine_override")
                or self._prompt_runtime_engine()
            )
            .strip()
            .lower()
        )
        if prompt_engine == "v1":
            logger.warning("[PromptRuntime] v1 metadata override ignored after P1-6")
        return "prompt"

    def _prompt_v2_audit_failure_policy(self) -> str:
        try:
            from core.settings_service import settings

            policy = (
                str(
                    settings.get("prompt_runtime.v2_audit_failure_policy", "fail_fast")
                    or "fail_fast"
                )
                .strip()
                .lower()
            )
        except Exception:
            policy = "fail_fast"
        if policy == "fallback_v1":
            logger.warning(
                "[PromptRuntime] fallback_v1 audit policy is deprecated; using fail_fast"
            )
        return "fail_fast"

    def _build_prompt_runtime_input(
        self,
        context: PromptRuntimeAssemblyContext,
    ) -> "PromptRuntimeInput":
        from nanobot_kt.prompt_runtime import PromptRuntimeInput

        meta = dict(context.meta or {})
        source_message_ids = [
            str(x) for x in (meta.get("source_message_ids") or []) if str(x).strip()
        ]
        try:
            tool_schemas = list(context.tool_plan.sent_tool_schemas)
        except Exception as e:
            raise RuntimeError("ToolPlan schema 快照失败") from e

        prompt_key = context.prompt_key
        if context.prompt_engine not in {"v2", "prompt", "canonical"} or prompt_key in {
            "group_chat",
            "private_chat",
        }:
            prompt_key = "chat_group" if context.is_group else "chat_private"

        return PromptRuntimeInput(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key=prompt_key,
            chat_type=context.chat_type,
            runtime_chat_type=context.runtime_chat_type,
            platform=context.platform, policy_profile=str(meta.get("policy_profile") or ""),
            session_id=context.session_id,
            user_id=context.user_id,
            group_id=context.group_id,
            sender_name=context.sender_name,
            sender_id=str(
                meta.get("sender_id") or meta.get("user_id") or context.user_id
            ),
            session_name=str(meta.get("session_name") or ""),
            trigger_reason=str(meta.get("trigger_reason") or ""),
            timing_decision=str(meta.get("timing_decision") or ""),
            event_time=str(meta.get("event_time") or ""),
            current_message_id=str(meta.get("message_id") or ""),
            source_message_ids=source_message_ids,
            self_id=str(meta.get("self_id") or ""),
            bot_id=str(meta.get("bot_id") or ""),
            bot_name=str(meta.get("bot_name") or meta.get("character_name") or ""),
            bot_aliases=list(meta.get("bot_aliases") or []),
            user_input=context.query,
            persona_text=context.persona_text or "无已存储画像",
            session_guidance=context.session_guidance,
            session_guidance_chat_stream_id=(context.session_guidance_chat_stream_id),
            session_guidance_resolution_status=(
                context.session_guidance_resolution_status
            ),
            history_header=context.history_header,
            history_messages=context.history_messages,
            summary_context=str(meta.get("summary_context") or ""),
            memory_recall_context=str(meta.get("memory_recall_context") or ""),
            project_context=str(meta.get("project_context") or ""),
            runtime_tool_prompt=context.runtime_tool_prompt,
            effort_constraint=context.effort_constraint,
            trace_id=context.trace_id,
            run_id=context.run_id,
            is_group=context.is_group,
            is_super_user=context.is_super_user is True,
            group_profile_context=str(meta.get("group_profile_context") or ""),
            tool_schemas=tool_schemas,
            debug={"context_debug": meta.get("context_debug") or {}},
            audit_failure_policy=self._prompt_v2_audit_failure_policy(),
        )

    from nanobot_kt.reply_contract import ALLOWED_SEND_MODES as _ALLOWED_SEND_MODES

    def _extract_reply_from_tool_output(self, session_id: str = "") -> str:
        """从 conversation 中提取 reply() / no_reply() 工具的结构化输出。

        返回 reply 文本内容；同时将 reply_meta 存入 per-session dict。
        如果是 no_reply() 工具，则记录 no_reply 标志并返回空字符串。
        """
        if all(getattr(self, name, None) is None for name in ("_runtime", "_agent")):
            return ""
        try:
            from nanobot_kt.reply_contract import extract_reply_tool_output

            messages = self._require_runtime().read_conversation()
            # ReplyDebug: 临时诊断日志——确认 conversation 尾部是否有 tool 消息
            logger.debug("[ReplyDebug] conversation messages=%d", len(messages))
            for i, msg in enumerate(messages[-8:]):
                role = _conversation_msg_role(msg)
                text = _conversation_msg_content(msg)
                logger.debug(
                    "[ReplyDebug] tail[%d] role=%s len=%d head=%r",
                    i,
                    role,
                    len(text),
                    text[:300],
                )
            result = extract_reply_tool_output(messages)
            if result.no_reply:
                if session_id:
                    store = self._reply_meta_store()
                    entry = store.get(session_id, {})
                    entry["_no_reply"] = True
                    entry["_no_reply_reason"] = result.no_reply_reason
                    store[session_id] = entry
                logger.info(
                    "[Reply] no_reply tool called, reason=%s", result.no_reply_reason
                )
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

            if all(getattr(self, name, None) is None for name in ("_runtime", "_agent")):
                return {}
            messages = self._require_runtime().read_conversation()
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
                    if reply_tool_call_count is None
                    else reply_tool_call_count
                )
                no_reply_tool_call_count = (
                    int(real_counts.get("no_reply_tool_call_count", 0) or 0)
                    if no_reply_tool_call_count is None
                    else no_reply_tool_call_count
                )
                structured_fallback_count = (
                    int(real_counts.get("structured_fallback_count", 0) or 0)
                    if structured_fallback_count is None
                    else structured_fallback_count
                )
                total_final_action_count = (
                    real_total
                    if total_final_action_count is None
                    else total_final_action_count
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
        runtime_context: RequestRuntimeContext,
        trace_id: str,
        run_id: str,
        llm_source: str = "replyer",
        cache_context: dict[str, Any] | None = None,
    ) -> tuple[Any, str]:
        retry_prompt = self._build_reply_contract_retry_prompt(raw_model_output)

        self._output.clear()
        from core.final_tools import final_tools_scope
        from core.llm_trace_context import llm_trace_scope
        from core.tool_execution_policy import (
            FINAL_ACTION_TOOLS,
            final_action_only_scope,
        )
        from core.tool_plan import (
            get_current_tool_plan,
            restrict_tool_plan,
            tool_plan_scope,
        )

        current_plan = get_current_tool_plan()
        if current_plan is None:
            raise RuntimeError("最终回复阶段缺少请求级 ToolPlan")
        final_action_plan = restrict_tool_plan(
            current_plan,
            FINAL_ACTION_TOOLS,
            disabled_reason="最终回复阶段只允许 reply/no_reply",
        )
        retry_source = f"{llm_source}.reply_contract_retry"
        with (
            tool_plan_scope(final_action_plan),
            final_tools_scope(final_action_plan),
            final_action_only_scope(),
            llm_trace_scope(
                trace_id=trace_id,
                run_id=run_id,
                source=retry_source,
                phase="agent.reply_contract_retry",
                cache_context=cache_context,
            ),
        ):
            turn = await self._require_runtime().run_event(
                AgentTurnRequest(
                    context=runtime_context,
                    content=retry_prompt,
                    stream=False,
                    kind=RuntimeTurnKind.USER_INPUT,
                ),
                build_runtime_event_handler(self),
            )
        retry_result = turn.raw_result
        retry_response = self._output.get_response()
        if not retry_response and retry_result:
            retry_response = str(retry_result)
        return retry_result, retry_response

    def _parse_structured_final_action(self, buffer_text: str) -> dict | None:
        from nanobot_kt.reply_contract import parse_structured_final_action

        return parse_structured_final_action(buffer_text)

    def _extract_last_rich_tool_output(
        self,
        allowed_report_kinds: tuple[str, ...] = ("ai_daily", "group_analysis"),
    ) -> RichTerminalOutput | None:
        if all(getattr(self, name, None) is None for name in ("_runtime", "_agent")):
            return None

        try:
            from nanobot_kt.reply_contract import extract_rich_terminal_output

            payloads = self._require_runtime().read_conversation()
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
            event_content = make_multimodal_content(
                prompt_event_content, images=image_parts
            )
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

    def _set_runtime_model_route(
        self,
        target_model: str,
        route_plan: Any,
    ) -> RuntimeModelRoute:
        return set_bridge_runtime_model_route(
            self,
            target_model,
            route_plan,
            unavailable_error=BridgeUnavailableError,
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
        runtime_context: RequestRuntimeContext,
        runtime_attributes: tuple[RuntimeAttribute, ...] = (),
        stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
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
        next_turn_kind = RuntimeTurnKind.USER_INPUT
        cache_context = build_bridge_llm_cache_context(meta, session_id)

        runtime_event_handler = build_runtime_event_handler(
            self,
            stream_queue if bool(meta.get("stream")) else None,
        )

        for attempt in range(max_attempts):
            self._output.clear()
            turn_kind = next_turn_kind
            next_turn_kind = RuntimeTurnKind.USER_INPUT

            # Get next model from ordered list
            try:
                candidate = next(model_iterator)
                selected_candidate = candidate
                target_model = candidate["id"]
            except StopIteration:
                logger.warning(
                    f"[Model Router] No more candidates after {attempt} attempts"
                )
                break
            attempts = attempt + 1
            health_status = "pending"

            candidate_route_plan = candidate.get("_route_plan") or route_plan
            attempt_cache_context = build_attempt_cache_context(cache_context, candidate_route_plan)
            meta["_prompt_cache_context"] = attempt_cache_context
            runtime_route = self._set_runtime_model_route(
                target_model,
                candidate_route_plan,
            )
            attempt_context = bind_native_recovery_model_plan(
                getattr(self, "runtime_kind", AgentRuntimeKind.KT),
                runtime_context,
                runtime_route,
            )
            logger.info(
                f"[Model Router] Attempt {attempt + 1}: {target_model} "
                f"(intel={candidate.get('intelligence')}, "
                f"cost={candidate.get('cost_input_1m')})"
            )

            runtime_error_text = ""
            try:
                logger.info(
                    "[NanobotBridge] Calling AgentRuntimePort (Attempt %d, kind=%s)...",
                    attempt + 1,
                    turn_kind.value,
                )
                from core.llm_trace_context import llm_trace_scope

                with llm_trace_scope(
                    trace_id=trace_id,
                    run_id=run_id,
                    source=reply_llm_source,
                    phase=(
                        "agent.tool_round"
                        if attempt == 0
                        else "model.route_retry"
                    ),
                    route_attempt_index=attempt + 1,
                    cache_context=attempt_cache_context,
                ):
                    turn_request = AgentTurnRequest(
                        context=attempt_context,
                        content=(
                            event_content
                            if turn_kind is RuntimeTurnKind.USER_INPUT
                            else ""
                        ),
                        stream=bool(meta["stream"]),
                        kind=turn_kind,
                        event_attributes=runtime_attributes,
                    )
                    runtime = self._require_runtime()
                    turn = await runtime.run_event(
                        turn_request,
                        runtime_event_handler,
                    )
                result = turn.raw_result
                logger.info(
                    "[NanobotBridge] AgentRuntimePort returned: type=%s",
                    type(result),
                )
            except Exception as e:
                from core.run_ledger.contracts import (
                    find_run_ledger_authority_error,
                )

                if isinstance(e, AgentRuntimeAmbiguousError):
                    # 部分输出或副作用结果未知时，禁止切换候选模型重放本轮；
                    # 即使因果链内含 Ledger 错误，也必须保留 ambiguous 终态。
                    raise
                authority_failure = find_run_ledger_authority_error(e)
                if authority_failure is not None:
                    raise authority_failure
                logger.error(
                    f"[NanobotBridge] Agent processing error: {e}", exc_info=True
                )
                runtime_error_text = f"\n[系统内部错误] {str(e)}"

            response = self._output.get_response()
            if runtime_error_text:
                response = f"{response}{runtime_error_text}"
            logger.info(
                f"[NanobotBridge] Attempt {attempt + 1} response: "
                f"len={len(response)}, empty={not response.strip()}, "
                f"has_sys_err={'[系统内部错误]' in response}, "
                f"has_tool_err={'[工具错误]' in response}, "
                f"preview={response[:100] if response else '(EMPTY)'}"
            )
            # 只接受绑定真实工具调用的结构化富结果，不从文本或缓存猜测 HTML。
            terminal_output = self._extract_last_rich_tool_output()
            if terminal_output:
                logger.info(
                    "[NanobotBridge] Using preserved tool HTML output"
                )
                response = terminal_output.html
                await record_candidate_health(
                    tracker, candidate, target_model, "success", session_id
                )
                health_status = "success"
                break

            reply_text = self._extract_reply_from_tool_output(session_id)
            if self.is_no_reply_session(session_id):
                logger.info("[NanobotBridge] no_reply() called, stopping model loop")
                await record_candidate_health(
                    tracker, candidate, target_model, "success", session_id
                )
                health_status = "success"
                break
            if reply_text:
                from core.reply_postprocess import strip_chat_end_punct

                reply_text = strip_chat_end_punct(reply_text)
                logger.info(
                    "[NanobotBridge] reply() called len=%d, stopping model loop",
                    len(reply_text),
                )
                await record_candidate_health(
                    tracker, candidate, target_model, "success", session_id
                )
                health_status = "success"
                break

            attempt_outcome = classify_attempt_outcome(response)
            if attempt_outcome == "failure":
                logger.warning(
                    f"[NanobotBridge] Framework error. Recording failure for {target_model}"
                )
                await record_candidate_health(
                    tracker, candidate, target_model, "failure", session_id
                )
                health_status = "failure"

                # reasoning_content: 只 ban 出错的特定模型，不波及同厂商其他模型
                if "reasoning_content" in response:
                    logger.warning(
                        "[NanobotBridge] reasoning_content — banning %s only",
                        target_model,
                    )

                if attempt >= max_attempts - 1:
                    break

                # Conversation rollback logic：只通过 Runtime Port 读写消息。
                tool_results_preserved = False
                messages = self._require_runtime().read_conversation()
                user_idx = next(
                    (
                        index
                        for index in range(len(messages) - 1, -1, -1)
                        if messages[index].role == "user"
                    ),
                    -1,
                )
                last_tool_idx = next(
                    (
                        index
                        for index in range(len(messages) - 1, user_idx, -1)
                        if messages[index].role == "tool"
                    ),
                    -1,
                )
                if last_tool_idx >= 0:
                    self._require_runtime().replace_conversation(
                        tuple(messages[: last_tool_idx + 1])
                    )
                    logger.info(
                        "[NanobotBridge] Preserved tool results (idx≤%d)",
                        last_tool_idx,
                    )
                    next_turn_kind = RuntimeTurnKind.CONTINUE
                    tool_results_preserved = True
                elif user_idx >= 0:
                    self._require_runtime().replace_conversation(
                        tuple(messages[:user_idx])
                    )
                if not tool_results_preserved:
                    next_turn_kind = RuntimeTurnKind.USER_INPUT
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
        trace_id: str,
        run_id: str,
        reply_llm_source: str,
        runtime_context: RequestRuntimeContext | None = None,
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
        _ = (result, target_model, query, event_content)

        buffer_text = (
            self._output.get_response() if hasattr(self._output, "get_response") else ""
        )
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
                runtime_context=(
                    runtime_context
                    or build_fallback_request_runtime_context(
                        session_id=session_id,
                        trace_id=trace_id,
                        run_id=run_id,
                    )
                ),
                trace_id=trace_id,
                run_id=run_id,
                llm_source=reply_llm_source,
                cache_context=build_bridge_llm_cache_context(meta, session_id, drop_none=True),
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
            from core.run_ledger.contracts import (
                find_run_ledger_authority_error,
            )

            authority_failure = find_run_ledger_authority_error(exc)
            if authority_failure is not None:
                raise authority_failure
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
        if all(getattr(self, name, None) is None for name in ("_runtime", "_agent")):
            return "Error: Agent not initialized"
        if not str(session_id or "").strip():
            raise ValueError("session_id 不能为空")

        # SessionRuntime: 按 session 分锁，同 session 串行，不同 session 并发
        import time as _time

        if not hasattr(self, "_session_locks"):
            self._session_locks = {}
        sess_lock = self._session_locks.setdefault(session_id, asyncio.Lock())

        # Interrupt: 如果该 session 正在处理，发 interrupt 信号
        if sess_lock.locked():
            interrupted = self._require_runtime().interrupt(
                reason="same_session_reentry"
            )
            logger.info(
                "[SessionRuntime] Interrupt requested for session=%s accepted=%s",
                session_id,
                interrupted,
            )

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
                    group_id = session_group_id[len("group_") :]
            prompt_engine = self._resolve_prompt_runtime_engine(meta)
            prompt_mode = "prompt"
            prompt_key = "chat_group" if is_group else "chat_private"
            from core.tracing import RunTracer, new_trace_id
            from core.tracing_context import set_trace_context

            trace_id = str(meta.get("trace_id") or new_trace_id())
            meta["trace_id"] = trace_id
            trigger_policy, run_meta = prepare_bridge_run_meta(
                meta, sender_name, is_group, prompt_engine, platform,
                chat_type, group_id, user_id, session_id,
            )
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
            trigger_policy.project_safe_trace_meta(run_meta)
            trace_tokens = set_trace_context(trace_id, run_handle.run_id)
            correlation_tokens = bind_bridge_runtime_correlation(
                meta, session_id, trace_id, run_handle.run_id
            )
            trace_finalizer = BridgeTraceFinalizer(
                run_id=run_handle.run_id,
                trace_tokens=trace_tokens,
                correlation_tokens=correlation_tokens,
                run_meta=run_meta,
                started_at=t_start,
                now=_time.time,
                task_lease=run_handle.task_lease,
            )
            request_scope.bind_trace_finalizer(trace_finalizer)
            await bind_run_task_owner(request_scope, run_handle.task_lease)
            from core.tool_execution_policy import (
                ToolExecutionState,
                set_current_tool_execution_state,
            )

            tool_execution_token = set_current_tool_execution_state(
                ToolExecutionState(
                    request_id=str(meta.get("message_id") or run_handle.run_id)
                )
            )
            trace_finalizer.set_tool_execution_token(tool_execution_token)

            self._prepare_output_for_request(
                stream_queue=stream_queue,
                stream_enabled=meta["stream"],
            )
            # reply meta store 是 per-session 缓存,只靠成功路径 pop 清理;
            # 上一请求若在错误/取消路径未 pop,残留的 _no_reply 会静默吞掉
            # 本请求。每请求开始处无条件清一次,保证从干净状态出发。
            if session_id:
                self._reply_meta_store().pop(session_id, None)
            logger.info(
                "[SessionRuntime] START session=%s user=%s query_len=%d",
                session_id,
                user_id,
                len(query),
            )

            # 每轮重建 conversation——DB 是唯一事实源，不在内存中跨请求复用
            runtime = self._require_runtime()
            before_len = len(runtime.read_conversation())
            runtime.replace_conversation(())
            if before_len:
                logger.info("[SessionRuntime] Reset conversation: %d→0", before_len)
            # --- Prompt runtime 输入：只收集结构化上下文，不在 bridge 手工注入 prompt ---
            persona_text = str(meta.get("persona_text", "")).strip()
            history_messages = meta.get("history_messages", [])
            history_header = str(meta.get("history_header", "")).strip()
            is_super_user = meta.get("is_superuser") is True
            effort_constraint = str(meta.get("effort_constraint", "")).strip()
            runtime_preset = str(meta.get("runtime_preset", "full")).strip()
            runtime_chat_type = (
                "private_superuser" if (not is_group and is_super_user) else chat_type
            )
            user_id = str(
                meta.get("user_id")
                or user_id
                or ("" if is_group else session_id)
                or ""
            ).strip()
            from core.final_tools import set_current_final_tools
            from core.tool_plan import set_current_tool_plan
            from core.runtime_tool_service import record_runtime_tool_decision
            from nanobot_kt.skill_runtime import build_request_extension_binding
            from core.uow import UnitOfWork
            with UnitOfWork() as uow:
                guidance = resolve_session_guidance(
                    uow.db,
                    platform=platform,
                    chat_type=chat_type,
                    session_id=session_id,
                )
                run_meta.update(guidance.debug)
                extension_binding = await build_request_extension_binding(
                    db=uow.db,
                    metadata=meta,
                    platform=platform,
                    runtime_chat_type=runtime_chat_type,
                    is_group=is_group,
                    group_id=group_id,
                    user_id=user_id,
                    session_id=session_id,
                    runtime_preset=runtime_preset,
                    agent_id=bridge_agent_id(self),
                    query=query,
                    agent=self._agent, cleanup_registrar=request_scope.bind_async_cleanup,
                )
                tool_plan = trigger_policy.restrict_tool_plan(extension_binding.tool_plan)
                meta["project_context"] = extension_binding.project_context
                run_meta.update(extension_binding.run_meta_update)
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
                if decision_recorded or extension_binding.persistence_pending:
                    try:
                        uow.commit()
                    except Exception as e:
                        uow.rollback()
                        logger.warning(
                            "[Bridge] failed to commit runtime tool decision: %s", e
                        )
            final_tools_token = set_current_final_tools(tool_plan)
            trace_finalizer.set_tool_tokens(final_tools_token=final_tools_token)
            tool_plan_token = set_current_tool_plan(tool_plan)
            trace_finalizer.set_tool_tokens(tool_plan_token=tool_plan_token)
            enabled = dict(tool_plan.enabled or {})
            disabled = dict(tool_plan.disabled or {})
            runtime_tool_prompt = tool_plan.runtime_tool_prompt
            effective_tools = sorted(tool_plan.executable_tool_names)
            logger.info(
                "[Bridge] runtime_preset=%s chat=%s effective=%s tool_plan=%s",
                runtime_preset,
                runtime_chat_type,
                effective_tools,
                tool_plan.sha256[:12],
            )
            meta["_runtime_preset"] = runtime_preset
            meta["_disabled_tools"] = {k: v for k, v in disabled.items()}
            memory_runtime = getattr(self, "_memory_runtime", None)
            if memory_runtime is not None and memory_runtime.initialized:
                from core.memory_provider import (
                    MemoryPrefetchContext,
                    MemorySessionContext,
                    MemoryToolSchemaContext,
                )

                request_id = str(meta.get("message_id") or run_handle.run_id)
                principal_id = (
                    f"{platform}:group:{group_id}"
                    if is_group
                    else f"{platform}:user:{user_id}"
                )
                memory_session = MemorySessionContext(
                    session_id=session_id,
                    principal_id=principal_id,
                    reason="chat_request",
                )
                await memory_runtime.on_session_start(memory_session)
                from nanobot_kt.memory_runtime import (
                    bind_memory_tool_runtime,
                    reset_memory_tool_runtime,
                )

                memory_binding_token = bind_memory_tool_runtime(
                    memory_runtime,
                    request_id=request_id,
                    session_id=session_id,
                    principal_id=principal_id,
                )

                async def reset_memory_binding() -> None:
                    reset_memory_tool_runtime(memory_binding_token)

                request_scope.bind_async_cleanup(reset_memory_binding)

                async def close_memory_session() -> None:
                    await memory_runtime.on_session_end(
                        MemorySessionContext(
                            session_id=session_id,
                            principal_id=principal_id,
                            reason="chat_request_complete",
                        )
                    )

                request_scope.bind_async_cleanup(close_memory_session)
                provider_schemas = await memory_runtime.tool_schemas(
                    MemoryToolSchemaContext(
                        request_id=request_id,
                        session_id=session_id,
                        principal_id=principal_id,
                        metadata={"tool_schemas": tool_plan.sent_tool_schemas},
                    )
                )
                prefetched = await memory_runtime.prefetch(
                    MemoryPrefetchContext(
                        request_id=request_id,
                        session_id=session_id,
                        principal_id=principal_id,
                        query=query,
                        limit=10,
                    )
                )
                run_meta.update(
                    {
                        "memory_provider_schema_count": len(provider_schemas),
                        "memory_provider_prefetch_count": len(prefetched),
                    }
                )
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
            meta["_prompt_cache_context"] = dict(prompt_build.cache_context)
            self._last_prompt_render_meta = prompt_build.trace_fields
            RunTracer.update_prompt_source(
                run_handle.run_id,
                **self._last_prompt_render_meta,
            )
            prompt_messages = tuple(
                RuntimeMessage(
                    role=str(message.get("role") or "system"),
                    content=message.get("content") or "",
                    name=str(message.get("name") or ""),
                    tool_call_id=str(message.get("tool_call_id") or ""),
                )
                for message in prompt_build.pre_event_messages
                if isinstance(message, dict)
            )
            applied_messages = runtime.replace_conversation(prompt_messages)
            if applied_messages:
                logger.info(
                    "[PromptRuntime] built key=%s mode=%s source=%s pre_messages=%d sha=%s",
                    prompt_build.prompt_key,
                    prompt_build.prompt_mode,
                    prompt_build.prompt_source,
                    applied_messages,
                    prompt_build.prompt_sha256[:12],
                )
            logger.debug(
                f"[NanobotBridge] Agent initialized: {self._agent is not None}"
            )
            logger.debug(f"[NanobotBridge] Output module: {self._output}")
            event_payload = await self._prepare_event_payload(
                prompt_event_content=prompt_build.event_content,
                files=meta.get("files"),
                tool_schemas=prompt_input.tool_schemas,
            )
            event_payload.required_capabilities.update(
                dict(meta.get("required_capabilities") or {})
            )
            image_parts = event_payload.image_parts
            event_content = event_payload.event_content
            required_capabilities = event_payload.required_capabilities
            memory_snapshot = bridge_memory_registry_snapshot(self)
            runtime_plans = build_request_runtime_plans(
                agent_id=bridge_agent_id(self),
                runtime_kind=getattr(
                    self,
                    "runtime_kind",
                    AgentRuntimeKind.KT,
                ),
                platform=platform,
                user_id=user_id,
                group_id=group_id,
                session_id=session_id,
                is_group=is_group,
                runtime_id=self._require_runtime().runtime_id,
                prompt_key=prompt_build.prompt_key,
                prompt_sha256=prompt_build.prompt_sha256,
                tool_plan=tool_plan,
                memory_registry_generation=memory_snapshot.generation,
                memory_registry_sha256=memory_snapshot.sha256,
            )
            runtime_context = build_request_runtime_context(
                request_id=str(meta.get("message_id") or run_handle.run_id),
                agent_id=bridge_agent_id(self),
                platform=platform,
                user_id=user_id,
                group_id=group_id,
                session_id=session_id,
                is_group=is_group,
                is_super_user=is_super_user,
                trace_id=trace_id,
                run_id=run_handle.run_id,
                turn_id=str(
                    meta.get("message_id")
                    or meta.get("request_id")
                    or run_handle.run_id
                ),
                correlation_id=str(
                    meta.get("correlation_id") or trace_id
                ),
                message_id=str(meta.get("message_id") or ""),
                capabilities=required_capabilities,
                prompt_key=prompt_build.prompt_key,
                prompt_sha256=prompt_build.prompt_sha256,
                tool_plan=tool_plan,
                runtime_plans=runtime_plans,
                session_goal_plan=extension_binding.session_goal_plan,
                skill_plan=extension_binding.skill_plan,
            )
            runtime_attributes_list = [
                RuntimeAttribute("runtime_chat_type", runtime_chat_type),
                RuntimeAttribute("actor_user_id", user_id),
                RuntimeAttribute("group_id", group_id),
                RuntimeAttribute("sender_name", sender_name),
            ]
            runtime_attributes_list.extend(extension_binding.runtime_attributes)
            runtime_attributes = tuple(runtime_attributes_list)

            # --- Dynamic Model Routing (new priority-ordered system) ---
            route_client = None
            raw_query = str(meta.get("raw_query", query)).strip() or query
            reply_llm_source = (
                "replyer.group_chat" if is_group else "replyer.private_chat"
            )
            try:
                tracker = NewAPIClient.get_failure_tracker()
            except Exception as e:
                tracker = None
                logger.warning(f"[Model Router] Failure tracker unavailable: {e}")

            # 从 WebUI route 读取 provider 配置，让 route provider 控制实际 API 调用
            try:
                from nanobot_kt.model_runtime import (
                    resolve_gateway_reply_route_plans,
                )

                route_plans = resolve_gateway_reply_route_plans(
                    default_base_url=NEW_API_BASE_URL,
                    default_api_key=NEW_API_KEY,
                    session_id=session_id,
                    gateway_binding_id=str(
                        meta.get("gateway_binding_id") or ""
                    ),
                )
                route_plan = route_plans[0]
            except RuntimeError as e:
                # 内部错误文本绝不能当作正常回复出站(会绕过 reply contract
                # 且写入 ConversationTurn),与"无候选模型"路径一致静默返回。
                logger.error("[Model Router] reply route disabled: %s", e)
                trace_finalizer.finish("error", error=str(e))
                return ""
            _route_provider_id = route_plan.provider_id
            _route_registry_provider = route_plan.registry_provider
            _route_timeout = route_plan.timeout
            _route_temperature = route_plan.temperature
            _route_max_tokens = route_plan.max_tokens
            _route_enable_thinking = route_plan.enable_thinking
            _client_base_url = route_plan.base_url
            _client_api_key = route_plan.api_key
            logger.info(
                "[Model Router] route provider=%s driver=%s preset=%s registry_provider=%s base_url=%s timeout=%s temperature=%s max_tokens=%s enable_thinking=%s",
                _route_provider_id,
                route_plan.driver_type,
                route_plan.profile_id or "(legacy)",
                _route_registry_provider,
                _client_base_url[:80] if _client_base_url else "(empty)",
                _route_timeout,
                _route_temperature,
                _route_max_tokens,
                _route_enable_thinking,
            )

            try:
                if route_plan.profile_id:
                    from nanobot_kt.model_runtime import PresetRouteClient

                    route_client = PresetRouteClient(route_plans)
                else:
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
                    logger.info(
                        "[Model Router] using metadata complexity=%s", complexity
                    )
                else:
                    complexity = route_client.estimate_complexity(
                        messages_for_routing, tools=[{}]
                    )
                from core.settings_service import settings

                reply_intel_floor_setting = settings.get_int(
                    "model.reply_intel_floor",
                    12,
                )
                reply_intel_boost = settings.get_int(
                    "model.reply_intel_boost",
                    2,
                )
                reply_max_cost = settings.get_float(
                    "model.reply_max_cost",
                    10.0,
                )
                base_intel_floor = max(1, complexity - 1)
                reply_intel_floor = max(
                    base_intel_floor + max(0, reply_intel_boost),
                    max(1, reply_intel_floor_setting),
                )
                automatic_candidates = route_client.get_ordered_candidates(
                    provider=_route_registry_provider,
                    intel_floor=reply_intel_floor,
                    max_cost=reply_max_cost,
                    required_capabilities=required_capabilities,
                )

                manual_reply_model = (
                    ""
                    if route_plan.profile_id
                    else str(
                        meta.get("reply_model")
                        or settings.get("model.reply")
                        or ""
                    ).strip()
                )
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
                        manual_reply_model,
                        complexity,
                        base_intel_floor,
                        reply_intel_floor,
                    )

                candidates = merge_model_candidates(
                    preferred_candidate,
                    automatic_candidates,
                )
                logger.info(
                    "[ReplyModel] auto complexity=%s base_floor=%s reply_floor=%s max_cost=%.3f required=%s",
                    complexity,
                    base_intel_floor,
                    reply_intel_floor,
                    reply_max_cost,
                    required_capabilities,
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
                            max_cost=reply_max_cost,
                            required_capabilities=required_capabilities,
                        )
                        logger.info(
                            "[Model Router] degraded text-only candidates=%s",
                            [
                                (c.get("id", "")[:30], c.get("intelligence"))
                                for c in candidates[:8]
                            ],
                        )
                    except Exception as e:
                        logger.error(
                            "[Model Router] degraded text-only route failed: %s",
                            e,
                            exc_info=True,
                        )

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
                runtime_context=runtime_context,
                runtime_attributes=runtime_attributes,
                stream_queue=stream_queue,
            )
            response = model_loop.response
            result = model_loop.result
            target_model = model_loop.target_model
            terminal_output = model_loop.terminal_output

            if not target_model:
                logger.warning("[Model Router] No model attempt was made")
                trace_finalizer.finish("empty", model="")
                return ""

            logger.info("[NanobotBridge] Checking runtime output...")

            # 如果公开输出为空，尝试从 Runtime 返回值获取
            if not response and result:
                logger.info(
                    "[NanobotBridge] Output empty, using runtime return value"
                )
                response = str(result) if result else ""

            # 事后只补提取已验证的富结果，不从 assistant 文本猜测 HTML。
            final_terminal = self._extract_last_rich_tool_output()
            if final_terminal and final_terminal.html.strip() != response.strip():
                logger.info("[NanobotBridge] post-loop HTML replacement")
                terminal_output = final_terminal
                response = final_terminal.html

            logger.info(
                "[NanobotBridge] After processing: response_len=%d",
                len(response),
            )
            if response:
                logger.debug(f"[NanobotBridge] Response preview: {response[:200]}")
            else:
                logger.warning("[NanobotBridge] EMPTY RESPONSE!")
                logger.warning(
                    "[NanobotBridge] runtime_result_type=%s",
                    type(result).__name__ if result is not None else "None",
                )

            reply_resolution = await self._check_reply_contract(
                session_id=session_id,
                response=response,
                result=result,
                terminal_output=terminal_output,
                target_model=target_model,
                query=query,
                meta=meta,
                event_content=event_content,
                trace_id=trace_id,
                run_id=run_handle.run_id,
                reply_llm_source=reply_llm_source,
                runtime_context=runtime_context,
            )
            response = reply_resolution.response
            reply_source = reply_resolution.agent_result
            if target_model:
                if (
                    reply_resolution.finish_status in {"success", "no_reply"}
                    and model_loop.health_status != "success"
                ):
                    await record_candidate_health(
                        tracker, model_loop.selected_candidate,
                        target_model, "success", session_id,
                    )
                    model_loop.health_status = "success"
                elif (
                    reply_resolution.finish_status in {"suppressed", "error"}
                    and model_loop.health_status != "failure"
                ):
                    await record_candidate_health(
                        tracker, model_loop.selected_candidate,
                        target_model, "failure", session_id,
                    )
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
                logger.warning(
                    "[NanobotBridge] KT agent returned empty response after strip"
                )
                trace_finalizer.finish("empty", model=locals().get("target_model", ""))
                return ""

            elapsed_ms = int((_time.time() - t_start) * 1000)
            response_source = reply_source
            logger.info(
                "[SessionRuntime] DONE session=%s latency=%dms resp_len=%d source=%s",
                session_id,
                elapsed_ms,
                len(response),
                response_source,
            )

            # 惰性清理：session_locks 过大时扫一遍过期锁（无等待者 = unlocked）
            if len(self._session_locks) > 200:
                stale_sids = [
                    sid
                    for sid, lock in list(self._session_locks.items())
                    if not lock.locked()
                ]
                for sid in stale_sids:
                    self._session_locks.pop(sid, None)
                if stale_sids:
                    logger.info(
                        "[SessionRuntime] Cleaned %d idle session locks",
                        len(stale_sids),
                    )

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
    def agent(self) -> Any | None:
        """Access the underlying KT agent for advanced operations."""
        return self._agent


class BridgeLifecycleState(str, Enum):
    """Bridge 与全局 Runtime 共用的显式生命周期状态。"""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class BridgeUnavailableError(RuntimeError):
    """Bridge 尚未就绪或已进入关闭流程。"""


class NanobotBridgePool(MessageContractBridgeMixin):
    """按会话隔离并固定 Agent Runtime，避免跨 Runtime 隐式重放。"""

    def __init__(
        self,
        creature_path: str = "creatures/nanobot",
        *,
        selection_policy: AgentRuntimeSelectionPolicy | None = None,
        bridge_factory: Callable[[AgentRuntimeKind], NanobotBridge] | None = None,
    ):
        self.creature_path = creature_path
        # 直接构造 Pool 的旧调用仍保持 KT 行为；生产 Composition Root 必须
        # 显式注入 Native-default 策略，避免测试夹具和兼容调用被暗中改写。
        if selection_policy is None:
            selection_policy = compatibility_runtime_selection_policy()
        self._selection_policy = selection_policy
        self._bridge_factory = bridge_factory or partial(
            build_child_bridge,
            NanobotBridge,
            self.creature_path,
        )
        self._bridges: dict[str, NanobotBridge] = {}
        self._bridge_runtime_kinds: dict[str, AgentRuntimeKind] = {}
        self._last_runtime_kinds: dict[str, AgentRuntimeKind] = {}
        self._bridge_last_used: dict[str, float] = {}
        self._bridge_inflight: dict[str, int] = {}
        self._stop_tasks: set[asyncio.Task] = set()
        self._create_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._lifecycle_state = BridgeLifecycleState.NEW
        self.BRIDGE_TTL_SECONDS = 600  # 10 分钟无使用则回收
        self.BRIDGE_STOP_TIMEOUT_SECONDS = 30  # stop 等待 inflight 的上限，超时强制回收

    def create_isolated_bridge(self) -> NanobotBridge:
        return self._bridge_factory(self._selection_policy.default_kind)

    async def start(self) -> None:
        async with self._create_lock:
            if self._lifecycle_state is BridgeLifecycleState.RUNNING:
                return
            if self._lifecycle_state is not BridgeLifecycleState.NEW:
                raise BridgeUnavailableError(
                    f"BridgePool 无法从 {self._lifecycle_state.value} 状态启动"
                )
            self._lifecycle_state = BridgeLifecycleState.STARTING
            self._lifecycle_state = BridgeLifecycleState.RUNNING
        logger.info("[NanobotBridgePool] started")

    @property
    def lifecycle_state(self) -> BridgeLifecycleState:
        return self._lifecycle_state

    @property
    def _tool_registry_info(self) -> dict:
        """从第一个 child bridge 获取工具注册表信息。"""
        for b in self._bridges.values():
            info = getattr(b, "_tool_registry_info", {})
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
                    if self._lifecycle_state is BridgeLifecycleState.STOPPED:
                        return
                    if self._lifecycle_state is BridgeLifecycleState.NEW:
                        self._lifecycle_state = BridgeLifecycleState.STOPPED
                        return
                    if self._lifecycle_state is not BridgeLifecycleState.RUNNING:
                        raise BridgeUnavailableError(
                            f"BridgePool 无法从 {self._lifecycle_state.value} 状态关闭"
                        )
                    self._lifecycle_state = BridgeLifecycleState.STOPPING

                deadline = _t.monotonic() + max(
                    0.0, float(self.BRIDGE_STOP_TIMEOUT_SECONDS)
                )
                forced = False
                while True:
                    async with self._create_lock:
                        inflight = dict(self._bridge_inflight)
                        if not inflight:
                            bridges = list(self._bridges.values())
                            self._bridges.clear()
                            self._bridge_runtime_kinds.clear()
                            self._last_runtime_kinds.clear()
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
                            self._bridge_runtime_kinds.clear()
                            self._last_runtime_kinds.clear()
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
                    await asyncio.gather(
                        *(bridge.stop() for bridge in bridges), return_exceptions=True
                    )
                if self._stop_tasks:
                    await asyncio.gather(
                        *list(self._stop_tasks), return_exceptions=True
                    )
                if forced:
                    logger.info(
                        "[NanobotBridgePool] stopped (forced after inflight timeout)"
                    )
                else:
                    logger.info("[NanobotBridgePool] stopped")
            finally:
                async with self._create_lock:
                    self._lifecycle_state = BridgeLifecycleState.STOPPED

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

    async def _get_or_create_bridge_locked(
        self,
        key: str,
        selection: AgentRuntimeSelection,
    ) -> NanobotBridge:
        import time as _t

        if self._lifecycle_state is not BridgeLifecycleState.RUNNING:
            raise BridgeUnavailableError(
                f"BridgePool 当前不可用: {self._lifecycle_state.value}"
            )

        # TTL 清理只回收空闲 bridge；正在处理请求的 bridge 由 release 刷新 last_used。
        now = _t.time()
        stale = [
            k
            for k, ts in list(self._bridge_last_used.items())
            if now - ts > self.BRIDGE_TTL_SECONDS
            and self._bridge_inflight.get(k, 0) <= 0
        ]
        for k in stale:
            b = self._bridges.pop(k, None)
            self._bridge_runtime_kinds.pop(k, None)
            self._bridge_last_used.pop(k, None)
            self._bridge_inflight.pop(k, None)
            if b:
                self._track_stop_task(b, k)
        if stale:
            logger.info("[BridgePool] Cleaned %d stale bridges", len(stale))

        from nanobot_kt.bridge_runtime_support import reconcile_selected_bridge

        bridge = await reconcile_selected_bridge(
            self,
            key,
            selection,
            unavailable_error=BridgeUnavailableError,
        )
        self._bridge_last_used[key] = _t.time()
        return bridge

    async def _get_bridge(self, key: str) -> NanobotBridge:
        from nanobot_kt.bridge_runtime_support import select_runtime_for_bridge_key

        selection = select_runtime_for_bridge_key(
            self._selection_policy,
            key,
            session_id=key,
        )
        async with self._create_lock:
            return await self._get_or_create_bridge_locked(key, selection)

    async def _acquire_bridge(
        self,
        key: str,
        selection: AgentRuntimeSelection,
    ) -> NanobotBridge:
        async with self._create_lock:
            bridge = await self._get_or_create_bridge_locked(key, selection)
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
        if self._lifecycle_state is not BridgeLifecycleState.RUNNING:
            raise BridgeUnavailableError(
                f"BridgePool 当前不可用: {self._lifecycle_state.value}"
            )
        key = self._session_key(user_id=user_id, session_id=session_id)
        # 请求进入 Pool 时只选择一次；此后无论启动、模型或工具调用如何失败，
        # 都不会重新选择另一个 Runtime，也不会跨 Runtime 重放本轮副作用。
        from nanobot_kt.bridge_runtime_support import select_runtime_for_bridge_key

        selection = select_runtime_for_bridge_key(
            self._selection_policy,
            key,
            user_id=user_id,
            session_id=session_id,
        )
        bridge = await self._acquire_bridge(key, selection)
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
_bridge_lifecycle_state = BridgeLifecycleState.NEW
_bridge_lifecycle_lock = asyncio.Lock()


def get_bridge_lifecycle_state() -> BridgeLifecycleState:
    """返回进程级 Bridge 生命周期快照，供 readiness 与诊断使用。"""

    return _bridge_lifecycle_state


def get_bridge() -> NanobotBridgePool:
    """只返回已显式启动的全局 Bridge；关闭后禁止惰性复活。"""

    if (
        _bridge_lifecycle_state is not BridgeLifecycleState.RUNNING
        or _bridge is None
        or _bridge.lifecycle_state is not BridgeLifecycleState.RUNNING
    ):
        raise BridgeUnavailableError(
            f"全局 Bridge 当前不可用: {_bridge_lifecycle_state.value}"
        )
    return _bridge


async def init_bridge(
    *,
    selection_policy: AgentRuntimeSelectionPolicy | None = None,
) -> NanobotBridgePool:
    """Initialize and start the global bridge. Called from server.py lifespan."""
    global _bridge, _bridge_lifecycle_state
    async with _bridge_lifecycle_lock:
        if (
            _bridge_lifecycle_state is BridgeLifecycleState.RUNNING
            and _bridge is not None
        ):
            if (
                selection_policy is not None
                and _bridge._selection_policy.policy_sha256
                != selection_policy.policy_sha256
            ):
                raise BridgeUnavailableError(
                    "全局 Bridge 已用不同 Runtime 策略启动；修改策略需要重启"
                )
            return _bridge
        if _bridge_lifecycle_state in {
            BridgeLifecycleState.STARTING,
            BridgeLifecycleState.STOPPING,
        }:
            raise BridgeUnavailableError(
                f"全局 Bridge 正在转换状态: {_bridge_lifecycle_state.value}"
            )

        _bridge_lifecycle_state = BridgeLifecycleState.STARTING
        candidate = NanobotBridgePool(selection_policy=selection_policy)
        try:
            await candidate.start()
        except BaseException:
            _bridge = None
            _bridge_lifecycle_state = BridgeLifecycleState.STOPPED
            raise
        _bridge = candidate
        _bridge_lifecycle_state = BridgeLifecycleState.RUNNING
        return candidate


async def shutdown_bridge() -> None:
    """Shutdown the global bridge. Called from server.py lifespan."""
    global _bridge, _bridge_lifecycle_state
    async with _bridge_lifecycle_lock:
        if _bridge_lifecycle_state is BridgeLifecycleState.STOPPED:
            return
        _bridge_lifecycle_state = BridgeLifecycleState.STOPPING
        current = _bridge
        try:
            if current is not None:
                await current.stop()
        finally:
            _bridge = None
            _bridge_lifecycle_state = BridgeLifecycleState.STOPPED
