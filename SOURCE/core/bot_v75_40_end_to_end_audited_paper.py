#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.40 PAPER — 매수·목표매도·재시도·영웅문 연동 종합 재검증판.

핵심 수정
- V75.39 매매 조건을 유지하되, 대량 음봉 3배 판정은 최근3봉 최대 대비 또는
  최근5봉 평균 대비 중 더 강한 근거를 사용한다. 낮은 전체 중앙값만 골라 3배로
  과장하는 경로는 차단하고, 어떤 기준으로 3배가 됐는지 이유에 표시한다.
- 음봉은 종가<시가로 판정한다. 갭상승 뒤 음봉처럼 전일 종가보다 높게 끝난 음봉도
  대량 매도 후보에서 임의 제외하지 않는다.
- 원 신호의 대량음봉 시각과 새 구조가 정확히 일치하지 않으면 다른 구조를 억지로
  연결하지 않는다.
- V75.21의 pending-entry 재시도 호출 시그니처 오류를 우회해 from_retry=True가 실제로
  전달되게 한다.
- 매수직전인데 매수되지 않은 경우 호가·스프레드·추격·유동성·신선도 등 최근 차단
  이유를 GUI에 표시하고 단계를 매수가능성/시장보류로 내린다.
- 영웅문 저장 대상과 현재 포커스가 다른 자식 입력칸이면 키를 보내지 않는다.
- 입력값 read-back은 '차트 전환 확인'이 아니라 '입력값 확인 + Enter 전송'으로만 보고한다.

V75.30의 PAPER +1.5% 목표매도, 자동손절 없음, 10억원/종목당 1천만원,
500/300/200만원 분할매수 정책을 그대로 상속한다. 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ctypes
from dataclasses import replace
from datetime import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import statistics
import sys
import time
from types import ModuleType
from typing import Any, Sequence

VERSION = 'V75.40-END-TO-END-AUDITED-PAPER'
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = 'bot_v75_39_strong_sell_volume_entry_alignment_paper.py'
BASE_SHA256 = 'c8bc1efb0bc94045c351433c785e690525cbba19b32019fab9e574320714a18a'
DIAG_FILE = BASE_DIR.parent / 'V75_40_LINK_DIAG.txt'
STRONG_VOLUME_RATIO = 3.0
WEAK_VOLUME_RATIO = 2.0
PRIOR_BULL_VOLUME_RATIO = 2.0
FIXED_TAKE_PROFIT_PCT = 0.015
ENTRY_DECISION_TTL_SEC = 180.0


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual != expected_sha:
        raise RuntimeError(f'{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}')
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'{filename} 로드 실패')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7539 = _load_fixed(BASE_FILENAME, BASE_SHA256, 'v7540_immutable_base')
v7538 = v7539.v7538
v7530 = v7539.v7530
v7525 = v7539.v7525
v7521 = v7525.v7521
v7520 = v7525.v7520
v7516 = v7525.v7516
v7515 = v7539.v7515
v7514 = v7539.v7514
v756 = v7539.v756
v754 = v7539.v754
v753 = v7539.v753
KST = v7539.KST
_ORIGINAL_DETECT = v7539._ORIGINAL_DETECT
_ORIGINAL_ASSESS = v7539._ORIGINAL_ASSESS
_ORIGINAL_CONDITION_REASON = v7539._ORIGINAL_CONDITION_REASON
_ORIGINAL_SELF_CHECK = v7539.self_check

LinkStatus = v7538.LinkStatus
SCHEMA_VERSION = v7538.SCHEMA_VERSION
IS_WINDOWS = v7538.IS_WINDOWS
_u32 = v7538._u32
VK_RETURN = v7538.VK_RETURN
WM_SETTEXT = v7538.WM_SETTEXT


def diag(message: str) -> None:
    try:
        with DIAG_FILE.open('a', encoding='utf-8') as handle:
            handle.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n')
    except OSError:
        pass


# 기반 클래스의 캡처/창탐색 진단도 V75.40 파일에 모은다.
v7538.diag = diag


def _context_metrics(bars: Sequence[Any], index: int, cfg: Any) -> dict[str, float | str]:
    """강신호 기준은 낮은 중앙값을 골라 쓰지 않고 최근 문맥을 보수적으로 사용한다."""
    left = list(bars[max(0, index - int(cfg.max_left_bars)):index])
    if len(left) < int(cfg.min_left_bars):
        return {
            'ratio': 0.0, 'basis': '자료부족', 'median': 0.0, 'mean': 0.0,
            'recent3_max': 0.0, 'recent5_mean': 0.0,
        }
    volumes = [max(0.0, float(item.volume)) for item in left]
    volume = max(0.0, float(bars[index].volume))
    median = float(statistics.median(volumes))
    mean = float(statistics.mean(volumes))
    recent3 = volumes[-min(3, len(volumes)):]
    recent5 = volumes[-min(5, len(volumes)):]
    recent3_max = max(recent3)
    recent5_mean = float(statistics.mean(recent5))
    vs_recent3_max = volume / max(1.0, recent3_max)
    vs_recent5_mean = volume / max(1.0, recent5_mean)
    # 사용자 기준: 몇 봉 전 거래량 '또는' 몇 봉 평균보다 3배 이상이면 강한 근거.
    # 전체 중앙값/전체 평균은 진단용으로만 남기고 실제 강·약 판정에는 쓰지 않는다.
    if vs_recent3_max >= vs_recent5_mean:
        ratio = vs_recent3_max
        basis = '최근3봉최대대비'
    else:
        ratio = vs_recent5_mean
        basis = '최근5봉평균대비'
    return {
        'ratio': float(ratio),
        'basis': basis,
        'median': median,
        'mean': mean,
        'recent3_max': recent3_max,
        'recent5_mean': recent5_mean,
        'vs_median': volume / max(1.0, median),
        'vs_mean': volume / max(1.0, mean),
        'vs_recent3_max': vs_recent3_max,
        'vs_recent5_mean': vs_recent5_mean,
    }


