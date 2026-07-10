import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_proactive_outreach_lease_migration_matches_orm_nullability_and_index():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.begin() as conn:
        columns = {
            str(row["name"]): row
            for row in conn.execute(
                text("PRAGMA table_xinfo(proactive_outreach_leases)")
            ).mappings()
        }
        assert tuple(columns) == (
            "user_id",
            "owner_token",
            "lease_expires_at",
            "created_at",
            "updated_at",
        )
        assert int(columns["user_id"]["notnull"]) == 1
        assert int(columns["user_id"]["pk"]) == 1
        index = conn.execute(text(
            "PRAGMA index_xinfo(ix_proactive_outreach_lease_expires_at)"
        )).mappings().all()
        assert [row["name"] for row in index if int(row["key"]) == 1] == [
            "lease_expires_at"
        ]
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO proactive_outreach_leases "
                "(user_id, owner_token, lease_expires_at) "
                "VALUES (NULL, 'owner', CURRENT_TIMESTAMP)"
            ))


def test_proactive_outreach_lease_migration_rejects_existing_schema_drift():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) PRIMARY KEY, "
            "owner_token INTEGER, "
            "lease_expires_at TEXT, "
            "created_at DATETIME, "
            "updated_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(owner_token)"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)

    with engine.connect() as conn:
        applied = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260710_proactive_outreach_leases'"
        )).scalar_one()
    assert applied == 0


@pytest.mark.parametrize(
    "extra_constraint",
    [
        ", CONSTRAINT ck_proactive_lease_owner CHECK (owner_token = 'only-owner')",
        ", UNIQUE(lease_expires_at)",
    ],
    ids=["extra-check", "extra-unique"],
)
def test_proactive_outreach_lease_migration_rejects_extra_write_constraints(
    extra_constraint,
):
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
            "owner_token VARCHAR(64) NOT NULL, "
            "lease_expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            f"{extra_constraint}"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(lease_expires_at)"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)


def test_proactive_outreach_lease_migration_rejects_extra_expression_index():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
            "owner_token VARCHAR(64) NOT NULL, "
            "lease_expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(lease_expires_at)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expression "
            "ON proactive_outreach_leases(json_extract(owner_token, '$'))"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)
