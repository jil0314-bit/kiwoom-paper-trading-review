#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.36 PAPER — 영웅문 이중 재검증 연동

매매 계층은 검증된 V75.30을 SHA256으로 그대로 불러온다.
이 파일은 GUI 더블클릭/클립보드/영웅문 입력 연동만 교체한다.

입력 순서
1. Tk/Win32 클립보드에 종목코드를 먼저 저장하고 다시 읽어 확인한다.
2. 영웅문 창을 활성화하고 저장 위치를 클릭한 뒤 실제 포커스를 확인한다.
3. 수동으로 성공했던 경로와 동일하게 Ctrl+A → Backspace → Ctrl+V → Enter.
4. 읽을 수 있는 컨트롤이면 WM_GETTEXT 또는 UI Automation으로 실제 입력값을 확인한다.
5. 실패하면 UI Automation ValuePattern, WM_SETTEXT, WM_PASTE, 실제 숫자 VK 입력 순으로 재시도한다.

KEYEVENTF_UNICODE는 영웅문 커스텀 컨트롤 호환성이 불확실하여 사용하지 않는다.
PAPER 전용이며 실주문 함수는 정의하지 않는다.
"""
from __future__ import annotations

import ast
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any

VERSION = "V75.36-DOUBLE-REVALIDATED-HERO-LINK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
BASE_FILENAME = "bot_v75_30_reserved_target_strict_entry_paper.py"
BASE_SHA256 = "06938a69ccd49b5e6bb33bafcfc97de6b449ed0ee5d315f787ea21ab8ccd8a54"
PACKAGE_DIR = BASE_DIR.parent
DIAG_FILE = PACKAGE_DIR / "V75_36_LINK_DIAG.txt"
IS_WINDOWS = os.name == "nt" and hasattr(ctypes, "WinDLL")
CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


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
        raise RuntimeError(f"V75.30 SHA256 불일치\n기대: {BASE_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7530_immutable_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.30 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v7530 = load_base()
v7529 = v7530.v7529
v7525 = v7530.v7525
BaseHeroLink = v7529.PowerShellHeroLink
BaseGui = v7525.SimpleDoubleClickGui
_ORIGINAL_SELF_CHECK = v7530.self_check


def _window_title_matches(saved: str, current: str) -> bool:
    saved_norm = " ".join(str(saved or "").split()).lower()
    current_norm = " ".join(str(current or "").split()).lower()
    return bool(saved_norm and current_norm) and (
        saved_norm == current_norm
        or saved_norm in current_norm
        or current_norm in saved_norm
    )


def diag(message: str) -> None:
    try:
        with DIAG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S.%f}] {message}\n")
    except OSError:
        pass


ULONG_PTR = ctypes.c_size_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG), ("top", wintypes.LONG),
        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_CONTROL = 0x11
VK_A = 0x41
VK_V = 0x56
VK_BACK = 0x08
VK_RETURN = 0x0D
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
GA_ROOT = 2
SW_RESTORE = 9
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_PASTE = 0x0302
EM_SETSEL = 0x00B1
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
MAPVK_VK_TO_VSC = 0
ASFW_ANY = 0xFFFFFFFF

if IS_WINDOWS:
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _k32.GlobalAlloc.restype = ctypes.c_void_p
    _k32.GlobalLock.argtypes = (ctypes.c_void_p,)
    _k32.GlobalLock.restype = ctypes.c_void_p
    _k32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    _k32.GlobalUnlock.restype = wintypes.BOOL
    _k32.GlobalFree.argtypes = (ctypes.c_void_p,)
    _k32.GlobalFree.restype = ctypes.c_void_p
    _k32.GetCurrentThreadId.argtypes = ()
    _k32.GetCurrentThreadId.restype = wintypes.DWORD
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    _u32.OpenClipboard.argtypes = (wintypes.HWND,)
    _u32.OpenClipboard.restype = wintypes.BOOL
    _u32.CloseClipboard.argtypes = ()
    _u32.CloseClipboard.restype = wintypes.BOOL
    _u32.EmptyClipboard.argtypes = ()
    _u32.EmptyClipboard.restype = wintypes.BOOL
    _u32.SetClipboardData.argtypes = (wintypes.UINT, ctypes.c_void_p)
    _u32.SetClipboardData.restype = ctypes.c_void_p
    _u32.GetClipboardData.argtypes = (wintypes.UINT,)
    _u32.GetClipboardData.restype = ctypes.c_void_p

    _u32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _u32.SendInput.restype = wintypes.UINT
    _u32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _u32.MapVirtualKeyW.restype = wintypes.UINT
    _u32.GetForegroundWindow.argtypes = ()
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    _u32.GetAncestor.restype = wintypes.HWND
    _u32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _u32.GetWindowTextLengthW.restype = ctypes.c_int
    _u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u32.GetWindowTextW.restype = ctypes.c_int
    _u32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u32.GetClassNameW.restype = ctypes.c_int
    _u32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
    _u32.GetGUIThreadInfo.restype = wintypes.BOOL
    _u32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    _u32.GetClientRect.restype = wintypes.BOOL
    _u32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    _u32.GetWindowRect.restype = wintypes.BOOL
    _u32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(POINT))
    _u32.ClientToScreen.restype = wintypes.BOOL
    _u32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
    _u32.GetCursorPos.restype = wintypes.BOOL
    _u32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    _u32.SetCursorPos.restype = wintypes.BOOL
    _u32.WindowFromPoint.argtypes = (POINT,)
    _u32.WindowFromPoint.restype = wintypes.HWND
    _u32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _u32.IsWindowVisible.restype = wintypes.BOOL
    _u32.IsWindow.argtypes = (wintypes.HWND,)
    _u32.IsWindow.restype = wintypes.BOOL
    _u32.GetDlgCtrlID.argtypes = (wintypes.HWND,)
    _u32.GetDlgCtrlID.restype = ctypes.c_int

    ENUMWINDOWSPROC = CALLBACK_FACTORY(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumWindows.argtypes = (ENUMWINDOWSPROC, wintypes.LPARAM)
    _u32.EnumWindows.restype = wintypes.BOOL
    _u32.EnumChildWindows.argtypes = (wintypes.HWND, ENUMWINDOWSPROC, wintypes.LPARAM)
    _u32.EnumChildWindows.restype = wintypes.BOOL

    _u32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    _u32.AttachThreadInput.restype = wintypes.BOOL
    _u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _u32.ShowWindow.restype = wintypes.BOOL
    _u32.BringWindowToTop.argtypes = (wintypes.HWND,)
    _u32.BringWindowToTop.restype = wintypes.BOOL
    _u32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _u32.SetForegroundWindow.restype = wintypes.BOOL
    _u32.SetActiveWindow.argtypes = (wintypes.HWND,)
    _u32.SetActiveWindow.restype = wintypes.HWND
    _u32.SetFocus.argtypes = (wintypes.HWND,)
    _u32.SetFocus.restype = wintypes.HWND
    _u32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
    _u32.AllowSetForegroundWindow.restype = wintypes.BOOL

    _u32.SendMessageTimeoutW.argtypes = (
        wintypes.HWND, wintypes.UINT, WPARAM, LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR),
    )
    _u32.SendMessageTimeoutW.restype = LRESULT
else:
    _u32 = None
    _k32 = None
    ENUMWINDOWSPROC = CALLBACK_FACTORY(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass(frozen=True)
class MethodResult:
    method: str
    sent: bool
    verified: bool
    detail: str
    observed: str = ""


UIA_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$result = [ordered]@{
  ok=$false; matched=$false; method=''; value='';
  class=''; name=''; automation_id=''; control_type=''; error=''
}
try {
  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  Add-Type -AssemblyName WindowsBase
  $x = [double]$env:V75_X
  $y = [double]$env:V75_Y
  $symbol = [string]$env:V75_SYMBOL
  $mode = [string]$env:V75_MODE
  $point = New-Object System.Windows.Point($x, $y)
  $element = [System.Windows.Automation.AutomationElement]::FromPoint($point)
  $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
  for ($i=0; $i -lt 10 -and $null -ne $element; $i++) {
    $result.class = [string]$element.Current.ClassName
    $result.name = [string]$element.Current.Name
    $result.automation_id = [string]$element.Current.AutomationId
    $result.control_type = [string]$element.Current.ControlType.ProgrammaticName

    $pattern = $null
    if ($element.TryGetCurrentPattern(
          [System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
      $vp = [System.Windows.Automation.ValuePattern]$pattern
      if ($mode -eq 'set' -and -not $vp.Current.IsReadOnly) {
        try { $element.SetFocus() } catch {}
        $vp.SetValue($symbol)
        Start-Sleep -Milliseconds 120
      }
      $result.value = [string]$vp.Current.Value
      $result.method = 'UIA.ValuePattern'
      $result.ok = $true
      $result.matched = ($result.value -eq $symbol)
      break
    }

    $legacyPattern = $null
    if ($element.TryGetCurrentPattern(
          [System.Windows.Automation.LegacyIAccessiblePattern]::Pattern,
          [ref]$legacyPattern)) {
      $legacy = [System.Windows.Automation.LegacyIAccessiblePattern]$legacyPattern
      if ($mode -eq 'set') {
        try { $element.SetFocus() } catch {}
        try { $legacy.SetValue($symbol) } catch {}
        Start-Sleep -Milliseconds 120
      }
      $result.value = [string]$legacy.Current.Value
      $result.method = 'UIA.LegacyIAccessible'
      $result.ok = $true
      $result.matched = ($result.value -eq $symbol)
      if ($result.matched -or $mode -eq 'read') { break }
    }
    $element = $walker.GetParent($element)
  }
} catch {
  $result.error = $_.Exception.GetType().Name + ': ' + $_.Exception.Message
}
$result | ConvertTo-Json -Compress
'''


class VerifiedMultiHeroLink(BaseHeroLink):
    """클립보드·포커스·전송·가능한 read-back을 단계별로 확인한다."""

    @staticmethod
    def _window_text(hwnd: int) -> str:
        if not IS_WINDOWS or not hwnd:
            return ""
        length = int(_u32.GetWindowTextLengthW(wintypes.HWND(hwnd)) or 0)
        buffer = ctypes.create_unicode_buffer(max(2, length + 1))
        _u32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
        return buffer.value.strip()

    @staticmethod
    def _class_name(hwnd: int) -> str:
        if not IS_WINDOWS or not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
        return buffer.value

    @staticmethod
    def _pid_of(hwnd: int) -> int:
        if not IS_WINDOWS or not hwnd:
            return 0
        pid = wintypes.DWORD(0)
        _u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    @staticmethod
    def _thread_of(hwnd: int) -> int:
        if not IS_WINDOWS or not hwnd:
            return 0
        return int(_u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None) or 0)

    @staticmethod
    def _process_name(pid: int) -> str:
        if not IS_WINDOWS or pid <= 0:
            return ""
        handle = _k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if _k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name
            return ""
        finally:
            _k32.CloseHandle(handle)

    @staticmethod
    def _root_of(hwnd: int) -> int:
        if not IS_WINDOWS or not hwnd:
            return 0
        return int(_u32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or hwnd)

    @classmethod
    def _foreground(cls) -> int:
        if not IS_WINDOWS:
            return 0
        return int(_u32.GetForegroundWindow() or 0)

    @classmethod
    def _focus_hwnd(cls, root: int) -> int:
        thread = cls._thread_of(root)
        if not thread:
            return 0
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not _u32.GetGUIThreadInfo(wintypes.DWORD(thread), ctypes.byref(info)):
            return 0
        return int(info.hwndFocus or 0)

    @staticmethod
    def _client_geometry(hwnd: int) -> tuple[int, int, int, int] | None:
        if not IS_WINDOWS or not hwnd:
            return None
        rect = RECT()
        if not _u32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        origin = POINT(0, 0)
        if not _u32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return None
        return int(origin.x), int(origin.y), width, height

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        if not IS_WINDOWS or not hwnd:
            return None
        rect = RECT()
        if not _u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    @staticmethod
    def _clipboard_write(text: str) -> bool:
        if not IS_WINDOWS or not text:
            return False
        data = (text + "\0").encode("utf-16-le")
        handle = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        pointer = _k32.GlobalLock(handle)
        if not pointer:
            _k32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            _k32.GlobalUnlock(handle)

        opened = False
        for _ in range(20):
            if _u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            _k32.GlobalFree(handle)
            return False
        transferred = False
        try:
            if not _u32.EmptyClipboard():
                return False
            if not _u32.SetClipboardData(CF_UNICODETEXT, handle):
                return False
            transferred = True
            return True
        finally:
            _u32.CloseClipboard()
            if not transferred:
                _k32.GlobalFree(handle)

    @staticmethod
    def _clipboard_read() -> str:
        if not IS_WINDOWS:
            return ""
        opened = False
        for _ in range(20):
            if _u32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            return ""
        try:
            handle = _u32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = _k32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                _k32.GlobalUnlock(handle)
        except (OSError, ValueError):
            return ""
        finally:
            _u32.CloseClipboard()

    def _find_window(self) -> int:
        if not IS_WINDOWS or not self.configured:
            return 0
        saved_title = str(self.config.get("window_title", ""))
        saved_process = str(self.config.get("process_name", ""))
        saved_class = str(self.config.get("window_class", ""))
        strong_hits: list[int] = []
        identity_hits: list[int] = []
        title_hits: list[int] = []

        @ENUMWINDOWSPROC
        def callback(hwnd: int, _lparam: int) -> bool:
            h = int(hwnd)
            if not _u32.IsWindowVisible(wintypes.HWND(h)):
                return True
            title = self._window_text(h)
            if not title:
                return True
            pid = self._pid_of(h)
            process = self._process_name(pid)
            window_class = self._class_name(h)
            title_match = _window_title_matches(saved_title, title)
            identity_match = bool(
                saved_process and saved_class
                and process.lower() == saved_process.lower()
                and window_class == saved_class
            )
            if title_match and identity_match:
                strong_hits.append(h)
            elif identity_match:
                identity_hits.append(h)
            elif title_match and pid != os.getpid():
                # 구버전 제목 검색에서도 현재 Python GUI 자신은 제외한다.
                title_hits.append(h)
            return True

        _u32.EnumWindows(callback, 0)
        if strong_hits:
            return strong_hits[0]
        if identity_hits:
            diag(f"WINDOW_FALLBACK process={saved_process} class={saved_class}")
            return identity_hits[0]
        # 새 설정에 프로세스명과 창 클래스가 있는데 일치하지 않으면
        # 제목만 비슷한 다른 창(이 GUI 포함)을 절대 선택하지 않는다.
        if saved_process and saved_class:
            if title_hits:
                diag(
                    f"WINDOW_REJECT_TITLE_ONLY saved_title={saved_title!r} "
                    f"process={saved_process!r} class={saved_class!r}"
                )
            return 0
        # 구버전 위치설정에는 프로세스/클래스가 없으므로 그때만 제목으로 찾는다.
        if title_hits:
            diag(f"WINDOW_LEGACY_TITLE_ONLY saved_title={saved_title!r}")
            return title_hits[0]
        return 0

    def _find_target(self, root: int, x: int, y: int) -> int:
        point = POINT(int(x), int(y))
        hit = int(_u32.WindowFromPoint(point) or 0)
        if hit and hit != root and self._root_of(hit) == root:
            saved_class = str(self.config.get("target_class", ""))
            if not saved_class or self._class_name(hit) == saved_class:
                return hit

        saved_class = str(self.config.get("target_class", ""))
        saved_id = int(self.config.get("target_control_id", 0) or 0)
        candidates: list[tuple[float, int]] = []
        if saved_class:
            @ENUMWINDOWSPROC
            def callback(hwnd: int, _lparam: int) -> bool:
                h = int(hwnd)
                if self._class_name(h) != saved_class:
                    return True
                if saved_id and int(_u32.GetDlgCtrlID(wintypes.HWND(h)) or 0) not in (0, saved_id):
                    return True
                rect = self._window_rect(h)
                if rect is None:
                    return True
                left, top, right, bottom = rect
                cx = (left + right) / 2.0
                cy = (top + bottom) / 2.0
                candidates.append(((cx - x) ** 2 + (cy - y) ** 2, h))
                return True
            _u32.EnumChildWindows(wintypes.HWND(root), callback, 0)
        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]
        # 최상위 창을 입력 컨트롤로 반환하면 WM_SETTEXT가 영웅문 창 제목을
        # 종목코드로 바꾸고 성공으로 오판할 수 있다. 자식 컨트롤이 없으면 0.
        if hit and hit != root and self._root_of(hit) == root:
            return hit
        return 0

    def capture_cursor_target(self) -> tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows에서만 설정할 수 있습니다."
        foreground = self._foreground()
        if not foreground:
            return False, "현재 활성 창을 찾지 못했습니다."
        root = self._root_of(foreground)
        title = self._window_text(root)
        if not title:
            return False, "영웅문 창 제목을 읽지 못했습니다."
        cursor = POINT()
        if not _u32.GetCursorPos(ctypes.byref(cursor)):
            return False, "마우스 위치를 읽지 못했습니다."
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, "영웅문 창 크기를 읽지 못했습니다."
        left, top, width, height = geometry
        rel_x = int(cursor.x) - left
        rel_y = int(cursor.y) - top
        if not (0 <= rel_x < width and 0 <= rel_y < height):
            return False, "마우스가 영웅문 창 안에 있지 않습니다."
        target = int(_u32.WindowFromPoint(POINT(int(cursor.x), int(cursor.y))) or 0)
        if target and self._root_of(target) != root:
            target = 0
        pid = self._pid_of(root)
        process_name = self._process_name(pid)
        if pid == os.getpid() or process_name.lower() in {"python.exe", "pythonw.exe", "py.exe"}:
            diag(
                f"CAPTURE_REJECT_SELF hwnd={root} pid={pid} process={process_name!r} "
                f"title={title!r}"
            )
            return False, "현재 프로그램 창이 선택되었습니다. 영웅문 종목코드 입력칸을 클릭하세요."
        self.config = {
            "window_title": title,
            "window_class": self._class_name(root),
            "process_name": process_name,
            "target_class": self._class_name(target),
            "target_control_id": int(_u32.GetDlgCtrlID(wintypes.HWND(target)) or 0) if target else 0,
            "x_ratio": max(0.0, min(1.0, rel_x / width)),
            "y_ratio": max(0.0, min(1.0, rel_y / height)),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()
        diag(
            "CAPTURE "
            f"title={title[:60]!r} process={self.config['process_name']!r} "
            f"root_class={self.config['window_class']!r} target_class={self.config['target_class']!r} "
            f"control_id={self.config['target_control_id']} ratio=({self.config['x_ratio']:.5f},"
            f"{self.config['y_ratio']:.5f})"
        )
        return True, "영웅문 종목코드 입력 위치 저장 완료"

    @staticmethod
    def _send_inputs(items: list[INPUT]) -> tuple[bool, int, int]:
        if not IS_WINDOWS or not items:
            return False, 0, 0
        array = (INPUT * len(items))(*items)
        ctypes.set_last_error(0)
        inserted = int(_u32.SendInput(len(items), array, ctypes.sizeof(INPUT)))
        error = int(ctypes.get_last_error())
        return inserted == len(items), inserted, error

    @staticmethod
    def _key_input(vk: int, up: bool = False, *, scan_code: bool = False) -> INPUT:
        item = INPUT(type=INPUT_KEYBOARD)
        if scan_code:
            scan = int(_u32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC) or 0)
            item.ki = KEYBDINPUT(0, scan, KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
        else:
            item.ki = KEYBDINPUT(int(vk), 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
        return item

    @classmethod
    def _press(cls, vk: int, *, scan_code: bool = False, hold: float = 0.045) -> bool:
        ok1, _, _ = cls._send_inputs([cls._key_input(vk, False, scan_code=scan_code)])
        time.sleep(hold)
        ok2, _, _ = cls._send_inputs([cls._key_input(vk, True, scan_code=scan_code)])
        return ok1 and ok2

    @classmethod
    def _hotkey(cls, modifier: int, key: int) -> bool:
        ok1, _, _ = cls._send_inputs([cls._key_input(modifier, False)])
        time.sleep(0.045)
        ok2, _, _ = cls._send_inputs([cls._key_input(key, False)])
        time.sleep(0.055)
        ok3, _, _ = cls._send_inputs([cls._key_input(key, True)])
        time.sleep(0.045)
        ok4, _, _ = cls._send_inputs([cls._key_input(modifier, True)])
        return ok1 and ok2 and ok3 and ok4

    @classmethod
    def _clear_with_keys(cls) -> bool:
        ok = cls._hotkey(VK_CONTROL, VK_A)
        time.sleep(0.10)
        return cls._press(VK_BACK) and ok

    @classmethod
    def _paste_with_keys(cls) -> bool:
        ok = cls._clear_with_keys()
        time.sleep(0.10)
        ok = cls._hotkey(VK_CONTROL, VK_V) and ok
        time.sleep(0.25)
        return ok

    @classmethod
    def _digits_with_keys(cls, symbol: str, *, scan_code: bool) -> bool:
        ok = cls._clear_with_keys()
        for char in symbol:
            time.sleep(0.045)
            ok = cls._press(ord(char), scan_code=scan_code, hold=0.035) and ok
        time.sleep(0.18)
        return ok

    @classmethod
    def _click(cls, x: int, y: int) -> bool:
        if not IS_WINDOWS:
            return False
        if not _u32.SetCursorPos(int(x), int(y)):
            return False
        time.sleep(0.08)
        down = INPUT(type=INPUT_MOUSE)
        down.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
        up = INPUT(type=INPUT_MOUSE)
        up.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
        ok, _, _ = cls._send_inputs([down, up])
        time.sleep(0.18)
        return ok

    def _activate_and_focus(self, root: int, target: int, x: int, y: int) -> tuple[bool, int, bool]:
        target_thread = self._thread_of(root)
        foreground = self._foreground()
        foreground_thread = self._thread_of(foreground) if foreground else 0
        this_thread = int(_k32.GetCurrentThreadId() or 0)
        attached: list[tuple[int, int]] = []
        try:
            try:
                _u32.AllowSetForegroundWindow(ASFW_ANY)
            except OSError:
                pass
            for first, second in ((this_thread, target_thread), (foreground_thread, target_thread)):
                if first and second and first != second:
                    if _u32.AttachThreadInput(first, second, True):
                        attached.append((first, second))
            _u32.ShowWindow(wintypes.HWND(root), SW_RESTORE)
            _u32.BringWindowToTop(wintypes.HWND(root))
            _u32.SetForegroundWindow(wintypes.HWND(root))
            _u32.SetActiveWindow(wintypes.HWND(root))
            time.sleep(0.20)
            clicked = self._click(x, y)
            if target and _u32.IsWindow(wintypes.HWND(target)):
                _u32.SetFocus(wintypes.HWND(target))
            time.sleep(0.18)
            current_root = self._foreground()
            focus = self._focus_hwnd(root)
            foreground_ok = current_root == root
            focus_ok = bool(focus and self._root_of(focus) == root)
            return foreground_ok, focus, clicked and (focus_ok or foreground_ok)
        finally:
            for first, second in reversed(attached):
                _u32.AttachThreadInput(first, second, False)

    @staticmethod
    def _send_message(hwnd: int, message: int, wparam: int = 0, lparam: int = 0, timeout_ms: int = 600) -> tuple[bool, int, int]:
        if not IS_WINDOWS or not hwnd:
            return False, 0, 0
        result = ULONG_PTR(0)
        ctypes.set_last_error(0)
        returned = _u32.SendMessageTimeoutW(
            wintypes.HWND(hwnd), wintypes.UINT(message), WPARAM(wparam), LPARAM(lparam),
            SMTO_BLOCK | SMTO_ABORTIFHUNG, int(timeout_ms), ctypes.byref(result),
        )
        return bool(returned), int(result.value), int(ctypes.get_last_error())

    @classmethod
    def _read_control_text(cls, hwnd: int) -> str:
        ok_len, length, _ = cls._send_message(hwnd, WM_GETTEXTLENGTH)
        size = max(32, min(512, int(length) + 2 if ok_len else 64))
        buffer = ctypes.create_unicode_buffer(size)
        ok, _, _ = cls._send_message(
            hwnd, WM_GETTEXT, size, ctypes.cast(buffer, ctypes.c_void_p).value or 0,
        )
        return buffer.value.strip() if ok else ""

    @classmethod
    def _set_text_message(cls, hwnd: int, symbol: str) -> MethodResult:
        buffer = ctypes.create_unicode_buffer(symbol)
        ok, result, error = cls._send_message(
            hwnd, WM_SETTEXT, 0, ctypes.cast(buffer, ctypes.c_void_p).value or 0,
        )
        time.sleep(0.12)
        observed = cls._read_control_text(hwnd)
        verified = observed == symbol
        return MethodResult("WM_SETTEXT", ok, verified, f"return={result} err={error}", observed)

    @classmethod
    def _paste_message(cls, hwnd: int, symbol: str) -> MethodResult:
        select_ok, _, select_err = cls._send_message(hwnd, EM_SETSEL, 0, -1)
        paste_ok, _, paste_err = cls._send_message(hwnd, WM_PASTE)
        time.sleep(0.15)
        observed = cls._read_control_text(hwnd)
        verified = observed == symbol
        return MethodResult(
            "WM_PASTE", select_ok and paste_ok, verified,
            f"select_err={select_err} paste_err={paste_err}", observed,
        )

    @classmethod
    def _char_message(cls, hwnd: int, symbol: str) -> MethodResult:
        cls._send_message(hwnd, EM_SETSEL, 0, -1)
        cls._send_message(hwnd, WM_CHAR, VK_BACK, 1)
        sent = True
        details: list[str] = []
        for char in symbol:
            ok, _, error = cls._send_message(hwnd, WM_CHAR, ord(char), 1)
            sent = sent and ok
            if error:
                details.append(str(error))
        time.sleep(0.15)
        observed = cls._read_control_text(hwnd)
        verified = observed == symbol
        return MethodResult("WM_CHAR", sent, verified, f"errors={','.join(details) or '0'}", observed)

    @staticmethod
    def _run_uia(x: int, y: int, symbol: str, mode: str) -> dict[str, Any]:
        if not IS_WINDOWS:
            return {"ok": False, "matched": False, "error": "not_windows"}
        encoded = base64.b64encode(UIA_SCRIPT.encode("utf-16-le")).decode("ascii")
        environment = os.environ.copy()
        environment.update({
            "V75_X": str(int(x)), "V75_Y": str(int(y)),
            "V75_SYMBOL": symbol, "V75_MODE": mode,
        })
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                timeout=5, creationflags=creation_flags, check=False, env=environment,
            )
        except Exception as exc:
            return {"ok": False, "matched": False, "error": f"{type(exc).__name__}: {exc}"}
        output = (completed.stdout or "").strip().splitlines()
        payload = next((line for line in reversed(output) if line.lstrip().startswith("{")), "")
        if not payload:
            error = (completed.stderr or completed.stdout or "").strip().replace("\n", " ")[:240]
            return {"ok": False, "matched": False, "error": f"code={completed.returncode} {error}"}
        try:
            result = json.loads(payload)
        except json.JSONDecodeError as exc:
            return {"ok": False, "matched": False, "error": f"JSONDecodeError: {exc}"}
        result["returncode"] = completed.returncode
        return result

    @classmethod
    def _message_target_safe(cls, target: int, root: int) -> bool:
        """WM_SETTEXT/WM_PASTE를 보내도 되는 실제 입력 자식창인지 확인한다."""
        if not target or target == root or cls._root_of(target) != root:
            return False
        class_name = cls._class_name(target).lower()
        return any(token in class_name for token in ("edit", "textbox", "richedit"))

    @classmethod
    def _verify_value(cls, target: int, root: int, x: int, y: int, symbol: str) -> tuple[bool, str, str]:
        win_text = cls._read_control_text(target) if cls._message_target_safe(target, root) else ""
        if win_text == symbol:
            return True, "WM_GETTEXT", win_text
        uia = cls._run_uia(x, y, symbol, "read")
        observed = str(uia.get("value", ""))
        if bool(uia.get("matched")) and observed == symbol:
            return True, str(uia.get("method", "UIA")), observed
        return False, f"WM={win_text!r} UIA={uia.get('method','')} err={uia.get('error','')}", observed or win_text

    def send_symbol(self, symbol: str) -> tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows에서만 작동합니다."
        if not symbol.isdigit() or len(symbol) != 6:
            diag(f"REJECT symbol={symbol!r}")
            return False, f"종목코드 형식 오류: {symbol}"
        if not self.configured:
            return False, "먼저 영웅문 위치 저장을 한 번 하세요."

        # GUI의 Tk 복사가 이미 성공했을 수 있다. 실제 read-back 값이 맞으면 성공으로 인정한다.
        clipboard_before = self._clipboard_read()
        winapi_written = False
        if clipboard_before != symbol:
            winapi_written = self._clipboard_write(symbol)
        clipboard_after = self._clipboard_read()
        clipboard_ok = clipboard_after == symbol
        if not clipboard_ok:
            diag(
                f"FAIL_CLIPBOARD symbol={symbol} before={clipboard_before!r} "
                f"written={winapi_written} after={clipboard_after!r}"
            )
            return False, "종목코드 클립보드 저장 실패"

        root = self._find_window()
        if not root:
            diag(f"FAIL_WINDOW symbol={symbol} clipboard_ok=1")
            return False, f"{symbol} 복사됨 — 저장한 영웅문 창을 찾지 못했습니다."
        geometry = self._client_geometry(root)
        if geometry is None:
            return False, f"{symbol} 복사됨 — 영웅문 창 위치를 읽지 못했습니다."
        left, top, width, height = geometry
        x = left + int(float(self.config["x_ratio"]) * width)
        y = top + int(float(self.config["y_ratio"]) * height)
        target = self._find_target(root, x, y)

        foreground_ok, focus, click_focus_ok = self._activate_and_focus(root, target, x, y)
        focus_root = self._root_of(focus)
        # WindowFromPoint가 최상위 창만 돌려줘도 클릭 뒤 실제 포커스 자식창을 사용한다.
        effective_target = (
            focus if focus and focus != root and focus_root == root
            else target if target and target != root and self._root_of(target) == root
            else 0
        )
        base_log = (
            f"symbol={symbol} root={root} target={target} effective_target={effective_target} "
            f"target_class={self._class_name(effective_target)!r} "
            f"xy=({x},{y}) foreground={foreground_ok} focus={focus} focus_root={focus_root} "
            f"click_focus={click_focus_ok} clipboard_ok={clipboard_ok}"
        )

        # 1순위: 사용자 PC에서 수동으로 성공했던 Ctrl+V 경로.
        paste_sent = False
        if foreground_ok and (focus_root == root or click_focus_ok):
            paste_sent = self._paste_with_keys()
            time.sleep(0.20)
            verified, verify_method, observed = self._verify_value(effective_target, root, x, y, symbol)
            enter_ok = self._press(VK_RETURN) if paste_sent else False
            diag(
                f"ATTEMPT method=SENDINPUT_PASTE sent={paste_sent} verified={verified} "
                f"enter={enter_ok} verify={verify_method!r} observed={observed!r} {base_log}"
            )
            if paste_sent and verified and enter_ok:
                return True, f"종목코드 입력 확인 + Enter 전송: {symbol}"
        else:
            diag(f"SKIP_SENDINPUT_PASTE no_focus {base_log}")

        # 2순위: UI Automation ValuePattern/LegacyIAccessible SetValue + read-back.
        self._activate_and_focus(root, effective_target, x, y)
        uia = self._run_uia(x, y, symbol, "set")
        uia_verified = bool(uia.get("matched")) and str(uia.get("value", "")) == symbol
        diag(f"ATTEMPT method=UIA data={json.dumps(uia, ensure_ascii=False)} {base_log}")
        if uia_verified:
            refocused, refocus_hwnd, refocus_click = self._activate_and_focus(
                root, effective_target, x, y
            )
            refocus_root = self._root_of(refocus_hwnd)
            enter_ok = (
                self._press(VK_RETURN)
                if refocused and (refocus_root == root or refocus_click)
                else False
            )
            diag(
                f"ENTER method=UIA ok={enter_ok} refocused={refocused} "
                f"focus={refocus_hwnd} focus_root={refocus_root} click={refocus_click} "
                f"{base_log}"
            )
            if enter_ok:
                return True, f"종목코드 입력 확인 + Enter 전송: {symbol} (UIA)"

        # 3순위: 표준 Edit 계열 자식 컨트롤에만 직접 메시지를 보낸다.
        # 최상위 창이나 라벨에 WM_SETTEXT를 보내는 위험을 차단한다.
        message_attempts: tuple[MethodResult, ...] = ()
        if self._message_target_safe(effective_target, root):
            message_attempts = (
                self._set_text_message(effective_target, symbol),
                self._paste_message(effective_target, symbol),
                self._char_message(effective_target, symbol),
            )
        else:
            diag(
                f"SKIP_DIRECT_MESSAGES unsafe_target={effective_target} "
                f"class={self._class_name(effective_target)!r} {base_log}"
            )
        for attempt in message_attempts:
            diag(
                f"ATTEMPT method={attempt.method} sent={attempt.sent} verified={attempt.verified} "
                f"observed={attempt.observed!r} detail={attempt.detail!r} {base_log}"
            )
            if attempt.sent and attempt.verified:
                refocused, refocus_hwnd, refocus_click = self._activate_and_focus(root, effective_target, x, y)
                enter_ok = self._press(VK_RETURN) if refocused and (refocus_hwnd or refocus_click) else False
                diag(
                    f"ENTER method={attempt.method} ok={enter_ok} refocused={refocused} "
                    f"focus={refocus_hwnd} click={refocus_click} {base_log}"
                )
                if enter_ok:
                    return True, f"종목코드 입력 확인 + Enter 전송: {symbol} ({attempt.method})"

        # 4순위: 물리 숫자 키와 가까운 VK/스캔코드 입력.
        unverified_sent = paste_sent
        for method, scan_code in (("VK_DIGITS", False), ("SCANCODE_DIGITS", True)):
            activated, digit_focus, digit_click = self._activate_and_focus(
                root, effective_target, x, y
            )
            digit_focus_root = self._root_of(digit_focus)
            if not activated or not (digit_focus_root == root or digit_click):
                diag(
                    f"SKIP_DIGITS method={method} activated={activated} "
                    f"focus={digit_focus} focus_root={digit_focus_root} click={digit_click} "
                    f"{base_log}"
                )
                continue
            sent = self._digits_with_keys(symbol, scan_code=scan_code)
            unverified_sent = unverified_sent or sent
            time.sleep(0.20)
            verified, verify_method, observed = self._verify_value(effective_target, root, x, y, symbol)
            enter_ok = self._press(VK_RETURN) if sent else False
            diag(
                f"ATTEMPT method={method} sent={sent} verified={verified} enter={enter_ok} "
                f"verify={verify_method!r} observed={observed!r} {base_log}"
            )
            if sent and verified and enter_ok:
                return True, f"종목코드 입력 확인 + Enter 전송: {symbol} ({method})"

        # 컨트롤이 값을 외부에 노출하지 않으면 read-back이 불가능하다.
        # 이 경우 성공을 단정하지 않고 전송 사실만 알린다. 클립보드는 항상 유지한다.
        if unverified_sent and foreground_ok:
            return False, f"{symbol} 입력 전송은 했지만 확인되지 않음 — 영웅문 화면을 확인하세요"
        return False, f"{symbol} 복사됨 — 자동입력 실패, 영웅문 입력칸에서 Ctrl+V 가능"


class LinkedGui(BaseGui):
    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return

        root.title("V75.36 관심·후보·매수·매도 — 이중 재검증형 영웅문 연동")
        root.geometry("1280x680")
        root.minsize(1080, 520)
        hero = VerifiedMultiHeroLink(v7525.HERO_LINK_FILE)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        status_var = tk.StringVar(
            value="종목 줄을 더블클릭하세요." if hero.configured
            else "처음 한 번만 영웅문 위치 저장을 하세요."
        )
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="영웅문 위치 저장", command=lambda: begin_capture()).pack(side="left")
        ttk.Label(toolbar, text="  더블클릭 → 코드 복사 + 자동전환").pack(side="left")
        ttk.Label(toolbar, textvariable=time_var).pack(side="right")

        columns = ("no", "stage", "buy_time", "name", "symbol", "price", "reason")
        tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        headings = {
            "no": "순번", "stage": "단계", "buy_time": "매수시각", "name": "종목명",
            "symbol": "코드", "price": "매수가 / +1.5% 매도가", "reason": "이유",
        }
        widths = {
            "no": 55, "stage": 95, "buy_time": 85, "name": 190,
            "symbol": 75, "price": 220, "reason": 500,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            anchor = "w" if column in ("name", "reason") else ("e" if column == "price" else "center")
            tree.column(
                column, width=widths[column], anchor=anchor,
                stretch=column in ("name", "reason"),
            )
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True, padx=(8, 0), pady=4)
        ttk.Label(root, textvariable=status_var, anchor="w", padding=8).pack(fill="x")

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear()
                root.clipboard_append(symbol)
                root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}:{exc}")
                return False

        link_busy = False
        link_results: queue.Queue[tuple[bool, str, bool]] = queue.Queue()

        def switch_symbol(symbol: str) -> None:
            nonlocal link_busy
            if not symbol.isdigit() or len(symbol) != 6:
                status_var.set(f"종목코드를 읽지 못했습니다: {symbol!r}")
                return
            if link_busy:
                status_var.set("앞 종목 연동을 확인 중입니다. 잠시만 기다리세요.")
                return
            copied = tk_copy(symbol)
            diag(f"DOUBLECLICK symbol={symbol} tk_clipboard={copied}")
            link_busy = True
            status_var.set(f"{symbol} 영웅문 연동 확인 중…")

            def worker() -> None:
                try:
                    ok, message = hero.send_symbol(symbol)
                except Exception as exc:
                    diag(f"LINK_EXCEPTION symbol={symbol} error={type(exc).__name__}:{exc}")
                    ok, message = False, f"연동 중 오류: {type(exc).__name__}"

                link_results.put((ok, message, copied))

            threading.Thread(target=worker, name="V75HeroLink", daemon=True).start()

        def on_double_click(event: Any) -> None:
            item_id = tree.identify_row(event.y)
            if not item_id:
                return
            tree.selection_set(item_id)
            tree.focus(item_id)
            values = tree.item(item_id, "values")
            if len(values) >= 5:
                switch_symbol(str(values[4]).strip())

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target()
            status_var.set(message if ok else f"위치 저장 실패: {message}")

        def countdown(remaining: int) -> None:
            if remaining <= 0:
                finish_capture()
                return
            status_var.set(f"{remaining}초 안에 영웅문 종목코드 입력칸을 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo(
                "영웅문 위치 저장",
                "확인을 누른 뒤 5초 안에 영웅문 종목코드 입력칸을 한 번 클릭하세요.\n"
                "클릭한 뒤 마우스를 움직이지 마세요.",
            )
            countdown(5)

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            rows = list(snapshot.get("rows", []))
            counters: dict[str, int] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                tree.insert("", "end", values=(
                    counters[stage], stage, row.get("buy_time", "-"), row.get("name", ""),
                    row.get("symbol", ""), row.get("display_price", row.get("price", "")),
                    row.get("reason", ""),
                ))
            time_var.set(str(snapshot.get("updated", "")))
            status_var.set(f"시장 {snapshot.get('market', '-')} | 중복 제거 후 {len(rows)}종목")

        def poll() -> None:
            nonlocal link_busy
            try:
                while True:
                    ok, message, copied = link_results.get_nowait()
                    link_busy = False
                    if ok:
                        status_var.set(message)
                    elif copied:
                        status_var.set(f"{message} (종목코드는 복사됨)")
                    else:
                        status_var.set(f"{message} (클립보드도 실패)")
            except queue.Empty:
                pass
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

        tree.bind("<Double-1>", on_double_click)
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(200, poll)
        root.mainloop()


# 매매 엔진은 V75.30 PrecisionTargetEngine 그대로 두고 GUI만 교체한다.
v7525.VERSION = VERSION
v7525.SimpleDoubleClickGui = LinkedGui


def _defined_function_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _loaded_names(source: str) -> set[str]:
    tree = ast.parse(source)
    return {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    source = Path(__file__).read_text(encoding="utf-8")
    defined = _defined_function_names(source)
    loaded = _loaded_names(source)
    forbidden = {"send_order", "create_order", "place_order", "order_stock", "submit_order"}
    results.extend([
        ("V75.30기준본SHA", sha256_file(BASE_DIR / BASE_FILENAME) == BASE_SHA256, BASE_SHA256[:16]),
        ("매매엔진무변경", v7525.V7525Engine is v7530.PrecisionTargetEngine, v7525.V7525Engine.__name__),
        ("제목비교함수정의", "_window_title_matches" in defined, "NameError 차단"),
        ("클립보드readback", "clipboard_after == symbol" in source, "Tk/Win32 실제 값 확인"),
        ("문자열성공오판제거", "paste_detail" not in loaded, "bool 반환값만 판정"),
        ("Enter반환확인", source.count("enter_ok = self._press(VK_RETURN)") >= 3, "모든 성공 경로 Enter 확인"),
        ("KEYEVENTF_UNICODE미사용", "KEYEVENTF_UNICODE" not in loaded, "VK/스캔코드 사용"),
        ("다중입력경로", all(token in source for token in ("SENDINPUT_PASTE", "UIA.ValuePattern", "WM_SETTEXT", "WM_PASTE", "SCANCODE_DIGITS")), "5단계"),
        ("미검증성공차단", "입력 전송은 했지만 확인되지 않음" in source and "return False" in source, "미검증은 실패 상태"),
        ("수동복구항상유지", "자동입력 실패, 영웅문 입력칸에서 Ctrl+V 가능" in source, "복사 선행"),
        ("GUI비동기연동", "threading.Thread(target=worker" in source and "link_results.put" in source, "UI 멈춤·Tk 스레드 호출 방지"),
        ("실주문함수부재", not (defined & forbidden), str(sorted(defined & forbidden))),
        ("전환성공과장제거", "영웅문 종목전환 확인" not in inspect.getsource(VerifiedMultiHeroLink.send_symbol), "입력값+Enter까지만 확인"),
        ("최상위창메시지차단", "return 0" in inspect.getsource(VerifiedMultiHeroLink._find_target) and "target == root" in inspect.getsource(VerifiedMultiHeroLink._message_target_safe), "창 제목 오염 방지"),
        ("Edit계열만직접메시지", all(token in inspect.getsource(VerifiedMultiHeroLink._message_target_safe) for token in ("edit", "textbox", "richedit")), "라벨/루트 제외"),
        ("신설정제목단독차단", "WINDOW_REJECT_TITLE_ONLY" in inspect.getsource(VerifiedMultiHeroLink._find_window), "다른 창 오선택 방지"),
        ("레거시검색자기GUI제외", "pid != os.getpid()" in inspect.getsource(VerifiedMultiHeroLink._find_window), "영웅문 문자열 포함 GUI 오선택 차단"),
        ("제목비교회귀", _window_title_matches("영웅문4", "영웅문4 - 삼성전자") and not _window_title_matches("영웅문4", "메모장"), "부분 제목 허용"),
        ("UIA후포커스재검증", "ENTER method=UIA" in source and "refocus_root == root" in source, "다른 창 Enter 차단"),
        ("숫자키전포커스재검증", "SKIP_DIGITS" in source and "digit_focus_root == root" in source, "다른 창 숫자 입력 차단"),
        ("자기창위치저장차단", "CAPTURE_REJECT_SELF" in source and "pid == os.getpid()" in source, "잘못된 최초 설정 차단"),
    ])
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7530.main())


if __name__ == "__main__":
    raise SystemExit(main())
