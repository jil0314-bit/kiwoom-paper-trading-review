"""V7619 초보수 모드 판정 — 2026-11-10 형 지시. 주문·파일·네트워크 없음(순수 계산).

형 정의: 외국인이 하락 몰빵(현물 대량 순매도)하고 지수가 계속 밀릴 때는 신규 1차와
2·3차 추가매수를 멈춘다. 매도(목표·손절·장마감 정리)는 그대로 둔다.

키움 주식 REST에는 외국인 '선물' 투자자 자료가 없어서 현물만 쓴다(형 기준 5천억은 선물+현물 합).
  ① 지수 60분 연속 하락: KODEX200(069500) 또는 KODEX코스닥150(229200) 1분봉 기준
       - 60분 수익률 <= -0.7%
       - 20분 간격 종가가 계속 낮아짐(60분 전 > 40분 전 > 20분 전 > 지금)
  ② 반등 실패: 60분 저점에서 되돌린 폭 <= (고점-저점)의 35%, 그리고 지금 종가 < 20분 평균
  ③ 외국인 현물 순매도: 코스피+코스닥 당일 누적 <= -3,000억 (ka10051 종합 줄, 단위 억원)
  ①②가 한 지수라도 맞고 ③이 맞으면 켜진다. 마지막으로 맞은 때부터 30분 유지, 그동안 다시 안 맞으면 꺼진다.
  외국인 자료를 못 받으면 켜지지 않는다(지수만으로는 판정하지 않음 — 형 조건은 둘 다).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from statistics import mean

KST = timezone(timedelta(hours=9))

ENABLED = True
WINDOW = 60                    # 분
STEP = 20                      # 20분 간격 연속 하락
INDEX_DROP = -0.007            # 60분 -0.7% 이하
REBOUND_MAX = 0.35             # 저점 대비 되돌림이 범위의 35% 이하면 반등 실패
FOREIGN_SELL_EOK = -3000.0     # 코스피+코스닥 외국인 현물 순매수 합(억원) 이 값 이하
HOLD_MINUTES = 30              # 한 번 켜지면 최소 유지
CHECK_SECONDS = 60.0           # 판정 주기
SESSION_START = time(9, 0)
SESSION_END = time(15, 30)


def _stamp(bar) -> datetime:
    value = bar["timestamp"] if isinstance(bar, dict) else getattr(bar, "timestamp")
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _close(bar) -> float:
    return float(bar["close"] if isinstance(bar, dict) else getattr(bar, "close"))


def _hl(bar) -> tuple[float, float]:
    if isinstance(bar, dict):
        return float(bar["high"]), float(bar["low"])
    return float(getattr(bar, "high")), float(getattr(bar, "low"))


def today_bars(bars, now: datetime) -> list:
    day = now.astimezone(KST).date()
    rows = [b for b in bars if _stamp(b).date() == day
            and SESSION_START <= _stamp(b).time() < SESSION_END]
    return sorted(rows, key=_stamp)


def index_falling(bars, now: datetime) -> tuple[bool, str]:
    """①+② 한 지수 판정. 완성 1분봉만 넣는다."""
    rows = today_bars(bars, now)
    if len(rows) < WINDOW + 1:
        return False, f"지수봉부족 {len(rows)}/{WINDOW + 1}"
    closes = [_close(b) for b in rows]
    window = rows[-(WINDOW + 1):]
    c = [_close(b) for b in window]
    if c[0] <= 0:
        return False, "지수가격오류"
    ret = c[-1] / c[0] - 1.0
    steps = [c[0], c[STEP], c[2 * STEP], c[-1]]
    stair = all(a > b for a, b in zip(steps, steps[1:]))
    highs, lows = zip(*(_hl(b) for b in window))
    high, low = max(highs), min(lows)
    span = high - low
    rebound = (c[-1] - low) / span if span > 0 else 0.0
    ma20 = mean(closes[-20:])
    failed = rebound <= REBOUND_MAX and c[-1] < ma20
    text = f"60분{ret:+.2%} 계단{'O' if stair else 'X'} 되돌림{rebound:.0%} 20분선{'아래' if c[-1] < ma20 else '위'}"
    return (ret <= INDEX_DROP and stair and failed), text


def foreign_total_eok(rows_by_market) -> float | None:
    """ka10051 응답들(코스피, 코스닥)에서 종합 줄 외국인 순매수 합(억원). 못 읽으면 None."""
    total, seen = 0.0, 0
    for rows in rows_by_market:
        row = next((r for r in rows or [] if str(r.get("inds_cd", "")).split("_")[0] in {"001", "101"}
                    or "종합" in str(r.get("inds_nm", ""))), None)
        if row is None:
            return None
        text = str(row.get("frgnr_netprps", "")).replace(",", "").replace("+", "").strip()
        try:
            total += float(text)
        except ValueError:
            return None
        seen += 1
    return total if seen else None


def decide(index_results: list[tuple[bool, str]], foreign_eok: float | None) -> tuple[bool, str]:
    falling = [text for ok, text in index_results if ok]
    detail = " / ".join(text for _ok, text in index_results)
    if foreign_eok is None:
        return False, f"외국인자료없음(판정안함) | {detail}"
    sell = foreign_eok <= FOREIGN_SELL_EOK
    reason = f"외국인현물 {foreign_eok:+,.0f}억 | {detail}"
    return bool(falling) and sell, reason


class Latch:
    """마지막으로 조건이 맞은 때부터 HOLD_MINUTES 동안 유지."""

    def __init__(self) -> None:
        self.last_trigger: datetime | None = None
        self.reason = ""

    def update(self, triggered: bool, reason: str, now: datetime) -> bool:
        if triggered:
            self.last_trigger = now
            self.reason = reason
            return True
        if self.last_trigger is not None and now - self.last_trigger < timedelta(minutes=HOLD_MINUTES):
            return True
        self.last_trigger = None
        self.reason = reason
        return False
