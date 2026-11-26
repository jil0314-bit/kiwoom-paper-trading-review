"""V7619 시험: 1차 100만·운용한도 5,000만 / 초보수 모드 / 최근5분 자료 보강 (주문·네트워크 없음)."""
from __future__ import annotations
import math, sys, tempfile, types, unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base   # 같은 대역 브로커·probe 재사용
engine, R, KST, V753 = base.engine, base.R, base.KST, base.V753
import v7619_conservative_rules as C

NOW = datetime(2026, 11, 10, 11, 30, 5, tzinfo=KST)


def index_bars(start_price, per_min, n=80, end=NOW):
    first = end.replace(second=0) - timedelta(minutes=n)
    rows, price = [], start_price
    for k in range(n):
        o = price; price = price * (1 + per_min)
        rows.append(V753.Bar("069500", first + timedelta(minutes=k), o, max(o, price) * 1.0001, min(o, price) * 0.9999, price, 1000))
    return rows


class RulesTests(unittest.TestCase):
    def test_thin_value_filter(self):
        import tempfile as _t
        item = base.probe(Path(_t.mkdtemp()))
        day = datetime.now(KST).replace(hour=9, minute=0, second=0, microsecond=0)
        bars = [V753.Bar("123450", day + timedelta(minutes=k), 10000, 10000, 10000, 10000, 1000) for k in range(60)]  # 6억
        item.client = SimpleNamespace(v7580_raw_cache={"123450": bars})
        item.candidate_meta = {}
        self.assertEqual(item._v7625_thin_reason("123450", day.replace(hour=9, minute=59)), "")     # 10시 전 미적용
        self.assertIn("< 10억", item._v7625_thin_reason("123450", day.replace(hour=10, minute=1)))
        item.candidate_meta = {"123450": SimpleNamespace(volume=250_000, price=-10000.0)}            # 순위자료 25억
        self.assertEqual(item._v7625_thin_reason("123450", day.replace(hour=12, minute=30)), "")
        self.assertIn("< 30억", item._v7625_thin_reason("123450", day.replace(hour=13, minute=5)))
        self.assertEqual(item._v7625_thin_reason("999999", day.replace(hour=13, minute=5)), "")      # 자료 없으면 막지 않음
        rows = [{"symbol": "123450", "stage": "관찰후보", "reason": "x"}, {"symbol": "123450", "stage": "매수종목", "reason": "y"}]
        item._v7613_decorate_rows(rows, day.replace(hour=13, minute=5))
        self.assertTrue(rows[0]["reason"].startswith("[거래부족·자동매수 제외]"), rows[0]["reason"])
        self.assertFalse(rows[1]["reason"].startswith("[거래부족"))

    def test_sizing(self):
        self.assertEqual(R.NOTIONALS, (1_000_000.0, 2_000_000.0, 6_000_000.0))   # V7633 1:2:6
        self.assertEqual(R.PLAN_TOTAL, 9_000_000.0)
        self.assertEqual(R.NEW_ENTRY_BUFFER, 6_000_000.0)
        self.assertEqual(R.BUDGET_LIMIT, 50_000_000.0)

    def test_index_staircase_decline_is_falling(self):
        ok, text = C.index_falling(index_bars(30000, -0.0002), NOW)   # 60분 약 -1.2%
        self.assertTrue(ok, text)

    def test_small_decline_or_rebound_is_not(self):
        self.assertFalse(C.index_falling(index_bars(30000, -0.00005), NOW)[0])   # -0.3%
        bars = index_bars(30000, -0.0002)
        last = bars[-1]
        bounce = [V753.Bar("069500", last.timestamp + timedelta(minutes=k + 1), last.close, last.close * 1.004,
                           last.close, last.close * 1.004, 1000) for k in range(3)]
        self.assertFalse(C.index_falling(bars[3:] + bounce, NOW + timedelta(minutes=3))[0])

    def test_too_few_bars(self):
        self.assertFalse(C.index_falling(index_bars(30000, -0.001, n=30), NOW)[0])

    def test_foreign_parse_and_decide(self):
        kospi = [{"inds_cd": "001_AL", "inds_nm": "종합(KOSPI)", "frgnr_netprps": "-2,100"}, {"inds_cd": "002_AL", "frgnr_netprps": "-9"}]
        kosdaq = [{"inds_cd": "101_AL", "inds_nm": "종합(KOSDAQ)", "frgnr_netprps": "-1000"}]
        total = C.foreign_total_eok([kospi, kosdaq])
        self.assertEqual(total, -3100.0)
        self.assertTrue(C.decide([(True, "a"), (False, "b")], total)[0])
        self.assertFalse(C.decide([(True, "a")], -2900.0)[0])
        self.assertFalse(C.decide([(False, "a")], -9000.0)[0])
        self.assertFalse(C.decide([(True, "a")], None)[0])          # 외국인 자료 없으면 켜지지 않음
        self.assertIsNone(C.foreign_total_eok([[{"inds_cd": "002_AL"}]]))

    def test_latch_holds_30_minutes_after_last_trigger(self):
        latch = C.Latch()
        self.assertTrue(latch.update(True, "x", NOW))
        self.assertTrue(latch.update(False, "y", NOW + timedelta(minutes=29)))
        self.assertFalse(latch.update(False, "y", NOW + timedelta(minutes=30)))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.patches = [mock.patch.object(engine, "V7613_STATE_FILE", self.folder / "split.json"),
                        mock.patch.object(engine, "V7613_VIEW_FILE", self.folder / "view.json"),
                        mock.patch.object(engine, "V7630_CARRY_OUTSIDE_BUDGET", False)]   # 실전 방식(보유 전체가 한도)
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def open_split(self, item, symbol, price=10000.0):
        qty = math.floor(R.FIRST_NOTIONAL / price)
        base.SYMBOL_SAVE = symbol
        fee = price * qty * item.broker.settings.commission_pct
        item.broker.cash -= price * qty + fee
        item.broker.positions[symbol] = V753.Position(symbol, qty, price, NOW, price, price * 0.985, price * 1.02, fee, engine.PATTERN, True)
        item._v7613_register_first(symbol, SimpleNamespace(pattern=engine.PATTERN, reason="시험"))

    def big(self, item, symbol, amount, price=10000.0):
        qty = int(amount / price)
        item.broker.positions[symbol] = V753.Position(symbol, qty, price, NOW, price, price * 0.9, price * 1.02, 0.0, engine.PATTERN, True)

    def test_budget_counts_actual_spend_with_buffer(self):
        """V7624: 실제 매입금액 + 1차 100만 + 여유(3차 자리, V7633 600만) <= 5,000만일 때만 새 1차."""
        item = base.probe(self.folder, cash=1_000_000_000.0)
        for n in range(12):                                   # 1차만 12종목(1,200만) → 계속 허용
            self.assertTrue(item._v7613_cash_allows_new(f"9{n:05d}"), n)
            self.open_split(item, f"9{n:05d}")
        self.big(item, "800001", 28_000_000.0)                # 실제 매입 4,000만
        self.assertAlmostEqual(item._v7613_committed(), 40_000_000.0, delta=200_000)
        self.assertTrue(item._v7613_cash_allows_new("999999"))
        self.big(item, "800002", 3_500_000.0)                 # 4,350만 + 100만 + 600만 > 5,000만 (V7633 1:2:6)
        self.assertFalse(item._v7613_cash_allows_new("999999"))
        self.assertTrue(any("운용한도 신규 1차 보류" in str(t) for _k, t in item.logger.lines))
        item.broker.positions.pop("800001")
        self.assertTrue(item._v7613_cash_allows_new("999999"))

    def test_add_skipped_when_budget_short_then_position_kept(self):
        item = base.probe(self.folder, cash=1_000_000_000.0)
        self.open_split(item, "900000")
        self.big(item, "800001", 47_500_000.0)                # 4,850만 사용 → 2차 200만 자리 없음
        split = item._v7613_info("900000")
        called = []
        item._v7613_execute_add = lambda *a: called.append(a) or True
        fresh = dict(fresh_trigger=True, trigger_time="2026-11-10T11:29:00+09:00", state_text="x", reason="r")
        with mock.patch.object(engine._SPLIT13, "replay", return_value=fresh), \
             mock.patch.object(engine._SPLIT13, "add_window", return_value=True):
            bar = V753.Bar("900000", NOW.replace(second=0) - timedelta(minutes=1), 1, 1, 1, 1, 1)
            item._v7613_regular_bars = lambda raw: [bar]
            self.assertFalse(item._v7613_try_add("900000", [bar], NOW))
            self.assertEqual(called, [])
            self.assertIn("건너뜀", split["watch"])
            self.assertIn("900000", item.broker.positions)     # 그대로 보유
            item.broker.positions.pop("800001")
            fresh["trigger_time"] = "2026-11-10T11:40:00+09:00"
            self.assertTrue(item._v7613_try_add("900000", [bar], NOW))
            self.assertEqual(len(called), 1)

    def _cons_item(self, foreign):
        item = base.probe(self.folder)
        item.v757_config = SimpleNamespace(market_proxy_codes=("069500", "229200"))
        falling = index_bars(30000, -0.0002)
        flat = index_bars(10000, 0.0)
        calls = []
        def query(*, api_id, endpoint, body, required_any, extra_headers=None):
            calls.append((api_id, body["mrkt_tp"]))
            code = "001_AL" if body["mrkt_tp"] == "0" else "101_AL"
            return {"inds_netprps": [{"inds_cd": code, "frgnr_netprps": str(foreign / 2)}]}
        item.client = SimpleNamespace(v7580_raw_cache={}, _v7628_query=query,
                                      get_minute_bars=lambda code, limit, completed_only: falling if code == "069500" else flat)
        return item, calls

    def test_conservative_on_blocks_adds_and_new(self):
        item, calls = self._cons_item(-4000.0)
        active, reason = item._v7619_conservative(NOW)
        self.assertTrue(active, reason)
        self.assertEqual([c[0] for c in calls], ["ka10051", "ka10051"])
        self.assertFalse(item._scale_v76_from_raw("123450", [], NOW))
        self.assertTrue(any("초보수모드 추가매수 중단" in str(t) for _k, t in item.logger.lines))
        self.assertTrue(item._v7619_blocked("123450", "신규1차", NOW))

    def test_conservative_off_when_foreign_small(self):
        item, _ = self._cons_item(-1500.0)
        self.assertFalse(item._v7619_conservative(NOW)[0])

    def test_no_foreign_query_when_index_fine(self):
        item, calls = self._cons_item(-9000.0)
        flat = index_bars(10000, 0.0)
        item.client.get_minute_bars = lambda code, limit, completed_only: flat
        self.assertFalse(item._v7619_conservative(NOW)[0])
        self.assertEqual(calls, [])

    def test_outside_session_never_active(self):
        item, calls = self._cons_item(-9000.0)
        self.assertFalse(item._v7619_conservative(NOW.replace(hour=16))[0])
        self.assertEqual(calls, [])

    # ---------------- 최근5분 자료 보강
    def _liq_item(self, volume):
        item = base.probe(self.folder)
        item.strategy = SimpleNamespace(bars={}, same_time_volume_ratio={"123450": 3.0})
        item.min_recent5_value = 150_000_000.0
        item.min_signal_bar_value = 30_000_000.0
        item.min_same_time_volume_ratio = 1.5
        item._minimum_cumulative_value = lambda now: 5_000_000_000.0
        item._record_entry_decision = lambda *a: None
        item._intrabar_snapshots = {}
        today = datetime.now(KST).replace(hour=10, minute=0, second=0, microsecond=0)
        raw = [V753.Bar("123450", today + timedelta(minutes=k), 10000, 10010, 9990, 10000, volume) for k in range(10)]
        item.client = SimpleNamespace(v7580_raw_cache={"123450": raw})
        signal = SimpleNamespace(symbol="123450", pattern=engine.PATTERN)
        quote = SimpleNamespace(cumulative_value=8_000_000_000.0, cumulative_volume=0, price=10000.0)
        return item, signal, quote

    def test_liquidity_uses_minute_cache_when_strategy_bars_empty(self):
        item, signal, quote = self._liq_item(volume=5000)          # 1분 5천만원
        ok, reason = item._liquidity_gate(signal, quote)
        self.assertTrue(ok, reason)
        self.assertIn("1분봉보강", reason)

    def test_liquidity_thresholds_unchanged(self):
        item, signal, quote = self._liq_item(volume=2000)          # 5분 1억 < 1.5억
        ok, reason = item._liquidity_gate(signal, quote)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("최근5분거래대금부족"), reason)

    def test_liquidity_still_short_without_cache(self):
        item, signal, quote = self._liq_item(volume=5000)
        item.client.v7580_raw_cache = {}
        self.assertEqual(item._liquidity_gate(signal, quote), (False, "최근5분자료부족"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
