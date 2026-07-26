"""Nanobot 与 sandboxd 之间的稳定后端接口。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SandboxBackend(Protocol):
    def health(self) -> dict[str, Any]: ...

    def ready(self) -> dict[str, Any]: ...

    def ensure_workspace(
        self,
        workspace_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]: ...

    def list_files(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def read_file(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def search_files(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def write_file(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def apply_patch(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def publish_asset(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def stage_assets(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def run(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def cancel_run(self, run_id: str, *, request_id: str) -> dict[str, Any]: ...

    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def ensure_lease(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_lease(self, lease_id: str) -> dict[str, Any]: ...

    def sync_lease_assets(
        self,
        lease_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def start_process(
        self,
        lease_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def get_process(
        self,
        process_id: str,
        *,
        cursor: str = "",
    ) -> dict[str, Any]: ...

    def write_process_stdin(
        self,
        process_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def terminate_process(
        self,
        process_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]: ...

    def stop_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]: ...

    def destroy_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]: ...

    def recreate_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]: ...

    def controller_state(self) -> dict[str, Any]: ...

    def list_leases(self) -> dict[str, Any]: ...

    def list_processes(self) -> dict[str, Any]: ...

    def workspace_usage(self) -> dict[str, Any]: ...

    def reconcile_leases(self, *, request_id: str) -> dict[str, Any]: ...

    def terminate_all_leases(
        self,
        *,
        request_id: str,
        reason: str,
    ) -> dict[str, Any]: ...

    def apply_workspace_quota(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def inspect_workspace_quota(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class FakeSandboxBackend:
    """不依赖 Docker 的单元测试后端。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, dict[str, Any]] = {}

    def set_response(self, operation: str, response: Mapping[str, Any]) -> None:
        self.responses[operation] = dict(response)

    def _call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        self.calls.append((operation, normalized))
        return dict(self.responses.get(operation, {
            "status": "success",
            "summary": "Fake SandboxBackend 调用成功",
            "next_actions": [],
            "artifacts": [],
            "data": {},
        }))

    def health(self) -> dict[str, Any]:
        return self._call("health", {})

    def ready(self) -> dict[str, Any]:
        return self._call("ready", {})

    def ensure_workspace(self, workspace_id: str, *, request_id: str) -> dict[str, Any]:
        return self._call("ensure_workspace", {
            "workspace_id": workspace_id,
            "request_id": request_id,
        })

    def list_files(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("list_files", payload)

    def read_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("read_file", payload)

    def search_files(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("search_files", payload)

    def write_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("write_file", payload)

    def apply_patch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("apply_patch", payload)

    def publish_asset(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("publish_asset", payload)

    def stage_assets(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("stage_assets", payload)

    def run(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("run", payload)

    def cancel_run(self, run_id: str, *, request_id: str) -> dict[str, Any]:
        return self._call("cancel_run", {"run_id": run_id, "request_id": request_id})

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._call("get_run", {"run_id": run_id})

    def ensure_lease(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("ensure_lease", payload)

    def get_lease(self, lease_id: str) -> dict[str, Any]:
        return self._call("get_lease", {"lease_id": lease_id})

    def sync_lease_assets(
        self,
        lease_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._call(
            "sync_lease_assets",
            {"lease_id": lease_id, **dict(payload)},
        )

    def start_process(
        self,
        lease_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._call(
            "start_process",
            {"lease_id": lease_id, **dict(payload)},
        )

    def get_process(
        self,
        process_id: str,
        *,
        cursor: str = "",
    ) -> dict[str, Any]:
        return self._call(
            "get_process",
            {"process_id": process_id, "cursor": cursor},
        )

    def write_process_stdin(
        self,
        process_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._call(
            "write_process_stdin",
            {"process_id": process_id, **dict(payload)},
        )

    def terminate_process(
        self,
        process_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "terminate_process",
            {
                "process_id": process_id,
                "request_id": request_id,
            },
        )

    def stop_lease(self, lease_id: str, *, request_id: str) -> dict[str, Any]:
        return self._call(
            "stop_lease",
            {"lease_id": lease_id, "request_id": request_id},
        )

    def destroy_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "destroy_lease",
            {"lease_id": lease_id, "request_id": request_id},
        )

    def recreate_lease(
        self,
        lease_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "recreate_lease",
            {"lease_id": lease_id, "request_id": request_id},
        )

    def controller_state(self) -> dict[str, Any]:
        return self._call("controller_state", {})

    def list_leases(self) -> dict[str, Any]:
        return self._call("list_leases", {})

    def list_processes(self) -> dict[str, Any]:
        return self._call("list_processes", {})

    def workspace_usage(self) -> dict[str, Any]:
        return self._call("workspace_usage", {})

    def reconcile_leases(self, *, request_id: str) -> dict[str, Any]:
        return self._call("reconcile_leases", {"request_id": request_id})

    def terminate_all_leases(
        self,
        *,
        request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._call(
            "terminate_all_leases",
            {"request_id": request_id, "reason": reason},
        )

    def apply_workspace_quota(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("apply_workspace_quota", payload)

    def inspect_workspace_quota(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("inspect_workspace_quota", payload)
