#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.95 R21 pure one-minute decision rules.

This module contains no broker, GUI, Kiwoom or filesystem side effects.  It
turns the numeric rules in ``자동매매 코딩 로직 요청사항.hwpx`` into decisions
that can be attacked by boundary tests before they are wired to the PAPER
engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from statistics import mean, median
from typing import Any, Sequence


OBS_SAME_TIME_RATIO = 1.60
PREVIOUS_DAY_EXPLOSION_RATIO = 3.00
OBS_ONE_MINUTE_RETURN = 0.015
OBS_ONE_MINUTE_VOLUME_RATIO = 2.00
OBS_SESSION_RISE = 0.05
MORNING_LIQUIDITY_VOLUME = 200_000
MORNING_LIQUIDITY_VALUE = 3_000_000_000.0
EARLY_TURNOVER_VALUE = 5_000_000_000.0

# Observation request 1: score current participation from traded value,
# persistence and price response.  These constants belong only to OBSERVED
# admission; INTEREST, PREBUY, PAPER BUY/SELL and the 0600 link are untouched.
OBS_SCORE_MINIMUM = 3
OBS_VALUE_MEDIAN_MULTIPLIER = 1.50
OBS_THREE_BAR_VALUE_MULTIPLIER = 1.50
OBS_THREE_BAR_RETURN = 0.01
OBS_BREAKOUT_LOOKBACK = 10
OBS_FIRST_EXPLOSION_MULTIPLIER = 2.00
OBS_CONTINUATION_RETAIN_RATIO = 0.60
OBS_SIGNAL_VALUE_MINIMUM = 30_000_000.0
OBS_CUMULATIVE_VALUE_0915 = 300_000_000.0
OBS_CUMULATIVE_VALUE_0930 = 500_000_000.0
OBS_CUMULATIVE_VALUE_1000 = 1_000_000_000.0

INTEREST_BEAR_VOLUME_RATIO = 3.00
DRY_VS_FLUSH = 0.50
EXHAUSTION_AVG_VS_FLUSH = 0.30
EXHAUSTION_MIN_BARS = 5
EXHAUSTION_MAX_BARS = 10
SHORT_RANGE_VS_FLUSH = 0.65

GENERAL_LOOKBACK_HIGH = 10
GENERAL_VOLUME_MA_PERIOD = 5
GENERAL_VOLUME_MULTIPLIER = 1.80
GENERAL_MIN_BREAK_TICKS = 2
GENERAL_BODY_RATIO = 0.40
GENERAL_UPPER_WICK_BODY = 1.20

DRY_LOOKBACK = 8
DRY_VOLUME_RATIO = 0.35
DRY_BREAK_VOLUME_MULTIPLIER = 2.20
DRY_CONSOLIDATION_BARS = 6
DRY_MAX_BAR_TO_AVG = 2.50
DRY_CLOSE_LOCATION = 0.65


@dataclass(frozen=True)
class AdmissionEvidence:
    allowed: bool
    paths: tuple[str, ...]
    reason: str
    cumulative_volume: int
    cumulative_value: float
    same_time_ratio: float | None = None
    session_rise: float = 0.0


@dataclass(frozen=True)
class StageDecision:
    allowed: bool
    reason: str
    score: float = 0.0
    reference_price: float = 0.0
    route: str = ""
    trigger_index: int = -1


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


def _stock_tick_size(price: float) -> int:
    """Return the same common-stock tick used by the PAPER broker."""
    value = max(0.0, float(price))
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def _today(bars: Sequence[Any], now: datetime) -> list[Any]:
    return sorted((bar for bar in bars if _stamp(bar).date() == now.date()), key=_stamp)


def _prior_same_time_value(bars: Sequence[Any], current: Any) -> float | None:
    earlier_days = sorted({_stamp(bar).date() for bar in bars if _stamp(bar).date() < _stamp(current).date()})
    if not earlier_days:
        return None
    prior_day = earlier_days[-1]
    cutoff = _stamp(current).time()
    value = sum(
        max(0.0, float(getattr(bar, "close", 0) or 0.0))
        * max(0, int(getattr(bar, "volume", 0) or 0))
        for bar in bars
        if _stamp(bar).date() == prior_day and _stamp(bar).time() <= cutoff
    )
    return value or None


