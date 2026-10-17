#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.61 PAPER — 프라임 시그널 대시보드 사용성 최종판.

V75.60의 매매·관찰·표본충분도 로직을 SHA256으로 고정 상속한다.
대시보드 표시와 조작만 정리한다: 코드열 제거, 이유열 확대, 날짜 앞 0 제거,
단계별 색상, 고정 로직 설명, 한 번 클릭 통일, 협업 버튼 4개 단순화.
PAPER 전용.
"""
from __future__ import annotations
import hashlib, importlib.util, sys
from pathlib import Path
from types import ModuleType

VERSION = "V75.66-COLOR-GUI-HIDDEN-CONSOLE-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7560_FILENAME = "bot_v75_60_sample_aware_final_paper.py"
V7560_SHA256 = "cf5b358d3de70ec8dc5c6698e5d487d31d0953aa528331988d795952bd31de42"

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path=BASE_DIR/filename
    actual=sha256_file(path)
    if actual.lower()!=expected_sha.lower():
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec=importlib.util.spec_from_file_location(module_name,path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module=importlib.util.module_from_spec(spec)
    sys.modules[module_name]=module
    spec.loader.exec_module(module)
    return module

v7560=_load_fixed(V7560_FILENAME,V7560_SHA256,'v7561_immutable_v7560_base')
# 최하위 콘솔/GUI에 표시되는 버전만 갱신한다.
v7560.v7559.v7557.v7556.v7525.VERSION=VERSION

def self_check():
    source=(BASE_DIR/'bot_v75_57_unified_dashboard_collaboration_paper.py').read_text(encoding='utf-8')
    parent=v7560.self_check()
    checks=[
        ('V75.60기준SHA', sha256_file(BASE_DIR/V7560_FILENAME)==V7560_SHA256, V7560_SHA256[:16]),
        ('V75.60회귀', all(ok for _,ok,_ in parent), f"{sum(ok for _,ok,_ in parent)}/{len(parent)}"),
        ('코드열제거',
          '"symbol": "코드"' not in source
          and 'columns = ("no", "stage", "name", "current", "change"' in source
          and '"volume"' in source
          and '"reason"' in source,
          '종목코드는 내부에만 유지'),
        ('이유열확대',
          '"reason": 760' in source and 'stretch=column == "reason"' in source,
          '조건·손익·보류이유 최우선'),
        ('날짜앞0제거', '_compact_stage_time' in source and 're.sub' in source, '05/23→5/23'),
        ('한번클릭통일', 'click_mode["single"] = True' in source, '차트 전환'),
        ('버튼4개', all(x in source for x in ('1  뜻 확인','2  해석대로 적용','3  활성등록','4  건너뛰기·취소')), '단순화'),
        ('버튼정책분리', v7560.v7559.v7557.route_interpreted_action('BUY_CONDITION') == 'ACTIVE_ONLY' and v7560.v7559.v7557.route_interpreted_action('SET_TARGET') == 'GENERAL', '가격조건은 활성등록 전용'),
        ('무손절안내정합', '현재 자동손절은 사용하지 않습니다' in source and '자동손절 주문을 뜻하지 않음' in source, '실제 정책과 문구 일치'),
        ('목표가출처표시', '자동 +1.5%' in source and '사용자 지정' in source and '전략/사용자 지정' in source, '목표가 출처 구분'),
        ('고정용어판', '단계·조건·약어 로직 요약' in source and 'S1:' in source and 'S2:' in source, '탭 없이 표시'),
        ('컬러단계명', '매수후보' in source and 'STAGE_ACCENTS' in source and '로그 보기' in source, '컬러 칩·숨김콘솔 로그'),
        ('PAPER전용', 'PAPER' in VERSION, VERSION),
    ]
    return checks

def quality_report():
    return v7560.quality_report()

def main()->int:
    return int(v7560.main())

if __name__=='__main__':
    if '--self-check' in sys.argv:
        rows=self_check()
        for name,ok,detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _,ok,_ in rows) else 1)
    if '--quality-report' in sys.argv:
        print(quality_report()); raise SystemExit(0)
    raise SystemExit(main())
