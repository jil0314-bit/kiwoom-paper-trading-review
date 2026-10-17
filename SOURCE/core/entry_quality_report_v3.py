#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.60 표본충분도 인식형 진입품질 리포트.

V75.59의 계측 데이터는 그대로 사용하고, 보고서 해석 계층만 강화한다.
매매·주문·손절·목표가 판단에는 절대 개입하지 않는다.

핵심 원칙
---------
1. 전체 거래 수뿐 아니라 완전한 시세경로, 거래일 수, 종목 수를 함께 본다.
2. 회복률처럼 분모가 작은 비율은 Wilson 95% 구간과 표본등급을 함께 표시한다.
3. 재시작 복구로 경로가 불완전한 거래는 MAE/MFE·회복률 추론에서 제외한다.
4. 같은 종목 반복이나 하루 집중으로 표본이 부풀려지는 경우를 경고한다.
5. 자료가 부족하면 숫자는 보여주되 결론은 명시적으로 유보한다.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping

from v7581_profitability_guard import (
    analyze_performance as analyze_profitability,
    calculate_profit_contract,
    format_profitability_lines,
)


MIN_EXPLORATORY = 30
MIN_OVERALL = 100
MIN_DAYS = 20
MIN_SYMBOLS = 10
MIN_BUCKET = 30


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _env_ratio(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) and value >= 0.0 else float(default)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """이항비율 Wilson 95% 신뢰구간을 백분율로 반환한다."""
    if total <= 0:
        return 0.0, 0.0
    successes = max(0, min(int(successes), int(total)))
    n = float(total)
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denominator
    return max(0.0, (center - margin) * 100.0), min(100.0, (center + margin) * 100.0)


def sample_grade(n: int) -> str:
    if n < 10:
        return "결론 유보"
    if n < MIN_EXPLORATORY:
        return "참고 수준"
    if n < MIN_OVERALL:
        return "초기 경향"
    return "표본 충분"


def _is_complete(row: Mapping[str, Any]) -> bool:
    return str(row.get("history_complete", "Y")).upper() == "Y"


def _target_touched(row: Mapping[str, Any]) -> bool:
    return str(row.get("target_touched", "N")).upper() == "Y"


