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
# 注意：vision_qwen 的 canonical 名取决于端点是否相同，见 canonical_provider_id()
PROVIDER_ALIASES: dict[str, str] = {
    "local_qwen": "local_llama",
    # vision_qwen 不在此处硬编码，由 canonical_provider_id() 动态决定
}
_DEPRECATED_PROVIDERS: set[str] = {"local_qwen", "vision_qwen"}


# 缓存 endpoint 比较结果（进程生命周期不变）
_cached_vision_canonical: str | None = None


def _resolve_vision_alias() -> str:
    """vision_qwen 的 canonical 名：同 endpoint 则 local_llama，否则 local_vision。"""
    global _cached_vision_canonical
    if _cached_vision_canonical is not None:
        return _cached_vision_canonical
    try:
        from config import CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL
        classifier = normalize_base_url(str(CLASSIFIER_API_URL or ""))
        vision = normalize_base_url(str(IMAGE_SUMMARY_API_URL or ""))
        _cached_vision_canonical = "local_llama" if (vision and vision == classifier) else "local_vision"
    except Exception:
        _cached_vision_canonical = "local_vision"
    return _cached_vision_canonical


def canonical_provider_id(provider_id: str) -> str:
    """把旧 provider 名映射到 canonical 名。"""
    pid = (provider_id or "").strip()
    if not pid:
        return pid
    mapped = PROVIDER_ALIASES.get(pid)
    if mapped is not None:
        return mapped
    if pid == "vision_qwen":
        return _resolve_vision_alias()
    return pid


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
