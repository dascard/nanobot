import pytest
from core.database import User, Persona, SystemPrompt, ChatLog

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
