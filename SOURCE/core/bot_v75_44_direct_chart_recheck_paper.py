#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.44 PAPER — 영웅문 차트 자동탐색·화살표 연동·매수신호 재검증판.

운영 안전 원칙
- PAPER 전용이다. 실제 주문 함수는 추가하지 않는다.
- 오래된 신호를 뒤늦게 추격하지 않는다. 놓친 완성봉 재검증은 최대 90초이며,
  현재가 이격·호가·유동성·시장상태·보유한도 등 기존 진입 게이트를 모두 다시 통과해야 한다.
- 매수가능성/매수직전 후보를 HOT 감시 최우선으로 승격해 5분 일반 폴링 사이의
  1분봉 신호 유실을 줄인다.
- 영웅문 [0600] 키움종합차트의 상단 종목 입력 Edit 컨트롤을 Win32로 자동 탐색한다.
  자동 탐색이 실패한 PC에서만 기존 수동 위치 저장을 예비 경로로 사용한다.
"""
from __future__ import annotations

import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
from typing import Any, Iterable, Sequence

VERSION = "V75.44-DIRECT-CHART-RECHECK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_43_atomic_safety_rebuild_paper.py"
BASE_SHA256 = "44ccbba4b506161737fcd5514fc3e40617d9b45f0c204345e0ee203eb78286fa"
PACKAGE_DIR = BASE_DIR.parent
DIAG_FILE = PACKAGE_DIR / "V75_44_LINK_DIAG.txt"
ENTRY_AUDIT_FILE = PACKAGE_DIR / "V75_44_ENTRY_AUDIT.csv"

CATCHUP_SIGNAL_MAX_AGE_SEC = 90.0
AUTO_TARGET_MIN_SCORE = 95.0
CHART_TITLE_TOKENS = ("키움종합차트", "[0600]")
HOT_STAGE_BONUS = {
    "즉시매수조건근접": 1_200.0,
    "엄격매수조건충족": 1_200.0,
    "관심종목-매수가능성": 1_000.0,
    "거래바닥근접": 850.0,
    "거래바닥관찰": 650.0,
    "대량음봉묶음발견": 350.0,
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(
            f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7543 = _load_fixed(BASE_FILENAME, BASE_SHA256, "v7544_immutable_base")
v7525 = v7543.v7525
v7538 = v7543.v7538
v7530 = v7543.v7530
v753 = v7543.v753
v754 = v7543.v754
v756 = v7543.v756
KST = v7543.KST
LinkStatus = v7543.LinkStatus
_ORIGINAL_SELF_CHECK = v7543.self_check


def diag(message: str) -> None:
    try:
        with DIAG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


# 하위 Win32 계층도 같은 진단 파일로 모은다.
v7543.diag = diag
v7538.diag = diag


@dataclass(frozen=True, slots=True)
class AutoChartTarget:
    root: int
    target: int
    root_title: str
    class_name: str
    text: str
    rect: tuple[int, int, int, int]
    score: float


def _contains_hangul(text: str) -> bool:
    return any("가" <= char <= "힣" for char in text)


def score_edit_candidate(
    *,
    root_rect: tuple[int, int, int, int],
    rect: tuple[int, int, int, int],
    class_name: str,
    text: str,
    parent_class: str = "",
) -> float:
    """[0600] 차트 상단 종목입력칸 후보 점수. Win32 없이도 테스트 가능하다."""
    root_left, root_top, root_right, root_bottom = root_rect
    left, top, right, bottom = rect
    width = max(0, right - left)
    height = max(0, bottom - top)
    rel_x = left - root_left
    rel_y = top - root_top
    root_width = max(1, root_right - root_left)
    root_height = max(1, root_bottom - root_top)

    cls = class_name.lower()
    parent = parent_class.lower()
    score = 0.0
    if cls == "edit":
        score += 70.0
    elif "edit" in cls:
        score += 55.0
    else:
        return -1_000.0
    if "combo" in parent:
        score += 20.0
    if 0 <= rel_y <= min(120, int(root_height * 0.16)):
        score += 45.0
    elif rel_y <= 180:
        score += 15.0
    else:
        score -= 45.0
    if 0 <= rel_x <= min(520, int(root_width * 0.40)):
        score += 35.0
    elif rel_x <= int(root_width * 0.70):
        score += 5.0
    else:
        score -= 35.0
    if 45 <= width <= 320:
        score += 25.0
    elif 25 <= width <= 500:
        score += 8.0
    else:
        score -= 20.0
    if 14 <= height <= 48:
        score += 15.0
    elif 10 <= height <= 70:
        score += 5.0
    else:
        score -= 15.0
    clean = text.strip()
    if clean.isdigit() and len(clean) == 6:
        score += 35.0
    elif 1 <= len(clean) <= 16 and _contains_hangul(clean):
        score += 25.0
    elif clean:
        score += 3.0
    return score


def select_arrow_index(current: int, count: int, delta: int) -> int:
    """Treeview ↑/↓ 이동 인덱스. 처음에는 위=0, 아래=0에서 시작한다."""
    if count <= 0:
        return -1
    if current < 0:
        return 0 if delta >= 0 else count - 1
    return max(0, min(count - 1, current + delta))


def _signal_age_seconds(signal: Any, *, now: datetime | None = None) -> float:
    timestamp = getattr(signal, "timestamp", None)
    if not isinstance(timestamp, datetime):
        return float("inf")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=KST)
    completed = timestamp.astimezone(KST) + timedelta(seconds=60)
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return max(0.0, (current.astimezone(KST) - completed).total_seconds())


def newest_fresh_buy(
    signals: Iterable[Any],
    *,
    now: datetime | None = None,
    max_age_sec: float = CATCHUP_SIGNAL_MAX_AGE_SEC,
) -> Any | None:
    """최근 완성봉 BUY 중 신선도 제한 안의 가장 최신 신호만 선택한다."""
    buys = [
        signal
        for signal in signals
        if getattr(signal, "action", None) == v753.SignalAction.BUY
        and _signal_age_seconds(signal, now=now) <= max_age_sec
    ]
    if not buys:
        return None
    return max(buys, key=lambda signal: v754.normalize_kst(signal.timestamp))


def hot_priority_score(
    *,
    stage: str = "",
    candidate_score: float = 0.0,
    pending: bool = False,
    buy_signal: bool = False,
    structure_active: bool = False,
    near_recent_high: bool = False,
    same_time_volume_ratio: float = 0.0,
    missing_count: int = 0,
) -> float:
    """매수 가능성이 높은 후보가 HOT 20에서 밀리지 않도록 우선순위를 계산한다."""
    score = float(candidate_score)
    score += HOT_STAGE_BONUS.get(str(stage), 0.0)
    if pending:
        score += 1_500.0
    if buy_signal:
        score += 1_350.0
    if structure_active:
        score += 120.0
    if near_recent_high:
        score += 60.0
    if same_time_volume_ratio >= 3.0:
        score += 35.0
    score -= max(0, int(missing_count)) * 8.0
    return score


class AutoDiscoverHeroLink(v7543.TargetIdentityHeroLink):
    """수동 위치 저장 없이 [0600] 차트의 종목 입력 Edit를 자동 탐색한다."""

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self._auto_cache: AutoChartTarget | None = None
        self._auto_cache_at = 0.0

    @staticmethod
    def _win_text(hwnd: int) -> str:
        if not v7538.IS_WINDOWS or not hwnd:
            return ""
        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(2, length + 2))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _win_class(hwnd: int) -> str:
        if not v7538.IS_WINDOWS or not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _win_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        if not v7538.IS_WINDOWS or not hwnd:
            return None
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    @staticmethod
    def _valid_hwnd(hwnd: int) -> bool:
        if not v7538.IS_WINDOWS or not hwnd:
            return False
        user32 = ctypes.windll.user32
        return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))

    def _chart_roots(self) -> list[int]:
        if not v7538.IS_WINDOWS:
            return []
        user32 = ctypes.windll.user32
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        roots: list[int] = []

        @enum_proc
        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._win_text(int(hwnd))
            if all(token in title for token in CHART_TITLE_TOKENS):
                roots.append(int(hwnd))
            elif "키움종합차트" in title:
                roots.append(int(hwnd))
            return True

        user32.EnumWindows(callback, 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        roots.sort(
            key=lambda hwnd: (
                0 if hwnd == foreground else 1,
                -len(self._win_text(hwnd)),
            )
        )
        return roots

    def _edit_candidates(self, root: int) -> list[AutoChartTarget]:
        if not v7538.IS_WINDOWS:
            return []
        user32 = ctypes.windll.user32
        root_rect = self._win_rect(root)
        if root_rect is None:
            return []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        candidates: list[AutoChartTarget] = []
        root_title = self._win_text(root)

        @enum_proc
        def callback(hwnd: int, _lparam: int) -> bool:
            child = int(hwnd)
            if not user32.IsWindowVisible(child) or not user32.IsWindowEnabled(child):
                return True
            class_name = self._win_class(child)
            if "edit" not in class_name.lower():
                return True
            rect = self._win_rect(child)
            if rect is None:
                return True
            parent = int(user32.GetParent(child) or 0)
            parent_class = self._win_class(parent)
            text = self._win_text(child)
            score = score_edit_candidate(
                root_rect=root_rect,
                rect=rect,
                class_name=class_name,
                text=text,
                parent_class=parent_class,
            )
            candidates.append(
                AutoChartTarget(
                    root=root,
                    target=child,
                    root_title=root_title,
                    class_name=class_name,
                    text=text,
                    rect=rect,
                    score=score,
                )
            )
            return True

        user32.EnumChildWindows(root, callback, 0)
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates

    def discover_target(self, *, force: bool = False) -> AutoChartTarget | None:
        now_mono = time.monotonic()
        if (
            not force
            and self._auto_cache is not None
            and now_mono - self._auto_cache_at <= 15.0
            and self._valid_hwnd(self._auto_cache.root)
            and self._valid_hwnd(self._auto_cache.target)
        ):
            return self._auto_cache
        all_candidates: list[AutoChartTarget] = []
        for root in self._chart_roots():
            all_candidates.extend(self._edit_candidates(root))
        all_candidates.sort(key=lambda item: item.score, reverse=True)
        preview = "; ".join(
            f"hwnd={item.target} score={item.score:.0f} class={item.class_name} "
            f"text={item.text!r} rect={item.rect}"
            for item in all_candidates[:5]
        )
        diag(f"AUTO_DISCOVER candidates={len(all_candidates)} top={preview}")
        if not all_candidates or all_candidates[0].score < AUTO_TARGET_MIN_SCORE:
            self._auto_cache = None
            self._auto_cache_at = now_mono
            return None
        self._auto_cache = all_candidates[0]
        self._auto_cache_at = now_mono
        return self._auto_cache

    def probe_auto_target(self) -> tuple[bool, str]:
        target = self.discover_target(force=True)
        if target is None:
            return False, "[0600] 키움종합차트의 종목 입력칸을 자동으로 찾지 못했습니다."
        return True, (
            f"자동연동 준비됨 — {target.class_name} / 점수 {target.score:.0f} / "
            f"현재값 {target.text or '빈칸'}"
        )

    def _activate_standard_target(self, root: int, target: int) -> tuple[bool, int]:
        """표준 Edit는 마우스 커서를 움직이지 않고 전경화·포커스한다."""
        if not v7538.IS_WINDOWS:
            return False, 0
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        root_thread = int(user32.GetWindowThreadProcessId(root, None) or 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground, None) or 0) if foreground else 0
        this_thread = int(kernel32.GetCurrentThreadId() or 0)
        attached: list[tuple[int, int]] = []
        try:
            try:
                user32.AllowSetForegroundWindow(0xFFFFFFFF)
            except OSError:
                pass
            for first, second in ((this_thread, root_thread), (foreground_thread, root_thread)):
                if first and second and first != second:
                    if user32.AttachThreadInput(first, second, True):
                        attached.append((first, second))
            user32.ShowWindow(root, 9)
            user32.BringWindowToTop(root)
            user32.SetForegroundWindow(root)
            user32.SetActiveWindow(root)
            user32.SetFocus(target)
            time.sleep(0.20)
            focus = self._focus_hwnd(root)
            return int(user32.GetForegroundWindow() or 0) == root, int(focus or 0)
        finally:
            for first, second in reversed(attached):
                user32.AttachThreadInput(first, second, False)

    def _send_to_auto_target(self, symbol: str, target: AutoChartTarget) -> tuple[Any, str]:
        left, top, right, bottom = target.rect
        x = (left + right) // 2
        y = (top + bottom) // 2
        foreground_ok, focus = self._activate_standard_target(target.root, target.target)
        click_focus_ok = False
        if not foreground_ok or focus != target.target:
            # 일부 영웅문 빌드는 SetFocus만 거부한다. 그때만 자동 클릭 경로를 한 번 사용한다.
            foreground_ok, focus, click_focus_ok = self._activate_and_focus(
                target.root, target.target, x, y
            )
        if not foreground_ok:
            diag(
                f"AUTO_GATE_FOREGROUND_FAIL symbol={symbol} root={target.root} "
                f"target={target.target} focus={focus} click={click_focus_ok}"
            )
            return LinkStatus.FAILED, f"{symbol} 복사됨 — 영웅문 차트를 활성화하지 못했습니다."
        sent, verified, observed = self._standard_edit_set_v7540(target.target, symbol)
        diag(
            f"AUTO_INPUT symbol={symbol} root={target.root} target={target.target} "
            f"sent={sent} verified={verified} observed={observed!r} focus={focus}"
        )
        if not sent:
            self._auto_cache = None
            return LinkStatus.FAILED, f"{symbol} 복사됨 — 자동 탐색 입력칸 전송 실패"
        # 입력값 read-back과 전경창을 확인한 뒤 Enter를 보낸다.
        if int(ctypes.windll.user32.GetForegroundWindow() or 0) != target.root:
            return LinkStatus.SENT_UNVERIFIED, f"{symbol} 입력됨 — Enter 직전 차트 포커스를 잃었습니다."
        enter_ok = self._press(v7538.VK_RETURN)
        diag(
            f"AUTO_ENTER symbol={symbol} root={target.root} target={target.target} "
            f"ok={enter_ok} verified={verified}"
        )
        if not enter_ok:
            return LinkStatus.SENT_UNVERIFIED, f"{symbol} 입력됨 — Enter 전송 실패"
        if verified:
            return LinkStatus.VERIFIED, f"{symbol} → 영웅문 차트 자동 전환 요청 완료"
        return LinkStatus.SENT_UNVERIFIED, f"{symbol} 입력·Enter 전송 — 차트 화면에서 확인 필요"

    def send_symbol(self, symbol: str):
        if not v7538.IS_WINDOWS:
            return LinkStatus.FAILED, "Windows에서만 작동합니다."
        if not symbol.isdigit() or len(symbol) != 6:
            return LinkStatus.FAILED, f"종목코드 형식 오류: {symbol}"
        if self._clipboard_read() != symbol:
            self._clipboard_write(symbol)
        if self._clipboard_read() != symbol:
            return LinkStatus.FAILED, "종목코드 클립보드 저장 실패"

        target = self.discover_target()
        if target is not None:
            status, message = self._send_to_auto_target(symbol, target)
            if status != LinkStatus.FAILED:
                return status, message
            # 컨트롤 핸들이 바뀐 경우 한 번만 강제 재탐색한다.
            target = self.discover_target(force=True)
            if target is not None:
                status, message = self._send_to_auto_target(symbol, target)
                if status != LinkStatus.FAILED:
                    return status, message

        # 자동 탐색이 실패한 특수 UI에서만 기존 수동 저장 위치를 예비 경로로 사용한다.
        if self.configured and self._config_valid():
            status, message = super().send_symbol(symbol)
            return status, "자동탐색 실패 후 예비 위치 사용: " + message
        return LinkStatus.FAILED, (
            f"{symbol} 복사됨 — [0600] 차트 입력칸 자동탐색 실패. "
            "영웅문 [0600] 차트를 열어 둔 뒤 다시 더블클릭하세요."
        )


class V7544Engine(v7543.V7543Engine):
    """매수가능성 HOT 우선감시, 최근 완성봉 재검증, 진입 감사로그를 추가한다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v7544_audit_lock = threading.RLock()
        self._v7544_hot_snapshot: tuple[str, ...] = ()
        super().__init__(*args, **kwargs)
        self.logger.warning(
            "[%s] 매수가능성 HOT 최우선 | 최근 완성봉 재검증<=%.0f초 | "
            "오래된 신호 추격금지 | 진입감사=%s",
            VERSION,
            CATCHUP_SIGNAL_MAX_AGE_SEC,
            ENTRY_AUDIT_FILE.name,
        )

    def _append_entry_audit(
        self,
        *,
        symbol: str,
        event: str,
        detail: str,
        bar_time: datetime | None = None,
        signal: Any | None = None,
        current_price: float | None = None,
    ) -> None:
        row = {
            "logged_at": datetime.now(KST).isoformat(timespec="seconds"),
            "symbol": str(symbol),
            "name": str(getattr(self, "names", {}).get(str(symbol), "")),
            "bar_time": (
                v754.normalize_kst(bar_time).isoformat(timespec="seconds")
                if isinstance(bar_time, datetime)
                else ""
            ),
            "event": str(event),
            "detail": str(detail)[:500],
            "pattern": str(getattr(signal, "pattern", "")) if signal is not None else "",
            "reference_price": (
                f"{float(getattr(signal, 'reference_price', 0.0)):.4f}"
                if signal is not None
                else ""
            ),
            "current_price": f"{float(current_price):.4f}" if current_price else "",
        }
        with self._v7544_audit_lock:
            exists = ENTRY_AUDIT_FILE.exists()
            try:
                with ENTRY_AUDIT_FILE.open("a", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    if not exists:
                        writer.writeheader()
                    writer.writerow(row)
            except OSError as exc:
                self.logger.warning("진입 감사로그 저장 실패: %s", exc)

    def _record_entry_decision(self, symbol: str, status: str, reason: str) -> None:
        super()._record_entry_decision(symbol, status, reason)
        quote = getattr(self, "latest_quotes", {}).get(str(symbol))
        self._append_entry_audit(
            symbol=str(symbol),
            event=f"ENTRY_{status}",
            detail=str(reason),
            current_price=float(quote.price) if quote is not None else None,
        )

    def _update_hot_symbols(self) -> None:
        scored: list[tuple[float, str]] = []
        pending_symbols = set(getattr(self, "pending_entries", {}))
        potential_map = getattr(self, "_supply_potential", {})
        now_mono = time.monotonic()
        for symbol in self.active_symbols:
            if symbol in self.broker.positions:
                continue
            meta = self.candidate_meta.get(symbol)
            potential = potential_map.get(symbol)
            stage = str(getattr(potential, "stage", ""))
            missing = tuple(getattr(potential, "strict_missing", ()) or ())
            latest_signal = self.latest_signals.get(symbol)
            buy_signal = (
                latest_signal is not None
                and getattr(latest_signal, "action", None) == v753.SignalAction.BUY
            )
            structure_active = (
                symbol in self.strategy.capitulation_cache
                or symbol in self.strategy.impulse_cache
                or potential is not None
            )
            near_recent_high = False
            bars = self.strategy.bars.get(symbol)
            if bars and len(bars) >= 15:
                recent = list(bars)[-30:]
                recent_high = max(float(item.high) for item in recent)
                near_recent_high = bool(
                    recent_high > 0 and float(recent[-1].close) >= recent_high * 0.985
                )
            score = hot_priority_score(
                stage=stage,
                candidate_score=float(getattr(meta, "score", 0.0) or 0.0),
                pending=symbol in pending_symbols,
                buy_signal=buy_signal,
                structure_active=structure_active,
                near_recent_high=near_recent_high,
                same_time_volume_ratio=float(
                    self.strategy.same_time_volume_ratio.get(symbol, 0.0) or 0.0
                ),
                missing_count=len(missing),
            )
            scored.append((score, symbol))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [symbol for _, symbol in scored[: max(1, self.hot_candidate_count)]]
        old = set(self.hot_symbols)
        self.hot_symbols = selected
        for symbol in set(selected) - old:
            # 새로 매수가능성에 승격된 종목은 다음 루프에서 바로 완성봉을 확인한다.
            self.next_chart_due[symbol] = min(
                self.next_chart_due.get(symbol, now_mono), now_mono
            )
        snapshot = tuple(selected)
        if snapshot != self._v7544_hot_snapshot:
            self._v7544_hot_snapshot = snapshot
            top_preview = ", ".join(
                f"{symbol}:{score:.0f}" for score, symbol in scored[:8]
            )
            self.logger.info("HOT재정렬 매수가능성우선 | %s", top_preview or "없음")

    def _refresh_actual_bars(self, symbol: str, quote: Any) -> None:
        # 보유종목 청산 감시는 검증된 부모 경로를 그대로 사용한다.
        if symbol in self.broker.positions:
            super()._refresh_actual_bars(symbol, quote)
            return

        self._refresh_symbol = symbol
        self._batch_stale_buy.discard(symbol)
        try:
            bars = [
                bar
                for bar in self.client.get_minute_bars(
                    symbol, limit=25, completed_only=True
                )
                if v753.regular_session_bar(bar.timestamp)
            ]
            selected = list(v756._same_session_bars(bars))
            if not selected:
                return
            last = self.last_bar_time.get(symbol)
            new_bars = [
                bar for bar in selected if last is None or bar.timestamp > last
            ]
            if not new_bars:
                return

            now_kst = datetime.now(KST)
            replay_candidates: list[Any] = []
            # 밀린 과거 완성봉도 BUY 여부를 보존한다. 단 최대 90초 이내만 재검증 대상이다.
            for old_bar in new_bars[:-1]:
                signal = self.strategy.on_bar(old_bar, None)
                self.latest_signals[symbol] = signal
                self.last_bar_time[symbol] = old_bar.timestamp
                if getattr(signal, "action", None) == v753.SignalAction.BUY:
                    replay_candidates.append(signal)
                    self._append_entry_audit(
                        symbol=symbol,
                        event="MISSED_COMPLETED_BAR_BUY",
                        detail=(
                            f"폴링 사이 완성봉 BUY 발견 age={_signal_age_seconds(signal, now=now_kst):.1f}초"
                        ),
                        bar_time=old_bar.timestamp,
                        signal=signal,
                        current_price=float(quote.price),
                    )

            newest = new_bars[-1]
            completed_at = newest.timestamp.astimezone(KST) + timedelta(seconds=60)
            age = max(0.0, (now_kst - completed_at).total_seconds())
            self._on_bar(
                newest,
                quote,
                allow_entry=age <= self.settings.max_bar_signal_age_sec,
                bar_age_sec=age,
            )
            self.last_bar_time[symbol] = newest.timestamp

            recent_buy = newest_fresh_buy(
                replay_candidates,
                now=now_kst,
                max_age_sec=min(
                    CATCHUP_SIGNAL_MAX_AGE_SEC,
                    float(self.max_entry_signal_age_sec),
                ),
            )
            newest_signal = self.latest_signals.get(symbol)
            newest_is_buy = (
                newest_signal is not None
                and getattr(newest_signal, "action", None) == v753.SignalAction.BUY
            )
            if (
                recent_buy is not None
                and not newest_is_buy
                and symbol not in self.broker.positions
                and symbol not in self.pending_entries
            ):
                valid, reason = self._pending_signal_valid(
                    recent_buy, float(quote.price)
                )
                self._append_entry_audit(
                    symbol=symbol,
                    event="CATCHUP_REVALIDATE",
                    detail=f"{'통과' if valid else '차단'}:{reason}",
                    bar_time=recent_buy.timestamp,
                    signal=recent_buy,
                    current_price=float(quote.price),
                )
                if valid:
                    self.logger.warning(
                        "최근완성봉 재검증 %s | 신호봉=%s | 현재가=%.0f | %s",
                        self._label(symbol),
                        v754.normalize_kst(recent_buy.timestamp).strftime("%H:%M"),
                        float(quote.price),
                        reason,
                    )
                    self._attempt_entry(recent_buy)

            if new_bars and self.risk.data_halted and not self.state_integrity_block:
                self.recovery_successes += 1
                if self.recovery_successes >= 2:
                    self.risk.set_data_halt(False)
                    self.recovery_successes = 0
                    self.logger.warning("시장데이터 정상복구")
        finally:
            if symbol in self._batch_stale_buy:
                self._stale_block_until_mono[symbol] = (
                    time.monotonic() + self.stale_symbol_cooldown_sec
                )
            self._refresh_symbol = None

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
        signal = self.latest_signals.get(bar.symbol)
        potential = getattr(self, "_supply_potential", {}).get(bar.symbol)
        stage = str(getattr(potential, "stage", "없음"))
        missing = tuple(getattr(potential, "strict_missing", ()) or ())
        decision = self._entry_decisions.get(str(bar.symbol))
        if getattr(signal, "action", None) == v753.SignalAction.BUY:
            detail = (
                f"BUY신호 allow_entry={allow_entry} age={bar_age_sec:.1f}초 "
                f"decision={decision[1:] if decision else '없음'} stage={stage}"
            )
            event = "BAR_BUY_SIGNAL"
        else:
            detail = (
                f"BUY없음 signal={getattr(signal, 'reason', '없음')} "
                f"stage={stage} missing={','.join(map(str, missing[:4])) or '없음'} "
                f"allow_entry={allow_entry} age={bar_age_sec:.1f}초"
            )
            event = "BAR_NO_BUY"
        self._append_entry_audit(
            symbol=str(bar.symbol),
            event=event,
            detail=detail,
            bar_time=bar.timestamp,
            signal=signal,
            current_price=float(getattr(quote, "price", 0.0) or 0.0),
        )


class V7544Gui(v7538.BaseGui):
    """더블클릭과 ↑/↓ 선택 이동 모두 영웅문 차트를 즉시 전환한다."""

    DEBOUNCE_SEC = 0.6

    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return
        import queue

        click_mode = {"single": v7538._single_click_enabled()}

        def mode_label() -> str:
            return "한 번 클릭" if click_mode["single"] else "더블클릭"

        root.title(f"V75.44 관심·관찰·매수·매도 — {mode_label()} 즉시 차트연동")
        root.geometry("1420x760")
        root.minsize(1120, 560)
        hero = AutoDiscoverHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        status_var = tk.StringVar(
            value="종목을 더블클릭하거나 ↑/↓로 이동하면 [0600] 영웅문 차트가 즉시 바뀝니다."
        )
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(
            toolbar, text="자동연동 확인", command=lambda: check_auto_link()
        ).pack(side="left")
        ttk.Button(
            toolbar, text="수동 위치 저장(예비)", command=lambda: begin_capture()
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar, text="예비 위치 초기화", command=lambda: do_reset()
        ).pack(side="left", padx=(6, 0))
        single_var = tk.BooleanVar(value=click_mode["single"])
        ttk.Checkbutton(
            toolbar,
            text="한 번 클릭 사용",
            variable=single_var,
            command=lambda: change_click_mode(),
        ).pack(side="left", padx=(10, 0))
        mode_help_var = tk.StringVar(
            value=f"  {mode_label()} 또는 ↑/↓ → 영웅문 차트 즉시 변경"
        )
        ttk.Label(toolbar, textvariable=mode_help_var).pack(side="left")
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("no", "stage", "stage_time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "no": "순번",
            "stage": "단계",
            "stage_time": "편입/매수시각",
            "name": "종목명",
            "symbol": "코드",
            "price": "현재가 / 평단 / +1.5% 목표",
            "reason": "조건·손익·보류이유",
        }
        widths = {
            "no": 55,
            "stage": 105,
            "stage_time": 110,
            "name": 180,
            "symbol": 75,
            "price": 290,
            "reason": 565,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = "w" if column in ("name", "reason") else ("e" if column == "price" else "center")
            tree.column(
                column,
                width=widths[column],
                anchor=anchor,
                stretch=column in ("name", "reason"),
            )
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor="w", padding=8).pack(fill="x")

        state: dict[str, Any] = {
            "last_symbol": "",
            "last_ts": 0.0,
            "selected_symbol": "",
            "last_action_ts": 0.0,
        }

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}")
                return False

        def switch_symbol(symbol: str, *, source: str) -> None:
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f"종목코드를 읽지 못했습니다: {symbol!r}")
                return
            now_mono = time.monotonic()
            if (
                click_mode["single"]
                and symbol == state["last_symbol"]
                and now_mono - float(state["last_ts"]) < self.DEBOUNCE_SEC
            ):
                return
            state["last_symbol"] = symbol
            state["selected_symbol"] = symbol
            state["last_ts"] = now_mono
            state["last_action_ts"] = now_mono
            copied = tk_copy(symbol)
            diag(f"GUI_LINK symbol={symbol} copied={copied} source={source}")
            status_var.set(f"{symbol} 영웅문 차트 전환 중…")
            root.update_idletasks()
            try:
                status, message = hero.send_symbol(symbol)
            except Exception as exc:
                status, message = LinkStatus.FAILED, f"오류: {type(exc).__name__}"
                diag(f"SEND_EXC symbol={symbol} {type(exc).__name__}: {exc}")
            prefix = {
                "VERIFIED": "✅ ",
                "SENT_UNVERIFIED": "⚠ ",
                "FAILED": "❌ ",
            }.get(status.value, "")
            if status == LinkStatus.FAILED and copied:
                message = f"{message} (코드는 복사됨)"
            status_var.set(f"{prefix}{message}")

        def symbol_from_iid(iid: str) -> str:
            values = tree.item(iid, "values")
            return str(values[4]).strip() if len(values) >= 5 else ""

        def selected_symbol(event: Any) -> None:
            row_id = tree.identify_row(event.y)
            if not row_id or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return
            tree.selection_set(row_id)
            tree.focus(row_id)
            switch_symbol(symbol_from_iid(row_id), source=mode_label())

        def move_selection(delta: int) -> str:
            items = list(tree.get_children(""))
            if not items:
                return "break"
            current_iid = tree.focus()
            if not current_iid:
                selected = tree.selection()
                current_iid = selected[0] if selected else ""
            current_index = items.index(current_iid) if current_iid in items else -1
            next_index = select_arrow_index(current_index, len(items), delta)
            if next_index < 0:
                return "break"
            next_iid = items[next_index]
            tree.selection_set(next_iid)
            tree.focus(next_iid)
            tree.see(next_iid)
            switch_symbol(
                symbol_from_iid(next_iid),
                source="아래화살표" if delta > 0 else "위화살표",
            )
            return "break"

        def apply_click_binding() -> None:
            tree.unbind("<ButtonRelease-1>")
            tree.unbind("<Double-1>")
            if click_mode["single"]:
                tree.bind("<ButtonRelease-1>", selected_symbol)
            else:
                tree.bind("<Double-1>", selected_symbol)
            mode_help_var.set(
                f"  {mode_label()} 또는 ↑/↓ → 영웅문 차트 즉시 변경"
            )
            root.title(
                f"V75.44 관심·관찰·매수·매도 — {mode_label()} 즉시 차트연동"
            )

        def change_click_mode() -> None:
            click_mode["single"] = bool(single_var.get())
            state["last_symbol"] = ""
            state["last_ts"] = 0.0
            apply_click_binding()
            status_var.set(f"클릭 방식을 {mode_label()}으로 변경했습니다.")

        def check_auto_link() -> None:
            ok, message = hero.probe_auto_target()
            state["last_action_ts"] = time.monotonic()
            status_var.set(("✅ " if ok else "❌ ") + message)

        def do_reset() -> None:
            hero.reset_config()
            status_var.set("예비 수동 위치를 초기화했습니다. 자동연동에는 영향이 없습니다.")

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(
                f"예비 위치 저장: {message}" if ok else f"예비 위치 저장 실패: {message}"
            )

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "예비 수동 위치 저장",
                "자동탐색이 실패하는 PC에서만 사용합니다. 확인 후 5초 안에 "
                "영웅문 종목코드 입력칸을 클릭하세요.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            preserve_symbol = str(state.get("selected_symbol", ""))
            if not preserve_symbol:
                selected = tree.selection()
                if selected:
                    preserve_symbol = symbol_from_iid(selected[0])
            for iid in tree.get_children():
                tree.delete(iid)
            rows = list(snapshot.get("rows", []))
            counters: dict[str, int] = {}
            symbol_to_iid: dict[str, str] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                symbol = str(row.get("symbol", ""))
                iid = tree.insert(
                    "",
                    "end",
                    values=(
                        counters[stage],
                        stage,
                        row.get("stage_time", row.get("time", "-")),
                        row.get("name", ""),
                        symbol,
                        row.get("display_price", row.get("price", "")),
                        row.get("reason", ""),
                    ),
                )
                if symbol:
                    symbol_to_iid[symbol] = iid
            if preserve_symbol in symbol_to_iid:
                iid = symbol_to_iid[preserve_symbol]
                tree.selection_set(iid)
                tree.focus(iid)
                tree.see(iid)
            time_var.set(str(snapshot.get("updated", "")))
            if time.monotonic() - float(state["last_action_ts"]) > 3.0:
                ready_count = sum(str(row.get("stage", "")) == "매수직전" for row in rows)
                holdings_count = sum(str(row.get("stage", "")) == "매수종목" for row in rows)
                universe = snapshot.get("universe", {})
                status_var.set(
                    f"시장 {snapshot.get('market', '-')} | 현재감시 {universe.get('active_count', '-')} | "
                    f"매수직전 {ready_count} | 보유 {holdings_count} | 더블클릭·↑↓ 자동연동"
                )

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "snapshot":
                        apply_snapshot(payload)
                    elif kind == "close":
                        root.destroy()
                        return
            except queue.Empty:
                pass
            if not self.closed.is_set():
                root.after(300, poll)

        apply_click_binding()
        tree.bind("<Up>", lambda _event: move_selection(-1))
        tree.bind("<Down>", lambda _event: move_selection(1))
        tree.bind("<Return>", lambda _event: move_selection(0))
        tree.bind("<Button-1>", lambda _event: tree.focus_set(), add="+")
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


