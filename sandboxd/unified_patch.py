"""针对单个 Workspace 文本文件的严格统一 diff 应用器。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.paths import validate_relative_path


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>[0-9]+)(?:,(?P<old_count>[0-9]+))? "
    r"\+(?P<new_start>[0-9]+)(?:,(?P<new_count>[0-9]+))? "
    r"@@(?: .*)?$"
)
_NO_NEWLINE_MARKER = "\\ No newline at end of file"


@dataclass(frozen=True, slots=True)
class AppliedPatch:
    content: str
    hunk_count: int
    added_lines: int
    removed_lines: int


def _invalid_patch(summary: str = "Workspace 补丁格式无效或上下文不匹配") -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.AUTHORIZATION_FAILED,
        summary,
        hint="重新读取目标文件，并基于当前内容生成单文件统一 diff",
    )


def _normalized_path(value: str) -> str:
    return "/".join(validate_relative_path(value))


def _header_path(value: str, *, prefix: str) -> str:
    raw = value.rstrip("\r\n")
    if "\t" in raw:
        raw = raw.split("\t", 1)[0]
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            raise _invalid_patch() from exc
        if len(parts) != 1:
            raise _invalid_patch()
        raw = parts[0]
    expected_prefix = f"{prefix}/"
    if raw.startswith(expected_prefix):
        raw = raw[len(expected_prefix):]
    return _normalized_path(raw)


def _without_line_ending(value: str) -> str:
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n") or value.endswith("\r"):
        return value[:-1]
    return value


def _preprocess_lines(patch: str) -> list[str]:
    if not isinstance(patch, str) or not patch or "\x00" in patch:
        raise _invalid_patch()
    raw_lines = patch.splitlines(keepends=True)
    result: list[str] = []
    for line in raw_lines:
        if _without_line_ending(line) == _NO_NEWLINE_MARKER:
            if not result or not result[-1].startswith((" ", "+", "-")):
                raise _invalid_patch()
            result[-1] = _without_line_ending(result[-1])
            continue
        result.append(line)
    return result


def _validate_file_headers(
    lines: list[str],
    *,
    path: str,
) -> int:
    """校验可选 git/diff 文件头，并返回首个 hunk 的索引。"""

    index = 0
    saw_old = False
    saw_new = False
    while index < len(lines):
        line = _without_line_ending(lines[index])
        if line.startswith("@@ "):
            break
        if line.startswith("diff --git "):
            if index != 0:
                raise _invalid_patch()
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                raise _invalid_patch() from exc
            if (
                len(parts) != 4
                or _header_path(parts[2], prefix="a") != path
                or _header_path(parts[3], prefix="b") != path
            ):
                raise _invalid_patch()
        elif line.startswith("index "):
            if index == 0:
                raise _invalid_patch()
        elif line.startswith("--- "):
            if saw_old or saw_new:
                raise _invalid_patch()
            if _header_path(line[4:], prefix="a") != path:
                raise _invalid_patch()
            saw_old = True
        elif line.startswith("+++ "):
            if not saw_old or saw_new:
                raise _invalid_patch()
            if _header_path(line[4:], prefix="b") != path:
                raise _invalid_patch()
            saw_new = True
        elif line:
            raise _invalid_patch()
        index += 1
    if saw_old != saw_new:
        raise _invalid_patch()
    return index


def apply_unified_patch(
    source: str,
    patch: str,
    *,
    path: str,
) -> AppliedPatch:
    """严格应用单文件 unified diff，不做模糊上下文匹配。"""

    normalized_path = _normalized_path(path)
    if not isinstance(source, str) or "\x00" in source:
        raise SandboxServiceError(
            SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
            "Workspace 补丁只支持 UTF-8 文本文件",
        )
    lines = _preprocess_lines(patch)
    index = _validate_file_headers(lines, path=normalized_path)
    source_lines = source.splitlines(keepends=True)
    output: list[str] = []
    source_cursor = 0
    hunk_count = 0
    added_lines = 0
    removed_lines = 0
    changed = False

    while index < len(lines):
        header = _without_line_ending(lines[index])
        match = _HUNK_HEADER_RE.fullmatch(header)
        if match is None:
            raise _invalid_patch()
        hunk_count += 1
        old_start = int(match.group("old_start"))
        old_count = int(
            match.group("old_count")
            if match.group("old_count") is not None
            else 1
        )
        new_start = int(match.group("new_start"))
        new_count = int(
            match.group("new_count")
            if match.group("new_count") is not None
            else 1
        )
        old_index = 0 if old_start == 0 else old_start - 1
        new_index = 0 if new_start == 0 else new_start - 1
        if (
            old_index < source_cursor
            or old_index > len(source_lines)
            or new_index != len(output) + old_index - source_cursor
        ):
            raise _invalid_patch()
        output.extend(source_lines[source_cursor:old_index])
        source_cursor = old_index
        index += 1
        old_seen = 0
        new_seen = 0

        while index < len(lines):
            line = lines[index]
            if _without_line_ending(line).startswith("@@ "):
                break
            if not line or line[0] not in {" ", "+", "-"}:
                raise _invalid_patch()
            marker = line[0]
            body = line[1:]
            if marker in {" ", "-"}:
                if (
                    source_cursor >= len(source_lines)
                    or source_lines[source_cursor] != body
                ):
                    raise _invalid_patch()
                source_cursor += 1
                old_seen += 1
            if marker in {" ", "+"}:
                output.append(body)
                new_seen += 1
            if marker == "+":
                added_lines += 1
                changed = True
            elif marker == "-":
                removed_lines += 1
                changed = True
            index += 1

        if old_seen != old_count or new_seen != new_count:
            raise _invalid_patch()

    if hunk_count == 0 or not changed:
        raise _invalid_patch("Workspace 补丁没有可应用的变更")
    output.extend(source_lines[source_cursor:])
    return AppliedPatch(
        content="".join(output),
        hunk_count=hunk_count,
        added_lines=added_lines,
        removed_lines=removed_lines,
    )


__all__ = ["AppliedPatch", "apply_unified_patch"]
