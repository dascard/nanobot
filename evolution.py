"""
后台自进化任务。
从 DB 捞未处理日志，依次调用 Dify 02→03→04，回写结果。
"""
import threading
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from config import API_KEY_02, API_KEY_03, API_KEY_04, EVOLUTION_THRESHOLD
from database import SessionLocal, ChatLog, Persona, SystemPrompt
from dify_client import call_dify_workflow

logger = logging.getLogger("nanobot.evolution")

# 进化锁：同一用户不可并发触发
_locks: dict[str, threading.Lock] = {}


def _get_lock(user_id: str) -> threading.Lock:
    if user_id not in _locks:
        _locks[user_id] = threading.Lock()
    return _locks[user_id]


def evolution_task(user_id: str) -> None:
    """
    全链路自进化：
      1. 捞未处理日志
      2. Dify 02 → 结构化摘要
      3. Dify 03 → 画像合并
      4. Dify 04 → Prompt 审计
      5. 回写 DB
    """
    lock = _get_lock(user_id)
    if not lock.acquire(blocking=False):
        logger.warning(f"Evolution already running for [{user_id}], skipping.")
        return

    # 后台任务必须自己管理 session（不复用 FastAPI Depends）
    db: Session = SessionLocal()
    try:
        logger.info(f"══════ Evolution START [{user_id}] ══════")

        # ── Step 1: 捞日志 ──
        logs = (
            db.query(ChatLog)
            .filter(ChatLog.user_id == user_id, ChatLog.processed == 0)
            .order_by(ChatLog.id.asc())
            .all()
        )
        if len(logs) < EVOLUTION_THRESHOLD:
            logger.info(f"  Only {len(logs)} logs, need {EVOLUTION_THRESHOLD}. Skip.")
            return

        raw_dialog = "\n".join(f"[{log.role}] {log.content}" for log in logs)
        logger.info(f"  Fetched {len(logs)} logs.")

        # 标记为处理中（防竞态）
        for log in logs:
            log.processed = 1
        db.commit()

        # ── Step 2: 读现有画像和设定 ──
        persona_obj = db.query(Persona).filter(Persona.user_id == user_id).first()
        sys_obj = db.query(SystemPrompt).filter(SystemPrompt.user_id == user_id).first()

        existing_persona = persona_obj.persona_json if persona_obj else "{}"
        current_prompt = sys_obj.prompt_text if sys_obj else "你是一个智能对话助手。"

        # ── Step 3: Dify 02 (日志收集器) ──
        logger.info("  [02] Dialog Log Collector...")
        out_02 = call_dify_workflow(API_KEY_02, {
            "raw_dialog_logs": raw_dialog,
            "user_id": user_id,
        })
        structured_json = out_02.get("structured_json", "")
        if not structured_json:
            raise RuntimeError("02 output 'structured_json' is empty")

        # ── Step 4: Dify 03 (画像提炼器) ──
        logger.info("  [03] Persona Extractor...")
        out_03 = call_dify_workflow(API_KEY_03, {
            "new_log_summary": structured_json,
            "user_id": user_id,
            "existing_persona": existing_persona,
        })
        final_persona = out_03.get("final_persona_json", "")
        if not final_persona:
            raise RuntimeError("03 output 'final_persona_json' is empty")

        # ── Step 5: Dify 04 (Prompt 审计) ──
        logger.info("  [04] Prompt Audit Engine...")
        out_04 = call_dify_workflow(API_KEY_04, {
            "current_system_prompt": current_prompt,
            "user_persona_json": final_persona,
            "audit_mode": "完整审计（含草稿）",
        })
        final_prompt = out_04.get("final_system_prompt", "")
        if not final_prompt:
            raise RuntimeError("04 output 'final_system_prompt' is empty")

        # ── Step 6: 回写 DB ──
        if persona_obj:
            persona_obj.persona_json = final_persona
            persona_obj.updated_at = datetime.utcnow()
        else:
            db.add(Persona(user_id=user_id, persona_json=final_persona))

        if sys_obj:
            sys_obj.prompt_text = final_prompt
            sys_obj.updated_at = datetime.utcnow()
        else:
            db.add(SystemPrompt(user_id=user_id, prompt_text=final_prompt))

        db.commit()
        logger.info(f"══════ Evolution SUCCESS [{user_id}] ══════")

    except Exception as e:
        logger.error(f"Evolution FAILED [{user_id}]: {e}", exc_info=True)
        try:
            db.rollback()
            db.query(ChatLog).filter(
                ChatLog.user_id == user_id, ChatLog.processed == 1
            ).update({ChatLog.processed: 0})
            db.commit()
        except Exception:
            logger.error("Failed to rollback log states", exc_info=True)
    finally:
        db.close()
        lock.release()