def _observation_liquidity_requirement(price: float, clock: time) -> tuple[int, float]:
    """Keep the existing time-of-day liquidity floors, normalized by price."""

    if clock < time(9, 15):
        return 0, 0.0
    required_value = OBS_CUMULATIVE_VALUE_0915
    if clock >= time(10, 0):
        required_value = OBS_CUMULATIVE_VALUE_1000
    elif clock >= time(9, 30):
        required_value = OBS_CUMULATIVE_VALUE_0930
    effective_price = max(1_000.0, float(price or 0.0))
    return int((required_value + effective_price - 1) // effective_price), required_value


def _penny_volume_floor(price: float, clock: time) -> int:
    """Requested cumulative-volume floor for stocks priced below 1,000 won."""

    if float(price) >= 1_000.0:
        return 0
    if clock >= time(10, 0):
        return 1_000_000
    if clock >= time(9, 30):
        return 500_000
    if clock >= time(9, 15):
        return 300_000
    return 0


def evaluate_observation_admission(
    bars: Sequence[Any], *, now: datetime, previous_full_volume: int | None = None,
    average_10d_volume: float | None = None,
) -> AdmissionEvidence:
    """OBS-20260909-NWAVE: first +5%, then +3% from each pullback low.

    Only observation admission changes.  A lower completed close after a
    qualified rise starts the next pullback; its first bar cannot also confirm
    that next rise.  Equal lows do not renew the inclusive 15-bar clock.
    Missing minutes break the price sequence, never become fabricated bars.
    """
    from datetime import timedelta, timezone
    from math import isfinite

    kst = timezone(timedelta(hours=9))
    local_now = now.replace(tzinfo=kst) if now.tzinfo is None else now.astimezone(kst)
    cutoff = local_now.replace(second=0, microsecond=0)
    today = []
    try:
        for item in bars:
            stamp = _stamp(item)
            stamp = stamp.replace(tzinfo=kst) if stamp.tzinfo is None else stamp.astimezone(kst)
            if stamp.date() == local_now.date() and time(8) <= stamp.time() and stamp < cutoff:
                if stamp.second or stamp.microsecond:
                    return AdmissionEvidence(False, (), "관찰대기=1분봉시각오류", 0, 0.0)
                today.append((stamp, item))
    except (TypeError, ValueError, AttributeError):
        return AdmissionEvidence(False, (), "관찰대기=1분봉시각오류", 0, 0.0)
    today.sort(key=lambda row: row[0])
    if not today:
        return AdmissionEvidence(False, (), "관찰대기=08시이후완성1분봉미확보", 0, 0.0)
    if today[-1][0] != cutoff - timedelta(minutes=1):
        return AdmissionEvidence(False, (), "관찰대기=최신완성1분봉미확보", 0, 0.0)

    low_price = float("inf")
    low_index = 0
    low_time = today[0][0]
    cumulative_volume = 0
    cumulative_value = 0.0
    cc = cv = 0.0
    wave = 0
    climbing = False
    previous_close = None
    threshold = 5
    for index, (stamp, item) in enumerate(today):
        gap = index and stamp - today[index - 1][0] != timedelta(minutes=1)
        if index and stamp == today[index - 1][0]:
            return AdmissionEvidence(False, (), "관찰대기=1분봉누락또는중복", cumulative_volume, cumulative_value)
        try:
            co, ch, cl, cc, cv, _range = _shape(item)
        except (TypeError, ValueError, OverflowError):
            return AdmissionEvidence(False, (), "관찰대기=비정상OHLCV", cumulative_volume, cumulative_value)
        if (not all(isfinite(x) for x in (co, ch, cl, cc, cv))
                or min(co, ch, cl, cc) <= 0 or cv < 0
                or not cl <= min(co, cc) <= max(co, cc) <= ch):
            return AdmissionEvidence(False, (), "관찰대기=비정상OHLCV", cumulative_volume, cumulative_value)
        cumulative_volume += int(cv)
        cumulative_value += cc * cv
        if gap:
            # A gap invalidates this leg only, not every later leg that day.
            # Retain only rises already confirmed entirely before the gap.
            low_price, low_index, low_time = cl, index, stamp
            climbing = False
            threshold = 5 if wave == 0 else 3
            previous_close = cc
            # Do not infer a new rise on the very first bar after missing data.
            continue
        if climbing and previous_close is not None and cc < previous_close:
            climbing = False
            low_price, low_index, low_time = cl, index, stamp
            threshold = 3
            previous_close = cc
            # OHLC cannot establish the order of this bar's high and low.
            continue
        if cl < low_price:
            low_price, low_index, low_time = cl, index, stamp
        if not climbing:
            threshold = 5 if wave == 0 else 3
            if index - low_index + 1 <= 15 and cc * 100 >= low_price * (100 + threshold) and cv > 0:
                wave += 1
                climbing = True
        previous_close = cc

    count = len(today) - low_index
    rise = cc / low_price - 1.0
    # No backdated admission during a pullback, even if an earlier bar qualified.
    allowed = climbing and count <= 15 and cc * 100 >= low_price * (100 + threshold) and cv > 0
    detail = (
        f"OBS-20260909-NWAVE·상승차수{wave if climbing else wave + 1}·기준+{threshold}%"
        f"·저점{low_time.strftime('%H:%M')}={low_price:,.0f}"
        f"·봉수{count}/15·완성종가{cc:,.0f}·저점대비{rise:+.2%}"
        f"·판정봉{today[-1][0].strftime('%H:%M')}"
    )
    reason = ("관찰편입=" if allowed else "관찰대기=") + detail
    return AdmissionEvidence(
        allowed, ("OBS-20260909-NWAVE",) if allowed else (), reason,
        cumulative_volume, cumulative_value, session_rise=rise,
    )

def interest_decision(bars: Sequence[Any], *, now: datetime) -> StageDecision:
    today = _today(bars, now)
    if len(today) < 2:
        return StageDecision(False, "당일1분봉2개미확보", route="interest")
    previous, current = today[-2], today[-1]
    _po, _ph, _pl, pc, pv, _pr = _shape(previous)
    co, _ch, cl, cc, cv, _cr = _shape(current)
    session_open = float(getattr(today[0], "open", 0.0) or 0.0)
    session_high = max(float(getattr(bar, "high", 0.0) or 0.0) for bar in today)
    rise = session_high / session_open - 1.0 if session_open > 0 else 0.0
    ratio = cv / max(1.0, pv)
    declining_bear = cc < co and cc < pc
    open_held = cl + 1e-12 >= session_open
    allowed = rise + 1e-12 >= OBS_SESSION_RISE and declining_bear and open_held and ratio + 1e-12 >= INTEREST_BEAR_VOLUME_RATIO
    missing = []
    if rise < OBS_SESSION_RISE:
        missing.append("시가대비+5%상승이력없음")
    if not declining_bear:
        missing.append("하락중음봉아님")
    if not open_held:
        missing.append("당일시가이탈")
    if ratio < INTEREST_BEAR_VOLUME_RATIO:
        missing.append(f"음봉거래x{ratio:.2f}_3배미달")
    reason = (
        f"시가대비최고{rise:.2%}후하락·시가지지·음봉거래x{ratio:.2f}"
        if allowed else "·".join(missing)
    )
    return StageDecision(allowed, reason, 95.0 if allowed else 0.0, cc, "interest")


def candidate_decision(flush: Any, current: Any, bars: Sequence[Any] = ()) -> StageDecision:
    fo, fh, fl, fc, fv, fr = _shape(flush)
    co, ch, cl, cc, cv, cr = _shape(current)
    del fo, fh, fl, fc, ch
    dry_ratio = cv / max(1.0, fv)
    body = abs(cc - co)
    lower_wick = min(co, cc) - cl
    wick = lower_wick >= max(body * 0.30, cr * 0.08)
    short = cr <= max(1e-9, fr) * SHORT_RANGE_VS_FLUSH
    short_bull = cc > co and short
    ma5_reclaim = False
    if len(bars) >= 5:
        closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in bars[-5:]]
        ma5_reclaim = cc > co and cc >= mean(closes)
    shape_ok = wick or short or short_bull or ma5_reclaim
    allowed = dry_ratio <= DRY_VS_FLUSH and shape_ok
    signs = [
        name for name, ok in (
            ("아래꼬리", wick), ("짧은봉", short),
            ("짧은양봉", short_bull), ("1분MA5회복", ma5_reclaim),
        ) if ok
    ]
    reason = (
        f"기준음봉대비거래{dry_ratio:.0%}·{'/'.join(signs)}"
        if allowed else
        f"거래{dry_ratio:.0%}{'_50%초과' if dry_ratio > DRY_VS_FLUSH else ''}"
        f"·형태신호{'없음' if not shape_ok else '있음'}"
    )
    return StageDecision(allowed, reason, 112.0 if allowed else 0.0, cc, "candidate")


