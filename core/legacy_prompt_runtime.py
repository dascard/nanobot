"""Deprecated: 旧版 Prompt 片段的运行时分离管理。

仅作为 PromptAssembler legacy rollback mode 保留；新主回复编排请使用
`core.prompt_assembler.PromptAssembler` 和 `prompts.default/*.md`。

Git 管理默认片段：prompts.legacy.default/fragments/
WebUI 写入运行时片段：data/prompt_fragments/
构建产物：data/runtime_prompt/prompt.md
备份：data/prompt_fragments_history/
"""

import hashlib
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LEGACY_FRAGMENT_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\.md")


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_fragments_dir() -> str:
    return os.environ.get(
        "NANOBOT_LEGACY_PROMPT_DEFAULT_DIR",
        os.path.join(_project_root(), "prompts.legacy.default", "fragments"),
    )


def runtime_fragments_dir() -> str:
    return os.environ.get(
        "NANOBOT_LEGACY_PROMPT_RUNTIME_DIR",
        os.path.join(_project_root(), "data", "prompt_fragments"),
    )


def backup_dir() -> str:
    return os.environ.get(
        "NANOBOT_LEGACY_PROMPT_BACKUP_DIR",
        os.path.join(_project_root(), "data", "prompt_fragments_history"),
    )


def runtime_prompt_output() -> str:
    return os.environ.get(
        "NANOBOT_LEGACY_PROMPT_OUTPUT",
        os.path.join(_project_root(), "data", "runtime_prompt", "prompt.md"),
    )


def legacy_default_prompt_path() -> str:
    return os.path.join(_project_root(), "creatures", "nanobot", "prompt.md")


def init_legacy_prompt_runtime_dir() -> dict[str, Any]:
    """从默认片段目录初始化缺失的运行时片段。只复制缺失，不覆盖已有。"""
    src_dir = default_fragments_dir()
    dst_dir = runtime_fragments_dir()
    os.makedirs(dst_dir, exist_ok=True)
    copied: list[str] = []
    if os.path.isdir(src_dir):
        for fname in sorted(os.listdir(src_dir)):
            if not fname.endswith(".md"):
                continue
            src = os.path.join(src_dir, fname)
            dst = os.path.join(dst_dir, fname)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                copied.append(fname)
    return {"runtime_dir": dst_dir, "source_dir": src_dir, "copied": copied}


def _safe_name(name: str) -> str:
    return os.path.basename(name)


