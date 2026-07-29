#!/usr/bin/env python3
"""生成受 .dockerignore 约束的 Runtime 构建上下文清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class BuildContextManifestError(RuntimeError):
    """构建上下文无法安全、确定性枚举。"""


@dataclass(frozen=True, slots=True)
class _IgnoreRule:
    negated: bool
    regex: re.Pattern[str]


_FORBIDDEN_ROOTS = frozenset({
    ".git",
    "assets",
    "data",
    "models",
    "runtime",
    "sentinel",
    "tmp",
    "workspace",
    "workspaces",
})


def _glob_regex(pattern: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    result.append("(?:.*/)?")
                    index += 1
                else:
                    result.append(".*")
                continue
            result.append("[^/]*")
        elif char == "?":
            result.append("[^/]")
        elif char == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1:
                result.append(r"\[")
            else:
                content = pattern[index + 1 : closing]
                if content.startswith("!"):
                    content = "^" + content[1:]
                result.append("[" + content + "]")
                index = closing
        else:
            result.append(re.escape(char))
        index += 1
    return "".join(result)


def parse_dockerignore(text: str) -> tuple[_IgnoreRule, ...]:
    rules: list[_IgnoreRule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        line = line.replace("\\", "/").lstrip("/")
        if not line or line == ".":
            continue
        line = line.rstrip("/")
        if not line:
            continue
        prefix = "^" if "/" in line else r"^(?:.*/)?"
        rules.append(_IgnoreRule(
            negated=negated,
            regex=re.compile(
                prefix + _glob_regex(line) + r"(?:/.*)?$"
            ),
        ))
    return tuple(rules)


def is_ignored(path: str, rules: tuple[_IgnoreRule, ...]) -> bool:
    ignored = False
    for rule in rules:
        if rule.regex.fullmatch(path):
            ignored = not rule.negated
    return ignored


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_not_forbidden(relative: str) -> None:
    parts = relative.split("/")
    root = parts[0]
    if root in _FORBIDDEN_ROOTS:
        raise BuildContextManifestError(
            f"敏感或持久目录进入构建上下文：{relative}"
        )
    if root == ".env" or (
        root.startswith(".env.") and root != ".env.example"
    ):
        raise BuildContextManifestError(
            f"环境凭据文件进入构建上下文：{relative}"
        )
    if ".git" in parts:
        raise BuildContextManifestError(
            f"Git 元数据进入构建上下文：{relative}"
        )


def build_context_manifest(
    root: Path,
    *,
    dockerignore: Path | None = None,
    include_git_identity: bool = False,
) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    ignore_path = (
        dockerignore.resolve(strict=True)
        if dockerignore is not None
        else (resolved_root / ".dockerignore").resolve(strict=True)
    )
    try:
        ignore_path.relative_to(resolved_root)
    except ValueError as exc:
        raise BuildContextManifestError(
            ".dockerignore 必须位于构建上下文内"
        ) from exc
    ignore_bytes = ignore_path.read_bytes()
    try:
        ignore_text = ignore_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildContextManifestError(
            ".dockerignore 必须是 UTF-8"
        ) from exc
    rules = parse_dockerignore(ignore_text)
    files: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(resolved_root.rglob("*")):
        relative = path.relative_to(resolved_root).as_posix()
        if is_ignored(relative, rules):
            continue
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise BuildContextManifestError(
                f"构建上下文不允许符号链接：{relative}"
            )
        if not stat.S_ISREG(metadata.st_mode):
            raise BuildContextManifestError(
                f"构建上下文只允许普通文件：{relative}"
            )
        _assert_not_forbidden(relative)
        total_bytes += int(metadata.st_size)
        files.append({
            "path": relative,
            "size_bytes": int(metadata.st_size),
            "mode": stat.S_IMODE(metadata.st_mode),
            "sha256": _sha256_file(path),
        })
    identity = {
        "dockerignore_sha256": hashlib.sha256(ignore_bytes).hexdigest(),
        "files": files,
    }
    canonical_identity = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    manifest: dict[str, object] = {
        "schema_version": 1,
        **identity,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "build_context_sha256": hashlib.sha256(
            canonical_identity
        ).hexdigest(),
    }
    if include_git_identity:
        manifest["untracked_context_files"] = (
            _git_untracked_context_files(
                resolved_root,
                tuple(str(item["path"]) for item in files),
            )
        )
    return manifest


def _git_output(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ("git", *arguments),
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildContextManifestError(
            "无法解析构建上下文的 Git 身份"
        ) from exc


def _git_untracked_context_files(
    root: Path,
    context_files: tuple[str, ...],
) -> list[str]:
    staged = _git_output(root, "ls-files", "--stage", "-z")
    tracked: set[str] = set()
    gitlinks: set[str] = set()
    for raw_entry in staged.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        if not separator:
            raise BuildContextManifestError("Git index 记录无法解析")
        try:
            path = raw_path.decode("utf-8")
            mode = metadata.split(b" ", 1)[0].decode("ascii")
        except UnicodeError as exc:
            raise BuildContextManifestError(
                "Git index 路径编码无效"
            ) from exc
        tracked.add(path)
        if mode == "160000":
            gitlinks.add(path)
    return [
        path
        for path in context_files
        if path not in tracked
        and not any(path.startswith(f"{gitlink}/") for gitlink in gitlinks)
    ]


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dockerignore", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--print-sha", action="store_true")
    parser.add_argument("--include-git-identity", action="store_true")
    args = parser.parse_args(argv)
    manifest = build_context_manifest(
        args.root,
        dockerignore=args.dockerignore,
        include_git_identity=args.include_git_identity,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    if args.print_sha:
        print(manifest["build_context_sha256"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except BuildContextManifestError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
