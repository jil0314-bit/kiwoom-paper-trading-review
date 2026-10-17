#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V75.28 PAPER
- V75.27 매매 로직은 그대로 유지한다.
- 더블클릭 시 종목코드를 클립보드에 복사하고 영웅문 입력칸을 클릭한 뒤,
  Windows PowerShell SendKeys로 Ctrl+A, Ctrl+V, Enter를 보낸다.
- 기존 V75.27 키입력 방식은 실패 시 보조수단으로만 사용한다.
- PAPER 전용이며 실제 주문 함수는 없다.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

VERSION = "V75.28-POWERSHELL-SENDKEYS-HERO-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_27_one_doubleclick_hero_paper.py"
BASE_SHA256 = "f4112f938b468a39b13c1bb4a1e5126a93a6facb9e0b21691bb9a22b997fe276"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_base() -> ModuleType:
    path = BASE_DIR / BASE_FILENAME
    actual = sha256_file(path)
    if actual.lower() != BASE_SHA256:
        raise RuntimeError(f"V75.27 SHA256 불일치\n기대: {BASE_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7527_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.27 모듈 로더 생성 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7527 = load_base()
v7526 = v7527.v7526
v7525 = v7527.v7525
v7524 = v7527.v7524
_ORIGINAL_SELF_CHECK = v7527.self_check


class PowerShellHeroLink(v7527.OneDoubleClickHeroLink):
    """수동 Ctrl+V와 같은 Windows SendKeys 경로를 사용한다."""

    @staticmethod
    def _window_pid(hwnd: int) -> int:
        pid = ctypes.c_ulong(0)
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value)

    @staticmethod
    def _powershell_sendkeys(pid: int) -> tuple[bool, str]:
        if pid <= 0:
            return False, "영웅문 PID 확인 실패"

        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$ws=New-Object -ComObject WScript.Shell;"
            f"$ok=$ws.AppActivate({pid});"
            "Start-Sleep -Milliseconds 300;"
            "[System.Windows.Forms.SendKeys]::SendWait('^a');"
            "Start-Sleep -Milliseconds 100;"
            "[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}');"
            "Start-Sleep -Milliseconds 100;"
            "[System.Windows.Forms.SendKeys]::SendWait('^v');"
            "Start-Sleep -Milliseconds 300;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            "Start-Sleep -Milliseconds 150;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            "if($ok){exit 0}else{exit 3}"
        )

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    script,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6,
                creationflags=creation_flags,
                check=False,
            )
        except Exception as exc:
            return False, f"PowerShell 실행 실패: {type(exc).__name__}"

        if result.returncode == 0:
            return True, "PowerShell SendKeys 전송"
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:120]
        return False, f"PowerShell SendKeys 실패 code={result.returncode} {detail}".strip()

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

        self._force_foreground(root)
        time.sleep(0.25)
        self._click(x, y)
        time.sleep(0.25)

        ok, detail = self._powershell_sendkeys(self._window_pid(root))
        if ok:
            return True, f"더블클릭 자동전환: {symbol} | {detail}"

        # PowerShell 경로가 실패할 때만 V75.27 방식을 한 번 실행한다.
        fallback_ok, fallback_detail = super().send_symbol(symbol)
        if fallback_ok:
            return True, f"보조방식 전송: {symbol} | {detail}"
        return False, f"자동전환 실패: {detail} / {fallback_detail}"


class PowerShellDoubleClickGui(v7527.OneDoubleClickGui):
    def _run(self) -> None:
        original = v7524.WindowsHeroRelativeLink
        try:
            v7524.WindowsHeroRelativeLink = PowerShellHeroLink
            super()._run()
        finally:
            v7524.WindowsHeroRelativeLink = original


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    source = Path(__file__).read_text(encoding="utf-8")
    results.extend([
        ("V75.27기준본SHA", sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
        ("PowerShellSendKeys", "System.Windows.Forms.SendKeys" in source, "Ctrl+A/Ctrl+V/Enter"),
        ("실패시보조전송", "super().send_symbol(symbol)" in source, "V75.27 fallback"),
    ])
    return results


v7525.VERSION = VERSION
v7525.SimpleDoubleClickGui = PowerShellDoubleClickGui
v7525.self_check = self_check


def main() -> int:
    return int(v7525.main())


if __name__ == "__main__":
    raise SystemExit(main())
