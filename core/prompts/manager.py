import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger_name = "nanobot.prompt_manager"


class PromptParseError(ValueError):
    """提示词模板 frontmatter 解析错误。"""


class PromptRenderError(ValueError):
    """提示词渲染错误。"""


@dataclass
class PromptTemplate:
    prompt_key: str
    path: Path
    frontmatter: dict[str, Any]
    body: str
    raw: str
    mtime: float

    @property
    def required_vars(self) -> list[str]:
        return _string_list(self.frontmatter.get("required_vars"))

    @property
    def optional_vars(self) -> list[str]:
        return _string_list(self.frontmatter.get("optional_vars"))


@dataclass
class RenderedPrompt:
    prompt_key: str
    content: str
    messages: list[dict[str, str]]
    frontmatter: dict[str, Any]
    required_vars: list[str]
    optional_vars: list[str]
    missing_required_vars: list[str]
    unknown_vars: list[str]
    warnings: list[str]
    token_estimate: int
    mode: str
    trace_id: str | None = None
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_key": self.prompt_key,
            "content": self.content,
            "messages": self.messages,
            "frontmatter": self.frontmatter,
            "required_vars": self.required_vars,
            "optional_vars": self.optional_vars,
            "missing_required_vars": self.missing_required_vars,
            "unknown_vars": self.unknown_vars,
            "warnings": self.warnings,
            "token_estimate": self.token_estimate,
            "mode": self.mode,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
        }


_VAR_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_prompt_dir() -> Path:
    return Path(os.environ.get("NANOBOT_PROMPT_DIR") or (_repo_root() / "prompts"))


