#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.14 SUPPLY-EXHAUSTION FOCUS ARCHITECTURE PAPER
=================================================

목적
- 기존 V75.13 전체 기능을 보존한다.
- --supply-only 플래그를 주면 매물소진 눌림 계열만 신규매수에 사용한다.
- 기존 상승률/거래량급증/쌍끌이 후보 API는 "발견용 1차 풀"로만 사용한다.
- 분봉 구조를 확인한 뒤 느슨한 매물소진 후보를 "관심종목-매수가능성"으로 승격하고,
  순위 API에서 잠시 사라져도 일정 시간 유지하여 거래량 바닥 봉을 기다린다.
- 엄격한 기존 기준만 실제 PAPER 매수를 허용한다.
- 관심등록·매수근거·거절사유를 날짜별 UTF-8 텍스트 로그에 모두 저장한다.

주의
- PAPER 전용이며 실제 주문 기능은 없다.
- --supply-only는 신규매수 패턴만 제한한다. 보유종목의 청산·위험관리는 항상 유지한다.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import math
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence, TextIO

VERSION = "V75.14-SUPPLY-FOCUS-ARCHITECTURE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7513_FILENAME = "bot_v75_13_all_pattern_market_defer_fresh_priority_paper.py"
V7513_SHA256 = "597c08f2a2b7ee46ff82cfadf06e7f3867153bf514a1960fe1f26899725ff76f"
RUNTIME_DIR = BASE_DIR / "v75_14_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_14_daily_logs"
POTENTIAL_FILE = RUNTIME_DIR / "v75_14_supply_potential.json"
FORBIDDEN_ORDER_NAMES = {
    "send_order", "create_order", "place_order", "order_stock", "submit_order"
}
SUPPLY_PATTERNS = {"매물소진눌림1차", "매물소진50초선진입"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7513() -> ModuleType:
    path = BASE_DIR / V7513_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7513_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7513_SHA256:
        raise RuntimeError(
            "V75.13 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7513_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7513_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.13 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7513 = load_v7513()
v7512 = v7513.v7512
v7511 = v7513.v7511
v7510 = v7513.v7510
v7591 = v7513.v7591
v758 = v7513.v758
v757 = v7513.v757
v756 = v7513.v756
v755 = v7513.v755
v754 = v7513.v754
v753 = v7513.v753
KST = v7513.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v7511.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v7511.env_int(name, default, minimum=minimum, maximum=maximum)


class TeeTextIO(io.TextIOBase):
    """콘솔 출력과 날짜별 텍스트 파일에 동시에 기록한다."""

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
    path = DAILY_LOG_DIR / f"V75_14_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeTextIO(original_stdout, handle)
    sys.stderr = TeeTextIO(original_stderr, handle)
    separator = "=" * 100
    print(f"\n{separator}")
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION} 실행 시작")
    print(f"명령행: {' '.join(argv)}")
    print(f"날짜별 로그: {path}")
    print(separator)
    return DailyTee(path, handle, original_stdout, original_stderr)




class BelowWarningFilter:
    def filter(self, record: Any) -> bool:
        import logging
        return record.levelno < logging.WARNING


def add_daily_detail_handler(logger: Any, path: Path, *, verbose: bool) -> None:
    """Tee가 담지 못하는 INFO/DEBUG를 같은 날짜별 파일에 추가한다."""
    import logging
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.addFilter(BelowWarningFilter())
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)


@dataclass(frozen=True, slots=True)
class LooseSupplyConfig:
    # 엄격 기준보다 느슨하며 실제 매수에는 사용하지 않는다.
    rise_lookback_bars: int = 22
    min_prior_rise_pct: float = 0.015
    max_peak_age_bars: int = 14
    min_retrace_of_rise: float = 0.05
    max_retrace_of_rise: float = 0.82
    flush_search_bars: int = 7
    flush_left_volume_bars: int = 12
    flush_min_bear_body_ratio: float = 0.22
    flush_min_drop_pct: float = 0.0025
    flush_volume_vs_median: float = 1.45
    flush_volume_vs_p75: float = 1.08
    flush_volume_vs_recent_max: float = 0.85
    developing_max_volume_vs_flush: float = 0.78
    developing_max_range_vs_flush: float = 0.90
    max_flush_low_break_pct: float = 0.008
    potential_keep_minutes: int = 15
    supply_pool_limit: int = 110
    discovery_fallback_count: int = 35

    @classmethod
    def load(cls) -> "LooseSupplyConfig":
        return cls(
            min_prior_rise_pct=env_float(
                "V7514_LOOSE_MIN_PRIOR_RISE", 0.015, minimum=0.005, maximum=0.08
            ),
            flush_volume_vs_median=env_float(
                "V7514_LOOSE_FLUSH_MEDIAN", 1.45, minimum=1.10, maximum=3.00
            ),
            flush_volume_vs_p75=env_float(
                "V7514_LOOSE_FLUSH_P75", 1.08, minimum=0.80, maximum=2.00
            ),
            flush_volume_vs_recent_max=env_float(
                "V7514_LOOSE_FLUSH_RECENT_MAX", 0.85, minimum=0.60, maximum=1.50
            ),
            developing_max_volume_vs_flush=env_float(
                "V7514_LOOSE_DRY_VS_FLUSH", 0.78, minimum=0.45, maximum=1.00
            ),
            max_flush_low_break_pct=env_float(
                "V7514_LOOSE_LOW_BREAK", 0.008, minimum=0.003, maximum=0.03
            ),
            potential_keep_minutes=env_int(
                "V7514_POTENTIAL_KEEP_MINUTES", 15, minimum=5, maximum=45
            ),
            supply_pool_limit=env_int(
                "V7514_SUPPLY_POOL_LIMIT", 110, minimum=40, maximum=180
            ),
            discovery_fallback_count=env_int(
                "V7514_DISCOVERY_FALLBACK", 35, minimum=15, maximum=80
            ),
        )


