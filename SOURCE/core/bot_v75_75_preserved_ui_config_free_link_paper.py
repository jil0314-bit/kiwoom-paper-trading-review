#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.75 PAPER — 보존형 대시보드·시간순 형 요청·설정무관 원클릭.

V75.74를 SHA 고정 상속한다. 완성 3분봉과 매매 판단은 바꾸지 않는다.
이번 버전의 변경 범위는 (1) 자유의견 시간순 구조화, (2) 화면 비율·설명·
이유열 정렬, (3) 과거 위치설정이 fallback target을 오염시키던 연동 결함이다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any


VERSION = "V75.75-PRESERVED-UI-CONFIG-FREE-LINK-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_74_verified_oneclick_three_min_paper.py"
PARENT_SHA256 = "8fd34ebdd95d27025ddaea6f9379324eea38f0bcf6ce7ea370cbcdece8198d97"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7575_immutable_v7574_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525


class ConfigFreeOneClickHeroLink(parent.VerifiedOneClickHeroLink):
    """저장 JSON을 읽더라도 target/point 선택에는 절대 사용하지 않는다."""

    def __init__(self, config_path: Path) -> None:
        super().__init__(config_path)
        loaded_keys = sorted(str(key) for key in self.config)
        self.config = {}
        parent.diag(
            f"V7575_CONFIG_IGNORED path={str(config_path)!r} loaded_keys={loaded_keys!r}"
        )

    def _direct_hit_target(self, root: int, x: int, y: int) -> int:
        if not v57.v7538.IS_WINDOWS or not root:
            return 0
        point = wintypes.POINT(int(x), int(y))
        hit = int(ctypes.windll.user32.WindowFromPoint(point) or 0)
        if hit and hit != root and int(self._root_of(hit) or 0) == root:
            return hit
        return 0

    def _resolve_input(self, root: int) -> tuple[int, tuple[int, int] | None, str]:
        candidates = self._input_candidates(root)
        if candidates:
            chosen = candidates[0]
            left, top, right, bottom = chosen.rect
            point = ((left + right) // 2, (top + bottom) // 2)
            return chosen.hwnd, point, (
                f"DISCOVERED_CONFIG_FREE hwnd={chosen.hwnd} score={chosen.score:.0f} "
                f"class={chosen.class_name!r} text={chosen.text!r} rect={chosen.rect}"
            )
        points = self._fallback_points(root)
        if not points:
            return 0, None, "NO_FALLBACK_POINT"
        # 첫 점이 형이 제공한 실제 [0600] 종목 입력칸 내부(96 DPI client 72,15)다.
        x, y = points[0]
        hit = self._direct_hit_target(root, x, y)
        hit_class = self._win_class(hit) if hit else ""
        hit_text = self._win_text(hit) if hit else ""
        return hit, (x, y), (
            f"DIRECT_HIT_CONFIG_FREE point={(x, y)} hit={hit} "
            f"class={hit_class!r} text={hit_text!r}"
        )

    def _activate_and_focus(self, root: int, target: int, x: int, y: int):
        # 물리 클릭이 커스텀 입력칸 포커스를 결정한다. 클릭 뒤 임의 target으로
        # SetFocus하여 포커스를 다시 빼앗던 상속 경로를 target=0으로 차단한다.
        return super()._activate_and_focus(root, 0, x, y)

    @staticmethod
    def _powershell_sendkeys(pid: int) -> tuple[bool, str]:
        """정확하지 않은 PID AppActivate 없이 현재 전경 [0600]에 SendKeys한다."""
        if pid <= 0:
            return False, "영웅문 PID 확인 실패"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Start-Sleep -Milliseconds 120;"
            "[System.Windows.Forms.SendKeys]::SendWait('^a');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('{BACKSPACE}');"
            "Start-Sleep -Milliseconds 80;"
            "[System.Windows.Forms.SendKeys]::SendWait('^v');"
            "Start-Sleep -Milliseconds 180;"
            "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
        )
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-Command", script,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception as exc:
            return False, f"PowerShell 실행 실패: {type(exc).__name__}"
        detail = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:160]
        return (True, "현재 [0600] Ctrl+V 보조전송") if result.returncode == 0 else (
            False, f"PowerShell 실패 code={result.returncode} {detail}".strip()
        )


