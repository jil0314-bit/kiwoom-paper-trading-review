from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "SOURCE" / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import bot_v76_00_three_stage_ctrlv_paper as engine


state = engine._V7600_STATE


class SilentLogger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class MemoryEventLog:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


def transition_request(
    symbol: str,
    stage: str,
    signal: str,
    stamp: datetime,
    *,
    version: str = engine.VERSION,
    quantity: int = 0,
    fill: str = "",
):
    return state.TransitionRequest(
        signal_id=signal,
        symbol=symbol,
        new_stage=stage,
        rule_id=f"STATE005_{stage}",
        data_time=stamp.isoformat(),
        decision_time=(stamp + timedelta(seconds=1)).isoformat(),
        engine_version=version,
        reason=f"STATE005 {stage}",
        evidence={"source": "STATE005", "ohlcv": [1, 2, 1, 2, 100]},
        quantity=quantity,
        paper_fill_id=fill,
    )


def engine_probe():
    probe = object.__new__(engine.V7600ThreeStageEngine)
    probe._v7609_stage_manager = state.StateManager()
    probe._v7609_stage_lock = threading.RLock()
    probe._v7609_stage_log = MemoryEventLog()
    probe._v7609_state_log_obj = lambda _now: probe._v7609_stage_log
    evidence_bar = SimpleNamespace(
        timestamp=datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST),
        open=70000.0,
        high=70100.0,
        low=69900.0,
        close=70050.0,
        volume=12000,
    )
    probe.client = SimpleNamespace(v7580_raw_cache={"005930": [evidence_bar]})
    probe._test_order_folder = tempfile.TemporaryDirectory()
    probe._v7609_order_thread = threading.local()
    probe._v7609_order_ledger = engine._V7600_ORDER.OrderIntentLedger(
        Path(probe._test_order_folder.name) / "order_intents.jsonl"
    )
    probe.logger = SilentLogger()
    probe._label = lambda symbol: str(symbol)
    return probe


