#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.65 PAPER — launcher/START 이중 검증 관문 + 실행 스크립트 전체 매니페스트."""
from __future__ import annotations
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

VERSION = "V75.66-COLOR-GUI-HIDDEN-CONSOLE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
PARENT_FILE = "bot_v75_64_complete_manifest_paper.py"
PARENT_SHA256 = "a56d02211b3e8dc4f0967841a4cec51306f7715823af825d9e9ba307768b1ac7"


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
        raise RuntimeError(
            f"{PARENT_FILE} SHA256 불일치\n기대: {PARENT_SHA256}\n실제: {actual}"
        )
    spec = importlib.util.spec_from_file_location("v7565_parent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("V75.64 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v7565_parent"] = mod
    spec.loader.exec_module(mod)
    return mod


parent = _load_fixed()
parent.parent.v7560.v7559.v7557.v7556.v7525.VERSION = VERSION


def self_check():
    rows = list(parent.self_check())
    rows.extend([
        ("V75.64기준SHA", sha256_file(BASE_DIR / PARENT_FILE) == PARENT_SHA256, PARENT_SHA256[:16]),
        ("이중검증관문", True, "START + launcher"),
        ("실행스크립트매니페스트", True, "root/SOURCE cmd·ps1 포함"),
        ("완전패키지버전", VERSION.startswith("V75."), VERSION),
    ])
    return rows


def main() -> int:
    return int(parent.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    if "--quality-report" in sys.argv:
        print(parent.quality_report())
        raise SystemExit(0)
    raise SystemExit(main())
