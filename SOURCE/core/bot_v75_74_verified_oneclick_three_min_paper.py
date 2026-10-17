#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.74 PAPER — 검증형 한 번 클릭 영웅문 연동 + 완성 3분봉.

V75.73의 매매 판단과 3분봉 집계는 SHA256으로 고정해 그대로 사용한다.
연동만 다음과 같이 교체한다.

* [0600] 상단의 현재 종목코드(6자리)를 가진 자식 컨트롤을 최우선 탐색한다.
* 표준 Edit가 아니어도 위치·크기·현재 코드로 입력 컨트롤 후보를 판정한다.
* 후보가 보이지 않는 커스텀 화면은 실제 [0600] 배치의 입력칸 내부점(72, 15)을 쓴다.
* 잘못된 x=110 고정점과 '창만 전경이면 성공'이라는 V75.73 오판을 제거한다.
* SendInput 뒤 읽기 검증이 안 되면, 수동 Ctrl+V와 같은 PowerShell SendKeys를
  동일하게 포커스된 입력칸에 한 번 더 보내는 보조 경로를 사용한다.
* GUI 클릭·포커스·붙여넣기·Enter 결과를 대시보드 로그 한 파일에 모은다.

PAPER 전용이며 실제 주문 경로는 추가하지 않는다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any


VERSION = "V75.74-VERIFIED-ONECLICK-3MIN-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_73_three_min_oneclick_paper.py"
PARENT_SHA256 = "c82d1b010e260f7b789d59650d37ea37959bb6f008221e992bfed637bc3ad444"
# V7557 GUI의 기존 '로그 보기' 버튼이 여는 이름을 유지해 클릭 진단까지 한 파일로 모은다.
RUNTIME_LOG = BASE_DIR.parent / "runtime" / "logs" / "v75_73_dashboard.log"
MIN_INPUT_SCORE = 260.0


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7574_immutable_v7573_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525

_diag_lock = threading.RLock()


def diag(message: str) -> None:
    """연동 기록을 사용자가 여는 대시보드 로그 하나에 남긴다."""
    try:
        RUNTIME_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _diag_lock, RUNTIME_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S.%f}] {message}\n")
            handle.flush()
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class ChartInputCandidate:
    hwnd: int
    class_name: str
    text: str
    rect: tuple[int, int, int, int]
    control_id: int
    score: float


def score_chart_input_candidate(
    *,
    root_rect: tuple[int, int, int, int],
    client_origin: tuple[int, int],
    rect: tuple[int, int, int, int],
    class_name: str,
    text: str,
) -> float:
    """[0600] 종목코드 입력칸 후보 점수. Win32 없이 회귀 테스트 가능."""
    root_left, root_top, root_right, root_bottom = root_rect
    origin_x, origin_y = client_origin
    left, top, right, bottom = rect
    width = max(0, right - left)
    height = max(0, bottom - top)
    rel_x = left - origin_x
    rel_y = top - origin_y
    root_width = max(1, root_right - root_left)
    root_height = max(1, root_bottom - root_top)
    cls = class_name.lower()
    value = str(text or "").strip()

    if width <= 0 or height <= 0 or rel_x < -5 or rel_y < -5:
        return -10_000.0
    if rel_x >= root_width or rel_y >= root_height:
        return -10_000.0

    score = 0.0
    if re.fullmatch(r"\d{6}", value):
        score += 1_000.0
    elif re.fullmatch(r"[A-Z]?\d{6}", value):
        score += 700.0
    elif value:
        score -= 40.0
    if "edit" in cls:
        score += 520.0
    if any(token in cls for token in ("button", "toolbar", "static")):
        score -= 180.0
    if 32 <= width <= 180:
        score += 100.0
    else:
        score -= min(180.0, abs(width - 90) * 1.5)
    if 14 <= height <= 42:
        score += 100.0
    else:
        score -= min(160.0, abs(height - 24) * 4.0)
    if 12 <= rel_x <= 185:
        score += 150.0
    if 0 <= rel_y <= 58:
        score += 170.0
    # 형이 제공한 [0600] 화면에서 입력칸 내부는 96 DPI client (약 48~98, 4~25).
    if left <= origin_x + 72 < right and top <= origin_y + 15 < bottom:
        score += 220.0
    return score


