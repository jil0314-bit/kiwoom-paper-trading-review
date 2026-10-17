#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.73 PAPER — completed 3-minute strategy and direct one-click [0600] link."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
from types import ModuleType
from typing import Any, Iterable

from v7573_three_minute_exhaustion_logic import (
    CONFIRMED,
    STRONG_BUY,
    SUPPORT_WAIT,
    WATCH,
    ThreeMinuteDecision,
    evaluate_three_minute_exhaustion,
)


VERSION = "V75.73-3MIN-ONECLICK-PAPER"
BAR_SECONDS = 180
BAR_MINUTES = 3
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_72_link_ui_final_paper.py"
# Updated mechanically after the V75.57 toolbar cleanup is SHA-pinned through V75.72.
PARENT_SHA256 = "08ec21fa12fa82d19dbcc39e98efdf092ac042012e7ca6cda75c06d7665e94de"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7573_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.72 부모 모듈 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v44 = v57.v7544
v43 = v44.v7543
v7525 = v57.v7525
v753 = v7525.v753
v754 = v43.v754
v756 = v43.v756
v7514 = v43.v7514
v7515 = v43.v7515
v7530 = v43.v7530
v7539 = v43.v7539
v7540 = v43.v7540
KST = v44.KST


def _bucket_start(timestamp: datetime) -> datetime:
    value = timestamp.astimezone(KST) if timestamp.tzinfo else timestamp.replace(tzinfo=KST)
    return value.replace(minute=(value.minute // BAR_MINUTES) * BAR_MINUTES, second=0, microsecond=0)


def aggregate_three_minute_bars(
    one_minute_bars: Iterable[Any],
    *,
    now: datetime | None = None,
    limit: int = 0,
) -> list[Any]:
    """Aggregate ordered 1-minute exchange bars into completed 3-minute OHLCV."""
    grouped: dict[datetime, list[Any]] = {}
    for bar in sorted(one_minute_bars, key=lambda item: item.timestamp):
        grouped.setdefault(_bucket_start(bar.timestamp), []).append(bar)
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    current = current.astimezone(KST)
    output: list[Any] = []
    for bucket, items in sorted(grouped.items()):
        # The strategy never sees an unfinished 3-minute candle.
        if bucket + timedelta(seconds=BAR_SECONDS) > current:
            continue
        first, last = items[0], items[-1]
        output.append(v753.Bar(
            symbol=str(first.symbol),
            timestamp=bucket,
            open=float(first.open),
            high=max(float(item.high) for item in items),
            low=min(float(item.low) for item in items),
            close=float(last.close),
            volume=sum(max(0, int(item.volume)) for item in items),
        ))
    return output[-limit:] if limit > 0 else output


BaseClient = v753.KiwoomRestClient


class ThreeMinuteKiwoomClient(BaseClient):
    """Keep the verified REST parser and expose only completed 3-minute bars."""

    def get_minute_bars(self, symbol: str, *, limit: int = 120, completed_only: bool = True):
        raw_limit = min(900, max(30, int(limit) * BAR_MINUTES + BAR_MINUTES))
        raw = super().get_minute_bars(symbol, limit=raw_limit, completed_only=False)
        bars = aggregate_three_minute_bars(raw, limit=limit)
        if not bars:
            raise v753.ApiSchemaError(f"완성 3분봉 없음 symbol={symbol}")
        return bars


def signal_completion_time_three_minute(signal: Any) -> datetime:
    timestamp = v754.normalize_kst(signal.timestamp)
    return timestamp + timedelta(seconds=BAR_SECONDS)


def _signal_age_seconds_three_minute(signal: Any, *, now: datetime | None = None) -> float:
    timestamp = getattr(signal, "timestamp", None)
    if not isinstance(timestamp, datetime):
        return float("inf")
    current = v754.normalize_kst(now or datetime.now(KST))
    return max(0.0, (current - v754.normalize_kst(timestamp) - timedelta(seconds=BAR_SECONDS)).total_seconds())


class OneClickDirectPasteHeroLink(v57.AutoDiscoverHeroLink):
    """No saved coordinate and no Edit discovery: one direct click/paste/Enter route."""

    REFERENCE_X_96DPI = 110
    REFERENCE_Y_96DPI = 15

    @staticmethod
    def _client_hit_point(root: int) -> tuple[int, int] | None:
        if not v57.v7538.IS_WINDOWS or not root:
            return None
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetClientRect(wintypes.HWND(root), ctypes.byref(rect)):
            return None
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(wintypes.HWND(root), ctypes.byref(origin)):
            return None
        try:
            dpi = int(user32.GetDpiForWindow(wintypes.HWND(root)) or 96)
        except (AttributeError, OSError):
            dpi = 96
        scale = max(0.75, min(3.0, dpi / 96.0))
        x = int(origin.x + OneClickDirectPasteHeroLink.REFERENCE_X_96DPI * scale)
        y = int(origin.y + OneClickDirectPasteHeroLink.REFERENCE_Y_96DPI * scale)
        width = max(1, int(rect.right - rect.left))
        height = max(1, int(rect.bottom - rect.top))
        if not origin.x <= x < origin.x + width or not origin.y <= y < origin.y + height:
            return None
        return x, y

    def probe_auto_target(self) -> tuple[bool, str]:
        if not v57.v7538.IS_WINDOWS:
            return False, "Windows에서만 작동합니다."
        roots = self._chart_roots()
        if not roots:
            return False, "[0600] 키움종합차트를 찾지 못했습니다. 차트를 먼저 열어주세요."
        point = self._client_hit_point(roots[0])
        if point is None:
            return False, "[0600] 차트 상단 입력영역을 계산하지 못했습니다."
        return True, f"원클릭 직접연동 준비됨 — 위치 저장 불필요 / 입력점={point}"

    def send_symbol(self, symbol: str):
        normalized = str(symbol).strip()
        if not v57.v7538.IS_WINDOWS:
            return v57.LinkStatus.FAILED, "Windows에서만 작동합니다."
        if not normalized.isdigit() or len(normalized) != 6:
            return v57.LinkStatus.FAILED, f"종목코드 형식 오류: {normalized}"
        if self._clipboard_read() != normalized:
            self._clipboard_write(normalized)
        if self._clipboard_read() != normalized:
            return v57.LinkStatus.FAILED, "종목코드 클립보드 저장 실패"

        roots = self._chart_roots()
        if not roots:
            return v57.LinkStatus.FAILED, "[0600] 키움종합차트를 찾지 못했습니다."
        root = roots[0]
        point = self._client_hit_point(root)
        if point is None:
            return v57.LinkStatus.FAILED, "[0600] 입력영역 계산 실패"
        x, y = point
        target = self._find_target(root, x, y)
        foreground_ok, focus, click_ok = self._activate_and_focus(root, target, x, y)
        focus_root = self._root_of(focus)
        gate = foreground_ok and (focus_root == root or click_ok)
        v57.diag(
            "ONECLICK_FOCUS "
            f"symbol={normalized} root={root} target={target} point={point} "
            f"foreground={foreground_ok} focus={focus} focus_root={focus_root} click={click_ok}"
        )
        if not gate:
            return v57.LinkStatus.FAILED, "[0600] 차트 입력칸 포커스 실패 — 관리자 실행 여부를 확인하세요."

        # Exactly one user-proven path: Ctrl+A -> Backspace -> Ctrl+V -> Enter.
        paste_ok = self._paste_with_keys()
        time.sleep(0.18)
        foreground_still_ok = self._foreground() == root
        enter_ok = self._press(v57.v7538.VK_RETURN) if paste_ok and foreground_still_ok else False
        effective = focus if focus and focus != root else target
        observed = self._read_control_text(effective) if effective else ""
        verified = observed == normalized
        v57.diag(
            "ONECLICK_RESULT "
            f"symbol={normalized} paste={paste_ok} foreground={foreground_still_ok} "
            f"enter={enter_ok} verified={verified} observed={observed!r}"
        )
        if not paste_ok:
            return v57.LinkStatus.FAILED, f"{normalized} Ctrl+V 전송 실패"
        if not foreground_still_ok:
            return v57.LinkStatus.SENT_UNVERIFIED, f"{normalized} 붙여넣음 — Enter 직전 포커스 상실"
        if not enter_ok:
            return v57.LinkStatus.SENT_UNVERIFIED, f"{normalized} 붙여넣음 — Enter 전송 실패"
        if verified:
            return v57.LinkStatus.VERIFIED, f"{normalized} 영웅문 차트 전환 확인"
        return v57.LinkStatus.SENT_UNVERIFIED, f"{normalized} 원클릭 전송 완료 — 차트 변경 확인"


_ORIGINAL_DETECT = v7525.detect_v7525
_ORIGINAL_ASSESS = v7514.assess_loose_supply_potential


def _same_session_bars(strategy: Any, symbol: str) -> list[Any]:
    return list(v756._same_session_bars(strategy.bars.get(symbol, [])))


def _percentile75(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, min(len(ordered) - 1, int((len(ordered) * 0.75) + 0.9999) - 1))]


def _compatible_evidence(bars: list[Any], decision: ThreeMinuteDecision):
    flush = bars[decision.flush_index]
    dry = bars[decision.dry_index]
    current = bars[-1]
    pre = bars[max(0, decision.flush_index - 8):decision.flush_index]
    pre_volumes = [max(0.0, float(item.volume)) for item in pre]
    left_median = max(1.0, float(statistics.median(pre_volumes)))
    left_p75 = max(1.0, _percentile75(pre_volumes))
    recent_max = max([1.0, *pre_volumes[-5:]])
    rise_low = min(float(item.low) for item in pre)
    rise_high = max(float(item.high) for item in pre)
    rise_span = max(1.0, rise_high - rise_low)
    pullback_low = min(float(item.low) for item in bars[decision.flush_index:])
    flush_volume = max(1.0, float(flush.volume))
    dry_range = max(1e-9, float(dry.high) - float(dry.low))
    flush_range = max(1e-9, float(flush.high) - float(flush.low))
    previous = bars[max(decision.flush_index, decision.dry_index - 1)]
    return v756.SupplyExhaustionEvidence(
        rise_low=rise_low,
        rise_high=rise_high,
        rise_pct=decision.rise_pct,
        retrace=(rise_high - pullback_low) / rise_span,
        flush_time=v754.normalize_kst(flush.timestamp),
        flush_low=float(flush.low),
        flush_volume=flush_volume,
        flush_volume_vs_median=flush_volume / left_median,
        flush_volume_vs_p75=flush_volume / left_p75,
        flush_volume_vs_recent_max=flush_volume / recent_max,
        dry_volume_vs_flush=float(dry.volume) / flush_volume,
        dry_volume_vs_left_median=float(dry.volume) / left_median,
        dry_volume_vs_previous=float(dry.volume) / max(1.0, float(previous.volume)),
        dry_range_vs_flush=dry_range / flush_range,
        current_close=float(current.close),
        structure_stop_pct=max(0.0, (float(current.close) - decision.support_low) / max(0.01, float(current.close))),
    )


def detect_v7573(strategy: Any, symbol: str, *, config: Any):
    bars = _same_session_bars(strategy, symbol)
    decision = evaluate_three_minute_exhaustion(bars)
    if decision is not None and decision.stage == STRONG_BUY:
        evidence = _compatible_evidence(bars, decision)
        current = bars[-1]
        signal = v753.Signal(
            symbol,
            v753.SignalAction.BUY,
            132.0,
            decision.reason + "|완성3분봉|자동손절없음|목표=1.5%고정",
            float(current.close),
            current.timestamp,
            "매물소진눌림1차",
            0.03,
            0.015,
        )
        return signal, "3분매도소진저점지지확인", evidence
    if decision is not None:
        # Once this sequence is recognised, the older dry-bar detector must not
        # buy early at WATCH/SUPPORT_WAIT or re-enter after the confirm bar.
        return None, f"3분매도소진_{decision.stage}_대기", None
    return _ORIGINAL_DETECT(strategy, symbol, config=config)


def assess_v7573(
    strategy: Any,
    symbol: str,
    *,
    loose: Any,
    strict: Any,
    now: datetime,
    name: str,
    source: str,
):
    item, state = _ORIGINAL_ASSESS(
        strategy, symbol, loose=loose, strict=strict, now=now, name=name, source=source
    )
    bars = _same_session_bars(strategy, symbol)
    decision = evaluate_three_minute_exhaustion(bars)
    if decision is None:
        return item, state
    flush = bars[decision.flush_index]
    dry = bars[decision.dry_index]
    pre = bars[max(0, decision.flush_index - 8):decision.flush_index]
    pre_volumes = [max(0.0, float(value.volume)) for value in pre]
    flush_volume = max(1.0, float(flush.volume))
    left_median = max(1.0, float(statistics.median(pre_volumes)))
    stage_map = {
        WATCH: ("거래바닥관찰", ("3분지지봉대기",), 82.0),
        SUPPORT_WAIT: ("관심종목-매수가능성", ("두봉뒤저점지지확인대기",), 92.0),
        STRONG_BUY: ("즉시매수조건근접", (), 100.0),
        CONFIRMED: ("관심종목-매수가능성", ("확인봉경과_재진입금지",), 88.0),
    }
    potential_stage, missing, score = stage_map[decision.stage]
    if item is None:
        item = v7514.SupplyPotential(
            symbol=symbol,
            name=name or symbol,
            stage=potential_stage,
            score=score,
            detected_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=24),
            flush_time=v754.normalize_kst(flush.timestamp),
            rise_pct=decision.rise_pct,
            retrace=0.0,
            flush_vs_median=flush_volume / left_median,
            flush_vs_p75=flush_volume / max(1.0, _percentile75(pre_volumes)),
            flush_vs_recent_max=flush_volume / max([1.0, *pre_volumes[-5:]]),
            dry_vs_flush=float(dry.volume) / flush_volume,
            dry_range_vs_flush=(float(dry.high) - float(dry.low)) / max(1e-9, float(flush.high) - float(flush.low)),
            low_break_pct=0.0,
            strict_missing=tuple(missing),
            source=f"{source}|{decision.reason}",
        )
    else:
        item = replace(
            item,
            stage=potential_stage,
            score=max(float(item.score), score),
            strict_missing=tuple(missing),
            source=f"{source}|{decision.reason}",
            updated_at=now,
        )
    return item, "3분매도소진단계"


