"""V7614 차트 패널 순수 계산 + 표시 부호 시험."""
import sys, types, unittest, subprocess
from datetime import date, datetime
from pathlib import Path
_s = types.ModuleType("msvcrt"); _s.LK_NBLCK = 2; _s.LK_LOCK = 1; _s.LK_UNLCK = 0; _s.LK_NBRLCK = 4; _s.LK_RLCK = 3
_s.locking = lambda *a, **k: None; sys.modules.setdefault("msvcrt", _s)
CORE = Path(__file__).resolve().parents[1] / "SOURCE" / "core"
if not CORE.exists():
    CORE = Path("/home/claude/t13/ROOT/SOURCE/core")
sys.path.insert(0, str(CORE))
import v7614_chart_panel as C
import bot_v76_00_three_stage_ctrlv_paper as engine


class ChartTests(unittest.TestCase):
    def test_minute_parse_sorted_abs(self):
        rows = [{"cntr_tm": "20261109090200", "open_pric": "-100", "high_pric": "+110", "low_pric": "-95", "cur_prc": "+105", "trde_qty": "10"},
                {"cntr_tm": "20261109090100", "open_pric": "100", "high_pric": "101", "low_pric": "99", "cur_prc": "100", "trde_qty": "5"}]
        bars = C.parse_minute_rows(rows)
        self.assertEqual([b["t"].minute for b in bars], [1, 2])
        self.assertEqual(bars[1]["o"], 100.0); self.assertEqual(bars[1]["l"], 95.0)

    def test_moving_average_all_periods(self):
        closes = [float(i) for i in range(1, 401)]
        mas = C.moving_averages(closes)
        self.assertEqual(sorted(mas), [5, 10, 20, 30, 60, 120, 200, 360])
        self.assertIsNone(mas[360][358]); self.assertAlmostEqual(mas[360][359], sum(range(1, 361)) / 360)
        self.assertAlmostEqual(mas[5][-1], (396 + 397 + 398 + 399 + 400) / 5)

    def test_investor_daily_weekly(self):
        bars = [{"t": datetime(2026, 11, d, tzinfo=C.KST)} for d in (2, 3, 4, 5, 6, 9)]
        daily = {date(2026, 11, d): float(d) for d in (2, 3, 4, 5, 6, 9)}
        self.assertEqual(C.align_investor(bars, daily, "D"), [2, 3, 4, 5, 6, 9])
        self.assertEqual(C.align_investor(bars, daily, "m3"), [None] * 6)
        weekly_bars = [{"t": datetime(2026, 11, 2, tzinfo=C.KST)}, {"t": datetime(2026, 11, 9, tzinfo=C.KST)}]
        more = {date(2026, 10, 30): 1.0, **daily}
        self.assertEqual(C.align_investor(weekly_bars, more, "W"), [20.0, 9.0])

    def test_signed_display(self):
        f = engine._v7614_signed_text
        self.assertEqual(f("+1,234"), "▲+1,234"); self.assertEqual(f("-0.50%"), "▼-0.50%")
        self.assertEqual(f("+0"), "0"); self.assertEqual(f("935,000,000"), "935,000,000")
        self.assertEqual(f("선택종목: 삼성전자 005930"), "선택종목: 삼성전자 005930")
        self.assertEqual(f("▲+1,234"), "▲+1,234")
        self.assertEqual(C.signed(-1234.0), "▼-1,234")

    def test_fetcher_uses_shared_client_and_cache(self):
        calls = []
        class Client:
            settings = types.SimpleNamespace(suffix="AL")
            _last_response_headers = {}
            def _v7628_auth_lock(self):
                import threading; return threading.RLock()
            def _v7628_query(self, **kw):
                calls.append((kw["api_id"], kw["body"]["stk_cd"]))
                return {"stk_min_pole_chart_qry": [{"cntr_tm": "20261109090100", "open_pric": "1", "high_pric": "1", "low_pric": "1", "cur_prc": "1", "trde_qty": "1"}]}
        fetcher = C.ChartFetcher(lambda: Client(), engine._v7614_api_code)
        fetcher.bars("005930", "m3"); fetcher.bars("005930", "m3")
        self.assertEqual(calls, [("ka10080", "005930_AL")])


class SpanTests(unittest.TestCase):
    """V7629: 최근 N봉 구간 버튼."""
    def test_span_list_and_slot_fit(self):
        self.assertEqual(C.SPANS, (560, 480, 360, 240, 180, 120, 60))
        for span in C.SPANS:
            self.assertAlmostEqual(C.span_slot(700, span) * span, 700)
        self.assertEqual(C.span_slot(100, 560), C.SPAN_SLOT_MIN)      # 아주 좁은 창에서도 0이 되지 않음

    def test_span_choice_is_remembered_and_bad_file_ignored(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "view.json"
            self.assertIsNone(C.load_span(path))
            C.save_span(240, path); self.assertEqual(C.load_span(path), 240)
            C.save_span(None, path); self.assertIsNone(C.load_span(path))
            path.write_text('{"span": 77}', encoding="utf-8"); self.assertIsNone(C.load_span(path))
            path.write_text('{broken', encoding="utf-8"); self.assertIsNone(C.load_span(path))
            C.save_span(60, Path(d) / "없는폴더" / "view.json")          # 폴더 없으면 조용히 건너뜀


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=1)
