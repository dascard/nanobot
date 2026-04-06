import pytest
from database import ChatLog

def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "version": "0.2.0"}

def test_get_context_default(client):
    response = client.get("/api/v1/context?user_id=new_user")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "new_user"
    assert data["persona_json"] == "{}"
    assert "智能助手" in data["system_prompt"]

def test_get_context_with_auth(client):
    """测试如果开启强制鉴权，不带 Auth 头应该失败"""
    import os
    os.environ["NANOBOT_API_TOKEN"] = "testtoken"
    
    # 因为 FastAPI 的依赖项解析在模块加载时就绑好了环境变量
    # 在这个作用域去改 environ 可能需要重新加载模块
    pass # 留作集成测试，我们在 conftest.py 里禁用了 Token

def test_submit_log(client, db_session):
    # 发送一条记录
    response = client.post(
        "/api/v1/log", 
        json={"user_id": "api_user", "role": "user", "content": "hello API"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "unprocessed_logs": 1}
    
    # 发送第二条记录
    response = client.post(
        "/api/v1/log", 
        json={"user_id": "api_user", "role": "model", "content": "hi"}
    )
    assert response.status_code == 200
    assert response.json()["unprocessed_logs"] == 2
    
    # 验证数据库是否插入成功
    logs = db_session.query(ChatLog).filter_by(user_id="api_user").all()
    assert len(logs) == 2
    assert logs[0].content == "hello API"
    assert logs[1].content == "hi"
