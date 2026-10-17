#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.17 PAPER / 장중명령 메모장 복구
- V75.16 전략·자금·분할매수 로직 유지.
- 장중명령.txt를 주기적으로 읽어 PAPER 명령을 실행.
- 수동 관심종목·매수금지·추가매수금지·추적손절 상태를 재시작 후 복구.
- 실제 주문 함수 없음.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

VERSION = "V75.17-INTRADAY-COMMAND-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7516_FILENAME = "bot_v75_16_paper_10eok_symbol_cap_split_buy.py"
V7516_SHA256 = "2b6b4cc1e9ad34f38acbbc05ca55e12b9bdbf6413389a574f80cebb356d039cf"
RUNTIME_DIR = BASE_DIR / "v75_17_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_17_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
COMMAND_STATE_FILE = RUNTIME_DIR / "v75_17_command_state.json"
COMMAND_POLL_SEC = 0.5
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7516() -> ModuleType:
    path = BASE_DIR / V7516_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7516_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7516_SHA256:
        raise RuntimeError(
            "V75.16 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7516_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7516_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.16 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7516 = load_v7516()
v7515 = v7516.v7515
v753 = v7516.v753
KST = v7516.KST

# V75.17 런타임을 이전 버전과 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_17_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_17_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_17_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_17_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_17_rank_schema_probe.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_17_supply_potential.json"
v7515.v7514.RUNTIME_DIR = RUNTIME_DIR
v7515.v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE

COMMAND_TEMPLATE = """# V75.17 장중명령 — 아래 예시를 복사해 맨 아래에 입력하고 저장하세요.
# 입력한 명령은 실행 후 자동으로 '# 완료' 또는 '# 오류'로 바뀝니다.
# 실제 주문은 없고 PAPER 계좌만 변경됩니다.
#
# 관심 005930 삼성전자
# 관심해제 005930
# 즉시검토 005930
# 매수검토 005930
# 1차매수 005930
# 2차매수 005930
# 3차매수 005930
# 추가매수금지 005930
# 추가매수허용 005930
# 매수금지 005930
# 금지해제 005930
# 전량매도 005930
# 절반매도 005930
# 수량매도 005930 10
# 비율매도 005930 30
# 손절 005930 3.0
# 손절가격 005930 70000
# 목표가 005930 1.5
# 목표가격 005930 73000
# 목표가해제 005930
# 목표가활성 005930
# 추적손절 005930 0.8
# 추적손절해제 005930
# 손익보호 005930 0.35
# 보유확인
# 관심목록
# 당일손익
# 상태
# 신규매수중지
# 신규매수재개
# 전체청산
# 전체정지
# 명령도움
"""


def normalize_symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if symbol.startswith("A"):
        symbol = symbol[1:]
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("종목코드는 숫자 6자리여야 합니다")
    return symbol


def parse_number(raw: str, label: str) -> float:
    cleaned = raw.replace(",", "").replace("%", "").strip()
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"{label} 숫자 오류") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} 숫자 오류")
    return value


def paper_partial_sell(
    broker: Any,
    symbol: str,
    quantity: int,
    quote: Any,
    reason: str,
) -> Any:
    position = broker.positions.get(symbol)
    if position is None:
        raise ValueError("미보유 종목")
    old_quantity = int(position.quantity)
    if quantity <= 0:
        raise ValueError("매도수량 0 이하")
    if quantity >= old_quantity:
        return broker.sell(symbol, quote, reason)
    if quote.best_bid is None or quote.best_bid <= 0:
        raise ValueError("매수1호가 없음")

    price = v753.align_stock_price(
        quote.best_bid * (1.0 - broker.settings.slippage_pct), upward=False
    )
    gross = price * quantity
    fee = gross * broker.settings.commission_pct
    tax = gross * broker.settings.sell_tax_pct
    proceeds = gross - fee - tax
    allocated_entry_fee = float(position.entry_fee) * quantity / old_quantity
    entry_cost = float(position.entry_price) * quantity + allocated_entry_fee
    pnl_value = proceeds - entry_cost
    pnl_pct = pnl_value / max(1.0, entry_cost)

    broker.sequence += 1
    broker.cash += proceeds
    broker.realized_pnl += pnl_value
    broker.daily_realized_pnl += pnl_value
    now = datetime.now(KST)
    position.quantity = old_quantity - quantity
    position.entry_fee = max(0.0, float(position.entry_fee) - allocated_entry_fee)

    fill = v753.Fill(
        f"PAPER-{broker.sequence:06d}", now, symbol, "SELL", quantity,
        price, fee, tax, pnl_value, reason,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        pnl_pct=pnl_pct,
        pattern=position.pattern,
        trigger_time=now,
        trigger_price=price,
        detection_time=now,
        trigger_source="MANUAL_COMMAND",
    )
    broker._record_fill(fill)
    broker.save_state()
    return fill