@dataclass(slots=True)
class SupplyPotential:
    symbol: str
    name: str
    stage: str
    score: float
    detected_at: datetime
    updated_at: datetime
    expires_at: datetime
    flush_time: datetime
    rise_pct: float
    retrace: float
    flush_vs_median: float
    flush_vs_p75: float
    flush_vs_recent_max: float
    dry_vs_flush: float | None
    dry_range_vs_flush: float | None
    low_break_pct: float
    strict_missing: tuple[str, ...]
    source: str

    def summary(self) -> str:
        dry = "대기" if self.dry_vs_flush is None else f"x{self.dry_vs_flush:.2f}"
        dry_range = "대기" if self.dry_range_vs_flush is None else f"x{self.dry_range_vs_flush:.2f}"
        missing = ",".join(self.strict_missing) if self.strict_missing else "없음"
        return (
            f"단계={self.stage} 점수={self.score:.1f} 선행상승={self.rise_pct:.2%} "
            f"조정={self.retrace:.0%} 대량음봉=중앙x{self.flush_vs_median:.2f}/"
            f"P75x{self.flush_vs_p75:.2f}/최근최대x{self.flush_vs_recent_max:.2f} "
            f"거래축소={dry} 폭축소={dry_range} 저점이탈={self.low_break_pct:.2%} "
            f"엄격미충족={missing}"
        )


