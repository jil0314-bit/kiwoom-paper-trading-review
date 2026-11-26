"""Real link methods with inert Windows boundaries; never imports/runs the bot."""
import ast
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'
sys.path.insert(0, str(CORE))
from v7609_account_guard import allowed_account, selected_account_matches

METHODS = {'_chart_candidates', '_v7609_multiple_0600_sessions', 'send_symbol',
    'capture_cursor_target', '_v7609_cached_target_valid', '_v7609_locked_chart_valid',
    '_v7609_clear_edit_cache', '_v7609_remember_target', '_fast_exact_edit_send',
    '_v7609_display_chart_valid', '_v7609_refresh_locked_edit',
    '_v7609_chart_selected_accounts', '_v7609_restore_locked_target',
    '_v7609_lock_saved_chart_awaiting_edit', '_v7609_selected_combo_text',
    '_v7609_link_block_message', '_v7609_parent_window', '_v7609_account_control_scope'}


class BaseLink:
    def _chart_candidates(self):
        return self.windows


source = ast.parse((CORE / 'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
original = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'V7600CtrlVHeroLink')
link_class = ast.ClassDef(name='Link', bases=[ast.Name(id='BaseLink', ctx=ast.Load())],
    keywords=[], decorator_list=[], body=[n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in METHODS])
namespace = dict(BaseLink=BaseLink, ctypes=ctypes, wintypes=wintypes,
    sys=SimpleNamespace(platform='win32', argv=[]), re=re, time=time,
    allowed_account=allowed_account, selected_account_matches=selected_account_matches,
    _V7609_VERIFIED_ACCOUNT='1234031111',
    v57=SimpleNamespace(LinkStatus=SimpleNamespace(FAILED='FAILED', SENT_UNVERIFIED='SENT'), diag=Mock()))
exec(compile(ast.fix_missing_locations(ast.Module(body=[
    ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    link_class], type_ignores=[])), '<extracted-real-link-methods>', 'exec'), namespace)
Link = namespace['Link']


def probe(windows=(1,)):
    p = Link()
    p.windows = [SimpleNamespace(hwnd=i, top=100+i) for i in windows]
    p.config = {}; p._v7609_fast_chart = p._v7609_fast_edit = p._v7609_fast_top = 0
    p._is_exact_0600_chart = lambda hwnd: hwnd in windows and hwnd != 2000
    p._v7609_account_control_scope = lambda chart, control: 10000 + chart
    p._belongs_to_chart = lambda chart, target: target == chart*10
    p._safe_edit_target = p._belongs_to_chart
    p._find_live_0600_edit = lambda chart: chart*10
    p._v7609_window_rect = lambda chart: [chart, 0, chart+100, 100]
    p._window_title_safe = lambda hwnd: '[0600] 키움종합차트'
    chart_ids = set(windows) - {2000}
    p.account_texts = {1000+i: '1234-0311' for i in chart_ids}
    p.hidden = set()
    p._window_pid = lambda hwnd: (hwnd-1000 if hwnd >= 1000 else hwnd-100 if hwnd >= 100 else hwnd)
    p._window_alive = lambda hwnd: bool(hwnd) and hwnd not in p.hidden
    p._top_level_of = lambda chart: chart+100
    p._enum_top_windows = lambda: [i+100 for i in chart_ids]
    p._enum_descendants = lambda root: [1000+(root-100 if root >= 100 else root)]
    p._win_class = lambda hwnd: 'ComboBox' if hwnd in p.account_texts else 'AfxWnd'
    p._read_control_text = lambda hwnd: p.account_texts.get(hwnd, '')
    p._save = Mock()
    p.sent = []
    def send(chart, top, target, symbol):
        if not p._v7609_display_chart_valid(chart):
            return 'FAILED', 'account changed'
        p.sent.append((chart, symbol))
        p._v7609_remember_target(chart, top, target)
        return 'SENT', symbol
    p._fast_exact_edit_send = send
    return p


class LinkDisplayTests(unittest.TestCase):
    def test_single_verified_0311_chart_sends(self):
        p = probe()
        with patch.object(ctypes, 'windll', SimpleNamespace()):
            self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_check_button_binds_without_sending(self):
        p = probe()
        self.assertTrue(p.capture_cursor_target()[0])
        self.assertEqual(p._v7609_fast_chart, 1)
        self.assertEqual(p.sent, [])

    def test_unknown_or_wrong_rest_account_blocks(self):
        for account in ('', '9876123411'):
            with patch.dict(namespace, _V7609_VERIFIED_ACCOUNT=account):
                p = probe()
                self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
                self.assertFalse(p.capture_cursor_target()[0])
                self.assertEqual(p.sent, [])

    def test_two_unbound_sessions_fail_closed(self):
        p = probe(windows=(1, 2))
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_known_chart_stays_bound_when_second_session_opens(self):
        p = probe(windows=(1, 2))
        p._v7609_remember_target(1, 101, 10)
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_click_and_arrow_keep_same_chart(self):
        p = probe()
        for symbol in ('005930', '000660', '009830'):
            self.assertEqual(p.send_symbol(symbol)[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930'), (1, '000660'), (1, '009830')])

    def test_order_window_2000_is_never_selected(self):
        p = probe(windows=(2000,))
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_order_window_does_not_hide_valid_chart(self):
        p = probe(windows=(2000, 1))
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_final_native_send_rejects_foreign_edit(self):
        p = probe(); p._send_message = Mock()
        self.assertEqual(Link._fast_exact_edit_send(p, 1, 101, 20000, '005930')[0], 'FAILED')
        p._send_message.assert_not_called()

    def test_missing_edit_never_falls_back(self):
        p = probe(); p._find_live_0600_edit = lambda chart: 0
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_locked_chart_missing_edit_never_switches(self):
        p = probe(windows=(1, 2)); p._v7609_fast_chart = 1
        p._find_live_0600_edit = lambda chart: 0 if chart == 1 else 20
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_invalid_symbol_never_sends(self):
        p = probe()
        self.assertEqual(p.send_symbol('BUY')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_real_native_send_targets_only_exact_edit(self):
        p = probe(); writes = []
        def send(hwnd, message, wparam, lparam, **kwargs):
            writes.append((hwnd, message, ctypes.wstring_at(lparam)))
            return True, 1, 0
        p._send_message = send
        p._read_control_text = lambda hwnd: '005930' if hwnd == 10 else p.account_texts.get(hwnd, '')
        p._post_enter_to_edit = Mock(return_value=(True, True))
        with patch.object(ctypes, 'windll', SimpleNamespace()):
            result = Link._fast_exact_edit_send(p, 1, 101, 10, '005930')
        self.assertEqual(result[0], 'SENT')
        self.assertEqual(writes, [(10, 0x000C, '005930')])
        p._post_enter_to_edit.assert_called_once_with(10)

    def test_readback_failure_does_not_post_enter(self):
        p = probe(); p._send_message = Mock(return_value=(True, 1, 0))
        p._read_control_text = lambda hwnd: '000660' if hwnd == 10 else p.account_texts.get(hwnd, '')
        p._post_enter_to_edit = Mock()
        self.assertEqual(Link._fast_exact_edit_send(p, 1, 101, 10, '005930')[0], 'FAILED')
        p._post_enter_to_edit.assert_not_called()

    def test_single_chart_without_account_proof_is_blocked(self):
        p = probe(); p.account_texts.clear()
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertFalse(p.capture_cursor_target()[0])
        self.assertEqual(p.sent, [])

    def test_foreign_accounts_never_send_even_when_only_chart(self):
        for suffix in ('9266', '9671', '1537'):
            p = probe(); p.account_texts[1001] = '9876-'+suffix
            self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
            self.assertEqual(p.sent, [])

    def test_correct_account_selected_independent_of_login_order(self):
        for order in ((1, 2), (2, 1)):
            p = probe(windows=order)
            p.account_texts[1001] = '9876-9266'
            self.assertEqual(p.send_symbol('005930')[0], 'SENT')
            self.assertEqual(p.sent, [(2, '005930')])

    def test_rest_0311_in_foreign_pid_cannot_authorize_wrong_chart(self):
        p = probe(windows=(1, 2))
        p.account_texts[1001] = '9876-9266'
        self.assertFalse(p._v7609_display_chart_valid(1))
        self.assertTrue(p._v7609_display_chart_valid(2))

    def test_cached_handle_is_rechecked_after_account_switch(self):
        p = probe(); p._v7609_remember_target(1, 101, 10)
        p.account_texts[1001] = '9876-9671'
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertFalse(p.capture_cursor_target()[0])
        self.assertEqual(p.sent, [])

    def test_hidden_or_masked_account_is_not_proof(self):
        p = probe(); p.hidden.add(1001)
        self.assertFalse(p._v7609_display_chart_valid(1))
        p.hidden.clear(); p.account_texts[1001] = '****-0311'
        self.assertFalse(p._v7609_display_chart_valid(1))

    def test_account_read_error_fails_closed(self):
        p = probe(); p._read_control_text = Mock(side_effect=OSError('denied'))
        self.assertFalse(p._v7609_display_chart_valid(1))

    def test_account_switch_before_enter_never_posts_enter(self):
        p = probe()
        p._send_message = Mock(return_value=(True, 1, 0))
        def read(hwnd):
            if hwnd == 10:
                p.account_texts[1001] = '9876-1537'
                return '005930'
            return p.account_texts.get(hwnd, '')
        p._read_control_text = read
        p._post_enter_to_edit = Mock()
        self.assertEqual(Link._fast_exact_edit_send(p, 1, 101, 10, '005930')[0], 'FAILED')
        p._post_enter_to_edit.assert_not_called()

    def test_masked_or_different_product_alongside_valid_account_blocks(self):
        self.assertFalse(selected_account_matches(['1234-0311', '****-9671'], '1234031111'))
        self.assertFalse(selected_account_matches(['1234031122'], '1234031111'))

    def test_saved_rectangle_never_restores_foreign_account(self):
        p = probe()
        p.config['v7609_0600_binding'] = {'chart_rect': [1, 0, 101, 100]}
        p.account_texts[1001] = '9876-9266'
        with tempfile.TemporaryDirectory() as folder, patch.dict(namespace, BASE_DIR=Path(folder)/'core'):
            p._v7609_restore_locked_target()
        self.assertEqual(p._v7609_fast_chart, 0)
        p._save.assert_not_called()

    def test_saved_rectangle_restores_only_current_verified_account(self):
        p = probe()
        p.config['v7609_0600_binding'] = {'chart_rect': [1, 0, 101, 100]}
        p._v7609_restore_locked_target()
        self.assertEqual(p._v7609_fast_chart, 1)


if __name__ == '__main__':
    unittest.main()
