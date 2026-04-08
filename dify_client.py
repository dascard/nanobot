"""
Dify Workflow HTTP 客户端。
封装所有对 Dify API 的调用，含指数退避重试。
"""
import json
import time
import logging
import requests

from config import (
    DIFY_BASE_URL,
    DIFY_MAX_RETRIES,
    DIFY_RETRY_BASE_DELAY,
    DIFY_REQUEST_TIMEOUT,
)

logger = logging.getLogger("nanobot.dify")


def call_dify_workflow(api_key: str, inputs: dict) -> dict:
    """
    通用 Dify Workflow 调用器（含指数退避重试）。
    返回 outputs 字典，失败时抛出 RuntimeError。
    """
    url = f"{DIFY_BASE_URL}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs,
        "response_mode": "blocking",
        "user": "nanobot-server",
    }
    safe_key = f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"

    last_exc: Exception | None = None
    for attempt in range(1, DIFY_MAX_RETRIES + 1):
        try:
            logger.info(
                f"  → [attempt {attempt}/{DIFY_MAX_RETRIES}] POST {url}  "
                f"key={safe_key}  inputs_keys={list(inputs.keys())}"
            )
            resp = requests.post(
                url, headers=headers, json=payload, timeout=DIFY_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            body = resp.json()

            outputs = body.get("data", {}).get("outputs", {})
            if not outputs:
                raise RuntimeError(
                    f"Dify returned empty outputs. "
                    f"Response: {json.dumps(body, ensure_ascii=False)[:500]}"
                )
            return outputs

        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if attempt < DIFY_MAX_RETRIES:
                delay = DIFY_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"  ⚠ Attempt {attempt} failed: {exc}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"  ✗ All {DIFY_MAX_RETRIES} attempts failed.")

    raise RuntimeError(
        f"Dify workflow call failed after {DIFY_MAX_RETRIES} retries: {last_exc}"
    )

def call_dify_chat(api_key: str, user_id: str, query: str, active_persona: str, active_system_prompt: str, recent_context_summary: str = "") -> str:
    """
    代理调用 Dify 01 对话模型引擎（/chat-messages 接口），并注入上下文。
    """
    url = f"{DIFY_BASE_URL}/chat-messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {
            "active_persona": active_persona,
            "active_system_prompt": active_system_prompt,
            "recent_context_summary": recent_context_summary
        },
        "query": query,
        "response_mode": "blocking",
        "user": user_id,
    }
    safe_key = f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"

    last_exc: Exception | None = None
    for attempt in range(1, DIFY_MAX_RETRIES + 1):
        try:
            logger.info(
                f"  → [chat attempt {attempt}/{DIFY_MAX_RETRIES}] POST {url} key={safe_key}"
            )
            resp = requests.post(
                url, headers=headers, json=payload, timeout=DIFY_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            body = resp.json()
            
            answer = body.get("answer", "")
            if not answer:
                logger.warning(f"Empty answer from Dify: {body}")
            return answer

        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if attempt < DIFY_MAX_RETRIES:
                delay = DIFY_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)

    raise RuntimeError(f"Dify chat call failed: {last_exc}")

def write_dify_dataset(dataset_id: str, document_name: str, document_text: str) -> None:
    """
    将文本写入指定的 Dify 知识库 (Dataset)。
    主要用于 02/03 的结果入库，恢复 Tri-layer 架构的 Warm RAG 检索层。
    """
    from config import DATASET_API_KEY
    if not DATASET_API_KEY or not dataset_id:
        logger.warning(f"  Skipping dataset write for {document_name} because DATASET_API_KEY or dataset_id is empty.")
        return

    url = f"{DIFY_BASE_URL}/datasets/{dataset_id}/document/create-by-text"
    headers = {
        "Authorization": f"Bearer {DATASET_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "name": document_name,
        "text": document_text,
        "indexing_technique": "high_quality",
        "process_rule": {
            "mode": "automatic"
        }
    }

    last_exc: Exception | None = None
    for attempt in range(1, DIFY_MAX_RETRIES + 1):
        try:
            logger.info(f"  → [dataset attempt {attempt}/{DIFY_MAX_RETRIES}] POST dataset {dataset_id}")
            resp = requests.post(
                url, headers=headers, json=payload, timeout=DIFY_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            logger.info(f"  ✓ Successfully wrote {document_name} to dataset {dataset_id}")
            return
        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if attempt < DIFY_MAX_RETRIES:
                delay = DIFY_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                time.sleep(delay)

    logger.error(f"  ✗ Failed to write dataset: {last_exc}")
