#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.16 PAPER 10EOK / SYMBOL CAP 1000MAN / SPLIT BUY
- V75.15 전략 로직은 변경하지 않는다.
- PAPER 최초 평가자산 10억원.
- 한 종목의 누적 매수원금은 최대 1천만원.
- 조정후거래바닥 계열은 500/300/200만원 3회 분할 가능.
- 추가매수는 새 완성봉에서 엄격 신호가 다시 확인되고 같은 가격대일 때만 허용.
- 다른 패턴은 1회 최대 1천만원.
- PAPER 전용, 실제 주문 함수 없음.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import math
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Mapping, Sequence, TextIO

VERSION = "V75.16-PAPER-10EOK-SYMBOL-CAP-SPLIT-BUY"
BASE_DIR = Path(__file__).resolve().parent
V7515_FILENAME = "bot_v75_15_adjustment_dry_bull_precision_paper.py"
V7515_SHA256 = "c38fba80373ccbfeba8b5771ddd3b3181ca8b0ce56f97ed6c1fcc9f4d971687e"
INITIAL_EQUITY = 1_000_000_000.0
MAX_SYMBOL_NOTIONAL = 10_000_000.0
SPLIT_NOTIONALS = (5_000_000.0, 3_000_000.0, 2_000_000.0)
SCALE_MIN_SECONDS = 45.0
SCALE_MIN_PRICE_FROM_AVG = -0.015
SCALE_MAX_PRICE_FROM_AVG = 0.007
RUNTIME_DIR = BASE_DIR / "v75_16_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_16_daily_logs"
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7515() -> ModuleType:
    path = BASE_DIR / V7515_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7515_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7515_SHA256:
        raise RuntimeError(
            "V75.15 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7515_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7515_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.15 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7515 = load_v7515()
v753 = v7515.v753
KST = v7515.KST
SPLIT_PATTERNS = {v7515.PATTERN_DRY, v7515.PATTERN_REIGNITION}

# V75.16 런타임을 이전 버전과 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_16_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_16_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_16_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_16_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_16_rank_schema_probe.json"
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_16_supply_potential.json"
v7515.v7514.RUNTIME_DIR = RUNTIME_DIR
v7515.v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE

_ORIGINAL_SETTINGS_LOAD = v753.Settings.load


def _fixed_settings_load(cls: type[Any]) -> Any:
    settings = _ORIGINAL_SETTINGS_LOAD()
    return replace(
        settings,
        initial_cash=INITIAL_EQUITY,
        paper_unlimited=False,
        paper_unit_notional=MAX_SYMBOL_NOTIONAL,
    )


v753.Settings.load = classmethod(_fixed_settings_load)


def _is_split_signal(signal: Any) -> bool:
    pattern = str(getattr(signal, "pattern", ""))
    reason = str(getattr(signal, "reason", ""))
    return pattern in SPLIT_PATTERNS or any(name in reason for name in SPLIT_PATTERNS)


def _initial_order_notional(signal: Any) -> float:
    return SPLIT_NOTIONALS[0] if _is_split_signal(signal) else MAX_SYMBOL_NOTIONAL


def _fixed_approve_entry(
    self: Any,
    signal: Any,
    quote: Any,
    equity: float,
    cash: float,
    positions: Mapping[str, Any],
) -> Any:
    if self.halted:
        return v753.RiskDecision(False, self.halt_reason)
    if not v753.entry_session_open(datetime.now(KST)):
        return v753.RiskDecision(False, "진입시간아님")
    if signal.score < self.settings.min_signal_score:
        return v753.RiskDecision(False, "신호점수미달")
    age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
    if age > self.settings.max_quote_age_sec:
        return v753.RiskDecision(False, f"시세지연_{age:.1f}초")
    if quote.symbol in positions:
        return v753.RiskDecision(False, "이미보유")
    if quote.spread_pct is None or quote.best_ask is None:
        return v753.RiskDecision(False, "호가없음")
    if quote.spread_pct > self.settings.max_spread_pct:
        return v753.RiskDecision(False, f"스프레드초과_{quote.spread_pct:.3%}")
    if len(positions) >= self.settings.max_open_positions:
        return v753.RiskDecision(False, "최대보유수")

    expected_fill = v753.align_stock_price(
        quote.best_ask * (1.0 + self.settings.slippage_pct), upward=True
    )
    if expected_fill <= 0:
        return v753.RiskDecision(False, "예상체결가오류")
    order_notional = _initial_order_notional(signal)
    by_notional = math.floor(order_notional / expected_fill)
    by_cash = math.floor(cash / (expected_fill * (1.0 + self.settings.commission_pct)))
    quantity = max(0, min(by_notional, by_cash))
    if quantity < 1:
        return v753.RiskDecision(False, "분할매수수량0또는현금부족")
    reason = "1차500만원승인" if _is_split_signal(signal) else "종목한도1000만원승인"
    return v753.RiskDecision(True, reason, quantity)


v753.RiskEngine.approve_entry = _fixed_approve_entry


def _current_position_notional(position: Any) -> float:
    return float(position.entry_price) * int(position.quantity)


def _next_scale_plan(position: Any) -> tuple[int, float] | None:
    current = _current_position_notional(position)
    # 주가 단위에 따른 정수주 반올림을 감안한 누적구간 판정.
    if current < 6_500_000.0:
        return 2, SPLIT_NOTIONALS[1]
    if current < 9_500_000.0:
        return 3, min(SPLIT_NOTIONALS[2], MAX_SYMBOL_NOTIONAL - current)
    return None


def paper_add_to_position(
    broker: Any,
    symbol: str,
    quantity: int,
    quote: Any,
    reason: str,
    *,
    stop_loss_pct: float,
    take_profit_pct: float,
    pattern: str,
) -> Any:
    position = broker.positions.get(symbol)
    if position is None:
        raise ValueError("분할매수 대상 미보유")
    if quantity <= 0:
        raise ValueError("분할매수 수량0")
    if quote.best_ask is None or quote.best_ask <= 0:
        raise ValueError("분할매수 매도1호가없음")

    price = v753.align_stock_price(
        quote.best_ask * (1.0 + broker.settings.slippage_pct), upward=True
    )
    current_notional = _current_position_notional(position)
    remaining = max(0.0, MAX_SYMBOL_NOTIONAL - current_notional)
    cap_quantity = math.floor(remaining / max(price, 1.0))
    cash_quantity = math.floor(
        broker.cash / (price * (1.0 + broker.settings.commission_pct))
    )
    quantity = max(0, min(quantity, cap_quantity, cash_quantity))
    if quantity < 1:
        raise ValueError("종목천만원한도또는현금부족")

    gross = price * quantity
    fee = gross * broker.settings.commission_pct
    old_quantity = int(position.quantity)
    new_quantity = old_quantity + quantity
    weighted_price = (
        float(position.entry_price) * old_quantity + price * quantity
    ) / max(1, new_quantity)

    broker.sequence += 1
    broker.cash -= gross + fee
    now = datetime.now(KST)
    position.quantity = new_quantity
    position.entry_price = weighted_price
    position.entry_fee = float(position.entry_fee) + fee
    # 추가매수 봉의 과거 저가로 즉시 청산되는 것을 막기 위해 최신 진입시각으로 갱신.
    position.entry_time = now
    position.highest_price = max(float(position.highest_price), price)
    position.stop_price = v753.align_stock_price(
        weighted_price * (1.0 - stop_loss_pct), upward=False
    )
    position.target_price = v753.align_stock_price(
        weighted_price * (1.0 + take_profit_pct), upward=True
    )
    position.pattern = pattern or position.pattern
    position.target_order_active = True

    fill = v753.Fill(
        f"PAPER-{broker.sequence:06d}", now, symbol, "BUY", quantity,
        price, fee, 0.0, -fee, reason, pattern=position.pattern,
    )
    broker._record_fill(fill)
    broker.save_state()
    return fill


class CapitalSplitEngine(v7515.PrecisionEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] PAPER 평가자산=%s원 | 종목당 누적한도=%s원 | "
            "거래바닥계열 분할=%s/%s/%s원",
            VERSION,
            f"{INITIAL_EQUITY:,.0f}", f"{MAX_SYMBOL_NOTIONAL:,.0f}",
            *(f"{value:,.0f}" for value in SPLIT_NOTIONALS),
        )

    def _scale_price_allowed(self, position: Any, price: float) -> tuple[bool, str]:
        drift = price / max(0.01, float(position.entry_price)) - 1.0
        if drift < SCALE_MIN_PRICE_FROM_AVG:
            return False, f"분할매수하락과다_{drift:+.2%}"
        if drift > SCALE_MAX_PRICE_FROM_AVG:
            return False, f"분할매수추격_{drift:+.2%}"
        if price <= float(position.stop_price) * 1.005:
            return False, "분할매수손절선근접"
        return True, f"평단근처_{drift:+.2%}"

    def _attempt_scale_in(self, bar: Any) -> bool:
        symbol = bar.symbol
        position = self.broker.positions.get(symbol)
        if position is None or str(position.pattern) not in SPLIT_PATTERNS:
            return False
        plan = _next_scale_plan(position)
        if plan is None:
            return False
        tranche_no, requested_notional = plan

        now = datetime.now(KST)
        if (now - position.entry_time).total_seconds() < SCALE_MIN_SECONDS:
            return False
        entry_minute = position.entry_time.replace(second=0, microsecond=0)
        bar_minute = v7515.v754.normalize_kst(bar.timestamp).replace(second=0, microsecond=0)
        if bar_minute <= entry_minute:
            return False

        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok or self.risk.halted:
            self.logger.info("분할매수보류 %s %s", self._label(symbol), market_reason or self.risk.halt_reason)
            return False

        signal, _, evidence = v7515.detect_precision_supply(
            self.strategy, symbol, config=None
        )
        if signal is None or signal.pattern not in SPLIT_PATTERNS:
            return False
        signal_minute = v7515.v754.normalize_kst(signal.timestamp).replace(second=0, microsecond=0)
        if signal_minute != bar_minute:
            return False

        try:
            fresh = self.client.get_quote(symbol)
            enriched = self.client.get_orderbook(symbol, fresh, side="buy")
            self.latest_quotes[symbol] = enriched
        except (v753.V73Error, ValueError) as exc:
            self._show_throttled_error(
                f"scale:{symbol}", f"분할매수대기 {self._label(symbol)}: {v753.brief_error(exc)}", 60.0
            )
            return False

        allowed, price_reason = self._scale_price_allowed(position, float(enriched.price))
        if not allowed:
            self.logger.info("분할매수보류 %s %s", self._label(symbol), price_reason)
            return False
        liquidity_ok, liquidity_reason = self._liquidity_gate(signal, enriched)
        if not liquidity_ok:
            self.logger.info("분할매수보류 %s %s", self._label(symbol), liquidity_reason)
            return False
        valid, invalid_reason = self._pending_signal_valid(signal, enriched.price)
        if not valid:
            self.logger.info("분할매수보류 %s %s", self._label(symbol), invalid_reason)
            return False

        expected_fill = v753.align_stock_price(
            enriched.best_ask * (1.0 + self.settings.slippage_pct), upward=True
        ) if enriched.best_ask else 0.0
        remaining = MAX_SYMBOL_NOTIONAL - _current_position_notional(position)
        order_notional = min(requested_notional, remaining)
        quantity = math.floor(order_notional / max(expected_fill, 1.0))
        if quantity < 1:
            return False

        if evidence is not None:
            self._supply_evidence[symbol] = evidence
        scale_reason = (
            f"분할매수{tranche_no}/3|{price_reason}|누적한도={MAX_SYMBOL_NOTIONAL:,.0f}|"
            + signal.reason + "|" + liquidity_reason
        )
        try:
            fill = paper_add_to_position(
                self.broker, symbol, quantity, enriched, scale_reason,
                stop_loss_pct=signal.stop_loss_pct,
                take_profit_pct=signal.take_profit_pct,
                pattern=signal.pattern,
            )
        except ValueError as exc:
            self.logger.info("분할매수보류 %s %s", self._label(symbol), exc)
            return False

        self.last_position_observation_at[symbol] = fill.timestamp
        self._print_buy(fill, self.broker.positions[symbol], signal)
        self.logger.warning(
            "분할매수완료 %s %d/3 | 추가=%s원 | 누적=%s원 | 수량=%s주 | "
            "평단=%.0f 손절=%.0f 목표=%.0f",
            self._label(symbol), tranche_no,
            f"{fill.price * fill.quantity:,.0f}",
            f"{_current_position_notional(self.broker.positions[symbol]):,.0f}",
            f"{self.broker.positions[symbol].quantity:,}",
            self.broker.positions[symbol].entry_price,
            self.broker.positions[symbol].stop_price,
            self.broker.positions[symbol].target_price,
        )
        return True

    def _on_bar(self, bar: Any, quote: Any, *, allow_entry: bool = True, bar_age_sec: float = 0.0) -> None:
        had_position = bar.symbol in self.broker.positions
        super()._on_bar(bar, quote, allow_entry=allow_entry, bar_age_sec=bar_age_sec)
        if (
            had_position
            and allow_entry
            and bar.symbol in self.broker.positions
            and self._new_entry_session_allowed()
        ):
            self._attempt_scale_in(bar)


