import os
import re
import json
import time
import requests
import logging
from typing import List

logger = logging.getLogger("nanobot.compact")

COMPACT_BASE_URL = os.environ.get("COMPACT_BASE_URL", "https://api.deepseek.com/v1")
COMPACT_API_KEY = os.environ.get("COMPACT_API_KEY", "")
COMPACT_MODEL = os.environ.get("COMPACT_MODEL", "deepseek-chat")

MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
Please provide your summary following this structure, ensuring precision and thoroughness in your response:

<analysis>
[Your thought process]
</analysis>

<summary>
1. Primary Request and Intent:
2. Key Technical Concepts:
3. Files and Code Sections:
4. Errors and fixes:
5. Problem Solving:
6. All user messages:
7. Pending Tasks:
8. Current Work:
</summary>
"""

def strip_media_blocks(text: str) -> str:
    """去除图片与多媒体占位符，防止触发大模型最大 Token 限制。"""
    text = re.sub(r'\[image\]', '[image placeholder]', text, flags=re.IGNORECASE)
    text = re.sub(r'\[document\]', '[document placeholder]', text, flags=re.IGNORECASE)
    text = re.sub(r'data:image\/[a-zA-Z]*;base64,[^\s"\'\]\)]+', '[image data stripped]', text)
    return text

def format_compact_summary(summary_text: str) -> str:
    """提取纯粹的 summary，剥离占据极多 Token 的 analysis 思维链推演。"""
    formatted = re.sub(r'<analysis>[\s\S]*?<\/analysis>', '', summary_text, flags=re.IGNORECASE)
    match = re.search(r'<summary>([\s\S]*?)<\/summary>', formatted, flags=re.IGNORECASE)
    if match:
        return f"Summary:\n{match.group(1).strip()}"
    return formatted.strip()

def truncate_head_for_ptl_retry(history_lines: List[str]) -> List[str]:
    """Circuit Breaker fallback: 截除 20% 最老的消息组。"""
    drop_count = max(1, int(len(history_lines) * 0.2))
    return history_lines[drop_count:]

def call_compaction_llm(context_text: str) -> str:
    """通用请求大模型执行折叠。"""
    if not COMPACT_API_KEY:
        raise ValueError("COMPACT_API_KEY is not set. Autocompact requires an LLM provider.")
    
    url = f"{COMPACT_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {COMPACT_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": COMPACT_MODEL,
        "messages": [
            {"role": "system", "content": NO_TOOLS_PREAMBLE},
            {"role": "user", "content": f"Please summarize the following conversation history:\n\n{context_text}"}
        ],
        "temperature": 0.3
    }

    try:
        from core.llm_trace_context import get_llm_trace_vars
        from core.tracing import LLMRequestTracer

        trace_id, run_id, _ = get_llm_trace_vars()
        LLMRequestTracer.record_request(
            trace_id=trace_id,
            run_id=run_id,
            source="compaction",
            provider="compaction",
            model=COMPACT_MODEL,
            url=url,
            method="POST",
            headers=headers,
            request=payload,
            status="created",
        )
    except Exception:
        pass
    
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    body = resp.json()
    return body.get("choices", [{}])[0].get("message", {}).get("content", "")

def run_autocompact_circuit_breaker(context_lines: List[str], max_length: int = 4000) -> str:
    """
    当内容超过 max_length 时，触发真正的 Autocompact，包含 Circuit Breaker（最大失败次数阈值3）。
    如果一切都失败，则强行执行首部截断。
    """
    current_lines = list(context_lines)
    failures = 0
    
    while True:
        # Step 1: Strip Media Blocks
        stripped_lines = [strip_media_blocks(line) for line in current_lines]
        context_text = "\n\n".join(stripped_lines)
        
        if len(context_text) <= max_length:
            return context_text
            
        if not COMPACT_API_KEY:
            logger.warning("No COMPACT_API_KEY provided. Falling back to hot memory hard truncation.")
            return "[System: Older context truncated...]\n" + context_text[-max_length:]
            
        # Step 2: Try Compact
        try:
            logger.info(f"Triggering AutoCompact on {len(context_text)} chars context...")
            raw_summary = call_compaction_llm(context_text)
            
            # Step 3: Strip <analysis> tagging
            clean_summary = format_compact_summary(raw_summary)
            return f"[Compact Summary of older history]\n{clean_summary}"
            
        except Exception as e:
            failures += 1
            logger.error(f"AutoCompact attempt {failures} failed: {e}")
            if failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
                logger.error("Tripped circuit breaker: MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES reached. Forcing PTL fallback.")
                # Circuit breaker drop
                current_lines = truncate_head_for_ptl_retry(current_lines)
                # BUG-07 FIX: Reset failures to allow retry after truncation
                failures = 0
                if not current_lines:
                    return ""
                continue
            
            # small delay before retry
            time.sleep(1)
