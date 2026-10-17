#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure, auditable V75.93 route arbitration.

Only bars from ``now.date()`` are eligible for an intraday pattern.  Previous
day bars are used solely for same-clock/full-day volume evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median
from typing import Any, Sequence

PHASE_RANK = {"": 0, "OBSERVED": 1, "INTEREST": 2, "CANDIDATE": 3,
              "PREBUY": 4, "TREND_PREBUY": 4}


def can_transition(current: str, target: str, *, explicit_failure: bool = False) -> bool:
    return explicit_failure or PHASE_RANK.get(target, 0) >= PHASE_RANK.get(current, 0)


@dataclass(slots=True)
class Decision:
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
    previous_full_ratio: float = 0.0
    previous_same_time_ratio: float = 0.0
    formed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def stamp(bar: Any) -> datetime:
    value = getattr(bar, "timestamp", None)
    if not isinstance(value, datetime):
        raise TypeError("bar.timestamp must be datetime")
    return value


def shape(bar: Any) -> tuple[float, float, float, float, float, float, float, float]:
    o = float(getattr(bar, "open", 0) or 0)
    h = float(getattr(bar, "high", 0) or 0)
    l = float(getattr(bar, "low", 0) or 0)
    c = float(getattr(bar, "close", 0) or 0)
    v = max(0.0, float(getattr(bar, "volume", 0) or 0))
    span = max(0.0, h-l)
    location = (c-l)/span if span else (1.0 if c >= o else 0.0)
    lower = (min(o, c)-l)/span if span else 0.0
    return o, h, l, c, v, span, location, lower


def session_bars(bars: Sequence[Any], now: datetime) -> list[Any]:
    return sorted((x for x in bars if stamp(x).date() == now.date()), key=stamp)


def volume_evidence(bars: Sequence[Any], now: datetime) -> tuple[float, float]:
    """Return current/full-prior and current/prior-at-same-clock ratios.

    Same-clock comparison removes the 09:01 extrapolation bug.  No projected
    end-of-day number is manufactured.
    """
    prior_days = sorted({stamp(x).date() for x in bars if stamp(x).date() < now.date()})
    if not prior_days:
        return 0.0, 0.0
    prior_day = prior_days[-1]
    today = [x for x in bars if stamp(x).date() == now.date() and stamp(x) <= now]
    prior = [x for x in bars if stamp(x).date() == prior_day]
    current_total = sum(shape(x)[4] for x in today)
    prior_full = sum(shape(x)[4] for x in prior)
    clock = (now.hour, now.minute)
    prior_same = sum(shape(x)[4] for x in prior if (stamp(x).hour, stamp(x).minute) <= clock)
    return (
        current_total/prior_full if prior_full > 0 else 0.0,
        current_total/prior_same if prior_same > 0 else 0.0,
    )


def baseline(bars: Sequence[Any], index: int, lookback: int = 30) -> float:
    values = [shape(x)[4] for x in bars[max(0, index-lookback):index]]
    # Zero-volume minutes are real evidence in a dead stock; retain them.
    return median(values) if values else 0.0


def resistance_belt(bars: Sequence[Any], index: int) -> float:
    """Repeated-contact belt from prior highs/closes, never a single wick."""
    prior = list(bars[max(0, index-120):index])
    if len(prior) < 12:
        return 0.0
    points: list[tuple[int, float]] = []
    for i, bar in enumerate(prior):
        _o, h, _l, c, _v, _s, _loc, _w = shape(bar)
        if h > 0: points.append((i, h))
        if c > 0: points.append((i, c))
    prices = sorted(value for _, value in points)
    if not prices:
        return 0.0
    cutoff = prices[max(0, int(len(prices)*.72)-1)]
    best: list[tuple[int, float]] = []
    for _, candidate in points:
        if candidate < cutoff:
            continue
        cluster = [(i, value) for i, value in points if abs(value/candidate-1.0) <= .006]
        if len({i for i, _ in cluster}) >= 2 and len(cluster) > len(best):
            best = cluster
    return median(value for _, value in best) if best else 0.0


def find_breakout(today: Sequence[Any]) -> tuple[int, float, float, float] | None:
    # Newest valid event wins; an older pullback cannot shadow a newer route.
    for index in range(len(today)-1, max(11, len(today)-19), -1):
        o, _h, _l, c, volume, _span, location, _lower = shape(today[index])
        base, belt = baseline(today, index), resistance_belt(today, index)
        ratio = volume/base if base > 0 else 0.0
        body_gain = c/o-1.0 if o > 0 else 0.0
        if (belt > 0 and body_gain >= .015 and c >= belt*1.003
                and ratio >= 3.0 and location >= .62):
            return index, belt, (o+c)/2.0, ratio
    return None


