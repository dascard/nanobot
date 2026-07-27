"""定时任务 schedule 规格:解析、下一次触发计算与迟到宽限。

统一入口接受四种写法(参考 hermes-agent cron 设计):
  - ``30m`` / ``2h`` / ``1d``       一次性,N 时间后触发
  - ``every 30m`` / ``every 2h``    固定间隔循环
  - ``0 9 * * *``                   标准五段 cron
  - ``2026-08-01T15:00``            一次性,指定时刻

时间约定:对外的 ``*_utc`` 参数与返回值一律是 UTC naive(与
outbox ``scheduled_for`` 一致);cron 表达式与 naive ISO 时间戳
按 Asia/Shanghai 解释。cron 语义以 croniter 为准(0=周日)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

SHANGHAI = ZoneInfo("Asia/Shanghai")

KIND_CRON = "cron"
KIND_ONCE = "once"
KIND_INTERVAL = "interval"
SCHEDULE_KINDS = frozenset({KIND_CRON, KIND_ONCE, KIND_INTERVAL})

# 迟到宽限:调度周期的一半,钳制在 [2 分钟, 2 小时]。
MIN_GRACE_SECONDS = 120
MAX_GRACE_SECONDS = 7200

# 创建时允许 ISO 时刻略早于当前(输入到落库之间的耗时容差)。
_PAST_RUN_AT_TOLERANCE_SECONDS = 60

_DURATION_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$"
)
_CRON_FIELD_RE = re.compile(r"^[\d*,/-]+$")
_DURATION_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440}


class ScheduleSpecError(ValueError):
    """schedule 写法无法解析或不合法。"""


def utc_now_naive() -> datetime:
    """当前 UTC naive 时间(next_fire_at / scheduled_for 的约定)。"""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_naive_to_shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(SHANGHAI)


def _aware_to_utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def parse_duration_minutes(text: str) -> int:
    match = _DURATION_RE.match(text.strip().lower())
    if not match:
        raise ScheduleSpecError(
            f"无法识别的时长写法: {text!r},请使用 30m / 2h / 1d"
        )
    value = int(match.group(1))
    if value <= 0:
        raise ScheduleSpecError("时长必须大于 0")
    return value * _DURATION_UNIT_MINUTES[match.group(2)[0]]


def _parse_cron(expr: str) -> dict:
    parts = expr.split()
    if len(parts) != 5:
        raise ScheduleSpecError("cron 表达式必须是五段:分 时 日 月 周")
    for part in parts:
        if not _CRON_FIELD_RE.match(part):
            raise ScheduleSpecError(
                f"cron 字段 {part!r} 只允许数字、* , - /(不支持英文名/宏)"
            )
    try:
        croniter(expr)
    except Exception as exc:
        raise ScheduleSpecError(f"cron 表达式无效: {expr!r}") from exc
    return {"kind": KIND_CRON, "expr": expr, "display": expr}


def _parse_once_timestamp(text: str, *, now_utc: datetime) -> dict:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleSpecError(f"时间戳无效: {text!r}") from exc
    # naive 时间戳按上海墙钟解释,避免依赖服务器时区。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    run_at_utc = _aware_to_utc_naive(parsed)
    tolerance = timedelta(seconds=_PAST_RUN_AT_TOLERANCE_SECONDS)
    if run_at_utc < now_utc - tolerance:
        raise ScheduleSpecError(f"指定时刻已经过去: {text!r}")
    return _once_spec(parsed)


def _once_spec(run_at_aware: datetime) -> dict:
    display = run_at_aware.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    return {
        "kind": KIND_ONCE,
        "run_at": run_at_aware.isoformat(),
        "display": f"单次 {display}",
    }


def parse_schedule(text: str, *, now_utc: datetime) -> dict:
    """把 schedule 字符串解析为结构化 spec。"""

    normalized = str(text or "").strip()
    if not normalized:
        raise ScheduleSpecError("schedule 不能为空")

    if normalized.lower().startswith("every "):
        minutes = parse_duration_minutes(normalized[6:])
        return {
            "kind": KIND_INTERVAL,
            "minutes": minutes,
            "display": f"每{minutes}分钟",
        }

    parts = normalized.split()
    if len(parts) == 5:
        return _parse_cron(normalized)

    if "T" in normalized or re.match(r"^\d{4}-\d{2}-\d{2}", normalized):
        return _parse_once_timestamp(normalized, now_utc=now_utc)

    minutes = parse_duration_minutes(normalized)
    run_at = _utc_naive_to_shanghai(now_utc) + timedelta(minutes=minutes)
    return _once_spec(run_at)


def schedule_fields(spec: dict) -> tuple[str, str, str]:
    """spec → (schedule_kind, schedule_spec JSON, cron_expr) 落库三元组。

    cron 种类同时回写 ``cron_expr`` 列,保持冻结快照与旧读方兼容。
    """

    kind = spec["kind"]
    spec_json = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    cron_expr = spec["expr"] if kind == KIND_CRON else ""
    return kind, spec_json, cron_expr


def spec_from_fields(
    schedule_kind: str | None,
    schedule_spec: str | None,
    cron_expr: str | None,
) -> dict | None:
    """从任务行字段还原 spec;老行(仅 cron_expr)自动回退。

    无法解析时返回 None,由调用方决定跳过并告警。
    """

    raw = str(schedule_spec or "").strip()
    if raw:
        try:
            spec = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(spec, dict) and spec.get("kind") in SCHEDULE_KINDS:
            return spec
        return None
    expr = str(cron_expr or "").strip()
    if not expr:
        return None
    try:
        return _parse_cron(expr)
    except ScheduleSpecError:
        return None


def schedule_display(spec: dict) -> str:
    return str(spec.get("display") or spec.get("expr") or spec["kind"])


def _cron_period_seconds(expr: str, *, base_utc: datetime) -> int:
    iterator = croniter(expr, _utc_naive_to_shanghai(base_utc))
    first = iterator.get_next(datetime)
    second = iterator.get_next(datetime)
    return max(int((second - first).total_seconds()), 60)


def grace_seconds(spec: dict, *, base_utc: datetime | None = None) -> int:
    """迟到多久以内仍补发;超过则快进(once 为禁用)。"""

    kind = spec["kind"]
    if kind == KIND_ONCE:
        return MAX_GRACE_SECONDS
    if kind == KIND_INTERVAL:
        period = int(spec["minutes"]) * 60
    else:
        anchor = base_utc or datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            period = _cron_period_seconds(spec["expr"], base_utc=anchor)
        except Exception:
            return MIN_GRACE_SECONDS
    return max(MIN_GRACE_SECONDS, min(period // 2, MAX_GRACE_SECONDS))


def once_run_at_utc(spec: dict) -> datetime | None:
    """once spec 的触发时刻(UTC naive);解析失败返回 None。"""

    try:
        parsed = datetime.fromisoformat(str(spec.get("run_at") or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return _aware_to_utc_naive(parsed)


def next_fire_after(
    spec: dict,
    *,
    base_utc: datetime,
    include_missed: bool = False,
) -> datetime | None:
    """严格晚于 ``base_utc`` 的下一次触发时刻(UTC naive)。

    ``include_missed=True`` 用于懒初始化:once 任务错过但仍在
    宽限窗口内时返回原定时刻,让到期检查补发。
    返回 None 表示不再有下一次(once 已触发或错过太久)。
    """

    kind = spec["kind"]
    if kind == KIND_ONCE:
        run_at = once_run_at_utc(spec)
        if run_at is None:
            return None
        cutoff = base_utc
        if include_missed:
            cutoff = base_utc - timedelta(seconds=MAX_GRACE_SECONDS)
        return run_at if run_at > cutoff else None
    if kind == KIND_INTERVAL:
        return base_utc + timedelta(minutes=int(spec["minutes"]))
    iterator = croniter(spec["expr"], _utc_naive_to_shanghai(base_utc))
    return _aware_to_utc_naive(iterator.get_next(datetime))


def initial_anchor(now_utc: datetime) -> datetime:
    """懒初始化基点:当前分钟槽起点前一秒,保证当前分钟可触发。"""

    return now_utc.replace(second=0, microsecond=0) - timedelta(seconds=1)


def cron_slot_matches(expr: str, slot_utc: datetime) -> bool:
    """判断 UTC naive 分钟槽是否匹配 cron 表达式(上海时区)。"""

    try:
        return bool(croniter.match(expr, _utc_naive_to_shanghai(slot_utc)))
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ResolvedScheduleFields:
    schedule_kind: str
    schedule_spec: str
    cron_expr: str
    next_fire_at: datetime
    display: str


def resolve_schedule_fields(
    *,
    schedule: str | None,
    cron_expr: str | None,
    now_utc: datetime,
) -> ResolvedScheduleFields:
    """工具与管理路由共用的创建/更新入口。

    ``schedule`` 优先;为空时接受旧参数 ``cron_expr``。
    """

    text = str(schedule or "").strip() or str(cron_expr or "").strip()
    spec = parse_schedule(text, now_utc=now_utc)
    kind, spec_json, expr = schedule_fields(spec)
    # interval 首次触发从当前时刻起算;cron/once 用分钟槽锚点,
    # 保证创建所在的当前分钟仍可触发(与旧调度器行为一致)。
    base = now_utc if kind == KIND_INTERVAL else initial_anchor(now_utc)
    next_fire = next_fire_after(
        spec,
        base_utc=base,
        include_missed=True,
    )
    if next_fire is None:
        raise ScheduleSpecError("该 schedule 不会再有下一次触发")
    return ResolvedScheduleFields(
        schedule_kind=kind,
        schedule_spec=spec_json,
        cron_expr=expr,
        next_fire_at=next_fire,
        display=schedule_display(spec),
    )


__all__ = [
    "KIND_CRON",
    "KIND_INTERVAL",
    "KIND_ONCE",
    "MAX_GRACE_SECONDS",
    "MIN_GRACE_SECONDS",
    "ResolvedScheduleFields",
    "SCHEDULE_KINDS",
    "ScheduleSpecError",
    "cron_slot_matches",
    "grace_seconds",
    "initial_anchor",
    "next_fire_after",
    "once_run_at_utc",
    "parse_duration_minutes",
    "parse_schedule",
    "resolve_schedule_fields",
    "schedule_display",
    "schedule_fields",
    "spec_from_fields",
    "utc_now_naive",
]
