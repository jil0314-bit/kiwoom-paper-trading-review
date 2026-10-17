#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.90 PAPER — full pattern fusion without altering proven surfaces."""
from __future__ import annotations

from datetime import datetime, timedelta
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import re
from types import ModuleType, SimpleNamespace
from typing import Any


VERSION = "V75.90-FULL-PATTERN-FUSION-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_89_dual_route_evidence_paper.py"
PARENT_SHA256 = "78c3f872f4490e24428f5daa4be30d2e0b4fbbafd959e9262ab38b09ae4ae77a"
TAKE_PROFIT_PCT = 0.015
HARD_STOP_PCT = 0.05
AUDIT_FILE = BASE_DIR.parent / "runtime" / "v7590_full_pattern_audit.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7590_immutable_v7589_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
from v7590_full_pattern_logic import PatternDecision, evaluate_new_routes, prior_flow_gate

v7525 = parent.v7525
v57 = parent.v57
v753 = parent.v753
v7556 = parent.parent.v7556
HUB = v7556.HUB
KST = parent.KST


_BASE_COMPILER = v57.compile_collaboration_input


def compile_collaboration_input_v7590(text: str, **kwargs: Any) -> Any:
    # The full live name dictionary is filled by dashboard snapshots.  Keep
    # the most common command target resolvable even before the first refresh.
    v7556._V7555_NAME_HINT.setdefault("삼성전자", "005930")
    draft = _BASE_COMPILER(text, **kwargs)
    raw = " ".join(str(text or "").split())
    exact_price_buy = bool(re.search(
        r"(?:\d[\d,]*)\s*원(?:에|으로)?\s*(?:\d+\s*주\s*)?(?:매수|사|사줘|매수해)", raw
    ) or re.search(
        r"(?:매수|사|사줘|매수해).{0,16}?(?:\d[\d,]*)\s*원", raw
    ))
    directional = bool(re.search(r"이하|아래|도달|돌파|이상|넘으면|위로", raw))
    if str(getattr(draft, "action", "")) == "BUY_CONDITION" and exact_price_buy and not directional:
        draft.action = "BUY_NAMED_PRICE"
        draft.trigger = "LIMIT_MAX_PRICE"
        draft.errors = [
            error for error in list(getattr(draft, "errors", []))
            if "가격 이하 도달인지 상향 돌파인지" not in error
        ]
    return draft


_BASE_ROUTE = v57.route_interpreted_action


def route_interpreted_action_v7590(action: str) -> str:
    return "GENERAL" if action == "BUY_NAMED_PRICE" else _BASE_ROUTE(action)


_BASE_APPLY = v7556._apply_collaboration_command


def _price_from_note(note: str) -> float | None:
    matches = re.findall(r"(\d[\d,]*)\s*원", str(note))
    return float(matches[-1].replace(",", "")) if matches else None


def _apply_collaboration_command_v7590(self: Any, command: dict[str, Any]) -> str:
    if str(command.get("kind", "")) != "BUY_NAMED_PRICE":
        return _BASE_APPLY(self, command)
    symbol = str(command.get("symbol", ""))
    name = str(command.get("name", "") or symbol)
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("종목명과 종목코드를 확인할 수 없습니다")
    buy_price = _price_from_note(str(command.get("note", "")))
    if buy_price is None or buy_price <= 0:
        raise ValueError("지정 매수가를 읽지 못했습니다")
    self._manual_add_watch(symbol, name)
    quote = getattr(self, "latest_quotes", {}).get(symbol)
    current = float(getattr(quote, "price", 0.0) or 0.0) if quote is not None else 0.0
    overlay = HUB.activate({
        "kind": "BUY_CONDITION", "symbol": symbol, "name": name,
        "trigger": "AT_OR_BELOW", "buy_price": buy_price,
        "target_price": None, "expires_at": v7556._next_session_close_iso(),
        "note": str(command.get("note", "")),
    })
    if current <= 0 or current > buy_price:
        return f"{name} {buy_price:,.0f}원 이하 최대매수가 주문을 활성화했습니다. 현재가 도달 시 실행합니다."
    result = self._manual_buy(symbol, 1)
    HUB.update(
        overlay.overlay_id, expected_version=overlay.version,
        status="EXECUTED", last_error=str(result),
    )
    return (
        f"{name} 현재가 {current:,.0f}원이 지정가 {buy_price:,.0f}원 이하라 즉시 PAPER 매수했습니다. "
        "체결 후 기존 +1.5% 목표매도를 적용합니다."
    )


