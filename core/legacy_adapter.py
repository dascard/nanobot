"""
KohakuTerrarium (KT) Adapter for Nanobot.
This module bridges the KT framework components with the Nanobot SQLite DB and OpenAI-compatible APIs.
"""
import asyncio
import json
import logging
from typing import Any
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.db.models.chat import ChatLog
from core.db.models.persona import Persona, SystemPrompt
from core.json_utils import json_repair as _json_repair
from config import MAX_TOOL_ROUNDS
from core.state_manager import StateManager
from sandbox import AnalysisSandbox
from creatures.nanobot.prompts.skills.news_search.tool import search_and_extract_news
from core.model_provider.chat_runtime import RuntimeChatCompletionClient
from core.model_provider.catalog_runtime import (
    ModelCatalogWriterPort,
    upsert_model_catalog,
)
from foundation.llm.messages import format_openai_messages
from core.persona_preprocess import (
    PersonaStateMachine,
    build_candidate_extraction_prompt,
    filter_user_messages,
    format_candidate_logs,
    get_candidate_extraction_system_prompt,
)

logger = logging.getLogger("nanobot.kt_adapter")

class SQLiteMemory:
    """
    Implements KohakuTerrarium's memory pattern using the Nanobot SQLite database.
    Focuses on 'Session' (historical context) and 'Persona' (user identity).
    
    BUG-03 FIX: Uses factory pattern — creates a fresh Session per operation
    instead of sharing a single Session across async requests.
    """
    def __init__(self):
        pass  # No longer holds a persistent session

    def _get_session(self) -> Session:
        """Create a new session for each operation (thread-safe)."""
        return SessionLocal()

    def close(self):
        pass  # Sessions are now closed per-operation

    def get_recent_context(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        """提取场（Session）的历史记录"""
        db = self._get_session()
        try:
            logs = (
                db.query(ChatLog)
                .filter(ChatLog.session_id == session_id)
                .order_by(ChatLog.id.desc())
                .limit(limit)
                .all()
            )
            logs.reverse()
            return [{"role": log.role, "content": log.content, "sender": log.sender_name} for log in logs]
        finally:
            db.close()

    def get_recent_context_summary(self, session_id: str, limit: int = 20) -> str:
        """提取场的历史记录并格式化为字符串"""
        logs = self.get_recent_context(session_id, limit)
        return "\n".join([
            f"{'User' if log['role'] == 'user' else 'Assistant'}({log['sender'] or ''}): {log['content']}"
            for log in logs
        ])

    def get_user_persona(self, user_id: str) -> str:
        """提取人（User）的画像 (返回 JSON 字符串)"""
        db = self._get_session()
        try:
            persona = db.query(Persona).filter(
                Persona.user_id == user_id,
                Persona.status == "active",
            ).first()
            return persona.persona_json if persona else "{}"
        finally:
            db.close()

    def get_system_prompt(self, user_id: str) -> str:
        """提取系统提示词"""
        db = self._get_session()
        try:
            sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()
            return sys_obj.prompt_text if sys_obj else "你是一个具备自进化能力的智能助手。"
        finally:
            db.close()

    def save_log(self, **kwargs):
        """保存新的对话记录"""
        db = self._get_session()
        try:
            log = ChatLog(**kwargs)
            db.add(log)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("save_log failed; transaction rolled back")
            raise
        finally:
            db.close()

    def get_unprocessed_logs(self, user_id: str) -> list[dict[str, Any]]:
        """提取待进化的原始日志 (返回 detached dicts 以避免跨 session 操作)
        
        Returns list of dicts with keys: id, user_id, role, content, sender_name, session_id, created_at
        """
        from config import EVOLUTION_THRESHOLD
        from core.moderation import is_no_learn_meta
        db = self._get_session()
        try:
            logs = (
                db.query(ChatLog)
                .filter(ChatLog.user_id == user_id, ChatLog.processed == 0)
                .order_by(ChatLog.id.asc())
                .all()
            )
            logs = [
                log for log in logs
                if not is_no_learn_meta(getattr(log, "meta_json", ""))
            ]
            if len(logs) < EVOLUTION_THRESHOLD:
                return []
            # Detach: convert ORM objects to plain dicts before session closes
            return [
                {
                    "id": log.id,
                    "user_id": log.user_id,
                    "role": log.role,
                    "content": log.content,
                    "sender_name": log.sender_name,
                    "session_id": log.session_id,
                    "created_at": log.created_at,
                    "meta_json": getattr(log, "meta_json", "") or "{}",
                }
                for log in logs
            ]
        finally:
            db.close()

    def _get_unprocessed_log_ids(self, user_id: str) -> list[int]:
        """获取未处理日志 ID 列表 (session-safe)"""
        from config import EVOLUTION_THRESHOLD
        db = self._get_session()
        try:
            logs = (
                db.query(ChatLog.id)
                .filter(ChatLog.user_id == user_id, ChatLog.processed == 0)
                .order_by(ChatLog.id.asc())
                .all()
            )
            if len(logs) < EVOLUTION_THRESHOLD:
                return []
            return [log_id for (log_id,) in logs]
        finally:
            db.close()

    def mark_logs_processed(self, log_ids: list[int]):
        """按 ID 批量标记日志为已处理 (session-safe)"""
        if not log_ids:
            return
        db = self._get_session()
        try:
            db.query(ChatLog).filter(ChatLog.id.in_(log_ids)).update(
                {ChatLog.processed: 1}, synchronize_session='fetch'
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("mark_logs_processed failed; transaction rolled back")
            raise
        finally:
            db.close()

    @staticmethod
    def _validated_persona_payload(
        user_id: str,
        persona_json: str | dict[str, Any],
    ) -> dict[str, Any] | None:
        import json as _json

        try:
            data = (
                _json.loads(persona_json)
                if isinstance(persona_json, str)
                else persona_json
            )
        except (_json.JSONDecodeError, TypeError):
            logger.error("Persona update rejected: invalid JSON for user %s", user_id)
            return None
        if not isinstance(data, dict):
            logger.error("Persona update rejected: not dict, user=%s", user_id)
            return None
        if "error" in data or "code" in data:
            logger.error(
                "Persona update rejected: error response, user=%s, preview=%s",
                user_id,
                str(data)[:200],
            )
            return None
        return data

    @staticmethod
    def _upsert_persona_row(
        db: Session,
        *,
        user_id: str,
        data: dict[str, Any],
        now: Any,
    ) -> None:
        persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
        payload = json.dumps(data, ensure_ascii=False)
        if persona_obj:
            persona_obj.persona_json = payload
            if str(persona_obj.status or "") == "archived":
                persona_obj.status = "review"
            persona_obj.updated_at = now
            return
        db.add(Persona(user_id=user_id, persona_json=payload, updated_at=now))

    def update_persona(
        self,
        user_id: str,
        persona_json: str | dict[str, Any],
    ) -> bool:
        """只回写 canonical 画像；不会创建无运行时消费者的 SystemPrompt。"""
        from core.time_utils import db_now_naive

        data = self._validated_persona_payload(user_id, persona_json)
        if data is None:
            return False

        db = self._get_session()
        try:
            self._upsert_persona_row(
                db,
                user_id=user_id,
                data=data,
                now=db_now_naive(),
            )
            db.commit()
            return True
        except SQLAlchemyError:
            db.rollback()
            logger.exception("update_persona failed; transaction rolled back")
            raise
        finally:
            db.close()

    def update_persona_and_prompt(
        self,
        user_id: str,
        persona_json: str | dict[str, Any],
        system_prompt: str,
    ) -> bool:
        """兼容历史控制面；主聊天不读取 `system_prompts` 表。"""
        from core.time_utils import db_now_naive

        data = self._validated_persona_payload(user_id, persona_json)
        if data is None:
            return False

        db = self._get_session()
        try:
            now = db_now_naive()
            self._upsert_persona_row(db, user_id=user_id, data=data, now=now)
            sys_obj = (
                db.query(SystemPrompt)
                .filter(SystemPrompt.user_id == user_id)
                .first()
            )
            if sys_obj:
                sys_obj.prompt_text = system_prompt
                sys_obj.updated_at = now
            else:
                db.add(
                    SystemPrompt(
                        user_id=user_id,
                        prompt_text=system_prompt,
                        updated_at=now,
                    )
                )
            db.commit()
            return True
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "update_persona_and_prompt failed; transaction rolled back"
            )
            raise
        finally:
            db.close()

class UnifiedProvider:
    """
    Vendor-agnostic provider that wraps OpenAI-compatible NewAPIClient.
    Acts as a bridge for the NanobotKTController.
    Supports model tiering (fast/smart/reasoning).
    """
    def __init__(self, provider_type: str, api_key: str, base_url: str = "", model_map: dict = None, timeout: int | None = None):
        self.provider_type = provider_type
        self.api_key = api_key
        if provider_type == "dify":
            raise RuntimeError("Dify provider has been removed. Please migrate to NewAPI/OpenAI-compatible route.")
        self.client = RuntimeChatCompletionClient(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def invoke(self, 
                      query: str, 
                      user_id: str, 
                      session_id: str, 
                      memory: "SQLiteMemory",
                      tools: list[dict[str, Any]] | None = None,
                      files: list[str] | None = None,
                      model_tier: str = "smart") -> Any:
        """调用统一推理层"""
        persona = memory.get_user_persona(user_id)
        sys_prompt = memory.get_system_prompt(user_id)
        context_str = memory.get_recent_context_summary(session_id)
        
        messages = format_openai_messages(sys_prompt, persona, context_str, query)
        from core.llm_trace_context import llm_trace_scope
        with llm_trace_scope(source="legacy_adapter.chat"):
            return await self.client.chat_completion(
                messages=messages,
                tools=tools,
                temperature=0.7,
                model_tier=model_tier
            )

    async def invoke_with_messages(self,
                                    messages: list[dict[str, Any]],
                                    tools: list[dict[str, Any]] | None = None,
                                    model_tier: str = "smart") -> dict[str, Any]:
        """直接传入已组装好的 messages 调用 LLM (多轮工具循环场景)"""
        from core.llm_trace_context import llm_trace_scope
        with llm_trace_scope(source="legacy_adapter.tool_loop"):
            return await self.client.chat_completion(
                messages=messages,
                tools=tools,
                temperature=0.7,
                model_tier=model_tier
            )

    async def invoke_raw(self, query: str, system_prompt: str, user_id: str, model_tier: str = "smart") -> str:
        """无画像/上下文注入的原始调用 (用于进化子代理)"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]
        from core.llm_trace_context import llm_trace_scope
        with llm_trace_scope(source="legacy_adapter.raw"):
            resp = await self.client.chat_completion(messages=messages, model_tier=model_tier)
        if isinstance(resp, dict) and "choices" in resp:
            return resp["choices"][0]["message"]["content"]
        return str(resp.get("error", "Unknown error"))

class NanobotKTController:
    """
    KohakuTerrarium Controller: Orchestrates the interaction loop and evolution pipeline.
    Aligned with KT's native tool calling protocol (multi-turn tool loop).
    """
    def __init__(self, provider: Any, memory: SQLiteMemory):
        self.provider = provider
        self.memory = memory
        self.state_manager = StateManager()
        self.sandbox = AnalysisSandbox()
        # 仍在运行的后台能力；画像进化已收口为候选提取 + Python 状态机。
        self.data_analyst = DataAnalystAgent(self.sandbox)
        self.model_scout = ModelScoutAgent()
        # 本地工具注册表 (slash commands)
        self.local_tools: dict[str, Any] = {
            "stats": self.data_analyst.perform_sql_audit,
            "vibe": self._get_vibe_tool,
            "ai_daily": search_and_extract_news,
        }
        
        # 定义原生 Tool 定义 (OpenAI 格式) — 对齐 KT Registry 模式
        self.native_tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": "run_sql_analysis",
                    "description": "在该用户的对话日志库执行 SQL 查询进行数据分析",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "sql": {"type": "string", "description": "符合 SQLite 语法的查询语句"}
                        },
                        "required": ["sql"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_ai_daily",
                    "description": "聚合 AI/科技可信来源并生成日报或资讯简报",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"}
                        },
                        "required": ["query"]
                    }
                }
            },
        ]

    def _get_vibe_tool(self, query: str, session_id: str) -> str:
        """获取当前氛围工具"""
        return self.state_manager.get_group_state(session_id).get("vibe", "neutral")

    async def _execute_local_tool(self, query: str, user_id: str, session_id: str) -> str:
        """执行拦截的本地工具 (async-safe)"""
        parts = query.split(maxsplit=1)
        cmd = parts[0].lstrip("/")
        tool_args = parts[1].strip() if len(parts) > 1 else ""
        
        handler = self.local_tools.get(cmd)
        if handler:
            from core.tool_tracing import begin_tool_trace, finish_tool_trace

            trace_args = {"query": tool_args, "user_id": user_id, "session_id": session_id}
            tool_call_id, started = begin_tool_trace(cmd, trace_args)
            try:
                if cmd == "vibe":
                    result = handler(tool_args, session_id)
                # Run sync handlers in thread to avoid blocking event loop
                elif cmd == "ai_daily":
                    result = await asyncio.to_thread(handler, tool_args)
                else:
                    result = handler(tool_args)
            except Exception as e:
                finish_tool_trace(tool_call_id, started, status="error", error=str(e))
                raise
            finish_tool_trace(tool_call_id, started, result=result)
            return f"[Local Tool]: {result}"
        return "Unknown local tool."

    def _execute_native_tool(self, func_name: str, args: dict[str, Any]) -> str:
        """Execute a native tool call and return the result string."""
        from core.tool_tracing import begin_tool_trace, finish_tool_trace

        tool_call_id, started = begin_tool_trace(func_name, args)
        try:
            if func_name == "run_sql_analysis":
                result = self.sandbox.run_query(args.get("sql", ""))
            elif func_name == "run_python_analysis":
                from sandbox import PYTHON_ANALYSIS_DISABLED_MESSAGE

                result = PYTHON_ANALYSIS_DISABLED_MESSAGE
            elif func_name == "run_ai_daily":
                result = search_and_extract_news(args.get("query", ""))
            else:
                result = f"Unknown tool: {func_name}"
            finish_tool_trace(tool_call_id, started, result=result)
            return result
        except Exception as e:
            finish_tool_trace(tool_call_id, started, status="error", error=str(e))
            raise

    async def chat(self, user_id: str, session_id: str, query: str, metadata: dict[str, Any]) -> str:
        # 1. Local Tool Interceptor
        if query.startswith("/") and any(query.startswith(f"/{t}") for t in self.local_tools):
            return await self._execute_local_tool(query, user_id, session_id)
            
        # 2. Agentic Loop (Core Interaction)
        return await self._agentic_chat_loop(user_id, session_id, query, metadata)

    async def _agentic_chat_loop(self, user_id: str, session_id: str, query: str, metadata: dict[str, Any]) -> str:
        """
        Multi-turn native tool loop (aligned with KT Controller protocol).
        
        Flow: LLM call → if tool_calls → execute tools → append tool results → re-call LLM
        Repeats until LLM returns plain text or MAX_TOOL_ROUNDS is reached.
        """
        self.memory.save_log(user_id=user_id, session_id=session_id, role="user", content=query, sender_name=metadata.get("sender_name"))
        
        # Build initial messages
        persona = self.memory.get_user_persona(user_id)
        sys_prompt = self.memory.get_system_prompt(user_id)
        context_str = self.memory.get_recent_context_summary(session_id)
        messages = format_openai_messages(sys_prompt, persona, context_str, query)
        
        tools = self.native_tool_definitions
        
        for round_idx in range(MAX_TOOL_ROUNDS):
            if round_idx == 0:
                resp = await self.provider.invoke(
                    query=query, user_id=user_id, session_id=session_id,
                    memory=self.memory, tools=tools
                )
            else:
                resp = await self.provider.invoke_with_messages(
                    messages=messages, tools=tools
                )
            
            if isinstance(resp, dict) and "error" in resp:
                return f"Error: {resp['error']}"

            choice = resp.get("choices", [{}])[0].get("message", {})
            
            # Check for tool calls
            if not choice.get("tool_calls"):
                break  # LLM returned plain text — exit loop
            
            # Append assistant message (with tool_calls) to conversation
            messages.append({
                "role": "assistant",
                "content": choice.get("content", ""),
                "tool_calls": choice["tool_calls"],
            })
            
            # Execute each tool call and append results
            for tc in choice["tool_calls"]:
                func_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                
                logger.info(f"  [Tool Call] {func_name}(keys={list(args.keys())})")
                
                # Run sync tool in thread to avoid blocking
                result = await asyncio.to_thread(self._execute_native_tool, func_name, args)
                
                # Append tool result in OpenAI format
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{func_name}"),
                    "content": str(result)[:4000],  # Truncate long results
                })
            
            logger.info(f"  [Tool Loop] Round {round_idx + 1}/{MAX_TOOL_ROUNDS} completed, re-calling LLM")
        
        # Extract final answer
        answer = choice.get("content") or "No content"
        self.memory.save_log(user_id=user_id, session_id=session_id, role="model", content=answer)
        return answer

    async def evolve(self, user_id: str):
        """
        Implementation of the local evolution loop using specialized sub-agents.
        Decoupled from hosted workflow providers; logic resides entirely in Python.
        """
        # 1. Memory Stage: Fetch logs (now returns list of dicts, not ORM objects)
        logs = self.memory.get_unprocessed_logs(user_id)
        if not logs:
            return

        # 2. LLM 候选提取 + Python 状态机。
        logger.info(f"  [KT Local StateMachine] Extracting persona candidates for {user_id}...")
        existing_persona = self.memory.get_user_persona(user_id)

        # 3a. 过滤用户消息 + 构建 prompt
        user_msgs = filter_user_messages(logs)
        logs_text = format_candidate_logs(user_msgs)
        if not logs_text.strip():
            logger.info("  [KT Local StateMachine] No user messages, skipping persona update")
            self.memory.mark_logs_processed([log["id"] for log in logs])
            return

        extraction_prompt = build_candidate_extraction_prompt(
            existing_persona,
            logs_text,
        )
        system_prompt = get_candidate_extraction_system_prompt()

        # 3b. LLM 调用（只用 fast tier，因为只做提取不推理）
        candidate_res = await self.provider.invoke_raw(
            query=extraction_prompt,
            system_prompt=system_prompt,
            user_id=user_id,
            model_tier="fast",
        )

        # 3c. 严格输出契约；解析/结构错误只定向重试一次，最终失败保留待处理日志。
        from core.prompt_v2.task_contracts import TaskOutputContractError, parse_task_output

        try:
            parsed = parse_task_output("memory_extract", candidate_res)
        except TaskOutputContractError:
            retry_prompt = (
                f"{extraction_prompt}\n\n"
                "[输出契约修正] 只输出 JSON object，且必须显式包含 candidates 数组。"
            )
            candidate_res = await self.provider.invoke_raw(
                query=retry_prompt,
                system_prompt=system_prompt,
                user_id=user_id,
                model_tier="fast",
            )
            parsed = parse_task_output("memory_extract", candidate_res)
        candidates = parsed["candidates"]

        if candidates == []:
            logger.warning("  [KT Local StateMachine] No candidates extracted, skipping")
            self.memory.mark_logs_processed([log["id"] for log in logs])
            return

        # 3d. Python 状态机——去重/聚类/计数/衰减/冲突检测
        db = SessionLocal()
        try:
            sm = PersonaStateMachine(db, user_id)
            stats = sm.process_candidates(candidates)
            persona_summary = sm.build_summary()
        finally:
            db.close()

        if int(stats.get("processing_errors", 0) or 0) > 0:
            raise RuntimeError("candidate processing errors; logs remain unprocessed")

        logger.info(f"  [KT Local StateMachine] {stats}")

        # 3. 只保存画像。SystemPrompt 历史表不是 canonical Prompt Runtime 输入。
        update_result = self.memory.update_persona(
            user_id,
            persona_summary,
        )
        if update_result is False:
            raise RuntimeError("persona persistence rejected; logs remain unprocessed")
        self.memory.mark_logs_processed([log["id"] for log in logs])
        logger.info(f"  [KT Local Success] User {user_id} evolved successfully.")

class EvolutionUtils:
    """封装历史工作流迁移来的 Python 容错逻辑。"""
    
    @staticmethod
    def json_repair(raw: Any) -> dict[str, Any]:
        """JSON 格式容错修补逻辑。"""
        return _json_repair(raw)

    @staticmethod
    def _read_log_field(log: Any, field: str, default: Any = "") -> Any:
        """兼容 dict 日志和 ORM 日志对象的字段读取。"""
        if isinstance(log, dict):
            return log.get(field, default)
        return getattr(log, field, default)

    @staticmethod
    def pre_clean_logs(logs: list[Any], user_id: str) -> dict[str, Any]:
        """日志预清洗与质量分级逻辑。"""
        turn_count: int = 0
        user_corrections: int = 0
        max_consecutive: int = 0
        consecutive_depth: int = 0
        long_exchanges: int = 0
        cleaned_lines = []
        
        DOMAIN_KEYWORDS = {
            "language_learning": ["英语", "english", "语法", "词汇", "口语", "翻译"],
            "mathematics": ["数学", "方程", "函数", "微积分"],
            "computer_science": ["编程", "代码", "python", "算法", "debug"],
            "humanities": ["历史", "哲学", "政治"],
            "gaming": ["游戏", "攻略", "装备"]
        }
        domain_counts = {}

        for log in logs:
            raw_role = str(EvolutionUtils._read_log_field(log, "role", "")).strip().lower()
            role = "user" if raw_role == "user" else "assistant"
            content = str(EvolutionUtils._read_log_field(log, "content", "")).strip()
            content_lower = content.lower()
            
            if role == "user":
                turn_count = turn_count + 1
                consecutive_depth = consecutive_depth + 1
                max_consecutive = max(max_consecutive, consecutive_depth)
                # 领域检测
                for domain, keywords in DOMAIN_KEYWORDS.items():
                    if any(kw in content_lower for kw in keywords):
                        domain_counts[domain] = domain_counts.get(domain, 0) + 1
                # 修正行为
                if any(kw in content_lower for kw in ['不对', '重新', '错了', '理解错了']):
                    user_corrections = user_corrections + 1
            else:
                consecutive_depth = 0
                if len(content) > 200:
                    long_exchanges = long_exchanges + 1
            
            cleaned_lines.append(f"{role.capitalize()}: {content}")

        correction_rate = round(float(user_corrections) / float(max(turn_count, 1)), 2)
        if correction_rate > 0.15 or max_consecutive >= 5 or long_exchanges >= 3:
            quality_tier = "high"
        elif turn_count >= 3:
            quality_tier = "medium"
        else:
            quality_tier = "low"

        return {
            "cleaned_log": "\n".join(cleaned_lines),
            "quality_tier": quality_tier,
            "domain_distribution": domain_counts,
            "meta_info": f"Turns: {turn_count}, Corrections: {user_corrections}, Tier: {quality_tier}"
        }

class DataAnalystAgent:
    """受限环境下的本地数据分析专家"""
    def __init__(self, sandbox: AnalysisSandbox):
        self.sandbox = sandbox

    def perform_sql_audit(self, query: str) -> str:
        return self.sandbox.run_query(query)

    def perform_python_analysis(self, python_code: str) -> str:
        del python_code
        from sandbox import PYTHON_ANALYSIS_DISABLED_MESSAGE

        return PYTHON_ANALYSIS_DISABLED_MESSAGE

class ModelScoutAgent:
    """自动模型情报侦察员：负责从海量信息中提取新模型并更新注册表"""

    def __init__(self, catalog_writer: ModelCatalogWriterPort | None = None) -> None:
        self.catalog_writer = catalog_writer

    async def run(self, scout_info: str, provider: Any) -> list[dict[str, Any]]:
        # 1. 自动决定是否执行实时搜索
        if not scout_info or "搜集" in scout_info or "search" in scout_info.lower():
            logger.info("  [Model Scout] Initiating real-time web search...")
            search_query = scout_info if len(scout_info) > 5 else "latest AI model releases today 2026"
            scout_info = search_and_extract_news(search_query)

        # 2. 调用 LLM 进行情报分析 (Use 'reasoning' tier for high precision mapping)
        from core.prompt_v2.task_contracts import (
            TaskOutputContractError,
            parse_task_output,
        )
        from core.prompt_v2.task_templates import render_task_prompt

        system_prompt = render_task_prompt("tasks/model_scout_system", {})
        response = await provider.invoke_raw(
            query=f"## 搜集到的实时情报\n{scout_info}",
            system_prompt=system_prompt,
            user_id="model_scout",
            model_tier="reasoning"
        )

        # 2. 通过 Task Contract 解析；无效结果不得污染目录。
        try:
            models_to_update = parse_task_output(
                "model_scout_system",
                response,
            )["models"]
        except TaskOutputContractError as exc:
            logger.error("ModelScout output contract rejected: %s", exc)
            return []

        # 3. 更新注册表
        if not models_to_update:
            count = 0
        elif self.catalog_writer is None:
            count = upsert_model_catalog(models_to_update)
        else:
            count = self.catalog_writer.upsert_models(tuple(models_to_update))
        
        logger.info(f"  [Model Scout] Registry updated with {count} models.")
        return models_to_update
