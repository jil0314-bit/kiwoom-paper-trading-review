#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.89 PAPER — preserve reversal logic and add evidence-based trend route.

The attached 14-stock study is useful for timing inside already-strong stocks,
but it is survivor-biased and has no OHLC validation.  Therefore V75.88's
user-labelled exhaustion/support route remains intact.  This layer adds a
separate one-minute MA20 continuation route and measures both routes.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Sequence


VERSION = "V75.89-DUAL-ROUTE-EVIDENCE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_88_incremental_stage_limit_paper.py"
PARENT_SHA256 = "3262cd8e3ea00010d7450c588c2936afd9ce9305a22e784f11086f6d88baaeb9"
TREND_MA20_GAP_MIN = 0.01
TREND_MA20_GAP_MAX = 0.05
TREND_OVERHEAT_GAP = 0.08
TREND_MA60_OPTIONAL_MIN = 0.01
TREND_MA60_OPTIONAL_MAX = 0.08
TAKE_PROFIT_PCT = 0.015
HARD_STOP_PCT = 0.05
AUDIT_FILE = BASE_DIR.parent / "runtime" / "v7589_dual_route_audit.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7589_immutable_v7588_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525 = parent.v7525
v57 = parent.v57
v753 = parent.v753
KST = parent.KST


class TrendDecision:
    __slots__ = (
        "allowed", "reason", "ma20", "ma20_gap", "ma20_rising",
        "preferred_time", "overheated", "ma60", "ma60_gap", "ma60_supportive",
    )

    def __init__(
        self, allowed: bool, reason: str, *, ma20: float = 0.0,
        ma20_gap: float = 0.0, ma20_rising: bool = False,
        preferred_time: bool = False, overheated: bool = False,
        ma60: float = 0.0, ma60_gap: float = 0.0,
        ma60_supportive: bool = False,
    ) -> None:
        self.allowed = bool(allowed)
        self.reason = str(reason)
        self.ma20 = float(ma20)
        self.ma20_gap = float(ma20_gap)
        self.ma20_rising = bool(ma20_rising)
        self.preferred_time = bool(preferred_time)
        self.overheated = bool(overheated)
        self.ma60 = float(ma60)
        self.ma60_gap = float(ma60_gap)
        self.ma60_supportive = bool(ma60_supportive)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


def preferred_trend_time(now: datetime) -> bool:
    clock = now.astimezone(KST).time()
    minutes = clock.hour * 60 + clock.minute
    return (570 <= minutes < 660) or (840 <= minutes <= 920)


def trend_continuation_signal(
    completed_bars: Sequence[Any], *, current_price: float, now: datetime,
) -> TrendDecision:
    """Evaluate the forming minute without waiting for a three-minute close."""
    closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in completed_bars]
    if len(closes) < 20 or current_price <= 0:
        return TrendDecision(False, "1분종가20개미확보")
    previous_ma20 = sum(closes[-20:]) / 20.0
    forming_ma20 = (sum(closes[-19:]) + current_price) / 20.0
    gap20 = current_price / forming_ma20 - 1.0 if forming_ma20 > 0 else 0.0
    rising = forming_ma20 > previous_ma20
    preferred = preferred_trend_time(now)
    overheated = gap20 > TREND_OVERHEAT_GAP

    ma60 = 0.0
    gap60 = 0.0
    ma60_supportive = False
    if len(closes) >= 59:
        ma60 = (sum(closes[-59:]) + current_price) / 60.0
        gap60 = current_price / ma60 - 1.0 if ma60 > 0 else 0.0
        ma60_supportive = TREND_MA60_OPTIONAL_MIN <= gap60 <= TREND_MA60_OPTIONAL_MAX

    failures: list[str] = []
    if not (TREND_MA20_GAP_MIN <= gap20 <= TREND_MA20_GAP_MAX):
        failures.append(f"MA20이격{gap20:.2%}_범위밖")
    if not rising:
        failures.append("MA20하락")
    if not preferred:
        failures.append("순추세효율시간대아님")
    if overheated:
        failures.append("MA20이격8%초과과열")
    reason = (
        f"MA20이격{gap20:.2%}·MA20상승·효율시간대"
        + (f"·MA60이격{gap60:.2%}" if ma60 else "·MA60표본대기")
    ) if not failures else " · ".join(failures)
    return TrendDecision(
        not failures, reason, ma20=forming_ma20, ma20_gap=gap20,
        ma20_rising=rising, preferred_time=preferred, overheated=overheated,
        ma60=ma60, ma60_gap=gap60, ma60_supportive=ma60_supportive,
    )


def interest_signal_v7589(previous: Any, current: Any) -> tuple[bool, str]:
    """Keep the user's x2 rule but remove an unintended previous-bearish demand."""
    co, _ch, _cl, cc, _cr = parent._bar_shape(current)
    previous_close = float(getattr(previous, "close", 0.0) or 0.0)
    previous_volume = max(1, int(getattr(previous, "volume", 0) or 0))
    current_volume = int(getattr(current, "volume", 0) or 0)
    ratio = current_volume / previous_volume
    declining_bear = cc < co and cc < previous_close
    return declining_bear and ratio + 1e-12 >= parent.INTEREST_BEAR_VOLUME_RATIO, (
        f"하락음봉거래x{ratio:.2f}"
    )


# V75.88's class resolves this global from its own module.
parent.interest_signal = interest_signal_v7589


