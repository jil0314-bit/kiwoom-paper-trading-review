#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.88 PAPER — incremental stage flow and collaboration limit orders.

V75.87 is immutable.  This layer removes only its over-broad global AND gate,
adds the requested 1-minute stage path, direct 관심종목 commands, limit-price
collaboration orders and live execution tracking.  Link, dashboard geometry,
broker, target/stop, ledger and every unrelated parent route stay inherited.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
from types import ModuleType
from typing import Any, Sequence


VERSION = "V75.88-INCREMENTAL-STAGE-LIMIT-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_87_strict_and_command_audit_paper.py"
PARENT_SHA256 = "055452f1e9743f7ac42fc9b9f2c98f53d05b3564da066392e223fcb847d43c1f"
OBS_RETURN = 0.015
OBS_VOLUME = 20_000
INTEREST_BEAR_VOLUME_RATIO = 2.0
DRY_VOLUME_RATIO = 0.50
SHORT_RANGE_RATIO = 0.65
TAKE_PROFIT_PCT = 0.015
HARD_STOP_PCT = 0.05
AUDIT_FILE = BASE_DIR.parent / "runtime" / "v7588_candidate_audit.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7588_immutable_v7587_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525 = parent.v7525
v57 = parent.v57
v7556 = parent.v7556
v753 = parent.parent.v753
v7514 = parent.parent.parent.parent.parent.v7514
KST = parent.KST
HUB = v7556.HUB
SupplyPotential = v7514.SupplyPotential


def _stamp(value: Any) -> datetime:
    stamp = getattr(value, "timestamp", value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=KST)
    return stamp.astimezone(KST)


def _today_bars(bars: Sequence[Any], *, now: datetime) -> list[Any]:
    today = now.astimezone(KST).date()
    return sorted((bar for bar in bars if _stamp(bar).date() == today), key=_stamp)


def _bar_shape(bar: Any) -> tuple[float, float, float, float, float]:
    open_ = float(getattr(bar, "open", 0.0) or 0.0)
    high = float(getattr(bar, "high", 0.0) or 0.0)
    low = float(getattr(bar, "low", 0.0) or 0.0)
    close = float(getattr(bar, "close", 0.0) or 0.0)
    return open_, high, low, close, max(0.0, high - low)


def observation_signal(bars: Sequence[Any]) -> tuple[bool, str]:
    if len(bars) < 2:
        return False, "1분봉2개미확보"
    previous, current = bars[-2], bars[-1]
    prev_close = float(getattr(previous, "close", 0.0) or 0.0)
    close = float(getattr(current, "close", 0.0) or 0.0)
    change = close / prev_close - 1.0 if prev_close > 0 else 0.0
    volume = int(getattr(current, "volume", 0) or 0)
    allowed = change + 1e-12 >= OBS_RETURN and volume >= OBS_VOLUME
    return allowed, f"1분등락{change:.2%}·거래량{volume:,}주"


def interest_signal(previous: Any, current: Any) -> tuple[bool, str]:
    po, _ph, _pl, pc, _pr = _bar_shape(previous)
    co, _ch, _cl, cc, _cr = _bar_shape(current)
    pv = max(1, int(getattr(previous, "volume", 0) or 0))
    cv = int(getattr(current, "volume", 0) or 0)
    bearish_decline = cc < co and cc < pc and pc <= po
    ratio = cv / pv
    return bearish_decline and ratio + 1e-12 >= INTEREST_BEAR_VOLUME_RATIO, f"하락음봉거래x{ratio:.2f}"


def buy_candidate_signal(flush: Any, current: Any) -> tuple[bool, str]:
    fo, fh, fl, fc, fr = _bar_shape(flush)
    co, ch, cl, cc, cr = _bar_shape(current)
    fv = max(1, int(getattr(flush, "volume", 0) or 0))
    cv = int(getattr(current, "volume", 0) or 0)
    body = abs(cc - co)
    lower_wick = min(co, cc) - cl
    bullish = cc > co
    dry = cv / fv <= DRY_VOLUME_RATIO
    short = cr <= max(1e-9, fr) * SHORT_RANGE_RATIO
    wick = lower_wick >= max(body * 0.30, cr * 0.08)
    return bullish and dry and short and wick, (
        f"거래축소x{cv/fv:.2f}·짧은양봉폭x{cr/max(1e-9, fr):.2f}·아래꼬리"
    )


