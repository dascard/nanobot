"""代码所有的验证套件目录与确定性 Verification Plan。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import PurePosixPath
import re

from core.lifecycle import (
    FEATURE_LIFECYCLE_REGISTRY,
    FeatureLifecycleState,
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
from core.release.artifacts import BUILD_PROFILE_REGISTRY
from core.release.impact import (
    RELEASE_IMPACT_REGISTRY,
    ReleaseImpactReport,
)


_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECURITY_LEVEL_ORDER = {
    "local": 0,
    "networked": 1,
    "host_privileged": 2,
    "production_write": 3,
}


class VerificationPlanError(RuntimeError, ValueError):
    """验证计划输入或跨 Registry 引用不满足稳定合同。"""


class VerificationSecurityLevel(str, Enum):
    """执行验证套件所需的最高权限边界。"""

    LOCAL = "local"
    NETWORKED = "networked"
    HOST_PRIVILEGED = "host_privileged"
    PRODUCTION_WRITE = "production_write"


def _validate_text(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ValueError(f"{field_name} 不能包含控制字符")
    return value


def _validate_text_tuple(
    values: object,
    *,
    field_name: str,
    required: bool = False,
    identifiers: bool = False,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} 必须是 tuple")
    if required and not values:
        raise ValueError(f"{field_name} 不能为空")
    if unique and len(values) != len(set(values)):
        raise ValueError(f"{field_name} 不能包含重复项")
    normalized: list[str] = []
    for value in values:
        if identifiers:
            normalized.append(
                validate_identifier(value, field_name=field_name)
            )
        else:
            normalized.append(
                _validate_text(value, field_name=field_name)
            )
    return tuple(normalized)


def _validate_working_directory(value: str) -> str:
    if value == ".":
        return value
    _validate_text(value, field_name="working_directory")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("working_directory 必须是仓库相对目录")
    return candidate.as_posix()


@dataclass(frozen=True, slots=True)
class VerificationSuiteDescriptor:
    """一个可执行验证套件的静态、无凭据描述。"""

    registry_id: str
    owner: str
    applicable_release_impacts: tuple[str, ...]
    command: tuple[str, ...]
    preconditions: tuple[str, ...]
    timeout_seconds: int
    allow_skip: bool
    required_credentials: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    success_criteria: tuple[str, ...]
    cleanup: tuple[str, ...]
    security_level: VerificationSecurityLevel
    working_directory: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    feature_lifecycle_states: tuple[str, ...] = ()
    feature_enablement_gates: tuple[str, ...] = ()
    artifact_profiles: tuple[str, ...] = ()
    always_required: bool = False
    registry_dependencies: tuple[str, ...] = ()
    registry_namespace: str = field(
        default="verification_suite",
        init=False,
    )

    def __post_init__(self) -> None:
        validate_identifier(
            self.registry_id,
            field_name="verification_suite.id",
        )
        validate_identifier(
            self.owner,
            field_name=f"{self.registry_id}.owner",
        )
        for field_name, values in (
            (
                "applicable_release_impacts",
                self.applicable_release_impacts,
            ),
            ("preconditions", self.preconditions),
            ("required_credentials", self.required_credentials),
            (
                "feature_enablement_gates",
                self.feature_enablement_gates,
            ),
            ("artifact_profiles", self.artifact_profiles),
            ("registry_dependencies", self.registry_dependencies),
        ):
            _validate_text_tuple(
                values,
                field_name=f"{self.registry_id}.{field_name}",
                identifiers=True,
            )
        _validate_text_tuple(
            self.command,
            field_name=f"{self.registry_id}.command",
            required=True,
            unique=False,
        )
        _validate_text_tuple(
            self.output_artifacts,
            field_name=f"{self.registry_id}.output_artifacts",
            required=True,
            identifiers=True,
        )
        _validate_text_tuple(
            self.success_criteria,
            field_name=f"{self.registry_id}.success_criteria",
            required=True,
            identifiers=True,
        )
        _validate_text_tuple(
            self.cleanup,
            field_name=f"{self.registry_id}.cleanup",
            required=True,
            identifiers=True,
        )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 86_400
        ):
            raise ValueError(
                f"{self.registry_id}.timeout_seconds 必须在 1..86400"
            )
        if type(self.allow_skip) is not bool:
            raise ValueError(f"{self.registry_id}.allow_skip 必须是 bool")
        if type(self.always_required) is not bool:
            raise ValueError(
                f"{self.registry_id}.always_required 必须是 bool"
            )
        if not isinstance(
            self.security_level,
            VerificationSecurityLevel,
        ):
            raise ValueError(
                f"{self.registry_id}.security_level 无效"
            )
        normalized_working_directory = _validate_working_directory(
            self.working_directory
        )
        object.__setattr__(
            self,
            "working_directory",
            normalized_working_directory,
        )
        if not isinstance(self.environment, tuple):
            raise ValueError(
                f"{self.registry_id}.environment 必须是 tuple"
            )
        environment_names: list[str] = []
        for item in self.environment:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) for value in item)
            ):
                raise ValueError(
                    f"{self.registry_id}.environment 项必须是键值 tuple"
                )
            name, value = item
            if _ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None:
                raise ValueError(
                    f"{self.registry_id}.environment 名称不合法"
                )
            if any(character in value for character in ("\0", "\r", "\n")):
                raise ValueError(
                    f"{self.registry_id}.environment 值包含控制字符"
                )
            environment_names.append(name)
        if len(environment_names) != len(set(environment_names)):
            raise ValueError(
                f"{self.registry_id}.environment 不能重复设置变量"
            )
        _validate_text_tuple(
            self.feature_lifecycle_states,
            field_name=(
                f"{self.registry_id}.feature_lifecycle_states"
            ),
        )
        valid_states = {state.value for state in FeatureLifecycleState}
        unknown_states = (
            set(self.feature_lifecycle_states) - valid_states
        )
        if unknown_states:
            raise ValueError(
                f"{self.registry_id} 引用了未知 Feature state: "
                + ", ".join(sorted(unknown_states))
            )

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "owner": self.owner,
            "applicable_release_impacts": (
                self.applicable_release_impacts
            ),
            "command": self.command,
            "preconditions": self.preconditions,
            "timeout_seconds": self.timeout_seconds,
            "allow_skip": self.allow_skip,
            "required_credentials": self.required_credentials,
            "output_artifacts": self.output_artifacts,
            "success_criteria": self.success_criteria,
            "cleanup": self.cleanup,
            "security_level": self.security_level.value,
            "working_directory": self.working_directory,
            "environment": self.environment,
            "feature_lifecycle_states": (
                self.feature_lifecycle_states
            ),
            "feature_enablement_gates": (
                self.feature_enablement_gates
            ),
            "artifact_profiles": self.artifact_profiles,
            "always_required": self.always_required,
        }


def _suite(
    registry_id: str,
    *,
    owner: str,
    release_impacts: tuple[str, ...] = (),
    command: tuple[str, ...],
    preconditions: tuple[str, ...],
    timeout_seconds: int,
    output_artifacts: tuple[str, ...] = ("captured_stdout",),
    success_criteria: tuple[str, ...] = ("exit_code_zero",),
    cleanup: tuple[str, ...] = ("none_required",),
    security_level: VerificationSecurityLevel = (
        VerificationSecurityLevel.LOCAL
    ),
    allow_skip: bool = False,
    required_credentials: tuple[str, ...] = (),
    working_directory: str = ".",
    environment: tuple[tuple[str, str], ...] = (),
    feature_states: tuple[str, ...] = (),
    feature_gates: tuple[str, ...] = (),
    artifact_profiles: tuple[str, ...] = (),
    always_required: bool = False,
    dependencies: tuple[str, ...] = (),
) -> VerificationSuiteDescriptor:
    return VerificationSuiteDescriptor(
        registry_id=registry_id,
        owner=owner,
        applicable_release_impacts=release_impacts,
        command=command,
        preconditions=preconditions,
        timeout_seconds=timeout_seconds,
        allow_skip=allow_skip,
        required_credentials=required_credentials,
        output_artifacts=output_artifacts,
        success_criteria=success_criteria,
        cleanup=cleanup,
        security_level=security_level,
        working_directory=working_directory,
        environment=environment,
        feature_lifecycle_states=feature_states,
        feature_enablement_gates=feature_gates,
        artifact_profiles=artifact_profiles,
        always_required=always_required,
        registry_dependencies=dependencies,
    )


_ALL_FEATURE_STATES = tuple(
    state.value for state in FeatureLifecycleState
)


_VERIFICATION_SUITES = (
    _suite(
        "architecture",
        owner="quality.architecture",
        release_impacts=("runtime", "operations", "deployment"),
        command=("python", "scripts/check_architecture.py"),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=120,
        artifact_profiles=(
            "nanobot-runtime",
            "nanobot-sandbox-python",
            "sandboxd",
        ),
    ),
    _suite(
        "backend-full",
        owner="quality.backend",
        release_impacts=(
            "runtime",
            "prompt_runtime",
            "database_schema",
            "sandbox",
            "news_resources",
            "kt_vendor",
            "tests",
        ),
        command=("python", "-m", "pytest", "tests/", "-v"),
        preconditions=(
            "python_dependencies_installed",
            "kt_compatibility_patch_applied",
            "prompt_runtime_acceptance_copy_initialized",
            "proxy_environment_cleared",
        ),
        timeout_seconds=1_800,
        output_artifacts=("captured_stdout", "pytest_summary"),
        success_criteria=("exit_code_zero", "zero_test_failures"),
        artifact_profiles=("nanobot-runtime", "sandboxd"),
        always_required=True,
        dependencies=("architecture",),
    ),
    _suite(
        "compose-config",
        owner="operations.compose",
        release_impacts=("deployment", "runtime_image"),
        command=(
            "bash",
            "-lc",
            (
                "cp .env.example .env && "
                "trap 'rm -f .env' EXIT && "
                "docker compose -f docker-compose.yml config --quiet && "
                "NANOBOT_RUNTIME_IMAGE="
                "example.invalid/nanobot@sha256:"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                "aaaaaaaaaaaaaaaa "
                "docker compose -f docker-compose.yml "
                "-f docker-compose.prod.yml config --quiet"
            ),
        ),
        preconditions=("docker_compose_available",),
        timeout_seconds=120,
        cleanup=("remove_temporary_dotenv",),
        artifact_profiles=("nanobot-runtime",),
    ),
    _suite(
        "database-migration-check",
        owner="core.db",
        release_impacts=("database_schema",),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_schema_migrations.py",
            "tests/test_group_learning_legacy_migration_cli.py",
            "-v",
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=420,
        output_artifacts=(
            "captured_stdout",
            "migration_idempotency_summary",
        ),
        success_criteria=(
            "exit_code_zero",
            "migration_idempotency_passed",
        ),
        feature_gates=("schema_ready",),
        artifact_profiles=("nanobot-runtime",),
        dependencies=("architecture",),
    ),
    _suite(
        "deployment-contract",
        owner="operations.release",
        release_impacts=("deployment", "runtime_image"),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_atomic_release_deployment.py",
            "tests/test_deploy_config.py",
            "tests/test_release_artifacts.py",
            "-v",
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=360,
        output_artifacts=("captured_stdout", "release_contract_summary"),
        artifact_profiles=("nanobot-runtime", "sandboxd"),
        dependencies=("compose-config",),
    ),
    _suite(
        "eval-gate",
        owner="quality.eval",
        release_impacts=("evaluation",),
        command=("bash", "scripts/run_eval_pr_gate.sh"),
        preconditions=(
            "python_dependencies_installed",
            "deterministic_eval_fixtures_available",
            "proxy_environment_cleared",
        ),
        timeout_seconds=900,
        output_artifacts=("captured_stdout", "eval_gate_reports"),
        success_criteria=(
            "exit_code_zero",
            "zero_new_eval_failures",
        ),
        feature_gates=(
            "offline_eval_passed",
            "model_review_observation_passed",
        ),
    ),
    _suite(
        "feature-lifecycle-contract",
        owner="core.lifecycle",
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_lifecycle_registries.py",
            "-v",
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=180,
        feature_states=_ALL_FEATURE_STATES,
    ),
    _suite(
        "frontend-lint-build",
        owner="webui",
        release_impacts=("webui",),
        command=("bash", "-lc", "npm run lint && npm run build"),
        preconditions=(
            "node_dependencies_installed",
            "feature_manifest_valid",
        ),
        timeout_seconds=900,
        output_artifacts=(
            "captured_stdout",
            "webui_dist_bundle",
        ),
        success_criteria=(
            "exit_code_zero",
            "eslint_zero_errors",
            "vite_build_completed",
        ),
        working_directory="webui",
        artifact_profiles=("nanobot-runtime", "webui"),
    ),
    _suite(
        "group-learning-governance",
        owner="core.group_learning",
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_group_learning_stage7a.py",
            "tests/test_group_learning_stage7b.py",
            "tests/test_group_learning_stage7c.py",
            "tests/test_group_learning_stage7d.py",
            "tests/test_group_learning_pipeline.py",
            "tests/test_group_learning_schedule.py",
            "tests/test_group_analysis_application_service.py",
            "-v",
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=480,
        output_artifacts=(
            "captured_stdout",
            "group_learning_contract_summary",
        ),
        feature_gates=(
            "schema_ready",
            "explicit_session_schedule",
            "candidate_writer_exclusive",
            "model_review_observation_passed",
            "evidence_policy_ready",
        ),
    ),
    _suite(
        "kt-compatibility",
        owner="nanobot_kt",
        release_impacts=("kt_vendor",),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_kt_framework.py",
            "tests/test_kt_integration.py",
            "tests/test_agent_runtime_gateway.py",
            "-v",
        ),
        preconditions=(
            "python_dependencies_installed",
            "kt_compatibility_patch_applied",
        ),
        timeout_seconds=420,
        artifact_profiles=("nanobot-runtime",),
        dependencies=("architecture",),
    ),
    _suite(
        "news-governance",
        owner="core.news",
        release_impacts=("news_resources",),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_news_governance.py",
            "tests/test_news_daily_pipeline.py",
            "tests/test_news_daily_model_route.py",
            "-v",
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=420,
        output_artifacts=(
            "captured_stdout",
            "news_governance_summary",
        ),
    ),
    _suite(
        "prompt-runtime-audit",
        owner="core.prompt_v2",
        release_impacts=("prompt_runtime",),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_prompt_v2_template_registry.py",
            "tests/test_prompt_v2_template_migration.py",
            "tests/test_prompt_v2_tool_template_integration.py",
            "tests/test_prompt_contribution_registry.py",
            "-v",
        ),
        preconditions=(
            "python_dependencies_installed",
            "prompt_runtime_acceptance_copy_initialized",
        ),
        timeout_seconds=600,
        output_artifacts=(
            "captured_stdout",
            "prompt_runtime_audit_summary",
        ),
        success_criteria=(
            "exit_code_zero",
            "prompt_runtime_audit_passed",
        ),
        artifact_profiles=("nanobot-runtime",),
        dependencies=("architecture",),
    ),
    _suite(
        "sandbox-real-docker",
        owner="core.sandbox",
        release_impacts=("sandbox",),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_sandbox_security.py",
            "-v",
        ),
        preconditions=(
            "real_docker_available",
            "sandbox_image_digest_pinned",
            "apparmor_profile_loaded",
            "sandboxd_ready",
            "sandbox_data_quota_ready",
            "proxy_environment_cleared",
        ),
        timeout_seconds=900,
        output_artifacts=(
            "captured_stdout",
            "sandbox_security_evidence",
        ),
        success_criteria=(
            "exit_code_zero",
            "real_docker_matrix_zero_skips",
        ),
        security_level=VerificationSecurityLevel.HOST_PRIVILEGED,
        required_credentials=("docker_host_access",),
        environment=(("NANOBOT_RUN_DOCKER_TESTS", "1"),),
        feature_gates=(
            "infrastructure_allowed",
            "sandboxd_ready",
            "apparmor_loaded",
            "fixed_image_digest",
            "workspace_quota_ready",
        ),
        artifact_profiles=("nanobot-sandbox-python", "sandboxd"),
        dependencies=("deployment-contract",),
    ),
    _suite(
        "sentinel-runtime-check",
        owner="core.guardrail",
        release_impacts=("runtime_resources",),
        command=("python", "scripts/build_behavior_baseline.py", "--check"),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=180,
        output_artifacts=(
            "captured_stdout",
            "security_behavior_golden",
        ),
        success_criteria=(
            "exit_code_zero",
            "security_golden_unchanged",
        ),
    ),
    _suite(
        "task-slo-contract",
        owner="core.task_runtime",
        command=(
            "bash",
            "-lc",
            (
                "python scripts/build_task_slo_manifest.py --check && "
                "python -m pytest tests/test_task_slo.py "
                "tests/test_task_slo_manifest.py -v"
            ),
        ),
        preconditions=("python_dependencies_installed",),
        timeout_seconds=300,
        output_artifacts=("captured_stdout", "task_slo_manifest"),
        success_criteria=(
            "exit_code_zero",
            "task_slo_manifest_unchanged",
        ),
        feature_gates=(
            "task_slo_activation_ready",
            "token_observability_ready",
        ),
    ),
)


def _build_verification_suite_registry(
) -> RegistrySnapshot[VerificationSuiteDescriptor]:
    generation = RegistryGeneration[VerificationSuiteDescriptor](
        "verification_suite"
    )

    def configure(
        builder: RegistryBuilder[VerificationSuiteDescriptor],
    ) -> None:
        for descriptor in _VERIFICATION_SUITES:
            builder.register(descriptor)

    return generation.rebuild(configure)


VERIFICATION_SUITE_REGISTRY = _build_verification_suite_registry()


def _validate_registry_links() -> None:
    release_impact_ids = set(RELEASE_IMPACT_REGISTRY.ordered_ids)
    artifact_profile_ids = set(BUILD_PROFILE_REGISTRY.ordered_ids)
    feature_gates = {
        gate
        for descriptor in FEATURE_LIFECYCLE_REGISTRY.descriptors()
        for gate in descriptor.enablement_gates
    }

    for suite in VERIFICATION_SUITE_REGISTRY:
        unknown_impacts = (
            set(suite.applicable_release_impacts)
            - release_impact_ids
        )
        if unknown_impacts:
            raise VerificationPlanError(
                f"Suite {suite.registry_id} 引用了未知 ReleaseImpact: "
                + ", ".join(sorted(unknown_impacts))
            )
        unknown_profiles = (
            set(suite.artifact_profiles) - artifact_profile_ids
        )
        if unknown_profiles:
            raise VerificationPlanError(
                f"Suite {suite.registry_id} 引用了未知 BuildProfile: "
                + ", ".join(sorted(unknown_profiles))
            )
        unknown_gates = (
            set(suite.feature_enablement_gates) - feature_gates
        )
        if unknown_gates:
            raise VerificationPlanError(
                f"Suite {suite.registry_id} 引用了未知 Feature gate: "
                + ", ".join(sorted(unknown_gates))
            )

    for impact in RELEASE_IMPACT_REGISTRY:
        for suite_id in impact.verification_suites:
            suite = VERIFICATION_SUITE_REGISTRY.get(suite_id)
            if suite is None:
                raise VerificationPlanError(
                    f"ReleaseImpact {impact.registry_id} 引用了未知 Suite: "
                    f"{suite_id}"
                )
            if (
                impact.registry_id
                not in suite.applicable_release_impacts
            ):
                raise VerificationPlanError(
                    f"ReleaseImpact {impact.registry_id} 与 Suite "
                    f"{suite_id} 的双向引用不一致"
                )

    mapped_profiles = {
        profile_id
        for suite in VERIFICATION_SUITE_REGISTRY
        for profile_id in suite.artifact_profiles
    }
    missing_profiles = artifact_profile_ids - mapped_profiles
    if missing_profiles:
        raise VerificationPlanError(
            "存在没有验证套件的 BuildProfile: "
            + ", ".join(sorted(missing_profiles))
        )
    backend = VERIFICATION_SUITE_REGISTRY.require("backend-full")
    if not backend.always_required or backend.allow_skip:
        raise VerificationPlanError(
            "backend-full 必须是不可跳过的提交硬门禁"
        )
    sandbox = VERIFICATION_SUITE_REGISTRY.require(
        "sandbox-real-docker"
    )
    if (
        sandbox.allow_skip
        or sandbox.security_level
        is not VerificationSecurityLevel.HOST_PRIVILEGED
        or not sandbox.required_credentials
    ):
        raise VerificationPlanError(
            "sandbox-real-docker 必须 fail closed 并声明宿主权限"
        )


_validate_registry_links()


@dataclass(frozen=True, slots=True)
class VerificationFeatureRef:
    feature_id: str
    state: str
    owner_module: str
    default_enabled: bool
    enablement_gates: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "state": self.state,
            "owner_module": self.owner_module,
            "default_enabled": self.default_enabled,
            "enablement_gates": list(self.enablement_gates),
        }


@dataclass(frozen=True, slots=True)
class VerificationArtifactProfileRef:
    profile_id: str
    artifact_kind: str
    fixed_services: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "artifact_kind": self.artifact_kind,
            "fixed_services": list(self.fixed_services),
        }


@dataclass(frozen=True, slots=True)
class VerificationPlanSuite:
    descriptor: VerificationSuiteDescriptor
    selection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        descriptor = self.descriptor
        return {
            "suite_id": descriptor.registry_id,
            "owner": descriptor.owner,
            "selection_reasons": list(self.selection_reasons),
            "applicable_release_impacts": list(
                descriptor.applicable_release_impacts
            ),
            "command": list(descriptor.command),
            "working_directory": descriptor.working_directory,
            "environment": [
                {"name": name, "value": value}
                for name, value in descriptor.environment
            ],
            "preconditions": list(descriptor.preconditions),
            "timeout_seconds": descriptor.timeout_seconds,
            "allow_skip": descriptor.allow_skip,
            "required_credentials": list(
                descriptor.required_credentials
            ),
            "output_artifacts": list(descriptor.output_artifacts),
            "success_criteria": list(descriptor.success_criteria),
            "cleanup": list(descriptor.cleanup),
            "security_level": descriptor.security_level.value,
        }


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    release_impact_sha256: str
    changed_paths: tuple[str, ...]
    release_impact_ids: tuple[str, ...]
    features: tuple[VerificationFeatureRef, ...]
    artifact_profiles: tuple[VerificationArtifactProfileRef, ...]
    suites: tuple[VerificationPlanSuite, ...]
    required_credentials: tuple[str, ...]
    highest_security_level: str
    verification_registry_generation: int
    verification_registry_sha256: str
    release_impact_registry_generation: int
    release_impact_registry_sha256: str
    feature_registry_generation: int
    feature_registry_sha256: str
    build_profile_registry_generation: int
    build_profile_registry_sha256: str
    canonical_json: str
    sha256: str

    def _content_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "release_impact": {
                "sha256": self.release_impact_sha256,
                "changed_paths": list(self.changed_paths),
                "descriptor_ids": list(self.release_impact_ids),
            },
            "features": [
                feature.to_dict() for feature in self.features
            ],
            "artifact_profiles": [
                profile.to_dict()
                for profile in self.artifact_profiles
            ],
            "suites": [suite.to_dict() for suite in self.suites],
            "required_credentials": list(self.required_credentials),
            "highest_security_level": self.highest_security_level,
            "registries": {
                "verification_suite": {
                    "generation": (
                        self.verification_registry_generation
                    ),
                    "sha256": self.verification_registry_sha256,
                },
                "release_impact": {
                    "generation": (
                        self.release_impact_registry_generation
                    ),
                    "sha256": self.release_impact_registry_sha256,
                },
                "feature_lifecycle": {
                    "generation": self.feature_registry_generation,
                    "sha256": self.feature_registry_sha256,
                },
                "build_profile": {
                    "generation": (
                        self.build_profile_registry_generation
                    ),
                    "sha256": self.build_profile_registry_sha256,
                },
            },
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_dict(), "sha256": self.sha256}

    @property
    def suite_ids(self) -> tuple[str, ...]:
        return tuple(
            suite.descriptor.registry_id for suite in self.suites
        )


def _normalize_selection_ids(
    values: Iterable[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    normalized = tuple(values)
    for value in normalized:
        validate_identifier(value, field_name=field_name)
    if len(normalized) != len(set(normalized)):
        raise VerificationPlanError(f"{field_name} 不能包含重复项")
    return tuple(sorted(normalized))


def _verify_release_report(report: ReleaseImpactReport) -> None:
    if not isinstance(report, ReleaseImpactReport):
        raise VerificationPlanError(
            "release_impact 必须是 ReleaseImpactReport"
        )
    actual_sha256 = hashlib.sha256(
        report.canonical_json.encode("utf-8")
    ).hexdigest()
    if actual_sha256 != report.sha256:
        raise VerificationPlanError("ReleaseImpact report Hash 无效")
    if (
        report.registry_generation
        != RELEASE_IMPACT_REGISTRY.generation
        or report.registry_sha256 != RELEASE_IMPACT_REGISTRY.sha256
    ):
        raise VerificationPlanError(
            "ReleaseImpact report Registry generation 已过期"
        )


def build_verification_plan(
    release_impact: ReleaseImpactReport,
    *,
    feature_ids: Iterable[str] = (),
    artifact_profile_ids: Iterable[str] = (),
) -> VerificationPlan:
    """从三类冻结事实源合成可解释、可复现的实际验证清单。"""

    _verify_release_report(release_impact)
    selected_feature_ids = _normalize_selection_ids(
        feature_ids,
        field_name="feature_id",
    )
    selected_profile_ids = _normalize_selection_ids(
        artifact_profile_ids,
        field_name="artifact_profile_id",
    )
    feature_snapshot = (
        FEATURE_LIFECYCLE_REGISTRY.registry_snapshot
    )

    features: list[VerificationFeatureRef] = []
    for feature_id in selected_feature_ids:
        try:
            descriptor = FEATURE_LIFECYCLE_REGISTRY.require(feature_id)
        except KeyError as exc:
            raise VerificationPlanError(
                f"未知 Feature lifecycle: {feature_id}"
            ) from exc
        features.append(VerificationFeatureRef(
            feature_id=descriptor.feature_id,
            state=descriptor.state.value,
            owner_module=descriptor.owner_module,
            default_enabled=descriptor.default_enabled,
            enablement_gates=descriptor.enablement_gates,
        ))

    profiles: list[VerificationArtifactProfileRef] = []
    for profile_id in selected_profile_ids:
        try:
            descriptor = BUILD_PROFILE_REGISTRY.require(profile_id)
        except KeyError as exc:
            raise VerificationPlanError(
                f"未知 BuildProfile: {profile_id}"
            ) from exc
        profiles.append(VerificationArtifactProfileRef(
            profile_id=descriptor.registry_id,
            artifact_kind=descriptor.artifact_kind,
            fixed_services=descriptor.fixed_services,
        ))

    release_impact_ids = tuple(sorted({
        descriptor_id
        for path_impact in release_impact.path_impacts
        for descriptor_id in path_impact.descriptor_ids
    }))
    reasons_by_suite: dict[str, set[str]] = {}

    def select_suite(suite_id: str, reason: str) -> None:
        descriptor = VERIFICATION_SUITE_REGISTRY.get(suite_id)
        if descriptor is None:
            raise VerificationPlanError(
                f"未知 Verification Suite: {suite_id}"
            )
        reasons_by_suite.setdefault(suite_id, set()).add(reason)
        for dependency in descriptor.registry_dependencies:
            select_suite(
                dependency,
                f"dependency:{suite_id}",
            )

    for descriptor in VERIFICATION_SUITE_REGISTRY:
        if descriptor.always_required:
            select_suite(descriptor.registry_id, "commit_gate")

    impact_id_set = set(release_impact_ids)
    for suite_id in release_impact.verification_suites:
        descriptor = VERIFICATION_SUITE_REGISTRY.get(suite_id)
        if descriptor is None:
            raise VerificationPlanError(
                f"ReleaseImpact report 引用了未知 Suite: {suite_id}"
            )
        matching_impacts = tuple(sorted(
            impact_id_set
            & set(descriptor.applicable_release_impacts)
        ))
        if not matching_impacts and not descriptor.always_required:
            raise VerificationPlanError(
                f"Suite {suite_id} 与当前 ReleaseImpact 不一致"
            )
        for impact_id in matching_impacts:
            select_suite(suite_id, f"release_impact:{impact_id}")

    for feature in features:
        gates = set(feature.enablement_gates)
        for suite in VERIFICATION_SUITE_REGISTRY:
            if feature.state in suite.feature_lifecycle_states:
                select_suite(
                    suite.registry_id,
                    (
                        f"feature_lifecycle:{feature.feature_id}:"
                        f"{feature.state}"
                    ),
                )
            for gate in sorted(
                gates & set(suite.feature_enablement_gates)
            ):
                select_suite(
                    suite.registry_id,
                    f"feature_gate:{feature.feature_id}:{gate}",
                )

    for profile in profiles:
        for suite in VERIFICATION_SUITE_REGISTRY:
            if profile.profile_id in suite.artifact_profiles:
                select_suite(
                    suite.registry_id,
                    f"artifact_profile:{profile.profile_id}",
                )

    plan_suites = tuple(
        VerificationPlanSuite(
            descriptor=VERIFICATION_SUITE_REGISTRY.require(
                suite_id
            ),
            selection_reasons=tuple(
                sorted(reasons_by_suite[suite_id])
            ),
        )
        for suite_id in VERIFICATION_SUITE_REGISTRY.ordered_ids
        if suite_id in reasons_by_suite
    )
    credentials = tuple(sorted({
        credential
        for suite in plan_suites
        for credential in suite.descriptor.required_credentials
    }))
    highest_security_level = max(
        (
            suite.descriptor.security_level.value
            for suite in plan_suites
        ),
        key=_SECURITY_LEVEL_ORDER.__getitem__,
        default=VerificationSecurityLevel.LOCAL.value,
    )
    content = {
        "schema_version": 1,
        "release_impact": {
            "sha256": release_impact.sha256,
            "changed_paths": list(release_impact.changed_paths),
            "descriptor_ids": list(release_impact_ids),
        },
        "features": [feature.to_dict() for feature in features],
        "artifact_profiles": [
            profile.to_dict() for profile in profiles
        ],
        "suites": [suite.to_dict() for suite in plan_suites],
        "required_credentials": list(credentials),
        "highest_security_level": highest_security_level,
        "registries": {
            "verification_suite": {
                "generation": VERIFICATION_SUITE_REGISTRY.generation,
                "sha256": VERIFICATION_SUITE_REGISTRY.sha256,
            },
            "release_impact": {
                "generation": RELEASE_IMPACT_REGISTRY.generation,
                "sha256": RELEASE_IMPACT_REGISTRY.sha256,
            },
            "feature_lifecycle": {
                "generation": feature_snapshot.generation,
                "sha256": feature_snapshot.sha256,
            },
            "build_profile": {
                "generation": BUILD_PROFILE_REGISTRY.generation,
                "sha256": BUILD_PROFILE_REGISTRY.sha256,
            },
        },
    }
    encoded = canonical_json(content)
    return VerificationPlan(
        release_impact_sha256=release_impact.sha256,
        changed_paths=release_impact.changed_paths,
        release_impact_ids=release_impact_ids,
        features=tuple(features),
        artifact_profiles=tuple(profiles),
        suites=plan_suites,
        required_credentials=credentials,
        highest_security_level=highest_security_level,
        verification_registry_generation=(
            VERIFICATION_SUITE_REGISTRY.generation
        ),
        verification_registry_sha256=(
            VERIFICATION_SUITE_REGISTRY.sha256
        ),
        release_impact_registry_generation=(
            RELEASE_IMPACT_REGISTRY.generation
        ),
        release_impact_registry_sha256=(
            RELEASE_IMPACT_REGISTRY.sha256
        ),
        feature_registry_generation=feature_snapshot.generation,
        feature_registry_sha256=feature_snapshot.sha256,
        build_profile_registry_generation=(
            BUILD_PROFILE_REGISTRY.generation
        ),
        build_profile_registry_sha256=BUILD_PROFILE_REGISTRY.sha256,
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "VERIFICATION_SUITE_REGISTRY",
    "VerificationArtifactProfileRef",
    "VerificationFeatureRef",
    "VerificationPlan",
    "VerificationPlanError",
    "VerificationPlanSuite",
    "VerificationSecurityLevel",
    "VerificationSuiteDescriptor",
    "build_verification_plan",
]
