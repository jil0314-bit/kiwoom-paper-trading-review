#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure V75.80 three-minute sell-exhaustion logic.

This module has no GUI, API, clock, broker, or file dependency.  It translates
the fifteen user-reviewed chart cases into one local-wave state machine and can
therefore be replayed with synthetic or captured bars without starting HeroMoon.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any, Iterable, Sequence


WATCH = "WATCH"
READY = "READY"
FIRST_BUY = "FIRST_BUY"
CONFIRM_BUY = "CONFIRM_BUY"
BLOCKED = "BLOCKED"

TAKE_PROFIT_PCT = 0.015
HARD_STOP_PCT = 0.05


@dataclass(frozen=True, slots=True)
class GemDecision:
    stage: str
    pattern: str
    score: float
    reason: str
    flush_index: int
    dry_index: int
    confirm_index: int
    support_low: float
    invalid_low: float
    flush_volume_ratio: float
    dry_volume_ratio: float
    range_compression: float
    ma5_slope_pct: float
    overhead_score: float
    target_room_pct: float
    risk_factor: float


@dataclass(frozen=True, slots=True)
class IntrabarProjection:
    eligible: bool
    stage: str
    score: float
    reason: str
    decision: GemDecision | None


def _n(bar: Any, name: str) -> float:
    try:
        value = float(getattr(bar, name))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _median(values: Iterable[float], default: float = 1.0) -> float:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return float(statistics.median(clean)) if clean else float(default)


def _metrics(bar: Any) -> tuple[float, float, float, float, float]:
    high = _n(bar, "high")
    low = _n(bar, "low")
    open_price = _n(bar, "open")
    close = _n(bar, "close")
    span = max(1e-9, high - low)
    signed = (close - open_price) / span
    body = abs(close - open_price) / span
    lower_wick = (min(open_price, close) - low) / span
    close_location = (close - low) / span
    return span, signed, body, lower_wick, close_location


def _minute_identity(bar: Any) -> tuple[int, int, int, int, int] | None:
    timestamp = getattr(bar, "timestamp", None)
    required = ("year", "month", "day", "hour", "minute")
    if timestamp is None or not all(hasattr(timestamp, name) for name in required):
        return None
    return (
        int(timestamp.year),
        int(timestamp.month),
        int(timestamp.day),
        int(timestamp.hour),
        int(timestamp.minute),
    )


