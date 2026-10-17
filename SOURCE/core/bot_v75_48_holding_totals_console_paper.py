#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.48 PAPER — 보유종목 총매수금액·총평가손익 합계 표시판.

V75.47의 더블클릭 즉시 차트 연동, 마우스 좌표 복원, ↑/↓ 연속 검토,
V75.45의 자동 입력칸 오탐 방어, V75.44의 매수신호 재검증을 SHA256
고정으로 그대로 상속한다.

이번 버전의 변경은 콘솔 5분 요약 출력뿐이다.

- 보유종목 상세표 바로 아래에 총매수금액을 표시한다.
- 신선한 시세가 있는 보유종목의 평가손익을 합산해 표시한다.
- 시세 미수신·지연 종목은 평가손익 합계에서 제외하고 개수를 명시한다.
- 총매수금액은 매수수수료를 포함한 실제 취득원가(entry_cost) 합계다.
- 매매 조건, 주문, 목표 +1.5%, 자동손절 없음, PAPER 전용은 변경하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Iterable

VERSION = "V75.48-HOLDING-TOTALS-CONSOLE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7547_FILENAME = "bot_v75_47_cursor_lock_arrow_verified_paper.py"
V7547_SHA256 = "c1720b66cb8bafaf69e75fc58ab8f6750418fbd2218ad4d601e6d51c3b769188"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(
            f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7547 = _load_fixed(V7547_FILENAME, V7547_SHA256, "v7548_immutable_base")
v7546 = v7547.v7546
v7545 = v7547.v7545
v7544 = v7547.v7544
v7543 = v7547.v7543
v7525 = v7547.v7525
KST = v7547.KST
_ORIGINAL_SELF_CHECK = v7547.self_check


@dataclass(frozen=True, slots=True)
class HoldingTotals:
    """보유종목 합계.

    purchase_cost는 모든 보유종목의 매수수수료 포함 취득원가 합계다.
    evaluation_pnl과 evaluated_purchase_cost는 신선한 시세가 확인된 종목만
    포함한다. 따라서 시세 미수신·지연 종목 때문에 손익을 임의 추정하지 않는다.
    """

    purchase_cost: float
    evaluated_purchase_cost: float
    evaluation_pnl: float
    evaluated_count: int
    unavailable_count: int

    @property
    def evaluation_return_pct(self) -> float:
        if self.evaluated_purchase_cost <= 0.0:
            return 0.0
        return self.evaluation_pnl / self.evaluated_purchase_cost


def calculate_holding_totals(
    holdings: Iterable[tuple[str, Any, dict[str, float | str | bool]]],
) -> HoldingTotals:
    """보유 상세행에서 취득원가와 평가손익 합계를 안전하게 계산한다."""
    purchase_cost = 0.0
    evaluated_purchase_cost = 0.0
    evaluation_pnl = 0.0
    evaluated_count = 0
    unavailable_count = 0

    for _symbol, position, metrics in holdings:
        entry_cost = max(0.0, float(position.entry_cost))
        purchase_cost += entry_cost
        if bool(metrics.get("quote_available", False)):
            evaluated_purchase_cost += entry_cost
            evaluation_pnl += float(metrics.get("pnl", 0.0))
            evaluated_count += 1
        else:
            unavailable_count += 1

    return HoldingTotals(
        purchase_cost=purchase_cost,
        evaluated_purchase_cost=evaluated_purchase_cost,
        evaluation_pnl=evaluation_pnl,
        evaluated_count=evaluated_count,
        unavailable_count=unavailable_count,
    )


def format_holding_totals(totals: HoldingTotals, total_count: int) -> str:
    """콘솔 한 줄 합계 문구를 만든다."""
    unavailable_note = (
        f" | 손익계산보류={totals.unavailable_count}종목"
        if totals.unavailable_count
        else ""
    )
    return (
        "[보유합계] "
        f"총매수금액(수수료포함)={totals.purchase_cost:,.0f}원 | "
        f"총평가손익={totals.evaluation_pnl:+,.0f}원 | "
        f"합산수익률={totals.evaluation_return_pct:+.2%} | "
        f"손익확인={totals.evaluated_count}/{int(total_count)}종목"
        f"{unavailable_note}"
    )


class V7548Engine(v7544.V7544Engine):
    """V75.44 매매 엔진을 유지하고 5분 콘솔 합계만 추가한다."""

    def _compact_report(self) -> None:
        self._sync_daily_state()
        self._enforce_fixed_targets()
        snapshot = self._gui_snapshot()
        if self.gui_bridge is not None:
            try:
                self.gui_bridge.publish(snapshot)
            except Exception as exc:
                self.logger.warning("GUI 목록 갱신 실패: %s", exc)

        now = datetime.now(KST)
        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        entry_allowed, entry_reason = v7543.market_entry_allowed(state)
        if self.manual_new_entries_paused:
            entry_allowed, entry_reason = False, "장중명령 신규매수중지"
        rows = list(snapshot.get("rows", []))
        grouped = {
            stage: [row for row in rows if row.get("stage") == stage]
            for stage in v7525.STAGE_ORDER
        }

        estimated_pnl = 0.0
        liquidation_value = float(self.broker.cash)
        unavailable_quotes = 0
        stale_quotes = 0
        holdings: list[tuple[str, Any, dict[str, float | str | bool]]] = []
        for symbol, position in self.broker.positions.items():
            quote = self.latest_quotes.get(symbol)
            raw_current = float(quote.price) if quote is not None else 0.0
            metrics = v7543.holding_metrics(
                position,
                raw_current,
                self.settings,
                quote_available=quote is not None,
                quote_timestamp=getattr(quote, "timestamp", None),
            )
            if bool(metrics.get("quote_available", False)):
                estimated_pnl += float(metrics["pnl"])
            else:
                unavailable_quotes += 1
                stale_quotes += int(bool(metrics.get("stale", False)))
            liquidation_value += float(metrics["proceeds"])
            holdings.append((symbol, position, metrics))
        holdings.sort(key=lambda item: item[1].entry_time)
        holding_totals = calculate_holding_totals(holdings)

        width = 168
        print("\n" + "=" * width)
        print(
            f"[{now:%H:%M}] 5분 요약 | 시장={v7543.v7518.market_label_short(state)} | "
            f"신규매수={'가능' if entry_allowed else '보류'}({entry_reason}) | "
            f"관심={len(grouped['관심종목']):>3} 관찰={len(grouped['관찰후보']):>3} "
            f"가능={len(grouped['매수가능성']):>3} 직전={len(grouped['매수직전']):>3} "
            f"보류={len(grouped['시장보류']):>3} 보유={len(holdings):>2}"
        )
        universe = snapshot.get("universe", {})
        print(
            f"감시원천={universe.get('sources', '-')} | 순위요청상한=각 {universe.get('rank_request_limit', 0)} | "
            f"통합풀상한={universe.get('pool_limit', 0)} | 현재감시={universe.get('active_count', 0)} | "
            f"매물전용상한={universe.get('supply_pool_limit', 0)} | "
            f"HOT={universe.get('hot_count', 0)}/{universe.get('hot_refresh_sec', 0):.0f}초 | "
            f"일반분봉={universe.get('general_refresh_sec', 0):.0f}초"
        )
        quote_note = (
            f" (손익계산보류 {unavailable_quotes}종목/시세지연 {stale_quotes}종목)"
            if unavailable_quotes else ""
        )
        print(
            f"평가자산={liquidation_value:>16,.0f}원 | 현금={float(self.broker.cash):>16,.0f}원 | "
            f"실현={float(self.broker.realized_pnl):>+13,.0f}원 | "
            f"평가={estimated_pnl:>+13,.0f}원{quote_note}"
        )

        limits = {
            "관심종목": 15,
            "관찰후보": 15,
            "매수가능성": 15,
            "매수직전": 12,
            "시장보류": 12,
        }
        for stage in ("관심종목", "관찰후보", "매수가능성", "매수직전", "시장보류"):
            stage_rows = grouped[stage]
            print("-" * width)
            print(f"[{stage}] {len(stage_rows)}종목")
            if not stage_rows:
                print("  없음")
                continue
            print(
                "  " + v7543.v7518.pad("편입시각", 8) + " " + v7543.v7518.pad("종목명", 20) + " "
                + v7543.v7518.pad("코드", 6) + " " + v7543.v7518.pad("현재가", 12, right=True) + "  이유"
            )
            for row in stage_rows[: limits[stage]]:
                print(
                    "  " + v7543.v7518.pad(str(row.get("stage_time", row.get("time", "-"))), 8) + " "
                    + v7543.v7518.pad(str(row.get("name", "")), 20) + " "
                    + v7543.v7518.pad(str(row.get("symbol", "")), 6) + " "
                    + v7543.v7518.pad(str(row.get("price", "")), 12, right=True) + "  "
                    + str(row.get("reason", ""))[:92]
                )
            hidden = len(stage_rows) - limits[stage]
            if hidden > 0:
                print(f"  ... 나머지 {hidden}종목은 GUI·상세로그")

        print("-" * width)
        print(f"[보유종목] {len(holdings)}종목 | 목표=매수가 대비 +1.5% | 자동손절=없음")
        if holdings:
            print(
                "  " + v7543.v7518.pad("매수시각", 8) + " " + v7543.v7518.pad("종목명", 20) + " "
                + v7543.v7518.pad("코드", 6) + " " + v7543.v7518.pad("수량", 9, right=True) + " "
                + v7543.v7518.pad("평단", 11, right=True) + " " + v7543.v7518.pad("현재", 11, right=True) + " "
                + v7543.v7518.pad("구분", 5) + " " + v7543.v7518.pad("수익률", 9, right=True) + " "
                + v7543.v7518.pad("평가손익", 14, right=True) + " " + v7543.v7518.pad("목표", 11, right=True) + "  재매수"
            )
            for symbol, position, metrics in holdings:
                quote_available = bool(metrics.get("quote_available", True))
                current_text = str(metrics.get("current_text", "조회대기"))
                rate_text = f"{float(metrics['pnl_pct']):+.2%}" if quote_available else "-"
                pnl_text = f"{float(metrics['pnl']):+,.0f}원" if quote_available else "-"
                print(
                    "  " + v7543.v7518.pad(position.entry_time.astimezone(KST).strftime("%H:%M:%S"), 8) + " "
                    + v7543.v7518.pad(self.names.get(symbol, symbol), 20) + " "
                    + v7543.v7518.pad(symbol, 6) + " "
                    + v7543.v7518.pad(f"{int(position.quantity):,}", 9, right=True) + " "
                    + v7543.v7518.pad(f"{float(position.entry_price):,.0f}", 11, right=True) + " "
                    + v7543.v7518.pad(current_text, 11, right=True) + " "
                    + v7543.v7518.pad(str(metrics["label"]), 5) + " "
                    + v7543.v7518.pad(rate_text, 9, right=True) + " "
                    + v7543.v7518.pad(pnl_text, 14, right=True) + " "
                    + v7543.v7518.pad(f"{float(position.target_price):,.0f}", 11, right=True) + "  "
                    + f"완료{self._v7541_completed_cycles.get(symbol, 0)}회/새조건후 무제한"
                )
        else:
            print("  없음")

        # 요청사항: 보유 상세표 바로 아래에 전체 매수금액과 평가손익 합계를 표시한다.
        print("  " + "-" * (width - 4))
        print("  " + format_holding_totals(holding_totals, len(holdings)))

        print(f"[오늘 매도종목] {len(grouped['매도종목'])}건")
        for row in grouped["매도종목"][-10:]:
            print(
                f"  {row.get('time', '-'):8} {v7543.v7518.pad(str(row.get('name', '')), 20)} "
                f"{v7543.v7518.pad(str(row.get('symbol', '')), 6)} "
                f"{v7543.v7518.pad(str(row.get('price', '')), 12, right=True)}  {row.get('reason', '')}"
            )
        if not grouped["매도종목"]:
            print("  없음")
        print("=" * width + "\n")
        self.save_pattern_state()


# 구버전 회귀검사는 엔진 클래스의 정확한 identity까지 검증한다.
# 따라서 새 하위 클래스로 교체하지 않고, 검증된 V75.44 엔진 클래스의
# 출력 메서드만 V75.48 구현으로 좁게 교체한다. 매매 메서드는 건드리지 않는다.
v7544.V7544Engine._compact_report = V7548Engine._compact_report
v7525.V7525Engine = v7544.V7544Engine
v7525.VERSION = VERSION


def _run_v7548_tests() -> tuple[bool, str]:
    path = BASE_DIR / "test_v75_48_holding_totals_console.py"
    try:
        spec = importlib.util.spec_from_file_location("v7548_special_test", path)
        if spec is None or spec.loader is None:
            return False, "테스트 로드 실패"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = int(module.run())
        return code == 0, "통과" if code == 0 else "실패"
    except Exception as exc:
        return False, f"예외 {type(exc).__name__}: {exc}"


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    test_ok, message = _run_v7548_tests()
    results.extend(
        [
            (
                "V75.47기준SHA",
                sha256_file(BASE_DIR / V7547_FILENAME) == V7547_SHA256,
                V7547_SHA256[:16],
            ),
            ("V75.48보유합계회귀", test_ok, message),
            (
                "보유합계출력적용",
                v7544.V7544Engine._compact_report is V7548Engine._compact_report
                and v7525.V7525Engine is v7544.V7544Engine,
                "엔진 identity 유지·출력 메서드만 V75.48",
            ),
            (
                "V75.47GUI유지",
                v7525.SimpleDoubleClickGui is v7546.V7546Gui,
                "더블클릭·↑↓·마우스복원 불변",
            ),
            (
                "V75.44매매엔진상속",
                issubclass(V7548Engine, v7544.V7544Engine),
                "매매 조건 불변",
            ),
        ]
    )
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7547.main())


if __name__ == "__main__":
    raise SystemExit(main())
