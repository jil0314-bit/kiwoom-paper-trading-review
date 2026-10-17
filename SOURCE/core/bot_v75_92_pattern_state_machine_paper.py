#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.92 PAPER — three-route state machine with preserved GUI/link/order stack."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib, importlib.util, json, os, sys
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.92-R2-NO-DEMO-CLEAN-PATTERN-STATE-MACHINE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_91_complete_12_fix_paper.py"
PARENT_SHA256 = "dfc076013ed11b32e66240f78449a002a9f515e1ec9969d1cca60923a96124e5"
RUNTIME_DIR = BASE_DIR.parent / "runtime_v7592"
AUDIT_FILE = RUNTIME_DIR / "v7592_pattern_audit.json"
LOCK_FILE = RUNTIME_DIR / "V75_92_RUNNING.lock"
TAKE_PROFIT_PCT, HARD_STOP_PCT = .015, .05

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE; actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대:{PARENT_SHA256}\n실제:{actual}")
    spec = importlib.util.spec_from_file_location("v7592_immutable_v7591_base", path)
    if spec is None or spec.loader is None: raise RuntimeError("V75.91 로드 실패")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module); return module

parent = _load_parent()
from v7592_pattern_state_logic import RouteEvidence, evaluate_routes

v7525, v57, v753, v7556, KST = parent.v7525, parent.v57, parent.v753, parent.v7556, parent.KST
_stamp = parent._stamp
_PARENT_CHECKS = tuple(parent.self_check())

def _install_isolated_runtime() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    parent.RUNTIME_DIR = RUNTIME_DIR
    parent.TIMELINE_FILE = RUNTIME_DIR / "stage_timeline.json"
    parent.AUDIT_FILE = RUNTIME_DIR / "v7591_compat_audit.json"
    parent.LOCK_FILE = LOCK_FILE
    parent.parent.AUDIT_FILE = RUNTIME_DIR / "v7590_compat_audit.json"
    v753.LOG_DIR = RUNTIME_DIR / "paper_ledger"
    v753.STATE_FILE = v753.LOG_DIR / "paper_state.json"
    v753.FILL_FILE = v753.LOG_DIR / "paper_fills.csv"
    v753.PATTERN_STATE_FILE = v753.LOG_DIR / "pattern_state.json"
    v753.ERROR_FILE = v753.LOG_DIR / "api_errors.txt"
    v57.TARGET_SOURCE_PATH = RUNTIME_DIR / "target_source_ledger.json"
    fresh = v7556.CollaborationHub(RUNTIME_DIR/"collaboration_state.json", RUNTIME_DIR/"collaboration_events.jsonl")
    v7556.HUB = fresh; v57.HUB = fresh; parent.HUB = fresh
    v88 = parent.parent.parent.parent
    v88.HUB = fresh; v88._BASE_HUB_SNAPSHOT = fresh.snapshot; fresh.snapshot = v88._hub_snapshot_v7588
    original = v57.IntentLedger
    class IsolatedIntentLedger(original):
        def __init__(self, _ignored: Any = None) -> None:
            super().__init__(RUNTIME_DIR / "user_intent_ledger.json")
    v57.IntentLedger = IsolatedIntentLedger

_install_isolated_runtime()
HUB = v7556.HUB