def _default_backup_dir() -> Path:
    return Path(
        os.environ.get("NANOBOT_PROMPT_BACKUP_DIR")
        or (_repo_root() / "data" / "prompt_template_backups")
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    current_key: str | None = None
    for lineno, raw_line in enumerate(frontmatter.splitlines(), start=1):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            if not current_key:
                raise PromptParseError(f"frontmatter 第 {lineno} 行缺少列表字段名")
            meta.setdefault(current_key, []).append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" not in raw_line:
            raise PromptParseError(f"frontmatter 第 {lineno} 行不是 key: value 格式")
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if not key:
            raise PromptParseError(f"frontmatter 第 {lineno} 行 key 为空")
        value = value.strip()
        if value == "":
            meta[key] = []
            current_key = key
        else:
            meta[key] = _coerce_scalar(value)
            current_key = None
    return meta


def _split_template(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    close_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            close_idx = idx
            break
    if close_idx is None:
        raise PromptParseError("frontmatter 缺少结束分隔符 ---")
    meta = _parse_frontmatter("\n".join(lines[1:close_idx]))
    body = "\n".join(lines[close_idx + 1 :])
    if raw.endswith("\n"):
        body += "\n"
    return meta, body


def _format_var(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = max(0, len(text) - cjk - ascii_chars)
    return int(cjk * 1.0 + ascii_chars * 0.35 + other * 0.8)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _safe_key(prompt_key: str) -> str:
    prompt_key = prompt_key.removesuffix(".md").strip()
    if not _KEY_PATTERN.fullmatch(prompt_key):
        raise PromptRenderError("prompt_key 只能包含字母、数字、下划线、点和短横线")
    return prompt_key


class PromptManager:
    """Markdown/frontmatter 提示词模板管理器。

    只负责模板文件、变量渲染、预览和备份，不做检索、记忆选择或上下文裁剪。
    """

    def __init__(
        self,
        prompt_dir: str | Path | None = None,
        backup_dir: str | Path | None = None,
        *,
        hot_reload: bool = True,
    ):
        self.prompt_dir = Path(prompt_dir) if prompt_dir else _default_prompt_dir()
        self.backup_dir = Path(backup_dir) if backup_dir else _default_backup_dir()
        self.hot_reload = hot_reload
        self._cache: dict[str, PromptTemplate] = {}

    def reload(self) -> dict[str, Any]:
        self._cache.clear()
        return {"ok": True, "prompt_dir": str(self.prompt_dir)}

    def _path_for(self, prompt_key: str) -> Path:
        key = _safe_key(prompt_key)
        return self.prompt_dir / f"{key}.md"

    def _load_template(self, prompt_key: str) -> PromptTemplate:
        key = _safe_key(prompt_key)
        path = self._path_for(key)
        if not path.exists():
            raise PromptRenderError(f"提示词模板不存在: {key}")
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = _split_template(raw)
        stat = path.stat()
        return PromptTemplate(
            prompt_key=key,
            path=path,
            frontmatter=frontmatter,
            body=body,
            raw=raw,
            mtime=stat.st_mtime,
        )

    def get_template(self, prompt_key: str) -> PromptTemplate:
        key = _safe_key(prompt_key)
        cached = self._cache.get(key)
        if cached and not self.hot_reload:
            return cached
        if cached and cached.path.exists() and cached.path.stat().st_mtime == cached.mtime:
            return cached
        tmpl = self._load_template(key)
        self._cache[key] = tmpl
        return tmpl

    def list_prompts(self) -> list[dict[str, Any]]:
        if not self.prompt_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.prompt_dir.glob("*.md")):
            key = path.stem
            try:
                tmpl = self.get_template(key)
                fm = tmpl.frontmatter
                items.append({
                    "prompt_key": key,
                    "name": str(fm.get("name") or key),
                    "description": str(fm.get("description") or ""),
                    "version": str(fm.get("version") or ""),
                    "required_vars": tmpl.required_vars,
                    "optional_vars": tmpl.optional_vars,
                    "size": path.stat().st_size,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "parse_error": "",
                })
            except Exception as e:
                items.append({
                    "prompt_key": key,
                    "name": key,
                    "description": "",
                    "version": "",
                    "required_vars": [],
                    "optional_vars": [],
                    "size": path.stat().st_size,
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "parse_error": str(e),
                })
        return items

    def get_prompt(self, prompt_key: str) -> dict[str, Any]:
        tmpl = self.get_template(prompt_key)
        return {
            "prompt_key": tmpl.prompt_key,
            "content": tmpl.raw,
            "body": tmpl.body,
            "frontmatter": tmpl.frontmatter,
            "required_vars": tmpl.required_vars,
            "optional_vars": tmpl.optional_vars,
            "updated_at": datetime.fromtimestamp(tmpl.mtime).isoformat(),
            "size": tmpl.path.stat().st_size,
            "token_estimate": _estimate_tokens(tmpl.body),
        }

    def render(
        self,
        prompt_key: str,
        variables: dict[str, Any] | None = None,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        mode: str | None = None,
        strict: bool = False,
    ) -> RenderedPrompt:
        """渲染提示词模板。strict=False 时缺必需变量不抛错，改为 warnings。"""
        variables = dict(variables or {})
        tmpl = self.get_template(prompt_key)
        required = tmpl.required_vars
        optional = tmpl.optional_vars
        declared = set(required + optional)
        missing = [name for name in required if name not in variables or variables[name] is None]
        placeholders = set(_VAR_PATTERN.findall(tmpl.body))
        unknown_vars = sorted(set(variables) - declared)
        warnings: list[str] = []
        if missing:
            if strict:
                raise PromptRenderError(f"缺少必需变量: {', '.join(missing)}")
            warnings.append(f"missing required variable: {', '.join(missing)}")
        for name in unknown_vars:
            warnings.append(f"unknown variable provided: {name}")
        for name in sorted(placeholders - declared):
            warnings.append(f"placeholder not declared: {name}")
        for name in sorted(placeholders - set(variables)):
            if name not in required:
                warnings.append(f"missing optional variable: {name}")

        def repl(match: re.Match[str]) -> str:
            return _format_var(variables.get(match.group(1), ""))

        content = _VAR_PATTERN.sub(repl, tmpl.body)
        rendered = RenderedPrompt(
            prompt_key=tmpl.prompt_key,
            content=content,
            messages=[{"role": "system", "content": content}],
            frontmatter=tmpl.frontmatter,
            required_vars=required,
            optional_vars=optional,
            missing_required_vars=missing,
            unknown_vars=unknown_vars,
            warnings=warnings,
            token_estimate=_estimate_tokens(content),
            mode=mode or "preview",
            trace_id=trace_id,
            run_id=run_id,
        )
        if trace_id or run_id:
            try:
                from core.tracing import PromptTracer

                PromptTracer.record_render(
                    trace_id=trace_id or "",
                    run_id=run_id or "",
                    prompt_key=tmpl.prompt_key,
                    mode=mode or "preview",
                    variables=variables,
                    rendered_content=content,
                    token_estimate=rendered.token_estimate,
                    warnings=warnings,
                )
            except Exception:
                pass
        return rendered

    def save_prompt(self, prompt_key: str, content: str, *, operator: str = "") -> dict[str, Any]:
        key = _safe_key(prompt_key)
        if not content.strip():
            raise PromptRenderError("拒绝保存空提示词模板")
        _split_template(content)
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(key)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        backup_name = ""
        if old:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_name = f"{key}.{ts}.{_hash_text(old)}.bak"
            (self.backup_dir / backup_name).write_text(old, encoding="utf-8")
        path.write_text(content, encoding="utf-8")
        self._cache.pop(key, None)
        return {
            "saved": True,
            "prompt_key": key,
            "backup_name": backup_name,
            "before_hash": _hash_text(old) if old else "",
            "after_hash": _hash_text(content),
            "operator": operator,
        }

    def history(self, prompt_key: str) -> list[dict[str, Any]]:
        key = _safe_key(prompt_key)
        if not self.backup_dir.exists():
            return []
        pattern = re.compile(
            rf"^{re.escape(key)}\.(?P<ts>\d{{8}}_\d{{6}}_\d{{6}})\.(?P<hash>[a-f0-9]+)\.bak$"
        )
        items: list[dict[str, Any]] = []
        for path in sorted(self.backup_dir.glob(f"{key}.*.bak"), reverse=True):
            match = pattern.match(path.name)
            if not match:
                continue
            stat = path.stat()
            items.append({
                "name": path.name,
                "prompt_key": key,
                "hash": match.group("hash"),
                "created_at": match.group("ts"),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return items

    def rollback(self, prompt_key: str, backup_name: str, *, operator: str = "") -> dict[str, Any]:
        key = _safe_key(prompt_key)
        backup_path = (self.backup_dir / os.path.basename(backup_name)).resolve()
        backup_root = self.backup_dir.resolve()
        if not str(backup_path).startswith(str(backup_root) + os.sep) or not backup_path.exists():
            raise PromptRenderError("备份不存在")
        if backup_path.name not in {item["name"] for item in self.history(key)}:
            raise PromptRenderError("备份不属于该 prompt_key")
        target = self._path_for(key)
        if target.exists():
            current = target.read_text(encoding="utf-8")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            guard_name = f"{key}.{ts}.{_hash_text(current)}.bak"
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            (self.backup_dir / guard_name).write_text(current, encoding="utf-8")
        shutil.copy2(backup_path, target)
        self._cache.pop(key, None)
        return {"ok": True, "prompt_key": key, "backup": backup_path.name, "operator": operator}


_MANAGER: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _MANAGER
    expected_dir = _default_prompt_dir()
    expected_backup = _default_backup_dir()
    if (
        _MANAGER is None
        or _MANAGER.prompt_dir != expected_dir
        or _MANAGER.backup_dir != expected_backup
    ):
        _MANAGER = PromptManager(prompt_dir=expected_dir, backup_dir=expected_backup)
    return _MANAGER
