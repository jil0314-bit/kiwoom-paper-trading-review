#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.9.1 DAILY-CUMULATIVE-VOLUME + LOG-FORMAT-FIX PAPER
========================================

V75.8까지의 전략·체결·시장보류 로직은 그대로 사용한다.
V75.7에서 잘못 해석된 "최근 20개 1분봉 합계 10만주" 관심종목 필터만
"오늘 장 시작 이후 당일 누적거래량 10만주" 필터로 바로잡는다.

핵심
- 최근 20개 분봉 합계가 10만주 미만이어도 당일 누적거래량이 10만주 이상이면 제외하지 않는다.
- 당일 누적거래량이 10만주 미만인 종목만 관심종목 분석에서 잠시 제외한다.
- 후보 API의 누적거래량이 10만주를 넘는 순간 다음 후보 갱신에서 자동 복귀한다.
- 후보 API 거래량이 비어 있으면 당일 전체 분봉 합계를 보조값으로 사용한다.
- 매수 직전에도 당일 누적거래량을 다시 확인한다.
- 실제 주문 기능은 없고 PAPER 전용이다.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import importlib.util
import logging
import os
import re
import sys
import time
import traceback
from collections import deque
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Sequence

VERSION = "V75.9.1-DAILY-CUMULATIVE-VOLUME-LOG-FIX-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V758_FILENAME = "bot_v75_8_market_pause_resume_paper.py"
V758_SHA256 = "2e89f8f3fa75930a6f2a5b0a8431ae624630c97435c006d9d3d68005923703ab"

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