BaseEngine = v7525.V7525Engine


class V7573ThreeMinuteEngine(BaseEngine):
    """Use 180-second completion age in the live V75.44 replay path."""

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        if symbol in self.broker.positions:
            super()._refresh_actual_bars(symbol, quote)
            return
        self._refresh_symbol = symbol
        self._batch_stale_buy.discard(symbol)
        try:
            bars = [
                bar for bar in self.client.get_minute_bars(symbol, limit=25, completed_only=True)
                if v753.regular_session_bar(bar.timestamp)
            ]
            selected = list(v756._same_session_bars(bars))
            if not selected:
                return
            last = self.last_bar_time.get(symbol)
            new_bars = [bar for bar in selected if last is None or bar.timestamp > last]
            if not new_bars:
                return
            now_kst = datetime.now(KST)
            replay_candidates: list[Any] = []
            for old_bar in new_bars[:-1]:
                signal = self.strategy.on_bar(old_bar, None)
                self.latest_signals[symbol] = signal
                self.last_bar_time[symbol] = old_bar.timestamp
                if getattr(signal, "action", None) == v753.SignalAction.BUY:
                    replay_candidates.append(signal)
            newest = new_bars[-1]
            age = max(0.0, (now_kst - v754.normalize_kst(newest.timestamp) - timedelta(seconds=BAR_SECONDS)).total_seconds())
            self._on_bar(
                newest,
                quote,
                allow_entry=age <= self.settings.max_bar_signal_age_sec,
                bar_age_sec=age,
            )
            self.last_bar_time[symbol] = newest.timestamp
            recent_buy = v44.newest_fresh_buy(
                replay_candidates,
                now=now_kst,
                max_age_sec=min(v44.CATCHUP_SIGNAL_MAX_AGE_SEC, float(self.max_entry_signal_age_sec)),
            )
            newest_signal = self.latest_signals.get(symbol)
            newest_is_buy = newest_signal is not None and getattr(newest_signal, "action", None) == v753.SignalAction.BUY
            if recent_buy is not None and not newest_is_buy and symbol not in self.broker.positions and symbol not in self.pending_entries:
                valid, reason = self._pending_signal_valid(recent_buy, float(quote.price))
                if valid:
                    self._attempt_entry(recent_buy)
        finally:
            self._refresh_symbol = None


