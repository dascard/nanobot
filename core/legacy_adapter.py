"""
KohakuTerrarium (KT) Adapter for Nanobot.
This module bridges the KT framework components with the Nanobot SQLite DB and OpenAI-compatible APIs.
"""
import asyncio
import copy
import json
import logging
import re
from typing import List, Optional, Dict, Any
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from core.database import ChatLog, Persona, SystemPrompt, User, SessionLocal
from core.token_utils import estimate_tokens as _shared_estimate_tokens
from config import (
    ADMIN_USER_ID, OPENAI_API_KEY, OPENAI_BASE_URL,
    MAX_TOOL_ROUNDS,
)
from core.state_manager import StateManager
from sandbox import AnalysisSandbox
from creatures.nanobot.prompts.skills.news_search.tool import search_and_extract_news
from clients.new_api_client import NewAPIClient, format_openai_messages
from core.persona_preprocess import (
    CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
    PersonaStateMachine,
    build_candidate_extraction_prompt,
    filter_user_messages,
    format_candidate_logs,
)


# --- Local Evolution Prompts (High Fidelity) ---

LOG_ANALYST_LLM_PROMPT = """你是一个专业的对话日志分析师。你的任务是从原始对话日志中提炼关键信息，生成结构化的日志摘要。
## ⚠️ 核心原则：不记事实，只记偏好（对标 Claude Code 记忆系统设计）
- ❌ 绝对不要记录具体的代码片段、文件路径、API 端点、数据库表名等"事实"信息
- ✅ 只记录用户的行为模式、偏好倾向、思维方式、沟通风格
- ✅ 只记录反复出现的痛点模式（≥2次出现才算"模式"）
- ✅ 只记录用户对 AI 输出的偏好
## 提取目标
1. 用户行为模式
2. 工作流痛点
3. 技术偏好
4. 沟通风格
5. 高质量/低质量对话信号
6. 领域信号（domain_signals）：snake_case 领域 ID + 水平信号 + 偏好 + 痛点
"""

PERSONA_MERGE_PROMPT = """你是用户行为分析师。从对话日志中提取可操作画像。

## ⚠️ 数据格式
日志格式: [时间] role(谁): 内容
- role="user" → 用户说的话 ← **只分析这部分**
- role="assistant" → 你的前任(nanobot)的回复 ← **忽略！不要分析 bot 的行为**
- role="tool" → 工具调用记录 ← 忽略

## 核心原则
每一条必须回答：**AI 知道了这个，回复会发生什么具体改变？**
描述系统的、描述 bot 的、描述工具错误的、泛化标签——全部丢弃。

## 输出 JSON
{
  "summary": "用户是谁、主要聊什么（15-25字，中文）",
  "traits": ["用户反复表现的具体行为。不是标签，是「每次提问都先发一段代码再问」这类可见模式。至少2次"],
  "preferences": ["用户明确说的喜欢/不喜欢，如'别发太长''直接给答案别铺垫'"],
  "pain_points": "用户表现不耐烦/受挫的行为。只看用户说了什么——不要填 SQL 报错、超时、API 失败、工具错误、PRAGMA 被拒绝等系统问题。无则\"\"",
  "response_style": "具体回复指令：语气(随意/正式/幽默)？长度(简短/详细/看情况)？要不要客套？技术细节接受度？——这是最重要的字段",
  "domain_profiles": {}
}

## 规则
- ≥2 次同类信号才写
- 旧信号不再出现则降 confidence 或删
- 忽略"好的""谢谢""嗯""OK"

## quality check (输出前自检)
□ traits 里有没有"SQL 报错后重试查询"这类 bot 行为？→ 删掉
□ pain_points 里有没有"PRAGMA 被拦截""超时""401 错误"？→ 删掉，不是用户行为
□ 每条的"AI 知道了会怎么改回复"能答上来吗？
"""

