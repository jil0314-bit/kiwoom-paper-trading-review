#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.15 ADJUSTMENT-DRY-BULL PRECISION PAPER
==========================================
- V75.14 전체 구조와 다른 매매 로직은 그대로 유지한다.
- '조정 후 거래바닥 양봉매수' 계열만 아래처럼 교체한다.
  * 상승 뒤 대량 음봉은 1개 또는 여러 개 허용
  * 마지막 유효 대량매도 묶음 뒤 거래량 60% 이상 감소를 추적
  * 고정 15분 만료 없음. 당일 패턴이 살아 있는 동안 계속 추적
  * 짧은 양봉 또는 아래꼬리 짧은 음봉이면 즉시 PAPER 매수
  * 거래바닥 뒤 거래량 재점화 장대양봉도 별도 매수
  * 이 기법 손절 -3%, 기본 목표 +1.5%, 저항공간에 따라 +2~3%
- PAPER 전용. 실제 주문 함수 없음.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import math
import os
import statistics
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence, TextIO

VERSION = "V75.15-ADJUSTMENT-DRY-BULL-PRECISION-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7514_FILENAME = "bot_v75_14_supply_focus_architecture_paper.py"
V7514_SHA256 = "88dd74bef13851f4154efba2565bebc30080d0ed36aec52ac2a3cc1ecb3ac380"
RUNTIME_DIR = BASE_DIR / "v75_15_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_15_daily_logs"
POTENTIAL_FILE = RUNTIME_DIR / "v75_15_supply_potential.json"
PATTERN_DRY = "조정후거래바닥양봉매수"
PATTERN_REIGNITION = "조정후거래바닥재점화매수"
FORBIDDEN_ORDER_NAMES = {
    "send_order", "create_order", "place_order", "order_stock", "submit_order"
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7514() -> ModuleType:
    path = BASE_DIR / V7514_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7514_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7514_SHA256:
        raise RuntimeError(
            "V75.14 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7514_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7514_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.14 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7514 = load_v7514()
v7513 = v7514.v7513
v7512 = v7514.v7512
v7511 = v7514.v7511
v7510 = v7514.v7510
v7591 = v7514.v7591
v758 = v7514.v758
v757 = v7514.v757
v756 = v7514.v756
v755 = v7514.v755
v754 = v7514.v754
v753 = v7514.v753
KST = v7514.KST

# 패치 전 검증결과를 먼저 고정한다. 이후 탐지기 교체가 과거 버전의 합성검사를 바꾸지 않게 한다.
BASELINE_V7514_SELF_CHECK = v7514.self_check()

# V75.15는 V75.14와 런타임/로그 파일을 분리한다.
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = POTENTIAL_FILE
v7514.SUPPLY_PATTERNS.update({PATTERN_DRY, PATTERN_REIGNITION})


@dataclass(frozen=True, slots=True)
class PrecisionConfig:
    rise_lookback_bars: int = 30
    min_prior_rise_pct: float = 0.025
    min_left_bars: int = 5
    max_left_bars: int = 20

    heavy_min_bear_body_ratio: float = 0.20
    heavy_min_drop_pct: float = 0.002
    heavy_volume_vs_median: float = 1.60
    heavy_volume_vs_p75: float = 1.05
    comparable_heavy_volume_ratio: float = 0.70

    dry_max_volume_vs_heavy_cluster: float = 0.40
    strong_dry_max_volume_vs_heavy_cluster: float = 0.30
    dry_max_volume_vs_context: float = 0.40
    short_max_range_vs_heavy: float = 0.80
    lower_wick_min_ratio: float = 0.30
    bearish_close_min_location: float = 0.35

    reignition_min_dry_bars: int = 2
    reignition_dry_window: int = 5
    reignition_volume_mult: float = 2.50
    reignition_min_body_ratio: float = 0.60
    reignition_min_close_location: float = 0.80
    reignition_max_upper_wick_ratio: float = 0.20
    reignition_min_range_mult: float = 1.35
    reignition_breakout_lookback: int = 3
    reignition_max_chase_pct: float = 0.007

    stop_loss_pct: float = 0.030
    default_take_profit_pct: float = 0.015
    medium_take_profit_pct: float = 0.020
    max_take_profit_pct: float = 0.030
    resistance_scan_bars: int = 120

    @classmethod
    def load(cls) -> "PrecisionConfig":
        def ef(name: str, default: float, minimum: float, maximum: float) -> float:
            return v7511.env_float(name, default, minimum=minimum, maximum=maximum)

        def ei(name: str, default: int, minimum: int, maximum: int) -> int:
            return v7511.env_int(name, default, minimum=minimum, maximum=maximum)

        return cls(
            rise_lookback_bars=ei("V7515_RISE_LOOKBACK", 30, 8, 90),
            min_prior_rise_pct=ef("V7515_MIN_PRIOR_RISE", 0.025, 0.008, 0.15),
            min_left_bars=ei("V7515_MIN_LEFT_BARS", 5, 3, 12),
            max_left_bars=ei("V7515_MAX_LEFT_BARS", 20, 8, 60),
            heavy_min_bear_body_ratio=ef("V7515_HEAVY_MIN_BODY", 0.20, 0.08, 0.80),
            heavy_min_drop_pct=ef("V7515_HEAVY_MIN_DROP", 0.002, 0.0005, 0.05),
            heavy_volume_vs_median=ef("V7515_HEAVY_VOL_MEDIAN", 1.60, 1.10, 5.0),
            heavy_volume_vs_p75=ef("V7515_HEAVY_VOL_P75", 1.05, 0.80, 3.0),
            comparable_heavy_volume_ratio=ef("V7515_COMPARABLE_HEAVY", 0.70, 0.40, 1.20),
            dry_max_volume_vs_heavy_cluster=ef("V7515_DRY_VS_HEAVY", 0.40, 0.10, 0.70),
            strong_dry_max_volume_vs_heavy_cluster=ef("V7515_STRONG_DRY_VS_HEAVY", 0.30, 0.05, 0.50),
            dry_max_volume_vs_context=ef("V7515_DRY_VS_CONTEXT", 0.40, 0.10, 0.80),
            short_max_range_vs_heavy=ef("V7515_SHORT_RANGE", 0.80, 0.30, 1.00),
            lower_wick_min_ratio=ef("V7515_LOWER_WICK", 0.30, 0.10, 0.70),
            bearish_close_min_location=ef("V7515_BEAR_CLOSE_LOCATION", 0.35, 0.10, 0.70),
            reignition_min_dry_bars=ei("V7515_REIGNITION_MIN_DRY_BARS", 2, 1, 8),
            reignition_dry_window=ei("V7515_REIGNITION_DRY_WINDOW", 5, 2, 12),
            reignition_volume_mult=ef("V7515_REIGNITION_VOL_MULT", 2.50, 1.50, 6.0),
            reignition_min_body_ratio=ef("V7515_REIGNITION_BODY", 0.60, 0.30, 0.90),
            reignition_min_close_location=ef("V7515_REIGNITION_CLOSE", 0.80, 0.55, 0.98),
            reignition_max_upper_wick_ratio=ef("V7515_REIGNITION_UPPER_WICK", 0.20, 0.05, 0.50),
            reignition_min_range_mult=ef("V7515_REIGNITION_RANGE_MULT", 1.35, 1.00, 3.0),
            reignition_breakout_lookback=ei("V7515_REIGNITION_BREAKOUT_BARS", 3, 2, 10),
            reignition_max_chase_pct=ef("V7515_REIGNITION_MAX_CHASE", 0.007, 0.002, 0.03),
            stop_loss_pct=ef("V7515_STOP_LOSS", 0.030, 0.015, 0.06),
            default_take_profit_pct=ef("V7515_DEFAULT_TARGET", 0.015, 0.008, 0.03),
            medium_take_profit_pct=ef("V7515_MEDIUM_TARGET", 0.020, 0.015, 0.04),
            max_take_profit_pct=ef("V7515_MAX_TARGET", 0.030, 0.02, 0.05),
            resistance_scan_bars=ei("V7515_RESISTANCE_SCAN_BARS", 120, 40, 240),
        )


@dataclass(frozen=True, slots=True)
class HeavyStructure:
    anchor_index: int
    heavy_indices: tuple[int, ...]
    last_heavy_index: int
    rise_low: float
    rise_high: float
    rise_pct: float
    retrace: float
    heavy_volume: float
    heavy_range: float
    context_volume: float
    volume_vs_median: float
    volume_vs_p75: float
    volume_vs_recent_max: float


@dataclass(frozen=True, slots=True)
class PrecisionEvidence:
    # V75.14 로그 호환 필드
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
    # V75.15 추가 필드
    confirmation: str
    heavy_count: int
    lower_wick_ratio: float
    ema5_recovered: bool
    target_pct: float
    nearest_resistance_gap: float | None
    target_reason: str


def percentile75(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(0.75 * len(ordered))) - 1
    return float(ordered[max(0, min(index, len(ordered) - 1))])


def candle_metrics(bar: Any) -> tuple[float, float, float, float, float, float]:
    high = float(bar.high)
    low = float(bar.low)
    open_price = float(bar.open)
    close = float(bar.close)
    span = max(1e-9, high - low)
    signed_body = (close - open_price) / span
    body_ratio = abs(close - open_price) / span
    close_location = (close - low) / span
    lower_wick = (min(open_price, close) - low) / span
    upper_wick = (high - max(open_price, close)) / span
    return span, signed_body, body_ratio, close_location, lower_wick, upper_wick


def ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    alpha = 2.0 / (period + 1.0)
    value = float(values[0])
    for item in values[1:]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def adaptive_left(bars: Sequence[Any], index: int, cfg: PrecisionConfig) -> list[Any]:
    count = min(cfg.max_left_bars, index)
    if count < cfg.min_left_bars:
        return []
    return list(bars[index - count:index])


def prior_rise(bars: Sequence[Any], index: int, cfg: PrecisionConfig) -> tuple[float, float, float] | None:
    start = max(0, index - cfg.rise_lookback_bars)
    window = list(bars[start:index])
    if len(window) < cfg.min_left_bars:
        return None
    best: tuple[float, float, float] | None = None
    running_low = float(window[0].low)
    for item in window[1:]:
        running_low = min(running_low, float(item.low))
        high = float(item.high)
        rise = high / max(0.01, running_low) - 1.0
        if best is None or rise > best[2]:
            best = (running_low, high, rise)
    if best is None or best[2] < cfg.min_prior_rise_pct:
        return None
    return best


def is_anchor_heavy(bars: Sequence[Any], index: int, cfg: PrecisionConfig) -> tuple[bool, dict[str, float]]:
    if index <= 0:
        return False, {}
    bar = bars[index]
    previous = bars[index - 1]
    span, signed_body, body_ratio, _, _, _ = candle_metrics(bar)
    del span
    if float(bar.close) >= float(bar.open):
        return False, {}
    if -signed_body < cfg.heavy_min_bear_body_ratio or body_ratio < cfg.heavy_min_bear_body_ratio:
        return False, {}
    drop_pct = float(bar.close) / max(0.01, float(previous.close)) - 1.0
    if drop_pct > -cfg.heavy_min_drop_pct:
        return False, {}
    left = adaptive_left(bars, index, cfg)
    if len(left) < cfg.min_left_bars:
        return False, {}
    volumes = [max(0.0, float(item.volume)) for item in left]
    median = float(statistics.median(volumes))
    p75 = percentile75(volumes)
    recent_max = max(volumes[-min(5, len(volumes)):])
    volume = max(0.0, float(bar.volume))
    vs_median = volume / max(1.0, median)
    vs_p75 = volume / max(1.0, p75)
    vs_recent = volume / max(1.0, recent_max)
    if vs_median < cfg.heavy_volume_vs_median or vs_p75 < cfg.heavy_volume_vs_p75:
        return False, {}
    return True, {
        "median": median,
        "p75": p75,
        "recent_max": recent_max,
        "vs_median": vs_median,
        "vs_p75": vs_p75,
        "vs_recent": vs_recent,
    }


def find_heavy_structures(bars: Sequence[Any], cfg: PrecisionConfig) -> list[HeavyStructure]:
    structures: list[HeavyStructure] = []
    # 현재봉도 대량 음봉일 수 있으나 매수 확인봉은 그 다음 봉부터다.
    for anchor_index in range(cfg.min_left_bars, max(cfg.min_left_bars, len(bars) - 1)):
        rise = prior_rise(bars, anchor_index, cfg)
        if rise is None:
            continue
        ok, metrics = is_anchor_heavy(bars, anchor_index, cfg)
        if not ok:
            continue
        anchor = bars[anchor_index]
        anchor_volume = max(1.0, float(anchor.volume))
        heavy_indices = [anchor_index]
        # 이후에도 비슷한 매도량의 음봉이 나오면 실패로 버리지 않고 같은 매도 묶음으로 갱신한다.
        for idx in range(anchor_index + 1, len(bars) - 1):
            item = bars[idx]
            _, signed_body, body_ratio, _, _, _ = candle_metrics(item)
            if float(item.close) >= float(item.open):
                continue
            local_left = adaptive_left(bars, idx, cfg)
            local_median = (
                float(statistics.median([float(x.volume) for x in local_left]))
                if local_left else 0.0
            )
            comparable = float(item.volume) >= anchor_volume * cfg.comparable_heavy_volume_ratio
            locally_heavy = (
                body_ratio >= cfg.heavy_min_bear_body_ratio
                and -signed_body >= cfg.heavy_min_bear_body_ratio
                and float(item.volume) >= max(1.0, local_median) * cfg.heavy_volume_vs_median
            )
            if comparable or locally_heavy:
                heavy_indices.append(idx)
                anchor_volume = max(anchor_volume, float(item.volume))

        last_heavy = heavy_indices[-1]
        heavy_volumes = [float(bars[idx].volume) for idx in heavy_indices]
        heavy_ranges = [candle_metrics(bars[idx])[0] for idx in heavy_indices]
        heavy_volume = float(statistics.median(heavy_volumes))
        heavy_range = float(statistics.median(heavy_ranges))
        rise_low, rise_high, rise_pct = rise
        after_lows = [float(item.low) for item in bars[anchor_index:]]
        pullback_low = min(after_lows) if after_lows else float(anchor.low)
        retrace = (rise_high - pullback_low) / max(1.0, rise_high - rise_low)
        structures.append(
            HeavyStructure(
                anchor_index=anchor_index,
                heavy_indices=tuple(heavy_indices),
                last_heavy_index=last_heavy,
                rise_low=rise_low,
                rise_high=rise_high,
                rise_pct=rise_pct,
                retrace=retrace,
                heavy_volume=max(1.0, heavy_volume),
                heavy_range=max(1e-9, heavy_range),
                context_volume=max(1.0, float(metrics["median"])),
                volume_vs_median=float(metrics["vs_median"]),
                volume_vs_p75=float(metrics["vs_p75"]),
                volume_vs_recent_max=float(metrics["vs_recent"]),
            )
        )
    # 마지막 대량매도 묶음이 가장 최근인 구조부터 본다.
    structures.sort(key=lambda item: (item.last_heavy_index, len(item.heavy_indices), -item.anchor_index), reverse=True)
    return structures


def resistance_target(
    bars: Sequence[Any],
    current_index: int,
    structure: HeavyStructure,
    current_price: float,
    cfg: PrecisionConfig,
) -> tuple[float, float | None, str]:
    prior = list(bars[max(0, current_index - cfg.resistance_scan_bars):current_index])
    candidates: list[tuple[float, str]] = []

    closes = [float(item.close) for item in bars[: current_index + 1]]
    for period, label in ((60, "EMA60"), (120, "EMA120")):
        level = ema(closes, period)
        if level is not None and level > current_price * 1.002:
            candidates.append((level, label))

    # 최근 반복·스윙 고점
    for idx in range(2, max(2, len(prior) - 2)):
        high = float(prior[idx].high)
        if high <= current_price * 1.002:
            continue
        neighborhood = [float(prior[j].high) for j in range(idx - 2, idx + 3)]
        if high >= max(neighborhood):
            candidates.append((high, "최근전고점"))

    # 대량 음봉 몸통/고가 매물
    for idx in structure.heavy_indices:
        item = bars[idx]
        for level, label in (
            (float(item.open), "대량음봉시가"),
            ((float(item.open) + float(item.close)) / 2.0, "대량음봉몸통"),
            (float(item.high), "대량음봉고가"),
        ):
            if level > current_price * 1.002:
                candidates.append((level, label))

    # 거래량이 몰린 봉의 가격대
    if prior:
        median_volume = float(statistics.median([max(0.0, float(item.volume)) for item in prior]))
        for item in prior:
            if float(item.volume) < max(1.0, median_volume) * 2.0:
                continue
            typical = (float(item.high) + float(item.low) + float(item.close)) / 3.0
            if typical > current_price * 1.002:
                candidates.append((typical, "거래밀집가격"))

    if candidates:
        level, label = min(candidates, key=lambda item: item[0])
        gap = level / max(0.01, current_price) - 1.0
    else:
        gap = None
        label = "상단저항미확인"

    if gap is None or gap >= 0.033:
        return cfg.max_take_profit_pct, gap, f"{label}_3%공간"
    if gap >= 0.023:
        return cfg.medium_take_profit_pct, gap, f"{label}_{gap:.2%}공간"
    return cfg.default_take_profit_pct, gap, f"가까운저항_{label}_{gap:.2%}"


def detect_precision_supply(
    strategy: Any,
    symbol: str,
    *,
    config: Any,
) -> tuple[Any | None, str, PrecisionEvidence | None]:
    del config  # 호출 호환용. V75.15 자체 설정을 사용한다.
    cfg = PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    minimum = max(cfg.max_left_bars + 3, 16)
    if len(bars) < minimum:
        return None, f"거래바닥자료부족_{len(bars)}<{minimum}", None

    current_index = len(bars) - 1
    current = bars[current_index]
    current_price = float(current.close)
    if current_price <= 0 or float(current.volume) <= 0:
        return None, "현재봉가격거래오류", None

    structures = find_heavy_structures(bars, cfg)
    if not structures:
        return None, "상승후대량음봉묶음없음", None

    current_range, current_signed, current_body, close_location, lower_wick, upper_wick = candle_metrics(current)
    del current_body

    for structure in structures:
        if structure.last_heavy_index >= current_index:
            continue
        after = list(bars[structure.last_heavy_index + 1:])
        if not after:
            continue

        dry_vs_heavy = float(current.volume) / max(1.0, structure.heavy_volume)
        dry_vs_context = float(current.volume) / max(1.0, structure.context_volume)
        previous = after[-2] if len(after) >= 2 else bars[structure.last_heavy_index]
        dry_vs_previous = float(current.volume) / max(1.0, float(previous.volume))
        range_vs_heavy = current_range / max(1e-9, structure.heavy_range)

        # 분기 A: 거래바닥 짧은 양봉 또는 아래꼬리 짧은 음봉
        volume_dry = (
            dry_vs_heavy <= cfg.dry_max_volume_vs_heavy_cluster
            and dry_vs_context <= cfg.dry_max_volume_vs_context
        )
        short_bar = range_vs_heavy <= cfg.short_max_range_vs_heavy
        bullish_short = current_signed > 0
        bearish_lower_wick = (
            current_signed <= 0
            and lower_wick >= cfg.lower_wick_min_ratio
            and close_location >= cfg.bearish_close_min_location
        )
        quiet_confirmation = volume_dry and short_bar and (bullish_short or bearish_lower_wick)

        confirmation = ""
        if quiet_confirmation:
            confirmation = "저거래짧은양봉" if bullish_short else "아래꼬리짧은음봉"

        # 분기 B: 거래바닥 뒤 재점화 장대양봉
        post_heavy_before_current = after[:-1]
        dry_candidates = [
            item for item in post_heavy_before_current
            if float(item.volume) <= structure.heavy_volume * cfg.dry_max_volume_vs_heavy_cluster
        ]
        dry_slice = dry_candidates[-cfg.reignition_dry_window:]
        reignition = False
        breakout_level = 0.0
        dry_mean = 0.0
        dry_range_median = 0.0
        if len(dry_slice) >= cfg.reignition_min_dry_bars:
            dry_mean = float(statistics.mean([float(item.volume) for item in dry_slice]))
            dry_range_median = float(statistics.median([candle_metrics(item)[0] for item in dry_slice]))
            recent_before = post_heavy_before_current[-cfg.reignition_breakout_lookback:]
            if recent_before:
                breakout_level = max(float(item.high) for item in recent_before)
                chase = current_price / max(0.01, breakout_level) - 1.0
                reignition = (
                    current_signed >= cfg.reignition_min_body_ratio
                    and close_location >= cfg.reignition_min_close_location
                    and upper_wick <= cfg.reignition_max_upper_wick_ratio
                    and float(current.volume) >= max(1.0, dry_mean) * cfg.reignition_volume_mult
                    and current_range >= max(1e-9, dry_range_median) * cfg.reignition_min_range_mult
                    and current_price > breakout_level
                    and chase <= cfg.reignition_max_chase_pct
                )
                if reignition:
                    confirmation = "거래바닥후재점화장대양봉"

        if not quiet_confirmation and not reignition:
            continue

        closes = [float(item.close) for item in bars]
        ema5 = ema(closes, 5)
        ema5_recovered = bool(ema5 is not None and current_price >= ema5)
        target_pct, resistance_gap, target_reason = resistance_target(
            bars, current_index, structure, current_price, cfg
        )
        flush = bars[structure.last_heavy_index]
        score = 105.0
        if dry_vs_heavy <= cfg.strong_dry_max_volume_vs_heavy_cluster:
            score += 20.0
        elif dry_vs_heavy <= cfg.dry_max_volume_vs_heavy_cluster:
            score += 12.0
        if confirmation == "저거래짧은양봉":
            score += 20.0
        elif confirmation == "아래꼬리짧은음봉":
            score += 20.0
        elif confirmation == "거래바닥후재점화장대양봉":
            score += 22.0
        if ema5_recovered:
            score += 8.0
        if len(structure.heavy_indices) >= 2:
            score += 4.0

        evidence = PrecisionEvidence(
            rise_low=structure.rise_low,
            rise_high=structure.rise_high,
            rise_pct=structure.rise_pct,
            retrace=structure.retrace,
            flush_time=v754.normalize_kst(flush.timestamp),
            flush_low=float(flush.low),
            flush_volume=structure.heavy_volume,
            flush_volume_vs_median=structure.volume_vs_median,
            flush_volume_vs_p75=structure.volume_vs_p75,
            flush_volume_vs_recent_max=structure.volume_vs_recent_max,
            dry_volume_vs_flush=dry_vs_heavy,
            dry_volume_vs_left_median=dry_vs_context,
            dry_volume_vs_previous=dry_vs_previous,
            dry_range_vs_flush=range_vs_heavy,
            current_close=current_price,
            structure_stop_pct=cfg.stop_loss_pct,
            confirmation=confirmation,
            heavy_count=len(structure.heavy_indices),
            lower_wick_ratio=lower_wick,
            ema5_recovered=ema5_recovered,
            target_pct=target_pct,
            nearest_resistance_gap=resistance_gap,
            target_reason=target_reason,
        )
        pattern = PATTERN_REIGNITION if reignition else PATTERN_DRY
        reason = (
            f"{pattern}|선행상승={structure.rise_pct:.2%}|대량음봉수={len(structure.heavy_indices)}"
            f"|대량거래=중앙x{structure.volume_vs_median:.2f}/P75x{structure.volume_vs_p75:.2f}"
            f"|거래감소=대량묶음대비{1.0-dry_vs_heavy:.1%}/문맥대비{1.0-dry_vs_context:.1%}"
            f"|봉폭=대량봉x{range_vs_heavy:.2f}|확인={confirmation}"
            f"|아래꼬리={lower_wick:.0%}|EMA5={'회복' if ema5_recovered else '미회복'}"
            f"|손절={cfg.stop_loss_pct:.1%}|목표={target_pct:.1%}({target_reason})"
        )
        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            score,
            reason,
            current_price,
            current.timestamp,
            pattern,
            cfg.stop_loss_pct,
            target_pct,
        )
        return signal, "조정후거래바닥확인", evidence

    return None, "조정후거래바닥조건없음", None


def assess_precision_watch(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
) -> tuple[Any | None, str]:
    del loose, strict
    cfg = PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    if len(bars) < max(cfg.max_left_bars + 2, 16):
        return None, f"자료부족_{len(bars)}"
    structures = find_heavy_structures(bars, cfg)
    if not structures:
        return None, "상승후대량음봉묶음없음"
    structure = structures[0]
    current = bars[-1]
    if structure.last_heavy_index >= len(bars) - 1:
        dry_vs_heavy = None
        dry_range = None
        stage = "대량음봉묶음발견"
        missing = ("거래량60%감소대기", "짧은양봉/아래꼬리음봉대기")
    else:
        dry_vs_heavy = float(current.volume) / max(1.0, structure.heavy_volume)
        dry_vs_context = float(current.volume) / max(1.0, structure.context_volume)
        dry_range = candle_metrics(current)[0] / max(1e-9, structure.heavy_range)
        _, signed, _, close_loc, lower_wick, _ = candle_metrics(current)
        short = dry_range <= cfg.short_max_range_vs_heavy
        volume_dry = (
            dry_vs_heavy <= cfg.dry_max_volume_vs_heavy_cluster
            and dry_vs_context <= cfg.dry_max_volume_vs_context
        )
        confirm = signed > 0 or (
            signed <= 0
            and lower_wick >= cfg.lower_wick_min_ratio
            and close_loc >= cfg.bearish_close_min_location
        )
        stage = "거래바닥관찰"
        if volume_dry:
            stage = "관심종목-매수가능성"
        if volume_dry and short and confirm:
            stage = "즉시매수조건근접"
        missing_list: list[str] = []
        if not volume_dry:
            missing_list.append(f"거래감소<{1.0-cfg.dry_max_volume_vs_heavy_cluster:.0%}(대량봉/주변봉)")
        if not short:
            missing_list.append(f"봉폭>{cfg.short_max_range_vs_heavy:.0%}")
        if not confirm:
            missing_list.append("짧은양봉/아래꼬리음봉없음")
        missing = tuple(missing_list)

    flush = bars[structure.last_heavy_index]
    end_of_session = datetime.combine(now.date(), dt_time(20, 0), tzinfo=KST)
    score = 55.0 + min(20.0, structure.rise_pct * 200.0)
    if dry_vs_heavy is not None:
        score += max(0.0, min(20.0, (1.0 - dry_vs_heavy) * 25.0))
    item = v7514.SupplyPotential(
        symbol=symbol,
        name=name or symbol,
        stage=stage,
        score=min(100.0, score),
        detected_at=now,
        updated_at=now,
        expires_at=end_of_session,
        flush_time=v754.normalize_kst(flush.timestamp),
        rise_pct=structure.rise_pct,
        retrace=structure.retrace,
        flush_vs_median=structure.volume_vs_median,
        flush_vs_p75=structure.volume_vs_p75,
        flush_vs_recent_max=structure.volume_vs_recent_max,
        dry_vs_flush=dry_vs_heavy,
        dry_range_vs_flush=dry_range,
        low_break_pct=0.0,
        strict_missing=missing,
        source=source,
    )
    return item, "정밀매물소진구조발견"


# 모든 상속 경로가 새 탐지기를 사용하도록 같은 모듈 객체를 교체한다.
v756.detect_supply_exhaustion_pullback = detect_precision_supply
v7514.assess_loose_supply_potential = assess_precision_watch

# V75.11의 비용포함 -2% 보정은 다른 패턴에는 유지하되,
# V75.15의 두 매수패턴과 그 50초 진입은 사용자가 정한 가격손절 -3%를 그대로 둔다.
_ORIGINAL_COST_STOP = v7511.signal_with_cost_aware_stop


def precision_cost_stop(signal: Any, settings: Any, config: Any) -> Any:
    pattern = str(getattr(signal, "pattern", ""))
    reason = str(getattr(signal, "reason", ""))
    if pattern in {PATTERN_DRY, PATTERN_REIGNITION, "매물소진50초선진입"} and (
        pattern in {PATTERN_DRY, PATTERN_REIGNITION}
        or PATTERN_DRY in reason
        or PATTERN_REIGNITION in reason
    ):
        return signal
    return _ORIGINAL_COST_STOP(signal, settings, config)


v7511.signal_with_cost_aware_stop = precision_cost_stop


class PrecisionEngine(v7514.SupplyFocusEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.v7515_config = PrecisionConfig.load()
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] 조정후거래바닥 정밀판 | 대량음봉 복수허용 | 거래감소>=%.0f%% | "
            "짧은봉<=대량봉%.0f%% | 양봉/아래꼬리음봉 즉시진입 | 손절 -%.1f%% | 목표 +1.5~3.0%%",
            VERSION,
            (1.0 - self.v7515_config.dry_max_volume_vs_heavy_cluster) * 100,
            self.v7515_config.short_max_range_vs_heavy * 100,
            self.v7515_config.stop_loss_pct * 100,
        )

    def _supply_discovery_compatible(self, symbol: str) -> bool:
        if symbol in self._supply_potential:
            return True
        meta = self.candidate_meta.get(symbol)
        if meta is None:
            return False
        source = str(getattr(meta, "source", "") or "")
        # 공급소진 전용모드에서는 거래량급증 API와 이미 저장된 구조만 사용한다.
        return "거래량급증" in source

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        previous = set(self.active_symbols)
        # V75.14의 +0.8%, 쌍끌이 +0.3%, 상위35 보완을 건너뛴다.
        v7513.AllPatternDeferredFreshEngine._refresh_universe(self, force=force)
        self._purge_expired_potential()
        if self.supply_only:
            potential_symbols = [
                symbol for symbol in self._supply_potential
                if symbol not in self.broker.positions
            ]
            volume_surge_symbols = [
                symbol for symbol in self.active_symbols
                if self._supply_discovery_compatible(symbol)
                and symbol not in self.broker.positions
            ]
            selected = list(dict.fromkeys(potential_symbols + volume_surge_symbols))
            self.active_symbols = selected[: self.loose_supply_config.supply_pool_limit]
            self.logger.warning(
                "매물소진전용 관심풀 %d종목 | 구조유지=%d | 거래량급증발견=%d | "
                "상승률+0.8/쌍끌이+0.3/상위35보완=미사용",
                len(self.active_symbols),
                len(potential_symbols),
                len(volume_surge_symbols),
            )
        self._log_discovery_registration(self.active_symbols)
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    def _format_strict_evidence(self, signal: Any) -> str:
        evidence = self._supply_evidence.get(signal.symbol)
        if evidence is None:
            snapshot = getattr(self, "_intrabar_snapshots", {}).get(signal.symbol)
            evidence = getattr(snapshot, "supply", None)
        if not isinstance(evidence, PrecisionEvidence):
            return super()._format_strict_evidence(signal)
        resistance = (
            "없음" if evidence.nearest_resistance_gap is None
            else f"{evidence.nearest_resistance_gap:.2%}"
        )
        return (
            f"패턴={signal.pattern} | 선행상승={evidence.rise_pct:.2%} | "
            f"대량음봉수={evidence.heavy_count} | 거래량감소="
            f"대량묶음대비{1.0-evidence.dry_volume_vs_flush:.1%}/"
            f"주변대비{1.0-evidence.dry_volume_vs_left_median:.1%} | "
            f"봉폭비={evidence.dry_range_vs_flush:.2f} | 확인봉={evidence.confirmation} | "
            f"아래꼬리={evidence.lower_wick_ratio:.0%} | EMA5="
            f"{'회복' if evidence.ema5_recovered else '미회복'} | "
            f"손절=-{signal.stop_loss_pct:.1%} | 목표=+{signal.take_profit_pct:.1%} | "
            f"최근저항공간={resistance}({evidence.target_reason})"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — PAPER")
    parser.add_argument("--supply-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--quote-test", action="store_true")
    parser.add_argument("--scan-test", action="store_true")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--minutes", type=int, default=0)
    parser.add_argument("--reset-paper", action="store_true")
    parser.add_argument("--reset-v754-safety", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


class TeeTextIO(io.TextIOBase):
    def __init__(self, console: TextIO, file_handle: TextIO) -> None:
        self.console = console
        self.file_handle = file_handle

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.console.write(text)
        self.file_handle.write(text)
        self.file_handle.flush()
        return len(text)

    def flush(self) -> None:
        self.console.flush()
        self.file_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.console, "isatty", lambda: False)())


