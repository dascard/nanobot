"""模型决策与模型目录 Port 生命周期合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.model_provider.catalog_runtime import (
    ModelCatalogRuntime,
    ModelCatalogRuntimeState,
    ModelCatalogWriterPort,
)
from core.model_provider.decision_runtime import (
    DecisionModelPort,
    DecisionModelRuntime,
    DecisionModelRuntimeState,
)


class _DecisionPort:
    @property
    def adapter_id(self) -> str:
        return "fake_decision"

    def classify_private(
        self,
        message: str,
        has_files: bool = False,
    ) -> Mapping[str, Any]:
        return {"action": "reply_now", "message": message, "has_files": has_files}

    def judge_group_timing(self, context: str) -> Mapping[str, Any]:
        return {"action": "continue", "context": context}

    def judge_group_proactive(self, context: str) -> Mapping[str, Any]:
        return {"should_speak": True, "context": context}


class _CatalogPort:
    def __init__(self) -> None:
        self.models: list[dict[str, Any]] = []

    @property
    def adapter_id(self) -> str:
        return "fake_catalog"

    def upsert_models(self, models: tuple[Mapping[str, Any], ...]) -> int:
        self.models.extend(dict(model) for model in models)
        return len(models)


def test_decision_model_runtime_is_explicit_and_fail_closed():
    runtime = DecisionModelRuntime()
    port = _DecisionPort()

    assert isinstance(port, DecisionModelPort)
    assert runtime.state is DecisionModelRuntimeState.NEW
    with pytest.raises(RuntimeError, match="尚未启动"):
        runtime.judge_group_timing("上下文")

    runtime.start(port)
    assert runtime.classify_private("消息", True)["has_files"] is True
    assert runtime.judge_group_timing("上下文")["action"] == "continue"
    assert runtime.judge_group_proactive("上下文")["should_speak"] is True
    assert runtime.introspect() == {
        "state": "running",
        "adapter_id": "fake_decision",
    }

    runtime.stop()
    assert runtime.state is DecisionModelRuntimeState.STOPPED
    with pytest.raises(RuntimeError, match="已经停止"):
        runtime.classify_private("消息")


def test_model_catalog_runtime_validates_lifecycle_and_write_count():
    runtime = ModelCatalogRuntime()
    port = _CatalogPort()

    assert isinstance(port, ModelCatalogWriterPort)
    assert runtime.state is ModelCatalogRuntimeState.NEW
    with pytest.raises(RuntimeError, match="尚未启动"):
        runtime.upsert_models(({"id": "model-a"},))

    runtime.start(port)
    assert runtime.upsert_models(({"id": "model-a"}, {"id": "model-b"})) == 2
    assert port.models == [{"id": "model-a"}, {"id": "model-b"}]

    runtime.stop()
    assert runtime.state is ModelCatalogRuntimeState.STOPPED