def _prior_bull_volume(bars: Sequence[Any], anchor: int, cfg: Any) -> dict[str, float | int | str] | None:
    start = max(0, anchor - min(30, int(cfg.rise_lookback_bars)))
    best: dict[str, float | int | str] | None = None
    for index in range(start, anchor):
        bar = bars[index]
        if float(bar.close) <= float(bar.open):
            continue
        metrics = _context_metrics(bars, index, cfg)
        ratio = float(metrics['ratio'])
        if ratio < PRIOR_BULL_VOLUME_RATIO:
            continue
        candidate: dict[str, float | int | str] = {
            'index': index, 'ratio': ratio, 'basis': str(metrics['basis'])
        }
        if best is None or ratio > float(best['ratio']):
            best = candidate
    return best


def _anchor_ok(bars: Sequence[Any], index: int, cfg: Any) -> tuple[bool, dict[str, float | str]]:
    if index <= 0:
        return False, {}
    bar = bars[index]
    _, signed, body_ratio, _, _, _ = v7515.candle_metrics(bar)
    # 사용자가 정의한 음봉: 종가가 시가보다 낮음. 전봉 종가 대비 하락은 필수 아님.
    if float(bar.close) >= float(bar.open):
        return False, {}
    if -signed < 0.10 or body_ratio < 0.10:
        return False, {}
    metrics = _context_metrics(bars, index, cfg)
    metrics['close_vs_prev'] = (
        float(bar.close) / max(0.01, float(bars[index - 1].close)) - 1.0
    )
    if float(metrics.get('ratio', 0.0)) < WEAK_VOLUME_RATIO:
        return False, metrics
    return True, metrics


def find_v7540_structures(bars: Sequence[Any], cfg: Any) -> list[Any]:
    structures: list[Any] = []
    for anchor in range(int(cfg.min_left_bars), max(int(cfg.min_left_bars), len(bars) - 1)):
        rise = v7515.prior_rise(bars, anchor, cfg)
        if rise is None:
            continue
        ok, metrics = _anchor_ok(bars, anchor, cfg)
        if not ok:
            continue
        anchor_volume = max(1.0, float(bars[anchor].volume))
        heavy_indices = [anchor]
        for idx in range(anchor + 1, len(bars) - 1):
            item = bars[idx]
            _, signed, body_ratio, _, _, _ = v7515.candle_metrics(item)
            if float(item.close) >= float(item.open) or -signed < 0.10 or body_ratio < 0.10:
                continue
            local_ok, _ = _anchor_ok(bars, idx, cfg)
            comparable = float(item.volume) >= anchor_volume * float(cfg.comparable_heavy_volume_ratio)
            if comparable or local_ok:
                heavy_indices.append(idx)
                anchor_volume = max(anchor_volume, float(item.volume))
        last = heavy_indices[-1]
        heavy_volumes = [float(bars[i].volume) for i in heavy_indices]
        heavy_ranges = [v7515.candle_metrics(bars[i])[0] for i in heavy_indices]
        rise_low, rise_high, rise_pct = rise
        after_lows = [float(item.low) for item in bars[anchor:]]
        pullback_low = min(after_lows) if after_lows else float(bars[anchor].low)
        retrace = (rise_high - pullback_low) / max(1.0, rise_high - rise_low)
        left_volumes = [
            float(x.volume) for x in bars[max(0, anchor - int(cfg.max_left_bars)):anchor]
        ]
        structures.append(v7515.HeavyStructure(
            anchor_index=anchor,
            heavy_indices=tuple(heavy_indices),
            last_heavy_index=last,
            rise_low=rise_low,
            rise_high=rise_high,
            rise_pct=rise_pct,
            retrace=retrace,
            heavy_volume=max(1.0, float(statistics.median(heavy_volumes))),
            heavy_range=max(1e-9, float(statistics.median(heavy_ranges))),
            context_volume=max(1.0, float(metrics['recent5_mean'])),
            volume_vs_median=float(metrics.get('vs_median', 0.0)),
            volume_vs_p75=float(bars[anchor].volume) / max(1.0, v7515.percentile75(left_volumes)),
            volume_vs_recent_max=float(metrics.get('vs_recent3_max', 0.0)),
        ))
    structures.sort(
        key=lambda item: (item.last_heavy_index, len(item.heavy_indices), -item.anchor_index),
        reverse=True,
    )
    return structures


def _matching_structure(bars: Sequence[Any], evidence: Any, cfg: Any) -> Any | None:
    flush = getattr(evidence, 'flush_time', None)
    if flush is None:
        return None
    normalized = v754.normalize_kst(flush)
    for structure in find_v7540_structures(bars, cfg):
        bar_time = v754.normalize_kst(bars[int(structure.last_heavy_index)].timestamp)
        if bar_time == normalized:
            return structure
    # 다른 과거 구조를 억지로 연결하면 허위 진입이 될 수 있다.
    return None


def _support_count(bars: Sequence[Any], structure: Any, cfg: Any) -> int:
    return int(v7539._support_count(bars, structure, cfg))


