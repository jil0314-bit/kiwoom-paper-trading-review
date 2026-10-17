#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.69 PAPER — 승인 디자인 정렬 + 영웅문 연동 복원."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType

VERSION = "V75.69-APPROVED-UI-LINK-RESTORE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_68_prime_signal_complete_rebuild_paper.py"
PARENT_SHA256 = "2567fb4a68ef47c20c344e092977ccfee1e8b6eddfae0d9f6246c843d88b47a9"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _load() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7569_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.68 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v7569_parent"] = mod
    spec.loader.exec_module(mod)
    return mod

parent = _load()
parent.parent.parent.parent.parent.parent.v7560.v7559.v7557.VERSION = VERSION

def self_check():
    rows = list(parent.self_check())
    source = (BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.68기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("승인단계순서", 'GUI_STAGE_ORDER = ("관찰후보", "관심종목", "매수가능성"' in source, "01 관찰→02 관심"),
        ("하단4등분", all(token in source for token in (
            'collab.grid(row=0, column=0', 'preview.grid(row=0, column=1',
            'active_tab.grid(row=0, column=2', 'opinion_history_tab.grid(row=0, column=3'
        )), "입력·해석·활성·이력"),
        ("실제연동확인", "hero.send_symbol(symbol)" in source and "queue_link_request(symbol" in source, "선택 종목 비동기 실제 전송"),
        ("화살표연동", 'tree.bind("<Up>"' in source and 'tree.bind("<Down>"' in source, "위·아래 연속전환"),
        ("이유열최대", '"reason": 760' in source and 'stretch=column == "reason"' in source, "조건·손익·보유이유"),
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
