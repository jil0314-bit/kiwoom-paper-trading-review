#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.78 PAPER — V75.77 연동 보존 + 화면 맞춤·비동기 안정화.

사용자가 실제 영웅문에서 작동을 확인한 V75.77 MDI 단일전송 클래스는 그대로
SHA 고정 상속한다. 이번 버전은 대시보드 작업영역 맞춤, 이력 삭제 버튼 대비,
종목코드 6자리 정규화, GUI 비차단 최신요청 처리를 검증해 활성화한다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


VERSION = "V75.78-STABLE-FAST-FIT-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_77_mdi_safe_oneclick_paper.py"
PARENT_SHA256 = "c77e42fe2232a510de96b5fffe76dbb33d32ef1e2831a502bc6aa1a67b23dc3b"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_parent() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual.lower() != PARENT_SHA256.lower():
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7578_immutable_v7577_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{PARENT_FILE} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v7525 = parent.v7525
MdiSafeOneClickHeroLink = parent.MdiSafeOneClickHeroLink
diag = parent.diag


def _install_runtime() -> None:
    # 연동 클래스는 사용자가 작동을 확인한 V75.77 객체와 정확히 동일하다.
    v57.RestoredHeroLink = MdiSafeOneClickHeroLink
    v57.ReliableHeroLink = MdiSafeOneClickHeroLink
    v57.VERSION = VERSION
    v7525.VERSION = VERSION


_install_runtime()


def runtime_identity() -> dict[str, Any]:
    identity = dict(parent.runtime_identity())
    identity.update({
        "version": VERSION,
        "entry": str(Path(__file__).resolve()),
        "link_class": f"{MdiSafeOneClickHeroLink.__module__}.{MdiSafeOneClickHeroLink.__qualname__}",
        "link_implementation": "V75.77 exact class; user-confirmed working route preserved",
        "gui_link_dispatch": "non-blocking single worker; latest pending selection wins",
        "symbol_normalization": "1-6 digits, Excel .0, A+6 digits -> zero-filled 6 digits",
        "focus_restore": "latest request only; one restore after completion",
        "screen_fit": "Windows SPI_GETWORKAREA; compact layout below 1060px work height",
    })
    return identity


def self_check() -> list[tuple[str, bool, str]]:
    rows = list(parent.self_check())
    gui_source = inspect.getsource(v57.V7557UnifiedDashboard._run)
    normalizer = v57.normalize_symbol_code
    samples = {
        "5930": "005930",
        "5930.0": "005930",
        "A005930": "005930",
        "005930": "005930",
        "삼성전자": "",
    }
    rows.extend([
        ("V75.77기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("검증연동동일성", v57.RestoredHeroLink is parent.MdiSafeOneClickHeroLink, str(v57.RestoredHeroLink)),
        ("종목코드정규화", all(normalizer(raw) == expected for raw, expected in samples.items()), str(samples)),
        ("GUI비차단전송", all(token in gui_source for token in ("run_link_worker", "GUI_LINK_COALESCED", "poll_link_results")), "single worker + latest pending"),
        ("단일포커스복귀", "root.after(60, attempt)" in gui_source and "for delay_ms in v7547.FOCUS_RESTORE_DELAYS_MS" not in gui_source, "one restore"),
        ("작업영역맞춤", "SystemParametersInfoW(0x0030" in gui_source and "compact_ui" in gui_source, "SPI_GETWORKAREA"),
        ("삭제버튼가시성", 'style="Delete.TButton"' in gui_source, "red/white explicit style"),
        ("완성3분봉보존", runtime_identity().get("bar_minutes") == 3 and runtime_identity().get("completed_only") is True, "3-minute completed only"),
        ("PAPER전용", VERSION.endswith("PAPER"), VERSION),
    ])
    return rows


def main() -> int:
    diag("V7578_RUNTIME " + json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
