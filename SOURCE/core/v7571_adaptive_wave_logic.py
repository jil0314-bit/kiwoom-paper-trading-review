#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.71 adaptive wave / pullback / theme-leadership logic.

This module intentionally avoids fixed rules such as "1~10 bars vs 11~40 bars".
It detects price pivots with a volatility-adaptive reversal threshold and evaluates:

- initial impulse / middle expansion / late extension
- pullback depth and support preservation
- heavy bearish-volume absorption vs distribution
- rebound confirmation
- relative strength in weak markets
- theme leader / second leader priority
- active user sector guards and theme priorities

PAPER policy remains: no automatic stop, default target +1.5%.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _number(obj: Any, key: str, default: float = 0.0) -> float:
    value = obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class BarPoint:
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Pivot:
    index: int
    kind: str  # LOW / HIGH
    price: float


@dataclass(frozen=True)
class ThemeLeader:
    symbol: str
    rank: int
    score: float
    reason: str


@dataclass(frozen=True)
class StructuralDecision:
    stage: str
    score: float
    phase: str
    impulse_leg_count: int
    adaptive_reversal_pct: float
    volume_shift: float
    retrace_ratio: float
    sell_volume_ratio: float
    support_hold: bool
    absorption: bool
    rebound_confirmed: bool
    relative_strength: float
    theme_rank: int | None
    market_state: str
    size_factor: float
    order_allowed: bool
    reason: str
    missing: tuple[str, ...] = field(default_factory=tuple)


def normalize_bars(bars: Iterable[Any]) -> list[BarPoint]:
    result: list[BarPoint] = []
    for bar in bars:
        point = BarPoint(
            timestamp=(bar.get("timestamp") if isinstance(bar, Mapping) else getattr(bar, "timestamp", None)),
            open=_number(bar, "open"),
            high=_number(bar, "high"),
            low=_number(bar, "low"),
            close=_number(bar, "close"),
            volume=max(0.0, _number(bar, "volume")),
        )
        if min(point.open, point.high, point.low, point.close) <= 0:
            continue
        result.append(point)
    return result


def adaptive_reversal_threshold(bars: Sequence[BarPoint]) -> float:
    if len(bars) < 3:
        return 0.012
    true_ranges: list[float] = []
    for prev, current in zip(bars, bars[1:]):
        base = max(0.01, prev.close)
        tr = max(
            current.high - current.low,
            abs(current.high - prev.close),
            abs(current.low - prev.close),
        ) / base
        if math.isfinite(tr):
            true_ranges.append(tr)
    if not true_ranges:
        return 0.012
    recent = true_ranges[-min(40, len(true_ranges)):]
    # 1.8× median true range, bounded to suppress micro-noise and over-wide swings.
    return clamp(statistics.median(recent) * 1.8, 0.006, 0.04)


def adaptive_pivots(bars: Sequence[BarPoint], threshold: float | None = None) -> list[Pivot]:
    if len(bars) < 3:
        return []
    threshold = adaptive_reversal_threshold(bars) if threshold is None else float(threshold)
    pivots: list[Pivot] = []

    # Establish the first direction from both a running low and running high.
    direction = 0
    running_low_price = bars[0].close
    running_low_index = 0
    running_high_price = bars[0].close
    running_high_index = 0
    extreme_price = bars[0].close
    extreme_index = 0

    for index in range(1, len(bars)):
        close = bars[index].close

        if direction == 0:
            if close < running_low_price:
                running_low_price, running_low_index = close, index
            if close > running_high_price:
                running_high_price, running_high_index = close, index

            rise = close / max(0.01, running_low_price) - 1.0
            fall = close / max(0.01, running_high_price) - 1.0
            if rise >= threshold:
                pivots.append(Pivot(running_low_index, "LOW", running_low_price))
                direction = 1
                extreme_price, extreme_index = close, index
            elif fall <= -threshold:
                pivots.append(Pivot(running_high_index, "HIGH", running_high_price))
                direction = -1
                extreme_price, extreme_index = close, index
            continue

        if direction > 0:
            if close >= extreme_price:
                extreme_price, extreme_index = close, index
            drawdown = close / max(0.01, extreme_price) - 1.0
            if drawdown <= -threshold:
                pivots.append(Pivot(extreme_index, "HIGH", extreme_price))
                direction = -1
                extreme_price, extreme_index = close, index
        else:
            if close <= extreme_price:
                extreme_price, extreme_index = close, index
            rebound = close / max(0.01, extreme_price) - 1.0
            if rebound >= threshold:
                pivots.append(Pivot(extreme_index, "LOW", extreme_price))
                direction = 1
                extreme_price, extreme_index = close, index

    final_kind = "HIGH" if direction >= 0 else "LOW"
    final = Pivot(extreme_index, final_kind, extreme_price)
    if not pivots or pivots[-1].index != final.index:
        pivots.append(final)

    compact: list[Pivot] = []
    for pivot in pivots:
        if compact and compact[-1].kind == pivot.kind:
            old = compact[-1]
            better = pivot.price > old.price if pivot.kind == "HIGH" else pivot.price < old.price
            if better:
                compact[-1] = pivot
        else:
            compact.append(pivot)
    return compact


