#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.81 PAPER — V75.79 link/UI preserved, profit-risk guard added.

The user-confirmed V75.79 HeroMoon link and approved dashboard remain immutable.
Only the market-data client, signal detector and PAPER order/stop policy are
installed below the pinned parent runtime.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import ModuleType
from typing import Any, Callable, Sequence

from v7580_gem_three_minute_logic import (
    BLOCKED,
    CONFIRM_BUY,
    FIRST_BUY,
    HARD_STOP_PCT,
    READY,
    TAKE_PROFIT_PCT,
    WATCH,
    GemDecision,
    evaluate_gem_three_minute,
    evaluate_intrabar_projection,
)
from v7581_profitability_guard import (
    PerformanceSnapshot,
    ProfitContract,
    analyze_performance,
    calculate_profit_contract,
    format_profitability_lines,
    read_trade_rows,
)


VERSION = "V75.81-COMPLETE-PROFIT-GUARD-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_79_smooth_continuous_paper.py"
PARENT_SHA256 = "82158bda28f73942889d13f51f190ea6c07a8b1db615707af3b64ed2009b7288"
APPROVED_GUI_SHA256 = "6e7e2e87b55326bed7a067b51939170088291224fd5b31838c5c501df02d4aa3"
BAR_SECONDS = 180
BAR_MINUTES = 3


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return int(default)


MAX_CONCURRENT_POSITIONS = _positive_env_int(
    "V7581_MAX_CONCURRENT_POSITIONS", 20
)
QUALITY_TRADES_CSV = BASE_DIR.parent / "runtime" / "entry_quality_trades_v2.csv"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7580_immutable_v7579_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525
v753 = v7525.v753
KST = v753.KST
diag = parent.diag


def _module_from_mro(cls: type[Any], class_name: str) -> ModuleType:
    for candidate in cls.__mro__:
        if candidate.__name__ == class_name:
            module = sys.modules.get(candidate.__module__)
            if module is None:
                break
            return module
    raise RuntimeError(f"실행 MRO에서 {class_name} 모듈을 찾지 못했습니다")


BaseEngine = v7525.V7525Engine
BaseThreeMinuteClient = v753.KiwoomRestClient
v73 = _module_from_mro(BaseEngine, "V7573ThreeMinuteEngine")
market_module = _module_from_mro(BaseEngine, "MarketPauseResumeEngine")
v756 = v73.v756
v7514 = v73.v7514
v7515 = v73.v7515
v7530 = v73.v7530
v7539 = v73.v7539
v7540 = v73.v7540

_ORIGINAL_ACTIVE_DETECT = v73.detect_v7573
_ORIGINAL_ACTIVE_ASSESS = v73.assess_v7573


def _raw_one_minute_getter() -> Callable[..., Any]:
    """Resolve by class identity/name, never by an MRO numeric index."""
    for candidate in BaseThreeMinuteClient.__mro__:
        method = candidate.__dict__.get("get_minute_bars")
        if (
            candidate is not BaseThreeMinuteClient
            and candidate.__name__ == "KiwoomRestClient"
            and callable(method)
        ):
            return method
    raise RuntimeError("원본 1분봉 REST 메서드를 찾지 못했습니다")


RAW_ONE_MINUTE_GETTER = _raw_one_minute_getter()


class V7580QualityBar(v753.Bar):
    """Three-minute bar carrying non-trading metadata used only for entry safety.

    Exit/stop paths still receive every wall-clock-completed bucket.  Entry
    logic can therefore reject a bucket built from fewer than three distinct
    one-minute records without weakening position monitoring.
    """

    __slots__ = ("source_minutes", "source_complete", "source_minute_keys")

    def __init__(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        source_minutes: int,
        source_complete: bool,
        source_minute_keys: tuple[str, ...],
    ) -> None:
        super().__init__(symbol, timestamp, open, high, low, close, volume)
        self.source_minutes = int(source_minutes)
        self.source_complete = bool(source_complete)
        self.source_minute_keys = tuple(source_minute_keys)


def _minute_stamp(timestamp: datetime) -> datetime:
    value = v73.v754.normalize_kst(timestamp)
    return value.replace(second=0, microsecond=0)


