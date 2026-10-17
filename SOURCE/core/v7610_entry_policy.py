"""V7610 자동 신규매수 허용 규칙 — 2026-09-23 손실분석 반영. 주문·파일·네트워크 없음.

근거: paper_fills.csv 389건 왕복 (2026-08-11 ~ 09-23), 거래당 기대값 -0.62% [-0.84, -0.40].
원장에서 해당 매수를 뺀 확정 계산(추정 없음):
  VOLUME5_EARLY 매수        244건  합계 -6,291,727원
  오후 13:00~15:20 신규매수   67건  제외 시 합계 -12,935,393 → -8,442,955원
  NXT 프리 08:00~08:50 신규   78건  제외 시 합계 -12,935,393 → -9,881,074원
수동 명령 매수는 이 규칙의 대상이 아니다.
"""
from datetime import time

# VOLUME5_EARLY: 형성 중인 1분봉이 양봉·5배 거래량 페이스일 때 즉시 매수.
# 매수 위치가 그 1분봉 최고가(중앙 1.00), 직전 20분 고점 위(중앙 1.16)였다.
EARLY_VOLUME_BUY_ENABLED = False

# (시작, 끝, 표시명) — 끝 시각은 포함하지 않는다.
BLOCKED_NEW_ENTRY_WINDOWS = (
    (time(8, 0), time(8, 50), "NXT프리 08:00~08:50"),
    (time(13, 0), time(15, 20), "오후 13:00~15:20"),
)


def blocked_window(now):
    """자동 신규매수·자동 추가매수 금지 시간대면 표시명, 아니면 빈 문자열."""
    clock = now.time()
    for start, end, label in BLOCKED_NEW_ENTRY_WINDOWS:
        if start <= clock < end:
            return label
    return ""
