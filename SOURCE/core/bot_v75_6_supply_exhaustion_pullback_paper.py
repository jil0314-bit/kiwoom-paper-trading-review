#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.6 SUPPLY-EXHAUSTION-PULLBACK PAPER
======================================

V75.3/V75.4/V75.5 원본을 수정하지 않고, V75.5 위에 사용자가 설명한
'1차 상승 -> 조정 중 대량 음봉(이익실현/악성매물/투매 가능성) ->
후속 거래절벽 소형봉에서 매도압력 소진 확인' 패턴을 추가한다.

중요한 해석
- 대량 음봉 하나만으로 "매도가 끝났다"고 단정하지 않는다.
- 대량 음봉 뒤 거래량 급감, 변동폭 축소, 저점 미이탈, 하락가속 정지를
  함께 확인한 뒤에만 PAPER 매수 신호를 만든다.
- 전고점/저항 부근에서는 V75.5의 거래폭발 돌파 게이트를 그대로 통과해야 한다.
- 신호 신선도, 유동성, 손실한도, 연속손실, 체결 로직은 V75.4/V75.3을 그대로 쓴다.
- 강한 테마/대장주는 현재 API에 정확한 테마 분류가 없으므로 후보순위·후보원 중복·쌍끌이를
  "시장주도 프록시"로 점수에만 가산한다. 환경변수로 수동 테마·대장 종목을 지정할 수 있다.
- 실제 주문 기능 없음. PAPER 전용.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import logging
import math
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

VERSION = "V75.6-SUPPLY-EXHAUSTION-PULLBACK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V755_FILENAME = "bot_v75_5_resistance_explosion_guard_paper.py"
V755_SHA256 = "9bf34be694d3dc079736420718c585558b1eaef7e2a54f996e0810c145eeb2c3"

