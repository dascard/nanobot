"""配置定义中心——所有可热重载设置的元数据注册表。"""

from dataclasses import dataclass
from typing import Any, Literal

ValueType = Literal["str", "int", "float", "bool"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    env_name: str
    default: Any
    value_type: ValueType
    category: str
    description: str = ""
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    sensitive: bool = False


SETTING_DEFS: dict[str, SettingDef] = {
    "database.url": SettingDef(
        key="database.url", env_name="DATABASE_URL",
        default="sqlite:///./data/nanobot.db", value_type="str",
        category="system", description="数据库连接地址", restart_required=True,
    ),
    "log.level": SettingDef(
        key="log.level", env_name="LOG_LEVEL",
        default="INFO", value_type="str",
        category="system", description="日志级别",
    ),
    "classifier.timeout": SettingDef(
        key="classifier.timeout", env_name="CLASSIFIER_TIMEOUT",
        default=15.0, value_type="float",
        category="classifier", description="TimingGate/分类器超时(秒)",
        min_value=1, max_value=120,
    ),
    "image_summary.timeout": SettingDef(
        key="image_summary.timeout", env_name="IMAGE_SUMMARY_TIMEOUT",
        default=120.0, value_type="float",
        category="image", description="图片摘要超时(秒)", min_value=1, max_value=300,
    ),
    "image_summary.max_tokens": SettingDef(
        key="image_summary.max_tokens", env_name="IMAGE_SUMMARY_MAX_TOKENS",
        default=512, value_type="int",
        category="image", description="图片摘要最大token", min_value=64, max_value=4096,
    ),
    "image_summary.temperature": SettingDef(
        key="image_summary.temperature", env_name="IMAGE_SUMMARY_TEMPERATURE",
        default=0.1, value_type="float",
        category="image", description="图片摘要temperature", min_value=0, max_value=2.0,
    ),
    "image_summary.top_p": SettingDef(
        key="image_summary.top_p", env_name="IMAGE_SUMMARY_TOP_P",
        default=0.9, value_type="float",
        category="image", description="图片摘要top_p", min_value=0, max_value=1.0,
    ),
    "sticker.auto_describe_enabled": SettingDef(
        key="sticker.auto_describe_enabled",
        env_name="STICKER_AUTO_DESCRIBE_ENABLED",
        default=True, value_type="bool",
        category="sticker", description="自动描述新表情包",
    ),
    "sticker.auto_describe_max_per_cycle": SettingDef(
        key="sticker.auto_describe_max_per_cycle",
        env_name="STICKER_AUTO_DESCRIBE_MAX_PER_CYCLE",
        default=3, value_type="int",
        category="sticker", description="每轮自动描述上限", min_value=1, max_value=100,
    ),
    "model.reply": SettingDef(
        key="model.reply", env_name="LLM_MODEL_REPLY",
        default="deepseek-v4-flash-max", value_type="str",
        category="model", description="对话回复模型ID",
    ),
    "model.smart": SettingDef(
        key="model.smart", env_name="LLM_MODEL_SMART",
        default="", value_type="str", category="model", description="智能路由默认模型",
    ),
    "model.fast": SettingDef(
        key="model.fast", env_name="LLM_MODEL_FAST",
        default="", value_type="str", category="model", description="快速模型",
    ),
    "model.reasoning": SettingDef(
        key="model.reasoning", env_name="LLM_MODEL_REASONING",
        default="", value_type="str", category="model", description="推理模型",
    ),
    "new_api.timeout": SettingDef(
        key="new_api.timeout", env_name="NEW_API_TIMEOUT",
        default=180, value_type="int",
        category="model", description="NewAPI超时(秒)", min_value=10, max_value=600,
    ),
    "new_api.max_retries": SettingDef(
        key="new_api.max_retries", env_name="NEW_API_MAX_RETRIES",
        default=3, value_type="int",
        category="model", description="NewAPI最大重试", min_value=0, max_value=10,
    ),
    "max_tool_rounds": SettingDef(
        key="max_tool_rounds", env_name="MAX_TOOL_ROUNDS",
        default=5, value_type="int",
        category="model", description="单次最大工具轮数", min_value=1, max_value=20,
    ),
    "router.cost_weight": SettingDef(
        key="router.cost_weight", env_name="ROUTER_COST_WEIGHT",
        default=6.0, value_type="float",
        category="router", description="路由成本权重", min_value=0, max_value=20,
    ),
    "router.intel_weight": SettingDef(
        key="router.intel_weight", env_name="ROUTER_INTEL_WEIGHT",
        default=5.0, value_type="float",
        category="router", description="路由智能度权重", min_value=0, max_value=20,
    ),
    "router.free_bonus": SettingDef(
        key="router.free_bonus", env_name="ROUTER_FREE_BONUS",
        default=-2.0, value_type="float",
        category="router", description="免费模型加分", min_value=-20, max_value=20,
    ),
    "router.unstable_penalty": SettingDef(
        key="router.unstable_penalty", env_name="ROUTER_UNSTABLE_PENALTY",
        default=5.0, value_type="float",
        category="router", description="不稳定模型扣分", min_value=0, max_value=20,
    ),
    "model.max_consecutive_failures": SettingDef(
        key="model.max_consecutive_failures",
        env_name="MODEL_MAX_CONSECUTIVE_FAILURES",
        default=3, value_type="int",
        category="router", description="熔断连续失败次数", min_value=1, max_value=20,
    ),
    "model.cooldown_base_seconds": SettingDef(
        key="model.cooldown_base_seconds",
        env_name="MODEL_COOLDOWN_BASE_SECONDS",
        default=300, value_type="int",
        category="router", description="熔断基础冷却(秒)", min_value=30, max_value=3600,
    ),
    "model.cooldown_max_seconds": SettingDef(
        key="model.cooldown_max_seconds",
        env_name="MODEL_COOLDOWN_MAX_SECONDS",
        default=1800, value_type="int",
        category="router", description="熔断最大冷却(秒)", min_value=60, max_value=36000,
    ),
    "daily_digest.enabled": SettingDef(
        key="daily_digest.enabled", env_name="DAILY_DIGEST_ENABLED",
        default=True, value_type="bool",
        category="scheduler", description="启用日报定时推送",
    ),
    "daily_digest.hour": SettingDef(
        key="daily_digest.hour", env_name="DAILY_DIGEST_HOUR",
        default=8, value_type="int",
        category="scheduler", description="日报推送小时", min_value=0, max_value=23,
    ),
}