def aggregate_v7580_three_minute_bars(
    one_minute_bars: Sequence[Any],
    *,
    now: datetime | None = None,
    limit: int = 0,
) -> list[V7580QualityBar]:
    """Aggregate, de-duplicate and label completed three-minute buckets.

    A completed wall-clock bucket is kept even when one source minute is
    missing so existing exit and stop monitoring remains intact.  The quality
    label is consumed by the entry gate below.
    """

    grouped: dict[datetime, dict[datetime, Any]] = {}
    for bar in sorted(one_minute_bars, key=lambda item: item.timestamp):
        minute = _minute_stamp(bar.timestamp)
        bucket = v73._bucket_start(minute)
        grouped.setdefault(bucket, {})[minute] = bar

    current = v73.v754.normalize_kst(now or datetime.now(KST))
    output: list[V7580QualityBar] = []
    for bucket, minute_map in sorted(grouped.items()):
        if bucket + timedelta(seconds=BAR_SECONDS) > current:
            continue
        expected = tuple(bucket + timedelta(minutes=offset) for offset in range(3))
        items = [minute_map[key] for key in sorted(minute_map)]
        if not items:
            continue
        first, last = items[0], items[-1]
        present = tuple(key.strftime("%Y-%m-%d %H:%M") for key in sorted(minute_map))
        output.append(V7580QualityBar(
            symbol=str(first.symbol),
            timestamp=bucket,
            open=float(first.open),
            high=max(float(item.high) for item in items),
            low=min(float(item.low) for item in items),
            close=float(last.close),
            volume=sum(max(0, int(item.volume)) for item in items),
            source_minutes=len(minute_map),
            source_complete=all(key in minute_map for key in expected),
            source_minute_keys=present,
        ))
    return output[-limit:] if limit > 0 else output


def _entry_data_quality_issue(
    bars: Sequence[Any],
    *,
    decision: GemDecision | None = None,
    lookback: int = 12,
) -> str:
    """Reject incomplete bars across the exact decision/evidence span.

    Without a recognised decision the recent window protects inherited
    fallback detectors.  With a decision, inspection starts eight bars before
    its flush because those bars feed median/p75/rise-high/rise-low evidence.
    """

    items = list(bars)
    if decision is not None and int(decision.flush_index) >= 0:
        inspected = items[max(0, int(decision.flush_index) - 8) :]
    else:
        inspected = items[-max(1, int(lookback)) :]
    failures: list[str] = []
    for bar in inspected:
        # Synthetic/legacy bars have no metadata and retain previous behaviour.
        if not hasattr(bar, "source_complete"):
            continue
        if bool(getattr(bar, "source_complete", False)):
            continue
        timestamp = v73.v754.normalize_kst(bar.timestamp).strftime("%H:%M")
        failures.append(f"{timestamp}({int(getattr(bar, 'source_minutes', 0))}/3)")
    return "완성3분봉원천결측=" + ",".join(failures) if failures else ""


def _completed_bar_hard_stop_breached(position: Any, bar: Any) -> bool:
    """Hard-stop policy: an observed post-entry -5% breach remains a breach.

    The current quote may already have recovered when a completed-bar fallback
    notices a low that REST tick polling missed.  Ignoring it would silently
    turn the requested hard stop into an optional close-price stop.
    """

    return float(bar.low) <= float(position.stop_price)