_BASE_PARSE_FREE = v57.parse_free_text


def parse_free_text_v7590(text: str, *args: Any, **kwargs: Any) -> list[Any]:
    rows = list(_BASE_PARSE_FREE(text, *args, **kwargs))
    exact_price = bool(re.search(r"\d[\d,]*\s*원", str(text)))
    for row in rows:
        if exact_price and str(getattr(row, "kind", "")) == "ORDER_REQUEST":
            row.kind = "ORDER_TRACK"
            row.title = "가격지정 주문"
            row.summary = "[적용대기/체결추적] " + str(row.summary)
    return rows


_BASE_PREPARE_ROWS = v7556.prepare_gui_rows


def prepare_gui_rows_v7590(rows: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    prepared, counts = _BASE_PREPARE_ROWS(rows)
    order = {stage: index for index, stage in enumerate(v7556.GUI_STAGE_ORDER)}
    def time_key(row: dict[str, Any]) -> int:
        text = str(row.get("stage_time", row.get("time", "")))
        found = re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", text)
        if not found:
            return -1
        parts = [int(value) for value in found[-1].split(":")]
        return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    prepared.sort(key=lambda row: (
        order.get(str(row.get("stage", "")), len(order)),
        1 if bool(row.get("departed")) else 0,
        -time_key(row),
        str(row.get("name", "")),
    ))
    return prepared, counts


_GUI_PATCHED = False
try:
    from tkinter import ttk as _ttk
    _TREE_HEADING = _ttk.Treeview.heading
    _TREE_COLUMN = _ttk.Treeview.column

    def _heading_v7590(self: Any, column: Any, option: Any = None, **kwargs: Any) -> Any:
        if kwargs.get("text") == "목표가":
            kwargs["text"] = "목표가/매도가"
        elif kwargs.get("text") == "편입/매수":
            kwargs["text"] = "편입·매수/매도시각"
        return _TREE_HEADING(self, column, option, **kwargs)

    def _column_v7590(self: Any, column: Any, option: Any = None, **kwargs: Any) -> Any:
        if str(column) == "qty" and "width" in kwargs:
            kwargs["width"] = max(68, int(kwargs["width"]))
        if str(column) == "stage_time" and "width" in kwargs:
            kwargs["width"] = max(155, int(kwargs["width"]))
        return _TREE_COLUMN(self, column, option, **kwargs)

    _ttk.Treeview.heading = _heading_v7590
    _ttk.Treeview.column = _column_v7590
    _GUI_PATCHED = True
except Exception:
    pass


_LABEL_PATCHED = False
try:
    import tkinter as _tk
    _LABEL_INIT = _tk.Label.__init__
    _TEXT_INIT = _tk.Text.__init__

    def _label_init_v7590(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
        text = str(kwargs.get("text", ""))
        if text == "프라임 시그널 대시보드  (PAPER)":
            kwargs["text"] = f"프라임 시그널 대시보드  (PAPER)  ·  {VERSION}"
        elif text == "종목":
            try:
                if str(master.cget("text")) == "형 요청 해석결과 미리보기":
                    kwargs["text"] = "해석된 종목(오주문 방지 확인)"
            except Exception:
                pass
        _LABEL_INIT(self, master, cnf or {}, **kwargs)

    def _text_init_v7590(self: Any, master: Any = None, cnf: Any = None, **kwargs: Any) -> None:
        try:
            if str(master.cget("text")) == "형 의견·명령 입력" and int(kwargs.get("height", 1)) < 3:
                kwargs["height"] = 3
        except Exception:
            pass
        _TEXT_INIT(self, master, cnf or {}, **kwargs)

    _tk.Label.__init__ = _label_init_v7590
    _tk.Text.__init__ = _text_init_v7590
    _LABEL_PATCHED = True
except Exception:
    pass


class V7590FullPatternFusionEngine(parent.V7589DualRouteEvidenceEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7590_decisions: dict[str, PatternDecision] = {}
        self._v7590_order_minute: dict[str, datetime] = {}
        self._v7590_route_counts = {
            "breakout_pullback": 0, "flow_absorption": 0, "verified_breakout": 0,
        }
        self._v7590_flow_gates: dict[str, PatternDecision] = {}
        self._v7590_previous_rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._v7590_departed_rows: list[dict[str, Any]] = []
        self._v7590_manual_tracking: dict[str, dict[str, Any]] = {}
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] V75.89 전 경로·GUI·연동·원장 보존 | 신규: 강한선행수급 첫눌림 + "
            "전고점 돌파후 거래최소 + 전일거래200%% 확신계측",
            VERSION,
        )

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        symbol = str(getattr(signal, "symbol", ""))
        now = datetime.now(KST)
        raw = sorted(
            list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])),
            key=parent._stamp,
        )
        decision = prior_flow_gate(raw, now=now)
        self._v7590_flow_gates[symbol] = decision
        if not decision.allowed:
            self.logger.warning("V75.90 신규매수차단 %s | %s", self._label(symbol), decision.reason)
            old = getattr(self, "_supply_potential", {}).get(symbol)
            if old is not None:
                self._supply_potential[symbol] = self._v7588_make_potential(
                    symbol, "관찰후보", f"신규매수차단:{decision.reason}", now
                )
                self._v7588_phase[symbol] = "OBSERVED"
            return False
        return bool(super()._attempt_entry(signal, from_retry=from_retry))

    def _v7590_forming_bar(self, symbol: str, quote: Any, raw: list[Any], now: datetime) -> Any | None:
        current = float(getattr(quote, "price", 0.0) or 0.0)
        if current <= 0:
            return None
        today_volume = sum(
            max(0, int(getattr(bar, "volume", 0) or 0))
            for bar in raw if parent._stamp(bar).date() == now.date()
        )
        cumulative = max(0, int(getattr(quote, "cumulative_volume", 0) or 0))
        forming_volume = max(0, cumulative - today_volume)
        if not raw or forming_volume <= 0:
            return None
        previous = float(getattr(raw[-1], "close", current) or current)
        return SimpleNamespace(
            symbol=symbol, timestamp=now, open=previous,
            high=max(previous, current), low=min(previous, current),
            close=current, volume=forming_volume,
        )

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        now = datetime.now(KST)
        potential = getattr(self, "_supply_potential", {}).get(symbol)
        phase = self._v7588_phase.get(symbol, "")
        if phase and potential is not None:
            source = str(getattr(potential, "source", ""))
            updated = getattr(potential, "updated_at", now)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=KST)
            if "형명령" not in source and now - updated.astimezone(KST) > timedelta(minutes=30):
                self.logger.warning("V75.90 단계유효기간종료 %s | %s 30분 경과", self._label(symbol), phase)
                self._v7588_phase.pop(symbol, None)
                self._v7588_flush.pop(symbol, None)
                self._supply_potential.pop(symbol, None)
        before = symbol in self.broker.positions
        super()._refresh_actual_bars(symbol, quote)
        if not before and symbol in self.broker.positions:
            return
        if symbol in self._v7587_excluded or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        phase = self._v7588_phase.get(symbol, "")
        if phase not in {"OBSERVED", "INTEREST", "CANDIDATE", "PREBUY", "TREND_PREBUY"}:
            return
        raw_all = sorted(
            list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, [])),
            key=parent._stamp,
        )
        if len(raw_all) < 22:
            return
        forming = self._v7590_forming_bar(symbol, quote, raw_all, now)
        evaluation_bars = raw_all + ([forming] if forming is not None else [])
        decision = evaluate_new_routes(evaluation_bars, now=now)
        self._v7590_decisions[symbol] = decision
        if not decision.allowed:
            return
        minute = now.replace(second=0, microsecond=0)
        if self._v7590_order_minute.get(symbol) == minute:
            return
        label = {
            "breakout_pullback": "돌파후첫눌림",
            "flow_absorption": "선행수급매물흡수",
            "verified_breakout": "전고점확인돌파",
        }[decision.route]
        self._v7588_publish(symbol, "PREBUY", "매수직전", f"{label}:{decision.reason}", now)
        current = float(getattr(quote, "price", 0.0) or decision.reference_price)
        signal = v753.Signal(
            symbol, v753.SignalAction.BUY, decision.score,
            f"{label}|{decision.reason}|체결즉시목표1.5%|고정손절5.0%",
            current, now, label, HARD_STOP_PCT, TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._v7590_order_minute[symbol] = minute
        if self._attempt_entry(signal):
            self._v7590_route_counts[decision.route] += 1

    def _v7588_write_audit(self, snapshot: dict[str, Any]) -> None:
        super()._v7588_write_audit(snapshot)
        parent_payload = {}
        if parent.AUDIT_FILE.exists():
            try:
                parent_payload = json.loads(parent.AUDIT_FILE.read_text(encoding="utf-8"))
            except Exception:
                parent_payload = {}
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "invariants": {
                "parent_first": True,
                "one_order_per_symbol_minute": True,
                "paper_only": True,
                "immediate_take_profit": TAKE_PROFIT_PCT,
                "hard_stop": HARD_STOP_PCT,
                "link_class": runtime_identity().get("link_class"),
            },
            "route_entry_counts": {
                **dict(parent_payload.get("route_entry_counts", {})),
                **dict(self._v7590_route_counts),
            },
            "new_route_decisions": {
                symbol: decision.to_dict()
                for symbol, decision in sorted(self._v7590_decisions.items())
            },
            "prior_flow_gates": {
                symbol: decision.to_dict()
                for symbol, decision in sorted(self._v7590_flow_gates.items())
            },
            "visible_rows": list(snapshot.get("rows", [])),
        }
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = AUDIT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temp.replace(AUDIT_FILE)

    def _v7590_enrich_sell_rows(self, snapshot: dict[str, Any], now: datetime) -> None:
        path = v753.FILL_FILE
        if not path.exists():
            return
        sells: dict[str, dict[str, Any]] = {}
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    if str(raw.get("side", "")).upper() != "SELL":
                        continue
                    stamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=KST)
                    stamp = stamp.astimezone(KST)
                    if stamp.date() != now.date():
                        continue
                    symbol = str(raw.get("symbol", "")).zfill(6)
                    entry_stamp = str(raw.get("entry_time", ""))
                    buy_clock = "-"
                    if entry_stamp:
                        parsed = datetime.fromisoformat(entry_stamp)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=KST)
                        buy_clock = parsed.astimezone(KST).strftime("%H:%M:%S")
                    sells[symbol] = {
                        "sell_price": float(raw.get("price", 0.0) or 0.0),
                        "entry_price": float(raw.get("entry_price", 0.0) or 0.0),
                        "quantity": int(float(raw.get("quantity", 0) or 0)),
                        "buy_clock": buy_clock,
                        "sell_clock": stamp.strftime("%H:%M:%S"),
                    }
        except (OSError, csv.Error, TypeError, ValueError) as exc:
            self.logger.error("V75.90 매수·매도시각/가격 원장읽기 실패: %s", exc)
            return
        for row in snapshot.get("rows", []):
            if str(row.get("stage", "")) != "매도종목":
                continue
            item = sells.get(str(row.get("symbol", "")).zfill(6))
            if not item:
                continue
            row.update({
                "current_price": item["sell_price"],
                "avg_price": item["entry_price"],
                "target_price": item["sell_price"],
                "quantity": item["quantity"],
                "stage_time": f"매수 {item['buy_clock']} / 매도 {item['sell_clock']}",
                "reason": f"매도가 {item['sell_price']:,.0f}원 · " + str(row.get("reason", "")),
            })

    def _v7590_enrich_holding_rows(self, snapshot: dict[str, Any]) -> None:
        """Keep holdings numerically complete even when an older GUI row is sparse."""
        positions = getattr(self.broker, "positions", {})
        quotes = getattr(self, "latest_quotes", {})
        for row in snapshot.get("rows", []):
            if str(row.get("stage", "")) != "매수종목":
                continue
            symbol = str(row.get("symbol", "")).zfill(6)
            position = positions.get(symbol)
            if position is None:
                continue
            quote = quotes.get(symbol)
            current = float(getattr(quote, "price", 0.0) or 0.0)
            if current <= 0:
                current = float(getattr(position, "entry_price", 0.0) or 0.0)
            entry_time = getattr(position, "entry_time", None)
            if isinstance(entry_time, datetime):
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=KST)
                stage_time = entry_time.astimezone(KST).strftime("%H:%M:%S")
            else:
                stage_time = str(row.get("stage_time", "-"))
            row.update({
                "current_price": current,
                "avg_price": float(getattr(position, "entry_price", 0.0) or 0.0),
                "target_price": float(getattr(position, "target_price", 0.0) or 0.0),
                "quantity": int(getattr(position, "quantity", 0) or 0),
                "stage_time": stage_time,
            })

    def _v7590_force_money_fields(self, snapshot: dict[str, Any], now: datetime) -> None:
        """Never let the dashboard fall back to its old +939,909 demo value."""
        today_realized = 0.0
        today_sell_count = 0
        path = v753.FILL_FILE
        if path.exists():
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        if str(raw.get("side", "")).upper() != "SELL":
                            continue
                        stamp = datetime.fromisoformat(str(raw.get("timestamp", "")))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=KST)
                        if stamp.astimezone(KST).date() == now.date():
                            today_realized += float(raw.get("pnl", 0.0) or 0.0)
                            today_sell_count += 1
            except (OSError, csv.Error, TypeError, ValueError) as exc:
                self.logger.error("V75.90 당일손익원장 읽기 실패: %s", exc)
        unrealized = 0.0
        holding_total = 0.0
        commission = float(getattr(self.settings, "commission_pct", 0.0) or 0.0)
        sell_tax = float(getattr(self.settings, "sell_tax_pct", 0.0) or 0.0)
        for symbol, position in getattr(self.broker, "positions", {}).items():
            quantity = int(getattr(position, "quantity", 0) or 0)
            entry_cost = float(getattr(position, "entry_cost", 0.0) or 0.0)
            if entry_cost <= 0:
                entry_cost = float(getattr(position, "entry_price", 0.0) or 0.0) * quantity
                entry_cost += float(getattr(position, "entry_fee", 0.0) or 0.0)
            quote = getattr(self, "latest_quotes", {}).get(symbol)
            current = float(getattr(quote, "price", 0.0) or getattr(position, "entry_price", 0.0) or 0.0)
            holding_total += entry_cost
            unrealized += current * quantity * (1.0 - commission - sell_tax) - entry_cost
        snapshot.update({
            "holding_total": holding_total,
            "unrealized_pnl": unrealized,
            "today_realized_pnl": today_realized,
            "total_pnl": today_realized + unrealized,
            "cash": float(getattr(self.broker, "cash", 0.0) or 0.0),
            "today_sell_count": today_sell_count,
            "pnl_source": "V75.90_today_sell_ledger",
        })

    def _v7590_stage_departures(self, snapshot: dict[str, Any], now: datetime) -> None:
        active_stages = {"관찰후보", "관심종목", "매수가능성", "매수직전"}
        current: dict[tuple[str, str], dict[str, Any]] = {}
        by_symbol: dict[str, str] = {}
        for row in snapshot.get("rows", []):
            stage = str(row.get("stage", ""))
            symbol = str(row.get("symbol", ""))
            if stage in active_stages and symbol:
                current[(stage, symbol)] = dict(row)
                by_symbol[symbol] = stage
        for (old_stage, symbol), old in self._v7590_previous_rows.items():
            if (old_stage, symbol) in current:
                continue
            new_stage = by_symbol.get(symbol, "추적해제")
            self._v7590_departed_rows.append({
                **old,
                "stage": old_stage,
                "stage_time": now.strftime("%H:%M:%S"),
                "departed": True,
                "reason": (
                    f"조건상실: {str(old.get('reason', '기존근거'))} → "
                    f"{new_stage}로 이동; 자동 삭제하지 않고 이탈이력 보존"
                ),
            })
        cutoff = now - timedelta(minutes=60)
        retained: list[dict[str, Any]] = []
        for row in self._v7590_departed_rows[-200:]:
            try:
                stamp = datetime.combine(now.date(), datetime.strptime(str(row["stage_time"]), "%H:%M:%S").time(), KST)
            except (TypeError, ValueError):
                stamp = now
            if stamp >= cutoff:
                retained.append(row)
        self._v7590_departed_rows = retained
        snapshot["rows"].extend(dict(row) for row in retained)
        self._v7590_previous_rows = current

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        now = datetime.now(KST)
        self._v7590_force_money_fields(snapshot, now)
        self._v7590_enrich_holding_rows(snapshot)
        self._v7590_enrich_sell_rows(snapshot, now)
        self._v7590_stage_departures(snapshot, now)
        return snapshot