class V7592PatternStateMachineEngine(parent.V7591Complete12FixEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7592_state: dict[str, RouteEvidence] = {}
        self._v7592_history: dict[str, list[dict[str, Any]]] = {}
        self._v7592_order_minute: dict[str, datetime] = {}
        self._v7592_route_counts = {"reversal": 0, "trend": 0, "flow_absorption": 0, "breakout": 0}
        super().__init__(*args, **kwargs)
        self.logger.warning("[%s] 세 경로 분리·돌파실패/재회복·전일거래200%%·1분 선제판단 활성", VERSION)

    def _record_route(self, symbol: str, decision: RouteEvidence, now: datetime) -> None:
        previous = self._v7592_state.get(symbol)
        self._v7592_state[symbol] = decision
        if previous is None or (previous.route, previous.state, previous.reason) != (decision.route, decision.state, decision.reason):
            history = self._v7592_history.setdefault(symbol, [])
            history.append({"at": now.isoformat(), **decision.to_dict()})
            self._v7592_history[symbol] = history[-80:]

    def _publish_route_state(self, symbol: str, decision: RouteEvidence, now: datetime) -> None:
        mapping = {
            "관찰종목": ("OBSERVED", "관찰후보"), "관심종목": ("INTEREST", "관심종목"),
            "매수후보": ("CANDIDATE", "매수가능성"), "매수직전": ("PREBUY", "매수직전"),
        }
        phase, stage = mapping.get(decision.stage, ("INTEREST", "관심종목"))
        current = self._v7588_phase.get(symbol, "")
        if decision.state == "FAILED" and current in {"CANDIDATE", "PREBUY"}:
            # Failed breakouts stay visible and are downgraded, never erased.
            self._v7588_phase.pop(symbol, None)
        self._v7588_publish(symbol, phase, stage, f"V7592:{decision.route}:{decision.state}:{decision.reason}", now)

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        now = datetime.now(KST)
        before = symbol in self.broker.positions
        # Keep V75.89 legacy reversal/trend and all older risk/order logic, but
        # deliberately skip V75.90's superseded stateless three-route executor.
        super(parent.parent.V7590FullPatternFusionEngine, self)._refresh_actual_bars(symbol, quote)
        if not before and symbol in self.broker.positions:
            reason = str(getattr(self.latest_signals.get(symbol), "reason", ""))
            route = "trend" if "순추세" in reason else "reversal"
            self._v7592_route_counts[route] += 1
            return
        if symbol in self._v7587_excluded or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        phase = self._v7588_phase.get(symbol, "")
        if phase not in {"OBSERVED", "INTEREST", "CANDIDATE", "PREBUY", "TREND_PREBUY"}:
            return
        raw = sorted(list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])), key=_stamp)
        if len(raw) < 22: return
        forming = self._v7590_forming_bar(symbol, quote, raw, now)
        bars = raw + ([forming] if forming is not None else [])
        decision = evaluate_routes(bars, now=now, formed=forming is None)
        self._record_route(symbol, decision, now)
        self._publish_route_state(symbol, decision, now)
        if not decision.allowed: return
        minute = now.replace(second=0, microsecond=0)
        if self._v7592_order_minute.get(symbol) == minute: return
        label = "돌파후첫눌림" if decision.route == "breakout" else "선행수급매물흡수"
        current = float(getattr(quote, "price", 0) or decision.reference_price)
        signal = v753.Signal(symbol, v753.SignalAction.BUY, decision.score,
            f"{label}|{decision.reason}|체결즉시목표1.5%|고정손절5.0%", current, now,
            label, HARD_STOP_PCT, TAKE_PROFIT_PCT)
        self.latest_signals[symbol] = signal; self._v7592_order_minute[symbol] = minute
        if self._attempt_entry(signal): self._v7592_route_counts[decision.route] += 1

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        payload = {
            "version": VERSION, "saved_at": datetime.now(KST).isoformat(),
            "routes": {k: v.to_dict() for k, v in sorted(self._v7592_state.items())},
            "route_history": self._v7592_history, "route_entry_counts": self._v7592_route_counts,
            "visible_rows": list(snapshot.get("rows", [])),
            "invariants": {"paper_only": True, "target": .015, "stop": .05,
                           "old_breakout_executor_skipped": True,
                           "link_class": runtime_identity().get("link_class")},
        }
        temp = AUDIT_FILE.with_suffix(".tmp"); temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); temp.replace(AUDIT_FILE)
        return snapshot