def _install_runtime() -> None:
    v57.RestoredHeroLink = ConfigFreeOneClickHeroLink
    v57.ReliableHeroLink = ConfigFreeOneClickHeroLink
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    return {
        **parent.runtime_identity(),
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "link_class": f"{v57.RestoredHeroLink.__module__}.{v57.RestoredHeroLink.__qualname__}",
        "link_route": "config ignored -> discover/direct hit -> physical click -> SendInput -> current-window SendKeys",
        "saved_config_loaded_for_target": False,
        "request_preview": "chronological news/theme/stock/market/derivatives/order summaries",
        "layout_contract": "main 74:26; bottom 28:38:18:16; reason heading left; centered 7-colour stage cards; boxed title bar",
    }


def _config_free_probe() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "stale.json"
        path.write_text(json.dumps({
            "target_class": "StaleSavedClass", "target_control_id": 77,
            "x_ratio": 0.91, "y_ratio": 0.88,
        }), encoding="utf-8")
        link = ConfigFreeOneClickHeroLink(path)
        link._input_candidates = lambda _root: []
        link._fallback_points = lambda _root: [(72, 38)]
        link._direct_hit_target = lambda _root, _x, _y: 200
        link._win_class = lambda _hwnd: "ActualInput"
        link._win_text = lambda _hwnd: "014950"
        link._find_target = lambda *_args: (_ for _ in ()).throw(AssertionError("saved target used"))
        target, point, route = link._resolve_input(100)
        return not link.config and target == 200 and point == (72, 38), route


def self_check() -> list[tuple[str, bool, str]]:
    # 이전 버전의 760px 고정 이유열 검사는 이번 사용자 요청(비례폭·좌측 제목)으로
    # 의도적으로 대체한다. 나머지 부모 검사는 그대로 보존한다.
    superseded = {"실행링크교체", "이유열확대", "이유열최대"}
    rows = [row for row in parent.self_check() if row[0] not in superseded]
    config_ok, config_detail = _config_free_probe()
    gui_source = (BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    parser_source = (BASE_DIR / "v7568_intent_engine.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.74기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("실행링크교체", v57.RestoredHeroLink is ConfigFreeOneClickHeroLink, str(v57.RestoredHeroLink)),
        ("저장설정완전무시", config_ok, config_detail),
        ("클릭뒤SetFocus차단", "super()._activate_and_focus(root, 0, x, y)" in inspect.getsource(ConfigFreeOneClickHeroLink._activate_and_focus), "physical click only"),
        ("PID활성화제거", "New-Object -ComObject WScript.Shell" not in inspect.getsource(ConfigFreeOneClickHeroLink._powershell_sendkeys), "current foreground only"),
        ("시간순요청미리보기", all(token in gui_source for token in ("request_preview_tree", "시간순 요청 요약", "refresh_request_preview")), "뉴스·추천·수급·가격"),
        ("화면비율고정", all(token in gui_source for token in ("weight=74", "weight=26", "(28, 38, 18, 16)", 'heading_anchor = "w"')), "목록/설명/하단/이유열"),
        ("단계카드중앙컬러", all(token in gui_source for token in ("stage_card_palette", 'anchor="center", justify="center"', "title_bar_bg", 'width=13, anchor="center"')), "7색 카드·중앙정렬·타이틀 바"),
        ("자유의견범위", all(token in parser_source for token in ("STOCK_WATCH", "DERIVATIVES_FLOW", "STOCK_FUTURES_FLOW", "THEME_PRIORITY")), "종목·뉴스·파생"),
        ("완성3분봉보존", parent.parent.BAR_MINUTES == 3, "V75.73 판단 유지"),
        ("PAPER전용", "PAPER" in VERSION, VERSION),
    ])
    return rows


def main() -> int:
    print("RUNTIME_IDENTITY", json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
    parent.diag("V7575_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
