#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.64 PAPER — 완전 실행 패키지, 중앙 매니페스트, 버전 배너, 목표가 출처 영속화."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType
VERSION = "V75.66-COLOR-GUI-HIDDEN-CONSOLE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_61_prime_signal_dashboard_final_paper.py"
PARENT_SHA256 = "023a98fcf1ec67efa6a9e187c115d0450c606bdb61d9beb3a42a961393771e2f"

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def _load_fixed() -> ModuleType:
    path=BASE_DIR/PARENT_FILE
    actual=sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec=importlib.util.spec_from_file_location('v7564_parent', path)
    if spec is None or spec.loader is None: raise RuntimeError('V75.61 로드 실패')
    mod=importlib.util.module_from_spec(spec); sys.modules['v7564_parent']=mod; spec.loader.exec_module(mod); return mod
parent=_load_fixed()
parent.v7560.v7559.v7557.v7556.v7525.VERSION=VERSION

def self_check():
    rows=list(parent.self_check())
    src=parent.v7560.v7559.v7557
    test_symbol='999999'
    old=src._load_target_sources().copy()
    try:
        src.record_target_source(test_symbol, '사용자 지정')
        persisted=src.target_source_for(test_symbol)=='사용자 지정'
    finally:
        restored=old
        try: src._save_target_sources(restored)
        except OSError: pass
    rows.extend([
        ('V75.61기준SHA', sha256_file(BASE_DIR/PARENT_FILE)==PARENT_SHA256, PARENT_SHA256[:16]),
        ('목표가출처영속화', persisted, '재시작 복원 원장'),
        ('완전패키지버전', VERSION.startswith('V75.'), VERSION),
    ])
    return rows

def main(): return int(parent.main())
if __name__=='__main__':
    if '--self-check' in sys.argv:
        rows=self_check()
        for name,ok,detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _,ok,_ in rows) else 1)
    if '--quality-report' in sys.argv:
        print(parent.quality_report()); raise SystemExit(0)
    raise SystemExit(main())
