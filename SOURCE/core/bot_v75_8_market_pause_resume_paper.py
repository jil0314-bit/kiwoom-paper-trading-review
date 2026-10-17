#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.8 MARKET-PAUSE + HIGH-ATTENTION RESUME PAPER
=================================================

V75.3~V75.7 원본을 수정하지 않고 V75.7 위에 시장 급락 보류·재개 계층만 추가한다.

핵심 원칙
1) 매물소진 조건이 맞아도 KODEX200·KODEX코스닥150이 단기간 급하게 흘러내리면
   즉시 매수하지 않고 "시장하락 보류후보"로 보존한다.
2) 후보를 완전히 버리지 않는다. HOT 최우선 감시목록에 유지한다.
3) 지수의 1분·5분·15분 하락이 멈춘 뒤 안정 상태가 일정 시간 유지될 때만 재검증한다.
4) 재검증 시 종목의 대량음봉 저점, 거래절벽, 소형봉, 비추격 조건이 살아 있어야 한다.
5) 재차 지수 하락, 종목 저점 이탈, 재대량매도, 과도한 상승 추격이면 후보를 취소한다.
6) 시장·계측 실패는 신규진입만 막고 보유종목 목표·손절 감시는 계속한다.
7) 실제 주문 기능은 없으며 PAPER 체결만 사용한다.

주의
- 대량 음봉 뒤 거래량 감소는 확률 조건이지 수익 보장이 아니다.
- 시장이 멈춘 것처럼 보였다가 다시 하락할 수 있으므로 90초 안정확인을 기본 적용한다.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import logging
import math
import os
import statistics
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

VERSION = "V75.8-MARKET-PAUSE-HIGH-ATTENTION-RESUME-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V757_FILENAME = "bot_v75_7_early_dryup_volume_filter_paper.py"
V757_SHA256 = "e54ad808dd999a8ecdc5e5f42df641d866969b7df72c283612214cabcd7aa305"

RUNTIME_DIR = BASE_DIR / "v75_8_runtime"
DEFERRED_FILE = RUNTIME_DIR / "v75_8_market_deferred.json"

FORBIDDEN_ORDER_NAMES = {
    "send_order",
    "create_order",
    "place_order",
    "order_stock",
    "submit_order",
}


