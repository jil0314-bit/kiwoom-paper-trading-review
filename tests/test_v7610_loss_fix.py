"""V7610 (2026-09-23 손실분석 반영) — 조기매수 중단, 시간대 차단, 눌림목 그림자 기록."""
from datetime import datetime, time, timedelta
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_state_invariants import engine, state, engine_probe
from tests.test_volume_prebuy import KST
from tests import test_early_volume_batch as early_tests
from tests import test_volume_prebuy_engine as volume_engine_tests

policy = engine._ENTRY_POLICY
pullback = engine._PULLBACK
DAY = datetime(2026, 9, 24, tzinfo=KST)


def clock_at(hour, minute):
    value = DAY.replace(hour=hour, minute=minute)

    class Clock(datetime, metaclass=volume_engine_tests.ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return value if tz is None else value.astimezone(tz)
    return Clock


def konan_signal(reason='V7609_PREBUY_20260915|대량하락음봉3배|PAPER_ONLY', pattern=None):
    return engine._V753.Signal('005930', engine._V753.SignalAction.BUY, 110.0, reason,
        10000.0, DAY.replace(hour=9, minute=30), pattern or engine.PATTERN, .015, .02)


def entry_probe():
    probe = engine_probe()
    probe.pending_entries = {'005930': 'queued'}
    probe._v7609_target_exit_at = {}
    probe._v7609_current_prebuy_approved = Mock(return_value=True)
    return probe


class EntryPolicyTests(unittest.TestCase):
    def test_window_boundaries_start_inclusive_end_exclusive(self):
        cases = {(7, 59): '', (8, 0): 'NXT', (8, 49): 'NXT', (8, 50): '', (12, 59): '',
                 (13, 0): '오후', (15, 19): '오후', (15, 20): '장후', (16, 0): '장후',
                 (19, 59): '장후', (20, 0): ''}
        for (hour, minute), expected in cases.items():
            label = policy.blocked_window(DAY.replace(hour=hour, minute=minute))
            self.assertEqual(bool(label), bool(expected), (hour, minute, label))
            self.assertIn(expected, label)
        self.assertEqual(policy.blocked_window(DAY.replace(hour=8, minute=49, second=59)),
                         'NXT프리 08:00~08:50')

    def test_defaults_match_the_analysis(self):
        self.assertFalse(policy.EARLY_VOLUME_BUY_ENABLED)
        self.assertFalse(engine._EXIT_POLICY.SUPPORT_DEFER)
        self.assertEqual(engine._EXIT_POLICY.MAX_LOSS, .03)


class FinalEntryGateTests(unittest.TestCase):
    def run_entry(self, signal, hour, minute):
        probe = entry_probe()
        with patch.object(engine, 'datetime', clock_at(hour, minute)), \
             patch.object(engine._V7588, '_attempt_entry', Mock(return_value=False)) as parent:
            result = probe._v7609_attempt_entry_inner(signal)
        return probe, parent, result

    def test_automatic_entry_blocked_in_afternoon_and_nxt_pre(self):
        for hour, minute in ((13, 30), (8, 10)):
            probe, parent, result = self.run_entry(konan_signal(), hour, minute)
            self.assertFalse(result)
            parent.assert_not_called()
            self.assertNotIn('005930', probe.pending_entries)

    def test_automatic_entry_reaches_parent_in_allowed_window(self):
        for hour, minute in ((10, 30), (15, 20), (16, 30)):
            _probe, parent, _result = self.run_entry(konan_signal(), hour, minute)
            parent.assert_called_once()

    def test_manual_token_reason_bypasses_window_at_final_gate(self):
        # Token logic only; the real command paths below never reach this gate.
        _probe, parent, _result = self.run_entry(konan_signal(reason='수동매수 형명령'), 13, 30)
        parent.assert_called_once()

    def test_real_quantity_command_buys_in_blocked_window_without_final_gate(self):
        probe = engine_probe()
        probe._v7609_attempt_entry_inner = Mock(side_effect=AssertionError('manual must not use auto gate'))
        probe._new_entry_session_allowed = Mock(return_value=True)
        probe.buy_blocked, probe.scale_blocked, probe.manual_watch = set(), set(), {}
        probe._market_entry_allowed = Mock(return_value=(True, ''))
        probe._manual_add_watch = Mock()
        probe._fresh_enriched_quote = Mock(return_value=SimpleNamespace(price=10000.0, best_ask=10000.0))
        probe.settings = SimpleNamespace(slippage_pct=0.0, commission_pct=0.0)
        positions = {}

        def buy(symbol, quantity, quote, reason, **kwargs):
            positions[symbol] = SimpleNamespace(quantity=quantity, entry_price=10000.0)
            return SimpleNamespace(timestamp=DAY.replace(hour=13, minute=30), quantity=quantity, price=10000.0)
        probe.broker = SimpleNamespace(positions=positions, cash=10_000_000.0, buy=Mock(side_effect=buy))
        probe.strategy = SimpleNamespace(mark_entry=Mock())
        probe.last_position_observation_at = {}
        probe._enforce_gem_orders = Mock()
        probe._v7609_enforce_fixed_stop = Mock(return_value=False)
        probe._print_buy = Mock()
        with patch.object(engine, 'datetime', clock_at(13, 30)):
            result = probe._v7609_manual_buy_exact_quantity('005930', 10)
        probe.broker.buy.assert_called_once()
        probe._v7609_attempt_entry_inner.assert_not_called()
        self.assertIn('10주', result)

    def test_every_manual_buy_override_buys_directly_not_through_auto_entry(self):
        overrides = [vars(cls)['_manual_buy'] for cls in engine.V7600ThreeStageEngine.__mro__
                     if '_manual_buy' in vars(cls)]
        files = [Path(inspect.getsourcefile(method)).name for method in overrides]
        self.assertIn('bot_v75_17_intraday_command_paper.py', files)
        for method, name in zip(overrides, files):
            self.assertNotIn('_attempt_entry(', inspect.getsource(method), name)
        final = overrides[files.index('bot_v75_17_intraday_command_paper.py')]
        self.assertIn('self.broker.buy(', inspect.getsource(final))

    def test_early_pattern_blocked_even_in_allowed_window(self):
        signal = konan_signal(reason=engine._VOLUME5.EARLY_REASON, pattern=engine.EARLY_PATTERN)
        probe, parent, result = self.run_entry(signal, 10, 30)
        self.assertFalse(result)
        parent.assert_not_called()
        self.assertNotIn('005930', probe.pending_entries)

    def test_scale_in_blocked_in_window_only(self):
        for hour, expect_add in ((13, False), (10, True)):
            probe = entry_probe()
            now = DAY.replace(hour=hour, minute=30)
            probe.broker = SimpleNamespace(positions={'005930': SimpleNamespace(
                pattern=engine.PATTERN, entry_time=now - timedelta(minutes=5))})
            probe._v76_tranche = {'005930': 1}
            probe._v76_order_bar = {}
            probe._add_one_third = Mock(return_value=True)
            bar = SimpleNamespace(timestamp=now - timedelta(minutes=1))
            with patch.object(engine, 'pattern_bars', return_value=[bar]), \
                 patch.object(engine, 'konan_second_half_decision',
                              return_value=SimpleNamespace(allowed=True, reason='unit')):
                probe._scale_v76_from_raw('005930', [bar], now)
            self.assertEqual(probe._add_one_third.called, expect_add, hour)

    def test_konan_source_checks_window_before_building_order(self):
        source = inspect.getsource(engine.V7600ThreeStageEngine._v7609_requested_prebuy)
        self.assertLess(source.index('_ENTRY_POLICY.blocked_window'), source.index('_V753.Signal('))


class ScaleInWindowTests(unittest.TestCase):
    def add_probe(self):
        probe = entry_probe()
        probe._market_gate_v76 = Mock(return_value=(True, ''))
        probe.risk = SimpleNamespace(halted=False, halt_reason='')
        quote = SimpleNamespace(price=10000.0, best_ask=10000.0)
        probe.client.get_quote = Mock(return_value=quote)
        probe.client.get_orderbook = Mock(return_value=quote)
        probe.latest_quotes = {}
        probe._scale_price_allowed = Mock(return_value=(True, 'ok'))
        probe.settings = SimpleNamespace(slippage_pct=0.0)
        probe._v7609_run_order_intent = Mock(return_value=None)
        return probe

    def test_second_half_rechecks_window_after_quote_io(self):
        # The earlier check can pass at 12:59:59; the order must still stop at 13:00.
        for hour, minute, expect_order in ((13, 0, False), (12, 30, True)):
            probe = self.add_probe()
            position = SimpleNamespace(entry_price=10000.0, quantity=100)
            with patch.object(engine, 'datetime', clock_at(hour, minute)):
                probe._add_one_third('005930', position, 'unit', 2)
            self.assertEqual(probe._v7609_run_order_intent.called, expect_order, (hour, minute))

    def test_inherited_scale_in_blocked_for_every_pattern(self):
        for hour, expect_parent in ((13, False), (8, False), (10, True)):
            probe = entry_probe()
            probe.broker = SimpleNamespace(positions={'005930': SimpleNamespace(
                pattern=engine.EARLY_PATTERN)})
            with patch.object(engine, 'datetime', clock_at(hour, 30 if hour != 8 else 10)), \
                 patch.object(engine._PARENT_ENGINE, '_attempt_scale_in',
                              Mock(return_value=False)) as parent:
                probe._attempt_scale_in(SimpleNamespace(symbol='005930'))
            self.assertEqual(parent.called, expect_parent, hour)


class EarlyPrebuyIsNotAnEntryPermitTests(unittest.TestCase):
    """GPT review 2026-09-25 item 1: the cited breakout/bottom order branches are unreachable,
    and the live route must still prove its own evidence over an early PREBUY."""

    def test_legacy_order_branches_after_requested_prebuy_stay_unreachable(self):
        source = inspect.getsource(engine.V7600ThreeStageEngine._refresh_actual_bars)
        call = 'self._v7609_requested_prebuy(symbol, raw, now, quote)'
        lines = [line.strip() for line in source.splitlines()]
        self.assertEqual(lines[lines.index(call) + 1], 'return')
        tail = source[source.index(call):]
        for branch in ('konan_breakout_pullback_decision', 'V7609_BOTTOM_MA5|'):
            self.assertIn(branch, tail)

    def test_konan_prebuy_over_early_prebuy_needs_its_own_evidence(self):
        evidence = early_tests.early()
        now = early_tests.NOW.replace(second=40)
        manager = state.StateManager()
        manager.apply(state.TransitionRequest(
            'early-id', '005930', state.PREBUY, engine._VOLUME5.EARLY_RULE_ID,
            evidence['data_time'], now.isoformat(), engine.VERSION,
            engine._VOLUME5.EARLY_REASON, evidence))
        forged = state.TransitionRequest(
            'konan-id', '005930', state.PREBUY, engine._PREBUY_RULES.RULE_ID,
            evidence['data_time'], now.isoformat(), engine.VERSION, 'forged',
            {'route': 'BREAKOUT', 'buy': True})
        with self.assertRaises(state.TransitionRejected):
            manager.apply(forged)
        self.assertEqual(manager.events[-1].rule_id, engine._VOLUME5.EARLY_RULE_ID)


class EarlyOrderWithheldTests(unittest.TestCase):
    def setUp(self):
        early_tests.EarlyEngineTests.setUp(self)

    def test_default_keeps_prebuy_stage_but_places_no_order(self):
        rows, now = early_tests.partial_fixture()
        with patch.object(engine, 'datetime', wraps=engine.datetime) as clock:
            clock.now.return_value = now
            self.p._v7609_try_early_volume_buy('005930', rows, now)
        event = self.p._v7609_stage_manager.events[-1]
        self.assertEqual(event.rule_id, engine._VOLUME5.EARLY_RULE_ID)
        self.assertEqual(self.p._v7609_stage_manager.stage_of('005930'), state.PREBUY)
        self.p._attempt_entry.assert_not_called()


def pullback_rows(*, pullback_volume=(150, 120, 100, 90), deep_low=None,
                  trigger_close=103.2, impulse_volume=500, trigger_volume=300):
    start = DAY.replace(hour=9, minute=30)
    rows = []

    def add(open_, high, low, close, volume):
        stamp = start + timedelta(minutes=len(rows))
        rows.append(dict(timestamp=stamp.isoformat(), open=open_, high=high, low=low,
                         close=close, volume=volume))

    for i in range(21):                               # gently falling base
        close = round(101.1 - 0.1 * i, 2)
        add(close + 0.05, close + 0.1, close - 0.1, close, 100)
    previous = rows[-1]['close']
    for close in (100.8, 101.6, 102.4, 103.2, 104.0):  # volume-ignited impulse
        add(previous, close + 0.1, previous - 0.1, close, impulse_volume)
        previous = close
    lows = [103.3, 102.8, deep_low or 102.5, 102.55]
    for (open_, close), low, volume in zip(((104.0, 103.4), (103.4, 102.9), (102.9, 102.6),
                                           (102.6, 102.7)), lows, pullback_volume):
        add(open_, max(open_, close) + 0.05, low, close, volume)
    add(102.7, trigger_close + 0.1, 102.65, trigger_close, trigger_volume)
    return rows


class PullbackRuleTests(unittest.TestCase):
    def test_reclaim_after_dry_pullback_fires_with_stop_below_pullback_low(self):
        signal = pullback.detect(pullback_rows())
        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal['stop'], 102.5 * (1 - pullback.STOP_BUFFER))
        self.assertAlmostEqual(signal['peak_high'], 104.1)
        self.assertEqual(signal['pullback_bars'], 4)
        self.assertTrue(pullback.RETRACE_MIN <= signal['retrace'] <= pullback.RETRACE_MAX)

    def test_each_missing_condition_rejects(self):
        cases = {
            'no volume dry-up': pullback_rows(pullback_volume=(400, 400, 400, 400),
                                              trigger_volume=500),
            'retrace too deep': pullback_rows(deep_low=100.5),
            'new-high chase': pullback_rows(trigger_close=104.2),
            'no volume ignition': pullback_rows(impulse_volume=200),
            'too few bars': pullback_rows()[1:],
        }
        for name, rows in cases.items():
            self.assertIsNone(pullback.detect(rows), name)

    def test_bearish_or_non_reclaiming_trigger_rejects(self):
        rows = pullback_rows()
        rows[-1] = dict(rows[-1], close=102.7, open=102.75, high=102.8)
        self.assertIsNone(pullback.detect(rows))


