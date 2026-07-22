"""LLM foundation 依赖方向与兼容入口契约。"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_CLIENT_HELPERS = {
    "core.final_tools",
    "core.llm_request_sanitizer",
    "core.llm_stream_trace",
    "core.model_route_options",
    "core.safe_diagnostics",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_gateway_clients_do_not_import_core_business_helpers():
    for relative in ("clients/new_api_client.py", "clients/provider_adapter.py"):
        imports = _imports(ROOT / relative)
        assert imports.isdisjoint(FORBIDDEN_CLIENT_HELPERS), (
            relative,
            sorted(imports & FORBIDDEN_CLIENT_HELPERS),
        )


def test_llm_foundation_has_no_business_or_adapter_dependencies():
    for path in sorted((ROOT / "foundation" / "llm").glob("*.py")):
        imports = _imports(path)
        forbidden = {
            name
            for name in imports
            if name == "core"
            or name.startswith("core.")
            or name == "clients"
            or name.startswith("clients.")
            or name == "api"
            or name.startswith("api.")
        }
        assert not forbidden, (path.name, sorted(forbidden))


def test_core_compatibility_entries_share_foundation_implementations():
    from clients.new_api_client import (
        format_openai_messages as client_format_messages,
    )
    from core.final_tools import filter_payload_tools as core_filter
    from core.llm_request_sanitizer import (
        sanitize_payload_messages as core_sanitize,
    )
    from core.llm_stream_trace import LLMStreamTraceAccumulator as CoreAccumulator
    from core.model_route_options import normalize_enable_thinking as core_normalize
    from core.safe_diagnostics import safe_response_summary as core_safe_summary
    from foundation.llm.model_options import normalize_enable_thinking
    from foundation.llm.messages import format_openai_messages
    from foundation.llm.request_sanitizer import sanitize_payload_messages
    from foundation.llm.safe_diagnostics import safe_response_summary
    from foundation.llm.stream_trace import LLMStreamTraceAccumulator
    from foundation.llm.tool_policy import filter_payload_tools

    assert core_filter is filter_payload_tools
    assert core_sanitize is sanitize_payload_messages
    assert CoreAccumulator is LLMStreamTraceAccumulator
    assert core_normalize is normalize_enable_thinking
    assert core_safe_summary is safe_response_summary
    assert client_format_messages is format_openai_messages
