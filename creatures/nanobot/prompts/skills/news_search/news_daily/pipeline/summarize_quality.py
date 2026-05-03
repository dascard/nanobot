"""Quality 模式 LLM 摘要——基于 Light Evidence Cards 生成日报 JSON。"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger("nanobot.news_daily.quality")

QUALITY_SYSTEM_PROMPT = """你是 AI/科技日报编辑。只能基于给定的候选新闻卡片写日报。

硬规则：
1. 不得引入卡片之外的事实。
2. top_story/highlight/watchlist 必须绑定 source_ids。
3. 不要补全未知信息；没价格/API/benchmark 就写入 missing_info。
4. 不要写"行业持续发展""值得关注"等空话，除非后面有具体原因。
5. 不要把社区/媒体来源写成官方确认。
6. 不要 Markdown，不要 HTML，只输出 JSON。
7. 如果多条新闻重复，合并成一条 highlight，保留多个 source_ids。
8. 没有足够信息宁愿少写，不要编。

输出严格 JSON：
{
  "title": "≤20字",
  "subtitle": "≤30字",
  "verdict": "≤60字",
  "top_story": {
    "title": "头条标题", "what_happened": "≤100字", "why_it_matters": "≤80字",
    "source_ids": [1,2], "confidence": "high/medium"
  },
  "highlights": [
    {"label": "分类", "text": "≤100字", "source_ids": [1], "importance": 1-5}
  ],
  "watchlist": [{"text": "...", "reason": "...", "source_ids": [1]}],
  "missing_info": ["缺失信息"],
  "closing": "≤40字"
}"""


def build_quality_prompt(cards: list[dict]) -> str:
    card_texts = []
    for c in cards:
        text = f"""### 来源 #{c['source_id']}
标题: {c.get('title', '')}
来源: {c.get('source_name', '')}
域名: {c.get('domain', '')}
时间: {c.get('published_at', 'unknown')}
可信度: {c.get('trust', 0):.0%} ({c.get('confidence', 'medium')})
分类: {c.get('category', '未分类')}
实体: {', '.join(c.get('entities', []))}
数字: {', '.join(c.get('numbers', []))}
断言: {'; '.join(c.get('claims', []))}
摘要: {c.get('summary', '')}
相关内容: {c.get('related_text', '')}
---"""
        card_texts.append(text)

    return f"""## 候选新闻卡片 ({len(cards)} 条)

{chr(10).join(card_texts)}

## 要求
生成 3-5 条 highlights，包含 watchlist。
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
                manual_model="deepseek-v4-flash", max_tokens=1600,
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
            return fallback

        parsed = json.loads(_extract_json(raw))
        return parsed if isinstance(parsed, dict) else fallback

    except Exception as e:
        logger.warning("[quality] LLM summary failed: %s", e)
        return fallback
