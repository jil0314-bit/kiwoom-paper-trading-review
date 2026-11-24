"""Actual PAPER persistence/sell methods with temporary files and a fixed clock.

No production imports, broker connection, GUI, or runtime writes.
"""
from __future__ import annotations
import ast
import csv
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import tempfile
from types import FunctionType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'
KST = timezone(timedelta(hours=9))


class Clock(datetime):
    current = datetime(2026, 11, 24, 10, tzinfo=KST)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def compile_nodes(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    nodes = [n for n in tree.body if getattr(n, 'name', None) in names]
    assert {n.name for n in nodes} == set(names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future] + nodes, type_ignores=[])),
                 str(path), 'exec'), namespace)


def broker_module(folder):
    m = ModuleType('_carry_test_broker')
    sys.modules[m.__name__] = m
    m.__dict__.update(datetime=Clock, KST=KST, timedelta=timedelta, math=math,
                     json=json, csv=csv, deque=deque, dataclass=dataclass,
                     env_int=lambda key, default: default, money=str,
                     ConfigurationError=RuntimeError, VERSION='test',
                     LOG_DIR=folder, STATE_FILE=folder/'state.json',
                     FILL_FILE=folder/'fills.csv', PATTERN_STATE_FILE=folder/'patterns.json')
    compile_nodes(CORE/'bot_v75_3_after_session_guard.py',
                  ['stock_tick_size', 'align_stock_price', 'Position', 'Fill', 'PaperBroker'], m.__dict__)
    return m


def install(m):
    ns = dict(_V753=m, FunctionType=FunctionType)
    compile_nodes(CORE/'bot_v76_00_three_stage_ctrlv_paper.py',
                  ['_v7633_install_paper_restore'], ns)
    ns['_v7633_install_paper_restore']()