PERSONA_CRITIQUE_PROMPT = """你是画像质量审计员。审查候选画像并输出修正版。

## 审计方法（逐条检查，通过的不用提）

### 第一遍：bot/系统污染扫描（最重要）
traits / pain_points / preferences 中，逐条问：**这是描述用户的行为，还是描述 bot/系统/工具？**
- "SQL 报错后会反复调整查询" → bot 行为，删除
- "PRAGMA 被拦截后换写法重试" → bot 行为，删除
- "直接查数据库表结构" → 这才是用户行为
- "模型超时导致回复延迟" → 系统问题，删除
- "API 返回 401 后切换模型" → bot/系统行为，删除

### 第二遍：五问
1. AI 不被告知也会这样做吗？→ 删。"默认行为"
2. 与画像其他条目矛盾吗？→ 保留一方。"冲突"
3. 换个说法重复了同一信息吗？→ 合并。"冗余"
4. 仅针对一次翻车的临时修正吗？→ 删。"创可贴"
5. 每次读到理解会不一样吗？→ 改写。"模糊"

### 第三遍：response_style
必须具体到 AI 能据此改变回复。泛词全部改写为具体指令。
✗ "友好一些" → ✓ "用口语化语气，不叫用户尊称"
✗ "更专业" → ✓ "给代码示例而非文字解释"

## 输出
修改要点：
- [污染/bot行为] 条目 → 删除
...

修正 JSON：
{...}
```"""

PROMPT_DRAFT_PROMPT = """你是一个 System Prompt 架构师，核心理念是"少即是多"。
## 你的任务
基于用户画像分析现有 System Prompt，生成候选最小必要规则集。
## ⚠️ Token 预算硬约束
最终精简后的 System Prompt 必须控制在 500 tokens 以内。
## 重要原则
- 不要写 AI 默认行为。
- 与用户画像强相关。
- 领域感知：对 interaction_count ≥ 5 的高频领域生成专属规则。
"""

PROMPT_AUDIT_PROMPT = """你是一个极度严格的 System Prompt 审计员。你的唯一职责是找出应该删除的规则。
## 五问审计框架
Q1. 本能判定：AI 默认也会做吗？
Q2. 冲突判定：规则间有矛盾吗？
Q3. 冗余判定：说了同一件事吗？
Q4. 创可贴判定：是不是针对偶发错误的临时补丁？
Q5. 模糊判定：规则足够具体可操作吗？
"""

PROMPT_SYNTHESIZE_PROMPT = """你是最终决策者。基于审计报告，输出最终极简版 System Prompt。
## 合成原则
1. 只保留通过审计的规则。
2. 合并冗余。
3. 顺序优化（重要度排序）。
4. Token 预算检查 (≤500)。
"""

