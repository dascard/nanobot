"""Eval candidates 离线导出、标注导入和晋升 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.eval_sampling.store import (
    candidate_trend_report,
    label_candidate,
    list_candidates,
    plan_candidate_batch_audit,
    preflight_candidate_promotions,
    promote_candidate,
)


def _jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def export_candidates(
    db,
    out_path: str | Path,
    *,
    suite: str = "",
    status: str = "candidate",
) -> int:
    items, _ = list_candidates(db, suite=suite, status=status, limit=10000, offset=0)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
    return len(items)


def import_labels(db, labels_path: str | Path) -> dict[str, int]:
    updated = 0
    for row in _jsonl_rows(labels_path):
        result = label_candidate(
            db,
            str(row["case_id"]),
            row.get("expected") or {},
            note=row.get("note"),
        )
        if result:
            updated += 1
    return {"updated": updated}


def promote_labeled(
    db,
    *,
    suite: str = "",
    target_dataset: str = "regression",
    apply: bool = False,
) -> dict[str, Any]:
    preflight = preflight_candidate_promotions(
        db,
        suite=suite,
        status="labeled",
        target_dataset=target_dataset,
        limit=10000,
    )
    items = []
    for item in preflight["items"]:
        readiness = item["readiness"]
        blocking_reasons = readiness["blocking_reasons"]
        first_reason = blocking_reasons[0]["code"] if blocking_reasons else ""
        items.append(
            {
                "case_id": item["case_id"],
                "ready": readiness["ready"],
                "path": item["path"],
                "target_dataset": item["target_dataset"],
                "error": first_reason,
                "readiness": readiness,
            }
        )

    result = {
        "ok": preflight["ok"],
        "count": preflight["total"],
        "ready": preflight["ready"],
        "blocked": preflight["blocked"],
        "applied": 0,
        "items": items,
    }
    if not apply:
        return result
    if preflight["blocked"]:
        result["ok"] = False
        return result

    applied_items = []
    for item in preflight["items"]:
        if not item["readiness"]["ready"]:
            continue
        path = promote_candidate(db, item["case_id"], target_dataset=target_dataset)
        applied_items.append({"case_id": item["case_id"], "path": path})
    result["ok"] = True
    result["applied"] = len(applied_items)
    result["items"] = applied_items
    return result


def audit_candidates(
    db,
    *,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    return plan_candidate_batch_audit(
        db,
        suite=suite,
        status=status,
        source=source,
        target_dataset=target_dataset,
        limit=limit,
    )


def trend_candidates(
    db,
    *,
    days: int = 30,
    suite: str = "",
    status: str = "",
    source: str = "",
    target_dataset: str = "",
) -> dict[str, Any]:
    return candidate_trend_report(
        db,
        days=days,
        suite=suite,
        status=status,
        source=source,
        target_dataset=target_dataset,
    )


def _open_db():
    from core.database import SessionLocal

    return SessionLocal()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    export_p = sub.add_parser("export")
    export_p.add_argument("--suite", default="")
    export_p.add_argument("--status", default="candidate")
    export_p.add_argument("--out", required=True)

    import_p = sub.add_parser("import-labels")
    import_p.add_argument("--labels", required=True)

    promote_p = sub.add_parser("promote")
    promote_p.add_argument("--suite", default="")
    promote_p.add_argument("--target-dataset", default="regression")
    promote_p.add_argument("--apply", action="store_true")
    promote_p.add_argument("--dry-run", action="store_true")

    audit_p = sub.add_parser("audit")
    audit_p.add_argument("--suite", default="")
    audit_p.add_argument("--status", default="")
    audit_p.add_argument("--source", default="")
    audit_p.add_argument("--target-dataset", default="")
    audit_p.add_argument("--limit", type=int, default=200)
    audit_p.add_argument("--out", default="")

    trend_p = sub.add_parser("trend")
    trend_p.add_argument("--days", type=int, default=30)
    trend_p.add_argument("--suite", default="")
    trend_p.add_argument("--status", default="")
    trend_p.add_argument("--source", default="")
    trend_p.add_argument("--target-dataset", default="")
    trend_p.add_argument("--out", default="")

    args = parser.parse_args(argv)
    db = _open_db()
    try:
        if args.command == "export":
            count = export_candidates(db, args.out, suite=args.suite, status=args.status)
            print(f"exported={count} out={args.out}")
            return 0
        if args.command == "import-labels":
            result = import_labels(db, args.labels)
            print(f"updated={result['updated']}")
            return 0
        if args.command == "promote":
            result = promote_labeled(
                db,
                suite=args.suite,
                target_dataset=args.target_dataset,
                apply=bool(args.apply and not args.dry_run),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "audit":
            result = audit_candidates(
                db,
                suite=args.suite,
                status=args.status,
                source=args.source,
                target_dataset=args.target_dataset,
                limit=args.limit,
            )
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text + "\n", encoding="utf-8")
            else:
                print(text)
            return 0
        if args.command == "trend":
            result = trend_candidates(
                db,
                days=args.days,
                suite=args.suite,
                status=args.status,
                source=args.source,
                target_dataset=args.target_dataset,
            )
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text + "\n", encoding="utf-8")
            else:
                print(text)
            readiness = result["summary"]["readiness"]
            print(
                "trend: "
                f"total={result['summary']['total']} "
                f"ready={readiness.get('ready', 0)} "
                f"blocked={readiness.get('blocked', 0)}"
            )
            return 0
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
