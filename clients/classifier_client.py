"""
Private chat classifier guardrail — 4-layer defense.

L1: Input sanitization (regex injection detection)
L2: Qwen model call (llama.cpp server)
L3: Output validation (strict format)
L4: Timeout (5s → treated as injection)
"""

import json
import logging
import re
import urllib.request

from config import (
    CLASSIFIER_API_URL,
    CLASSIFIER_TIMEOUT,
    GUARDRAIL_INJECTION_PATTERNS,
)

logger = logging.getLogger("nanobot.classifier")

# Pattern for Qwen output validation: 是/否 + comma + number (optional negative)
OUTPUT_PATTERN = re.compile(r"^(是|否)[,，](-?\d+)$")

# Pattern to strip think/thought blocks from Qwen response
THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)

# Control characters to strip (exclude \n, \t, \r)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Guardrail:
    """4-layer guardrail for private message classification."""

    def __init__(self):
        self._injection_regexes = [
            re.compile(p) for p in GUARDRAIL_INJECTION_PATTERNS
        ]
        self._system_prompt = (
            "你是消息过滤器。判断私聊消息是否需要回复，及复杂度。\n\n"
            "需要回复输出: 是,数字\n"
            "不需要回复输出: 否,数字\n\n"
            "数字=复杂度 1-10（1你好谢谢/5普通/9很难）\n"
            "纯链接/密钥/文件路径无对话文字 → 否\n\n"
            "示例: 你好 → 是,1  |  sk-abc123 → 否,0  |  帮我写代码 → 是,6\n\n"
            "直接输出。禁止思考推理。"
        )

    # ── L1: Input Sanitization ──

    def _sanitize_input(self, message: str) -> bool:
        """Normalize input and detect prompt injection patterns.

        Returns True if an injection pattern is found (message must be rejected).
        """
        # Normalize newlines
        sanitized = message.replace("\r\n", "\n").replace("\r", "\n")
        # Strip control characters
        sanitized = CONTROL_CHAR_PATTERN.sub("", sanitized)

        for regex in self._injection_regexes:
            if regex.search(sanitized):
                logger.warning("Injection pattern matched: %s", regex.pattern)
                return True
        return False

    # ── L2: Qwen Call ──

    def _call_qwen(self, message: str) -> str:
        """Call Qwen model via llama.cpp API (synchronous).

        Returns the cleaned response text with <think> blocks stripped.
        """
        payload = {
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": message},
            ],
            "max_tokens": 30,
            "temperature": 0,
        }
        data = json.dumps(payload).encode("utf-8")
        url = f"{CLASSIFIER_API_URL.rstrip('/')}/chat/completions"

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response = urllib.request.urlopen(req, timeout=CLASSIFIER_TIMEOUT)
        body = json.loads(response.read().decode("utf-8"))

        content = body["choices"][0]["message"]["content"]

        # Strip think/thought blocks that Qwen may produce
        content = THINK_PATTERN.sub("", content).strip()
        return content

    # ── L3: Output Validation ──

    def _validate_output(self, text: str) -> tuple:
        """Validate and parse Qwen output.

        Returns (is_valid, type_str, complexity):
          is_valid: whether the output matches the expected format.
          type_str: "是" or "否" (empty if invalid).
          complexity: parsed complexity, clamped to [1, 10].
                      Forced to 0 when type="否" and raw_complexity > 2
                      (model is confused).
        """
        match = OUTPUT_PATTERN.match(text.strip())
        if not match:
            return (False, "", 0)

        type_str = match.group(1)
        complexity = int(match.group(2))

        # Clamp complexity to [1, 10]
        if complexity > 10:
            complexity = 10
        elif complexity < 1:
            complexity = 1

        # If model says "no" with high complexity, it's confused -> treat as silent
        if type_str == "否" and complexity > 2:
            return (True, "否", 0)

        return (True, type_str, complexity)

    # ── Public API ──

    def classify(self, message: str) -> dict:
        """Classify a private chat message.

        Returns dict with:
          status: "reply" | "silent" | "injection"
          complexity: int (0 for silent/injection, 1-10 for reply)
        """
        # L1: Input sanitization
        if self._sanitize_input(message):
            return {"status": "injection", "complexity": 0}

        # L2 + L4: Call Qwen (L4 = timeout handled by urlopen)
        try:
            response_text = self._call_qwen(message)
        except Exception as exc:
            logger.warning("Qwen call failed: %s", exc)
            return {"status": "injection", "complexity": 0}

        # L3: Output validation
        is_valid, type_str, complexity = self._validate_output(response_text)
        if not is_valid:
            return {"status": "injection", "complexity": 0}

        if type_str == "否":
            return {"status": "silent", "complexity": 0}

        return {"status": "reply", "complexity": complexity}


# ── Module-level singleton ──

_guardrail_instance: Guardrail | None = None


def get_guardrail() -> Guardrail:
    """Return the module-level Guardrail singleton."""
    global _guardrail_instance
    if _guardrail_instance is None:
        _guardrail_instance = Guardrail()
    return _guardrail_instance