MODEL_SCOUT_PROMPT = """你是一个专业的 AI 模型情报分析师。
## 你的任务
从提供的 AI 新闻、早报或搜索结果中提取新发布的或更新的 LLM 模型信息。
## 提取要求 (JSON 格式)
请输出一个 JSON 数组，包含以下字段：
- id: 官方模型 ID (字符串)
- provider: 厂商 (gemini, deepseek, zhipu, qwen, siliconflow, openai 等)
- intelligence: 智能水平评分 (1-10)
- cost_input_1m: 每百万输入 Token 成本 ($ USD)
- cost_output_1m: 每百万输出 Token 成本 ($ USD)
- tier: 层级 (smart/fast/reasoning)
- reasoning: 为什么给出该评分 (简短说明)

## ⚠️ 严禁幻觉
只提取明确提到的模型及其参数。如果由于是新模型信息不足，请给出合理的估计值并在 reasoning 中说明。
"""

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

    def get_recent_context(self, session_id: str, limit: int = 20) -> List[Dict[str, str]]:
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
            f"{'User' if l['role'] == 'user' else 'Assistant'}({l['sender'] or ''}): {l['content']}" 
            for l in logs
        ])

    def get_user_persona(self, user_id: str) -> str:
        """提取人（User）的画像 (返回 JSON 字符串)"""
        db = self._get_session()
        try:
            persona = db.query(Persona).filter(Persona.user_id == user_id).first()
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
        finally:
            db.close()

    def get_unprocessed_logs(self, user_id: str) -> List[Dict[str, Any]]:
        """提取待进化的原始日志 (返回 detached dicts 以避免跨 session 操作)
        
        Returns list of dicts with keys: id, user_id, role, content, sender_name, session_id, created_at
        """
        from config import EVOLUTION_THRESHOLD
        db = self._get_session()
        try:
            logs = (
                db.query(ChatLog)
                .filter(ChatLog.user_id == user_id, ChatLog.processed == 0)
                .order_by(ChatLog.id.asc())
                .all()
            )
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
                }
                for log in logs
            ]
        finally:
            db.close()

    def _get_unprocessed_log_ids(self, user_id: str) -> List[int]:
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

    def mark_logs_processed(self, log_ids: List[int]):
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

    def update_persona_and_prompt(self, user_id: str, persona_json: str, system_prompt: str):
        """同步回写画像与提示词（带有效性校验）"""
        import json as _json
        from datetime import datetime

        try:
            data = _json.loads(persona_json) if isinstance(persona_json, str) else persona_json
        except (_json.JSONDecodeError, TypeError):
            logger.error(f"Persona update rejected: invalid JSON for user {user_id}")
            return
        if not isinstance(data, dict):
            logger.error(f"Persona update rejected: not dict, user={user_id}")
            return
        if "error" in data or "code" in data:
            logger.error(f"Persona update rejected: error response, user={user_id}, "
                          f"preview={str(data)[:200]}")
            return

        db = self._get_session()
        try:
            persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
            sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()
            now = datetime.now()
            if persona_obj:
                persona_obj.persona_json = _json.dumps(data, ensure_ascii=False)
                persona_obj.updated_at = now
            else:
                db.add(Persona(user_id=user_id,
                               persona_json=_json.dumps(data, ensure_ascii=False),
                               updated_at=now))
            if sys_obj:
                sys_obj.prompt_text = system_prompt
                sys_obj.updated_at = now
            else:
                db.add(SystemPrompt(user_id=user_id, prompt_text=system_prompt, updated_at=now))
            db.commit()
        finally:
            db.close()

