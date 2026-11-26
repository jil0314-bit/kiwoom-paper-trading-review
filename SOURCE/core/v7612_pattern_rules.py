"""V7612 (2026-11-08) 형 지정 참고패턴 A/B 판정. 브로커·GUI·네트워크·파일 접근 없음.

패턴 B  투매 소화 후 아래꼬리 반전 (삼성전자우 005935, 2026-11-06 3분봉)
        1차 반등 -> 하락조정 중 장대음봉+대량거래(투매) -> 저점 높임 + 아래꼬리 양봉 연속
        -> 장대음봉 중심가 회복 시 매수.
패턴 A  1차 상승 후 조정·거래 고갈 -> 다음 날 출발 신호 (엘리스·혜인, 2026-11-06 3분봉)
        장 막바지(15:00 이후) 셋업이 갖춰진 종목을 다음 날 오전 '거래 실린 양봉의 박스 상단
        돌파'에서 매수.

모든 수치는 형이 준 차트 3장을 눈으로 읽은 초기값이다. 실매매 근거가 아니라 검증 대상이며,
같은 모양인데 오르지 않은 사례와 비교해 다시 맞춰야 한다.
판정은 1분봉을 형 차트와 같은 3분봉으로 묶어서 한다.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from statistics import mean, median

import v7609_volume_prebuy as volume5

RULE_TAG = "V7612"
ROUTE_TAIL = "TAIL_REVERSAL"
ROUTE_CARRY = "CARRY_A"

# 개별 끄기 스위치 (False 로 바꾸면 해당 패턴만 즉시 비활성)
TAIL_ENABLED = True
CARRY_ENABLED = True

BAR_MINUTES = 3
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)

# ---------------- 패턴 B (투매 소화 아래꼬리 반전) ----------------
B_MIN_REBOUND = 0.05        # S1 장대음봉 전 1차 반등: 저점 대비 +5% 이상
B_RETRACE_MIN = 0.25        # S2 반등폭 되돌림 하한
B_RETRACE_MAX = 0.70        # S2 반등폭 되돌림 상한
B_BODY_MULT = 2.0           # S2 음봉 몸통 >= 직전 봉 몸통 중앙값 x2.0
B_VOLUME_MULT = 2.5         # S3 음봉 거래량 >= 직전 봉 거래량 중앙값 x2.5 (시초 대량거래에 휘둘리지 않게 중앙값 사용)
B_BASE_BARS = 20            # S2/S3 비교 기준 봉 수
B_TAIL_WINDOW = 3           # S5 장대음봉 뒤 몇 봉 안에서 꼬리 양봉을 찾나
B_TAIL_MIN_COUNT = 2        # S5 꼬리 양봉 최소 개수
B_TAIL_WICK_BODY = 1.5      # S5 아래꼬리 >= 몸통 x1.5
B_TAIL_WICK_SPAN = 0.35     # S5 아래꼬리 >= 봉 전체 길이의 35%
B_SUPPORT_TOL = 0.005       # 장대음봉 저가 아래로 0.5% 넘게 빠지면 무효
B_HIGHER_LOW_MARGIN = 0.001 # S4 조정 저점이 당일 이전 최저보다 0.1% 이상 높아야 함
B_TRIGGER_WINDOW = 6        # 장대음봉 뒤 6봉(18분) 안에 매수 신호가 나와야 함

# ---------------- 패턴 A 셋업 (장 막바지에 판정) ----------------
A_SETUP_FROM = time(15, 0)
A_MIN_RISE = 0.20           # P1 당일 저점 대비 1차 상승 +20% 이상
A_RETRACE_MIN = 0.15        # P2 되돌림 하한
A_RETRACE_MAX = 0.50        # P2 되돌림 상한
A_MIN_BARS_SINCE_PEAK = 10  # 고점 뒤 최소 30분 조정
A_DRY_BARS = 10             # P4 '거래 바닥' 판정 구간(최근 10봉=30분)
A_DRY_RATIO = 0.30          # P4 최근 10봉 평균 거래량 <= 상승구간 최대봉의 30%
A_MA_LONG = 60              # P5 60선(3분봉)
A_MA_TOL = 0.97             # P5 종가 >= 60선 x0.97 (15:30엔 60선이 아직 고점권 가격을 포함 -> 차트(19:50)보다 완화)
A_BOX_BARS = 20             # 다음 날 돌파 기준 박스 = 마지막 20봉(1시간) 고가

# ---------------- 패턴 A 다음 날 출발 신호 ----------------
A_TRIGGER_VOLUME_MULT = 3.0  # P7 거래량 >= 전날 마른 구간 평균 x3
A_CHASE_LIMIT = 1.05         # 박스 상단 +5% 넘게 뜬 자리는 추격 금지
A_TRIGGER_UNTIL = time(11, 0)
A_CARRY_MAX_AGE_DAYS = 5     # 연휴 감안, 셋업일로부터 5일 넘으면 폐기


def _rows(raw):
    """1분봉을 검증된 dict 로 변환. 손상된 봉은 건너뛴다(판정은 결정적으로 유지)."""
    out = []
    seen = set()
    for item in raw or ():
        try:
            row = volume5._bar(item)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
        if row["timestamp"] in seen:
            continue
        seen.add(row["timestamp"])
        out.append(row)
    out.sort(key=lambda r: r["timestamp"])
    return out


def three_minute(rows, day, cutoff=None):
    """`day` 정규장(09:00~15:30) 1분봉을 09:00 기준 3분봉으로 묶는다.

    cutoff(이 시각 이전 1분봉까지만 완성)가 주어지면 끝나지 않은 3분봉은 버린다.
    """
    buckets = {}
    for r in rows:
        s = volume5._stamp(r["timestamp"])
        if s.date() != day or not (SESSION_OPEN <= s.time() < SESSION_CLOSE):
            continue
        idx = (s.hour * 60 + s.minute - 9 * 60) // BAR_MINUTES
        buckets.setdefault(idx, []).append((s, r))
    out = []
    for idx in sorted(buckets):
        items = sorted(buckets[idx], key=lambda x: x[0])
        first = items[0][0]
        start = first.replace(hour=9, minute=0) + timedelta(minutes=idx * BAR_MINUTES)
        end = start + timedelta(minutes=BAR_MINUTES)
        if cutoff is not None and end > cutoff:
            continue
        rs = [r for _, r in items]
        out.append({
            "timestamp": start.isoformat(),
            "open": rs[0]["open"],
            "high": max(r["high"] for r in rs),
            "low": min(r["low"] for r in rs),
            "close": rs[-1]["close"],
            "volume": int(sum(r["volume"] for r in rs)),
        })
    return out


def _lower_wick(b):
    return min(b["open"], b["close"]) - b["low"]


def _is_tail_bar(b, flush_volume):
    span = b["high"] - b["low"]
    body = abs(b["close"] - b["open"])
    if span <= 0:
        return False
    bullish_or_doji = b["close"] >= b["open"] or body <= span * 0.10
    wick = _lower_wick(b)
    return (bullish_or_doji and wick >= B_TAIL_WICK_BODY * body
            and wick >= B_TAIL_WICK_SPAN * span and b["volume"] < flush_volume)


def tail_reversal(t):
    """패턴 B. t = 오늘 완성 3분봉. 마지막 봉이 '처음으로' 매수 조건을 채운 봉일 때만 반환."""
    n = len(t)
    if n < 6:
        return None
    last = n - 1
    for j in range(last - 1, max(2, last - B_TRIGGER_WINDOW) - 1, -1):
        f = t[j]
        if not f["close"] < f["open"]:
            continue
        base = t[max(0, j - B_BASE_BARS):j]
        if len(base) < 3:
            continue
        body_ref = median(abs(b["close"] - b["open"]) for b in base)
        vol_ref = median(b["volume"] for b in base)
        if body_ref <= 0 or vol_ref <= 0:
            continue
        if f["open"] - f["close"] < B_BODY_MULT * body_ref:          # S2 장대음봉
            continue
        if f["volume"] < B_VOLUME_MULT * vol_ref:                     # S3 투매 거래량
            continue
        pk = max(range(j), key=lambda i: (t[i]["high"], -i))
        pre_low = min(b["low"] for b in t[:pk + 1])
        peak = t[pk]["high"]
        if pre_low <= 0 or peak / pre_low - 1 < B_MIN_REBOUND:        # S1 1차 반등
            continue
        retrace = (peak - f["low"]) / (peak - pre_low)
        if not B_RETRACE_MIN <= retrace <= B_RETRACE_MAX:             # S2 되돌림 범위
            continue
        after = t[j + 1:]
        day_low_before = min(b["low"] for b in t[:j])
        zone_low = min([f["low"]] + [b["low"] for b in after])
        if zone_low <= day_low_before * (1 + B_HIGHER_LOW_MARGIN):    # S4 저점 높임
            continue
        if any(b["low"] < f["low"] * (1 - B_SUPPORT_TOL) for b in after):  # 지지 실패
            continue
        tails = [i for i in range(j + 1, min(j + 1 + B_TAIL_WINDOW, n))
                 if _is_tail_bar(t[i], f["volume"])]                  # S5+S6
        if len(tails) < B_TAIL_MIN_COUNT:
            continue
        second = tails[B_TAIL_MIN_COUNT - 1]
        if last < second or last - j > B_TRIGGER_WINDOW:
            continue
        mid = (f["open"] + f["close"]) / 2
        def triggered(b):
            return b["close"] > b["open"] and b["close"] >= mid
        if not triggered(t[last]):
            return None
        if any(triggered(t[k]) for k in range(second, last)):
            return None  # 이미 앞 봉에서 신호가 나갔다(같은 에피소드 중복 매수 방지)
        return dict(
            route=ROUTE_TAIL, buy=True, episode=f["timestamp"],
            flush_low=f["low"], flush_mid=mid, rebound=peak / pre_low - 1, retrace=retrace,
            flush_volume_x=f["volume"] / vol_ref, tail_count=len(tails),
            reason=(f"{RULE_TAG}패턴B·투매소화꼬리반전·반등+{peak / pre_low - 1:.1%}"
                    f"·되돌림{retrace:.0%}·투매거래x{f['volume'] / vol_ref:.1f}"
                    f"·꼬리양봉{len(tails)}·저점높임·중심가회복"),
        )
    return None


def carry_setup(t):
    """패턴 A 셋업. t = 하루치 완성 3분봉(09:00~). 조건 미달이면 None."""
    n = len(t)
    if n < A_MA_LONG:
        return None
    pk = max(range(n), key=lambda i: (t[i]["high"], -i))
    peak = t[pk]["high"]
    pre_low = min(b["low"] for b in t[:pk + 1])
    if pre_low <= 0:
        return None
    rise = peak / pre_low - 1
    if rise < A_MIN_RISE:                                             # P1
        return None
    if n - 1 - pk < A_MIN_BARS_SINCE_PEAK:
        return None
    post = t[pk + 1:]
    pull_low = min(b["low"] for b in post)
    retrace = (peak - pull_low) / (peak - pre_low)
    if not A_RETRACE_MIN <= retrace <= A_RETRACE_MAX:                 # P2
        return None
    leg_max = max(b["volume"] for b in t[:pk + 1])
    dry = t[-A_DRY_BARS:]
    dry_avg = mean(b["volume"] for b in dry)
    if leg_max <= 0 or dry_avg <= 0 or dry_avg > A_DRY_RATIO * leg_max:  # P4
        return None
    if mean(b["volume"] for b in dry[-5:]) > mean(b["volume"] for b in dry[:5]):
        return None                                                   # P4 거래 감소 추세
    ma60 = mean(b["close"] for b in t[-A_MA_LONG:])
    last_close = t[-1]["close"]
    if last_close < ma60 * A_MA_TOL:                                  # P5
        return None
    if last_close <= pull_low:
        return None
    box_top = max(b["high"] for b in t[-A_BOX_BARS:])
    return dict(peak=peak, pre_low=pre_low, pull_low=pull_low, rise=rise, retrace=retrace,
                dry_avg=dry_avg, dry_ratio=dry_avg / leg_max, ma60=ma60, box_top=box_top,
                last_close=last_close, peak_time=t[pk]["timestamp"])


def _previous_session_day(rows, today):
    days = sorted({volume5._stamp(r["timestamp"]).date() for r in rows
                   if volume5._stamp(r["timestamp"]).date() < today
                   and SESSION_OPEN <= volume5._stamp(r["timestamp"]).time() < SESSION_CLOSE})
    return days[-1] if days else None


def carry_trigger(setup, today_bars, now):
    """패턴 A 다음 날 출발 신호. 마지막 봉이 '처음으로' 조건을 채운 봉일 때만 반환."""
    if not today_bars or now.time() > A_TRIGGER_UNTIL:
        return None
    if min(b["low"] for b in today_bars) < setup["pull_low"]:
        return None                                   # 전날 조정 저점 이탈 -> 셋업 무효
    if today_bars[0]["open"] > setup["box_top"] * A_CHASE_LIMIT:
        return None                                   # 박스 상단보다 5% 넘게 갭상승 -> 추격 금지
    need = A_TRIGGER_VOLUME_MULT * setup["dry_avg"]
    def triggered(b):
        return (b["close"] > b["open"] and b["close"] > setup["box_top"]
                and b["close"] <= setup["box_top"] * A_CHASE_LIMIT and b["volume"] >= need)
    if not triggered(today_bars[-1]):
        return None
    if any(triggered(b) for b in today_bars[:-1]):
        return None
    c = today_bars[-1]
    return dict(
        route=ROUTE_CARRY, buy=True, episode=c["timestamp"], box_top=setup["box_top"],
        pull_low=setup["pull_low"], trigger_volume_x=c["volume"] / setup["dry_avg"],
        reason=(f"{RULE_TAG}패턴A·전일거래바닥이월·전일상승+{setup['rise']:.0%}"
                f"·되돌림{setup['retrace']:.0%}·마른거래{setup['dry_ratio']:.0%}"
                f"·박스{setup['box_top']:,.0f}돌파·거래x{c['volume'] / setup['dry_avg']:.1f}"),
    )


def evaluate(raw, today_rows, *, now):
    """v7609_prebuy_rules.evaluate 에서 호출. 매수 신호(dict) 또는 None.

    today_rows: 기존 completed() 결과(오늘 완성 1분봉 dict, 마지막 봉 신선도 이미 검증됨).
    raw: 전날 봉까지 포함된 원본(패턴 A 셋업 재계산용).
    """
    now = volume5._stamp(now)
    cutoff = now.replace(second=0, microsecond=0)
    today = three_minute(today_rows, now.date(), cutoff)
    if TAIL_ENABLED:
        hit = tail_reversal(today)
        if hit is not None:
            return hit
    if CARRY_ENABLED:
        rows = _rows(raw)
        prev_day = _previous_session_day(rows, now.date())
        if prev_day is not None and (now.date() - prev_day).days <= A_CARRY_MAX_AGE_DAYS:
            prior = [r for r in rows if volume5._stamp(r["timestamp"]).date() == prev_day
                     and SESSION_OPEN <= volume5._stamp(r["timestamp"]).time() < SESSION_CLOSE]
            setup = carry_setup(three_minute(prior, prev_day))
            if setup is not None:
                hit = carry_trigger(setup, today, now)
                if hit is not None:
                    hit["prior_bars"] = prior
                    hit["setup_day"] = prev_day.isoformat()
                    return hit
    return None


def end_of_day_setup(raw, *, now):
    """장 막바지(15:00~15:30) 오늘 봉으로 패턴 A 셋업 판정. 이월 후보 기록용(주문 아님)."""
    if not CARRY_ENABLED:
        return None
    now = volume5._stamp(now)
    if not (A_SETUP_FROM <= now.time() <= time(15, 35)):
        return None
    rows = [r for r in _rows(raw) if volume5._stamp(r["timestamp"]).date() == now.date()]
    return carry_setup(three_minute(rows, now.date(), now.replace(second=0, microsecond=0)))
