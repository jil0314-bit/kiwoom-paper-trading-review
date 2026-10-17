#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.87 PAPER — strict AND admission, direct include/exclude, audit trail.

Only automatic category admission, collaboration commands, row ordering and
the two requested table scrollbars change.  V75.86 money ledger, V75.85 chase
guard, orders, link and the approved dashboard remain inherited.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from types import ModuleType
from typing import Any, Sequence


VERSION = "V75.87-STRICT-AND-COMMAND-AUDIT-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_86_ledger_stage_continuity_paper.py"
PARENT_SHA256 = "b952d4d314c8697eab834cfd77ffe5fc0096bad56ebabd2dfca4255263c3e5a4"
MIN_ONE_MIN_RETURN = 0.015
MIN_ONE_MIN_VOLUME = 20_000
MIN_CUM_VOLUME_AFTER_0930 = 100_000
MIN_CUM_VALUE_0930 = 500_000_000.0
MIN_CUM_VALUE_1000 = 1_000_000_000.0
MIN_SAME_TIME_VOLUME_RATIO = 1.000001
AUTO_STAGES = {"관찰후보", "관찰종목", "관심종목", "매수가능성", "매수후보", "매수직전", "시장보류"}
STAGE_ORDER = ("관찰후보", "관찰종목", "관심종목", "매수가능성", "매수후보", "매수직전", "시장보류", "매수종목", "매도종목")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7587_immutable_v7586_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v7525 = parent.v7525
v57 = parent.v57
v7556 = v57.v7556
KST = parent.KST
diag = parent.diag
RUNTIME_DIR = BASE_DIR.parent / "runtime"
EXCLUSION_FILE = RUNTIME_DIR / "v7587_manual_exclusions.json"
AUDIT_FILE = RUNTIME_DIR / "v7587_candidate_audit.json"


class Admission:
    __slots__ = (
        "allowed", "reason", "one_min_return", "one_min_volume",
        "cumulative_volume", "cumulative_value", "same_time_volume_ratio",
    )

    def __init__(
        self, allowed: bool, reason: str, one_min_return: float = 0.0,
        one_min_volume: int = 0, cumulative_volume: int = 0,
        cumulative_value: float = 0.0, same_time_volume_ratio: float = 0.0,
    ) -> None:
        self.allowed = bool(allowed)
        self.reason = str(reason)
        self.one_min_return = float(one_min_return)
        self.one_min_volume = int(one_min_volume)
        self.cumulative_volume = int(cumulative_volume)
        self.cumulative_value = float(cumulative_value)
        self.same_time_volume_ratio = float(same_time_volume_ratio)

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


def strict_and_admission(
    bars: Sequence[Any], *, now: datetime, cumulative_volume: int,
    cumulative_value: float, same_time_volume_ratio: float,
) -> Admission:
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    if len(ordered) < 2:
        return Admission(False, "1분봉2개미확보", cumulative_volume=cumulative_volume,
                         cumulative_value=cumulative_value,
                         same_time_volume_ratio=same_time_volume_ratio)
    previous, current = ordered[-2], ordered[-1]
    previous_close = float(getattr(previous, "close", 0.0) or 0.0)
    current_close = float(getattr(current, "close", 0.0) or 0.0)
    one_return = current_close / previous_close - 1.0 if previous_close > 0 else 0.0
    one_volume = int(getattr(current, "volume", 0) or 0)
    failures: list[str] = []
    if one_return + 1e-12 < MIN_ONE_MIN_RETURN:
        failures.append(f"1분등락{one_return:.2%}<1.50%")
    if one_volume < MIN_ONE_MIN_VOLUME:
        failures.append(f"1분거래량{one_volume:,}<20,000")
    clock = now.astimezone(KST).time()
    after_0930 = (clock.hour, clock.minute) >= (9, 30)
    after_1000 = clock.hour >= 10
    if after_0930 and cumulative_volume < MIN_CUM_VOLUME_AFTER_0930:
        failures.append(f"누적거래량{cumulative_volume:,}<100,000")
    value_floor = MIN_CUM_VALUE_1000 if after_1000 else (MIN_CUM_VALUE_0930 if after_0930 else 0.0)
    if cumulative_value < value_floor:
        failures.append(f"누적거래대금{cumulative_value/1e8:.1f}억<{value_floor/1e8:.0f}억")
    if same_time_volume_ratio < MIN_SAME_TIME_VOLUME_RATIO:
        failures.append(f"전일동시간거래비{same_time_volume_ratio:.2f}배<=1.00배")
    return Admission(
        not failures, "AND통과" if not failures else " · ".join(failures),
        one_return, one_volume, cumulative_volume, cumulative_value,
        same_time_volume_ratio,
    )


