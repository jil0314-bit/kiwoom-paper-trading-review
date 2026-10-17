#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.13 ALL-PATTERN MARKET-DEFER + FRESH-PRIORITY PAPER
======================================================

V75.12의 모든 진입·청산·손실방어·한글 UTF-8 기능을 그대로 유지한다.

수정 1: 시장 하락 보류 범위를 모든 BUY 패턴으로 확대
- 기존 V75.8은 매물소진 2개 패턴만 시장하락 보류후보로 저장했다.
- V75.13은 정석박스돌파, 임펄스눌림, 투매눌림, 계단식추세,
  전고점 거래폭발 등 모든 유효 BUY 신호를 시장하락 보류후보로 저장한다.
- 매물소진 패턴은 기존의 전용 정밀 재검증을 그대로 사용한다.
- 나머지 패턴은 지수 안정 후 현재가·최신 완성봉·추격·약화·재대량음봉과
  각 패턴의 기존 진입 게이트를 다시 통과해야만 PAPER 진입한다.

수정 2: 최신봉 우선 감시
- 시장보류, pending, 패턴 진행상태, 최신 BUY, 후보점수 상위 순으로 HOT을 재정렬한다.
- HOT 수를 정밀 후보 중심으로 제한해 120~150종목 순차검색 지연을 줄인다.
- 한 번에 여러 완성봉이 밀렸을 때 과거봉은 상태만 갱신하고,
  최신 완성봉 한 개만 진입판정하여 오래된 신호 로그와 중복판정을 줄인다.

주의
- API가 종목별 단건 조회만 제공하므로 모든 종목을 매초 확인할 수는 없다.
- 우선순위 밖 종목은 일반 라운드로빈으로 계속 검색한다.
- PAPER 전용이며 실제 주문 기능은 없다.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

