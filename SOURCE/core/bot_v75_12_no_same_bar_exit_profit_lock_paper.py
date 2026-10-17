#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.12 NO-SAME-BAR-EXIT + BREAKOUT HOLD + PROFIT LOCK PAPER
=============================================================

V75.11의 진입 품질·비용포함 손절·한글 UTF-8·기존 모든 매수기법을 유지한다.

핵심 수정
- 매수한 바로 그 1분봉의 고가·저가는 매수 이전 가격경로를 포함하므로 청산판정에서 제외한다.
- V75.11의 25초 호가 기반 돌파실패 조기청산을 사용하지 않는다.
- 정석박스돌파 실패는 최소 120초 후 완성봉 종가가 돌파 유지선 아래에서
  2개 연속 확인될 때만 PAPER 청산한다.
- 고정손절·목표가는 REST 현재가로 계속 즉시 감시한다.
- 돌파 후 +0.70% 이상 수익이 확인되면 가격 손절선을 +0.35%로 올려
  왕복비용 이후에도 손익을 최대한 보호한다.
- 갭·호가공백·급락에서는 목표 손실·수익이 보장되지 않는다.
- 실제 주문 기능은 없다.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.12-NO-SAME-BAR-EXIT-PROFIT-LOCK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7511_FILENAME = "bot_v75_11_loss_control_korean_paper.py"
V7511_SHA256 = "3e8ded5f7a066e1f25931ebd90ec9ee8f52c46243b3c5027c400301628c16b81"
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7511() -> ModuleType:
    path = BASE_DIR / V7511_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7511_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7511_SHA256:
        raise RuntimeError(
            "V75.11 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7511_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7511_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.11 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7511 = load_v7511()
v7510 = v7511.v7510
v7591 = v7511.v7591
v753 = v7511.v753
KST = v7511.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v7511.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v7511.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class HoldProtectionConfig:
    breakout_min_observe_sec: int = 120
    breakout_failure_closes: int = 2
    breakout_close_buffer_pct: float = 0.0015
    profit_lock_arm_pct: float = 0.0070
    profit_lock_trigger_pct: float = 0.0035

    @classmethod
    def load(cls) -> "HoldProtectionConfig":
        return cls(
            breakout_min_observe_sec=env_int(
                "V7512_BREAKOUT_MIN_OBSERVE_SEC", 120, minimum=60, maximum=600
            ),
            breakout_failure_closes=env_int(
                "V7512_BREAKOUT_FAILURE_CLOSES", 2, minimum=2, maximum=4
            ),
            breakout_close_buffer_pct=env_float(
                "V7512_BREAKOUT_CLOSE_BUFFER", 0.0015, minimum=0.0, maximum=0.01
            ),
            profit_lock_arm_pct=env_float(
                "V7512_PROFIT_LOCK_ARM", 0.0070, minimum=0.004, maximum=0.02
            ),
            profit_lock_trigger_pct=env_float(
                "V7512_PROFIT_LOCK_TRIGGER", 0.0035, minimum=0.0025, maximum=0.01
            ),
        )


def minute_key(value: datetime) -> datetime:
    localized = value.astimezone(KST)
    return localized.replace(second=0, microsecond=0)


def is_entry_bar(entry_time: datetime, bar_time: datetime) -> bool:
    return minute_key(bar_time) <= minute_key(entry_time)


def profit_lock_price(entry_price: float, config: HoldProtectionConfig) -> float:
    return float(entry_price) * (1.0 + config.profit_lock_trigger_pct)


class HoldProtectionEngine(v7511.LossControlEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.v7512_config = HoldProtectionConfig.load()
        super().__init__(*args, **kwargs)
        self._breakout_close_fail_hits: dict[str, int] = {}
        self._profit_lock_armed: set[str] = set()
        self.logger.warning(
            "[%s] 청산보정 | 진입봉 고저가 청산판정 제외 | "
            "돌파실패=최소 %s초 후 완성봉 %s개 | 수익 +%.2f%% 후 보호선 +%.2f%%",
            VERSION,
            self.v7512_config.breakout_min_observe_sec,
            self.v7512_config.breakout_failure_closes,
            self.v7512_config.profit_lock_arm_pct * 100,
            self.v7512_config.profit_lock_trigger_pct * 100,
        )

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._breakout_close_fail_hits[signal.symbol] = 0
            self._profit_lock_armed.discard(signal.symbol)
        return entered

    def _check_tick_exit(
        self,
        quote: Any,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        position = self.broker.positions.get(quote.symbol)
        if position is not None and position.pattern == "정석박스돌파":
            arm_price = position.entry_price * (1.0 + self.v7512_config.profit_lock_arm_pct)
            if (
                quote.price >= arm_price
                and quote.symbol not in self._profit_lock_armed
            ):
                protected = profit_lock_price(position.entry_price, self.v7512_config)
                if protected > position.stop_price:
                    position.stop_price = protected
                self._profit_lock_armed.add(quote.symbol)
                self.logger.warning(
                    "수익보호작동 %s 최고현재가=%.0f원 | 보호손절=%.0f원",
                    self._label(quote.symbol),
                    quote.price,
                    position.stop_price,
                )

        # V75.11의 25초 호가 기반 돌파실패 조기청산을 우회한다.
        # V75.10의 현재가 목표·고정손절 감시만 호출한다.
        v7510.MultiPatternPrecisionEngine._check_tick_exit(
            self,
            quote,
            detection_time=detection_time,
            observed_lag_sec=observed_lag_sec,
        )

    def _append_entry_bar_without_exit(self, bar: Any, position: Any) -> None:
        segment = v753.session_segment(bar.timestamp.astimezone(KST))
        previous_segment = self.symbol_sessions.get(bar.symbol)
        if previous_segment is not None and previous_segment != segment:
            self.strategy.bars[bar.symbol].clear()
            self.strategy.capitulation_cache.pop(bar.symbol, None)
            self.strategy.impulse_cache.pop(bar.symbol, None)
        self.symbol_sessions[bar.symbol] = segment

        history = self.strategy.bars[bar.symbol]
        if not history or bar.timestamp > history[-1].timestamp:
            history.append(bar)

        hold = v753.Signal(
            bar.symbol,
            v753.SignalAction.HOLD,
            0.0,
            "진입봉고저가_청산판정제외",
            bar.close,
            bar.timestamp,
            position.pattern,
        )
        self.latest_signals[bar.symbol] = hold

    def _confirm_breakout_failure(self, bar: Any) -> None:
        position = self.broker.positions.get(bar.symbol)
        if position is None or position.pattern != "정석박스돌파":
            return

        age_sec = max(
            0.0,
            (datetime.now(KST) - position.entry_time.astimezone(KST)).total_seconds(),
        )
        if age_sec < self.v7512_config.breakout_min_observe_sec:
            self._breakout_close_fail_hits[bar.symbol] = 0
            return

        hold_level = self._breakout_hold_level.get(bar.symbol, 0.0)
        if hold_level <= 0:
            return

        failure_line = hold_level * (
            1.0 - self.v7512_config.breakout_close_buffer_pct
        )
        if bar.close < failure_line:
            hits = self._breakout_close_fail_hits.get(bar.symbol, 0) + 1
            self._breakout_close_fail_hits[bar.symbol] = hits
            self.logger.warning(
                "돌파유지선종가이탈 %s %.0f<%.0f | 완성봉확인 %s/%s",
                self._label(bar.symbol),
                bar.close,
                failure_line,
                hits,
                self.v7512_config.breakout_failure_closes,
            )
        else:
            self._breakout_close_fail_hits[bar.symbol] = 0
            return

        if self._breakout_close_fail_hits[bar.symbol] < self.v7512_config.breakout_failure_closes:
            return

        detected_at = datetime.now(KST)
        try:
            fresh_quote = self.client.get_quote(bar.symbol)
            enriched = self.client.get_orderbook(bar.symbol, fresh_quote, side="sell")
            fill = self.broker.sell(
                bar.symbol,
                enriched,
                "돌파실패_완성봉2개확정",
                trigger_time=bar.timestamp.astimezone(KST) + timedelta(minutes=1),
                trigger_price=bar.close,
                detection_time=detected_at,
                observed_lag_sec=max(
                    0.0,
                    (
                        detected_at
                        - (bar.timestamp.astimezone(KST) + timedelta(minutes=1))
                    ).total_seconds(),
                ),
                trigger_source="V75.12_COMPLETED_BAR_FAILURE",
            )
        except (v753.V73Error, ValueError) as exc:
            self.logger.warning("돌파실패청산대기 %s %s", self._label(bar.symbol), exc)
            return

        self.strategy.mark_exit(bar.symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)

    def _on_bar(
        self,
        bar: Any,
        quote: Any,
        *,
        allow_entry: bool = True,
        bar_age_sec: float = 0.0,
    ) -> None:
        position = self.broker.positions.get(bar.symbol)

        # 매수한 분봉에는 매수 전 고가·저가가 섞여 있다.
        # 그 전체 OHLC로 목표·손절을 판정하면 사후정보 사용이므로 건너뛴다.
        if position is not None and is_entry_bar(position.entry_time, bar.timestamp):
            self._append_entry_bar_without_exit(bar, position)
            return

        super()._on_bar(
            bar,
            quote,
            allow_entry=allow_entry,
            bar_age_sec=bar_age_sec,
        )

        if self.broker.positions.get(bar.symbol) is not None:
            self._confirm_breakout_failure(bar)

    def _print_sell(self, fill: Any) -> None:
        self._breakout_close_fail_hits.pop(fill.symbol, None)
        self._profit_lock_armed.discard(fill.symbol)
        super()._print_sell(fill)


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.11-SHA256고정",
        sha256_file(BASE_DIR / V7511_FILENAME) == V7511_SHA256,
        V7511_SHA256[:16] + "…",
    )

    inherited = v7511.self_check()
    inherited_failures = [name for name, ok, _ in inherited if not ok]
    record(
        "V75.11기존검증위임",
        not inherited_failures,
        f"{len(inherited)}항목 / 실패={inherited_failures or '없음'}",
    )

    entry = datetime(2026, 7, 10, 13, 12, 45, tzinfo=KST)
    same_bar = datetime(2026, 7, 10, 13, 12, 0, tzinfo=KST)
    next_bar = datetime(2026, 7, 10, 13, 13, 0, tzinfo=KST)
    record("진입봉청산차단", is_entry_bar(entry, same_bar), "13:12 매수→13:12봉 제외")
    record("다음봉청산허용", not is_entry_bar(entry, next_bar), "13:13봉부터 정상판정")

    cfg = HoldProtectionConfig()
    locked = profit_lock_price(10000.0, cfg)
    record(
        "수익보호가격",
        abs(locked - 10035.0) < 0.001,
        f"10,000원→{locked:,.0f}원",
    )
    record(
        "최소관찰120초",
        cfg.breakout_min_observe_sec >= 120,
        f"{cfg.breakout_min_observe_sec}초",
    )
    record(
        "완성봉2개확정",
        cfg.breakout_failure_closes >= 2,
        f"{cfg.breakout_failure_closes}개",
    )

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (function_names & FORBIDDEN_ORDER_NAMES),
        f"금지함수={sorted(function_names & FORBIDDEN_ORDER_NAMES) or '없음'}",
    )
    tick_method = source.split("def _check_tick_exit", 1)[1].split(
        "def _append_entry_bar_without_exit", 1
    )[0]
    record(
        "25초조기손절우회",
        "v7510.MultiPatternPrecisionEngine._check_tick_exit" in tick_method
        and "super()._check_tick_exit" not in tick_method,
        "V75.11 조기청산 미호출",
    )
    record(
        "고정손절즉시유지",
        "MultiPatternPrecisionEngine._check_tick_exit" in source,
        "현재가 목표·고정손절 유지",
    )
    record(
        "한글UTF8유지",
        "# -*- coding: utf-8 -*-" in source,
        "UTF-8 소스",
    )
    record(
        "기존패턴보존",
        "class HoldProtectionEngine(v7511.LossControlEngine)" in source,
        "V75.11 전체 기능 상속",
    )
    record(
        "V75.11원본쓰기부재",
        "write_text" not in source.split("def load_v7511", 1)[1].split("v7511 =", 1)[0],
        "SHA 확인 후 읽기 로드만",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{VERSION} — 진입봉청산차단·돌파보유 PAPER")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--quote-test", action="store_true")
    parser.add_argument("--scan-test", action="store_true")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--minutes", type=int, default=0)
    parser.add_argument("--reset-paper", action="store_true")
    parser.add_argument("--reset-v754-safety", action="store_true")
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

        newly_saved = v753.ensure_credentials(logger)
        if newly_saved:
            settings = v753.Settings.load()
        settings.assert_credentials()

        if args.quote_test:
            return v753.quote_test(settings, logger, args.symbol)
        if args.scan_test:
            return v753.scan_test(settings, logger)

        client = v753.KiwoomRestClient(settings, logger)
        logger.warning("시작 전 현재가 계약검증: %s", settings.watchlist[0][0])
        first = client.get_quote(settings.watchlist[0][0])
        logger.warning("계약검증 성공 %s %.0f원", first.symbol, first.price)

        engine_logger = v7591.CommaSafeLoggerAdapter(logger, {})
        engine = HoldProtectionEngine(
            settings,
            client,
            engine_logger,
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
