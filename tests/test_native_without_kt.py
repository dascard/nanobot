"""Native Runtime 在 KT 可选依赖缺失时的进程级回归。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_native_runtime_and_server_import_without_kt() -> None:
    script = textwrap.dedent(
        """
        import asyncio
        import importlib.abc
        import sys
        from datetime import datetime, timedelta, timezone

        OPTIONAL_KT_IMPORT_ROOTS = {
            "aiofiles",
            "anthropic",
            "bottle",
            "brotli",
            "ddgs",
            "distro",
            "docstring_parser",
            "fake_useragent",
            "fitz",
            "git",
            "gitdb",
            "h2",
            "hpack",
            "html2text",
            "httpcore2",
            "httptools",
            "httpx2",
            "hyperframe",
            "jiter",
            "jwt",
            "kohakuterrarium",
            "kohakuvault",
            "libcst",
            "linkify_it",
            "mdit_py_plugins",
            "model2vec",
            "multipart",
            "openai",
            "opentelemetry",
            "platformdirs",
            "prompt_toolkit",
            "proxy_tools",
            "pyjwt",
            "pymupdf",
            "ruamel",
            "smmap",
            "sniffio",
            "socksio",
            "sse_starlette",
            "textual",
            "truststore",
            "uc_micro",
            "uvloop",
            "watchfiles",
            "wcwidth",
            "websockets",
            "webview",
        }

        class BlockKtOnlyDependencies(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in OPTIONAL_KT_IMPORT_ROOTS:
                    raise ModuleNotFoundError(
                        f"blocked optional dependency: {fullname}",
                        name=fullname,
                    )
                return None

        sys.meta_path.insert(0, BlockKtOnlyDependencies())

        from bootstrap.model_runtime import (
            start_model_runtime,
            stop_model_runtime,
        )
        from bootstrap.native_tool_runtime import (
            build_native_tool_execution_port,
        )
        from core.agent_runtime import (
            AgentRuntimeKind,
            RequestRuntimeContext,
            RuntimeActor,
            RuntimeActorType,
            RuntimeChatType,
            RuntimeCapability,
            RuntimeOwnerType,
            RuntimePrincipal,
            RuntimeToolCall,
            RuntimeToolExecutionRequest,
        )
        from core.agent_runtime.request_scope import runtime_context_scope
        from nanobot_kt.bridge import NanobotBridge
        from server import app

        start_model_runtime()
        stop_model_runtime()
        assert app.title == "Nanobot Self-Evolution Gateway"

        context = RequestRuntimeContext(
            request_id="without-kt-1",
            agent_id="nanobot",
            principal=RuntimePrincipal(
                "qq",
                RuntimeOwnerType.USER,
                "10001",
            ),
            session_id="private_10001",
            chat_type=RuntimeChatType.PRIVATE,
            trace_id="trace-without-kt",
            run_id="run-without-kt",
            turn_id="turn-without-kt",
            correlation_id="correlation-without-kt",
            actor=RuntimeActor(RuntimeActorType.USER, "10001"),
            deadline_at=(
                datetime.now(timezone.utc) + timedelta(seconds=10)
            ),
        )
        request = RuntimeToolExecutionRequest(
            context=context,
            tool_call=RuntimeToolCall(
                "call-reply",
                "reply",
                {"content": "无 KT 回复"},
            ),
            execution_port_id="tool.reply.execute",
            idempotency_key="without-kt-1:call-reply",
            timeout_seconds=10,
        )

        async def main():
            bridge = NanobotBridge(runtime_kind=AgentRuntimeKind.NATIVE)
            await bridge.start()
            assert bridge.agent is None
            assert bridge._runtime.runtime_id == "native:nanobot"
            assert bridge._runtime.runtime_capabilities.supports(
                RuntimeCapability.CHECKPOINT_RECOVERY
            )
            assert "reply" in bridge._runtime.list_tool_names()

            port = build_native_tool_execution_port()
            with runtime_context_scope({
                "chat_type": "private",
                "runtime_chat_type": "private",
                "is_group": False,
                "session_id": "private_10001",
                "user_id": "10001",
                "platform": "qq",
            }):
                result = await port.execute(request)
            assert result.success
            assert "无 KT 回复" in str(result.output)
            await bridge.stop()

        asyncio.run(main())
        print("native_without_kt=ok")
        """
    )
    env = dict(os.environ)
    env.update({
        "NANOBOT_TESTING": "1",
        "DATABASE_URL": "sqlite:///:memory:",
        "NEW_API_KEY": "test-key-for-ci",
        "NANOBOT_API_TOKEN": "",
    })
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "native_without_kt=ok" in result.stdout
