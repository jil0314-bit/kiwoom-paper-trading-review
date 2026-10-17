"""Opt-in local Tk smoke checks; no broker/client/0600 connection is created."""
import os
import sys
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tkinter as tk
from tkinter import ttk
from tests.test_state_invariants import engine
from v7609_trade_view import TradeView, install_command_submit


@unittest.skipUnless(os.environ.get('V7609_TEST_TK') == '1', 'opt-in Tk widget check')
class TradeViewTkTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        host = ttk.Frame(self.root)
        host.pack(fill='both', expand=True)
        tree = ttk.Treeview(host, columns=('no', 'stage', 'name'), show='headings')
        tree.pack(fill='both', expand=True)
        tree._v7609_navigate_selected = Mock()
        self.gui = SimpleNamespace(tree=tree, category_filter_var=tk.StringVar(self.root, '매수종목'),
            last_gui_rows=[dict(stage='매수종목', symbol='005930', name='삼성전자', current_price=10000, net_pnl=23)] +
                [dict(stage='매도종목', symbol=f'{i:06d}', name=f'매도{i}', net_pnl=-12, net_pnl_pct=-1.5) for i in range(23)])
        def choose(stage):
            self.gui.category_filter_var.set(stage)
            for iid in tree.get_children():
                tree.delete(iid)
            for row in self.gui.last_gui_rows:
                if row['stage'] == stage:
                    tree.insert('', 'end', values=(1, stage, row['name']))
        self.gui.choose_category = choose
        choose('매수종목')
        self.view = TradeView(self.root, self.gui, {'매수종목':('#173d5c','#ffffff'),
            '매도종목':('#5a1820','#ffe3e6'), 'gain':'#c8ffd8', 'loss':'#fff0a8'})

    def test_every_sold_row_stays_reachable_through_the_vertical_scrollbar(self):
        sold = self.view.tables['매도종목']
        # All 23 rows exist; the table shows ten at a time and scrolls to the rest.
        self.assertEqual(len(sold.get_children()), 23)
        self.assertEqual(int(sold.cget('height')), 10)
        self.assertTrue(str(sold.cget('yscrollcommand')))
        first, last = sold.yview()
        self.assertEqual(first, 0.0)
        self.assertLess(last, 1.0)
        sold.yview_moveto(1.0)
        self.assertGreater(sold.yview()[0], 0.0)
        last_iid = sold.get_children()[-1]
        self.assertEqual(sold.item(last_iid, 'values')[0], '23')

    def test_same_day_repeat_sell_of_one_symbol_keeps_both_rows(self):
        sold = self.view.tables['매도종목']
        self.gui.last_gui_rows.append(dict(stage='매도종목', symbol='000000',
            name='매도0', sell_time='10:00:00', net_pnl=5, net_pnl_pct=0.5))
        self.view.render()
        rows = [sold.item(i, 'values')[1] for i in sold.get_children()]
        self.assertEqual(rows.count('매도0'), 2)
        self.assertEqual(len(sold.get_children()), 24)

    def test_profit_loss_colors_and_scroll_position_survive_refresh(self):
        sold = self.view.tables['매도종목']
        self.gui.last_gui_rows[1]['net_pnl'] = 10
        self.view.render()
        first, second = sold.get_children()[:2]
        self.assertEqual(sold.item(first, 'tags'), ('gain',))
        self.assertEqual(sold.item(second, 'tags'), ('loss',))
        self.assertNotEqual(sold.tag_configure('gain')['foreground'], sold.tag_configure('loss')['foreground'])
        sold.yview_moveto(1)
        before = sold.yview()[0]
        self.gui.last_gui_rows[-1]['net_pnl'] = -30
        self.view.render()
        self.assertEqual(sold.yview()[0], before)

    def test_selection_uses_original_navigation_callback(self):
        sold = self.view.tables['매도종목']
        sold.selection_set(sold.get_children()[0])
        self.view.select('매도종목')
        self.gui.tree._v7609_navigate_selected.assert_called_once_with(None)
        self.gui.category_filter_var.set('전체')
        self.view.poll()
        self.assertFalse(self.view.shown)

    def test_quote_update_keeps_selection_without_new_navigation(self):
        tree = self.view.tables['매수종목']
        iid = tree.get_children()[0]
        tree.selection_set(iid)
        self.gui.last_gui_rows[0]['current_price'] = 10020
        self.view.render()
        self.assertEqual(tree.selection(), (iid,))
        self.gui.tree._v7609_navigate_selected.assert_not_called()

    def test_command_registration_does_not_submit_while_typing(self):
        panel = ttk.LabelFrame(self.root, text='형 의견·명령 입력')
        panel.pack()
        text = tk.Text(panel)
        text.grid(row=0, column=0)
        hub = SimpleNamespace(submit=Mock())
        install_command_submit(self.root, self.gui, engine._v7609_compile_command, hub)
        text.insert('1.0', '삼성전자 매수해')
        hub.submit.assert_not_called()
        self.assertTrue(text.bind('<Return>'))


@unittest.skipUnless(os.environ.get('V7609_TEST_TK') == '1', 'opt-in Tk widget check')
class FullDashboardTkTests(unittest.TestCase):
    def test_actual_dashboard_builds_combined_view_and_help_without_engine(self):
        source = str(engine.BASE_DIR.parent)
        if source not in sys.path:
            sys.path.insert(0, source)
            self.addCleanup(sys.path.remove, source)
        gui = engine.v57.V7557UnifiedDashboard(Mock())
        failures = []
        gui.logger.exception.side_effect = lambda *args: failures.append(traceback.format_exc())
        gui.publish({'rows': [], 'updated':'TEST', 'holding_total':0, 'unrealized_pnl':0,
            'today_realized_pnl':0, 'total_pnl':0, 'cash':0})
        original = tk.Tk.__init__
        observed, callback_errors = [], []
        def init(root, *args, **kwargs):
            original(root, *args, **kwargs)
            root.withdraw()
            root.report_callback_exception = lambda *error: callback_errors.append(error)
            def inspect_and_close():
                observed.append(hasattr(root, '_v7609_trade_view'))
                pending, labels = list(root.winfo_children()), []
                while pending:
                    child = pending.pop()
                    pending.extend(child.winfo_children())
                    if isinstance(child, tk.Label):
                        labels.append(str(child.cget('text')))
                observed.append(any('진행봉 25~59초' in text for text in labels))
                observed.append(any('순손실 -3%' in text for text in labels))
                gui.closed.set()
                root.quit()
            root.after(700, inspect_and_close)
        # Preserve the actual dashboard construction/mainloop, suppress native display only.
        with patch.object(tk.Tk, '__init__', init), patch.object(tk.Wm, 'deiconify'), \
             patch.object(tk.Wm, 'state', return_value='withdrawn'), \
             patch.object(engine.v57, 'diag'), \
             patch.object(engine.parent, '_write_startup_status'), \
             patch.object(engine.v57, '_save_target_sources'):
            gui._run()
        self.assertEqual(observed, [True, True, True], '\n'.join(failures))
        self.assertEqual(callback_errors, [])
