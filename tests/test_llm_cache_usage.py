import pytest

from foundation.llm.cache_usage import normalize_llm_cache_usage


@pytest.mark.parametrize(
    ("response", "expected_hit_tokens", "expected_write_tokens", "source"),
    [
        (
            {"usage": {"prompt_tokens_details": {"cached_tokens": 128}}},
            128,
            0,
            "usage.prompt_tokens_details.cached_tokens",
        ),
        (
            {"usage": {"input_tokens_details": {"cached_tokens": 64}}},
            64,
            0,
            "usage.input_tokens_details.cached_tokens",
        ),
        (
            {
                "usage": {
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                },
            },
            80,
            20,
            "usage.cache_read_input_tokens",
        ),
        (
            {
                "usage": {
                    "prompt_cache_hit_tokens": 42,
                    "prompt_cache_miss_tokens": 100,
                },
            },
            42,
            0,
            "usage.prompt_cache_hit_tokens",
        ),
        (
            {"usage_metadata": {"cached_content_token_count": 31}},
            31,
            0,
            "usage_metadata.cached_content_token_count",
        ),
        (
            {"usageMetadata": {"cachedContentTokenCount": 23}},
            23,
            0,
            "usageMetadata.cachedContentTokenCount",
        ),
        (
            {"usage": {"cached_tokens": 18, "cache_write_tokens": 7}},
            18,
            7,
            "usage.cached_tokens",
        ),
    ],
)
def test_normalize_llm_cache_usage_records_provider_hits(
    response,
    expected_hit_tokens,
    expected_write_tokens,
    source,
):
    result = normalize_llm_cache_usage(response, successful=True)

    assert result.status == "hit"
    assert result.hit is True
    assert result.hit_tokens == expected_hit_tokens
    assert result.write_tokens == expected_write_tokens
    assert source in {
        metric["source"] for metric in result.details["reported_metrics"]
    }


def test_normalize_llm_cache_usage_keeps_deepseek_miss_tokens_separate():
    result = normalize_llm_cache_usage(
        {
            "usage": {
                "prompt_cache_hit_tokens": 42,
                "prompt_cache_miss_tokens": 100,
            },
        },
        successful=True,
    )

    assert result.hit_tokens == 42
    assert result.miss_tokens == 100
    assert result.write_tokens == 0


def test_normalize_llm_cache_usage_derives_openai_uncached_tokens():
    result = normalize_llm_cache_usage(
        {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 640},
            },
        },
        successful=True,
    )

    assert result.hit_tokens == 640
    assert result.miss_tokens == 360
    assert {
        metric["kind"] for metric in result.details["reported_metrics"]
    } == {"read", "miss_derived"}


def test_normalize_llm_cache_usage_distinguishes_miss_from_not_reported():
    missed = normalize_llm_cache_usage(
        {
            "usage": {
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 240,
            },
        },
        successful=True,
    )
    not_reported = normalize_llm_cache_usage(
        {"usage": {"prompt_tokens": 10}},
        successful=True,
    )

    assert missed.status == "miss"
    assert missed.hit is False
    assert missed.hit_tokens == 0
    assert missed.write_tokens == 240
    assert not_reported.status == "not_reported"
    assert not_reported.hit is None


def test_normalize_llm_cache_usage_does_not_double_count_aliases():
    result = normalize_llm_cache_usage(
        {
            "usage": {
                "prompt_tokens_details": {"cached_tokens": 100},
                "cached_tokens": 100,
                "total_cached_tokens": 100,
            },
        },
        successful=True,
    )

    assert result.hit_tokens == 100
    assert len(result.details["reported_metrics"]) == 3


def test_normalize_llm_cache_usage_reads_stream_chunk_fallback():
    result = normalize_llm_cache_usage(
        {
            "chunks_sample": [
                {"choices": [{"delta": {"content": "正文"}}]},
                {"usageMetadata": {"cachedContentTokenCount": 36}},
            ],
        },
        successful=True,
    )

    assert result.status == "hit"
    assert result.hit_tokens == 36
    assert result.details["reported_metrics"][0]["source"] == (
        "chunks_sample[1].usageMetadata.cachedContentTokenCount"
    )


@pytest.mark.parametrize("invalid_value", [True, -1, 1.5, "12", None])
def test_normalize_llm_cache_usage_ignores_invalid_counts(invalid_value):
    result = normalize_llm_cache_usage(
        {"usage": {"cached_tokens": invalid_value}},
        successful=True,
    )

    assert result.status == "not_reported"
    assert result.hit is None


def test_normalize_llm_cache_usage_marks_failed_calls_as_error():
    result = normalize_llm_cache_usage(
        {"usage": {"cached_tokens": 100}},
        successful=False,
    )

    assert result.status == "error"
    assert result.hit is None
    assert result.hit_tokens == 0
    assert result.miss_tokens == 0
    assert result.details == {}
