"""主动外呼评估租约的 SQLite schema 契约。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from core.schema_validation import SchemaMigrationValidationError


_COLUMNS = (
    ("user_id", "VARCHAR(255)", 1, None, 1),
    ("owner_token", "VARCHAR(64)", 1, None, 0),
    ("lease_expires_at", "DATETIME", 1, None, 0),
    ("created_at", "DATETIME", 1, "current_timestamp", 0),
    ("updated_at", "DATETIME", 1, "current_timestamp", 0),
)
_SQL_KEYWORDS = {
    "check",
    "collate",
    "current_date",
    "current_time",
    "current_timestamp",
}
_SQLToken = tuple[str, str]


def _tokenize_sql(value: str) -> tuple[_SQLToken, ...]:
    """切分 SQLite DDL，避免把 literal 或注释里的关键字误判为约束。"""

    tokens: list[_SQLToken] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char.isspace():
            index += 1
            continue
        if value.startswith("--", index):
            newline = value.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if value.startswith("/*", index):
            comment_end = value.find("*/", index + 2)
            if comment_end < 0:
                tokens.append(("invalid", value[index:]))
                break
            index = comment_end + 2
            continue
        if char == "'":
            start = index
            index += 1
            closed = False
            while index < length:
                if value[index] != "'":
                    index += 1
                    continue
                if index + 1 < length and value[index + 1] == "'":
                    index += 2
                    continue
                index += 1
                closed = True
                break
            if not closed:
                tokens.append(("invalid", value[start:]))
                break
            tokens.append(("literal", value[start:index]))
            continue
        if char in ('"', "`"):
            delimiter = char
            identifier: list[str] = []
            index += 1
            closed = False
            while index < length:
                if value[index] != delimiter:
                    identifier.append(value[index])
                    index += 1
                    continue
                if index + 1 < length and value[index + 1] == delimiter:
                    identifier.append(delimiter)
                    index += 2
                    continue
                index += 1
                closed = True
                break
            if not closed:
                tokens.append(("invalid", "".join(identifier)))
                break
            tokens.append(("identifier", "".join(identifier).casefold()))
            continue
        if char == "[":
            closing = value.find("]", index + 1)
            if closing < 0:
                tokens.append(("invalid", value[index:]))
                break
            tokens.append(("identifier", value[index + 1 : closing].casefold()))
            index = closing + 1
            continue
        if char.isalpha() or char == "_" or ord(char) >= 128:
            start = index
            index += 1
            while index < length:
                current = value[index]
                if not (
                    current.isalnum()
                    or current in "_$"
                    or ord(current) >= 128
                ):
                    break
                index += 1
            word = value[start:index].casefold()
            kind = "keyword" if word in _SQL_KEYWORDS else "identifier"
            tokens.append((kind, word))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (
                value[index].isalnum() or value[index] in "._"
            ):
                index += 1
            tokens.append(("number", value[start:index].casefold()))
            continue
        tokens.append(("symbol", char))
        index += 1
    return tuple(tokens)


def _has_wrapping_parentheses(tokens: tuple[_SQLToken, ...]) -> bool:
    if (
        len(tokens) < 2
        or tokens[0] != ("symbol", "(")
        or tokens[-1] != ("symbol", ")")
    ):
        return False
    depth = 0
    for index, token in enumerate(tokens):
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth == 0 and index != len(tokens) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _normalize_default(value: Any) -> tuple[_SQLToken, ...] | None:
    if value is None:
        return None
    tokens = _tokenize_sql(str(value))
    while _has_wrapping_parentheses(tokens):
        tokens = tokens[1:-1]
    return tokens


def _validate_table(conn: Any) -> None:
    rows = conn.execute(
        text("PRAGMA table_xinfo(proactive_outreach_leases)")
    ).mappings().all()
    hidden_columns = [
        (str(row["name"]), int(row["hidden"]))
        for row in rows
        if int(row["hidden"]) != 0
    ]
    if hidden_columns:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 hidden 隐藏或生成列: "
            f"{hidden_columns!r}"
        )

    actual_names = tuple(str(row["name"]) for row in rows)
    expected_names = tuple(item[0] for item in _COLUMNS)
    if tuple(name.casefold() for name in actual_names) != tuple(
        name.casefold() for name in expected_names
    ):
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 列集合或顺序不符合契约: "
            f"expected={expected_names!r} actual={actual_names!r}"
        )

    for row, expected in zip(rows, _COLUMNS, strict=True):
        name, column_type, not_null, default, primary_key = expected
        actual = (
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalize_default(row["dflt_value"]),
            int(row["pk"]),
        )
        required = (
            column_type,
            not_null,
            _normalize_default(default),
            primary_key,
        )
        if actual != required:
            raise SchemaMigrationValidationError(
                f"proactive_outreach_leases.{name} 定义不符合契约: "
                f"expected={required!r} actual={actual!r}"
            )

    table_sql = conn.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND lower(name) = 'proactive_outreach_leases'"
    )).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 缺少建表 DDL"
        )
    table_tokens = _tokenize_sql(table_sql)
    if any(kind == "invalid" for kind, _value in table_tokens):
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 建表 DDL 包含未闭合的注释或引号"
        )
    if ("keyword", "collate") in table_tokens:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 COLLATE 声明"
        )
    if ("keyword", "check") in table_tokens:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 CHECK 约束"
        )

    triggers = conn.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'trigger' AND lower(tbl_name) = 'proactive_outreach_leases'"
    )).scalars().all()
    if triggers:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 trigger 触发器: "
            f"{sorted(str(name) for name in triggers)!r}"
        )
    foreign_keys = conn.execute(
        text("PRAGMA foreign_key_list(proactive_outreach_leases)")
    ).mappings().all()
    if foreign_keys:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 FOREIGN KEY 外键约束"
        )


def _validate_indexes(conn: Any) -> None:
    rows = conn.execute(
        text("PRAGMA index_list(proactive_outreach_leases)")
    ).mappings().all()
    expected_name = "ix_proactive_outreach_lease_expires_at"
    unexpected = sorted(
        str(row["name"])
        for row in rows
        if str(row["origin"]) != "pk"
        and str(row["name"]).casefold() != expected_name.casefold()
    )
    if unexpected:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许额外索引: "
            f"{unexpected!r}"
        )

    entry = next(
        (
            row
            for row in rows
            if str(row["name"]).casefold() == expected_name.casefold()
        ),
        None,
    )
    if entry is None:
        raise SchemaMigrationValidationError(
            f"proactive_outreach_leases 缺少索引 {expected_name}"
        )
    escaped_name = str(entry["name"]).replace("'", "''")
    xinfo_rows = conn.execute(
        text(f"PRAGMA index_xinfo('{escaped_name}')")
    ).mappings().all()
    key_rows = sorted(
        (item for item in xinfo_rows if int(item["key"]) == 1),
        key=lambda item: int(item["seqno"]),
    )
    auxiliary_rows = [item for item in xinfo_rows if int(item["key"]) == 0]
    actual_columns = tuple(
        None if item["name"] is None else str(item["name"]).casefold()
        for item in key_rows
    )
    key_semantics_valid = all(
        item["name"] is not None
        and int(item["cid"]) >= 0
        and str(item["coll"]).upper() == "BINARY"
        and int(item["desc"]) == 0
        for item in key_rows
    )
    auxiliary_rows_valid = (
        len(auxiliary_rows) == 1
        and int(auxiliary_rows[0]["cid"]) == -1
        and auxiliary_rows[0]["name"] is None
        and str(auxiliary_rows[0]["coll"]).upper() == "BINARY"
        and int(auxiliary_rows[0]["desc"]) == 0
    )
    if (
        int(entry["unique"]) != 0
        or str(entry["origin"]) != "c"
        or int(entry["partial"]) != 0
        or actual_columns != ("lease_expires_at",)
        or not key_semantics_valid
        or not auxiliary_rows_valid
    ):
        raise SchemaMigrationValidationError(
            f"proactive_outreach_leases 索引 {expected_name} 不符合契约: "
            f"unique={int(entry['unique'])} "
            f"origin={str(entry['origin'])!r} "
            f"partial={int(entry['partial'])} "
            f"columns={actual_columns!r}"
        )


def proactive_outreach_leases_table(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建主动外呼租约表，并拒绝已有的约束或索引漂移。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS proactive_outreach_leases ("
        "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "owner_token VARCHAR(64) NOT NULL, "
        "lease_expires_at DATETIME NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))
    _validate_table(conn)
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_proactive_outreach_lease_expires_at "
        "ON proactive_outreach_leases(lease_expires_at)"
    ))
    _validate_indexes(conn)
