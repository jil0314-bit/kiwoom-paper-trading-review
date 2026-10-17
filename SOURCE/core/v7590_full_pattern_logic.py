#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure one-minute pattern evaluators for V75.90.

The functions in this module do not place orders.  They turn the user's
labelled chart cases into auditable decisions and are intentionally isolated
from the broker, GUI and Hero link layers.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time, timedelta
from statistics import median
from typing import Any, Sequence


BREAKOUT_VOLUME_MULT = 3.0
BREAKOUT_PRICE_MARGIN = 0.003
PULLBACK_VOLUME_MAX = 0.60
PULLBACK_MAX_BARS = 10
BREAKOUT_MAX_CHASE = 0.035
MIDPOINT_TOLERANCE = 0.010
BELT_TOLERANCE = 0.006
REPEATED_FLOW_MULT = 2.5
REPEATED_FLOW_MIN_COUNT = 3
PREVIOUS_VOLUME_CONFIRM = 2.0


@dataclass(slots=True)
class PatternDecision:
    allowed: bool
    route: str
    reason: str
    score: float = 0.0
    reference_price: float = 0.0
    trigger_index: int = -1
    belt: float = 0.0
    midpoint: float = 0.0
    volume_ratio: float = 0.0
    previous_day_ratio: float = 0.0
    projected_previous_day_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stamp(bar: Any) -> datetime:
    value = getattr(bar, "timestamp", None)
    if not isinstance(value, datetime):
        raise TypeError("bar.timestamp must be datetime")
    return value


def _shape(bar: Any) -> tuple[float, float, float, float, float, float, float]:
    open_ = float(getattr(bar, "open", 0.0) or 0.0)
    high = float(getattr(bar, "high", 0.0) or 0.0)
    low = float(getattr(bar, "low", 0.0) or 0.0)
    close = float(getattr(bar, "close", 0.0) or 0.0)
    volume = float(getattr(bar, "volume", 0.0) or 0.0)
    span = max(0.0, high - low)
    close_location = (close - low) / span if span > 0 else (1.0 if close >= open_ else 0.0)
    return open_, high, low, close, volume, span, close_location


def _positive(values: Sequence[float]) -> list[float]:
    return [float(value) for value in values if float(value) > 0]


def _volume_baseline(bars: Sequence[Any], index: int, lookback: int = 30) -> float:
    volumes = _positive([_shape(bar)[4] for bar in bars[max(0, index - lookback):index]])
    return median(volumes) if volumes else 0.0


def _resistance_belt(bars: Sequence[Any], index: int, lookback: int = 120) -> float:
    prior = bars[max(0, index - lookback):index]
    if len(prior) < 10:
        return 0.0
    closes = _positive([_shape(bar)[3] for bar in prior])
    if not closes:
        return 0.0
    # A single high wick must not manufacture a resistance line.  The upper
    # close cluster is a stable approximation when exported XLS lacks OHLC.
    ordered = sorted(closes)
    cluster_start = max(0, int(len(ordered) * 0.90) - 1)
    return median(ordered[cluster_start:])


def previous_day_volume_evidence(bars: Sequence[Any], now: datetime) -> tuple[float, float]:
    by_day: dict[Any, float] = {}
    for bar in bars:
        stamp = _stamp(bar)
        if stamp.date() > now.date():
            continue
        by_day[stamp.date()] = by_day.get(stamp.date(), 0.0) + _shape(bar)[4]
    days = sorted(by_day)
    if now.date() not in by_day or len(days) < 2:
        return 0.0, 0.0
    earlier = [day for day in days if day < now.date()]
    if not earlier:
        return 0.0, 0.0
    previous = by_day[earlier[-1]]
    if previous <= 0:
        return 0.0, 0.0
    actual = by_day[now.date()] / previous
    clock = now.time()
    elapsed = max(1, min(390, (clock.hour * 60 + clock.minute) - 540))
    projected = actual * 390.0 / elapsed
    return actual, projected


