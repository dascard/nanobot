"""
Nanobot 集中配置模块。
所有环境变量和常量在此统一管理。
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ── 进化参数 ──
EVOLUTION_THRESHOLD = int(os.environ.get("EVOLUTION_THRESHOLD", "10"))

# ── API 认证 ──
# NANOBOT_API_TOKEN：bot 推送 / chat / group/message 接口认证（为空则不启用）
NANOBOT_API_TOKEN = os.environ.get("NANOBOT_API_TOKEN", "")

# NANOBOT_ADMIN_TOKEN：WebUI /api/v1/admin/* 管理接口认证（自动生成，必填）
NANOBOT_ADMIN_TOKEN = os.environ.get("NANOBOT_ADMIN_TOKEN", "")
if not NANOBOT_ADMIN_TOKEN:
    import secrets
    NANOBOT_ADMIN_TOKEN = secrets.token_hex(16)
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(_env_path, "a") as _fh:
            _fh.write(f"\nNANOBOT_ADMIN_TOKEN={NANOBOT_ADMIN_TOKEN}\n")
    except Exception:
        pass
ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", "0000000000")


# ── 超级用户列表 ──
def _parse_id_set(raw: str) -> set[str]:
    return {
        x.strip()
        for x in str(raw or "").replace("，", ",").split(",")
        if x.strip()
    }

SUPER_USER_IDS = _parse_id_set(
    os.environ.get("NANOBOT_SUPER_USER_IDS")
    or os.environ.get("SUPER_USER_IDS")
    or os.environ.get("ADMIN_USER_ID", "")
)

# ── Bot 身份变量 ──
NANOBOT_CHARACTER_NAME = (
    os.environ.get("NANOBOT_CHARACTER_NAME")
    or os.environ.get("BOT_NAME")
    or "nanobot"
).strip()
NANOBOT_BOT_ALIASES = _parse_id_set(
    os.environ.get("NANOBOT_BOT_ALIASES")
    or os.environ.get("BOT_ALIASES")
    or NANOBOT_CHARACTER_NAME
)

# ── 数据库 ──
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/nanobot.db")

# ── 日志 ──
LOG_DIR = os.environ.get("LOG_DIR", "./data")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def parse_cors_origins(raw: str) -> list[str]:
    """解析 CORS origins 配置，支持 * 或逗号分隔 URL。"""
    value = str(raw or "").strip()
    if not value or value == "*":
        return ["*"]
    origins = [item.strip() for item in value.replace("，", ",").split(",")]
    return [origin for origin in origins if origin] or ["*"]


def get_cors_origins() -> list[str]:
    return parse_cors_origins(os.environ.get("NANOBOT_CORS_ORIGINS", "*"))

# ── 厂商 API (OpenAI 兼容) ──
LLM_PROVIDER = os.environ.get(
    "LLM_PROVIDER", "new-api"
)  # new-api | deepseek | zhipu | qwen | openrouter | gemini | siliconflow
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# ── New-API 网关 ──
NEW_API_KEY = os.environ.get("NEW_API_KEY", "")
NEW_API_BASE_URL = os.environ.get("NEW_API_BASE_URL", "https://api.new-api.com/v1")
NEW_API_TIMEOUT = int(os.environ.get("NEW_API_TIMEOUT", "180"))
NEW_API_AUTO_MODEL_SYNC = os.environ.get("NEW_API_AUTO_MODEL_SYNC", "1") == "1"
NEW_API_MODEL_SYNC_INTERVAL_MINUTES = int(
    os.environ.get("NEW_API_MODEL_SYNC_INTERVAL_MINUTES", "60")
)

# 自动路由：off | code | model
AUTO_MODEL_ROUTING_MODE = os.environ.get("AUTO_MODEL_ROUTING_MODE", "code")

# 模型分级与预算控制
LLM_MODEL_SMART = os.environ.get("LLM_MODEL_SMART", "")
LLM_MODEL_FAST = os.environ.get("LLM_MODEL_FAST", "")
LLM_MODEL_REASONING = os.environ.get("LLM_MODEL_REASONING", "")
LLM_BUDGET_CAP = float(
    os.environ.get("LLM_BUDGET_CAP", "10.0")
)  # 每百万 Token 输入的最大允许成本

# 回复主链路模型策略：当前 KT 仍是 planner/reply 同轮调用，这组配置用于提升最终生成链路智能度。
LLM_MODEL_REPLY = os.environ.get("LLM_MODEL_REPLY", "deepseek-v4-flash-max")
REPLY_MODEL_INTEL_FLOOR = int(os.environ.get("REPLY_MODEL_INTEL_FLOOR", "12"))
REPLY_MODEL_INTEL_BOOST = int(os.environ.get("REPLY_MODEL_INTEL_BOOST", "2"))
REPLY_MODEL_MAX_COST = float(
    os.environ.get("REPLY_MODEL_MAX_COST", str(LLM_BUDGET_CAP))
)

# ── Agent 行为 ──
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "5"))  # 单次对话最大工具轮数
NEW_API_MAX_RETRIES = int(os.environ.get("NEW_API_MAX_RETRIES", "3"))

# ── 模型路由参数 ──
# 优先级评分权重 (参考 RouteLLM: cost ×10 + intel ×3)
ROUTER_COST_WEIGHT = float(os.environ.get("ROUTER_COST_WEIGHT", "6.0"))
ROUTER_INTEL_WEIGHT = float(os.environ.get("ROUTER_INTEL_WEIGHT", "5.0"))
ROUTER_FREE_BONUS = float(os.environ.get("ROUTER_FREE_BONUS", "-2.0"))
ROUTER_UNSTABLE_PENALTY = float(os.environ.get("ROUTER_UNSTABLE_PENALTY", "5.0"))
# 熔断器参数: 连续失败 N 次后临时禁用，指数退避恢复
MODEL_MAX_CONSECUTIVE_FAILURES = int(
    os.environ.get("MODEL_MAX_CONSECUTIVE_FAILURES", "3")
)
MODEL_COOLDOWN_BASE_SECONDS = int(os.environ.get("MODEL_COOLDOWN_BASE_SECONDS", "300"))
MODEL_COOLDOWN_MAX_SECONDS = int(os.environ.get("MODEL_COOLDOWN_MAX_SECONDS", "1800"))

# ── 每日记忆折叠 ──
DAILY_DIGEST_ENABLED = os.environ.get("DAILY_DIGEST_ENABLED", "1") == "1"
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", "4"))

# ── 私聊分类器 ──
CLASSIFIER_API_URL = os.environ.get("CLASSIFIER_API_URL", "http://172.17.0.1:9999/v1")
SENTINEL_MODEL_PATH = os.environ.get("SENTINEL_MODEL_PATH", "./models/sentinel")
CLASSIFIER_TIMEOUT = float(os.environ.get("CLASSIFIER_TIMEOUT", "15.0"))

# ── 图片摘要（复用本地 Qwen 视觉模型） ──
IMAGE_SUMMARY_API_URL = os.environ.get("IMAGE_SUMMARY_API_URL", CLASSIFIER_API_URL)
IMAGE_SUMMARY_TIMEOUT = float(os.environ.get("IMAGE_SUMMARY_TIMEOUT", "120.0"))
IMAGE_SUMMARY_MAX_TOKENS = int(os.environ.get("IMAGE_SUMMARY_MAX_TOKENS", "512"))
IMAGE_SUMMARY_TEMPERATURE = float(os.environ.get("IMAGE_SUMMARY_TEMPERATURE", "0.1"))
IMAGE_SUMMARY_TOP_P = float(os.environ.get("IMAGE_SUMMARY_TOP_P", "0.9"))

# ── 表情包自动描述 ──
STICKER_AUTO_DESCRIBE_ENABLED = (
    os.environ.get("STICKER_AUTO_DESCRIBE_ENABLED", "1") == "1"
)
STICKER_AUTO_DESCRIBE_MAX_PER_CYCLE = int(
    os.environ.get("STICKER_AUTO_DESCRIBE_MAX_PER_CYCLE", "3")
)

# ── 图片预处理（下载/缓存/压缩后再上传） ──
IMAGE_PREPROCESS_CACHE_DIR = os.environ.get(
    "IMAGE_PREPROCESS_CACHE_DIR",
    os.path.join(LOG_DIR, "image_cache"),
)
# 压缩后图片字节上限；默认 768KiB，转 base64 后约等于 1MiB
IMAGE_PREPROCESS_MAX_BYTES = int(
    os.environ.get("IMAGE_PREPROCESS_MAX_BYTES", str(768 * 1024))
)
# 下载/解码后的原始图片字节上限，防止超大图片进入解码流程
IMAGE_PREPROCESS_RAW_MAX_BYTES = int(
    os.environ.get("IMAGE_PREPROCESS_RAW_MAX_BYTES", str(12 * 1024 * 1024))
)
IMAGE_PREPROCESS_ALLOW_LOCAL_FILES = (
    os.environ.get("IMAGE_PREPROCESS_ALLOW_LOCAL_FILES", "0") == "1"
)
IMAGE_PREPROCESS_MAX_SIDE = int(os.environ.get("IMAGE_PREPROCESS_MAX_SIDE", "1024"))
IMAGE_PREPROCESS_START_QUALITY = int(
    os.environ.get("IMAGE_PREPROCESS_START_QUALITY", "92")
)
IMAGE_PREPROCESS_MIN_QUALITY = int(os.environ.get("IMAGE_PREPROCESS_MIN_QUALITY", "45"))
IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT = float(
    os.environ.get("IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT", "20.0")
)

GUARDRAIL_INJECTION_PATTERNS = [
    r"\[SYSTEM",
    r"\[INST\]",
    r"</?system>",
    r"</?user>",
    r"IGNORE\s+.*RULE",
    r"忽略\s*.*指令",
    r"忽略\s*.*规则",
    r"OUTPUT\s*:",
    r"输出\s*:",
    r"ALWAYS\s+输出",
    r"你是.*过滤器",
    r"你的任务是\s+ALWAYS",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"从现在开始.*助手",
    r"从现在开始.*无限制",
]