def breakout_decision(all_bars: Sequence[Any], *, now: datetime, formed: bool) -> Decision:
    today = session_bars(all_bars, now)
    if len(today) < 22:
        return Decision(False, "breakout", "NONE", "", "당일1분봉22개미만", formed=formed)
    found = find_breakout(today)
    if found is None:
        return Decision(False, "breakout", "NONE", "", "당일반복매물대돌파없음", formed=formed)
    index, belt, midpoint, spike = found
    after = list(today[index+1:])
    full_ratio, same_ratio = volume_evidence(all_bars, now)
    supply = full_ratio >= 2.0 or same_ratio >= 2.0
    if not after:
        return Decision(False, "breakout", "SEEN", "관심종목", "당일돌파확인·첫눌림대기",
                        80, shape(today[index])[3], index, belt, midpoint, spike,
                        full_ratio, same_ratio, formed)
    if len(after) > 12:
        return Decision(False, "breakout", "EXPIRED", "", "첫눌림12분경과",
                        trigger_index=index, belt=belt, midpoint=midpoint,
                        previous_full_ratio=full_ratio, previous_same_time_ratio=same_ratio, formed=formed)
    last = shape(after[-1]); prior = shape(after[-2]) if len(after) > 1 else last
    breakout = shape(today[index])
    consecutive_below = len(after) >= 2 and all(
        shape(x)[3] < belt*.994 and shape(x)[3] < midpoint*.99 for x in after[-2:]
    )
    sell_accel = last[3] < last[0] and last[4] > prior[4]*1.35
    deep_break = last[2] < min(breakout[2], belt)*.99
    if consecutive_below or (sell_accel and deep_break):
        return Decision(False, "breakout", "FAILED", "관심종목", "돌파실패:지지연속이탈/매도재확대",
                        trigger_index=index, belt=belt, midpoint=midpoint,
                        previous_full_ratio=full_ratio, previous_same_time_ratio=same_ratio, formed=formed)
    volumes = [shape(x)[4] for x in after]
    dry = last[4] <= min(volumes)+1e-9 and last[4] <= breakout[4]*.60
    held = last[3] >= belt*.994 or last[3] >= midpoint*.995
    recovered = last[3] >= midpoint and (last[3] >= last[0] or last[7] >= .28)
    compressed = last[5] <= max(1e-9, breakout[5]*.58)
    not_falling = len(after) < 2 or last[3] >= prior[3]*.995
    not_chasing = last[3] <= breakout[3]*1.035
    ready = supply and dry and held and (compressed or recovered) and not_falling and not_chasing
    missing = []
    if not supply: missing.append(f"전일동시각거래{same_ratio:.0%}")
    if not dry: missing.append("눌림거래최소대기")
    if not held: missing.append("벨트/중심선미방어")
    if not (compressed or recovered): missing.append("봉압축/회복미확인")
    if not not_falling: missing.append("가격하락지속")
    if not not_chasing: missing.append("돌파후추격과열")
    reason = (f"당일돌파·거래{last[4]/max(1,breakout[4]):.0%}·지지방어·"
              f"전일전체{full_ratio:.0%}/동시각{same_ratio:.0%}") if ready else "·".join(missing)
    return Decision(ready, "breakout", "READY" if ready else "PULLBACK",
                    "매수직전" if ready else "매수후보", reason, 138 if ready else 96,
                    last[3], index, belt, midpoint, last[4]/max(1,breakout[4]),
                    full_ratio, same_ratio, formed)


def flow_decision(all_bars: Sequence[Any], *, now: datetime, formed: bool) -> Decision:
    today = session_bars(all_bars, now)
    if len(today) < 25:
        return Decision(False, "flow_absorption", "NONE", "", "당일1분봉25개미만", formed=formed)
    impulses: list[int] = []
    for i in range(max(5, len(today)-70), len(today)-1):
        o, _h, _l, c, v, _s, loc, _w = shape(today[i]); base = baseline(today, i, 20)
        if base > 0 and c > o and v/base >= 2.5 and loc >= .55:
            impulses.append(i)
    if len(impulses) < 3:
        return Decision(False, "flow_absorption", "TRACK", "", f"당일양봉상대거래{len(impulses)}회", formed=formed)
    index = impulses[-1]; after = list(today[index+1:])
    full_ratio, same_ratio = volume_evidence(all_bars, now)
    if not after or len(after) > 10:
        return Decision(False, "flow_absorption", "WAIT", "관심종목", "선행수급첫눌림대기/경과",
                        trigger_index=index, previous_full_ratio=full_ratio,
                        previous_same_time_ratio=same_ratio, formed=formed)
    impulse = shape(today[index]); midpoint = (impulse[0]+impulse[3])/2
    last = shape(after[-1]); volumes = [shape(x)[4] for x in after]
    dry = last[4] <= min(volumes)+1e-9 and last[4] <= impulse[4]*.60
    defended = last[3] >= midpoint or (last[2] < midpoint and last[3] >= midpoint*.995 and last[7] >= .30)
    stable = last[3] >= last[0] or last[7] >= .28
    not_chasing = last[3] <= impulse[3]*1.035
    ready = dry and defended and stable and not_chasing
    reason = (f"양봉선행수급{len(impulses)}회·첫눌림거래{last[4]/max(1,impulse[4]):.0%}·중심선방어"
              if ready else "·".join(filter(None, [
                  "눌림거래최소대기" if not dry else "", "중심선미방어" if not defended else "",
                  "봉안정미확인" if not stable else "", "추격과열" if not not_chasing else "",
              ])))
    return Decision(ready, "flow_absorption", "READY" if ready else "PULLBACK",
                    "매수직전" if ready else "매수후보", reason, 134 if ready else 94,
                    last[3], index, midpoint=midpoint, volume_ratio=last[4]/max(1,impulse[4]),
                    previous_full_ratio=full_ratio, previous_same_time_ratio=same_ratio, formed=formed)


def evaluate_all(all_bars: Sequence[Any], *, now: datetime, formed: bool = True) -> list[Decision]:
    return [breakout_decision(all_bars, now=now, formed=formed),
            flow_decision(all_bars, now=now, formed=formed)]


def arbitrate(decisions: Sequence[Decision]) -> Decision | None:
    allowed = [x for x in decisions if x.allowed]
    if allowed:
        return max(allowed, key=lambda x: (x.score, x.trigger_index))
    visible = [x for x in decisions if x.stage and x.state not in {"NONE", "TRACK", "EXPIRED"}]
    rank = {"FAILED": 1, "SEEN": 2, "WAIT": 2, "PULLBACK": 3, "READY": 4}
    return max(visible, key=lambda x: (rank.get(x.state, 0), x.trigger_index), default=None)