def prior_flow_gate(bars: Sequence[Any], *, now: datetime) -> PatternDecision:
    """Reject algorithmic entries that never had a real buying/volume wave.

    This is deliberately adaptive: low-priced or thin stocks may pass through
    repeated relative bullish spikes, while liquid stocks may pass through a
    single absolute 20k-share +1.5% minute.  Mere quiet drifting cannot pass.
    """
    today = [bar for bar in bars if _stamp(bar).date() == now.date()]
    if len(today) < 3:
        return PatternDecision(False, "prior_flow", "당일1분봉3개미확보")
    # A morning spike must not authorize an unrelated afternoon entry.  Only
    # the most recent hour can supply the entry's "prior flow" evidence.
    recent_cutoff = now - timedelta(minutes=60)
    bullish_spikes = 0
    recent_spikes = 0
    absolute_impulse = False
    session_low = min(_shape(bar)[2] for bar in today if _shape(bar)[2] > 0)
    session_high = max(_shape(bar)[1] for bar in today)
    for index, bar in enumerate(today):
        open_, _h, _l, close, volume, _span, location = _shape(bar)
        baseline = _volume_baseline(today, index, 20)
        relative = volume / baseline if baseline > 0 else 0.0
        if close > open_ and relative >= REPEATED_FLOW_MULT and location >= 0.55:
            bullish_spikes += 1
            if _stamp(bar) >= recent_cutoff:
                recent_spikes += 1
        if index > 0:
            previous_close = _shape(today[index - 1])[3]
            change = close / previous_close - 1.0 if previous_close > 0 else 0.0
            if _stamp(bar) >= recent_cutoff:
                absolute_impulse = absolute_impulse or (abs(change) >= 0.015 and volume >= 20_000)
    actual, projected = previous_day_volume_evidence(bars, now)
    rise = session_high / session_low - 1.0 if session_low > 0 else 0.0
    volume_alive = actual >= 0.80 or projected >= 1.00
    wave = rise >= 0.03 and (recent_spikes >= 1 or absolute_impulse)
    repeated = recent_spikes >= REPEATED_FLOW_MIN_COUNT
    allowed = volume_alive and (wave or repeated)
    reason = (
        f"선행상승{rise:.2%}·최근60분양봉상대거래{recent_spikes}회·전일비{actual:.0%}/예상{projected:.0%}"
        if allowed else "·".join(filter(None, [
            "전일대비거래부족" if not volume_alive else "",
            "거래동반일차상승없음" if not (wave or repeated) else "",
        ])) or "선행수급미확인"
    )
    return PatternDecision(
        allowed, "prior_flow", reason, score=100.0 if allowed else 0.0,
        previous_day_ratio=actual, projected_previous_day_ratio=projected,
    )


def _find_breakout(bars: Sequence[Any]) -> tuple[int, float, float, float] | None:
    start = max(10, len(bars) - (PULLBACK_MAX_BARS + 5))
    for index in range(len(bars) - 1, start - 1, -1):
        open_, _high, _low, close, volume, _span, close_location = _shape(bars[index])
        baseline = _volume_baseline(bars, index)
        belt = _resistance_belt(bars, index)
        if baseline <= 0 or belt <= 0:
            continue
        volume_ratio = volume / baseline
        if (
            close > open_
            and close >= belt * (1.0 + BREAKOUT_PRICE_MARGIN)
            and volume_ratio >= BREAKOUT_VOLUME_MULT
            and close_location >= 0.60
        ):
            midpoint = (open_ + close) / 2.0
            return index, belt, midpoint, volume_ratio
    return None


def breakout_pullback_signal(bars: Sequence[Any], *, now: datetime) -> PatternDecision:
    if len(bars) < 22:
        return PatternDecision(False, "breakout_pullback", "1분봉표본부족")
    found = _find_breakout(bars)
    if found is None:
        return PatternDecision(False, "breakout_pullback", "유효전고점돌파없음")
    index, belt, midpoint, spike_ratio = found
    after = list(bars[index + 1:])
    if not after:
        return PatternDecision(False, "breakout_pullback", "돌파후눌림대기")
    if len(after) > PULLBACK_MAX_BARS:
        return PatternDecision(False, "breakout_pullback", "첫눌림유효시간경과", trigger_index=index)
    breakout_volume = max(1.0, _shape(bars[index])[4])
    current = after[-1]
    current_open, _ch, current_low, current_close, current_volume, current_span, _loc = _shape(current)
    volumes = [_shape(bar)[4] for bar in after]
    volume_ratio = current_volume / breakout_volume
    local_floor = current_volume <= min(volumes) + 1e-9
    drying = volume_ratio <= PULLBACK_VOLUME_MAX and (
        len(volumes) == 1 or current_volume <= median(volumes)
    )
    belt_held = current_close >= belt * (1.0 - BELT_TOLERANCE)
    midpoint_held = current_close >= midpoint * (1.0 - MIDPOINT_TOLERANCE)
    recovered = belt_held or current_close >= min(current_open, midpoint) or (
        current_span > 0 and current_close - current_low >= current_span * 0.35
    )
    not_chasing = current_close <= _shape(bars[index])[3] * (1.0 + BREAKOUT_MAX_CHASE)
    actual, projected = previous_day_volume_evidence(bars, now)
    allowed = drying and local_floor and (belt_held or midpoint_held) and recovered and not_chasing
    reason = (
        f"돌파거래x{spike_ratio:.2f}·눌림거래{volume_ratio:.0%}·"
        f"{'벨트방어' if belt_held else '중심선방어'}·상대거래최소"
        if allowed else
        "·".join(filter(None, [
            "거래미감소" if not drying else "",
            "상대최소아님" if not local_floor else "",
            "벨트·중심선미방어" if not (belt_held or midpoint_held) else "",
            "저가회복미흡" if not recovered else "",
            "돌파후추격과열" if not not_chasing else "",
        ])) or "눌림확인대기"
    )
    return PatternDecision(
        allowed, "breakout_pullback", reason,
        score=132.0 if allowed else 0.0, reference_price=current_close,
        trigger_index=index, belt=belt, midpoint=midpoint,
        volume_ratio=volume_ratio, previous_day_ratio=actual,
        projected_previous_day_ratio=projected,
    )


