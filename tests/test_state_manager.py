from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).parents[1] / "SOURCE" / "core" / "v7600_state_manager.py"
SPEC = importlib.util.spec_from_file_location("v7600_state_manager", MODULE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = state
SPEC.loader.exec_module(state)


def request(stage: str, signal: str, *, quantity: int = 0, fill: str = ""):
    return state.TransitionRequest(
        signal_id=signal,
        symbol="005930",
        new_stage=stage,
        rule_id="TEST-RULE",
        data_time="2026-08-14T09:01:00+09:00",
        decision_time="2026-08-14T09:01:01+09:00",
        engine_version="TEST",
        reason="test",
        evidence={"ohlcv": [1, 2, 1, 2, 100]},
        quantity=quantity,
        paper_fill_id=fill,
    )


class StateManagerTests(unittest.TestCase):
    def test_happy_path_and_duplicate_signal_block(self):
        manager = state.StateManager()
        manager.apply(request(state.OBSERVED, "a"))
        manager.apply(request(state.INTEREST, "b"))
        manager.apply(request(state.PREBUY, "c"))
        manager.apply_paper_fill(
            request(state.HOLDING, "d", quantity=1, fill="PAPER-BUY-1")
        )
        manager.apply_paper_fill(
            request(state.SOLD, "e", quantity=1, fill="PAPER-SELL-1")
        )
        self.assertEqual(manager.stage_of("005930"), state.SOLD)
        with self.assertRaises(state.TransitionRejected):
            manager.apply_paper_fill(
                request(state.SOLD, "e", quantity=1, fill="PAPER-SELL-1")
            )

    def test_unheld_sell_is_rejected(self):
        manager = state.StateManager()
        with self.assertRaises(state.TransitionRejected):
            manager.apply(request(state.SOLD, "sell", quantity=1, fill="PAPER-SELL-1"))

    def test_confirmed_paper_buy_and_sell_are_authoritative(self):
        manager = state.StateManager()
        manager.apply_paper_fill(
            request(state.HOLDING, "buy", quantity=3, fill="PAPER-000001")
        )
        self.assertEqual(manager.stage_of("005930"), state.HOLDING)
        manager.apply_paper_fill(
            request(state.SOLD, "sell", quantity=3, fill="PAPER-000002")
        )
        self.assertEqual(manager.stage_of("005930"), state.SOLD)

    def test_duplicate_paper_fill_is_rejected(self):
        manager = state.StateManager()
        manager.apply_paper_fill(
            request(state.HOLDING, "buy", quantity=1, fill="PAPER-000001")
        )
        with self.assertRaises(state.TransitionRejected):
            manager.apply_paper_fill(
                request(state.HOLDING, "buy2", quantity=1, fill="PAPER-000001")
            )

    def test_scale_in_and_reentry_use_distinct_confirmed_fills(self):
        manager = state.StateManager()
        manager.apply_paper_fill(
            request(state.HOLDING, "buy1", quantity=1, fill="PAPER-000001")
        )
        manager.apply_paper_fill(
            request(state.HOLDING, "buy2", quantity=2, fill="PAPER-000002")
        )
        manager.apply_paper_fill(
            request(state.SOLD, "sell1", quantity=3, fill="PAPER-000003")
        )
        manager.apply_paper_fill(
            request(state.HOLDING, "buy3", quantity=1, fill="PAPER-000004")
        )
        self.assertEqual(manager.stage_of("005930"), state.HOLDING)
        self.assertEqual(len(manager.events), 4)

    def test_generic_transition_cannot_create_trade_stage(self):
        manager = state.StateManager()
        manager.apply(request(state.OBSERVED, "observe"))
        manager.apply(request(state.INTEREST, "interest"))
        manager.apply(request(state.PREBUY, "prebuy"))
        with self.assertRaises(state.TransitionRejected):
            manager.apply(
                request(state.HOLDING, "buy", quantity=1, fill="PAPER-000001")
            )

    def test_paper_sell_without_holding_and_zero_quantity_are_rejected(self):
        manager = state.StateManager()
        with self.assertRaises(state.TransitionRejected):
            manager.apply_paper_fill(
                request(state.SOLD, "sell", quantity=1, fill="PAPER-000002")
            )
        with self.assertRaises(state.TransitionRejected):
            manager.apply_paper_fill(
                request(state.HOLDING, "buy", quantity=0, fill="PAPER-000001")
            )

    def test_stage_skipping_is_rejected(self):
        manager = state.StateManager()
        manager.apply(request(state.OBSERVED, "a"))
        with self.assertRaises(state.TransitionRejected):
            manager.apply(request(state.HOLDING, "b", quantity=1, fill="PAPER-BUY-1"))

    def test_same_day_hash_chain_restores(self):
        manager = state.StateManager()
        first = manager.apply(request(state.OBSERVED, "a"))
        second = manager.apply(request(state.INTEREST, "b"))
        restored = state.StateManager()
        restored.restore(
            [state.asdict(first), state.asdict(second)],
            session_date="2026-08-14",
            engine_version="TEST",
        )
        self.assertEqual(restored.stage_of("005930"), state.INTEREST)

    def test_same_day_paper_fill_chain_restores(self):
        manager = state.StateManager()
        first = manager.apply_paper_fill(
            request(state.HOLDING, "buy", quantity=2, fill="PAPER-000001")
        )
        second = manager.apply_paper_fill(
            request(state.SOLD, "sell", quantity=2, fill="PAPER-000002")
        )
        restored = state.StateManager()
        restored.restore(
            [state.asdict(first), state.asdict(second)],
            session_date="2026-08-14",
            engine_version="TEST",
        )
        self.assertEqual(restored.stage_of("005930"), state.SOLD)
        self.assertTrue(restored.has_paper_fill("PAPER-000002"))

    def test_other_engine_same_day_event_is_rejected(self):
        manager = state.StateManager()
        event = manager.apply(request(state.OBSERVED, "old-engine"))
        restored = state.StateManager()
        with self.assertRaisesRegex(state.TransitionRejected, "엔진 버전"):
            restored.restore(
                [state.asdict(event)],
                session_date="2026-08-14",
                engine_version="CURRENT-ENGINE",
            )

    def test_failed_durable_append_can_undo_only_last_transition(self):
        manager = state.StateManager()
        first = manager.apply(request(state.OBSERVED, "a"))
        second = manager.apply(request(state.INTEREST, "b"))
        manager.discard_last(second)
        self.assertEqual(manager.events, (first,))
        self.assertEqual(manager.stage_of("005930"), state.OBSERVED)

    def test_jsonl_log_round_trip(self):
        manager = state.StateManager()
        event = manager.apply(request(state.OBSERVED, "a"))
        with tempfile.TemporaryDirectory() as directory:
            log = state.JsonlEventLog(Path(directory) / "events.jsonl")
            log.append(event)
            self.assertEqual(log.read()[0]["hash"], event.hash)


if __name__ == "__main__":
    unittest.main()
