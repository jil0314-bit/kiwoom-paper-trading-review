#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.4 FRESH-SIGNAL-GUARD PAPER
==============================

V75.3 분봉 PAPER 봇을 바이트 단위로 보존하고, 그 위에 신규진입 안전장치만
추가하는 독립 실행본이다. 실제 주문 기능은 없다.

추가 안전장치
1. 모든 매수 경로에서 분봉 신호의 완성시각 기준 신선도를 재검증한다.
2. 한 번의 분봉 조회 묶음에서 오래된 매수신호가 발견되면 같은 종목의 즉시
   재진입을 차단한다. "지연신호 폐기 → 바로 매수"를 막는다.
3. 오래된 신호는 재시도 큐에도 넣지 않는다.
4. PAPER라도 당일 확정손실 및 연속손실 한도에 도달하면 신규진입을 잠시 막는다.
5. V75.3 원본 SHA256이 다르면 실행을 중단한다.

V75.3의 전략, 목표가, 손절가, PAPER 체결 계산은 변경하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

VERSION = "V75.4-FRESH-SIGNAL-GUARD-PAPER"
BASE_FILENAME = "bot_v75_3_after_session_guard.py"
BASE_SHA256 = "7c6d5fa95f01a3a9610084ec74edd841dfac385e47bbe6c9da2ebc9c147ecd6a"
BASE_DIR = Path(__file__).resolve().parent
SAFETY_STATE_FILENAME = "v75_4_safety_state.json"

