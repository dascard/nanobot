"""FastAPI Endpoint Contract 的稳定描述符与 OpenAPI 投影。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict


_OPERATION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_HTTP_METHODS = frozenset({"GET", "PUT", "POST", "DELETE", "PATCH"})
_OPENAPI_METHODS = frozenset({
    "get",
    "put",
    "post",
    "delete",
    "patch",
    "options",
    "head",
})


class ApiErrorResponse(BaseModel):
    """兼容现有 HTTPException，同时允许逐步加入稳定错误码。"""

    model_config = ConfigDict(extra="allow")

    detail: str | list[dict[str, Any]] | dict[str, Any]
    code: str | None = None
    retryable: bool | None = None
    trace_ref: str | None = None


def standard_error_responses(
    *status_codes: int,
) -> dict[int, dict[str, object]]:
    """为显式 Endpoint Contract 声明稳定错误响应 Schema。"""

    descriptions = {
        400: "请求不满足业务合同",
        401: "管理员鉴权失败",
        403: "权限不足",
        404: "目标资源不存在",
        409: "资源状态冲突",
        422: "请求参数校验失败",
        500: "服务内部错误",
        503: "依赖服务不可用",
    }
    return {
        int(status): {
            "model": ApiErrorResponse,
            "description": descriptions.get(
                int(status),
                "请求失败",
            ),
        }
        for status in dict.fromkeys(status_codes)
    }


@dataclass(frozen=True, slots=True)
class EndpointContractDescriptor:
    contract_id: str
    operation_id: str
    owner_module: str
    method: str
    path: str
    client_function: str
    response_schema: str
    request_schema: str = ""
    error_statuses: tuple[int, ...] = ()
    pagination: str = "none"
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        method = str(self.method or "").strip().upper()
        if not _CONTRACT_ID_RE.fullmatch(self.contract_id):
            raise ValueError("Endpoint Contract ID 格式无效")
        if not _OPERATION_ID_RE.fullmatch(self.operation_id):
            raise ValueError("Endpoint operation_id 格式无效")
        if not _OPERATION_ID_RE.fullmatch(self.client_function):
            raise ValueError("Endpoint client_function 格式无效")
        if method not in _HTTP_METHODS:
            raise ValueError("Endpoint HTTP method 不受支持")
        if not self.path.startswith("/api/"):
            raise ValueError("Endpoint path 必须是完整 API 路径")
        if not self.owner_module.strip():
            raise ValueError("Endpoint owner_module 不能为空")
        if not self.response_schema.strip():
            raise ValueError("Endpoint response_schema 不能为空")
        if self.pagination not in {
            "none",
            "limit",
            "page_limit",
            "cursor_limit",
        }:
            raise ValueError("Endpoint pagination 合同无效")
        normalized_errors = tuple(
            dict.fromkeys(int(status) for status in self.error_statuses)
        )
        if any(status < 400 or status > 599 for status in normalized_errors):
            raise ValueError("Endpoint error status 必须是 4xx/5xx")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "error_statuses", normalized_errors)

    @property
    def registry_namespace(self) -> str:
        return "endpoint_contract"

    @property
    def registry_id(self) -> str:
        return self.contract_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "version": self.version,
            "operation_id": self.operation_id,
            "owner_module": self.owner_module,
            "method": self.method,
            "path": self.path,
            "client_function": self.client_function,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "error_statuses": list(self.error_statuses),
            "pagination": self.pagination,
        }


def stable_operation_id(route: APIRoute) -> str:
    """为未显式登记的兼容端点生成与声明顺序无关的 ID。"""

    methods = sorted(
        method.lower()
        for method in route.methods
        if method not in {"HEAD", "OPTIONS"}
    )
    method = methods[0] if methods else "call"
    path = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        route.path_format.strip("/"),
    ).strip("_")
    name = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        str(route.name or "endpoint"),
    ).strip("_")
    return f"{method}_{path or 'root'}_{name}"[:128]


def _stable_openapi_operation_id(method: str, path: str) -> str:
    path_part = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        str(path or "").strip("/"),
    ).strip("_")
    return f"{method.lower()}_{path_part or 'root'}"


def _compatibility_response_schema() -> dict[str, object]:
    return {
        "description": (
            "尚未迁移到类型化 Endpoint Contract 的兼容响应；"
            "运行时媒体类型和正文保持原端点语义。"
        )
    }


def _api_error_schema() -> dict[str, object]:
    return ApiErrorResponse.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )


def _response_schema_ref(response: Mapping[str, object]) -> str:
    content = response.get("content")
    if not isinstance(content, Mapping):
        return ""
    for media in content.values():
        if not isinstance(media, Mapping):
            continue
        schema = media.get("schema")
        if not isinstance(schema, Mapping):
            continue
        ref = schema.get("$ref")
        if isinstance(ref, str):
            return ref.rsplit("/", 1)[-1]
    return ""


def _operation_success_schema_ref(
    operation: Mapping[str, object],
) -> str:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping):
        return ""
    for status, response in responses.items():
        if not str(status).startswith("2"):
            continue
        if isinstance(response, Mapping):
            ref = _response_schema_ref(response)
            if ref:
                return ref
    return ""


def _request_schema_ref(operation: Mapping[str, object]) -> str:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, Mapping):
        return ""
    return _response_schema_ref(request_body)


def _ensure_success_schema(operation: dict[str, object]) -> None:
    responses = operation.setdefault("responses", {})
    assert isinstance(responses, dict)
    success_statuses = [
        status
        for status in responses
        if str(status).startswith("2")
    ]
    if not success_statuses:
        responses["200"] = {
            "description": "Successful Response",
            "content": {
                "*/*": {
                    "schema": {
                        "$ref": (
                            "#/components/schemas/"
                            "CompatibilityResponse"
                        )
                    }
                }
            },
        }
        return
    for status in success_statuses:
        response = responses[status]
        if not isinstance(response, dict):
            continue
        content = response.get("content")
        if not isinstance(content, dict) or not content:
            response["content"] = {
                "*/*": {
                    "schema": {
                        "$ref": (
                            "#/components/schemas/"
                            "CompatibilityResponse"
                        )
                    }
                }
            }
            continue
        for media in content.values():
            if not isinstance(media, dict):
                continue
            if not media.get("schema"):
                media["schema"] = {
                    "$ref": (
                        "#/components/schemas/"
                        "CompatibilityResponse"
                    )
                }


def _ensure_default_error_schema(operation: dict[str, object]) -> None:
    responses = operation.setdefault("responses", {})
    assert isinstance(responses, dict)
    responses.setdefault(
        "default",
        {
            "description": "稳定错误响应",
            "content": {
                "application/json": {
                    "schema": {
                        "$ref": (
                            "#/components/schemas/"
                            "ApiErrorResponse"
                        )
                    }
                }
            },
        },
    )


def _pagination_kind(operation: Mapping[str, object]) -> str:
    parameters = operation.get("parameters")
    if not isinstance(parameters, list):
        return "none"
    names = {
        str(item.get("name") or "")
        for item in parameters
        if isinstance(item, Mapping)
        and item.get("in") == "query"
    }
    if {"cursor", "limit"} <= names:
        return "cursor_limit"
    if {"page", "limit"} <= names:
        return "page_limit"
    if "limit" in names:
        return "limit"
    return "none"


def normalize_openapi_schema(
    schema: dict[str, object],
    descriptors: Iterable[EndpointContractDescriptor],
) -> dict[str, object]:
    """补齐全局兼容合同，并验证类型化 Endpoint Descriptor。"""

    descriptor_by_route = {
        (descriptor.path, descriptor.method.lower()): descriptor
        for descriptor in descriptors
    }
    components = schema.setdefault("components", {})
    assert isinstance(components, dict)
    schemas = components.setdefault("schemas", {})
    assert isinstance(schemas, dict)
    schemas["ApiErrorResponse"] = _api_error_schema()
    schemas["CompatibilityResponse"] = (
        _compatibility_response_schema()
    )

    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise RuntimeError("OpenAPI paths 缺失")
    operation_ids: set[str] = set()
    seen_contracts: set[str] = set()

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if (
                method not in _OPENAPI_METHODS
                or not isinstance(operation, dict)
            ):
                continue
            descriptor = descriptor_by_route.get((path, method))
            if descriptor is None:
                operation["operationId"] = (
                    _stable_openapi_operation_id(method, path)
                )
            operation_id = str(operation.get("operationId") or "")
            if not operation_id:
                raise RuntimeError(f"{method.upper()} {path} 缺 operation_id")
            if operation_id in operation_ids:
                raise RuntimeError(f"重复 operation_id：{operation_id}")
            operation_ids.add(operation_id)
            _ensure_success_schema(operation)
            _ensure_default_error_schema(operation)

            if descriptor is None:
                operation["x-nanobot-contract-lifecycle"] = (
                    "compatibility"
                )
                operation["x-nanobot-pagination"] = _pagination_kind(
                    operation
                )
                continue
            if operation_id != descriptor.operation_id:
                raise RuntimeError(
                    f"{descriptor.contract_id} operation_id 不匹配："
                    f"{operation_id}"
                )
            if (
                _operation_success_schema_ref(operation)
                != descriptor.response_schema
            ):
                raise RuntimeError(
                    f"{descriptor.contract_id} response schema 不匹配"
                )
            if descriptor.request_schema and (
                _request_schema_ref(operation)
                != descriptor.request_schema
            ):
                raise RuntimeError(
                    f"{descriptor.contract_id} request schema 不匹配"
                )
            responses = operation["responses"]
            assert isinstance(responses, Mapping)
            missing_errors = [
                status
                for status in descriptor.error_statuses
                if str(status) not in responses
            ]
            if missing_errors:
                raise RuntimeError(
                    f"{descriptor.contract_id} 缺少错误响应："
                    f"{missing_errors}"
                )
            operation["x-nanobot-endpoint-contract-id"] = (
                descriptor.contract_id
            )
            operation["x-nanobot-contract-lifecycle"] = "typed"
            operation["x-nanobot-pagination"] = descriptor.pagination
            seen_contracts.add(descriptor.contract_id)

    expected_contracts = {
        descriptor.contract_id
        for descriptor in descriptor_by_route.values()
    }
    missing_contracts = sorted(expected_contracts - seen_contracts)
    if missing_contracts:
        raise RuntimeError(
            f"Endpoint Contract 未绑定路由：{missing_contracts}"
        )
    return schema


def install_openapi_contracts(
    app: FastAPI,
    descriptors: Iterable[EndpointContractDescriptor],
) -> None:
    """安装确定性的 OpenAPI 生成器；不改变 HTTP 运行时控制流。"""

    registry_generation = getattr(descriptors, "generation", None)
    registry_sha256 = getattr(descriptors, "sha256", "")
    frozen_descriptors = tuple(descriptors)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        normalized = normalize_openapi_schema(
            schema,
            frozen_descriptors,
        )
        if (
            isinstance(registry_generation, int)
            and registry_generation > 0
            and isinstance(registry_sha256, str)
            and registry_sha256
        ):
            normalized["x-nanobot-endpoint-registry"] = {
                "generation": registry_generation,
                "sha256": registry_sha256,
            }
        app.openapi_schema = normalized
        return app.openapi_schema

    app.openapi = custom_openapi


__all__ = [
    "ApiErrorResponse",
    "EndpointContractDescriptor",
    "install_openapi_contracts",
    "normalize_openapi_schema",
    "stable_operation_id",
    "standard_error_responses",
]