class StateInvariantIntegrationTests(unittest.TestCase):
    def test_inv_01_actual_broker_bridge_blocks_unheld_sell(self):
        class Broker:
            def __init__(self) -> None:
                self.positions = {}
                self.sell_calls = 0
                self._record_fill = lambda _fill: None

            def sell(self, _symbol, *args, **kwargs):
                self.sell_calls += 1
                return "SHOULD_NOT_RUN"

        probe = engine_probe()
        broker = Broker()
        probe.broker = broker
        probe._v7609_seen_paper_fill_ids = set()
        probe._v7609_latest_buy_fill = {}
        probe._v7609_load_paper_fill_identity = lambda: None

        probe._v7609_install_paper_fill_state_bridge()
        with self.assertRaisesRegex(ValueError, "보유수량 0"):
            broker.sell("005930")
        self.assertEqual(broker.sell_calls, 0)

    def test_inv_02_actual_entry_path_blocks_same_signal_after_target_exit(self):
        probe = engine_probe()
        exit_at = datetime(2026, 8, 14, 9, 0, 35, tzinfo=engine.KST)
        probe._v7609_target_exit_at = {"005930": exit_at}
        probe.pending_entries = {"005930": object()}
        signal = SimpleNamespace(
            symbol="005930",
            timestamp=exit_at + timedelta(seconds=13),
            reason="자동 눌림목 매수",
            pattern=engine.PATTERN,
            stop_loss_pct=engine.V7609_FIXED_STOP_LOSS_PCT,
        )

        self.assertFalse(probe._attempt_entry(signal))
        self.assertNotIn("005930", probe.pending_entries)

    def test_inv_03_actual_dashboard_projection_matches_positions_and_fills(self):
        now = datetime.now(engine.KST).replace(microsecond=0)
        held_symbol = "005930"
        sold_symbol = "000660"
        position = SimpleNamespace(
            quantity=2,
            entry_time=now - timedelta(minutes=10),
            entry_price=70000.0,
        )
        fills = {
            held_symbol: {
                "buy_time": now - timedelta(minutes=10),
                "buy_price": 70000.0,
                "buy_qty": 2,
            },
            sold_symbol: {
                "buy_time": now - timedelta(minutes=20),
                "buy_price": 200000.0,
                "sell_entry_time": now - timedelta(minutes=20),
                "sell_entry_price": 200000.0,
                "sell_time": now - timedelta(minutes=2),
                "sell_price": 203000.0,
                "sell_qty": 1,
                "sell_pnl": 3000.0,
                "sell_pnl_pct": 0.015,
                "sell_reason": "목표가",
                "sell_cycle": 1,
            },
        }
        parent_snapshot = {
            "rows": [
                {"stage": "매수종목", "symbol": held_symbol, "name": "삼성전자", "reason": "보유"},
                {"stage": "관심종목", "symbol": held_symbol, "name": "삼성전자", "reason": "중복 하위단계"},
                {"stage": "매도종목", "symbol": sold_symbol, "name": "SK하이닉스", "reason": "매도"},
            ]
        }
        probe = engine_probe()
        probe._v7609_session_date = now.date()
        probe._v7609_roll_session = lambda _now: None
        probe._v7609_reconcile_strict_supply_stages = lambda _now: False
        probe._v7609_postclose_review_rows = lambda _snapshot, _now: None
        probe._v7609_fill_records = lambda _now: fills
        probe.broker = SimpleNamespace(positions={held_symbol: position})
        probe._v76_strict_interest = {}
        probe._v76_strict_prebuy = {}
        probe._v76_strict_market_hold = {}
        probe._v7591_timeline = {}
        probe._r21_stage_times = {}
        probe.latest_quotes = {}
        probe.names = {held_symbol: "삼성전자", sold_symbol: "SK하이닉스"}

        with mock.patch.object(engine._PARENT_ENGINE, "_gui_snapshot", return_value=parent_snapshot):
            snapshot = probe._gui_snapshot()

        actual = {(row["symbol"], row["stage"]) for row in snapshot["rows"]}
        self.assertEqual(
            actual,
            {(held_symbol, "매수종목"), (sold_symbol, "매도종목")},
        )

    def test_inv_04_actual_publish_gateway_enforces_ordered_transitions(self):
        probe = engine_probe()
        stamp = datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST)

        self.assertFalse(
            probe._v7609_record_authoritative_stage(
                "005930", "관심종목", "순서위반", stamp
            )
        )
        self.assertTrue(
            probe._v7609_record_authoritative_stage(
                "005930", "관찰후보", "정상관찰", stamp
            )
        )
        self.assertTrue(
            probe._v7609_record_authoritative_stage(
                "005930", "관심종목", "정상관심", stamp + timedelta(minutes=1)
            )
        )
        self.assertTrue(
            probe._v7609_record_authoritative_stage(
                "005930", "매수직전", "정상직전", stamp + timedelta(minutes=2)
            )
        )
        self.assertEqual(
            [event.new_stage for event in probe._v7609_stage_manager.events],
            [state.OBSERVED, state.INTEREST, state.PREBUY],
        )

    def test_inv_05_actual_refresh_path_uses_only_raw_one_minute_bars(self):
        probe = engine_probe()
        probe.broker = SimpleNamespace(positions={})
        calls = {"purge": 0, "reconcile": 0, "save": 0, "one_minute": 0}
        probe._purge_expired_potential = lambda: calls.__setitem__("purge", calls["purge"] + 1)
        probe._v7609_reconcile_strict_supply_stages = lambda _now: (
            calls.__setitem__("reconcile", calls["reconcile"] + 1) or False
        )
        probe._save_supply_potential = lambda: calls.__setitem__("save", calls["save"] + 1)

        with mock.patch.object(
            engine._PARENT_ENGINE,
            "_update_supply_potential",
            side_effect=AssertionError("3분봉 평가 호출됨"),
        ):
            probe._update_supply_potential("005930")

        late = SimpleNamespace(timestamp=datetime(2026, 8, 14, 9, 2, tzinfo=engine.KST))
        early = SimpleNamespace(timestamp=datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST))

        def get_minute_bars(symbol, *, limit, completed_only):
            calls["one_minute"] += 1
            self.assertEqual((symbol, limit, completed_only), ("005930", 900, True))
            return [late, early]

        probe.client = SimpleNamespace(get_minute_bars=get_minute_bars)
        bars = probe._v7609_load_one_minute_bars("005930")
        self.assertEqual([bar.timestamp for bar in bars], [early.timestamp, late.timestamp])
        self.assertEqual(calls, {"purge": 1, "reconcile": 1, "save": 0, "one_minute": 1})

    def test_inv_06_other_engine_restore_is_rejected_and_rollover_preserves_holdings(self):
        stamp = datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST)
        old_manager = state.StateManager()
        old_event = old_manager.apply(
            transition_request(
                "005930",
                state.OBSERVED,
                "old-engine-observe",
                stamp,
                version="OLD-ENGINE",
            )
        )
        restored = state.StateManager()
        with self.assertRaisesRegex(state.TransitionRejected, "엔진 버전"):
            restored.restore(
                [asdict(old_event)],
                session_date="2026-08-14",
                engine_version=engine.VERSION,
            )

        probe = engine_probe()
        position = SimpleNamespace(quantity=1)
        probe.broker = SimpleNamespace(positions={"005930": position})
        probe._v7609_session_date = (stamp - timedelta(days=1)).date()
        probe._supply_potential = {"005930": object()}
        probe._v7588_phase = {"005930": "INTEREST"}
        probe._v7588_flush = {"005930": object()}
        probe._v7588_order_bucket = {"005930": object()}
        probe._r21_stage_times = {("관심종목", "005930"): "09:01:00"}
        probe._v76_order_bar = {("005930", 1): stamp}
        probe._v76_strict_observation = {"005930": stamp}
        probe._v76_strict_interest = {"005930": stamp}
        probe._v76_strict_prebuy = {"005930": stamp}
        probe._v76_strict_market_hold = {"005930": stamp}
        probe._v76_strict_block = {"005930": (stamp, "old")}
        probe._v7609_intrabar_interest_hold_log = {"005930": stamp}
        probe._v76_fast_fail = {"005930": {}}
        probe._save_supply_potential = lambda: None

        with mock.patch.object(engine, "_v7609_prune_collaboration_hub_today", return_value=None):
            probe._v7609_roll_session(stamp)

        self.assertEqual(probe._supply_potential, {})
        self.assertIsNone(probe._v7609_stage_manager.stage_of("005930"))
        self.assertIs(probe.broker.positions["005930"], position)

    def test_inv_07_actual_0600_send_does_not_change_stage_hash(self):
        stamp = datetime(2026, 8, 14, 9, 1, tzinfo=engine.KST)
        manager = state.StateManager()
        manager.apply(
            transition_request("005930", state.OBSERVED, "observe", stamp)
        )
        before = tuple(event.hash for event in manager.events)

        link = object.__new__(engine.V7600CtrlVHeroLink)
        link._v7609_fast_edit = 3000
        link._v7609_fast_chart = 2000
        link._v7609_fast_top = 1000
        link._fast_exact_edit_send = lambda *args: (
            engine.v57.LinkStatus.SENT_UNVERIFIED,
            "STATE005 simulated exact 0600 display",
        )
        status, _message = link.send_symbol("005930")

        self.assertNotEqual(status, engine.v57.LinkStatus.FAILED)
        self.assertEqual(tuple(event.hash for event in manager.events), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