class GemThreeMinuteKiwoomClient(BaseThreeMinuteClient):
    """Return completed three-minute bars while retaining raw one-minute internals."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v7580_raw_cache: dict[str, list[Any]] = {}

    def get_one_minute_bars(
        self,
        symbol: str,
        *,
        limit: int = 120,
        completed_only: bool = False,
    ) -> list[Any]:
        values = list(
            RAW_ONE_MINUTE_GETTER(
                self,
                symbol,
                limit=max(3, int(limit)),
                completed_only=bool(completed_only),
            )
        )
        self.v7580_raw_cache[str(symbol)] = values
        return values

    def get_minute_bars(
        self,
        symbol: str,
        *,
        limit: int = 120,
        completed_only: bool = True,
    ) -> list[Any]:
        raw_limit = min(900, max(36, int(limit) * BAR_MINUTES + BAR_MINUTES))
        raw = self.get_one_minute_bars(
            symbol, limit=raw_limit, completed_only=False
        )
        bars = aggregate_v7580_three_minute_bars(raw, limit=limit)
        if not bars:
            raise v753.ApiSchemaError(f"완성 3분봉 없음 symbol={symbol}")
        return bars


def _compatible_evidence(bars: Sequence[Any], decision: GemDecision) -> Any:
    flush = bars[decision.flush_index]
    dry = bars[decision.dry_index] if decision.dry_index >= 0 else bars[-1]
    current = bars[-1]
    pre = bars[max(0, decision.flush_index - 8) : decision.flush_index]
    pre_volumes = [max(0.0, float(item.volume)) for item in pre]
    median_volume = max(1.0, float(statistics.median(pre_volumes or [1.0])))
    p75 = max(1.0, float(v73._percentile75(pre_volumes or [1.0])))
    recent_max = max([1.0, *pre_volumes[-5:]])
    rise_low = min([float(item.low) for item in pre] or [float(flush.low)])
    rise_high = max([float(item.high) for item in pre] or [float(flush.high)])
    rise_span = max(1.0, rise_high - rise_low)
    pullback_low = min(float(item.low) for item in bars[decision.flush_index :])
    flush_volume = max(1.0, float(flush.volume))
    dry_range = max(1e-9, float(dry.high) - float(dry.low))
    flush_range = max(1e-9, float(flush.high) - float(flush.low))
    previous = bars[max(decision.flush_index, (decision.dry_index if decision.dry_index >= 0 else len(bars) - 1) - 1)]
    return v756.SupplyExhaustionEvidence(
        rise_low=rise_low,
        rise_high=rise_high,
        rise_pct=max(0.0, rise_high / max(0.01, rise_low) - 1.0),
        retrace=max(0.0, (rise_high - pullback_low) / rise_span),
        flush_time=v73.v754.normalize_kst(flush.timestamp),
        flush_low=float(flush.low),
        flush_volume=flush_volume,
        flush_volume_vs_median=flush_volume / median_volume,
        flush_volume_vs_p75=flush_volume / p75,
        flush_volume_vs_recent_max=flush_volume / recent_max,
        dry_volume_vs_flush=float(dry.volume) / flush_volume,
        dry_volume_vs_left_median=float(dry.volume) / median_volume,
        dry_volume_vs_previous=float(dry.volume) / max(1.0, float(previous.volume)),
        dry_range_vs_flush=dry_range / flush_range,
        current_close=float(current.close),
        structure_stop_pct=max(
            0.0,
            (float(current.close) - decision.support_low) / max(0.01, float(current.close)),
        ),
    )


def detect_v7580(strategy: Any, symbol: str, *, config: Any):
    bars = list(v73._same_session_bars(strategy, symbol))
    decision = evaluate_gem_three_minute(bars)
    quality_issue = _entry_data_quality_issue(bars, decision=decision)
    if quality_issue:
        decision_store = getattr(strategy, "_v7580_decisions", None)
        if isinstance(decision_store, dict):
            decision_store.pop(symbol, None)
        # Never fall back to an older, looser detector on incomplete recent
        # source data.  Exit/stop monitoring is unaffected by this entry gate.
        return None, f"보석3분_BLOCKED_{quality_issue}", None
    decision_store = getattr(strategy, "_v7580_decisions", None)
    if not isinstance(decision_store, dict):
        decision_store = {}
        setattr(strategy, "_v7580_decisions", decision_store)
    if decision is not None:
        decision_store[symbol] = decision
    if decision is None:
        return _ORIGINAL_ACTIVE_DETECT(strategy, symbol, config=config)
    if decision.stage in {FIRST_BUY, CONFIRM_BUY}:
        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            max(105.0, decision.score + 25.0),
            decision.reason + "|완성3분봉|체결즉시목표1.5%|고정손절5.0%",
            float(bars[-1].close),
            bars[-1].timestamp,
            "매물소진눌림1차",
            HARD_STOP_PCT,
            TAKE_PROFIT_PCT,
        )
        return signal, f"보석3분_{decision.pattern}", _compatible_evidence(bars, decision)
    # Once a reviewed wave is recognised, old loose detectors may not buy it early.
    return None, f"보석3분_{decision.stage}_{decision.pattern}", None


def assess_v7580(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
):
    bars = list(v73._same_session_bars(strategy, symbol))
    decision = evaluate_gem_three_minute(bars)
    quality_issue = _entry_data_quality_issue(bars, decision=decision)
    if quality_issue:
        item, state = _ORIGINAL_ACTIVE_ASSESS(
            strategy,
            symbol,
            loose=loose,
            strict=strict,
            now=now,
            name=name,
            source=source,
        )
        if item is not None:
            item = replace(
                item,
                stage="시장보류",
                strict_missing=(quality_issue,),
                source=f"{source}|{quality_issue}|매수판정만보류",
                updated_at=now,
            )
        return item, "3분원천결측매수보류"
    if decision is None:
        return _ORIGINAL_ACTIVE_ASSESS(
            strategy,
            symbol,
            loose=loose,
            strict=strict,
            now=now,
            name=name,
            source=source,
        )
    flush = bars[decision.flush_index]
    dry = bars[decision.dry_index] if decision.dry_index >= 0 else bars[-1]
    pre = bars[max(0, decision.flush_index - 8) : decision.flush_index]
    pre_volumes = [max(0.0, float(item.volume)) for item in pre]
    median_volume = max(1.0, float(statistics.median(pre_volumes or [1.0])))
    stage_map = {
        WATCH: ("관심종목", ("매물소진확인대기",), 72.0),
        READY: ("관찰후보", ("5봉선·저점확인대기",), 84.0),
        FIRST_BUY: ("즉시매수조건근접", (), 98.0),
        CONFIRM_BUY: ("즉시매수조건근접", (), 100.0),
        BLOCKED: ("시장보류", (decision.pattern,), 68.0),
    }
    stage, missing, display_score = stage_map[decision.stage]
    item = v7514.SupplyPotential(
        symbol=symbol,
        name=name or symbol,
        stage=stage,
        score=max(display_score, decision.score),
        detected_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=24),
        flush_time=v73.v754.normalize_kst(flush.timestamp),
        rise_pct=max(
            0.0,
            max([float(item.high) for item in pre] or [float(flush.high)])
            / max(0.01, min([float(item.low) for item in pre] or [float(flush.low)]))
            - 1.0,
        ),
        retrace=0.0,
        flush_vs_median=max(1.0, float(flush.volume)) / median_volume,
        flush_vs_p75=max(1.0, float(flush.volume)) / max(1.0, v73._percentile75(pre_volumes or [1.0])),
        flush_vs_recent_max=max(1.0, float(flush.volume)) / max([1.0, *pre_volumes[-5:]]),
        dry_vs_flush=float(dry.volume) / max(1.0, float(flush.volume)),
        dry_range_vs_flush=(float(dry.high) - float(dry.low))
        / max(1e-9, float(flush.high) - float(flush.low)),
        low_break_pct=0.0,
        strict_missing=tuple(missing),
        source=f"{source}|{decision.reason}",
    )
    return item, "보석3분통합단계"


class V7580GemEngine(BaseEngine):
    """Reviewed signal branches plus atomic PAPER target and -5% stop."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._gem_market_risk_factor = 1.0
        self._gem_market_risk_reason = "시장리듬정상"
        self._gem_last_decisions: dict[str, GemDecision] = {}
        self._gem_intrabar_hits: dict[tuple[str, datetime], tuple[str, int]] = {}
        self._gem_exit_inflight: set[str] = set()
        self._gem_order_announced: set[str] = set()
        self._gem_entry_factor: dict[str, float] = {}
        self._profit_contract: ProfitContract | None = None
        self._profit_snapshot: PerformanceSnapshot | None = None
        super().__init__(*args, **kwargs)
        self._refresh_profitability_telemetry()
        self._install_quantity_rhythm_wrapper()
        for symbol in list(self.broker.positions):
            self._enforce_gem_orders(symbol, announce=False)
        self.logger.warning(
            "[%s] V75.79 연동·대시보드 고정 | 3분봉+1분내부센서 | "
            "체결즉시 +1.5%% 목표 | 평균단가 -5.0%% 손절 | 손절 당일 재진입 금지",
            VERSION,
        )
        self.logger.warning(
            "[위험계약] PAPER 일일실현손실한도=%s(%.2f%%) | 동시보유상한=%d종목",
            "활성" if not bool(self.settings.paper_unlimited) else "비활성",
            float(self.settings.max_daily_loss_pct) * 100.0,
            MAX_CONCURRENT_POSITIONS,
        )

    def _refresh_profitability_telemetry(self) -> None:
        """Measure the fixed contract; never block or alter an order."""
        self._profit_contract = calculate_profit_contract(
            take_profit_pct=TAKE_PROFIT_PCT,
            hard_stop_pct=HARD_STOP_PCT,
            commission_pct=float(self.settings.commission_pct),
            sell_tax_pct=float(self.settings.sell_tax_pct),
            stop_slippage_pct=float(self.settings.slippage_pct),
        )
        self._profit_snapshot = analyze_performance(
            read_trade_rows(QUALITY_TRADES_CSV), self._profit_contract
        )
        for line in format_profitability_lines(
            self._profit_contract, self._profit_snapshot
        ):
            self.logger.warning("[수익계측] %s", line)

    def _compact_report(self) -> None:
        super()._compact_report()
        self._refresh_profitability_telemetry()

    def _install_quantity_rhythm_wrapper(self) -> None:
        original = self.risk.approve_entry

        def approve(
            signal: Any,
            quote: Any,
            equity: float,
            cash: float,
            positions: Any,
        ) -> Any:
            symbol = str(signal.symbol)
            current_symbols = set(positions or {})
            if (
                symbol not in current_symbols
                and len(current_symbols) >= MAX_CONCURRENT_POSITIONS
            ):
                return v753.RiskDecision(
                    False,
                    f"동시보유상한_{MAX_CONCURRENT_POSITIONS}종목_신규매수보류",
                    0,
                )
            decision = original(signal, quote, equity, cash, positions)
            if not bool(getattr(decision, "approved", False)):
                return decision
            pattern_decision = self._gem_last_decisions.get(str(signal.symbol))
            pattern_factor = (
                float(pattern_decision.risk_factor) if pattern_decision is not None else 1.0
            )
            factor = max(0.35, min(1.0, self._gem_market_risk_factor, pattern_factor))
            quantity = max(1, math.floor(int(decision.quantity) * factor))
            self._gem_entry_factor[str(signal.symbol)] = factor
            return v753.RiskDecision(
                True,
                f"{decision.reason}|시장·상단매물비중x{factor:.2f}|{self._gem_market_risk_reason}",
                quantity,
            )

        self.risk.approve_entry = approve  # type: ignore[method-assign]

    def _enforce_gem_orders(self, symbol: str, *, announce: bool = True) -> bool:
        position = self.broker.positions.get(symbol)
        if position is None:
            return False
        target = v753.align_stock_price(
            float(position.entry_price) * (1.0 + TAKE_PROFIT_PCT), upward=True
        )
        stop = v753.align_stock_price(
            float(position.entry_price) * (1.0 - HARD_STOP_PCT), upward=False
        )
        changed = (
            abs(float(position.target_price) - float(target)) > 1e-9
            or abs(float(position.stop_price) - float(stop)) > 1e-9
            or not bool(position.target_order_active)
        )
        position.target_price = target
        position.stop_price = stop
        position.target_order_active = True
        if changed:
            self.broker.save_state()
        if announce and symbol not in self._gem_order_announced:
            self._gem_order_announced.add(symbol)
            self.logger.warning(
                "PAPER 주문등록 %s | 실제보유=%s주 | 평단=%.0f | 목표=%.0f(+1.5%%) | 손절=%.0f(-5.0%%)",
                self._label(symbol), int(position.quantity), float(position.entry_price),
                float(position.target_price), float(position.stop_price),
            )
        return True

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        stored = getattr(self.strategy, "_v7580_decisions", {})
        if isinstance(stored, dict) and isinstance(stored.get(str(signal.symbol)), GemDecision):
            self._gem_last_decisions[str(signal.symbol)] = stored[str(signal.symbol)]
        entered = bool(super()._attempt_entry(signal, from_retry=from_retry))
        if entered:
            factor = self._gem_entry_factor.get(str(signal.symbol), 1.0)
            if factor < 0.99 and hasattr(self, "scale_blocked"):
                self.scale_blocked.add(str(signal.symbol))
            self._enforce_gem_orders(str(signal.symbol), announce=True)
        return entered

    def _attempt_scale_in(self, bar: Any) -> bool:
        """Re-register the contract immediately after any automatic scale fill."""
        scaled = bool(super()._attempt_scale_in(bar))
        if scaled:
            self._enforce_gem_orders(str(bar.symbol), announce=False)
        return scaled

    def _manual_buy(self, symbol: str, tranche: int) -> str:
        """Apply the same target/stop contract to intraday-command PAPER buys."""
        result = str(super()._manual_buy(symbol, tranche))
        self._enforce_gem_orders(str(symbol), announce=True)
        return result

    def _execute_gem_stop(
        self,
        symbol: str,
        quote: Any,
        *,
        detected_at: datetime,
        source: str,
        trigger_price: float,
        observed_lag_sec: float = 0.0,
    ) -> bool:
        if symbol in self._gem_exit_inflight:
            return False
        position = self.broker.positions.get(symbol)
        if position is None:
            return False
        self._gem_exit_inflight.add(symbol)
        was_active = bool(position.target_order_active)
        position.target_order_active = False  # cancel simulated target before market stop
        self.broker.save_state()
        try:
            enriched = self.client.get_orderbook(symbol, quote, side="sell")
            fill = self.broker.sell(
                symbol,
                enriched,
                "고정손절-5.0%",
                trigger_time=detected_at,
                trigger_price=float(trigger_price),
                detection_time=detected_at,
                observed_lag_sec=max(0.0, float(observed_lag_sec)),
                trigger_source=source,
            )
        except Exception as exc:
            position = self.broker.positions.get(symbol)
            if position is not None:
                position.target_order_active = was_active
                self.broker.save_state()
            self._show_throttled_error(
                f"stop:{symbol}", f"고정손절대기 {self._label(symbol)}: {v753.brief_error(exc)}", 10.0
            )
            return False
        finally:
            self._gem_exit_inflight.discard(symbol)

        tomorrow = (detected_at.astimezone(KST) + timedelta(days=1)).date()
        block_until = datetime.combine(tomorrow, datetime.min.time(), tzinfo=KST)
        self.broker.reentry_block_until[symbol] = block_until
        self.broker.save_state()
        self.strategy.mark_exit(symbol)
        self.risk.record_pnl(fill.pnl)
        self._print_sell(fill)
        return True

    def _check_tick_exit(
        self,
        quote: Any,
        *,
        detection_time: datetime | None = None,
        observed_lag_sec: float = 0.0,
    ) -> None:
        # Preserve V75.79's target path and entry-quality observation first.
        super()._check_tick_exit(
            quote,
            detection_time=detection_time,
            observed_lag_sec=observed_lag_sec,
        )
        position = self.broker.positions.get(quote.symbol)
        if position is None:
            return
        self._enforce_gem_orders(str(quote.symbol), announce=False)
        if float(quote.price) > float(position.stop_price):
            return
        detected_at = (
            detection_time.astimezone(KST)
            if detection_time is not None
            else datetime.now(KST)
        )
        self._execute_gem_stop(
            str(quote.symbol),
            quote,
            detected_at=detected_at,
            source="REST_TICK_FIXED_5PCT",
            trigger_price=float(quote.price),
            observed_lag_sec=observed_lag_sec,
        )

    def _on_bar(
        self,
        bar: Any,
        quote: Any,
        *,
        allow_entry: bool = True,
        bar_age_sec: float = 0.0,
    ) -> None:
        super()._on_bar(bar, quote, allow_entry=allow_entry, bar_age_sec=bar_age_sec)
        position = self.broker.positions.get(bar.symbol)
        if position is None:
            return
        self._enforce_gem_orders(str(bar.symbol), announce=False)
        # Do not apply a historical low from the candle that caused the entry.
        completed_at = v73.v754.normalize_kst(bar.timestamp) + timedelta(seconds=BAR_SECONDS)
        if completed_at <= position.entry_time.astimezone(KST):
            return
        if not _completed_bar_hard_stop_breached(position, bar):
            return
        self._execute_gem_stop(
            str(bar.symbol),
            quote,
            detected_at=datetime.now(KST),
            source="COMPLETED_3MIN_LOW_RECOVERY",
            trigger_price=float(bar.low),
        )

    def _gui_snapshot(self) -> dict[str, Any]:
        snapshot = super()._gui_snapshot()
        for row in snapshot.get("rows", []):
            if str(row.get("symbol", "")) in self.broker.positions:
                row["reason"] = str(row.get("reason", "")).replace(
                    "자동손절 없음", "고정손절 -5.0%"
                )
        return snapshot

    def _refresh_market_guard(self, *, force: bool = False) -> Any:
        """Use true raw one-minute index bars; V75.73 previously aggregated them."""
        now_mono = time.monotonic()
        if not force and now_mono < self._market_next_refresh:
            return self._market_snapshot
        self._market_next_refresh = now_mono + self.v757_config.market_refresh_sec

        daily: list[float] = []
        one: list[float] = []
        five: list[float] = []
        fifteen: list[float] = []
        failures: list[str] = []
        for code in self.v757_config.market_proxy_codes:
            try:
                quote = self.client.get_quote(code)
                bars = self.client.get_one_minute_bars(code, limit=20, completed_only=True)
                daily.append(float(quote.change_pct) / 100.0)
                one.append(market_module.v757._return_over_bars(bars, 1))
                five.append(market_module.v757._return_over_bars(bars, 5))
                fifteen.append(market_module.v757._return_over_bars(bars, 15))
            except v753.V73Error as exc:
                failures.append(f"{code}:{v753.brief_error(exc)}")
            except Exception as exc:
                failures.append(f"{code}:{exc}")

        now = datetime.now(KST)
        if failures or len(daily) != len(self.v757_config.market_proxy_codes):
            self._market_failures += 1
            if self._market_failures >= self.v757_config.market_failure_limit:
                self._market_snapshot = market_module.v757.MarketGuardSnapshot(
                    "DATA_HALT",
                    "시장지수조회실패_" + "/".join(failures),
                    tuple(daily), tuple(five), tuple(fifteen), now,
                )
                self._market_flow_reading = market_module.MarketFlowReading(
                    "DATA_HALT", self._market_snapshot.reason, True, False,
                    min(daily) if daily else 0.0,
                    min(one) if one else 0.0,
                    min(five) if five else 0.0,
                    min(fifteen) if fifteen else 0.0,
                )
            return self._market_snapshot

        self._market_failures = 0
        reading = market_module.classify_market_flow(
            daily, one, five, fifteen, self.v758_config
        )
        self._market_flow_reading = reading
        self._market_one_min_changes = tuple(one)
        previous_state = self._market_snapshot.state

        if reading.active_decline:
            self._market_decline_seen = True
            self._market_stable_since_mono = 0.0
            self._market_recovery_ready_until = 0.0
            state, reason = reading.state, reading.reason
        elif self._market_decline_seen:
            if reading.stable_now:
                if self._market_stable_since_mono <= 0.0:
                    self._market_stable_since_mono = now_mono
                stable_for = now_mono - self._market_stable_since_mono
                if stable_for >= self.v758_config.recovery_confirm_sec:
                    self._market_recovery_ready_until = max(
                        self._market_recovery_ready_until,
                        now_mono + self.v758_config.recovery_ready_window_sec,
                    )
                    self._market_decline_seen = False
                    state = "RECOVERY_READY"
                    reason = f"지수하락멈춤확인_{stable_for:.0f}초 | {reading.reason}"
                else:
                    state = "RECOVERY_WAIT"
                    reason = (
                        f"지수하락멈춤확인중_{stable_for:.0f}/"
                        f"{self.v758_config.recovery_confirm_sec:.0f}초 | {reading.reason}"
                    )
            else:
                self._market_stable_since_mono = 0.0
                state = "RECOVERY_WAIT"
                reason = "지수반등불안정_재하락가능 | " + reading.reason
        elif now_mono < self._market_recovery_ready_until:
            state, reason = "RECOVERY_READY", "지수하락멈춤확인완료 | " + reading.reason
        else:
            state, reason = "NORMAL", reading.reason

        broad_five = min(five) if five else 0.0
        broad_fifteen = min(fifteen) if fifteen else 0.0
        if state == "NORMAL" and broad_fifteen >= 0.012 and broad_five >= 0.004:
            self._gem_market_risk_factor = 0.60
            self._gem_market_risk_reason = "지수단기과열_신규비중축소"
            reason += " | 지수단기과열 신규비중60%"
        elif state == "NORMAL" and broad_fifteen >= 0.007:
            self._gem_market_risk_factor = 0.78
            self._gem_market_risk_reason = "지수상승말기주의_비중축소"
            reason += " | 지수상승말기 신규비중78%"
        else:
            self._gem_market_risk_factor = 1.0
            self._gem_market_risk_reason = "시장리듬정상"

        self._market_snapshot = market_module.v757.MarketGuardSnapshot(
            state, reason, tuple(daily), tuple(five), tuple(fifteen), now
        )
        if state in {"SHOCK", "CAUTION", "RECOVERY_WAIT", "DATA_HALT"}:
            if hasattr(self, "scale_blocked"):
                self.scale_blocked.update(self.broker.positions)
        if state != previous_state or state not in {"NORMAL", "RECOVERY_READY"}:
            self.logger.warning(
                "시장진입가드=%s | %s | 보류관심=%d종목",
                state, reason, len(self._deferred_market_signals),
            )
        return self._market_snapshot

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        super()._refresh_actual_bars(symbol, quote)
        if symbol in self.broker.positions or symbol in self.pending_entries:
            return
        raw = list(getattr(self.client, "v7580_raw_cache", {}).get(symbol, []))
        if not raw:
            return
        now = datetime.now(KST)
        bucket = v73._bucket_start(now)
        # One REST response may repeat a minute at a paging boundary.  Count
        # distinct exchange minutes and never double its volume.
        minute_map = {
            _minute_stamp(bar.timestamp): bar
            for bar in raw
            if v73._bucket_start(bar.timestamp) == bucket
        }
        internals = [minute_map[key] for key in sorted(minute_map)]
        if len(internals) < 2:
            return
        partial = v753.Bar(
            symbol=symbol,
            timestamp=bucket,
            open=float(internals[0].open),
            high=max([float(item.high) for item in internals] + [float(quote.price)]),
            low=min([float(item.low) for item in internals] + [float(quote.price)]),
            close=float(quote.price),
            volume=sum(max(0, int(item.volume)) for item in internals),
        )
        completed = list(v73._same_session_bars(self.strategy, symbol))
        projection = evaluate_intrabar_projection(
            completed,
            partial,
            internals,
            elapsed_seconds=(now - bucket).total_seconds(),
        )
        if projection.decision is not None:
            self._gem_last_decisions[symbol] = projection.decision
        signature = (
            f"{projection.stage}:{projection.decision.flush_index}:"
            f"{projection.decision.dry_index}"
            if projection.decision is not None else projection.stage
        )
        key = (symbol, bucket)
        previous_signature, hits = self._gem_intrabar_hits.get(key, ("", 0))
        hits = hits + 1 if signature == previous_signature and projection.eligible else 1 if projection.eligible else 0
        self._gem_intrabar_hits[key] = (signature, hits)
        if not projection.eligible or hits < 2:
            return
        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            max(105.0, projection.score + 25.0),
            projection.reason + "|형성중3분봉2회지속|체결즉시목표1.5%|고정손절5.0%",
            float(quote.price),
            bucket,
            "매물소진눌림1차",
            HARD_STOP_PCT,
            TAKE_PROFIT_PCT,
        )
        self.latest_signals[symbol] = signal
        self._attempt_entry(signal)


