from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "SOURCE" / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import bot_v76_00_three_stage_ctrlv_paper as engine


class SilentLogger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class MemoryEventLog:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


class PaperBrokerProbe:
    def __init__(self) -> None:
        self.positions = {}
        self.sequence = 0
        self.buy_calls = 0
        self.sell_calls = 0
        self.recorded = []

    def _record_fill(self, fill) -> None:
        self.recorded.append(fill)

    def buy(self, symbol, quantity, quote, reason, *args, **kwargs):
        self.buy_calls += 1
        time.sleep(0.03)
        self.sequence += 1
        now = datetime.now(engine.KST)
        self.positions[symbol] = SimpleNamespace(
            quantity=int(quantity), entry_time=now, entry_price=float(quote.price)
        )
        fill = SimpleNamespace(
            order_id=f"PAPER-{self.sequence:06d}",
            timestamp=now,
            symbol=symbol,
            side="BUY",
            quantity=int(quantity),
            price=float(quote.price),
            reason=reason,
        )
        self._record_fill(fill)
        return fill

    def sell(self, symbol, quote, reason, *args, **kwargs):
        self.sell_calls += 1
        time.sleep(0.03)
        position = self.positions[symbol]
        self.sequence += 1
        now = datetime.now(engine.KST)
        fill = SimpleNamespace(
            order_id=f"PAPER-{self.sequence:06d}",
            timestamp=now,
            symbol=symbol,
            side="SELL",
            quantity=int(position.quantity),
            price=float(quote.price),
            reason=reason,
        )
        del self.positions[symbol]
        self._record_fill(fill)
        return fill


def quote(price: float = 70000.0):
    return SimpleNamespace(
        price=price,
        best_ask=price,
        best_bid=price,
        timestamp=datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST),
    )


def signal(symbol: str = "005930"):
    return SimpleNamespace(
        symbol=symbol,
        timestamp=datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST),
        pattern=engine.PATTERN,
        reason="IDEM 동일 자동매수 신호",
        reference_price=70000.0,
        stop_loss_pct=engine.V7609_FIXED_STOP_LOSS_PCT,
    )


def probe(ledger_path: Path):
    item = object.__new__(engine.V7600ThreeStageEngine)
    item._v7609_stage_lock = threading.RLock()
    item._v7609_stage_manager = engine._V7600_STATE.StateManager()
    item._v7609_stage_log = MemoryEventLog()
    item._v7609_state_log_obj = lambda _now: item._v7609_stage_log
    item._v7609_order_thread = threading.local()
    item._v7609_order_ledger = engine._V7600_ORDER.OrderIntentLedger(ledger_path)
    item._v7609_seen_paper_fill_ids = set()
    item._v7609_latest_buy_fill = {}
    item._v7609_load_paper_fill_identity = lambda: None
    item.logger = SilentLogger()
    item._label = lambda symbol: str(symbol)
    item.client = SimpleNamespace(v7580_raw_cache={})
    item.broker = PaperBrokerProbe()
    item._v7609_install_paper_fill_state_bridge()
    item._v7609_install_order_idempotency_bridge()
    return item


