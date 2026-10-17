#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.72 PAPER — restored [0600] linkage and approved dashboard entry."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


VERSION = "V75.72-LINK-UI-FINAL-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_71_adaptive_wave_theme_engine_paper.py"
PARENT_SHA256 = "d0b415ab82671bf0f8911fe2cfb8303c35886c505556f1d5f55c2a5d0fbda37d"


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path* without loading it all at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_parent() -> ModuleType:
    """Load the pinned V75.71 parent or stop before any engine starts."""
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(
            f"{PARENT_FILE} SHA 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7572_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.71 부모 모듈 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules["v7572_parent"] = module
    spec.loader.exec_module(module)
    return module


parent = _load_parent()
v57 = parent.v57
v57.VERSION = VERSION
v57.v7525.VERSION = VERSION


def runtime_identity() -> dict[str, Any]:
    """Describe the exact GUI and link classes used by the live entry chain."""
    gui_class = v57.v7525.SimpleDoubleClickGui
    link_class = v57.RestoredHeroLink
    manifest_path = BASE_DIR.parent / "chain_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {"version": f"READ_ERROR:{type(exc).__name__}", "files": {}}
    return {
        "entry": str(Path(__file__).resolve()),
        "gui_class": f"{gui_class.__module__}.{gui_class.__qualname__}",
        "gui_file": str(Path(sys.modules[gui_class.__module__].__file__).resolve()),
        "link_class": f"{link_class.__module__}.{link_class.__qualname__}",
        "link_mro": [f"{item.__module__}.{item.__qualname__}" for item in link_class.__mro__],
        "hero_config": str(v57.v7525.HERO_LINK_FILE),
        "manifest_version": str(manifest.get("version", "UNKNOWN")),
        "manifest_files": len(manifest.get("files", {})),
        "python": sys.executable,
        "cwd": str(Path.cwd()),
    }


def self_check() -> list[tuple[str, bool, str]]:
    """Run inherited checks plus V75.72 runtime-object and regression checks."""
    rows = list(parent.self_check())
    source = (BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(
        encoding="utf-8"
    )
    identity = runtime_identity()
    rows.extend([
        (
            "V75.71기준SHA",
            sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256,
            PARENT_SHA256[:16],
        ),
        (
            "실행GUI확정",
            v57.v7525.SimpleDoubleClickGui is v57.V7557UnifiedDashboard,
            str(identity["gui_class"]),
        ),
        (
            "실행연동객체확정",
            issubclass(v57.RestoredHeroLink, v57.AutoDiscoverHeroLink),
            str(identity["link_class"]),
        ),
        (
            "자동탐색우선복원",
            "route=AUTO_THEN_SAVED_FALLBACK" in source
            and "super().send_symbol(normalized)" in source
            and "__mro__[" not in source,
            "V75.44 자동탐색→저장좌표 예비",
        ),
        (
            "승인대시보드구조",
            all(
                token in source
                for token in (
                    'GUI_STAGE_ORDER = ("관찰후보", "관심종목", "매수가능성"',
                    "형 의견·명령 입력",
                    "형 요청 해석결과 미리보기",
                    "형 요청 활성 목록",
                    "의견·요청 시간대별 이력",
                    '"volume": "거래량(20일비)"',
                )
            ),
            "7단계·좌우 본문·하단4패널",
        ),
        (
            "불필요띠배너미표시",
            "selected_strip.pack" not in source and "chain_banner.pack" not in source,
            "선택상세 띠·대형 매니페스트 배너 제거",
        ),
        (
            "PAPER전용",
            "PAPER" in VERSION and "실주문" not in VERSION,
            VERSION,
        ),
    ])
    return rows


def main() -> int:
    """Log resolved runtime identity, then delegate to the preserved engine."""
    print("RUNTIME_IDENTITY", json.dumps(runtime_identity(), ensure_ascii=False, sort_keys=True))
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
