from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_chat_media_precache_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_media_precache.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert (
        "from nanobot_kt.image_pipeline import precache_image_sources"
        not in source.splitlines()[:20]
    )


def test_parent_media_precache_wrapper_remains_in_routes_and_delegates(monkeypatch):
    from api import routes

    calls = []

    def fake_schedule(background_tasks, files, **kwargs):
        calls.append((background_tasks, files, kwargs))

    monkeypatch.setattr("api.chat_media_precache.schedule_image_precache", fake_schedule)

    background_tasks = object()
    routes._schedule_image_precache(
        background_tasks,
        [" img://a "],
        source_type="chat_request",
        source_name_prefix="session_message",
    )

    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert len(calls) == 1
    assert calls[0][0] is background_tasks
    assert calls[0][1] == [" img://a "]
    assert calls[0][2]["source_type"] == "chat_request"
    assert calls[0][2]["source_name_prefix"] == "session_message"
    assert calls[0][2]["normalize_files"] is routes._normalize_files


def test_schedule_image_precache_noops_without_files_or_background_tasks():
    from api.chat_media_precache import schedule_image_precache

    class FakeBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    tasks = FakeBackgroundTasks()
    schedule_image_precache(
        tasks,
        ["", "  "],
        source_type="chat_request",
        source_name_prefix="empty",
        normalize_files=lambda files: [],
        precache_image_sources=lambda *args, **kwargs: None,
    )
    schedule_image_precache(
        None,
        ["img://a"],
        source_type="chat_request",
        source_name_prefix="none",
        normalize_files=lambda files: ["img://a"],
        precache_image_sources=lambda *args, **kwargs: None,
    )

    assert tasks.calls == []


def test_schedule_image_precache_adds_precache_task_with_normalized_files():
    from api.chat_media_precache import schedule_image_precache

    class FakeBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, func, *args, **kwargs):
            self.calls.append((func, args, kwargs))

    def fake_precache(*args, **kwargs):
        return None

    tasks = FakeBackgroundTasks()

    schedule_image_precache(
        tasks,
        [" raw "],
        source_type="chat_request",
        source_name_prefix="session_message",
        normalize_files=lambda files: ["img://a", "img://b"],
        precache_image_sources=fake_precache,
    )

    assert tasks.calls == [
        (
            fake_precache,
            (["img://a", "img://b"],),
            {
                "source_type": "chat_request",
                "source_name_prefix": "session_message",
            },
        )
    ]
