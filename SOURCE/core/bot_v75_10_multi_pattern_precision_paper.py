#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.10 MULTI-PATTERN PRECISION PAPER
=====================================

V75.9.1까지의 모든 매수기법과 리스크 계층을 그대로 보존하면서,
'대량거래 점화 → 얕은 조정 → 양봉거래 우위 → 계단식 상승 지속' 패턴을
독립 매수모듈로 추가한다.

중요
- 기존 정석 박스돌파, 투매 후 눌림, 임펄스 눌림, 매물소진 눌림,
  전고점 거래폭발 돌파를 대체하지 않는다.
- 새 패턴은 여러 매수타점 중 하나일 뿐이다.
- 전고점 부근에서는 기존 V75.5 거래폭발 게이트가 계속 최종 방어한다.
- 실제 주문 기능은 없고 PAPER 전용이다.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
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
from typing import Any, Sequence

VERSION = "V75.10-MULTI-PATTERN-PRECISION-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7591_FILENAME = "bot_v75_9_1_daily_cumulative_volume_log_fix_paper.py"
V7591_SHA256 = "6a11a73531314539a984343c980b320497a4a554d8b448bfa307eb2449378f0b"

FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7591() -> ModuleType:
    path = BASE_DIR / V7591_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7591_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7591_SHA256:
        raise RuntimeError(
            "V75.9.1 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7591_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7591_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.9.1 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7591 = load_v7591()
v758 = v7591.v758
v757 = v7591.v757
v756 = v7591.v756
v755 = v7591.v755
v754 = v7591.v754
v753 = v7591.v753
KST = v7591.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v756.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v756.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class StairTrendConfig:
    lookback_bars: int = 45
    ignition_search_bars: int = 26
    box_left_bars: int = 12
    min_box_bars: int = 8
    ignition_break_clear_pct: float = 0.001
    ignition_volume_vs_median: float = 2.00
    ignition_volume_vs_left_max: float = 1.05
    ignition_min_body_ratio: float = 0.55
    ignition_min_close_location: float = 0.75
    min_advance_after_ignition: float = 0.015
    max_pullback_from_peak: float = 0.028
    max_close_below_ma5: float = 0.005
    max_low_below_ma10: float = 0.010
    max_ma5_extension: float = 0.025
    active_volume_vs_prebreak_median: float = 1.15
    pullback_volume_vs_ignition: float = 0.70
    bull_to_bear_volume_ratio: float = 1.15
    min_bull_bar_share: float = 0.50
    confirm_min_close_location: float = 0.55
    confirm_max_range_vs_recent: float = 1.40
    structure_stop_max_pct: float = 0.025
    hot_near_peak_pct: float = 0.035
    main_start_delay_sec: int = 65

    @classmethod
    def load(cls) -> "StairTrendConfig":
        return cls(
            ignition_volume_vs_median=env_float("V7510_IGNITION_VOL_VS_MEDIAN", 2.00, minimum=1.20, maximum=8.0),
            ignition_volume_vs_left_max=env_float("V7510_IGNITION_VOL_VS_LEFT_MAX", 1.05, minimum=0.80, maximum=3.0),
            bull_to_bear_volume_ratio=env_float("V7510_BULL_BEAR_VOL_RATIO", 1.15, minimum=1.0, maximum=4.0),
            active_volume_vs_prebreak_median=env_float("V7510_ACTIVE_VOL_VS_PREBREAK", 1.15, minimum=0.70, maximum=5.0),
            max_pullback_from_peak=env_float("V7510_MAX_PULLBACK_FROM_PEAK", 0.028, minimum=0.008, maximum=0.08),
            main_start_delay_sec=env_int("V7510_MAIN_START_DELAY_SEC", 65, minimum=60, maximum=180),
        )


@dataclass(frozen=True, slots=True)
class StairTrendEvidence:
    ignition_time: datetime
    ignition_close: float
    ignition_volume: float
    box_high: float
    volume_vs_left_median: float
    volume_vs_left_max: float
    peak: float
    pullback_pct: float
    ma5: float
    ma10: float
    ma20: float
    bull_volume: float
    bear_volume: float
    bull_bear_ratio: float
    active_volume_ratio: float
    pullback_volume_ratio: float
    support_low: float


def _same_session_bars(strategy: Any, symbol: str, limit: int) -> list[Any]:
    bars = v756._same_session_bars(strategy.bars.get(symbol, []))
    return bars[-limit:]


def _ma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return statistics.fmean(values[-period:])


def _bar_metrics(bar: Any) -> tuple[float, float, float, float]:
    return v756._candle_metrics(bar)


def _latest_ignition(bars: list[Any], cfg: StairTrendConfig) -> tuple[int, Any, float, float, float] | None:
    first = max(cfg.box_left_bars, len(bars) - cfg.ignition_search_bars)
    last = len(bars) - 3
    for idx in range(last, first - 1, -1):
        bar = bars[idx]
        left = bars[max(0, idx - cfg.box_left_bars):idx]
        if len(left) < cfg.min_box_bars:
            continue
        _, signed_body, _, close_location = _bar_metrics(bar)
        if float(bar.close) <= float(bar.open):
            continue
        if signed_body < cfg.ignition_min_body_ratio or close_location < cfg.ignition_min_close_location:
            continue
        box_high = max(float(item.high) for item in left)
        if float(bar.close) < box_high * (1.0 + cfg.ignition_break_clear_pct):
            continue
        left_volumes = [max(0.0, float(item.volume)) for item in left]
        median_volume = statistics.median(left_volumes)
        max_volume = max(left_volumes)
        volume = max(0.0, float(bar.volume))
        vs_median = volume / max(1.0, median_volume)
        vs_max = volume / max(1.0, max_volume)
        if vs_median < cfg.ignition_volume_vs_median:
            continue
        if vs_max < cfg.ignition_volume_vs_left_max:
            continue
        return idx, bar, box_high, vs_median, vs_max
    return None


def detect_staircase_trend_continuation(
    strategy: Any,
    symbol: str,
    *,
    config: StairTrendConfig,
) -> tuple[Any | None, str, StairTrendEvidence | None]:
    """거래가 붙은 점화 뒤 얕은 조정과 양봉거래 우위를 보이는 계단식 추세를 탐지한다."""
    bars = _same_session_bars(strategy, symbol, config.lookback_bars)
    if len(bars) < 24:
        return None, f"계단추세자료부족_{len(bars)}<24", None

    ignition = _latest_ignition(bars, config)
    if ignition is None:
        return None, "계단추세점화봉없음", None
    ignition_idx, ignition_bar, box_high, vol_vs_median, vol_vs_max = ignition
    after = bars[ignition_idx + 1:]
    if len(after) < 3:
        return None, "계단추세점화후봉부족", None

    closes = [float(item.close) for item in bars]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    prev_ma5 = _ma(closes[:-1], 5)
    prev_ma10 = _ma(closes[:-1], 10)
    if min(ma5, ma10, ma20) <= 0:
        return None, "계단추세이평자료부족", None
    if not (ma5 > ma10 > ma20 and ma5 > prev_ma5 and ma10 >= prev_ma10):
        return None, "계단추세이평정배열·상승미충족", None

    current = bars[-1]
    current_close = float(current.close)
    current_low = float(current.low)
    current_range, current_signed_body, _, current_close_location = _bar_metrics(current)
    if current_close <= 0 or float(current.volume) <= 0:
        return None, "계단추세현재봉오류", None
    if current_close < ma5 * (1.0 - config.max_close_below_ma5):
        return None, "계단추세5이평안착실패", None
    if current_low < ma10 * (1.0 - config.max_low_below_ma10):
        return None, "계단추세10이평지지실패", None
    if current_close > ma5 * (1.0 + config.max_ma5_extension):
        return None, "계단추세5이평이격과다", None

    peak = max(float(item.high) for item in after)
    peak_idx_local = max(range(len(after)), key=lambda idx: float(after[idx].high))
    peak_idx = ignition_idx + 1 + peak_idx_local
    advance = peak / max(0.01, float(ignition_bar.close)) - 1.0
    if advance < config.min_advance_after_ignition:
        return None, f"계단추세점화후상승부족_{advance:.2%}", None
    pullback_pct = max(0.0, peak / max(0.01, current_close) - 1.0)
    if pullback_pct > config.max_pullback_from_peak:
        return None, f"계단추세조정과다_{pullback_pct:.2%}", None

    prebreak = bars[max(0, ignition_idx - config.box_left_bars):ignition_idx]
    prebreak_median = statistics.median(max(0.0, float(item.volume)) for item in prebreak)
    trend_window = bars[max(ignition_idx + 1, len(bars) - 10):]
    trend_median = statistics.median(max(0.0, float(item.volume)) for item in trend_window)
    active_volume_ratio = trend_median / max(1.0, prebreak_median)
    if active_volume_ratio < config.active_volume_vs_prebreak_median:
        return None, f"계단추세전체거래활성부족_x{active_volume_ratio:.2f}", None

    # 조정구간은 최근 고점 이후를 우선 사용하고, 너무 짧으면 최근 8봉으로 확장한다.
    flow_start = peak_idx + 1
    if len(bars) - flow_start < 3:
        flow_start = max(ignition_idx + 1, len(bars) - 8)
    flow = bars[flow_start:]
    if len(flow) < 3:
        return None, "계단추세조정흐름부족", None

    bull_bars = [item for item in flow if float(item.close) >= float(item.open)]
    bear_bars = [item for item in flow if float(item.close) < float(item.open)]
    bull_volume = sum(max(0.0, float(item.volume)) for item in bull_bars)
    bear_volume = sum(max(0.0, float(item.volume)) for item in bear_bars)
    bull_bear_ratio = bull_volume / max(1.0, bear_volume)
    bull_share = len(bull_bars) / max(1, len(flow))
    if bull_bear_ratio < config.bull_to_bear_volume_ratio:
        return None, f"계단추세양봉거래우위부족_x{bull_bear_ratio:.2f}", None
    if bull_share < config.min_bull_bar_share:
        return None, f"계단추세양봉수부족_{bull_share:.0%}", None

    pullback_median_volume = statistics.median(max(0.0, float(item.volume)) for item in flow)
    pullback_volume_ratio = pullback_median_volume / max(1.0, float(ignition_bar.volume))
    if pullback_volume_ratio > config.pullback_volume_vs_ignition:
        return None, f"계단추세조정거래미감소_x{pullback_volume_ratio:.2f}", None

    recent_ranges = [max(1e-9, float(item.high) - float(item.low)) for item in bars[-10:-1]]
    median_range = statistics.median(recent_ranges) if recent_ranges else current_range
    if current_range > median_range * config.confirm_max_range_vs_recent:
        return None, "계단추세확인봉폭과대", None
    if current_signed_body < 0:
        return None, "계단추세확인봉음봉", None
    if current_close_location < config.confirm_min_close_location:
        return None, "계단추세확인봉종가약함", None

    support_low = min(ma10, min(float(item.low) for item in flow[-4:]))
    structure_stop_pct = (current_close - support_low) / max(0.01, current_close)
    if structure_stop_pct <= 0 or structure_stop_pct > config.structure_stop_max_pct:
        return None, f"계단추세구조손절폭부적합_{structure_stop_pct:.2%}", None

    evidence = StairTrendEvidence(
        ignition_time=v754.normalize_kst(ignition_bar.timestamp),
        ignition_close=float(ignition_bar.close),
        ignition_volume=float(ignition_bar.volume),
        box_high=box_high,
        volume_vs_left_median=vol_vs_median,
        volume_vs_left_max=vol_vs_max,
        peak=peak,
        pullback_pct=pullback_pct,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        bull_volume=bull_volume,
        bear_volume=bear_volume,
        bull_bear_ratio=bull_bear_ratio,
        active_volume_ratio=active_volume_ratio,
        pullback_volume_ratio=pullback_volume_ratio,
        support_low=support_low,
    )
    score = 118.0
    score += min(8.0, max(0.0, vol_vs_median - config.ignition_volume_vs_median) * 2.0)
    score += min(7.0, max(0.0, bull_bear_ratio - config.bull_to_bear_volume_ratio) * 3.0)
    score += min(5.0, max(0.0, active_volume_ratio - config.active_volume_vs_prebreak_median) * 2.0)
    reason = (
        f"계단식추세지속|점화={evidence.ignition_time:%H:%M}"
        f"|박스돌파={box_high:.0f}|점화거래=중앙x{vol_vs_median:.2f}/왼쪽최대x{vol_vs_max:.2f}"
        f"|전체거래활성=x{active_volume_ratio:.2f}"
        f"|조정양봉거래우위=x{bull_bear_ratio:.2f}"
        f"|조정거래축소=점화봉x{pullback_volume_ratio:.2f}"
        f"|얕은조정={pullback_pct:.2%}|이평=5>{10}>{20}상승"
    )
    stop_pct = min(config.structure_stop_max_pct, max(0.008, structure_stop_pct + 0.002))
    signal = v753.Signal(
        symbol,
        v753.SignalAction.BUY,
        score,
        reason,
        current_close,
        current.timestamp,
        "계단식추세지속1차",
        stop_pct,
        getattr(strategy, "take_profit_pct", 0.015),
    )
    return signal, "계단추세확인", evidence


class MultiPatternPrecisionEngine(v7591.DailyCumulativeVolumeEngine):
    """기존 모든 패턴을 보존하고 계단식 추세 지속형만 독립 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v7510_config = StairTrendConfig.load()
        self._stair_evidence: dict[str, StairTrendEvidence] = {}
        self._stair_block_until: dict[str, float] = {}
        self._last_service_mono: dict[str, float] = {}
        self._install_stair_signal()
        cfg = self.v7510_config
        self.logger.warning(
            "[%s] 계단식추세 추가 | 점화거래 중앙x%.2f/왼쪽최대x%.2f | "
            "조정 양봉거래/음봉거래>=x%.2f | 얕은조정<=%.2f%% | 기존 패턴 전부 유지",
            VERSION,
            cfg.ignition_volume_vs_median,
            cfg.ignition_volume_vs_left_max,
            cfg.bull_to_bear_volume_ratio,
            cfg.max_pullback_from_peak * 100,
        )

    def _install_stair_signal(self) -> None:
        original_on_bar = self.strategy.on_bar
        engine = self

        def wrapped_on_bar(bar: Any, position: Any) -> Any:
            base_signal = original_on_bar(bar, position)
            if position is not None or base_signal.action == v753.SignalAction.SELL:
                return base_signal
            # 기존 전략에서 BUY가 나오면 반드시 기존 전략을 우선한다.
            if base_signal.action == v753.SignalAction.BUY:
                return base_signal
            signal, _, evidence = detect_staircase_trend_continuation(
                engine.strategy,
                bar.symbol,
                config=engine.v7510_config,
            )
            if signal is None or evidence is None:
                return base_signal
            engine._stair_evidence[bar.symbol] = evidence
            fresh, _, _ = v754.signal_freshness(signal, max_age_sec=engine.max_entry_signal_age_sec)
            if not fresh:
                engine._batch_stale_buy.add(signal.symbol)
                engine._stale_reject_bar_time[signal.symbol] = v754.normalize_kst(signal.timestamp)
            return signal

        self.strategy.on_bar = wrapped_on_bar

    def _stair_entry_allowed(self, signal: Any, current_price: float | None = None) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") != "계단식추세지속1차":
            return True, "계단추세패턴아님"
        now_mono = time.monotonic()
        block_until = self._stair_block_until.get(signal.symbol, 0.0)
        if now_mono < block_until:
            return False, f"계단추세차단냉각_{block_until-now_mono:.0f}초"
        evidence = self._stair_evidence.get(signal.symbol)
        bars = _same_session_bars(self.strategy, signal.symbol, self.v7510_config.lookback_bars)
        if evidence is None or not bars:
            return False, "계단추세검증자료없음"
        price = float(current_price) if current_price is not None else float(bars[-1].close)
        if price < evidence.ma10 * (1.0 - self.v7510_config.max_low_below_ma10):
            self._stair_block_until[signal.symbol] = now_mono + 120.0
            return False, "계단추세10이평지지붕괴"
        if price > evidence.ma5 * (1.0 + self.v7510_config.max_ma5_extension):
            return False, "계단추세추격이격과다"
        if min(float(item.low) for item in bars[-3:]) < evidence.support_low * 0.995:
            self._stair_block_until[signal.symbol] = now_mono + 120.0
            return False, "계단추세최근지지저점이탈"
        return True, "계단추세구조유지"

    def _max_chase_for_pattern(self, pattern: str) -> float:
        if pattern == "계단식추세지속1차":
            return 0.007
        return super()._max_chase_for_pattern(pattern)

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason = self._stair_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._stair_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._stair_evidence.pop(signal.symbol, None)
        return entered

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, stair_reason = self._stair_entry_allowed(signal, current_price=current_price)
        if not allowed:
            return False, stair_reason
        return True, stair_reason if getattr(signal, "pattern", "") == "계단식추세지속1차" else reason

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        scored: list[tuple[float, str]] = []
        for symbol in self.active_symbols:
            bars = _same_session_bars(self.strategy, symbol, self.v7510_config.lookback_bars)
            if len(bars) < 20:
                continue
            ignition = _latest_ignition(bars, self.v7510_config)
            if ignition is None:
                continue
            _, ignition_bar, _, vs_median, vs_max = ignition
            recent_peak = max(float(item.high) for item in bars[-10:])
            current_close = float(bars[-1].close)
            if current_close < recent_peak * (1.0 - self.v7510_config.hot_near_peak_pct):
                continue
            score = 70.0 + min(20.0, vs_median * 3.0) + min(10.0, vs_max * 4.0)
            if float(ignition_bar.volume) > 0:
                score += 5.0
            scored.append((score, symbol))
        scored.sort(reverse=True)
        stair_hot = [symbol for _, symbol in scored]
        # 시장하락 보류후보는 부모가 이미 앞에 놓으므로 순서를 보존한다.
        self.hot_symbols = list(dict.fromkeys(self.hot_symbols + stair_hot))[: max(self.hot_candidate_count, 35)]

    def _bootstrap_one(self) -> bool:
        # 09:00 직후에는 완성된 첫 정규장 분봉이 없으므로 09:01:05 전까지 큐를 보존한다.
        now = datetime.now(KST)
        if v753.session_segment(now) == "MAIN":
            seconds = now.hour * 3600 + now.minute * 60 + now.second
            if seconds < 9 * 3600 + self.v7510_config.main_start_delay_sec:
                return False
        return super()._bootstrap_one()

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        # 동일 루프·중복 경로에서 같은 종목을 연속 호출하지 않도록 짧은 중복방지.
        now_mono = time.monotonic()
        minimum_gap = 2.0 if hot else 5.0
        if now_mono - self._last_service_mono.get(symbol, 0.0) < minimum_gap:
            return
        self._last_service_mono[symbol] = now_mono
        super()._service_symbol(symbol, hot=hot)


def _bar(symbol: str, minute: int, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar(symbol, datetime(2026, 7, 10, 9, minute, tzinfo=KST), o, h, l, c, v)


def _stair_case(*, weak_ignition: bool = False, bearish_dominant: bool = False, deep_pullback: bool = False) -> tuple[Any, str]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "751000"
    bars: list[Any] = []
    # 박스권
    for idx in range(14):
        base = 100.0 + (idx % 4) * 0.12
        bars.append(_bar(symbol, idx, base, base + 0.45, base - 0.35, base + 0.12, 500 + (idx % 3) * 30))
    # 대량거래 점화·박스돌파
    bars.append(_bar(symbol, 14, 100.3, 103.0, 100.2, 102.8, 850 if weak_ignition else 2600))
    # 계단식 상승
    normal_volumes = [1500, 1200, 1550, 700, 900, 780, 650, 820, 760]
    if weak_ignition:
        normal_volumes = [620, 600, 610, 560, 590, 570, 550, 580, 560]
    if bearish_dominant:
        normal_volumes = [1000, 950, 980, 2600, 700, 650, 2400, 650, 620]
    bars.extend([
        _bar(symbol, 15, 102.8, 103.8, 102.6, 103.5, normal_volumes[0]),
        _bar(symbol, 16, 103.5, 104.2, 103.2, 103.9, normal_volumes[1]),
        _bar(symbol, 17, 103.9, 105.0, 103.7, 104.7, normal_volumes[2]),
        _bar(symbol, 18, 104.7, 105.2, 104.3, 104.6, normal_volumes[3]),
        _bar(symbol, 19, 104.6, 105.3, 104.5, 105.1, normal_volumes[4]),
        _bar(symbol, 20, 105.1, 105.5, 104.9, 105.3, normal_volumes[5]),
        _bar(symbol, 21, 105.3, 105.7, 105.0, 105.2, normal_volumes[6]),
        _bar(symbol, 22, 105.2, 105.8, 105.1, 105.6, normal_volumes[7]),
        _bar(symbol, 23, 105.6, 106.0, 105.3, 105.8, normal_volumes[8]),
    ])
    if deep_pullback:
        bars[-1] = _bar(symbol, 23, 105.0, 105.2, 101.6, 102.0, 900)
    strategy.bars[symbol].extend(bars)
    strategy.daily_context[symbol] = v753.DailyContext(95.0, 120.0, 0.0, 0.0, True)
    return strategy, symbol


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record("V75.9.1-SHA256고정", sha256_file(BASE_DIR / V7591_FILENAME) == V7591_SHA256, V7591_SHA256[:16] + "…")
    base_results = v7591.self_check()
    base_fail = [name for name, ok, _ in base_results if not ok]
    record("V75.9.1기존검증위임", not base_fail, f"{len(base_results)}항목 / 실패={base_fail or '없음'}")

    cfg = StairTrendConfig()
    strategy, symbol = _stair_case()
    signal, reason, evidence = detect_staircase_trend_continuation(strategy, symbol, config=cfg)
    record(
        "대량점화·양봉거래우위BUY",
        signal is not None and signal.pattern == "계단식추세지속1차" and evidence is not None and evidence.bull_bear_ratio >= cfg.bull_to_bear_volume_ratio,
        signal.reason if signal else reason,
    )

    strategy, symbol = _stair_case(weak_ignition=True)
    signal, reason, _ = detect_staircase_trend_continuation(strategy, symbol, config=cfg)
    record("점화거래부족차단", signal is None, reason)

    strategy, symbol = _stair_case(bearish_dominant=True)
    signal, reason, _ = detect_staircase_trend_continuation(strategy, symbol, config=cfg)
    record("조정음봉거래우위차단", signal is None, reason)

    strategy, symbol = _stair_case(deep_pullback=True)
    signal, reason, _ = detect_staircase_trend_continuation(strategy, symbol, config=cfg)
    record("깊은조정차단", signal is None, reason)

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), f"금지함수={sorted(names & FORBIDDEN_ORDER_NAMES) or '없음'}")
    record(
        "기존패턴우선보존",
        "if base_signal.action == v753.SignalAction.BUY" in source and "return base_signal" in source,
        "기존 BUY가 있으면 새 패턴으로 덮어쓰지 않음",
    )
    record(
        "전고점거래폭발게이트보존",
        "class MultiPatternPrecisionEngine(v7591.DailyCumulativeVolumeEngine)" in source,
        "V75.9.1→V75.8→V75.5 게이트 상속",
    )
    record(
        "09시첫완성봉대기",
        "main_start_delay_sec" in source and "9 * 3600 + self.v7510_config.main_start_delay_sec" in source,
        "기본 09:01:05까지 초기분봉 대기",
    )
    record(
        "중복서비스방지",
        "_last_service_mono" in source and "minimum_gap" in source,
        "HOT 2초/일반 5초 이내 같은 종목 중복호출 차단",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — 복수 매수기법 정밀 PAPER")
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
        settings.assert_credentials()
        if args.quote_test:
            return v753.quote_test(settings, logger, args.symbol)
        if args.scan_test:
            return v753.scan_test(settings, logger)
        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine_logger = v7591.CommaSafeLoggerAdapter(logger, {})
        engine = MultiPatternPrecisionEngine(
            settings,
            client,
            engine_logger,
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
    except Exception as exc:
        logger.critical("실행 실패: %s", exc)
        v753.log_error(traceback.format_exc())
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
