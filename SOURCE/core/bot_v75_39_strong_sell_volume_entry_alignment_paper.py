#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.39 PAPER — 3배 대량음봉·최종매수단계 정합성 수정.

- V75.38 영웅문 연동을 그대로 사용한다.
- V75.30 PAPER 목표 +1.5%, 자동손절 없음, 자금관리 로직은 유지한다.
- 선행 대량 양봉은 장대양봉 필수가 아니라 신뢰 가산 조건이다.
- 조정 음봉 거래량은 최근 문맥 대비 3배 이상을 강한 신호로 본다.
- 2~3배는 약한 신호이며 추가 지지와 선행 대량 양봉이 있어야 진입한다.
- GUI의 매수직전 단계와 실제 최종 매수판정을 동일한 평가기로 계산한다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time as dt_time
import hashlib
import importlib.util
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

VERSION = 'V75.39-STRONG-SELL-VOLUME-ENTRY-ALIGNMENT-PAPER'
BASE_DIR = Path(__file__).resolve().parent
LINK_FILENAME = 'bot_v75_38_focus_gated_hero_link_paper.py'
LINK_SHA256 = '9ef24f1b123b1ca8eb2871662c47e74d00c94d66228f8ead836004deed5eea04'
FIXED_TAKE_PROFIT_PCT = 0.015
WEAK_VOLUME_RATIO = 2.0
STRONG_VOLUME_RATIO = 3.0
PRIOR_BULL_VOLUME_RATIO = 2.0
STRONG_SUPPORT_REQUIRED = 1
WEAK_SUPPORT_REQUIRED = 2
SUPPORT_LOOKBACK = 8


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_base() -> ModuleType:
    path = BASE_DIR / LINK_FILENAME
    actual = sha256_file(path)
    if actual != LINK_SHA256:
        raise RuntimeError(f'V75.38 SHA256 불일치\n기대: {LINK_SHA256}\n실제: {actual}')
    spec = importlib.util.spec_from_file_location('v7538_immutable_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('V75.38 로드 실패')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


v7538 = load_base()
v7536 = v7538.v7536
v7530 = v7538.v7530
v7529 = v7530.v7529
v7525 = v7530.v7525
v7520 = v7525.v7520
v7515 = v7530.v7515
v7514 = v7530.v7514
v756 = v7530.v756
v754 = v7530.v754
v753 = v7530.v753
KST = v7530.KST
_ORIGINAL_SELF_CHECK = v7538.self_check
_ORIGINAL_ASSESS = v7525._ORIGINAL_ASSESS
_ORIGINAL_DETECT = v7525._ORIGINAL_DETECT
_ORIGINAL_CONDITION_REASON = v7520._condition_reason


def _context_metrics(bars: Sequence[Any], index: int, cfg: Any) -> dict[str, float | str]:
    left = list(bars[max(0, index-int(cfg.max_left_bars)):index])
    if len(left) < int(cfg.min_left_bars):
        return {'ratio': 0.0, 'basis': '자료부족', 'median': 0.0, 'mean': 0.0}
    volumes = [max(0.0, float(item.volume)) for item in left]
    volume = max(0.0, float(bars[index].volume))
    median = float(statistics.median(volumes))
    mean = float(statistics.mean(volumes))
    recent3 = volumes[-min(3, len(volumes)):]
    recent5 = volumes[-min(5, len(volumes)):]
    refs = {
        '중앙': volume / max(1.0, median),
        '평균': volume / max(1.0, mean),
        '최근3최대': volume / max(1.0, max(recent3)),
        '최근5평균': volume / max(1.0, float(statistics.mean(recent5))),
    }
    basis, ratio = max(refs.items(), key=lambda item: item[1])
    return {'ratio': float(ratio), 'basis': basis, 'median': median, 'mean': mean,
            'vs_median': refs['중앙'], 'vs_mean': refs['평균']}


def _prior_bull_volume(bars: Sequence[Any], anchor: int, cfg: Any) -> dict[str, float | int | str] | None:
    start = max(0, anchor - min(30, int(cfg.rise_lookback_bars)))
    best = None
    for index in range(start, anchor):
        bar = bars[index]
        if float(bar.close) <= float(bar.open):
            continue
        metrics = _context_metrics(bars, index, cfg)
        ratio = float(metrics['ratio'])
        if ratio < PRIOR_BULL_VOLUME_RATIO:
            continue
        candidate = {'index': index, 'ratio': ratio, 'basis': str(metrics['basis'])}
        if best is None or ratio > float(best['ratio']):
            best = candidate
    return best


def _anchor_ok(bars: Sequence[Any], index: int, cfg: Any) -> tuple[bool, dict[str, float | str]]:
    if index <= 0:
        return False, {}
    bar = bars[index]
    span, signed, body_ratio, _, _, _ = v7515.candle_metrics(bar)
    if float(bar.close) >= float(bar.open):
        return False, {}
    # 장대음봉뿐 아니라 일반 음봉의 대량거래도 허용하되, 도지성 잡음은 제외한다.
    if -signed < 0.10 or body_ratio < 0.10:
        return False, {}
    drop = float(bar.close) / max(0.01, float(bars[index-1].close)) - 1.0
    if drop > -0.001:
        return False, {}
    metrics = _context_metrics(bars, index, cfg)
    if float(metrics.get('ratio', 0.0)) < WEAK_VOLUME_RATIO:
        return False, metrics
    return True, metrics


def find_v7539_structures(bars: Sequence[Any], cfg: Any) -> list[Any]:
    structures = []
    for anchor in range(int(cfg.min_left_bars), max(int(cfg.min_left_bars), len(bars)-1)):
        rise = v7515.prior_rise(bars, anchor, cfg)
        if rise is None:
            continue
        ok, metrics = _anchor_ok(bars, anchor, cfg)
        if not ok:
            continue
        anchor_volume = max(1.0, float(bars[anchor].volume))
        heavy_indices = [anchor]
        for idx in range(anchor+1, len(bars)-1):
            item = bars[idx]
            _, signed, body_ratio, _, _, _ = v7515.candle_metrics(item)
            if float(item.close) >= float(item.open) or -signed < 0.10 or body_ratio < 0.10:
                continue
            local_ok, local_metrics = _anchor_ok(bars, idx, cfg)
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
        retrace = (rise_high-pullback_low)/max(1.0, rise_high-rise_low)
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
            context_volume=max(1.0, float(metrics['median'])),
            volume_vs_median=float(metrics.get('vs_median', 0.0)),
            volume_vs_p75=float(bars[anchor].volume)/max(1.0, v7515.percentile75([float(x.volume) for x in bars[max(0,anchor-int(cfg.max_left_bars)):anchor]])),
            volume_vs_recent_max=float(bars[anchor].volume)/max(1.0, max(float(x.volume) for x in bars[max(0,anchor-3):anchor])),
        ))
    structures.sort(key=lambda item: (item.last_heavy_index, len(item.heavy_indices), -item.anchor_index), reverse=True)
    return structures


