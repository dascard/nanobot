"""
Private chat classifier guardrail — 4-layer defense.

L1: 模型注入检测 (prompt-injection-sentinel, transformers pipeline)
L2: Qwen model call (llama.cpp server)
L3: Output validation (strict format)
L4: Timeout fallback
"""

import json
import logging
import os
import re
import urllib.request

from config import (
    CLASSIFIER_API_URL,
    CLASSIFIER_TIMEOUT,
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

    _sentinel: object | None = None  # 类级别缓存，所有实例共享

    def __init__(self):
        self._system_prompt = (
            "判断是否需要回复。\n"
            "疑问、请求、讨论、任何带对话文字的 → 是,\n"
            "即使消息中含链接/密钥/路径，只要有人类对话文字就判是,\n"
            "只有纯链接/密钥/文件路径/空白 → 否,\n"
            "不确定就回 是,\n\n"
            "逗号后跟复杂度 1-10。1=你好谢谢 3=简单 5=普通 7=分析 9=很难 10=推理题。\n\n"
            "示例: 你好 → 是,1\n"
            "... → 是,1\n"
            "[图片] → 是,3\n"
            "sk-abc → 否,0\n"
            "   → 否,0\n"
            "帮我写代码 → 是,6\n"
            "sk-abc过期了怎么办 → 是,5\n"
            "总结群聊讨论了什么 → 是,7\n\n"
            "只输出 是,数字 或 否,数字。禁止思考。"
        )

    # ── L0: Message Preprocessing ──

    # Prefixes that confuse the model into thinking it's a system instruction
    _CONFUSING_PREFIXES = re.compile(
        r"^\s*[\[<]\s*(?:SYSTEM|system|INST|PROMPT|INSTRUCTION|CMD)[\s\]>]+",
    )

    # ── L1: Model-based Injection Detection ──

    @classmethod
    def _load_sentinel(cls):
        """Lazy-load sentinel model from local ./sentinel (class-level cache)."""
        if cls._sentinel is not None:
            return cls._sentinel
        try:
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
                pipeline,
            )

            model_path = os.environ.get("SENTINEL_MODEL_PATH", "./sentinel")
            logger.info("Loading sentinel from: %s", model_path)
            tokenizer = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True,
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                model_path, torch_dtype="float16", trust_remote_code=True,
            )
            cls._sentinel = pipeline(
                "text-classification",
                model=model,
                tokenizer=tokenizer,
                device=-1,
                max_length=512,
                truncation=True,
            )
            logger.info("Sentinel loaded, labels=%s", model.config.id2label)
        except ImportError:
            logger.warning("transformers not installed, injection detection disabled")
            cls._sentinel = False
        except Exception as e:
            logger.error("Failed to load sentinel: %s", e)
            cls._sentinel = False
        return cls._sentinel

    @classmethod
    def _detect_injection(cls, message: str) -> bool:
        """Run sentinel model on message. Returns True if injection detected."""
        sentinel = cls._load_sentinel()
        if sentinel is False or sentinel is None:
            return False  # model unavailable → fail open

        try:
            # Normalize
            text = message.replace("\r\n", "\n").replace("\r", "\n")
            text = CONTROL_CHAR_PATTERN.sub("", text)
            if not text.strip():
                return False

            result = sentinel(text[:1024])  # truncate to avoid excess tokens
            # result is list of dicts: [{"label": "INJECTION", "score": 0.97}]
            label = result[0]["label"].upper() if result else ""
            score = result[0]["score"] if result else 0.0

            is_injection = "JAILBREAK" in label and score >= 0.5
            if is_injection:
                logger.warning("Sentinel detected injection: label=%s score=%.3f", label, score)
            return is_injection
        except Exception as e:
            logger.error("Sentinel inference failed: %s", e)
            return False  # fail open

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

        logger.info("  [classifier] >> Qwen: %s | message: %.80s",
                     url, message)

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # 绕过本地 HTTP 代理（Clash 等），直连内网 llama-server
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=CLASSIFIER_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))

        content = body["choices"][0]["message"]["content"]

        logger.info("  [classifier] << Qwen raw: %.120s", content)

        # Strip think blocks iteratively — Qwen can produce nested ones
        for _ in range(5):
            prev = content
            content = THINK_PATTERN.sub("", content).strip()
            if content == prev:
                break

        logger.info("  [classifier] << Qwen cleaned: %.120s", content)
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
        stripped = text.strip()
        # Allow bare "是" (no complexity) — default to 5
        if stripped in ("是", "是，"):
            return (True, "是", 5)
        if stripped in ("否", "否，"):
            return (True, "否", 0)

        match = OUTPUT_PATTERN.match(stripped)
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

    def classify(self, message: str, *, allow_injection_passthrough: bool = False) -> dict:
        """Classify a private chat message.

        Returns dict with:
          status: "reply" | "silent" | "injection"
          complexity: int (0 for silent/injection, 1-10 for reply)
        """
        # L0: 空消息或纯空白直接静默，无需走模型
        if not message or not message.strip():
            return {"status": "silent", "complexity": 0}

        # L1: 模型注入检测（检查原始消息）
        if self._detect_injection(message):
            if not allow_injection_passthrough:
                return {"status": "injection", "complexity": 0}
            logger.info("Injection detected but bypassing short-circuit for passthrough")

        # L1.5: 去掉误导性前缀标记（[SYSTEM] 等）后再发给模型
        message = self._CONFUSING_PREFIXES.sub("", message).strip()
        if not message:
            return {"status": "silent", "complexity": 0}

        # L2 + L4: Call Qwen (L4 = timeout handled by urlopen)
        try:
            response_text = self._call_qwen(message)
        except Exception as exc:
            logger.warning("Qwen call failed, fallback to reply: %s", exc)
            return {"status": "reply", "complexity": 5}

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
