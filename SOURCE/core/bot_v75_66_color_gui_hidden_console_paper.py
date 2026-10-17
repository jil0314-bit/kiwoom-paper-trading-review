#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.66 PAPER — 컬러 단계 대시보드 + 기본 콘솔 숨김 + 파일 로그."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType
VERSION = "V75.66-COLOR-GUI-HIDDEN-CONSOLE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_65_double_gate_full_manifest_paper.py"
PARENT_SHA256 = "276fbb7ad35eb7a3bbc25e4613189c6ea25360d5d4ee01dc2fa202706844fcbf"

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def _load_fixed() -> ModuleType:
    path=BASE_DIR/PARENT_FILE; actual=sha256_file(path)
    if actual != PARENT_SHA256: raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec=importlib.util.spec_from_file_location("v7566_parent", path)
    if spec is None or spec.loader is None: raise RuntimeError("V75.65 로드 실패")
    mod=importlib.util.module_from_spec(spec); sys.modules["v7566_parent"]=mod; spec.loader.exec_module(mod); return mod
parent=_load_fixed()
parent.parent.parent.v7560.v7559.v7557.v7556.v7525.VERSION=VERSION

def self_check():
    rows=list(parent.self_check())
    source=(BASE_DIR/"bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.65기준SHA", sha256_file(BASE_DIR/PARENT_FILE)==PARENT_SHA256, PARENT_SHA256[:16]),
        ("매수후보표시", "매수후보" in source and "DISPLAY_STAGE" in source, "내부 로직명은 보존"),
        ("컬러단계카드", "STAGE_ACCENTS" in source and "stage_legend" in source, "7단계 색상 구분"),
        ("로그보기", "open_runtime_log" in source, "콘솔 대신 파일 로그"),
        ("완전패키지버전", "V75.66" in VERSION, VERSION),
    ])
    return rows

def main(): return int(parent.main())
if __name__=="__main__":
    if "--self-check" in sys.argv:
        rows=self_check()
        for name,ok,detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _,ok,_ in rows) else 1)
    if "--quality-report" in sys.argv:
        print(parent.parent.parent.quality_report()); raise SystemExit(0)
    raise SystemExit(main())