class VerifiedOneClickHeroLink(parent.OneClickDirectPasteHeroLink):
    """입력칸을 확인한 뒤 한 번 클릭·붙여넣기·Enter를 실행한다."""

    REFERENCE_X_96DPI = 72
    REFERENCE_Y_96DPI = 15
    FALLBACK_CLIENT_POINTS_96DPI = ((72, 15), (64, 15), (80, 15), (54, 15))

    @staticmethod
    def _window_pid(hwnd: int) -> int:
        if not v57.v7538.IS_WINDOWS or not hwnd:
            return 0
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    @staticmethod
    def _client_origin_and_scale(root: int) -> tuple[tuple[int, int], float] | None:
        if not v57.v7538.IS_WINDOWS or not root:
            return None
        user32 = ctypes.windll.user32
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(wintypes.HWND(root), ctypes.byref(origin)):
            return None
        try:
            dpi = int(user32.GetDpiForWindow(wintypes.HWND(root)) or 96)
        except (AttributeError, OSError):
            dpi = 96
        return (int(origin.x), int(origin.y)), max(0.75, min(3.0, dpi / 96.0))

    def _input_candidates(self, root: int) -> list[ChartInputCandidate]:
        if not v57.v7538.IS_WINDOWS or not root:
            return []
        root_rect = self._win_rect(root)
        origin_scale = self._client_origin_and_scale(root)
        if root_rect is None or origin_scale is None:
            return []
        origin, _scale = origin_scale
        user32 = ctypes.windll.user32
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        candidates: list[ChartInputCandidate] = []

        @enum_proc
        def callback(hwnd: int, _lparam: int) -> bool:
            child = int(hwnd)
            if not user32.IsWindowVisible(wintypes.HWND(child)):
                return True
            rect = self._win_rect(child)
            if rect is None:
                return True
            class_name = self._win_class(child)
            text = self._win_text(child)
            score = score_chart_input_candidate(
                root_rect=root_rect,
                client_origin=origin,
                rect=rect,
                class_name=class_name,
                text=text,
            )
            if score >= MIN_INPUT_SCORE:
                candidates.append(
                    ChartInputCandidate(
                        hwnd=child,
                        class_name=class_name,
                        text=text,
                        rect=rect,
                        control_id=int(user32.GetDlgCtrlID(wintypes.HWND(child)) or 0),
                        score=score,
                    )
                )
            return True

        user32.EnumChildWindows(wintypes.HWND(root), callback, 0)
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates

    def _fallback_points(self, root: int) -> list[tuple[int, int]]:
        origin_scale = self._client_origin_and_scale(root)
        if origin_scale is None:
            return []
        (origin_x, origin_y), scale = origin_scale
        return [
            (int(origin_x + x * scale), int(origin_y + y * scale))
            for x, y in self.FALLBACK_CLIENT_POINTS_96DPI
        ]

    def _resolve_input(self, root: int) -> tuple[int, tuple[int, int] | None, str]:
        candidates = self._input_candidates(root)
        if candidates:
            chosen = candidates[0]
            left, top, right, bottom = chosen.rect
            point = ((left + right) // 2, (top + bottom) // 2)
            preview = "; ".join(
                f"hwnd={item.hwnd},score={item.score:.0f},class={item.class_name},"
                f"text={item.text!r},rect={item.rect}"
                for item in candidates[:4]
            )
            return chosen.hwnd, point, "DISCOVERED " + preview

        for point in self._fallback_points(root):
            target = int(self._find_target(root, point[0], point[1]) or 0)
            class_name = self._win_class(target) if target else ""
            text = self._win_text(target) if target else ""
            lowered = class_name.lower()
            if any(token in lowered for token in ("button", "toolbar")):
                continue
            return target, point, (
                f"FALLBACK point={point} target={target} class={class_name!r} text={text!r}"
            )
        points = self._fallback_points(root)
        return 0, (points[0] if points else None), "FALLBACK_DRAWN_CONTROL"

    @staticmethod
    def _powershell_sendkeys(pid: int) -> tuple[bool, str]:
        if pid <= 0:
            return False, "영웅문 PID 확인 실패"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$ws=New-Object -ComObject WScript.Shell;"
            f"$ok=$ws.AppActivate({pid});"
            "Start-Sleep -Milliseconds 220;"
            "[System.Windows.Forms.SendKeys]::SendWait('^a');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('^v');"
            "Start-Sleep -Milliseconds 220;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            "if($ok){exit 0}else{exit 3}"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-Command", script,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception as exc:
            return False, f"PowerShell 실행 실패: {type(exc).__name__}"
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:160]
        if result.returncode == 0:
            return True, "PowerShell Ctrl+V 보조전송"
        return False, f"PowerShell 실패 code={result.returncode} {detail}".strip()

    def probe_auto_target(self) -> tuple[bool, str]:
        if not v57.v7538.IS_WINDOWS:
            return False, "Windows에서만 작동합니다."
        roots = self._chart_roots()
        if not roots:
            return False, "[0600] 키움종합차트를 찾지 못했습니다."
        target, point, route = self._resolve_input(roots[0])
        if point is None:
            return False, "[0600] 종목 입력칸을 계산하지 못했습니다."
        return True, f"원클릭 준비됨 · target={target} · point={point} · {route}"

    def send_symbol(self, symbol: str):
        normalized = str(symbol).strip()
        if not v57.v7538.IS_WINDOWS:
            return v57.LinkStatus.FAILED, "Windows에서만 작동합니다."
        if not re.fullmatch(r"\d{6}", normalized):
            return v57.LinkStatus.FAILED, f"종목코드 형식 오류: {normalized}"
        if self._clipboard_read() != normalized:
            self._clipboard_write(normalized)
        if self._clipboard_read() != normalized:
            return v57.LinkStatus.FAILED, "종목코드 클립보드 저장 실패"

        roots = self._chart_roots()
        if not roots:
            return v57.LinkStatus.FAILED, "[0600] 키움종합차트를 찾지 못했습니다."
        root = roots[0]
        target, point, resolution = self._resolve_input(root)
        diag(
            f"V7574_TARGET symbol={normalized} root={root} target={target} "
            f"point={point} resolution={resolution!r}"
        )
        if point is None:
            return v57.LinkStatus.FAILED, "[0600] 종목 입력칸 확인 실패"
        x, y = point
        foreground_ok, focus, click_ok = self._activate_and_focus(root, target, x, y)
        focus_root = int(self._root_of(focus) or 0)
        foreground_now = int(self._foreground() or 0)
        gate = bool(foreground_ok and click_ok and foreground_now == root)
        diag(
            f"V7574_FOCUS symbol={normalized} foreground={foreground_ok} "
            f"foreground_hwnd={foreground_now} focus={focus} focus_root={focus_root} "
            f"click={click_ok} gate={gate}"
        )
        if not gate:
            return v57.LinkStatus.FAILED, "[0600] 입력칸을 실제 클릭·포커스하지 못했습니다."

        paste_ok = self._paste_with_keys()
        time.sleep(0.20)
        foreground_still = int(self._foreground() or 0) == root
        effective = focus if focus and focus != root else target
        observed_before = self._read_control_text(effective) if effective else ""
        enter_ok = bool(paste_ok and foreground_still and self._press(v57.v7538.VK_RETURN))
        time.sleep(0.22)
        observed_after = self._read_control_text(effective) if effective else ""
        verified = normalized in {str(observed_before).strip(), str(observed_after).strip()}
        diag(
            f"V7574_NATIVE symbol={normalized} paste={paste_ok} foreground={foreground_still} "
            f"enter={enter_ok} observed_before={observed_before!r} "
            f"observed_after={observed_after!r} verified={verified}"
        )
        if verified:
            return v57.LinkStatus.VERIFIED, f"{normalized} 영웅문 차트 전환 확인"

        # 읽을 수 없는 영웅문 커스텀 입력칸은 수동 Ctrl+V와 같은 보조 경로로 재전송한다.
        ps_ok, ps_detail = self._powershell_sendkeys(self._window_pid(root))
        time.sleep(0.20)
        observed_ps = self._read_control_text(effective) if effective else ""
        ps_verified = str(observed_ps).strip() == normalized
        diag(
            f"V7574_POWERSHELL symbol={normalized} ok={ps_ok} detail={ps_detail!r} "
            f"observed={observed_ps!r} verified={ps_verified}"
        )
        if ps_verified:
            return v57.LinkStatus.VERIFIED, f"{normalized} 영웅문 차트 전환 확인(PowerShell)"
        if (paste_ok and foreground_still and enter_ok) or ps_ok:
            routes = "SendInput+PowerShell" if ps_ok else "SendInput"
            return v57.LinkStatus.SENT_UNVERIFIED, f"{normalized} {routes} 전송 완료"
        return v57.LinkStatus.FAILED, (
            f"{normalized} 전송 실패 · native(paste={paste_ok},enter={enter_ok}) · {ps_detail}"
        )