def _median_volume(items: Sequence[BarPoint]) -> float:
    values = [max(0.0, bar.volume) for bar in items if bar.volume >= 0]
    return statistics.median(values) if values else 0.0


def _volume_regime_shift(
    bars: Sequence[BarPoint],
    low_index: int,
    high_index: int,
) -> tuple[float, int, float, float]:
    """Detect a sustained volume changepoint inside an impulse.

    This avoids averaging a long quiet base together with the actual inflow.
    Candidate windows are determined by the detected impulse length, not fixed
    absolute bar numbers.
    """
    if high_index <= low_index:
        return 0.0, low_index, 0.0, 0.0

    impulse_length = high_index - low_index + 1
    context_length = max(6, impulse_length)
    context_start = max(0, low_index - context_length)
    external_context = list(bars[context_start:low_index])

    best_ratio = 0.0
    best_start = low_index
    best_before = max(1.0, _median_volume(external_context))
    best_after = max(1.0, _median_volume(bars[low_index:high_index + 1]))

    min_post = max(3, math.ceil(impulse_length * 0.25))
    min_pre = max(3, math.ceil(impulse_length * 0.20))
    first_split = low_index + min_pre
    last_split = high_index - min_post + 1

    for split in range(first_split, last_split + 1):
        before = external_context + list(bars[low_index:split])
        after = list(bars[split:high_index + 1])
        if len(before) < 3 or len(after) < min_post:
            continue
        before_median = max(1.0, _median_volume(before))
        after_median = max(1.0, _median_volume(after))
        ratio = after_median / before_median
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = split
            best_before = before_median
            best_after = after_median

    # Fallback for a uniformly active impulse.
    if best_ratio <= 0:
        before = external_context or list(bars[max(0, low_index - 6):low_index])
        best_before = max(1.0, _median_volume(before))
        best_after = max(1.0, _median_volume(bars[low_index:high_index + 1]))
        best_ratio = best_after / best_before
    return best_ratio, best_start, best_before, best_after


def _latest_impulse(
    pivots: Sequence[Pivot],
    bar_count: int,
) -> tuple[Pivot, Pivot, int] | None:
    pairs: list[tuple[Pivot, Pivot]] = []
    for left, right in zip(pivots, pivots[1:]):
        if left.kind == "LOW" and right.kind == "HIGH" and right.index > left.index:
            pairs.append((left, right))
    if not pairs:
        return None

    # Prefer the latest completed impulse that already has a measurable pullback.
    completed = [pair for pair in pairs if bar_count - pair[1].index - 1 >= 2]
    selected = completed[-1] if completed else pairs[-1]
    low, high = selected
    selected_position = pairs.index(selected) + 1
    return low, high, selected_position


def rank_theme_leaders(rows: Iterable[Mapping[str, Any]]) -> dict[str, ThemeLeader]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            continue
        normalized.append({
            "symbol": symbol,
            "return": float(row.get("change_pct", row.get("return_pct", 0.0)) or 0.0),
            "turnover": float(row.get("turnover", row.get("trading_value", 0.0)) or 0.0),
            "volume_ratio": float(row.get("volume_ratio", 0.0) or 0.0),
            "strength": float(row.get("execution_strength", row.get("trade_strength", 0.0)) or 0.0),
            "lead_seconds": float(row.get("lead_seconds", 0.0) or 0.0),
        })
    if not normalized:
        return {}

    def percentile(field: str, value: float) -> float:
        ordered = sorted(float(row[field]) for row in normalized)
        if len(ordered) == 1:
            return 1.0
        position = sum(v <= value for v in ordered) - 1
        return position / max(1, len(ordered) - 1)

    scored: list[tuple[str, float, str]] = []
    median_turnover = max(1.0, statistics.median(row["turnover"] for row in normalized))
    for row in normalized:
        p_return = percentile("return", row["return"])
        p_turnover = percentile("turnover", row["turnover"])
        p_volume = percentile("volume_ratio", row["volume_ratio"])
        p_strength = percentile("strength", row["strength"])
        # Earlier leadership gets a small advantage. Missing values are neutral.
        lead_bonus = clamp(row["lead_seconds"] / 900.0, 0.0, 1.0)
        score = (
            p_return * 0.28
            + p_turnover * 0.32
            + p_volume * 0.16
            + p_strength * 0.19
            + lead_bonus * 0.05
        ) * 100.0

        # A thinly traded spike cannot be called the true theme leader.
        liquidity_ratio = row["turnover"] / median_turnover
        if liquidity_ratio < 0.25:
            score -= 28.0
        elif liquidity_ratio < 0.50:
            score -= 14.0

        reason = (
            f"상승률P{p_return:.0%}·거래대금P{p_turnover:.0%}·"
            f"거래량P{p_volume:.0%}·체결P{p_strength:.0%}"
        )
        if liquidity_ratio < 0.50:
            reason += "·유동성감점"
        scored.append((row["symbol"], score, reason))

    scored.sort(key=lambda item: item[1], reverse=True)
    return {
        symbol: ThemeLeader(symbol, rank, score, reason)
        for rank, (symbol, score, reason) in enumerate(scored, start=1)
    }


