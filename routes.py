"""
FastAPI 路由模块。
定义所有 HTTP 端点，含 Bearer Token 认证中间件。
"""
import os
import logging
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Header, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List

from config import NANOBOT_API_TOKEN, EVOLUTION_THRESHOLD, API_KEY_01_CHAT, ADMIN_USER_ID
from database import get_db, User, Persona, SystemPrompt, ChatLog
from evolution import evolution_task
from dify_client import call_dify_chat, stream_dify_chat
from compaction import run_autocompact_circuit_breaker

logger = logging.getLogger("nanobot.routes")
router = APIRouter(prefix="/api/v1")


# ── 认证中间件 ──

def verify_token(authorization: str = Header(default="")):
    """
    简单 Bearer Token 校验。
    若环境变量 NANOBOT_API_TOKEN 为空，则不启用认证（开发模式）。
    """
    if not NANOBOT_API_TOKEN:
        return  # 未配置 Token 则跳过认证
    token = authorization.replace("Bearer ", "").strip()
    if token != NANOBOT_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


# ── 请求模型 ──

class LogRequest(BaseModel):
    user_id: str = "default_user"
    role: str  # 'user' | 'model'
    content: str


class ChatProxyRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str = "default_session"
    query: str = ""
    files: Optional[List[str]] = None
    sender_name: Optional[str] = None
    session_name: Optional[str] = None

class EvolutionTriggerRequest(BaseModel):
    user_id: str

class AmbientLogRequest(BaseModel):
    group_id: str = "unknown"
    session_name: str | None = None  # 场景名 (如群名)
    sender_name: str = "unknown"    # 发送者名
    content: str = ""



# ── 端点 ──

@router.get("/context")
def get_context(
    user_id: str = "default_user",
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """返回拼合后的系统设定 + 用户画像 + 近期上下文，供前端注入脚本使用。"""
    persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
    sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()
    
    # 提取最近上下文 (Stateless Sliding Window)
    recent_logs = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == user_id)
        .order_by(ChatLog.id.desc())
        .limit(20)
        .all()
    )
    recent_logs.reverse()
    context_lines = []
    for lg in recent_logs:
        speaker = "User" if lg.role == "user" else "Assistant"
        context_lines.append(f"{speaker}: {lg.content}")
        
    recent_context_summary = run_autocompact_circuit_breaker(context_lines, max_length=4000)

    return {
        "user_id": user_id,
        "persona_json": persona_obj.persona_json if persona_obj else "{}",
        "system_prompt": sys_obj.prompt_text if sys_obj else "你是一个具备自进化能力的智能助手。",
        "recent_context_summary": recent_context_summary
    }


@router.post("/log")
def submit_log(
    log_req: LogRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """接收聊天记录，累积到阈值后触发后台自进化。"""
    # 1. 自动注册用户
    if not db.query(User).filter(User.id == log_req.user_id).first():
        db.add(User(id=log_req.user_id))
        db.commit()

    # 2. 写入日志
    db.add(ChatLog(
        user_id=log_req.user_id,
        role=log_req.role,
        content=log_req.content,
        processed=0,
    ))
    db.commit()

    # 3. 检查阈值
    pending = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == log_req.user_id, ChatLog.processed == 0)
        .count()
    )
    if pending >= EVOLUTION_THRESHOLD:
        background_tasks.add_task(evolution_task, log_req.user_id)

    return {"status": "ok", "unprocessed_logs": pending}

