"""Admin 应用服务与只读投影。"""

from core.admin.table_views import (
    ADMIN_TABLE_VIEW_REGISTRY,
    AdminTableViewDescriptor,
    AdminTableViewService,
)

__all__ = [
    "ADMIN_TABLE_VIEW_REGISTRY",
    "AdminTableViewDescriptor",
    "AdminTableViewService",
]