def _install_runtime() -> None:
    v753.KiwoomRestClient = ThreeMinuteKiwoomClient
    v7525.V7525Engine = V7573ThreeMinuteEngine
    v754.signal_completion_time = signal_completion_time_three_minute
    v44._signal_age_seconds = _signal_age_seconds_three_minute
    # Disable the inherited 50/57-second 1-minute early-entry path.
    early_module = sys.modules.get(next(cls.__module__ for cls in BaseEngine.__mro__ if cls.__name__ == "EarlyDryupVolumeEngine"), None)
    if early_module is not None:
        early_module.intrabar_probe_stage = lambda _now, _config: 0
    v7525.detect_v7525 = detect_v7573
    v756.detect_supply_exhaustion_pullback = detect_v7573
    for module, name in (
        (v7515, "detect_precision_supply"),
        (v7530, "strict_detect"),
        (v7540, "detect_v7540"),
        (v7539, "detect_v7539"),
    ):
        setattr(module, name, detect_v7573)
    v7514.assess_loose_supply_potential = assess_v7573
    v7514.SUPPLY_PATTERNS.add("매물소진눌림1차")
    v57.RestoredHeroLink = OneClickDirectPasteHeroLink
    v57.ReliableHeroLink = OneClickDirectPasteHeroLink
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    return {
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "bar_minutes": BAR_MINUTES,
        "completed_only": True,
        "client_class": f"{v753.KiwoomRestClient.__module__}.{v753.KiwoomRestClient.__qualname__}",
        "engine_class": f"{v7525.V7525Engine.__module__}.{v7525.V7525Engine.__qualname__}",
        "gui_class": f"{v7525.SimpleDoubleClickGui.__module__}.{v7525.SimpleDoubleClickGui.__qualname__}",
        "link_class": f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
        "link_route": "[0600] fixed top input -> Ctrl+A -> Backspace -> Ctrl+V -> Enter",
        "saved_position_used": False,
        "python": sys.executable,
        "cwd": str(Path.cwd()),
    }


