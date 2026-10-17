#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.67 PAPER — 최종 운용 대시보드: 컬러 UI, 단계 필터, 콘솔 종목 복원, 자유명령 협업."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType
VERSION = "V75.67-FINAL-OPERATION-DASHBOARD-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_66_color_gui_hidden_console_paper.py"
PARENT_SHA256 = "1670f096f1445236d88158f7e7e36d347153dff8ff756e1c81e976ddd43b7e2e"
def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()
def _load_fixed() -> ModuleType:
    path=BASE_DIR/PARENT_FILE; actual=sha256_file(path)
    if actual != PARENT_SHA256: raise RuntimeError(f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec=importlib.util.spec_from_file_location("v7567_parent", path)
    if spec is None or spec.loader is None: raise RuntimeError("V75.66 로드 실패")
    mod=importlib.util.module_from_spec(spec); sys.modules["v7567_parent"]=mod; spec.loader.exec_module(mod); return mod
parent=_load_fixed()
parent.parent.parent.parent.v7560.v7559.v7557.v7556.v7525.VERSION=VERSION
def self_check():
    rows=list(parent.self_check())
    source=(BASE_DIR/"bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    rows.extend([
      ("V75.66기준SHA", sha256_file(BASE_DIR/PARENT_FILE)==PARENT_SHA256, PARENT_SHA256[:16]),
      ("단계카드필터", "choose_category(chosen)" in source and "category_filter_var" in source, "상단 단계 클릭 필터"),
      ("콘솔종목복원", "SEED_CONSOLE_ROWS" in source and "주성엔지니어링" in source, "최근 콘솔 종목 fallback"),
      ("이유열확대", '"reason": 760' in source and 'stretch=column == "reason"' in source, "조건·손익·보유이유 확대"),
      ("형요청3분할", "형 의견·명령 입력" in source and "형 요청 해석결과 미리보기" in source and "형 요청 활성 목록" in source, "운용 3영역"),
    ])
    return rows
def main(): return int(parent.main())
if __name__=="__main__":
    if "--self-check" in sys.argv:
        rows=self_check()
        for name,ok,detail in rows: print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _,ok,_ in rows) else 1)
    raise SystemExit(main())
