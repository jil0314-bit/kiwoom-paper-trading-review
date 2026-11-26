"""Real account-reader regressions; fake Win32 boundaries, no UI or network."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_link_account_order import Link, probe, namespace


def edit_probe(windows=(1,)):
    p = probe(windows=windows)
    p.account_texts = {1000 + i * 100 + j: '1234-0311' for i in windows for j in (1, 2)}
    p.owners = {h: (h - 1000) // 100 for h in p.account_texts}
    p.descendants = {i: [h for h, pid in p.owners.items() if pid == i] for i in windows}
    p.classes = {h: 'Edit' for h in p.account_texts}
    p.children = set()
    p._window_pid = lambda h: p.owners.get(h, h - 100 if 100 <= h < 1000 else h)
    p._enum_descendants = lambda root: p.descendants.get(root - 100 if root >= 100 else root, [])
    p._win_class = lambda h: p.classes.get(h, 'AfxWnd')
    p._is_child = lambda parent, child: (parent, child) in p.children
    return p


class AccountEditTests(unittest.TestCase):
    def test_two_visible_account_edits_reconnect(self):
        p = edit_probe()
        self.assertTrue(p.capture_cursor_target()[0])
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_recreated_chart_with_two_edits_reconnects(self):
        p = edit_probe(windows=(2,)); p._v7609_fast_chart = 1; p._v7609_fast_edit = 10
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(2, '005930')])

    def test_only_correct_session_is_selected(self):
        p = edit_probe(windows=(1, 2))
        for h in (1101, 1102): p.account_texts[h] = '9876-9266'
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(2, '005930')])

    def test_same_suffix_different_full_account_blocked(self):
        p = edit_probe()
        for h in p.account_texts: p.account_texts[h] = '9876-0311'
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_two_verified_sessions_without_binding_blocked(self):
        p = edit_probe(windows=(1, 2))
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_lone_edit_or_duplicate_handle_cannot_prove_identity(self):
        for values in ([1101], [1101, 1101, 1101]):
            p = edit_probe(); p.descendants[1] = values
            self.assertFalse(p.capture_cursor_target()[0])
            self.assertEqual(p.sent, [])

    def test_hidden_edit_is_not_counted(self):
        p = edit_probe(); p.hidden.add(1102)
        self.assertFalse(p.capture_cursor_target()[0])

    def test_cross_process_edit_is_not_counted(self):
        p = edit_probe(); p.owners[1102] = 2
        self.assertFalse(p.capture_cursor_target()[0])

    def test_masked_or_mixed_account_cannot_authorize(self):
        for text in ('****-0311', '9876-9266', '1234-0311-22'):
            p = edit_probe(); p.account_texts[1102] = text
            self.assertFalse(p.capture_cursor_target()[0])
            self.assertEqual(p.sent, [])

    def test_third_conflicting_display_blocks_two_matching_ones(self):
        p = edit_probe(); p.account_texts[1103] = '****-0311'
        p.owners[1103] = 1; p.classes[1103] = 'Edit'; p.descendants[1].append(1103)
        self.assertFalse(p.capture_cursor_target()[0])

    def test_numeric_prices_symbols_and_free_form_text_are_not_accounts(self):
        for text in ('12340311', '005930', '1234031111', 'memo 1234-0311', '계좌 1234-0311'):
            p = edit_probe()
            for h in p.account_texts: p.account_texts[h] = text
            self.assertFalse(p.capture_cursor_target()[0])

    def test_chart_edits_are_not_account_evidence(self):
        p = edit_probe(); p._belongs_to_chart = lambda chart, h: h in (10, 1101, 1102)
        self.assertFalse(p.capture_cursor_target()[0])

    def test_combo_children_are_not_independent_edit_proof(self):
        p = edit_probe(); p.classes[1100] = 'CustomComboBox'
        p.owners[1100] = 1; p.account_texts[1100] = ''
        p.descendants[1].append(1100); p.children = {(1100, 1101), (1100, 1102)}
        self.assertFalse(p.capture_cursor_target()[0])

    def test_read_error_and_deadline_fail_closed(self):
        p = edit_probe(); p._read_control_text = Mock(side_effect=OSError('denied'))
        self.assertFalse(p._v7609_display_chart_valid(1))
        p = edit_probe()
        with patch.object(namespace['time'], 'monotonic', side_effect=[0, 3]):
            self.assertEqual(p._v7609_chart_selected_accounts(1), [])

    def test_rest_identity_is_still_required(self):
        p = edit_probe()
        with patch.dict(namespace, _V7609_VERIFIED_ACCOUNT=''):
            self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_account_change_before_enter_is_blocked(self):
        p = edit_probe()
        def write(*args, **kwargs):
            p.account_texts[1102] = '9876-9266'
            return True, 1, 0
        p._send_message = write
        p._read_control_text = lambda h: '005930' if h == 10 else p.account_texts.get(h, '')
        p._post_enter_to_edit = Mock()
        with patch.object(ctypes, 'windll', SimpleNamespace()):
            self.assertEqual(Link._fast_exact_edit_send(p, 1, 101, 10, '005930')[0], 'FAILED')
        p._post_enter_to_edit.assert_not_called()

    def test_identity_logs_do_not_repeat_for_alternating_charts(self):
        p = edit_probe(windows=(1, 2)); logger = namespace['v57'].diag
        logger.reset_mock()
        for _ in range(3):
            p._v7609_display_chart_valid(1); p._v7609_display_chart_valid(2)
        identity_calls = [c for c in logger.call_args_list if 'V7609_0600_IDENTITY' in str(c)]
        self.assertEqual(len(identity_calls), 2)
        self.assertEqual(logger.call_count, 4)
        self.assertNotIn('1234-0311', str(logger.call_args_list))


if __name__ == '__main__':
    unittest.main()
