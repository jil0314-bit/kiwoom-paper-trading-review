"""V7613 분할매수 엔진 통합 시험 — 실제 엔진 메서드 + 대역 브로커/시세(주문·네트워크 없음)."""
from __future__ import annotations
import json, math, sys, tempfile, threading, types, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
CORE = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith("core") else "/home/claude/t13/ROOT/SOURCE/core")
sys.path.insert(0, str(CORE))
import bot_v76_00_three_stage_ctrlv_paper as engine
import v7613_split_rules as R

KST = engine.KST
V753 = engine._V753
SYMBOL = "123450"
DAY = datetime(2026, 11, 9, tzinfo=KST)


class SilentLogger:
    def __init__(self): self.lines = []
    def __getattr__(self, name):
        return lambda *a, **k: self.lines.append((name, a[0] % a[1:] if len(a) > 1 else (a[0] if a else "")))


class MemoryEventLog:
    def __init__(self): self.events = []
    def append(self, e): self.events.append(e)


class FakeBroker:
    def __init__(self, cash=1_000_000_000.0):
        self.positions = {}
        self.cash = cash
        self.sequence = 0
        self.settings = SimpleNamespace(commission_pct=0.00015, sell_tax_pct=0.0018, slippage_pct=0.0005)
        self.recorded = []
        self.reentry_block_until = {}
    def _record_fill(self, fill): self.recorded.append(fill)
    def save_state(self): pass
    def buy(self, *a, **k): raise AssertionError("1차 매수는 이 시험에서 직접 열기만 한다")
    def open(self, symbol, price, qty, when):
        fee = price * qty * self.settings.commission_pct
        self.cash -= price * qty + fee
        self.positions[symbol] = V753.Position(symbol, qty, price, when, price, price * 0.985, price * 1.02, fee, engine.PATTERN, True)
    def sell(self, symbol, quote, reason, **kw):
        if kw.get("reserved_fill_price") is not None and not reason.startswith("예약목표"):
            raise ValueError("예약목표가 아닌 지정가격 PAPER 체결 차단")   # 부모 PaperBroker와 같은 관문
        p = self.positions.pop(symbol)
        self.sequence += 1
        gross = quote.price * p.quantity
        fee = gross * self.settings.commission_pct; tax = gross * self.settings.sell_tax_pct
        pnl = gross - fee - tax - p.entry_cost
        fill = V753.Fill(f"PAPER-{self.sequence:06d}", datetime.now(KST), symbol, "SELL", p.quantity, quote.price, fee, tax, pnl, reason)
        self._record_fill(fill)
        return fill


def make_bar(t, o, h, l, c, v):
    return V753.Bar(SYMBOL, t, float(o), float(h), float(l), float(c), int(v))


def history(start, n=30, price=10000):
    return [make_bar(start + timedelta(minutes=k), price, price + 10, price - 10, price, 1000) for k in range(n)]


def add_sequence(t0, top):
    """top 아래에서 거래음봉→거래바닥→양봉→5선안착→재바닥→양봉."""
    seq = [
        (top - 50, top - 40, top - 220, top - 200, 6000),   # 거래 터진 음봉
        (top - 200, top - 195, top - 215, top - 210, 300),  # 거래바닥
        (top - 210, top - 185, top - 212, top - 190, 500),  # 양봉(반전)
        (top - 190, top - 140, top - 192, top - 150, 700),  # 5선 안착 양봉
        (top - 150, top - 145, top - 158, top - 152, 150),  # 재바닥
        (top - 152, top - 110, top - 154, top - 120, 600),  # 재바닥 뒤 양봉 → 매수
    ]
    return [make_bar(t0 + timedelta(minutes=k), *row) for k, row in enumerate(seq)]


def probe(folder: Path, cash=1_000_000_000.0):
    item = object.__new__(engine.V7600ThreeStageEngine)
    item._v7609_stage_lock = threading.RLock()
    item._v7609_stage_manager = engine._V7600_STATE.StateManager()
    item._v7609_stage_log = MemoryEventLog()
    item._v7609_state_log_obj = lambda _now: item._v7609_stage_log
    item._v7609_order_thread = threading.local()
    item._v7609_order_ledger = engine._V7600_ORDER.OrderIntentLedger(folder / "intents.jsonl")
    item._v7609_seen_paper_fill_ids = set()
    item._v7609_latest_buy_fill = {}
    item._v7609_load_paper_fill_identity = lambda: None
    item._v7609_record_paper_fill_stage = lambda fill: True
    item._v7609_reconcile_one_holding = lambda s, p: True
    item.logger = SilentLogger()
    item._label = lambda s: str(s)
    item._show_throttled_error = lambda key, text, sec: item.logger.lines.append(("throttled", text))
    item._v7609_resolve_display_name = lambda s, n="": "시험종목"
    item.client = SimpleNamespace(v7580_raw_cache={})
    item.broker = FakeBroker(cash)
    item.settings = item.broker.settings
    item.risk = SimpleNamespace(data_halted=False, halted=False, record_pnl=lambda p: None)
    item.latest_quotes = {}
    item._gem_exit_inflight = set()
    item._gem_order_announced = set()
    item._print_buy = lambda fill, pos, sig: None
    item.strategy = SimpleNamespace(mark_exit=lambda s: None)
    item.pending_entries = {}
    item._v7609_target_exit_at = {}
    item._v7609_install_paper_fill_state_bridge()
    item._v7609_install_order_idempotency_bridge()
    return item


