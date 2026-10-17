#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V76.00 pure one-minute, three-tranche decision rules.

The functions in this file have no broker, GUI, network, Windows or filesystem
side effects.  They deliberately separate the user's *entry timing* from the
older three-minute context engine so boundary tests can attack the timing
rules without starting the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from statistics import mean, median
from typing import Any, Sequence


SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
PREMARKET_OPEN = time(8, 0)
PREMARKET_CLOSE = time(8, 50)
DRY_HALF_RATIO = 0.50
DRY_FLOOR_RATIO = 0.35
SHORT_RANGE_RATIO = 0.65
SUPPORT_BREAK_TOLERANCE = 0.003
MARKET_CRASH_BAR = -0.0035
MARKET_GAP_DECLINE = -0.015
MARKET_MA5_FLAT_TOLERANCE = -0.0003
MARKET_PERSISTENT_DROP = -0.008
MARKET_PERSISTENT_MA5_SLOPE = -0.0015
PROMINENT_BEAR_MEDIAN_RATIO = 1.80
PROMINENT_BEAR_MAX_RATIO = 1.20
PREVIOUS_BEAR_VOLUME_GROWTH = 1.30
MEANINGFUL_BEAR_BASELINE_RATIO = 0.25
TOP_DISTRIBUTION_VOLUME_RATIO = 1.50
TOP_DISTRIBUTION_CLOSE_LOCATION = 0.35
STRUCTURAL_RECLAIM_MA5_TOLERANCE = -0.0005
MIN_REAL_PULLBACK = 0.015
VOLUME_FLOOR_VS_FLUSH = 0.20
VOLUME_FLOOR_VS_CONTEXT = 0.55
FIRST_IMPULSE_RISE = 0.05
MIN_IMPULSE_RETRACEMENT = 0.30
STRONG_LEADER_RISE = 0.10
STRONG_LEADER_RETRACEMENT = 0.20
STRONG_LEADER_LIQUIDITY_MULTIPLIER = 2.0
STRONG_LEADER_MAX_PEAK_AGE_BARS = 20
MAX_INTEREST_PEAK_AGE_BARS = 60
PULLBACK_EVIDENCE_WINDOW_BARS = 30
STALE_IMPULSE_MINUTES = 60
STALE_IMPULSE_REBURST_RATIO = 1.50
SHORT_BODY_RATIO = 0.35
LONG_LOWER_WICK_BODY_RATIO = 1.50
LONG_LOWER_WICK_RANGE_RATIO = 0.35
FIRST_HALF_MAX_VS_FLUSH = 0.60
BREAKOUT_LEFT_PEAK_RATIO = 1.05
BREAKOUT_CONTEXT_RATIO = 1.80
BREAKOUT_PULLBACK_MAX_VOLUME = 0.65
BREAKOUT_CONFIRM_VS_BREAKOUT = 1.25
BREAKOUT_CONFIRM_VS_PULLBACK = 1.50
SECOND_HALF_MA5_SLOPE_MIN = -0.003
SECOND_HALF_MAX_BARS = 20
SECOND_HALF_VOLUME_RECOVERY = 1.10
SECOND_HALF_MAX_BULL_GAIN = 0.02


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    score: float = 0.0
    reference_price: float = 0.0
    trigger_index: int = -1


@dataclass(frozen=True)
class VirtualExit:
    """Read-only replay result; it never represents a broker order or fill."""

    exited: bool
    reason: str
    timestamp: datetime | None = None
    price: float = 0.0
    pnl_pct: float = 0.0


def _stamp(bar: Any) -> datetime:
    value = getattr(bar, "timestamp")
    if not isinstance(value, datetime):
        raise TypeError("bar.timestamp must be datetime")
    return value


def _shape(bar: Any) -> tuple[float, float, float, float, float, float]:
    open_ = float(getattr(bar, "open", 0.0) or 0.0)
    high = float(getattr(bar, "high", 0.0) or 0.0)
    low = float(getattr(bar, "low", 0.0) or 0.0)
    close = float(getattr(bar, "close", 0.0) or 0.0)
    volume = float(getattr(bar, "volume", 0.0) or 0.0)
    return open_, high, low, close, volume, max(0.0, high - low)


def in_regular_session(value: datetime) -> bool:
    return SESSION_OPEN <= value.time() <= SESSION_CLOSE


def in_pattern_session(value: datetime) -> bool:
    """The PAPER pattern may observe NXT pre-market and the KRX regular session."""
    clock = value.time()
    return (
        PREMARKET_OPEN <= clock < PREMARKET_CLOSE
        or SESSION_OPEN <= clock <= SESSION_CLOSE
    )


def today_bars(bars: Sequence[Any], now: datetime) -> list[Any]:
    return sorted(
        (
            bar for bar in bars
            if _stamp(bar).date() == now.date()
            and in_regular_session(_stamp(bar))
        ),
        key=_stamp,
    )


def pattern_bars(bars: Sequence[Any], now: datetime) -> list[Any]:
    return sorted(
        (
            bar for bar in bars
            if _stamp(bar).date() == now.date()
            and in_pattern_session(_stamp(bar))
        ),
        key=_stamp,
    )


def interest_session_bars(bars: Sequence[Any], now: datetime) -> list[Any]:
    """Keep pre-market and regular-session impulse cycles independent.

    A pre-market rise may remain visible on the dashboard, but it must not make
    a small regular-session fluctuation look like a fresh +5% pullback cycle.
    """

    sample = pattern_bars(bars, now)
    if in_regular_session(now):
        return [bar for bar in sample if in_regular_session(_stamp(bar))]
    return [
        bar
        for bar in sample
        if PREMARKET_OPEN <= _stamp(bar).time() < PREMARKET_CLOSE
    ]


def _bar_is_bearish(bar: Any) -> bool:
    open_, _high, _low, close, volume, _span = _shape(bar)
    return close < open_ and volume > 0


def _same_correction_bearish_before(
    today: Sequence[Any],
    *,
    current_index: int,
    peak_index: int,
) -> list[Any]:
    """Return bearish bars from the current impulse's correction only."""

    return [
        bar
        for bar in today[max(0, peak_index + 1):current_index]
        if _bar_is_bearish(bar)
    ]


def _meaningful_previous_bear(
    today: Sequence[Any],
    *,
    current_index: int,
    peak_index: int,
) -> tuple[Any | None, float]:
    """Reject a tiny print as the denominator of a large volume multiple."""

    prior_bears = _same_correction_bearish_before(
        today,
        current_index=current_index,
        peak_index=peak_index,
    )
    if not prior_bears:
        return None, 0.0
    correction_volumes = [
        _shape(bar)[4]
        for bar in today[max(0, peak_index + 1):current_index]
        if _shape(bar)[4] > 0
    ]
    if not correction_volumes:
        return None, 0.0
    normal_volume = median(correction_volumes)
    minimum_meaningful = max(1.0, normal_volume * MEANINGFUL_BEAR_BASELINE_RATIO)
    meaningful = [bar for bar in prior_bears if _shape(bar)[4] >= minimum_meaningful]
    if not meaningful:
        return None, minimum_meaningful
    return meaningful[-1], minimum_meaningful