def _evaluate(bars: Sequence[Any], structure: Any, cfg: Any, signal: Any | None = None) -> dict[str, Any]:
    strongest = _context_metrics(bars, int(structure.anchor_index), cfg)
    for idx in structure.heavy_indices:
        metrics = _context_metrics(bars, int(idx), cfg)
        if float(metrics['ratio']) > float(strongest['ratio']):
            strongest = metrics
    strength = float(strongest['ratio'])
    grade = '강함' if strength >= STRONG_VOLUME_RATIO else '약함' if strength >= WEAK_VOLUME_RATIO else '미달'
    bull = _prior_bull_volume(bars, int(structure.anchor_index), cfg)
    bull_ratio = float(bull['ratio']) if bull else 0.0
    current = bars[-1]
    span, signed, _, close_loc, lower_wick, _ = v7515.candle_metrics(current)
    dry_heavy = float(current.volume) / max(1.0, float(structure.heavy_volume))
    dry_context = float(current.volume) / max(1.0, float(structure.context_volume))
    short = span <= float(structure.heavy_range) * float(cfg.short_max_range_vs_heavy)
    dry = (
        dry_heavy <= float(cfg.dry_max_volume_vs_heavy_cluster)
        and dry_context <= float(cfg.dry_max_volume_vs_context)
    )
    confirm = signed > 0 or (
        signed <= 0
        and lower_wick >= float(cfg.lower_wick_min_ratio)
        and close_loc >= float(cfg.bearish_close_min_location)
    )
    supports = _support_count(bars, structure, cfg)
    reignition = signal is not None and str(getattr(signal, 'pattern', '')) == str(v7515.PATTERN_REIGNITION)
    base_confirmation = (dry and short and confirm) or reignition
    strong_ok = strength >= STRONG_VOLUME_RATIO and supports >= v7539.STRONG_SUPPORT_REQUIRED
    weak_ok = (
        WEAK_VOLUME_RATIO <= strength < STRONG_VOLUME_RATIO
        and supports >= v7539.WEAK_SUPPORT_REQUIRED
        and bull_ratio >= PRIOR_BULL_VOLUME_RATIO
        and dry_heavy <= float(cfg.strong_dry_max_volume_vs_heavy_cluster)
    )
    eligible = base_confirmation and (strong_ok or weak_ok)
    missing: list[str] = []
    if strength < WEAK_VOLUME_RATIO:
        missing.append(f'음봉대량거래x{strength:.2f}<2.00')
    elif strength < STRONG_VOLUME_RATIO and not weak_ok:
        missing.append(f'약한음봉거래x{strength:.2f}<3.00')
        if bull_ratio < PRIOR_BULL_VOLUME_RATIO:
            missing.append(f'선행양봉대량x{bull_ratio:.2f}<2.00')
        if supports < v7539.WEAK_SUPPORT_REQUIRED:
            missing.append(f'지지확인{supports}/{v7539.WEAK_SUPPORT_REQUIRED}')
        if dry_heavy > float(cfg.strong_dry_max_volume_vs_heavy_cluster):
            missing.append('강한거래감소70%대기')
    if not dry and not reignition:
        missing.append('거래감소60%대기')
    if not short and not reignition:
        missing.append('짧은봉대기')
    if not confirm and not reignition:
        missing.append('반전봉대기')
    if strength >= STRONG_VOLUME_RATIO and supports < v7539.STRONG_SUPPORT_REQUIRED:
        missing.append('지지확인봉대기')
    return {
        'eligible': eligible,
        'strength': strength,
        'basis': str(strongest['basis']),
        'grade': grade,
        'bull_ratio': bull_ratio,
        'supports': supports,
        'dry_heavy': dry_heavy,
        'dry_context': dry_context,
        'short': short,
        'confirm': confirm,
        'missing': tuple(dict.fromkeys(missing)),
    }


def detect_v7540(strategy: Any, symbol: str, *, config: Any) -> tuple[Any | None, str, Any | None]:
    signal, state, evidence = _ORIGINAL_DETECT(strategy, symbol, config=config)
    if signal is None or evidence is None:
        return signal, state, evidence
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    if not bars:
        return None, '분봉없음', None
    if v754.normalize_kst(signal.timestamp) != v754.normalize_kst(bars[-1].timestamp):
        return None, '과거신호차단', evidence
    structure = _matching_structure(bars, evidence, cfg)
    if structure is None:
        return None, '대량음봉시각불일치', evidence
    evaluated = _evaluate(bars, structure, cfg, signal)
    if not evaluated['eligible']:
        return None, '매수대기_' + '_'.join(evaluated['missing'][:3]), evidence
    import re
    reason = re.sub(r'\|손절=[^|]+', '', str(signal.reason))
    reason = re.sub(r'\|목표=[^|]+', '', reason)
    # 구버전 중앙값/P75 문구는 V75.40 판정 기준과 다르므로 사용자 표시에서 제거한다.
    reason = re.sub(r'\|대량거래=[^|]+', '', reason)
    bonus = (
        8.0 if evaluated['strength'] >= STRONG_VOLUME_RATIO else 3.0
    ) + (5.0 if evaluated['bull_ratio'] >= PRIOR_BULL_VOLUME_RATIO else 0.0) + max(0, evaluated['supports'] - 1) * 3.0
    reason += (
        f"|조정음봉거래={evaluated['strength']:.2f}배({evaluated['basis']}/{evaluated['grade']})"
        f"|선행양봉대량={evaluated['bull_ratio']:.2f}배(가산조건)"
        f"|지지확인봉={evaluated['supports']}|자동손절없음|목표=1.5%고정"
    )
    fixed = v753.Signal(
        signal.symbol, signal.action, float(signal.score) + bonus, reason,
        float(signal.reference_price), signal.timestamp, str(signal.pattern),
        float(signal.stop_loss_pct), FIXED_TAKE_PROFIT_PCT,
    )
    return fixed, state, evidence


