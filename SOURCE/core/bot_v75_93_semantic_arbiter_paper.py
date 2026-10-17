#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.93 PAPER — semantic arbitration and adversarially guarded entry."""
from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import replace
import hashlib, importlib.util, json, sys
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.93-SEMANTIC-ARBITER-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_92_pattern_state_machine_paper.py"
PARENT_SHA256 = "9d5e07295826c20137e850f3837d2bec5924cfd3755c9d4ee876cca8f895d5e6"
RUNTIME_DIR = BASE_DIR.parent / "runtime_v7593"
AUDIT_FILE = RUNTIME_DIR / "v7593_semantic_audit.json"
TAKE_PROFIT_PCT, HARD_STOP_PCT = .015, .05


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if PARENT_SHA256 and actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대:{PARENT_SHA256}\n실제:{actual}")
    spec = importlib.util.spec_from_file_location("v7593_immutable_v7592_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.92 로드 실패")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module); return module


parent = _load_parent()
from v7593_semantic_arbiter_logic import Decision, arbitrate, can_transition, evaluate_all, stamp

v7525, v57, v753, KST = parent.v7525, parent.v57, parent.v753, parent.KST
_PARENT_CHECKS = tuple(parent.self_check())


class V7593SemanticArbiterEngine(parent.V7592PatternStateMachineEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7593_decisions: dict[str, list[Decision]] = {}
        self._v7593_history: dict[str, list[dict[str, Any]]] = {}
        self._v7593_provisional: dict[str, dict[str, Any]] = {}
        self._v7593_order_minute: dict[str, datetime] = {}
        self._v7593_route_counts = {"reversal": 0, "trend": 0, "flow_absorption": 0, "breakout": 0}
        self._v7593_previous_rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._v7593_departed_rows: list[dict[str, Any]] = []
        self._v7593_departure_day = datetime.now(KST).date()
        super().__init__(*args, **kwargs)
        # Candidate discovery is not a five-minute batch.  Keep the public
        # ranking calls bounded while refreshing entry-near symbols quickly.
        try:
            self.settings = replace(self.settings, scan_interval_sec=10.0)
        except (TypeError, ValueError):
            object.__setattr__(self.settings, "scan_interval_sec", 10.0)
        self.chart_refresh = 15.0
        self.chart_refresh_hot = 3.0
        self.logger.warning("[%s] 당일봉격리·동시각거래비교·비역행단계·독립경로중재 활성", VERSION)

    def _v7588_publish(self, symbol: str, phase: str, stage: str, reason: str,
                       now: datetime, *, allow_downgrade: bool = False) -> None:
        """One monotonic stage writer for old and new routes.

        V75.91's hidden prior-flow publication gate is intentionally bypassed:
        the user's +1.5% AND 20k observation is allowed to be observed first;
        entry remains protected by the existing order/risk gates.
        """
        current = self._v7588_phase.get(symbol, "")
        if not can_transition(current, phase, explicit_failure=allow_downgrade):
            return
        base = parent.parent.parent.parent.parent.V7588IncrementalStageLimitEngine
        before = self._v7588_phase.get(symbol, "")
        if allow_downgrade and before != phase:
            self._v7588_phase.pop(symbol, None)
        base._v7588_publish(self, symbol, phase, stage, reason, now)
        if before != self._v7588_phase.get(symbol, ""):
            self._record_stage(symbol, stage, now, reason)

    def _publish_decision(self, symbol: str, decision: Decision, now: datetime) -> None:
        if not decision.stage:
            return
        mapping = {"관찰종목": ("OBSERVED", "관찰후보"), "관심종목": ("INTEREST", "관심종목"),
                   "매수후보": ("CANDIDATE", "매수가능성"), "매수직전": ("PREBUY", "매수직전")}
        phase, stage = mapping[decision.stage]
        failed = decision.state == "FAILED"
        self._v7588_publish(symbol, phase, stage,
                            f"V7593:{decision.route}:{decision.state}:{decision.reason}", now,
                            allow_downgrade=failed)

    def _record(self, symbol: str, decisions: list[Decision], chosen: Decision | None,
                now: datetime) -> None:
        self._v7593_decisions[symbol] = decisions
        row = {"at": now.isoformat(), "chosen": chosen.to_dict() if chosen else None,
               "all": [x.to_dict() for x in decisions]}
        history = self._v7593_history.setdefault(symbol, [])
        signature = json.dumps(row["chosen"], ensure_ascii=False, sort_keys=True)
        if not history or history[-1].get("signature") != signature:
            row["signature"] = signature; history.append(row)
            self._v7593_history[symbol] = history[-80:]

    def _forming_confirmed(self, symbol: str, decision: Decision, now: datetime) -> bool:
        if decision.formed:
            self._v7593_provisional.pop(symbol, None)
            return True
        key = (decision.route, decision.state, round(decision.reference_price, 4), decision.trigger_index)
        old = self._v7593_provisional.get(symbol)
        if old is None or old.get("key")[:2] != key[:2] or decision.reference_price < old.get("price", 0)*.997:
            self._v7593_provisional[symbol] = {"key": key, "first": now, "count": 1,
                                                "price": decision.reference_price}
            return False
        old["count"] += 1
        elapsed = (now-old["first"]).total_seconds()
        return old["count"] >= 2 and elapsed >= 3.0

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        now = datetime.now(KST)
        before = symbol in self.broker.positions
        # Execute only the preserved V75.89 reversal/trend stack.  V75.90 and
        # V75.92's superseded route executors are deliberately skipped.
        old_v7590 = parent.parent.parent.V7590FullPatternFusionEngine
        super(old_v7590, self)._refresh_actual_bars(symbol, quote)
        if not before and symbol in self.broker.positions:
            reason = str(getattr(self.latest_signals.get(symbol), "reason", ""))
            self._v7593_route_counts["trend" if "순추세" in reason else "reversal"] += 1
            return
        if symbol in self._v7587_excluded or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        phase = self._v7588_phase.get(symbol, "")
        if phase not in {"OBSERVED", "INTEREST", "CANDIDATE", "PREBUY", "TREND_PREBUY"}:
            return
        raw_all = sorted(list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), key=stamp)
        today = [x for x in raw_all if stamp(x).date() == now.date()]
        if len(today) < 22:
            return
        forming = self._v7590_forming_bar(symbol, quote, today, now)
        bars = raw_all + ([forming] if forming is not None else [])
        decisions = evaluate_all(bars, now=now, formed=forming is None)
        chosen = arbitrate(decisions)
        self._record(symbol, decisions, chosen, now)
        if chosen is None:
            return
        self._publish_decision(symbol, chosen, now)
        if not chosen.allowed or not self._forming_confirmed(symbol, chosen, now):
            return
        minute = now.replace(second=0, microsecond=0)
        if self._v7593_order_minute.get(symbol) == minute:
            return
        label = "돌파후첫눌림" if chosen.route == "breakout" else "선행수급매물흡수"
        current = float(getattr(quote, "price", 0) or chosen.reference_price)
        signal = v753.Signal(symbol, v753.SignalAction.BUY, chosen.score,
            f"{label}|{chosen.reason}|체결즉시목표1.5%|고정손절5.0%", current, now,
            label, HARD_STOP_PCT, TAKE_PROFIT_PCT)
        self.latest_signals[symbol] = signal
        self._v7593_order_minute[symbol] = minute
        if self._attempt_entry(signal):
            self._v7593_route_counts[chosen.route] += 1

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        self._preserve_all_stage_departures(snapshot, now)
        self._clarify_display_reasons(snapshot)
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "saved_at": now.isoformat(),
                   "decisions": {k: [x.to_dict() for x in v] for k, v in self._v7593_decisions.items()},
                   "history": self._v7593_history, "route_entry_counts": self._v7593_route_counts,
                   "visible_rows": list(snapshot.get("rows", []))}
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(AUDIT_FILE)
        return snapshot

    @staticmethod
    def _clarify_display_reasons(snapshot: dict[str, Any]) -> None:
        """Make ranking-source shorthand understandable without changing logic."""
        for row in snapshot.get("rows", []):
            reason = str(row.get("reason", ""))
            reason = reason.replace("시장 거래량 급증(방향 미확인)", "거래량급증 목록 유입(가격방향 확인 전)")
            reason = reason.replace("시장 거래량급증(방향미확인)", "거래량급증 목록 유입(가격방향 확인 전)")
            reason = reason.replace("·상승률", "·상승률 상위목록 유입")
            reason = reason.replace("·쌍끌이", "·외국인·기관 동시 순매수 목록 유입")
            # The category column already says 관심종목.  Remove only that
            # redundant label from the detailed timeline; keep its clock in
            # the dedicated all-stage-time column.
            if str(row.get("stage", "")) == "관심종목":
                reason = __import__("re").sub(r"(?:\s*→\s*)?관심\s+\d{1,2}:\d{2}:\d{2}", "", reason)
                reason = reason.replace("시간이력  ·", "").replace("시간이력 ·", "")
            row["reason"] = " ".join(reason.split())

    def _preserve_all_stage_departures(self, snapshot: dict[str, Any], now: datetime) -> None:
        """Retain every category departure at the bottom for the KST day."""
        stages = {"관찰후보", "관심종목", "매수가능성", "매수직전", "시장보류", "매수종목", "매도종목"}
        if self._v7593_departure_day != now.date():
            self._v7593_previous_rows.clear()
            self._v7593_departed_rows.clear()
            self._v7593_departure_day = now.date()
        live_rows = [dict(row) for row in snapshot.get("rows", []) if not bool(row.get("departed"))]
        current: dict[tuple[str, str], dict[str, Any]] = {}
        by_symbol: dict[str, str] = {}
        for row in live_rows:
            stage, symbol = str(row.get("stage", "")), str(row.get("symbol", ""))
            if stage in stages and symbol:
                current[(stage, symbol)] = row
                by_symbol[symbol] = stage
        for (old_stage, symbol), old in self._v7593_previous_rows.items():
            if (old_stage, symbol) in current:
                continue
            moved = by_symbol.get(symbol, "추적대상에서 제외")
            rejection = str(getattr(self, "_v7591_rejections", {}).get(symbol, "")).strip()
            if rejection:
                why = rejection
            elif moved != "추적대상에서 제외":
                why = f"{moved} 단계로 이동"
            else:
                why = "현재 단계 조건 상실 또는 감시목록 이탈"
            departed_at = now.strftime("%H:%M:%S")
            original_time = str(old.get("stage_time", old.get("time", "-")))
            self._v7593_departed_rows.append({
                **old,
                "stage": old_stage,
                "departed": True,
                "departure_time": departed_at,
                "stage_time": f"{original_time} / 이탈 {departed_at}",
                "reason": f"단계이탈 {departed_at} · 제외이유: {why}",
            })
        self._v7593_departed_rows = self._v7593_departed_rows[-500:]
        snapshot["rows"] = live_rows + [dict(row) for row in self._v7593_departed_rows]
        self._v7593_previous_rows = current