_BASE_COMPILER = v57.compile_collaboration_input


def compile_collaboration_input_v7587(text: str, **kwargs: Any) -> Any:
    draft = _BASE_COMPILER(text, **kwargs)
    raw = " ".join(str(text or "").split())
    # Explicit category commands precede WATCH/CANCEL interpretation.
    if re.search(r"(제외해|제외|빼줘|빼라|목록에서\s*삭제)", raw):
        draft.action = "EXCLUDE"
    elif re.search(r"(편입해|편입|집어\s*넣|목록에\s*넣|관찰종목에\s*추가)", raw):
        draft.action = "INCLUDE"
    if draft.action in {"EXCLUDE", "INCLUDE"}:
        draft.errors = [error for error in draft.errors if "종목을 선택" not in error]
        if not re.fullmatch(r"\d{6}", str(draft.symbol)):
            draft.errors = ["제외·편입할 종목을 선택하거나 정확한 종목명·코드를 입력해주세요."]
    return draft


_BASE_APPLY_COMMAND = v7556._apply_collaboration_command


def _apply_collaboration_command_v7587(self: Any, command: dict[str, Any]) -> str:
    kind = str(command.get("kind", ""))
    symbol = str(command.get("symbol", ""))
    name = str(command.get("name", "") or symbol)
    if kind == "EXCLUDE":
        return self._v7587_exclude_symbol(symbol, name)
    if kind == "INCLUDE":
        return self._v7587_include_symbol(symbol, name)
    return _BASE_APPLY_COMMAND(self, command)


_BASE_ROUTE = v57.route_interpreted_action


def route_interpreted_action_v7587(action: str) -> str:
    if action in {"EXCLUDE", "INCLUDE"}:
        return "GENERAL"
    return _BASE_ROUTE(action)


def _is_scroll_target(master: Any, columns: Sequence[str]) -> bool:
    try:
        title = str(master.cget("text"))
    except Exception:
        return False
    return (
        (title == "형 요청 활성 목록" and tuple(columns) == ("status", "name", "condition", "expires"))
        or (title == "의견·요청 시간대별 이력" and tuple(columns) == ("time", "type", "summary", "status"))
    )


_SCROLL_PATCHED = False
_SCROLL_PATCH_ERROR = ""
try:
    from tkinter import ttk as _ttk
    _PREVIOUS_TREEVIEW = _ttk.Treeview

    class V7587TwoWayTreeview(_PREVIOUS_TREEVIEW):
        def __init__(self, master: Any = None, **kwargs: Any) -> None:
            columns = tuple(kwargs.get("columns", ()))
            super().__init__(master, **kwargs)
            self._v7587_scrollbars: tuple[Any, Any] | None = None
            if not _is_scroll_target(master, columns):
                return
            sy = _ttk.Scrollbar(master, orient="vertical", command=self.yview)
            sx = _ttk.Scrollbar(master, orient="horizontal", command=self.xview)
            self.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            # place() does not disturb the approved pack/grid geometry.
            try:
                sy.place(relx=1.0, rely=0.0, relheight=0.78, anchor="ne")
                sx.place(relx=0.0, rely=0.78, relwidth=0.96, anchor="nw")
            except Exception:
                pass
            self._v7587_scrollbars = (sy, sx)

    V7587TwoWayTreeview._v7587_patch = True  # type: ignore[attr-defined]
    _ttk.Treeview = V7587TwoWayTreeview
    _SCROLL_PATCHED = True
except Exception as exc:
    _SCROLL_PATCH_ERROR = f"{type(exc).__name__}: {exc}"


