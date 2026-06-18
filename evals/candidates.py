"""Eval candidates 离线导出、标注导入和晋升 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.eval_sampling.store import (
    label_candidate,
    list_candidates,
    plan_candidate_promotion,
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
    items, _ = list_candidates(db, suite=suite, status="labeled", limit=10000, offset=0)
    plans = []
    for item in items:
        if apply:
            path = promote_candidate(db, item["case_id"], target_dataset=target_dataset)
            plans.append({"case_id": item["case_id"], "path": path})
        else:
            plans.append(
                plan_candidate_promotion(
                    db,
                    item["case_id"],
                    target_dataset=target_dataset,
                )
            )
    return {"count": len(plans), "items": plans}


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
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