def exhaustion_entry_decision(
    flush: Any, after_flush: Sequence[Any], *, now: datetime,
) -> StageDecision:
    del now
    if not (EXHAUSTION_MIN_BARS <= len(after_flush) <= EXHAUSTION_MAX_BARS):
        return StageDecision(
            False, f"기준음봉후{len(after_flush)}봉_필요5~10봉", route="exhaustion",
        )
    _fo, _fh, _fl, _fc, fv, fr = _shape(flush)
    current = after_flush[-1]
    co, _ch, cl, cc, cv, cr = _shape(current)
    recent = list(after_flush[-EXHAUSTION_MAX_BARS:])
    average_ratio = mean(_shape(bar)[4] for bar in recent) / max(1.0, fv)
    current_ratio = cv / max(1.0, fv)
    body = abs(cc - co)
    lower_wick = min(co, cc) - cl
    wick = lower_wick >= max(body * 0.30, cr * 0.08)
    short = cr <= max(1e-9, fr) * SHORT_RANGE_VS_FLUSH
    short_bull = cc > co and short
    allowed = (
        current_ratio <= DRY_VS_FLUSH
        and average_ratio <= EXHAUSTION_AVG_VS_FLUSH
        and (wick or short or short_bull)
    )
    signs = "/".join(name for name, ok in (
        ("아래꼬리", wick), ("짧은봉", short), ("짧은양봉", short_bull),
    ) if ok)
    reason = (
        f"기준음봉대비현재거래{current_ratio:.0%}·후속{len(recent)}봉평균{average_ratio:.0%}"
        f"·{signs}"
        if allowed else
        "·".join(filter(None, (
            f"현재거래{current_ratio:.0%}_50%초과" if current_ratio > DRY_VS_FLUSH else "",
            f"후속평균{average_ratio:.0%}_30%초과" if average_ratio > EXHAUSTION_AVG_VS_FLUSH else "",
            "아래꼬리·짧은봉·짧은양봉없음" if not (wick or short or short_bull) else "",
        ))) or "소진확인대기"
    )
    return StageDecision(allowed, reason, 128.0 if allowed else 0.0, cc, "exhaustion")


