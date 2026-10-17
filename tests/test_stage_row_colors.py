"""Run the actual nested insertion functions without starting Tk or the engine."""
import ast
from pathlib import Path
import textwrap
import unittest

CORE = Path(__file__).resolve().parents[1] / 'SOURCE' / 'core'


def extract(filename, name, namespace):
    tree = ast.parse((CORE / filename).read_text(encoding='utf-8-sig'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-row-insert>', 'exec'), namespace)
    return namespace[name]


class Tree:
    _v7595_main_stock_tree = True

    def item(self, iid, **kwargs):
        self.rows[iid].update(kwargs)


class StageRowColorTests(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.tree.rows = {}

        def terminal(tree, parent, index, iid=None, **kwargs):
            key = iid or 'row1'
            tree.rows[key] = kwargs
            return key

        legacy = extract('bot_v75_95_complete_locked_paper.py', 'tree_insert',
                         {'Any': object, 'textwrap': textwrap, '_R20_MAIN_COLUMNS': range(11),
                          'old_tree_insert': terminal})
        entry_tree = ast.parse((CORE / 'bot_v76_00_three_stage_ctrlv_paper.py').read_text(encoding='utf-8-sig'))
        mappings = {}
        for node in entry_tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {'STAGE_TO_TAG', 'CANONICAL_STAGE'}:
                        mappings[target.id] = ast.literal_eval(node.value)
        namespace = {'Any': object, 'old_insert': legacy, **mappings}
        canonical = next(n for n in entry_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'canonical_stage')
        exec(compile(ast.Module(body=[canonical], type_ignores=[]), '<actual-canonical>', 'exec'), namespace)
        self.insert = extract('bot_v76_00_three_stage_ctrlv_paper.py', 'insert', namespace)

    def row(self, stage, reason, tags=None):
        values = (1, stage, '종목', 100, 0, 1, 100, 102, 1, '09:00', reason)
        iid = self.insert(self.tree, '', 'end', values=values, tags=tags or (stage,))
        return self.tree.rows[iid]

    def test_sale_history_arrow_preserves_sale_color(self):
        plain = self.row('매도종목', '매도 완료')['tags']
        self.assertEqual(self.row('매도종목', '관찰 → 보유 → 매도')['tags'], plain)
        self.assertNotEqual(plain, ('R20_DEPARTED',))

    def test_profit_and_loss_sale_colors_survive_history(self):
        for badge, tag in (('▲ 수익', '매도종목_수익'), ('▼ 손실', '매도종목_손실')):
            self.assertEqual(self.row('매도종목', badge + ' 관찰 → 매도')['tags'], (tag,))

    def test_active_stages_ignore_historical_departure_words(self):
        for stage in ('관찰후보', '관심종목', '매수가능성', '시장보류', '매수종목'):
            plain = self.row(stage, '정상')['tags']
            self.assertEqual(self.row(stage, '이전 추적종료 → 재편입')['tags'], plain)

    def test_explicit_departed_tag_remains_gray(self):
        self.assertEqual(self.row('관찰후보', '종료', ('R20_DEPARTED',))['tags'], ('R20_DEPARTED',))

    def test_non_stock_tree_is_untouched(self):
        self.tree._v7595_main_stock_tree = False
        self.assertEqual(self.row('매도종목', '관찰 → 매도', ('custom',))['tags'], ('custom',))

    def test_description_and_values_are_not_removed(self):
        row = self.row('매도종목', '관찰 → 보유 → 매도')
        self.assertIn('→', row['values'][-1])
        self.assertEqual(row['values'][2], '종목')


if __name__ == '__main__':
    unittest.main(verbosity=2)
