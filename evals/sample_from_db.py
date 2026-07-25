"""从运行 DB 抽样生成候选 eval case——不修改数据，不自动标 expected。

用法:
    python -m evals.sample_from_db --db data/nanobot.db --out evals/cases/candidates --limit 100
    python -m evals.sample_from_db --db data/nanobot.db --suite group_reply --limit 30
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta


def _open_db(db_path: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    return Session()


def _safe_json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


# ── Suite samplers ──

def _sample_group_reply(db, limit: int) -> list[dict]:
    """从 ChatLog 抽 bot 回复 + 前后 ambient 上下文。"""
    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.role == "assistant")
        .order_by(ChatLog.id.desc())
        .limit(limit * 3)
        .all()
    )

    cases: list[dict] = []
    group_counts: dict[str, int] = {}
    per_group = max(1, min(limit // 3, 20))  # 每群最多取 N 条，默认取 limit/3
    for r in rows:
        meta = _safe_json(r.meta_json)
        if meta.get("kind") != "group_reply":
            continue
        group_id = str(r.session_id or "").removeprefix("group_")
        if not group_id:
            continue
        gc = group_counts.get(group_id, 0)
        if gc >= per_group:
            continue
        group_counts[group_id] = gc + 1
        if len(cases) >= limit:
            break

        # 按时间窗口取上下文：助理回复前 2 分钟到后 30 秒
        created = r.created_at
        context_rows = (
            db.query(ChatLog)
            .filter(
                ChatLog.session_id == r.session_id,
                ChatLog.created_at.between(
                    created - timedelta(minutes=2),
                    created + timedelta(seconds=30),
                ),
            )
            .order_by(ChatLog.created_at)
            .limit(20)
            .all()
        )
        context = [
            {"role": cr.role, "content": (cr.content or "")[:200], "sender_name": cr.sender_name or ""}
            for cr in context_rows
        ]

        cases.append({
            "id": f"candidate_group_reply_{r.id}",
            "suite": "group_reply",
            "description": f"群 {group_id} bot 回复 #{r.id}",
            "input": {
                "group_id": group_id,
                "bot_reply": (r.content or "")[:300],
                "reply_meta": meta.get("reply_meta"),
                "context": context,
            },
            "expected": {"needs_label": True},
            "tags": ["sampled", "group_reply"],
            "source": "db",
            "source_ref": f"chatlog:{r.id}",
        })
    return cases


def _sample_reply_contract(db, limit: int) -> list[dict]:
    """抽取 reply_meta 非空的 bot 回复。"""
    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.role == "assistant")
        .order_by(ChatLog.id.desc())
        .limit(limit * 5)
        .all()
    )

    cases: list[dict] = []
    for r in rows:
        meta = _safe_json(r.meta_json)
        reply_meta = meta.get("reply_meta")
        if not reply_meta:
            continue
        if len(cases) >= limit:
            break

        cases.append({
            "id": f"candidate_reply_contract_{r.id}",
            "suite": "reply_contract",
            "description": f"reply #{r.id} send_mode={reply_meta.get('send_mode', '')}",
            "input": {
                "group_id": str(r.session_id or "").removeprefix("group_"),
                "reply_meta": reply_meta,
                "content": (r.content or "")[:300],
            },
            "expected": {"needs_label": True},
            "tags": ["sampled", "reply_contract"],
            "source": "db",
            "source_ref": f"chatlog:{r.id}",
        })
    return cases


def _sample_timing_gate(db, limit: int) -> list[dict]:
    """从 ChatLog.meta_json.timing_gate 抽 TimingGate 判定样本。"""
    from core.database import ChatLog

    rows = (
        db.query(ChatLog)
        .filter(ChatLog.role == "ambient")
        .order_by(ChatLog.id.desc())
        .limit(limit * 5)
        .all()
    )

    cases: list[dict] = []
    for r in rows:
        meta = _safe_json(r.meta_json)
        tg = meta.get("timing_gate")
        if not tg:
            continue
        if len(cases) >= limit:
            break

        cases.append({
            "id": f"candidate_timing_gate_{r.id}",
            "suite": "timing_gate",
            "description": f"action={tg.get('action', '')} reason={tg.get('reason', '')}",
            "input": {
                "group_id": str(r.session_id or "").removeprefix("group_"),
                "action": tg.get("action", ""),
                "trigger_reason": tg.get("reason", ""),
                "generation": tg.get("generation", 0),
                "latency_ms": tg.get("latency_ms", 0),
                "pending_count": tg.get("pending_count", 0),
                "talk_value": tg.get("talk_value"),
                "message": (r.content or "")[:200],
            },
            "expected": {"needs_label": True},
            "tags": ["sampled", "timing_gate"],
            "source": "db",
            "source_ref": f"chatlog:{r.id}",
        })
    return cases


def _sample_memory_learning(db, limit: int) -> list[dict]:
    """复用在线 Eval Sampling，只读取新群学习候选事实源。"""

    from core.eval_sampling.db_sampler import sample_memory_learning

    cases: list[dict] = []
    for sampled in sample_memory_learning(
        db,
        candidate_type="all",
        limit=limit,
    ):
        case = dict(sampled)
        case["id"] = case.pop("case_id")
        cases.append(case)
    return cases


SUITE_SAMPLERS = {
    "group_reply": _sample_group_reply,
    "reply_contract": _sample_reply_contract,
    "timing_gate": _sample_timing_gate,
    "memory_learning": _sample_memory_learning,
}


def main():
    parser = argparse.ArgumentParser(description="从运行 DB 抽样候选 eval case")
    parser.add_argument("--db", default="data/nanobot.db")
    parser.add_argument("--out", default="evals/cases/candidates")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--suite", default="")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    db = _open_db(args.db)

    try:
        suites = [s.strip() for s in args.suite.split(",") if s.strip()] if args.suite else list(SUITE_SAMPLERS)
        total = 0
        for suite in suites:
            sampler = SUITE_SAMPLERS.get(suite)
            if sampler is None:
                print(f"Unknown suite: {suite}, skipping")
                continue
            cases = sampler(db, args.limit)
            for case in cases:
                fpath = os.path.join(args.out, f"{case['id']}.json")
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(case, f, indent=2, ensure_ascii=False)
                total += 1
            print(f"[{suite}] {len(cases)} candidates")
        print(f"Total: {total} → {args.out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
