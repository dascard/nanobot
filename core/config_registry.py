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
    dangerous: bool = False


SETTING_DEFS: dict[str, SettingDef] = {
    "database.url": SettingDef(
        key="database.url", env_name="DATABASE_URL",
        default="sqlite:///./data/nanobot.db", value_type="str",
        category="system", description="数据库连接地址", restart_required=True, dangerous=True,
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
    "proactive_outreach.enabled": SettingDef(
        key="proactive_outreach.enabled",
        env_name="PROACTIVE_OUTREACH_ENABLED",
        default=False,
        value_type="bool",
        category="proactive",
        description="主动情感外呼总开关；单用户自用场景下为纯布尔开关",
        restart_required=True,
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
    ),
    "classifier.timeout": SettingDef(
        key="classifier.timeout", env_name="CLASSIFIER_TIMEOUT",
        default=15.0, value_type="float",
        category="classifier", description="TimingGate/分类器超时(秒)",
        min_value=1, max_value=120,
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
        key="model.session_summary", env_name="",
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
        default=1200, value_type="int",
        category="model", description="近期摘要最大输出 tokens", min_value=64, max_value=8000,
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
        default=1800, value_type="int",
        category="model", description="长期摘要最大输出 tokens", min_value=128, max_value=12000,
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
        category="tool", description="自动降档轻量预设允许的工具列表（JSON数组）",
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

_OUTREACH_ROUTE_DEFAULTS = {
    "timing_proactive": {"timeout": 30, "temperature": 0.0, "max_tokens": 512},
    "outreach_extract": {"timeout": 30, "temperature": 0.0, "max_tokens": 512},
    "outreach_judge": {"timeout": 45, "temperature": 0.0, "max_tokens": 768},
    "outreach_generate": {"timeout": 60, "temperature": 0.7, "max_tokens": 1024},
}

for _route_key, _route_defaults in _OUTREACH_ROUTE_DEFAULTS.items():
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
                max_value=2 if _field == "temperature" else (300 if _field == "timeout" else 8192),
            ),
        )
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.enable_thinking",
        SettingDef(
            key=f"model.route.{_route_key}.enable_thinking",
            env_name="",
            default="false",
            value_type="str",
            category="model",
            description=f"{_route_key} thinking 模式: auto/true/false",
        ),
    )

for _route_key in (
    "reply",
    "fast",
    "smart",
    "session_summary",
    "memory_digest",
    "timing_gate",
    "private_decision",
    "classifier_legacy",
):
    SETTING_DEFS.setdefault(
        f"model.route.{_route_key}.enable_thinking",
        SettingDef(
            key=f"model.route.{_route_key}.enable_thinking",
            env_name="",
            default="auto",
            value_type="str",
            category="model",
            description=f"{_route_key} thinking 模式: auto/true/false",
        ),
    )
# sticker_describe 是视觉/JSON 输出任务，默认禁用 thinking 减少不必要 reasoning
SETTING_DEFS.setdefault(
    "model.route.sticker_describe.enable_thinking",
    SettingDef(
        key="model.route.sticker_describe.enable_thinking",
        env_name="",
        default="false",
        value_type="str",
        category="model",
        description="sticker_describe thinking 模式: auto/true/false（视觉摘要默认禁用）",
    ),
)


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
