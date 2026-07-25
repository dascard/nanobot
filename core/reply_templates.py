"""Casual 场景模板回复——只消费已验证的稳定 intent。"""

import random

CASUAL_TEMPLATES = {
    "identity_probe": ["你猜", "我啊", "别查户口", "问这个干嘛"],
    "check_capability": ["你先说事", "看情况", "能看就看"],
    "is_bot_probe": ["问这个干嘛", "别查户口", "你猜"],
    "personal_probe": ["别查户口", "问这个干嘛", "不重要"],
    "missing_material": ["发", "日志呢", "先贴出来"],
    "too_broad": ["太大了，挑一块", "你先说目标"],
    "uncertain_debug": ["不一定，先看日志", "先别急着怪模型"],
    "daily_request_casual": ["这个有点麻烦", "晚点看", "你真要看"],
    "unclear_request": ["啥意思", "你先说清楚", "没看懂"],
    "image_no_context": ["这图要看啥", "你想问哪块"],
}


def pick_casual_reply(intent: str) -> str:
    options = CASUAL_TEMPLATES.get(intent, [])
    if not options:
        return ""
    return random.choice(options)


def get_casual_reply(intent: str) -> str:
    return pick_casual_reply(str(intent or "").strip())
