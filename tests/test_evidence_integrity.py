from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "SOURCE" / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import bot_v76_00_three_stage_ctrlv_paper as engine


class SilentLogger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class PaperBrokerProbe:
    def __init__(self) -> None:
        self.positions = {}
        self.sequence = 0
        self.recorded = []

    def _record_fill(self, fill) -> None:
        self.recorded.append(fill)

    def buy(self, symbol, quantity, quote, reason, *args, **kwargs):
        del args, kwargs
        self.sequence += 1
        stamp = quote.timestamp
        self.positions[str(symbol).zfill(6)] = SimpleNamespace(
            quantity=int(quantity), entry_time=stamp, entry_price=float(quote.price)
        )
        fill = SimpleNamespace(
            order_id=f"EVID-PAPER-{self.sequence:04d}", timestamp=stamp,
            symbol=str(symbol).zfill(6), side="BUY", quantity=int(quantity),
            price=float(quote.price), reason=str(reason),
        )
        self._record_fill(fill)
        return fill

    def sell(self, symbol, quote, reason, *args, **kwargs):
        del args, kwargs
        symbol = str(symbol).zfill(6)
        position = self.positions[symbol]
        self.sequence += 1
        fill = SimpleNamespace(
            order_id=f"EVID-PAPER-{self.sequence:04d}", timestamp=quote.timestamp,
            symbol=symbol, side="SELL", quantity=int(position.quantity),
            price=float(quote.price), reason=str(reason),
        )
        del self.positions[symbol]
        self._record_fill(fill)
        return fill


def bar(stamp: datetime, price: float, volume: float, *, source_minutes: int = 1):
    return SimpleNamespace(
        timestamp=stamp, open=price - 10.0, high=price + 20.0,
        low=price - 30.0, close=price, volume=volume,
        source_minutes=source_minutes,
    )


def quote(stamp: datetime, price: float):
    return SimpleNamespace(
        timestamp=stamp, price=price, best_ask=price, best_bid=price
    )


