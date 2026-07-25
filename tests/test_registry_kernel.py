from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from threading import Event, Thread

import pytest

from core.registry import (
    RegistryBuilder,
    RegistryConflictError,
    RegistryDependencyError,
    RegistryFrozenError,
    RegistryGeneration,
    RegistryPublishConflictError,
    RegistryValidationError,
)


@dataclass(frozen=True, slots=True)
class ExampleDescriptor:
    registry_namespace: str
    registry_id: str
    registry_dependencies: tuple[str, ...] = ()
    label: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "label": self.label,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class MutableDescriptor:
    registry_namespace: str
    registry_id: str
    registry_dependencies: tuple[str, ...] = ()

    def registry_payload(self) -> dict[str, object]:
        return {}


def _descriptor(
    descriptor_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    label: str = "",
    metadata: tuple[tuple[str, str], ...] = (),
) -> ExampleDescriptor:
    return ExampleDescriptor(
        registry_namespace="example",
        registry_id=descriptor_id,
        registry_dependencies=dependencies,
        label=label,
        metadata=metadata,
    )


@pytest.mark.parametrize(
    ("namespace", "descriptor_id"),
    [
        ("Example", "valid"),
        ("example", "Uppercase"),
        ("example", "contains space"),
        ("example", "../escape"),
        ("example", ""),
    ],
)
def test_registry_rejects_invalid_namespace_or_id(
    namespace: str,
    descriptor_id: str,
):
    if namespace != "example":
        with pytest.raises(RegistryValidationError):
            RegistryBuilder[ExampleDescriptor](namespace)
        return

    builder = RegistryBuilder[ExampleDescriptor](namespace)
    with pytest.raises(RegistryValidationError):
        builder.register(
            ExampleDescriptor(
                registry_namespace=namespace,
                registry_id=descriptor_id,
            )
        )


def test_registry_rejects_descriptor_from_another_namespace():
    builder = RegistryBuilder[ExampleDescriptor]("example")

    with pytest.raises(RegistryValidationError, match="namespace"):
        builder.register(
            ExampleDescriptor(
                registry_namespace="another",
                registry_id="entry",
            )
        )


def test_registry_rejects_duplicate_id_without_override_path():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("entry", label="first"))

    with pytest.raises(RegistryConflictError, match="entry"):
        builder.register(_descriptor("entry", label="second"))


def test_registry_rejects_mutable_descriptor():
    builder = RegistryBuilder[MutableDescriptor]("example")

    with pytest.raises(RegistryValidationError, match="不可变"):
        builder.register(
            MutableDescriptor(
                registry_namespace="example",
                registry_id="mutable",
            )
        )


def test_registry_rejects_missing_dependency():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("consumer", dependencies=("missing",)))

    with pytest.raises(RegistryDependencyError, match="missing"):
        builder.freeze()


def test_registry_rejects_dependency_cycle():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("first", dependencies=("second",)))
    builder.register(_descriptor("second", dependencies=("first",)))

    with pytest.raises(RegistryDependencyError, match="循环"):
        builder.freeze()


def test_registry_can_finish_build_after_missing_dependency_is_added():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("consumer", dependencies=("provider",)))

    with pytest.raises(RegistryDependencyError):
        builder.freeze()

    builder.register(_descriptor("provider"))
    snapshot = builder.freeze()
    assert snapshot.ordered_ids == ("provider", "consumer")


def test_registry_resolves_dependencies_before_consumers_deterministically():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("leaf", dependencies=("root",)))
    builder.register(_descriptor("independent"))
    builder.register(_descriptor("root"))

    snapshot = builder.freeze()

    assert snapshot.ordered_ids == ("independent", "root", "leaf")
    assert tuple(item.registry_id for item in snapshot) == snapshot.ordered_ids


def test_registry_freeze_rejects_later_registration():
    builder = RegistryBuilder[ExampleDescriptor]("example")
    builder.register(_descriptor("entry"))
    snapshot = builder.freeze()

    with pytest.raises(RegistryFrozenError):
        builder.register(_descriptor("late"))

    assert builder.freeze() is snapshot