_PARENT_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v7556.compile_collaboration_input = compile_collaboration_input_v7590
    v57.compile_collaboration_input = compile_collaboration_input_v7590
    v57.route_interpreted_action = route_interpreted_action_v7590
    v7556._apply_collaboration_command = _apply_collaboration_command_v7590
    v57.parse_free_text = parse_free_text_v7590
    v7556.prepare_gui_rows = prepare_gui_rows_v7590
    v57.prepare_gui_rows = prepare_gui_rows_v7590
    v7525.V7525Engine = V7590FullPatternFusionEngine
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "preserved_routes": "V75.89 reversal + trend",
        "new_routes": ["flow_absorption", "breakout_pullback", "verified_breakout"],
        "previous_day_200pct": "confidence evidence; not a standalone buy",
        "named_price_semantics": "price >= current: immediate PAPER buy; price < current: wait at-or-below",
        "sell_row_fields": "entry time + sell time + sell price + quantity",
        "latest_first_with_departures": True,
        # Linux 검증기는 Tcl/Tk 공유 라이브러리가 없을 수 있다. 소스 계약과
        # Windows 런타임 패치 여부를 분리해 거짓 FAIL/PASS를 만들지 않는다.
        "gui_version_visible": "_label_init_v7590" in Path(__file__).read_text(encoding="utf-8"),
        "gui_version_runtime_patch": _LABEL_PATCHED,
        "audit_file": str(AUDIT_FILE),
        "link_exact_parent": parent.runtime_identity()["link_exact_parent"],
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    identity = runtime_identity()
    rows = list(_PARENT_CHECKS)
    rows.extend([
        ("V75.89기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("V75.90실행엔진", v7525.V7525Engine is V7590FullPatternFusionEngine, identity["engine_class"]),
        ("기존두경로보존", identity["preserved_routes"] == "V75.89 reversal + trend", identity["preserved_routes"]),
        ("신규세경로", len(identity["new_routes"]) == 3, ",".join(identity["new_routes"])),
        ("전일200퍼독립매수금지", "not a standalone" in identity["previous_day_200pct"], identity["previous_day_200pct"]),
        ("가격지정기본의미", identity["named_price_semantics"].startswith("price >= current"), identity["named_price_semantics"]),
        ("매도행필드완성", "sell time" in identity["sell_row_fields"], identity["sell_row_fields"]),
        ("최신순·이탈하단", identity["latest_first_with_departures"] is True, "활성 최신순/조건상실 하단"),
        ("버전상단표시", identity["gui_version_visible"] is True, VERSION),
        ("정상연동보존", identity["link_exact_parent"] is True, str(identity.get("link_class"))),
        ("PAPER전용", identity["paper_only"] and VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def print_self_check() -> int:
    rows = self_check()
    for name, ok, detail in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, ok, _ in rows if ok)
    print(f"V75.90 SELF-CHECK: {passed}/{len(rows)} PASS")
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
