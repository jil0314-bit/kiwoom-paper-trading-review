#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.45 PAPER — 영웅문 자동탐색 오입력 방어 강화

V75.44 의 [0600] 키움종합차트 자동탐색·화살표 연동·매수신호 재검증을 그대로
유지하고, 교차검증에서 확인된 '오입력 위험' 한 가지만 좁힌다.

확인된 위험 (Claude 교차검증)
-----------------------------
V75.44 score_edit_candidate 는 상단 영역 밖의 Edit(예: 하단 조건검색칸)도
가점 합산으로 AUTO_TARGET_MIN_SCORE(95)를 넘길 수 있었다. 정상적으로 종목
입력칸(약 245점)이 존재하면 최고점 하나만 선택하므로 안전하지만, 종목칸이
표준 Edit 로 노출되지 않는 영웅문 빌드에서 종목칸이 후보에서 빠지면 하단
조건검색칸(약 100점)이 잘못 선택될 수 있었다. (read-back 으로 2차 방어되어
VERIFIED 로 오판되지는 않으나, 애초에 넣지 않는 것이 안전하다.)

이 파일의 수정
--------------
1. 상단 영역(차트 상단 약 16% 또는 150px) 밖의 Edit 는 후보에서 하드 배제한다.
   좌측 영역(약 70%) 밖도 하드 배제한다. → 하단·우측 Edit 오입력 차단.
2. 자동탐색 통과 임계값을 95 → 150 으로 올린다. 정상 종목칸(약 210~245)은
   여전히 통과하지만, 상단 밖 Edit 의 잔여 조합 점수로는 넘지 못한다.
3. 위 두 가지 외에는 V75.44 를 SHA256 고정으로 그대로 상속한다. 매매 로직,
   차트 자동탐색 흐름, 화살표 연동, 90초 catch-up, 진입 감사로그, PAPER 전용,
   +1.5% 목표·자동손절 없음·분할·종목당 한도는 모두 변경하지 않는다.

주의: 실제 영웅문 UI 확인 전에는 '확정 작동'이라 말하지 않는다. 자동탐색이
실패하면 V75.44 와 동일하게 수동 예비 위치 경로로 넘어간다.
PAPER 전용이며 실주문 함수는 없다.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

VERSION = 'V75.45-AUTO-TARGET-GUARDED-PAPER'
BASE_DIR = Path(__file__).resolve().parent

V7544_FILENAME = 'bot_v75_44_direct_chart_recheck_paper.py'
V7544_SHA256 = '3f77517d380942663d2b129157b43176bc2300007161840bc0e6abca0460c555'
V7543_SHA256 = '44ccbba4b506161737fcd5514fc3e40617d9b45f0c204345e0ee203eb78286fa'

# 상향된 통과 임계값 (V75.44 는 95.0)
GUARDED_MIN_SCORE = 150.0
# 상단 영역 한계: 차트 높이의 이 비율 또는 TOP_MAX_PX 중 큰 값까지만 허용
TOP_BAND_RATIO = 0.16
TOP_MAX_PX = 150
# 좌측 영역 한계: 차트 너비의 이 비율까지만 허용
LEFT_BAND_RATIO = 0.70


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha:
        raise RuntimeError(f'{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}')
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'{filename} 로드 실패')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7544 = _load_fixed(V7544_FILENAME, V7544_SHA256, 'v7545_immutable_base')
v7543 = v7544.v7543
v7538 = v7544.v7538
v7530 = v7544.v7530
v7525 = v7544.v7525
KST = v7544.KST

# V75.43 SHA 재확인 (이중 안전)
_v7543_path = BASE_DIR / 'bot_v75_43_atomic_safety_rebuild_paper.py'
if _v7543_path.exists() and sha256_file(_v7543_path).lower() != V7543_SHA256:
    raise RuntimeError('V75.43 SHA256 불일치 — 실행 중단')

_ORIGINAL_SELF_CHECK = v7544.self_check
_original_score = v7544.score_edit_candidate


