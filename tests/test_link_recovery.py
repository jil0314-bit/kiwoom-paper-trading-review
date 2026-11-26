"""Offline regressions: real extracted methods, fake credentials and Win32 IO."""
import ast
import ctypes
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_link_account_order import Link, probe, namespace as link_namespace

SOURCE = Path(__file__).resolve().parents[1] / 'SOURCE'
tree = ast.parse((SOURCE / 'launcher.py').read_text(encoding='utf-8-sig'))
names = {'parse_env_file', 'credential_values', 'credential_priority',
         'install_credentials', 'recover_existing_credentials'}
credential_ns = dict(os=os, Path=Path)
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name in names], type_ignores=[]),
             '<isolated-launcher-credentials>', 'exec'), credential_ns)


class CredentialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'packages' / 'V76_test' / 'SOURCE'
        self.core = self.root / 'core'
        self.core.mkdir(parents=True)
        credential_ns.update(ROOT=self.root, CORE=self.core,
                             CREDENTIAL_POINTER=self.home / 'pointer.txt')
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.home_patch = patch.object(Path, 'home', return_value=self.home)
        self.home_patch.start(); self.addCleanup(self.home_patch.stop)

    def file(self, name, values):
        path = self.core / name
        path.write_text('\n'.join(f'{k}={v}' for k, v in values.items()), encoding='utf-8')
        return path

    def pointer(self, path):
        credential_ns['CREDENTIAL_POINTER'].write_text(str(path), encoding='utf-8')

    def recover(self):
        return credential_ns['recover_existing_credentials']()

    def keys(self):
        return {'KIWOOM_APP_KEY': 'fake-app', 'KIWOOM_SECRET_KEY': 'fake-secret'}

    def test_token_only_environment_also_recovers_keys(self):
        os.environ['KIWOOM_ACCESS_TOKEN'] = 'fake-expired'
        path = self.file('v73_keys.env', self.keys()); self.pointer(path)
        self.assertEqual(self.recover(), 'cached-pointer')
        self.assertEqual(os.environ['KIWOOM_ACCESS_TOKEN'], 'fake-expired')
        self.assertEqual(os.environ['KIWOOM_APP_KEY'], 'fake-app')

    def test_token_only_cache_does_not_preempt_complete_keys(self):
        cached = self.file('old.env', {'KIWOOM_ACCESS_TOKEN': 'fake-stale'})
        self.pointer(cached)
        self.file('v73_keys.env', self.keys())
        self.assertEqual(self.recover(), 'recovered-sibling')
        self.assertEqual(os.environ['KIWOOM_SECRET_KEY'], 'fake-secret')
        self.assertNotIn('KIWOOM_ACCESS_TOKEN', os.environ)

    def test_complete_environment_never_reads_other_credentials(self):
        os.environ.update(self.keys())
        with patch.dict(credential_ns, parse_env_file=Mock(side_effect=AssertionError('unexpected read'))):
            self.assertEqual(self.recover(), 'environment')

    def test_secret_alias_is_normalized(self):
        os.environ.update(KIWOOM_APP_KEY='fake-app', KIWOOM_APP_SECRET='fake-secret')
        self.assertEqual(self.recover(), 'environment')
        self.assertEqual(os.environ['KIWOOM_SECRET_KEY'], 'fake-secret')

    def test_token_only_environment_without_keys_is_retained(self):
        os.environ['KIWOOM_ACCESS_TOKEN'] = 'fake-token'
        self.assertEqual(self.recover(), 'environment')
        self.assertNotIn('KIWOOM_APP_KEY', os.environ)

    def test_token_only_cached_path_remains_fallback(self):
        self.pointer(self.file('old.env', {'KIWOOM_ACCESS_TOKEN': 'fake-token'}))
        self.assertEqual(self.recover(), 'recovered-sibling')
        self.assertEqual(os.environ['KIWOOM_ACCESS_TOKEN'], 'fake-token')

    def test_no_partial_key_pair_mixing(self):
        os.environ['KIWOOM_APP_KEY'] = 'another-app'
        self.pointer(self.file('v73_keys.env', self.keys()))
        self.assertEqual(self.recover(), 'not-found')
        self.assertEqual(os.environ['KIWOOM_APP_KEY'], 'another-app')
        self.assertNotIn('KIWOOM_SECRET_KEY', os.environ)

    def test_real_and_mock_credentials_not_mixed(self):
        os.environ['KIWOOM_BASE_URL'] = 'https://api.kiwoom.com'
        self.pointer(self.file('v73_keys.env', dict(self.keys(), KIWOOM_BASE_URL='https://mockapi.kiwoom.com')))
        self.assertEqual(self.recover(), 'not-found')
        self.assertNotIn('KIWOOM_APP_KEY', os.environ)