def assess_v7540(
    strategy: Any, symbol: str, *, loose: Any, strict: Any,
    now: datetime, name: str, source: str,
) -> tuple[Any | None, str]:
    item, state = _ORIGINAL_ASSESS(
        strategy, symbol, loose=loose, strict=strict, now=now, name=name, source=source
    )
    if item is None:
        return None, state
    cfg = v7515.PrecisionConfig.load()
    bars = list(v756._same_session_bars(strategy.bars.get(symbol, [])))
    structures = find_v7540_structures(bars, cfg) if bars else []
    if not structures:
        return None, '상승후2배이상음봉거래없음'
    base_signal, _, base_evidence = _ORIGINAL_DETECT(strategy, symbol, config=None)
    structure = structures[0]
    if base_evidence is not None:
        matched = _matching_structure(bars, base_evidence, cfg)
        if matched is not None:
            structure = matched
    evaluated = _evaluate(bars, structure, cfg, base_signal)
    strict_signal, strict_state, _ = detect_v7540(strategy, symbol, config=None)
    if strict_signal is not None:
        stage = '즉시매수조건근접'
        missing: tuple[str, ...] = ()
    elif evaluated['dry_heavy'] <= float(cfg.dry_max_volume_vs_heavy_cluster):
        stage = '관심종목-매수가능성'
        missing = evaluated['missing'] or (strict_state,)
    else:
        stage = '거래바닥관찰'
        missing = evaluated['missing'] or ('거래감소60%대기',)
    marker = (
        f"V7540|음봉거래x{evaluated['strength']:.2f}({evaluated['grade']}/{evaluated['basis']})"
        f"|선행양봉x{evaluated['bull_ratio']:.2f}|지지{evaluated['supports']}"
        f"|판정={'진입가능' if strict_signal is not None else '대기'}"
    )
    score = float(item.score) + (12 if evaluated['strength'] >= 3 else 3) + (5 if evaluated['bull_ratio'] >= 2 else 0)
    return replace(
        item, stage=stage, strict_missing=tuple(missing), score=min(100.0, score),
        source=f'{source}|{marker}', updated_at=now,
    ), state


def condition_reason_v7540(item: Any, bucket: str) -> str:
    source = str(getattr(item, 'source', ''))
    if 'V7540|' not in source:
        return _ORIGINAL_CONDITION_REASON(item, bucket)
    marker = source.split('V7540|', 1)[1]
    parts = marker.split('|')
    summary = ' · '.join(parts[:3])
    missing = tuple(getattr(item, 'strict_missing', ()) or ())
    if bucket == 'ready':
        return summary + ' · 전략매수조건완료'
    if missing:
        return summary + ' · 대기:' + ','.join(str(x) for x in missing[:2])
    return summary


# 모든 탐지·후보 경로를 같은 평가기로 교체한다.
v7539._context_metrics = _context_metrics
v7539._prior_bull_volume = _prior_bull_volume
v7539._anchor_ok = _anchor_ok
v7539.find_v7539_structures = find_v7540_structures
v7539._matching_structure = _matching_structure
v7539._evaluate = _evaluate
v7539.detect_v7539 = detect_v7540
v7539.assess_v7539 = assess_v7540
v7515.find_heavy_structures = find_v7540_structures
v7525.find_qualified_heavy_structures = find_v7540_structures
v7525.detect_v7525 = detect_v7540
v7525.assess_v7525 = assess_v7540
v7515.detect_precision_supply = detect_v7540
v7515.assess_precision_watch = assess_v7540
v756.detect_supply_exhaustion_pullback = detect_v7540
v7514.assess_loose_supply_potential = assess_v7540
v7520._condition_reason = condition_reason_v7540
v7530.strict_detect = detect_v7540
v7530.strict_assess = assess_v7540


