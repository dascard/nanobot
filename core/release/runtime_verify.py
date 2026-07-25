"""部署后在 Runtime 容器内核验数据库迁移 Head。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys
from typing import Any

from sqlalchemy import text

from core.schema_migrations import MIGRATIONS


class RuntimeSchemaVerificationError(RuntimeError):
    """Runtime 代码声明与数据库迁移状态不一致。"""


def current_schema_migration_head() -> str:
    if not MIGRATIONS:
        raise RuntimeSchemaVerificationError("迁移清单为空")
    return str(MIGRATIONS[-1][0])


def verify_schema_migrations(
    engine: Any,
    *,
    expected_head: str,
) -> None:
    """允许数据库含未来版本，但当前 Runtime 的全部迁移必须已应用。"""

    code_head = current_schema_migration_head()
    if expected_head != code_head:
        raise RuntimeSchemaVerificationError(
            "ReleaseManifest migration head 与 Runtime 代码不一致"
        )
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT version FROM schema_migrations")
            ).fetchall()
    except Exception as exc:
        raise RuntimeSchemaVerificationError(
            "无法读取 schema_migrations"
        ) from exc
    applied = {str(row[0]) for row in rows}
    expected = {str(version) for version, _name, _function in MIGRATIONS}
    missing = sorted(expected - applied)
    if missing:
        raise RuntimeSchemaVerificationError(
            "数据库缺少 Runtime 所需迁移: " + ", ".join(missing)
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="核验当前 Runtime 的数据库迁移 Head"
    )
    parser.add_argument("--expected-schema-head", required=True)
    args = parser.parse_args(argv)

    from core.database import engine

    try:
        verify_schema_migrations(
            engine,
            expected_head=args.expected_schema_head,
        )
    except RuntimeSchemaVerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "数据库迁移 Head 验证通过："
        + current_schema_migration_head()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