def structural_distribution_risk(
    flush: Any,
    bars: Sequence[Any],
    *,
    now: datetime,
    session_bars: Sequence[Any] | None = None,
) -> Decision:
    """Detect a new-high distribution bar or a long support-breaking bear.

    These candles may remain interesting, but they are not rebound-entry
    permission.  A later MA5/support reclaim is required.
    """

    today = list(session_bars) if session_bars is not None else pattern_bars(bars, now)
    flush_stamp = _stamp(flush)
    flush_index = next(
        (index for index, bar in enumerate(today) if _stamp(bar) == flush_stamp),
        -1,
    )
    if flush_index < 1:
        return Decision(False, "STRUCTURAL_RISK_NOT_FOUND")
    open_, high, low, close, volume, span = _shape(today[flush_index])
    if not (close < open_ and span > 0 and volume > 0):
        return Decision(False, "STRUCTURAL_RISK_NOT_BEARISH")
    prior = today[max(0, flush_index - 20):flush_index]
    prior_high = max((_shape(bar)[1] for bar in prior), default=0.0)
    prior_lows = [_shape(bar)[2] for bar in prior[-6:] if _shape(bar)[2] > 0]
    prior_volumes = [_shape(bar)[4] for bar in prior[-5:] if _shape(bar)[4] > 0]
    close_location = (close - low) / span
    body_ratio = (open_ - close) / span
    volume_ratio = volume / max(1.0, median(prior_volumes)) if prior_volumes else 0.0
    at_new_high = prior_high > 0 and high >= prior_high * 0.997
    top_distribution = (
        at_new_high
        and close_location <= TOP_DISTRIBUTION_CLOSE_LOCATION
        and volume_ratio >= TOP_DISTRIBUTION_VOLUME_RATIO
    )
    prior_support = min(prior_lows) if prior_lows else 0.0
    support_break = (
        prior_support > 0
        and close < prior_support * (1.0 - SUPPORT_BREAK_TOLERANCE)
        and body_ratio >= 0.55
    )
    # Three or more bars pinned to the same intraday ceiling followed by a
    # heavy bearish failure is treated as an upper-limit/top failure.
    ceiling_bars = [
        bar for bar in prior[-6:]
        if prior_high > 0
        and _shape(bar)[1] >= prior_high * 0.999
        and _shape(bar)[3] >= prior_high * 0.997
    ]
    ceiling_failure = len(ceiling_bars) >= 3 and top_distribution
    if ceiling_failure:
        return Decision(True, "UPPER_LIMIT_OR_CEILING_FAILURE", 160.0, (open_ + close) / 2.0, flush_index)
    if top_distribution:
        return Decision(True, "NEW_HIGH_TOP_DISTRIBUTION", 150.0, (open_ + close) / 2.0, flush_index)
    if support_break:
        return Decision(True, "LONG_BEAR_SUPPORT_BREAK", 145.0, (open_ + close) / 2.0, flush_index)
    return Decision(False, "STRUCTURAL_RISK_NOT_FOUND")


def structural_reclaim_confirmed(
    risk: Decision,
    bars: Sequence[Any],
    *,
    now: datetime,
    session_bars: Sequence[Any] | None = None,
) -> bool:
    """Require a flat/rising MA5 plus midpoint and descending-high reclaim."""

    if not risk.allowed:
        return True
    today = list(session_bars) if session_bars is not None else pattern_bars(bars, now)
    if len(today) < 6 or risk.trigger_index < 0 or risk.trigger_index >= len(today) - 1:
        return False
    closes = [_shape(bar)[3] for bar in today]
    current_ma5 = mean(closes[-5:])
    previous_ma5 = mean(closes[-6:-1])
    current_close = closes[-1]
    previous_highs = [_shape(bar)[1] for bar in today[max(risk.trigger_index + 1, len(today) - 4):-1]]
    high_reclaim = bool(previous_highs) and current_close >= max(previous_highs)
    return (
        current_ma5 >= previous_ma5 * (1.0 + STRUCTURAL_RECLAIM_MA5_TOLERANCE)
        and current_close >= risk.reference_price
        and high_reclaim
    )


def virtual_exit_replay(
    bars: Sequence[Any],
    *,
    entry_time: datetime,
    entry_price: float,
    target_price: float,
    stop_loss_pct: float = 0.03,
) -> VirtualExit:
    """Replay the first target/stop touch after a read-only virtual entry.

    When both levels are touched in the same one-minute candle, intrabar order
    is unknowable from OHLC alone, so the result is explicitly unverified.
    """

    if entry_price <= 0 or target_price <= 0:
        return VirtualExit(False, "VIRTUAL_EXIT_INVALID_PRICE")
    stop_price = entry_price * (1.0 - stop_loss_pct)
    later = sorted(
        (bar for bar in bars if _stamp(bar) > entry_time),
        key=_stamp,
    )
    for bar in later:
        _open, high, low, _close, _volume, _span = _shape(bar)
        target_hit = high >= target_price
        stop_hit = low <= stop_price
        if target_hit and stop_hit:
            return VirtualExit(
                False,
                "VIRTUAL_EXIT_SAME_BAR_ORDER_UNVERIFIED",
                _stamp(bar),
            )
        if target_hit:
            return VirtualExit(
                True,
                "VIRTUAL_TARGET_FIRST",
                _stamp(bar),
                target_price,
                target_price / entry_price - 1.0,
            )
        if stop_hit:
            return VirtualExit(
                True,
                "VIRTUAL_STOP_FIRST",
                _stamp(bar),
                stop_price,
                -stop_loss_pct,
            )
    return VirtualExit(False, "VIRTUAL_EXIT_NOT_REACHED")


