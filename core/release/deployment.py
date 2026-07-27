"""四个固定 Runtime 服务的原子切换与整体回滚。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time
from typing import Protocol

from core.release.artifacts import (
    ArtifactManifest,
    ReleaseManifest,
    build_observed_runtime_artifact,
    build_release_manifest,
    dump_release_manifest,
    load_release_manifest,
)
from core.release.impact import FIXED_RUNTIME_SERVICES


SERVICE_CONTAINERS: Mapping[str, str] = {
    "nanobot-server": "nanobot-server",
    "session-summary-worker": "nanobot-session-summary-worker",
    "outbound-delivery-worker": "nanobot-outbound-delivery-worker",
    "semantic-index-worker": "nanobot-semantic-index-worker",
}
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")
SYSTEM_MIN_FREE_BYTES = 60 * 1024 * 1024 * 1024
_INSPECT_FORMAT = (
    '{{.Config.Image}}|{{.Image}}|'
    '{{index .Config.Labels "org.opencontainers.image.revision"}}|'
    '{{if .State.Health}}{{.State.Health.Status}}'
    "{{else}}missing{{end}}"
)
_NONFIXED_INSPECT_FORMAT = "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}"
_SECURITY_INSPECT_FORMAT = (
    "{{.Config.User}}|{{.HostConfig.ReadonlyRootfs}}|"
    "{{json .HostConfig.CapDrop}}|{{json .HostConfig.SecurityOpt}}|"
    "{{.HostConfig.Privileged}}|{{.HostConfig.PidsLimit}}|"
    "{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|"
    "{{range .Mounts}}{{.Destination}}={{.RW}};{{end}}"
)
_INFRASTRUCTURE_PERMISSION_ENV_KEY = (
    "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED"
)
_FEATURE_ENV_KEYS = (
    "NANOBOT_SANDBOX_ENABLED",
    "NANOBOT_SANDBOX_EXEC_ENABLED",
    "NANOBOT_SANDBOX_GROUP_ENABLED",
    "NANOBOT_GROUP_LEARNING_ENABLED",
    "GROUP_MEMORY_INJECTION_ENABLED",
)


class DeploymentError(RuntimeError):
    """原子发布稳定错误基类。"""


class DeploymentCommandError(DeploymentError):
    """外部命令失败；错误消息不包含 stderr 或环境变量。"""


class DeploymentVerificationError(DeploymentError):
    """服务身份、健康或迁移证据不满足发布合同。"""


class DeploymentStateError(DeploymentError):
    """本地 Release 状态与实际容器不一致。"""


class AtomicDeploymentError(DeploymentError):
    """目标发布失败，并携带整体回滚是否成功的事实。"""

    def __init__(
        self,
        message: str,
        *,
        rollback_succeeded: bool,
    ) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    image_reference: str
    image_id: str
    revision: str
    health_by_service: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    release_id: str
    previous_release_id: str
    changed: bool


def _same_runtime(
    first: ArtifactManifest,
    second: ArtifactManifest,
) -> bool:
    return (
        first.oci_image_reference == second.oci_image_reference
        and bool(
            _runtime_image_identity_digests(first)
            & _runtime_image_identity_digests(second)
        )
        and first.source.git_full_commit
        == second.source.git_full_commit
    )


def _runtime_image_identity_digests(
    artifact: ArtifactManifest,
) -> frozenset[str]:
    """兼容 legacy config ID 与 containerd OCI 索引 ID。"""

    return frozenset((
        artifact.oci_image_digest,
        artifact.oci_image_id,
    ))


class ReleaseStateStore:
    """用 current／pending／rollback 文件协调 Docker 与发布状态。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.current_path = root / "current.json"
        self.pending_path = root / "pending.json"
        self.rollback_path = root / "rollback.json"
        self.history_dir = root / "history"

    def current(self) -> ReleaseManifest:
        if not self.current_path.is_file():
            raise DeploymentStateError("缺少 current ReleaseManifest")
        try:
            return load_release_manifest(self.current_path)
        except ValueError as exc:
            raise DeploymentStateError(
                "current ReleaseManifest 无效"
            ) from exc

    def _pending(self) -> ReleaseManifest | None:
        if not self.pending_path.is_file():
            return None
        try:
            return load_release_manifest(self.pending_path)
        except ValueError as exc:
            raise DeploymentStateError(
                "pending ReleaseManifest 无效"
            ) from exc

    def rollback(self) -> ReleaseManifest | None:
        if not self.rollback_path.is_file():
            return None
        try:
            return load_release_manifest(self.rollback_path)
        except ValueError as exc:
            raise DeploymentStateError(
                "rollback ReleaseManifest 无效"
            ) from exc

    def adopt_or_reconcile(
        self,
        observed: ReleaseManifest,
    ) -> ReleaseManifest:
        """首次采用现状，或收敛上次中断留下的 pending。"""

        self.root.mkdir(parents=True, exist_ok=True)
        if not self.current_path.is_file():
            dump_release_manifest(self.current_path, observed)
            return observed

        current = self.current()
        pending = self._pending()
        if pending is not None:
            observed_is_current = _same_runtime(
                observed.runtime_artifact,
                current.runtime_artifact,
            )
            observed_is_pending = _same_runtime(
                observed.runtime_artifact,
                pending.runtime_artifact,
            )
            if observed_is_pending and observed_is_current:
                self.pending_path.unlink(missing_ok=True)
            elif observed_is_pending:
                self.commit()
                current = self.current()
            elif observed_is_current:
                self.abort()
            else:
                raise DeploymentStateError(
                    "实际 Runtime 与 current／pending 均不一致"
                )

        current = self.current()
        if not _same_runtime(
            observed.runtime_artifact,
            current.runtime_artifact,
        ):
            raise DeploymentStateError(
                "实际 Runtime 与 current ReleaseManifest 不一致"
            )
        return current

    def stage(self, target: ReleaseManifest) -> None:
        self.current()
        dump_release_manifest(self.pending_path, target)

    def commit(self) -> ReleaseManifest:
        current = self.current()
        pending = self._pending()
        if pending is None:
            raise DeploymentStateError(
                "没有可提交的 pending ReleaseManifest"
            )
        if not _same_runtime(
            current.runtime_artifact,
            pending.runtime_artifact,
        ):
            dump_release_manifest(self.rollback_path, current)
        dump_release_manifest(self.current_path, pending)
        dump_release_manifest(
            self.history_dir / f"{pending.release_id}.json",
            pending,
        )
        self.pending_path.unlink(missing_ok=True)
        return pending

    def abort(self) -> None:
        self.pending_path.unlink(missing_ok=True)


