from __future__ import annotations


def test_provider_reported_cost_takes_precedence_over_pricing_estimate():
    from foundation.llm.cost_usage import normalize_llm_cost_usage

    result = normalize_llm_cost_usage(
        {"usage": {"cost": 0.012345}},
        successful=True,
        input_tokens=1000,
        output_tokens=100,
        cost_input_1m=1.0,
        cost_output_1m=2.0,
    )

    assert result.cost_microusd == 12345
    assert result.source == "provider_reported"
    assert result.estimated is False
    assert result.details["reported_source"] == "usage.cost"


def test_pricing_estimate_requires_both_prices():
    from foundation.llm.cost_usage import normalize_llm_cost_usage

    result = normalize_llm_cost_usage(
        {"usage": {"prompt_tokens": 100}},
        successful=True,
        input_tokens=100,
        output_tokens=20,
        cost_input_1m=1.0,
        cost_output_1m=None,
    )

    assert result.cost_microusd == 0
    assert result.source == "not_available"
