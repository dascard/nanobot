"""响应信封渲染契约 eval runner。"""
from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from core.qq_outbound_renderer import render_qq_outbound_envelope
from evals.schema import EvalCase, EvalOutput


@contextmanager
def _temporary_generated_image_public_env(inp: Mapping[str, Any]):
    base_url = inp.get("public_base_url")
    token = inp.get("generated_image_token")
    keys = {
        "NANOBOT_PUBLIC_BASE_URL": None if base_url is None else str(base_url),
        "NANOBOT_GENERATED_IMAGE_TOKEN": None if token is None else str(token),
    }
    old = {key: os.environ.get(key) for key in keys}
    try:
        for key, value in keys.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_rendering_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    envelope = inp.get("envelope")
    if not isinstance(envelope, Mapping):
        return EvalOutput(
            case_id=case.id,
            suite=case.suite,
            errors=["input.envelope must be object"],
        )

    with _temporary_generated_image_public_env(inp):
        result = render_qq_outbound_envelope(
            envelope,
            allow_base64=bool(inp.get("allow_base64", False)),
        )

    return EvalOutput(
        case_id=case.id,
        suite=case.suite,
        reply_text=result.message,
        should_reply=bool(result.message),
        reply_meta=result.reply_meta,
        raw={
            "rendered_message": result.message,
            "messages": result.messages,
            "reply_meta": result.reply_meta,
            "warnings": result.warnings,
        },
    )
