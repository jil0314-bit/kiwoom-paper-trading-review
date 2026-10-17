#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.47 PAPER — 마우스 좌표 즉시 복원 + 연속 화살표 실사 강화판.

V75.46의 영웅문 자동탐색, V75 검토창 포커스 복귀, ↑/↓ 연속 차트전환,
V75.45의 입력칸 오탐 방어, V75.44의 매수 로직을 SHA256 고정으로 상속한다.

이번 버전은 GUI 연동 안정성만 좁혀 보강한다.

1. 차트 전환 뒤 첫 복귀 시도를 0ms부터 시작한다.
2. 영웅문 예비 클릭 경로가 물리 마우스를 이동했더라도, V75 전경 복귀의
   성공 여부와 무관하게 전송 직전 좌표로 마우스를 복원한다.
3. Tk 전경 복귀 요청(root.deiconify/lift/focus_force)과 Win32 전경·포커스
   복귀를 함께 사용한다.
4. 기존 focus_token 방식으로 빠른 연속 이동 중 오래된 복귀 예약은 무효화한다.
5. 매매 엔진, 진입 로직, PAPER 전용, 목표 +1.5%, 자동손절 없음은 변경하지 않는다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

VERSION = "V75.47-CURSOR-LOCK-ARROW-VERIFIED-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7546_FILENAME = "bot_v75_46_review_focus_arrow_paper.py"
V7546_SHA256 = "ea2f58cea9bbfccc702ef6253ad46a2d749d74e7d8d93501086d141a9cb04244"

# 0ms 즉시 시도 + 짧은 재시도. 첫 성공 뒤 V75.46의 토큰 가드가 나머지를 중단한다.
FOCUS_RESTORE_DELAYS_MS: tuple[int, ...] = (0, 50, 150, 300)
GA_ROOT = 2
SW_RESTORE = 9
ASFW_ANY = 0xFFFFFFFF


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


v7546 = _load_fixed(V7546_FILENAME, V7546_SHA256, "v7547_immutable_base")
v7545 = v7546.v7545
v7544 = v7546.v7544
v7543 = v7546.v7543
v7538 = v7546.v7538
v7530 = v7546.v7530
v7525 = v7546.v7525
KST = v7546.KST
CursorPosition = v7546.CursorPosition
diag = v7546.diag
_ORIGINAL_SELF_CHECK = v7546.self_check


def _widget_hwnd(widget: Any) -> int:
    try:
        return int(widget.winfo_id())
    except Exception:
        return 0


def _widget_root_hwnd(widget: Any) -> int:
    hwnd = _widget_hwnd(widget)
    if not v7538.IS_WINDOWS or not hwnd:
        return hwnd
    try:
        root_hwnd = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or 0)
    except (AttributeError, OSError):
        return hwnd
    return root_hwnd or hwnd


def restore_cursor_position(cursor: CursorPosition | None) -> bool:
    """마우스를 종목 전환 직전 좌표로 복원한다.

    전경창 복귀 성공과 분리한다. 따라서 영웅문 클릭 fallback 뒤 V75 전경화가
    일시 실패해도 물리 마우스는 영웅문 입력칸에 남지 않는다.
    """
    if cursor is None:
        return True
    if not v7538.IS_WINDOWS:
        return True
    try:
        return bool(
            ctypes.windll.user32.SetCursorPos(int(cursor.x), int(cursor.y))
        )
    except (AttributeError, OSError):
        return False


def is_review_window_foreground(root: Any) -> bool:
    if not v7538.IS_WINDOWS:
        return True
    hwnd = _widget_root_hwnd(root)
    if not hwnd:
        return False
    try:
        foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        foreground_root = int(
            ctypes.windll.user32.GetAncestor(foreground, GA_ROOT) or 0
        )
    except (AttributeError, OSError):
        return False
    return foreground == hwnd or foreground_root == hwnd