def _read_active(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return [dict(item) for item in payload.get("active", []) if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        return []


def _latest_snapshot(path: Path, *, csv_module: Any) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    last: dict[str, str] = {}
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv_module.DictReader(handle):
                last = dict(row)
    except OSError:
        return {}
    return last


def _sample_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete_rows = [row for row in rows if _is_complete(row)]
    dates: set[str] = set()
    symbols: list[str] = []
    for row in complete_rows:
        parsed = _parse_dt(row.get("entry_time"))
        if parsed is not None:
            dates.add(parsed.date().isoformat())
        symbol = str(row.get("symbol", "")).strip()
        if symbol:
            symbols.append(symbol)
    counts = Counter(symbols)
    top_symbol, top_count = (counts.most_common(1)[0] if counts else ("", 0))
    top_share = top_count / len(complete_rows) * 100.0 if complete_rows else 0.0
    return {
        "complete_rows": complete_rows,
        "complete_count": len(complete_rows),
        "incomplete_count": len(rows) - len(complete_rows),
        "unique_days": len(dates),
        "unique_symbols": len(counts),
        "top_symbol": top_symbol,
        "top_count": top_count,
        "top_share": top_share,
    }


def _overall_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    complete = int(context.get("complete_count", 0))
    days = int(context.get("unique_days", 0))
    symbols = int(context.get("unique_symbols", 0))
    top_share = _finite(context.get("top_share"))
    if complete < MIN_OVERALL:
        reasons.append(f"완전경로 거래 {complete}/{MIN_OVERALL}건")
    if days < MIN_DAYS:
        reasons.append(f"거래일 {days}/{MIN_DAYS}일")
    if symbols < MIN_SYMBOLS:
        reasons.append(f"종목 {symbols}/{MIN_SYMBOLS}개")
    if complete >= 10 and top_share > 30.0:
        reasons.append(f"단일 종목 집중 {top_share:.1f}%")
    return not reasons, reasons


def analyze_quality_sample_aware(
    trades_csv: Path,
    active_state_json: Path,
    snapshots_csv: Path,
    *,
    read_trade_rows: Any,
    csv_module: Any,
    legacy_csv: Path | None = None,
) -> str:
    """V75.59 자료를 표본충분도와 데이터품질을 반영해 분석한다."""
    rows: list[dict[str, Any]] = read_trade_rows(trades_csv, legacy_csv)
    active = _read_active(active_state_json)
    context = _sample_context(rows)
    path_rows: list[dict[str, Any]] = context["complete_rows"]
    gate_ok, gate_reasons = _overall_gate(context)

    lines = ["=" * 86, "진입품질 리포트 V3 — 표본충분도 인식형 · PAPER 체결 관찰", "=" * 86]
    lines.append(f"완료 거래 {len(rows)}건 | 완전한 시세경로 {len(path_rows)}건 | 현재 보유 관찰 {len(active)}건")
    lines.append(
        f"거래일 {context['unique_days']}일 | 고유 종목 {context['unique_symbols']}개 | "
        f"최다 반복 종목 {context['top_symbol'] or '-'} {context['top_count']}건({context['top_share']:.1f}%)"
    )

    lines.append("\n[표본충분도 판정]")
    if len(path_rows) < MIN_EXPLORATORY:
        lines.append(f"  ⚠ 완전경로 {len(path_rows)}건: 탐색 단계입니다. 손절 수준·전략 변경 결론을 유보하세요.")
    elif len(path_rows) < MIN_OVERALL:
        lines.append(f"  ℹ 완전경로 {len(path_rows)}건: 초기 경향만 볼 수 있습니다. 최소 {MIN_OVERALL}건까지 축적하세요.")
    else:
        lines.append(f"  ✓ 완전경로 {len(path_rows)}건: 전체 경향을 검토할 최소 표본을 충족했습니다.")
    if context["unique_days"] < MIN_DAYS:
        lines.append(f"  ⚠ 거래일 {context['unique_days']}일: 같은 장세에 치우칠 수 있어 최소 {MIN_DAYS}거래일 전에는 결론을 유보합니다.")
    if context["unique_symbols"] < MIN_SYMBOLS:
        lines.append(f"  ⚠ 고유 종목 {context['unique_symbols']}개: 특정 종목 성격일 수 있어 최소 {MIN_SYMBOLS}개 종목이 필요합니다.")
    if len(path_rows) >= 10 and context["top_share"] > 30.0:
        lines.append(f"  ⚠ {context['top_symbol']} 비중 {context['top_share']:.1f}%: 반복 거래가 독립 표본처럼 부풀려질 수 있습니다.")
    if context["incomplete_count"]:
        lines.append(
            f"  ⚠ 재시작·복구로 경로가 불완전한 {context['incomplete_count']}건은 "
            "손익 집계에는 남기되 MAE/MFE·회복률 추론에서 제외했습니다."
        )
    if gate_ok:
        lines.append("  판정: 전체 표본 관문 통과. 그래도 상승장·하락장·급락일을 분리해 재확인해야 합니다.")
    else:
        lines.append("  판정: 결론 유보 — " + " / ".join(gate_reasons))

    if rows:
        net_pcts = [_finite(row.get("net_pnl_pct")) for row in rows]
        lines.append("\n[전체 실현 성과 — 불완전경로도 실제 체결손익에는 포함]")
        lines.append(
            f"  거래당 순손익률 평균 {statistics.fmean(net_pcts):+.2f}% | "
            f"중앙값 {_percentile(net_pcts, 0.5):+.2f}% | 합계 {sum(net_pcts):+.2f}%"
        )

    profit_contract = calculate_profit_contract(
        take_profit_pct=0.015,
        hard_stop_pct=0.05,
        commission_pct=_env_ratio("V73_COMMISSION_PCT", 0.00015),
        sell_tax_pct=_env_ratio("V73_SELL_TAX_PCT", 0.0018),
        stop_slippage_pct=_env_ratio("V73_SLIPPAGE_PCT", 0.0005),
    )
    profit_snapshot = analyze_profitability(rows, profit_contract)
    lines.append("\n[고정 +1.5% / -5% 손익분기·실제 기대값 — 주문 불개입]")
    lines.extend(f"  {line}" for line in format_profitability_lines(
        profit_contract, profit_snapshot
    ))

    if path_rows:
        touched = [row for row in path_rows if _target_touched(row)]
        not_touched = [row for row in path_rows if not _target_touched(row)]
        maes = [_finite(row.get("mae_pct")) for row in path_rows]
        mfes = [_finite(row.get("mfe_pct")) for row in path_rows]
        low, high = wilson_interval(len(touched), len(path_rows))
        lines.append("\n[완전경로 기준 전체 움직임]")
        lines.append(
            f"  목표가 터치 {len(touched)}/{len(path_rows)}건 ({len(touched)/len(path_rows)*100:.1f}%) "
            f"| 95% 구간 {low:.1f}~{high:.1f}% | {sample_grade(len(path_rows))}"
        )
        lines.append(
            f"  MAE 중앙 {_percentile(maes,0.5):+.2f}% · 하위10% {_percentile(maes,0.1):+.2f}% · 최악 {min(maes):+.2f}%"
        )
        lines.append(f"  MFE 중앙 {_percentile(mfes,0.5):+.2f}% · 상위10% {_percentile(mfes,0.9):+.2f}%")

        lines.append("\n[빠졌다가 목표까지 회복한 거래]")
        if touched:
            adverse = [_finite(row.get("max_adverse_before_target_pct")) for row in touched]
            lines.append(
                f"  목표 터치 전 최대하락: 중앙 {_percentile(adverse,0.5):+.2f}% | "
                f"75%지점 {_percentile(adverse,0.25):+.2f}% | 90%지점 {_percentile(adverse,0.1):+.2f}% | 최악 {min(adverse):+.2f}%"
            )
            if len(touched) < MIN_EXPLORATORY:
                lines.append(f"  ⚠ 회복 거래가 {len(touched)}건뿐이므로 하락 깊이 분포는 결론 유보입니다.")
        else:
            lines.append("  아직 목표 터치 완료 거래가 없습니다.")

        lines.append("\n[하락 깊이별 목표 회복률 — 분모·95% 구간·판정 동시 표시]")
        for level in (-1.0, -2.0, -3.0, -5.0, -8.0):
            crossed = [row for row in path_rows if _finite(row.get("mae_pct")) <= level]
            recovered = [row for row in crossed if _target_touched(row)]
            open_crossed = 0
            for item in active:
                entry = _finite(item.get("entry_price"))
                low_price = _finite(item.get("lowest_price"))
                mae = (low_price - entry) / entry * 100.0 if entry > 0 and low_price > 0 else 0.0
                if mae <= level:
                    open_crossed += 1
            if crossed:
                rate = len(recovered) / len(crossed) * 100.0
                ci_low, ci_high = wilson_interval(len(recovered), len(crossed))
                lines.append(
                    f"  {level:>5.1f}% 도달 {len(crossed):>3}건 → 목표회복 {len(recovered):>3}건 "
                    f"({rate:>5.1f}%, 95% {ci_low:>4.1f}~{ci_high:>4.1f}%) | "
                    f"열림 {open_crossed}건 | {sample_grade(len(crossed))}"
                )
            else:
                lines.append(f"  {level:>5.1f}% 도달 0건 | 계산 불가 | 열림 {open_crossed}건 | 결론 유보")

        lines.append("\n[가상 손절선의 관측상 상충관계]")
        for stop in (-2.0, -3.0, -5.0, -8.0):
            recovered_but_cut = sum(
                1 for row in touched if _finite(row.get("max_adverse_before_target_pct")) <= stop
            )
            non_recovered_cut = sum(
                1 for row in not_touched if _finite(row.get("mae_pct")) <= stop
            )
            denominator = sum(1 for row in path_rows if _finite(row.get("mae_pct")) <= stop)
            lines.append(
                f"  손절 {stop:.0f}% 가정: 이후 목표에 닿은 {recovered_but_cut}건도 잘렸을 수 있음 / "
                f"목표 미도달 {non_recovered_cut}건은 더 일찍 제한했을 수 있음 | "
                f"관련표본 {denominator}건({sample_grade(denominator)})"
            )
        lines.append("  ※ 호가·슬리피지·갭을 재현한 체결 백테스트가 아니라 관측 경로 비교입니다.")

        lines.append("\n[손실이 커진 조건 — 패턴·진입시간별, 소표본 라벨 포함]")
        pattern_groups: dict[str, list[dict[str, Any]]] = {}
        time_groups: dict[str, list[dict[str, Any]]] = {
            "09~10시": [], "10~12시": [], "12~14시": [], "14시이후": [], "시간미상": [],
        }
        for row in path_rows:
            pattern = str(row.get("pattern", "") or "미분류")
            pattern_groups.setdefault(pattern, []).append(row)
            parsed = _parse_dt(row.get("entry_time"))
            if parsed is None:
                band = "시간미상"
            elif parsed.hour < 10:
                band = "09~10시"
            elif parsed.hour < 12:
                band = "10~12시"
            elif parsed.hour < 14:
                band = "12~14시"
            else:
                band = "14시이후"
            time_groups[band].append(row)
        for pattern, group in sorted(pattern_groups.items(), key=lambda item: (-len(item[1]), item[0])):
            target_count = sum(1 for row in group if _target_touched(row))
            avg_net = statistics.fmean(_finite(row.get("net_pnl_pct")) for row in group)
            worst_mae = min(_finite(row.get("mae_pct")) for row in group)
            ci_low, ci_high = wilson_interval(target_count, len(group))
            lines.append(
                f"  패턴 {pattern}: {len(group)}건 | 목표터치 {target_count/len(group)*100:.1f}% "
                f"(95% {ci_low:.1f}~{ci_high:.1f}%) | 평균순손익 {avg_net:+.2f}% | "
                f"최악MAE {worst_mae:+.2f}% | {sample_grade(len(group))}"
            )
        for band, group in time_groups.items():
            if not group:
                continue
            target_count = sum(1 for row in group if _target_touched(row))
            avg_net = statistics.fmean(_finite(row.get("net_pnl_pct")) for row in group)
            worst_mae = min(_finite(row.get("mae_pct")) for row in group)
            ci_low, ci_high = wilson_interval(target_count, len(group))
            lines.append(
                f"  시간 {band}: {len(group)}건 | 목표터치 {target_count/len(group)*100:.1f}% "
                f"(95% {ci_low:.1f}~{ci_high:.1f}%) | 평균순손익 {avg_net:+.2f}% | "
                f"최악MAE {worst_mae:+.2f}% | {sample_grade(len(group))}"
            )
        deepest = sorted(path_rows, key=lambda row: _finite(row.get("mae_pct")))[:5]
        if deepest:
            lines.append("  깊은 하락 사례:")
            for row in deepest:
                lines.append(
                    f"    {row.get('name') or row.get('symbol')} | MAE {_finite(row.get('mae_pct')):+.2f}% | "
                    f"순손익 {_finite(row.get('net_pnl_pct')):+.2f}% | {row.get('pattern') or '미분류'} | "
                    f"{row.get('entry_reason') or '-'}"
                )
    else:
        lines.append("\n[완전경로 기준 분석]")
        lines.append("  아직 완전한 시세경로 완료 거래가 없어 MAE/MFE·회복률을 계산할 수 없습니다.")

    lines.append("\n[현재 열린 위험 — 생존편향 방지]")
    if active:
        open_rows: list[tuple[float, str, str]] = []
        incomplete = 0
        for item in active:
            entry = _finite(item.get("entry_price"))
            last = _finite(item.get("last_price"))
            pct = (last - entry) / entry * 100.0 if entry > 0 and last > 0 else 0.0
            open_rows.append((pct, str(item.get("symbol", "")), str(item.get("name", ""))))
            incomplete += int(not bool(item.get("history_complete", True)))
        open_rows.sort()
        lines.append(f"  최악 열린 손익률 {open_rows[0][0]:+.2f}% ({open_rows[0][2] or open_rows[0][1]})")
        for level in (-2.0, -3.0, -5.0, -8.0, -10.0):
            count = sum(1 for pct, _, _ in open_rows if pct <= level)
            lines.append(f"  {level:>5.1f}% 이하 {count}종목")
        if incomplete:
            lines.append(f"  ⚠ 재시작 전 경로가 불완전한 보유 {incomplete}종목")
    else:
        lines.append("  현재 열린 관찰 포지션이 없습니다.")

    latest = _latest_snapshot(snapshots_csv, csv_module=csv_module)
    if latest:
        lines.append("\n[실현수익 잠식 감시 — 최근 5분 스냅샷]")
        lines.append(
            f"  미실현손익 {_finite(latest.get('open_pnl')):+,.0f}원 | 손실 종목 합계 "
            f"{_finite(latest.get('open_loss_only')):+,.0f}원"
        )
        daily = _finite(latest.get("daily_erosion_pct"))
        cumulative = _finite(latest.get("cumulative_erosion_pct"))
        lines.append(f"  오늘 실현수익 잠식률 {daily:.1f}% | 누적 실현수익 잠식률 {cumulative:.1f}%")
        lines.append(
            f"  최악 보유 {_finite(latest.get('worst_pnl_pct')):+.2f}% "
            f"({latest.get('worst_symbol','-')})"
        )

    lines.append("\n[최종 해석 관문]")
    if gate_ok:
        lines.append("  전체 표본 관문은 통과했습니다. 다만 손절선별 관련표본이 30건 미만이면 해당 손절선 결론은 유보합니다.")
    else:
        lines.append("  현재 자료는 관찰용입니다. 자동손절 도입·제거 또는 손절 폭 변경의 근거로 확정 사용하지 않습니다.")
    lines.append("  권장 최소조건: 완전경로 100건 + 20거래일 + 10종목 + 검토할 하락구간 30건 이상.")
    lines.append("  상승장·하락장·급락일을 분리하고, 호가·슬리피지·갭을 포함한 별도 체결검증이 필요합니다.")
    lines.append("  이 보고서는 자동매도·자동손절을 실행하지 않습니다.")
    return "\n".join(lines)