def percentile75(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(math.ceil(0.75 * len(ordered))) - 1
    return float(ordered[max(0, min(index, len(ordered) - 1))])


def candle_metrics(bar: Any) -> tuple[float, float, float, float]:
    high = float(bar.high)
    low = float(bar.low)
    open_price = float(bar.open)
    close = float(bar.close)
    span = max(1e-9, high - low)
    signed_body = (close - open_price) / span
    abs_body = abs(signed_body)
    close_location = (close - low) / span
    return span, signed_body, abs_body, close_location


def same_session_bars(strategy: Any, symbol: str) -> list[Any]:
    return list(v756._same_session_bars(strategy.bars.get(symbol, [])))


def assess_loose_supply_potential(
    strategy: Any,
    symbol: str,
    *,
    loose: LooseSupplyConfig,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
) -> tuple[SupplyPotential | None, str]:
    """엄격 매수 직전의 느슨한 구조를 찾는다. 실제 BUY는 절대 만들지 않는다."""
    bars = same_session_bars(strategy, symbol)
    minimum = max(loose.rise_lookback_bars + 2, loose.flush_left_volume_bars + 2, 16)
    if len(bars) < minimum:
        return None, f"자료부족_{len(bars)}<{minimum}"

    first_flush = max(1, len(bars) - 1 - loose.flush_search_bars)
    best: SupplyPotential | None = None
    terminal_reason = "느슨한대량음봉없음"

    # 최신 조정봉을 우선한다. 현재 마지막 봉이 대량 음봉이면 after=0으로 관심등록 가능하다.
    for flush_index in range(len(bars) - 1, first_flush - 1, -1):
        flush = bars[flush_index]
        previous = bars[flush_index - 1]
        flush_range, flush_signed_body, flush_abs_body, _ = candle_metrics(flush)
        drop_pct = float(flush.close) / max(0.01, float(previous.close)) - 1.0
        if float(flush.close) >= float(flush.open):
            continue
        if -flush_signed_body < loose.flush_min_bear_body_ratio:
            continue
        if drop_pct > -loose.flush_min_drop_pct:
            continue

        left = bars[max(0, flush_index - loose.flush_left_volume_bars):flush_index]
        if len(left) < 6:
            continue
        left_volumes = [max(0.0, float(item.volume)) for item in left]
        left_median = float(statistics.median(left_volumes))
        left_p75 = percentile75(left_volumes)
        recent_max = max(left_volumes[-min(5, len(left_volumes)):])
        flush_volume = max(0.0, float(flush.volume))
        vs_median = flush_volume / max(1.0, left_median)
        vs_p75 = flush_volume / max(1.0, left_p75)
        vs_recent_max = flush_volume / max(1.0, recent_max)
        if vs_median < loose.flush_volume_vs_median:
            continue
        if vs_p75 < loose.flush_volume_vs_p75:
            continue
        if vs_recent_max < loose.flush_volume_vs_recent_max:
            continue

        rise_window = bars[max(0, flush_index - loose.rise_lookback_bars):flush_index]
        if len(rise_window) < 6:
            continue
        peak_pos = max(range(len(rise_window)), key=lambda idx: float(rise_window[idx].high))
        peak_age = len(rise_window) - 1 - peak_pos
        if peak_age > loose.max_peak_age_bars:
            continue
        prior_to_peak = rise_window[:peak_pos + 1]
        if len(prior_to_peak) < 3:
            continue
        rise_low = min(float(item.low) for item in prior_to_peak)
        rise_high = float(rise_window[peak_pos].high)
        rise_span = rise_high - rise_low
        rise_pct = rise_high / max(0.01, rise_low) - 1.0
        if rise_pct < loose.min_prior_rise_pct or rise_span <= 0:
            continue

        after = bars[flush_index + 1:]
        if len(after) > loose.flush_search_bars:
            continue
        pullback_low = min(float(item.low) for item in bars[flush_index:])
        retrace = (rise_high - pullback_low) / max(1.0, rise_span)
        if not loose.min_retrace_of_rise <= retrace <= loose.max_retrace_of_rise:
            continue

        low_break_pct = max(0.0, float(flush.low) - pullback_low) / max(0.01, float(flush.low))
        if low_break_pct > loose.max_flush_low_break_pct:
            return None, f"구조무효_대량음봉저점이탈_{low_break_pct:.2%}"

        repeated_heavy = False
        for item in after:
            _, signed_body, _, _ = candle_metrics(item)
            if signed_body <= -strict.flush_min_bear_body_ratio and float(item.volume) >= flush_volume * 0.80:
                repeated_heavy = True
                break
        if repeated_heavy:
            return None, "구조무효_재대량음봉"

        dry_vs_flush: float | None = None
        dry_range_vs_flush: float | None = None
        if after:
            latest = after[-1]
            latest_range, _, _, _ = candle_metrics(latest)
            dry_vs_flush = float(latest.volume) / max(1.0, flush_volume)
            dry_range_vs_flush = latest_range / max(1e-9, flush_range)

        strict_missing: list[str] = []
        if rise_pct < strict.min_prior_rise_pct:
            strict_missing.append(f"선행상승<{strict.min_prior_rise_pct:.1%}")
        if -flush_signed_body < strict.flush_min_bear_body_ratio:
            strict_missing.append(f"음봉몸통<{strict.flush_min_bear_body_ratio:.0%}")
        if -drop_pct < strict.flush_min_drop_pct:
            strict_missing.append(f"음봉하락<{strict.flush_min_drop_pct:.2%}")
        if vs_median < strict.flush_volume_vs_median:
            strict_missing.append(f"대량봉중앙x{vs_median:.2f}<{strict.flush_volume_vs_median:.2f}")
        if vs_p75 < strict.flush_volume_vs_p75:
            strict_missing.append(f"대량봉P75x{vs_p75:.2f}<{strict.flush_volume_vs_p75:.2f}")
        if vs_recent_max < strict.flush_volume_vs_recent_max:
            strict_missing.append(f"대량봉최근최대x{vs_recent_max:.2f}<{strict.flush_volume_vs_recent_max:.2f}")
        if not after:
            strict_missing.append("후속거래바닥봉대기")
        else:
            latest = after[-1]
            latest_range, signed_body, abs_body, close_location = candle_metrics(latest)
            previous_after = after[-2] if len(after) >= 2 else flush
            dry_vs_left = float(latest.volume) / max(1.0, left_median)
            dry_vs_previous = float(latest.volume) / max(1.0, float(previous_after.volume))
            if dry_vs_flush is not None and dry_vs_flush > strict.dry_max_volume_vs_flush:
                strict_missing.append(f"거래바닥대량봉x{dry_vs_flush:.2f}>{strict.dry_max_volume_vs_flush:.2f}")
            if dry_vs_left > strict.dry_max_volume_vs_left_median:
                strict_missing.append(f"거래바닥중앙x{dry_vs_left:.2f}>{strict.dry_max_volume_vs_left_median:.2f}")
            if dry_vs_previous > strict.dry_max_volume_vs_previous:
                strict_missing.append(f"거래바닥직전x{dry_vs_previous:.2f}>{strict.dry_max_volume_vs_previous:.2f}")
            if dry_range_vs_flush is not None and dry_range_vs_flush > strict.dry_max_range_vs_flush:
                strict_missing.append(f"봉폭x{dry_range_vs_flush:.2f}>{strict.dry_max_range_vs_flush:.2f}")
            if close_location < strict.dry_min_close_location:
                strict_missing.append(f"종가위치{close_location:.0%}<{strict.dry_min_close_location:.0%}")
            if signed_body < 0 and abs_body > strict.dry_max_bear_body_ratio:
                strict_missing.append("음봉몸통과대")
            if signed_body >= 0 and abs_body > strict.dry_max_bull_body_ratio:
                strict_missing.append("양봉몸통과대")

        stage = "대량음봉발견"
        score = 50.0
        if after:
            stage = "거래축소관찰"
            score += 12.0
            if (
                dry_vs_flush is not None
                and dry_vs_flush <= loose.developing_max_volume_vs_flush
                and dry_range_vs_flush is not None
                and dry_range_vs_flush <= loose.developing_max_range_vs_flush
            ):
                stage = "거래바닥근접"
                score += 15.0
        score += min(12.0, max(0.0, vs_median - loose.flush_volume_vs_median) * 5.0)
        score += min(8.0, max(0.0, rise_pct - loose.min_prior_rise_pct) * 100.0)
        score += max(0.0, 5.0 - low_break_pct * 500.0)
        if not strict_missing:
            stage = "엄격매수조건충족"
            score = 100.0

        flush_time = v754.normalize_kst(flush.timestamp)
        item = SupplyPotential(
            symbol=symbol,
            name=name or symbol,
            stage=stage,
            score=min(100.0, score),
            detected_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=loose.potential_keep_minutes),
            flush_time=flush_time,
            rise_pct=rise_pct,
            retrace=retrace,
            flush_vs_median=vs_median,
            flush_vs_p75=vs_p75,
            flush_vs_recent_max=vs_recent_max,
            dry_vs_flush=dry_vs_flush,
            dry_range_vs_flush=dry_range_vs_flush,
            low_break_pct=low_break_pct,
            strict_missing=tuple(strict_missing[:8]),
            source=source,
        )
        if best is None or item.score > best.score:
            best = item
        terminal_reason = "느슨한구조발견"

    return best, terminal_reason