def guarded_score_edit_candidate(
    *,
    root_rect: tuple[int, int, int, int],
    rect: tuple[int, int, int, int],
    class_name: str,
    text: str,
    parent_class: str = '',
) -> float:
    """V75.44 점수에 상단·좌측 영역 하드 배제를 추가한다.

    상단 밴드(높이 16% 또는 150px) 밖, 또는 좌측 밴드(너비 70%) 밖의 Edit 는
    종목 입력칸이 아닐 확률이 높으므로 후보에서 제외(-1000)한다.
    """
    root_left, root_top, root_right, root_bottom = root_rect
    left, top, right, bottom = rect
    root_width = max(1, root_right - root_left)
    root_height = max(1, root_bottom - root_top)
    rel_x = left - root_left
    rel_y = top - root_top

    top_limit = max(TOP_MAX_PX, int(root_height * TOP_BAND_RATIO))
    left_limit = int(root_width * LEFT_BAND_RATIO)

    # 상단 밴드 밖 → 하드 배제 (하단 조건검색칸 등)
    if rel_y < 0 or rel_y > top_limit:
        return -1_000.0
    # 좌측 밴드 밖 → 하드 배제 (우측 수량·가격칸 등)
    if rel_x < 0 or rel_x > left_limit:
        return -1_000.0

    return _original_score(
        root_rect=root_rect,
        rect=rect,
        class_name=class_name,
        text=text,
        parent_class=parent_class,
    )


# V75.44 모듈의 점수 함수와 임계값을 교체한다.
# discover_target 은 모듈 전역 score_edit_candidate / AUTO_TARGET_MIN_SCORE 를
# 호출 시점에 조회하므로, 여기서 바꾸면 실제 탐색에 반영된다.
v7544.score_edit_candidate = guarded_score_edit_candidate
v7544.AUTO_TARGET_MIN_SCORE = GUARDED_MIN_SCORE

# GUI 가 참조하는 링크 클래스는 V75.44 의 것을 그대로 쓰되, 위 전역 교체가
# discover_target 내부 호출에 반영된다. (AutoDiscoverHeroLink._edit_candidates 가
# 모듈 전역 score_edit_candidate 를 호출하기 때문)

v7525.VERSION = VERSION


def self_check() -> list[tuple[str, bool, str]]:
    results = list(_ORIGINAL_SELF_CHECK())
    root = (0, 0, 1400, 900)

    # 정상 종목칸: 여전히 통과해야 한다
    top_symbol = guarded_score_edit_candidate(
        root_rect=root, rect=(40, 20, 140, 44), class_name='Edit',
        text='005930', parent_class='ComboBox')
    # 하단 조건검색칸: 이제 배제돼야 한다
    bottom_search = guarded_score_edit_candidate(
        root_rect=root, rect=(40, 700, 200, 724), class_name='Edit',
        text='', parent_class='')
    # 우측 수량칸: 배제돼야 한다
    right_qty = guarded_score_edit_candidate(
        root_rect=root, rect=(1200, 400, 1300, 424), class_name='Edit',
        text='10', parent_class='')
    # 큰 박스: 상단이라도 폭 넓으면 점수 낮게, 임계값 못 넘어야 안전
    big_box = guarded_score_edit_candidate(
        root_rect=root, rect=(40, 20, 900, 320), class_name='Edit',
        text='', parent_class='')

    results.extend([
        ('V75.44기준SHA', sha256_file(BASE_DIR / V7544_FILENAME) == V7544_SHA256, V7544_SHA256[:16]),
        ('가드_정상종목칸통과', top_symbol >= GUARDED_MIN_SCORE, f'{top_symbol:.0f}>={GUARDED_MIN_SCORE:.0f}'),
        ('가드_하단칸배제', bottom_search < GUARDED_MIN_SCORE, f'{bottom_search:.0f}'),
        ('가드_우측칸배제', right_qty < GUARDED_MIN_SCORE, f'{right_qty:.0f}'),
        ('가드_대형박스배제', big_box < GUARDED_MIN_SCORE, f'{big_box:.0f}'),
        ('임계값상향', v7544.AUTO_TARGET_MIN_SCORE == GUARDED_MIN_SCORE, f'{GUARDED_MIN_SCORE:.0f}'),
        ('점수함수교체', v7544.score_edit_candidate is guarded_score_edit_candidate, '전역 반영'),
    ])
    return results


v7525.self_check = self_check


def main() -> int:
    return int(v7544.main())


if __name__ == '__main__':
    raise SystemExit(main())