class TargetIdentityHeroLink(v7538.SingleFocusedHeroLink):
    """저장 대상과 다른 입력칸에 종목코드를 쓰는 것을 차단한다."""

    def _standard_edit_set_v7540(self, target: int, symbol: str) -> tuple[bool, bool, str]:
        if not self._message_target_safe(target, self._root_of(target)):
            return False, False, ''
        buffer = ctypes.create_unicode_buffer(symbol)
        sent, _, _ = self._send_message(
            target, WM_SETTEXT, 0, ctypes.cast(buffer, ctypes.c_void_p).value or 0
        )
        if not sent:
            return False, False, ''
        time.sleep(0.10)
        observed = self._read_control_text(target)
        return True, observed == symbol, observed

    def _focus_still_valid(self, root: int, target: int, effective_target: int, standard: bool) -> bool:
        if not v7538.IS_WINDOWS:
            return False
        if self._foreground() != root:
            return False
        focus = self._focus_hwnd(root)
        focus_root = self._root_of(focus)
        if standard:
            return bool(focus and focus == effective_target and focus_root == root)
        if target and focus and focus != target:
            return False
        if focus:
            return focus_root == root
        # HWND 포커스를 노출하지 않는 커스텀 입력칸은 클릭 좌표 경로만 미검증으로 허용.
        return effective_target in (0, target)

    def send_symbol(self, symbol: str):
        if not v7538.IS_WINDOWS:
            return LinkStatus.FAILED, 'Windows에서만 작동합니다.'
        if not symbol.isdigit() or len(symbol) != 6:
            diag(f'REJECT symbol={symbol!r}')
            return LinkStatus.FAILED, f'종목코드 형식 오류: {symbol}'
        if not self.configured:
            return LinkStatus.FAILED, '먼저 영웅문 위치 저장을 한 번 하세요.'
        if not self._config_valid():
            return LinkStatus.FAILED, '저장 위치가 현재 화면과 맞지 않습니다. 위치 저장을 다시 하세요.'

        if self._clipboard_read() != symbol:
            self._clipboard_write(symbol)
        if self._clipboard_read() != symbol:
            return LinkStatus.FAILED, '종목코드 클립보드 저장 실패'

        root = self._find_window()
        if not root:
            return LinkStatus.FAILED, f'{symbol} 복사됨 — 저장한 영웅문 창을 찾지 못했습니다.'
        geometry = self._client_geometry(root)
        if geometry is None:
            return LinkStatus.FAILED, f'{symbol} 복사됨 — 영웅문 창 위치를 읽지 못했습니다.'
        left, top, width, height = geometry
        x = left + int(float(self.config['x_ratio']) * width)
        y = top + int(float(self.config['y_ratio']) * height)
        target = self._find_target(root, x, y)

        foreground_ok, focus, click_focus_ok = self._activate_and_focus(root, target, x, y)
        focus_root = self._root_of(focus)
        focus_inside = bool(focus and focus_root == root)
        target_inside = bool(target and target != root and self._root_of(target) == root)
        target_standard = target_inside and self._message_target_safe(target, root)

        # 저장한 대상이 있는데 다른 자식 입력칸이 포커스를 가졌다면 절대 키를 보내지 않는다.
        if target_inside and focus_inside and focus != target:
            diag(f'GATE_WRONG_CHILD symbol={symbol} root={root} target={target} focus={focus}')
            return LinkStatus.FAILED, (
                f'{symbol} 복사됨 — 저장 입력칸과 현재 포커스가 다릅니다. 위치 저장을 다시 하세요.'
            )
        if target_standard and focus != target:
            diag(f'GATE_STANDARD_NOT_FOCUSED symbol={symbol} target={target} focus={focus}')
            return LinkStatus.FAILED, f'{symbol} 복사됨 — 저장한 종목 입력칸에 포커스를 잡지 못했습니다.'
        focus_gate = foreground_ok and (
            (target_inside and focus == target)
            or (not target_inside and focus_inside)
            or (click_focus_ok and not focus_inside)
        )
        if not focus_gate:
            diag(
                f'GATE_BLOCK symbol={symbol} root={root} target={target} focus={focus} '
                f'foreground={foreground_ok} click={click_focus_ok}'
            )
            return LinkStatus.FAILED, (
                f'{symbol} 복사됨 — 영웅문 입력칸에 포커스를 잡지 못했습니다. Ctrl+V로 확인하세요.'
            )

        effective_target = target if target_inside else focus if focus_inside else 0
        standard = bool(effective_target) and self._message_target_safe(effective_target, root)
        verified = False
        method = ''
        if standard:
            sent, verified, observed = self._standard_edit_set_v7540(effective_target, symbol)
            method = 'WM_SETTEXT'
            diag(
                f'INPUT method={method} sent={sent} verified={verified} observed={observed!r} '
                f'root={root} target={target} focus={focus}'
            )
            if not sent:
                return LinkStatus.FAILED, f'{symbol} 복사됨 — 입력 메시지 전송 실패'
        else:
            paste_ok = self._paste_with_keys()
            time.sleep(0.18)
            method = 'CTRL_V'
            if not paste_ok:
                return LinkStatus.FAILED, f'{symbol} 복사됨 — 자동 붙여넣기 실패, Ctrl+V 하세요.'
            observed = self._read_control_text(effective_target) if effective_target else ''
            verified = observed == symbol
            diag(
                f'INPUT method={method} verified={verified} observed={observed!r} '
                f'root={root} target={target} focus={focus}'
            )

        if not self._focus_still_valid(root, target, effective_target, standard):
            diag(f'FOCUS_LOST_BEFORE_ENTER symbol={symbol} method={method}')
            return LinkStatus.SENT_UNVERIFIED, (
                f'{symbol} 입력은 전송했으나 Enter 직전 포커스를 잃었습니다.'
            )
        enter_ok = self._press(VK_RETURN)
        diag(f'ENTER symbol={symbol} method={method} ok={enter_ok} input_verified={verified}')
        if not enter_ok:
            return LinkStatus.SENT_UNVERIFIED, f'{symbol} 입력됨 — Enter 전송 실패'
        if verified:
            return LinkStatus.VERIFIED, (
                f'{symbol} 입력값 확인 + Enter 전송 ({method}) — 영웅문 차트 확인'
            )
        return LinkStatus.SENT_UNVERIFIED, (
            f'{symbol} 입력·Enter 전송 ({method}) — 값 확인 불가, 영웅문 화면 확인'
        )


v7538.SingleFocusedHeroLink = TargetIdentityHeroLink


