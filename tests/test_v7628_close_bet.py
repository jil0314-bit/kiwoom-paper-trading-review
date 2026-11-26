"""V7628 시험: 일봉 종가배팅(검색 → 15:15 매수 → 다음날 시가 수익매도/+2% 대기/15:19 정리). 주문·네트워크 없음."""
from __future__ import annotations
import json, math, sys, tempfile, types, unittest, subprocess
from datetime import datetime, timedelta, date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base
engine, KST, V753 = base.engine, base.KST, base.V753
import v7628_close_bet_rules as C

MONDAY = datetime(2026, 11, 16, tzinfo=KST)


def daily_rows(today="20261116", last_close=10300.0, last_open=10100.0):
    """장대양봉(8일 전 +15%, 거래 10배) → 조정 -12% → 거래바닥 → 오늘 5일선 위 양봉 +3%."""
    rows, d = [], datetime.strptime(today, "%Y%m%d") - timedelta(days=60)
    def add(o, h, l, c, v):
        nonlocal d
        d += timedelta(days=1)
        rows.append(dict(d=d.strftime("%Y%m%d"), o=float(o), h=float(h), l=float(l), c=float(c), v=float(v)))
    for _ in range(30): add(10000, 10050, 9950, 10000, 100_000)
    add(10000, 11600, 10000, 11500, 1_200_000)                       # 장대양봉 +15%
    for c in (11300, 11000, 10700, 10400, 10200, 10100, 10000):      # 조정
        add(c + 100, c + 150, c - 50, c, 150_000)
    rows[-2]["v"] = 100_000.0                                         # 거래바닥(장대양봉의 8%)
    add(last_open, max(last_open, last_close) + 20, min(last_open, last_close) - 20, last_close, 400_000)
    rows[-1]["d"] = today
    return rows


class RuleTests(unittest.TestCase):
    def test_signal_and_breaks(self):
        self.assertIsNotNone(C.signal(daily_rows()))
        self.assertIsNone(C.signal(daily_rows(last_close=9990.0, last_open=10050.0)))     # 오늘 음봉·하락
        self.assertIsNone(C.signal(daily_rows(last_close=10900.0)))                        # +9% 추격 금지
        rows = daily_rows(); rows[30]["v"] = 200_000.0                                     # 거래폭발 없음
        self.assertIsNone(C.signal(rows))
        rows = daily_rows(); rows[-1]["v"] = 100_000.0                                     # 오늘 거래대금 10억
        self.assertIsNone(C.signal(rows))

    def test_prefilter(self):
        ok = {"stk_cd": "123450", "stk_nm": "가나다", "cur_prc": "+10300", "flu_rt": "+3.00", "trde_prica": "4120"}
        self.assertEqual(C.prefilter(ok), ("123450", "가나다", 4_120_000_000.0))
        for patch in ({"flu_rt": "+9.1"}, {"trde_prica": "2000"}, {"stk_nm": "KODEX 200"}, {"cur_prc": "900"}):
            self.assertIsNone(C.prefilter(dict(ok, **patch)), patch)
        self.assertIsNotNone(C.prefilter(dict(ok, flu_rt="-1.0")))      # V7631: 내린 날도 후보(5일선 위면 매수)
        with mock.patch.object(C, "ANY_SIGNAL", False):
            self.assertIsNone(C.prefilter(dict(ok, flu_rt="-1.0")))

    def test_v7631_many_bottom_shapes(self):
        """거래바닥 뒤 아래꼬리/작은양봉/5일선돌파가 있고 오늘 5일선 위면 음봉·하락이어도 매수."""
        rows = daily_rows(last_close=10200.0, last_open=10250.0)        # 오늘 음봉(시가보다 낮게 끝남), 아래꼬리 짧음
        rows[-1].update(l=10180.0, h=10270.0)                            # 예전 규칙의 '긴 아래꼬리'에도 해당 안 됨
        got = C.signal(rows)
        self.assertIsNotNone(got)
        self.assertIn("바닥신호(", got["reason"])
        self.assertIn("5일선위 지지", got["reason"])
        with mock.patch.object(C, "ANY_SIGNAL", False):
            self.assertIsNone(C.signal(rows))                           # 예전 규칙이면 음봉이라 제외
        flat = daily_rows(); flat[-1].update(c=flat[-1]["c"], o=flat[-1]["c"])
        below = daily_rows(last_close=9800.0, last_open=9850.0)          # 5일선 아래면 어떤 모양이든 제외
        self.assertIsNone(C.signal(below))

    def test_windows(self):
        self.assertTrue(C.scan_window(MONDAY.replace(hour=15, minute=1)))
        self.assertFalse(C.scan_window(MONDAY.replace(hour=14, minute=59)))
        self.assertFalse(C.scan_window((MONDAY - timedelta(days=1)).replace(hour=15, minute=5)))   # 일요일
        self.assertFalse(C.buy_window(MONDAY.replace(hour=15, minute=19)))
        nxt = MONDAY + timedelta(days=1)
        self.assertTrue(C.open_sell_due(nxt.replace(hour=9, minute=1), "2026-11-16", ""))
        self.assertFalse(C.open_sell_due(nxt.replace(hour=9, minute=0, second=10), "2026-11-16", ""))
        self.assertFalse(C.open_sell_due(nxt.replace(hour=9, minute=1), "2026-11-16", "2026-11-17"))  # 하루 한 번
        self.assertFalse(C.open_sell_due(MONDAY.replace(hour=16), "2026-11-16", ""))                   # 산 날은 아님


