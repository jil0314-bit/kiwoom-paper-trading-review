#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.79 PAPER — 승인 UI 고정 + 영웅문 연속전환 안정화.

V75.78 전체를 SHA256으로 고정 상속한다. 대시보드 구성, 완성 3분봉,
단계판정, 주문정책, 형 요청 원장은 수정하지 않는다. 변경 범위는 실제로
작동이 확인된 V75.77 전송 순서의 대기시간·최근 성공창 캐시와 대시보드
포커스 복귀 방식뿐이다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import replace
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Callable


VERSION = "V75.79-SMOOTH-CONTINUOUS-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_78_stable_fast_fit_paper.py"
PARENT_SHA256 = "60d30991e240470154db9cd8a168cab71708abbd130556b36525cd1ea1f5f69f"
APPROVED_GUI_SHA256 = "6e7e2e87b55326bed7a067b51939170088291224fd5b31838c5c501df02d4aa3"

VK_CONTROL = 0x11
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_V = 0x56
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_RESTORE = 9
WM_SETTEXT = 0x000C


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7579_immutable_v7578_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v77 = parent.parent
v57 = parent.v57
v7525 = parent.v7525
LinkStatus = v57.LinkStatus
ChartWindowCandidate = v77.ChartWindowCandidate
IS_WINDOWS = v77.IS_WINDOWS
diag = parent.diag


def _wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.008) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def smooth_restore_review_focus(root: Any, tree: Any, cursor: Any) -> bool:
    """정상 창에는 lift/ShowWindow/BringWindowToTop을 생략해 번쩍임을 줄인다."""
    restore_cursor = v57.v7547.restore_cursor_position
    try:
        root.update_idletasks()
    except Exception:
        restore_cursor(cursor)
        return False

    if not v57.v7538.IS_WINDOWS:
        try:
            tree.focus_set()
            return True
        except Exception:
            return False
        finally:
            restore_cursor(cursor)

    hwnd = int(v57.v7547._widget_root_hwnd(root) or 0)
    tree_hwnd = int(v57.v7547._widget_hwnd(tree) or 0)
    if not hwnd:
        restore_cursor(cursor)
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    attached: list[tuple[int, int]] = []
    try:
        if not user32.IsWindow(wintypes.HWND(hwnd)):
            return False
        current_thread = int(kernel32.GetCurrentThreadId() or 0)
        target_thread = int(user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None) or 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(wintypes.HWND(foreground), None) or 0)
            if foreground else 0
        )
        try:
            user32.AllowSetForegroundWindow(-1)
        except OSError:
            pass
        for first, second in ((current_thread, target_thread), (foreground_thread, target_thread)):
            if first and second and first != second:
                try:
                    if user32.AttachThreadInput(first, second, True):
                        attached.append((first, second))
                except OSError:
                    pass

        # 실제로 최소화된 경우에만 복원한다. 정상 최대화/표시 상태에서는
        # SW_RESTORE와 lift를 호출하지 않아 창이 꺼졌다 켜지는 느낌을 없앤다.
        if bool(user32.IsIconic(wintypes.HWND(hwnd))):
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        user32.SetActiveWindow(wintypes.HWND(hwnd))
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            user32.SetForegroundWindow(wintypes.HWND(hwnd))

        foreground_ok = _wait_until(
            lambda: int(user32.GetForegroundWindow() or 0) == hwnd,
            timeout=0.14,
        )
        if tree_hwnd and user32.IsWindow(wintypes.HWND(tree_hwnd)):
            user32.SetFocus(wintypes.HWND(tree_hwnd))
        try:
            tree.focus_force()
        except Exception:
            tree.focus_set()
        return bool(foreground_ok)
    except Exception as exc:
        diag(f"V7579_SMOOTH_RESTORE_FAIL {type(exc).__name__}: {exc}")
        return False
    finally:
        restore_cursor(cursor)
        for first, second in reversed(attached):
            try:
                user32.AttachThreadInput(first, second, False)
            except OSError:
                pass


