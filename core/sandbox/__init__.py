"""Nanobot Sandbox 的身份、元数据与安全存储基础组件。"""

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.identity import Principal, derive_principal

__all__ = [
    "Principal",
    "SandboxErrorCode",
    "SandboxServiceError",
    "derive_principal",
]
