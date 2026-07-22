"""集中解析应用进程可写目录；业务模块不得写入源码树。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

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


RUNTIME_PATHS = RuntimePaths.from_environment()