class SmoothContinuousHeroLink(parent.MdiSafeOneClickHeroLink):
    """최근 성공 대상 재사용 + 같은 입력순서의 짧은 상태확인형 전송."""

    CACHE_SECONDS = 8.0

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self._smooth_candidates: dict[int, ChartWindowCandidate] = {}
        self._smooth_candidate_at = 0.0
        self._smooth_input: dict[int, tuple[int, tuple[int, int], str, tuple[int, int, int, int], float]] = {}
        self._using_smooth_cache = False

    @classmethod
    def _press(cls, vk: int, *, scan_code: bool = False, hold: float = 0.018) -> bool:
        ok1, _, _ = cls._send_inputs([cls._key_input(vk, False, scan_code=scan_code)])
        time.sleep(hold)
        ok2, _, _ = cls._send_inputs([cls._key_input(vk, True, scan_code=scan_code)])
        return bool(ok1 and ok2)

    @classmethod
    def _hotkey(cls, modifier: int, key: int) -> bool:
        ok1, _, _ = cls._send_inputs([cls._key_input(modifier, False)])
        time.sleep(0.012)
        ok2, _, _ = cls._send_inputs([cls._key_input(key, False)])
        time.sleep(0.015)
        ok3, _, _ = cls._send_inputs([cls._key_input(key, True)])
        time.sleep(0.012)
        ok4, _, _ = cls._send_inputs([cls._key_input(modifier, True)])
        return bool(ok1 and ok2 and ok3 and ok4)

    @classmethod
    def _clear_with_keys(cls) -> bool:
        selected = cls._hotkey(VK_CONTROL, 0x41)
        time.sleep(0.025)
        return bool(cls._press(VK_BACK) and selected)

    @classmethod
    def _paste_with_keys(cls) -> bool:
        cleared = cls._clear_with_keys()
        time.sleep(0.025)
        pasted = cls._hotkey(VK_CONTROL, VK_V)
        time.sleep(0.055)
        return bool(cleared and pasted)

    @classmethod
    def _click(cls, x: int, y: int) -> bool:
        if not IS_WINDOWS:
            return False
        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(int(x), int(y)):
            return False
        time.sleep(0.015)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.035)
        return True

    def _activate_mdi_child(self, chart: int, top: int) -> None:
        if not IS_WINDOWS or not chart or chart == top:
            return
        mdi_client = self._mdi_client_for(chart, top)
        if mdi_client:
            self._send_message(mdi_client, v77.WM_MDIACTIVATE, chart, 0, timeout_ms=220)
        try:
            self._u32().ShowWindow(wintypes.HWND(chart), v77.SW_SHOW)
        except (AttributeError, OSError, TypeError):
            pass
        time.sleep(0.018)

    def _activate_and_focus(self, root: int, target: int, x: int, y: int):
        chart = int(root)
        top = self._top_level_of(chart)
        self._activate_mdi_child(chart, top)
        user32 = self._u32()
        kernel32 = ctypes.windll.kernel32
        target_thread = self._thread_of(top)
        foreground = self._foreground()
        foreground_thread = self._thread_of(foreground) if foreground else 0
        this_thread = int(kernel32.GetCurrentThreadId() or 0)
        attached: list[tuple[int, int]] = []
        try:
            try:
                user32.AllowSetForegroundWindow(-1)
            except OSError:
                pass
            for first, second in ((this_thread, target_thread), (foreground_thread, target_thread)):
                if first and second and first != second:
                    try:
                        if user32.AttachThreadInput(first, second, True):
                            attached.append((first, second))
                    except OSError:
                        pass
            if bool(user32.IsIconic(wintypes.HWND(top))):
                user32.ShowWindow(wintypes.HWND(top), SW_RESTORE)
            user32.SetForegroundWindow(wintypes.HWND(top))
            user32.SetActiveWindow(wintypes.HWND(top))
            foreground_ok = _wait_until(lambda: int(self._foreground() or 0) == top, 0.16)
            if not foreground_ok:
                # 보통은 호출하지 않는다. Windows가 첫 전경 요청을 거절한
                # 경우에만 기존 방식으로 한 번 보강해 연동 성공률을 보존한다.
                user32.BringWindowToTop(wintypes.HWND(top))
                user32.SetForegroundWindow(wintypes.HWND(top))
                foreground_ok = _wait_until(lambda: int(self._foreground() or 0) == top, 0.12)
            click_ok = bool(foreground_ok and self._click(x, y))
            _wait_until(
                lambda: self._belongs_to_chart(chart, int(self._focus_hwnd(top) or 0))
                or self._belongs_to_chart(chart, self._window_from_point(x, y)),
                0.10,
            )
            foreground_now = int(self._foreground() or 0)
            focus = int(self._focus_hwnd(top) or 0)
            hit = self._window_from_point(x, y)
            focus_inside = self._belongs_to_chart(chart, focus)
            hit_inside = self._belongs_to_chart(chart, hit)
            gate = bool(
                foreground_ok and foreground_now == top and click_ok
                and (focus_inside or hit_inside)
            )
            self._last_focus_detail = (
                f"chart={chart} top={top} foreground={foreground_now} focus={focus} "
                f"hit={hit} focus_inside={focus_inside} hit_inside={hit_inside} "
                f"click={click_ok} gate={gate} smooth=True"
            )
            return gate, focus, click_ok
        finally:
            for first, second in reversed(attached):
                try:
                    user32.AttachThreadInput(first, second, False)
                except OSError:
                    pass

    def _cached_candidate(self) -> ChartWindowCandidate | None:
        hwnd = int(getattr(self, "_last_chart_hwnd", 0) or 0)
        cached = self._smooth_candidates.get(hwnd)
        if not hwnd or cached is None or time.monotonic() - self._smooth_candidate_at > self.CACHE_SECONDS:
            return None
        if not self._is_window_visible(hwnd) or not self._is_window_enabled(hwnd):
            return None
        rect = self._win_rect(hwnd)
        title = self._win_text(hwnd)
        if not self._rect_usable(rect) or v77._title_token_count(title) <= 0:
            return None
        return replace(cached, title=title, rect=rect)

    def _chart_candidates(self) -> list[ChartWindowCandidate]:
        if IS_WINDOWS:
            cached = self._cached_candidate()
            if cached is not None:
                self._using_smooth_cache = True
                diag(f"V7579_CACHE_HIT chart={cached.hwnd} top={cached.top}")
                return [cached]
        self._using_smooth_cache = False
        values = super()._chart_candidates()
        self._smooth_candidates = {item.hwnd: item for item in values}
        self._smooth_candidate_at = time.monotonic()
        return values

    def _resolve_input(self, root: int):
        cached = self._smooth_input.get(int(root))
        current_rect = self._win_rect(root)
        if self._using_smooth_cache and cached is not None and self._rect_usable(current_rect):
            target, point, route, old_rect, saved_at = cached
            if time.monotonic() - saved_at <= self.CACHE_SECONDS:
                dx, dy = point[0] - old_rect[0], point[1] - old_rect[1]
                adjusted = (current_rect[0] + dx, current_rect[1] + dy)
                inside = (
                    current_rect[0] <= adjusted[0] < current_rect[2]
                    and current_rect[1] <= adjusted[1] < current_rect[3]
                )
                target_ok = not target or self._belongs_to_chart(root, target)
                if inside and target_ok:
                    diag(f"V7579_INPUT_CACHE_HIT chart={root} target={target} point={adjusted}")
                    return target, adjusted, "SMOOTH_CACHE " + route
        result = super()._resolve_input(root)
        target, point, route = result
        if point is not None and self._rect_usable(current_rect):
            self._smooth_input[int(root)] = (
                int(target or 0), (int(point[0]), int(point[1])), str(route),
                current_rect, time.monotonic(),
            )
        return result

    def _finish_edit_route(self, chart: int, top: int, target: int, symbol: str):
        buffer = ctypes.create_unicode_buffer(symbol)
        sent, _result, error = self._send_message(
            target, WM_SETTEXT, 0, ctypes.cast(buffer, ctypes.c_void_p).value or 0,
            timeout_ms=350,
        )
        if not sent:
            return None
        observed_before = self._read_control_text(target)
        if int(self._foreground() or 0) != top:
            diag(f"V7579_FAIL stage=foreground_lost_after_wmset symbol={symbol}")
            return LinkStatus.FAILED, "입력 중 영웅문 전경 포커스를 잃었습니다."
        enter_ok = self._press(VK_RETURN)
        time.sleep(0.055)
        observed_after = self._read_control_text(target)
        verified = symbol in {str(observed_before).strip(), str(observed_after).strip()}
        diag(
            f"V7579_SEND route=WM_SETTEXT symbol={symbol} target={target} "
            f"sent={sent} enter={enter_ok} before={observed_before!r} "
            f"after={observed_after!r} verified={verified} error={error}"
        )
        if not enter_ok:
            return LinkStatus.FAILED, f"{symbol} 입력 후 Enter 전송에 실패했습니다."
        if verified:
            return LinkStatus.VERIFIED, f"{symbol} 영웅문 차트 전환 확인"
        return LinkStatus.SENT_UNVERIFIED, f"{symbol} 빠른 단일 전송 완료 — 영웅문 화면을 확인해주세요."

    def _finish_keyboard_route(self, chart: int, top: int, effective: int, symbol: str):
        if int(self._foreground() or 0) != top:
            return LinkStatus.FAILED, "키 전송 직전 영웅문 전경 포커스를 잃었습니다."
        paste_ok = self._paste_with_keys()
        time.sleep(0.035)
        foreground_still = int(self._foreground() or 0) == top
        observed_before = self._read_control_text(effective) if effective else ""
        enter_ok = bool(paste_ok and foreground_still and self._press(VK_RETURN))
        time.sleep(0.065)
        observed_after = self._read_control_text(effective) if effective else ""
        verified = symbol in {str(observed_before).strip(), str(observed_after).strip()}
        diag(
            f"V7579_SEND route=CTRL_V_ONCE symbol={symbol} paste={paste_ok} "
            f"foreground={foreground_still} enter={enter_ok} before={observed_before!r} "
            f"after={observed_after!r} verified={verified}"
        )
        if not paste_ok or not foreground_still or not enter_ok:
            return LinkStatus.FAILED, (
                f"{symbol} 빠른 단일 전송 실패 · paste={paste_ok} "
                f"foreground={foreground_still} enter={enter_ok}"
            )
        if verified:
            return LinkStatus.VERIFIED, f"{symbol} 영웅문 차트 전환 확인"
        return LinkStatus.SENT_UNVERIFIED, f"{symbol} 빠른 Ctrl+V 한 번 전송 완료 — 화면을 확인해주세요."

    def _invalidate_smooth_cache(self) -> None:
        self._smooth_candidates.clear()
        self._smooth_input.clear()
        self._smooth_candidate_at = 0.0
        self._using_smooth_cache = False

    def send_symbol(self, symbol: str):
        started = time.monotonic()
        normalized = v57.normalize_symbol_code(symbol)
        result = super().send_symbol(normalized or str(symbol).strip())
        used_cache = bool(self._using_smooth_cache)
        safe_retry = (
            result[0] == LinkStatus.FAILED
            and used_cache
            and any(token in str(result[1]) for token in (
                "안전하게 클릭하지 못했습니다", "보이는 [0600]", "입력칸을 안전하게",
            ))
        )
        if safe_retry:
            diag(f"V7579_CACHE_INVALIDATE symbol={normalized!r} reason={result[1]!r}")
            self._invalidate_smooth_cache()
            result = super().send_symbol(normalized or str(symbol).strip())
        diag(
            f"V7579_RESULT symbol={normalized!r} status={result[0].value} "
            f"cache={used_cache} elapsed_ms={(time.monotonic() - started) * 1000:.1f}"
        )
        return result