def restore_review_focus(
    root: Any,
    tree: Any,
    cursor: CursorPosition | None,
) -> bool:
    """V75 검토창과 Treeview 포커스를 복구하고 마우스 좌표를 반드시 되돌린다."""
    try:
        root.update_idletasks()
    except Exception:
        restore_cursor_position(cursor)
        return False

    if not v7538.IS_WINDOWS:
        try:
            tree.focus_set()
            return True
        except Exception:
            return False
        finally:
            restore_cursor_position(cursor)

    hwnd = _widget_root_hwnd(root)
    tree_hwnd = _widget_hwnd(tree)
    if not hwnd:
        restore_cursor_position(cursor)
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    attached: list[tuple[int, int]] = []
    foreground_ok = False
    try:
        if not user32.IsWindow(hwnd):
            return False

        # Tk 계층에서도 먼저 복귀를 요청한다.
        for method_name in ("deiconify", "lift"):
            try:
                getattr(root, method_name)()
            except Exception:
                pass

        current_thread = int(kernel32.GetCurrentThreadId() or 0)
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(foreground, None) or 0)
            if foreground
            else 0
        )
        try:
            user32.AllowSetForegroundWindow(ASFW_ANY)
        except OSError:
            pass

        for first, second in (
            (current_thread, target_thread),
            (foreground_thread, target_thread),
        ):
            if first and second and first != second:
                try:
                    if user32.AttachThreadInput(first, second, True):
                        attached.append((first, second))
                except OSError:
                    pass

        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        if tree_hwnd and user32.IsWindow(tree_hwnd):
            user32.SetFocus(tree_hwnd)

        try:
            tree.focus_force()
        except Exception:
            tree.focus_set()
        try:
            root.focus_force()
        except Exception:
            pass
        try:
            tree.focus_force()
        except Exception:
            tree.focus_set()

        foreground_ok = is_review_window_foreground(root)
        return foreground_ok
    except Exception as exc:
        diag(f"GUI_FOCUS_RESTORE_V7547_EXC {type(exc).__name__}: {exc}")
        return False
    finally:
        # 전경화 실패와 무관하게 마우스는 항상 원래 V75 목록 좌표로 되돌린다.
        cursor_ok = restore_cursor_position(cursor)
        diag(f"GUI_CURSOR_RESTORE ok={cursor_ok} cursor={cursor}")
        for first, second in reversed(attached):
            try:
                user32.AttachThreadInput(first, second, False)
            except OSError:
                pass


# V75.46 GUI 내부 함수는 호출 시 모듈 전역을 조회하므로 안전하게 좁은 패치가 반영된다.
v7546.restore_review_focus = restore_review_focus
v7546.is_review_window_foreground = is_review_window_foreground
v7546.FOCUS_RESTORE_DELAYS_MS = FOCUS_RESTORE_DELAYS_MS
v7525.VERSION = VERSION


def _run_v7547_tests() -> tuple[bool, str]:
    path = BASE_DIR / "test_v75_47_cursor_lock_arrow_verified.py"
    try:
        spec = importlib.util.spec_from_file_location("v7547_special_test", path)
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
    test_ok, message = _run_v7547_tests()
    results.extend(
        [
            (
                "V75.46기준SHA",
                sha256_file(BASE_DIR / V7546_FILENAME) == V7546_SHA256,
                V7546_SHA256[:16],
            ),
            ("V75.47GUI실사회귀", test_ok, message),
            (
                "즉시포커스복귀",
                v7546.FOCUS_RESTORE_DELAYS_MS[0] == 0,
                str(v7546.FOCUS_RESTORE_DELAYS_MS),
            ),
            (
                "마우스복원독립",
                v7546.restore_review_focus is restore_review_focus,
                "전경복귀 실패와 무관하게 SetCursorPos",
            ),
            (
                "화살표GUI유지",
                v7525.SimpleDoubleClickGui is v7546.V7546Gui,
                "V75.46 실제 콜백 유지",
            ),
            (
                "매매로직불변",
                v7525.V7525Engine is v7544.V7544Engine,
                "V75.44 엔진 유지",
            ),
        ]
    )
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7546.main())


if __name__ == "__main__":
    raise SystemExit(main())
