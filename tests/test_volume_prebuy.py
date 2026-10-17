"""VOL5 synthetic boundary regressions, not profitability or live REST proof."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.util
import sys
import unittest

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'
spec = importlib.util.spec_from_file_location('vol5_test_state', CORE / 'v7600_state_manager.py')
state = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = state
spec.loader.exec_module(state)
rule = state._VOLUME5
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 9, 10, 1, tzinfo=KST)


def bar(stamp, volume=100, close=101):
    return dict(timestamp=stamp.isoformat(), open=102, high=103, low=99, close=close, volume=volume)


def fixture(now=NOW):
    return [bar(now - timedelta(minutes=i)) for i in range(41, 1, -1)] + [bar(now-timedelta(minutes=1), 500)]


def evidence(rows=None, now=NOW, close=100, prior=None):
    return rule.evaluate(fixture(now) if rows is None else rows, now=now,
                         previous_day=prior or (now-timedelta(days=1)).date(), previous_close=close)


def request(ev=None, now=NOW):
    ev = evidence(now=now) if ev is None else ev
    return state.TransitionRequest('vol5-signal', '005930', state.PREBUY, rule.RULE_ID,
                                   ev['data_time'], now.isoformat(), 'TEST', '5배급등거래', ev)


class VolumeRuleTests(unittest.TestCase):
    def test_exact_fivefold_and_current_excluded(self):
        self.assertEqual(evidence()['volume_multiple'], 5)
        rows = fixture(); rows[-1]['volume'] = 499
        self.assertIsNone(evidence(rows))

    def test_strictly_above_previous_close_no_minimum_or_bullish_gate(self):
        rows = fixture(); rows[-1]['close'] = 100.001
        self.assertIsNotNone(evidence(rows))  # Red candle intentionally accepted.
        for close in (100, 99.5):
            rows[-1]['close'] = close
            self.assertIsNone(evidence(rows))

    def test_opening_first_fifteen_use_previous_day(self):
        for hour, minute in ((8, 0), (8, 14), (9, 0), (9, 14)):
            trigger = NOW.replace(hour=hour, minute=minute)
            now = trigger + timedelta(minutes=1)
            previous = NOW.replace(day=8, hour=15, minute=0)
            rows = [bar(previous-timedelta(minutes=i)) for i in range(39, -1, -1)]
            rows += [bar(trigger-timedelta(minutes=1), 9999), bar(trigger, 500)]
            result = evidence(rows, now)
            self.assertEqual(result['reference_kind'], 'PREVIOUS_SESSION_LAST40')
            self.assertEqual(result['reference_average_volume'], 100)

    def test_after_opening_uses_last_forty_completed_bars(self):
        for hour in (8, 9):
            now = NOW.replace(hour=hour, minute=16)
            self.assertEqual(evidence(now=now)['reference_kind'], 'PREVIOUS40')

    def test_first_fifteen_not_cumulative_volume(self):
        now = NOW.replace(hour=9, minute=15)
        prior = NOW.replace(day=8, hour=15, minute=0)
        rows = [bar(prior-timedelta(minutes=i)) for i in range(39, -1, -1)]
        rows += [bar(now-timedelta(minutes=i), 400) for i in range(15, 0, -1)]
        self.assertIsNone(evidence(rows, now))

    def test_forming_ignored_and_stale_rejected(self):
        self.assertEqual(evidence(fixture()+[bar(NOW, 99999)]), evidence())
        self.assertIsNone(evidence(fixture(), NOW+timedelta(minutes=1)))

    def test_missing_zero_duplicate_and_bad_prices_rejected(self):
        self.assertIsNone(evidence(fixture()[1:]))
        rows = fixture()
        for row in rows[:-1]: row['volume'] = 0
        self.assertIsNone(evidence(rows))
        self.assertIsNone(evidence(fixture()+[fixture()[0]]))
        rows = fixture(); rows[-1]['high'] = 1
        with self.assertRaises(ValueError): evidence(rows)
        for previous_close in (0, float('nan')):
            self.assertIsNone(evidence(close=previous_close))

    def test_weekend_previous_session_and_exact_forty(self):
        now = NOW.replace(day=7, hour=8, minute=1)
        friday = now.replace(day=4, hour=15, minute=0)
        rows = [bar(friday-timedelta(minutes=i)) for i in range(39, -1, -1)]
        rows += [bar(now-timedelta(minutes=1), 500)]
        self.assertIsNotNone(evidence(rows, now, prior=friday.date()))
        self.assertIsNone(evidence(rows[1:], now, prior=friday.date()))

    def test_direct_transition_recomputes_evidence(self):
        manager = state.StateManager(); event = manager.apply(request())
        self.assertIsNone(event.old_stage)
        self.assertEqual([e.new_stage for e in manager.events], [state.PREBUY])
        with self.assertRaises(state.TransitionRejected): manager.apply(request())
        with self.assertRaises(state.TransitionRejected): manager.apply(replace(request(), signal_id='other'))
        altered = deepcopy(evidence()); altered['trigger_bar']['volume'] = 499
        with self.assertRaises(state.TransitionRejected): state.StateManager().apply(request(altered))

    def test_ordinary_route_still_cannot_skip_stages(self):
        with self.assertRaises(state.TransitionRejected):
            state.StateManager().apply(replace(request(), rule_id='old-rule'))
        for target in (state.INTEREST, state.HOLDING, state.SOLD):
            with self.assertRaises(state.TransitionRejected):
                state.StateManager().apply(replace(request(), new_stage=target))

    def test_protected_stages_not_overwritten(self):
        for prior in (state.MARKET_HOLD, state.HOLDING, state.SOLD):
            manager = state.StateManager()
            manager._stage_by_symbol['005930'] = prior  # Boundary setup only.
            with self.assertRaises(state.TransitionRejected): manager.apply(request())
            self.assertEqual(manager.stage_of('005930'), prior)

    def test_restore_is_deterministic_without_fake_preceding_stages(self):
        manager = state.StateManager(); manager.apply(request())
        restored = state.StateManager()
        restored.restore([asdict(e) for e in manager.events], session_date='2026-09-09', engine_version='TEST')
        self.assertEqual(restored.events, manager.events)


if __name__ == '__main__':
    unittest.main()
