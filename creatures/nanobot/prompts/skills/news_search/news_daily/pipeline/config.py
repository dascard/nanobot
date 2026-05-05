"""管线配置——阈值、配额、权重。"""

DAILY_FRESHNESS_HOURS = 48
TOP_STORY_FRESHNESS_HOURS = 36

MAX_FINAL_CLUSTERS = 8
MAX_ARTICLES_PER_DOMAIN_FINAL = 2
MAX_CLUSTERS_PER_DOMAIN_FINAL = 2
MAX_SAME_ENTITY_CLUSTERS_DAILY = 1

CLUSTER_SIM_THRESHOLD = 0.48

OFFICIAL_SOURCES = {
    "openai_news", "anthropic_news", "google_deepmind_news",
    "mistral_news", "deepseek_news", "qwen_blog",
    "kimi_blog", "xai_news", "nvidia_blog", "cohere_blog",
    "meta_ai_blog",
}

SOURCE_QUALITY = {
    "core_provider": 1.0, "core_platform": 0.9,
    "ai_media": 0.75, "curated": 0.55,
    "research": 0.55, "community": 0.35, "unknown": 0.3,
}

EVENT_TYPE_WEIGHT = {
    "model_release": 0.9, "benchmark": 0.6, "funding": 0.5,
    "product": 0.65, "policy": 0.75, "research": 0.55, "incident": 0.8,
}

MAJOR_ENTITIES = {"openai", "anthropic", "google", "deepseek", "qwen", "kimi", "meta"}

STOP_WORDS = {"发布", "宣布", "推出", "上线", "开源", "模型", "AI", "正式", "全新", "最新", "重磅"}

TOPIC_KEYWORDS = {
    "model_release": ["发布", "推出", "release", "launch", "open-source", "开源"],
    "benchmark": ["benchmark", "评测", "swe-bench", "mmlu"],
    "funding": ["融资", "funding", "raised", "valuation"],
    "product": ["app", "agent", "api", "platform", "browser"],
    "policy": ["regulation", "policy", "法案", "监管"],
    "research": ["paper", "论文", "arxiv", "research"],
    "incident": ["outage", "leak", "breach", "security", "故障", "泄露"],
}

KNOWN_ENTITIES = {
    "kimi": ["kimi", "moonshot", "月之暗面"],
    "openai": ["openai", "chatgpt", "gpt"],
    "anthropic": ["anthropic", "claude"],
    "google": ["google", "deepmind", "gemini"],
    "deepseek": ["deepseek", "深度求索"],
    "qwen": ["qwen", "通义千问", "alibaba"],
    "mistral": ["mistral"],
    "meta": ["meta", "llama"],
    "nvidia": ["nvidia"],
    "xai": ["xai", "grok"],
}
