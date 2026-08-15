"""集中解析应用进程可写目录；业务模块不得写入源码树。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil

from core.config_registry import SETTING_DEFS
from core.settings_specs import resolve_boot_setting_value


def _configured_directory(setting_key: str) -> Path:
    spec = SETTING_DEFS[setting_key]
    raw = str(resolve_boot_setting_value(spec, os.environ) or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError(f"{spec.env_name} 必须是有效目录")
    return Path(raw).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """只包含 operator 控制的启动期路径，不接受请求或模型输入。"""

    data_dir: Path
    temp_dir: Path

    @classmethod
    def from_environment(cls) -> "RuntimePaths":
        return cls(
            data_dir=_configured_directory("runtime.data_dir"),
            temp_dir=_configured_directory("runtime.temp_dir"),
        )

    @property
    def rag_benchmark_manual_dir(self) -> Path:
        return self.data_dir / "evals" / "cases" / "rag_benchmark" / "manual"

    @property
    def rag_benchmark_report_dir(self) -> Path:
        return self.data_dir / "evals" / "rag_benchmark" / "reports"

    @property
    def rag_benchmark_generated_dir(self) -> Path:
        return self.temp_dir / "rag_benchmark" / "generated"

    @property
    def rag_benchmark_backup_dir(self) -> Path:
        return self.temp_dir / "rag_benchmark" / "case_backups"

    @property
    def rag_benchmark_trash_dir(self) -> Path:
        return self.temp_dir / "rag_benchmark" / "case_trash"

    @property
    def rag_benchmark_lock(self) -> Path:
        return self.temp_dir / "rag_benchmark" / "run.lock"

    @property
    def eval_cases_dir(self) -> Path:
        return self.data_dir / "evals" / "cases"

    @property
    def evolution_control_dir(self) -> Path:
        return self.data_dir / "evals" / "evolution_control"

    @property
    def skill_candidate_dir(self) -> Path:
        return self.data_dir / "evals" / "skill_candidates"


RUNTIME_PATHS = RuntimePaths.from_environment()


def prepare_rag_benchmark_runtime(
    paths: RuntimePaths | None = None,
    *,
    bundled_manual_dir: Path | None = None,
) -> dict[str, int]:
    """初始化 RAG Benchmark 可写目录，并一次性投影新增内置案例。"""

    resolved = paths or RUNTIME_PATHS
    directories = (
        resolved.rag_benchmark_manual_dir,
        resolved.rag_benchmark_report_dir,
        resolved.rag_benchmark_generated_dir,
        resolved.rag_benchmark_backup_dir,
        resolved.rag_benchmark_trash_dir,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    source_dir = bundled_manual_dir or (
        Path(__file__).resolve().parents[1]
        / "evals"
        / "cases"
        / "rag_benchmark"
        / "manual"
    )
    marker = resolved.rag_benchmark_manual_dir / ".bundled_cases.seeded"
    seeded_names = {
        line.strip()
        for line in marker.read_text(encoding="utf-8").splitlines()
        if line.strip()
    } if marker.exists() else set()
    seeded_count = 0
    if source_dir.is_dir():
        for source in sorted(source_dir.glob("*.json")):
            if source.name in seeded_names:
                continue
            target = resolved.rag_benchmark_manual_dir / source.name
            if not target.exists():
                temporary = target.with_suffix(".json.seed.tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
                seeded_count += 1
            seeded_names.add(source.name)

    marker_tmp = marker.with_suffix(".tmp")
    marker_tmp.write_text(
        "".join(f"{name}\n" for name in sorted(seeded_names)),
        encoding="utf-8",
    )
    os.replace(marker_tmp, marker)
    return {
        "directories": len(directories),
        "seeded_cases": seeded_count,
    }
