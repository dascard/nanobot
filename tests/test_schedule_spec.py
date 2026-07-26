"""schedule 规格解析与下一次触发计算的单元测试。

时间约定:所有 *_utc 参数与返回值均为 UTC naive;
cron 表达式与 ISO 时间戳按 Asia/Shanghai 解释(UTC+8)。
"""

from datetime import datetime, timedelta

import pytest

from core.schedule_spec import (
    KIND_CRON,
    KIND_INTERVAL,
    KIND_ONCE,
    MAX_GRACE_SECONDS,
    MIN_GRACE_SECONDS,
    ScheduleSpecError,
    grace_seconds,
    initial_anchor,
    next_fire_after,
    parse_schedule,
    resolve_schedule_fields,
    schedule_display,
    schedule_fields,
    spec_from_fields,
)


# 2026-07-15 12:00:00 上海 = 04:00:00 UTC
NOW_UTC = datetime(2026, 7, 15, 4, 0, 0)


# ── parse_schedule ──────────────────────────────────────────────


def test_parse_schedule_interval_minutes():
    spec = parse_schedule("every 30m", now_utc=NOW_UTC)

    assert spec["kind"] == KIND_INTERVAL
    assert spec["minutes"] == 30


def test_parse_schedule_interval_hours():
    spec = parse_schedule("every 2h", now_utc=NOW_UTC)

    assert spec["kind"] == KIND_INTERVAL
    assert spec["minutes"] == 120


def test_parse_schedule_cron_expression():
    spec = parse_schedule("0 9 * * *", now_utc=NOW_UTC)

    assert spec["kind"] == KIND_CRON
    assert spec["expr"] == "0 9 * * *"


def test_parse_schedule_cron_rejects_names_and_macros():
    with pytest.raises(ScheduleSpecError):
        parse_schedule("0 9 * * mon", now_utc=NOW_UTC)


def test_parse_schedule_cron_rejects_invalid_field():
    with pytest.raises(ScheduleSpecError):
        parse_schedule("99 9 * * *", now_utc=NOW_UTC)


def test_parse_schedule_duration_is_one_shot():
    spec = parse_schedule("30m", now_utc=NOW_UTC)

    assert spec["kind"] == KIND_ONCE
    assert next_fire_after(spec, base_utc=NOW_UTC) == NOW_UTC + timedelta(
        minutes=30
    )


def test_parse_schedule_iso_timestamp_anchors_shanghai():
    spec = parse_schedule("2026-08-01T15:00", now_utc=NOW_UTC)

    assert spec["kind"] == KIND_ONCE
    # 15:00 上海 = 07:00 UTC
    assert next_fire_after(spec, base_utc=NOW_UTC) == datetime(
        2026, 8, 1, 7, 0, 0
    )


def test_parse_schedule_iso_timestamp_in_past_rejected():
    with pytest.raises(ScheduleSpecError):
        parse_schedule("2026-07-14T09:00", now_utc=NOW_UTC)


def test_parse_schedule_garbage_rejected():
    with pytest.raises(ScheduleSpecError):
        parse_schedule("明天九点", now_utc=NOW_UTC)


def test_parse_schedule_empty_rejected():
    with pytest.raises(ScheduleSpecError):
        parse_schedule("   ", now_utc=NOW_UTC)


# ── 字段序列化与回读 ────────────────────────────────────────────


def test_schedule_fields_round_trip_cron():
    spec = parse_schedule("0 9 * * 1", now_utc=NOW_UTC)
    kind, spec_json, cron_expr = schedule_fields(spec)

    assert kind == KIND_CRON
    assert cron_expr == "0 9 * * 1"
    restored = spec_from_fields(kind, spec_json, cron_expr)
    assert restored == spec


def test_schedule_fields_round_trip_once():
    spec = parse_schedule("2026-08-01T15:00", now_utc=NOW_UTC)
    kind, spec_json, cron_expr = schedule_fields(spec)

    assert kind == KIND_ONCE
    assert cron_expr == ""
    restored = spec_from_fields(kind, spec_json, cron_expr)
    assert next_fire_after(restored, base_utc=NOW_UTC) == datetime(
        2026, 8, 1, 7, 0, 0
    )


def test_spec_from_fields_legacy_row_falls_back_to_cron_expr():
    restored = spec_from_fields("", "", "0 9 * * *")

    assert restored is not None
    assert restored["kind"] == KIND_CRON
    assert restored["expr"] == "0 9 * * *"


def test_spec_from_fields_unparseable_returns_none():
    assert spec_from_fields("", "", "not a cron") is None
    assert spec_from_fields("once", "{broken json", "") is None


# ── next_fire_after ─────────────────────────────────────────────


def test_next_fire_after_cron_same_day():
    spec = parse_schedule("0 13 * * *", now_utc=NOW_UTC)

    # 现在 12:00 上海,下一次 13:00 上海 = 05:00 UTC
    assert next_fire_after(spec, base_utc=NOW_UTC) == datetime(
        2026, 7, 15, 5, 0, 0
    )