# 실제 실행 모듈이 참조하는 공유 객체를 V75.44로 교체한다.
v7525.V7525Engine = V7544Engine
v7525.SimpleDoubleClickGui = V7544Gui
v7525.VERSION = VERSION


def _run_v7544_tests() -> tuple[bool, str]:
    path = BASE_DIR / "test_v75_44_direct_chart_recheck.py"
    try:
        spec = importlib.util.spec_from_file_location("v7544_special_test", path)
        if spec is None or spec.loader is None:
            return False, "테스트 로드 실패"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        code = int(module.run())
        return code == 0, "통과" if code == 0 else "실패"
    except Exception as exc:
        return False, f"예외 {type(exc).__name__}: {exc}"


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    test_ok, test_message = _run_v7544_tests()
    source = Path(__file__).read_text(encoding="utf-8")
    results.extend(
        [
            ("V75.43기준SHA", sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
            ("V75.44전용회귀", test_ok, test_message),
            ("자동차트탐색", "discover_target" in source and "키움종합차트" in source, "[0600] Edit 자동탐색"),
            ("화살표차트연동", 'tree.bind("<Down>"' in source and "move_selection" in source, "↑/↓ 선택+전환"),
            ("매수가능성HOT우선", HOT_STAGE_BONUS["관심종목-매수가능성"] >= 1000, "일반5분 폴링 유실완화"),
            ("놓친봉재검증상한", CATCHUP_SIGNAL_MAX_AGE_SEC <= 90.0, "90초 이내만"),
            ("진입감사로그", ENTRY_AUDIT_FILE.name in source, ENTRY_AUDIT_FILE.name),
        ]
    )
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7530.main())


if __name__ == "__main__":
    raise SystemExit(main())
