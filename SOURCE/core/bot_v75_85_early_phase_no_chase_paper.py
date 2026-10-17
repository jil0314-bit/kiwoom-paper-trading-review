#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.85 PAPER — enter the exhaustion phase, never its late rebound.

Only the final automatic-entry admission point changes from V75.84.  A valid
three-minute exhaustion setup must be acted on within two bars of the latest
dry-volume bar and before price has rebounded more than 2.2% from support.
This preserves the intended first/second split entry while rejecting the
reviewed Neotis 11:09 chase after the useful 09:36/09:39 phase had passed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Sequence


VERSION = "V75.85-EARLY-PHASE-NO-CHASE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_84_validated_early_wave_paper.py"
PARENT_SHA256 = "6d3fb44dd77ff72f5388745d13de02407cc1ca033c9e9c8a02db681c5e2481ec"
MAX_BARS_AFTER_DRY = 2
MAX_REBOUND_FROM_SUPPORT = 0.022
CAUTION_OVERHEAD_SCORE = 0.55
MIN_TARGET_ROOM = 0.017


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7585_immutable_v7584_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525
v73 = parent.v73
diag = parent.diag


@dataclass(frozen=True, slots=True)
class EntryPhase:
    allowed: bool
    reason: str
    bars_after_dry: int
    rebound_from_support: float
    overhead_score: float
    target_room_pct: float


def entry_phase_guard(decision: Any, *, current_price: float) -> EntryPhase:
    dry_index = int(getattr(decision, "dry_index", -1))
    current_index = int(getattr(decision, "confirm_index", -1))
    bars_after_dry = current_index - dry_index if dry_index >= 0 else 999
    support = max(0.01, float(getattr(decision, "support_low", 0.01) or 0.01))
    rebound = max(0.0, float(current_price) / support - 1.0)
    overhead = max(0.0, float(getattr(decision, "overhead_score", 0.0) or 0.0))
    room = float(getattr(decision, "target_room_pct", 0.0) or 0.0)

    if dry_index < 0:
        return EntryPhase(False, "거래바닥미확인", bars_after_dry, rebound, overhead, room)
    if bars_after_dry > MAX_BARS_AFTER_DRY:
        return EntryPhase(
            False, f"거래바닥후{bars_after_dry}봉경과_초기진입구간종료",
            bars_after_dry, rebound, overhead, room,
        )
    if rebound > MAX_REBOUND_FROM_SUPPORT:
        return EntryPhase(
            False, f"지지대비{rebound:.1%}반등_추격금지",
            bars_after_dry, rebound, overhead, room,
        )
    if overhead >= CAUTION_OVERHEAD_SCORE and room < MIN_TARGET_ROOM:
        return EntryPhase(
            False, f"상단매물{overhead:.2f}·목표공간{room:.1%}_부족",
            bars_after_dry, rebound, overhead, room,
        )
    return EntryPhase(
        True, f"거래바닥후{bars_after_dry}봉·지지대비{rebound:.1%}_초기구간",
        bars_after_dry, rebound, overhead, room,
    )


class V7585EarlyPhaseNoChaseEngine(parent.V7584ValidatedEarlyWaveEngine):
    """Apply the timing gate to every automatic gem entry route."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7585_phase: dict[str, EntryPhase] = {}
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] V75.84 전 기능 고정 | 거래바닥 후 0~2봉만 진입 | "
            "지지대비 +2.2%% 초과 추격금지 | 네오티스 11:09 후행진입 차단",
            VERSION,
        )

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(getattr(signal, "symbol", ""))
        pattern = str(getattr(signal, "pattern", ""))
        # Completed-three-minute signals publish their newest decision on the
        # strategy immediately before calling the engine.  Synchronise it here,
        # before the phase gate (the V75.81 parent used to do this later).
        stored = getattr(self.strategy, "_v7580_decisions", {})
        stored_decision = stored.get(symbol) if isinstance(stored, dict) else None
        if stored_decision is not None and hasattr(stored_decision, "dry_index"):
            self._gem_last_decisions[symbol] = stored_decision
        decision = getattr(self, "_gem_last_decisions", {}).get(symbol)
        # Manual commands and unrelated legacy patterns keep their exact path.
        if pattern == "매물소진눌림1차" and decision is not None:
            price = float(getattr(signal, "reference_price", 0.0) or 0.0)
            if price <= 0.0:
                quote = getattr(self, "latest_quotes", {}).get(symbol)
                price = float(getattr(quote, "price", 0.0) or 0.0)
            phase = entry_phase_guard(decision, current_price=price)
            self._v7585_phase[symbol] = phase
            if not phase.allowed:
                self.logger.warning(
                    "후행매수차단 %s | %s | 지지반등=%.2f%% | 상단매물=%.2f",
                    self._label(symbol), phase.reason,
                    phase.rebound_from_support * 100.0, phase.overhead_score,
                )
                old = getattr(self, "_supply_potential", {}).get(symbol)
                if old is not None:
                    try:
                        self._supply_potential[symbol] = replace(
                            old,
                            stage="시장보류",
                            strict_missing=(phase.reason,),
                            source=f"{old.source}|후행추격차단",
                        )
                        self._save_supply_potential()
                    except Exception:
                        pass
                return False
        return bool(super()._attempt_entry(signal, from_retry=from_retry))


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7525.V7525Engine = V7585EarlyPhaseNoChaseEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "entry_phase": "latest dry bar +0..2 bars; rebound from support <=2.2%",
        "neotis_1109_policy": "blocked as late chase",
        "parent_v7584_sha256": PARENT_SHA256,
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "target_registration": parent.runtime_identity()["target_registration"],
        "hard_stop_pct": parent.parent.HARD_STOP_PCT,
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.84기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.85실행엔진", v7525.V7525Engine is V7585EarlyPhaseNoChaseEngine, identity["engine_class"]),
        ("초기진입봉수", MAX_BARS_AFTER_DRY == 2, "거래바닥 후 0~2봉"),
        ("추격상한", MAX_REBOUND_FROM_SUPPORT == 0.022, "지지 대비 +2.2%"),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity["link_class"])),
        ("즉시목표보존", parent.parent.TAKE_PROFIT_PCT == 0.015, "+1.5%"),
        ("손절보존", parent.parent.HARD_STOP_PCT == 0.05, "-5.0%"),
        ("PAPER전용", identity["paper_only"] is True and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7585_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
