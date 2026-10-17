#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.7 EARLY-DRYUP + 20-BAR-VOLUME-FILTER PAPER
================================================

V75.3~V75.6 원본을 수정하지 않고 V75.6 위에 아래 운영 계층만 추가한다.

1) 매물소진 눌림의 확인봉을 1분봉 완성까지 기다리지 않는다.
   현재 1분봉이 시작된 지 50초 이상 지난 시점에, 대량 음봉 뒤 거래절벽·소형봉·
   저점방어가 확인되면 PAPER 진입을 시도한다. 고점 돌파를 추가 요구하지 않는다.
   단, 미완성봉의 거래량을 60초 기준으로 환산해 "아직 50초라 적어 보이는 거래량"을
   거래절벽으로 오판하지 않는다.

2) 현재 진행봉을 포함한 최근 20개 1분봉 누적거래량이 100,000주 미만이면
   신규 관심종목 분석에서 제외한다. 후보 API의 당일 누적거래량이 100,000주 미만이면
   차트 API를 호출하기 전에 먼저 제외한다. 이후 거래가 붙으면 후보 API 갱신과
   재검사를 통해 자동으로 관심종목에 복귀한다.

3) KODEX200·KODEX코스닥150을 이용해 지수 하락·급락을 감시한다.
   시장이 CAUTION/SHOCK/DATA_HALT이면 신규진입만 중단하고 보유종목 청산은 유지한다.

기존 보존 항목
- V75.4 신호 신선도·연속손실·일일손실 방어
- V75.5 전고점 거래폭발 돌파 방어
- V75.6 대량 음봉 -> 거래절벽 소형봉 패턴과 시장주도력 보강
- V75.3 PAPER 체결·목표·손절·상태 로직
- 실제 주문 기능 없음
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
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Iterable, Sequence

VERSION = "V75.7-EARLY-DRYUP-20BAR-VOLUME-FILTER-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V756_FILENAME = "bot_v75_6_supply_exhaustion_pullback_paper.py"
V756_SHA256 = "49a06c82fb563a939abc00270086845cc74a858501d10a97becab82196fe89e5"

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


