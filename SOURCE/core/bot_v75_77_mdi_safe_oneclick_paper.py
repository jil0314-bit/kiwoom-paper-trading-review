#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.77 PAPER — MDI·다중창 안전 선택 + 단일 전송 원클릭.

V75.75를 SHA 고정 상속한다. 완성 3분봉, PAPER 정책, 단계 판단과 대시보드는
변경하지 않는다. 이번 버전은 영웅문 [0600] 창 탐색·활성화·입력 전송만 교체한다.

핵심 계약
---------
1. 최상위 창과 MDI 자식창을 함께 찾되 숨김·무효·0면적 창은 제외한다.
2. 최상위/자식이라는 이유로 우선하지 않고 전경 소유자·활성 MDI·포커스·Z순서를
   하나의 점수로 계산해 백그라운드의 옛 [0600] 오선택을 줄인다.
3. 좌표·입력컨트롤 탐색은 실제 차트 자식창 기준, 활성화·전경 판정은 최상위 조상
   기준으로 분리한다.
4. 키 입력 전에 포커스 또는 WindowFromPoint가 선택 차트 내부임을 확인한다.
5. 표준 Edit는 WM_SETTEXT+Enter, 커스텀 입력칸은 Ctrl+A/Backspace/Ctrl+V/Enter를
   정확히 한 경로로 한 번만 보낸다. 읽기 불가를 이유로 두 번째 전송하지 않는다.
6. 모든 실패 경로를 같은 대시보드 로그에 남긴다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
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
from typing import Any


VERSION = "V75.77-MDI-SAFE-ONECLICK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_75_preserved_ui_config_free_link_paper.py"
PARENT_SHA256 = "0082c36022077ebdb5b7c355165b6082c48bf00622b99fa31d74e1e2e3751417"

CHART_TOKENS = ("[0600]", "키움종합차트")
OWNER_TOKENS = ("영웅문", "키움")
WM_MDIACTIVATE = 0x0222
WM_MDIGETACTIVE = 0x0229
SW_SHOW = 5


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7577_immutable_v7575_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525
LinkStatus = v57.LinkStatus
diag = parent.parent.diag
IS_WINDOWS = v57.v7538.IS_WINDOWS


@dataclass(frozen=True, slots=True)
class ChartWindowCandidate:
    hwnd: int
    top: int
    title: str
    class_name: str
    rect: tuple[int, int, int, int]
    score: float
    top_z: int
    is_mdi: bool
    active_mdi: bool
    focus_inside: bool


def _title_token_count(title: str) -> int:
    return sum(1 for token in CHART_TOKENS if token in str(title or ""))


