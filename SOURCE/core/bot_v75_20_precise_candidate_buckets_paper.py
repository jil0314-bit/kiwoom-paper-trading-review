#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.20 PAPER / 실제 조건 단계별 후보 분리 출력
- V75.18의 매매·회복·장중명령 로직은 변경하지 않는다.
- 점수는 후보 판정에 사용하지 않고 실제 필수조건 단계로만 분류한다.
- 관심종목 / 관찰후보 / 매수가능성 / 매수직전 / 시장보류를 구분한다.
- 거래감소·짧은봉·반전봉 필수조건이 모두 충족돼야 매수직전으로 표시한다.
- 상세 계산은 날짜별 로그에 유지한다.
- 종료 시 TeeTextIO 닫힘 경고를 방지한다.
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
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, TextIO

VERSION = "V75.20-PRECISE-CANDIDATE-BUCKETS-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7518_FILENAME = "bot_v75_18_quiet_flexible_recovery_paper.py"
V7518_SHA256 = "510dd8f2e7e8a2f6f4a85c3915ea19e0c9cb4cf999aa9598ff9f5cb492081cd8"
RUNTIME_DIR = BASE_DIR / "v75_20_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_20_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
REPORT_INTERVAL_SEC = 300
SCREEN_INTEREST_LIMIT = 15
SCREEN_WATCH_LIMIT = 12
SCREEN_POSSIBLE_LIMIT = 12
SCREEN_READY_LIMIT = 10
SCREEN_DEFERRED_LIMIT = 10
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7518() -> ModuleType:
    path = BASE_DIR / V7518_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7518_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7518_SHA256:
        raise RuntimeError(
            "V75.18 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7518_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7518_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.18 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7518 = load_v7518()
v7517 = v7518.v7517
v7516 = v7518.v7516
v7515 = v7518.v7515
v7514 = v7515.v7514
v753 = v7518.v753
KST = v7518.KST

# V75.19 실행 상태를 이전 버전과 분리한다.
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_20_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_20_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_20_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_20_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_20_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_20_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_20_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE


def _clean_source(source: str) -> str:
    parts = [part.strip() for part in str(source or "").split("+") if part.strip()]
    preferred = []
    for key in ("수동관심", "거래량급증", "상승률", "쌍끌이"):
        if any(key in part for part in parts):
            preferred.append(key)
    return "·".join(preferred) or "구조유지"


def _missing_labels(values: Any) -> tuple[str, ...]:
    labels: list[str] = []
    for raw in tuple(values or ()):
        text = str(raw)
        if "거래" in text and ("감소" in text or "60%" in text):
            label = "거래감소 대기"
        elif "봉폭" in text:
            label = "짧은봉 대기"
        elif "양봉" in text or "아래꼬리" in text:
            label = "반전봉 대기"
        elif "대량음봉" in text:
            label = "대량매도 확인"
        else:
            label = text[:18]
        if label not in labels:
            labels.append(label)
    return tuple(labels)


def _has_volume_missing(values: Any) -> bool:
    return "거래감소 대기" in _missing_labels(values)


def _classify_potential(item: Any) -> str:
    """점수가 아니라 실제 필수조건 단계로만 분류한다."""
    stage = str(getattr(item, "stage", ""))
    labels = _missing_labels(getattr(item, "strict_missing", ()))
    if stage == "즉시매수조건근접" and not labels:
        return "ready"
    if stage in {"관심종목-매수가능성", "즉시매수조건근접"} and not _has_volume_missing(
        getattr(item, "strict_missing", ())
    ):
        return "possible"
    return "watch"


def _split_potentials(items: list[tuple[str, Any]], deferred_symbols: set[str]) -> dict[str, list[tuple[str, Any]]]:
    buckets: dict[str, list[tuple[str, Any]]] = {
        "watch": [], "possible": [], "ready": [], "deferred": []
    }
    for symbol, item in items:
        if symbol in deferred_symbols:
            buckets["deferred"].append((symbol, item))
            continue
        buckets[_classify_potential(item)].append((symbol, item))

    def quality(pair: tuple[str, Any]) -> tuple[float, float, float, str]:
        symbol, item = pair
        missing_count = float(len(_missing_labels(getattr(item, "strict_missing", ()))))
        dry = getattr(item, "dry_vs_flush", None)
        dry_value = float(dry) if dry is not None else 99.0
        width = getattr(item, "dry_range_vs_flush", None)
        width_value = float(width) if width is not None else 99.0
        return (missing_count, dry_value, width_value, symbol)

    for rows in buckets.values():
        rows.sort(key=quality)
    return buckets


def _condition_reason(item: Any, bucket: str) -> str:
    labels = list(_missing_labels(getattr(item, "strict_missing", ())))
    dry = getattr(item, "dry_vs_flush", None)
    reduction = None
    if dry is not None:
        reduction = max(-9.99, min(0.999, 1.0 - float(dry)))

    if bucket == "deferred":
        return "조건완료·시장안정 대기"
    if bucket == "ready":
        parts = []
        if reduction is not None:
            parts.append(f"거래감소 {reduction:.0%}")
        parts.extend(("짧은봉", "반전봉"))
        return "·".join(parts)
    if bucket == "possible":
        parts = []
        if reduction is not None:
            parts.append(f"거래감소 {reduction:.0%}")
        parts.extend(labels[:2])
        return "·".join(parts) or "거래감소 충족"
    stage = str(getattr(item, "stage", ""))
    if stage == "대량음봉묶음발견":
        return "대량매도 발견·거래감소 대기"
    return "·".join(labels[:2]) or "구조 관찰"


class SafeTeeTextIO:
    """로그 파일이 닫힌 뒤 인터프리터가 flush해도 예외를 내지 않는다."""

    def __init__(self, console: TextIO, file_handle: TextIO) -> None:
        self.console = console
        self.file_handle = file_handle

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        try:
            self.console.write(text)
        except Exception:
            pass
        if not getattr(self.file_handle, "closed", True):
            try:
                self.file_handle.write(text)
                self.file_handle.flush()
            except (ValueError, OSError):
                pass
        return len(text)

    def flush(self) -> None:
        try:
            self.console.flush()
        except Exception:
            pass
        if not getattr(self.file_handle, "closed", True):
            try:
                self.file_handle.flush()
            except (ValueError, OSError):
                pass

    def isatty(self) -> bool:
        return bool(getattr(self.console, "isatty", lambda: False)())


@dataclass(slots=True)
class SafeDailyTee:
    path: Path
    handle: TextIO
    original_stdout: TextIO
    original_stderr: TextIO
    stdout_tee: SafeTeeTextIO
    stderr_tee: SafeTeeTextIO

    def detach_logger_streams(self, logger: logging.Logger) -> None:
        for handler in logger.handlers:
            if not isinstance(handler, logging.StreamHandler) or isinstance(handler, logging.FileHandler):
                continue
            stream = getattr(handler, "stream", None)
            if stream is self.stdout_tee:
                handler.setStream(self.original_stdout)
            elif stream is self.stderr_tee:
                handler.setStream(self.original_stderr)

    def close(self) -> None:
        try:
            self.stdout_tee.flush()
            self.stderr_tee.flush()
        finally:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            if not self.handle.closed:
                self.handle.close()


def install_daily_tee() -> SafeDailyTee:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_20_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = SafeTeeTextIO(original_stdout, handle)
    stderr_tee = SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 112)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path}")
    print("=" * 112)
    return SafeDailyTee(path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee)


