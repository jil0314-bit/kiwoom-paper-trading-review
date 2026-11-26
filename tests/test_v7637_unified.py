"""V7637 (2026-11-26 형 결정): 1차 진입 = 눌림목 + 통합(투매 2회형 바닥). 돌파·옛 바닥·패턴A/B 끔. 급등 제외 5일50%/3일30%."""
from __future__ import annotations
import sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "SOURCE" / "core"))
import v7609_prebuy_rules as P

KST = timezone(timedelta(hours=9))
PREV = dict(previous_close=10000.0, previous_volume=100000.0, previous_day="2026-11-25")


def bar(hm, o, h, l, c, v):
    hh, mm = map(int, hm.split(":"))
    return dict(timestamp=datetime(2026, 11, 26, hh, mm, tzinfo=KST), open=o, high=h, low=l, close=c, volume=v)


def scenario(second_flush=True, break_floor=False):
    """09:00~09:14 거래 터지며 +6.5% → 09:20·09:25 투매 음봉 → 09:26 가장 작은 거래 양봉(바닥) → 09:27 5선 위 종가."""
    rows = []
    price = 10000
    for m in range(10):                      # 09:00~09:09 잔잔
        rows.append(bar(f"09:{m:02d}", price, price + 20, price - 10, price + 10, 1000)); price += 10
    for m in range(10, 15):                  # 09:10~09:14 거래 폭발 상승
        rows.append(bar(f"09:{m:02d}", price, price + 120, price - 10, price + 110, 5000)); price += 110
    peak_close = price                       # 약 10650
    closes = [peak_close - 30, peak_close - 60, peak_close - 80, peak_close - 100, peak_close - 120]
    for m, c in zip(range(15, 20), closes):  # 09:15~09:19 완만한 눌림
        rows.append(bar(f"09:{m:02d}", c + 20, c + 30, c - 10, c, 1500))
    rows.append(bar("09:20", 10530, 10535, 10380, 10400, 9000))          # 투매 ①
    for m, c in zip(range(21, 25), (10420, 10450, 10440, 10430)):
        rows.append(bar(f"09:{m:02d}", c - 10, c + 10, c - 20, c, 1500))
    if second_flush:
        rows.append(bar("09:25", 10430, 10435, 10280, 10300, 9000))      # 투매 ②
    else:
        rows.append(bar("09:25", 10430, 10435, 10290, 10300, 1600))      # 거래 없는 하락(투매 아님)
    rows.append(bar("09:26", 10290, 10305, 10285, 10300, 300))           # 바닥: 가장 작은 거래 양봉
    if break_floor:
        rows.append(bar("09:27", 10300, 10310, 10150, 10200, 2000))      # 투매 저가 10280의 -1% 아래로 이탈
    else:
        rows.append(bar("09:27", 10300, 10425, 10295, 10420, 1200))      # 1분 5선 위 종가 → 매수
    return rows


def judge(rows, unified_only=True):
    now = rows[-1]["timestamp"] + timedelta(minutes=1, seconds=5)
    if unified_only:
        with mock.patch.object(P, "PULLBACK_ENABLED", False):
            return P.evaluate(rows, now=now, **PREV)
    return P.evaluate(rows, now=now, **PREV)


class V7637DefaultTests(unittest.TestCase):
    def test_switches(self):
        self.assertFalse(P.BREAKOUT_ENABLED)
        self.assertFalse(P.FLOOR_ENABLED)
        self.assertFalse(P.PATTERN12_ENABLED)
        self.assertTrue(P.PULLBACK_ENABLED)
        self.assertTrue(P.UNIFIED_ENABLED)
        self.assertEqual(P.UNIFIED_RISE, 0.04)            # 형과 4%로 합의
        self.assertEqual(P.UNIFIED_FLUSHES, 2)
        self.assertEqual(P.UNIFIED_SETTLE, 1)
        self.assertEqual(P.UNIFIED_FLUSH_MULT, 3.0)