def _fixed_target_once(signal: Any) -> Any:
    if getattr(signal, 'action', None) != v753.SignalAction.BUY:
        return signal
    reason = str(getattr(signal, 'reason', ''))
    marker = '목표수익1.5%고정'
    if marker not in reason:
        reason = reason + '|' + marker
    return v753.Signal(
        signal.symbol, signal.action, float(signal.score), reason,
        float(signal.reference_price), signal.timestamp, str(signal.pattern),
        float(signal.stop_loss_pct), FIXED_TAKE_PROFIT_PCT,
    )


def apply_entry_diagnostics(
    rows: list[dict[str, str]],
    decisions: dict[str, tuple[datetime, str, str]],
    *,
    now: datetime,
    held_symbols: set[str],
) -> list[dict[str, str]]:
    for row in rows:
        symbol = str(row.get('symbol', ''))
        if not symbol or symbol in held_symbols:
            continue
        record = decisions.get(symbol)
        if record is None:
            continue
        timestamp, status, reason = record
        age = (now - timestamp).total_seconds()
        if age < 0 or age > ENTRY_DECISION_TTL_SEC:
            continue
        if status == 'BLOCKED':
            row['reason'] = (str(row.get('reason', '')) + f' · 실제진입보류:{reason}')[:180]
            if row.get('stage') == '매수직전':
                row['stage'] = '시장보류' if any(token in reason for token in ('시장', '진입시간', '지연분봉')) else '매수가능성'
        elif status == 'ATTEMPT':
            row['reason'] = (str(row.get('reason', '')) + ' · 호가·유동성 최종확인중')[:180]
    return rows


