"""Casual 场景模板回复——不需要进主模型。"""

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


def get_casual_reply(text: str, is_superuser: bool = False) -> str | None:
    if is_superuser:
        return None
    checks = [
        (("你是谁", "你是啥", "你是？", "你是?", "你叫啥", "你叫什么"), "identity_probe"),
        (("你能干嘛", "你能做什么", "你会什么", "有什么功能"), "check_capability"),
        (("机器人", "bot", "Bot", "是不是人"), "is_bot_probe"),
        (("哪里人", "多大", "男的女的", "真人吗", "在哪", "住哪"), "personal_probe"),
        (("帮我看", "帮我查", "帮我看下", "帮我看报错", "帮我看代码"), "missing_material"),
        (("整个项目", "全部代码", "完整方案", "全部改", "全改", "帮我审"), "too_broad"),
    ]
    t = text.strip()
    for keywords, intent in checks:
        if any(k in t for k in keywords) and len(t) < 50:
            return pick_casual_reply(intent)
    return None
