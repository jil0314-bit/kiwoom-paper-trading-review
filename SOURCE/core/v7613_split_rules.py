"""V7613 분할매수 규칙 — 2026-11-08 형 지시(1:3:9) → 2026-11-25 V7633 1:2:6. 주문·파일·네트워크 없음.

근거: 과거 거래바닥 계열 정규장(13시 이전) 매수 183건을 그날 실제 1분봉으로 재현.
  1차 100만 → 패턴 재출현 시 2차 300만 → 3차 900만 (최대 1,300만, 2026-11-10 형 지정)
  운용한도 5,000만(V7624): 실제 매입금액 기준. 새 1차는 +100만+여유 900만, 추가매수는 +그 차수 금액이 5,000만 안일 때만
  추가매수 조건 = 직전 매수가 아래에서
      ① 의미 있는 거래 음봉(직전 20봉 중앙값 x2.5 이상)
      ② 거래바닥(그 음봉 거래량의 40% 이하, 직전 5봉 중앙값 이하)
      ③ 아래꼬리 또는 양봉
      ④ 5봉선 안착 양봉
      ⑤ 안착 뒤 거래바닥 한 번 더(직전 5봉 중앙값 x0.7 이하) → 그다음 5선 위 양봉에서 매수
  매도 = 1차만 보유: 비용포함 +2% / 추가매수 뒤: 평단 대비 비용포함 +0.5%
  손절 = 최초 매수가 -10% (형 지정) / 15:19 전량 정리
  재현 결과(1:3:9, 손절10%): 설계기간 -671만 · 검증기간 +672만 · 한 건 최악 -531만 · 승률 86%
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from statistics import mean, median

KST = timezone(timedelta(hours=9))

ENABLED = True
FIRST_NOTIONAL = 1_000_000.0      # V7619: 형 지정 1차 100만
# V7633 (2026-11-25 형 위임 "가장 적절하게"): 1:3:9 → 1:2:6. 추가매수 1% 간격과 함께 재현(같은 매수 243건/235건):
#   1:3:9 +392만 / -261만 · 한 건 최악 -44만 / -83만 · 종목당 최대 1,300만
#   1:2:6 +322만 / -202만 · 한 건 최악 -31만 / -58만 · 종목당 최대   900만  ← 채택(합계 거의 같고 위험 30% 작음, 최근 기간 더 좋음)
#   1:2:4 +235 / -197 · 1:3:6 +243 / -242 · 1:2:3 +229 / -189
#   (손실은 대부분 3차까지 간 종목에서 나온다. 뒤 차수를 크게 사는 형 원칙은 유지하되 크기만 줄임)
WEIGHTS = (1.0, 2.0, 6.0)
NOTIONALS = tuple(FIRST_NOTIONAL * w for w in WEIGHTS)   # 100만 / 200만 / 600만
PLAN_TOTAL = sum(NOTIONALS)                               # 900만
BUDGET_LIMIT = 50_000_000.0       # 운용한도 5,000만
# V7624 (2026-11-12 형 지시·재현): 3차 몫을 미리 잡지 않고 '실제 쓴 돈' 기준으로 센다.
#   새 1차: 실제 매입금액 + 1차 100만 + 여유(3차 한 번 자리, V7633부터 600만) <= 5,000만
#   2·3차: 실제 매입금액 + 그 차수 금액 <= 5,000만 (모자라면 그 신호는 건너뛰고 다음 재출현을 기다림)
#   재현(35거래일 212신호): 3차몫 예약 +240만(66건 막힘) → 실제금액 +281만(2건 막힘), 최악일 -48만 → -30만
#   15:19 정리는 유지(다음날 이월은 18건 -200만 → -215만으로 더 나빴음)
NEW_ENTRY_BUFFER = NOTIONALS[-1]   # 3차 한 번 자리(1:2:6 → 600만)
MAX_TRANCHE = len(WEIGHTS)

FIRST_TARGET = 0.02      # 1차만 보유 중: 비용포함 순수익 +2%
ADD_TARGET = 0.005       # 2·3차 체결 뒤: 평단 대비 비용포함 순수익 +0.5%
HARD_STOP = 0.10         # 최초 매수가 대비 -10%

ADD_START = time(9, 0)
ADD_END = time(15, 19)   # 추가매수는 13시 이후에도 허용(신규 1차만 13시 차단 유지)
EOD_CLOSE = time(15, 19)
EOD_UNTIL = time(20, 0)

FLUSH_MULT = 2.5
FLUSH_HISTORY = 20
FLUSH_MIN_HISTORY = 10
DRY_RATIO = 0.40
SECOND_DRY_RATIO = 0.70
TIMEOUT_BARS = 40
RESET_BELOW = 0.99
SECOND_DRY = True
# V7633 (2026-11-25): 추가매수 가격이 직전 매수가보다 이 비율 이상 낮을 때만 산다(패턴 재출현 조건은 그대로).
#   아이티엠반도체 11/25: 2차가 1차보다 0.36%만 낮은 5,590에 3배 수량 → 평단은 거의 그대로, 위험만 3배.
#   재현(1:3:9, 같은 매수 243건/235건): 현행 +331만 / -415만 · 0.5% +378/-340 · 0.75% +343/-353
#     · 1% +392만 / -261만(한 건 최악 -65→-44만, -99→-83만) · 1.25% +309/-402 · 1.5% +248/-377 → 1% 채택
#   (매수 신호 판정은 마지막 매수 봉에서만 이 간격을 본다. 중간 단계인 5선 안착 봉에는 적용 안 함)
ADD_MIN_GAP = 0.01

STATE_TEXT = {
    0: "추가하락 거래음봉 대기",
    1: "거래바닥 대기",
    2: "아래꼬리·양봉 대기",
    3: "5봉선 안착 대기",
    4: "안착 뒤 재바닥 대기",
    5: "재바닥 뒤 양봉 대기",
}


# ------------------------------------------------------------------ 봉 접근
def _value(bar, key):
    return bar[key] if isinstance(bar, dict) else getattr(bar, key)


def stamp(bar) -> datetime:
    value = _value(bar, "timestamp")
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _ohlcv(bar):
    return (float(_value(bar, "open")), float(_value(bar, "high")), float(_value(bar, "low")),
            float(_value(bar, "close")), float(_value(bar, "volume")))


# ------------------------------------------------------------------ 금액
def notional_for(tranche: int) -> float:
    """tranche 차수(1부터)의 매수금액."""
    if not 1 <= int(tranche) <= MAX_TRANCHE:
        return 0.0
    return NOTIONALS[int(tranche) - 1]


def remaining_after(tranche: int) -> float:
    """tranche 차수까지 체결된 종목이 앞으로 더 쓸 수 있는 금액."""
    return float(sum(NOTIONALS[max(0, int(tranche)):]))


# ------------------------------------------------------------------ 시간
def add_window(now: datetime) -> bool:
    clock = now.astimezone(KST).time()
    return ADD_START <= clock < ADD_END


def eod_due(now: datetime) -> bool:
    clock = now.astimezone(KST).time()
    return EOD_CLOSE <= clock < EOD_UNTIL


# ------------------------------------------------------------------ 청산가
def target_price(cost_per_share: float, tranche: int, commission: float, tax: float,
                 align, tick_size) -> float:
    rate = FIRST_TARGET if int(tranche) <= 1 else ADD_TARGET
    proceeds = 1.0 - float(commission) - float(tax)
    if cost_per_share <= 0 or proceeds <= 0:
        raise ValueError("V7613 청산가 계산에 유효한 매입원가가 필요합니다")
    raw = float(cost_per_share) * (1.0 + rate) / proceeds
    target = float(align(raw, upward=True))
    if target + 1e-9 < raw:
        target += float(tick_size(target))
    return target


def stop_price(first_price: float, align) -> float:
    if first_price <= 0:
        raise ValueError("V7613 손절가 계산에 최초 매수가가 필요합니다")
    return float(align(float(first_price) * (1.0 - HARD_STOP), upward=True))


# ------------------------------------------------------------------ 패턴 재출현(형 정의)
def replay(bars, start_after: datetime, ref_price: float) -> dict:
    """완성 1분봉을 처음부터 다시 따라가 재출현 판정을 복원한다(상태 저장 없음 → 재시작 안전).

    bars: 시간순 완성 1분봉(전일 봉 포함 가능, 거래량 기준선에 쓰임).
    start_after: 마지막 체결 시각. 이 시각 뒤에 시작한 봉부터 판정한다.
    ref_price: 마지막 체결가. 모든 판정 봉의 종가가 이 값 아래여야 한다.
    결과 trigger_index: 매수 조건이 완성된 봉 위치(없으면 -1). 마지막 봉이 아니면 이미 지난 신호다.
    """
    rows = sorted(bars, key=stamp)
    values = [_ohlcv(b) for b in rows]
    state, flush_i, flush_low, flush_v = 0, -1, 0.0, 0.0
    last_trigger = -1
    last_reason = ""

    def reset():
        nonlocal state, flush_i
        state, flush_i = 0, -1

    start = next((k for k, b in enumerate(rows) if stamp(b) > start_after), len(rows))
    for i in range(start, len(rows)):
        o, h, l, c, v = values[i]
        if state and i - flush_i > TIMEOUT_BARS:
            reset()
        if state and l < flush_low * RESET_BELOW:
            reset()
        if state == 0:
            hist = [x[4] for x in values[max(0, i - FLUSH_HISTORY):i]]
            if (len(hist) >= FLUSH_MIN_HISTORY and c < o and c < ref_price
                    and v >= FLUSH_MULT * max(1.0, median(hist))):
                state, flush_i, flush_low, flush_v = 1, i, l, v
            continue
        if state == 1:
            prior5 = [x[4] for x in values[max(0, i - 5):i]]
            if v <= DRY_RATIO * flush_v and v <= median(prior5):
                state = 2
            continue
        span, body = h - l, abs(c - o)
        wick = min(o, c) - l
        if state == 2:
            if c > o or (span > 0 and wick >= 1.5 * body and wick >= 0.35 * span):
                state = 3
            continue
        ma5 = mean(x[3] for x in values[max(0, i - 4):i + 1])
        if state == 3:
            if c >= ma5 and c > o and c < ref_price and (SECOND_DRY or c <= ref_price * (1 - ADD_MIN_GAP)):
                if not SECOND_DRY:
                    last_trigger, last_reason = i, "5선안착"
                    reset()
                    continue
                state = 4
            continue
        if state == 4:
            prior5 = [x[4] for x in values[max(0, i - 5):i]]
            if v <= median(prior5) * SECOND_DRY_RATIO:
                state = 5
            continue
        if state == 5 and c > o and c >= ma5 and c < ref_price and c <= ref_price * (1 - ADD_MIN_GAP):
            last_trigger, last_reason = i, "5선안착+재바닥양봉"
            reset()
    return {
        "trigger_index": last_trigger,
        "last_index": len(rows) - 1,
        "fresh_trigger": last_trigger >= 0 and last_trigger == len(rows) - 1,
        "trigger_time": stamp(rows[last_trigger]).isoformat() if last_trigger >= 0 else "",
        "trigger_close": values[last_trigger][3] if last_trigger >= 0 else 0.0,
        "flush_low": flush_low,
        "state": state,
        "state_text": STATE_TEXT.get(state, ""),
        "reason": last_reason,
    }
