"""Provider 响应的纯归一化逻辑。"""

from __future__ import annotations

import re


_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_PATTERN = re.compile(r"<think>.*", re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """迭代去除模型的 ``<think>`` 块（含未闭合标签）。"""

    for _ in range(5):
        previous = text
        text = _THINK_PATTERN.sub("", text).strip()
        if text == previous:
            break
    return _THINK_OPEN_PATTERN.sub("", text).strip()
