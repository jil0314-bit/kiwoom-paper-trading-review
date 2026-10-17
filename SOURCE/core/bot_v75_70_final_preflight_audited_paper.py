#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.70 PAPER — 최종 사전검증·승인 디자인·연동 복원."""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType

VERSION = "V75.70-FINAL-PREFLIGHT-AUDITED-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_69_approved_ui_link_restore_paper.py"
PARENT_SHA256 = "fdf7d7f2fe51a2ab4e8e962aa8fa1f916151b4010683007da5e750ed13bde098"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _load() -> ModuleType:
    path = BASE_DIR / PARENT_FILE
    actual = sha256_file(path)
    if actual != PARENT_SHA256:
        raise RuntimeError(f"{PARENT_FILE} SHA 불일치\n기대: {PARENT_SHA256}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location("v7570_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.69 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules["v7570_parent"] = module
    spec.loader.exec_module(module)
    return module

parent = _load()
parent.parent.parent.parent.parent.parent.parent.v7560.v7559.v7557.VERSION = VERSION

def self_check():
    rows = list(parent.self_check())
    source = (BASE_DIR / "bot_v75_57_unified_dashboard_collaboration_paper.py").read_text(encoding="utf-8")
    rows.extend([
        ("V75.69기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("자동탐색우선연동", "class RestoredHeroLink" in source and "AUTO_THEN_SAVED_FALLBACK" in source, "자동탐색→저장위치 예비"),
        ("거래량열복원", '"volume": "거래량(20일비)"' in source and "fmt_volume" in source, "20일비"),
        ("활성목록통합표시", 'iid=f"intent:{intent_id}"' in source and 'iid=f"overlay:{overlay_id}"' in source, "종목조건+판단/테마"),
        ("활성선택취소", 'selected_id.startswith("intent:")' in source and 'HUB.cancel(overlay_id)' in source, "의견/조건 구분"),
        ("승인하단4패널", all(token in source for token in (
            "형 의견·명령 입력", "형 요청 해석결과 미리보기",
            "형 요청 활성 목록", "의견·요청 시간대별 이력"
        )), "입력·해석·활성·이력"),
        ("승인단계순서", 'GUI_STAGE_ORDER = ("관찰후보", "관심종목", "매수가능성"' in source, "01 관찰→02 관심"),
        ("화살표원클릭연동", 'tree.bind("<Up>"' in source and 'tree.bind("<Down>"' in source and "RestoredHeroLink" in source, "↑↓/한 번 클릭"),
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
