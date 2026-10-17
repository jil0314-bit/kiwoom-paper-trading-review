#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.68 PAPER — 프라임 시그널 완전 리빌드."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType

VERSION = "V75.68-PRIME-SIGNAL-COMPLETE-REBUILD-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_67_final_operation_dashboard_paper.py"
PARENT_SHA256 = "3d3840090e5dd34061933e799bd9aa3d063bd2702dce26551a7a8c5f1e111e58"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _load_fixed() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7568_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.67 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v7568_parent"] = mod
    spec.loader.exec_module(mod)
    return mod

parent = _load_fixed()
parent.parent.parent.parent.parent.v7560.v7559.v7557.v7556.v7525.VERSION = VERSION

def self_check():
    rows = list(parent.self_check())
    source = (BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    engine = (BASE_DIR / "v7568_intent_engine.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.67기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("자유의견구조화", "parse_free_text" in engine and "SECTOR_GUARD" in engine and "THEME_PRIORITY" in engine, "시장·섹터·테마 분리"),
        ("시간대별이력", "의견·요청 시간대별 이력" in source and "IntentLedger" in source, "수정·해제·삭제"),
        ("활성목록통합", "형 요청 활성 목록" in source and "refresh_combined_active_summary" in source, "종목조건+판단/테마"),
        ("선택조건취소", "선택 요청 취소" in source and "HUB.cancel" in source and 'selected_id.startswith("intent:")' in source, "선택 활성조건 취소"),
        ("단계순서", all(token in source for token in ("관찰후보", "관심종목", "매수가능성", "매수직전", "시장보류", "매수종목", "매도종목")), "01~07"),
    ])
    return rows

def main():
    return int(parent.main())

if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    raise SystemExit(main())