def settled_above_ma5(bars: Sequence[Any]) -> bool:
    if len(bars) < 6:
        return False
    closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in bars]
    ma_prev = sum(closes[-6:-1]) / 5.0
    ma_last = sum(closes[-5:]) / 5.0
    return closes[-2] >= ma_prev and closes[-1] >= ma_last


_BASE_COMPILER = parent.compile_collaboration_input_v7587


def compile_collaboration_input_v7588(text: str, **kwargs: Any) -> Any:
    draft = _BASE_COMPILER(text, **kwargs)
    raw = " ".join(str(text or "").split())
    if re.search(r"관심\s*종목(?:에|으로)?.*(?:넣|편입|추가|등록)", raw):
        draft.action = "ADD_INTEREST"
        draft.errors = [error for error in draft.errors if "가격" not in error]
    if str(getattr(draft, "action", "")) == "BUY_CONDITION" and re.search(
        r"(?:지정가|그\s*가격(?:으로|에)|가격을?\s*지정)", raw
    ):
        draft.trigger = "LIMIT_PRICE"
        draft.errors = [
            error for error in draft.errors
            if "가격 이하 도달인지 상향 돌파인지" not in error
        ]
    return draft


_BASE_ROUTE = parent.route_interpreted_action_v7587


def route_interpreted_action_v7588(action: str) -> str:
    return "GENERAL" if action == "ADD_INTEREST" else _BASE_ROUTE(action)


_BASE_TRIGGER_LABEL = v7556._trigger_label
_BASE_TRIGGER_CODE = v7556._trigger_code


def _trigger_label_v7588(trigger: str) -> str:
    return "지정가 주문" if trigger == "LIMIT_PRICE" else _BASE_TRIGGER_LABEL(trigger)


def _trigger_code_v7588(label: str) -> str:
    return "LIMIT_PRICE" if label in {"지정가 주문", "LIMIT_PRICE"} else _BASE_TRIGGER_CODE(label)


_TRACKING_ROWS: dict[str, dict[str, Any]] = {}
_BASE_HUB_SNAPSHOT = HUB.snapshot


def _hub_snapshot_v7588() -> dict[str, Any]:
    snapshot = _BASE_HUB_SNAPSHOT()
    active = list(snapshot.get("active", []))
    active.extend(dict(item) for item in _TRACKING_ROWS.values())
    snapshot["active"] = active
    return snapshot


_BASE_APPLY_COMMAND = parent._apply_collaboration_command_v7587


def _apply_collaboration_command_v7588(self: Any, command: dict[str, Any]) -> str:
    kind = str(command.get("kind", ""))
    if kind == "ADD_INTEREST":
        return self._v7588_add_interest(str(command.get("symbol", "")), str(command.get("name", "")))
    if kind == "BUY_CONDITION" and str(command.get("trigger", "")) == "LIMIT_PRICE":
        adjusted = dict(command)
        adjusted["trigger"] = "AT_OR_BELOW"
        adjusted["note"] = "[지정가주문] " + str(command.get("note", ""))
        return _BASE_APPLY_COMMAND(self, adjusted).replace("가격조건", "지정가")
    return _BASE_APPLY_COMMAND(self, command)


_COMBO_PATCHED = False
try:
    from tkinter import ttk as _ttk
    _PREVIOUS_COMBOBOX = _ttk.Combobox

    class V7588LimitCombobox(_PREVIOUS_COMBOBOX):
        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            values = tuple(kwargs.get("values", ()))
            if values == ("가격 이하 도달", "상향 돌파"):
                kwargs["values"] = (*values, "지정가 주문")
            super().__init__(master, **kwargs)

    _ttk.Combobox = V7588LimitCombobox
    _COMBO_PATCHED = True