def sha256_file(path: Path) -> str:
    """파일 SHA256을 계산한다."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_v757() -> ModuleType:
    """검증된 V75.7을 SHA256 확인 후 읽기 로드한다."""
    path = BASE_DIR / V757_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V757_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V757_SHA256:
        raise RuntimeError(
            "V75.7 SHA256 불일치 — 검증된 기준본이 아니므로 중단합니다.\n"
            f"기대: {V757_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v757_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.7 모듈 로더를 만들 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v757 = load_v757()
v756 = v757.v756
v755 = v757.v755
v754 = v757.v754
v753 = v757.v753
KST = v757.KST


def env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    return v756.env_float(name, default, minimum=minimum, maximum=maximum)


def env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return v756.env_int(name, default, minimum=minimum, maximum=maximum)


@dataclass(frozen=True, slots=True)
class MarketPauseConfig:
    """지수 하락 보류와 재개 판정 설정."""

    deferred_max_minutes: int = 60
    recovery_confirm_sec: float = 90.0
    recovery_ready_window_sec: float = 300.0
    deferred_service_sec: float = 3.0

    one_min_caution_pct: float = -0.0015
    one_min_shock_pct: float = -0.0030
    five_min_caution_pct: float = -0.0040
    five_min_shock_pct: float = -0.0080
    fifteen_min_caution_pct: float = -0.0070
    fifteen_min_shock_pct: float = -0.0120
    hard_daily_block_pct: float = -0.0300

    recovery_one_min_min_pct: float = -0.0005
    recovery_five_min_min_pct: float = -0.0015
    recovery_fifteen_min_min_pct: float = -0.0150

    max_resume_chase_pct: float = 0.012
    max_resume_adverse_pct: float = 0.012
    resume_max_volume_vs_flush: float = 0.55
    resume_max_volume_vs_left_median: float = 0.85
    resume_max_bear_body_ratio: float = 0.40
    resume_min_close_location: float = 0.25
    resume_max_range_vs_flush: float = 0.75

    @classmethod
    def load(cls) -> "MarketPauseConfig":
        return cls(
            deferred_max_minutes=env_int(
                "V758_DEFERRED_MAX_MINUTES", 60, minimum=3, maximum=180
            ),
            recovery_confirm_sec=env_float(
                "V758_RECOVERY_CONFIRM_SEC", 90.0, minimum=30.0, maximum=600.0
            ),
            recovery_ready_window_sec=env_float(
                "V758_RECOVERY_READY_WINDOW_SEC", 300.0, minimum=60.0, maximum=1800.0
            ),
            deferred_service_sec=env_float(
                "V758_DEFERRED_SERVICE_SEC", 3.0, minimum=1.0, maximum=30.0
            ),
            one_min_caution_pct=env_float(
                "V758_MARKET_1M_CAUTION", -0.0015, minimum=-0.03, maximum=0.0
            ),
            one_min_shock_pct=env_float(
                "V758_MARKET_1M_SHOCK", -0.0030, minimum=-0.05, maximum=-0.0001
            ),
            five_min_caution_pct=env_float(
                "V758_MARKET_5M_CAUTION", -0.0040, minimum=-0.08, maximum=0.0
            ),
            five_min_shock_pct=env_float(
                "V758_MARKET_5M_SHOCK", -0.0080, minimum=-0.12, maximum=-0.0001
            ),
            fifteen_min_caution_pct=env_float(
                "V758_MARKET_15M_CAUTION", -0.0070, minimum=-0.12, maximum=0.0
            ),
            fifteen_min_shock_pct=env_float(
                "V758_MARKET_15M_SHOCK", -0.0120, minimum=-0.20, maximum=-0.0001
            ),
            hard_daily_block_pct=env_float(
                "V758_HARD_DAILY_BLOCK", -0.0300, minimum=-0.15, maximum=-0.005
            ),
            recovery_one_min_min_pct=env_float(
                "V758_RECOVERY_1M_MIN", -0.0005, minimum=-0.02, maximum=0.01
            ),
            recovery_five_min_min_pct=env_float(
                "V758_RECOVERY_5M_MIN", -0.0015, minimum=-0.04, maximum=0.02
            ),
            recovery_fifteen_min_min_pct=env_float(
                "V758_RECOVERY_15M_MIN", -0.0150, minimum=-0.08, maximum=0.03
            ),
            max_resume_chase_pct=env_float(
                "V758_MAX_RESUME_CHASE", 0.012, minimum=0.002, maximum=0.05
            ),
            max_resume_adverse_pct=env_float(
                "V758_MAX_RESUME_ADVERSE", 0.012, minimum=0.002, maximum=0.05
            ),
            resume_max_volume_vs_flush=env_float(
                "V758_RESUME_MAX_VOL_VS_FLUSH", 0.55, minimum=0.10, maximum=0.90
            ),
            resume_max_volume_vs_left_median=env_float(
                "V758_RESUME_MAX_VOL_VS_LEFT_MEDIAN", 0.85, minimum=0.20, maximum=1.50
            ),
            resume_max_bear_body_ratio=env_float(
                "V758_RESUME_MAX_BEAR_BODY", 0.40, minimum=0.10, maximum=0.80
            ),
            resume_min_close_location=env_float(
                "V758_RESUME_MIN_CLOSE_LOCATION", 0.25, minimum=0.0, maximum=0.80
            ),
            resume_max_range_vs_flush=env_float(
                "V758_RESUME_MAX_RANGE_VS_FLUSH", 0.75, minimum=0.20, maximum=1.50
            ),
        )


@dataclass(frozen=True, slots=True)
class MarketFlowReading:
    """두 지수의 단기 흐름 판정 결과."""

    state: str
    reason: str
    active_decline: bool
    stable_now: bool
    worst_daily: float
    worst_one: float
    worst_five: float
    worst_fifteen: float


@dataclass(slots=True)
class DeferredMarketSignal:
    """시장 하락 때문에 실행하지 않고 최우선 감시하는 매물소진 신호."""

    signal: Any
    evidence: Any
    name: str
    detected_at: datetime
    expires_at: datetime
    market_reason: str
    last_check_mono: float = 0.0
    check_count: int = 0


def classify_market_flow(
    daily: Sequence[float],
    one: Sequence[float],
    five: Sequence[float],
    fifteen: Sequence[float],
    config: MarketPauseConfig,
) -> MarketFlowReading:
    """시장 수준보다 '지금 흘러내리는 중인지'를 우선 판정한다."""
    if not daily or not one or not five or not fifteen:
        return MarketFlowReading(
            "DATA_HALT",
            "시장지수데이터부족",
            True,
            False,
            min(daily) if daily else 0.0,
            min(one) if one else 0.0,
            min(five) if five else 0.0,
            min(fifteen) if fifteen else 0.0,
        )

    worst_daily = min(daily)
    worst_one = min(one)
    worst_five = min(five)
    worst_fifteen = min(fifteen)

    # 15분 하락률은 이미 지나간 급락을 오래 포함할 수 있다.
    # 따라서 15분만 나쁘고 1·5분이 안정된 경우에는 "현재도 하락 중"으로 보지 않는다.
    shock = (
        worst_daily <= config.hard_daily_block_pct
        or worst_one <= config.one_min_shock_pct
        or worst_five <= config.five_min_shock_pct
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
            and (
                worst_one < config.recovery_one_min_min_pct
                or worst_five < config.recovery_five_min_min_pct
            )
        )
    )
    stable = (
        worst_daily > config.hard_daily_block_pct
        and worst_one >= config.recovery_one_min_min_pct
        and worst_five >= config.recovery_five_min_min_pct
        and worst_fifteen >= config.recovery_fifteen_min_min_pct
    )

    metrics = (
        f"일={worst_daily:+.2%} 1분={worst_one:+.2%} "
        f"5분={worst_five:+.2%} 15분={worst_fifteen:+.2%}"
    )
    if shock:
        return MarketFlowReading("SHOCK", f"지수급락진행 {metrics}", True, False,
                                 worst_daily, worst_one, worst_five, worst_fifteen)
    if caution:
        return MarketFlowReading("CAUTION", f"지수하락진행 {metrics}", True, False,
                                 worst_daily, worst_one, worst_five, worst_fifteen)
    return MarketFlowReading("FLOW_STABLE", f"지수단기하락멈춤 {metrics}", False, stable,
                             worst_daily, worst_one, worst_five, worst_fifteen)


def _evidence_to_payload(evidence: Any) -> dict[str, Any]:
    payload = asdict(evidence)
    payload["flush_time"] = v754.normalize_kst(evidence.flush_time).isoformat()
    return payload


def _evidence_from_payload(payload: Mapping[str, Any]) -> Any:
    values = dict(payload)
    values["flush_time"] = datetime.fromisoformat(str(values["flush_time"]))
    return v756.SupplyExhaustionEvidence(**values)


def _signal_to_payload(signal: Any) -> dict[str, Any]:
    return {
        "symbol": signal.symbol,
        "score": float(signal.score),
        "reason": str(signal.reason),
        "reference_price": float(signal.reference_price),
        "timestamp": v754.normalize_kst(signal.timestamp).isoformat(),
        "pattern": str(signal.pattern),
        "stop_loss_pct": float(signal.stop_loss_pct),
        "take_profit_pct": float(signal.take_profit_pct),
    }


def _signal_from_payload(payload: Mapping[str, Any]) -> Any:
    return v753.Signal(
        str(payload["symbol"]),
        v753.SignalAction.BUY,
        float(payload["score"]),
        str(payload["reason"]),
        float(payload["reference_price"]),
        datetime.fromisoformat(str(payload["timestamp"])),
        str(payload["pattern"]),
        float(payload["stop_loss_pct"]),
        float(payload["take_profit_pct"]),
    )


class MarketPauseResumeEngine(v757.EarlyDryupVolumeEngine):
    """V75.7에 시장 급락 보류후보와 지수 안정 후 재진입을 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v758_config = MarketPauseConfig.load()
        self._deferred_market_signals: dict[str, DeferredMarketSignal] = {}
        self._deferred_cursor = 0
        self._next_deferred_service = 0.0
        self._market_stable_since_mono = 0.0
        self._market_recovery_ready_until = 0.0
        self._market_decline_seen = False
        self._market_one_min_changes: tuple[float, ...] = ()
        self._market_flow_reading = MarketFlowReading(
            "DATA_HALT", "시장지수초기확인전", True, False, 0.0, 0.0, 0.0, 0.0
        )
        self._resuming_symbols: set[str] = set()
        self._load_deferred_state()
        if self._deferred_market_signals:
            # 재시작 직후에도 즉시 매수하지 않고 지수 안정확인을 다시 거친다.
            self._market_decline_seen = True
        cfg = self.v758_config
        self.logger.warning(
            "[%s] 활성 | 지수하락 중 조건충족 종목은 폐기하지 않고 보류 | "
            "하락멈춤 %.0f초 확인 후 재검증 | 보류최대 %d분",
            VERSION,
            cfg.recovery_confirm_sec,
            cfg.deferred_max_minutes,
        )

    def _save_deferred_state(self) -> None:
        """보류후보를 원자적으로 저장한다. 실패는 전략을 막지 않는다."""
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": VERSION,
                "saved_at": datetime.now(KST).isoformat(),
                "candidates": [
                    {
                        "signal": _signal_to_payload(item.signal),
                        "evidence": _evidence_to_payload(item.evidence),
                        "name": item.name,
                        "detected_at": item.detected_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                        "market_reason": item.market_reason,
                        "check_count": item.check_count,
                    }
                    for item in self._deferred_market_signals.values()
                ],
            }
            temp = DEFERRED_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, DEFERRED_FILE)
        except Exception as exc:
            self.logger.warning("시장보류후보 저장 실패(매매는 계속): %s", exc)

    def _load_deferred_state(self) -> None:
        """같은 거래일의 유효한 보류후보만 복원한다."""
        if not DEFERRED_FILE.exists():
            return
        try:
            payload = json.loads(DEFERRED_FILE.read_text(encoding="utf-8"))
            now = datetime.now(KST)
            for raw in payload.get("candidates", []):
                detected = datetime.fromisoformat(str(raw["detected_at"])).astimezone(KST)
                expires = datetime.fromisoformat(str(raw["expires_at"])).astimezone(KST)
                if detected.date() != now.date() or expires <= now:
                    continue
                signal = _signal_from_payload(raw["signal"])
                evidence = _evidence_from_payload(raw["evidence"])
                self._deferred_market_signals[signal.symbol] = DeferredMarketSignal(
                    signal=signal,
                    evidence=evidence,
                    name=str(raw.get("name") or signal.symbol),
                    detected_at=detected,
                    expires_at=expires,
                    market_reason=str(raw.get("market_reason", "시장하락")),
                    check_count=int(raw.get("check_count", 0)),
                )
            if self._deferred_market_signals:
                self.logger.warning(
                    "시장보류후보 %d종목 복원—지수 안정 후 우선 재검증",
                    len(self._deferred_market_signals),
                )
        except Exception as exc:
            self.logger.warning("시장보류후보 복원 실패(빈 목록으로 계속): %s", exc)
            self._deferred_market_signals.clear()

    def _refresh_market_guard(self, *, force: bool = False) -> Any:
        """1·5·15분 지수 흐름을 측정하고 하락→안정 상태를 관리한다."""
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
                bars = self.client.get_minute_bars(code, limit=20, completed_only=True)
                daily.append(float(quote.change_pct) / 100.0)
                one.append(v757._return_over_bars(bars, 1))
                five.append(v757._return_over_bars(bars, 5))
                fifteen.append(v757._return_over_bars(bars, 15))
            except v753.V73Error as exc:
                failures.append(f"{code}:{v753.brief_error(exc)}")
            except Exception as exc:
                failures.append(f"{code}:{exc}")

        now = datetime.now(KST)
        if failures or len(daily) != len(self.v757_config.market_proxy_codes):
            self._market_failures += 1
            if self._market_failures >= self.v757_config.market_failure_limit:
                self._market_snapshot = v757.MarketGuardSnapshot(
                    "DATA_HALT",
                    "시장지수조회실패_" + "/".join(failures),
                    tuple(daily),
                    tuple(five),
                    tuple(fifteen),
                    now,
                )
                self._market_flow_reading = MarketFlowReading(
                    "DATA_HALT", self._market_snapshot.reason, True, False,
                    min(daily) if daily else 0.0,
                    min(one) if one else 0.0,
                    min(five) if five else 0.0,
                    min(fifteen) if fifteen else 0.0,
                )
            return self._market_snapshot

        self._market_failures = 0
        reading = classify_market_flow(daily, one, five, fifteen, self.v758_config)
        self._market_flow_reading = reading
        self._market_one_min_changes = tuple(one)
        previous_state = self._market_snapshot.state

        if reading.active_decline:
            self._market_decline_seen = True
            self._market_stable_since_mono = 0.0
            self._market_recovery_ready_until = 0.0
            state = reading.state
            reason = reading.reason
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
            state = "RECOVERY_READY"
            reason = "지수하락멈춤확인완료 | " + reading.reason
        else:
            state = "NORMAL"
            reason = reading.reason

        self._market_snapshot = v757.MarketGuardSnapshot(
            state, reason, tuple(daily), tuple(five), tuple(fifteen), now
        )
        if state != previous_state or state not in {"NORMAL", "RECOVERY_READY"}:
            self.logger.warning(
                "시장진입가드=%s | %s | 보류관심=%d종목",
                state,
                reason,
                len(self._deferred_market_signals),
            )
        return self._market_snapshot

    def _market_entry_allowed(self) -> tuple[bool, str]:
        snapshot = self._refresh_market_guard()
        if snapshot.state in {"NORMAL", "RECOVERY_READY"}:
            return True, snapshot.reason
        return False, f"시장신규매수보류_{snapshot.state}_{snapshot.reason}"

    def _find_supply_evidence(self, signal: Any) -> Any | None:
        snapshot = self._intrabar_snapshots.get(signal.symbol)
        if snapshot is not None:
            return snapshot.supply
        return self._supply_evidence.get(signal.symbol)

    def _can_defer_signal(self, signal: Any) -> bool:
        return (
            signal.action == v753.SignalAction.BUY
            and signal.pattern in {"매물소진50초선진입", "매물소진눌림1차"}
            and self._find_supply_evidence(signal) is not None
        )

    def _defer_market_signal(self, signal: Any, market_reason: str) -> bool:
        """조건충족 신호를 폐기하지 않고 시장안정 대기목록에 넣는다."""
        evidence = self._find_supply_evidence(signal)
        if evidence is None:
            return False
        now = datetime.now(KST)
        expires = now + timedelta(minutes=self.v758_config.deferred_max_minutes)
        old = self._deferred_market_signals.get(signal.symbol)
        if old is not None and old.detected_at > v754.normalize_kst(signal.timestamp):
            return True
        self._deferred_market_signals[signal.symbol] = DeferredMarketSignal(
            signal=signal,
            evidence=evidence,
            name=self.names.get(signal.symbol, signal.symbol),
            detected_at=now,
            expires_at=expires,
            market_reason=market_reason,
        )
        self.pending_entries.pop(signal.symbol, None)
        self._save_deferred_state()
        self.logger.warning(
            "시장하락보류 %s | 조건은 유효하나 지수 하락 중—폐기하지 않고 %s까지 최우선 감시",
            self._label(signal.symbol),
            expires.strftime("%H:%M:%S"),
        )
        return True

    def _drop_deferred(self, symbol: str, reason: str) -> None:
        if self._deferred_market_signals.pop(symbol, None) is not None:
            self._save_deferred_state()
            self.logger.warning("시장보류후보취소 %s %s", self._label(symbol), reason)

    def _update_hot_symbols(self) -> None:
        super()._update_hot_symbols()
        deferred = [
            symbol for symbol in self._deferred_market_signals
            if symbol not in self.broker.positions
        ]
        self.hot_symbols = list(dict.fromkeys(deferred + self.hot_symbols))

    def _try_intrabar_entry(self, symbol: str) -> None:
        """신호를 먼저 확인하고, 시장 하락이면 폐기 대신 보류한다."""
        now = datetime.now(KST)
        stage = v757.intrabar_probe_stage(now, self.v757_config)
        if stage == 0 or symbol in self.broker.positions or symbol in self.pending_entries:
            return
        if symbol not in self._armed_symbols:
            return
        minute = now.replace(second=0, microsecond=0)
        if self._probe_stage_done(symbol, minute, stage):
            return

        try:
            bars = [
                bar
                for bar in self.client.get_minute_bars(symbol, limit=40, completed_only=False)
                if v753.regular_session_bar(bar.timestamp)
            ]
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"v758-intrabar:{symbol}",
                f"50초확인 실패 {self._label(symbol)}: {v753.brief_error(exc)}",
                60.0,
            )
            return
        selected = v757._same_session_bars(bars)
        if not selected:
            return
        current = selected[-1]
        current_minute = current.timestamp.astimezone(KST).replace(second=0, microsecond=0)
        if current_minute != minute:
            return
        recent = v757.recent_volume_sum(selected, self.v757_config.recent_volume_bars)
        self._recent_volume_cache[symbol] = recent
        if recent < self.v757_config.min_recent_volume:
            meta = self.candidate_meta.get(symbol)
            cumulative = int(getattr(meta, "volume", 0) or 0)
            self._exclude_low_volume(symbol, recent, cumulative)
            return

        elapsed = (now - minute).total_seconds()
        signal, snapshot, _ = self._build_intrabar_signal(symbol, current, selected, elapsed)
        if signal is None or snapshot is None:
            return
        self._intrabar_snapshots[symbol] = snapshot
        self._supply_evidence[symbol] = snapshot.supply
        self.latest_signals[symbol] = signal

        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            self._defer_market_signal(signal, market_reason)
            return

        self.logger.warning(
            "50초매물소진확인 %s 경과=%.1f초 거래=%s(환산%s) 최근%d봉=%s — 즉시 PAPER 진입검증",
            self._label(symbol), elapsed, f"{snapshot.raw_volume:,}",
            f"{snapshot.projected_volume:,}", self.v757_config.recent_volume_bars,
            f"{snapshot.recent_volume:,}",
        )
        self._attempt_entry(signal)

    def _validate_deferred_setup(
        self,
        item: DeferredMarketSignal,
        selected: Sequence[Any],
        projected_bar: Any,
        current_price: float,
    ) -> tuple[str, str]:
        """보류후보가 여전히 매물소진 구조인지 판정한다.

        반환 상태: READY / WAIT / CANCEL
        """
        cfg = self.v758_config
        evidence = item.evidence
        guard_low = evidence.flush_low * (
            1.0 - self.supply_exhaustion_config.max_flush_low_break_pct
        )
        since = [
            bar for bar in selected
            if v754.normalize_kst(bar.timestamp) >= item.detected_at.replace(second=0, microsecond=0)
        ]
        if since and min(float(bar.low) for bar in since) < guard_low:
            return "CANCEL", f"보류중매물소진저점이탈_{guard_low:.0f}"
        if current_price < guard_low:
            return "CANCEL", f"현재가저점이탈_{current_price:.0f}<{guard_low:.0f}"

        drift = current_price / max(0.01, float(item.signal.reference_price)) - 1.0
        if drift > cfg.max_resume_chase_pct:
            return "CANCEL", f"지수안정전이미상승_{drift:+.2%}>추격한도"
        if drift < -cfg.max_resume_adverse_pct:
            return "CANCEL", f"보류중종목약화_{drift:+.2%}"

        for bar in since:
            _, signed_body, _, _ = v756._candle_metrics(bar)
            if (
                signed_body <= -self.supply_exhaustion_config.flush_min_bear_body_ratio
                and float(bar.volume)
                >= evidence.flush_volume
                * self.supply_exhaustion_config.dry_max_intermediate_volume_vs_flush
            ):
                return "CANCEL", "보류중재차대량음봉"

        flush_bar = next(
            (
                bar for bar in selected
                if v754.normalize_kst(bar.timestamp).replace(second=0, microsecond=0)
                == v754.normalize_kst(evidence.flush_time).replace(second=0, microsecond=0)
            ),
            None,
        )
        if flush_bar is None:
            return "WAIT", "대량음봉원본재확인중"
        flush_range = max(1e-9, float(flush_bar.high) - float(flush_bar.low))
        current_range, signed_body, _, close_location = v756._candle_metrics(projected_bar)
        current_volume = max(0.0, float(projected_bar.volume))
        left_median = evidence.flush_volume / max(0.01, evidence.flush_volume_vs_median)

        if (
            signed_body <= -self.supply_exhaustion_config.flush_min_bear_body_ratio
            and current_volume
            >= evidence.flush_volume
            * self.supply_exhaustion_config.dry_max_intermediate_volume_vs_flush
        ):
            return "CANCEL", "현재봉재차대량음봉"
        if current_volume > evidence.flush_volume * cfg.resume_max_volume_vs_flush:
            return "WAIT", (
                f"거래절벽대기_대량음봉대비x{current_volume/max(1.0,evidence.flush_volume):.2f}"
            )
        if current_volume > left_median * cfg.resume_max_volume_vs_left_median:
            return "WAIT", (
                f"거래바닥대기_왼쪽중앙대비x{current_volume/max(1.0,left_median):.2f}"
            )
        if current_range > flush_range * cfg.resume_max_range_vs_flush:
            return "WAIT", "현재봉폭아직큼"
        if signed_body < -cfg.resume_max_bear_body_ratio:
            return "WAIT", "현재봉음봉몸통아직큼"
        if close_location < cfg.resume_min_close_location:
            return "WAIT", "현재봉종가위치약함"

        if len(selected) >= 2:
            previous = selected[-2]
            if current_price < float(previous.close) * 0.997:
                return "WAIT", "종목하락멈춤미확인"

        return "READY", (
            f"지수안정+거래바닥유지 거래=대량봉x"
            f"{current_volume/max(1.0,evidence.flush_volume):.2f}"
        )

    def _service_one_deferred(self) -> None:
        now_mono = time.monotonic()
        if now_mono < self._next_deferred_service or not self._deferred_market_signals:
            return
        self._next_deferred_service = now_mono + self.v758_config.deferred_service_sec
        now = datetime.now(KST)

        for symbol, item in list(self._deferred_market_signals.items()):
            if item.expires_at <= now:
                self._drop_deferred(symbol, "보류시간만료")
            elif symbol in self.broker.positions:
                self._drop_deferred(symbol, "이미보유")
        if not self._deferred_market_signals:
            return

        market_ok, market_reason = self._market_entry_allowed()
        if not market_ok:
            self._show_throttled_error(
                "v758-deferred-wait",
                f"시장하락보류후보 {len(self._deferred_market_signals)}종목 최우선감시—{market_reason}",
                30.0,
            )
            return

        # 미완성 1분봉을 50초 시점에 재확인해 거래바닥 오판을 줄인다.
        if v757.intrabar_probe_stage(now, self.v757_config) == 0:
            return

        symbols = list(self._deferred_market_signals)
        symbol = symbols[self._deferred_cursor % len(symbols)]
        self._deferred_cursor += 1
        item = self._deferred_market_signals[symbol]
        if now_mono - item.last_check_mono < self.v758_config.deferred_service_sec:
            return
        item.last_check_mono = now_mono
        item.check_count += 1

        try:
            quote = self.client.get_quote(symbol)
            bars = [
                bar
                for bar in self.client.get_minute_bars(symbol, limit=120, completed_only=False)
                if v753.regular_session_bar(bar.timestamp)
            ]
        except v753.V73Error as exc:
            self._show_throttled_error(
                f"v758-deferred:{symbol}",
                f"시장보류후보 재확인 실패 {self._label(symbol)}: {v753.brief_error(exc)}",
                60.0,
            )
            return
        selected = v757._same_session_bars(bars)
        if not selected:
            return
        current = selected[-1]
        minute = now.replace(second=0, microsecond=0)
        if current.timestamp.astimezone(KST).replace(second=0, microsecond=0) != minute:
            return

        elapsed = max(1.0, (now - minute).total_seconds())
        projected_volume = v757.projected_minute_volume(
            int(current.volume), elapsed,
            enabled=bool(self.v757_config.current_volume_projection),
        )
        projected_bar = v753.Bar(
            symbol=current.symbol,
            timestamp=current.timestamp,
            open=float(current.open),
            high=float(current.high),
            low=float(current.low),
            close=float(current.close),
            volume=projected_volume,
        )
        recent = v757.recent_volume_sum(selected, self.v757_config.recent_volume_bars)
        if recent < self.v757_config.min_recent_volume:
            self._drop_deferred(symbol, f"최근20봉거래량감소_{recent:,}주")
            return

        status, reason = self._validate_deferred_setup(
            item, selected, projected_bar, float(quote.price)
        )
        if status == "CANCEL":
            self._drop_deferred(symbol, reason)
            return
        if status == "WAIT":
            self._show_throttled_error(
                f"v758-deferred-structure:{symbol}",
                f"시장안정후 종목재확인중 {self._label(symbol)} {reason}",
                30.0,
            )
            self._save_deferred_state()
            return

        view = v757._strategy_view_with_current(
            self.strategy,
            symbol,
            [*selected[:-1], projected_bar],
        )
        snapshot = v757.IntrabarSnapshot(
            symbol=symbol,
            bar=projected_bar,
            strategy_view=view,
            elapsed_sec=elapsed,
            raw_volume=int(current.volume),
            projected_volume=projected_volume,
            recent_volume=recent,
            supply=item.evidence,
        )
        fresh_signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            float(item.signal.score),
            (
                f"{item.signal.reason}|시장하락보류후지수안정재개"
                f"|보류={(now-item.detected_at).total_seconds()/60:.1f}분|{reason}"
            ),
            float(quote.price),
            now,
            "매물소진50초선진입",
            float(item.signal.stop_loss_pct),
            float(item.signal.take_profit_pct),
        )
        self._intrabar_snapshots[symbol] = snapshot
        self._supply_evidence[symbol] = item.evidence
        self.latest_signals[symbol] = fresh_signal
        self._resuming_symbols.add(symbol)
        self.logger.warning(
            "시장하락멈춤 재진입검증 %s | %s | 원신호가=%.0f 현재=%.0f",
            self._label(symbol), reason, item.signal.reference_price, quote.price,
        )
        try:
            entered = super()._attempt_entry(fresh_signal)
        finally:
            self._resuming_symbols.discard(symbol)
        if entered or symbol in self.pending_entries:
            self._deferred_market_signals.pop(symbol, None)
            self._save_deferred_state()

    def _service_symbol(self, symbol: str, *, hot: bool) -> None:
        super()._service_symbol(symbol, hot=hot)
        self._service_one_deferred()

    def _queue_pending_entry(self, signal: Any) -> None:
        allowed, reason = self._market_entry_allowed()
        if not allowed:
            if self._can_defer_signal(signal):
                self._defer_market_signal(signal, reason)
            else:
                self.pending_entries.pop(signal.symbol, None)
                self.logger.warning("진입재시도차단 %s %s", self._label(signal.symbol), reason)
            return
        if signal.symbol in self._deferred_market_signals and signal.symbol not in self._resuming_symbols:
            self._deferred_market_signals.pop(signal.symbol, None)
            self._save_deferred_state()
        super()._queue_pending_entry(signal)

    def _attempt_entry(self, signal: Any, *, from_retry: bool = False) -> bool:
        allowed, reason = self._market_entry_allowed()
        if not allowed:
            if self._can_defer_signal(signal):
                self._defer_market_signal(signal, reason)
            else:
                self.pending_entries.pop(signal.symbol, None)
                self.logger.warning("진입폐기 %s %s", self._label(signal.symbol), reason)
            return False
        if signal.symbol in self._deferred_market_signals and signal.symbol not in self._resuming_symbols:
            self._deferred_market_signals.pop(signal.symbol, None)
            self._save_deferred_state()
        entered = super()._attempt_entry(signal, from_retry=from_retry)
        if entered:
            self._deferred_market_signals.pop(signal.symbol, None)
            self._save_deferred_state()
        return entered

    def _pending_signal_valid(self, signal: Any, current_price: float) -> tuple[bool, str]:
        valid, reason = super()._pending_signal_valid(signal, current_price)
        if not valid:
            return valid, reason
        allowed, market_reason = self._market_entry_allowed()
        if not allowed:
            if self._can_defer_signal(signal):
                self._defer_market_signal(signal, market_reason)
            return False, market_reason
        return True, reason