@dataclass(slots=True)
class DailyTee:
    path: Path
    handle: TextIO
    original_stdout: TextIO
    original_stderr: TextIO

    def close(self) -> None:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.handle.close()


def install_daily_tee(argv: Sequence[str]) -> DailyTee:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_15_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeTextIO(original_stdout, handle)
    sys.stderr = TeeTextIO(original_stderr, handle)
    print("=" * 100)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION} 실행 시작")
    print(f"명령행: {' '.join(argv)}")
    print(f"날짜별 로그: {path}")
    print("=" * 100)
    return DailyTee(path, handle, original_stdout, original_stderr)


def _bar(symbol: str, minute: int, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar(symbol, datetime(2026, 7, 10, 9, minute, tzinfo=KST), o, h, l, c, v)


def synthetic_case(kind: str) -> tuple[Any, str]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "751500"
    bars: list[Any] = []
    # 안정구간 20봉
    for idx in range(20):
        base = 100.0 + idx * 0.03
        bars.append(_bar(symbol, idx, base, base + 0.25, base - 0.20, base + 0.08, 1000))
    # 선행상승
    for offset, close in enumerate([100.8, 101.4, 102.0, 102.7, 103.3], start=20):
        bars.append(_bar(symbol, offset, close - 0.35, close + 0.35, close - 0.45, close, 1200))
    # 대량 음봉 2개
    bars.append(_bar(symbol, 25, 103.2, 103.3, 101.5, 101.9, 3200))
    bars.append(_bar(symbol, 26, 102.0, 102.2, 100.9, 101.2, 2600))
    # 거래바닥 구간
    bars.append(_bar(symbol, 27, 101.2, 101.5, 100.9, 101.0, 380))
    bars.append(_bar(symbol, 28, 101.0, 101.3, 100.8, 101.15, 340))
    if kind == "quiet":
        bars.append(_bar(symbol, 29, 101.1, 101.35, 100.95, 101.25, 320))
    elif kind == "wick":
        bars.append(_bar(symbol, 29, 101.2, 101.25, 100.75, 101.05, 320))
    elif kind == "reignition":
        bars.append(_bar(symbol, 29, 101.1, 102.2, 101.0, 102.1, 1000))
    else:
        bars.append(_bar(symbol, 29, 101.1, 101.8, 100.6, 100.8, 1800))
    for bar in bars:
        strategy.on_bar(bar, None)
    return strategy, symbol


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.14-SHA256고정",
        sha256_file(BASE_DIR / V7514_FILENAME) == V7514_SHA256,
        V7514_SHA256[:16] + "…",
    )
    inherited = BASELINE_V7514_SELF_CHECK
    failures = [name for name, ok, _ in inherited if not ok]
    record("V75.14기존검증위임", not failures, f"{len(inherited)}항목 / 실패={failures or '없음'}")

    quiet_strategy, symbol = synthetic_case("quiet")
    quiet_signal, _, quiet_ev = detect_precision_supply(quiet_strategy, symbol, config=None)
    record(
        "복수대량음봉후짧은양봉",
        quiet_signal is not None
        and quiet_signal.pattern == PATTERN_DRY
        and isinstance(quiet_ev, PrecisionEvidence)
        and quiet_ev.heavy_count >= 2,
        quiet_signal.reason if quiet_signal else "미탐지",
    )

    wick_strategy, symbol = synthetic_case("wick")
    wick_signal, _, wick_ev = detect_precision_supply(wick_strategy, symbol, config=None)
    record(
        "아래꼬리짧은음봉동일취급",
        wick_signal is not None
        and wick_signal.pattern == PATTERN_DRY
        and isinstance(wick_ev, PrecisionEvidence)
        and "아래꼬리" in wick_ev.confirmation,
        wick_signal.reason if wick_signal else "미탐지",
    )

    reignition_strategy, symbol = synthetic_case("reignition")
    reignition_signal, _, _ = detect_precision_supply(reignition_strategy, symbol, config=None)
    record(
        "거래바닥후재점화",
        reignition_signal is not None and reignition_signal.pattern == PATTERN_REIGNITION,
        reignition_signal.reason if reignition_signal else "미탐지",
    )

    bad_strategy, symbol = synthetic_case("bad")
    bad_signal, _, _ = detect_precision_supply(bad_strategy, symbol, config=None)
    record("거래바닥없는봉차단", bad_signal is None, "미탐지" if bad_signal is None else bad_signal.reason)

    cfg = PrecisionConfig()
    test_signal = v753.Signal(
        "751500", v753.SignalAction.BUY, 120.0, PATTERN_DRY, 100.0,
        datetime(2026, 7, 10, 10, 0, tzinfo=KST), PATTERN_DRY,
        cfg.stop_loss_pct, cfg.default_take_profit_pct,
    )
    adjusted = precision_cost_stop(test_signal, v753.Settings.load(), v7511.LossControlConfig())
    record("이기법손절3퍼센트유지", abs(adjusted.stop_loss_pct - 0.03) < 1e-12, f"{adjusted.stop_loss_pct:.2%}")

    parser = build_parser().parse_args(["--supply-only", "--minutes", "1"])
    record("전용플래그유지", parser.supply_only and parser.minutes == 1, "--supply-only")

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), str(sorted(names & FORBIDDEN_ORDER_NAMES)))
    record(
        "불필요관심필터제거",
        "상승률+0.8/쌍끌이+0.3/상위35보완=미사용" in source,
        "거래량급증+구조유지만",
    )
    record(
        "날짜별로그",
        "V75_15_{now:%Y-%m-%d}.txt" in source,
        "실행시 자동생성",
    )
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = build_parser().parse_args()
    tee = install_daily_tee(sys.argv)
    logger = v753.setup_logging(args.verbose)
    v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    exit_code = 9
    try:
        settings = v753.Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            exit_code = 1 if failed else 0
            return exit_code

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = v753.Settings.load()
        settings.assert_credentials()
        if args.quote_test:
            exit_code = v753.quote_test(settings, logger, args.symbol)
            return exit_code
        if args.scan_test:
            exit_code = v753.scan_test(settings, logger)
            return exit_code

        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine_logger = v7591.CommaSafeLoggerAdapter(logger, {})
        engine = PrecisionEngine(
            settings,
            client,
            engine_logger,
            supply_only=args.supply_only,
            reset_paper=args.reset_paper,
            reset_v754_safety=args.reset_v754_safety,
        )
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        exit_code = 0
        return exit_code
    except v753.ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        exit_code = 2
        return exit_code
    except v753.AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        exit_code = 3
        return exit_code
    except v753.V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        exit_code = 4
        return exit_code
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        v753.append_api_error(traceback.format_exc())
        exit_code = 9
        return exit_code
    finally:
        print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {VERSION} 종료코드={exit_code}")
        tee.close()


if __name__ == "__main__":
    raise SystemExit(main())