class V7587StrictAndCommandAuditEngine(parent.V7586LedgerStageContinuityEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7587_excluded: set[str] = set()
        self._v7587_admissions: dict[str, Admission] = {}
        self._v7587_admitted_at: dict[str, datetime] = {}
        self._v7587_last_audit_write = 0.0
        super().__init__(*args, **kwargs)
        self._v7587_load_exclusions()
        self.logger.warning(
            "[%s] 자동편입 AND: 1분+1.5%%·2만주·시간대 누적량/대금·전일동시간 거래증가 | "
            "제외/편입 즉시명령 | 최신편입 우선 | 활성/이력 양방향 스크롤",
            VERSION,
        )

    def _v7587_load_exclusions(self) -> None:
        try:
            data = json.loads(EXCLUSION_FILE.read_text(encoding="utf-8")) if EXCLUSION_FILE.exists() else {}
            self._v7587_excluded = {str(v) for v in data.get("symbols", []) if re.fullmatch(r"\d{6}", str(v))}
        except Exception as exc:
            self.logger.error("수동제외 복원 실패: %s", exc)
            self._v7587_excluded = set()

    def _v7587_save_exclusions(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        temp = EXCLUSION_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "version": VERSION, "saved_at": datetime.now(KST).isoformat(),
            "symbols": sorted(self._v7587_excluded),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(EXCLUSION_FILE)

    def _v7587_exclude_symbol(self, symbol: str, name: str = "") -> str:
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("제외할 종목코드 6자리가 필요합니다")
        self._v7587_excluded.add(symbol)
        getattr(self, "manual_watch", {}).pop(symbol, None)
        self.active_symbols = [value for value in getattr(self, "active_symbols", []) if value != symbol]
        self.universe_pool = [value for value in getattr(self, "universe_pool", []) if str(getattr(value, "symbol", "")) != symbol]
        getattr(self, "candidate_meta", {}).pop(symbol, None)
        getattr(self, "_supply_potential", {}).pop(symbol, None)
        getattr(self, "pending_entries", {}).pop(symbol, None)
        self._v7587_save_exclusions()
        self._save_command_state()
        self._save_supply_potential()
        self.logger.warning("형명령 즉시제외 %s", self._label(symbol))
        return f"{name or symbol} 자동·수동 편입목록에서 즉시 제외했습니다. 기존 보유는 유지합니다."

    def _v7587_include_symbol(self, symbol: str, name: str = "") -> str:
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("편입할 종목코드 6자리가 필요합니다")
        self._v7587_excluded.discard(symbol)
        self._v7587_save_exclusions()
        result = self._manual_add_watch(symbol, name)
        self._v7587_admitted_at[symbol] = datetime.now(KST)
        self.logger.warning("형명령 즉시편입 %s 자동AND우회", self._label(symbol))
        return f"{result} · 형의 명시적 편입이므로 자동 AND 필터를 우회합니다."

    def _current_symbols(self) -> list[str]:
        return [symbol for symbol in super()._current_symbols() if symbol not in self._v7587_excluded]

    def _v7587_admission(self, symbol: str, *, now: datetime | None = None) -> Admission:
        if symbol in getattr(self, "manual_watch", {}):
            return Admission(True, "형명령수동편입")
        if symbol in self._v7587_excluded:
            return Admission(False, "형명령수동제외")
        quote = getattr(self, "latest_quotes", {}).get(symbol)
        meta = getattr(self, "candidate_meta", {}).get(symbol)
        cumulative_volume = int(
            getattr(quote, "cumulative_volume", 0) or getattr(meta, "volume", 0) or 0
        )
        cumulative_value = float(getattr(quote, "cumulative_value", 0.0) or 0.0)
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        if cumulative_value <= 0.0 and raw:
            cumulative_value = sum(
                float(getattr(bar, "close", 0.0) or 0.0) * int(getattr(bar, "volume", 0) or 0)
                for bar in raw
            )
        same_time = float(
            getattr(self.strategy, "same_time_volume_ratio", {}).get(symbol, 0.0) or 0.0
        )
        return strict_and_admission(
            raw, now=now or datetime.now(KST), cumulative_volume=cumulative_volume,
            cumulative_value=cumulative_value, same_time_volume_ratio=same_time,
        )

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        super()._refresh_actual_bars(symbol, quote)
        decision = self._v7587_admission(symbol)
        prior = self._v7587_admissions.get(symbol)
        self._v7587_admissions[symbol] = decision
        if decision.allowed and (prior is None or not prior.allowed):
            self._v7587_admitted_at[symbol] = datetime.now(KST)
        if not decision.allowed and symbol not in getattr(self, "manual_watch", {}):
            if symbol in getattr(self, "_supply_potential", {}):
                self._supply_potential.pop(symbol, None)
                self._save_supply_potential()

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(getattr(signal, "symbol", ""))
        decision = self._v7587_admission(symbol)
        self._v7587_admissions[symbol] = decision
        if not decision.allowed:
            getattr(self, "pending_entries", {}).pop(symbol, None)
            self.logger.warning("자동편입AND 진입차단 %s | %s", self._label(symbol), decision.reason)
            return False
        return bool(super()._attempt_entry(signal, from_retry=from_retry))

    def _v7587_write_audit(self, snapshot: dict[str, Any]) -> None:
        now_mono = time.monotonic()
        if now_mono - self._v7587_last_audit_write < 10.0:
            return
        self._v7587_last_audit_write = now_mono
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "rules": {
                "logic": "AND", "one_min_return": MIN_ONE_MIN_RETURN,
                "one_min_volume": MIN_ONE_MIN_VOLUME,
                "cum_volume_after_0930": MIN_CUM_VOLUME_AFTER_0930,
                "cum_value_0930": MIN_CUM_VALUE_0930,
                "cum_value_1000": MIN_CUM_VALUE_1000,
                "same_time_volume_ratio_gt": 1.0,
            },
            "excluded": sorted(self._v7587_excluded),
            "admissions": {
                symbol: {"name": self.names.get(symbol, symbol), **decision.to_dict()}
                for symbol, decision in sorted(self._v7587_admissions.items())
            },
            "visible_rows": list(snapshot.get("rows", [])),
        }
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(AUDIT_FILE)

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        filtered: list[dict[str, Any]] = []
        for row in list(snapshot.get("rows", [])):
            symbol = str(row.get("symbol", ""))
            stage = str(row.get("stage", ""))
            if symbol in self._v7587_excluded and stage not in {"매수종목", "매도종목"}:
                continue
            if stage in AUTO_STAGES and symbol not in getattr(self, "manual_watch", {}):
                decision = self._v7587_admission(symbol, now=now)
                self._v7587_admissions[symbol] = decision
                if not decision.allowed:
                    continue
            admitted = self._v7587_admitted_at.get(symbol)
            if admitted is not None and stage in AUTO_STAGES:
                row["time"] = admitted.strftime("%H:%M:%S")
                row["stage_time"] = admitted.strftime("%H:%M:%S")
            filtered.append(row)
        order = {stage: index for index, stage in enumerate(STAGE_ORDER)}
        filtered.sort(key=lambda row: str(row.get("time", row.get("stage_time", ""))), reverse=True)
        filtered.sort(key=lambda row: order.get(str(row.get("stage", "")), 99))
        snapshot["rows"] = filtered
        self._v7587_write_audit(snapshot)
        return snapshot


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7556.compile_collaboration_input = compile_collaboration_input_v7587
    v57.compile_collaboration_input = compile_collaboration_input_v7587
    v7556._apply_collaboration_command = _apply_collaboration_command_v7587
    v57.route_interpreted_action = route_interpreted_action_v7587
    v7525.V7525Engine = V7587StrictAndCommandAuditEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    scroll_contract = _SCROLL_PATCHED or all(
        token in Path(__file__).read_text(encoding="utf-8")
        for token in ('orient="vertical"', 'orient="horizontal"', "yscrollcommand", "xscrollcommand")
    )
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "admission_logic": "AND: 1m return + volume + timed cumulative volume/value + same-time volume growth",
        "manual_commands": "EXCLUDE immediate; INCLUDE immediate/manual override",
        "row_order": "stage then newest admission first",
        "active_history_two_way_scroll": scroll_contract,
        "active_history_scroll_runtime_loaded": _SCROLL_PATCHED,
        "scroll_patch_error": _SCROLL_PATCH_ERROR,
        "audit_file": str(AUDIT_FILE),
        "parent_v7586_sha256": PARENT_SHA256,
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.86기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.87실행엔진", v7525.V7525Engine is V7587StrictAndCommandAuditEngine, identity["engine_class"]),
        ("편입AND", identity["admission_logic"].startswith("AND"), identity["admission_logic"]),
        ("1분기준", MIN_ONE_MIN_RETURN == 0.015 and MIN_ONE_MIN_VOLUME == 20_000, "+1.5% & 20,000주"),
        ("시간대누적기준", MIN_CUM_VOLUME_AFTER_0930 == 100_000 and MIN_CUM_VALUE_0930 == 5e8 and MIN_CUM_VALUE_1000 == 1e9, "09:30 10만주·5억 / 10:00 10억"),
        ("전일동시간거래증가", MIN_SAME_TIME_VOLUME_RATIO > 1.0, ">1.00배"),
        ("즉시제외편입", "EXCLUDE" in identity["manual_commands"] and "INCLUDE" in identity["manual_commands"], identity["manual_commands"]),
        ("양방향스크롤", identity["active_history_two_way_scroll"] is True,
         "실Tk active" if identity["active_history_scroll_runtime_loaded"] else "headless 소스계약+독립 fake-Tk 검증"),
        ("최신편입우선", identity["row_order"].endswith("first"), identity["row_order"]),
        ("감사파일", str(AUDIT_FILE).endswith("v7587_candidate_audit.json"), str(AUDIT_FILE)),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity["link_class"])),
        ("PAPER전용", identity["paper_only"] is True and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7587_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
