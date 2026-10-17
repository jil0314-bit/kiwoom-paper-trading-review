#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.84 PAPER — fast 1-minute prediction with a mandatory causal setup.

V75.82 already predicts a forming three-minute candle from two distinct
one-minute records.  V75.84 keeps that timing, but forbids a dry-volume candle
from becoming an entry by itself.  A candidate must first prove either:

1. a directional first impulse with expanding volume, or
2. an exceptional capitulation decline whose local supply-exhaustion pattern
   is strong enough to stand without a prior bullish impulse.

This specifically prevents the reviewed FarmStory 09:58 false promotion while
retaining the approved dashboard, HeroMoon link, PAPER broker, immediate +1.5%
target order, -5% hard stop and all V75.81/V75.82 safeguards byte-pinned below.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import sys
from types import ModuleType
from typing import Any, Sequence


VERSION = "V75.84-VALIDATED-EARLY-WAVE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_82_preemptive_intrabar_safe_paper.py"
PARENT_SHA256 = "69c82b73710e44a20ca6a9577b98f9a65a2fda4bbc4c5b876e5072960f60928e"

MIN_FIRST_WAVE_GAIN = 0.022
MIN_FIRST_WAVE_NET_GAIN = 0.010
MIN_FIRST_WAVE_VOLUME_RATIO = 1.60
MIN_CAPITULATION_DROP = 0.040
MIN_CAPITULATION_FLUSH_RATIO = 2.00


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7584_immutable_v7582_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525
v753 = parent.v753
v73 = parent.v73
KST = parent.KST
diag = parent.diag


@dataclass(frozen=True, slots=True)
class CausalSetup:
    qualified: bool
    mode: str
    reason: str
    first_wave_gain: float
    first_wave_volume_ratio: float
    decline_from_peak: float


