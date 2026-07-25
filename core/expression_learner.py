"""旧表达/黑话自动学习调度器的兼容墓碑。

规则信号统一由 ``core.group_learning`` 的冻结 Registry 提供；本模块不再
包含自然语言正则、保留词副本、数据库读取或任何 Writer。
"""

from __future__ import annotations

import logging


logger = logging.getLogger("nanobot.expression_learner")


def _record_retired_writer_usage() -> None:
    from core.lifecycle import record_compatibility_usage

    record_compatibility_usage(
        "schema.legacy_expression_memory_write"
    )
    record_compatibility_usage(
        "schema.legacy_jargon_memory_write"
    )


def run_learning_cycle() -> dict[str, object]:
    """兼容墓碑：旧 Writer 已停止，不读取或写入数据库。"""

    _record_retired_writer_usage()
    logger.warning(
        "[ExpressionLearner] retired_writer_noop=true "
        "replacement=group_learning_candidates"
    )
    return {
        "retired": True,
        "writer": "disabled",
        "replacement": "group_learning_candidates",
    }


def expression_learner_scheduler(stop_event) -> None:
    """兼容墓碑：记录误启动并等待宿主关闭。"""

    _record_retired_writer_usage()
    logger.warning(
        "[ExpressionLearner] scheduler_retired=true "
        "replacement=group_learning_candidates"
    )
    stop_event.wait()


__all__ = [
    "expression_learner_scheduler",
    "run_learning_cycle",
]
