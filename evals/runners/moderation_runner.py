"""Moderation suite runner——block rules、no_reply/no_learn/no_context。"""
from __future__ import annotations

import os
import tempfile
from evals.schema import EvalCase, EvalOutput


def run_moderation_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    matched_rule = inp.get("matched_rule", {})
    user_block_rule = inp.get("user_block_rule", {})

    # 屏蔽词规则：调用真实 check_message_moderation
    if matched_rule:
        from core.moderation import check_message_moderation

        rules = [{
            "pattern": matched_rule.get("pattern", ""),
            "scope_type": matched_rule.get("scope_type", "session"),
            "no_reply": matched_rule.get("no_reply", False),
            "no_learn": matched_rule.get("no_learn", True),
            "no_context": matched_rule.get("no_context", False),
            "enabled": True,
        }]
        result = check_message_moderation(
            inp.get("message", ""),
            chat_stream_id=inp.get("chat_stream_id", ""),
            rules=rules,
        )
        if result:
            out.db_writes["no_reply"] = result["no_reply"]
            out.db_writes["no_learn"] = result["no_learn"]
            out.db_writes["no_context"] = result["no_context"]
            out.db_writes["in_context"] = not result["no_context"]
            out.db_writes["jargon_created"] = not result["no_learn"]
            out.db_writes["expression_created"] = not result["no_learn"]
            out.should_reply = not result["no_reply"]
        else:
            # 无规则命中——正常处理
            out.db_writes["no_reply"] = False
            out.db_writes["no_learn"] = False
            out.db_writes["no_context"] = False
            out.db_writes["in_context"] = True
            out.db_writes["jargon_created"] = True
            out.db_writes["expression_created"] = True
            out.should_reply = True

    # 屏蔽用户规则：用真实 SQLite DB 调用 real _check_user_blocked
    if user_block_rule:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.database import UserBlockRule, Base

        db_path = os.path.join(tempfile.mkdtemp(), "moderation_eval.db")
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        try:
            block = UserBlockRule(
                user_id=user_block_rule.get("user_id", "blocked_user"),
                target_type=user_block_rule.get("target_type", "private"),
                group_id=user_block_rule.get("group_id", ""),
                enabled=1 if user_block_rule.get("enabled", False) else 0,
            )
            db.add(block)
            db.commit()

            from api.routes import _check_user_blocked
            blocked = _check_user_blocked(
                db,
                user_id=user_block_rule.get("user_id", "blocked_user"),
                target_type=user_block_rule.get("target_type", "private"),
                group_id=user_block_rule.get("group_id", ""),
            )
        finally:
            db.close()

        if blocked:
            out.db_writes["chatlog_written"] = True
            out.db_writes["conversation_turn_written"] = False
            out.db_writes["no_reply"] = True
            out.db_writes["no_learn"] = True
            out.db_writes["no_context"] = True
            out.db_writes["in_context"] = False
            out.db_writes["jargon_created"] = False
            out.db_writes["expression_created"] = False
            out.should_reply = False
        else:
            out.db_writes["chatlog_written"] = True
            out.db_writes["conversation_turn_written"] = True
            out.db_writes["no_reply"] = False
            out.db_writes["no_learn"] = False
            out.db_writes["no_context"] = False
            out.db_writes["in_context"] = True
            out.db_writes["jargon_created"] = True
            out.db_writes["expression_created"] = True
            out.should_reply = True

    return out