def _install_runtime() -> None:
    # GUI의 전역 이름은 _run 시점에 조회되므로 이 교체가 실제 생성 객체에 적용된다.
    v57.RestoredHeroLink = VerifiedOneClickHeroLink
    v57.ReliableHeroLink = VerifiedOneClickHeroLink
    v57.diag = diag
    # 하위 계층의 직접 진단 호출도 같은 파일로 모은다.
    for module in (getattr(v57, "v7556", None), getattr(v57, "v7544", None), getattr(v57, "v7538", None)):
        if module is not None:
            module.diag = diag
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    return {
        **parent.runtime_identity(),
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "link_class": f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
        "link_route": "discover current 6-digit control -> click -> SendInput -> PowerShell fallback",
        "click_mode": "ONE_CLICK_FORCED",
        "diagnostic_log": str(RUNTIME_LOG.resolve()),
        "saved_position_used": False,
    }


def self_check() -> list[tuple[str, bool, str]]:
    # V75.73의 링크 클래스 동일성 검사는 이 버전에서 의도적으로 교체되므로 제외한다.
    rows = [row for row in parent.self_check() if row[0] != "원클릭직접연동"]
    source = Path(__file__).read_text(encoding="utf-8")
    rows.extend([
        ("V75.73기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("실행링크교체", v57.RestoredHeroLink is VerifiedOneClickHeroLink, str(v57.RestoredHeroLink)),
        (
            "잘못된110좌표비사용",
            VerifiedOneClickHeroLink.FALLBACK_CLIENT_POINTS_96DPI[0] == (72, 15),
            "입력점 72 + 동적탐색",
        ),
        ("PowerShell보조", "System.Windows.Forms.SendKeys" in source, "수동 Ctrl+V 호환"),
        ("단일진단로그", v57.diag is diag, str(RUNTIME_LOG)),
        ("완성3분봉유지", parent.BAR_MINUTES == 3, "부모 매매로직 무변경"),
        ("PAPER전용", "PAPER" in VERSION, VERSION),
    ])
    return rows


def main() -> int:
    print("RUNTIME_IDENTITY", json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    diag("V7574_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
