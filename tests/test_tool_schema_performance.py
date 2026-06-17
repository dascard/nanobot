from __future__ import annotations


def test_tool_schema_preview_does_not_import_runtime_tool_modules(monkeypatch):
    import core.tool_schema_preview as module

    imported: list[str] = []

    def fail_import(name: str):
        imported.append(name)
        raise AssertionError(f"schema preview imported runtime module: {name}")

    monkeypatch.setattr(module.importlib, "import_module", fail_import)

    schema = module.build_tool_schema("ai_daily")
    assert schema["function"]["name"] == "ai_daily"
    assert "AI" in schema["function"]["description"]
    assert "query" in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["properties"]["freshness"]["type"] == "string"
    assert imported == []


def test_builtin_tool_schema_preview_does_not_import_kohakuterrarium(monkeypatch):
    import builtins

    import core.tool_schema_preview as module

    real_import = builtins.__import__
    blocked: list[str] = []

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("kohakuterrarium"):
            blocked.append(name)
            raise AssertionError(f"schema preview imported vendor module: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    schema = module.build_tool_schema("bash")
    assert schema["function"]["name"] == "bash"
    assert "command" in schema["function"]["parameters"]["properties"]
    assert blocked == []
