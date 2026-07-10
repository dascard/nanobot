"""普通 API 历史、上下文与日志路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from config import EVOLUTION_THRESHOLD
from core.compaction import run_autocompact_circuit_breaker
from core.database import (
    ChatLog,
    ConversationTurn,
    Persona,
    ProactiveOutreachLease,
    ProactiveOutreachLog,
    SystemPrompt,
    User,
    get_db,
)
from core.evolution import evolution_task
from core.group_runtime.ids import normalize_group_session_id
from core.sqlite_retry import run_sqlite_locked_retry
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.routes.history_log")
router = APIRouter(tags=["history-log"])


class LogRequest(BaseModel):
    user_id: str = "default_user"
    role: str  # 'user' | 'model'
    content: str


@router.post("/chat/mark-clear")
def mark_clear(
    user_id: str,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    标记用户历史记录的清除点（非删除）。
    ConversationTurn 旧记录立即删除，ChatLog 保留（进化素材）。
    之后的历史查询只拉取此时间点之后的 ConversationTurn。
    """
    try:
        now = db_now_naive()
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            db.add(User(id=user_id, history_clear_at=now))
        else:
            user.history_clear_at = now

        # 删除旧 ConversationTurn，保留 ChatLog 做进化素材
        deleted = db.query(ConversationTurn).filter(
            ConversationTurn.user_id == user_id,
            ConversationTurn.created_at <= now,
        ).delete()
        cancelled_outreach = db.query(ProactiveOutreachLog).filter(
            ProactiveOutreachLog.user_id == user_id,
            ProactiveOutreachLog.status.in_(("pending", "candidate")),
            ProactiveOutreachLog.created_at <= now,
        ).update(
            {ProactiveOutreachLog.status: "cancelled"},
            synchronize_session=False,
        )
        cancelled_evaluations = db.query(ProactiveOutreachLease).filter(
            ProactiveOutreachLease.user_id == user_id,
        ).delete(synchronize_session=False)
        try:
            from app.session_memory.rolling_summary import archive_active_summaries_for_user

            archived = archive_active_summaries_for_user(db, user_id)
        except Exception:
            archived = 0
        db.commit()
        logger.info(
            f"[/mark-clear] Clear marker set for user={user_id}, "
            f"deleted {deleted} ConversationTurn rows, archived {archived} rolling summaries"
        )
        return {
            "status": "success",
            "message": "已标记清除点",
            "deleted_context_rows": deleted,
            "archived_rolling_summaries": archived,
            "cancelled_outreach_candidates": cancelled_outreach,
            "cancelled_outreach_evaluations": cancelled_evaluations,
        }
    except Exception:
        logger.exception("[/mark-clear] Failed")
        raise HTTPException(status_code=500, detail="内部错误")


