"""Run only in the existing isolated runner: no production import in place."""
from dataclasses import asdict
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tests.test_state_invariants import engine, state, engine_probe
from tests.test_volume_prebuy import NOW, fixture


class ClockMeta(type):
    def __instancecheck__(cls, value):
        return isinstance(value, datetime)


class FixedClock(datetime, metaclass=ClockMeta):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


class VolumeEngineTests(unittest.TestCase):
    def setUp(self):
        self.p = engine_probe()
        self.addCleanup(self.p._test_order_folder.cleanup)
        self.p.names = {'005930': '삼성전자'}
        self.p.broker = SimpleNamespace(positions={})
        self.p.pending_entries = {}
        self.p._v7587_excluded = {}
        self.p._supply_potential = {}
        self.p._v7588_phase = {}
        self.p._v7588_flush = {}
        self.p._v76_strict_observation = {}
        self.p._v76_strict_interest = {}
        self.p._v76_strict_prebuy = {}
        self.p._v76_strict_market_hold = {}
        self.p._save_supply_potential = Mock()
        self.p._attempt_entry = Mock(side_effect=AssertionError('admission cannot order'))
        self.p.client.get_daily_bars = Mock(return_value=[SimpleNamespace(
            symbol='005930', trading_date=(NOW-timedelta(days=1)).date(), close=100)])
        self.return_now = NOW

    def run_route(self, rows=None, now=NOW):
        with patch.object(engine, 'datetime', wraps=engine.datetime) as clock:
            clock.now.return_value = self.return_now
            return self.p._v7609_try_volume_prebuy('005930', fixture() if rows is None else rows, now)

    def test_direct_admission_has_one_durable_event_no_order(self):
        self.run_route()
        self.assertEqual(self.p._v7609_stage_manager.stage_of('005930'), state.PREBUY)
        self.assertEqual(len(self.p._v7609_stage_log.events), 1)
        self.assertEqual(self.p._v7588_phase['005930'], 'PREBUY')
        self.assertEqual(self.p._v76_strict_observation, {})
        self.assertEqual(self.p._v76_strict_interest, {})
        self.assertIn('5배급등거래', self.p._supply_potential['005930'].source)
        self.p._attempt_entry.assert_not_called()

    def test_repeat_does_not_duplicate_or_refetch(self):
        self.run_route(); self.run_route()
        self.assertEqual(len(self.p._v7609_stage_log.events), 1)
        self.p.client.get_daily_bars.assert_called_once_with('005930', limit=3)

    def test_no_daily_lookup_without_volume_candidate(self):
        rows = fixture(); rows[-1]['volume'] = 499
        self.run_route(rows)
        self.p.client.get_daily_bars.assert_not_called()
        self.assertEqual(self.p._v7609_stage_manager.events, ())

    def test_price_gate_cache_and_no_minimum_return(self):
        self.p.client.get_daily_bars.return_value[0].close = 101
        self.run_route(); self.run_route()
        self.assertEqual(self.p._v7609_stage_manager.events, ())
        self.p.client.get_daily_bars.assert_called_once()

    def test_failure_backoff_does_not_throw_or_change_stage(self):
        self.p.client.get_daily_bars.side_effect = OSError('offline')
        self.assertEqual(self.run_route(), NOW)
        self.run_route()
        self.p.client.get_daily_bars.assert_called_once()
        self.assertEqual(self.p._v7609_stage_manager.events, ())

    def test_fetch_crossing_minute_rejects_stale_and_returns_new_clock(self):
        self.return_now = NOW+timedelta(minutes=1)
        self.assertEqual(self.run_route(), NOW+timedelta(minutes=1))
        self.assertEqual(self.p._v7609_stage_manager.events, ())

    def test_log_failure_rolls_back_no_mirror_or_order(self):
        self.p._v7609_stage_log.append = Mock(side_effect=OSError('disk'))
        self.run_route()
        self.assertEqual(self.p._v7609_stage_manager.events, ())
        self.assertEqual(self.p._supply_potential, {})
        self.p._save_supply_potential.assert_not_called()

    def test_restore_rebuilds_missing_mirror_without_observation(self):
        self.run_route()
        rows = [asdict(e) for e in self.p._v7609_stage_manager.events]
        self.p._v7609_stage_manager = state.StateManager()
        self.p._v7609_stage_manager.restore(rows, session_date=NOW.date().isoformat(), engine_version=engine.VERSION)
        self.p._supply_potential.clear(); self.p._v7588_phase.clear(); self.p._v76_strict_prebuy.clear()
        self.p._v7609_restore_today_scanner_state(NOW)
        self.p._v7609_prune_unverified_scanner_rows(NOW)
        self.assertIn('005930', self.p._supply_potential)
        self.assertEqual(self.p._v7588_phase['005930'], 'PREBUY')
        self.assertEqual(self.p._v76_strict_observation, {})
        stamp, reason = self.p._v7609_strict_timeline_evidence('005930', 'PREBUY', NOW)
        self.assertEqual(stamp, NOW-timedelta(minutes=1))
        self.assertEqual(reason, '5배급등거래')

    def test_hold_pending_and_manual_exclusion_no_lookup(self):
        for attr in (self.p.broker.positions, self.p.pending_entries, self.p._v7587_excluded):
            attr['005930'] = object()
            self.run_route()
            attr.clear()
        self.p.client.get_daily_bars.assert_not_called()
        self.assertEqual(self.p._v7609_stage_manager.events, ())

    def test_fake_mirror_or_yesterday_cannot_restore(self):
        self.p._supply_potential['005930'] = self.p._v7588_make_potential('005930', '매수직전', '5배급등거래', NOW)
        self.p._v7609_prune_unverified_scanner_rows(NOW)
        self.assertNotIn('005930', self.p._supply_potential)
        self.run_route()
        self.assertEqual(self.p._v7609_volume_prebuy_event('005930', NOW+timedelta(days=1)), (None, None))

    def test_actual_refresh_calls_new_route_without_fake_observation_or_order(self):
        raw = [SimpleNamespace(**dict(b, timestamp=datetime.fromisoformat(b['timestamp']))) for b in fixture()]
        self.p._v7609_load_one_minute_bars = Mock(return_value=raw)
        self.p._v7609_roll_session = Mock()
        self.p._v7609_refresh_same_time_ratio = Mock()
        self.p._v76_fast_fail = {}
        self.p._v7609_five_bar_average_bear_interest_decision = Mock(return_value=(False, '', None))
        rejected = SimpleNamespace(allowed=False)
        with patch.object(engine, 'datetime', FixedClock), \
             patch.object(engine, 'three_bear_rising_volume_decision', return_value=rejected), \
             patch.object(engine, 'konan_breakout_pullback_decision', return_value=rejected):
            self.p._refresh_actual_bars('005930', SimpleNamespace(price=101))
        self.assertEqual([e.new_stage for e in self.p._v7609_stage_manager.events], [state.PREBUY])
        self.assertEqual(self.p._v76_strict_observation, {})
        self.p._attempt_entry.assert_not_called()

    def test_actual_dashboard_projects_direct_prebuy_reason(self):
        self.run_route()
        self.p._v7609_roll_session = Mock()
        self.p._v7609_postclose_review_rows = Mock(return_value=None)
        self.p._v7609_fill_records = Mock(return_value={})
        self.p._v7591_timeline = {}
        self.p._r21_stage_times = {}
        self.p.latest_quotes = {}
        parent_rows = {'rows': [dict(stage='매수직전', symbol='005930', name='삼성전자', reason='구조 관찰')]}
        with patch.object(engine, 'datetime', FixedClock), \
             patch.object(engine._PARENT_ENGINE, '_gui_snapshot', return_value=parent_rows):
            result = self.p._gui_snapshot()
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['stage'], '매수가능성')
        self.assertEqual(result['rows'][0]['stage_time'], '10:01:00')
        self.assertIn('5배급등거래', result['rows'][0]['reason'])
        self.assertEqual(len(self.p._v7609_stage_manager.events), 1)


if __name__ == '__main__':
    unittest.main()
