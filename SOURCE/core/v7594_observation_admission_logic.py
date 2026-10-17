from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from statistics import mean
from typing import Any, Sequence


@dataclass(frozen=True)
class AdmissionEvidence:
    allowed: bool
    paths: tuple[str, ...]
    reason: str
    cumulative_volume: int
    cumulative_value: float


def _stamp(bar: Any) -> datetime:
    return getattr(bar, "timestamp")


def price_normalized_liquidity_requirement(price: float, now: datetime) -> tuple[int, int]:
    """Return the cumulative share/value floor derived from the 1,000-won rule."""
    clock = (now.hour, now.minute)
    if clock < (9, 15):
        return 0, 0
    required_value = 300_000_000
    if clock >= (10, 0):
        required_value = 1_000_000_000
    elif clock >= (9, 30):
        required_value = 500_000_000
    effective_price = max(1_000.0, float(price or 0.0))
    return int(ceil(required_value / effective_price)), required_value


def evaluate_observation_admission(
    bars: Sequence[Any], *, now: datetime, previous_full_volume: int | None = None,
    average_10d_volume: float | None = None,
) -> AdmissionEvidence:
    """Admit only a fresh one-minute impulse with price-normalized liquidity."""
    today = sorted((b for b in bars if _stamp(b).date() == now.date()), key=_stamp)
    if len(today) < 2:
        return AdmissionEvidence(False, (), "ONE_MINUTE_BARS_INSUFFICIENT", 0, 0.0)
    previous, current = today[-2], today[-1]
    pv = max(1, int(getattr(previous, "volume", 0) or 0))
    cv = int(getattr(current, "volume", 0) or 0)
    po = float(getattr(previous, "open", 0) or 0)
    pc = float(getattr(previous, "close", 0) or 0)
    co = float(getattr(current, "open", 0) or 0)
    cc = float(getattr(current, "close", 0) or 0)
    one_return = cc / pc - 1.0 if pc > 0 else 0.0
    cumulative_volume = sum(max(0, int(getattr(b, "volume", 0) or 0)) for b in today)
    cumulative_value = sum(
        max(0.0, float(getattr(b, "close", 0) or 0)) * max(0, int(getattr(b, "volume", 0) or 0))
        for b in today
    )

    previous_days = sorted({_stamp(b).date() for b in bars if _stamp(b).date() < now.date()})
    same_ratio: float | None = None
    if previous_days:
        prior = [b for b in bars if _stamp(b).date() == previous_days[-1] and _stamp(b).time() <= _stamp(current).time()]
        prior_volume = sum(max(0, int(getattr(b, "volume", 0) or 0)) for b in prior)
        if prior_volume > 0:
            same_ratio = cumulative_volume / prior_volume

    paths: list[str] = []
    if cv >= 20_000 and same_ratio is not None and same_ratio >= 2.0:
        paths.append(f"SAME_TIME_X{same_ratio:.2f}")
    if one_return >= .015:
        paths.append(f"ONE_MINUTE_UP_{one_return:.2%}")
    if cc > co and pc > po:
        paths.append("TWO_BULLISH")
    if cv / pv >= 2.0:
        paths.append(f"VOLUME_SPIKE_X{cv/pv:.2f}")
    if previous_full_volume and previous_full_volume > 0 and cumulative_volume / previous_full_volume >= 2.0:
        paths.append(f"PREV_DAY_X{cumulative_volume/previous_full_volume:.2f}")
    if average_10d_volume and average_10d_volume > 0 and cumulative_volume / average_10d_volume >= 2.0:
        paths.append(f"AVG10_X{cumulative_volume/average_10d_volume:.2f}")

    prior_window = today[max(0, len(today) - 7):-1]
    prior_high = max((float(getattr(b, "high", 0) or 0) for b in prior_window), default=0.0)
    prior_volumes = [int(getattr(b, "volume", 0) or 0) for b in prior_window]
    prior_average = mean(prior_volumes) if prior_volumes else 0.0
    fresh_breakout = cc > co and cc > prior_high and cv >= max(1.0, prior_average) * 1.5
    fresh_surge = cc > co and one_return >= .015
    fresh_event = fresh_breakout or fresh_surge
    if fresh_breakout:
        paths.append("FRESH_BREAKOUT")
    elif fresh_surge:
        paths.append("FRESH_SURGE")

    required_shares, required_value = price_normalized_liquidity_requirement(cc, now)
    liquidity_ok = cumulative_volume >= required_shares and cumulative_value >= required_value
    if not paths:
        reason = "NO_ADMISSION_PATH"
    elif not fresh_event:
        reason = "STALE_OR_NON_FRESH_TRIGGER"
    elif not liquidity_ok:
        reason = (
            f"LIQUIDITY_SHORT shares={cumulative_volume:,}/{required_shares:,} "
            f"value={cumulative_value:,.0f}/{required_value:,}"
        )
    else:
        reason = f"ADMIT={','.join(paths)} cumulative={cumulative_volume:,}/{cumulative_value:,.0f}"
    return AdmissionEvidence(
        bool(paths) and fresh_event and liquidity_ok,
        tuple(paths), reason, cumulative_volume, cumulative_value,
    )
