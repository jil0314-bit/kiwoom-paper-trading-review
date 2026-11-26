"""V7620 시험: 형 지시(목표 %·이월, 즉시 손절, 해제, 장 약함, 테마 관심) + 부정문 즉시주문 차단. 주문·네트워크 없음."""
from __future__ import annotations
import json, math, sys, tempfile, types, unittest, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v7613_engine as base
engine, R, KST, V753, SYMBOL, DAY = base.engine, base.R, base.KST, base.V753, base.SYMBOL, base.DAY
import v7620_command_rules as C

KNOWN = {"삼성전자": "005930", "에코프로": "086520", "포스코퓨처엠": "003670", "씨어스": "458870"}


class InterpretTests(unittest.TestCase):
    def ask(self, text, held=("005930",)):
        return C.interpret(text, selected_symbol="458870", selected_name="씨어스", known_names=KNOWN, held=set(held))

    def test_hold_pct(self):
        r = self.ask("삼성전자 로직대로 팔지 말고 5% 수익나면 팔아")
        self.assertEqual((r.kind, r.symbol, r.pct, r.errors), ("V7620_HOLD_PCT", "005930", 0.05, []))

    def test_hold_pct_requires_holding_and_range(self):
        self.assertEqual(self.ask("에코프로 5% 수익나면 팔아").kind, "ERROR")
        self.assertEqual(self.ask("삼성전자 50% 오르면 팔아").kind, "ERROR")

    def test_stop_now_and_conditional_stop(self):
        self.assertEqual(self.ask("삼성전자 지금 손절해").kind, "IMMEDIATE_SELL")
        self.assertEqual(self.ask("삼성전자 3% 빠지면 손절").kind, "ERROR")
        self.assertIsNone(self.ask("삼성전자 손절하지 마"))

    def test_market_and_theme(self):
        self.assertEqual(self.ask("오늘 장 약하다").kind, "V7620_MARKET_WEAK")
        self.assertEqual(self.ask("장 회복됐다").kind, "V7620_MARKET_OK")
        r = self.ask("오늘 2차전지 테마 강하다 에코프로 포스코퓨처엠")
        self.assertEqual(r.kind, "V7620_THEME_WATCH")
        self.assertEqual(r.watch, [("086520", "에코프로"), ("003670", "포스코퓨처엠")])
        self.assertIsNone(self.ask("2차전지 테마 강하다"))        # 종목명 없으면 기존 해석기(의견 저장)

    def test_release_and_passthrough(self):
        self.assertEqual(self.ask("삼성전자 원래 로직대로 해").kind, "V7620_RELEASE")
        self.assertEqual(self.ask("삼성전자 매도해").kind, "IMMEDIATE_SELL")      # V7626 단순화
        self.assertIsNone(self.ask("삼성전자 10000원에 매수"))

    def test_v7626_plain_buy_sell_words(self):
        for text in ("삼성전자 오늘 분위기 안좋으니 매도", "삼성전자 그냥 팔자", "삼성전자 정리"):
            self.assertEqual(self.ask(text).kind, "IMMEDIATE_SELL", text)
        self.assertEqual(self.ask("에코프로 매도").kind, "ERROR")                  # 보유 아님
        for text in ("에코프로 좋아 보이니 매수", "에코프로 매수해줘 지금 바로"):
            self.assertEqual(self.ask(text).kind, "IMMEDIATE_BUY", text)
        for text in ("삼성전자 매도하지 마", "매수 중지", "에코프로 매수 금지", "에코프로 왜 매수 안돼?",
                     "에코프로 오르면 매수", "삼성전자 72000원에 매도"):
            self.assertIsNone(self.ask(text), text)                                 # 즉시 주문 아님(기존 해석으로)

    def test_v7624_exclude_buy_partial(self):
        self.assertEqual(self.ask("에코프로 빼").kind, "EXCLUDE")
        self.assertEqual(self.ask("에코프로 관심에서 빼").kind, "EXCLUDE")
        self.assertEqual(self.ask("에코프로 매수 제외").kind, "EXCLUDE")
        self.assertEqual(self.ask("삼성전자 빼").kind, "ERROR")            # 보유 중이면 제외 대신 안내
        self.assertEqual(self.ask("에코프로 지금 사").kind, "IMMEDIATE_BUY")
        self.assertEqual(self.ask("에코프로 사줘").kind, "IMMEDIATE_BUY")
        self.assertIsNone(self.ask("에코프로 70000원에 사줘"))             # 가격 있으면 가격조건 매수(기존)
        self.assertIsNone(self.ask("에코프로 로직 맞으면 사"))
        for text in ("삼성전자 50% 매도", "삼성전자 절반 팔아", "삼성전자 100주 매도"):
            self.assertEqual(self.ask(text).kind, "ERROR", text)           # 전량 매도로 잘못 나가지 않음
        self.assertIsNone(self.ask("에코프로 제외하지 마"))

    def test_negative_sentence_never_immediate_order(self):
        draft = engine._v7609_compile_command("삼성전자 로직대로 팔지 말고 기다려", selected_symbol="005930",
                                              selected_name="삼성전자", known_names=KNOWN)
        self.assertEqual(draft.action, "MEMO")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.patches = [mock.patch.object(engine, "V7613_STATE_FILE", self.folder / "split.json"),
                        mock.patch.object(engine, "V7613_VIEW_FILE", self.folder / "view.json"),
                        mock.patch.object(engine, "V7620_OVERRIDE_FILE", self.folder / "override.json")]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def opened(self):
        item = base.probe(self.folder)
        item._v7609_cost_rates = lambda: (0.00015, 0.0018)
        entry_time = DAY.replace(hour=10, minute=0, second=30)
        qty = math.floor(R.FIRST_NOTIONAL / 10000)
        item.broker.open(SYMBOL, 10000.0, qty, entry_time)
        item._v7613_register_first(SYMBOL, SimpleNamespace(pattern=engine.PATTERN, reason="시험 1차"))
        item._enforce_gem_orders(SYMBOL, announce=False)
        return item

    def test_hold_pct_changes_target_skips_eod_and_relabels_sell(self):
        item = self.opened()
        pos = item.broker.positions[SYMBOL]
        before = pos.target_price
        message = item._v7620_apply({"kind": "V7620_HOLD_PCT", "symbol": SYMBOL, "name": "시험", "pct": 0.05})
        self.assertIn("+5.0%", message)
        net = engine._EXIT_POLICY.net_return(pos, pos.target_price, 0.00015, 0.0018)
        self.assertGreater(pos.target_price, before)
        self.assertTrue(0.05 - 1e-9 <= net < 0.052, net)
        self.assertAlmostEqual(pos.stop_price, 9000.0)                       # 손절 -10% 유지
        saved = json.loads((self.folder / "override.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["positions"][SYMBOL]["skip_eod"])
        with mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_execute_fixed_stop_locked", return_value=True) as close:
            self.assertFalse(item._v7613_eod_close(SYMBOL, base.set_quote(item, 10050), DAY.replace(hour=15, minute=19, second=5)))
            close.assert_not_called()
        # 재시작해도 유지
        fresh = base.probe(self.folder)
        fresh.broker.positions = item.broker.positions
        self.assertIsNotNone(fresh._v7620_position_override(SYMBOL))
        # 목표 체결 사유는 '예약목표'로 시작해야 부모 PAPER가 받아준다
        q = base.set_quote(item, pos.target_price)
        fill = item.broker.sell(SYMBOL, q, "예약목표+1.5%", reserved_fill_price=pos.target_price)
        self.assertTrue(str(fill.reason).startswith("예약목표 형지시 평단+5.0%"), fill.reason)
        item._v7613_on_sell(fill)
        self.assertIsNone(item._v7620_position_override(SYMBOL))

    def test_release_restores_logic(self):
        item = self.opened()
        pos = item.broker.positions[SYMBOL]
        original = pos.target_price
        item._v7620_apply({"kind": "V7620_HOLD_PCT", "symbol": SYMBOL, "pct": 0.05})
        item._v7620_apply({"kind": "V7620_RELEASE", "symbol": SYMBOL})
        self.assertAlmostEqual(pos.target_price, original)
        with mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_execute_fixed_stop_locked", return_value=True), \
                mock.patch.object(engine, "V7630_EOD_CLOSE", True):
            self.assertTrue(item._v7613_eod_close(SYMBOL, base.set_quote(item, 10050), DAY.replace(hour=15, minute=19, second=5)))

    def test_hold_pct_rejects_not_held(self):
        item = base.probe(self.folder)
        with self.assertRaises(ValueError):
            item._v7620_apply({"kind": "V7620_HOLD_PCT", "symbol": "005930", "pct": 0.05})

    def test_market_weak_turns_on_conservative_today_only(self):
        item = base.probe(self.folder)
        now = datetime.now(KST)
        item._v7620_apply({"kind": "V7620_MARKET_WEAK", "note": "오늘 장 약하다"})
        active, reason = item._v7619_conservative(now.replace(hour=10, minute=0))
        self.assertTrue(active, reason)
        self.assertIn("형 지시", reason)
        self.assertFalse(item._v7619_conservative((now + timedelta(days=1)).replace(hour=10))[0] and
                         "형 지시" in item._v7619_conservative((now + timedelta(days=1)).replace(hour=10))[1])
        item._v7620_apply({"kind": "V7620_MARKET_OK"})
        item.client = SimpleNamespace(v7580_raw_cache={}, get_minute_bars=lambda *a, **k: [])
        item.v757_config = SimpleNamespace(market_proxy_codes=("069500",))
        self.assertFalse(item._v7619_conservative(now.replace(hour=10, minute=1))[0])

    def test_theme_watch_uses_existing_watch_path(self):
        item = base.probe(self.folder)
        calls = []
        item._manual_add_watch = lambda symbol, name: calls.append((symbol, name))
        item._v7620_apply({"kind": "V7620_THEME_WATCH", "symbol": "086520", "name": "에코프로"})
        self.assertEqual(calls, [("086520", "에코프로")])

    def test_apply_command_routes_v7620(self):
        item = base.probe(self.folder)
        message = engine._v7609_apply_command(item, {"kind": "V7620_MARKET_WEAK", "note": "x"})
        self.assertIn("초보수", message)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