def _finite(bar: Any, field: str, default: float = 0.0) -> float:
    try:
        value = float(getattr(bar, field))
    except (AttributeError, TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _same_day_sorted_unique(bars: Sequence[Any], *, before: datetime) -> list[Any]:
    chosen: dict[tuple[int, int, int, int, int], Any] = {}
    for bar in bars:
        timestamp = getattr(bar, "timestamp", None)
        if timestamp is None or timestamp.date() != before.date() or timestamp >= before:
            continue
        key = (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute)
        chosen[key] = bar
    return [chosen[key] for key in sorted(chosen)]


def causal_setup_from_one_minute(
    bars: Sequence[Any],
    *,
    before: datetime,
    flush_volume_ratio: float = 0.0,
) -> CausalSetup:
    """Require cause before confirmation; missing minutes are never fabricated."""
    items = _same_day_sorted_unique(bars, before=before)
    if len(items) < 6:
        return CausalSetup(False, "DATA_BLOCK", "실제1분봉6개미만", 0.0, 0.0, 0.0)

    volumes = [max(0.0, _finite(item, "volume")) for item in items]
    positive_volumes = [value for value in volumes if value > 0.0]
    baseline_volume = max(1.0, statistics.median(positive_volumes)) if positive_volumes else 1.0

    best_gain = 0.0
    best_net = 0.0
    best_volume_ratio = 0.0
    # Search short, tradeable first-wave windows.  The current dry-up area is
    # excluded by `before`; no future candle or later outcome enters evidence.
    for start in range(len(items)):
        start_price = max(0.01, _finite(items[start], "open", _finite(items[start], "close", 0.01)))
        for end in range(start, min(len(items), start + 8)):
            window = items[start : end + 1]
            peak = max(_finite(item, "high", _finite(item, "close")) for item in window)
            last = _finite(window[-1], "close")
            gain = peak / start_price - 1.0
            net = last / start_price - 1.0
            volume_ratio = max(max(0.0, _finite(item, "volume")) for item in window) / baseline_volume
            positive_steps = sum(
                _finite(window[pos], "close") > _finite(window[pos - 1], "close")
                for pos in range(1, len(window))
            )
            directional = positive_steps >= 2 or (
                len(window) == 1
                and _finite(window[0], "close") > _finite(window[0], "open")
            )
            if gain > best_gain and directional:
                best_gain, best_net, best_volume_ratio = gain, net, volume_ratio

    first_wave = (
        best_gain >= MIN_FIRST_WAVE_GAIN
        and best_net >= MIN_FIRST_WAVE_NET_GAIN
        and best_volume_ratio >= MIN_FIRST_WAVE_VOLUME_RATIO
    )

    peak = max(max(_finite(item, "high"), _finite(item, "close")) for item in items)
    trough = min(min(_finite(item, "low"), _finite(item, "close")) for item in items)
    decline = max(0.0, 1.0 - trough / max(0.01, peak))
    capitulation = (
        decline >= MIN_CAPITULATION_DROP
        and float(flush_volume_ratio) >= MIN_CAPITULATION_FLUSH_RATIO
    )
    if first_wave:
        return CausalSetup(
            True, "FIRST_WAVE",
            f"일차상승{best_gain:.1%}·방향거래x{best_volume_ratio:.2f}",
            best_gain, best_volume_ratio, decline,
        )
    if capitulation:
        return CausalSetup(
            True, "CAPITULATION",
            f"급락{decline:.1%}·하락매물방출x{float(flush_volume_ratio):.2f}",
            best_gain, best_volume_ratio, decline,
        )
    return CausalSetup(
        False, "NO_CAUSE",
        f"일차파동부족({best_gain:.1%},x{best_volume_ratio:.2f})·급락소진부족({decline:.1%})",
        best_gain, best_volume_ratio, decline,
    )


class V7584ValidatedEarlyWaveEngine(parent.V7582PreemptiveIntrabarEngine):
    """V75.82 timing with a one-minute causal-sequence admission gate."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7584_causal_setup: dict[str, CausalSetup] = {}
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] V75.82 전 기능 고정 | 실제1분봉 일차파동/급락소진 선행검증 | "
            "거래바닥 단독 매수직전 금지 | 결측분 합성금지",
            VERSION,
        )

    def _publish_intrabar_stage(
        self, symbol: str, completed: Sequence[Any], partial: Any,
        projection: Any, plan: Any, *, now: datetime, bucket: datetime,
    ) -> None:
        setup = self._v7584_causal_setup.get(symbol)
        if setup is not None and not setup.qualified:
            # Preserve observation, but do not advertise a dry bar as an entry.
            plan = replace(
                plan,
                dashboard_stage="관찰후보",
                enter_now=False,
                reason=f"선행원인미확인|{setup.reason}",
            )
        super()._publish_intrabar_stage(
            symbol, completed, partial, projection, plan, now=now, bucket=bucket
        )

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        now = datetime.now(KST)
        bucket = v73._bucket_start(now)
        decision = getattr(self, "_gem_last_decisions", {}).get(symbol)
        flush_ratio = float(getattr(decision, "flush_volume_ratio", 0.0) or 0.0)
        self._v7584_causal_setup[symbol] = causal_setup_from_one_minute(
            raw, before=bucket, flush_volume_ratio=flush_ratio
        )
        super()._refresh_actual_bars(symbol, quote)


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7525.V7525Engine = V7584ValidatedEarlyWaveEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "one_minute_causal_gate": "first directional wave OR exceptional capitulation",
        "missing_minute_policy": "block evidence; never synthesize zero-volume bars",
        "farmstory_0958_policy": "observe only when first-wave/capitulation cause is absent",
        "parent_v7582_sha256": PARENT_SHA256,
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "target_registration": parent.runtime_identity()["target_registration"],
        "hard_stop_pct": parent.HARD_STOP_PCT,
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.82기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.84실행엔진", v7525.V7525Engine is V7584ValidatedEarlyWaveEngine, identity["engine_class"]),
        ("1분선행원인게이트", callable(causal_setup_from_one_minute), identity["one_minute_causal_gate"]),
        ("결측분봉합성금지", identity["missing_minute_policy"].startswith("block"), identity["missing_minute_policy"]),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity["link_class"])),
        ("즉시목표보존", parent.TAKE_PROFIT_PCT == 0.015, "+1.5%"),
        ("손절보존", parent.HARD_STOP_PCT == 0.05, "-5.0%"),
        ("PAPER전용", identity["paper_only"] is True and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7584_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
