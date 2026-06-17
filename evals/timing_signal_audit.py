"""TimingGate 真实日志信号审计 CLI。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from core.eval_sampling.timing_signal_audit import (
    SIGNAL_NAMES,
    build_timing_signal_audit_report,
    extract_timing_signal_samples,
)


DEFAULT_REPORT = Path("evals/reports/timing_signal_audit_latest.json")


def query_timing_rows(db, *, after_id: int = 0, limit: int = 200):
    from core.database import ChatLog

    return (
        db.query(ChatLog)
        .filter(ChatLog.role == "ambient", ChatLog.id > after_id)
        .order_by(ChatLog.id.asc())
        .limit(limit * 5)
        .all()
    )


def run_audit(
    db,
    *,
    output_path: str | Path = DEFAULT_REPORT,
    after_id: int = 0,
    limit: int = 200,
    signal_names: tuple[str, ...] = SIGNAL_NAMES,
    db_path: str = "",
) -> dict:
    rows = query_timing_rows(db, after_id=after_id, limit=limit)
    samples = extract_timing_signal_samples(rows, signal_names=signal_names)[:limit]
    report = build_timing_signal_audit_report(samples)
    payload = {
        **report,
        "samples": samples,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "db": db_path,
            "after_id": after_id,
            "limit": limit,
            "signals": list(signal_names),
        },
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _open_db(db_path: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    return sessionmaker(bind=engine)()


def main() -> int:
    parser = argparse.ArgumentParser(description="从 ChatLog 审计 TimingGate scoring 信号假阳率")
    parser.add_argument("--db", default="data/nanobot.db")
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--signals", default=",".join(SIGNAL_NAMES))
    args = parser.parse_args()

    signal_names = tuple(name.strip() for name in args.signals.split(",") if name.strip())
    db = _open_db(args.db)
    try:
        report = run_audit(
            db,
            output_path=args.out,
            after_id=args.after_id,
            limit=args.limit,
            signal_names=signal_names or SIGNAL_NAMES,
            db_path=args.db,
        )
    finally:
        db.close()

    print(
        "Timing signal audit: "
        f"samples={report['total_samples']} "
        f"mismatch={report['shadow']['action_mismatch_count']} "
        f"out={args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