def test_next_fire_after_cron_rolls_to_next_day():
    spec = parse_schedule("0 9 * * *", now_utc=NOW_UTC)

    # 现在 12:00 上海已过 9 点,下一次是次日 09:00 上海 = 01:00 UTC
    assert next_fire_after(spec, base_utc=NOW_UTC) == datetime(
        2026, 7, 16, 1, 0, 0
    )


def test_next_fire_after_cron_sunday_zero_is_standard_semantics():
    spec = parse_schedule("0 9 * * 0", now_utc=NOW_UTC)

    fire = next_fire_after(spec, base_utc=NOW_UTC)
    assert fire is not None
    # 2026-07-19 是周日;09:00 上海 = 01:00 UTC
    assert fire == datetime(2026, 7, 19, 1, 0, 0)


def test_next_fire_after_interval_advances_from_base():
    spec = parse_schedule("every 30m", now_utc=NOW_UTC)

    assert next_fire_after(spec, base_utc=NOW_UTC) == NOW_UTC + timedelta(
        minutes=30
    )


def test_next_fire_after_once_fires_only_once():
    spec = parse_schedule("30m", now_utc=NOW_UTC)
    run_at = NOW_UTC + timedelta(minutes=30)

    assert next_fire_after(spec, base_utc=NOW_UTC) == run_at
    # 已触发(base 晚于 run_at)后不再产生下一次
    assert next_fire_after(spec, base_utc=run_at) is None


def test_next_fire_after_once_missed_within_grace_still_returns():
    spec = parse_schedule("30m", now_utc=NOW_UTC)
    run_at = NOW_UTC + timedelta(minutes=30)

    late_base = run_at + timedelta(seconds=MAX_GRACE_SECONDS - 60)
    # 错过但仍在宽限窗口内:懒初始化仍应返回 run_at 以便补发
    assert (
        next_fire_after(spec, base_utc=late_base, include_missed=True)
        == run_at
    )
    beyond = run_at + timedelta(seconds=MAX_GRACE_SECONDS + 60)
    assert next_fire_after(spec, base_utc=beyond, include_missed=True) is None


def test_initial_anchor_includes_current_minute_slot():
    now = datetime(2026, 7, 15, 4, 0, 20)
    spec = parse_schedule("* * * * *", now_utc=now)

    anchor = initial_anchor(now)
    # 当前分钟槽 04:00 应可作为首次触发(与旧调度器行为一致)
    assert next_fire_after(spec, base_utc=anchor) == datetime(
        2026, 7, 15, 4, 0, 0
    )


# ── grace_seconds ───────────────────────────────────────────────


def test_grace_seconds_frequent_cron_clamped_to_min():
    spec = parse_schedule("* * * * *", now_utc=NOW_UTC)

    assert grace_seconds(spec) == MIN_GRACE_SECONDS


def test_grace_seconds_daily_cron_clamped_to_max():
    spec = parse_schedule("0 9 * * *", now_utc=NOW_UTC)

    assert grace_seconds(spec) == MAX_GRACE_SECONDS


def test_grace_seconds_interval_half_period():
    spec = parse_schedule("every 10m", now_utc=NOW_UTC)

    assert grace_seconds(spec) == 300


def test_grace_seconds_once_uses_max():
    spec = parse_schedule("30m", now_utc=NOW_UTC)

    assert grace_seconds(spec) == MAX_GRACE_SECONDS


# ── resolve_schedule_fields(工具/路由共用入口) ─────────────────


def test_resolve_schedule_fields_prefers_schedule_text():
    fields = resolve_schedule_fields(
        schedule="every 1h",
        cron_expr="0 9 * * *",
        now_utc=NOW_UTC,
    )

    assert fields.schedule_kind == KIND_INTERVAL
    assert fields.cron_expr == ""
    assert fields.next_fire_at == NOW_UTC + timedelta(hours=1)


def test_resolve_schedule_fields_accepts_legacy_cron_expr():
    fields = resolve_schedule_fields(
        schedule=None,
        cron_expr="0 13 * * *",
        now_utc=NOW_UTC,
    )

    assert fields.schedule_kind == KIND_CRON
    assert fields.cron_expr == "0 13 * * *"
    assert fields.next_fire_at == datetime(2026, 7, 15, 5, 0, 0)


def test_resolve_schedule_fields_requires_some_schedule():
    with pytest.raises(ScheduleSpecError):
        resolve_schedule_fields(schedule=None, cron_expr="", now_utc=NOW_UTC)


def test_schedule_display_is_human_readable():
    assert "30" in schedule_display(parse_schedule("every 30m", now_utc=NOW_UTC))
    assert "0 9 * * *" in schedule_display(
        parse_schedule("0 9 * * *", now_utc=NOW_UTC)
    )
