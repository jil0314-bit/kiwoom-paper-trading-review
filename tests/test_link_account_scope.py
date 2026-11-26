"""Replay observed Hero4 topology: 2000 order panes vs independent query tab."""
import unittest
from test_link_account_order import Link
from test_link_account_edits import edit_probe


def scoped_probe():
    p = edit_probe()
    p.parents = {1101: 5001, 1102: 5002, 5001: 8000, 5002: 8000, 8000: 101}
    p.titles = {1: '[0600] 키움종합차트', 8000: '[2000] 주식종합', 101: '영웅문4'}
    p.owners.update({h: 1 for h in p.parents})
    p._v7609_parent_window = lambda h: p.parents.get(h, 0)
    p._window_title_safe = lambda h: p.titles.get(h, '')
    p._v7609_account_control_scope = lambda chart, control: Link._v7609_account_control_scope(p, chart, control)
    return p


def query_display(p, h=1103, value='9876-9266'):
    p.account_texts[h] = value; p.owners[h] = 1; p.classes[h] = 'Edit'
    p.descendants[1].append(h); p.parents[h] = 8100; p.parents[8100] = 8000
    p.owners[8100] = 1; p.classes[8100] = 'SysTabControl32'


class AccountScopeTests(unittest.TestCase):
    def test_observed_primary_pair_with_different_balance_query_reconnects(self):
        p = scoped_probe(); query_display(p)
        self.assertEqual(p._v7609_chart_selected_accounts(1), ['1234-0311', '1234-0311'])
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')
        self.assertEqual(p.sent, [(1, '005930')])

    def test_masked_query_does_not_override_unmasked_order_pair(self):
        p = scoped_probe(); query_display(p, value='****-9266')
        self.assertTrue(p.capture_cursor_target()[0])

    def test_masked_primary_not_rescued_by_query(self):
        p = scoped_probe(); p.account_texts[1102] = '****-0311'
        query_display(p, value='1234-0311')
        self.assertFalse(p.capture_cursor_target()[0])

    def test_different_order_accounts_not_rescued_by_query(self):
        p = scoped_probe(); p.account_texts[1102] = '9876-9266'
        query_display(p, value='1234-0311')
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_only_query_matches_never_authorizes(self):
        p = scoped_probe(); p.account_texts[1101] = p.account_texts[1102] = '9876-9266'
        query_display(p, 1103, '1234-0311'); query_display(p, 1104, '1234-0311')
        self.assertFalse(p.capture_cursor_target()[0])

    def test_separate_order_window_cannot_substitute_primary_scope(self):
        p = scoped_probe(); p.titles[8000] = '[0307] 주문종합'
        self.assertFalse(p.capture_cursor_target()[0])

    def test_single_remaining_primary_display_blocks(self):
        p = scoped_probe(); p.parents[1102] = 8100
        query_display(p)
        self.assertFalse(p.capture_cursor_target()[0])

    def test_hidden_parent_or_cross_process_parent_blocks(self):
        p = scoped_probe(); p.hidden.add(8000)
        self.assertFalse(p.capture_cursor_target()[0])
        p = scoped_probe(); p.owners[8000] = 2
        self.assertFalse(p.capture_cursor_target()[0])

    def test_cyclic_ancestry_and_wrong_title_fail_closed(self):
        p = scoped_probe(); p.parents[5001] = 5001
        self.assertFalse(p.capture_cursor_target()[0])
        p = scoped_probe(); p.titles[8000] = '[2000] unrelated window'
        self.assertFalse(p.capture_cursor_target()[0])

    def test_multiple_primary_2000_windows_are_not_arbitrarily_selected(self):
        p = scoped_probe(); p.titles[9000] = '[2000] 주식종합'; p.owners[9000] = 1
        p.parents[9000] = 101
        for h in (1201, 1202):
            p.account_texts[h] = '1234-0311'; p.classes[h] = 'Edit'
            p.owners[h] = 1; p.parents[h] = 9000; p.descendants[1].append(h)
        self.assertFalse(p.capture_cursor_target()[0])
        self.assertEqual(p.sent, [])

    def test_recreated_scope_uses_live_ancestry(self):
        p = scoped_probe(); self.assertTrue(p.capture_cursor_target()[0])
        p.titles[8000] = ''; p.titles[9000] = '[2000] 주식종합'
        p.parents[5001] = p.parents[5002] = 9000
        p.parents[9000] = 101; p.owners[9000] = 1
        self.assertEqual(p.send_symbol('005930')[0], 'SENT')

    def test_primary_account_change_on_cached_chart_blocks(self):
        p = scoped_probe(); self.assertTrue(p.capture_cursor_target()[0])
        query_display(p, value='1234-0311')
        p.account_texts[1101] = p.account_texts[1102] = '9876-9266'
        self.assertEqual(p.send_symbol('005930')[0], 'FAILED')
        self.assertEqual(p.sent, [])

    def test_query_tab_is_never_read_or_written(self):
        p = scoped_probe(); query_display(p)
        original = p._read_control_text
        def read(h):
            if h == 1103: raise AssertionError('Query tab account was read')
            return original(h)
        p._read_control_text = read
        self.assertTrue(p.capture_cursor_target()[0])
        self.assertEqual(p.sent, [])


if __name__ == '__main__': unittest.main()
