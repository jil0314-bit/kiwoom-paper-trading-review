#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
ROOT_DIR = SOURCE_DIR.parent
MANIFEST = SOURCE_DIR / "chain_manifest.json"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def verify_chain():
    if not MANIFEST.exists():
        return False, ["chain_manifest.json 누락"]
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"매니페스트 읽기 실패: {exc}"]
    problems = []
    for rel, expected in manifest.get("files", {}).items():
        path = ROOT_DIR / rel
        if not path.exists():
            problems.append(f"누락: {rel}")
        elif sha256_file(path) != expected:
            problems.append(f"불일치: {rel}")
    return not problems, problems

def make_banner():
    ok, problems = verify_chain()
    m = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    return f"{m.get('version','UNKNOWN')} · 빌드 {m.get('built_at','-')} · " + (
        f"체인 OK({len(m.get('files',{}))}개)" if ok else f"체인 문제 {len(problems)}건"
    )

def main():
    ok, problems = verify_chain()
    print(make_banner())
    if ok:
        print("[OK] 실행 파일이 모두 기준과 일치합니다.")
        return 0
    for i, problem in enumerate(problems, 1):
        print(f"[문제 {i}] {problem}")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
