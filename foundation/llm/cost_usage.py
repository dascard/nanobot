"""归一化供应商成本，并在显式定价存在时生成保守估算。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMCostUsage:
    cost_microusd: int
    source: str
    estimated: bool
    details: dict[str, Any]


def _mapping_path(value: Any, *parts: str) -> tuple[bool, Any]:
    current = value
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _nonnegative_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _reported_cost(response: Any) -> tuple[int, str] | None:
    micro_paths = (
        ("usage", "cost_microusd"),
        ("usage", "cost_microunits"),
        ("cost_microusd",),
        ("cost_microunits",),
    )
    for path in micro_paths:
        present, raw = _mapping_path(response, *path)
        parsed = _nonnegative_decimal(raw) if present else None
        if parsed is not None:
            return (
                int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                ".".join(path),
            )
    dollar_paths = (
        ("usage", "cost"),
        ("usage", "total_cost"),
        ("cost",),
    )
    for path in dollar_paths:
        present, raw = _mapping_path(response, *path)
        parsed = _nonnegative_decimal(raw) if present else None
        if parsed is not None:
            micros = (parsed * Decimal("1000000")).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
            return int(micros), ".".join(path)
    return None


def _price(value: Any) -> Decimal | None:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return _nonnegative_decimal(value)


def normalize_llm_cost_usage(
    response: Any,
    *,
    successful: bool,
    input_tokens: int,
    output_tokens: int,
    cost_input_1m: Any = None,
    cost_output_1m: Any = None,
) -> LLMCostUsage:
    """优先采用供应商成本，否则按每百万 token 显式定价估算。"""

    if not successful:
        return LLMCostUsage(0, "error", False, {})
    reported = _reported_cost(response)
    if reported is not None:
        cost, source = reported
        return LLMCostUsage(
            cost,
            "provider_reported",
            False,
            {"reported_source": source},
        )
    input_price = _price(cost_input_1m)
    output_price = _price(cost_output_1m)
    if input_price is None or output_price is None:
        return LLMCostUsage(0, "not_available", False, {})
    estimated = (
        Decimal(max(0, int(input_tokens or 0))) * input_price
        + Decimal(max(0, int(output_tokens or 0))) * output_price
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return LLMCostUsage(
        int(estimated),
        "pricing_estimate",
        True,
        {
            "cost_input_1m": float(input_price),
            "cost_output_1m": float(output_price),
        },
    )


__all__ = ["LLMCostUsage", "normalize_llm_cost_usage"]
