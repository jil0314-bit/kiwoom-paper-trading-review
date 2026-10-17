#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.18 PAPER / 조용한 화면 + 유연한 지수 회복 재진입
- V75.17 전략, 자금관리, 분할매수, 장중명령을 그대로 유지한다.
- 내부 상세 로그는 계속 저장하고 콘솔은 5분 요약 중심으로 단순화한다.
- 지수 하락 중 신규매수는 보류한다.
- 하방 경직이 확인되면 CAUTION 후 30초, SHOCK 후 60초 확인 뒤 재검수한다.
- 실제 주문 함수 없음.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import sys
import time
import traceback
import unicodedata
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.18-QUIET-FLEXIBLE-RECOVERY-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7517_FILENAME = "bot_v75_17_intraday_command_paper.py"
V7517_SHA256 = "d34ce4051afaa35c6efe5bc61e26589c40b702b65593b876b7e8ae583d52b454"
RUNTIME_DIR = BASE_DIR / "v75_18_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_18_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
REPORT_INTERVAL_SEC = 300
CAUTION_RECOVERY_CONFIRM_SEC = 30.0
SHOCK_RECOVERY_CONFIRM_SEC = 60.0
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7517() -> ModuleType:
    path = BASE_DIR / V7517_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7517_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7517_SHA256:
        raise RuntimeError(
            "V75.17 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7517_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7517_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.17 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7517 = load_v7517()
v7516 = v7517.v7516
v7515 = v7517.v7515
v753 = v7517.v753
KST = v7517.KST

# V75.18 실행상태를 이전 버전과 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_18_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_18_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_18_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_18_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_18_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_18_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_18_supply_potential.json"
v7515.v7514.RUNTIME_DIR = RUNTIME_DIR
v7515.v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE


class QuietConsoleFilter(logging.Filter):
    """상세 경고는 파일에 남기고 화면에는 명령 결과와 오류만 통과시킨다."""

    ALLOW_PREFIXES = (
        "장중명령완료",
        "장중명령오류",
        "사용자 Ctrl+C 종료",
        "[종료]",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            return True
        message = record.getMessage()
        return message.startswith(self.ALLOW_PREFIXES)


def install_quiet_console_filter(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.addFilter(QuietConsoleFilter())


def add_daily_warning_handler(logger: logging.Logger, path: Path) -> None:
    """콘솔에서 숨긴 WARNING 이상도 날짜별 상세 로그에는 모두 남긴다."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)


def text_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in value)


def pad(value: Any, width: int, *, right: bool = False) -> str:
    text = str(value)
    spaces = max(0, width - text_width(text))
    return (" " * spaces + text) if right else (text + " " * spaces)


def market_label_short(state: str) -> str:
    return {
        "NORMAL": "정상",
        "CAUTION": "하락주의",
        "SHOCK": "급락",
        "RECOVERY_WAIT": "하방경직 확인",
        "RECOVERY_READY": "매수재개",
        "DATA_HALT": "지수오류",
    }.get(state, state)


def pattern_short(pattern: str) -> str:
    if "거래바닥재점화" in pattern:
        return "거래재점화"
    if "거래바닥" in pattern or "매물소진" in pattern:
        return "거래축소양봉"
    if "수동" in pattern:
        return "수동"
    return pattern[:12] or "-"


class QuietFlexibleRecoveryEngine(v7517.IntradayCommandEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7518_decline_severity = ""
        self._v7518_last_market_console_state: str | None = None
        super().__init__(*args, **kwargs)
        self.v758_config = replace(
            self.v758_config,
            recovery_confirm_sec=CAUTION_RECOVERY_CONFIRM_SEC,
        )
        self.next_report = time.monotonic() + REPORT_INTERVAL_SEC

    def _refresh_market_guard(self, *, force: bool = False) -> Any:
        # 급락 뒤에는 60초, 완만한 하락 뒤에는 30초 하방 경직을 확인한다.
        confirm_sec = (
            SHOCK_RECOVERY_CONFIRM_SEC
            if self._v7518_decline_severity == "SHOCK"
            else CAUTION_RECOVERY_CONFIRM_SEC
        )
        if self.v758_config.recovery_confirm_sec != confirm_sec:
            self.v758_config = replace(self.v758_config, recovery_confirm_sec=confirm_sec)

        snapshot = super()._refresh_market_guard(force=force)
        state = str(snapshot.state)
        if state == "SHOCK":
            self._v7518_decline_severity = "SHOCK"
        elif state == "CAUTION" and self._v7518_decline_severity != "SHOCK":
            self._v7518_decline_severity = "CAUTION"
        elif state in {"NORMAL", "RECOVERY_READY"}:
            self._v7518_decline_severity = ""

        if state != self._v7518_last_market_console_state:
            self._v7518_last_market_console_state = state
            entry = "가능" if state in {"NORMAL", "RECOVERY_READY"} else "보류"
            print(
                f"[시장] {datetime.now(KST):%H:%M:%S} | "
                f"{market_label_short(state)} | 신규매수 {entry}"
            )
        return snapshot

    def _print_buy(self, fill: Any, position: Any, signal: Any) -> None:
        amount = float(fill.price) * int(fill.quantity)
        print(
            f"[매수] {fill.timestamp:%H:%M:%S} | {pad(self._label(fill.symbol), 18)} | "
            f"{int(fill.quantity):>8,}주 | {float(fill.price):>10,.0f}원 | "
            f"{amount:>13,.0f}원 | 목표 {float(position.target_price):,.0f} | "
            f"손절 {float(position.stop_price):,.0f} | {pattern_short(str(signal.pattern))}"
        )

    def _print_sell(self, fill: Any) -> None:
        print(
            f"[매도] {fill.timestamp:%H:%M:%S} | {pad(self._label(fill.symbol), 18)} | "
            f"{int(fill.quantity):>8,}주 | {float(fill.price):>10,.0f}원 | "
            f"손익 {float(fill.pnl):>+12,.0f}원 ({float(fill.pnl_pct):+.2%}) | {fill.reason}"
        )

    def _compact_report(self) -> None:
        self._sync_daily_state()
        now = datetime.now(KST)
        prices = {symbol: quote.price for symbol, quote in self.latest_quotes.items()}
        estimated_pnl = 0.0
        liquidation_value = float(self.broker.cash)
        rows: list[tuple[str, Any, float, float]] = []

        for symbol, position in self.broker.positions.items():
            current = float(prices.get(symbol, position.entry_price))
            gross = current * int(position.quantity)
            proceeds = gross * (
                1.0 - self.settings.commission_pct - self.settings.sell_tax_pct
            )
            pnl = proceeds - float(position.entry_cost)
            pnl_pct = pnl / max(1.0, float(position.entry_cost))
            estimated_pnl += pnl
            liquidation_value += proceeds
            rows.append((symbol, position, current, pnl_pct))

        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        entry = "가능" if state in {"NORMAL", "RECOVERY_READY"} and not self.manual_new_entries_paused else "보류"
        active_count = len(self._current_symbols())
        potential_count = len(getattr(self, "_supply_potential", {}))
        deferred_count = len(getattr(self, "_deferred_market_signals", {}))

        line = "=" * 104
        print("\n" + line)
        print(
            f"[{now:%H:%M}] 5분 요약 | 시장 {pad(market_label_short(state), 12)} | "
            f"신규매수 {entry} | 관심 {active_count:>3} | 후보 {potential_count:>3} | 보류 {deferred_count:>3}"
        )
        print(
            f"평가자산 {liquidation_value:>16,.0f}원 | 현금 {float(self.broker.cash):>16,.0f}원 | "
            f"실현 {float(self.broker.realized_pnl):>+12,.0f}원 | 평가 {estimated_pnl:>+12,.0f}원 | 보유 {len(rows):>2}"
        )

        if rows:
            print("-" * 104)
            print(
                f"{pad('종목', 18)} {pad('수량', 9, right=True)} {pad('평단', 11, right=True)} "
                f"{pad('현재', 11, right=True)} {pad('손익률', 9, right=True)} "
                f"{pad('목표', 11, right=True)} {pad('손절', 11, right=True)}"
            )
            for symbol, position, current, pnl_pct in rows:
                print(
                    f"{pad(self._label(symbol), 18)} "
                    f"{pad(f'{int(position.quantity):,}', 9, right=True)} "
                    f"{pad(f'{float(position.entry_price):,.0f}', 11, right=True)} "
                    f"{pad(f'{current:,.0f}', 11, right=True)} "
                    f"{pad(f'{pnl_pct:+.2%}', 9, right=True)} "
                    f"{pad(f'{float(position.target_price):,.0f}', 11, right=True)} "
                    f"{pad(f'{float(position.stop_price):,.0f}', 11, right=True)}"
                )
        else:
            print("보유종목 없음")
        print(line + "\n")
        self.save_pattern_state()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_18_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = v7516.TeeTextIO(original_stdout, handle)
    sys.stderr = v7516.TeeTextIO(original_stderr, handle)
    print("=" * 104)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path}")
    print("=" * 104)
    return v7515.DailyTee(path, handle, original_stdout, original_stderr)


def self_check() -> list[tuple[str, bool, str]]:
    results = list(v7517.self_check())

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record("V75.17기준본SHA", sha256_file(BASE_DIR / V7517_FILENAME) == V7517_SHA256, V7517_SHA256[:16])
    record("5분요약고정", REPORT_INTERVAL_SEC == 300, str(REPORT_INTERVAL_SEC))
    record("하락후빠른재검수", CAUTION_RECOVERY_CONFIRM_SEC == 30.0, "CAUTION=30초")
    record("급락후보수재검수", SHOCK_RECOVERY_CONFIRM_SEC == 60.0, "SHOCK=60초")
    record("장중명령계승", COMMAND_FILE.name == "장중명령.txt", COMMAND_FILE.name)
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (names & FORBIDDEN_ORDER_NAMES), str(sorted(names & FORBIDDEN_ORDER_NAMES)))
    source = Path(__file__).read_text(encoding="utf-8")
    record("상태변경만화면표시", "_v7518_last_market_console_state" in source, "중복시장출력차단")
    record("상세로그유지", "add_daily_warning_handler" in source, "WARNING 이상 날짜별 저장")
    return results


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = v7515.build_parser().parse_args()
    tee = install_daily_tee()
    logger = v753.setup_logging(args.verbose)
    v7515.v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    add_daily_warning_handler(logger, tee.path)
    install_quiet_console_filter(logger)
    exit_code = 9
    try:
        settings = replace(v753.Settings.load(), report_interval_sec=REPORT_INTERVAL_SEC)
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
            settings = replace(v753.Settings.load(), report_interval_sec=REPORT_INTERVAL_SEC)
        settings.assert_credentials()
        if args.quote_test:
            exit_code = v753.quote_test(settings, logger, args.symbol)
            return exit_code
        if args.scan_test:
            exit_code = v753.scan_test(settings, logger)
            return exit_code

        client = v753.KiwoomRestClient(settings, logger)
        first = client.get_quote(settings.watchlist[0][0])
        print(f"[시작] PAPER | 평가자산 10억원 | 종목당 최대 1천만원 | 기준종목 {first.symbol} {first.price:,.0f}원")
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = QuietFlexibleRecoveryEngine(
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
