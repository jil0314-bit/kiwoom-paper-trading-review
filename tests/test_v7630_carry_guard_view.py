"""V7630 시험: 신호봉 저가 확인, 보유 칸 나누기(오늘/전일/전전일~), 차트 드래그 확대 계산. 주문·네트워크 없음."""
from __future__ import annotations
import subprocess, sys, types, unittest
from datetime import date
from pathlib import Path

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base
engine = base.engine
import v7609_trade_view as V
import v7614_chart_panel as C


class GuardTests(unittest.TestCase):
    def test_signal_low_is_taken_from_the_signal_bar(self):
        bars = [dict(timestamp="2026-11-17T09:13:00+09:00", low=3425.0), dict(timestamp="2026-11-17T09:14:00+09:00", low=3415.0)]
        proof = dict(bars=bars, data_time="2026-11-17T09:14:00+09:00")
        self.assertEqual(engine._v7630_signal_low(proof), 3415.0)       # 노랑풍선: 3,405 매수는 이 값 아래 → 취소 대상
        self.assertEqual(engine._v7630_signal_low(dict(bars=bars, data_time="없음")), 3415.0)
        self.assertEqual(engine._v7630_signal_low({}), 0.0)
        self.assertEqual(engine._v7630_signal_low(dict(bars=[{"timestamp": "x"}], data_time="x")), 0.0)

    def test_guard_is_wired_before_the_order(self):
        source = Path(engine.__file__).read_text(encoding="utf-8")
        guard = source.index("signal_low = _v7630_signal_low(proof)")
        self.assertLess(guard, source.index("pattern = V7612_SLOT_PATTERN.get(slot, PATTERN)", guard))
        self.assertIn("if signal_low > 0 and price < signal_low:", source)


class CarryBudgetTests(unittest.TestCase):
    """V7630b: 이월한 보유는 운용한도에서 빠지고, 오늘 매입분만 센다."""
    def test_only_todays_buys_count(self):
        import tempfile
        from datetime import datetime, timedelta
        from types import SimpleNamespace
        from unittest import mock
        folder = Path(tempfile.mkdtemp())
        with mock.patch.object(engine, "V7613_STATE_FILE", folder / "s.json"), mock.patch.object(engine, "V7613_VIEW_FILE", folder / "v.json"):
            item = base.probe(folder, cash=1_000_000_000.0)
            now = datetime.now(base.KST)
            V = base.V753
            def hold(symbol, amount, when, fills=None):
                item.broker.positions[symbol] = V.Position(symbol, int(amount / 10000), 10000.0, when, 10000.0, 9000.0, 10200.0, 0.0, engine.PATTERN, True)
                item._v7613_info(symbol)                                 # 원장 준비
                if fills is not None:
                    item._v7613_split[symbol] = {"symbol": symbol, "tranche": len(fills), "fills": fills}
            self.assertTrue(engine.V7630_CARRY_OUTSIDE_BUDGET)
            y = (now - timedelta(days=1)).isoformat()
            hold("111111", 13_000_000, now - timedelta(days=1), [{"time": y, "notional": 1_000_000.0}, {"time": y, "notional": 12_000_000.0}])
            hold("222222", 13_000_000, now - timedelta(days=2), [{"time": y, "notional": 13_000_000.0}])
            hold("333333", 40_000_000, now - timedelta(days=3))          # 분할 원장 없는 옛 보유도 이월분
            self.assertEqual(item._v7613_committed(), 0.0)
            self.assertTrue(item._v7613_cash_allows_new("999999"))       # 6,600만 보유 중이어도 오늘은 새로 살 수 있음
            # 이월 종목에 오늘 추가매수한 금액과 오늘 새로 산 종목은 센다
            item._v7613_split["111111"]["fills"].append({"time": now.isoformat(), "notional": 9_000_000.0})
            hold("444444", 1_000_000, now, [{"time": now.isoformat(), "notional": 1_000_000.0}])
            self.assertEqual(item._v7613_committed(), 10_000_000.0)
            with mock.patch.object(engine, "V7630_CARRY_OUTSIDE_BUDGET", False):   # 실전 스위치: 보유 전체
                self.assertAlmostEqual(item._v7613_committed(), 67_000_000.0, delta=1.0)
                self.assertFalse(item._v7613_cash_allows_new("999999"))


class SectionTests(unittest.TestCase):
    def test_sections_by_buy_day(self):
        monday = date(2026, 11, 16)
        self.assertEqual(V.previous_trading_day(monday), date(2026, 11, 13))        # 월요일의 전일 = 금요일
        self.assertEqual(V.hold_section("2026-11-16", monday), 0)
        self.assertEqual(V.hold_section("2026-11-13", monday), 1)
        self.assertEqual(V.hold_section("2026-11-12", monday), 2)
        self.assertEqual(V.hold_section("", monday), 0)
        rows = [dict(name="가", buy_date="2026-11-12"), dict(name="나", buy_date="2026-11-16"), dict(name="다", buy_date="2026-11-13")]
        self.assertEqual([[r["name"] for r in g] for _, g in V.sectioned(rows, monday)], [["나"], ["다"], ["가"]])
        self.assertEqual(len(V.sectioned([], monday)), 3)                           # 빈 칸도 항상 표시


class ZoomTests(unittest.TestCase):
    def test_zoom_window(self):
        self.assertEqual(C.zoom_window(100, 159, 600), (60, 440))      # 60봉, 오른쪽 끝에서 440봉 과거
        self.assertEqual(C.zoom_window(159, 100, 600), (60, 440))      # 오른쪽→왼쪽 드래그도 같음
        self.assertEqual(C.zoom_window(599, 599, 600), (5, 0))         # 너무 좁으면 최소 5봉
        self.assertEqual(C.zoom_window(0, 1, 600)[0], 5)
        self.assertIsNone(C.zoom_window(None, 3, 600))
        self.assertIsNone(C.zoom_window(1, 3, 0))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
