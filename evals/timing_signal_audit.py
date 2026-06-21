"""TimingGate 真实日志信号审计 CLI。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from core.eval_sampling.timing_signal_audit import (
    SIGNAL_NAMES,
    build_timing_signal_audit_report,
    extract_timing_signal_samples,
    merge_timing_signal_labels,
)


DEFAULT_REPORT = Path("evals/reports/timing_signal_audit_latest.json")


def _write_report(payload: dict[str, Any], output_path: str | Path) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_report_samples_and_source(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = _read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("input report must be a JSON object")
    samples = data.get("samples")
    if not isinstance(samples, list):
        raise ValueError("input report must contain a samples list")
    source = data.get("source")
    return (
        [dict(item) for item in samples if isinstance(item, dict)],
        dict(source) if isinstance(source, dict) else {},
    )


def _load_report_samples(path: str | Path) -> list[dict[str, Any]]:
    samples, _source = _load_report_samples_and_source(path)
    return samples


def _load_label_items(path: str | Path) -> list[dict[str, Any]]:
    label_path = Path(path)
    if label_path.suffix.lower() == ".jsonl":
        labels: list[dict[str, Any]] = []
        for line in label_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                labels.append(item)
        return labels

    data = _read_json_file(label_path)
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("labels"), list):
        return [dict(item) for item in data["labels"] if isinstance(item, dict)]
    raise ValueError("labels must be a JSON array, a JSON object with labels, or JSONL")


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
    run_id: str = "",
) -> dict:
    rows = query_timing_rows(db, after_id=after_id, limit=limit)
    samples = extract_timing_signal_samples(rows, signal_names=signal_names)[:limit]
    report = build_timing_signal_audit_report(samples)
    payload = {
        **report,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "db": db_path,
            "after_id": after_id,
            "limit": limit,
            "signals": list(signal_names),
            "run_id": str(run_id or ""),
        },
    }
    _write_report(payload, output_path)
    return payload


def run_labeled_audit(
    *,
    input_report_path: str | Path,
    output_path: str | Path = DEFAULT_REPORT,
    labels_path: str | Path | None = None,
    limit: int = 200,
    signal_names: tuple[str, ...] = SIGNAL_NAMES,
    run_id: str = "",
) -> dict:
    samples, input_source = _load_report_samples_and_source(input_report_path)
    if labels_path is not None:
        samples = merge_timing_signal_labels(samples, _load_label_items(labels_path))

    wanted = set(signal_names)
    filtered = [
        sample for sample in samples
        if not wanted or str(sample.get("signal_name") or "") in wanted
    ][:limit]
    report = build_timing_signal_audit_report(filtered)
    effective_run_id = str(run_id or input_source.get("run_id") or "")
    payload = {
        **report,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "mode": "input_report",
            "input_report": str(input_report_path),
            "labels": str(labels_path) if labels_path is not None else "",
            "limit": limit,
            "signals": list(signal_names),
            "run_id": effective_run_id,
        },
    }
    _write_report(payload, output_path)
    return payload


def _open_db(db_path: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    return sessionmaker(bind=engine)()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 ChatLog 审计 TimingGate scoring 信号假阳率")
    parser.add_argument("--db", default="data/nanobot.db")
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--signals", default=",".join(SIGNAL_NAMES))
    parser.add_argument("--input-report", default=None,
                        help="从已有 audit report 的 samples 离线复跑")
    parser.add_argument("--labels", default=None,
                        help="JSON/JSONL sidecar labels，按 log_id + signal_name 合并")
    parser.add_argument("--run-id", default=os.environ.get("TIMING_SIGNAL_AUDIT_RUN_ID", ""),
                        help="本次 eval run 标识，用于写入 report.source.run_id")
    args = parser.parse_args(argv)

    signal_names = tuple(name.strip() for name in args.signals.split(",") if name.strip())
    if args.input_report:
        report = run_labeled_audit(
            input_report_path=args.input_report,
            output_path=args.out,
            labels_path=args.labels,
            limit=args.limit,
            signal_names=signal_names or SIGNAL_NAMES,
            run_id=args.run_id,
        )
        print(
            "Timing signal audit: "
            f"samples={report['total_samples']} "
            f"mismatch={report['shadow']['action_mismatch_count']} "
            f"out={args.out}"
        )
        return 0

    db = _open_db(args.db)
    try:
        report = run_audit(
            db,
            output_path=args.out,
            after_id=args.after_id,
            limit=args.limit,
            signal_names=signal_names or SIGNAL_NAMES,
            db_path=args.db,
            run_id=args.run_id,
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