class MdiSafeOneClickHeroLink(parent.ConfigFreeOneClickHeroLink):
    """숨김·다중창을 걸러 실제 [0600] 한 곳에 한 경로로만 전송한다."""

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        self._last_chart_hwnd = 0
        self._last_focus_detail = ""

    @staticmethod
    def _u32():
        return ctypes.windll.user32

    @classmethod
    def _is_window_visible(cls, hwnd: int) -> bool:
        if not IS_WINDOWS or not hwnd:
            return False
        u32 = cls._u32()
        try:
            return bool(u32.IsWindow(wintypes.HWND(hwnd)) and u32.IsWindowVisible(wintypes.HWND(hwnd)))
        except (AttributeError, OSError, TypeError):
            return False

    @classmethod
    def _is_window_enabled(cls, hwnd: int) -> bool:
        if not IS_WINDOWS or not hwnd:
            return False
        try:
            return bool(cls._u32().IsWindowEnabled(wintypes.HWND(hwnd)))
        except (AttributeError, OSError, TypeError):
            return True

    @staticmethod
    def _rect_usable(rect: tuple[int, int, int, int] | None) -> bool:
        if rect is None:
            return False
        left, top, right, bottom = rect
        return right - left >= 240 and bottom - top >= 120

    @classmethod
    def _enum_top_windows(cls) -> list[int]:
        if not IS_WINDOWS:
            return []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = callback_type(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        values: list[int] = []

        @enum_proc
        def callback(hwnd, _lparam):
            values.append(int(hwnd))
            return True

        cls._u32().EnumWindows(callback, 0)
        return values

    @classmethod
    def _enum_descendants(cls, root: int) -> list[int]:
        if not IS_WINDOWS or not root:
            return []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_proc = callback_type(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        values: list[int] = []

        @enum_proc
        def callback(hwnd, _lparam):
            values.append(int(hwnd))
            return True

        cls._u32().EnumChildWindows(wintypes.HWND(root), callback, 0)
        return list(dict.fromkeys(values))

    @classmethod
    def _is_child(cls, parent_hwnd: int, child_hwnd: int) -> bool:
        if not IS_WINDOWS or not parent_hwnd or not child_hwnd or parent_hwnd == child_hwnd:
            return False
        try:
            return bool(cls._u32().IsChild(wintypes.HWND(parent_hwnd), wintypes.HWND(child_hwnd)))
        except (AttributeError, OSError, TypeError):
            return False

    def _belongs_to_chart(self, chart: int, hwnd: int) -> bool:
        return bool(hwnd and (hwnd == chart or self._is_child(chart, hwnd)))

    def _top_level_of(self, hwnd: int) -> int:
        try:
            return int(self._root_of(hwnd) or hwnd)
        except Exception:
            return int(hwnd or 0)

    def _mdi_client_for(self, chart: int, top: int) -> int:
        if not IS_WINDOWS or not chart or chart == top:
            return 0
        try:
            current = int(self._u32().GetParent(wintypes.HWND(chart)) or 0)
        except (AttributeError, OSError, TypeError):
            current = 0
        visited: set[int] = set()
        while current and current != top and current not in visited:
            visited.add(current)
            if self._win_class(current).lower() == "mdiclient":
                return current
            try:
                current = int(self._u32().GetParent(wintypes.HWND(current)) or 0)
            except (AttributeError, OSError, TypeError):
                break
        for child in self._enum_descendants(top):
            if self._win_class(child).lower() == "mdiclient" and self._is_child(child, chart):
                return child
        return 0

    def _active_mdi_child(self, top: int) -> int:
        if not IS_WINDOWS or not top:
            return 0
        for child in self._enum_descendants(top):
            if self._win_class(child).lower() != "mdiclient":
                continue
            ok, active, _error = self._send_message(child, WM_MDIGETACTIVE, 0, 0, timeout_ms=250)
            if ok and active:
                return int(active)
        return 0

    def _chart_candidates(self) -> list[ChartWindowCandidate]:
        if not IS_WINDOWS:
            return []
        foreground = int(self._foreground() or 0)
        foreground_top = self._top_level_of(foreground)
        candidates: list[ChartWindowCandidate] = []
        seen: set[int] = set()
        for top_z, top in enumerate(self._enum_top_windows()):
            if not self._is_window_visible(top):
                continue
            top_title = self._win_text(top)
            top_focus = int(self._focus_hwnd(top) or 0)
            active_mdi = self._active_mdi_child(top)
            for hwnd in (top, *self._enum_descendants(top)):
                if hwnd in seen or not self._is_window_visible(hwnd) or not self._is_window_enabled(hwnd):
                    continue
                title = self._win_text(hwnd)
                token_count = _title_token_count(title)
                if token_count <= 0:
                    continue
                rect = self._win_rect(hwnd)
                if not self._rect_usable(rect):
                    continue
                seen.add(hwnd)
                owner_top = self._top_level_of(hwnd)
                focus_inside = self._belongs_to_chart(hwnd, top_focus)
                active_inside = bool(active_mdi and (active_mdi == hwnd or self._is_child(hwnd, active_mdi)))
                score = 500.0 + token_count * 300.0
                if any(token in top_title for token in OWNER_TOKENS):
                    score += 180.0
                if owner_top == foreground_top and foreground_top:
                    score += 520.0
                if active_inside:
                    score += 460.0
                if focus_inside:
                    score += 420.0
                if hwnd == self._last_chart_hwnd:
                    score += 700.0
                score -= min(240.0, top_z * 8.0)
                candidates.append(ChartWindowCandidate(
                    hwnd=hwnd,
                    top=owner_top,
                    title=title,
                    class_name=self._win_class(hwnd),
                    rect=rect,
                    score=score,
                    top_z=top_z,
                    is_mdi=owner_top != hwnd,
                    active_mdi=active_inside,
                    focus_inside=focus_inside,
                ))
        candidates.sort(key=lambda item: (-item.score, item.top_z, item.hwnd))
        detail = [
            {
                "hwnd": item.hwnd, "top": item.top, "score": round(item.score, 1),
                "mdi": item.is_mdi, "active_mdi": item.active_mdi,
                "focus_inside": item.focus_inside, "title": item.title,
                "class": item.class_name, "rect": item.rect,
            }
            for item in candidates[:12]
        ]
        diag(f"V7577_CANDIDATES foreground={foreground} values={detail!r}")
        return candidates

    def _chart_roots(self) -> list[int]:
        return [item.hwnd for item in self._chart_candidates()]

    def _window_from_point(self, x: int, y: int) -> int:
        if not IS_WINDOWS:
            return 0
        point = wintypes.POINT(int(x), int(y))
        try:
            return int(self._u32().WindowFromPoint(point) or 0)
        except (AttributeError, OSError, TypeError):
            return 0

    def _direct_hit_target(self, root: int, x: int, y: int) -> int:
        hit = self._window_from_point(x, y)
        return hit if self._belongs_to_chart(root, hit) else 0

    def _activate_mdi_child(self, chart: int, top: int) -> None:
        if not IS_WINDOWS or not chart or chart == top:
            return
        mdi_client = self._mdi_client_for(chart, top)
        if mdi_client:
            self._send_message(mdi_client, WM_MDIACTIVATE, chart, 0, timeout_ms=300)
        try:
            self._u32().ShowWindow(wintypes.HWND(chart), SW_SHOW)
            self._u32().BringWindowToTop(wintypes.HWND(chart))
        except (AttributeError, OSError, TypeError):
            pass
        time.sleep(0.08)

    def _activate_and_focus(self, root: int, target: int, x: int, y: int):
        chart = int(root)
        top = self._top_level_of(chart)
        self._activate_mdi_child(chart, top)
        foreground_ok, focus, click_ok = super()._activate_and_focus(top, 0, x, y)
        foreground_now = int(self._foreground() or 0)
        hit = self._window_from_point(x, y)
        focus_inside = self._belongs_to_chart(chart, int(focus or 0))
        hit_inside = self._belongs_to_chart(chart, hit)
        gate = bool(
            foreground_ok and foreground_now == top and click_ok
            and (focus_inside or hit_inside)
        )
        self._last_focus_detail = (
            f"chart={chart} top={top} foreground={foreground_now} focus={focus} "
            f"hit={hit} focus_inside={focus_inside} hit_inside={hit_inside} "
            f"click={click_ok} gate={gate}"
        )
        return gate, int(focus or 0), bool(click_ok)

    def _safe_edit_target(self, chart: int, hwnd: int) -> bool:
        if not hwnd or hwnd == chart or not self._belongs_to_chart(chart, hwnd):
            return False
        class_name = self._win_class(hwnd).lower()
        return any(token in class_name for token in ("edit", "textbox", "richedit"))

    def _finish_edit_route(self, chart: int, top: int, target: int, symbol: str):
        result = self._set_text_message(target, symbol)
        foreground_ok = int(self._foreground() or 0) == top
        if not result.sent:
            return None
        if not foreground_ok:
            diag(f"V7577_FAIL stage=foreground_lost_after_wmset symbol={symbol}")
            return LinkStatus.FAILED, "입력 중 영웅문 전경 포커스를 잃었습니다."
        enter_ok = self._press(v57.v7538.VK_RETURN)
        time.sleep(0.20)
        observed = self._read_control_text(target)
        verified = result.verified or observed == symbol
        diag(
            f"V7577_SEND route=WM_SETTEXT symbol={symbol} target={target} "
            f"sent={result.sent} enter={enter_ok} observed={observed!r} verified={verified}"
        )
        if not enter_ok:
            return LinkStatus.FAILED, f"{symbol} 입력 후 Enter 전송에 실패했습니다."
        if verified:
            return LinkStatus.VERIFIED, f"{symbol} 영웅문 차트 전환 확인"
        return LinkStatus.SENT_UNVERIFIED, f"{symbol} 한 번 전송 완료 — 영웅문 화면을 확인해주세요."

    def _finish_keyboard_route(self, chart: int, top: int, effective: int, symbol: str):
        if int(self._foreground() or 0) != top:
            diag(f"V7577_FAIL stage=foreground_before_keys symbol={symbol}")
            return LinkStatus.FAILED, "키 전송 직전 영웅문 전경 포커스를 잃었습니다."
        paste_ok = self._paste_with_keys()
        time.sleep(0.18)
        foreground_still = int(self._foreground() or 0) == top
        observed_before = self._read_control_text(effective) if effective else ""
        enter_ok = bool(
            paste_ok and foreground_still
            and self._press(v57.v7538.VK_RETURN)
        )
        time.sleep(0.22)
        observed_after = self._read_control_text(effective) if effective else ""
        verified = symbol in {str(observed_before).strip(), str(observed_after).strip()}
        diag(
            f"V7577_SEND route=CTRL_V_ONCE symbol={symbol} paste={paste_ok} "
            f"foreground={foreground_still} enter={enter_ok} before={observed_before!r} "
            f"after={observed_after!r} verified={verified}"
        )
        if not paste_ok or not foreground_still or not enter_ok:
            return LinkStatus.FAILED, (
                f"{symbol} 단일 전송 실패 · paste={paste_ok} "
                f"foreground={foreground_still} enter={enter_ok}"
            )
        if verified:
            return LinkStatus.VERIFIED, f"{symbol} 영웅문 차트 전환 확인"
        return LinkStatus.SENT_UNVERIFIED, f"{symbol} Ctrl+V 한 번 전송 완료 — 영웅문 화면을 확인해주세요."

    def send_symbol(self, symbol: str):
        normalized = str(symbol).strip()
        diag(f"V7577_ENTER symbol={normalized!r}")
        if not IS_WINDOWS:
            diag("V7577_FAIL stage=platform")
            return LinkStatus.FAILED, "Windows에서만 작동합니다."
        if not re.fullmatch(r"\d{6}", normalized):
            diag(f"V7577_FAIL stage=format symbol={normalized!r}")
            return LinkStatus.FAILED, f"종목코드 형식 오류: {normalized}"
        if self._clipboard_read() != normalized:
            self._clipboard_write(normalized)
        if self._clipboard_read() != normalized:
            diag("V7577_FAIL stage=clipboard")
            return LinkStatus.FAILED, "종목코드 클립보드 저장 실패"

        candidates = self._chart_candidates()
        if not candidates:
            diag("V7577_FAIL stage=no_chart_window")
            return LinkStatus.FAILED, "보이는 [0600] 키움종합차트 창을 찾지 못했습니다."

        chosen: ChartWindowCandidate | None = None
        chosen_target = 0
        chosen_focus = 0
        for candidate in candidates:
            target, point, resolution = self._resolve_input(candidate.hwnd)
            diag(
                f"V7577_TARGET_TRY symbol={normalized} chart={candidate.hwnd} "
                f"top={candidate.top} score={candidate.score:.1f} target={target} "
                f"point={point} resolution={resolution!r}"
            )
            if point is None:
                diag(f"V7577_CANDIDATE_SKIP stage=no_point chart={candidate.hwnd}")
                continue
            gate, focus, _click_ok = self._activate_and_focus(
                candidate.hwnd, target, point[0], point[1]
            )
            diag(f"V7577_FOCUS_TRY {self._last_focus_detail}")
            if not gate:
                continue
            chosen = candidate
            chosen_target = int(target or 0)
            chosen_focus = int(focus or 0)
            break

        if chosen is None:
            diag("V7577_FAIL stage=no_focusable_candidate")
            return LinkStatus.FAILED, (
                "[0600] 창은 찾았지만 보이는 종목 입력칸을 안전하게 클릭하지 못했습니다. "
                "영웅문과 프로그램의 관리자 권한을 같게 실행해주세요."
            )

        effective = 0
        if self._safe_edit_target(chosen.hwnd, chosen_target):
            effective = chosen_target
        elif self._safe_edit_target(chosen.hwnd, chosen_focus):
            effective = chosen_focus

        if effective:
            result = self._finish_edit_route(
                chosen.hwnd, chosen.top, effective, normalized
            )
            if result is not None:
                if result[0] != LinkStatus.FAILED:
                    self._last_chart_hwnd = chosen.hwnd
                return result

        result = self._finish_keyboard_route(
            chosen.hwnd, chosen.top,
            chosen_focus if self._belongs_to_chart(chosen.hwnd, chosen_focus) else chosen_target,
            normalized,
        )
        if result[0] != LinkStatus.FAILED:
            self._last_chart_hwnd = chosen.hwnd
        return result

    def probe_auto_target(self) -> tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "Windows에서만 작동합니다."
        candidates = self._chart_candidates()
        if not candidates:
            return False, "보이는 [0600] 키움종합차트를 찾지 못했습니다."
        chosen = candidates[0]
        target, point, route = self._resolve_input(chosen.hwnd)
        if point is None:
            return False, "[0600] 입력칸 위치를 계산하지 못했습니다."
        return True, (
            f"원클릭 준비 · chart={chosen.hwnd} · top={chosen.top} · "
            f"MDI={chosen.is_mdi} · target={target} · point={point} · {route}"
        )


def _install_runtime() -> None:
    v57.RestoredHeroLink = MdiSafeOneClickHeroLink
    v57.ReliableHeroLink = MdiSafeOneClickHeroLink
    v57.diag = diag
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "link_class": f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
        "link_mro": [f"{item.__module__}.{item.__name__}" for item in v57.RestoredHeroLink.__mro__[:5]],
        "link_route": "rank visible top/MDI -> activate owner -> validate chart focus/hit -> one WM_SETTEXT or Ctrl+V route",
        "chart_lookup": "visible top + visible MDI descendants; unified scored selection",
        "focus_gate": "foreground top + physical click + focus-or-hit inside selected chart",
        "send_policy": "one route once: safe Edit WM_SETTEXT OR custom Ctrl+V",
        "saved_config_loaded_for_target": False,
    })
    return identity