class ShadowRecorderTests(unittest.TestCase):
    def test_records_once_per_peak_and_never_orders(self):
        probe = engine_probe()
        probe._attempt_entry = Mock(side_effect=AssertionError('shadow cannot order'))
        bars =[SimpleNamespace(**dict(r, timestamp=datetime.fromisoformat(r['timestamp'])))
                for r in pullback_rows()]
        now = bars[-1].timestamp + timedelta(minutes=1, seconds=5)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(engine, 'PULLBACK_SHADOW_DIR', Path(folder)):
                probe._v7610_record_pullback_shadow('005930', bars, now)
                probe._v7610_record_pullback_shadow('005930', bars, now)
                probe._v7610_pullback_checked.clear()
                probe._v7610_record_pullback_shadow('005930', bars, now)
                files = list(Path(folder).glob('pullback_shadow_*.jsonl'))
                self.assertEqual(len(files), 1)
                lines = files[0].read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record['order'], 'NONE_SHADOW_ONLY')
        self.assertEqual(record['symbol'], '005930')
        self.assertEqual(record['rule_id'], pullback.RULE_ID)
        probe._attempt_entry.assert_not_called()

    def test_failed_write_is_retried_on_next_poll(self):
        probe = engine_probe()
        bars = [SimpleNamespace(**dict(r, timestamp=datetime.fromisoformat(r['timestamp'])))
                for r in pullback_rows()]
        now = bars[-1].timestamp + timedelta(minutes=1, seconds=5)
        with tempfile.TemporaryDirectory() as folder:
            blocker = Path(folder) / 'not_a_folder'
            blocker.write_text('x', encoding='utf-8')
            with patch.object(engine, 'PULLBACK_SHADOW_DIR', blocker / 'shadow'):
                with self.assertRaises(OSError):
                    probe._v7610_record_pullback_shadow('005930', bars, now)
            good = Path(folder) / 'shadow'
            with patch.object(engine, 'PULLBACK_SHADOW_DIR', good):
                probe._v7610_record_pullback_shadow('005930', bars, now)
                lines = next(good.glob('pullback_shadow_*.jsonl')).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)

    def test_poll_calls_recorder_and_recorder_has_no_order_path(self):
        poll = inspect.getsource(engine.V7600ThreeStageEngine._v7609_poll_volume)
        self.assertIn('_v7610_record_pullback_shadow', poll)
        recorder = inspect.getsource(engine.V7600ThreeStageEngine._v7610_record_pullback_shadow)
        for token in ('_attempt_entry', 'broker', 'manager.apply', '_V7600_STATE.PREBUY'):
            self.assertNotIn(token, recorder)


if __name__ == '__main__':
    unittest.main()