def _install_runtime() -> None:
    v57.RestoredHeroLink = SmoothContinuousHeroLink
    v57.ReliableHeroLink = SmoothContinuousHeroLink
    v57.restore_review_focus = smooth_restore_review_focus
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "link_class": f"{SmoothContinuousHeroLink.__module__}.{SmoothContinuousHeroLink.__qualname__}",
        "link_implementation": "V75.77 input order/focus gates preserved; wait/cache/restore optimized",
        "link_base": "V75.77 MDI-safe single-route sequence",
        "continuous_mode": "last-success chart/input cache + latest-request worker",
        "focus_restore": "no lift/restore/topmost in normal state; one latest-only restore",
        "ui_source_sha256": sha256_file(BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py"),
        "ui_changed_from_v75_78": False,
    })
    return identity


def _offline_cache_probe() -> tuple[bool, str]:
    global IS_WINDOWS
    with tempfile.TemporaryDirectory() as temp_dir:
        link = SmoothContinuousHeroLink(Path(temp_dir) / "unused.json")
        candidate = ChartWindowCandidate(
            hwnd=2000, top=1000, title="[0600] 키움종합차트", class_name="AfxMDIChild",
            rect=(10, 40, 1300, 900), score=2000.0, top_z=0, is_mdi=True,
            active_mdi=True, focus_inside=True,
        )
        link._last_chart_hwnd = 2000
        link._smooth_candidates = {2000: candidate}
        link._smooth_candidate_at = time.monotonic()
        link._is_window_visible = lambda _hwnd: True
        link._is_window_enabled = lambda _hwnd: True
        link._win_rect = lambda _hwnd: (10, 40, 1300, 900)
        link._win_text = lambda _hwnd: "[0600] 키움종합차트"
        original = IS_WINDOWS
        IS_WINDOWS = True
        try:
            values = link._chart_candidates()
        finally:
            IS_WINDOWS = original
        return len(values) == 1 and values[0].hwnd == 2000 and link._using_smooth_cache, "chart=2000 cache-hit"