class UnifiedProvider:
    """
    Vendor-agnostic provider that wraps OpenAI-compatible NewAPIClient.
    Acts as a bridge for the NanobotKTController.
    Supports model tiering (fast/smart/reasoning).
    """
    def __init__(self, provider_type: str, api_key: str, base_url: str = "", model_map: dict = None, timeout: Optional[int] = None):
        self.provider_type = provider_type
        self.api_key = api_key
        if provider_type == "dify":
            raise RuntimeError("Dify provider has been removed. Please migrate to NewAPI/OpenAI-compatible route.")
        self.client = NewAPIClient(api_key=api_key, base_url=base_url, timeout=timeout)

    async def invoke(self, 
                      query: str, 
                      user_id: str, 
                      session_id: str, 
                      memory: "SQLiteMemory",
                      tools: Optional[List[Dict[str, Any]]] = None,
                      files: Optional[List[str]] = None,
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
                                    messages: List[Dict[str, Any]],
                                    tools: Optional[List[Dict[str, Any]]] = None,
                                    model_tier: str = "smart") -> Dict[str, Any]:
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
        # Sub-agents
        self.log_analyst = LogAnalystAgent()
        self.persona_architect = PersonaArchitectAgent()
        self.prompt_auditor = PromptAuditorAgent()
        self.data_analyst = DataAnalystAgent(self.sandbox)
        self.model_scout = ModelScoutAgent()
        # 本地工具注册表 (slash commands)
        self.local_tools: Dict[str, Any] = {
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
                    "name": "run_python_analysis",
                    "description": "在安全沙箱中执行 Python 数据分析脚本，可用于统计计算和可视化",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Python 代码（可用 sqlite3, json, math, statistics, collections）"}
                        },
                        "required": ["code"]
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

    def _execute_native_tool(self, func_name: str, args: Dict[str, Any]) -> str:
        """Execute a native tool call and return the result string."""
        from core.tool_tracing import begin_tool_trace, finish_tool_trace

        tool_call_id, started = begin_tool_trace(func_name, args)
        try:
            if func_name == "run_sql_analysis":
                result = self.sandbox.run_query(args.get("sql", ""))
            elif func_name == "run_python_analysis":
                result = self.sandbox.execute_python_analysis(args.get("code", ""))
            elif func_name == "run_ai_daily":
                result = search_and_extract_news(args.get("query", ""))
            else:
                result = f"Unknown tool: {func_name}"
            finish_tool_trace(tool_call_id, started, result=result)
            return result
        except Exception as e:
            finish_tool_trace(tool_call_id, started, status="error", error=str(e))
            raise

    async def chat(self, user_id: str, session_id: str, query: str, metadata: Dict[str, Any]) -> str:
        # 1. Local Tool Interceptor
        if query.startswith("/") and any(query.startswith(f"/{t}") for t in self.local_tools):
            return await self._execute_local_tool(query, user_id, session_id)
            
        # 2. Agentic Loop (Core Interaction)
        return await self._agentic_chat_loop(user_id, session_id, query, metadata)

    async def _agentic_chat_loop(self, user_id: str, session_id: str, query: str, metadata: Dict[str, Any]) -> str:
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
        if not logs: return

        # 2. Analysis Stage: LogAnalyst sub-agent
        logger.info(f"  [KT Local Analyst] Analyzing logs for {user_id}...")
        results_02 = await self.log_analyst.run(logs, self.provider)
        
        # 3. Design Stage: LLM 候选提取 + Python 状态机（替代旧的 PersonaArchitectAgent）
        logger.info(f"  [KT Local StateMachine] Extracting persona candidates for {user_id}...")
        existing_persona = self.memory.get_user_persona(user_id)

        # 3a. 过滤用户消息 + 构建 prompt
        user_msgs = filter_user_messages(logs)
        logs_text = format_candidate_logs(user_msgs)
        if not logs_text.strip():
            logger.info(f"  [KT Local StateMachine] No user messages, skipping persona update")
            self.memory.mark_logs_processed([log["id"] for log in logs])
            return

        extraction_prompt = build_candidate_extraction_prompt(existing_persona, logs_text)
        try:
            from core.prompt_runtime import render_prompt_content

            extraction_prompt = render_prompt_content(
                "memory_extract",
                {"conversation": logs_text, "existing_memory": existing_persona},
                extraction_prompt,
            )
        except Exception:
            pass

        # 3b. LLM 调用（只用 fast tier，因为只做提取不推理）
        candidate_res = await self.provider.invoke_raw(
            query=extraction_prompt,
            system_prompt=CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
            user_id=user_id,
            model_tier="fast",
        )

        # 3c. JSON 解析
        parsed = EvolutionUtils.json_repair(candidate_res)
        candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []

        if not candidates:
            logger.warning(f"  [KT Local StateMachine] No candidates extracted, skipping")
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

        logger.info(f"  [KT Local StateMachine] {stats}")

        # 4. Quality Stage: PromptAuditor sub-agent
        logger.info(f"  [KT Local Auditor] Auditing system prompt for {user_id}...")
        current_prompt = self.memory.get_system_prompt(user_id)
        results_04 = await self.prompt_auditor.run(current_prompt, persona_summary, self.provider)

        # 5. Commit Stage: Save to DB
        self.memory.update_persona_and_prompt(
            user_id,
            persona_summary,
            results_04["final_system_prompt"]
        )
        self.memory.mark_logs_processed([log["id"] for log in logs])
        logger.info(f"  [KT Local Success] User {user_id} evolved successfully.")