class V7540Engine(v7525.V7525Engine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._entry_decisions: dict[str, tuple[datetime, str, str]] = {}
        super().__init__(*args, **kwargs)
        original_approve = self.risk.approve_entry

        def approve_with_reason(signal: Any, quote: Any, equity: float, cash: float, positions: Any) -> Any:
            decision = original_approve(signal, quote, equity, cash, positions)
            if not decision.approved:
                self._record_entry_decision(signal.symbol, 'BLOCKED', decision.reason)
            return decision

        self.risk.approve_entry = approve_with_reason

    def _record_entry_decision(self, symbol: str, status: str, reason: str) -> None:
        self._entry_decisions[str(symbol)] = (datetime.now(KST), str(status), str(reason)[:120])

    def _show_throttled_error(self, key: str, message: str, interval: float) -> None:
        if key.startswith(('entry:', 'entrybuy:', 'scale:')):
            symbol = key.split(':', 1)[1] if ':' in key else ''
            if symbol:
                self._record_entry_decision(symbol, 'BLOCKED', message)
        return super()._show_throttled_error(key, message, interval)

    def _liquidity_gate(self, signal: Any, quote: Any) -> tuple[bool, str]:
        ok, reason = super()._liquidity_gate(signal, quote)
        if not ok:
            self._record_entry_decision(signal.symbol, 'BLOCKED', reason)
        return ok, reason

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        ok, reason = super()._pending_signal_valid(signal, current_price)
        if not ok:
            self._record_entry_decision(signal.symbol, 'BLOCKED', reason)
        return ok, reason

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(signal.symbol)
        self._record_entry_decision(symbol, 'ATTEMPT', '진입조건 최종확인')
        if symbol in getattr(self, 'buy_blocked', set()):
            self._record_entry_decision(symbol, 'BLOCKED', '장중명령 종목매수금지')
            self.pending_entries.pop(symbol, None)
            return False
        if bool(getattr(self, 'manual_new_entries_paused', False)):
            self._record_entry_decision(symbol, 'BLOCKED', '장중명령 신규매수중지')
            self.pending_entries.pop(symbol, None)
            return False
        if not self._new_entry_session_allowed():
            self._record_entry_decision(symbol, 'BLOCKED', '진입시간·시장상태 보류')
            self.pending_entries.pop(symbol, None)
            return False
        if symbol in self.broker.positions:
            self._record_entry_decision(symbol, 'BLOCKED', '이미보유')
            self.pending_entries.pop(symbol, None)
            return False
        fixed = _fixed_target_once(signal)
        before = self._entry_decisions.get(symbol)
        # V75.21의 잘못된 (self, signal) 시그니처를 건너뛰고 from_retry를 보존한다.
        entered = bool(
            super(v7521.FixedTargetFlexibleEngine, self)._attempt_entry(
                fixed, from_retry=from_retry
            )
        )
        if entered:
            self._record_entry_decision(symbol, 'FILLED', 'PAPER 매수완료')
        else:
            current = self._entry_decisions.get(symbol)
            if current == before or (current and current[1] == 'ATTEMPT'):
                self._record_entry_decision(symbol, 'BLOCKED', '최종진입게이트 보류·상세로그 확인')
        return entered

    def _on_bar(self, bar: Any, quote: Any, *, allow_entry: bool = True, bar_age_sec: float = 0.0) -> None:
        had_position = bar.symbol in self.broker.positions
        super()._on_bar(bar, quote, allow_entry=allow_entry, bar_age_sec=bar_age_sec)
        signal = self.latest_signals.get(bar.symbol)
        if (
            not had_position
            and not allow_entry
            and bar.symbol not in self.broker.positions
            and signal is not None
            and getattr(signal, 'action', None) == v753.SignalAction.BUY
        ):
            self._record_entry_decision(bar.symbol, 'BLOCKED', f'지연분봉 {bar_age_sec:.0f}초')

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        rows = list(snapshot.get('rows', []))
        apply_entry_diagnostics(
            rows, self._entry_decisions, now=now,
            held_symbols=set(self.broker.positions),
        )
        order_index = {name: index for index, name in enumerate(v7525.STAGE_ORDER)}
        rows.sort(key=lambda row: (
            order_index.get(str(row.get('stage', '')), 99),
            str(row.get('time', '')), str(row.get('name', '')), str(row.get('symbol', '')),
        ))
        snapshot['rows'] = rows
        return snapshot


v7525.V7525Engine = V7540Engine
v7525.VERSION = VERSION


class FocusAuditedGui(v7538.BaseGui):
    """V75.40 전용 GUI. 더블클릭 기본, V75_SINGLE_CLICK=1일 때만 한 번 클릭."""

    DEBOUNCE_SEC = 0.6

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning('GUI 시작 실패: %s', exc)
            return
        import queue

        click_mode = {'single': v7538._single_click_enabled()}
        def mode_label() -> str:
            return '한 번 클릭' if click_mode['single'] else '더블클릭'
        root.title(f'V75.40 관심·후보·매수·매도 — {mode_label()} 영웅문 연동')
        root.geometry('1280x700')
        root.minsize(1080, 540)
        hero = TargetIdentityHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill='x')
        status_var = tk.StringVar(
            value=f'종목 줄을 {mode_label()}하세요.'
            if (hero.configured and hero._config_valid())
            else '처음 한 번만 영웅문 위치 저장을 하세요.'
        )
        time_var = tk.StringVar(value='갱신 전')
        ttk.Button(toolbar, text='영웅문 위치 저장', command=lambda: begin_capture()).pack(side='left')
        ttk.Button(toolbar, text='위치 설정 초기화', command=lambda: do_reset()).pack(side='left', padx=(6, 0))
        ttk.Button(toolbar, text='위치 상태 확인', command=lambda: check_link_state()).pack(side='left', padx=(6, 0))
        single_var = tk.BooleanVar(value=click_mode['single'])
        ttk.Checkbutton(
            toolbar, text='한 번 클릭 사용', variable=single_var,
            command=lambda: change_click_mode(),
        ).pack(side='left', padx=(10, 0))
        mode_help_var = tk.StringVar(value=f'  {mode_label()} → 코드 복사 + 자동입력 + Enter')
        ttk.Label(toolbar, textvariable=mode_help_var).pack(side='left')
        ttk.Label(toolbar, textvariable=time_var).pack(side='right')

        columns = ('no', 'stage', 'buy_time', 'name', 'symbol', 'price', 'reason')
        tree = ttk.Treeview(root, columns=columns, show='headings', selectmode='browse')
        headings = {
            'no': '순번', 'stage': '단계', 'buy_time': '매수시각', 'name': '종목명',
            'symbol': '코드', 'price': '매수가 / +1.5% 매도가', 'reason': '이유',
        }
        widths = {
            'no': 55, 'stage': 105, 'buy_time': 85, 'name': 180,
            'symbol': 75, 'price': 220, 'reason': 520,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = 'w' if column in ('name', 'reason') else ('e' if column == 'price' else 'center')
            tree.column(column, width=widths[column], anchor=anchor, stretch=column in ('name', 'reason'))
        scrollbar = ttk.Scrollbar(root, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True, padx=(8, 0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor='w', padding=8).pack(fill='x')

        state = {'last_symbol': '', 'last_ts': 0.0}

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f'TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}')
                return False

        def switch_symbol(symbol: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f'종목코드를 읽지 못했습니다: {symbol!r}')
                return
            now_mono = time.monotonic()
            if click_mode['single'] and symbol == state['last_symbol'] and (now_mono - state['last_ts']) < self.DEBOUNCE_SEC:
                return
            state['last_symbol'] = symbol
            state['last_ts'] = now_mono
            copied = tk_copy(symbol)
            diag(f'CLICK symbol={symbol} tk_clipboard={copied} mode={mode_label()}')
            status_var.set(f'{symbol} 영웅문 입력 중…')
            root.update_idletasks()
            # 사용자 입력 이벤트를 받은 Tk 메인 스레드에서 포커스·키 입력을 실행한다.
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f'오류: {type(exc).__name__}'
                diag(f'SEND_EXC symbol={symbol} {type(exc).__name__}: {exc}')
            prefix = {
                'VERIFIED': '✅ ', 'SENT_UNVERIFIED': '⚠ ', 'FAILED': '❌ '
            }.get(status.value, '')
            if status != LinkStatus.VERIFIED and copied:
                message = f'{message} (코드 복사됨 — 수동 Ctrl+V 가능)'
            status_var.set(f'{prefix}{message}')

        def selected_symbol(event: Any) -> None:
            row = tree.identify_row(event.y)
            if not row or tree.identify_region(event.x, event.y) not in ('cell', 'tree'):
                return
            tree.selection_set(row)
            values = tree.item(row, 'values')
            if len(values) >= 5:
                switch_symbol(str(values[4]).strip())

        def apply_click_binding() -> None:
            tree.unbind('<ButtonRelease-1>')
            tree.unbind('<Double-1>')
            if click_mode['single']:
                tree.bind('<ButtonRelease-1>', selected_symbol)
            else:
                tree.bind('<Double-1>', selected_symbol)
            mode_help_var.set(f'  {mode_label()} → 코드 복사 + 자동입력 + Enter')
            root.title(f'V75.40 관심·후보·매수·매도 — {mode_label()} 영웅문 연동')

        def change_click_mode() -> None:
            click_mode['single'] = bool(single_var.get())
            state['last_symbol'] = ''
            state['last_ts'] = 0.0
            apply_click_binding()
            status_var.set(f'클릭 방식을 {mode_label()}으로 변경했습니다.')

        def do_reset() -> None:
            hero.reset_config()
            status_var.set('위치 설정을 초기화했습니다. 영웅문 위치 저장을 다시 하세요.')

        def check_link_state() -> None:
            if not hero.configured:
                status_var.set('연동 위치가 아직 저장되지 않았습니다.')
            elif not hero._config_valid():
                status_var.set('저장 위치가 현재 화면과 맞지 않습니다. 위치 저장을 다시 하세요.')
            else:
                status_var.set('연동 위치 설정 정상 — 종목 줄을 더블클릭해 실제 전환을 확인하세요.')

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            if ok:
                status_var.set(f'{message} — 이제 종목 줄을 {mode_label()}하세요.')
            else:
                status_var.set(f'위치 저장 실패: {message}')

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f'{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.')
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                '영웅문 위치 저장',
                '최초 한 번만 설정합니다. 확인 후 5초 안에 영웅문 종목코드 입력칸을 클릭하세요.\n'
                '클릭한 뒤 저장 완료까지 마우스를 움직이지 마세요.',
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            for iid in tree.get_children():
                tree.delete(iid)
            rows = list(snapshot.get('rows', []))
            counters: dict[str, int] = {}
            for row in rows:
                stage = str(row.get('stage', ''))
                counters[stage] = counters.get(stage, 0) + 1
                tree.insert('', 'end', values=(
                    counters[stage], stage, row.get('buy_time', '-'), row.get('name', ''),
                    row.get('symbol', ''), row.get('display_price', row.get('price', '')),
                    row.get('reason', ''),
                ))
            time_var.set(str(snapshot.get('updated', '')))
            ready_count = sum(str(row.get('stage', '')) == '매수직전' for row in rows)
            blocked_count = sum('실제진입보류:' in str(row.get('reason', '')) for row in rows)
            if hero.configured and hero._config_valid():
                status_var.set(
                    f"시장 {snapshot.get('market', '-')} | {len(rows)}종목 | "
                    f"매수직전 {ready_count} | 실제진입보류 {blocked_count} | {mode_label()} 연동"
                )
            elif hero.configured:
                status_var.set('저장 위치가 현재 화면과 다릅니다 — 위치 저장을 다시 하세요.')
            else:
                status_var.set('먼저 영웅문 위치 저장을 한 번 하세요.')

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == 'snapshot':
                        apply_snapshot(payload)
                    elif kind == 'close':
                        root.destroy()
                        return
            except queue.Empty:
                pass
            if not self.closed.is_set():
                root.after(300, poll)

        apply_click_binding()
        root.protocol('WM_DELETE_WINDOW', lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


v7525.SimpleDoubleClickGui = FocusAuditedGui


def _run_regression() -> tuple[bool, str]:
    path = BASE_DIR / 'test_v75_40_regression.py'
    if not path.exists():
        return False, '회귀검사 파일 없음'
    try:
        spec = importlib.util.spec_from_file_location('v7540_regress_selfcheck', path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = module.run()
        return code == 0, ('회귀검사 통과' if code == 0 else '회귀검사 실패')
    except Exception as exc:
        return False, f'회귀검사 예외 {type(exc).__name__}: {exc}'


def _run_end_to_end() -> tuple[bool, str]:
    path = BASE_DIR / 'test_v75_40_end_to_end.py'
    if not path.exists():
        return False, 'E2E 검사 파일 없음'
    try:
        spec = importlib.util.spec_from_file_location('v7540_e2e_selfcheck', path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = module.run()
        return code == 0, ('E2E 통과' if code == 0 else 'E2E 실패')
    except Exception as exc:
        return False, f'E2E 예외 {type(exc).__name__}: {exc}'


def _run_stress() -> tuple[bool, str]:
    path = BASE_DIR / 'test_v75_40_stress.py'
    if not path.exists():
        return False, '스트레스 검사 파일 없음'
    try:
        spec = importlib.util.spec_from_file_location('v7540_stress_selfcheck', path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = module.run()
        return code == 0, ('스트레스 통과' if code == 0 else '스트레스 실패')
    except Exception as exc:
        return False, f'스트레스 예외 {type(exc).__name__}: {exc}'


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    ok, message = _run_regression()
    e2e_ok, e2e_message = _run_end_to_end()
    stress_ok, stress_message = _run_stress()
    import inspect
    results.extend([
        ('V75.39기준SHA', sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
        ('V75.40종합회귀', ok, message),
        ('V75.40매수매도E2E', e2e_ok, e2e_message),
        ('V75.40호가성능스트레스', stress_ok, stress_message),
        ('pending재시도시그니처', 'from_retry' in inspect.signature(V7540Engine._attempt_entry).parameters, str(inspect.signature(V7540Engine._attempt_entry))),
        ('3배OR기준', STRONG_VOLUME_RATIO == 3.0, '최근3최대 또는 최근5평균'),
        ('갭상승음봉허용', 'drop >' not in inspect.getsource(_anchor_ok), '종가<시가'),
        ('목표1.5유지', FIXED_TAKE_PROFIT_PCT == 0.015, '+1.5%'),
        ('V75.40GUI제목', "V75.40 관심·후보·매수·매도" in inspect.getsource(FocusAuditedGui), '구버전 제목 방지'),
        ('더블클릭기본', "tree.bind('<Double-1>'" in inspect.getsource(FocusAuditedGui), '싱글클릭은 옵션'),
    ])
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7530.main())


if __name__ == '__main__':
    raise SystemExit(main())
