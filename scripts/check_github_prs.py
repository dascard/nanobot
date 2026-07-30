#!/usr/bin/env python3
"""用持久状态和 ETag 检查 GitHub 仓库的新 Pull Request。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


STATE_VERSION = 1
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEFAULT_STATE_FILE = ".nanobot/github-pr-watch.json"


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_state(path: Path) -> tuple[dict[str, Any], bool]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}, False
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {}, False
    seen = raw.get("seen_numbers")
    if not isinstance(seen, list) or any(type(item) is not int for item in seen):
        return {}, False
    return dict(raw), True


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = _json_text(dict(state)) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _normalize_prs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("GitHub PR 响应不是数组")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        number = item.get("number")
        if type(number) is not int or number <= 0:
            continue
        user = item.get("user")
        author = (
            str(user.get("login") or "unknown")
            if isinstance(user, Mapping)
            else "unknown"
        )
        result.append(
            {
                "number": number,
                "title": str(item.get("title") or "").strip(),
                "author": author,
                "html_url": str(item.get("html_url") or "").strip(),
                "created_at": str(item.get("created_at") or "").strip(),
            }
        )
    return result


def _fetch_open_prs(
    repo: str,
    *,
    etag: str = "",
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[list[dict[str, Any]] | None, str]:
    url = (
        f"https://api.github.com/repos/{repo}/pulls"
        "?state=open&sort=created&direction=desc&per_page=100"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nanobot-pr-checker/2.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
            next_etag = str(response.headers.get("ETag") or etag)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag
        raise
    return _normalize_prs(payload), next_etag


def _build_message(prs: list[dict[str, Any]]) -> str:
    lines = [f"发现 {len(prs)} 个新的 Pull Request："]
    for pr in prs:
        title = str(pr.get("title") or "（无标题）")
        lines.extend(
            [
                f"- #{pr['number']} {title}",
                f"  作者：{pr['author']}",
                f"  创建时间：{pr['created_at'] or '未知'}",
                f"  链接：{pr['html_url']}",
            ]
        )
    return "\n".join(lines)


def _ack_command(
    *,
    script_name: str,
    repo: str,
    state_file: str,
    token: str,
) -> str:
    return " ".join(
        shlex.quote(item)
        for item in (
            "python3",
            script_name,
            "--repo",
            repo,
            "--state-file",
            state_file,
            "--ack",
            token,
        )
    )


def check(
    *,
    repo: str,
    state_path: Path,
    state_file_arg: str,
    script_name: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
    token_factory: Callable[[], str] = lambda: secrets.token_hex(16),
) -> dict[str, Any]:
    state, initialized = _load_state(state_path)
    pending = state.get("pending")
    if isinstance(pending, Mapping):
        token = str(pending.get("token") or "")
        message = str(pending.get("message") or "")
        prs = pending.get("prs")
        if token and message and isinstance(prs, list):
            return {
                "action": "report",
                "reason": "pending_delivery",
                "cache_hit": True,
                "prs": prs,
                "message": message,
                "ack_command": _ack_command(
                    script_name=script_name,
                    repo=repo,
                    state_file=state_file_arg,
                    token=token,
                ),
            }

    etag = str(state.get("etag") or "") if initialized else ""
    prs, next_etag = _fetch_open_prs(repo, etag=etag, opener=opener)
    if prs is None:
        return {
            "action": "noop",
            "reason": "etag_not_modified",
            "cache_hit": True,
        }

    current_numbers = {int(item["number"]) for item in prs}
    if not initialized:
        _write_state(
            state_path,
            {
                "version": STATE_VERSION,
                "repo": repo,
                "etag": next_etag,
                "seen_numbers": sorted(current_numbers),
            },
        )
        return {
            "action": "noop",
            "reason": "baseline_initialized",
            "cache_hit": False,
        }

    seen_numbers = {
        int(item)
        for item in state.get("seen_numbers", [])
        if type(item) is int and item > 0
    }
    new_prs = [item for item in prs if int(item["number"]) not in seen_numbers]
    state["etag"] = next_etag
    state["repo"] = repo
    if not new_prs:
        _write_state(state_path, state)
        return {
            "action": "noop",
            "reason": "no_new_pr",
            "cache_hit": False,
        }

    token = token_factory()
    if not re.fullmatch(r"[0-9a-f]{32,64}", token):
        raise ValueError("确认令牌格式无效")
    message = _build_message(new_prs)
    pending = {
        "token": token,
        "numbers": [int(item["number"]) for item in new_prs],
        "prs": new_prs,
        "message": message,
        "fingerprint": hashlib.sha256(
            _json_text(new_prs).encode("utf-8")
        ).hexdigest(),
    }
    state["pending"] = pending
    _write_state(state_path, state)
    return {
        "action": "report",
        "reason": "new_pr",
        "cache_hit": False,
        "prs": new_prs,
        "message": message,
        "ack_command": _ack_command(
            script_name=script_name,
            repo=repo,
            state_file=state_file_arg,
            token=token,
        ),
    }


def acknowledge(*, state_path: Path, token: str) -> dict[str, Any]:
    state, initialized = _load_state(state_path)
    if not initialized:
        return {"action": "acked", "reason": "state_absent"}
    pending = state.get("pending")
    if not isinstance(pending, Mapping):
        return {"action": "acked", "reason": "already_acked"}
    if str(pending.get("token") or "") != token:
        raise ValueError("确认令牌与待投递批次不匹配")
    seen_numbers = {
        int(item)
        for item in state.get("seen_numbers", [])
        if type(item) is int and item > 0
    }
    seen_numbers.update(
        int(item)
        for item in pending.get("numbers", [])
        if type(item) is int and item > 0
    )
    state["seen_numbers"] = sorted(seen_numbers)
    state.pop("pending", None)
    _write_state(state_path, state)
    return {"action": "acked", "reason": "delivery_recorded"}


def _state_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("state-file 必须是安全的 Workspace 相对路径")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--ack", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not REPO_RE.fullmatch(args.repo):
            raise ValueError("repo 必须是 owner/name")
        state_path = _state_path(args.state_file)
        if args.ack:
            result = acknowledge(state_path=state_path, token=str(args.ack))
        else:
            result = check(
                repo=args.repo,
                state_path=state_path,
                state_file_arg=args.state_file,
                script_name=Path(__file__).name,
            )
    except urllib.error.HTTPError as exc:
        result = {
            "action": "noop",
            "reason": "github_http_error",
            "status_code": int(exc.code),
        }
    except urllib.error.URLError:
        result = {"action": "noop", "reason": "network_error"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            _json_text(
                {
                    "action": "error",
                    "reason": type(exc).__name__,
                }
            )
        )
        return 1
    print(_json_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
