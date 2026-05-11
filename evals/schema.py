"""Eval 数据模型——EvalCase / EvalOutput / EvalResult。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    id: str
    suite: str
    description: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class EvalOutput(BaseModel):
    case_id: str
    suite: str
    reply_text: str = ""
    should_reply: bool | None = None
    reply_meta: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[str] = Field(default_factory=list)
    db_writes: dict[str, Any] = Field(default_factory=dict)
    timing_action: str = ""
    model_used: str = ""
    http_status: int | None = None
    content_type: str = ""
    errors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    case_id: str
    suite: str
    passed: bool
    score: float = 0.0
    errors: list[str] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)


class SuiteReport(BaseModel):
    suite: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    failed_cases: list[dict[str, Any]] = Field(default_factory=list)
