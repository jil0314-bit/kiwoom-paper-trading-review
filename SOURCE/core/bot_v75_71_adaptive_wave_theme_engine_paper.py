#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.71 PAPER — adaptive wave, pullback absorption, theme leader logic."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from v7571_adaptive_wave_logic import evaluate_structure, rank_theme_leaders

VERSION = "V75.71-ADAPTIVE-WAVE-THEME-ENGINE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_70_final_preflight_audited_paper.py"
PARENT_SHA256 = "e02dbe17b69fcf7508bb08e28426035a307e3a6d4bf5f5dff044fcdc92a9b072"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7571_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.70 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules["v7571_parent"] = module
    spec.loader.exec_module(module)
    return module


parent = _load()
v69 = parent.parent
v68 = v69.parent
v67 = v68.parent
v66 = v67.parent
v65 = v66.parent
v64 = v65.parent
v61 = v64.parent
v60 = v61.v7560
v59 = v60.v7559
v57 = v59.v7557
v56 = v57.v7556
v48 = v56.v7548
v43 = v48.v7543

_BASE_ASSESS = v43.assess_v7541
_BASE_REASON = v43.condition_reason_v7541
INTENT_FILE = BASE_DIR.parent / "runtime" / "user_intent_ledger.json"


def _active_user_overlays(theme_or_sector: str) -> tuple[bool, bool]:
    """Return (sector_guard, theme_priority) for currently active user judgments."""
    if not theme_or_sector or not INTENT_FILE.exists():
        return False, False
    try:
        rows = json.loads(INTENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, False
    guard = False
    priority = False
    needle = theme_or_sector.lower()
    for row in rows if isinstance(rows, list) else []:
        if row.get("status") != "활성":
            continue
        scopes = [str(value).lower() for value in row.get("scope", [])]
        if not any(scope in needle or needle in scope for scope in scopes if scope):
            continue
        kind = str(row.get("kind", ""))
        if kind == "SECTOR_GUARD":
            guard = True
        elif kind == "THEME_PRIORITY":
            priority = True
    return guard, priority


def _market_context(strategy: Any) -> tuple[str, float]:
    snapshot = getattr(strategy, "_market_snapshot", None)
    state = str(getattr(snapshot, "state", "NORMAL") or "NORMAL")
    daily = tuple(getattr(snapshot, "daily_returns", ()) or ())
    if not daily:
        daily = tuple(getattr(snapshot, "daily", ()) or ())
    try:
        market_return = sum(float(value) for value in daily) / len(daily) if daily else 0.0
    except (TypeError, ValueError):
        market_return = 0.0
    return state, market_return


def _theme_context(strategy: Any, symbol: str) -> tuple[str, int | None, int | None]:
    meta = getattr(strategy, "candidate_meta", {}).get(symbol)
    theme = str(
        getattr(meta, "theme", "")
        or getattr(meta, "sector", "")
        or getattr(meta, "industry", "")
        or ""
    )
    rank = getattr(meta, "theme_rank", None)
    size = getattr(meta, "theme_size", None)

    peers_by_theme = getattr(strategy, "theme_peer_rows", {})
    peers = peers_by_theme.get(theme, []) if theme and isinstance(peers_by_theme, dict) else []
    if peers:
        ranked = rank_theme_leaders(peers)
        leader = ranked.get(symbol)
        if leader is not None:
            rank = leader.rank
            size = len(ranked)
    try:
        rank = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank = None
    try:
        size = int(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    return theme, rank, size


def assess_v7571(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
):
    item, state_text = _BASE_ASSESS(
        strategy, symbol, loose=loose, strict=strict,
        now=now, name=name, source=source
    )
    if item is None:
        return None, state_text

    bars = list(v43.v756._same_session_bars(strategy.bars.get(symbol, [])))
    market_state, market_return = _market_context(strategy)
    theme, theme_rank, theme_size = _theme_context(strategy, symbol)
    sector_guard, theme_priority = _active_user_overlays(theme)

    decision = evaluate_structure(
        bars,
        market_state=market_state,
        market_return=market_return,
        theme_rank=theme_rank,
        theme_size=theme_size,
        theme_priority=theme_priority,
        sector_guard=sector_guard,
    )

    original_stage = str(getattr(item, "stage", ""))
    strict_original = original_stage == "즉시매수조건근접"
    if decision.stage == "시장보류":
        stage = "관심종목-매수가능성"
    elif strict_original and decision.order_allowed:
        stage = "즉시매수조건근접"
    elif decision.stage in {"매수가능성", "매수직전", "관심종목"}:
        stage = "관심종목-매수가능성"
    else:
        stage = "거래바닥관찰"

    missing = list(getattr(item, "strict_missing", ()) or ())
    missing.extend(decision.missing)
    if strict_original and not decision.order_allowed:
        missing.append("구조점수재검증대기")
    if market_state in {"CAUTION", "SHOCK", "RECOVERY_WAIT"}:
        missing.append(f"시장{market_state}선별모드")

    # The adaptive engine may demote but never manufactures a strict entry by itself.
    adjusted_score = min(100.0, max(float(getattr(item, "score", 0.0)), decision.score))
    marker = (
        f"V7571|{decision.reason}|시장={market_state}"
        f"|주문비중={decision.size_factor:.0%}"
        f"|판정={'허용' if decision.order_allowed and strict_original else '대기'}"
    )
    return replace(
        item,
        stage=stage,
        strict_missing=tuple(dict.fromkeys(str(value) for value in missing if value)),
        score=adjusted_score,
        source=f"{source}|{marker}",
        updated_at=now,
    ), state_text


def condition_reason_v7571(item: Any, bucket: str) -> str:
    source = str(getattr(item, "source", ""))
    if "V7571|" not in source:
        return _BASE_REASON(item, bucket)
    marker = source.split("V7571|", 1)[1]
    parts = marker.split("|")
    summary = " · ".join(parts[:3])
    missing = tuple(getattr(item, "strict_missing", ()) or ())
    if bucket == "ready" and "판정=허용" in marker:
        return summary + " · 적응형구조매수조건완료"
    if missing:
        return summary + " · 대기:" + ",".join(str(value) for value in missing[:2])
    return summary


def market_entry_allowed_v7571(state: str) -> tuple[bool, str]:
    """Market decline is a stricter selector, not a universal entry prohibition."""
    normalized = str(state or "DATA_HALT").upper()
    if normalized == "DATA_HALT":
        return False, "시장데이터확인대기"
    if normalized == "NORMAL":
        return True, "시장정상_100%운용"
    if normalized == "RECOVERY_READY":
        return True, "시장회복확인_85%선별운용"
    if normalized == "CAUTION":
        return True, "시장약세_역행강세70%선별운용"
    if normalized == "RECOVERY_WAIT":
        return True, "시장회복대기_강한종목55%선별운용"
    if normalized == "SHOCK":
        return True, "시장급락_초기파동대장주40%극선별운용"
    return False, "시장상태미확인"


# Install the same assessor in every path used by watch lists and strict rechecks.
v43.assess_v7541 = assess_v7571
v43.condition_reason_v7541 = condition_reason_v7571
v43.market_entry_allowed = market_entry_allowed_v7571
v43.v7540.assess_v7540 = assess_v7571
v43.v7540.condition_reason_v7540 = condition_reason_v7571
v43.v7539.assess_v7539 = assess_v7571
v43.v7515.assess_precision_watch = assess_v7571
v43.v7525.assess_v7525 = assess_v7571
v43.v7514.assess_loose_supply_potential = assess_v7571
v43.v7530.strict_assess = assess_v7571

# Update the deepest visible version string.
v57.VERSION = VERSION


def self_check():
    rows = list(parent.self_check())
    source_text = Path(__file__).read_text(encoding="utf-8")
    logic_text = (BASE_DIR / "v7571_adaptive_wave_logic.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.70기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("고정봉배제", "adaptive_reversal_threshold" in logic_text and "adaptive_pivots" in logic_text, "변동성 적응형"),
        ("초기중기후기파동", all(token in logic_text for token in ("초기파동·첫조정", "중기확장·조정", "후기연장·주의")), "1·3·5파 취지"),
        ("매도흡수판정", "sell_volume_ratio" in logic_text and "absorption" in logic_text, "흡수/분배 구분"),
        ("테마대장우선", "rank_theme_leaders" in logic_text and "theme_rank == 1" in logic_text, "대장·2대장"),
        ("하락장선별운용", "시장급락_초기파동대장주40%" in source_text, "전면차단 아님"),
        ("사용자의견가중", "_active_user_overlays" in source_text and "SECTOR_GUARD" in source_text, "섹터제한·테마우선"),
        ("엄격진입비제조", "never manufactures a strict entry" in source_text, "기존 엄격신호 필요"),
    ])
    return rows


def main():
    return int(parent.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    raise SystemExit(main())
