"""V7628 일봉 종가배팅 규칙 — 2026-11-15 형 지시. 순수 계산(주문·파일·네트워크 없음).

형 지시: 일봉에서 우리 로직(장대양봉+거래폭발 → 조정 → 거래바닥 → 5일선 위 양봉)이 나오면
  종가 무렵 종목당 50만원 매수(하루 최대 10종목, 거래대금 큰 순) → 다음날 매도.
  다음날 09:00 시가에 수익이면 바로 매도, 아니면 더 기다림(장중 비용포함 +2% 목표),
  손절은 매수가 -10%, 다음날 15:19까지 못 팔면 정리.

재현(전 종목 일봉 3,600종목·559거래일, 하루 10종목 제한 4,432건, 왕복비용 0.25%):
  다음날 시가 무조건 매도            건당 +0.05%
  시가 수익이면 매도·아니면 +2% 대기·당일 정리(채택)  건당 -0.19% · 승률 68%
  같은 방식으로 2~10일 보유         건당 -0.24~-0.29%
→ 과거 자료로는 기대수익이 없다. 형 지시로 소액(50만) 모의 검증용으로 넣는다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from statistics import median

KST = timezone(timedelta(hours=9))

ENABLED = True
PATTERN = "V7628_일봉종가배팅"
NOTIONAL = 500_000.0
MAX_PER_DAY = 10
MAX_CANDIDATES = 300            # 거래대금 상위에서 일봉을 조회할 최대 종목 수

SCAN_FROM = time(15, 0)         # 일봉 검색 시작
BUY_FROM = time(15, 15)         # 매수 시작(검색이 끝났으면)
BUY_UNTIL = time(15, 19)        # 이 시각 전까지만 매수(15:20 종가 단일가 전)
OPEN_SELL_FROM = time(9, 0, 30)  # 다음날 시가 수익 확인 시각
HARD_STOP = 0.10

# 일봉 패턴(재현과 같은 값). 거래대금은 종가×거래량(원)으로 근사한다.
MIN_PRICE = 1000.0
MIN_VALUE = 3_000_000_000.0     # 오늘 거래대금 30억 이상
MIN_UP = 0.0                    # 오늘 전일 대비 0% 초과
MAX_UP = 0.08                   # ~ +8% 이하(이미 급등한 날은 추격 금지)
IMPULSE_MIN_AGO = 3             # 장대양봉은 3~15거래일 전
IMPULSE_MAX_AGO = 15
IMPULSE_RETURN = 0.10           # 전일 대비 +10% 이상 양봉
IMPULSE_VOLUME = 3.0            # 직전 20일 거래량 중앙값의 3배 이상
IMPULSE_VALUE = 10_000_000_000.0  # 그날 거래대금 100억 이상
PULLBACK_MIN = 0.08             # 그 뒤 고점 대비 8% 이상 조정
FLOOR_KEEP = 0.97               # 조정 저점이 장대양봉 시가의 97% 아래로 무너지지 않음
DRY_RATIO = 0.35                # 조정 중 거래량이 장대양봉의 35% 이하인 날이 있음
BELOW_TOP = 0.97                # 오늘 종가가 고점의 97% 이하(이미 재돌파한 종목 제외)
MIN_ROWS = 27
# V7631 (2026-11-17 밤 형 지시): 바닥 신호를 여러 형태로 — 거래바닥(장대양봉 거래량의 35% 이하) 날부터
#   ① 작은 양봉 2~3개 ② 음봉이어도 아래꼬리(길든 짧든) ③ 5일선 돌파(거래 크든 작든) 중 하나가 나오고,
#   오늘 종가가 5일선 위면 양봉·음봉, 오늘 등락과 관계없이 매수(+8% 넘게 오른 날만 추격 금지).
#   재현(3,600종목·559일, 하루 10종목·50만, 다음날 시가 수익 매도·아니면 +2%·당일 정리):
#     현행 4,432건 건당 -0.19% → 다형 신호 약 5,100건 건당 -0.10%. 여전히 기대수익은 마이너스(모의 검증용).
ANY_SIGNAL = True
SIGNAL_HOLD_DAYS = 5            # 바닥 신호부터 오늘까지 최대 거래일
DRY_TO_TODAY_DAYS = 10          # 첫 거래바닥 날부터 오늘까지 최대 거래일
SMALL_BODY = 0.6                # '작은 양봉' = 몸통이 장대양봉 전 10일 봉 길이 중앙값의 60% 이하

EXCLUDE_NAME_TOKENS = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL ", "HANARO", "ARIRANG", "KOSEF",
                       "PLUS ", "TIMEFOLIO", "ETN", "스팩", "인버스", "레버리지", "선물")


def _num(value) -> float:
    try:
        return abs(float(str(value).replace(",", "").replace("+", "").strip() or 0))
    except ValueError:
        return 0.0


def prefilter(row: dict) -> tuple[str, str, float] | None:
    """ka10032(거래대금상위) 한 줄 → (종목코드, 이름, 거래대금 원) 또는 None."""
    code = str(row.get("stk_cd", "")).strip().split("_")[0]
    name = str(row.get("stk_nm", "")).strip()
    if len(code) != 6 or not code.isdigit() or any(token in name.upper() for token in EXCLUDE_NAME_TOKENS):
        return None
    try:
        change = float(str(row.get("flu_rt", "")).replace("+", "").replace(",", "").strip())
    except ValueError:
        return None
    price = _num(row.get("cur_prc"))
    value = _num(row.get("trde_prica")) * 1_000_000.0
    low_ok = change > MIN_UP * 100 or ANY_SIGNAL   # V7631: 오늘 내린 종목도 5일선 위면 후보
    if price < MIN_PRICE or value < MIN_VALUE or not (low_ok and change <= MAX_UP * 100):
        return None
    return code, name, value


def signal(rows: list[dict]) -> dict | None:
    """rows: 과거→오늘 일봉 dict(d,o,h,l,c,v). 마지막 줄이 오늘(장중이면 현재가 기준)."""
    n = len(rows)
    if n < MIN_ROWS:
        return None
    i = n - 1
    r, prev = rows[i], rows[i - 1]
    if min(r["o"], r["h"], r["l"], r["c"], prev["c"]) <= 0 or r["v"] <= 0:
        return None
    if r["c"] < MIN_PRICE or r["c"] * r["v"] < MIN_VALUE:
        return None
    up = r["c"] / prev["c"] - 1
    ma5 = sum(x["c"] for x in rows[i - 4:i + 1]) / 5
    span, body = r["h"] - r["l"], abs(r["c"] - r["o"])
    wick = min(r["o"], r["c"]) - r["l"]
    bull = r["c"] > r["o"] or (span > 0 and wick >= 1.5 * body and wick >= 0.35 * span)
    if ANY_SIGNAL:
        if not (r["c"] >= ma5 and up <= MAX_UP):
            return None
    elif not (bull and r["c"] >= ma5 and MIN_UP < up <= MAX_UP):
        return None
    found = None
    for j in range(max(21, i - IMPULSE_MAX_AGO), i - IMPULSE_MIN_AGO + 1):
        q, qp = rows[j], rows[j - 1]
        if qp["c"] <= 0:
            continue
        base = median(x["v"] for x in rows[j - 20:j])
        if (q["c"] / qp["c"] - 1 >= IMPULSE_RETURN and q["c"] > q["o"]
                and q["v"] >= IMPULSE_VOLUME * max(1.0, base) and q["c"] * q["v"] >= IMPULSE_VALUE):
            found = j
    if found is None:
        return None
    q = rows[found]
    middle = rows[found + 1:i]
    if not middle:
        return None
    top = max(x["h"] for x in rows[found:i])
    low = min(x["l"] for x in middle)
    if low > top * (1 - PULLBACK_MIN) or low < q["o"] * FLOOR_KEEP:
        return None
    kind = "5일선위 양봉"
    if ANY_SIGNAL:
        drys = [k for k in range(found + 1, i + 1) if rows[k]["v"] <= DRY_RATIO * q["v"]]
        if not drys or i - drys[0] > DRY_TO_TODAY_DAYS:
            return None
        spans = [x["h"] - x["l"] for x in rows[found - 10:found] if x["h"] > x["l"]]
        ref = median(spans) if spans else 1.0
        small = lambda x: x["c"] > x["o"] and x["c"] - x["o"] <= ref * SMALL_BODY
        avg5 = lambda k: sum(x["c"] for x in rows[k - 4:k + 1]) / 5
        hit = None
        for s in range(drys[0], i + 1):
            b, a = rows[s], rows[s - 1]
            kinds = [name for name, ok in (("아래꼬리", b["l"] < min(b["o"], b["c"])),
                                           ("작은양봉연속", small(a) and small(b)),
                                           ("5일선돌파", b["c"] >= avg5(s) and a["c"] < avg5(s - 1))) if ok]
            if kinds:
                hit = (s, kinds)
        if hit is None or i - hit[0] > SIGNAL_HOLD_DAYS:
            return None
        kind = "바닥신호(" + "+".join(hit[1]) + ")·5일선위 지지"
    elif min(x["v"] for x in middle) > DRY_RATIO * q["v"]:
        return None
    if r["c"] > top * BELOW_TOP:
        return None
    return {
        "impulse_day": str(q["d"]), "impulse_return": q["c"] / rows[found - 1]["c"] - 1,
        "pullback": 1 - low / top, "up_today": up, "value": r["c"] * r["v"],
        "reason": (f"일봉종가배팅·장대양봉 {str(q['d'])[4:6]}/{str(q['d'])[6:8]}(+{q['c'] / rows[found - 1]['c'] - 1:.0%})"
                   f"·조정 -{1 - low / top:.0%}·거래바닥·{kind}({up:+.1%})"),
    }


def hot_gain(rows: list[dict], days: int = 3) -> float:
    """오늘을 뺀 직전 days거래일 상승률(급등 제외 규칙과 같은 계산)."""
    if len(rows) < days + 2:
        return 0.0
    return rows[-2]["c"] / rows[-2 - days]["c"] - 1


def scan_window(now: datetime) -> bool:
    now = now.astimezone(KST)
    return now.weekday() < 5 and SCAN_FROM <= now.time() < BUY_UNTIL


def buy_window(now: datetime) -> bool:
    now = now.astimezone(KST)
    return now.weekday() < 5 and BUY_FROM <= now.time() < BUY_UNTIL


def open_sell_due(now: datetime, buy_day: str, checked_day: str) -> bool:
    """매수 다음 거래일 이후, 09:00:30~15:19 사이, 그날 아직 확인 안 했을 때 한 번."""
    now = now.astimezone(KST)
    today = now.date().isoformat()
    return (bool(buy_day) and today > str(buy_day) and str(checked_day) != today
            and OPEN_SELL_FROM <= now.time() < time(15, 19))


def bought_today(buy_day: str, today: date) -> bool:
    return str(buy_day) == today.isoformat()
