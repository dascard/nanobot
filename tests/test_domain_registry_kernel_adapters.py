from __future__ import annotations


def test_registry_kernel_accepts_namespaced_resource_id():
    from dataclasses import dataclass

    from core.registry import RegistryBuilder

    @dataclass(frozen=True, slots=True)
    class ResourceDescriptor:
        registry_namespace: str
        registry_id: str
        registry_dependencies: tuple[str, ...] = ()

        def registry_payload(self):
            return {"id": self.registry_id}

    snapshot = (
        RegistryBuilder[ResourceDescriptor]("resource")
        .register(
            ResourceDescriptor(
                registry_namespace="resource",
                registry_id="tasks/private_decision",
            )
        )
        .freeze()
    )

    assert snapshot.ordered_ids == ("tasks/private_decision",)


def test_model_provider_registry_exposes_kernel_snapshot():
    from core.model_provider import (
        ModelProviderRegistry,
        ProviderAvailability,
        ProviderCapability,
        ProviderDescriptor,
    )
    from core.registry import RegistrySnapshot

    class Provider:
        descriptor = ProviderDescriptor(
            id="local",
            display_name="Local",
            capabilities=frozenset(
                {ProviderCapability.CHAT_COMPLETION}
            ),
        )

        def availability(self):
            return ProviderAvailability(
                available=True,
                configured=True,
                reason_code="configured",
            )

        def introspect(self):
            return {}

    registry = ModelProviderRegistry()
    registry.register(Provider())
    registry.freeze()

    snapshot = registry.registry_snapshot
    assert isinstance(snapshot, RegistrySnapshot)
    assert snapshot.namespace == "model_provider"
    assert snapshot.generation == 1
    assert snapshot.require("local") is Provider.descriptor
    assert len(snapshot.sha256) == 64


def test_memory_provider_registry_exposes_kernel_snapshot():
    from core.memory_provider import (
        FakeMemoryProvider,
        MemoryProviderDescriptor,
        MemoryProviderRegistry,
    )
    from core.registry import RegistrySnapshot

    descriptor = MemoryProviderDescriptor(
        id="semantic",
        display_name="Semantic",
        capabilities=frozenset({"prefetch"}),
    )
    registry = MemoryProviderRegistry()
    registry.register(
        "semantic",
        descriptor,
        lambda: FakeMemoryProvider(descriptor),
    )
    registry.freeze()

    snapshot = registry.registry_snapshot
    assert isinstance(snapshot, RegistrySnapshot)
    assert snapshot.namespace == "memory_provider"
    assert snapshot.require("semantic") is descriptor
    assert snapshot.generation == 1


def test_static_domain_registries_share_snapshot_generation_and_hash():
    from core.prompt_v2.section_descriptors import (
        PROMPT_SECTION_REGISTRY,
    )
    from core.prompt_v2.task_contracts import (
        task_contract_registry_kernel_snapshot,
    )
    from core.registry import RegistrySnapshot
    from core.runtime.event_registry import RUNTIME_EVENT_REGISTRY
    from core.tool_registry import TOOL_DESCRIPTOR_REGISTRY

    snapshots = (
        TOOL_DESCRIPTOR_REGISTRY.registry_snapshot,
        PROMPT_SECTION_REGISTRY,
        task_contract_registry_kernel_snapshot(),
        RUNTIME_EVENT_REGISTRY.registry_snapshot,
    )

    assert all(
        isinstance(snapshot, RegistrySnapshot)
        for snapshot in snapshots
    )
    assert [snapshot.namespace for snapshot in snapshots] == [
        "tool",
        "prompt_section",
        "task_contract",
        "runtime_event",
    ]
    assert all(snapshot.generation == 1 for snapshot in snapshots)
    assert all(len(snapshot.sha256) == 64 for snapshot in snapshots)
    assert (
        snapshots[1].require("current_user_event").section_id
        == "current_user_event"
    )
    assert (
        snapshots[2]
        .require("tasks/private_decision")
        .task_key
        == "tasks/private_decision"
    )
    assert (
        snapshots[3].require("prompt.compile").name
        == "prompt.compile"
    )


def test_domain_registry_hashes_are_stable_across_registration_order():
    from core.runtime.events import (
        RuntimeEventDescriptor,
        RuntimeEventRegistry,
    )

    first = RuntimeEventDescriptor(
        name="test.first",
        domain="test",
        phases=("started",),
    )
    second = RuntimeEventDescriptor(
        name="test.second",
        domain="test",
        phases=("started",),
    )

    forward = RuntimeEventRegistry((first, second)).freeze()
    reverse = RuntimeEventRegistry((second, first)).freeze()

    assert (
        forward.registry_snapshot.sha256
        == reverse.registry_snapshot.sha256
    )
    assert forward.registry_snapshot.ordered_ids == (
        "test.first",
        "test.second",
    )