class EvolutionUtils:
    """封装历史工作流迁移来的 Python 容错逻辑。"""
    
    @staticmethod
    def json_repair(raw: Any) -> Dict[str, Any]:
        """JSON 格式容错修补逻辑。"""
        if raw is None:
            return {"parse_error": True, "raw": ""}
        raw = str(raw).strip()
        if not raw:
            return {"parse_error": True, "raw": ""}
        # 1. 直接解析
        try: return json.loads(raw)
        except (json.JSONDecodeError, ValueError): pass
        # 2. 提取 ```json ... ```
        try:
            m = re.search(r'```json\s*([\s\S]*?)```', raw)
            if m: return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError): pass
        # 3. 提取最外层 { ... }
        try:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m: return json.loads(m.group())
        except (json.JSONDecodeError, ValueError): pass
        # 4. 常见错误修补 (移除漏逗号等)
        try:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                fixed = m.group()
                fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
                fixed = fixed.replace("'", '"')
                return json.loads(fixed)
        except (json.JSONDecodeError, ValueError): pass
        return {"parse_error": True, "raw": str(raw)[:1000]}

    @staticmethod
    def _read_log_field(log: Any, field: str, default: Any = "") -> Any:
        """兼容 dict 日志和 ORM 日志对象的字段读取。"""
        if isinstance(log, dict):
            return log.get(field, default)
        return getattr(log, field, default)

    @staticmethod
    def pre_clean_logs(logs: List[Any], user_id: str) -> Dict[str, Any]:
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

        for l in logs:
            raw_role = str(EvolutionUtils._read_log_field(l, "role", "")).strip().lower()
            role = "user" if raw_role == "user" else "assistant"
            content = str(EvolutionUtils._read_log_field(l, "content", "")).strip()
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
                if len(content) > 200: long_exchanges = long_exchanges + 1
            
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

class LogAnalystAgent:
    """对话日志结构化提炼 (High Fidelity)"""
    async def run(self, logs: List[Any], provider: Any) -> Dict[str, Any]:
        # 1. 预清洗与元数据生成
        clean_res = EvolutionUtils.pre_clean_logs(logs, "N/A")
        
        # 2. 调用 LLM 结构化提取 (Use 'fast' tier)
        prompt = f"## 元信息\n{clean_res['meta_info']}\n\n## 领域分布\n{clean_res['domain_distribution']}\n\n## 对话日志\n{clean_res['cleaned_log']}"
        response = await provider.invoke_raw(
            query=prompt, 
            system_prompt=LOG_ANALYST_LLM_PROMPT, 
            user_id="log_analyst",
            model_tier="fast"
        )
        
        # 3. JSON 修补并返回
        structured = EvolutionUtils.json_repair(response)
        if not isinstance(structured, dict):
            structured = {"parse_error": True, "raw": str(structured)[:500]}
        structured.update({
            "quality_tier": clean_res["quality_tier"],
            "kb_document": self._format_kb(structured, clean_res)
        })
        return structured

    def _format_kb(self, structured: Dict[str, Any], clean_res: Dict[str, Any]) -> str:
        """生成结构化知识库文档。"""
        return f"""# 对话日志摘要
{clean_res['meta_info']}
Tier: {structured.get('quality_tier')} | Summary: {structured.get('summary')}
## 行为模式
{chr(10).join('- ' + s for s in structured.get('behavior_patterns', []))}
## 技术偏好
{chr(10).join('- ' + s for s in structured.get('tech_preferences', []))}
"""