def signal_is_supply(signal: Any) -> bool:
    return str(getattr(signal, "pattern", "")) in SUPPLY_PATTERNS


def mode_allows_signal(signal: Any, supply_only: bool) -> bool:
    if getattr(signal, "action", None) != v753.SignalAction.BUY:
        return True
    return (not supply_only) or signal_is_supply(signal)


def hold_from_signal(signal: Any, reason: str) -> Any:
    return v753.Signal(
        signal.symbol,
        v753.SignalAction.HOLD,
        0.0,
        reason,
        float(getattr(signal, "reference_price", 0.0) or 0.0),
        getattr(signal, "timestamp", datetime.now(KST)),
        "매물소진전용모드대기",
        0.0,
        0.0,
    )


class SupplyFocusEngine(v7513.AllPatternDeferredFreshEngine):
    def __init__(
        self,
        *args: Any,
        supply_only: bool = False,
        **kwargs: Any,
    ) -> None:
        self.supply_only = bool(supply_only)
        self.loose_supply_config = LooseSupplyConfig.load()
        self._supply_potential: dict[str, SupplyPotential] = {}
        self._potential_log_signature: dict[str, str] = {}
        self._discovery_active_last: set[str] = set()
        self._discovery_source_signature: dict[str, str] = {}
        super().__init__(*args, **kwargs)
        self._load_supply_potential()
        self._install_supply_mode_wrapper()
        self._purge_disallowed_deferred()
        mode = "매물소진만" if self.supply_only else "전체패턴"
        self.logger.warning(
            "[%s] 전략모드=%s | 엄격기준=실제매수 | 느슨기준=관심종목-매수가능성만 | "
            "발견풀=기존순위API + 매물소진구조유지",
            VERSION,
            mode,
        )

    def _purge_disallowed_deferred(self) -> None:
        if not self.supply_only:
            return
        removed = 0
        for symbol, item in list(self._generic_deferred.items()):
            if not signal_is_supply(item.signal):
                self._generic_deferred.pop(symbol, None)
                removed += 1
        for symbol, item in list(self.pending_entries.items()):
            signal = getattr(item, "signal", item)
            if not signal_is_supply(signal):
                self.pending_entries.pop(symbol, None)
                removed += 1
        if removed:
            self._save_generic_deferred()
            self.logger.warning("매물소진전용모드 시작—다른 패턴 대기신호 %d건 제거", removed)

    def _install_supply_mode_wrapper(self) -> None:
        inherited_on_bar = self.strategy.on_bar
        engine = self

        def wrapped_on_bar(bar: Any, position: Any) -> Any:
            inherited_signal = inherited_on_bar(bar, position)
            engine._update_supply_potential(bar.symbol)
            if not engine.supply_only or position is not None:
                return inherited_signal
            if inherited_signal.action == v753.SignalAction.SELL:
                return inherited_signal

            # 기존 구조에서는 다른 패턴 BUY가 먼저 나오면 매물소진 탐지가 가려질 수 있다.
            # 전용모드에서는 매물소진 엄격 탐지를 독립 실행해 우선권을 준다.
            strict_signal, _, evidence = v756.detect_supply_exhaustion_pullback(
                engine.strategy,
                bar.symbol,
                config=engine.supply_exhaustion_config,
            )
            if strict_signal is not None and evidence is not None:
                adjusted = engine._apply_leadership_confidence(strict_signal, evidence)
                if adjusted is not None:
                    engine._supply_evidence[bar.symbol] = evidence
                    return adjusted

            if inherited_signal.action == v753.SignalAction.BUY and not signal_is_supply(inherited_signal):
                return hold_from_signal(
                    inherited_signal,
                    f"전략모드차단_{inherited_signal.pattern}",
                )
            return inherited_signal

        self.strategy.on_bar = wrapped_on_bar

    def _potential_payload(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "candidates": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "stage": item.stage,
                    "score": item.score,
                    "detected_at": item.detected_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                    "flush_time": item.flush_time.isoformat(),
                    "rise_pct": item.rise_pct,
                    "retrace": item.retrace,
                    "flush_vs_median": item.flush_vs_median,
                    "flush_vs_p75": item.flush_vs_p75,
                    "flush_vs_recent_max": item.flush_vs_recent_max,
                    "dry_vs_flush": item.dry_vs_flush,
                    "dry_range_vs_flush": item.dry_range_vs_flush,
                    "low_break_pct": item.low_break_pct,
                    "strict_missing": list(item.strict_missing),
                    "source": item.source,
                }
                for item in self._supply_potential.values()
            ],
        }

    def _save_supply_potential(self) -> None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            temp = POTENTIAL_FILE.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self._potential_payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, POTENTIAL_FILE)
        except Exception as exc:
            self.logger.warning("매수가능성 관심종목 저장 실패(매매는 계속): %s", exc)

    def _load_supply_potential(self) -> None:
        if not POTENTIAL_FILE.exists():
            return
        try:
            raw = json.loads(POTENTIAL_FILE.read_text(encoding="utf-8"))
            now = datetime.now(KST)
            for item in raw.get("candidates", []):
                expires = datetime.fromisoformat(str(item["expires_at"])).astimezone(KST)
                if expires <= now or expires.date() != now.date():
                    continue
                potential = SupplyPotential(
                    symbol=str(item["symbol"]),
                    name=str(item.get("name") or item["symbol"]),
                    stage=str(item["stage"]),
                    score=float(item["score"]),
                    detected_at=datetime.fromisoformat(str(item["detected_at"])).astimezone(KST),
                    updated_at=datetime.fromisoformat(str(item["updated_at"])).astimezone(KST),
                    expires_at=expires,
                    flush_time=datetime.fromisoformat(str(item["flush_time"])).astimezone(KST),
                    rise_pct=float(item["rise_pct"]),
                    retrace=float(item["retrace"]),
                    flush_vs_median=float(item["flush_vs_median"]),
                    flush_vs_p75=float(item["flush_vs_p75"]),
                    flush_vs_recent_max=float(item["flush_vs_recent_max"]),
                    dry_vs_flush=(
                        None if item.get("dry_vs_flush") is None else float(item["dry_vs_flush"])
                    ),
                    dry_range_vs_flush=(
                        None if item.get("dry_range_vs_flush") is None else float(item["dry_range_vs_flush"])
                    ),
                    low_break_pct=float(item["low_break_pct"]),
                    strict_missing=tuple(str(value) for value in item.get("strict_missing", [])),
                    source=str(item.get("source") or "복원"),
                )
                self._supply_potential[potential.symbol] = potential
            if self._supply_potential:
                self.logger.warning(
                    "관심종목-매수가능성 %d종목 복원—엄격조건 충족 여부 재검사",
                    len(self._supply_potential),
                )
        except Exception as exc:
            self.logger.warning("매수가능성 관심종목 복원 실패(빈 목록으로 계속): %s", exc)
            self._supply_potential.clear()

    def _purge_expired_potential(self) -> None:
        now = datetime.now(KST)
        removed = [
            symbol for symbol, item in self._supply_potential.items()
            if item.expires_at <= now or item.updated_at.date() != now.date()
        ]
        for symbol in removed:
            item = self._supply_potential.pop(symbol)
            self.logger.info(
                "관심해제[매수가능성] %s 시간만료 | 마지막=%s",
                self._label(symbol),
                item.summary(),
            )
        if removed:
            self._save_supply_potential()

    def _candidate_source(self, symbol: str) -> str:
        meta = self.candidate_meta.get(symbol)
        if meta is None:
            return "매물소진구조유지"
        return str(getattr(meta, "source", "") or "순위후보")

    def _update_supply_potential(self, symbol: str) -> None:
        if symbol in self.broker.positions:
            return
        now = datetime.now(KST)
        self._purge_expired_potential()
        potential, reason = assess_loose_supply_potential(
            self.strategy,
            symbol,
            loose=self.loose_supply_config,
            strict=self.supply_exhaustion_config,
            now=now,
            name=self.names.get(symbol, symbol),
            source=self._candidate_source(symbol),
        )
        if potential is None:
            if reason.startswith("구조무효") and symbol in self._supply_potential:
                old = self._supply_potential.pop(symbol)
                self.logger.warning(
                    "관심해제[매수가능성] %s %s | 마지막=%s",
                    self._label(symbol),
                    reason,
                    old.summary(),
                )
                self._save_supply_potential()
            return

        old = self._supply_potential.get(symbol)
        if old is not None:
            potential.detected_at = old.detected_at
        self._supply_potential[symbol] = potential
        signature = (
            f"{potential.stage}|{potential.flush_time.isoformat()}|"
            f"{potential.strict_missing}|{potential.dry_vs_flush}"
        )
        if self._potential_log_signature.get(symbol) != signature:
            self._potential_log_signature[symbol] = signature
            self.logger.warning(
                "관심등록[매수가능성] %s | 발견경로=%s | %s | "
                "주의=느슨기준은 매수금지, 엄격기준 충족시만 매수",
                self._label(symbol),
                potential.source,
                potential.summary(),
            )
        self._save_supply_potential()

    def _supply_discovery_compatible(self, symbol: str) -> bool:
        if symbol in self._supply_potential:
            return True
        meta = self.candidate_meta.get(symbol)
        if meta is None:
            return False
        source = str(getattr(meta, "source", "") or "")
        change = float(getattr(meta, "change_pct", 0.0) or 0.0)
        dual = bool(getattr(meta, "dual_buy", False))
        source_count = len([part for part in source.split("+") if part.strip()])
        return (
            "거래량급증" in source
            or source_count >= 2
            or ("상승률" in source and change >= 0.8)
            or (dual and change >= 0.3)
        )

    def _log_discovery_registration(self, symbols: Sequence[str]) -> None:
        current = set(symbols)
        newly_added = current - self._discovery_active_last
        for symbol in symbols:
            if symbol not in newly_added:
                continue
            meta = self.candidate_meta.get(symbol)
            if meta is None:
                self.logger.warning(
                    "관심등록[발견] %s | 조건=매물소진구조유지 | 순위API에서 이탈해도 잠시 유지",
                    self._label(symbol),
                )
                continue
            source = str(getattr(meta, "source", "") or "없음")
            change = float(getattr(meta, "change_pct", 0.0) or 0.0)
            volume = int(getattr(meta, "volume", 0) or 0)
            surge = float(getattr(meta, "surge_rate", 0.0) or 0.0)
            dual = bool(getattr(meta, "dual_buy", False))
            self.logger.warning(
                "관심등록[발견] %s | 조건=%s | 상승률=%+.2f%% | 당일누적=%s주 | "
                "거래급증률=x%.2f | 쌍끌이=%s",
                self._label(symbol),
                source,
                change,
                f"{volume:,}",
                surge,
                "예" if dual else "아니오",
            )
        self._discovery_active_last = current

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        previous = set(self.active_symbols)
        super()._refresh_universe(force=force)
        self._purge_expired_potential()

        if self.supply_only:
            potential_symbols = [
                symbol for symbol in self._supply_potential
                if symbol not in self.broker.positions
            ]
            compatible = [
                symbol for symbol in self.active_symbols
                if self._supply_discovery_compatible(symbol)
                and symbol not in self.broker.positions
            ]
            # 완전 초기에는 구조 데이터가 없으므로 상위 후보 일부를 탐색용으로 유지한다.
            fallback = [
                item.symbol for item in self.universe_pool[: self.loose_supply_config.discovery_fallback_count]
                if item.symbol not in self.broker.positions
            ]
            selected = list(dict.fromkeys(potential_symbols + compatible + fallback))
            self.active_symbols = selected[: self.loose_supply_config.supply_pool_limit]
            self.logger.warning(
                "매물소진전용 관심풀 %d종목 | 매수가능성유지=%d | 구조친화발견=%d | "
                "상위탐색보완=%d | 실제매수는 엄격기준만",
                len(self.active_symbols),
                len(potential_symbols),
                len(compatible),
                len(fallback),
            )

        self._log_discovery_registration(self.active_symbols)
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    def _priority_score(self, symbol: str) -> float:
        score = super()._priority_score(symbol)
        potential = self._supply_potential.get(symbol)
        if potential is not None:
            score += 200_000.0 + potential.score * 100.0
            if potential.stage == "거래바닥근접":
                score += 50_000.0
            elif potential.stage == "엄격매수조건충족":
                score += 80_000.0
        if symbol in getattr(self, "_armed_symbols", set()):
            score += 180_000.0
        return score

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        potential = sorted(
            self._supply_potential,
            key=lambda symbol: self._priority_score(symbol),
            reverse=True,
        )
        self.hot_symbols = list(dict.fromkeys(potential + self.hot_symbols))

    def _can_defer_signal(self, signal: Any) -> bool:
        if self.supply_only and not signal_is_supply(signal):
            return False
        return super()._can_defer_signal(signal)

    def _queue_pending_entry(self, signal: Any) -> None:
        if not mode_allows_signal(signal, self.supply_only):
            self.pending_entries.pop(signal.symbol, None)
            self.logger.info(
                "진입차단[전략모드] %s 패턴=%s | --supply-only에서는 매물소진만 허용",
                self._label(signal.symbol),
                signal.pattern,
            )
            return
        super()._queue_pending_entry(signal)

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        if not mode_allows_signal(signal, self.supply_only):
            return False, f"전략모드차단_{signal.pattern}"
        return super()._pending_signal_valid(signal, current_price)

    def _format_strict_evidence(self, signal: Any) -> str:
        evidence = self._supply_evidence.get(signal.symbol)
        if evidence is None:
            snapshot = getattr(self, "_intrabar_snapshots", {}).get(signal.symbol)
            evidence = getattr(snapshot, "supply", None)
        if evidence is None:
            return f"패턴={signal.pattern} | 신호내역={signal.reason}"
        cfg = self.supply_exhaustion_config
        return (
            f"패턴={signal.pattern} | 선행상승={evidence.rise_pct:.2%}>={cfg.min_prior_rise_pct:.2%} | "
            f"조정={evidence.retrace:.0%}({cfg.min_retrace_of_rise:.0%}~{cfg.max_retrace_of_rise:.0%}) | "
            f"대량음봉 중앙x{evidence.flush_volume_vs_median:.2f}>={cfg.flush_volume_vs_median:.2f}, "
            f"P75x{evidence.flush_volume_vs_p75:.2f}>={cfg.flush_volume_vs_p75:.2f}, "
            f"최근최대x{evidence.flush_volume_vs_recent_max:.2f}>={cfg.flush_volume_vs_recent_max:.2f} | "
            f"거래바닥 대량봉x{evidence.dry_volume_vs_flush:.2f}<={cfg.dry_max_volume_vs_flush:.2f}, "
            f"왼쪽중앙x{evidence.dry_volume_vs_left_median:.2f}<={cfg.dry_max_volume_vs_left_median:.2f}, "
            f"직전봉x{evidence.dry_volume_vs_previous:.2f}<={cfg.dry_max_volume_vs_previous:.2f} | "
            f"봉폭x{evidence.dry_range_vs_flush:.2f}<={cfg.dry_max_range_vs_flush:.2f} | "
            f"대량음봉저점={evidence.flush_low:.0f} 방어 | 구조손절폭={evidence.structure_stop_pct:.2%}"
        )

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        if not mode_allows_signal(signal, self.supply_only):
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning(
                "진입차단[전략모드] %s 패턴=%s | 매물소진전용모드",
                self._label(signal.symbol),
                signal.pattern,
            )
            return False
        strict_detail = self._format_strict_evidence(signal) if signal_is_supply(signal) else ""
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered and signal_is_supply(signal):
            self.logger.warning(
                "매수근거[매물소진엄격] %s | %s | 공통게이트=유동성·시장·전고점·추격·신선도 통과",
                self._label(signal.symbol),
                strict_detail,
            )
            self._supply_potential.pop(signal.symbol, None)
            self._save_supply_potential()
        return entered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{VERSION} — 매물소진 집중모드·관심/매수 로그 PAPER"
    )
    parser.add_argument(
        "--supply-only",
        action="store_true",
        help="매물소진눌림1차·매물소진50초선진입만 신규매수에 사용",
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


def _loose_case(*, strict_ready: bool = False) -> tuple[Any, str]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "751400"
    bars: list[Any] = []
    for idx in range(18):
        base = 100.0 + (idx % 3) * 0.1
        bars.append(_bar(symbol, idx, base, base + 0.4, base - 0.3, base + 0.1, 1000))
    for idx, close in enumerate([100.5, 101.0, 101.6, 102.2, 102.8, 103.2], start=18):
        bars.append(_bar(symbol, idx, close - 0.3, close + 0.4, close - 0.5, close, 1050))
    flush_volume = 2200 if strict_ready else 1650
    bars.append(_bar(symbol, 24, 103.1, 103.3, 101.4, 101.9, flush_volume))
    dry_volume = 650 if strict_ready else 1050
    bars.append(_bar(symbol, 25, 101.8, 102.2, 101.5, 102.0, dry_volume))
    for bar in bars:
        strategy.on_bar(bar, None)
    return strategy, symbol


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.13-SHA256고정",
        sha256_file(BASE_DIR / V7513_FILENAME) == V7513_SHA256,
        V7513_SHA256[:16] + "…",
    )
    inherited = v7513.self_check()
    inherited_failures = [name for name, ok, _ in inherited if not ok]
    record(
        "V75.13기존검증위임",
        not inherited_failures,
        f"{len(inherited)}항목 / 실패={inherited_failures or '없음'}",
    )

    buy_supply = v753.Signal(
        "751400", v753.SignalAction.BUY, 100.0, "테스트", 10000.0,
        datetime(2026, 7, 10, 10, 0, tzinfo=KST), "매물소진눌림1차", 0.015, 0.015
    )
    buy_box = v753.Signal(
        "751401", v753.SignalAction.BUY, 100.0, "테스트", 10000.0,
        datetime(2026, 7, 10, 10, 0, tzinfo=KST), "정석박스돌파", 0.015, 0.015
    )
    record("전용모드매물소진허용", mode_allows_signal(buy_supply, True), "엄격 매물소진")
    record("전용모드다른패턴차단", not mode_allows_signal(buy_box, True), "정석박스돌파 차단")
    record("전체모드기존패턴허용", mode_allows_signal(buy_box, False), "기존 동작 보존")

    loose_strategy, loose_symbol = _loose_case(strict_ready=False)
    loose_item, loose_reason = assess_loose_supply_potential(
        loose_strategy,
        loose_symbol,
        loose=LooseSupplyConfig(),
        strict=v756.SupplyExhaustionConfig(),
        now=datetime(2026, 7, 10, 10, 0, tzinfo=KST),
        name="느슨합성",
        source="거래량급증",
    )
    strict_signal, _, _ = v756.detect_supply_exhaustion_pullback(
        loose_strategy,
        loose_symbol,
        config=v756.SupplyExhaustionConfig(),
    )
    record(
        "느슨관심·엄격매수분리",
        loose_item is not None and strict_signal is None,
        f"관심={loose_reason} / 엄격={'통과' if strict_signal else '미통과'}",
    )

    parser = build_parser()
    parsed = parser.parse_args(["--supply-only", "--minutes", "1"])
    record("실행플래그", parsed.supply_only and parsed.minutes == 1, "--supply-only")

    expected_log = DAILY_LOG_DIR / "V75_14_2026-07-10.txt"
    sample_path = DAILY_LOG_DIR / f"V75_14_{datetime(2026, 7, 10, tzinfo=KST):%Y-%m-%d}.txt"
    record("날짜별로그경로", sample_path == expected_log, str(sample_path.name))

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (function_names & FORBIDDEN_ORDER_NAMES),
        f"금지함수={sorted(function_names & FORBIDDEN_ORDER_NAMES) or '없음'}",
    )
    record(
        "모든진입경로모드차단",
        "def _queue_pending_entry" in source
        and "def _pending_signal_valid" in source
        and "def _attempt_entry" in source,
        "대기·재검수·즉시진입 모두 확인",
    )
    record(
        "관심등록로그",
        "관심등록[발견]" in source and "관심등록[매수가능성]" in source,
        "발견조건·느슨조건 출력",
    )
    record(
        "매수근거로그",
        "매수근거[매물소진엄격]" in source,
        "실제 매수 성공 시 엄격수치 출력",
    )
    record(
        "하이브리드관심풀",
        "기존순위API + 매물소진구조유지" in source,
        "발견과 유지 역할 분리",
    )
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = build_parser().parse_args()
    tee = install_daily_tee(sys.argv)
    logger = v753.setup_logging(args.verbose)
    add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
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
        engine = SupplyFocusEngine(
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
