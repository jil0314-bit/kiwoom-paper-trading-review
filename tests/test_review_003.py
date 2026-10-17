"""September 13 verified defects and requested boundaries; offline PAPER only."""
from datetime import timedelta, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch, mock_open
import unittest
from tests.test_state_invariants import engine, engine_probe
from tests.test_support_exit import position, bars, policy
from tests.test_volume_prebuy import NOW
from v7609_observation import early_candidate
from v7609_trade_view import unique_names


def rise_bars(count=15, close=10400):
    return [SimpleNamespace(timestamp=NOW-timedelta(minutes=count-i), open=10000,
        low=10000, high=max(10000, close if i == count-1 else 10000),
        close=close if i == count-1 else 10000, volume=100) for i in range(count)]


class Review003Tests(unittest.TestCase):
    def test_early_four_exact_fifteen_bar_boundary(self):
        self.assertIsNotNone(early_candidate(rise_bars(), NOW))
        for count, close in ((16,10400),(15,10399),(15,10500)):
            self.assertIsNone(early_candidate(rise_bars(count,close), NOW))

    def test_early_rejects_stale_forming_duplicates_gap(self):
        raw = rise_bars()
        self.assertIsNone(early_candidate(raw, NOW+timedelta(minutes=1)))
        self.assertIsNone(early_candidate(raw, NOW-timedelta(seconds=1)))
        self.assertIsNone(early_candidate(raw+[raw[-1]], NOW))
        raw[-1].timestamp += timedelta(minutes=1)
        self.assertIsNone(early_candidate(raw, NOW+timedelta(minutes=1)))

    def test_lagged_observation_runs_real_evaluator_not_mock(self):
        probe = engine_probe()
        probe._v7588_phase = {}
        probe._v7588_publish = Mock()
        raw = rise_bars(close=10500)
        probe._v7609_admit_observation_from_one_minute('005930', raw, NOW+timedelta(minutes=3), allow_lag=True)
        probe._v7588_publish.assert_called_once()
        self.assertEqual(probe._v7588_publish.call_args.args[1], 'OBSERVED')
        self.assertIn('완성봉지연3분', probe._v7588_publish.call_args.args[3])
        probe._v7588_publish.reset_mock()
        probe._v7609_admit_observation_from_one_minute('005930', raw, NOW+timedelta(minutes=4), allow_lag=True)
        probe._v7588_publish.assert_not_called()
        probe._v7609_admit_observation_from_one_minute('005930', raw+[raw[-1]], NOW, allow_lag=True)
        probe._v7588_publish.assert_not_called()

    def test_early_never_writes_authoritative_or_higher_stage(self):
        p = engine_probe()
        p._v7588_phase, p.pending_entries, p._v7587_excluded = {}, {}, set()
        p.broker = SimpleNamespace(positions={})
        p._v7609_update_early_observation('005930', rise_bars(), NOW)
        self.assertIn('005930', p._v7609_early_observation)
        self.assertEqual(p._v7609_stage_manager.events, ())
        p._v7609_update_early_observation('005930', rise_bars(close=10399), NOW)
        self.assertNotIn('005930', p._v7609_early_observation)
        p._v7588_phase['005930'] = 'INTEREST'
        p._v7609_update_early_observation('005930', rise_bars(), NOW)
        self.assertNotIn('005930', p._v7609_early_observation)

    def test_scale_in_keeps_low_new_holding_resets(self):
        p = position()
        state, _ = policy.support_snapshot(p, bars(), NOW)
        p.entry_time += timedelta(minutes=5)
        later = bars()+[SimpleNamespace(timestamp=NOW, open=9850, high=9900, low=9500, close=9600)]
        updated, _ = policy.support_snapshot(p, later, NOW+timedelta(minutes=5), state)
        self.assertEqual(updated['low'], 9700)
        new, _ = policy.support_snapshot(position(entry_time=p.entry_time), later, NOW+timedelta(minutes=5), updated)
        self.assertEqual(new['low'], 9500)
        restart, _ = policy.support_snapshot(position(entry_time=p.entry_time), later,
            NOW+timedelta(minutes=5), entry_time_override=NOW)
        self.assertEqual(restart['low'], 9700)

    def test_lagged_stage_does_not_arm_interest_flush(self):
        p = engine_probe()
        p.broker = SimpleNamespace(positions={})
        p._v7588_phase, p.pending_entries, p._v7587_excluded = {}, {}, set()
        p._v7609_refresh_same_time_ratio = Mock()
        p._v7588_publish = Mock()
        p._v7609_five_bar_average_bear_interest_decision = Mock(side_effect=AssertionError('stale interest'))
        p._v7609_stage_only_admission('005930', rise_bars(close=10500), NOW+timedelta(minutes=1))
        self.assertEqual(p._v7588_publish.call_args.args[1], 'OBSERVED')

    def test_future_and_ambiguous_names_do_not_become_immediate(self):
        kwargs = dict(selected_symbol='005930', selected_name='삼성전자', known_names={'삼성전자':'005930'})
        for text in ('내일 매수해','삼성전자 다음주 매도해','10분 후 삼성전자 매수해','삼성전자 나중에 팔아줘'):
            self.assertNotIn(engine._v7609_compile_command(text, **kwargs).action, ('IMMEDIATE_BUY','IMMEDIATE_SELL'))
        self.assertEqual(unique_names([{'name':'동명이름','symbol':'000001'},
            {'name':'동명이름','symbol':'000002'}]), ({}, {'동명이름'}))

    def test_real_csv_reader_and_snapshot_keep_all_sale_events(self):
        now = datetime.now(engine.KST).replace(hour=10, minute=0, second=0, microsecond=0)
        header = 'timestamp,symbol,side,price,quantity,pnl,pnl_pct,reason,entry_time,entry_price\n'
        records = [f'{(now+timedelta(seconds=i)).isoformat()},005930,SELL,{10100+i},1,{100+i},0.01,test,{(now-timedelta(minutes=5)).isoformat()},10000\n' for i in range(23)]
        p = engine_probe()
        with patch.object(engine.Path, 'exists', return_value=True), patch.object(engine.Path, 'open', mock_open(read_data=header+''.join(records))):
            fills = p._v7609_fill_records(now)
        self.assertEqual(len(fills['005930']['sales']), 23)
        p._v7609_roll_session = Mock()
        p._v7609_reconcile_strict_supply_stages = Mock()
        p._v7609_postclose_review_rows = Mock(return_value=None)
        p._v7609_fill_records = Mock(return_value=fills)
        p.broker = SimpleNamespace(positions={})
        p.names = {'005930':'삼성전자'}
        with patch.object(engine._PARENT_ENGINE, '_gui_snapshot', return_value={'rows':[]}):
            snap = p._gui_snapshot()
        self.assertEqual(len(snap['rows']), 23)
        self.assertEqual({r['sell_cycle'] for r in snap['rows']}, set(range(1,24)))
        self.assertEqual(snap['rows'][0]['net_pnl'],100)
        self.assertEqual(snap['rows'][-1]['net_pnl'],122)

    def test_candidate_service_does_not_duplicate_parent_holding_monitor(self):
        p = engine_probe()
        p.broker = SimpleNamespace(positions={'005930':position()})
        calls = []
        p._monitor_one_position = lambda: calls.append('holding')
        p._v7609_poll_volume = lambda s: calls.append('volume')
        with patch.object(engine._PARENT_ENGINE, '_service_symbol', side_effect=lambda *a,**k:calls.append('service')):
            p._service_symbol('000660', hot=True)
        self.assertEqual(calls, ['volume','service'])

    def test_early_hot_priority_never_displaces_urgent_candidates(self):
        p = engine_probe()
        p.broker = SimpleNamespace(positions={})
        p.pending_entries, p._v7587_excluded = {}, set()
        p._v7588_phase = {'000001':'PREBUY'}
        p.hot_symbols = ['000003','000001']
        p._v7609_early_observation = {'000002': {'expires_at':datetime.now(engine.KST)+timedelta(minutes=1)}}
        with patch.object(engine._PARENT_ENGINE, '_update_hot_symbols'):
            p._update_hot_symbols()
        self.assertEqual(p.hot_symbols,['000001','000002'])