def _install_runtime() -> None:
    # Never replace an empty live snapshot with the old GUI demonstration
    # portfolio.  Empty means empty, including index/API error periods.
    v57.SEED_CONSOLE_ROWS = []
    v7525.V7525Engine = V7592PatternStateMachineEngine
    v57.VERSION = VERSION; v7525.VERSION = VERSION

    # V75.90 installed a label wrapper with its literal version text.  Install
    # the final wrapper last so the visible title always identifies this exact
    # runtime rather than an inherited parent.
    try:
        import tkinter as tk
        inherited_label_init = tk.Label.__init__
        def label_init_v7592(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
            if str(kwargs.get("text", "")).startswith("프라임 시그널 대시보드"):
                kwargs["text"] = f"프라임 시그널 대시보드  (PAPER)  ·  {VERSION}"
            inherited_label_init(self, master, cnf or {}, **kwargs)
        tk.Label.__init__ = label_init_v7592
    except Exception:
        pass

_install_runtime()

def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({"version":VERSION, "entry":str(Path(__file__).resolve()),
        "engine_class":f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "routes":["reversal_exhaustion","trend_continuation","flow_absorption","breakout_state_machine"],
        "runtime_isolated":str(RUNTIME_DIR), "audit_file":str(AUDIT_FILE),
        "timeline_file":str(parent.TIMELINE_FILE),
        "old_breakout_executor_skipped":True, "paper_only":True,
        "demo_rows_disabled":len(getattr(v57,"SEED_CONSOLE_ROWS",[]))==0,
        "link_exact_parent":parent.runtime_identity()["link_exact_parent"]})
    return identity

def self_check() -> list[tuple[str,bool,str]]:
    ident=runtime_identity(); rows=list(_PARENT_CHECKS)
    rows.extend([
        ("V75.91기준SHA",sha256_file(BASE_DIR/PARENT_FILE)==PARENT_SHA256,PARENT_SHA256[:16]),
        ("V75.92실행엔진",v7525.V7525Engine is V7592PatternStateMachineEngine,ident["engine_class"]),
        ("세매매철학분리",len(ident["routes"])==4,",".join(ident["routes"])),
        ("구돌파실행기교체",ident["old_breakout_executor_skipped"],"V75.89→V75.92"),
        ("독립PAPER원장",str(v753.STATE_FILE).startswith(str(RUNTIME_DIR)),str(v753.STATE_FILE)),
        ("시연37종목완전차단",ident["demo_rows_disabled"],"빈 실시간 화면은 0종목으로 표시"),
        ("연동보존",ident["link_exact_parent"] is True,str(ident.get("link_class"))),
        ("즉시목표·손절보존",TAKE_PROFIT_PCT==.015 and HARD_STOP_PCT==.05,"+1.5% / -5.0%"),
        ("PAPER전용",ident["paper_only"] and VERSION.endswith("PAPER"),VERSION),
    ]); return rows

def print_self_check() -> int:
    rows=self_check()
    for name,ok,detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed=sum(1 for _,ok,_ in rows if ok); print(f"V75.92 SELF-CHECK: {passed}/{len(rows)} PASS")
    return 0 if passed==len(rows) else 1

_LOCK_HANDLE: Any = None
def _acquire_single_instance() -> None:
    global _LOCK_HANDLE
    _LOCK_HANDLE=LOCK_FILE.open("a+",encoding="utf-8")
    try:
        if os.name=="nt":
            import msvcrt; _LOCK_HANDLE.seek(0); _LOCK_HANDLE.write("0"); _LOCK_HANDLE.flush(); _LOCK_HANDLE.seek(0); msvcrt.locking(_LOCK_HANDLE.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl; fcntl.flock(_LOCK_HANDLE.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,BlockingIOError) as exc: raise RuntimeError("V75.92가 이미 실행 중입니다. 대시보드는 한 개만 실행하십시오.") from exc

def main() -> None:
    if "--self-check" in sys.argv: raise SystemExit(print_self_check())
    if "--runtime-info" in sys.argv: print("RUNTIME_IDENTITY "+json.dumps(runtime_identity(),ensure_ascii=False,sort_keys=True,default=str)); raise SystemExit(0)
    _acquire_single_instance(); parent.parent.main()

if __name__=="__main__": main()
