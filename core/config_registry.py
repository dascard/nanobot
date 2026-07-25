"""配置定义中心——所有受管设置的类型化元数据注册表。"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.lifecycle import COMPATIBILITY_REGISTRY, CompatibilityKind
from core.model_provider.route_registry import list_model_route_descriptors
from core.settings_specs import (
    SettingDef,
    SettingSpec,
    validate_setting_catalog,
)


@dataclass(frozen=True)
class LegacySettingAlias:
    """一个发布周期内保留的旧设置入口。"""

    key: str
    env_name: str
    compatibility_id: str


def _legacy_setting_alias_projection(
) -> Mapping[str, LegacySettingAlias]:
    """从 Compatibility Registry 生成旧设置只读投影。"""

    projected: dict[str, LegacySettingAlias] = {}
    for descriptor in COMPATIBILITY_REGISTRY.descriptors(
        CompatibilityKind.SETTING
    ):
        if descriptor.environment_alias is None:
            raise ValueError(
                f"旧设置 {descriptor.compatibility_id} 缺少环境变量 alias"
            )
        canonical_key = descriptor.canonical_replacement
        if canonical_key in projected:
            raise ValueError(f"旧设置 canonical key 冲突: {canonical_key}")
        projected[canonical_key] = LegacySettingAlias(
            key=descriptor.alias_value,
            env_name=descriptor.environment_alias,
            compatibility_id=descriptor.compatibility_id,
        )
    return MappingProxyType(projected)


LEGACY_SETTING_ALIASES = _legacy_setting_alias_projection()

LEGACY_SETTING_CANONICAL_KEYS = MappingProxyType({
    alias.key: canonical_key
    for canonical_key, alias in LEGACY_SETTING_ALIASES.items()
})


def canonical_setting_key(key: str) -> str:
    """把兼容期旧键归一为唯一 canonical key。"""

    return LEGACY_SETTING_CANONICAL_KEYS.get(str(key), str(key))


def _validate_proactive_outreach_probability_range(
    values: Mapping[str, object],
) -> None:
    minimum = values.get("proactive_outreach.surge_min_prob")
    maximum = values.get("proactive_outreach.surge_max_prob")
    if minimum is None or maximum is None:
        return
    if float(minimum) > float(maximum):
        raise ValueError(
            "proactive_outreach.surge_min_prob 不能大于 surge_max_prob"
        )


def _validate_private_timing_confidence_thresholds(
    values: Mapping[str, object],
) -> None:
    decision = values.get(
        "private_timing.decision_confidence_threshold"
    )
    template = values.get(
        "private_timing.template_confidence_threshold"
    )
    if decision is None or template is None:
        return
    if float(decision) > float(template):
        raise ValueError(
            "private_timing.decision_confidence_threshold "
            "不能大于 template_confidence_threshold"
        )


def _validate_news_governance_settings(
    values: Mapping[str, object],
) -> None:
    mode = str(
        values.get("news.relevance_review.mode", "disabled")
    ).strip().lower()
    if mode not in {"disabled", "observation", "active"}:
        raise ValueError(
            "news.relevance_review.mode 必须是 "
            "disabled/observation/active"
        )
    raw_overrides = str(values.get("news.source_overrides", "{}"))
    try:
        overrides = json.loads(raw_overrides)
    except json.JSONDecodeError as exc:
        raise ValueError("news.source_overrides 必须是 JSON 对象") from exc
    if not isinstance(overrides, dict):
        raise ValueError("news.source_overrides 必须是 JSON 对象")
    from core.news.source_registry import load_news_source_registry

    load_news_source_registry(operator_overrides=overrides)


SETTING_DEFS: dict[str, SettingDef] = {
    "runtime.data_dir": SettingDef(
        key="runtime.data_dir",
        env_name="NANOBOT_DATA_DIR",
        default="./data",
        value_type="str",
        category="system",
        description="运行时持久数据根目录",
        restart_required=True,
        dangerous=True,
        source_precedence=("environment", "default"),
        owner_module="core.runtime_paths",
    ),
    "runtime.temp_dir": SettingDef(
        key="runtime.temp_dir",
        env_name="NANOBOT_TEMP_DIR",
        default="./tmp",
        value_type="str",
        category="system",
        description="运行时临时文件根目录",
        restart_required=True,
        dangerous=True,
        source_precedence=("environment", "default"),
        owner_module="core.runtime_paths",
    ),
    "database.url": SettingDef(
        key="database.url", env_name="DATABASE_URL",
        default="sqlite:///./data/nanobot.db", value_type="str",
        category="system", description="数据库连接地址", restart_required=True, dangerous=True,
        source_precedence=("environment", "default"),
        owner_module="core.database",
        safety_class="invariant",
    ),
    "log.level": SettingDef(
        key="log.level", env_name="LOG_LEVEL",
        default="INFO", value_type="str",
        category="system", description="日志级别",
    ),
    "cors.origins": SettingDef(
        key="cors.origins", env_name="NANOBOT_CORS_ORIGINS",
        default="*", value_type="str",
        category="system",
        description="CORS 允许来源，支持 * 或逗号分隔 URL",
        restart_required=True,
    ),
    "bot.character_name": SettingDef(
        key="bot.character_name",
        env_name="NANOBOT_CHARACTER_NAME",
        default="nanobot",
        value_type="str",
        category="bot",
        description="角色名，对应 Prompt V2 变量 {{ character_name }} / {{ name_hint }}",
    ),
    "bot.alias_names": SettingDef(
        key="bot.alias_names",
        env_name="NANOBOT_BOT_ALIASES",
        default="nanobot",
        value_type="str",
        category="bot",
        description="角色别名，支持逗号或换行分隔，对应 {{ alias_names }} / {{ bot_aliases }}",
    ),
    "persona.injection_enabled": SettingDef(
        key="persona.injection_enabled",
        env_name="PERSONA_INJECTION_ENABLED",
        default=False,
        value_type="bool",
        category="memory",
        description="是否允许在真实私聊回复中自动注入已审核画像",
    ),
    "persona.auto_update_enabled": SettingDef(
        key="persona.auto_update_enabled",
        env_name="PERSONA_AUTO_UPDATE_ENABLED",
        default=False,
        value_type="bool",
        category="memory",
        description="是否允许后台进化任务自动提取并累加画像事实",
    ),
    "group_memory.injection_enabled": SettingDef(
        key="group_memory.injection_enabled",
        env_name="GROUP_MEMORY_INJECTION_ENABLED",
        default=False,
        value_type="bool",
        category="memory",
        description="是否允许在真实群聊回复中自动检索并注入群记忆",
    ),
    "group_learning.enabled": SettingDef(
        key="group_learning.enabled",
        env_name="NANOBOT_GROUP_LEARNING_ENABLED",
        default=False,
        value_type="bool",
        category="memory",
        description="群学习候选扫描、模型审核和治理写入总开关",
        dangerous=True,
        owner_module="core.group_learning",
    ),
    "group_learning.rule_controls": SettingDef(
        key="group_learning.rule_controls",
        env_name="NANOBOT_GROUP_LEARNING_RULE_CONTROLS",
        default="",
        value_type="str",
        category="memory",
        description="群学习提取规则的全局与 canonical session 禁用配置",
        dangerous=True,
        owner_module="core.group_learning",
    ),
    "proactive_outreach.enabled": SettingDef(
        key="proactive_outreach.enabled",
        env_name="PROACTIVE_OUTREACH_ENABLED",
        default=False,
        value_type="bool",
        category="proactive",
        description="主动情感外呼总开关；单用户自用场景下为纯布尔开关",
    ),
    "proactive_outreach.fallback_interval_min": SettingDef(
        key="proactive_outreach.fallback_interval_min",
        env_name="PROACTIVE_OUTREACH_FALLBACK_INTERVAL_MIN",
        default=120,
        value_type="int",
        category="proactive",
        description="主动情感外呼兜底心跳间隔(分钟)",
        min_value=1,
        max_value=10080,
    ),
    "proactive_outreach.min_interval_min": SettingDef(
        key="proactive_outreach.min_interval_min",
        env_name="PROACTIVE_OUTREACH_MIN_INTERVAL_MIN",
        default=30,
        value_type="int",
        category="proactive",
        description="两次主动外呼最小间隔和 next_check_at 下界(分钟)",
        min_value=1,
        max_value=1440,
    ),
    "proactive_outreach.max_check_interval_min": SettingDef(
        key="proactive_outreach.max_check_interval_min",
        env_name="PROACTIVE_OUTREACH_MAX_CHECK_INTERVAL_MIN",
        default=1440,
        value_type="int",
        category="proactive",
        description="Judge 单次 next_check_at 最大推迟时间(分钟)",
        min_value=1,
        max_value=10080,
    ),
    "proactive_outreach.max_silence_min": SettingDef(
        key="proactive_outreach.max_silence_min",
        env_name="PROACTIVE_OUTREACH_MAX_SILENCE_MIN",
        default=2880,
        value_type="int",
        category="proactive",
        description="超过该沉默窗口未真实发送时强制开口(分钟)",
        min_value=1,
        max_value=43200,
    ),
    "proactive_outreach.ambiguous_hold_min": SettingDef(
        key="proactive_outreach.ambiguous_hold_min",
        env_name="PROACTIVE_OUTREACH_AMBIGUOUS_HOLD_MIN",
        default=120,
        value_type="int",
        category="proactive",
        description="投递结果不确定后暂停新外呼评估的时间(分钟)",
        min_value=1,
        max_value=10080,
    ),
    "proactive_outreach.repeat_topic_cooldown_min": SettingDef(
        key="proactive_outreach.repeat_topic_cooldown_min",
        env_name="PROACTIVE_OUTREACH_REPEAT_TOPIC_COOLDOWN_MIN",
        default=1440,
        value_type="int",
        category="proactive",
        description="同一用户锚点和后续意图再次外呼前的冷却时间(分钟)",
        min_value=1,
        max_value=10080,
    ),
    "proactive_outreach.allow_early_surge": SettingDef(
        key="proactive_outreach.allow_early_surge",
        env_name="PROACTIVE_OUTREACH_ALLOW_EARLY_SURGE",
        default=False,
        value_type="bool",
        category="proactive",
        description="是否允许在 next_check_at 前按概率提前重新评估",
    ),
    "proactive_outreach.surge_min_prob": SettingDef(
        key="proactive_outreach.surge_min_prob",
        env_name="PROACTIVE_OUTREACH_SURGE_MIN_PROB",
        default=0.1,
        value_type="float",
        category="proactive",
        description="next_check_at 未到时，活跃时段提前考虑的最低冲击概率",
        min_value=0.0,
        max_value=1.0,
    ),
    "proactive_outreach.surge_max_prob": SettingDef(
        key="proactive_outreach.surge_max_prob",
        env_name="PROACTIVE_OUTREACH_SURGE_MAX_PROB",
        default=0.6,
        value_type="float",
        category="proactive",
        description="next_check_at 未到时，活跃时段提前考虑的最高冲击概率",
        min_value=0.0,
        max_value=1.0,
        cross_field_validator=_validate_proactive_outreach_probability_range,
    ),
    "classifier.timeout": SettingDef(
        key="classifier.timeout", env_name="CLASSIFIER_TIMEOUT",
        default=15.0, value_type="float",
        category="classifier", description="TimingGate/分类器超时(秒)",
        min_value=1, max_value=120,
    ),
    "private_timing.rollout.default_mode": SettingDef(
        key="private_timing.rollout.default_mode",
        env_name="PRIVATE_TIMING_ROLLOUT_DEFAULT_MODE",
        default="disabled",
        value_type="str",
        category="classifier",
        description="私聊 Timing v2 默认灰度模式：disabled/observation/active",
        owner_module="core.private_timing_policy",
    ),
    "private_timing.rollout.session_modes": SettingDef(
        key="private_timing.rollout.session_modes",
        env_name="PRIVATE_TIMING_ROLLOUT_SESSION_MODES",
        default="{}",
        value_type="str",
        category="classifier",
        description="私聊 Timing v2 按 canonical session 配置灰度模式的 JSON 对象",
        owner_module="core.private_timing_policy",
    ),
    "private_timing.rollout.active_allowed": SettingDef(
        key="private_timing.rollout.active_allowed",
        env_name="PRIVATE_TIMING_ROLLOUT_ACTIVE_ALLOWED",
        default=False,
        value_type="bool",
        category="classifier",
        description="发布门禁是否允许私聊 Timing v2 从观察切为生效",
        dangerous=True,
        owner_module="core.private_timing_policy",
    ),
    "private_timing.decision_confidence_threshold": SettingDef(
        key="private_timing.decision_confidence_threshold",
        env_name="PRIVATE_TIMING_DECISION_CONFIDENCE_THRESHOLD",
        default=0.70,
        value_type="float",
        category="classifier",
        description="私聊结构化语义决策最低置信度",
        min_value=0,
        max_value=1,
        owner_module="core.private_timing_policy",
    ),
    "private_timing.template_confidence_threshold": SettingDef(
        key="private_timing.template_confidence_threshold",
        env_name="PRIVATE_TIMING_TEMPLATE_CONFIDENCE_THRESHOLD",
        default=0.85,
        value_type="float",
        category="classifier",
        description="私聊模板 Fast Path 最低置信度",
        min_value=0,
        max_value=1,
        owner_module="core.private_timing_policy",
        cross_field_validator=(
            _validate_private_timing_confidence_thresholds
        ),
    ),
    "news.relevance_review.mode": SettingDef(
        key="news.relevance_review.mode",
        env_name="NEWS_RELEVANCE_REVIEW_MODE",
        default="disabled",
        value_type="str",
        category="news",
        description="新闻相关性批量审核模式：disabled/observation/active",
        owner_module="core.news",
    ),
    "news.relevance_review.active_allowed": SettingDef(
        key="news.relevance_review.active_allowed",
        env_name="NEWS_RELEVANCE_REVIEW_ACTIVE_ALLOWED",
        default=False,
        value_type="bool",
        category="news",
        description="发布门禁是否允许新闻审核从观察切为生效",
        dangerous=True,
        owner_module="core.news",
    ),
    "news.relevance_review.confidence_threshold": SettingDef(
        key="news.relevance_review.confidence_threshold",
        env_name="NEWS_RELEVANCE_REVIEW_CONFIDENCE_THRESHOLD",
        default=0.80,
        value_type="float",
        category="news",
        description="新闻审核结果允许删除候选的最低置信度",
        min_value=0,
        max_value=1,
        owner_module="core.news",
    ),
    "news.relevance_review.max_batch_size": SettingDef(
        key="news.relevance_review.max_batch_size",
        env_name="NEWS_RELEVANCE_REVIEW_MAX_BATCH_SIZE",
        default=24,
        value_type="int",
        category="news",
        description="单次新闻相关性审核最大候选数",
        min_value=1,
        max_value=40,
        owner_module="core.news",
    ),
    "news.source_overrides": SettingDef(
        key="news.source_overrides",
        env_name="NEWS_SOURCE_OVERRIDES",
        default="{}",
        value_type="str",
        category="news",
        description="仅允许覆盖启用、质量权重、超时和单次上限的 JSON 对象",
        owner_module="core.news",
        cross_field_validator=_validate_news_governance_settings,
    ),
    "timing_gate.model_policy.default": SettingDef(
        key="timing_gate.model_policy.default",
        env_name="TIMING_GATE_MODEL_POLICY_DEFAULT",
        default="enabled",
        value_type="str",
        category="classifier",
        description="TimingGate 模型层默认策略: enabled/rules_only/shadow",
    ),
    "timing_gate.model_policy.platforms": SettingDef(
        key="timing_gate.model_policy.platforms",
        env_name="TIMING_GATE_MODEL_POLICY_PLATFORMS",
        default="{}",
        value_type="str",
        category="classifier",
        description="TimingGate 按 platform 覆盖模型策略的 JSON 对象",
    ),
    "timing_gate.model_policy.sessions": SettingDef(
        key="timing_gate.model_policy.sessions",
        env_name="TIMING_GATE_MODEL_POLICY_SESSIONS",
        default="{}",
        value_type="str",
        category="classifier",
        description="TimingGate 按 session 覆盖模型策略的 JSON 对象",
    ),
    "timing_gate.proactive.enabled": SettingDef(
        key="timing_gate.proactive.enabled",
        env_name="TIMING_GATE_PROACTIVE_ENABLED",
        default=True, value_type="bool",
        category="classifier",
        description="群聊主动发言总开关(代码级 kill switch;真实上线由协议层发言许可兜底)",
    ),
    "timing_gate.proactive.window_sec": SettingDef(
        key="timing_gate.proactive.window_sec",
        env_name="TIMING_GATE_PROACTIVE_WINDOW_SEC",
        default=1800, value_type="int",
        category="classifier",
        description="主动发言预算滑动窗口(秒)", min_value=60, max_value=86400,
    ),
    "timing_gate.proactive.max_per_window": SettingDef(
        key="timing_gate.proactive.max_per_window",
        env_name="TIMING_GATE_PROACTIVE_MAX_PER_WINDOW",
        default=2, value_type="int",
        category="classifier",
        description="每群每窗口最多主动发言次数", min_value=1, max_value=20,
    ),
    "timing_gate.proactive.activity_floor": SettingDef(
        key="timing_gate.proactive.activity_floor",
        env_name="TIMING_GATE_PROACTIVE_ACTIVITY_FLOOR",
        default=3, value_type="int",
        category="classifier",
        description="主动发言活跃度下限(msg_5m 少于此值视为死群,不主动)", min_value=0, max_value=100,
    ),
    "timing_gate.proactive.activity_ceiling": SettingDef(
        key="timing_gate.proactive.activity_ceiling",
        env_name="TIMING_GATE_PROACTIVE_ACTIVITY_CEILING",
        default=20, value_type="int",
        category="classifier",
        description="主动发言活跃度上限(msg_1m 超过此值视为刷屏,不插话)", min_value=1, max_value=500,
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
        owner_module="core.model_provider",
    ),
    "model.reply_intel_floor": SettingDef(
        key="model.reply_intel_floor", env_name="REPLY_MODEL_INTEL_FLOOR",
        default=12, value_type="int", category="model",
        description="回复候选模型最低智能等级", min_value=1, max_value=20,
        owner_module="core.model_provider",
    ),
    "model.reply_intel_boost": SettingDef(
        key="model.reply_intel_boost", env_name="REPLY_MODEL_INTEL_BOOST",
        default=2, value_type="int", category="model",
        description="在请求复杂度基础上追加的智能等级", min_value=0, max_value=20,
        owner_module="core.model_provider",
    ),
    "model.reply_max_cost": SettingDef(
        key="model.reply_max_cost", env_name="REPLY_MODEL_MAX_COST",
        default=10.0, value_type="float", category="model",
        description="回复候选模型最大输入成本", min_value=0, max_value=1000,
        owner_module="core.model_provider",
    ),
    "prompt_runtime.engine": SettingDef(
        key="prompt_runtime.engine", env_name="NANOBOT_PROMPT_ENGINE",
        default="prompt", value_type="str",
        category="prompt", description="提示词运行引擎；旧 v1/v2 值会被迁移兼容层归一到 canonical runtime",
    ),
    "prompt_runtime.v2_audit_failure_policy": SettingDef(
        key="prompt_runtime.v2_audit_failure_policy",
        env_name="NANOBOT_PROMPT_V2_AUDIT_FAILURE_POLICY",
        default="fail_fast",
        value_type="str",
        category="prompt",
        description="Prompt Runtime live audit 失败策略；fallback_v1 已废弃，运行时固定 fail_fast",
    ),
    "model.smart": SettingDef(
        key="model.smart", env_name="LLM_MODEL_SMART",
        default="", value_type="str", category="model", description="智能路由默认模型",
    ),
    "model.fast": SettingDef(
        key="model.fast", env_name="LLM_MODEL_FAST",
        default="", value_type="str", category="model", description="快速模型",
    ),
    "model.session_summary": SettingDef(
        key="model.session_summary", env_name="LLM_MODEL_SESSION_SUMMARY",
        default="", value_type="str",
        category="model", description="近期摘要模型",
    ),
    "model.memory_digest": SettingDef(
        key="model.memory_digest", env_name="",
        default="", value_type="str",
        category="model", description="长期摘要模型",
    ),
    "model.reasoning": SettingDef(
        key="model.reasoning", env_name="LLM_MODEL_REASONING",
        default="", value_type="str", category="model", description="推理模型",
    ),
    "model.route.timing_gate": SettingDef(
        key="model.route.timing_gate", env_name="CLASSIFIER_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model",
        description="分类器地址；完整配置请使用 model.route.timing_gate.model/api_key/timeout 等子字段",
    ),
    "model.route.timing_gate.model": SettingDef(
        key="model.route.timing_gate.model", env_name="",
        default="", value_type="str",
        category="model", description="TimingGate 模型名（API 调用时需要）",
    ),
    "model.route.timing_gate.api_key": SettingDef(
        key="model.route.timing_gate.api_key", env_name="",
        default="", value_type="str",
        category="model", description="TimingGate API key", sensitive=True,
    ),
    "model.route.timing_gate.timeout": SettingDef(
        key="model.route.timing_gate.timeout", env_name="",
        default=15, value_type="int",
        category="model", description="TimingGate 超时(秒)", min_value=3, max_value=120,
    ),
    "model.route.timing_gate.temperature": SettingDef(
        key="model.route.timing_gate.temperature", env_name="",
        default=0.0, value_type="float",
        category="model", description="TimingGate 温度", min_value=0, max_value=2,
    ),
    "model.route.timing_gate.max_tokens": SettingDef(
        key="model.route.timing_gate.max_tokens", env_name="",
        default=30, value_type="int",
        category="model", description="TimingGate 最大输出 tokens", min_value=5, max_value=500,
    ),
    "model.route.private_decision": SettingDef(
        key="model.route.private_decision", env_name="",
        default="", value_type="str",
        category="model", description="私聊决策分类器地址（空则回退到 timing_gate）",
    ),
    "model.route.private_decision.model": SettingDef(
        key="model.route.private_decision.model", env_name="",
        default="", value_type="str",
        category="model", description="私聊决策模型名",
    ),
    "model.route.private_decision.api_key": SettingDef(
        key="model.route.private_decision.api_key", env_name="",
        default="", value_type="str",
        category="model", description="私聊决策 API key", sensitive=True,
    ),
    "model.route.private_decision.timeout": SettingDef(
        key="model.route.private_decision.timeout", env_name="",
        default=15, value_type="int",
        category="model", min_value=3, max_value=120,
    ),
    "model.route.private_decision.temperature": SettingDef(
        key="model.route.private_decision.temperature", env_name="",
        default=0.0, value_type="float",
        category="model", min_value=0, max_value=2,
    ),
    "model.route.private_decision.max_tokens": SettingDef(
        key="model.route.private_decision.max_tokens", env_name="",
        default=120, value_type="int",
        category="model", min_value=5, max_value=500,
    ),
    "model.route.classifier_legacy": SettingDef(
        key="model.route.classifier_legacy", env_name="",
        default="", value_type="str",
        category="model", description="旧Qwen分类器地址（空则回退到 timing_gate）",
    ),
    "model.route.classifier_legacy.model": SettingDef(
        key="model.route.classifier_legacy.model", env_name="",
        default="", value_type="str",
        category="model", description="旧分类器模型名",
    ),
    "model.route.classifier_legacy.api_key": SettingDef(
        key="model.route.classifier_legacy.api_key", env_name="",
        default="", value_type="str",
        category="model", description="旧分类器 API key", sensitive=True,
    ),
    "model.route.classifier_legacy.timeout": SettingDef(
        key="model.route.classifier_legacy.timeout", env_name="",
        default=15, value_type="int",
        category="model", min_value=3, max_value=120,
    ),
    "model.route.classifier_legacy.temperature": SettingDef(
        key="model.route.classifier_legacy.temperature", env_name="",
        default=0.0, value_type="float",
        category="model", min_value=0, max_value=2,
    ),
    "model.route.classifier_legacy.max_tokens": SettingDef(
        key="model.route.classifier_legacy.max_tokens", env_name="",
        default=30, value_type="int",
        category="model", min_value=5, max_value=500,
    ),
    "model.route.sticker_describe": SettingDef(
        key="model.route.sticker_describe", env_name="IMAGE_SUMMARY_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model", description="表情包打标 API 地址",
    ),
    "model.route.sticker_describe.model": SettingDef(
        key="model.route.sticker_describe.model", env_name="",
        default="", value_type="str",
        category="model", description="表情包打标模型名",
    ),
    "model.route.sticker_describe.api_key": SettingDef(
        key="model.route.sticker_describe.api_key", env_name="",
        default="", value_type="str",
        category="model", description="表情包打标 API key", sensitive=True,
    ),
    "model.route.sticker_describe.timeout": SettingDef(
        key="model.route.sticker_describe.timeout", env_name="",
        default=15, value_type="int",
        category="model", min_value=3, max_value=120,
    ),
    "model.route.sticker_describe.temperature": SettingDef(
        key="model.route.sticker_describe.temperature", env_name="",
        default=0.0, value_type="float",
        category="model", min_value=0, max_value=2,
    ),
    "model.route.sticker_describe.max_tokens": SettingDef(
        key="model.route.sticker_describe.max_tokens", env_name="",
        default=256, value_type="int",
        category="model", min_value=10, max_value=2000,
    ),
    "rag.reranker.model_path": SettingDef(
        key="rag.reranker.model_path", env_name="RAG_LOCAL_RERANKER_MODEL",
        default="./models/bge-reranker-v2-m3", value_type="str",
        category="model", description="本地 RAG reranker 模型目录",
    ),
    "rag.reranker.hf_model": SettingDef(
        key="rag.reranker.hf_model", env_name="RAG_RERANKER_HF_MODEL",
        default="", value_type="str",
        category="model", description="可选 HuggingFace 下载源；默认目录使用 BAAI/bge-reranker-v2-m3",
    ),
    "rag.reranker.score_mode": SettingDef(
        key="rag.reranker.score_mode", env_name="RAG_RERANKER_SCORE_MODE",
        default="sigmoid", value_type="str",
        category="model", description="本地 RAG reranker 分数归一化: sigmoid/identity/minmax",
    ),
    "rag.reranker.max_text_chars": SettingDef(
        key="rag.reranker.max_text_chars", env_name="RAG_RERANKER_MAX_TEXT_CHARS",
        default=1200, value_type="int",
        category="model", description="发送给本地 reranker 的单候选文本最大字符数", min_value=100, max_value=20000,
    ),
    # ── Provider 供应商配置 ──
    "model.providers.newapi.base_url": SettingDef(
        key="model.providers.newapi.base_url", env_name="NEW_API_BASE_URL",
        default="", value_type="str",
        category="model", description="NewAPI 供应商地址",
    ),
    "model.providers.newapi.api_key": SettingDef(
        key="model.providers.newapi.api_key", env_name="NEW_API_KEY",
        default="", value_type="str",
        category="model", description="NewAPI API key", sensitive=True,
    ),
    "model.providers.newapi.enabled": SettingDef(
        key="model.providers.newapi.enabled", env_name="",
        default=True, value_type="bool",
        category="model", description="NewAPI 是否启用",
    ),
    "model.providers.newapi.registry_provider": SettingDef(
        key="model.providers.newapi.registry_provider", env_name="",
        default="new-api", value_type="str",
        category="model", description="NewAPI 在 registry 中的 provider 名",
    ),
    # [DEPRECATED] local_qwen is an alias — use local_llama instead
    "model.providers.local_qwen.base_url": SettingDef(
        key="model.providers.local_qwen.base_url", env_name="CLASSIFIER_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model", description="[DEPRECATED] 本地 Qwen 服务地址——请使用 local_llama",
    ),
    "model.providers.local_qwen.api_key": SettingDef(
        key="model.providers.local_qwen.api_key", env_name="",
        default="", value_type="str",
        category="model", description="[DEPRECATED] 本地 Qwen API key", sensitive=True,
    ),
    "model.providers.local_qwen.enabled": SettingDef(
        key="model.providers.local_qwen.enabled", env_name="",
        default=True, value_type="bool",
        category="model", description="[DEPRECATED] 本地 Qwen 是否启用",
    ),
    # [DEPRECATED] vision_qwen is an alias — use local_vision instead
    "model.providers.vision_qwen.base_url": SettingDef(
        key="model.providers.vision_qwen.base_url", env_name="IMAGE_SUMMARY_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model", description="[DEPRECATED] 视觉 Qwen 服务地址——请使用 local_vision",
    ),
    "model.providers.vision_qwen.api_key": SettingDef(
        key="model.providers.vision_qwen.api_key", env_name="",
        default="", value_type="str",
        category="model", description="[DEPRECATED] 视觉 Qwen API key", sensitive=True,
    ),
    "model.providers.vision_qwen.enabled": SettingDef(
        key="model.providers.vision_qwen.enabled", env_name="",
        default=True, value_type="bool",
        category="model", description="[DEPRECATED] 视觉 Qwen 是否启用",
    ),
    # ── 本地 llama.cpp 服务（原 local_qwen） ──
    "model.providers.local_llama.base_url": SettingDef(
        key="model.providers.local_llama.base_url", env_name="CLASSIFIER_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model", description="本地 llama.cpp 服务地址",
    ),
    "model.providers.local_llama.api_key": SettingDef(
        key="model.providers.local_llama.api_key", env_name="",
        default="", value_type="str",
        category="model", description="本地 llama.cpp API key", sensitive=True,
    ),
    "model.providers.local_llama.enabled": SettingDef(
        key="model.providers.local_llama.enabled", env_name="",
        default=True, value_type="bool",
        category="model", description="本地 llama.cpp 是否启用",
    ),
    "model.providers.local_llama.registry_provider": SettingDef(
        key="model.providers.local_llama.registry_provider", env_name="",
        default="", value_type="str",
        category="model", description="local_llama 在 registry 中的 provider 名",
    ),
    # ── 本地视觉模型（仅当 IMAGE_SUMMARY_API_URL != CLASSIFIER_API_URL 时才在列表中出现） ──
    "model.providers.local_vision.base_url": SettingDef(
        key="model.providers.local_vision.base_url", env_name="IMAGE_SUMMARY_API_URL",
        default="http://172.17.0.1:9999/v1", value_type="str",
        category="model", description="本地视觉模型服务地址（独立端点时启用）",
    ),
    "model.providers.local_vision.api_key": SettingDef(
        key="model.providers.local_vision.api_key", env_name="",
        default="", value_type="str",
        category="model", description="本地视觉模型 API key", sensitive=True,
    ),
    "model.providers.local_vision.enabled": SettingDef(
        key="model.providers.local_vision.enabled", env_name="",
        default=True, value_type="bool",
        category="model", description="本地视觉模型是否启用",
    ),
    "model.providers.local_vision.registry_provider": SettingDef(
        key="model.providers.local_vision.registry_provider", env_name="",
        default="", value_type="str",
        category="model", description="local_vision 在 registry 中的 provider 名",
    ),
    # ── Route provider 关联 ──
    "model.route.timing_gate.provider": SettingDef(
        key="model.route.timing_gate.provider", env_name="",
        default="local_llama", value_type="str",
        category="model", description="TimingGate 使用的供应商",
    ),
    "model.route.private_decision.provider": SettingDef(
        key="model.route.private_decision.provider", env_name="",
        default="", value_type="str",
        category="model", description="私聊决策供应商（空=继承timing_gate）",
    ),
    "model.route.classifier_legacy.provider": SettingDef(
        key="model.route.classifier_legacy.provider", env_name="",
        default="", value_type="str",
        category="model", description="旧分类器供应商（空=继承timing_gate）",
    ),
    "model.route.sticker_describe.provider": SettingDef(
        key="model.route.sticker_describe.provider", env_name="",
        default="local_llama", value_type="str",
        category="model", description="图片描述供应商（默认与 timing_gate 共用同一端点）",
    ),
    "model.route.reply.provider": SettingDef(
        key="model.route.reply.provider", env_name="",
        default="newapi", value_type="str",
        category="model", description="主回复供应商",
    ),
    "model.route.reply.timeout": SettingDef(
        key="model.route.reply.timeout", env_name="",
        default=120, value_type="int",
        category="model", description="主回复请求超时(秒)", min_value=3, max_value=300,
    ),
    "model.route.reply.temperature": SettingDef(
        key="model.route.reply.temperature", env_name="",
        default=0.7, value_type="float",
        category="model", description="主回复温度", min_value=0, max_value=2,
    ),
    "model.route.reply.max_tokens": SettingDef(
        key="model.route.reply.max_tokens", env_name="",
        default=0, value_type="int",
        category="model", description="主回复最大输出 tokens（0=由API决定）", min_value=0, max_value=200000,
    ),
    "model.route.fast.provider": SettingDef(
        key="model.route.fast.provider", env_name="",
        default="newapi", value_type="str",
        category="model", description="快速模型供应商",
    ),
    "model.route.fast.timeout": SettingDef(
        key="model.route.fast.timeout", env_name="",
        default=120, value_type="int",
        category="model", description="快速模型请求超时(秒)", min_value=3, max_value=300,
    ),
    "model.route.fast.temperature": SettingDef(
        key="model.route.fast.temperature", env_name="",
        default=0.7, value_type="float",
        category="model", description="快速模型温度", min_value=0, max_value=2,
    ),
    "model.route.fast.max_tokens": SettingDef(
        key="model.route.fast.max_tokens", env_name="",
        default=0, value_type="int",
        category="model", description="快速模型最大输出 tokens（0=由API决定）", min_value=0, max_value=200000,
    ),
    "model.route.smart.provider": SettingDef(
        key="model.route.smart.provider", env_name="",
        default="newapi", value_type="str",
        category="model", description="智能模型供应商",
    ),
    "model.route.smart.timeout": SettingDef(
        key="model.route.smart.timeout", env_name="",
        default=120, value_type="int",
        category="model", description="智能模型请求超时(秒)", min_value=3, max_value=300,
    ),
    "model.route.smart.temperature": SettingDef(
        key="model.route.smart.temperature", env_name="",
        default=0.7, value_type="float",
        category="model", description="智能模型温度", min_value=0, max_value=2,
    ),
    "model.route.smart.max_tokens": SettingDef(
        key="model.route.smart.max_tokens", env_name="",
        default=0, value_type="int",
        category="model", description="智能模型最大输出 tokens（0=由API决定）", min_value=0, max_value=200000,
    ),
    "model.route.session_summary.provider": SettingDef(
        key="model.route.session_summary.provider", env_name="",
        default="newapi", value_type="str",
        category="model", description="近期摘要供应商",
    ),
    "model.route.session_summary.timeout": SettingDef(
        key="model.route.session_summary.timeout", env_name="",
        default=120, value_type="int",
        category="model", description="近期摘要请求超时(秒)", min_value=3, max_value=300,
    ),
    "model.route.session_summary.temperature": SettingDef(
        key="model.route.session_summary.temperature", env_name="",
        default=0.1, value_type="float",
        category="model", description="近期摘要温度", min_value=0, max_value=2,
    ),
    "model.route.session_summary.max_tokens": SettingDef(
        key="model.route.session_summary.max_tokens", env_name="",
        default=4096, value_type="int",
        category="model", description="近期摘要最大输出 tokens", min_value=3000, max_value=12000,
    ),
    "model.route.memory_digest.provider": SettingDef(
        key="model.route.memory_digest.provider", env_name="",
        default="newapi", value_type="str",
        category="model", description="长期摘要供应商",
    ),
    "model.route.memory_digest.timeout": SettingDef(
        key="model.route.memory_digest.timeout", env_name="",
        default=180, value_type="int",
        category="model", description="长期摘要请求超时(秒)", min_value=3, max_value=600,
    ),
    "model.route.memory_digest.temperature": SettingDef(
        key="model.route.memory_digest.temperature", env_name="",
        default=0.1, value_type="float",
        category="model", description="长期摘要温度", min_value=0, max_value=2,
    ),
    "model.route.memory_digest.max_tokens": SettingDef(
        key="model.route.memory_digest.max_tokens", env_name="",
        default=8192, value_type="int",
        category="model", description="长期摘要最大输出 tokens", min_value=4096, max_value=20000,
    ),
    "new_api.timeout": SettingDef(
        key="new_api.timeout", env_name="NEW_API_TIMEOUT",
        default=300, value_type="int",
        category="model", description="NewAPI超时(秒)", min_value=10, max_value=600,
    ),
    "new_api.max_retries": SettingDef(
        key="new_api.max_retries", env_name="NEW_API_MAX_RETRIES",
        default=3, value_type="int",
        category="model", description="NewAPI最大重试", min_value=0, max_value=10,
    ),
    "image_generation.model": SettingDef(
        key="image_generation.model", env_name="IMAGE_GENERATION_MODEL",
        default="gpt-image", value_type="str",
        category="image", description="图片生成模型",
    ),
    "image_generation.timeout": SettingDef(
        key="image_generation.timeout", env_name="IMAGE_GENERATION_TIMEOUT",
        default=600.0, value_type="float",
        category="image", description="图片生成超时(秒)", min_value=10, max_value=1200,
    ),
    "tool.lightweight_set": SettingDef(
        key="tool.lightweight_set", env_name="",
        default='["reply","no_reply","image_summary","image_generation","sticker_search"]',
        value_type="str",
        category="tool", description="显式 lightweight 兼容预设；普通聊天不自动使用（JSON数组）",
    ),
    "sandbox.enabled": SettingDef(
        key="sandbox.enabled", env_name="NANOBOT_SANDBOX_ENABLED",
        default=False, value_type="bool", category="sandbox",
        description="Sandbox 工具总开关", dangerous=True,
    ),
    "sandbox.infrastructure_enable_allowed": SettingDef(
        key="sandbox.infrastructure_enable_allowed",
        env_name="NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        default=False,
        value_type="bool",
        category="sandbox",
        description="宿主运维允许 Sandbox 数据面的安全硬上限",
        restart_required=True,
        dangerous=True,
        source_precedence=("environment", "default"),
        safety_class="invariant",
    ),
    "sandbox.exec_enabled": SettingDef(
        key="sandbox.exec_enabled", env_name="NANOBOT_SANDBOX_EXEC_ENABLED",
        default=False, value_type="bool", category="sandbox",
        description="Sandbox 命令执行能力开关", dangerous=True,
    ),
    "sandbox.group_enabled": SettingDef(
        key="sandbox.group_enabled", env_name="NANOBOT_SANDBOX_GROUP_ENABLED",
        default=False, value_type="bool", category="sandbox",
        description="群聊 Workspace 开关", dangerous=True,
    ),
    "sandbox.workspace_quota_bytes": SettingDef(
        key="sandbox.workspace_quota_bytes", env_name="NANOBOT_SANDBOX_WORKSPACE_QUOTA_BYTES",
        default=2 * 1024 * 1024 * 1024, value_type="int", category="sandbox",
        description="单 Workspace 逻辑配额（字节）", min_value=1024 * 1024,
        max_value=1024 * 1024 * 1024 * 1024,
    ),
    "sandbox.asset_max_bytes": SettingDef(
        key="sandbox.asset_max_bytes", env_name="NANOBOT_SANDBOX_ASSET_MAX_BYTES",
        default=512 * 1024 * 1024, value_type="int", category="sandbox",
        description="单个资产大小上限（字节）", min_value=1024,
        max_value=100 * 1024 * 1024 * 1024,
    ),
    "sandbox.total_quota_bytes": SettingDef(
        key="sandbox.total_quota_bytes", env_name="NANOBOT_SANDBOX_TOTAL_QUOTA_BYTES",
        default=10 * 1024 * 1024 * 1024, value_type="int", category="sandbox",
        description="Sandbox Workspace 总逻辑预算（字节）", min_value=1024 * 1024,
        max_value=10 * 1024 * 1024 * 1024 * 1024,
    ),
    "sandbox.disk_max_percent": SettingDef(
        key="sandbox.disk_max_percent", env_name="NANOBOT_SANDBOX_DISK_MAX_PERCENT",
        default=80, value_type="int", category="sandbox",
        description="拒绝新写入和执行的磁盘使用率水位", min_value=1, max_value=99,
    ),
    "sandbox.disk_min_free_bytes": SettingDef(
        key="sandbox.disk_min_free_bytes", env_name="NANOBOT_SANDBOX_DISK_MIN_FREE_BYTES",
        default=50 * 1024 * 1024 * 1024, value_type="int", category="sandbox",
        description="Sandbox 必须保留的最小可用磁盘空间（字节）", min_value=0,
        max_value=10 * 1024 * 1024 * 1024 * 1024,
    ),
    "sandbox.sandboxd_socket": SettingDef(
        key="sandbox.sandboxd_socket", env_name="NANOBOT_SANDBOXD_SOCKET",
        default="/run/nanobot-sandboxd/sandboxd.sock", value_type="str",
        category="sandbox", description="sandboxd Unix Socket 路径",
        restart_required=True,
    ),
    "sandbox.sandboxd_token_file": SettingDef(
        key="sandbox.sandboxd_token_file", env_name="NANOBOT_SANDBOXD_TOKEN_FILE",
        default="/run/nanobot-sandboxd/client.token", value_type="str",
        category="sandbox", description="sandboxd 客户端 Token 文件路径",
        restart_required=True,
    ),
    "sandbox.sandboxd_admin_token_file": SettingDef(
        key="sandbox.sandboxd_admin_token_file",
        env_name="NANOBOT_SANDBOXD_ADMIN_TOKEN_FILE",
        default="/run/nanobot-sandboxd/admin-client.token",
        value_type="str",
        category="sandbox",
        description="sandboxd 配额管理专用客户端 Token 文件路径",
        restart_required=True,
        dangerous=True,
        source_precedence=("environment", "default"),
        safety_class="invariant",
    ),
    "sandbox.backend_timeout_seconds": SettingDef(
        key="sandbox.backend_timeout_seconds", env_name="NANOBOT_SANDBOX_BACKEND_TIMEOUT",
        default=15, value_type="int", category="sandbox",
        description="sandboxd 普通请求超时（秒）", min_value=1, max_value=60,
    ),
    "sandbox.run_timeout_seconds": SettingDef(
        key="sandbox.run_timeout_seconds", env_name="NANOBOT_SANDBOX_RUN_TIMEOUT",
        default=165, value_type="int", category="sandbox",
        description="sandboxd 执行请求客户端超时（秒）", min_value=10, max_value=180,
    ),
    "sandbox.asset_transfer_timeout_seconds": SettingDef(
        key="sandbox.asset_transfer_timeout_seconds",
        env_name="NANOBOT_SANDBOX_ASSET_TRANSFER_TIMEOUT",
        default=600, value_type="int", category="sandbox",
        description="大资产经 Unix Socket 流式传输的超时（秒）", min_value=30, max_value=3600,
    ),
    "sandbox.asset_token_secret": SettingDef(
        key="sandbox.asset_token_secret", env_name="NANOBOT_ASSET_TOKEN_SECRET",
        default="", value_type="str", category="sandbox",
        description="资产短期下载 Token 的 HMAC 密钥（至少 32 字节）",
        sensitive=True, restart_required=True,
    ),
    "sandbox.asset_token_ttl_seconds": SettingDef(
        key="sandbox.asset_token_ttl_seconds", env_name="NANOBOT_ASSET_TOKEN_TTL_SECONDS",
        default=300, value_type="int", category="sandbox",
        description="资产下载 Token 默认有效期（秒）", min_value=60, max_value=86400,
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
        category="router", description="熔断连续失败次数(需重建tracker)", min_value=1, max_value=20,
    ),
    "model.cooldown_base_seconds": SettingDef(
        key="model.cooldown_base_seconds",
        env_name="MODEL_COOLDOWN_BASE_SECONDS",
        default=300, value_type="int",
        category="router", description="熔断基础冷却(秒,需重建tracker)", min_value=30, max_value=3600,
    ),
    "model.cooldown_max_seconds": SettingDef(
        key="model.cooldown_max_seconds",
        env_name="MODEL_COOLDOWN_MAX_SECONDS",
        default=1800, value_type="int",
        category="router", description="熔断最大冷却(秒,需重建tracker)", min_value=60, max_value=36000,
    ),
    "eval.sample_log_enabled": SettingDef(
        key="eval.sample_log_enabled", env_name="EVAL_SAMPLE_LOG_ENABLED",
        default=True, value_type="bool", category="eval",
        description="启用日志自动采样",
    ),
    "eval.sample_db_enabled": SettingDef(
        key="eval.sample_db_enabled", env_name="EVAL_SAMPLE_DB_ENABLED",
        default=True, value_type="bool", category="eval",
        description="启用 DB 自动采样",
    ),
    "eval.sample_interval_sec": SettingDef(
        key="eval.sample_interval_sec", env_name="EVAL_SAMPLE_INTERVAL_SEC",
        default=600, value_type="int", category="eval",
        description="自动采样间隔(秒)", min_value=60, max_value=3600,
    ),
    "eval.sample_limit_per_cycle": SettingDef(
        key="eval.sample_limit_per_cycle", env_name="EVAL_SAMPLE_LIMIT_PER_CYCLE",
        default=100, value_type="int", category="eval",
        description="每轮采样上限", min_value=10, max_value=500,
    ),
    "eval.log_path": SettingDef(
        key="eval.log_path", env_name="EVAL_LOG_PATH",
        default="data/nanobot.log", value_type="str", category="eval",
        description="采样日志路径",
    ),
    "memory_digest.scheduler_enabled": SettingDef(
        key="memory_digest.scheduler_enabled",
        env_name="MEMORY_DIGEST_SCHEDULER_ENABLED",
        default=True, value_type="bool",
        category="scheduler", description="启用每日记忆折叠",
    ),
    "memory_digest.schedule_hour": SettingDef(
        key="memory_digest.schedule_hour",
        env_name="MEMORY_DIGEST_SCHEDULE_HOUR",
        default=4, value_type="int",
        category="scheduler", description="每日记忆折叠小时", min_value=0, max_value=23,
    ),
}

_MODEL_ROUTE_DESCRIPTORS = list_model_route_descriptors()
for _route_descriptor in _MODEL_ROUTE_DESCRIPTORS:
    if _route_descriptor.inherits_from != "reply":
        continue
    _route_key = _route_descriptor.route_key
    _route_defaults = {
        "timeout": _route_descriptor.default_timeout_seconds,
        "temperature": _route_descriptor.default_temperature,
        "max_tokens": _route_descriptor.default_max_tokens,
    }
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}",
        SettingDef(
            key=f"model.route.{_route_key}",
            env_name="",
            default="",
            value_type="str",
            category="model",
            description=f"{_route_key} 地址（空则继承 reply）",
        ),
    )
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.model",
        SettingDef(
            key=f"model.route.{_route_key}.model",
            env_name="",
            default="",
            value_type="str",
            category="model",
            description=f"{_route_key} 模型名（空则继承 reply）",
        ),
    )
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.api_key",
        SettingDef(
            key=f"model.route.{_route_key}.api_key",
            env_name="",
            default="",
            value_type="str",
            category="model",
            description=f"{_route_key} API key",
            sensitive=True,
        ),
    )
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.provider",
        SettingDef(
            key=f"model.route.{_route_key}.provider",
            env_name="",
            default="",
            value_type="str",
            category="model",
            description=f"{_route_key} 供应商（空则继承 reply）",
        ),
    )
    for _field, _default in _route_defaults.items():
        _value_type = "float" if _field == "temperature" else "int"
        SETTING_DEFS.setdefault(
            f"model.route.{_route_key}.{_field}",
            SettingDef(
                key=f"model.route.{_route_key}.{_field}",
                env_name="",
                default=_default,
                value_type=_value_type,
                category="model",
                description=f"{_route_key} {_field}",
                min_value=0 if _field == "temperature" else (3 if _field == "timeout" else 5),
                max_value=2 if _field == "temperature" else (300 if _field == "timeout" else 65536),
            ),
        )
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.enable_thinking",
        SettingDef(
            key=f"model.route.{_route_key}.enable_thinking",
            env_name="",
            default=_route_descriptor.default_enable_thinking,
            value_type="str",
            category="model",
            description=f"{_route_key} thinking 模式: auto/true/false",
        ),
    )

for _route_descriptor in _MODEL_ROUTE_DESCRIPTORS:
    if _route_descriptor.inherits_from == "reply":
        continue
    _route_key = _route_descriptor.route_key
    _thinking_default = _route_descriptor.default_enable_thinking
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.enable_thinking",
        SettingDef(
            key=f"model.route.{_route_key}.enable_thinking",
            env_name="",
            default=_thinking_default,
            value_type="str",
            category="model",
            description=f"{_route_key} thinking 模式: auto/true/false",
        ),
    )


def _validate_model_route_setting_specs() -> None:
    """启动期核对 Descriptor 与 SettingSpec，防止默认值再次漂移。"""

    for descriptor in _MODEL_ROUTE_DESCRIPTORS:
        required_keys = {
            descriptor.model_setting_key,
            f"{descriptor.setting_prefix}.provider",
            f"{descriptor.setting_prefix}.timeout",
            f"{descriptor.setting_prefix}.temperature",
            f"{descriptor.setting_prefix}.max_tokens",
            f"{descriptor.setting_prefix}.enable_thinking",
        }
        if (
            descriptor.execution_mode
            is not None
            and descriptor.route_type != "controller"
        ):
            required_keys.update({
                descriptor.setting_prefix,
                f"{descriptor.setting_prefix}.api_key",
            })
        missing = sorted(required_keys - SETTING_DEFS.keys())
        if missing:
            raise ValueError(
                f"模型路由 {descriptor.route_key} 缺少 SettingSpec: {missing}"
            )

        expected_defaults = {
            f"{descriptor.setting_prefix}.timeout": (
                descriptor.default_timeout_seconds
            ),
            f"{descriptor.setting_prefix}.temperature": (
                descriptor.default_temperature
            ),
            f"{descriptor.setting_prefix}.max_tokens": (
                descriptor.default_max_tokens
            ),
            f"{descriptor.setting_prefix}.enable_thinking": (
                descriptor.default_enable_thinking
            ),
        }
        for key, expected in expected_defaults.items():
            actual = SETTING_DEFS[key].default
            if isinstance(expected, (int, float)) and not isinstance(
                expected,
                bool,
            ):
                matches = float(actual) == float(expected)
            else:
                matches = str(actual) == str(expected)
            if not matches:
                raise ValueError(
                    f"模型路由 {descriptor.route_key} 默认值漂移: "
                    f"{key}={actual!r}, Descriptor={expected!r}"
                )


_validate_model_route_setting_specs()


def _register_web_search_settings() -> None:
    from core.web_search.provider_catalog import env_name_for, list_provider_catalog

    for item in list_provider_catalog():
        base = f"web_search.providers.{item.id}"
        SETTING_DEFS.setdefault(
            f"{base}.enabled",
            SettingDef(
                key=f"{base}.enabled",
                env_name=env_name_for(item.id, "enabled"),
                default=item.enabled_by_default,
                value_type="bool",
                category="web_search",
                description=f"{item.name} 启用状态",
            ),
        )
        SETTING_DEFS.setdefault(
            f"{base}.api_key",
            SettingDef(
                key=f"{base}.api_key",
                env_name=env_name_for(item.id, "api_key"),
                default="",
                value_type="str",
                category="web_search",
                description=f"{item.name} API Key",
                sensitive=True,
            ),
        )
        if item.supports_base_url:
            SETTING_DEFS.setdefault(
                f"{base}.base_url",
                SettingDef(
                    key=f"{base}.base_url",
                    env_name=env_name_for(item.id, "base_url"),
                    default=item.default_base_url,
                    value_type="str",
                    category="web_search",
                    description=f"{item.name} Base URL",
                ),
            )
        SETTING_DEFS.setdefault(
            f"{base}.priority",
            SettingDef(
                key=f"{base}.priority",
                env_name=env_name_for(item.id, "priority"),
                default=item.default_priority,
                value_type="int",
                category="web_search",
                description=f"{item.name} 自动搜索优先级（数值越小越优先）",
                min_value=0,
                max_value=1_000_000,
            ),
        )


_register_web_search_settings()

# 动态 provider/web-search 设置全部注册完毕后再冻结校验。导入期失败即阻止服务
# 带着不一致或可被数据库覆盖的安全不变量启动。
validate_setting_catalog(SETTING_DEFS)
SETTING_DEFS = MappingProxyType(dict(SETTING_DEFS))

__all__ = [
    "LEGACY_SETTING_ALIASES",
    "SETTING_DEFS",
    "SettingDef",
    "SettingSpec",
    "canonical_setting_key",
]
