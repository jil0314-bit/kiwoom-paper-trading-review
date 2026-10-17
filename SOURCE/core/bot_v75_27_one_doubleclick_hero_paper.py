#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.27 PAPER
- V75.26의 매매 로직은 그대로 유지한다.
- 영웅문 위치는 사용자 홈의 기존 설정을 재사용한다.
- GUI 종목 줄 더블클릭 한 번으로 클립보드 설정, 영웅문 활성화,
  입력칸 클릭, 붙여넣기, Enter까지 자동 수행한다.
- 키 입력은 느린 간격과 재시도로 전달한다.
- PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

VERSION = "V75.27-ONE-DOUBLECLICK-HERO-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_26_robust_hero_doubleclick_paper.py"
BASE_SHA256 = "1ec7baa7395da6c7c9fa61c32a1f81176545c086775dca4af5d2e9d8e3f4e4c3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_base() -> ModuleType:
    path = BASE_DIR / BASE_FILENAME
    if not path.exists():
        raise RuntimeError(f"{BASE_FILENAME}이 같은 폴더에 없습니다.")
    actual = sha256_file(path)
    if actual.lower() != BASE_SHA256:
        raise RuntimeError(f"V75.26 SHA256 불일치\n기대: {BASE_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7526_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.26 모듈 로더 생성 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7526 = load_base()
v7525 = v7526.v7525
v7524 = v7526.v7524
_ORIGINAL_SELF_CHECK = v7526.self_check


class OneDoubleClickHeroLink(v7526.RobustHeroLink):
    """수동 Ctrl+V 없이 더블클릭 한 번으로 종목코드를 전달한다."""

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    KEYEVENTF_KEYUP = 0x0002

    @staticmethod
    def _set_windows_clipboard(text: str) -> bool:
        if not text:
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        data = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(OneDoubleClickHeroLink.GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)

        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(OneDoubleClickHeroLink.CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            handle = 0  # Windows가 메모리 소유권을 가져감
            return True
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    @classmethod
    def _legacy_key(cls, vk: int, down: bool) -> None:
        flags = 0 if down else cls.KEYEVENTF_KEYUP
        ctypes.windll.user32.keybd_event(vk, 0, flags, 0)

    @classmethod
    def _legacy_press(cls, vk: int, hold: float = 0.04) -> None:
        cls._legacy_key(vk, True)
        time.sleep(hold)
        cls._legacy_key(vk, False)

    @classmethod
    def _legacy_hotkey(cls, modifier: int, key: int) -> None:
        cls._legacy_key(modifier, True)
        time.sleep(0.05)
        cls._legacy_key(key, True)
        time.sleep(0.06)
        cls._legacy_key(key, False)
        time.sleep(0.05)
        cls._legacy_key(modifier, False)

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not self.available:
            return False, "Windows에서만 작동합니다."
        if not self.configured:
            return False, "처음 한 번만 영웅문 위치 저장이 필요합니다."
        if not symbol.isdigit() or len(symbol) != 6:
            return False, f"종목코드 형식 오류: {symbol}"

        root = self._find_window()
        if not root:
            return False, "저장한 영웅문 창을 찾지 못했습니다."
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, "영웅문 창 위치를 읽지 못했습니다."

        if not self._set_windows_clipboard(symbol):
            return False, f"클립보드 저장 실패: {symbol}"

        left, top, width, height = geometry
        x = left + int(float(self.config["x_ratio"]) * width)
        y = top + int(float(self.config["y_ratio"]) * height)

        activated = self._force_foreground(root)
        time.sleep(0.35)
        self._click(x, y)
        time.sleep(0.35)

        # 수동 Ctrl+V가 성공했던 동일 경로를 느린 간격으로 자동 실행한다.
        for attempt in range(2):
            self._legacy_hotkey(self.VK_CONTROL, self.VK_A)
            time.sleep(0.12)
            self._legacy_press(self.VK_BACK)
            time.sleep(0.15)
            self._legacy_hotkey(self.VK_CONTROL, self.VK_V)
            time.sleep(0.30)
            self._legacy_press(self.VK_RETURN)
            time.sleep(0.45)
            if attempt == 0:
                # 일부 화면은 첫 입력을 놓치므로 입력칸을 다시 클릭하고 한 번 재시도한다.
                self._click(x, y)
                time.sleep(0.25)

        return True, f"더블클릭 자동전환: {symbol}" + ("" if activated else " (창 활성화 제한 가능)")


class OneDoubleClickGui(v7526.RobustDoubleClickGui):
    def _run(self) -> None:
        original = v7524.WindowsHeroRelativeLink
        try:
            v7524.WindowsHeroRelativeLink = OneDoubleClickHeroLink
            super()._run()
        finally:
            v7524.WindowsHeroRelativeLink = original


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    source = Path(__file__).read_text(encoding="utf-8")
    results.extend([
        ("V75.26기준본SHA", sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
        ("더블클릭자동붙여넣기", "_legacy_hotkey(self.VK_CONTROL, self.VK_V)" in source, "Ctrl+V 자동"),
        ("느린입력재시도", "for attempt in range(2)" in source, "2회 시도"),
        ("기존위치설정재사용", ".v75_hero_input_position.json" in Path(BASE_DIR / "bot_v75_25_impulse_support_doubleclick_paper.py").read_text(encoding="utf-8"), "사용자 홈 저장"),
    ])
    return results


v7525.VERSION = VERSION
v7525.SimpleDoubleClickGui = OneDoubleClickGui
v7525.self_check = self_check


def main() -> int:
    return int(v7525.main())


if __name__ == "__main__":
    raise SystemExit(main())
