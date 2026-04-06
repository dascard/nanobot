import pytest
from unittest.mock import patch
from database import ChatLog, Persona, SystemPrompt
from evolution import evolution_task

@patch("evolution.call_dify_workflow")
def test_evolution_task_not_triggered(mock_dify, db_session):
    """测试日志没有达到阈值时不触发进化"""
    import config
    # 塞入很少的几条记录
    for i in range(5):
        log = ChatLog(user_id="evo_user", role="user", content=f"msg {i}", processed=0)
        db_session.add(log)
    db_session.commit()
    
    with patch("evolution.SessionLocal", return_value=db_session):
        evolution_task("evo_user")
        
    mock_dify.assert_not_called()

@patch("evolution.call_dify_workflow")
def test_evolution_task_triggered(mock_dify, db_session):
    """测试日志达到 20 条阈值后，走通 Dify 回写逻辑"""
    # 模拟 Dify 3 个阶段的返回
    def side_effect(api_key, inputs):
        if "raw_dialog_logs" in inputs:
            return {"structured_json": '{"summary": "Test Summary"}'}
        if "new_log_summary" in inputs:
            return {"final_persona_json": '{"likes": "movies"}'}
        if "audit_mode" in inputs:
            return {"final_system_prompt": 'You love movies.'}
        return {}
        
    mock_dify.side_effect = side_effect
    
    import config
    limit = config.EVOLUTION_THRESHOLD
    for i in range(limit):
        log = ChatLog(user_id="evo_user_2", role="user", content=f"hit {i}", processed=0)
        db_session.add(log)
    db_session.commit()
    
    with patch("evolution.SessionLocal", return_value=db_session):
        evolution_task("evo_user_2")
        
    assert mock_dify.call_count == 3
    
    # 验证日志状态变成了已处理 (processed = 1)
    logs = db_session.query(ChatLog).filter_by(user_id="evo_user_2").all()
    assert all(log.processed == 1 for log in logs)
    
    # 验证画像写入数据库
    persona = db_session.query(Persona).filter_by(user_id="evo_user_2").first()
    assert persona is not None
    assert persona.persona_json == '{"likes": "movies"}'
    
    sys_prompt = db_session.query(SystemPrompt).filter_by(user_id="evo_user_2").first()
    assert sys_prompt is not None
    assert sys_prompt.prompt_text == 'You love movies.'