def test_registry_snapshot_and_descriptors_are_immutable():
    descriptor = _descriptor("entry", metadata=(("owner", "core"),))
    snapshot = (
        RegistryBuilder[ExampleDescriptor]("example")
        .register(descriptor)
        .freeze()
    )

    with pytest.raises(TypeError):
        snapshot.items["other"] = descriptor  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        snapshot.generation = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        descriptor.label = "changed"  # type: ignore[misc]

    assert snapshot.require("entry") is descriptor
    with pytest.raises(KeyError):
        snapshot.require("missing")


def test_registry_hash_does_not_depend_on_registration_order():
    descriptors = (
        _descriptor("root", label="根"),
        _descriptor(
            "leaf",
            dependencies=("root",),
            label="叶",
            metadata=(("z", "末"), ("a", "首")),
        ),
    )
    first = RegistryBuilder[ExampleDescriptor]("example")
    second = RegistryBuilder[ExampleDescriptor]("example")
    for descriptor in descriptors:
        first.register(descriptor)
    for descriptor in reversed(descriptors):
        second.register(descriptor)

    first_snapshot = first.freeze(generation=3)
    second_snapshot = second.freeze(generation=8)

    assert first_snapshot.sha256 == second_snapshot.sha256
    assert first_snapshot.canonical_json == second_snapshot.canonical_json
    assert first_snapshot.generation == 3
    assert second_snapshot.generation == 8


def test_registry_hash_changes_when_descriptor_payload_changes():
    first = (
        RegistryBuilder[ExampleDescriptor]("example")
        .register(_descriptor("entry", label="before"))
        .freeze()
    )
    second = (
        RegistryBuilder[ExampleDescriptor]("example")
        .register(_descriptor("entry", label="after"))
        .freeze()
    )

    assert first.sha256 != second.sha256


def test_registry_generation_publishes_only_complete_candidate():
    generation = RegistryGeneration[ExampleDescriptor]("example")
    first = generation.rebuild(
        lambda builder: builder.register(_descriptor("stable", label="v1"))
    )

    def fail_during_build(builder: RegistryBuilder[ExampleDescriptor]) -> None:
        builder.register(_descriptor("partial", label="v2"))
        raise RuntimeError("candidate build failed")

    with pytest.raises(RuntimeError, match="candidate build failed"):
        generation.rebuild(fail_during_build)

    assert generation.current is first
    assert generation.current is not None
    assert generation.current.generation == 1
    assert generation.current.ordered_ids == ("stable",)


def test_registry_generation_keeps_previous_snapshot_on_validation_failure():
    generation = RegistryGeneration[ExampleDescriptor]("example")
    first = generation.rebuild(
        lambda builder: builder.register(_descriptor("stable"))
    )

    with pytest.raises(RegistryDependencyError):
        generation.rebuild(
            lambda builder: builder.register(
                _descriptor("broken", dependencies=("missing",))
            )
        )

    assert generation.current is first


def test_registry_generation_increments_while_content_hash_remains_stable():
    generation = RegistryGeneration[ExampleDescriptor]("example")
    first = generation.rebuild(
        lambda builder: builder.register(_descriptor("stable"))
    )
    second = generation.rebuild(
        lambda builder: builder.register(_descriptor("stable"))
    )

    assert first.generation == 1
    assert second.generation == 2
    assert first.sha256 == second.sha256
    assert generation.current is second


def test_registry_generation_rejects_stale_concurrent_candidate():
    generation = RegistryGeneration[ExampleDescriptor]("example")
    generation.rebuild(
        lambda builder: builder.register(_descriptor("stable", label="v1"))
    )
    slow_started = Event()
    release_slow = Event()
    failures: list[BaseException] = []

    def configure_slow(
        builder: RegistryBuilder[ExampleDescriptor],
    ) -> None:
        builder.register(_descriptor("slow", label="stale"))
        slow_started.set()
        assert release_slow.wait(timeout=5)

    def publish_slow() -> None:
        try:
            generation.rebuild(configure_slow)
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=publish_slow)
    thread.start()
    assert slow_started.wait(timeout=5)

    winner = generation.rebuild(
        lambda builder: builder.register(_descriptor("winner", label="v2"))
    )
    release_slow.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], RegistryPublishConflictError)
    assert generation.current is winner
    assert winner.generation == 2
    assert winner.ordered_ids == ("winner",)
