"""PAPER-NOSTOP-001: actual method bodies, isolated from production imports/I/O."""
import ast
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 21, 10, tzinfo=KST)


def pure_module(name):
    spec = importlib.util.spec_from_file_location(name, CORE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


POLICY = pure_module('v7609_exit_policy')
SPLIT = pure_module('v7613_split_rules')


def align(price, upward=False):
    import math
    return (math.ceil(price / 10) if upward else math.floor(price / 10)) * 10


def method_class(path, name, methods, bases, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    selected = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    assert {n.name for n in selected} == set(methods)
    node = ast.ClassDef(name=name, bases=[ast.Name(id=b, ctx=ast.Load()) for b in bases],
                        keywords=[], body=selected, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
        names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace[name]


NS = dict(datetime=datetime, timedelta=timedelta, KST=KST, V7632_PAPER_AUTO_STOP=False, _EXIT_POLICY=POLICY,
          _SPLIT13=SPLIT, V7630_EOD_CLOSE=False,
          _V753=SimpleNamespace(align_stock_price=align, stock_tick_size=lambda p: 10,
                                V73Error=ValueError, brief_error=str),
          v753=SimpleNamespace(V73Error=ValueError, brief_error=str))
Parent = method_class(CORE / 'bot_v75_23_control_link_no_stop_paper.py',
    'NoStopTimedHeroEngine', ['_check_tick_exit'], [], NS)
NS['Parent'] = Parent
METHODS = ['_v7609_enforce_fixed_stop', '_v7609_exit_prices', '_v7609_cost_rates',
    '_v7609_position_exit_prices', '_v7609_support_stop_reason', '_enforce_gem_orders',
    '_execute_gem_stop', '_check_tick_exit', '_v7609_execute_fixed_stop_locked',
    '_v7613_exit_prices', '_v7609_fast_failure_exit', '_v7613_eod_close']
Engine = method_class(CORE / 'bot_v76_00_three_stage_ctrlv_paper.py',
    'V7600ThreeStageEngine', METHODS, ['Parent'], NS)


def probe(split=None):
    e = Engine()
    p = SimpleNamespace(symbol='123450', quantity=100, entry_price=10000., entry_cost=1000150.,
        entry_time=NOW, entry_fee=150., stop_price=9850., target_price=10230.,
        target_order_active=True, highest_price=10000.)
    e.broker = SimpleNamespace(positions={'123450': p}, save_state=Mock(), sell=Mock(
        return_value=SimpleNamespace(pnl=20000.)), settings=SimpleNamespace(
            commission_pct=.00015, sell_tax_pct=.0018))
    e.logger = SimpleNamespace(warning=Mock())
    e._label = str
    e._v7613_info = lambda symbol: split
    e._v7620_position_override = lambda symbol: {}
    e._v7628_open_sell = Mock(return_value=False)
    e._gem_order_announced = set()
    e._gem_exit_inflight = set()
    e._v7609_stage_lock = threading.RLock()
    e.client = SimpleNamespace(get_quote=Mock(), get_orderbook=Mock(
        side_effect=lambda symbol, quote, **kw: quote))
    e.strategy = SimpleNamespace(mark_exit=Mock())
    e.risk = SimpleNamespace(record_pnl=Mock())
    e._print_sell = Mock()
    return e, p


class NoStopTests(unittest.TestCase):
    def test_production_flag_is_disabled(self):
        tree = ast.parse((CORE / 'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
        flag = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'V7632_PAPER_AUTO_STOP' for t in n.targets))
        self.assertIs(ast.literal_eval(flag), False)

    def test_general_and_static_targets_unchanged_stop_zero(self):
        e, p = probe()
        expected = POLICY.exit_prices(p.entry_price, align, lambda p: 10,
            entry_cost=POLICY.cost_per_share(p, .00015), commission=.00015, tax=.0018)[1]
        self.assertEqual(e._v7609_position_exit_prices(p), (0., expected))
        self.assertEqual(e._v7609_exit_prices(10000), (0., 10200.))

    def test_split_and_close_bet_targets_unchanged(self):
        for tranche in (1, 2, 3):
            for close_bet in (False, True):
                info = dict(first_price=10000., tranche=tranche, close_bet=close_bet)
                e, p = probe(info)
                target = SPLIT.target_price(POLICY.cost_per_share(p, .00015), tranche,
                    .00015, .0018, align, lambda p: 10)
                self.assertEqual(e._v7609_position_exit_prices(p), (0., target))

    def test_target_override_still_applies(self):
        # Exercise the unchanged target override branch with the actual pure function.
        import sys
        sys.path.insert(0, str(CORE))
        try:
            import v7620_command_rules as commands
            NS['_CMD20'] = commands
            for split in (None, dict(first_price=10000., tranche=2)):
                e, p = probe(split)
                e._v7620_position_override = lambda symbol: dict(target_pct=.05)
                stop, target = e._v7609_position_exit_prices(p)
                self.assertEqual(stop, 0.)
                self.assertGreaterEqual(POLICY.net_return(p, target, .00015, .0018), .05)
        finally:
            sys.path.remove(str(CORE))

    def test_persisted_stop_normalized_without_changing_quantity(self):
        e, p = probe(dict(first_price=10000., tranche=2))
        e._enforce_gem_orders('123450', announce=False)
        self.assertEqual(p.stop_price, 0.)
        self.assertEqual(p.quantity, 100)
        self.assertTrue(p.target_order_active)
        self.assertFalse(e._v7609_enforce_fixed_stop('123450'))

    def test_general_split_close_bet_no_stop_even_on_extreme_gap(self):
        for info in (None, dict(first_price=10000., tranche=2),
                     dict(first_price=10000., tranche=1, close_bet=True)):
            e, p = probe(info)
            for price in (9840., 9500., 8900., 1000.):
                self.assertEqual(e._v7609_support_stop_reason('123450', price, NOW), '')
                self.assertFalse(e._execute_gem_stop('123450', SimpleNamespace(price=price),
                    detected_at=NOW, source='REST_TICK_FIXED_1P5PCT', trigger_price=price))
            e.broker.sell.assert_not_called()

    def test_stale_direct_stop_request_cannot_mutate_or_sell(self):
        e, p = probe()
        self.assertFalse(e._v7609_execute_fixed_stop_locked('123450', None,
            detected_at=NOW, source='legacy queued stop', trigger_price=9000.))
        e.broker.sell.assert_not_called()
        e.broker.save_state.assert_not_called()
        self.assertTrue(p.target_order_active)
        self.assertEqual(e._gem_exit_inflight, set())

    def test_non_stop_shared_executor_routes_still_sell(self):
        for source in ('V7628_OPEN_PROFIT', 'V7613_EOD_1519'):
            e, p = probe()
            e.broker.reentry_block_until = {}
            quote = SimpleNamespace(price=10100.)
            self.assertTrue(e._v7609_execute_fixed_stop_locked('123450', quote,
                detected_at=NOW, source=source, trigger_price=10100., reason='existing non-stop exit'))
            e.broker.sell.assert_called_once()

    def test_completed_bar_structure_stop_cannot_query_or_sell(self):
        e, p = probe()
        self.assertFalse(e._v7609_fast_failure_exit('123450', [SimpleNamespace(close=1000.)]))
        e.client.get_quote.assert_not_called()
        e.broker.sell.assert_not_called()

    def test_tick_below_old_stop_does_not_sell(self):
        e, p = probe()
        e._check_tick_exit(SimpleNamespace(symbol='123450', price=1000., best_bid=1000.),
                           detection_time=NOW)
        self.assertEqual(p.stop_price, 0.)
        e.broker.sell.assert_not_called()

    def test_tick_target_still_sells_once(self):
        e, p = probe()
        e._check_tick_exit(SimpleNamespace(symbol='123450', price=10300., best_bid=10300.),
                           detection_time=NOW)
        e.broker.sell.assert_called_once()
        self.assertEqual(e.broker.sell.call_args.kwargs['reserved_fill_price'], p.target_price)

    def test_close_bet_open_sell_route_preserved(self):
        e, p = probe(dict(first_price=10000., tranche=1, close_bet=True))
        e._v7628_open_sell.return_value = True
        e._check_tick_exit(SimpleNamespace(symbol='123450', price=9900.), detection_time=NOW)
        e._v7628_open_sell.assert_called_once()
        e.broker.save_state.assert_not_called()

    def test_general_eod_carry_rule_preserved(self):
        e, p = probe(dict(first_price=10000., tranche=2))
        self.assertFalse(e._v7613_eod_close('123450', None, NOW))
        e.broker.sell.assert_not_called()


if __name__ == '__main__':
    unittest.main()
