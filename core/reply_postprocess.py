"""回复后处理——去句尾标点 + 长句拆短。"""

import re

_END_PUNCT = "。！？!?；;，,…"
_KEEP_PATTERNS = (
    re.compile(r"```[\s\S]*```$"),
    re.compile(r"https?://\S+$"),
    re.compile(r"[\w./-]+\.\w+\.\w+$"),
)


def strip_chat_end_punct(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    if any(p.search(text) for p in _KEEP_PATTERNS):
        return text
    return text.rstrip(_END_PUNCT).strip()


def split_short_messages(text: str, *, max_len: int = 45, max_parts: int = 3) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if "```" in text or text.lstrip().startswith("<"):
        return [text]
    if len(text) <= max_len:
        return [strip_chat_end_punct(text)]

    raw_parts = re.split(r"[。！？!?\n]+", text)
    parts = [strip_chat_end_punct(p) for p in raw_parts if p.strip()]
    if len(parts) <= 1:
        return [strip_chat_end_punct(text)]

    merged, buf = [], ""
    for part in parts:
        if not buf:
            buf = part
        elif len(buf) + len(part) + 1 <= max_len:
            buf += "，" + part
        else:
            merged.append(strip_chat_end_punct(buf))
            buf = part
    if buf:
        merged.append(strip_chat_end_punct(buf))
    return merged[:max_parts]


def postprocess_reply(answer: str, *, effort: str = "short") -> str:
    text = (answer or "").strip()
    if not text:
        return text
    if text.lstrip().startswith("<") or "```" in text:
        return answer
    if effort in ("casual", "short"):
        parts = split_short_messages(text, max_len=45, max_parts=3)
        return "\n".join(parts)
    return strip_chat_end_punct(text)