def _sample_evidence() -> Any:
    return v756.SupplyExhaustionEvidence(
        rise_low=100.0,
        rise_high=110.0,
        rise_pct=0.10,
        retrace=0.30,
        flush_time=datetime(2026, 7, 10, 10, 0, tzinfo=KST),
        flush_low=104.0,
        flush_volume=10_000.0,
        flush_volume_vs_median=2.5,
        flush_volume_vs_p75=1.5,
        flush_volume_vs_recent_max=1.2,
        dry_volume_vs_flush=0.30,
        dry_volume_vs_left_median=0.70,
        dry_volume_vs_previous=0.60,
        dry_range_vs_flush=0.50,
        current_close=105.0,
        structure_stop_pct=0.015,
    )


def self_check() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    record(
        "V75.7-SHA256고정",
        sha256_file(BASE_DIR / V757_FILENAME) == V757_SHA256,
        V757_SHA256[:16] + "…",
    )
    base_results = v757.self_check()
    base_failures = [name for name, ok, _ in base_results if not ok]
    record("V75.7-기존검증위임", not base_failures, f"{len(base_results)}개 / 실패={base_failures or '없음'}")

    cfg = MarketPauseConfig()
    shock = classify_market_flow(
        [-0.01, -0.005], [-0.004, -0.001], [-0.009, -0.002], [-0.013, -0.003], cfg
    )
    caution = classify_market_flow(
        [-0.008, -0.004], [-0.002, -0.0005], [-0.005, -0.001], [-0.006, -0.002], cfg
    )
    stable = classify_market_flow(
        [-0.015, -0.010], [-0.0001, 0.0002], [-0.001, 0.0003], [-0.010, -0.008], cfg
    )
    hard_daily = classify_market_flow(
        [-0.031, -0.010], [0.001, 0.001], [0.001, 0.001], [0.001, 0.001], cfg
    )
    record("지수급락진행판정", shock.state == "SHOCK" and shock.active_decline, shock.reason)
    record("지수하락진행판정", caution.state == "CAUTION" and caution.active_decline, caution.reason)
    record("일중음수여도단기안정판정", stable.state == "FLOW_STABLE" and stable.stable_now, stable.reason)
    record("일일초급락은계속차단", hard_daily.state == "SHOCK", hard_daily.reason)

    signal = v753.Signal(
        "000001", v753.SignalAction.BUY, 88.0, "테스트", 105.0,
        datetime(2026, 7, 10, 10, 1, tzinfo=KST), "매물소진50초선진입", 0.015, 0.015,
    )
    evidence = _sample_evidence()
    signal_payload = _signal_to_payload(signal)
    evidence_payload = _evidence_to_payload(evidence)
    restored_signal = _signal_from_payload(signal_payload)
    restored_evidence = _evidence_from_payload(evidence_payload)
    record(
        "보류신호저장복원",
        restored_signal.symbol == signal.symbol
        and restored_signal.reference_price == signal.reference_price
        and restored_evidence.flush_low == evidence.flush_low,
        "signal/evidence JSON round-trip",
    )

    # 순수 재검증 함수용 더미 엔진
    dummy = object.__new__(MarketPauseResumeEngine)
    dummy.v758_config = cfg
    dummy.supply_exhaustion_config = v756.SupplyExhaustionConfig()
    item = DeferredMarketSignal(
        signal=signal,
        evidence=evidence,
        name="테스트",
        detected_at=datetime(2026, 7, 10, 10, 1, tzinfo=KST),
        expires_at=datetime(2026, 7, 10, 10, 21, tzinfo=KST),
        market_reason="지수하락",
    )
    bars = [
        v753.Bar("000001", datetime(2026, 7, 10, 10, 0, tzinfo=KST), 108, 109, 104, 105, 10_000),
        v753.Bar("000001", datetime(2026, 7, 10, 10, 1, tzinfo=KST), 105, 105.4, 104.8, 105.1, 2_500),
        v753.Bar("000001", datetime(2026, 7, 10, 10, 2, tzinfo=KST), 105.1, 105.3, 104.9, 105.2, 2_000),
    ]
    ready, ready_reason = MarketPauseResumeEngine._validate_deferred_setup(
        dummy, item, bars, bars[-1], 105.2
    )
    record("지수안정후거래바닥재검증", ready == "READY", f"{ready}:{ready_reason}")

    broken = list(bars)
    broken[-1] = v753.Bar(
        "000001", broken[-1].timestamp, 104.5, 104.8, 103.0, 103.2, 8_000
    )
    cancel, cancel_reason = MarketPauseResumeEngine._validate_deferred_setup(
        dummy, item, broken, broken[-1], 103.2
    )
    record("보류중저점이탈취소", cancel == "CANCEL", f"{cancel}:{cancel_reason}")

    heavy = list(bars)
    heavy[-1] = v753.Bar(
        "000001", heavy[-1].timestamp, 105.0, 105.1, 104.0, 104.1, 8_000
    )
    cancel2, cancel_reason2 = MarketPauseResumeEngine._validate_deferred_setup(
        dummy, item, heavy, heavy[-1], 104.1
    )
    record("보류중재대량음봉취소", cancel2 == "CANCEL", f"{cancel2}:{cancel_reason2}")

    chase, chase_reason = MarketPauseResumeEngine._validate_deferred_setup(
        dummy, item, bars, bars[-1], 107.0
    )
    record("지수안정전급등추격차단", chase == "CANCEL", f"{chase}:{chase_reason}")

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
        str(sorted(function_names & FORBIDDEN_ORDER_NAMES)),
    )
    record(
        "시장하락신호폐기대신보류",
        "def _defer_market_signal" in source
        and "시장하락보류" in source
        and "_deferred_market_signals" in source,
        "조건충족 후보 별도 상태 보존",
    )
    record(
        "보류후보HOT우선감시",
        "deferred + self.hot_symbols" in source,
        "HOT 목록 맨 앞 배치",
    )
    record(
        "지수안정90초후재검증",
        "recovery_confirm_sec" in source
        and "RECOVERY_WAIT" in source
        and "RECOVERY_READY" in source,
        "하락 즉시 해제 금지",
    )
    record(
        "보류후구조재검증",
        "def _validate_deferred_setup" in source
        and "보류중재차대량음봉" in source
        and "거래절벽대기" in source,
        "저점·거래량·소형봉·추격 재검증",
    )
    record(
        "원본쓰기부재",
        "write_text" not in source.split("def load_v757", 1)[1].split("v757 =", 1)[0],
        "V75.7은 SHA 확인 후 읽기 로드만",
    )
    record(
        "계측저장fail-soft",
        "시장보류후보 저장 실패(매매는 계속)" in source,
        "상태 저장 실패가 보유 청산을 막지 않음",
    )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{VERSION} — 지수하락 보류후보·하락멈춤 재진입 PAPER"
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
        engine = MarketPauseResumeEngine(
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
