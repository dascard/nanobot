from __future__ import annotations

from dataclasses import fields
import hashlib

import pytest

from core.agent_runtime import (
    InMemoryMcpProvider,
    InMemorySkillProvider,
    McpProviderPort,
    RegisteredToolExecutionPort,
    RuntimeMcpSnapshot,
    RuntimeMcpToolDescriptor,
    RuntimeOwnerType,
    RuntimePrincipal,
    RuntimeSkillContent,
    RuntimeSkillDescriptor,
    RuntimeSkillScope,
    RuntimeSkillSnapshot,
    SkillProviderPort,
)


def _owner(owner_id: str = "owner-1") -> RuntimePrincipal:
    return RuntimePrincipal("qq", RuntimeOwnerType.USER, owner_id)


def _skill(
    skill_id: str,
    document: bytes,
    *,
    scope: RuntimeSkillScope,
    version: str = "1",
) -> RuntimeSkillContent:
    descriptor = RuntimeSkillDescriptor(
        provider_id="skills",
        skill_id=skill_id,
        scope=scope,
        version=version,
        description=f"{skill_id} 描述",
        content_sha256=hashlib.sha256(document).hexdigest(),
        dependencies=("base-runtime",),
        required_permissions=("workspace.read",),
    )
    return RuntimeSkillContent(descriptor=descriptor, document=document)


@pytest.mark.asyncio
async def test_skill_provider_uses_content_pins_and_owner_scoped_disclosure():
    builtin = _skill(
        "builtin-guide",
        b"# Builtin\n",
        scope=RuntimeSkillScope.BUILTIN,
    )
    private = _skill(
        "private-guide",
        "# 私有技能\n".encode(),
        scope=RuntimeSkillScope.USER,
    )
    owner = _owner()
    provider = InMemorySkillProvider(
        "skills",
        revision="revision-1",
        builtin_contents=(builtin,),
        owner_contents={owner.canonical_id: (private,)},
    )

    assert isinstance(provider, SkillProviderPort)
    snapshot = await provider.snapshot(owner=owner)
    other_snapshot = await provider.snapshot(owner=_owner("owner-2"))

    assert [item.skill_id for item in snapshot.skills] == [
        "builtin-guide",
        "private-guide",
    ]
    assert [item.skill_id for item in other_snapshot.skills] == ["builtin-guide"]
    assert snapshot.snapshot_sha256 != other_snapshot.snapshot_sha256
    assert (await provider.load(private.descriptor, owner=owner)).document.startswith(
        b"#"
    )
    with pytest.raises(PermissionError, match="owner 未授权"):
        await provider.load(private.descriptor, owner=_owner("owner-2"))


def test_skill_snapshot_rejects_ambiguous_active_versions_and_content_drift():
    first = _skill(
        "versioned-guide",
        b"# v1\n",
        scope=RuntimeSkillScope.PROJECT,
        version="1",
    )
    second = _skill(
        "versioned-guide",
        b"# v2\n",
        scope=RuntimeSkillScope.PROJECT,
        version="2",
    )

    with pytest.raises(ValueError, match="多个同 scope"):
        RuntimeSkillSnapshot(
            provider_id="skills",
            revision="revision-1",
            skills=(first.descriptor, second.descriptor),
        )
    with pytest.raises(ValueError, match="content_sha256 不匹配"):
        RuntimeSkillContent(descriptor=first.descriptor, document=b"changed")


def _mcp_tool(server_id: str, *, description: str = "") -> RuntimeMcpToolDescriptor:
    return RuntimeMcpToolDescriptor(
        provider_id="mcp",
        server_id=server_id,
        tool_name="search",
        input_schema_json=(
            b'{"type":"object","properties":{"query":{"type":"string"}}}'
        ),
        execution_port_id=f"{server_id}/search",
        description=description,
    )


@pytest.mark.asyncio
async def test_mcp_provider_keeps_server_namespace_schema_pin_and_execution_port():
    first = _mcp_tool("server-a", description="搜索 A")
    second = _mcp_tool("server-b", description="搜索 B")
    execution_port = RegisteredToolExecutionPort({})
    provider = InMemoryMcpProvider(
        "mcp",
        revision="revision-1",
        tools=(second, first),
        tool_execution_port=execution_port,
    )

    assert isinstance(provider, McpProviderPort)
    snapshot = await provider.snapshot(owner=_owner())
    assert [item.qualified_name for item in snapshot.tools] == [
        "server-a:search",
        "server-b:search",
    ]
    assert snapshot.snapshot_sha256
    assert (
        first.input_schema_sha256 == hashlib.sha256(first.input_schema_json).hexdigest()
    )
    assert provider.tool_execution_port is execution_port


def test_mcp_snapshot_rejects_same_server_collision_and_invalid_schema():
    first = _mcp_tool("server-a", description="first")
    duplicate = _mcp_tool("server-a", description="second")
    with pytest.raises(ValueError, match="重名工具"):
        RuntimeMcpSnapshot(
            provider_id="mcp",
            revision="revision-1",
            tools=(first, duplicate),
        )
    with pytest.raises(ValueError, match="顶层必须是对象"):
        RuntimeMcpToolDescriptor(
            provider_id="mcp",
            server_id="server-a",
            tool_name="invalid",
            input_schema_json=b"[]",
            execution_port_id="server-a/invalid",
        )


def test_extension_contract_does_not_embed_transport_endpoint_or_credentials():
    contract_fields = {
        field.name
        for contract in (
            RuntimeSkillDescriptor,
            RuntimeMcpToolDescriptor,
            RuntimeMcpSnapshot,
        )
        for field in fields(contract)
    }

    assert contract_fields.isdisjoint(
        {
            "credential",
            "endpoint",
            "secret",
            "transport",
        }
    )