def bullish_ma5_settle(bars: Sequence[Any]) -> StageDecision:
    if len(bars) < 6:
        return StageDecision(False, "1분봉6개미확보", route="ma5_completion")
    closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in bars]
    current_open = float(getattr(bars[-1], "open", 0.0) or 0.0)
    previous_ma5 = mean(closes[-6:-1])
    current_ma5 = mean(closes[-5:])
    allowed = closes[-1] > current_open and closes[-2] >= previous_ma5 and closes[-1] >= current_ma5
    reason = (
        "양봉·1분MA5위2봉안착"
        if allowed else
        f"MA5안착대기 전봉{closes[-2]:.0f}/{previous_ma5:.0f}·현재{closes[-1]:.0f}/{current_ma5:.0f}"
    )
    return StageDecision(allowed, reason, 130.0 if allowed else 0.0, closes[-1], "ma5_completion")


def _body_quality(bar: Any) -> tuple[bool, float]:
    open_, high, low, close, _volume, span = _shape(bar)
    if span <= 0:
        return False, 0.0
    body = close - open_
    upper = high - max(open_, close)
    ratio = body / span
    return body > 0 and ratio >= GENERAL_BODY_RATIO and upper <= body * GENERAL_UPPER_WICK_BODY, ratio


def general_breakout_decision(bars: Sequence[Any], *, now: datetime) -> StageDecision:
    today = _today(bars, now)
    if len(today) < GENERAL_LOOKBACK_HIGH + 1:
        return StageDecision(False, "일반돌파표본11봉미달", route="general_breakout")
    current = today[-1]
    open_, high, low, close, volume, _span = _shape(current)
    del high, low
    prior = today[-(GENERAL_LOOKBACK_HIGH + 1):-1]
    resistance = max(float(getattr(bar, "high", 0.0) or 0.0) for bar in prior)
    volume_base = mean(_shape(bar)[4] for bar in today[-(GENERAL_VOLUME_MA_PERIOD + 1):-1])
    volume_ratio = volume / max(1.0, volume_base)
    closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in today]
    ma5_now = mean(closes[-5:])
    ma5_before = mean(closes[-6:-1])
    required_break = resistance + _stock_tick_size(resistance) * GENERAL_MIN_BREAK_TICKS
    price_break = close + 1e-12 >= required_break
    ma5_cross = closes[-2] < ma5_before and close >= ma5_now
    body_ok, body_ratio = _body_quality(current)
    allowed = (
        _stamp(current).time() <= time(9, 40)
        and (price_break or ma5_cross)
        and volume_ratio >= GENERAL_VOLUME_MULTIPLIER
        and body_ok
    )
    reason = (
        f"{'10봉고점돌파' if price_break else 'MA5상향돌파'}·거래x{volume_ratio:.2f}"
        f"·몸통비{body_ratio:.0%}"
        if allowed else
        "·".join(filter(None, (
            "09:40이후일반돌파종료" if _stamp(current).time() > time(9, 40) else "",
            "고점·MA5미돌파" if not (price_break or ma5_cross) else "",
            f"거래x{volume_ratio:.2f}_1.8미달" if volume_ratio < GENERAL_VOLUME_MULTIPLIER else "",
            f"몸통·윗꼬리불량({body_ratio:.0%})" if not body_ok else "",
        ))) or "일반돌파대기"
    )
    return StageDecision(
        allowed, reason, 126.0 if allowed else 0.0, close,
        "general_breakout", len(today) - 1,
    )