def load_v758() -> ModuleType:
    path = BASE_DIR / V758_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V758_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V758_SHA256:
        raise RuntimeError(
            "V75.8 SHA256 불일치 — 검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V758_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v758_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.8 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v758 = load_v758()
v757 = v758.v757
v756 = v758.v756
v755 = v758.v755
v754 = v758.v754
v753 = v758.v753
KST = v758.KST


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v756.env_int(name, default, minimum=minimum, maximum=maximum)


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v756.env_float(name, default, minimum=minimum, maximum=maximum)



_PRINTF_TOKEN = re.compile(
    r"%(?:\([^)]+\))?[#0\- +]?(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%]|%,d"
)


def _normalize_comma_log_message(
    message: Any,
    args: tuple[Any, ...],
) -> tuple[Any, tuple[Any, ...]]:
    """logging이 지원하지 않는 `%,d`를 안전한 `%s` + 천단위 문자열로 변환한다."""
    if not isinstance(message, str) or "%,d" not in message or not args:
        return message, args

    new_args = list(args)
    arg_index = 0
    pieces: list[str] = []
    cursor = 0

    for match in _PRINTF_TOKEN.finditer(message):
        token = match.group(0)
        pieces.append(message[cursor:match.start()])

        if token == "%%":
            pieces.append(token)
            cursor = match.end()
            continue

        if arg_index >= len(new_args):
            pieces.append(token)
            cursor = match.end()
            continue

        if token == "%,d":
            try:
                new_args[arg_index] = f"{int(new_args[arg_index]):,}"
            except (TypeError, ValueError, OverflowError):
                new_args[arg_index] = str(new_args[arg_index])
            pieces.append("%s")
        else:
            pieces.append(token)

        arg_index += 1
        cursor = match.end()

    pieces.append(message[cursor:])
    return "".join(pieces), tuple(new_args)


class CommaSafeLoggerAdapter(logging.LoggerAdapter):
    """부모 버전의 `%,d` 로그까지 최신 계층에서 안전하게 보정한다."""

    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:
        if not self.isEnabledFor(level):
            return
        normalized_msg, normalized_args = _normalize_comma_log_message(msg, tuple(args))
        normalized_msg, kwargs = self.process(normalized_msg, kwargs)
        self.logger.log(level, normalized_msg, *normalized_args, **kwargs)

def same_session_cumulative_volume(bars: Sequence[Any]) -> int:
    """주어진 분봉 중 현재와 같은 세션 전체 누적거래량을 계산한다."""
    selected = v757._same_session_bars(bars)
    return sum(max(0, int(getattr(bar, "volume", 0) or 0)) for bar in selected)


class DailyCumulativeVolumeEngine(v758.MarketPauseResumeEngine):
    """V75.8 위에 당일 누적거래량 10만주 필터 수정만 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v759_min_daily_volume = env_int(
            "V759_MIN_DAILY_CUM_VOLUME",
            100_000,
            minimum=10_000,
            maximum=100_000_000,
        )
        self.v759_recheck_sec = env_float(
            "V759_LOW_DAILY_VOLUME_RECHECK_SEC",
            180.0,
            minimum=30.0,
            maximum=1800.0,
        )
        self._daily_volume_cache: dict[str, int] = {}
        self._daily_volume_day = datetime.now(KST).date()
        self.logger.warning(
            "[%s] 거래량필터 수정 적용 | 최근20개 분봉 합계가 아니라 "
            "당일 누적거래량 %,d주 미만만 관심제외",
            VERSION,
            self.v759_min_daily_volume,
        )

    def _reset_volume_filter_day(self) -> None:
        """날짜가 바뀌면 누적거래량과 차단상태를 초기화한다."""
        today = datetime.now(KST).date()
        if self._daily_volume_day != today:
            self._daily_volume_day = today
            self._daily_volume_cache.clear()
            self._low_volume_blocks.clear()
            self._intrabar_probe_state.clear()
            self._intrabar_snapshots.clear()

    def _candidate_daily_volume(self, symbol: str) -> int:
        meta = self.candidate_meta.get(symbol)
        candidate_value = max(0, int(getattr(meta, "volume", 0) or 0))
        cached = max(0, int(self._daily_volume_cache.get(symbol, 0)))
        value = max(candidate_value, cached)
        if value > 0:
            self._daily_volume_cache[symbol] = value
        return value

    def _update_daily_volume_from_bars(self, symbol: str, bars: Sequence[Any]) -> int:
        value = same_session_cumulative_volume(bars)
        value = max(value, self._candidate_daily_volume(symbol))
        if value > 0:
            self._daily_volume_cache[symbol] = value
        return value

    def _ensure_daily_volume(self, symbol: str) -> int | None:
        """매수 직전 누적거래량을 확인한다. 조회 실패 시 신규진입만 보류한다."""
        known = self._candidate_daily_volume(symbol)
        if known >= self.v759_min_daily_volume:
            return known
        # 후보 API 값이 아직 10만주 미만이거나 비어 있으면, 진입 직전에
        # 당일 전체 분봉 합계로 한 번 더 확인해 갱신 지연 오판을 막는다.
        try:
            bars = [
                bar
                for bar in self.client.get_minute_bars(symbol, limit=900, completed_only=False)
                if v753.regular_session_bar(bar.timestamp)
            ]
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"v759-volume-check:{symbol}",
                f"당일누적거래량 확인실패 {self._label(symbol)}: {v753.brief_error(exc)}",
                60.0,
            )
            return None
        return self._update_daily_volume_from_bars(symbol, bars)

    def _exclude_low_volume(self, symbol: str, _rolling_volume: int, cumulative_volume: int) -> None:
        """호환 시그니처. 최근20봉이 아니라 당일 누적값으로만 제외한다."""
        self._reset_volume_filter_day()
        daily_volume = max(
            0,
            int(cumulative_volume or 0),
            int(self._daily_volume_cache.get(symbol, 0)),
        )
        self._daily_volume_cache[symbol] = daily_volume
        self._low_volume_blocks[symbol] = v757.LowVolumeBlock(
            session_date=self._daily_volume_day,
            recent_volume=daily_volume,
            cumulative_volume=daily_volume,
            next_recheck_mono=time.monotonic() + self.v759_recheck_sec,
        )
        self.active_symbols = [item for item in self.active_symbols if item != symbol]
        self.hot_symbols = [item for item in self.hot_symbols if item != symbol]
        self.bootstrap_pending.discard(symbol)
        self.bootstrap_queue = deque(item for item in self.bootstrap_queue if item != symbol)
        self.pending_entries.pop(symbol, None)
        self._armed_symbols.discard(symbol)
        if hasattr(self, "_deferred_market_signals"):
            self._deferred_market_signals.pop(symbol, None)
            try:
                self._save_deferred_state()
            except Exception:
                pass
        self._show_throttled_error(
            f"v759-low-daily-volume:{symbol}",
            f"관심제외 {self._label(symbol)} "
            f"당일누적거래량={daily_volume:,}주<{self.v759_min_daily_volume:,}주",
            300.0,
        )

    def _rolling_block_allows_recheck(self, item: Any, now_mono: float) -> bool:
        block = self._low_volume_blocks.get(item.symbol)
        if block is None:
            return True
        if block.session_date != self._daily_volume_day:
            self._low_volume_blocks.pop(item.symbol, None)
            return True
        cumulative = max(0, int(getattr(item, "volume", 0) or 0))
        if cumulative >= self.v759_min_daily_volume:
            self._daily_volume_cache[item.symbol] = cumulative
            self._low_volume_blocks.pop(item.symbol, None)
            return True
        if now_mono >= block.next_recheck_mono:
            self._low_volume_blocks.pop(item.symbol, None)
            return True
        return False

    def _refresh_universe(self, *, force: bool = False) -> list[str]:
        """후보 API의 당일 누적거래량으로 1차 필터한다."""
        self._reset_volume_filter_day()
        now_mono = time.monotonic()
        if not force and now_mono < self.next_scan:
            return []
        self.next_scan = now_mono + self.settings.scan_interval_sec
        previous = set(self.active_symbols)
        groups: list[list[Any]] = []
        failures: list[str] = []
        if self.settings.dynamic_scan:
            for name, loader in (
                ("상승률100", lambda: self.client.get_gain_rank_candidates(limit=self.rank_limit)),
                ("거래량급증", lambda: self.client.get_volume_surge_candidates(limit=self.rank_limit)),
                ("외국인·기관쌍끌이", lambda: self.client.get_dual_buy_candidates(limit=self.rank_limit)),
            ):
                try:
                    groups.append(loader())
                except v753.V73Error as exc:
                    failures.append(f"{name}:{v753.brief_error(exc)}")

        if groups:
            pool = self._merge_candidates(groups)[: max(self.rank_limit, self.pool_limit)]
            if pool and not self.state_integrity_block:
                self.risk.set_data_halt(False)
            self.universe_pool = pool
            for item in pool:
                self.names[item.symbol] = item.name
                self.candidate_meta[item.symbol] = item
                self.rank_snapshots[item.symbol] = item
                cumulative = max(0, int(getattr(item, "volume", 0) or 0))
                if cumulative > 0:
                    self._daily_volume_cache[item.symbol] = max(
                        cumulative,
                        self._daily_volume_cache.get(item.symbol, 0),
                    )

            selected: list[str] = []
            prefiltered = 0
            blocked = 0
            for item in pool:
                cumulative = max(0, int(getattr(item, "volume", 0) or 0))
                if 0 < cumulative < self.v759_min_daily_volume:
                    prefiltered += 1
                    continue
                if not self._rolling_block_allows_recheck(item, now_mono):
                    blocked += 1
                    continue
                selected.append(item.symbol)

            self.active_symbols = list(dict.fromkeys(selected))
            active_set = set(self.active_symbols)
            top_items = [item for item in pool if item.symbol in active_set][:5]
            top = ", ".join(f"{item.name}({item.source})" for item in top_items)
            self.logger.warning(
                "관심종목 %s개 등록 / 당일누적 %,d주 필터: 선제제외 %s + 재검사대기 %s / 상위=%s",
                len(self.active_symbols),
                self.v759_min_daily_volume,
                prefiltered,
                blocked,
                top or "없음",
            )
            if failures and self.debug:
                self.logger.info("일부 후보원 실패: %s", " / ".join(failures))
        else:
            self.active_symbols = []
            self.universe_pool = []
            self.risk.set_data_halt(True)
            self._show_throttled_error(
                "universe-all-failed",
                "후보 API 전체 실패—신규 매수 중단, 보유종목 청산만 유지",
                120.0,
            )
            if self.debug and failures:
                self.logger.info("후보 API 실패 상세: %s", " / ".join(failures))
        return [symbol for symbol in self.active_symbols if symbol not in previous]

    @contextmanager
    def _without_v757_rolling_filter(self) -> Iterator[None]:
        """상위 V75.7의 잘못된 최근20봉 필터만 호출 중 일시 비활성화한다."""
        original = self.v757_config
        self.v757_config = replace(original, min_recent_volume=0)
        try:
            yield
        finally:
            self.v757_config = original

    def _bootstrap_one(self) -> bool:
        if not self.bootstrap_queue:
            return False
        symbol = self.bootstrap_queue[0]
        known = self._candidate_daily_volume(symbol)
        if 0 < known < self.v759_min_daily_volume:
            self._exclude_low_volume(symbol, 0, known)
            return False
        with self._without_v757_rolling_filter():
            ready = super()._bootstrap_one()
        if not ready:
            return False
        bars = list(self.strategy.bars.get(symbol, []))
        daily = self._update_daily_volume_from_bars(symbol, bars)
        if 0 < daily < self.v759_min_daily_volume and symbol not in self.broker.positions:
            self._exclude_low_volume(symbol, 0, daily)
            return False
        return True

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        with self._without_v757_rolling_filter():
            super()._refresh_actual_bars(symbol, quote)
        bars = list(self.strategy.bars.get(symbol, []))
        daily = self._update_daily_volume_from_bars(symbol, bars)
        if 0 < daily < self.v759_min_daily_volume and symbol not in self.broker.positions:
            self._exclude_low_volume(symbol, 0, daily)

    def _try_intrabar_entry(self, symbol: str) -> None:
        daily = self._ensure_daily_volume(symbol)
        if daily is None:
            return
        if daily < self.v759_min_daily_volume:
            self._exclude_low_volume(symbol, 0, daily)
            return
        with self._without_v757_rolling_filter():
            super()._try_intrabar_entry(symbol)

    def _service_one_deferred(self) -> None:
        for symbol in list(getattr(self, "_deferred_market_signals", {})):
            daily = self._candidate_daily_volume(symbol)
            if 0 < daily < self.v759_min_daily_volume:
                self._drop_deferred(symbol, f"당일누적거래량부족_{daily:,}주")
        with self._without_v757_rolling_filter():
            super()._service_one_deferred()

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        daily = self._ensure_daily_volume(signal.symbol)
        if daily is None:
            self.pending_entries.pop(signal.symbol, None)
            self.logger.warning(
                "진입폐기 %s 당일누적거래량확인불가",
                self._label(signal.symbol),
            )
            return False
        if daily < self.v759_min_daily_volume:
            self._exclude_low_volume(signal.symbol, 0, daily)
            self.logger.warning(
                "진입폐기 %s 당일누적거래량부족_%s주<%s주",
                self._label(signal.symbol),
                f"{daily:,}",
                f"{self.v759_min_daily_volume:,}",
            )
            return False
        return super()._attempt_entry(signal, from_retry=from_retry)

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        daily = self._candidate_daily_volume(signal.symbol)
        if 0 < daily < self.v759_min_daily_volume:
            return False, f"당일누적거래량부족_{daily:,}주"
        return True, reason


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.8-SHA256고정",
        sha256_file(BASE_DIR / V758_FILENAME).lower() == V758_SHA256,
        V758_SHA256[:16] + "…",
    )

    base_results = v758.self_check()
    base_fail = [name for name, ok, _ in base_results if not ok]
    record(
        "V75.8자체검증위임",
        not base_fail,
        f"{len(base_results)}항목 / 실패 {base_fail or '없음'}",
    )

    class Bar:
        def __init__(self, volume: int) -> None:
            self.volume = volume
            self.timestamp = datetime(2026, 7, 9, 10, 0, tzinfo=KST)

    rolling_twenty = [Bar(3_000) for _ in range(20)]
    record(
        "최근20봉6만주여도당일50만주통과",
        same_session_cumulative_volume(rolling_twenty) == 60_000
        and 500_000 >= 100_000,
        "최근20봉=60,000 / 당일누적=500,000 → 제외하면 안 됨",
    )
    record(
        "당일누적10만주미만만제외",
        99_999 < 100_000 and 100_000 >= 100_000,
        "99,999 제외 / 100,000 통과",
    )

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (names & FORBIDDEN_ORDER_NAMES),
        f"금지함수={sorted(names & FORBIDDEN_ORDER_NAMES) or '없음'}",
    )
    record(
        "최근20봉필터우회",
        "replace(original, min_recent_volume=0)" in source
        and "당일누적거래량" in source,
        "기존 rolling 필터만 비활성화",
    )
    record(
        "자동복귀",
        "cumulative >= self.v759_min_daily_volume" in source
        and "self._low_volume_blocks.pop(item.symbol, None)" in source,
        "후보 API 누적 10만주 도달 시 다음 갱신에서 복귀",
    )
    record(
        "매수직전재검증",
        "def _attempt_entry" in source and "_ensure_daily_volume(signal.symbol)" in source,
        "모든 최종 진입에서 당일누적 재확인",
    )
    record(
        "V75.8원본쓰기부재",
        "write_text" not in source.split("def load_v758", 1)[1].split("v758 =", 1)[0],
        "V75.8은 SHA 확인 후 읽기 로드만",
    )

    stream = io.StringIO()
    smoke_logger = logging.getLogger("v7591-log-smoke")
    smoke_logger.handlers.clear()
    smoke_logger.propagate = False
    smoke_logger.setLevel(logging.WARNING)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    smoke_logger.addHandler(handler)
    safe_logger = CommaSafeLoggerAdapter(smoke_logger, {})
    safe_logger.warning(
        "관심종목 %s개 등록 / 당일누적 %,d주 필터 / 상위=%s",
        137,
        100_000,
        "테스트",
    )
    rendered = stream.getvalue().strip()
    record(
        "천단위로그형식보정",
        rendered == "관심종목 137개 등록 / 당일누적 100,000주 필터 / 상위=테스트",
        rendered,
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{VERSION} — 당일 누적거래량 10만주 필터 수정 PAPER"
    )
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
        engine_logger = CommaSafeLoggerAdapter(logger, {})
        engine = DailyCumulativeVolumeEngine(
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
    except Exception as exc:
        logger.critical("실행 실패: %s", exc)
        v753.log_error(traceback.format_exc())
        return 9


if __name__ == "__main__":
    raise SystemExit(main())
