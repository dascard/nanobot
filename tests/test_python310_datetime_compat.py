import os
from pathlib import Path


def test_python310_compatible_datetime_utc_usage():
    """Docker 运行时使用 Python 3.10，避免引入 3.11 才支持的 datetime 常量。"""
    bad_import = "from datetime import " + "U" + "TC"
    bad_attr = "datetime." + "U" + "TC"
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    excluded_directories = {
        ".agents",
        ".cache",
        ".claude",
        ".codex",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".qoder",
        ".ruff_cache",
        ".tmp",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "data",
        "dist",
        "models",
        "node_modules",
        "vendor",
        "venv",
        "workspace",
    }

    for current_root, directories, filenames in os.walk(root):
        directories[:] = [
            name for name in directories
            if name not in excluded_directories
        ]
        current_path = Path(current_root)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = current_path / filename
            text = path.read_text(encoding="utf-8")
            if bad_import in text or bad_attr in text:
                offenders.append(path.relative_to(root).as_posix())

    assert offenders == []