def _bucket_identity(bar: Any) -> tuple[int, int, int, int, int] | None:
    key = _minute_identity(bar)
    if key is None:
        return None
    year, month, day, hour, minute = key
    return year, month, day, hour, (minute // 3) * 3


def _sma(values: Sequence[float], end: int, period: int) -> float:
    if end < 0:
        return 0.0
    start = max(0, end - period + 1)
    window = values[start : end + 1]
    return sum(window) / max(1, len(window))


def _ma_slope(values: Sequence[float], end: int, period: int = 5, lag: int = 2) -> float:
    current = _sma(values, end, period)
    previous = _sma(values, max(0, end - lag), period)
    return (current - previous) / max(0.01, previous)


def _falling_context(items: Sequence[Any], index: int) -> tuple[bool, float]:
    prior = list(items[max(0, index - 6) : index])
    if len(prior) < 3:
        return False, 0.0
    closes = [_n(item, "close") for item in items]
    close_drop = _n(prior[-1], "close") / max(0.01, _n(prior[0], "close")) - 1.0
    negative_steps = sum(
        _n(prior[pos], "close") < _n(prior[pos - 1], "close")
        for pos in range(1, len(prior))
    )
    old_lows = [_n(item, "low") for item in prior[: max(2, len(prior) // 2)]]
    new_lows = [_n(item, "low") for item in prior[max(2, len(prior) // 2) :]]
    lower_low = bool(old_lows and new_lows and min(new_lows) < min(old_lows) * 0.998)
    ma5_slope = _ma_slope(closes, index - 1, 5, 2)
    # This explicitly rejects the Dasco-like sideways high-volume bearish bar.
    falling = (
        close_drop <= -0.005
        or ma5_slope <= -0.0025
        or (negative_steps >= (2 if len(prior) <= 3 else 3) and lower_low)
    )
    return falling, ma5_slope


def _dominant_flush(items: Sequence[Any]) -> tuple[int, float] | None:
    """Find the latest meaningful bearish supply discharge inside a down-wave."""
    best: tuple[int, float, float, float] | None = None
    first = max(3, len(items) - 22)
    for index in range(first, len(items) - 1):
        current = items[index]
        span, signed, body, _lower, _location = _metrics(current)
        if signed >= -0.02 or body < 0.18:
            continue
        falling, _ = _falling_context(items, index)
        if not falling:
            continue
        prior = items[max(0, index - 7) : index]
        median_volume = max(1.0, _median(max(0.0, _n(item, "volume")) for item in prior))
        median_range = max(1e-9, _median(_metrics(item)[0] for item in prior))
        volume_ratio = max(0.0, _n(current, "volume")) / median_volume
        if volume_ratio < 1.22 or span < median_range * 0.72:
            continue
        low = _n(current, "low")
        quality = volume_ratio + min(1.5, span / median_range) + min(1.0, body)
        if best is None:
            best = index, volume_ratio, low, quality
            continue
        old_index, _old_ratio, old_low, old_quality = best
        # A later, larger new-low discharge invalidates the earlier exhaustion.
        later_reset = index > old_index and low <= old_low * 0.998 and (
            _n(current, "volume") >= _n(items[old_index], "volume") * 0.72
        )
        if later_reset or quality > old_quality * 1.22:
            best = index, volume_ratio, low, quality
    if best is not None:
        return best[0], best[1]

    # Early-session HLB-BioStep branch: several consecutive large bearish bars
    # may discharge supply while their volumes decline from one another, so no
    # single last bar is a local-volume spike.  The following dry bars provide
    # the relative baseline without using any future price outcome.
    for end in range(2, len(items) - 2):
        chain = list(items[max(0, end - 3) : end + 1])
        if len(chain) < 3:
            continue
        bearish = sum(_n(item, "close") < _n(item, "open") for item in chain)
        drop = _n(chain[-1], "close") / max(0.01, _n(chain[0], "open")) - 1.0
        if bearish < 3 or drop > -0.025:
            continue
        chain_volume = max(1.0, _median(_n(item, "volume") for item in chain))
        after = items[end + 1 : min(len(items), end + 4)]
        after_volume = max(1.0, _median(_n(item, "volume") for item in after))
        if after_volume > chain_volume * 0.48:
            continue
        return end, max(1.25, chain_volume / after_volume)
    return None


def _sideways_bear_trap(items: Sequence[Any]) -> tuple[int, float] | None:
    """Identify a large bearish bar that occurred without a falling wave.

    This is the reviewed Dasco false-positive.  Returning an explicit blocked
    state prevents a legacy detector from re-admitting the same bar merely
    because the new exhaustion detector did not find a valid down-wave.
    """
    first = max(3, len(items) - 14)
    for index in range(first, len(items) - 1):
        current = items[index]
        _span, signed, body, _lower, _location = _metrics(current)
        if signed >= -0.02 or body < 0.18:
            continue
        prior = items[max(0, index - 7) : index]
        median_volume = max(
            1.0, _median(max(0.0, _n(item, "volume")) for item in prior)
        )
        ratio = max(0.0, _n(current, "volume")) / median_volume
        falling, _slope = _falling_context(items, index)
        if ratio >= 1.50 and not falling:
            return index, ratio
    return None


def _overhead_congestion(
    items: Sequence[Any],
    entry_index: int,
    *,
    extra_resistance_levels: Sequence[float] = (),
) -> tuple[float, float]:
    """Return graded overhead score and nearest *strong* target room.

    This is an OHLC congestion proxy, not a replacement for HeroMoon's exact
    volume-at-price profile.  It deliberately grades risk instead of banning
    every nearby moving average.
    """
    entry = max(0.01, _n(items[entry_index], "close"))
    target_band = entry * 1.019
    history = list(items[max(0, entry_index - 24) : entry_index])
    local_volume = max(1.0, _median(max(0.0, _n(item, "volume")) for item in history))
    touches: list[Any] = []
    for item in history:
        if _n(item, "high") >= entry * 1.002 and _n(item, "low") <= target_band:
            touches.append(item)
    touch_density = min(1.0, len(touches) / 9.0)
    heavy = (
        _median((_n(item, "volume") for item in touches), 0.0) / local_volume
        if touches else 0.0
    )

    closes = [_n(item, "close") for item in items]
    falling_ma_count = 0
    for period in (10, 20):
        if len(closes) >= period + 2:
            value = _sma(closes, entry_index, period)
            slope = _ma_slope(closes, entry_index, period, 2)
            if entry < value <= target_band and slope < -0.0015:
                falling_ma_count += 1

    extra_density = min(
        1.0,
        sum(entry < float(level) <= target_band for level in extra_resistance_levels) / 3.0,
    )
    score = min(
        1.0,
        touch_density * 0.58
        + min(1.0, heavy / 1.35) * 0.22
        + falling_ma_count * 0.14
        + extra_density * 0.45,
    )
    strong_levels = [
        max(_n(item, "open"), _n(item, "close"))
        for item in touches
        if (
            _n(item, "volume") >= local_volume * 1.15
            and max(_n(item, "open"), _n(item, "close")) > entry
        )
    ]
    strong_levels.extend(float(level) for level in extra_resistance_levels if float(level) > entry)
    nearest = min(strong_levels, default=entry * 9.0)
    room = nearest / entry - 1.0
    return score, room


def evaluate_gem_three_minute(
    bars: Sequence[Any],
    *,
    resistance_levels: Sequence[float] = (),
) -> GemDecision | None:
    """Evaluate one completed/temporary 3-minute bar sequence.

    Hard gates implement the reviewed failures: falling context, real candle
    compression, lower-wick or repeated low defence, MA5 stabilisation for the
    ordinary branch, hollow-bounce rejection, repeated-flush reset, and graded
    target-space risk.
    """
    items = list(bars)
    if len(items) < 7:
        return None
    flush_result = _dominant_flush(items)
    if flush_result is None:
        sideways = _sideways_bear_trap(items)
        if sideways is not None:
            index, ratio = sideways
            closes = [_n(item, "close") for item in items]
            slope = _ma_slope(closes, len(items) - 1, 5, 2)
            low = _n(items[index], "low")
            return GemDecision(
                BLOCKED,
                "횡보대량음봉배제",
                0.0,
                f"국면차단=실제하락아님|대량음봉x{ratio:.2f}",
                index,
                -1,
                len(items) - 1,
                low,
                low,
                ratio,
                1.0,
                1.0,
                slope,
                0.0,
                0.0,
                0.55,
            )
        return None
    flush_index, flush_volume_ratio = flush_result
    if len(items) - flush_index > 12:
        return None

    flush = items[flush_index]
    flush_volume = max(1.0, _n(flush, "volume"))
    flush_range = max(1e-9, _metrics(flush)[0])
    pre = items[max(0, flush_index - 6) : flush_index]
    pre_range = max(1e-9, _median(_metrics(item)[0] for item in pre))
    pre_volume = max(1.0, _median(max(0.0, _n(item, "volume")) for item in pre))
    post = items[flush_index + 1 :]
    if not post:
        return None

    dry_indices: list[int] = []
    for index in range(flush_index + 1, len(items)):
        current = items[index]
        prior_local = items[max(flush_index, index - 5) : index]
        local_median = max(
            1.0,
            _median(max(0.0, _n(item, "volume")) for item in prior_local),
        )
        volume = max(0.0, _n(current, "volume"))
        span = _metrics(current)[0]
        if (
            volume <= flush_volume * 0.46
            and volume <= max(pre_volume, local_median) * 0.72
            and span <= max(flush_range * 0.72, pre_range * 0.85)
        ):
            dry_indices.append(index)

    current_index = len(items) - 1
    current = items[current_index]
    current_range, signed, body, lower_wick, close_location = _metrics(current)
    current_volume = max(0.0, _n(current, "volume"))
    current_close = max(0.01, _n(current, "close"))
    closes = [_n(item, "close") for item in items]
    ma5 = _sma(closes, current_index, 5)
    ma5_slope = _ma_slope(closes, current_index, 5, 2)
    # Compare the current candle against a low that already existed before it.
    # Using min(post), which includes current, would make every fresh new low
    # appear to have defended itself and could create the exact early-entry
    # error highlighted in the MakinaRocks review.
    prior_support_bars = post[:-1]
    support_low = min(
        [_n(item, "low") for item in prior_support_bars]
        or [_n(flush, "low")]
    )
    low_hold = _n(current, "low") >= support_low * 0.9975
    settle = current_close >= ma5 * 0.997
    ma_flat = ma5_slope >= -0.0015

    dry_index = dry_indices[-1] if dry_indices else -1
    dry_volume_ratio = (
        _n(items[dry_index], "volume") / flush_volume if dry_index >= 0 else 1.0
    )
    dry_range = _metrics(items[dry_index])[0] if dry_index >= 0 else current_range
    range_compression = dry_range / flush_range

    recent = items[max(flush_index + 1, current_index - 4) : current_index]
    recent_volume = max(1.0, _median(max(0.0, _n(item, "volume")) for item in recent))
    recent_range = max(1e-9, _median(_metrics(item)[0] for item in recent))
    price_jump = abs(current_close / max(0.01, _n(items[current_index - 1], "close")) - 1.0)
    hollow_bounce = (
        current_volume <= recent_volume * 0.20
        and (current_range >= recent_range * 1.18 or price_jump >= 0.006)
    )
    hollow_recent = False
    for index in range(max(flush_index + 1, current_index - 2), current_index + 1):
        before = items[max(flush_index + 1, index - 3) : index]
        if not before:
            continue
        before_volume = max(1.0, _median(_n(item, "volume") for item in before))
        before_range = max(1e-9, _median(_metrics(item)[0] for item in before))
        move = abs(
            _n(items[index], "close")
            / max(0.01, _n(items[index - 1], "close"))
            - 1.0
        )
        if (
            _n(items[index], "volume") <= before_volume * 0.20
            and (_metrics(items[index])[0] >= before_range * 1.18 or move >= 0.006)
        ):
            hollow_recent = True
            break

    later_sell_reset = any(
        index > flush_index
        and _n(items[index], "close") < _n(items[index], "open")
        and _n(items[index], "low") < _n(flush, "low") * 0.997
        and _n(items[index], "volume") >= flush_volume * 1.05
        for index in range(flush_index + 1, len(items))
    )

    flush_chain = sum(
        _n(items[index], "close") < _n(items[index], "open")
        and _n(items[index], "volume") >= pre_volume * 1.05
        for index in range(max(0, flush_index - 3), flush_index + 1)
    )
    wick_or_defence = lower_wick >= 0.15 or low_hold
    short_now = current_range <= max(flush_range * 0.70, pre_range * 0.78)

    # Retest branch: first rebound, then a quieter non-breaking visit to the low.
    post_high = max(_n(item, "high") for item in post[:-1]) if len(post) > 1 else support_low
    retest = (
        len(post) >= 3
        and post_high >= support_low * 1.006
        and _n(current, "low") <= support_low * 1.008
        and low_hold
        and current_volume <= flush_volume * 0.45
    )

    overhead_score, target_room_pct = _overhead_congestion(
        items, current_index, extra_resistance_levels=resistance_levels
    )
    hard_overhead = overhead_score >= 0.78 and target_room_pct < 0.017

    score = 36.0
    score += 15.0 if dry_index >= 0 else 0.0
    score += 12.0 if range_compression <= 0.62 else 4.0 if range_compression <= 0.78 else 0.0
    score += 9.0 if wick_or_defence else 0.0
    score += 9.0 if ma_flat else 0.0
    score += 8.0 if settle else 0.0
    score += 7.0 if signed >= 0.0 else 0.0
    score += 5.0 if flush_chain >= 2 else 0.0
    score += 5.0 if retest else 0.0
    score -= overhead_score * 13.0
    score = max(0.0, min(100.0, score))
    risk_factor = max(0.55, 1.0 - overhead_score * 0.38)

    base = (
        f"3분보석소진|대량음봉x{flush_volume_ratio:.2f}|거래바닥={dry_volume_ratio:.2f}"
        f"|봉압축={range_compression:.2f}|5봉기울기={ma5_slope:+.3%}"
        f"|상단매물={overhead_score:.2f}|점수={score:.1f}"
    )
    if later_sell_reset:
        return GemDecision(
            BLOCKED, "추가투매재설정", score, base + "|차단=더큰거래·신저가",
            flush_index, dry_index, current_index, support_low, support_low,
            flush_volume_ratio, dry_volume_ratio, range_compression, ma5_slope,
            overhead_score, target_room_pct, risk_factor,
        )
    if hollow_bounce or hollow_recent:
        return GemDecision(
            BLOCKED, "호가공백반등", score, base + "|차단=저거래긴반등",
            flush_index, dry_index, current_index, support_low, support_low,
            flush_volume_ratio, dry_volume_ratio, range_compression, ma5_slope,
            overhead_score, target_room_pct, min(risk_factor, 0.55),
        )
    if hard_overhead:
        return GemDecision(
            BLOCKED, "상단매물공간부족", score, base + "|차단=목표전강한저항",
            flush_index, dry_index, current_index, support_low, support_low,
            flush_volume_ratio, dry_volume_ratio, range_compression, ma5_slope,
            overhead_score, target_room_pct, min(risk_factor, 0.60),
        )

    preemptive = (
        dry_index >= 0
        and current_index - dry_index <= 2
        and signed >= -0.05
        and short_now
        and wick_or_defence
        and low_hold
        and (flush_chain >= 2 or lower_wick >= 0.18 or flush_volume_ratio >= 3.0)
        and dry_volume_ratio <= 0.35
        and score >= 78.0
    )
    confirmed = (
        dry_index >= 0
        and low_hold
        and ma_flat
        and settle
        and wick_or_defence
        and (
            short_now
            or (
                current_index > dry_index
                and current_range <= flush_range * 0.92
                and current_volume <= flush_volume * 0.72
            )
        )
        and (signed >= -0.10 or close_location >= 0.55)
        and score >= 74.0
    )

    if confirmed:
        pattern = "투매재시험형" if retest else "하락압축확인형"
        stage = CONFIRM_BUY
    elif preemptive:
        pattern = "연속매물방출선제형" if flush_chain >= 2 else "아래꼬리선제형"
        stage = FIRST_BUY
    elif dry_index >= 0 and (short_now or range_compression <= 0.72):
        pattern = "소진확인대기"
        stage = READY
    else:
        pattern = "하락매물방출관찰"
        stage = WATCH

    reason = base + f"|형태={pattern}|판정={stage}"
    return GemDecision(
        stage, pattern, score, reason, flush_index, dry_index, current_index,
        support_low, support_low, flush_volume_ratio, dry_volume_ratio,
        range_compression, ma5_slope, overhead_score, target_room_pct, risk_factor,
    )


def evaluate_intrabar_projection(
    completed_bars: Sequence[Any],
    partial_bar: Any,
    internal_one_minute_bars: Sequence[Any],
    *,
    elapsed_seconds: float,
    resistance_levels: Sequence[float] = (),
) -> IntrabarProjection:
    """Use one-minute internals only to time a projected three-minute entry."""
    elapsed = max(0.0, float(elapsed_seconds))
    if elapsed < 90.0:
        return IntrabarProjection(False, READY, 0.0, "부분3분봉_90초전대기", None)
    if elapsed >= 178.0:
        return IntrabarProjection(False, READY, 0.0, "부분3분봉_마감경계대기", None)

    incomplete = [
        item for item in list(completed_bars)[-12:]
        if hasattr(item, "source_complete")
        and not bool(getattr(item, "source_complete", False))
    ]
    if incomplete:
        return IntrabarProjection(
            False, BLOCKED, 0.0, "완성3분봉원천결측_선제매수차단", None
        )

    partial_bucket = _bucket_identity(partial_bar)
    unique: dict[tuple[int, int, int, int, int], Any] = {}
    for item in internal_one_minute_bars:
        key = _minute_identity(item)
        if key is None or _bucket_identity(item) != partial_bucket:
            continue
        unique[key] = item
    internals = sorted(unique.values(), key=lambda item: getattr(item, "timestamp", 0))
    if len(internals) < 2:
        return IntrabarProjection(False, READY, 0.0, "1분내부센서_서로다른2분미만", None)

    last = internals[-1]
    _span, last_signed, _body, _wick, last_location = _metrics(last)
    prior_volume = max(1.0, _median(_n(item, "volume") for item in internals[:-1]))
    late_collapse = (
        last_signed < -0.30
        and last_location < 0.30
        and _n(last, "volume") >= prior_volume * 1.35
    )
    if late_collapse:
        return IntrabarProjection(False, BLOCKED, 0.0, "마지막1분_매도재가속", None)

    decision = evaluate_gem_three_minute(
        [*completed_bars, partial_bar], resistance_levels=resistance_levels
    )
    if decision is None:
        return IntrabarProjection(False, WATCH, 0.0, "부분3분봉_하락소진구조없음", None)
    decision_span = list(completed_bars)[max(0, int(decision.flush_index) - 8) :]
    if any(
        hasattr(item, "source_complete")
        and not bool(getattr(item, "source_complete", False))
        for item in decision_span
    ):
        return IntrabarProjection(
            False,
            BLOCKED,
            decision.score,
            "완성3분봉판정기준선원천결측_선제매수차단",
            decision,
        )
    if decision.stage == BLOCKED:
        return IntrabarProjection(False, BLOCKED, decision.score, decision.reason, decision)

    first_low = max(0.01, _n(internals[0], "low"))
    internal_low_hold = _n(last, "low") >= first_low * 0.996
    current_span, current_signed, _current_body, current_wick, current_location = _metrics(partial_bar)
    completed_ranges = [_metrics(item)[0] for item in completed_bars[-5:]]
    range_ok = current_span <= max(1e-9, _median(completed_ranges)) * 0.82
    close_ok = current_signed >= -0.05 and current_location >= 0.48
    wick_ok = current_wick >= 0.12 or internal_low_hold
    eligible = (
        decision.stage in {FIRST_BUY, CONFIRM_BUY, READY}
        and decision.score >= 82.0
        and internal_low_hold
        and range_ok
        and close_ok
        and wick_ok
    )
    projected_stage = FIRST_BUY if eligible else READY
    reason = decision.reason + (
        "|부분봉선제진입가능" if eligible else "|부분봉조건지속확인중"
    )
    return IntrabarProjection(eligible, projected_stage, decision.score, reason, decision)


__all__ = [
    "BLOCKED", "CONFIRM_BUY", "FIRST_BUY", "HARD_STOP_PCT", "READY",
    "TAKE_PROFIT_PCT", "WATCH", "GemDecision", "IntrabarProjection",
    "evaluate_gem_three_minute", "evaluate_intrabar_projection",
]
