"""sandboxd UDS 的双重 Token 鉴权。"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError


def _decode_token(raw: bytes) -> str:
    """只接受可安全放入 HTTP Header 的可见 ASCII Token。"""

    if raw.endswith(b"\r\n"):
        payload = raw[:-2]
    elif raw.endswith(b"\n"):
        payload = raw[:-1]
    else:
        payload = raw
    if not 32 <= len(payload) <= 4096 or any(
        byte < 0x21 or byte > 0x7E for byte in payload
    ):
        raise ValueError("invalid token content")
    return payload.decode("ascii")


class TokenAuthenticator:
    def __init__(self, token_file: Path, client_token_path: Path) -> None:
        self.token_file = token_file
        self.client_token_path = client_token_path

    def read_token(self) -> str:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            file_fd = os.open(self.token_file, flags)
            try:
                metadata = os.fstat(file_fd)
                mode = stat.S_IMODE(metadata.st_mode)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > 4096
                    or not mode & stat.S_IRUSR
                    or mode & ~0o600
                ):
                    raise ValueError("invalid token file")
                raw = os.read(file_fd, 4097)
                if len(raw) != metadata.st_size:
                    raise ValueError("token file changed while reading")
            finally:
                os.close(file_fd)
            token = _decode_token(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面凭据不可用",
            ) from exc
        return token

    def prepare_client_token(self) -> None:
        token = self.read_token()
        parent = self.client_token_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temp = parent / f".{self.client_token_path.name}.{secrets.token_hex(8)}.tmp"
        file_fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o640,
        )
        try:
            os.write(file_fd, token.encode("utf-8") + b"\n")
            os.fsync(file_fd)
            os.fchmod(file_fd, 0o640)
        finally:
            os.close(file_fd)
        os.replace(temp, self.client_token_path)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def verify_authorization(self, authorization: str | None) -> bool:
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            return False
        candidate = authorization[len(prefix):]
        try:
            expected = self.read_token()
        except SandboxServiceError:
            return False
        return secrets.compare_digest(candidate, expected)