def _file_hash(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except FileNotFoundError:
        return ""


def list_fragments_with_status() -> list[dict[str, Any]]:
    """列出运行时 fragments，附带与默认版本的对比状态。"""
    import os as _os
    runtime_dir = runtime_fragments_dir()
    default_dir = default_fragments_dir()
    os.makedirs(runtime_dir, exist_ok=True)

    names: set[str] = set()
    if os.path.isdir(runtime_dir):
        for f in os.listdir(runtime_dir):
            if f.endswith(".md"):
                names.add(f)
    if os.path.isdir(default_dir):
        for f in os.listdir(default_dir):
            if f.endswith(".md"):
                names.add(f)

    items = []
    for name in sorted(names):
        rp = os.path.join(runtime_dir, name)
        dp = os.path.join(default_dir, name)
        has_runtime = os.path.isfile(rp)
        has_default = os.path.isfile(dp)

        content = ""
        if has_runtime:
            with open(rp, "r", encoding="utf-8") as fh:
                content = fh.read()
        elif has_default:
            with open(dp, "r", encoding="utf-8") as fh:
                content = fh.read()

        runtime_hash = _file_hash(rp) if has_runtime else ""
        default_hash = _file_hash(dp) if has_default else ""
        is_modified = has_runtime and has_default and runtime_hash != default_hash

        rt_stat = _os.stat(rp) if has_runtime else None
        df_stat = _os.stat(dp) if has_default else None

        items.append({
            "name": name,
            "content": content,
            "runtime_path": rp if has_runtime else "",
            "default_path": dp if has_default else "",
            "has_default": has_default,
            "has_runtime": has_runtime,
            "is_modified": is_modified,
            "runtime_hash": runtime_hash,
            "default_hash": default_hash,
            "updated_at": datetime.fromtimestamp(rt_stat.st_mtime).isoformat() if rt_stat else "",
            "default_updated_at": datetime.fromtimestamp(df_stat.st_mtime).isoformat() if df_stat else "",
        })
    return items


def save_fragment(name: str, content: str) -> dict[str, Any]:
    """保存 fragment 到运行时目录。保存前备份旧 runtime 到备份目录。"""
    safe = _safe_name(name)
    if not _LEGACY_FRAGMENT_NAME_RE.fullmatch(safe):
        raise ValueError(f"Invalid fragment name: {name}")

    runtime_dir = runtime_fragments_dir()
    os.makedirs(runtime_dir, exist_ok=True)
    rp = os.path.join(runtime_dir, safe)

    before_hash = _file_hash(rp) if os.path.isfile(rp) else ""

    if before_hash:
        bkp_dir = backup_dir()
        os.makedirs(bkp_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        bkp_name = f"{safe}.{ts}.{before_hash}.bak"
        bkp_path = os.path.join(bkp_dir, bkp_name)
        shutil.copy2(rp, bkp_path)
    else:
        bkp_name = ""

    with open(rp, "w", encoding="utf-8") as fh:
        fh.write(content)

    after_hash = _file_hash(rp)
    return {
        "name": safe,
        "saved": True,
        "runtime_path": rp,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "backup_name": bkp_name,
    }


def get_default_fragment(name: str) -> dict[str, Any] | None:
    """读取默认 fragment。"""
    safe = _safe_name(name)
    dp = os.path.join(default_fragments_dir(), safe)
    if not os.path.isfile(dp):
        return None
    with open(dp, "r", encoding="utf-8") as fh:
        content = fh.read()
    return {
        "name": safe,
        "content": content,
        "path": dp,
        "hash": _file_hash(dp),
    }


def reset_to_default(name: str) -> dict[str, Any]:
    """用默认 fragment 覆盖运行时 fragment。先备份运行时。"""
    safe = _safe_name(name)
    dp = os.path.join(default_fragments_dir(), safe)
    if not os.path.isfile(dp):
        raise FileNotFoundError(f"Default fragment not found: {safe}")

    with open(dp, "r", encoding="utf-8") as fh:
        default_content = fh.read()

    rp = os.path.join(runtime_fragments_dir(), safe)
    if os.path.isfile(rp):
        old_hash = _file_hash(rp)
        bkp_dir = backup_dir()
        os.makedirs(bkp_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        bkp_path = os.path.join(bkp_dir, f"{safe}.{ts}.{old_hash}.bak")
        shutil.copy2(rp, bkp_path)

    os.makedirs(runtime_fragments_dir(), exist_ok=True)
    with open(rp, "w", encoding="utf-8") as fh:
        fh.write(default_content)

    return {
        "name": safe,
        "reset": True,
        "runtime_path": rp,
        "default_hash": _file_hash(dp),
        "after_hash": _file_hash(rp),
    }


def build_prompt_from_runtime(chat_type: str = "base") -> dict[str, Any]:
    """从运行时 fragments 构建 prompt.md 输出到 runtime_prompt_output()。"""
    fragments_dir = runtime_fragments_dir()
    if chat_type == "base":
        allowed = ("00_", "05_", "10_", "30_")
    elif chat_type == "group":
        allowed = ("00_", "05_", "10_", "20_", "25_", "30_")
    elif chat_type == "private":
        allowed = ("00_", "05_", "10_", "26_", "30_")
    else:
        raise ValueError(f"Unknown chat_type: {chat_type}")

    fragments: list[tuple[str, str]] = []
    if os.path.isdir(fragments_dir):
        for fname in sorted(os.listdir(fragments_dir)):
            if not fname.endswith(".md"):
                continue
            if not any(fname.startswith(p) for p in allowed):
                continue
            with open(os.path.join(fragments_dir, fname), "r", encoding="utf-8") as fh:
                fragments.append((fname, fh.read().strip()))

    if not fragments:
        return {"ok": False, "error": f"No fragments found in {fragments_dir} for chat_type={chat_type}"}

    parts = []
    for i, (_, content) in enumerate(fragments):
        parts.append(content)
        if i < len(fragments) - 1:
            parts.append("")
    output = "\n".join(parts) + "\n"

    output_path = runtime_prompt_output()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(output)

    return {
        "ok": True,
        "deprecated": True,
        "output": output_path,
        "fragments_used": [name for name, _ in fragments],
        "output_hash": _file_hash(output_path),
        "output_size": len(output),
    }


_STALE_RUNTIME_PROMPT_MARKERS = (
    "## 工具路由",
    "\n## 工具\n",
    "{{ name_hint",
    "{{name_hint",
    "{{ alias_names",
    "{{alias_names",
    "{{ character_name",
    "{{character_name",
    "`persona_update`：用户说\"记住了\"时更新画像",
    "`read`/`write`/`edit`/`grep`/`glob`/`bash`：文件操作工具",
)


def _included_runtime_fragment_mtime(chat_type: str = "base") -> float:
    fragments_dir = runtime_fragments_dir()
    if chat_type == "base":
        allowed = ("00_", "05_", "10_", "30_")
    elif chat_type == "group":
        allowed = ("00_", "05_", "10_", "20_", "25_", "30_")
    elif chat_type == "private":
        allowed = ("00_", "05_", "10_", "26_", "30_")
    else:
        allowed = ("00_", "05_", "10_", "30_")
    newest = 0.0
    if not os.path.isdir(fragments_dir):
        return newest
    for fname in os.listdir(fragments_dir):
        if not fname.endswith(".md"):
            continue
        if not any(fname.startswith(p) for p in allowed):
            continue
        try:
            newest = max(newest, os.path.getmtime(os.path.join(fragments_dir, fname)))
        except OSError:
            pass
    return newest


def _runtime_prompt_needs_rebuild(path: str, content: str) -> bool:
    """判断运行时 prompt.md 是否是旧构建产物。"""
    if any(marker in content for marker in _STALE_RUNTIME_PROMPT_MARKERS):
        return True
    try:
        output_mtime = os.path.getmtime(path)
    except OSError:
        return True
    return _included_runtime_fragment_mtime("base") > output_mtime


def read_runtime_or_default_prompt() -> dict[str, Any]:
    """读取完整 prompt.md。优先运行时，fallback 到默认。"""
    rp = runtime_prompt_output()
    dp = legacy_default_prompt_path()
    if os.path.isfile(rp):
        with open(rp, "r", encoding="utf-8") as fh:
            content = fh.read()
        auto_rebuilt = False
        if _runtime_prompt_needs_rebuild(rp, content):
            result = build_prompt_from_runtime("base")
            if result.get("ok") and os.path.isfile(rp):
                with open(rp, "r", encoding="utf-8") as fh:
                    content = fh.read()
                auto_rebuilt = True
        return {
            "content": content,
            "source": "runtime",
            "deprecated": True,
            "output_path": rp,
            "default_path": dp,
            "auto_rebuilt": auto_rebuilt,
        }
    if os.path.isfile(dp):
        with open(dp, "r", encoding="utf-8") as fh:
            content = fh.read()
        return {
            "content": content,
            "source": "default",
            "deprecated": True,
            "output_path": rp,
            "default_path": dp,
        }
    return {"content": "", "source": "none", "deprecated": True, "output_path": rp, "default_path": dp}
