"""Approved 2026-09-10 cost/support boundaries and command safety."""
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tests.test_state_invariants import engine, engine_probe
from tests.test_volume_prebuy import NOW
from v7609_trade_view import page_rows, display_value, row_key

policy = engine._EXIT_POLICY


def position(**changes):
    values = dict(entry_price=10000., quantity=10, entry_fee=15., entry_time=NOW,
                  stop_price=9870., target_price=10230., target_order_active=True)
    values.update(changes)
    return SimpleNamespace(**values)


def bars():
    return [SimpleNamespace(timestamp=NOW-timedelta(minutes=i), open=9800.,
        high=9900., low=9700., close=9850.) for i in range(40, 0, -1)]


class SupportExitTests(unittest.TestCase):
    def test_cost_target_meets_net_two_percent_and_previous_tick_does_not(self):
        p = position()
        stop, target = policy.exit_prices(p.entry_price, engine._V753.align_stock_price,
            engine._V753.stock_tick_size, entry_cost=policy.cost_per_share(p, .00015),
            commission=.00015, tax=.0018)
        self.assertGreaterEqual(policy.net_return(p, target, .00015, .0018), .02)
        self.assertLess(policy.net_return(p, target-10, .00015, .0018), .02)
        self.assertLess(abs(policy.net_return(p, stop, .00015, .0018)+.015), .00051)

    def test_left_low_frozen_even_when_later_bars_have_lower_lows(self):
        p = position()
        state, fresh = policy.support_snapshot(p, bars(), NOW)
        self.assertTrue(fresh)
        self.assertEqual(state['low'], 9700)
        later = bars()+[SimpleNamespace(timestamp=NOW, open=9850, high=9900, low=9500, close=9600)]
        next_state, _ = policy.support_snapshot(p, later, NOW+timedelta(minutes=1), state)
        self.assertEqual(next_state['low'], 9700)

    def test_missing_40_bars_never_invents_left_low(self):
        state, _ = policy.support_snapshot(position(), bars()[1:], NOW)
        self.assertNotIn('low', state)

    def test_forming_future_duplicate_bars_cannot_supply_support(self):
        raw = bars()
        self.assertEqual(policy.completed_bars(raw+[raw[0]], NOW), [])
        raw[-1].timestamp = NOW
        self.assertEqual(len(policy.completed_bars(raw, NOW)), 39)

    def test_v7610_stop_is_immediate_even_above_fresh_support(self):
        p = position(entry_fee=0)
        state = {'low': 9750, 'ma': 9700}
        self.assertFalse(policy.SUPPORT_DEFER)
        self.assertEqual(policy.stop_reason(p, 9750, 9850, 0, 0, state, True), '비용포함 기본손절-1.5%')
        self.assertEqual(policy.stop_reason(p, 9851, 9850, 0, 0, state, True), '')
        self.assertIn('저점지지', policy.stop_reason(p, 9740, 9850, 0, 0, state, True))
        self.assertIn('-3.0%', policy.stop_reason(p, 9700, 9850, 0, 0, {'low': 9500}, True))

    def test_support_equality_holds_break_sells_and_three_percent_overrides(self):
        # Previous behaviour, kept behind the switch.
        p = position(entry_fee=0)
        state = {'low': 9750}
        with patch.object(policy, 'SUPPORT_DEFER', True):
            self.assertEqual(policy.stop_reason(p, 9750, 9850, 0, 0, state, True), '')
            self.assertIn('저점지지', policy.stop_reason(p, 9740, 9850, 0, 0, state, True))
            self.assertIn('-3.0%', policy.stop_reason(p, 9700, 9850, 0, 0, {'low': 9500}, True))

    def test_stale_support_does_not_defer_default_stop(self):
        self.assertIn('기본손절', policy.stop_reason(position(entry_fee=0), 9840, 9850, 0, 0, {'low': 9700}, False))

    def test_ma_break_not_masked_by_a_lower_left_low(self):
        self.assertIn('이평밀집', policy.stop_reason(position(entry_fee=0), 9840, 9850, 0, 0,
            {'low': 9700, 'ma': 9845}, True))

    def test_inherited_stop_route_cannot_bypass_support_deferral(self):
        probe = engine_probe()
        probe.broker = SimpleNamespace(positions={'005930': position(entry_fee=0)})
        probe.client.v7580_raw_cache['005930'] = []
        probe._v7609_support_stop_reason = Mock(return_value='')
        probe._v7609_execute_fixed_stop_locked = Mock()
        self.assertFalse(probe._execute_gem_stop('005930', SimpleNamespace(price=9840),
            detected_at=NOW, source='COMPLETED_3MIN_LOW_RECOVERY', trigger_price=9500))
        probe._v7609_execute_fixed_stop_locked.assert_not_called()

    def test_hard_stop_uses_actual_quote_and_does_not_relabel_historical_low(self):
        probe = engine_probe()
        probe.broker = SimpleNamespace(positions={'005930': position(entry_fee=0)})
        probe._v7609_execute_fixed_stop_locked = Mock(return_value=True)
        self.assertTrue(probe._execute_gem_stop('005930', SimpleNamespace(price=9680),
            detected_at=NOW, source='REST_TICK', trigger_price=9400))
        args = probe._v7609_execute_fixed_stop_locked.call_args.kwargs
        self.assertEqual(args['trigger_price'], 9680)
        self.assertIn('-3.0%', args['reason'])

    def test_specific_buy_pause_and_conditional_commands(self):
        kwargs = dict(selected_symbol='005930', selected_name='삼성전자', known_names={'삼성전자':'005930'})
        self.assertEqual(engine._v7609_compile_command('삼성전자 매수해', **kwargs).action, 'IMMEDIATE_BUY')
        self.assertEqual(engine._v7609_compile_command('매수 중지', **kwargs).action, 'PAUSE_BUY')
        for text in ('삼성전자 매수하지 마', '삼성전자 조건 충족하면 매수해'):
            self.assertNotEqual(engine._v7609_compile_command(text, **kwargs).action, 'IMMEDIATE_BUY')

    def test_trade_paging_retains_all_rows_and_formats_net_values(self):
        rows = list(range(23))
        self.assertEqual(page_rows(rows, 2), ([20, 21, 22], 2, 3))
        self.assertEqual(page_rows([], 8), ([], 0, 1))
        self.assertEqual(display_value({'net_pnl_pct': -2.33}, 'net_pnl_pct'), '-2.33%')
        self.assertEqual(display_value({}, 'net_pnl'), '-')

    def test_same_symbol_sold_twice_today_gets_two_distinct_row_keys(self):
        first = {'symbol': '005930', 'sell_date': '2026-09-11', 'sell_time': '09:25:31', 'sell_cycle': 1}
        second = {**first, 'sell_time': '11:02:44', 'sell_cycle': 2}
        self.assertNotEqual(row_key('매도종목', first, 1), row_key('매도종목', second, 2))
        # A holding row stays keyed on the symbol so quote updates reuse the row.
        self.assertEqual(row_key('매수종목', first, 1), row_key('매수종목', second, 2))

    def test_stage_bar_lag_accepts_bounded_delay_and_rejects_future_bars(self):
        probe = engine_probe()
        base = NOW.replace(second=0, microsecond=0)
        def rows(last_minute):
            return [SimpleNamespace(symbol='005930', timestamp=base - timedelta(minutes=i),
                open=100., high=101., low=99., close=100., volume=1000)
                for i in range(last_minute + 4, last_minute - 1, -1)]
        lag = probe._v7609_completed_bar_lag_minutes
        self.assertEqual(lag(rows(1), base), 0)
        self.assertEqual(lag(rows(4), base), 3)
        self.assertIsNone(lag(rows(0), base))
        self.assertIsNone(lag([], base))

    def test_lagged_bar_admission_never_reaches_an_order_or_prebuy(self):
        import inspect
        source = inspect.getsource(engine.V7600ThreeStageEngine._v7609_stage_only_admission)
        for token in ('_attempt_entry', 'broker.buy', 'PREBUY', '매수직전'):
            self.assertNotIn(token, source)
        self.assertIn('_v7609_stage_only_admission',
            inspect.getsource(engine.V7600ThreeStageEngine._refresh_actual_bars))
        self.assertLessEqual(engine.V7609_STAGE_BAR_MAX_LAG_MIN, 5)
