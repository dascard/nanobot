"""聊天投递 outbox 的严格 SQLite schema 契约。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from core.schema_validation import SchemaMigrationValidationError


TABLE_NAME = "chat_delivery_outbox"
_COLUMNS = (
    ("id", "INTEGER", 1, None, 1),
    ("delivery_key", "VARCHAR(64)", 1, None, 0),
    ("platform", "VARCHAR(32)", 1, None, 0),
    ("chat_type", "VARCHAR(16)", 1, None, 0),
    ("session_id", "VARCHAR(255)", 1, None, 0),
    ("message_id", "VARCHAR(255)", 1, None, 0),
    ("target_type", "VARCHAR(16)", 1, None, 0),
    ("target_id", "VARCHAR(255)", 1, None, 0),
    ("envelope_json", "TEXT", 1, "'{}'", 0),
    ("status", "VARCHAR(16)", 1, "'pending'", 0),
    ("owner_token", "VARCHAR(64)", 1, "''", 0),
    ("lease_expires_at", "DATETIME", 0, None, 0),
    ("attempt_count", "INTEGER", 1, "0", 0),
    ("next_attempt_at", "DATETIME", 0, None, 0),
    ("last_error", "TEXT", 1, "''", 0),
    ("created_at", "DATETIME", 1, "current_timestamp", 0),
    ("updated_at", "DATETIME", 1, "current_timestamp", 0),
    ("delivered_at", "DATETIME", 0, None, 0),
)
_CHECKS = (
    "CONSTRAINT ck_chat_delivery_outbox_status "
    "CHECK (status IN ('pending', 'sending', 'ambiguous', 'delivered', 'failed'))",
    "CONSTRAINT ck_chat_delivery_outbox_attempt_count "
    "CHECK (attempt_count >= 0)",
)
_INDEXES = {
    "uq_chat_delivery_outbox_delivery_key": (1, ("delivery_key",)),
    "uq_chat_delivery_outbox_claim_identity": (
        1,
        ("platform", "chat_type", "session_id", "message_id"),
    ),
    "ix_chat_delivery_outbox_due": (0, ("status", "next_attempt_at")),
    "ix_chat_delivery_outbox_status_lease": (
        0,
        ("status", "lease_expires_at"),
    ),
}
_SQL_KEYWORDS = {
    "autoincrement",
    "check",
    "collate",
    "constraint",
    "current_date",
    "current_time",
    "current_timestamp",
    "in",
    "integer",
    "key",
    "not",
    "null",
    "primary",
}
_SQLToken = tuple[str, str]


def _tokenize_sql(value: str) -> tuple[_SQLToken, ...]:
    tokens: list[_SQLToken] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char.isspace():
            index += 1
            continue
        if value.startswith("--", index):
            newline = value.find("\n", index + 2)
            index = len(value) if newline < 0 else newline + 1
            continue
        if value.startswith("/*", index):
            end = value.find("*/", index + 2)
            if end < 0:
                tokens.append(("invalid", value[index:]))
                break
            index = end + 2
            continue
        if char == "'":
            start = index
            index += 1
            while index < len(value):
                if value[index] != "'":
                    index += 1
                    continue
                if index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                index += 1
                tokens.append(("literal", value[start:index]))
                break
            else:
                tokens.append(("invalid", value[start:]))
            continue
        if char in ('"', "`"):
            delimiter = char
            identifier: list[str] = []
            index += 1
            while index < len(value):
                if value[index] != delimiter:
                    identifier.append(value[index])
                    index += 1
                    continue
                if index + 1 < len(value) and value[index + 1] == delimiter:
                    identifier.append(delimiter)
                    index += 2
                    continue
                index += 1
                tokens.append(("identifier", "".join(identifier).casefold()))
                break
            else:
                tokens.append(("invalid", "".join(identifier)))
            continue
        if char == "[":
            end = value.find("]", index + 1)
            if end < 0:
                tokens.append(("invalid", value[index:]))
                break
            tokens.append(("identifier", value[index + 1:end].casefold()))
            index = end + 1
            continue
        if char.isalpha() or char == "_" or ord(char) >= 128:
            start = index
            index += 1
            while index < len(value):
                current = value[index]
                if not (
                    current.isalnum()
                    or current in "_$"
                    or ord(current) >= 128
                ):
                    break
                index += 1
            word = value[start:index].casefold()
            tokens.append((
                "keyword" if word in _SQL_KEYWORDS else "identifier",
                word,
            ))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < len(value) and (
                value[index].isalnum() or value[index] in "._"
            ):
                index += 1
            tokens.append(("number", value[start:index].casefold()))
            continue
        tokens.append(("symbol", char))
        index += 1
    return tuple(tokens)


def _has_wrapping_parentheses(tokens: tuple[_SQLToken, ...]) -> bool:
    if len(tokens) < 2 or tokens[0] != ("symbol", "(") or tokens[-1] != ("symbol", ")"):
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


def _count_sequence(
    tokens: tuple[_SQLToken, ...],
    expected: tuple[_SQLToken, ...],
) -> int:
    if not expected or len(expected) > len(tokens):
        return 0
    return sum(
        tokens[index:index + len(expected)] == expected
        for index in range(len(tokens) - len(expected) + 1)
    )


def _validate_table(conn: Any) -> None:
    rows = conn.execute(
        text(f"PRAGMA table_xinfo({TABLE_NAME})")
    ).mappings().all()
    hidden = [
        (str(row["name"]), int(row["hidden"]))
        for row in rows
        if int(row["hidden"]) != 0
    ]
    if hidden:
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} 不允许 hidden 隐藏或生成列: {hidden!r}"
        )
    actual_names = tuple(str(row["name"]) for row in rows)
    expected_names = tuple(item[0] for item in _COLUMNS)
    if tuple(name.casefold() for name in actual_names) != expected_names:
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} 列集合或顺序不符合契约: "
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
                f"{TABLE_NAME}.{name} 定义不符合契约: "
                f"expected={required!r} actual={actual!r}"
            )

    table_sql = conn.execute(text(
        "SELECT sql FROM sqlite_master "
        f"WHERE type = 'table' AND lower(name) = '{TABLE_NAME}'"
    )).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError(f"{TABLE_NAME} 缺少建表 DDL")
    tokens = _tokenize_sql(table_sql)
    if any(kind == "invalid" for kind, _value in tokens):
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} 建表 DDL 包含未闭合的注释或引号"
        )
    if ("keyword", "collate") in tokens:
        raise SchemaMigrationValidationError(f"{TABLE_NAME} 不允许 COLLATE 声明")
    autoincrement = _tokenize_sql(
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT"
    )
    if _count_sequence(tokens, autoincrement) != 1:
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME}.id 必须显式使用 INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT"
        )
    for check in _CHECKS:
        if _count_sequence(tokens, _tokenize_sql(check)) != 1:
            raise SchemaMigrationValidationError(
                f"{TABLE_NAME} 缺少或错误的具名 CHECK: {check}"
            )
    check_count = sum(
        tokens[index] == ("keyword", "check")
        and tokens[index + 1] == ("symbol", "(")
        for index in range(len(tokens) - 1)
    )
    if check_count != len(_CHECKS):
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} CHECK 数量不符合契约: {check_count}"
        )
    triggers = conn.execute(text(
        "SELECT name FROM sqlite_master "
        f"WHERE type = 'trigger' AND lower(tbl_name) = '{TABLE_NAME}'"
    )).scalars().all()
    if triggers:
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} 不允许 trigger: {sorted(map(str, triggers))!r}"
        )
    if conn.execute(text(f"PRAGMA foreign_key_list({TABLE_NAME})")).fetchall():
        raise SchemaMigrationValidationError(f"{TABLE_NAME} 不允许 FOREIGN KEY")


def _validate_indexes(conn: Any) -> None:
    rows = conn.execute(
        text(f"PRAGMA index_list({TABLE_NAME})")
    ).mappings().all()
    by_name = {str(row["name"]).casefold(): row for row in rows}
    if len(by_name) != len(rows):
        raise SchemaMigrationValidationError(f"{TABLE_NAME} 存在大小写等价重复索引")
    expected_names = {name.casefold() for name in _INDEXES}
    actual_names = {
        str(row["name"]).casefold()
        for row in rows
        if str(row["origin"]) != "pk"
    }
    if actual_names != expected_names:
        raise SchemaMigrationValidationError(
            f"{TABLE_NAME} 索引集合不符合契约: "
            f"expected={sorted(expected_names)!r} actual={sorted(actual_names)!r}"
        )
    for index_name, (unique, columns) in _INDEXES.items():
        row = by_name[index_name.casefold()]
        escaped = str(row["name"]).replace("'", "''")
        xinfo = conn.execute(
            text(f"PRAGMA index_xinfo('{escaped}')")
        ).mappings().all()
        keys = sorted(
            (item for item in xinfo if int(item["key"]) == 1),
            key=lambda item: int(item["seqno"]),
        )
        auxiliary = [item for item in xinfo if int(item["key"]) == 0]
        actual_columns = tuple(
            None if item["name"] is None else str(item["name"]).casefold()
            for item in keys
        )
        key_valid = all(
            item["name"] is not None
            and int(item["cid"]) >= 0
            and str(item["coll"]).upper() == "BINARY"
            and int(item["desc"]) == 0
            for item in keys
        )
        auxiliary_valid = (
            len(auxiliary) == 1
            and int(auxiliary[0]["cid"]) == -1
            and auxiliary[0]["name"] is None
            and str(auxiliary[0]["coll"]).upper() == "BINARY"
            and int(auxiliary[0]["desc"]) == 0
        )
        if (
            int(row["unique"]) != unique
            or str(row["origin"]) != "c"
            or int(row["partial"]) != 0
            or actual_columns != tuple(column.casefold() for column in columns)
            or not key_valid
            or not auxiliary_valid
        ):
            raise SchemaMigrationValidationError(
                f"{TABLE_NAME} 索引 {index_name} 不符合契约"
            )


def chat_delivery_outbox_table(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建 outbox，并拒绝已有表或索引的契约漂移。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS chat_delivery_outbox ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "delivery_key VARCHAR(64) NOT NULL, "
        "platform VARCHAR(32) NOT NULL, "
        "chat_type VARCHAR(16) NOT NULL, "
        "session_id VARCHAR(255) NOT NULL, "
        "message_id VARCHAR(255) NOT NULL, "
        "target_type VARCHAR(16) NOT NULL, "
        "target_id VARCHAR(255) NOT NULL, "
        "envelope_json TEXT NOT NULL DEFAULT '{}', "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "owner_token VARCHAR(64) NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "next_attempt_at DATETIME, "
        "last_error TEXT NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "delivered_at DATETIME, "
        + _CHECKS[0]
        + ", "
        + _CHECKS[1]
        + ")"
    ))
    _validate_table(conn)
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_delivery_outbox_delivery_key "
        "ON chat_delivery_outbox(delivery_key)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_delivery_outbox_claim_identity "
        "ON chat_delivery_outbox(platform, chat_type, session_id, message_id)",
        "CREATE INDEX IF NOT EXISTS ix_chat_delivery_outbox_due "
        "ON chat_delivery_outbox(status, next_attempt_at)",
        "CREATE INDEX IF NOT EXISTS ix_chat_delivery_outbox_status_lease "
        "ON chat_delivery_outbox(status, lease_expires_at)",
    )
    for statement in statements:
        conn.execute(text(statement))
    _validate_indexes(conn)
