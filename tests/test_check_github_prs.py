"""GitHub PR 条件请求与跨轮次状态回归测试。"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

from scripts import check_github_prs


class _Response:
    def __init__(self, payload, *, etag='"v1"') -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        self.headers["ETag"] = etag

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self._payload


def _pr(number: int) -> dict:
    return {
        "number": number,
        "title": f"功能 {number}",
        "user": {"login": "alice"},
        "html_url": f"https://github.com/example/repo/pull/{number}",
        "created_at": "2026-07-30T00:00:00Z",
    }


def test_first_check_initializes_baseline_without_report(tmp_path):
    state_path = tmp_path / "state.json"

    result = check_github_prs.check(
        repo="example/repo",
        state_path=state_path,
        state_file_arg=".nanobot/state.json",
        script_name="check_pr.py",
        opener=lambda *_args, **_kwargs: _Response([_pr(1)]),
    )

    assert result == {
        "action": "noop",
        "reason": "baseline_initialized",
        "cache_hit": False,
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["seen_numbers"] == [1]
    assert state["etag"] == '"v1"'


def test_etag_hit_skips_payload_and_reports_cache_hit(tmp_path):
    state_path = tmp_path / "state.json"
    check_github_prs._write_state(
        state_path,
        {
            "version": 1,
            "repo": "example/repo",
            "etag": '"v1"',
            "seen_numbers": [1],
        },
    )

    def not_modified(request, *, timeout):
        assert timeout == 25
        assert request.get_header("If-none-match") == '"v1"'
        raise urllib.error.HTTPError(
            request.full_url,
            304,
            "Not Modified",
            Message(),
            io.BytesIO(),
        )

    result = check_github_prs.check(
        repo="example/repo",
        state_path=state_path,
        state_file_arg=".nanobot/state.json",
        script_name="check_pr.py",
        opener=not_modified,
    )

    assert result == {
        "action": "noop",
        "reason": "etag_not_modified",
        "cache_hit": True,
    }


def test_new_pr_stays_pending_until_delivery_is_acknowledged(tmp_path):
    state_path = tmp_path / "state.json"
    check_github_prs._write_state(
        state_path,
        {
            "version": 1,
            "repo": "example/repo",
            "etag": '"v1"',
            "seen_numbers": [1],
        },
    )

    result = check_github_prs.check(
        repo="example/repo",
        state_path=state_path,
        state_file_arg=".nanobot/state.json",
        script_name="check_pr.py",
        opener=lambda *_args, **_kwargs: _Response(
            [_pr(2), _pr(1)],
            etag='"v2"',
        ),
        token_factory=lambda: "a" * 32,
    )

    assert result["action"] == "report"
    assert result["cache_hit"] is False
    assert [item["number"] for item in result["prs"]] == [2]
    assert "#2 功能 2" in result["message"]
    assert result["ack_command"].endswith("--ack " + "a" * 32)

    def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("待确认批次应直接命中本地状态")

    repeated = check_github_prs.check(
        repo="example/repo",
        state_path=state_path,
        state_file_arg=".nanobot/state.json",
        script_name="check_pr.py",
        opener=must_not_fetch,
    )
    assert repeated["action"] == "report"
    assert repeated["cache_hit"] is True
    assert repeated["message"] == result["message"]

    acked = check_github_prs.acknowledge(
        state_path=state_path,
        token="a" * 32,
    )
    assert acked == {
        "action": "acked",
        "reason": "delivery_recorded",
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["seen_numbers"] == [1, 2]
    assert "pending" not in state


def test_ack_is_idempotent_after_pending_batch_is_cleared(tmp_path):
    state_path = tmp_path / "state.json"
    check_github_prs._write_state(
        state_path,
        {
            "version": 1,
            "repo": "example/repo",
            "etag": '"v1"',
            "seen_numbers": [1],
        },
    )

    assert check_github_prs.acknowledge(
        state_path=state_path,
        token="b" * 32,
    ) == {
        "action": "acked",
        "reason": "already_acked",
    }
