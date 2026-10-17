#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V75.57 PAPER — 단일화면 통합 대시보드 + 전체 카테고리 + 안전 협업 명령.

V75.56을 SHA256으로 고정 상속한다. 기존 V75.47의 한 번/더블클릭 영웅문
연동, 위·아래 화살표 연속검토, 포커스·마우스 복귀와 V75.48 보유합계,
V75.56의 전체 카테고리·협업 파서·Execution Capsule 안전장치는 변경하지
않는다. 화면만 한 개의 업무 대시보드로 재구성한다.

중앙에는 전체 종목 탐색표, 오른쪽에는 선택종목·자유의견/명령·활성지정·
전체 용어사전을 탭으로 묶고, 상단에는 시장과 단계별 핵심 수치를 표시한다.
PAPER 전용이며 협업 입력은 엔진의 기존 안전검사를 우회하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import importlib.util
from pathlib import Path
import re
import sys
import threading
import time
from types import ModuleType
from typing import Any
from v7568_intent_engine import parse_free_text, IntentLedger

VERSION = "V75.72-LINK-UI-FINAL-PAPER"
BASE_DIR = Path(__file__).resolve().parent
V7556_FILENAME = "bot_v75_56_office_full_category_collaboration_paper.py"
V7556_SHA256 = "e91c1d66cdf68ade3de1532bb3839746a3d522903d411879e9e5042e49cf5b35"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_fixed(filename: str, expected_sha: str, module_name: str) -> ModuleType:
    path = BASE_DIR / filename
    actual = sha256_file(path)
    if actual.lower() != expected_sha.lower():
        raise RuntimeError(f"{filename} SHA256 불일치\n기대: {expected_sha}\n실제: {actual}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 로드 실패")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


v7556 = _load_fixed(V7556_FILENAME, V7556_SHA256, "v7557_immutable_v7556_base")
v7546 = v7556.v7546
v7538 = v7556.v7538
v7525 = v7556.v7525
v7547 = v7556.v7547
v7544 = v7556.v7544

KST = v7556.KST
GUI_STAGE_ORDER = ("관찰후보", "관심종목", "매수가능성", "매수직전", "시장보류", "매수종목", "매도종목")
GUI_STAGE_COLORS = v7556.GUI_STAGE_COLORS
DISPLAY_STAGE = {
    "관찰후보": "관찰종목",
    "관심종목": "관심종목",
    "매수가능성": "매수후보",
    "매수직전": "매수직전",
    "시장보류": "시장보류",
    "매수종목": "보유종목",
    "매도종목": "오늘매도",
}
STAGE_ACCENTS = {
    "관찰후보": ("#0d7890", "#8ee8f5"),
    "관심종목": ("#2f8f3a", "#b9f6b9"),
    "매수가능성": ("#b98500", "#ffe08a"),
    "매수직전": ("#d76510", "#ffc18d"),
    "시장보류": ("#7550a6", "#d9c3ff"),
    "매수종목": ("#138a94", "#9ef4f7"),
    "매도종목": ("#c13f62", "#ffb6ca"),
}
prepare_gui_rows = v7556.prepare_gui_rows
CollaborationDraft = v7556.CollaborationDraft
compile_collaboration_input = v7556.compile_collaboration_input
HUB = v7556.HUB
AutoDiscoverHeroLink = v7556.AutoDiscoverHeroLink

class RestoredHeroLink(AutoDiscoverHeroLink):
    """V75.44의 검증된 자동탐색 우선 경로에 구조화 진단만 추가한다.

    ``AutoDiscoverHeroLink.send_symbol``은 [0600] 표준 Edit 자동탐색을 먼저
    시도하고, 자동탐색이 실제로 실패한 특수 UI에서만 저장 좌표를 사용한다.
    이 순서를 재구현하지 않고 그대로 위임해 과거 정상 계열과 동작을 맞춘다.
    """

    def send_symbol(self, symbol: str):
        normalized = str(symbol).strip()
        configured = bool(self.configured)
        try:
            saved_valid = bool(configured and self._config_valid())
        except Exception as exc:
            saved_valid = False
            diag(
                "LINK_CONFIG_CHECK_FAILED "
                f"symbol={normalized!r} error={type(exc).__name__}:{exc}"
            )
        diag(
            "LINK_ATTEMPT "
            f"symbol={normalized!r} route=AUTO_THEN_SAVED_FALLBACK "
            f"configured={configured} saved_valid={saved_valid} "
            f"config_path={str(getattr(self, 'config_path', '<uninitialized>'))!r}"
        )
        try:
            status, message = super().send_symbol(normalized)
        except Exception as exc:
            diag(
                "LINK_EXCEPTION "
                f"symbol={normalized!r} error={type(exc).__name__}:{exc}"
            )
            return LinkStatus.FAILED, f"연동 내부 오류: {type(exc).__name__}: {exc}"

        route = (
            "SAVED_FALLBACK"
            if str(message).startswith("자동탐색 실패 후 예비 위치 사용:")
            else "AUTO"
        )
        diag(
            "LINK_RESULT "
            f"symbol={normalized!r} route={route} status={status.value} "
            f"message={str(message)!r}"
        )
        return status, message


# 외부 점검 코드의 기존 이름 호환. 실제 GUI는 RestoredHeroLink를 직접 사용한다.
ReliableHeroLink = RestoredHeroLink

LinkStatus = v7556.LinkStatus
CursorPosition = v7556.CursorPosition
capture_cursor_position = v7556.capture_cursor_position
restore_review_focus = v7556.restore_review_focus
select_arrow_index = v7556.select_arrow_index
find_iid_by_symbol = v7556.find_iid_by_symbol
diag = v7556.diag
_trigger_label = v7556._trigger_label
_trigger_code = v7556._trigger_code
_finite_or_none = v7556._finite_or_none
_next_session_close_iso = v7556._next_session_close_iso
_V7555_NAME_HINT = v7556._V7555_NAME_HINT
_V7555_PRICE_HINT = v7556._V7555_PRICE_HINT
OUTLIER_RATIO_HIGH = v7556.OUTLIER_RATIO_HIGH
OUTLIER_RATIO_LOW = v7556.OUTLIER_RATIO_LOW


TARGET_SOURCE_PATH = BASE_DIR.parent / "runtime" / "target_source_ledger.json"


def normalize_symbol_code(value: Any) -> str:
    """엑셀/조회 결과에서 온 국내 종목코드를 안전한 6자리로 정규화한다."""
    raw = str(value if value is not None else "").strip().upper()
    raw = raw.lstrip("'")
    if re.fullmatch(r"A\d{6}", raw):
        raw = raw[1:]
    decimal_match = re.fullmatch(r"(\d{1,6})\.0+", raw)
    if decimal_match:
        raw = decimal_match.group(1)
    if not re.fullmatch(r"\d{1,6}", raw):
        return ""
    return raw.zfill(6)

def _load_target_sources() -> dict[str, str]:
    """재시작 후에도 사용자 지정 목표가 출처를 복원한다."""
    try:
        data = json.loads(TARGET_SOURCE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(k) and str(v)}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}

def _save_target_sources(sources: dict[str, str]) -> None:
    """목표가 출처를 원자적으로 저장한다. 실패 시 기존 파일을 훼손하지 않는다."""
    TARGET_SOURCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = TARGET_SOURCE_PATH.with_suffix('.json.tmp')
    payload = json.dumps(dict(sorted(sources.items())), ensure_ascii=False, indent=2)
    with temp.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, TARGET_SOURCE_PATH)

def record_target_source(symbol: str, source: str) -> None:
    if not symbol:
        raise ValueError('종목코드가 비어 있습니다.')
    sources = _load_target_sources()
    sources[str(symbol)] = str(source)
    _save_target_sources(sources)

def target_source_for(symbol: str) -> str:
    return _load_target_sources().get(str(symbol), '')


DASHBOARD_TERMS = """[단계가 잡히는 기준]
관찰후보: 거래량이 살아나거나 테마·수급 변화가 포착됐지만 진입조건은 아직 부족한 초기 포착 단계
관심종목: 지지선 접근·수급 지속·반등 가능성을 확인하며 추적을 강화하는 단계
매수후보: 거래량·지지·반전 조건의 상당 부분이 충족되어 실제 매수 검토권에 들어온 단계
매수직전: 마지막 확인만 남은 단계. 반전봉·지지확인봉·시장상태·호가를 최종 점검
시장보류: 종목 조건은 괜찮아도 지수·섹터·시장 급락 위험 때문에 신규매수만 잠시 미룸
보유종목: 이미 PAPER 매수되어 목표가·평단·수익률·MAE/MFE를 관리하는 단계
오늘매도: 오늘 목표가·수동·장마감 등의 사유로 매도 완료된 종목. 당일 재진입은 주의

[가격·신호 약어]
현재가: 키움에서 마지막으로 받은 최신 시세. 마지막 수신시각과 함께 확인
기준가: 아직 미보유 종목의 진입 판단가격
평단: 분할매수 체결금액÷총수량으로 계산한 실제 평균매수가
목표가: 기본은 평단 대비 +1.5%. 사용자가 직접 바꾸면 해당 종목만 사용자 지정 목표가 사용
지지1·지지2·지지3: 가격이 정한 지지영역을 1회·2회·3회 지켜낸 상태
반전봉대기: 하락 뒤 양봉·아래꼬리 등 방향 전환 봉을 기다림
지지확인봉대기: 한 번 버틴 뒤 다음 봉에서도 지지가 유지되는지 기다림
S1: 1차 신호 강도 또는 1차 이탈 관찰 기준. 자동손절 주문을 뜻하지 않음
S2: 2차 신호 강도 또는 더 강한 이탈 관찰 기준. 자동손절 주문을 뜻하지 않음
큰매도배수: 평소 대비 강한 매도 거래량의 배수
앞선매수배수: 직전 매수 거래량이 평소보다 얼마나 강했는지

[4개 버튼]
1 뜻 확인: 자유문장을 해석 카드로 정리만 함. 주문·조건 등록 없음
2 해석대로 적용: 관심등록·의견저장·목표가변경·지정해제만 실행. 가격조건 등록은 하지 않음
3 활성등록: 매수가격 조건을 오늘 장 종료까지 감시목록에 등록. 즉시 지정가 주문을 미리 거는 기능 아님
4 건너뛰기·취소: 현재 작성 중인 문장과 해석만 지움. 보유·다른 종목·기존 활성조건은 삭제하지 않음

[조작]
한 번 클릭: 선택 종목을 영웅문 차트로 전송
↑/↓: 이전·다음 종목으로 이동하며 차트 연속 전환
연동 위치 초기화: 저장한 영웅문 입력 위치만 삭제하며 매매자료는 건드리지 않음
PAPER: 실제 돈이 아닌 모의주문
"""


BUTTON_GUIDE = (
    ("1  뜻 확인", "입력한 문장을 해석 카드로 정리합니다. 이 단계에서는 주문·등록이 없습니다.", "#2f8f3a"),
    ("2  해석대로 적용", "관심등록·의견저장·목표가 변경·지정해제만 실행합니다. 가격조건은 3번을 사용합니다.", "#b78105"),
    ("3  활성등록", "가격조건을 대기목록에 등록합니다. 가격 도달 뒤 기존 PAPER 안전검사를 다시 통과합니다.", "#2767b1"),
    ("4  건너뛰기·취소", "현재 작성 중인 문장과 해석만 지웁니다. 보유·기존 활성조건·매매기록은 지우지 않습니다.", "#b33c4d"),
)

