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
    user_id: str = "default_user"   # 标识发件人（用于画像 Persona 索引，实现跨群认人）
    session_id: str = "default_session" # 标识对话场（用于 Context 索引，实现群聊环境隔离）
    query: str
    files: list[str] = None  # 支持可选的多模态图片 URL 列表

class AmbientLogRequest(BaseModel):
    group_id: str
    sender_name: str
    content: str


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
        role="ambient",
        content=formatted_content,
        processed=1,  # 标注为已处理，不触发自进化总结，防止浪费算力
    ))
    db.commit()
    return {"status": "ok", "message": "ambient log saved"}

@router.get("/search_logs")
def search_history_logs(
    user_id: str,
    keyword: str = "",
    limit: int = 50,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    提供给 Dify Agent 作为 Custom Tool 调用的数据库本地精确检索 API。
    实现无需全量 RAG 的按需、极速精准回忆。
    """
    if user_id == "all":
        # 特权：全量全局搜索，允许机器人跨群回忆
        query = db.query(ChatLog)
    else:
        query = db.query(ChatLog).filter(ChatLog.user_id == user_id)
    
    if keyword:
        # 简单粗暴且准确的 LIKE 查询，解决向量检索的时间线模糊问题
        query = query.filter(ChatLog.content.like(f"%{keyword}%"))
        
    results = query.order_by(ChatLog.id.desc()).limit(limit).all()
    results.reverse()
    
    filtered_output = []
    for row in results:
        t = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        # 增加返回来源 ID，让大模型知道这段历史来自哪个群/私聊
        source = f"[{row.user_id}]" if user_id == "all" else ""
        filtered_output.append(f"[{t}]{source} {row.role.upper()}: {row.content}")
        
    return {
        "status": "ok",
        "results_found": len(filtered_output),
        "logs": "\n".join(filtered_output) if filtered_output else "未检索到匹配结果。"
    }


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

    # 3. 本地对话历史滑窗提取（基于 session_id: 认场）
    recent_logs = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == req.session_id)
        .order_by(ChatLog.id.desc())
        .limit(20)  # 最近 10 轮
        .all()
    )
    recent_logs.reverse()  # 恢复为时间正序
    
    context_lines = []
    for lg in recent_logs:
        speaker = "User" if lg.role == "user" else "Assistant"
        context_lines.append(f"{speaker}: {lg.content}")
        
    # 强制截断防爆处理与 Autocompact (Circuit Breaker / Strip Media / LLM prompt)
    recent_context_summary = run_autocompact_circuit_breaker(context_lines, max_length=4000)

    # 4. 阻塞调用 Dify 01 (Stateless)
    try:
        answer = call_dify_chat(
            API_KEY_01_CHAT, 
            req.user_id, 
            req.query, 
            p_json, 
            s_prompt,
            recent_context_summary,
            files=req.files
        )
    except Exception as e:
        logger.error(f"Chat Proxy failed: {e}")
        raise HTTPException(status_code=502, detail=f"Upstream Error: {str(e)}")

    # 5. 双向写库 (记录在 Session 下，维持该场景的对话流)
    # 如果包含图片，在文本记录中追加占位符以便上下文回溯
    display_query = req.query
    if req.files:
        display_query += f" [包含 {len(req.files)} 张图片]"
        
    db.add(ChatLog(user_id=req.session_id, role="user", content=display_query, processed=0))
    db.add(ChatLog(user_id=req.session_id, role="model", content=answer, processed=0))
    db.commit()

    # 6. 检查进化触发阈值 (基于 Session 触发：该场景产生了足够的对话流)
    pending = db.query(ChatLog).filter(ChatLog.user_id == req.session_id, ChatLog.processed == 0).count()
    if pending >= EVOLUTION_THRESHOLD:
        # 触发进化时，针对具体的物理人触发（提取该人跨 Session 的足迹）
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