VERSION = "V75.13-ALL-PATTERN-MARKET-DEFER-FRESH-PRIORITY-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7512_FILENAME = "bot_v75_12_no_same_bar_exit_profit_lock_paper.py"
V7512_SHA256 = "8ef85c83f13e8252b160a95a5e9ead0072c8a4689fb7d7049a9f6d4ab5490695"
RUNTIME_DIR = BASE_DIR / "v75_13_runtime"
GENERIC_DEFERRED_FILE = RUNTIME_DIR / "v75_13_all_pattern_deferred.json"
FORBIDDEN_ORDER_NAMES = {
    "send_order", "create_order", "place_order", "order_stock", "submit_order"
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v7512() -> ModuleType:
    path = BASE_DIR / V7512_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7512_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7512_SHA256:
        raise RuntimeError(
            "V75.12 SHA256 불일치—검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V7512_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7512_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.12 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7512 = load_v7512()
v7511 = v7512.v7511
v7510 = v7512.v7510
v7591 = v7511.v7591
v758 = v7511.v758
v757 = v7511.v757
v756 = v7511.v756
v755 = v7511.v755
v754 = v7511.v754
v753 = v7511.v753
KST = v7512.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v7511.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v7511.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class V7513Config:
    generic_deferred_max_minutes: int = 30
    generic_service_sec: float = 2.0
    max_resume_chase_pct: float = 0.008
    max_resume_adverse_pct: float = 0.008
    heavy_bear_body_ratio: float = 0.55
    heavy_bear_volume_ratio: float = 1.50
    fast_hot_count: int = 28

    @classmethod
    def load(cls) -> "V7513Config":
        return cls(
            generic_deferred_max_minutes=env_int(
                "V7513_GENERIC_DEFER_MINUTES", 30, minimum=5, maximum=90
            ),
            generic_service_sec=env_float(
                "V7513_GENERIC_SERVICE_SEC", 2.0, minimum=1.0, maximum=15.0
            ),
            max_resume_chase_pct=env_float(
                "V7513_MAX_RESUME_CHASE", 0.008, minimum=0.003, maximum=0.02
            ),
            max_resume_adverse_pct=env_float(
                "V7513_MAX_RESUME_ADVERSE", 0.008, minimum=0.003, maximum=0.02
            ),
            heavy_bear_body_ratio=env_float(
                "V7513_HEAVY_BEAR_BODY", 0.55, minimum=0.35, maximum=0.90
            ),
            heavy_bear_volume_ratio=env_float(
                "V7513_HEAVY_BEAR_VOLUME", 1.50, minimum=1.10, maximum=3.00
            ),
            fast_hot_count=env_int(
                "V7513_FAST_HOT_COUNT", 28, minimum=16, maximum=45
            ),
        )


@dataclass(slots=True)
class GenericDeferredSignal:
    signal: Any
    name: str
    detected_at: datetime
    expires_at: datetime
    market_reason: str
    baseline_bar_time: datetime | None = None
    last_check_mono: float = 0.0
    check_count: int = 0


def _signal_to_payload(signal: Any) -> dict[str, Any]:
    return v758._signal_to_payload(signal)


def _signal_from_payload(payload: Mapping[str, Any]) -> Any:
    return v758._signal_from_payload(payload)


def _body_ratio(bar: Any) -> float:
    span = max(1e-9, float(bar.high) - float(bar.low))
    return abs(float(bar.close) - float(bar.open)) / span


def _is_heavy_bearish(
    bar: Any,
    recent_volumes: Sequence[float],
    config: V7513Config,
) -> bool:
    baseline = float(statistics.median(recent_volumes)) if recent_volumes else 0.0
    return (
        float(bar.close) < float(bar.open)
        and _body_ratio(bar) >= config.heavy_bear_body_ratio
        and baseline > 0
        and float(bar.volume) >= baseline * config.heavy_bear_volume_ratio
    )


def _fresh_clone(signal: Any, price: float, now: datetime, suffix: str) -> Any:
    return v753.Signal(
        signal.symbol,
        v753.SignalAction.BUY,
        float(signal.score),
        f"{signal.reason}|{suffix}",
        float(price),
        now,
        str(signal.pattern),
        float(signal.stop_loss_pct),
        float(signal.take_profit_pct),
    )


class AllPatternDeferredFreshEngine(v7512.HoldProtectionEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.v7513_config = V7513Config.load()
        super().__init__(*args, **kwargs)
        self._generic_deferred: dict[str, GenericDeferredSignal] = {}
        self._generic_cursor = 0
        self._next_generic_service = 0.0
        self._priority_minute: datetime | None = None
        self._load_generic_deferred()
        if self._generic_deferred:
            # 재시작 직후 바로 매수하지 않고 시장 안정확인을 다시 거친다.
            self._market_decline_seen = True
        cfg = self.v7513_config
        self.logger.warning(
            "[%s] 모든 BUY패턴 시장하락보류 | 일반패턴 보류최대 %d분 | "
            "HOT 정밀우선 %d종목 | 최신완성봉만 진입판정",
            VERSION,
            cfg.generic_deferred_max_minutes,
            cfg.fast_hot_count,
        )

    def _is_supply_special(self, signal: Any) -> bool:
        return v758.MarketPauseResumeEngine._can_defer_signal(self, signal)

    def _can_defer_signal(self, signal: Any) -> bool:
        return (
            getattr(signal, "action", None) == v753.SignalAction.BUY
            and bool(getattr(signal, "symbol", ""))
        )

    def _save_generic_deferred(self) -> None:
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": VERSION,
                "saved_at": datetime.now(KST).isoformat(),
                "candidates": [
                    {
                        "signal": _signal_to_payload(item.signal),
                        "name": item.name,
                        "detected_at": item.detected_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                        "market_reason": item.market_reason,
                        "baseline_bar_time": (
                            item.baseline_bar_time.isoformat()
                            if item.baseline_bar_time is not None else None
                        ),
                        "check_count": item.check_count,
                    }
                    for item in self._generic_deferred.values()
                ],
            }
            temp = GENERIC_DEFERRED_FILE.with_suffix(".tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp, GENERIC_DEFERRED_FILE)
        except Exception as exc:
            self.logger.warning("전체패턴 보류후보 저장 실패(매매는 계속): %s", exc)

    def _load_generic_deferred(self) -> None:
        if not GENERIC_DEFERRED_FILE.exists():
            return
        try:
            payload = json.loads(GENERIC_DEFERRED_FILE.read_text(encoding="utf-8"))
            now = datetime.now(KST)
            for raw in payload.get("candidates", []):
                detected = datetime.fromisoformat(str(raw["detected_at"])).astimezone(KST)
                expires = datetime.fromisoformat(str(raw["expires_at"])).astimezone(KST)
                if detected.date() != now.date() or expires <= now:
                    continue
                signal = _signal_from_payload(raw["signal"])
                baseline_raw = raw.get("baseline_bar_time")
                baseline = (
                    datetime.fromisoformat(str(baseline_raw)).astimezone(KST)
                    if baseline_raw else None
                )
                self._generic_deferred[signal.symbol] = GenericDeferredSignal(
                    signal=signal,
                    name=str(raw.get("name") or signal.symbol),
                    detected_at=detected,
                    expires_at=expires,
                    market_reason=str(raw.get("market_reason") or "시장하락"),
                    baseline_bar_time=baseline,
                    check_count=int(raw.get("check_count", 0)),
                )
            if self._generic_deferred:
                self.logger.warning(
                    "전체패턴 시장보류후보 %d종목 복원—지수 안정 후 재검수",
                    len(self._generic_deferred),
                )
        except Exception as exc:
            self.logger.warning("전체패턴 보류후보 복원 실패(빈 목록으로 계속): %s", exc)
            self._generic_deferred.clear()

    def _drop_generic(self, symbol: str, reason: str) -> None:
        if self._generic_deferred.pop(symbol, None) is not None:
            self._save_generic_deferred()
            self.logger.warning("시장보류후보취소 %s %s", self._label(symbol), reason)

    def _defer_generic_signal(self, signal: Any, market_reason: str) -> bool:
        now = datetime.now(KST)
        old = self._generic_deferred.get(signal.symbol)
        signal_time = v754.normalize_kst(signal.timestamp)
        if old is not None and v754.normalize_kst(old.signal.timestamp) > signal_time:
            return True
        baseline = self.last_bar_time.get(signal.symbol)
        self._generic_deferred[signal.symbol] = GenericDeferredSignal(
            signal=signal,
            name=self.names.get(signal.symbol, signal.symbol),
            detected_at=now,
            expires_at=now + timedelta(
                minutes=self.v7513_config.generic_deferred_max_minutes
            ),
            market_reason=market_reason,
            baseline_bar_time=(
                baseline.astimezone(KST) if baseline is not None else None
            ),
        )
        self.pending_entries.pop(signal.symbol, None)
        self._save_generic_deferred()
        self.logger.warning(
            "시장하락보류 %s | 패턴=%s | 폐기하지 않고 지수안정 후 재검수",
            self._label(signal.symbol),
            signal.pattern,
        )
        return True

    def _defer_market_signal(self, signal: Any, market_reason: str) -> bool:
        # 매물소진은 V75.8의 거래바닥·대량음봉저점 전용 검증을 그대로 사용한다.
        if self._is_supply_special(signal):
            return v758.MarketPauseResumeEngine._defer_market_signal(
                self, signal, market_reason
            )
        return self._defer_generic_signal(signal, market_reason)

    def _priority_score(self, symbol: str) -> float:
        score = 0.0
        if symbol in self._generic_deferred:
            score += 100_000.0
        if symbol in self._deferred_market_signals:
            score += 95_000.0
        if symbol in self.pending_entries:
            score += 90_000.0
        if symbol in getattr(self, "_armed_symbols", set()):
            score += 80_000.0
        if symbol in self.strategy.capitulation_cache:
            score += 70_000.0
        if symbol in self.strategy.impulse_cache:
            score += 70_000.0
        if symbol in getattr(self, "_stair_evidence", {}):
            score += 65_000.0
        latest = self.latest_signals.get(symbol)
        if latest is not None and latest.action == v753.SignalAction.BUY:
            score += 75_000.0
        meta = self.candidate_meta.get(symbol)
        if meta is not None:
            score += float(getattr(meta, "score", 0.0) or 0.0) * 10.0
            if bool(getattr(meta, "dual_buy", False)):
                score += 500.0
        bars = self.strategy.bars.get(symbol)
        if bars:
            recent = list(bars)[-20:]
            if recent:
                peak = max(float(bar.high) for bar in recent)
                if peak > 0 and float(recent[-1].close) >= peak * 0.985:
                    score += 1_000.0
        return score

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        mandatory = list(dict.fromkeys(
            list(self._generic_deferred)
            + list(self._deferred_market_signals)
            + list(self.pending_entries)
        ))
        universe = list(dict.fromkeys(
            mandatory + self.hot_symbols + list(self._current_symbols())
        ))
        ranked = sorted(
            (symbol for symbol in universe if symbol not in self.broker.positions),
            key=lambda symbol: (self._priority_score(symbol), symbol),
            reverse=True,
        )
        selected = mandatory + [
            symbol for symbol in ranked if symbol not in set(mandatory)
        ][: self.v7513_config.fast_hot_count]
        self.hot_symbols = list(dict.fromkeys(selected))
        current_minute = datetime.now(KST).replace(second=0, microsecond=0)
        if self._priority_minute != current_minute:
            self._priority_minute = current_minute
            self.hot_cursor = 0

    def _generic_resume_validation(
        self,
        item: GenericDeferredSignal,
        quote: Any,
        bars: Sequence[Any],
    ) -> tuple[bool, str, Any | None]:
        if not bars:
            return False, "최신완성분봉없음", None
        latest_bar = bars[-1]
        current_price = float(quote.price)
        original_price = max(0.01, float(item.signal.reference_price))
        drift = current_price / original_price - 1.0
        pattern_chase = self._max_chase_for_pattern(str(item.signal.pattern))
        chase_limit = min(
            self.v7513_config.max_resume_chase_pct,
            max(0.003, float(pattern_chase)),
        )
        if drift > chase_limit:
            return False, f"보류중추격초과_{drift:+.2%}>{chase_limit:.2%}", None
        adverse_limit = min(
            self.v7513_config.max_resume_adverse_pct,
            max(0.004, float(item.signal.stop_loss_pct) * 0.50),
        )
        if drift < -adverse_limit:
            return False, f"보류중종목약화_{drift:+.2%}<-{adverse_limit:.2%}", None

        recent_volumes = [float(bar.volume) for bar in bars[-11:-1]]
        if _is_heavy_bearish(latest_bar, recent_volumes, self.v7513_config):
            return False, "최신봉재대량음봉", None

        latest_signal = self.latest_signals.get(item.signal.symbol)
        source_signal = item.signal
        if (
            latest_signal is not None
            and latest_signal.action == v753.SignalAction.BUY
            and v754.normalize_kst(latest_signal.timestamp)
            >= v754.normalize_kst(item.signal.timestamp)
        ):
            source_signal = latest_signal
        fresh_signal = _fresh_clone(
            source_signal,
            current_price,
            datetime.now(KST),
            "시장하락보류후지수안정재검수",
        )
        valid, reason = self._pending_signal_valid(fresh_signal, current_price)
        if not valid:
            return False, reason, fresh_signal
        return True, "현재가·최신봉·기존패턴게이트재통과", fresh_signal

    def _service_one_generic_deferred(self) -> None:
        now_mono = time.monotonic()
        if (
            now_mono < self._next_generic_service
            or not self._generic_deferred
        ):
            return
        self._next_generic_service = now_mono + self.v7513_config.generic_service_sec
        now = datetime.now(KST)

        for symbol, item in list(self._generic_deferred.items()):
            if item.expires_at <= now:
                self._drop_generic(symbol, "보류시간만료")
            elif symbol in self.broker.positions:
                self._drop_generic(symbol, "이미보유")
        if not self._generic_deferred:
            return

        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            self._show_throttled_error(
                "v7513-generic-wait",
                f"전체패턴 시장하락보류후보 {len(self._generic_deferred)}종목 "
                f"최우선감시—{market_reason}",
                30.0,
            )
            return

        symbols = list(self._generic_deferred)
        symbol = symbols[self._generic_cursor % len(symbols)]
        self._generic_cursor += 1
        item = self._generic_deferred[symbol]
        if now_mono - item.last_check_mono < self.v7513_config.generic_service_sec:
            return
        item.last_check_mono = now_mono
        item.check_count += 1

        try:
            quote = self.client.get_quote(symbol)
            bars = [
                bar for bar in self.client.get_minute_bars(
                    symbol, limit=40, completed_only=True
                )
                if v753.regular_session_bar(bar.timestamp)
            ]
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"v7513-generic:{symbol}",
                f"시장보류후보 재검수 실패 {self._label(symbol)}: "
                f"{v753.brief_error(exc)}",
                60.0,
            )
            return
        selected = v757._same_session_bars(bars)
        if not selected:
            return

        # 전략 상태를 최신 완성봉까지 먼저 따라잡는다. 이 과정에서 새 BUY가 나오면
        # 현재 시장이 안정된 상태이므로 기존 모든 진입 게이트가 즉시 다시 적용된다.
        self.latest_quotes[symbol] = quote
        self._refresh_actual_bars(symbol, quote)
        if symbol in self.broker.positions or symbol in self.pending_entries:
            self._drop_generic(symbol, "재검수후진입경로전환")
            return

        valid, reason, fresh_signal = self._generic_resume_validation(
            item, quote, selected
        )
        if not valid or fresh_signal is None:
            # 단순 시장 안정 대기 사유는 유지하고, 구조·추격·약화 실패는 취소한다.
            waiting_prefixes = ("현재세션", "최신완성분봉", "시장신규매수보류")
            if reason.startswith(waiting_prefixes):
                self._show_throttled_error(
                    f"v7513-generic-structure:{symbol}",
                    f"시장안정후 종목재확인중 {self._label(symbol)} {reason}",
                    30.0,
                )
            else:
                self._drop_generic(symbol, reason)
            return

        self.logger.warning(
            "시장하락멈춤 전체패턴 재진입검증 %s | 패턴=%s | %s",
            self._label(symbol), item.signal.pattern, reason,
        )
        entered = super()._attempt_entry(fresh_signal)
        if entered or symbol in self.pending_entries:
            self._drop_generic(symbol, "재검수통과")
        else:
            # 기존 패턴 게이트가 거절한 경우 오래된 원신호를 계속 반복하지 않는다.
            self._drop_generic(symbol, "기존패턴게이트재검수불통과")

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._drop_generic(signal.symbol, "매수완료")
        return entered

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        # 보유종목은 청산 신호를 놓치면 안 되므로 검증된 부모 로직을 그대로 사용한다.
        if symbol in self.broker.positions:
            super()._refresh_actual_bars(symbol, quote)
            return
        bars = [
            bar for bar in self.client.get_minute_bars(
                symbol, limit=25, completed_only=True
            )
            if v753.regular_session_bar(bar.timestamp)
        ]
        if not bars:
            return
        last = self.last_bar_time.get(symbol)
        new_bars = [bar for bar in bars if last is None or bar.timestamp > last]
        if not new_bars:
            return

        # 밀린 과거봉은 전략 상태만 순서대로 갱신한다. BUY가 나와도 실행·폐기 로그를
        # 만들지 않는다. 최신 완성봉 하나만 실제 진입판정을 받는다.
        for old_bar in new_bars[:-1]:
            signal = self.strategy.on_bar(old_bar, None)
            self.latest_signals[symbol] = signal
            self.last_bar_time[symbol] = old_bar.timestamp

        newest = new_bars[-1]
        completed_at = newest.timestamp.astimezone(KST) + timedelta(seconds=60)
        age = max(0.0, (datetime.now(KST) - completed_at).total_seconds())
        self._on_bar(
            newest,
            quote,
            allow_entry=age <= self.settings.max_bar_signal_age_sec,
            bar_age_sec=age,
        )
        self.last_bar_time[symbol] = newest.timestamp
        if self.risk.data_halted and not self.state_integrity_block:
            self.recovery_successes += 1
            if self.recovery_successes >= 2:
                self.risk.set_data_halt(False)
                self.recovery_successes = 0
                self.logger.warning("시장데이터 정상복구")

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        super()._service_symbol(symbol, hot=hot)
        self._service_one_generic_deferred()


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.12-SHA256고정",
        sha256_file(BASE_DIR / V7512_FILENAME) == V7512_SHA256,
        V7512_SHA256[:16] + "…",
    )
    inherited = v7512.self_check()
    inherited_failures = [name for name, ok, _ in inherited if not ok]
    record(
        "V75.12기존검증위임",
        not inherited_failures,
        f"{len(inherited)}항목 / 실패={inherited_failures or '없음'}",
    )

    engine = object.__new__(AllPatternDeferredFreshEngine)
    buy = v753.Signal(
        "751300", v753.SignalAction.BUY, 100.0, "테스트", 10000.0,
        datetime(2026, 7, 10, 14, 0, tzinfo=KST), "정석박스돌파", 0.016, 0.015
    )
    hold = v753.Signal(
        "751300", v753.SignalAction.HOLD, 0.0, "대기", 10000.0,
        datetime(2026, 7, 10, 14, 0, tzinfo=KST), "없음", 0.0, 0.0
    )
    record("모든BUY보류가능", engine._can_defer_signal(buy), "정석박스돌파 포함")
    record("HOLD보류금지", not engine._can_defer_signal(hold), "BUY만 보류")

    cfg = V7513Config()
    bar_type = v753.Bar
    bars = [
        bar_type("751300", datetime(2026, 7, 10, 13, i, tzinfo=KST),
                 100.0, 101.0, 99.5, 100.5, 1000)
        for i in range(10)
    ]
    heavy = bar_type(
        "751300", datetime(2026, 7, 10, 13, 10, tzinfo=KST),
        101.0, 101.2, 98.0, 98.5, 2000
    )
    record(
        "재대량음봉탐지",
        _is_heavy_bearish(heavy, [float(bar.volume) for bar in bars], cfg),
        "몸통·거래량 동시 확인",
    )
    clone = _fresh_clone(buy, 10050.0, datetime(2026, 7, 10, 14, 2, tzinfo=KST), "재검수")
    record(
        "재검수신호재생성",
        clone.timestamp > buy.timestamp and clone.reference_price == 10050.0,
        "현재가·현재시각으로 재생성",
    )
    record("HOT정밀우선28", cfg.fast_hot_count == 28, "일반후보는 라운드로빈 유지")

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    record(
        "실주문함수부재",
        not (function_names & FORBIDDEN_ORDER_NAMES),
        f"금지함수={sorted(function_names & FORBIDDEN_ORDER_NAMES) or '없음'}",
    )
    record(
        "매물소진전용검증보존",
        "MarketPauseResumeEngine._defer_market_signal" in source,
        "매물소진은 V75.8 전용 재검증",
    )
    record(
        "과거밀린봉진입금지",
        "for old_bar in new_bars[:-1]" in source
        and "newest = new_bars[-1]" in source,
        "최신 완성봉만 _on_bar 진입판정",
    )
    record(
        "일반패턴상태저장",
        "v75_13_all_pattern_deferred.json" in source
        and "os.replace(temp, GENERIC_DEFERRED_FILE)" in source,
        "원자적 JSON 저장",
    )
    record(
        "한글UTF8유지",
        "# -*- coding: utf-8 -*-" in source,
        "UTF-8 소스·실행파일",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{VERSION} — 모든패턴 시장보류·최신봉우선 PAPER"
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
        engine = AllPatternDeferredFreshEngine(
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
