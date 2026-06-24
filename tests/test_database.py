from core.database import User, Persona, ChatLog

def test_create_user(db_session):
    user = User(id="test_user_1")
    db_session.add(user)
    db_session.commit()
    
    fetched = db_session.query(User).filter_by(id="test_user_1").first()
    assert fetched is not None
    assert fetched.id == "test_user_1"

def test_create_persona(db_session):
    persona = Persona(user_id="user_2", persona_json='{"likes": "apple"}')
    db_session.add(persona)
    db_session.commit()
    
    fetched = db_session.query(Persona).filter_by(user_id="user_2").first()
    assert fetched is not None
    assert fetched.persona_json == '{"likes": "apple"}'

def test_chat_log_processing_flag(db_session):
    log = ChatLog(user_id="user_3", role="user", content="Hello, testing flag")
    db_session.add(log)
    db_session.commit()
    
    fetched = db_session.query(ChatLog).filter_by(user_id="user_3").first()
    assert fetched.processed == 0
    
    fetched.processed = 1
    db_session.commit()
    
    updated = db_session.query(ChatLog).filter_by(user_id="user_3").first()
    assert updated.processed == 1


def test_sqlite_connect_args_include_busy_timeout(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "45000")

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["check_same_thread"] is False
    assert args["timeout"] == 45.0


def test_sqlite_connect_args_default_busy_timeout_is_short(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT_MS", raising=False)

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["timeout"] == 1.0


def test_sqlite_connect_args_invalid_busy_timeout_falls_back_short(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "not-a-number")

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["timeout"] == 1.0
