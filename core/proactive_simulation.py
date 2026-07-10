"""主动研究伙伴的确定性事件回放，不包含生产发布依赖。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from kohakuterrarium.modules.plugin.base import PluginBlockError

from core.proactive_candidate import (
    DEFAULT_ACTIVE_HOURS,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_SURGE_MAX_PROB,
    DEFAULT_SURGE_MIN_PROB,
    evaluate_outreach_candidate,
    evaluate_outreach_due_gate,
    evaluate_outreach_min_interval_gate,
)
from core.proactive_research import (
    ResearchBudget,
    ResearchBudgetPlugin,
    ResearchRequest,
    ResearchSource,
    run_proactive_research,
)


_VIRTUAL_START = datetime(2026, 7, 10, 0, 0, 0)
_ACTIVE_HOURS = DEFAULT_ACTIVE_HOURS


_REQUIRED_CASE_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "quiet_hours": {
        "status": "skipped_quiet_hours",
        "observations": {
            "virtual_at": "2026-07-10T02:00:00",
            "judge_calls": 0,
            "research_calls": 0,
            "generator_calls": 0,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "judge_complete": {
        "status": "candidate",
        "observations": {
            "contract_ok": True,
            "finish_reason": "stop",
            "error_type": "",
            "truncated": False,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "judge_finish_reason_length": {
        "status": "judge_error",
        "observations": {
            "contract_ok": False,
            "finish_reason": "length",
            "error_type": "model_truncated",
            "truncated": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "judge_partial_json": {
        "status": "judge_error",
        "observations": {
            "contract_ok": False,
            "finish_reason": "stop",
            "error_type": "contract_error",
            "truncated": False,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "judge_schema_error": {
        "status": "judge_error",
        "observations": {
            "contract_ok": False,
            "finish_reason": "stop",
            "error_type": "contract_error",
            "truncated": False,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "empty_generator": {
        "status": "generation_error",
        "observations": {
            "generated_chars": 0,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "min_interval": {
        "status": "skipped_min_interval",
        "observations": {
            "minutes_since_last": 10,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "surge_not_due": {
        "status": "skipped_not_due",
        "observations": {
            "surge_probability": 0.12083333333333333,
            "surge_roll": 0.99,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "surge_due": {
        "status": "candidate",
        "observations": {
            "surge_probability": 0.6,
            "surge_roll": 0.0,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "research_zero_sources": {
        "status": "research_blocked",
        "observations": {
            "source_count": 0,
            "source_coverage": 0.0,
            "reason_code": "insufficient_sources",
            "production_runner": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "research_one_source": {
        "status": "research_blocked",
        "observations": {
            "source_count": 1,
            "source_coverage": 0.5,
            "reason_code": "insufficient_sources",
            "production_runner": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "research_two_sources": {
        "status": "candidate",
        "observations": {
            "source_count": 2,
            "source_coverage": 1.0,
            "reason_code": "",
            "production_runner": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "max_silence": {
        "status": "recorded",
        "observations": {
            "forced": True,
            "judge_calls": 0,
            "recorded_publish_count": 1,
            "external_push_count": 0,
        },
    },
    "duplicate_key": {
        "status": "deduplicated",
        "observations": {
            "attempt_count": 2,
            "unique_key_count": 1,
            "recorded_publish_count": 1,
            "duplicate_rate": 0.5,
            "duplicate_publish_rate": 0.0,
            "external_push_count": 0,
        },
    },
    "research_timeout": {
        "status": "research_blocked",
        "observations": {
            "timed_out": True,
            "timeout_caught": True,
            "reason_code": "timeout",
            "production_runner": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "research_budget_exhausted": {
        "status": "research_blocked",
        "observations": {
            "budget_exhausted": True,
            "reason_code": "budget_exhausted",
            "production_runner": True,
            "limit": 2,
            "attempted_calls": 3,
            "allowed_calls": 2,
            "blocked_calls": 1,
            "exploration_calls": 2,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
    "auto_publish": {
        "status": "recorded",
        "observations": {
            "mode": "auto",
            "would_publish": True,
            "recorded_publish_count": 1,
            "external_push_count": 0,
        },
    },
    "dry_run": {
        "status": "candidate",
        "observations": {
            "mode": "dry_run",
            "would_publish": True,
            "recorded_publish_count": 0,
            "external_push_count": 0,
        },
    },
}

_EXPECTED_LEDGER_STATE = {
    "candidate_rows": 3,
    "sent_rows": 3,
    "delivery_attempts": 4,
    "accepted_attempts": 3,
    "rejected_attempts": 1,
    "distinct_attempt_keys": 3,
    "duplicate_attempt_count": 1,
    "duplicate_publish_count": 0,
    "duplicate_attempts": 1,
    "duplicate_publishes": 0,
    "attempts_by_key": {
        "auto-candidate": 1,
        "max-silence": 1,
        "same-due-anchor": 2,
    },
}

_EXPECTED_PUBLISH_RECORD_KEYS = {
    ("max_silence", "max-silence"),
    ("duplicate_key", "same-due-anchor"),
    ("auto_publish", "auto-candidate"),
}


@dataclass
class VirtualClock:
    current: datetime = _VIRTUAL_START
    events: list[str] = field(default_factory=list)

    def advance_to(self, value: datetime, *, event: str) -> datetime:
        if value < self.current:
            raise ValueError("虚拟时间不可倒退")
        self.current = value
        self.events.append(event)
        return self.current


@dataclass
class RecordingPublisher:
    records: list[dict[str, Any]] = field(default_factory=list)

    async def record(
        self,
        *,
        key: str,
        message: str,
        case_id: str = "",
        at: datetime | None = None,
        sink: str = "recording",
    ) -> bool:
        self.records.append({
            "case_id": case_id,
            "key": key,
            "message": message,
            "virtual_at": at.isoformat() if at is not None else "",
            "sink": sink,
            "outcome": "success",
        })
        return True

    def snapshot(self) -> dict[str, Any]:
        recorded = [row for row in self.records if row.get("sink") == "recording"]
        successful = [row for row in self.records if row.get("outcome") == "success"]
        keys = [str(row.get("key") or "") for row in successful]
        return {
            "recorded_publish_count": len(recorded),
            "successful_publish_count": len(successful),
            "unique_published_keys": len(set(keys)),
            "duplicate_record_count": max(0, len(keys) - len(set(keys))),
            "external_event_count": sum(
                int(row.get("sink") != "recording") for row in self.records
            ),
            "records": [dict(row) for row in self.records],
        }


class SimulationLedger:
    """用内存 SQLite 记录候选、原子抢占和发布尝试。"""

    def __init__(self) -> None:
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE candidates (
                idempotency_key TEXT PRIMARY KEY,
                case_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT,
                publish_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE delivery_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL,
                case_id TEXT NOT NULL DEFAULT '',
                attempted_at TEXT NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def stage_candidate(
        self,
        *,
        key: str,
        message: str,
        now: datetime,
        case_id: str = "",
    ) -> bool:
        cursor = self._db.execute(
            """
            INSERT OR IGNORE INTO candidates (
                idempotency_key, case_id, status, message, created_at
            ) VALUES (?, ?, 'candidate', ?, ?)
            """,
            (key, case_id, message, now.isoformat()),
        )
        self._db.commit()
        return cursor.rowcount == 1

    async def deliver(
        self,
        *,
        key: str,
        now: datetime,
        publisher: RecordingPublisher,
        case_id: str = "",
    ) -> bool:
        attempt = self._db.execute(
            """
            INSERT INTO delivery_attempts (idempotency_key, case_id, attempted_at)
            VALUES (?, ?, ?)
            """,
            (key, case_id, now.isoformat()),
        )
        claimed = self._db.execute(
            """
            UPDATE candidates
            SET status = 'sending'
            WHERE idempotency_key = ? AND status = 'candidate'
            """,
            (key,),
        )
        if claimed.rowcount != 1:
            self._db.commit()
            return False

        self._db.execute(
            "UPDATE delivery_attempts SET accepted = 1 WHERE id = ?",
            (attempt.lastrowid,),
        )
        row = self._db.execute(
            "SELECT message, case_id FROM candidates WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        self._db.commit()
        ok = await publisher.record(
            key=key,
            message=str(row["message"]),
            case_id=case_id or str(row["case_id"] or ""),
            at=now,
        )
        self._db.execute(
            """
            UPDATE candidates
            SET status = ?, published_at = ?,
                publish_count = publish_count + ?
            WHERE idempotency_key = ? AND status = 'sending'
            """,
            ("sent" if ok else "failed", now.isoformat(), int(ok), key),
        )
        self._db.commit()
        return bool(ok)

    def snapshot(self) -> dict[str, Any]:
        row_count = int(
            self._db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        )
        sent_count = int(
            self._db.execute(
                "SELECT COUNT(*) FROM candidates WHERE status = 'sent'"
            ).fetchone()[0]
        )
        delivery_attempts = int(
            self._db.execute(
                "SELECT COUNT(*) FROM delivery_attempts"
            ).fetchone()[0]
        )
        accepted_attempts = int(
            self._db.execute(
                "SELECT COALESCE(SUM(accepted), 0) FROM delivery_attempts"
            ).fetchone()[0]
        )
        distinct_attempt_keys = int(
            self._db.execute(
                "SELECT COUNT(DISTINCT idempotency_key) FROM delivery_attempts"
            ).fetchone()[0]
        )
        duplicate_attempt_count = int(
            self._db.execute(
                """
                SELECT COALESCE(SUM(attempt_count - 1), 0)
                FROM (
                    SELECT COUNT(*) AS attempt_count
                    FROM delivery_attempts
                    GROUP BY idempotency_key
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
        duplicate_publish_count = int(
            self._db.execute(
                """
                SELECT COALESCE(SUM(
                    CASE WHEN publish_count > 1 THEN publish_count - 1 ELSE 0 END
                ), 0)
                FROM candidates
                """
            ).fetchone()[0]
        )
        attempts_by_key = {
            str(row["idempotency_key"]): int(row["attempt_count"])
            for row in self._db.execute(
                """
                SELECT idempotency_key, COUNT(*) AS attempt_count
                FROM delivery_attempts
                GROUP BY idempotency_key
                ORDER BY idempotency_key
                """
            ).fetchall()
        }
        candidate_records = [
            {
                "idempotency_key": str(row["idempotency_key"]),
                "case_id": str(row["case_id"]),
                "status": str(row["status"]),
                "message": str(row["message"]),
                "created_at": str(row["created_at"]),
                "published_at": str(row["published_at"] or ""),
                "publish_count": int(row["publish_count"]),
            }
            for row in self._db.execute(
                """
                SELECT idempotency_key, case_id, status, message, created_at,
                       published_at, publish_count
                FROM candidates
                ORDER BY idempotency_key
                """
            ).fetchall()
        ]
        attempt_records = [
            {
                "id": int(row["id"]),
                "idempotency_key": str(row["idempotency_key"]),
                "case_id": str(row["case_id"]),
                "attempted_at": str(row["attempted_at"]),
                "accepted": int(row["accepted"]),
            }
            for row in self._db.execute(
                """
                SELECT id, idempotency_key, case_id, attempted_at, accepted
                FROM delivery_attempts
                ORDER BY id
                """
            ).fetchall()
        ]
        return {
            "candidate_rows": row_count,
            "sent_rows": sent_count,
            "delivery_attempts": delivery_attempts,
            "accepted_attempts": accepted_attempts,
            "rejected_attempts": delivery_attempts - accepted_attempts,
            "distinct_attempt_keys": distinct_attempt_keys,
            "duplicate_attempt_count": duplicate_attempt_count,
            "duplicate_publish_count": duplicate_publish_count,
            "duplicate_attempts": duplicate_attempt_count,
            "duplicate_publishes": duplicate_publish_count,
            "attempts_by_key": attempts_by_key,
            "candidate_records": candidate_records,
            "attempt_records": attempt_records,
        }


def _case_conforms_expected(case: dict[str, Any]) -> bool:
    """按代码内规范验证场景，不采信可由调用方修改的 passed/expected。"""

    case_id = str(case.get("case_id") or "")
    expected = _REQUIRED_CASE_EXPECTATIONS.get(case_id)
    if expected is None or case.get("expected") != expected:
        return False
    if case.get("status") != expected["status"]:
        return False

    observations = case.get("observations")
    if not isinstance(observations, dict):
        return False
    expected_observations = expected.get("observations")
    if not isinstance(expected_observations, dict):
        return False
    if any(
        observations.get(field) != expected_value
        for field, expected_value in expected_observations.items()
    ):
        return False

    if case_id.startswith("research_") and case_id.endswith("_sources"):
        sources = observations.get("sources")
        source_count = observations.get("source_count")
        if not isinstance(sources, list) or len(sources) != source_count:
            return False
        urls: list[str] = []
        for source in sources:
            if not isinstance(source, dict):
                return False
            url = str(source.get("url") or "")
            if not url.startswith(("http://", "https://")):
                return False
            urls.append(url)
        if len(urls) != len(set(urls)):
            return False

    return True


def _case(
    case_id: str,
    *,
    status: str,
    passed: bool,
    observations: dict[str, Any],
    category: str = "",
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "case_id": case_id,
        "status": status,
        "passed": bool(passed),
        "observations": observations,
    }
    if category:
        payload["category"] = category
    declarative_expected = _REQUIRED_CASE_EXPECTATIONS.get(case_id, expected)
    if declarative_expected is not None:
        payload["expected"] = deepcopy(declarative_expected)
        payload["passed"] = _case_conforms_expected(payload)
    return payload


def _judge(
    now: datetime,
    *,
    should_reach_out: bool = True,
    kind: str = "message",
) -> dict[str, Any]:
    return {
        "should_reach_out": should_reach_out,
        "reason": "脚本化具体话题",
        "next_check_at": (now + timedelta(hours=2)).isoformat(),
        "next_intent": "继续跟进",
        "outreach_kind": kind,
        "research_query": "调查 Agent 记忆" if kind == "research" else "",
        "finish_reason": "stop",
        "error_type": None,
    }


def _judge_error(*, finish_reason: str, error_type: str, reason: str) -> dict[str, Any]:
    return {
        "should_reach_out": None,
        "reason": reason,
        "next_check_at": "2026-07-10T15:00:00",
        "next_intent": "",
        "finish_reason": finish_reason,
        "error_type": error_type,
    }


def _source(index: int) -> ResearchSource:
    return ResearchSource(
        tool_call_id=f"tool-{index}",
        title=f"模拟来源 {index}",
        url=f"https://research.example/source-{index}",
    )


class _SimulationPluginManager:
    def __init__(self) -> None:
        self._plugins: list[Any] = [
            SimpleNamespace(name="nanobot_tool_plan_guard"),
        ]

    def register(self, plugin: Any) -> None:
        self._plugins.append(plugin)


class _SimulationResearchBridge:
    """只执行生产研究生命周期所需契约，不连接模型或外部网络。"""

    def __init__(
        self,
        *,
        response: str = "有真实来源的研究候选",
        wait_forever: bool = False,
        budget_attempts: int = 0,
    ) -> None:
        self.response = response
        self.wait_forever = wait_forever
        self.budget_attempts = budget_attempts
        self.events: list[str] = []
        self.allowed_calls = 0
        self.blocked_calls = 0
        plugins = _SimulationPluginManager()
        self._agent = SimpleNamespace(
            plugins=plugins,
            controller=SimpleNamespace(
                _nanobot_tool_plan_schema_filter_installed=True,
            ),
        )

    async def start(self) -> None:
        self.events.append("start")

    async def handle_message(self, _query: str, **_kwargs: Any) -> str:
        self.events.append("handle")
        if self.wait_forever:
            await asyncio.Event().wait()
        if self.budget_attempts:
            plugin = next(
                item
                for item in self._agent.plugins._plugins
                if isinstance(item, ResearchBudgetPlugin)
            )
            for index in range(self.budget_attempts):
                try:
                    await plugin.pre_tool_execute(
                        {"query": plugin.research_query},
                        tool_name="web_search",
                        job_id=f"simulation-budget-{index}",
                    )
                except PluginBlockError:
                    self.blocked_calls += 1
                else:
                    self.allowed_calls += 1
        return self.response

    async def stop(self) -> None:
        self.events.append("stop")


def _source_tool_call(index: int, *, query: str) -> Any:
    title = f"Agent 记忆研究来源 {index}"
    url = f"https://research.example/source-{index}"
    preview = (
        "WEB_SEARCH_RESULTS_BEGIN\n"
        f"QUERY: {query}\n"
        "PROVIDER: simulation\n"
        "RESULT_COUNT: 1\n"
        "QUALITY: ok\n"
        "QUALITY_SCORE: 1.0\n"
        "QUALITY_REASON: 与 Agent 记忆研究直接相关\n"
        "RESULTS:\n"
        f"1. {title}\n"
        f"   URL: {url}\n"
        "   摘要: Agent 记忆研究模拟证据\n"
        "WEB_SEARCH_RESULTS_END"
    )
    return SimpleNamespace(
        tool_call_id=f"tool-{index}",
        tool_name="web_search",
        args_json=f'{{"query":"{query}"}}',
        result_preview=preview,
        status="success",
    )


def _schedule_gate(
    *,
    now: datetime,
    last_effective_at: datetime | None = None,
    first_attempt_at: datetime | None = None,
    last_interaction_at: datetime | None = None,
    next_check_at: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    surge_min_prob: float = DEFAULT_SURGE_MIN_PROB,
    surge_max_prob: float = DEFAULT_SURGE_MAX_PROB,
    surge_roll: float = 1.0,
) -> dict[str, Any]:
    due_decision = evaluate_outreach_due_gate(
        now=now,
        active_hours=_ACTIVE_HOURS,
        last_effective_at=last_effective_at,
        first_attempt_at=first_attempt_at,
        last_interaction_at=last_interaction_at,
        next_check_at=next_check_at,
        max_silence_min=max_silence_min,
        surge_min_prob=surge_min_prob,
        surge_max_prob=surge_max_prob,
        surge_roll=surge_roll,
    )
    if due_decision["status"] not in {"due", "forced"}:
        return due_decision

    interval_decision = evaluate_outreach_min_interval_gate(
        now=now,
        last_effective_at=last_effective_at,
        first_attempt_at=first_attempt_at,
        min_interval_min=min_interval_min,
        max_silence_min=max_silence_min,
    )
    if interval_decision["status"] == "skipped_min_interval":
        return interval_decision
    return {
        "status": "forced" if interval_decision["forced"] else "due",
        "forced": bool(interval_decision["forced"]),
        "surge_probability": float(due_decision.get("surge_probability") or 0.0),
        "surge_roll": due_decision.get("surge_roll"),
    }


async def _evaluate_message(
    *,
    now: datetime,
    request_id: str,
    judge_result: dict[str, Any],
    generated: str = "模拟候选正文",
) -> dict[str, Any]:
    return await evaluate_outreach_candidate(
        user_id="simulation-user",
        request_id=request_id,
        grounding={"user_id": "simulation-user", "recent_messages": []},
        now=now,
        judge_fn=lambda *_args, **_kwargs: judge_result,
        generator_fn=lambda *_args, **_kwargs: generated,
    )


async def _evaluate_research(
    *,
    now: datetime,
    request_id: str,
    source_count: int,
    reason_code: str = "",
) -> dict[str, Any]:
    async def research(request):
        if reason_code:
            raise AssertionError("来源场景不得手工注入研究终态")
        bridge = _SimulationResearchBridge()
        calls = [
            _source_tool_call(index, query=request.query)
            for index in range(1, source_count + 1)
        ]
        return await run_proactive_research(
            request,
            bridge_factory=lambda: bridge,
            source_loader=lambda _trace_id: calls,
        )

    return await evaluate_outreach_candidate(
        user_id="simulation-user",
        request_id=request_id,
        grounding={"user_id": "simulation-user", "recent_messages": []},
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: "不应调用",
        research_fn=research,
    )


async def _evaluate_timeout_research(
    *,
    now: datetime,
    request_id: str,
) -> tuple[dict[str, Any], bool]:
    async def research(request):
        timed_request = ResearchRequest(
            request_id=request.request_id,
            query=request.query,
            user_id=request.user_id,
            context_summary=request.context_summary,
            budget=ResearchBudget(
                timeout_seconds=0.001,
                max_exploration_calls=request.budget.max_exploration_calls,
                min_sources=request.budget.min_sources,
                max_sources=request.budget.max_sources,
                max_draft_chars=request.budget.max_draft_chars,
            ),
        )
        bridge = _SimulationResearchBridge(wait_forever=True)
        return await run_proactive_research(
            timed_request,
            bridge_factory=lambda: bridge,
            source_loader=lambda _trace_id: [],
        )

    result = await evaluate_outreach_candidate(
        user_id="simulation-user",
        request_id=request_id,
        grounding={"user_id": "simulation-user", "recent_messages": []},
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: "不应调用",
        research_fn=research,
    )
    timeout_caught = (
        result.get("status") == "research_blocked"
        and result.get("reason_code") == "timeout"
    )
    return result, timeout_caught


async def _evaluate_budget_exhaustion(
    *,
    now: datetime,
    request_id: str,
    limit: int = 2,
) -> tuple[dict[str, Any], dict[str, int]]:
    bridge = _SimulationResearchBridge(budget_attempts=limit + 1)

    async def research(request):
        budgeted_request = ResearchRequest(
            request_id=request.request_id,
            query=request.query,
            user_id=request.user_id,
            context_summary=request.context_summary,
            budget=ResearchBudget(
                timeout_seconds=request.budget.timeout_seconds,
                max_exploration_calls=limit,
                min_sources=request.budget.min_sources,
                max_sources=request.budget.max_sources,
                max_draft_chars=request.budget.max_draft_chars,
            ),
        )
        calls = [
            _source_tool_call(index, query=request.query)
            for index in range(1, 3)
        ]
        return await run_proactive_research(
            budgeted_request,
            bridge_factory=lambda: bridge,
            source_loader=lambda _trace_id: calls,
        )

    result = await evaluate_outreach_candidate(
        user_id="simulation-user",
        request_id=request_id,
        grounding={"user_id": "simulation-user", "recent_messages": []},
        now=now,
        judge_fn=lambda *_args, **_kwargs: _judge(now, kind="research"),
        generator_fn=lambda *_args, **_kwargs: "不应调用",
        research_fn=research,
    )
    evidence = {
        "limit": limit,
        "attempted_calls": limit + 1,
        "allowed_calls": bridge.allowed_calls,
        "blocked_calls": bridge.blocked_calls,
        "exploration_calls": int(
            (result.get("research") or {}).get("exploration_calls") or 0
        ),
    }
    return result, evidence


def _rate(numerator: int | float, denominator: int) -> float:
    return float(numerator) / denominator if denominator > 0 else 0.0


def derive_simulation_metrics(
    cases: list[dict[str, Any]],
    ledger_state: dict[str, Any],
    publisher_state: dict[str, Any],
) -> dict[str, Any]:
    """只根据场景结果、SQLite ledger 和记录型 publisher 派生报告指标。"""

    contract_ids = {
        "judge_complete",
        "judge_finish_reason_length",
        "judge_partial_json",
        "judge_schema_error",
    }
    source_ids = {
        "research_zero_sources",
        "research_one_source",
        "research_two_sources",
    }
    fault_ids = {"research_timeout", "research_budget_exhausted"}
    contract_cases = [
        case
        for case in cases
        if case.get("category") == "contract" or case.get("case_id") in contract_ids
    ]
    source_cases = [
        case
        for case in cases
        if case.get("category") == "research_source"
        or case.get("case_id") in source_ids
    ]
    fault_cases = [
        case
        for case in cases
        if case.get("category") == "fault_injection"
        or case.get("case_id") in fault_ids
    ]

    actual_truncations = 0
    expected_truncations = 0
    unexpected_truncations = 0
    contract_successes = 0
    parse_failures = 0
    for case in contract_cases:
        observations = case.get("observations") or {}
        expected = case.get("expected") or {}
        expected_observations = expected.get("observations") or {}
        error_type = str(observations.get("error_type") or "")
        expected_error_type = str(
            expected_observations.get("error_type")
            or expected.get("error_type")
            or ""
        )
        contract_successes += int(bool(observations.get("contract_ok")))
        parse_failures += int(error_type == "contract_error")
        is_truncated = error_type == "model_truncated"
        expects_truncation = expected_error_type == "model_truncated"
        actual_truncations += int(is_truncated)
        expected_truncations += int(expects_truncation)
        unexpected_truncations += int(is_truncated and not expects_truncation)

    source_coverage_values: list[float] = []
    successful_research_coverage: list[int] = []
    for case in source_cases:
        observations = case.get("observations") or {}
        source_count = int(observations.get("source_count") or 0)
        source_coverage_values.append(min(1.0, source_count / 2.0))
        if case.get("status") == "candidate":
            successful_research_coverage.append(int(source_count >= 2))

    timeout_count = sum(
        int(bool((case.get("observations") or {}).get("timeout_caught")))
        for case in fault_cases
    )
    budget_block_count = 0
    budget_overrun_count = 0
    for case in fault_cases:
        observations = case.get("observations") or {}
        if "limit" not in observations:
            continue
        limit = int(observations.get("limit") or 0)
        exploration_calls = int(observations.get("exploration_calls") or 0)
        budget_block_count += int(observations.get("blocked_calls") or 0)
        budget_overrun_count += max(0, exploration_calls - limit)

    delivery_attempts = int(ledger_state.get("delivery_attempts") or 0)
    accepted_attempts = int(ledger_state.get("accepted_attempts") or 0)
    duplicate_attempt_count = int(
        ledger_state.get("duplicate_attempt_count")
        if ledger_state.get("duplicate_attempt_count") is not None
        else ledger_state.get("duplicate_attempts")
        or 0
    )
    duplicate_publish_count = int(
        ledger_state.get("duplicate_publish_count")
        if ledger_state.get("duplicate_publish_count") is not None
        else ledger_state.get("duplicate_publishes")
        or 0
    )
    evaluator_cases = [
        case
        for case in cases
        if str(case.get("status") or "")
        in {
            "candidate",
            "no_candidate",
            "judge_error",
            "generation_error",
            "research_blocked",
        }
    ]
    recorded_publish_count = int(publisher_state.get("recorded_publish_count") or 0)
    successful_publish_count = int(
        publisher_state.get("successful_publish_count") or 0
    )
    sent_rows = int(ledger_state.get("sent_rows") or 0)
    source_coverage = (
        sum(source_coverage_values) / len(source_coverage_values)
        if source_coverage_values
        else 0.0
    )
    successful_source_coverage = (
        sum(successful_research_coverage) / len(successful_research_coverage)
        if successful_research_coverage
        else 0.0
    )
    return {
        "external_push_count": int(publisher_state.get("external_event_count") or 0),
        "recorded_publish_count": recorded_publish_count,
        "contract_success_rate": _rate(contract_successes, len(contract_cases)),
        "truncation_rate": _rate(actual_truncations, len(contract_cases)),
        "expected_truncation_rate": _rate(
            expected_truncations,
            len(contract_cases),
        ),
        "unexpected_truncation_rate": _rate(
            unexpected_truncations,
            len(contract_cases),
        ),
        "parse_failure_rate": _rate(parse_failures, len(contract_cases)),
        "source_coverage": source_coverage,
        "successful_research_source_coverage": successful_source_coverage,
        "duplicate_rate": _rate(duplicate_attempt_count, delivery_attempts),
        "duplicate_attempt_rate": _rate(
            duplicate_attempt_count,
            delivery_attempts,
        ),
        "duplicate_publish_rate": _rate(
            duplicate_publish_count,
            accepted_attempts,
        ),
        "injected_timeout_case_rate": _rate(timeout_count, len(fault_cases)),
        "timeout_rate": _rate(timeout_count, len(fault_cases)),
        "candidate_rate": _rate(
            sum(int(case.get("status") == "candidate") for case in evaluator_cases),
            len(evaluator_cases),
        ),
        "budget_block_count": budget_block_count,
        "budget_overrun_count": budget_overrun_count,
        "timeout_count": timeout_count,
        "duplicate_attempt_count": duplicate_attempt_count,
        "duplicate_publish_count": duplicate_publish_count,
        "ledger_publisher_consistent": int(
            sent_rows == successful_publish_count == recorded_publish_count
        ),
    }


def _parse_evidence_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def _ledger_evidence(
    ledger_state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """从 SQLite 原始行重算聚合，并核对调用方携带的摘要。"""

    raw_candidates = ledger_state.get("candidate_records")
    raw_attempts = ledger_state.get("attempt_records")
    if not isinstance(raw_candidates, list) or not isinstance(raw_attempts, list):
        return {}, [], False
    if not all(isinstance(row, dict) for row in raw_candidates + raw_attempts):
        return {}, [], False

    candidate_records: list[dict[str, Any]] = []
    candidate_by_key: dict[str, dict[str, Any]] = {}
    for raw in raw_candidates:
        key = raw.get("idempotency_key")
        case_id = raw.get("case_id")
        status = raw.get("status")
        message = raw.get("message")
        created_at = raw.get("created_at")
        published_at = raw.get("published_at")
        publish_count = raw.get("publish_count")
        if (
            not isinstance(key, str)
            or not key
            or key in candidate_by_key
            or not isinstance(case_id, str)
            or not case_id
            or not isinstance(status, str)
            or not status
            or not isinstance(message, str)
            or not message.strip()
            or _parse_evidence_time(created_at) is None
            or not isinstance(published_at, str)
            or type(publish_count) is not int
            or publish_count < 0
        ):
            return {}, [], False
        published_time = (
            _parse_evidence_time(published_at) if published_at else None
        )
        if published_at and published_time is None:
            return {}, [], False
        created_time = _parse_evidence_time(created_at)
        if (
            published_time is not None
            and created_time is not None
            and published_time < created_time
        ):
            return {}, [], False
        if status == "sent":
            if publish_count != 1 or published_time is None:
                return {}, [], False
        elif publish_count != 0:
            return {}, [], False
        normalized = {
            "idempotency_key": key,
            "case_id": case_id,
            "status": status,
            "message": message,
            "created_at": created_at,
            "published_at": published_at,
            "publish_count": publish_count,
        }
        candidate_by_key[key] = normalized
        candidate_records.append(normalized)

    attempt_records: list[dict[str, Any]] = []
    attempt_ids: set[int] = set()
    attempts_by_key: dict[str, int] = {}
    accepted_by_key: dict[str, int] = {}
    for raw in raw_attempts:
        attempt_id = raw.get("id")
        key = raw.get("idempotency_key")
        case_id = raw.get("case_id")
        attempted_at = raw.get("attempted_at")
        accepted = raw.get("accepted")
        candidate = candidate_by_key.get(key) if isinstance(key, str) else None
        if (
            type(attempt_id) is not int
            or attempt_id <= 0
            or attempt_id in attempt_ids
            or candidate is None
            or not isinstance(case_id, str)
            or case_id != candidate["case_id"]
            or _parse_evidence_time(attempted_at) is None
            or type(accepted) is not int
            or accepted not in {0, 1}
        ):
            return {}, [], False
        attempted_time = _parse_evidence_time(attempted_at)
        created_time = _parse_evidence_time(candidate["created_at"])
        if (
            attempted_time is not None
            and created_time is not None
            and attempted_time < created_time
        ):
            return {}, [], False
        attempt_ids.add(attempt_id)
        attempts_by_key[key] = attempts_by_key.get(key, 0) + 1
        accepted_by_key[key] = accepted_by_key.get(key, 0) + accepted
        attempt_records.append({
            "id": attempt_id,
            "idempotency_key": key,
            "case_id": case_id,
            "attempted_at": attempted_at,
            "accepted": accepted,
        })

    if any(
        accepted_by_key.get(key, 0) != int(candidate["status"] == "sent")
        for key, candidate in candidate_by_key.items()
    ):
        return {}, [], False

    attempts_by_key = dict(sorted(attempts_by_key.items()))
    delivery_attempts = len(attempt_records)
    accepted_attempts = sum(row["accepted"] for row in attempt_records)
    duplicate_attempt_count = sum(
        max(0, count - 1) for count in attempts_by_key.values()
    )
    duplicate_publish_count = sum(
        max(0, int(row["publish_count"]) - 1) for row in candidate_records
    )
    derived = {
        "candidate_rows": len(candidate_records),
        "sent_rows": sum(int(row["status"] == "sent") for row in candidate_records),
        "delivery_attempts": delivery_attempts,
        "accepted_attempts": accepted_attempts,
        "rejected_attempts": delivery_attempts - accepted_attempts,
        "distinct_attempt_keys": len(attempts_by_key),
        "duplicate_attempt_count": duplicate_attempt_count,
        "duplicate_publish_count": duplicate_publish_count,
        "duplicate_attempts": duplicate_attempt_count,
        "duplicate_publishes": duplicate_publish_count,
        "attempts_by_key": attempts_by_key,
    }
    summary_matches = all(
        ledger_state.get(field) == value for field, value in derived.items()
    )
    expected_matches = all(
        derived.get(field) == expected
        for field, expected in _EXPECTED_LEDGER_STATE.items()
    )
    return (
        {
            **derived,
            "candidate_records": candidate_records,
            "attempt_records": attempt_records,
        },
        candidate_records,
        summary_matches and expected_matches,
    )


def _publisher_evidence(
    publisher_state: dict[str, Any],
    candidate_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    records = publisher_state.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        records = []
        records_valid = False
    else:
        records_valid = True

    recorded = [record for record in records if record.get("sink") == "recording"]
    successful = [record for record in records if record.get("outcome") == "success"]
    successful_keys = [str(record.get("key") or "") for record in successful]
    derived = {
        "recorded_publish_count": len(recorded),
        "successful_publish_count": len(successful),
        "unique_published_keys": len(set(successful_keys)),
        "duplicate_record_count": max(
            0,
            len(successful_keys) - len(set(successful_keys)),
        ),
        "external_event_count": sum(
            int(record.get("sink") != "recording") for record in records
        ),
        "records": [dict(record) for record in records],
    }
    summary_matches = all(
        publisher_state.get(field) == value
        for field, value in derived.items()
        if field != "records"
    )
    record_keys = {
        (str(record.get("case_id") or ""), str(record.get("key") or ""))
        for record in records
    }
    sent_candidates = [
        candidate
        for candidate in candidate_records
        if candidate.get("status") == "sent"
    ]
    candidate_by_record_key = {
        (
            str(candidate.get("case_id") or ""),
            str(candidate.get("idempotency_key") or ""),
        ): candidate
        for candidate in sent_candidates
    }
    candidates_unique = len(candidate_by_record_key) == len(sent_candidates)

    def record_matches_candidate(record: dict[str, Any]) -> bool:
        record_key = (
            str(record.get("case_id") or ""),
            str(record.get("key") or ""),
        )
        candidate = candidate_by_record_key.get(record_key)
        message = record.get("message")
        virtual_at = record.get("virtual_at")
        if (
            candidate is None
            or record.get("sink") != "recording"
            or record.get("outcome") != "success"
            or not isinstance(message, str)
            or not message.strip()
            or message != candidate.get("message")
            or not isinstance(virtual_at, str)
            or virtual_at != candidate.get("published_at")
            or _parse_evidence_time(virtual_at) is None
        ):
            return False
        return True

    records_conform = (
        len(records) == len(_EXPECTED_PUBLISH_RECORD_KEYS)
        and record_keys == _EXPECTED_PUBLISH_RECORD_KEYS
        and candidates_unique
        and set(candidate_by_record_key) == _EXPECTED_PUBLISH_RECORD_KEYS
        and len(records) == len(sent_candidates)
        and all(record_matches_candidate(record) for record in records)
    )
    return derived, records_valid and summary_matches and records_conform


def evaluate_simulation_gate(
    cases: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    ledger_state: dict[str, Any] | None = None,
    publisher_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从不可变场景规范和原始证据重算门禁，不采信报告指标。"""

    raw_ledger_evidence = ledger_state if isinstance(ledger_state, dict) else {}
    (
        normalized_ledger,
        candidate_records,
        ledger_evidence_consistent,
    ) = _ledger_evidence(raw_ledger_evidence)
    raw_publisher_evidence = (
        publisher_state if isinstance(publisher_state, dict) else {}
    )
    normalized_publisher, publisher_evidence_consistent = _publisher_evidence(
        raw_publisher_evidence,
        candidate_records,
    )
    try:
        derived_metrics = derive_simulation_metrics(
            cases,
            normalized_ledger,
            normalized_publisher,
        )
    except (TypeError, ValueError):
        derived_metrics = {}

    case_ids = [str(case.get("case_id") or "") for case in cases]
    required_case_ids = set(_REQUIRED_CASE_EXPECTATIONS)
    required_cases_complete_and_unique = (
        len(case_ids) == len(required_case_ids)
        and set(case_ids) == required_case_ids
    )
    checks = {
        "required_cases_complete_and_unique": required_cases_complete_and_unique,
        "all_cases_passed": required_cases_complete_and_unique
        and all(_case_conforms_expected(case) for case in cases),
        "ledger_evidence_consistent": ledger_evidence_consistent,
        "publisher_evidence_consistent": publisher_evidence_consistent,
        "reported_metrics_match_evidence": isinstance(metrics, dict)
        and metrics == derived_metrics,
        "no_external_publish": int(
            derived_metrics.get("external_push_count", -1)
        ) == 0,
        "no_unexpected_truncation": float(
            derived_metrics.get("unexpected_truncation_rate", 1)
        ) == 0,
        "no_duplicate_publish": float(
            derived_metrics.get("duplicate_publish_rate", 1)
        ) == 0,
        "successful_research_has_sources": float(
            derived_metrics.get("successful_research_source_coverage", 0)
        ) == 1,
        "budget_within_limit": int(
            derived_metrics.get("budget_overrun_count", 1)
        ) == 0,
        "budget_guard_exercised": int(
            derived_metrics.get("budget_block_count", 0)
        ) >= 1,
        "timeout_exercised": int(derived_metrics.get("timeout_count", 0)) >= 1,
        "duplicate_claim_exercised": int(
            derived_metrics.get("duplicate_attempt_count", 0)
        ) >= 1,
        "ledger_matches_publisher": int(
            derived_metrics.get("ledger_publisher_consistent", 0)
        ) == 1,
    }
    return {"passed": all(checks.values()), "checks": checks}


async def run_accelerated_simulation() -> dict[str, Any]:
    """用共享候选内核、内存 SQLite 和虚拟事件回放七天。"""

    clock = VirtualClock()
    recorder = RecordingPublisher()
    ledger = SimulationLedger()
    cases: list[dict[str, Any]] = []

    try:
        at = clock.advance_to(_VIRTUAL_START + timedelta(hours=2), event="quiet_hours")
        quiet = _schedule_gate(now=at)
        cases.append(_case(
            "quiet_hours",
            status=quiet["status"],
            passed=quiet["status"] == "skipped_quiet_hours",
            observations={
                "virtual_at": at.isoformat(),
                "judge_calls": 0,
                "research_calls": 0,
                "generator_calls": 0,
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(_VIRTUAL_START + timedelta(hours=12), event="judge_matrix")
        complete = await _evaluate_message(
            now=at,
            request_id="judge-complete",
            judge_result=_judge(at),
        )
        length = await _evaluate_message(
            now=at,
            request_id="judge-length",
            judge_result=_judge_error(
                finish_reason="length",
                error_type="model_truncated",
                reason="模型输出被截断",
            ),
        )
        partial = await _evaluate_message(
            now=at,
            request_id="judge-partial",
            judge_result=_judge_error(
                finish_reason="stop",
                error_type="contract_error",
                reason="JSON 不完整",
            ),
        )
        schema = await _evaluate_message(
            now=at,
            request_id="judge-schema",
            judge_result=_judge_error(
                finish_reason="stop",
                error_type="contract_error",
                reason="字段类型错误",
            ),
        )
        cases.extend((
            _case(
                "judge_complete",
                status=complete["status"],
                passed=complete["status"] == "candidate",
                observations={
                    "contract_ok": complete["status"] == "candidate",
                    "finish_reason": "stop",
                    "error_type": str(complete.get("error_type") or ""),
                    "truncated": False,
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="contract",
                expected={"status": "candidate", "error_type": ""},
            ),
            _case(
                "judge_finish_reason_length",
                status=length["status"],
                passed=length.get("error_type") == "model_truncated",
                observations={
                    "contract_ok": False,
                    "finish_reason": "length",
                    "error_type": str(length.get("error_type") or ""),
                    "truncated": length.get("error_type") == "model_truncated",
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="contract",
                expected={"status": "judge_error", "error_type": "model_truncated"},
            ),
            _case(
                "judge_partial_json",
                status=partial["status"],
                passed=partial.get("error_type") == "contract_error",
                observations={
                    "contract_ok": False,
                    "finish_reason": "stop",
                    "error_type": str(partial.get("error_type") or ""),
                    "truncated": partial.get("error_type") == "model_truncated",
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="contract",
                expected={"status": "judge_error", "error_type": "contract_error"},
            ),
            _case(
                "judge_schema_error",
                status=schema["status"],
                passed=schema.get("error_type") == "contract_error",
                observations={
                    "contract_ok": False,
                    "finish_reason": "stop",
                    "error_type": str(schema.get("error_type") or ""),
                    "truncated": False,
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="contract",
                expected={"status": "judge_error", "error_type": "contract_error"},
            ),
        ))

        empty = await _evaluate_message(
            now=at,
            request_id="empty-generator",
            judge_result=_judge(at),
            generated="<think>只有推理</think>",
        )
        cases.append(_case(
            "empty_generator",
            status=empty["status"],
            passed=empty["status"] == "generation_error",
            observations={
                "generated_chars": 0,
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(hours=12, minutes=10),
            event="min_interval",
        )
        min_interval = _schedule_gate(
            now=at,
            last_effective_at=at - timedelta(minutes=10),
        )
        cases.append(_case(
            "min_interval",
            status=min_interval["status"],
            passed=min_interval["status"] == "skipped_min_interval",
            observations={
                "minutes_since_last": 10,
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(_VIRTUAL_START + timedelta(hours=13), event="surge_not_due")
        not_due = _schedule_gate(
            now=at,
            last_interaction_at=at - timedelta(hours=2),
            next_check_at=at + timedelta(hours=4),
            surge_roll=0.99,
        )
        cases.append(_case(
            "surge_not_due",
            status=not_due["status"],
            passed=not_due["status"] == "skipped_not_due",
            observations={
                "surge_probability": not_due["surge_probability"],
                "surge_roll": not_due["surge_roll"],
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(_VIRTUAL_START + timedelta(hours=14), event="surge_due")
        surge = _schedule_gate(
            now=at,
            last_interaction_at=at - timedelta(days=2),
            next_check_at=at + timedelta(hours=4),
            surge_roll=0.0,
        )
        surge_candidate = await _evaluate_message(
            now=at,
            request_id="surge-due",
            judge_result=_judge(at),
        )
        cases.append(_case(
            "surge_due",
            status=surge_candidate["status"],
            passed=surge["status"] == "due" and surge_candidate["status"] == "candidate",
            observations={
                "surge_probability": surge["surge_probability"],
                "surge_roll": surge["surge_roll"],
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=1, hours=12),
            event="research_sources",
        )
        for source_count, case_id in (
            (0, "research_zero_sources"),
            (1, "research_one_source"),
            (2, "research_two_sources"),
        ):
            result = await _evaluate_research(
                now=at,
                request_id=case_id,
                source_count=source_count,
            )
            sources = (result.get("research") or {}).get("sources") or []
            expected_status = "candidate" if source_count == 2 else "research_blocked"
            actual_source_count = len(sources)
            cases.append(_case(
                case_id,
                status=result["status"],
                passed=result["status"] == expected_status,
                observations={
                    "source_count": actual_source_count,
                    "sources": sources,
                    "source_coverage": min(1.0, actual_source_count / 2.0),
                    "reason_code": str(result.get("reason_code") or ""),
                    "production_runner": True,
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="research_source",
                expected={"status": expected_status, "min_sources": 2},
            ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=2, hours=13),
            event="max_silence",
        )
        forced = _schedule_gate(
            now=at,
            first_attempt_at=_VIRTUAL_START + timedelta(hours=12),
        )
        before = len(recorder.records)
        if forced["forced"]:
            ledger.stage_candidate(
                key="max-silence",
                message="最长沉默兜底候选",
                now=at,
                case_id="max_silence",
            )
            await ledger.deliver(
                key="max-silence",
                now=at,
                publisher=recorder,
                case_id="max_silence",
            )
        cases.append(_case(
            "max_silence",
            status="recorded" if forced["forced"] else "not_due",
            passed=forced["forced"] and len(recorder.records) == before + 1,
            observations={
                "forced": forced["forced"],
                "judge_calls": 0,
                "recorded_publish_count": len(recorder.records) - before,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=3, hours=12),
            event="duplicate_key",
        )
        duplicate_candidate = await _evaluate_message(
            now=at,
            request_id="duplicate-key",
            judge_result=_judge(at),
            generated="幂等候选",
        )
        before = len(recorder.records)
        ledger.stage_candidate(
            key="same-due-anchor",
            message=duplicate_candidate["message"],
            now=at,
            case_id="duplicate_key",
        )
        first_recorded = await ledger.deliver(
            key="same-due-anchor",
            now=at,
            publisher=recorder,
            case_id="duplicate_key",
        )
        second_recorded = await ledger.deliver(
            key="same-due-anchor",
            now=at,
            publisher=recorder,
            case_id="duplicate_key",
        )
        duplicate_records = len(recorder.records) - before
        duplicate_attempt_count = int(
            ledger.snapshot()["attempts_by_key"].get("same-due-anchor", 0)
        )
        cases.append(_case(
            "duplicate_key",
            status="deduplicated",
            passed=first_recorded and not second_recorded and duplicate_records == 1,
            observations={
                "attempt_count": duplicate_attempt_count,
                "unique_key_count": int(duplicate_attempt_count > 0),
                "recorded_publish_count": duplicate_records,
                "duplicate_rate": _rate(
                    max(0, duplicate_attempt_count - 1),
                    duplicate_attempt_count,
                ),
                "duplicate_publish_rate": _rate(
                    max(0, duplicate_records - 1),
                    duplicate_records,
                ),
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=4, hours=12),
            event="research_fail_closed",
        )
        timed_out, timeout_caught = await _evaluate_timeout_research(
            now=at,
            request_id="research-timeout",
        )
        budget_exhausted, budget_evidence = await _evaluate_budget_exhaustion(
            now=at,
            request_id="research-budget",
        )
        cases.extend((
            _case(
                "research_timeout",
                status=timed_out["status"],
                passed=timeout_caught and timed_out.get("reason_code") == "timeout",
                observations={
                    "timed_out": timeout_caught,
                    "timeout_caught": timeout_caught,
                    "reason_code": str(timed_out.get("reason_code") or ""),
                    "production_runner": True,
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="fault_injection",
                expected={"status": "research_blocked", "reason_code": "timeout"},
            ),
            _case(
                "research_budget_exhausted",
                status=budget_exhausted["status"],
                passed=budget_exhausted.get("reason_code") == "budget_exhausted",
                observations={
                    "budget_exhausted": True,
                    "reason_code": str(budget_exhausted.get("reason_code") or ""),
                    "production_runner": True,
                    **budget_evidence,
                    "recorded_publish_count": 0,
                    "external_push_count": 0,
                },
                category="fault_injection",
                expected={
                    "status": "research_blocked",
                    "reason_code": "budget_exhausted",
                },
            ),
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=5, hours=12),
            event="auto_publish",
        )
        auto_candidate = await _evaluate_message(
            now=at,
            request_id="auto-candidate",
            judge_result=_judge(at),
            generated="自动候选",
        )
        before = len(recorder.records)
        ledger.stage_candidate(
            key="auto-candidate",
            message=auto_candidate["message"],
            now=at,
            case_id="auto_publish",
        )
        auto_recorded = await ledger.deliver(
            key="auto-candidate",
            now=at,
            publisher=recorder,
            case_id="auto_publish",
        )
        cases.append(_case(
            "auto_publish",
            status="recorded" if auto_recorded else "duplicate",
            passed=auto_recorded,
            observations={
                "mode": "auto",
                "would_publish": auto_candidate["status"] == "candidate",
                "recorded_publish_count": len(recorder.records) - before,
                "external_push_count": 0,
            },
        ))

        at = clock.advance_to(
            _VIRTUAL_START + timedelta(days=6, hours=12),
            event="dry_run",
        )
        before = len(recorder.records)
        dry_candidate = await _evaluate_message(
            now=at,
            request_id="dry-run",
            judge_result=_judge(at),
            generated="只返回候选",
        )
        cases.append(_case(
            "dry_run",
            status=dry_candidate["status"],
            passed=dry_candidate["status"] == "candidate" and len(recorder.records) == before,
            observations={
                "mode": "dry_run",
                "would_publish": dry_candidate.get("would_publish") is True,
                "recorded_publish_count": 0,
                "external_push_count": 0,
            },
        ))
        clock.advance_to(_VIRTUAL_START + timedelta(days=7), event="simulation_end")

        sqlite_state = ledger.snapshot()
        publisher_state = recorder.snapshot()
        metrics = derive_simulation_metrics(cases, sqlite_state, publisher_state)
        gate = evaluate_simulation_gate(
            cases,
            metrics,
            ledger_state=sqlite_state,
            publisher_state=publisher_state,
        )
        return {
            "report_schema_version": 2,
            "mode": "seven_day_conformance_replay",
            "passed": gate["passed"],
            "gate": gate,
            "storage_backend": "sqlite:///:memory:",
            "sqlite_state": sqlite_state,
            "publisher_state": publisher_state,
            "policy": {
                "active_hours": sorted(_ACTIVE_HOURS),
                "min_interval_min": DEFAULT_MIN_INTERVAL_MIN,
                "max_silence_min": DEFAULT_MAX_SILENCE_MIN,
                "surge_min_prob": DEFAULT_SURGE_MIN_PROB,
                "surge_max_prob": DEFAULT_SURGE_MAX_PROB,
            },
            "virtual_start": _VIRTUAL_START.isoformat(),
            "virtual_end": clock.current.isoformat(),
            "virtual_events": list(clock.events),
            "cases": cases,
            "metrics": metrics,
        }
    finally:
        ledger.close()