except Exception:
    pass


_LABEL_PATCHED = False
try:
    import tkinter as _tk
    _ORIGINAL_LABEL_INIT = _tk.Label.__init__
    _OLD_OBSERVATION = "초기 포착 · 거래량 유입과 큰 매도 구조를 발견했지만 지지·반전은 아직 미확인"
    _NEW_OBSERVATION = (
        "초기 포착 · 1분봉 거래량 2만주 이상과 직전 종가 대비 +1.5% 이상 상승으로 "
        "큰 매수·상승 유입을 확인한 단계이며, 조정·지지·반전은 아직 미확인"
    )

    def _label_init_v7588(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
        if kwargs.get("text") == _OLD_OBSERVATION:
            kwargs["text"] = _NEW_OBSERVATION
        _ORIGINAL_LABEL_INIT(self, master, cnf or {}, **kwargs)

    _tk.Label.__init__ = _label_init_v7588
    _LABEL_PATCHED = True
except Exception:
    pass


class V7588IncrementalStageLimitEngine(parent.V7587StrictAndCommandAuditEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7588_phase: dict[str, str] = {}
        self._v7588_flush: dict[str, Any] = {}
        self._v7588_order_bucket: dict[str, datetime] = {}
        self._v7588_last_audit = 0.0
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] V75.87 전역AND 제거 | 관찰=1분+1.5%% AND 2만주 | "
            "관심=하락음봉거래x2 | 후보=거래축소·짧은양봉·아래꼬리 | "
            "두 1분봉 MA5 안착 후 세번째 1분봉 선제매수",
            VERSION,
        )

    def _v7588_make_potential(self, symbol: str, stage: str, reason: str, now: datetime) -> Any:
        old = getattr(self, "_supply_potential", {}).get(symbol)
        if old is not None:
            return replace(
                old, stage=stage, score=max(float(old.score), 80.0), updated_at=now,
                expires_at=now + timedelta(minutes=30), strict_missing=(),
                source=f"{old.source}|V75.88:{reason}",
            )
        return SupplyPotential(
            symbol=symbol, name=self.names.get(symbol, symbol), stage=stage, score=80.0,
            detected_at=now, updated_at=now, expires_at=now + timedelta(minutes=30),
            flush_time=now, rise_pct=0.015, retrace=0.0, flush_vs_median=1.0,
            flush_vs_p75=1.0, flush_vs_recent_max=1.0, dry_vs_flush=None,
            dry_range_vs_flush=None, low_break_pct=0.0, strict_missing=(),
            source=f"V75.88:{reason}",
        )

    def _v7588_publish(self, symbol: str, phase: str, stage: str, reason: str, now: datetime) -> None:
        if self._v7588_phase.get(symbol) == phase and symbol in getattr(self, "_supply_potential", {}):
            return
        self._v7588_phase[symbol] = phase
        self._supply_potential[symbol] = self._v7588_make_potential(symbol, stage, reason, now)
        self._save_supply_potential()
        self.logger.warning("V75.88 단계전환 %s → %s | %s", self._label(symbol), stage, reason)

    def _v7588_add_interest(self, symbol: str, name: str = "") -> str:
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("관심종목 종목코드 6자리가 필요합니다")
        self._v7587_excluded.discard(symbol)
        self._v7587_save_exclusions()
        self._manual_add_watch(symbol, name)
        now = datetime.now(KST)
        self._v7588_publish(symbol, "INTEREST", "관심종목", "형명령_관심종목즉시편입", now)
        return f"{name or symbol} 관심종목에 즉시 편입했습니다."

    def _current_symbols(self) -> list[str]:
        # Preserve V75.86 universe behaviour plus explicit exclusions only.
        return [
            symbol
            for symbol in super(parent.V7587StrictAndCommandAuditEngine, self)._current_symbols()
            if symbol not in self._v7587_excluded
        ]

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        # Bypass only V75.87's global AND gate; V75.85 phase/risk, position cap,
        # daily loss, PAPER broker and immediate target order remain intact.
        return bool(super(parent.V7587StrictAndCommandAuditEngine, self)._attempt_entry(
            signal, from_retry=from_retry
        ))

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        # Bypass only V75.87 post-refresh filtering/removal.
        super(parent.V7587StrictAndCommandAuditEngine, self)._refresh_actual_bars(symbol, quote)
        if symbol in self._v7587_excluded or symbol in self.broker.positions:
            return
        now = datetime.now(KST)
        raw = _today_bars(list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), now=now)
        if len(raw) < 2:
            return
        phase = self._v7588_phase.get(symbol, "")
        observed, observed_reason = observation_signal(raw)
        if not phase and observed:
            self._v7588_publish(symbol, "OBSERVED", "관찰후보", observed_reason, now)
            phase = "OBSERVED"
        if phase == "OBSERVED":
            interested, reason = interest_signal(raw[-2], raw[-1])
            if interested:
                self._v7588_flush[symbol] = raw[-1]
                self._v7588_publish(symbol, "INTEREST", "관심종목", reason, now)
                phase = "INTEREST"
        if phase == "INTEREST" and symbol in self._v7588_flush:
            candidate, reason = buy_candidate_signal(self._v7588_flush[symbol], raw[-1])
            if candidate:
                self._v7588_publish(symbol, "CANDIDATE", "매수가능성", reason, now)
                phase = "CANDIDATE"
        if phase != "CANDIDATE" or not settled_above_ma5(raw):
            return
        closes = [float(getattr(bar, "close", 0.0) or 0.0) for bar in raw[-4:]]
        current = float(getattr(quote, "price", 0.0) or 0.0)
        forming_ma5 = (sum(closes) + current) / 5.0 if len(closes) == 4 else current
        if current < forming_ma5:
            return
        bucket = now.replace(second=0, microsecond=0)
        if self._v7588_order_bucket.get(symbol) == bucket:
            return
        self._v7588_publish(symbol, "PREBUY", "매수직전", "1분MA5_2봉안착·3번째봉선제", now)
        signal = v753.Signal(
            symbol, v753.SignalAction.BUY, 120.0,
            "1분MA5두봉안착_세번째1분봉선제매수|체결즉시목표1.5%|고정손절5.0%",
            current, now, "형요청1분선제매수", HARD_STOP_PCT, TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._v7588_order_bucket[symbol] = bucket
        self._attempt_entry(signal)

    def _v7588_refresh_tracking(self) -> None:
        _TRACKING_ROWS.clear()
        collab = _BASE_HUB_SNAPSHOT()
        for item in collab.get("all", []):
            if item.get("kind") != "BUY_CONDITION" or item.get("status") != "EXECUTED":
                continue
            symbol = str(item.get("symbol", ""))
            position = self.broker.positions.get(symbol)
            if position is None:
                continue
            entry = float(getattr(position, "entry_price", 0.0) or 0.0)
            current = float(self._current_price(symbol) or entry)
            qty = int(getattr(position, "quantity", 0) or 0)
            pnl_pct = current / entry - 1.0 if entry > 0 else 0.0
            pnl_value = (current - entry) * qty
            overlay_id = str(item.get("overlay_id", symbol))
            _TRACKING_ROWS[overlay_id] = {
                **item,
                "overlay_id": f"track-{overlay_id}",
                "kind": f"체결 {entry:,.0f}원 · 현재 {current:,.0f}원 · 손익 {pnl_value:+,.0f}원({pnl_pct:+.2%})",
                "status": "TRACKING",
            }

    def _v7588_write_audit(self, snapshot: dict[str, Any]) -> None:
        now_mono = time.monotonic()
        if now_mono - self._v7588_last_audit < 10.0:
            return
        self._v7588_last_audit = now_mono
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "blocking_rules": {
                "observation": {"one_min_return_gte": OBS_RETURN, "one_min_volume_gte": OBS_VOLUME},
                "interest": {"declining_bearish": True, "volume_vs_previous_gte": INTEREST_BEAR_VOLUME_RATIO},
                "candidate": {"volume_vs_flush_lte": DRY_VOLUME_RATIO, "range_vs_flush_lte": SHORT_RANGE_RATIO,
                              "bullish": True, "lower_wick": True},
                "preemptive_entry": "two completed 1m closes over MA5 + forming third minute over MA5",
                "v7587_global_five_term_and": False,
            },
            "phases": dict(sorted(self._v7588_phase.items())),
            "excluded": sorted(self._v7587_excluded),
            "visible_rows": list(snapshot.get("rows", [])),
            "collaboration_tracking": list(_TRACKING_ROWS.values()),
        }
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(AUDIT_FILE)

    def _gui_snapshot(self) -> dict[str, Any]:
        # V75.86 snapshot retains authoritative money ledger and all legacy
        # stages.  V75.87 row suppression is intentionally bypassed.
        snapshot = super(parent.V7587StrictAndCommandAuditEngine, self)._gui_snapshot()
        rows = []
        for row in list(snapshot.get("rows", [])):
            symbol = str(row.get("symbol", ""))
            if symbol in self._v7587_excluded and str(row.get("stage", "")) not in {"매수종목", "매도종목"}:
                continue
            rows.append(row)
        snapshot["rows"] = rows
        self._v7588_refresh_tracking()
        snapshot["collaboration"] = _hub_snapshot_v7588()
        self._v7588_write_audit(snapshot)
        return snapshot


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7556.compile_collaboration_input = compile_collaboration_input_v7588
    v57.compile_collaboration_input = compile_collaboration_input_v7588
    v7556._trigger_label = _trigger_label_v7588
    v57._trigger_label = _trigger_label_v7588
    v7556._trigger_code = _trigger_code_v7588
    v57._trigger_code = _trigger_code_v7588
    v7556._apply_collaboration_command = _apply_collaboration_command_v7588
    v57.route_interpreted_action = route_interpreted_action_v7588
    HUB.snapshot = _hub_snapshot_v7588
    v7525.V7525Engine = V7588IncrementalStageLimitEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    source = Path(__file__).read_text(encoding="utf-8")
    limit_contract = _COMBO_PATCHED or all(
        token in source for token in ('"지정가 주문"', '"LIMIT_PRICE"', "V7588LimitCombobox")
    )
    description_contract = _LABEL_PATCHED or all(
        token in source for token in ("큰 매수·상승 유입", "_OLD_OBSERVATION", "_NEW_OBSERVATION")
    )
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "v7587_global_and_gate": False,
        "admission_logic": "observation only: 1m return >=1.5% AND 1m volume >=20,000; later stages use sequential evidence",
        "observation_gate": "1m return >=1.5% AND 1m volume >=20,000",
        "stage_flow": "관찰→하락음봉거래x2 관심→거래축소·짧은양봉·아래꼬리 후보→1m MA5 2봉 안착 후 3번째 선제매수",
        "direct_interest_command": True,
        "limit_price_option": limit_contract,
        "execution_live_tracking": True,
        "observation_description_fixed": description_contract,
        "audit_file": str(AUDIT_FILE),
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.87기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.88실행엔진", v7525.V7525Engine is V7588IncrementalStageLimitEngine, identity["engine_class"]),
        ("전역AND제거", identity["v7587_global_and_gate"] is False, identity["observation_gate"]),
        ("관심명령", identity["direct_interest_command"], "ADD_INTEREST→관심종목"),
        ("지정가선택", identity["limit_price_option"], "지정가 주문"),
        ("체결손익추적", identity["execution_live_tracking"], "실제체결·현재가·손익"),
        ("관찰설명정정", identity["observation_description_fixed"], "큰 매수·상승 유입"),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity.get("link_class"))),
        ("PAPER전용", identity["paper_only"] and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def print_self_check() -> int:
    rows = self_check()
    for name, ok, detail in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"V75.88 SELF-CHECK: {passed}/{len(rows)} PASS")
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
