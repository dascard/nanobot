"""通过受认证 UDS 验证 sandboxd 已进入执行静默状态。"""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.sandbox.contracts import SandboxServiceError
from sandboxd.auth import TokenAuthenticator


MAX_HTTP_RESPONSE_BYTES = 256 * 1024
MAX_HTTP_HEADER_BYTES = 16 * 1024


class MaintenanceProbeError(RuntimeError):
    """维护探针无法证明执行静默。"""


def _positive_count(data: Mapping[str, Any], name: str) -> int:
    value = data.get(name)
    if type(value) is not int or value < 0:
        raise MaintenanceProbeError(f"sandboxd {name} 事实无效")
    return value


def _parse_execution_state_response(response: bytes) -> dict[str, Any]:
    header, separator, body = response.partition(b"\r\n\r\n")
    if not separator or len(header) > MAX_HTTP_HEADER_BYTES:
        raise MaintenanceProbeError("sandboxd 管理响应头无效")
    status_line = header.split(b"\r\n", 1)[0]
    status_fields = status_line.split(b" ", 2)
    if len(status_fields) < 2 or status_fields[1] != b"200":
        raise MaintenanceProbeError("sandboxd 执行状态接口未返回成功")
    try:
        payload = json.loads(body)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise MaintenanceProbeError("sandboxd 执行状态响应不是有效 JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("status") != "success":
        raise MaintenanceProbeError("sandboxd 执行状态响应未声明成功")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise MaintenanceProbeError("sandboxd 执行状态事实缺失")

    active_count = _positive_count(data, "active_container_count")
    verified_count = _positive_count(
        data,
        "verified_managed_container_count",
    )
    ambiguous_count = _positive_count(data, "ambiguous_container_count")
    reservation_count = _positive_count(
        data,
        "active_run_reservation_count",
    )
    if verified_count + ambiguous_count != active_count:
        raise MaintenanceProbeError("sandboxd 执行容器计数不一致")
    quiesced = data.get("quiesced")
    if type(quiesced) is not bool:
        raise MaintenanceProbeError("sandboxd 静默事实类型无效")
    expected_quiesced = active_count == 0 and reservation_count == 0
    if quiesced is not expected_quiesced:
        raise MaintenanceProbeError("sandboxd 静默事实与执行计数冲突")
    return {
        "quiesced": quiesced,
        "active_container_count": active_count,
        "verified_managed_container_count": verified_count,
        "ambiguous_container_count": ambiguous_count,
        "active_run_reservation_count": reservation_count,
    }


def query_execution_state(
    *,
    socket_path: Path,
    token_file: Path,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """读取管理接口的有界响应，不通过 TCP 或 Docker Socket。"""

    if not socket_path.is_absolute() or not token_file.is_absolute():
        raise MaintenanceProbeError("sandboxd Socket 和 Token 必须是绝对路径")
    if not 0.1 <= float(timeout_seconds) <= 30.0:
        raise MaintenanceProbeError("sandboxd 维护探针超时必须位于 0.1..30 秒")
    try:
        socket_metadata = socket_path.lstat()
    except OSError as exc:
        raise MaintenanceProbeError("sandboxd 管理 Socket 不可用") from exc
    if not stat.S_ISSOCK(socket_metadata.st_mode) or socket_path.is_symlink():
        raise MaintenanceProbeError("sandboxd 管理 Socket 类型无效")

    token = TokenAuthenticator(token_file, token_file).read_token()
    request = (
        b"GET /v1/admin/execution-state HTTP/1.1\r\n"
        b"Host: sandboxd\r\n"
        b"Authorization: Bearer "
        + token.encode("ascii")
        + b"\r\nConnection: close\r\n\r\n"
    )
    response = bytearray()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(float(timeout_seconds))
            client.connect(os.fspath(socket_path))
            client.sendall(request)
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > MAX_HTTP_RESPONSE_BYTES:
                    raise MaintenanceProbeError("sandboxd 管理响应超过安全上限")
    except MaintenanceProbeError:
        raise
    except OSError as exc:
        raise MaintenanceProbeError("sandboxd 执行状态接口不可用") from exc
    return _parse_execution_state_response(bytes(response))


def assert_quiesced(
    *,
    socket_path: Path,
    token_file: Path,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    state = query_execution_state(
        socket_path=socket_path,
        token_file=token_file,
        timeout_seconds=timeout_seconds,
    )
    if state["quiesced"] is not True:
        raise MaintenanceProbeError(
            "仍有 Sandbox 执行资源："
            f"容器 {state['active_container_count']} 个，"
            f"运行保留 {state['active_run_reservation_count']} 个"
        )
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 sandboxd 管理 UDS 验证 Sandbox 执行静默",
    )
    parser.add_argument(
        "--socket",
        default=os.environ.get(
            "NANOBOT_SANDBOXD_SOCKET",
            "/run/nanobot-sandboxd/sandboxd.sock",
        ),
        help="sandboxd Unix Socket 绝对路径",
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get(
            "NANOBOT_SANDBOXD_ADMIN_TOKEN_FILE",
            "/etc/nanobot/sandboxd-admin.token",
        ),
        help="sandboxd 管理 Token 文件绝对路径",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="UDS 请求超时，范围 0.1..30 秒",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        state = assert_quiesced(
            socket_path=Path(args.socket),
            token_file=Path(args.token_file),
            timeout_seconds=args.timeout_seconds,
        )
    except (MaintenanceProbeError, SandboxServiceError) as exc:
        print(f"Sandbox 执行静默检查失败：{exc}", file=sys.stderr)
        return 1
    print(
        "Sandbox 执行静默检查通过："
        f"容器 {state['active_container_count']} 个，运行保留 0 个。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MaintenanceProbeError",
    "assert_quiesced",
    "main",
    "query_execution_state",
]
