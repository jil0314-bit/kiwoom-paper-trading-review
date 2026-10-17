#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.21 PAPER
- V75.20 후보 단계·5분 요약·장중명령을 유지한다.
- 매물소진 계열 자동매매 목표수익을 +1.5%로 고정한다.
- 대량매도 대비 거래량 60% 감소는 유지하되, 주변 5~20봉 문맥은
  과도하게 엄격하지 않도록 30% 감소(현재<=70%)를 보조 조건으로 쓴다.
- 지수 급락 후 1·5분 하방 경직이 확인되면 짧게 재검수하고 재진입한다.
- PAPER 전용. 실제 주문 함수 없음.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import logging
import sys
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

VERSION = "V75.21-FIXED-15-FLEXIBLE-REENTRY-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7520_FILENAME = "bot_v75_20_precise_candidate_buckets_paper.py"
V7520_SHA256 = "5699b8f125f3dfb76155e5d48394715adf0f0576e8a3a855de0a8c64df0aa6e3"
RUNTIME_DIR = BASE_DIR / "v75_21_runtime"
DAILY_LOG_DIR = BASE_DIR / "v75_21_daily_logs"
COMMAND_FILE = BASE_DIR / "장중명령.txt"
FIXED_TAKE_PROFIT_PCT = 0.015
CAUTION_RECOVERY_CONFIRM_SEC = 20.0
SHOCK_RECOVERY_CONFIRM_SEC = 30.0
CONTEXT_VOLUME_MAX_RATIO = 0.70
RECOVERY_FIFTEEN_MIN_FLOOR = -0.025
FORBIDDEN_ORDER_NAMES = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7520() -> ModuleType:
    path = BASE_DIR / V7520_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7520_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7520_SHA256:
        raise RuntimeError(
            "V75.20 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7520_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7520_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.20 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7520 = load_v7520()
# 패치 전 기준본 검사를 먼저 고정한다.
BASELINE_V7520_SELF_CHECK = v7520.self_check()

v7518 = v7520.v7518
v7517 = v7520.v7517
v7516 = v7520.v7516
v7515 = v7520.v7515
v7514 = v7515.v7514
v7513 = v7514.v7513
v758 = v7513.v758
v753 = v7520.v753
KST = v7520.KST

# 버전별 상태·로그 분리
v753.LOG_DIR = RUNTIME_DIR
v753.STATE_FILE = RUNTIME_DIR / "v75_21_paper_state.json"
v753.FILL_FILE = RUNTIME_DIR / "v75_21_paper_fills.csv"
v753.PATTERN_STATE_FILE = RUNTIME_DIR / "v75_21_pattern_state.json"
v753.ERROR_FILE = RUNTIME_DIR / "v75_21_api_errors.txt"
v753.RANK_SCHEMA_FILE = RUNTIME_DIR / "v75_21_rank_schema_probe.json"
v7517.RUNTIME_DIR = RUNTIME_DIR
v7517.DAILY_LOG_DIR = DAILY_LOG_DIR
v7517.COMMAND_FILE = COMMAND_FILE
v7517.COMMAND_STATE_FILE = RUNTIME_DIR / "v75_21_command_state.json"
v7516.RUNTIME_DIR = RUNTIME_DIR
v7516.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.RUNTIME_DIR = RUNTIME_DIR
v7515.DAILY_LOG_DIR = DAILY_LOG_DIR
v7515.POTENTIAL_FILE = RUNTIME_DIR / "v75_21_supply_potential.json"
v7514.RUNTIME_DIR = RUNTIME_DIR
v7514.DAILY_LOG_DIR = DAILY_LOG_DIR
v7514.POTENTIAL_FILE = v7515.POTENTIAL_FILE

# 거래바닥 문맥 조건과 목표가를 고정한다.
_ORIGINAL_PRECISION_LOAD = v7515.PrecisionConfig.load.__func__
_ORIGINAL_RESISTANCE_TARGET = v7515.resistance_target


def _precision_load_v7521(cls: type[Any]) -> Any:
    cfg = _ORIGINAL_PRECISION_LOAD(cls)
    return replace(
        cfg,
        dry_max_volume_vs_context=CONTEXT_VOLUME_MAX_RATIO,
        default_take_profit_pct=FIXED_TAKE_PROFIT_PCT,
        medium_take_profit_pct=FIXED_TAKE_PROFIT_PCT,
        max_take_profit_pct=FIXED_TAKE_PROFIT_PCT,
    )


def _fixed_resistance_target(
    bars: Sequence[Any],
    current_index: int,
    structure: Any,
    current_price: float,
    cfg: Any,
) -> tuple[float, float | None, str]:
    # 저항 위치는 로그 참고용으로만 계산하고 자동 목표는 항상 1.5%다.
    _, gap, reason = _ORIGINAL_RESISTANCE_TARGET(
        bars, current_index, structure, current_price, cfg
    )
    return FIXED_TAKE_PROFIT_PCT, gap, f"1.5%고정/{reason}"


v7515.PrecisionConfig.load = classmethod(_precision_load_v7521)
v7515.resistance_target = _fixed_resistance_target

# 급락 뒤 단기 하방 경직을 우선한다. 일간 낙폭만으로 하루 종일 봉쇄하지 않는다.
def flexible_market_flow(
    daily: Sequence[float],
    one: Sequence[float],
    five: Sequence[float],
    fifteen: Sequence[float],
    config: Any,
) -> Any:
    if not daily or not one or not five or not fifteen:
        return v758.MarketFlowReading(
            "DATA_HALT", "시장지수데이터부족", True, False,
            min(daily) if daily else 0.0,
            min(one) if one else 0.0,
            min(five) if five else 0.0,
            min(fifteen) if fifteen else 0.0,
        )

    worst_daily = min(daily)
    worst_one = min(one)
    worst_five = min(five)
    worst_fifteen = min(fifteen)
    short_term_unstable = (
        worst_one < config.recovery_one_min_min_pct
        or worst_five < config.recovery_five_min_min_pct
    )
    hard_daily_active = (
        worst_daily <= config.hard_daily_block_pct and short_term_unstable
    )
    shock = (
        worst_one <= config.one_min_shock_pct
        or worst_five <= config.five_min_shock_pct
        or hard_daily_active
        or (
            worst_fifteen <= config.fifteen_min_shock_pct
            and worst_five < config.recovery_five_min_min_pct
        )
    )
    caution = (
        shock
        or worst_one <= config.one_min_caution_pct
        or worst_five <= config.five_min_caution_pct
        or (
            worst_fifteen <= config.fifteen_min_caution_pct
            and short_term_unstable
        )
    )
    fifteen_floor = min(
        float(config.recovery_fifteen_min_min_pct),
        RECOVERY_FIFTEEN_MIN_FLOOR,
    )
    stable = (
        worst_one >= config.recovery_one_min_min_pct
        and worst_five >= config.recovery_five_min_min_pct
        and worst_fifteen >= fifteen_floor
    )
    metrics = (
        f"일={worst_daily:+.2%} 1분={worst_one:+.2%} "
        f"5분={worst_five:+.2%} 15분={worst_fifteen:+.2%}"
    )
    if shock:
        return v758.MarketFlowReading(
            "SHOCK", f"지수급락진행 {metrics}", True, False,
            worst_daily, worst_one, worst_five, worst_fifteen,
        )
    if caution:
        return v758.MarketFlowReading(
            "CAUTION", f"지수하락진행 {metrics}", True, False,
            worst_daily, worst_one, worst_five, worst_fifteen,
        )
    return v758.MarketFlowReading(
        "FLOW_STABLE", f"지수단기하방경직 {metrics}", False, stable,
        worst_daily, worst_one, worst_five, worst_fifteen,
    )


v758.classify_market_flow = flexible_market_flow
v7518.CAUTION_RECOVERY_CONFIRM_SEC = CAUTION_RECOVERY_CONFIRM_SEC
v7518.SHOCK_RECOVERY_CONFIRM_SEC = SHOCK_RECOVERY_CONFIRM_SEC

# 보류후보는 시장 안정 후 1초 간격으로 재검수한다.
_ORIGINAL_MARKET_CONFIG_LOAD = v758.MarketPauseConfig.load.__func__
_ORIGINAL_GENERIC_CONFIG_LOAD = v7513.V7513Config.load.__func__


def _market_config_load_v7521(cls: type[Any]) -> Any:
    cfg = _ORIGINAL_MARKET_CONFIG_LOAD(cls)
    return replace(cfg, deferred_service_sec=1.0)


def _generic_config_load_v7521(cls: type[Any]) -> Any:
    cfg = _ORIGINAL_GENERIC_CONFIG_LOAD(cls)
    return replace(cfg, generic_service_sec=1.0)


v758.MarketPauseConfig.load = classmethod(_market_config_load_v7521)
v7513.V7513Config.load = classmethod(_generic_config_load_v7521)

FIXED_TARGET_PATTERNS = set(v7514.SUPPLY_PATTERNS) | {
    v7515.PATTERN_DRY,
    v7515.PATTERN_REIGNITION,
    "매물소진50초선진입",
    "매물소진눌림1차",
}


def fixed_target_signal(signal: Any) -> Any:
    if (
        getattr(signal, "action", None) == v753.SignalAction.BUY
        and str(getattr(signal, "pattern", "")) in FIXED_TARGET_PATTERNS
    ):
        return v753.Signal(
            signal.symbol,
            signal.action,
            float(signal.score),
            f"{signal.reason}|목표수익1.5%고정",
            float(signal.reference_price),
            signal.timestamp,
            str(signal.pattern),
            float(signal.stop_loss_pct),
            FIXED_TAKE_PROFIT_PCT,
        )
    return signal


class FixedTargetFlexibleEngine(v7520.PreciseCandidateBucketEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enforce_fixed_targets()
        self.logger.warning(
            "[%s] 목표수익 +1.5%% 고정 | 대량봉대비 거래감소>=60%% | "
            "주변문맥 현재<=70%% | 급락후 하방경직 재확인=%d/%d초",
            VERSION,
            int(CAUTION_RECOVERY_CONFIRM_SEC),
            int(SHOCK_RECOVERY_CONFIRM_SEC),
        )

    def _attempt_entry(self, signal: Any) -> Any:
        return super()._attempt_entry(fixed_target_signal(signal))

    def _enforce_fixed_targets(self) -> None:
        changed = False
        for position in self.broker.positions.values():
            if str(getattr(position, "pattern", "")) not in FIXED_TARGET_PATTERNS:
                continue
            target = v753.align_stock_price(
                float(position.entry_price) * (1.0 + FIXED_TAKE_PROFIT_PCT),
                upward=True,
            )
            if (
                abs(float(position.target_price) - float(target)) > 1e-9
                or not bool(getattr(position, "target_order_active", False))
            ):
                position.target_price = target
                position.target_order_active = True
                changed = True
        if changed:
            self.broker.save_state()

    def _on_bar(
        self,
        bar: Any,
        quote: Any,
        *,
        allow_entry: bool = True,
        bar_age_sec: float = 0.0,
    ) -> None:
        super()._on_bar(
            bar, quote, allow_entry=allow_entry, bar_age_sec=bar_age_sec
        )
        self._enforce_fixed_targets()

    def _compact_report(self) -> None:
        self._enforce_fixed_targets()
        super()._compact_report()


def install_daily_tee() -> Any:
    DAILY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    path = DAILY_LOG_DIR / f"V75_21_{now:%Y-%m-%d}.txt"
    handle = path.open("a", encoding="utf-8", buffering=1)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_tee = v7520.SafeTeeTextIO(original_stdout, handle)
    stderr_tee = v7520.SafeTeeTextIO(original_stderr, handle)
    sys.stdout = stdout_tee  # type: ignore[assignment]
    sys.stderr = stderr_tee  # type: ignore[assignment]
    print("=" * 112)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] {VERSION}")
    print(f"장중명령: {COMMAND_FILE.name} | 상세로그: {path}")
    print("=" * 112)
    return v7520.SafeDailyTee(
        path, handle, original_stdout, original_stderr, stdout_tee, stderr_tee
    )


def _context_relaxed_case() -> tuple[Any, str]:
    strategy, symbol = v7515.synthetic_case("quiet")
    bars = list(strategy.bars[symbol])
    last = bars[-1]
    bars[-1] = v753.Bar(
        last.symbol, last.timestamp,
        float(last.open), float(last.high), float(last.low), float(last.close), 600,
    )
    strategy.bars[symbol].clear()
    for bar in bars:
        strategy.on_bar(bar, None)
    return strategy, symbol


def self_check() -> list[tuple[str, bool, str]]:
    results = list(BASELINE_V7520_SELF_CHECK)

    def record(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.20기준본SHA",
        sha256_file(BASE_DIR / V7520_FILENAME) == V7520_SHA256,
        V7520_SHA256[:16],
    )
    cfg = v7515.PrecisionConfig.load()
    record(
        "자동목표1.5퍼센트고정",
        cfg.default_take_profit_pct == cfg.medium_take_profit_pct
        == cfg.max_take_profit_pct == FIXED_TAKE_PROFIT_PCT,
        f"{cfg.default_take_profit_pct:.2%}",
    )
    record(
        "주변문맥과잉엄격완화",
        abs(cfg.dry_max_volume_vs_context - CONTEXT_VOLUME_MAX_RATIO) < 1e-12,
        f"현재<=주변문맥 {cfg.dry_max_volume_vs_context:.0%}",
    )
    strategy, symbol = _context_relaxed_case()
    signal, _, evidence = v7515.detect_precision_supply(strategy, symbol, config=None)
    record(
        "대량봉60퍼감소우선탐지",
        signal is not None
        and float(signal.take_profit_pct) == FIXED_TAKE_PROFIT_PCT
        and evidence is not None
        and float(evidence.dry_volume_vs_flush) <= 0.40
        and float(evidence.dry_volume_vs_left_median) <= CONTEXT_VOLUME_MAX_RATIO,
        signal.reason if signal is not None else "미탐지",
    )
    stabilized = flexible_market_flow(
        (-0.040,), (0.000,), (-0.001,), (-0.020,), v758.MarketPauseConfig.load()
    )
    falling = flexible_market_flow(
        (-0.040,), (-0.004,), (-0.010,), (-0.020,), v758.MarketPauseConfig.load()
    )
    record(
        "일간급락중하방경직재검수",
        stabilized.state == "FLOW_STABLE" and stabilized.stable_now,
        stabilized.reason,
    )
    record(
        "단기하락진행중매수차단",
        falling.state == "SHOCK" and falling.active_decline,
        falling.reason,
    )
    record(
        "회복확인20초30초",
        v7518.CAUTION_RECOVERY_CONFIRM_SEC == 20.0
        and v7518.SHOCK_RECOVERY_CONFIRM_SEC == 30.0,
        "CAUTION=20/SHOCK=30",
    )
    record(
        "보류후보1초재검수",
        v758.MarketPauseConfig.load().deferred_service_sec == 1.0
        and v7513.V7513Config.load().generic_service_sec == 1.0,
        "1초",
    )
    test_signal = v753.Signal(
        "752100", v753.SignalAction.BUY, 100.0, "테스트", 10000.0,
        datetime(2026, 7, 13, 10, 0, tzinfo=KST),
        v7515.PATTERN_DRY, 0.03, 0.03,
    )
    fixed = fixed_target_signal(test_signal)
    record(
        "진입직전목표강제고정",
        abs(float(fixed.take_profit_pct) - FIXED_TAKE_PROFIT_PCT) < 1e-12,
        f"{fixed.take_profit_pct:.2%}",
    )
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (names & FORBIDDEN_ORDER_NAMES),
        str(sorted(names & FORBIDDEN_ORDER_NAMES)),
    )
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
        settings = replace(
            v753.Settings.load(), report_interval_sec=v7520.REPORT_INTERVAL_SEC
        )
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
            settings = replace(
                v753.Settings.load(), report_interval_sec=v7520.REPORT_INTERVAL_SEC
            )
        settings.assert_credentials()
        if args.quote_test:
            exit_code = v753.quote_test(settings, logger, args.symbol)
            return exit_code
        if args.scan_test:
            exit_code = v753.scan_test(settings, logger)
            return exit_code

        client = v753.KiwoomRestClient(settings, logger)
        first = client.get_quote(settings.watchlist[0][0])
        print(
            f"[시작] PAPER | 평가자산 10억원 | 종목당 최대 1천만원 | "
            f"목표 +1.5% 고정 | 기준종목 {first.symbol} {first.price:,.0f}원"
        )
        engine_logger = v7515.v7591.CommaSafeLoggerAdapter(logger, {})
        engine = FixedTargetFlexibleEngine(
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