def verified_breakout_signal(bars: Sequence[Any], *, now: datetime) -> PatternDecision:
    """Very strong completed breakout; the safer pullback route stays preferred."""
    if len(bars) < 22:
        return PatternDecision(False, "verified_breakout", "1분봉표본부족")
    index = len(bars) - 1
    open_, _high, _low, close, volume, _span, close_location = _shape(bars[index])
    baseline = _volume_baseline(bars, index)
    belt = _resistance_belt(bars, index)
    ratio = volume / baseline if baseline > 0 else 0.0
    actual, projected = previous_day_volume_evidence(bars, now)
    supply_confirmed = actual >= PREVIOUS_VOLUME_CONFIRM or projected >= PREVIOUS_VOLUME_CONFIRM
    gap = close / belt - 1.0 if belt > 0 else 0.0
    allowed = (
        open_ > 0 and close > open_ and belt > 0
        and BREAKOUT_PRICE_MARGIN <= gap <= BREAKOUT_MAX_CHASE
        and ratio >= 4.0 and volume >= 20_000
        and close_location >= 0.72 and supply_confirmed
    )
    reason = (
        f"전고점돌파{gap:.2%}·거래x{ratio:.2f}·전일비{actual:.0%}/예상{projected:.0%}"
        if allowed else "강한완성돌파조건미달"
    )
    return PatternDecision(
        allowed, "verified_breakout", reason, score=128.0 if allowed else 0.0,
        reference_price=close, trigger_index=index, belt=belt,
        midpoint=(open_ + close) / 2.0, volume_ratio=ratio,
        previous_day_ratio=actual, projected_previous_day_ratio=projected,
    )


def strong_flow_absorption_signal(bars: Sequence[Any], *, now: datetime) -> PatternDecision:
    """Repeated bullish relative-volume flow followed by its first defended dry-up."""
    if len(bars) < 25:
        return PatternDecision(False, "flow_absorption", "1분봉표본부족")
    start = max(5, len(bars) - 70)
    impulses: list[int] = []
    for index in range(start, len(bars) - 1):
        open_, _h, _l, close, volume, _span, location = _shape(bars[index])
        baseline = _volume_baseline(bars, index, 20)
        if baseline > 0 and close > open_ and volume / baseline >= REPEATED_FLOW_MULT and location >= 0.55:
            impulses.append(index)
    if len(impulses) < REPEATED_FLOW_MIN_COUNT:
        return PatternDecision(False, "flow_absorption", f"양봉상대대량거래{len(impulses)}회")
    impulse = impulses[-1]
    after = list(bars[impulse + 1:])
    if not after or len(after) > PULLBACK_MAX_BARS:
        return PatternDecision(False, "flow_absorption", "첫눌림대기또는경과", trigger_index=impulse)
    io, _ih, _il, ic, iv, _ir, _iloc = _shape(bars[impulse])
    midpoint = (io + ic) / 2.0
    current = after[-1]
    co, _ch, cl, cc, cv, cr, _cloc = _shape(current)
    volumes = [_shape(bar)[4] for bar in after]
    dry_ratio = cv / max(1.0, iv)
    dry = dry_ratio <= PULLBACK_VOLUME_MAX and cv <= min(volumes) + 1e-9
    # The aggressive flow route still requires the latest close to recover the
    # last impulse's exact body midpoint.  A tolerance here admitted the
    # labelled TXR 13:32 failed breakout near its local top.
    support = cc >= midpoint
    recovery = cc >= co or (cr > 0 and cc - cl >= cr * 0.35)
    actual, projected = previous_day_volume_evidence(bars, now)
    allowed = dry and support and recovery
    reason = (
        f"양봉상대대량거래{len(impulses)}회·첫눌림거래{dry_ratio:.0%}·중심선방어"
        if allowed else "·".join(filter(None, [
            "첫눌림거래미소진" if not dry else "",
            "선행양봉중심선이탈" if not support else "",
            "저가회복미흡" if not recovery else "",
        ])) or "첫눌림확인대기"
    )
    return PatternDecision(
        allowed, "flow_absorption", reason, score=130.0 if allowed else 0.0,
        reference_price=cc, trigger_index=impulse, midpoint=midpoint,
        volume_ratio=dry_ratio, previous_day_ratio=actual,
        projected_previous_day_ratio=projected,
    )


def evaluate_new_routes(bars: Sequence[Any], *, now: datetime) -> PatternDecision:
    """Priority: defended breakout pullback, first absorption, exceptional breakout."""
    for evaluator in (breakout_pullback_signal, strong_flow_absorption_signal, verified_breakout_signal):
        decision = evaluator(bars, now=now)
        if decision.allowed:
            return decision
    return PatternDecision(False, "none", "신규세경로조건미충족")