def _offline_contract_probe() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        link = MdiSafeOneClickHeroLink(Path(temp_dir) / "unused.json")
        link._root_of = lambda hwnd: 1000 if hwnd in {2000, 3000} else hwnd
        link._is_child = lambda parent_hwnd, child_hwnd: (parent_hwnd, child_hwnd) == (2000, 3000)
        return (
            link._top_level_of(2000) == 1000
            and link._belongs_to_chart(2000, 3000)
            and not link.config,
            "child=2000 top=1000 input=3000 config=ignored",
        )


def self_check() -> list[tuple[str, bool, str]]:
    # 부모의 정확한 V75.75 클래스 동일성 검사는 V75.77 런타임 교체로 대체한다.
    rows = [row for row in parent.self_check() if row[0] != "실행링크교체"]
    source = Path(__file__).read_text(encoding="utf-8")
    probe_ok, probe_detail = _offline_contract_probe()
    gui_module = sys.modules.get(v7525.SimpleDoubleClickGui.__module__)
    rows.extend([
        ("V75.75기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("실행링크교체", v57.RestoredHeroLink is MdiSafeOneClickHeroLink, str(v57.RestoredHeroLink)),
        ("GUI모듈패치", getattr(gui_module, "ReliableHeroLink", None) is MdiSafeOneClickHeroLink, v7525.SimpleDoubleClickGui.__name__),
        ("MDI계약", probe_ok, probe_detail),
        ("숨김창제외", "not self._is_window_visible(hwnd)" in source, "자식창도 IsWindowVisible"),
        ("통합후보점수", "candidates.sort" in inspect.getsource(MdiSafeOneClickHeroLink._chart_candidates), "최상위/자식 단일 순위"),
        ("차트내부포커스관문", all(token in source for token in ("focus_inside", "hit_inside", "foreground_now == top")), "focus-or-hit inside chart"),
        ("단일전송정책", "route=CTRL_V_ONCE" in source and "_powershell_sendkeys" not in inspect.getsource(MdiSafeOneClickHeroLink.send_symbol), "재전송 없음"),
        ("진입즉시로그", "V7577_ENTER" in source, "첫 단계 기록"),
        ("런타임정보진입점", runtime_identity().get("entry") == str(Path(__file__).resolve()), str(runtime_identity().get("entry"))),
        ("완성3분봉보존", runtime_identity().get("bar_minutes") == 3 and runtime_identity().get("completed_only") is True, "3-minute completed only"),
        ("PAPER전용", VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7577_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