_PARENT_BASELINE_CHECKS = tuple(parent.self_check())


def _install_runtime() -> None:
    v753.KiwoomRestClient = GemThreeMinuteKiwoomClient
    v7525.V7525Engine = V7580GemEngine
    for module, name in (
        (v7525, "detect_v7525"),
        (v756, "detect_supply_exhaustion_pullback"),
        (v7515, "detect_precision_supply"),
        (v7530, "strict_detect"),
        (v7540, "detect_v7540"),
        (v7539, "detect_v7539"),
    ):
        setattr(module, name, detect_v7580)
    v7514.assess_loose_supply_potential = assess_v7580
    v7514.SUPPLY_PATTERNS.add("매물소진눌림1차")
    # Exact user-confirmed link object; do not wrap, replace, or subclass it here.
    v57.RestoredHeroLink = parent.SmoothContinuousHeroLink
    v57.ReliableHeroLink = parent.SmoothContinuousHeroLink
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def _configured_profit_contract(settings: Any | None = None) -> ProfitContract:
    active = settings if settings is not None else v753.Settings.load()
    return calculate_profit_contract(
        take_profit_pct=TAKE_PROFIT_PCT,
        hard_stop_pct=HARD_STOP_PCT,
        commission_pct=float(active.commission_pct),
        sell_tax_pct=float(active.sell_tax_pct),
        stop_slippage_pct=float(active.slippage_pct),
    )


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    settings = v753.Settings.load()
    contract = _configured_profit_contract(settings)
    snapshot = analyze_performance(read_trade_rows(QUALITY_TRADES_CSV), contract)
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "bar_minutes": BAR_MINUTES,
        "completed_only": True,
        "entry_source_quality": "3 distinct 1-minute records required across flush-8 through decision bar",
        "intrabar_sensor": "raw 1-minute internals -> temporary 3-minute projection; two-hit persistence",
        "client_class": f"{v753.KiwoomRestClient.__module__}.{v753.KiwoomRestClient.__qualname__}",
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "link_class": f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
        "link_exact_parent": v57.RestoredHeroLink is parent.SmoothContinuousHeroLink,
        "dashboard_sha256": sha256_file(BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py"),
        "take_profit_pct": TAKE_PROFIT_PCT,
        "hard_stop_pct": HARD_STOP_PCT,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "paper_daily_loss_limit_active": not bool(settings.paper_unlimited),
        "max_daily_loss_pct": float(settings.max_daily_loss_pct),
        "profit_required_win_rate_pct": contract.required_win_rate_pct,
        "profit_observed_trades": snapshot.total,
        "profit_gate": "measurement only until >=100 trades, >=20 days, >=10 symbols",
        "target_registration": "immediate after PAPER fill; actual held quantity; reset after scale-in",
        "stop_reentry": "blocked until next KST day",
        "market_bars": "true raw 1-minute returns for 1m/5m/15m guard",
        "learning_cases": 16,
        "paper_only": True,
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    # Parent checks are captured before V75.81 intentionally replaces only the
    # client and engine.  This distinguishes regression from expected override.
    rows = list(_PARENT_BASELINE_CHECKS)
    identity = runtime_identity()
    settings = v753.Settings.load()
    rows.extend([
        ("V75.79기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("승인대시보드바이트고정", identity["dashboard_sha256"] == APPROVED_GUI_SHA256, APPROVED_GUI_SHA256[:16]),
        ("정상연동객체그대로", v57.RestoredHeroLink is parent.SmoothContinuousHeroLink, identity["link_class"]),
        ("완성3분봉클라이언트", v753.KiwoomRestClient is GemThreeMinuteKiwoomClient, "1분 원본→완성3분"),
        ("3분원천결측매수차단", callable(_entry_data_quality_issue), "투매-8봉부터 판정봉까지 각 3/3"),
        ("1분내부센서", callable(evaluate_intrabar_projection), "2회지속+막판붕괴차단"),
        ("통합보석판정", callable(evaluate_gem_three_minute), "성공·오진 16사례"),
        ("PAPER목표", TAKE_PROFIT_PCT == 0.015, "+1.5%"),
        ("PAPER손절", HARD_STOP_PCT == 0.05, "평균단가 -5.0%"),
        ("PAPER일손실한도활성", not settings.paper_unlimited and abs(float(settings.max_daily_loss_pct) - 0.01) < 1e-12, f"paper_unlimited={settings.paper_unlimited}, limit={settings.max_daily_loss_pct:.2%}"),
        ("동시보유상한", MAX_CONCURRENT_POSITIONS == 20, f"{MAX_CONCURRENT_POSITIONS}종목"),
        ("손익분기계측", 79.0 < float(identity["profit_required_win_rate_pct"]) < 83.0, f"필요승률={identity['profit_required_win_rate_pct']:.2f}%"),
        ("실행엔진교체", v7525.V7525Engine is V7580GemEngine, identity["engine_class"]),
        ("PAPER전용", VERSION.endswith("PAPER") and identity["paper_only"] is True, VERSION),
    ])
    return rows


def main() -> int:
    diag("V7581_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    return int(parent.main())


if __name__ == "__main__":
    if "--runtime-info" in sys.argv or "--identity" in sys.argv:
        print(json.dumps(runtime_identity(), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)
    if "--self-check" in sys.argv:
        checks = self_check()
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in checks) else 1)
    raise SystemExit(main())