def self_check() -> list[tuple[str, bool, str]]:
    rows = list(parent.self_check())
    rows.extend([
        ("V75.72기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("완성3분봉클라이언트", v753.KiwoomRestClient is ThreeMinuteKiwoomClient, "1분 OHLCV→3분 합성"),
        ("3분봉완성시각", v754.signal_completion_time is signal_completion_time_three_minute, "+180초"),
        ("1분50초선진입차단", any(cls.__name__ == "EarlyDryupVolumeEngine" for cls in BaseEngine.__mro__), "완성3분봉만"),
        ("원클릭직접연동", v57.RestoredHeroLink is OneClickDirectPasteHeroLink, "저장좌표·Edit탐색 미사용"),
        ("실행엔진교체", v7525.V7525Engine is V7573ThreeMinuteEngine, str(v7525.V7525Engine)),
        ("삼익형단계판정", callable(evaluate_three_minute_exhaustion), "관찰→지지대기→강력매수"),
        ("PAPER전용", "PAPER" in VERSION, VERSION),
    ])
    return rows


def main() -> int:
    print("RUNTIME_IDENTITY", json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    return int(parent.main())


if __name__ == "__main__":
    if "--runtime-info" in sys.argv:
        print(json.dumps(runtime_identity(), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(0)
    if "--self-check" in sys.argv:
        checks = self_check()
        for name, ok, detail in checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in checks) else 1)
    raise SystemExit(main())
