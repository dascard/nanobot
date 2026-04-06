"""
FastAPI 路由模块。
定义所有 HTTP 端点，含 Bearer Token 认证中间件。
"""
import logging

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from config import NANOBOT_API_TOKEN, EVOLUTION_THRESHOLD, API_KEY_01_CHAT
from database import get_db, User, Persona, SystemPrompt, ChatLog
from evolution import evolution_task
from dify_client import call_dify_chat

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
    query: str


# ── 端点 ──

@router.get("/context")
def get_context(
    user_id: str = "default_user",
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """返回拼合后的系统设定 + 用户画像，供前端注入脚本使用。"""
    persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
    sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()

    return {
        "user_id": user_id,
        "persona_json": persona_obj.persona_json if persona_obj else "{}",
        "system_prompt": (
            sys_obj.prompt_text if sys_obj
            else "你是一个具备自进化能力的智能助手。"
        ),
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

    # 1. 自动注册用户
    if not db.query(User).filter(User.id == req.user_id).first():
        db.add(User(id=req.user_id))
        db.commit()

    # 2. 捞取用户画像上下文
    persona_obj = db.query(Persona).filter(Persona.user_id == req.user_id).first()
    sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == req.user_id).first()
    
    p_json = persona_obj.persona_json if persona_obj else "{}"
    s_prompt = sys_obj.prompt_text if sys_obj else "你是一个具备自进化能力的智能助手。"

    # 3. 阻塞调用 Dify 01
    try:
        answer = call_dify_chat(API_KEY_01_CHAT, req.user_id, req.query, p_json, s_prompt)
    except Exception as e:
        logger.error(f"Chat Proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Upstream Error: {str(e)}")

    # 4. 双向写库
    db.add(ChatLog(user_id=req.user_id, role="user", content=req.query, processed=0))
    db.add(ChatLog(user_id=req.user_id, role="model", content=answer, processed=0))
    db.commit()

    # 5. 检查进化触发阈值
    pending = db.query(ChatLog).filter(ChatLog.user_id == req.user_id, ChatLog.processed == 0).count()
    if pending >= EVOLUTION_THRESHOLD:
        background_tasks.add_task(evolution_task, req.user_id)

    return {
        "status": "ok",
        "user_id": req.user_id,
        "answer": answer,
        "unprocessed_logs": pending
    }


@router.get("/health")
def health_check():
    """健康检查端点。"""
    return {"status": "healthy", "version": "0.2.0"}
