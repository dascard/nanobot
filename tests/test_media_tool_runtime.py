import pytest

from core.media_tool_runtime import (
    MediaToolRuntimeUnavailableError,
    bind_media_tool_providers,
    clear_media_tool_providers,
    get_image_generation_provider,
    get_image_summary_provider,
)


class _FakeImageSummaryProvider:
    def __init__(self) -> None:
        self.invalidated = False

    def summarize(self, files: tuple[str, ...], focus: str) -> str:
        return f"{','.join(files)}:{focus}"

    def invalidate_route_cache(self) -> None:
        self.invalidated = True


class _FakeImageGenerationProvider:
    def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
        background: str,
    ) -> dict[str, object]:
        return {
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "background": background,
        }


@pytest.fixture(autouse=True)
def _reset_media_tool_runtime():
    clear_media_tool_providers()
    yield
    clear_media_tool_providers()


def test_media_tool_runtime_requires_explicit_binding():
    with pytest.raises(
        MediaToolRuntimeUnavailableError,
        match="图片摘要 Provider 当前不可用",
    ):
        get_image_summary_provider()
    with pytest.raises(
        MediaToolRuntimeUnavailableError,
        match="图片生成 Provider 当前不可用",
    ):
        get_image_generation_provider()


def test_media_tool_runtime_delegates_to_bound_providers():
    summary = _FakeImageSummaryProvider()
    generation = _FakeImageGenerationProvider()
    bind_media_tool_providers(summary=summary, generation=generation)

    assert get_image_summary_provider().summarize(
        ("a.png", "b.png"),
        "主体",
    ) == "a.png,b.png:主体"
    get_image_summary_provider().invalidate_route_cache()
    assert summary.invalidated is True
    assert get_image_generation_provider().generate(
        prompt="一只猫",
        size="1024x1024",
        quality="high",
        background="opaque",
    ) == {
        "prompt": "一只猫",
        "size": "1024x1024",
        "quality": "high",
        "background": "opaque",
    }


def test_media_tool_runtime_rejects_duplicate_binding():
    bind_media_tool_providers(
        summary=_FakeImageSummaryProvider(),
        generation=_FakeImageGenerationProvider(),
    )

    with pytest.raises(RuntimeError, match="Media Tool Runtime 已绑定"):
        bind_media_tool_providers(
            summary=_FakeImageSummaryProvider(),
            generation=_FakeImageGenerationProvider(),
        )