def market_size_factor(market_state: str) -> float:
    state = str(market_state or "DATA_HALT").upper()
    return {
        "NORMAL": 1.00,
        "RECOVERY_READY": 0.85,
        "CAUTION": 0.70,
        "RECOVERY_WAIT": 0.55,
        "SHOCK": 0.40,
        "DATA_HALT": 0.00,
    }.get(state, 0.00)


def evaluate_structure(
    raw_bars: Sequence[Any],
    *,
    market_state: str = "NORMAL",
    market_return: float = 0.0,
    theme_rank: int | None = None,
    theme_size: int | None = None,
    theme_priority: bool = False,
    sector_guard: bool = False,
) -> StructuralDecision:
    bars = normalize_bars(raw_bars)
    state = str(market_state or "DATA_HALT").upper()
    size_factor = market_size_factor(state)

    if len(bars) < 8:
        return StructuralDecision(
            stage="관찰후보", score=0.0, phase="자료부족", impulse_leg_count=0,
            adaptive_reversal_pct=adaptive_reversal_threshold(bars),
            volume_shift=0.0, retrace_ratio=0.0, sell_volume_ratio=0.0,
            support_hold=False, absorption=False, rebound_confirmed=False,
            relative_strength=0.0, theme_rank=theme_rank, market_state=state,
            size_factor=size_factor, order_allowed=False,
            reason="분봉 자료가 부족해 구조 판정을 보류합니다.",
            missing=("분봉자료부족",),
        )

    threshold = adaptive_reversal_threshold(bars)
    pivots = adaptive_pivots(bars, threshold)
    impulse = _latest_impulse(pivots, len(bars))
    if impulse is None:
        return StructuralDecision(
            stage="관찰후보", score=20.0, phase="수급탐색", impulse_leg_count=0,
            adaptive_reversal_pct=threshold, volume_shift=0.0, retrace_ratio=0.0,
            sell_volume_ratio=0.0, support_hold=False, absorption=False,
            rebound_confirmed=False, relative_strength=0.0, theme_rank=theme_rank,
            market_state=state, size_factor=size_factor, order_allowed=False,
            reason="의미 있는 상승 파동이 아직 완성되지 않았습니다.",
            missing=("상승파동대기",),
        )

    low_pivot, high_pivot, leg_count = impulse
    impulse_bars = bars[low_pivot.index:high_pivot.index + 1]
    pullback_bars = bars[high_pivot.index + 1:]
    volume_shift, surge_start, baseline_volume, impulse_volume = _volume_regime_shift(
        bars, low_pivot.index, high_pivot.index
    )
    active_impulse_bars = bars[surge_start:high_pivot.index + 1] or impulse_bars

    impulse_range = max(0.01, high_pivot.price - low_pivot.price)
    pullback_low = min((bar.low for bar in pullback_bars), default=bars[-1].low)
    retrace = clamp((high_pivot.price - pullback_low) / impulse_range, 0.0, 2.0)

    up_volumes = [bar.volume for bar in active_impulse_bars if bar.close >= bar.open]
    down_volumes = [bar.volume for bar in pullback_bars if bar.close < bar.open]
    impulse_up_volume = statistics.median(up_volumes) if up_volumes else impulse_volume
    sell_volume = statistics.median(down_volumes) if down_volumes else 0.0
    sell_volume_ratio = sell_volume / max(1.0, impulse_up_volume)

    first_pullback = leg_count <= 1
    support_limit = 0.72 if first_pullback else 0.62
    support_hold = (
        pullback_low > low_pivot.price * (1.0 + threshold * 0.35)
        and retrace <= support_limit
    )

    latest = bars[-1]
    previous = bars[-2]
    rebound_from_low = (latest.close - pullback_low) / max(0.01, high_pivot.price - pullback_low)
    close_location = (latest.close - latest.low) / max(0.01, latest.high - latest.low)
    rebound_confirmed = (
        (latest.close > latest.open and close_location >= 0.60)
        or (latest.close > previous.close and previous.close > pullback_low)
    )

    recent_lows = [bar.low for bar in pullback_bars[-3:]]
    higher_low = len(recent_lows) >= 2 and recent_lows[-1] >= min(recent_lows[:-1])
    absorption = (
        support_hold
        and sell_volume_ratio >= 0.75
        and (rebound_from_low >= 0.28 or higher_low)
    )

    stock_return = latest.close / max(0.01, low_pivot.price) - 1.0
    relative_strength = stock_return - float(market_return)

    if leg_count <= 1:
        phase = "초기파동·첫조정"
        phase_score = 25.0
    elif leg_count == 2:
        phase = "중기확장·조정"
        phase_score = 18.0
    else:
        phase = "후기연장·주의"
        phase_score = 6.0

    score = phase_score
    score += clamp((volume_shift - 1.0) * 10.0, 0.0, 20.0)
    score += 15.0 if support_hold else -16.0
    score += 12.0 if absorption else 0.0
    score += 10.0 if rebound_confirmed else 0.0
    score += clamp(relative_strength * 250.0, -10.0, 10.0)

    if theme_rank == 1:
        score += 10.0
    elif theme_rank == 2:
        score += 7.0
    elif theme_rank is not None and theme_size and theme_rank <= max(3, math.ceil(theme_size * 0.35)):
        score += 2.0
    elif theme_rank is not None and theme_rank > 2:
        score -= 4.0

    if theme_priority:
        score += 6.0
    if sector_guard:
        score -= 22.0

    if retrace > 0.85:
        score -= 15.0
    if leg_count >= 3:
        score -= 10.0
    overextension = latest.close / max(0.01, low_pivot.price) - 1.0
    if overextension > 0.18 and leg_count >= 2:
        score -= 8.0

    score = clamp(score, 0.0, 100.0)

    candidate_threshold = {
        "NORMAL": 70.0, "RECOVERY_READY": 74.0,
        "CAUTION": 78.0, "RECOVERY_WAIT": 82.0,
        "SHOCK": 86.0, "DATA_HALT": 101.0,
    }.get(state, 101.0)
    direct_threshold = {
        "NORMAL": 85.0, "RECOVERY_READY": 87.0,
        "CAUTION": 90.0, "RECOVERY_WAIT": 92.0,
        "SHOCK": 94.0, "DATA_HALT": 101.0,
    }.get(state, 101.0)

    missing: list[str] = []
    if volume_shift < 1.20:
        missing.append("신규수급부족")
    if not support_hold:
        missing.append("지지확인대기")
    if sell_volume_ratio >= 0.75 and not absorption:
        missing.append("흡수/분배판별대기")
    if not rebound_confirmed:
        missing.append("반전봉대기")
    if leg_count >= 3:
        missing.append("후기파동감점")
    if sector_guard:
        missing.append("사용자섹터제한")

    if state == "DATA_HALT":
        stage = "시장보류"
    elif sector_guard:
        stage = "시장보류"
    elif score >= direct_threshold and support_hold and rebound_confirmed:
        stage = "매수직전"
    elif score >= candidate_threshold and support_hold:
        stage = "매수가능성"
    elif score >= 58.0:
        stage = "관심종목"
    else:
        stage = "관찰후보"

    order_allowed = (
        stage == "매수직전"
        and size_factor > 0
        and support_hold
        and rebound_confirmed
        and not sector_guard
    )

    theme_text = ""
    if theme_rank == 1:
        theme_text = " · 테마대장1"
    elif theme_rank == 2:
        theme_text = " · 테마대장2"
    elif theme_rank is not None:
        theme_text = f" · 테마{theme_rank}위"

    absorption_text = "매도흡수" if absorption else "흡수미확정"
    reason = (
        f"{phase} · 수급전환 {volume_shift:.2f}배 · "
        f"조정 {retrace:.0%} · {absorption_text} · "
        f"상대강도 {relative_strength:+.1%}{theme_text} · 점수 {score:.0f}"
    )

    return StructuralDecision(
        stage=stage,
        score=score,
        phase=phase,
        impulse_leg_count=leg_count,
        adaptive_reversal_pct=threshold,
        volume_shift=volume_shift,
        retrace_ratio=retrace,
        sell_volume_ratio=sell_volume_ratio,
        support_hold=support_hold,
        absorption=absorption,
        rebound_confirmed=rebound_confirmed,
        relative_strength=relative_strength,
        theme_rank=theme_rank,
        market_state=state,
        size_factor=size_factor,
        order_allowed=order_allowed,
        reason=reason,
        missing=tuple(dict.fromkeys(missing)),
    )
