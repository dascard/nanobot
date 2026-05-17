"""路由元数据——route type / provider alias / url 归一化。

后端、WebUI、诊断接口共享的集中定义，避免多文件重复。
"""

# ── 路由元数据 ──
ROUTE_METADATA: dict[str, dict] = {
    "reply":    {"type": "controller", "label": "主回复模型"},
    "fast":     {"type": "controller", "label": "快速模型（预留）"},
    "smart":    {"type": "controller", "label": "智能模型（预留）"},
    "timing_gate":        {"type": "classifier", "label": "TimingGate 分类器"},
    "private_decision":   {"type": "classifier", "label": "私聊决策分类器"},
    "classifier_legacy":  {"type": "classifier", "label": "旧分类器"},
    "sticker_describe":   {"type": "vision",     "label": "表情包打标"},
}


def route_type_for(route_key: str) -> str:
    """返回 route_key 对应的 route_type，未知时返回 'unknown'。"""
    return ROUTE_METADATA.get(route_key, {}).get("type", "unknown")


def route_label_for(route_key: str) -> str:
    """返回 route_key 对应的中文标签。"""
    return ROUTE_METADATA.get(route_key, {}).get("label", route_key)


def route_capability_for(route_key: str) -> str | None:
    """从 route_type 推断模型能力（不再从 provider 名推断）。"""
    t = route_type_for(route_key)
    if t == "controller":
        return "chat"
    elif t == "classifier":
        return "classifier"
    elif t == "vision":
        return "vision"
    return None


# ── Provider 别名（旧名 → canonical） ──
PROVIDER_ALIASES: dict[str, str] = {
    "local_qwen": "local_llama",
    "vision_qwen": "local_vision",
}
_DEPRECATED_PROVIDERS: set[str] = set(PROVIDER_ALIASES.keys())


def canonical_provider_id(provider_id: str) -> str:
    """把旧 provider 名映射到 canonical 名。"""
    pid = (provider_id or "").strip()
    if not pid:
        return pid
    return PROVIDER_ALIASES.get(pid, pid)


def is_deprecated_provider(provider_id: str) -> bool:
    return (provider_id or "").strip() in _DEPRECATED_PROVIDERS


# ── 迁移期内置 provider ──
BUILTIN_PROVIDERS: list[str] = ["newapi", "local_llama"]


# ── URL 归一化 ──
def normalize_base_url(url: str) -> str:
    """归一化 base_url 用于比较是否指向同一服务。"""
    u = (url or "").strip().rstrip("/")
    # 统一 /v1 后缀
    if u.endswith("/v1"):
        u = u.removesuffix("/v1")
    return u.lower()
