"""聊天回复标点风格清理——问号保留，其余标点转换行。"""

import re


def normalize_chat_reply_style(text: str) -> str:
    if not text:
        return text

    # 不处理代码块
    if "```" in text:
        return text

    # 跳过 URL
    if "http://" in text or "https://" in text:
        return text

    # 跳过 JSON / 结构化输出
    s = text.strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return text

    # 全角标点 → 半角
    text = text.replace("？", "?")
    text = text.replace("！", "!")

    # 用占位符保护组合标点 ?! / !?
    placeholders = {"?!": "__QEXCL__", "!?": "__EXCLQ__"}
    for k, v in placeholders.items():
        text = text.replace(k, v)

    for ch in "，,。．；;：:!、":
        text = text.replace(ch, "\n")
    for ch in "（）()「」『』“”‘’《》":
        text = text.replace(ch, "")

    for k, v in placeholders.items():
        text = text.replace(v, k)

    # 清理多余换行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()
