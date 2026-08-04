"""框架无关的声明式 Agent Manifest 聚合合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import (
    canonical_json,
    content_sha256,
    manifest_dict,
    manifest_payload,
)
from .validation import sha256, version
from .values import (
    AgentBudget,
    AgentEvaluationPolicy,
    AgentExtensionSet,
    AgentIdentity,
    AgentModelSpec,
    AgentOutputContractRef,
    AgentPermissionSpec,
    AgentPromptBundleRef,
    AgentRolloutPolicy,
    AgentRuntimeSpec,
    AgentSourceRef,
    AgentStatePolicy,
)


@dataclass(frozen=True, slots=True)
class AgentManifest:
    """只保存声明和外部引用，不保存 Provider 凭据或运行时可变状态。"""

    schema_version: int
    version: str
    identity: AgentIdentity
    source: AgentSourceRef
    runtime: AgentRuntimeSpec
    model: AgentModelSpec
    prompt: AgentPromptBundleRef
    output: AgentOutputContractRef
    extensions: AgentExtensionSet
    state: AgentStatePolicy
    permissions: AgentPermissionSpec
    budget: AgentBudget
    evaluation: AgentEvaluationPolicy
    rollout: AgentRolloutPolicy
    content_sha256: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("manifest.schema_version 当前只支持 1")
        object.__setattr__(
            self,
            "version",
            version(self.version, "manifest.version"),
        )
        expected_types = {
            "identity": AgentIdentity,
            "source": AgentSourceRef,
            "runtime": AgentRuntimeSpec,
            "model": AgentModelSpec,
            "prompt": AgentPromptBundleRef,
            "output": AgentOutputContractRef,
            "extensions": AgentExtensionSet,
            "state": AgentStatePolicy,
            "permissions": AgentPermissionSpec,
            "budget": AgentBudget,
            "evaluation": AgentEvaluationPolicy,
            "rollout": AgentRolloutPolicy,
        }
        for field_name, expected_type in expected_types.items():
            if not isinstance(getattr(self, field_name), expected_type):
                raise ValueError(
                    f"manifest.{field_name} 必须是 {expected_type.__name__}"
                )
        digest = content_sha256(manifest_payload(self))
        declared = sha256(
            self.content_sha256,
            "manifest.content_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise ValueError("manifest.content_sha256 与声明内容不匹配")
        object.__setattr__(self, "content_sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return manifest_dict(self)

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())
