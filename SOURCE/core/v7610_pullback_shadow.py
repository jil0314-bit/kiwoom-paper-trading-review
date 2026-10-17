"""V7610 눌림목 재상승 확인 — 그림자(SHADOW) 판정. 주문·파일·네트워크 없음.

2026-09-23 손실분석: 거래량이 터지는 봉 한가운데서 사는 VOLUME5_EARLY 는
직전 20분 고점 위(중앙 1.16)에서 매수했고 기대값이 음수였다.
이 규칙은 거래량 폭발 뒤
  (1) 추세가 살아 있고           — 1분 MA20 상승, 종가 > MA20
  (2) 거래량이 줄며 눌렸다가      — 되돌림 25~62%, 눌림 평균거래량 ≤ 파동의 50%
  (3) 다시 오르기 시작한 것을     — 양봉이 직전 봉 고가 위에서 마감, 거래량 증가
완성봉으로 확인한 뒤에만 신호를 낸다. 바닥을 예측해 사지 않는다.

수치는 과거 데이터에 맞추지 않고 사전에 고정했다.
검증 전이므로 엔진은 이 신호로 주문하지 않고 기록만 한다.
"""
from statistics import mean

RULE_ID = "V7610_PULLBACK_RECLAIM_SHADOW"
REASON = "눌림목재상승확인(그림자)"

LOOKBACK = 30            # 파동 고점을 찾는 범위(봉, 신호봉 제외)
BASE_LOOKBACK = 20       # 고점 이전 파동 시작 저점을 찾는 범위
PRE_VOLUME_BARS = 20     # 파동 이전 평균거래량 범위
MIN_PRE_VOLUME_BARS = 10
MIN_IMPULSE = 0.03       # 파동 상승폭 ≥ 3%
IGNITION = 3.0           # 파동 안 최대거래량 ≥ 파동 이전 평균의 3배
MIN_PULLBACK_BARS = 2
MAX_PULLBACK_BARS = 15
RETRACE_MIN = 0.25       # 너무 얕으면 눌림이 아니다
RETRACE_MAX = 0.62       # 너무 깊으면 추세가 깨진 것이다
DRY_UP = 0.5             # 눌림 평균거래량 ≤ 파동 평균거래량 × 0.5
MA = 20
MA_RISE_BARS = 10
STOP_BUFFER = 0.002      # 손절 = 눌림 최저가 0.2% 아래
RISK_MIN = 0.006         # 손절폭이 너무 좁으면 잡음에 걸린다
RISK_MAX = 0.025         # 손절폭이 너무 넓으면 손익비가 무너진다


def _volumes(rows):
    return [float(r["volume"]) for r in rows]


def detect(rows):
    """rows: 오늘의 연속 완성 1분봉(dict), 마지막이 방금 완성된 신호봉.

    신호가 없으면 None. 있으면 판정 근거 dict.
    """
    n = len(rows)
    if n < MA + MA_RISE_BARS + 1:
        return None
    c, prev = rows[-1], rows[-2]

    # (3) 재상승 확인 — 바닥 예측이 아니라 반등 시작을 확인한다.
    if not (c["close"] > c["open"] and c["close"] > prev["high"]
            and c["volume"] > prev["volume"]):
        return None

    # (1) 추세 유지
    closes = [float(r["close"]) for r in rows]
    ma_now = mean(closes[-MA:])
    ma_before = mean(closes[-MA - MA_RISE_BARS:-MA_RISE_BARS])
    if not (c["close"] > ma_now and ma_now > ma_before):
        return None

    # 파동 고점: 신호봉을 뺀 최근 LOOKBACK 봉의 최고가
    lo = max(0, n - 1 - LOOKBACK)
    peak_i = max(range(lo, n - 1), key=lambda i: rows[i]["high"])
    pullback = rows[peak_i + 1:n - 1]
    if not MIN_PULLBACK_BARS <= len(pullback) <= MAX_PULLBACK_BARS:
        return None
    high = float(rows[peak_i]["high"])

    # 파동 시작 저점과 상승폭
    base_i = min(range(max(0, peak_i - BASE_LOOKBACK), peak_i + 1), key=lambda i: rows[i]["low"])
    base_low = float(rows[base_i]["low"])
    if base_low <= 0 or high / base_low - 1 < MIN_IMPULSE:
        return None

    # 거래량 점화: 파동 안 최대거래량이 파동 이전 평균의 3배 이상
    impulse = rows[base_i:peak_i + 1]
    pre = rows[max(0, base_i - PRE_VOLUME_BARS):base_i]
    if len(pre) < MIN_PRE_VOLUME_BARS:
        return None
    pre_avg = mean(_volumes(pre))
    impulse_max = max(_volumes(impulse))
    if pre_avg <= 0 or impulse_max < IGNITION * pre_avg:
        return None

    # (2) 거래량이 줄며 눌림
    pullback_low = min(float(r["low"]) for r in pullback)
    retrace = (high - pullback_low) / (high - base_low)
    if not RETRACE_MIN <= retrace <= RETRACE_MAX:
        return None
    impulse_avg = mean(_volumes(impulse))
    pullback_avg = mean(_volumes(pullback))
    if impulse_avg <= 0 or pullback_avg > DRY_UP * impulse_avg:
        return None
    if c["volume"] <= pullback_avg:
        return None

    # 신고가 추격이 아니라 눌림 구간 안에서의 재상승만
    entry = float(c["close"])
    if entry >= high:
        return None
    stop = pullback_low * (1 - STOP_BUFFER)
    risk = entry / stop - 1
    if not RISK_MIN <= risk <= RISK_MAX:
        return None

    return {
        "rule_id": RULE_ID,
        "reason": REASON,
        "data_time": c["timestamp"],
        "entry_ref": entry,
        "stop": stop,
        "risk_pct": risk * 100,
        "peak_high": high,
        "peak_time": rows[peak_i]["timestamp"],
        "reward_to_peak_pct": (high / entry - 1) * 100,
        "base_low": base_low,
        "base_time": rows[base_i]["timestamp"],
        "impulse_pct": (high / base_low - 1) * 100,
        "ignition_multiple": impulse_max / pre_avg,
        "pullback_low": pullback_low,
        "pullback_bars": len(pullback),
        "retrace": retrace,
        "dry_up_ratio": pullback_avg / impulse_avg,
        "ma20": ma_now,
    }
