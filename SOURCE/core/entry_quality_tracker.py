#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75 진입품질 계측기 (MAE/MFE) — 손절 없는 관찰 도구

형의 관찰 가설을 데이터로 검증한다:
  "잠시 빠졌다가 다시 오르는 경우가 많다. 손절을 걸면 다시 오를 걸 손실
   확정한다. 손절 없이, 어떤 경우에 회복하고 어떤 경우에 손실이 커져
   실현수익까지 까먹는지 데이터로 보고 싶다."

이 도구가 매수 후 각 종목에 대해 기록하는 것
--------------------------------------------
- MFE (Maximum Favorable Excursion): 매수 후 최대 상승폭 (가장 유리했던 지점)
- MAE (Maximum Adverse Excursion): 매수 후 최대 하락폭 (가장 불리했던 지점)
- 목표(+1.5%) 도달 여부와 도달까지 걸린 시간
- 목표 도달 전에 최대 얼마나 빠졌었나 (형 가설의 핵심: "빠졌다 오른다")
- 청산 시점 손익 (도달 못한 경우 어디서 끝났나)

이 도구가 답하는 질문
--------------------
Q1. +1.5% 도달률은? (신호가 목표까지 가는 비율)
Q2. 목표 도달한 것들이 도중에 최대 얼마나 빠졌었나? (-1%? -3%? -5%?)
    → "빠졌다 다시 오른다"가 실제로 몇 %이고 얼마나 깊었는지
Q3. 도달 못한 것들은 어디까지 빠졌나? (테일 리스크)
    → 손실이 커져 실현수익을 까먹는 케이스의 크기와 빈도
Q4. "회복 구간"별 통계: -2% 빠진 것 중 몇 %가 +1.5% 회복했나?
    → 손절선을 어디 두면 회복 기회를 얼마나 버리는지 (판단 근거)

매매 로직은 건드리지 않는다. 순수 관찰·기록만 한다. PAPER 전용.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TradeObservation:
    """매수 1건의 전 생애 관찰 기록."""
    symbol: str
    name: str
    entry_time: str
    entry_price: float
    target_price: float           # +1.5% 지점
    # 관찰 중 갱신되는 값
    highest_price: float = 0.0    # 최고가 (MFE 계산용)
    lowest_price: float = 0.0     # 최저가 (MAE 계산용)
    target_reached: bool = False
    target_reached_time: str = ""
    max_adverse_before_target: float = 0.0  # 목표 도달 전 최대 하락 (형 가설 핵심)
    # 청산 시
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    ticks: int = 0

    def mfe_pct(self) -> float:
        """매수가 대비 최대 상승 %"""
        if self.entry_price <= 0 or self.highest_price <= 0:
            return 0.0
        return (self.highest_price - self.entry_price) / self.entry_price * 100

    def mae_pct(self) -> float:
        """매수가 대비 최대 하락 % (음수)"""
        if self.entry_price <= 0 or self.lowest_price <= 0:
            return 0.0
        return (self.lowest_price - self.entry_price) / self.entry_price * 100

    def exit_pnl_pct(self) -> float:
        if self.entry_price <= 0 or self.exit_price <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100


