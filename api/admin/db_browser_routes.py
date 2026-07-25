"""Admin 结构化数据库视图路由。"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.admin.table_views import (
    ADMIN_TABLE_VIEW_REGISTRY,
    AdminTableViewCursorError,
    AdminTableViewFilterError,
    AdminTableViewLimitError,
    AdminTableViewNotFoundError,
    AdminTableViewService,
    AdminTableViewUnavailableError,
)
from core.database import get_db


logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/db", tags=["admin-db-browser"])
TABLE_VIEW_SERVICE = AdminTableViewService(ADMIN_TABLE_VIEW_REGISTRY)

AdminFilterValue = str | int | float | bool | None
AdminCellValue = str | int | float | bool | None


class AdminTableViewQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: dict[str, AdminFilterValue] = Field(default_factory=dict)
    cursor: str | None = None
    limit: int = 50


class AdminRegistrySnapshotResponse(BaseModel):
    namespace: str
    generation: int
    sha256: str


class AdminTableSortResponse(BaseModel):
    column: str
    direction: Literal["asc", "desc"]
    tie_breaker: str | None


class AdminTableFilterResponse(BaseModel):
    filter_id: str
    column: str
    value_type: Literal[
        "string",
        "integer",
        "number",
        "boolean",
        "datetime",
    ]


class AdminTableColumnResponse(BaseModel):
    name: str
    display_policy: Literal["full", "preview", "redacted"]


class AdminTableViewResponse(BaseModel):
    view_id: str
    owner: str
    group_id: str
    group_label: str
    description: str
    columns: list[AdminTableColumnResponse]
    default_sort: AdminTableSortResponse
    filters: list[AdminTableFilterResponse]
    max_limit: int
    default_limit: int
    lifecycle: Literal["active", "deprecated"]


class AdminTableViewGroupResponse(BaseModel):
    group_id: str
    label: str
    view_ids: list[str]


class AdminTableViewListResponse(BaseModel):
    registry: AdminRegistrySnapshotResponse
    views: list[AdminTableViewResponse]
    groups: list[AdminTableViewGroupResponse]


class AdminCellMetaResponse(BaseModel):
    kind: Literal["null", "value", "text", "binary", "redacted"]
    truncated: bool
    full_length: int | None
    redacted: bool


class AdminTableRowsResponse(BaseModel):
    view_id: str
    total: int
    limit: int
    has_next: bool
    next_cursor: str | None
    columns: list[str]
    rows: list[dict[str, AdminCellValue]]
    cell_meta: list[dict[str, AdminCellMetaResponse]]


def _raise_view_error(exc: Exception) -> None:
    if isinstance(exc, AdminTableViewNotFoundError):
        status_code = 404
    elif isinstance(exc, AdminTableViewUnavailableError):
        status_code = 503
    else:
        status_code = 400
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": getattr(exc, "code", "admin_view_error"),
            "message": str(exc),
        },
    ) from exc


@router.get(
    "/views",
    response_model=AdminTableViewListResponse,
)
def list_views(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    descriptors = TABLE_VIEW_SERVICE.available_descriptors(db)
    groups: dict[str, dict[str, object]] = {}
    for descriptor in descriptors:
        group = groups.setdefault(
            descriptor.group_id,
            {
                "group_id": descriptor.group_id,
                "label": descriptor.group_label,
                "view_ids": [],
            },
        )
        group["view_ids"].append(descriptor.registry_id)  # type: ignore[union-attr]
    return {
        "registry": {
            "namespace": ADMIN_TABLE_VIEW_REGISTRY.namespace,
            "generation": ADMIN_TABLE_VIEW_REGISTRY.generation,
            "sha256": ADMIN_TABLE_VIEW_REGISTRY.sha256,
        },
        "views": [
            descriptor.to_public_dict()
            for descriptor in descriptors
        ],
        "groups": list(groups.values()),
    }


@router.post(
    "/views/{view_id}/rows",
    response_model=AdminTableRowsResponse,
)
def query_view_rows(
    view_id: str,
    body: AdminTableViewQuery,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    try:
        result = TABLE_VIEW_SERVICE.query(
            db,
            view_id=view_id,
            filters=body.filters,
            cursor=body.cursor,
            limit=body.limit,
        )
    except (
        AdminTableViewNotFoundError,
        AdminTableViewUnavailableError,
        AdminTableViewFilterError,
        AdminTableViewCursorError,
        AdminTableViewLimitError,
    ) as exc:
        _raise_view_error(exc)
    except Exception:
        logger.exception(
            "admin structured db view query failed: view_id=%s",
            view_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "admin_view_internal_error",
                "message": "内部错误",
            },
        )
    return {
        "view_id": result.view_id,
        "total": result.total,
        "limit": result.limit,
        "has_next": result.has_next,
        "next_cursor": result.next_cursor,
        "columns": list(result.columns),
        "rows": list(result.rows),
        "cell_meta": list(result.cell_meta),
    }