def dry_breakout_decision(bars: Sequence[Any], *, now: datetime) -> StageDecision:
    today = _today(bars, now)
    needed = max(DRY_LOOKBACK + 1, DRY_CONSOLIDATION_BARS + 2)
    if len(today) < needed:
        return StageDecision(False, f"마른돌파표본{needed}봉미달", route="dry_breakout")
    current = today[-1]
    open_, high, low, close, volume, span = _shape(current)
    recent_dry = today[-(DRY_LOOKBACK + 1):-1]
    dry_volumes = [_shape(bar)[4] for bar in recent_dry]
    dry_average = mean(dry_volumes)
    earlier = today[:-DRY_LOOKBACK]
    explosion_average = max(
        max((_shape(bar)[4] for bar in earlier[-20:]), default=0.0),
        mean((_shape(bar)[4] for bar in earlier[-20:])) if earlier[-20:] else 0.0,
    )
    dry_enough = (
        explosion_average > 0
        and dry_average <= explosion_average * DRY_VOLUME_RATIO
        and max(dry_volumes, default=0.0) <= dry_average * DRY_MAX_BAR_TO_AVG
    )
    prior_six = today[-(DRY_CONSOLIDATION_BARS + 1):-1]
    resistance = max(float(getattr(bar, "high", 0.0) or 0.0) for bar in prior_six)
    closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in today]
    ma5_now = mean(closes[-5:])
    ma5_before = mean(closes[-6:-1])
    price_break = close > resistance
    ma5_reclaim = closes[-2] < ma5_before and close >= ma5_now
    volume_ratio = volume / max(1.0, dry_average)
    location = (close - low) / span if span > 0 else 0.0
    allowed = (
        _stamp(current).time() > time(9, 40)
        and dry_enough
        and (price_break or ma5_reclaim)
        and volume_ratio >= DRY_BREAK_VOLUME_MULTIPLIER
        and close > open_
        and location >= DRY_CLOSE_LOCATION
    )
    reason = (
        f"8봉마른거래{dry_average/max(1.0, explosion_average):.0%}"
        f"·{'6봉고점돌파' if price_break else 'MA5회복'}"
        f"·거래x{volume_ratio:.2f}·종가위치{location:.0%}"
        if allowed else
        "·".join(filter(None, (
            "09:40이전은일반돌파사용" if _stamp(current).time() <= time(9, 40) else "",
            "8봉거래마름부족" if not dry_enough else "",
            "6봉고점·MA5미돌파" if not (price_break or ma5_reclaim) else "",
            f"거래x{volume_ratio:.2f}_2.2미달" if volume_ratio < DRY_BREAK_VOLUME_MULTIPLIER else "",
            "양봉아님" if close <= open_ else "",
            f"종가위치{location:.0%}_65%미달" if location < DRY_CLOSE_LOCATION else "",
        ))) or "마른돌파대기"
    )
    return StageDecision(
        allowed, reason, 134.0 if allowed else 0.0, close,
        "dry_breakout", len(today) - 1,
    )


def breakout_decision(bars: Sequence[Any], *, now: datetime) -> StageDecision:
    current = _today(bars, now)
    if not current:
        return StageDecision(False, "당일봉없음", route="breakout")
    if _stamp(current[-1]).time() <= time(9, 40):
        return general_breakout_decision(bars, now=now)
    return dry_breakout_decision(bars, now=now)