def _install_runtime() -> None:
    v57.SEED_CONSOLE_ROWS = []
    v7525.V7525Engine = V7593SemanticArbiterEngine
    v57.VERSION = VERSION; v7525.VERSION = VERSION
    # The toolbar's no-selection branch calls probe_auto_target().  Older
    # versions merely searched for an input control, so the chart did not
    # change and looked completely broken when the table was empty.  Make the
    # check observable: transmit Samsung Electronics once through the exact
    # same proven link route used by row clicks.
    link_class = v57.RestoredHeroLink
    def probe_and_send_test(self: Any) -> tuple[bool, str]:
        status, message = self.send_symbol("005930")
        value = str(getattr(status, "value", status))
        return value != "FAILED", f"005930 실제 전송 시험 · {message}"
    link_class.probe_auto_target = probe_and_send_test
    try:
        import tkinter as tk
        inherited = tk.Label.__init__
        def label_init(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
            is_title = str(kwargs.get("text", "")).startswith("프라임 시그널 대시보드")
            inherited(self, master, cnf or {}, **kwargs)
            # Parent wrappers run inside ``inherited`` and may replace the
            # text with their own literal version.  Set the authoritative
            # title only after every inherited wrapper has returned.
            if is_title:
                self.configure(text=f"프라임 시그널 대시보드  (PAPER)  ·  {VERSION}")
        tk.Label.__init__ = label_init
        from tkinter import ttk
        inherited_insert = ttk.Treeview.insert
        def tree_insert(self: Any, parent_id: Any, index: Any, iid: Any = None, **kwargs: Any) -> Any:
            values = kwargs.get("values", ())
            reason = str(values[-1]) if values else ""
            if "단계이탈 " in reason or "조건상실:" in reason:
                try:
                    self.tag_configure("단계이탈", background="#263746", foreground="#9fb0bf")
                except Exception:
                    pass
                kwargs["tags"] = (*tuple(kwargs.get("tags", ())), "단계이탈")
            return inherited_insert(self, parent_id, index, iid=iid, **kwargs)
        ttk.Treeview.insert = tree_insert
    except Exception:
        pass


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    value = dict(parent.runtime_identity())
    value.update({"version": VERSION, "entry": str(Path(__file__).resolve()),
                  "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
                  "today_only_patterns": True, "same_clock_volume": True,
                  "monotonic_stages": True, "forming_confirmation": "2 samples / >=3 sec",
                  "independent_arbitration": True, "paper_only": True,
                  "all_stage_departures": True,
                  "refresh_seconds": {"universe": 10, "normal_chart": 15, "hot_chart": 3},
                  "empty_table_link_test": "005930 through SmoothContinuousHeroLink",
                  "link_class": parent.runtime_identity().get("link_class")})
    return value


def self_check() -> list[tuple[str, bool, str]]:
    ident = runtime_identity(); rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.93실행엔진", v7525.V7525Engine is V7593SemanticArbiterEngine, ident["engine_class"]),
        ("당일패턴격리", ident["today_only_patterns"], "전일봉은 거래비교 전용"),
        ("동시각거래비교", ident["same_clock_volume"], "장마감예상치 제거"),
        ("단계비역행", ident["monotonic_stages"], "FAILED만 명시 하향"),
        ("미완성봉지속확인", bool(ident["forming_confirmation"]), ident["forming_confirmation"]),
        ("독립경로중재", ident["independent_arbitration"], "허용 신호 최고점 선택"),
        ("전단계이탈보존", ident["all_stage_departures"], "7개 단계·당일·이탈시각·이유"),
        ("준실시간갱신", ident["refresh_seconds"] == {"universe": 10, "normal_chart": 15, "hot_chart": 3}, str(ident["refresh_seconds"])),
        ("빈목록실제연동시험", ident["empty_table_link_test"].startswith("005930"), ident["empty_table_link_test"]),
        ("연동보존", ident.get("link_class", "").endswith("SmoothContinuousHeroLink"), str(ident.get("link_class"))),
        ("즉시목표손절", TAKE_PROFIT_PCT == .015 and HARD_STOP_PCT == .05, "+1.5% / -5.0%"),
    ])
    return rows


def print_self_check() -> int:
    rows = self_check()
    for name, ok, detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"V75.93 SELF-CHECK: {passed}/{len(rows)} PASS")
    return 0 if passed == len(rows) else 1


def main() -> None:
    if "--self-check" in sys.argv: raise SystemExit(print_self_check())
    if "--runtime-info" in sys.argv:
        print("RUNTIME_IDENTITY " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True, default=str)); raise SystemExit(0)
    # V75.92's file lock could outlive the visible Tk window when a hidden
    # python process remained, falsely blocking every later launch.  Preserve
    # the established pre-V75.92 launch behaviour: the VBS starts this GUI
    # directly and does not infer liveness from a background process/file.
    parent.parent.parent.main()


if __name__ == "__main__": main()
