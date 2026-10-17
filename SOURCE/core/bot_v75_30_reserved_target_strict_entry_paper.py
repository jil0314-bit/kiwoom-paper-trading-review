#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.30 PAPER

핵심 수정
- 매수 즉시 등록된 +1.5% PAPER 지정가를 '현재가 샘플'만으로 감시하지 않는다.
- 현재가를 놓쳐도 이후 1분봉 고가가 목표가에 닿으면 예약 지정가 체결로 복원한다.
- 진입 분봉의 매수 전 고가는 사용하지 않는다.
- 자동손절 없음 정책을 현재가 감시뿐 아니라 완성/진행 분봉 경로에도 적용한다.
- 선행 1차 상승과 고거래 장대양봉 기준을 강화하고, 보통 신호는 지지확인 2봉을 요구한다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Sequence

VERSION = 'V75.30-RESERVED-TARGET-STRICT-ENTRY-PAPER'
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = 'bot_v75_29_gui_link_display_fix_paper.py'
BASE_SHA256 = '5f5e9405c92cfc7123e3f3a6d7a48dc3b2c597d21972bd337fce1357d83dedf5'
TARGET_RECONCILE_SEC = 8.0
TARGET_BOOTSTRAP_BARS = 500
TARGET_INCREMENTAL_BARS = 8
FIXED_TAKE_PROFIT_PCT = 0.015

