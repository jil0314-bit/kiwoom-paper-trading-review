"""Structural regressions for the four 2026-09-07 audit findings.

Synthetic boundary fixtures, not historical strategy/profitability replay.
Run in the existing isolated runner; never import the production engine in place.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase, mock

from tests.test_order_idempotency import probe, quote
from tests.test_state_invariants import engine, engine_probe, transition_request
from pathlib import Path
import tempfile


state = engine._V7600_STATE
Engine = engine.V7600ThreeStageEngine


def entry_probe(stage=None):
    item = engine_probe()
    now = datetime.now(engine.KST)
    item._v7609_target_exit_at = {}
    item._v76_strict_prebuy = {"005930": now - timedelta(seconds=2)}
    item._v76_strict_interest = {}
    item.pending_entries = {}
    item._v76_fast_fail = {}
    item.broker = SimpleNamespace(positions={}, save_state=mock.Mock())
    item._v7609_enforce_fixed_stop = mock.Mock(return_value=False)
    if stage is not None:
        for i, current in enumerate((state.OBSERVED, state.INTEREST, state.PREBUY)):
            item._v7609_stage_manager.apply(transition_request(
                "005930", current, f"entry-{i}", now - timedelta(seconds=20-i)
            ))
            if current == stage:
                break
        if stage == state.SOLD:
            for i, current in enumerate((state.HOLDING, state.SOLD)):
                item._v7609_stage_manager.apply_paper_fill(transition_request(
                    "005930", current, f"fill-{i}", now,
                    quantity=1, fill=f"PAPER-{i}",
                ))
    return item


def entry_signal(pattern, reason="automatic"):
    return SimpleNamespace(
        symbol="005930", timestamp=datetime.now(engine.KST), pattern=pattern,
        reason=reason, stop_loss_pct=engine.V7609_FIXED_STOP_LOSS_PCT,
        reference_price=70000.0,
    )


class FourRouteFixTests(TestCase):
    def test_01_breakout_cannot_buy_from_missing_observed_or_interest(self):
        for current in (None, state.OBSERVED, state.INTEREST):
            with self.subTest(stage=current):
                item = entry_probe(current)
                with mock.patch.object(engine._PARENT_ENGINE, "_attempt_entry") as parent_buy:
                    self.assertFalse(item._v7609_attempt_entry_inner(
                        entry_signal(engine.BREAKOUT_PATTERN)))
                    parent_buy.assert_not_called()

    def test_02_bottom_cannot_buy_from_stale_prebuy_map_alone(self):
        item = entry_probe(state.OBSERVED)
        with mock.patch.object(engine._V7588, "_attempt_entry") as parent_buy:
            self.assertFalse(item._v7609_attempt_entry_inner(entry_signal(engine.PATTERN)))
            parent_buy.assert_not_called()

    def test_03_both_approved_routes_reach_unchanged_parent(self):
        for pattern in (engine.PATTERN, engine.BREAKOUT_PATTERN):
            with self.subTest(pattern=pattern):
                item = entry_probe(state.PREBUY)
                sig = entry_signal(pattern)
                parent_class = engine._V7588 if pattern == engine.PATTERN else engine._PARENT_ENGINE
                with mock.patch.object(parent_class, "_attempt_entry", autospec=True, return_value=True) as buy:
                    self.assertTrue(item._v7609_attempt_entry_inner(sig, from_retry=True))
                    buy.assert_called_once_with(item, sig, from_retry=True)

    def test_04_manual_route_remains_allowed(self):
        item = entry_probe()
        with mock.patch.object(engine._PARENT_ENGINE, "_attempt_entry", autospec=True, return_value=True) as buy:
            self.assertTrue(item._v7609_attempt_entry_inner(
                entry_signal(engine.BREAKOUT_PATTERN, "즉시 PAPER 매수")))
            buy.assert_called_once()

    def test_05_sale_blocks_old_approval_even_after_minute_changes(self):
        for pattern in (engine.PATTERN, engine.BREAKOUT_PATTERN):
            item = entry_probe(state.SOLD)
            item._v7609_target_exit_at["005930"] = datetime.now(engine.KST) - timedelta(minutes=2)
            with mock.patch.object(engine._V7588, "_attempt_entry") as buy:
                self.assertFalse(item._v7609_attempt_entry_inner(entry_signal(pattern)))
                buy.assert_not_called()

    def test_06_approval_older_than_exit_is_rejected(self):
        item = entry_probe(state.PREBUY)
        now = datetime.now(engine.KST)
        item._v76_strict_prebuy["005930"] = now - timedelta(minutes=4)
        item._v7609_target_exit_at["005930"] = now - timedelta(minutes=2)
        self.assertFalse(item._v7609_current_prebuy_approved("005930", now))

    def test_07_yesterday_approval_is_rejected(self):
        item = entry_probe(state.PREBUY)
        now = datetime.now(engine.KST)
        item._v76_strict_prebuy["005930"] = now - timedelta(days=1)
        self.assertFalse(item._v7609_current_prebuy_approved("005930", now))

    def test_08_actual_fill_bridge_consumes_approval_on_sale(self):
        with tempfile.TemporaryDirectory() as folder:
            item = probe(Path(folder) / "intents.jsonl")
            stamp = datetime.now(engine.KST)
            item._v76_strict_interest = {"005930": stamp}
            item._v76_strict_prebuy = {"005930": stamp}
            item.pending_entries = {"005930": object()}
            item.broker.buy("005930", 1, quote(), "seed")
            item.broker.sell("005930", quote(), "예약목표+1.5%")
            self.assertNotIn("005930", item._v76_strict_prebuy)
            self.assertNotIn("005930", item._v76_strict_interest)
            self.assertNotIn("005930", item.pending_entries)
            self.assertEqual(item._v7609_stage_manager.stage_of("005930"), state.SOLD)
            self.assertEqual(len(item._v7609_stage_log.events), 2)

    def stop_probe(self, price, stop):
        item = object.__new__(Engine)

        class Position:
            entry_price = price
            target_price = 0.0
            target_order_active = False
            quantity = 1

            def __init__(self):
                self._stop = stop
                self.writes = []

            @property
            def stop_price(self):
                return self._stop

            @stop_price.setter
            def stop_price(self, value):
                self.writes.append(value)
                self._stop = value

        position = Position()
        item.broker = SimpleNamespace(positions={"005930": position})
        item.broker.save_state = mock.Mock()
        item.logger = SimpleNamespace(warning=mock.Mock())
        item._label = lambda symbol: symbol
        item._gem_order_announced = set()
        return item, position

    def test_09_repeated_stop_registration_never_writes_or_saves_five_percent(self):
        item, pos = self.stop_probe(41650.0, 39550.0)
        saved_stops = []
        item.broker.save_state.side_effect = lambda: saved_stops.append(pos.stop_price)
        legacy = next(cls for cls in Engine.__mro__[1:] if "_enforce_gem_orders" in cls.__dict__)
        with mock.patch.object(legacy, "_enforce_gem_orders", side_effect=AssertionError("old 5% writer")):
            for _ in range(100):
                self.assertTrue(item._enforce_gem_orders("005930", announce=False))
        # EXIT-LINK-20260909-003 replaces upward rounding with the nearest tick.
        self.assertEqual(pos.writes, [41050.0])
        self.assertEqual(saved_stops, [41050.0])
        self.assertEqual(pos.target_price, 42500.0)
        self.assertTrue(pos.target_order_active)

    def test_10_approved_nearest_stop_and_strictly_above_target_ticks(self):
        for price, stop, target in ((4115., 4055., 4200.), (6740., 6640., 6880.),
                                    (80000., 78800., 81600.)):
            item, pos = self.stop_probe(price, price * .95)
            item._enforce_gem_orders("005930", announce=False)
            self.assertEqual(pos.stop_price, stop)
            self.assertEqual(pos.target_price, target)

    def test_11_already_correct_stop_does_not_save_again(self):
        item, pos = self.stop_probe(80000.0, 78800.0)
        pos.target_price = 81600.0
        pos.target_order_active = True
        item._enforce_gem_orders("005930", announce=False)
        self.assertEqual(pos.writes, [])
        item.broker.save_state.assert_not_called()

    def test_12_missing_position_remains_noop(self):
        item, _ = self.stop_probe(80000.0, 78400.0)
        self.assertFalse(item._enforce_gem_orders("000660", announce=False))
        item.broker.save_state.assert_not_called()

    def refresh_probe(self, stamp):
        item = object.__new__(Engine)
        bar = SimpleNamespace(timestamp=stamp, open=100., high=101., low=99., close=100., volume=100)
        item._v7609_roll_session = mock.Mock()
        item._v7609_load_one_minute_bars = mock.Mock(return_value=[bar])
        item._v7609_refresh_same_time_ratio = mock.Mock()
        item._v7609_admit_observation_from_one_minute = mock.Mock()
        item.broker = SimpleNamespace(positions={"005930": object()})
        item._v7609_fast_failure_exit = mock.Mock(return_value=True)
        item.logger = SimpleNamespace(warning=mock.Mock())
        item._label = lambda symbol: symbol
        return item

    def run_boundary(self, stamp):
        before = datetime(2026, 9, 7, 8, 18, 59, tzinfo=engine.KST)
        after = before + timedelta(seconds=1)
        item = self.refresh_probe(stamp)

        class Clock(datetime):
            times = iter((before, after))

            @classmethod
            def now(cls, tz=None):
                return next(cls.times)

        # Preserve datetime isinstance checks while controlling only the clock.
        class ClockMeta(type):
            def __instancecheck__(cls, value):
                return isinstance(value, datetime)

        FixedClock = ClockMeta("FixedClock", (Clock,), {})
        with mock.patch.object(engine, "datetime", FixedClock):
            item._refresh_actual_bars("005930", SimpleNamespace(price=100))
        return item, after

    def test_13_minute_boundary_accepts_just_completed_bar(self):
        item, after = self.run_boundary(datetime(2026, 9, 7, 8, 18, tzinfo=engine.KST))
        item._v7609_admit_observation_from_one_minute.assert_called_once()
        self.assertEqual(item._v7609_admit_observation_from_one_minute.call_args.args[2], after)
        item.logger.warning.assert_not_called()

    def test_14_genuinely_old_bar_remains_blocked(self):
        item, _ = self.run_boundary(datetime(2026, 9, 7, 8, 17, tzinfo=engine.KST))
        item._v7609_admit_observation_from_one_minute.assert_not_called()

    def test_15_forming_bar_is_still_excluded(self):
        now = datetime(2026, 9, 7, 8, 19, tzinfo=engine.KST)
        raw = [SimpleNamespace(timestamp=now-timedelta(minutes=1)), SimpleNamespace(timestamp=now)]
        self.assertEqual(Engine._v7609_completed_one_minute_bars(raw, now), raw[:1])