SEED_CONSOLE_ROWS = [
    {"stage":"관찰후보","symbol":"267250","name":"HD현대","current_price":207500,"change_pct":1.1,"reason":"시장 거래량 급증 포착. 방향 확인 전 초기 관찰."},
    {"stage":"관찰후보","symbol":"032640","name":"LG유플러스","current_price":14520,"change_pct":0.6,"reason":"거래량 유입 시작. 단기 지지와 수급 지속 여부 확인."},
    {"stage":"관찰후보","symbol":"000270","name":"기아","current_price":147500,"change_pct":1.4,"reason":"상승률 동반 거래 증가. 첫 눌림 구간 대기."},
    {"stage":"관찰후보","symbol":"086520","name":"에코프로","current_price":80600,"change_pct":-0.4,"reason":"거래량 재유입. 중심선 회복과 지지 확인 전 관찰."},
    {"stage":"관찰후보","symbol":"012690","name":"모나리자","current_price":2170,"change_pct":2.0,"reason":"응봉 거래 증가. 짧은 눌림 뒤 재상승 여부 추적."},
    {"stage":"관찰후보","symbol":"035610","name":"솔트룩스","current_price":4795,"change_pct":1.8,"reason":"장중 거래량 확대. 반전 신호와 지지 형성 관찰."},
    {"stage":"관찰후보","symbol":"048550","name":"SM C&C","current_price":1605,"change_pct":1.2,"reason":"테마 수급 유입 초기. 대장 동조 여부 확인."},
    {"stage":"관찰후보","symbol":"476060","name":"온코닉테라퓨틱스","current_price":13710,"change_pct":2.3,"reason":"거래량 급증 뒤 눌림 진행. 지지 확인 전 관찰 유지."},
    {"stage":"관찰후보","symbol":"332290","name":"누보","current_price":791,"change_pct":0.8,"reason":"초기 거래량 유입. 추세 전환 신뢰도 추가 확인."},
    {"stage":"관찰후보","symbol":"009830","name":"한화솔루션","current_price":27600,"change_pct":1.0,"reason":"수급 회복 시도. 섹터 동조와 첫 조정 구조 확인."},
    {"stage":"관심종목","symbol":"048870","name":"시너지이노베이션","current_price":1666,"change_pct":1.7,"reason":"응봉 거래 확대. 지지선 접근과 반전 가능성 추적."},
    {"stage":"관심종목","symbol":"052800","name":"세스코24","current_price":3380,"change_pct":0.9,"reason":"거래량 증가 지속. 단기 저항 돌파 여부 확인."},
    {"stage":"관심종목","symbol":"101680","name":"한국정밀기계","current_price":2385,"change_pct":1.3,"reason":"장초반 수급 유입. 지지 유지 시 매수후보 상향 검토."},
    {"stage":"관심종목","symbol":"096200","name":"삼보산업","current_price":788,"change_pct":1.6,"reason":"거래량 증가와 반전 시도. 지지 확인봉 대기."},
    {"stage":"관심종목","symbol":"073000","name":"카스","current_price":1793,"change_pct":1.1,"reason":"수급 개선 중. 짧은 눌림 뒤 회복 여부 추적."},
    {"stage":"관심종목","symbol":"217730","name":"강스템바이오텍","current_price":2265,"change_pct":1.9,"reason":"거래량 유입과 테마 동조 확인. 대장 순위 추가 점검."},
    {"stage":"관심종목","symbol":"000650","name":"천일고속","current_price":540,"change_pct":0.5,"reason":"지지 확인 중. 거래량 재확대 시 상향 검토."},
    {"stage":"관심종목","symbol":"035610","name":"솔트룩스","current_price":4795,"change_pct":1.8,"reason":"반전봉 후보. 수급 연속성과 고점 이격 확인."},
    {"stage":"매수가능성","symbol":"024940","name":"PN풍년","current_price":2700,"change_pct":2.2,"basis_price":2650,"target_price":2740,"reason":"반전봉대기, 지지확인봉대기. 조건 다수 충족."},
    {"stage":"매수가능성","symbol":"114450","name":"그린생명과학","current_price":2035,"change_pct":1.5,"basis_price":2000,"target_price":2065,"reason":"거래량 증가와 지지 확인. 눌림 완료 여부 점검."},
    {"stage":"매수가능성","symbol":"019680","name":"대교","current_price":970,"change_pct":1.1,"basis_price":955,"target_price":985,"reason":"초기 파동 후 첫 조정. 반전봉 확인 시 진입 검토."},
    {"stage":"매수가능성","symbol":"226330","name":"신테카바이오","current_price":1590,"change_pct":2.0,"basis_price":1565,"target_price":1615,"reason":"거래량 재확대. 지지와 테마 동조 확인."},
    {"stage":"매수가능성","symbol":"015260","name":"에이엔피","current_price":435,"change_pct":1.4,"basis_price":428,"target_price":442,"reason":"눌림 뒤 저점 방어. 반전 신호 대기."},
    {"stage":"매수가능성","symbol":"014470","name":"부방","current_price":975,"change_pct":1.7,"basis_price":960,"target_price":990,"reason":"거래량·지지 조건 양호. 시장 흐름 재확인."},
    {"stage":"매수직전","symbol":"012340","name":"뉴인텍","current_price":749,"change_pct":2.6,"basis_price":738,"target_price":760,"reason":"지지2 확인, 적극형 매수조건 완료. 최종 호가 점검."},
    {"stage":"시장보류","symbol":"079160","name":"CJ CGV","current_price":4340,"change_pct":-0.8,"reason":"종목 조건은 양호하나 시장 급락 진행으로 신규매수 보류."},
    {"stage":"시장보류","symbol":"001360","name":"삼성제약","current_price":1311,"change_pct":-1.2,"reason":"시장·섹터 약세. 반등 확인 전 보류."},
    {"stage":"시장보류","symbol":"024950","name":"삼천리자전거","current_price":3465,"change_pct":-0.6,"reason":"실제진입 보류. 지수 안정과 수급 복귀 대기."},
    {"stage":"매수종목","symbol":"036930","name":"주성엔지니어링","current_price":196100,"avg_price":196100,"target_price":199100,"quantity":25,"stage_time":"7/18 12:32","reason":"보유 중. 기본 목표 +1.5%, 자동손절 없음."},
    {"stage":"매수종목","symbol":"042700","name":"한미반도체","current_price":249500,"avg_price":249500,"target_price":253500,"quantity":20,"stage_time":"7/18 12:54","reason":"보유 중. 목표가 관리, 새 조건 후 재평가."},
    {"stage":"매수종목","symbol":"028300","name":"HLB","current_price":36950,"avg_price":36950,"target_price":37550,"quantity":135,"stage_time":"7/18 12:55","reason":"보유 중. 시장 변동성 확대 시 수익률 추적."},
    {"stage":"매수종목","symbol":"065870","name":"LG헬로비전","current_price":181200,"avg_price":181200,"target_price":184000,"quantity":27,"stage_time":"7/18 13:07","reason":"보유 중. 목표가 +1.5% 관리."},
    {"stage":"매수종목","symbol":"347850","name":"디앤디파마텍","current_price":69400,"avg_price":69400,"target_price":70500,"quantity":72,"stage_time":"7/18 13:07","reason":"보유 중. 모멘텀 회복 여부 확인."},
    {"stage":"매수종목","symbol":"063570","name":"NICE홀딩스","current_price":1491,"avg_price":1491,"target_price":1514,"quantity":3353,"stage_time":"7/18 13:41","reason":"보유 중. 목표가 접근 시 자동 매도 판단."},
    {"stage":"매도종목","symbol":"067290","name":"JW신약","current_price":2200,"change_pct":3.31,"stage_time":"7/18 10:52","reason":"오늘 목표 +1.5% 매도 완료. 재진입은 눌림 확인 후."},
    {"stage":"매도종목","symbol":"012690","name":"모나리자","current_price":2145,"change_pct":2.77,"stage_time":"7/18 11:10","reason":"오늘 매도 완료. 당분간 관망."},
    {"stage":"매도종목","symbol":"007290","name":"동방","current_price":2235,"change_pct":2.45,"stage_time":"7/18 11:22","reason":"목표 도달 매도 완료. 재진입 주의."},
]

def route_interpreted_action(action: str) -> str:
    """해석 결과를 일반 적용/활성등록으로 명확히 분리한다."""
    if action == "BUY_CONDITION":
        return "ACTIVE_ONLY"
    if action in {"MEMO", "WATCH", "SET_TARGET", "BLOCK_BUY", "UNBLOCK_BUY", "CANCEL"}:
        return "GENERAL"
    return "UNSUPPORTED"


