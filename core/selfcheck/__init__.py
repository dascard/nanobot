"""Nanobot 自检能力清单与执行引擎。"""

from core.selfcheck.capabilities import (
    RAG_CAPABILITY_SOURCES,
    WORKER_CAPABILITY_SOURCES,
    CapabilityDescriptor,
    build_capability_registry,
    capability_coverage_summary,
)


__all__ = [
    "RAG_CAPABILITY_SOURCES",
    "WORKER_CAPABILITY_SOURCES",
    "CapabilityDescriptor",
    "build_capability_registry",
    "capability_coverage_summary",
]