class EntryQualityTracker:
    """보유 포지션의 MAE/MFE/목표도달/회복을 관찰해 CSV로 남긴다.

    엔진의 매 시세 갱신 때 observe()를 부르고, 청산 때 close()를 부른다.
    매매 판단에는 일절 개입하지 않는다.
    """

    def __init__(self, csv_path: Path, target_pct: float = 1.5):
        self.csv_path = Path(csv_path)
        self.target_pct = target_pct
        self._active: dict[str, TradeObservation] = {}

    def on_entry(self, symbol: str, name: str, entry_price: float,
                 entry_time: datetime, target_price: float | None = None) -> None:
        tp = target_price if target_price else entry_price * (1 + self.target_pct / 100)
        self._active[symbol] = TradeObservation(
            symbol=symbol, name=name,
            entry_time=entry_time.isoformat() if hasattr(entry_time, 'isoformat') else str(entry_time),
            entry_price=float(entry_price), target_price=float(tp),
            highest_price=float(entry_price), lowest_price=float(entry_price),
        )

    def observe(self, symbol: str, price: float, now: datetime | None = None) -> None:
        """매 시세마다 호출. 최고/최저/목표도달/도달전최대하락을 갱신."""
        obs = self._active.get(symbol)
        if obs is None or price <= 0:
            return
        obs.ticks += 1
        obs.highest_price = max(obs.highest_price, price)
        obs.lowest_price = min(obs.lowest_price, price)
        # 목표 도달 전이라면, 지금까지의 최대 하락을 기록 (형 가설의 핵심 지표)
        if not obs.target_reached:
            adverse = (price - obs.entry_price) / obs.entry_price * 100
            obs.max_adverse_before_target = min(obs.max_adverse_before_target, adverse)
            if price >= obs.target_price:
                obs.target_reached = True
                obs.target_reached_time = (now.isoformat() if now and hasattr(now, 'isoformat')
                                           else str(now or ''))

    def close(self, symbol: str, exit_price: float, exit_reason: str,
              now: datetime | None = None) -> TradeObservation | None:
        obs = self._active.pop(symbol, None)
        if obs is None:
            return None
        obs.exit_price = float(exit_price)
        obs.exit_reason = exit_reason
        obs.exit_time = (now.isoformat() if now and hasattr(now, 'isoformat') else str(now or ''))
        self._append_csv(obs)
        return obs

    def _append_csv(self, obs: TradeObservation) -> None:
        new_file = not self.csv_path.exists()
        try:
            with self.csv_path.open('a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow([
                        '종목', '종목명', '매수시각', '매수가', '목표가',
                        'MFE최대상승%', 'MAE최대하락%', '목표도달',
                        '도달전최대하락%', '청산시각', '청산가', '청산손익%', '청산사유', '관측수'])
                writer.writerow([
                    obs.symbol, obs.name, obs.entry_time, f'{obs.entry_price:.0f}',
                    f'{obs.target_price:.0f}', f'{obs.mfe_pct():.2f}', f'{obs.mae_pct():.2f}',
                    'Y' if obs.target_reached else 'N', f'{obs.max_adverse_before_target:.2f}',
                    obs.exit_time, f'{obs.exit_price:.0f}', f'{obs.exit_pnl_pct():.2f}',
                    obs.exit_reason, obs.ticks])
        except OSError:
            pass

    def snapshot_active(self) -> list[dict[str, Any]]:
        """현재 보유 중인 것들의 실시간 MAE/MFE (GUI 표시용)."""
        out = []
        for obs in self._active.values():
            out.append({
                'symbol': obs.symbol, 'name': obs.name,
                'mfe_pct': round(obs.mfe_pct(), 2), 'mae_pct': round(obs.mae_pct(), 2),
                'target_reached': obs.target_reached,
                'max_adverse_before_target': round(obs.max_adverse_before_target, 2),
            })
        return out


