"""LLM 消息组装的纯数据变换。"""

from __future__ import annotations


def format_openai_messages(
    system_prompt: str,
    persona: str,
    context: str,
    query: str,
) -> list[dict[str, str]]:
    """把旧画像/历史输入转换成 OpenAI-compatible 消息列表。"""

    full_system = f"{system_prompt}\n\n[USER PERSONA]\n{persona}"
    return [
        {"role": "system", "content": full_system},
        {
            "role": "user",
            "content": f"[HISTORY]\n{context}\n\n[USER QUERY]\n{query}",
        },
    ]


__all__ = ["format_openai_messages"]
