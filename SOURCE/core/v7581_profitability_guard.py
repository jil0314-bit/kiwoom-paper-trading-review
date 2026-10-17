#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.81 PAPER profitability and exposure telemetry.

This module measures the fixed +1.5% / -5% contract with the configured fees
and stop slippage.  It never sends, changes or blocks an order by itself.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping


MIN_EXPLORATORY = 30
MIN_DECISION = 100
MIN_DAYS = 20
MIN_SYMBOLS = 10


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


@dataclass(frozen=True, slots=True)
class ProfitContract:
    gross_target_pct: float
    gross_stop_pct: float
    commission_pct: float
    sell_tax_pct: float
    stop_slippage_pct: float
    net_win_pct: float
    net_loss_pct: float
    required_win_rate_pct: float


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    total: int
    wins: int
    losses: int
    flats: int
    unique_days: int
    unique_symbols: int
    observed_win_rate_pct: float
    average_net_pct: float
    median_net_pct: float
    binary_expectancy_pct: float
    verdict: str


def calculate_profit_contract(
    *,
    take_profit_pct: float,
    hard_stop_pct: float,
    commission_pct: float,
    sell_tax_pct: float,
    stop_slippage_pct: float,
) -> ProfitContract:
    values = (
        take_profit_pct,
        hard_stop_pct,
        commission_pct,
        sell_tax_pct,
        stop_slippage_pct,
    )
    if any(not math.isfinite(float(value)) or float(value) < 0.0 for value in values):
        raise ValueError("수익계약 입력값은 0 이상의 유한수여야 합니다")
    if hard_stop_pct >= 1.0 or commission_pct + sell_tax_pct >= 1.0:
        raise ValueError("수익계약 비율 범위 오류")

    entry_cost = 1.0 + float(commission_pct)
    target_proceeds = (1.0 + float(take_profit_pct)) * (
        1.0 - float(commission_pct) - float(sell_tax_pct)
    )
    stop_proceeds = (1.0 - float(hard_stop_pct)) * (
        1.0 - float(stop_slippage_pct)
    ) * (1.0 - float(commission_pct) - float(sell_tax_pct))
    net_win = (target_proceeds / entry_cost - 1.0) * 100.0
    net_loss = (stop_proceeds / entry_cost - 1.0) * 100.0
    loss_size = abs(net_loss)
    required = (
        loss_size / (max(0.0, net_win) + loss_size) * 100.0
        if net_win > 0.0 and loss_size > 0.0 else 100.0
    )
    return ProfitContract(
        gross_target_pct=float(take_profit_pct) * 100.0,
        gross_stop_pct=-float(hard_stop_pct) * 100.0,
        commission_pct=float(commission_pct) * 100.0,
        sell_tax_pct=float(sell_tax_pct) * 100.0,
        stop_slippage_pct=float(stop_slippage_pct) * 100.0,
        net_win_pct=net_win,
        net_loss_pct=net_loss,
        required_win_rate_pct=required,
    )


def read_trade_rows(path: Path) -> list[dict[str, str]]:
    target = Path(path)
    if not target.exists():
        return []
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error):
        return []


def analyze_performance(
    rows: Iterable[Mapping[str, Any]],
    contract: ProfitContract,
) -> PerformanceSnapshot:
    values: list[float] = []
    days: set[str] = set()
    symbols: set[str] = set()
    for row in rows:
        values.append(_finite(row.get("net_pnl_pct")))
        symbol = str(row.get("symbol", "")).strip()
        if symbol:
            symbols.add(symbol)
        raw_time = str(row.get("entry_time", "")).strip()
        if raw_time:
            try:
                days.add(datetime.fromisoformat(raw_time).date().isoformat())
            except ValueError:
                pass

    total = len(values)
    wins = sum(value > 0.0 for value in values)
    losses = sum(value < 0.0 for value in values)
    flats = total - wins - losses
    win_rate = wins / total * 100.0 if total else 0.0
    probability = win_rate / 100.0
    binary_expectancy = (
        probability * contract.net_win_pct
        + (1.0 - probability) * contract.net_loss_pct
        if total else 0.0
    )
    average = statistics.fmean(values) if values else 0.0
    median = statistics.median(values) if values else 0.0

    if total == 0:
        verdict = "자료없음"
    elif total < MIN_EXPLORATORY:
        verdict = "결론유보_30건미만"
    elif total < MIN_DECISION or len(days) < MIN_DAYS or len(symbols) < MIN_SYMBOLS:
        verdict = "초기경향_자동차단금지"
    elif average > 0.0:
        verdict = "표본관문통과_관찰상양수"
    else:
        verdict = "표본관문통과_관찰상음수_전략재검토"

    return PerformanceSnapshot(
        total=total,
        wins=wins,
        losses=losses,
        flats=flats,
        unique_days=len(days),
        unique_symbols=len(symbols),
        observed_win_rate_pct=win_rate,
        average_net_pct=average,
        median_net_pct=median,
        binary_expectancy_pct=binary_expectancy,
        verdict=verdict,
    )


def format_profitability_lines(
    contract: ProfitContract,
    snapshot: PerformanceSnapshot,
) -> list[str]:
    return [
        (
            "고정계약 순손익 추정 "
            f"승리 {contract.net_win_pct:+.3f}% / 손절 {contract.net_loss_pct:+.3f}% / "
            f"필요승률 {contract.required_win_rate_pct:.1f}%"
        ),
        (
            f"실제완료 {snapshot.total}건 | 수익 {snapshot.wins} 손실 {snapshot.losses} 보합 {snapshot.flats} | "
            f"승률 {snapshot.observed_win_rate_pct:.1f}% "
            f"(필요대비 {snapshot.observed_win_rate_pct - contract.required_win_rate_pct:+.1f}%p) | "
            f"평균순손익 {snapshot.average_net_pct:+.3f}% | "
            f"단순이항기대값 {snapshot.binary_expectancy_pct:+.3f}%"
        ),
        (
            f"표본 거래일 {snapshot.unique_days}일·종목 {snapshot.unique_symbols}개 | {snapshot.verdict} | "
            "측정값은 충분한 표본 전 자동매수 차단에 사용하지 않음"
        ),
    ]


__all__ = [
    "MIN_DAYS", "MIN_DECISION", "MIN_EXPLORATORY", "MIN_SYMBOLS",
    "PerformanceSnapshot", "ProfitContract", "analyze_performance",
    "calculate_profit_contract", "format_profitability_lines", "read_trade_rows",
]
