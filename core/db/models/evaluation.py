"""回复和离线评测模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from core.db.base import Base


class ReplyEvalCase(Base):
    __tablename__ = "reply_eval_cases"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, unique=True, index=True)
    title = Column(String, default="")
    chat_type = Column(String, default="group")
    input_text = Column(Text, default="")
    context_json = Column(Text, default="{}")
    expected_action = Column(String, default="any")
    expected_keywords_json = Column(Text, default="[]")
    forbidden_keywords_json = Column(Text, default="[]")
    source = Column(String, default="manual")
    tags_json = Column(Text, default="[]")
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class ReplyEvalRun(Base):
    __tablename__ = "reply_eval_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, default="")
    variant = Column(String, default="")
    total = Column(Integer, default=0)
    reply_contract_ok = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)


class ReplyEvalResult(Base):
    __tablename__ = "reply_eval_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True)
    agent_run_id = Column(String, index=True, default="")
    trace_id = Column(String, index=True, default="")
    prompt_sha256 = Column(String, index=True, default="")
    case_id = Column(String, index=True)
    variant = Column(String, default="")
    expected_action = Column(String, default="")
    actual_action = Column(String, default="")
    called_reply_or_no_reply = Column(Integer, default=0)
    retry_used = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    raw_output_preview = Column(Text, default="")
    final_content_preview = Column(Text, default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class EvalCandidate(Base):
    __tablename__ = "eval_candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, unique=True, nullable=False)
    suite = Column(String, nullable=False)
    source = Column(String, default="log")
    source_ref = Column(String, default="")
    description = Column(Text, default="")
    input_json = Column(Text, default="{}")
    expected_json = Column(Text, default='{"needs_label": true}')
    tags_json = Column(Text, default="[]")
    status = Column(String, default="candidate")
    priority = Column(Integer, default=0)
    fingerprint = Column(String, index=True, default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class EvalSampleCursor(Base):
    __tablename__ = "eval_sample_cursors"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String, nullable=False)
    source_key = Column(String, nullable=False)
    cursor_json = Column(Text, default="{}")
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    suite = Column(String)
    git_sha = Column(String, default="")
    status = Column(String, default="running")
    total = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    pass_rate = Column(Float, default=0.0)
    summary_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


class EvalRunResult(Base):
    __tablename__ = "eval_run_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, index=True)
    case_id = Column(String)
    suite = Column(String)
    passed = Column(Integer)
    score = Column(Float)
    errors_json = Column(Text, default="[]")
    output_json = Column(Text, default="{}")


__all__ = [
    "EvalCandidate",
    "EvalRun",
    "EvalRunResult",
    "EvalSampleCursor",
    "ReplyEvalCase",
    "ReplyEvalResult",
    "ReplyEvalRun",
]
