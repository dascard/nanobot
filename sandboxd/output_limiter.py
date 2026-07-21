"""Sandbox stdout/stderr 的软截断与硬终止计数器。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class OutputSnapshot:
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    hard_limit_exceeded: bool


class OutputLimiter:
    def __init__(
        self,
        *,
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
        hard_limit_bytes: int,
    ) -> None:
        self.stdout_limit_bytes = int(stdout_limit_bytes)
        self.stderr_limit_bytes = int(stderr_limit_bytes)
        self.hard_limit_bytes = int(hard_limit_bytes)
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stdout_bytes = 0
        self._stderr_bytes = 0
        self._hard_limit_exceeded = False
        self._lock = Lock()

    def feed(self, stdout: bytes | None, stderr: bytes | None) -> bool:
        with self._lock:
            if stdout:
                self._stdout_bytes += len(stdout)
                remaining = max(0, self.stdout_limit_bytes - len(self._stdout))
                self._stdout.extend(stdout[:remaining])
            if stderr:
                self._stderr_bytes += len(stderr)
                remaining = max(0, self.stderr_limit_bytes - len(self._stderr))
                self._stderr.extend(stderr[:remaining])
            if self._stdout_bytes + self._stderr_bytes > self.hard_limit_bytes:
                self._hard_limit_exceeded = True
            return self._hard_limit_exceeded

    def snapshot(self) -> OutputSnapshot:
        with self._lock:
            return OutputSnapshot(
                stdout=bytes(self._stdout).decode("utf-8", errors="replace"),
                stderr=bytes(self._stderr).decode("utf-8", errors="replace"),
                stdout_bytes=self._stdout_bytes,
                stderr_bytes=self._stderr_bytes,
                stdout_truncated=self._stdout_bytes > len(self._stdout),
                stderr_truncated=self._stderr_bytes > len(self._stderr),
                hard_limit_exceeded=self._hard_limit_exceeded,
            )