class V7637UnifiedTests(unittest.TestCase):
    def test_buys_on_first_close_above_ma5_after_two_flushes(self):
        rows = scenario()
        r = judge(rows)
        self.assertEqual((r["route"], r["buy"]), ("UNIFIED", True), r and r.get("reason"))
        self.assertIn("투매2회", r["reason"])
        self.assertEqual(r["data_time"], rows[-1]["timestamp"].isoformat())
        with mock.patch.object(P, "PULLBACK_ENABLED", False):   # 주문 직전 재검증도 같은 판정
            self.assertTrue(P.validate_evidence(r, r["data_time"], (rows[-1]["timestamp"] + timedelta(seconds=70)).isoformat()))

    def test_waits_on_bottom_bar(self):
        r = judge(scenario()[:-1])                         # 09:26 바닥 봉까지 → 5선 아래, 매수직전
        self.assertEqual((r["route"], r["buy"]), ("UNIFIED_WAIT", False))

    def test_one_flush_is_not_enough(self):
        r = judge(scenario(second_flush=False))
        self.assertFalse(r and r.get("route", "").startswith("UNIFIED"))

    def test_floor_break_resets(self):
        r = judge(scenario(break_floor=True))
        self.assertFalse(r and r.get("buy"))

    def test_signal_used_once(self):
        rows = scenario()
        rows.append(bar("09:28", 10420, 10440, 10410, 10430, 900))
        r = judge(rows)
        self.assertFalse(r and r.get("route") == "UNIFIED" and r.get("buy"))   # 지난 봉의 신호는 다시 안 씀

    def test_early_signal_gives_up_day(self):
        with mock.patch.object(P, "UNIFIED_FROM", datetime(2026, 1, 1, 9, 30).time()):
            r = judge(scenario())
        self.assertFalse(r and r.get("buy"))

    def test_below_previous_close_not_bought(self):
        rows = scenario()
        now = rows[-1]["timestamp"] + timedelta(minutes=1, seconds=5)
        with mock.patch.object(P, "PULLBACK_ENABLED", False):
            r = P.evaluate(rows, now=now, **dict(PREV, previous_close=10500.0))
        self.assertFalse(r and r.get("buy"))

    def test_disabled_routes_do_not_buy(self):
        rows = scenario()
        with mock.patch.object(P, "UNIFIED_ENABLED", False), mock.patch.object(P, "PULLBACK_ENABLED", False):
            r = judge(rows, unified_only=False)
        self.assertFalse(r and r.get("buy"))               # 돌파·옛 바닥·패턴은 꺼져 있어 사지 않음

    def test_sharp_market_check_uses_bottom_bar(self):
        # 지수 급락장(SHARP_DECLINE)에서는 옛 바닥매수처럼 '바닥 봉 뒤 짧은 양봉'을 한 번 더 요구한다.
        r = judge(scenario())
        self.assertEqual(r["bars"][r["floor_index"]]["timestamp"], "2026-11-26T09:26:00+09:00")
        self.assertFalse(P.sharp_market_entry_allowed(r))  # 09:27은 긴 양봉 → 급락장에선 보류


class V7637HotRuleTests(unittest.TestCase):
    def test_hot_rules(self):
        sys.modules.setdefault("msvcrt", type(sys)("msvcrt"))
        import importlib
        try:
            engine = importlib.import_module("bot_v76_00_three_stage_ctrlv_paper")
        except Exception as exc:   # 윈도우 전용 모듈 등 — 순수 함수만 따로 확인
            self.skipTest(f"engine import: {exc}")
        hit = engine._v7637_hot_hit
        self.assertEqual(hit([100, 100, 100, 110, 120, 131])[0], 3)          # 3일 +31%
        self.assertEqual(hit([100, 110, 120, 125, 130, 152])[0], 5)          # 5일 +52%
        self.assertIsNone(hit([100, 100, 110, 120, 125, 129]))               # 둘 다 미달
        self.assertIsNone(hit([100, 129]))                                   # 자료 부족


if __name__ == "__main__":
    unittest.main()