def set_quote(item, price):
    q = SimpleNamespace(price=float(price), best_ask=float(price), best_bid=float(price),
                        timestamp=datetime.now(KST), symbol=SYMBOL, spread_pct=0.001)
    item.client.get_quote = lambda s: q
    item.client.get_orderbook = lambda s, fresh, side="buy": q
    return q


class V7613Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.patches = [mock.patch.object(engine, "V7613_STATE_FILE", self.folder / "split.json"),
                        mock.patch.object(engine, "V7613_VIEW_FILE", self.folder / "view.json")]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def opened(self, cash=1_000_000_000.0):
        item = probe(self.folder, cash)
        entry_time = DAY.replace(hour=10, minute=0, second=30)
        qty = math.floor(R.FIRST_NOTIONAL / 10000)
        item.broker.open(SYMBOL, 10000.0, qty, entry_time)
        item._v7613_register_first(SYMBOL, SimpleNamespace(pattern=engine.PATTERN, reason="시험 1차"))
        return item, entry_time

    def test_01_first_notional_all_split_patterns(self):
        for pattern in (engine.PATTERN, engine.BREAKOUT_PATTERN, engine.TAIL_PATTERN, engine.CARRY_PATTERN):
            self.assertEqual(engine._v76_initial_notional(SimpleNamespace(pattern=pattern, reason="")), 1_000_000.0)
        other = SimpleNamespace(pattern="기타", reason="")
        self.assertEqual(engine._v76_initial_notional(other), engine._ORIGINAL_INITIAL_NOTIONAL(other))

    def test_02_register_sets_stop_minus10_target_plus2(self):
        item, _ = self.opened()
        pos = item.broker.positions[SYMBOL]
        self.assertEqual(item._v7613_info(SYMBOL)["tranche"], 1)
        self.assertAlmostEqual(pos.stop_price, 9000.0)
        net = engine._EXIT_POLICY.net_return(pos, pos.target_price, 0.00015, 0.0018)
        self.assertGreaterEqual(net, 0.02 - 1e-9); self.assertLess(net, 0.0215)
        saved = json.loads((self.folder / "split.json").read_text(encoding="utf-8"))
        self.assertIn(SYMBOL, saved["positions"])

    def test_03_stop_reason_only_minus10(self):
        item, _ = self.opened()
        now = DAY.replace(hour=10, minute=5)
        self.assertEqual(item._v7609_support_stop_reason(SYMBOL, 9500.0, now), "")   # -5%: 기존 -3% 최대손절 미적용
        self.assertEqual(item._v7609_support_stop_reason(SYMBOL, 9010.0, now), "")
        self.assertIn("V7613", item._v7609_support_stop_reason(SYMBOL, 9000.0, now))

    def test_04_cash_gate(self):
        item = probe(self.folder, cash=900_000.0)
        self.assertFalse(item._v7613_cash_allows_new("000001"))   # 예수금 90만 < 1차 100만
        item2, _ = self.opened(cash=25_000_000.0)          # V7624: 3차 몫을 미리 잡지 않는다
        self.assertTrue(item2._v7613_cash_allows_new("000001"))
        item3 = probe(self.folder, cash=1_000_000_000.0)
        self.assertTrue(item3._v7613_cash_allows_new("000001"))

    def test_05_adds_2nd_and_3rd_then_stop(self):
        item, entry_time = self.opened()
        bars = history(DAY.replace(hour=9, minute=30)) + [make_bar(DAY.replace(hour=10, minute=0), 10000, 10010, 9990, 10000, 1000)]
        seq2 = add_sequence(DAY.replace(hour=10, minute=1), 10000)
        bars2 = bars + seq2
        # 마지막 봉 직전까지는 매수 없음
        for k in range(len(bars) + 1, len(bars2)):
            now = bars2[k - 1].timestamp + timedelta(minutes=1, seconds=5)
            set_quote(item, bars2[k - 1].close)
            self.assertFalse(item._v7613_try_add(SYMBOL, bars2[:k], now), k)
        now = seq2[-1].timestamp + timedelta(minutes=1, seconds=5)
        set_quote(item, 9880)
        self.assertTrue(item._v7613_try_add(SYMBOL, bars2, now))
        info = item._v7613_info(SYMBOL); pos = item.broker.positions[SYMBOL]
        self.assertEqual(info["tranche"], 2)
        added = info["fills"][-1]["notional"]
        self.assertTrue(R.NOTIONALS[1] * 0.99 <= added <= R.NOTIONALS[1], added)
        self.assertAlmostEqual(pos.stop_price, 9000.0)              # 손절은 최초가 -10% 유지
        net = engine._EXIT_POLICY.net_return(pos, pos.target_price, 0.00015, 0.0018)
        self.assertTrue(0.005 - 1e-9 <= net < 0.0065, net)           # 평단 +0.5%
        self.assertFalse(item._v7613_try_add(SYMBOL, bars2, now))    # 같은 봉 재주문 금지
        # 3차
        seq3 = add_sequence(seq2[-1].timestamp + timedelta(minutes=1), info["last_fill_price"])
        bars3 = bars2 + seq3
        now3 = seq3[-1].timestamp + timedelta(minutes=1, seconds=5)
        set_quote(item, seq3[-1].close)
        self.assertTrue(item._v7613_try_add(SYMBOL, bars3, now3))
        info = item._v7613_info(SYMBOL)
        self.assertEqual(info["tranche"], 3)
        self.assertTrue(R.NOTIONALS[2] * 0.99 <= info["fills"][-1]["notional"] <= R.NOTIONALS[2])
        total = sum(f["notional"] for f in info["fills"])
        self.assertLessEqual(total, R.PLAN_TOTAL)
        # 4차는 없음
        seq4 = add_sequence(seq3[-1].timestamp + timedelta(minutes=1), info["last_fill_price"])
        now4 = seq4[-1].timestamp + timedelta(minutes=1, seconds=5)
        self.assertFalse(item._v7613_try_add(SYMBOL, bars3 + seq4, now4))
        # 재시작 복원
        fresh = probe(self.folder); fresh.broker.positions = item.broker.positions
        self.assertEqual(fresh._v7613_info(SYMBOL)["tranche"], 3)
        # 매도 → 결과 기록
        q = set_quote(item, pos.target_price)
        fill = item.broker.sell(SYMBOL, q, "시험매도")
        item._v7613_on_sell(fill)
        self.assertIsNone(item._v7613_info(SYMBOL))
        saved = json.loads((self.folder / "split.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["closed"][-1]["tranche"], 3)

    def test_05b_target_sell_after_add_is_accepted(self):
        item, _ = self.opened()
        bars = history(DAY.replace(hour=9, minute=30)) + [make_bar(DAY.replace(hour=10, minute=0), 10000, 10010, 9990, 10000, 1000)]
        seq2 = add_sequence(DAY.replace(hour=10, minute=1), 10000)
        set_quote(item, 9880)
        self.assertTrue(item._v7613_try_add(SYMBOL, bars + seq2, seq2[-1].timestamp + timedelta(minutes=1, seconds=5)))
        pos = item.broker.positions[SYMBOL]
        q = set_quote(item, pos.target_price)
        fill = item.broker.sell(SYMBOL, q, "예약목표+1.5%", reserved_fill_price=pos.target_price)
        self.assertTrue(str(fill.reason).startswith("예약목표 V7613 2차후"))
        self.assertNotIn(SYMBOL, item.broker.positions)

    def test_06_add_blocked_when_not_fresh_or_after_1519(self):
        item, _ = self.opened()
        bars = history(DAY.replace(hour=9, minute=30)) + add_sequence(DAY.replace(hour=10, minute=1), 10000)
        stale_now = bars[-1].timestamp + timedelta(minutes=2, seconds=5)
        set_quote(item, 9880)
        self.assertFalse(item._v7613_try_add(SYMBOL, bars, stale_now))
        late = [make_bar(b.timestamp.replace(hour=15, minute=b.timestamp.minute), b.open, b.high, b.low, b.close, b.volume) for b in bars[-6:]]
        late_now = DAY.replace(hour=15, minute=19, second=5)
        self.assertFalse(item._v7613_try_add(SYMBOL, bars[:-6] + late, late_now))

    def test_07_after_13_add_allowed(self):
        item, _ = self.opened()
        base = history(DAY.replace(hour=13, minute=0))
        seq = add_sequence(DAY.replace(hour=13, minute=30), 10000)
        now = seq[-1].timestamp + timedelta(minutes=1, seconds=5)
        set_quote(item, 9880)
        self.assertTrue(item._v7613_try_add(SYMBOL, base + seq, now))

    def test_08_eod_close(self):
        item, _ = self.opened()
        q = set_quote(item, 10050)
        with mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_execute_fixed_stop_locked", return_value=True) as close:
            # V7630: 모의투자 기본은 장마감 정리 없음(다음날로 이월)
            self.assertFalse(engine.V7630_EOD_CLOSE)
            self.assertFalse(item._v7613_eod_close(SYMBOL, q, DAY.replace(hour=15, minute=19, second=1)))
            close.assert_not_called()
            with mock.patch.object(engine, "V7630_EOD_CLOSE", True):     # 스위치를 켜면 예전 정리 동작 그대로
                self.assertFalse(item._v7613_eod_close(SYMBOL, q, DAY.replace(hour=15, minute=18, second=59)))
                self.assertTrue(item._v7613_eod_close(SYMBOL, q, DAY.replace(hour=15, minute=19, second=1)))
                self.assertEqual(close.call_args.kwargs["reason"], "V7613 장마감정리 15:19")

    def test_09_legacy_position_untouched(self):
        item = probe(self.folder)
        item.broker.open("999990", 10000.0, 500, DAY.replace(hour=10))
        self.assertIsNone(item._v7613_info("999990"))
        stop, target = item._v7609_position_exit_prices(item.broker.positions["999990"])
        self.assertTrue(9860 <= stop <= 9880, stop)    # 기존 비용포함 -1.5%
        self.assertFalse(item._v7613_eod_close("999990", set_quote(item, 1), DAY.replace(hour=15, minute=20)))

    def test_10_gui_rows_and_view(self):
        item, _ = self.opened()
        rows = [{"symbol": SYMBOL, "stage": "매수종목", "reason": "기존이유"}, {"symbol": "000002", "stage": "관찰후보", "reason": "x"}]
        item._v7613_decorate_rows(rows, DAY.replace(hour=10, minute=3))
        self.assertTrue(rows[0]["reason"].startswith("[1차매수"))
        self.assertEqual(rows[1]["reason"], "x")
        view = json.loads((self.folder / "view.json").read_text(encoding="utf-8"))
        self.assertEqual(view["holdings"][0]["tranche"], 1)

    def test_11_poll_path_buy_signal_reaches_order(self):
        item = probe(self.folder)
        st = engine._V7600_STATE
        now = datetime.now(KST).replace(hour=10, minute=5, second=10, microsecond=0)
        data_time = (now.replace(second=0) - timedelta(minutes=1)).isoformat()
        proof = {"route": "FLOOR", "buy": True, "episode": data_time, "data_time": data_time,
                 "reason": "시험바닥매수", "bars": [{"timestamp": data_time, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
        item._v7609_prebuy_daily_cache = {}
        item._v7587_excluded = set()
        item._supply_potential = {}; item._v7588_phase = {}; item._v76_strict_interest = {}
        item._v76_strict_prebuy = {}; item._v76_order_bar = {}; item.latest_signals = {}
        item._v7588_make_potential = lambda *a: None; item._save_supply_potential = lambda: None
        item._market_gate_v76 = lambda **k: (True, "")
        item._v7609_record_stage_wait = lambda *a, **k: None
        q = set_quote(item, 10000)
        called = []
        item._attempt_entry = lambda sig, **k: called.append(sig) or True
        with mock.patch.object(engine._PREBUY_RULES, "needs_daily", return_value=False), \
             mock.patch.object(engine._PREBUY_RULES, "evaluate", return_value=dict(proof)), \
             mock.patch.object(engine._PREBUY_RULES, "validate_evidence", return_value=True), \
             mock.patch.object(engine._PREBUY_RULES, "in_session", return_value=True), \
             mock.patch.object(engine._ENTRY_POLICY, "blocked_window", return_value=""), \
             mock.patch.object(engine.V7600ThreeStageEngine, "_v7609_current_prebuy_approved", return_value=True):
            item._v7609_requested_prebuy(SYMBOL, [], now, None)   # 후보감시 경로: quote 없음
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0].reference_price, 10000.0)


class TargetReasonTests(unittest.TestCase):
    def test_add_target_reason_passes_parent_reserved_guard(self):
        src = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertIn('reason = f"예약목표 V7613 {int(split', src)
        self.assertTrue("예약목표 V7613 2차후 평단+0.5%".startswith("예약목표"))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