def active_interest_pullback_decision(
    bars: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Validate the pullback on the *current* completed bearish candle.

    Historical correction lows are not reusable for a later interest
    admission.  The trigger candle itself must reach both the absolute
    high-to-low decline and the impulse-retracement threshold.  A tightly
    bounded strong-leader branch permits a 20% retracement, but never waives
    the 1.5% absolute decline, liquidity, freshness or structure guards.
    """

    today = interest_session_bars(bars, now)
    if len(today) < 4:
        return Decision(False, "ACTIVE_PULLBACK_BARS_LT_4")
    if not price_normalized_liquidity_ok(today, now=now):
        return Decision(False, "ACTIVE_PULLBACK_INSUFFICIENT_ABSOLUTE_LIQUIDITY")

    current = today[-1]
    open_, _high, low, close, volume, _span = _shape(current)
    previous_close = _shape(today[-2])[3]
    if not (open_ > 0 and low > 0 and close < open_ and close <= previous_close and volume > 0):
        return Decision(False, "ACTIVE_PULLBACK_TRIGGER_NOT_FALLING_BEARISH")

    session_open = _shape(today[0])[0]
    if session_open <= 0 or close < session_open * 0.985:
        return Decision(False, "ACTIVE_PULLBACK_CHRONIC_DOWN_TREND")

    structural = structural_distribution_risk(current, today, now=now)
    if structural.allowed:
        return Decision(False, f"ACTIVE_PULLBACK_{structural.reason}")

    traded_value = sum(_shape(bar)[3] * _shape(bar)[4] for bar in today)
    candidates: list[tuple[int, float, float, float, bool]] = []
    for peak_index in range(1, len(today) - 1):
        peak_price = _shape(today[peak_index])[1]
        base_start = max(0, peak_index - 20)
        prior_local_high = max(
            (_shape(bar)[1] for bar in today[base_start:peak_index]),
            default=0.0,
        )
        if prior_local_high > 0 and peak_price < prior_local_high:
            continue
        base_price = min(_shape(bar)[2] for bar in today[base_start:peak_index + 1])
        if base_price <= 0 or peak_price <= base_price:
            continue
        impulse_rise = peak_price / base_price - 1.0
        if impulse_rise + 1e-12 < FIRST_IMPULSE_RISE:
            continue

        peak_age = len(today) - 1 - peak_index
        absolute_decline = (peak_price - low) / peak_price
        impulse_retracement = (peak_price - low) / (peak_price - base_price)
        standard = (
            absolute_decline + 1e-12 >= MIN_REAL_PULLBACK
            and impulse_retracement + 1e-12 >= MIN_IMPULSE_RETRACEMENT
        )

        prior_bear_volumes = [
            _shape(bar)[4]
            for bar in today[peak_index + 1:-1]
            if _bar_is_bearish(bar)
        ][-6:]
        prominent_bear = (
            len(prior_bear_volumes) >= 2
            and volume + 1e-12 >= median(prior_bear_volumes) * PROMINENT_BEAR_MEDIAN_RATIO
        )
        ma20 = mean(_shape(bar)[3] for bar in today[-20:]) if len(today) >= 20 else 0.0
        strong_floor = max(300_000_000.0, _minimum_traded_value(now))
        strong_leader = (
            impulse_rise + 1e-12 >= STRONG_LEADER_RISE
            and absolute_decline + 1e-12 >= MIN_REAL_PULLBACK
            and impulse_retracement + 1e-12 >= STRONG_LEADER_RETRACEMENT
            and peak_age <= STRONG_LEADER_MAX_PEAK_AGE_BARS
            and traded_value + 1e-12
            >= strong_floor * STRONG_LEADER_LIQUIDITY_MULTIPLIER
            and ma20 > 0
            and close >= session_open
            and close >= ma20
            and prominent_bear
        )
        if not (standard or strong_leader):
            continue

        if peak_age > MAX_INTEREST_PEAK_AGE_BARS:
            fresh_reburst = False
            for index in range(max(peak_index + 1, len(today) - 3), len(today)):
                if index < 5:
                    continue
                bo, _bh, _bl, bc, bv, _bs = _shape(today[index])
                prior_volumes = [_shape(bar)[4] for bar in today[index - 5:index]]
                if (
                    bc > bo
                    and all(item > 0 for item in prior_volumes)
                    and bv + 1e-12
                    >= mean(prior_volumes) * STALE_IMPULSE_REBURST_RATIO
                ):
                    fresh_reburst = True
                    break
            if not fresh_reburst:
                continue

        candidates.append(
            (peak_index, peak_price, impulse_rise, impulse_retracement, strong_leader)
        )

    if not candidates:
        return Decision(False, "ACTIVE_PULLBACK_CURRENT_BAR_DEPTH_MISSING")

    peak_index, peak_price, impulse_rise, retracement, strong_leader = candidates[-1]
    absolute_decline = (peak_price - low) / peak_price
    route = "STRONG_LEADER" if strong_leader else "STANDARD"
    return Decision(
        True,
        f"ACTIVE_PULLBACK_{route}_RISE={impulse_rise:.1%}"
        f"_DECLINE={absolute_decline:.1%}_RETURN={retracement:.0%}",
        120.0 if strong_leader else 100.0,
        peak_price,
        peak_index,
    )


def prominent_bear_interest_decision(
    bars: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Prominent bearish-volume flush after a +5% first impulse.

    The current volume is compared with nearby *bearish* candles, not only the
    immediately preceding candle.  A long bearish body raises the score but is
    not required, matching the user's 08:13 example without overfitting to one
    exact volume.
    """
    today = interest_session_bars(bars, now)
    if len(today) < 5:
        return Decision(False, "주변 음봉 비교용 1분봉 5개 미달")
    # Relative volume is not enough for an illiquid name.  The threshold is
    # expressed as traded value so it scales with the share price.
    if not price_normalized_liquidity_ok(today, now=now):
        return Decision(False, "FIRST_IMPULSE_INSUFFICIENT_ABSOLUTE_LIQUIDITY")
    current = today[-1]
    co, _ch, cl, cc, cv, span = _shape(current)
    active_pullback = active_interest_pullback_decision(today, now=now)
    if not active_pullback.allowed:
        return Decision(False, active_pullback.reason)
    peak_index = active_pullback.trigger_index
    cycle_high = active_pullback.reference_price
    base_start = max(0, peak_index - 20)
    cycle_base = min(_shape(bar)[2] for bar in today[base_start:peak_index + 1])
    rise = cycle_high / cycle_base - 1.0 if cycle_base > 0 else 0.0
    pullback = cycle_high / cc - 1.0 if cc > 0 else 0.0
    bearish = cc < co and cc < _shape(today[-2])[3]
    recent = today[max(0, len(today) - 3):]
    lower_close_count = sum(
        _shape(recent[index])[3] < _shape(recent[index - 1])[3]
        for index in range(1, len(recent))
    )
    nearby = _same_correction_bearish_before(
        today,
        current_index=len(today) - 1,
        peak_index=peak_index,
    )[-6:]
    volumes = [_shape(bar)[4] for bar in nearby]
    median_ratio = cv / max(1.0, median(volumes)) if volumes else 0.0
    max_ratio = cv / max(1.0, max(volumes)) if volumes else 0.0
    prominent = (
        len(volumes) >= 2
        and median_ratio + 1e-12 >= PROMINENT_BEAR_MEDIAN_RATIO
        and max_ratio + 1e-12 >= PROMINENT_BEAR_MAX_RATIO
    )
    previous_bear, minimum_meaningful = _meaningful_previous_bear(
        today,
        current_index=len(today) - 1,
        peak_index=peak_index,
    )
    previous_bear_volume = _shape(previous_bear)[4] if previous_bear is not None else 0.0
    previous_bear_ratio = cv / max(1.0, previous_bear_volume)
    previous_bear_growth = (
        previous_bear_volume > 0
        and previous_bear_ratio + 1e-12 >= PREVIOUS_BEAR_VOLUME_GROWTH
    )
    last_three = today[-3:]
    three_bear_shapes = [_shape(bar) for bar in last_three]
    three_bear_volume_rising = (
        len(three_bear_shapes) == 3
        and all(
            close < open_ and volume > 0
            for open_, _high, _low, close, volume, _span in three_bear_shapes
        )
        and three_bear_shapes[0][3] > three_bear_shapes[1][3] > three_bear_shapes[2][3]
        and three_bear_shapes[0][4] < three_bear_shapes[1][4] < three_bear_shapes[2][4]
        and len(today) - 3 > peak_index
    )
    body_ratio = (co - cc) / span if span > 0 else 0.0
    real_pullback = (
        active_pullback.allowed
        and (lower_close_count >= 2 or body_ratio >= 0.60)
    )
    requested_pullback = active_pullback.allowed
    requested_volume_pattern = previous_bear_growth or three_bear_volume_rising
    allowed = (
        bearish
        and (
            (prominent and real_pullback)
            or (requested_volume_pattern and requested_pullback)
        )
    )
    if allowed:
        if three_bear_volume_rising:
            three_volumes = [shape[4] for shape in three_bear_shapes]
            return Decision(
                True,
                f"1차상승{rise:.1%}후조정·하락음봉3봉거래연속증가"
                f"{three_volumes[0]:,.0f}→{three_volumes[1]:,.0f}→{three_volumes[2]:,.0f}",
                114.0,
                cc,
                len(today) - 1,
            )
        if previous_bear_growth:
            return Decision(
                True,
                f"1차상승{rise:.1%}후조정·직전음봉대비거래x{previous_bear_ratio:.2f}"
                "(30%이상증가)",
                110.0,
                cc,
                len(today) - 1,
            )
        confidence = "장대음봉 신뢰가산" if body_ratio >= 0.60 else "두드러진 음봉거래"
        return Decision(
            True,
            f"1차상승{rise:.1%}후조정·주변음봉중앙x{median_ratio:.2f}"
            f"·최대x{max_ratio:.2f}·{confidence}",
            104.0 + (8.0 if body_ratio >= 0.60 else 0.0),
            cc,
            len(today) - 1,
        )
    missing: list[str] = []
    if not bearish:
        missing.append("조정하락 음봉 아님")
    if not prominent:
        missing.append(
            f"주변음봉대비 중앙x{median_ratio:.2f}/최대x{max_ratio:.2f} 미달"
        )
    if not requested_volume_pattern:
        missing.append(
            f"같은조정유효음봉대비x{previous_bear_ratio:.2f}_30%미달"
            f"(유효하한{minimum_meaningful:,.0f})·3음봉거래연속증가아님"
        )
    if not real_pullback:
        missing.append(
            f"실제하락조정미달 고점대비{pullback:.1%}/하락종가{lower_close_count}봉"
        )
    return Decision(False, "·".join(missing) or "관심편입 대기")


def _first_impulse_context(today: Sequence[Any]) -> tuple[int, float, int] | None:
    """Return the newest completed +5% impulse/pullback cycle.

    A morning impulse must not keep controlling a separate afternoon wave.  A
    valid cycle therefore uses a local base from the preceding twenty completed
    one-minute candles and selects the latest cycle that has already made a
    material pullback.  This deliberately does not accept an uncorrected late
    breakout as a pullback-buy cycle.
    """

    if len(today) < 4:
        return None
    candidates: list[tuple[int, float, int]] = []
    for peak_index in range(1, len(today) - 1):
        peak_price = _shape(today[peak_index])[1]
        base_start = max(0, peak_index - 20)
        prior_local_high = max(
            (_shape(bar)[1] for bar in today[base_start:peak_index]),
            default=0.0,
        )
        if prior_local_high > 0 and peak_price < prior_local_high:
            continue
        base_price = min(_shape(bar)[2] for bar in today[base_start:peak_index + 1])
        if base_price <= 0 or peak_price / base_price - 1.0 < FIRST_IMPULSE_RISE:
            continue
        for correction_index in range(peak_index + 1, len(today)):
            correction_low = min(
                _shape(bar)[2] for bar in today[peak_index:correction_index + 1]
            )
            correction_pct = (peak_price - correction_low) / peak_price
            impulse_retracement = (peak_price - correction_low) / max(
                1e-9, peak_price - base_price
            )
            if (
                correction_pct + 1e-12 >= MIN_REAL_PULLBACK
                and impulse_retracement + 1e-12 >= MIN_IMPULSE_RETRACEMENT
            ):
                candidates.append((peak_index, peak_price, correction_index))
                break
    return candidates[-1] if candidates else None


def late_low_price_liquidity_excluded(
    bars: Sequence[Any], *, now: datetime
) -> bool:
    """Request 9 exact boundary for new scanner and entry stages."""

    today = pattern_bars(bars, now)
    if not today or now.time() < time(14, 0):
        return False
    current_price = _shape(today[-1])[3]
    cumulative_shares = sum(_shape(bar)[4] for bar in today)
    return current_price <= 5_000.0 and cumulative_shares <= 500_000.0


def _minimum_traded_value(now: datetime) -> float:
    clock = now.time()
    if clock >= time(10, 0):
        return 1_000_000_000.0
    if clock >= time(9, 30):
        return 500_000_000.0
    if clock >= time(9, 15):
        return 300_000_000.0
    return 0.0


def price_normalized_liquidity_ok(bars: Sequence[Any], *, now: datetime) -> bool:
    """Reject low-turnover names without using a fixed share-count cutoff.

    Share volume alone is misleading across a 1,000 won and a 100,000 won
    stock.  The gate therefore keeps the user's time-of-day liquidity floors
    as traded-value floors (300m / 500m / 1bn KRW).
    """
    today = pattern_bars(bars, now)
    if not today:
        return False
    clock = now.time()
    # Request 9: from 14:00, a <=5,000 won name with no more than 500,000
    # cumulative shares is excluded from every new scanner/entry stage.
    # Existing holdings are deliberately not handled here and are not sold.
    if late_low_price_liquidity_excluded(today, now=now):
        return False
    minimum_value = _minimum_traded_value(now)
    if minimum_value <= 0:
        return True
    traded_value = sum(_shape(bar)[3] * _shape(bar)[4] for bar in today)
    return traded_value + 1e-12 >= minimum_value


def three_bear_rising_volume_decision(
    bars: Sequence[Any], *, now: datetime
) -> Decision:
    """Qualified 1-minute three-bear pullback route.

    It is intentionally not a generic "three red candles" buy: a current
    first-impulse/pullback cycle, price-normalized liquidity, and a real
    descending correction are mandatory.  This blocks the chronic all-day
    downtrend and thin-name false positives reported in requests 3, 7, 8-10.
    """
    today = interest_session_bars(bars, now)
    if len(today) < 8:
        return Decision(False, "THREE_BEAR_NOT_ENOUGH_1M_BARS")
    if not price_normalized_liquidity_ok(today, now=now):
        return Decision(False, "THREE_BEAR_INSUFFICIENT_ABSOLUTE_LIQUIDITY")
    active_pullback = active_interest_pullback_decision(today, now=now)
    if not active_pullback.allowed:
        return Decision(False, f"THREE_BEAR_{active_pullback.reason}")
    peak_index = active_pullback.trigger_index
    peak_price = active_pullback.reference_price
    last_three = [_shape(bar) for bar in today[-3:]]
    bearish = all(close < open_ for open_, _high, _low, close, _volume, _span in last_three)
    descending = last_three[0][3] > last_three[1][3] > last_three[2][3]
    rising_volume = last_three[0][4] < last_three[1][4] < last_three[2][4]
    if not (bearish and descending and rising_volume):
        return Decision(False, "THREE_BEAR_SEQUENCE_MISSING")
    current_close = last_three[-1][3]
    correction = (peak_price - current_close) / peak_price if peak_price > 0 else 0.0
    open_, high, low, close, volume, span = last_three[-1]
    lower_wick = max(0.0, min(open_, close) - low)
    body = abs(close - open_)
    wick_bonus = 8.0 if lower_wick >= max(body * LONG_LOWER_WICK_BODY_RATIO, span * LONG_LOWER_WICK_RANGE_RATIO) else 0.0
    return Decision(
        True,
        "V7610_3BEAR_RISING_VOLUME"
        f"_PULLBACK={correction:.1%}_LAST_VOL={volume:,.0f}"
        f"_{active_pullback.reason}",
        112.0 + wick_bonus,
        current_close,
        len(today) - 1,
    )


def first_impulse_pullback_decision(
    bars: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Require a real pullback from the day's earliest confirmed first impulse."""

    today = pattern_bars(bars, now)
    context = _first_impulse_context(today)
    if context is None:
        return Decision(False, "FIRST_IMPULSE_OR_REAL_PULLBACK_MISSING")
    peak_index, peak_price, correction_index = context
    session_open = _shape(today[0])[0]
    first_rise = peak_price / session_open - 1.0
    correction_low = min(
        _shape(bar)[2] for bar in today[peak_index:correction_index + 1]
    )
    correction_pct = (peak_price - correction_low) / peak_price
    impulse_retracement = (peak_price - correction_low) / max(
        1e-9, peak_price - session_open
    )
    return Decision(
        True,
        f"FIRST_IMPULSE_{first_rise:.1%}_PULLBACK_{correction_pct:.1%}"
        f"_RETURN_{impulse_retracement:.0%}",
        100.0,
        peak_price,
        correction_index,
    )


def _fresh_bullish_reburst(today: Sequence[Any], correction_index: int) -> bool:
    """For a stale impulse, accept only a fresh bullish 2x-volume expansion."""

    start = max(correction_index, len(today) - 3)
    for index in range(start, len(today)):
        if index < 5:
            continue
        open_, _high, _low, close, volume, _span = _shape(today[index])
        prior = [_shape(bar)[4] for bar in today[index - 5:index]]
        if (
            close > open_
            and volume > 0
            and all(item > 0 for item in prior)
            and volume + 1e-12 >= mean(prior) * STALE_IMPULSE_REBURST_RATIO
        ):
            return True
    return False


def bottom_volume_ma5_entry_decision(
    flush: Any,
    bars: Sequence[Any],
    *,
    now: datetime,
    _require_ma5: bool = True,
    market_sharp_decline: bool = False,
) -> Decision:
    """Enter when pullback evidence exists in any order and price recovers MA5."""
    if not in_pattern_session(now):
        return Decision(False, "매매 세션 아님")
    today = pattern_bars(bars, now)
    if len(today) < 6:
        return Decision(False, "MA5·거래바닥 확인용 1분봉 6개 미달")
    flush_stamp = _stamp(flush)
    flush_index = next(
        (index for index, bar in enumerate(today) if _stamp(bar) == flush_stamp),
        -1,
    )
    if flush_index < 0 or flush_index >= len(today) - 1:
        return Decision(False, "관심 기준 음봉 뒤 완성봉 대기")
    flush_volume = _shape(flush)[4]
    if flush_volume <= 0:
        return Decision(False, "관심 기준 음봉 거래량 없음")

    flush_open, _flush_high, _flush_low, flush_close, _fv, _fs = _shape(flush)
    if not (flush_close < flush_open):
        return Decision(False, "FLUSH_NOT_BEARISH")
    impulse_context = _first_impulse_context(today)
    if impulse_context is None:
        return Decision(False, "FIRST_IMPULSE_OR_REAL_PULLBACK_MISSING")
    peak_index, peak_price, correction_index = impulse_context
    if flush_index < correction_index:
        return Decision(False, "FLUSH_BEFORE_REAL_PULLBACK")
    structural_risk = structural_distribution_risk(flush, today, now=now)
    if structural_risk.allowed and not structural_reclaim_confirmed(
        structural_risk, today, now=now
    ):
        return Decision(False, f"{structural_risk.reason}_RECLAIM_REQUIRED")

    prior_five = today[max(0, flush_index - 5) : flush_index]
    prior_five_volumes = [_shape(bar)[4] for bar in prior_five]
    if len(prior_five_volumes) != 5 or any(
        volume <= 0 for volume in prior_five_volumes
    ):
        return Decision(False, "FLUSH_PREVIOUS_5_VOLUME_INVALID")
    prior_five_average = mean(prior_five_volumes)
    if flush_volume <= prior_five_average:
        return Decision(
            False,
            f"FLUSH_NOT_ABOVE_PREVIOUS_5_AVERAGE_{flush_volume / prior_five_average:.2f}",
        )

    evidence_start = max(correction_index, len(today) - PULLBACK_EVIDENCE_WINDOW_BARS)
    if flush_index < evidence_start:
        return Decision(False, "PULLBACK_FLUSH_TOO_OLD")

    floor_index = -1
    floor_context_ratio = 0.0
    for index in range(evidence_start, len(today)):
        volume = _shape(today[index])[4]
        prior = today[max(evidence_start, index - 5):index]
        prior_volumes = [_shape(bar)[4] for bar in prior if _shape(bar)[4] > 0]
        if volume <= 0 or not prior_volumes:
            continue
        context = median(prior_volumes)
        local_floor = min(prior_volumes + [volume])
        if (
            volume <= flush_volume * DRY_FLOOR_RATIO + 1e-12
            and volume <= context * VOLUME_FLOOR_VS_CONTEXT + 1e-12
            and volume <= local_floor + 1e-12
        ):
            floor_index = index
            floor_context_ratio = volume / max(1.0, context)
    if floor_index < 0:
        return Decision(False, "관심음봉·주변대비 거래바닥 급감 대기")

    closes = [_shape(bar)[3] for bar in today]
    current_ma5 = mean(closes[-5:])
    previous_ma5 = mean(closes[-6:-1])
    current_open, _current_high, _current_low, current_close, _current_volume, current_span = _shape(today[-1])
    previous_close = closes[-2]
    crossed = previous_close < previous_ma5 and current_close >= current_ma5
    settled = (
        previous_close >= previous_ma5
        and current_close >= current_ma5
    )
    # Volume floor, lower wick, short candle and bullish candle may appear in
    # any order inside the same real pullback.  One reversal shape is required.
    shape_window_for_gate = today[evidence_start:]
    prior_spans = [
        _shape(bar)[5]
        for bar in today[max(0, evidence_start - 5) : evidence_start]
        if _shape(bar)[5] > 0
    ]
    gate_reference_span = (
        median(prior_spans) if prior_spans else max(current_span, 1e-9)
    )
    gate_lower_wick = False
    gate_short = False
    gate_bull = False
    for gate_bar in shape_window_for_gate:
        gate_open, _gh, gate_low, gate_close, _gv, gate_span = _shape(gate_bar)
        gate_body = abs(gate_close - gate_open)
        gate_wick = min(gate_open, gate_close) - gate_low
        gate_is_short = (
            gate_span > 0
            and gate_body <= gate_span * SHORT_BODY_RATIO
            and gate_span <= gate_reference_span * SHORT_RANGE_RATIO + 1e-12
        )
        gate_lower_wick = gate_lower_wick or (
            gate_span > 0
            and gate_wick + 1e-12 >= gate_body * LONG_LOWER_WICK_BODY_RATIO
            and gate_wick + 1e-12 >= gate_span * LONG_LOWER_WICK_RANGE_RATIO
        )
        gate_short = gate_short or gate_is_short
        gate_bull = gate_bull or gate_close > gate_open
    gate_confidence = [
        label
        for flag, label in (
            (gate_lower_wick, "LOWER_WICK"),
            (gate_short, "SHORT_BAR"),
            (gate_bull, "BULL_BAR"),
        )
        if flag
    ]
    if not gate_confidence:
        return Decision(False, "PULLBACK_REVERSAL_SHAPE_NOT_CONFIRMED")

    peak_age_minutes = max(
        0.0,
        (_stamp(today[-1]) - _stamp(today[peak_index])).total_seconds() / 60.0,
    )
    fresh_reburst = _fresh_bullish_reburst(today, correction_index)
    if peak_age_minutes > STALE_IMPULSE_MINUTES and not fresh_reburst:
        return Decision(False, f"FIRST_IMPULSE_STALE_{peak_age_minutes:.0f}MIN_REBURST_REQUIRED")
    if not _require_ma5:
        floor_volume = _shape(today[floor_index])[4]
        return Decision(
            True,
            (
                f"VOLUME_FLOOR_{floor_volume / flush_volume:.0%}"
                f"_CONTEXT_{floor_context_ratio:.0%}|"
                + "+".join(gate_confidence)
            ),
            106.0 + 2.0 * len(gate_confidence),
            current_close,
            len(today) - 1,
        )
    if not (crossed or settled):
        return Decision(
            False,
            f"거래바닥확인·MA5상향회복대기 CLOSE={current_close:,.0f}/MA5={current_ma5:,.0f}",
        )

    # In a persistent index decline, require two meaningful bearish flushes
    # (or one exceptional two-times flush) before the normal rebound route.
    # This is deliberately narrower than the ordinary-market rule.
    if market_sharp_decline:
        sharp_flushes = 0
        for index in range(evidence_start, len(today)):
            open_, _high, _low, close, volume, _span = _shape(today[index])
            prior = [_shape(item)[4] for item in today[max(0, index - 5):index]]
            if close < open_ and len(prior) == 5 and volume > mean(prior):
                sharp_flushes += 1
        if flush_volume < prior_five_average * 2.0 and sharp_flushes < 2:
            return Decision(False, "SHARP_MARKET_SECOND_FLUSH_REQUIRED")

    shape_window = today[evidence_start:]
    spans = [
        _shape(bar)[5]
        for bar in today[max(0, evidence_start - 5):evidence_start]
        if _shape(bar)[5] > 0
    ]
    reference_span = median(spans) if spans else max(current_span, 1e-9)
    lower_wick_seen = False
    short_seen = False
    short_bull_seen = False
    lower_wick_time = ""
    short_time = ""
    short_bull_time = ""
    for bar in shape_window:
        open_, _high, low, close, _volume, span = _shape(bar)
        body = abs(close - open_)
        lower_wick = min(open_, close) - low
        is_short = (
            span > 0
            and body <= span * SHORT_BODY_RATIO
            and span <= reference_span * SHORT_RANGE_RATIO + 1e-12
        )
        has_lower_wick = (
            span > 0
            and lower_wick + 1e-12 >= body * LONG_LOWER_WICK_BODY_RATIO
            and lower_wick + 1e-12 >= span * LONG_LOWER_WICK_RANGE_RATIO
        )
        if has_lower_wick and not lower_wick_seen:
            lower_wick_time = _stamp(bar).strftime("%H:%M")
        if is_short and not short_seen:
            short_time = _stamp(bar).strftime("%H:%M")
        if close > open_ and is_short and not short_bull_seen:
            short_bull_time = _stamp(bar).strftime("%H:%M")
        lower_wick_seen = lower_wick_seen or has_lower_wick
        short_seen = short_seen or is_short
        short_bull_seen = short_bull_seen or (close > open_ and is_short)
    if market_sharp_decline and not short_bull_seen:
        return Decision(False, "SHARP_MARKET_SHORT_BULL_REQUIRED")
    confidence = [
        label
        for flag, label in (
            (lower_wick_seen, "아래꼬리"),
            (short_seen, "짧은봉"),
            (short_bull_seen, "짧은양봉"),
        )
        if flag
    ]
    mode = "MA5돌파" if crossed else "MA5안착"
    confidence_text = "+".join(confidence) if confidence else "형태가산없음"
    floor_volume = _shape(today[floor_index])[4]
    evidence_times = "/".join(filter(None, (
        f"거래바닥{_stamp(today[floor_index]).strftime('%H:%M')}",
        f"아래꼬리{lower_wick_time}" if lower_wick_time else "",
        f"짧은봉{short_time}" if short_time else "",
        f"짧은양봉{short_bull_time}" if short_bull_time else "",
    )))
    signal_time = _stamp(today[-1]).strftime("%H:%M")
    decision_time = now.strftime("%H:%M:%S")
    confidence_prefix = "저신뢰·" if not confidence else ""
    return Decision(
        True,
        f"거래바닥급감{floor_volume / flush_volume:.0%}/주변{floor_context_ratio:.0%}"
        f"·순서무관반전·{mode}·{confidence_prefix}{confidence_text}"
        f"·근거시각[{evidence_times}]·신호봉{signal_time}·판정{decision_time}"
        + ("·3시간경과신규거래폭발" if fresh_reburst and peak_age_minutes > STALE_IMPULSE_MINUTES else ""),
        110.0 + 2.0 * len(confidence),
        current_close,
        len(today) - 1,
    )


def bottom_volume_shape_prebuy_decision(
    flush: Any,
    bars: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """PREBUY after floor and reversal evidence, before final MA5 recovery."""
    return bottom_volume_ma5_entry_decision(
        flush,
        bars,
        now=now,
        _require_ma5=False,
    )


def _volume_floor_index(flush: Any, after_flush: Sequence[Any]) -> int:
    flush_volume = _shape(flush)[4]
    for index, bar in enumerate(after_flush[:-1]):
        volume = _shape(bar)[4]
        prior = [flush] + list(after_flush[max(0, index - 3):index])
        context = median([_shape(item)[4] for item in prior])
        local = min([_shape(item)[4] for item in prior] + [volume])
        if (
            volume <= flush_volume * VOLUME_FLOOR_VS_FLUSH + 1e-12
            and volume <= context * VOLUME_FLOOR_VS_CONTEXT + 1e-12
            and volume <= local + 1e-12
        ):
            return index
    return -1


def konan_first_half_decision(
    flush: Any,
    after_flush: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Buy the first half only after floor -> short lower-wick bearish candle."""
    if not in_pattern_session(now):
        return Decision(False, "매매 세션 아님")
    if len(after_flush) < 2:
        return Decision(False, "거래바닥 뒤 확인봉 대기")
    floor_index = _volume_floor_index(flush, after_flush)
    if floor_index < 0:
        return Decision(False, "주변 대비 뚜렷한 거래바닥 대기")
    bars_after_floor = len(after_flush) - 1 - floor_index
    if not 1 <= bars_after_floor <= 3:
        return Decision(False, f"거래바닥후{bars_after_floor}봉_허용1~3봉")
    current = after_flush[-1]
    co, _ch, cl, cc, cv, span = _shape(current)
    _fo, _fh, _fl, _fc, fv, flush_span = _shape(flush)
    body = abs(cc - co)
    lower_wick = min(co, cc) - cl
    bearish = cc < co
    wick = lower_wick >= max(body * 0.20, span * 0.06)
    short = span <= max(1e-9, flush_span) * 0.75
    volume_ok = cv <= fv * FIRST_HALF_MAX_VS_FLUSH + 1e-12
    allowed = bearish and wick and short and volume_ok
    floor_ratio = _shape(after_flush[floor_index])[4] / max(1.0, fv)
    reason = (
        f"거래바닥{floor_ratio:.0%}후{bars_after_floor}봉"
        f"·아래꼬리짧은음봉·거래{cv/max(1.0, fv):.0%}"
        if allowed else
        "·".join(filter(None, (
            "음봉아님" if not bearish else "",
            "아래꼬리부족" if not wick else "",
            "짧은봉아님" if not short else "",
            f"거래{cv/max(1.0, fv):.0%}_60%초과" if not volume_ok else "",
        ))) or "1차절반 대기"
    )
    return Decision(
        allowed, reason, 142.0 if allowed else 0.0, cc, len(after_flush) - 1,
    )


def konan_second_half_decision(
    bars: Sequence[Any],
    *,
    first_bar_time: datetime,
    now: datetime,
) -> Decision:
    """Complete the position within 20 bars after MA5 and volume confirmation."""
    today = pattern_bars(bars, now)
    if len(today) < 6:
        return Decision(False, "1분 MA5 표본 6개 미달")
    first_index = max(
        (
            index for index, bar in enumerate(today)
            if _stamp(bar) <= first_bar_time
        ),
        default=-1,
    )
    later = len(today) - 1 - first_index
    if first_index < 0:
        return Decision(False, "1차매수봉확인실패")
    if later < 1:
        return Decision(False, "1차절반후확인봉대기")
    if later > SECOND_HALF_MAX_BARS:
        return Decision(False, f"1차절반후{later}봉_20봉경과_2차취소")

    floor_candidates = today[max(0, first_index - 4):first_index]
    if not floor_candidates:
        return Decision(False, "최초거래바닥저점확인실패")
    floor_bar = min(floor_candidates, key=lambda bar: _shape(bar)[4])
    floor_low = _shape(floor_bar)[2]
    closes_after_first = [_shape(bar)[3] for bar in today[first_index + 1:]]
    floor_broken = any(
        close < floor_low * (1.0 - SUPPORT_BREAK_TOLERANCE)
        for close in closes_after_first
    )
    if floor_broken:
        return Decision(
            False,
            f"최초거래바닥저점{floor_low:,.0f}_종가이탈_2차영구취소",
        )

    current = today[-1]
    co, _ch, _cl, cc, cv, span = _shape(current)
    previous_volume = _shape(today[-2])[4]
    recent_ranges = [_shape(bar)[5] for bar in today[-6:-1] if _shape(bar)[5] > 0]
    range_base = median(recent_ranges) if recent_ranges else span
    closes = [_shape(bar)[3] for bar in today]
    previous_ma5 = mean(closes[-6:-1])
    current_ma5 = mean(closes[-5:])
    slope = current_ma5 / previous_ma5 - 1.0 if previous_ma5 > 0 else -1.0
    bullish = cc > co
    short = span <= max(1e-9, range_base) * 0.85
    settled = cc >= current_ma5
    volume_recovering = cv >= previous_volume * SECOND_HALF_VOLUME_RECOVERY
    bull_gain = cc / co - 1.0 if co > 0 else 1.0
    not_chasing = bull_gain <= SECOND_HALF_MAX_BULL_GAIN
    allowed = (
        bullish and short and settled
        and volume_recovering and not_chasing
        and slope >= SECOND_HALF_MA5_SLOPE_MIN
    )
    reason = (
        f"짧은양봉·MA5안착 {cc:,.0f}/{current_ma5:,.0f}"
        f"·거래회복x{cv/max(1.0, previous_volume):.2f}·1차후{later}봉"
        if allowed else
        "·".join(filter(None, (
            "양봉아님" if not bullish else "",
            "짧은봉아님" if not short else "",
            f"MA5미안착 {cc:,.0f}/{current_ma5:,.0f}" if not settled else "",
            (
                f"거래회복대기x{cv/max(1.0, previous_volume):.2f}"
                if not volume_recovering else ""
            ),
            f"급등추격봉{bull_gain:.1%}" if not not_chasing else "",
            f"MA5기울기{slope:.2%}" if slope < SECOND_HALF_MA5_SLOPE_MIN else "",
        ))) or "2차절반 대기"
    )
    return Decision(
        allowed, reason, 148.0 if allowed else 0.0, cc, len(today) - 1,
    )


def konan_breakout_pullback_decision(
    bars: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Breakout volume absorbs the left peak, then pullback and re-expansion."""
    today = pattern_bars(bars, now)
    if len(today) < 10:
        return Decision(False, "돌파패턴 1분봉 10개 미달")
    current = today[-1]
    co, _ch, _cl, cc, cv, current_span = _shape(current)
    if cc <= co or current_span <= 0:
        return Decision(False, "재상승 확인 양봉 대기")
    for breakout_index in range(len(today) - 2, max(5, len(today) - 5), -1):
        pullbacks = today[breakout_index + 1:-1]
        if not 1 <= len(pullbacks) <= 3:
            continue
        breakout = today[breakout_index]
        bo, bh, bl, bc, bv, breakout_span = _shape(breakout)
        if bc <= bo or breakout_span <= 0:
            continue
        left = today[max(0, breakout_index - 10):breakout_index]
        if len(left) < 5:
            continue
        resistance = max(_shape(bar)[1] for bar in left)
        left_peak = max(_shape(bar)[4] for bar in left)
        context_volume = median([_shape(bar)[4] for bar in left[-5:]])
        body_ratio = (bc - bo) / breakout_span
        breakout_ok = (
            bc >= resistance + max(1.0, resistance * 0.0005)
            and bv >= left_peak * BREAKOUT_LEFT_PEAK_RATIO
            and bv >= context_volume * BREAKOUT_CONTEXT_RATIO
            and body_ratio >= 0.40
        )
        if not breakout_ok:
            continue
        midpoint = (bo + bc) / 2.0
        pullback_volumes = [_shape(bar)[4] for bar in pullbacks]
        pullback_ok = all(
            _shape(bar)[2] >= midpoint * 0.997
            and _shape(bar)[3] >= midpoint
            and _shape(bar)[4] <= bv * BREAKOUT_PULLBACK_MAX_VOLUME
            for bar in pullbacks
        )
        if not pullback_ok:
            continue
        pullback_base = mean(pullback_volumes)
        confirmation = (
            cc > bc
            and cv >= bv * BREAKOUT_CONFIRM_VS_BREAKOUT
            and cv >= pullback_base * BREAKOUT_CONFIRM_VS_PULLBACK
        )
        if confirmation:
            return Decision(
                True,
                f"왼쪽최대거래소화x{bv/max(1.0,left_peak):.2f}"
                f"·중심선지지{len(pullbacks)}봉"
                f"·재상승거래x{cv/max(1.0,bv):.2f}",
                152.0,
                cc,
                breakout_index,
            )
    return Decision(False, "전고점 대량돌파→거래축소 중심선지지→재상승 대기")


def _bar_signs(bar: Any, baseline_range: float, bars: Sequence[Any]) -> tuple[str, ...]:
    open_, _high, low, close, _volume, span = _shape(bar)
    body = abs(close - open_)
    lower_wick = min(open_, close) - low
    wick = lower_wick >= max(body * 0.30, span * 0.08)
    short = span <= max(1e-9, baseline_range) * SHORT_RANGE_RATIO
    bullish = close > open_
    ma5_reclaim = False
    if len(bars) >= 5:
        closes = [_shape(item)[3] for item in bars[-5:]]
        ma5_reclaim = bullish and close >= mean(closes)
    return tuple(
        name for name, matched in (
            ("아래꼬리", wick),
            ("짧은봉", short),
            ("양봉", bullish),
            ("1분MA5회복", ma5_reclaim),
        )
        if matched
    )


def first_tranche_decision(
    baseline: Any,
    after_baseline: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """First third: 50% dry-up seen, then <=30% plus any reversal shape.

    There is intentionally no candle-count requirement.  The 50% dry-up may
    have happened on any earlier candle after the baseline; the current candle
    must be the stronger <=30% volume-floor candle.
    """
    if not in_regular_session(now):
        return Decision(False, "정규장시간아님", trigger_index=-1)
    if len(after_baseline) < 2:
        return Decision(False, "기준봉50%이하봉후30%이하봉순서대기", trigger_index=-1)
    _bo, _bh, _bl, _bc, baseline_volume, baseline_range = _shape(baseline)
    if baseline_volume <= 0:
        return Decision(False, "기준음봉거래량0", trigger_index=-1)
    ratios = [_shape(bar)[4] / baseline_volume for bar in after_baseline]
    # The user's order is explicit: a <=50% candle must appear first and a
    # later candle must then reach the stronger <=30% floor.  The current
    # candle cannot satisfy both steps by itself.
    half_seen = any(value <= DRY_HALF_RATIO + 1e-12 for value in ratios[:-1])
    current_ratio = ratios[-1]
    signs = _bar_signs(after_baseline[-1], baseline_range, after_baseline)
    allowed = half_seen and current_ratio <= DRY_FLOOR_RATIO + 1e-12 and bool(signs)
    missing: list[str] = []
    if not half_seen:
        missing.append("기준봉50%이하거래미출현")
    if current_ratio > DRY_FLOOR_RATIO:
        missing.append(f"현재거래{current_ratio:.0%}_30%초과")
    if not signs:
        missing.append("반전형태없음")
    reason = (
        f"기준봉50%이하선행·현재거래{current_ratio:.0%}·{'/'.join(signs)}"
        if allowed else "·".join(missing)
    )
    return Decision(
        allowed, reason, 132.0 if allowed else 0.0,
        _shape(after_baseline[-1])[3], len(after_baseline) - 1,
    )


def prior_low_support_decision(
    bars_after_first: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Second third: a later candle confirms the already-made prior low."""
    if not in_regular_session(now):
        return Decision(False, "정규장시간아님")
    if len(bars_after_first) < 2:
        return Decision(False, "1차매수후지지확인봉2개미만")
    prior = list(bars_after_first[:-1])
    current = bars_after_first[-1]
    prior_low = min(_shape(bar)[2] for bar in prior)
    open_, _high, low, close, _volume, span = _shape(current)
    body = abs(close - open_)
    lower_wick = min(open_, close) - low
    defended = low + 1e-12 >= prior_low * (1.0 - SUPPORT_BREAK_TOLERANCE)
    close_recovered = close + 1e-12 >= prior_low
    stabilizing = close >= open_ or lower_wick >= max(body * 0.25, span * 0.06)
    allowed = prior_low > 0 and defended and close_recovered and stabilizing
    reason = (
        f"전저점{prior_low:,.0f}지지·저가{low:,.0f}·종가{close:,.0f}"
        if allowed else
        f"전저점지지대기 prior={prior_low:,.0f} low={low:,.0f} close={close:,.0f}"
    )
    return Decision(allowed, reason, 136.0 if allowed else 0.0, close, len(bars_after_first) - 1)


def bullish_ma5_reclaim_decision(
    bars_after_second: Sequence[Any],
    *,
    now: datetime,
) -> Decision:
    """Final third: a later bullish one-minute candle climbs onto MA5."""
    if not in_regular_session(now):
        return Decision(False, "정규장시간아님")
    if len(bars_after_second) < 6:
        return Decision(False, "1분MA5판정6봉미만")
    bars = list(bars_after_second)
    closes = [_shape(bar)[3] for bar in bars]
    current_open = _shape(bars[-1])[0]
    previous_ma5 = mean(closes[-6:-1])
    current_ma5 = mean(closes[-5:])
    slope = current_ma5 / previous_ma5 - 1.0 if previous_ma5 > 0 else -1.0
    bullish = closes[-1] > current_open
    reclaimed = closes[-2] < previous_ma5 and closes[-1] >= current_ma5
    # If the second-tranche support candle already sat on MA5, accept the next
    # bullish continuation candle instead of waiting for an artificial recross.
    continuation = closes[-2] >= previous_ma5 and closes[-1] >= current_ma5
    allowed = bullish and (reclaimed or continuation) and slope >= MARKET_MA5_FLAT_TOLERANCE
    reason = (
        f"양봉1분MA5안착·종가{closes[-1]:,.0f}/MA5{current_ma5:,.0f}·기울기{slope:.3%}"
        if allowed else
        f"MA5안착대기·종가{closes[-1]:,.0f}/MA5{current_ma5:,.0f}·기울기{slope:.3%}"
    )
    return Decision(allowed, reason, 140.0 if allowed else 0.0, closes[-1], len(bars) - 1)


def persistent_market_decline_decision(index_bars: Sequence[Any]) -> Decision:
    """Block only a persistent/crash decline; an ordinary red market passes."""
    if len(index_bars) < 7:
        return Decision(True, "지수추가판정표본부족·기존시장가드사용", 1.0)
    bars = sorted(index_bars, key=_stamp)
    closes = [_shape(bar)[3] for bar in bars]
    lows = [_shape(bar)[2] for bar in bars]
    returns = [
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    recent_returns = returns[-6:]
    crash_count = sum(value <= MARKET_CRASH_BAR for value in recent_returns)
    lower_lows = all(lows[index] < lows[index - 1] for index in range(len(lows) - 3, len(lows)))
    gap_change = closes[-1] / _shape(bars[0])[0] - 1.0 if _shape(bars[0])[0] > 0 else 0.0
    recent_change = closes[-1] / closes[-7] - 1.0 if closes[-7] > 0 else 0.0
    current_ma5 = mean(closes[-5:])
    prior_ma5 = mean(closes[-7:-2])
    ma5_slope = current_ma5 / prior_ma5 - 1.0 if prior_ma5 > 0 else 0.0
    gap_unrecovered = gap_change <= MARKET_GAP_DECLINE and lower_lows
    # Do not turn an ordinary red market into a blanket no-buy rule.  Persistent
    # deferral needs all three: successive lower lows, at least -0.8% over the
    # latest six minutes, and a clearly falling MA5 curve.
    persistent = (
        lower_lows
        and recent_change <= MARKET_PERSISTENT_DROP
        and ma5_slope <= MARKET_PERSISTENT_MA5_SLOPE
    )
    # Crashes and unrecovered gaps remain fail-closed.  An orderly persistent
    # decline routes to the stricter two-flush + short-bull entry rule instead.
    blocked = crash_count >= 3 or gap_unrecovered
    if blocked:
        return Decision(False, "MARKET_HARD_BLOCK")
    if persistent:
        return Decision(True, "SHARP_DECLINE")
    reason = (
        f"지수급락보류·급락봉{crash_count}개·저점연속하락={lower_lows}"
        f"·최근6분{recent_change:.2%}·시가대비{gap_change:.2%}·MA5기울기{ma5_slope:.3%}"
        if blocked else
        f"일반하락허용·급락봉{crash_count}개·시가대비{gap_change:.2%}"
        f"·최근6분{recent_change:.2%}·MA5기울기{ma5_slope:.3%}"
    )
    return Decision(not blocked, reason, 1.0 if not blocked else 0.0, closes[-1])
