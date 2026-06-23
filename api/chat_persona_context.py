from typing import Any

from core.context_builder import sanitize_prompt_text


DEFAULT_MAX_PERSONA_CHARS = 1600


def format_persona_for_prompt(
    persona_data: dict,
    max_chars: int = DEFAULT_MAX_PERSONA_CHARS,
) -> str:
    """把画像 JSON 压成给主回复模型看的文本，避免注入半截 JSON。"""
    if not isinstance(persona_data, dict) or not persona_data:
        return ""

    parts: list[str] = []

    summary = str(persona_data.get("persona_summary") or persona_data.get("summary") or "").strip()
    if summary:
        parts.append(f"【用户画像】{summary}")

    resp_style = str(persona_data.get("response_style") or persona_data.get("communication_style") or "").strip()
    if resp_style:
        parts.append(f"【回复要求】{resp_style}")

    traits = persona_data.get("traits")
    if isinstance(traits, list) and traits:
        parts.append(f"【特质】{', '.join(str(t) for t in traits[:5] if t)}")

    prefs = persona_data.get("preferences")
    if isinstance(prefs, list) and prefs:
        parts.append(f"【偏好】{' | '.join(str(p) for p in prefs[:4] if p)}")

    pain = str(persona_data.get("pain_points") or "").strip()
    if pain:
        parts.append(f"【雷区】{pain[:300]}")

    identity = persona_data.get("identity")
    if isinstance(identity, dict) and identity:
        ident_parts = [f"{k}: {v}" for k, v in identity.items() if v and str(v).strip()]
        if ident_parts:
            parts.append(f"【身份】{' | '.join(ident_parts)}")

    domains = persona_data.get("domain_profiles", {})
    if isinstance(domains, dict) and domains:
        def _domain_rank(item: tuple) -> tuple[int, int]:
            info = item[1]
            if not isinstance(info, dict):
                return (0, 0)
            conf_score = {"high": 3, "medium": 2, "low": 1}.get(
                str(info.get("confidence", "low")).lower(), 0
            )
            count = int(info.get("interaction_count", 0) or 0)
            return (conf_score, count)

        ranked = sorted(domains.items(), key=_domain_rank, reverse=True)
        domain_lines = []
        for domain, info in ranked[:3]:
            if not isinstance(info, dict):
                continue
            conf = str(info.get("confidence", "?"))[:5]
            desc = str(info.get("summary") or info.get("description") or "").strip()
            if desc:
                domain_lines.append(f"  [{conf}] {domain}: {desc[:240]}")
        if domain_lines:
            parts.append("【关注领域】\n" + "\n".join(domain_lines))

    facts = persona_data.get("facts")
    if isinstance(facts, list) and facts:
        def _fact_rank(fact: Any) -> tuple[int, int]:
            if not isinstance(fact, dict):
                return (0, 0)
            conf_text = str(fact.get("confidence") or "").lower()
            conf_score = {
                "确认": 4, "高": 4, "high": 4,
                "可能": 2, "中": 2, "medium": 2,
                "低": 1, "low": 1,
            }.get(conf_text, 0)
            try:
                evidence = int(fact.get("evidence") or fact.get("evidence_count") or 0)
            except (TypeError, ValueError):
                evidence = 0
            return (conf_score, evidence)

        fact_lines = []
        for fact in sorted([f for f in facts if isinstance(f, dict)], key=_fact_rank, reverse=True)[:10]:
            content = str(fact.get("content") or "").strip()
            if not content:
                continue
            domain = str(fact.get("domain") or fact.get("domain_primary") or "").strip()
            fact_type = str(fact.get("type") or fact.get("fact_type") or "").strip()
            confidence = str(fact.get("confidence") or "").strip()
            evidence = fact.get("evidence", fact.get("evidence_count", ""))
            tags = " ".join(x for x in [
                f"[{confidence}]" if confidence else "",
                f"[证据{evidence}]" if evidence not in ("", None) else "",
                domain,
                fact_type,
            ] if x)
            prefix = f"{tags}: " if tags else ""
            fact_lines.append(f"- {prefix}{content[:220]}")
        if fact_lines:
            parts.append("【稳定画像事实】\n" + "\n".join(fact_lines))

    if not parts:
        scalar_items = []
        for key, value in persona_data.items():
            if isinstance(value, (str, int, float, bool)) and str(value).strip():
                scalar_items.append(f"{key}: {str(value)[:120]}")
        if scalar_items:
            parts.append("【用户画像】" + " | ".join(scalar_items[:6]))

    return sanitize_prompt_text("\n\n".join(parts), max_chars)