class TeeTextIO(io.TextIOBase):
    def __init__(self, console: TextIO, file_handle: TextIO) -> None:
        self.console = console
        self.file_handle = file_handle

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.console.write(text)
        self.file_handle.write(text)
        self.file_handle.flush()
        return len(text)

    def flush(self) -> None:
        self.console.flush()
        self.file_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.console, "isatty", lambda: False)())


def install_daily_tee(argv: Sequence[str]) -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_16_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = TeeTextIO(original_stdout, handle)
    sys.stderr = TeeTextIO(original_stderr, handle)
    print("=" * 100)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION} 실행 시작")
    print(
        f"평가자산={INITIAL_EQUITY:,.0f}원 | 종목당누적한도={MAX_SYMBOL_NOTIONAL:,.0f}원 | "
        f"분할={SPLIT_NOTIONALS[0]:,.0f}/{SPLIT_NOTIONALS[1]:,.0f}/{SPLIT_NOTIONALS[2]:,.0f}원"
    )
    print(f"날짜별 로그: {path}")
    print("=" * 100)
    return v7515.DailyTee(path, handle, original_stdout, original_stderr)


def self_check() -> list[tuple[str, bool, str]]:
    results = list(v7515.self_check())

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    settings = v753.Settings.load()
    record("평가자산10억원", settings.initial_cash == INITIAL_EQUITY, f"{settings.initial_cash:,.0f}원")
    record("종목당누적한도천만원", settings.paper_unit_notional == MAX_SYMBOL_NOTIONAL, f"{settings.paper_unit_notional:,.0f}원")
    record("실제현금제약활성", not settings.paper_unlimited, f"paper_unlimited={settings.paper_unlimited}")

    quote = v753.Quote(
        "751600", "검증", 50_000.0, 1.0, 1_000_000, 50_000_000_000.0,
        datetime.now(timezone.utc), best_bid=49_950.0, best_ask=50_000.0,
    )
    signal = v753.Signal(
        "751600", v753.SignalAction.BUY, 120.0, "검증", 50_000.0,
        datetime.now(KST), v7515.PATTERN_DRY, 0.03, 0.015,
    )
    expected = v753.align_stock_price(quote.best_ask * (1 + settings.slippage_pct), upward=True)
    first_quantity = math.floor(_initial_order_notional(signal) / expected)
    first_value = first_quantity * expected
    record("1차500만원이하", 0 < first_value <= SPLIT_NOTIONALS[0], f"{first_value:,.0f}원")

    fake_position = v753.Position(
        symbol="751600", quantity=first_quantity, entry_price=expected,
        entry_time=datetime.now(KST), highest_price=expected,
        stop_price=v753.align_stock_price(expected * 0.97, upward=False),
        target_price=v753.align_stock_price(expected * 1.015, upward=True),
        entry_fee=first_value * settings.commission_pct,
        pattern=v7515.PATTERN_DRY, target_order_active=True,
    )
    fake = SimpleNamespace(
        positions={"751600": fake_position}, cash=INITIAL_EQUITY-first_value,
        settings=settings, sequence=1,
        _record_fill=lambda fill: None, save_state=lambda: None,
    )
    second_plan = _next_scale_plan(fake_position)
    second_qty = math.floor(second_plan[1] / expected) if second_plan else 0
    paper_add_to_position(
        fake, "751600", second_qty, quote, "검증2차",
        stop_loss_pct=0.03, take_profit_pct=0.015, pattern=v7515.PATTERN_DRY,
    )
    third_plan = _next_scale_plan(fake_position)
    third_qty = math.floor(third_plan[1] / expected) if third_plan else 0
    paper_add_to_position(
        fake, "751600", third_qty, quote, "검증3차",
        stop_loss_pct=0.03, take_profit_pct=0.015, pattern=v7515.PATTERN_DRY,
    )
    final_value = _current_position_notional(fake_position)
    record("3회분할누적천만원이하", 0 < final_value <= MAX_SYMBOL_NOTIONAL, f"{final_value:,.0f}원")
    record("평단기준손절3퍼센트", abs(fake_position.stop_price / fake_position.entry_price - 0.97) < 0.002, f"평단={fake_position.entry_price:.0f} 손절={fake_position.stop_price:.0f}")
    record("매수직후목표활성", fake_position.target_order_active and fake_position.target_price > fake_position.entry_price, f"목표={fake_position.target_price:.0f}")

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), str(sorted(names & FORBIDDEN_ORDER_NAMES)))
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = v7515.build_parser().parse_args()
    tee = install_daily_tee(sys.argv)
    logger = v753.setup_logging(args.verbose)
    v7515.v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    exit_code = 9
    try:
        settings = v753.Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        logger.warning(
            "V75.16 PAPER 자금설정 | 평가자산=%s원 | 종목당누적=%s원 | 분할=500/300/200만원",
            f"{settings.initial_cash:,.0f}", f"{settings.paper_unit_notional:,.0f}",
        )
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
        engine = CapitalSplitEngine(
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
