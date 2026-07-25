#!/usr/bin/env python3
"""导出规范化 OpenAPI，并生成首批 Admin TypeScript Client。"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPENAPI_OUTPUT = ROOT / "docs" / "api" / "openapi.v1.json"
TYPESCRIPT_OUTPUT = (
    ROOT / "webui" / "src" / "api" / "generated" / "adminClient.ts"
)
ADMIN_API_PREFIX = "/api/v1/admin"
_REF_PREFIX = "#/components/schemas/"
_TS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True, slots=True)
class RenderedArtifacts:
    openapi: str
    typescript: str


def _schema_ref_name(schema: Mapping[str, object]) -> str:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith(_REF_PREFIX):
        return ""
    return ref.removeprefix(_REF_PREFIX)


def _json_literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ts_property_name(name: str) -> str:
    return name if _TS_IDENTIFIER_RE.fullmatch(name) else _json_literal(name)


def _ts_type(
    schema: object,
    *,
    indent: int = 0,
) -> str:
    if not isinstance(schema, Mapping):
        return "unknown"
    ref_name = _schema_ref_name(schema)
    if ref_name:
        return ref_name
    if "const" in schema:
        return _json_literal(schema["const"])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(_json_literal(item) for item in enum)
    for union_key, separator in (
        ("anyOf", " | "),
        ("oneOf", " | "),
        ("allOf", " & "),
    ):
        variants = schema.get(union_key)
        if isinstance(variants, list) and variants:
            return separator.join(
                f"({_ts_type(item, indent=indent)})"
                for item in variants
            )

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(
            _ts_type({"type": item}, indent=indent)
            for item in schema_type
        )
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        return f"Array<{_ts_type(schema.get('items'), indent=indent)}>"

    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    if isinstance(properties, Mapping):
        required = {
            str(item)
            for item in schema.get("required", [])
            if isinstance(item, str)
        }
        prefix = " " * indent
        child_prefix = " " * (indent + 2)
        lines = ["{"]
        for name in sorted(properties):
            optional = "" if name in required else "?"
            lines.append(
                f"{child_prefix}{_ts_property_name(str(name))}"
                f"{optional}: "
                f"{_ts_type(properties[name], indent=indent + 2)}"
            )
        if additional is True:
            lines.append(f"{child_prefix}[key: string]: unknown")
        elif isinstance(additional, Mapping):
            lines.append(
                f"{child_prefix}[key: string]: "
                f"{_ts_type(additional, indent=indent + 2)}"
            )
        lines.append(f"{prefix}}}")
        return "\n".join(lines)
    if schema_type == "object" or additional is not None:
        if isinstance(additional, Mapping):
            return f"Record<string, {_ts_type(additional, indent=indent)}>"
        return "Record<string, unknown>"
    return "unknown"


def _collect_schema_refs(
    schema: object,
    *,
    components: Mapping[str, object],
    collected: set[str],
) -> None:
    if isinstance(schema, list):
        for item in schema:
            _collect_schema_refs(
                item,
                components=components,
                collected=collected,
            )
        return
    if not isinstance(schema, Mapping):
        return
    ref_name = _schema_ref_name(schema)
    if ref_name:
        if ref_name in collected:
            return
        collected.add(ref_name)
        _collect_schema_refs(
            components.get(ref_name),
            components=components,
            collected=collected,
        )
        return
    for value in schema.values():
        _collect_schema_refs(
            value,
            components=components,
            collected=collected,
        )


def _operation(
    schema: Mapping[str, object],
    *,
    path: str,
    method: str,
) -> Mapping[str, object]:
    paths = schema.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("OpenAPI paths 缺失")
    path_item = paths.get(path)
    if not isinstance(path_item, Mapping):
        raise RuntimeError(f"OpenAPI 路径不存在：{path}")
    operation = path_item.get(method.lower())
    if not isinstance(operation, Mapping):
        raise RuntimeError(f"OpenAPI method 不存在：{method} {path}")
    return operation


def _json_content_schema(container: object) -> Mapping[str, object]:
    if not isinstance(container, Mapping):
        return {}
    content = container.get("content")
    if not isinstance(content, Mapping):
        return {}
    media = content.get("application/json")
    if not isinstance(media, Mapping):
        return {}
    schema = media.get("schema")
    return schema if isinstance(schema, Mapping) else {}


def _success_schema(operation: Mapping[str, object]) -> Mapping[str, object]:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping):
        return {}
    for status, response in responses.items():
        if str(status).startswith("2"):
            schema = _json_content_schema(response)
            if schema:
                return schema
    return {}


def _request_schema(operation: Mapping[str, object]) -> Mapping[str, object]:
    return _json_content_schema(operation.get("requestBody"))


def _parameter_fields(
    operation: Mapping[str, object],
) -> list[tuple[str, str, bool, str]]:
    parameters = operation.get("parameters")
    if not isinstance(parameters, list):
        return []
    fields: list[tuple[str, str, bool, str]] = []
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            continue
        location = str(parameter.get("in") or "")
        if location not in {"path", "query"}:
            continue
        name = str(parameter.get("name") or "")
        if not name:
            continue
        fields.append((
            name,
            _ts_type(parameter.get("schema")),
            bool(parameter.get("required")) or location == "path",
            location,
        ))
    return fields


def _client_url(path: str, path_fields: set[str]) -> str:
    if not path.startswith(ADMIN_API_PREFIX):
        raise RuntimeError(f"Admin Client 路径前缀无效：{path}")
    relative = path.removeprefix(ADMIN_API_PREFIX) or "/"
    for name in path_fields:
        relative = relative.replace(
            "{" + name + "}",
            "${encodeURIComponent(String(args."
            + name
            + "))}",
        )
    return f"`{relative}`"


def _render_client_function(
    descriptor: object,
    operation: Mapping[str, object],
) -> str:
    fields = _parameter_fields(operation)
    request_schema = _request_schema(operation)
    request_type = _ts_type(request_schema)
    if request_schema:
        fields.append(("body", request_type, True, "body"))
    response_type = _ts_type(_success_schema(operation))

    lines = [
        f"/** Endpoint Contract: {descriptor.contract_id} */",
        f"export async function {descriptor.client_function}(",
    ]
    if fields:
        all_optional = not any(required for _, _, required, _ in fields)
        lines.append("  args: {")
        for name, field_type, required, _ in fields:
            optional = "" if required else "?"
            lines.append(
                f"    {_ts_property_name(name)}{optional}: {field_type}"
            )
        lines.append(f"  }}{' = {}' if all_optional else ''},")
    lines.append(f"): Promise<{response_type}> {{")
    path_fields = {
        name
        for name, _, _, location in fields
        if location == "path"
    }
    query_fields = [
        name
        for name, _, _, location in fields
        if location == "query"
    ]
    lines.extend([
        f"  const response = await api.request<{response_type}>({{",
        f"    method: '{descriptor.method}',",
        f"    url: {_client_url(descriptor.path, path_fields)},",
    ])
    if query_fields:
        lines.append("    params: {")
        for name in query_fields:
            lines.append(f"      {name}: args.{name},")
        lines.append("    },")
    if request_schema:
        lines.append("    data: args.body,")
    lines.extend([
        "  })",
        "  return response.data",
        "}",
    ])
    return "\n".join(lines)


def render_typescript(schema: Mapping[str, object]) -> str:
    from api.admin.endpoint_registry import (
        ADMIN_ENDPOINT_CONTRACT_REGISTRY,
    )

    descriptors = tuple(
        ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot
    )
    components_root = schema.get("components")
    components = (
        components_root.get("schemas", {})
        if isinstance(components_root, Mapping)
        else {}
    )
    if not isinstance(components, Mapping):
        raise RuntimeError("OpenAPI components.schemas 缺失")

    collected: set[str] = {"ApiErrorResponse"}
    operations: dict[str, Mapping[str, object]] = {}
    for descriptor in descriptors:
        operation = _operation(
            schema,
            path=descriptor.path,
            method=descriptor.method,
        )
        operations[descriptor.contract_id] = operation
        _collect_schema_refs(
            _request_schema(operation),
            components=components,
            collected=collected,
        )
        _collect_schema_refs(
            _success_schema(operation),
            components=components,
            collected=collected,
        )
    for name in tuple(collected):
        _collect_schema_refs(
            components.get(name),
            components=components,
            collected=collected,
        )

    lines = [
        "/* eslint-disable */",
        "/* 此文件由 scripts/generate_openapi_client.py 生成；请勿手工修改。 */",
        "",
        "import { api } from '../client'",
        "",
    ]
    for name in sorted(collected):
        component = components.get(name)
        if not isinstance(component, Mapping):
            raise RuntimeError(f"OpenAPI Schema 不存在：{name}")
        lines.extend([
            f"export type {name} = {_ts_type(component)}",
            "",
        ])
    for descriptor in descriptors:
        lines.extend([
            _render_client_function(
                descriptor,
                operations[descriptor.contract_id],
            ),
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def render_artifacts() -> RenderedArtifacts:
    from server import app

    schema = app.openapi()
    openapi = (
        json.dumps(
            schema,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return RenderedArtifacts(
        openapi=openapi,
        typescript=render_typescript(schema),
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def write_artifacts(rendered: RenderedArtifacts) -> None:
    _atomic_write(OPENAPI_OUTPUT, rendered.openapi)
    _atomic_write(TYPESCRIPT_OUTPUT, rendered.typescript)


def check_artifacts(rendered: RenderedArtifacts) -> bool:
    expected = {
        OPENAPI_OUTPUT: rendered.openapi,
        TYPESCRIPT_OUTPUT: rendered.typescript,
    }
    stale = [
        path
        for path, content in expected.items()
        if not path.is_file()
        or path.read_text(encoding="utf-8") != content
    ]
    if stale:
        print("OpenAPI／TypeScript 生成物缺失或已漂移：", file=sys.stderr)
        for path in stale:
            print(f"- {path.relative_to(ROOT)}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成或检查 OpenAPI 与 Admin TypeScript Client"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args()

    rendered = render_artifacts()
    if args.write:
        write_artifacts(rendered)
        return 0
    return 0 if check_artifacts(rendered) else 1


if __name__ == "__main__":
    raise SystemExit(main())