def fingerprint_runtime() -> dict[str, str]:
    runtime = ROOT / "SOURCE" / "runtime_v7592"
    result = {}
    if not runtime.exists():
        return result
    for path in sorted(runtime.rglob("*.jsonl")):
        result[str(path.relative_to(runtime))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def make_probe(folder: Path):
    item = object.__new__(engine.V7600ThreeStageEngine)
    item._v7609_stage_lock = threading.RLock()
    item._v7609_stage_manager = engine._V7600_STATE.StateManager()
    item._v7609_stage_log = engine._V7600_STATE.JsonlEventLog(folder / "stages.jsonl")
    item._v7609_state_log_obj = lambda _now: item._v7609_stage_log
    item._v7609_order_thread = threading.local()
    item._v7609_order_ledger = engine._V7600_ORDER.OrderIntentLedger(
        folder / "order_intents.jsonl"
    )
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


def assert_required_stage(test: unittest.TestCase, event) -> None:
    test.assertTrue(event.signal_id)
    test.assertEqual(len(event.symbol), 6)
    test.assertTrue(hasattr(event, "old_stage"))
    test.assertTrue(event.new_stage)
    test.assertTrue(event.rule_id)
    test.assertTrue(event.reason)
    test.assertTrue(event.data_time)
    test.assertTrue(event.decision_time)
    test.assertEqual(event.engine_version, engine.VERSION)
    test.assertIn("signal_id", event.evidence)
    test.assertIn("order_intent_id", event.evidence)


class EvidenceIntegrityIntegrationTests(unittest.TestCase):
    runtime_before: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime_before = fingerprint_runtime()

    def test_evid_01_scanner_stage_has_exact_raw_rest_one_minute_evidence(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            item = make_probe(Path(raw_folder))
            stamp = datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST)
            prior = stamp - timedelta(minutes=1)
            item.client.v7580_raw_cache["005930"] = [
                bar(prior, 70000.0, 12000.0),
                bar(stamp, 70100.0, 12345.0),
            ]
            self.assertTrue(item._v7609_record_authoritative_stage(
                "005930", "관찰후보", "EVID 관찰 규칙", stamp
            ))
            event = item._v7609_stage_manager.events[-1]
            assert_required_stage(self, event)
            self.assertIsNone(event.old_stage)
            self.assertEqual(event.new_stage, engine._V7600_STATE.OBSERVED)
            raw = event.evidence
            self.assertEqual(raw["source"], "KIWOOM_REST_KA10080_RAW_1M")
            self.assertEqual(raw["data_time"], stamp.isoformat())
            self.assertEqual(raw["ohlcv"], {
                "timestamp": stamp.isoformat(), "open": 70090.0,
                "high": 70120.0, "low": 70070.0, "close": 70100.0,
                "volume": 12345.0,
            })
            self.assertEqual(len(raw["one_minute_bars"]), 2)
            self.assertEqual(raw["one_minute_bars"][0]["timestamp"], prior.isoformat())
            self.assertEqual(len(raw["one_minute_bars_sha256"]), 64)
            self.assertEqual(len(item._v7609_stage_log.read()), 1)

    def test_evid_02_screen_legacy_three_minute_or_missing_raw_cannot_create_stage(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            item = make_probe(Path(raw_folder))
            stamp = datetime(2026, 8, 14, 9, 2, tzinfo=engine.KST)
            item._v7591_timeline = {"005930": {"stage": "관심"}}
            item._supply_potential = {"005930": SimpleNamespace(stage="관심종목")}
            item.client.v7580_raw_cache["005930"] = [
                bar(stamp, 70200.0, 99999.0, source_minutes=3)
            ]
            self.assertFalse(item._v7609_record_authoritative_stage(
                "005930", "관찰후보", "화면·과거·3분봉만 존재", stamp
            ))
            self.assertEqual(item._v7609_stage_manager.events, ())
            self.assertEqual(item._v7609_stage_log.read(), [])
            forming_now = stamp + timedelta(seconds=10)
            completed = bar(stamp - timedelta(minutes=1), 70100.0, 1000.0)
            forming = bar(stamp, 70200.0, 2000.0)
            completed_rows = item._v7609_completed_one_minute_bars(
                [completed, forming], forming_now
            )
            self.assertEqual(completed_rows, [completed])
            self.assertTrue(item._v7609_latest_completed_bar_is_fresh(
                completed_rows, forming_now
            )[0])
            self.assertFalse(item._v7609_latest_completed_bar_is_fresh(
                [bar(stamp - timedelta(minutes=2), 70000.0, 900.0)], forming_now
            )[0])

    def test_evid_03_paper_buy_links_order_intent_fill_and_holding_event(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            item = make_probe(Path(raw_folder))
            stamp = datetime(2026, 8, 14, 9, 3, tzinfo=engine.KST)
            item.client.v7580_raw_cache["005930"] = [bar(stamp, 70300.0, 20000.0)]
            fill = item.broker.buy("005930", 3, quote(stamp, 70310.0), "EVID 매수 규칙")
            accepted = [
                row for row in item._v7609_order_ledger.records()
                if row["status"] == "ACCEPTED"
            ]
            self.assertEqual(len(accepted), 1)
            order = accepted[0]
            event = item._v7609_stage_manager.events[-1]
            assert_required_stage(self, event)
            self.assertEqual(event.new_stage, engine._V7600_STATE.HOLDING)
            self.assertEqual(event.paper_fill_id, fill.order_id)
            self.assertEqual(event.quantity, 3)
            self.assertEqual(event.evidence["order_intent_id"], order["order_intent_id"])
            self.assertEqual(event.evidence["order_signal_id"], order["signal_id"])
            self.assertEqual(order["old_stage"], "")
            self.assertEqual(order["new_stage"], engine._V7600_STATE.HOLDING)
            self.assertEqual(order["fill"]["order_id"], fill.order_id)
            self.assertEqual(order["fill"]["price"], 70310.0)
            self.assertEqual(order["fill"]["quantity"], 3)
            self.assertEqual(order["evidence"]["rest_1m"]["ohlcv"]["volume"], 20000.0)

    def test_evid_04_paper_sell_links_order_intent_fill_and_sold_event(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            item = make_probe(Path(raw_folder))
            buy_time = datetime(2026, 8, 14, 9, 4, tzinfo=engine.KST)
            sell_time = buy_time + timedelta(minutes=1)
            item.client.v7580_raw_cache["005930"] = [
                bar(buy_time, 70400.0, 21000.0), bar(sell_time, 71000.0, 22000.0)
            ]
            item.broker.buy("005930", 2, quote(buy_time, 70410.0), "EVID 매수")
            fill = item.broker.sell("005930", quote(sell_time, 71010.0), "EVID 매도")
            accepted = [
                row for row in item._v7609_order_ledger.records()
                if row["status"] == "ACCEPTED" and row["side"] == "SELL"
            ]
            self.assertEqual(len(accepted), 1)
            order = accepted[0]
            event = item._v7609_stage_manager.events[-1]
            assert_required_stage(self, event)
            self.assertEqual(event.old_stage, engine._V7600_STATE.HOLDING)
            self.assertEqual(event.new_stage, engine._V7600_STATE.SOLD)
            self.assertEqual(event.paper_fill_id, fill.order_id)
            self.assertEqual(event.evidence["order_intent_id"], order["order_intent_id"])
            self.assertEqual(order["old_stage"], engine._V7600_STATE.HOLDING)
            self.assertEqual(order["new_stage"], engine._V7600_STATE.SOLD)
            self.assertEqual(order["fill"]["price"], 71010.0)
            self.assertEqual(order["fill"]["quantity"], 2)

    def test_evid_05_duplicate_signal_and_fill_leave_one_evidence_event(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            item = make_probe(Path(raw_folder))
            stamp = datetime(2026, 8, 14, 9, 6, tzinfo=engine.KST)
            item.client.v7580_raw_cache["005930"] = [bar(stamp, 70500.0, 23000.0)]
            self.assertTrue(item._v7609_record_authoritative_stage(
                "005930", "관찰후보", "EVID 중복신호", stamp
            ))
            self.assertTrue(item._v7609_record_authoritative_stage(
                "005930", "관찰후보", "EVID 중복신호", stamp
            ))
            self.assertEqual(len(item._v7609_stage_manager.events), 1)
            expired = SimpleNamespace(updated_at=stamp)
            self.assertTrue(item._v7609_record_scanner_removal(
                "005930", expired, "SCANNER_ROW_EXPIRED", stamp + timedelta(minutes=1)
            ))
            self.assertEqual(
                item._v7609_stage_manager.stage_of("005930"),
                engine._V7600_STATE.REMOVED,
            )
            self.assertIsNone(item._v7609_today_observation_timestamp(
                "005930", stamp + timedelta(minutes=1)
            ))
            next_stamp = stamp + timedelta(minutes=2)
            item.client.v7580_raw_cache["005930"] = [bar(next_stamp, 70510.0, 24000.0)]
            self.assertTrue(item._v7609_record_authoritative_stage(
                "005930", "관찰후보", "EVID 재관찰", next_stamp
            ))
            fill = item.broker.buy("005930", 1, quote(stamp, 70510.0), "EVID 중복체결")
            events_after_fill = len(item._v7609_stage_manager.events)
            records_after_fill = len(item._v7609_order_ledger.records())
            with self.assertRaisesRegex(ValueError, "중복 PAPER 체결"):
                item.broker._record_fill(fill)
            self.assertEqual(len(item._v7609_stage_manager.events), events_after_fill)
            self.assertEqual(len(item._v7609_order_ledger.records()), records_after_fill)

    def test_evid_06_restart_preserves_chain_and_blocks_same_accepted_intent(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            folder = Path(raw_folder)
            first = make_probe(folder)
            stamp = datetime(2026, 8, 14, 9, 7, tzinfo=engine.KST)
            first.client.v7580_raw_cache["005930"] = [bar(stamp, 70600.0, 24000.0)]
            first.broker.buy("005930", 1, quote(stamp, 70610.0), "EVID 재시작")
            stage_rows = first._v7609_stage_log.read()
            order_count = len(first._v7609_order_ledger.records())

            restarted = make_probe(folder)
            restarted._v7609_stage_manager.restore(
                stage_rows, session_date="2026-08-14", engine_version=engine.VERSION
            )
            restarted.client.v7580_raw_cache["005930"] = [bar(stamp, 70600.0, 24000.0)]
            with self.assertRaisesRegex(ValueError, "중복 차단"):
                restarted.broker.buy("005930", 1, quote(stamp, 70610.0), "EVID 재시작")
            self.assertEqual(len(restarted._v7609_stage_manager.events), 1)
            self.assertEqual(len(restarted._v7609_stage_log.read()), 1)
            self.assertEqual(len(restarted._v7609_order_ledger.records()), order_count)

    def test_evid_07_evidence_storage_has_no_stage_authority_or_runtime_side_effect(self):
        with tempfile.TemporaryDirectory() as raw_folder:
            folder = Path(raw_folder)
            item = make_probe(folder)
            identity = item._v7609_make_order_identity(
                side="BUY", symbol="005930", source="EVID_ONLY", source_id="NO_STAGE"
            )
            payload = {"quantity": 1, "price": 70000.0, "reason": "증거만 저장"}
            item._v7609_order_ledger.claim(
                identity, payload,
                evidence=item._v7609_order_evidence(identity, payload),
            )
            self.assertIsNone(item._v7609_stage_manager.stage_of("005930"))
            self.assertEqual(item._v7609_stage_manager.events, ())
            self.assertEqual(fingerprint_runtime(), self.runtime_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