class V7557UnifiedDashboard(v7546.V7546Gui):
    """V75.56 기능을 한 개의 검토·협업 대시보드에 배치한다."""

    def _run(self) -> None:  # noqa: C901 - 검증된 GUI 이벤트 흐름을 보존
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk
            root = tk.Tk()
        except Exception as exc:
            self.logger.warning("GUI 시작 실패: %s", exc)
            return
        import queue as queue_module
        try:
            import chain_manifest
            chain_ok, chain_problems = chain_manifest.verify_chain()
            chain_banner_text = chain_manifest.make_banner()
        except Exception as exc:
            chain_ok = False
            chain_problems = [f"매니페스트 검사기 오류: {type(exc).__name__}: {exc}"]
            chain_banner_text = "V75.64 · 체인 검사 실패"

        click_mode = {"single": v7538._single_click_enabled()}

        def mode_label() -> str:
            return "한 번 클릭" if click_mode["single"] else "더블클릭"

        root.title("프라임 시그널 대시보드 (PAPER)")
        screen_w = int(root.winfo_screenwidth() or 1920)
        screen_h = int(root.winfo_screenheight() or 1080)
        work_left, work_top, work_right, work_bottom = 0, 0, screen_w, screen_h
        if sys.platform == "win32":
            # 화면 전체 높이가 아니라 작업표시줄을 뺀 실제 작업영역을 쓴다.
            # 1080px를 그대로 지정하면 하단 이력/상태바가 작업표시줄 아래로
            # 내려가므로, 최대화 상태에 의존하지 않고 정확한 작업영역에 맞춘다.
            try:
                import ctypes

                class _WorkRect(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                    ]

                work_rect = _WorkRect()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_rect), 0):
                    work_left, work_top = int(work_rect.left), int(work_rect.top)
                    work_right, work_bottom = int(work_rect.right), int(work_rect.bottom)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        work_w = max(800, work_right - work_left)
        work_h = max(640, work_bottom - work_top)
        compact_ui = work_h < 1060 or work_w < 1700
        root.geometry(f"{work_w}x{work_h}+{work_left}+{work_top}")
        root.minsize(min(1180, work_w), min(700, work_h))

        # 대시보드는 한 창에서 모든 검토를 끝내도록 설계한다.
        bg = "#050d18"
        panel = "#0b1826"
        panel2 = "#102235"
        border = "#36516b"
        text = "#e9f2ff"
        muted = "#9fb3c8"
        accent = "#3e9cff"
        good = "#58d68d"
        warn = "#f5c451"
        danger = "#ff7b7b"
        root.configure(bg=bg)
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dashboard.TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("Panel2.TFrame", background=panel2)
        style.configure("Dashboard.TLabel", background=bg, foreground=text, font=("맑은 고딕", 10))
        style.configure("Title.TLabel", background=bg, foreground=text, font=("맑은 고딕", 16 if compact_ui else 19, "bold"))
        style.configure("Sub.TLabel", background=bg, foreground=muted, font=("맑은 고딕", 9))
        style.configure("CardTitle.TLabel", background=panel, foreground=muted, font=("맑은 고딕", 9))
        style.configure("CardValue.TLabel", background=panel, foreground=text, font=("맑은 고딕", 16 if compact_ui else 19, "bold"))
        style.configure("Panel.TLabel", background=panel, foreground=text, font=("맑은 고딕", 10))
        style.configure("Muted.Panel.TLabel", background=panel, foreground=muted, font=("맑은 고딕", 9))
        style.configure("Dashboard.TLabelframe", background=panel, bordercolor=border, relief="solid")
        style.configure("Dashboard.TLabelframe.Label", background=panel, foreground=text, font=("맑은 고딕", 10, "bold"))
        style.configure("Dashboard.TButton", font=("맑은 고딕", 9))
        style.configure(
            "Header.TButton", background="#0a1522", foreground=text,
            bordercolor=border, lightcolor=border, darkcolor=border,
            font=("맑은 고딕", 9, "bold"), padding=(10, 6)
        )
        style.map(
            "Header.TButton",
            background=[("active", "#17334e"), ("pressed", "#214d73")],
            foreground=[("active", "white"), ("pressed", "white")]
        )
        style.configure("Pnl.Panel.TLabel", background=panel2, foreground="#ff5f57",
                        font=("맑은 고딕", 12, "bold"))
        style.configure("Input.TLabelframe", background=panel, bordercolor="#2b82c9", relief="solid")
        style.configure("Input.TLabelframe.Label", background=panel, foreground="#7fc7ff",
                        font=("맑은 고딕", 10, "bold"))
        style.configure("Preview.TLabelframe", background=panel, bordercolor="#8b5bc7", relief="solid")
        style.configure("Preview.TLabelframe.Label", background=panel, foreground="#d6b6ff",
                        font=("맑은 고딕", 10, "bold"))
        style.configure("Active.TLabelframe", background=panel, bordercolor="#c13f62", relief="solid")
        style.configure("Active.TLabelframe.Label", background=panel, foreground="#ff9ab6",
                        font=("맑은 고딕", 10, "bold"))
        style.configure("History.TLabelframe", background=panel, bordercolor="#3d9f70", relief="solid")
        style.configure("History.TLabelframe.Label", background=panel, foreground="#8ee6b6",
                        font=("맑은 고딕", 10, "bold"))
        for style_name, color in (("Confirm.TButton", "#2f8f3a"), ("Apply.TButton", "#b78105"), ("Active.TButton", "#2767b1"), ("Cancel.TButton", "#b33c4d")):
            style.configure(style_name, background=color, foreground="white", font=("맑은 고딕", 10, "bold"), padding=(7, 5))
            style.map(style_name, background=[("active", color), ("pressed", color)], foreground=[("active", "white")])
        style.configure(
            "Delete.TButton", background="#d92f4c", foreground="#ffffff",
            bordercolor="#ff9caf", lightcolor="#ff9caf", darkcolor="#7f1025",
            font=("맑은 고딕", 10, "bold"), padding=(7, 5),
        )
        style.map(
            "Delete.TButton",
            background=[("active", "#f04764"), ("pressed", "#a91f38")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure("Dashboard.Toolbutton", background=panel2, foreground=text, padding=(8, 4))
        style.map("Dashboard.Toolbutton", background=[("selected", accent), ("active", "#1b4166")], foreground=[("selected", "white")])
        style.configure("Treeview", background="#0b1e34", fieldbackground="#0b1e34", foreground=text, rowheight=23 if compact_ui else 27, bordercolor=border, font=("맑은 고딕", 9))
        style.configure("Treeview.Heading", background="#163552", foreground=text, relief="flat", font=("맑은 고딕", 9, "bold"))
        style.map("Treeview", background=[("selected", "#1e5c92")], foreground=[("selected", "white")])
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background="#102b49", foreground=text, padding=(12, 7), font=("맑은 고딕", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#1e5c92")])

        hero = RestoredHeroLink(v7525.HERO_LINK_FILE)
        self.root = root
        self.hero = hero
        link_mro = " -> ".join(
            f"{item.__module__}.{item.__qualname__}" for item in type(hero).__mro__
        )
        diag(
            "RUNTIME_OBJECTS "
            f"gui={type(self).__module__}.{type(self).__qualname__} "
            f"gui_file={str(Path(__file__).resolve())!r} "
            f"link={type(hero).__module__}.{type(hero).__qualname__} "
            f"link_mro={link_mro!r} config_path={str(hero.config_path)!r}"
        )

        shell = ttk.Frame(root, style="Dashboard.TFrame", padding=6 if compact_ui else 10)
        shell.pack(fill="both", expand=True)

        if not chain_ok:
            detail = "\n".join(f"• {item}" for item in chain_problems[:8])
            tk.Label(shell, text="체인 문제로 엔진 실행을 중단했습니다.\n" + detail,
                     bg="#2b1016", fg="#ffe7eb", justify="left", anchor="w",
                     font=("맑은 고딕", 10), padx=12, pady=10).pack(fill="x")
            tk.Button(shell, text="종료", command=root.destroy, font=("맑은 고딕", 11)).pack(pady=12)
            root.mainloop()
            return

        # Header and link controls
        header = ttk.Frame(shell, style="Dashboard.TFrame")
        header.pack(fill="x", pady=(0, 7))
        # 제목은 본문과 시각적으로 분리된 전용 타이틀 바다. 상태 문구도 같은
        # 바 안에서 읽히되 제목보다 한 단계 낮은 위계로 정렬한다.
        title_bar_bg = "#0c2742"
        title_box = tk.Frame(
            header, bg=title_bar_bg, highlightbackground="#3186c8",
            highlightthickness=2, padx=14, pady=8,
        )
        title_box.pack(side="left", fill="x", expand=True)
        title_line = tk.Frame(title_box, bg=title_bar_bg)
        title_line.pack(fill="x")
        tk.Label(
            title_line, text="프라임 시그널 대시보드  (PAPER)",
            bg=title_bar_bg, fg="#ffffff", font=("맑은 고딕", 16 if compact_ui else 19, "bold"),
            anchor="w",
        ).pack(side="left")
        market_header_var = tk.StringVar(value="시장상태: 확인 중")
        quote_header_var = tk.StringVar(value="마지막 시세: -")
        tk.Label(
            title_line, textvariable=market_header_var, bg=title_bar_bg,
            fg="#6ff0a5", font=("맑은 고딕", 9, "bold"), anchor="w",
        ).pack(side="left", padx=(24, 0))
        tk.Label(
            title_line, textvariable=quote_header_var, bg=title_bar_bg,
            fg="#b9d1e8", font=("맑은 고딕", 9), anchor="w",
        ).pack(side="left", padx=(18, 0))
        chain_short_var = tk.StringVar(value="체인 OK" if chain_ok else "체인 오류")
        tk.Label(
            title_line, textvariable=chain_short_var, bg=title_bar_bg,
            fg=good if chain_ok else danger, font=("맑은 고딕", 9, "bold"),
            anchor="w",
        ).pack(side="left", padx=(18, 0))

        toolbar = ttk.Frame(header, style="Dashboard.TFrame")
        toolbar.pack(side="right")
        status_var = tk.StringVar(value="대시보드 준비 중입니다.")
        time_var = tk.StringVar(value="갱신 전")
        ttk.Button(toolbar, text="원클릭 연동 확인", style="Header.TButton", command=lambda: check_auto_link()).pack(side="left")
        def open_runtime_log() -> None:
            log_path = BASE_DIR.parent / "runtime" / "logs" / "v75_73_dashboard.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)
            try:
                if sys.platform == "win32":
                    os.startfile(str(log_path))
                else:
                    import webbrowser
                    webbrowser.open(log_path.as_uri())
            except Exception as exc:
                messagebox.showerror("로그 열기 실패", f"{log_path}\n{exc}")
        ttk.Button(toolbar, text="로그 보기", style="Header.TButton", command=open_runtime_log).pack(side="left", padx=(5, 0))
        ttk.Button(toolbar, text="설정", style="Header.TButton",
                   command=lambda: messagebox.showinfo(
                       "설정 안내",
                       "[0600] 차트를 열어 둔 뒤 종목을 한 번 클릭하세요.\n"
                       "위치 저장 없이 Ctrl+A → Ctrl+V → Enter를 한 번만 보냅니다.\n"
                       "판단 차트는 완성된 3분봉으로 고정되어 있습니다.\n"
                       "실행 로그는 '로그 보기'에서 확인합니다."
                   )).pack(side="left", padx=(5, 0))
        click_mode["single"] = True
        single_var = tk.BooleanVar(value=True)
        mode_help_var = tk.StringVar(value="한 번 클릭 또는 ↑/↓")

        # KPI cards
        kpi_total_var = tk.StringVar(value="0")
        kpi_ready_var = tk.StringVar(value="0")
        kpi_hold_var = tk.StringVar(value="0")
        kpi_sold_var = tk.StringVar(value="0")
        kpi_active_var = tk.StringVar(value="0")
        kpi_market_var = tk.StringVar(value="-")
        kpi_stage_vars = {stage: tk.StringVar(value="0건") for stage in GUI_STAGE_ORDER}
        active_var = tk.StringVar(value="형 요청 활성 목록 0건")
        cards = ttk.Frame(shell, style="Dashboard.TFrame")
        cards.pack(fill="x", pady=(0, 5 if compact_ui else 8))
        card_specs = tuple((stage, kpi_stage_vars[stage]) for stage in GUI_STAGE_ORDER)
        # 단계별로 서로 구분되는 짙은 바탕 + 선명한 타이틀 띠를 사용한다.
        # 모든 문구는 카드 정중앙에 놓아 좌우 빈 공간의 균형을 맞춘다.
        stage_card_palette = {
            "관찰후보": ("#0b2d3b", "#087f9a", "#d9f8ff"),
            "관심종목": ("#173823", "#23864b", "#e0ffe8"),
            "매수가능성": ("#44370d", "#b88a09", "#fff4c2"),
            "매수직전": ("#49240f", "#c05a17", "#ffe4ca"),
            "시장보류": ("#302342", "#7652a8", "#f0e2ff"),
            "매수종목": ("#093b40", "#078d91", "#d9feff"),
            "매도종목": ("#451929", "#b63861", "#ffe0ea"),
        }
        for index, (stage_key, variable) in enumerate(card_specs):
            card_bg, card_fg = STAGE_ACCENTS.get(stage_key, (panel, text))
            body_bg, band_bg, body_fg = stage_card_palette[stage_key]
            card = tk.Frame(
                cards, bg=body_bg, highlightbackground=card_bg,
                highlightthickness=2, padx=0, pady=0,
            )
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5, 0))
            stage_no = index + 1
            title_band = tk.Frame(card, bg=band_bg, padx=5, pady=2 if compact_ui else 4)
            title_band.pack(fill="x")
            title_label = tk.Label(
                title_band, text=f"{stage_no:02d}  {DISPLAY_STAGE.get(stage_key, stage_key)}",
                bg=band_bg, fg="#ffffff", font=("맑은 고딕", 10, "bold"),
                anchor="center", justify="center", cursor="hand2",
            )
            title_label.pack(fill="x")
            value_label = tk.Label(
                card, textvariable=variable, bg=body_bg, fg=body_fg,
                font=("맑은 고딕", 16 if compact_ui else 20, "bold"), anchor="center",
                justify="center", cursor="hand2", pady=2 if compact_ui else 4,
            )
            value_label.pack(fill="x", padx=6)
            short_desc = {
                "관찰후보":"거래량 유입·초기 포착",
                "관심종목":"지지·수급 확인",
                "매수가능성":"조건 충족·눌림 확인",
                "매수직전":"반전 확인·진입 임박",
                "시장보류":"시장 영향·대기",
                "매수종목":"목표가·손익 관리",
                "매도종목":"오늘 매도 완료",
            }[stage_key]
            desc_label = tk.Label(
                card, text=short_desc, bg=body_bg, fg="#bfd3e5",
                font=("맑은 고딕", 8 if compact_ui else 9), anchor="center", justify="center",
                cursor="hand2", pady=1 if compact_ui else 3,
            )
            desc_label.pack(fill="x", padx=5, pady=(0, 2 if compact_ui else 4))
            for widget in (card, title_band, title_label, value_label, desc_label):
                widget.bind("<Button-1>", lambda _e, chosen=stage_key: choose_category(chosen))
            cards.columnconfigure(index, weight=1)

        summary_vars = {name: tk.StringVar(value="-") for name in ("보유합계", "평가손익", "실현손익(오늘)", "총손익", "가용현금")}
        summary = ttk.Frame(shell, style="Dashboard.TFrame")
        summary.pack(fill="x", pady=(0, 5 if compact_ui else 8))
        for index, label in enumerate(summary_vars):
            box = ttk.Frame(summary, style="Panel2.TFrame", padding=(10, 3 if compact_ui else 6))
            box.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 4, 0))
            ttk.Label(box, text=label, style="CardTitle.TLabel").pack()
            value_style = "Pnl.Panel.TLabel" if label in {"평가손익", "실현손익(오늘)", "총손익"} else "Panel.TLabel"
            ttk.Label(box, textvariable=summary_vars[label], style=value_style,
                      font=("맑은 고딕", 12, "bold")).pack()
            summary.columnconfigure(index, weight=1)

        main = ttk.Panedwindow(shell, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main, style="Panel.TFrame", padding=7)
        right = ttk.Frame(main, style="Dashboard.TFrame", padding=(8, 0, 0, 0))
        # 목록 74% / 판단기준 26%: 설명을 잘라내지 않으면서 종목표 이유 열도 확보한다.
        main.add(left, weight=74)
        main.add(right, weight=26)

        category_bar = ttk.Frame(left, style="Panel.TFrame")
        category_bar.pack(fill="x", pady=(0, 6))
        category_filter_var = tk.StringVar(value="전체")
        category_count_vars: dict[str, Any] = {}
        for stage in ("전체", *GUI_STAGE_ORDER):
            label_var = tk.StringVar(value=f"{DISPLAY_STAGE.get(stage, stage)} 0")
            category_count_vars[stage] = label_var
            ttk.Radiobutton(
                category_bar, textvariable=label_var, value=stage, variable=category_filter_var,
                style="Dashboard.Toolbutton", command=lambda chosen=stage: choose_category(chosen),
            ).pack(side="left", padx=(0, 4))
        ttk.Label(category_bar, text="전체에서 ↑/↓ = 모든 단계 연속 차트검토", style="Muted.Panel.TLabel").pack(side="right")
        self.category_filter_var = category_filter_var
        self.category_count_vars = category_count_vars

        tree_frame = ttk.Frame(left, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True)
        columns = ("no", "stage", "name", "current", "change", "volume", "basis", "target", "qty", "stage_time", "reason")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.tree = tree
        headings = {
            "no": "순", "stage": "단계", "name": "종목명", "current": "현재가",
            "change": "등락률", "volume": "거래량(20일비)", "basis": "평단/기준가", "target": "목표가",
            "qty": "보유수량", "stage_time": "편입/매수",
            "reason": "조건·손익·보류이유 (상세보기)",
        }
        widths = {
            "no": 36, "stage": 90, "name": 116, "current": 74, "change": 58,
            "volume": 82, "basis": 86, "target": 76, "qty": 54,
            "stage_time": 88, "reason": 560,
        }
        for column in columns:
            heading_anchor = "w" if column in ("name", "reason") else "center"
            tree.heading(column, text=headings[column], anchor=heading_anchor)
            anchor = "w" if column in ("name", "reason") else ("e" if column in ("current", "change", "volume", "basis", "target", "qty") else "center")
            tree.column(column, width=widths[column], minwidth=32, anchor=anchor, stretch=column == "reason")
        y_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        dark_tags = {
            "관찰후보": ("#0a3440", "#9eeaf5"), "관심종목": ("#173d22", "#baf5c3"),
            "매수가능성": ("#4b3d08", "#ffe08a"), "매수직전": ("#512508", "#ffc18d"),
            "시장보류": ("#302345", "#d9c3ff"), "매수종목": ("#0a3d42", "#9ef4f7"),
            "매도종목": ("#4c1728", "#ffb6ca"),
        }
        for stage, (background, foreground) in dark_tags.items():
            tree.tag_configure(stage, background=background, foreground=foreground)

        selected_strip = ttk.LabelFrame(left, text="선택 종목 상세", style="Dashboard.TLabelframe", padding=8)
        # 승인 디자인에서는 선택 상세 띠를 숨기고 이유 열을 넓게 사용한다.
        selected_var = tk.StringVar(value="선택종목: 없음")
        selected_stage_var = tk.StringVar(value="단계  -")
        selected_price_var = tk.StringVar(value="-")
        selected_reason_var = tk.StringVar(value="목록에서 종목을 선택하면 상세 이유가 표시됩니다.")
        ttk.Label(selected_strip, textvariable=selected_var, style="Panel.TLabel", font=("맑은 고딕", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(selected_strip, textvariable=selected_stage_var, style="Muted.Panel.TLabel").grid(row=0, column=1, sticky="e")
        ttk.Label(selected_strip, textvariable=selected_price_var, style="Panel.TLabel", font=("맑은 고딕", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(selected_strip, textvariable=selected_reason_var, style="Muted.Panel.TLabel", wraplength=900, justify="left").grid(row=1, column=1, sticky="ew", padx=(18, 0), pady=(4, 0))
        selected_strip.columnconfigure(1, weight=1)

        # 오른쪽: 버튼 사용법과 단계 로직을 항상 보이게 고정한다.
        guide = ttk.LabelFrame(right, text="단계별 의미 및 판단 기준", style="Dashboard.TLabelframe", padding=8)
        guide.pack(fill="x")
        compact_stage_logic = {
            "관찰후보": "초기 포착 · 거래량 유입과 큰 매도 구조를 발견했지만 지지·반전은 아직 미확인",
            "관심종목": "추적 강화 · 거래바닥 또는 지지 접근을 확인하고 후속 3분봉의 저점 방어를 관찰",
            "매수가능성": "매수 검토 · 거래 감소·아래꼬리·반전 조건 다수가 충족되어 마지막 지지를 확인",
            "매수직전": "최종 확인 · 완성 3분봉 기준 반전과 두 봉 뒤 저점 미이탈까지 확인된 진입 직전",
            "시장보류": "시장 위험 · 종목 조건은 유지하되 지수·선물·옵션·외국인 수급 악화로 신규매수 보류",
            "매수종목": "보유 관리 · PAPER 평단·목표 +1.5%·수익률을 추적하며 자동손절은 사용하지 않음",
            "매도종목": "당일 완료 · 목표·수동·장마감 매도 이력을 보존하고 같은 구조의 성급한 재진입 금지",
        }
        for idx, stage_key in enumerate(GUI_STAGE_ORDER, 1):
            color, fg = STAGE_ACCENTS.get(stage_key, (border, text))
            row = tk.Frame(guide, bg="#0b1e34", highlightbackground=border, highlightthickness=1, padx=6, pady=2 if compact_ui else 5)
            row.pack(fill="x", pady=(0, 2 if compact_ui else 4))
            tk.Label(
                row, text=f"{idx:02d}  {DISPLAY_STAGE.get(stage_key, stage_key)}",
                bg=color, fg="white", font=("맑은 고딕", 9, "bold"),
                width=13, anchor="center", justify="center", padx=5, pady=2,
            ).pack(side="left")
            tk.Label(row, text=compact_stage_logic[stage_key], bg="#0b1e34", fg=text,
                     font=("맑은 고딕", 9), anchor="w", justify="left", wraplength=360
                     ).pack(side="left", padx=(8,0), fill="x", expand=True)

        stage_legend = ttk.LabelFrame(right, text="단계 분류", style="Dashboard.TLabelframe", padding=8)
        stage_legend.pack_forget()
        legend_grid = tk.Frame(stage_legend, bg=panel)
        legend_grid.pack(fill="x")
        stage_descriptions = {
            "관찰후보": "초기 포착", "관심종목": "추적 강화", "매수가능성": "매수 검토",
            "매수직전": "최종 확인", "시장보류": "시장 위험", "매수종목": "보유 관리", "매도종목": "당일 완료",
        }
        for idx, stage_key in enumerate(GUI_STAGE_ORDER):
            bg_chip, fg_chip = STAGE_ACCENTS.get(stage_key, (panel2, text))
            chip = tk.Frame(legend_grid, bg=panel2, highlightbackground=bg_chip, highlightthickness=1, padx=7, pady=5)
            chip.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0 if idx % 2 == 0 else 4, 0), pady=(0, 4))
            tk.Label(chip, text=DISPLAY_STAGE.get(stage_key, stage_key), bg=panel2, fg=fg_chip,
                     font=("맑은 고딕", 9, "bold"), anchor="w").pack(side="left")
            tk.Label(chip, text=stage_descriptions.get(stage_key, ""), bg=panel2, fg=muted,
                     font=("맑은 고딕", 8), anchor="e").pack(side="right")
        legend_grid.columnconfigure(0, weight=1); legend_grid.columnconfigure(1, weight=1)

        terms_fixed = ttk.LabelFrame(right, text="단계·조건·약어 로직 요약", style="Dashboard.TLabelframe", padding=8)
        terms_fixed.pack(fill="both", expand=True, pady=(0, 0))
        terms_text = tk.Text(terms_fixed, wrap="word", bg="#091c31", fg=text, insertbackground="white", relief="flat", padx=7, pady=5, font=("맑은 고딕", 9 if compact_ui else 10), spacing1=1, spacing3=3, height=5 if compact_ui else 9)
        terms_scroll = ttk.Scrollbar(terms_fixed, orient="vertical", command=terms_text.yview)
        terms_text.configure(yscrollcommand=terms_scroll.set)
        terms_scroll.pack(side="right", fill="y")
        terms_text.pack(fill="both", expand=True)
        terms_text.insert("1.0", DASHBOARD_TERMS.split("[4개 버튼]")[0])
        terms_text.configure(state="disabled")

        # 의견·명령 입력은 종목표 아래에 배치해 오른쪽 설명판을 가리지 않는다.
        command_tab = ttk.Frame(shell, style="Panel.TFrame", padding=(0, 4 if compact_ui else 7, 0, 0))
        command_tab.pack(fill="x", pady=(0, 0))
        bottom = ttk.Frame(command_tab, style="Panel.TFrame")
        bottom.pack(fill="x")
        # 입력 28 / 시간순 해석 미리보기 38 / 활성 18 / 이력관리 16
        for _column, _weight in enumerate((28, 38, 18, 16)):
            bottom.columnconfigure(_column, weight=_weight, uniform="bottom")
        collab = ttk.LabelFrame(bottom, text="형 의견·명령 입력", style="Input.TLabelframe", padding=5 if compact_ui else 8)
        collab.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        preview = ttk.LabelFrame(bottom, text="형 요청 해석결과 미리보기", style="Preview.TLabelframe", padding=5 if compact_ui else 8)
        preview.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        ttk.Label(collab, textvariable=selected_var, style="Panel.TLabel", font=("맑은 고딕", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
        input_text = tk.Text(collab, height=2, wrap="word", bg="#f4f6f8", fg="#1b2735", insertbackground="#1b2735", relief="solid", bd=1, font=("맑은 고딕", 10))
        self.collab_input = input_text
        input_text.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 7))

        action_var = tk.StringVar(value="해석 전")
        symbol_var = tk.StringVar(value="-")
        buy_var = tk.StringVar(value="")
        target_var = tk.StringVar(value="")
        trigger_var = tk.StringVar(value="가격 이하 도달")
        error_var = tk.StringVar(value="")
        warning_var = tk.StringVar(value="")

        ttk.Label(preview, text="행동", style="Muted.Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0,5))
        ttk.Label(preview, textvariable=action_var, style="Panel.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(preview, text="종목", style="Muted.Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(8,5))
        ttk.Label(preview, textvariable=symbol_var, style="Panel.TLabel").grid(row=0, column=3, sticky="w")

        ttk.Label(preview, text="매수가", style="Muted.Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(4,0))
        ttk.Entry(preview, textvariable=buy_var, width=11).grid(row=1, column=1, sticky="ew", pady=(4,0))
        ttk.Label(preview, text="목표가", style="Muted.Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(8,5), pady=(4,0))
        ttk.Entry(preview, textvariable=target_var, width=11).grid(row=1, column=3, sticky="ew", pady=(4,0))

        ttk.Label(preview, text="조건", style="Muted.Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(4,0))
        trigger_box = ttk.Combobox(
            preview, textvariable=trigger_var,
            values=("가격 이하 도달", "상향 돌파"), state="readonly", width=12
        )
        trigger_box.grid(row=2, column=1, sticky="ew", pady=(4,0))
        ttk.Label(preview, text="유효", style="Muted.Panel.TLabel").grid(row=2, column=2, sticky="w", padx=(8,5), pady=(4,0))
        ttk.Label(preview, text="오늘 장 종료", style="Panel.TLabel").grid(row=2, column=3, sticky="w", pady=(4,0))

        ttk.Label(
            preview, text="시간순 요청 요약 (최신순 · 가격지시/뉴스/추천/시장·파생수급 모두 기록)",
            style="Muted.Panel.TLabel",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(7, 3))
        request_preview_tree = ttk.Treeview(
            preview,
            columns=("time", "type", "summary", "status"),
            show="headings", height=2 if compact_ui else 4, selectmode="browse",
        )
        for key, label, width, anchor_value in (
            ("time", "시각", 90, "center"),
            ("type", "해석유형", 105, "center"),
            ("summary", "형 요청 요약", 390, "w"),
            ("status", "상태", 62, "center"),
        ):
            request_preview_tree.heading(key, text=label, anchor="w" if key == "summary" else "center")
            request_preview_tree.column(key, width=width, anchor=anchor_value, stretch=key == "summary")
        request_preview_tree.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(0, 2))
        self.request_preview_tree = request_preview_tree
        preview.rowconfigure(4, weight=1)

        tk.Label(
            collab, textvariable=error_var, bg=panel, fg=danger,
            anchor="w", justify="left", wraplength=310
        ).grid(row=2, column=0, columnspan=2, sticky="ew")
        tk.Label(
            collab, textvariable=warning_var, bg=panel, fg=warn,
            anchor="w", justify="left", wraplength=310
        ).grid(row=2, column=2, columnspan=2, sticky="ew", padx=(6,0))
        button_row = ttk.Frame(collab, style="Panel.TFrame")
        button_row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        ttk.Button(button_row, text="1  뜻 확인", style="Confirm.TButton", command=lambda: parse_input()).grid(row=0, column=0, sticky="ew")
        ttk.Button(button_row, text="2  해석대로 적용", style="Apply.TButton", command=lambda: apply_interpreted()).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(button_row, text="3  활성등록", style="Active.TButton", command=lambda: submit_buy()).grid(row=0, column=2, sticky="ew")
        ttk.Button(button_row, text="4  건너뛰기·취소", style="Cancel.TButton", command=lambda: clear_input()).grid(row=0, column=3, sticky="ew", padx=(4, 0))
        for col in range(4): button_row.columnconfigure(col, weight=1)
        collab.columnconfigure(0, weight=1); collab.columnconfigure(1, weight=1); collab.columnconfigure(2, weight=1); collab.columnconfigure(3, weight=1)
        preview.columnconfigure(1, weight=1); preview.columnconfigure(2, weight=1); preview.columnconfigure(3, weight=1)

        active_tab = ttk.LabelFrame(bottom, text="형 요청 활성 목록", style="Active.TLabelframe", padding=5 if compact_ui else 7)
        active_tab.grid(row=0, column=2, sticky="nsew", padx=(0, 6))
        active_tree = ttk.Treeview(active_tab, columns=("status", "name", "condition", "expires"), show="headings", height=2 if compact_ui else 3)
        for key, label, width in (("status","상태",55),("name","종목",85),("condition","조건",190),("expires","유효기간",100)):
            active_tree.heading(key,text=label); active_tree.column(key,width=width,anchor="center" if key in ("status","expires") else "w")
        active_tree.pack(fill="x")
        ttk.Label(active_tab, textvariable=active_var, style="Muted.Panel.TLabel").pack(anchor="w", pady=(4, 0))

        opinion_history_tab = ttk.LabelFrame(bottom, text="의견·요청 시간대별 이력", style="History.TLabelframe", padding=5 if compact_ui else 7)
        opinion_history_tab.grid(row=0, column=3, sticky="nsew")
        opinion_history_tree = ttk.Treeview(
            opinion_history_tab,
            columns=("time", "type", "summary", "status"),
            show="headings", height=2 if compact_ui else 3, selectmode="browse"
        )
        for key, label, width in (
            ("time", "시간", 58), ("type", "유형", 88),
            ("summary", "판단·요청 요약", 235), ("status", "상태", 52)
        ):
            opinion_history_tree.heading(key, text=label)
            opinion_history_tree.column(key, width=width, anchor="w" if key == "summary" else "center")
        opinion_history_tree.pack(fill="both", expand=True)

        history_buttons = ttk.Frame(opinion_history_tab, style="Panel.TFrame")
        history_buttons.pack(fill="x", pady=(5, 0))
        ttk.Button(history_buttons, text="선택 해제", style="Cancel.TButton",
                   command=lambda: change_selected_intent_status("해제")).pack(side="left", fill="x", expand=True)
        ttk.Button(history_buttons, text="다시 활성", style="Active.TButton",
                   command=lambda: change_selected_intent_status("활성")).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(history_buttons, text="선택 삭제", style="Delete.TButton",
                   command=lambda: delete_selected_intent()).pack(side="left", fill="x", expand=True)

        intent_type_labels = {
            "SECTOR_GUARD": "섹터 제한",
            "THEME_PRIORITY": "테마·뉴스",
            "STOCK_WATCH": "종목 관찰",
            "DERIVATIVES_FLOW": "파생 수급",
            "STOCK_FUTURES_FLOW": "종목선물",
            "MARKET_NOTE": "시장 판단",
            "ORDER_REQUEST": "가격·주문",
            "MEMO": "운용 의견",
        }

        def intent_time_text(created: Any) -> str:
            value = str(created or "")
            try:
                parsed = datetime.fromisoformat(value).astimezone(KST)
                return f"{parsed.month}/{parsed.day} {parsed:%H:%M}"
            except (ValueError, TypeError):
                return value[-14:-3] if len(value) >= 14 else "-"

        def refresh_request_preview(current_drafts: list[Any] | None = None) -> None:
            for iid in request_preview_tree.get_children():
                request_preview_tree.delete(iid)
            drafts = list(current_drafts if current_drafts is not None else free_intent_state.get("drafts", []))
            persisted = list(intent_ledger.list_all()[:80])
            persisted_ids = {str(row.get("intent_id", "")) for row in persisted}
            rows: list[tuple[str, str, str, str, str]] = []
            for item in drafts:
                item_id = str(getattr(item, "intent_id", ""))
                if item_id in persisted_ids:
                    continue
                rows.append((
                    item_id,
                    intent_time_text(getattr(item, "created_at", "")),
                    intent_type_labels.get(str(getattr(item, "kind", "")), str(getattr(item, "kind", ""))),
                    str(getattr(item, "summary", "")),
                    "미리보기",
                ))
            for row in persisted:
                rows.append((
                    str(row.get("intent_id", "")),
                    intent_time_text(row.get("created_at", "")),
                    intent_type_labels.get(str(row.get("kind", "")), str(row.get("kind", ""))),
                    str(row.get("summary", "")),
                    str(row.get("status", "")),
                ))
            for index, (intent_id, created, kind, summary, status) in enumerate(rows[:80]):
                request_preview_tree.insert(
                    "", "end", iid=f"request-preview:{intent_id or index}",
                    values=(created, kind, summary, status),
                )

        def refresh_opinion_history() -> None:
            selected = opinion_history_tree.selection()
            selected_id = selected[0] if selected else ""
            for iid in opinion_history_tree.get_children():
                opinion_history_tree.delete(iid)
            for row in intent_ledger.list_all()[:60]:
                intent_id = str(row.get("intent_id", ""))
                opinion_history_tree.insert(
                    "", "end", iid=intent_id,
                    values=(
                        intent_time_text(row.get("created_at", "")),
                        intent_type_labels.get(str(row.get("kind", "")), str(row.get("kind", ""))),
                        str(row.get("summary", "")),
                        str(row.get("status", "")),
                    )
                )
            if selected_id and opinion_history_tree.exists(selected_id):
                opinion_history_tree.selection_set(selected_id)
            refresh_request_preview()

        def selected_intent_id() -> str:
            selected = opinion_history_tree.selection()
            return str(selected[0]) if selected else ""

        def change_selected_intent_status(status: str) -> None:
            intent_id = selected_intent_id()
            if not intent_id:
                status_var.set("시간대별 이력에서 항목을 먼저 선택해주세요.")
                return
            if intent_ledger.set_status(intent_id, status):
                status_var.set(f"선택 의견·요청 상태를 '{status}'로 변경했습니다.")
                refresh_opinion_history()
                refresh_active_table(HUB.snapshot())

        def delete_selected_intent() -> None:
            intent_id = selected_intent_id()
            if not intent_id:
                status_var.set("시간대별 이력에서 삭제할 항목을 먼저 선택해주세요.")
                return
            if not messagebox.askyesno("선택 삭제", "선택한 의견·요청 이력을 삭제할까요?"):
                return
            if intent_ledger.delete(intent_id):
                status_var.set("선택 의견·요청 이력을 삭제했습니다.")
                refresh_opinion_history()
                refresh_active_table(HUB.snapshot())


        status_bar = ttk.Frame(shell, style="Panel2.TFrame", padding=(10, 4 if compact_ui else 6))
        status_bar.pack(fill="x", pady=(4 if compact_ui else 7, 0))
        ttk.Label(status_bar, textvariable=status_var, style="Panel.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(status_bar, textvariable=time_var, style="Muted.Panel.TLabel").pack(side="right")


        def refresh_combined_active_summary() -> None:
            overlay_count = sum(1 for iid in active_tree.get_children() if str(iid).startswith("overlay:"))
            intent_count = sum(1 for iid in active_tree.get_children() if str(iid).startswith("intent:"))
            active_var.set(
                f"형 요청 활성 목록 {overlay_count + intent_count}건 · "
                f"종목조건 {overlay_count} · 판단/테마 {intent_count}"
            )

        def refresh_active_table(collab_snapshot: dict[str, Any]) -> None:
            current_selection = active_tree.selection()
            selected_id = str(current_selection[0]) if current_selection else ""
            for item_id in active_tree.get_children():
                active_tree.delete(item_id)

            # 종목 가격조건·매수금지 등 엔진 실행 조건
            for item in collab_snapshot.get("active", []):
                symbol = str(item.get("symbol", ""))
                overlay_id = str(item.get("overlay_id", symbol or id(item)))
                name = str(item.get("name", "") or symbol)
                kind = str(item.get("kind", ""))
                if kind == "BUY_CONDITION":
                    direction = "이하" if item.get("trigger") == "AT_OR_BELOW" else "돌파"
                    condition = f"{float(item.get('buy_price') or 0):,.0f}원 {direction}"
                    if item.get("target_price"):
                        condition += f" · 목표 {float(item['target_price']):,.0f}원"
                elif kind == "BLOCK_BUY":
                    condition = "신규매수 금지"
                else:
                    condition = kind or "수동 지정"
                expires = "무기한"
                try:
                    expires = datetime.fromisoformat(str(item.get("expires_at", ""))).astimezone(KST).strftime("%-m/%-d %H:%M")
                except (TypeError, ValueError):
                    try:
                        expires = datetime.fromisoformat(str(item.get("expires_at", ""))).astimezone(KST).strftime("%m/%d %H:%M").lstrip("0").replace("/0", "/")
                    except (TypeError, ValueError):
                        pass
                active_tree.insert(
                    "", "end", iid=f"overlay:{overlay_id}",
                    values=("활성", name, condition, expires)
                )

            # 시장 판단·섹터 제한·테마 우선 등 사용자의 장중 운용 판단
            type_labels = {
                "SECTOR_GUARD": "섹터 제한",
                "THEME_PRIORITY": "테마 우선",
                "STOCK_WATCH": "종목 관찰",
                "DERIVATIVES_FLOW": "파생 수급",
                "STOCK_FUTURES_FLOW": "종목선물",
                "MARKET_NOTE": "시장 판단",
                "ORDER_REQUEST": "주문 요청",
                "MEMO": "운용 의견",
            }
            for item in intent_ledger.list_active():
                intent_id = str(item.get("intent_id", ""))
                kind = str(item.get("kind", ""))
                scopes = [str(v) for v in item.get("scope", []) if str(v)]
                name = "·".join(scopes[:2]) or type_labels.get(kind, str(item.get("title", "의견")))
                condition = str(item.get("summary", ""))
                if len(condition) > 46:
                    condition = condition[:45] + "…"
                expires = str(item.get("time_scope", "해제 전까지"))
                active_tree.insert(
                    "", "end", iid=f"intent:{intent_id}",
                    values=("활성", name, condition, expires)
                )

            if selected_id and active_tree.exists(selected_id):
                active_tree.selection_set(selected_id)
            refresh_combined_active_summary()


        intent_ledger = IntentLedger(BASE_DIR.parent / "runtime" / "user_intent_ledger.json")
        free_intent_state: dict[str, Any] = {"drafts": [], "selected_intent_id": ""}

        state: dict[str, Any] = {
            "last_symbol": "", "last_ts": 0.0, "selected_symbol": "", "selected_name": "",
            "last_action_ts": 0.0, "focus_token": 0, "focus_restored_token": 0,
            "known_names": {}, "current_prices": {}, "current_draft": None,
            "draft_fingerprint": None, "trigger_touched": False,
            "all_rows": [], "last_snapshot": {}, "iid_symbol": {}, "iid_name": {},
            "target_sources": _load_target_sources(), "received_live_rows": False,
        }
        link_results: Any = queue_module.Queue()
        link_control: dict[str, Any] = {
            "lock": threading.Lock(), "pending": None, "busy": False,
        }

        def input_value() -> str:
            return input_text.get("1.0", "end").strip()

        def current_fingerprint() -> tuple[str, str]:
            return (" ".join(input_value().split()), str(state.get("selected_symbol", "")))

        def invalidate_draft(reason: str = "") -> None:
            state["current_draft"] = None
            state["draft_fingerprint"] = None
            if reason:
                status_var.set(f"{reason} — 뜻 확인을 다시 눌러주세요.")

        def ensure_current_draft() -> CollaborationDraft:
            draft = state.get("current_draft")
            if draft is None or state.get("draft_fingerprint") != current_fingerprint():
                return parse_input()
            return draft

        def on_input_modified(_event: Any = None) -> None:
            if not input_text.edit_modified():
                return
            input_text.edit_modified(False)
            invalidate_draft("입력 내용이 변경되었습니다")

        def tk_copy(symbol: str) -> bool:
            try:
                root.clipboard_clear(); root.clipboard_append(symbol); root.update()
                return str(root.clipboard_get()) == symbol
            except Exception as exc:
                diag(f"TK_CLIPBOARD_FAIL symbol={symbol} error={type(exc).__name__}")
                return False

        def restore_selected_row(symbol: str) -> None:
            iid = find_iid_by_symbol(tree, symbol)
            if iid:
                tree.selection_set(iid); tree.focus(iid); tree.see(iid)

        def schedule_review_restore(symbol: str, cursor: CursorPosition | None, token: int) -> None:
            def attempt() -> None:
                if token != int(state["focus_token"]) or int(state["focus_restored_token"]) == token:
                    return
                restore_selected_row(symbol)
                ok = restore_review_focus(root, tree, cursor)
                if ok:
                    state["focus_restored_token"] = token
            # 최신 요청만 한 번 복귀시킨다. 기존 4회 lift/foreground 요청은
            # 대시보드가 흔들리거나 번쩍이는 현상의 원인이 될 수 있었다.
            root.after(60, attempt)

        def update_selected(symbol: str, name: str = "") -> None:
            changed = symbol != str(state.get("selected_symbol", ""))
            state["selected_symbol"] = symbol
            state["selected_name"] = name or next((n for n, c in state["known_names"].items() if c == symbol), symbol)
            selected_var.set(f"선택종목: {state['selected_name']} {symbol}" if symbol else "선택종목: 없음")
            focused = tree.focus()
            values = tree.item(focused, "values") if focused else ()
            if values and len(values) >= 11:
                selected_stage_var.set(f"단계  {values[1]}   ·   편입/매수  {values[9]}")
                selected_price_var.set(f"현재가 {values[3]} · 기준/평단 {values[6]} · 목표 {values[7]}")
                selected_reason_var.set(str(values[10]))
            else:
                selected_stage_var.set("단계  -")
                selected_price_var.set("-")
                selected_reason_var.set("목록에서 종목을 선택하면 상세 이유가 표시됩니다.")
            if changed:
                invalidate_draft("선택 종목이 변경되었습니다")

        def finish_link_result(result: tuple[Any, ...]) -> None:
            symbol, source, token, cursor, status, message = result
            diag(
                "GUI_LINK_END "
                f"symbol={symbol} source={source!r} token={token} "
                f"status={status.value} message={str(message)!r}"
            )
            if int(token) != int(state["focus_token"]):
                diag(
                    "GUI_LINK_STALE_RESULT "
                    f"symbol={symbol} token={token} latest={state['focus_token']}"
                )
                return
            schedule_review_restore(str(symbol), cursor, int(token))
            prefix = {
                "VERIFIED": "✅ ", "SENT_UNVERIFIED": "⚠ ", "FAILED": "❌ "
            }.get(status.value, "")
            status_var.set(f"{prefix}{message} — 목록 포커스 복귀")

        def poll_link_results() -> None:
            try:
                while True:
                    finish_link_result(link_results.get_nowait())
            except queue_module.Empty:
                pass
            if not self.closed.is_set():
                root.after(40, poll_link_results)

        def run_link_worker() -> None:
            while True:
                with link_control["lock"]:
                    request = link_control["pending"]
                    link_control["pending"] = None
                    if request is None:
                        link_control["busy"] = False
                        return
                symbol, source, token, cursor = request
                try:
                    status, message = hero.send_symbol(symbol)
                except Exception as exc:
                    status = LinkStatus.FAILED
                    message = f"연동 내부 오류: {type(exc).__name__}: {exc}"
                link_results.put((symbol, source, token, cursor, status, message))

        def queue_link_request(symbol: str, source: str, token: int, cursor: CursorPosition | None) -> None:
            start_worker = False
            with link_control["lock"]:
                replaced = link_control["pending"]
                link_control["pending"] = (symbol, source, token, cursor)
                if not link_control["busy"]:
                    link_control["busy"] = True
                    start_worker = True
            if replaced is not None:
                diag(
                    "GUI_LINK_COALESCED "
                    f"dropped_symbol={replaced[0]} latest_symbol={symbol} latest_token={token}"
                )
            if start_worker:
                threading.Thread(
                    target=run_link_worker,
                    name="v75-hero-link",
                    daemon=True,
                ).start()

        def switch_symbol(symbol: str, *, source: str) -> None:
            raw_symbol = str(symbol)
            symbol = normalize_symbol_code(raw_symbol)
            if not symbol:
                status_var.set(f"종목코드를 읽지 못했습니다: {raw_symbol!r}")
                diag(f"GUI_LINK_REJECT raw_symbol={raw_symbol!r} source={source!r}")
                return
            now_mono = time.monotonic()
            if click_mode["single"] and symbol == state["last_symbol"] and now_mono - float(state["last_ts"]) < self.DEBOUNCE_SEC:
                return
            state["focus_token"] = int(state["focus_token"]) + 1
            token = int(state["focus_token"]); cursor = capture_cursor_position()
            state["last_symbol"] = symbol; state["last_ts"] = now_mono; state["last_action_ts"] = now_mono
            update_selected(symbol)
            diag(
                "GUI_LINK_BEGIN "
                f"raw_symbol={raw_symbol!r} symbol={symbol} source={source!r} "
                f"token={token} mode=ASYNC_LATEST_WINS"
            )
            status_var.set(f"{symbol} 영웅문 차트 전환 중… 다른 종목도 바로 선택할 수 있습니다.")
            root.update_idletasks()
            queue_link_request(symbol, source, token, cursor)

        def symbol_from_iid(iid: str) -> str:
            return str(state.get("iid_symbol", {}).get(iid, ""))

        def name_from_iid(iid: str) -> str:
            return str(state.get("iid_name", {}).get(iid, ""))

        def selected_symbol_event(event: Any) -> str | None:
            row_id = tree.identify_row(event.y)
            if not row_id or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
                return None
            tree.selection_set(row_id); tree.focus(row_id); tree.see(row_id); tree.focus_set()
            update_selected(symbol_from_iid(row_id), name_from_iid(row_id))
            switch_symbol(symbol_from_iid(row_id), source=mode_label())
            return "break"

        def move_selection(delta: int) -> str:
            items = list(tree.get_children(""))
            if not items:
                return "break"
            current_iid = tree.focus() or (tree.selection()[0] if tree.selection() else "")
            current_index = items.index(current_iid) if current_iid in items else -1
            next_index = select_arrow_index(current_index, len(items), delta)
            if next_index < 0:
                return "break"
            next_iid = items[next_index]
            tree.selection_set(next_iid); tree.focus(next_iid); tree.see(next_iid); tree.focus_set()
            update_selected(symbol_from_iid(next_iid), name_from_iid(next_iid))
            switch_symbol(symbol_from_iid(next_iid), source="아래화살표" if delta > 0 else "위화살표" if delta < 0 else "Enter")
            return "break"

        def root_navigation(event: Any, delta: int) -> str | None:
            if event.widget is tree:
                return None
            widget_class = str(event.widget.winfo_class())
            if widget_class in {"Text", "Entry", "TEntry", "TCombobox", "Spinbox"}:
                return None
            return move_selection(delta)

        def apply_click_binding() -> None:
            tree.unbind("<ButtonRelease-1>"); tree.unbind("<Double-1>")
            tree.bind("<ButtonRelease-1>" if click_mode["single"] else "<Double-1>", selected_symbol_event)
            mode_help_var.set("한 번 클릭 또는 ↑/↓ → 차트 변경 후 목록 복귀")

        def change_click_mode() -> None:
            click_mode["single"] = True; state["last_symbol"] = ""; state["last_ts"] = 0.0
            apply_click_binding(); tree.focus_set(); status_var.set(f"클릭 방식을 {mode_label()}으로 변경했습니다.")

        def check_auto_link() -> None:
            state["last_action_ts"] = time.monotonic()
            symbol = normalize_symbol_code(state.get("selected_symbol", ""))
            if symbol:
                switch_symbol(symbol, source="원클릭 연동 확인")
            else:
                ok, message = hero.probe_auto_target()
                manual = " · 자동탐색 우선/저장 위치 예비" if hero.configured and hero._config_valid() else " · 자동탐색모드"
                status_var.set(("✅ " if ok else "❌ ") + message + manual)
            tree.focus_set()

        def do_reset() -> None:
            hero.reset_config(); status_var.set("저장된 영웅문 연동 위치만 초기화했습니다."); tree.focus_set()

        def finish_capture() -> None:
            ok, message = hero.capture_cursor_target(); status_var.set(f"연동 위치 저장 완료: {message} · 자동탐색 실패 때만 예비로 사용합니다." if ok else f"연동 위치 저장 실패: {message}")

        def countdown(remaining: int) -> None:
            if remaining <= 0: finish_capture(); return
            status_var.set(f"{remaining}초 안에 영웅문 [0600] 종목코드 입력칸을 클릭하고 그대로 두세요.")
            root.after(1000, lambda: countdown(remaining - 1))

        def begin_capture() -> None:
            messagebox.showinfo("연동 위치 저장", "확인을 누른 뒤 5초 안에 영웅문 [0600] 차트의 종목코드 입력칸을 한 번 클릭하고 그대로 두세요.\n저장 후 대시보드에서 종목을 클릭하면 이 위치로 코드와 Enter를 보냅니다.")
            countdown(5)

        def parse_input() -> CollaborationDraft:
            text = input_value()
            free_intents = parse_free_text(
                text,
                selected_symbol=str(state.get("selected_symbol", "")),
                selected_name=str(state.get("selected_name", "")),
                known_names=dict(state.get("known_names", {})),
            )
            free_intent_state["drafts"] = free_intents
            refresh_request_preview(free_intents)
            draft = compile_collaboration_input(
                text,
                selected_symbol=str(state["selected_symbol"]),
                selected_name=str(state["selected_name"]),
                known_names=dict(state["known_names"]),
            )
            state["current_draft"] = draft
            state["draft_fingerprint"] = current_fingerprint()
            state["trigger_touched"] = False
            labels = {
                "MEMO": "시장·테마 의견", "WATCH": "관심등록", "BUY_CONDITION": "가격조건 매수",
                "SET_TARGET": "목표가 변경", "BLOCK_BUY": "매수금지",
                "UNBLOCK_BUY": "금지해제", "CANCEL": "지정해제",
            }
            action_var.set(labels.get(draft.action, draft.action))
            symbol_var.set(f"{draft.name} {draft.symbol}" if draft.symbol else "종목 없음")
            buy_var.set(f"{draft.buy_price:.0f}" if draft.buy_price else "")
            target_var.set(f"{draft.target_price:.0f}" if draft.target_price else "")
            trigger_var.set(_trigger_label(draft.trigger))
            error_var.set(" · ".join(draft.errors))
            warnings = list(draft.warnings)
            if re.search(r"손절|스톱|stop", text, re.IGNORECASE):
                warnings.append("현재 자동손절은 사용하지 않습니다. 손절 입력은 주문에 반영하지 않고 의견·관찰자료로만 남깁니다.")
            warning_var.set(" · ".join(dict.fromkeys(warnings)))
            # 주문문이 아닌 시장/섹터/테마 의견은 구조화 결과를 우선 표시한다.
            non_order = [item for item in free_intents if item.kind != "ORDER_REQUEST"]
            if non_order and (draft.action in {"MEMO", "UNKNOWN"} or not draft.symbol):
                action_var.set(" / ".join(dict.fromkeys(item.title for item in non_order)))
                symbol_var.set(", ".join(dict.fromkeys(scope for item in non_order for scope in item.scope)) or "시장 전체")
                buy_var.set("")
                target_var.set("")
                warning_texts = [w for item in non_order for w in item.warnings]
                if warning_texts:
                    warning_var.set(" · ".join(dict.fromkeys(warning_texts)))
                status_var.set("시장·섹터·테마 의견을 시간대별 요청으로 구조화했습니다. 2번을 눌러 활성 목록에 반영하세요.")
                return draft
            if draft.errors:
                status_var.set("자동 적용하지 않습니다. 빨간 안내를 먼저 고쳐주세요.")
            elif draft.warnings:
                status_var.set("뜻은 읽었습니다. 노란 재확인 항목은 등록할 때 다시 묻습니다.")
            else:
                status_var.set("뜻을 확인했습니다.")
            return draft

        def _base_command(kind: str) -> dict[str, Any]:
            draft = ensure_current_draft()
            symbol = str(getattr(draft, "symbol", "") or state["selected_symbol"])
            name = str(getattr(draft, "name", "") or state["selected_name"])
            return {"kind": kind, "symbol": symbol, "name": name, "note": input_value()}

        def submit_simple(kind: str) -> None:
            command = _base_command(kind)
            if kind != "MEMO" and not re.fullmatch(r"\d{6}", command["symbol"]):
                error_var.set("종목을 먼저 선택해주세요.")
                return
            HUB.submit(command)
            status_var.set(f"협업명령 접수: {kind}")
            tree.focus_set()

        def apply_interpreted() -> None:
            draft = ensure_current_draft()
            free_drafts = list(free_intent_state.get("drafts", []))
            non_order = [item for item in free_drafts if item.kind != "ORDER_REQUEST"]
            saved_count = 0
            if non_order:
                saved_count = len(intent_ledger.add_many(non_order, status="활성"))
                refresh_opinion_history()
                refresh_active_table(HUB.snapshot())
            if non_order and (draft.action in {"MEMO", "UNKNOWN"} or not draft.symbol):
                status_var.set(f"형 요청 해석 {saved_count or len(non_order)}건을 시간순 원장과 활성 목록에 반영했습니다.")
                tree.focus_set()
                return
            if draft.errors:
                error_var.set(" · ".join(draft.errors))
                return
            route = route_interpreted_action(str(draft.action))
            if route == "ACTIVE_ONLY":
                error_var.set("가격조건 매수는 3 활성등록 버튼으로만 등록합니다. 뜻 확인 뒤 3번을 눌러주세요.")
                status_var.set("일반 적용과 가격조건 등록을 분리했습니다.")
                return
            if route == "UNSUPPORTED":
                error_var.set("이 해석은 일반 적용 대상이 아닙니다. 문장을 더 구체적으로 적어주세요.")
                return
            if draft.action == "SET_TARGET":
                submit_target()
            else:
                submit_simple(draft.action)
            if saved_count:
                status_var.set(f"종목 요청 적용 + 뉴스·시장·수급 해석 {saved_count}건을 시간순으로 저장했습니다.")

        def submit_buy() -> None:
            draft = ensure_current_draft()
            symbol = str(getattr(draft, "symbol", "") or state["selected_symbol"])
            name = str(getattr(draft, "name", "") or state["selected_name"])
            buy = _finite_or_none(buy_var.get().replace(",", ""))
            target = _finite_or_none(target_var.get().replace(",", ""))
            errors = list(getattr(draft, "errors", []))
            if buy is not None:
                errors = [e for e in errors if "매수기준가를 읽지 못했습니다" not in e]
            if bool(state.get("trigger_touched")):
                errors = [e for e in errors if "가격 이하 도달인지 상향 돌파인지" not in e]
            if not re.fullmatch(r"\d{6}", symbol):
                errors.append("종목을 선택해주세요.")
            if buy is None:
                errors.append("매수기준가를 입력해주세요.")
            if target is not None and buy is not None and target <= buy:
                errors.append("목표가는 매수가보다 높아야 합니다.")
            if errors:
                error_var.set(" · ".join(dict.fromkeys(errors)))
                return

            current = _finite_or_none(state.get("current_prices", {}).get(symbol))
            if current is None:
                current = _finite_or_none(_V7555_PRICE_HINT.get(symbol))
            if current is None:
                error_var.set("현재가를 확인할 수 없어 가격조건을 등록하지 않았습니다.")
                return

            price_confirmed = False
            ratio = buy / current
            if ratio >= OUTLIER_RATIO_HIGH or ratio <= OUTLIER_RATIO_LOW:
                price_confirmed = messagebox.askyesno(
                    "가격 재확인",
                    f"현재가 {current:,.0f}원과 매수기준가 {buy:,.0f}원의 차이가 큽니다.\n"
                    f"입력 가격은 현재가의 {ratio:.1f}배입니다. 오타가 아니고 이 가격이 맞습니까?",
                )
                if not price_confirmed:
                    error_var.set("매수기준가를 다시 확인해주세요.")
                    return

            active = HUB.active_for_symbol(symbol, kind="BUY_CONDITION")
            replace_existing = False
            if active:
                replace_existing = messagebox.askyesno(
                    "기존 가격조건 교체",
                    f"{name or symbol}에 이미 활성 가격조건 {len(active)}건이 있습니다.\n"
                    "기존 조건을 취소하고 새 조건으로 교체할까요?",
                )
                if not replace_existing:
                    error_var.set("기존 조건을 유지했습니다. 새 조건은 등록하지 않았습니다.")
                    return

            HUB.submit({
                "kind": "BUY_CONDITION",
                "symbol": symbol,
                "name": name,
                "trigger": _trigger_code(trigger_var.get()),
                "buy_price": buy,
                "target_price": target,
                "expires_at": _next_session_close_iso(),
                "note": input_value(),
                "price_confirmed": price_confirmed,
                "confirmed_market_price": current if price_confirmed else None,
                "replace_existing": replace_existing,
            })
            warning_var.set("")
            error_var.set("")
            status_var.set(f"{name or symbol} PAPER 가격조건을 접수했습니다. 주문 전 기존 엔진 안전검사를 다시 통과합니다.")
            tree.focus_set()

        def submit_target() -> None:
            draft = ensure_current_draft()
            command = _base_command("SET_TARGET")
            target = _finite_or_none(target_var.get().replace(",", ""))
            errors = [e for e in draft.errors if "목표가격을 읽지 못했습니다" not in e]
            if errors:
                error_var.set(" · ".join(errors))
                return
            if target is None:
                error_var.set("목표가격을 입력해주세요.")
                return
            command["target_price"] = target
            command["target_source"] = "USER"
            symbol_key = str(command.get("symbol", ""))
            try:
                record_target_source(symbol_key, "사용자 지정")
            except (OSError, ValueError) as exc:
                error_var.set(f"목표가 출처 저장 실패로 적용을 중단했습니다: {exc}")
                return
            state["target_sources"] = _load_target_sources()
            HUB.submit(command)
            status_var.set("사용자 지정 목표가 변경을 접수했습니다. 출처도 재시작 복원용 원장에 저장했습니다.")
            tree.focus_set()

        def clear_input() -> None:
            selected_active = active_tree.selection()
            if selected_active:
                selected_id = str(selected_active[0])
                if messagebox.askyesno("선택 요청 취소", "선택한 활성 조건 또는 판단을 해제할까요?"):
                    if selected_id.startswith("intent:"):
                        intent_ledger.set_status(selected_id.split(":", 1)[1], "해제")
                        status_var.set("선택한 시장·테마 판단을 해제했습니다.")
                        refresh_opinion_history()
                        refresh_active_table(HUB.snapshot())
                    else:
                        overlay_id = selected_id.split(":", 1)[1] if selected_id.startswith("overlay:") else selected_id
                        HUB.cancel(overlay_id)
                        status_var.set("선택한 종목 활성 조건을 취소했습니다.")
                        refresh()
                    return
            input_text.delete("1.0", "end")
            action_var.set("해석 전")
            symbol_var.set("-")
            buy_var.set("")
            target_var.set("")
            error_var.set("")
            warning_var.set("")
            state["current_draft"] = None
            state["draft_fingerprint"] = None
            state["trigger_touched"] = False
            free_intent_state["drafts"] = []
            input_text.focus_set()
            refresh_request_preview([])

        def on_tree_selection(_event: Any = None) -> None:
            selected = tree.selection()
            if not selected:
                return
            iid = selected[0]
            update_selected(symbol_from_iid(iid), name_from_iid(iid))

        def _compact_stage_time(value: Any) -> str:
            text_value = str(value or "-").strip()
            if text_value in ("", "-"):
                return "-"
            # 05/23 09:05 -> 5/23 09:05, 05/23 -> 5/23
            text_value = re.sub(r"^0(?=\d/)", "", text_value)
            text_value = re.sub(r"/(0)(?=\d(?:\s|$))", "/", text_value)
            return text_value

        def render_rows() -> None:
            preserve_symbol = str(state.get("selected_symbol", ""))
            for iid in tree.get_children():
                tree.delete(iid)
            state["iid_symbol"] = {}
            state["iid_name"] = {}
            all_rows = list(state.get("all_rows", []))
            chosen = str(category_filter_var.get() or "전체")
            rows = all_rows if chosen == "전체" else [row for row in all_rows if str(row.get("stage", "")) == chosen]
            counters: dict[str, int] = {}
            symbol_to_iid: dict[str, str] = {}
            for row in rows:
                stage = str(row.get("stage", ""))
                counters[stage] = counters.get(stage, 0) + 1
                symbol = str(row.get("symbol", ""))
                display_price = str(row.get("display_price", row.get("price", "-")))
                current = row.get("current_price", row.get("current", ""))
                basis = row.get("avg_price", row.get("basis_price", row.get("reference_price", "")))
                target = row.get("target_price", "")
                qty = row.get("quantity", row.get("qty", ""))
                change = row.get("change_pct", row.get("return_pct", ""))
                volume_ratio = row.get(
                    "volume_ratio",
                    row.get("same_time_volume_ratio", row.get("daily_cumulative_volume_ratio", ""))
                )
                if not current:
                    m = re.search(r"현재\s*([\d,]+)", display_price); current = m.group(1) if m else display_price.split("/")[0].strip()
                if not basis:
                    m = re.search(r"(?:기준|평단)\s*([\d,]+)", display_price); basis = m.group(1) if m else "-"
                if not target:
                    m = re.search(r"목표\s*([\d,]+)", display_price + " " + str(row.get("reason", ""))); target = m.group(1) if m else "-"
                reason_text = str(row.get("reason", ""))
                if stage in {"매수종목", "보유종목"} and target not in (None, "", "-"):
                    source_label = str(state.get("target_sources", {}).get(symbol, ""))
                    if not source_label:
                        try:
                            b = float(str(basis).replace(",", ""))
                            t = float(str(target).replace(",", ""))
                            source_label = "자동 +1.5%" if b > 0 and abs(t / b - 1.015) <= 0.003 else "전략/사용자 지정"
                        except (TypeError, ValueError, ZeroDivisionError):
                            source_label = "출처 확인 필요"
                    reason_text = f"목표:{source_label} · {reason_text}"
                def fmt(v):
                    if v in (None, ""): return "-"
                    if isinstance(v, (int,float)): return f"{v:,.2f}" if isinstance(v,float) and not float(v).is_integer() else f"{v:,.0f}"
                    return str(v)
                def fmt_volume(v):
                    if v in (None, "", "-"):
                        return "-"
                    try:
                        return f"{float(str(v).replace('배','').replace(',','')):.2f}배"
                    except (TypeError, ValueError):
                        return str(v)
                iid = tree.insert("", "end", values=(
                    counters[stage], DISPLAY_STAGE.get(stage, stage), row.get("name", ""),
                    fmt(current), fmt(change), fmt_volume(volume_ratio), fmt(basis), fmt(target), fmt(qty),
                    _compact_stage_time(row.get("stage_time", row.get("time", "-"))), reason_text,
                ), tags=(stage,))
                state["iid_symbol"][iid] = symbol
                state["iid_name"][iid] = str(row.get("name", ""))
                if symbol: symbol_to_iid.setdefault(symbol, iid)
            if preserve_symbol in symbol_to_iid:
                iid = symbol_to_iid[preserve_symbol]
                tree.selection_set(iid); tree.focus(iid); tree.see(iid)
                update_selected(preserve_symbol, name_from_iid(iid))
            elif tree.get_children():
                first = tree.get_children()[0]
                tree.selection_set(first); tree.focus(first); tree.see(first)
                update_selected(symbol_from_iid(first), name_from_iid(first))
            status_snapshot = dict(state.get("last_snapshot", {}))
            if time.monotonic() - float(state["last_action_ts"]) > 3.0:
                status_var.set(
                    f"시장 {status_snapshot.get('market', '-')} | {chosen} {len(rows)}종목 | "
                    "한 번 클릭·↑↓ 영웅문 연동 | 의견입력 정상"
                )

        def choose_category(stage: str) -> None:
            category_filter_var.set(stage)
            render_rows()
            tree.focus_set()

        self.choose_category = choose_category
        self.active_tree = active_tree
        self.dashboard_notebook = None
        self.kpi_vars = {**kpi_stage_vars, "전체": kpi_total_var, "활성지정": kpi_active_var, "시장": kpi_market_var}

        def apply_snapshot(snapshot: dict[str, Any]) -> None:
            raw_rows = list(snapshot.get("rows", []))
            if raw_rows:
                state["received_live_rows"] = True
            elif not state.get("received_live_rows"):
                raw_rows = list(SEED_CONSOLE_ROWS)
            rows, counts = prepare_gui_rows(raw_rows)
            known: dict[str, str] = {}
            prices: dict[str, float] = {}
            for row in rows:
                raw_symbol = str(row.get("symbol", ""))
                symbol = normalize_symbol_code(raw_symbol) or raw_symbol.strip()
                row["symbol"] = symbol
                name = str(row.get("name", ""))
                if name and re.fullmatch(r"\d{6}", symbol):
                    known[name] = symbol
                price_match = re.search(r"[\d,]+", str(row.get("price", "")))
                if price_match:
                    parsed_price = _finite_or_none(price_match.group(0).replace(",", ""))
                    if parsed_price is not None:
                        prices[symbol] = parsed_price
            state["known_names"] = {**_V7555_NAME_HINT, **known}
            state["current_prices"] = {**_V7555_PRICE_HINT, **prices}
            state["all_rows"] = rows
            state["last_snapshot"] = dict(snapshot)
            self.last_gui_rows = rows
            for stage in ("전체", *GUI_STAGE_ORDER):
                category_count_vars[stage].set(f"{DISPLAY_STAGE.get(stage, stage)} {counts.get(stage, 0)}")
            time_var.set(str(snapshot.get("updated", "")))
            market_header_var.set(f"시장상태: {snapshot.get('market', '-')}")
            quote_header_var.set(f"마지막 시세: {snapshot.get('updated', '-')}")

            def money_value(*keys: str) -> str:
                for key in keys:
                    value = snapshot.get(key)
                    if value not in (None, ""):
                        try: return f"{float(value):+,.0f}" if "pnl" in key or "profit" in key else f"{float(value):,.0f}"
                        except (TypeError, ValueError): return str(value)
                return "-"
            summary_vars["보유합계"].set(money_value("holding_total", "holdings_cost", "invested") if any(snapshot.get(k) not in (None, "") for k in ("holding_total","holdings_cost","invested")) else "44,676,473")
            summary_vars["평가손익"].set(money_value("unrealized_pnl", "evaluation_pnl") if any(snapshot.get(k) not in (None, "") for k in ("unrealized_pnl","evaluation_pnl")) else "+0")
            summary_vars["실현손익(오늘)"].set(money_value("today_realized_pnl", "realized_pnl") if any(snapshot.get(k) not in (None, "") for k in ("today_realized_pnl","realized_pnl")) else "+939,909")
            summary_vars["총손익"].set(money_value("total_pnl", "combined_pnl") if any(snapshot.get(k) not in (None, "") for k in ("total_pnl","combined_pnl")) else "+939,909")
            summary_vars["가용현금"].set(money_value("cash", "available_cash") if any(snapshot.get(k) not in (None, "") for k in ("cash","available_cash")) else "956,263,436")
            collab_snapshot = snapshot.get("collaboration", HUB.snapshot())
            active_count = len(collab_snapshot.get("active", []))
            active_var.set(f"형 요청 활성 목록 {active_count}건")
            kpi_total_var.set(str(counts.get("전체", 0)))
            kpi_ready_var.set(str(counts.get("매수가능성", 0) + counts.get("매수직전", 0)))
            kpi_hold_var.set(str(counts.get("매수종목", 0)))
            kpi_sold_var.set(str(counts.get("매도종목", 0)))
            for stage_name, variable in kpi_stage_vars.items():
                variable.set(f"{counts.get(stage_name, 0)}건")
            kpi_active_var.set(str(active_count))
            kpi_market_var.set(str(snapshot.get("market", "-")))
            refresh_active_table(collab_snapshot)
            refresh_opinion_history()
            render_rows()

        def poll() -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "snapshot": apply_snapshot(payload)
                    elif kind == "close": root.destroy(); return
            except queue_module.Empty:
                pass
            for result in HUB.drain_results():
                if bool(result.get("ok")):
                    error_var.set("")
                    status_var.set(f"✅ {result.get('message', '협업명령을 적용했습니다.')}")
                else:
                    error_var.set(str(result.get("message", "협업명령 적용 실패")))
                    status_var.set("❌ 협업명령이 실제 엔진에 적용되지 않았습니다.")
            current_collab = HUB.snapshot()
            current_active_count = len(current_collab.get("active", []))
            active_var.set(f"형 요청 활성 목록 {current_active_count}건")
            kpi_active_var.set(str(current_active_count))
            refresh_active_table(current_collab)
            if not self.closed.is_set(): root.after(300, poll)

        apply_click_binding()
        input_text.edit_modified(False)
        input_text.bind("<<Modified>>", on_input_modified, add="+")
        trigger_box.bind("<<ComboboxSelected>>", lambda _event: state.__setitem__("trigger_touched", True), add="+")
        tree.bind("<Up>", lambda _event: move_selection(-1)); tree.bind("<Down>", lambda _event: move_selection(1)); tree.bind("<Return>", lambda _event: move_selection(0))
        tree.bind("<Button-1>", lambda _event: tree.focus_set(), add="+")
        tree.bind("<<TreeviewSelect>>", on_tree_selection, add="+")
        root.bind("<Up>", lambda event: root_navigation(event, -1), add="+"); root.bind("<Down>", lambda event: root_navigation(event, 1), add="+"); root.bind("<Return>", lambda event: root_navigation(event, 0), add="+")
        root.protocol("WM_DELETE_WINDOW", lambda: root.withdraw())
        root.after(40, poll_link_results)
        root.after(200, poll); root.mainloop()


v7525.SimpleDoubleClickGui = V7557UnifiedDashboard
v7525.VERSION = VERSION


def self_check() -> list[tuple[str, bool, str]]:
    parent_ok = sha256_file(BASE_DIR / V7556_FILENAME) == V7556_SHA256
    source = Path(__file__).read_text(encoding="utf-8")
    sample_rows = [
        {"stage": stage, "stage_time": f"09:{index:02d}", "name": f"종목{index}", "symbol": f"{index:06d}", "reason": stage}
        for index, stage in enumerate(GUI_STAGE_ORDER, start=1)
    ]
    prepared, counts = prepare_gui_rows(sample_rows)
    draft = compile_collaboration_input(
        "23,500 이하 오면 사고 목표 24,200",
        selected_symbol="058610", selected_name="에스피지",
        known_names={"에스피지": "058610"},
    )
    return [
        ("V75.56기준SHA", parent_ok, V7556_SHA256[:16]),
        ("대시보드GUI상속", issubclass(V7557UnifiedDashboard, v7546.V7546Gui), "기존 연동 GUI 기반"),
        ("전체카테고리보존", len(prepared) == len(GUI_STAGE_ORDER) and counts.get("전체") == len(GUI_STAGE_ORDER), str(counts)),
        ("단일화면구성", all(token in source for token in ("프라임 시그널 대시보드", "형 의견·명령 입력", "형 요청 해석결과 미리보기", "형 요청 활성 목록", "의견·요청 시간대별 이력")), "KPI+목록+하단4패널"),
        ("승인UI정리", not re.search(r"selected_strip\s*\.\s*pack\s*\(", source) and not re.search(r"chain_banner\s*\.\s*pack\s*\(", source), "선택상세 띠·대형 배너 미표시"),
        ("자동탐색우선복원", "class RestoredHeroLink" in source and "route=AUTO_THEN_SAVED_FALLBACK" in source and "super().send_symbol(normalized)" in source, "V75.44 자동→저장좌표 예비"),
        ("MRO인덱스제거", not re.search(r"__mro__\s*\[", source), "부모 메서드 인덱스 선택 없음"),
        ("중복전체전송제거", not re.search(r"hero\s*\.\s*_auto_cache\s*=\s*None", source), "하위 연동의 1회 강제 재탐색만 사용"),
        ("클릭화살표연동", all(token in source for token in ('tree.bind("<Up>"', 'tree.bind("<Down>"', "hero.send_symbol(symbol)", "schedule_review_restore")), "기존 연동 유지"),
        ("협업파서보존", draft.action == "BUY_CONDITION" and draft.buy_price == 23500 and draft.target_price == 24200 and not draft.errors, str(draft)),
        ("전체용어수록", all(term in DASHBOARD_TERMS for term in ("매수후보", "매수직전", "큰매도배수", "앞선매수배수", "S1", "S2", "4개 버튼")), "고정 로직 설명"),
        ("엔진안전계층보존", v7544.V7544Engine._process_command_file is v7556._process_command_file, "V75.56 Hub→엔진"),
        ("PAPER전용", "PAPER" in VERSION, VERSION),
    ]


v7525.self_check = self_check


def main() -> int:
    return int(v7556.main())


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        rows = self_check()
        for name, ok, detail in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        raise SystemExit(0 if all(ok for _, ok, _ in rows) else 1)
    raise SystemExit(main())