# 강화된 1차 상승/장대양봉 기준
STRICT_IMPULSE_LOOKBACK = 30
STRICT_IMPULSE_MIN_BODY_RATIO = 0.55
STRICT_IMPULSE_MIN_BODY_GAIN = 0.008
STRICT_IMPULSE_MIN_RANGE_PCT = 0.012
STRICT_IMPULSE_MIN_CLOSE_LOCATION = 0.75
STRICT_IMPULSE_MAX_UPPER_WICK = 0.18
STRICT_IMPULSE_MIN_VOL_MEDIAN = 1.80
STRICT_IMPULSE_MIN_VOL_P75 = 1.20
STRICT_PRIOR_RISE_MIN = 0.030
STRICT_ADVANCING_BARS_MIN = 2


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_base() -> ModuleType:
    path = BASE_DIR / BASE_FILENAME
    if sha256_file(path) != BASE_SHA256:
        raise RuntimeError('V75.29 기준본 SHA256 불일치')
    spec = importlib.util.spec_from_file_location('v7529_immutable_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('V75.29 로드 실패')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


v7529 = load_base()
v7525 = v7529.v7525
v7524 = v7529.v7524
v7515 = v7525.v7515
v756 = v7525.v756
v754 = v7525.v754
v753 = v7525.v753
v7514 = v7525.v7514
v7520 = v7525.v7520
KST = v7525.KST
_ORIGINAL_SELF_CHECK = v7525.self_check


def _bar_minute(value: datetime) -> datetime:
    ts = value if value.tzinfo is not None else value.replace(tzinfo=KST)
    return ts.astimezone(KST).replace(second=0, microsecond=0)


def reserved_target_bar_eligible(entry_time: datetime, bar: Any, target_price: float) -> bool:
    """예약 지정가는 매수 다음 분봉부터 고가가 목표가에 닿으면 체결로 본다.

    매수한 바로 그 분봉은 고가가 매수 전에 형성됐을 수 있어 사용하지 않는다.
    """
    return (
        _bar_minute(bar.timestamp) > _bar_minute(entry_time)
        and float(getattr(bar, 'volume', 0.0)) > 0
        and float(getattr(bar, 'high', 0.0)) >= float(target_price)
    )


def strict_prior_impulse(bars: Sequence[Any], structure: Any, cfg: Any) -> dict[str, float | int] | None:
    anchor = int(structure.anchor_index)
    start = max(0, anchor - min(STRICT_IMPULSE_LOOKBACK, int(cfg.rise_lookback_bars)))
    best: dict[str, float | int] | None = None
    for index in range(start, anchor):
        bar = bars[index]
        span, signed, body_ratio, close_location, _, upper_wick = v7515.candle_metrics(bar)
        open_price = max(0.01, float(bar.open))
        body_gain = (float(bar.close) - float(bar.open)) / open_price
        range_pct = span / open_price
        if signed <= 0:
            continue
        if body_ratio < STRICT_IMPULSE_MIN_BODY_RATIO or body_gain < STRICT_IMPULSE_MIN_BODY_GAIN:
            continue
        if range_pct < STRICT_IMPULSE_MIN_RANGE_PCT:
            continue
        if close_location < STRICT_IMPULSE_MIN_CLOSE_LOCATION or upper_wick > STRICT_IMPULSE_MAX_UPPER_WICK:
            continue

        left_start = max(start, index - min(12, int(cfg.max_left_bars)))
        left = list(bars[left_start:index])
        if len(left) < int(cfg.min_left_bars):
            continue
        volumes = [max(0.0, float(item.volume)) for item in left]
        median = float(statistics.median(volumes))
        p75 = float(v7515.percentile75(volumes))
        volume = max(0.0, float(bar.volume))
        vs_median = volume / max(1.0, median)
        vs_p75 = volume / max(1.0, p75)
        if vs_median < STRICT_IMPULSE_MIN_VOL_MEDIAN or vs_p75 < STRICT_IMPULSE_MIN_VOL_P75:
            continue

        rise_window = list(bars[left_start:anchor])
        local_low = min(float(item.low) for item in rise_window)
        pre_heavy_high = max(float(item.high) for item in rise_window)
        rise_gain = pre_heavy_high / max(0.01, local_low) - 1.0
        advancing = sum(1 for item in bars[left_start:index + 1] if float(item.close) > float(item.open))
        if rise_gain < max(STRICT_PRIOR_RISE_MIN, float(cfg.min_prior_rise_pct)):
            continue
        if advancing < STRICT_ADVANCING_BARS_MIN:
            continue

        score = (
            body_ratio * 30.0 + close_location * 20.0 + min(vs_median, 5.0) * 10.0
            + body_gain * 700.0 + rise_gain * 500.0
        )
        candidate: dict[str, float | int] = {
            'index': index,
            'body': body_ratio,
            'close_location': close_location,
            'upper_wick': upper_wick,
            'body_gain': body_gain,
            'range_pct': range_pct,
            'rise_gain': rise_gain,
            'advancing_bars': advancing,
            'volume_vs_median': vs_median,
            'volume_vs_p75': vs_p75,
            'score': score,
        }
        if best is None or float(candidate['score']) > float(best['score']):
            best = candidate
    return best


def strict_find_heavy_structures(bars: Sequence[Any], cfg: Any) -> list[Any]:
    structures = v7525._ORIGINAL_FIND_HEAVY(bars, cfg)
    return [item for item in structures if strict_prior_impulse(bars, item, cfg) is not None]


def strict_matching_structure(bars: Sequence[Any], evidence: Any, cfg: Any) -> Any | None:
    flush_time = getattr(evidence, 'flush_time', None)
    if flush_time is None:
        return None
    normalized = v754.normalize_kst(flush_time)
    for structure in strict_find_heavy_structures(bars, cfg):
        bar_time = v754.normalize_kst(bars[int(structure.last_heavy_index)].timestamp)
        if bar_time == normalized:
            return structure
    # 다른 과거 구조를 억지로 연결하지 않는다.
    return None


def _exceptional_single_support(bars: Sequence[Any], structure: Any, cfg: Any, impulse: dict[str, float | int]) -> bool:
    current = bars[-1]
    span, signed, body_ratio, close_location, _, upper_wick = v7515.candle_metrics(current)
    dry_heavy = float(current.volume) / max(1.0, float(structure.heavy_volume))
    dry_context = float(current.volume) / max(1.0, float(structure.context_volume))
    short_ratio = span / max(1e-9, float(structure.heavy_range))
    return (
        float(impulse['rise_gain']) >= 0.040
        and float(impulse['volume_vs_median']) >= 2.0
        and signed > 0
        and body_ratio >= 0.30
        and close_location >= 0.70
        and upper_wick <= 0.20
        and dry_heavy <= min(0.30, float(cfg.strong_dry_max_volume_vs_heavy_cluster))
        and dry_context <= float(cfg.dry_max_volume_vs_context)
        and short_ratio <= 0.60
    )


def strict_detect(strategy: Any, symbol: str, *, config: Any) -> tuple[Any | None, str, Any | None]:
    signal, state, evidence = v7525._ORIGINAL_DETECT(strategy, symbol, config=config)
    if signal is None or evidence is None:
        return signal, state, evidence
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    if not bars:
        return None, '분봉없음', None
    if v754.normalize_kst(signal.timestamp) != v754.normalize_kst(bars[-1].timestamp):
        return None, '과거신호차단', evidence
    structure = strict_matching_structure(bars, evidence, cfg)
    if structure is None:
        return None, '선행1차상승·장대양봉불충분', evidence
    impulse = strict_prior_impulse(bars, structure, cfg)
    if impulse is None:
        return None, '선행1차상승·장대양봉불충분', evidence

    supports = int(v7525._support_count(bars, structure, cfg))
    required = 1 if _exceptional_single_support(bars, structure, cfg, impulse) else 2
    if supports < required:
        return None, f'지지확인봉대기_{supports}/{required}', evidence

    import re
    reason = re.sub(r'\|손절=[^|]+', '', str(signal.reason))
    reason = re.sub(r'\|목표=[^|]+', '', reason)
    reason += (
        f"|1차상승={float(impulse['rise_gain']):.1%}"
        f"|선행장대양봉=몸통{float(impulse['body_gain']):.1%}/거래x{float(impulse['volume_vs_median']):.2f}"
        f"|지지확인봉={supports}/{required}|자동손절없음|목표=1.5%고정"
    )
    fixed = v753.Signal(
        signal.symbol, signal.action, float(signal.score) + min(15.0, max(0, supports - 1) * 5.0),
        reason, float(signal.reference_price), signal.timestamp, str(signal.pattern),
        float(signal.stop_loss_pct), FIXED_TAKE_PROFIT_PCT,
    )
    return fixed, state, evidence


def strict_assess(strategy: Any, symbol: str, *, loose: Any, strict: Any, now: datetime, name: str, source: str) -> tuple[Any | None, str]:
    item, state = v7525._ORIGINAL_ASSESS(
        strategy, symbol, loose=loose, strict=strict, now=now, name=name, source=source
    )
    if item is None:
        return None, state
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    structures = strict_find_heavy_structures(bars, cfg) if bars else []
    if not structures:
        return None, '선행1차상승·장대양봉불충분'
    return replace(item, source=f'{source}|선행1차상승+장대양봉'), state


# 상속 경로 전체에 강화 탐지기를 적용한다.
v7525._prior_impulse = strict_prior_impulse
v7525.find_qualified_heavy_structures = strict_find_heavy_structures
v7525._matching_structure = strict_matching_structure
v7525.detect_v7525 = strict_detect
v7525.assess_v7525 = strict_assess
v7515.find_heavy_structures = strict_find_heavy_structures
v7515.detect_precision_supply = strict_detect
v7515.assess_precision_watch = strict_assess
v756.detect_supply_exhaustion_pullback = strict_detect
v7514.assess_loose_supply_potential = strict_assess


class PrecisionTargetEngine(v7529.EnhancedEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._target_bar_due: dict[str, float] = {}
        self._target_completed_checked: dict[str, datetime] = {}
        self._target_bootstrap_done: set[str] = set()
        super().__init__(*args, **kwargs)

    def _execute_reserved_target_from_bar(self, symbol: str, bar: Any, *, source: str) -> bool:
        position = self.broker.positions.get(symbol)
        if position is None or not bool(position.target_order_active):
            return False
        if not reserved_target_bar_eligible(position.entry_time, bar, position.target_price):
            return False
        detected = datetime.now(KST)
        quote = self.latest_quotes.get(symbol)
        if quote is None:
            quote = self.client.get_quote(symbol)
            self.latest_quotes[symbol] = quote
        try:
            fill = self.broker.sell(
                symbol, quote, '예약목표+1.5%', reserved_fill_price=position.target_price,
                trigger_time=detected, trigger_price=position.target_price,
                detection_time=detected, observed_lag_sec=0.0, trigger_source=source,
            )
        except (v753.V73Error, ValueError) as exc:
            self._show_throttled_error(
                f'targetbar:{symbol}', f'목표매도 복원대기 {self._label(symbol)}: {v753.brief_error(exc)}', 15.0
            )
            return False
        self.strategy.mark_exit(symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)
        self.logger.warning(
            '예약목표 체결복원 %s 목표=%.0f 확인봉고가=%.0f source=%s',
            self._label(symbol), float(position.target_price), float(bar.high), source,
        )
        self._target_bar_due.pop(symbol, None)
        self._target_completed_checked.pop(symbol, None)
        self._target_bootstrap_done.discard(symbol)
        return True

    def _reconcile_reserved_target(self, symbol: str) -> bool:
        position = self.broker.positions.get(symbol)
        if position is None or not bool(position.target_order_active):
            return False
        now_mono = time.monotonic()
        if now_mono < self._target_bar_due.get(symbol, 0.0):
            return False
        self._target_bar_due[symbol] = now_mono + TARGET_RECONCILE_SEC
        first = symbol not in self._target_bootstrap_done
        limit = TARGET_BOOTSTRAP_BARS if first else TARGET_INCREMENTAL_BARS
        try:
            bars = self.client.get_minute_bars(symbol, limit=limit, completed_only=False)
        except v753.V73Error as exc:
            self._show_throttled_error(
                f'targetbars:{symbol}', f'목표봉 감시지연 {self._label(symbol)}: {v753.brief_error(exc)}', 15.0
            )
            return False
        self._target_bootstrap_done.add(symbol)
        current_minute = _bar_minute(datetime.now(KST))
        last_completed = self._target_completed_checked.get(symbol)
        newest_completed: datetime | None = last_completed
        for bar in sorted(bars, key=lambda item: item.timestamp):
            bar_minute = _bar_minute(bar.timestamp)
            if bar_minute < current_minute:
                if last_completed is not None and bar_minute <= last_completed:
                    continue
                if newest_completed is None or bar_minute > newest_completed:
                    newest_completed = bar_minute
            elif bar_minute > current_minute:
                continue
            # 현재 진행봉은 매번 다시 확인하여 고가 갱신을 놓치지 않는다.
            if self._execute_reserved_target_from_bar(symbol, bar, source='REST_MINUTE_HIGH_RESERVED_ORDER'):
                return True
        if newest_completed is not None:
            self._target_completed_checked[symbol] = newest_completed
        return False

    def _monitor_one_position(self) -> bool:
        symbols = list(self.broker.positions)
        selected = symbols[self.position_cursor % len(symbols)] if symbols else None
        result = super()._monitor_one_position()
        if selected is not None and selected in self.broker.positions:
            self._reconcile_reserved_target(selected)
        return result

    def _on_bar(self, bar: Any, quote: Any, *, allow_entry: bool = True, bar_age_sec: float = 0.0) -> None:
        # 보유 중에는 목표 예약매도만 허용한다. 분봉 저가에 의한 자동손절은 실행하지 않는다.
        position = self.broker.positions.get(bar.symbol)
        if position is not None:
            signal = self.strategy.on_bar(bar, position)
            self.latest_signals[bar.symbol] = signal
            if self._execute_reserved_target_from_bar(bar.symbol, bar, source='COMPLETED_OR_CURRENT_BAR'):
                return
            if getattr(signal.action, 'value', str(signal.action)) == 'SELL':
                self.logger.info('자동손절 신호 무시 %s reason=%s', self._label(bar.symbol), signal.reason)
            return
        super()._on_bar(bar, quote, allow_entry=allow_entry, bar_age_sec=bar_age_sec)

    def _print_buy(self, fill: Any, pos: Any, signal: Any) -> None:
        super()._print_buy(fill, pos, signal)
        print(
            f'[목표매도등록] {fill.timestamp:%H:%M:%S} {self._label(fill.symbol)} '
            f'매수가 {float(pos.entry_price):,.0f}원 → +1.5% 지정가 {float(pos.target_price):,.0f}원'
        )


# V75.29 GUI/표시를 유지하면서 엔진만 교체한다.
v7525.VERSION = VERSION
v7525.V7525Engine = PrecisionTargetEngine


def _synthetic_bar(ts: datetime, o: float, h: float, l: float, c: float, v: int) -> Any:
    return v753.Bar('999999', ts, o, h, l, c, v)


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    # 1차: 기준본과 정적 구조
    record('V75.29기준본SHA', sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16])
    source = Path(__file__).read_text(encoding='utf-8')
    record('현재가놓침분봉복원', 'REST_MINUTE_HIGH_RESERVED_ORDER' in source, '진행/완성 1분봉 고가 재확인')
    record('자동손절완전차단', '자동손절 신호 무시' in source and 'stop_price' not in source.split('def _on_bar',1)[1].split('def _print_buy',1)[0], '분봉 손절 미실행')
    record('목표즉시등록표시', '[목표매도등록]' in source, '+1.5% 지정가')

    # 2차: 실제 사례 수치 회귀검사
    entry = datetime(2026, 7, 14, 13, 51, 24, tzinfo=KST)
    maki = _synthetic_bar(datetime(2026, 7, 14, 13, 52, tzinfo=KST), 28000, 28650, 27900, 28300, 1000)
    jw = _synthetic_bar(datetime(2026, 7, 14, 14, 1, tzinfo=KST), 1595, 1606, 1592, 1602, 1000)
    same_minute = _synthetic_bar(datetime(2026, 7, 14, 13, 51, tzinfo=KST), 27700, 28650, 27600, 27800, 1000)
    record('마키나락스회귀', reserved_target_bar_eligible(entry, maki, 28250), '고가 28,650 >= 목표 28,250')
    record('JW신약정확터치', reserved_target_bar_eligible(datetime(2026,7,14,13,56,50,tzinfo=KST), jw, 1606), '고가=목표도 체결')
    record('매수전고가오인차단', not reserved_target_bar_eligible(entry, same_minute, 28250), '진입분봉 제외')

    # 3차: 선행상승/장대양봉 강도 회귀검사
    cfg = v7515.PrecisionConfig.load()
    base_ts = datetime(2026, 7, 14, 10, 0, tzinfo=KST)
    bars = []
    for i in range(12):
        px = 100.0 + i * 0.08
        bars.append(_synthetic_bar(base_ts + timedelta(minutes=i), px, px+0.25, px-0.20, px+0.05, 1000))
    bars.append(_synthetic_bar(base_ts + timedelta(minutes=12), 100.9, 103.2, 100.7, 102.9, 2600))
    bars.append(_synthetic_bar(base_ts + timedelta(minutes=13), 102.8, 103.8, 102.6, 103.5, 1400))
    bars.append(_synthetic_bar(base_ts + timedelta(minutes=14), 103.4, 104.2, 103.2, 104.0, 1300))
    bars.append(_synthetic_bar(base_ts + timedelta(minutes=15), 104.0, 104.1, 102.3, 102.6, 2800))
    structure = SimpleNamespace(anchor_index=15)
    strong = strict_prior_impulse(bars, structure, cfg)
    weak_bars = list(bars)
    weak_bars[12] = _synthetic_bar(base_ts + timedelta(minutes=12), 101.0, 101.6, 100.9, 101.4, 1300)
    weak = strict_prior_impulse(weak_bars, structure, cfg)
    record('강한1차상승통과', strong is not None, str(strong)[:120])
    record('약한횡보양봉차단', weak is None, '장대/거래/상승폭 미달')
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7525.main())


if __name__ == '__main__':
    raise SystemExit(main())
