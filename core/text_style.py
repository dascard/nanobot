"""聊天回复标点风格清理——问号保留，其余标点转换行。"""

import re

PUNCT_TO_NEWLINE = "，,。．；;：:！!、"
QUOTE_PARENS = "（）()「」『』“”‘’《》"


def normalize_chat_reply_style(text: str) -> str:
    if not text:
        return text

    # 不处理代码块
    if "```" in text:
        return text

    # 跳过 URL（避免误伤 http://... 中的标点）
    if "http://" in text or "https://" in text:
        return text

    # 跳过 JSON / 结构化输出
    s = text.strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return text

    for ch in PUNCT_TO_NEWLINE:
        text = text.replace(ch, "\n")
    for ch in QUOTE_PARENS:
        text = text.replace(ch, "")

    # 中文问号 → 英文问号
    text = text.replace("？", "?")

    # 恢复组合标点 ?! / !?
    text = text.replace("?\n", "?!")
    text = text.replace("\n?", "!?")

    # 清理多余换行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()
