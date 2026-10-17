#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.26 PAPER
- V75.25 매매 로직은 그대로 유지한다.
- GUI 종목 더블클릭 시 영웅문 창을 강제로 전면 활성화한다.
- 저장한 종목코드 위치를 클릭한 뒤 Ctrl+A, Backspace, Ctrl+V, Enter를 SendInput으로 전달한다.
- 자동손절 없음, 목표 +1.5% 고정, 장중명령 유지.
- PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

VERSION = "V75.26-ROBUST-HERO-DOUBLECLICK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7525_FILENAME = "bot_v75_25_impulse_support_doubleclick_paper.py"
V7525_SHA256 = "65085fd8b54391a74ceef00a417d5abe5df39846607c860f6be8de543523927d"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_v7525() -> ModuleType:
    path = BASE_DIR / V7525_FILENAME
    if not path.exists():
        raise RuntimeError(f"{V7525_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != V7525_SHA256:
        raise RuntimeError(f"V75.25 SHA256 불일치\n기대: {V7525_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location('v7525_immutable_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('V75.25 모듈 로더 생성 실패')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7525 = load_v7525()
v7524 = v7525.v7524
_ORIGINAL_SELF_CHECK = v7525.self_check


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.c_ushort),
        ('wScan', ctypes.c_ushort),
        ('dwFlags', ctypes.c_ulong),
        ('time', ctypes.c_ulong),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('union', INPUT_UNION)]


class RobustHeroLink(v7524.WindowsHeroRelativeLink):
    """영웅문 활성화 제한을 우회하고 클립보드 붙여넣기로 종목코드를 전달한다."""

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_V = 0x56
    VK_BACK = 0x08
    VK_RETURN = 0x0D

    @staticmethod
    def _send_vk(vk: int, key_up: bool = False) -> None:
        flags = RobustHeroLink.KEYEVENTF_KEYUP if key_up else 0
        inp = INPUT(type=RobustHeroLink.INPUT_KEYBOARD)
        inp.union.ki = KEYBDINPUT(vk, 0, flags, 0, None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    @classmethod
    def _press(cls, vk: int) -> None:
        cls._send_vk(vk, False)
        cls._send_vk(vk, True)

    @classmethod
    def _hotkey(cls, modifier: int, key: int) -> None:
        cls._send_vk(modifier, False)
        cls._send_vk(key, False)
        cls._send_vk(key, True)
        cls._send_vk(modifier, True)

    @staticmethod
    def _force_foreground(hwnd: int) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        current = int(user32.GetForegroundWindow() or 0)
        current_tid = int(user32.GetWindowThreadProcessId(current, None) or 0) if current else 0
        target_tid = int(user32.GetWindowThreadProcessId(hwnd, None) or 0)
        this_tid = int(kernel32.GetCurrentThreadId() or 0)
        attached: list[tuple[int, int]] = []
        try:
            for a, b in ((this_tid, target_tid), (current_tid, target_tid)):
                if a and b and a != b and user32.AttachThreadInput(a, b, True):
                    attached.append((a, b))
            user32.ShowWindow(hwnd, 9)
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
            time.sleep(0.25)
            return int(user32.GetForegroundWindow() or 0) == int(hwnd)
        finally:
            for a, b in reversed(attached):
                user32.AttachThreadInput(a, b, False)

    @staticmethod
    def _click(x: int, y: int) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.06)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.12)

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not self.available:
            return False, 'Windows에서만 작동합니다.'
        if not self.configured:
            return False, "먼저 '영웅문 위치 저장'을 하세요."
        if not symbol.isdigit() or len(symbol) != 6:
            return False, f'종목코드 형식 오류: {symbol}'
        root = self._find_window()
        if not root:
            return False, '저장한 영웅문 창을 찾지 못했습니다.'
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, '영웅문 창 위치를 읽지 못했습니다.'
        left, top, width, height = geometry
        x = left + int(float(self.config['x_ratio']) * width)
        y = top + int(float(self.config['y_ratio']) * height)

        activated = self._force_foreground(root)
        self._click(x, y)
        self._hotkey(self.VK_CONTROL, self.VK_A)
        time.sleep(0.05)
        self._press(self.VK_BACK)
        time.sleep(0.05)
        self._hotkey(self.VK_CONTROL, self.VK_V)
        time.sleep(0.12)
        self._press(self.VK_RETURN)
        time.sleep(0.20)

        # 일부 영웅문 화면은 첫 Enter를 무시하므로 한 번 더 보낸다.
        self._press(self.VK_RETURN)
        return True, f"영웅문 전환 전송: {symbol}" + ('' if activated else ' (창 활성화 제한 가능)')


class RobustDoubleClickGui(v7525.SimpleDoubleClickGui):
    def _run(self) -> None:
        original = v7524.WindowsHeroRelativeLink
        try:
            v7524.WindowsHeroRelativeLink = RobustHeroLink
            super()._run()
        finally:
            v7524.WindowsHeroRelativeLink = original


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    results.extend([
        ('V75.25기준본SHA', sha256_file(BASE_DIR / V7525_FILENAME) == V7525_SHA256, V7525_SHA256[:16]),
        ('강제창활성화', hasattr(RobustHeroLink, '_force_foreground'), 'AttachThreadInput/SetForegroundWindow'),
        ('클립보드입력', hasattr(RobustHeroLink, 'send_symbol'), 'Ctrl+A/Backspace/Ctrl+V/Enter'),
    ])
    return results


# V75.25 main이 새 GUI를 사용하도록 교체한다.
v7525.VERSION = VERSION
v7525.SimpleDoubleClickGui = RobustDoubleClickGui
v7525.self_check = self_check


def main() -> int:
    return int(v7525.main())


if __name__ == '__main__':
    raise SystemExit(main())
