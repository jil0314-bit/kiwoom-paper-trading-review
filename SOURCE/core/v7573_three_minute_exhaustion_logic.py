#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure 3-minute sell-exhaustion/support logic for V75.73.

The evaluator deliberately has no API, GUI, clock, or order dependency so the
same completed-bar sequence drives the watch list and the PAPER entry signal.
"""
from __future__ import annotations

from dataclasses import dataclass
import statistics
from typing import Any, Sequence


WATCH = "WATCH"
SUPPORT_WAIT = "SUPPORT_WAIT"
STRONG_BUY = "STRONG_BUY"
CONFIRMED = "CONFIRMED"


@dataclass(frozen=True, slots=True)
class ThreeMinuteDecision:
    stage: str
    reason: str
    flush_index: int
    dry_index: int
    support_index: int
    confirm_index: int
    rise_pct: float
    flush_volume_ratio: float
    dry_volume_ratio: float
    support_low: float


def _number(bar: Any, name: str) -> float:
    return float(getattr(bar, name))


def _metrics(bar: Any) -> tuple[float, float, float, float, float]:
    """Return range, signed body, body, lower-wick and close-location ratios."""
    high = _number(bar, "high")
    low = _number(bar, "low")
    open_price = _number(bar, "open")
    close = _number(bar, "close")
    span = max(1e-9, high - low)
    signed_body = (close - open_price) / span
    body = abs(close - open_price) / span
    lower_wick = (min(open_price, close) - low) / span
    close_location = (close - low) / span
    return span, signed_body, body, lower_wick, close_location


def evaluate_three_minute_exhaustion(
    bars: Sequence[Any],
    *,
    low_break_tolerance: float = 0.0025,
) -> ThreeMinuteDecision | None:
    """Recognise the user-defined completed 3-minute sequence.

    Sequence: prior rise -> long bearish lower-wick flush -> another high-volume
    bearish lower-wick bar -> two bullish bars -> dry short bar (WATCH) -> later
    dry bearish lower-wick support (SUPPORT_WAIT) -> two bars later the support
    low still holds (STRONG_BUY on that exact completed bar).
    """
    items = list(bars)
    if len(items) < 11:
        return None

    first = max(4, len(items) - 20)
    for flush_index in range(first, len(items) - 4):
        flush = items[flush_index]
        pre = items[max(0, flush_index - 8):flush_index]
        if len(pre) < 4:
            continue
        pre_volumes = [max(0.0, _number(item, "volume")) for item in pre]
        pre_ranges = [_metrics(item)[0] for item in pre]
        pre_median_volume = max(1.0, float(statistics.median(pre_volumes)))
        pre_median_range = max(1e-9, float(statistics.median(pre_ranges)))
        rise_low = min(_number(item, "low") for item in pre)
        rise_high = max(_number(item, "high") for item in pre)
        rise_pct = rise_high / max(0.01, rise_low) - 1.0

        flush_range, flush_signed, flush_body, flush_lower, _ = _metrics(flush)
        flush_volume = max(0.0, _number(flush, "volume"))
        flush_volume_ratio = flush_volume / pre_median_volume
        if rise_pct < 0.025:
            continue
        if flush_signed >= 0 or flush_body < 0.35 or flush_lower < 0.12:
            continue
        if flush_volume_ratio < 1.12 or flush_range < pre_median_range * 1.05:
            continue

        follow = items[flush_index + 1]
        follow_range, follow_signed, _, follow_lower, _ = _metrics(follow)
        follow_volume = max(0.0, _number(follow, "volume"))
        if follow_signed >= 0 or follow_lower < 0.20:
            continue
        if not flush_volume * 0.45 <= follow_volume <= flush_volume * 0.95:
            continue
        if follow_range < flush_range * 0.45:
            continue

        first_bull = items[flush_index + 2]
        second_bull = items[flush_index + 3]
        if _number(first_bull, "close") <= _number(first_bull, "open"):
            continue
        if _number(second_bull, "close") <= _number(second_bull, "open"):
            continue

        dry_index = -1
        for index in range(flush_index + 4, len(items)):
            current = items[index]
            current_range = _metrics(current)[0]
            current_volume = max(0.0, _number(current, "volume"))
            local = items[max(flush_index, index - 4):index]
            local_median = max(
                1.0,
                float(statistics.median(max(0.0, _number(item, "volume")) for item in local)),
            )
            if (
                current_volume <= flush_volume * 0.35
                and current_volume <= local_median * 0.55
                and current_range <= flush_range * 0.55
            ):
                dry_index = index
                break
        if dry_index < 0:
            continue

        base_reason = (
            f"3분매도소진|1차상승={rise_pct:.2%}|대량음봉거래x{flush_volume_ratio:.2f}"
            f"|거래바닥={getattr(items[dry_index], 'timestamp', dry_index)}"
        )
        support_index = -1
        for index in range(dry_index + 1, len(items)):
            current = items[index]
            current_range, signed, body, lower_wick, close_location = _metrics(current)
            current_volume = max(0.0, _number(current, "volume"))
            if (
                signed < 0
                and body <= 0.55
                and lower_wick >= 0.18
                and close_location >= 0.35
                and current_range <= flush_range * 0.55
                and current_volume <= flush_volume * 0.25
            ):
                support_index = index
                break

        if support_index < 0:
            return ThreeMinuteDecision(
                WATCH,
                base_reason + "|판정=관찰종목",
                flush_index,
                dry_index,
                -1,
                -1,
                rise_pct,
                flush_volume_ratio,
                _number(items[dry_index], "volume") / max(1.0, flush_volume),
                0.0,
            )

        support_low = _number(items[support_index], "low")
        confirm_index = support_index + 2
        if len(items) <= confirm_index:
            return ThreeMinuteDecision(
                SUPPORT_WAIT,
                base_reason + f"|지지저점={support_low:.0f}|판정=지지확인대기",
                flush_index,
                dry_index,
                support_index,
                confirm_index,
                rise_pct,
                flush_volume_ratio,
                _number(items[dry_index], "volume") / max(1.0, flush_volume),
                support_low,
            )

        held = all(
            _number(items[index], "low") >= support_low * (1.0 - low_break_tolerance)
            for index in range(support_index + 1, confirm_index + 1)
        )
        if not held:
            continue
        stage = STRONG_BUY if len(items) - 1 == confirm_index else CONFIRMED
        return ThreeMinuteDecision(
            stage,
            base_reason + f"|지지저점={support_low:.0f}|판정={'강력매수' if stage == STRONG_BUY else '확인완료'}",
            flush_index,
            dry_index,
            support_index,
            confirm_index,
            rise_pct,
            flush_volume_ratio,
            _number(items[dry_index], "volume") / max(1.0, flush_volume),
            support_low,
        )
    return None
