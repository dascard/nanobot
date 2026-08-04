"""恢复前文件状态的 sandboxd 只读验证适配。"""

from __future__ import annotations

from collections.abc import Mapping
from hmac import compare_digest

from sqlalchemy.orm import Session

from core.run_recovery.contracts import RunRecoveryFileProof
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import HttpSandboxdBackend
from core.settings_service import settings


class SandboxdRecoveryFileVerifier:
    """只读取虚拟 Workspace 路径，不接触或暴露宿主真实路径。"""

    def __init__(self, backend: SandboxBackend) -> None:
        if not isinstance(backend, SandboxBackend):
            raise TypeError("backend 未实现 SandboxBackend")
        self._backend = backend

    @classmethod
    def from_settings(cls, db: Session) -> "SandboxdRecoveryFileVerifier":
        return cls(HttpSandboxdBackend(
            socket_path=str(settings.get_for_session(
                db,
                "sandbox.sandboxd_socket",
            )),
            token_file=str(settings.get_for_session(
                db,
                "sandbox.sandboxd_token_file",
            )),
            timeout_seconds=float(settings.get_for_session(
                db,
                "sandbox.backend_timeout_seconds",
            )),
            run_timeout_seconds=float(settings.get_for_session(
                db,
                "sandbox.run_timeout_seconds",
            )),
        ))

    def verify(self, proof: RunRecoveryFileProof) -> bool:
        response = self._backend.read_text_file({
            "workspace_id": proof.workspace_id,
            "path": proof.virtual_path,
            "cwd": "",
        })
        data = response.get("data")
        if not isinstance(data, Mapping):
            return False
        actual_path = str(data.get("path") or "")
        actual_sha256 = str(data.get("sha256") or "").lower()
        return (
            proof.exists
            and actual_path == proof.virtual_path
            and len(actual_sha256) == 64
            and compare_digest(actual_sha256, proof.sha256)
        )

    def close(self) -> None:
        close = getattr(self._backend, "close", None)
        if callable(close):
            close()


__all__ = ["SandboxdRecoveryFileVerifier"]