def analyze_observations(csv_path: Path) -> str:
    """쌓인 관찰 CSV를 형의 가설 관점에서 분석한 리포트를 만든다."""
    path = Path(csv_path)
    if not path.exists():
        return '아직 관찰 데이터가 없습니다.'
    rows = []
    with path.open('r', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if not rows:
        return '관찰 데이터가 비어 있습니다.'

    n = len(rows)
    reached = [r for r in rows if r['목표도달'] == 'Y']
    not_reached = [r for r in rows if r['목표도달'] != 'Y']

    def fnum(r, k):
        try: return float(r[k])
        except (ValueError, KeyError): return 0.0

    lines = []
    lines.append('=' * 64)
    lines.append('  진입품질 리포트 — 손절 없는 관찰 (형 가설 검증)')
    lines.append('=' * 64)
    lines.append(f'\n총 관찰 매수: {n}건')
    lines.append(f'  +1.5% 목표 도달: {len(reached)}건 ({len(reached)/n*100:.1f}%)')
    lines.append(f'  목표 미도달:     {len(not_reached)}건 ({len(not_reached)/n*100:.1f}%)')

    # Q2: 목표 도달한 것들이 도중에 얼마나 빠졌었나 (형 가설의 핵심)
    lines.append('\n[형 가설 검증] "빠졌다 다시 오른다" — 목표 도달 종목의 도중 최대하락')
    if reached:
        adverses = sorted(fnum(r, '도달전최대하락%') for r in reached)
        # 구간별 분포
        buckets = {'0~-1%': 0, '-1~-2%': 0, '-2~-3%': 0, '-3~-5%': 0, '-5%이하': 0}
        for a in adverses:
            if a > -1: buckets['0~-1%'] += 1
            elif a > -2: buckets['-1~-2%'] += 1
            elif a > -3: buckets['-2~-3%'] += 1
            elif a > -5: buckets['-3~-5%'] += 1
            else: buckets['-5%이하'] += 1
        for k, v in buckets.items():
            bar = '█' * int(v / len(reached) * 30)
            lines.append(f'  {k:>8}: {v:>3}건 ({v/len(reached)*100:>4.1f}%) {bar}')
        lines.append(f'  → 목표 도달한 것 중 도중에 -2% 넘게 빠졌던 비율: '
                     f'{sum(1 for a in adverses if a<=-2)/len(reached)*100:.1f}%')
        lines.append(f'    (이만큼이 손절 -2%였다면 회복 기회를 놓쳤을 거래)')

    # Q3: 미도달 종목의 테일 리스크
    lines.append('\n[테일 리스크] 목표 미도달 종목이 어디까지 빠졌나 (실현수익 잠식 위험)')
    if not_reached:
        maes = sorted(fnum(r, 'MAE최대하락%') for r in not_reached)
        worst = maes[0] if maes else 0
        median = maes[len(maes)//2] if maes else 0
        lines.append(f'  미도달 {len(not_reached)}건의 최대하락(MAE):')
        lines.append(f'    중앙값: {median:.1f}%  최악: {worst:.1f}%')
        deep = [m for m in maes if m <= -5]
        lines.append(f'    -5% 넘게 빠진 건: {len(deep)}건 '
                     f'({len(deep)/len(not_reached)*100:.1f}% of 미도달)')
        lines.append(f'  → 이 깊은 하락들이 "실현수익을 까먹는" 케이스. '
                     f'빈도와 크기를 손절 없이 관찰한 실측치.')

    # 종합 판단 재료
    lines.append('\n[판단 재료] 손절선을 걸었다면? (가정 시뮬레이션)')
    for stop in (-2.0, -3.0, -5.0):
        # 손절 걸었으면 회복 못했을 도달건 + 막았을 손실
        would_stop_but_recovered = sum(1 for r in reached if fnum(r, '도달전최대하락%') <= stop)
        would_stop_losses = sum(1 for r in not_reached if fnum(r, 'MAE최대하락%') <= stop)
        lines.append(f'  손절 {stop:.0f}%: 회복될 것 {would_stop_but_recovered}건을 손절 '
                     f'(놓친 이익) / 미도달 {would_stop_losses}건을 조기 차단 (막은 손실)')
    lines.append('\n  → 위 트레이드오프를 형의 실제 데이터로 보고 손절 여부·수준을 정하면 됩니다.')
    lines.append('    지금은 손절 없이 "회복 vs 잠식"의 실제 분포를 모으는 단계입니다.')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────
# 데모: 샘플 데이터로 리포트가 어떻게 보이는지
# ─────────────────────────────────────────────────────────
def _demo() -> None:
    import tempfile, random
    from datetime import timedelta
    random.seed(42)
    tmp = Path(tempfile.gettempdir()) / 'demo_entry_quality.csv'
    if tmp.exists():
        tmp.unlink()
    tracker = EntryQualityTracker(tmp, target_pct=1.5)
    base = datetime(2026, 7, 16, 9, 30)

    # 형 관찰을 반영한 샘플: 대부분 빠졌다 회복, 일부는 크게 빠짐
    for i in range(50):
        entry = 10000.0
        tracker.on_entry(f'{i:06d}', f'종목{i}', entry, base + timedelta(minutes=i))
        # 시나리오: 70%는 도중 하락 후 목표 도달, 30%는 미도달(일부 크게 빠짐)
        roll = random.random()
        if roll < 0.70:
            dip = random.uniform(-0.3, -4.0)   # 도중 하락
            low = entry * (1 + dip/100)
            tracker.observe(f'{i:06d}', low, base+timedelta(minutes=i+2))
            tracker.observe(f'{i:06d}', entry*1.015, base+timedelta(minutes=i+8))  # 목표 도달
            tracker.close(f'{i:06d}', entry*1.015, '목표도달', base+timedelta(minutes=i+8))
        else:
            dip = random.uniform(-2.0, -12.0)  # 미도달, 일부 크게
            low = entry * (1 + dip/100)
            tracker.observe(f'{i:06d}', low, base+timedelta(minutes=i+5))
            tracker.close(f'{i:06d}', low*1.01, '장마감청산', base+timedelta(minutes=i+30))

    print(analyze_observations(tmp))


if __name__ == '__main__':
    _demo()
