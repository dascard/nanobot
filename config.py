"""
Nanobot 集中配置模块。
所有环境变量和常量在此统一管理。
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Dify 连接 ──
DIFY_BASE_URL = os.environ.get("DIFY_BASE_URL", "http://localhost:5001/v1")
API_KEY_01_CHAT = os.environ.get("API_KEY_01_CHAT", "")
API_KEY_02 = os.environ.get("API_KEY_02_LOG", "")
API_KEY_03 = os.environ.get("API_KEY_03_PERSONA", "")
API_KEY_04 = os.environ.get("API_KEY_04_AUDIT", "")

# ── Dify 知识库 ──
DATASET_API_KEY = os.environ.get("DATASET_API_KEY", "")
DATASET_ID_LOGS = os.environ.get("DATASET_ID_LOGS", "")
DATASET_ID_PERSONAS = os.environ.get("DATASET_ID_PERSONAS", "")

# ── 进化参数 ──
EVOLUTION_THRESHOLD = int(os.environ.get("EVOLUTION_THRESHOLD", "10"))

# ── API 认证 ──
NANOBOT_API_TOKEN = os.environ.get("NANOBOT_API_TOKEN", "")
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "0000000000")

# ── 数据库 ──
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/nanobot.db")

# ── 日志 ──
LOG_DIR = os.environ.get("LOG_DIR", "./data")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ── Dify 重试 ──
DIFY_MAX_RETRIES = int(os.environ.get("DIFY_MAX_RETRIES", "3"))
DIFY_RETRY_BASE_DELAY = float(os.environ.get("DIFY_RETRY_BASE_DELAY", "2.0"))
DIFY_REQUEST_TIMEOUT = int(os.environ.get("DIFY_REQUEST_TIMEOUT", "180"))

# ── 厂商 API (OpenAI 兼容) ──
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "new-api")  # new-api | deepseek | zhipu | qwen | openrouter | gemini | siliconflow | dify
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# ── New-API 网关 ──
NEW_API_KEY = os.environ.get("NEW_API_KEY", "")
NEW_API_BASE_URL = os.environ.get("NEW_API_BASE_URL", "https://api.new-api.com/v1")
NEW_API_TIMEOUT = int(os.environ.get("NEW_API_TIMEOUT", "180"))
NEW_API_AUTO_MODEL_SYNC = os.environ.get("NEW_API_AUTO_MODEL_SYNC", "1") == "1"
NEW_API_MODEL_SYNC_INTERVAL_MINUTES = int(os.environ.get("NEW_API_MODEL_SYNC_INTERVAL_MINUTES", "60"))

# 自动路由：off | code | model
AUTO_MODEL_ROUTING_MODE = os.environ.get("AUTO_MODEL_ROUTING_MODE", "code")

# 模型分级与预算控制
LLM_MODEL_SMART = os.environ.get("LLM_MODEL_SMART", "")
LLM_MODEL_FAST = os.environ.get("LLM_MODEL_FAST", "")
LLM_MODEL_REASONING = os.environ.get("LLM_MODEL_REASONING", "")
LLM_BUDGET_CAP = float(os.environ.get("LLM_BUDGET_CAP", "10.0")) # 每百万 Token 输入的最大允许成本

# ── Agent 行为 ──
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "5"))  # 单次对话最大工具轮数
NEW_API_MAX_RETRIES = int(os.environ.get("NEW_API_MAX_RETRIES", "3"))

# ── 每日记忆折叠 ──
DAILY_DIGEST_ENABLED = os.environ.get("DAILY_DIGEST_ENABLED", "1") == "1"
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", "4"))
