"""Lease 进程的有界增量 stdout/stderr 缓冲。"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError


_CURSOR_RE = re.compile(r"v1:(0|[1-9][0-9]*):(0|[1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class ProcessOutputSlice:
    stdout: str
    stderr: str
    next_cursor: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    hard_limit_exceeded: bool


class _Ring:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self.start = 0
        self.end = 0
        self.data = bytearray()

    def append(self, value: bytes) -> None:
        if not value:
            return
        self.data.extend(value)
        self.end += len(value)
        overflow = len(self.data) - self.capacity
        if overflow > 0:
            del self.data[:overflow]
            self.start += overflow

    def read_from(self, offset: int) -> tuple[bytes, bool]:
        requested = max(0, int(offset))
        truncated = requested < self.start
        effective = max(requested, self.start)
        if effective > self.end:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程输出游标无效",
            )
        return bytes(self.data[effective - self.start :]), truncated

    def retained(self) -> tuple[bytes, bool]:
        return bytes(self.data), self.start > 0


class ProcessOutputBuffer:
    """按绝对字节游标读取增量；内存始终受软上限约束。"""

    def __init__(
        self,
        *,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
        hard_limit_bytes: int,
    ) -> None:
        self._stdout = _Ring(stdout_limit_bytes)
        self._stderr = _Ring(stderr_limit_bytes)
        self._hard_limit_bytes = max(1, int(hard_limit_bytes))
        self._hard_limit_exceeded = False
        self._lock = threading.Lock()

    @staticmethod
    def _cursor(stdout_offset: int, stderr_offset: int) -> str:
        return f"v1:{int(stdout_offset)}:{int(stderr_offset)}"

    @staticmethod
    def _parse_cursor(value: str) -> tuple[int, int]:
        normalized = str(value or "")
        if not normalized:
            return 0, 0
        match = _CURSOR_RE.fullmatch(normalized)
        if match is None or len(normalized) > 96:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程输出游标无效",
            )
        try:
            stdout_offset = int(match.group(1))
            stderr_offset = int(match.group(2))
        except (TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程输出游标无效",
            ) from exc
        if stdout_offset > 2**63 - 1 or stderr_offset > 2**63 - 1:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程输出游标无效",
            )
        return stdout_offset, stderr_offset

    def feed(
        self,
        *,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
    ) -> bool:
        with self._lock:
            if stdout:
                self._stdout.append(bytes(stdout))
            if stderr:
                self._stderr.append(bytes(stderr))
            if (
                self._stdout.end + self._stderr.end
                > self._hard_limit_bytes
            ):
                self._hard_limit_exceeded = True
            return self._hard_limit_exceeded

    def snapshot(
        self,
        *,
        cursor: str = "",
        full: bool = False,
    ) -> ProcessOutputSlice:
        with self._lock:
            if full:
                stdout, stdout_truncated = self._stdout.retained()
                stderr, stderr_truncated = self._stderr.retained()
            else:
                stdout_offset, stderr_offset = self._parse_cursor(cursor)
                stdout, stdout_truncated = self._stdout.read_from(
                    stdout_offset
                )
                stderr, stderr_truncated = self._stderr.read_from(
                    stderr_offset
                )
            return ProcessOutputSlice(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                next_cursor=self._cursor(
                    self._stdout.end,
                    self._stderr.end,
                ),
                stdout_bytes=self._stdout.end,
                stderr_bytes=self._stderr.end,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                hard_limit_exceeded=self._hard_limit_exceeded,
            )


__all__ = ["ProcessOutputBuffer", "ProcessOutputSlice"]
