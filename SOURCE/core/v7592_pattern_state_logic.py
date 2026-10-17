#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auditable pattern state machine for V75.92 PAPER.

The module never places orders.  It classifies the user's three independent
routes from one-minute OHLCV while the engine keeps three-minute structure as
the primary context.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median
from typing import Any, Sequence


@dataclass(slots=True)
class RouteEvidence:
    allowed: bool
    route: str
    state: str
    stage: str
    reason: str
    score: float = 0.0
    reference_price: float = 0.0
    trigger_index: int = -1
    belt: float = 0.0
    midpoint: float = 0.0
    volume_ratio: float = 0.0
    previous_day_ratio: float = 0.0
    projected_previous_day_ratio: float = 0.0
    formed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stamp(bar: Any) -> datetime:
    value = getattr(bar, "timestamp", None)
    if not isinstance(value, datetime):
        raise TypeError("bar.timestamp must be datetime")
    return value


def _shape(bar: Any) -> tuple[float, float, float, float, float, float, float, float]:
    o = float(getattr(bar, "open", 0) or 0)
    h = float(getattr(bar, "high", 0) or 0)
    l = float(getattr(bar, "low", 0) or 0)
    c = float(getattr(bar, "close", 0) or 0)
    v = float(getattr(bar, "volume", 0) or 0)
    span = max(0.0, h - l)
    loc = (c - l) / span if span else (1.0 if c >= o else 0.0)
    lower = (min(o, c) - l) / span if span else 0.0
    return o, h, l, c, v, span, loc, lower


def _baseline(bars: Sequence[Any], index: int, lookback: int = 30) -> float:
    values = [_shape(x)[4] for x in bars[max(0, index-lookback):index] if _shape(x)[4] > 0]
    return median(values) if values else 0.0


def previous_day_volume(bars: Sequence[Any], now: datetime) -> tuple[float, float]:
    totals: dict[Any, float] = {}
    for bar in bars:
        day = _stamp(bar).date()
        if day <= now.date():
            totals[day] = totals.get(day, 0.0) + _shape(bar)[4]
    earlier = sorted(day for day in totals if day < now.date())
    prior = totals.get(earlier[-1], 0.0) if earlier else 0.0
    if prior <= 0:
        return 0.0, 0.0
    actual = totals.get(now.date(), 0.0) / prior
    elapsed = max(1, min(390, now.hour * 60 + now.minute - 540))
    return actual, actual * 390.0 / elapsed


def _supply_belt(bars: Sequence[Any], index: int) -> float:
    """Repeated-contact supply belt; isolated wicks cannot define it."""
    prior = list(bars[max(0, index-120):index])
    if len(prior) < 12:
        return 0.0
    candidates = sorted(_shape(x)[3] for x in prior if _shape(x)[3] > 0)
    if not candidates:
        return 0.0
    upper = candidates[max(0, int(len(candidates)*0.82)-1):]
    best: list[float] = []
    for value in upper:
        cluster = [x for x in upper if abs(x/value-1.0) <= 0.006]
        if len(cluster) > len(best):
            best = cluster
    return median(best) if len(best) >= 2 else median(upper)


def _find_breakout(bars: Sequence[Any]) -> tuple[int, float, float, float] | None:
    for i in range(max(12, len(bars)-18), len(bars)):
        o, _h, _l, c, v, _span, loc, _lower = _shape(bars[i])
        base, belt = _baseline(bars, i), _supply_belt(bars, i)
        ratio = v/base if base > 0 else 0.0
        if belt > 0 and c > o and c >= belt*1.003 and ratio >= 3.0 and loc >= .62:
            return i, belt, (o+c)/2.0, ratio
    return None


