from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".agents",
    ".codex",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "cc2codex",
    "dist",
    "htmlcov",
    "node_modules",
    "sentinel",
    "site-packages",
    "venv",
    "vendor",
}
ASYNCIO_NEEDLES = ("asyncio",)
RG_EXCLUDE_GLOBS = tuple(f"!{skip}/**" for skip in sorted(SKIP_DIRS))


def _is_main_guard(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_dynamic_asyncio_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "__import__"
        and len(node.args) >= 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "asyncio"
    )


def _asyncio_run_calls_outside_main_guard(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    asyncio_module_aliases = {"asyncio"}
    asyncio_run_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "asyncio":
                    asyncio_module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for alias in node.names:
                if alias.name == "run":
                    asyncio_run_aliases.add(alias.asname or alias.name)

    bad_lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_asyncio_run = (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and isinstance(func.value, ast.Name)
            and func.value.id in asyncio_module_aliases
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and _is_dynamic_asyncio_import(func.value)
        ) or (
            isinstance(func, ast.Name)
            and func.id in asyncio_run_aliases
        )
        if not is_asyncio_run:
            continue

        current: ast.AST | None = node
        under_main = False
        while current is not None:
            if _is_main_guard(current):
                under_main = True
                break
            current = parents.get(current)
        if not under_main:
            bad_lines.append(int(getattr(node, "lineno", 0)))
    return bad_lines


def _candidate_asyncio_run_files_with_python(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            path = Path(current_root) / filename
            rel_parts = path.relative_to(root).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            content = path.read_text(encoding="utf-8")
            if any(needle in content for needle in ASYNCIO_NEEDLES):
                candidates.append(path)
    return candidates


def _candidate_asyncio_run_files(root: Path) -> list[Path]:
    matches: set[Path] = set()
    base_args = ["rg", "-l", "--null", "--fixed-strings", "-g", "*.py"]
    for glob in RG_EXCLUDE_GLOBS:
        base_args.extend(["-g", glob])

    for needle in ASYNCIO_NEEDLES:
        try:
            completed = subprocess.run(
                [*base_args, needle],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return _candidate_asyncio_run_files_with_python(root)
        if completed.returncode not in (0, 1):
            return _candidate_asyncio_run_files_with_python(root)
        for item in completed.stdout.split("\0"):
            if item:
                matches.add(root / item)
    return sorted(matches)


def test_candidate_asyncio_run_files_skip_files_without_matching_text(tmp_path):
    irrelevant = tmp_path / "irrelevant.py"
    irrelevant.write_text("print('hello')\n", encoding="utf-8")
    main_only = tmp_path / "main_only.py"
    main_only.write_text(
        "import asyncio\n"
        "async def main():\n"
        "    pass\n"
        "if __name__ == '__main__':\n"
        f"    {'asyncio'}.run(main())\n",
        encoding="utf-8",
    )
    alias = tmp_path / "alias.py"
    alias.write_text(f"import asyncio as _asyncio\n{'_asyncio'}.run(main())\n", encoding="utf-8")
    custom_alias = tmp_path / "custom_alias.py"
    custom_alias.write_text("import asyncio as aio\naio.run(main())\n", encoding="utf-8")
    from_import = tmp_path / "from_import.py"
    from_import.write_text("from asyncio import run\nrun(main())\n", encoding="utf-8")

    assert list(_candidate_asyncio_run_files(tmp_path)) == [alias, custom_alias, from_import, main_only]


def test_asyncio_run_detection_handles_import_aliases(tmp_path):
    custom_alias = tmp_path / "custom_alias.py"
    custom_alias.write_text("import asyncio as aio\naio.run(main())\n", encoding="utf-8")
    from_import = tmp_path / "from_import.py"
    from_import.write_text("from asyncio import run as run_async\nrun_async(main())\n", encoding="utf-8")
    dynamic_import = tmp_path / "dynamic_import.py"
    dynamic_import.write_text('__import__("asyncio").run(main())\n', encoding="utf-8")
    guarded_alias = tmp_path / "guarded_alias.py"
    guarded_alias.write_text(
        "import asyncio as aio\n"
        "if __name__ == '__main__':\n"
        "    aio.run(main())\n",
        encoding="utf-8",
    )

    assert _asyncio_run_calls_outside_main_guard(custom_alias) == [2]
    assert _asyncio_run_calls_outside_main_guard(from_import) == [2]
    assert _asyncio_run_calls_outside_main_guard(dynamic_import) == [1]
    assert _asyncio_run_calls_outside_main_guard(guarded_alias) == []


def test_asyncio_run_only_appears_under_main_guard():
    offenders: list[str] = []
    for path in _candidate_asyncio_run_files(ROOT):
        bad_lines = _asyncio_run_calls_outside_main_guard(path)
        offenders.extend(f"{path.relative_to(ROOT)}:{line}" for line in bad_lines)

    assert offenders == []