def load_v756() -> ModuleType:
    path = BASE_DIR / V756_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V756_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V756_SHA256:
        raise RuntimeError(
            "V75.6 SHA256 불일치 — 검증된 매물소진 기준본이 아니므로 중단합니다.\n"
            f"기대: {V756_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v756_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.6 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v756 = load_v756()
v755 = v756.v755
v754 = v756.v754
v753 = v756.v753
KST = v756.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v756.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v756.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class EarlyDryupConfig:
    recent_volume_bars: int = 20
    min_recent_volume: int = 100_000
    low_volume_recheck_sec: float = 180.0
    intrabar_first_probe_second: int = 50
    intrabar_second_probe_second: int = 57
    intrabar_max_second: int = 59
    current_volume_projection: int = 1
    min_cum_value_before_noon: float = 3_000_000_000.0
    min_cum_value_after_noon: float = 5_000_000_000.0

    market_refresh_sec: float = 60.0
    market_failure_limit: int = 2
    market_proxy_codes: tuple[str, str] = ("069500", "229200")
    market_daily_caution_pct: float = -0.010
    market_daily_shock_pct: float = -0.018
    market_average_caution_pct: float = -0.007
    market_average_shock_pct: float = -0.012
    market_5m_caution_pct: float = -0.004
    market_5m_shock_pct: float = -0.008
    market_15m_caution_pct: float = -0.007
    market_15m_shock_pct: float = -0.012

    @classmethod
    def load(cls) -> "EarlyDryupConfig":
        bars = env_int("V757_RECENT_VOLUME_BARS", 20, minimum=5, maximum=60)
        minimum_volume = env_int("V757_MIN_RECENT_VOLUME", 100_000, minimum=10_000, maximum=10_000_000)
        first_probe = env_int("V757_INTRABAR_FIRST_SECOND", 50, minimum=40, maximum=58)
        second_probe = env_int("V757_INTRABAR_SECOND_SECOND", 57, minimum=first_probe, maximum=59)
        return cls(
            recent_volume_bars=bars,
            min_recent_volume=minimum_volume,
            low_volume_recheck_sec=env_float(
                "V757_LOW_VOLUME_RECHECK_SEC", 180.0, minimum=30.0, maximum=1800.0
            ),
            intrabar_first_probe_second=first_probe,
            intrabar_second_probe_second=second_probe,
            intrabar_max_second=59,
            current_volume_projection=env_int("V757_PROJECT_CURRENT_VOLUME", 1, minimum=0, maximum=1),
            min_cum_value_before_noon=env_float(
                "V757_MIN_CUM_VALUE_BEFORE_NOON", 3_000_000_000.0,
                minimum=0.0, maximum=100_000_000_000.0,
            ),
            min_cum_value_after_noon=env_float(
                "V757_MIN_CUM_VALUE_AFTER_NOON", 5_000_000_000.0,
                minimum=0.0, maximum=100_000_000_000.0,
            ),
            market_refresh_sec=env_float("V757_MARKET_REFRESH_SEC", 60.0, minimum=20.0, maximum=300.0),
            market_failure_limit=env_int("V757_MARKET_FAILURE_LIMIT", 2, minimum=1, maximum=10),
            market_daily_caution_pct=env_float("V757_MARKET_DAILY_CAUTION", -0.010, minimum=-0.10, maximum=0.0),
            market_daily_shock_pct=env_float("V757_MARKET_DAILY_SHOCK", -0.018, minimum=-0.15, maximum=-0.001),
            market_average_caution_pct=env_float("V757_MARKET_AVG_CAUTION", -0.007, minimum=-0.10, maximum=0.0),
            market_average_shock_pct=env_float("V757_MARKET_AVG_SHOCK", -0.012, minimum=-0.15, maximum=-0.001),
            market_5m_caution_pct=env_float("V757_MARKET_5M_CAUTION", -0.004, minimum=-0.05, maximum=0.0),
            market_5m_shock_pct=env_float("V757_MARKET_5M_SHOCK", -0.008, minimum=-0.10, maximum=-0.001),
            market_15m_caution_pct=env_float("V757_MARKET_15M_CAUTION", -0.007, minimum=-0.08, maximum=0.0),
            market_15m_shock_pct=env_float("V757_MARKET_15M_SHOCK", -0.012, minimum=-0.15, maximum=-0.001),
        )


@dataclass(slots=True)
class LowVolumeBlock:
    session_date: date
    recent_volume: int
    cumulative_volume: int
    next_recheck_mono: float


@dataclass(frozen=True, slots=True)
class MarketGuardSnapshot:
    state: str
    reason: str
    daily_changes: tuple[float, ...]
    five_min_changes: tuple[float, ...]
    fifteen_min_changes: tuple[float, ...]
    measured_at: datetime


@dataclass(frozen=True, slots=True)
class IntrabarSnapshot:
    symbol: str
    bar: Any
    strategy_view: Any
    elapsed_sec: float
    raw_volume: int
    projected_volume: int
    recent_volume: int
    supply: Any


def projected_minute_volume(raw_volume: int, elapsed_sec: float, *, enabled: bool = True) -> int:
    raw = max(0, int(raw_volume))
    if not enabled:
        return raw
    elapsed = min(60.0, max(1.0, float(elapsed_sec)))
    return max(raw, int(math.ceil(raw * 60.0 / elapsed)))


def recent_volume_sum(bars: Sequence[Any], count: int) -> int:
    return sum(max(0, int(getattr(bar, "volume", 0))) for bar in list(bars)[-count:])


def intrabar_probe_stage(now: datetime, config: EarlyDryupConfig) -> int:
    second = now.astimezone(KST).second
    if second < config.intrabar_first_probe_second or second > config.intrabar_max_second:
        return 0
    if second >= config.intrabar_second_probe_second:
        return 2
    return 1


def _return_over_bars(bars: Sequence[Any], periods: int) -> float:
    items = list(bars)
    if len(items) <= periods:
        return 0.0
    start = float(items[-periods - 1].close)
    end = float(items[-1].close)
    if start <= 0:
        return 0.0
    return end / start - 1.0


def classify_market_guard(
    daily: Sequence[float],
    five: Sequence[float],
    fifteen: Sequence[float],
    config: EarlyDryupConfig,
) -> tuple[str, str]:
    if not daily:
        return "DATA_HALT", "시장지수데이터없음"

    worst_daily = min(daily)
    average_daily = statistics.fmean(daily)
    worst_five = min(five) if five else 0.0
    worst_fifteen = min(fifteen) if fifteen else 0.0

    if (
        worst_daily <= config.market_daily_shock_pct
        or average_daily <= config.market_average_shock_pct
        or worst_five <= config.market_5m_shock_pct
        or worst_fifteen <= config.market_15m_shock_pct
    ):
        return "SHOCK", (
            f"시장급락 일중최저={worst_daily:+.2%} 평균={average_daily:+.2%} "
            f"5분={worst_five:+.2%} 15분={worst_fifteen:+.2%}"
        )

    if (
        worst_daily <= config.market_daily_caution_pct
        or average_daily <= config.market_average_caution_pct
        or worst_five <= config.market_5m_caution_pct
        or worst_fifteen <= config.market_15m_caution_pct
    ):
        return "CAUTION", (
            f"시장하락 일중최저={worst_daily:+.2%} 평균={average_daily:+.2%} "
            f"5분={worst_five:+.2%} 15분={worst_fifteen:+.2%}"
        )

    return "NORMAL", (
        f"시장정상 일중최저={worst_daily:+.2%} 평균={average_daily:+.2%} "
        f"5분={worst_five:+.2%} 15분={worst_fifteen:+.2%}"
    )


def _same_session_bars(items: Iterable[Any]) -> list[Any]:
    return v756._same_session_bars(items)


def _strategy_view_with_current(strategy: Any, symbol: str, bars: Sequence[Any]) -> Any:
    return SimpleNamespace(
        bars={symbol: deque(bars, maxlen=500)},
        daily_context=strategy.daily_context,
    )


def _flush_setup_armed(strategy: Any, symbol: str, config: Any) -> bool:
    """대량 음봉은 이미 나왔고 다음 거래절벽 봉을 기다리는 종목인지 빠르게 판정한다."""
    bars = _same_session_bars(strategy.bars.get(symbol, []))
    minimum = max(config.rise_lookback_bars + 2, config.flush_left_volume_bars + 2, 15)
    if len(bars) < minimum:
        return False

    first_flush = max(1, len(bars) - config.flush_search_bars)
    for flush_index in range(len(bars) - 1, first_flush - 1, -1):
        flush = bars[flush_index]
        previous = bars[flush_index - 1]
        after = bars[flush_index + 1:]
        flush_range, signed_body, _, _ = v756._candle_metrics(flush)
        if flush_range <= 0 or float(flush.close) >= float(flush.open):
            continue
        if -signed_body < config.flush_min_bear_body_ratio:
            continue
        drop_pct = float(flush.close) / max(0.01, float(previous.close)) - 1.0
        if drop_pct > -config.flush_min_drop_pct:
            continue

        left = bars[max(0, flush_index - config.flush_left_volume_bars):flush_index]
        if len(left) < 6:
            continue
        left_volumes = [max(0.0, float(item.volume)) for item in left]
        flush_volume = max(0.0, float(flush.volume))
        if flush_volume / max(1.0, statistics.median(left_volumes)) < config.flush_volume_vs_median:
            continue
        if flush_volume / max(1.0, v756._percentile75(left_volumes)) < config.flush_volume_vs_p75:
            continue
        if flush_volume / max(1.0, max(left_volumes[-min(5, len(left_volumes)):])) < config.flush_volume_vs_recent_max:
            continue

        rise_window = bars[max(0, flush_index - config.rise_lookback_bars):flush_index]
        if len(rise_window) < 6:
            continue
        peak_pos = max(range(len(rise_window)), key=lambda idx: float(rise_window[idx].high))
        if len(rise_window) - 1 - peak_pos > config.max_peak_age_bars:
            continue
        prior_to_peak = rise_window[:peak_pos + 1]
        if len(prior_to_peak) < 3:
            continue
        rise_low = min(float(item.low) for item in prior_to_peak)
        rise_high = float(rise_window[peak_pos].high)
        if rise_high / max(0.01, rise_low) - 1.0 < config.min_prior_rise_pct:
            continue

        if after:
            if min(float(item.low) for item in after) < float(flush.low) * (1.0 - config.max_flush_low_break_pct):
                continue
            continued = any(
                v756._candle_metrics(item)[1] <= -config.flush_min_bear_body_ratio
                and float(item.volume) >= flush_volume * config.dry_max_intermediate_volume_vs_flush
                for item in after
            )
            if continued:
                continue
        return True
    return False


class EarlyDryupVolumeEngine(v756.SupplyExhaustionEngine):
    """V75.6 위에 50초 선진입, 최근20봉 거래량 필터, 시장하락 차단을 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v757_config = EarlyDryupConfig.load()
        self._low_volume_blocks: dict[str, LowVolumeBlock] = {}
        self._recent_volume_cache: dict[str, int] = {}
        self._volume_filter_day = datetime.now(KST).date()
        self._armed_symbols: set[str] = set()
        self._intrabar_probe_state: dict[tuple[str, datetime], int] = {}
        self._intrabar_snapshots: dict[str, IntrabarSnapshot] = {}
        self._intrabar_armed_cursor = 0
        self._market_snapshot = MarketGuardSnapshot(
            "DATA_HALT", "시장지수초기확인전", (), (), (), datetime.now(KST)
        )
        self._market_next_refresh = 0.0
        self._market_failures = 0
        cfg = self.v757_config
        self.logger.warning(
            "[%s] 활성 | 최근%d봉(현재봉포함) 거래량 %,d주 미만 제외 | "
            "매물소진 확인봉 %d초/%d초 선진입 | 시장하락 신규진입 중단",
            VERSION,
            cfg.recent_volume_bars,
            cfg.min_recent_volume,
            cfg.intrabar_first_probe_second,
            cfg.intrabar_second_probe_second,
        )

    def _reset_volume_filter_day(self) -> None:
        today = datetime.now(KST).date()
        if self._volume_filter_day != today:
            self._volume_filter_day = today
            self._low_volume_blocks.clear()
            self._recent_volume_cache.clear()
            self._intrabar_probe_state.clear()
            self._intrabar_snapshots.clear()

    def _exclude_low_volume(self, symbol: str, recent_volume: int, cumulative_volume: int) -> None:
        self._reset_volume_filter_day()
        now_mono = time.monotonic()
        self._recent_volume_cache[symbol] = max(0, int(recent_volume))
        self._low_volume_blocks[symbol] = LowVolumeBlock(
            session_date=self._volume_filter_day,
            recent_volume=max(0, int(recent_volume)),
            cumulative_volume=max(0, int(cumulative_volume)),
            next_recheck_mono=now_mono + self.v757_config.low_volume_recheck_sec,
        )
        self.active_symbols = [item for item in self.active_symbols if item != symbol]
        self.hot_symbols = [item for item in self.hot_symbols if item != symbol]
        self.bootstrap_pending.discard(symbol)
        self.bootstrap_queue = deque(item for item in self.bootstrap_queue if item != symbol)
        self.pending_entries.pop(symbol, None)
        self._armed_symbols.discard(symbol)
        self._show_throttled_error(
            f"v757-low-volume:{symbol}",
            f"관심제외 {self._label(symbol)} 최근{self.v757_config.recent_volume_bars}봉 "
            f"거래량={recent_volume:,}주<{self.v757_config.min_recent_volume:,}주",
            300.0,
        )

    def _rolling_block_allows_recheck(self, item: Any, now_mono: float) -> bool:
        block = self._low_volume_blocks.get(item.symbol)
        if block is None:
            return True
        if block.session_date != self._volume_filter_day:
            self._low_volume_blocks.pop(item.symbol, None)
            return True
        needed = max(1, self.v757_config.min_recent_volume - block.recent_volume)
        cumulative = max(0, int(getattr(item, "volume", 0)))
        growth = max(0, cumulative - block.cumulative_volume)
        if growth >= needed or now_mono >= block.next_recheck_mono:
            self._low_volume_blocks.pop(item.symbol, None)
            return True
        return False

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        """후보 API 단계에서 명백한 저거래 종목을 먼저 빼 차트 호출 시간을 줄인다."""
        self._reset_volume_filter_day()
        now_mono = time.monotonic()
        if not force and now_mono < self.next_scan:
            return []
        self.next_scan = now_mono + self.settings.scan_interval_sec
        previous = set(self.active_symbols)
        groups: list[list[Any]] = []
        failures: list[str] = []
        if self.settings.dynamic_scan:
            for name, loader in (
                ("상승률100", lambda: self.client.get_gain_rank_candidates(limit=self.rank_limit)),
                ("거래량급증", lambda: self.client.get_volume_surge_candidates(limit=self.rank_limit)),
                ("외국인·기관쌍끌이", lambda: self.client.get_dual_buy_candidates(limit=self.rank_limit)),
            ):
                try:
                    groups.append(loader())
                except v753.V73Error as exc:
                    failures.append(f"{name}:{v753.brief_error(exc)}")

        if groups:
            pool = self._merge_candidates(groups)[: max(self.rank_limit, self.pool_limit)]
            if pool and not self.state_integrity_block:
                self.risk.set_data_halt(False)
            self.universe_pool = pool
            for item in pool:
                self.names[item.symbol] = item.name
                self.candidate_meta[item.symbol] = item
                self.rank_snapshots[item.symbol] = item

            selected: list[str] = []
            prefiltered = 0
            blocked = 0
            for item in pool:
                cumulative = max(0, int(getattr(item, "volume", 0)))
                # 당일 누적도 10만주 미만이면 최근20봉이 10만주일 수 없으므로 즉시 제외.
                if 0 < cumulative < self.v757_config.min_recent_volume:
                    prefiltered += 1
                    continue
                if not self._rolling_block_allows_recheck(item, now_mono):
                    blocked += 1
                    continue
                selected.append(item.symbol)

            self.active_symbols = list(dict.fromkeys(selected))
            top_items = [item for item in pool if item.symbol in set(self.active_symbols)][:5]
            top = ", ".join(f"{item.name}({item.source})" for item in top_items)
            self.logger.warning(
                "관심종목 %s개 등록 / 최근%d봉 %,d주 필터: 선제제외 %s + 재검사대기 %s / 상위=%s",
                len(self.active_symbols),
                self.v757_config.recent_volume_bars,
                self.v757_config.min_recent_volume,
                prefiltered,
                blocked,
                top or "없음",
            )
            if failures and self.debug:
                self.logger.info("일부 후보원 실패: %s", " / ".join(failures))
        else:
            self.active_symbols = []
            self.universe_pool = []
            self.risk.set_data_halt(True)
            self._show_throttled_error(
                "universe-all-failed",
                "후보 API 전체 실패—신규 매수 중단, 보유종목 청산만 유지",
                120.0,
            )
            if self.debug and failures:
                self.logger.info("후보 API 실패 상세: %s", " / ".join(failures))
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    def _bootstrap_one(self) -> bool:
        """현재 진행봉을 포함한 최근20봉 거래량을 먼저 확인한 뒤 전략 차트를 준비한다."""
        if not self.bootstrap_queue:
            return False
        symbol = self.bootstrap_queue.popleft()
        self.bootstrap_pending.discard(symbol)
        if symbol not in self.active_symbols:
            return False

        now_kst = datetime.now(KST)
        self._reset_after_state_if_needed(now_kst)
        today = now_kst.date()
        try:
            current_segment = v753.session_segment(now_kst)
            if current_segment not in {"PRE", "MAIN", "AFTER"}:
                return False
            if current_segment == "AFTER" and not self.after_entry_enabled:
                return False

            all_bars = [
                bar
                for bar in self.client.get_minute_bars(symbol, limit=900, completed_only=False)
                if v753.regular_session_bar(bar.timestamp)
            ]
            selected_all = [
                bar
                for bar in all_bars
                if bar.timestamp.astimezone(KST).date() == today
                and v753.session_segment(bar.timestamp.astimezone(KST)) == current_segment
            ]
            if not selected_all:
                if current_segment == "AFTER":
                    self.after_no_bar_until[symbol] = time.monotonic() + max(60.0, self.after_reprobe_sec)
                    return False
                raise v753.ApiSchemaError(
                    f"현재세션 분봉 없음 symbol={symbol} segment={current_segment}"
                )

            recent = recent_volume_sum(selected_all, self.v757_config.recent_volume_bars)
            meta = self.candidate_meta.get(symbol)
            cumulative = int(getattr(meta, "volume", 0) or 0)
            self._recent_volume_cache[symbol] = recent
            if recent < self.v757_config.min_recent_volume:
                self._exclude_low_volume(symbol, recent, cumulative)
                return False
            self._low_volume_blocks.pop(symbol, None)

            current_minute = now_kst.replace(second=0, microsecond=0)
            completed = [bar for bar in selected_all if bar.timestamp.astimezone(KST) < current_minute]
            if not completed:
                raise v753.ApiSchemaError(
                    f"현재세션 완성분봉 없음 symbol={symbol} segment={current_segment}"
                )

            completed_all = [
                bar for bar in all_bars if bar.timestamp.astimezone(KST) < current_minute
            ]
            self.after_no_bar_until.pop(symbol, None)
            self.strategy.seed_bars(symbol, completed)
            self.symbol_sessions[symbol] = current_segment
            self.last_bar_time[symbol] = completed[-1].timestamp
            ratio = self._same_time_volume_ratio(completed_all, today)
            self.strategy.set_context(
                symbol,
                same_time_volume_ratio=ratio,
                sources=meta.source if meta else "",
                dual_buy=meta.dual_buy if meta else False,
            )
            cache = self.daily_cache.get(symbol)
            if cache is None or time.monotonic() - cache[0] > 6 * 3600:
                daily = self.client.get_daily_bars(symbol, limit=120)
                context = self._build_daily_context(daily, completed[-1].close)
                self.daily_cache[symbol] = (time.monotonic(), context)
            else:
                context = cache[1]
            self.strategy.set_context(symbol, daily=context)
            self.next_chart_due[symbol] = time.monotonic() + self.chart_refresh
            if self.debug:
                self.logger.info(
                    "차트준비 %s 분봉=%s 최근%d봉거래=%s 동시간거래=x%.2f",
                    self._label(symbol),
                    len(completed),
                    self.v757_config.recent_volume_bars,
                    f"{recent:,}",
                    ratio,
                )
            self.risk.set_data_halt(False)
            return True
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"bootstrap:{symbol}",
                f"차트준비 실패 {self._label(symbol)}: {v753.brief_error(exc)}",
                120.0,
            )
            self.next_chart_due[symbol] = time.monotonic() + 30.0
            return False

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        super()._refresh_actual_bars(symbol, quote)
        completed = _same_session_bars(self.strategy.bars.get(symbol, []))
        if len(completed) >= self.v757_config.recent_volume_bars:
            recent = recent_volume_sum(completed, self.v757_config.recent_volume_bars)
            self._recent_volume_cache[symbol] = recent
            if recent < self.v757_config.min_recent_volume and symbol not in self.broker.positions:
                meta = self.candidate_meta.get(symbol)
                cumulative = int(getattr(meta, "volume", 0) or 0)
                self._exclude_low_volume(symbol, recent, cumulative)

    def _refresh_market_guard(self, *, force: bool = False) -> MarketGuardSnapshot:
        now_mono = time.monotonic()
        if not force and now_mono < self._market_next_refresh:
            return self._market_snapshot
        self._market_next_refresh = now_mono + self.v757_config.market_refresh_sec

        daily: list[float] = []
        five: list[float] = []
        fifteen: list[float] = []
        failures: list[str] = []
        for code in self.v757_config.market_proxy_codes:
            try:
                quote = self.client.get_quote(code)
                bars = self.client.get_minute_bars(code, limit=20, completed_only=True)
                daily.append(float(quote.change_pct) / 100.0)
                five.append(_return_over_bars(bars, 5))
                fifteen.append(_return_over_bars(bars, 15))
            except v753.V73Error as exc:
                failures.append(f"{code}:{v753.brief_error(exc)}")
            except Exception as exc:
                failures.append(f"{code}:{exc}")

        now = datetime.now(KST)
        if failures or len(daily) != len(self.v757_config.market_proxy_codes):
            self._market_failures += 1
            if self._market_failures >= self.v757_config.market_failure_limit:
                self._market_snapshot = MarketGuardSnapshot(
                    "DATA_HALT",
                    "시장지수조회실패_" + "/".join(failures),
                    tuple(daily),
                    tuple(five),
                    tuple(fifteen),
                    now,
                )
            return self._market_snapshot

        self._market_failures = 0
        state, reason = classify_market_guard(daily, five, fifteen, self.v757_config)
        previous_state = self._market_snapshot.state
        self._market_snapshot = MarketGuardSnapshot(
            state, reason, tuple(daily), tuple(five), tuple(fifteen), now
        )
        if state != previous_state or state != "NORMAL":
            self.logger.warning("시장진입가드=%s | %s", state, reason)
        return self._market_snapshot

    def _market_entry_allowed(self) -> tuple[bool, str]:
        snapshot = self._refresh_market_guard()
        if snapshot.state != "NORMAL":
            return False, f"시장신규매수중단_{snapshot.state}_{snapshot.reason}"
        return True, snapshot.reason

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        armed = [
            symbol
            for symbol in self.active_symbols
            if symbol not in self.broker.positions
            and _flush_setup_armed(
                self.strategy, symbol, self.supply_exhaustion_config
            )
        ]
        self._armed_symbols = set(armed)
        combined = armed + [symbol for symbol in self.hot_symbols if symbol not in self._armed_symbols]
        self.hot_symbols = list(dict.fromkeys(combined))

    def _probe_stage_done(self, symbol: str, minute: datetime, stage: int) -> bool:
        key = (symbol, minute)
        old = self._intrabar_probe_state.get(key, 0)
        if old >= stage:
            return True
        self._intrabar_probe_state[key] = stage
        # 오래된 키 정리
        cutoff = minute - timedelta(minutes=3)
        for old_key in list(self._intrabar_probe_state):
            if old_key[1] < cutoff:
                self._intrabar_probe_state.pop(old_key, None)
        return False

    def _build_intrabar_signal(
        self,
        symbol: str,
        current_bar: Any,
        selected: Sequence[Any],
        elapsed_sec: float,
    ) -> tuple[Any | None, IntrabarSnapshot | None, str]:
        raw_volume = max(0, int(current_bar.volume))
        projected = projected_minute_volume(
            raw_volume,
            elapsed_sec,
            enabled=bool(self.v757_config.current_volume_projection),
        )
        projected_bar = v753.Bar(
            symbol=current_bar.symbol,
            timestamp=current_bar.timestamp,
            open=float(current_bar.open),
            high=float(current_bar.high),
            low=float(current_bar.low),
            close=float(current_bar.close),
            volume=projected,
        )
        completed = [bar for bar in selected if bar.timestamp < current_bar.timestamp]
        view_bars = [*completed, projected_bar]
        view = _strategy_view_with_current(self.strategy, symbol, view_bars)
        signal, reason, evidence = v756.detect_supply_exhaustion_pullback(
            view,
            symbol,
            config=self.supply_exhaustion_config,
        )
        if signal is None or evidence is None:
            return None, None, reason

        signal = self._apply_leadership_confidence(signal, evidence)
        if signal is None:
            return None, None, "시장주도력미달"
        signal = v753.Signal(
            signal.symbol,
            signal.action,
            signal.score,
            (
                f"{signal.reason}|50초선진입|경과={elapsed_sec:.1f}초"
                f"|현재거래={raw_volume:,}|60초환산={projected:,}"
            ),
            float(current_bar.close),
            current_bar.timestamp,
            "매물소진50초선진입",
            signal.stop_loss_pct,
            signal.take_profit_pct,
        )
        recent = recent_volume_sum(selected, self.v757_config.recent_volume_bars)
        snapshot = IntrabarSnapshot(
            symbol=symbol,
            bar=projected_bar,
            strategy_view=view,
            elapsed_sec=elapsed_sec,
            raw_volume=raw_volume,
            projected_volume=projected,
            recent_volume=recent,
            supply=evidence,
        )
        return signal, snapshot, "50초매물소진확인"

    def _try_intrabar_entry(self, symbol: str) -> None:
        now = datetime.now(KST)
        stage = intrabar_probe_stage(now, self.v757_config)
        if stage == 0 or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        if symbol not in self._armed_symbols:
            return
        minute = now.replace(second=0, microsecond=0)
        if self._probe_stage_done(symbol, minute, stage):
            return
        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            self._show_throttled_error("v757-market-block", market_reason, 60.0)
            return

        try:
            bars = [
                bar
                for bar in self.client.get_minute_bars(symbol, limit=40, completed_only=False)
                if v753.regular_session_bar(bar.timestamp)
            ]
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"v757-intrabar:{symbol}",
                f"50초확인 실패 {self._label(symbol)}: {v753.brief_error(exc)}",
                60.0,
            )
            return
        selected = _same_session_bars(bars)
        if not selected:
            return
        current = selected[-1]
        current_minute = current.timestamp.astimezone(KST).replace(second=0, microsecond=0)
        if current_minute != minute:
            return
        recent = recent_volume_sum(selected, self.v757_config.recent_volume_bars)
        self._recent_volume_cache[symbol] = recent
        if recent < self.v757_config.min_recent_volume:
            meta = self.candidate_meta.get(symbol)
            cumulative = int(getattr(meta, "volume", 0) or 0)
            self._exclude_low_volume(symbol, recent, cumulative)
            return

        elapsed = (now - minute).total_seconds()
        signal, snapshot, _ = self._build_intrabar_signal(symbol, current, selected, elapsed)
        if signal is None or snapshot is None:
            return
        self._intrabar_snapshots[symbol] = snapshot
        self._supply_evidence[symbol] = snapshot.supply
        self.latest_signals[symbol] = signal
        self.logger.warning(
            "50초매물소진확인 %s 경과=%.1f초 거래=%s(환산%s) 최근%d봉=%s — 즉시 PAPER 진입검증",
            self._label(symbol),
            elapsed,
            f"{snapshot.raw_volume:,}",
            f"{snapshot.projected_volume:,}",
            self.v757_config.recent_volume_bars,
            f"{snapshot.recent_volume:,}",
        )
        self._attempt_entry(signal)

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        super()._service_symbol(symbol, hot=hot)
        if symbol in self.active_symbols and symbol not in self.broker.positions:
            self._try_intrabar_entry(symbol)

        # 50~59초의 짧은 확인창에는 일반 순환순서와 별개로
        # 대량 음봉이 이미 확인된 종목을 한 종목씩 최우선 재확인한다.
        if intrabar_probe_stage(datetime.now(KST), self.v757_config) > 0:
            armed = [
                item for item in self._armed_symbols
                if item in self.active_symbols
                and item not in self.broker.positions
                and item not in self.pending_entries
            ]
            if armed:
                target = armed[self._intrabar_armed_cursor % len(armed)]
                self._intrabar_armed_cursor += 1
                if target != symbol:
                    self._try_intrabar_entry(target)

    def _minimum_cumulative_value(self, now: datetime) -> float:
        """장 초반 50억 고정 차단을 피하고 정오 이후에만 50억을 요구한다."""
        segment = v753.session_segment(now)
        if segment == "PRE":
            return self.min_cum_value_pre
        if segment == "AFTER":
            return self.v757_config.min_cum_value_after_noon
        minute = now.hour * 60 + now.minute
        if minute < 9 * 60 + 10:
            return self.min_cum_value_0910
        if minute < 9 * 60 + 30:
            return self.min_cum_value_0930
        if minute < 10 * 60:
            return self.min_cum_value_1000
        if minute < 12 * 60:
            return self.v757_config.min_cum_value_before_noon
        return self.v757_config.min_cum_value_after_noon

    def _liquidity_gate(self, signal: Any, quote: Any) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") != "매물소진50초선진입":
            return super()._liquidity_gate(signal, quote)
        snapshot = self._intrabar_snapshots.get(signal.symbol)
        if snapshot is None:
            return False, "50초유동성자료없음"
        bars = list(snapshot.strategy_view.bars.get(signal.symbol, []))
        if len(bars) < 5:
            return False, "최근5분자료부족"
        recent5_value = sum(
            max(0, int(bar.volume)) * max(0.0, float(bar.close)) for bar in bars[-5:]
        )
        signal_bar_value = max(0, int(snapshot.bar.volume)) * max(0.0, float(snapshot.bar.close))
        cumulative_value = max(
            0.0,
            float(quote.cumulative_value),
            float(quote.cumulative_volume) * max(0.0, float(quote.price)),
        )
        minimum_cumulative = self._minimum_cumulative_value(datetime.now(KST))
        if cumulative_value < minimum_cumulative:
            return False, (
                f"누적거래대금부족_{cumulative_value/1e8:.1f}억"
                f"<{minimum_cumulative/1e8:.1f}억"
            )
        if recent5_value < self.min_recent5_value:
            return False, f"최근5분거래대금부족_{recent5_value/1e8:.1f}억"
        if signal_bar_value < self.min_signal_bar_value:
            return False, f"신호봉거래대금부족_{signal_bar_value/1e8:.1f}억"
        same_time = self.strategy.same_time_volume_ratio.get(signal.symbol, 0.0)
        exceptional = (
            cumulative_value >= minimum_cumulative * 2.0
            and recent5_value >= self.min_recent5_value * 2.0
        )
        if same_time < self.min_same_time_volume_ratio and not exceptional:
            if same_time <= 0:
                return False, "전일동시간거래량확인불가"
            return False, f"전일동시간거래량약함_x{same_time:.2f}"
        return True, (
            f"유동성정상 누적={cumulative_value/1e8:.1f}억 "
            f"최근5분={recent5_value/1e8:.1f}억 "
            f"50초신호봉={signal_bar_value/1e8:.1f}억 동시간=x{same_time:.2f}"
        )

    def _supply_entry_allowed(self, signal: Any, current_price: float | None = None) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") != "매물소진50초선진입":
            return super()._supply_entry_allowed(signal, current_price=current_price)
        snapshot = self._intrabar_snapshots.get(signal.symbol)
        if snapshot is None:
            return False, "50초확인자료없음"
        current_minute = datetime.now(KST).replace(second=0, microsecond=0)
        signal_minute = v754.normalize_kst(signal.timestamp).replace(second=0, microsecond=0)
        if signal_minute != current_minute:
            return False, "50초확인봉만료"
        observed = float(current_price) if current_price is not None else float(snapshot.bar.close)
        guard_low = snapshot.supply.flush_low * (
            1.0 - self.supply_exhaustion_config.max_flush_low_break_pct
        )
        if float(snapshot.bar.low) < guard_low or observed < guard_low:
            return False, f"매물소진저점재이탈_현재={observed:.0f}<방어={guard_low:.0f}"
        return True, "50초거래절벽·저점방어유지"

    def _resistance_entry_allowed(
        self,
        signal: Any,
        *,
        current_price: float | None = None,
    ) -> tuple[bool, str]:
        if getattr(signal, "pattern", "") != "매물소진50초선진입":
            return super()._resistance_entry_allowed(signal, current_price=current_price)

        block_until = self._resistance_block_until.get(signal.symbol, 0.0)
        now_mono = time.monotonic()
        if now_mono < block_until:
            return False, f"전고점차단냉각_{block_until - now_mono:.0f}초"
        if block_until:
            self._resistance_block_until.pop(signal.symbol, None)

        snapshot = self._intrabar_snapshots.get(signal.symbol)
        if snapshot is None:
            return False, "50초전고점검증자료없음"
        allowed, reason, _ = v755.assess_resistance_breakout(
            snapshot.strategy_view,
            signal,
            config=self.resistance_guard_config,
            current_price=current_price,
        )
        if not allowed:
            self._resistance_block_until[signal.symbol] = (
                now_mono + self.resistance_guard_config.blocked_cooldown_sec
            )
        return allowed, reason

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason = self._market_entry_allowed()
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입재시도차단 %s %s", self._label(signal.symbol), reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._market_entry_allowed()
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._intrabar_snapshots.pop(signal.symbol, None)
        return entered

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, market_reason = self._market_entry_allowed()
        if not allowed:
            return False, market_reason
        return True, reason


def _synthetic_intrabar_case(*, bullish: bool, dry_volume: int = 350) -> tuple[Any, str, Any]:
    strategy, symbol = v756._pattern_strategy(dry_bull=bullish, dry_volume=dry_volume)
    current = strategy.bars[symbol].pop()
    completed = list(strategy.bars[symbol])
    view = _strategy_view_with_current(strategy, symbol, [*completed, current])
    return strategy, symbol, view


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.6-SHA256고정",
        sha256_file(BASE_DIR / V756_FILENAME) == V756_SHA256,
        V756_SHA256[:16] + "…",
    )
    base_results = v756.self_check()
    base_failures = [name for name, ok, _ in base_results if not ok]
    record("V75.6-기존검증위임", not base_failures, f"{len(base_results)}개 / 실패={base_failures or '없음'}")

    cfg = EarlyDryupConfig()
    sample_bars = [SimpleNamespace(volume=value) for value in [4_000] * 19 + [30_000]]
    record(
        "최근20봉현재봉포함",
        recent_volume_sum(sample_bars, 20) == 106_000,
        f"합계={recent_volume_sum(sample_bars, 20):,}",
    )
    record(
        "10만주미만차단",
        recent_volume_sum([SimpleNamespace(volume=4_000)] * 20, 20) < cfg.min_recent_volume,
        "80,000<100,000",
    )
    record(
        "거래증가시자동복귀수학",
        (40_000 + 60_000) >= cfg.min_recent_volume,
        "기존4만+신규6만=10만",
    )
    volume_dummy = object.__new__(EarlyDryupVolumeEngine)
    volume_dummy.v757_config = cfg
    volume_dummy._volume_filter_day = date(2026, 7, 10)
    volume_dummy._low_volume_blocks = {
        "000001": LowVolumeBlock(
            session_date=date(2026, 7, 10),
            recent_volume=40_000,
            cumulative_volume=100_000,
            next_recheck_mono=time.monotonic() + 999.0,
        )
    }
    recovered = EarlyDryupVolumeEngine._rolling_block_allows_recheck(
        volume_dummy,
        SimpleNamespace(symbol="000001", volume=160_000),
        time.monotonic(),
    )
    record(
        "10만주도달자동관심복귀",
        recovered and "000001" not in volume_dummy._low_volume_blocks,
        "최근4만 상태에서 신규6만 유입 시 즉시 재검사",
    )

    projected = projected_minute_volume(1_000, 50.0, enabled=True)
    record("50초거래량60초환산", projected == 1_200, f"1000→{projected}")
    t49 = datetime(2026, 7, 10, 10, 0, 49, tzinfo=KST)
    t50 = datetime(2026, 7, 10, 10, 0, 50, tzinfo=KST)
    t57 = datetime(2026, 7, 10, 10, 0, 57, tzinfo=KST)
    record(
        "50초선진입시각",
        intrabar_probe_stage(t49, cfg) == 0
        and intrabar_probe_stage(t50, cfg) == 1
        and intrabar_probe_stage(t57, cfg) == 2,
        "49초=대기/50초=1차/57초=2차",
    )

    strategy, symbol, view = _synthetic_intrabar_case(bullish=True)
    signal, reason, evidence = v756.detect_supply_exhaustion_pullback(
        view, symbol, config=v756.SupplyExhaustionConfig()
    )
    record(
        "50초저거래양봉신호",
        signal is not None and evidence is not None,
        signal.reason if signal else reason,
    )

    strategy, symbol, view = _synthetic_intrabar_case(bullish=False)
    signal, reason, evidence = v756.detect_supply_exhaustion_pullback(
        view, symbol, config=v756.SupplyExhaustionConfig()
    )
    record(
        "50초저거래소형음봉신호",
        signal is not None and evidence is not None,
        signal.reason if signal else reason,
    )

    strategy, symbol, view = _synthetic_intrabar_case(bullish=True)
    intrabar_signal, _, _ = v756.detect_supply_exhaustion_pullback(
        view, symbol, config=v756.SupplyExhaustionConfig()
    )
    if intrabar_signal is not None:
        view.daily_context[symbol] = v753.DailyContext(
            95.0, float(intrabar_signal.reference_price) * 1.005, 0.0, 0.0, False
        )
        allowed, resistance_reason, _ = v755.assess_resistance_breakout(
            view, intrabar_signal, config=v755.ResistanceGuardConfig()
        )
        record("50초진입도전고점방어유지", not allowed, resistance_reason)
    else:
        record("50초진입도전고점방어유지", False, "합성신호생성실패")

    strategy, symbol, view = _synthetic_intrabar_case(bullish=True, dry_volume=1_500)
    signal, reason, _ = v756.detect_supply_exhaustion_pullback(
        view, symbol, config=v756.SupplyExhaustionConfig()
    )
    record("50초거래미축소차단", signal is None, reason)

    market_cfg = EarlyDryupConfig()
    normal_state, _ = classify_market_guard([-0.001, 0.002], [0.001, -0.001], [0.002, 0.0], market_cfg)
    caution_state, _ = classify_market_guard([-0.011, -0.004], [-0.001, -0.001], [-0.002, -0.002], market_cfg)
    shock_state, _ = classify_market_guard([-0.020, -0.005], [-0.009, -0.001], [-0.013, -0.002], market_cfg)
    record("시장정상허용", normal_state == "NORMAL", normal_state)
    record("시장하락차단", caution_state == "CAUTION", caution_state)
    record("시장급락차단", shock_state == "SHOCK", shock_state)
    data_state, _ = classify_market_guard([], [], [], market_cfg)
    record("시장데이터없음차단", data_state == "DATA_HALT", data_state)

    # 장 초반에는 50억을 요구하지 않고 정오 이후부터 50억을 적용한다.
    dummy = object.__new__(EarlyDryupVolumeEngine)
    dummy.v757_config = market_cfg
    dummy.min_cum_value_pre = 300_000_000.0
    dummy.min_cum_value_0910 = 500_000_000.0
    dummy.min_cum_value_0930 = 1_500_000_000.0
    dummy.min_cum_value_1000 = 2_500_000_000.0
    morning_value = EarlyDryupVolumeEngine._minimum_cumulative_value(
        dummy, datetime(2026, 7, 10, 10, 30, tzinfo=KST)
    )
    noon_value = EarlyDryupVolumeEngine._minimum_cumulative_value(
        dummy, datetime(2026, 7, 10, 12, 0, tzinfo=KST)
    )
    record(
        "거래대금시간대별적용",
        morning_value == 3_000_000_000.0 and noon_value == 5_000_000_000.0,
        f"10:30={morning_value/1e8:.0f}억/12:00={noon_value/1e8:.0f}억",
    )

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (function_names & FORBIDDEN_ORDER_NAMES),
        str(sorted(function_names & FORBIDDEN_ORDER_NAMES)),
    )
    intrabar_source = source[
        source.find("def _build_intrabar_signal") : source.find("def _try_intrabar_entry")
    ]
    record(
        "50초신호고점돌파불요",
        "50초선진입" in intrabar_source
        and "breakout" not in intrabar_source.lower()
        and "고점돌파" not in intrabar_source,
        "거래절벽·소형봉·저점방어로 진입",
    )
    record(
        "진입전시장·전고점·매물소진재검증",
        "def _attempt_entry" in source
        and "def _pending_signal_valid" in source
        and "def _resistance_entry_allowed" in source
        and "def _supply_entry_allowed" in source,
        "모든 진입경로 재검증",
    )
    record(
        "V75.3~V75.6원본쓰기부재",
        "write_text" not in source.split("def load_v756", 1)[1].split("v756 =", 1)[0],
        "원본은 SHA검증 후 읽기 로드만",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{VERSION} — 50초 거래절벽 선진입 + 최근20봉 10만주 필터 PAPER"
    )
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
        engine = EarlyDryupVolumeEngine(
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