@router.post("/log_ambient")
def submit_ambient_log(
    req: AmbientLogRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """专门接收前台悄无声息收集的环境窥屏包，设为已处理，不消耗高级分析算力，只做持久化备份"""
    actual_user_id = f"group_{req.group_id}"
    
    # 自动注册隐式群体用户
    if not db.query(User).filter(User.id == actual_user_id).first():
        db.add(User(id=actual_user_id))
        
    formatted_content = f"[{req.sender_name}]: {req.content}"
    
    db.add(ChatLog(
        user_id=actual_user_id,
        session_id=str(req.group_id),
        sender_name=req.sender_name,
        session_name=req.session_name,
        role="ambient",
        content=formatted_content,
        processed=1,
    ))
    db.commit()
    return {"status": "ok", "message": "ambient log saved"}

@router.get("/search_logs")
def search_history_logs(
    user_id: str,
    keyword: str = "",
    limit: int = 50,
    context_size: int = 0,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    提供给 Dify Agent 作为 Custom Tool 调用的数据库本地精确检索 API。
    实现无需全量 RAG 的按需、极速精准回忆。带有上下文支持。
    """
    base_query = db.query(ChatLog)
    if user_id != "all":
        # 【弹性搜索核心】：允许 user_id 匹配 ID 或者 模糊匹配人名/场景名
        # 【优先精确匹配】
        exact_match = base_query.filter(
            or_(
                ChatLog.user_id == user_id,
                ChatLog.session_id == user_id
            )
        )
        if exact_match.count() > 0:
            base_query = exact_match
        else:
            # 环境模糊匹配兜底
            base_query = base_query.filter(
                or_(
                    ChatLog.sender_name.like(f"%{user_id}%"),
                    ChatLog.session_name.like(f"%{user_id}%")
                )
            )
    
    if not keyword:
        # 无关键词：直接返回最新记录
        results = base_query.order_by(ChatLog.id.desc()).limit(limit).all()
        results.reverse()
        final_logs = results
    else:
        # 有关键词：查找匹配及其上下文
        matches = base_query.filter(ChatLog.content.like(f"%{keyword}%")).order_by(ChatLog.id.desc()).limit(limit).all()
        
        log_dict = {}
        for match in matches:
            log_dict[match.id] = match
            
            if context_size > 0:
                # 查找上下文，必须限制在同一个 session_id（对话场）中，确保逻辑连贯
                # 向上查找
                before_logs = db.query(ChatLog).filter(
                    ChatLog.session_id == match.session_id,
                    ChatLog.id < match.id
                ).order_by(ChatLog.id.desc()).limit(context_size).all()
                
                # 向下查找
                after_logs = db.query(ChatLog).filter(
                    ChatLog.session_id == match.session_id,
                    ChatLog.id > match.id
                ).order_by(ChatLog.id.asc()).limit(context_size).all()
                
                for log in before_logs + after_logs:
                    log_dict[log.id] = log
                
        # 按照 ID 升序重排，确保呈现给 AI 的是正确的时间顺序
        final_logs = [log_dict[log_id] for log_id in sorted(log_dict.keys())]
        
    filtered_output = []
    for row in final_logs:
        t = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # 来源标识：跨群搜索时很有用
        source = f"[{row.session_id}]"
        filtered_output.append(f"[{t}]{source} {row.role.upper()}: {row.content}")
        
    return {
        "status": "ok",
        "results_found": len(filtered_output),
        "logs": "\n".join(filtered_output) if filtered_output else "未检索到匹配结果。"
    }

@router.get("/render")
async def render_markdown(text: str):
    """遗留端点，已弃用。目前直接内嵌 base64 返回"""
    return {"status": "deprecated"}



@router.post("/chat")
def proxy_chat(
    req: ChatProxyRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    统一网关：接收客户端的发问，注入本地 DB 画像，代理调用 Dify 01，返回结果并双向落库。
    """
    if not API_KEY_01_CHAT:
        raise HTTPException(status_code=500, detail="API_KEY_01_CHAT not configured")

    # 1. 自动注册用户 & 场
    for target_id in [req.user_id, req.session_id]:
        if not db.query(User).filter(User.id == target_id).first():
            db.add(User(id=target_id))
    db.commit()

    # 2. 捞取用户画像上下文（基于 user_id: 认人）
    persona_obj = db.query(Persona).filter(Persona.user_id == req.user_id).first()
    sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == req.user_id).first()
    
    p_json = persona_obj.persona_json if persona_obj else "{}"
    s_prompt = sys_obj.prompt_text if sys_obj else "你是一个具备自进化能力的智能助手。"

    # 识别管理员：剥离前缀比对
    raw_uid = req.user_id.split("_")[-1] if "_" in req.user_id else req.user_id
    is_admin = (raw_uid == ADMIN_USER_ID)

    # 3. 本地对话历史滑窗提取（基于 session_id: 认场）
    recent_logs = (
        db.query(ChatLog)
        .filter(ChatLog.session_id == req.session_id)
        .order_by(ChatLog.id.desc())
        .limit(20)
        .all()
    )
    recent_logs.reverse()
    
    context_lines = []
    for lg in recent_logs:
        speaker = "User" if lg.role == "user" else "Assistant"
        # 加上人名，辅助 Dify 这里的 context 更有区分度
        name_tag = f"({lg.sender_name})" if lg.sender_name else ""
        context_lines.append(f"{speaker}{name_tag}: {lg.content}")
        
    # 强制截断防爆处理
    recent_context_summary = run_autocompact_circuit_breaker(context_lines, max_length=4000)

    # 4. 流式调用 Dify 01 (Stateless)
    # 为了解决首字响应慢的问题，我们在这里采用“伪流式”分段聚合
    answer = ""
    try:
        # stream_dify_chat 会实时产出文本块
        for chunk in stream_dify_chat(
            API_KEY_01_CHAT, 
            req.user_id, 
            req.query, 
            p_json, 
            s_prompt, 
            recent_context_summary, 
            is_admin_user=is_admin,
            files=req.files
        ):
            answer += chunk
            # TODO: 未来可以配合 WebSocket 实现真正的实时推流
    except Exception as e:
        logger.error(f"Chat Proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Upstream Error: {str(e)}")

    # 5. 双向写库
    display_query = req.query
    if req.files:
        display_query += f" [包含 {len(req.files)} 张图片]"
        
    db.add(ChatLog(
        user_id=req.user_id,
        session_id=req.session_id,
        sender_name=req.sender_name,
        session_name=req.session_name,
        role="user",
        content=display_query,
        processed=0
    ))
    db.add(ChatLog(
        user_id="nanobot",
        session_id=req.session_id,
        sender_name="Nanobot",
        session_name=req.session_name,
        role="model",
        content=answer,
        processed=0
    ))
    db.commit()

    # 6. 检查进化触发阈值 (基于 Session 触发：该场景产生了足够的对话流)
    pending = db.query(ChatLog).filter(ChatLog.session_id == req.session_id, ChatLog.processed == 0).count()
    if pending >= EVOLUTION_THRESHOLD:
        # 触发进化时，针对具体的物理人触发（提取该人跨 Session 的足迹）
        background_tasks.add_task(evolution_task, req.user_id)

    # ── 模拟短对话：内容自动拆分逻辑 ──
    # 将长回复按双换行或特定分段符拆分，让前端可以分条发送
    if "\n\n" in answer:
        answer_chunks = [c.strip() for c in answer.split("\n\n") if c.strip()]
    elif len(answer) > 300:
        # 兜底：如果太长且没换行，按句号尝试拆分（可选）
        answer_chunks = [answer]
    else:
        answer_chunks = [answer]

    return {
        "status": "ok",
        "user_id": req.user_id,
        "answer": answer,           # 保留全文兼容旧逻辑
        "answer_chunks": answer_chunks, # 新增拆分后的列表
        "unprocessed_logs": pending
    }

@router.post("/evolution/trigger")
def trigger_evolution(
    req: EvolutionTriggerRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_token),
):
    """
    手动触发自进化：通过 API 强制开启画像提炼与同步，不再依赖日志计数阈值。
    """
    logger.info(f"Manual evolution triggered for user [{req.user_id}]")
    background_tasks.add_task(evolution_task, req.user_id)
    return {"status": "ok", "message": f"Evolution task queued for {req.user_id}"}


@router.get("/health")
def health_check():
    """健康检查端点。"""
    return {"status": "healthy", "version": "0.2.0"}
