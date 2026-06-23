from __future__ import annotations

from pathlib import Path

from api import routes


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_chat_guardrail_facade_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_guardrail_facade.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source


def test_parent_guardrail_wrapper_keeps_api_routes_module():
    assert routes._detect_guardrail.__module__ == "api.routes"


def test_detect_guardrail_prefers_detect_injection():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def __init__(self):
            self.detect_calls = []
            self.classify_calls = []

        def detect_injection(self, message, *, allow_passthrough=False):
            self.detect_calls.append((message, allow_passthrough))
            return {"status": "safe", "custom": "kept"}

        def classify(self, message, allow_injection_passthrough=False):
            self.classify_calls.append((message, allow_injection_passthrough))
            return {"status": "injection"}

    guardrail = Guardrail()
    result = detect_guardrail(guardrail, "hello", allow_passthrough=True)

    assert result == {"status": "safe", "custom": "kept"}
    assert guardrail.detect_calls == [("hello", True)]
    assert guardrail.classify_calls == []


def test_detect_guardrail_legacy_reply_maps_to_safe_and_keeps_passthrough():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def __init__(self):
            self.calls = []

        def classify(self, message, allow_injection_passthrough=False):
            self.calls.append((message, allow_injection_passthrough))
            return {"status": "reply", "complexity": 5}

    guardrail = Guardrail()
    result = detect_guardrail(guardrail, "hello", allow_passthrough=True)

    assert guardrail.calls == [("hello", True)]
    assert result == {
        "status": "safe",
        "complexity": 5,
        "injection": False,
        "passthrough": True,
    }


def test_detect_guardrail_legacy_silent_maps_to_silent():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "silent", "complexity": 0}

    result = detect_guardrail(Guardrail(), "hello", allow_passthrough=True)

    assert result == {
        "status": "silent",
        "complexity": 0,
        "injection": False,
        "passthrough": True,
    }


def test_detect_guardrail_legacy_injection_maps_to_injection():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "injection", "complexity": 0}

    result = detect_guardrail(Guardrail(), "hello", allow_passthrough=True)

    assert result == {
        "status": "injection",
        "complexity": 0,
        "injection": True,
        "passthrough": False,
    }


def test_detect_guardrail_legacy_non_dict_falls_back_to_safe():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return "bad"

    result = detect_guardrail(Guardrail(), "hello")

    assert result == {
        "status": "safe",
        "injection": False,
        "passthrough": False,
    }


def test_guardrail_status_from_result_maps_known_and_unknown_values():
    from api.chat_guardrail_facade import guardrail_status_from_result

    assert guardrail_status_from_result({"status": "injection"}) == "injection"
    assert guardrail_status_from_result({"status": "silent"}) == "silent"
    assert guardrail_status_from_result({"status": "safe"}) == "safe"
    assert guardrail_status_from_result({"status": "reply"}) == "safe"
    assert guardrail_status_from_result({}) == "safe"
    assert guardrail_status_from_result(None) == "safe"


def test_parent_guardrail_wrapper_matches_new_module():
    from api.chat_guardrail_facade import detect_guardrail

    class Guardrail:
        def classify(self, message, allow_injection_passthrough=False):
            return {"status": "reply", "complexity": 3}

    parent_guardrail = Guardrail()
    module_guardrail = Guardrail()

    assert routes._detect_guardrail(
        parent_guardrail,
        "hello",
        allow_passthrough=True,
    ) == detect_guardrail(
        module_guardrail,
        "hello",
        allow_passthrough=True,
    )