FORBIDDEN_ORDER_NAMES = {
    "send_order",
    "create_order",
    "place_order",
    "order_stock",
    "submit_order",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v755() -> ModuleType:
    path = BASE_DIR / V755_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V755_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V755_SHA256:
        raise RuntimeError(
            "V75.5 SHA256 불일치 — 검증된 전고점 방어판이 아니므로 중단합니다.\n"
            f"기대: {V755_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v755_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.5 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v755 = load_v755()
v754 = v755.v754
v753 = v755.v753
KST = v755.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise v753.ConfigurationError(f"{name}는 숫자여야 합니다: {raw!r}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise v753.ConfigurationError(f"{name} 범위는 {minimum}~{maximum}: {value}")
    return value


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise v753.ConfigurationError(f"{name}는 정수여야 합니다: {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise v753.ConfigurationError(f"{name} 범위는 {minimum}~{maximum}: {value}")
    return value


@dataclass(frozen=True, slots=True)
class SupplyExhaustionConfig:
    # 1차 상승 확인
    rise_lookback_bars: int = 18
    min_prior_rise_pct: float = 0.025
    max_peak_age_bars: int = 10
    min_retrace_of_rise: float = 0.12
    max_retrace_of_rise: float = 0.68

    # 조정 중 매물출회 대량 음봉
    flush_search_bars: int = 4
    flush_left_volume_bars: int = 12
    flush_min_bear_body_ratio: float = 0.35
    flush_min_drop_pct: float = 0.004
    flush_volume_vs_median: float = 2.00
    flush_volume_vs_p75: float = 1.30
    flush_volume_vs_recent_max: float = 1.05

    # 후속 거래절벽 소형봉
    dry_max_volume_vs_flush: float = 0.45
    dry_max_volume_vs_left_median: float = 0.75
    dry_max_volume_vs_previous: float = 0.80
    dry_max_range_vs_flush: float = 0.65
    dry_max_bear_body_ratio: float = 0.40
    dry_max_bull_body_ratio: float = 0.60
    dry_min_close_location: float = 0.25
    dry_max_intermediate_volume_vs_flush: float = 0.70
    max_flush_low_break_pct: float = 0.003
    max_structure_stop_pct: float = 0.025
    stop_loss_pct: float = 0.015
    blocked_cooldown_sec: float = 90.0

    # 시장 주도력 보강. 정확한 "테마 대장" 판별 데이터가 없을 때는
    # 후보 순위·후보원 중복·쌍끌이를 시장주도 프록시로만 사용한다.
    leader_top_pct: float = 0.15
    leader_strong_top_pct: float = 0.08
    leader_min_sources: int = 2
    leader_bonus_strong: float = 8.0
    leader_bonus_moderate: float = 4.0
    manual_theme_bonus: float = 5.0
    manual_leader_bonus: float = 10.0
    require_leader_proxy: int = 0
    weak_leader_max_dry_vs_flush: float = 0.35
    weak_leader_min_flush_vs_median: float = 2.30

    @classmethod
    def load(cls) -> "SupplyExhaustionConfig":
        return cls(
            rise_lookback_bars=env_int("V756_RISE_LOOKBACK_BARS", 18, minimum=8, maximum=60),
            min_prior_rise_pct=env_float("V756_MIN_PRIOR_RISE_PCT", 0.025, minimum=0.008, maximum=0.15),
            max_peak_age_bars=env_int("V756_MAX_PEAK_AGE_BARS", 10, minimum=2, maximum=30),
            min_retrace_of_rise=env_float("V756_MIN_RETRACE_OF_RISE", 0.12, minimum=0.0, maximum=0.60),
            max_retrace_of_rise=env_float("V756_MAX_RETRACE_OF_RISE", 0.68, minimum=0.30, maximum=1.05),
            flush_search_bars=env_int("V756_FLUSH_SEARCH_BARS", 4, minimum=1, maximum=8),
            flush_left_volume_bars=env_int("V756_FLUSH_LEFT_VOLUME_BARS", 12, minimum=6, maximum=30),
            flush_min_bear_body_ratio=env_float("V756_FLUSH_MIN_BEAR_BODY", 0.35, minimum=0.15, maximum=0.90),
            flush_min_drop_pct=env_float("V756_FLUSH_MIN_DROP_PCT", 0.004, minimum=0.001, maximum=0.05),
            flush_volume_vs_median=env_float("V756_FLUSH_VOL_VS_MEDIAN", 2.00, minimum=1.20, maximum=8.0),
            flush_volume_vs_p75=env_float("V756_FLUSH_VOL_VS_P75", 1.30, minimum=1.0, maximum=5.0),
            flush_volume_vs_recent_max=env_float("V756_FLUSH_VOL_VS_RECENT_MAX", 1.05, minimum=0.70, maximum=3.0),
            dry_max_volume_vs_flush=env_float("V756_DRY_MAX_VOL_VS_FLUSH", 0.45, minimum=0.10, maximum=0.90),
            dry_max_volume_vs_left_median=env_float("V756_DRY_MAX_VOL_VS_LEFT_MEDIAN", 0.75, minimum=0.20, maximum=2.0),
            dry_max_volume_vs_previous=env_float("V756_DRY_MAX_VOL_VS_PREVIOUS", 0.80, minimum=0.20, maximum=1.50),
            dry_max_range_vs_flush=env_float("V756_DRY_MAX_RANGE_VS_FLUSH", 0.65, minimum=0.20, maximum=1.20),
            dry_max_bear_body_ratio=env_float("V756_DRY_MAX_BEAR_BODY", 0.40, minimum=0.10, maximum=0.80),
            dry_max_bull_body_ratio=env_float("V756_DRY_MAX_BULL_BODY", 0.60, minimum=0.10, maximum=0.95),
            dry_min_close_location=env_float("V756_DRY_MIN_CLOSE_LOCATION", 0.25, minimum=0.0, maximum=0.80),
            dry_max_intermediate_volume_vs_flush=env_float("V756_DRY_MAX_INTERMEDIATE_VOL", 0.70, minimum=0.20, maximum=1.20),
            max_flush_low_break_pct=env_float("V756_MAX_FLUSH_LOW_BREAK_PCT", 0.003, minimum=0.0, maximum=0.03),
            max_structure_stop_pct=env_float("V756_MAX_STRUCTURE_STOP_PCT", 0.025, minimum=0.005, maximum=0.08),
            stop_loss_pct=env_float("V756_STOP_LOSS_PCT", 0.015, minimum=0.005, maximum=0.05),
            blocked_cooldown_sec=env_float("V756_BLOCKED_COOLDOWN_SEC", 90.0, minimum=15.0, maximum=1800.0),
            leader_top_pct=env_float("V756_LEADER_TOP_PCT", 0.15, minimum=0.02, maximum=0.50),
            leader_strong_top_pct=env_float("V756_LEADER_STRONG_TOP_PCT", 0.08, minimum=0.01, maximum=0.30),
            leader_min_sources=env_int("V756_LEADER_MIN_SOURCES", 2, minimum=1, maximum=3),
            leader_bonus_strong=env_float("V756_LEADER_BONUS_STRONG", 8.0, minimum=0.0, maximum=30.0),
            leader_bonus_moderate=env_float("V756_LEADER_BONUS_MODERATE", 4.0, minimum=0.0, maximum=20.0),
            manual_theme_bonus=env_float("V756_MANUAL_THEME_BONUS", 5.0, minimum=0.0, maximum=30.0),
            manual_leader_bonus=env_float("V756_MANUAL_LEADER_BONUS", 10.0, minimum=0.0, maximum=40.0),
            require_leader_proxy=env_int("V756_REQUIRE_LEADER_PROXY", 0, minimum=0, maximum=1),
            weak_leader_max_dry_vs_flush=env_float("V756_WEAK_LEADER_MAX_DRY_VS_FLUSH", 0.35, minimum=0.10, maximum=0.70),
            weak_leader_min_flush_vs_median=env_float("V756_WEAK_LEADER_MIN_FLUSH_VS_MEDIAN", 2.30, minimum=1.20, maximum=8.0),
        )


@dataclass(frozen=True, slots=True)
class SupplyExhaustionEvidence:
    rise_low: float
    rise_high: float
    rise_pct: float
    retrace: float
    flush_time: datetime
    flush_low: float
    flush_volume: float
    flush_volume_vs_median: float
    flush_volume_vs_p75: float
    flush_volume_vs_recent_max: float
    dry_volume_vs_flush: float
    dry_volume_vs_left_median: float
    dry_volume_vs_previous: float
    dry_range_vs_flush: float
    current_close: float
    structure_stop_pct: float


@dataclass(frozen=True, slots=True)
class LeadershipEvidence:
    tier: str
    bonus: float
    rank: int | None
    total: int
    rank_pct: float | None
    source_count: int
    source: str
    dual_buy: bool
    manual_theme: bool
    manual_leader: bool


def _symbol_set_from_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def assess_leadership_proxy(
    symbol: str,
    *,
    meta: Any | None,
    universe: Iterable[Any],
    config: SupplyExhaustionConfig,
    manual_theme_symbols: set[str] | None = None,
    manual_leader_symbols: set[str] | None = None,
) -> LeadershipEvidence:
    """실제 테마 분류가 없을 때 사용할 시장 주도력 보조지표다.

    이것은 정확한 테마 대장 판별이 아니다. 후보 순위, 후보원 중복,
    외국인·기관 동시 순매수를 이용해 신뢰도만 가산한다.
    """
    pool = list(universe)
    rank: int | None = None
    for idx, item in enumerate(pool, start=1):
        if getattr(item, "symbol", "") == symbol:
            rank = idx
            break
    total = len(pool)
    rank_pct = (rank / total) if rank is not None and total > 0 else None
    source = str(getattr(meta, "source", "") or "")
    sources = {part.strip() for part in source.split("+") if part.strip()}
    source_count = len(sources)
    dual_buy = bool(getattr(meta, "dual_buy", False))
    manual_theme = symbol in (manual_theme_symbols or set())
    manual_leader = symbol in (manual_leader_symbols or set())

    if manual_leader:
        tier = "수동지정대장"
        bonus = config.manual_leader_bonus
    elif manual_theme:
        tier = "수동지정강한테마"
        bonus = config.manual_theme_bonus
    elif (
        rank_pct is not None
        and rank_pct <= config.leader_strong_top_pct
        and (source_count >= config.leader_min_sources or dual_buy)
    ):
        tier = "시장주도강"
        bonus = config.leader_bonus_strong
    elif (
        (rank_pct is not None and rank_pct <= config.leader_top_pct)
        or source_count >= config.leader_min_sources
        or dual_buy
    ):
        tier = "시장주도중"
        bonus = config.leader_bonus_moderate
    else:
        tier = "시장주도보통"
        bonus = 0.0

    return LeadershipEvidence(
        tier=tier,
        bonus=bonus,
        rank=rank,
        total=total,
        rank_pct=rank_pct,
        source_count=source_count,
        source=source,
        dual_buy=dual_buy,
        manual_theme=manual_theme,
        manual_leader=manual_leader,
    )


def leadership_adjusted_pattern_allowed(
    leadership: LeadershipEvidence,
    supply: SupplyExhaustionEvidence,
    config: SupplyExhaustionConfig,
) -> tuple[bool, str]:
    """주도력이 약하면 매물소진 증거를 한 단계 더 강하게 요구한다."""
    if config.require_leader_proxy and leadership.bonus <= 0:
        return False, "시장주도프록시필수미달"
    if leadership.bonus > 0:
        return True, leadership.tier
    if supply.dry_volume_vs_flush > config.weak_leader_max_dry_vs_flush:
        return False, (
            f"비주도주거래절벽부족_x{supply.dry_volume_vs_flush:.2f}>"
            f"{config.weak_leader_max_dry_vs_flush:.2f}"
        )
    if supply.flush_volume_vs_median < config.weak_leader_min_flush_vs_median:
        return False, (
            f"비주도주대량음봉약함_x{supply.flush_volume_vs_median:.2f}<"
            f"{config.weak_leader_min_flush_vs_median:.2f}"
        )
    return True, "비주도주지만매물소진증거강함"


def _normalize_kst(value: datetime) -> datetime:
    return v754.normalize_kst(value)


def _same_session_bars(bars: Iterable[Any]) -> list[Any]:
    items = list(bars)
    if not items:
        return []
    last_time = _normalize_kst(items[-1].timestamp)
    last_date = last_time.date()
    try:
        last_segment = v753.session_segment(last_time)
    except Exception:
        last_segment = ""
    same: list[Any] = []
    for bar in items:
        bar_time = _normalize_kst(bar.timestamp)
        if bar_time.date() != last_date:
            continue
        if last_segment:
            try:
                if v753.session_segment(bar_time) != last_segment:
                    continue
            except Exception:
                continue
        same.append(bar)
    return same


def _percentile75(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(0.75 * len(ordered))) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _candle_metrics(bar: Any) -> tuple[float, float, float, float]:
    high = float(bar.high)
    low = float(bar.low)
    open_price = float(bar.open)
    close = float(bar.close)
    candle_range = max(1e-9, high - low)
    signed_body = (close - open_price) / candle_range
    absolute_body = abs(close - open_price) / candle_range
    close_location = (close - low) / candle_range
    return candle_range, signed_body, absolute_body, close_location


def detect_supply_exhaustion_pullback(
    strategy: Any,
    symbol: str,
    *,
    config: SupplyExhaustionConfig,
) -> tuple[Any | None, str, SupplyExhaustionEvidence | None]:
    """대량 음봉 뒤 거래절벽·저점방어가 확인된 조정만 BUY 신호로 만든다."""
    bars = _same_session_bars(strategy.bars.get(symbol, []))
    minimum = max(
        config.rise_lookback_bars + 3,
        config.flush_left_volume_bars + 3,
        16,
    )
    if len(bars) < minimum:
        return None, f"매물소진자료부족_{len(bars)}<{minimum}", None

    current = bars[-1]
    current_range, current_signed_body, current_abs_body, current_close_location = _candle_metrics(current)
    current_close = float(current.close)
    if current_close <= 0 or float(current.volume) <= 0:
        return None, "현재봉가격거래오류", None

    first_flush = max(1, len(bars) - 1 - config.flush_search_bars)
    for flush_index in range(len(bars) - 2, first_flush - 1, -1):
        flush = bars[flush_index]
        previous = bars[flush_index - 1]
        after = bars[flush_index + 1:]
        if not 1 <= len(after) <= config.flush_search_bars:
            continue

        flush_range, flush_signed_body, flush_abs_body, _ = _candle_metrics(flush)
        drop_pct = float(flush.close) / max(0.01, float(previous.close)) - 1.0
        if float(flush.close) >= float(flush.open):
            continue
        if -flush_signed_body < config.flush_min_bear_body_ratio:
            continue
        if drop_pct > -config.flush_min_drop_pct:
            continue

        left = bars[max(0, flush_index - config.flush_left_volume_bars):flush_index]
        if len(left) < 6:
            continue
        left_volumes = [max(0.0, float(item.volume)) for item in left]
        left_median = statistics.median(left_volumes)
        left_p75 = _percentile75(left_volumes)
        recent_max = max(left_volumes[-min(5, len(left_volumes)):])
        flush_volume = max(0.0, float(flush.volume))
        vol_vs_median = flush_volume / max(1.0, left_median)
        vol_vs_p75 = flush_volume / max(1.0, left_p75)
        vol_vs_recent_max = flush_volume / max(1.0, recent_max)
        if vol_vs_median < config.flush_volume_vs_median:
            continue
        if vol_vs_p75 < config.flush_volume_vs_p75:
            continue
        if vol_vs_recent_max < config.flush_volume_vs_recent_max:
            continue

        rise_window = bars[max(0, flush_index - config.rise_lookback_bars):flush_index]
        if len(rise_window) < 6:
            continue
        peak_pos = max(range(len(rise_window)), key=lambda i: float(rise_window[i].high))
        peak_age = len(rise_window) - 1 - peak_pos
        if peak_age > config.max_peak_age_bars:
            continue
        prior_to_peak = rise_window[:peak_pos + 1]
        if len(prior_to_peak) < 3:
            continue
        rise_low = min(float(item.low) for item in prior_to_peak)
        rise_high = float(rise_window[peak_pos].high)
        rise_span = rise_high - rise_low
        rise_pct = rise_high / max(0.01, rise_low) - 1.0
        if rise_pct < config.min_prior_rise_pct or rise_span <= 0:
            continue

        pullback_low = min(float(item.low) for item in bars[flush_index:])
        retrace = (rise_high - pullback_low) / max(1.0, rise_span)
        if not config.min_retrace_of_rise <= retrace <= config.max_retrace_of_rise:
            continue
        if current_close <= rise_low:
            continue

        # 대량 음봉 뒤 다시 큰 매도봉이 나오면 "매물 소진"이 아니라 매도 지속으로 본다.
        continued_distribution = False
        for item in after:
            _, signed_body, _, _ = _candle_metrics(item)
            if signed_body <= -config.flush_min_bear_body_ratio and float(item.volume) >= flush_volume * config.dry_max_intermediate_volume_vs_flush:
                continued_distribution = True
                break
        if continued_distribution:
            continue

        if min(float(item.low) for item in after) < float(flush.low) * (1.0 - config.max_flush_low_break_pct):
            continue

        dry_volume_vs_flush = float(current.volume) / max(1.0, flush_volume)
        dry_volume_vs_left_median = float(current.volume) / max(1.0, left_median)
        previous_after = after[-2] if len(after) >= 2 else flush
        dry_volume_vs_previous = float(current.volume) / max(1.0, float(previous_after.volume))
        dry_range_vs_flush = current_range / max(1e-9, flush_range)
        if dry_volume_vs_flush > config.dry_max_volume_vs_flush:
            continue
        if dry_volume_vs_left_median > config.dry_max_volume_vs_left_median:
            continue
        if dry_volume_vs_previous > config.dry_max_volume_vs_previous:
            continue
        if dry_range_vs_flush > config.dry_max_range_vs_flush:
            continue
        if current_close_location < config.dry_min_close_location:
            continue
        if current_signed_body < 0 and current_abs_body > config.dry_max_bear_body_ratio:
            continue
        if current_signed_body >= 0 and current_abs_body > config.dry_max_bull_body_ratio:
            continue

        structure_stop_pct = (current_close - float(flush.low)) / max(0.01, current_close)
        if structure_stop_pct <= -config.max_flush_low_break_pct:
            continue
        if structure_stop_pct > config.max_structure_stop_pct:
            continue

        evidence = SupplyExhaustionEvidence(
            rise_low=rise_low,
            rise_high=rise_high,
            rise_pct=rise_pct,
            retrace=retrace,
            flush_time=_normalize_kst(flush.timestamp),
            flush_low=float(flush.low),
            flush_volume=flush_volume,
            flush_volume_vs_median=vol_vs_median,
            flush_volume_vs_p75=vol_vs_p75,
            flush_volume_vs_recent_max=vol_vs_recent_max,
            dry_volume_vs_flush=dry_volume_vs_flush,
            dry_volume_vs_left_median=dry_volume_vs_left_median,
            dry_volume_vs_previous=dry_volume_vs_previous,
            dry_range_vs_flush=dry_range_vs_flush,
            current_close=current_close,
            structure_stop_pct=structure_stop_pct,
        )

        score = 116.0
        score += min(8.0, max(0.0, vol_vs_median - config.flush_volume_vs_median) * 2.0)
        score += min(6.0, max(0.0, config.dry_max_volume_vs_flush - dry_volume_vs_flush) * 10.0)
        score += 3.0 if current_signed_body >= 0 else 0.0
        score += 3.0 if current_close_location >= 0.60 else 0.0
        reason = (
            f"매물소진눌림|1차상승={rise_pct:.2%}|조정={retrace:.0%}"
            f"|대량음봉={evidence.flush_time:%H:%M} 거래중앙x{vol_vs_median:.2f}"
            f"/상위25%x{vol_vs_p75:.2f}/최근최대x{vol_vs_recent_max:.2f}"
            f"|거래절벽=대량봉x{dry_volume_vs_flush:.2f}/왼쪽중앙x{dry_volume_vs_left_median:.2f}"
            f"/직전봉x{dry_volume_vs_previous:.2f}"
            f"|폭축소=x{dry_range_vs_flush:.2f}|저점방어={float(flush.low):.0f}"
            f"|확인봉={'양봉' if current_signed_body >= 0 else '소형음봉'}"
        )
        structure_buffered_stop = min(
            config.max_structure_stop_pct,
            max(0.006, structure_stop_pct + 0.002),
        )
        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            score,
            reason,
            current_close,
            current.timestamp,
            "매물소진눌림1차",
            structure_buffered_stop,
            getattr(strategy, "take_profit_pct", 0.015),
        )
        return signal, "매물소진확인", evidence

    return None, "매물소진조건없음", None


class SupplyExhaustionEngine(v755.ResistanceGuardEngine):
    """V75.5 위에 대량 음봉 후 거래절벽 매수 패턴을 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.supply_exhaustion_config = SupplyExhaustionConfig.load()
        self._supply_evidence: dict[str, SupplyExhaustionEvidence] = {}
        self._leadership_evidence: dict[str, LeadershipEvidence] = {}
        self._supply_block_until: dict[str, float] = {}
        self._manual_theme_symbols = _symbol_set_from_env("V756_STRONG_THEME_SYMBOLS")
        self._manual_leader_symbols = _symbol_set_from_env("V756_THEME_LEADER_SYMBOLS")
        self._install_supply_exhaustion_signal()
        cfg = self.supply_exhaustion_config
        self.logger.warning(
            "[%s] 활성 | 대량음봉 중앙x%.2f/상위25%%x%.2f/최근최대x%.2f | "
            "후속거래<=대량봉x%.2f/왼쪽중앙x%.2f/직전봉x%.2f | 폭<=x%.2f | 저점이탈<=%.2f%%",
            VERSION,
            cfg.flush_volume_vs_median,
            cfg.flush_volume_vs_p75,
            cfg.flush_volume_vs_recent_max,
            cfg.dry_max_volume_vs_flush,
            cfg.dry_max_volume_vs_left_median,
            cfg.dry_max_volume_vs_previous,
            cfg.dry_max_range_vs_flush,
            cfg.max_flush_low_break_pct * 100,
        )

    def _apply_leadership_confidence(
        self, signal: Any, supply: SupplyExhaustionEvidence
    ) -> Any | None:
        meta = self.candidate_meta.get(signal.symbol)
        evidence = assess_leadership_proxy(
            signal.symbol,
            meta=meta,
            universe=self.universe_pool,
            config=self.supply_exhaustion_config,
            manual_theme_symbols=self._manual_theme_symbols,
            manual_leader_symbols=self._manual_leader_symbols,
        )
        self._leadership_evidence[signal.symbol] = evidence
        allowed, leadership_reason = leadership_adjusted_pattern_allowed(
            evidence, supply, self.supply_exhaustion_config
        )
        if not allowed:
            self.logger.info("매물소진후보제외 %s %s", self._label(signal.symbol), leadership_reason)
            return None
        rank_text = f"{evidence.rank}/{evidence.total}" if evidence.rank is not None else "순위없음"
        reason = (
            f"{signal.reason}|주도력={evidence.tier}"
            f"|후보순위={rank_text}|후보원={evidence.source_count}개"
            f"|쌍끌이={'예' if evidence.dual_buy else '아니오'}"
            f"|주도력판정={leadership_reason}"
        )
        return v753.Signal(
            signal.symbol,
            signal.action,
            signal.score + evidence.bonus,
            reason,
            signal.reference_price,
            signal.timestamp,
            signal.pattern,
            signal.stop_loss_pct,
            signal.take_profit_pct,
        )

    def _install_supply_exhaustion_signal(self) -> None:
        original_on_bar = self.strategy.on_bar
        engine = self

        def wrapped_on_bar(bar: Any, position: Any) -> Any:
            base_signal = original_on_bar(bar, position)
            if position is not None or base_signal.action == v753.SignalAction.SELL:
                return base_signal
            # 기존 V75.3이 이미 유효한 BUY를 만들면 기존 검증된 패턴을 우선 보존한다.
            if base_signal.action == v753.SignalAction.BUY:
                return base_signal

            signal, _, evidence = detect_supply_exhaustion_pullback(
                engine.strategy,
                bar.symbol,
                config=engine.supply_exhaustion_config,
            )
            if signal is None or evidence is None:
                return base_signal

            signal = engine._apply_leadership_confidence(signal, evidence)
            if signal is None:
                return base_signal
            engine._supply_evidence[bar.symbol] = evidence
            # V75.4의 동일조회주기 지연신호 차단에도 새 패턴을 포함한다.
            fresh, _, _ = v754.signal_freshness(
                signal,
                max_age_sec=engine.max_entry_signal_age_sec,
            )
            if not fresh:
                engine._batch_stale_buy.add(signal.symbol)
                previous = engine._stale_reject_bar_time.get(signal.symbol)
                bar_time = v754.normalize_kst(signal.timestamp)
                if previous is None or bar_time > previous:
                    engine._stale_reject_bar_time[signal.symbol] = bar_time
            return signal

        self.strategy.on_bar = wrapped_on_bar

    def _supply_entry_allowed(self, signal: Any, current_price: float | None = None) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") != "매물소진눌림1차":
            return True, "매물소진패턴아님"

        block_until = self._supply_block_until.get(signal.symbol, 0.0)
        now_mono = time.monotonic()
        if now_mono < block_until:
            return False, f"매물소진차단냉각_{block_until - now_mono:.0f}초"
        if block_until:
            self._supply_block_until.pop(signal.symbol, None)

        evidence = self._supply_evidence.get(signal.symbol)
        if evidence is None:
            check_signal, _, evidence = detect_supply_exhaustion_pullback(
                self.strategy,
                signal.symbol,
                config=self.supply_exhaustion_config,
            )
            if check_signal is None or evidence is None:
                return False, "매물소진구조재검증실패"
            self._supply_evidence[signal.symbol] = evidence

        bars = list(self.strategy.bars.get(signal.symbol, []))
        if not bars:
            return False, "매물소진분봉없음"
        latest = bars[-1]
        guard_low = evidence.flush_low * (1.0 - self.supply_exhaustion_config.max_flush_low_break_pct)
        observed_price = float(current_price) if current_price is not None else float(latest.close)
        if float(latest.low) < guard_low or observed_price < guard_low:
            self._supply_block_until[signal.symbol] = (
                now_mono + self.supply_exhaustion_config.blocked_cooldown_sec
            )
            return False, f"매물소진저점재이탈_현재={observed_price:.0f}<방어={guard_low:.0f}"

        latest_range, latest_signed_body, _, _ = _candle_metrics(latest)
        if (
            latest_signed_body <= -self.supply_exhaustion_config.flush_min_bear_body_ratio
            and float(latest.volume) >= evidence.flush_volume * self.supply_exhaustion_config.dry_max_intermediate_volume_vs_flush
        ):
            self._supply_block_until[signal.symbol] = (
                now_mono + self.supply_exhaustion_config.blocked_cooldown_sec
            )
            return False, "매물소진후재차대량매도"
        return True, "매물소진구조유지"

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason = self._supply_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._supply_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._supply_evidence.pop(signal.symbol, None)
        return entered

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, supply_reason = self._supply_entry_allowed(signal, current_price=current_price)
        if not allowed:
            return False, supply_reason
        return True, supply_reason


def _bar(symbol: str, minute: int, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar(
        symbol,
        datetime(2026, 7, 10, 9, minute, tzinfo=KST),
        o,
        h,
        l,
        c,
        v,
    )


def _pattern_strategy(*, dry_bull: bool = True, dry_volume: int = 350, break_low: bool = False, flush_volume: int = 2600, prior_rise: bool = True) -> tuple[Any, str]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "777777"
    bars: list[Any] = []

    # 안정 구간 + 1차 상승
    for idx in range(12):
        base = 100.0 + (idx % 3) * 0.1
        bars.append(_bar(symbol, idx, base, base + 0.35, base - 0.25, base + 0.10, 700 + (idx % 4) * 30))
    if prior_rise:
        rise_prices = [100.5, 101.2, 102.0, 103.0, 104.0, 105.0]
    else:
        rise_prices = [100.4, 100.5, 100.6, 100.7, 100.8, 100.9]
    for offset, close in enumerate(rise_prices, start=12):
        bars.append(_bar(symbol, offset, close - 0.4, close + 0.5, close - 0.7, close, 850 + (offset - 12) * 70))

    # 조정 시작 후 두드러진 대량 음봉
    bars.append(_bar(symbol, 18, 104.7 if prior_rise else 100.8, 104.9 if prior_rise else 101.0, 102.6 if prior_rise else 99.8, 103.0 if prior_rise else 100.0, flush_volume))
    # 거래량이 줄면서 저점을 지키는 중간 소형봉
    bars.append(_bar(symbol, 19, 103.0 if prior_rise else 100.0, 103.3 if prior_rise else 100.3, 102.7 if prior_rise else 99.9, 102.9 if prior_rise else 100.1, 500))
    if break_low:
        bars.append(_bar(symbol, 20, 102.8, 103.0, 101.7, 102.0, dry_volume))
    elif dry_bull:
        bars.append(_bar(symbol, 20, 102.9 if prior_rise else 100.1, 103.4 if prior_rise else 100.4, 102.8 if prior_rise else 100.0, 103.2 if prior_rise else 100.3, dry_volume))
    else:
        bars.append(_bar(symbol, 20, 103.10 if prior_rise else 100.20, 103.25 if prior_rise else 100.30, 102.85 if prior_rise else 100.00, 103.00 if prior_rise else 100.10, dry_volume))

    strategy.bars[symbol].extend(bars)
    strategy.daily_context[symbol] = v753.DailyContext(95.0, 120.0, 0.0, 0.0, False)
    return strategy, symbol


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record("V75.5-SHA256고정", sha256_file(BASE_DIR / V755_FILENAME) == V755_SHA256, V755_SHA256[:16] + "…")
    base_results = v755.self_check()
    base_failures = [name for name, ok, _ in base_results if not ok]
    record("V75.5-기존검증위임", not base_failures, f"{len(base_results)}개 / 실패={base_failures or '없음'}")

    cfg = SupplyExhaustionConfig()

    strategy, symbol = _pattern_strategy(dry_bull=True)
    signal, reason, evidence = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record(
        "대량음봉후저거래양봉BUY",
        signal is not None and signal.action == v753.SignalAction.BUY and signal.pattern == "매물소진눌림1차" and evidence is not None,
        signal.reason if signal else reason,
    )

    strategy, symbol = _pattern_strategy(dry_bull=False)
    signal, reason, evidence = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record(
        "대량음봉후저거래소형음봉BUY",
        signal is not None and signal.action == v753.SignalAction.BUY and evidence is not None,
        signal.reason if signal else reason,
    )

    strategy, symbol = _pattern_strategy(flush_volume=1200)
    signal, reason, _ = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record("대량음봉거래부족차단", signal is None, reason)

    strategy, symbol = _pattern_strategy(dry_volume=1500)
    signal, reason, _ = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record("후속거래미감소차단", signal is None, reason)

    strategy, symbol = _pattern_strategy(break_low=True)
    signal, reason, _ = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record("대량음봉저점이탈차단", signal is None, reason)

    strategy, symbol = _pattern_strategy(prior_rise=False)
    signal, reason, _ = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    record("1차상승없는대량음봉차단", signal is None, reason)

    # 전고점 부근이면 새 패턴도 V75.5의 강한 거래폭발 돌파 게이트를 통과해야 한다.
    strategy, symbol = _pattern_strategy(dry_bull=True)
    signal, _, _ = detect_supply_exhaustion_pullback(strategy, symbol, config=cfg)
    if signal is not None:
        strategy.daily_context[symbol] = v753.DailyContext(95.0, float(signal.reference_price) * 1.005, 0.0, 0.0, False)
        allowed, resistance_reason, _ = v755.assess_resistance_breakout(
            strategy,
            signal,
            config=v755.ResistanceGuardConfig(),
        )
        record("전고점부근기존V75.5차단유지", not allowed, resistance_reason)
    else:
        record("전고점부근기존V75.5차단유지", False, "합성신호생성실패")

    leader_cfg = SupplyExhaustionConfig()
    meta_strong = v753.Candidate("777777", "강", 100.0, 7.0, 1000000, 300.0, 200.0, "상승률+거래량급증", 0.0, 0.0, False)
    pool = [meta_strong] + [v753.Candidate(f"{i:06d}", str(i), 100.0, 1.0, 1000, 1.0, 1.0, "상승률") for i in range(1, 20)]
    leadership = assess_leadership_proxy("777777", meta=meta_strong, universe=pool, config=leader_cfg)
    record("시장주도프록시강가점", leadership.tier == "시장주도강" and leadership.bonus > 0, str(leadership))

    leadership_manual = assess_leadership_proxy(
        "777777", meta=None, universe=[], config=leader_cfg, manual_leader_symbols={"777777"}
    )
    record("수동테마대장가점", leadership_manual.tier == "수동지정대장" and leadership_manual.bonus == leader_cfg.manual_leader_bonus, str(leadership_manual))

    strategy, symbol = _pattern_strategy(dry_bull=True)
    _, _, supply_evidence = detect_supply_exhaustion_pullback(strategy, symbol, config=leader_cfg)
    if supply_evidence is not None:
        weak = LeadershipEvidence("시장주도보통", 0.0, None, 0, None, 0, "", False, False, False)
        weak_allowed, weak_reason = leadership_adjusted_pattern_allowed(weak, supply_evidence, leader_cfg)
        record("비주도주강한매물소진만허용", weak_allowed, weak_reason)
    else:
        record("비주도주강한매물소진만허용", False, "합성증거없음")

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (function_names & FORBIDDEN_ORDER_NAMES), str(sorted(function_names & FORBIDDEN_ORDER_NAMES)))
    record(
        "모든진입경로매물소진재검증",
        "def _queue_pending_entry" in source
        and "def _attempt_entry" in source
        and "def _pending_signal_valid" in source,
        "등록+직접진입+현재가재검증",
    )
    record(
        "V75.3~V75.5원본쓰기부재",
        "write_text" not in source.split("def load_v755", 1)[1].split("v755 =", 1)[0],
        "원본은 SHA검증 후 읽기 로드만",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — 대량 음봉 후 거래절벽 눌림 PAPER")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--quote-test", action="store_true")
    parser.add_argument("--scan-test", action="store_true")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--minutes", type=int, default=0)
    parser.add_argument("--reset-paper", action="store_true")
    parser.add_argument("--reset-v754-safety", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = build_parser().parse_args()
    logger = v753.setup_logging(args.verbose)
    try:
        settings = v753.Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            return 1 if failed else 0

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = v753.Settings.load()
            logger.info("신규 환경파일 적용=%s", [str(path) for path in newly_saved])
        settings.assert_credentials()

        if args.quote_test:
            return v753.quote_test(settings, logger, args.symbol)
        if args.scan_test:
            return v753.scan_test(settings, logger)

        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine = SupplyExhaustionEngine(
            settings,
            client,
            logger,
            reset_paper=args.reset_paper,
            reset_v754_safety=args.reset_v754_safety,
        )
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        return 0
    except v753.ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        return 2
    except v753.AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        return 3
    except v753.V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        return 4
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        v753.append_api_error(traceback.format_exc())
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
