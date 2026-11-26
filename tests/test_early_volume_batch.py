"""Synthetic safety regressions. Run only in the existing isolated runner."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_state_invariants import engine, state, engine_probe
from tests.test_volume_prebuy import NOW, bar
from tests import test_volume_prebuy_engine as volume_engine_tests
from v7609_account_guard import allowed_account, selected_account_matches

rule = engine._VOLUME5


class PartialClock(datetime, metaclass=volume_engine_tests.ClockMeta):
    @classmethod
    def now(cls, tz=None):
        value = NOW.replace(second=40)
        return value if tz is None else value.astimezone(tz)


def partial_fixture(second=40, volume=400):
    now = NOW.replace(second=second)
    rows = [bar(NOW-timedelta(minutes=i)) for i in range(40, 0, -1)]
    rows.append(dict(timestamp=NOW.isoformat(), open=100, high=103, low=99, close=102, volume=volume))
    return rows, now


def early(rows=None, now=None):
    default, clock = partial_fixture()
    return rule.evaluate_early(default if rows is None else rows, now=now or clock,
        previous_day=(NOW-timedelta(days=1)).date(), previous_close=100)


class EarlyPureTests(unittest.TestCase):
    def test_elapsed_threshold_boundaries(self):
        for second in (25, 30, 40, 50, 59):
            volume = math.ceil(500*second/60)
            rows, now = partial_fixture(second, volume)
            self.assertIsNotNone(early(rows, now))
            rows[-1]['volume'] = volume-1
            self.assertIsNone(early(rows, now))

    def test_24_seconds_and_completed_rejected(self):
        rows, now = partial_fixture(24, 10000)
        self.assertIsNone(early(rows, now))
        self.assertIsNone(early(rows, NOW+timedelta(minutes=1)))

    def test_40_seconds_threefold_is_not_fivefold(self):
        rows, now = partial_fixture(40, 300)
        self.assertIsNone(early(rows, now))

    def test_bear_and_doji_rejected(self):
        for close in (99, 100):
            rows, now = partial_fixture(); rows[-1]['close'] = close
            self.assertIsNone(early(rows, now))

    def test_previous_close_must_be_exceeded(self):
        rows, now = partial_fixture()
        self.assertIsNone(rule.evaluate_early(rows, now=now, previous_day=NOW.date()-timedelta(days=1), previous_close=102))

    def test_partial_label_and_measured_vs_projected(self):
        e = early()
        self.assertFalse(e['completed'])
        self.assertEqual(e['actual_volume_multiple'], 4)
        self.assertEqual(e['volume_multiple'], 6)
        self.assertTrue(rule.validate_evidence(e, e['data_time'], NOW.replace(second=40).isoformat()))

    def test_missing_and_duplicate_fail(self):
        rows, now = partial_fixture()
        self.assertIsNone(early(rows[1:], now))
        self.assertIsNone(early(rows+[rows[0]], now))

    def test_forged_evidence_rejected_by_state_manager(self):
        e = early(); now = NOW.replace(second=40)
        req = state.TransitionRequest('early-id', '005930', state.PREBUY, rule.EARLY_RULE_ID,
            e['data_time'], now.isoformat(), engine.VERSION, rule.EARLY_REASON, e)
        manager = state.StateManager(); event = manager.apply(req)
        self.assertEqual(event.new_stage, state.PREBUY)
        with self.assertRaises(state.TransitionRejected): manager.apply(req)
        e['trigger_bar']['close'] = e['trigger_bar']['open']
        with self.assertRaises(state.TransitionRejected): state.StateManager().apply(req)

    def test_opening_uses_previous_session_not_today(self):
        now = NOW.replace(hour=9, minute=0, second=40)
        prev = NOW.replace(day=8, hour=15, minute=29)
        rows = [bar(prev-timedelta(minutes=i)) for i in range(39, -1, -1)]
        rows.append(dict(timestamp=now.replace(second=0).isoformat(), open=100, high=103, low=99, close=102, volume=400))
        self.assertEqual(early(rows, now)['reference_kind'], 'PREVIOUS_SESSION_LAST40')


class AccountTests(unittest.TestCase):
    def test_experiment_uses_real_one_minute_parser_without_shared_cache_write(self):
        client = object.__new__(engine.V7609OneMinuteKiwoomClient)
        client.settings = SimpleNamespace(suffix='')
        client.v7580_raw_cache = {'005930': ['unchanged']}
        client.post = Mock(return_value={'stk_min_pole_chart_qry': [dict(
            cntr_tm=NOW.strftime('%Y%m%d%H%M%S'), open_pric='100',
            high_pric='103', low_pric='99', cur_prc='102', trde_qty='400')]})
        rows = client.get_experiment_bars('005930')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].timestamp, NOW)
        self.assertEqual(rows[0].volume, 400)
        self.assertEqual(client.post.call_args.kwargs['api_id'], 'ka10080')
        self.assertEqual(client.post.call_args.kwargs['body']['tic_scope'], '1')
        self.assertEqual(client.v7580_raw_cache, {'005930': ['unchanged']})

    def test_account_suffix_is_basic8_not_product_suffix(self):
        self.assertTrue(allowed_account('1234031111'))
        self.assertFalse(allowed_account('1234560311'))
        self.assertFalse(allowed_account('0311'))
        self.assertFalse(allowed_account('****031111'))

    def test_selected_account_identity_not_just_suffix(self):
        self.assertTrue(selected_account_matches(['1234-0311'], '1234031111'))
        self.assertFalse(selected_account_matches(['9999-0311'], '1234031111'))
        self.assertFalse(selected_account_matches(['1234-0311', '9876-1234'], '1234031111'))
        self.assertFalse(selected_account_matches(['****-0311'], '1234031111'))
        self.assertFalse(selected_account_matches([], '1234031111'))

    def test_auth_checks_actual_response_and_never_caches_identity_on_disk(self):
        client = object.__new__(engine.V7609OneMinuteKiwoomClient)
        client.settings = SimpleNamespace(base_url='https://api.kiwoom.com')
        client._json_post = Mock(return_value=({'return_code':0, 'acctNo':'1234031111'}, 200))
        self.addCleanup(setattr, engine, '_V7609_VERIFIED_ACCOUNT', '')
        with patch.object(engine._V7609_INHERITED_CLIENT, 'authenticate', return_value='unit-token'):
            self.assertEqual(client.authenticate(), 'unit-token')
            self.assertEqual(client.authenticate(), 'unit-token')
        client._json_post.assert_called_once()
        with patch.object(engine._V7609_INHERITED_CLIENT, '_save_token') as save:
            client._save_token('unit-token', '20990101000000'); save.assert_not_called()

    def test_wrong_account_and_unknown_fail_closed(self):
        for account in ('1234560311', '', 'not-account'):
            client = object.__new__(engine.V7609OneMinuteKiwoomClient)
            client.settings = SimpleNamespace(base_url='https://api.kiwoom.com')
            client._json_post = Mock(return_value=({'return_code':0, 'acctNo':account}, 200))
            with patch.object(engine._V7609_INHERITED_CLIENT, 'authenticate', return_value='unit-token'):
                with self.assertRaises(engine._V753.AuthenticationError): client.authenticate()

    def test_failed_reauthentication_clears_previous_account_proof(self):
        client = object.__new__(engine.V7609OneMinuteKiwoomClient)
        client._v7609_verified_token = 'old-test-token'
        with patch.object(engine, '_V7609_VERIFIED_ACCOUNT', '1234031111'), \
             patch.object(engine._V7609_INHERITED_CLIENT, 'authenticate', side_effect=ValueError('offline')):
            with self.assertRaises(ValueError):
                client.authenticate()
            self.assertEqual(engine._V7609_VERIFIED_ACCOUNT, '')
            self.assertEqual(client._v7609_verified_token, '')

    def test_live_order_endpoints_are_blocked_before_transport(self):
        client = object.__new__(engine.V7609OneMinuteKiwoomClient)
        with patch.object(engine._V7609_INHERITED_CLIENT, '_json_post') as transport:
            with self.assertRaises(engine._V753.AuthenticationError):
                client._json_post('https://api.kiwoom.com/api/dostk/ordr', {}, {'api-id':'kt10000'})
            transport.assert_not_called()

    def test_0600_no_account_no_send_even_with_cached_handle(self):
        link = object.__new__(engine.V7600CtrlVHeroLink)
        link._v7609_account_matches_chart = Mock(return_value=False)
        link._send_message = Mock()
        status, _ = link._fast_exact_edit_send(1, 2, 3, '005930')
        self.assertEqual(status, engine.v57.LinkStatus.FAILED)
        link._send_message.assert_not_called()


class EarlyEngineTests(unittest.TestCase):
    def setUp(self):
        volume_engine_tests.VolumeEngineTests.setUp(self)
        self.p._attempt_entry = Mock(return_value=False)

    def test_actual_partial_route_records_separate_evidence_and_requests_buy(self):
        # V7610 disables this order by default; the switch-on path must still work.
        rows, now = partial_fixture()
        with patch.object(engine, 'datetime', wraps=engine.datetime) as clock, \
             patch.object(engine._ENTRY_POLICY, 'EARLY_VOLUME_BUY_ENABLED', True):
            clock.now.return_value = now
            self.p._v7609_try_early_volume_buy('005930', rows, now)
        event = self.p._v7609_stage_manager.events[-1]
        self.assertEqual(event.rule_id, rule.EARLY_RULE_ID)
        self.assertFalse(event.evidence['completed'])
        signal = self.p._attempt_entry.call_args.args[0]
        self.assertEqual(signal.pattern, engine.EARLY_PATTERN)
        self.assertEqual(signal.stop_loss_pct, .015)
        self.assertEqual(signal.take_profit_pct, .02)

    def test_bear_route_never_requests_order(self):
        rows, now = partial_fixture(); rows[-1]['close'] = 99
        self.p._v7609_try_early_volume_buy('005930', rows, now)
        self.p._attempt_entry.assert_not_called()

    def test_order_revalidation_rejects_turning_bear_or_late(self):
        rows, now = partial_fixture(); e = early(rows, now); e['decision_time'] = now.isoformat()
        self.p._v7609_early_proofs = {'005930': e}
        self.assertTrue(self.p._v7609_early_order_allowed('005930', 102, now))
        self.assertFalse(self.p._v7609_early_order_allowed('005930', 100, now))
        self.assertFalse(self.p._v7609_early_order_allowed('005930', 102, NOW+timedelta(minutes=1)))

    def test_restored_partial_prebuy_does_not_place_order(self):
        rows, now = partial_fixture()
        with patch.object(engine, 'datetime', wraps=engine.datetime) as clock:
            clock.now.return_value = now
            self.p._v7609_try_early_volume_buy('005930', rows, now)
        events = [asdict(e) for e in self.p._v7609_stage_manager.events]
        self.p._v7609_stage_manager = state.StateManager()
        self.p._v7609_stage_manager.restore(events, session_date=now.date().isoformat(), engine_version=engine.VERSION)
        self.p._attempt_entry.reset_mock()
        self.p._supply_potential.clear(); self.p._v76_strict_prebuy.clear()
        self.p._v7609_restore_today_scanner_state(now)
        self.assertIn('005930', self.p._supply_potential)
        self.p._attempt_entry.assert_not_called()

    def test_service_checks_volume_before_legacy_bootstrap_gate(self):
        self.p._v7609_poll_volume = Mock()
        with patch.object(engine._PARENT_ENGINE, '_service_symbol') as parent:
            self.p._service_symbol('005930', hot=False)
        self.p._v7609_poll_volume.assert_called_once_with('005930')
        parent.assert_called_once_with('005930', hot=False)

    def test_poll_filters_partial_out_of_legacy_cache(self):
        rows, now = partial_fixture()
        raw = [SimpleNamespace(**dict(r, timestamp=datetime.fromisoformat(r['timestamp']))) for r in rows]
        self.p.client.get_experiment_bars = Mock(return_value=raw)
        self.p._v7609_try_volume_prebuy = Mock(return_value=now)
        self.p._v7609_try_early_volume_buy = Mock()
        with patch.object(engine, 'datetime', PartialClock):
            self.p._v7609_poll_volume('005930')
        self.assertEqual(len(self.p.client.v7580_raw_cache['005930']), 40)
        self.assertEqual(len(self.p._v7609_try_early_volume_buy.call_args.args[1]), 41)

    def test_fixed_stop_uses_nearest_tick_not_always_upward(self):
        p = self.p
        p.broker.positions['005930'] = SimpleNamespace(entry_price=2535, stop_price=2400)
        self.assertTrue(p._v7609_enforce_fixed_stop('005930'))
        # Approved 1.5% stop: 2496.975 is nearer 2495 than 2500.
        self.assertEqual(p.broker.positions['005930'].stop_price, 2495)

    def test_stop_gets_priority_and_no_target_path_after_stop(self):
        p = self.p
        p.broker.positions['005930'] = SimpleNamespace(stop_price=98)
        p._enforce_gem_orders = Mock()
        p._execute_gem_stop = Mock(return_value=True)
        with patch.object(engine._PARENT_ENGINE, '_check_tick_exit') as parent:
            p._check_tick_exit(SimpleNamespace(symbol='005930', price=97))
        p._execute_gem_stop.assert_called_once()
        parent.assert_not_called()

    def test_actual_paper_fill_registers_target_and_consumes_one_minute_intent(self):
        p = self.p
        broker = object.__new__(engine._V753.PaperBroker)
        broker.settings = SimpleNamespace(slippage_pct=0, commission_pct=0,
            paper_unlimited=False, stop_loss_pct=.02, take_profit_pct=.015)
        broker.positions = {}; broker.cash = 10000000; broker.sequence = 0
        broker.logger = p.logger
        broker._record_fill = Mock(); recorded = broker._record_fill
        broker.save_state = Mock()
        p.broker = broker
        p._v7609_load_paper_fill_identity = Mock()
        p._v7609_seen_paper_fill_ids = set(); p._v7609_latest_buy_fill = {}
        p._v7609_target_exit_at = {}; p._gem_order_announced = set()
        p._v7609_install_paper_fill_state_bridge()
        p._v7609_install_order_idempotency_bridge()
        rows, now = partial_fixture()
        with patch.object(engine, 'datetime', PartialClock), \
             patch.object(engine._ENTRY_POLICY, 'EARLY_VOLUME_BUY_ENABLED', True):
            p._v7609_try_early_volume_buy('005930', rows, now)
            signal = p._attempt_entry.call_args.args[0]
            del p._attempt_entry
            quote = SimpleNamespace(symbol='005930', price=102, best_ask=102,
                best_bid=101, timestamp=now)
            def approved_entry(_self, sig, **kwargs):
                broker.buy(sig.symbol, 10, quote, sig.reason, pattern=sig.pattern,
                    stop_loss_pct=sig.stop_loss_pct, take_profit_pct=sig.take_profit_pct)
                return True
            # Isolate upstream approval, exercise the actual final PAPER/fill/IDEM path.
            with patch.object(engine._V7588, '_attempt_entry', approved_entry):
                self.assertTrue(p._attempt_entry(signal))
                self.assertFalse(p._attempt_entry(engine.replace(signal, reference_price=103)))
        recorded.assert_called_once()
        position = broker.positions['005930']
        self.assertEqual(position.quantity, 10)
        self.assertTrue(position.target_order_active)
        self.assertEqual(position.target_price, engine._V753.align_stock_price(102*1.02, upward=True))
        self.assertEqual(position.stop_price, engine.V7600ThreeStageEngine._v7609_exit_prices(102)[0])
        self.assertEqual(p._v7609_stage_manager.stage_of('005930'), state.HOLDING)

    def test_holding_snapshot_shows_actual_average_and_quote_relative_return(self):
        p = self.p
        p._v7609_roll_session = Mock()
        p._v7609_postclose_review_rows = Mock(return_value=None)
        p._v7609_fill_records = Mock(return_value={})
        p._v7591_timeline = {}; p._r21_stage_times = {}
        p.broker.positions['005930'] = SimpleNamespace(entry_price=100,
            entry_time=NOW, quantity=10, stop_price=98, target_price=102)
        p.latest_quotes = {'005930': SimpleNamespace(price=103)}
        rows = {'rows': [dict(stage='매수종목', symbol='005930', name='삼성전자',
            reason='', current_price=999, change_pct=-50)]}
        with patch.object(engine, 'datetime', PartialClock), \
             patch.object(engine._PARENT_ENGINE, '_gui_snapshot', return_value=rows):
            result = p._gui_snapshot()['rows'][0]
        self.assertEqual(result['avg_price'], 100)
        self.assertEqual(result['current_price'], 103)
        self.assertAlmostEqual(result['change_pct'], 3)
        self.assertEqual(result['quantity'], 10)


class ExitTickTests(unittest.TestCase):
    def test_exact_one_point_five_percent_stop_is_not_shifted(self):
        self.assertEqual(engine.V7600ThreeStageEngine._v7609_exit_prices(10000)[0], 9850)

    def test_equal_distance_stop_prefers_smaller_loss(self):
        self.assertEqual(engine.V7600ThreeStageEngine._v7609_exit_prices(2500)[0], 2465)

    def test_target_exactly_two_percent_does_not_add_an_extra_tick(self):
        self.assertEqual(engine.V7600ThreeStageEngine._v7609_exit_prices(10000)[1], 10200)

    def test_target_uses_nearest_tick_at_or_above_two_percent(self):
        self.assertEqual(engine.V7600ThreeStageEngine._v7609_exit_prices(2675)[1], 2730)

    def test_price_ladder_boundaries_and_fractional_average(self):
        from decimal import Decimal
        for entry in (1000, 1969, 1970, 1999, 2000, 4995, 5000, 19700,
                      20000, 49950, 50000, 199900, 200000, 499500, 500000,
                      1000000, 2533.333333):
            with self.subTest(entry=entry):
                stop, target = engine.V7600ThreeStageEngine._v7609_exit_prices(entry)
                self.assertTrue(engine._V753.is_tick_aligned(stop))
                self.assertTrue(engine._V753.is_tick_aligned(target))
                self.assertGreaterEqual(Decimal(str(target)), Decimal(str(entry))*Decimal('1.02'))
                self.assertGreaterEqual((entry-stop)/entry, .014-1e-10)
                self.assertLessEqual((entry-stop)/entry, .016+1e-10)
                self.assertLessEqual((target-entry)/entry, .024+1e-10)

    def test_unrepresentable_low_price_does_not_invent_fractional_tick(self):
        self.assertEqual(engine.V7600ThreeStageEngine._v7609_exit_prices(10), (10, 11))

    def test_bad_entry_fails_without_order(self):
        for entry in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                engine.V7600ThreeStageEngine._v7609_exit_prices(entry)

    def test_structure_failure_uses_same_stop_calculation(self):
        p = engine_probe()
        p.broker = SimpleNamespace(positions={'005930': SimpleNamespace(entry_price=2535)})
        p._v76_fast_fail = {'005930': {'entry_bar': NOW-timedelta(minutes=10), 'support': 2600}}
        rows = [SimpleNamespace(timestamp=NOW-timedelta(minutes=i), close=2500)
                for i in range(4, -1, -1)]
        with patch.object(p, '_v7609_position_exit_prices', wraps=p._v7609_position_exit_prices) as prices:
            self.assertFalse(p._v7609_fast_failure_exit('005930', rows))
            prices.assert_called_once_with(p.broker.positions['005930'])

    def test_order_registration_overwrites_old_prices_once_and_keeps_quantity(self):
        p = engine_probe()
        p.broker = SimpleNamespace(positions={}, save_state=Mock())
        p._gem_order_announced = set()
        p.broker.positions['005930'] = SimpleNamespace(entry_price=10000,
            stop_price=9500, target_price=10150, target_order_active=True, quantity=7)
        p._enforce_gem_orders('005930', announce=False)
        position = p.broker.positions['005930']
        self.assertEqual((position.stop_price, position.target_price, position.quantity), (9850, 10200, 7))
        self.assertTrue(position.target_order_active)
        p._enforce_gem_orders('005930', announce=False)
        p.broker.save_state.assert_called_once()


if __name__ == '__main__':
    unittest.main()