def breakout_state(bars: Sequence[Any], *, now: datetime, formed: bool = True) -> RouteEvidence:
    """Breakout -> defended pullback -> relative floor/reclaim, or explicit failure."""
    if len(bars) < 22:
        return RouteEvidence(False, "breakout", "NONE", "관찰종목", "1분봉표본부족", formed=formed)
    found = _find_breakout(bars)
    if not found:
        return RouteEvidence(False, "breakout", "NONE", "관찰종목", "반복매물대확인돌파없음", formed=formed)
    idx, belt, midpoint, spike = found
    actual, projected = previous_day_volume(bars, now)
    supply = actual >= 2.0 or projected >= 2.0
    after = list(bars[idx+1:])
    if not after:
        return RouteEvidence(False, "breakout", "BREAKOUT_SEEN", "관심종목",
                             f"돌파확인·눌림대기·전일비{actual:.0%}/예상{projected:.0%}",
                             80, _shape(bars[idx])[3], idx, belt, midpoint, spike, actual, projected, formed)
    if len(after) > 12:
        return RouteEvidence(False, "breakout", "EXPIRED", "관심종목", "첫눌림12분유효시간경과",
                             trigger_index=idx, belt=belt, midpoint=midpoint, previous_day_ratio=actual,
                             projected_previous_day_ratio=projected, formed=formed)
    last = _shape(after[-1])
    breakout_low = _shape(bars[idx])[2]
    below = [(_shape(x)[3] < belt*.994 and _shape(x)[3] < midpoint*.99) for x in after[-2:]]
    sell_accel = len(after) >= 2 and last[3] < last[0] and last[4] > _shape(after[-2])[4]*1.35
    deep_break = last[2] < min(breakout_low, belt)*.99
    if (len(below) == 2 and all(below)) or (sell_accel and deep_break):
        return RouteEvidence(False, "breakout", "FAILED", "관심종목",
                             "돌파실패:벨트·중심선연속이탈" if not sell_accel else "돌파실패:매도거래재확대·저점이탈",
                             trigger_index=idx, belt=belt, midpoint=midpoint, previous_day_ratio=actual,
                             projected_previous_day_ratio=projected, formed=formed)
    volumes = [_shape(x)[4] for x in after]
    dry = last[4] <= min(volumes)+1e-9 and last[4] <= _shape(bars[idx])[4]*.60
    held = last[3] >= belt*.994 or last[3] >= midpoint*.995
    reclaim = last[3] >= midpoint and (last[3] >= last[0] or last[7] >= .28)
    compressed = last[5] <= max(1e-9, _shape(bars[idx])[5]*.58)
    not_falling = len(after) < 2 or last[3] >= _shape(after[-2])[3]*.995
    ready = supply and dry and held and (compressed or reclaim) and not_falling
    state = "READY" if ready else "PULLBACK"
    stage = "매수직전" if ready else "매수후보"
    why = (f"돌파첫눌림·거래최소{last[4]/max(1,_shape(bars[idx])[4]):.0%}·"
           f"{'중심선/벨트방어' if held else '지지확인대기'}·전일비{actual:.0%}/예상{projected:.0%}")
    return RouteEvidence(ready, "breakout", state, stage, why, 136 if ready else 95,
                         last[3], idx, belt, midpoint, last[4]/max(1,_shape(bars[idx])[4]),
                         actual, projected, formed)


def flow_absorption_state(bars: Sequence[Any], *, now: datetime, formed: bool = True) -> RouteEvidence:
    """Repeated bullish relative flow followed by the first defended dry-up."""
    if len(bars) < 25:
        return RouteEvidence(False, "flow_absorption", "NONE", "관찰종목", "1분봉표본부족", formed=formed)
    impulses: list[int] = []
    for i in range(max(5, len(bars)-70), len(bars)-1):
        o, _h, _l, c, v, _s, loc, _w = _shape(bars[i]); base = _baseline(bars, i, 20)
        if base > 0 and c > o and v/base >= 2.5 and loc >= .55:
            impulses.append(i)
    actual, projected = previous_day_volume(bars, now)
    if len(impulses) < 3:
        return RouteEvidence(False, "flow_absorption", "TRACK", "관찰종목",
                             f"양봉상대대량거래{len(impulses)}회", previous_day_ratio=actual,
                             projected_previous_day_ratio=projected, formed=formed)
    idx = impulses[-1]; after = list(bars[idx+1:])
    if not after or len(after) > 10:
        return RouteEvidence(False, "flow_absorption", "WAIT", "관심종목", "첫눌림대기또는경과",
                             trigger_index=idx, previous_day_ratio=actual, projected_previous_day_ratio=projected,
                             formed=formed)
    io, _ih, _il, ic, iv, _is, _iloc, _iw = _shape(bars[idx]); mid = (io+ic)/2
    last = _shape(after[-1]); vols = [_shape(x)[4] for x in after]
    dry = last[4] <= min(vols)+1e-9 and last[4] <= iv*.60
    defended = last[3] >= mid or (last[2] < mid and last[3] >= mid*.995 and last[7] >= .30)
    stable = last[3] >= last[0] or last[7] >= .28
    ready = dry and defended and stable
    return RouteEvidence(ready, "flow_absorption", "READY" if ready else "PULLBACK",
                         "매수직전" if ready else "매수후보",
                         f"양봉선행수급{len(impulses)}회·첫눌림거래{last[4]/max(1,iv):.0%}·"
                         f"{'중심선방어' if defended else '중심선재확인대기'}",
                         132 if ready else 92, last[3], idx, midpoint=mid,
                         volume_ratio=last[4]/max(1,iv), previous_day_ratio=actual,
                         projected_previous_day_ratio=projected, formed=formed)


def evaluate_routes(bars: Sequence[Any], *, now: datetime, formed: bool = True) -> RouteEvidence:
    breakout = breakout_state(bars, now=now, formed=formed)
    if breakout.allowed or breakout.state in {"BREAKOUT_SEEN", "PULLBACK", "FAILED"}:
        return breakout
    flow = flow_absorption_state(bars, now=now, formed=formed)
    return flow if flow.state != "NONE" else breakout
