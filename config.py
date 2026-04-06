"""
Nanobot 集中配置模块。
所有环境变量和常量在此统一管理。
"""
import os

# ── Dify 连接 ──
DIFY_BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost:5001/v1")
API_KEY_02 = os.environ.get("API_KEY_02_LOG", "")
API_KEY_03 = os.environ.get("API_KEY_03_PERSONA", "")
API_KEY_04 = os.environ.get("API_KEY_04_AUDIT", "")

# ── 进化参数 ──
EVOLUTION_THRESHOLD = int(os.environ.get("EVOLUTION_THRESHOLD", "20"))

# ── API 认证 ──
NANOBOT_API_TOKEN = os.environ.get("NANOBOT_API_TOKEN", "")

# ── 数据库 ──
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/nanobot.db")

# ── 日志 ──
LOG_DIR = os.environ.get("LOG_DIR", "./data")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ── Dify 重试 ──
DIFY_MAX_RETRIES = int(os.environ.get("DIFY_MAX_RETRIES", "3"))
DIFY_RETRY_BASE_DELAY = float(os.environ.get("DIFY_RETRY_BASE_DELAY", "2.0"))
DIFY_REQUEST_TIMEOUT = int(os.environ.get("DIFY_REQUEST_TIMEOUT", "180"))
