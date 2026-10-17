#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.11 LOSS-CONTROL + KOREAN-CONSOLE PAPER
==========================================

V75.10의 모든 매수기법을 유지하면서 손실 원인을 줄이기 위한 최종 공통 진입·손절 계층이다.

추가 방어
- 정석박스돌파는 강한 거래폭발, 강한 몸통, 상단 종가, 짧은 윗꼬리, 이평 상승을 모두 요구한다.
- 진입 직전 현재가가 돌파선 아래로 되밀리면 매수하지 않는다.
- 돌파 후 짧은 시간 안에 돌파선이 무너지면 2회 확인 후 조기 PAPER 청산한다.
- 표시 손절률을 가격 손실률이 아니라 비용 포함 순손실 목표에 가깝도록 앞당긴다.
- 갭·호가 공백·급락 시에는 순손실 목표보다 더 커질 수 있으며 이를 보장하지 않는다.
- Windows 실행창은 UTF-8로 실행한다.
- 실제 주문 기능은 없다.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import math
import os
import re
import statistics
import sys
import time
import traceback
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

VERSION = 'V75.11-LOSS-CONTROL-KOREAN-PAPER'
BASE_DIR = Path(__file__).resolve().parent
V7510_FILENAME = 'bot_v75_10_multi_pattern_precision_paper.py'
V7510_SHA256 = '9b58c7dcb9ddef789d52b954d198be1481f579a7429f222201e37911d72341fa'
FORBIDDEN_ORDER_NAMES = {'send_order', 'create_order', 'place_order', 'order_stock', 'submit_order'}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7510() -> ModuleType:
    path = BASE_DIR / V7510_FILENAME
    if not path.exists():
        raise RuntimeError(f'{V7510_FILENAME}이 같은 폴더에 없습니다.')
    actual = sha256_file(path)
    if actual.lower() != V7510_SHA256:
        raise RuntimeError(
            'V75.10 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n'
            f'기대: {V7510_SHA256}\n실제: {actual}'
        )
    spec = importlib.util.spec_from_file_location('v7510_immutable_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('V75.10 모듈 로더를 만들 수 없습니다.')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7510 = load_v7510()
v7591 = v7510.v7591
v758 = v7510.v758
v757 = v7510.v757
v756 = v7510.v756
v755 = v7510.v755
v754 = v7510.v754
v753 = v7510.v753
KST = v7510.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v756.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v756.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class LossControlConfig:
    breakout_left_bars: int = 35
    breakout_min_left_bars: int = 20
    breakout_volume_vs_left_max: float = 1.50
    breakout_volume_vs_left_median: float = 4.00
    breakout_min_body_ratio: float = 0.58
    breakout_min_close_location: float = 0.78
    breakout_max_upper_wick_ratio: float = 0.22
    breakout_min_clear_pct: float = 0.006
    breakout_hold_above_pct: float = 0.002
    breakout_max_extension_pct: float = 0.025
    breakout_min_bull_bear_volume_ratio: float = 1.05
    early_fail_min_age_sec: int = 25
    early_fail_confirmations: int = 2
    early_fail_hold_buffer_pct: float = 0.001
    early_fail_entry_loss_pct: float = 0.010
    target_net_stop_loss_pct: float = 0.020
    stop_execution_reserve_pct: float = 0.0012
    minimum_price_stop_pct: float = 0.008

    @classmethod
    def load(cls) -> 'LossControlConfig':
        return cls(
            breakout_volume_vs_left_max=env_float('V7511_BREAKOUT_VOL_VS_MAX', 1.50, minimum=1.10, maximum=5.0),
            breakout_volume_vs_left_median=env_float('V7511_BREAKOUT_VOL_VS_MEDIAN', 4.00, minimum=2.0, maximum=12.0),
            breakout_min_body_ratio=env_float('V7511_BREAKOUT_MIN_BODY', 0.58, minimum=0.30, maximum=0.90),
            breakout_min_close_location=env_float('V7511_BREAKOUT_MIN_CLOSE_LOC', 0.78, minimum=0.55, maximum=0.98),
            breakout_max_upper_wick_ratio=env_float('V7511_BREAKOUT_MAX_UPPER_WICK', 0.22, minimum=0.05, maximum=0.50),
            breakout_hold_above_pct=env_float('V7511_BREAKOUT_HOLD_ABOVE', 0.002, minimum=0.0, maximum=0.02),
            early_fail_min_age_sec=env_int('V7511_EARLY_FAIL_MIN_AGE_SEC', 25, minimum=5, maximum=180),
            early_fail_confirmations=env_int('V7511_EARLY_FAIL_CONFIRMATIONS', 2, minimum=1, maximum=5),
            early_fail_entry_loss_pct=env_float('V7511_EARLY_FAIL_ENTRY_LOSS', 0.010, minimum=0.004, maximum=0.025),
            target_net_stop_loss_pct=env_float('V7511_TARGET_NET_STOP_LOSS', 0.020, minimum=0.010, maximum=0.035),
            stop_execution_reserve_pct=env_float('V7511_STOP_EXECUTION_RESERVE', 0.0012, minimum=0.0, maximum=0.006),
        )


@dataclass(frozen=True, slots=True)
class BreakoutQualityEvidence:
    signal_time: datetime
    box_high: float
    signal_close: float
    body_ratio: float
    close_location: float
    upper_wick_ratio: float
    volume_vs_left_max: float
    volume_vs_left_median: float
    bull_bear_volume_ratio: float
    ma5: float
    ma10: float
    ma20: float


_BOX_RE = re.compile(r'상단=([0-9]+(?:\.[0-9]+)?)')


def _parse_box_high(reason: str) -> float:
    match = _BOX_RE.search(str(reason))
    return float(match.group(1)) if match else 0.0


def _same_session_bars(strategy: Any, symbol: str) -> list[Any]:
    return list(v756._same_session_bars(strategy.bars.get(symbol, [])))


def _find_signal_bar_index(bars: Sequence[Any], timestamp: datetime) -> int | None:
    target = v754.normalize_kst(timestamp)
    for idx in range(len(bars) - 1, -1, -1):
        if v754.normalize_kst(bars[idx].timestamp) == target:
            return idx
    return None


def _ma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return statistics.fmean(values[-period:])


def assess_strict_box_breakout(
    strategy: Any,
    signal: Any,
    *,
    config: LossControlConfig,
    current_price: float | None = None,
) -> tuple[bool, str, BreakoutQualityEvidence | None]:
    if getattr(signal, 'pattern', '') != '정석박스돌파':
        return True, '엄격박스돌파대상아님', None

    bars = _same_session_bars(strategy, signal.symbol)
    signal_idx = _find_signal_bar_index(bars, signal.timestamp)
    if signal_idx is None:
        return False, '엄격돌파신호봉없음', None
    signal_bar = bars[signal_idx]
    left = bars[max(0, signal_idx - config.breakout_left_bars):signal_idx]
    if len(left) < config.breakout_min_left_bars:
        return False, f'엄격돌파왼쪽분봉부족_{len(left)}<{config.breakout_min_left_bars}', None

    box_high = _parse_box_high(getattr(signal, 'reason', ''))
    if box_high <= 0:
        box_high = max(float(item.high) for item in left)

    candle_range, signed_body, absolute_body, close_location = v756._candle_metrics(signal_bar)
    upper_wick_ratio = max(
        0.0,
        (float(signal_bar.high) - max(float(signal_bar.open), float(signal_bar.close))) / max(1e-9, candle_range),
    )
    if float(signal_bar.close) <= float(signal_bar.open) or signed_body <= 0:
        return False, '엄격돌파봉양봉아님', None

    left_volumes = [max(0.0, float(item.volume)) for item in left]
    vol_vs_max = float(signal_bar.volume) / max(1.0, max(left_volumes))
    vol_vs_median = float(signal_bar.volume) / max(1.0, statistics.median(left_volumes))

    flow = left[-8:] + [signal_bar]
    bull_volume = sum(max(0.0, float(item.volume)) for item in flow if float(item.close) >= float(item.open))
    bear_volume = sum(max(0.0, float(item.volume)) for item in flow if float(item.close) < float(item.open))
    bull_bear_ratio = bull_volume / max(1.0, bear_volume)

    closes = [float(item.close) for item in bars[:signal_idx + 1]]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    prev_ma5 = _ma(closes[:-1], 5)
    prev_ma10 = _ma(closes[:-1], 10)

    evidence = BreakoutQualityEvidence(
        signal_time=v754.normalize_kst(signal_bar.timestamp),
        box_high=box_high,
        signal_close=float(signal_bar.close),
        body_ratio=signed_body,
        close_location=close_location,
        upper_wick_ratio=upper_wick_ratio,
        volume_vs_left_max=vol_vs_max,
        volume_vs_left_median=vol_vs_median,
        bull_bear_volume_ratio=bull_bear_ratio,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
    )

    required_close = box_high * (1.0 + config.breakout_min_clear_pct)
    if float(signal_bar.close) < required_close:
        return False, f'엄격돌파종가미달_{signal_bar.close:.0f}<{required_close:.0f}', evidence
    extension = float(signal_bar.close) / max(0.01, box_high) - 1.0
    if extension > config.breakout_max_extension_pct:
        return False, f'엄격돌파추격이격_{extension:.2%}>{config.breakout_max_extension_pct:.2%}', evidence
    if vol_vs_max < config.breakout_volume_vs_left_max:
        return False, f'엄격돌파거래왼쪽최대부족_x{vol_vs_max:.2f}<{config.breakout_volume_vs_left_max:.2f}', evidence
    if vol_vs_median < config.breakout_volume_vs_left_median:
        return False, f'엄격돌파거래중앙부족_x{vol_vs_median:.2f}<{config.breakout_volume_vs_left_median:.2f}', evidence
    if signed_body < config.breakout_min_body_ratio:
        return False, f'엄격돌파몸통부족_{signed_body:.2f}<{config.breakout_min_body_ratio:.2f}', evidence
    if close_location < config.breakout_min_close_location:
        return False, f'엄격돌파종가위치약함_{close_location:.2f}<{config.breakout_min_close_location:.2f}', evidence
    if upper_wick_ratio > config.breakout_max_upper_wick_ratio:
        return False, f'엄격돌파윗꼬리과다_{upper_wick_ratio:.2f}>{config.breakout_max_upper_wick_ratio:.2f}', evidence
    if bull_bear_ratio < config.breakout_min_bull_bear_volume_ratio:
        return False, f'엄격돌파양봉거래우위부족_x{bull_bear_ratio:.2f}', evidence
    if min(ma5, ma10, ma20) <= 0 or float(signal_bar.close) < max(ma5, ma10, ma20):
        return False, '엄격돌파이평상단안착실패', evidence
    if ma5 <= prev_ma5 or ma10 < prev_ma10 * 0.999:
        return False, '엄격돌파단기이평상승실패', evidence

    if current_price is not None:
        hold = box_high * (1.0 + config.breakout_hold_above_pct)
        if float(current_price) < hold:
            return False, f'엄격돌파현재가재이탈_{current_price:.0f}<{hold:.0f}', evidence
        drift = float(current_price) / max(0.01, float(signal.reference_price)) - 1.0
        if drift > 0.007:
            return False, f'엄격돌파현재가추격_{drift:+.2%}>0.70%', evidence

    return True, (
        f'엄격돌파확인_거래=최대x{vol_vs_max:.2f}/중앙x{vol_vs_median:.2f}'
        f'_몸통={signed_body:.2f}_종가위치={close_location:.2f}_양봉거래우위=x{bull_bear_ratio:.2f}'
    ), evidence


def cost_aware_price_stop_pct(settings: Any, requested_stop_pct: float, config: LossControlConfig) -> float:
    """순손실 목표에서 매수·매도 비용과 매도 슬리피지, 소규모 체결여유를 역산한다."""
    requested = max(config.minimum_price_stop_pct, float(requested_stop_pct))
    commission = max(0.0, float(settings.commission_pct))
    tax = max(0.0, float(settings.sell_tax_pct))
    slippage = max(0.0, float(settings.slippage_pct))
    denominator = max(0.90, (1.0 - slippage) * (1.0 - commission - tax))
    trigger_ratio = ((1.0 - config.target_net_stop_loss_pct) * (1.0 + commission)) / denominator
    trigger_ratio = min(0.999, trigger_ratio + config.stop_execution_reserve_pct)
    cost_aware = max(config.minimum_price_stop_pct, 1.0 - trigger_ratio)
    return min(requested, cost_aware)


def signal_with_cost_aware_stop(signal: Any, settings: Any, config: LossControlConfig) -> Any:
    if getattr(signal, 'action', None) != v753.SignalAction.BUY:
        return signal
    adjusted_stop = cost_aware_price_stop_pct(settings, signal.stop_loss_pct, config)
    if adjusted_stop >= float(signal.stop_loss_pct) - 1e-12:
        return signal
    return replace(
        signal,
        stop_loss_pct=adjusted_stop,
        reason=(
            f'{signal.reason}|비용포함손절보정=' 
            f'{signal.stop_loss_pct:.2%}→가격트리거{adjusted_stop:.2%}'
        ),
    )


class LossControlEngine(v7510.MultiPatternPrecisionEngine):
    """V75.10 전체 패턴 위에 공통 품질·손실 제어를 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v7511_config = LossControlConfig.load()
        self._breakout_hold_level: dict[str, float] = {}
        self._breakout_fail_hits: dict[str, int] = {}
        cfg = self.v7511_config
        adjusted = cost_aware_price_stop_pct(self.settings, 0.020, cfg)
        self.logger.warning(
            '[%s] 손실방어 추가 | 정석돌파 거래=왼쪽최대x%.2f/중앙x%.2f | '
            '몸통>=%.0f%% 종가위치>=%.0f%% 윗꼬리<=%.0f%% | '
            '순손실목표 %.1f%%용 가격손절 약 %.2f%% | 한글 UTF-8',
            VERSION,
            cfg.breakout_volume_vs_left_max,
            cfg.breakout_volume_vs_left_median,
            cfg.breakout_min_body_ratio * 100,
            cfg.breakout_min_close_location * 100,
            cfg.breakout_max_upper_wick_ratio * 100,
            cfg.target_net_stop_loss_pct * 100,
            adjusted * 100,
        )

    def _loss_quality_allowed(self, signal: Any, current_price: float | None = None) -> tuple[bool, str, BreakoutQualityEvidence | None]:
        return assess_strict_box_breakout(
            self.strategy,
            signal,
            config=self.v7511_config,
            current_price=current_price,
        )

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason, _ = self._loss_quality_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning('진입폐기 %s %s', self._label(signal.symbol), reason)
            return
        adjusted = signal_with_cost_aware_stop(signal, self.settings, self.v7511_config)
        super()._queue_pending_entry(adjusted)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason, evidence = self._loss_quality_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning('진입폐기 %s %s', self._label(signal.symbol), reason)
            return False
        adjusted = signal_with_cost_aware_stop(signal, self.settings, self.v7511_config)
        entered = super()._attempt_entry(adjusted, from_retry=from_retry)
        if entered and getattr(adjusted, 'pattern', '') == '정석박스돌파':
            if evidence is None:
                _, _, evidence = self._loss_quality_allowed(adjusted)
            if evidence is not None:
                self._breakout_hold_level[adjusted.symbol] = evidence.box_high * (
                    1.0 + self.v7511_config.breakout_hold_above_pct
                )
                self._breakout_fail_hits[adjusted.symbol] = 0
        return entered

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, quality_reason, _ = self._loss_quality_allowed(signal, current_price=current_price)
        if not allowed:
            return False, quality_reason
        return True, quality_reason if getattr(signal, 'pattern', '') == '정석박스돌파' else reason

    def _check_tick_exit(
        self,
        quote: Any,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        position = self.broker.positions.get(quote.symbol)
        if position is not None and position.pattern == '정석박스돌파':
            age_sec = max(0.0, (datetime.now(KST) - position.entry_time.astimezone(KST)).total_seconds())
            hold_level = self._breakout_hold_level.get(quote.symbol, 0.0)
            above_fixed_stop = float(quote.price) > float(position.stop_price)
            failed_hold = hold_level > 0 and float(quote.price) < hold_level * (
                1.0 - self.v7511_config.early_fail_hold_buffer_pct
            )
            failed_entry = float(quote.price) <= float(position.entry_price) * (
                1.0 - self.v7511_config.early_fail_entry_loss_pct
            )
            early_failure = age_sec >= self.v7511_config.early_fail_min_age_sec and above_fixed_stop and (
                failed_hold or failed_entry
            )
            if early_failure:
                hits = self._breakout_fail_hits.get(quote.symbol, 0) + 1
                self._breakout_fail_hits[quote.symbol] = hits
                if hits >= self.v7511_config.early_fail_confirmations:
                    detected_at = detection_time.astimezone(KST) if detection_time is not None else datetime.now(KST)
                    try:
                        enriched = self.client.get_orderbook(quote.symbol, quote, side='sell')
                        fill = self.broker.sell(
                            quote.symbol,
                            enriched,
                            '돌파실패조기손절',
                            trigger_time=detected_at,
                            trigger_price=quote.price,
                            detection_time=detected_at,
                            observed_lag_sec=observed_lag_sec,
                            trigger_source='V75.11_BREAKOUT_FAILURE',
                        )
                    except (v753.V73Error, ValueError) as exc:
                        self.logger.warning('조기청산대기 %s %s', self._label(quote.symbol), exc)
                    else:
                        self.strategy.mark_exit(quote.symbol)
                        self.risk.record_pnl(fill.pnl)
                        self._print_sell(fill)
                        return
            else:
                self._breakout_fail_hits[quote.symbol] = 0
        super()._check_tick_exit(
            quote,
            detection_time=detection_time,
            observed_lag_sec=observed_lag_sec,
        )

    def _print_sell(self, fill: Any) -> None:
        self._breakout_hold_level.pop(fill.symbol, None)
        self._breakout_fail_hits.pop(fill.symbol, None)
        super()._print_sell(fill)


def _bar(symbol: str, minute: int, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar(symbol, datetime(2026, 7, 10, 9, minute, tzinfo=KST), o, h, l, c, v)


def _breakout_case(*, volume: int = 2300, upper_wick: bool = False) -> tuple[Any, Any]:
    settings = v753.Settings.load()
    strategy = v753.MomentumVolumeStrategy(settings)
    symbol = '751100'
    bars: list[Any] = []
    for idx in range(30):
        base = 101.0 + idx * 0.10 + (idx % 3) * 0.05
        high = min(105.0, base + 0.45)
        vol = 430 + (idx % 5) * 25
        if idx == 12:
            high = 105.0
            vol = 1000
        bars.append(_bar(symbol, idx, base, high, base - 0.30, min(high - 0.12, base + 0.20), vol))
    high = 108.0 if upper_wick else 106.25
    signal_bar = _bar(symbol, 30, 104.80, high, 104.70, 106.05, volume)
    bars.append(signal_bar)
    strategy.bars[symbol].extend(bars)
    strategy.daily_context[symbol] = v753.DailyContext(98.0, 120.0, 0.0, 0.0, True)
    signal = v753.Signal(
        symbol,
        v753.SignalAction.BUY,
        120.0,
        '정석박스돌파|박스=30봉 상단=105|테스트',
        signal_bar.close,
        signal_bar.timestamp,
        '정석박스돌파',
        0.020,
        0.015,
    )
    return strategy, signal


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = '') -> None:
        results.append((name, bool(ok), detail))

    record('V75.10-SHA256고정', sha256_file(BASE_DIR / V7510_FILENAME) == V7510_SHA256, V7510_SHA256[:16] + '…')
    base_results = v7510.self_check()
    base_failures = [name for name, ok, _ in base_results if not ok]
    record('V75.10기존검증위임', not base_failures, f'{len(base_results)}항목 / 실패={base_failures or "없음"}')

    cfg = LossControlConfig()
    strategy, signal = _breakout_case()
    allowed, reason, evidence = assess_strict_box_breakout(strategy, signal, config=cfg, current_price=106.0)
    record(
        '강한거래돌파허용',
        allowed and evidence is not None and evidence.volume_vs_left_max >= 1.5 and evidence.volume_vs_left_median >= 4.0,
        reason,
    )

    strategy, signal = _breakout_case(volume=1300)
    allowed, reason, _ = assess_strict_box_breakout(strategy, signal, config=cfg, current_price=106.0)
    record('약한거래돌파차단', not allowed and '거래' in reason, reason)

    strategy, signal = _breakout_case(upper_wick=True)
    allowed, reason, _ = assess_strict_box_breakout(strategy, signal, config=cfg, current_price=106.0)
    record('긴윗꼬리돌파차단', not allowed and ('윗꼬리' in reason or '몸통' in reason), reason)

    strategy, signal = _breakout_case()
    allowed, reason, _ = assess_strict_box_breakout(strategy, signal, config=cfg, current_price=104.9)
    record('돌파선재이탈차단', not allowed and '재이탈' in reason, reason)

    settings = v753.Settings.load()
    adjusted = cost_aware_price_stop_pct(settings, 0.020, cfg)
    denominator = (1.0 + settings.commission_pct)
    estimated_sell_price_ratio = (1.0 - adjusted) * (1.0 - settings.slippage_pct)
    estimated_net_ratio = estimated_sell_price_ratio * (1.0 - settings.commission_pct - settings.sell_tax_pct) / denominator
    estimated_net_loss = 1.0 - estimated_net_ratio
    record(
        '비용포함손절보정',
        0.008 <= adjusted < 0.020 and estimated_net_loss <= cfg.target_net_stop_loss_pct + cfg.stop_execution_reserve_pct + 0.0005,
        f'가격트리거={adjusted:.2%} 예상순손실={estimated_net_loss:.2%}',
    )

    adjusted_signal = signal_with_cost_aware_stop(signal, settings, cfg)
    record('신호손절률교체', adjusted_signal.stop_loss_pct < signal.stop_loss_pct, f'{signal.stop_loss_pct:.2%}→{adjusted_signal.stop_loss_pct:.2%}')

    source = Path(__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    record('실주문함수부재', not (names & FORBIDDEN_ORDER_NAMES), f'금지함수={sorted(names & FORBIDDEN_ORDER_NAMES) or "없음"}')
    record(
        '모든진입경로품질검사',
        'def _queue_pending_entry' in source and 'def _attempt_entry' in source and 'def _pending_signal_valid' in source,
        '등록+직접진입+현재가재검증',
    )
    record(
        '돌파실패2회조기청산',
        '_breakout_fail_hits' in source and 'early_fail_confirmations' in source and '돌파실패조기손절' in source,
        '기본 25초 이후 2회 확인',
    )
    record(
        '기존패턴보존',
        'class LossControlEngine(v7510.MultiPatternPrecisionEngine)' in source,
        'V75.10 전체 패턴 상속',
    )
    record(
        'V75.10원본쓰기부재',
        'write_text' not in source.split('def load_v7510', 1)[1].split('v7510 =', 1)[0],
        'SHA 검증 후 읽기 로드만',
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f'{VERSION} — 손실방어·복수패턴 PAPER')
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--quote-test', action='store_true')
    parser.add_argument('--scan-test', action='store_true')
    parser.add_argument('--symbol', default='005930')
    parser.add_argument('--minutes', type=int, default=0)
    parser.add_argument('--reset-paper', action='store_true')
    parser.add_argument('--reset-v754-safety', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    return parser


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = build_parser().parse_args()
    logger = v753.setup_logging(args.verbose)
    try:
        settings = v753.Settings.load()
        logger.info('환경파일 자동탐색=%s', [str(path) for path in loaded] or '없음')
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}')
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
        logger.warning('시작 전 현재가 계약검증: %s', settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning('계약검증 성공 %s %.0f원', first.symbol, first.price)
        engine_logger = v7591.CommaSafeLoggerAdapter(logger, {})
        engine = LossControlEngine(
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
        logger.critical('설정 오류: %s', exc)
        return 2
    except v753.AuthenticationError as exc:
        logger.critical('인증 오류: %s', exc)
        return 3
    except v753.V73Error as exc:
        logger.critical('실행 차단: %s', exc)
        return 4
    except Exception as exc:
        logger.critical('예상 밖 치명 오류: %s', exc)
        v753.append_api_error(traceback.format_exc())
        return 9


if __name__ == '__main__':
    raise SystemExit(main())
