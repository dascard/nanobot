from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from api import chat_recovery
from app.persona.injection_service import PersonaInjectionService, record_persona_injected
from core.inbound_idempotency import InboundClaimKey


ROOT = Path(__file__).parents[1]


class _PersonaRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.flush_count = 0

    def list_for_user(self, user_id: str, *, limit: int = 120):
        return tuple(row for row in self.rows if row.user_id == user_id)[:limit]

    def list_by_ids(self, ids):
        wanted = set(ids)
        return tuple(row for row in self.rows if row.id in wanted)

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _ChatRepository:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def find_chat_logs(self, *, session_id: str, message_id: str, role: str):
        return tuple(
            row
            for row in self.rows
            if row.session_id == session_id
            and row.message_id == message_id
            and row.role == role
        )

    def add_chat_log(self, **values):
        row = SimpleNamespace(id=len(self.rows) + 1, **values)
        self.rows.append(row)
        return row

    def add_conversation_turn(self, **values):
        return SimpleNamespace(**values)

    def add_sensitive_data(self, **values):
        return SimpleNamespace(**values)

    def get_chat_log(self, row_id: int):
        return next((row for row in self.rows if row.id == row_id), None)

    def count_pending_chat_logs(self, user_id: str) -> int:
        return sum(
            1
            for row in self.rows
            if row.user_id == user_id and row.processed == 0
        )

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _persona_fact() -> Any:
    now = datetime.now()
    return SimpleNamespace(
        id=1,
        user_id="u1",
        content="用户偏好先给结论，再给必要步骤",
        domain_primary="协作方式",
        confidence="确认",
        fact_type="preference",
        memory_type="stable_preference",
        status="active",
        inject_policy="auto",
        evidence_count=3,
        first_seen=now - timedelta(days=5),
        last_seen=now,
        injected_count=0,
        last_injected_at=None,
    )


def test_persona_service_accepts_repository_port_without_sqlalchemy_session() -> None:
    row = _persona_fact()
    repository = _PersonaRepository([row])

    result = PersonaInjectionService(repository).build_context(
        user_id="u1",
        current_user_input="请先给结论",
    )
    updated = record_persona_injected(repository, result.selected_ids)

    assert result.selected_ids == [1]
    assert updated == 1
    assert repository.flush_count == 1
    assert row.injected_count == 1


def test_chat_recovery_accepts_repository_port_without_orm_query() -> None:
    key = InboundClaimKey(
        platform="qq",
        chat_type="private",
        session_id="private_u1",
        message_id="message-1",
    )
    request_sha256 = "a" * 64
    meta = chat_recovery.attach_private_request_fingerprint(
        {"kind": chat_recovery.REQUEST_JOURNAL_KIND},
        request_sha256,
    )
    row = SimpleNamespace(
        id=1,
        user_id="u1",
        session_id=key.session_id,
        message_id=key.message_id,
        role="user",
        content="你好",
        processed=1,
        meta_json=json.dumps(meta, ensure_ascii=False),
    )

    loaded = chat_recovery.load_private_request_journal(
        _ChatRepository([row]),
        key=key,
        request_sha256=request_sha256,
    )

    assert loaded is not None
    assert loaded[0] is row


def test_database_port_contracts_do_not_import_sqlalchemy_or_orm_models() -> None:
    path = ROOT / "core" / "db" / "contracts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    modules.update(
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert "sqlalchemy" not in modules
    assert "core" not in modules


def test_database_facade_reexports_subdomain_owned_models() -> None:
    from core import database
    from core.db.base import Base
    from core.db.models.chat import ChatLog, ConversationTurn, SensitiveData, User
    from core.db.models.inbound import ChatDeliveryOutbox, InboundMessageClaim
    from core.db.models.outbound import (
        OutboundDeliveryAttempt,
        OutboundDeliveryCircuit,
        OutboundDeliveryControl,
        OutboundDeliveryOutbox,
        OutboundGenerationAttempt,
        OutboundRun,
    )
    from core.db.models.persona import (
        Persona,
        PersonaBehavior,
        PersonaFact,
        SystemPrompt,
    )
    from core.db.models.session_memory import (
        MemoryDigest,
        MemoryDigestJob,
        RollingSessionSummary,
        SessionSummaryJob,
    )
    from core.db.models.proactive import ProactiveOutreachLease, ProactiveOutreachLog
    from core.db.models.scheduling import ScheduledTask

    assert database.Base is Base
    assert database.ChatLog is ChatLog
    assert database.ConversationTurn is ConversationTurn
    assert database.SensitiveData is SensitiveData
    assert database.User is User
    assert database.ChatDeliveryOutbox is ChatDeliveryOutbox
    assert database.InboundMessageClaim is InboundMessageClaim
    assert database.Persona is Persona
    assert database.PersonaBehavior is PersonaBehavior
    assert database.PersonaFact is PersonaFact
    assert database.SystemPrompt is SystemPrompt
    assert database.MemoryDigest is MemoryDigest
    assert database.MemoryDigestJob is MemoryDigestJob
    assert database.RollingSessionSummary is RollingSessionSummary
    assert database.SessionSummaryJob is SessionSummaryJob
    assert database.ProactiveOutreachLease is ProactiveOutreachLease
    assert database.ProactiveOutreachLog is ProactiveOutreachLog
    assert database.OutboundDeliveryAttempt is OutboundDeliveryAttempt
    assert database.OutboundDeliveryCircuit is OutboundDeliveryCircuit
    assert database.OutboundDeliveryControl is OutboundDeliveryControl
    assert database.OutboundDeliveryOutbox is OutboundDeliveryOutbox
    assert database.OutboundGenerationAttempt is OutboundGenerationAttempt
    assert database.OutboundRun is OutboundRun
    assert database.ScheduledTask is ScheduledTask


def test_migrated_database_adapters_do_not_import_compatibility_facade() -> None:
    paths = (
        ROOT / "core" / "db" / "adapter.py",
        ROOT / "core" / "persona_preprocess.py",
        ROOT / "core" / "repositories" / "chat_logs.py",
        ROOT / "core" / "repositories" / "users.py",
        *sorted((ROOT / "app" / "memory_digest").glob("*.py")),
        *sorted((ROOT / "app" / "session_memory").glob("*.py")),
        *sorted((ROOT / "core" / "outbound").glob("*.py")),
        ROOT / "core" / "outbound_delivery.py",
        ROOT / "core" / "outbound_delivery_service.py",
        ROOT / "core" / "scheduled_task_outbound.py",
        *(
            path
            for path in sorted((ROOT / "core" / "proactive").glob("*.py"))
            if path.name
            not in {"delivery_runtime.py", "grounding.py", "runtime_support.py"}
        ),
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "core.database" not in source, path
