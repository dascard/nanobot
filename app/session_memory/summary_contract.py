"""Session Summary 输入/输出合同的版本与稳定指纹。"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache


# 该版本同时覆盖解析器字段集合、继承审计语义和摘要任务模板。合同发生
# 不兼容变化时必须递增，旧失败任务才可以走一次迁移式自动恢复。
SESSION_SUMMARY_CONTRACT_VERSION = 5


@lru_cache(maxsize=1)
def session_summary_contract_fingerprint() -> str:
    """计算当前有效摘要模板和代码合同的 SHA-256 指纹。

    模板目录在启动早期可能尚未初始化；此时保留确定性的版本指纹，避免
    入队链路因为观测信息不可用而失败。worker 真正渲染模板时仍会执行完整
    的模板可用性校验。
    """

    try:
        from core.prompt_v2.task_templates import render_task_prompt

        templates = {
            key: render_task_prompt(key, {})
            for key in (
                "tasks/session_summary_system",
                "tasks/session_summary_output",
            )
        }
    except Exception:
        templates = {}
    payload = {
        "contract_version": SESSION_SUMMARY_CONTRACT_VERSION,
        "templates": templates,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def clear_session_summary_contract_fingerprint_cache() -> None:
    """测试或运行时热更新模板后清除进程内缓存。"""

    session_summary_contract_fingerprint.cache_clear()