def _matching_structure(bars: Sequence[Any], evidence: Any, cfg: Any) -> Any | None:
    flush = getattr(evidence, 'flush_time', None)
    if flush is not None:
        flush = v754.normalize_kst(flush)
    structures = find_v7539_structures(bars, cfg)
    for structure in structures:
        bar_time = v754.normalize_kst(bars[int(structure.last_heavy_index)].timestamp)
        if flush is not None and bar_time == flush:
            return structure
    return structures[0] if structures else None


def _support_count(bars: Sequence[Any], structure: Any, cfg: Any) -> int:
    start = int(structure.last_heavy_index)+1
    post = list(bars[start:])[-SUPPORT_LOOKBACK:]
    count=0
    for bar in post:
        span,signed,_,close_loc,lower_wick,_=v7515.candle_metrics(bar)
        dry=(float(bar.volume)<=float(structure.heavy_volume)*float(cfg.dry_max_volume_vs_heavy_cluster)
             and float(bar.volume)<=float(structure.context_volume)*float(cfg.dry_max_volume_vs_context))
        short=span<=float(structure.heavy_range)*float(cfg.short_max_range_vs_heavy)
        confirm=signed>0 or (signed<=0 and lower_wick>=float(cfg.lower_wick_min_ratio)
                            and close_loc>=float(cfg.bearish_close_min_location))
        if dry and short and confirm:
            count+=1
    return count


