"""基于共享 Registry Kernel 的显式 Composition Root。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import hashlib

from core.modules.contracts import (
    ApplicationModule,
    CompositionState,
    ModuleContributionRef,
    ModuleHealth,
    ModuleManifest,
    ModuleRegistrationPort,
    ModuleRuntimeContext,
)
from core.registry import (
    RegistryBuilder,
    RegistryGeneration,
    RegistrySnapshot,
)
from core.registry.validation import (
    canonical_json,
    validate_identifier,
)


class CompositionError(RuntimeError):
    """Composition Root 稳定错误基类。"""


class CompositionValidationError(CompositionError):
    """模块声明、贡献或能力所有权冲突。"""


class CompositionStartError(CompositionError):
    """模块启动失败；不泄漏底层异常正文。"""


class CompositionStopError(CompositionError):
    """模块停止失败；其他模块仍会继续反向停止。"""


@dataclass(frozen=True, slots=True)
class ModuleContributionDescriptor:
    registry_id: str
    module_id: str
    kind: str
    contribution_id: str
    registry_namespace: str = field(
        default="module_contribution",
        init=False,
    )
    registry_dependencies: tuple[str, ...] = field(
        default=(),
        init=False,
    )

    def registry_payload(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "kind": self.kind,
            "contribution_id": self.contribution_id,
        }


@dataclass(frozen=True, slots=True)
class CompositionSnapshot:
    modules: RegistrySnapshot[ModuleManifest]
    contributions: RegistrySnapshot[
        ModuleContributionDescriptor
    ]
    generation: int
    sha256: str


class _ScopedRegistrationBuilder(ModuleRegistrationPort):
    def __init__(
        self,
        *,
        module_id: str,
        declared: tuple[ModuleContributionRef, ...],
        target: RegistryBuilder[
            ModuleContributionDescriptor
        ],
    ) -> None:
        self.module_id = module_id
        self._declared = {
            (item.kind, item.contribution_id)
            for item in declared
        }
        self._registered: set[tuple[str, str]] = set()
        self._target = target

    def register(
        self,
        kind: str,
        contribution_id: str,
    ) -> None:
        key = (kind, contribution_id)
        if key not in self._declared:
            raise CompositionValidationError(
                f"模块 {self.module_id} 注册了未声明贡献 "
                f"{kind}.{contribution_id}"
            )
        if key in self._registered:
            raise CompositionValidationError(
                f"模块 {self.module_id} 重复注册贡献 "
                f"{kind}.{contribution_id}"
            )
        registry_id = f"{kind}.{contribution_id}"
        validate_identifier(
            registry_id,
            field_name="module contribution registry_id",
        )
        self._target.register(ModuleContributionDescriptor(
            registry_id=registry_id,
            module_id=self.module_id,
            kind=kind,
            contribution_id=contribution_id,
        ))
        self._registered.add(key)

    def require_complete(self) -> None:
        missing = sorted(self._declared - self._registered)
        if missing:
            rendered = ", ".join(
                f"{kind}.{contribution_id}"
                for kind, contribution_id in missing
            )
            raise CompositionValidationError(
                f"模块 {self.module_id} 未注册声明贡献: {rendered}"
            )


class CompositionRoot:
    """内建模块只能在此处组合、启动、停止和读取。"""

    def __init__(
        self,
        modules: tuple[ApplicationModule, ...],
    ) -> None:
        if not isinstance(modules, tuple):
            raise CompositionValidationError(
                "modules 必须是 tuple"
            )
        self._module_objects = modules
        self._modules_by_id: dict[str, ApplicationModule] = {}
        self._snapshot: CompositionSnapshot | None = None
        self._started_ids: list[str] = []
        self._state = CompositionState.NEW

    @property
    def state(self) -> CompositionState:
        return self._state

    @property
    def snapshot(self) -> CompositionSnapshot | None:
        return self._snapshot

    def _validate_capabilities(
        self,
        manifests: tuple[ModuleManifest, ...],
    ) -> None:
        owners: dict[str, str] = {}
        for manifest in manifests:
            for capability in manifest.provided_capabilities:
                previous = owners.get(capability)
                if previous is not None:
                    raise CompositionValidationError(
                        f"能力 {capability} 同时由 "
                        f"{previous} 和 {manifest.module_id} 提供"
                    )
                owners[capability] = manifest.module_id

    @staticmethod
    def _validate_phases(
        snapshot: RegistrySnapshot[ModuleManifest],
    ) -> None:
        for manifest in snapshot:
            for dependency_id in manifest.required_modules:
                dependency = snapshot.require(dependency_id)
                if (
                    dependency.startup_phase
                    > manifest.startup_phase
                ):
                    raise CompositionValidationError(
                        f"模块 {manifest.module_id} 的 startup_phase "
                        f"早于依赖 {dependency_id}"
                    )
                if (
                    dependency.shutdown_phase
                    > manifest.shutdown_phase
                ):
                    raise CompositionValidationError(
                        f"模块 {manifest.module_id} 的 shutdown_phase "
                        f"早于依赖 {dependency_id}"
                    )

    def build(self) -> CompositionSnapshot:
        if self._snapshot is not None:
            return self._snapshot
        if self._state is not CompositionState.NEW:
            raise CompositionValidationError(
                f"当前状态不能构建 Composition：{self._state.value}"
            )
        module_manifests = tuple(
            (module, module.manifest())
            for module in self._module_objects
        )
        manifests = tuple(
            manifest for _module, manifest in module_manifests
        )
        if any(
            not isinstance(manifest, ModuleManifest)
            for manifest in manifests
        ):
            raise CompositionValidationError(
                "ApplicationModule.manifest() 必须返回 ModuleManifest"
            )
        self._validate_capabilities(manifests)

        generation = RegistryGeneration[ModuleManifest](
            "application_module"
        )

        def configure(
            builder: RegistryBuilder[ModuleManifest],
        ) -> None:
            for manifest in manifests:
                builder.register(manifest)

        module_snapshot = generation.rebuild(configure)
        self._validate_phases(module_snapshot)
        self._modules_by_id = {
            manifest.module_id: module
            for module, manifest in module_manifests
        }

        contribution_builder = RegistryBuilder[
            ModuleContributionDescriptor
        ]("module_contribution")
        for module_id in module_snapshot.ordered_ids:
            module = self._modules_by_id[module_id]
            manifest = module_snapshot.require(module_id)
            scoped = _ScopedRegistrationBuilder(
                module_id=module_id,
                declared=manifest.contributions,
                target=contribution_builder,
            )
            module.register(scoped)
            scoped.require_complete()
        contribution_snapshot = contribution_builder.freeze(
            generation=module_snapshot.generation
        )
        content = {
            "schema_version": 1,
            "generation": module_snapshot.generation,
            "module_registry_sha256": module_snapshot.sha256,
            "contribution_registry_sha256": (
                contribution_snapshot.sha256
            ),
        }
        encoded = canonical_json(content)
        self._snapshot = CompositionSnapshot(
            modules=module_snapshot,
            contributions=contribution_snapshot,
            generation=module_snapshot.generation,
            sha256=hashlib.sha256(
                encoded.encode("utf-8")
            ).hexdigest(),
        )
        return self._snapshot

    async def _stop_started(self) -> list[tuple[str, str]]:
        failures: list[tuple[str, str]] = []
        while self._started_ids:
            module_id = self._started_ids.pop()
            module = self._modules_by_id[module_id]
            try:
                await module.stop()
            except BaseException as exc:
                failures.append((module_id, type(exc).__name__))
        return failures

    async def start(
        self,
        runtime_context: ModuleRuntimeContext,
    ) -> None:
        if self._state is not CompositionState.NEW:
            raise CompositionStartError(
                f"Composition 当前不能启动：{self._state.value}"
            )
        snapshot = self.build()
        context = replace(
            runtime_context,
            composition_generation=snapshot.generation,
            composition_sha256=snapshot.sha256,
        )
        self._state = CompositionState.STARTING
        current_id = ""
        try:
            for current_id in snapshot.modules.ordered_ids:
                module = self._modules_by_id[current_id]
                await module.start(context)
                self._started_ids.append(current_id)
                health = module.health()
                if (
                    not isinstance(health, ModuleHealth)
                    or not health.ready
                ):
                    raise CompositionValidationError(
                        "模块启动后未达到 ready"
                    )
        except BaseException as exc:
            await self._stop_started()
            self._state = CompositionState.FAILED
            if isinstance(
                exc,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            ):
                raise
            raise CompositionStartError(
                f"模块 {current_id or 'unknown'} 启动失败 "
                f"({type(exc).__name__})"
            ) from exc
        self._state = CompositionState.RUNNING

    async def stop(self) -> None:
        if self._state is CompositionState.STOPPED:
            return
        if self._state is not CompositionState.RUNNING:
            raise CompositionStopError(
                f"Composition 当前不能停止：{self._state.value}"
            )
        self._state = CompositionState.STOPPING
        failures = await self._stop_started()
        self._state = CompositionState.STOPPED
        if failures:
            rendered = ", ".join(
                f"{module_id}:{error_type}"
                for module_id, error_type in failures
            )
            raise CompositionStopError(
                f"部分模块停止失败：{rendered}"
            )

    def require_module(self, module_id: str) -> ApplicationModule:
        if self._state is not CompositionState.RUNNING:
            raise RuntimeError("Composition 未运行，不能读取模块")
        try:
            return self._modules_by_id[module_id]
        except KeyError as exc:
            raise KeyError(f"未知 ApplicationModule: {module_id}") from exc

    def health(self) -> dict[str, ModuleHealth]:
        if self._state is not CompositionState.RUNNING:
            raise RuntimeError("Composition 未运行，不能读取健康状态")
        snapshot = self.build()
        return {
            module_id: self._modules_by_id[module_id].health()
            for module_id in snapshot.modules.ordered_ids
        }
