from pathlib import Path


def test_python310_compatible_datetime_utc_usage():
    """Docker 运行时使用 Python 3.10，避免引入 3.11 才支持的 datetime 常量。"""
    bad_import = "from datetime import " + "U" + "TC"
    bad_attr = "datetime." + "U" + "TC"
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("vendor/", ".venv/", "venv/")):
            continue
        text = path.read_text(encoding="utf-8")
        if bad_import in text or bad_attr in text:
            offenders.append(rel)

    assert offenders == []