FORBIDDEN_ORDER_NAMES = {
    "send_order",
    "create_order",
    "place_order",
    "order_stock",
    "submit_order",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_base() -> ModuleType:
    path = BASE_DIR / BASE_FILENAME
    if not path.exists():
        raise RuntimeError(f"{BASE_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != BASE_SHA256:
        raise RuntimeError(
            "V75.3 SHA256 불일치 — 검증된 원본이 아니므로 실행을 중단합니다.\n"
            f"기대: {BASE_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v753_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.3 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v753 = load_base()
KST = v753.KST


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise v753.ConfigurationError(f"{name}는 정수여야 합니다: {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise v753.ConfigurationError(f"{name} 범위는 {minimum}~{maximum}: {value}")
    return value


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise v753.ConfigurationError(f"{name}는 숫자여야 합니다: {raw!r}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise v753.ConfigurationError(f"{name} 범위는 {minimum}~{maximum}: {value}")
    return value


def normalize_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def signal_completion_time(signal: Any) -> datetime:
    """1분봉 timestamp는 봉 시작시각이므로 60초를 더해 완성시각으로 바꾼다."""
    timestamp = normalize_kst(signal.timestamp)
    return timestamp + timedelta(seconds=60)


def signal_age_seconds(signal: Any, now: datetime | None = None) -> float:
    current = normalize_kst(now or datetime.now(KST))
    return max(0.0, (current - signal_completion_time(signal)).total_seconds())


def signal_freshness(
    signal: Any,
    *,
    max_age_sec: float,
    now: datetime | None = None,
) -> tuple[bool, str, float]:
    current = normalize_kst(now or datetime.now(KST))
    try:
        completed = signal_completion_time(signal)
    except Exception:
        return False, "신호시각오류", float("inf")
    age = max(0.0, (current - completed).total_seconds())
    if completed.date() != current.date():
        return False, "다른거래일신호", age
    try:
        signal_segment = v753.session_segment(completed)
        current_segment = v753.session_segment(current)
    except Exception:
        return False, "세션판별오류", age
    if signal_segment != current_segment:
        return False, f"세션변경_{signal_segment}->{current_segment}", age
    if age > max_age_sec:
        return False, f"분봉신호지연_{age:.1f}초>{max_age_sec:.0f}초", age
    return True, "신선", age


@dataclass(slots=True)
class EntrySafetyState:
    session_date: str
    consecutive_losses: int = 0
    blocked_until: str = ""
    corrupt: bool = False

    @classmethod
    def fresh(cls, today: date) -> "EntrySafetyState":
        return cls(session_date=today.isoformat())

    def reset_if_new_day(self, today: date) -> None:
        if self.session_date != today.isoformat():
            self.session_date = today.isoformat()
            self.consecutive_losses = 0
            self.blocked_until = ""
            self.corrupt = False

    def blocked_until_datetime(self) -> datetime | None:
        if not self.blocked_until:
            return None
        parsed = datetime.fromisoformat(self.blocked_until)
        return normalize_kst(parsed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "session_date": self.session_date,
            "consecutive_losses": self.consecutive_losses,
            "blocked_until": self.blocked_until,
            "corrupt": self.corrupt,
        }


class SafeTradingEngine(v753.TradingEngine):
    """V75.3 엔진에 신규진입 안전장치만 추가한다."""

    def __init__(self, *args: Any, reset_v754_safety: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        configured_signal_age = env_float(
            "V754_MAX_ENTRY_SIGNAL_AGE_SEC",
            float(self.settings.max_bar_signal_age_sec),
            minimum=10.0,
            maximum=300.0,
        )
        if configured_signal_age > float(self.settings.max_bar_signal_age_sec):
            raise v753.ConfigurationError(
                "V754_MAX_ENTRY_SIGNAL_AGE_SEC는 기존 V75.3 제한보다 크게 설정할 수 없습니다: "
                f"{configured_signal_age:.0f}>{self.settings.max_bar_signal_age_sec}"
            )
        self.max_entry_signal_age_sec = configured_signal_age
        self.stale_symbol_cooldown_sec = env_float(
            "V754_STALE_SYMBOL_COOLDOWN_SEC", 90.0, minimum=10.0, maximum=900.0
        )
        self.paper_loss_guard_enabled = env_bool("V754_ENABLE_PAPER_LOSS_GUARD", True)
        self.paper_daily_loss_pct = env_float(
            "V754_PAPER_DAILY_LOSS_PCT", 0.015, minimum=0.001, maximum=0.20
        )
        self.consecutive_loss_limit = env_int(
            "V754_CONSECUTIVE_LOSS_LIMIT", 3, minimum=1, maximum=20
        )
        self.loss_cooldown_min = env_int(
            "V754_LOSS_COOLDOWN_MIN", 30, minimum=1, maximum=1440
        )
        self.fast_lane_count = env_int(
            "V754_FAST_LANE_COUNT", 40, minimum=10, maximum=100
        )
        # 기존 25개 HOT보다 넓은 상위 후보군을 우선 감시하되 전체 후보는 그대로 유지한다.
        self.hot_candidate_count = max(self.hot_candidate_count, self.fast_lane_count)

        self._refresh_symbol: str | None = None
        self._batch_stale_buy: set[str] = set()
        self._stale_block_until_mono: dict[str, float] = {}
        self._stale_reject_bar_time: dict[str, datetime] = {}

        self.safety_state_path = v753.LOG_DIR / SAFETY_STATE_FILENAME
        if reset_v754_safety:
            try:
                self.safety_state_path.unlink(missing_ok=True)
            except OSError as exc:
                raise v753.ConfigurationError(f"V75.4 안전상태 초기화 실패: {exc}") from exc
        self.safety_state = self._load_safety_state()
        self._install_signal_observer()
        self.logger.warning(
            "[%s] 활성 | 신호최대 %.0f초 | 지연종목차단 %.0f초 | "
            "당일손실 %.1f%% | 연속손실 %d회/%d분 | HOT %d종목",
            VERSION,
            self.max_entry_signal_age_sec,
            self.stale_symbol_cooldown_sec,
            self.paper_daily_loss_pct * 100,
            self.consecutive_loss_limit,
            self.loss_cooldown_min,
            self.hot_candidate_count,
        )

    def _load_safety_state(self) -> EntrySafetyState:
        today = datetime.now(KST).date()
        if not self.safety_state_path.exists():
            return EntrySafetyState.fresh(today)
        try:
            payload = json.loads(self.safety_state_path.read_text(encoding="utf-8"))
            state = EntrySafetyState(
                session_date=str(payload["session_date"]),
                consecutive_losses=max(0, int(payload.get("consecutive_losses", 0))),
                blocked_until=str(payload.get("blocked_until", "")),
                corrupt=bool(payload.get("corrupt", False)),
            )
            # ISO 시각 검증
            if state.blocked_until:
                state.blocked_until_datetime()
            state.reset_if_new_day(today)
            return state
        except Exception as exc:
            self.logger.error("V75.4 안전상태 손상—신규진입 차단: %s", exc)
            return EntrySafetyState(
                session_date=today.isoformat(),
                consecutive_losses=self.consecutive_loss_limit,
                blocked_until="",
                corrupt=True,
            )

    def _save_safety_state(self) -> None:
        try:
            v753.LOG_DIR.mkdir(parents=True, exist_ok=True)
            temp = self.safety_state_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self.safety_state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.safety_state_path)
        except OSError as exc:
            # 안전상태 저장 실패 시 재시작 우회 가능성이 생기므로 현재 프로세스 신규진입을 막는다.
            self.safety_state.corrupt = True
            self.logger.error("V75.4 안전상태 저장 실패—신규진입 차단: %s", exc)

    def _install_signal_observer(self) -> None:
        original_on_bar = self.strategy.on_bar
        engine = self

        def observed_on_bar(bar: Any, position: Any) -> Any:
            signal = original_on_bar(bar, position)
            if signal.action == v753.SignalAction.BUY and position is None:
                fresh, _, _ = signal_freshness(
                    signal,
                    max_age_sec=engine.max_entry_signal_age_sec,
                )
                if not fresh:
                    engine._batch_stale_buy.add(signal.symbol)
                    previous = engine._stale_reject_bar_time.get(signal.symbol)
                    bar_time = normalize_kst(signal.timestamp)
                    if previous is None or bar_time > previous:
                        engine._stale_reject_bar_time[signal.symbol] = bar_time
            return signal

        self.strategy.on_bar = observed_on_bar

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        self._refresh_symbol = symbol
        self._batch_stale_buy.discard(symbol)
        try:
            super()._refresh_actual_bars(symbol, quote)
        finally:
            if symbol in self._batch_stale_buy:
                self._stale_block_until_mono[symbol] = (
                    time.monotonic() + self.stale_symbol_cooldown_sec
                )
            self._refresh_symbol = None

    def _signal_entry_allowed(self, signal: Any) -> tuple[bool, str]:
        fresh, reason, age = signal_freshness(
            signal,
            max_age_sec=self.max_entry_signal_age_sec,
        )
        if not fresh:
            return False, f"{reason} age={age:.1f}초"

        symbol = signal.symbol
        if self._refresh_symbol == symbol and symbol in self._batch_stale_buy:
            return False, "동일조회주기_지연신호후재진입차단"

        block_until = self._stale_block_until_mono.get(symbol, 0.0)
        if time.monotonic() < block_until:
            remain = max(0.0, block_until - time.monotonic())
            return False, f"지연신호종목냉각_{remain:.0f}초"
        if block_until:
            self._stale_block_until_mono.pop(symbol, None)
        return True, "정상"

    def _entry_risk_block_reason(self) -> str:
        if not self.paper_loss_guard_enabled:
            return ""
        now = datetime.now(KST)
        self.safety_state.reset_if_new_day(now.date())
        if self.safety_state.corrupt:
            return "안전상태손상_초기화필요"

        daily_limit = -self.risk.starting_equity * self.paper_daily_loss_pct
        if self.risk.daily_realized_pnl <= daily_limit:
            return (
                f"당일손실한도_{self.risk.daily_realized_pnl:,.0f}원"
                f"<={daily_limit:,.0f}원"
            )

        blocked_until = self.safety_state.blocked_until_datetime()
        if blocked_until is not None:
            if now < blocked_until:
                return f"연속손실대기_{blocked_until:%H:%M:%S}까지"
            self.safety_state.blocked_until = ""
            self.safety_state.consecutive_losses = 0
            self._save_safety_state()
        return ""

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason = self._signal_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("진입재시도차단 %s %s", self._label(signal.symbol), reason)
            return
        risk_reason = self._entry_risk_block_reason()
        if risk_reason:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning("신규진입중단 %s", risk_reason)
            return
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._signal_entry_allowed(signal)
        if not allowed:
            self.pending_entries.pop(signal.symbol, None)
            self._stale_block_until_mono[signal.symbol] = max(
                self._stale_block_until_mono.get(signal.symbol, 0.0),
                time.monotonic() + self.stale_symbol_cooldown_sec,
            )
            self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False

        risk_reason = self._entry_risk_block_reason()
        if risk_reason:
            self.pending_entries.pop(signal.symbol, None)
            self._show_throttled_error(
                "v754-risk-block",
                f"신규진입중단—{risk_reason}",
                60.0,
            )
            return False
        return super()._attempt_entry(signal, from_retry=from_retry)

    def _print_sell(self, fill: Any) -> None:
        super()._print_sell(fill)
        if not self.paper_loss_guard_enabled:
            return
        self.safety_state.reset_if_new_day(datetime.now(KST).date())
        if fill.pnl < 0:
            self.safety_state.consecutive_losses += 1
            if self.safety_state.consecutive_losses >= self.consecutive_loss_limit:
                blocked_until = datetime.now(KST) + timedelta(minutes=self.loss_cooldown_min)
                self.safety_state.blocked_until = blocked_until.isoformat()
                self.logger.warning(
                    "연속손실 %d회—신규진입 %d분 중단(%s까지)",
                    self.safety_state.consecutive_losses,
                    self.loss_cooldown_min,
                    blocked_until.strftime("%H:%M:%S"),
                )
        else:
            self.safety_state.consecutive_losses = 0
            self.safety_state.blocked_until = ""
        self._save_safety_state()


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    base_path = BASE_DIR / BASE_FILENAME
    record("V75.3-SHA256고정", sha256_file(base_path) == BASE_SHA256, BASE_SHA256[:16] + "…")

    # V75.3의 기존 59개 회귀검증을 그대로 위임한다.
    settings = v753.Settings.load()
    base_results = v753.self_check(settings)
    base_failures = [name for name, ok, _ in base_results if not ok]
    record("V75.3-59개위임", len(base_results) == 59 and not base_failures, f"{len(base_results)}개 / 실패={base_failures or '없음'}")

    now = datetime(2026, 7, 8, 10, 0, 30, tzinfo=KST)
    fresh_signal = v753.Signal(
        "000001", v753.SignalAction.BUY, 90, "테스트", 1000,
        datetime(2026, 7, 8, 9, 59, 0, tzinfo=KST), "정석박스돌파"
    )
    stale_signal = v753.Signal(
        "000002", v753.SignalAction.BUY, 90, "테스트", 1000,
        datetime(2026, 7, 8, 9, 56, 0, tzinfo=KST), "정석박스돌파"
    )
    previous_day = v753.Signal(
        "000003", v753.SignalAction.BUY, 90, "테스트", 1000,
        datetime(2026, 7, 7, 15, 29, 0, tzinfo=KST), "정석박스돌파"
    )

    ok, reason, age = signal_freshness(fresh_signal, max_age_sec=120, now=now)
    record("신선신호허용", ok and abs(age - 30.0) < 1e-9, f"{reason} age={age:.1f}")
    ok, reason, age = signal_freshness(stale_signal, max_age_sec=120, now=now)
    record("지연신호차단", not ok and "지연" in reason and age > 120, f"{reason} age={age:.1f}")
    ok, reason, _ = signal_freshness(previous_day, max_age_sec=120, now=now)
    record("전일신호차단", not ok and "다른거래일" in reason, reason)

    # 안전상태의 일자 초기화와 연속손실 대기를 독립 검증한다.
    state = EntrySafetyState("2026-07-07", 3, "2026-07-07T15:00:00+09:00")
    state.reset_if_new_day(date(2026, 7, 8))
    record("안전상태일자초기화", state.consecutive_losses == 0 and not state.blocked_until, str(state.to_dict()))

    # 동일 조회 묶음 차단의 핵심 분기 검증(외부 API 호출 전 차단).
    runtime_now = datetime.now(KST)
    runtime_fresh_signal = v753.Signal(
        "000001", v753.SignalAction.BUY, 90, "테스트", 1000,
        runtime_now - timedelta(seconds=75), "정석박스돌파"
    )
    dummy = object.__new__(SafeTradingEngine)
    dummy.max_entry_signal_age_sec = 120.0
    dummy._refresh_symbol = "000001"
    dummy._batch_stale_buy = {"000001"}
    dummy._stale_block_until_mono = {}
    allowed, reason = SafeTradingEngine._signal_entry_allowed(dummy, runtime_fresh_signal)
    record("동일조회주기재진입차단", not allowed and "동일조회주기" in reason, reason)

    dummy._refresh_symbol = None
    dummy._batch_stale_buy = set()
    dummy._stale_block_until_mono = {"000001": time.monotonic() + 30.0}
    allowed, reason = SafeTradingEngine._signal_entry_allowed(dummy, runtime_fresh_signal)
    record("지연종목냉각차단", not allowed and "냉각" in reason, reason)

    # 직접진입과 재시도 큐가 API 호출 전에 오래된 신호를 거부하는지 확인한다.
    null_logger = logging.getLogger("v754-selfcheck")
    null_logger.handlers.clear()
    null_logger.addHandler(logging.NullHandler())
    direct_dummy = object.__new__(SafeTradingEngine)
    direct_dummy.max_entry_signal_age_sec = 120.0
    direct_dummy._refresh_symbol = None
    direct_dummy._batch_stale_buy = set()
    direct_dummy._stale_block_until_mono = {}
    direct_dummy.stale_symbol_cooldown_sec = 90.0
    direct_dummy.pending_entries = {}
    direct_dummy.logger = null_logger
    direct_dummy._label = lambda symbol: symbol
    runtime_stale_signal = v753.Signal(
        "000009", v753.SignalAction.BUY, 90, "테스트", 1000,
        datetime.now(KST) - timedelta(minutes=5), "정석박스돌파"
    )
    direct_result = SafeTradingEngine._attempt_entry(direct_dummy, runtime_stale_signal)
    record(
        "직접진입API전지연차단",
        direct_result is False and not direct_dummy.pending_entries,
        "오래된 신호는 시세·호가 조회 전 종료",
    )
    SafeTradingEngine._queue_pending_entry(direct_dummy, runtime_stale_signal)
    record(
        "재시도큐지연신호미등록",
        "000009" not in direct_dummy.pending_entries,
        "오래된 신호 재시도 금지",
    )

    # 소스 수준 실주문 함수와 원본 쓰기 금지 확인.
    import ast
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record("실주문함수부재", not (function_names & FORBIDDEN_ORDER_NAMES), str(sorted(function_names & FORBIDDEN_ORDER_NAMES)))

    load_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "load_base"
    )
    write_api_names = {"write_text", "write_bytes", "unlink", "rename", "replace"}
    write_calls = []
    for node in ast.walk(load_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in write_api_names:
                write_calls.append(node.func.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value)
            if any(flag in mode for flag in "wax+"):
                write_calls.append(f"open:{mode}")
    record("V75.3쓰기부재", not write_calls, f"쓰기호출={write_calls or '없음'}")
    record(
        "모든진입경로신선도검사",
        "def _attempt_entry" in source
        and "def _queue_pending_entry" in source
        and "self._signal_entry_allowed(signal)" in source,
        "직접진입+재시도큐 공통검사",
    )
    record(
        "PAPER손실가드구현",
        "V754_PAPER_DAILY_LOSS_PCT" in source
        and "V754_CONSECUTIVE_LOSS_LIMIT" in source
        and "def _entry_risk_block_reason" in source,
        "당일손실+연속손실",
    )
    record("기존목표손절불변", abs(settings.take_profit_pct - 0.015) < 1e-12 and abs(settings.stop_loss_pct - 0.020) < 1e-12, "+1.5% / -2.0%")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — V75.3 무수정 신규진입 안전패치")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--quote-test", action="store_true")
    parser.add_argument("--scan-test", action="store_true")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--minutes", type=int, default=0)
    parser.add_argument("--reset-paper", action="store_true")
    parser.add_argument("--reset-v754-safety", action="store_true")
    parser.add_argument("--reset-v754-safety-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    v753.configure_console()
    loaded = v753.load_environment_files()
    args = build_parser().parse_args()
    logger = v753.setup_logging(args.verbose)
    try:
        settings = v753.Settings.load()
        logger.info("환경파일 자동탐색=%s", [str(path) for path in loaded] or "없음")
        if args.self_check:
            failed = False
            for name, ok, detail in self_check():
                print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
                failed = failed or not ok
            return 1 if failed else 0

        if args.reset_v754_safety_only:
            safety_path = v753.LOG_DIR / SAFETY_STATE_FILENAME
            safety_path.unlink(missing_ok=True)
            print(f"V75.4 safety state reset: {safety_path}")
            return 0

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = v753.Settings.load()
            logger.info("신규 환경파일 적용=%s", [str(path) for path in newly_saved])
        settings.assert_credentials()

        if args.quote_test:
            return v753.quote_test(settings, logger, args.symbol)
        if args.scan_test:
            return v753.scan_test(settings, logger)

        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)
        engine = SafeTradingEngine(
            settings,
            client,
            logger,
            reset_paper=args.reset_paper,
            reset_v754_safety=args.reset_v754_safety,
        )
        engine.latest_quotes[first.symbol] = first
        engine.run(minutes=args.minutes if args.minutes > 0 else None)
        return 0
    except v753.ConfigurationError as exc:
        logger.critical("설정 오류: %s", exc)
        return 2
    except v753.AuthenticationError as exc:
        logger.critical("인증 오류: %s", exc)
        return 3
    except v753.V73Error as exc:
        logger.critical("실행 차단: %s", exc)
        return 4
    except Exception as exc:
        logger.critical("예상 밖 치명 오류: %s", exc)
        v753.append_api_error(traceback.format_exc())
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
