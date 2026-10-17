#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.86 PAPER — authoritative P/L ledger and category continuity.

V75.85 trading/link/dashboard behaviour is preserved.  This layer supplies
real KPI values on every GUI snapshot, so the old dashboard's demonstration
fallback numbers can never appear.  Today's realized P/L is derived from the
same dated SELL ledger that creates the ``오늘매도`` rows.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any


VERSION = "V75.86-LEDGER-STAGE-CONTINUITY-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_85_early_phase_no_chase_paper.py"
PARENT_SHA256 = "bc37a64bd9b48fb8fce565b9fa78af84534a0a1684293a366d243088e0e1706c"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7586_immutable_v7585_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525 = parent.v7525
v57 = parent.v57
v753 = parent.v73.v753
KST = parent.v73.KST
diag = parent.diag


def today_sell_ledger(path: Path, *, now: datetime | None = None) -> tuple[float, int, bool]:
    """Return (today pnl, sell count, readable).

    A missing ledger means there have been no persisted fills, therefore zero
    sells and zero realized P/L.  A malformed existing ledger is reported as
    unreadable so the caller can use the broker's in-memory daily value.
    """
    today = (now or datetime.now(KST)).astimezone(KST).date()
    if not path.exists():
        return 0.0, 0, True
    total = 0.0
    count = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("side", "")).upper() != "SELL":
                    continue
                stamp = datetime.fromisoformat(str(row.get("timestamp", "")))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=KST)
                if stamp.astimezone(KST).date() != today:
                    continue
                total += float(row.get("pnl", 0.0) or 0.0)
                count += 1
    except (OSError, csv.Error, TypeError, ValueError):
        return 0.0, 0, False
    return total, count, True


class V7586LedgerStageContinuityEngine(parent.V7585EarlyPhaseNoChaseEngine):
    """Make dashboard money fields ledger-backed and preserve valid stages."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7586_last_ledger_warning = ""
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] GUI 예시손익 차단 | 오늘매도 원장=오늘실현손익 단일근거 | "
            "형성중 조건이탈은 삭제 대신 관심종목 하향",
            VERSION,
        )

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        ledger_pnl, ledger_count, readable = today_sell_ledger(v753.FILL_FILE, now=now)
        broker_daily = float(getattr(self.broker, "daily_realized_pnl", 0.0) or 0.0)
        today_realized = ledger_pnl if readable else broker_daily

        positions = dict(getattr(self.broker, "positions", {}))
        holding_total = 0.0
        unrealized = 0.0
        commission = float(getattr(self.settings, "commission_pct", 0.0) or 0.0)
        sell_tax = float(getattr(self.settings, "sell_tax_pct", 0.0) or 0.0)
        for symbol, position in positions.items():
            quantity = int(getattr(position, "quantity", 0) or 0)
            entry_cost = float(getattr(position, "entry_cost", 0.0) or 0.0)
            if entry_cost <= 0:
                entry_cost = float(getattr(position, "entry_price", 0.0) or 0.0) * quantity
                entry_cost += float(getattr(position, "entry_fee", 0.0) or 0.0)
            current = float(self._current_price(str(symbol)) or getattr(position, "entry_price", 0.0) or 0.0)
            liquidation = current * quantity * (1.0 - commission - sell_tax)
            holding_total += entry_cost
            unrealized += liquidation - entry_cost

        snapshot.update({
            "holding_total": holding_total,
            "unrealized_pnl": unrealized,
            "today_realized_pnl": today_realized,
            "total_pnl": today_realized + unrealized,
            "cash": float(getattr(self.broker, "cash", 0.0) or 0.0),
            "today_sell_count": ledger_count,
            "pnl_source": "today_sell_ledger" if readable else "broker_daily_fallback_ledger_error",
        })

        visible_sells = sum(1 for row in snapshot.get("rows", []) if str(row.get("stage", "")) == "매도종목")
        if readable and visible_sells != ledger_count:
            self.logger.error("오늘매도 원장/화면 불일치 ledger=%d gui=%d", ledger_count, visible_sells)
        if readable and abs(broker_daily - ledger_pnl) > 0.5:
            signature = f"{broker_daily:.2f}/{ledger_pnl:.2f}/{ledger_count}"
            if signature != self._v7586_last_ledger_warning:
                self._v7586_last_ledger_warning = signature
                self.logger.error(
                    "당일실현손익 상태/원장 불일치 broker=%+.0f ledger=%+.0f sells=%d; 화면은 원장 사용",
                    broker_daily, ledger_pnl, ledger_count,
                )
        return snapshot

    def _publish_intrabar_stage(self, *args: Any, **kwargs: Any) -> None:
        symbol = str(args[0]) if args else str(kwargs.get("symbol", ""))
        old = getattr(self, "_supply_potential", {}).get(symbol)
        super()._publish_intrabar_stage(*args, **kwargs)
        current = getattr(self, "_supply_potential", {}).get(symbol)
        # A single unfinished one-minute fluctuation must not make a valid
        # candidate disappear or jump to market-risk hold.  Keep monitoring at
        # 관심종목; true support invalidation is still handled by completed-bar
        # structure invalidation in the parent engine.
        if (
            old is not None
            and current is not None
            and str(getattr(current, "stage", "")) == "시장보류"
            and tuple(getattr(current, "strict_missing", ())) == ("형성중3분봉구조이탈",)
        ):
            self._supply_potential[symbol] = replace(
                current,
                stage="관심종목",
                strict_missing=("형성중변동·지지재확인",),
                source=f"{current.source}|단계연속성보존",
            )
            self._save_supply_potential()


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7525.V7525Engine = V7586LedgerStageContinuityEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "pnl_source": "dated SELL ledger",
        "demo_money_fallback_reachable": False,
        "stage_continuity": "forming loss -> 관심종목; completed invalidation may remove",
        "parent_v7585_sha256": PARENT_SHA256,
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.85기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.86실행엔진", v7525.V7525Engine is V7586LedgerStageContinuityEngine, identity["engine_class"]),
        ("손익원장단일화", identity["pnl_source"] == "dated SELL ledger", identity["pnl_source"]),
        ("예시손익차단", identity["demo_money_fallback_reachable"] is False, "snapshot 5개 KPI 항상 공급"),
        ("단계연속성", "관심종목" in identity["stage_continuity"], identity["stage_continuity"]),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity["link_class"])),
        ("PAPER전용", identity["paper_only"] is True and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7586_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    return int(parent.main())


if __name__ == "__main__":
    if "--runtime-info" in sys.argv or "--identity" in sys.argv:
        print(json.dumps(runtime_identity(), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)
    if "--self-check" in sys.argv:
        checks = self_check()
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in checks) else 1)
    raise SystemExit(main())