def _evaluate(bars: Sequence[Any], structure: Any, cfg: Any, signal: Any | None=None) -> dict[str, Any]:
    anchor_strength = _context_metrics(bars, int(structure.anchor_index), cfg)
    # 매도 묶음 중 가장 강한 음봉을 신뢰 기준으로 사용한다.
    for idx in structure.heavy_indices:
        m=_context_metrics(bars,int(idx),cfg)
        if float(m['ratio'])>float(anchor_strength['ratio']): anchor_strength=m
    strength=float(anchor_strength['ratio'])
    grade='강함' if strength>=STRONG_VOLUME_RATIO else '약함' if strength>=WEAK_VOLUME_RATIO else '미달'
    bull=_prior_bull_volume(bars,int(structure.anchor_index),cfg)
    bull_ratio=float(bull['ratio']) if bull else 0.0
    current=bars[-1]
    span,signed,_,close_loc,lower_wick,_=v7515.candle_metrics(current)
    dry_heavy=float(current.volume)/max(1.0,float(structure.heavy_volume))
    dry_context=float(current.volume)/max(1.0,float(structure.context_volume))
    short=span<=float(structure.heavy_range)*float(cfg.short_max_range_vs_heavy)
    dry=(dry_heavy<=float(cfg.dry_max_volume_vs_heavy_cluster)
         and dry_context<=float(cfg.dry_max_volume_vs_context))
    confirm=signed>0 or (signed<=0 and lower_wick>=float(cfg.lower_wick_min_ratio)
                        and close_loc>=float(cfg.bearish_close_min_location))
    supports=_support_count(bars,structure,cfg)
    reignition=signal is not None and str(getattr(signal,'pattern',''))==str(v7515.PATTERN_REIGNITION)
    base_confirmation=(dry and short and confirm) or reignition
    strong_ok=(strength>=STRONG_VOLUME_RATIO and supports>=STRONG_SUPPORT_REQUIRED)
    weak_comp=(WEAK_VOLUME_RATIO<=strength<STRONG_VOLUME_RATIO
               and supports>=WEAK_SUPPORT_REQUIRED and bull_ratio>=PRIOR_BULL_VOLUME_RATIO
               and dry_heavy<=float(cfg.strong_dry_max_volume_vs_heavy_cluster))
    eligible=base_confirmation and (strong_ok or weak_comp)
    missing=[]
    if strength<WEAK_VOLUME_RATIO:
        missing.append(f'음봉대량거래x{strength:.2f}<2.00')
    elif strength<STRONG_VOLUME_RATIO and not weak_comp:
        missing.append(f'약한음봉거래x{strength:.2f}<3.00')
        if bull_ratio<PRIOR_BULL_VOLUME_RATIO: missing.append(f'선행양봉대량x{bull_ratio:.2f}<2.00')
        if supports<WEAK_SUPPORT_REQUIRED: missing.append(f'지지확인{supports}/{WEAK_SUPPORT_REQUIRED}')
        if dry_heavy>float(cfg.strong_dry_max_volume_vs_heavy_cluster): missing.append('강한거래감소70%대기')
    if not dry and not reignition: missing.append('거래감소60%대기')
    if not short and not reignition: missing.append('짧은봉대기')
    if not confirm and not reignition: missing.append('반전봉대기')
    if strength>=STRONG_VOLUME_RATIO and supports<STRONG_SUPPORT_REQUIRED: missing.append('지지확인봉대기')
    return {'eligible':eligible,'strength':strength,'basis':str(anchor_strength['basis']),
            'grade':grade,'bull_ratio':bull_ratio,'supports':supports,'dry_heavy':dry_heavy,
            'dry_context':dry_context,'short':short,'confirm':confirm,'missing':tuple(dict.fromkeys(missing))}


def detect_v7539(strategy: Any, symbol: str, *, config: Any) -> tuple[Any|None,str,Any|None]:
    signal,state,evidence=_ORIGINAL_DETECT(strategy,symbol,config=config)
    if signal is None or evidence is None:
        return signal,state,evidence
    cfg=v7515.PrecisionConfig.load()
    bars=list(v756._same_session_bars(strategy.bars.get(symbol,[])))
    if not bars: return None,'분봉없음',None
    if v754.normalize_kst(signal.timestamp)!=v754.normalize_kst(bars[-1].timestamp):
        return None,'과거신호차단',evidence
    structure=_matching_structure(bars,evidence,cfg)
    if structure is None: return None,'상승후2배이상음봉거래없음',evidence
    ev=_evaluate(bars,structure,cfg,signal)
    if not ev['eligible']:
        return None,'매수대기_'+'_'.join(ev['missing'][:3]),evidence
    reason=str(signal.reason)
    import re
    reason=re.sub(r'\|손절=[^|]+','',reason)
    reason=re.sub(r'\|목표=[^|]+','',reason)
    bonus=(8.0 if ev['strength']>=STRONG_VOLUME_RATIO else 3.0)+(5.0 if ev['bull_ratio']>=2.0 else 0.0)+max(0,ev['supports']-1)*3.0
    reason+=(f"|조정음봉거래={ev['strength']:.2f}배({ev['basis']}/{ev['grade']})"
             f"|선행양봉대량={ev['bull_ratio']:.2f}배(가산조건)"
             f"|지지확인봉={ev['supports']}|자동손절없음|목표=1.5%고정")
    fixed=v753.Signal(signal.symbol,signal.action,float(signal.score)+bonus,reason,
                      float(signal.reference_price),signal.timestamp,str(signal.pattern),
                      float(signal.stop_loss_pct),FIXED_TAKE_PROFIT_PCT)
    return fixed,state,evidence