class PreciseCandidateBucketEngine(v7518.QuietFlexibleRecoveryEngine):
    def _current_price(self, symbol: str) -> float:
        quote = self.latest_quotes.get(symbol)
        if quote is not None:
            return float(getattr(quote, "price", 0.0) or 0.0)
        meta = self.candidate_meta.get(symbol)
        return float(getattr(meta, "price", 0.0) or 0.0)

    def _interest_reason(self, symbol: str) -> str:
        if symbol in getattr(self, "manual_watch", {}):
            return "수동관심"
        if symbol in getattr(self, "_deferred_market_signals", {}):
            return "시장안정 후 재검사"
        if symbol in getattr(self, "pending_entries", {}):
            return "진입 재검사"
        meta = self.candidate_meta.get(symbol)
        if meta is None:
            return "구조유지"
        return _clean_source(str(getattr(meta, "source", "")))

    def _potential_buckets(self) -> dict[str, list[tuple[str, Any]]]:
        items = list(getattr(self, "_supply_potential", {}).items())
        deferred_symbols = set(getattr(self, "_deferred_market_signals", {}).keys())
        return _split_potentials(items, deferred_symbols)

    def _print_rows(self, title: str, rows: list[tuple[str, Any]], limit: int, bucket: str) -> None:
        suffix = f" | 화면 상위 {limit}종목" if len(rows) > limit else ""
        print(f"[{title}] {len(rows)}종목{suffix}")
        if not rows:
            print("  없음")
            return
        print(f"  {'종목명(코드)':<30} {'현재가':>12}  이유")
        for symbol, item in rows[:limit]:
            label = self._label(symbol)
            price = self._current_price(symbol)
            price_text = f"{price:,.0f}원" if price > 0 else "조회대기"
            reason = _condition_reason(item, bucket)
            print(f"  {v7518.pad(label, 30)} {price_text:>12}  {reason}")
        hidden = len(rows) - limit
        if hidden > 0:
            print(f"  ... 나머지 {hidden}종목은 상세로그")

    def _print_interest_and_candidates(self, buckets: dict[str, list[tuple[str, Any]]] | None = None) -> None:
        buckets = buckets or self._potential_buckets()
        potential_symbols = {
            symbol for key in ("watch", "possible", "ready", "deferred")
            for symbol, _ in buckets[key]
        }
        interest_symbols = [
            symbol for symbol in self._current_symbols()
            if symbol not in potential_symbols and symbol not in self.broker.positions
        ]
        interest_symbols.sort(
            key=lambda symbol: (
                0 if symbol in getattr(self, "manual_watch", {}) else 1,
                -float(getattr(self.candidate_meta.get(symbol), "score", 0.0) or 0.0),
                symbol,
            )
        )

        print("-" * 112)
        suffix = f" | 화면 상위 {SCREEN_INTEREST_LIMIT}종목" if len(interest_symbols) > SCREEN_INTEREST_LIMIT else ""
        print(f"[관심종목] {len(interest_symbols)}종목{suffix}")
        if not interest_symbols:
            print("  없음")
        else:
            print(f"  {'종목명(코드)':<30} {'현재가':>12}  이유")
            for symbol in interest_symbols[:SCREEN_INTEREST_LIMIT]:
                label = self._label(symbol)
                price = self._current_price(symbol)
                price_text = f"{price:,.0f}원" if price > 0 else "조회대기"
                print(f"  {v7518.pad(label, 30)} {price_text:>12}  {self._interest_reason(symbol)}")
            hidden = len(interest_symbols) - SCREEN_INTEREST_LIMIT
            if hidden > 0:
                print(f"  ... 나머지 {hidden}종목은 상세로그")

        self._print_rows("관찰후보", buckets["watch"], SCREEN_WATCH_LIMIT, "watch")
        self._print_rows("매수가능성", buckets["possible"], SCREEN_POSSIBLE_LIMIT, "possible")
        self._print_rows("매수직전", buckets["ready"], SCREEN_READY_LIMIT, "ready")
        self._print_rows("시장보류", buckets["deferred"], SCREEN_DEFERRED_LIMIT, "deferred")

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
            proceeds = gross * (1.0 - self.settings.commission_pct - self.settings.sell_tax_pct)
            pnl = proceeds - float(position.entry_cost)
            pnl_pct = pnl / max(1.0, float(position.entry_cost))
            estimated_pnl += pnl
            liquidation_value += proceeds
            rows.append((symbol, position, current, pnl_pct))

        state = str(getattr(self._market_snapshot, "state", "DATA_HALT"))
        entry = "가능" if state in {"NORMAL", "RECOVERY_READY"} and not self.manual_new_entries_paused else "보류"
        buckets = self._potential_buckets()
        potential_symbols = {
            symbol for key in ("watch", "possible", "ready", "deferred")
            for symbol, _ in buckets[key]
        }
        interest_count = sum(
            1 for symbol in self._current_symbols()
            if symbol not in potential_symbols and symbol not in self.broker.positions
        )

        line = "=" * 112
        print("\n" + line)
        print(
            f"[{now:%H:%M}] 5분 요약 | 시장 {v7518.pad(v7518.market_label_short(state), 12)} | 신규매수 {entry} | "
            f"관심 {interest_count:>3} | 관찰 {len(buckets['watch']):>3} | 가능 {len(buckets['possible']):>3} | "
            f"직전 {len(buckets['ready']):>3} | 보류 {len(buckets['deferred']):>3}"
        )
        print(
            f"평가자산 {liquidation_value:>16,.0f}원 | 현금 {float(self.broker.cash):>16,.0f}원 | "
            f"실현 {float(self.broker.realized_pnl):>+12,.0f}원 | 평가 {estimated_pnl:>+12,.0f}원 | 보유 {len(rows):>2}"
        )

        self._print_interest_and_candidates(buckets)

        print("-" * 112)
        print(f"[보유종목] {len(rows)}종목")
        if rows:
            print(
                f"  {v7518.pad('종목', 22)} {v7518.pad('수량', 9, right=True)} {v7518.pad('평단', 11, right=True)} "
                f"{v7518.pad('현재', 11, right=True)} {v7518.pad('손익률', 9, right=True)} "
                f"{v7518.pad('목표', 11, right=True)} {v7518.pad('손절', 11, right=True)}"
            )
            for symbol, position, current, pnl_pct in rows:
                print(
                    f"  {v7518.pad(self._label(symbol), 22)} "
                    f"{v7518.pad(f'{int(position.quantity):,}', 9, right=True)} "
                    f"{v7518.pad(f'{float(position.entry_price):,.0f}', 11, right=True)} "
                    f"{v7518.pad(f'{current:,.0f}', 11, right=True)} "
                    f"{v7518.pad(f'{pnl_pct:+.2%}', 9, right=True)} "
                    f"{v7518.pad(f'{float(position.target_price):,.0f}', 11, right=True)} "
                    f"{v7518.pad(f'{float(position.stop_price):,.0f}', 11, right=True)}"
                )
        else:
            print("  없음")
        print(line + "\n")
        self.save_pattern_state()