class OrderIdempotencyIntegrationTests(unittest.TestCase):
    def test_idem_01_same_auto_buy_signal_concurrently_places_one_order(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            q = quote()
            item._v7609_attempt_entry_inner = (
                lambda sig, from_retry=False: bool(
                    item.broker.buy(sig.symbol, 1, q, sig.reason)
                )
            )
            results = []
            threads = [
                threading.Thread(target=lambda: results.append(item._attempt_entry(signal())))
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(item.broker.buy_calls, 1)
            self.assertEqual(len(item.broker.recorded), 1)
            self.assertEqual(sum(bool(result) for result in results), 1)

    def test_idem_02_same_auto_sell_signal_concurrently_places_one_order(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            item.broker.buy("005930", 1, quote(), "seed")
            results = []
            blocked = []

            def submit_sell():
                try:
                    results.append(
                        item.broker.sell("005930", quote(71000.0), "예약목표+1.5%")
                    )
                except ValueError as exc:
                    blocked.append(str(exc))

            threads = [
                threading.Thread(target=submit_sell)
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(item.broker.sell_calls, 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(blocked), 7)
            self.assertEqual(
                len([fill for fill in item.broker.recorded if fill.side == "SELL"]), 1
            )

    def test_idem_03_duplicate_fill_id_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            fill = item.broker.buy("005930", 1, quote(), "seed")
            stage_count = len(item._v7609_stage_manager.events)
            with self.assertRaisesRegex(ValueError, "중복 PAPER 체결"):
                item.broker._record_fill(fill)
            self.assertEqual(len(item.broker.recorded), 1)
            self.assertEqual(len(item._v7609_stage_manager.events), stage_count)

    def test_idem_04_restart_blocks_same_accepted_order_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "intents.jsonl"
            first = probe(path)
            q = quote()
            first._v7609_attempt_entry_inner = (
                lambda sig, from_retry=False: bool(
                    first.broker.buy(sig.symbol, 1, q, sig.reason)
                )
            )
            self.assertTrue(first._attempt_entry(signal()))

            restarted = probe(path)
            restarted._v7609_attempt_entry_inner = (
                lambda sig, from_retry=False: bool(
                    restarted.broker.buy(sig.symbol, 1, q, sig.reason)
                )
            )
            self.assertFalse(restarted._attempt_entry(signal(), from_retry=True))
            self.assertEqual(restarted.broker.buy_calls, 0)

    def test_idem_05_failure_before_acceptance_can_retry_but_accepted_cannot(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            attempts = {"count": 0}

            def enter(sig, from_retry=False):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise ValueError("호가 수신 전 실패")
                return bool(item.broker.buy(sig.symbol, 1, quote(), sig.reason))

            item._v7609_attempt_entry_inner = enter
            with self.assertRaisesRegex(ValueError, "호가 수신 전 실패"):
                item._attempt_entry(signal())
            self.assertTrue(item._attempt_entry(signal(), from_retry=True))
            self.assertFalse(item._attempt_entry(signal(), from_retry=True))
            self.assertEqual(item.broker.buy_calls, 1)

            item._v7552_processed_command_ids = set()
            manual_attempts = {"count": 0}

            def base_apply(target, command):
                command_id = str(command["command_id"])
                if command_id in target._v7552_processed_command_ids:
                    return "메모리 중복 차단"
                target._v7552_processed_command_ids.add(command_id)
                manual_attempts["count"] += 1
                if manual_attempts["count"] == 1:
                    raise ValueError("주문 접수 전 수동 실패")
                target.broker.buy("000660", 1, quote(), "형 승인 재시도")
                return "수동 재시도 성공"

            command = {
                "kind": "IMMEDIATE_BUY",
                "symbol": "000660",
                "command_id": "RETRY-APPROVAL",
            }
            with mock.patch.object(engine, "_V7609_BASE_COMMAND_APPLY", base_apply):
                with self.assertRaisesRegex(ValueError, "주문 접수 전 수동 실패"):
                    engine._v7609_apply_command(item, command)
                self.assertEqual(
                    engine._v7609_apply_command(item, command), "수동 재시도 성공"
                )
            self.assertEqual(manual_attempts["count"], 2)

    def test_idem_06_distinct_manual_approval_ids_are_not_collapsed(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")

            def base_apply(target, command):
                symbol = str(command["symbol"])
                target.broker.buy(symbol, 1, quote(), "형 승인 즉시매수")
                return "승인 주문 실행"

            commands = [
                {"kind": "IMMEDIATE_BUY", "symbol": "005930", "command_id": "APPROVAL-A"},
                {"kind": "IMMEDIATE_BUY", "symbol": "005930", "command_id": "APPROVAL-B"},
            ]
            with mock.patch.object(engine, "_V7609_BASE_COMMAND_APPLY", base_apply):
                for command in commands:
                    engine._v7609_apply_command(item, command)
            self.assertEqual(item.broker.buy_calls, 2)
            self.assertEqual([fill.symbol for fill in item.broker.recorded], ["005930", "005930"])

    def test_idem_07_buy_and_sell_identity_never_collide(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            buy = item._v7609_make_order_identity(
                side="BUY", symbol="005930", source="TEST", source_id="same"
            )
            sell = item._v7609_make_order_identity(
                side="SELL", symbol="005930", source="TEST", source_id="same"
            )
            self.assertNotEqual(buy.signal_id, sell.signal_id)
            self.assertNotEqual(buy.order_intent_id, sell.order_intent_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
