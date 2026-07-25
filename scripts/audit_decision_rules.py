#!/usr/bin/env python3
"""盘点仓库中的硬编码决策、正则和路由字面量。

本脚本只负责发现与分类候选，不自动修改业务代码。自动分类是保守的初筛，
人工结论通过稳定 ``rule_id`` 覆盖，并在生成清单时进行完整性校验。
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

DEFAULT_SCAN_ROOTS = (
    "core",
    "api",
    "app",
    "clients",
    "nanobot_kt",
    "creatures",
    "bootstrap",
    "foundation",
    "sandboxd",
    "scripts",
    "workers",
    "webui/src",
    "config.py",
    "server.py",
)

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "docs",
    "node_modules",
    "tests",
    "vendor",
}

EXCLUDED_FILE_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}

SUPPORTED_CATEGORIES = {
    "protocol_syntax",
    "security_invariant",
    "data_consistency",
    "configurable_policy",
    "natural_language_semantic",
    "compatibility",
    "presentation",
}

SUPPORTED_DISPOSITIONS = {
    "preserve",
    "configure",
    "policy",
    "model_signal_only",
    "compatibility_migration",
    "delete",
    "resource",
}

SUPPORTED_REVIEW_STATUSES = {
    "auto_classified",
    "reviewed",
    "approved",
    "rejected",
}

OVERRIDABLE_FIELDS = {
    "category",
    "disposition",
    "review_status",
    "reason",
}

GROUP_MATCH_FIELDS = {
    "category",
    "detector",
    "detectors",
    "disposition",
    "language",
    "owner",
    "path",
    "path_glob",
    "plan_stage",
}

PYTHON_SUFFIXES = {".py"}
WEB_SUFFIXES = {".js", ".jsx", ".mjs", ".ts", ".tsx"}
SHELL_SUFFIXES = {".bash", ".sh"}

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SPACE_RE = re.compile(r"\s+")
_WEB_REGEX_RE = re.compile(
    r"/(?![/*])(?:\\.|[^/\\\n])+/[dgimsuvy]*",
)
_WEB_ROUTE_RE = re.compile(
    r"(?P<quote>['\"`])(?P<route>/(?![/*])[^'\"`\s]{1,240})(?P=quote)",
)
_SHELL_REGEX_RE = re.compile(r"\[\[.*?=~\s*(?P<pattern>.+?)\s*\]\]")
_SHELL_LITERAL_TEST_RE = re.compile(
    r"\[\[.*?(?:==|!=|-eq|-ne|-gt|-ge|-lt|-le)\s+"
    r"(?P<value>\"[^\"]*\"|'[^']*'|-?\d+(?:\.\d+)?)",
)
_SHELL_CASE_PATTERN_RE = re.compile(r"^\s*(?P<pattern>[^)#]+)\)\s*")
_POLICY_SYMBOL_RE = re.compile(
    r"(?:^|_)(?:"
    r"action|allow|block|capabilit|category|compat|deny|disposition|"
    r"enum|feature|kind|legacy|limit|marker|mode|model|pattern|policy|"
    r"prefix|priority|provider|registry|retry|route|rule|scope|status|"
    r"suffix|support|task|threshold|timeout|tool|type"
    r")(?:_|$)",
    re.IGNORECASE,
)


class AuditConfigurationError(ValueError):
    """审计配置或人工覆盖无效。"""


@dataclass(frozen=True, slots=True)
class DecisionRule:
    """一条可复核的决策规则候选。"""

    rule_id: str
    path: str
    line: int
    language: str
    detector: str
    category: str
    owner: str
    control_flow: bool
    model_call: bool
    disposition: str
    confidence: float
    review_status: str
    excerpt: str
    normalized_fingerprint: str
    reason: str
    input_boundary: str
    current_tests: tuple[str, ...]
    plan_stage: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AuditError:
    """单个文件无法分析时的稳定错误记录。"""

    path: str
    error_type: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AuditReport:
    """完整审计报告。"""

    source_revision: str
    scan_roots: tuple[str, ...]
    rules: tuple[DecisionRule, ...]
    errors: tuple[AuditError, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def summary(self) -> dict[str, Any]:
        by_category = Counter(rule.category for rule in self.rules)
        by_disposition = Counter(rule.disposition for rule in self.rules)
        by_detector = Counter(rule.detector for rule in self.rules)
        by_review_status = Counter(rule.review_status for rule in self.rules)
        return {
            "rules_total": len(self.rules),
            "errors_total": len(self.errors),
            "by_category": dict(sorted(by_category.items())),
            "by_disposition": dict(sorted(by_disposition.items())),
            "by_detector": dict(sorted(by_detector.items())),
            "by_review_status": dict(sorted(by_review_status.items())),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_revision": self.source_revision,
            "scan_roots": list(self.scan_roots),
            "summary": self.summary(),
            "rules": [rule.to_dict() for rule in self.rules],
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: str
    line: int
    language: str
    detector: str
    control_flow: bool
    excerpt: str
    normalized_source: str


def _compact_excerpt(value: str, *, limit: int = 240) -> str:
    compact = _SPACE_RE.sub(" ", value).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment:
        return _compact_excerpt(segment)
    lines = source.splitlines()
    line_number = max(int(getattr(node, "lineno", 1)), 1)
    if line_number <= len(lines):
        return _compact_excerpt(lines[line_number - 1])
    return node.__class__.__name__


def _ast_fingerprint(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _contains_string(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str)
        for child in ast.walk(node)
    )


def _contains_number(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, (int, float))
        and not isinstance(child.value, bool)
        for child in ast.walk(node)
    )


def _literal_string_collection(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return False
    values = [
        item.value
        for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    return len(values) >= 2 and len(values) == len(node.elts)


def _literal_string_mapping(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict) or len(node.keys) < 2:
        return False
    scalar_pairs = 0
    for key, value in zip(node.keys, node.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if isinstance(value, ast.Constant) and isinstance(
            value.value,
            (str, int, float, bool, type(None)),
        ):
            scalar_pairs += 1
    return scalar_pairs >= 2


def _call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        parts = [function.attr]
        value = function.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


class _PythonRuleVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: str,
        source: str,
        *,
        control_names: set[str],
        literal_symbols: set[str],
        regex_symbols: set[str],
    ) -> None:
        self.path = path
        self.source = source
        self.control_names = control_names
        self.literal_symbols = literal_symbols
        self.regex_symbols = regex_symbols
        self.candidates: list[_Candidate] = []

    def _add(
        self,
        node: ast.AST,
        detector: str,
        *,
        control_flow: bool,
        normalized_node: ast.AST | None = None,
    ) -> None:
        normalized = _ast_fingerprint(normalized_node or node)
        self.candidates.append(
            _Candidate(
                path=self.path,
                line=max(int(getattr(node, "lineno", 1)), 1),
                language="python",
                detector=detector,
                control_flow=control_flow,
                excerpt=_source_segment(self.source, node),
                normalized_source=normalized,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _call_name(node)
        regex_method = call_name.rsplit(".", 1)[-1]
        regex_owner = call_name.rsplit(".", 1)[0] if "." in call_name else ""
        compiled_symbol_call = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.regex_symbols
        )
        if (
            regex_owner in {"re", "regex"}
            and regex_method
            in {
                "compile",
                "findall",
                "finditer",
                "fullmatch",
                "match",
                "search",
                "split",
                "sub",
                "subn",
            }
        ) or (
            compiled_symbol_call
            and regex_method
            in {
                "findall",
                "finditer",
                "fullmatch",
                "match",
                "search",
                "split",
                "sub",
                "subn",
            }
        ):
            self._add(node, "python.regex_call", control_flow=False)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {
                "endswith",
                "startswith",
            }
            and (
                any(_contains_string(argument) for argument in node.args)
                or any(
                    isinstance(argument, ast.Name)
                    and argument.id in self.literal_symbols
                    for argument in node.args
                )
            )
        ):
            self._add(
                node,
                "python.string_control_flow",
                control_flow=True,
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        names = {
            target.id for target in node.targets if isinstance(target, ast.Name)
        }
        self._visit_literal_assignment(node, node.value, names)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        names = {node.target.id} if isinstance(node.target, ast.Name) else set()
        if node.value is not None:
            self._visit_literal_assignment(node, node.value, names)
        self.generic_visit(node)

    def _visit_literal_assignment(
        self,
        node: ast.Assign | ast.AnnAssign,
        value: ast.AST,
        names: set[str],
    ) -> None:
        if not names or not any(
            name in self.control_names or _POLICY_SYMBOL_RE.search(name)
            for name in names
        ):
            return
        if _literal_string_collection(value):
            self._add(
                node,
                "python.literal_collection",
                control_flow=False,
                normalized_node=value,
            )
        elif _literal_string_mapping(value):
            self._add(
                node,
                "python.literal_mapping",
                control_flow=False,
                normalized_node=value,
            )

    def visit_Compare(self, node: ast.Compare) -> None:
        if _contains_string(node):
            self._add(
                node,
                "python.string_control_flow",
                control_flow=True,
            )
        elif _contains_number(node):
            self._add(
                node,
                "python.numeric_control_flow",
                control_flow=True,
            )
        self.generic_visit(node)

    def visit_MatchValue(self, node: ast.MatchValue) -> None:
        if _contains_string(node):
            self._add(node, "python.match_literal", control_flow=True)
        elif _contains_number(node):
            self._add(node, "python.match_literal", control_flow=True)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_route_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_route_decorators(node)
        self.generic_visit(node)

    def _visit_route_decorators(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            call_name = _call_name(decorator)
            method = call_name.rsplit(".", 1)[-1].lower()
            if method not in {
                "delete",
                "get",
                "head",
                "options",
                "patch",
                "post",
                "put",
                "route",
                "websocket",
            }:
                continue
            if not decorator.args or not _contains_string(decorator.args[0]):
                continue
            self._add(
                decorator,
                "python.route_literal",
                control_flow=False,
                normalized_node=decorator,
            )


def _scan_python(path: Path, relative_path: str) -> tuple[list[_Candidate], AuditError | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], _safe_audit_error(relative_path, exc)
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        summary = f"{exc.msg}（第 {exc.lineno or 0} 行）"
        return [], AuditError(relative_path, "SyntaxError", summary)
    control_names = _collect_control_names(tree)
    literal_symbols, regex_symbols = _collect_literal_symbols(tree)
    visitor = _PythonRuleVisitor(
        relative_path,
        source,
        control_names=control_names,
        literal_symbols=literal_symbols,
        regex_symbols=regex_symbols,
    )
    visitor.visit(tree)
    return visitor.candidates, None


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    if isinstance(node, ast.AnnAssign):
        return {node.target.id} if isinstance(node.target, ast.Name) else set()
    return {
        target.id for target in node.targets if isinstance(target, ast.Name)
    }


def _collect_literal_symbols(tree: ast.AST) -> tuple[set[str], set[str]]:
    literal_symbols: set[str] = set()
    regex_symbols: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        names = _assigned_names(node)
        if _literal_string_collection(value) or _literal_string_mapping(value):
            literal_symbols.update(names)
        if (
            isinstance(value, ast.Call)
            and _call_name(value) in {"re.compile", "regex.compile"}
        ):
            regex_symbols.update(names)
    return literal_symbols, regex_symbols


def _collect_control_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    expressions: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            expressions.append(node.test)
        elif isinstance(node, ast.Compare):
            expressions.append(node)
        elif isinstance(node, ast.Match):
            expressions.append(node.subject)
    for expression in expressions:
        names.update(
            child.id
            for child in ast.walk(expression)
            if isinstance(child, ast.Name)
        )
    return names


def _looks_like_web_regex(line: str, start: int) -> bool:
    prefix = line[:start].rstrip()
    if not prefix:
        return True
    if prefix.endswith(("return", "case", "=>")):
        return True
    return prefix[-1] in "=(:,[!&|?;{"


def _scan_web(path: Path, relative_path: str) -> tuple[list[_Candidate], AuditError | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], _safe_audit_error(relative_path, exc)

    candidates: list[_Candidate] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        for match in _WEB_REGEX_RE.finditer(line):
            if not _looks_like_web_regex(line, match.start()):
                continue
            value = match.group(0)
            candidates.append(
                _Candidate(
                    path=relative_path,
                    line=line_number,
                    language="web",
                    detector="web.regex_literal",
                    control_flow=False,
                    excerpt=_compact_excerpt(line),
                    normalized_source=_SPACE_RE.sub("", value),
                )
            )
        for match in _WEB_ROUTE_RE.finditer(line):
            route = match.group("route")
            candidates.append(
                _Candidate(
                    path=relative_path,
                    line=line_number,
                    language="web",
                    detector="web.route_literal",
                    control_flow="startsWith" in line or "pathname" in line,
                    excerpt=_compact_excerpt(line),
                    normalized_source=route,
                )
            )
    return candidates, None


def _scan_shell(path: Path, relative_path: str) -> tuple[list[_Candidate], AuditError | None]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [], _safe_audit_error(relative_path, exc)

    candidates: list[_Candidate] = []
    in_case = False
    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("case ") and stripped.endswith(" in"):
            in_case = True
            continue
        if in_case and stripped == "esac":
            in_case = False
            continue

        regex_match = _SHELL_REGEX_RE.search(line)
        if regex_match:
            pattern = regex_match.group("pattern").strip()
            candidates.append(
                _Candidate(
                    path=relative_path,
                    line=line_number,
                    language="shell",
                    detector="shell.regex_condition",
                    control_flow=True,
                    excerpt=_compact_excerpt(line),
                    normalized_source=pattern,
                )
            )

        literal_match = _SHELL_LITERAL_TEST_RE.search(line)
        if literal_match:
            value = literal_match.group("value").strip()
            candidates.append(
                _Candidate(
                    path=relative_path,
                    line=line_number,
                    language="shell",
                    detector="shell.literal_condition",
                    control_flow=True,
                    excerpt=_compact_excerpt(line),
                    normalized_source=value,
                )
            )

        if not in_case:
            continue
        pattern_match = _SHELL_CASE_PATTERN_RE.match(line)
        if not pattern_match:
            continue
        pattern = _SPACE_RE.sub("", pattern_match.group("pattern"))
        if not pattern or pattern in {"*", "--"}:
            continue
        candidates.append(
            _Candidate(
                path=relative_path,
                line=line_number,
                language="shell",
                detector="shell.case_pattern",
                control_flow=True,
                excerpt=_compact_excerpt(line),
                normalized_source=pattern,
            )
        )
    return candidates, None


def _safe_audit_error(path: str, exc: BaseException) -> AuditError:
    summary = _compact_excerpt(str(exc), limit=160)
    return AuditError(path=path, error_type=type(exc).__name__, summary=summary)


def _is_excluded(relative_path: Path) -> bool:
    if relative_path.as_posix() == "scripts/audit_decision_rules.py":
        return True
    if any(part in EXCLUDED_PARTS for part in relative_path.parts):
        return True
    name = relative_path.name
    if name.startswith("decision-rule-inventory."):
        return True
    return any(name.endswith(suffix) for suffix in EXCLUDED_FILE_SUFFIXES)


def _iter_source_files(root: Path, scan_roots: Sequence[str]) -> Iterable[Path]:
    seen: set[str] = set()
    for scan_root in scan_roots:
        candidate = root / scan_root
        if not candidate.exists() or candidate.is_symlink():
            continue
        paths = [candidate] if candidate.is_file() else candidate.rglob("*")
        for path in paths:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if _is_excluded(relative):
                continue
            if not _supported_source(path):
                continue
            key = relative.as_posix()
            if key in seen:
                continue
            seen.add(key)
            yield path


def _supported_source(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in PYTHON_SUFFIXES | WEB_SUFFIXES | SHELL_SUFFIXES:
        return True
    if suffix:
        return False
    try:
        with path.open("rb") as file:
            first_line = file.readline(160)
    except OSError:
        return False
    return first_line.startswith(b"#!") and any(
        marker in first_line for marker in (b"bash", b"/sh", b"zsh")
    )


def _infer_owner(path: str) -> str:
    lowered = path.lower()
    if "private_timing" in lowered:
        return "chat.private_timing"
    if "proactive" in lowered or "outreach" in lowered:
        return "chat.proactive_outreach"
    if "group" in lowered and (
        "memory" in lowered or "analysis" in lowered or "ingress" in lowered
    ):
        return "group.memory"
    if "news" in lowered:
        return "news.routing"
    if "expression" in lowered or "jargon" in lowered:
        return "memory.expression"
    if "sandbox" in lowered:
        return "sandbox"
    if "prompt" in lowered:
        return "prompt.runtime"
    if path.startswith("webui/"):
        return "web.routes"
    if path.startswith("api/"):
        return "api"
    if path.startswith("clients/"):
        return "clients"
    if path.startswith("nanobot_kt/"):
        return "kt.adapter"
    if path.startswith("scripts/"):
        return "operations"
    stem = Path(path).with_suffix("").as_posix().replace("/", ".")
    return stem.removeprefix("core.") or "repository"


def _is_model_related(path: str, excerpt: str) -> bool:
    haystack = f"{path} {excerpt}".lower()
    return any(
        marker in haystack
        for marker in (
            "classifier",
            "completion",
            "llm",
            "model",
            "prompt",
            "reasoning",
        )
    )


def _infer_input_boundary(candidate: _Candidate) -> str:
    path = candidate.path
    if path.startswith("api/") or candidate.detector.endswith("route_literal"):
        return "HTTP 路径、查询参数或请求体"
    if path.startswith("webui/"):
        return "浏览器路由、表单状态或 API 响应"
    if path.startswith("scripts/") or candidate.language == "shell":
        return "运维命令行参数、环境变量或宿主状态"
    if path.startswith("clients/"):
        return "外部 Provider 配置、请求或响应"
    if "sandbox" in path.lower():
        return "Sandbox 内部请求、策略配置或宿主运行状态"
    if "prompt" in path.lower():
        return "Prompt Runtime 配置、模板或模型输出"
    if "group" in path.lower() or "memory" in path.lower():
        return "会话消息、记忆候选或持久化状态"
    return "进程内领域输入、配置或持久化状态"


def _infer_plan_stage(candidate: _Candidate, category: str) -> str:
    haystack = f"{candidate.path} {candidate.excerpt}".lower()
    if "private_timing" in haystack or "timing_gate" in haystack:
        return "阶段 5"
    if "news" in haystack:
        return "阶段 6"
    if any(
        marker in haystack
        for marker in (
            "expression",
            "group_analysis",
            "group_memory",
            "jargon",
        )
    ):
        return "阶段 7A–7D"
    if "prompt" in haystack or "tool" in haystack:
        return "阶段 8"
    if category == "security_invariant":
        return "阶段 4"
    if category == "compatibility":
        return "阶段 3／7D"
    if candidate.detector.endswith("route_literal"):
        return "阶段 1／3"
    return "阶段 3／4"


def _discover_current_tests(
    root: Path,
    source_paths: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return {path: () for path in source_paths}

    test_records: list[tuple[str, str, str]] = []
    for test_path in sorted(tests_root.rglob("test_*.py")):
        if not test_path.is_file() or test_path.is_symlink():
            continue
        relative = test_path.relative_to(root).as_posix()
        try:
            content = test_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            content = ""
        test_records.append((relative, test_path.stem.lower(), content))

    result: dict[str, tuple[str, ...]] = {}
    for source_path in sorted(set(source_paths)):
        module_path = Path(source_path).with_suffix("").as_posix()
        dotted_module = module_path.replace("/", ".")
        source_stem = Path(source_path).stem.lower()
        matches = {
            test_path
            for test_path, test_stem, content in test_records
            if (
                source_stem in test_stem
                or dotted_module in content
                or source_path in content
            )
        }
        result[source_path] = tuple(sorted(matches))
    return result


def _classify(candidate: _Candidate) -> tuple[str, str, float, str]:
    haystack = (
        f"{candidate.path} {candidate.detector} {candidate.excerpt}"
    ).lower()
    excerpt = candidate.excerpt.lower()

    if candidate.detector.endswith("route_literal"):
        return (
            "protocol_syntax",
            "resource",
            0.98,
            "路由字面量属于公开协议面，应登记并由路由资源统一管理。",
        )

    if any(
        marker in haystack
        for marker in (
            "apparmor",
            "asset://",
            "auth",
            "cap_drop",
            "credential",
            "docker",
            "email_pattern",
            "injection",
            "ip_literal",
            "local_path",
            "password",
            "permission",
            "quota",
            "sandbox",
            "seccomp",
            "secret",
            "sensitive",
            "sha256",
            "token",
        )
    ):
        return (
            "security_invariant",
            "preserve",
            0.92,
            "命中安全边界词，默认视为需保留的确定性安全规则。",
        )

    if any(
        marker in haystack
        for marker in ("compat", "deprecated", "legacy", "retired")
    ):
        return (
            "compatibility",
            "compatibility_migration",
            0.9,
            "命中兼容性标记，应通过有期限的迁移路径退役。",
        )

    semantic_path = any(
        marker in candidate.path.lower()
        for marker in (
            "classifier",
            "expression",
            "group_analysis",
            "group_memory_learning",
            "jargon",
            "news",
            "persona",
            "proactive",
            "timing",
        )
    )
    semantic_signal = _CJK_RE.search(candidate.excerpt) or any(
        marker in excerpt
        for marker in (
            "casual",
            "emotion",
            "expression",
            "intent",
            "jargon",
            "keyword",
            "marker",
            "natural_language",
            "phrase",
            "relevance",
            "semantic",
            "sentiment",
            "slang",
            "topic",
        )
    )
    if semantic_path and semantic_signal:
        return (
            "natural_language_semantic",
            "model_signal_only",
            0.82,
            "自然语言字面量只能作为候选信号，不能单独作最终语义决策。",
        )

    if _CJK_RE.search(candidate.excerpt):
        return (
            "configurable_policy",
            "policy",
            0.66,
            "包含中文业务枚举或展示值，需人工确认其属于协议、资源还是语义信号。",
        )

    if candidate.detector in {
        "python.numeric_control_flow",
        "python.literal_collection",
        "python.literal_mapping",
        "shell.literal_condition",
    }:
        return (
            "configurable_policy",
            "configure",
            0.74,
            "阈值或枚举可能是可配置策略，需要领域负责人复核归属。",
        )

    if candidate.detector == "python.string_control_flow" and any(
        marker in excerpt
        for marker in (
            ".status",
            "[\"status\"]",
            "['status']",
            ".type",
            "[\"type\"]",
            "['type']",
            ".mode",
            "[\"mode\"]",
            "['mode']",
            ".action",
            "[\"action\"]",
            "['action']",
        )
    ):
        return (
            "data_consistency",
            "policy",
            0.76,
            "状态机、类型或动作枚举参与控制流，应由领域合同统一。",
        )

    if candidate.detector in {
        "python.regex_call",
        "python.match_literal",
        "python.string_control_flow",
        "shell.case_pattern",
        "shell.regex_condition",
        "web.regex_literal",
    }:
        return (
            "protocol_syntax",
            "policy",
            0.68,
            "模式可能表达协议语法或业务决策，需要人工确认后固化处置。",
        )

    return (
        "data_consistency",
        "policy",
        0.55,
        "无法可靠自动归类，保守登记为待人工复核的确定性策略。",
    )


def _rule_id_material(candidate: _Candidate, occurrence: int) -> tuple[str, str]:
    fingerprint_material = "\x1f".join(
        (
            candidate.language,
            candidate.detector,
            candidate.normalized_source,
        )
    )
    normalized_fingerprint = hashlib.sha256(
        fingerprint_material.encode("utf-8")
    ).hexdigest()
    identity_material = "\x1f".join(
        (
            candidate.path,
            candidate.detector,
            normalized_fingerprint,
            str(occurrence),
        )
    )
    digest = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:20]
    return f"decision.{digest}", normalized_fingerprint


def _build_rules(
    candidates: Sequence[_Candidate],
    current_tests: Mapping[str, tuple[str, ...]],
) -> list[DecisionRule]:
    occurrences: Counter[tuple[str, str, str]] = Counter()
    rules: list[DecisionRule] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.path,
            item.line,
            item.detector,
            item.normalized_source,
        ),
    ):
        occurrence_key = (
            candidate.path,
            candidate.detector,
            candidate.normalized_source,
        )
        occurrence = occurrences[occurrence_key]
        occurrences[occurrence_key] += 1
        rule_id, normalized_fingerprint = _rule_id_material(
            candidate,
            occurrence,
        )
        category, disposition, confidence, reason = _classify(candidate)
        rules.append(
            DecisionRule(
                rule_id=rule_id,
                path=candidate.path,
                line=candidate.line,
                language=candidate.language,
                detector=candidate.detector,
                category=category,
                owner=_infer_owner(candidate.path),
                control_flow=candidate.control_flow,
                model_call=_is_model_related(
                    candidate.path,
                    candidate.excerpt,
                ),
                disposition=disposition,
                confidence=confidence,
                review_status="auto_classified",
                excerpt=candidate.excerpt,
                normalized_fingerprint=normalized_fingerprint,
                reason=reason,
                input_boundary=_infer_input_boundary(candidate),
                current_tests=current_tests.get(candidate.path, ()),
                plan_stage=_infer_plan_stage(candidate, category),
            )
        )
    return rules


def _validate_override(
    rule_id: str,
    override: Mapping[str, str],
) -> None:
    unknown_fields = set(override) - OVERRIDABLE_FIELDS
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise AuditConfigurationError(
            f"{rule_id} 包含不可覆盖字段：{fields}"
        )
    missing_fields = OVERRIDABLE_FIELDS - set(override)
    if missing_fields:
        fields = ", ".join(sorted(missing_fields))
        raise AuditConfigurationError(
            f"{rule_id} 缺少人工分类字段：{fields}"
        )
    if override["category"] not in SUPPORTED_CATEGORIES:
        raise AuditConfigurationError(
            f"{rule_id} 的 category 无效：{override['category']}"
        )
    if override["disposition"] not in SUPPORTED_DISPOSITIONS:
        raise AuditConfigurationError(
            f"{rule_id} 的 disposition 无效：{override['disposition']}"
        )
    if override["review_status"] not in SUPPORTED_REVIEW_STATUSES:
        raise AuditConfigurationError(
            f"{rule_id} 的 review_status 无效：{override['review_status']}"
        )
    if not str(override["reason"]).strip():
        raise AuditConfigurationError(f"{rule_id} 的 reason 不能为空")


def _apply_overrides(
    rules: Sequence[DecisionRule],
    overrides: Mapping[str, Mapping[str, str]],
) -> list[DecisionRule]:
    by_id = {rule.rule_id: rule for rule in rules}
    unknown_rule_ids = sorted(set(overrides) - set(by_id))
    if unknown_rule_ids:
        raise AuditConfigurationError(
            "人工分类引用了不存在的规则："
            + ", ".join(unknown_rule_ids)
        )
    for rule_id, override in overrides.items():
        _validate_override(rule_id, override)
        by_id[rule_id] = replace(
            by_id[rule_id],
            category=override["category"],
            disposition=override["disposition"],
            review_status=override["review_status"],
            reason=str(override["reason"]).strip(),
        )
    return sorted(
        by_id.values(),
        key=lambda rule: (rule.path, rule.line, rule.detector, rule.rule_id),
    )


def _rule_id_set_sha256(rules: Iterable[DecisionRule]) -> str:
    rule_ids = sorted(rule.rule_id for rule in rules)
    return hashlib.sha256("\n".join(rule_ids).encode("utf-8")).hexdigest()


def _matches_group(
    rule: DecisionRule,
    matcher: Mapping[str, Any],
) -> bool:
    for field, expected in matcher.items():
        if field == "path_glob":
            if not isinstance(expected, str) or not fnmatch.fnmatchcase(
                rule.path,
                expected,
            ):
                return False
            continue
        if field == "detectors":
            if (
                not isinstance(expected, list)
                or rule.detector not in expected
            ):
                return False
            continue
        if getattr(rule, field) != expected:
            return False
    return True


def _validate_group_override(
    group: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], Mapping[str, str], int, str]:
    required = {
        "group_id",
        "match",
        "expected_count",
        "expected_rule_ids_sha256",
        "override",
    }
    missing = required - set(group)
    unknown = set(group) - required
    if missing:
        raise AuditConfigurationError(
            "批量复核缺少字段：" + ", ".join(sorted(missing))
        )
    if unknown:
        raise AuditConfigurationError(
            "批量复核包含未知字段：" + ", ".join(sorted(unknown))
        )
    group_id = group["group_id"]
    matcher = group["match"]
    override = group["override"]
    expected_count = group["expected_count"]
    expected_sha256 = group["expected_rule_ids_sha256"]
    if not isinstance(group_id, str) or not group_id.strip():
        raise AuditConfigurationError("批量复核 group_id 不能为空")
    if not isinstance(matcher, dict) or not matcher:
        raise AuditConfigurationError(f"{group_id} 的 match 必须是非空对象")
    unknown_match_fields = set(matcher) - GROUP_MATCH_FIELDS
    if unknown_match_fields:
        raise AuditConfigurationError(
            f"{group_id} 包含未知 match 字段："
            + ", ".join(sorted(unknown_match_fields))
        )
    if "path" in matcher and "path_glob" in matcher:
        raise AuditConfigurationError(
            f"{group_id} 不能同时声明 path 和 path_glob"
        )
    if not isinstance(override, dict):
        raise AuditConfigurationError(f"{group_id} 的 override 必须是对象")
    normalized_override = {
        str(key): str(value) for key, value in override.items()
    }
    _validate_override(group_id, normalized_override)
    if not isinstance(expected_count, int) or expected_count <= 0:
        raise AuditConfigurationError(
            f"{group_id} 的 expected_count 必须是正整数"
        )
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise AuditConfigurationError(
            f"{group_id} 的 expected_rule_ids_sha256 无效"
        )
    return (
        group_id,
        matcher,
        normalized_override,
        expected_count,
        expected_sha256,
    )


def _apply_group_overrides(
    rules: Sequence[DecisionRule],
    group_overrides: Sequence[Mapping[str, Any]],
) -> list[DecisionRule]:
    original = {rule.rule_id: rule for rule in rules}
    reviewed = dict(original)
    claimed_by: dict[str, str] = {}
    seen_group_ids: set[str] = set()

    for group in group_overrides:
        (
            group_id,
            matcher,
            override,
            expected_count,
            expected_sha256,
        ) = _validate_group_override(group)
        if group_id in seen_group_ids:
            raise AuditConfigurationError(
                f"批量复核 group_id 重复：{group_id}"
            )
        seen_group_ids.add(group_id)
        matched = [
            rule for rule in original.values() if _matches_group(rule, matcher)
        ]
        if len(matched) != expected_count:
            raise AuditConfigurationError(
                f"{group_id} 命中 {len(matched)} 条，"
                f"预期 {expected_count} 条"
            )
        actual_sha256 = _rule_id_set_sha256(matched)
        if actual_sha256 != expected_sha256:
            raise AuditConfigurationError(
                f"{group_id} 的规则集合哈希不一致："
                f"实际 {actual_sha256}"
            )
        overlaps = sorted(
            rule.rule_id for rule in matched if rule.rule_id in claimed_by
        )
        if overlaps:
            first = overlaps[0]
            raise AuditConfigurationError(
                f"{group_id} 与 {claimed_by[first]} 重复覆盖 {first}"
            )
        for rule in matched:
            claimed_by[rule.rule_id] = group_id
            reviewed[rule.rule_id] = replace(
                rule,
                category=override["category"],
                disposition=override["disposition"],
                review_status=override["review_status"],
                reason=override["reason"].strip(),
            )

    return sorted(
        reviewed.values(),
        key=lambda rule: (rule.path, rule.line, rule.detector, rule.rule_id),
    )


def audit_repository(
    root: Path,
    *,
    scan_roots: tuple[str, ...] | None = None,
    source_revision: str = "",
    overrides: Mapping[str, Mapping[str, str]] | None = None,
    group_overrides: Sequence[Mapping[str, Any]] = (),
) -> AuditReport:
    """扫描仓库并返回确定性报告。

    ``overrides`` 必须引用本次扫描真实存在的 ``rule_id``；悬空覆盖会失败，
    防止代码变化后继续沿用已经失效的人工结论。
    """

    repository_root = root.resolve()
    selected_roots = tuple(scan_roots or DEFAULT_SCAN_ROOTS)
    candidates: list[_Candidate] = []
    errors: list[AuditError] = []

    for path in _iter_source_files(repository_root, selected_roots):
        relative_path = path.relative_to(repository_root).as_posix()
        suffix = path.suffix.lower()
        if suffix in PYTHON_SUFFIXES:
            discovered, error = _scan_python(path, relative_path)
        elif suffix in WEB_SUFFIXES:
            discovered, error = _scan_web(path, relative_path)
        else:
            discovered, error = _scan_shell(path, relative_path)
        candidates.extend(discovered)
        if error is not None:
            errors.append(error)

    current_tests = _discover_current_tests(
        repository_root,
        (candidate.path for candidate in candidates),
    )
    rules = _build_rules(candidates, current_tests)
    if group_overrides:
        rules = _apply_group_overrides(rules, group_overrides)
    if overrides:
        rules = _apply_overrides(rules, overrides)
    return AuditReport(
        source_revision=source_revision,
        scan_roots=selected_roots,
        rules=tuple(rules),
        errors=tuple(
            sorted(errors, key=lambda item: (item.path, item.error_type))
        ),
    )


def render_json(report: AuditReport) -> str:
    """生成稳定 JSON。"""

    return (
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def render_markdown(report: AuditReport) -> str:
    """生成人工审阅用 Markdown 摘要。

    完整逐项数据保存在 JSON；Markdown 聚合全部文件，并展开需要人工确认的
    语义、兼容和低置信度业务枚举，避免生成无法审阅的数万行重复表格。
    """

    summary = report.summary()
    by_path = Counter(rule.path for rule in report.rules)
    review_queue = [
        rule
        for rule in report.rules
        if (
            rule.category in {"compatibility", "natural_language_semantic"}
            or rule.review_status != "auto_classified"
            or (
                rule.category == "configurable_policy"
                and rule.confidence < 0.7
            )
        )
    ]
    lines = [
        "# 决策规则审计清单",
        "",
        f"- Schema 版本：{report.schema_version}",
        f"- 源提交：`{report.source_revision or '未指定'}`",
        f"- 规则总数：{summary['rules_total']}",
        f"- 扫描错误：{summary['errors_total']}",
        f"- 人工复核队列：{len(review_queue)}",
        "- 完整逐项记录：`decision-rule-inventory.json`",
        "",
        "## 分类汇总",
        "",
        "| 分类 | 数量 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{category}` | {count} |"
        for category, count in summary["by_category"].items()
    )
    lines.extend(
        [
            "",
            "## 文件汇总",
            "",
            "| 文件 | 命中数 |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| `{path}` | {count} |"
        for path, count in sorted(
            by_path.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )
    lines.extend(
        [
            "",
            "## 人工复核队列",
            "",
            "| Rule ID | 位置 | 检测器 | 分类 | 处置 | 阶段 | 复核 |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for rule in review_queue:
        location = f"`{rule.path}:{rule.line}`"
        lines.append(
            "| `{rule_id}` | {location} | `{detector}` | `{category}` | "
            "`{disposition}` | {plan_stage} | `{review_status}` |".format(
                rule_id=rule.rule_id,
                location=location,
                detector=rule.detector,
                category=rule.category,
                disposition=rule.disposition,
                plan_stage=rule.plan_stage,
                review_status=rule.review_status,
            )
        )
        lines.append(
            f"|  | 摘要：{_markdown_cell(rule.excerpt)} |  |  |  |  |  |"
        )
        lines.append(
            f"|  | 原因：{_markdown_cell(rule.reason)} |  |  |  |  |  |"
        )
    if report.errors:
        lines.extend(
            [
                "",
                "## 扫描错误",
                "",
                "| 文件 | 类型 | 摘要 |",
                "|---|---|---|",
            ]
        )
        for error in report.errors:
            lines.append(
                f"| `{error.path}` | `{error.error_type}` | "
                f"{_markdown_cell(error.summary)} |"
            )
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def _load_override_configuration(
    path: Path | None,
) -> tuple[Mapping[str, Mapping[str, str]], tuple[Mapping[str, Any], ...]]:
    if path is None:
        return {}, ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditConfigurationError(
            f"无法读取人工分类覆盖 {path}：{exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise AuditConfigurationError("人工分类覆盖必须是 JSON 对象")
    if "overrides" in payload or "groups" in payload:
        unknown_top_level = set(payload) - {
            "schema_version",
            "overrides",
            "groups",
        }
        if unknown_top_level:
            raise AuditConfigurationError(
                "人工分类覆盖包含未知顶层字段："
                + ", ".join(sorted(unknown_top_level))
            )
        if payload.get("schema_version", 1) != 1:
            raise AuditConfigurationError("人工分类覆盖 schema_version 无效")
        raw_overrides = payload.get("overrides", {})
        raw_groups = payload.get("groups", [])
    else:
        raw_overrides = payload
        raw_groups = []
    if not isinstance(raw_overrides, dict):
        raise AuditConfigurationError("overrides 必须是 JSON 对象")
    if not isinstance(raw_groups, list):
        raise AuditConfigurationError("groups 必须是 JSON 数组")
    normalized: dict[str, Mapping[str, str]] = {}
    for rule_id, override in raw_overrides.items():
        if not isinstance(rule_id, str) or not isinstance(override, dict):
            raise AuditConfigurationError(
                "人工分类覆盖必须是 rule_id 到分类对象的映射"
            )
        normalized[rule_id] = {
            str(key): str(value) for key, value in override.items()
        }
    normalized_groups: list[Mapping[str, Any]] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            raise AuditConfigurationError("groups 中的每一项必须是对象")
        normalized_groups.append(group)
    return normalized, tuple(normalized_groups)


def _source_revision(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _check_content(path: Path, expected: str) -> bool:
    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return current == expected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="盘点仓库中的硬编码决策、正则和路由字面量",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="仓库根目录，默认当前目录",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        dest="scan_roots",
        help="扫描相对路径，可重复指定",
    )
    parser.add_argument(
        "--source-revision",
        default=None,
        help="写入报告的源提交，默认读取当前 Git HEAD",
    )
    override_mode = parser.add_mutually_exclusive_group()
    override_mode.add_argument(
        "--overrides",
        type=Path,
        help=(
            "人工分类覆盖 JSON；未指定时自动使用 "
            "config/decision-rule-overrides.v1.json（若存在）"
        ),
    )
    override_mode.add_argument(
        "--no-overrides",
        action="store_true",
        help="仅生成自动分类候选，供人工复核覆盖文件漂移",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("docs/architecture/decision-rule-inventory.json"),
        help="JSON 输出路径",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("docs/architecture/decision-rule-inventory.md"),
        help="Markdown 输出路径",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="原子写入 JSON 和 Markdown 清单",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="检查已生成清单是否与代码一致",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    json_output = (
        arguments.json_output
        if arguments.json_output.is_absolute()
        else root / arguments.json_output
    )
    markdown_output = (
        arguments.markdown_output
        if arguments.markdown_output.is_absolute()
        else root / arguments.markdown_output
    )
    override_path = arguments.overrides
    if arguments.no_overrides:
        override_path = None
    elif override_path is None:
        default_override_path = (
            root / "config/decision-rule-overrides.v1.json"
        )
        override_path = (
            default_override_path
            if default_override_path.is_file()
            else None
        )
    elif not override_path.is_absolute():
        override_path = root / override_path
    try:
        overrides, group_overrides = _load_override_configuration(
            override_path
        )
        report = audit_repository(
            root,
            scan_roots=(
                tuple(arguments.scan_roots)
                if arguments.scan_roots
                else None
            ),
            source_revision=(
                arguments.source_revision
                if arguments.source_revision is not None
                else _source_revision(root)
            ),
            overrides=overrides,
            group_overrides=group_overrides,
        )
    except AuditConfigurationError as exc:
        parser.error(str(exc))

    json_content = render_json(report)
    markdown_content = render_markdown(report)
    if arguments.write:
        _write_atomic(json_output, json_content)
        _write_atomic(markdown_output, markdown_content)
    elif arguments.check:
        stale = [
            str(path)
            for path, content in (
                (json_output, json_content),
                (markdown_output, markdown_content),
            )
            if not _check_content(path, content)
        ]
        if stale:
            print(
                "决策规则清单缺失或已漂移：\n- " + "\n- ".join(stale),
                file=sys.stderr,
            )
            return 1
    else:
        sys.stdout.write(markdown_content)

    if report.errors:
        print(
            f"扫描完成，但有 {len(report.errors)} 个文件无法分析。",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