class AtomicRuntimeDeployer:
    """所有副作用经 CommandRunner 注入，便于故障注入验证。"""

    def __init__(
        self,
        *,
        runner: CommandRunner,
        state_store: ReleaseStateStore,
        compose_env_file: Path,
        ready_url: str,
        system_min_free_bytes: int = SYSTEM_MIN_FREE_BYTES,
        pull_reserve_bytes: int = 5 * 1024 * 1024 * 1024,
        disk_free_bytes: Callable[[], int] | None = None,
        health_attempts: int = 60,
        health_interval_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str],
    ) -> None:
        if health_attempts <= 0:
            raise ValueError("health_attempts 必须是正整数")
        if system_min_free_bytes < SYSTEM_MIN_FREE_BYTES:
            raise ValueError("system_min_free_bytes 不能低于 60 GiB")
        if pull_reserve_bytes <= 0:
            raise ValueError("pull_reserve_bytes 必须是正整数")
        self.runner = runner
        self.state_store = state_store
        self.compose_env_file = Path(compose_env_file)
        self.ready_url = ready_url
        self.system_min_free_bytes = system_min_free_bytes
        self.pull_reserve_bytes = pull_reserve_bytes
        self.disk_free_bytes = disk_free_bytes or (
            lambda: shutil.disk_usage("/").free
        )
        self.health_attempts = health_attempts
        self.health_interval_seconds = health_interval_seconds
        self.sleep = sleep
        self.now = now

    def _run(
        self,
        args: Sequence[str],
        *,
        image_reference: str | None = None,
        operation: str,
    ) -> CommandResult:
        environment = (
            {"NANOBOT_RUNTIME_IMAGE": image_reference}
            if image_reference is not None
            else None
        )
        result = self.runner.run(args, environment=environment)
        if result.returncode != 0:
            raise DeploymentCommandError(
                f"{operation} 失败，退出码 {result.returncode}"
            )
        return result

    def _compose(
        self,
        arguments: Sequence[str],
        *,
        image_reference: str,
        operation: str,
    ) -> CommandResult:
        compose_base: list[str] = [
            "docker",
            "compose",
            "--project-name",
            "nanobot",
            "--env-file",
            str(self.compose_env_file),
        ]
        for compose_file in COMPOSE_FILES:
            compose_base.extend(("-f", compose_file))
        return self._run(
            (*compose_base, *arguments),
            image_reference=image_reference,
            operation=operation,
        )

    @staticmethod
    def _environment_bool(value: str, *, key: str) -> bool:
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        raise DeploymentVerificationError(
            f"当前 Runtime 的 {key} 不是合法布尔值"
        )

    def _assert_runtime_feature_environment_off(self) -> None:
        result = self._run(
            (
                "docker",
                "inspect",
                "--format",
                "{{json .Config.Env}}",
                "nanobot-server",
            ),
            operation="读取 Runtime Feature 环境边界",
        )
        try:
            raw_values = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DeploymentVerificationError(
                "无法解析当前 Runtime Feature 环境边界"
            ) from exc
        if not isinstance(raw_values, list):
            raise DeploymentVerificationError(
                "当前 Runtime Feature 环境边界格式无效"
            )
        environment: dict[str, str] = {}
        for item in raw_values:
            key, separator, value = str(item).partition("=")
            if separator and (
                key in _FEATURE_ENV_KEYS
                or key == _INFRASTRUCTURE_PERMISSION_ENV_KEY
            ):
                environment[key] = value
        if _INFRASTRUCTURE_PERMISSION_ENV_KEY in environment:
            self._environment_bool(
                environment[_INFRASTRUCTURE_PERMISSION_ENV_KEY],
                key=_INFRASTRUCTURE_PERMISSION_ENV_KEY,
            )
        enabled = sorted(
            key
            for key in _FEATURE_ENV_KEYS
            if self._environment_bool(environment.get(key, "false"), key=key)
        )
        if enabled:
            raise DeploymentVerificationError(
                "当前 Runtime Feature 硬开关尚未全部关闭："
                + ", ".join(enabled)
            )

    def _assert_no_active_sandboxes(self) -> None:
        result = self._run(
            (
                "docker",
                "ps",
                "--format",
                "{{.Names}}",
                "--filter",
                "label=com.nanobot.sandbox=true",
                "--filter",
                "label=com.nanobot.managed-by=sandboxd",
            ),
            operation="检查活动 Sandbox 容器",
        )
        if result.stdout.strip():
            raise DeploymentVerificationError(
                "仍有活动 Sandbox 容器，拒绝切换 Runtime"
            )

    def _snapshot_nonfixed_containers(self) -> tuple[str, ...]:
        result = self._run(
            (
                "docker",
                "ps",
                "-a",
                "--format",
                _NONFIXED_INSPECT_FORMAT,
            ),
            operation="记录非固定容器白名单快照",
        )
        fixed_names = set(SERVICE_CONTAINERS.values())
        snapshot: list[str] = []
        for line in result.stdout.splitlines():
            fields = line.split("\t", 3)
            if len(fields) != 4 or not all(fields):
                raise DeploymentVerificationError(
                    "容器白名单快照格式无效"
                )
            if fields[1] not in fixed_names:
                snapshot.append(line)
        return tuple(sorted(snapshot))

    def _assert_nonfixed_containers_unchanged(
        self,
        expected: tuple[str, ...],
    ) -> None:
        if self._snapshot_nonfixed_containers() != expected:
            raise DeploymentVerificationError(
                "非 Nanobot 固定容器的 ID、名称、镜像或状态发生变化"
            )

    def _assert_pre_pull_disk_gate(self) -> None:
        if self.disk_free_bytes() < (
            self.system_min_free_bytes + self.pull_reserve_bytes
        ):
            raise DeploymentVerificationError(
                "拉取前磁盘不足以保留系统水位与镜像拉取／解包预算"
            )

    def _assert_runtime_disk_gate(self) -> None:
        if self.disk_free_bytes() < self.system_min_free_bytes:
            raise DeploymentVerificationError(
                "Runtime 操作后根分区可用空间低于 60 GiB"
            )

    def _assert_runtime_security_boundaries(self) -> None:
        for service in FIXED_RUNTIME_SERVICES:
            container = SERVICE_CONTAINERS[service]
            result = self._run(
                (
                    "docker",
                    "inspect",
                    "--format",
                    _SECURITY_INSPECT_FORMAT,
                    container,
                ),
                operation=f"检查 {service} Runtime 安全边界",
            )
            fields = result.stdout.strip().split("|", 8)
            if len(fields) != 9:
                raise DeploymentVerificationError(
                    f"{service} Runtime 安全字段缺失"
                )
            (
                user,
                readonly_rootfs,
                cap_drop_raw,
                security_opt_raw,
                privileged,
                pids_limit,
                memory_limit,
                nano_cpus,
                mounts_raw,
            ) = fields
            try:
                cap_drop = json.loads(cap_drop_raw)
                security_opt = json.loads(security_opt_raw)
                positive_limits = all(
                    int(value) > 0
                    for value in (pids_limit, memory_limit, nano_cpus)
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DeploymentVerificationError(
                    f"{service} Runtime 安全字段格式无效"
                ) from exc
            if (
                user != "10001:10001"
                or readonly_rootfs != "true"
                or privileged != "false"
                or not isinstance(cap_drop, list)
                or "ALL" not in cap_drop
                or not isinstance(security_opt, list)
                or not any(
                    str(option).startswith("no-new-privileges")
                    for option in security_opt
                )
                or not positive_limits
            ):
                raise DeploymentVerificationError(
                    f"{service} Runtime 最小权限或资源限制未生效"
                )
            mounts: dict[str, bool] = {}
            for item in mounts_raw.split(";"):
                if not item:
                    continue
                destination, separator, writable = item.partition("=")
                if not separator or writable not in {"true", "false"}:
                    raise DeploymentVerificationError(
                        f"{service} Runtime 挂载字段格式无效"
                    )
                mounts[destination] = writable == "true"
            if "/var/run/docker.sock" in mounts or "/srv/nanobot" in mounts:
                raise DeploymentVerificationError(
                    f"{service} Runtime 暴露了禁止的宿主控制面"
                )
            if service == "nanobot-server":
                if mounts.get("/run/nanobot-sandboxd") is not False:
                    raise DeploymentVerificationError(
                        "nanobot-server 必须只读挂载 sandboxd UDS 目录"
                    )
                required_prompt_mounts = (
                    "/var/lib/nanobot/prompt-runtime/live",
                    "/var/lib/nanobot/prompt-runtime/state",
                    "/var/lib/nanobot/prompt-runtime/backups",
                )
                if any(mounts.get(path) is not True for path in required_prompt_mounts):
                    raise DeploymentVerificationError(
                        "nanobot-server 未读写挂载外置 Prompt Runtime"
                    )
            elif "/run/nanobot-sandboxd" in mounts:
                raise DeploymentVerificationError(
                    f"{service} 不得访问 sandboxd UDS"
                )
            if service == "session-summary-worker":
                worker_prompt_mounts = (
                    "/var/lib/nanobot/prompt-runtime/live",
                    "/var/lib/nanobot/prompt-runtime/state",
                )
                if any(
                    mounts.get(path) is not False
                    for path in worker_prompt_mounts
                ):
                    raise DeploymentVerificationError(
                        "session-summary-worker 必须只读挂载外置 Prompt Runtime"
                    )

    def _assert_static_preconditions(self) -> tuple[str, ...]:
        self._assert_pre_pull_disk_gate()
        self._assert_no_active_sandboxes()
        self._assert_runtime_feature_environment_off()
        return self._snapshot_nonfixed_containers()

    def _assert_postconditions(
        self,
        expected_nonfixed: tuple[str, ...],
    ) -> None:
        self._assert_runtime_disk_gate()
        self._assert_no_active_sandboxes()
        self._assert_runtime_feature_environment_off()
        self._assert_runtime_security_boundaries()
        self._assert_nonfixed_containers_unchanged(expected_nonfixed)

    def _observe(self) -> RuntimeObservation:
        references: set[str] = set()
        image_ids: set[str] = set()
        revisions: set[str] = set()
        health: dict[str, str] = {}
        for service in FIXED_RUNTIME_SERVICES:
            container = SERVICE_CONTAINERS[service]
            result = self._run(
                (
                    "docker",
                    "inspect",
                    "--format",
                    _INSPECT_FORMAT,
                    container,
                ),
                operation=f"读取 {service} 容器状态",
            )
            fields = result.stdout.strip().split("|")
            if len(fields) != 4 or not all(fields[:3]):
                raise DeploymentVerificationError(
                    f"{service} 容器身份字段缺失"
                )
            reference, image_id, revision, health_status = fields
            references.add(reference)
            image_ids.add(image_id)
            revisions.add(revision)
            health[service] = health_status
        if (
            len(references) != 1
            or len(image_ids) != 1
            or len(revisions) != 1
        ):
            raise DeploymentVerificationError(
                "四个固定服务的镜像或 Runtime revision 不一致"
            )
        unhealthy = sorted(
            service
            for service, status in health.items()
            if status != "healthy"
        )
        if unhealthy:
            raise DeploymentVerificationError(
                "固定服务健康检查未通过: "
                + ", ".join(unhealthy)
            )
        return RuntimeObservation(
            image_reference=next(iter(references)),
            image_id=next(iter(image_ids)),
            revision=next(iter(revisions)),
            health_by_service=health,
        )

    @staticmethod
    def _assert_observation(
        observation: RuntimeObservation,
        artifact: ArtifactManifest,
    ) -> None:
        if (
            observation.image_reference
            != artifact.oci_image_reference
            or observation.image_id
            not in _runtime_image_identity_digests(artifact)
            or observation.revision
            != artifact.source.git_full_commit
        ):
            raise DeploymentVerificationError(
                "实际 Runtime 身份与 ReleaseManifest 不一致"
            )

    def _assert_pulled_artifact_identity(
        self,
        artifact: ArtifactManifest,
    ) -> None:
        result = self._run(
            (
                "docker",
                "image",
                "inspect",
                "--format",
                '{{.Id}}|{{index .Config.Labels "org.opencontainers.image.revision"}}|{{json .RepoDigests}}',
                artifact.oci_image_reference,
            ),
            operation="校验已拉取 Runtime 身份",
        )
        fields = result.stdout.strip().split("|", 2)
        if len(fields) != 3:
            raise DeploymentVerificationError("已拉取 Runtime 身份字段缺失")
        try:
            repo_digests = json.loads(fields[2])
        except json.JSONDecodeError as exc:
            raise DeploymentVerificationError(
                "已拉取 Runtime RepoDigest 格式无效"
            ) from exc
        if (
            fields[0] not in _runtime_image_identity_digests(artifact)
            or fields[1] != artifact.source.git_full_commit
            or not isinstance(repo_digests, list)
            or artifact.oci_image_reference not in repo_digests
        ):
            raise DeploymentVerificationError(
                "已拉取 Runtime 的镜像存储 ID、revision 或 RepoDigest 与 ArtifactManifest 不一致"
            )

    def _check_ready(self) -> None:
        self._run(
            (
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                self.ready_url,
            ),
            operation="主服务 readiness 检查",
        )

    def _wait_for_runtime(
        self,
        artifact: ArtifactManifest,
    ) -> RuntimeObservation:
        last_error: DeploymentError | None = None
        for attempt in range(self.health_attempts):
            try:
                observation = self._observe()
                self._assert_observation(observation, artifact)
                self._check_ready()
                return observation
            except DeploymentError as exc:
                last_error = exc
                if attempt + 1 < self.health_attempts:
                    self.sleep(self.health_interval_seconds)
        raise DeploymentVerificationError(
            "固定服务未在预算内达到同一健康 Runtime"
        ) from last_error

    def _verify_schema_head(
        self,
        artifact: ArtifactManifest,
    ) -> None:
        self._compose(
            (
                "exec",
                "-T",
                "nanobot-server",
                "python",
                "-m",
                "core.release.runtime_verify",
                "--expected-schema-head",
                artifact.schema_migration_head,
            ),
            image_reference=artifact.oci_image_reference,
            operation="数据库迁移 Head 检查",
        )

    def _observed_release(
        self,
        observation: RuntimeObservation,
    ) -> ReleaseManifest:
        observed_at = self.now()
        artifact = build_observed_runtime_artifact(
            image_reference=observation.image_reference,
            image_id=observation.image_id,
            revision=observation.revision,
            observed_at=observed_at,
        )
        return build_release_manifest(
            artifacts=(artifact,),
            created_at=observed_at,
        )

    def _rollback(
        self,
        previous: ReleaseManifest,
        *,
        expected_nonfixed: tuple[str, ...],
    ) -> None:
        artifact = previous.runtime_artifact
        self._compose(
            (
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                *FIXED_RUNTIME_SERVICES,
            ),
            image_reference=artifact.oci_image_reference,
            operation="整体恢复前一 Runtime",
        )
        self._wait_for_runtime(artifact)
        self._assert_postconditions(expected_nonfixed)
        self.state_store.abort()

    def _remove_superseded_image(
        self,
        obsolete: ReleaseManifest | None,
        *,
        current: ReleaseManifest,
        rollback: ReleaseManifest | None,
    ) -> None:
        """只按精确不可变引用淘汰不再属于 current／rollback 的镜像。"""

        if obsolete is None:
            return
        obsolete_reference = (
            obsolete.runtime_artifact.oci_image_reference
        )
        kept_references = {
            current.runtime_artifact.oci_image_reference
        }
        if rollback is not None:
            kept_references.add(
                rollback.runtime_artifact.oci_image_reference
            )
        if obsolete_reference in kept_references:
            return
        references = self.runner.run(
            (
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"ancestor={obsolete_reference}",
            )
        )
        if references.returncode != 0 or references.stdout.strip():
            return
        self.runner.run(
            ("docker", "image", "rm", obsolete_reference)
        )

    def deploy(self, target: ReleaseManifest) -> DeploymentResult:
        """切换目标 Release；任一服务失败时恢复全部四个服务。"""

        target_artifact = target.runtime_artifact
        if target_artifact.provenance != "built":
            raise DeploymentVerificationError(
                "新目标 Release 必须使用 provenance=built"
            )

        nonfixed_before = self._assert_static_preconditions()
        current_observation = self._observe()
        observed_release = self._observed_release(
            current_observation
        )
        previous = self.state_store.adopt_or_reconcile(
            observed_release
        )
        if _same_runtime(
            previous.runtime_artifact,
            target_artifact,
        ):
            obsolete = self.state_store.rollback()
            self.state_store.stage(target)
            try:
                self._wait_for_runtime(target_artifact)
                self._verify_schema_head(target_artifact)
                self._assert_postconditions(nonfixed_before)
                self.state_store.commit()
            except DeploymentError:
                self.state_store.abort()
                raise
            self._remove_superseded_image(
                obsolete,
                current=target,
                rollback=self.state_store.rollback(),
            )
            return DeploymentResult(
                release_id=target.release_id,
                previous_release_id=previous.release_id,
                changed=False,
            )

        obsolete = self.state_store.rollback()
        self.state_store.stage(target)
        containers_may_have_changed = False
        try:
            self._compose(
                ("config", "--quiet"),
                image_reference=target_artifact.oci_image_reference,
                operation="Compose 配置校验",
            )
            self._compose(
                ("pull", *FIXED_RUNTIME_SERVICES),
                image_reference=target_artifact.oci_image_reference,
                operation="拉取目标 Runtime",
            )
            self._assert_runtime_disk_gate()
            self._assert_pulled_artifact_identity(target_artifact)
            containers_may_have_changed = True
            self._compose(
                (
                    "up",
                    "-d",
                    "--no-build",
                    "--force-recreate",
                    *FIXED_RUNTIME_SERVICES,
                ),
                image_reference=target_artifact.oci_image_reference,
                operation="切换四个固定服务",
            )
            self._wait_for_runtime(target_artifact)
            self._verify_schema_head(target_artifact)
            self._assert_postconditions(nonfixed_before)
            self.state_store.commit()
        except DeploymentError as exc:
            if not containers_may_have_changed:
                self.state_store.abort()
                raise
            try:
                self._rollback(
                    previous,
                    expected_nonfixed=nonfixed_before,
                )
            except DeploymentError as rollback_exc:
                raise AtomicDeploymentError(
                    "目标 Release 失败，且整体回滚未通过验证",
                    rollback_succeeded=False,
                ) from rollback_exc
            raise AtomicDeploymentError(
                "目标 Release 失败，四个固定服务已整体回滚",
                rollback_succeeded=True,
            ) from exc

        self._remove_superseded_image(
            obsolete,
            current=target,
            rollback=self.state_store.rollback(),
        )
        return DeploymentResult(
            release_id=target.release_id,
            previous_release_id=previous.release_id,
            changed=True,
        )