class LinkReconnectionTests(unittest.TestCase):
    def combo(self, *, index=2, after=2, length=9, style=0x200, text='1234-0311'):
        p = probe(); p._read_control_text = Mock(return_value='')
        p._win_class = lambda hwnd: 'ComboBox'
        calls = []; indices = iter((index, after))
        def send(hwnd, message, wparam=0, lparam=0, **kwargs):
            calls.append((hwnd, message, wparam))
            if message == 0x147: return True, next(indices), 0
            if message == 0x149: return True, length, 0
            if message == 0x148:
                ctypes.memmove(lparam, ctypes.create_unicode_buffer(text),
                               (len(text)+1)*ctypes.sizeof(ctypes.c_wchar))
                return True, len(text), 0
            raise AssertionError('Unexpected Win32 message')
        p._send_message = send
        fake = SimpleNamespace(user32=SimpleNamespace(GetWindowLongW=lambda *args: style))
        return p, calls, fake

    def test_selected_item_fallback_reads_without_input(self):
        p, calls, fake = self.combo()
        with patch.object(ctypes, 'windll', fake):
            self.assertEqual(p._v7609_selected_combo_text(1001), '1234-0311')
        self.assertEqual([m for _, m, _ in calls], [0x147, 0x149, 0x148, 0x147])
        self.assertTrue(all(wparam == 2 for _, msg, wparam in calls if msg in (0x148, 0x149)))

    def test_fallback_reconnects_matching_account_end_to_end(self):
        p, calls, fake = self.combo()
        p._win_class = lambda hwnd: 'ComboBox' if hwnd == 1001 else 'AfxWnd'
        read_item = p._send_message
        def repeatable(hwnd, message, *args, **kwargs):
            if message == 0x147: return True, 2, 0
            return read_item(hwnd, message, *args, **kwargs)
        p._send_message = repeatable
        with patch.object(ctypes, 'windll', fake):
            self.assertTrue(p.capture_cursor_target()[0])
            self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_fallback_wrong_account_is_still_blocked(self):
        p, calls, fake = self.combo(text='9876-9266')
        p._win_class = lambda hwnd: 'ComboBox' if hwnd == 1001 else 'AfxWnd'
        with patch.object(ctypes, 'windll', fake):
            self.assertFalse(p.capture_cursor_target()[0])
        self.assertEqual(p.sent, [])

    def test_existing_masked_text_never_reads_underlying_item(self):
        p = probe(); p._read_control_text = lambda _: '****-0311'
        p._send_message = Mock(side_effect=AssertionError('mask bypass'))
        self.assertEqual(p._v7609_selected_combo_text(1001), '****-0311')
        p._send_message.assert_not_called()

    def test_owner_draw_item_data_is_not_read_as_text(self):
        p, calls, fake = self.combo(style=0x10)
        with patch.object(ctypes, 'windll', fake):
            self.assertEqual(p._v7609_selected_combo_text(1001), '')
        self.assertEqual(calls, [])

    def test_selection_change_no_account_proof(self):
        p, calls, fake = self.combo(after=3)
        with patch.object(ctypes, 'windll', fake):
            self.assertEqual(p._v7609_selected_combo_text(1001), '')

    def test_invalid_index_and_length_fail_closed(self):
        for kwargs in ({'index': -1}, {'index': 2**64-1}, {'length': 0}, {'length': 512}):
            p, calls, fake = self.combo(**kwargs)
            with patch.object(ctypes, 'windll', fake):
                self.assertEqual(p._v7609_selected_combo_text(1001), '')
            self.assertNotIn(0x148, [msg for _, msg, _ in calls])

    def test_custom_control_never_receives_standard_combo_messages(self):
        p, calls, fake = self.combo(); p._win_class = lambda _: 'CustomComboBox'
        self.assertEqual(p._v7609_selected_combo_text(1001), '')
        self.assertEqual(calls, [])

    def test_native_read_denied_fails_closed(self):
        p, calls, fake = self.combo(); p._send_message = Mock(return_value=(False, 0, 5))
        with patch.object(ctypes, 'windll', fake):
            self.assertEqual(p._v7609_selected_combo_text(1001), '')

    def test_recreated_chart_rebinds_only_verified_session(self):
        p = probe(windows=(2, 3)); p._v7609_fast_chart = 1; p._v7609_fast_edit = 10
        p.account_texts[1003] = '9876-9266'
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(2, '005930')])

    def test_recreated_chart_without_account_stays_blocked(self):
        p = probe(windows=(2,)); p._v7609_fast_chart = 1; p._v7609_fast_edit = 10
        p.account_texts.clear()
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_long_idle_does_not_expire_a_revalidated_chart(self):
        p = probe(); p._v7609_remember_target(1, 101, 10)
        p._v7609_fast_saved_at = -40 * 86400
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')

    def test_errors_distinguish_rest_identity_and_hts_read(self):
        p = probe()
        with patch.dict(link_namespace, _V7609_VERIFIED_ACCOUNT=''):
            self.assertIn('REST 계좌 인증 미확인', p.send_symbol('005930')[1])
        p.account_texts.clear()
        self.assertIn('계좌 선택 표시를 읽지 못했습니다', p.send_symbol('005930')[1])

    def test_multiple_verified_sessions_diagnostic(self):
        p = probe(windows=(1, 2))
        self.assertIn('대상이 여러 개', p.send_symbol('005930')[1])

    def test_logs_do_not_expose_account_text(self):
        p = probe(); logger = link_namespace['v57'].diag
        logger.reset_mock(); p.capture_cursor_target()
        self.assertNotIn('1234-0311', str(logger.call_args_list))
        self.assertNotIn('1234031111', str(logger.call_args_list))


if __name__ == '__main__':
    unittest.main()