def self_check() -> list[tuple[str, bool, str]]:
    superseded = {"실행링크교체", "GUI모듈패치", "검증연동동일성", "단일포커스복귀"}
    rows = [row for row in parent.self_check() if row[0] not in superseded]
    cache_ok, cache_detail = _offline_cache_probe()
    restore_source = inspect.getsource(smooth_restore_review_focus)
    link_source = inspect.getsource(SmoothContinuousHeroLink)
    gui_sha = sha256_file(BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py")
    rows.extend([
        ("V75.78기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("대시보드파일무변경", gui_sha == APPROVED_GUI_SHA256, gui_sha[:16]),
        ("실행연동교체", v57.RestoredHeroLink is SmoothContinuousHeroLink, str(v57.RestoredHeroLink)),
        ("최근성공창캐시", cache_ok, cache_detail),
        ("정상창무복원", "if bool(user32.IsIconic" in restore_source and ".lift(" not in restore_source, "minimized only SW_RESTORE"),
        ("빠른동일키순서", all(token in link_source for token in ("_clear_with_keys", "_paste_with_keys", "VK_RETURN", "CTRL_V_ONCE")), "Ctrl+A/Backspace/Ctrl+V/Enter"),
        ("안전한캐시재탐색", "safe_retry" in link_source and "_invalidate_smooth_cache" in link_source, "pre-send failures only"),
        ("완성3분봉보존", runtime_identity().get("bar_minutes") == 3 and runtime_identity().get("completed_only") is True, "3-minute completed only"),
        ("PAPER전용", VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7579_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