class PersonaArchitectAgent:
    """深层画像提炼引擎 (High Fidelity: Merge -> Critique -> Format)"""
    @staticmethod
    def _to_json_len(payload: Dict[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _enforce_persona_size(persona: Dict[str, Any], max_chars: int = 3000) -> Dict[str, Any]:
        """将画像压缩到长度预算内，优先删除低置信/低活跃领域。"""
        if not isinstance(persona, dict):
            return {"parse_error": True, "raw": str(persona)[:1000]}

        result = copy.deepcopy(persona)
        if PersonaArchitectAgent._to_json_len(result) <= max_chars:
            return result

        # 先裁剪高噪声自由文本字段
        for key in ["domain_update_log", "delta_from_previous", "persona_summary"]:
            if key in result and PersonaArchitectAgent._to_json_len(result) > max_chars:
                result[key] = ""

        # 再裁剪低优先领域画像（low -> medium -> high, interaction_count 升序）
        domain_profiles = result.get("domain_profiles")
        if isinstance(domain_profiles, dict) and PersonaArchitectAgent._to_json_len(result) > max_chars:
            def _rank(item: Any) -> Any:
                _, value = item
                if not isinstance(value, dict):
                    return (3, 0)
                conf = str(value.get("confidence", "low")).lower()
                conf_rank = {"low": 0, "medium": 1, "high": 2}.get(conf, 0)
                count = int(value.get("interaction_count", 0) or 0)
                return (conf_rank, count)

            for domain_key, _ in sorted(domain_profiles.items(), key=_rank):
                if PersonaArchitectAgent._to_json_len(result) <= max_chars:
                    break
                domain_profiles.pop(domain_key, None)

        # 裁剪过长列表
        if PersonaArchitectAgent._to_json_len(result) > max_chars:
            for key, value in list(result.items()):
                if isinstance(value, list) and len(value) > 5:
                    result[key] = value[:5]

        # 兜底：裁剪长字符串
        if PersonaArchitectAgent._to_json_len(result) > max_chars:
            for key, value in list(result.items()):
                if isinstance(value, str) and len(value) > 240:
                    result[key] = value[:240]
                    if PersonaArchitectAgent._to_json_len(result) <= max_chars:
                        break

        # 最终兜底：保留核心框架
        if PersonaArchitectAgent._to_json_len(result) > max_chars:
            result = {
                "version": result.get("version", ""),
                "user_id": result.get("user_id", ""),
                "identity": result.get("identity", {}),
                "communication_style": result.get("communication_style", ""),
                "domain_profiles": result.get("domain_profiles", {}),
                "persona_summary": str(result.get("persona_summary", ""))[:400],
            }

        return result

    @staticmethod
    def to_kb_document(persona: Dict[str, Any], user_id: str) -> str:
        """将画像转成可写入知识库的文本快照。"""
        if not isinstance(persona, dict):
            return ""
        return (
            f"# Persona Snapshot\n"
            f"User: {user_id}\n\n"
            f"## Summary\n{persona.get('persona_summary', '')}\n\n"
            f"## Full JSON\n{json.dumps(persona, ensure_ascii=False)}"
        )

    async def run(self, existing_persona: str, log_summary: Dict[str, Any], provider: Any) -> Dict[str, Any]:
        # 1. Stage: Merge (T2 - Use Smart model)
        merge_input = f"## 现有画像\n{existing_persona}\n\n## 新日志摘要\n{json.dumps(log_summary, ensure_ascii=False)}"
        draft_res = await provider.invoke_raw(query=merge_input, system_prompt=PERSONA_MERGE_PROMPT, user_id="p_architect_merge", model_tier="smart")
        draft_json = EvolutionUtils.json_repair(draft_res)

        # Validate merge output; retry once with explicit repair hint on parse failure
        if isinstance(draft_json, dict) and draft_json.get("parse_error"):
            logger.warning(f"PersonaArchitectAgent merge parse error, retrying: {draft_json.get('raw', '')[:200]}")
            repair_input = f"## 修复要求\n之前的输出无法解析为 JSON。请直接输出有效的 JSON 对象，键为 identity/communication_style/domain_profiles/persona_summary/version。\n\n## 原始合并输入\n{merge_input}"
            draft_res = await provider.invoke_raw(query=repair_input, system_prompt=PERSONA_MERGE_PROMPT, user_id="p_architect_merge_retry", model_tier="smart")
            draft_json = EvolutionUtils.json_repair(draft_res)

        # 2. Stage: Six-Dimension Critique (T3 - Use Reasoning model if available)
        critique_res = await provider.invoke_raw(query=json.dumps(draft_json, ensure_ascii=False), system_prompt=PERSONA_CRITIQUE_PROMPT, user_id="p_architect_critique", model_tier="reasoning")

        # 3. Stage: Final Repair & Truncation (Python)
        final_persona = EvolutionUtils.json_repair(critique_res)

        # Validate critique output; retry once on parse failure
        if isinstance(final_persona, dict) and final_persona.get("parse_error"):
            logger.warning(f"PersonaArchitectAgent critique parse error, retrying: {final_persona.get('raw', '')[:200]}")
            repair_input = f"## 修复要求\n之前的输出无法解析为 JSON。请直接输出有效的 JSON 对象。\n\n## 草稿画像\n{json.dumps(draft_json, ensure_ascii=False)}"
            critique_res = await provider.invoke_raw(query=repair_input, system_prompt=PERSONA_CRITIQUE_PROMPT, user_id="p_architect_critique_retry", model_tier="reasoning")
            final_persona = EvolutionUtils.json_repair(critique_res)

        # Validate expected keys; log warnings for missing fields
        if isinstance(final_persona, dict):
            expected_keys = {"identity", "communication_style", "domain_profiles", "persona_summary", "version"}
            present_keys = set(final_persona.keys())
            missing = expected_keys - present_keys
            unexpected = present_keys - expected_keys - set(["parse_error", "raw"])
            if missing:
                logger.warning(f"PersonaArchitectAgent output missing expected keys: {missing}")
            if unexpected:
                logger.debug(f"PersonaArchitectAgent output has unexpected keys: {unexpected}")

        normalized_persona = self._enforce_persona_size(final_persona, max_chars=3000)
        if self._to_json_len(normalized_persona) > 3000:
            logger.warning("Persona remains larger than 3000 chars after compression.")
        return normalized_persona

class PromptAuditorAgent:
    """System Prompt 五问审计精简引擎 (High Fidelity: Draft -> Audit -> Synthesize)"""
    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, _shared_estimate_tokens(text))

    @staticmethod
    def _compute_deletion_rate(audit_report: str) -> float:
        deleted = len(re.findall(r"❌", audit_report))
        total = len(re.findall(r"\|\s*\d+\s*\|", audit_report))
        if total == 0:
            return 0.0
        return min(1.0, deleted / total)

    def _apply_token_budget(self, prompt: str, max_tokens: int = 500) -> tuple[str, int, bool]:
        estimated = self._estimate_tokens(prompt)
        if estimated <= max_tokens:
            return prompt, estimated, False

        # 按行裁剪，优先保留前置规则
        kept_lines = []
        running = ""
        for line in prompt.splitlines():
            candidate = (running + "\n" + line).strip() if running else line
            if self._estimate_tokens(candidate) > max_tokens:
                break
            kept_lines.append(line)
            running = candidate

        truncated = "\n".join(kept_lines).strip()
        if not truncated:
            truncated = prompt[: max_tokens * 2]

        return truncated, self._estimate_tokens(truncated), True

    async def run(self, current_prompt: str, persona_json: str, provider: Any) -> Dict[str, Any]:
        # 1. Stage: Draft (T2 - Smart)
        draft_res = await provider.invoke_raw(
            query=f"## 现有 Prompt\n{current_prompt}\n\n## 患者画像\n{persona_json}", 
            system_prompt=PROMPT_DRAFT_PROMPT, user_id="p_auditor_draft",
            model_tier="smart"
        )
        
        # 2. Stage: 5-Question Audit (T3 - Reasoning)
        audit_res = await provider.invoke_raw(
            query=f"## 候选规则\n{draft_res}\n\n## 参考原版\n{current_prompt}", 
            system_prompt=PROMPT_AUDIT_PROMPT, user_id="p_auditor_audit",
            model_tier="reasoning"
        )
        
        # 3. Stage: Synthesize (T2 - Smart)
        synth_res = await provider.invoke_raw(
            query=f"## 审计报告\n{audit_res}\n\n## 原始 Prompt\n{current_prompt}", 
            system_prompt=PROMPT_SYNTHESIZE_PROMPT, user_id="p_auditor_synth",
            model_tier="smart"
        )
        
        # 提取最终版本 (regex 保持鲁棒)
        prompt_match = re.search(r'(?is)最终(精简)?(版)?Prompt[:：\s]*\n+([\s\S]*?)($|---)', synth_res)
        final_prompt = prompt_match.group(3).strip() if prompt_match else current_prompt

        # 质量门禁（删除率 > 60% 触发）
        deletion_rate = self._compute_deletion_rate(audit_res)
        quality_gate = "triggered" if deletion_rate > 0.6 else "pass"
        quality_warning = (
            f"草稿删除率 {deletion_rate * 100:.0f}% > 60%，建议回看候选规则生成阶段。"
            if quality_gate == "triggered"
            else ""
        )

        # Token 预算约束（<= 500）
        bounded_prompt, estimated_tokens, was_truncated = self._apply_token_budget(final_prompt, max_tokens=500)
        if was_truncated:
            logger.warning(f"Final prompt truncated to fit token budget: ~{estimated_tokens}/500")
        
        return {
            "final_system_prompt": bounded_prompt,
            "audit_report": audit_res,
            "quality_gate": quality_gate,
            "quality_warning": quality_warning,
            "deletion_rate": deletion_rate,
            "estimated_tokens": estimated_tokens,
            "token_budget_truncated": was_truncated,
        }

class DataAnalystAgent:
    """受限环境下的本地数据分析专家"""
    def __init__(self, sandbox: AnalysisSandbox):
        self.sandbox = sandbox

    def perform_sql_audit(self, query: str) -> str:
        return self.sandbox.run_query(query)

    def perform_python_analysis(self, python_code: str) -> str:
        return self.sandbox.execute_python_analysis(python_code)

class ModelScoutAgent:
    """自动模型情报侦察员：负责从海量信息中提取新模型并更新注册表"""
    async def run(self, scout_info: str, provider: Any) -> List[Dict[str, Any]]:
        # 1. 自动决定是否执行实时搜索
        if not scout_info or "搜集" in scout_info or "search" in scout_info.lower():
            logger.info("  [Model Scout] Initiating real-time web search...")
            search_query = scout_info if len(scout_info) > 5 else "latest AI model releases today 2026"
            scout_info = search_and_extract_news(search_query)

        # 2. 调用 LLM 进行情报分析 (Use 'reasoning' tier for high precision mapping)
        response = await provider.invoke_raw(
            query=f"## 搜集到的实时情报\n{scout_info}", 
            system_prompt=MODEL_SCOUT_PROMPT, 
            user_id="model_scout",
            model_tier="reasoning"
        )
        
        # 2. 解析 JSON 情报
        models_to_update = EvolutionUtils.json_repair(response)
        if isinstance(models_to_update, dict) and "parse_error" not in models_to_update:
             # Handle case where LLM returns a single object instead of a list
             models_to_update = [models_to_update]
        elif not isinstance(models_to_update, list):
             logger.error(f"ModelScout parse error: {response}")
             return []

        # 3. 更新注册表
        from clients.model_registry import registry
        count = 0
        for m_data in models_to_update:
            if m_data.get("id"):
                registry.add_or_update_model(m_data)
                count += 1
        
        logger.info(f"  [Model Scout] Registry updated with {count} models.")
        return models_to_update