def assess_v7539(strategy: Any,symbol: str,*,loose: Any,strict: Any,now: datetime,name: str,source: str):
    item,state=_ORIGINAL_ASSESS(strategy,symbol,loose=loose,strict=strict,now=now,name=name,source=source)
    if item is None: return None,state
    cfg=v7515.PrecisionConfig.load()
    bars=list(v756._same_session_bars(strategy.bars.get(symbol,[])))
    structures=find_v7539_structures(bars,cfg) if bars else []
    if not structures: return None,'상승후2배이상음봉거래없음'
    structure=structures[0]
    base_signal,_,base_evidence=_ORIGINAL_DETECT(strategy,symbol,config=None)
    if base_evidence is not None:
        matched=_matching_structure(bars,base_evidence,cfg)
        if matched is not None: structure=matched
    ev=_evaluate(bars,structure,cfg,base_signal)
    if ev['eligible'] and base_signal is not None:
        stage='즉시매수조건근접'; missing=()
    elif ev['dry_heavy']<=float(cfg.dry_max_volume_vs_heavy_cluster):
        stage='관심종목-매수가능성'; missing=ev['missing'] or ('반전봉대기',)
    else:
        stage='거래바닥관찰'; missing=ev['missing'] or ('거래감소60%대기',)
    marker=(f"V7539|음봉거래x{ev['strength']:.2f}({ev['grade']}/{ev['basis']})"
            f"|선행양봉x{ev['bull_ratio']:.2f}|지지{ev['supports']}"
            f"|판정={'진입가능' if ev['eligible'] and base_signal is not None else '대기'}")
    score=float(item.score)+(12 if ev['strength']>=3 else 3)+(5 if ev['bull_ratio']>=2 else 0)
    return replace(item,stage=stage,strict_missing=tuple(missing),score=min(100.0,score),
                   source=f'{source}|{marker}'),state


def condition_reason_v7539(item: Any,bucket: str) -> str:
    source=str(getattr(item,'source',''))
    if 'V7539|' not in source:
        return _ORIGINAL_CONDITION_REASON(item,bucket)
    marker=source.split('V7539|',1)[1]
    parts=marker.split('|')
    summary=' · '.join(parts[:3])
    missing=tuple(getattr(item,'strict_missing',()) or ())
    if bucket=='ready': return summary+' · 최종매수조건완료'
    if missing: return summary+' · 대기:'+','.join(str(x) for x in missing[:2])
    return summary


# 모든 실제 탐지·후보표시 경로에 같은 평가기를 사용한다.
v7515.find_heavy_structures=find_v7539_structures
v7525.find_qualified_heavy_structures=find_v7539_structures
v7525.detect_v7525=detect_v7539
v7525.assess_v7525=assess_v7539
v7515.detect_precision_supply=detect_v7539
v7515.assess_precision_watch=assess_v7539
v756.detect_supply_exhaustion_pullback=detect_v7539
v7514.assess_loose_supply_potential=assess_v7539
v7520._condition_reason=condition_reason_v7539
v7530.strict_detect=detect_v7539
v7530.strict_assess=assess_v7539


class V7539Engine(v7525.V7525Engine):
    def _gui_snapshot(self) -> dict[str,Any]:
        snap=super()._gui_snapshot()
        for row in snap.get('rows',[]):
            if row.get('stage')=='관심종목':
                reason=str(row.get('reason',''))
                reason=reason.replace('대량 음봉 거래량 급증','시장 거래량 급증(방향 미확인)')
                row['reason']=reason
        return snap

v7525.V7525Engine=V7539Engine
v7525.VERSION=VERSION


def _run_regression() -> tuple[bool,str]:
    path=BASE_DIR/'test_v75_39_regression.py'
    if not path.exists(): return False,'회귀검사 파일 없음'
    try:
        spec=importlib.util.spec_from_file_location('v7539_regress_selfcheck',path)
        mod=importlib.util.module_from_spec(spec); assert spec and spec.loader
        sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
        code=mod.run()
        return code==0,('회귀검사 통과' if code==0 else '회귀검사 실패')
    except Exception as exc:
        return False,f'회귀검사 예외 {type(exc).__name__}: {exc}'


def self_check():
    results=list(_ORIGINAL_SELF_CHECK())
    ok,msg=_run_regression()
    results.extend([
        ('V75.38연동SHA',sha256_file(BASE_DIR/LINK_FILENAME)==LINK_SHA256,LINK_SHA256[:16]),
        ('V75.39매수단계정합회귀',ok,msg),
        ('선행장대양봉필수제거','body_gain' not in _prior_bull_volume.__code__.co_names,'양봉+대량거래만 가산'),
        ('음봉거래3배강신호',STRONG_VOLUME_RATIO==3.0,'3배'),
        ('음봉거래2배약신호',WEAK_VOLUME_RATIO==2.0,'2~3배 보강필요'),
        ('목표1.5유지',FIXED_TAKE_PROFIT_PCT==0.015,'+1.5%'),
    ])
    return results

v7525.self_check=self_check


def main()->int:
    return int(v7530.main())

if __name__=='__main__':
    raise SystemExit(main())