class IntradayCommandEngine(v7516.CapitalSplitEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.manual_watch: dict[str, str] = {}
        self.buy_blocked: set[str] = set()
        self.scale_blocked: set[str] = set()
        self.trailing_stop_pct: dict[str, float] = {}
        self.manual_new_entries_paused = False
        self.command_stop_requested = False
        self._next_command_poll = 0.0
        self._load_command_state()
        self._ensure_command_file()
        for symbol, name in self.manual_watch.items():
            self.names[symbol] = name or symbol
            self._register_manual_candidate(symbol, name)
        self.logger.warning(
            "[%s] 장중명령 활성 | 파일=%s | PAPER 전용",
            VERSION, COMMAND_FILE.name,
        )

    def _ensure_command_file(self) -> None:
        if COMMAND_FILE.exists():
            return
        COMMAND_FILE.write_text(COMMAND_TEMPLATE, encoding="utf-8-sig")

    def _load_command_state(self) -> None:
        if not COMMAND_STATE_FILE.exists():
            return
        try:
            data = json.loads(COMMAND_STATE_FILE.read_text(encoding="utf-8"))
            self.manual_watch = {
                normalize_symbol(str(symbol)): str(name)
                for symbol, name in dict(data.get("manual_watch", {})).items()
            }
            self.buy_blocked = {
                normalize_symbol(str(symbol)) for symbol in data.get("buy_blocked", [])
            }
            self.scale_blocked = {
                normalize_symbol(str(symbol)) for symbol in data.get("scale_blocked", [])
            }
            self.trailing_stop_pct = {
                normalize_symbol(str(symbol)): float(value)
                for symbol, value in dict(data.get("trailing_stop_pct", {})).items()
                if 0 < float(value) < 0.20
            }
            self.manual_new_entries_paused = bool(data.get("new_entries_paused", False))
        except Exception as exc:
            self.logger.warning("장중명령 상태복구 실패—빈 상태로 시작: %s", exc)
            self.manual_watch = {}
            self.buy_blocked = set()
            self.scale_blocked = set()
            self.trailing_stop_pct = {}
            self.manual_new_entries_paused = False

    def _save_command_state(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "saved_at": datetime.now(KST).isoformat(),
            "manual_watch": self.manual_watch,
            "buy_blocked": sorted(self.buy_blocked),
            "scale_blocked": sorted(self.scale_blocked),
            "trailing_stop_pct": self.trailing_stop_pct,
            "new_entries_paused": self.manual_new_entries_paused,
        }
        temp = COMMAND_STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(COMMAND_STATE_FILE)

    def _register_manual_candidate(self, symbol: str, name: str = "") -> None:
        display_name = name.strip() or self.names.get(symbol, symbol)
        self.names[symbol] = display_name
        quote = self.latest_quotes.get(symbol)
        self.candidate_meta[symbol] = v753.Candidate(
            symbol=symbol,
            name=display_name,
            price=float(getattr(quote, "price", 0.0) or 0.0),
            change_pct=float(getattr(quote, "change_pct", 0.0) or 0.0),
            volume=int(getattr(quote, "cumulative_volume", 0) or 0),
            surge_rate=0.0,
            score=999.0,
            source="수동관심",
        )
        if symbol not in self.active_symbols and symbol not in self.broker.positions:
            self.active_symbols.insert(0, symbol)
        self._queue_bootstrap([symbol])

    def _manual_add_watch(self, symbol: str, name: str = "") -> str:
        self.manual_watch[symbol] = name.strip() or self.names.get(symbol, symbol)
        self._register_manual_candidate(symbol, self.manual_watch[symbol])
        self._save_command_state()
        self.logger.warning("수동관심등록 %s 사유=장중명령", self._label(symbol))
        return f"수동 관심등록 {self._label(symbol)}"

    def _manual_remove_watch(self, symbol: str) -> str:
        existed = symbol in self.manual_watch
        self.manual_watch.pop(symbol, None)
        self._save_command_state()
        return "수동 관심해제" if existed else "수동 관심에 없었음"

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        previous = set(self.active_symbols)
        super()._refresh_universe(force=force)
        for symbol, name in self.manual_watch.items():
            self._register_manual_candidate(symbol, name)
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    def _current_symbols(self) -> list[str]:
        base = list(super()._current_symbols())
        manual = [symbol for symbol in self.manual_watch if symbol not in self.broker.positions]
        return list(dict.fromkeys(manual + base))

    def _new_entry_session_allowed(self, now_kst: datetime | None = None) -> bool:
        if self.manual_new_entries_paused:
            return False
        return super()._new_entry_session_allowed(now_kst)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        if signal.symbol in self.buy_blocked:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입차단 %s 사유=장중명령매수금지", self._label(signal.symbol))
            return False
        if self.manual_new_entries_paused:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입차단 %s 사유=장중명령신규매수중지", self._label(signal.symbol))
            return False
        return super()._attempt_entry(signal, from_retry=from_retry)

    def _attempt_scale_in(self, bar: Any) -> bool:
        if bar.symbol in self.scale_blocked or self.manual_new_entries_paused:
            return False
        return super()._attempt_scale_in(bar)

    def _check_tick_exit(
        self,
        quote: Any,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        position = self.broker.positions.get(quote.symbol)
        trail = self.trailing_stop_pct.get(quote.symbol)
        if position is not None and trail is not None:
            position.highest_price = max(float(position.highest_price), float(quote.price))
            proposed = v753.align_stock_price(
                position.highest_price * (1.0 - trail), upward=False
            )
            if proposed > float(position.stop_price):
                old = float(position.stop_price)
                position.stop_price = proposed
                self.broker.save_state()
                self.logger.info(
                    "추적손절상향 %s %.0f→%.0f 최고가=%.0f 간격=%.2f%%",
                    self._label(quote.symbol), old, proposed,
                    position.highest_price, trail * 100,
                )
        super()._check_tick_exit(
            quote,
            detection_time=detection_time,
            observed_lag_sec=observed_lag_sec,
        )
        if quote.symbol not in self.broker.positions and quote.symbol in self.trailing_stop_pct:
            self.trailing_stop_pct.pop(quote.symbol, None)
            self._save_command_state()

    def _ensure_symbol_ready(self, symbol: str) -> None:
        self._manual_add_watch(symbol, self.manual_watch.get(symbol, ""))
        if symbol not in self.last_bar_time:
            try:
                self.bootstrap_queue.remove(symbol)
            except ValueError:
                pass
            self.bootstrap_pending.discard(symbol)
            self.bootstrap_queue.appendleft(symbol)
            self.bootstrap_pending.add(symbol)
            self._bootstrap_one()
        self.next_chart_due[symbol] = 0.0
        self._service_symbol(symbol, hot=True)

    def _manual_review(self, symbol: str) -> str:
        if not self._new_entry_session_allowed():
            return "현재 신규매수 중지 또는 진입시간 아님"
        self._ensure_symbol_ready(symbol)
        signal, reason, evidence = v7515.detect_precision_supply(
            self.strategy, symbol, config=None
        )
        if signal is None:
            return f"엄격 매수조건 미충족: {reason}"
        if evidence is not None:
            self._supply_evidence[symbol] = evidence
        entered = self._attempt_entry(signal)
        return "엄격조건 충족 PAPER 매수" if entered else "조건은 있었으나 공통 진입검사에서 차단"

    def _fresh_enriched_quote(self, symbol: str, *, side: str) -> Any:
        quote = self.client.get_quote(symbol)
        enriched = self.client.get_orderbook(symbol, quote, side=side)
        self.latest_quotes[symbol] = enriched
        self.names[symbol] = enriched.name or self.names.get(symbol, symbol)
        return enriched

    def _manual_buy(self, symbol: str, tranche: int) -> str:
        if not self._new_entry_session_allowed():
            raise ValueError("현재 신규매수 중지 또는 진입시간 아님")
        if symbol in self.buy_blocked:
            raise ValueError("매수금지 종목")
        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            raise ValueError(f"시장가드 차단: {market_reason}")
        self._manual_add_watch(symbol, self.manual_watch.get(symbol, ""))
        quote = self._fresh_enriched_quote(symbol, side="buy")
        if quote.best_ask is None or quote.best_ask <= 0:
            raise ValueError("매도1호가 없음")
        expected = v753.align_stock_price(
            quote.best_ask * (1.0 + self.settings.slippage_pct), upward=True
        )

        if tranche == 1:
            if symbol in self.broker.positions:
                raise ValueError("이미 보유—2차 또는 3차매수 사용")
            notional = v7516.SPLIT_NOTIONALS[0]
            quantity = min(
                math.floor(notional / expected),
                math.floor(self.broker.cash / (expected * (1.0 + self.settings.commission_pct))),
            )
            if quantity < 1:
                raise ValueError("수량0 또는 현금부족")
            signal = v753.Signal(
                symbol, v753.SignalAction.BUY, 999.0, "장중명령1차매수",
                quote.price, datetime.now(KST), "수동명령매수", 0.03, 0.015,
            )
            fill = self.broker.buy(
                symbol, quantity, quote, "장중명령1차매수",
                stop_loss_pct=0.03, take_profit_pct=0.015,
                pattern="수동명령매수",
            )
            self.last_position_observation_at[symbol] = fill.timestamp
            self.strategy.mark_entry(symbol)
            self._print_buy(fill, self.broker.positions[symbol], signal)
            return f"1차 PAPER 매수 {fill.quantity:,}주 {fill.price:,.0f}원"

        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유—먼저 1차매수")
        if symbol in self.scale_blocked:
            raise ValueError("추가매수금지 종목")
        requested = v7516.SPLIT_NOTIONALS[1] if tranche == 2 else v7516.SPLIT_NOTIONALS[2]
        remaining = max(0.0, v7516.MAX_SYMBOL_NOTIONAL - v7516._current_position_notional(position))
        notional = min(requested, remaining)
        quantity = math.floor(notional / expected)
        if quantity < 1:
            raise ValueError("종목 누적 1천만원 한도 또는 수량0")
        fill = v7516.paper_add_to_position(
            self.broker, symbol, quantity, quote, f"장중명령{tranche}차매수",
            stop_loss_pct=0.03, take_profit_pct=0.015,
            pattern=position.pattern or "수동명령매수",
        )
        self.last_position_observation_at[symbol] = fill.timestamp
        dummy = v753.Signal(
            symbol, v753.SignalAction.BUY, 999.0, f"장중명령{tranche}차매수",
            quote.price, datetime.now(KST), position.pattern or "수동명령매수", 0.03, 0.015,
        )
        self._print_buy(fill, self.broker.positions[symbol], dummy)
        return f"{tranche}차 PAPER 매수 {fill.quantity:,}주 {fill.price:,.0f}원"

    def _manual_sell(self, symbol: str, mode: str, value: float | None = None) -> str:
        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목")
        quote = self._fresh_enriched_quote(symbol, side="sell")
        old_qty = int(position.quantity)
        if mode == "전량":
            quantity = old_qty
        elif mode == "절반":
            quantity = max(1, old_qty // 2)
        elif mode == "수량":
            quantity = int(value or 0)
        elif mode == "비율":
            pct = float(value or 0)
            if pct <= 0 or pct > 100:
                raise ValueError("매도비율은 0초과 100이하")
            quantity = max(1, math.floor(old_qty * pct / 100.0))
        else:
            raise ValueError("매도방식 오류")
        quantity = min(quantity, old_qty)
        if quantity >= old_qty:
            fill = self.broker.sell(symbol, quote, f"장중명령{mode}매도")
            self.strategy.mark_exit(symbol)
            self.trailing_stop_pct.pop(symbol, None)
        else:
            fill = paper_partial_sell(
                self.broker, symbol, quantity, quote, f"장중명령{mode}매도"
            )
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)
        self._save_command_state()
        return f"PAPER 매도 {fill.quantity:,}주 {fill.price:,.0f}원 손익={fill.pnl:+,.0f}원"

    def _set_stop_pct(self, symbol: str, pct: float) -> str:
        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목")
        pct = abs(pct) / 100.0
        if pct <= 0 or pct >= 0.30:
            raise ValueError("손절률은 0초과 30%미만")
        position.stop_price = v753.align_stock_price(
            position.entry_price * (1.0 - pct), upward=False
        )
        self.broker.save_state()
        return f"손절 {position.stop_price:,.0f}원(-{pct:.2%})"

    def _set_stop_price(self, symbol: str, price: float) -> str:
        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목")
        if price <= 0 or price >= position.entry_price:
            raise ValueError("손절가격은 0보다 크고 평단보다 낮아야 함")
        position.stop_price = v753.align_stock_price(price, upward=False)
        self.broker.save_state()
        return f"손절가격 {position.stop_price:,.0f}원"

    def _set_target_pct(self, symbol: str, pct: float) -> str:
        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목")
        pct = abs(pct) / 100.0
        if pct <= 0 or pct > 0.10:
            raise ValueError("목표수익률은 0초과 10%이하")
        position.target_price = v753.align_stock_price(
            position.entry_price * (1.0 + pct), upward=True
        )
        position.target_order_active = True
        self.broker.save_state()
        return f"목표 {position.target_price:,.0f}원(+{pct:.2%})"

    def _set_target_price(self, symbol: str, price: float) -> str:
        position = self.broker.positions.get(symbol)
        if position is None:
            raise ValueError("미보유 종목")
        if price <= position.entry_price:
            raise ValueError("목표가격은 평단보다 높아야 함")
        position.target_price = v753.align_stock_price(price, upward=True)
        position.target_order_active = True
        self.broker.save_state()
        return f"목표가격 {position.target_price:,.0f}원"

    def _print_holdings(self) -> str:
        if not self.broker.positions:
            self.logger.warning("장중명령 보유확인: 없음")
            return "보유 없음"
        for symbol, pos in self.broker.positions.items():
            quote = self.latest_quotes.get(symbol)
            price = float(getattr(quote, "price", pos.entry_price))
            pnl = price / max(0.01, pos.entry_price) - 1.0
            self.logger.warning(
                "장중명령 보유 %s 수량=%s 평단=%.0f 현재=%.0f 평가=%+.2f%% 손절=%.0f 목표=%.0f",
                self._label(symbol), f"{pos.quantity:,}", pos.entry_price,
                price, pnl * 100, pos.stop_price, pos.target_price,
            )
        return f"보유 {len(self.broker.positions)}종목 출력"

    def _print_watchlist(self) -> str:
        items = [self._label(symbol) for symbol in self._current_symbols()]
        self.logger.warning("장중명령 관심목록 %d종목: %s", len(items), ", ".join(items[:60]) or "없음")
        return f"관심 {len(items)}종목 출력"

    def _print_pnl(self) -> str:
        prices = {symbol: quote.price for symbol, quote in self.latest_quotes.items()}
        equity = self.broker.equity(prices)
        text = (
            f"평가자산={equity:,.0f}원 현금={self.broker.cash:,.0f}원 "
            f"당일실현={self.broker.daily_realized_pnl:+,.0f}원 누적실현={self.broker.realized_pnl:+,.0f}원"
        )
        self.logger.warning("장중명령 손익 %s", text)
        return text

    def _close_all(self) -> str:
        symbols = list(self.broker.positions)
        if not symbols:
            return "보유 없음"
        results: list[str] = []
        for symbol in symbols:
            try:
                results.append(self._manual_sell(symbol, "전량"))
            except Exception as exc:
                results.append(f"{symbol} 실패:{exc}")
        return " / ".join(results)

    def _command_help(self) -> str:
        self.logger.warning(
            "명령: 관심/관심해제/즉시검토/매수검토/1차매수/2차매수/3차매수/"
            "추가매수금지/추가매수허용/매수금지/금지해제/전량매도/절반매도/"
            "수량매도/비율매도/손절/손절가격/목표가/목표가격/목표가해제/목표가활성/"
            "추적손절/추적손절해제/손익보호/보유확인/관심목록/당일손익/상태/"
            "신규매수중지/신규매수재개/전체청산/전체정지"
        )
        return "명령목록을 로그에 출력"

    def _execute_command(self, line: str) -> str:
        parts = line.split()
        if not parts:
            return "빈 명령"
        command = parts[0].strip()

        no_symbol = {
            "보유확인", "관심목록", "당일손익", "상태", "신규매수중지",
            "신규매수재개", "전체청산", "전체정지", "명령도움",
        }
        if command not in no_symbol and len(parts) < 2:
            raise ValueError("종목코드가 필요합니다")
        symbol = normalize_symbol(parts[1]) if command not in no_symbol else ""

        if command == "관심":
            return self._manual_add_watch(symbol, " ".join(parts[2:]))
        if command == "관심해제":
            return self._manual_remove_watch(symbol)
        if command in {"즉시검토", "매수검토"}:
            return self._manual_review(symbol)
        if command in {"1차매수", "2차매수", "3차매수"}:
            return self._manual_buy(symbol, int(command[0]))
        if command == "추가매수금지":
            self.scale_blocked.add(symbol)
            self._save_command_state()
            return "추가매수 금지"
        if command == "추가매수허용":
            self.scale_blocked.discard(symbol)
            self._save_command_state()
            return "추가매수 허용"
        if command == "매수금지":
            self.buy_blocked.add(symbol)
            self.pending_entries.pop(symbol, None)
            self._save_command_state()
            return "신규매수 금지"
        if command == "금지해제":
            self.buy_blocked.discard(symbol)
            self._save_command_state()
            return "신규매수 금지 해제"
        if command == "전량매도":
            return self._manual_sell(symbol, "전량")
        if command == "절반매도":
            return self._manual_sell(symbol, "절반")
        if command == "수량매도":
            if len(parts) < 3:
                raise ValueError("매도수량 필요")
            return self._manual_sell(symbol, "수량", parse_number(parts[2], "수량"))
        if command == "비율매도":
            if len(parts) < 3:
                raise ValueError("매도비율 필요")
            return self._manual_sell(symbol, "비율", parse_number(parts[2], "비율"))
        if command == "손절":
            if len(parts) < 3:
                raise ValueError("손절률 필요")
            return self._set_stop_pct(symbol, parse_number(parts[2], "손절률"))
        if command == "손절가격":
            if len(parts) < 3:
                raise ValueError("손절가격 필요")
            return self._set_stop_price(symbol, parse_number(parts[2], "손절가격"))
        if command == "목표가":
            if len(parts) < 3:
                raise ValueError("목표수익률 필요")
            return self._set_target_pct(symbol, parse_number(parts[2], "목표수익률"))
        if command == "목표가격":
            if len(parts) < 3:
                raise ValueError("목표가격 필요")
            return self._set_target_price(symbol, parse_number(parts[2], "목표가격"))
        if command == "목표가해제":
            position = self.broker.positions.get(symbol)
            if position is None:
                raise ValueError("미보유 종목")
            position.target_order_active = False
            self.broker.save_state()
            return "자동 목표매도 해제"
        if command == "목표가활성":
            position = self.broker.positions.get(symbol)
            if position is None:
                raise ValueError("미보유 종목")
            position.target_order_active = True
            self.broker.save_state()
            return "자동 목표매도 활성"
        if command == "추적손절":
            if len(parts) < 3:
                raise ValueError("추적 간격 필요")
            pct = abs(parse_number(parts[2], "추적 간격")) / 100.0
            if pct <= 0 or pct >= 0.20:
                raise ValueError("추적 간격은 0초과 20%미만")
            if symbol not in self.broker.positions:
                raise ValueError("미보유 종목")
            self.trailing_stop_pct[symbol] = pct
            self._save_command_state()
            return f"추적손절 활성 간격={pct:.2%}"
        if command == "추적손절해제":
            self.trailing_stop_pct.pop(symbol, None)
            self._save_command_state()
            return "추적손절 해제"
        if command == "손익보호":
            if len(parts) < 3:
                raise ValueError("보호수익률 필요")
            position = self.broker.positions.get(symbol)
            if position is None:
                raise ValueError("미보유 종목")
            pct = parse_number(parts[2], "보호수익률") / 100.0
            if pct <= -0.10 or pct >= 0.10:
                raise ValueError("보호수익률은 -10%초과 10%미만")
            price = position.entry_price * (1.0 + pct)
            position.stop_price = v753.align_stock_price(price, upward=False)
            self.broker.save_state()
            return f"손익보호선 {position.stop_price:,.0f}원({pct:+.2%})"
        if command == "보유확인":
            return self._print_holdings()
        if command == "관심목록":
            return self._print_watchlist()
        if command in {"당일손익", "상태"}:
            pnl = self._print_pnl()
            self._print_holdings()
            return pnl
        if command == "신규매수중지":
            self.manual_new_entries_paused = True
            self.pending_entries.clear()
            self._save_command_state()
            return "자동·수동 신규매수 중지"
        if command == "신규매수재개":
            self.manual_new_entries_paused = False
            self._save_command_state()
            return "신규매수 재개"
        if command == "전체청산":
            return self._close_all()
        if command == "전체정지":
            self.command_stop_requested = True
            return "프로그램 종료 요청"
        if command == "명령도움":
            return self._command_help()
        raise ValueError("알 수 없는 명령")

    def _process_command_file(self) -> None:
        now_mono = time.monotonic()
        if now_mono < self._next_command_poll:
            return
        self._next_command_poll = now_mono + COMMAND_POLL_SEC
        self._ensure_command_file()
        try:
            text = COMMAND_FILE.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.logger.warning("장중명령 읽기 실패: %s", exc)
            return
        lines = text.splitlines()
        changed = False
        output: list[str] = []
        for original in lines:
            stripped = original.strip()
            if not stripped or stripped.startswith("#"):
                output.append(original)
                continue
            stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            try:
                result = self._execute_command(stripped)
                self.logger.warning("장중명령완료 | %s | %s", stripped, result)
                output.append(f"# 완료 {stamp} | {stripped} | {result}")
            except Exception as exc:
                result = str(exc)
                self.logger.warning("장중명령오류 | %s | %s", stripped, result)
                output.append(f"# 오류 {stamp} | {stripped} | {result}")
            changed = True
        if not changed:
            return
        try:
            temp = COMMAND_FILE.with_suffix(".tmp")
            temp.write_text("\n".join(output) + "\n", encoding="utf-8-sig")
            temp.replace(COMMAND_FILE)
        except OSError as exc:
            self.logger.warning("장중명령 결과표시 실패: %s", exc)

    def run(self, *, minutes: int | None = None) -> None:
        deadline = None if minutes is None or minutes <= 0 else time.monotonic() + minutes * 60
        self.logger.warning("=" * 86)
        self.logger.warning("[모의 자동매매 시작 %s] 실제 주문은 전혀 없습니다", VERSION)
        self.logger.warning("장중명령: %s를 저장하면 약 %.1f초 간격으로 처리", COMMAND_FILE.name, COMMAND_POLL_SEC)
        self.logger.warning("평가자산 10억원 / 종목당 누적 1천만원 / 분할 500·300·200만원")
        self.logger.warning("=" * 86)
        session_was_active = False
        if v753.market_label(datetime.now(KST)) != "ACTIVE":
            self._compact_report()
            self.next_report = time.monotonic() + self.settings.report_interval_sec
        try:
            while (deadline is None or time.monotonic() < deadline) and not self.command_stop_requested:
                loop_start = time.monotonic()
                self._process_command_file()
                if self.command_stop_requested:
                    break
                now_kst = datetime.now(KST)
                label = v753.market_label(now_kst)
                if label != "ACTIVE":
                    session_was_active = False
                    if time.monotonic() >= self.next_report:
                        self._compact_report()
                        self.next_report = time.monotonic() + self.settings.report_interval_sec
                    time.sleep(max(0.5, self.idle_sleep))
                    continue

                if not session_was_active:
                    if v753.session_segment(now_kst) == "AFTER" and not self.after_entry_enabled:
                        session_was_active = True
                    else:
                        added = self._refresh_universe(force=True)
                        self._queue_bootstrap(added or self._current_symbols())
                        session_was_active = True

                self._monitor_one_position()
                self._reset_after_state_if_needed(now_kst)
                if v753.session_segment(now_kst) == "AFTER" and not self.after_entry_enabled:
                    self._enter_after_monitor_only(now_kst)
                    self._sync_daily_state()
                    if time.monotonic() >= self.next_report:
                        self._compact_report()
                        self.next_report = time.monotonic() + self.settings.report_interval_sec
                    elapsed = time.monotonic() - loop_start
                    self.loop_seconds_ema = self.loop_seconds_ema * 0.9 + elapsed * 0.1
                    time.sleep(max(0.10, min(1.0, self.idle_sleep * 0.20)))
                    continue

                self._process_pending_entries()
                added = self._refresh_universe()
                if added:
                    self._queue_bootstrap(added)
                self._bootstrap_one()
                if time.monotonic() >= self.next_hot_update:
                    self._update_hot_symbols()
                    self.next_hot_update = time.monotonic() + 30.0

                self.loop_index += 1
                hot = [s for s in self.hot_symbols if s not in self.broker.positions]
                if hot:
                    symbol = hot[self.hot_cursor % len(hot)]
                    self.hot_cursor += 1
                    self._service_symbol(symbol, hot=True)
                hot_set = set(hot)
                general = [
                    s for s in self._current_symbols()
                    if s not in hot_set and s not in self.broker.positions
                ]
                if general and (not hot or self.loop_index % 2 == 0):
                    symbol = general[self.candidate_cursor % len(general)]
                    self.candidate_cursor += 1
                    self._service_symbol(symbol, hot=False)

                self._sync_daily_state()
                if time.monotonic() >= self.next_report:
                    self._compact_report()
                    self.next_report = time.monotonic() + self.settings.report_interval_sec
                elapsed = time.monotonic() - loop_start
                self.loop_seconds_ema = self.loop_seconds_ema * 0.9 + elapsed * 0.1
                time.sleep(max(0.10, min(1.0, self.idle_sleep * 0.20)))
        except KeyboardInterrupt:
            self.logger.warning("사용자 Ctrl+C 종료")
        finally:
            self.broker.save_state()
            self._save_command_state()
            self.save_pattern_state()
            self._final_report()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_17_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = v7516.TeeTextIO(original_stdout, handle)
    sys.stderr = v7516.TeeTextIO(original_stderr, handle)
    print("=" * 100)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION} 실행 시작")
    print(f"장중명령 파일: {COMMAND_FILE}")
    print(f"날짜별 로그: {path}")
    print("=" * 100)
    return v7515.DailyTee(path, handle, original_stdout, original_stderr)


def self_check() -> list[tuple[str, bool, str]]:
    results = list(v7516.self_check())

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record("V75.16기준본SHA", sha256_file(BASE_DIR / V7516_FILENAME) == V7516_SHA256, V7516_SHA256[:16])
    record("장중명령파일명", COMMAND_FILE.name == "장중명령.txt", COMMAND_FILE.name)
    record("명령폴링0.5초", COMMAND_POLL_SEC == 0.5, str(COMMAND_POLL_SEC))
    record("종목코드정규화", normalize_symbol("A005930") == "005930", normalize_symbol("A005930"))
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), str(sorted(names & FORBIDDEN_ORDER_NAMES)))
    required_commands = {
        "관심", "즉시검토", "1차매수", "2차매수", "3차매수", "전량매도",
        "절반매도", "손절", "목표가", "추적손절", "신규매수중지", "전체정지",
    }
    source = Path(__file__).read_text(encoding="utf-8")
    missing = sorted(command for command in required_commands if f'"{command}"' not in source)
    record("필수장중명령포함", not missing, str(missing or "모두포함"))
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = v7515.build_parser().parse_args()
    tee = install_daily_tee()
    logger = v753.setup_logging(args.verbose)
    v7515.v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    exit_code = 9
    try:
        settings = v753.Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            exit_code = 1 if failed else 0
            return exit_code

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = v753.Settings.load()
        settings.assert_credentials()
        if args.quote_test:
            exit_code = v753.quote_test(settings, logger, args.symbol)
            return exit_code
        if args.scan_test:
            exit_code = v753.scan_test(settings, logger)
            return exit_code

        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = IntradayCommandEngine(
            settings, client, engine_logger,
            supply_only=args.supply_only,
            reset_paper=args.reset_paper,
            reset_v754_safety=args.reset_v754_safety,
        )
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        exit_code = 0
        return exit_code
    except v753.ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        exit_code = 2
        return exit_code
    except v753.AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        exit_code = 3
        return exit_code
    except v753.V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        exit_code = 4
        return exit_code
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        v753.append_api_error(traceback.format_exc())
        exit_code = 9
        return exit_code
    finally:
        print(f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {VERSION} 종료코드={exit_code}")
        tee.close()


if __name__ == "__main__":
    raise SystemExit(main())