class Broker(base.FakeBroker):
    def buy(self, symbol, quantity, quote, reason, *, stop_loss_pct=None, take_profit_pct=None, pattern=""):
        price = float(quote.best_ask)
        fee = price * quantity * self.settings.commission_pct
        self.cash -= price * quantity + fee
        self.positions[symbol] = V753.Position(symbol, quantity, price, datetime.now(KST), price, price * 0.9, price * 1.02, fee, pattern, True)
        self.sequence += 1
        fill = V753.Fill(f"PAPER-{self.sequence:06d}", datetime.now(KST), symbol, "BUY", quantity, price, fee, 0.0, 0.0, reason)
        self._record_fill(fill)
        return fill


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(engine, "V7613_STATE_FILE", self.folder / "split.json"),
                        mock.patch.object(engine, "V7613_VIEW_FILE", self.folder / "view.json"),
                        mock.patch.object(engine, "V7620_OVERRIDE_FILE", self.folder / "ov.json"),
                        mock.patch.object(engine, "V7628_STATE_FILE", self.folder / "cb.json"),
                        mock.patch.object(engine.time, "monotonic", side_effect=iter(range(0, 100000, 5)))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def item(self, names=("가", "나", "다"), price=10300.0):
        item = base.probe(self.folder)
        item.broker = Broker()
        item.settings = item.broker.settings
        item._v7609_cost_rates = lambda: (0.00015, 0.0018)
        item._v7619_conservative = lambda now=None: (False, "")
        item.strategy = SimpleNamespace(mark_entry=lambda s: None)
        item.last_position_observation_at, item.latest_quotes, item.buy_blocked, item._v7587_excluded = {}, {}, set(), {}
        item._print_buy = lambda *a: None
        codes = [f"{100000 + k:06d}" for k in range(len(names))]
        pages = [{"trde_prica_upper": [{"stk_cd": c, "stk_nm": n, "cur_prc": "+10300", "flu_rt": "+3.00",
                                         "trde_prica": str(9000 - 100 * k)} for k, (c, n) in enumerate(zip(codes, names))]
                  + [{"stk_cd": "999999", "stk_nm": "KODEX 인버스", "cur_prc": "5000", "flu_rt": "+1", "trde_prica": "99999"}]}]
        calls = {"rank": 0, "daily": []}
        def rank(api, body, max_pages=5):
            calls["rank"] += 1
            return pages if body["mrkt_tp"] == "001" else []
        def get_daily(symbol, limit=45):
            calls["daily"].append(symbol)
            rows = daily_rows() if symbol != codes[-1] else daily_rows(last_close=9990.0, last_open=10050.0)
            return [SimpleNamespace(symbol=symbol, trading_date=datetime.strptime(r["d"], "%Y%m%d").date(), open=r["o"],
                                    high=r["h"], low=r["l"], close=r["c"], volume=int(r["v"])) for r in rows]
        quote = lambda s: SimpleNamespace(symbol=s, price=price, best_ask=price, best_bid=price - 10)
        item.client = SimpleNamespace(v7580_raw_cache={}, settings=SimpleNamespace(stex_tp="1"), _rank_pages=rank,
                                      get_daily_bars=get_daily, get_quote=quote, get_orderbook=lambda s, q, side: q)
        return item, codes, calls

    def run_day(self, item, minutes=((15, 0), (15, 1), (15, 2), (15, 3), (15, 15), (15, 16), (15, 17), (15, 18))):
        for hh, mm in minutes:
            item._v7628_step(MONDAY.replace(hour=hh, minute=mm, second=5))

    def test_scan_then_buy_top_by_value_and_register(self):
        item, codes, calls = self.item()
        item._v7628_step(MONDAY.replace(hour=14, minute=59))
        self.assertEqual(calls["rank"], 0)                                   # 15:00 전에는 아무것도 안 함
        self.run_day(item)
        self.assertEqual(calls["rank"], 2)                                   # 코스피·코스닥 한 번씩
        self.assertEqual(calls["daily"], codes)                              # ETF 제외, 종목당 일봉 1번
        self.assertEqual(sorted(item.broker.positions), codes[:2])           # 셋째는 패턴 아님
        pos = item.broker.positions[codes[0]]
        self.assertEqual(pos.quantity, math.floor(500_000 / V753.align_stock_price(10300 * 1.0005, upward=True)))
        self.assertEqual(pos.pattern, C.PATTERN)
        split = item._v7613_info(codes[0])
        self.assertTrue(split["close_bet"]); self.assertEqual(split["buy_day"], "2026-11-16")
        self.assertAlmostEqual(pos.stop_price, V753.align_stock_price(pos.entry_price * 0.9, upward=True))   # 손절 -10%
        saved = json.loads((self.folder / "cb.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["bought"], codes[:2])
        # 재시작해도 같은 날 다시 사지 않는다
        item._v7628_state = None
        before = dict(item.broker.positions)
        self.run_day(item)
        self.assertEqual(item.broker.positions, before)

    def test_max_ten_per_day(self):
        item, codes, _ = self.item(names=tuple(f"종목{k}" for k in range(14)) + ("음봉",))
        self.run_day(item, [(15, 0)] + [(15, m) for m in (1, 2, 3, 4, 5, 6, 7, 8)] + [(15, 15)] * 16)
        self.assertEqual(len(item.broker.positions), 10)
        self.assertEqual(sorted(item.broker.positions), codes[:10])          # 거래대금 큰 순

    def test_pattern_rechecked_at_buy_price(self):
        item, codes, _ = self.item(price=9900.0)                             # 매수 직전 음봉으로 변함
        self.run_day(item)
        self.assertEqual(item.broker.positions, {})
        self.assertTrue(any("패턴 깨짐" in str(t) for _k, t in item.logger.lines))

    def test_no_eod_close_on_buy_day_and_no_add(self):
        item, codes, _ = self.item(names=("가", "음봉"))
        self.run_day(item)
        q = SimpleNamespace(symbol=codes[0], price=10300.0)
        with mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_execute_fixed_stop_locked", return_value=True) as close:
            self.assertFalse(item._v7613_eod_close(codes[0], q, MONDAY.replace(hour=15, minute=19, second=5)))
            close.assert_not_called()
            self.assertFalse(item._v7613_try_add(codes[0], [], MONDAY.replace(hour=15, minute=18)))
            nxt = MONDAY + timedelta(days=1)
            self.assertTrue(item._v7613_eod_close(codes[0], q, nxt.replace(hour=15, minute=19, second=5)))
            self.assertIn("종가배팅 다음날 장마감정리", close.call_args.kwargs["reason"])

    def test_next_day_open_profit_sells_loss_waits(self):
        item, codes, _ = self.item(names=("가", "나", "음봉"))
        self.run_day(item)
        nxt = (MONDAY + timedelta(days=1)).replace(hour=9, minute=0, second=40)
        item._gem_exit_inflight = set()
        with mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_execute_fixed_stop_locked", return_value=True) as sell:
            self.assertFalse(item._v7628_open_sell(codes[0], SimpleNamespace(symbol=codes[0], price=10600.0), nxt.replace(second=10)))
            self.assertTrue(item._v7628_open_sell(codes[0], SimpleNamespace(symbol=codes[0], price=10600.0), nxt))   # 수익 → 매도
            self.assertEqual(sell.call_args.kwargs["reason"], "종가배팅 다음날 시가 수익매도")
            sell.reset_mock()
            self.assertFalse(item._v7628_open_sell(codes[1], SimpleNamespace(symbol=codes[1], price=10310.0), nxt))  # 비용 빼면 손실
            sell.assert_not_called()
            self.assertIn("+2% 대기", item._v7613_info(codes[1])["watch"])
            self.assertFalse(item._v7628_open_sell(codes[1], SimpleNamespace(symbol=codes[1], price=10900.0), nxt + timedelta(minutes=5)))
            sell.assert_not_called()                                         # 시가 확인은 하루 한 번(이후는 +2% 목표가가 처리)

    def test_weekend_and_conservative_skip(self):
        item, _codes, calls = self.item()
        item._v7628_step((MONDAY - timedelta(days=2)).replace(hour=15, minute=5))
        self.assertEqual(calls["rank"], 0)
        item._v7619_conservative = lambda now=None: (True, "x")
        self.run_day(item)
        self.assertEqual((calls["rank"], item.broker.positions), (0, {}))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
