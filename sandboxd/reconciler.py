"""只回收 sandboxd 自己的孤儿容器。"""

from __future__ import annotations

from typing import Any

from sandboxd.docker_backend import (
    CONTAINER_PREFIX,
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    managed_container,
)


class OrphanReconciler:
    def __init__(self, docker_client: Any) -> None:
        self.client = docker_client

    def reconcile(self) -> dict[str, int]:
        filters = {
            "label": [
                f"{MANAGED_LABEL}=true",
                f"{MANAGED_BY_LABEL}=sandboxd",
            ],
            "name": CONTAINER_PREFIX,
        }
        candidates = self.client.containers.list(all=True, filters=filters)
        inspected = 0
        removed = 0
        for container in candidates:
            inspected += 1
            if not managed_container(container):
                continue
            try:
                container.reload()
                if bool(container.attrs.get("State", {}).get("Running")):
                    try:
                        container.stop(timeout=2)
                    except Exception:
                        container.kill()
                container.remove(force=True, v=True)
                removed += 1
            except Exception:
                continue
        return {"inspected": inspected, "removed": removed}
