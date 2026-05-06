"""Quality 模式 LLM 摘要——基于 Light Evidence Cards 生成日报 JSON。"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger("nanobot.news_daily.quality")

QUALITY_SYSTEM_PROMPT = """你是 AI/科技日报编辑。只能基于给定的候选新闻卡片写日报。

硬规则：
1. 不得引入卡片之外的事实。
2. top_story/highlight/watchlist/details 必须绑定 source_ids。
3. 不要补全未知信息；没价格/API/benchmark 就写入 missing_info。
4. 不要写"行业持续发展""值得关注"等空话，除非后面有具体原因。
5. 不要把社区/媒体来源写成官方确认。
6. 不要 Markdown，不要 HTML，只输出 JSON。
7. 如果多条新闻重复，合并成一条 highlight，保留多个 source_ids。
8. 没有足够信息宁愿少写，不要编。
9. details 必须包含 known（已知2-3点）、unknown（缺失0-2点）、impact（一句话影响）。
10. 如果卡片没有足够细节，就明确写"信息不足"，不要扩写。

输出严格 JSON：
{
  "title": "≤20字",
  "subtitle": "≤30字",
  "verdict": "≤90字",
  "top_story": {
    "title": "头条标题", "what_happened": "≤160字", "why_it_matters": "≤100字",
    "source_ids": [1,2], "confidence": "high/medium"
  },
  "highlights": [
    {"label": "分类", "text": "100-150字，写清楚什么事+为什么重要+对谁有影响，不能只写标题", "source_ids": [1], "importance": 1-5}
  ],
  "details": [
    {"title": "事件标题", "known": ["已知事实"], "unknown": ["缺失信息"], "impact": "影响一句话", "source_labels": ["来源名"]}
  ],
  "watchlist": [{"text": "...", "reason": "...", "source_ids": [1]}],
  "missing_info": ["缺失信息"],
  "closing": "≤40字"
}"""


def build_quality_prompt(cards: list[dict]) -> str:
    card_texts = []
    for c in cards:
        detail = c.get('detail_text', '')
        known = '\n  - '.join(c.get('known_facts', [])) or '无'
        parts = [
            f"### 来源 #{c['source_id']}",
            f"标题: {c.get('title', '')}",
            f"来源: {c.get('source_name', '')} (组: {c.get('source_group', 'curated')})",
            f"域名: {c.get('domain', '')}",
            f"时间: {c.get('published_at', 'unknown')}",
            f"可信度: {c.get('trust', 0):.0%} ({c.get('confidence', 'medium')})",
            f"分类: {c.get('category', '未分类')}",
            f"实体: {', '.join(c.get('entities', []))}",
            f"数字: {', '.join(c.get('numbers', []))}",
            f"断言: {'; '.join(c.get('claims', []))}",
            f"摘要: {c.get('summary', '')}",
        ]
        if detail:
            parts.append(f"详情正文:\n  {detail}")
            parts.append(f"已知事实:\n  - {known}")
        parts.append(f"影响提示: {c.get('why_it_matters_hint', '')}")
        parts.append("---")
        card_texts.append("\n".join(parts))

    return f"""## 候选新闻卡片 ({len(cards)} 条)

{chr(10).join(card_texts)}

## 要求
生成 6-8 条 highlights、2-3 条 details、1-2 条 watchlist。
每条 highlight 100-150字，必须写清楚：什么事、为什么重要、对谁有影响。要像新闻导语一样有信息量，不能只写标题。
每条 detail 必须有 known（已知信息2-3点）、unknown（缺失信息0-2点）、impact（一句话影响）。
details 的 source_labels 使用卡片中的 "来源名（组）" 格式。
只输出 JSON，第一个字符必须是 {{，最后一个必须是 }}。"""


def _extract_json(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw).strip()
        raw = re.sub(r"```\s*$", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    return m.group(0) if m else raw


def summarize_quality(cards: list[dict], fallback: dict) -> dict:
    """调用 LLM 生成 quality 日报 JSON。失败返回 fallback。"""
    prompt = build_quality_prompt(cards)

    try:
        from clients.new_api_client import NewAPIClient
        from config import NEW_API_KEY, NEW_API_BASE_URL
        import asyncio

        async def _call():
            client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL, timeout=20)
            resp = await client.chat_completion(
                messages=[
                    {"role": "system", "content": QUALITY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                model_tier="fast", temperature=0.1,
                manual_model="deepseek-v4-flash", max_tokens=3200,
            )
            if isinstance(resp, dict) and "choices" in resp:
                return resp["choices"][0]["message"]["content"]
            return ""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                raw = pool.submit(asyncio.run, _call()).result()
        else:
            raw = asyncio.run(_call())

        if not raw:
            logger.warning("[quality] LLM returned empty, using fallback"); return fallback

        parsed = json.loads(_extract_json(raw))
        logger.info("[quality] LLM summary success chars=%d", len(raw)); parsed["_quality_source"] = "llm"; return parsed if isinstance(parsed, dict) else fallback

    except Exception as e:
        logger.warning("[quality] LLM summary failed: %s", e)
        return fallback