@router.get("/chat/history-summary")
def get_history_summary(
    user_id: str,
    session_id: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    获取对话历史摘要。
    返回最近 limit 条记录的统计信息和摘要。
    """
    try:
        query = db.query(ChatLog).filter(ChatLog.user_id == user_id)
        if session_id:
            query = query.filter(ChatLog.session_id == session_id)

        recent = query.order_by(ChatLog.id.desc()).limit(limit).all()
        recent.reverse()  # Chronological order

        user_msgs = [{"time": log.created_at, "preview": log.content[:100]}
                     for log in recent if log.role == "user"]
        assistant_msgs = [{"time": log.created_at, "preview": log.content[:100]}
                          for log in recent if log.role == "assistant"]
        tool_msgs = [{"time": log.created_at, "preview": log.content[:100]}
                     for log in recent if log.role == "tool"]

        return {
            "user_id": user_id,
            "session_id": session_id or "all",
            "user_messages_count": len(user_msgs),
            "assistant_messages_count": len(assistant_msgs),
            "tool_calls_count": len(tool_msgs),
            "total_count": len(recent),
            "recent_user_messages": user_msgs[-5:],
            "recent_assistant_messages": assistant_msgs[-5:],
            "recent_tool_calls": tool_msgs[-5:],
        }
    except Exception:
        logger.exception("[/history-summary] Failed")
        raise HTTPException(status_code=500, detail="内部错误")


@router.post("/chat/compact-history")
def compact_history(
    user_id: str,
    session_id: str | None = None,
    keep_recent: int = 20,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    压缩对话上下文：清理超过 keep_recent 条的 ConversationTurn 旧记录。
    ChatLog 不受影响（保留做进化素材）。
    """
    try:
        query = db.query(ConversationTurn).filter(ConversationTurn.user_id == user_id)
        if session_id:
            query = query.filter(ConversationTurn.session_id == session_id)

        total = query.count()
        if total <= keep_recent:
            return {
                "status": "success",
                "message": f"上下文记录数 ({total}) 未超过限制 ({keep_recent})",
                "deleted_count": 0
            }

        oldest_kept = query.order_by(ConversationTurn.id.desc()).limit(keep_recent).all()
        if oldest_kept:
            min_id_to_keep = min(t.id for t in oldest_kept)
            deleted = query.filter(ConversationTurn.id < min_id_to_keep).delete()
            db.commit()
            logger.info(f"[/compact-history] Compacted context for user={user_id}: kept={keep_recent}, deleted={deleted}")
            return {
                "status": "success",
                "message": f"已压缩上下文，保留最近 {keep_recent} 条",
                "deleted_count": deleted,
                "remaining_count": keep_recent
            }
        else:
            return {"status": "error", "message": "无法确定要保留的记录", "deleted_count": 0}
    except Exception:
        logger.exception("[/compact-history] Failed")
        raise HTTPException(status_code=500, detail="内部错误")


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
    def operation() -> None:
        if not db.query(User).filter(User.id == log_req.user_id).first():
            db.add(User(id=log_req.user_id))
        db.add(ChatLog(
            user_id=log_req.user_id,
            role=log_req.role,
            content=log_req.content,
            processed=0,
        ))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="chat_log_submit",
        logger=logger,
    )

    pending = (
        db.query(ChatLog)
        .filter(ChatLog.user_id == log_req.user_id, ChatLog.processed == 0)
        .count()
    )
    if pending >= EVOLUTION_THRESHOLD:
        background_tasks.add_task(evolution_task, log_req.user_id)

    return {"status": "ok", "unprocessed_logs": pending}


class AmbientLogRequest(BaseModel):
    group_id: str = "unknown"
    session_name: str | None = None
    sender_name: str = "unknown"
    content: str = ""
    message_id: str | None = None


@router.post("/log_ambient")
def submit_ambient_log(req: AmbientLogRequest, db: Session = Depends(get_db),
                       _auth=Depends(verify_token)):
    """[DEPRECATED] 使用 /group/message 替代。"""
    logger.warning("[DEPRECATED] /log_ambient called by group=%s — migrate to /group/message", req.group_id)
    actual_user_id = normalize_group_session_id(req.group_id)
    formatted = f"[{req.sender_name}]: {req.content}"

    def operation() -> None:
        user = db.query(User).filter(User.id == actual_user_id).first()
        if not user:
            db.add(User(id=actual_user_id, name=req.session_name or ""))
        elif req.session_name and user.name != req.session_name:
            user.name = req.session_name
        db.add(ChatLog(user_id=actual_user_id, session_id=actual_user_id,
                       sender_name=req.sender_name, session_name=req.session_name,
                       role="ambient", content=formatted, processed=1,
                       message_id=req.message_id))
        db.commit()

    run_sqlite_locked_retry(
        operation,
        rollback=db.rollback,
        label="ambient_log_submit",
        logger=logger,
    )
    return {"status": "ok", "message": "ambient log saved [deprecated]"}


@router.get("/search_logs")
def search_history_logs(
    user_id: str,
    keyword: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    context_size: int = Query(default=0, ge=0, le=20),
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """
    提供给外部工具调用的数据库本地精确检索 API。
    实现无需全量 RAG 的按需、极速精准回忆。带有上下文支持。
    """
    def _like_contains(value: str) -> str:
        escaped = str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

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
            user_pattern = _like_contains(user_id)
            base_query = base_query.filter(
                or_(
                    ChatLog.sender_name.like(user_pattern, escape="\\"),
                    ChatLog.session_name.like(user_pattern, escape="\\")
                )
            )

    if not keyword:
        # 无关键词：直接返回最新记录
        results = base_query.order_by(ChatLog.id.desc()).limit(limit).all()
        results.reverse()
        final_logs = results
    else:
        # 有关键词：查找匹配及其上下文
        keyword_pattern = _like_contains(keyword)
        matches = base_query.filter(
            ChatLog.content.like(keyword_pattern, escape="\\")
        ).order_by(ChatLog.id.desc()).limit(limit).all()

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