def position(stop=0.):
    return dict(quantity=17, entry_price=10000., entry_time='2026-11-23T10:00:00+09:00',
                highest_price=10100., stop_price=stop, target_price=10300.,
                entry_fee=25.5, pattern='synthetic', target_order_active=True)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.m = broker_module(self.folder)
        self.settings = SimpleNamespace(initial_cash=1000000., commission_pct=.00015,
                                        sell_tax_pct=.0018, slippage_pct=0.)
        Clock.current = datetime(2026, 11, 24, 10, tzinfo=KST)
        self.payload = dict(session_date='2026-11-23', cash=829974.5, realized_pnl=100.,
                            daily_realized_pnl=100., sequence=7, last_equity=1000000.,
                            day_start_equity=1000000., positions={'111111': position()})
        self.write(self.payload)

    def write(self, payload):
        self.m.STATE_FILE.write_text(json.dumps(payload), encoding='utf-8')

    def broker(self):
        return self.m.PaperBroker(self.settings, Mock())

    def test_original_bug_reproduced_then_fixed(self):
        original = self.broker()
        self.assertTrue(original.state_load_failed)
        self.assertEqual(original.positions, {})
        install(self.m)
        fixed = self.broker()
        self.assertFalse(fixed.state_load_failed)
        self.assertEqual(fixed.positions['111111'].stop_price, 0.)

    def test_multiple_days_restarts_and_weekend_keep_exact_position(self):
        install(self.m)
        original = None
        for day in (24, 25, 26, 27, 28, 30):
            Clock.current = datetime(2026, 11, day, 10, tzinfo=KST)
            b = self.broker()
            current = asdict(b.positions['111111'])
            if original is None: original = current
            self.assertEqual(current, original)
            self.assertEqual(b.cash, self.payload['cash'])
            self.assertEqual(b.realized_pnl, 100.)
            self.assertEqual(b.sequence, 7)
            self.assertEqual(b.daily_realized_pnl, 0.)
            self.assertEqual(b.session_date, f'2026-11-{day}')
            b.save_state()
            self.assertEqual(asdict(self.broker().positions['111111']), original)
        self.assertFalse(self.m.FILL_FILE.exists())

    def test_open_engine_day_roll_keeps_positions(self):
        install(self.m)
        b = self.broker()
        before = asdict(b.positions['111111'])
        for day in (25, 26):
            Clock.current = datetime(2026, 11, day, 9, tzinfo=KST)
            self.assertTrue(b.roll_session_if_needed(1000000.))
            self.assertFalse(b.roll_session_if_needed(1000000.))
            self.assertEqual(asdict(self.broker().positions['111111']), before)

    def test_actual_sell_then_restart_does_not_resurrect(self):
        install(self.m)
        b = self.broker()
        fill = b.sell('111111', SimpleNamespace(best_bid=10300.), '예약목표', reserved_fill_price=10300.)
        self.assertEqual(fill.quantity, 17)
        self.assertEqual(fill.entry_time.isoformat(), position()['entry_time'])
        Clock.current = datetime(2026, 11, 26, 9, tzinfo=KST)
        restored = self.broker()
        self.assertEqual(restored.positions, {})
        self.assertEqual(restored.sequence, 8)
        self.assertEqual(restored.cash, b.cash)
        with self.assertRaises(ValueError): restored.sell('111111', SimpleNamespace(best_bid=10300.), '예약목표')
        with self.m.FILL_FILE.open(encoding='utf-8-sig') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 1)

    def test_same_day_pnl_positive_stop_and_price_validator_unchanged(self):
        self.payload['session_date'] = '2026-11-24'
        self.payload['positions']['111111']['stop_price'] = 9853.
        self.write(self.payload)
        install(self.m)
        b = self.broker()
        self.assertEqual(b.daily_realized_pnl, 100.)
        self.assertEqual(b.positions['111111'].stop_price, self.m.align_stock_price(9853., upward=False))
        with self.assertRaises(ValueError): self.m.align_stock_price(0., upward=False)

    def test_invalid_stop_and_target_abort_without_overwriting_files(self):
        install(self.m)
        for field, value in [('stop_price', -1.), ('stop_price', float('nan')),
                             ('stop_price', float('inf')), ('target_price', 0.)]:
            with self.subTest(field=field, value=value):
                self.payload['positions']['111111'] = position()
                self.payload['positions']['111111'][field] = value
                self.write(self.payload)
                before = self.m.STATE_FILE.read_bytes()
                with self.assertRaisesRegex(RuntimeError, '복원 실패'): self.broker()
                self.assertEqual(self.m.STATE_FILE.read_bytes(), before)
                self.assertFalse(self.m.FILL_FILE.exists())

    def test_existing_backup_fallback_preserves_zero_stop(self):
        self.m.STATE_FILE.with_suffix('.bak').write_bytes(self.m.STATE_FILE.read_bytes())
        self.m.STATE_FILE.write_text('{broken', encoding='utf-8')
        install(self.m)
        self.assertEqual(self.broker().positions['111111'].quantity, 17)

    def test_both_unreadable_abort_without_saving(self):
        self.m.STATE_FILE.write_text('{broken', encoding='utf-8')
        install(self.m)
        with self.assertRaises(RuntimeError): self.broker()
        self.assertEqual(self.m.STATE_FILE.read_text(), '{broken')

    def test_no_state_still_allows_new_paper_session(self):
        self.m.STATE_FILE.unlink()
        install(self.m)
        self.assertEqual(self.broker().cash, 1000000.)

    def test_install_idempotent_and_uses_current_state_path(self):
        install(self.m)
        method = self.m.PaperBroker.load_state
        install(self.m)
        self.assertIs(method, self.m.PaperBroker.load_state)
        self.m.STATE_FILE = self.folder/'another.json'
        self.assertEqual(self.broker().positions, {})

    def test_split_tranches_and_first_buy_date_survive_multiple_days(self):
        if 'def _v7613_states(' not in (CORE/'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'):
            self.skipTest('Legacy public snapshot predates split-buy state; exercised on current production source')
        install(self.m)
        ns = dict(json=json, datetime=Clock, KST=KST, _SPLIT13=SimpleNamespace(MAX_TRANCHE=3),
                  V7613_STATE_FILE=self.folder/'split.json')
        tree = ast.parse((CORE/'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='V7600ThreeStageEngine')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='_v7613_states')
        method.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',
            names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])), '<states>', 'exec'), ns)
        split = dict(symbol='111111', quantity=17, tranche=3, first_price=11000.,
                     first_time='2026-11-20T10:00:00+09:00', fills=[{'time':'2026-11-20', 'notional':170000}])
        ns['V7613_STATE_FILE'].write_text(json.dumps({'date':'2026-11-23', 'positions':{'111111':split}}))
        for day in (24, 25, 26):
            Clock.current = datetime(2026, 11, day, 10, tzinfo=KST)
            engine = SimpleNamespace(broker=self.broker())
            self.assertEqual(ns['_v7613_states'](engine)['111111'], split)


if __name__ == '__main__': unittest.main()