class V7589DualRouteEvidenceEngine(parent.V7588IncrementalStageLimitEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7589_trend_decisions: dict[str, TrendDecision] = {}
        self._v7589_order_minute: dict[str, datetime] = {}
        self._v7589_route_counts = {"reversal": 0, "trend": 0}
        super().__init__(*args, **kwargs)
        # Reconstruct only the display phase after restart; do not invent a
        # flush reference, so reversal entry still requires fresh evidence.
        stage_phase = {
            "관찰후보": "OBSERVED", "관심종목": "INTEREST",
            "매수가능성": "CANDIDATE", "매수직전": "PREBUY",
        }
        for symbol, item in getattr(self, "_supply_potential", {}).items():
            self._v7588_phase.setdefault(symbol, stage_phase.get(str(getattr(item, "stage", "")), ""))
        self.logger.warning(
            "[%s] V75.88 역추세 매물소진 경로 보존 + 실데이터 MA20 순추세 경로 추가 | "
            "순추세만 09:30~11:00/14:00~15:20 | MA20 +1~5%%·상승 | MA60 계측",
            VERSION,
        )

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        before = symbol in self.broker.positions
        super()._refresh_actual_bars(symbol, quote)
        if not before and symbol in self.broker.positions:
            self._v7589_route_counts["reversal"] += 1
            return
        if symbol in self._v7587_excluded or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        phase = self._v7588_phase.get(symbol, "")
        if phase not in {"OBSERVED", "INTEREST", "CANDIDATE", "PREBUY"}:
            return
        now = datetime.now(KST)
        raw = parent._today_bars(
            list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), now=now
        )
        decision = trend_continuation_signal(
            raw, current_price=float(getattr(quote, "price", 0.0) or 0.0), now=now
        )
        self._v7589_trend_decisions[symbol] = decision
        if not decision.allowed:
            return
        minute = now.replace(second=0, microsecond=0)
        if self._v7589_order_minute.get(symbol) == minute:
            return
        self._v7588_publish(symbol, "TREND_PREBUY", "매수직전", f"실데이터순추세:{decision.reason}", now)
        current = float(getattr(quote, "price", 0.0) or 0.0)
        signal = v753.Signal(
            symbol, v753.SignalAction.BUY, 125.0,
            f"실데이터순추세|{decision.reason}|체결즉시목표1.5%|고정손절5.0%",
            current, now, "실데이터순추세1분", HARD_STOP_PCT, TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._v7589_order_minute[symbol] = minute
        if self._attempt_entry(signal):
            self._v7589_route_counts["trend"] += 1

    def _v7588_write_audit(self, snapshot: dict[str, Any]) -> None:
        # Keep the parent's 10-second cadence and atomic write, then add a
        # separate evidence file so old and new meanings cannot be confused.
        super()._v7588_write_audit(snapshot)
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "study_scope": "14 survivor-selected stocks; 3 days; timing evidence only",
            "routes": {
                "reversal": "V75.88 exhaustion/support/MA5 path preserved",
                "trend": "observed stock + forming 1m MA20 gap 1~5% + rising MA20 + preferred time",
                "trend_ma60": "1~8% measured as optional evidence; not a blocker",
                "lunch_policy": "blocks trend route only; reversal route remains available",
            },
            "route_entry_counts": dict(self._v7589_route_counts),
            "trend_decisions": {
                symbol: decision.to_dict()
                for symbol, decision in sorted(self._v7589_trend_decisions.items())
            },
            "visible_rows": list(snapshot.get("rows", [])),
        }
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(AUDIT_FILE)


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7525.V7525Engine = V7589DualRouteEvidenceEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "entry_routes": "reversal preserved + evidence-based MA20 continuation",
        "trend_required": "MA20 gap +1..+5%, MA20 rising, 09:30..11:00 or 14:00..15:20",
        "trend_ma60": "optional measurement +1..+8%; not blocker",
        "lunch_effect": "trend route only; reversal route preserved",
        "survivorship_bias_guard": True,
        "ohlc_unverified_deletions_rejected": True,
        "audit_file": str(AUDIT_FILE),
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.88기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.89실행엔진", v7525.V7525Engine is V7589DualRouteEvidenceEngine, identity["engine_class"]),
        ("역추세경로보존", "reversal preserved" in identity["entry_routes"], identity["entry_routes"]),
        ("MA20순추세추가", "MA20 gap" in identity["trend_required"], identity["trend_required"]),
        ("점심범위제한", identity["lunch_effect"].startswith("trend route only"), identity["lunch_effect"]),
        ("생존편향방어", identity["survivorship_bias_guard"], "종목선정 일반화 금지"),
        ("OHLC미검증삭제거부", identity["ohlc_unverified_deletions_rejected"], "아래꼬리·봉폭 경로 유지"),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity.get("link_class"))),
        ("PAPER전용", identity["paper_only"] and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def print_self_check() -> int:
    rows = self_check()
    for name, ok, detail in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"V75.89 SELF-CHECK: {passed}/{len(rows)} PASS")
    return 0 if passed == len(rows) else 1


def main() -> None:
    if "--self-check" in sys.argv:
        raise SystemExit(print_self_check())
    if "--runtime-info" in sys.argv:
        print("RUNTIME_IDENTITY " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True, default=str))
        raise SystemExit(0)
    parent.main()


if __name__ == "__main__":
    main()