def self_check() -> list[tuple[str, bool, str]]:
    results = list(v7518.self_check())

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record("V75.18기준본SHA", sha256_file(BASE_DIR / V7518_FILENAME) == V7518_SHA256, V7518_SHA256[:16])
    record("5분요약", REPORT_INTERVAL_SEC == 300, "300초")

    heavy = SimpleNamespace(stage="대량음봉묶음발견", strict_missing=("거래량60%감소대기", "짧은양봉/아래꼬리음봉대기"), dry_vs_flush=None, dry_range_vs_flush=None)
    not_dry = SimpleNamespace(stage="거래바닥관찰", strict_missing=("거래감소<60%(대량봉/주변봉)",), dry_vs_flush=0.80, dry_range_vs_flush=0.50)
    possible = SimpleNamespace(stage="관심종목-매수가능성", strict_missing=("짧은양봉/아래꼬리음봉없음",), dry_vs_flush=0.25, dry_range_vs_flush=0.50)
    ready = SimpleNamespace(stage="즉시매수조건근접", strict_missing=(), dry_vs_flush=0.20, dry_range_vs_flush=0.40)
    inconsistent = SimpleNamespace(stage="즉시매수조건근접", strict_missing=("거래감소<60%(대량봉/주변봉)",), dry_vs_flush=0.20, dry_range_vs_flush=0.40)

    record("대량매도는관찰", _classify_potential(heavy) == "watch", "매수후보 아님")
    record("거래감소미달은관찰", _classify_potential(not_dry) == "watch", "매수후보 아님")
    record("거래감소충족은가능", _classify_potential(possible) == "possible", "형태 확인 대기")
    record("필수조건완료만직전", _classify_potential(ready) == "ready", "strict_missing=없음")
    record("불일치자동강등", _classify_potential(inconsistent) == "watch", "단계명보다 필수조건 우선")

    test_items = [("A", heavy), ("B", not_dry), ("C", possible), ("D", ready)]
    buckets = _split_potentials(test_items, {"D"})
    record("시장보류분리", [x[0] for x in buckets["deferred"]] == ["D"], "중복표시 없음")
    record("점수미사용", "score" not in _classify_potential.__code__.co_names, "필수조건으로만 분류")
    record("요약범주5개", all(key in buckets for key in ("watch", "possible", "ready", "deferred")), "관심은 별도")

    tee_test = SafeTeeTextIO(sys.stdout, open(Path(__file__), "r", encoding="utf-8"))
    tee_test.file_handle.close()
    try:
        tee_test.flush()
        safe_flush = True
    except Exception:
        safe_flush = False
    record("종료Flush경고방지", safe_flush, "닫힌파일 flush 안전")
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
    tee = install_daily_tee()
    logger = v753.setup_logging(args.verbose)
    v7514.add_daily_detail_handler(logger, tee.path, verbose=args.verbose)
    v7518.add_daily_warning_handler(logger, tee.path)
    v7518.install_quiet_console_filter(logger)
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
        engine = PreciseCandidateBucketEngine(
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
        tee.detach_logger_streams(logger)
        tee.close()


if __name__ == "__main__":
    raise SystemExit(main())
