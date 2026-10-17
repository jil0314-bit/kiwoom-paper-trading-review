#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.5 RESISTANCE-EXPLOSION-GUARD PAPER
======================================

V75.3 원본과 V75.4 신호신선도 안전패치를 수정하지 않고, 그 위에
'전고점/저항 부근 매수 금지 + 거래폭발 돌파만 허용' 계층을 추가한다.

핵심 원칙
- 전고점 바로 아래 또는 애매하게 걸친 가격에서는 신규매수 금지.
- 전고점을 종가로 명확히 넘고, 신호봉 거래량이 왼쪽 최대 거래량과
  중앙값을 모두 크게 초과하며, 몸통과 종가 위치가 강할 때만 허용.
- 돌파 후 현재가가 다시 전고점 아래로 밀리면 진입 직전 재검증에서 차단.
- 실제 주문 기능 없음. PAPER 전용.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import logging
import math
import os
import re
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

VERSION = "V75.5-RESISTANCE-EXPLOSION-GUARD-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V754_FILENAME = "bot_v75_4_fresh_signal_guard_paper.py"
V754_SHA256 = "614f6e486ad51ffa81e84f63665eacea431a82e3e004798abf90d5caebfcc03b"

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


def load_v754() -> ModuleType:
    path = BASE_DIR / V754_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V754_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V754_SHA256:
        raise RuntimeError(
            "V75.4 SHA256 불일치 — 검증된 안전패치가 아니므로 중단합니다.\n"
            f"기대: {V754_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v754_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.4 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v754 = load_v754()
v753 = v754.v753
KST = v754.KST


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
class ResistanceGuardConfig:
    near_below_pct: float = 0.012
    near_above_pct: float = 0.015
    breakout_clear_pct: float = 0.005
    hold_above_pct: float = 0.001
    volume_vs_left_max: float = 1.50
    volume_vs_left_median: float = 4.00
    min_body_ratio: float = 0.55
    min_close_location: float = 0.80
    max_upper_wick_ratio: float = 0.20
    left_window_bars: int = 30
    pivot_radius: int = 2
    blocked_cooldown_sec: float = 180.0

    @classmethod
    def load(cls) -> "ResistanceGuardConfig":
        return cls(
            near_below_pct=env_float("V755_RESISTANCE_NEAR_BELOW_PCT", 0.012, minimum=0.002, maximum=0.05),
            near_above_pct=env_float("V755_RESISTANCE_NEAR_ABOVE_PCT", 0.015, minimum=0.002, maximum=0.05),
            breakout_clear_pct=env_float("V755_RESISTANCE_BREAK_CLEAR_PCT", 0.005, minimum=0.001, maximum=0.03),
            hold_above_pct=env_float("V755_RESISTANCE_HOLD_ABOVE_PCT", 0.001, minimum=0.0, maximum=0.02),
            volume_vs_left_max=env_float("V755_RESISTANCE_VOL_VS_MAX", 1.50, minimum=1.10, maximum=5.0),
            volume_vs_left_median=env_float("V755_RESISTANCE_VOL_VS_MEDIAN", 4.00, minimum=1.50, maximum=15.0),
            min_body_ratio=env_float("V755_BREAKOUT_MIN_BODY_RATIO", 0.55, minimum=0.20, maximum=0.95),
            min_close_location=env_float("V755_BREAKOUT_MIN_CLOSE_LOCATION", 0.80, minimum=0.50, maximum=1.0),
            max_upper_wick_ratio=env_float("V755_BREAKOUT_MAX_UPPER_WICK", 0.20, minimum=0.0, maximum=0.50),
            left_window_bars=env_int("V755_RESISTANCE_LEFT_WINDOW", 30, minimum=10, maximum=120),
            pivot_radius=env_int("V755_RESISTANCE_PIVOT_RADIUS", 2, minimum=1, maximum=5),
            blocked_cooldown_sec=env_float("V755_RESISTANCE_BLOCK_COOLDOWN_SEC", 180.0, minimum=30.0, maximum=1800.0),
        )


@dataclass(frozen=True, slots=True)
class ResistanceEvidence:
    resistance: float
    source: str
    signal_close: float
    vol_vs_max: float
    vol_vs_median: float
    body_ratio: float
    close_location: float
    upper_wick_ratio: float


_BOX_HIGH_RE = re.compile(r"상단=([0-9]+(?:\.[0-9]+)?)")


def _normalize_kst(value: datetime) -> datetime:
    return v754.normalize_kst(value)


def _find_signal_bar(bars: list[Any], signal: Any) -> tuple[int, Any] | None:
    target = _normalize_kst(signal.timestamp)
    exact: tuple[int, Any] | None = None
    for idx, bar in enumerate(bars):
        bar_time = _normalize_kst(bar.timestamp)
        if bar_time == target:
            return idx, bar
        if bar_time <= target and (target - bar_time).total_seconds() <= 60:
            exact = (idx, bar)
    return exact


def _same_segment_left_bars(bars: list[Any], signal_index: int, signal_bar: Any, limit: int) -> list[Any]:
    signal_time = _normalize_kst(signal_bar.timestamp)
    signal_date = signal_time.date()
    try:
        segment = v753.session_segment(signal_time)
    except Exception:
        segment = ""
    left: list[Any] = []
    for bar in bars[:signal_index]:
        bar_time = _normalize_kst(bar.timestamp)
        if bar_time.date() != signal_date:
            continue
        if segment:
            try:
                if v753.session_segment(bar_time) != segment:
                    continue
            except Exception:
                continue
        left.append(bar)
    return left[-limit:]


def _pivot_highs(bars: list[Any], radius: int) -> list[float]:
    if len(bars) < radius * 2 + 1:
        return []
    values: list[float] = []
    for idx in range(radius, len(bars) - radius):
        item = bars[idx]
        window = bars[idx - radius: idx + radius + 1]
        high = float(item.high)
        if high >= max(float(x.high) for x in window):
            values.append(high)
    return values


def _parse_box_high(signal: Any) -> float | None:
    if getattr(signal, "pattern", "") != "정석박스돌파":
        return None
    match = _BOX_HIGH_RE.search(str(getattr(signal, "reason", "")))
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def assess_resistance_breakout(
    strategy: Any,
    signal: Any,
    *,
    config: ResistanceGuardConfig,
    current_price: float | None = None,
) -> tuple[bool, str, ResistanceEvidence | None]:
    """전고점/저항 부근이면 강한 거래폭발 돌파만 허용한다."""
    if signal.action != v753.SignalAction.BUY:
        return True, "매수신호아님", None

    bars = list(strategy.bars.get(signal.symbol, []))
    located = _find_signal_bar(bars, signal)
    if located is None:
        if signal.pattern == "정석박스돌파":
            return False, "전고점검증분봉없음", None
        return True, "전고점자료없음_비돌파패턴", None

    signal_index, signal_bar = located
    left = _same_segment_left_bars(bars, signal_index, signal_bar, config.left_window_bars)
    if len(left) < 10:
        if signal.pattern == "정석박스돌파":
            return False, f"전고점왼쪽분봉부족_{len(left)}개", None
        return True, "왼쪽분봉부족_비돌파패턴", None

    signal_close = float(signal_bar.close)
    if signal_close <= 0:
        return False, "신호봉가격오류", None

    candidates: list[tuple[float, str]] = []
    box_high = _parse_box_high(signal)
    if box_high is not None:
        candidates.append((box_high, "박스상단"))

    daily = strategy.daily_context.get(signal.symbol)
    if daily is not None and getattr(daily, "resistance", None):
        daily_resistance = float(daily.resistance)
        if daily_resistance > 0:
            candidates.append((daily_resistance, "일봉전고점"))

    for high in _pivot_highs(left, config.pivot_radius):
        candidates.append((high, "분봉전고점"))

    reference = signal_close
    relevant = [
        (price, source)
        for price, source in candidates
        if reference * (1.0 - config.near_below_pct)
        <= price
        <= reference * (1.0 + config.near_above_pct)
    ]
    if not relevant:
        return True, "근접전고점없음", None

    # 가까운 저항이 여러 개면 가장 높은 저항까지 모두 돌파해야 한다.
    resistance, source = max(relevant, key=lambda item: item[0])

    left_volumes = [max(0.0, float(bar.volume)) for bar in left]
    max_volume = max(left_volumes) if left_volumes else 0.0
    median_volume = statistics.median(left_volumes) if left_volumes else 0.0
    signal_volume = max(0.0, float(signal_bar.volume))
    vol_vs_max = signal_volume / max(1.0, max_volume)
    vol_vs_median = signal_volume / max(1.0, median_volume)

    candle_range = max(1.0, float(signal_bar.high) - float(signal_bar.low))
    body_ratio = (float(signal_bar.close) - float(signal_bar.open)) / candle_range
    close_location = (float(signal_bar.close) - float(signal_bar.low)) / candle_range
    upper_wick_ratio = (float(signal_bar.high) - float(signal_bar.close)) / candle_range

    evidence = ResistanceEvidence(
        resistance=resistance,
        source=source,
        signal_close=signal_close,
        vol_vs_max=vol_vs_max,
        vol_vs_median=vol_vs_median,
        body_ratio=body_ratio,
        close_location=close_location,
        upper_wick_ratio=upper_wick_ratio,
    )

    required_close = resistance * (1.0 + config.breakout_clear_pct)
    if signal_close < required_close:
        return False, (
            f"전고점돌파미확인_{source}={resistance:.0f}_"
            f"종가={signal_close:.0f}<{required_close:.0f}"
        ), evidence

    if vol_vs_max < config.volume_vs_left_max or vol_vs_median < config.volume_vs_left_median:
        return False, (
            f"전고점거래폭발부족_왼쪽최대x{vol_vs_max:.2f}<{config.volume_vs_left_max:.2f}_"
            f"중앙x{vol_vs_median:.2f}<{config.volume_vs_left_median:.2f}"
        ), evidence

    if body_ratio < config.min_body_ratio:
        return False, f"돌파봉몸통부족_{body_ratio:.2f}<{config.min_body_ratio:.2f}", evidence
    if close_location < config.min_close_location:
        return False, f"돌파봉종가위치약함_{close_location:.2f}<{config.min_close_location:.2f}", evidence
    if upper_wick_ratio > config.max_upper_wick_ratio:
        return False, f"돌파봉윗꼬리과다_{upper_wick_ratio:.2f}>{config.max_upper_wick_ratio:.2f}", evidence

    if current_price is not None:
        hold_level = resistance * (1.0 + config.hold_above_pct)
        if current_price < hold_level:
            return False, (
                f"돌파후전고점재이탈_현재={current_price:.0f}<"
                f"유지선={hold_level:.0f}({source}={resistance:.0f})"
            ), evidence

    return True, (
        f"전고점돌파확인_{source}={resistance:.0f}_"
        f"거래=최대x{vol_vs_max:.2f}/중앙x{vol_vs_median:.2f}"
    ), evidence


class ResistanceGuardEngine(v754.SafeTradingEngine):
    """V75.4 안전장치 위에 전고점 거래폭발 돌파 게이트를 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.resistance_guard_config = ResistanceGuardConfig.load()
        self._resistance_block_until: dict[str, float] = {}
        cfg = self.resistance_guard_config
        self.logger.warning(
            "[%s] 활성 | 전고점 종가돌파 +%.2f%% | 거래량 왼쪽최대x%.2f/중앙x%.2f | "
            "몸통>=%.0f%% | 종가위치>=%.0f%%",
            VERSION,
            cfg.breakout_clear_pct * 100,
            cfg.volume_vs_left_max,
            cfg.volume_vs_left_median,
            cfg.min_body_ratio * 100,
            cfg.min_close_location * 100,
        )

    def _resistance_entry_allowed(
        self,
        signal: Any,
        *,
        current_price: float | None = None,
    ) -> tuple[bool, str]:
        block_until = self._resistance_block_until.get(signal.symbol, 0.0)
        now_mono = time.monotonic()
        if now_mono < block_until:
            return False, f"전고점차단냉각_{block_until - now_mono:.0f}초"
        if block_until:
            self._resistance_block_until.pop(signal.symbol, None)

        allowed, reason, _ = assess_resistance_breakout(
            self.strategy,
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
        allowed, reason = self._resistance_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._resistance_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False
        return super()._attempt_entry(signal, from_retry=from_retry)

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, resistance_reason = self._resistance_entry_allowed(
            signal,
            current_price=current_price,
        )
        if not allowed:
            return False, resistance_reason
        return True, resistance_reason


def _bar(symbol: str, minute: int, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar(
        symbol,
        datetime(2026, 7, 9, 9, minute, tzinfo=KST),
        o,
        h,
        l,
        c,
        v,
    )


def _strategy_with_breakout(signal_volume: int, signal_close: float = 105.70) -> tuple[Any, Any]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "999999"
    bars: list[Any] = []
    for idx in range(30):
        base = 100.0 + (idx % 5) * 0.8
        high = min(105.0, base + 0.8)
        volume = 350 + (idx % 6) * 20
        if idx == 12:
            high = 105.0
            volume = 1000
        bars.append(_bar(symbol, idx, base, high, base - 0.5, min(high - 0.2, base + 0.4), volume))
    bars.append(_bar(symbol, 30, 104.80, 105.82, 104.70, signal_close, signal_volume))
    strategy.bars[symbol].extend(bars)
    strategy.daily_context[symbol] = v753.DailyContext(98.0, 105.0, 0.0, 0.0, False)
    signal = v753.Signal(
        symbol,
        v753.SignalAction.BUY,
        120.0,
        "정석박스돌파|박스=30봉 상단=105|테스트",
        signal_close,
        bars[-1].timestamp,
        "정석박스돌파",
        0.02,
        0.015,
    )
    return strategy, signal


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record("V75.4-SHA256고정", sha256_file(BASE_DIR / V754_FILENAME) == V754_SHA256, V754_SHA256[:16] + "…")
    base_results = v754.self_check()
    base_failures = [name for name, ok, _ in base_results if not ok]
    record("V75.4-기존검증위임", not base_failures, f"{len(base_results)}개 / 실패={base_failures or '없음'}")

    cfg = ResistanceGuardConfig()

    strategy, weak_signal = _strategy_with_breakout(signal_volume=1300)
    allowed, reason, evidence = assess_resistance_breakout(strategy, weak_signal, config=cfg)
    record(
        "전고점약한거래차단",
        not allowed and "거래폭발부족" in reason and evidence is not None,
        reason,
    )

    strategy, strong_signal = _strategy_with_breakout(signal_volume=2200)
    allowed, reason, evidence = assess_resistance_breakout(strategy, strong_signal, config=cfg)
    record(
        "전고점강한돌파허용",
        allowed and evidence is not None and evidence.vol_vs_max >= 1.5 and evidence.vol_vs_median >= 4.0,
        reason,
    )

    strategy, below_signal = _strategy_with_breakout(signal_volume=2200, signal_close=105.20)
    allowed, reason, _ = assess_resistance_breakout(strategy, below_signal, config=cfg)
    record("전고점애매돌파차단", not allowed and "돌파미확인" in reason, reason)

    strategy, strong_signal = _strategy_with_breakout(signal_volume=2200)
    allowed, reason, _ = assess_resistance_breakout(
        strategy,
        strong_signal,
        config=cfg,
        current_price=104.90,
    )
    record("돌파후재이탈차단", not allowed and "재이탈" in reason, reason)

    # 저항과 멀리 떨어진 눌림패턴은 새 게이트가 임의로 막지 않아야 한다.
    settings = v753.Settings.load()
    far_strategy = v753.MomentumVolumeStrategy(settings)
    symbol = "888888"
    far_bars = [_bar(symbol, i, 80, 81, 79, 80, 500) for i in range(19)]
    far_bars.append(_bar(symbol, 19, 89.5, 90.5, 89.0, 90.0, 700))
    far_strategy.bars[symbol].extend(far_bars)
    far_strategy.daily_context[symbol] = v753.DailyContext(80.0, 110.0, 0.0, 0.0, False)
    far_signal = v753.Signal(
        symbol,
        v753.SignalAction.BUY,
        90.0,
        "임펄스눌림 테스트",
        90.0,
        far_bars[-1].timestamp,
        "임펄스눌림1차",
        0.015,
        0.015,
    )
    allowed, reason, _ = assess_resistance_breakout(far_strategy, far_signal, config=cfg)
    record("저항멀면기존전략보존", allowed, reason)

    import ast
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (function_names & FORBIDDEN_ORDER_NAMES), str(sorted(function_names & FORBIDDEN_ORDER_NAMES)))
    record(
        "모든진입경로전고점검사",
        "def _queue_pending_entry" in source
        and "def _attempt_entry" in source
        and "def _pending_signal_valid" in source,
        "등록+직접진입+현재가재검증",
    )
    record(
        "V75.3V75.4원본쓰기부재",
        "write_text" not in source.split("def load_v754", 1)[1].split("v754 =", 1)[0],
        "원본은 SHA검증 후 읽기 로드만",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — 전고점 거래폭발 돌파만 허용")
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
        engine = ResistanceGuardEngine(
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
