"""分层 Agent Harness 评测门禁、外部证据校验和线上只读采样。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Protocol
from urllib.parse import quote
import xml.etree.ElementTree as ET

from evals.harness_registry import (
    EVAL_HARNESS_REGISTRY,
    EvaluationLane,
    HarnessCheckDescriptor,
    HarnessSuiteDescriptor,
    harness_catalog_payload,
)


HARNESS_GATE_SCHEMA_VERSION = 1
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]{0,255}")
_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class HarnessGateError(ValueError):
    """评测输入、执行证据或 lane 权限无效。"""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
    except FileNotFoundError:
        return hashlib.sha256(b"").hexdigest(), 0
    return digest.hexdigest(), size


def _safe_token(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if _SAFE_TOKEN_RE.fullmatch(normalized) is None:
        raise HarnessGateError(f"{name} 必须是安全标识符")
    return normalized


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessGateError(f"{name} 必须是 JSON 对象")
    if any(not isinstance(key, str) for key in value):
        raise HarnessGateError(f"{name} 的键必须是字符串")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HarnessGateError(f"{name} 必须是 JSON 数组")
    return value


def _exact_keys(
    payload: Mapping[str, Any],
    *,
    name: str,
    required: frozenset[str],
) -> None:
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required)
    if missing:
        raise HarnessGateError(f"{name} 缺少字段: {', '.join(missing)}")
    if unknown:
        raise HarnessGateError(
            f"{name} 包含未允许字段: {', '.join(unknown)}"
        )


def _source_revision(root: Path) -> str:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip().lower()
    except (OSError, subprocess.SubprocessError) as exc:
        raise HarnessGateError("无法读取 Git source revision") from exc
    if _REVISION_RE.fullmatch(revision) is None:
        raise HarnessGateError("Git source revision 格式无效")
    return revision


@dataclass(frozen=True, slots=True)
class CheckExecutionEvidence:
    check_id: str
    return_code: int
    timed_out: bool
    tests: int
    passed: int
    failures: int
    errors: int
    skipped: int
    duration_ms: int
    stdout_sha256: str
    stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    failed_case_sha256s: tuple[str, ...] = ()

    @property
    def pass_rate(self) -> float:
        return self.passed / self.tests if self.tests else 0.0

    @property
    def accepted(self) -> bool:
        return (
            self.return_code == 0
            and not self.timed_out
            and self.tests > 0
            and self.failures == 0
            and self.errors == 0
            and self.skipped == 0
            and self.passed == self.tests
        )

    def to_dict(self, descriptor: HarnessCheckDescriptor) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "title": descriptor.title,
            "metric_id": descriptor.metric_id,
            "selectors": list(descriptor.selectors),
            "status": "passed" if self.accepted else "failed",
            "accepted": self.accepted,
            "return_code": self.return_code,
            "timed_out": self.timed_out,
            "tests": self.tests,
            "passed": self.passed,
            "failures": self.failures,
            "errors": self.errors,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
            "duration_ms": self.duration_ms,
            "stdout": {
                "sha256": self.stdout_sha256,
                "bytes": self.stdout_bytes,
            },
            "stderr": {
                "sha256": self.stderr_sha256,
                "bytes": self.stderr_bytes,
            },
            "failed_case_sha256s": list(self.failed_case_sha256s),
        }


class HarnessCheckExecutor(Protocol):
    def execute(
        self,
        descriptor: HarnessCheckDescriptor,
    ) -> CheckExecutionEvidence: ...


def _junit_counts(path: Path) -> dict[str, object]:
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError):
        return {
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 1,
            "skipped": 0,
            "duration_ms": 0,
            "failed_case_sha256s": (),
        }
    cases = root.findall(".//testcase")
    failures = [item for item in cases if item.find("failure") is not None]
    errors = [item for item in cases if item.find("error") is not None]
    skipped = [item for item in cases if item.find("skipped") is not None]
    failed_hashes = tuple(sorted({
        hashlib.sha256(
            (
                f"{item.attrib.get('classname', '')}::"
                f"{item.attrib.get('name', '')}"
            ).encode("utf-8")
        ).hexdigest()
        for item in (*failures, *errors)
    }))
    duration_seconds = 0.0
    for item in cases:
        try:
            duration_seconds += max(0.0, float(item.attrib.get("time", "0")))
        except (TypeError, ValueError):
            continue
    total = len(cases)
    return {
        "tests": total,
        "passed": total - len(failures) - len(errors) - len(skipped),
        "failures": len(failures),
        "errors": len(errors),
        "skipped": len(skipped),
        "duration_ms": int(duration_seconds * 1000),
        "failed_case_sha256s": failed_hashes,
    }


class PytestCheckExecutor:
    """只运行 Registry 固定 selector；不接受用户提供命令。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = {
            key: value
            for key in (
                "CONDA_PREFIX",
                "HOME",
                "LANG",
                "LC_ALL",
                "PATH",
                "PYTHONPATH",
                "TMPDIR",
                "VIRTUAL_ENV",
                "XDG_CACHE_HOME",
            )
            if (value := os.environ.get(key))
        }
        allowed.update({
            "DATABASE_URL": "sqlite:///:memory:",
            "NANOBOT_ADMIN_TOKEN": "harness-offline-token",
            "NANOBOT_TESTING": "1",
            "NEW_API_KEY": "harness-offline-key",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        })
        return allowed

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
                return
            except (OSError, subprocess.SubprocessError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                return
        process.kill()

    def execute(
        self,
        descriptor: HarnessCheckDescriptor,
    ) -> CheckExecutionEvidence:
        with tempfile.TemporaryDirectory(prefix="nanobot-harness-gate-") as temp:
            temp_dir = Path(temp)
            junit_path = temp_dir / "junit.xml"
            stdout_path = temp_dir / "stdout.bin"
            stderr_path = temp_dir / "stderr.bin"
            command = [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                *descriptor.selectors,
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={junit_path}",
            ]
            timed_out = False
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=self._root,
                    env=self._environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=(os.name == "posix"),
                )
                try:
                    return_code = process.wait(
                        timeout=descriptor.timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate(process)
                    return_code = process.wait()
            counts = _junit_counts(junit_path)
            stdout_sha256, stdout_bytes = _hash_file(stdout_path)
            stderr_sha256, stderr_bytes = _hash_file(stderr_path)
            return CheckExecutionEvidence(
                check_id=descriptor.check_id,
                return_code=return_code,
                timed_out=timed_out,
                tests=int(counts["tests"]),
                passed=int(counts["passed"]),
                failures=int(counts["failures"]),
                errors=int(counts["errors"]),
                skipped=int(counts["skipped"]),
                duration_ms=int(counts["duration_ms"]),
                stdout_sha256=stdout_sha256,
                stdout_bytes=stdout_bytes,
                stderr_sha256=stderr_sha256,
                stderr_bytes=stderr_bytes,
                failed_case_sha256s=tuple(counts["failed_case_sha256s"]),
            )


def _offline_suites(suite_ids: Sequence[str] | None) -> tuple[HarnessSuiteDescriptor, ...]:
    available = {
        descriptor.registry_id: descriptor
        for descriptor in EVAL_HARNESS_REGISTRY
        if descriptor.lane is EvaluationLane.OFFLINE_DETERMINISTIC
    }
    selected_ids = tuple(suite_ids or available.keys())
    if not selected_ids:
        raise HarnessGateError("至少选择一个离线 suite")
    if len(selected_ids) != len(set(selected_ids)):
        raise HarnessGateError("离线 suite 不能重复")
    unknown = sorted(set(selected_ids) - available.keys())
    if unknown:
        raise HarnessGateError(f"未知离线 suite: {', '.join(unknown)}")
    return tuple(available[suite_id] for suite_id in selected_ids)


def run_offline_gate(
    *,
    suite_ids: Sequence[str] | None = None,
    root: str | Path = ".",
    executor: HarnessCheckExecutor | None = None,
) -> dict[str, object]:
    """执行固定 pytest selector，并把 100% 通过作为唯一离线门槛。"""

    root_path = Path(root).resolve()
    selected = _offline_suites(suite_ids)
    check_executor = executor or PytestCheckExecutor(root_path)
    suites: list[dict[str, object]] = []
    total_checks = 0
    passed_checks = 0
    total_tests = 0
    passed_tests = 0
    duration_ms = 0
    for suite in selected:
        checks: list[dict[str, object]] = []
        for check in suite.checks:
            evidence = check_executor.execute(check)
            rendered = evidence.to_dict(check)
            checks.append(rendered)
            total_checks += 1
            total_tests += evidence.tests
            passed_tests += evidence.passed
            duration_ms += evidence.duration_ms
            if evidence.accepted:
                passed_checks += 1
        suite_passed = all(bool(item["accepted"]) for item in checks)
        suites.append({
            "suite_id": suite.registry_id,
            "title": suite.title,
            "domains": list(suite.domains),
            "status": "passed" if suite_passed else "failed",
            "blocking": True,
            "checks": checks,
            "metrics": {
                str(item["metric_id"]): item["pass_rate"]
                for item in checks
            },
            "wall_time_ms": sum(int(item["duration_ms"]) for item in checks),
        })
    passed = passed_checks == total_checks and total_checks > 0
    content = {
        "schema_version": HARNESS_GATE_SCHEMA_VERSION,
        "lane": EvaluationLane.OFFLINE_DETERMINISTIC.value,
        "authority": "blocking_gate",
        "blocking": True,
        "network_allowed": False,
        "model_calls_allowed": False,
        "production_data_mode": "forbidden",
        "source_revision": _source_revision(root_path),
        "registry": {
            "generation": EVAL_HARNESS_REGISTRY.generation,
            "sha256": EVAL_HARNESS_REGISTRY.sha256,
        },
        "status": "passed" if passed else "failed",
        "passed": passed,
        "summary": {
            "total_suites": len(suites),
            "passed_suites": sum(
                1 for suite in suites if suite["status"] == "passed"
            ),
            "total_checks": total_checks,
            "passed_checks": passed_checks,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "pass_rate": passed_tests / total_tests if total_tests else 0.0,
            "wall_time_ms": duration_ms,
        },
        "suites": suites,
    }
    return {**content, "report_sha256": _sha256_json(content)}


_EXTERNAL_EVIDENCE_FIELDS = frozenset({
    "schema_version",
    "lane",
    "suite_id",
    "run_id",
    "source_revision",
    "dataset_sha256",
    "started_at",
    "finished_at",
    "metrics",
    "model_call_count",
    "production_write_count",
    "production_data_access",
    "readonly",
    "explicit_opt_in",
    "approved_cost_microunits",
    "actual_cost_microunits",
    "raw_content_persisted",
    "artifact_sha256s",
})


def _aware_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise HarnessGateError(f"{name} 必须是 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HarnessGateError(f"{name} 不是有效 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarnessGateError(f"{name} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise HarnessGateError(f"{name} 必须是非负整数")
    return value


def _metric_value(value: object, kind: str, name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessGateError(f"metrics.{name} 必须是数值")
    if not math.isfinite(float(value)):
        raise HarnessGateError(f"metrics.{name} 必须是有限数值")
    if kind == "integer" and type(value) is not int:
        raise HarnessGateError(f"metrics.{name} 必须是整数")
    if kind == "rate" and not 0 <= float(value) <= 1:
        raise HarnessGateError(f"metrics.{name} 必须位于 0..1")
    return value


def validate_external_evidence(value: object) -> dict[str, object]:
    """验证真实模型或线上采样证据，但永远不授予提交阻断权。"""

    payload = _mapping(value, "evidence")
    _exact_keys(
        payload,
        name="evidence",
        required=_EXTERNAL_EVIDENCE_FIELDS,
    )
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != HARNESS_GATE_SCHEMA_VERSION
    ):
        raise HarnessGateError("evidence.schema_version 不受支持")
    try:
        lane = EvaluationLane(payload["lane"])
    except ValueError as exc:
        raise HarnessGateError("evidence.lane 无效") from exc
    if lane is EvaluationLane.OFFLINE_DETERMINISTIC:
        raise HarnessGateError("离线确定性 gate 必须实际执行，不能导入外部证据")
    suite_id = _safe_token(payload["suite_id"], "suite_id")
    descriptor = EVAL_HARNESS_REGISTRY.get(suite_id)
    if descriptor is None or descriptor.lane is not lane:
        raise HarnessGateError("suite_id 与 evidence.lane 不匹配")
    run_id = _safe_token(payload["run_id"], "run_id")
    revision = str(payload["source_revision"] or "").strip().lower()
    if _REVISION_RE.fullmatch(revision) is None:
        raise HarnessGateError("source_revision 必须是完整 Git 摘要")
    dataset_sha256 = str(payload["dataset_sha256"] or "").strip().lower()
    if _SHA256_RE.fullmatch(dataset_sha256) is None:
        raise HarnessGateError("dataset_sha256 必须是 SHA-256")
    started_at = _aware_timestamp(payload["started_at"], "started_at")
    finished_at = _aware_timestamp(payload["finished_at"], "finished_at")
    if finished_at < started_at:
        raise HarnessGateError("finished_at 不能早于 started_at")
    metrics = _mapping(payload["metrics"], "metrics")
    expected_metrics = {
        item.metric_id: item for item in descriptor.evidence_metrics
    }
    if set(metrics) != set(expected_metrics):
        missing = sorted(set(expected_metrics) - set(metrics))
        unknown = sorted(set(metrics) - set(expected_metrics))
        raise HarnessGateError(
            f"metrics 合同不匹配: missing={missing}, unknown={unknown}"
        )
    normalized_metrics: dict[str, float | int] = {}
    threshold_errors: list[str] = []
    for metric_id, contract in expected_metrics.items():
        metric = _metric_value(metrics[metric_id], contract.value_kind, metric_id)
        normalized_metrics[metric_id] = metric
        if contract.minimum is not None and metric < contract.minimum:
            threshold_errors.append(f"{metric_id}:below_minimum")
        if contract.maximum is not None and metric > contract.maximum:
            threshold_errors.append(f"{metric_id}:above_maximum")
    model_calls = _nonnegative_int(
        payload["model_call_count"],
        "model_call_count",
    )
    writes = _nonnegative_int(
        payload["production_write_count"],
        "production_write_count",
    )
    approved_cost = _nonnegative_int(
        payload["approved_cost_microunits"],
        "approved_cost_microunits",
    )
    actual_cost = _nonnegative_int(
        payload["actual_cost_microunits"],
        "actual_cost_microunits",
    )
    for name in (
        "production_data_access",
        "readonly",
        "explicit_opt_in",
        "raw_content_persisted",
    ):
        if type(payload[name]) is not bool:
            raise HarnessGateError(f"{name} 必须是 bool")
    artifacts = tuple(
        str(item or "").strip().lower()
        for item in _sequence(payload["artifact_sha256s"], "artifact_sha256s")
    )
    if not artifacts or any(_SHA256_RE.fullmatch(item) is None for item in artifacts):
        raise HarnessGateError("artifact_sha256s 必须包含至少一个 SHA-256")
    if len(artifacts) != len(set(artifacts)):
        raise HarnessGateError("artifact_sha256s 不能重复")

    policy_errors: list[str] = []
    if writes != 0:
        policy_errors.append("production_write_forbidden")
    if payload["raw_content_persisted"]:
        policy_errors.append("raw_content_persisted")
    if not payload["readonly"]:
        policy_errors.append("readonly_required")
    if lane is EvaluationLane.REAL_MODEL_BENCHMARK:
        if not payload["explicit_opt_in"]:
            policy_errors.append("explicit_opt_in_required")
        if payload["production_data_access"]:
            policy_errors.append("production_data_forbidden")
        if model_calls <= 0:
            policy_errors.append("real_model_call_required")
        if approved_cost <= 0:
            policy_errors.append("cost_budget_required")
        if actual_cost > approved_cost:
            policy_errors.append("cost_budget_exceeded")
    else:
        if model_calls != 0:
            policy_errors.append("model_calls_forbidden")
        if not payload["production_data_access"]:
            policy_errors.append("production_read_source_required")
        if approved_cost != 0 or actual_cost != 0:
            policy_errors.append("online_sampling_cost_must_be_zero")

    valid = not threshold_errors and not policy_errors
    content = {
        "schema_version": HARNESS_GATE_SCHEMA_VERSION,
        "lane": lane.value,
        "authority": descriptor.authority.value,
        "blocking_eligible": False,
        "valid": valid,
        "suite_id": suite_id,
        "run_id": run_id,
        "source_revision": revision,
        "dataset_sha256": dataset_sha256,
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "metrics": normalized_metrics,
        "threshold_errors": threshold_errors,
        "policy_errors": policy_errors,
        "model_call_count": model_calls,
        "production_write_count": writes,
        "actual_cost_microunits": actual_cost,
        "approved_cost_microunits": approved_cost,
        "artifact_sha256s": list(artifacts),
        "evidence_sha256": _sha256_json(payload),
    }
    return {**content, "report_sha256": _sha256_json(content)}


def _readonly_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: str,
    order_by: str,
    limit: int,
) -> list[tuple[Any, ...]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return []
    return list(connection.execute(
        f"SELECT {columns} FROM {table} ORDER BY {order_by} DESC LIMIT ?",
        (limit,),
    ).fetchall())


def sample_online_readonly(
    database_path: str | Path,
    *,
    limit: int = 1_000,
    root: str | Path = ".",
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """用 SQLite mode=ro 聚合无正文运行指标，并生成非阻断证据。"""

    if type(limit) is not int or not (1 <= limit <= 10_000):
        raise HarnessGateError("limit 必须位于 1..10000")
    path = Path(database_path).resolve()
    if not path.is_file():
        raise HarnessGateError("只读采样数据库不存在或不是文件")
    now = clock or (lambda: datetime.now(timezone.utc))
    started = now()
    if not isinstance(started, datetime) or started.tzinfo is None:
        raise HarnessGateError("采样 clock 必须返回带时区 datetime")
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
    except sqlite3.Error as exc:
        raise HarnessGateError("无法以只读模式打开采样数据库") from exc
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 3000")
        run_rows = _readonly_rows(
            connection,
            "agent_runs",
            "status",
            "started_at",
            limit,
        )
        recovery_rows = _readonly_rows(
            connection,
            "run_recovery_operations",
            "status",
            "prepared_at",
            limit,
        )
        llm_rows = _readonly_rows(
            connection,
            "llm_api_request_logs",
            "cache_hit, cost_microusd",
            "id",
            limit,
        )
    except sqlite3.Error as exc:
        raise HarnessGateError("线上只读采样查询失败") from exc
    finally:
        connection.close()
    finished = now()
    if not isinstance(finished, datetime) or finished.tzinfo is None:
        raise HarnessGateError("采样 clock 必须返回带时区 datetime")
    success_statuses = {"completed", "delivered", "success", "succeeded"}
    run_statuses = [str(row[0] or "").strip().lower() for row in run_rows]
    recovery_statuses = [
        str(row[0] or "").strip().lower() for row in recovery_rows
    ]
    success_count = sum(item in success_statuses for item in run_statuses)
    recovery_success = sum(item == "succeeded" for item in recovery_statuses)
    cache_observed = [row for row in llm_rows if row[0] is not None]
    cache_hits = sum(bool(row[0]) for row in cache_observed)
    costs = [max(0, int(row[1] or 0)) for row in llm_rows]
    metrics = {
        "sample_count": len(run_statuses),
        "success_rate": (
            success_count / len(run_statuses) if run_statuses else 0.0
        ),
        "recovery_rate": (
            recovery_success / len(recovery_statuses)
            if recovery_statuses
            else 0.0
        ),
        "cache_hit_rate": (
            cache_hits / len(cache_observed) if cache_observed else 0.0
        ),
        "average_cost_microunits": (
            sum(costs) / len(costs) if costs else 0.0
        ),
    }
    dataset_sha256 = _sha256_json({
        "run_statuses": run_statuses,
        "recovery_statuses": recovery_statuses,
        "cache_observed": [bool(row[0]) for row in cache_observed],
        "costs": costs,
        "limit": limit,
    })
    root_path = Path(root).resolve()
    revision = _source_revision(root_path)
    envelope = {
        "schema_version": HARNESS_GATE_SCHEMA_VERSION,
        "lane": EvaluationLane.ONLINE_READONLY_SAMPLING.value,
        "suite_id": "online_readonly_runtime_sample",
        "run_id": f"online-sample-{dataset_sha256[:24]}",
        "source_revision": revision,
        "dataset_sha256": dataset_sha256,
        "started_at": started.astimezone(timezone.utc).isoformat(),
        "finished_at": finished.astimezone(timezone.utc).isoformat(),
        "metrics": metrics,
        "model_call_count": 0,
        "production_write_count": 0,
        "production_data_access": True,
        "readonly": True,
        "explicit_opt_in": False,
        "approved_cost_microunits": 0,
        "actual_cost_microunits": 0,
        "raw_content_persisted": False,
        "artifact_sha256s": [dataset_sha256],
    }
    return {
        "evidence": envelope,
        "validation": validate_external_evidence(envelope),
    }


def _load_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise HarnessGateError(f"无法读取 JSON: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessGateError(f"JSON 格式无效: {exc}") from exc


def _emit(value: object, output: str) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if not output:
        print(rendered)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{rendered}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Harness 分层评测门禁")
    commands = parser.add_subparsers(dest="command", required=True)

    catalog = commands.add_parser("catalog")
    catalog.add_argument("--output", default="")

    offline = commands.add_parser("offline")
    selection = offline.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--suite", action="append", default=[])
    offline.add_argument("--output", default="")

    validate = commands.add_parser("validate-evidence")
    validate.add_argument("--input", required=True)
    validate.add_argument("--output", default="")

    sample = commands.add_parser("sample-online")
    sample.add_argument("--db", required=True)
    sample.add_argument("--limit", type=int, default=1_000)
    sample.add_argument("--output", default="")
    args = parser.parse_args(argv)

    try:
        if args.command == "catalog":
            report = harness_catalog_payload()
            exit_code = 0
        elif args.command == "offline":
            report = run_offline_gate(
                suite_ids=None if args.all else args.suite,
            )
            exit_code = 0 if report["passed"] else 1
        elif args.command == "validate-evidence":
            report = validate_external_evidence(_load_json(args.input))
            exit_code = 0 if report["valid"] else 1
        else:
            report = sample_online_readonly(args.db, limit=args.limit)
            exit_code = 0 if report["validation"]["valid"] else 1
        _emit(report, args.output)
        return exit_code
    except HarnessGateError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CheckExecutionEvidence",
    "HARNESS_GATE_SCHEMA_VERSION",
    "HarnessCheckExecutor",
    "HarnessGateError",
    "PytestCheckExecutor",
    "main",
    "run_offline_gate",
    "sample_online_readonly",
    "validate_external_evidence",
]
