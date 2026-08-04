from __future__ import annotations

import pytest

from core.media_preprocess_runtime import (
    ImagePrecachePort,
    ImagePrecacheRuntimeUnavailableError,
    bind_image_precache_port,
    clear_image_precache_port,
    get_image_precache_port,
)


class _FakeImagePrecachePort:
    def __init__(self) -> None:
        self.calls = []

    def precache(
        self,
        sources: tuple[str, ...],
        *,
        source_type: str,
        source_name_prefix: str,
    ):
        self.calls.append((sources, source_type, source_name_prefix))
        return ({"source": sources[0], "ok": True},) if sources else ()


@pytest.fixture(autouse=True)
def _clear_runtime():
    clear_image_precache_port()
    yield
    clear_image_precache_port()


def test_image_precache_runtime_is_explicit_and_fail_closed():
    with pytest.raises(ImagePrecacheRuntimeUnavailableError):
        get_image_precache_port()

    port = _FakeImagePrecachePort()
    assert isinstance(port, ImagePrecachePort)
    bind_image_precache_port(port)

    assert get_image_precache_port() is port
    assert port.precache(
        ("https://example.invalid/image.jpg",),
        source_type="chat",
        source_name_prefix="message",
    ) == ({"source": "https://example.invalid/image.jpg", "ok": True},)

    with pytest.raises(RuntimeError, match="已绑定"):
        bind_image_precache_port(_FakeImagePrecachePort())
