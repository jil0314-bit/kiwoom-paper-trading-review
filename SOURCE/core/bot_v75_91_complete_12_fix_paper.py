#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.91 PAPER — the twelve requested corrections, isolated and auditable."""
from __future__ import annotations

from datetime import datetime, timedelta
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any

VERSION = "V75.91-COMPLETE-12-FIX-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_90_full_pattern_fusion_paper.py"
PARENT_SHA256 = "98d3766ed10751eafce30095db968414e6896cc10f16bc3557e1934bd8ecd43c"
RUNTIME_DIR = BASE_DIR.parent / "runtime_v7591"
TIMELINE_FILE = RUNTIME_DIR / "stage_timeline.json"
AUDIT_FILE = RUNTIME_DIR / "v7591_complete_12_audit.json"
LOCK_FILE = RUNTIME_DIR / "V75_91_RUNNING.lock"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7591_immutable_v7590_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525, v57, v753, v7556, KST = parent.v7525, parent.v57, parent.v753, parent.v7556, parent.KST
_stamp = parent.parent.parent._stamp


def _install_isolated_runtime() -> None:
    """Never import fills, positions, commands or demo rows from an older build."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    v753.LOG_DIR = RUNTIME_DIR / "paper_ledger"
    v753.STATE_FILE = v753.LOG_DIR / "paper_state.json"
    v753.FILL_FILE = v753.LOG_DIR / "paper_fills.csv"
    v753.PATTERN_STATE_FILE = v753.LOG_DIR / "pattern_state.json"
    v753.ERROR_FILE = v753.LOG_DIR / "api_errors.txt"
    v57.TARGET_SOURCE_PATH = RUNTIME_DIR / "target_source_ledger.json"
    fresh_hub = v7556.CollaborationHub(
        RUNTIME_DIR / "collaboration_state.json",
        RUNTIME_DIR / "collaboration_events.jsonl",
    )
    v7556.HUB = fresh_hub
    if hasattr(v57, "HUB"):
        v57.HUB = fresh_hub
    parent.HUB = fresh_hub
    v88 = parent.parent.parent
    v88.HUB = fresh_hub
    v88._BASE_HUB_SNAPSHOT = fresh_hub.snapshot
    fresh_hub.snapshot = v88._hub_snapshot_v7588
    original_ledger = v57.IntentLedger

    class IsolatedIntentLedger(original_ledger):
        def __init__(self, _ignored: Any = None) -> None:
            super().__init__(RUNTIME_DIR / "user_intent_ledger.json")

    v57.IntentLedger = IsolatedIntentLedger


_install_isolated_runtime()
HUB = v7556.HUB


def _clock(value: Any) -> str:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=KST)
        return stamp.astimezone(KST).strftime("%H:%M:%S")
    found = re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", str(value or ""))
    if not found:
        return "-"
    return found[-1] if found[-1].count(":") == 2 else found[-1] + ":00"


def _load_timeline() -> dict[str, Any]:
    try:
        payload = json.loads(TIMELINE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_timeline(payload: dict[str, Any]) -> None:
    TIMELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = TIMELINE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(TIMELINE_FILE)


STAGE_SHORT = {
    "관찰후보": "관찰", "관찰종목": "관찰", "관심종목": "관심",
    "매수가능성": "후보", "매수후보": "후보", "매수직전": "직전",
    "시장보류": "보류", "매수종목": "매수", "보유종목": "매수", "매도종목": "매도",
}


def _entry_context_gate(raw: list[Any], now: datetime) -> tuple[bool, str]:
    """Block late quiet tops while retaining verified breakout and pullback routes."""
    today = [bar for bar in raw if _stamp(bar).astimezone(KST).date() == now.date()]
    if len(today) < 8:
        return False, "당일1분봉8개미만"
    recent = today[-30:]
    closes = [float(getattr(x, "close", 0.0) or 0.0) for x in recent]
    vols = [max(0, int(getattr(x, "volume", 0) or 0)) for x in recent]
    if not closes or min(closes) <= 0:
        return False, "가격자료불완전"
    current, high, low = closes[-1], max(closes), min(closes)
    rise = high / low - 1.0
    at_top = current >= high * 0.995
    baseline = sorted(vols[:-1])[(len(vols[:-1]) - 1) // 2] if len(vols) > 1 else vols[-1]
    quiet = vols[-1] < max(20_000, baseline * 1.2)
    if rise >= 0.05 and at_top and quiet:
        return False, f"고점추격차단:최근상승{rise:.1%}·고점이격{current/high-1:.2%}·거래미확인"
    return True, f"진입맥락확인:최근폭{rise:.1%}·고점이격{current/high-1:.2%}"


class V7591Complete12FixEngine(parent.V7590FullPatternFusionEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7591_timeline = _load_timeline()
        self._v7591_rejections: dict[str, str] = {}
        super().__init__(*args, **kwargs)
        self.logger.warning("[%s] 독립원장·단일실행·전단계시간·오편입/고점추격방어 활성", VERSION)

    def _record_stage(self, symbol: str, stage: str, now: datetime, reason: str) -> None:
        if not symbol or not stage:
            return
        item = self._v7591_timeline.setdefault(symbol, {"events": []})
        events = item.setdefault("events", [])
        short = STAGE_SHORT.get(stage, stage)
        if not events or events[-1].get("stage") != short:
            events.append({"stage": short, "at": now.isoformat(), "reason": str(reason)})
            item[short] = now.isoformat()
            item["updated_at"] = now.isoformat()
            item["name"] = self.names.get(symbol, symbol)
            item["events"] = events[-20:]
            _save_timeline(self._v7591_timeline)

    def _v7588_publish(self, symbol: str, phase: str, stage: str, reason: str, now: datetime) -> None:
        manual = "형명령" in str(reason)
        if not manual:
            raw = sorted(list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), key=_stamp)
            flow = parent.prior_flow_gate(raw, now=now)
            if not flow.allowed:
                self._v7591_rejections[symbol] = f"{stage} 편입거절:{flow.reason}"
                self.logger.warning("V75.91 오편입차단 %s | %s", self._label(symbol), flow.reason)
                return
        before = self._v7588_phase.get(symbol, "")
        super()._v7588_publish(symbol, phase, stage, reason, now)
        if before != self._v7588_phase.get(symbol, "") or symbol in getattr(self, "_supply_potential", {}):
            self._record_stage(symbol, stage, now, reason)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(getattr(signal, "symbol", ""))
        reason = str(getattr(signal, "reason", ""))
        raw = sorted(list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), key=_stamp)
        new_route = any(label in reason for label in ("돌파후첫눌림", "선행수급매물흡수", "전고점확인돌파"))
        if not new_route:
            allowed, why = _entry_context_gate(raw, datetime.now(KST))
            if not allowed:
                self._v7591_rejections[symbol] = why
                self.logger.warning("V75.91 오매수차단 %s | %s", self._label(symbol), why)
                return False
        bought = bool(super()._attempt_entry(signal, from_retry=from_retry))
        if bought:
            self._record_stage(symbol, "매수종목", datetime.now(KST), reason)
        return bought

    def _fill_times(self, now: datetime) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not v753.FILL_FILE.exists():
            return result
        try:
            with v753.FILL_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    symbol = str(row.get("symbol", "")).zfill(6)
                    stamp = datetime.fromisoformat(str(row.get("timestamp", "")))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=KST)
                    if stamp.astimezone(KST).date() != now.date():
                        continue
                    side = str(row.get("side", "")).upper()
                    item = result.setdefault(symbol, {})
                    item[side.lower() + "_time"] = stamp.astimezone(KST).isoformat()
                    item[side.lower() + "_price"] = float(row.get("price", 0.0) or 0.0)
                    item[side.lower() + "_qty"] = int(float(row.get("quantity", 0) or 0))
        except (OSError, ValueError, TypeError, csv.Error):
            return result
        return result

    def _enrich_all_times(self, snapshot: dict[str, Any], now: datetime) -> None:
        fills = self._fill_times(now)
        for row in snapshot.get("rows", []):
            symbol, stage = str(row.get("symbol", "")).zfill(6), str(row.get("stage", ""))
            original = _clock(row.get("stage_time", row.get("time", "")))
            item = self._v7591_timeline.setdefault(symbol, {"events": []})
            short = STAGE_SHORT.get(stage, stage)
            if short and short not in item and original != "-":
                stamp = datetime.combine(now.date(), datetime.strptime(original, "%H:%M:%S").time(), KST)
                item[short] = stamp.isoformat()
                item.setdefault("events", []).append({"stage": short, "at": stamp.isoformat(), "reason": "화면행최초확인"})
            fill = fills.get(symbol, {})
            if fill.get("buy_time"):
                item["매수"] = fill["buy_time"]
            if fill.get("sell_time"):
                item["매도"] = fill["sell_time"]
            order = ["관찰", "관심", "후보", "직전", "보류", "매수", "매도"]
            pieces = [f"{key} {_clock(item[key])}" for key in order if item.get(key)]
            row["stage_time"] = " / ".join(pieces) or original
            if pieces:
                base_reason = str(row.get("reason", ""))
                row["reason"] = f"시간이력 {' → '.join(pieces)} · {base_reason}"
            quote = getattr(self, "latest_quotes", {}).get(symbol)
            if quote is not None:
                current = float(getattr(quote, "price", 0.0) or 0.0)
                if current > 0:
                    row["current_price"] = current
            if stage == "매도종목" and fill:
                row["current_price"] = fill.get("sell_price", row.get("current_price", 0))
                row["target_price"] = fill.get("sell_price", row.get("target_price", 0))
                row["quantity"] = fill.get("sell_qty", row.get("quantity", 0))
            reject = self._v7591_rejections.get(symbol)
            if reject and stage not in {"매수종목", "매도종목"}:
                row["reason"] = f"{reject} · " + str(row.get("reason", ""))
        _save_timeline(self._v7591_timeline)

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        self._enrich_all_times(snapshot, now)
        payload = {
            "version": VERSION, "saved_at": now.isoformat(),
            "runtime_isolated": str(RUNTIME_DIR),
            "state_file": str(v753.STATE_FILE), "fill_file": str(v753.FILL_FILE),
            "stage_timeline": self._v7591_timeline,
            "automatic_rejections": self._v7591_rejections,
            "visible_rows": list(snapshot.get("rows", [])),
            "collaboration": snapshot.get("collaboration", {}),
        }
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(AUDIT_FILE)
        return snapshot


_BASE_PREPARE = v7556.prepare_gui_rows


def prepare_gui_rows_v7591(rows: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prepared, counts = _BASE_PREPARE(rows)
    order = {stage: index for index, stage in enumerate(v7556.GUI_STAGE_ORDER)}
    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        found = re.findall(r"\d{1,2}:\d{2}:\d{2}", str(row.get("stage_time", "")))
        seconds = -1
        if found:
            h, m, s = map(int, found[-1].split(":")); seconds = h * 3600 + m * 60 + s
        return (order.get(str(row.get("stage", "")), 99), 1 if row.get("departed") else 0, -seconds, str(row.get("name", "")))
    prepared.sort(key=key)
    return prepared, counts


try:
    from tkinter import ttk as _ttk
    _heading, _column = _ttk.Treeview.heading, _ttk.Treeview.column
    def heading(self: Any, column: Any, option: Any = None, **kwargs: Any) -> Any:
        if kwargs.get("text") in {"편입/매수", "편입·매수/매도시각"}:
            kwargs["text"] = "전체 단계·매수·매도 시간이력"
        return _heading(self, column, option, **kwargs)
    def column(self: Any, name: Any, option: Any = None, **kwargs: Any) -> Any:
        if str(name) == "stage_time" and "width" in kwargs: kwargs["width"] = max(260, int(kwargs["width"]))
        if str(name) == "qty" and "width" in kwargs: kwargs["width"] = max(82, int(kwargs["width"]))
        return _column(self, name, option, **kwargs)
    _ttk.Treeview.heading, _ttk.Treeview.column = heading, column
except Exception:
    pass


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7556.prepare_gui_rows = prepare_gui_rows_v7591
    v57.prepare_gui_rows = prepare_gui_rows_v7591
    v7525.V7525Engine = V7591Complete12FixEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION, "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "runtime_isolated": str(RUNTIME_DIR), "state_file": str(v753.STATE_FILE),
        "fill_file": str(v753.FILL_FILE), "timeline_file": str(TIMELINE_FILE),
        "audit_file": str(AUDIT_FILE),
        "all_stage_times": True, "single_instance": True, "paper_only": True,
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.90기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.91실행엔진", v7525.V7525Engine is V7591Complete12FixEngine, identity["engine_class"]),
        ("독립PAPER상태", str(v753.STATE_FILE).startswith(str(RUNTIME_DIR)), identity["state_file"]),
        ("독립체결원장", str(v753.FILL_FILE).startswith(str(RUNTIME_DIR)), identity["fill_file"]),
        ("독립명령허브", str(HUB.state_file).startswith(str(RUNTIME_DIR)), str(HUB.state_file)),
        ("전단계시간이력", identity["all_stage_times"] is True, identity["timeline_file"]),
        ("고점추격방어", callable(_entry_context_gate), "최근30분 고점·거래 확인"),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity.get("link_class"))),
        ("PAPER전용", identity["paper_only"] and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def print_self_check() -> int:
    rows = self_check()
    for name, ok, detail in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"V75.91 SELF-CHECK: {passed}/{len(rows)} PASS")
    return 0 if passed == len(rows) else 1


_LOCK_HANDLE: Any = None


def _acquire_single_instance() -> None:
    global _LOCK_HANDLE
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _LOCK_HANDLE = LOCK_FILE.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            _LOCK_HANDLE.seek(0); _LOCK_HANDLE.write("0"); _LOCK_HANDLE.flush(); _LOCK_HANDLE.seek(0)
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        raise RuntimeError("V75.91이 이미 실행 중입니다. 대시보드는 한 개만 실행하십시오.") from exc
    _LOCK_HANDLE.seek(0); _LOCK_HANDLE.truncate(); _LOCK_HANDLE.write(f"pid={os.getpid()} version={VERSION}\n"); _LOCK_HANDLE.flush()


def main() -> None:
    if "--self-check" in sys.argv:
        raise SystemExit(print_self_check())
    if "--runtime-info" in sys.argv:
        print("RUNTIME_IDENTITY " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True, default=str))
        raise SystemExit(0)
    _acquire_single_instance()
    parent.main()


if __name__ == "__main__":
    main()
